"""Q-318: hx records compaction itself and hx say holds on that state."""
from __future__ import annotations

import copy
import datetime as dt
import json
import os
import threading
import time

import pytest

from hx import __main__ as cli
from hx import context, host, ledger, say


AGENTS = {"result": {"agents": [
    {"name": "dev-claude", "pane_id": "w8:p1"},
    {"name": "dev-codex", "pane_id": "w8:p2"},
    {"name": "dev-opencode", "pane_id": "w8:p3"},
]}}


@pytest.fixture
def controller(tmp_path, monkeypatch):
    led = ledger.Ledger(repo_root=tmp_path, ledger_path=tmp_path / "ledger.json",
                        leases_path=tmp_path / "leases.json", lock_path=tmp_path / "lock")
    c = context.Controller(led, {"context": {"compact_wait_seconds": 0}})
    c.agent = host.member({"name": "hx-codex", "agent": "codex", "agent_status": "idle",
                           "agent_session": {"value": "sid"}, "cwd": str(tmp_path)})
    c.calls = []
    c.sample = {"session_id": "sid", "utilisation": .7,
                "sampled_at": context._now().isoformat()}
    monkeypatch.setattr(context, "agent_info", lambda name: copy.deepcopy(c.agent))
    monkeypatch.setattr(context, "telemetry", lambda *a: dict(c.sample))
    monkeypatch.setattr(context, "_send_slash",
                        lambda member, command: c.calls.append(command) or True)
    return c


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch, tmp_path) -> list[list[str]]:
    calls: list[list[str]] = []
    monkeypatch.setenv("HERDR_ENV", "1")
    monkeypatch.setenv("HX_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(host.HerdrHost, "all_members",
                        lambda self: [host.member(a) for a in AGENTS["result"]["agents"]])
    monkeypatch.setattr(say.context, "agent_info", lambda name: host.member({
        "name": name,
        "agent": "claude" if name.endswith("-claude") else "opencode",
        "agent_session": {"value": "sid"},
    }))
    monkeypatch.setattr(say.context, "telemetry", lambda *args: {
        "session_id": "sid", "utilisation": None, "sampled_at": None})

    class Done:
        returncode = 0
        stdout = '{"result":{"type":"agent_prompted","agent":{"agent_status":"working","turn":1}}}'
        stderr = ""

    monkeypatch.setattr(host.subprocess, "run", lambda argv, **_: calls.append(argv) or Done())
    return calls


def write_leases(path, sessions):
    ledger._atomic_write_json(path, {"files": {}, "sessions": sessions})
    return path


def test_quick_records_compacting_then_clears_after_idle_lower_reading(controller, monkeypatch):
    def send_slash(member, command):
        controller.calls.append(command)
        lease = json.loads(controller.ledger.leases_path.read_text())["sessions"]["sid"]
        assert lease["state"] == "compacting"
        assert lease["agent"] == "hx-codex"
        assert lease["utilisation_before"] == .7
        assert lease["requested_at"]
        controller.sample.update(utilisation=.2, sampled_at=context._now().isoformat())
        return True
    monkeypatch.setattr(context, "_send_slash", send_slash)

    result = controller.compact_quick("hx-codex")

    assert result["action"] == "compacted"
    assert result["before"]["utilisation"] == .7
    assert result["after"]["utilisation"] == .2
    assert controller.calls == ["/compact"]
    assert json.loads(controller.ledger.leases_path.read_text())["sessions"] == {}


