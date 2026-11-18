"""Boundary decisions are durable, including refused and unknown recovery."""

import datetime as dt
import io
import json
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from hx import __main__ as cli
from hx import boundary as boundary_mod
from hx import checkpoint, context, devin as devin_mod, host, ledger, paths


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


@pytest.mark.parametrize('provider', ['devin', 'codex'])
def test_unregistered_pressure_has_actionable_recovery(boundary_controller, provider):
    controller, agent, sample = boundary_controller
    controller.ledger.set_status('one', 'done')
    agent.update(name='dev-pane', kind=provider)
    sample['utilisation'] = .89
    result = controller.boundary('dev-pane')
    assert result['action'] == 'compact-needed'
    assert result['disposition'] == 'recovery-needed'
    assert result['steps']
    assert 'checkpoint' in ' '.join(result['steps']).lower()


def test_cli_pane_boundary_records_without_executing_recovery(boundary_controller, capsys):
    controller, _, sample = boundary_controller
    sample['utilisation'] = .89
    assert cli.main(['boundary', 'dev-codex']) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['action'] == 'compact'
    assert result['disposition'] == 'recovery-needed'
    assert controller.boundary_path.exists()


def test_lead_guard_is_per_work_item(boundary_controller, monkeypatch):
    controller, agent, sample = boundary_controller
    agent.update(name='dev-claude', kind='claude')
    sample['utilisation'] = .8
    monkeypatch.setattr(context, '_lead_telemetry', lambda *args: dict(sample))
    controller.policy['context'] = {'min_interval_min': 0, 'max_per_task': 2}

    def confirm(item):
        ledger._atomic_write_json(controller.recovery_path, {'dev-claude': {
            'session_id': 'sid', 'utilisation': .9, 'work_item': item,
            'at': (context._now() - dt.timedelta(seconds=1)).isoformat()}})
        sample['sampled_at'] = context._now().isoformat()
        controller.self_compacted('dev-claude', 'sid', work_item=item)

    confirm('release')
    confirm('release')
    same = controller.decide('dev-claude', work_item='release')
    another = controller.decide('dev-claude', work_item='next-release')
    assert same['reason'] == 'task compaction loop guard'
    assert another['action'] == 'self-compact'
    assert another['work_item'] == 'next-release'
    assert '--work-item' in another['steps'][-1]


# --- worker boundary manifest (team/boundary.json) ---------------------------

_MANIFEST_FILES = [
    boundary_mod.MANIFEST_NAME,
    "policy.json",
    boundary_mod._OPENCODE_OVERLAY,
    "team/devin-worker.config.json",
    "team/devin-pane.config.json",
    "team/herdr-team.rules",
    "team/claude-settings.json",
]


def _copy_boundary_tree(root: Path, tmp_path: Path) -> Path:
    """A scratch copy of every config the manifest generates, plus the manifest."""
    clone = tmp_path / "tree"
    for rel in _MANIFEST_FILES:
        target = clone / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / rel, target)
    return clone


def test_manifest_check_report(capsys):
    """hx boundary check on this tree: prints the divergence report, passes on no drift."""
    out = io.StringIO()
    rc = boundary_mod.check(out=out)
    print("\n" + out.getvalue())
    assert rc == 0, out.getvalue()


def test_manifest_check_names_a_planted_deny(tmp_path):
    """One extra Exec rule in a copied config fails the check and names the file."""
    clone = _copy_boundary_tree(paths.package_root(), tmp_path)
    worker = clone / "team/devin-worker.config.json"
    config = json.loads(devin_mod._strip_json_comments(worker.read_text(encoding="utf-8")))
    config["permissions"]["deny"].insert(3, "Exec(planted drift)")
    worker.write_text(json.dumps(config, indent=2), encoding="utf-8")

    out = io.StringIO()
    rc = boundary_mod.check(root=clone, out=out)
    report = out.getvalue()
    print("\n" + report)
    assert rc == 1
    assert "team/devin-worker.config.json" in report
    assert "planted drift" in report


def test_manifest_check_names_a_planted_path_rule(tmp_path):
    """An extra Write() deny in a copied config is reported the same way."""
    clone = _copy_boundary_tree(paths.package_root(), tmp_path)
    pane = clone / "team/devin-pane.config.json"
    config = json.loads(devin_mod._strip_json_comments(pane.read_text(encoding="utf-8")))
    config["permissions"]["deny"].append("Write(**/secrets/**)")
    pane.write_text(json.dumps(config, indent=2), encoding="utf-8")

    out = io.StringIO()
    assert boundary_mod.check(root=clone, out=out) == 1
    report = out.getvalue()
    assert "team/devin-pane.config.json" in report
    assert "secrets" in report
