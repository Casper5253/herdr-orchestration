"""Q-839: hx team stalls detects a frozen working screen and alerts once."""

from __future__ import annotations

import json

import pytest

from hx import host, team_stalls


def member(name: str, status: str = "working", session: str = "s1",
           created: int = 1, turn: int = 1) -> host.Member:
    team, _, role = name.rpartition("-")
    return host.Member(
        name=name, team=team, role=role, kind="opencode", status=status,
        cwd="", address=name, session_id=session,
        session_reason="fixture", focused=None, turn=turn, created_ms=created,
    )


class FakeHost:
    def __init__(self, members, screens):
        self.members = {item["name"]: item for item in members}
        self.screens = screens
        self.sent = []
        self.reads = []
        self.unreadable = set()

    def team(self, prefix):
        return {name: item for name, item in self.members.items()
                if name.startswith(prefix)}

    def all_members(self):
        return list(self.members.values())

    def read(self, name, lines=team_stalls.SCREEN_LINES):
        if name in self.unreadable:
            raise RuntimeError("unreadable screen")
        self.reads.append(name)
        return self.screens[name]

    def send_checked(self, name, text, **_kwargs):
        self.sent.append((name, text))
        return {"started": True, "status": "working", "turn": 1,
                "ended": False, "reason": None}


POLICY = {"lead_role": "lead", "team_prefix": "dev-", "stall_minutes": 30}
LEAD = member("dev-lead", status="working", session="lead")
DEVIN = member("dev-devin")
CODEX = member("dev-codex", status="idle")


def fixture(tmp_path, screens=None):
    screens = screens or {
        "dev-devin": "⠋ Typing 131m 38s\n> work",
        "dev-codex": "⠙ Typing 131m 38s\n> work",
        "dev-lead": "⠹ Typing 131m 38s\n> work",
    }
    fake = FakeHost([LEAD, DEVIN], screens)
    state = tmp_path / "state" / "team-stalls.json"
    log = tmp_path / "state" / "stalls.log"
    return fake, state, log


def run(fake, state, log, now, *, policy=None, dry_run=False):
    return team_stalls.check(
        fake, team="dev", policy=policy or POLICY, now=now,
        state_path=state, log_path=log, dry_run=dry_run)


def test_spinner_timers_and_counters_do_not_count_as_change():
    a = "⠋ Typing 131m 38s\nTokens: 12,345\n> work"
    b = "⠙ Typing 9m 5s\nTokens: 98,765\n> work"
    assert team_stalls.digest(a) == team_stalls.digest(b)
    assert team_stalls.digest("\x1b[31mred\x1b[0m") == team_stalls.digest("red")
    assert team_stalls.digest(a) != team_stalls.digest(a + "\nnew line")


def test_a_working_screen_stalls_at_the_policy_threshold(tmp_path):
    fake, state, log = fixture(tmp_path)
    first = run(fake, state, log, 0)
    assert {row["name"] for row in first} == {"dev-devin"}
    assert not fake.sent

    assert not run(fake, state, log, 29 * 60)[0]["stalled"]
    stalled = run(fake, state, log, 30 * 60)
    assert stalled[0]["stalled"] is True
    assert stalled[0]["alert"] == "sent"
    assert len(fake.sent) == 1
    assert fake.sent[0][0] == "dev-lead"
    assert "dev-devin" in fake.sent[0][1]
    assert "30m" in fake.sent[0][1]
    assert "> work" in fake.sent[0][1]
    entry = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    assert entry["worker"] == "dev-devin"


def test_one_alert_per_stall_then_reset_on_screen_change(tmp_path):
    fake, state, log = fixture(tmp_path)
    run(fake, state, log, 0)
    run(fake, state, log, 30 * 60)
    assert len(fake.sent) == 1

    again = run(fake, state, log, 31 * 60)
    assert again[0]["alert"] == "already"
    assert len(fake.sent) == 1

    fake.screens["dev-devin"] += "\nnew output"
    reset = run(fake, state, log, 32 * 60)
    assert reset[0]["stalled"] is False
    assert reset[0]["alerted"] is False
    assert not run(fake, state, log, 61 * 60)[0]["stalled"]
    assert run(fake, state, log, 62 * 60)[0]["alert"] == "sent"
    assert len(fake.sent) == 2


