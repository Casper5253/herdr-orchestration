"""Use provider-session identity and measured rows, not in-flight zero counters."""

import datetime as dt
import json
import sqlite3

import pytest

from hx import __main__ as cli
from hx import context, host, ledger, usage


@pytest.fixture
def pane(tmp_path, monkeypatch):
    db = tmp_path / 'opencode.db'
    with sqlite3.connect(db) as conn:
        conn.execute('CREATE TABLE message (session_id TEXT, time_created INTEGER, data TEXT)')
    monkeypatch.setenv('HX_OPENCODE_DB', str(db))
    monkeypatch.setenv('HX_CONTEXT_RESETS_PATH', str(tmp_path / 'context-resets.json'))
    monkeypatch.setenv('HX_PROJECT_ROOT', str(tmp_path))
    monkeypatch.setenv('HERDR_ENV', '1')
    monkeypatch.setenv('HERDR_PANE_ID', 'sender-pane')
    monkeypatch.setattr(host.HerdrHost, 'all_members', lambda self: [host.member(a) for a in [
        {'name': 'dev-codex', 'pane_id': 'sender-pane'},
        {'name': 'dev-opencode', 'pane_id': 'worker-pane'},
    ]])
    policy = {'team_prefix': 'dev-', 'context_limits': {'model': 200000}}
    monkeypatch.setattr(usage, 'load_policy', lambda: policy)
    agent = host.member({'name': 'dev-opencode', 'agent': 'opencode', 'agent_status': 'idle',
                         'agent_session': {'value': 'old'}, 'cwd': str(tmp_path)})
    monkeypatch.setattr(context, 'agent_info', lambda name: dict(agent))
    led = ledger.Ledger(repo_root=tmp_path, ledger_path=tmp_path/'ledger.json',
                        lock_path=tmp_path/'lock', leases_path=tmp_path/'leases.json')
    controller = context.Controller(led, policy)
    monkeypatch.setattr(cli.context, 'Controller', lambda: controller)
    sent = []
    monkeypatch.setattr(host.subprocess, 'run', lambda args, **kw: sent.append(args) or type(
        'Result', (), {'returncode': 0, 'stdout': '{"result":{"type":"agent_prompted","agent":{"agent_status":"idle"}}}'})())
    created = int(context._now().timestamp()*1000) - 1000

    def append(sid, tokens, *, complete=True):
        nonlocal created
        created += 1
        data = {'role': 'assistant', 'modelID': 'model',
                'tokens': {'input': tokens, 'cache': {'read': 0}}}
        if complete:
            data['finish'] = 'stop'
        with sqlite3.connect(db) as conn:
            conn.execute('INSERT INTO message VALUES (?, ?, ?)',
                         (sid, created, json.dumps(data)))
    return agent, append, sent


def test_real_session_reset_clears_hold_for_both_cli_paths(pane, capsys):
    agent, append, sent = pane
    append('old', 598000)
    assert cli.main(['say', 'opencode', 'first assignment']) == 1
    assert sent == []
    assert '299%' in capsys.readouterr().err
    append('new', 1000)
    agent['session_id'] = 'new'
    assert cli.main(['context', 'decide', 'dev-opencode']) == 0
    decision = json.loads(capsys.readouterr().out)
    assert decision['session_id'] == decision['telemetry']['session_id'] == 'new'
    assert decision['telemetry']['utilisation'] == .005
    assert cli.main(['say', 'opencode', 'next assignment']) == 0
    assert len(sent) == 1
    assert 'Held:' not in capsys.readouterr().err


def test_same_session_placeholder_does_not_fake_a_reset(pane, capsys):
    _, append, sent = pane
    append('old', 598000)
    append('old', 0, complete=False)
    assert cli.main(['context', 'decide', 'dev-opencode']) == 0
    decision = json.loads(capsys.readouterr().out)
    assert decision['telemetry']['utilisation'] == 2.99
    assert cli.main(['say', 'opencode', 'next assignment']) == 1
    assert sent == []
    assert '299%' in capsys.readouterr().err


def test_empty_new_session_is_unknown_instead_of_reusing_old_pressure(pane, capsys):
    agent, append, sent = pane
    append('old', 598000)
    append('new', 0, complete=False)
    agent['session_id'] = 'new'
    assert cli.main(['context', 'decide', 'dev-opencode']) == 0
    decision = json.loads(capsys.readouterr().out)
    assert decision['telemetry']['session_id'] == 'new'
    assert decision['telemetry']['utilisation'] is None
    assert cli.main(['say', 'opencode', 'next assignment']) == 0
    assert len(sent) == 1


