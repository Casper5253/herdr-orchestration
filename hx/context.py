"""Session-bound telemetry and protected native compaction."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
import re
import sqlite3
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from hx import checkpoint, host, ledger, usage


class _RetainContext(ValueError):
    disposition = "keep"


class _DeferredContext(ValueError):
    disposition = "defer"


class _UnknownContext(ValueError):
    disposition = "unknown"


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _timestamp(value: Any) -> dt.datetime | None:
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else None
    except (ValueError, TypeError, AttributeError):
        return None


RESET_COMMANDS = {"opencode": "/new", "devin": "/clear", "claude": "/clear"}


def default_resets_path(leases_path: Path | None = None) -> Path:
    """Where recorded worker resets live; the pane-side `hx say` reads the same file."""
    env = os.environ.get("HX_CONTEXT_RESETS_PATH")
    if env:
        return Path(env)
    if leases_path is not None:
        return Path(leases_path).parent / "context-resets.json"
    state_dir = Path(os.environ.get("HX_STATE_DIR", Path.home() / ".herdr-team" / "state"))
    return state_dir / "context-resets.json"


def _load_resets(path: Path) -> dict[str, Any]:
    data = ledger._load_json(path)
    return data if isinstance(data, dict) else {}


def write_reset(path: Path, name: str, session_id: str, *, now: dt.datetime | None = None) -> dict[str, Any]:
    record = {"agent": name, "session_id": session_id, "reset_at": (now or _now()).isoformat()}
    with ledger.LeaseLock(path.with_suffix(".lock")):
        data = _load_resets(path)
        data[name] = record
        ledger._atomic_write_json(path, data)
    return record


def reset_pending(name: str, session_id: str, policy: dict[str, Any], *,
                  path: Path | None = None, now: dt.datetime | None = None) -> bool:
    """A recorded reset still awaiting the first sample from the new session.

    The provider session id stays at the pre-reset value until the pane's first
    message, so pressure read from that session is stale for the reset worker.
    The clearance expires after ``context.reset_grace_min`` and never applies
    once a different session id is observed. Unknown or unreadable state leaves
    the pressure hold in place.
    """
    try:
        record = _load_resets(path or default_resets_path()).get(name)
        if not isinstance(record, dict) or not session_id or record.get("session_id") != session_id:
            return False
        reset_at = _timestamp(record.get("reset_at"))
        grace = (policy.get("context") or {}).get("reset_grace_min", 10)
        if (reset_at is None or not isinstance(grace, (int, float))
                or not math.isfinite(grace) or grace < 0):
            return False
        return 0 <= ((now or _now()) - reset_at).total_seconds() <= grace * 60
    except (OSError, ValueError, TypeError):
        return False


def _reset_command(policy: dict[str, Any], kind: Any) -> str | None:
    commands = (policy.get("context") or {}).get("reset_commands") or RESET_COMMANDS
    command = commands.get(kind) if isinstance(commands, dict) else None
    return str(command) if command else None


def _detect() -> host.Host:
    detected = host.detect()
    if detected is None:
        raise RuntimeError("hx needs a team host: run it inside a Herdr pane or the Herdr app")
    return detected


def _send_checked(name: str, text: str, *, wait: bool = False,
                  until: tuple[str, ...] = (), timeout_ms: int | None = None) -> host.SendResult:
    return _detect().send_checked(name, text, wait=wait, until=until, timeout_ms=timeout_ms)


def _send_slash(member: host.Member, command: str) -> bool:
    """Type a slash command as keys through the host; the one native-reset transport."""
    return _detect().send_slash(member, command)


def agent_info(name: str) -> host.Member:
    return _detect().info(name)


# Herdr reports a finished turn as "done" and an untouched one as "idle"; both are ready for input.
READY = usage.READY


def _session(agent: host.Member) -> str:
    sid = agent.get("session_id") or ""
    if not sid and agent.get("kind") == "claude" and agent.get("address"):
        sid = _claude_session_for_pane(str(agent["address"]))
    if not sid and agent.get("kind") == "devin":
        # Devin exposes no provider session through the host; the pane is the only stable identity.
        pane = str(agent.get("address") or agent.get("name") or "")
        if pane:
            sid = f"devin-pane:{pane}"
    return sid


def _claude_session_for_pane(pane_id: str) -> str:
    """Session id of the newest Claude status file the status line wrote from this Herdr pane."""
    state_dir = Path(os.environ.get("HX_STATE_DIR", Path.home() / ".herdr-team" / "state"))
    best: tuple[str, str] | None = None
    for path in state_dir.glob("claude-status-*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if data.get("herdr_pane_id") == pane_id and data.get("session_id"):
            stamp = str(data.get("timestamp") or "")
            if best is None or stamp > best[0]:
                best = (stamp, str(data["session_id"]))
    return best[1] if best else ""


def _herdr_read(name: str, lines: int = 200) -> str:
    """The pane as plain text."""
    return _detect().read(name, lines)


def _squash(text: str) -> str:
    # TUIs hard-wrap long replies (OpenCode splits "re-" / "review"), so compare without whitespace.
    return "".join(text.split())


# Devin pane footer: "Context: 157k / 262k tokens (59%)" wide, "44k / 262k (16%)" narrow.
_DEVIN_USAGE = re.compile(
    r"(\d+(?:\.\d+)?)\s*([kKmM])?\s*/\s*(\d+(?:\.\d+)?)\s*([kKmM])?\s*"
    r"(?:tokens?\b)?\s*\(\s*(\d+(?:\.\d+)?)\s*%\s*\)"
)

_DEVIN_UNIT = {"k": 1e3, "m": 1e6}


def _devin_footer_member(line: str) -> bool:
    """A line of the Devin footer status block; anything else ends the block.

    The block is the status chrome Devin draws at the bottom: usage lines
    (Context marker with the model prefix, or the narrow bare form), the
    model/mode line, the Pro quota line, update nags, separators and blanks.
    Task output above it — including a Context example — is never footer.
    """
    if not line:
        return True
    if "Context:" in line:
        prefix = line.rsplit("Context:", 1)[0].strip()
        return not prefix or bool(re.match(r"^SWE-2\b", prefix))
    return bool(
        _DEVIN_USAGE.fullmatch(line)
        or re.match(r"^SWE-2\b.*[·|]", line)
        or re.match(r"^Pro\s*[·|]", line)
        or re.match(r"^Update\s+v?[\d.]", line)
        or re.fullmatch(r"[─—\-=\s]+", line)
    )


def _devin_footer_lines(pane_text: str) -> list[str]:
    """The contiguous footer status block at the bottom, bottom-most line first."""
    block: list[str] = []
    for raw in reversed((pane_text or "").rstrip().splitlines()):
        line = raw.strip()
        if not _devin_footer_member(line):
            break
        block.append(line)
    return block


def _devin_footer_label(pane_text: str) -> str | None:
    """The footer block's model label ("SWE-2 High"), or None when absent.

    Wide footers put the label on the Context line itself; the narrow two-line
    footer carries it on the model/mode line beside a bare usage line.
    """
    for line in _devin_footer_lines(pane_text):
        if "Context:" in line:
            prefix = line.rsplit("Context:", 1)[0].strip()
        elif re.match(r"^SWE-2\b", line):
            prefix = line
        else:
            continue
        if not prefix:
            continue
        label = re.split(r"[·|]", prefix, maxsplit=1)[0].strip()
        if label:
            return label
    return None


def _parse_devin_footer(pane_text: str) -> tuple[float, float] | None:
    """(used, total) tokens from the last footer usage line; None when absent or inconsistent.

    Only the contiguous footer status block at the bottom counts: a Context
    line in task output above the block is prose, not telemetry. A footer
    line that fails its own percentage check is a present-but-broken footer:
    unknown, never an older line.
    """
    for line in _devin_footer_lines(pane_text):
        if "Context:" in line:
            _prefix, rest = line.rsplit("Context:", 1)
            match = _DEVIN_USAGE.fullmatch(rest.strip())
            if match is None:
                return None
        else:
            match = _DEVIN_USAGE.fullmatch(line)
            if match is None:
                continue
        used = float(match[1]) * _DEVIN_UNIT.get((match[2] or "").lower(), 1.0)
        total = float(match[3]) * _DEVIN_UNIT.get((match[4] or "").lower(), 1.0)
        if total <= 0 or used < 0:
            return None
        # The footer shows its own percentage; a mismatch means the line is not what it looked like.
        if abs(used / total * 100 - float(match[5])) > 1.5:
            return None
        return used, total
    return None


def _has_acknowledgement(value: Any, expected: str) -> bool:
    if isinstance(value, str):
        return _squash(expected) in _squash(value)
    if isinstance(value, dict):
        return any(_has_acknowledgement(item, expected) for item in value.values())
    if isinstance(value, list):
        return any(_has_acknowledgement(item, expected) for item in value)
    return False


def _has_blocking_session_lease(
    sessions: dict[str, Any], sid: str, name: str, task: dict[str, Any]
) -> bool:
    for key, lease in sessions.items():
        if key == sid:
            return True
        if not isinstance(lease, dict) or lease.get("agent") != name:
            continue
        is_current_task_hold = (
            key == name
            and lease.get("state") == "held"
            and "token" not in lease
            and lease.get("task_id") == task.get("id")
            and task.get("owner") == name
            and task.get("status") in ledger.Ledger.OPEN_STATUSES
        )
        if not is_current_task_hold:
            return True
    return False


def _sample(session_id: str, tokens: Any, limit: Any, sampled_at: str) -> dict[str, Any]:
    try:
        fraction = float(tokens) / float(limit)
        if float(tokens) < 0 or float(limit) <= 0 or not math.isfinite(fraction):
            raise ValueError("invalid context counters")
    except (ValueError, TypeError, ZeroDivisionError):
        return {"session_id": session_id, "utilisation": None, "sampled_at": sampled_at}
    return {"session_id": session_id, "utilisation": fraction, "sampled_at": sampled_at}


def telemetry(agent: host.Member, policy: dict[str, Any]) -> dict[str, Any]:
    """Read only the provider session named by the host; never pick newest globally."""
    sid = _session(agent)
    unknown = {"session_id": sid, "utilisation": None, "sampled_at": None}
    if not sid:
        return unknown
    try:
        if agent.get("kind") == "codex":
            for path in usage.default_codex_sessions_dir().rglob(f"*{sid}*.jsonl"):
                meta = None
                latest = None
                for line in path.read_text(encoding="utf-8").splitlines():
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if event.get("type") == "session_meta":
                        meta = event.get("payload", {})
                    payload = event.get("payload") or {}
                    if event.get("type") == "event_msg" and payload.get("type") == "token_count":
                        info = payload.get("info") or {}
                        latest = _sample(sid, (info.get("last_token_usage") or {}).get("input_tokens"),
                                         info.get("model_context_window"), event.get("timestamp"))
                if meta and meta.get("id") == sid and ledger._canonical_path(meta.get("cwd", "")) == ledger._canonical_path(agent.get("cwd", "")):
                    return latest or unknown
        elif agent.get("kind") == "claude":
            state_dir = Path(os.environ.get("HX_STATE_DIR", Path.home() / ".herdr-team" / "state"))
            path = state_dir / f"claude-status-{sid}.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("session_id") == sid:
                timestamp = data.get("timestamp") or dt.datetime.fromtimestamp(path.stat().st_mtime, dt.timezone.utc).isoformat()
                return _sample(sid, (data.get("context_window") or {}).get("used_percentage"), 100, timestamp)
        elif agent.get("kind") == "devin":
            try:
                footer = _herdr_read(str(agent.get("name") or ""), lines=8)
            except (OSError, subprocess.SubprocessError):
                return unknown
            parsed = _parse_devin_footer(footer)
            if parsed is not None:
                return _sample(sid, parsed[0], parsed[1], _now().isoformat())
            return unknown
        elif agent.get("kind") == "opencode":
            with sqlite3.connect(usage.default_opencode_db_path().resolve().as_uri() + "?mode=ro", uri=True) as conn:
                rows = conn.execute("SELECT time_created, data FROM message WHERE session_id = ? ORDER BY time_created DESC", (sid,))
                for created, raw in rows:
                    data = json.loads(raw)
                    if data.get("role") != "assistant":
                        continue
                    tokens = data.get("tokens") or {}
                    if (tokens.get("input") == 0 and (tokens.get("cache") or {}).get("read", 0) == 0
                            and not data.get("finish") and not (data.get("time") or {}).get("completed")):
                        continue
                    model = data.get("modelID")
                    limit = policy.get("context_limits", {}).get(model)
                    timestamp = dt.datetime.fromtimestamp(created / 1000, dt.timezone.utc).isoformat()
                    return _sample(sid, tokens.get("input") + (tokens.get("cache") or {}).get("read", 0), limit, timestamp)
    except (OSError, ValueError, TypeError, sqlite3.Error):
        pass
    return unknown


def session_telemetry(agent: host.Member, policy: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Pin the provider session once before reading its counters."""
    sid = _session(agent)
    if not sid:
        return sid, {"session_id": sid, "utilisation": None, "sampled_at": None}
    bound = {**agent, "session_id": sid}
    return sid, telemetry(bound, policy)


