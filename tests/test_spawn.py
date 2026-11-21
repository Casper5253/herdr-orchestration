"""Q-854: hx.spawn hides a child's console window only when the parent has none."""

from __future__ import annotations

import subprocess

import pytest

from hx import spawn

WINDOWS_FLAGS = hasattr(subprocess, "CREATE_NO_WINDOW")
requires_windows_flags = pytest.mark.skipif(
    not WINDOWS_FLAGS, reason="console window flags exist on Windows only")


def recorded_run(monkeypatch):
    calls = []

    def fake(*args, **kwargs):
        calls.append(kwargs)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(spawn.subprocess, "run", fake)
    return calls


@requires_windows_flags
def test_no_console_adds_create_no_window_and_devnull_stdin(monkeypatch):
    monkeypatch.setattr(spawn, "_no_console", lambda: True)
    calls = recorded_run(monkeypatch)

    spawn.run(["hc", "ls", "--json"], capture_output=True, timeout=30)

    assert calls[0]["creationflags"] == subprocess.CREATE_NO_WINDOW
    assert calls[0]["stdin"] == subprocess.DEVNULL
    assert calls[0]["timeout"] == 30


def test_a_console_parent_leaves_kwargs_untouched(monkeypatch):
    monkeypatch.setattr(spawn, "_no_console", lambda: False)
    calls = recorded_run(monkeypatch)

    spawn.run(["hc", "ls", "--json"], capture_output=True)

    assert "creationflags" not in calls[0]
    assert "stdin" not in calls[0]


def test_off_windows_there_is_no_console_to_hide(monkeypatch):
    monkeypatch.setattr(spawn.os, "name", "posix")
    calls = recorded_run(monkeypatch)

    spawn.run(["hc", "ls", "--json"], capture_output=True)

    assert "creationflags" not in calls[0]
    assert "stdin" not in calls[0]


@requires_windows_flags
def test_callers_own_stdin_and_creationflags_win(monkeypatch):
    monkeypatch.setattr(spawn, "_no_console", lambda: True)
    calls = recorded_run(monkeypatch)

    spawn.run(["x"], stdin=None,
              creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)

    assert calls[0]["stdin"] is None
    assert calls[0]["creationflags"] == (
        subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW)


@requires_windows_flags
def test_check_output_and_popen_share_the_adjustment(monkeypatch):
    monkeypatch.setattr(spawn, "_no_console", lambda: True)
    out_calls = []
    monkeypatch.setattr(spawn.subprocess, "check_output",
                        lambda *a, **kw: out_calls.append(kw) or b"")
    popen_calls = []
    monkeypatch.setattr(spawn.subprocess, "Popen",
                        lambda *a, **kw: popen_calls.append(kw) or object())

    spawn.check_output(["git", "status"], stderr=subprocess.DEVNULL)
    spawn.Popen(["hc", "ls"], stdout=subprocess.PIPE)

    assert out_calls[0]["creationflags"] == subprocess.CREATE_NO_WINDOW
    assert out_calls[0]["stdin"] == subprocess.DEVNULL
    assert popen_calls[0]["creationflags"] == subprocess.CREATE_NO_WINDOW
    assert popen_calls[0]["stdin"] == subprocess.DEVNULL


def test_real_detection_matches_getconsolewindow():
    if spawn.os.name != "nt":
        pytest.skip("Windows-only check")
    import ctypes
    attached = bool(ctypes.windll.kernel32.GetConsoleWindow())
    assert spawn._no_console() is (not attached)
    kwargs = spawn._hidden_console({"capture_output": True})
    assert ("creationflags" in kwargs) is (not attached)
