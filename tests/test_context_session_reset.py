"""Use provider-session identity and measured rows, not in-flight zero counters."""

import datetime as dt
import json
import sqlite3

import pytest

from hx import __main__ as cli
from hx import context, ledger, usage


@pytest.fixture
def pane(tmp_path, monkeypatch):
    db = tmp_path / 'opencode.db'
    with sqlite3.connect(db) as conn:
        conn.execute('CREATE TABLE message (session_id TEXT, time_created INTEGER, data TEXT)')
    monkeypatch.setenv('HX_OPENCODE_DB', str(db))
    monkeypatch.setenv('HX_CONTEXT_RESETS_PATH', str(tmp_path / 'context-resets.json'))
    monkeypatch.setenv('HERDR_PANE_ID', 'sender-pane')
    monkeypatch.setattr(usage, '_herdr_agent_list', lambda: {'result': {'agents': [
        {'name': 'dev-codex', 'pane_id': 'sender-pane'},
        {'name': 'dev-opencode', 'pane_id': 'worker-pane'},
    ]}})
    policy = {'team_prefix': 'dev-', 'context_limits': {'model': 200000}}
    monkeypatch.setattr(usage, 'load_policy', lambda: policy)
    agent = {'name': 'dev-opencode', 'agent': 'opencode', 'agent_status': 'idle',
             'agent_session': {'value': 'old'}, 'cwd': str(tmp_path)}
    monkeypatch.setattr(context, 'agent_info', lambda name: dict(agent))
    led = ledger.Ledger(repo_root=tmp_path, ledger_path=tmp_path/'ledger.json',
                        lock_path=tmp_path/'lock', leases_path=tmp_path/'leases.json')
    controller = context.Controller(led, policy)
    monkeypatch.setattr(cli.context, 'Controller', lambda: controller)
    sent = []
    monkeypatch.setattr(cli.say.subprocess, 'run', lambda args, **kw: sent.append(args) or type(
        'Result', (), {'returncode': 0, 'stdout': '{"result": {}}'})())
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
    agent['agent_session'] = {'value': 'new'}
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
    agent['agent_session'] = {'value': 'new'}
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
    sid, sample = context.session_telemetry({'agent': 'claude', 'pane_id': 'pane'}, {})
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


def test_hx_reset_sends_new_then_records_it(pane, capsys):
    agent, append, sent = pane
    append('old', 598000)
    assert cli.main(['reset', 'dev-opencode']) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['action'] == 'reset-sent'
    assert result['command'] == '/new'
    assert result['session_id'] == 'old'
    assert sent == [['herdr', 'agent', 'prompt', 'dev-opencode', '/new']]
    assert cli.main(['say', 'opencode', 'first message of the new session']) == 0
    assert len(sent) == 2


def test_hx_reset_refuses_a_busy_agent(pane, capsys):
    agent, append, sent = pane
    agent['agent_status'] = 'working'
    assert cli.main(['reset', 'dev-opencode']) == 1
    assert sent == []
    assert 'not idle' in capsys.readouterr().err
