"""hx say receipts: one line per send, the Enter press, and dead-role records."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import pytest

from hx import devin, host, ledger, say


_Q572 = Path(__file__).parent / "fixtures" / "q572"


@pytest.mark.parametrize("sidebar", [False, True])
def test_captured_opencode_home_allows_delivery(fake, capsys, sidebar):
    screen = (_Q572 / "opencode-home.txt").read_text(encoding="utf-8")
    if sidebar:
        rows = screen.splitlines()
        width = max(len(row) for row in rows)
        screen = "\n".join(row.ljust(width + 2) + "│ Todo: finish report" for row in rows)
    fake.views["dev-opencode"] = screen
    assert say.say("opencode", "brief ends here") == 0
    assert capsys.readouterr().out.endswith("submitted\n")
    assert len(fake.sent) == 1


@pytest.mark.parametrize("example", ["Fix broken tests", "What is the tech stack of this project?"])
def test_opencode_rotating_placeholder_is_an_empty_composer(example):
    """20 Nov: OpenCode 1.19.5 rotates the example inside its placeholder."""
    assert host._composer_empty(f'Ask anything… "{example}"', "opencode")
    assert not host._composer_empty("Fix broken tests", "opencode")


def test_opencode_sidebar_does_not_hide_an_existing_draft(fake, capsys):
    screen = (_Q572 / "opencode-home.txt").read_text(encoding="utf-8")
    screen = screen.replace('Ask anything… "What is the tech stack of this project?"', "my existing draft")
    rows = screen.splitlines()
    width = max(len(row) for row in rows)
    fake.views["dev-opencode"] = "\n".join(
        row.ljust(width + 2) + "│ Todo: finish report" for row in rows)
    assert say.say("opencode", "brief ends here") == 1
    assert "pending (composer not empty)" in capsys.readouterr().out
    assert fake.sent == []
    assert fake.keys == []


def test_delayed_wrapped_codex_paste_is_not_receipted_before_tab(fake, capsys):
    fake.members["dev-lead"].update(kind="codex", status="working")
    fake.advance = False
    wrapped = (_Q572 / "codex-wrapped.txt").read_text(encoding="utf-8")
    wrapped = wrapped.rsplit("\n", 2)[0] + "\n  tab to queue message\n"
    views = iter([_CODEX_IDLE_EMPTY, _CODEX_IDLE_EMPTY, wrapped, _CODEX_BUSY_QUEUED])
    fake.read = lambda *args: next(views)
    fake.press = lambda member, *keys: fake.keys.append(list(keys)) or True
    assert say.say("lead", "brief ends here") == 0
    assert capsys.readouterr().out.endswith("queued\n")
    assert fake.keys == [["tab"]]


def test_empty_codex_without_turn_advance_is_uncertain(fake, capsys):
    fake.members["dev-lead"].update(kind="codex", status="working")
    fake.advance = False
    assert say.say("lead", "brief ends here") == 1
    assert "uncertain (empty composer without submission evidence)" in capsys.readouterr().out
    assert fake.keys == []


def _member(name, role=None, kind="codex", status="idle", address="t1"):
    team = name.rsplit("-", 1)[0]
    return host.Member(
        name=name, team=team, role=role or name.rsplit("-", 1)[-1],
        kind=kind, status=status, cwd="C:\\repo", address=address,
        session_id=None, session_reason=None, focused=None, turn=4)


# Live screen shapes, captured read-only with `hc read` on 16 Nov. A mid-turn
# Codex keeps the pasted message in its composer and offers `tab to queue
# message`; Tab moves it to the queued list and the empty composer prints the
# `Ask Codex to do anything` placeholder. Devin's empty composer prints the
# `Guide Devin while it works` placeholder above the footer rule.
_CODEX_STATUS = "  gpt-5.2-codex high · 91% context left"
_CODEX_BUSY_STAGED = (
    "• Working (13m 15s • esc to interrupt)\n\n"
    '› <hc from="t9">[team from dev-codex] brief ends here</hc>\n\n'
    f"{_CODEX_STATUS}\n  tab to queue message\n")
_CODEX_FOREIGN_PREFIX = f"› [team\n\n{_CODEX_STATUS}\n"
_CODEX_WRAPPED_FOREIGN_TAIL = (
    '› <hc from="t9">[team from dev-codex] brief ends here</hc>\n'
    "  plus the creator's own addition\n\n"
    f"{_CODEX_STATUS}\n")
_CODEX_BLANK_PROMPT_STAGED = (
    "› \n"
    '  <hc from="t9">[team from dev-codex] brief ends here</hc>\n\n'
    f"{_CODEX_STATUS}\n")
_CODEX_IDLE_STAGED_QUOTED = (
    "Earlier output quoting tab to queue message while explaining keys\n\n"
    '› <hc from="t9">[team from dev-codex] brief ends here</hc>\n\n'
    f"{_CODEX_STATUS}\n")
_CODEX_STAGED_WITH_EDIT = (
    '› <hc from="t9">[team from dev-codex] brief ends here</hc> more\n\n'
    f"{_CODEX_STATUS}\n")
_DEVIN_STAGED_WRAP = (
    '❭ <hc from="t9">[team from dev-codex] brief ends\n'
    "  here</hc>\n" + "─" * 20 + "\nSWE-2 High · accept edits")
_OPENCODE_STAGED_WRAP = (
    '┃ <hc from="t9">[team from dev-codex] brief\n'
    "┃ ends here</hc>\n"
    "┃ \n"
    "Build · opencode")
_CODEX_BUSY_QUEUED = (
    "• Working (13m 15s • esc to interrupt)\n\n"
    "• Queued follow-up inputs\n"
    '  ↳ <hc from="t9">[team from dev-codex] brief ends here</hc>\n'
    "    shift+← edit last queued message\n\n"
    f"› Ask Codex to do anything\n\n{_CODEX_STATUS}\n")
_CODEX_IDLE_STAGED = (
    '› <hc from="t9">[team from dev-codex] brief ends here</hc>\n\n'
    f"{_CODEX_STATUS}\n")
_CODEX_IDLE_EMPTY = f"› \n\n{_CODEX_STATUS}\n"
_CODEX_FOREIGN_DRAFT = f"› remind me to buy milk\n\n{_CODEX_STATUS}\n"
_DEVIN_EMPTY = "❭ Guide Devin while it works\n" + "─" * 20 + "\nSWE-2 High · accept edits"
_OPENCODE_EMPTY = "┃  \nBuild · opencode"


class FakeHc:
    """An hc-shaped host: roster lookup, a send queue, a screen, recorded keys."""

    def __init__(self):
        self.members = {
            "dev-codex": _member("dev-codex", address="t9"),
            "dev-lead": _member("dev-lead", role="lead", kind="claude", address="t24"),
            "dev-opencode": _member("dev-opencode", kind="opencode", address="t13"),
            "dev-devin": _member("dev-devin", kind="devin", address="t46"),
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
        if self.advance:
            self.members[name]["turn"] += 1  # a real submission advances the turn
        if self.on_send:
            self.on_send(name)
        return host.SendResult(started=True, status=status, turn=4, ended=False, reason=None)

    def send_slash(self, member, command):
        return True

    def read(self, name, lines=200):
        empty = {"codex": _CODEX_IDLE_EMPTY, "devin": _DEVIN_EMPTY,
                 "opencode": _OPENCODE_EMPTY}
        return self.views.get(name, empty.get(self.members[name]["kind"], "some output\n>"))

    def press(self, member, *keys):
        self.keys.append(["keys", member["address"], *keys])
        member["turn"] += 1  # the Enter submits, so the turn advances
        self.views["dev-lead"] = "submitted\n>"
        return True


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setattr(say, "SUBMIT_POLL_SECONDS", 0.3)
    monkeypatch.setattr(say, "KEY_SETTLE_SECONDS", 0.01)
    hc = FakeHc()
    monkeypatch.setenv("HC_TERMINAL_ID", "t9")
    monkeypatch.setenv("HX_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(host, "detect", lambda: hc)
    monkeypatch.setattr(say, "_under_pressure", lambda name: False)
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




@pytest.mark.parametrize("draft", [
    _CODEX_FOREIGN_DRAFT, _CODEX_FOREIGN_PREFIX,
    _CODEX_WRAPPED_FOREIGN_TAIL, _CODEX_BLANK_PROMPT_STAGED,
    _CODEX_IDLE_STAGED.replace('from="t9"', 'from="other"'),
    _CODEX_IDLE_STAGED.replace("brief ends here", "briefendshere"),
])
def test_existing_draft_is_pending_without_typing(fake, capsys, tmp_path, draft):
    fake.members["dev-lead"]["kind"] = "codex"
    fake.views["dev-lead"] = draft
    assert say.say("lead", "brief ends here") == 1
    assert "pending (composer not empty)" in capsys.readouterr().out
    assert fake.sent == []
    assert fake.keys == []
    record = next((tmp_path / ".hx" / "outbox").rglob("*.json"))
    assert json.loads(record.read_text())["state"] == "pending"
    fake.views["dev-lead"] = _CODEX_IDLE_EMPTY
    assert say.retry_pending() == 0
    assert len(fake.sent) == 1


@pytest.mark.parametrize("kind,empty", [
    ("codex", _CODEX_IDLE_EMPTY),
    ("codex", _CODEX_BUSY_QUEUED),
    ("devin", _DEVIN_EMPTY),
    ("devin", _DEVIN_EMPTY.replace("Guide Devin while it works",
        "Ask Devin to build features, fix bugs, or work on your code")),
    ("opencode", _OPENCODE_EMPTY),
])
def test_empty_composers_and_placeholders_allow_delivery(fake, capsys, kind, empty):
    fake.members["dev-lead"]["kind"] = kind
    fake.views["dev-lead"] = empty
    assert say.say("lead", "brief ends here") == 0
    assert capsys.readouterr().out.endswith("submitted\n")
    assert len(fake.sent) == 1
    assert fake.keys == []


@pytest.mark.parametrize("kind,staged,empty,key,word", [
    ("codex", _CODEX_BUSY_STAGED, _CODEX_BUSY_QUEUED, "tab", "queued"),
    ("codex", _CODEX_IDLE_STAGED, _CODEX_IDLE_EMPTY, "enter", "submitted"),
    ("codex", _CODEX_IDLE_STAGED_QUOTED, _CODEX_IDLE_EMPTY, "enter", "submitted"),
    ("codex", _CODEX_BLANK_PROMPT_STAGED, _CODEX_IDLE_EMPTY, "enter", "submitted"),
    ("devin", _DEVIN_STAGED_WRAP, _DEVIN_EMPTY, "enter", "submitted"),
    ("opencode", _OPENCODE_STAGED_WRAP, _OPENCODE_EMPTY, "enter", "submitted"),
])
def test_newly_staged_paste_gets_provider_key(fake, capsys, kind, staged, empty, key, word):
    fake.members["dev-lead"]["kind"] = kind
    fake.views["dev-lead"] = empty
    fake.on_send = lambda name: fake.views.update({name: staged})
    def press(member, *keys):
        fake.keys.append(list(keys))
        fake.views["dev-lead"] = empty
        return True
    fake.press = press
    assert say.say("lead", "brief ends here") == 0
    assert capsys.readouterr().out.endswith(word + "\n")
    assert fake.keys == [[key]]


@pytest.mark.parametrize("after", [_CODEX_IDLE_STAGED, _CODEX_STAGED_WITH_EDIT,
    _CODEX_IDLE_STAGED.replace("brief ends here", "EDITED brief ends here")])
def test_still_nonempty_after_key_is_pending(fake, capsys, after):
    fake.members["dev-lead"]["kind"] = "codex"
    fake.views["dev-lead"] = _CODEX_IDLE_EMPTY
    fake.on_send = lambda name: fake.views.update({name: _CODEX_IDLE_STAGED})
    def press(member, *keys):
        fake.keys.append(list(keys))
        fake.views["dev-lead"] = after
        return True
    fake.press = press
    assert say.say("lead", "brief ends here") == 1
    assert "pending (paste still staged after enter)" in capsys.readouterr().out
    assert fake.keys == [["enter"]]


def test_unidentified_composer_does_not_type(fake, capsys):
    fake.members["dev-lead"]["kind"] = "codex"
    fake.views["dev-lead"] = "no composer visible"
    assert say.say("lead", "hi") == 1
    assert "uncertain (no composer identified)" in capsys.readouterr().out
    assert fake.sent == []
    assert fake.keys == []


_DEVIN_COMPOSER = Path(__file__).parent / "fixtures" / "devin-composer"
_DEVIN_IDLE_234 = (_DEVIN_COMPOSER / "idle-v3000.11.234.txt").read_text(encoding="utf-8")
_DEVIN_QUEUED_234 = (_DEVIN_COMPOSER / "queued-v3000.11.234.txt").read_text(encoding="utf-8")
_DEVIN_FULL_234 = (_DEVIN_COMPOSER / "full-v3000.11.234.txt").read_text(encoding="utf-8")
_DEVIN_QUEUED_TEXT = "[team from dev-lead] New brief: read .hx/briefs/q632-e2e-repair.md and do exactly that."


@pytest.mark.parametrize("edited", [False, True])
def test_devin_full_composer_retries_only_the_sent_text(fake, capsys, monkeypatch, edited):
    from hx.__main__ import main

    fake.members["dev-lead"]["kind"] = "devin"
    fake.views["dev-lead"] = _DEVIN_IDLE_234
    fake.advance = False
    body = ("Please use the synthetic worktree to review the requested fix, run the "
            "existing local checks, and report the exact outcome with the path to the "
            "saved evidence. Keep this fixture local.")
    denied = ("Denied for you (a denied call cancels your turn): sample-deploy, "
              "sample-push, sample-upload, sample-publish, sample-admin, "
              "sample-install, sample-release, sample-reset, sample-remove")
    monkeypatch.setattr(devin, "pane_denied_line", lambda name: denied)
    staged = _DEVIN_FULL_234.replace("sample-remove", "edited") if edited else _DEVIN_FULL_234
    fake.on_send = lambda name: fake.views.update({name: staged})

    def press(member, *keys):
        fake.keys.append(list(keys))
        fake.views["dev-lead"] = _DEVIN_IDLE_234
        return True

    fake.press = press
    assert main(["say", "lead", body]) == int(edited)
    assert fake.sent == [("dev-lead", f"[team from dev-codex] {body} {denied}")]
    if edited:
        assert fake.keys == []
        assert "uncertain (staged Devin text differs from send)" in capsys.readouterr().out
    else:
        assert fake.keys == [["enter"]]
        assert capsys.readouterr().out.endswith("submitted\n")


def test_cli_pending_retry_keeps_send_away_from_compacting_devin(fake, capsys):
    from hx.__main__ import main

    fake.members["dev-lead"].update(kind="devin", session_id="ses-devin")
    fake.views["dev-lead"] = _DEVIN_IDLE_234.replace(
        "Ask Devin to build features, fix bugs, or work on your code", "existing draft")
    assert main(["say", "lead", "brief ends here"]) == 1
    assert "pending (composer not empty)" in capsys.readouterr().out
    fake.views["dev-lead"] = _DEVIN_IDLE_234
    led = ledger.default_ledger()
    led.mutate_leases(lambda leases: leases["sessions"].update({
        "ses-devin": {"agent": "dev-lead", "session_id": "ses-devin",
                      "token": "fixture", "state": "compacting"},
    }))

    assert main(["say", "--pending"]) == 1
    assert "pending (compaction compacting)" in capsys.readouterr().out
    assert fake.sent == []
    assert fake.keys == []

    led.mutate_leases(lambda leases: leases["sessions"].pop("ses-devin"))
    assert main(["say", "--pending"]) == 0
    assert len(fake.sent) == 1


@pytest.mark.parametrize("fixture", [_DEVIN_IDLE_234, _DEVIN_QUEUED_234])
def test_devin_v3000_composer_is_identified_and_empty(fixture):
    """Q-616: Devin v3000.11.234 keeps `❭` above the footer rule; the
    placeholder means nothing is staged."""
    staged = host._composer_text(fixture, "devin")
    assert staged == "Ask Devin to build features, fix bugs, or work on your code"
    assert host._composer_empty(staged, "devin")
    assert host._submit_key(fixture, "devin") == ("enter", "submitted")


def test_devin_queue_holds_only_the_sent_text():
    """The `── 1 queued ──` banner's `○` rows hold sends waiting for Enter."""
    assert host._queue_holds(_DEVIN_QUEUED_234, "devin", _DEVIN_QUEUED_TEXT)
    assert not host._queue_holds(_DEVIN_QUEUED_234, "devin", "a different message")
    assert not host._queue_holds(_DEVIN_IDLE_234, "devin", _DEVIN_QUEUED_TEXT)


