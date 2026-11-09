"""Devin pane footer telemetry and the clear-needed decision path."""

import copy
import json
import subprocess

import pytest

from hx import checkpoint, context, host, ledger


@pytest.fixture
def controller(tmp_path, monkeypatch):
    led = ledger.Ledger(repo_root=tmp_path, ledger_path=tmp_path / "ledger.json",
                        leases_path=tmp_path / "leases.json", lock_path=tmp_path / "lock")
    led.add_task("t", "Devin work", "dev-devin", ["owned.py"], base_commit="base")
    led.set_status("t", "done")
    cp = tmp_path / "checkpoint.md"
    cp.write_text("## Decisions\nKeep the design.\n\n## Exact errors\nNone.\n\n## Next action\nRun acceptance.\n\n## References\nDocs.\n", encoding="utf-8")
    monkeypatch.setattr(checkpoint, "validate_checkpoint", lambda *a, **kw: {"ok": True, "checkpoint": str(cp)})
    monkeypatch.setattr(checkpoint, "_run_git", lambda *a: (0, "", ""))
    c = context.Controller(led, {"team_prefix": "dev-", "context": {"verification_timeout_seconds": 0, "compact_wait_seconds": 0}})
    c.agent = host.member({"name": "dev-devin", "agent": "devin", "agent_status": "idle",
                           "pane_id": "wN:p6", "cwd": str(tmp_path)})
    c.footer = "SWE-2 High · accept edits    Context: 157k / 262k tokens (59%)\n"
    c.calls = []
    monkeypatch.setattr(context, "agent_info", lambda name: copy.deepcopy(c.agent))
    monkeypatch.setattr(context, "_herdr_read", lambda name, lines=200: c.footer)

    def send_checked(name, text, **kwargs):
        c.calls.append((name, text, kwargs))
        return {"started": True, "status": "unknown", "turn": None, "ended": True, "reason": None}

    monkeypatch.setattr(context, "_send_checked", send_checked)
    return c


@pytest.mark.parametrize("text,expected", [
    ("SWE-2 High · accept edits    Context: 157k / 262k tokens (59%)", (157000.0, 262000.0)),
    ("44k / 262k (16%)", (44000.0, 262000.0)),
    ("SWE-2 Max · accept edits    Context: 1.5M / 2M tokens (75%)", (1500000.0, 2000000.0)),
    ("Context: 300k / 262k tokens (114%)", (300000.0, 262000.0)),
    ("Context: 52k / 262k tokens (20%)", (52000.0, 262000.0)),
])
def test_parse_devin_footer_usage_forms(text, expected):
    assert context._parse_devin_footer(text) == expected


@pytest.mark.parametrize("text", [
    "",
    "Pro · 100% remaining (resets in 23h 20m)",
    "SWE-2 High · accept edits",
    "   orchestration\n- Implement H1 CLI task fr... -\n> Ask Devin to build featur...\n-----------------------------\nSWE-2 High · accept edits\n",
    "Context: 157k / 262k tokens (20%)",
    "Context: 44k / 0 tokens (0%)",
])
def test_parse_devin_footer_unknown_or_inconsistent(text):
    assert context._parse_devin_footer(text) is None


def test_parse_devin_footer_uses_last_matching_line():
    pane = (
        "Context: 90k / 262k tokens (34%)\n"
        "some scrolled output\n"
        "SWE-2 High · accept edits\n"
        "44k / 262k (16%)\n"
    )
    assert context._parse_devin_footer(pane) == (44000.0, 262000.0)


def test_parse_devin_footer_scans_past_a_non_footer_last_line():
    """The hc screen keeps the update nag below the Devin footer."""
    pane = (
        "some scrolled output\n"
        "SWE-2 High · bypass                  Context: 118k / 262k tokens (45%)\n"
        "Update v3000.12.32 available!\n"
    )
    assert context._parse_devin_footer(pane) == (118000.0, 262000.0)


def test_parse_devin_footer_stops_at_a_present_but_broken_footer():
    """A footer-shaped line that fails its own consistency check is present-but-
    unreadable, so it stays unknown instead of falling back to a stale line."""
    pane = (
        "Context: 90k / 262k tokens (34%)\n"
        "Context: 157k / 262k tokens (20%)\n"
    )
    assert context._parse_devin_footer(pane) is None


