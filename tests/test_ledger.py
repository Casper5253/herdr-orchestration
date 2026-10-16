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


def _owned_paths(leases: dict, ledger: ledger_mod.Ledger) -> list[str]:
    """Flat list of file-lease paths inside this ledger's project partition."""
    partition = leases["files"].get(ledger._project_partition_key(), {})
    return list(partition)


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
    assert _owned_paths(leases, ledger)


    ledger.set_status("t1", "done")
    leases = ledger_mod._load_json(ledger.leases_path)
    assert not _owned_paths(leases, ledger)


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
    assert any("foo.py" in key for key in _owned_paths(leases, ledger))
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


# ---------- correction round 1: shared leases lock, pid steal, partitions -----


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True)


def test_persistent_assign_requires_agent(ledger: ledger_mod.Ledger) -> None:
    ledger.add_task("t1", title="T", owner="hx-opencode", files=["src/a.py"])
    with pytest.raises(ValueError):
        ledger.assign_task("t1", owner="hx-opencode", method="persistent")


def test_persistent_assign_atomically_reserves_session(ledger: ledger_mod.Ledger) -> None:
    ledger.add_task("t1", title="T", owner="hx-opencode", files=["src/a.py"])
    task = ledger.assign_task("t1", owner="w", method="persistent", agent="hx-opencode")
    assert task["status"] == "assigned"
    lease = ledger.get_session("hx-opencode")
    assert lease is not None
    assert lease["holder"] == "w"
    assert lease["state"] == "held"
    assert lease["task_id"] == "t1"


def test_lock_files_record_pid_and_are_stolen_when_dead(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    leases_lock = tmp_path / "leases.lock"
    monkeypatch.setenv("HX_LEDGER_PATH", str(tmp_path / "ledger.json"))
    monkeypatch.setenv("HX_LEASES_PATH", str(tmp_path / "leases.json"))
    monkeypatch.setenv("HX_LOCK_PATH", str(tmp_path / "l.lock"))
    led = ledger_mod.Ledger(repo_root=tmp_path)
    assert led.leases_lock_path == leases_lock

    with led._leases_lock():
        info = json.loads(leases_lock.read_text(encoding="utf-8"))
        assert info["pid"] == os.getpid()

    leases_lock.write_text(json.dumps({"pid": 99999999, "ts": "2026-01-01"}), encoding="utf-8")
    with led._leases_lock():
        assert json.loads(leases_lock.read_text(encoding="utf-8"))["pid"] == os.getpid()


def test_lock_is_not_stolen_while_pid_alive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock_path = tmp_path / "l.lock"
    lock_path.write_text(
        json.dumps({"pid": os.getpid(), "ts": ledger_mod._utcnow().isoformat()}),
        encoding="utf-8",
    )
    with pytest.raises(ledger_mod.LockTimeout):
        ledger_mod._acquire_lock(lock_path, stale_seconds=1.0, retries=2, delay=0.05)


def test_mutations_take_shared_leases_lock_next_to_leases_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    leases_path = tmp_path / "leases.json"
    monkeypatch.setenv("HX_LEDGER_PATH", str(tmp_path / "ledger.json"))
    monkeypatch.setenv("HX_LEASES_PATH", str(leases_path))
    monkeypatch.setenv("HX_LOCK_PATH", str(tmp_path / "l.lock"))
    led = ledger_mod.Ledger(repo_root=tmp_path)

    observed: list[Path] = []
    original_acquire = ledger_mod._acquire_lock

    def recording_acquire(path: Path, stale_seconds: float = 60.0, retries: int = 100, delay: float = 0.05) -> None:
        observed.append(path)
        original_acquire(path, stale_seconds=stale_seconds, retries=retries, delay=delay)

    monkeypatch.setattr(ledger_mod, "_acquire_lock", recording_acquire)
    led.add_task("t1", title="T", owner="o", files=["a.py"])

    assert ledger_mod.Path(tmp_path / "l.lock") in observed
    assert ledger_mod.Path(tmp_path / "leases.lock") in observed
    assert observed.index(tmp_path / "l.lock") < observed.index(tmp_path / "leases.lock")


def test_file_leases_partitioned_per_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    leases = tmp_path / "leases.json"
    monkeypatch.setenv("HX_LEASES_PATH", str(leases))

    led_a = ledger_mod.Ledger(
        ledger_path=tmp_path / "projA" / ".hx" / "ledger.json",
        leases_path=leases,
        lock_path=tmp_path / "projA" / ".hx" / "l.lock",
        repo_root=tmp_path / "projA",
    )
    led_b = ledger_mod.Ledger(
        ledger_path=tmp_path / "projB" / ".hx" / "ledger.json",
        leases_path=leases,
        lock_path=tmp_path / "projB" / ".hx" / "l.lock",
        repo_root=tmp_path / "projB",
    )

    led_a.add_task("tA", title="A", owner="o", files=["a.py"])
    led_b.add_task("tB", title="B", owner="o", files=["a.py"])

    raw = json.loads(leases.read_text(encoding="utf-8"))
    partition_a = raw["files"][led_a._project_partition_key()]
    partition_b = raw["files"][led_b._project_partition_key()]
    assert any(e["task_id"] == "tA" for e in partition_a.values())
    assert any(e["task_id"] == "tB" for e in partition_b.values())

    subsession = raw["sessions"].get("x")
    assert subsession is None

    # Recomputing project A's partition must not drop project B's leases.
    led_a.add_task("tA2", title="A2", owner="o", files=["b.py"])
    raw = json.loads(leases.read_text(encoding="utf-8"))
    partition_b = raw["files"][led_b._project_partition_key()]
    assert any(e["task_id"] == "tB" for e in partition_b.values())


def test_worktree_claims_verified_against_git_worktree_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    leases = tmp_path / "leases.json"
    monkeypatch.setenv("HX_LEASES_PATH", str(leases))
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "owned1.py").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "seed")
    wt = tmp_path / "wt"
    _git(repo, "worktree", "add", str(wt), "-b", "wtbranch")

    led = ledger_mod.Ledger(
        ledger_path=tmp_path / "repo" / ".hx" / "ledger.json",
        leases_path=leases,
        lock_path=tmp_path / "repo" / ".hx" / "l.lock",
        repo_root=repo,
    )

    task = led.add_task("t1", title="T", owner="o", files=["owned1.py"], worktree=str(wt))
    assert task["worktree"] == str(wt)

    with pytest.raises(ValueError):
        led.add_task("t2", title="T2", owner="o", files=["owned2.py"], worktree=str(tmp_path / "bogus"))

    with pytest.raises(ledger_mod.OwnershipConflict):
        led.add_task("t3", title="T3", owner="o", files=["owned1.py"], worktree=str(wt))

    task = led.add_task("t4", title="T4", owner="o", files=["owned1.py"])
    assert task["worktree"] is None