@pytest.mark.parametrize("fixture", [_DEVIN_IDLE_234, _DEVIN_QUEUED_234])
def test_devin_history_read_is_composer_evidence(fake, capsys, fixture):
    """Q-616, 18 Nov: an idle Devin pane reads how=history, complete=False
    (scrollback outgrew the buffer); that was refused as no composer and the
    send never ran. A history read ends at the same live screen, so its
    composer anchors count."""
    fake.members["dev-lead"]["kind"] = "devin"
    fake.views["dev-lead"] = host.ReadText(
        fixture, complete=False, why="printed", how="history")
    assert say.say("lead", "brief ends here") == 0
    assert capsys.readouterr().out.endswith("submitted\n")
    assert len(fake.sent) == 1


def test_devin_queued_send_is_flushed_by_one_enter(fake, capsys):
    """Q-616, second shape: the send lands in Devin's `1 queued` list and the
    composer shows the placeholder. One Enter on the empty input sends it."""
    fake.members["dev-lead"]["kind"] = "devin"
    fake.views["dev-lead"] = _DEVIN_IDLE_234
    fake.advance = False  # a client-side queue never advances the turn

    def queue_our_send(name):
        fake.views["dev-lead"] = _DEVIN_QUEUED_234.replace(
            "[team from dev-lead] New brief: read .hx/briefs/q632-e2e-\n"
            "  repair.md and do exactly that.",
            fake.sent[-1][1])
    fake.on_send = queue_our_send

    def flush(member, *keys):
        fake.keys.append(list(keys))
        fake.views["dev-lead"] = _DEVIN_IDLE_234  # Enter sent the queue
        return True
    fake.press = flush
    assert say.say("lead", "brief ends here") == 0
    assert capsys.readouterr().out.endswith("submitted\n")
    assert fake.keys == [["enter"]]
    assert len(fake.sent) == 1


