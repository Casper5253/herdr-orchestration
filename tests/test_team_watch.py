"""hx team watch: the lead's background wait for non-child workers."""
from __future__ import annotations

import json

from hx import host, team_watch


def _member(name, status="idle", address="t1", turn=7):
    team, role = name.rsplit("-", 1)
    return host.Member(name=name, team=team, role=role, kind="codex",
                       status=status, cwd="C:\\repo", address=address,
                       session_id=None, session_reason=None, focused=None, turn=turn)


class FakeHost:
    def __init__(self):
        self.members = {}

    def own(self):
        return _member("dev-lead", status="working", address="t23")

    def team(self, prefix):
        return {n: m for n, m in self.members.items() if m["team"] == prefix}

    def info(self, name):
        return self.members[name]


class FakeController:
    def __init__(self):
        self.decisions = []

    def decide(self, name):
        self.decisions.append(name)
        return {"disposition": "keep", "action": "keep"}


def _watch(host, controller, steps):
    """Drive watch() with an instant clock; each sleep applies the next roster step."""
    out = []
    t = [0.0]
    steps = iter(steps)

    def sleep(_):
        t[0] += 1
        try:
            next(steps)()
        except StopIteration:
            pass

    rc = team_watch.watch(
        "dev", interval=1.0, timeout=30, detected=host,
        controller=controller, emit=out.append, sleep=sleep, clock=lambda: t[0])
    return rc, [json.loads(line) for line in out]


def test_a_worker_going_idle_wakes_the_lead():
    hc = FakeHost()
    hc.members["dev-codex"] = _member("dev-codex", "working", "t24")
    controller = FakeController()
    rc, events = _watch(hc, controller, [
        lambda: hc.members["dev-codex"].update(status="idle")])
    assert rc == 0
    (event,) = events
    assert event["to"] == "idle" and event["from"] == "working"
    assert event["address"] == "t24" and event["role"] == "codex" and event["turn"] == 7
    assert controller.decisions == ["dev-codex"]


def test_a_startup_idle_worker_never_counts_as_finished():
    hc = FakeHost()
    hc.members["dev-codex"] = _member("dev-codex", "idle", "t24")
    rc, events = _watch(hc, FakeController(), [lambda: None] * 5)
    assert rc == 0 and events == []


def test_a_heartbeat_does_not_fire():
    hc = FakeHost()
    hc.members["dev-codex"] = _member("dev-codex", "working", "t24")
    rc, events = _watch(hc, FakeController(), [lambda: None] * 5)
    assert rc == 0 and events == []


def test_blocked_and_exit_both_wake():
    hc = FakeHost()
    hc.members["dev-codex"] = _member("dev-codex", "working", "t24")
    rc, events = _watch(hc, FakeController(), [
        lambda: hc.members["dev-codex"].update(status="blocked")])
    assert events[0]["to"] == "blocked"

    hc = FakeHost()
    hc.members["dev-codex"] = _member("dev-codex", "working", "t24")
    rc, events = _watch(hc, FakeController(), [lambda: hc.members.pop("dev-codex")])
    assert events[0]["to"] == "exit" and events[0]["from"] == "working"


def test_a_new_worker_is_picked_up_mid_watch():
    hc = FakeHost()
    controller = FakeController()

    def spawn():
        hc.members["dev-opencode"] = _member("dev-opencode", "working", "t13")

    rc, events = _watch(hc, controller, [
        spawn,
        lambda: hc.members["dev-opencode"].update(status="done")])
    assert events[0]["name"] == "dev-opencode" and events[0]["to"] == "done"


def test_decide_runs_once_for_a_deferred_idle():
    hc = FakeHost()
    hc.members["dev-codex"] = _member("dev-codex", "working", "t24")
    controller = FakeController()
    _watch(hc, controller, [lambda: hc.members["dev-codex"].update(status="idle")])
    assert controller.decisions == ["dev-codex"]


def test_the_lead_itself_is_not_watched():
    hc = FakeHost()
    hc.members["dev-lead"] = hc.own()
    hc.members["dev-lead"]["status"] = "working"
    rc, events = _watch(hc, FakeController(), [lambda: None] * 5)
    assert events == []


def test_the_lead_is_excluded_even_with_an_explicit_team():
    hc = FakeHost()
    hc.members["dev-lead"] = hc.own()
    hc.members["dev-lead"]["status"] = "working"
    controller = FakeController()
    rc, events = _watch(hc, controller, [
        lambda: hc.members["dev-lead"].update(status="idle"),
        lambda: None] * 2)
    assert events == [] and controller.decisions == []


def test_unknown_to_idle_at_the_same_turn_is_no_event():
    hc = FakeHost()
    hc.members["dev-codex"] = _member("dev-codex", "unknown", "t24")
    controller = FakeController()
    rc, events = _watch(hc, controller, [
        lambda: hc.members["dev-codex"].update(status="idle"),
        lambda: None] * 2)
    assert events == [] and controller.decisions == []


def test_a_turn_advance_between_scans_is_an_event():
    hc = FakeHost()
    hc.members["dev-codex"] = _member("dev-codex", "idle", "t24", turn=4)
    controller = FakeController()
    rc, events = _watch(hc, controller, [
        lambda: hc.members["dev-codex"].update(turn=5)])
    (event,) = events
    assert event["name"] == "dev-codex" and event["turn"] == 5
    assert controller.decisions == ["dev-codex"]


def test_a_task_starting_does_not_wake_the_lead():
    hc = FakeHost()
    hc.members["dev-codex"] = _member("dev-codex", "idle", "t24", turn=4)
    controller = FakeController()
    rc, events = _watch(hc, controller, [
        lambda: hc.members["dev-codex"].update(status="working", turn=5),
        lambda: None] * 2)
    assert events == [] and controller.decisions == []


def test_unknown_keeps_the_working_baseline():
    hc = FakeHost()
    hc.members["dev-codex"] = _member("dev-codex", "working", "t24", turn=4)
    controller = FakeController()
    rc, events = _watch(hc, controller, [
        lambda: hc.members["dev-codex"].update(status="unknown"),
        lambda: hc.members["dev-codex"].update(status="idle")])
    (event,) = events
    assert event["from"] == "working" and event["to"] == "idle"


def test_main_wires_the_team_watch_command(monkeypatch, capsys):
    from hx.__main__ import main
    seen = {}
    monkeypatch.setattr(team_watch, "watch", lambda team, **kw: seen.update(team=team) or 0)
    assert main(["team", "watch", "--team", "dev", "--timeout", "5"]) == 0
    assert seen["team"] == "dev"
