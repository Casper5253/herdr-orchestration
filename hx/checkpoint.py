"""hx.checkpoint: task checkpoint files with git-derived facts for owned files.

A checkpoint is a Markdown file in ``<repo>/.hx/checkpoints/<task-id>.md`` with
fixed sections. The script fills git-derived sections; the agent supplies the
rest via flags or stdin. Validation enforces completeness and consistency with
the working tree.
"""

from __future__ import annotations

import datetime
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from hx import ledger as ledger_mod


SECTIONS = [
    "Objective",
    "Owner",
    "Status",
    "Files changed",
    "Decisions",
    "Exact errors",
    "Tests",
    "Next action",
    "References",
]


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _env_path(var: str, default: Path) -> Path:
    value = os.environ.get(var)
    return Path(value) if value else default


def default_checkpoints_dir() -> Path:
    return _env_path("HX_CHECKPOINTS_DIR", _repo_root() / ".hx" / "checkpoints")


def _canonical_path(path: str | Path) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(str(path))))


def _task_workdir(task: dict[str, Any], repo_root: Path) -> Path:
    if task.get("worktree"):
        return Path(task["worktree"])
    return repo_root


def _run_git(args: list[str], cwd: Path) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=30,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except Exception as exc:
        return 1, "", str(exc)


def _git_status_porcelain(workdir: Path) -> list[str]:
    rc, out, _err = _run_git(["status", "--porcelain"], workdir)
    if rc != 0:
        return []
    return [line for line in out.splitlines() if line.strip()]


def _git_diff_stat(workdir: Path, base_commit: str) -> list[str]:
    if not base_commit:
        return []
    rc, out, _err = _run_git(["diff", "--stat", base_commit], workdir)
    if rc != 0:
        return []
    return [line for line in out.splitlines() if line.strip()]


def _filter_owned(
    lines: list[str],
    owned_files: set[str],
    workdir: Path,
) -> list[str]:
    """Keep git status/diff lines whose path is owned by this task."""
    owned: list[str] = []
    for line in lines:
        # status --porcelain: XY path or XY path -> orig
        if len(line) < 3:
            continue
        # First two characters are the status codes; the path starts after them.
        path_part = line[3:].strip()
        if not path_part:
            continue
        # Handle rename arrow.
        if " -> " in path_part:
            path_part = path_part.split(" -> ")[-1]
        full = _canonical_path(workdir / path_part)
        if full in owned_files:
            owned.append(line)
    return owned


def _parse_diff_files(stat_lines: list[str]) -> list[str]:
    """Extract filenames from `git diff --stat` output."""
    files: list[str] = []
    for line in stat_lines:
        # Each line looks like:  src/foo.py |  3 +--
        match = re.match(r"^(.+?)\s+\|\s+\d+", line)
        if match:
            files.append(match.group(1).strip())
    return files


def _write_checkpoint(
    path: Path,
    task: dict[str, Any],
    objective: str,
    owner: str,
    status: str,
    decisions: str,
    errors: str,
    tests: str,
    next_action: str,
    references: str,
    git_status: list[str],
    diff_stat: list[str],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# Checkpoint: {task['id']}",
        "",
        "## Objective",
        objective,
        "",
        "## Owner",
        owner or task.get("owner", ""),
        "",
        "## Status",
        status or task.get("status", ""),
        "",
        "## Files changed",
        "",
        "### git status --porcelain",
        *git_status,
        "",
        "### git diff --stat",
        *diff_stat,
        "",
        "## Decisions",
        decisions,
        "",
        "## Exact errors",
        errors,
        "",
        "## Tests",
        tests,
        "",
        "## Next action",
        next_action,
        "",
        "## References",
        references,
        "",
    ]
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def write_checkpoint(
    task_id: str,
    ledger: ledger_mod.Ledger | None = None,
    *,
    objective: str = "",
    owner: str = "",
    status: str = "",
    decisions: str = "",
    errors: str = "",
    tests: str = "",
    next_action: str = "",
    references: str = "",
    repo_root: Path | None = None,
    checkpoints_dir: Path | None = None,
) -> Path:
    if ledger is None:
        ledger = ledger_mod.default_ledger()
    if repo_root is None:
        repo_root = ledger.repo_root
    if checkpoints_dir is None:
        checkpoints_dir = ledger.repo_root / ".hx" / "checkpoints"

    task = ledger.get_task(task_id)
    workdir = _task_workdir(task, repo_root)
    owned_files = {_canonical_path(workdir / f) for f in task.get("files", [])}

    status_lines = _git_status_porcelain(workdir)
    diff_stat_lines = _git_diff_stat(workdir, task.get("base_commit", ""))

    owned_status = _filter_owned(status_lines, owned_files, workdir)
    owned_diff_stat = _filter_owned(diff_stat_lines, owned_files, workdir)

    path = checkpoints_dir / f"{task_id}.md"
    # Record the checkpoint path in the ledger first so the file mtime is
    # strictly newer than the task's updated timestamp when validated.
    ledger.update_task(task_id, checkpoint=str(path))
    _write_checkpoint(
        path,
        task,
        objective=objective,
        owner=owner,
        status=status,
        decisions=decisions,
        errors=errors,
        tests=tests,
        next_action=next_action,
        references=references,
        git_status=owned_status,
        diff_stat=owned_diff_stat,
    )
    return path