# ---------- Q-862: the OpenCode plugin's message ack is submission evidence ----------


def _opencode_ack(tmp_path: Path, address: str, text: str, received_ms: int) -> None:
    """The ack the hc-status plugin leaves in the terminal's binding file."""
    directory = tmp_path / "state" / "hc-bindings"
    directory.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256("".join(text.split()).encode("utf-8")).hexdigest()
    (directory / f"{address}.json").write_text(json.dumps({
        "v": 2, "terminal": address, "provider": "opencode",
        "chat_ack": {"text_sha256": digest, "received_ms": received_ms},
    }), encoding="utf-8")


def _busy_opencode_sidebar() -> str:
    screen = (_Q572 / "opencode-home.txt").read_text(encoding="utf-8")
    rows = screen.splitlines()
    width = max(len(row) for row in rows)
    return "\n".join(row.ljust(width + 2) + "│ Todo: finish report" for row in rows)


def test_busy_opencode_pane_ack_proves_submission(fake, capsys, tmp_path, monkeypatch):
    """Q-862: a busy OpenCode pane keeps its turn, so the fresh message ack in
    its binding file is the submission evidence — no key press needed."""
    monkeypatch.setenv("HX_STATE_DIR", str(tmp_path / "state"))
    fake.members["dev-lead"].update(kind="opencode", status="working")
    fake.advance = False
    fake.views["dev-lead"] = _busy_opencode_sidebar()
    _opencode_ack(tmp_path, "t24", "[team from dev-codex] brief ends here",
                  int(time.time() * 1000) + 60_000)
    assert say.say("lead", "brief ends here") == 0
    assert capsys.readouterr().out.endswith("submitted\n")
    assert fake.keys == []