def _fresh(sample: dict[str, Any], sid: str, now: dt.datetime, after: dt.datetime | None = None) -> bool:
    timestamp = _timestamp(sample.get("sampled_at"))
    value = sample.get("utilisation")
    return bool(
        sid and sample.get("session_id") == sid and timestamp
        and 0 <= (now - timestamp).total_seconds() <= 3600
        and (after is None or timestamp > after)
        and isinstance(value, (int, float)) and math.isfinite(value) and value >= 0
    )


def _lead_telemetry(sid: str, policy: dict[str, Any]) -> dict[str, Any]:
    unknown = {"session_id": sid, "utilisation": None, "sampled_at": None}
    if not sid:
        return unknown
    state_dir = Path(os.environ.get("HX_STATE_DIR", Path.home() / ".herdr-team" / "state"))
    path = state_dir / f"claude-status-{usage._safe_session_id(sid)}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("session_id") != sid:
            return unknown
        window = data.get("context_window") or {}
        current = window.get("current_usage")
        if current is not None:
            counters = [float(current["input_tokens"]),
                        float(current.get("cache_creation_input_tokens", 0)),
                        float(current.get("cache_read_input_tokens", 0))]
            if any(value < 0 or not math.isfinite(value) for value in counters):
                return unknown
            tokens = sum(counters)
        else:
            tokens = float(window.get("used_percentage")) * float(window.get("context_window_size")) / 100
        timestamp = data.get("timestamp") or dt.datetime.fromtimestamp(path.stat().st_mtime, dt.timezone.utc).isoformat()
        return _sample(sid, tokens, policy.get("context_limits", {}).get("claude-lead", 400000), timestamp)
    except (OSError, ValueError, TypeError, AttributeError, KeyError):
        return unknown


