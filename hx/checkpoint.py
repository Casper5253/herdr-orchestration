"""hx.checkpoint: task checkpoint files with git-derived facts for owned files.

A checkpoint is a Markdown file in ``<repo>/.hx/checkpoints/<task-id>.md`` with
fixed sections plus a ``ledger_rev`` line. The script fills git-derived
sections; the agent supplies Decisions/Exact errors/Next action via flags or
stdin. Validation enforces completeness and consistency with the working tree
and the task's current revision.
"""

from __future__ import annotations

import datetime
import hashlib
import os
import re
import subprocess
import tempfile
from contextlib import contextmanager, nullcontext
from pathlib import Path
from typing import Any

from hx import ledger as ledger_mod
from hx import paths
from hx import reconcile
from hx import spawn


class CheckpointInvalid(Exception):
    """Raised when a checkpoint fails validation (task close is refused)."""


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

AGENT_SECTIONS = ["Decisions", "Exact errors", "Next action"]
# Appended by hx step; not a required section, so older checkpoints still validate.
STEPS_SECTION = "Steps"
STEP_DETAIL_LIMIT = 300
STEP_ID = re.compile(r" <!-- hx-step:([0-9a-f]{32}) -->$")
MARKDOWN_LINE = re.compile(r"^ {0,3}(?:##[ \t]+(?P<heading>[^\r\n]*)|(?P<fence>`{3,}|~{3,})(?P<tail>[^\r\n]*))\r?$", re.MULTILINE)

_LEDGER_REV_RE = re.compile(r"^ledger_rev:\s*(\d+)\s*$", re.MULTILINE)


