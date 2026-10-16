"""Tests for the claude hooks; hooks are exercised as real subprocesses."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parent.parent / "hooks"


def _run_hook(name: str, payload: dict | None, cwd: Path, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    stdin = json.dumps(payload) if payload is not None else ""
    return subprocess.run(
        [sys.executable, str(HOOKS_DIR / name)],
        input=stdin,
        capture_output=True,
        text=True,
        cwd=str(cwd),
        env=env,
        timeout=60,
    )


# ---------- claude_statusline.py ---------------------------------------------


def test_statusline_writes_session_and_aggregate_state(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    payload = {
        "session_id": "session-abc-123",
        "context_window": {"used_percentage": 41.7, "remaining_percentage": 58.3},
        "rate_limits": {
            "five_hour": {"used_percentage": 30.0},
            "seven_day": {"used_percentage": 10.0},
        },
    }
    proc = _run_hook("claude_statusline.py", payload, tmp_path, {"HX_STATE_DIR": str(state_dir)})

    assert proc.returncode == 0
    per_session = state_dir / "claude-status-session-abc-123.json"
    aggregate = state_dir / "claude-status.json"
    assert per_session.exists()
    assert aggregate.exists()

    record = json.loads(per_session.read_text(encoding="utf-8"))
    assert record["session_id"] == "session-abc-123"
    assert record["context_window"]["used_percentage"] == 41.7
    assert record["rate_limits"]["five_hour"]["used_percentage"] == 30.0
    assert "timestamp" in record

    aggregate_record = json.loads(aggregate.read_text(encoding="utf-8"))
    assert aggregate_record["context_window"]["used_percentage"] == 41.7

    assert "ctx 42%" in proc.stdout
    assert "5h 30% wk 10%" in proc.stdout
    assert proc.stdout.count("\n") == 1


def test_statusline_missing_session_id_goes_to_unknown(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    payload = {"context_window": {"used_percentage": 80.0}}
    proc = _run_hook("claude_statusline.py", payload, tmp_path, {"HX_STATE_DIR": str(state_dir)})

    assert proc.returncode == 0
    assert (state_dir / "claude-status-unknown.json").exists()


def test_statusline_never_fails_on_garbage_input(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    proc = subprocess.run(
        [sys.executable, str(HOOKS_DIR / "claude_statusline.py")],
        input="this is not json {",
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
        env={**os.environ, "HX_STATE_DIR": str(state_dir)},
        timeout=60,
    )
    assert proc.returncode == 0


def test_statusline_replaces_file_atomically_across_runs(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    _run_hook("claude_statusline.py", {"session_id": "s", "context_window": {"used_percentage": 10.0}}, tmp_path, {"HX_STATE_DIR": str(state_dir)})
    _run_hook("claude_statusline.py", {"session_id": "s", "context_window": {"used_percentage": 20.0}}, tmp_path, {"HX_STATE_DIR": str(state_dir)})

    record = json.loads((state_dir / "claude-status-s.json").read_text(encoding="utf-8"))
    assert record["context_window"]["used_percentage"] == 20.0
    leftovers = list(state_dir.glob("*.tmp"))
    assert leftovers == []


# ---------- claude_precompact.py ---------------------------------------------


def test_precompact_prints_instructions_and_active_tasks(tmp_path: Path) -> None:
    proc = _run_hook("claude_precompact.py", {"cwd": str(tmp_path)}, tmp_path)

    assert proc.returncode == 0
    out = proc.stdout
    lowered = out.lower()
    assert "task ids" in lowered
    assert "exact error" in lowered
    assert "decisions" in lowered
    assert "next actions" in lowered
    assert "Active tasks (hx ledger)" in out
    assert "[]" in out


def test_precompact_never_fails(tmp_path: Path) -> None:
    proc = _run_hook("claude_precompact.py", None, tmp_path)
    assert proc.returncode == 0


# ---------- claude_session_compact.py ----------------------------------------


def _make_checkpoints(tmp_path: Path) -> None:
    checkpoints = tmp_path / ".hx" / "checkpoints"
    checkpoints.mkdir(parents=True)
    (checkpoints / "0001.md").write_text("# Checkpoint: old\n\n## Next action\nold action\n", encoding="utf-8")
    (checkpoints / "0002.md").write_text("# Checkpoint: newest\n\n## Next action\nthe newest action\n", encoding="utf-8")
    old_ts = (420.0, 420.0)
    os.utime(checkpoints / "0001.md", old_ts)
    new_ts = (840.0, 840.0)
    os.utime(checkpoints / "0002.md", new_ts)


def test_session_compact_prints_newest_checkpoint(tmp_path: Path) -> None:
    _make_checkpoints(tmp_path)
    proc = _run_hook("claude_session_compact.py", {"cwd": str(tmp_path)}, tmp_path)

    assert proc.returncode == 0
    out = proc.stdout
    assert "Active tasks (hx ledger)" in out
    assert "# Checkpoint: newest" in out
    assert "the newest action" in out
    assert "# Checkpoint: old" not in out


def test_session_compact_no_checkpoints_found(tmp_path: Path) -> None:
    proc = _run_hook("claude_session_compact.py", {"cwd": str(tmp_path)}, tmp_path)
    assert proc.returncode == 0
    assert "no checkpoint found" in proc.stdout


def test_session_compact_never_fails(tmp_path: Path) -> None:
    proc = _run_hook("claude_session_compact.py", None, tmp_path)
    assert proc.returncode == 0
