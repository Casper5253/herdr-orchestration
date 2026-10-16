"""Protected compaction with fake Herdr and provider telemetry."""

import copy
import datetime as dt
import json
import sqlite3
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from hx import __main__ as cli
from hx import checkpoint, context, ledger


@pytest.fixture
def controller(tmp_path, monkeypatch):
    led = ledger.Ledger(repo_root=tmp_path, ledger_path=tmp_path / "ledger.json",
                        leases_path=tmp_path / "leases.json", lock_path=tmp_path / "lock")
    led.add_task("t", "Completed work", "hx-codex", ["owned.py"], base_commit="base")
    led.set_status("t", "done")
    cp = tmp_path / "checkpoint.md"
    cp.write_text("## Decisions\nKeep the design.\n\n## Exact errors\nNone.\n\n## Next action\nRun acceptance.\n\n## References\nDocs.\n", encoding="utf-8")
    monkeypatch.setattr(checkpoint, "validate_checkpoint", lambda *a, **kw: {"ok": True, "checkpoint": str(cp)})
    monkeypatch.setattr(checkpoint, "_run_git", lambda *a: (0, "", ""))
    c = context.Controller(led, {"context": {"verification_timeout_seconds": 0}})
    c.agent = {"name": "hx-codex", "agent": "codex", "agent_status": "idle",
               "agent_session": {"value": "sid"}, "cwd": str(tmp_path)}
    c.calls = []
    c.sample = {"session_id": "sid", "utilisation": .7,
                "sampled_at": context._now().isoformat()}
    monkeypatch.setattr(context, "agent_info", lambda name: copy.deepcopy(c.agent))
    monkeypatch.setattr(context, "telemetry", lambda *a: dict(c.sample))

    def herdr(*args, **kwargs):
        c.calls.append(args)
        if args[0] == "prompt" and args[2] == "/compact":
            lease = json.loads(led.leases_path.read_text())["sessions"]["sid"]
            assert lease["state"] == "compacting"
            c.sample.update(utilisation=.2, sampled_at=context._now().isoformat())
        if args[0] == "read":
            token = json.loads(led.leases_path.read_text())["sessions"]["sid"]["token"]
            return {"result": {"text": f"{token} Run acceptance."}}
        return {"result": {"type": "agent_prompted"}}

    monkeypatch.setattr(context, "_herdr", herdr)
    return c


def test_success_reserves_verifies_restores_and_releases(controller):
    result = controller.compact("hx-codex")
    assert result["action"] == "compacted"
    assert result["telemetry"]["utilisation"] == .2
    assert controller.calls[0][2] == "/compact"
    assert "checkpoint.md" in controller.calls[1][2]
    assert json.loads(controller.ledger.leases_path.read_text())["sessions"] == {}
    assert controller.decide("hx-codex")["action"] == "keep"


@pytest.mark.parametrize("change,reason", [
    ({"agent_status": "working"}, "not idle"),
    ({"agent_status": "blocked"}, "not idle"),
    ({"agent": "claude"}, "native auto"),
    ({"cwd": "elsewhere"}, "repository"),
])
def test_decision_rejects_unsafe_agent(controller, change, reason):
    controller.agent.update(change)
    assert reason in controller.decide("hx-codex")["reason"]
    assert controller.compact("hx-codex")["action"] == "keep"
    assert not controller.calls


@pytest.mark.parametrize("sample", [
    {"session_id": "another"}, {"utilisation": None},
    {"sampled_at": "2000-01-01T00:00:00+00:00"},
    {"utilisation": float("nan")}, {"sampled_at": "invalid"},
])
def test_unknown_or_stale_never_compacts(controller, sample):
    controller.sample.update(sample)
    assert controller.compact("hx-codex")["action"] == "keep"
    assert not controller.calls


def test_boundary_only_and_related_work(controller):
    assert controller.decide("hx-codex")["action"] == "compact"
    assert controller.decide("hx-codex", related=True)["action"] == "keep"
    controller.sample["utilisation"] = .39
    assert controller.decide("hx-codex")["action"] == "keep"
    controller.sample["utilisation"] = .99
    controller.ledger.set_status("t", "in_progress")
    assert controller.compact("hx-codex")["action"] == "keep"