def _extract_section(text: str, section: str) -> str | None:
    pattern = rf"## {re.escape(section)}\n(.*?)\n(?:## |\Z)"
    match = re.search(pattern, text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return None


def validate_checkpoint(
    task_id: str,
    ledger: ledger_mod.Ledger | None = None,
    *,
    repo_root: Path | None = None,
    checkpoints_dir: Path | None = None,
) -> dict[str, Any]:
    if ledger is None:
        ledger = ledger_mod.default_ledger()
    if repo_root is None:
        repo_root = ledger.repo_root
    if checkpoints_dir is None:
        checkpoints_dir = ledger.repo_root / ".hx" / "checkpoints"

    task = ledger.get_task(task_id)
    path = checkpoints_dir / f"{task_id}.md"
    errors: list[str] = []

    if not path.exists():
        return {"ok": False, "errors": [f"Checkpoint file not found: {path}"]}

    mtime = datetime.datetime.fromtimestamp(path.stat().st_mtime, tz=datetime.timezone.utc)
    task_updated = task.get("updated")
    if task_updated:
        try:
            updated_dt = datetime.datetime.fromisoformat(task_updated)
            if mtime <= updated_dt:
                errors.append("Checkpoint is not newer than the last ledger update")
        except ValueError:
            pass

    text = path.read_text(encoding="utf-8")
    for section in SECTIONS:
        content = _extract_section(text, section)
        if content is None:
            errors.append(f"Missing section: {section}")
        elif section == "Next action" and not content:
            errors.append("Next action section is empty")

    # Validate that the owned file list matches git status for owned files.
    workdir = _task_workdir(task, repo_root)
    owned_files = {_canonical_path(workdir / f) for f in task.get("files", [])}
    owned_paths = [Path(f) for f in owned_files]
    status_lines = _git_status_porcelain(workdir)
    owned_status = _filter_owned(status_lines, owned_files, workdir)
    file_section = _extract_section(text, "Files changed")
    if file_section is not None:
        listed_status = [
            line for line in file_section.splitlines()
            if line.strip().startswith((" ", "\t")) or (len(line) >= 2 and line[:2] in {" M", " A", " D", "??", " R", " C"})
        ]
        # Simpler: collect lines that look like git status entries under the porcelain subheading.
        in_porcelain = False
        listed: list[str] = []
        for line in file_section.splitlines():
            if "git status --porcelain" in line:
                in_porcelain = True
                continue
            if "git diff --stat" in line:
                break
            if in_porcelain and line.strip():
                listed.append(line)
        if listed != owned_status:
            errors.append("Files changed section does not match current git status for owned files")

    # Detect owned files that were modified after the checkpoint was written.
    for owned_path in owned_paths:
        if owned_path.exists() and owned_path.stat().st_mtime > path.stat().st_mtime:
            errors.append(
                f"git status shows owned file changed after checkpoint: {owned_path.name}"
            )

    return {"ok": not errors, "errors": errors, "checkpoint": str(path), "mtime": mtime.isoformat()}
