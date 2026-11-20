import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "greats"
FIXN = FIXTURES / "team-now"
SPEC = importlib.util.spec_from_file_location("greats_measure", ROOT / "research" / "greats" / "measure.py")
measure = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = measure
SPEC.loader.exec_module(measure)

SINCE = measure.parse_day("2026-10-25")
UNTIL = measure.parse_day("2026-11-08")
SINCE_N = measure.parse_day("2026-11-15")
UNTIL_N = measure.parse_day("2026-11-16")


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


def _team_options():
    return measure.Options(
        since=SINCE_N,
        until=UNTIL_N,
        routing=[FIXTURES / "routing.jsonl"],
        ledger=FIXTURES / "ledger.json",
        todo_repo=FIXTURES,
        lead_sessions=[FIXTURES / "sessions" / "lead.jsonl"],
        session_paths=sorted((FIXTURES / "sessions").glob("*.jsonl")),
        commit_counter=lambda repo, start, end: 0,
        outbox=[FIXN / "outbox"],
        briefs=[FIXN / "briefs"],
        reports=[FIXN / "reports"],
        afk=[FIXN / "afk"],
        boundaries=[FIXN / "context-boundaries.jsonl"],
    )


def _team_rows():
    return {row.name: row for row in measure.measure_team_now(_team_options())}


def test_team_now_briefs_per_worker():
    row = _team_rows()["briefs_per_worker"]
    assert row.denominator == 4
    assert row.numerator == 3
    workers = row.detail["workers"]
    assert workers["dev-codex"]["distinct"] == 2
    assert workers["dev-codex"]["answered"] == 1
    assert workers["dev-devin"]["answered"] == 1
    assert workers["dev-opencode"]["answered"] == 1
    assert "lower bound on answered briefs" in row.value


def test_team_now_lead_reports():
    row = _team_rows()["lead_reports"]
    assert row.detail["total"] == 3
    assert row.detail["confirmed"] == 1
    assert row.detail["uncertain"] == 1
    assert row.detail["timeout"] == 1
    assert row.detail["other"] == 0


def test_team_now_worker_compactions():
    row = _team_rows()["worker_compactions"]
    assert row.denominator == 5
    assert row.numerator == 2
    assert row.detail["afk_keyed"] == 4
    assert row.detail["afk_drop"] == 1
    assert row.detail["afk_no_drop"] == 1
    assert row.detail["afk_unknown"] == 2
    assert row.detail["context_keyed"] == 1
    assert row.detail["context_drop"] == 1


def test_team_now_cross_family_reviews():
    row = _team_rows()["cross_family_reviews"]
    assert row.detail["total"] == 3
    assert row.detail["families"] == {"codex": 2, "devin": 1}


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
        "--outbox",
        str(FIXN / "outbox"),
        "--briefs",
        str(FIXN / "briefs"),
        "--reports",
        str(FIXN / "reports"),
        "--afk",
        str(FIXN / "afk"),
        "--boundaries",
        str(FIXN / "context-boundaries.jsonl"),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=True)
    payload = json.loads(completed.stdout)
    names = [item["name"] for item in payload["measures"]]
    assert names == ["failed_run_rate", "compaction", "review_coverage", "skill_use"]
    team_names = [item["name"] for item in payload["team_now"]]
    assert team_names == [
        "briefs_per_worker",
        "lead_reports",
        "worker_compactions",
        "cross_family_reviews",
    ]
    assert payload["window"]["untilExclusive"] is True