def test_checkpoint_failure_sends_nothing_and_blocks_retry(controller, monkeypatch):
    monkeypatch.setattr(checkpoint, "validate_checkpoint", lambda *a, **kw: {"ok": False, "errors": ["stale"]})
    assert "checkpoint validation failed" in controller.compact("hx-codex")["reason"]
    assert not controller.calls
    assert json.loads(controller.ledger.leases_path.read_text())["sessions"] == {}
    assert "loop guard" in controller.decide("hx-codex")["reason"]


def test_empty_authored_checkpoint_rejected(controller):
    (controller.ledger.repo_root / "checkpoint.md").write_text("## Decisions\n\n## Exact errors\nNone\n\n## Next action\nNext\n", encoding="utf-8")
    assert "section empty" in controller.compact("hx-codex")["reason"]
    assert not controller.calls


def test_unowned_edits_rejected(controller, monkeypatch):
    monkeypatch.setattr(checkpoint, "_run_git", lambda *a: (0, " M other.py\0", ""))
    assert "ownership" in controller.compact("hx-codex")["reason"]
    assert not controller.calls


@pytest.fixture
def cross_repo(controller, monkeypatch):
    pane_root = controller.ledger.repo_root / "pane-repo"
    pane_root.mkdir()
    controller.agent["cwd"] = str(pane_root / "subdir")
    statuses = {}
    calls = []

    def git(args, cwd):
        cwd = cwd.resolve()
        calls.append((args, cwd))
        if args == ["rev-parse", "--show-toplevel"]:
            return 0, str(pane_root), ""
        return 0, statuses.get(cwd, ""), ""

    monkeypatch.setattr(checkpoint, "_run_git", git)
    return controller, pane_root, statuses, calls


@pytest.mark.parametrize("recorded_worktree", [False, True])
def test_clean_separate_pane_repository_is_compactable(cross_repo, recorded_worktree):
    controller, pane_root, statuses, calls = cross_repo
    task_root = controller.ledger.repo_root
    if recorded_worktree:
        task_root = task_root / "task-worktree"
        task_root.mkdir()
        controller.ledger.update_task("t", worktree=str(task_root),
                                      files=[ledger._canonical_path(task_root / "owned.py")])
    assert controller.decide("hx-codex")["action"] == "compact"
    assert controller.compact("hx-codex")["action"] == "compacted"
    checked_roots = {cwd for args, cwd in calls if args[0] == "status"}
    assert checked_roots == {task_root, pane_root}


def test_same_repository_pane_subdirectory_is_allowed(controller, monkeypatch):
    root = controller.ledger.repo_root
    controller.agent["cwd"] = str(root / "subdir")
    calls = []

    def git(args, cwd):
        calls.append((args, cwd))
        return (0, str(root), "") if args[0] == "rev-parse" else (0, "", "")

    monkeypatch.setattr(checkpoint, "_run_git", git)
    assert controller.decide("hx-codex")["action"] == "compact"
    assert [cwd for args, cwd in calls if args[0] == "status"] == [root]


def test_dirty_open_task_in_pane_repository_blocks_compaction(cross_repo):
    controller, pane_root, statuses, calls = cross_repo
    pane_ledger = ledger.Ledger(repo_root=pane_root,
                                ledger_path=pane_root / ".hx" / "ledger.json",
                                lock_path=pane_root / ".hx" / "ledger.lock",
                                leases_path=controller.ledger.leases_path)
    pane_ledger.add_task("unfinished", "Other work", "hx-codex", ["unfinished.py"], base_commit="base")
    pane_ledger.set_status("unfinished", "in_progress")
    statuses[pane_root] = " M unfinished.py\0"
    result = controller.compact("hx-codex")
    assert result["action"] == "keep"
    assert "owned by an open task in pane repository" in result["reason"]
    assert not controller.calls


@pytest.mark.parametrize("dirty_tree", ["task", "pane"])
def test_cross_repo_unknown_dirt_blocks_compaction(cross_repo, dirty_tree):
    controller, pane_root, statuses, calls = cross_repo
    root = controller.ledger.repo_root if dirty_tree == "task" else pane_root
    statuses[root] = "?? unrecorded.py\0"
    result = controller.compact("hx-codex")
    assert result["action"] == "keep"
    assert "ownership" in result["reason"] if dirty_tree == "task" else "uncheckpointed" in result["reason"]
    assert not controller.calls