def test_parse_devin_footer_never_crosses_the_footer_block_into_output():
    """Codex's screen: a Context example in task output above the footer block
    must never become telemetry."""
    pane = (
        "Context: 210k / 262k tokens (80%)\n"
        "Ask Devin to build features\n"
        "SWE-2 High | accept edits\n"
        "Pro | 100% remaining (resets in 23h 20m)\n"
    )
    assert context._parse_devin_footer(pane) is None


@pytest.mark.parametrize("pane,expected", [
    ("SWE-2 High · bypass                  Context: 118k / 262k tokens (45%)\n"
     "Update v3000.12.32 available!\n", "SWE-2 High"),
    ("SWE-2 High · bypass\n44k / 262k (16%)\n", "SWE-2 High"),
    ("SWE-2 Max | accept edits\nPro | 100% remaining (resets in 23h 20m)\n", "SWE-2 Max"),
    ("Context: 52k / 262k tokens (20%)\n", None),
    ("Pro | 100% remaining (resets in 23h 20m)\n", None),
    ("Context: 210k / 262k tokens (80%)\nAsk Devin to build features\n"
     "SWE-2 High | accept edits\n", "SWE-2 High"),
])
def test_devin_footer_label_reads_the_model_line(pane, expected):
    """The label comes from the footer block's model line whether or not it
    shares a line with Context — the narrow two-line footer included."""
    assert context._devin_footer_label(pane) == expected


def test_devin_telemetry_reads_the_footer_above_an_update_nag(controller):
    controller.footer = (
        "work output\n"
        "SWE-2 High · bypass                  Context: 118k / 262k tokens (45%)\n"
        "Update v3000.12.32 available!\n"
    )
    sample = context.telemetry(controller.agent, {})
    assert sample["session_id"] == "devin-pane:wN:p6"
    assert sample["utilisation"] == pytest.approx(118 / 262)
    assert context._timestamp(sample["sampled_at"]) is not None


def test_devin_session_uses_pane_identity():
    assert context._session(host.member({"agent": "devin", "name": "dev-devin", "pane_id": "wN:p6"})) == "devin-pane:wN:p6"
    assert context._session(host.member({"agent": "devin", "name": "dev-devin"})) == "devin-pane:dev-devin"
    assert context._session({"agent": "devin"}) == ""


def test_devin_telemetry_parses_footer(controller):
    sample = context.telemetry(controller.agent, {})
    assert sample["session_id"] == "devin-pane:wN:p6"
    assert sample["utilisation"] == pytest.approx(157 / 262)
    assert context._timestamp(sample["sampled_at"]) is not None


def test_devin_telemetry_unknown_without_footer(controller, monkeypatch):
    monkeypatch.setattr(context, "_herdr_read", lambda name, lines=200: "Pro · 100% remaining (resets in 23h 20m)\n")
    sample = context.telemetry(controller.agent, {})
    assert sample["utilisation"] is None
    assert sample["sampled_at"] is None


def test_r1_prose_ratio_is_not_footer_telemetry(controller):
    controller.footer = (
        "Example for the parser: Context: 210k / 262k tokens (80%)\n"
        "Ask Devin to build features\n"
        "SWE-2 High | accept edits\n"
        "Pro | 100% remaining (resets in 23h 20m)\n"
    )
    sample = context.telemetry(controller.agent, {})
    assert sample["utilisation"] is None
    assert sample["sampled_at"] is None


def test_devin_telemetry_unknown_when_read_fails(controller, monkeypatch):
    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, "herdr")
    monkeypatch.setattr(context, "_herdr_read", fail)
    assert context.telemetry(controller.agent, {})["utilisation"] is None


def test_decide_clear_needed_without_open_task(controller):
    decision = controller.decide("dev-devin")
    assert decision["action"] == "clear-needed"
    assert decision.get("task_id") is None
    assert "no open task" in decision["reason"]
    assert any("/clear" in step for step in decision["steps"])


def test_decide_clear_needed_under_pressure_mid_task(controller):
    controller.ledger.set_status("t", "in_progress")
    controller.footer = "Context: 210k / 262k tokens (80%)\n"
    decision = controller.decide("dev-devin", related=True)
    assert decision["action"] == "clear-needed"
    assert decision["pressure"] is True
    assert decision["mid_task"] is True
    assert "during active task" in decision["reason"]


