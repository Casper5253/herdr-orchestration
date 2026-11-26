"""Q-1015: heavy/light dispatch and the stuck-worker watchdog."""
import datetime as dt
import json
import subprocess
from pathlib import Path

from hx import balance, host

NOW = dt.datetime(2026, 11, 26, 12, 0, tzinfo=dt.timezone.utc)
POLICY = {"team_prefix": "dev-", "lead_role": "lead", "next": {
    "heavy_cap": 1, "heavy_cap_when_memory_ok": 2,
    "free_memory_gb_threshold": 6.0, "stuck_minutes": 45}}


def member(name, status="idle"):
    team, _, role = name.rpartition("-")
    return host.Member(name=name, team=team, role=role, kind=role, status=status,
        cwd="", address=name, session_id="s", session_reason="fixture",
        focused=None, turn=1, created_ms=1)


class FakeLedger:
    repo_root = Path(".")
    def __init__(self, tasks): self.tasks = list(tasks)
    def list_tasks(self, status=None):
        return [t for t in self.tasks if not status or t.get("status") == status]


def task(tid, owner, weight, status="in_progress", updated="2026-11-26T11:00:00+00:00"):
    return {"id": tid, "title": tid, "owner": owner, "weight": weight,
            "status": status, "updated": updated}


def unit(weight="heavy", hint="codex", ref="u"):
    return {"source": "audit", "ref": ref, "title": ref, "weight": weight,
            "worker_hint": hint, "why": "fixture"}


def snap(tmp_path, tasks=None, members=None, free_gb=32.0, oneoffs=0,
         choices=None, screen="same", reports=None, now=NOW, state_path=None):
    members = members if members is not None else [member("dev-lead", "working")]
    reports = reports or {}
    state_path = state_path or tmp_path / "balance-state.json"
    def next_unit(weight=None, running_heavy=0, worker=None, exclude_refs=None):
        return (choices or {}).get((weight, worker), unit(weight or "light", worker or "codex", f"{weight}-{worker}"))
    def read_screen(name, lines=240):
        if screen is None: raise RuntimeError("unreadable")
        return screen
    return balance.snapshot(ledger=FakeLedger(tasks or []), members=members,
        policy=POLICY, now=now, free_gb=free_gb, oneoff_count=oneoffs,
        next_unit=next_unit, read_screen=read_screen,
        report_time=lambda worker, lead: reports.get(worker), state_path=state_path)


def test_slots_and_running_states(tmp_path):
    rows = snap(tmp_path, tasks=[task("h", "dev-codex", "heavy"), task("l", "dev-claude", "light"), task("open", "dev-opencode", "heavy", status="open")],
        members=[member("dev-lead"), member("dev-codex", "working"), member("dev-claude", "working"), member("dev-opencode")])
    slots = {s["worker"]: s for s in rows["slots"]}
    assert slots["dev-codex"]["state"] == "heavy"
    assert slots["dev-codex"]["task_id"] == "h"
    assert slots["dev-codex"]["since"] == "2026-11-26T11:00:00+00:00"
    assert slots["dev-claude"]["state"] == "light"
    assert slots["dev-opencode"]["state"] == "idle"
    assert rows["running_heavy"] == 1


def test_cap_memory_oneoffs_and_assignment_order(tmp_path):
    workers = [member("dev-lead"), member("dev-codex"), member("dev-claude"), member("dev-opencode")]
    roomy = snap(tmp_path / "a", members=workers, free_gb=8)
    tight = snap(tmp_path / "b", members=workers, free_gb=2)
    assert roomy["heavy_cap"] == 2 and tight["heavy_cap"] == 1
    assert [a["weight"] for a in roomy["assignments"]] == ["heavy", "heavy", "light"]
    limited = snap(tmp_path / "c", members=workers, free_gb=8, oneoffs=2)
    assert limited["running_heavy"] == 2
    assert all(a["weight"] == "light" for a in limited["assignments"])


def test_worker_hint_is_passed_and_fallback_is_explicit(tmp_path):
    rows = snap(tmp_path, members=[member("dev-lead"), member("dev-opencode")],
        choices={("heavy", "opencode"): unit("heavy", "codex", "chosen")})
    got = rows["assignments"][0]
    assert got["unit"]["ref"] == "chosen" and got["hint"] == "fallback"
    rows = snap(tmp_path / "m", members=[member("dev-lead"), member("dev-codex")],
        choices={("heavy", "codex"): unit("heavy", "codex", "chosen")})
    assert rows["assignments"][0]["hint"] == "matched"


def stuck_args(tmp_path, report_at=None):
    state = tmp_path / "state.json"
    tasks = [task("t1", "dev-codex", "heavy", updated="2026-11-26T10:00:00+00:00")]
    members = [member("dev-lead"), member("dev-codex", "working")]
    return dict(tasks=tasks, members=members, reports={"dev-codex": report_at}, state_path=state)


def test_stuck_after_no_status_output_or_report_for_threshold(tmp_path):
    args = stuck_args(tmp_path)
    first = snap(tmp_path, **args)
    assert first["slots"][0]["state"] == "heavy"
    later = NOW + dt.timedelta(minutes=46)
    rows = snap(tmp_path, now=later, **args)
    slot = rows["slots"][0]
    assert slot["state"] == "stuck" and slot["minutes_stuck"] >= 45
    assert rows["stuck"][0]["recovery"]["step"] == "read-screen"
    assert rows["stuck"][0]["recovery"]["ladder"] == ["read its screen", "nudge it", "restart it at a task boundary"]


