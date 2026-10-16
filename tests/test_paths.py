"""Tests for hx.paths root resolution."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from hx import paths


def test_package_root_holds_policy_json() -> None:
    assert (paths.package_root() / "policy.json").exists()


def test_project_root_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HX_PROJECT_ROOT", str(tmp_path))
    assert paths.project_root() == tmp_path.resolve()


def test_hx_dir_follows_project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HX_PROJECT_ROOT", str(tmp_path))
    assert paths.hx_dir() == tmp_path.resolve() / ".hx"


def test_project_root_git_toplevel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HX_PROJECT_ROOT", raising=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)
    monkeypatch.chdir(str(repo))
    assert paths.project_root().resolve() == repo.resolve()


def test_project_root_falls_back_to_cwd(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HX_PROJECT_ROOT", raising=False)

    def boom(*args: object, **kwargs: object) -> bytes:
        raise OSError("no git")

    monkeypatch.setattr(paths.subprocess, "check_output", boom)
    assert paths.project_root() == Path.cwd()