def test_cross_repo_dirt_is_rechecked_before_sending(cross_repo, monkeypatch):
    controller, pane_root, statuses, calls = cross_repo
    assert controller.decide("hx-codex")["action"] == "compact"
    original_validate = checkpoint.validate_checkpoint

    def validate(*args, **kwargs):
        statuses[pane_root] = " M newly-dirty.py\0"
        return original_validate(*args, **kwargs)

    monkeypatch.setattr(checkpoint, "validate_checkpoint", validate)
    result = controller.compact("hx-codex")
    assert result["action"] == "keep"
    assert "uncheckpointed" in result["reason"]
    assert not controller.calls


@pytest.mark.parametrize("failure", ["root", "status"])
def test_cross_repo_git_failure_is_not_treated_as_clean(cross_repo, monkeypatch, failure):
    controller, pane_root, statuses, calls = cross_repo
    original_git = checkpoint._run_git

    def git(args, cwd):
        if (failure == "root" and args[0] == "rev-parse") or (failure == "status" and cwd == pane_root):
            return 1, "", "git unavailable"
        return original_git(args, cwd)

    monkeypatch.setattr(checkpoint, "_run_git", git)
    result = controller.compact("hx-codex")
    assert result["action"] == "keep"
    assert "git unavailable" in result["reason"]
    assert not controller.calls


def test_busy_immediately_before_send_rejected(controller, monkeypatch):
    replies = iter([dict(controller.agent), dict(controller.agent, agent_status="working")])
    monkeypatch.setattr(context, "agent_info", lambda name: next(replies))
    assert "became busy" in controller.compact("hx-codex")["reason"]
    assert not controller.calls


def test_timeout_holds_lease_until_safe_recovery(controller, monkeypatch):
    def timeout(*a, **kw):
        raise subprocess.TimeoutExpired("herdr", 310)
    monkeypatch.setattr(context, "_herdr", timeout)
    assert controller.compact("hx-codex")["action"] == "compact-unknown"
    assert "leased" in controller.decide("hx-codex")["reason"]
    assert controller.recover("hx-codex")["action"] == "keep"
    controller.sample["sampled_at"] = context._now().isoformat()
    controller.agent["agent_status"] = "working"
    assert controller.recover("hx-codex")["action"] == "keep"
    controller.agent["agent_status"] = "idle"
    assert controller.recover("hx-codex")["action"] == "recovered"
    assert "loop guard" in controller.decide("hx-codex")["reason"]


@pytest.mark.parametrize("failure", ["session", "telemetry-session", "stale", "unchanged", "ack"])
def test_unverified_completion_is_unknown(controller, monkeypatch, failure):
    original = context._herdr
    def herdr(*args, **kwargs):
        before = dict(controller.sample)
        result = original(*args, **kwargs)
        if args[0] == "prompt" and args[2] == "/compact":
            if failure == "session":
                controller.agent["agent_session"] = {"value": "new"}
            elif failure == "telemetry-session":
                controller.sample["session_id"] = "other"
            elif failure == "stale":
                controller.sample["sampled_at"] = before["sampled_at"]
            elif failure == "unchanged":
                controller.sample["utilisation"] = .7
        if args[0] == "read" and failure == "ack":
            return {"result": {"text": "I forgot."}}
        return result
    monkeypatch.setattr(context, "_herdr", herdr)
    assert controller.compact("hx-codex")["action"] == "compact-unknown"
    assert json.loads(controller.ledger.leases_path.read_text())["sessions"]["sid"]["state"] == "compact-unknown"


def test_existing_lease_prevents_send(controller):
    ledger._atomic_write_json(controller.ledger.leases_path, {"files": {}, "sessions": {"sid": {"state": "dispatch"}}})
    assert controller.compact("hx-codex")["action"] == "keep"
    assert not controller.calls


def test_previous_session_lease_also_blocks_agent(controller):
    ledger._atomic_write_json(controller.ledger.leases_path, {"files": {}, "sessions": {"old": {"agent": "hx-codex", "state": "compact-unknown"}}})
    assert controller.compact("hx-codex")["action"] == "keep"
    assert not controller.calls


def test_other_team_never_contacted(controller, monkeypatch):
    def forbidden(name):
        pytest.fail("must not contact another team")
    monkeypatch.setattr(context, "agent_info", forbidden)
    assert "outside" in controller.decide("dev-codex")["reason"]


