"""Q-1051: cross-repo ownership for hx next and hx balance."""
import json
from pathlib import Path

from hx import balance, ledger as ledger_mod, next as next_mod, task_index


def make_repo(root: Path, leases: Path):
    root.mkdir(parents=True, exist_ok=True)
    return ledger_mod.Ledger(
        ledger_path=root / ".hx" / "ledger.json",
        leases_path=leases,
        lock_path=root / ".hx" / "ledger.lock",
        repo_root=root,
    )


def add_working_task(led, ref="Q-922"):
    led.add_task("work", "work", "dev-opencode", [], jotarium=ref, weight="heavy")
    led.set_status("work", "in_progress")


def test_repo_a_open_task_is_indexed_then_close_frees_it(tmp_path, monkeypatch):
    monkeypatch.setenv("HX_TASK_INDEX_PATH", str(tmp_path / "index.json"))
    monkeypatch.setenv("HX_TASK_INDEX_LOCK_PATH", str(tmp_path / "index.lock"))
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    leases = tmp_path / "leases.json"
    a = make_repo(repo_a, leases)
    make_repo(repo_b, leases)
    add_working_task(a)

    entries = task_index.open_entries()
    assert len(entries) == 1
    assert entries[0]["repo_root"] == str(repo_a.resolve()).lower()
    assert entries[0]["jotarium"] == "Q-922"
    assert entries[0]["owner"] == "dev-opencode"

    a.set_status("work", "done")
    assert task_index.open_entries() == []


def test_hx_next_in_repo_b_skips_repo_a_ref_and_close_frees_it(tmp_path, monkeypatch):
    monkeypatch.setenv("HX_TASK_INDEX_PATH", str(tmp_path / "index.json"))
    monkeypatch.setenv("HX_TASK_INDEX_LOCK_PATH", str(tmp_path / "index.lock"))
    repo_a = tmp_path / "repo-a"
    a = make_repo(repo_a, tmp_path / "leases.json")
    add_working_task(a)

    item = {"ref": "Q-922", "kind": "task", "title": "Rain radar", "status": "todo", "children": {"open": 0, "done": 0}}
    def query(name, payload):
        if name == "listItems": return {"items": [item], "nextCursor": None}
        if name == "getItem": return {"item": item, "comments": [], "activity": []}
        if name == "listIdeas": return {"ideas": []}
        if name == "recentActivity": return {"entries": []}
        raise AssertionError(name)
    class EmptyLedger:
        repo_root = tmp_path / "repo-b"
        def list_tasks(self, status=None): return []
    sources = next_mod.Sources(query, EmptyLedger(), policy={"next": {}}, free_gb=32.0)
    assert "Q-922" not in {unit["ref"] for unit in sources.open_items()}

    a.set_status("work", "done")
    sources = next_mod.Sources(query, EmptyLedger(), policy={"next": {}}, free_gb=32.0)
    assert "Q-922" in {unit["ref"] for unit in sources.open_items()}


def member(name, status="idle"):
    team, _, role = name.rpartition("-")
    return {"name": name, "team": team, "role": role, "kind": role, "status": status,
            "cwd": "", "address": name, "session_id": "s", "session_reason": "test",
            "focused": None, "turn": 1, "created_ms": 1}


