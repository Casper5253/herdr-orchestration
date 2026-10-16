"""Claude PreCompact hook: extra compact instructions for the lead.

Claude Code appends this hook's stdout to the compact instructions. We ask the
compacted context to keep the durable facts (task ids, exact errors, decisions,
next actions) and inject the current active tasks from the hx ledger.

Sets nothing; reads the hook payload on stdin (ignored except for cwd).
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


INSTRUCTIONS = """\
# Compaction guardrails (amounts to keep, set by hooks/claude_precompact.py)
Keep verbatim through compaction:
- task ids from the hx ledger and which files each task owns,
- exact error text (verbatim strings and exit codes; do not paraphrase),
- decisions made and why, quoting the rejected alternatives,
- next actions as concrete imperative steps (file, command, expected result).
"""


def main() -> int:
    payload = _read_payload()
    cwd_value = payload.get("cwd") or os.getcwd()
    cwd = Path(str(cwd_value))

    print(INSTRUCTIONS, end="")
    print("## Active tasks (hx ledger)")
    try:
        print(_active_tasks(cwd))
    except Exception as exc:
        print(f"(hx task list unavailable: {exc})")
    return 0


def _entry() -> int:
    try:
        return main()
    except BaseException as exc:
        try:
            print(f"precompact hook failed: {exc}")
        except Exception:
            pass
        return 0


if __name__ == "__main__":
    sys.exit(_entry())
