"""hx.ocrun: OpenCode worker wrapper with two launch modes.

``headless`` keeps the original background behaviour: it runs
``opencode run -m opencode-go/<model> --format json`` and aggregates the JSON
event stream. A headless run exits 0 even when the model silently failed
(docs/FINDINGS.md items 4, 6, 7), so ocrun never trusts the exit code.

``visible`` (the default when HERDR_ENV=1) shows every run in its own
console window: it writes ``<repo>/.hx/runs/<title>/{prompt.md, run.ps1,
done.json}`` and opens the script via ``powershell -Command Start-Process
powershell -ArgumentList -NoExit,...,-File,<run.ps1>``. The window stays
open so the user can read it; ``done.json`` marks completion. Results come
from ``opencode export <sessionID>`` and are classified with the same
code path as headless runs.

Detected silent failures and their classification (shared by both modes):

- ``{"type":"error"}`` event in the stream -> ``quota`` when the text matches a
  rate limit / 429 / quota / "Global regions" marker, else ``capability``.
- auto-rejected permission ask -> ``tool`` failure.
- final step finish reason ``length`` with zero output tokens -> ``capability``
  (reasoning budget exhausted, no output).

Every run appends one outcome line to ``<repo>/.hx/routing.jsonl``.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from hx import paths

PROVIDER = "opencode-go"

VISIBLE_PROMPT_FILE = "prompt.md"
VISIBLE_RUN_SCRIPT = "run.ps1"
VISIBLE_DONE_FILE = "done.json"
VISIBLE_LAST_FILE = "last.txt"
VISIBLE_DONE_MARKER = "HX-DONE"
POLL_INTERVAL_S = 2.0
TITLE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")

CODEX_WARNING_MARKERS = ("prefer_mxc", "hook timeout")
CODEX_MAX_ROLLOUT_FILES = 200

FINISH_ERROR_REASONS = {"error"}
TERMINAL_FINISH_REASONS = {"stop", "end_turn"}
PROCESS_TREE_KILL_TIMEOUT = 10.0
PROCESS_TREE_DRAIN_TIMEOUT = 10.0
PROCESS_TREE_POLL_TIMEOUT = 2.0

QUOTA_MARKERS = (
    "rate limit",
    "rate_limit",
    "ratelimit",
    "429",
    "too many requests",
    "quota",
    "global regions",
)


def _repo_root(cwd: Path | None = None) -> Path:
    if cwd is None:
        return paths.project_root()
    # paths.project_root() resolves against os.getcwd() and the HX_PROJECT_ROOT
    # env var. Pin the env var to the effective cwd so routing state is written
    # to the target project, not the caller's project.
    old = os.environ.get("HX_PROJECT_ROOT")
    try:
        os.environ["HX_PROJECT_ROOT"] = str(cwd)
        return paths.project_root()
    finally:
        if old is None:
            os.environ.pop("HX_PROJECT_ROOT", None)
        else:
            os.environ["HX_PROJECT_ROOT"] = old


def default_routing_log(cwd: Path | None = None) -> Path:
    env = os.environ.get("HX_ROUTING_LOG")
    if env:
        return Path(env)
    return _repo_root(cwd) / ".hx" / "routing.jsonl"


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _strings(value: Any) -> list[str]:
    """Recursively collect every string inside an arbitrary JSON blob."""
    found: list[str] = []
    if isinstance(value, str):
        found.append(value)
    elif isinstance(value, dict):
        for k, v in value.items():
            found.extend(_strings(k))
            found.extend(_strings(v))
    elif isinstance(value, (list, tuple)):
        for item in value:
            found.extend(_strings(item))
    return found


def classify_error_text(text: str) -> str:
    lowered = (text or "").lower()
    for marker in QUOTA_MARKERS:
        if marker in lowered:
            return "quota"
    return "capability"


def _looks_like_permission_rejection(status: Any, error_text: str) -> bool:
    if status in {"denied", "rejected"}:
        return True
    blob = (error_text or "").lower()
    if "permission" in blob and any(word in blob for word in ("denied", "rejected", "refused")):
        return True
    return "external_directory" in blob


def parse_events(stream: str) -> dict[str, Any]:
    """Aggregate a raw ``--format json`` stdout event stream.

    Tolerates malformed lines and missing fields.
    """
    aggregated: dict[str, Any] = {
        "session_id": None,
        "cost": 0.0,
        "tokens": {
            "total": 0,
            "input": 0,
            "output": 0,
            "reasoning": 0,
            "cache_read": 0,
            "cache_write": 0,
        },
        "tool_calls": 0,
        "tool_errors": 0,
        "permission_rejected": None,
        "error_reasons": [],
        "finish_reason": None,
        "finish_tokens_output": None,
        "terminal_finish_reason": None,
        "has_unresolved_tool_error": False,
    }

    def accumulate(part: dict[str, Any]) -> None:
        tokens = part.get("tokens") or {}
        cache = tokens.get("cache") or {}
        mapping = (
            ("input", "input"),
            ("output", "output"),
            ("reasoning", "reasoning"),
            ("total", "total"),
            ("read", "cache_read"),
            ("write", "cache_write"),
        )
        for src_key, dst_key in mapping:
            value = tokens.get(src_key)
            if not isinstance(value, (int, float)) and isinstance(cache, dict):
                value = cache.get(src_key)
            if isinstance(value, (int, float)):
                aggregated["tokens"][dst_key] += value

    last_tool_error_idx = -1
    last_success_step_idx = -1
    line_idx = 0

    def _step_finish_success(reason: Any) -> bool:
        return reason is not None and reason not in FINISH_ERROR_REASONS

    for raw in stream.splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            evt = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(evt, dict):
            continue
        kind = evt.get("type")
        part = evt.get("part")
        if not isinstance(part, dict):
            part = {}

        if evt.get("sessionID"):
            aggregated["session_id"] = evt["sessionID"]
        elif part.get("sessionID"):
            aggregated["session_id"] = part["sessionID"]

        if kind == "error":
            aggregated["error_reasons"].append("\n".join(_strings(evt)))

        if kind == "step_finish":
            accumulate(part)
            cost = part.get("cost")
            if isinstance(cost, (int, float)):
                aggregated["cost"] += float(cost)
            out = (part.get("tokens") or {}).get("output")
            reason = part.get("reason")
            aggregated["finish_reason"] = reason
            aggregated["finish_tokens_output"] = out if isinstance(out, (int, float)) else 0
            aggregated["terminal_finish_reason"] = reason
            if _step_finish_success(reason):
                last_success_step_idx = line_idx

        part_type = part.get("type")
        if kind in {"tool_use", "tool"} or part_type in {"tool", "tool_use"}:
            aggregated["tool_calls"] += 1
            state = part.get("state") or {}
            if not isinstance(state, dict):
                state = {}
            status = state.get("status")
            error_blob = "\n".join(_strings(state) + _strings(part)).lower()
            tool_error = bool(part.get("error") or state.get("error")) or (
                status not in (None, "completed", "running", "pending")
            )
            if tool_error:
                aggregated["tool_errors"] += 1
                last_tool_error_idx = line_idx
            # Only failed tool calls can be permission rejections, and only their error text counts:
            # successful reads carry file contents that may mention "rejected" (docs/FINDINGS.md 10).
            error_only = "\n".join(
                _strings(state.get("error")) + _strings(part.get("error"))
            ).lower()
            if (
                tool_error
                and aggregated["permission_rejected"] is None
                and _looks_like_permission_rejection(status, error_only)
            ):
                aggregated["permission_rejected"] = error_only[:300] or "auto-rejected permission"

        line_idx += 1

    aggregated["has_unresolved_tool_error"] = last_tool_error_idx > last_success_step_idx
    return aggregated


def declared_variants(model: str) -> list[str]:
    """Variants the model declares, from policy.opencode_variants.

    Measured with ``opencode models --verbose``; empty when it declares none
    (a --variant for an undeclared effort is silently ignored by these
    models, docs/FINDINGS.md / bench/RESULTS.md effort table).
    """
    from hx.usage import load_policy

    try:
        policy = load_policy()
    except Exception:
        return []
    model_name = model.split("/")[-1]
    return policy.get("opencode_variants", {}).get(model_name) or []


def _resolved_variant(model: str, variant: str | None) -> str | None:
    if not variant or variant not in declared_variants(model):
        return None
    return variant


def build_command(
    prompt: str,
    model: str,
    variant: str | None,
    cwd: Path,
    title: str | None,
) -> list[str]:
    model_id = model if "/" in model else f"{PROVIDER}/{model}"
    cmd = ["opencode", "run", "-m", model_id]
    variant = _resolved_variant(model, variant)
    if variant:
        cmd += ["--variant", variant]
    cmd += ["--format", "json", "--dir", str(cwd)]
    if title:
        cmd += ["--title", title]
    cmd.append(prompt)
    return cmd


def _kill_process_tree(proc: subprocess.Popen) -> bool:
    """Kill ``proc`` and its descendants; return True when confirmed dead."""
    if sys.platform == "win32":
        try:
            result = subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=PROCESS_TREE_KILL_TIMEOUT,
                check=False,
            )
            if result.returncode != 0:
                return False
        except Exception:
            return False
    else:
        try:
            pgid = os.getpgid(proc.pid)
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            return True
        except Exception:
            return False
        # Brief grace period, then SIGKILL.
        try:
            proc.wait(timeout=2.0)
            return True
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            return True
        except Exception:
            return False

    try:
        proc.wait(timeout=PROCESS_TREE_POLL_TIMEOUT)
    except subprocess.TimeoutExpired:
        return False
    return True


def _sanitize_title(title: str) -> str | None:
    """Keep only a Windows-safe run-directory slug, or None when unusable."""
    if not title:
        return None
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", title.strip())
    slug = re.sub(r"-{2,}", "-", slug).strip(".-_")
    if not slug or not TITLE_RE.match(slug):
        return None
    return slug


def visible_run_dir(cwd: Path, title: str) -> Path:
    return _repo_root(cwd) / ".hx" / "runs" / title


def write_run_script(
    run_dir: Path,
    prompt: str,
    model: str,
    variant: str | None,
    title: str,
    cwd: Path,
) -> dict[str, Path]:
    """Write prompt.md and run.ps1 under ``<repo>/.hx/runs/<title>/``."""
    run_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = run_dir / VISIBLE_PROMPT_FILE
    prompt_path.write_text(prompt, encoding="utf-8")

    model_id = model if "/" in model else f"{PROVIDER}/{model}"
    # Single-quoted PowerShell literals double embedded single quotes,
    # so every field stays one literal token.
    variant = _resolved_variant(model, variant)

    def _ps(text: str) -> str:
        return "'" + text.replace("'", "''") + "'"

    cmd_parts = ["opencode", "run", "-m", _ps(model_id)]
    if variant:
        cmd_parts += ["--variant", _ps(variant)]
    cmd_parts += ["--title", _ps(title), "--dir", _ps(str(cwd))]
    cmd_parts += ["(Get-Content -Raw %s)" % _ps(str(prompt_path))]
    script = "\n".join(
        [
            "$host.ui.RawUI.WindowTitle = %s" % _ps(title),
            "clear-host",
            "& %s" % " ".join(cmd_parts),
            "$code = $LASTEXITCODE",
            "@{ exit_code = $code } | ConvertTo-Json | Set-Content -Encoding utf8 %s" % _ps(str(run_dir / VISIBLE_DONE_FILE)),
            "Write-Host %s" % VISIBLE_DONE_MARKER,
        ]
    )
    script_path = run_dir / VISIBLE_RUN_SCRIPT
    script_path.write_text(script, encoding="utf-8")
    return {"prompt": prompt_path, "script": script_path, "done": run_dir / VISIBLE_DONE_FILE}


def launch_visible(script_path: Path) -> bool:
    """Open the run script in a new visible PowerShell window.

    Uses a fixed argv (never a shell) so no quoting is hand-rolled. The
    arguments are passed as comma-separated single-quoted PowerShell literals:
    powershell.exe -Command re-parses the command line, where a bare
    comma list errors out ("missing argument in parameter list") and a
    space-joined list is rejected by Start-Process ("positional parameter
    cannot be found").
    """
    parts = ["-NoExit", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script_path)]
    arg_list = ",".join(
        "'" + part.replace("'", "''") + "'" for part in parts
    )
    try:
        proc = subprocess.Popen(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                "Start-Process powershell -ArgumentList " + arg_list,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        proc.wait(timeout=30)
        return True
    except Exception:
        return False


def write_codex_run_script(
    run_dir: Path,
    prompt: str,
    model: str,
    effort: str | None,
    title: str,
    cwd: Path,
) -> dict[str, Path]:
    """Write prompt.md and run.ps1 for a codex exec visible window.

    The window -o writes the agent's last message to run_dir/last.txt so the
    aggregation can require a non-empty final answer.
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = run_dir / VISIBLE_PROMPT_FILE
    prompt_path.write_text(prompt, encoding="utf-8")

    def _ps(text: str) -> str:
        return "'" + text.replace("'", "''") + "'"

    cmd_parts = ["codex", "exec", "-p", "trusted-dev", "-m", _ps(model)]
    if effort:
        cmd_parts += ["-c", _ps(f"model_reasoning_effort={effort}")]
    cmd_parts += ["-s", "workspace-write", "--skip-git-repo-check"]
    cmd_parts += ["-C", _ps(str(cwd)), "-o", _ps(str(run_dir / VISIBLE_LAST_FILE))]
    cmd_parts += ["(Get-Content -Raw %s)" % _ps(str(prompt_path))]
    script = "\n".join(
        [
            "$host.ui.RawUI.WindowTitle = %s" % _ps(title),
            "clear-host",
            "& %s" % " ".join(cmd_parts),
            "$code = $LASTEXITCODE",
            "@{ exit_code = $code } | ConvertTo-Json | Set-Content -Encoding utf8 %s" % _ps(str(run_dir / VISIBLE_DONE_FILE)),
            "Write-Host %s" % VISIBLE_DONE_MARKER,
        ]
    )
    script_path = run_dir / VISIBLE_RUN_SCRIPT
    script_path.write_text(script, encoding="utf-8")
    return {
        "prompt": prompt_path,
        "script": script_path,
        "done": run_dir / VISIBLE_DONE_FILE,
        "last": run_dir / VISIBLE_LAST_FILE,
    }


def find_session_by_title(
    title: str,
    cwd: Path | None = None,
    settle_seconds: float = 30.0,
) -> str | None:
    """Newest opencode session whose title matches exactly (argv, no shell).

    The listing is project-scoped (per git root), so it must be queried from
    the run's cwd. Results may lag the run's exit a moment, hence the settle
    retry loop.
    """
    deadline = time.monotonic() + settle_seconds
    while True:
        try:
            result = subprocess.run(
                ["opencode", "session", "list", "--format", "json", "-n", "50"],
                capture_output=True,
                timeout=60,
                cwd=None if cwd is None else str(cwd),
            )
        except Exception:
            return None
        try:
            sessions = json.loads(result.stdout.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            return None
        if isinstance(sessions, list):
            for item in sessions:
                if isinstance(item, dict) and item.get("title") == title and item.get("id"):
                    return item["id"]
        if time.monotonic() >= deadline:
            return None
        time.sleep(POLL_INTERVAL_S)


def export_session_text(session_id: str | None) -> tuple[str | None, str | None]:
    """Export a session as JSON text; stdout has the payload, stderr a banner."""
    if not session_id:
        return None, None
    try:
        result = subprocess.run(
            ["opencode", "export", session_id],
            capture_output=True,
            timeout=300,
        )
        return result.stdout.decode("utf-8", errors="replace"), None
    except Exception as exc:
        return None, str(exc)


def parse_export(payload: str) -> dict[str, Any]:
    """Aggregate an ``opencode export`` document with parse_events.

    Export is {"info", "messages": [{"info", "parts": [...]}]}; parts carry the
    same shapes as the JSON stream events (tool parts nest under "state"), so
    the shared aggregator just needs the flattened part list with ``step-
    finish`` renamed to ``step_finish``.
    """
    return parse_events(_export_stream(payload))


def _export_stream(payload: str) -> str:
    try:
        document = json.loads(payload)
    except json.JSONDecodeError:
        return ""
    if not isinstance(document, dict):
        return ""
    lines: list[str] = []
    for message in document.get("messages") or []:
        if not isinstance(message, dict):
            continue
        for part in message.get("parts") or []:
            if isinstance(part, dict):
                kind = part.get("type")
                if kind == "step-finish":
                    kind = "step_finish"
                evt: dict[str, Any] = {
                    "type": kind,
                    "sessionID": part.get("sessionID"),
                    "part": part,
                }
                lines.append(json.dumps(evt))
    return "\n".join(lines)


def wait_for_done(done_path: Path, timeout: float) -> bool:
    """Poll for done.json until the timeout expires."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if done_path.exists():
            return True
        time.sleep(POLL_INTERVAL_S)
    return False


def codex_home() -> Path:
    env = os.environ.get("CODEX_HOME")
    if env:
        return Path(env)
    return Path(os.path.expanduser("~")) / ".codex"


def _normalise_path(text: str | None) -> str | None:
    if not text:
        return None
    return os.path.normcase(Path(text).resolve() if Path(text).exists() else Path(text))


def find_codex_rollout(
    cwd: Path,
    started_utc: datetime.datetime,
    home: Path | None = None,
) -> tuple[Path, dict[str, Any]] | tuple[None, None]:
    """Newest codex rollout in ``~/.codex/sessions`` matching the run cwd.

    A rollout counts when its ``session_meta.cwd`` equals the run cwd and its
    meta timestamp is not earlier than the launch time; the newest by meta
    timestamp wins. Session files are project-scoped, hence the cwd match.
    """
    root = (home or codex_home()) / "sessions"
    if not root.is_dir():
        return None, None
    candidates: list[Path] = list(root.rglob("rollout-*.jsonl"))
    candidates = sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)[:CODEX_MAX_ROLLOUT_FILES]
    best: tuple[datetime.datetime, Path, dict[str, Any]] | None = None
    want_cwd = _normalise_path(str(cwd))
    for path in candidates:
        meta = _read_session_meta(path)
        if meta is None:
            continue
        meta_time = _meta_timestamp(meta.get("timestamp"))
        if meta_time is None or meta_time < started_utc:
            continue
        if _normalise_path(meta.get("cwd")) != want_cwd:
            continue
        if best is None or meta_time > best[0]:
            best = (meta_time, path, meta)
    if best is None:
        return None, None
    return best[1], best[2]


def _read_session_meta(path: Path) -> dict[str, Any] | None:
    try:
        with open(path, encoding="utf-8") as fh:
            line = fh.readline()
        meta = json.loads(line)
    except Exception:
        return None
    payload = meta.get("payload")
    return payload if isinstance(payload, dict) and meta.get("type") == "session_meta" else None


def _meta_timestamp(text: str | None) -> datetime.datetime | None:
    if not text:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed


def parse_codex_rollout(path: Path) -> dict[str, Any]:
    """Aggregate a codex rollout for tokens, warning-free error items, tools.

    tokens come from the last ``token_count`` event (info.total_token_usage,
    the session total including reasoning); json error items that merely
    warn (prefer_mxc preview notice, hook timeout clamp) are ignored.
    """
    tokens = {
        "total": 0,
        "input": 0,
        "output": 0,
        "reasoning": 0,
        "cache_read": 0,
        "cache_write": 0,
    }
    result: dict[str, Any] = {
        "session_id": None,
        "cost": 0.0,
        "tokens": tokens,
        "tool_calls": 0,
        "tool_errors": 0,
        "error_texts": [],
    }
    try:
        fh = open(path, encoding="utf-8")
    except OSError:
        return result
    with fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                evt = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(evt, dict):
                continue
            payload = evt.get("payload")
            if not isinstance(payload, dict):
                continue
            if evt.get("type") == "session_meta":
                result["session_id"] = payload.get("session_id") or payload.get("id")
                continue
            if evt.get("type") == "event_msg" and payload.get("type") == "token_count":
                usage = (payload.get("info") or {}).get("total_token_usage") or {}
                tokens["total"] = _int_or_default(usage.get("total_tokens"), tokens["total"])
                tokens["input"] = _int_or_default(usage.get("input_tokens"), tokens["input"])
                tokens["output"] = _int_or_default(usage.get("output_tokens"), tokens["output"])
                tokens["reasoning"] = _int_or_default(
                    usage.get("reasoning_output_tokens"), tokens["reasoning"]
                )
                tokens["cache_read"] = _int_or_default(
                    usage.get("cached_input_tokens"), tokens["cache_read"]
                )
                tokens["cache_write"] = _int_or_default(
                    usage.get("cache_write_input_tokens"), tokens["cache_write"]
                )
                continue
            if evt.get("type") != "response_item":
                continue
            kind = payload.get("type")
            if kind in ("function_call", "custom_tool_call", "local_shell_call"):
                result["tool_calls"] += 1
                continue
            if kind == "error":
                text = _codex_error_text(payload)
                lowered = (text or "").lower()
                if any(marker in lowered for marker in CODEX_WARNING_MARKERS):
                    continue
                result["error_texts"].append(text)
    return result


def _int_or_default(value: Any, default: int) -> int:
    return int(value) if isinstance(value, (int, float)) else default


def _codex_error_text(payload: dict[str, Any]) -> str:
    for key in ("message", "text", "error"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return json.dumps(payload)[:300]


def _detect_codex_failure(
    exit_code: int | None,
    last_text: str,
    error_texts: list[str],
    timed_out: bool,
) -> dict[str, Any] | None:
    if timed_out:
        return {"kind": "tool", "reason": "termination-unconfirmed"}
    if error_texts:
        text = error_texts[0]
        return {"kind": classify_error_text(text), "reason": text[:300]}
    if exit_code not in (None, 0):
        return {"kind": "exit", "reason": f"codex exec exited {exit_code}"}
    if not (last_text or "").strip():
        return {"kind": "tool", "reason": "empty last message (no last.txt)"}
    return None


def execute(
    cmd: list[str],
    cwd: Path,
    timeout: float,
) -> tuple[int | None, str, str, bool, bool]:
    """Run the opencode process.

    Returns (exit_code, stdout, stderr, timed_out, terminated).
    ``terminated`` is True when the process tree was confirmed dead after a
    timeout; it is always True for a normal exit.
    """
    try:
        popen_kwargs: dict[str, Any] = {
            "cwd": str(cwd),
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
        }
        if sys.platform != "win32":
            popen_kwargs["start_new_session"] = True
        proc = subprocess.Popen(cmd, **popen_kwargs)
    except OSError as exc:
        return 1, "", str(exc), False, True
    try:
        out, err = proc.communicate(timeout=timeout)
        return proc.returncode, out or "", err or "", False, True
    except subprocess.TimeoutExpired:
        terminated = _kill_process_tree(proc)
        out, err = "", ""
        if proc.stdout is not None or proc.stderr is not None:
            try:
                out, err = proc.communicate(timeout=PROCESS_TREE_DRAIN_TIMEOUT)
            except subprocess.TimeoutExpired:
                if proc.stdout is not None:
                    try:
                        proc.stdout.close()
                    except Exception:
                        pass
                if proc.stderr is not None:
                    try:
                        proc.stderr.close()
                    except Exception:
                        pass
        return proc.returncode, out or "", err or "", True, terminated


def _detect_failure(
    parsed: dict[str, Any],
    exit_code: int | None,
    timed_out: bool,
    terminated: bool,
) -> dict[str, Any] | None:
    for reason in parsed["error_reasons"]:
        return {"kind": classify_error_text(reason), "reason": reason[:300]}
    if parsed["permission_rejected"] is not None:
        return {
            "kind": "tool",
            "reason": f"auto-rejected permission: {parsed['permission_rejected'][:280]}",
        }
    if timed_out:
        if terminated:
            return {"kind": "tool", "reason": "timeout"}
        return {"kind": "tool", "reason": "termination-unconfirmed"}
    if exit_code not in (None, 0):
        return {"kind": "exit", "reason": f"opencode run exited {exit_code}"}
    if parsed["has_unresolved_tool_error"]:
        return {"kind": "tool", "reason": "unresolved tool error"}
    terminal = parsed["terminal_finish_reason"]
    if terminal in TERMINAL_FINISH_REASONS and parsed["session_id"] is not None:
        return None
    if terminal == "length":
        return {"kind": "capability", "reason": "incomplete event stream"}
    return {"kind": "tool", "reason": "incomplete event stream"}


def append_outcome(routing_log: Path, entry: dict[str, Any]) -> None:
    """Append one outcome line to .hx/routing.jsonl; never raise."""
    try:
        routing_log.parent.mkdir(parents=True, exist_ok=True)
        with open(routing_log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
    except Exception:
        pass


def _outcome_result(
    failure: dict[str, Any] | None,
    session_id: str | None,
    parsed: dict[str, Any] | None,
    latency: float,
    exit_code: int | None,
    timed_out: bool,
    terminated: bool,
    mode: str,
    run_dir: Path | None,
) -> dict[str, Any]:
    parsed = parsed or {
        "session_id": None,
        "cost": 0.0,
        "tokens": {
            "total": 0,
            "input": 0,
            "output": 0,
            "reasoning": 0,
            "cache_read": 0,
            "cache_write": 0,
        },
        "tool_calls": 0,
        "tool_errors": 0,
    }
    result: dict[str, Any] = {
        "ok": failure is None,
        "failure": failure,
        "session_id": session_id,
        "cost": round(parsed["cost"], 6),
        "tokens": parsed["tokens"],
        "tool_calls": parsed["tool_calls"],
        "tool_errors": parsed["tool_errors"],
        "latency_s": round(latency, 3),
        "mode": mode,
    }
    if exit_code is not None:
        result["exit_code"] = exit_code
    if run_dir is not None:
        result["run_dir"] = str(run_dir)
    if timed_out and not terminated:
        result["retry_safe"] = False
    return result


def _unique_run_dir(cwd: Path, title: str | None) -> Path:
    if cwd is None:
        cwd = _repo_root()
    else:
        cwd = Path(cwd).resolve()
    base = _repo_root(cwd)
    timestamp = _utcnow().strftime("%Y%m%d-%H%M%S")
    slug = title if title is not None else f"hx-{timestamp}"
    if title is not None and _sanitize_title(title) is None:
        raise ValueError(f"invalid title: {title}")
    candidate = base / ".hx" / "runs" / slug
    index = 1
    while candidate.exists() and index < 100:
        index += 1
        candidate = base / ".hx" / "runs" / f"{slug}-{index}"
    return candidate


def run_visible(args_kw: dict[str, Any]) -> dict[str, Any]:
    worker = args_kw.get("worker", "opencode")
    effort = args_kw.get("effort")
    prompt = args_kw["prompt"]
    model = args_kw["model"]
    variant = args_kw.get("variant")
    cwd: Path = Path(args_kw["cwd"]).resolve()
    timeout = args_kw.get("timeout", 1800.0)
    title = args_kw.get("title")
    task_id = args_kw.get("task_id")
    decision_id = args_kw.get("decision_id")

    started = time.monotonic()
    run_dir = _unique_run_dir(cwd, title)
    run_title = run_dir.name
    if worker == "codex":
        started_utc = _utcnow()
        files = write_codex_run_script(run_dir, prompt, model, effort, run_title, cwd)
    else:
        started_utc = None
        files = write_run_script(run_dir, prompt, model, variant, run_title, cwd)

    if not launch_visible(files["script"]):
        result = _outcome_result(
            {"kind": "tool", "reason": "failed to launch visible window"},
            None,
            None,
            time.monotonic() - started,
            None,
            False,
            True,
            "visible",
            run_dir,
        )
        result["worker"] = worker
        result["effort"] = effort
        _append_outcome_entry(
            result,
            run_dir.name,
            model,
            variant,
            cwd,
            task_id,
            decision_id,
            args_kw.get("routing_log"),
        )
        return result

    finished = wait_for_done(files["done"], timeout)
    latency = time.monotonic() - started
    exit_code: int | None = None
    if finished:
        try:
            done = json.loads(files["done"].read_text(encoding="utf-8-sig"))
            exit_code = int(done["exit_code"])
        except Exception:
            exit_code = None

    timed_out = not finished
    if worker == "codex":
        result = _finish_codex_visible(
            files,
            run_dir,
            cwd,
            started_utc,
            exit_code,
            timed_out,
            latency,
            worker,
        )
    else:
        result = _finish_opencode_visible(
            files, run_dir, cwd, exit_code, timed_out, latency, worker, run_title
        )
    result["worker"] = worker
    result["effort"] = effort
    _append_outcome_entry(
        result,
        run_title,
        model,
        variant,
        cwd,
        task_id,
        decision_id,
        args_kw.get("routing_log"),
    )
    return result


def _finish_codex_visible(
    files: dict[str, Path],
    run_dir: Path,
    cwd: Path,
    started_utc: datetime.datetime,
    exit_code: int | None,
    timed_out: bool,
    latency: float,
    worker: str,
) -> dict[str, Any]:
    rollout_path, meta = find_codex_rollout(cwd, started_utc)
    parsed = parse_codex_rollout(rollout_path) if rollout_path else None
    error_texts = (parsed or {}).get("error_texts") or []
    if timed_out:
        failure = {"kind": "tool", "reason": "termination-unconfirmed"}
    else:
        last_text = ""
        last_file = files.get("last")
        if last_file and last_file.exists():
            last_text = last_file.read_text(encoding="utf-8", errors="replace")
        if parsed is None:
            failure = {"kind": "tool", "reason": "no codex rollout matching the run cwd"}
        else:
            failure = _detect_codex_failure(exit_code, last_text, error_texts, False)
    parsed = parsed or _blank_parsed()
    result = _outcome_result(
        failure,
        (parsed or {}).get("session_id"),
        parsed,
        latency,
        exit_code,
        timed_out,
        not timed_out,
        "visible",
        run_dir,
    )
    result["worker"] = worker
    return result


def _finish_opencode_visible(    files: dict[str, Path],
    run_dir: Path,
    cwd: Path,
    exit_code: int | None,
    timed_out: bool,
    latency: float,
    worker: str,
    run_title: str,
) -> dict[str, Any]:
    session_id = find_session_by_title(run_title, cwd=cwd)
    if session_id is None:
        parsed = None
        if timed_out:
            failure = {"kind": "tool", "reason": "termination-unconfirmed"}
        else:
            failure = {"kind": "tool", "reason": f"no session named {run_title}"}
    else:
        payload, export_error = export_session_text(session_id)
        if payload:
            parsed = parse_export(payload)
        failure = _detect_failure(parsed or _blank_parsed(), exit_code, timed_out, not timed_out)
        if payload is None:
            failure = failure or {"kind": "tool", "reason": f"export failed: {export_error}"}
    result = _outcome_result(
        failure,
        session_id,
        parsed,
        latency,
        exit_code,
        timed_out,
        not timed_out,
        "visible",
        run_dir,
    )
    result["worker"] = worker
    return result


def _blank_parsed() -> dict[str, Any]:
    return parse_events("")


def _append_outcome_entry(
    result: dict[str, Any],
    title: str | None,
    model: str,
    variant: str | None,
    cwd: Path,
    task_id: str | None,
    decision_id: str | None,
    routing_log: Path | None = None,
) -> None:
    entry = {
        "kind": "outcome",
        "decision_id": decision_id,
        "task_id": task_id,
        "ts": _utcnow().isoformat(),
        "title": title,
        "model": model,
        "variant": variant,
        "cwd": str(cwd),
        "ok": result["ok"],
        "failure": result["failure"],
        "cost": result["cost"],
        "tokens": result["tokens"],
        "tool_calls": result["tool_calls"],
        "tool_errors": result["tool_errors"],
        "latency_s": result["latency_s"],
        "exit_code": result.get("exit_code"),
        "session_id": result["session_id"],
        "mode": result["mode"],
        "worker": result.get("worker", "opencode"),
        "effort": result.get("effort"),
    }
    append_outcome(routing_log or default_routing_log(cwd), entry)


def resolve_mode(headless: bool) -> str:
    """CLI-level mode choice: visible by default when HERDR_ENV=1."""
    if headless:
        return "headless"
    return "visible" if os.environ.get("HERDR_ENV") == "1" else "headless"


def run(
    prompt: str,
    model: str,
    variant: str | None = None,
    cwd: Path | None = None,
    timeout: float = 1800.0,
    title: str | None = None,
    routing_log: Path | None = None,
    task_id: str | None = None,
    decision_id: str | None = None,
    mode: str | None = None,
    worker: str = "opencode",
    effort: str | None = None,
) -> dict[str, Any]:
    """Run one OpenCode or Codex worker and return the run outcome."""
    if cwd is None:
        cwd = _repo_root()
    if mode == "visible":
        return run_visible(
            {
                "prompt": prompt,
                "model": model,
                "variant": variant,
                "cwd": cwd,
                "timeout": timeout,
                "title": title,
                "routing_log": routing_log,
                "task_id": task_id,
                "decision_id": decision_id,
                "worker": worker,
                "effort": effort,
            }
        )
    if worker != "opencode":
        raise ValueError(f"worker {worker} supports visible mode only")
    started = time.monotonic()
    cmd = build_command(prompt, model, variant, cwd, title)
    execute_result = execute(cmd, cwd, timeout)
    if len(execute_result) == 4:
        exit_code, out, _err, timed_out = execute_result
        terminated = True
    else:
        exit_code, out, _err, timed_out, terminated = execute_result
    latency = time.monotonic() - started

    parsed = parse_events(out)
    failure = _detect_failure(parsed, exit_code, timed_out, terminated)

    result: dict[str, Any] = {
        "ok": failure is None,
        "failure": failure,
        "session_id": parsed["session_id"],
        "cost": round(parsed["cost"], 6),
        "tokens": parsed["tokens"],
        "tool_calls": parsed["tool_calls"],
        "tool_errors": parsed["tool_errors"],
        "latency_s": round(latency, 3),
        "mode": "headless",
        "worker": "opencode",
    }

    if timed_out and not terminated:
        result["retry_safe"] = False

    entry = {
        "kind": "outcome",
        "decision_id": decision_id,
        "task_id": task_id,
        "ts": _utcnow().isoformat(),
        "title": title,
        "model": model,
        "variant": variant,
        "cwd": str(cwd),
        "ok": result["ok"],
        "failure": failure,
        "cost": result["cost"],
        "tokens": result["tokens"],
        "tool_calls": result["tool_calls"],
        "tool_errors": result["tool_errors"],
        "latency_s": result["latency_s"],
        "exit_code": exit_code,
        "session_id": result["session_id"],
    }
    append_outcome(routing_log or default_routing_log(cwd), entry)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m hx.ocrun",
        description="Headless OpenCode worker wrapper with silent-failure detection.",
    )
    parser.add_argument("prompt", nargs="?", default=None, help="task prompt for the worker")
    parser.add_argument(
        "--prompt-file",
        default=None,
        help="read the prompt from this file instead of the positional prompt",
    )
    parser.add_argument("--model", required=True, help="model name or provider/model id")
    parser.add_argument("--variant", default=None, help="opencode provider variant (reasoning effort)")
    parser.add_argument("--effort", default=None, help="codex reasoning effort (model_reasoning_effort override)")
    parser.add_argument(
        "--worker",
        default="opencode",
        choices=["opencode", "codex"],
        help="which worker runs the task (codex requires visible mode)",
    )
    parser.add_argument("--cwd", default=None, help="directory to run in")
    parser.add_argument("--timeout", type=float, default=1800.0)
    parser.add_argument("--title", default=None, help="title for the session")
    parser.add_argument("--task-id", default=None, help="correlation id for the task")
    parser.add_argument("--decision-id", default=None, help="correlation id from route.decide")
    parser.add_argument(
        "--headless",
        action="store_true",
        help="run in background --format json mode even when HERDR_ENV=1 (visible mode is the default there)",
    )
    args = parser.parse_args(argv)

    if args.prompt_file:
        prompt = Path(args.prompt_file).read_text(encoding="utf-8")
    elif args.prompt:
        prompt = args.prompt
    else:
        parser.error("one of prompt or --prompt-file is required")

    mode = resolve_mode(args.headless)
    if args.worker == "codex" and mode == "headless":
        parser.error("codex worker supports visible mode only (drop --headless)")

    result = run(
        prompt,
        args.model,
        variant=args.variant,
        cwd=Path(args.cwd) if args.cwd else None,
        timeout=args.timeout,
        title=args.title,
        task_id=args.task_id,
        decision_id=args.decision_id,
        mode=mode,
        worker=args.worker,
        effort=args.effort,
    )
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
