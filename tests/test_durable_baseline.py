import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIX = Path(__file__).resolve().parent / "fixtures" / "durable-steps"
SPEC = importlib.util.spec_from_file_location("durable_baseline", ROOT / "research" / "durable-steps" / "baseline.py")
mod = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = mod
SPEC.loader.exec_module(mod)
SINCE = mod.parse_day("2026-11-14")
UNTIL = mod.parse_day("2026-11-16")


def test_sends_by_receipt():
    rows = mod.measure_sends([FIX / "outbox"], SINCE, UNTIL)
    by = {row.detail["destination"]: row for row in rows}
    assert by["claude lead"].denominator == 3
    assert by["claude lead"].numerator == 2
    assert by["codex"].detail["receipts"]["submitted"] == 1
    assert by["codex"].detail["receipts"]["uncertain"] == 1
    assert by["opencode"].detail["receipts"]["submitted"] == 1
    assert by["claude lead"].detail["receipts"]["submitted"] == 1


def test_reports_lost_and_doubled():
    result = mod.measure_reports([FIX / "outbox"], [FIX / "briefs"], [FIX / "reports"], SINCE, UNTIL)
    assert result.detail["distinct_briefs"] == 3
    assert [item["brief"] for item in result.detail["lost"]] == ["q2-lost"]
    assert len(result.detail["doubled"]) == 1


def test_stalls():
    result = mod.measure_stalls(
        [FIX / "routing.jsonl"],
        [FIX / "context-boundaries.jsonl"],
        [FIX / "afk"],
        [FIX / "outbox"],
        SINCE,
        UNTIL,
    )
    assert result.detail["failed"] == 1
    assert result.detail["failure_kinds"] == {"tool": 1}
    assert result.detail["dispositions"] == {"refused": 1, "recovery-needed": 1}
    assert len(result.detail["afk_stall_rows"]) == 1


def test_compactions():
    result = mod.measure_compactions(
        [FIX / "sessions" / "lead.jsonl"],
        [FIX / "context-boundaries.jsonl"],
        [FIX / "afk"],
        SINCE,
        UNTIL,
    )
    assert result.detail["session_attempts"] == 1
    assert result.detail["session_succeeded"] == 1
    assert result.detail["resumed_task_session_only"] == 1


def test_time_lost_gap():
    result = mod.measure_time_lost(
        [FIX / "outbox"],
        [FIX / "routing.jsonl"],
        [FIX / "briefs"],
        [FIX / "reports"],
        SINCE,
        UNTIL,
    )
    lost = [item for item in result.detail["cases"] if item["kind"] == "lost report"]
    assert len(lost) == 1
    assert lost[0]["brief"] == "q2-lost"
    assert lost[0]["gap_minutes"] == 30.0


def test_cli_json():
    command = [
        sys.executable,
        "-I",
        str(ROOT / "research" / "durable-steps" / "baseline.py"),
        "--since",
        "2026-11-14",
        "--until",
        "2026-11-16",
        "--json",
        "--outbox",
        str(FIX / "outbox"),
        "--briefs",
        str(FIX / "briefs"),
        "--reports",
        str(FIX / "reports"),
        "--routing",
        str(FIX / "routing.jsonl"),
        "--afk",
        str(FIX / "afk"),
        "--boundaries",
        str(FIX / "context-boundaries.jsonl"),
        "--lead-session",
        str(FIX / "sessions" / "lead.jsonl"),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=True)
    payload = json.loads(completed.stdout)
    names = [item["name"] for item in payload["measures"]]
    assert "sends_claude_lead" in names
    assert "reports" in names
    assert payload["window"]["untilExclusive"] is True