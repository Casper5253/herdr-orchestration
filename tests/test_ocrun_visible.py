"""Tests for hx.ocrun visible mode (faked window launch, session list, export)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hx import ocrun

TITLE = "vis-ok"


def _export_doc(session: str, *messages: list[dict]) -> dict:
    return {"info": {"id": session}, "messages": [{"info": {}, "parts": list(m)} for m in messages]}


def _finish_part(session: str, reason: str = "stop", out: int = 120) -> dict:
    return {
        "type": "step-finish",
        "sessionID": session,
        "reason": reason,
        "tokens": {"total": 300, "input": 100, "output": out, "reasoning": 12, "cache": {"read": 0, "write": 280}},
        "cost": 0.00042,
    }


SUCCESS_EXPORT = _export_doc(
    "ses_visible_ok",
    [{"type": "text", "text": "did the thing", "sessionID": "ses_visible_ok"}],
    [_finish_part("ses_visible_ok")],
)

ERROR_EXPORT = _export_doc(
    "ses_visible_ok",
    [
        {
            "type": "error",
            "sessionID": "ses_visible_ok",
            "error": {"name": "APIError", "data": {"message": "429 too many requests"}},
        }
    ],
)


@pytest.fixture
def ack(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Fake the opencode subprocess seam and record every argv used."""
    state: dict = {"argv": [], "export": SUCCESS_EXPORT, "title": TITLE, "session": "ses_visible_ok"}

    def fake_run(argv: list[str], **kwargs: object) -> object:
        state["argv"].append(argv)

        class Result:
            returncode = 0

            def __init__(self) -> None:
                if argv[:2] == ["opencode", "session"]:
                    payload = json.dumps(
                        [{"id": state["session"], "title": state["title"], "updated": 1}]
                    )
                else:
                    payload = json.dumps(state["export"])
                self.stdout = payload.encode("utf-8")

        return Result()

    def fake_wait(done: Path, timeout: float) -> bool:
        done.parent.joinpath(ocrun.VISIBLE_DONE_FILE).write_text('{"exit_code": 0}', encoding="utf-8")
        return True

    monkeypatch.setattr(ocrun.subprocess, "run", fake_run)
    monkeypatch.setattr(ocrun, "wait_for_done", fake_wait)
    monkeypatch.setattr(ocrun.time, "sleep", lambda seconds: None)
    return state


