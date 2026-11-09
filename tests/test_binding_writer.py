"""The v2 binding writer shared by the Claude and Codex hooks.

The hc lookup and the process ancestor list are injected, so these tests never
touch a live terminal.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / "hooks"
sys.path.insert(0, str(HOOKS))

import hc_binding


@pytest.fixture(autouse=True)
def synthetic_images(monkeypatch):
    monkeypatch.setattr(hc_binding, "process_image", {123: "claude.exe", 300: "codex.exe"}.get)


def _load_statusline():
    spec = importlib.util.spec_from_file_location(
        "claude_statusline", HOOKS / "claude_statusline.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _observed(**extra):
    base = {
        "address": "t1",
        "state": "live",
        "pid": 123,
        "created_ms": 111,
        "status": {"app": "claude-code", "state": "idle"},
    }
    base.update(extra)
    return base


def _path(tmp_path: Path) -> Path:
    return tmp_path / "state" / "hc-bindings" / "t1.json"


@pytest.mark.parametrize("provider,app,image", [
    ("claude", "claude-code", "C:/tools/claude.exe"),
    ("codex", "codex", "C:/tools/codex.exe"),
])
def test_wrapped_provider_binding_expires_when_provider_exits(
    tmp_path, monkeypatch, provider, app, image,
) -> None:
    from hx import host

    monkeypatch.setenv("HX_STATE_DIR", str(tmp_path / "state"))
    images = {100: "powershell.exe", 200: "pwsh.exe", 300: image,
              400: "cmd.exe", 500: "python.exe"}
    monkeypatch.setattr(hc_binding, "process_image", images.get)
    raw = _observed(pid=100, status={"app": app})
    out = hc_binding.publish(
        provider, "ses_abc", environ={"HC_TERMINAL_ID": "t1"},
        run_hc=lambda terminal: raw, ancestors=[400, 300, 200, 100], own_pid=500,
    )
    assert out is not None
    assert out["provider_pid"] == 300
    parents = {500: 400, 400: 300, 300: 200, 200: 100, 100: 1}
    assert host._hc_binding(raw, parents) == ("ses_abc", "v2 binding")
    assert host._hc_binding(raw, {200: 100, 100: 1}) == (None, "provider pid is not live")


def test_publish_writes_the_v2_record_atomically(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("HX_STATE_DIR", str(tmp_path / "state"))
    out = hc_binding.publish(
        "claude",
        "ses_abc",
        environ={"HC_TERMINAL_ID": "t1"},
        run_hc=lambda terminal: _observed(),
        ancestors=[],
        own_pid=123,
        now_ms=222,
    )
    assert out == {
        "v": 2,
        "terminal": "t1",
        "terminal_created_ms": 111,
        "provider": "claude",
        "session_id": "ses_abc",
        "provider_pid": 123,
        "written_ms": 222,
    }
    assert json.loads(_path(tmp_path).read_text(encoding="utf-8")) == out
    assert list(_path(tmp_path).parent.glob("*.tmp")) == []
    assert capsys.readouterr().err == ""


def test_provider_pid_is_the_provider_executable_below_the_terminal(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HX_STATE_DIR", str(tmp_path / "state"))
    out = hc_binding.publish(
        "codex",
        "ses_abc",
        environ={"HC_TERMINAL_ID": "t1"},
        run_hc=lambda terminal: _observed(),
        ancestors=[300, 123, 50],
        own_pid=500,
        now_ms=222,
    )
    assert out is not None
    assert out["provider_pid"] == 300


def test_provider_is_the_terminal_when_the_hook_is_its_direct_child(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HX_STATE_DIR", str(tmp_path / "state"))
    out = hc_binding.publish(
        "claude",
        "ses_abc",
        environ={"HC_TERMINAL_ID": "t1"},
        run_hc=lambda terminal: _observed(),
        ancestors=[123],
        own_pid=500,
    )
    assert out is not None
    assert out["provider_pid"] == 123


def test_own_process_is_the_provider_when_it_owns_the_terminal(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HX_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(hc_binding, "process_image", {123: "opencode.exe"}.get)
    out = hc_binding.publish(
        "opencode",
        "ses_abc",
        environ={"HC_TERMINAL_ID": "t1"},
        run_hc=lambda terminal: _observed(),
        ancestors=[50],
        own_pid=123,
    )
    assert out is not None
    assert out["provider_pid"] == 123


@pytest.mark.parametrize("image", [None, "python.exe", "codex-helper.exe", "claude.exe"])
def test_unknown_provider_image_refuses_instead_of_binding_a_shell(
    tmp_path, monkeypatch, capsys, image,
) -> None:
    monkeypatch.setenv("HX_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(hc_binding, "process_image", {500: image, 50: "codex.exe"}.get)
    out = hc_binding.publish(
        "codex", "ses_abc", environ={"HC_TERMINAL_ID": "t1"},
        run_hc=lambda terminal: _observed(), ancestors=[123, 50], own_pid=500,
    )
    assert out is None
    assert not _path(tmp_path).exists()
    assert capsys.readouterr().err == (
        "hc-binding: refusing t1: no codex executable in this terminal's ancestor chain\n"
    )


def test_publish_refuses_without_hc_terminal(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("HX_STATE_DIR", str(tmp_path / "state"))
    out = hc_binding.publish(
        "claude",
        "ses_abc",
        environ={},
        run_hc=lambda terminal: _observed(),
        ancestors=[],
        own_pid=123,
    )
    assert out is None
    assert not _path(tmp_path).exists()
    err = capsys.readouterr().err
    assert err.count("\n") == 1
    assert "HC_TERMINAL_ID" in err


def test_publish_refuses_a_missing_session_without_touching_the_file(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("HX_STATE_DIR", str(tmp_path / "state"))
    out = hc_binding.publish(
        "codex",
        None,
        environ={"HC_TERMINAL_ID": "t1"},
        run_hc=lambda terminal: _observed(),
        ancestors=[],
        own_pid=123,
    )
    assert out is None
    assert not _path(tmp_path).exists()
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("observed, ancestors, own_pid, needle", [
    (_observed(state="finished"), [], 123, "not live"),
    (_observed(address="t9"), [], 123, "another terminal"),
    (_observed(created_ms=None), [], 123, "generation"),
    (_observed(created_ms=True), [], 123, "generation"),
    (_observed(pid=None), [], 123, "no pid"),
    (_observed(pid=999), [], 123, "not this process"),
    (_observed(pid=999), [500], 123, "not this process"),
    (None, [], 123, "no terminal detail"),
])
def test_publish_refuses_a_stale_or_foreign_terminal(
    tmp_path, monkeypatch, capsys, observed, ancestors, own_pid, needle
) -> None:
    monkeypatch.setenv("HX_STATE_DIR", str(tmp_path / "state"))
    out = hc_binding.publish(
        "claude",
        "ses_abc",
        environ={"HC_TERMINAL_ID": "t1"},
        run_hc=lambda terminal: observed,
        ancestors=ancestors,
        own_pid=own_pid,
    )
    assert out is None
    assert not _path(tmp_path).exists()
    err = capsys.readouterr().err
    assert err.count("\n") == 1
    assert needle in err


def test_publish_refuses_an_unsafe_terminal_address(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("HX_STATE_DIR", str(tmp_path / "state"))
    out = hc_binding.publish(
        "claude",
        "ses_abc",
        environ={"HC_TERMINAL_ID": "../t1"},
        run_hc=lambda terminal: _observed(),
        ancestors=[],
        own_pid=123,
    )
    assert out is None
    assert not (tmp_path / "state" / "hc-bindings").exists()
    assert "safe terminal address" in capsys.readouterr().err


def test_publish_refuses_an_unknown_provider(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("HX_STATE_DIR", str(tmp_path / "state"))
    out = hc_binding.publish(
        "other",
        "ses_abc",
        environ={"HC_TERMINAL_ID": "t1"},
        run_hc=lambda terminal: _observed(),
        ancestors=[],
        own_pid=123,
    )
    assert out is None
    assert not _path(tmp_path).exists()
    assert "unknown provider" in capsys.readouterr().err


def test_write_failure_leaves_no_partial_or_temp_file(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("HX_STATE_DIR", str(tmp_path / "state"))

    def fail_replace(*args):
        raise OSError("rename failed")

    monkeypatch.setattr(hc_binding.os, "replace", fail_replace)
    out = hc_binding.publish(
        "claude",
        "ses_abc",
        environ={"HC_TERMINAL_ID": "t1"},
        run_hc=lambda terminal: _observed(),
        ancestors=[],
        own_pid=123,
    )
    assert out is None
    assert not _path(tmp_path).exists()
    assert list(_path(tmp_path).parent.glob("*.tmp")) == []
    assert "could not write" in capsys.readouterr().err


def test_statusline_publishes_the_claude_session(tmp_path, monkeypatch) -> None:
    module = _load_statusline()
    calls = []
    monkeypatch.setattr(hc_binding, "publish", lambda provider, session: calls.append((provider, session)))
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"session_id": "ses_abc"})))
    monkeypatch.setenv("HX_STATE_DIR", str(tmp_path / "state"))
    assert module.main() == 0
    assert calls == [("claude", "ses_abc")]


def test_statusline_does_not_publish_an_unknown_session(tmp_path, monkeypatch) -> None:
    module = _load_statusline()
    calls = []
    monkeypatch.setattr(hc_binding, "publish", lambda provider, session: calls.append((provider, session)))
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"context_window": {}})))
    monkeypatch.setenv("HX_STATE_DIR", str(tmp_path / "state"))
    assert module.main() == 0
    assert calls == []