def _devin_clear_steps(name: str, task_id: str) -> list[str]:
    return [
        f"hx checkpoint write {task_id} --owner {name} --objective '<current objective>' --decisions '<decisions>' --errors '<exact errors or none>' --tests '<tests and results>' --next '<next action>'",
        f"Send /clear to the pane from Python: {host.prompt_command(name, '/clear')} (never Git Bash; MSYS rewrites /clear as a Windows path)",
        f"Re-brief the agent from .hx/checkpoints/{task_id}.md",
    ]


def write_lead_checkpoint(led: ledger.Ledger, **sections: str) -> Path:
    """A lead checkpoint has no worker task or ownership to update."""
    path = led.repo_root / ".hx" / "checkpoints" / "lead.md"
    saved = checkpoint._read_authored_sections(path)
    for field, heading in (("objective", "Objective"), ("decisions", "Decisions"),
                           ("errors", "Exact errors"), ("tests", "Tests"),
                           ("next_action", "Next action"), ("references", "References")):
        sections[field] = sections.get(field) or saved.get(heading, "")
    checkpoint._write_checkpoint(
        path, {"id": "lead"}, **sections,
        git_status=[], diff_stat=[], committed=[], ledger_rev=0,
    )
    return path


class Controller:
    def __init__(self, led: ledger.Ledger | None = None, policy: dict[str, Any] | None = None):
        self.ledger = led if led is not None else ledger.default_ledger()
        self.policy = policy if policy is not None else usage.load_policy()
        self.guard_path = Path(os.environ.get("HX_COMPACT_GUARD_PATH", self.ledger.leases_path.parent / "compact-guard.json"))
        self.recovery_path = self.ledger.leases_path.parent / "lead-recovery.json"
        self.resets_path = default_resets_path(self.ledger.leases_path)
        self.boundary_path = self.ledger.repo_root / ".hx" / "context-boundaries.jsonl"

    def record_reset(self, name: str, session_id: str | None = None) -> dict[str, Any]:
        """Record a reset the caller already sent to an idle worker."""
        agent = self._agent(name)
        return write_reset(self.resets_path, name, session_id or _session(agent))

    def reset(self, name: str, session_id: str | None = None) -> dict[str, Any]:
        """Send the provider's reset command from Python, then record it for pressure holds."""
        agent = self._agent(name)
        if self._is_lead(name):
            raise ValueError("hx reset targets a worker; the Claude lead self-compacts")
        if agent.get("status") not in READY:
            raise ValueError("agent is not idle; wait for its turn to finish before resetting")
        command = _reset_command(self.policy, agent.get("kind"))
        if not command:
            raise ValueError(
                f"no reset command is configured for {agent.get('kind')}; "
                f"send the reset yourself and record it with hx boundary {name} --reset-sent")
        sid = session_id or _session(agent)
        hold = f"reset:{name}"

        def reserve(leases: dict[str, Any]) -> dict[str, Any]:
            entry = leases.setdefault("sessions", {}).get(hold)
            if isinstance(entry, dict):
                observed = _session(agent)
                if (entry.get("state") == "reset-unknown" and observed
                        and observed != entry.get("session_id")):
                    del leases["sessions"][hold]
                    return {"action": "verified", "session_id": observed,
                            "from_sid": entry.get("session_id")}
                return {"action": "blocked"}
            leases["sessions"][hold] = {
                "agent": name, "state": "resetting", "reset_of": name,
                "session_id": sid, "requested_at": _now().isoformat()}
            return {"action": "reserved"}

        verdict = self.ledger.mutate_leases(reserve)
        if verdict["action"] == "verified":
            return {"action": "reset-verified", "command": command,
                    "new_session_id": verdict["session_id"],
                    **write_reset(self.resets_path, name, verdict.get("from_sid") or sid)}
        if verdict["action"] == "blocked":
            raise RuntimeError(
                f"a reset for {name} is still unverified; its reservation blocks another send")
        try:
            accepted = _send_slash(agent, command)
        except Exception:
            accepted = False
        if not accepted:
            def fail(leases: dict[str, Any]) -> None:
                entry = leases.get("sessions", {}).get(hold)
                if isinstance(entry, dict):
                    entry["state"] = "reset-unknown"
            self.ledger.mutate_leases(fail)
            raise RuntimeError("host did not accept the reset keys")
        # Proof of reset is a new provider session id through the binding, not the
        # key call's exit code; without it the outcome is unknown, not "sent".
        deadline = time.monotonic() + float(
            self.policy.get("context", {}).get("verification_timeout_seconds", 10))
        new_sid = ""
        while time.monotonic() < deadline:
            current = self._agent(name)
            observed = _session(current)
            if observed and observed != sid:
                new_sid = observed
                break
            time.sleep(0.25)

        def finish(leases: dict[str, Any]) -> None:
            sessions = leases.setdefault("sessions", {})
            if new_sid:
                sessions.pop(hold, None)
            elif isinstance(sessions.get(hold), dict):
                sessions[hold]["state"] = "reset-unknown"

        self.ledger.mutate_leases(finish)
        if not new_sid:
            raise RuntimeError("reset keys went out but no new provider session was observed; outcome unknown")
        return {"action": "reset-verified", "command": command,
                "new_session_id": new_sid, **write_reset(self.resets_path, name, sid)}

    def boundary(self, name: str, task_id: str | None = None, *,
                 work_item: str | None = None, session_id: str | None = None,
                 source: str = "boundary") -> dict[str, Any]:
        result = {**self.decide(name, task_id, session_id=session_id, work_item=work_item),
                  "source": source, "recorded_at": _now().isoformat(), "event_id": str(uuid.uuid4())}
        sample = result.get("telemetry")
        if sample is not None:
            sample = dict(sample)
            value = sample.get("utilisation")
            if not isinstance(value, (int, float)) or not math.isfinite(value):
                sample["utilisation"] = None
            result["telemetry"] = sample
        self.boundary_path.parent.mkdir(parents=True, exist_ok=True)
        with ledger.LeaseLock(self.boundary_path.with_suffix(".lock")):
            with self.boundary_path.open("a", encoding="utf-8") as output:
                output.write(json.dumps(result, allow_nan=False) + "\n")
                output.flush()
                os.fsync(output.fileno())
        return result

    def _lead_work_item(self, task_id: str | None, work_item: str | None) -> str:
        if work_item or task_id:
            return work_item or task_id
        path = self.ledger.repo_root / ".hx" / "checkpoints" / "lead.md"
        try:
            objective = checkpoint._extract_section(path.read_text(encoding="utf-8"), "Objective")
        except OSError:
            objective = None
        if not objective:
            raise ValueError("lead work item unknown; pass --work-item <item> or --task <id>, or write the lead checkpoint objective")
        return "objective:" + hashlib.sha256(objective.encode("utf-8")).hexdigest()[:16]

    def _agent(self, name: str) -> host.Member:
        prefix = usage.team_prefix(self.policy)
        if not (name.startswith(prefix) and usage.TEAM_AGENT.fullmatch(name)):
            raise ValueError("agent is outside the configured team")
        return agent_info(name)

    def _is_lead(self, name: str) -> bool:
        return name == usage.lead_name(self.policy, usage.team_prefix(self.policy))

    def _devin_compact_command(self) -> str | None:
        # devin.compact_command defaults to null: interactive /compact is unverified for Devin.
        value = (self.policy.get("devin") or {}).get("compact_command")
        return str(value) if value else None

    def _task(self, name: str, task_id: str | None, allow_in_progress: bool = False) -> dict[str, Any]:
        tasks = [t for t in self.ledger.list_tasks() if t.get("owner") == name]
        unfinished_tasks = [t for t in tasks if t.get("status") not in ledger.Ledger.CLOSED_STATUSES]
        if unfinished_tasks and not allow_in_progress:
            raise ValueError("agent has an unfinished task")
        if len(unfinished_tasks) > 1:
            raise ValueError("agent has multiple unfinished tasks")
        if not tasks:
            raise ValueError("no task registered for agent")
        task = unfinished_tasks[0] if unfinished_tasks else max(tasks, key=lambda t: t.get("updated", ""))
        if task_id and task["id"] != task_id:
            raise ValueError("task is not the current task or most recent boundary for agent")
        return task

    def _dirty_paths(self, workdir: Path) -> set[str]:
        rc, dirty, error = checkpoint._run_git(
            ["status", "--porcelain", "-z", "--untracked-files=all"], workdir)
        if rc:
            raise ValueError("cannot verify worktree: " + error)
        paths: set[str] = set()
        entries = iter(dirty.split("\0"))
        for entry in entries:
            if not entry:
                continue
            if len(entry) < 4:
                raise ValueError("cannot verify malformed worktree status")
            paths.add(ledger._canonical_path(workdir / entry[3:]))
            if "R" in entry[:2] or "C" in entry[:2]:
                original = next(entries, "")
                if not original:
                    raise ValueError("cannot verify incomplete worktree rename")
                paths.add(ledger._canonical_path(workdir / original))
        return paths

    def _check_worktrees(self, agent: host.Member, task: dict[str, Any]) -> None:
        workdir = Path(task.get("worktree") or self.ledger.repo_root)
        owned = {ledger._canonical_path(path) for path in task.get("files", [])}
        if self._dirty_paths(workdir) - owned:
            raise ValueError("worktree contains edits outside checkpoint task ownership")
        if not agent.get("cwd"):
            raise ValueError("cannot verify agent repository without pane cwd")
        pane_cwd = Path(agent["cwd"])
        if ledger._canonical_path(pane_cwd) == ledger._canonical_path(workdir):
            return
        rc, root, error = checkpoint._run_git(["rev-parse", "--show-toplevel"], pane_cwd)
        if rc or not root.strip():
            raise ValueError("cannot verify agent repository: " + error)
        pane_root = Path(root.strip())
        if ledger._canonical_path(pane_root) == ledger._canonical_path(workdir):
            return
        pane_dirty = self._dirty_paths(pane_root)
        if not pane_dirty:
            return
        pane_ledger_path = pane_root / ".hx" / "ledger.json"
        if pane_ledger_path.exists():
            pane_ledger = ledger.Ledger(
                repo_root=pane_root, ledger_path=pane_ledger_path,
                lock_path=pane_root / ".hx" / "ledger.lock",
                leases_path=self.ledger.leases_path)
            open_owned = {
                ledger._canonical_path(path)
                for active in pane_ledger.list_tasks()
                if active.get("owner") == task["owner"]
                and active.get("status") in ledger.Ledger.OPEN_STATUSES
                for path in active.get("files", [])
            }
            if pane_dirty & open_owned:
                raise ValueError("agent has dirty files owned by an open task in pane repository")
        raise ValueError("pane repository contains uncheckpointed edits")

    def _decide_lead(self, result: dict[str, Any], agent: host.Member, session_id: str | None,
                     task_id: str | None, work_item: str | None) -> dict[str, Any]:
        name = result["agent"]
        kind = agent.get("kind")
        # The lead seat can be any family; each has its own in-place command.
        lead_commands = {"claude": "/compact", "codex": "/clear", "opencode": "/new"}
        if kind == "devin":
            raise ValueError(
                "a Devin lead cannot be reset in place; restart its pane and hand "
                "the seat back with team\\Set-HerdrLead.ps1")
        lead_command = lead_commands.get(str(kind))
        if not lead_command:
            raise ValueError(f"no verified in-place recovery for a {kind} lead")
        sid = _session(agent) or session_id or ""
        sample = (_lead_telemetry(sid, self.policy) if kind == "claude"
                  else session_telemetry(agent, self.policy)[1])
        result.update(session_id=sid, telemetry=sample, agent_kind=kind)
        result.setdefault("task_id", "lead")
        if not sid:
            raise ValueError("provider session id unavailable; pass --session <session-id>")
        if not _fresh(sample, sid, _now()):
            raise _UnknownContext("session telemetry unknown or stale")
        item = self._lead_work_item(task_id, work_item)
        result["work_item"] = item
        with self.ledger._leases_lock():
            guard = (ledger._load_json(self.guard_path) or {}).get(name, {})
            task_guard = guard.get("tasks", {}).get(item, {})
            if task_guard.get("failed") or task_guard.get("count", 0) >= self.policy.get("context", {}).get("max_per_task", 2):
                raise ValueError("task compaction loop guard")
            last = _timestamp(guard.get("last_at"))
            if last and (_now() - last).total_seconds() < self.policy.get("context", {}).get("min_interval_min", 20) * 60:
                raise _DeferredContext("minimum compaction interval")
        threshold = self.policy.get("context", {}).get(
            "claude_lead_threshold" if kind == "claude" else "pressure_threshold", 0.6)
        if sample["utilisation"] < threshold:
            raise _RetainContext("below lead recovery threshold")
        if kind != "claude":
            # Reset providers change their session id; self-compacted must
            # prove the old session is gone before it records the cooldown.
            # Written only once the decision survives every refusal check.
            with self.ledger._leases_lock():
                pending = ledger._load_json(self.recovery_path) or {}
                pending[name] = {"from_sid": sid, "work_item": item, "at": _now().isoformat()}
                ledger._atomic_write_json(self.recovery_path, pending)
        quoted_name = "'" + name.replace("'", "''") + "'"
        quoted_sid = "'" + sid.replace("'", "''") + "'"
        quoted_item = "'" + item.replace("'", "''") + "'"
        # hc keys through the resolved executable: a typed slash command, never a
        # standalone herdr or a wrapped hc send (which the provider cannot run).
        command = f"Start-Sleep 60; {host.prompt_command(quoted_name, lead_command)}"
        background = "Start-Process powershell.exe -WindowStyle Hidden -ArgumentList '-NoProfile', '-Command', '" + command.replace("'", "''") + "'"
        result.update(action="self-compact", reason=f"{kind} lead above threshold; {lead_command} at the next turn boundary", steps=[
            f"hx checkpoint write lead --owner {quoted_name} --objective '<current objective>' --decisions '<decisions>' --errors '<exact errors or none>' --tests '<tests and results>' --next '<next action>'",
            background,
            "End the turn so the background /compact prompt can reach the idle pane.",
            f"After compaction resumes, confirm with hx context self-compacted {quoted_name} --session {quoted_sid if kind == 'claude' else '<new-session-id>'} --work-item {quoted_item}",
        ])
        return result

    def self_compacted(self, name: str, session_id: str, work_item: str | None = None) -> dict[str, Any]:
        agent = self._agent(name)
        kind = agent.get("kind")
        if not self._is_lead(name) or kind not in ("claude", "codex", "opencode"):
            raise ValueError("self-compacted requires a lead with an in-place recovery")
        live_sid = _session(agent)
        if kind == "claude":
            if not session_id or (live_sid and live_sid != session_id):
                raise ValueError("--session must identify the current Claude session")
        else:
            pending = (ledger._load_json(self.recovery_path) or {}).get(name)
            if not isinstance(pending, dict):
                raise ValueError("no reset recovery is pending for this lead")
            if (not session_id or session_id == pending.get("from_sid")
                    or (live_sid and live_sid != session_id)):
                raise ValueError("--session must name the new provider session after the reset")
        item = self._lead_work_item(None, work_item)
        with self.ledger._leases_lock():
            guards = ledger._load_json(self.guard_path) or {}
            guard = guards.setdefault(name, {"tasks": {}})
            task_guard = guard.setdefault("tasks", {}).setdefault(item, {"count": 0})
            task_guard["count"] += 1
            guard["last_at"] = _now().isoformat()
            ledger._atomic_write_json(self.guard_path, guards)
            if kind != "claude":
                remaining = ledger._load_json(self.recovery_path) or {}
                remaining.pop(name, None)
                ledger._atomic_write_json(self.recovery_path, remaining)
        return {"action": "self-compacted", "agent": name, "session_id": session_id, "task_id": "lead", "work_item": item}

    def decide(self, name: str, task_id: str | None = None, related: bool = False,
               session_id: str | None = None, work_item: str | None = None) -> dict[str, Any]:
        result = self._decide(name, task_id, related, session_id, work_item)
        result.setdefault("disposition", "recovery-needed" if result["action"] != "keep" else "keep")
        result["readiness"] = "ready" if result["disposition"] == "keep" else "held"
        return result

    def _decide(self, name: str, task_id: str | None, related: bool,
                session_id: str | None, work_item: str | None) -> dict[str, Any]:
        result: dict[str, Any] = {"action": "keep", "agent": name}
        if task_id:
            result["task_id"] = task_id
        try:
            agent = self._agent(name)
            if self._is_lead(name):
                return self._decide_lead(result, agent, session_id, task_id, work_item)
            sid, sample = session_telemetry(agent, self.policy)
            result.update(session_id=sid, telemetry=sample, agent_kind=agent.get("kind"))
            if agent.get("status") not in READY:
                raise _DeferredContext("agent is not idle")
            if not _fresh(sample, sid, _now()):
                raise _UnknownContext("session telemetry unknown or stale")
            if reset_pending(name, sid, self.policy, path=self.resets_path):
                result["reset_pending"] = True
                raise _RetainContext("recent reset; awaiting the new session's first sample")
            context_policy = self.policy.get("context", {})
            devin_clear = agent.get("kind") == "devin" and not self._devin_compact_command()
            pressure = sample["utilisation"] >= context_policy.get("pressure_threshold", 0.75)
            owned_tasks = [t for t in self.ledger.list_tasks() if t.get("owner") == name]
            if not owned_tasks and not task_id:
                if sample["utilisation"] < context_policy.get("boundary_threshold", 0.40):
                    raise _RetainContext("below boundary threshold")
                if devin_clear:
                    handoff = self.ledger.repo_root / ".hx" / "handoffs" / f"{name}.md"
                    result.update(action="handoff-needed", reason="no hx task is registered; use a fresh-owner handoff because native Devin compaction is unverified", steps=[
                        "Register the work with hx task add and checkpoint it, then run "
                        f"`hx handoff write {name} --task <id>` (writes {handoff.parent}).",
                        "Have the lead start a fresh owner in the same repository and send it "
                        "the line `hx handoff brief <file>` prints; do not clear the current "
                        "owner until its state is saved.",
                        f"Run hx boundary {name} after the fresh owner acknowledges the next action.",
                    ])
                else:
                    result.update(action="compact-needed", reason="no hx task is registered; native compaction recommended after registering the work and checkpointing", steps=[
                        "Have the lead register the current work with hx task add and assign its actual files to this owner.",
                        f"Write hx checkpoint write <task-id> with the current objective, decisions, errors, tests and next action, then run hx compact {name} --task <task-id>.",
                    ])
                return result
            task = self._task(name, task_id, allow_in_progress=pressure)
            result["task_id"] = task["id"]
            mid_task = task.get("status") in ledger.Ledger.OPEN_STATUSES
            result.update(pressure=pressure, mid_task=mid_task)
            self._check_worktrees(agent, task)
            with self.ledger._leases_lock():
                leases = self.ledger._load_leases()
                guards = ledger._load_json(self.guard_path) or {}
                if _has_blocking_session_lease(leases.get("sessions", {}), sid, name, task):
                    raise ValueError("session is leased; recovery required for compact-unknown")
                guard = guards.get(sid, {})
                task_guard = guard.get("tasks", {}).get(task["id"], {})
                if task_guard.get("failed") or task_guard.get("count", 0) >= self.policy.get("context", {}).get("max_per_task", 2):
                    raise ValueError("task compaction loop guard")
                last = _timestamp(guard.get("last_at"))
                if last and (_now() - last).total_seconds() < self.policy.get("context", {}).get("min_interval_min", 20) * 60:
                    raise _DeferredContext("minimum compaction interval")
            if related and not pressure:
                raise _RetainContext("next task continues the same work; retain warm context")
            if pressure:
                if devin_clear:
                    reason = "idle under context pressure during active task" if mid_task else "idle under context pressure"
                    result.update(action="clear-needed", reason=reason + "; Devin has no verified /compact, clear instead",
                                  steps=_devin_clear_steps(name, task["id"]))
                    return result
                result.update(action="compact", reason="idle under context pressure during active task" if mid_task else "idle under context pressure")
                return result
            if sample["utilisation"] < context_policy.get("boundary_threshold", 0.40):
                raise _RetainContext("below boundary threshold")
            if devin_clear:
                result.update(action="clear-needed", reason="idle at task boundary above threshold; Devin has no verified /compact, clear instead",
                              steps=_devin_clear_steps(name, task["id"]))
                return result
            result.update(action="compact", reason="idle at task boundary above threshold")
        except (ValueError, OSError, KeyError, RuntimeError, subprocess.SubprocessError) as exc:
            result["reason"] = str(exc)
            result["disposition"] = getattr(exc, "disposition", "refused")
        return result

    def _finish(self, sid: str, token: str, state: str | None, failed: bool) -> None:
        def finish(leases: dict[str, Any]) -> None:
            lease = leases.get("sessions", {}).get(sid)
            if not lease or lease.get("token") != token:
                raise RuntimeError("session lease lost; manual reconciliation required")
            guards = ledger._load_json(self.guard_path) or {}
            guards[sid]["tasks"][lease["task_id"]]["failed"] = failed
            ledger._atomic_write_json(self.guard_path, guards)
            if state:
                lease["state"] = state
            else:
                del leases["sessions"][sid]

        self.ledger.mutate_leases(finish)

    def compact(self, name: str, task_id: str | None = None, related: bool = False) -> dict[str, Any]:
        decision = self.decide(name, task_id, related)
        if decision.get("agent_kind") == "devin" and not self._devin_compact_command():
            return {**decision, "action": "keep", "reason": "hx compact does not run for Devin panes: /compact is unverified for Devin. Run the clear flow instead: write a checkpoint, send /clear from Python (never Git Bash), then re-brief from the checkpoint."}
        if decision["action"] != "compact":
            return decision
        sid, task_id = decision["session_id"], decision["task_id"]
        token = str(uuid.uuid4())
        started = _now()

        def reserve(ledger_data: dict[str, Any], leases: dict[str, Any]) -> dict[str, Any]:
            sessions = leases.setdefault("sessions", {})
            task = ledger_data["tasks"].get(task_id)
            if task is None or task.get("owner") != name:
                return {"action": "keep", "reason": "task changed before reservation"}
            if _has_blocking_session_lease(sessions, sid, name, task):
                return {"action": "keep", "reason": "session already leased"}
            if decision.get("mid_task"):
                if not decision.get("pressure") or task.get("status") not in ledger.Ledger.OPEN_STATUSES:
                    return {"action": "keep", "reason": "pressure task changed before reservation"}
            elif task.get("status") not in {"done", "failed"}:
                return {"action": "keep", "reason": "task changed before reservation"}
            if any(t.get("owner") == name and t.get("id") != task_id
                   and t.get("status") not in {"done", "failed"}
                   for t in ledger_data["tasks"].values()):
                return {"action": "keep", "reason": "agent acquired unfinished work"}
            guards = ledger._load_json(self.guard_path) or {}
            guard = guards.setdefault(sid, {"tasks": {}})
            task_guard = guard.setdefault("tasks", {}).setdefault(task_id, {"count": 0})
            last = _timestamp(guard.get("last_at"))
            if (task_guard.get("failed") or task_guard["count"] >= self.policy.get("context", {}).get("max_per_task", 2)
                    or (last and (started - last).total_seconds() < self.policy.get("context", {}).get("min_interval_min", 20) * 60)):
                return {"action": "keep", "reason": "compaction guard changed before reservation"}
            sessions[sid] = {"agent": name, "task_id": task_id, "token": token,
                             "state": "compacting", "requested_at": started.isoformat()}
            guard["last_at"] = started.isoformat()
            task_guard["count"] += 1
            ledger._atomic_write_json(self.guard_path, guards)
            return {"action": "reserved", "task": task}

        reservation = self.ledger.mutate_leases_with_repo(reserve)
        if reservation["action"] != "reserved":
            return reservation
        task = reservation["task"]
        submitted = False
        try:
            validation = checkpoint.validate_checkpoint(task_id, ledger=self.ledger)
            if not validation["ok"]:
                raise ValueError("checkpoint validation failed: " + "; ".join(validation["errors"]))
            path = Path(validation["checkpoint"])
            text = path.read_text(encoding="utf-8")
            for section in ("Decisions", "Exact errors", "Next action"):
                if not checkpoint._extract_section(text, section):
                    raise ValueError(f"agent-authored checkpoint section empty: {section}")
            current = self._agent(name)
            if _session(current) != sid or current.get("status") not in READY:
                raise ValueError("session changed or agent became busy before send")
            self._check_worktrees(current, task)
            current_task = self.ledger.get_task(task_id)
            if current_task["rev"] != task["rev"]:
                raise ValueError("task changed during checkpoint validation")
            before = telemetry(current, self.policy)
            if not _fresh(before, sid, _now()):
                raise ValueError("pre-request telemetry unknown or stale")
            started = _now()

            def mark_requested(leases: dict[str, Any]) -> None:
                lease = leases.get("sessions", {}).get(sid)
                if not lease or lease.get("token") != token:
                    raise ValueError("session lease lost before send")
                lease["requested_at"] = started.isoformat()

            self.ledger.mutate_leases(mark_requested)
            current = self._agent(name)
            if _session(current) != sid or current.get("status") not in READY:
                raise ValueError("session changed or agent became busy immediately before send")
            submitted = True
            compact_command = self._devin_compact_command() if current.get("kind") == "devin" else "/compact"
            # Slash commands run only as typed keys; `hc send` wraps them so the
            # provider never sees a real /compact.
            sent_turn = current.get("turn")
            if not _send_slash(current, compact_command):
                raise RuntimeError("host did not accept the compaction keys")
            settle_deadline = time.monotonic() + float(
                self.policy.get("context", {}).get("compact_wait_seconds", 300))
            # The turn must actually start: status leaves READY or the turn
            # advances. Members with no turn counter cannot prove a start, so
            # the telemetry verification below stays the arbiter for them.
            if isinstance(sent_turn, int) and not isinstance(sent_turn, bool):
                while True:
                    current = self._agent(name)
                    if _session(current) != sid:
                        raise ValueError("session changed during compaction")
                    if (current.get("status") not in READY
                            or (isinstance(current.get("turn"), int)
                                and current["turn"] > sent_turn)):
                        break
                    if time.monotonic() >= settle_deadline:
                        raise RuntimeError("compaction turn never started")
                    time.sleep(0.5)
            while current.get("status") not in READY:
                after = telemetry(current, self.policy)
                if (_session(current) == sid and _fresh(after, sid, _now())
                        and after["utilisation"] < before["utilisation"]):
                    break  # finished between polls without a visible turn bump
                if time.monotonic() >= settle_deadline:
                    raise RuntimeError("compaction turn did not end before timeout")
                time.sleep(0.5)
                current = self._agent(name)
            deadline = settle_deadline
            while True:
                current = self._agent(name)
                after = telemetry(current, self.policy)
                if (_session(current) == sid and current.get("status") in READY
                        and _fresh(after, sid, _now(), after=started)
                        and after["utilisation"] < before["utilisation"]):
                    break
                if _session(current) != sid or time.monotonic() >= deadline:
                    raise ValueError("compaction completion not verified in same session with lower fresh telemetry")
                time.sleep(.25)
            next_action = checkpoint._extract_section(text, "Next action")
            lead = usage.lead_name(self.policy, usage.team_prefix(self.policy))
            message = f"[team from {lead}] Context compacted. Read {path}; reply with {token} followed by the checkpoint Next action only."
            sent = _send_checked(name, message, wait=True, until=("idle", "done"), timeout_ms=300000)
            if not sent["ended"]:
                raise RuntimeError(sent["reason"] or "host did not confirm the acknowledgement turn ended")
            current = self._agent(name)
            reply = _herdr_read(name)
            if _session(current) != sid or current.get("status") not in READY or not _has_acknowledgement(reply, f"{token} {next_action}"):
                raise ValueError("checkpoint next action acknowledgement not verified")
            self._finish(sid, token, None, False)
            return {**decision, "action": "compacted", "telemetry": after}
        except Exception as exc:
            self._finish(sid, token, "compact-unknown" if submitted else None, True)
            return {**decision, "action": "compact-unknown" if submitted else "keep", "reason": str(exc)}

    def recover(self, name: str) -> dict[str, Any]:
        """Release an unknown outcome only after same-session idle and fresh telemetry."""
        agent = self._agent(name)
        sid = _session(agent)
        sample = telemetry(agent, self.policy)

        def recover(leases: dict[str, Any]) -> dict[str, Any]:
            sessions = leases.get("sessions", {})
            lease = sessions.get(sid)
            if lease and lease.get("agent") == name and lease.get("state") == "compact-unknown":
                requested = _timestamp(lease.get("requested_at"))
                if not requested or agent.get("status") not in READY or not _fresh(sample, sid, _now(), after=requested):
                    return {"action": "keep", "reason": "recovery requires same-session idle and post-request telemetry"}
                del sessions[sid]
                return {"action": "recovered", "session_id": sid, "reason": "reservation released; task remains in no-retry guard"}
            reset_hold = sessions.get(f"reset:{name}")
            if isinstance(reset_hold, dict) and reset_hold.get("state") == "reset-unknown":
                held_sid = reset_hold.get("session_id")
                # The session moved on: the reset provably ran — or the agent is
                # idle on the same session: the send never landed. Both are safe
                # to release; a busy agent keeps the reservation.
                if sid != held_sid or agent.get("status") in READY:
                    del sessions[f"reset:{name}"]
                    return {"action": "recovered", "session_id": sid,
                            "reason": "reset reservation released"}
                return {"action": "keep", "reason": "reset outcome still unverified"}
            return {"action": "keep", "reason": "no matching unknown outcome"}

        return self.ledger.mutate_leases(recover)
