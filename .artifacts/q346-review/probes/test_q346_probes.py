"""Cross-family review probes for Q-346 compaction admission.

Each test asserts the behavior the goal requires, so a failure documents a
real gap in the worktree code. Run:

    python -m pytest .artifacts/q346-review/probes/test_q346_probes.py -q
"""

from __future__ import annotations

import copy
import datetime
import json
import threading
from pathlib import Path

import pytest

from hx import context, host, ledger, say, usage


POLICY = {
    "team_prefix": "dev-",
    "lead_role": "lead",
    "say": {"work_ttl_seconds": 86400},
    "context": {
        "pressure_threshold": .75,
        "compact_wait_seconds": 0,
        "verification_timeout_seconds": 0,
    },
}


def _member(name: str, role: str | None = None, kind: str = "opencode",
            status: str = "idle", address: str = "t1", session_id: str | None = None,
            turn: int = 4) -> host.Member:
    team = name.rsplit("-", 1)[0]
    return host.Member(
        name=name, team=team, role=role or name.rsplit("-", 1)[-1], kind=kind,
        status=status, cwd="C:\\repo", address=address,
        session_id=session_id or f"ses-{name}", session_reason=None,
        focused=None, turn=turn)


class FakeHc:
    def __init__(self) -> None:
        self.members = {
            "dev-codex": _member("dev-codex", kind="codex", address="t9"),
            "dev-lead": _member("dev-lead", role="lead", kind="claude", address="t24"),
            "dev-opencode": _member("dev-opencode", address="t13", session_id="ses-target"),
        }
        self.sent: list[tuple[str, str]] = []

    def own(self) -> host.Member:
        return self.members["dev-codex"]

    def team(self, prefix: str) -> dict[str, host.Member]:
        return {m["name"]: m for m in self.members.values() if m["name"].startswith(prefix)}

    def info(self, name: str) -> host.Member:
        return self.members[name]

    def send_checked(self, name: str, text: str, **_: object) -> host.SendResult:
        self.sent.append((name, text))
        self.members[name]["turn"] += 1
        return host.SendResult(started=True, status="idle", turn=4, ended=False, reason=None)

    def send_slash(self, member: host.Member, command: str) -> bool:
        return True

    def read(self, name: str, lines: int = 200) -> str:
        return "some output\n>"

    def press(self, member: host.Member, *keys: str) -> bool:
        return True


@pytest.fixture
def led(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ledger.Ledger:
    monkeypatch.setenv("HX_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("HX_LEDGER_PATH", str(tmp_path / ".hx" / "ledger.json"))
    monkeypatch.setenv("HX_LOCK_PATH", str(tmp_path / ".hx" / "ledger.lock"))
    monkeypatch.setenv("HX_LEASES_PATH", str(tmp_path / "leases.json"))
    monkeypatch.setenv("HX_LEASES_LOCK_PATH", str(tmp_path / "leases.lock"))
    monkeypatch.setenv("HX_CONTEXT_RESETS_PATH", str(tmp_path / "resets.json"))
    return ledger.Ledger(
        repo_root=tmp_path,
        ledger_path=tmp_path / ".hx" / "ledger.json",
        lock_path=tmp_path / ".hx" / "ledger.lock",
        leases_path=tmp_path / "leases.json",
    )


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch, led: ledger.Ledger) -> FakeHc:
    hc = FakeHc()
    monkeypatch.setattr(host, "detect", lambda: hc)
    monkeypatch.setattr(say, "_under_pressure", lambda name: False)
    monkeypatch.setattr(say, "_denied_line_for", lambda destination: "")
    monkeypatch.setattr(usage, "load_policy", lambda: POLICY)
    return hc


@pytest.fixture
def controller(monkeypatch: pytest.MonkeyPatch, led: ledger.Ledger) -> context.Controller:
    c = context.Controller(led, POLICY)
    c.member = _member("dev-opencode", session_id="ses-target")
    c.sample = {"session_id": "ses-target", "utilisation": .7,
                "sampled_at": context._now().isoformat()}
    c.calls: list[tuple[str, str]] = []
    monkeypatch.setattr(context, "agent_info", lambda name: copy.deepcopy(c.member))
    monkeypatch.setattr(context, "telemetry", lambda *args: dict(c.sample))

    def send_slash(member: host.Member, command: str) -> bool:
        c.calls.append((member["name"], command))
        return True

    monkeypatch.setattr(context, "_send_slash", send_slash)
    return c


def _seed_operation(led: ledger.Ledger, agent: str = "dev-opencode",
                    sid: str = "ses-target", *, state: str = "compacting",
                    phase: str = "requested", token: str = "op-1") -> None:
    stamp = (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=30)
    ).isoformat()
    record = {
        "agent": agent,
        "token": token,
        "session_id": sid,
        "state": state,
        "phase": phase,
        "source": "guarded",
        "requested_at": stamp,
        "reserved_at": stamp,
        "before_utilisation": .9,
        "before_sampled_at": stamp,
    }
    led.mutate_leases(
        lambda leases: leases.setdefault("sessions", {}).__setitem__(sid, record))


