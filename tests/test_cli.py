"""End-to-end CLI tests: python -m hx wiring and project-root resolution."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from hx import __main__ as cli

REPO_ROOT = Path(__file__).resolve().parent.parent

OCRUN_SUCCESS_STREAM = "\n".join(
    [
        json.dumps({"type": "step_start", "timestamp": 1, "sessionID": "ses_cli", "part": {"id": "prt_1", "sessionID": "ses_cli", "messageID": "msg_1"}}),
        json.dumps({"type": "text", "timestamp": 2, "sessionID": "ses_cli", "part": {"id": "prt_2", "sessionID": "ses_cli", "messageID": "msg_1", "text": "done"}}),
        json.dumps({"type": "step_finish", "timestamp": 3, "sessionID": "ses_cli", "part": {"id": "prt_3", "sessionID": "ses_cli", "messageID": "msg_1", "reason": "stop", "tokens": {"total": 500, "input": 100, "output": 400, "reasoning": 0, "cache": {"write": 0, "read": 0}}, "cost": 0.001}}),
    ]
)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(repo), check=True, capture_output=True)


@pytest.fixture
def cli_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    repo = tmp_path / "proj"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "cli@example.com")
    _git(repo, "config", "user.name", "cli")
    (repo / "owned.py").write_text("original\n", encoding="utf-8")
    (repo / "other.py").write_text("original\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "seed")

    monkeypatch.setenv("HX_PROJECT_ROOT", str(repo))
    monkeypatch.setenv("HX_LEDGER_PATH", str(repo / ".hx" / "ledger.json"))
    monkeypatch.setenv("HX_LEASES_PATH", str(tmp_path / "leases.json"))
    monkeypatch.setenv("HX_LOCK_PATH", str(repo / ".hx" / "ledger.lock"))
    monkeypatch.setenv("HX_STATE_DIR", str(tmp_path / "state"))
    return repo


def test_task_add_assign_checkpoint_done_validate_flow(
    cli_repo: Path, capsys: pytest.CaptureFixture
) -> None:
    assert cli.main(["task", "add", "t1", "--title", "Task 1", "--owner", "w", "--files", "owned.py"]) == 0
    capsys.readouterr()
    assert cli.main(["task", "assign", "t1", "--owner", "w"]) == 0
    capsys.readouterr()
    assert cli.main(["checkpoint", "write", "t1", "--objective", "o", "--decisions", "d", "--errors", "e", "--tests", "t", "--next", "n"]) == 0
    capsys.readouterr()

    (cli_repo / "owned.py").write_text("modified\n", encoding="utf-8")

    assert cli.main(["task", "done", "t1", "--objective", "o", "--decisions", "d", "--errors", "e", "--tests", "t", "--next", "next step"]) == 0
    done_out = capsys.readouterr().out
    task = json.loads(done_out)
    assert task["status"] == "done"

    capsys.readouterr()
    assert cli.main(["checkpoint", "validate", "t1"]) == 0
    validate_out = capsys.readouterr().out
    result = json.loads(validate_out)
    assert result["ok"] is True
    assert result["errors"] == []

    checkpoint_text = (cli_repo / ".hx" / "checkpoints" / "t1.md").read_text(encoding="utf-8")
    assert f"ledger_rev: {task['rev']}" in checkpoint_text

    capsys.readouterr()
    assert cli.main(["task", "show", "t1"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["status"] == "done"
    assert shown["checkpoint"] == str(cli_repo / ".hx" / "checkpoints" / "t1.md")


def test_checkpoint_includes_committed_changes_for_owned_files(
    cli_repo: Path, capsys: pytest.CaptureFixture
) -> None:
    assert cli.main(["task", "add", "t1", "--title", "Task 1", "--owner", "w", "--files", "owned.py"]) == 0
    capsys.readouterr()

    (cli_repo / "owned.py").write_text("committed change\n", encoding="utf-8")
    (cli_repo / "other.py").write_text("uncommitted dirt\n", encoding="utf-8")
    _git(cli_repo, "add", "-A")
    _git(cli_repo, "commit", "-m", "ownMarker task work")

    assert cli.main(["checkpoint", "write", "t1", "--objective", "o", "--decisions", "d", "--errors", "e", "--tests", "t", "--next", "n"]) == 0
    capsys.readouterr()
    text = (cli_repo / ".hx" / "checkpoints" / "t1.md").read_text(encoding="utf-8")

    assert "### committed (base..HEAD)" in text
    assert "ownMarker task work" in text
    committed_block = text.split("### committed (base..HEAD)", 1)[1].split("### git diff --stat", 1)[0]
    assert "other.py" not in committed_block

    capsys.readouterr()
    assert cli.main(["checkpoint", "validate", "t1"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is True
    assert result["errors"] == []


def test_task_assign_persistent_blocked_by_compacting_session(
    cli_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    leases_path = tmp_path / "leases.json"
    leases_path.write_text(
        json.dumps(
            {
                "files": {},
                "sessions": {
                    "hx-opencode": {
                        "agent": "hx-opencode",
                        "session_id": "ses_1",
                        "holder": "hx-claude",
                        "state": "compacting",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    assert cli.main(["task", "add", "t1", "--title", "T", "--owner", "w", "--files", "owned.py"]) == 0
    capsys.readouterr()

    rc = cli.main(["task", "assign", "t1", "--owner", "w", "--method", "persistent", "--agent", "hx-opencode"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "compacting" in err


def test_task_assign_persistent_allowed_for_free_agent(
    cli_repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    assert cli.main(["task", "add", "t1", "--title", "T", "--owner", "w", "--files", "owned.py"]) == 0
    capsys.readouterr()

    rc = cli.main(["task", "assign", "t1", "--owner", "w", "--method", "persistent", "--agent", "hx-opencode"])
    assert rc == 0


def test_route_subcommand_wired(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setenv("HX_PROJECT_ROOT", str(tmp_path / "proj"))
    monkeypatch.setenv("HX_OPENCODE_DB", str(tmp_path / "missing.db"))
    monkeypatch.setenv("HX_CODEX_SESSIONS_DIR", str(tmp_path))
    monkeypatch.setenv("HX_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr("hx.usage._herdr_agent_list", lambda: {"result": {"agents": []}})

    rc = cli.main(["route", "--kind", "trivial"])
    assert rc == 0
    result = json.loads(capsys.readouterr().out)
    assert result["worker"] == "claude"

    routing_log = tmp_path / "proj" / ".hx" / "routing.jsonl"
    assert routing_log.exists()
    entry = json.loads(routing_log.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert "decision_id" in entry


def test_ocrun_subcommand_wired_with_prompt_file(
    cli_repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    prompt_file = cli_repo.parent / "brief.md"
    prompt_file.write_text("fix the stats module\n", encoding="utf-8")

    def fake_execute(cmd, cwd, timeout):
        return 0, OCRUN_SUCCESS_STREAM, "", False

    monkeypatch.setattr("hx.ocrun.execute", fake_execute)

    rc = cli.main(["ocrun", "--model", "qwen3.8-flash", "--cwd", str(cli_repo), "--prompt-file", str(prompt_file)])
    assert rc == 0
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is True
    assert result["session_id"] == "ses_cli"

    routing_log = cli_repo / ".hx" / "routing.jsonl"
    last = json.loads(routing_log.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert last["kind"] == "outcome"
    assert last["model"] == "qwen3.8-flash"
    assert last["ok"] is True


def test_project_root_resolved_from_cwd(tmp_path: Path) -> None:
    detached = tmp_path / "proj"
    detached.mkdir()
    _git(detached, "init")

    env = os.environ.copy()
    for key in (
        "HX_PROJECT_ROOT",
        "HX_LEDGER_PATH",
        "HX_LOCK_PATH",
        "HX_CHECKPOINTS_DIR",
        "HX_ROUTING_LOG",
    ):
        env.pop(key, None)
    env["PYTHONPATH"] = str(REPO_ROOT)
    env["HX_LEASES_PATH"] = str(tmp_path / "leases.json")

    proc = subprocess.run(
        [sys.executable, "-m", "hx", "task", "add", "t1", "--title", "T", "--owner", "w", "--files", "owned.py"],
        cwd=str(detached),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    task = json.loads(proc.stdout)
    assert task["id"] == "t1"

    assert (detached / ".hx" / "ledger.json").exists()