def test_decide_clear_needed_without_registered_task(controller, tmp_path):
    controller.ledger = ledger.Ledger(
        repo_root=tmp_path,
        ledger_path=tmp_path / "empty-ledger.json",
        leases_path=tmp_path / "empty-leases.json",
        lock_path=tmp_path / "empty-lock",
    )
    controller.footer = "Context: 210k / 262k tokens (80%)\n"
    decision = controller.decide("dev-devin")
    assert decision["action"] == "clear-needed"
    assert "no open task" in decision["reason"]
    assert "/clear" in " ".join(decision["steps"])


def test_decide_keep_below_threshold(controller):
    controller.footer = "44k / 262k (16%)\n"
    decision = controller.decide("dev-devin")
    assert decision["action"] == "keep"
    assert "below boundary threshold" in decision["reason"]


def test_decide_keep_retains_warm_context_for_related_task(controller):
    assert controller.decide("dev-devin", related=True)["action"] == "keep"


def test_decide_keep_when_footer_unknown(controller):
    controller.footer = "Pro · 100% remaining (resets in 23h 20m)\n"
    decision = controller.decide("dev-devin")
    assert decision["action"] == "keep"
    assert "telemetry unknown or stale" in decision["reason"]


def test_decide_keep_when_busy(controller):
    controller.agent["status"] = "working"
    decision = controller.decide("dev-devin")
    assert decision["action"] == "keep"
    assert "not idle" in decision["reason"]


def test_compact_refuses_devin_without_compact_command(controller):
    result = controller.compact("dev-devin")
    assert result["action"] == "keep"
    assert "/clear" in result["reason"]
    assert not controller.calls


def test_compact_refuses_devin_even_below_threshold(controller):
    controller.footer = "44k / 262k (16%)\n"
    result = controller.compact("dev-devin")
    assert result["action"] == "keep"
    assert "/clear" in result["reason"]
    assert not controller.calls


def test_compact_command_policy_runs_standard_compaction(controller, monkeypatch):
    controller.policy["devin"] = {"compact_command": "/compact"}
    assert controller.decide("dev-devin", "t")["action"] == "compact"

    def read(name, lines=200):
        if lines <= 10:
            return controller.footer
        token = json.loads(controller.ledger.leases_path.read_text())["sessions"]["devin-pane:wN:p6"]["token"]
        return f"{token} Run acceptance."

    def send_checked(name, text, **kwargs):
        controller.calls.append((name, text, kwargs))
        return {"started": True, "status": "unknown", "turn": None, "ended": True, "reason": None}

    def send_slash(member, command):
        controller.calls.append((member["name"], command, {}))
        if command == "/compact":
            lease = json.loads(controller.ledger.leases_path.read_text())["sessions"]["devin-pane:wN:p6"]
            assert lease["state"] == "compacting"
            controller.footer = "Context: 40k / 262k tokens (15%)\n"
        return True

    monkeypatch.setattr(context, "_send_checked", send_checked)
    monkeypatch.setattr(context, "_send_slash", send_slash)
    monkeypatch.setattr(context, "_herdr_read", read)

    result = controller.compact("dev-devin", "t")
    assert result["action"] == "compacted"
    assert result["telemetry"]["utilisation"] == pytest.approx(40 / 262)
    assert controller.calls[0][1] == "/compact"
    assert not any("/clear" in str(call) for call in controller.calls)
    assert json.loads(controller.ledger.leases_path.read_text())["sessions"] == {}


def test_compact_command_applies_to_devin_only(controller, monkeypatch):
    """A configured devin.compact_command must never change what codex compaction sends."""
    controller.policy["devin"] = {"compact_command": "/squash"}
    controller.agent["kind"] = "codex"
    controller.agent["session_id"] = "sid"
    del controller.agent["address"]
    controller.sample = {"session_id": "sid", "utilisation": .7,
                         "sampled_at": context._now().isoformat()}
    monkeypatch.setattr(context, "telemetry", lambda *a: dict(controller.sample))
    monkeypatch.setattr(context, "_herdr_read", lambda name, lines=200:
                        json.loads(controller.ledger.leases_path.read_text())["sessions"]["sid"]["token"] + " Run acceptance.")

    def send_checked(name, text, **kwargs):
        controller.calls.append((name, text, kwargs))
        return {"started": True, "status": "unknown", "turn": None, "ended": True, "reason": None}

    def send_slash(member, command):
        controller.calls.append((member["name"], command, {}))
        if command == "/compact":
            controller.sample.update(utilisation=.2, sampled_at=context._now().isoformat())
        return True

    monkeypatch.setattr(context, "_send_checked", send_checked)
    monkeypatch.setattr(context, "_send_slash", send_slash)

    result = controller.compact("dev-devin", "t")
    assert result["action"] == "compacted"
    assert controller.calls[0][1] == "/compact"


