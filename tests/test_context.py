"""Protected compaction with fake Herdr and provider telemetry."""

import copy
import json
import sqlite3
import subprocess

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
    ({"cwd": "elsewhere"}, "worktree"),
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
