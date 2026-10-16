"""Tests for the codex worker of hx.ocrun (faked launch, fake CODEX_HOME)."""

from __future__ import annotations

import datetime
import json
from pathlib import Path

import pytest

from hx import ocrun

RUN_DIR = "runs-codex"


def _meta(session: str, cwd: Path, ts: str) -> str:
    return json.dumps(
        {
            "type": "session_meta",
            "timestamp": ts,
            "payload": {"session_id": session, "id": session, "cwd": str(cwd), "timestamp": ts},
        }
    )


def _token_count(evt_total: int, reasoning: int, cached: int, out: int) -> str:
    return json.dumps(
        {
            "type": "event_msg",
            "timestamp": "2026-10-16T03:28:12.834Z",
            "payload": {
                "type": "token_count",
                "info": {
                    "total_token_usage": {
                        "input_tokens": evt_total,
                        "cached_input_tokens": cached,
                        "cache_write_input_tokens": 0,
                        "output_tokens": out,
                        "reasoning_output_tokens": reasoning,
                        "total_tokens": evt_total + out,
                    },
                    "last_token_usage": {"total_tokens": 10},
                    "model_context_window": 258400,
                },
            },
        }
    )


def _error_item(text: str) -> str:
    return json.dumps(
        {
            "type": "response_item",
            "timestamp": "2026-10-16T03:28:00.000Z",
            "payload": {"type": "error", "message": text},
        }
    )


def _tool_call() -> str:
    return json.dumps(
        {
            "type": "response_item",
            "timestamp": "2026-10-16T03:28:01.000Z",
            "payload": {"type": "function_call", "name": "shell"},
        }
    )


