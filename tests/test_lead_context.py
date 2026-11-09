"""Claude lead decisions use session-bound status files and explicit confirmation."""

import copy
import datetime as dt
import json

import pytest

from hx import __main__ as cli
from hx import context, host, ledger, usage


@pytest.fixture
def lead(tmp_path, monkeypatch):
    monkeypatch.setenv("HX_STATE_DIR", str(tmp_path))
    led = ledger.Ledger(repo_root=tmp_path, ledger_path=tmp_path / "ledger.json",
                        leases_path=tmp_path / "leases.json", lock_path=tmp_path / "lock")
    c = context.Controller(led, {
        "team_prefix": "test-", "lead_role": "claude", "context_limits": {"claude-lead": 400000},
        "context": {"claude_lead_threshold": .6, "max_per_task": 2, "min_interval_min": 20},
    })
    c.agent = host.member({"name": "test-claude", "agent": "claude", "agent_status": "working",
                           "agent_session": {"value": "lead-sid"}})
    c.status = {"session_id": "lead-sid", "timestamp": context._now().isoformat(),
                "context_window": {"context_window_size": 1000000, "used_percentage": 25,
                                   "current_usage": {"input_tokens": 10000,
                                                     "cache_creation_input_tokens": 20000,
                                                     "cache_read_input_tokens": 220000,
                                                     "output_tokens": 5000}}}
    c.path = tmp_path / "claude-status-lead-sid.json"
    c.path.write_text(json.dumps(c.status), encoding="utf-8")
    c.checkpoint = tmp_path / ".hx" / "checkpoints" / "lead.md"
    c.checkpoint.parent.mkdir(parents=True)
    c.checkpoint.write_text("## Objective\nFinish this release.\n", encoding="utf-8")
    monkeypatch.setattr(context, "agent_info", lambda name: copy.deepcopy(c.agent))
    monkeypatch.setattr(cli.context, "Controller", lambda: c)
    return c


@pytest.mark.parametrize("tokens,action", [(239999, "keep"), (240000, "self-compact"), (300000, "self-compact")])
def test_lead_threshold_uses_tokens_and_effective_limit(lead, tokens, action):
    lead.status["context_window"]["current_usage"] = {"input_tokens": tokens}
    lead.path.write_text(json.dumps(lead.status))
    decision = lead.decide("test-claude")
    assert decision["action"] == action
    assert decision["telemetry"]["utilisation"] == tokens / 400000
    assert lead.ledger.list_tasks() == []
    assert not lead.guard_path.exists()


def test_lead_steps_and_repeated_decisions_do_not_burn_guard(lead):
    result = lead.decide("test-claude")
    assert result["action"] == "self-compact"
    assert result["task_id"] == "lead"
    assert result["steps"][0].startswith("hx checkpoint write lead ")
    assert "Start-Process powershell.exe -WindowStyle Hidden" in result["steps"][1]
    assert "Start-Sleep 60; herdr agent prompt" in result["steps"][1]
    assert "/compact" in result["steps"][1]
    assert "End the turn" in result["steps"][2]
    assert "hx context self-compacted 'test-claude' --session 'lead-sid'" in result["steps"][3]
    assert lead.decide("test-claude")["action"] == "self-compact"
    assert not lead.guard_path.exists()


@pytest.mark.parametrize("case", ["missing", "stale", "wrong-session", "future", "invalid", "missing-counts"])
def test_lead_requires_fresh_session_telemetry(lead, case):
    if case == "missing":
        lead.path.unlink()
    else:
        if case == "stale":
            lead.status["timestamp"] = (context._now() - dt.timedelta(hours=2)).isoformat()
        elif case == "future":
            lead.status["timestamp"] = (context._now() + dt.timedelta(hours=2)).isoformat()
        elif case == "wrong-session":
            lead.status["session_id"] = "other"
        elif case == "missing-counts":
            lead.status["context_window"] = {"used_percentage": 90}
        lead.path.write_text("invalid" if case == "invalid" else json.dumps(lead.status))
    result = lead.decide("test-claude")
    assert result["action"] == "keep"
    assert "telemetry unknown or stale" in result["reason"]


def test_lead_without_current_usage_can_convert_native_percentage(lead):
    del lead.status["context_window"]["current_usage"]
    lead.path.write_text(json.dumps(lead.status))
    result = lead.decide("test-claude")
    assert result["action"] == "self-compact"
    assert result["telemetry"]["utilisation"] == .625


@pytest.mark.parametrize("current", [{}, {"input_tokens": None}, {"input_tokens": float("nan")},
                                     {"input_tokens": 300000, "cache_read_input_tokens": -1}])
def test_invalid_token_counters_never_trigger_compaction(lead, current):
    lead.status["context_window"]["current_usage"] = current
    lead.path.write_text(json.dumps(lead.status))
    result = lead.decide("test-claude")
    assert result["action"] == "keep"
    assert result["reason"] == "session telemetry unknown or stale"


def test_missing_session_never_chooses_newest_status_file(lead):
    del lead.agent["session_id"]
    result = lead.decide("test-claude")
    assert result["action"] == "keep"
    assert "--session" in result["reason"]
    assert result["session_id"] == ""


