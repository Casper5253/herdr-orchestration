"""hx team watch: the lead's background wait for non-child workers."""
from __future__ import annotations

import json

from hx import host, team_watch


def _member(name, status="idle", address="t1", turn=7, kind="codex"):
    team, role = name.rsplit("-", 1)
    return host.Member(name=name, team=team, role=role, kind=kind,
                       status=status, cwd="C:\\repo", address=address,
                       session_id=None, session_reason=None, focused=None, turn=turn)


class FakeHost:
    def __init__(self):
        self.members = {}
        self.screen = ""

    def own(self):
        return _member("dev-lead", status="working", address="t23")

    def team(self, prefix):
        return {n: m for n, m in self.members.items() if m["team"] == prefix}

    def info(self, name):
        return self.members[name]

    def read(self, name, lines=200):
        return self.screen


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


# ---------- Q-558: a Devin turn that ended on a blocked command ----------


def test_blocked_command_is_read_from_the_last_screen_lines():
    lines = [" ✗ cd old && false"] + [f" working {index}" for index in range(20)]
    lines.append(" ✗ cd repo && npm test")
    assert team_watch.blocked_command("\n".join(lines)) == "cd repo && npm test"


def test_a_blocked_line_outside_the_tail_is_not_a_current_stop():
    lines = [" ✗ cd old && false"] + [f" working {index}" for index in range(20)]
    assert team_watch.blocked_command("\n".join(lines)) is None
    assert team_watch.blocked_command(" ✓ tests pass\n ✓ reported") is None


def test_a_devin_stop_on_a_blocked_command_notifies_the_lead(monkeypatch):
    hc = FakeHost()
    hc.members["dev-devin"] = _member("dev-devin", "working", "t46", kind="devin")
    hc.screen = " working\n\n ✗ cd repo && npm test\n\n❭ Ask Devin to build features"
    monkeypatch.setattr(
        team_watch.say, "has_report_since_brief", lambda worker, lead: False)
    notices = []
    monkeypatch.setattr(
        team_watch.say, "notify",
        lambda target, text: notices.append((target, text)) or ("delivered", None))
    rc, events = _watch(hc, FakeController(), [
        lambda: hc.members["dev-devin"].update(status="idle")])
    assert rc == 0
    assert events[-1]["notice"] == (
        "[hx] dev-devin stopped on a blocked command: cd repo && npm test")
    assert notices == [("dev-lead", events[-1]["notice"])]


def test_a_devin_stop_with_a_report_since_the_brief_is_quiet(monkeypatch):
    hc = FakeHost()
    hc.members["dev-devin"] = _member("dev-devin", "working", "t46", kind="devin")
    hc.screen = " ✗ cd repo && npm test"
    monkeypatch.setattr(
        team_watch.say, "has_report_since_brief", lambda worker, lead: True)
    notices = []
    monkeypatch.setattr(
        team_watch.say, "notify",
        lambda target, text: notices.append((target, text)) or ("delivered", None))
    _watch(hc, FakeController(), [
        lambda: hc.members["dev-devin"].update(status="idle")])
    assert notices == []


def test_only_a_devin_stop_with_a_blocked_line_notifies(monkeypatch):
    hc = FakeHost()
    hc.members["dev-codex"] = _member("dev-codex", "working", "t24")
    hc.screen = " ✗ cd repo && npm test"
    monkeypatch.setattr(
        team_watch.say, "has_report_since_brief", lambda worker, lead: False)
    notices = []
    monkeypatch.setattr(
        team_watch.say, "notify",
        lambda target, text: notices.append((target, text)) or ("delivered", None))
    _watch(hc, FakeController(), [
        lambda: hc.members["dev-codex"].update(status="idle")])
    assert notices == []

    hc = FakeHost()
    hc.members["dev-devin"] = _member("dev-devin", "working", "t46", kind="devin")
    hc.screen = " ✓ tests pass\n ✓ report sent"
    _watch(hc, FakeController(), [
        lambda: hc.members["dev-devin"].update(status="idle")])
    assert notices == []


def test_unconfirmed_notice_is_retried_on_the_next_pass(monkeypatch):
    hc = FakeHost()
    hc.members["dev-devin"] = _member("dev-devin", "working", "t46", kind="devin")
    hc.screen = " ✗ cd repo && npm test"
    monkeypatch.setattr(team_watch.say, "has_report_since_brief", lambda worker, lead: False)
    outcomes = iter([("pending", "blocked"), ("uncertain", "unknown"), ("delivered", None)])
    attempts = []
    def notify(target, text):
        attempts.append((target, text))
        return next(outcomes)
    monkeypatch.setattr(team_watch.say, "notify", notify)
    _watch(hc, FakeController(), [lambda: hc.members["dev-devin"].update(status="idle")])
    assert len(attempts) == 3

