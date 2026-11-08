"""hx.handoff: the portable handoff artifact C10 names (Q-203).

A fresh owner gets a new session and loses its predecessor's state; today the
lead re-briefs by hand. ``hx handoff write <agent> --task <id>`` turns what hx
already records -- the authored checkpoint, the ledger task, and the newest
run outcome -- into one short portable document at
``.hx/handoffs/<task>-<n>.md``: objective, decisions so far, the exact
current error, files touched, next action, constraints. ``hx handoff brief
<file>`` prints the one-line pointer a fresh session opens with.

Per Matt Pocock's handoff semantics (research/greats/matt-pocock.md): the
artifact gives portability, not compression; it references source artifacts
by path instead of duplicating them; secret-looking lines are redacted; and
a hard length cap keeps it a briefing, not a transcript.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hx import checkpoint as checkpoint_mod
from hx import ledger as ledger_mod
from hx import paths

MAX_BYTES = 4096
MAX_SECTION_LINES = 8

_SECRET_RE = re.compile(
    r"(?i)\b(token|secret|api[_-]?key|password|credential)\b[^\n]*[:=]\s*\S+"
)


def _repo_root() -> Path:
    return paths.project_root()


def _redact(text: str) -> str:
    return _SECRET_RE.sub(lambda m: m.group(0)[: m.group(0).find(m.group(1))] + m.group(1) + ": [redacted]", text)


def _truncate(text: str) -> str:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) > MAX_SECTION_LINES:
        lines = lines[:MAX_SECTION_LINES] + [f"... [{len(lines) - MAX_SECTION_LINES} more lines in source]"]
    return "\n".join(lines)


def _section(body: list[str], title: str, text: str | None) -> None:
    if text:
        body.append(f"## {title}")
        body.append(_truncate(_redact(text.strip())))
        body.append("")


def _last_outcome(task_id: str, routing_log: Path) -> dict[str, Any] | None:
    found = None
    try:
        lines = routing_log.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        try:
            record = json.loads(line)
        except (TypeError, ValueError):
            continue
        if record.get("kind") == "outcome" and record.get("task_id") == task_id:
            found = record
    return found


def _task_from_ledger(task_id: str, root: Path) -> dict[str, Any] | None:
    try:
        ledger = ledger_mod.Ledger(repo_root=root)
        for task in ledger.list_tasks():
            if task.get("id") == task_id:
                return task
    except Exception:
        pass
    return None


def _cap_bytes(text: str) -> str:
    data = text.encode("utf-8")
    if len(data) <= MAX_BYTES:
        return text
    return data[: MAX_BYTES - 40].decode("utf-8", errors="ignore") + "\n\n[truncated to fit cap]\n"


def write_handoff(
    agent: str,
    task_id: str,
    root: Path | None = None,
    checkpoints_dir: Path | None = None,
    routing_log: Path | None = None,
) -> Path:
    """Compose .hx/handoffs/<task>-<n>.md from the checkpoint, ledger and runs."""
    root = root or _repo_root()
    checkpoints_dir = checkpoints_dir or checkpoint_mod.default_checkpoints_dir()
    routing_log = routing_log or root / ".hx" / "routing.jsonl"

    checkpoint_path = checkpoints_dir / f"{task_id}.md"
    sections = checkpoint_mod._read_authored_sections(checkpoint_path)
    try:
        raw = checkpoint_path.read_text(encoding="utf-8")
    except OSError:
        raw = ""
    extra_constraints = checkpoint_mod._extract_section(raw, "Constraints")
    task = _task_from_ledger(task_id, root) or {}
    outcome = _last_outcome(task_id, routing_log)

    body: list[str] = [
        f"# Handoff: {task_id}",
        (
            f"task: {task_id} | previous owner: {agent} | "
            f"written: {datetime.now(timezone.utc).isoformat(timespec='seconds')}"
        ),
        "",
    ]
    _section(body, "Objective", sections.get("Objective") or task.get("title"))
    _section(body, "Decisions so far", sections.get("Decisions"))
    error = sections.get("Exact errors") or ""
    if outcome and (outcome.get("failure") or {}).get("reason"):
        reason = outcome["failure"]["reason"]
        error = f"{error}\nlatest run outcome: {reason}".strip()
    _section(body, "Current error", error)
    files = sections.get("Files changed")
    if not files and task.get("files"):
        files = "\n".join(f"- {f}" for f in task["files"])
    _section(body, "Files touched", files)
    _section(body, "Next action", sections.get("Next action"))
    constraints = extra_constraints or ""
    workdir = task.get("workdir") or str(root)
    constraints = f"- worktree: {workdir}\n{constraints}".strip()
    _section(body, "Constraints", constraints)
    refs = sections.get("References") or ""
    refs = f"{refs}\n- checkpoint: {checkpoints_dir / (task_id + '.md')}\n- routing log: {routing_log}".strip()
    _section(body, "References", refs)
    body.append(
        "Suggested skills: poteto-mode, herdr-team; re-read the checkpoint for full detail.\n"
    )

    handoffs_dir = root / ".hx" / "handoffs"
    handoffs_dir.mkdir(parents=True, exist_ok=True)
    seq = len(list(handoffs_dir.glob(f"{task_id}-*.md"))) + 1
    path = handoffs_dir / f"{task_id}-{seq}.md"
    path.write_text(_cap_bytes("\n".join(body)), encoding="utf-8")
    return path


def brief(path: Path) -> str:
    """The one-line pointer message a fresh session opens with."""
    text = ""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        pass
    nxt = checkpoint_mod._extract_section(text, "Next action") or "the next action it names"
    first = nxt.splitlines()[0].strip() if nxt.strip() else "the next action it names"
    return f"Continue from handoff {path}: read it first, then {first.rstrip('.')}."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hx handoff", description="Portable handoff for a fresh owner.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    write_p = sub.add_parser("write", help="write .hx/handoffs/<task>-<n>.md for a task")
    write_p.add_argument("agent")
    write_p.add_argument("--task", required=True)
    brief_p = sub.add_parser("brief", help="print the pointer line for the fresh session")
    brief_p.add_argument("file")
    args = parser.parse_args(argv)
    if args.cmd == "write":
        path = write_handoff(args.agent, args.task)
        print(path)
        return 0
    print(brief(Path(args.file)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
