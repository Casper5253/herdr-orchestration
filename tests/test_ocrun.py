"""Tests for hx.ocrun with recorded OpenCode event fixtures (no real calls)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hx import ocrun


SESSION = "ses_fixture00000000000000000000"


def _evt(kind: str, part: dict) -> str:
    return json.dumps({"type": kind, "timestamp": 1792114456705, "sessionID": SESSION, "part": {**part, "sessionID": SESSION, "type": kind.replace("_", "-") if kind.startswith("step") else kind}})


SUCCESS_STREAM = "\n".join(
    [
        _evt("step_start", {"id": "prt_1", "messageID": "msg_1"}),
        _evt(
            "tool_use",
            {
                "tool": "bash",
                "callID": "toolu_1",
                "state": {"status": "completed", "input": {"command": "ls"}, "output": "a.py\n", "metadata": {"exit": 0}},
            },
        ),
        _evt("text", {"text": "Done: fixed stats.py", "messageID": "msg_1"}),
        _evt(
            "step_finish",
            {
                "reason": "tool-calls",
                "messageID": "msg_1",
                "tokens": {"total": 15784, "input": 6, "output": 117, "reasoning": 0, "cache": {"write": 15661, "read": 0}},
                "cost": 0.00318809,
            },
        ),
        _evt(
            "step_finish",
            {
                "reason": "stop",
                "messageID": "msg_1",
                "tokens": {"total": 200, "input": 6, "output": 200, "reasoning": 0, "cache": {"write": 0, "read": 15661}},
                "cost": 0.000589628,
            },
        ),
    ]
)

QUOTA_ERROR_STREAM = "\n".join(
    [
        _evt("step_start", {"id": "prt_1", "messageID": "msg_1"}),
        _evt(
            "error",
            {
                "error": {
                    "name": "APIError",
                    "data": {
                        "message": "Upstream request failed: This Go model requires Global regions. Select Global in your workspace's Privacy settings to use it.",
                        "statusCode": 400,
                    },
                }
            },
        ),
    ]
)

CAPABILITY_ERROR_STREAM = "\n".join(
    [
        _evt(
            "error",
            {"error": {"name": "APIError", "data": {"message": "provider returned an unexpected response"}}},
        ),
    ]
)

PERMISSION_STREAM = "\n".join(
    [
        _evt("step_start", {"id": "prt_1", "messageID": "msg_1"}),
        _evt(
            "tool_use",
            {
                "tool": "bash",
                "callID": "toolu_2",
                "state": {
                    "status": "error",
                    "input": {"command": "type C:\\other\\repo\\file.py"},
                    "error": "Permission denied: external_directory access is not enabled for this session (auto-rejected)",
                },
            },
        ),
        _evt(
            "step_finish",
            {
                "reason": "stop",
                "messageID": "msg_1",
                "tokens": {"total": 9000, "input": 6, "output": 43, "reasoning": 0, "cache": {"write": 0, "read": 8994}},
                "cost": 0.0004,
            },
        ),
    ]
)

LENGTH_ZERO_OUTPUT_STREAM = "\n".join(
    [
        _evt("step_start", {"id": "prt_1", "messageID": "msg_1"}),
        _evt("tool_use", {"tool": "read", "callID": "toolu_3", "state": {"status": "completed", "input": {"filePath": "a.py"}, "output": "text"}}),
        _evt(
            "step_finish",
            {
                "reason": "length",
                "messageID": "msg_1",
                "tokens": {"total": 32000, "input": 0, "output": 0, "reasoning": 32000, "cache": {"write": 0, "read": 0}},
                "cost": 0.05,
            },
        ),
    ]
)

MALFORMED_STREAM = "\n".join([">{not json}dont parse me", json.dumps({"type": "error", "sessionID": SESSION})])


def _fake_execute(stream: str, exit_code: int = 0, timed_out: bool = False):
    def fake(cmd, cwd, timeout):
        return exit_code, stream, "", timed_out

    return fake


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    return repo


@pytest.fixture
def routing_log(tmp_path: Path) -> Path:
    return tmp_path / ".hx" / "routing.jsonl"


def test_run_success(repo: Path, routing_log: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ocrun, "execute", _fake_execute(SUCCESS_STREAM, 0))
    result = ocrun.run("fix stats.py", "qwen3.8-flash", cwd=repo, routing_log=routing_log)

    assert result["ok"] is True
    assert result["failure"] is None
    assert result["session_id"] == SESSION
    assert result["cost"] == pytest.approx(0.003777718, abs=1e-6)
    assert round(result["cost"], 6) == 0.003778
    assert result["tool_calls"] == 1
    assert result["tool_errors"] == 0
    assert result["tokens"]["input"] == 12
    assert result["tokens"]["output"] == 317
    assert result["tokens"]["cache_write"] == 15661
    assert result["tokens"]["cache_read"] == 15661


def test_run_quota_error(repo: Path, routing_log: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ocrun, "execute", _fake_execute(QUOTA_ERROR_STREAM, 0))
    result = ocrun.run("do the thing", "deepseek-v4.1-flash", cwd=repo, routing_log=routing_log)

    assert result["ok"] is False
    assert result["failure"] is not None
    assert result["failure"]["kind"] == "quota"
    assert "global regions" in result["failure"]["reason"].lower()


def test_run_rate_limit_error_is_quota(repo: Path, routing_log: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stream = json.dumps({"type": "error", "sessionID": SESSION, "error": {"message": "HTTP 429 Too Many Requests"}})
    monkeypatch.setattr(ocrun, "execute", _fake_execute(stream, 0))
    result = ocrun.run("do", "minimax-m3", cwd=repo, routing_log=routing_log)

    assert result["failure"] is not None
    assert result["failure"]["kind"] == "quota"


def test_run_non_quota_error_is_capability(repo: Path, routing_log: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ocrun, "execute", _fake_execute(CAPABILITY_ERROR_STREAM, 0))
    result = ocrun.run("do the thing", "glm-5.3-flash", cwd=repo, routing_log=routing_log)

    assert result["ok"] is False
    assert result["failure"] is not None
    assert result["failure"]["kind"] == "capability"


def test_run_permission_rejected_is_tool_failure(repo: Path, routing_log: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ocrun, "execute", _fake_execute(PERMISSION_STREAM, 0))
    result = ocrun.run("read other repo", "mimo-v2.6-flash", cwd=repo, routing_log=routing_log)

    assert result["ok"] is False
    assert result["failure"] is not None
    assert result["failure"]["kind"] == "tool"
    assert result["tool_errors"] == 1
    assert result["tool_calls"] == 1


def test_run_finish_length_zero_output_is_capability(repo: Path, routing_log: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ocrun, "execute", _fake_execute(LENGTH_ZERO_OUTPUT_STREAM, 0))
    result = ocrun.run("multi file correction", "minimax-m3", cwd=repo, routing_log=routing_log)

    assert result["ok"] is False
    assert result["failure"] is not None
    assert result["failure"]["kind"] == "capability"
    assert "length" in result["failure"]["reason"]


def test_run_timeout(repo: Path, routing_log: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ocrun, "execute", _fake_execute(SUCCESS_STREAM, -1, timed_out=True))
    result = ocrun.run("big task", "qwen3.7-plus", cwd=repo, routing_log=routing_log)

    assert result["ok"] is False
    assert result["failure"] is not None
    assert result["failure"]["kind"] == "timeout"


def test_run_nonzero_exit_without_silent_failure(repo: Path, routing_log: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ocrun, "execute", _fake_execute("", 3))
    result = ocrun.run("x", "qwen3.8-flash", cwd=repo, routing_log=routing_log)

    assert result["ok"] is False
    assert result["failure"] is not None
    assert result["failure"]["kind"] == "exit"


def test_run_tolerates_malformed_lines(repo: Path, routing_log: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ocrun, "execute", _fake_execute(MALFORMED_STREAM, 0))
    result = ocrun.run("x", "qwen3.8-flash", cwd=repo, routing_log=routing_log)

    assert result["ok"] is False
    assert result["failure"]["kind"] == "capability"


def test_run_appends_routing_outcome(repo: Path, routing_log: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ocrun, "execute", _fake_execute(SUCCESS_STREAM, 0))
    ocrun.run("fix stats.py", "qwen3.8-flash", title="stats", cwd=repo, routing_log=routing_log)

    assert routing_log.exists()
    lines = [line for line in routing_log.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["kind"] == "ocrun"
    assert entry["title"] == "stats"
    assert entry["model"] == "qwen3.8-flash"
    assert entry["ok"] is True
    assert entry["session_id"] == SESSION
    assert "ts" in entry


def test_run_quietly_keeps_going_when_routing_log_unwritable(repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ocrun, "execute", _fake_execute(SUCCESS_STREAM, 0))
    blocked = tmp_path / "not-a-dir" / "routing.jsonl"
    (tmp_path / "not-a-dir").write_text("a file blocks the parent dir", encoding="utf-8")
    result = ocrun.run("x", "qwen3.8-flash", cwd=repo, routing_log=blocked)

    assert result["ok"] is True


def test_build_command_flags(tmp_path: Path) -> None:
    cmd = ocrun.build_command("p", "qq", None, tmp_path, None)
    assert cmd[:4] == ["opencode", "run", "-m", "opencode-go/qq"]
    assert cmd[-1] == "p"
    assert "--dir" in cmd

    cmd2 = ocrun.build_command("p", "opencode-go/qq", "high", tmp_path, "Title")
    assert "opencode-go/qq" in cmd2
    assert "--variant" in cmd2
    assert "high" in cmd2
    assert "--title" in cmd2


def test_main_prints_json_and_exit_code(repo: Path, routing_log: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    monkeypatch.setattr(ocrun, "execute", _fake_execute(SUCCESS_STREAM, 0))
    code = ocrun.main(["fix it", "--model", "qwen3.8-flash", "--cwd", str(repo)])
    out1 = capsys.readouterr().out
    assert code == 0
    assert json.loads(out1)["ok"] is True

    monkeypatch.setattr(ocrun, "execute", _fake_execute(QUOTA_ERROR_STREAM, 0))
    code = ocrun.main(["fix it", "--model", "deepseek-v4.1-flash", "--cwd", str(repo)])
    out2 = capsys.readouterr().out
    assert code == 1
    assert json.loads(out2)["failure"]["kind"] == "quota"


def test_default_routing_log_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HX_ROUTING_LOG", str(tmp_path / "routing.jsonl"))
    assert ocrun.default_routing_log() == tmp_path / "routing.jsonl"