def _virtual_now(monkeypatch):
    """Strictly increasing _now: Windows dt.now() ticks coarsely, so samples can tie."""
    box = [context.dt.datetime.now(context.dt.timezone.utc)]
    monkeypatch.setattr(
        context, "_now",
        lambda: box.__setitem__(0, box[0] + context.dt.timedelta(milliseconds=1)) or box[0])
    return box[0]


def test_devin_reset_verified_by_footer_drop(controller, monkeypatch):
    """A Devin pane keeps its session id; a fresh lower footer proves the /clear."""
    t = [0.0]
    monkeypatch.setattr(context.time, "monotonic", lambda: t[0])
    monkeypatch.setattr(context.time, "sleep", lambda s: t.__setitem__(0, t[0] + s))
    _virtual_now(monkeypatch)
    controller.policy["context"]["verification_timeout_seconds"] = 10
    sent = []

    def send_slash(member, command):
        sent.append(command)
        controller.footer = "Context: 20k / 262k tokens (8%)\n"
        return True

    monkeypatch.setattr(context, "_send_slash", send_slash)
    result = controller.reset("dev-devin")
    assert result["action"] == "reset-verified"
    assert result["verified_by"] == "telemetry"
    assert result["command"] == "/clear"
    assert result["session_id"] == "devin-pane:wN:p6"
    assert sent == ["/clear"]
    assert json.loads(controller.ledger.leases_path.read_text())["sessions"] == {}


def test_devin_reset_unverified_keeps_reservation(controller, monkeypatch):
    t = [0.0]
    monkeypatch.setattr(context.time, "monotonic", lambda: t[0])
    monkeypatch.setattr(context.time, "sleep", lambda s: t.__setitem__(0, t[0] + s))
    controller.policy["context"]["verification_timeout_seconds"] = 10
    monkeypatch.setattr(context, "_send_slash", lambda member, command: True)
    result = controller.reset("dev-devin")
    assert result["action"] == "reset-unknown"
    hold = json.loads(controller.ledger.leases_path.read_text())["sessions"]["reset:dev-devin"]
    assert hold["state"] == "reset-unknown"
    assert controller.decide("dev-devin")["reason"].startswith("session is leased")


def test_devin_reset_needs_fresh_footer(controller, monkeypatch):
    monkeypatch.setattr(context, "_herdr_read", lambda name, lines=200: "Pro · 100% remaining\n")
    sent = []
    monkeypatch.setattr(context, "_send_slash", lambda m, c: sent.append(c) or True)
    result = controller.reset("dev-devin")
    assert result["action"] == "keep"
    assert "telemetry" in result["reason"]
    assert sent == []


def test_task_bound_reset_validates_checkpoint(controller, monkeypatch):
    controller.ledger.set_status("t", "in_progress")
    monkeypatch.setattr(checkpoint, "validate_checkpoint", lambda *a, **kw: {"ok": False, "errors": ["stale"]})
    sent = []
    monkeypatch.setattr(context, "_send_slash", lambda m, c: sent.append(c) or True)
    result = controller.reset("dev-devin", task_id="t")
    assert result["action"] == "keep"
    assert "checkpoint validation failed" in result["reason"]
    assert sent == []
    assert json.loads(controller.ledger.leases_path.read_text())["sessions"] == {}


def test_reset_guard_bounds_taskless_recovery(controller, monkeypatch):
    ledger._atomic_write_json(controller.guard_path,
                              {"dev-devin": {"tasks": {"<no-task>": {"count": 2}}}})
    controller.footer = "Context: 210k / 262k tokens (80%)\n"
    decision = controller.decide("dev-devin")
    assert decision["action"] == "keep"
    assert "loop guard" in decision["reason"]


