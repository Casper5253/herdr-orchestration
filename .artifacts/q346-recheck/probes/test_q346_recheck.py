"""Q-346 recheck probes.

Each test asserts the behavior the recheck requires, so a failure documents a
real gap in the worktree code. Run:

    python -m pytest .artifacts/q346-recheck/probes/test_q346_recheck.py -q
"""

from __future__ import annotations

import copy
import datetime
import json
from pathlib import Path

import pytest

from hx import checkpoint, context, host, ledger, say, usage


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
                    phase: str = "requested", token: str = "op-1",
                    requested_at: str | None = None, before: float | None = .9) -> None:
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
    led.mutate_leases(
        lambda leases: leases.setdefault("sessions", {}).__setitem__(sid, record))


def _record(tmp_path: Path) -> dict:
    paths = list((tmp_path / ".hx" / "outbox" / "dev__codex").glob("*.json"))
    assert len(paths) == 1
    return json.loads(paths[0].read_text(encoding="utf-8"))


# Finding 1: a forced control message is not stored as control, so a pending
# retry is treated as ordinary work and held by compaction admission.
def test_forced_message_is_stored_as_control(fake: FakeHc, led: ledger.Ledger,
                                             tmp_path: Path) -> None:
    fake.fail_once = True
    assert say.say("opencode", "recovery instructions", force=True) == 1
    record = _record(tmp_path)
    assert record["state"] == "pending"
    assert record["control"] is True, (
        "a forced control message must stay control in the outbox")


def test_forced_pending_retry_bypasses_compaction(fake: FakeHc, led: ledger.Ledger,
                                                  tmp_path: Path) -> None:
    fake.fail_once = True
    assert say.say("opencode", "recovery instructions", force=True) == 1
    _seed_operation(led)
    assert say.retry_pending() == 0
    assert len(fake.sent) == 1, (
        "the forced pending message was held by compaction admission on retry")


# Finding 2: with the live session id unknown, the new recover branch releases
# a held operation after a stale telemetry sample. A live but idle session also
# has stale telemetry, so quiet is not proof the session is dead.
def test_recover_keeps_live_held_session_when_live_sid_unknown(
    controller: context.Controller
) -> None:
    requested = (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=30)
    ).isoformat()
    _seed_operation(controller.ledger, sid="ses-old", state="compact-unknown",
                    phase="requested", requested_at=requested)
    controller.member["session_id"] = ""
    controller.sample = {
        "session_id": "ses-old",
        "utilisation": .7,
        "sampled_at": (
            datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=60)
        ).isoformat(),
    }
    result = controller.recover("dev-opencode")
    assert result["action"] == "keep", (
        "a live held session with stale telemetry was released; quiet is not death")


# Finding 3: the guarded compact path does not consult the shared rule. A
# synthetic block from key_stream_reason must stop the keys.
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
    cp = tmp_path / "checkpoint.md"
    cp.write_text(
        "## Decisions\nKeep the design.\n\n## Exact errors\nNone.\n\n"
        "## Next action\nRun acceptance.\n\n## References\nDocs.\n",
        encoding="utf-8")
    monkeypatch.setattr(checkpoint, "validate_checkpoint",
                        lambda *a, **kw: {"ok": True, "checkpoint": str(cp)})
    monkeypatch.setattr(checkpoint, "_run_git", lambda *a: (0, "", ""))
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

    def send_checked(name, text, **kwargs):
        c.calls.append((name, text, kwargs))
        return {"started": True, "status": "unknown", "turn": None, "ended": True,
                "reason": None}

    def send_slash(member, command):
        c.calls.append((member["name"], command, {}))
        if command == "/compact":
            lease = json.loads(led.leases_path.read_text())["sessions"]["sid"]
            assert lease["state"] == "compacting"
            c.sample.update(utilisation=.2, sampled_at=context._now().isoformat())
        return True

    monkeypatch.setattr(context, "_send_checked", send_checked)
    monkeypatch.setattr(context, "_send_slash", send_slash)
    monkeypatch.setattr(context, "_herdr_read", lambda name, lines=60: (
        json.loads(led.leases_path.read_text())["sessions"]["sid"]["token"] + " Run acceptance."))
    return c


def test_guarded_compact_consults_the_shared_key_stream_rule(
    guarded: context.Controller, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ledger, "key_stream_reason",
                        lambda *a, **k: ("blocked", "synthetic key stream"))
    result = guarded.compact("hx-codex")
    assert result["action"] == "keep", (
        "guarded compact ignored the shared key-stream rule")
    assert guarded.calls == [], (
        "guarded compact typed keys despite the shared key-stream rule")


# Finding 4: the lead self-compact command is a key-sending path that does not
# consult the shared rule. A delivery in flight should stop the decision.
@pytest.fixture
def lead(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> context.Controller:
    monkeypatch.delenv("HC_TERMINAL_ID", raising=False)
    monkeypatch.delenv("HERDR_PANE_ID", raising=False)
    monkeypatch.setenv("HX_HERDR_PREFIX", "test-")
    monkeypatch.setenv("HX_STATE_DIR", str(tmp_path))
    led = ledger.Ledger(
        repo_root=tmp_path, ledger_path=tmp_path / "ledger.json",
        leases_path=tmp_path / "leases.json", lock_path=tmp_path / "lock")
    c = context.Controller(led, {
        "team_prefix": "test-", "lead_role": "claude",
        "context_limits": {"claude-lead": 400000},
        "context": {"claude_lead_threshold": .6, "max_per_task": 2,
                    "min_interval_min": 20},
    })
    c.agent = host.member({"name": "test-claude", "agent": "claude",
                           "agent_status": "working",
                           "agent_session": {"value": "lead-sid"}})
    status = {
        "session_id": "lead-sid",
        "timestamp": context._now().isoformat(),
        "context_window": {
            "context_window_size": 1000000,
            "used_percentage": 63,
            "current_usage": {"input_tokens": 630000},
        },
    }
    path = tmp_path / "claude-status-lead-sid.json"
    path.write_text(json.dumps(status), encoding="utf-8")
    checkpoint_path = tmp_path / ".hx" / "checkpoints" / "lead.md"
    checkpoint_path.parent.mkdir(parents=True)
    checkpoint_path.write_text("## Objective\nFinish this release.\n", encoding="utf-8")
    monkeypatch.setattr(context, "agent_info", lambda name: copy.deepcopy(c.agent))
    return c


def test_lead_self_compact_refuses_while_a_key_stream_is_active(
    lead: context.Controller
) -> None:
    lead.ledger.mutate_leases(
        lambda leases: leases.setdefault("deliveries", {}).__setitem__(
            "test-claude",
            {"agent": "test-claude", "holder": "record-1",
             "acquired_at": context._now().isoformat()}))
    result = lead.decide("test-claude")
    assert result["action"] == "keep", (
        "the lead self-compact was scheduled while a delivery was in flight")


# Check, no finding: the 300 s delivery TTL is above the real send bound plus
# the submission-evidence window.
def test_delivery_ttl_covers_the_real_send_bound() -> None:
    assert ledger.DELIVERY_STALE_SECONDS > 60 + say.SUBMIT_POLL_SECONDS + say.ENTER_SETTLE_SECONDS