def _seed_reset_hold(led: ledger.Ledger, agent: str = "dev-opencode",
                     state: str = "reset-unknown") -> None:
    stamp = (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=30)
    ).isoformat()
    led.mutate_leases(
        lambda leases: leases.setdefault("sessions", {}).__setitem__(
            f"reset:{agent}",
            {"agent": agent, "state": state, "reset_of": agent,
             "session_id": "ses-old", "requested_at": stamp}))


# --- P1: hx compact --quick ignores a live reset hold -------------------------
#
# reset:<agent> entries carry no token, so compaction_operations never sees
# them. compact_quick reserves and types /compact while an hx reset is in
# flight or unverified; the guarded path refuses the same state through
# _has_blocking_session_lease.

@pytest.mark.parametrize("reset_state", ["resetting", "reset-unknown"])
def test_quick_compaction_must_refuse_while_reset_unverified(
    controller: context.Controller, reset_state: str
) -> None:
    _seed_reset_hold(controller.ledger, state=reset_state)
    result = controller.compact_quick("dev-opencode")
    assert result["action"] == "keep", (
        f"quick compaction typed {controller.calls} while a {reset_state} reset hold exists")
    assert controller.calls == []


# --- P2: hx reset ignores compaction operations and delivery holds ------------
#
# reset's reserve only checks sessions["reset:<name>"]. A compaction operation
# on sessions[sid] and a per-target deliveries reservation are both invisible,
# so /new or /clear keys can land while /compact verification or an hx say
# paste is in flight on the same pane.

def test_reset_must_refuse_during_active_compaction(
    controller: context.Controller
) -> None:
    _seed_operation(controller.ledger)
    with pytest.raises(RuntimeError):
        controller.reset("dev-opencode")
    assert controller.calls == [], (
        f"reset keys {controller.calls} went out while a compaction operation was active")


def test_reset_must_refuse_during_delivery_in_flight(
    controller: context.Controller
) -> None:
    controller.ledger.mutate_leases(
        lambda leases: leases.setdefault("deliveries", {}).__setitem__(
            "dev-opencode",
            {"agent": "dev-opencode", "holder": "record-1", "session_id": "ses-target",
             "acquired_at": context._now().isoformat(),
             "heartbeat_at": context._now().isoformat()}))
    with pytest.raises(RuntimeError):
        controller.reset("dev-opencode")
    assert controller.calls == [], (
        f"reset keys {controller.calls} went out while a delivery was in flight")


# --- P3: a delivery entry without timestamps never expires --------------------
#
# delivery_hold returns the entry when heartbeat_at/acquired_at is missing or
# unparseable, and hx context recover only touches sessions, so nothing ever
# releases the slot: sends and compactions on that target block forever.

def test_delivery_without_timestamp_must_not_block_forever(
    fake: FakeHc, led: ledger.Ledger, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(say, "DELIVERY_WAIT_SECONDS", .05)
    led.mutate_leases(
        lambda leases: leases.setdefault("deliveries", {}).__setitem__(
            "dev-opencode", {"agent": "dev-opencode", "holder": "dead"}))
    leases = json.loads(led.leases_path.read_text(encoding="utf-8"))
    far_future = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=1)
    assert ledger.delivery_hold(leases, "dev-opencode", far_future) is None, (
        "a delivery entry without a parseable timestamp is held forever")
    assert say.say("opencode", "brief") == 0


# --- P4: recover cannot release an obsolete op while the live sid is unknown --
#
# The obsolete-session release needs a truthy live_sid; with an unidentified
# live session every candidate falls into the proof branches, which can never
# pass for a session that no longer exists.

def test_recover_releases_obsolete_op_when_live_session_unknown(
    controller: context.Controller
) -> None:
    _seed_operation(controller.ledger, sid="ses-old")
    controller.member["session_id"] = ""
    controller.sample = {"session_id": "", "utilisation": None, "sampled_at": None}
    result = controller.recover("dev-opencode")
    assert result["action"] == "recovered", (
        f"obsolete operation on ses-old is unreleasable while live sid is unknown: {result}")