def test_quick_clears_only_when_idle_at_the_lower_reading(controller, monkeypatch):
    """A lower reading while the worker still runs must not clear the state."""
    t = [0.0]
    monkeypatch.setattr(context.time, "monotonic", lambda: t[0])
    monkeypatch.setattr(context.time, "sleep", lambda s: t.__setitem__(0, t[0] + s))
    controller.policy["context"]["compact_wait_seconds"] = 300
    polls = []

    def send_slash(member, command):
        controller.agent["status"] = "working"  # the compaction turn starts
        controller.sample.update(utilisation=.2, sampled_at=context._now().isoformat())
        return True
    monkeypatch.setattr(context, "_send_slash", send_slash)

    def info(name):
        polls.append(controller.agent["status"])
        if t[0] >= 5:
            controller.agent["status"] = "idle"
        return copy.deepcopy(controller.agent)
    monkeypatch.setattr(context, "agent_info", info)

    result = controller.compact_quick("hx-codex")

    assert result["action"] == "compacted"
    assert "working" in polls, "the wait must have seen the worker busy at a lower reading"


def test_quick_timeout_leaves_compact_unknown_and_holds_say(controller, monkeypatch, capsys):
    monkeypatch.setenv("HX_LEASES_PATH", str(controller.ledger.leases_path))
    result = controller.compact_quick("hx-codex")

    assert result["action"] == "compact-unknown"
    assert result["recover"] == "hx context recover hx-codex"
    lease = json.loads(controller.ledger.leases_path.read_text())["sessions"]["sid"]
    assert lease["state"] == "compact-unknown"
    led = ledger.default_ledger()
    with led._leases_lock():
        assert say._compaction_hold(led, "hx-codex") == "compact-unknown"
    assert "compact-unknown" in capsys.readouterr().err


def test_quick_unaccepted_keys_leave_compact_unknown(controller, monkeypatch):
    monkeypatch.setattr(context, "_send_slash", lambda member, command: False)
    result = controller.compact_quick("hx-codex")
    assert result["action"] == "compact-unknown"
    assert "did not accept" in result["reason"]
    lease = json.loads(controller.ledger.leases_path.read_text())["sessions"]["sid"]
    assert lease["state"] == "compact-unknown"


def test_quick_refuses_an_existing_session_lease(controller):
    write_leases(controller.ledger.leases_path,
                 {"sid": {"agent": "hx-codex", "state": "compact-unknown"}})
    result = controller.compact_quick("hx-codex")
    assert result["action"] == "keep"
    assert controller.calls == []


def test_quick_refuses_a_busy_agent(controller):
    controller.agent["status"] = "working"
    with pytest.raises(ValueError, match="not idle"):
        controller.compact_quick("hx-codex")
    assert controller.calls == []


def test_quick_never_compacts_the_lead(controller):
    with pytest.raises(ValueError, match="lead self-compacts"):
        controller.compact_quick("hx-lead")
    assert controller.calls == []


def test_cli_compact_quick_success_and_timeout_exit(controller, monkeypatch, capsys):
    monkeypatch.setattr(context, "Controller", lambda: controller)
    monkeypatch.setattr(context, "_send_slash", lambda member, command: False)
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["compact", "hx-codex", "--quick"])
    assert excinfo.value.code == 1
    assert "hx context recover hx-codex" in capsys.readouterr().out

    write_leases(controller.ledger.leases_path, {})

    def send_slash(member, command):
        controller.sample.update(utilisation=.2, sampled_at=context._now().isoformat())
        return True
    monkeypatch.setattr(context, "_send_slash", send_slash)
    assert cli.main(["compact", "hx-codex", "--quick"]) == 0
    assert json.loads(capsys.readouterr().out)["action"] == "compacted"


@pytest.mark.parametrize("state", ["compacting", "compact-unknown"])
def test_say_holds_on_the_recorded_compaction_state(monkeypatch, sent, tmp_path, capsys, state):
    monkeypatch.setenv("HERDR_PANE_ID", "w8:p2")
    monkeypatch.setenv("HX_LEASES_PATH", str(write_leases(tmp_path / "leases.json", {
        "sid": {"agent": "dev-opencode", "session_id": "sid", "token": "tok",
                "state": state, "requested_at": context._now().isoformat()},
    })))

    assert cli.main(["say", "opencode", "the next brief"]) == 1
    err = capsys.readouterr().err
    assert f"dev-opencode session is {state}" in err
    assert "hx say --pending" in err
    assert sent == []