# ---------- correction round 2 ------------------------------------------------


def test_dual_lock_releases_acquired_locks_when_a_later_acquire_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = tmp_path / "first.lock"
    second = tmp_path / "second.lock"
    original = ledger_mod._acquire_lock

    def failing_second(path: Path, stale_seconds: float = 60.0, retries: int = 100, delay: float = 0.05) -> None:
        if path == second:
            raise ledger_mod.LockTimeout(f"forced failure on {path}")
        original(path, stale_seconds=stale_seconds, retries=retries, delay=delay)

    monkeypatch.setattr(ledger_mod, "_acquire_lock", failing_second)
    with pytest.raises(ledger_mod.LockTimeout):
        with ledger_mod.DualLock([first, second]):
            pass
    assert not first.exists()
    assert not second.exists()


def test_persistent_reservation_refuses_same_owner_other_task(
    ledger: ledger_mod.Ledger,
) -> None:
    ledger.add_task("t1", title="T1", owner="w", files=["src/a.py"])
    ledger.add_task("t2", title="T2", owner="w", files=["src/b.py"])
    ledger.assign_task("t1", owner="w", method="persistent", agent="hx-opencode")
    with pytest.raises(ledger_mod.SessionLeaseConflict):
        ledger.assign_task("t2", owner="w", method="persistent", agent="hx-opencode")


def test_primary_root_and_none_worktree_are_the_same_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    leases = tmp_path / "leases.json"
    monkeypatch.setenv("HX_LEASES_PATH", str(leases))
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "owned1.py").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "seed")
    wt = tmp_path / "wt"
    _git(repo, "worktree", "add", str(wt), "-b", "wtbranch")

    led = ledger_mod.Ledger(
        ledger_path=tmp_path / "repo" / ".hx" / "ledger.json",
        leases_path=leases,
        lock_path=tmp_path / "repo" / ".hx" / "l.lock",
        repo_root=repo,
    )

    led.add_task("t_none", title="T", owner="o", files=["owned1.py"])
    from hx.ledger import OwnershipConflict

    with pytest.raises(OwnershipConflict):
        led.add_task(
            "t_root", title="T", owner="o", files=["owned1.py"], worktree=str(repo)
        )

    # The primary root as worktree is a valid git worktree of the repo.
    task = led.add_task(
        "t_root_otherfile", title="T", owner="o", files=["owned2foo.py"], worktree=str(repo)
    )
    assert task["worktree"] is not None