def test_output_change_and_recent_report_prevent_stuck(tmp_path):
    args = stuck_args(tmp_path, report_at=NOW + dt.timedelta(minutes=30))
    snap(tmp_path, **args)
    rows = snap(tmp_path, now=NOW + dt.timedelta(minutes=46), **args)
    assert rows["slots"][0]["state"] == "heavy"

    args = stuck_args(tmp_path / "out")
    snap(tmp_path / "out", **args)
    rows = snap(tmp_path / "out", now=NOW + dt.timedelta(minutes=20), screen="same\nnew output", **args)
    assert rows["slots"][0]["state"] == "heavy"
    rows = snap(tmp_path / "out", now=NOW + dt.timedelta(minutes=64), screen="same\nnew output", **args)
    assert rows["slots"][0]["state"] == "heavy"
    rows = snap(tmp_path / "out", now=NOW + dt.timedelta(minutes=66), screen="same\nnew output", **args)
    assert rows["slots"][0]["state"] == "stuck"


def test_process_scan_pattern_and_json_shape(tmp_path, monkeypatch):
    calls = []
    def runner(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="3\n", stderr="")
    assert balance.count_oneoff_processes(runner=runner) == 3
    assert "$_.ProcessId -ne $PID" in calls[0][-1]
    assert "hx(\.exe)?\"? +ocrun" in calls[0][-1] and "-m hx ocrun" in calls[0][-1]
    rows = snap(tmp_path, members=[member("dev-lead"), member("dev-codex")])
    assert set(rows) >= {"heavy_cap", "running_heavy", "oneoff_count", "free_memory_gb", "slots", "assignments", "stuck", "warnings"}
    json.dumps(rows)


def test_idle_workers_never_get_the_same_unit_in_one_run(tmp_path):
    offered = [unit("heavy", "codex", "same"), unit("heavy", "claude", "other")]
    def next_unit(weight=None, running_heavy=0, worker=None, exclude_refs=None):
        excluded = set(exclude_refs or [])
        return next(u for u in offered if u["ref"] not in excluded)
    state = tmp_path / "state.json"
    rows = balance.snapshot(ledger=FakeLedger([]), members=[member("dev-lead"), member("dev-codex"), member("dev-devin")],
        policy=POLICY, now=NOW, free_gb=32, oneoff_count=0, next_unit=next_unit,
        read_screen=lambda n, lines=240: "", report_time=lambda w, l: None, state_path=state)
    refs = [a["unit"]["ref"] for a in rows["assignments"]]
    assert refs == ["same", "other"]


def test_devin_gets_no_second_copy_when_no_distinct_unit_exists(tmp_path):
    def next_unit(weight=None, running_heavy=0, worker=None, exclude_refs=None):
        if "same" in set(exclude_refs or []): raise balance.NextUnitUnavailable("no distinct unit")
        return unit("heavy", "codex", "same")
    rows = balance.snapshot(ledger=FakeLedger([]), members=[member("dev-lead"), member("dev-codex"), member("dev-devin")],
        policy=POLICY, now=NOW, free_gb=32, oneoff_count=0, next_unit=next_unit,
        read_screen=lambda n, lines=240: "", report_time=lambda w, l: None, state_path=tmp_path / "state.json")
    assert [a["worker"] for a in rows["assignments"]] == ["dev-codex"]


def test_codex_gets_no_ordinary_unit_under_the_policy_rule(tmp_path):
    policy = json.loads(json.dumps(POLICY))
    policy["next"]["worker_assignment_rules"] = {"codex": {"allow_if_any": [
        {"source": "follow-up", "why_contains": "failed task"},
        {"title_contains_any": ["security", "token"]}]}}
    rows = snap(tmp_path, members=[member("dev-lead"), member("dev-codex")], choices={("heavy", "codex"): unit("heavy", "codex", "ordinary")})
    rows = balance.snapshot(ledger=FakeLedger([]), members=[member("dev-lead"), member("dev-codex")],
        policy=policy, now=NOW, free_gb=32, oneoff_count=0,
        next_unit=lambda weight=None, running_heavy=0, worker=None, exclude_refs=None: unit("heavy", "codex", "ordinary"),
        read_screen=lambda n, lines=240: "", report_time=lambda w, l: None, state_path=tmp_path / "state.json")
    assert rows["assignments"] == []


def test_codex_rule_allows_failed_followups_and_security_or_token_checks(tmp_path):
    policy = json.loads(json.dumps(POLICY))
    policy["next"]["worker_assignment_rules"] = {"codex": {"allow_if_any": [
        {"source": "follow-up", "why_contains": "failed task"},
        {"title_contains_any": ["security", "token"]}]}}
    follow = unit("heavy", "codex", "fix")
    follow["source"] = "follow-up"
    follow["why"] = "failed task left a next action"
    rows = balance.snapshot(ledger=FakeLedger([]), members=[member("dev-lead"), member("dev-codex")],
        policy=policy, now=NOW, free_gb=32, oneoff_count=0,
        next_unit=lambda weight=None, running_heavy=0, worker=None, exclude_refs=None: follow,
        read_screen=lambda n, lines=240: "", report_time=lambda w, l: None, state_path=tmp_path / "state.json")
    assert len(rows["assignments"]) == 1