def test_say_pending_resend_delivers_once_compaction_clears(monkeypatch, sent, tmp_path, capsys):
    monkeypatch.setenv("HERDR_PANE_ID", "w8:p2")
    leases = write_leases(tmp_path / "leases.json", {
        "sid": {"agent": "dev-opencode", "session_id": "sid", "token": "tok",
                "state": "compacting", "requested_at": context._now().isoformat()},
    })
    monkeypatch.setenv("HX_LEASES_PATH", str(leases))

    assert cli.main(["say", "opencode", "the next brief"]) == 1
    assert sent == []

    write_leases(leases, {})  # hx cleared the state after proving the compaction
    assert cli.main(["say", "--pending"]) == 0
    assert sent == [["herdr", "agent", "prompt", "dev-opencode",
                     "[team from dev-codex] the next brief"]]


def test_say_force_bypasses_the_compaction_hold(monkeypatch, sent, tmp_path):
    monkeypatch.setenv("HERDR_PANE_ID", "w8:p2")
    monkeypatch.setenv("HX_LEASES_PATH", str(write_leases(tmp_path / "leases.json", {
        "sid": {"agent": "dev-opencode", "session_id": "sid", "token": "tok",
                "state": "compacting", "requested_at": context._now().isoformat()},
    })))
    assert cli.main(["say", "--force", "opencode", "recovery instructions"]) == 0
    assert len(sent) == 1


def _blocking_send(monkeypatch, entered, gate):
    """Hold the host transport open until the gate opens; signal on entry."""
    fixture_run = host.subprocess.run

    def run(argv, **kwargs):
        if argv[:3] == ["herdr", "agent", "prompt"]:
            entered.set()
            gate.wait(10)
        return fixture_run(argv, **kwargs)
    monkeypatch.setattr(host.subprocess, "run", run)


def test_say_sends_without_holding_the_leases_lock(monkeypatch, sent, tmp_path):
    """F1: the shared lock covers check + delivery claim, never host I/O."""
    monkeypatch.setenv("HERDR_PANE_ID", "w8:p2")
    leases = write_leases(tmp_path / "leases.json", {})
    monkeypatch.setenv("HX_LEASES_PATH", str(leases))
    entered, gate = threading.Event(), threading.Event()
    _blocking_send(monkeypatch, entered, gate)

    rc = []
    worker = threading.Thread(
        target=lambda: rc.append(cli.main(["say", "opencode", "hi"])))
    worker.start()
    assert entered.wait(5), "the send never reached the host transport"
    try:
        # A stalled send must not hold the machine-wide lock: a compaction or
        # another delivery mutates leases while this send is still blocked.
        sessions = ledger.default_ledger().mutate_leases(
            lambda data: dict(data["sessions"]))
        delivering = sessions.get("deliver:dev-opencode")
        assert delivering is not None
        assert delivering["state"] == "delivering"
        assert ledger._heartbeat_dead(delivering) is False
    finally:
        gate.set()
        worker.join(10)
    assert rc == [0]
    sessions = ledger._load_json(leases)["sessions"]
    assert "deliver:dev-opencode" not in sessions


def test_say_force_still_writes_a_delivery_reservation(monkeypatch, sent, tmp_path):
    """F1: --force skips the compaction check but still reserves the delivery."""
    monkeypatch.setenv("HERDR_PANE_ID", "w8:p2")
    compacting = {"agent": "dev-opencode", "session_id": "sid", "token": "tok",
                  "state": "compacting", "requested_at": context._now().isoformat()}
    leases = write_leases(tmp_path / "leases.json", {"sid": compacting})
    monkeypatch.setenv("HX_LEASES_PATH", str(leases))
    entered, gate = threading.Event(), threading.Event()
    _blocking_send(monkeypatch, entered, gate)

    rc = []
    worker = threading.Thread(
        target=lambda: rc.append(cli.main(["say", "--force", "opencode", "recover now"])))
    worker.start()
    assert entered.wait(5), "the force send never reached the host transport"
    try:
        sessions = ledger.default_ledger().mutate_leases(
            lambda data: dict(data["sessions"]))
        assert sessions["deliver:dev-opencode"]["state"] == "delivering"
    finally:
        gate.set()
        worker.join(10)
    assert rc == [0]
    sessions = ledger._load_json(leases)["sessions"]
    assert "deliver:dev-opencode" not in sessions
    assert sessions["sid"]["state"] == "compacting"


