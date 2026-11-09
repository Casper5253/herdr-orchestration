"""Only hx/host.py may name the Herdr CLI.

The host boundary exists so the Herdr app's `hc` core can replace the old
`herdr` CLI without touching callers. A subprocess argument, a `herdr agent`
command or a `herdr pane` command anywhere else means the boundary eroded.
"""

from __future__ import annotations

from pathlib import Path

import pytest

HX = Path(__file__).resolve().parents[1] / "hx"
HOST = HX / "host.py"

FORBIDDEN = ('"herdr"', "'herdr'", "herdr agent", "herdr pane")


def _sources() -> list[Path]:
    return sorted(path for path in HX.glob("*.py") if path != HOST)


@pytest.mark.parametrize("needle", FORBIDDEN)
def test_only_the_host_module_names_the_herdr_cli(needle: str) -> None:
    offenders = [
        path.name for path in _sources() if needle in path.read_text(encoding="utf-8")
    ]
    assert offenders == [], f"{needle!r} appears outside hx/host.py: {offenders}"


def test_host_module_holds_the_herdr_cli() -> None:
    source = HOST.read_text(encoding="utf-8")
    assert '"herdr"' in source
    assert "herdr agent" in source
