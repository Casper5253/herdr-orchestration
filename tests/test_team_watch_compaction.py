"""hx team watch auto-compacts only eligible idle workers."""
from __future__ import annotations

import json

from hx import host, say, team_watch


def _member(name, status="idle", address="t1", turn=7):
    team, role = name.rsplit("-", 1)
    return host.Member(name=name, team=team, role=role, kind="codex",
                       status=status, cwd="C:\\repo", address=address,
                       session_id=f"ses-{name}", session_reason=None,
                       focused=None, turn=turn)


class FakeHost:
    def __init__(self, owner="dev-lead", members=None):
        self.owner = owner
        self.members = dict(members or {})
        if owner not in self.members:
            self.members[owner] = _member(owner, "working", "t1")

    def own(self):
        return self.members[self.owner]

    def team(self, prefix):
        return {n: m for n, m in self.members.items() if m["team"] == prefix}


class FakeController:
    def __init__(self):
        self.policy = {"context": {"pressure_threshold": .75}}
        self.decisions = []
        self.compact_calls = []
        self.watch_calls = []
        self.decision = {"disposition": "recovery-needed",
                         "action": "compact-needed", "session_id": "ses-dev-codex"}
        self.result = {"action": "compacted"}

    def decide(self, name):
        self.decisions.append(name)
        return dict(self.decision)

    def compact(self, name):
        self.compact_calls.append(name)
        return {"agent": name, **self.result}

    def compact_watch(self, name):
        self.watch_calls.append(name)
        return {"agent": name, **self.result}


def _watch(detected, controller, steps):
    out = []
    t = [0.0]
    steps = iter(steps)

    def sleep(_):
        t[0] += 1
        try:
            next(steps)()
        except StopIteration:
            pass

    rc = team_watch.watch("dev", interval=1.0, timeout=30, detected=detected,
                          controller=controller, emit=out.append, sleep=sleep,
                          clock=lambda: t[0])
    return rc, [json.loads(line) for line in out]


def test_idle_over_threshold_compacts_once_and_delivers(monkeypatch):
    hc = FakeHost(members={"dev-codex": _member("dev-codex", "working", "t24")})
    controller = FakeController()
    delivered = []
    monkeypatch.setattr(say, "retry_pending_for", lambda _host, name: (
        delivered.append(name) or {"considered": 1, "delivered": 1, "pending": 0,
                                   "uncertain": 0, "failed": 0, "cancelled": 0,
                                   "unresolved": 0}))
    rc, lines = _watch(hc, controller, [
        lambda: hc.members["dev-codex"].update(status="idle")])
    assert rc == 0
    assert lines[0]["name"] == "dev-codex" and lines[0]["to"] == "idle"
    assert lines[0]["decision"]["action"] == "compact-needed"
    assert lines[1]["type"] == "recovery" and lines[1]["action"] == "compacted"
    assert lines[1]["delivery"]["delivered"] == 1
    assert controller.watch_calls == ["dev-codex"]
    assert controller.compact_calls == []
    assert delivered == ["dev-codex"]

    rc, lines = _watch(hc, controller, [lambda: None] * 3)
    assert rc == 0 and lines == []
    assert controller.watch_calls == ["dev-codex"], (
        "a watcher restart compacted the same idle worker again")


def test_a_busy_worker_is_never_compacted():
    hc = FakeHost(members={"dev-codex": _member("dev-codex", "working", "t24")})
    controller = FakeController()
    rc, lines = _watch(hc, controller, [lambda: None] * 4)
    assert rc == 0 and lines == []
    assert controller.decisions == []
    assert controller.watch_calls == [] and controller.compact_calls == []


def test_the_lead_is_never_compacted():
    hc = FakeHost(owner="dev-opencode", members={
        "dev-lead": _member("dev-lead", "working", "t1"),
        "dev-opencode": _member("dev-opencode", "idle", "t2"),
    })
    controller = FakeController()
    rc, lines = _watch(hc, controller, [
        lambda: hc.members["dev-lead"].update(status="idle")])
    assert rc == 0 and lines == []
    assert controller.decisions == []
    assert controller.watch_calls == [] and controller.compact_calls == []


def test_an_unknown_compaction_is_reported_without_delivery(monkeypatch):
    hc = FakeHost(members={"dev-codex": _member("dev-codex", "working", "t24")})
    controller = FakeController()
    controller.result = {"action": "compact-unknown", "reason": "timeout"}
    delivered = []
    monkeypatch.setattr(say, "retry_pending_for", lambda _host, name: delivered.append(name))
    rc, lines = _watch(hc, controller, [
        lambda: hc.members["dev-codex"].update(status="idle")])
    assert rc == 0
    assert lines[1]["action"] == "compact-unknown"
    assert lines[1]["reason"] == "timeout"
    assert "delivery" not in lines[1]
    assert delivered == []


def test_a_registered_task_uses_the_task_compaction(monkeypatch):
    hc = FakeHost(members={"dev-codex": _member("dev-codex", "working", "t24")})
    controller = FakeController()
    controller.decision = {"disposition": "recovery-needed", "action": "compact",
                           "task_id": "t", "session_id": "ses-dev-codex"}
    controller.result = {"action": "compacted", "task_id": "t"}
    monkeypatch.setattr(say, "retry_pending_for", lambda _host, name: {"delivered": 0})
    rc, lines = _watch(hc, controller, [
        lambda: hc.members["dev-codex"].update(status="idle")])
    assert rc == 0
    assert controller.compact_calls == ["dev-codex"]
    assert controller.watch_calls == []
    assert lines[1]["task_id"] == "t"
    assert lines[1]["action"] == "compacted"


def test_a_refused_decision_does_not_attempt_compaction():
    hc = FakeHost(members={"dev-codex": _member("dev-codex", "working", "t24")})
    controller = FakeController()
    controller.decision = {"disposition": "keep", "action": "keep", "reason": "below threshold"}
    rc, lines = _watch(hc, controller, [
        lambda: hc.members["dev-codex"].update(status="idle")])
    assert rc == 0
    assert len(lines) == 1
    assert lines[0]["decision"]["reason"] == "below threshold"
    assert controller.watch_calls == [] and controller.compact_calls == []