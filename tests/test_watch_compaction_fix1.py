"""Q-260 fix round 1: the review probes, kept as real regression tests."""
from __future__ import annotations

import copy
import datetime as dt
import json

import pytest

from hx import checkpoint, context, host, ledger


@pytest.fixture
def c(tmp_path, monkeypatch):
    for key in ("HC_TERMINAL_ID", "HERDR_PANE_ID", "HC_SCOPE", "HERDR_ENV"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HX_HERDR_PREFIX", "hx-")
    monkeypatch.setenv("HX_CONTEXT_RESETS_PATH", str(tmp_path / "resets.json"))
    led = ledger.Ledger(repo_root=tmp_path, ledger_path=tmp_path / "ledger.json",
                        leases_path=tmp_path / "leases.json",
                        lock_path=tmp_path / "lock")
    obj = context.Controller(led, {
        "team_prefix": "hx-", "lead_role": "lead",
        "context": {"pressure_threshold": .75, "boundary_threshold": .4,
                    "compact_wait_seconds": 0, "min_interval_min": 20},
    })
    obj.agent = host.member({"name": "hx-codex", "agent": "codex",
                             "agent_status": "idle", "agent_session": {"value": "sid"},
                             "cwd": str(tmp_path)})
    obj.sample = {"session_id": "sid", "utilisation": .9,
                  "sampled_at": (context._now() - dt.timedelta(seconds=10)).isoformat()}
    obj.calls = []
    monkeypatch.setattr(context, "agent_info", lambda name: copy.deepcopy(obj.agent))
    monkeypatch.setattr(context, "telemetry", lambda *args: dict(obj.sample))

    def send(member, command):
        obj.calls.append((member["name"], command, obj.agent["status"]))
        obj.sample.update(utilisation=.2, sampled_at=context._now().isoformat())
        return True

    monkeypatch.setattr(context, "_send_slash", send)
    return obj


def test_two_watchers_cannot_compact_twice_after_the_first_finishes(c, monkeypatch):
    other = context.Controller(c.ledger, copy.deepcopy(c.policy))
    reserve = c.ledger.mutate_leases_with_repo
    armed = True

    def interleave(callback):
        nonlocal armed
        if armed:
            armed = False
            assert other.compact_watch("hx-codex")["action"] == "compacted"
        return reserve(callback)

    monkeypatch.setattr(c.ledger, "mutate_leases_with_repo", interleave)
    c.compact_watch("hx-codex")
    assert len(c.calls) == 1, f"two watchers sent {c.calls}"


def test_a_sample_from_before_the_command_cannot_drive_the_next_decision(c):
    stale = (context._now() - dt.timedelta(seconds=1)).isoformat()
    assert c.compact_watch("hx-codex")["action"] == "compacted"
    c.sample.update(utilisation=.9, sampled_at=stale)
    result = c.decide("hx-codex")
    assert result["action"] == "keep" and "post-compaction" in result["reason"], result


def test_watcher_checkpoint_does_not_overwrite_a_real_task_checkpoint(c):
    task_id = "watch-hx-codex"
    c.ledger.add_task(task_id, "Real task", "hx-opencode", [], base_commit="synthetic")
    path = checkpoint.write_checkpoint(
        task_id, ledger=c.ledger, objective="Preserve real task work",
        decisions="Keep the selected design", errors="None",
        tests="Synthetic checks passed", next_action="Finish the actual work")
    original = path.read_text(encoding="utf-8")
    assert c.ledger.get_task(task_id)["checkpoint"] == str(path)
    assert c.compact_watch("hx-codex")["action"] == "compacted"
    assert path.read_text(encoding="utf-8") == original


def test_worker_that_becomes_busy_during_reservation_is_not_sent_keys(c, monkeypatch):
    mutate = c.ledger.mutate_leases

    def became_busy(callback):
        result = mutate(callback)
        sessions = c.ledger._load_leases().get("sessions", {})
        if sessions.get("sid", {}).get("phase") == "requested":
            c.agent["status"] = "working"
        return result

    monkeypatch.setattr(c.ledger, "mutate_leases", became_busy)
    c.compact_watch("hx-codex")
    assert c.calls == [], f"keys reached busy worker: {c.calls}"