"""Tests for the claude hooks; hooks are exercised as real subprocesses."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from hx import checkpoint, ledger

HOOKS_DIR = Path(__file__).resolve().parent.parent / "hooks"


def _run_hook(name: str, payload: dict | None, cwd: Path, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    # pytest tmp_path can live inside the real repo; pin the project root so the
    # hook resolves the ledger the test created instead of the enclosing repo.
    cwd_source = payload.get("cwd") if payload else None
    project_root = str(Path(cwd_source if cwd_source else cwd).resolve())
    env["HX_PROJECT_ROOT"] = project_root
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


def _make_led(repo_root: Path) -> ledger.Ledger:
    return ledger.Ledger(
        ledger_path=repo_root / ".hx" / "ledger.json",
        leases_path=repo_root / ".hx" / "leases.json",
        lock_path=repo_root / ".hx" / "ledger.lock",
        repo_root=repo_root,
    )


def _add_assigned_task(led: ledger.Ledger, task_id: str) -> None:
    led.add_task(
        task_id=task_id,
        title=f"Task {task_id}",
        owner="test",
        files=[],
    )
    led.assign_task(task_id, owner="test")


def _write_checkpoint(
    repo_root: Path,
    led: ledger.Ledger,
    task_id: str,
    next_action: str,
    objective: str = "",
) -> Path:
    path = checkpoint.write_checkpoint(
        task_id,
        ledger=led,
        objective=objective or f"Objective for {task_id}",
        owner="test",
        status="in_progress",
        decisions="some decision",
        errors="none",
        tests="",
        next_action=next_action,
        references="",
    )
    # Ensure checkpoint mtime is strictly after the task's updated timestamp.
    future = time.time() + 1.0
    os.utime(path, (future, future))
    return path


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


def test_precompact_prints_instructions_and_active_task_next_actions(tmp_path: Path) -> None:
    led = _make_led(tmp_path)
    _add_assigned_task(led, "t1")
    _write_checkpoint(tmp_path, led, "t1", "finish the parser")

    proc = _run_hook("claude_precompact.py", {"cwd": str(tmp_path)}, tmp_path)

    assert proc.returncode == 0
    out = proc.stdout
    lowered = out.lower()
    assert "task ids" in lowered
    assert "exact error" in lowered
    assert "decisions" in lowered
    assert "next actions" in lowered
    assert "Next action for t1: finish the parser" in out
    assert "Active task checkpoints" in out


def test_precompact_reports_tasks_without_valid_checkpoint(tmp_path: Path) -> None:
    led = _make_led(tmp_path)
    _add_assigned_task(led, "t1")
    _add_assigned_task(led, "t2")
    _write_checkpoint(tmp_path, led, "t1", "finish the parser")

    proc = _run_hook("claude_precompact.py", {"cwd": str(tmp_path)}, tmp_path)

    assert proc.returncode == 0
    out = proc.stdout
    assert "Next action for t1: finish the parser" in out
    assert "Next action for t2:" not in out
    assert "Tasks without a valid checkpoint: t2" in out


def test_precompact_never_fails(tmp_path: Path) -> None:
    proc = _run_hook("claude_precompact.py", None, tmp_path)
    assert proc.returncode == 0


# ---------- claude_session_compact.py ----------------------------------------


def test_session_compact_prints_valid_checkpoints_for_active_tasks(tmp_path: Path) -> None:
    led = _make_led(tmp_path)
    _add_assigned_task(led, "t1")
    _add_assigned_task(led, "t2")
    _write_checkpoint(tmp_path, led, "t1", "do t1 next")
    _write_checkpoint(tmp_path, led, "t2", "do t2 next")

    proc = _run_hook("claude_session_compact.py", {"cwd": str(tmp_path)}, tmp_path)

    assert proc.returncode == 0
    out = proc.stdout
    assert "Next action for t1: do t1 next" in out
    assert "Next action for t2: do t2 next" in out
    assert "## Rest of checkpoint for t1" in out
    assert "## Rest of checkpoint for t2" in out


def test_session_compact_ignores_non_active_tasks(tmp_path: Path) -> None:
    led = _make_led(tmp_path)
    _add_assigned_task(led, "t1")
    _add_assigned_task(led, "t2")
    led.set_status("t2", "done")
    _write_checkpoint(tmp_path, led, "t1", "do t1 next")
    _write_checkpoint(tmp_path, led, "t2", "do t2 next")

    proc = _run_hook("claude_session_compact.py", {"cwd": str(tmp_path)}, tmp_path)

    assert proc.returncode == 0
    out = proc.stdout
    assert "Next action for t1: do t1 next" in out
    assert "Next action for t2:" not in out


def test_session_compact_ignores_open_status_tasks(tmp_path: Path) -> None:
    led = _make_led(tmp_path)
    led.add_task("t1", title="Task t1", owner="test", files=[])
    _write_checkpoint(tmp_path, led, "t1", "do t1 next")

    proc = _run_hook("claude_session_compact.py", {"cwd": str(tmp_path)}, tmp_path)

    assert proc.returncode == 0
    out = proc.stdout
    assert "Next action for t1:" not in out
    assert "checkpoint missing or stale for t1" not in out


def test_session_compact_resolves_project_root_from_payload_cwd(tmp_path: Path) -> None:
    subdir = tmp_path / "nested"
    subdir.mkdir()
    led = _make_led(tmp_path)
    _add_assigned_task(led, "t1")
    _write_checkpoint(tmp_path, led, "t1", "resolved root")

    proc = _run_hook("claude_session_compact.py", {"cwd": str(tmp_path)}, subdir)

    assert proc.returncode == 0
    out = proc.stdout
    assert "Next action for t1: resolved root" in out


def test_session_compact_reports_missing_checkpoints(tmp_path: Path) -> None:
    led = _make_led(tmp_path)
    _add_assigned_task(led, "t1")
    _add_assigned_task(led, "t2")
    _write_checkpoint(tmp_path, led, "t1", "do t1 next")

    proc = _run_hook("claude_session_compact.py", {"cwd": str(tmp_path)}, tmp_path)

    assert proc.returncode == 0
    out = proc.stdout
    assert "Next action for t1: do t1 next" in out
    assert "checkpoint missing or stale for t2" in out


def test_session_compact_reports_stale_checkpoints(tmp_path: Path) -> None:
    led = _make_led(tmp_path)
    _add_assigned_task(led, "t1")
    _write_checkpoint(tmp_path, led, "t1", "do t1 next")
    # Bump the task revision so the existing checkpoint becomes stale.
    led.update_task("t1", notes="bump")

    proc = _run_hook("claude_session_compact.py", {"cwd": str(tmp_path)}, tmp_path)

    assert proc.returncode == 0
    out = proc.stdout
    assert "Next action for t1:" not in out
    assert "checkpoint missing or stale for t1" in out


def test_session_compact_next_action_first_and_never_truncated(tmp_path: Path) -> None:
    led = _make_led(tmp_path)
    _add_assigned_task(led, "t1")
    long_next = "A" * 800
    _write_checkpoint(tmp_path, led, "t1", long_next, objective="O" * 5000)

    proc = _run_hook("claude_session_compact.py", {"cwd": str(tmp_path)}, tmp_path)

    assert proc.returncode == 0
    out = proc.stdout
    next_idx = out.index(f"Next action for t1: {long_next}")
    body_idx = out.index("## Rest of checkpoint for t1")
    assert next_idx < body_idx
    assert f"Next action for t1: {long_next}" in out
    assert len(out) < 7000


def test_session_compact_trims_other_sections_near_6000_chars(tmp_path: Path) -> None:
    led = _make_led(tmp_path)
    _add_assigned_task(led, "t1")
    _add_assigned_task(led, "t2")
    _write_checkpoint(tmp_path, led, "t1", "short next one", objective="O" * 4000)
    _write_checkpoint(tmp_path, led, "t2", "short next two", objective="O" * 4000)

    proc = _run_hook("claude_session_compact.py", {"cwd": str(tmp_path)}, tmp_path)

    assert proc.returncode == 0
    out = proc.stdout
    assert "Next action for t1: short next one" in out
    assert "Next action for t2: short next two" in out
    assert len(out) <= 6500


def test_session_compact_no_checkpoints_found(tmp_path: Path) -> None:
    led = _make_led(tmp_path)
    _add_assigned_task(led, "t1")

    proc = _run_hook("claude_session_compact.py", {"cwd": str(tmp_path)}, tmp_path)

    assert proc.returncode == 0
    out = proc.stdout
    assert "checkpoint missing or stale for t1" in out


def test_session_compact_never_fails(tmp_path: Path) -> None:
    proc = _run_hook("claude_session_compact.py", None, tmp_path)
    assert proc.returncode == 0