def test_session_lookup_is_pinned_during_a_telemetry_read(tmp_path, monkeypatch):
    monkeypatch.setenv('HX_STATE_DIR', str(tmp_path))
    looked_up = []

    def by_pane(pane_id):
        looked_up.append(pane_id)
        return 'first' if len(looked_up) == 1 else 'second'

    monkeypatch.setattr(context, '_claude_session_for_pane', by_pane)
    (tmp_path/'claude-status-first.json').write_text(json.dumps({
        'session_id': 'first', 'timestamp': context._now().isoformat(),
        'context_window': {'used_percentage': 20},
    }))
    sid, sample = context.session_telemetry(host.member({'agent': 'claude', 'pane_id': 'pane'}), {})
    assert sid == sample['session_id'] == 'first'
    assert sample['utilisation'] == .2
    assert looked_up == ['pane']


def test_recorded_reset_clears_the_hold_before_the_new_session_exists(pane, capsys):
    agent, append, sent = pane
    append('old', 598000)
    assert cli.main(['say', 'opencode', 'first assignment']) == 1
    assert sent == []
    assert '299%' in capsys.readouterr().err
    assert cli.main(['boundary', 'dev-opencode', '--reset-sent']) == 0
    recorded = json.loads(capsys.readouterr().out)
    assert recorded['action'] == 'keep'
    assert recorded['reset_pending'] is True
    assert recorded['reset']['session_id'] == 'old'
    assert cli.main(['say', 'opencode', 'start the new session']) == 0
    assert len(sent) == 1
    assert 'Held:' not in capsys.readouterr().err


def test_fake_reset_holds_again_after_the_grace_window(pane, capsys, monkeypatch):
    agent, append, sent = pane
    append('old', 598000)
    usage.load_policy()['context'] = {'reset_grace_min': 5}
    assert cli.main(['boundary', 'dev-opencode', '--reset-sent']) == 0
    recorded = json.loads(capsys.readouterr().out)
    reset_at = dt.datetime.fromisoformat(recorded['reset']['reset_at'])
    monkeypatch.setattr(context, '_now', lambda: reset_at + dt.timedelta(minutes=6))
    assert cli.main(['say', 'opencode', 'too late']) == 1
    assert sent == []
    assert '299%' in capsys.readouterr().err


def test_hx_reset_sends_new_then_records_it(pane, capsys, monkeypatch):
    agent, append, sent = pane
    append('old', 598000)
    slashes = []

    def send_slash(member, command):
        slashes.append((member["name"], command))
        agent["session_id"] = "new"
        return True
    monkeypatch.setattr(context, "_send_slash", send_slash)

    assert cli.main(['reset', 'dev-opencode']) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['action'] == 'reset-verified'
    assert result['command'] == '/new'
    assert result['session_id'] == 'old' and result['new_session_id'] == 'new'
    assert slashes == [('dev-opencode', '/new')]
    assert sent == []
    assert cli.main(['say', 'opencode', 'first message of the new session']) == 0
    assert len(sent) == 1


def test_hx_reset_without_a_new_session_is_unknown(pane, capsys, monkeypatch):
    agent, append, sent = pane
    append('old', 598000)
    monkeypatch.setattr(context, "_send_slash", lambda member, command: True)
    monkeypatch.setattr(
        context, "_now", lambda: context.dt.datetime.now(context.dt.timezone.utc))
    assert cli.main(['reset', 'dev-opencode']) == 1
    assert 'no new provider session' in capsys.readouterr().err


def test_hx_reset_refuses_a_busy_agent(pane, capsys):
    agent, append, sent = pane
    agent['status'] = 'working'
    assert cli.main(['reset', 'dev-opencode']) == 1
    assert sent == []
    assert 'not idle' in capsys.readouterr().err


def test_unverified_reset_keeps_reservation_and_blocks_resend(pane, capsys, monkeypatch, tmp_path):
    agent, append, sent = pane
    append('old', 598000)
    slashes = []
    monkeypatch.setattr(context, "_send_slash",
                        lambda member, command: slashes.append(command) or True)
    leases_path = tmp_path / 'leases.json'

    assert cli.main(['reset', 'dev-opencode']) == 1
    assert 'no new provider session' in capsys.readouterr().err
    # The durable unknown outcome keeps its reservation.
    leases = json.loads(leases_path.read_text())
    hold = leases["sessions"].get("reset:dev-opencode")
    assert hold is not None and hold["state"] == "reset-unknown"
    assert hold["session_id"] == "old"
    # A second call must not send keys again while the outcome is unknown.
    assert cli.main(['reset', 'dev-opencode']) == 1
    assert 'still unverified' in capsys.readouterr().err
    assert len(slashes) == 1
    # Once the provider session changes, the hold resolves instead of blocking.
    agent['session_id'] = 'new'
    assert cli.main(['reset', 'dev-opencode']) == 0
    assert json.loads(capsys.readouterr().out)["action"] == "reset-verified"
    assert "reset:dev-opencode" not in json.loads(leases_path.read_text())["sessions"]


