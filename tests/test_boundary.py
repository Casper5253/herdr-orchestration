"""Boundary decisions are durable, including refused and unknown recovery."""

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from hx import __main__ as cli
from hx import checkpoint, context, host, ledger


@pytest.fixture
def boundary_controller(tmp_path, monkeypatch):
    led = ledger.Ledger(repo_root=tmp_path, ledger_path=tmp_path / 'ledger.json',
                        leases_path=tmp_path / 'leases.json', lock_path=tmp_path / 'ledger.lock')
    led.add_task('one', 'First item', 'dev-codex', ['owned.py'], base_commit='base')
    led.set_status('one', 'done')
    controller = context.Controller(led, {'team_prefix': 'dev-', 'lead_role': 'claude'})
    agent = host.member({'name': 'dev-codex', 'agent': 'codex', 'agent_status': 'idle',
                         'agent_session': {'value': 'sid'}, 'cwd': str(tmp_path)})
    sample = {'session_id': 'sid', 'utilisation': .2, 'sampled_at': context._now().isoformat()}
    monkeypatch.setattr(context, 'agent_info', lambda name: dict(agent))
    monkeypatch.setattr(context, 'telemetry', lambda *args: dict(sample))
    monkeypatch.setattr(checkpoint, '_run_git', lambda *args: (0, '', ''))
    monkeypatch.setattr(cli.context, 'Controller', lambda *args, **kwargs: controller)
    return controller, agent, sample


@pytest.mark.parametrize('case, disposition', [
    ('low', 'keep'), ('busy', 'defer'), ('unknown', 'unknown'),
    ('dirty', 'refused'), ('pressure', 'recovery-needed'),
])
def test_boundary_records_every_disposition(boundary_controller, monkeypatch, case, disposition):
    controller, agent, sample = boundary_controller
    if case == 'busy':
        agent['status'] = 'working'
    elif case == 'unknown':
        sample['utilisation'] = None
    elif case == 'dirty':
        monkeypatch.setattr(controller, '_dirty_paths', lambda path: {'outside-owned-files'})
    elif case == 'pressure':
        sample['utilisation'] = .89
    result = controller.boundary('dev-codex', 'one')
    records = [json.loads(line) for line in controller.boundary_path.read_text().splitlines()]
    assert len(records) == 1
    assert records[0] == result
    assert result['disposition'] == disposition
    assert result['agent'] == 'dev-codex'
    assert result['task_id'] == 'one'
    assert result['source'] == 'boundary'
    assert result['recorded_at']


def test_boundary_appends_instead_of_overwriting(boundary_controller):
    controller, _, sample = boundary_controller
    first = controller.boundary('dev-codex', 'one')
    sample['utilisation'] = .89
    second = controller.boundary('dev-codex', 'one')
    records = [json.loads(line) for line in controller.boundary_path.read_text().splitlines()]
    assert records == [first, second]


def test_concurrent_boundaries_keep_complete_records(boundary_controller):
    controller, _, _ = boundary_controller
    with ThreadPoolExecutor(max_workers=4) as workers:
        results = list(workers.map(lambda _: controller.boundary('dev-codex', 'one'), range(8)))
    records = [json.loads(line) for line in controller.boundary_path.read_text().splitlines()]
    assert len(records) == 8
    assert {row['event_id'] for row in records} == {row['event_id'] for row in results}


def test_invalid_telemetry_still_has_a_valid_json_record(boundary_controller):
    controller, _, sample = boundary_controller
    sample['utilisation'] = float('nan')
    result = controller.boundary('dev-codex')
    assert result['disposition'] == 'unknown'
    assert result['telemetry']['utilisation'] is None
    assert 'NaN' not in controller.boundary_path.read_text()


def test_journal_failure_is_not_a_silent_success(boundary_controller):
    controller, _, _ = boundary_controller
    controller.boundary_path.mkdir(parents=True)
    with pytest.raises(OSError):
        controller.boundary('dev-codex')


@pytest.mark.parametrize('provider, action', [('devin', 'clear-needed'), ('codex', 'compact-needed')])
def test_unregistered_pressure_has_actionable_recovery(boundary_controller, provider, action):
    controller, agent, sample = boundary_controller
    controller.ledger.set_status('one', 'done')
    agent.update(name='dev-pane', kind=provider)
    sample['utilisation'] = .89
    result = controller.boundary('dev-pane')
    assert result['action'] == action
    assert result['disposition'] == 'recovery-needed'
    assert result['steps']
    assert 'checkpoint' in ' '.join(result['steps']).lower() or 'handoff' in ' '.join(result['steps']).lower()


def test_cli_pane_boundary_records_without_executing_recovery(boundary_controller, capsys):
    controller, _, sample = boundary_controller
    sample['utilisation'] = .89
    assert cli.main(['boundary', 'dev-codex']) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['action'] == 'compact-needed'
    assert result['disposition'] == 'recovery-needed'
    assert controller.boundary_path.exists()


def test_lead_guard_is_per_work_item(boundary_controller, monkeypatch):
    controller, agent, sample = boundary_controller
    agent.update(name='dev-claude', kind='claude')
    sample['utilisation'] = .8
    monkeypatch.setattr(context, '_lead_telemetry', lambda *args: dict(sample))
    controller.policy['context'] = {'min_interval_min': 0, 'max_per_task': 2}
    controller.self_compacted('dev-claude', 'sid', work_item='release')
    controller.self_compacted('dev-claude', 'sid', work_item='release')
    same = controller.decide('dev-claude', work_item='release')
    another = controller.decide('dev-claude', work_item='next-release')
    assert same['reason'] == 'task compaction loop guard'
    assert another['action'] == 'self-compact'
    assert another['work_item'] == 'next-release'
    assert '--work-item' in another['steps'][-1]
