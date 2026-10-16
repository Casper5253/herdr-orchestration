"""Tests for hx.checkpoint."""

from __future__ import annotations

import json
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
    monkeypatch.setenv("HX_LEASES_LOCK_PATH", str(tmp_path / ".herdr-team" / "state" / "leases.lock"))
    monkeypatch.setenv("HX_CHECKPOINTS_DIR", str(tmp_path / ".hx" / "checkpoints"))
    monkeypatch.setenv("HX_PROJECT_ROOT", str(tmp_path))
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
        decisions="Chose the simple approach",
        errors="none",
        next_action="Open owned.py",
    )
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "## Objective" in text
    assert "## Next action" in text
    assert f"ledger_rev: {ledger.get_task('t1')['rev']}" in text
    assert "owned.py" in text
    assert "other.py" not in text

    result = checkpoint.validate_checkpoint("t1", ledger=ledger)
    assert result["ok"] is True, result["errors"]


def test_validate_missing_next_action(repo: Path, ledger: ledger_mod.Ledger) -> None:
    checkpoint.write_checkpoint("t1", ledger=ledger, objective="Do")
    result = checkpoint.validate_checkpoint("t1", ledger=ledger)
    assert result["ok"] is False
    assert any("Next action" in e for e in result["errors"])


def test_validate_empty_agent_sections_fail(repo: Path, ledger: ledger_mod.Ledger) -> None:
    checkpoint.write_checkpoint(
        "t1", ledger=ledger, next_action="Next", decisions="   "
    )
    result = checkpoint.validate_checkpoint("t1", ledger=ledger)
    assert result["ok"] is False
    assert any("Decisions" in e for e in result["errors"])

    checkpoint.write_checkpoint("t1", ledger=ledger, next_action="Next")
    result = checkpoint.validate_checkpoint("t1", ledger=ledger)
    assert result["ok"] is False
    assert any("Exact errors" in e for e in result["errors"])


def test_validate_git_mismatch(repo: Path, ledger: ledger_mod.Ledger) -> None:
    (repo / "owned.py").write_text("v1\n", encoding="utf-8")
    checkpoint.write_checkpoint(
        "t1", ledger=ledger, decisions="D", errors="none", next_action="Continue"
    )
    result = checkpoint.validate_checkpoint("t1", ledger=ledger)
    assert result["ok"] is True

    # Modify the owned file after the checkpoint was written.
    (repo / "owned.py").write_text("v2\n", encoding="utf-8")
    result = checkpoint.validate_checkpoint("t1", ledger=ledger)
    assert result["ok"] is False
    assert any("git status" in e for e in result["errors"])


def test_validate_rev_mismatch_fails(repo: Path, ledger: ledger_mod.Ledger) -> None:
    checkpoint.write_checkpoint(
        "t1", ledger=ledger, decisions="D", errors="none", next_action="Continue"
    )
    assert checkpoint.validate_checkpoint("t1", ledger=ledger)["ok"] is True

    ledger.set_status("t1", "in_progress")
    result = checkpoint.validate_checkpoint("t1", ledger=ledger)
    assert result["ok"] is False
    assert any("ledger_rev" in e for e in result["errors"])


def test_task_done_orders_checkpoint(repo: Path) -> None:
    led = ledger_mod.Ledger(repo_root=repo)
    led.add_task("t1", title="Task 1", owner="hx-opencode", files=["owned.py"])
    (repo / "owned.py").write_text("done\n", encoding="utf-8")

    code = cli.main([
        "task", "done", "t1",
        "--objective", "Do the thing",
        "--next", "Ship it",
        "--decisions", "Chose the simple approach",
        "--errors", "none",
    ])
    assert code == 0

    task = led.get_task("t1")
    assert task["status"] == "done"
    assert task["checkpoint"] is not None
    cp_path = Path(task["checkpoint"])
    assert cp_path.exists()
    text = cp_path.read_text(encoding="utf-8")
    assert "Ship it" in text


def test_validate_passes_right_after_done(repo: Path) -> None:
    led = ledger_mod.Ledger(repo_root=repo)
    led.add_task("t1", title="Task 1", owner="hx-opencode", files=["owned.py"])
    (repo / "owned.py").write_text("done\n", encoding="utf-8")

    checkpoint.write_completion_checkpoint(
        "t1",
        ledger=led,
        status="done",
        objective="Do the thing",
        decisions="Chose the simple approach",
        errors="none",
        next_action="Ship it",
    )
    result = checkpoint.validate_checkpoint("t1", ledger=led)
    assert result["ok"] is True, result["errors"]
    task = led.get_task("t1")
    assert task["status"] == "done"
    assert task["rev"] == 2


