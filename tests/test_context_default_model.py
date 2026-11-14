import json
import sqlite3

import pytest

from hx import context, host, usage


def test_default_opencode_worker_has_a_measured_context_utilisation(tmp_path, monkeypatch):
    policy = usage.load_policy()
    model = policy['persistent_opencode_model']
    db = tmp_path / 'opencode.db'
    with sqlite3.connect(db) as conn:
        conn.execute('CREATE TABLE message (session_id TEXT, time_created INTEGER, data TEXT)')
        conn.execute('INSERT INTO message VALUES (?, ?, ?)', ('current', 1000, json.dumps({
            'role': 'assistant', 'modelID': model,
            'tokens': {'input': 125000, 'cache': {'read': 375000}},
        })))
    monkeypatch.setenv('HX_OPENCODE_DB', str(db))
    sample = context.telemetry(host.member({'agent': 'opencode', 'agent_session': {'value': 'current'}}), policy)
    assert sample['utilisation'] == pytest.approx(0.5)
    assert sample['session_id'] == 'current'

