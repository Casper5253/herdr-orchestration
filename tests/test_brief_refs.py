"""One Jotarium item per brief, enforced at the hx say boundary."""
from __future__ import annotations

from pathlib import Path

import pytest

from hx import briefs


def _brief(directory: Path, name: str, line: str | None) -> Path:
    path = directory / ".hx" / "briefs" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text((line + "\n") if line else "# Brief\n", encoding="utf-8")
    return path


def test_a_unique_item_is_allowed(tmp_path: Path) -> None:
    _brief(tmp_path, "unique.md", "Jotarium: Q-379")
    briefs.validate_message(f"Read {tmp_path / '.hx' / 'briefs' / 'unique.md'}", tmp_path / ".hx" / "briefs")


def test_two_items_on_one_line_are_allowed(tmp_path: Path) -> None:
    _brief(tmp_path, "multi.md", "Jotarium: Q-379 and Q-436")
    briefs.validate_message(f"Read {tmp_path / '.hx' / 'briefs' / 'multi.md'}", tmp_path / ".hx" / "briefs")


def test_a_missing_item_names_the_fix(tmp_path: Path) -> None:
    path = _brief(tmp_path, "missing.md", None)
    with pytest.raises(briefs.BriefError, match="Create a child item for this round"):
        briefs.validate_message(f"Read {path}", path.parent)


def test_a_reused_item_names_the_other_brief(tmp_path: Path) -> None:
    _brief(tmp_path, "prior.md", "Jotarium: Q-437")
    path = _brief(tmp_path, "round2.md", "Jotarium: Q-437")
    with pytest.raises(briefs.BriefError, match="already used by prior.md"):
        briefs.validate_message(f"Read {path}", path.parent)


def test_a_missing_brief_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(briefs.BriefError, match="brief file not found"):
        briefs.validate_message(f"Read {tmp_path / '.hx' / 'briefs' / 'absent.md'}", tmp_path / ".hx" / "briefs")


def test_a_message_without_a_brief_pointer_is_ignored(tmp_path: Path) -> None:
    briefs.validate_message("Please review the changes", tmp_path / ".hx" / "briefs")


def test_pointer_detection_handles_windows_paths_and_duplicates(tmp_path: Path) -> None:
    path = _brief(tmp_path, "one.md", "Jotarium: Q-1")
    found = briefs.pointer_paths(f"{path} and {path}", path.parent)
    assert found == [path.resolve()]


def test_the_header_may_end_an_intro_line_and_examples_do_not_count(tmp_path: Path) -> None:
    path = tmp_path / ".hx" / "briefs" / "example.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "Load the skill. Jotarium: Q-379 and Q-436.\n\n"
        "The fix text mentions Jotarium: Q-123 and the old Q-437 case.\n",
        encoding="utf-8",
    )
    assert briefs.refs_for(path) == ["Q-379", "Q-436"]
