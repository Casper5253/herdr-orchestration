"""Pytest configuration: keep temp dirs inside the repo to avoid Windows
permission issues with the system temp root."""

import os
from pathlib import Path

import pytest


PYTEST_TEMP_ROOT = Path(__file__).resolve().parent.parent / ".hx" / "pytest-temp"


def pytest_configure(config):
    # One basetemp per run: pytest wipes basetemp at start, and a shared one let a
    # concurrent run delete another run's temp git repos mid-test (9 Nov).
    if not config.option.basetemp:
        base = PYTEST_TEMP_ROOT / f"run-{os.getpid()}"
        base.mkdir(parents=True, exist_ok=True)
        config.option.basetemp = base
    # git may never walk up from a temp dir into this repo. Without this, a test
    # whose temp repo vanished ran `git config` and `git commit` on the real repo.
    os.environ["GIT_CEILING_DIRECTORIES"] = str(PYTEST_TEMP_ROOT)


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch: pytest.MonkeyPatch, tmp_path):
    # Tests must never inherit the agent session's HERDR_ENV=1, which flips
    # ocrun's default launch mode to visible (real console windows). A test
    # that needs the visible default sets the variable explicitly.
    monkeypatch.delenv("HERDR_ENV", raising=False)
    # Tests must never inherit an hc terminal either: host.detect() would use
    # HcHost and hide the old herdr path under test.
    monkeypatch.delenv("HC_TERMINAL_ID", raising=False)
    # Likewise the session's pane id and prefix would make team_prefix() query
    # the live herdr server instead of the policy under test.
    monkeypatch.delenv("HERDR_PANE_ID", raising=False)
    monkeypatch.delenv("HX_HERDR_PREFIX", raising=False)
    # The live terminal scope is identity evidence for own(); tests set their own.
    monkeypatch.delenv("HC_SCOPE", raising=False)
    monkeypatch.delenv("HC_TERMINAL_SOCKET", raising=False)
    monkeypatch.setenv("HX_OPENCODE_AUTH", str(tmp_path / "missing-opencode-auth.json"))
    monkeypatch.setenv("HX_CONTEXT_RESETS_PATH", str(tmp_path / "context-resets.json"))
    monkeypatch.setenv("HX_LEASES_PATH", str(tmp_path / "leases.json"))
    monkeypatch.setenv("HX_LEASES_LOCK_PATH", str(tmp_path / "leases.lock"))