@pytest.mark.parametrize("case", ["missing", "stale", "different"])
def test_opencode_ack_gaps_stay_uncertain(fake, capsys, tmp_path, monkeypatch, case):
    """A missing, older, or different ack is never evidence for this send."""
    monkeypatch.setenv("HX_STATE_DIR", str(tmp_path / "state"))
    fake.members["dev-lead"].update(kind="opencode", status="working")
    fake.advance = False
    fake.views["dev-lead"] = _busy_opencode_sidebar()
    if case == "stale":
        _opencode_ack(tmp_path, "t24", "[team from dev-codex] brief ends here",
                      int(time.time() * 1000) - 60_000)
    elif case == "different":
        _opencode_ack(tmp_path, "t24", "a different recent message",
                      int(time.time() * 1000) + 60_000)
    assert say.say("lead", "brief ends here") == 1
    assert "uncertain (empty composer without submission evidence)" in capsys.readouterr().out
    assert fake.keys == []


def test_devin_foreign_queue_never_earns_enter(fake, capsys):
    """A queued message that is not ours gets no key and no receipt."""
    fake.members["dev-lead"]["kind"] = "devin"
    fake.views["dev-lead"] = _DEVIN_QUEUED_234
    fake.advance = False
    assert say.say("lead", "brief ends here") == 1
    assert "uncertain" in capsys.readouterr().out
    assert fake.keys == []