def test_crash_before_commit_leaves_stale_checkpoint(repo: Path) -> None:
    led = ledger_mod.Ledger(repo_root=repo)
    led.add_task("t1", title="Task 1", owner="hx-opencode", files=["owned.py"])
    (repo / "owned.py").write_text("done\n", encoding="utf-8")
    before = led.get_task("t1")

    def crash(_ledger: dict, _leases: dict) -> None:
        raise RuntimeError("simulated crash before the ledger commit")

    led._save = crash
    try:
        with pytest.raises(RuntimeError):
            checkpoint.write_completion_checkpoint(
                "t1",
                ledger=led,
                status="done",
                objective="Do the thing",
                decisions="D",
                errors="none",
                next_action="Ship it",
            )
    finally:
        del led._save

    led2 = ledger_mod.Ledger(repo_root=repo)
    task = led2.get_task("t1")
    assert task["status"] == before["status"]
    assert task["rev"] == before["rev"]

    result = checkpoint.validate_checkpoint("t1", ledger=led2)
    assert result["ok"] is False
    assert any("ledger_rev" in e for e in result["errors"])


def test_checkpoint_only_owned_files(repo: Path, ledger: ledger_mod.Ledger) -> None:
    (repo / "owned.py").write_text("changed\n", encoding="utf-8")
    (repo / "other.py").write_text("changed\n", encoding="utf-8")
    path = checkpoint.write_checkpoint("t1", ledger=ledger, next_action="Next")
    text = path.read_text(encoding="utf-8")
    # owned.py is owned by t1; other.py is not.
    assert "owned.py" in text
    assert "other.py" not in text


# ---------- correction round 1: authored preservation and close refusal -------


def test_task_done_keeps_authored_checkpoint(
    repo: Path, ledger: ledger_mod.Ledger, capsys: "pytest.CaptureFixture[str]"
) -> None:
    (repo / "owned.py").write_text("modified\n", encoding="utf-8")
    assert cli.main([
        "checkpoint", "write", "t1",
        "--objective", "Authored objective",
        "--decisions", "Authored decision",
        "--errors", "Authored error",
        "--tests", "authored test",
        "--next", "Authored next step",
        "--references", "authored ref",
    ]) == 0
    capsys.readouterr()

    assert cli.main(["task", "done", "t1"]) == 0
    done_out = capsys.readouterr().out
    task = json_recover(done_out, "t1")
    text = Path(task["checkpoint"]).read_text(encoding="utf-8")

    assert "Authored objective" in text
    assert "Authored decision" in text
    assert "Authored error" in text
    assert "authored test" in text
    assert "Authored next step" in text
    assert "authored ref" in text
    assert f"ledger_rev: {task['rev']}" in text

    capsys.readouterr()
    result = checkpoint.validate_checkpoint("t1", ledger=ledger)
    assert result["ok"] is True, result["errors"]


def test_task_done_authored_content_wins_over_flag_defaults(
    repo: Path, ledger: ledger_mod.Ledger, capsys: "pytest.CaptureFixture[str]"
) -> None:
    (repo / "owned.py").write_text("modified\n", encoding="utf-8")
    checkpoint.write_checkpoint(
        "t1",
        ledger=ledger,
        decisions="KEEP THIS",
        errors="KEEP ERR",
        next_action="KEEP NEXT",
    )
    assert cli.main([
        "task", "done", "t1",
        "--decisions", "flag decisions",
        "--errors", "flag errors",
        "--next", "flag next",
    ]) == 0
    capsys.readouterr()
    result = checkpoint.validate_checkpoint("t1", ledger=ledger)
    text = Path(result["checkpoint"]).read_text(encoding="utf-8")
    assert "KEEP THIS" in text
    assert "KEEP ERR" in text
    assert "KEEP NEXT" in text


def test_task_done_refuses_when_validation_fails(
    repo: Path, ledger: ledger_mod.Ledger
) -> None:
    (repo / "owned.py").write_text("done-ish\n", encoding="utf-8")
    before = ledger.get_task("t1")

    code = cli.main([
        "task", "done", "t1",
        "--objective", "o",
        "--errors", "none",
        "--next", "n",
    ])
    assert code != 0

    after = ledger.get_task("t1")
    assert after["status"] == before["status"]
    assert after["rev"] == before["rev"]

    # Empty Decisions means the close is invalid; the writer's trace file
    # exists but the ledger refuses to close.
    result = checkpoint.validate_checkpoint("t1", ledger=ledger)
    assert result["ok"] is False

    (repo / "owned.py").write_text("done-ish2\n", encoding="utf-8")
    code_ok = cli.main([
        "task", "done", "t1",
        "--objective", "o",
        "--decisions", "d",
        "--errors", "none",
        "--next", "n",
    ])
    assert code_ok == 0
    assert ledger.get_task("t1")["status"] == "done"


# ---------- helpers -----------------------------------------------------------


def json_recover(stdout: str, task_id: str) -> dict:
    decoder = json.JSONDecoder()
    obj, _end = decoder.raw_decode(stdout.strip())
    assert obj["id"] == task_id
    return obj