def test_reset_guard_cooldown_defers_taskless_recovery(controller, monkeypatch):
    ledger._atomic_write_json(controller.guard_path,
                              {"dev-devin": {"last_at": context._now().isoformat(), "tasks": {}}})
    controller.footer = "Context: 210k / 262k tokens (80%)\n"
    decision = controller.decide("dev-devin")
    assert decision["action"] == "keep"
    assert "minimum compaction interval" in decision["reason"]


def test_verified_reset_bumps_the_agent_guard(controller, monkeypatch):
    t = [0.0]
    monkeypatch.setattr(context.time, "monotonic", lambda: t[0])
    monkeypatch.setattr(context.time, "sleep", lambda s: t.__setitem__(0, t[0] + s))
    _virtual_now(monkeypatch)
    controller.policy["context"]["verification_timeout_seconds"] = 10

    def send_slash(member, command):
        controller.footer = "Context: 20k / 262k tokens (8%)\n"
        return True

    monkeypatch.setattr(context, "_send_slash", send_slash)
    assert controller.reset("dev-devin")["action"] == "reset-verified"
    guard = json.loads(controller.guard_path.read_text())["dev-devin"]
    assert guard["tasks"]["<no-task>"]["count"] == 1 and guard["last_at"]


def test_execute_recovery_maps_decisions(controller, monkeypatch):
    calls = []
    monkeypatch.setattr(controller, "compact",
                        lambda name, task_id=None, related=False:
                        calls.append(("compact", task_id)) or {"action": "compacted"})
    monkeypatch.setattr(controller, "reset",
                        lambda name, **kw: calls.append(("reset", kw)) or {"action": "reset-verified"})
    out = controller.execute_recovery("dev-devin", {"action": "compact", "task_id": "t"})
    assert out["outcome"] == "compacted" and calls[-1] == ("compact", "t")
    out = controller.execute_recovery("dev-devin", {"action": "clear-needed", "task_id": "t"})
    assert out["outcome"] == "reset-verified" and calls[-1] == ("reset", {"task_id": "t"})
    out = controller.execute_recovery("dev-devin", {"action": "compact-needed", "agent_kind": "codex"})
    assert calls[-1] == ("reset", {"command": "/compact"})
    # Devin without a configured compact command has nothing to run.
    out = controller.execute_recovery("dev-devin", {"action": "compact-needed", "agent_kind": "devin"})
    assert out["outcome"] == "skipped"
    out = controller.execute_recovery("dev-devin", {"action": "keep"})
    assert out["outcome"] == "skipped"


def test_execute_recovery_reports_executor_errors(controller, monkeypatch):
    def boom(name, **kw):
        raise RuntimeError("host died")
    monkeypatch.setattr(controller, "reset", boom)
    out = controller.execute_recovery("dev-devin", {"action": "clear-needed"})
    assert out["outcome"] == "error" and "host died" in out["reason"]


# ---------- review findings: the reset path must re-prove its decision ----------


def test_reset_reservation_rechecks_other_session_leases(controller, monkeypatch):
    """Review 1: an unrelated lease appearing after decide must abort the reservation."""
    controller.ledger.set_status("t", "in_progress")
    ledger._atomic_write_json(controller.ledger.leases_path, {"files": {}, "sessions": {
        "other-sid": {"agent": "dev-devin", "state": "compact-unknown"}}})
    sent = []
    monkeypatch.setattr(context, "_send_slash", lambda m, c: sent.append(c) or True)
    result = controller.reset("dev-devin", task_id="t")
    assert result["action"] == "keep"
    assert sent == []
    sessions = json.loads(controller.ledger.leases_path.read_text())["sessions"]
    assert sessions["other-sid"]["state"] == "compact-unknown"


def test_reset_reservation_rechecks_the_reset_guard(controller, monkeypatch):
    """Review 1: the per-task limit and cooldown bind at reservation, not only at decide."""
    controller.ledger.set_status("t", "in_progress")
    ledger._atomic_write_json(controller.guard_path,
                              {"dev-devin": {"tasks": {"t": {"count": 2}}}})
    sent = []
    monkeypatch.setattr(context, "_send_slash", lambda m, c: sent.append(c) or True)
    result = controller.reset("dev-devin", task_id="t")
    assert result["action"] == "keep"
    assert "loop guard" in result["reason"]
    assert sent == []


