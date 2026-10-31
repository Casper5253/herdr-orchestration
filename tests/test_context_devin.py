"""Devin pane footer telemetry and the clear-needed decision path."""

import copy
import json
import subprocess

import pytest

from hx import checkpoint, context, ledger


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
    c = context.Controller(led, {"team_prefix": "dev-", "context": {"verification_timeout_seconds": 0}})
    c.agent = {"name": "dev-devin", "agent": "devin", "agent_status": "idle",
               "pane_id": "wN:p6", "cwd": str(tmp_path)}
    c.footer = "SWE-2 High · accept edits    Context: 157k / 262k tokens (59%)\n"
    c.calls = []
    monkeypatch.setattr(context, "agent_info", lambda name: copy.deepcopy(c.agent))
    monkeypatch.setattr(context, "_herdr_read", lambda name, lines=200: c.footer)

    def herdr(*args, **kwargs):
        c.calls.append(args)
        return {"result": {"type": "agent_prompted"}}

    monkeypatch.setattr(context, "_herdr", herdr)
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


def test_devin_session_uses_pane_identity():
    assert context._session({"agent": "devin", "name": "dev-devin", "pane_id": "wN:p6"}) == "devin-pane:wN:p6"
    assert context._session({"agent": "devin", "name": "dev-devin"}) == "devin-pane:dev-devin"
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


def test_decide_clear_needed_at_task_boundary(controller):
    decision = controller.decide("dev-devin")
    assert decision["action"] == "clear-needed"
    assert decision["task_id"] == "t"
    assert decision["pressure"] is False
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
    assert "no hx task is registered" in decision["reason"]
    assert "checkpoint" in decision["reason"]


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
    controller.agent["agent_status"] = "working"
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
    assert controller.decide("dev-devin")["action"] == "compact"

    def read(name, lines=200):
        if lines <= 10:
            return controller.footer
        token = json.loads(controller.ledger.leases_path.read_text())["sessions"]["devin-pane:wN:p6"]["token"]
        return f"{token} Run acceptance."

    def herdr(*args, **kwargs):
        controller.calls.append(args)
        if args[0] == "prompt" and args[2] == "/compact":
            lease = json.loads(controller.ledger.leases_path.read_text())["sessions"]["devin-pane:wN:p6"]
            assert lease["state"] == "compacting"
            controller.footer = "Context: 40k / 262k tokens (15%)\n"
        return {"result": {"type": "agent_prompted"}}

    monkeypatch.setattr(context, "_herdr", herdr)
    monkeypatch.setattr(context, "_herdr_read", read)

    result = controller.compact("dev-devin")
    assert result["action"] == "compacted"
    assert result["telemetry"]["utilisation"] == pytest.approx(40 / 262)
    assert controller.calls[0][2] == "/compact"
    assert not any("/clear" in str(call) for call in controller.calls)
    assert json.loads(controller.ledger.leases_path.read_text())["sessions"] == {}


def test_compact_command_applies_to_devin_only(controller, monkeypatch):
    """A configured devin.compact_command must never change what codex compaction sends."""
    controller.policy["devin"] = {"compact_command": "/squash"}
    controller.agent["agent"] = "codex"
    controller.agent["agent_session"] = {"value": "sid"}
    del controller.agent["pane_id"]
    controller.sample = {"session_id": "sid", "utilisation": .7,
                         "sampled_at": context._now().isoformat()}
    monkeypatch.setattr(context, "telemetry", lambda *a: dict(controller.sample))
    monkeypatch.setattr(context, "_herdr_read", lambda name, lines=200:
                        json.loads(controller.ledger.leases_path.read_text())["sessions"]["sid"]["token"] + " Run acceptance.")

    def herdr(*args, **kwargs):
        controller.calls.append(args)
        if args[0] == "prompt" and args[2] == "/compact":
            controller.sample.update(utilisation=.2, sampled_at=context._now().isoformat())
        return {"result": {"type": "agent_prompted"}}

    monkeypatch.setattr(context, "_herdr", herdr)

    result = controller.compact("dev-devin")
    assert result["action"] == "compacted"
    assert controller.calls[0][2] == "/compact"
