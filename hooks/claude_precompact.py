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


def _hx_env() -> dict[str, str]:
    env = dict(os.environ)
    repo_root = Path(__file__).resolve().parent.parent
    existing = env.get("PYTHONPATH", "")
    if str(repo_root) not in existing:
        env["PYTHONPATH"] = f"{repo_root}{os.pathsep}{existing}" if existing else str(repo_root)
    return env


def _hx_cmd() -> list[str]:
    return [sys.executable, "-m", "hx", "task", "list"]


def _active_tasks(cwd: Path) -> list[dict[str, Any]]:
    try:
        proc = subprocess.run(
            _hx_cmd(),
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=30,
            env=_hx_env(),
        )
        stdout = proc.stdout or ""
        tasks = json.loads(stdout)
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


def _load_next_action(task_id: str, repo_root: Path) -> str | None:
    try:
        from hx import checkpoint as cp
        from hx import ledger as ledger_mod

        led = ledger_mod.Ledger(repo_root=repo_root)
        result = cp.validate_checkpoint(task_id, ledger=led, repo_root=repo_root)
        if not result.get("ok"):
            return None
        path = Path(result["checkpoint"])
        text = path.read_text(encoding="utf-8", errors="replace")
        return _extract_next_action(text)
    except Exception:
        return None


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
    _resolve_cwd(payload)
    try:
        from hx import paths

        repo_root = paths.project_root()
    except Exception:
        repo_root = Path.cwd()

    active = _active_tasks(repo_root)

    print(INSTRUCTIONS, end="")
    print("## Active task checkpoints")

    next_lines: list[str] = []
    missing_ids: list[str] = []

    for task in active:
        task_id = task.get("id", "unknown")
        next_action = _load_next_action(task_id, repo_root)
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