def test_self_compacted_confirms_same_session_lower_telemetry(tmp_path, monkeypatch):
    led = ledger.Ledger(repo_root=tmp_path, ledger_path=tmp_path / 'ledger.json',
                        lock_path=tmp_path / 'lock', leases_path=tmp_path / 'leases.json')
    controller = context.Controller(led, {"team_prefix": "dev-", "lead_role": "lead"})
    member = host.member({"name": "dev-lead", "agent": "codex",
                          "agent_status": "idle",
                          "agent_session": {"value": "sid"},
                          "pane_id": "t23", "cwd": str(tmp_path)})
    monkeypatch.setattr(context, "agent_info", lambda name: dict(member))
    sample = {"session_id": "sid", "utilisation": .9,
              "sampled_at": context._now().isoformat()}
    monkeypatch.setattr(context, "telemetry", lambda agent, policy: dict(sample))
    pending = tmp_path / 'lead-recovery.json'
    pending.write_text(json.dumps({"dev-lead": {"session_id": "sid",
                                                "utilisation": .9,
                                                "work_item": "w1",
                                                "at": (context._now() - dt.timedelta(seconds=1)).isoformat()}}),
                       encoding="utf-8")
    # A different session proves nothing about this compaction.
    with pytest.raises(ValueError, match="same provider session"):
        controller.self_compacted("dev-lead", "other", work_item="w1")
    assert json.loads(pending.read_text()).get("dev-lead")
    # Same session with fresh lower telemetry completes the confirmation.
    sample.update(utilisation=.2, sampled_at=context._now().isoformat())
    assert controller.self_compacted("dev-lead", "sid", work_item="w1")["action"] == "self-compacted"
    assert json.loads(pending.read_text()).get("dev-lead") is None
    # Without a pending compaction there is nothing to confirm.
    with pytest.raises(ValueError, match="pending"):
        controller.self_compacted("dev-lead", "sid", work_item="w1")


def test_a_failed_reset_send_marks_the_hold_recoverable(pane, capsys, monkeypatch, tmp_path):
    agent, append, sent = pane
    append('old', 598000)
    monkeypatch.setattr(context, "_send_slash", lambda member, command: False)
    leases_path = tmp_path / 'leases.json'

    assert cli.main(['reset', 'dev-opencode']) == 1
    assert 'did not accept' in capsys.readouterr().err
    hold = json.loads(leases_path.read_text())["sessions"].get("reset:dev-opencode")
    assert hold is not None and hold["state"] == "reset-unknown"

    # Pre-request telemetry cannot prove that failed keys have settled.
    assert cli.context.Controller().recover("dev-opencode")["action"] == "keep"
    monkeypatch.setattr(context, "telemetry", lambda *a: {
        "session_id": "old", "utilisation": .7,
        "sampled_at": context._now().isoformat(),
    })
    out = cli.context.Controller().recover("dev-opencode")
    assert out["action"] == "recovered"
    assert "reset:dev-opencode" not in json.loads(leases_path.read_text())["sessions"]


def test_refused_lead_decide_writes_no_pending_recovery(tmp_path, monkeypatch):
    led = ledger.Ledger(repo_root=tmp_path, ledger_path=tmp_path / 'ledger.json',
                        lock_path=tmp_path / 'lock', leases_path=tmp_path / 'leases.json')
    controller = context.Controller(led, {"team_prefix": "dev-", "lead_role": "lead"})
    member = host.member({"name": "dev-lead", "agent": "codex",
                          "agent_status": "idle",
                          "agent_session": {"value": "old"},
                          "pane_id": "t23", "cwd": str(tmp_path)})
    monkeypatch.setattr(context, "agent_info", lambda name: dict(member))
    monkeypatch.setattr(context, "telemetry",
                        lambda agent, policy: {"session_id": "old", "utilisation": .3,
                                               "sampled_at": context._now().isoformat()})
    try:
        controller._decide_lead({"agent": "dev-lead"}, member, "old", None, "w1")
    except Exception as exc:
        assert "threshold" in str(exc) or "Retain" in type(exc).__name__
    pending = controller.recovery_path
    assert not pending.exists() or "dev-lead" not in json.loads(pending.read_text())
