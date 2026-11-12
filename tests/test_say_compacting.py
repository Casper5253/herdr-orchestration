"""hx say never sends into a pane whose screen shows a compaction in progress.

Seen 10 and 11 Nov: a message sent while Codex still showed "Compacting
context" was eaten. No queued-message line appeared and it was never acted
on. `hc wait`
returning done is not a reliable end-of-compaction signal, so `hx say` reads
the target's screen, waits a bounded time for the marker to clear, then sends;
a marker that never clears holds the message in the outbox for
`hx say --pending`.
"""
from __future__ import annotations

import json

import pytest

from hx import host, say


def _member(name, role=None, kind="codex", status="idle", address="t1"):
    team = name.rsplit("-", 1)[0]
    return host.Member(
        name=name, team=team, role=role or name.rsplit("-", 1)[-1],
        kind=kind, status=status, cwd="C:\\repo", address=address,
        session_id=None, session_reason=None, focused=None, turn=4)


# The shape Codex draws while compaction runs: the marker sits on the status
# line below the › composer, where "context left" otherwise stands.
CODEX_COMPACTING = (
    "earlier output\n"
    "› \n"
    "  Compacting context · esc to interrupt\n"
)
CODEX_READY = "compaction done\n› \n  gpt-5.2-codex high · 12% context left\n"
CLAUDE_COMPACTING = (
    "earlier output\n"
    "✽ Compacting conversation\n"
    + "─" * 30 + "\n"
    "❯ \n"
    "  ctx 90% | 5h 80% wk 60%\n"
)


class FakeHc:
    """An hc-shaped host whose per-name screens are set by the test."""

    def __init__(self):
        self.members = {
            "dev-opencode": _member("dev-opencode", kind="opencode", address="t13"),
            "dev-codex": _member("dev-codex", kind="codex", address="t9"),
            "dev-lead": _member("dev-lead", role="lead", kind="claude", address="t24"),
            "dev-worker": _member("dev-worker", kind="opencode", address="t30"),
            "dev-devin": _member("dev-devin", kind="devin", address="t5"),
        }
        self.sent = []
        self.views = {}
        self.reads = []

    def own(self):
        return self.members["dev-opencode"]

    def all_members(self):
        return list(self.members.values())

    def team(self, prefix):
        return {m["name"]: m for m in self.members.values() if m["name"].startswith(prefix)}

    def info(self, name):
        return self.members[name]

    def send(self, name, text):
        return 0

    def send_checked(self, name, text, **_):
        self.sent.append((name, text))
        status = self.members[name]["status"]
        self.members[name]["turn"] += 1  # a real submission advances the turn
        return host.SendResult(started=True, status=status, turn=4, ended=False, reason=None)

    def send_slash(self, member, command):
        return True

    def read(self, name, lines=200):
        self.reads.append(name)
        return self.views.get(name, "ordinary output\n› \n")

    def press(self, member, *keys):
        member["turn"] += 1
        return True