def test_reset_reservation_refuses_a_released_task(controller, monkeypatch):
    """Review 1 + released-task fix: a task released after decide is no longer attributable."""
    controller.ledger.set_status("t", "in_progress")
    controller.ledger.release_task_reservations("t")
    sent = []
    monkeypatch.setattr(context, "_send_slash", lambda m, c: sent.append(c) or True)
    result = controller.reset("dev-devin", task_id="t")
    assert result["action"] == "keep"
    assert sent == []


def test_reset_rechecks_readiness_right_before_sending(controller, monkeypatch):
    """Review 1: a worker going busy during checkpoint validation gets no keys."""
    controller.ledger.set_status("t", "in_progress")
    replies = iter([dict(controller.agent), dict(controller.agent, status="working")])
    monkeypatch.setattr(context, "agent_info", lambda name: next(replies))
    sent = []
    monkeypatch.setattr(context, "_send_slash", lambda m, c: sent.append(c) or True)
    result = controller.reset("dev-devin", task_id="t")
    assert result["action"] == "keep"
    assert "busy" in result["reason"]
    assert sent == []
    assert "reset:dev-devin" not in json.loads(
        controller.ledger.leases_path.read_text())["sessions"]


def test_reset_rechecks_the_worktree_right_before_sending(controller, monkeypatch):
    """Review 1: dirt landing after decide aborts the send."""
    controller.ledger.set_status("t", "in_progress")
    monkeypatch.setattr(checkpoint, "_run_git", lambda *a: (0, " M stray.py\0", ""))
    sent = []
    monkeypatch.setattr(context, "_send_slash", lambda m, c: sent.append(c) or True)
    result = controller.reset("dev-devin", task_id="t")
    assert result["action"] == "keep"
    assert "ownership" in result["reason"]
    assert sent == []


def test_task_bound_reset_rebriefs_from_the_validated_checkpoint(controller, monkeypatch):
    """Review 2: a task-bound clear is done only once the worker is re-briefed and acks."""
    t = [0.0]
    monkeypatch.setattr(context.time, "monotonic", lambda: t[0])
    monkeypatch.setattr(context.time, "sleep", lambda s: t.__setitem__(0, t[0] + s))
    _virtual_now(monkeypatch)
    controller.policy["context"]["verification_timeout_seconds"] = 10
    controller.ledger.set_status("t", "in_progress")
    sent = []

    def send_slash(member, command):
        sent.append(command)
        controller.footer = "Context: 20k / 262k tokens (8%)\n"
        return True
    monkeypatch.setattr(context, "_send_slash", send_slash)

    def read(name, lines=200):
        if lines <= 10:
            return controller.footer
        token = controller.calls[-1][1].split("reply with ")[1].split(" ")[0]
        return f"{token} Run acceptance."
    monkeypatch.setattr(context, "_herdr_read", read)

    result = controller.reset("dev-devin", task_id="t")
    assert result["action"] == "reset-verified"
    briefs = [call for call in controller.calls if "checkpoint.md" in str(call[1])]
    assert briefs, "the worker was never re-briefed from its checkpoint"
    assert json.loads(controller.ledger.leases_path.read_text())["sessions"] == {}
    guard = json.loads(controller.guard_path.read_text())["dev-devin"]
    assert guard["tasks"]["t"]["count"] == 1


def test_task_bound_reset_keeps_the_reservation_until_rebriefed(controller, monkeypatch):
    """Review 2: a verified reset without an acknowledged re-brief is an unknown outcome."""
    t = [0.0]
    monkeypatch.setattr(context.time, "monotonic", lambda: t[0])
    monkeypatch.setattr(context.time, "sleep", lambda s: t.__setitem__(0, t[0] + s))
    _virtual_now(monkeypatch)
    controller.policy["context"]["verification_timeout_seconds"] = 10
    controller.ledger.set_status("t", "in_progress")

    def send_slash(member, command):
        controller.footer = "Context: 20k / 262k tokens (8%)\n"
        return True
    monkeypatch.setattr(context, "_send_slash", send_slash)
    monkeypatch.setattr(context, "_herdr_read", lambda name, lines=200: (
        controller.footer if lines <= 10 else "I lost my place."))

    result = controller.reset("dev-devin", task_id="t")
    assert result["action"] == "reset-unknown"
    assert "re-brief" in result["reason"]
    hold = json.loads(controller.ledger.leases_path.read_text())["sessions"]["reset:dev-devin"]
    assert hold["state"] == "reset-unknown"


