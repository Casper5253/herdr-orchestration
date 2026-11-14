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
                "context_window": {"context_window_size": 1000000, "used_percentage": 63,
                                   "current_usage": {"input_tokens": 390000,
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


def _set_lead_usage(lead, tokens, when):
    """A fresh status file at `when` carrying `tokens` of current usage."""
    lead.status["timestamp"] = when.isoformat()
    lead.status["context_window"]["current_usage"] = {"input_tokens": tokens}
    lead.path.write_text(json.dumps(lead.status))


def _confirm_compaction(lead, monkeypatch, when, tokens=20000, session="lead-sid"):
    """Same-session compaction proof: the status file drops below the pending
    utilisation, then `self_compacted` confirms it."""
    monkeypatch.setattr(context, "_now", lambda: when)
    _set_lead_usage(lead, tokens, when)
    return lead.self_compacted("test-claude", session)


@pytest.mark.parametrize("tokens,action", [(599999, "keep"), (600000, "self-compact"), (700000, "self-compact")])
def test_lead_threshold_uses_tokens_and_reported_window(lead, tokens, action):
    lead.status["context_window"]["current_usage"] = {"input_tokens": tokens}
    lead.path.write_text(json.dumps(lead.status))
    decision = lead.decide("test-claude")
    assert decision["action"] == action
    assert decision["telemetry"]["utilisation"] == pytest.approx(tokens / 1000000)
    assert lead.ledger.list_tasks() == []
    assert not lead.guard_path.exists()


def test_lead_utilisation_uses_the_window_the_statusline_reports(lead):
    """Q-273: 630K of a reported 1M window is 63%, not 157% of the 400K fallback."""
    lead.status["context_window"]["current_usage"] = {"input_tokens": 630000}
    lead.path.write_text(json.dumps(lead.status), encoding="utf-8")
    decision = lead.decide("test-claude")
    assert decision["telemetry"]["utilisation"] == pytest.approx(0.63)
    assert decision["action"] == "self-compact"


@pytest.mark.parametrize("reported", [
    pytest.param(None, id="absent"),
    pytest.param(0, id="zero"),
    pytest.param(-1, id="negative"),
    pytest.param("not a number", id="text"),
    pytest.param(10**400, id="overflowing-int"),
    pytest.param(1e400, id="infinite-float"),
])
def test_lead_falls_back_to_the_policy_limit_without_a_usable_window(lead, reported):
    window = lead.status["context_window"]
    if reported is None:
        window.pop("context_window_size", None)
    else:
        window["context_window_size"] = reported
    window["current_usage"] = {"input_tokens": 240000}
    lead.path.write_text(json.dumps(lead.status), encoding="utf-8")
    decision = lead.decide("test-claude")
    assert decision["telemetry"]["utilisation"] == pytest.approx(0.6)
    assert decision["action"] == "self-compact"


@pytest.mark.parametrize("reported", [
    pytest.param(10**400, id="overflowing-int"),
    pytest.param(1e400, id="infinite-float"),
])
def test_lead_percentage_branch_falls_back_for_an_overflowing_window(lead, reported):
    lead.status["context_window"] = {"context_window_size": reported, "used_percentage": 60}
    lead.path.write_text(json.dumps(lead.status), encoding="utf-8")
    decision = lead.decide("test-claude")
    assert decision["telemetry"]["utilisation"] == pytest.approx(0.6)
    assert decision["action"] == "self-compact"


def test_lead_steps_and_repeated_decisions_do_not_burn_guard(lead):
    result = lead.decide("test-claude")
    assert result["action"] == "self-compact"
    assert result["task_id"] == "lead"
    assert result["steps"][0].startswith("hx checkpoint write lead ")
    assert "Start-Process powershell.exe -WindowStyle Hidden" in result["steps"][1]
    assert "Start-Sleep 60; hx compact" in result["steps"][1]
    assert "--quick" in result["steps"][1]
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
    assert result["telemetry"]["utilisation"] == pytest.approx(.63)


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


def test_cli_confirmation_records_guard_and_interval(lead, capsys, monkeypatch):
    lead.decide("test-claude")
    with pytest.raises(ValueError, match="unproven"):
        lead.self_compacted("test-claude", "lead-sid")  # telemetry did not drop
    later = context._now() + dt.timedelta(seconds=2)
    monkeypatch.setattr(context, "_now", lambda: later)
    _set_lead_usage(lead, 20000, later)
    assert cli.main(["context", "self-compacted", "test-claude", "--session", "lead-sid"]) == 0
    assert json.loads(capsys.readouterr().out)["action"] == "self-compacted"
    guard = json.loads(lead.guard_path.read_text())["test-claude"]
    assert guard["tasks"][lead._lead_work_item(None, None)]["count"] == 1
    assert context._timestamp(guard["last_at"]) is not None
    decision = lead.decide("test-claude")
    assert decision["action"] == "keep"
    assert decision["reason"] == "minimum compaction interval"


def test_confirmation_works_without_herdr_session(lead, monkeypatch):
    del lead.agent["session_id"]
    lead.decide("test-claude", session_id="lead-sid")
    later = context._now() + dt.timedelta(seconds=2)
    _confirm_compaction(lead, monkeypatch, later)
    assert lead.decide("test-claude", session_id="lead-sid")["action"] == "keep"


def test_confirmation_preserves_worker_guard_and_leases(lead, monkeypatch):
    other = {"last_at": "2026-01-01T00:00:00+00:00", "tasks": {"work": {"count": 1}}}
    ledger._atomic_write_json(lead.guard_path, {"worker-sid": other})
    requested = context._now() - dt.timedelta(seconds=1)
    ledger._atomic_write_json(lead.recovery_path, {"test-claude": {
        "session_id": "lead-sid", "utilisation": .625, "work_item": "w",
        "at": requested.isoformat()}})
    original_write = ledger._atomic_write_json

    def locked_write(path, data):
        assert lead.ledger.leases_lock_path.exists()
        original_write(path, data)

    monkeypatch.setattr(ledger, "_atomic_write_json", locked_write)
    later = context._now() + dt.timedelta(seconds=2)
    _confirm_compaction(lead, monkeypatch, later)
    assert json.loads(lead.guard_path.read_text())["worker-sid"] == other
    assert not lead.ledger.leases_path.exists()


def test_lead_max_per_task_guard_after_interval(lead, monkeypatch):
    now = context._now()
    lead.decide("test-claude")
    _confirm_compaction(lead, monkeypatch, now + dt.timedelta(seconds=2))
    t2 = now + dt.timedelta(minutes=21)
    monkeypatch.setattr(context, "_now", lambda: t2)
    _set_lead_usage(lead, 650000, t2)
    assert lead.decide("test-claude")["action"] == "self-compact"
    _confirm_compaction(lead, monkeypatch, t2 + dt.timedelta(seconds=2))
    t4 = t2 + dt.timedelta(minutes=21)
    monkeypatch.setattr(context, "_now", lambda: t4)
    _set_lead_usage(lead, 650000, t4)
    assert lead.decide("test-claude")["reason"] == "task compaction loop guard"


def test_lead_changed_checkpoint_objective_starts_a_new_work_item(lead, monkeypatch):
    lead.policy['context']['min_interval_min'] = 0
    now = context._now()
    first = lead.decide('test-claude')['work_item']
    t1 = now + dt.timedelta(seconds=2)
    _confirm_compaction(lead, monkeypatch, t1)
    t2 = t1 + dt.timedelta(seconds=2)
    monkeypatch.setattr(context, "_now", lambda: t2)
    _set_lead_usage(lead, 650000, t2)
    lead.decide('test-claude')
    t3 = t2 + dt.timedelta(seconds=2)
    _confirm_compaction(lead, monkeypatch, t3)
    t4 = t3 + dt.timedelta(seconds=2)
    monkeypatch.setattr(context, "_now", lambda: t4)
    _set_lead_usage(lead, 650000, t4)
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


def test_self_compacted_binds_the_confirmation_to_the_pending_work_item(lead, monkeypatch):
    """Round-2 review finding 2: a confirmation under a different work item is
    refused and leaves the pending record and every guard untouched; omitting
    --work-item confirms under the item the compaction was decided for."""
    lead.decide("test-claude")
    pending = json.loads(lead.recovery_path.read_text())["test-claude"]
    later = context._now() + dt.timedelta(seconds=2)
    monkeypatch.setattr(context, "_now", lambda: later)
    _set_lead_usage(lead, 20000, later)
    with pytest.raises(ValueError, match="work.item"):
        lead.self_compacted("test-claude", "lead-sid", work_item="other-work")
    assert json.loads(lead.recovery_path.read_text())["test-claude"] == pending
    assert not lead.guard_path.exists()
    confirmed = lead.self_compacted("test-claude", "lead-sid")
    assert confirmed["action"] == "self-compacted"
    assert confirmed["work_item"] == pending["work_item"]
    guard = json.loads(lead.guard_path.read_text())["test-claude"]
    assert guard["tasks"] == {pending["work_item"]: {"count": 1}}
    assert json.loads(lead.recovery_path.read_text()).get("test-claude") is None


def test_self_compacted_needs_the_pending_session_and_a_lower_sample(lead):
    with pytest.raises(ValueError, match="pending"):
        lead.self_compacted("test-claude", "lead-sid")
    lead.decide("test-claude")
    with pytest.raises(ValueError, match="same provider session"):
        lead.self_compacted("test-claude", "other-session")
    with pytest.raises(ValueError, match="unproven"):
        lead.self_compacted("test-claude", "lead-sid")  # telemetry did not drop
    assert json.loads(lead.recovery_path.read_text())["test-claude"]
    assert not lead.guard_path.exists()


@pytest.mark.parametrize("kind", ["codex", "opencode"])
def test_decide_lead_recommends_compact_for_reset_providers(lead, monkeypatch, kind):
    """Review finding 1: a codex or opencode lead gets /compact, never /clear."""
    lead.agent = host.member({"name": "test-claude", "agent": kind,
                              "agent_status": "idle",
                              "agent_session": {"value": "lead-sid"}})
    monkeypatch.setattr(context, "session_telemetry", lambda agent, policy: (
        "lead-sid", {"session_id": "lead-sid", "utilisation": .9,
                     "sampled_at": context._now().isoformat()}))
    decision = lead.decide("test-claude")
    assert decision["action"] == "self-compact"
    text = json.dumps(decision["steps"])
    assert "/compact" in text
    assert "/clear" not in text and "/new" not in text and "<new-session-id>" not in text
    pending = json.loads(lead.recovery_path.read_text())["test-claude"]
    assert pending["session_id"] == "lead-sid"
    assert pending["utilisation"] == .9


@pytest.mark.parametrize("kind", ["codex", "opencode"])
def test_self_compacted_confirms_same_session_lower_telemetry(lead, monkeypatch, kind):
    """Review finding 1: confirmation accepts the same session id at lower
    utilisation; a different id proves nothing."""
    lead.agent = host.member({"name": "test-claude", "agent": kind,
                              "agent_status": "idle",
                              "agent_session": {"value": "lead-sid"}})
    sample = {"session_id": "lead-sid", "utilisation": .9,
              "sampled_at": context._now().isoformat()}
    monkeypatch.setattr(context, "telemetry", lambda agent, policy: dict(sample))
    monkeypatch.setattr(context, "session_telemetry", lambda agent, policy: ("lead-sid", dict(sample)))
    lead.decide("test-claude")
    with pytest.raises(ValueError, match="same provider session"):
        lead.self_compacted("test-claude", "other-session")
    later = context._now() + dt.timedelta(seconds=2)
    monkeypatch.setattr(context, "_now", lambda: later)
    sample.update(utilisation=.1, sampled_at=later.isoformat())
    assert lead.self_compacted("test-claude", "lead-sid")["action"] == "self-compacted"
    assert json.loads(lead.recovery_path.read_text()).get("test-claude") is None


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