@pytest.mark.parametrize("mode", ["corrupt", "denied"])
def test_say_fails_closed_when_leases_are_unreadable(monkeypatch, sent, tmp_path, capsys, mode):
    """F2: an unreadable leases file holds the message and names the file."""
    monkeypatch.setenv("HERDR_PANE_ID", "w8:p2")
    leases = tmp_path / "leases.json"
    if mode == "corrupt":
        leases.write_text("not json", encoding="utf-8")
    else:
        write_leases(leases, {})

        def denied(self):
            raise PermissionError("denied")
        monkeypatch.setattr(ledger.Ledger, "_load_leases", denied)
    monkeypatch.setenv("HX_LEASES_PATH", str(leases))

    assert cli.main(["say", "opencode", "hi"]) == 1
    err = capsys.readouterr().err
    assert "Held:" in err
    assert "leases.json" in err
    assert "hx say --pending" in err
    assert sent == []

    assert cli.main(["say", "--force", "opencode", "hi"]) == 0
    assert len(sent) == 1


def test_quick_keyboard_interrupt_still_marks_compact_unknown(controller, monkeypatch):
    """F3: any unclear exit leaves compact-unknown, never a stranded compacting."""
    def boom(member, command):
        raise KeyboardInterrupt
    monkeypatch.setattr(context, "_send_slash", boom)

    with pytest.raises(KeyboardInterrupt):
        controller.compact_quick("hx-codex")

    lease = json.loads(controller.ledger.leases_path.read_text())["sessions"]["sid"]
    assert lease["state"] == "compact-unknown"
    assert lease["pid"] == os.getpid()


@pytest.mark.parametrize("heartbeat", ["fresh", "stale", "missing"])
def test_recover_releases_compacting_only_when_the_heartbeat_is_dead(controller, heartbeat):
    """F2: the heartbeat proves the owner alive; a reused live pid cannot."""
    requested = (context._now() - dt.timedelta(seconds=60)).isoformat()
    lease = {"agent": "hx-codex", "session_id": "sid", "token": "t",
             "state": "compacting", "requested_at": requested,
             "pid": os.getpid()}  # live pid: the pid alone must not hold the lease
    if heartbeat == "fresh":
        lease["heartbeat_at"] = context._now().isoformat()
    elif heartbeat == "stale":
        lease["heartbeat_at"] = (context._now() - dt.timedelta(
            seconds=ledger.LEASE_HEARTBEAT_DEAD_SECONDS * 2)).isoformat()
    write_leases(controller.ledger.leases_path, {"sid": lease})

    result = controller.recover("hx-codex")

    sessions = json.loads(controller.ledger.leases_path.read_text())["sessions"]
    if heartbeat == "fresh":
        assert result["action"] == "keep"
        assert sessions["sid"]["state"] == "compacting"
    else:
        assert result["action"] == "recovered"
        assert "sid" not in sessions


