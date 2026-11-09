"""hx say receipts: one line per send, the Enter press, and dead-role records."""
from __future__ import annotations

import json

import pytest

from hx import host, say, usage


def _member(name, role=None, kind="codex", status="idle", address="t1"):
    team = name.rsplit("-", 1)[0]
    return host.Member(
        name=name, team=team, role=role or name.rsplit("-", 1)[-1],
        kind=kind, status=status, cwd="C:\\repo", address=address,
        session_id=None, session_reason=None, focused=None, turn=4)


class FakeHc:
    """An hc-shaped host: roster lookup, a send queue, a screen, recorded keys."""

    def __init__(self):
        self.members = {
            "dev-codex": _member("dev-codex", address="t9"),
            "dev-lead": _member("dev-lead", role="lead", kind="claude", address="t24"),
            "dev-opencode": _member("dev-opencode", kind="opencode", address="t13"),
        }
        self.sent = []
        self.keys = []
        self.views = {}
        self.attempt_error = None
        self.on_send = None
        self.advance = True

    def own(self):
        return self.members["dev-codex"]

    def all_members(self):
        return list(self.members.values())

    def team(self, prefix):
        return {m["name"]: m for m in self.members.values() if m["name"].startswith(prefix)}

    def info(self, name):
        return self.members[name]

    def send(self, name, text):
        return 0

    def send_checked(self, name, text, **_):
        if self.attempt_error:
            raise self.attempt_error
        self.sent.append((name, text))
        status = self.members[name]["status"]
        turn = self.members[name]["turn"]
        if self.advance:
            self.members[name]["turn"] += 1  # a real submission advances the turn
        if self.on_send:
            self.on_send(name)
        return host.SendResult(started=True, status=status, turn=turn, ended=False, reason=None)

    def send_slash(self, member, command):
        return True

    def read(self, name, lines=200):
        return self.views.get(name, "some output\n>")

    def press(self, member, *keys):
        self.keys.append(["keys", member["address"], *keys])
        member["turn"] += 1  # the Enter submits, so the turn advances
        self.views["dev-lead"] = "submitted\n>"
        return True


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setattr(say, "SUBMIT_POLL_SECONDS", 0.3)
    monkeypatch.setattr(say, "ENTER_SETTLE_SECONDS", 0.01)
    hc = FakeHc()
    monkeypatch.setenv("HC_TERMINAL_ID", "t9")
    monkeypatch.setenv("HX_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(host, "detect", lambda: hc)
    monkeypatch.setattr(say, "_under_pressure", lambda name: False)
    monkeypatch.setattr(usage, "load_policy", lambda: {})
    monkeypatch.setattr(say, "SUBMIT_POLL_SECONDS", 0.3)
    monkeypatch.setattr(say, "ENTER_SETTLE_SECONDS", 0.01)
    return hc


def test_a_submitted_send_prints_one_receipt_line(fake, capsys):
    fake.on_send = lambda name: fake.members["dev-lead"].update(status="working")
    assert say.say("lead", "status please") == 0
    (line,) = capsys.readouterr().out.splitlines()
    assert line.endswith(" -> t24: submitted")
    assert fake.keys == []


def test_a_busy_receiver_is_reported_queued(fake, capsys):
    fake.members["dev-lead"]["status"] = "working"
    assert say.say("lead", "queued note") == 0
    assert capsys.readouterr().out.splitlines()[-1].endswith(" -> t24: queued")


def test_a_stuck_paste_gets_one_enter_press(fake, capsys):
    fake.advance = False  # send accepted but the turn never advanced
    text = "[team from dev-codex] brief ends here"
    fake.views["dev-lead"] = f"prompt\n<hc from=t9>{text}</hc>\n>"
    assert say.say("lead", "brief ends here") == 0
    (line,) = [ln for ln in capsys.readouterr().out.splitlines() if "-> t24:" in ln]
    assert line.endswith("submitted")
    assert fake.keys == [["keys", "t24", "enter"]]  # exactly one Enter


def test_a_stuck_paste_that_never_submits_is_uncertain(fake, capsys):
    fake.advance = False
    fake.views["dev-lead"] = "prompt\n<hc from=t9>[team from dev-codex] brief</hc>\n>"
    fake.press = lambda member, *keys: (fake.keys.append(["keys", member["address"], *keys]) or True)
    assert say.say("lead", "brief") == 1
    out = capsys.readouterr().out
    assert "uncertain" in out
    assert fake.keys == [["keys", "t24", "enter"]]  # pressed once, never twice


def test_no_visible_paste_means_no_enter_press(fake, capsys):
    fake.advance = False  # no turn advance and nothing pasted: unproven
    fake.views["dev-lead"] = "completely different screen\n>"
    assert say.say("lead", "hi") == 1
    assert " -> t24: uncertain" in capsys.readouterr().out
    assert fake.keys == []


def test_a_turn_advance_proves_submission(fake, capsys):
    fake.views["dev-lead"] = "completely different screen\n>"
    assert say.say("lead", "hi") == 0
    assert capsys.readouterr().out.endswith("submitted\n")
    assert fake.keys == []


def test_same_turn_working_status_is_not_submission(fake, capsys):
    fake.advance = False
    fake.members["dev-lead"]["status"] = "idle"
    fake.on_send = lambda name: fake.members["dev-lead"].update(status="working")
    assert say.say("lead", "hi") == 1
    assert " -> t24: uncertain" in capsys.readouterr().out


def test_a_working_send_with_no_advance_is_uncertain_not_queued(fake, capsys):
    fake.members["dev-lead"]["status"] = "working"
    fake.advance = False  # status alone: the 1312-char stuck-paste shape
    assert say.say("lead", "hi") == 1
    assert " -> t24: uncertain" in capsys.readouterr().out


def test_send_not_attempted_is_pending_and_retried(fake, capsys):
    fake.attempt_error = host.SendNotAttempted("hc not found")
    assert say.say("lead", "retry me") == 1
    assert " -> t24: pending" in capsys.readouterr().out
    fake.attempt_error = None
    fake.on_send = lambda name: fake.members["dev-lead"].update(status="working")
    assert say.retry_pending() == 0
    assert any(call[1].endswith("retry me") for call in fake.sent)


def test_a_dead_role_record_resolves_once_with_a_reason(fake, capsys, tmp_path):
    fake.members.pop("dev-lead")
    path = say._write_outbox("dev__codex", "dev-codex", "dev-lead",
                           "old report", "[team from dev-codex] old report")
    assert say.retry_pending() == 1
    out = capsys.readouterr().out
    assert " -> dev-lead: failed" in out and "undeliverable" in out
    assert json.loads(path.read_text())["state"] == "failed"
    capsys.readouterr()
    assert say.retry_pending() == 0
    assert "No pending messages for dev-codex." in capsys.readouterr().out


def _devin(fake):
    member = _member("dev-devin", kind="devin", status="idle", address="t2w")
    member["turn"] = 0
    fake.members["dev-devin"] = member
    return member


def test_devin_idle_to_working_proves_submission(fake, capsys):
    """Devin's turn stays put while it works; the status move is the proof."""
    devin = _devin(fake)
    fake.advance = False
    fake.on_send = lambda name: devin.update(status="working")
    assert say.say("devin", "brief") == 0
    assert capsys.readouterr().out.endswith(" -> t2w: submitted\n")
    assert fake.keys == []


def test_devin_still_idle_is_uncertain(fake, capsys):
    _devin(fake)
    fake.advance = False
    assert say.say("devin", "brief") == 1
    assert " -> t2w: uncertain" in capsys.readouterr().out
    assert fake.keys == []


def test_devin_queued_while_busy_stays_uncertain(fake, capsys):
    """A busy Devin's turn only advances on finish, so queueing is not provable."""
    devin = _devin(fake)
    devin["status"] = "working"
    fake.advance = False
    assert say.say("devin", "queued note") == 1
    assert " -> t2w: uncertain" in capsys.readouterr().out


def test_devin_stuck_paste_gets_one_enter_press(fake, capsys):
    devin = _devin(fake)
    fake.advance = False
    # The Devin pane's deny line rides on the message, so the stuck paste is
    # whatever was actually sent; mirror it into the fake's composer.
    fake.on_send = lambda name: fake.views.__setitem__(
        "dev-devin", f"prompt\n<hc from=t9>{fake.sent[-1][1]}</hc>\n>")
    assert say.say("devin", "brief ends here") == 0
    assert fake.keys == [["keys", "t2w", "enter"]]  # exactly one Enter


def test_submit_poll_seconds_reads_per_kind_windows(fake, monkeypatch):
    monkeypatch.setattr(usage, "load_policy", lambda: {
        "say": {"submit_poll_seconds": {"devin": 25, "default": 8}}})
    assert say._submit_poll_seconds("devin") == 25
    assert say._submit_poll_seconds("codex") == 8
    assert say._submit_poll_seconds("unknown-kind") == 8
    monkeypatch.setattr(usage, "load_policy", lambda: {})
    assert say._submit_poll_seconds("devin") == say.SUBMIT_POLL_SECONDS
