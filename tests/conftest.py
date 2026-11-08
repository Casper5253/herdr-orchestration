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
def _hermetic_env(monkeypatch: pytest.MonkeyPatch, tmp_path):
    # Tests must never inherit the agent session's HERDR_ENV=1, which flips
    # ocrun's default launch mode to visible (real console windows). A test
    # that needs the visible default sets the variable explicitly.
    monkeypatch.delenv("HERDR_ENV", raising=False)
    # Likewise the session's pane id and prefix would make team_prefix() query
    # the live herdr server instead of the policy under test.
    monkeypatch.delenv("HERDR_PANE_ID", raising=False)
    monkeypatch.delenv("HX_HERDR_PREFIX", raising=False)
    monkeypatch.setenv("HX_OPENCODE_AUTH", str(tmp_path / "missing-opencode-auth.json"))
    monkeypatch.setenv("HX_CONTEXT_RESETS_PATH", str(tmp_path / "context-resets.json"))