def _repo_root() -> Path:
    return paths.project_root()


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
        proc = spawn.run(
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


def _git_committed(
    workdir: Path, base_commit: str, owned_files: set[str]
) -> list[str]:
    """Commit subjects in ``base_commit..HEAD`` touching owned files.

    ``git status --porcelain`` only sees working-tree dirt; work already
    committed during the task shows up here instead.
    """
    if not base_commit or not owned_files:
        return []
    rels = [os.path.relpath(f, workdir) for f in sorted(owned_files)]
    rc, out, _err = _run_git(
        ["log", "--oneline", "--no-decorate", f"{base_commit}..HEAD", "--", *rels],
        workdir,
    )
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


def _compose_checkpoint(
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
    committed: list[str],
    ledger_rev: int,
    steps: str = "",
) -> str:
    """Assemble the checkpoint markdown (no I/O)."""
    owner_text = owner or task.get("owner", "")
    status_text = status or task.get("status", "")
    lines = [
        f"# Checkpoint: {task['id']}",
        "",
        f"ledger_rev: {ledger_rev}",
        "",
        "## Objective",
        objective,
        "",
        "## Owner",
        owner_text,
        "",
        "## Status",
        status_text,
        "",
        "## Files changed",
        "",
        "### git status --porcelain",
        *git_status,
        "",
        "### committed (base..HEAD)",
        *committed,
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
    if steps:
        lines += [f"## {STEPS_SECTION}", "", steps, ""]
    return "\n".join(lines)


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
    committed: list[str],
    ledger_rev: int,
    steps: str = "",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = _compose_checkpoint(
        task,
        objective=objective,
        owner=owner,
        status=status,
        decisions=decisions,
        errors=errors,
        tests=tests,
        next_action=next_action,
        references=references,
        git_status=git_status,
        diff_stat=diff_stat,
        committed=committed,
        ledger_rev=ledger_rev,
        steps=steps,
    )
    _atomic_write_text(path, text)


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
    steps: str = "",
    repo_root: Path | None = None,
    checkpoints_dir: Path | None = None,
    _lock_held: bool = False,
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
    committed_lines = _git_committed(workdir, task.get("base_commit", ""), owned_files)

    owned_status = _filter_owned(status_lines, owned_files, workdir)
    owned_diff_stat = _filter_owned(diff_stat_lines, owned_files, workdir)
    owned_committed = [line for line in committed_lines]

    path = checkpoints_dir / f"{task_id}.md"
    with (nullcontext() if _lock_held else _checkpoint_lock(path)):
        # Explicit non-empty flags override the saved text; saved text is the
        # default for sections the caller left empty, so repeated writes preserve
        # earlier agent prose unless the agent rewrites it.
        saved = _read_authored_sections(path)
        merged = {
            "objective": objective or saved.get("Objective", ""),
            "owner": owner or "",
            "status": status or "",
            "decisions": decisions or saved.get("Decisions", ""),
            "errors": errors or saved.get("Exact errors", ""),
            "tests": tests or saved.get("Tests", ""),
            "next_action": next_action or saved.get("Next action", ""),
            "references": references or saved.get("References", ""),
            "steps": steps or saved.get(STEPS_SECTION, ""),
        }
        # Record the checkpoint path in the ledger first so the file mtime is
        # strictly newer than the task's updated timestamp when validated.
        task = ledger.update_task(task_id, checkpoint=str(path))
        _write_checkpoint(
            path,
            task,
            objective=merged["objective"],
            owner=merged["owner"],
            status=merged["status"],
            decisions=merged["decisions"],
            errors=merged["errors"],
            tests=merged["tests"],
            next_action=merged["next_action"],
            references=merged["references"],
            git_status=owned_status,
            diff_stat=owned_diff_stat,
            committed=owned_committed,
            ledger_rev=task["rev"],
            steps=merged["steps"],
        )
    return path


def write_completion_checkpoint(
    task_id: str,
    ledger: ledger_mod.Ledger | None = None,
    *,
    status: str = "done",
    objective: str = "",
    owner: str = "",
    status_note: str = "",
    decisions: str = "",
    errors: str = "",
    tests: str = "",
    next_action: str = "",
    references: str = "",
    repo_root: Path | None = None,
    checkpoints_dir: Path | None = None,
) -> Path:
    """Record the checkpoint for a closing transition, then commit the ledger.

    Used by ``hx task done``/``failed``. The agent-authored sections
    (Objective, Decisions, Exact errors, Tests, Next action, References) are
    kept from the existing checkpoint when the caller passes empty defaults,
    so re-running ``task done`` never overwrites authored work; git facts and
    the revision are always refreshed. Validation runs before the ledger is
    committed and a failed validation refuses the close by raising
    :class:`CheckpointInvalid`.

    The transition's revision is computed inside the ledger lock, the
    checkpoint is written with it, and the ledger is committed at that
    revision, so validation passes right after the close. A crash before the
    commit leaves the ledger intact and the checkpoint validates as stale.
    """
    if ledger is None:
        ledger = ledger_mod.default_ledger()
    if repo_root is None:
        repo_root = ledger.repo_root
    if checkpoints_dir is None:
        checkpoints_dir = ledger.repo_root / ".hx" / "checkpoints"

    def writer(task: dict[str, Any], rev: int) -> Path:
        path = checkpoints_dir / f"{task_id}.md"
        authored = _read_authored_sections(path)
        merged = {
            "objective": authored.get("Objective", "") or objective,
            "owner": authored.get("Owner", "") or owner,
            "status": authored.get("Status", "") or status_note or status,
            "decisions": authored.get("Decisions", "") or decisions,
            "errors": authored.get("Exact errors", "") or errors,
            "tests": authored.get("Tests", "") or tests,
            "next_action": authored.get("Next action", "") or next_action,
            "references": authored.get("References", "") or references,
            "steps": authored.get(STEPS_SECTION, ""),
        }
        workdir = _task_workdir(task, repo_root)
        owned_files = {_canonical_path(workdir / f) for f in task.get("files", [])}
        base = task.get("base_commit", "")
        owned_status = _filter_owned(
            _git_status_porcelain(workdir), owned_files, workdir
        )
        owned_diff_stat = _filter_owned(
            _git_diff_stat(workdir, base), owned_files, workdir
        )
        owned_committed = _git_committed(workdir, base, owned_files)

        text = _compose_checkpoint(
            task,
            objective=merged["objective"],
            owner=merged["owner"],
            status=merged["status"],
            decisions=merged["decisions"],
            errors=merged["errors"],
            tests=merged["tests"],
            next_action=merged["next_action"],
            references=merged["references"],
            git_status=owned_status,
            diff_stat=owned_diff_stat,
            committed=owned_committed,
            ledger_rev=rev,
            steps=merged["steps"],
        )

        # Validate BEFORE the close is committed; the writer runs inside the
        # ledger lock, so raising here aborts complete_task without saving.
        validation_errors = _validate_content(text, task, rev, workdir)
        if validation_errors:
            _atomic_write_text(path, text)
            raise CheckpointInvalid(
                f"checkpoint validation failed: {'; '.join(validation_errors)}"
            )

        _write_checkpoint(
            path,
            task,
            objective=merged["objective"],
            owner=merged["owner"],
            status=merged["status"],
            decisions=merged["decisions"],
            errors=merged["errors"],
            tests=merged["tests"],
            next_action=merged["next_action"],
            references=merged["references"],
            git_status=owned_status,
            diff_stat=owned_diff_stat,
            committed=owned_committed,
            ledger_rev=rev,
            steps=merged["steps"],
        )
        return path

    path = checkpoints_dir / f"{task_id}.md"
    with _checkpoint_lock(path):
        ledger.complete_task(task_id, status, writer)
    return path


def _section_span(text: str, section: str) -> tuple[int, int] | None:
    start = None
    fence = ""
    for match in MARKDOWN_LINE.finditer(text):
        marker = match["fence"]
        if marker:
            if not fence:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence) and not match["tail"].strip():
                fence = ""
        elif not fence:
            if start is not None:
                return start, match.start()
            heading = re.sub(r"[ \t]+#+[ \t]*$", "", match["heading"]).strip()
            if heading == section:
                start = match.end() + (text[match.end():match.end() + 1] == "\n")
    return (start, len(text)) if start is not None else None


def _extract_section(text: str, section: str) -> str | None:
    span = _section_span(text, section)
    return text[span[0]:span[1]].strip() if span else None


def _read_authored_sections(path: Path) -> dict[str, str]:
    """Parse an existing (authored) checkpoint's sections for preservation."""
    sections: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return sections
    for section in SECTIONS:
        content = _extract_section(text, section)
        if content is not None:
            sections[section] = content
    steps = _extract_section(text, STEPS_SECTION)
    if steps is not None:
        sections[STEPS_SECTION] = steps
    return sections


def _validate_content(
    text: str,
    task: dict[str, Any],
    rev: int,
    workdir: Path,
) -> list[str]:
    """Validate assembled checkpoint markdown against the task state.

    Contains the file-content checks shared by the pre-close validation (which
    refuses to commit an invalid close) and disk validation after the fact.
    """
    errors: list[str] = []
    for section in SECTIONS:
        content = _extract_section(text, section)
        if content is None:
            errors.append(f"Missing section: {section}")
        elif section in AGENT_SECTIONS and not content:
            errors.append(f"{section} section is empty")

    rev_match = _LEDGER_REV_RE.search(text)
    if rev_match is None:
        errors.append("Checkpoint does not record ledger_rev")
    else:
        recorded_rev = int(rev_match.group(1))
        if recorded_rev != rev:
            errors.append(
                f"Checkpoint ledger_rev {recorded_rev} does not match "
                f"the task's current revision {rev}"
            )

    owned_files = {_canonical_path(workdir / f) for f in task.get("files", [])}
    owned_status = _filter_owned(
        _git_status_porcelain(workdir), owned_files, workdir
    )
    owned_committed = _git_committed(workdir, task.get("base_commit", ""), owned_files)

    file_section = _extract_section(text, "Files changed")
    listed_porcelain: list[str] = []
    listed_committed: list[str] = []
    if file_section is not None:
        subsection: str | None = None
        for line in file_section.splitlines():
            stripped = line.strip()
            if stripped.startswith("### "):
                if "git status --porcelain" in stripped:
                    subsection = "porcelain"
                    continue
                if "committed" in stripped:
                    subsection = "committed"
                    continue
                if "git diff --stat" in stripped:
                    subsection = "stat"
                    continue
            if not stripped:
                continue
            if subsection == "porcelain":
                listed_porcelain.append(line)
            elif subsection == "committed":
                listed_committed.append(line)
    if listed_porcelain != owned_status:
        errors.append("Files changed section does not match current git status for owned files")
    if listed_committed != owned_committed:
        errors.append("Files changed section does not match committed changes for owned files")
    return errors


def validate_checkpoint(
    task_id: str,
    ledger: ledger_mod.Ledger | None = None,
    *,
    repo_root: Path | None = None,
    checkpoints_dir: Path | None = None,
    expected_task: dict[str, Any] | None = None,
    expected_rev: int | None = None,
) -> dict[str, Any]:
    if ledger is None:
        ledger = ledger_mod.default_ledger()
    if repo_root is None:
        repo_root = ledger.repo_root
    if checkpoints_dir is None:
        checkpoints_dir = ledger.repo_root / ".hx" / "checkpoints"

    # During the closing transition the task dict is already mutated in memory
    # but not committed; callers pass the in-memory state explicitly.
    task = expected_task if expected_task is not None else ledger.get_task(task_id)
    rev = expected_rev if expected_rev is not None else task.get("rev", 0)
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
    workdir = _task_workdir(task, repo_root)
    errors.extend(_validate_content(text, task, rev, workdir))

    # Detect owned files that were modified after the checkpoint was written.
    worktree_owned = {_canonical_path(workdir / f) for f in task.get("files", [])}
    for owned_path in [Path(f) for f in worktree_owned]:
        if owned_path.exists() and owned_path.stat().st_mtime > path.stat().st_mtime:
            errors.append(
                f"git status shows owned file changed after checkpoint: {owned_path.name}"
            )

    return {"ok": not errors, "errors": errors, "checkpoint": str(path), "mtime": mtime.isoformat()}


def format_step(when: datetime.datetime, agent: str, kind: str, detail: str,
                exit_status: int | None = None) -> str:
    label = " ".join(str(agent or "manual").split()) or "manual"
    text = " ".join(str(detail or "").split())
    if len(text) > STEP_DETAIL_LIMIT:
        text = text[: STEP_DETAIL_LIMIT - 3] + "..."
    stamp = when.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    tail = f" exit={exit_status}" if exit_status is not None else ""
    return f"- {stamp} {label} {kind}{tail} {text}"


def _with_steps(text: str, lines: list[str], span: tuple[int, int] | None) -> str:
    first_break = re.search(r"\r?\n", text)
    eol = first_break[0] if first_break else "\n"
    body = eol.join(lines)
    if span is None:
        gap = eol if text.endswith(eol) else eol * 2
        return text + gap + f"## {STEPS_SECTION}{eol}{eol}{body}{eol}"
    start, end = span
    section = text[start:end]
    first, last = re.search(r"\S", section), re.search(r"\s*$", section)
    if first:
        return text[:start + first.start()] + body + text[start + last.start():]
    heading_gap = eol if text[:start].endswith("\n") else eol * 2
    return text[:start] + heading_gap + body + eol + text[start:]


@contextmanager
def _checkpoint_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    # Keep the lock inode valid for other processes already waiting on it.
    with path.with_suffix(".lock").open("a+b") as lock:
        lock.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        yield


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False,
        mode="w", encoding="utf-8", newline="",
    ) as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(handle.name, path)


