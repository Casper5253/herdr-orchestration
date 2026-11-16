"""Brief-file ownership rules for hx say."""
from __future__ import annotations

import re
from pathlib import Path

from hx import paths

_BRIEF_MARKER = re.compile(
    r"(?i)[\\/]?\.hx[\\/]briefs[\\/][A-Za-z0-9._-]+\.md(?![A-Za-z0-9._-])"
)
_DELIMITERS = frozenset(" \t\r\n`'\"<>()[]{}")
_JOTARIUM_LINE = re.compile(r"(?im)Jotarium\s*:\s*(?P<line>[^\r\n]+)")
_REF = re.compile(r"\b([PFQ]-\d+)\b", re.IGNORECASE)


class BriefError(ValueError):
    """A brief pointer that cannot satisfy the one-item-per-brief rule."""


def briefs_dir() -> Path:
    return paths.hx_dir() / "briefs"


def _resolved_pointer(raw: str, directory: Path) -> Path:
    candidate = Path(raw)
    if candidate.is_absolute():
        candidates = [candidate]
    else:
        candidates = [directory.parent.parent / candidate, directory / candidate.name]
    for candidate in candidates:
        candidate = candidate.resolve()
        if candidate.parent.name != "briefs" or candidate.parent.parent.name != ".hx":
            continue
        if candidate.is_file():
            return candidate
    if any((candidate.parent.name == "briefs"
            and candidate.parent.parent.name == ".hx") for candidate in candidates):
        raise BriefError(f"brief file not found: {raw}")
    raise BriefError(f"{raw} is not a .hx/briefs file")


def _pointer_token(message: str, start: int, end: int) -> str:
    left = start
    while left > 0 and message[left - 1] not in _DELIMITERS:
        left -= 1
    return message[left:end].strip().rstrip(".,;:!?)]}")


def pointer_paths(message: str, directory: Path | None = None) -> list[Path]:
    """Every .hx/briefs/*.md pointer in a message, in first-seen order."""
    base = (directory or briefs_dir()).resolve()
    found: list[Path] = []
    for match in _BRIEF_MARKER.finditer(message):
        raw = _pointer_token(message, match.start(), match.end())
        path = _resolved_pointer(raw, base)
        if path not in found:
            found.append(path)
    return found


def refs_for(path: Path) -> list[str]:
    """Jotarium refs named by Jotarium lines in one brief."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise BriefError(f"cannot read brief {path}: {exc}") from exc
    match = _JOTARIUM_LINE.search(text)
    if match is None:
        return []
    line = re.split(r"\.(?:\s|$)", match.group("line").strip(), maxsplit=1)[0]
    return [ref.upper() for ref in _REF.findall(line)]


def _other_refs(directory: Path, current: Path) -> dict[str, list[str]]:
    used: dict[str, list[str]] = {}
    for path in sorted(directory.glob("*.md")):
        if path.resolve() == current:
            continue
        for ref in refs_for(path):
            used.setdefault(ref, []).append(path.name)
    return used


def _child_step(name: str, ref: str | None) -> str:
    """The exact next step after a brief fails the one-item rule."""
    subject = ref if ref else "the intended parent item"
    return (
        f"Create a child item for this round: if {subject} is a top-level item, create the child under {subject}. "
        f"A subtask cannot hold children (Jotarium "
        f"returns invalidParent), so when {subject} is a subtask the child must go "
        f"under that subtask's parent. Then add Jotarium: Q-<new> to {name}."
    )


def validate_message(message: str, directory: Path | None = None) -> None:
    """Refuse a brief pointer without a unique, unused Jotarium item."""
    pointers = pointer_paths(message, directory)
    errors: list[str] = []
    for path in pointers:
        refs = refs_for(path)
        if not refs:
            errors.append(
                f"Brief {path} names no Jotarium item. "
                + _child_step(path.name, None)
            )
            continue
        used = _other_refs(path.parent, path)
        for ref in sorted(set(refs)):
            if ref in used:
                others = ", ".join(sorted(set(used[ref])))
                errors.append(
                    f"Brief {path} reuses Jotarium {ref}, already used by {others}. "
                    + _child_step(path.name, ref)
                )
    if errors:
        raise BriefError(
            " ".join(errors)
            + " Use --force only for a control message."
        )
