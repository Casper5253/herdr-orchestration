"""The Host contract: callers use only the declared methods.

A fake with exactly the Protocol methods must satisfy every caller. This keeps
the boundary honest before HcHost exists: no `.command` escape hatch and no
getattr probing.
"""

from __future__ import annotations

import json
import subprocess
from unittest.mock import patch

import pytest

from hx import context, devin, host, say, usage


def _member(
    name: str,
    team: str = "dev",
    role: str = "codex",
    kind: str = "codex",
    status: str = "idle",
    address: str = "t11",
    session_id: str | None = None,
    session_reason: str | None = None,
    cwd: str = "repo",
    focused: bool | None = None,
    turn: int | None = None,
) -> host.Member:
    return host.Member(
        name=name, team=team, role=role, kind=kind, status=status,
        cwd=cwd, address=address, session_id=session_id,
        session_reason=session_reason, focused=focused, turn=turn,
    )


def _completed(stdout: str, returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(["herdr"], returncode, stdout, "")


def _stub_prompt(monkeypatch, stdout: str, returncode: int = 0) -> None:
    def run(args, **kwargs):
        assert kwargs["shell"] is False
        return _completed(stdout, returncode)

    monkeypatch.setattr(host.subprocess, "run", run)


class MinimalHost:
    """Exactly the declared Host methods, no private ones."""

    def __init__(self, members: list[host.Member], roster: list[host.Member] | None = None) -> None:
        self.members = members
        self.roster = members if roster is None else roster
        self.sent: list[tuple[str, str]] = []
        self.checked: list[tuple[str, str, bool, tuple[str, ...], int | None]] = []

    def own(self) -> host.Member | None:
        return self.members[0] if self.members else None

    def all_members(self) -> list[host.Member] | None:
        return self.roster

    def team(self, prefix: str) -> dict[str, host.Member]:
        return {member["name"]: member for member in self.members if member["name"].startswith(prefix)}

    def info(self, name: str) -> host.Member:
        for member in self.members:
            if member["name"] == name:
                return member
        raise ValueError(name)

    def send(self, name: str, text: str) -> int:
        self.sent.append((name, text))
        return 0

    def send_checked(
        self, name: str, text: str, *, wait: bool = False,
        until: tuple[str, ...] = (), timeout_ms: int | None = None,
    ) -> host.SendResult:
        self.checked.append((name, text, wait, until, timeout_ms))
        self.info(name)["turn"] = 2  # the receiver takes its next turn
        return host.SendResult(
            started=True, status="working", turn=1, ended=wait, reason=None)

    def read(self, name: str, lines: int) -> str:
        return "pane text"


@pytest.fixture
def roster() -> list[host.Member]:
    return [
        _member("dev-codex"),
        _member("dev-devin", role="devin", kind="devin", address="t12"),
        _member("", team="", role="", kind="devin", address="t20"),
        _member("other-devin", team="other", role="devin", kind="devin", address="t21"),
    ]


def test_all_members_uses_the_declared_roster(roster):
    fake = MinimalHost(roster)
    with patch.object(host, "detect", return_value=fake):
        assert host.all_members() == roster
        assert usage.live_members() == roster


def test_all_members_parses_a_subprocess_roster(monkeypatch):
    payload = {"result": {"agents": [
        {"name": "dev-devin", "agent": "devin", "agent_status": "idle",
         "pane_id": "t12", "cwd": "C:\\work",
         "agent_session": {"value": "s1"}, "focused": True},
        {"name": "plain", "pane_id": "t20", "agent_status": "working"},
    ]}}
    calls = []

    def check_output(args, **kwargs):
        calls.append((args, kwargs))
        return json.dumps(payload).encode("utf-8")

    monkeypatch.setattr(host.subprocess, "check_output", check_output)
    members = host.HerdrHost().all_members()

    assert calls == [(["herdr", "agent", "list"], {"stderr": subprocess.DEVNULL, "timeout": 30})]
    assert members[0] == _member(
        "dev-devin", role="devin", kind="devin", address="t12",
        session_id="s1", session_reason="agent_session", cwd="C:\\work", focused=True, turn=None)
    assert members[1]["kind"] == "unknown"
    assert members[1]["status"] == "working"
    assert members[1]["address"] == "t20"


def test_devin_counts_every_team_and_unnamed_panes(roster):
    fake = MinimalHost(roster)
    with patch.object(host, "detect", return_value=fake):
        sessions = devin.live_sessions(usage.live_members())
    assert [s["name"] for s in sessions] == ["dev-devin", None, "other-devin"]


def test_unknown_roster_refuses_admission(roster):
    fake = MinimalHost(roster)
    fake.roster = None
    with patch.object(host, "detect", return_value=fake):
        with pytest.raises(RuntimeError):
            usage.live_members()
        assert devin._read_live_sessions() is None


def test_context_uses_only_declared_methods(roster):
    fake = MinimalHost(roster)
    with patch.object(host, "detect", return_value=fake):
        assert context.agent_info("dev-codex") == roster[0]
        sent = context._send_checked(
            "dev-codex", "/compact", wait=True, until=("idle", "done"), timeout_ms=1000)
    assert sent["ended"] is True
    assert fake.checked == [("dev-codex", "/compact", True, ("idle", "done"), 1000)]


def test_say_sends_through_the_declared_checked_send(monkeypatch, tmp_path):
    fake = MinimalHost([_member("dev-codex"), _member("dev-claude")])
    unknown = {"session_id": None, "utilisation": None, "sampled_at": None}
    monkeypatch.setenv("HX_PROJECT_ROOT", str(tmp_path))
    with patch.object(host, "detect", return_value=fake), \
            patch.object(context, "telemetry", return_value=unknown), \
            patch.object(usage, "load_policy", return_value={"lead_role": "claude"}):
        assert say.say("claude", "hi") == 0
    assert fake.checked == [("dev-claude", "[team from dev-codex] hi", False, (), None)]


def test_focused_is_nullable_not_dropped():
    assert host.member({"focused": True})["focused"] is True
    assert host.member({"focused": False})["focused"] is False
    assert host.member({})["focused"] is None


def test_unknown_status_is_never_available():
    unknown = _member("dev-opencode", role="opencode", kind="unknown", status="unknown")
    idle = _member("dev-codex")
    assert usage.agents_usage({}, prefix="dev-", agents=[unknown])["available"] is False
    assert usage.agents_usage({}, prefix="dev-", agents=[unknown, idle])["available"] is False
    assert usage.agents_usage({}, prefix="dev-", agents=[idle])["available"] is True


def test_wait_timeout_is_not_completion(monkeypatch):
    def run(args, **kwargs):
        raise subprocess.TimeoutExpired(args, 310)
    monkeypatch.setattr(host.subprocess, "run", run)
    sent = host.HerdrHost().send_checked("dev-codex", "/compact", wait=True)
    assert sent["started"] is None
    assert sent["ended"] is False
    assert sent["reason"] == "timeout"


def test_waited_send_reports_an_observed_turn_end(monkeypatch):
    _stub_prompt(
        monkeypatch,
        '{"result":{"type":"agent_prompted","agent":{"agent_status":"idle"}}}',
    )
    sent = host.HerdrHost().send_checked("dev-codex", "/compact", wait=True)
    assert sent == host.SendResult(
        started=True, status="idle", turn=None, ended=True, reason=None)


def test_json_timeout_keeps_the_host_start_evidence(monkeypatch):
    _stub_prompt(
        monkeypatch,
        '{"result":{"type":"timeout","started":true,"ended":false,"reason":"timeout"}}',
    )
    sent = host.HerdrHost().send_checked("dev-codex", "/compact", wait=True)
    assert sent["started"] is True
    assert sent["ended"] is False
    assert sent["reason"] == "timeout"


@pytest.mark.parametrize("stdout,returncode,started,status,reason", [
    ('{"error":{"code":"agent_blocked","message":"blocked"}}', 1, False, "blocked", "blocked"),
    ('{"error":{"code":"agent_prompt_stalled","message":"stalled"}}', 1, False, "unknown", "stalled"),
    ('{"result":{"type":"agent_prompted","agent":{"agent_status":"blocked"}}}',
     0, True, "blocked", "blocked"),
])
def test_blocked_or_stalled_send_is_never_ended(
    monkeypatch, stdout, returncode, started, status, reason
):
    _stub_prompt(monkeypatch, stdout, returncode)
    sent = host.HerdrHost().send_checked("dev-codex", "/compact", wait=True)
    assert sent["started"] is started
    assert sent["status"] == status
    assert sent["ended"] is False
    assert sent["reason"] == reason


@pytest.mark.parametrize("stdout", ['{"result":{}}', "not json"])
def test_malformed_response_is_unknown(monkeypatch, stdout):
    _stub_prompt(monkeypatch, stdout)
    sent = host.HerdrHost().send_checked("dev-codex", "/compact", wait=True)
    assert sent["started"] is None
    assert sent["ended"] is False
    assert sent["reason"] == "malformed response"


def test_subprocess_failure_is_unknown(monkeypatch):
    def run(args, **kwargs):
        raise OSError("herdr missing")
    monkeypatch.setattr(host.subprocess, "run", run)
    sent = host.HerdrHost().send_checked("dev-codex", "/compact", wait=True)
    assert sent["started"] is None
    assert sent["ended"] is False
    assert sent["reason"] == "host command failed"


def test_detect_returns_herdr_without_the_pane_marker(monkeypatch):
    monkeypatch.delenv("HERDR_ENV", raising=False)
    monkeypatch.delenv("HC_TERMINAL_ID", raising=False)
    assert isinstance(host.detect(), host.HerdrHost)


def test_detect_returns_herdr_inside_a_pane(monkeypatch):
    monkeypatch.setenv("HERDR_ENV", "1")
    monkeypatch.delenv("HC_TERMINAL_ID", raising=False)
    assert isinstance(host.detect(), host.HerdrHost)


def test_detect_returns_hc_in_an_hc_terminal(monkeypatch):
    monkeypatch.setenv("HC_TERMINAL_ID", "t11")
    assert isinstance(host.detect(), host.HcHost)


def test_the_protocol_declares_every_caller_method():
    assert {"own", "all_members", "team", "info", "send", "send_checked", "read"} <= set(host.Host.__dict__)
