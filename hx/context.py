"""Session-bound telemetry and protected, boundary-only native compaction."""

from __future__ import annotations

import datetime as dt
import json
import math
import os
import sqlite3
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from hx import checkpoint, ledger, usage


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _timestamp(value: Any) -> dt.datetime | None:
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else None
    except (ValueError, TypeError, AttributeError):
        return None


def _herdr(*args: str, timeout: float = 30) -> dict[str, Any]:
    result = subprocess.run(
        ["herdr", "agent", *args], capture_output=True, text=True,
        check=True, timeout=timeout, shell=False,
    )
    data = json.loads(result.stdout)
    if data.get("error") or (data.get("result") or {}).get("type") in {"timeout", "agent_prompt_stalled", "agent_blocked", "error"}:
        raise RuntimeError("Herdr did not complete the request: " + result.stdout)
    return data


def agent_info(name: str) -> dict[str, Any]:
    return _herdr("get", name)["result"]["agent"]


def _session(agent: dict[str, Any]) -> str:
    return (agent.get("agent_session") or {}).get("value") or ""


def _herdr_read(name: str, lines: int = 60) -> str:
    """`herdr agent read` prints the pane as plain text, not JSON."""
    result = subprocess.run(
        ["herdr", "agent", "read", name, "--source", "recent-unwrapped", "--lines", str(lines)],
        capture_output=True, text=True, check=True, timeout=30, shell=False,
    )
    return result.stdout


def _squash(text: str) -> str:
    # TUIs hard-wrap long replies (OpenCode splits "re-" / "review"), so compare without whitespace.
    return "".join(text.split())


def _has_acknowledgement(value: Any, expected: str) -> bool:
    if isinstance(value, str):
        return _squash(expected) in _squash(value)
    if isinstance(value, dict):
        return any(_has_acknowledgement(item, expected) for item in value.values())
    if isinstance(value, list):
        return any(_has_acknowledgement(item, expected) for item in value)
    return False


def _sample(session_id: str, tokens: Any, limit: Any, sampled_at: str) -> dict[str, Any]:
    try:
        fraction = float(tokens) / float(limit)
        if float(tokens) < 0 or float(limit) <= 0 or not math.isfinite(fraction):
            raise ValueError("invalid context counters")
    except (ValueError, TypeError, ZeroDivisionError):
        return {"session_id": session_id, "utilisation": None, "sampled_at": sampled_at}
    return {"session_id": session_id, "utilisation": fraction, "sampled_at": sampled_at}