def test_visible_run_writes_and_launches_script(
    ack: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launched: list[Path] = []
    monkeypatch.setattr(ocrun, "launch_visible", lambda script: launched.append(script) or True)

    result = ocrun.run(
        "count README lines please, 'quote' kept",
        "glm-5.3-flash",
        cwd=tmp_path,
        timeout=5.0,
        title=TITLE,
        routing_log=tmp_path / "routing.jsonl",
        mode="visible",
    )
    assert result["ok"] is True

    assert len(launched) == 1
    script_path = launched[0]
    assert script_path.parent == tmp_path / ".hx" / "runs" / TITLE
    assert script_path.name == ocrun.VISIBLE_RUN_SCRIPT
    script = script_path.read_text(encoding="utf-8")
    assert "$host.ui.RawUI.WindowTitle = " + "'" + TITLE + "'" in script
    assert "opencode run -m 'opencode-go/glm-5.3-flash' --title 'vis-ok' --dir" in script
    assert "(Get-Content -Raw '" + str(script_path.parent / ocrun.VISIBLE_PROMPT_FILE) + "')" in script
    assert "{ exit_code = $code } | ConvertTo-Json" in script

    prompt = (script_path.parent / ocrun.VISIBLE_PROMPT_FILE).read_text(encoding="utf-8")
    assert prompt == "count README lines please, 'quote' kept"

    # opencode was only ever called with argv lists, never through a shell.
    assert ack["argv"][0][:4] == ["opencode", "session", "list", "--format"]
    assert ack["argv"][1][:2] == ["opencode", "export"]


def test_visible_run_classifies_export_success(
    ack: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ocrun, "launch_visible", lambda script: True)

    result = ocrun.run(
        "x",
        "glm-5.3-flash",
        cwd=tmp_path,
        timeout=5.0,
        title=TITLE,
        routing_log=tmp_path / "routing.jsonl",
        mode="visible",
    )

    assert result["ok"] is True
    assert result["failure"] is None
    assert result["session_id"] == "ses_visible_ok"
    assert result["exit_code"] == 0
    assert result["mode"] == "visible"
    assert result["tokens"]["output"] == 120
    assert result["tokens"]["reasoning"] == 12
    assert result["cost"] == 0.00042


def test_visible_run_quota_error_classification(
    ack: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ack["export"] = ERROR_EXPORT
    monkeypatch.setattr(ocrun, "launch_visible", lambda script: True)

    result = ocrun.run(
        "x",
        "glm-5.3-flash",
        cwd=tmp_path,
        timeout=5.0,
        title=TITLE,
        routing_log=tmp_path / "routing.jsonl",
        mode="visible",
    )

    assert result["ok"] is False
    assert result["failure"]["kind"] == "quota"


def test_visible_run_timeout_reports_unconfirmed(
    ack: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ocrun, "launch_visible", lambda script: True)
    monkeypatch.setattr(ocrun, "wait_for_done", lambda done, timeout: False)

    result = ocrun.run(
        "x",
        "glm-5.3-flash",
        cwd=tmp_path,
        timeout=1.0,
        title=TITLE,
        routing_log=tmp_path / "routing.jsonl",
        mode="visible",
    )

    assert result["ok"] is False
    assert result["failure"]["kind"] == "tool"
    assert result["failure"]["reason"] == "termination-unconfirmed"
    assert result["retry_safe"] is False


def test_visible_run_launch_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ocrun, "launch_visible", lambda script: False)

    result = ocrun.run(
        "x",
        "glm-5.3-flash",
        cwd=tmp_path,
        timeout=1.0,
        title=TITLE,
        routing_log=tmp_path / "routing.jsonl",
        mode="visible",
    )

    assert result["ok"] is False
    assert result["failure"]["reason"] == "failed to launch visible window"
    assert result["session_id"] is None


def test_cli_mode_defaults_from_herdr_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    selected: list = []

    def fake_run(*args: object, **kw: object) -> dict:
        selected.append(kw["mode"])
        return {"mode": kw["mode"], "ok": True}

    monkeypatch.setattr(ocrun, "run", fake_run)
    monkeypatch.setenv("HERDR_ENV", "1")

    ocrun.main(["--model", "glm-5.3-flash", "prompt text"])
    assert selected == ["visible"]

    selected.clear()
    ocrun.main(["--model", "glm-5.3-flash", "prompt text", "--headless"])
    assert selected == ["headless"]

    monkeypatch.delenv("HERDR_ENV", raising=False)
    selected.clear()
    ocrun.main(["--model", "glm-5.3-flash", "prompt text"])
    assert selected == ["headless"]


def test_run_library_default_is_headless(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HERDR_ENV", "1")

    class Args:
        pass

    captured: dict = {}

    def fake_execute(cmd: list[str], cwd: Path, timeout: float):
        captured["cmd"] = cmd
        return 0, json.dumps({"sessionID": "ses_h", "type": "turn_started"}) + "\n" + json.dumps(
            {
                "type": "step_finish",
                "part": {
                    "type": "step-finish",
                    "reason": "stop",
                    "tokens": {"total": 1, "input": 0, "output": 1, "reasoning": 0, "cache": {"read": 0, "write": 0}},
                    "cost": 0.000001,
                    "sessionID": "ses_h",
                },
            }
        ), "", False, True

    monkeypatch.setattr(ocrun, "execute", fake_execute)
    monkeypatch.setattr(ocrun.paths, "project_root", lambda: tmp_path)

    result = ocrun.run("x", "glm-5.3-flash", cwd=tmp_path, routing_log=tmp_path / "r.jsonl")
    assert result["mode"] == "headless"
    assert "--format" in captured["cmd"]
    assert "json" in captured["cmd"]


def test_unique_run_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HX_PROJECT_ROOT", raising=False)
    monkeypatch.setattr(ocrun.paths, "project_root", lambda: tmp_path)
    first = ocrun._unique_run_dir(tmp_path, "slug one")
    ocrun.write_run_script(first, "p", "glm-5.3-flash", None, first.name, tmp_path)
    second = ocrun._unique_run_dir(tmp_path, "slug one")
    assert first.exists()
    assert first.name != second.name
    assert ocrun._sanitize_title("bad title/pipe") == "bad-title-pipe"
    with pytest.raises(ValueError):
        ocrun._unique_run_dir(tmp_path, "with spaces!" * 10)