def test_cli_session_fallback_and_herdr_precedence(lead, capsys):
    assert cli.main(["context", "decide", "test-claude", "--session", "other"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["action"] == "self-compact"
    assert result["session_id"] == "lead-sid"
    del lead.agent["session_id"]
    assert cli.main(["context", "decide", "test-claude", "--session", "lead-sid"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["action"] == "self-compact"
    assert result["session_id"] == "lead-sid"


def test_cli_confirmation_records_guard_and_interval(lead, capsys):
    assert cli.main(["context", "self-compacted", "test-claude", "--session", "lead-sid"]) == 0
    assert json.loads(capsys.readouterr().out)["action"] == "self-compacted"
    guard = json.loads(lead.guard_path.read_text())["lead-sid"]
    assert guard["tasks"][lead._lead_work_item(None, None)]["count"] == 1
    assert context._timestamp(guard["last_at"]) is not None
    decision = lead.decide("test-claude")
    assert decision["action"] == "keep"
    assert decision["reason"] == "minimum compaction interval"


def test_confirmation_works_without_herdr_session(lead):
    del lead.agent["session_id"]
    assert lead.self_compacted("test-claude", "lead-sid")["action"] == "self-compacted"
    assert lead.decide("test-claude", session_id="lead-sid")["action"] == "keep"


def test_confirmation_preserves_worker_guard_and_leases(lead, monkeypatch):
    other = {"last_at": "2026-01-01T00:00:00+00:00", "tasks": {"work": {"count": 1}}}
    ledger._atomic_write_json(lead.guard_path, {"worker-sid": other})
    original_write = ledger._atomic_write_json

    def locked_write(path, data):
        assert lead.ledger.leases_lock_path.exists()
        original_write(path, data)

    monkeypatch.setattr(ledger, "_atomic_write_json", locked_write)
    lead.self_compacted("test-claude", "lead-sid")
    assert json.loads(lead.guard_path.read_text())["worker-sid"] == other
    assert not lead.ledger.leases_path.exists()


def test_lead_max_per_task_guard_after_interval(lead, monkeypatch):
    now = context._now()
    lead.self_compacted("test-claude", "lead-sid")
    monkeypatch.setattr(context, "_now", lambda: now + dt.timedelta(minutes=21))
    assert lead.decide("test-claude")["action"] == "self-compact"
    lead.self_compacted("test-claude", "lead-sid")
    monkeypatch.setattr(context, "_now", lambda: now + dt.timedelta(minutes=42))
    assert lead.decide("test-claude")["reason"] == "task compaction loop guard"


def test_lead_changed_checkpoint_objective_starts_a_new_work_item(lead, monkeypatch):
    lead.policy['context']['min_interval_min'] = 0
    first = lead.decide('test-claude')['work_item']
    lead.self_compacted('test-claude', 'lead-sid')
    lead.self_compacted('test-claude', 'lead-sid')
    assert lead.decide('test-claude')['reason'] == 'task compaction loop guard'
    lead.checkpoint.write_text('## Objective\nShip the next release.\n')
    decision = lead.decide('test-claude')
    assert decision['action'] == 'self-compact'
    assert decision['work_item'] != first


def test_lead_requires_a_work_item_instead_of_a_session_wide_default(lead):
    lead.checkpoint.unlink()
    result = lead.decide('test-claude')
    assert result['disposition'] == 'refused'
    assert '--work-item' in result['reason']
    assert lead.decide('test-claude', work_item='release')['action'] == 'self-compact'


def test_lead_task_boundary_preserves_the_completed_task_id(lead):
    result = lead.boundary('test-claude', 'release-task')
    assert result['task_id'] == result['work_item'] == 'release-task'


@pytest.mark.parametrize("change,sid", [({}, "wrong"), ({"kind": "codex"}, "lead-sid"), ({}, "")])
def test_confirmation_rejects_wrong_session_or_provider(lead, change, sid):
    lead.agent.update(change)
    with pytest.raises(ValueError):
        lead.self_compacted("test-claude", sid)
    assert not lead.guard_path.exists()


def test_policy_declares_lead_threshold():
    policy = usage.load_policy()
    assert policy["context_limits"]["claude-lead"] == 400000
    assert policy["context"]["claude_lead_threshold"] == .6


def test_a_claude_worker_is_not_treated_as_the_lead(lead):
    lead.agent = {**lead.agent, "name": "test-haiku"}
    decision = lead.decide("test-haiku")
    assert decision["action"] == "keep"
    assert decision["reason"] == "agent is not idle"
    assert "steps" not in decision


def test_the_lead_role_comes_from_policy(lead):
    lead.policy = {**lead.policy, "lead_role": "opus"}
    lead.agent = {**lead.agent, "name": "test-opus"}
    assert lead.decide("test-opus")["action"] == "self-compact"
    assert "steps" not in lead.decide("test-claude")


def test_claude_session_is_found_by_pane_when_herdr_has_none(tmp_path, monkeypatch):
    monkeypatch.setenv("HX_STATE_DIR", str(tmp_path))
    for sid, pane, stamp in [("old", "wA:p3", "2026-10-18T08:00:00+00:00"), ("new", "wA:p3", "2026-10-18T09:00:00+00:00"), ("other", "wA:p4", "2026-10-18T10:00:00+00:00")]:
        (tmp_path / f"claude-status-{sid}.json").write_text(json.dumps({"session_id": sid, "herdr_pane_id": pane, "timestamp": stamp}), encoding="utf-8")
    assert context._session(host.member({"agent": "claude", "pane_id": "wA:p3"})) == "new"
    assert context._session(host.member({"agent": "claude", "pane_id": "wA:p9"})) == ""
    assert context._session(host.member({"agent": "codex", "pane_id": "wA:p3"})) == ""
    assert context._session(host.member({"agent": "claude", "pane_id": "wA:p3", "agent_session": {"value": "herdr-sid"}})) == "herdr-sid"


def test_a_worker_in_herdr_done_state_counts_as_ready(lead):
    lead.agent = {**lead.agent, "name": "test-haiku", "status": "done"}
    assert lead.decide("test-haiku")["reason"] != "agent is not idle"