def telemetry(agent: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    """Read only the provider session named by Herdr; never pick newest globally."""
    sid = _session(agent)
    unknown = {"session_id": sid, "utilisation": None, "sampled_at": None}
    if not sid:
        return unknown
    try:
        if agent.get("agent") == "codex":
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
        elif agent.get("agent") == "claude":
            state_dir = Path(os.environ.get("HX_STATE_DIR", Path.home() / ".herdr-team" / "state"))
            path = state_dir / f"claude-status-{sid}.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("session_id") == sid:
                timestamp = data.get("timestamp") or dt.datetime.fromtimestamp(path.stat().st_mtime, dt.timezone.utc).isoformat()
                return _sample(sid, (data.get("context_window") or {}).get("used_percentage"), 100, timestamp)
        elif agent.get("agent") == "opencode":
            with sqlite3.connect(usage.default_opencode_db_path().resolve().as_uri() + "?mode=ro", uri=True) as conn:
                rows = conn.execute("SELECT time_created, data FROM message WHERE session_id = ? ORDER BY time_created DESC", (sid,))
                for created, raw in rows:
                    data = json.loads(raw)
                    if data.get("role") != "assistant":
                        continue
                    tokens = data.get("tokens") or {}
                    model = data.get("modelID")
                    limit = policy.get("context_limits", {}).get(model)
                    timestamp = dt.datetime.fromtimestamp(created / 1000, dt.timezone.utc).isoformat()
                    return _sample(sid, tokens.get("input") + (tokens.get("cache") or {}).get("read", 0), limit, timestamp)
    except (OSError, ValueError, TypeError, sqlite3.Error):
        pass
    return unknown


def _fresh(sample: dict[str, Any], sid: str, now: dt.datetime, after: dt.datetime | None = None) -> bool:
    timestamp = _timestamp(sample.get("sampled_at"))
    value = sample.get("utilisation")
    return bool(
        sid and sample.get("session_id") == sid and timestamp
        and 0 <= (now - timestamp).total_seconds() <= 3600
        and (after is None or timestamp > after)
        and isinstance(value, (int, float)) and math.isfinite(value) and value >= 0
    )


class Controller:
    def __init__(self, led: ledger.Ledger | None = None, policy: dict[str, Any] | None = None):
        self.ledger = led if led is not None else ledger.default_ledger()
        self.policy = policy if policy is not None else usage.load_policy()
        self.guard_path = Path(os.environ.get("HX_COMPACT_GUARD_PATH", self.ledger.leases_path.parent / "compact-guard.json"))

    def _agent(self, name: str) -> dict[str, Any]:
        prefix = self.policy.get("team_prefix", "hx-")
        if name not in {prefix + kind for kind in ("codex", "opencode", "claude")}:
            raise ValueError("agent is outside the configured team")
        return agent_info(name)

    def _task(self, name: str, task_id: str | None) -> dict[str, Any]:
        tasks = [t for t in self.ledger.list_tasks() if t.get("owner") == name]
        if any(t.get("status") not in {"done", "failed"} for t in tasks):
            raise ValueError("agent has an unfinished task")
        if not tasks:
            raise ValueError("no completed or failed task for agent")
        task = max(tasks, key=lambda t: t.get("updated", ""))
        if task_id and task["id"] != task_id:
            raise ValueError("task is not the most recent boundary for agent")
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

    def _check_worktrees(self, agent: dict[str, Any], task: dict[str, Any]) -> None:
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

    def decide(self, name: str, task_id: str | None = None, related: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {"action": "keep", "agent": name}
        try:
            agent = self._agent(name)
            sid = _session(agent)
            sample = telemetry(agent, self.policy)
            result.update(session_id=sid, telemetry=sample)
            if agent.get("agent") == "claude":
                raise ValueError("Claude uses native auto-compaction")
            if agent.get("agent_status") != "idle":
                raise ValueError("agent is not idle")
            task = self._task(name, task_id)
            result["task_id"] = task["id"]
            self._check_worktrees(agent, task)
            if not _fresh(sample, sid, _now()):
                raise ValueError("session telemetry unknown or stale")
            with self.ledger._leases_lock():
                leases = self.ledger._load_leases()
                guards = ledger._load_json(self.guard_path) or {}
                if sid in leases.get("sessions", {}) or any(item.get("agent") == name for item in leases.get("sessions", {}).values()):
                    raise ValueError("session is leased; recovery required for compact-unknown")
                guard = guards.get(sid, {})
                task_guard = guard.get("tasks", {}).get(task["id"], {})
                if task_guard.get("failed") or task_guard.get("count", 0) >= self.policy.get("context", {}).get("max_per_task", 2):
                    raise ValueError("task compaction loop guard")
                last = _timestamp(guard.get("last_at"))
                if last and (_now() - last).total_seconds() < self.policy.get("context", {}).get("min_interval_min", 20) * 60:
                    raise ValueError("minimum compaction interval")
            if related:
                raise ValueError("next task continues the same work; retain warm context")
            if sample["utilisation"] < self.policy.get("context", {}).get("boundary_threshold", 0.40):
                raise ValueError("below boundary threshold")
            result.update(action="compact", reason="idle at task boundary above threshold")
        except (ValueError, OSError, KeyError, RuntimeError, subprocess.SubprocessError) as exc:
            result["reason"] = str(exc)
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
        if decision["action"] != "compact":
            return decision
        sid, task_id = decision["session_id"], decision["task_id"]
        token = str(uuid.uuid4())
        started = _now()

        def reserve(ledger_data: dict[str, Any], leases: dict[str, Any]) -> dict[str, Any]:
            sessions = leases.setdefault("sessions", {})
            if sid in sessions or any(item.get("agent") == name for item in sessions.values()):
                return {"action": "keep", "reason": "session already leased"}
            task = ledger_data["tasks"][task_id]
            if task.get("owner") != name or task.get("status") not in {"done", "failed"}:
                return {"action": "keep", "reason": "task changed before reservation"}
            if any(t.get("owner") == name and t.get("status") not in {"done", "failed"}
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
            if _session(current) != sid or current.get("agent_status") != "idle":
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
            if _session(current) != sid or current.get("agent_status") != "idle":
                raise ValueError("session changed or agent became busy immediately before send")
            submitted = True
            _herdr("prompt", name, "/compact", "--wait", "--until", "idle", "--timeout", "300000", timeout=310)
            deadline = time.monotonic() + self.policy.get("context", {}).get("verification_timeout_seconds", 10)
            while True:
                current = self._agent(name)
                after = telemetry(current, self.policy)
                if (_session(current) == sid and current.get("agent_status") == "idle"
                        and _fresh(after, sid, _now(), after=started)
                        and after["utilisation"] < before["utilisation"]):
                    break
                if _session(current) != sid or time.monotonic() >= deadline:
                    raise ValueError("compaction completion not verified in same session with lower fresh telemetry")
                time.sleep(.25)
            next_action = checkpoint._extract_section(text, "Next action")
            message = f"[team from {self.policy.get('team_prefix', 'hx-')}claude] Context compacted. Read {path}; reply with {token} followed by the checkpoint Next action only."
            _herdr("prompt", name, message, "--wait", "--until", "idle", "--timeout", "300000", timeout=310)
            current = self._agent(name)
            reply = _herdr_read(name)
            if _session(current) != sid or current.get("agent_status") != "idle" or not _has_acknowledgement(reply, f"{token} {next_action}"):
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
            lease = leases.get("sessions", {}).get(sid)
            if not lease or lease.get("agent") != name or lease.get("state") != "compact-unknown":
                return {"action": "keep", "reason": "no matching unknown compaction"}
            requested = _timestamp(lease.get("requested_at"))
            if not requested or agent.get("agent_status") != "idle" or not _fresh(sample, sid, _now(), after=requested):
                return {"action": "keep", "reason": "recovery requires same-session idle and post-request telemetry"}
            del leases["sessions"][sid]
            return {"action": "recovered", "session_id": sid, "reason": "reservation released; task remains in no-retry guard"}

        return self.ledger.mutate_leases(recover)