def test_multiline_acknowledgement_matches_parsed_text():
    assert context._has_acknowledgement({"result": {"text": "nonce First\nSecond"}}, "nonce First\nSecond")


@pytest.mark.parametrize("guard", [
    {"last_at": context._now().isoformat(), "tasks": {}},
    {"tasks": {"t": {"count": 2}}},
    {"tasks": {"t": {"count": 0, "failed": True}}},
])
def test_guard_rejects_recent_or_repeated_compaction(controller, guard):
    ledger._atomic_write_json(controller.guard_path, {"sid": guard})
    assert controller.compact("hx-codex")["action"] == "keep"
    assert not controller.calls


def test_stale_acknowledgement_without_current_nonce_rejected(controller, monkeypatch):
    original = context._herdr
    def herdr(*args, **kwargs):
        if args[0] == "read":
            return {"result": {"text": "Run acceptance."}}
        return original(*args, **kwargs)
    monkeypatch.setattr(context, "_herdr", herdr)
    assert controller.compact("hx-codex")["action"] == "compact-unknown"


def test_reservation_rechecks_task_after_decision(controller, monkeypatch):
    original = controller.decide
    def decide(*args):
        result = original(*args)
        controller.ledger.set_status("t", "in_progress")
        return result
    monkeypatch.setattr(controller, "decide", decide)
    assert controller.compact("hx-codex")["action"] == "keep"
    assert not controller.calls


def test_cli_routes_context_commands(controller, monkeypatch, capsys):
    monkeypatch.setattr(context, "Controller", lambda: controller)
    assert cli.main(["context", "decide", "hx-codex"]) == 0
    assert json.loads(capsys.readouterr().out)["action"] == "compact"
    assert cli.main(["compact", "hx-codex", "--task", "t"]) == 0
    assert json.loads(capsys.readouterr().out)["action"] == "compacted"
    assert cli.main(["context", "recover", "hx-codex"]) == 0
    assert json.loads(capsys.readouterr().out)["action"] == "keep"


def test_herdr_uses_argument_array_without_shell(monkeypatch):
    def run(args, **kwargs):
        assert args == ["herdr", "agent", "prompt", "hx-codex", "/compact"]
        assert kwargs["shell"] is False
        return subprocess.CompletedProcess(args, 0, '{"result":{}}', "")
    monkeypatch.setattr(subprocess, "run", run)
    assert context._herdr("prompt", "hx-codex", "/compact") == {"result": {}}


def test_codex_telemetry_uses_exact_session_and_cwd(tmp_path, monkeypatch):
    monkeypatch.setenv("HX_CODEX_SESSIONS_DIR", str(tmp_path))
    now = context._now().isoformat()
    def rollout(sid, tokens, cwd):
        events = [{"type": "session_meta", "payload": {"id": sid, "cwd": str(cwd)}},
                  {"type": "event_msg", "timestamp": now, "payload": {"type": "token_count", "info": {"last_token_usage": {"input_tokens": tokens}, "model_context_window": 1000}}}]
        (tmp_path / f"rollout-{sid}.jsonl").write_text("\n".join(map(json.dumps, events)), encoding="utf-8")
    rollout("wanted", 600, tmp_path)
    rollout("other", 10, tmp_path)
    agent = {"agent": "codex", "agent_session": {"value": "wanted"}, "cwd": str(tmp_path)}
    assert context.telemetry(agent, {})["utilisation"] == .6
    agent["cwd"] = str(tmp_path / "elsewhere")
    assert context.telemetry(agent, {})["utilisation"] is None


def test_claude_telemetry_requires_session_specific_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("HX_STATE_DIR", str(tmp_path))
    path = tmp_path / "claude-status-sid.json"
    agent = {"agent": "claude", "agent_session": {"value": "sid"}}
    path.write_text(json.dumps({"session_id": "sid", "context_window": {"used_percentage": 60}}))
    assert context.telemetry(agent, {})["utilisation"] == .6
    path.write_text(json.dumps({"session_id": "other", "context_window": {"used_percentage": 60}}))
    assert context.telemetry(agent, {})["utilisation"] is None


