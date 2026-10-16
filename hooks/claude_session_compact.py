"""Claude SessionStart(compact) hook: restore context after compaction.

Prints the active hx tasks and the newest checkpoint under ``<cwd>/.hx/checkpoints``.
Claude Code injects stdout into the resumed session when the SessionStart hook
matches source ``compact``.

Hooks must never fail the session: every error is caught and exit code is 0.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

OPEN_STATUSES = {"open", "assigned", "in_progress", "review"}

MAX_CHECKPOINT_CHARS = 6000


def _read_payload() -> dict[str, Any]:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    return payload if isinstance(payload, dict) else {}


def _hx_cmd() -> list[str]:
    return [sys.executable, "-m", "hx", "task", "list"]


def _hx_env() -> dict[str, str]:
    env = dict(os.environ)
    repo_root = Path(__file__).resolve().parent.parent
    existing = env.get("PYTHONPATH", "")
    if str(repo_root) not in existing:
        env["PYTHONPATH"] = f"{repo_root}{os.pathsep}{existing}" if existing else str(repo_root)
    return env


def _active_tasks(cwd: Path) -> str:
    proc = subprocess.run(
        _hx_cmd(),
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=30,
        env=_hx_env(),
    )
    stdout = proc.stdout or ""
    try:
        tasks = json.loads(stdout)
    except Exception:
        return stdout.strip() or "(hx task list returned no output)"
    if isinstance(tasks, list):
        active = [t for t in tasks if isinstance(t, dict) and t.get("status") in OPEN_STATUSES]
        return json.dumps(active, indent=2)
    return stdout.strip()


def _newest_checkpoint(cwd: Path) -> Path | None:
    checkpoints = cwd / ".hx" / "checkpoints"
    if not checkpoints.is_dir():
        return None
    candidates = sorted(checkpoints.glob("*.md"), key=lambda p: p.stat().st_mtime)
    return candidates[-1] if candidates else None


def _checkpoint_text(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return f"(checkpoint unreadable: {exc})"
    if len(text) <= MAX_CHECKPOINT_CHARS:
        return text
    return (
        text[:MAX_CHECKPOINT_CHARS]
        + f"\n... (truncated; full checkpoint: {path})"
    )


def main() -> int:
    payload = _read_payload()
    cwd_value = payload.get("cwd") or os.getcwd()
    cwd = Path(str(cwd_value))

    print("# Post-compact restore (hooks/claude_session_compact.py)")
    print("## Active tasks (hx ledger)")
    try:
        print(_active_tasks(cwd))
    except Exception as exc:
        print(f"(hx task list unavailable: {exc})")

    checkpoint = _newest_checkpoint(cwd)
    if checkpoint is None:
        print("## Newest checkpoint")
        print("(no checkpoint found under .hx/checkpoints)")
    else:
        print(f"## Newest checkpoint: {checkpoint.name}")
        print(_checkpoint_text(checkpoint))
    return 0


def _entry() -> int:
    try:
        return main()
    except BaseException as exc:
        try:
            print(f"session-compact hook failed: {exc}")
        except Exception:
            pass
        return 0


if __name__ == "__main__":
    sys.exit(_entry())
