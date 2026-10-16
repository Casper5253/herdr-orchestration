"""Claude SessionStart(compact) hook: restore context after compaction.

Resolves the project root from the hook payload, lists active tasks from the
ledger, and injects only valid per-task checkpoints. Tasks without a valid
checkpoint are reported as missing/stale. The Next action is always printed
first and never truncated; other sections are trimmed to keep total output
around 6000 characters.

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

# Hooks run as scripts from hooks/, so make the hx package next to them importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ACTIVE_STATUSES = {"assigned", "in_progress", "review"}
MAX_OUTPUT_CHARS = 6000


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


def _load_checkpoint(
    task_id: str, repo_root: Path
) -> tuple[str | None, str | None, Path | None]:
    """Return (next_action, full_text, path) for a valid checkpoint, or Nones."""
    try:
        from hx import checkpoint as cp
        from hx import ledger as ledger_mod

        led = ledger_mod.Ledger(repo_root=repo_root)
        result = cp.validate_checkpoint(task_id, ledger=led, repo_root=repo_root)
        if not result.get("ok"):
            return None, None, None
        path = Path(result["checkpoint"])
        text = path.read_text(encoding="utf-8", errors="replace")
        next_action = _extract_next_action(text)
        if next_action is None:
            return None, None, None
        return next_action, text, path
    except Exception:
        return None, None, None


def _trim_text(text: str, limit: int) -> str:
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    trimmed = text[:limit]
    last_nl = trimmed.rfind("\n")
    if last_nl > limit * 0.7:
        return trimmed[:last_nl].rstrip()
    for marker in (". ", "! ", "? "):
        idx = trimmed.rfind(marker)
        if idx > limit * 0.7:
            return trimmed[: idx + 1].rstrip()
    return trimmed.rstrip()


def main() -> int:
    payload = _read_payload()
    _resolve_cwd(payload)
    try:
        from hx import paths

        repo_root = paths.project_root()
    except Exception:
        repo_root = Path.cwd()

    active = _active_tasks(repo_root)

    header = "# Post-compact restore (hooks/claude_session_compact.py)\n"
    next_lines: list[str] = []
    missing_lines: list[str] = []
    bodies: list[tuple[str, str, Path]] = []

    for task in active:
        task_id = task.get("id", "unknown")
        next_action, text, path = _load_checkpoint(task_id, repo_root)
        if next_action is None:
            missing_lines.append(f"checkpoint missing or stale for {task_id}")
            continue
        next_lines.append(f"Next action for {task_id}: {next_action}")
        body = _NEXT_RE.sub("", text).strip()
        bodies.append((task_id, body, path))

    fixed = header + "".join(line + "\n" for line in next_lines) + "".join(
        line + "\n" for line in missing_lines
    )
    budget = MAX_OUTPUT_CHARS - len(fixed)
    per_task_budget = budget // len(bodies) if bodies else 0

    body_blocks: list[str] = []
    if per_task_budget > 0:
        for task_id, body, path in bodies:
            trimmed = _trim_text(body, per_task_budget)
            block = f"## Rest of checkpoint for {task_id}\n{trimmed}"
            if len(body) > per_task_budget:
                block += f"\n... (trimmed; full checkpoint: {path})"
            body_blocks.append(block)

    output = fixed + "\n".join(body_blocks)
    print(output)
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
