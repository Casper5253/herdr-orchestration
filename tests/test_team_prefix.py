"""team_prefix: any <team>- name is recognised, per the herdr-team skill."""
from __future__ import annotations

import pytest

from hx import context, usage

AGENTS = {"result": {"agents": [
    {"name": "dev-claude", "pane_id": "w8:p1"},
    {"name": "dev-codex", "pane_id": "w8:p2"},
    {"name": "hx-claude", "pane_id": "w9:p1"},
    {"name": "alpha-opencode", "pane_id": "w10:p3"},
    {"name": "my-team-codex", "pane_id": "w11:p2"},
    {"agent": "claude", "pane_id": "w3:p1"},
]}}


@pytest.mark.parametrize(("pane", "expected"), [("w8:p1", "dev-"), ("w8:p2", "dev-"), ("w9:p1", "hx-"), ("w10:p3", "alpha-"), ("w11:p2", "my-team-"), ("w3:p1", "hx-")])
def test_prefix_comes_from_this_panes_agent(monkeypatch: pytest.MonkeyPatch, pane: str, expected: str) -> None:
    monkeypatch.setenv("HERDR_PANE_ID", pane)
    monkeypatch.setattr(usage, "_herdr_agent_list", lambda: AGENTS)
    assert usage.team_prefix({"team_prefix": "hx-"}) == expected


def test_env_override_and_policy_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    assert usage.team_prefix({"team_prefix": "hx-"}) == "hx-"
    monkeypatch.setenv("HX_HERDR_PREFIX", "dev-")
    assert usage.team_prefix({"team_prefix": "hx-"}) == "dev-"


def test_herdr_failure_falls_back_to_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HERDR_PANE_ID", "w8:p1")

    def broken() -> dict:
        raise OSError("herdr not running")

    monkeypatch.setattr(usage, "_herdr_agent_list", broken)
    assert usage.team_prefix({"team_prefix": "hx-"}) == "hx-"


def test_context_accepts_the_dev_team(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HERDR_PANE_ID", "w8:p1")
    monkeypatch.setattr(usage, "_herdr_agent_list", lambda: AGENTS)
    monkeypatch.setattr(context, "agent_info", lambda name: {"name": name})
    controller = context.Controller(policy={"team_prefix": "hx-"})
    assert controller._agent("dev-codex") == {"name": "dev-codex"}
    with pytest.raises(ValueError, match="outside the configured team"):
        controller._agent("hx-codex")
