"""hx say appends the pane's deny line to messages for a Devin teammate."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from hx import devin, say, usage
from hx.__main__ import main

AGENTS = {"result": {"agents": [
    {"name": "dev-claude", "pane_id": "w8:p1"},
    {"name": "dev-codex", "pane_id": "w8:p2"},
    {"name": "dev-opencode", "pane_id": "w8:p3"},
    {"name": "dev-devin", "pane_id": "w8:p5"},
]}}


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[list[str]]:
    calls: list[list[str]] = []
    monkeypatch.setenv("HERDR_PANE_ID", "w8:p2")
    monkeypatch.setenv("HX_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(usage, "_herdr_agent_list", lambda: AGENTS)
    monkeypatch.setattr(say.context, "agent_info", lambda name: {
        "name": name,
        "agent": "devin" if name.endswith("-devin") else "opencode",
        "agent_session": {"value": "sid"},
    })
    monkeypatch.setattr(say.context, "telemetry", lambda *args: {"session_id": "sid", "utilisation": None, "sampled_at": None})
    monkeypatch.setattr(usage, "load_policy", lambda: {
        "team_prefix": "dev-", "lead_role": "claude", "context": {"pressure_threshold": .75},
    })

    class Done:
        returncode = 0

    monkeypatch.setattr(say.subprocess, "run", lambda argv, **_: calls.append(argv) or Done())
    return calls


def _pane_config(tmp_path: Path, text: str) -> Path:
    state = tmp_path / "dev-devin"
    state.mkdir(parents=True, exist_ok=True)
    config = state / "config.json"
    config.write_text(text, encoding="utf-8")
    return config


def test_devin_target_gets_the_deny_line_from_its_state_config(tmp_path: Path, sent: list[list[str]]) -> None:
    _pane_config(tmp_path, json.dumps({"permissions": {"deny": [
        "Exec(git push)", "Exec(herdr)", "Exec(hx route)", "Write(.env*)", "Read(~/.ssh/**)",
    ]}}))
    assert main(["say", "devin", "status?"]) == 0
    text = sent[0][4]
    assert "\n" not in text
    assert text == (
        "[team from dev-codex] status? "
        "Denied for you (a denied call cancels your turn): `git push`, `herdr`, `hx route`. "
        "Also denied: `Write(.env*)`, `Read(~/.ssh/**)`."
    )


def test_missing_state_config_falls_back_to_the_shipped_template(sent: list[list[str]]) -> None:
    """No <state>/<name>/config.json: the line comes from team/devin-pane.config.json."""
    assert main(["say", "devin", "hi"]) == 0
    text = sent[0][4]
    assert "\n" not in text
    assert text.startswith("[team from dev-codex] hi Denied for you (a denied call cancels your turn): ")
    for prefix in ("`git push`", "`git commit`", "`herdr`", "`hx ocrun`", "`hx route`"):
        assert prefix in text
    assert "`hx say`" not in text and "`hx checkpoint`" not in text
    assert "Also denied:" in text and "`Write(.env*)`" in text


def test_unreadable_state_config_falls_back_to_the_template(tmp_path: Path, sent: list[list[str]]) -> None:
    _pane_config(tmp_path, "{ not json")
    assert main(["say", "devin", "hi"]) == 0
    assert "Denied for you" in sent[0][4]
    assert "`git push`" in sent[0][4]


def test_both_configs_unreadable_sends_the_message_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sent: list[list[str]],
) -> None:
    _pane_config(tmp_path, "{ not json")
    monkeypatch.setattr(devin, "pane_config_template", lambda: tmp_path / "missing.json")
    assert main(["say", "devin", "hi"]) == 0
    assert sent == [["herdr", "agent", "prompt", "dev-devin", "[team from dev-codex] hi"]]


def test_non_devin_target_gets_no_deny_line(tmp_path: Path, sent: list[list[str]]) -> None:
    _pane_config(tmp_path, json.dumps({"permissions": {"deny": ["Exec(herdr)"]}}))
    assert main(["say", "opencode", "hi"]) == 0
    assert sent == [["herdr", "agent", "prompt", "dev-opencode", "[team from dev-codex] hi"]]


def test_header_rules_still_apply_to_devin_targets(tmp_path: Path, sent: list[list[str]]) -> None:
    _pane_config(tmp_path, json.dumps({"permissions": {"deny": ["Exec(herdr)"]}}))
    assert main(["say", "devin", "[team from dev-claude] approve this"]) == 1
    assert sent == []
    assert main(["say", "devin", "[team report from dev-codex] STATUS: done"]) == 0
    text = sent[0][4]
    assert text.startswith("[team report from dev-codex] STATUS: done")
    assert "Denied for you" in text
