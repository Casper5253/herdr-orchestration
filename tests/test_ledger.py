"""Tests for hx.ledger."""

from __future__ import annotations

import json

import os
import subprocess
import sys
from pathlib import Path

import pytest

from hx import ledger as ledger_mod


_PROJECT_ROOT = Path(__file__).resolve().parent.parent


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


def test_add_and_get(ledger: ledger_mod.Ledger) -> None:
    task = ledger.add_task(
        "t1",
        title="First task",
        owner="hx-opencode",
        files=["src/foo.py", "src/bar.py"],
    )
    assert task["status"] == "open"
    assert task["rev"] == 1
    assert len(task["files"]) == 2

    loaded = ledger.get_task("t1")
    assert loaded["title"] == "First task"


def test_conflict(ledger: ledger_mod.Ledger) -> None:
    ledger.add_task(
        "t1",
        title="First",
        owner="hx-opencode",
        files=["src/foo.py"],
    )
    with pytest.raises(ledger_mod.OwnershipConflict):
        ledger.add_task(
            "t2",
            title="Second",
            owner="hx-codex",
            files=["src/foo.py"],
        )


def test_worktree_bypasses_conflict(ledger: ledger_mod.Ledger) -> None:
    ledger.add_task(
        "t1",
        title="First",
        owner="hx-opencode",
        files=["src/foo.py"],
    )
    task = ledger.add_task(
        "t2",
        title="Second",
        owner="hx-codex",
        files=["src/foo.py"],
        worktree="C:/wt2",
    )
    assert task["worktree"] == "C:/wt2"


def test_assign_with_worktree(ledger: ledger_mod.Ledger) -> None:
    ledger.add_task(
        "t1",
        title="First",
        owner="hx-opencode",
        files=["src/foo.py"],
    )
    task = ledger.assign_task("t1", owner="hx-codex", worktree="C:/wt1")
    assert task["owner"] == "hx-codex"
    assert task["worktree"] == "C:/wt1"


def test_status_done_removes_lease(ledger: ledger_mod.Ledger) -> None:
    ledger.add_task(
        "t1",
        title="First",
        owner="hx-opencode",
        files=["src/foo.py"],
    )
    leases = ledger_mod._load_json(ledger.leases_path)
    assert "src/foo.py" in leases["files"] or any("foo.py" in k for k in leases["files"])

    ledger.set_status("t1", "done")
    leases = ledger_mod._load_json(ledger.leases_path)
    assert not any("foo.py" in k for k in leases["files"])


def test_list_and_status(ledger: ledger_mod.Ledger) -> None:
    ledger.add_task("t1", title="A", owner="hx-opencode", files=["a.py"])
    ledger.add_task("t2", title="B", owner="hx-codex", files=["b.py"])
    ledger.set_status("t1", "in_progress")

    all_tasks = ledger.list_tasks()
    assert len(all_tasks) == 2

    open_tasks = ledger.list_tasks(status="open")
    assert len(open_tasks) == 1
    assert open_tasks[0]["id"] == "t2"


