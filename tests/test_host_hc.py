"""HcHost and `hx say` on the Herdr app core, against a fake `hc`.

The fake parses the same JSON the CLI prints: `hc ls --json` gives addresses,
`hc get <t> --json` gives the Terminal (labels, status.app, pid), `hc send`
gives a SendResult, and a refused send prints `{error: {kind}}`.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from hx import host, say, usage
from hx.__main__ import main


def _completed(stdout: str, returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(["hc"], returncode, stdout, "")


def _terminal(
    address: str,
    *,
    name: str | None = None,
    team: str | None = None,
    role: str | None = None,
    app: str | None = None,
    state: str | None = None,
    pid: int | None = None,
    cwd: str = "C:\\repo",
) -> dict:
    labels = {}
    if team is not None:
        labels["team"] = team
    if role is not None:
        labels["role"] = role
    return {
        "address": address,
        "name": name,
        "cwd": cwd,
        "pid": pid,
        "labels": labels,
        "status": {"app": app, "state": state} if app and state else None,
    }


class FakeHc:
    def __init__(self) -> None:
        self.order = ["t1", "t7", "t8", "t9", "t20", "t21"]
        self.terminals = {
            "t1": _terminal("t1", team="dev", role="claude", app="claude-code",
                            state="idle", pid=10),
            "t7": _terminal("t7", name="dev-codex", team="dev", role="codex",
                            app="codex", state="working", pid=11),
            "t8": _terminal("t8", name="dev-opencode", team="dev", role="opencode",
                            pid=12),
            "t9": _terminal("t9", name="dev-devin", team="dev", role="devin",
                            app="devin", state="idle", pid=13),
            "t20": _terminal("t20", team="other", role="claude", app="codex",
                             state="idle", pid=20),
            "t21": _terminal("t21", app="pi", state="idle", pid=21),
        }
        self.by_name = {
            detail["name"]: address
            for address, detail in self.terminals.items()
            if detail["name"]
        }
        self.runs: list[list[str]] = []
        self.sent: list[tuple[str, str]] = []
        self.replies: list[dict] = []

    def run(self, argv, **kwargs):
        assert kwargs["shell"] is False
        self.runs.append(list(argv))
        assert argv[0] == "hc"
        command = argv[1]
        if command == "ls":
            rows = [{"address": address} for address in self.order]
            return _completed(json.dumps(
                {"terminals": rows, "hidden_finished": 0, "machines": []}))
        if command == "get":
            address = self.by_name.get(argv[2], argv[2])
            detail = self.terminals.get(address)
            if detail is None:
                return _completed(
                    json.dumps({"error": {"kind": "not_found"}}), 1)
            return _completed(json.dumps(detail))
        if command == "send":
            self.sent.append((argv[2], argv[3]))
            reply = self.replies.pop(0) if self.replies else {
                "started": True, "status": "working", "turn": 1, "wait": None}
            return _completed(json.dumps(reply))
        if command == "read":
            return _completed("pane text")
        raise AssertionError(f"unexpected hc command: {argv}")

    def add(self, detail: dict) -> None:
        self.order.append(detail["address"])
        self.terminals[detail["address"]] = detail
        if detail["name"]:
            self.by_name[detail["name"]] = detail["address"]


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakeHc:
    fake = FakeHc()
    monkeypatch.setenv("HC_TERMINAL_ID", "t7")
    monkeypatch.setenv("HX_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("HX_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(host.subprocess, "run", fake.run)
    monkeypatch.setattr(say, "_under_pressure", lambda name: False)
    return fake


def test_own_uses_the_terminal_labels_for_the_unnamed_lead(
    fake: FakeHc, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HC_TERMINAL_ID", "t1")
    member = host.HcHost().own()
    assert member is not None
    assert member["name"] == "dev-claude"
    assert (member["team"], member["role"]) == ("dev", "claude")
    assert member["kind"] == "claude"
    assert member["address"] == "t1"


def test_all_members_is_the_full_roster_any_team(fake: FakeHc) -> None:
    members = host.HcHost().all_members()
    by_address = {member["address"]: member for member in members}
    assert set(by_address) == {"t1", "t7", "t8", "t9", "t20", "t21"}
    assert by_address["t1"]["name"] == "dev-claude"
    assert by_address["t20"]["team"] == "other"
    assert by_address["t21"]["name"] == ""
    assert by_address["t8"]["kind"] == "unknown"
    assert by_address["t21"]["kind"] == "unknown"
    assert all(member["focused"] is None for member in members)


def test_team_filters_on_the_team_label(fake: FakeHc) -> None:
    team = host.HcHost().team("dev-")
    assert sorted(team) == ["dev-claude", "dev-codex", "dev-devin", "dev-opencode"]
    assert "other-claude" not in team


def test_duplicate_role_is_refused(fake: FakeHc) -> None:
    fake.add(_terminal("t22", team="dev", role="codex", app="codex", state="idle"))
    with pytest.raises(ValueError, match="duplicate role dev-codex"):
        host.HcHost().team("dev-")
    assert main(["say", "claude", "hi"]) == 1
    assert fake.sent == []


def test_cross_team_target_is_refused(fake: FakeHc) -> None:
    assert main(["say", "other-claude", "hi"]) == 1
    assert main(["say", "hx-claude", "hi"]) == 1
    assert fake.sent == []


def test_unknown_roster_fails_closed(fake: FakeHc, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(argv, **kwargs):
        return _completed("not json", 1)

    monkeypatch.setattr(host.subprocess, "run", broken)
    with pytest.raises(ValueError):
        host.HcHost().all_members()


def test_binding_match_sets_session_and_mismatch_is_none(fake: FakeHc, tmp_path: Path) -> None:
    bindings = tmp_path / "state" / "hc-bindings"
    bindings.mkdir(parents=True)
    (bindings / "t7.json").write_text(json.dumps({
        "terminal": "t7", "session_id": "ses_t7", "pid": 11, "cwd": "C:\\repo",
    }), encoding="utf-8")
    (bindings / "t8.json").write_text(json.dumps({
        "terminal": "t8", "session_id": "ses_stale", "pid": 999, "cwd": "C:\\repo",
    }), encoding="utf-8")

    members = {member["address"]: member for member in host.HcHost().all_members()}
    assert members["t7"]["session_id"] == "ses_t7"
    assert members["t8"]["session_id"] is None


def test_send_checked_maps_the_start_and_the_wait(fake: FakeHc) -> None:
    sent = host.HcHost().send_checked("dev-claude", "ping")
    assert sent == host.SendResult(
        started=True, status="working", turn=1, ended=False, reason=None)

    fake.replies = [{
        "started": True, "status": "working", "turn": 4,
        "wait": {"ended": False, "reason": "timeout", "turn": 4},
    }]
    sent = host.HcHost().send_checked("dev-claude", "ping", wait=True, timeout_ms=1000)
    assert sent["started"] is True
    assert sent["ended"] is False
    assert sent["reason"] == "timeout"
    assert fake.runs[-1] == [
        "hc", "send", "dev-claude", "ping", "--json", "--wait", "--timeout", "1"]
    assert "--force" not in fake.runs[-1]

    fake.replies = [{
        "started": True, "status": "working", "turn": 4,
        "wait": {"ended": True, "reason": "status", "turn": 4},
    }]
    sent = host.HcHost().send_checked("dev-claude", "ping", wait=True)
    assert sent["ended"] is True
    assert sent["reason"] is None


@pytest.mark.parametrize("kind,started,status,reason", [
    ("blocked", False, "blocked", "blocked"),
    ("finished", False, "unknown", "finished"),
    ("unavailable", False, "unknown", "unavailable"),
    ("status_mismatch", False, "unknown", "status_mismatch"),
])
def test_send_errors_map_without_inventing_completion(fake, kind, started, status, reason):
    fake.replies = [{"error": {"kind": kind}}]
    sent = host.HcHost().send_checked("dev-claude", "ping")
    assert sent["started"] is started
    assert sent["status"] == status
    assert sent["ended"] is False
    assert sent["reason"] == reason


def test_read_uses_hc_read_with_the_line_count(fake: FakeHc) -> None:
    assert host.HcHost().read("dev-codex", 40) == "pane text"
    assert fake.runs[-1] == ["hc", "read", "dev-codex", "--lines", "40"]


def test_say_writes_the_outbox_then_marks_it_delivered(fake: FakeHc, tmp_path: Path) -> None:
    assert main(["say", "claude", "ping"]) == 0
    assert fake.sent == [("dev-claude", "[team from dev-codex] ping")]
    records = list((tmp_path / ".hx" / "outbox" / "dev-codex").glob("*.json"))
    assert len(records) == 1
    record = json.loads(records[0].read_text(encoding="utf-8"))
    assert record["target"] == "dev-claude"
    assert record["text"] == "[team from dev-codex] ping"
    assert record["delivered"] is True
    assert record["delivered_at"]


def test_a_blocked_target_is_refused_and_stays_pending(fake: FakeHc, tmp_path: Path) -> None:
    fake.terminals["t1"]["status"] = {"app": "claude-code", "state": "blocked"}
    assert main(["say", "claude", "ping"]) == 1
    assert fake.sent == []
    records = list((tmp_path / ".hx" / "outbox" / "dev-codex").glob("*.json"))
    assert len(records) == 1
    record = json.loads(records[0].read_text(encoding="utf-8"))
    assert record["delivered"] is False


def test_pending_lists_and_retries_after_the_target_recovers(fake: FakeHc, tmp_path: Path) -> None:
    fake.terminals["t1"]["status"] = {"app": "claude-code", "state": "blocked"}
    assert main(["say", "claude", "ping"]) == 1
    assert fake.sent == []

    fake.terminals["t1"]["status"] = {"app": "claude-code", "state": "idle"}
    assert main(["say", "--pending"]) == 0
    assert fake.sent == [("dev-claude", "[team from dev-codex] ping")]
    record = json.loads(
        next((tmp_path / ".hx" / "outbox" / "dev-codex").glob("*.json")).read_text(
            encoding="utf-8"))
    assert record["delivered"] is True


def test_pending_keeps_a_still_blocked_message(fake: FakeHc) -> None:
    fake.terminals["t1"]["status"] = {"app": "claude-code", "state": "blocked"}
    assert main(["say", "claude", "ping"]) == 1

    fake.replies = [{"error": {"kind": "blocked"}}]
    assert main(["say", "--pending"]) == 1


def test_devin_target_gets_the_deny_line_on_hc(fake: FakeHc) -> None:
    assert main(["say", "devin", "status?"]) == 0
    text = fake.sent[0][1]
    assert text.startswith("[team from dev-codex] status?")
    assert "Denied for you (a denied call cancels your turn): " in text
    assert "`git push`" in text
    assert "`hc send`" in text