def test_idle_and_blocked_workers_are_never_stalled(tmp_path):
    fake, state, log = fixture(tmp_path)
    fake.members["dev-devin"] = member("dev-devin", status="idle")
    fake.members["dev-codex"] = member("dev-codex", status="blocked")
    rows = run(fake, state, log, 120 * 60)
    assert all(row["stalled"] is False for row in rows)
    assert not fake.sent
    assert not log.exists()
    state_data = json.loads(state.read_text(encoding="utf-8"))
    assert state_data["workers"] == {}


def test_a_restart_resets_the_clock(tmp_path):
    fake, state, log = fixture(tmp_path)
    run(fake, state, log, 0)
    fake.members["dev-devin"] = member("dev-devin", session="s2")
    restarted = run(fake, state, log, 40 * 60)
    assert restarted[0]["stalled"] is False
    assert not fake.sent
    assert not run(fake, state, log, 69 * 60)[0]["stalled"]
    assert run(fake, state, log, 70 * 60)[0]["alert"] == "sent"


def test_unreadable_screen_is_unknown_not_stalled(tmp_path):
    fake, state, log = fixture(tmp_path)
    run(fake, state, log, 0)
    fake.unreadable.add("dev-devin")
    rows = run(fake, state, log, 120 * 60)
    devin = next(row for row in rows if row["name"] == "dev-devin")
    assert devin["stalled"] is False
    assert devin["note"] == "screen unreadable"
    assert not fake.sent


def test_dry_run_sends_logs_and_saves_nothing(tmp_path):
    fake, state, log = fixture(tmp_path)
    run(fake, state, log, 0)
    before = state.read_text(encoding="utf-8")
    rows = run(fake, state, log, 31 * 60, dry_run=True)
    assert rows[0]["alert"] == "would-alert"
    assert not fake.sent
    assert not log.exists()
    assert state.read_text(encoding="utf-8") == before


def test_policy_threshold_override(tmp_path):
    fake, state, log = fixture(tmp_path)
    run(fake, state, log, 0, policy={"lead_role": "lead", "stall_minutes": 5})
    assert not run(fake, state, log, 4 * 60, policy={
        "lead_role": "lead", "stall_minutes": 5})[0]["stalled"]
    assert run(fake, state, log, 5 * 60, policy={
        "lead_role": "lead", "stall_minutes": 5})[0]["alert"] == "sent"


def test_the_lead_is_never_checked(tmp_path):
    fake, state, log = fixture(tmp_path)
    rows = run(fake, state, log, 0)
    assert "dev-lead" not in {row["name"] for row in rows}
    assert "dev-lead" not in fake.reads

def test_devin_frozen_screen_from_21_nov_counts_as_unchanged():
    from hx import team_stalls
    first = "Now Q-823 tests 1b, 1c, new 1d, and 2d.\n\n⢰⠀ Typing · 131m 38s (esc twice to interrupt)\nSWE-2 High · bypass  Context: 115k / 262k tokens (43%)"
    later = "Now Q-823 tests 1b, 1c, new 1d, and 2d.\n\n⠤⠄ Typing · 132m 28s (esc twice to interrupt)\nSWE-2 High · bypass  Context: 115k / 262k tokens (43%)"
    assert team_stalls.digest(first) == team_stalls.digest(later)


def test_opencode_progress_bar_frames_count_as_unchanged():
    from hx import team_stalls
    first = "Thinking\n⬝⬝⬝⬝■■■■  esc interrupt  28.3K (3%)"
    later = "Thinking\n■■⬝⬝⬝⬝■■  esc interrupt  28.3K (3%)"
    assert team_stalls.digest(first) == team_stalls.digest(later)