def _write_rollout(home: Path, cwd: Path, session: str, body: list[str], ts: str, name: str) -> Path:
    day_dir = home / "sessions" / "2026" / "10" / "16"
    day_dir.mkdir(parents=True, exist_ok=True)
    path = day_dir / f"rollout-2026-10-16T03-27-45-{name}.jsonl"
    path.write_text(_meta(session, cwd, ts) + "\n" + "\n".join(body) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def codex_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    home = tmp_path / "codex-home"
    home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.setattr(ocrun, "launch_visible", lambda script: True)

    def fake_wait(done: Path, timeout: float) -> bool:
        done.parent.joinpath(ocrun.VISIBLE_DONE_FILE).write_text('{"exit_code": 0}', encoding="utf-8")
        return True

    monkeypatch.setattr(ocrun, "wait_for_done", fake_wait)
    monkeypatch.setattr(ocrun.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(ocrun.paths, "project_root", lambda: tmp_path)
    return home, tmp_path


def test_codex_visible_ok_and_tokens(codex_env: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    home, root = codex_env
    # A stale rollout in another cwd must be ignored.
    _write_rollout(
        home,
        Path(r"C:\elsewhere"),
        "ses_old",
        [_token_count(1, 0, 0, 1)],
        "2026-10-16T01:00:00Z",
        "old",
    )

    def fake_rollout(cwd: Path, started_utc: object, home_arg: Path | None = None):
        session = "01a142c1-629e-70a1-86c2-e7f6398d83c2"
        path = home / "sessions" / "20261016" / "rollout.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            _meta(session, cwd, "2026-10-16T03:27:46.928Z")
            + "\n"
            + _tool_call()
            + "\n"
            + _token_count(160, 25, 120, 45)
            + "\n",
            encoding="utf-8",
        )
        return path, {"session_id": session, "cwd": str(cwd), "timestamp": "later"}

    monkeypatch.setattr(ocrun, "find_codex_rollout", fake_rollout)

    def fake_wait_realstarted(done: Path, timeout: float) -> bool:
        done.parent.joinpath(ocrun.VISIBLE_LAST_FILE).write_text("60 lines", encoding="utf-8")
        (done.parent / ocrun.VISIBLE_DONE_FILE).write_text('{"exit_code": 0}', encoding="utf-8")
        return True

    monkeypatch.setattr(ocrun, "wait_for_done", fake_wait_realstarted)

    result = ocrun.run(
        "count the lines",
        "gpt-5.6-luna",
        cwd=root,
        timeout=5.0,
        title="cx-ok",
        routing_log=root / "routing.jsonl",
        mode="visible",
        worker="codex",
        effort="low",
    )

    assert result["ok"] is True, result["failure"]
    assert result["worker"] == "codex"
    assert result["session_id"].startswith("01a142c1")
    assert result["tokens"]["input"] == 160
    assert result["tokens"]["reasoning"] == 25
    assert result["tokens"]["cache_read"] == 120
    assert result["tokens"]["output"] == 45
    assert result["exit_code"] == 0
    assert result["tool_calls"] == 1
    script = (root / ".hx" / "runs" / "cx-ok" / "run.ps1").read_text(encoding="utf-8")
    assert "codex exec -p trusted-dev -m 'gpt-5.6-luna'" in script
    assert "-c 'model_reasoning_effort=low'" in script
    assert "-s workspace-write --skip-git-repo-check" in script
    assert "-C '" + str(root) + "'" in script
    assert "-o " in script
    assert "last.txt" in script

    entry = json.loads((root / "routing.jsonl").read_text(encoding="utf-8").strip().splitlines()[-1])
    assert entry["worker"] == "codex"
    assert entry["effort"] == "low"


def test_codex_visible_bad_exit(codex_env: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    home, root = codex_env

    def fake_rollout(cwd: Path, started_utc: object, home_arg: Path | None = None):
        path = home / "sessions" / "rollout.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_meta("ses_x", cwd, "2026-10-16T03:27:46.928Z") + "\n", encoding="utf-8")
        return path, {}

    monkeypatch.setattr(ocrun, "find_codex_rollout", fake_rollout)

    def fake_wait(done: Path, timeout: float) -> bool:
        (done.parent / ocrun.VISIBLE_DONE_FILE).write_text('{"exit_code": 2}', encoding="utf-8")
        return True

    monkeypatch.setattr(ocrun, "wait_for_done", fake_wait)

    result = ocrun.run(
        "p",
        "gpt-5.6-luna",
        cwd=root,
        timeout=5.0,
        title="cx-exit",
        routing_log=root / "routing.jsonl",
        mode="visible",
        worker="codex",
        effort="low",
    )

    assert result["ok"] is False
    assert result["failure"]["kind"] == "exit"
    assert result["failure"]["reason"] == "codex exec exited 2"


def test_codex_visible_empty_last_message(codex_env: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    home, root = codex_env

    def fake_rollout(cwd: Path, started_utc: object, home_arg: Path | None = None):
        path = home / "sessions" / "rollout.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_meta("ses_x", cwd, "2026-10-16T03:27:46.928Z") + "\n", encoding="utf-8")
        return path, {}

    monkeypatch.setattr(ocrun, "find_codex_rollout", fake_rollout)
    monkeypatch.setattr(ocrun, "wait_for_done", lambda done, timeout: True)

    result = ocrun.run(
        "p",
        "gpt-5.6-luna",
        cwd=root,
        timeout=5.0,
        title="cx-empty",
        routing_log=root / "routing.jsonl",
        mode="visible",
        worker="codex",
        effort="low",
    )

    assert result["ok"] is False
    assert result["failure"] == {"kind": "tool", "reason": "empty last message (no last.txt)"}


def test_codex_warnings_are_not_failures(codex_env: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    home, root = codex_env

    def fake_rollout(cwd: Path, started_utc: object, home_arg: Path | None = None):
        session = "ses_warn"
        path = home / "sessions" / "rollout.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            _meta(session, cwd, "2026-10-16T03:27:46.928Z")
            + "\n"
            + _error_item("prefer_mxc endpoint is a preview; switch when the notice says so")
            + "\n"
            + _error_item("hook timeout clamped to 600s")
            + "\n"
            + _token_count(10, 0, 0, 5)
            + "\n",
            encoding="utf-8",
        )
        return path, {"session_id": session}

    monkeypatch.setattr(ocrun, "find_codex_rollout", fake_rollout)

    def fake_wait(done: Path, timeout: float) -> bool:
        (done.parent / ocrun.VISIBLE_LAST_FILE).write_text("done", encoding="utf-8")
        (done.parent / ocrun.VISIBLE_DONE_FILE).write_text('{"exit_code": 0}', encoding="utf-8")
        return True

    monkeypatch.setattr(ocrun, "wait_for_done", fake_wait)

    result = ocrun.run(
        "p",
        "gpt-5.6-luna",
        cwd=root,
        timeout=5.0,
        title="cx-warn",
        routing_log=root / "routing.jsonl",
        mode="visible",
        worker="codex",
        effort="low",
    )

    assert result["ok"] is True, result["failure"]


def test_codex_real_error_item_fails(codex_env: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    home, root = codex_env

    def fake_rollout(cwd: Path, started_utc: object, home_arg: Path | None = None):
        path = home / "sessions" / "rollout.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            _meta("ses_err", cwd, "2026-10-16T03:27:46.928Z")
            + "\n"
            + _error_item("HTTP 429 too many requests")
            + "\n"
            + _token_count(3, 0, 0, 2)
            + "\n",
            encoding="utf-8",
        )
        return path, {}

    monkeypatch.setattr(ocrun, "find_codex_rollout", fake_rollout)
    monkeypatch.setattr(ocrun, "wait_for_done", lambda done, timeout: True)

    result = ocrun.run(
        "p",
        "gpt-5.6-luna",
        cwd=root,
        timeout=5.0,
        title="cx-err",
        routing_log=root / "routing.jsonl",
        mode="visible",
        worker="codex",
        effort="low",
    )

    assert result["ok"] is False
    assert result["failure"] == {"kind": "quota", "reason": "HTTP 429 too many requests"}


def test_codex_no_matching_rollout_fails(codex_env: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    home, root = codex_env
    monkeypatch.setattr(ocrun, "find_codex_rollout", lambda cwd, started, home_arg=None: (None, None))
    monkeypatch.setattr(ocrun, "wait_for_done", lambda done, timeout: True)

    result = ocrun.run(
        "p",
        "gpt-5.6-luna",
        cwd=root,
        timeout=5.0,
        title="cx-none",
        routing_log=root / "routing.jsonl",
        mode="visible",
        worker="codex",
        effort="low",
    )

    assert result["ok"] is False
    assert result["failure"] == {"kind": "tool", "reason": "no codex rollout matching the run cwd"}


def test_codex_timeout_reports_unconfirmed(codex_env: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    home, root = codex_env
    monkeypatch.setattr(ocrun, "launch_visible", lambda script: True)
    monkeypatch.setattr(ocrun, "wait_for_done", lambda done, timeout: False)
    monkeypatch.setattr(ocrun, "find_codex_rollout", lambda cwd, started, home_arg=None: (None, None))

    result = ocrun.run(
        "p",
        "gpt-5.6-luna",
        cwd=root,
        timeout=1.0,
        title="cx-timeout",
        routing_log=root / "routing.jsonl",
        mode="visible",
        worker="codex",
        effort="low",
    )

    assert result["ok"] is False
    assert result["failure"]["reason"] == "termination-unconfirmed"
    assert result["retry_safe"] is False


def test_codex_requires_exit_zero_and_last_message_finding(
    codex_env: tuple[Path, Path],
) -> None:
    home, root = codex_env
    meta_ts = "2026-10-16T03:27:46.928Z"
    meta = _meta("ses_m", root, meta_ts)
    body = meta + "\n" + _token_count(42, 7, 200, 11) + "\n"
    session = _write_rollout(home, root, "ses_m", body.splitlines()[1:], meta_ts, "match")
    assert session.exists()

    before = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
    rollout_path, rollout_meta = ocrun.find_codex_rollout(root, before)
    assert rollout_path is not None
    assert rollout_meta["session_id"] == "ses_m"

    parsed = ocrun.parse_codex_rollout(session)
    assert parsed["session_id"] == "ses_m"
    assert parsed["tokens"]["input"] == 42
    assert parsed["tokens"]["reasoning"] == 7
    assert parsed["tokens"]["cache_read"] == 200
    assert parsed["tokens"]["output"] == 11


def test_codex_worker_rejects_headless(codex_env: tuple[Path, Path]) -> None:
    home, root = codex_env
    with pytest.raises(ValueError):
        ocrun.run("p", "gpt-5.6-luna", cwd=root, mode="headless", worker="codex")
