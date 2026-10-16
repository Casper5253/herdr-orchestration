"""Tests for hx.checkpoint."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from hx import __main__ as cli
from hx import checkpoint, ledger as ledger_mod


def _git_init(repo: Path) -> None:
    subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(repo), check=True, capture_output=True)


def _git_commit_all(repo: Path, message: str) -> None:
    subprocess.run(["git", "add", "-A"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", message], cwd=str(repo), check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    _git_init(tmp_path)
    (tmp_path / "owned.py").write_text("original\n", encoding="utf-8")
    (tmp_path / "other.py").write_text("original\n", encoding="utf-8")
    _git_commit_all(tmp_path, "initial")
    monkeypatch.setenv("HX_LEDGER_PATH", str(tmp_path / ".hx" / "ledger.json"))
    monkeypatch.setenv("HX_LEASES_PATH", str(tmp_path / ".herdr-team" / "state" / "leases.json"))
    monkeypatch.setenv("HX_LOCK_PATH", str(tmp_path / ".hx" / "ledger.lock"))
    monkeypatch.setenv("HX_CHECKPOINTS_DIR", str(tmp_path / ".hx" / "checkpoints"))
    return tmp_path


@pytest.fixture
def ledger(repo: Path) -> ledger_mod.Ledger:
    led = ledger_mod.Ledger(repo_root=repo)
    led.add_task("t1", title="Task 1", owner="hx-opencode", files=["owned.py"])
    return led


def test_checkpoint_write_and_validate(repo: Path, ledger: ledger_mod.Ledger) -> None:
    (repo / "owned.py").write_text("modified\n", encoding="utf-8")
    path = checkpoint.write_checkpoint(
        "t1",
        ledger=ledger,
        objective="Do the thing",
        next_action="Open owned.py",
    )
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "## Objective" in text
    assert "## Next action" in text
    assert "owned.py" in text
    assert "other.py" not in text

    result = checkpoint.validate_checkpoint("t1", ledger=ledger)
    assert result["ok"] is True


def test_validate_missing_next_action(repo: Path, ledger: ledger_mod.Ledger) -> None:
    checkpoint.write_checkpoint("t1", ledger=ledger, objective="Do")
    result = checkpoint.validate_checkpoint("t1", ledger=ledger)
    assert result["ok"] is False
    assert any("Next action" in e for e in result["errors"])


def test_validate_git_mismatch(repo: Path, ledger: ledger_mod.Ledger) -> None:
    (repo / "owned.py").write_text("v1\n", encoding="utf-8")
    checkpoint.write_checkpoint("t1", ledger=ledger, next_action="Continue")
    result = checkpoint.validate_checkpoint("t1", ledger=ledger)
    assert result["ok"] is True

    # Modify the owned file after the checkpoint was written.
    (repo / "owned.py").write_text("v2\n", encoding="utf-8")
    result = checkpoint.validate_checkpoint("t1", ledger=ledger)
    assert result["ok"] is False
    assert any("git status" in e for e in result["errors"])


def test_task_done_orders_checkpoint(repo: Path) -> None:
    led = ledger_mod.Ledger(repo_root=repo)
    led.add_task("t1", title="Task 1", owner="hx-opencode", files=["owned.py"])
    (repo / "owned.py").write_text("done\n", encoding="utf-8")

    code = cli.main([
        "task", "done", "t1",
        "--objective", "Do the thing",
        "--next", "Ship it",
    ])
    assert code == 0

    task = led.get_task("t1")
    assert task["status"] == "done"
    assert task["checkpoint"] is not None
    cp_path = Path(task["checkpoint"])
    assert cp_path.exists()
    text = cp_path.read_text(encoding="utf-8")
    assert "Ship it" in text


def test_checkpoint_only_owned_files(repo: Path, ledger: ledger_mod.Ledger) -> None:
    (repo / "owned.py").write_text("changed\n", encoding="utf-8")
    (repo / "other.py").write_text("changed\n", encoding="utf-8")
    path = checkpoint.write_checkpoint("t1", ledger=ledger, next_action="Next")
    text = path.read_text(encoding="utf-8")
    # owned.py is owned by t1; other.py is not.
    assert "owned.py" in text
    assert "other.py" not in text