def test_opencode_telemetry_filters_session_and_requires_model_limit(tmp_path, monkeypatch):
    db = tmp_path / "opencode.db"
    monkeypatch.setenv("HX_OPENCODE_DB", str(db))
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE message (session_id TEXT, time_created INTEGER, data TEXT)")
        for sid, value in [("sid", 100), ("other", 800)]:
            conn.execute("INSERT INTO message VALUES (?, ?, ?)", (sid, 1000, json.dumps({"role": "assistant", "modelID": "model", "tokens": {"input": value, "cache": {"read": 200}}})))
    agent = {"agent": "opencode", "agent_session": {"value": "sid"}}
    assert context.telemetry(agent, {"context_limits": {"model": 1000}})["utilisation"] == .3
    assert context.telemetry(agent, {})["utilisation"] is None


@pytest.mark.parametrize("outcome", ["success", "timeout"])
def test_lease_access_is_shared_locked_and_herdr_is_unlocked(controller, monkeypatch, outcome):
    led = controller.ledger
    original_load = led._load_leases
    original_herdr = context._herdr
    reads = []

    def load_leases():
        assert led.leases_lock_path.exists(), "every controller lease access needs the shared lock"
        reads.append(True)
        return original_load()

    def herdr(*args, **kwargs):
        assert not led.leases_lock_path.exists(), "Herdr must run outside the shared lock"
        assert not led.lock_path.exists(), "Herdr must run outside the repo lock"
        if outcome == "timeout":
            raise subprocess.TimeoutExpired("herdr", 310)
        return original_herdr(*args, **kwargs)

    monkeypatch.setattr(led, "_load_leases", load_leases)
    monkeypatch.setattr(context, "_herdr", herdr)
    result = controller.compact("hx-codex")
    if outcome == "success":
        assert result["action"] == "compacted"
    else:
        assert result["action"] == "compact-unknown"
        controller.sample["sampled_at"] = context._now().isoformat()
        assert controller.recover("hx-codex")["action"] == "recovered"
    assert reads


@pytest.mark.parametrize("operation", ["finish", "recover"])
def test_context_update_serializes_with_another_projects_lease_writer(controller, monkeypatch, operation):
    led = controller.ledger
    other_root = led.repo_root / "other-project"
    other = ledger.Ledger(repo_root=other_root, ledger_path=other_root / "ledger.json",
                          lock_path=other_root / "repo.lock", leases_path=led.leases_path)
    assert other.lock_path != led.lock_path
    assert other.leases_lock_path == led.leases_lock_path
    lease = {"agent": "hx-codex", "task_id": "t", "token": "token",
             "state": "compact-unknown",
             "requested_at": (context._now() - dt.timedelta(seconds=1)).isoformat()}
    led.mutate_leases(lambda data: data["sessions"].update(sid=lease))
    ledger._atomic_write_json(controller.guard_path, {"sid": {"tasks": {"t": {"count": 1}}}})
    context_loaded = threading.Event()
    release_context = threading.Event()
    other_attempting = threading.Event()
    other_loaded = threading.Event()
    original_load = led._load_leases
    original_other_load = other._load_leases

    def context_load():
        data = original_load()
        context_loaded.set()
        assert release_context.wait(3), "test must release the context writer"
        return data

    def other_load():
        other_loaded.set()
        return original_other_load()

    def write_other_project():
        other_attempting.set()
        other.mutate_leases(lambda data: data["sessions"].update(
            other_session={"agent": "other-worker", "state": "held"}))

    monkeypatch.setattr(led, "_load_leases", context_load)
    monkeypatch.setattr(other, "_load_leases", other_load)
    with ThreadPoolExecutor(max_workers=2) as pool:
        if operation == "finish":
            context_update = pool.submit(controller._finish, "sid", "token", "compact-unknown", True)
        else:
            context_update = pool.submit(controller.recover, "hx-codex")
        try:
            assert context_loaded.wait(3)
            other_update = pool.submit(write_other_project)
            assert other_attempting.wait(3)
            blocked_by_shared_lock = not other_loaded.wait(.2)
        finally:
            release_context.set()
        result = context_update.result(timeout=3)
        other_update.result(timeout=3)
    assert blocked_by_shared_lock, "the other project must wait for the context transaction"
    saved = json.loads(led.leases_path.read_text())
    assert saved["sessions"]["other_session"]["state"] == "held"
    if operation == "recover":
        assert result["action"] == "recovered"
        assert "sid" not in saved["sessions"]
    else:
        assert saved["sessions"]["sid"]["state"] == "compact-unknown"
