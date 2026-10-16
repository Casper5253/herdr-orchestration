"""Pytest configuration: keep temp dirs inside the repo to avoid Windows
permission issues with the system temp root."""

import os
from pathlib import Path

import pytest


def pytest_configure(config):
    if not config.option.basetemp:
        base = Path(__file__).resolve().parent.parent / ".hx" / "pytest-temp"
        base.mkdir(parents=True, exist_ok=True)
        config.option.basetemp = base


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch: pytest.MonkeyPatch):
    # Tests must never inherit the agent session's HERDR_ENV=1, which flips
    # ocrun's default launch mode to visible (real console windows). A test
    # that needs the visible default sets the variable explicitly.
    monkeypatch.delenv("HERDR_ENV", raising=False)