def test_devin_send_still_queued_after_enter_is_pending(fake, capsys):
    fake.members["dev-lead"]["kind"] = "devin"
    fake.views["dev-lead"] = _DEVIN_IDLE_234
    fake.advance = False

    def queue_our_send(name):
        fake.views["dev-lead"] = _DEVIN_QUEUED_234.replace(
            "[team from dev-lead] New brief: read .hx/briefs/q632-e2e-\n"
            "  repair.md and do exactly that.",
            fake.sent[-1][1])
    fake.on_send = queue_our_send
    fake.press = lambda member, *keys: fake.keys.append(list(keys)) or True
    assert say.say("lead", "brief ends here") == 1
    assert "pending (send still queued after enter)" in capsys.readouterr().out
    assert fake.keys == [["enter"]]


@pytest.mark.parametrize("phase", ["before", "after", "recheck"])
def test_incomplete_composer_evidence_never_earns_success(fake, capsys, phase):
    fake.members["dev-lead"]["kind"] = "codex"
    incomplete = host.ReadText(_CODEX_IDLE_EMPTY, complete=False)
    fake.views["dev-lead"] = incomplete if phase == "before" else _CODEX_IDLE_EMPTY
    fake.on_send = lambda name: fake.views.update({name:
        incomplete if phase == "after" else _CODEX_IDLE_STAGED})
    def press(member, *keys):
        fake.keys.append(list(keys))
        fake.views["dev-lead"] = incomplete
        return True
    fake.press = press
    assert say.say("lead", "brief ends here") == 1
    assert "uncertain" in capsys.readouterr().out
    assert len(fake.sent) == (0 if phase == "before" else 1)
    assert fake.keys == ([["enter"]] if phase == "recheck" else [])