@pytest.mark.parametrize("heartbeat", ["fresh", "stale", "missing"])
def test_recover_releases_a_delivery_only_when_its_heartbeat_is_dead(controller, heartbeat):
    """F2: a stranded deliver reservation recovers like a stranded compaction."""
    stamp = context._now()
    lease = {"agent": "hx-codex", "state": "delivering", "token": "d1",
             "started_at": stamp.isoformat()}
    if heartbeat == "fresh":
        lease["heartbeat_at"] = stamp.isoformat()
    elif heartbeat == "stale":
        lease["heartbeat_at"] = (stamp - dt.timedelta(
            seconds=ledger.LEASE_HEARTBEAT_DEAD_SECONDS * 2)).isoformat()
    write_leases(controller.ledger.leases_path, {"deliver:hx-codex": lease})

    result = controller.recover("hx-codex")

    sessions = json.loads(controller.ledger.leases_path.read_text())["sessions"]
    if heartbeat == "fresh":
        assert result["action"] == "keep"
        assert sessions["deliver:hx-codex"]["state"] == "delivering"
    else:
        assert result["action"] == "recovered"
        assert "deliver:hx-codex" not in sessions


def test_quick_waits_for_a_live_delivery_to_finish(controller, monkeypatch):
    """F1: a live delivery to the same agent blocks compaction only briefly."""
    stamp = context._now().isoformat()
    write_leases(controller.ledger.leases_path, {
        "deliver:hx-codex": {"agent": "hx-codex", "state": "delivering",
                             "token": "d1", "started_at": stamp, "heartbeat_at": stamp},
    })

    def clear_soon():
        time.sleep(.3)
        controller.ledger.mutate_leases(
            lambda data: data["sessions"].pop("deliver:hx-codex", None))
    threading.Thread(target=clear_soon, daemon=True).start()

    def send_slash(member, command):
        controller.sample.update(utilisation=.2, sampled_at=context._now().isoformat())
        return True
    monkeypatch.setattr(context, "_send_slash", send_slash)

    result = controller.compact_quick("hx-codex")

    assert result["action"] == "compacted"
    assert json.loads(controller.ledger.leases_path.read_text())["sessions"] == {}


def test_quick_reaps_a_dead_delivery_and_compacts(controller, monkeypatch):
    """F1: a delivery whose heartbeat stopped is dead, not a blocker."""
    stale = (context._now() - dt.timedelta(
        seconds=ledger.LEASE_HEARTBEAT_DEAD_SECONDS * 2)).isoformat()
    write_leases(controller.ledger.leases_path, {
        "deliver:hx-codex": {"agent": "hx-codex", "state": "delivering",
                             "token": "d1", "started_at": stale, "heartbeat_at": stale},
    })

    def send_slash(member, command):
        controller.sample.update(utilisation=.2, sampled_at=context._now().isoformat())
        return True
    monkeypatch.setattr(context, "_send_slash", send_slash)

    result = controller.compact_quick("hx-codex")

    assert result["action"] == "compacted"
    assert json.loads(controller.ledger.leases_path.read_text())["sessions"] == {}


def test_quick_refuses_while_a_delivery_stays_live(controller, monkeypatch):
    """F1: a still-beating delivery outlasts the brief wait and refuses."""
    monkeypatch.setattr(ledger, "LEASE_HEARTBEAT_DEAD_SECONDS", .5)
    stamp = context._now().isoformat()
    write_leases(controller.ledger.leases_path, {
        "deliver:hx-codex": {"agent": "hx-codex", "state": "delivering",
                             "token": "d1", "started_at": stamp, "heartbeat_at": stamp},
    })
    # The sender is alive: its heartbeat keeps the reservation fresh while
    # compact_quick waits, so the wait expires into a refusal.
    heartbeat = ledger.LeaseHeartbeat(
        controller.ledger, "deliver:hx-codex", "d1", interval=.05)
    heartbeat.start()
    try:
        result = controller.compact_quick("hx-codex")
    finally:
        heartbeat.stop()

    assert result["action"] == "keep"
    assert "in flight" in result["reason"]
    assert controller.calls == []


