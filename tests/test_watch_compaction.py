"""Watcher-owned compaction through the context controller."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from hx import context, host, ledger


@pytest.fixture
def controller(tmp_path, monkeypatch):
    monkeypatch.setenv("HX_HERDR_PREFIX", "hx-")
    led = ledger.Ledger(repo_root=tmp_path, ledger_path=tmp_path / "ledger.json",
                        leases_path=tmp_path / "leases.json",
                        lock_path=tmp_path / "lock")
    c = context.Controller(led, {
        "team_prefix": "hx-", "lead_role": "lead",
        "context": {"pressure_threshold": .75, "boundary_threshold": .4,
                    "verification_timeout_seconds": 0, "compact_wait_seconds": 0,
                    "min_interval_min": 0},
    })
    c.agent = host.member({"name": "hx-codex", "agent": "codex",
                           "agent_status": "idle", "agent_session": {"value": "sid"},
                           "cwd": str(tmp_path)})
    c.calls = []
    c.sample = {"session_id": "sid", "utilisation": .9,
                "sampled_at": context._now().isoformat()}
    monkeypatch.setattr(context, "agent_info", lambda name: copy.deepcopy(c.agent))
    monkeypatch.setattr(context, "telemetry", lambda *a: dict(c.sample))

    def send_slash(member, command):
        c.calls.append((member["name"], command))
        if command == "/compact":
            c.sample.update(utilisation=.2, sampled_at=context._now().isoformat())
        return True

    monkeypatch.setattr(context, "_send_slash", send_slash)
    return c


def test_watch_compaction_uses_an_owned_checkpoint_and_guard(controller):
    result = controller.compact_watch("hx-codex")
    assert result["action"] == "compacted"
    path = Path(result["watch_checkpoint"])
    proof = controller.sample["sampled_at"]
    assert path.parent.name == "watch-checkpoints"
    text = path.read_text(encoding="utf-8")
    assert "## Objective" in text and "task: none registered" in text
    assert controller.calls == [("hx-codex", "/compact")]
    assert json.loads(controller.ledger.leases_path.read_text())["sessions"] == {}
    guards = json.loads(controller.guard_path.read_text())
    assert guards["sid"]["tasks"]["watch"]["count"] == 1
    assert guards["sid"]["last_compacted_sample"] == proof


def test_stale_pre_compaction_sample_is_ignored(controller):
    before = controller.sample["sampled_at"]
    assert controller.compact_watch("hx-codex")["action"] == "compacted"
    controller.sample.update(utilisation=.9, sampled_at=before)
    result = controller.compact_watch("hx-codex")
    assert result["action"] == "keep"
    assert "post-compaction" in result["reason"]
    assert controller.calls == [("hx-codex", "/compact")]


def test_recent_watch_compaction_is_not_repeated(controller):
    assert controller.compact_watch("hx-codex")["action"] == "compacted"
    controller.policy["context"]["min_interval_min"] = 20
    controller.sample.update(utilisation=.9,
                             sampled_at=context._now().isoformat())
    result = controller.compact_watch("hx-codex")
    assert result["action"] == "keep"
    assert "minimum compaction interval" in result["reason"]
    assert controller.calls == [("hx-codex", "/compact")]


def test_unknown_watch_compaction_leaves_the_worker_held(controller, monkeypatch):
    monkeypatch.setattr(context, "_send_slash", lambda member, command: True)
    result = controller.compact_watch("hx-codex")
    assert result["action"] == "compact-unknown"
    sessions = json.loads(controller.ledger.leases_path.read_text())["sessions"]
    assert sessions["sid"]["state"] == "compact-unknown"
    assert "leased" in controller.decide("hx-codex")["reason"]
    guards = json.loads(controller.guard_path.read_text())
    assert guards["sid"]["tasks"]["watch"]["failed"] is True


def test_busy_worker_is_never_compacted(controller):
    controller.agent["status"] = "working"
    result = controller.compact_watch("hx-codex")
    assert result["action"] == "keep"
    assert "not idle" in result["reason"]
    assert controller.calls == []


def test_the_lead_is_never_compacted(controller):
    result = controller.compact_watch("hx-lead")
    assert result["action"] == "keep"
    assert "never compacts the lead" in result["reason"]
    assert controller.calls == []