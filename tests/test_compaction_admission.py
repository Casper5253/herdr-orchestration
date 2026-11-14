"""Compaction admission: ordinary work waits while a compaction is active or unknown."""

from __future__ import annotations

import copy
import datetime
import json
import threading
import time
from pathlib import Path

import pytest

from hx import context, host, ledger, say, usage
from hx.__main__ import main


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
        self.entered = threading.Event()
        self.release = threading.Event()
        self.block_once = False

    def own(self) -> host.Member:
        return self.members["dev-codex"]

    def team(self, prefix: str) -> dict[str, host.Member]:
        return {m["name"]: m for m in self.members.values() if m["name"].startswith(prefix)}

    def info(self, name: str) -> host.Member:
        return self.members[name]

    def send_checked(self, name: str, text: str, **_: object) -> host.SendResult:
        if self.block_once:
            self.block_once = False
            self.entered.set()
            self.release.wait(5)
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
        leases = json.loads(led.leases_path.read_text(encoding="utf-8"))
        lease = leases["sessions"]["ses-target"]
        assert lease["state"] == "compacting"
        assert lease.get("token")
        c.sample.update(utilisation=.2, sampled_at=context._now().isoformat())
        return True

    monkeypatch.setattr(context, "_send_slash", send_slash)
    return c


def _seed_operation(led: ledger.Ledger, agent: str = "dev-opencode",
                    sid: str = "ses-target", *, state: str = "compacting",
                    phase: str = "requested", before: float | None = 1.03,
                    requested_at: str | None = None, token: str = "op-1") -> None:
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
    }
    if before is not None:
        record["before_utilisation"] = before
        record["before_sampled_at"] = stamp

    def add(leases: dict) -> None:
        leases.setdefault("sessions", {})[sid] = record

    led.mutate_leases(add)


def _record(tmp_path: Path) -> dict:
    paths = list((tmp_path / ".hx" / "outbox" / "dev__codex").glob("*.json"))
    assert len(paths) == 1
    return json.loads(paths[0].read_text(encoding="utf-8"))