def test_reset_counting_precedes_sending(controller, monkeypatch):
    """Review 3: a bookkeeping failure can never leave keys sent with no attempt recorded."""
    controller.ledger.set_status("t", "in_progress")
    sent = []
    monkeypatch.setattr(context, "_send_slash", lambda m, c: sent.append(c) or True)

    def boom(*args, **kwargs):
        raise OSError("guard write failed")
    monkeypatch.setattr(controller, "_bump_reset_guard", boom)

    for _ in range(2):
        with pytest.raises(OSError):
            controller.reset("dev-devin", task_id="t")
    assert sent == []
    assert "reset:dev-devin" not in json.loads(
        controller.ledger.leases_path.read_text())["sessions"]


# ---------- review 2 findings: reset verification, binding and transport ----------


def test_moved_session_task_bound_hold_still_owes_the_rebrief(controller, monkeypatch):
    """Review 2 finding 1: a task-bound reset-unknown hold on a moved session
    resumes the checkpoint re-brief instead of releasing unverified."""
    controller.policy["context"]["verification_timeout_seconds"] = 10
    controller.ledger.set_status("t", "in_progress")
    sent = []

    def send_slash(member, command):
        sent.append(command)
        controller.agent["address"] = "wN:p7"  # the pane was replaced mid-reset
        return True
    monkeypatch.setattr(context, "_send_slash", send_slash)
    monkeypatch.setattr(context, "_herdr_read", lambda name, lines=200: (
        controller.footer if lines <= 10 else "I lost my place."))

    first = controller.reset("dev-devin", task_id="t")
    assert first["action"] == "reset-unknown"
    hold = json.loads(controller.ledger.leases_path.read_text())["sessions"]["reset:dev-devin"]
    assert hold["state"] == "reset-unknown"
    # recover cannot release it either: the re-brief is still owed.
    assert controller.recover("dev-devin")["action"] == "keep"

    monkeypatch.setattr(context, "_herdr_read", lambda name, lines=200: (
        controller.footer if lines <= 10 else
        controller.calls[-1][1].split("reply with ")[1].split(" ")[0] + " Run acceptance."))
    second = controller.reset("dev-devin", task_id="t")
    assert second["action"] == "reset-verified"
    assert sent == ["/clear"]
    briefs = [call for call in controller.calls if "checkpoint.md" in str(call[1])]
    assert len(briefs) == 2
    assert "reset:dev-devin" not in json.loads(
        controller.ledger.leases_path.read_text())["sessions"]


def test_devin_task_bound_reset_resumes_an_unacknowledged_rebrief(controller, monkeypatch):
    """Review 2 finding 1: a pane-bound session cannot move, so the hold records
    the owed re-brief and a retry finishes it instead of blocking forever."""
    t = [0.0]
    monkeypatch.setattr(context.time, "monotonic", lambda: t[0])
    monkeypatch.setattr(context.time, "sleep", lambda s: t.__setitem__(0, t[0] + s))
    _virtual_now(monkeypatch)
    controller.policy["context"]["verification_timeout_seconds"] = 10
    controller.ledger.set_status("t", "in_progress")
    sent = []

    def send_slash(member, command):
        sent.append(command)
        controller.footer = "Context: 20k / 262k tokens (8%)\n"
        return True
    monkeypatch.setattr(context, "_send_slash", send_slash)
    monkeypatch.setattr(context, "_herdr_read", lambda name, lines=200: (
        controller.footer if lines <= 10 else "I lost my place."))

    first = controller.reset("dev-devin", task_id="t")
    assert first["action"] == "reset-unknown"
    assert sent == ["/clear"]
    hold = json.loads(controller.ledger.leases_path.read_text())["sessions"]["reset:dev-devin"]
    assert hold["state"] == "reset-unknown"
    assert controller.recover("dev-devin")["action"] == "keep"

    monkeypatch.setattr(context, "_herdr_read", lambda name, lines=200: (
        controller.footer if lines <= 10 else
        controller.calls[-1][1].split("reply with ")[1].split(" ")[0] + " Run acceptance."))
    second = controller.reset("dev-devin", task_id="t")
    assert second["action"] == "reset-verified"
    assert sent == ["/clear"]
    briefs = [call for call in controller.calls if "checkpoint.md" in str(call[1])]
    assert len(briefs) == 2
    assert "reset:dev-devin" not in json.loads(
        controller.ledger.leases_path.read_text())["sessions"]


