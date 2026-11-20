import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "greats"
SPEC = importlib.util.spec_from_file_location("greats_measure", ROOT / "research" / "greats" / "measure.py")
measure = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = measure
SPEC.loader.exec_module(measure)

SINCE = measure.parse_day("2026-10-25")
UNTIL = measure.parse_day("2026-11-08")


def test_failed_run_rate_counts_outcomes_in_window():
    result = measure.measure_failed_runs([FIXTURES / "routing.jsonl"], SINCE, UNTIL)
    assert result.numerator == 1
    assert result.denominator == 2
    assert result.detail["decisions"] == 2
    assert result.detail["failure_kinds"] == {"tool": 1}


def test_compaction_counts_decisions_and_refused_attempts():
    result = measure.measure_compaction([FIXTURES / "sessions" / "lead.jsonl"], SINCE, UNTIL)
    assert result.detail["decisions"] == 1
    assert result.denominator == 1
    assert result.numerator == 0
    assert result.detail["actions"] == {"keep": 2}


def test_review_coverage_uses_ledger_tasks_and_commit_count():
    result = measure.measure_review_coverage(
        FIXTURES / "ledger.json",
        FIXTURES,
        SINCE,
        UNTIL,
        commit_counter=lambda repo, start, end: 4,
    )
    assert result.numerator == 1
    assert result.denominator == 4
    assert result.detail["tasks"] == ["r1-review"]


def test_skill_use_counts_skill_tool_use():
    result = measure.measure_skill_use(
        sorted((FIXTURES / "sessions").glob("*.jsonl")), SINCE, UNTIL
    )
    assert result.numerator == 1
    assert result.denominator == 1
    assert result.detail["distribution"] == {"poteto-mode": 1}
    assert result.detail["files_with_skill"] == 1


def test_cli_prints_json_for_each_measure():
    command = [
        sys.executable,
        "-I",
        str(ROOT / "research" / "greats" / "measure.py"),
        "--since",
        "2026-10-25",
        "--until",
        "2026-11-08",
        "--json",
        "--sessions-root",
        str(FIXTURES / "sessions"),
        "--routing",
        str(FIXTURES / "routing.jsonl"),
        "--ledger",
        str(FIXTURES / "ledger.json"),
        "--todo-repo",
        str(FIXTURES),
        "--lead-session",
        str(FIXTURES / "sessions" / "lead.jsonl"),
        "--commits",
        "4",
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=True)
    payload = json.loads(completed.stdout)
    names = [item["name"] for item in payload["measures"]]
    assert names == ["failed_run_rate", "compaction", "review_coverage", "skill_use"]
    assert payload["window"]["untilExclusive"] is True