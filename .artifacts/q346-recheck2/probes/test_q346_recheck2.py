"""Q-346 recheck round 2 probes.

Each test asserts the behavior the recheck requires, so a failure documents a
real gap in the worktree code. Run:

    python -m pytest .artifacts/q346-recheck2/probes -q
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
            "dev-lead": _member("dev-lead", role="lead", kind="opencode",
                                address="t24", session_id="lead-sid"),
            "dev-opencode": _member("dev-opencode", address="t13", session_id="ses-target"),
        }
        self.sent: list[tuple[str, str]] = []
        self.fail_once = False

    def own(self) -> host.Member:
        return self.members["dev-codex"]

    def team(self, prefix: str) -> dict[str, host.Member]:
        return {m["name"]: m for m in self.members.values() if m["name"].startswith(prefix)}

    def info(self, name: str) -> host.Member:
        return self.members[name]

    def send_checked(self, name: str, text: str, **_: object) -> host.SendResult:
        if self.fail_once:
            self.fail_once = False
            raise host.SendNotAttempted("blocked")
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
    monkeypatch.setenv("HX_HERDR_PREFIX", "dev-")
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
    monkeypatch.setattr(say, "_submission_evidence", lambda *a, **k: "submitted")
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
                    phase: str = "requested", token: str = "op-1",
                    requested_at: str | None = None) -> None:
    stamp = requested_at or (
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
                     sid: str = "ses-old", state: str = "reset-unknown") -> None:
    record = {
        "agent": agent,
        "state": state,
        "reset_of": agent,
        "session_id": sid,
        "requested_at": (
            datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=30)
        ).isoformat(),
    }
    led.mutate_leases(
        lambda leases: leases.setdefault("sessions", {}).__setitem__(
            f"reset:{agent}", record))


def _record(tmp_path: Path) -> dict:
    paths = list((tmp_path / ".hx" / "outbox" / "dev__codex").glob("*.json"))
    assert len(paths) == 1
    return json.loads(paths[0].read_text(encoding="utf-8"))


# Finding 1 root: a forced message that goes pending for any reason (here a
# blocked destination, not the probe's SendNotAttempted) must stay control and
# bypass compaction on the --pending retry.
def test_forced_pending_after_blocked_destination_retries_as_control(
    fake: FakeHc, led: ledger.Ledger, tmp_path: Path
) -> None:
    fake.members["dev-opencode"]["status"] = "blocked"
    assert say.say("opencode", "recovery instructions", force=True) == 1
    record = _record(tmp_path)
    assert record["state"] == "pending"
    assert record["control"] is True
    _seed_operation(led)
    fake.members["dev-opencode"]["status"] = "idle"
    assert say.retry_pending() == 0
    assert len(fake.sent) == 1, (
        "a forced pending message lost its control flag on a blocked-destination retry")


def test_ordinary_pending_message_is_still_held_by_compaction(
    fake: FakeHc, led: ledger.Ledger, tmp_path: Path
) -> None:
    fake.members["dev-opencode"]["status"] = "blocked"
    assert say.say("opencode", "ordinary brief") == 1
    record = _record(tmp_path)
    assert record["control"] is False
    _seed_operation(led)
    fake.members["dev-opencode"]["status"] = "idle"
    assert say.retry_pending() == 1
    assert fake.sent == [], (
        "an ordinary pending message bypassed an active compaction on retry")


# Finding 2 root: the unknown-live-session rule is shared by every held
# operation. A reset whose outcome is unknown must not be released just because
# the live session id is unknown; a busy pane is not proof the keys never landed.
def test_recover_keeps_reset_unknown_when_live_sid_is_unknown(
    controller: context.Controller
) -> None:
    _seed_reset_hold(controller.ledger, sid="ses-old", state="reset-unknown")
    controller.member["session_id"] = ""
    controller.member["status"] = "working"
    result = controller.recover("dev-opencode")
    assert result["action"] == "keep", (
        "an unknown live session id released a reset whose keys may still be in flight")
    assert "reset:dev-opencode" in controller.ledger._load_leases()["sessions"]


def test_recover_keeps_reset_unknown_when_live_sid_is_known_and_same(
    controller: context.Controller
) -> None:
    _seed_reset_hold(controller.ledger, sid="ses-target", state="reset-unknown")
    controller.member["session_id"] = "ses-target"
    controller.member["status"] = "working"
    assert controller.recover("dev-opencode")["action"] == "keep"


# Finding 3 root: the guarded reserve consults the shared rule for real state,
# not only through a patched key_stream_reason. A live delivery hold and a live
# reset hold must both stop the keys.
@pytest.fixture
def guarded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> context.Controller:
    monkeypatch.delenv("HC_TERMINAL_ID", raising=False)
    monkeypatch.delenv("HERDR_PANE_ID", raising=False)
    monkeypatch.setenv("HX_HERDR_PREFIX", "hx-")
    led = ledger.Ledger(
        repo_root=tmp_path, ledger_path=tmp_path / "ledger.json",
        leases_path=tmp_path / "leases.json", lock_path=tmp_path / "lock")
    led.add_task("t", "Completed work", "hx-codex", ["owned.py"], base_commit="base")
    led.set_status("t", "done")
    c = context.Controller(led, {"team_prefix": "hx-", "lead_role": "lead",
                                 "context": {"verification_timeout_seconds": 0,
                                             "compact_wait_seconds": 0}})
    c.agent = host.member({"name": "hx-codex", "agent": "codex", "agent_status": "idle",
                           "agent_session": {"value": "sid"}, "cwd": str(tmp_path)})
    c.calls = []
    c.sample = {"session_id": "sid", "utilisation": .7,
                "sampled_at": context._now().isoformat()}
    monkeypatch.setattr(context, "agent_info", lambda name: copy.deepcopy(c.agent))
    monkeypatch.setattr(context, "telemetry", lambda *a: dict(c.sample))
    monkeypatch.setattr(context, "_send_slash",
                        lambda member, command: c.calls.append((member["name"], command)))
    return c


def test_guarded_compact_refuses_a_real_delivery_hold(guarded: context.Controller) -> None:
    guarded.ledger.mutate_leases(
        lambda leases: leases.setdefault("deliveries", {}).__setitem__(
            "hx-codex", {"agent": "hx-codex", "holder": "record-1",
                         "acquired_at": context._now().isoformat()}))
    assert guarded.compact("hx-codex")["action"] == "keep"
    assert guarded.calls == []


def test_guarded_compact_refuses_a_real_reset_hold(guarded: context.Controller) -> None:
    _seed_reset_hold(guarded.ledger, agent="hx-codex", sid="sid", state="reset-unknown")
    assert guarded.compact("hx-codex")["action"] == "keep"
    assert guarded.calls == []


# Finding 4 root: a control send is a key stream. While one is in flight to the
# lead, the lead's guarded quick compaction must not type /compact into the same
# pane. Lead reports are control by role, so they take no delivery reservation
# and the shared rule cannot see them.
def test_lead_quick_compact_waits_for_an_in_flight_control_send(
    monkeypatch: pytest.MonkeyPatch, led: ledger.Ledger
) -> None:
    hc = FakeHc()
    started = threading.Event()
    release = threading.Event()
    original = hc.send_checked

    def blocking_send(name: str, text: str, **kwargs: object) -> host.SendResult:
        if name == "dev-lead":
            started.set()
            release.wait(5)
        return original(name, text, **kwargs)

    monkeypatch.setattr(hc, "send_checked", blocking_send)
    monkeypatch.setattr(host, "detect", lambda: hc)
    monkeypatch.setattr(say, "_under_pressure", lambda name: False)
    monkeypatch.setattr(say, "_denied_line_for", lambda destination: "")
    monkeypatch.setattr(say, "_submission_evidence", lambda *a, **k: "submitted")
    monkeypatch.setattr(usage, "load_policy", lambda: POLICY)

    sender = threading.Thread(target=lambda: say.say("lead", "status report"))
    sender.start()
    assert started.wait(2), "the control send never started"
    try:
        # The control send holds no leases reservation, so the shared rule is blind.
        assert "dev-lead" not in led._load_leases().get("deliveries", {})

        c = context.Controller(led, POLICY)
        c.calls = []
        lead = hc.members["dev-lead"]
        sample = {"session_id": "lead-sid", "utilisation": .7,
                  "sampled_at": context._now().isoformat()}
        monkeypatch.setattr(context, "agent_info", lambda name: copy.deepcopy(lead))
        monkeypatch.setattr(context, "session_telemetry",
                            lambda *a: ("lead-sid", dict(sample)))
        monkeypatch.setattr(context, "telemetry", lambda *a: dict(sample))

        def send_slash(member: host.Member, command: str) -> bool:
            c.calls.append((member["name"], command))
            sample.update(utilisation=.2, sampled_at=context._now().isoformat())
            return True

        monkeypatch.setattr(context, "_send_slash", send_slash)
        result = c.compact_quick("dev-lead")
        assert result["action"] == "keep", (
            "the lead quick compaction typed /compact while a control send was in flight")
        assert c.calls == []
    finally:
        release.set()
        sender.join(5)


# Design invariant 5: every lease mutation preserves deliveries. A task release
# must not drop a live send reservation or another agent's compaction operation.
def test_task_release_preserves_deliveries_and_session_operations(
    led: ledger.Ledger
) -> None:
    led.add_task("t", "Released work", "dev-opencode", ["owned.py"])
    led.mutate_leases(
        lambda leases: leases.setdefault("deliveries", {}).__setitem__(
            "dev-opencode", {"agent": "dev-opencode", "holder": "record-1",
                             "acquired_at": context._now().isoformat()}))
    _seed_operation(led, sid="ses-target", state="compacting")
    led.release_task_reservations("t")
    data = led._load_leases()
    assert "dev-opencode" in data.get("deliveries", {}), (
        "task release dropped a live delivery reservation")
    assert "ses-target" in data.get("sessions", {}), (
        "task release dropped a live compaction operation")


def test_task_release_does_not_open_admission_during_compaction(
    led: ledger.Ledger
) -> None:
    led.add_task("t", "Released work", "dev-opencode", ["owned.py"])
    _seed_operation(led, sid="ses-target", state="compacting")
    led.release_task_reservations("t")
    admitted, reason = led.admit_delivery(
        "dev-opencode", "holder-1", session_id="ses-target")
    assert not admitted, (
        "task release erased the compaction hold and admitted an ordinary send")
