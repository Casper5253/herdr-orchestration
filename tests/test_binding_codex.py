"""The Codex SessionStart hook: payload parsing and the refusal path.

The publish call is injected in-process; the subprocess test covers the refusal
without an hc terminal, so no live terminal is touched.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / "hooks"
sys.path.insert(0, str(HOOKS))

import hc_binding


def _load_hook():
    spec = importlib.util.spec_from_file_location(
        "codex_session_binding", HOOKS / "codex_session_binding.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_inprocess(monkeypatch, payload) -> list:
    module = _load_hook()
    calls = []
    monkeypatch.setattr(hc_binding, "publish", lambda provider, session: calls.append((provider, session)))
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload if isinstance(payload, str) else json.dumps(payload)))
    assert module.main() == 0
    return calls


def test_session_start_publishes_the_codex_session(monkeypatch) -> None:
    calls = _run_inprocess(monkeypatch, {
        "hook_event_name": "SessionStart",
        "session_id": "ses_codex",
        "transcript_path": "C:/tmp/rollout.jsonl",
        "source": "startup",
    })
    assert calls == [("codex", "ses_codex")]


def test_session_start_accepts_a_payload_without_the_event_name(monkeypatch) -> None:
    assert _run_inprocess(monkeypatch, {"session_id": "ses_codex"}) == [("codex", "ses_codex")]


def test_other_events_and_missing_sessions_publish_nothing(monkeypatch) -> None:
    assert _run_inprocess(monkeypatch, {"hook_event_name": "Stop", "session_id": "ses_codex"}) == []
    assert _run_inprocess(monkeypatch, {"hook_event_name": "SessionStart"}) == []
    assert _run_inprocess(monkeypatch, {"hook_event_name": "SessionStart", "session_id": None}) == []


def test_garbage_payload_never_fails(monkeypatch) -> None:
    assert _run_inprocess(monkeypatch, "not json {") == []


def test_hook_refuses_without_hc_terminal(tmp_path: Path) -> None:
    env = os.environ.copy()
    env.pop("HC_TERMINAL_ID", None)
    env["HX_STATE_DIR"] = str(tmp_path / "state")
    proc = subprocess.run(
        [sys.executable, str(HOOKS / "codex_session_binding.py")],
        input=json.dumps({"hook_event_name": "SessionStart", "session_id": "ses_codex"}),
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert proc.returncode == 0
    assert not (tmp_path / "state" / "hc-bindings").exists()
    assert proc.stderr.count("\n") == 1
    assert "HC_TERMINAL_ID" in proc.stderr