def test_ordinary_work_waits_for_compaction_and_retry_delivers(
    fake: FakeHc, led: ledger.Ledger, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed_operation(led)
    assert say.say("opencode", "continue the task") == 1
    assert fake.sent == []
    record = _record(tmp_path)
    assert record["state"] == "pending"
    assert "compaction" in record["note"]

    led.mutate_leases(lambda leases: leases["sessions"].pop("ses-target", None))
    assert say.retry_pending() == 0
    assert len(fake.sent) == 1
    assert _record(tmp_path)["state"] == "delivered"


def test_lead_report_bypasses_compaction_hold(fake: FakeHc, led: ledger.Ledger) -> None:
    _seed_operation(led, agent="dev-lead", sid="ses-dev-lead")
    assert say.say("lead", "[team report from dev-codex] STATUS: done") == 0
    assert len(fake.sent) == 1
    assert json.loads(led.leases_path.read_text(encoding="utf-8")).get("deliveries", {}) == {}


def test_explicit_control_bypasses_compaction_hold(fake: FakeHc, led: ledger.Ledger) -> None:
    _seed_operation(led)
    assert say.say("opencode", "recovery instructions", control=True) == 0
    assert len(fake.sent) == 1


def test_stale_103_percent_does_not_release_hold(
    controller: context.Controller, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_operation(controller.ledger)
    controller.sample.update(utilisation=1.03, sampled_at=context._now().isoformat())
    assert controller.recover("dev-opencode")["action"] == "keep"

    controller.sample.update(utilisation=.2, sampled_at=context._now().isoformat())
    assert controller.recover("dev-opencode")["action"] == "recovered"


def test_reserved_operation_releases_without_keys(controller: context.Controller) -> None:
    _seed_operation(controller.ledger, state="compacting", phase="reserved", before=.7)
    controller.sample.update(utilisation=.7, sampled_at=context._now().isoformat())
    assert controller.recover("dev-opencode")["action"] == "recovered"
    assert json.loads(controller.ledger.leases_path.read_text(encoding="utf-8"))["sessions"] == {}


def test_session_replacement_releases_only_obsolete_operation(
    controller: context.Controller
) -> None:
    _seed_operation(controller.ledger, sid="ses-old", token="old")
    _seed_operation(controller.ledger, agent="dev-codex", sid="ses-codex", token="other")
    controller.member["session_id"] = "ses-new"
    controller.sample.update(session_id="ses-new", utilisation=.2, sampled_at=context._now().isoformat())
    assert controller.recover("dev-opencode")["action"] == "recovered"
    sessions = json.loads(controller.ledger.leases_path.read_text(encoding="utf-8"))["sessions"]
    assert "ses-old" not in sessions
    assert "ses-codex" in sessions


def test_quick_compaction_reserves_before_keys(controller: context.Controller) -> None:
    assert controller.compact_quick("dev-opencode")["action"] == "compacted"
    assert controller.calls == [("dev-opencode", "/compact")]
    assert json.loads(controller.ledger.leases_path.read_text(encoding="utf-8"))["sessions"] == {}


def test_quick_compaction_interrupt_leaves_unknown(
    controller: context.Controller, monkeypatch: pytest.MonkeyPatch
) -> None:
    def interrupt(member: host.Member, command: str) -> bool:
        raise KeyboardInterrupt

    monkeypatch.setattr(context, "_send_slash", interrupt)
    with pytest.raises(KeyboardInterrupt):
        controller.compact_quick("dev-opencode")
    lease = json.loads(controller.ledger.leases_path.read_text(encoding="utf-8"))["sessions"]["ses-target"]
    assert lease["state"] == "compact-unknown"
    assert lease["phase"] == "unknown"
    assert lease.get("token")


def test_two_concurrent_sends_one_waits_then_retry(
    fake: FakeHc, led: ledger.Ledger, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(say, "DELIVERY_WAIT_SECONDS", .05)
    fake.block_once = True
    results: list[int] = []
    first = threading.Thread(target=lambda: results.append(say.say("opencode", "first")))
    first.start()
    assert fake.entered.wait(2)
    second = threading.Thread(target=lambda: results.append(say.say("opencode", "second")))
    second.start()
    second.join(5)
    fake.release.set()
    first.join(5)
    assert sorted(results) == [0, 1]
    assert len(fake.sent) == 1

    assert say.retry_pending() == 0
    assert len(fake.sent) == 2


def test_compaction_refuses_while_delivery_in_flight(controller: context.Controller) -> None:
    controller.ledger.mutate_leases(lambda leases: leases.setdefault("deliveries", {}).update({
        "dev-opencode": {
            "agent": "dev-opencode", "holder": "record-1", "session_id": "ses-target",
            "acquired_at": context._now().isoformat(), "heartbeat_at": context._now().isoformat(),
        }
    }))
    result = controller.compact_quick("dev-opencode")
    assert result["action"] == "keep"
    assert "delivery" in result["reason"]
    assert controller.calls == []


def test_recompute_preserves_deliveries(led: ledger.Ledger) -> None:
    led.mutate_leases(lambda leases: leases.setdefault("deliveries", {}).update({
        "dev-opencode": {"agent": "dev-opencode", "holder": "r", "heartbeat_at": context._now().isoformat()}
    }))
    led.add_task("t1", "Task", "dev-opencode", ["owned.py"], base_commit="base")
    leases = json.loads(led.leases_path.read_text(encoding="utf-8"))
    assert leases["deliveries"]["dev-opencode"]["holder"] == "r"


def test_cli_quick_compaction_routes_to_quick(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[str] = []
    monkeypatch.setattr(context, "Controller", lambda: type("C", (), {
        "compact_quick": lambda self, name: called.append(name) or {"action": "compacted"},
    })())
    assert main(["compact", "dev-opencode", "--quick"]) == 0
    assert called == ["dev-opencode"]

def test_quick_compaction_failure_before_keys_releases_reservation(
    controller: context.Controller, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = {"count": 0}
    original = context.agent_info

    def info(name: str) -> host.Member:
        calls["count"] += 1
        member = original(name)
        if calls["count"] == 2:
            member["status"] = "working"
        return member

    monkeypatch.setattr(context, "agent_info", info)
    result = controller.compact_quick("dev-opencode")
    assert result["action"] == "keep"
    assert json.loads(controller.ledger.leases_path.read_text(encoding="utf-8"))["sessions"] == {}


def test_stale_delivery_reservation_is_reclaimed(
    fake: FakeHc, led: ledger.Ledger
) -> None:
    old = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=10)).isoformat()
    led.mutate_leases(lambda leases: leases.setdefault("deliveries", {}).update({
        "dev-opencode": {"agent": "dev-opencode", "holder": "dead", "heartbeat_at": old}
    }))
    assert say.say("opencode", "brief") == 0
    assert len(fake.sent) == 1


def test_obsolete_session_operation_does_not_block_work(
    fake: FakeHc, led: ledger.Ledger
) -> None:
    _seed_operation(led, sid="ses-old")
    assert say.say("opencode", "brief") == 0
    assert len(fake.sent) == 1


def test_recovery_wrong_token_keeps_operation(controller: context.Controller) -> None:
    _seed_operation(controller.ledger, token="op-1")
    assert controller.recover("dev-opencode", token="nope")["action"] == "keep"
    assert "ses-target" in json.loads(controller.ledger.leases_path.read_text(encoding="utf-8"))["sessions"]


def test_recovery_token_releases_only_that_operation(controller: context.Controller) -> None:
    _seed_operation(controller.ledger, sid="ses-target", token="one")
    _seed_operation(controller.ledger, sid="ses-other", token="two")
    assert controller.recover("dev-opencode", token="two")["action"] == "recovered"
    sessions = json.loads(controller.ledger.leases_path.read_text(encoding="utf-8"))["sessions"]
    assert "ses-target" in sessions
    assert "ses-other" not in sessions


def test_send_runs_outside_leases_lock(
    fake: FakeHc, led: ledger.Ledger, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = fake.send_checked

    def checked(name: str, text: str, **kwargs: object) -> host.SendResult:
        assert not led.leases_lock_path.exists()
        assert not led.lock_path.exists()
        return original(name, text, **kwargs)

    monkeypatch.setattr(fake, "send_checked", checked)
    assert say.say("opencode", "brief") == 0
    assert len(fake.sent) == 1


def test_unknown_quick_compaction_holds_work_then_recovery_delivers(
    controller: context.Controller, fake: FakeHc, monkeypatch: pytest.MonkeyPatch
) -> None:
    def interrupt(member: host.Member, command: str) -> bool:
        raise KeyboardInterrupt

    monkeypatch.setattr(context, "_send_slash", interrupt)
    with pytest.raises(KeyboardInterrupt):
        controller.compact_quick("dev-opencode")
    assert say.say("opencode", "brief") == 1
    assert fake.sent == []

    controller.sample.update(utilisation=.2, sampled_at=context._now().isoformat())
    assert controller.recover("dev-opencode")["action"] == "recovered"
    assert say.retry_pending() == 0
    assert len(fake.sent) == 1

def test_legacy_compacting_lease_without_phase_is_recoverable(
    controller: context.Controller
) -> None:
    _seed_operation(controller.ledger, state="compacting", phase=None, before=None)
    controller.sample.update(utilisation=.7, sampled_at=context._now().isoformat())
    assert controller.recover("dev-opencode")["action"] == "recovered"


def test_devin_pane_session_identity_ignores_obsolete_operation(
    fake: FakeHc, led: ledger.Ledger
) -> None:
    fake.members["dev-opencode"] = _member(
        "dev-opencode", kind="devin", session_id="", address="t13")
    _seed_operation(led, sid="devin-pane:old")
    assert say.say("opencode", "brief") == 0
    assert len(fake.sent) == 1

def test_quick_compaction_send_not_attempted_releases_reservation(
    controller: context.Controller, monkeypatch: pytest.MonkeyPatch
) -> None:
    def not_attempted(member: host.Member, command: str) -> bool:
        raise host.SendNotAttempted("no address")

    monkeypatch.setattr(context, "_send_slash", not_attempted)
    result = controller.compact_quick("dev-opencode")
    assert result["action"] == "keep"
    assert json.loads(controller.ledger.leases_path.read_text(encoding="utf-8"))["sessions"] == {}