def test_execute_recovery_binds_reset_to_the_decision_session(controller, monkeypatch):
    """Review 2 finding 2: a decision made for one session must not reset another."""
    controller.policy["context"]["verification_timeout_seconds"] = 10
    controller.ledger.set_status("t", "in_progress")
    sent = []

    def send_slash(member, command):
        sent.append(command)
        controller.agent["address"] = "wN:p7"
        return True
    monkeypatch.setattr(context, "_send_slash", send_slash)
    monkeypatch.setattr(context, "_herdr_read", lambda name, lines=200: (
        controller.footer if lines <= 10 else
        controller.calls[-1][1].split("reply with ")[1].split(" ")[0] + " Run acceptance."))

    stale = {"action": "clear-needed", "task_id": "t",
             "session_id": "devin-pane:wN:p0", "agent_kind": "devin"}
    out = controller.execute_recovery("dev-devin", stale)
    assert out["outcome"] == "keep"
    assert "session" in out["reason"]
    assert sent == []

    stale_taskless = {"action": "compact-needed",
                      "session_id": "devin-pane:wN:p0", "agent_kind": "codex"}
    out = controller.execute_recovery("dev-devin", stale_taskless)
    assert out["outcome"] == "keep"
    assert "session" in out["reason"]
    assert sent == []

    current = {"action": "clear-needed", "task_id": "t",
               "session_id": "devin-pane:wN:p6", "agent_kind": "devin"}
    out = controller.execute_recovery("dev-devin", current)
    assert out["outcome"] == "reset-verified"
    assert sent == ["/clear"]


def test_reset_snapshots_readiness_again_after_the_worktree_check(controller, monkeypatch):
    """Review 2 finding 3: a worker going busy during the worktree check gets no keys."""
    controller.ledger.set_status("t", "in_progress")
    original = controller._check_worktrees

    def busy_during_check(agent, task):
        controller.agent["status"] = "working"
        return original(agent, task)

    monkeypatch.setattr(controller, "_check_worktrees", busy_during_check)
    sent = []
    monkeypatch.setattr(context, "_send_slash", lambda m, c: sent.append(c) or True)
    result = controller.reset("dev-devin", task_id="t")
    assert result["action"] == "keep"
    assert "busy" in result["reason"]
    assert sent == []
    assert "reset:dev-devin" not in json.loads(
        controller.ledger.leases_path.read_text())["sessions"]


def test_post_send_failure_leaves_a_recoverable_reset_unknown(controller, monkeypatch):
    """Review 2 finding 5: an exception after accepted keys ends in the
    recoverable reset-unknown state, never an unreachable resetting hold."""
    t = [0.0]
    monkeypatch.setattr(context.time, "monotonic", lambda: t[0])
    monkeypatch.setattr(context.time, "sleep", lambda s: t.__setitem__(0, t[0] + s))
    controller.policy["context"]["verification_timeout_seconds"] = 10
    boom = [False]
    original = context.agent_info

    def info(name):
        if boom[0]:
            raise RuntimeError("host read failed after keys")
        return original(name)

    sent = []

    def send_slash(member, command):
        sent.append(command)
        boom[0] = True
        return True
    monkeypatch.setattr(context, "agent_info", info)
    monkeypatch.setattr(context, "_send_slash", send_slash)

    result = controller.reset("dev-devin")
    assert result["action"] == "reset-unknown"
    hold = json.loads(controller.ledger.leases_path.read_text())["sessions"]["reset:dev-devin"]
    assert hold["state"] == "reset-unknown"

    boom[0] = False
    assert controller.recover("dev-devin")["action"] == "recovered"
    assert "reset:dev-devin" not in json.loads(
        controller.ledger.leases_path.read_text())["sessions"]
