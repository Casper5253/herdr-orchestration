"""Pytest configuration: keep temp dirs inside the repo to avoid Windows
permission issues with the system temp root."""

from pathlib import Path


def pytest_configure(config):
    if not config.option.basetemp:
        base = Path(__file__).resolve().parent.parent / ".hx" / "pytest-temp"
        base.mkdir(parents=True, exist_ok=True)
        config.option.basetemp = base