class FakeHcApp(FakeHc, host.HcHost):
    """The same fake, seen by say as the Herdr app whose read returns the TUI screen."""


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch, tmp_path):
    hc = FakeHcApp()
    monkeypatch.setenv("HC_TERMINAL_ID", "t13")
    monkeypatch.setenv("HX_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(host, "detect", lambda: hc)
    monkeypatch.setattr(say, "_under_pressure", lambda name: False)
    monkeypatch.setattr(say, "COMPACT_WAIT_SECONDS", 1.0, raising=False)
    monkeypatch.setattr(say, "COMPACT_POLL_SECONDS", 0.01, raising=False)
    monkeypatch.setattr(say, "SUBMIT_POLL_SECONDS", 0.3)
    monkeypatch.setattr(say, "ENTER_SETTLE_SECONDS", 0.01)
    return hc


def test_a_compacting_receiver_is_never_sent_the_message(fake, capsys):
    fake.views["dev-codex"] = CODEX_COMPACTING
    assert say.say("codex", "the brief") == 1
    captured = capsys.readouterr()
    assert fake.sent == []
    assert "pending" in captured.out
    assert "compacting" in captured.err.lower()
    assert "hx say --pending" in captured.err


def test_a_compacting_lead_is_held_too(fake):
    """The compaction hold is not the pressure hold: the lead self-compacts."""
    fake.views["dev-lead"] = CLAUDE_COMPACTING
    assert say.say("lead", "status please") == 1
    assert fake.sent == []


def test_a_marker_that_clears_inside_the_wait_still_sends(fake, capsys):
    screens = [CODEX_COMPACTING, CODEX_READY]

    def read(name, lines=200):
        fake.reads.append(name)
        return screens.pop(0) if screens else CODEX_READY

    fake.read = read
    assert say.say("codex", "the brief") == 0
    assert fake.sent == [("dev-codex", "[team from dev-opencode] the brief")]
    assert "submitted" in capsys.readouterr().out


def test_the_held_message_resends_with_pending_once_ready(fake):
    fake.views["dev-codex"] = CODEX_COMPACTING
    assert say.say("codex", "the brief") == 1
    assert fake.sent == []
    fake.views["dev-codex"] = CODEX_READY
    assert say.retry_pending() == 0
    assert [call[1] for call in fake.sent] == ["[team from dev-opencode] the brief"]


def test_a_held_retry_does_not_send_while_still_compacting(fake):
    fake.views["dev-codex"] = CODEX_COMPACTING
    assert say.say("codex", "the brief") == 1
    assert say.retry_pending() == 1
    assert fake.sent == []
    fake.views["dev-codex"] = CODEX_READY
    assert say.retry_pending() == 0
    assert [call[1] for call in fake.sent] == ["[team from dev-opencode] the brief"]


def test_a_clean_screen_sends_without_waiting(fake, monkeypatch):
    sleeps = []
    monkeypatch.setattr(say.time, "sleep", lambda seconds: sleeps.append(seconds))
    assert say.say("codex", "the brief") == 0
    assert fake.sent == [("dev-codex", "[team from dev-opencode] the brief")]
    assert fake.reads == ["dev-codex"]  # one screen read, no polling


def test_a_non_app_host_never_screen_checks(monkeypatch, tmp_path):
    """The standalone herdr read is unwrapped agent output, not the TUI screen."""
    fake = FakeHc()
    fake.views["dev-codex"] = CODEX_COMPACTING
    monkeypatch.setenv("HX_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(host, "detect", lambda: fake)
    monkeypatch.setattr(say, "_under_pressure", lambda name: False)
    monkeypatch.setattr(say, "SUBMIT_POLL_SECONDS", 0.3)
    assert say.say("codex", "the brief") == 0
    assert fake.sent == [("dev-codex", "[team from dev-opencode] the brief")]
    assert fake.reads == []  # the screen was never consulted


def test_an_unreadable_screen_never_blocks_the_send(fake):
    def read(name, lines=200):
        raise RuntimeError("hc read failed")

    fake.read = read
    assert say.say("codex", "the brief") == 0
    assert fake.sent == [("dev-codex", "[team from dev-opencode] the brief")]


def test_force_still_overrides_the_compaction_hold(fake):
    """--force is the documented escape for an intentional control message."""
    fake.views["dev-codex"] = CODEX_COMPACTING
    assert say.say("codex", "recovery note", force=True) == 0
    assert fake.sent == [("dev-codex", "[team from dev-opencode] recovery note")]


_CLAUDE_RULE = "─" * 30


@pytest.mark.parametrize("target,view", [
    # Codex review finding 1: the marker is a live-status claim. The same words
    # in transcript output (a quote, a code review, this file's own source) are
    # a mention, never a compaction.
    ("codex",
     "checking the report: it says Compacting context was the marker\n"
     "› \n"
     "  gpt-5.2-codex high · 40% context left\n"),
    ("lead",
     "notes: Compacting conversation is the spinner text\n"
     "more output\n" + _CLAUDE_RULE + "\n"
     "❯ \n"
     "  ctx 23% | 5h 17% wk 16%\n"),
    ("worker",
     "docs say compacting shows while the session compacts\n"
     "┃ \n"
     "Build · opencode\n"),
    ("devin",
     "the footer marker word is compacting\n"
     "❭ \n" + _CLAUDE_RULE + "\n"
     "SWE-2 High · accept edits\n"
     "Context: 40k / 262k tokens (15%)\n"),
])
def test_a_marker_mentioned_in_transcript_output_is_not_a_hold(fake, capsys, target, view):
    name = f"dev-{target}"
    fake.views[name] = view
    assert say.say(target, "the brief") == 0
    # A devin target carries its deny line; the body is what matters here.
    assert len(fake.sent) == 1 and fake.sent[0][0] == name
    assert "[team from dev-opencode] the brief" in fake.sent[0][1]
    assert fake.reads == [name]  # one screen read, no waiting


def test_a_read_error_after_a_seen_marker_keeps_the_hold(fake, capsys):
    """Codex review finding 2: marker seen, then the screen goes unreadable.

    The pane may still be compacting; a failed read is no clear signal, so the
    message holds instead of repeating the original lost-brief failure.
    """
    def read(name, lines=200):
        fake.reads.append(name)
        if len(fake.reads) == 1:
            return CODEX_COMPACTING
        raise RuntimeError("hc read failed")

    fake.read = read
    assert say.say("codex", "the brief") == 1
    assert fake.sent == []
    assert "pending" in capsys.readouterr().out


@pytest.mark.parametrize("text,why", [
    (CODEX_READY, "in-progress"),
    (CODEX_READY, "truncated"),
    ("", "in-progress"),
])
def test_an_incomplete_read_after_a_seen_marker_keeps_the_hold(
        fake, capsys, text, why):
    """Codex recheck probe 1: an incomplete read is not a clear read.

    `hc read --json` may return an in-progress or truncated view; `complete`
    False means the clear-looking text is not proof the marker is gone, so
    the hold stays until a complete read clears it or the wait expires.
    """
    def read(name, lines=200):
        fake.reads.append(name)
        if len(fake.reads) == 1:
            return CODEX_COMPACTING
        return host.ReadText(text, complete=False, why=why)

    fake.read = read
    assert say.say("codex", "the brief") == 1
    assert fake.sent == []
    assert "pending" in capsys.readouterr().out


def test_an_incomplete_first_read_is_no_evidence_and_sends(fake):
    """A screen never fully read is like an unreadable one: nothing was seen."""
    def read(name, lines=200):
        fake.reads.append(name)
        return host.ReadText("", complete=False, why="in-progress")

    fake.read = read
    assert say.say("codex", "the brief") == 0
    assert fake.sent == [("dev-codex", "[team from dev-opencode] the brief")]


@pytest.mark.parametrize("target,view", [
    # Codex recheck probe 2: a narrow pane wraps the marker across two status
    # lines; matching each line separately misses it and sends anyway.
    ("codex",
     "› \n"
     "  Compacting\n"
     "  context plus esc to interrupt\n"),
    ("lead",
     "earlier output\n"
     "✽ Compacting\n"
     "conversation\n"
     + _CLAUDE_RULE + "\n"
     "❯ \n"
     "  ctx 90% | 5h 80% wk 60%\n"),
])
def test_a_marker_wrapped_across_status_lines_still_holds(fake, capsys, target, view):
    name = f"dev-{target}"
    fake.views[name] = view
    assert say.say(target, "the brief") == 1
    assert fake.sent == []
    assert "pending" in capsys.readouterr().out


@pytest.mark.parametrize("target,view", [
    # Codex recheck probe 3: a standalone marker line in transcript output is
    # a quote or bullet, not live status; the status region alone decides.
    ("codex",
     "the review said so\n"
     "- \"Compacting context\"\n"
     "› \n"
     "  gpt-5.2-codex high · 40% context left\n"),
    ("lead",
     "- \"Compacting conversation\"\n"
     "more output\n" + _CLAUDE_RULE + "\n"
     "❯ \n"
     "  ctx 23% | 5h 17% wk 16%\n"),
    ("worker",
     "notes\n"
     "> \"Compacting\"\n"
     "┃ \n"
     "Build · opencode\n"),
    ("devin",
     "notes\n"
     "> \"Compacting\"\n"
     "❭ \n" + _CLAUDE_RULE + "\n"
     "SWE-2 High · accept edits\n"
     "Context: 40k / 262k tokens (15%)\n"),
])
def test_a_quoted_or_bulleted_marker_line_is_not_a_hold(fake, capsys, target, view):
    name = f"dev-{target}"
    fake.views[name] = view
    assert say.say(target, "the brief") == 0
    assert len(fake.sent) == 1 and fake.sent[0][0] == name
    assert "[team from dev-opencode] the brief" in fake.sent[0][1]
    assert fake.reads == [name]


@pytest.mark.parametrize("target,view", [
    # Codex recheck probe 4: the composer is typed text, not status. A draft
    # naming the marker must not hold the message.
    ("codex",
     "› Review Compacting context detection\n"
     "  gpt-5.2-codex high · 40% context left\n"),
    ("lead",
     "output\n" + _CLAUDE_RULE + "\n"
     "❯ Review Compacting conversation detection\n"
     "  ctx 23% | 5h 17% wk 16%\n"),
    ("worker",
     "output\n"
     "┃ Review compacting detection\n"
     "Build · opencode\n"),
    ("devin",
     "output\n"
     "❭ Review compacting detection\n"
     + _CLAUDE_RULE + "\n"
     "SWE-2 High · accept edits\n"
     "Context: 40k / 262k tokens (15%)\n"),
])
def test_a_composer_draft_naming_the_marker_is_not_a_hold(fake, capsys, target, view):
    name = f"dev-{target}"
    fake.views[name] = view
    assert say.say(target, "the brief") == 0
    assert len(fake.sent) == 1 and fake.sent[0][0] == name
    assert "[team from dev-opencode] the brief" in fake.sent[0][1]
    assert fake.reads == [name]
