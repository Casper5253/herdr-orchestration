"""Claude PreCompact hook: extra compact instructions for the lead.

Prints compact guardrails plus the active task ids and their Next actions
from valid checkpoints. Tasks without a valid checkpoint are listed so the
lead knows the hook cannot recover them. The hook does not save authored
state.

Hooks must never fail the session: every error is caught and exit code is 0.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ACTIVE_STATUSES = {"assigned", "in_progress", "review"}


def _read_payload() -> dict[str, Any]:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    return payload if isinstance(payload, dict) else {}


def _resolve_cwd(payload: dict[str, Any]) -> Path:
    cwd_value = payload.get("cwd") or os.getcwd()
    cwd = Path(str(cwd_value)).resolve()
    try:
        os.chdir(str(cwd))
    except Exception:
        pass
    return cwd


def _hx_executable() -> str:
    return "hx.cmd" if os.name == "nt" else "hx"


def _hx_cmd(*args: str) -> list[str]:
    return [_hx_executable(), *args]


def _run_hx(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        _hx_cmd(*args),
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=30,
    )


def _active_tasks(cwd: Path) -> list[dict[str, Any]]:
    try:
        proc = _run_hx(["task", "list"], cwd)
        tasks = json.loads(proc.stdout or "")
        if isinstance(tasks, list):
            return [
                t for t in tasks
                if isinstance(t, dict) and t.get("status") in ACTIVE_STATUSES
            ]
    except Exception:
        pass
    return []


_NEXT_RE = re.compile(r"(?:^|\n)## Next action\s*\n(.*?)(?=\n## |\Z)", re.DOTALL)


def _extract_next_action(text: str) -> str | None:
    match = _NEXT_RE.search(text)
    if match:
        content = match.group(1).strip()
        return content if content else None
    return None


def _validated_checkpoint(task_id: str, cwd: Path) -> Path | None:
    try:
        proc = _run_hx(["checkpoint", "validate", task_id], cwd)
        result = json.loads(proc.stdout or "")
        if not isinstance(result, dict) or not result.get("ok"):
            return None
        path = Path(str(result.get("checkpoint") or ""))
        return path if path.is_file() else None
    except Exception:
        return None


def _load_next_action(task_id: str, cwd: Path) -> str | None:
    path = _validated_checkpoint(task_id, cwd)
    if path is None:
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return _extract_next_action(text)


INSTRUCTIONS = """\
# Compaction guardrails (set by hooks/claude_precompact.py)
Preserve through compaction:
- task ids from the hx ledger and which files each task owns,
- exact error text (verbatim strings and exit codes; do not paraphrase),
- decisions made and why, quoting the rejected alternatives,
- next actions as concrete imperative steps (file, command, expected result).

This hook lists active task ids and their current next actions from valid
checkpoints; it does not author or restore state.
"""


def main() -> int:
    payload = _read_payload()
    cwd = _resolve_cwd(payload)
    active = _active_tasks(cwd)

    print(INSTRUCTIONS, end="")
    print("## Active task checkpoints")

    next_lines: list[str] = []
    missing_ids: list[str] = []

    for task in active:
        task_id = task.get("id", "unknown")
        next_action = _load_next_action(task_id, cwd)
        if next_action is None:
            missing_ids.append(task_id)
        else:
            next_lines.append(f"Next action for {task_id}: {next_action}")

    if next_lines:
        print("\n".join(next_lines))
    if missing_ids:
        print(f"Tasks without a valid checkpoint: {', '.join(missing_ids)}")
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
