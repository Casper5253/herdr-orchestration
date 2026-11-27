"""hx step harvest reads a worker transcript and appends steps to the checkpoint."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
import datetime as dt
from datetime import timedelta
from io import StringIO
from pathlib import Path
from threading import Barrier, BrokenBarrierError
from unittest.mock import patch
import json
import re
import sqlite3
import subprocess

import pytest

from hx import __main__ as cli
from hx import checkpoint, ledger as ledger_mod, reconcile

T = dt.datetime(2026, 11, 26, 9, 0, tzinfo=dt.timezone.utc)
MINUTE = dt.timedelta(minutes=1)
BRIEF = "TEAM BRIEF TASK: Q-999. Run the suite and report."
TEST_COMMAND = "python -m unittest discover -s tests"
EDIT_PATH = "hx/step.py"


def iso(when: dt.datetime) -> str:
    return when.isoformat()


def epoch_ms(when: dt.datetime) -> int:
    return int(when.timestamp() * 1000)


def roots(tmp_path) -> dict:
    return {
        "claude": tmp_path / "claude",
        "codex": tmp_path / "codex",
        "opencode": tmp_path / "opencode.db",
        "devin": tmp_path / "devin",
    }


def outbox(tmp_path, *specs):
    hx = tmp_path / ".hx"
    directory = hx / "outbox" / "dev__lead"
    directory.mkdir(parents=True, exist_ok=True)
    for record_id, target, text, when in specs:
        (directory / f"{record_id}.json").write_text(json.dumps({
            "id": record_id,
            "sender": "dev-lead",
            "target": target,
            "text": text,
            "created_at": iso(when),
            "state": "delivered",
        }), encoding="utf-8")
    return hx


def stub_checkpoint(tmp_path, task_id: str = "Q-999") -> object:
    directory = tmp_path / "checkpoints"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{task_id}.md"
    path.write_text(f"# Checkpoint: {task_id}\n\n## Objective\ndo the thing\n", encoding="utf-8")
    return path


def claude_session(tmp_path) -> None:
    path = roots(tmp_path)["claude"] / "C--Users-test" / "11111111-1111-4111-8111-111111111111.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        {"type": "user", "timestamp": iso(T - 10 * MINUTE),
         "message": {"role": "user", "content": "earlier"}},
        {"type": "user", "timestamp": iso(T + 30 * dt.timedelta(seconds=1)),
         "message": {"role": "user", "content": BRIEF}},
        {"type": "assistant", "timestamp": iso(T + 1 * MINUTE), "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": TEST_COMMAND}},
        ]}},
        {"type": "user", "timestamp": iso(T + 2 * MINUTE), "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_1", "content": "Exit code 1", "is_error": True},
        ]}},
        {"type": "assistant", "timestamp": iso(T + 3 * MINUTE), "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "toolu_2", "name": "Edit", "input": {"file_path": EDIT_PATH}},
        ]}},
    ]
    path.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")


def codex_session(tmp_path) -> None:
    path = roots(tmp_path)["codex"] / "2026" / "11" / "26" / "rollout-2026-11-26T09-00-00-22222222.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        {"type": "session_meta", "timestamp": iso(T - 20 * MINUTE), "payload": {"id": "22222222"}},
        {"type": "response_item", "timestamp": iso(T - 10 * MINUTE),
         "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "earlier"}]}},
        {"type": "response_item", "timestamp": iso(T + 30 * dt.timedelta(seconds=1)),
         "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": BRIEF}]}},
        {"type": "response_item", "timestamp": iso(T + 1 * MINUTE), "payload": {
            "type": "custom_tool_call", "name": "exec", "call_id": "c1",
            "input": 'text(await tools.exec_command({cmd:"' + TEST_COMMAND + '"}));',
        }},
        {"type": "response_item", "timestamp": iso(T + 2 * MINUTE), "payload": {
            "type": "custom_tool_call_output", "call_id": "c1", "output": [
                {"type": "input_text", "text": "Script completed\nWall time 1.0 seconds\nOutput:\n"},
                {"type": "input_text", "text": json.dumps({"exit_code": 0, "output": "ok"})},
            ],
        }},
        {"type": "response_item", "timestamp": iso(T + 3 * MINUTE), "payload": {
            "type": "custom_tool_call", "name": "exec", "call_id": "c2",
            "input": 'text(await tools.apply_patch("*** Begin Patch\\n*** Update File: ' + EDIT_PATH + '\\n+"));',
        }},
        {"type": "response_item", "timestamp": iso(T + 4 * MINUTE), "payload": {
            "type": "function_call", "name": "shell", "call_id": "c3",
            "arguments": json.dumps({"command": ["bash", "-lc", "git status --short"]}),
        }},
        {"type": "response_item", "timestamp": iso(T + 5 * MINUTE), "payload": {
            "type": "function_call_output", "call_id": "c3", "output": "Exit code: 2",
        }},
    ]
    path.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")


def opencode_session(tmp_path) -> None:
    db = roots(tmp_path)["opencode"]
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE session (id TEXT, time_created INTEGER, time_updated INTEGER);"
        "CREATE TABLE message (id TEXT, session_id TEXT, time_created INTEGER, data TEXT);"
        "CREATE TABLE part (message_id TEXT, session_id TEXT, time_created INTEGER, data TEXT);"
    )
    session_id = "ses_test"
    conn.execute("INSERT INTO session VALUES (?, ?, ?)",
                 (session_id, epoch_ms(T - 10 * MINUTE), epoch_ms(T + 10 * MINUTE)))
    rows = [("m1", "user", T - 10 * MINUTE, {"type": "text", "text": "earlier"}),
            ("m2", "user", T + 30 * dt.timedelta(seconds=1), {"type": "text", "text": BRIEF}),
            ("m3", "assistant", T + 1 * MINUTE, {
                "type": "tool", "tool": "bash",
                "state": {"status": "completed", "input": {"command": TEST_COMMAND},
                          "metadata": {"exit": 0}}}),
            ("m3", "assistant", T + 2 * MINUTE, {
                "type": "tool", "tool": "edit",
                "state": {"status": "completed", "input": {"filePath": EDIT_PATH}}}),
            ("m3", "assistant", T + 3 * MINUTE, {"type": "patch", "files": ["hx/other.py"]})]
    for message_id, role, when, data in rows:
        if message_id not in ("m1", "m2") or role == "user":
            conn.execute("INSERT OR IGNORE INTO message VALUES (?, ?, ?, ?)",
                         (message_id, session_id, epoch_ms(when), json.dumps({"role": role})))
        conn.execute("INSERT INTO part VALUES (?, ?, ?, ?)",
                     (message_id, session_id, epoch_ms(when), json.dumps(data)))
    conn.commit()
    conn.close()


def devin_session(tmp_path) -> None:
    path = roots(tmp_path)["devin"] / "transcripts" / "ready-otter.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"session_id": "ready-otter", "steps": [
        {"timestamp": iso(T - 10 * MINUTE), "source": "user", "message": "earlier"},
        {"timestamp": iso(T + 30 * dt.timedelta(seconds=1)), "source": "user", "message": BRIEF},
        {"timestamp": iso(T + 1 * MINUTE), "source": "agent", "tool_calls": [
            {"tool_call_id": "exec:0#a", "function_name": "exec",
             "arguments": {"command": TEST_COMMAND}}],
         "observation": {"results": [
             {"source_call_id": "exec:0#a", "content": "Output from command\nExit code: 0"}]}},
        {"timestamp": iso(T + 2 * MINUTE), "source": "agent", "tool_calls": [
            {"tool_call_id": "edit:1#b", "function_name": "edit",
             "arguments": {"file_path": EDIT_PATH}}]},
    ]}), encoding="utf-8")


BUILDERS = {
    "claude": claude_session,
    "codex": codex_session,
    "opencode": opencode_session,
    "devin": devin_session,
}


def harvest(tmp_path, agent: str, **kwargs):
    hx = outbox(tmp_path, ("brief-1", agent, BRIEF, T))
    return checkpoint.harvest(
        "Q-999", agent, hx_dir=hx, roots=roots(tmp_path),
        checkpoints_dir=tmp_path / "checkpoints", now=T + dt.timedelta(hours=1), **kwargs)


@pytest.mark.parametrize("family,builder", sorted(BUILDERS.items()))
def test_each_family_yields_commands_edits_and_tests(tmp_path, family, builder):
    builder(tmp_path)
    stub_checkpoint(tmp_path)
    result = harvest(tmp_path, "dev-" + family)
    lines = result["steps"]
    tests = [line for line in lines if " test " in line]
    edits = [line for line in lines if " edit " in line]
    assert tests and tests[0].endswith(TEST_COMMAND), lines
    assert any(TEST_COMMAND in line for line in tests)
    assert any(EDIT_PATH in line for line in edits), lines
    text = (tmp_path / "checkpoints" / "Q-999.md").read_text(encoding="utf-8")
    assert "## Steps" in text
    assert all(line in text for line in lines)


@pytest.mark.parametrize("command,kind", [
    ("python -m pytest tests -q", "test"),
    ("node tests/site_logic_test.cjs", "command"),
    ("Get-ChildItem -LiteralPath .hx/pytest-temp -Directory", "command"),
    ("hx say lead 'run python -m unittest'", "command"),
    ("cat >> report.md <<'EOF'\npytest tests\nEOF", "command"),
])
def test_test_classification_ignores_paths_prose_and_heredocs(command, kind):
    assert reconcile._command_step(T, command).kind == kind


def claude_commands(tmp_path, commands) -> None:
    path = roots(tmp_path)["claude"] / "C--Users-test" / "11111111-1111-4111-8111-111111111111.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        {"type": "user", "timestamp": iso(T + 30 * dt.timedelta(seconds=1)),
         "message": {"role": "user", "content": BRIEF}},
    ]
    for index, (offset, command) in enumerate(commands):
        lines.append({"type": "assistant", "timestamp": iso(T + offset),
                      "message": {"role": "assistant", "content": [
                          {"type": "tool_use", "id": f"toolu_{index}", "name": "Bash",
                           "input": {"command": command}}]}})
        lines.append({"type": "user", "timestamp": iso(T + offset + dt.timedelta(seconds=1)),
                      "message": {"role": "user", "content": [
                          {"type": "tool_result", "tool_use_id": f"toolu_{index}",
                           "content": "Exit code 0"}]}})
    path.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")


REPORT = "hx say lead '[team report] STATUS: done. WHY: Q-999 passed'"


def test_a_report_after_a_test_closes_the_window(tmp_path):
    claude_commands(tmp_path, [
        (1 * MINUTE, TEST_COMMAND),
        (3 * MINUTE, REPORT),
        (4 * MINUTE, "git status --short"),
    ])
    stub_checkpoint(tmp_path)
    result = harvest(tmp_path, "dev-claude")
    assert REPORT in result["steps"][-1]
    assert not any("git status --short" in line for line in result["steps"])


def test_a_report_before_any_test_closes_the_window(tmp_path):
    claude_commands(tmp_path, [
        (1 * MINUTE, REPORT),
        (2 * MINUTE, TEST_COMMAND),
    ])
    stub_checkpoint(tmp_path)
    result = harvest(tmp_path, "dev-claude")
    assert not any(TEST_COMMAND in line for line in result["steps"])


def test_harvest_twice_appends_nothing_new(tmp_path):
    codex_session(tmp_path)
    path = stub_checkpoint(tmp_path)
    first = harvest(tmp_path, "dev-codex")
    before = path.read_bytes()
    second = harvest(tmp_path, "dev-codex")
    assert first["added"] == len(first["steps"]) > 0
    assert second["added"] == 0
    assert path.read_bytes() == before


def test_exit_codes_come_from_the_transcript(tmp_path):
    codex_session(tmp_path)
    stub_checkpoint(tmp_path)
    lines = harvest(tmp_path, "dev-codex")["steps"]
    assert any(" exit=0 " in line for line in lines)
    assert any("git status --short" in line and " exit=2 " in line for line in lines), lines


def test_a_brief_with_no_readable_transcript_is_a_clear_error(tmp_path):
    outbox(tmp_path, ("brief-1", "dev-opencode", BRIEF, T))
    with pytest.raises(ValueError) as caught:
        checkpoint.harvest(
            "Q-999", "dev-opencode", hx_dir=tmp_path / ".hx", roots=roots(tmp_path),
            checkpoints_dir=tmp_path / "checkpoints", now=T + dt.timedelta(hours=1))
    message = str(caught.value)
    assert "no readable opencode transcript" in message
    assert "hx step add Q-999 --note" in message


def test_cli_reports_a_missing_transcript_without_a_traceback(tmp_path, monkeypatch, capsys):
    outbox(tmp_path, ("brief-1", "dev-claude", BRIEF, T))
    monkeypatch.setenv("HX_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("HX_CHECKPOINTS_DIR", str(tmp_path / "checkpoints"))
    monkeypatch.setenv("HX_CLAUDE_PROJECTS_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("HX_DEVIN_CLI_DIR", str(tmp_path / "devin"))
    from hx import usage
    monkeypatch.setattr(usage, "default_codex_sessions_dir", lambda: tmp_path / "codex")
    monkeypatch.setattr(usage, "default_opencode_db_path", lambda: tmp_path / "opencode.db")
    assert cli.main(["step", "harvest", "Q-999", "--agent", "dev-claude"]) == 1
    captured = capsys.readouterr()
    assert "no readable claude transcript" in captured.err


def test_harvest_uses_the_named_brief(tmp_path):
    claude_session(tmp_path)
    stub_checkpoint(tmp_path)
    hx = outbox(tmp_path,
                ("older", "dev-claude", "TASK: Q-999 round 1", T - dt.timedelta(hours=2)),
                ("brief-1", "dev-claude", BRIEF, T))
    result = checkpoint.harvest(
        "Q-999", "dev-claude", brief="brief-1", hx_dir=hx, roots=roots(tmp_path),
        checkpoints_dir=tmp_path / "checkpoints", now=T + dt.timedelta(hours=1))
    assert result["brief"] == "brief-1"
    assert result["total"] >= 2


def test_since_reads_a_window_without_a_brief(tmp_path):
    devin_session(tmp_path)
    stub_checkpoint(tmp_path)
    outbox(tmp_path, ("unrelated", "dev-devin", "something else", T - dt.timedelta(hours=3)))
    result = checkpoint.harvest(
        "Q-999", "dev-devin", since=iso(T + dt.timedelta(seconds=1)),
        hx_dir=tmp_path / ".hx", roots=roots(tmp_path),
        checkpoints_dir=tmp_path / "checkpoints", now=T + dt.timedelta(hours=1))
    assert result["brief"] is None
    assert any(TEST_COMMAND in line for line in result["steps"])


def test_step_add_records_a_note_by_hand(tmp_path, monkeypatch, capsys):
    path = stub_checkpoint(tmp_path)
    monkeypatch.setenv("HX_CHECKPOINTS_DIR", str(tmp_path / "checkpoints"))
    assert cli.main(["step", "add", "Q-999", "--note", "Devin cannot run hx here"]) == 0
    text = path.read_text(encoding="utf-8")
    assert "note Devin cannot run hx here" in text
    assert "## Steps" in text
    out = capsys.readouterr().out
    assert "recorded" in out


def _git_init(repo) -> None:
    subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(repo),
                   check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(repo),
                   check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    _git_init(tmp_path)
    (tmp_path / "owned.py").write_text("original\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=str(tmp_path), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=str(tmp_path),
                   check=True, capture_output=True)
    monkeypatch.setenv("HX_LEDGER_PATH", str(tmp_path / ".hx" / "ledger.json"))
    monkeypatch.setenv("HX_LEASES_PATH", str(tmp_path / ".herdr-team" / "state" / "leases.json"))
    monkeypatch.setenv("HX_LOCK_PATH", str(tmp_path / ".hx" / "ledger.lock"))
    monkeypatch.setenv("HX_LEASES_LOCK_PATH", str(tmp_path / ".herdr-team" / "state" / "leases.lock"))
    monkeypatch.setenv("HX_CHECKPOINTS_DIR", str(tmp_path / ".hx" / "checkpoints"))
    monkeypatch.setenv("HX_PROJECT_ROOT", str(tmp_path))
    return tmp_path


def test_harvest_creates_the_checkpoint_from_the_ledger(repo):
    ledger = ledger_mod.Ledger(repo_root=repo)
    ledger.add_task("Q-999", title="Task", owner="dev-opencode", files=["owned.py"])
    codex_session(repo)
    hx = outbox(repo, ("brief-1", "dev-codex", BRIEF, T))
    result = checkpoint.harvest(
        "Q-999", "dev-codex", hx_dir=hx, roots=roots(repo),
        checkpoints_dir=repo / ".hx" / "checkpoints", ledger=ledger,
        now=T + dt.timedelta(hours=1))
    text = (repo / ".hx" / "checkpoints" / "Q-999.md").read_text(encoding="utf-8")
    assert "## Objective" in text
    assert "## Steps" in text
    assert TEST_COMMAND in text
    assert result["added"] > 0


def test_a_checkpoint_rewrite_keeps_the_steps(repo):
    ledger = ledger_mod.Ledger(repo_root=repo)
    ledger.add_task("Q-999", title="Task", owner="dev-opencode", files=["owned.py"])
    codex_session(repo)
    hx = outbox(repo, ("brief-1", "dev-codex", BRIEF, T))
    checkpoint.harvest(
        "Q-999", "dev-codex", hx_dir=hx, roots=roots(repo),
        checkpoints_dir=repo / ".hx" / "checkpoints", ledger=ledger,
        now=T + dt.timedelta(hours=1))
    path = checkpoint.write_checkpoint(
        "Q-999", ledger=ledger, objective="keep going", decisions="none",
        errors="none", next_action="run the suite")
    text = path.read_text(encoding="utf-8")
    assert "## Steps" in text and TEST_COMMAND in text


def test_untracked_steps_do_not_break_validation(repo):
    ledger = ledger_mod.Ledger(repo_root=repo)
    ledger.add_task("Q-999", title="Task", owner="dev-opencode", files=["owned.py"])
    checkpoint.write_checkpoint("Q-999", ledger=ledger, objective="x", decisions="d",
                                errors="none", next_action="next")
    steps = [checkpoint.format_step(T, "dev-devin", "note", "recorded by hand")]
    checkpoint.append_steps("Q-999", steps, checkpoints_dir=repo / ".hx" / "checkpoints")
    result = checkpoint.validate_checkpoint("Q-999", ledger=ledger)
    assert result["ok"] is True, result["errors"]


def test_unknown_agent_is_refused(tmp_path):
    with pytest.raises(ValueError, match="unknown agent family"):
        checkpoint.harvest("Q-999", "dev-nobody", hx_dir=tmp_path, roots=roots(tmp_path))


TASK = "Q-999"
CANARY = "SYNTHETIC_CANARY"


def at(second):
    return (T + timedelta(seconds=second)).isoformat()


def claude_user(second, text):
    return {"type": "user", "timestamp": at(second), "message": {"content": text}}


def claude_command(second, text, call_id="tool"):
    return {"type": "assistant", "timestamp": at(second), "message": {"content": [
        {"type": "tool_use", "name": "Bash", "id": call_id, "input": {"command": text}},
    ]}}


def codex_event(second, payload):
    return {"type": "response_item", "timestamp": at(second), "payload": payload}


def codex_user(second, text):
    return codex_event(second, {"role": "user", "content": [{"text": text}]})


def codex_call(second, name, call_id, **fields):
    return codex_event(second, {"type": "custom_tool_call", "name": name,
                                "call_id": call_id, **fields})


def codex_result(second, call_id, output):
    return codex_event(second, {"type": "custom_tool_call_output", "call_id": call_id,
                                "output": output})


def jsonl(path, events):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
    return path


class TestReview:
    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.root = tmp_path
        self.path = stub_checkpoint(self.root)
        self.roots = roots(self.root)

    def send(self, family="claude", *others):
        self.outbox = outbox(self.root, ("brief", "dev-" + family, BRIEF, T), *others)

    def harvest(self, family="claude", **kwargs):
        return checkpoint.harvest(TASK, "dev-" + family, hx_dir=self.outbox, roots=self.roots,
                                  checkpoints_dir=self.path.parent,
                                  now=T + timedelta(hours=1), **kwargs)

    def claude(self, *events, name="session"):
        return jsonl(self.roots["claude"] / "synthetic" / (name + ".jsonl"), events)

    def codex(self, *events):
        return jsonl(self.roots["codex"] / "rollout-synthetic.jsonl", events)

    def assert_contains(self, result, value, explanation):
        assert any(value in line for line in result["steps"]), explanation

    def assert_excludes(self, result, value, explanation):
        assert not any(value in line for line in result["steps"]), explanation

    def test_starts_at_receipt(self):
        self.send()
        self.claude(claude_command(5, "git status --short"), claude_user(20, BRIEF),
                    claude_command(25, "git diff --stat"))
        result = self.harvest()
        self.assert_contains(result, "git diff", "work after brief receipt is missing")
        self.assert_excludes(result, "git status", "work before brief receipt was harvested")

    def test_same_timestamp_uses_transcript_order_at_both_boundaries(self):
        self.send()
        self.claude(claude_command(1, "git status --short", "before"),
                    claude_user(1, BRIEF),
                    claude_command(1, "python -m pytest -q", "inside"),
                    claude_user(1, "[team from dev-lead] New brief: read Q-1000.md"),
                    claude_command(1, "git diff --stat", "after"))
        result = self.harvest()
        assert len(result["steps"]) == 1
        assert "pytest" in result["steps"][0]

    def test_report_before_test_closes_window(self):
        self.send()
        self.claude(claude_user(1, BRIEF),
                    claude_command(2, "hx say lead '[team report from dev-claude] STATUS: blocked. Q-999'"),
                    claude_user(3, "Work on a different project."),
                    claude_command(4, "git diff --stat"))
        self.assert_excludes(self.harvest(), "git diff", "a blocked report left the window open")

    @pytest.mark.parametrize("report", ["STATUS: done. WHY: Verified.", "Verified."])
    def test_report_without_id_closes_window(self, report):
        self.send()
        self.claude(claude_user(1, BRIEF), claude_command(2, "python -m pytest -q"),
                    claude_command(3, f"hx say lead '[team report from dev-claude] {report}'"),
                    claude_user(4, "Work on a different project."),
                    claude_command(5, "git diff --stat"))
        self.assert_excludes(self.harvest(), "git diff", "the standard report left the window open")

    def test_next_nontask_message_does_not_cut_window(self):
        self.send("claude", ("update", "dev-claude", "Please keep going with Q-999.", T + timedelta(seconds=3)))
        self.claude(claude_user(1, BRIEF), claude_user(4, "Please keep going with Q-999."),
                    claude_command(5, "python -m pytest -q"))
        self.assert_contains(self.harvest(), "pytest", "a follow-up cut off current-task work")

    def test_next_brief_stops_at_session_receipt(self):
        self.send()
        self.claude(claude_user(1, BRIEF), claude_command(2, "git status --short"),
                    claude_user(3, "[team from dev-lead] New brief: read .hx/briefs/Q-1000.md"),
                    claude_command(4, "git diff --stat"))
        self.assert_excludes(self.harvest(), "git diff", "next brief in the transcript was ignored")

    def test_similar_task_id_cannot_replace_brief(self):
        other = BRIEF.replace(TASK, "Q-9990")
        self.send("claude", ("other", "dev-claude", other, T + timedelta(seconds=3)))
        self.claude(claude_user(1, BRIEF), claude_command(2, "git status --short"),
                    claude_user(4, other), claude_command(5, "git diff --stat"))
        assert self.harvest()["brief"] == "brief", "selected the brief for a different task ID"

    def test_since_refuses_ambiguous_worker_sessions(self):
        self.send()
        self.claude(claude_user(1, BRIEF), claude_command(2, "git status --short"), name="a")
        self.claude(claude_user(1, "Other worker in another project."),
                    claude_command(2, "git diff --stat"), name="b")
        with pytest.raises(ValueError, match="ambiguous"):
            self.harvest(since=at(0))

    def test_multiline_payload_is_not_persisted(self):
        self.send()
        self.claude(claude_user(1, BRIEF),
                    claude_command(2, "cat <<'EOF' > note.txt\n" + CANARY + "\nEOF"))
        self.harvest()
        assert not CANARY in self.path.read_text(encoding="utf-8"), "the checkpoint persisted a synthetic heredoc body"

    def test_secret_looking_arguments_are_not_persisted(self):
        self.send()
        self.claude(claude_user(1, BRIEF),
                    claude_command(2, "curl --header 'Authorization: Bearer " + CANARY + "' https://example.test"))
        result = self.harvest()
        self.assert_excludes(result, CANARY, "a bearer credential appeared in harvest output")
        assert CANARY not in self.path.read_text(encoding="utf-8")

    def test_codex_only_extracts_shell_from_actual_shell_tool(self):
        self.send("codex")
        body = ("*** Begin Patch\n*** Update File: a.py\n"
                "+const opts = {cmd: 'git diff --stat'};\n*** End Patch")
        source = "text(await tools.apply_patch(" + json.dumps(body) + "));"
        self.codex(codex_user(1, BRIEF), codex_call(2, "exec", "edit", input=source))
        self.assert_excludes(self.harvest("codex"), "git diff", "patch source fabricated a shell step")

    def test_codex_sanitizes_escaped_multiline_and_ignores_regex_literals(self):
        self.send("codex")
        source = (
            r'const pattern = /tools.exec_command({cmd:"git status --short"})/; '
            r"text(await tools.exec_command({cmd:'cat <<EOF\n" + CANARY + r"\nEOF'}));"
        )
        self.codex(codex_user(1, BRIEF), codex_call(2, "exec", "run", input=source))
        result = self.harvest("codex")
        assert len(result["steps"]) == 1
        assert "cat <<EOF" in result["steps"][0]
        assert CANARY not in self.path.read_text(encoding="utf-8")

    def test_inline_file_payload_is_not_persisted(self):
        self.send()
        self.claude(claude_user(1, BRIEF),
                    claude_command(2, "Set-Content -Path draft.txt -Value " + CANARY))
        assert CANARY not in " ".join(self.harvest()["steps"])
        assert CANARY not in self.path.read_text(encoding="utf-8")

    def test_codex_does_not_index_exits_by_print_order(self):
        self.send("codex")
        source = ('const a = await tools.exec_command({cmd:"git status --short"}); '
                  'const b = await tools.exec_command({cmd:"python -m pytest -q"}); text(b); text(a);')
        output = [{"text": json.dumps({"exit_code": code})} for code in [7, 0]]
        self.codex(codex_user(1, BRIEF), codex_call(2, "exec", "batch", input=source),
                   codex_result(3, "batch", output))
        result = self.harvest("codex")
        self.assert_excludes(result, "exit=0 python -m pytest", "the failed test was marked successful")

    def test_claude_success_without_exit_banner(self):
        self.send()
        self.claude(claude_user(1, BRIEF), claude_command(2, "python -m pytest -q"),
                    {"type": "user", "timestamp": at(3), "message": {"content": [
                        {"type": "tool_result", "tool_use_id": "tool",
                         "content": "Suite completed.", "is_error": False},
                    ]}})
        self.assert_contains(self.harvest(), "exit=0", "the successful tool result had no exit code")

    def test_devin_exit_banner_uses_final_status(self):
        self.send("devin")
        devin_session(self.root)
        path = self.roots["devin"] / "transcripts" / "ready-otter.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        observation = data["steps"][2]["observation"]["results"][0]
        observation["content"] = "Output from command\nExit code: 31\nExit code: 0"
        path.write_text(json.dumps(data), encoding="utf-8")
        self.assert_excludes(self.harvest("devin"), "exit=31", "stdout line won over the exit trailer")

    def test_devin_structured_exit(self):
        self.send("devin")
        folder = self.roots["devin"] / "transcripts"
        folder.mkdir(parents=True)
        (folder / "synthetic.json").write_text(json.dumps({"steps": [
            {"timestamp": at(1), "source": "user", "message": BRIEF},
            {"timestamp": at(2), "tool_calls": [{"tool_call_id": "tool", "function_name": "exec",
                                                "arguments": {"command": "python -m pytest -q"}}]},
            {"timestamp": at(3), "observation": {"results": [
                {"source_call_id": "tool", "content": "Suite completed.", "exit_code": 7}]}},
        ]}), encoding="utf-8")
        result = self.harvest("devin")
        self.assert_contains(result, "test python -m pytest -q", "undocumented Devin exit must stay unknown")

    def test_opencode_running_step_converges_when_result_arrives(self):
        self.send("opencode")
        opencode_session(self.root)
        import sqlite3
        from contextlib import closing

        db = self.roots["opencode"]
        with closing(sqlite3.connect(db)) as conn:
            rowid, raw = conn.execute("SELECT rowid, data FROM part ORDER BY rowid LIMIT 1 OFFSET 2").fetchone()
            completed = json.loads(raw)
            running = {**completed, "state": {"status": "running", "input": completed["state"]["input"]}}
            conn.execute("UPDATE part SET data=? WHERE rowid=?", (json.dumps(running), rowid))
            conn.commit()
            self.harvest("opencode")
            conn.execute("UPDATE part SET data=? WHERE rowid=?", (raw, rowid))
            conn.commit()
            self.harvest("opencode")
        text = self.path.read_text(encoding="utf-8")
        assert text.count(TEST_COMMAND) == 1, "one tool call was persisted twice"
        assert "test exit=0" in text

    def test_repeat_with_fixed_transcript(self):
        self.send()
        self.claude(claude_user(1, BRIEF), claude_command(2, "python -m pytest -q"))
        assert self.harvest()["added"] == 1
        original = self.path.read_bytes()
        assert self.harvest()["added"] == 0
        assert original == self.path.read_bytes(), "repeat changed checkpoint bytes"

    def test_distinct_commands_in_same_second_do_not_collapse(self):
        self.send()
        first = claude_command(2, "git status --short", "a")
        second = claude_command(2, "git status --short", "b")
        second["timestamp"] = at(2).replace("+00:00", ".500000+00:00")
        self.claude(claude_user(1, BRIEF), first, second)
        assert self.harvest()["added"] == 2, "two calls within the same second collapsed"

    def test_truncated_tail_recovers_prefix_then_updates_in_place(self):
        self.send("codex")
        transcript = self.codex(codex_user(1, BRIEF),
                                codex_call(2, "exec", "run",
                                           input='text(await tools.exec_command({cmd:"python -m pytest -q"}));'))
        line = json.dumps(codex_result(3, "run", [{"text": json.dumps({"exit_code": 7})}]))
        split = len(line) // 2
        with transcript.open("a", encoding="utf-8") as handle:
            handle.write(line[:split])
        assert self.harvest("codex")["added"] == 1
        with transcript.open("a", encoding="utf-8") as handle:
            handle.write(line[split:] + "\n")
        self.harvest("codex")
        assert self.path.read_text(encoding="utf-8").count("python -m pytest") == 1, "recovery duplicated the call with a different exit field"

    def test_missing_transcript_has_no_checkpoint_side_effect(self):
        self.send()
        original = self.path.read_bytes()
        with pytest.raises(ValueError, match="no readable"):
            self.harvest()
        assert original == self.path.read_bytes(), "missing transcript changed checkpoint"

    def test_steps_merge_preserves_other_sections_byte_for_byte(self):
        prefix = b"# Checkpoint\r\n\r\n## Objective\r\nKeep the objective\r\nkeep this\r\n\r\n"
        suffix = b"\r\n## Next action\r\nKeep this too.\r\n"
        self.path.write_bytes(prefix + b"## Steps\r\n\r\n- earlier\r\n" + suffix)
        checkpoint.append_steps(TASK, ["- later"], checkpoints_dir=self.path.parent)
        text = self.path.read_bytes()
        assert text.startswith(prefix) and text.endswith(suffix), "steps merge changed bytes outside the Steps section"
        assert b"- earlier" in text and b"- later" in text

    def test_inline_steps_reference_is_not_a_heading(self):
        self.path.write_bytes(b"# Checkpoint\n\n## Objective\nReference to ## Steps\nkeep this\n"
                              b"\n## Steps\n\n- earlier\n\n## Next action\nKeep this too.\n")
        checkpoint.append_steps(TASK, ["- later"], checkpoints_dir=self.path.parent)
        assert b"Reference to ## Steps\nkeep this\n" in self.path.read_bytes(), "inline Steps text was mistaken for a heading"

    def test_steps_heading_in_fenced_example_is_ignored(self):
        original = b"# Checkpoint\r\n\r\n## Objective\r\n~~~md\r\n## Steps\r\n- sample\r\n~~~\r\n"
        self.path.write_bytes(original)
        checkpoint.append_steps(TASK, ["- live"], checkpoints_dir=self.path.parent)
        assert self.path.read_bytes() == original + b"\r\n## Steps\r\n\r\n- live\r\n"

    def test_matching_brief_in_multiple_sessions_is_ambiguous(self):
        self.send()
        self.claude(claude_user(1, BRIEF), claude_command(2, "git status --short"), name="a")
        self.claude(claude_user(2, BRIEF), claude_command(3, "git diff --stat"), name="b")
        with pytest.raises(ValueError, match="ambiguous"):
            self.harvest()

    def test_parallel_harvest_preserves_both_updates(self):
        self.send()
        self.claude(claude_user(1, BRIEF), claude_command(2, "git status --short"))
        codex_session(self.root)
        outbox(self.root, ("codex-brief", "dev-codex", BRIEF, T))
        snapshot = Barrier(2)
        original_text, original_bytes = Path.read_text, Path.read_bytes

        def hold_snapshot(original):
            def read(path, *args, **kwargs):
                text = original(path, *args, **kwargs)
                if path == self.path:
                    try:
                        snapshot.wait(timeout=0.5)
                    except BrokenBarrierError:
                        pass
                return text
            return read

        with (patch.object(Path, "read_text", hold_snapshot(original_text)),
              patch.object(Path, "read_bytes", hold_snapshot(original_bytes)),
              ThreadPoolExecutor(max_workers=2) as pool):
            jobs = [pool.submit(self.harvest, family) for family in ("claude", "codex")]
            errors = [job.exception() for job in jobs]
        after = self.path.read_text(encoding="utf-8")
        assert not any(errors), "concurrent harvesters collided while writing"
        assert "dev-claude" in after and "dev-codex" in after, "concurrent harvest discarded one worker's update"

    def test_single_writer_replaces_complete_checkpoint(self):
        self.send()
        self.claude(claude_user(1, BRIEF), claude_command(2, "git status --short"))
        self.path.write_bytes(self.path.read_text(encoding="utf-8").encode("utf-8"))
        before = self.path.read_bytes()
        replace = checkpoint.os.replace
        swaps = []

        def inspect(source, target):
            assert self.path.read_bytes() == before, "visible checkpoint changed before swap"
            assert Path(source).read_bytes().startswith(before), "replacement lost original content"
            assert b"git status --short" in Path(source).read_bytes(), "replacement was incomplete"
            swaps.append(target)
            replace(source, target)

        with patch.object(checkpoint.os, "replace", side_effect=inspect):
            assert self.harvest()["added"] == 1
        assert swaps == [self.path]
        assert b"git status --short" in self.path.read_bytes()

    def test_codex_output_body_is_not_persisted(self):
        self.send("codex")
        self.codex(codex_user(1, BRIEF),
                   codex_call(2, "exec", "run",
                              input='text(await tools.exec_command({cmd:"python -m pytest -q"}));'),
                   codex_result(3, "run", [{"text": json.dumps({"exit_code": 0, "output": CANARY})}]))
        result = self.harvest("codex")
        self.assert_contains(result, "exit=0", "structured exit did not survive")
        assert CANARY not in self.path.read_text(encoding="utf-8"), "tool stdout entered the checkpoint"

    def test_cli_manual_add(self):
        out = StringIO()
        err = StringIO()
        with patch.object(checkpoint, "default_checkpoints_dir", return_value=self.path.parent):
            with redirect_stdout(out), redirect_stderr(err):
                rc = cli.main(["step", "add", TASK, "--note", "Synthetic note"])
        assert rc == 0
        assert "manual note Synthetic note" in self.path.read_text(encoding="utf-8")