def _merge_steps(existing: list[str], wanted: list[str]) -> tuple[list[str], int]:
    positions = {}
    for index, line in enumerate(existing):
        identity = STEP_ID.search(line)
        positions[identity[1] if identity else line] = index
    added = 0
    for line in wanted:
        identity = STEP_ID.search(line)
        key = identity[1] if identity else line
        index = positions.get(key)
        if index is None:
            positions[key] = len(existing)
            existing.append(line)
            added += 1
        elif identity and re.match(r"^- \S+ \S+ (?:command|test) exit=-?\d+ ", line):
            existing[index] = line
    return existing, added


def append_steps(
    task_id: str,
    lines: list[str],
    *,
    checkpoints_dir: Path | None = None,
    ledger: ledger_mod.Ledger | None = None,
) -> tuple[Path, int]:
    """Append step lines to a task checkpoint, creating it from the ledger if needed.

    A line already in the Steps section is skipped, so running the same harvest
    twice appends nothing.
    """
    directory = checkpoints_dir or default_checkpoints_dir()
    path = directory / f"{task_id}.md"
    wanted, _ = _merge_steps([], [line for line in lines if line.strip()])
    with _checkpoint_lock(path):
        if path.exists():
            text = path.read_bytes().decode("utf-8")
            span = _section_span(text, STEPS_SECTION)
            existing = text[span[0]:span[1]].strip().splitlines() if span else []
            merged, added = _merge_steps(existing.copy(), wanted)
            if merged != existing:
                _atomic_write_text(path, _with_steps(text, merged, span))
            return path, added
        if ledger is None:
            ledger = ledger_mod.default_ledger()
        try:
            ledger.get_task(task_id)
        except ledger_mod.TaskNotFound as exc:
            raise ValueError(
                f"no checkpoint and no ledger task {task_id}; record steps under a task"
            ) from exc
        path = write_checkpoint(task_id, ledger=ledger,
                                steps="\n".join(wanted),
                                checkpoints_dir=directory, _lock_held=True)
        return path, len(wanted)