def test_a_delivery_reservation_is_not_a_dispatch_lease(controller):
    """deliver: entries stay invisible to the dispatch session API."""
    stamp = context._now().isoformat()
    write_leases(controller.ledger.leases_path, {
        "deliver:hx-codex": {"agent": "hx-codex", "state": "delivering",
                             "token": "d1", "started_at": stamp, "heartbeat_at": stamp},
    })
    led = controller.ledger
    assert led.get_session("hx-codex") is None
    assert led.release_session("hx-codex", holder="hx-claude") is False
    assert led.set_session_state("hx-codex", "held") is False
    assert led.acquire_session("hx-codex", "s9", holder="hx-claude") is True
    sessions = json.loads(controller.ledger.leases_path.read_text())["sessions"]
    assert sessions["deliver:hx-codex"]["state"] == "delivering"
    assert sessions["hx-codex"]["state"] == "held"


def test_recover_clears_every_compaction_lease_for_the_agent(controller):
    """F4: a lease on a replaced session clears too; other agents are untouched."""
    requested = (context._now() - dt.timedelta(seconds=60)).isoformat()
    write_leases(controller.ledger.leases_path, {
        "oldsid": {"agent": "hx-codex", "session_id": "oldsid", "token": "t1",
                   "state": "compact-unknown", "requested_at": "2000-01-01T00:00:00+00:00"},
        "sid": {"agent": "hx-codex", "session_id": "sid", "token": "t2",
                "state": "compact-unknown", "requested_at": requested},
        "other": {"agent": "hx-opencode", "state": "compact-unknown",
                  "requested_at": requested},
    })

    result = controller.recover("hx-codex")

    assert result["action"] == "recovered"
    sessions = json.loads(controller.ledger.leases_path.read_text())["sessions"]
    assert "oldsid" not in sessions
    assert "sid" not in sessions
    assert sessions["other"]["state"] == "compact-unknown"


def test_quick_records_an_unknown_baseline_and_clears_below_threshold(controller, monkeypatch):
    """F5: a stale before-reading records unknown, not a refusal."""
    controller.sample["sampled_at"] = "2000-01-01T00:00:00+00:00"

    def send_slash(member, command):
        lease = json.loads(controller.ledger.leases_path.read_text())["sessions"]["sid"]
        assert lease["utilisation_before"] is None
        controller.sample.update(utilisation=.2, sampled_at=context._now().isoformat())
        return True
    monkeypatch.setattr(context, "_send_slash", send_slash)

    result = controller.compact_quick("hx-codex")

    assert result["action"] == "compacted"
    assert result["before"]["utilisation"] == .7
    assert result["after"]["utilisation"] == .2
    assert json.loads(controller.ledger.leases_path.read_text())["sessions"] == {}


def test_quick_unknown_baseline_without_low_reading_is_compact_unknown(controller, monkeypatch):
    """F5: with no baseline, only a post-send reading under pressure threshold clears."""
    controller.sample["sampled_at"] = "2000-01-01T00:00:00+00:00"

    def send_slash(member, command):
        controller.sample.update(utilisation=.9, sampled_at=context._now().isoformat())
        return True
    monkeypatch.setattr(context, "_send_slash", send_slash)

    result = controller.compact_quick("hx-codex")

    assert result["action"] == "compact-unknown"
    lease = json.loads(controller.ledger.leases_path.read_text())["sessions"]["sid"]
    assert lease["state"] == "compact-unknown"


def test_say_ignores_unrelated_and_held_session_leases(monkeypatch, sent, tmp_path):
    monkeypatch.setenv("HERDR_PANE_ID", "w8:p2")
    monkeypatch.setenv("HX_LEASES_PATH", str(write_leases(tmp_path / "leases.json", {
        "other-sid": {"agent": "dev-claude", "session_id": "other-sid", "token": "tok",
                      "state": "compacting", "requested_at": context._now().isoformat()},
        "dev-opencode": {"agent": "dev-opencode", "state": "held", "task_id": "t",
                         "holder": "dev-lead"},
    })))
    assert cli.main(["say", "opencode", "continue"]) == 0
    assert len(sent) == 1
