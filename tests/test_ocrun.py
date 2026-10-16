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


def _fake_execute(stream: str, exit_code: int = 0, timed_out: bool = False, terminated: bool = True):
    def fake(cmd, cwd, timeout):
        return exit_code, stream, "", timed_out, terminated

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
    monkeypatch.setattr(ocrun, "execute", _fake_execute(SUCCESS_STREAM, -1, timed_out=True, terminated=True))
    result = ocrun.run("big task", "qwen3.7-plus", cwd=repo, routing_log=routing_log)

    assert result["ok"] is False
    assert result["failure"] is not None
    assert result["failure"]["kind"] == "tool"
    assert result["failure"]["reason"] == "timeout"


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
    ocrun.run(
        "fix stats.py",
        "qwen3.8-flash",
        title="stats",
        cwd=repo,
        routing_log=routing_log,
        task_id="task_1",
        decision_id="dec_1",
    )

    assert routing_log.exists()
    lines = [line for line in routing_log.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["kind"] == "outcome"
    assert entry["task_id"] == "task_1"
    assert entry["decision_id"] == "dec_1"
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
    code = ocrun.main(
        [
            "fix it",
            "--model", "qwen3.8-flash",
            "--cwd", str(repo),
            "--task-id", "task_main",
            "--decision-id", "dec_main",
        ]
    )
    out1 = capsys.readouterr().out
    assert code == 0
    assert json.loads(out1)["ok"] is True

    expected_log = repo / ".hx" / "routing.jsonl"
    assert expected_log.exists()
    entry = json.loads(expected_log.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert entry["kind"] == "outcome"
    assert entry["task_id"] == "task_main"
    assert entry["decision_id"] == "dec_main"

    monkeypatch.setattr(ocrun, "execute", _fake_execute(QUOTA_ERROR_STREAM, 0))
    code = ocrun.main(["fix it", "--model", "deepseek-v4.1-flash", "--cwd", str(repo)])
    out2 = capsys.readouterr().out
    assert code == 1
    assert json.loads(out2)["failure"]["kind"] == "quota"


def test_default_routing_log_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HX_ROUTING_LOG", str(tmp_path / "routing.jsonl"))
    assert ocrun.default_routing_log() == tmp_path / "routing.jsonl"


# ---------- regression tests for reviewer findings ----------


def test_timeout_without_termination_confirmation(repo: Path, routing_log: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Finding 6: unconfirmed termination is still a tool failure but noted."""
    monkeypatch.setattr(ocrun, "execute", _fake_execute(SUCCESS_STREAM, -1, timed_out=True, terminated=False))
    result = ocrun.run("big task", "qwen3.7-plus", cwd=repo, routing_log=routing_log)

    assert result["ok"] is False
    assert result["failure"]["kind"] == "tool"
    assert "timeout" in result["failure"]["reason"].lower()


def test_empty_output_exit_zero_is_incomplete(repo: Path, routing_log: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Finding 7: exit 0 with empty output is an incomplete event stream."""
    monkeypatch.setattr(ocrun, "execute", _fake_execute("", 0))
    result = ocrun.run("x", "qwen3.8-flash", cwd=repo, routing_log=routing_log)

    assert result["ok"] is False
    assert result["failure"]["kind"] == "tool"
    assert result["failure"]["reason"] == "incomplete event stream"


def test_garbage_output_exit_zero_is_incomplete(repo: Path, routing_log: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Finding 7: exit 0 with unparseable output is an incomplete event stream."""
    monkeypatch.setattr(ocrun, "execute", _fake_execute(">{not json}\n\nmore garbage", 0))
    result = ocrun.run("x", "qwen3.8-flash", cwd=repo, routing_log=routing_log)

    assert result["ok"] is False
    assert result["failure"]["kind"] == "tool"
    assert result["failure"]["reason"] == "incomplete event stream"


def test_truncated_output_without_step_finish_is_incomplete(repo: Path, routing_log: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Finding 7: a step_start without a final step_finish is incomplete."""
    stream = _evt("step_start", {"id": "prt_1", "messageID": "msg_1"})
    monkeypatch.setattr(ocrun, "execute", _fake_execute(stream, 0))
    result = ocrun.run("x", "qwen3.8-flash", cwd=repo, routing_log=routing_log)

    assert result["ok"] is False
    assert result["failure"]["kind"] == "tool"
    assert result["failure"]["reason"] == "incomplete event stream"


def test_missing_session_id_is_incomplete(repo: Path, routing_log: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Finding 7: terminal success without a session id is incomplete."""
    stream = json.dumps({"type": "step_finish", "part": {"reason": "stop", "tokens": {"output": 1}}})
    monkeypatch.setattr(ocrun, "execute", _fake_execute(stream, 0))
    result = ocrun.run("x", "qwen3.8-flash", cwd=repo, routing_log=routing_log)

    assert result["ok"] is False
    assert result["failure"]["kind"] == "tool"
    assert result["failure"]["reason"] == "incomplete event stream"


def test_error_finish_reason_is_incomplete(repo: Path, routing_log: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Finding 7: a final step_finish with reason=error is not terminal success."""
    stream = _evt("step_finish", {"reason": "error", "messageID": "msg_1", "tokens": {"output": 1}})
    monkeypatch.setattr(ocrun, "execute", _fake_execute(stream, 0))
    result = ocrun.run("x", "qwen3.8-flash", cwd=repo, routing_log=routing_log)

    assert result["ok"] is False
    assert result["failure"]["kind"] == "tool"
    assert result["failure"]["reason"] == "incomplete event stream"


def test_unresolved_tool_error_at_end_is_tool_failure(repo: Path, routing_log: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Finding 7: tool error with no later successful step_finish -> tool failure."""
    stream = "\n".join(
        [
            _evt("step_start", {"id": "prt_1", "messageID": "msg_1"}),
            _evt(
                "tool_use",
                {
                    "tool": "bash",
                    "callID": "toolu_err",
                    "state": {"status": "error", "error": "command failed"},
                },
            ),
        ]
    )
    monkeypatch.setattr(ocrun, "execute", _fake_execute(stream, 0))
    result = ocrun.run("x", "qwen3.8-flash", cwd=repo, routing_log=routing_log)

    assert result["ok"] is False
    assert result["failure"]["kind"] == "tool"
    assert "unresolved tool error" in result["failure"]["reason"].lower()


def test_resolved_tool_error_with_stop_is_ok(repo: Path, routing_log: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Finding 7: tool error followed by a successful step_finish is not unresolved."""
    stream = "\n".join(
        [
            _evt("step_start", {"id": "prt_1", "messageID": "msg_1"}),
            _evt(
                "tool_use",
                {
                    "tool": "bash",
                    "callID": "toolu_err",
                    "state": {"status": "error", "error": "command failed"},
                },
            ),
            _evt(
                "step_finish",
                {
                    "reason": "stop",
                    "messageID": "msg_1",
                    "tokens": {"total": 100, "input": 10, "output": 90, "reasoning": 0, "cache": {"write": 0, "read": 0}},
                    "cost": 0.001,
                },
            ),
        ]
    )
    monkeypatch.setattr(ocrun, "execute", _fake_execute(stream, 0))
    result = ocrun.run("x", "qwen3.8-flash", cwd=repo, routing_log=routing_log)

    assert result["ok"] is True
    assert result["failure"] is None


def test_routing_log_uses_cwd_project_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Finding 9: ocrun logs to the project root of --cwd, not the caller's root."""
    repo = tmp_path / "effective_repo"
    repo.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ocrun, "execute", _fake_execute(SUCCESS_STREAM, 0))
    result = ocrun.run("x", "qwen3.8-flash", cwd=repo)

    assert result["ok"] is True
    expected_log = repo / ".hx" / "routing.jsonl"
    assert expected_log.exists()
    entry = json.loads(expected_log.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert entry["kind"] == "outcome"



def test_completed_read_mentioning_rejection_is_not_a_permission_failure(
    repo: Path, routing_log: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A successful read whose output contains 'auto-rejected permission' must not fail the run."""
    read = _evt(
        "tool_use",
        {"tool": "read", "callID": "toolu_r", "state": {"status": "completed",
         "output": "- auto-rejected permission ask -> tool failure; external_directory"}},
    )
    lines = SUCCESS_STREAM.split("\n")
    stream = "\n".join([lines[0], read] + lines[1:])
    monkeypatch.setattr(ocrun, "execute", _fake_execute(stream, 0))
    result = ocrun.run("x", "qwen3.8-flash", cwd=repo, routing_log=routing_log)
    assert result["ok"] is True, result["failure"]