def _parse_since(value: str) -> datetime.datetime:
    try:
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"--since must be an ISO timestamp: {exc}") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=datetime.timezone.utc)


def harvest(
    task_id: str,
    agent: str,
    *,
    brief: str | None = None,
    since: str | None = None,
    roots: dict[str, Path] | None = None,
    hx_dir: Path | None = None,
    checkpoints_dir: Path | None = None,
    ledger: ledger_mod.Ledger | None = None,
    now: datetime.datetime | None = None,
) -> dict[str, Any]:
    """Read the agent's steps since its brief and append them to the task checkpoint."""
    records = reconcile.load_records(hx_dir or paths.hx_dir())
    family = reconcile.role_family(agent)
    if family is None:
        raise ValueError(
            f"unknown agent family for {agent or 'no agent'}: name a worker such as dev-opencode"
        )
    record: dict[str, Any] | None = None
    if brief:
        record = next((item for item in records if str(item.get("id")) == brief), None)
        if record is None:
            raise ValueError(f"no send record {brief} in {hx_dir or paths.hx_dir()}")
        if not reconcile.same_agent(str(record.get("target") or ""), agent):
            raise ValueError(f"send record {brief} went to {record.get('target')}, not {agent}")
        if not reconcile.names_task(str(record.get("text") or ""), task_id):
            raise ValueError(f"send record {brief} does not name task {task_id}")
        start = record["_sent"]
    elif since:
        start = _parse_since(since)
    else:
        record = reconcile.brief_for_task(records, task_id, agent)
        if record is None:
            raise ValueError(
                f"no send to {agent} names {task_id}; pass --brief <id> or --since <iso>"
            )
        start = record["_sent"]
    until = now or datetime.datetime.now(datetime.timezone.utc)
    # Allow clock skew between the sender and worker when finding a receipt.
    logs = reconcile.load_logs(roots or reconcile.default_roots(), start, until)
    if record is not None:
        match = reconcile.brief_receipt(record, family, logs)
        if match is None:
            raise ValueError(
                f"no readable {family} transcript received the brief for {task_id} sent "
                f"{start.strftime('%Y-%m-%dT%H:%MZ')}; record steps by hand with "
                f"hx step add {task_id} --note ..."
            )
        log, receipt = match
    else:
        covering = [log for log in logs if log.family == family
                    and log.first is not None and log.last is not None
                    and log.first <= until and log.last >= start]
        if not covering:
            raise ValueError(
                f"no readable {family} transcript covers {start.strftime('%Y-%m-%dT%H:%MZ')}"
                f"..{until.strftime('%Y-%m-%dT%H:%MZ')}; record steps by hand with "
                f"hx step add {task_id} --note ..."
            )
        if len(covering) != 1:
            raise ValueError(f"ambiguous {family} worker session for --since {since}")
        log = covering[0]
        receipt = next((entry for entry in log.entries
                        if entry.at is not None and start <= entry.at <= until
                        and reconcile.TASK_BRIEF.match(entry.text)
                        and reconcile.names_task(entry.text, task_id)),
                       reconcile.Entry(start, "", -1))
    found, until = reconcile.steps_in_window(log, receipt, until)
    start = receipt.at
    lines = [format_step(step.at, agent, step.kind, step.detail, step.exit) for step in found]
    persisted = []
    for step, line in zip(found, lines):
        identity = "\\0".join((family, log.session_id, step.invocation))
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
        persisted.append(f"{line} <!-- hx-step:{digest} -->")
    path, added = append_steps(task_id, persisted, checkpoints_dir=checkpoints_dir, ledger=ledger)
    return {
        "task": task_id,
        "agent": agent,
        "family": family,
        "brief": str(record.get("id")) if record else None,
        "brief_at": record["_sent"].isoformat() if record else None,
        "since": start.isoformat(),
        "until": until.isoformat(),
        "session": log.path,
        "steps": lines,
        "total": len(lines),
        "added": added,
        "newest": lines[-1] if lines else None,
        "checkpoint": str(path),
    }


def render_harvest(result: dict[str, Any]) -> str:
    source = (f"brief {result['brief']}" if result.get("brief")
              else f"since {result['since']}")
    lines = [f"hx step harvest {result['task']} {result['agent']}: {source}, "
             f"{result['total']} steps, {result['added']} new",
             f"  window {result['since']} .. {result['until']}"]
    if result.get("newest"):
        lines.append(f"  newest {result['newest']}")
    lines.append(f"checkpoint {result['checkpoint']}")
    return "\n".join(lines)