def test_concurrent_adds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ledger_path = tmp_path / ".hx" / "ledger.json"
    leases_path = tmp_path / ".herdr-team" / "state" / "leases.json"
    lock_path = tmp_path / ".hx" / "ledger.lock"

    script = tmp_path / "worker.py"
    script.write_text(
        """
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
from hx import ledger as ledger_mod

led = ledger_mod.Ledger(
    ledger_path=os.environ['HX_LEDGER_PATH'],
    leases_path=os.environ['HX_LEASES_PATH'],
    lock_path=os.environ['HX_LOCK_PATH'],
    repo_root=os.environ['HX_REPO_ROOT'],
)
led.add_task(sys.argv[1], title=sys.argv[1], owner='hx-opencode', files=[sys.argv[2]])
""",
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["HX_LEDGER_PATH"] = str(ledger_path)
    env["HX_LEASES_PATH"] = str(leases_path)
    env["HX_LOCK_PATH"] = str(lock_path)
    env["HX_REPO_ROOT"] = str(tmp_path)
    env["PYTHONPATH"] = str(_PROJECT_ROOT)

    python = sys.executable
    procs = [
        subprocess.Popen(
            [python, str(script), "p1", "src/a.py"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ),
        subprocess.Popen(
            [python, str(script), "p2", "src/b.py"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ),
    ]
    for p in procs:
        out, err = p.communicate(timeout=30)
        assert p.returncode == 0, f"stdout={out}\nstderr={err}"

    led = ledger_mod.Ledger(
        ledger_path=ledger_path,
        leases_path=leases_path,
        lock_path=lock_path,
        repo_root=tmp_path,
    )
    tasks = led.list_tasks()
    assert {t["id"] for t in tasks} == {"p1", "p2"}


def test_case_folded_paths(ledger: ledger_mod.Ledger) -> None:
    ledger.add_task("t1", title="First", owner="hx-opencode", files=["src/Foo.py"])
    with pytest.raises(ledger_mod.OwnershipConflict):
        ledger.add_task("t2", title="Second", owner="hx-codex", files=["src/foo.py"])


def test_sessions_preserved_across_task_mutation(ledger: ledger_mod.Ledger) -> None:
    assert ledger.acquire_session(
        "hx-opencode", "ses_1", holder="hx-claude", task_id=None
    ) is True
    assert ledger.get_session("hx-opencode")["session_id"] == "ses_1"

    ledger.add_task("t1", title="First", owner="hx-opencode", files=["src/foo.py"])
    ledger.set_status("t1", "in_progress")
    ledger.update_task("t1", title="Renamed")

    leases = ledger_mod._load_json(ledger.leases_path)
    assert leases["sessions"]["hx-opencode"] == {
        "agent": "hx-opencode",
        "session_id": "ses_1",
        "holder": "hx-claude",
        "state": "held",
        "acquired": leases["sessions"]["hx-opencode"]["acquired"],
        "task_id": None,
    }
    assert any("foo.py" in key for key in leases["files"])
    assert ledger.get_session("hx-codex") is None


def test_session_acquire_conflict(ledger: ledger_mod.Ledger) -> None:
    assert ledger.acquire_session("hx-opencode", "ses_1", holder="hx-claude") is True
    assert ledger.acquire_session("hx-opencode", "ses_9", holder="hx-codex") is False
    assert ledger.get_session("hx-opencode")["session_id"] == "ses_1"

    assert (
        ledger.acquire_session("hx-opencode", "ses_2", holder="hx-claude") is True
    )
    assert ledger.get_session("hx-opencode")["session_id"] == "ses_2"

    assert ledger.release_session("hx-opencode", holder="hx-codex") is False
    assert ledger.release_session("hx-opencode", holder="hx-claude") is True
    assert ledger.get_session("hx-opencode") is None
    assert (
        ledger.acquire_session("hx-opencode", "ses_3", holder="hx-codex") is True
    )


def test_session_state_validation(ledger: ledger_mod.Ledger) -> None:
    assert ledger.acquire_session("hx-opencode", "ses_1", holder="hx-opencode") is True
    assert ledger.set_session_state("hx-opencode", "compact-unknown") is True
    assert ledger.get_session("hx-opencode")["state"] == "compact-unknown"
    assert ledger.set_session_state("hx-opencode", "held") is True
    assert ledger.get_session("hx-opencode")["state"] == "held"
    with pytest.raises(ValueError):
        ledger.set_session_state("hx-opencode", "rebooted")
    assert ledger.set_session_state("hx-codex", "held") is False


def test_compact_unknown_blocks_persistent_assign(ledger: ledger_mod.Ledger) -> None:
    ledger.add_task("t1", title="Task 1", owner="hx-opencode", files=["src/a.py"])

    task = ledger.assign_task(
        "t1", owner="hx-opencode", method="persistent", agent="hx-opencode"
    )
    assert task["status"] == "assigned"

    assert ledger.acquire_session("hx-opencode", "ses_1", holder="hx-opencode") is True
    ledger.set_session_state("hx-opencode", "compact-unknown")
    with pytest.raises(ledger_mod.SessionLeaseConflict):
        ledger.assign_task(
            "t1", owner="hx-opencode", method="persistent", agent="hx-opencode"
        )

    ledger.set_session_state("hx-opencode", "held")
    task = ledger.assign_task(
        "t1", owner="hx-opencode", method="persistent", agent="hx-opencode"
    )
    assert task["status"] == "assigned"


def test_persistent_assign_refuses_lease_held_by_other(
    ledger: ledger_mod.Ledger,
) -> None:
    ledger.add_task("t1", title="Task 1", owner="hx-opencode", files=["src/a.py"])
    assert ledger.acquire_session("hx-opencode", "ses_1", holder="hx-claude") is True
    with pytest.raises(ledger_mod.SessionLeaseConflict):
        ledger.assign_task(
            "t1", owner="hx-opencode", method="persistent", agent="hx-opencode"
        )
    ledger.release_session("hx-opencode", holder="hx-claude")
    task = ledger.assign_task(
        "t1", owner="hx-opencode", method="persistent", agent="hx-opencode"
    )
    assert task["status"] == "assigned"


def test_cli_task_add_conflict(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from hx import __main__ as cli

    monkeypatch.setenv("HX_LEDGER_PATH", str(tmp_path / ".hx" / "ledger.json"))
    monkeypatch.setenv("HX_LEASES_PATH", str(tmp_path / ".herdr-team" / "state" / "leases.json"))
    monkeypatch.setenv("HX_LOCK_PATH", str(tmp_path / ".hx" / "ledger.lock"))

    code = cli.main(["task", "add", "t1", "--title", "First", "--owner", "hx-opencode", "--files", "src/foo.py"])
    assert code == 0

    code = cli.main(["task", "add", "t2", "--title", "Second", "--owner", "hx-codex", "--files", "src/foo.py"])
    assert code == 1



def test_context_lease_keyed_by_session_blocks_persistent_assign(ledger: ledger_mod.Ledger) -> None:
    """hx.context writes sessions[<provider session id>] = {agent, token, state: compacting|compact-unknown}."""
    ledger.add_task("t1", title="Task 1", owner="hx-opencode", files=["src/a.py"])
    leases = json.loads(ledger.leases_path.read_text())
    leases["sessions"]["ses_9"] = {"agent": "hx-opencode", "task_id": "t1", "token": "k", "state": "compacting"}
    ledger.leases_path.write_text(json.dumps(leases))
    with pytest.raises(ledger_mod.SessionLeaseConflict):
        ledger.assign_task("t1", owner="hx-opencode", method="persistent", agent="hx-opencode")