def test_worktree_verification_fails_closed_on_git_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    leases = tmp_path / "leases.json"
    monkeypatch.setenv("HX_LEASES_PATH", str(leases))
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    monkeypatch.setattr(
        ledger_mod,
        "_run_git",
        lambda args, cwd: (1, "", "git exploded"),
    )
    led = ledger_mod.Ledger(
        ledger_path=tmp_path / "repo" / ".hx" / "ledger.json",
        leases_path=leases,
        lock_path=tmp_path / "repo" / ".hx" / "l.lock",
        repo_root=repo,
    )
    with pytest.raises(ValueError):
        led.add_task("t1", title="T", owner="o", files=["a.py"], worktree=str(tmp_path / "wt"))


def test_reservation_released_on_close_and_status(
    ledger: ledger_mod.Ledger,
) -> None:
    ledger.add_task("t1", title="T1", owner="w", files=["src/a.py"])
    ledger.assign_task("t1", owner="w", method="persistent", agent="hx-opencode")
    assert ledger.get_session("hx-opencode") is not None

    ledger.set_status("t1", "done")
    assert ledger.get_session("hx-opencode") is None

    ledger.add_task("t2", title="T2", owner="w", files=["src/b.py"])
    ledger.assign_task("t2", owner="w", method="persistent", agent="hx-opencode")
    assert ledger.get_session("hx-opencode") is not None

    ledger.set_status("t2", "failed")
    assert ledger.get_session("hx-opencode") is None

    ledger.add_task("t3", title="T3", owner="w", files=["src/c.py"])
    ledger.assign_task("t3", owner="w", method="persistent", agent="hx-opencode")

    def writer(task, rev):
        return ledger.leases_path.parent / "unused.md"

    ledger.complete_task("t3", "blocked", writer)
    assert ledger.get_session("hx-opencode") is None


def test_reservation_released_on_close_via_checkpoint_writer(
    ledger: ledger_mod.Ledger,
) -> None:
    from hx import checkpoint as checkpoint_mod

    ledger.add_task("t1", title="T1", owner="w", files=["src/a.py"])
    ledger.assign_task("t1", owner="w", method="persistent", agent="hx-opencode")
    checkpoint_mod.write_completion_checkpoint(
        "t1",
        ledger=ledger,
        status="done",
        objective="o",
        decisions="d",
        errors="none",
        next_action="n",
    )
    assert ledger.get_session("hx-opencode") is None


def test_task_release_cli(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from hx import __main__ as cli

    monkeypatch.setenv("HX_LEDGER_PATH", str(tmp_path / ".hx" / "ledger.json"))
    monkeypatch.setenv("HX_LEASES_PATH", str(tmp_path / ".herdr-team" / "state" / "leases.json"))
    monkeypatch.setenv("HX_LOCK_PATH", str(tmp_path / ".hx" / "ledger.lock"))
    led = ledger_mod.Ledger(repo_root=tmp_path)
    led.add_task("t1", title="T", owner="w", files=["a.py"])
    led.assign_task("t1", owner="w", method="persistent", agent="hx-opencode")
    assert led.get_session("hx-opencode") is not None

    import io, contextlib

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = cli.main(["task", "release", "t1"])
    assert code == 0
    payload = json.loads(buf.getvalue())
    assert payload["released"] == 1
    assert led.get_session("hx-opencode") is None


def test_mutate_leases_runs_under_shared_lock_and_saves(
    ledger: ledger_mod.Ledger, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock_seen: list[Path] = []
    original = ledger_mod._acquire_lock

    def spy(path: Path, stale_seconds: float = 60.0, retries: int = 100, delay: float = 0.05) -> None:
        lock_seen.append(path)
        original(path, stale_seconds=stale_seconds, retries=retries, delay=delay)

    monkeypatch.setattr(ledger_mod, "_acquire_lock", spy)

    def add_entry(data: dict) -> str:
        data["sessions"]["hx-codex"] = {"agent": "hx-codex", "session_id": "s1", "state": "held"}
        return "done"

    result = ledger.mutate_leases(add_entry)
    assert result == "done"
    assert lock_seen == [ledger_mod.Path(ledger.leases_lock_path)]
    assert ledger.get_session("hx-codex")["session_id"] == "s1"

    def round_trip(ledger_data: dict, data: dict) -> tuple[int, bool]:
        return ledger_data["rev"], "files" in data

    rev, has_files = ledger.mutate_leases_with_repo(round_trip)
    assert rev >= 0
    assert has_files is True
    assert ledger.get_session("hx-codex") is not None