def test_balance_in_repo_b_counts_repo_a_heavy_slot_and_close_frees_it(tmp_path, monkeypatch):
    monkeypatch.setenv("HX_TASK_INDEX_PATH", str(tmp_path / "index.json"))
    monkeypatch.setenv("HX_TASK_INDEX_LOCK_PATH", str(tmp_path / "index.lock"))
    repo_a = tmp_path / "repo-a"
    a = make_repo(repo_a, tmp_path / "leases.json")
    add_working_task(a)

    class EmptyLedger:
        repo_root = tmp_path / "repo-b"
        def list_tasks(self, status=None): return []
    members = [member("dev-lead", "working"), member("dev-opencode", "idle")]
    kwargs = dict(ledger=EmptyLedger(), members=members,
        policy={"team_prefix":"dev-","lead_role":"lead","next":{"heavy_cap":1,"heavy_cap_when_memory_ok":2,"free_memory_gb_threshold":6}},
        free_gb=32, oneoff_count=0, next_unit=lambda **kw: {"source":"audit","ref":"u","title":"u","weight":"light","worker_hint":"opencode","why":"x"},
        read_screen=lambda n, lines=240: "same", report_time=lambda w,l: None,
        state_path=tmp_path / "watch.json")
    rows = balance.snapshot(**kwargs)
    assert rows["running_heavy"] == 1
    slot = next(s for s in rows["slots"] if s["worker"] == "dev-opencode")
    assert slot["state"] == "heavy"
    assert slot["task_id"] == "work"
    assert slot["repo_root"] == str(repo_a.resolve()).lower()

    a.set_status("work", "done")
    rows = balance.snapshot(**kwargs)
    assert rows["running_heavy"] == 0
    slot = next(s for s in rows["slots"] if s["worker"] == "dev-opencode")
    assert slot["state"] == "idle"
    assert slot["task_id"] is None


def test_concurrent_syncs_leave_a_valid_index(tmp_path, monkeypatch):
    monkeypatch.setenv("HX_TASK_INDEX_PATH", str(tmp_path / "index.json"))
    monkeypatch.setenv("HX_TASK_INDEX_LOCK_PATH", str(tmp_path / "index.lock"))
    import threading
    repos = []
    leases = tmp_path / "leases.json"
    for number in range(4):
        led = make_repo(tmp_path / f"repo-{number}", leases)
        add_working_task(led, f"Q-{1000+number}")
        repos.append(led)
    threads = [threading.Thread(target=task_index.sync_repo, kwargs={
        "repo_root": led.repo_root, "ledger_path": led.ledger_path,
        "tasks": led.list_tasks(), "open_statuses": ledger_mod.Ledger.OPEN_STATUSES}) for led in repos]
    for thread in threads: thread.start()
    for thread in threads: thread.join()
    entries = task_index.open_entries()
    assert len(entries) == 4
    assert {entry["jotarium"] for entry in entries} == {"Q-1000", "Q-1001", "Q-1002", "Q-1003"}


def test_hx_task_index_migrates_existing_open_tasks(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HX_TASK_INDEX_PATH", str(tmp_path / "index.json"))
    monkeypatch.setenv("HX_TASK_INDEX_LOCK_PATH", str(tmp_path / "index.lock"))
    repo_a = tmp_path / "repo-a"
    ledger_path = repo_a / ".hx" / "ledger.json"
    a = make_repo(repo_a, tmp_path / "leases.json")
    add_working_task(a)
    (tmp_path / "index.json").unlink()

    monkeypatch.setenv("HX_PROJECT_ROOT", str(repo_a))
    monkeypatch.setenv("HX_LEDGER_PATH", str(ledger_path))
    monkeypatch.setenv("HX_LOCK_PATH", str(repo_a / ".hx" / "ledger.lock"))
    from hx import __main__ as cli
    assert cli.main(["task", "index"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["indexed"] == 1
    assert [entry["jotarium"] for entry in task_index.open_entries()] == ["Q-922"]


def test_stale_open_index_entry_does_not_block_when_repo_ledger_is_done(tmp_path, monkeypatch):
    monkeypatch.setenv("HX_TASK_INDEX_PATH", str(tmp_path / "index.json"))
    monkeypatch.setenv("HX_TASK_INDEX_LOCK_PATH", str(tmp_path / "index.lock"))
    repo_a = tmp_path / "repo-a"
    a = make_repo(repo_a, tmp_path / "leases.json")
    add_working_task(a)
    assert task_index.open_entries()

    # Simulate a crash after the ledger close but before the index sync. The
    # index still says in_progress; verification against the repo ledger wins.
    data = json.loads((repo_a / ".hx" / "ledger.json").read_text(encoding="utf-8"))
    data["tasks"]["work"]["status"] = "done"
    (repo_a / ".hx" / "ledger.json").write_text(json.dumps(data), encoding="utf-8")
    assert task_index.open_entries() == []