def test_live_screen_read_of_a_working_codex_is_composer_evidence(fake, capsys):
    """hc answers complete=False, how=screen, why=working for a live Codex pane (16 Nov)."""
    fake.members["dev-lead"]["kind"] = "codex"
    fake.views["dev-lead"] = host.ReadText(
        _CODEX_IDLE_EMPTY, complete=False, why="working", how="screen")
    fake.on_send = lambda name: fake.views.update({name: host.ReadText(
        _CODEX_IDLE_EMPTY, complete=False, why="working", how="screen")})
    assert say.say("lead", "brief ends here") == 0
    assert "submitted" in capsys.readouterr().out
    assert len(fake.sent) == 1


def test_failed_submit_key_keeps_message_pending(fake, capsys):
    fake.members["dev-lead"]["kind"] = "codex"
    fake.views["dev-lead"] = _CODEX_IDLE_EMPTY
    fake.on_send = lambda name: fake.views.update({name: _CODEX_IDLE_STAGED})
    fake.press = lambda member, *keys: False
    assert say.say("lead", "brief ends here") == 1
    assert "pending (paste staged; enter press failed)" in capsys.readouterr().out


def test_cli_pending_retry_keeps_foreign_draft_untouched(fake, capsys):
    from hx.__main__ import main
    fake.members["dev-lead"]["kind"] = "codex"
    fake.views["dev-lead"] = _CODEX_FOREIGN_DRAFT
    assert main(["say", "lead", "brief ends here"]) == 1
    assert "pending (composer not empty)" in capsys.readouterr().out
    assert main(["say", "--pending"]) == 1
    assert fake.sent == []
    assert fake.keys == []
    fake.views["dev-lead"] = _CODEX_IDLE_EMPTY
    assert main(["say", "--pending"]) == 0
    assert capsys.readouterr().out.endswith("submitted\n")
    assert len(fake.sent) == 1
