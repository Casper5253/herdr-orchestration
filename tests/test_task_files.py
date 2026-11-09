"""Tests for hx task files: extend and shrink a task's file ownership."""

from __future__ import annotations

import contextlib
import copy
import io
import json
from pathlib import Path

import pytest

from hx import __main__ as cli
from hx import checkpoint, context, host
from hx import ledger as ledger_mod


def _names(paths: list[str]) -> list[str]:
    return [Path(p).name for p in paths]


def _partition(ledger: ledger_mod.Ledger) -> dict:
    leases = ledger_mod._load_json(ledger.leases_path)
    return leases["files"].get(ledger._project_partition_key(), {})


@pytest.fixture
def ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ledger_mod.Ledger:
    ledger_path = tmp_path / ".hx" / "ledger.json"
    leases_path = tmp_path / ".herdr-team" / "state" / "leases.json"
    lock_path = tmp_path / ".hx" / "ledger.lock"
    monkeypatch.setenv("HX_LEDGER_PATH", str(ledger_path))
    monkeypatch.setenv("HX_LEASES_PATH", str(leases_path))
    monkeypatch.setenv("HX_LOCK_PATH", str(lock_path))
    return ledger_mod.Ledger(
        ledger_path=ledger_path,
        leases_path=leases_path,
        lock_path=lock_path,
        repo_root=tmp_path,
    )


def test_add_extends_files_and_leases(
    ledger: ledger_mod.Ledger, tmp_path: Path
) -> None:
    ledger.add_task("t1", title="T", owner="dev-opencode", files=["src/a.py"])
    task = ledger.update_task_files("t1", add=["src/b.py"])

    assert _names(task["files"]) == ["a.py", "b.py"]
    owned = _partition(ledger)
    added = ledger_mod._canonical_path(tmp_path / "src" / "b.py")
    assert added in owned
    assert owned[added]["task_id"] == "t1"
    assert owned[added]["owner"] == "dev-opencode"


def test_add_refuses_a_file_owned_by_another_open_task(
    ledger: ledger_mod.Ledger,
) -> None:
    ledger.add_task("t1", title="T1", owner="dev-opencode", files=["a.py"])
    ledger.add_task("t2", title="T2", owner="dev-devin", files=["b.py"])

    with pytest.raises(ledger_mod.OwnershipConflict, match="t2"):
        ledger.update_task_files("t1", add=["b.py"])

    assert _names(ledger.get_task("t1")["files"]) == ["a.py"]
    assert _names(list(_partition(ledger))) == ["a.py", "b.py"]


def test_add_twice_is_idempotent(ledger: ledger_mod.Ledger) -> None:
    ledger.add_task("t1", title="T", owner="dev-opencode", files=["a.py"])
    ledger.update_task_files("t1", add=["b.py"])
    task = ledger.update_task_files("t1", add=["b.py"])
    assert _names(task["files"]) == ["a.py", "b.py"]


def test_remove_drops_the_file_and_its_lease(ledger: ledger_mod.Ledger) -> None:
    ledger.add_task("t1", title="T", owner="dev-opencode", files=["a.py", "b.py"])

    task = ledger.update_task_files("t1", remove=["a.py"])

    assert _names(task["files"]) == ["b.py"]
    assert _names(list(_partition(ledger))) == ["b.py"]
    ledger.add_task("t2", title="U", owner="dev-devin", files=["a.py"])


def test_remove_of_a_file_the_task_does_not_own_is_a_noop(
    ledger: ledger_mod.Ledger,
) -> None:
    ledger.add_task("t1", title="T", owner="dev-opencode", files=["a.py"])
    task = ledger.update_task_files("t1", remove=["other.py"])
    assert _names(task["files"]) == ["a.py"]


def test_paths_resolve_under_the_task_worktree(
    ledger: ledger_mod.Ledger, tmp_path: Path
) -> None:
    worktree = tmp_path / "wt"
    ledger.add_task(
        "t1", title="T", owner="dev-opencode", files=["a.py"], worktree=str(worktree)
    )

    task = ledger.update_task_files("t1", add=["src/b.py"])

    assert ledger_mod._canonical_path(worktree / "src" / "b.py") in task["files"]
    assert ledger_mod._canonical_path(tmp_path / "src" / "b.py") not in task["files"]


def test_update_keeps_the_task_checkpoint(ledger: ledger_mod.Ledger) -> None:
    ledger.add_task("t1", title="T", owner="dev-opencode", files=["a.py"])
    ledger.update_task("t1", checkpoint=".hx/checkpoints/t1.md")

    task = ledger.update_task_files("t1", add=["b.py"])

    assert task["checkpoint"] == ".hx/checkpoints/t1.md"


def test_empty_change_is_refused(ledger: ledger_mod.Ledger) -> None:
    ledger.add_task("t1", title="T", owner="dev-opencode", files=["a.py"])
    with pytest.raises(ValueError, match="nothing to change"):
        ledger.update_task_files("t1")


def test_cli_adds_and_removes_files(
    ledger: ledger_mod.Ledger, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger.add_task("t1", title="T", owner="dev-opencode", files=["a.py"])
    monkeypatch.setattr(ledger_mod, "default_ledger", lambda: ledger)

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert cli.main(["task", "files", "t1", "--add", "b.py"]) == 0
    assert _names(json.loads(out.getvalue())["files"]) == ["a.py", "b.py"]

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert cli.main(["task", "files", "t1", "--remove", "a.py"]) == 0
    assert _names(json.loads(out.getvalue())["files"]) == ["b.py"]


def test_cli_refuses_a_conflicting_file(
    ledger: ledger_mod.Ledger, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger.add_task("t1", title="T1", owner="dev-opencode", files=["a.py"])
    ledger.add_task("t2", title="T2", owner="dev-devin", files=["b.py"])
    monkeypatch.setattr(ledger_mod, "default_ledger", lambda: ledger)

    assert cli.main(["task", "files", "t1", "--add", "b.py"]) == 1


def test_decision_sees_the_new_ownership(
    ledger: ledger_mod.Ledger, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger.add_task("t1", title="T", owner="hx-codex", files=["owned.py"])
    ledger.set_status("t1", "done")
    monkeypatch.setattr(
        checkpoint, "_run_git", lambda *a: (0, " M owned.py\0 M extra.py\0", "")
    )
    controller = context.Controller(
        ledger, {"context": {"verification_timeout_seconds": 0, "compact_wait_seconds": 0}}
    )
    agent = host.member(
        {
            "name": "hx-codex",
            "agent": "codex",
            "agent_status": "idle",
            "agent_session": {"value": "sid"},
            "cwd": str(tmp_path),
        }
    )
    monkeypatch.setattr(context, "agent_info", lambda name: copy.deepcopy(agent))
    monkeypatch.setattr(
        context,
        "telemetry",
        lambda *a: {
            "session_id": "sid",
            "utilisation": 0.5,
            "sampled_at": context._now().isoformat(),
        },
    )

    before = controller.decide("hx-codex")
    assert "ownership" in before["reason"]

    ledger.update_task_files("t1", add=["extra.py"])

    after = controller.decide("hx-codex")
    assert "ownership" not in after.get("reason", "")
