#!/usr/bin/env python3
"""Classify today's team tool time into tests/checks, waiting and other.

Read-only. Sources: the lead Claude transcript, the Devin pane transcript,
today's OpenCode sessions from its database, Codex rollouts, and hx one-off
runs. Every tool call is classified by its own wall time; a tool that waits
for another agent is "waiting", a test/check command is "tests", the rest is
"other". Run with `python -I scripts/team-time.py`.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sqlite3
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

HOME = Path.home()
CLAUDE = HOME / ".claude/projects/C--Users-caspe/19bea786-d5c8-4351-8086-29c396ac0796.jsonl"
DEVIN = HOME / ".herdr-team/state/dev-devin"
OPENCODE_DB = HOME / ".local/share/opencode/opencode.db"
CODEX = HOME / ".codex/sessions"
HX_RUNS = [
    Path(r"C:\Users\caspe\Documents\Projects\todo-rpg\.hx\runs"),
    Path(r"C:\Users\caspe\Documents\Projects\fde-portfolio-wealthops\wops-q799\.hx\runs"),
]
SECRET = re.compile(
    r"(?i)(token|api[_-]?key|secret|password)\s*[:=]\s*\S+|sk-[A-Za-z0-9_-]{8,}")


def safe_command(text: str) -> str:
    """A command for printing: test paths and names only, no secret-shaped values."""
    return SECRET.sub("[redacted]", text or "")


REPO_HINTS = [
    r"C:\Users\caspe\Documents\Projects\todo-rpg",
    r"C:\Users\caspe\Documents\Projects\todo-rpg-worktrees",
    r"C:\Users\caspe\Documents\Projects\fde-portfolio-wealthops",
    r"C:\Users\caspe\Documents\Projects\herdr-orchestration-stall",
    r"C:\Users\caspe\Documents\Projects\book-circle",
]

SEGMENT_SPLIT = re.compile(r"&&|\|\||;|\||\n")
WAIT_PATTERNS = (
    re.compile(r"^(?:&\s*)?(?:\"[^\"]*\"\s*)?hc\s+wait\b", re.I),
    re.compile(r"^(?:&\s*)?herdr\s+agent\s+wait\b", re.I),
    re.compile(r"^(?:&\s*)?hx\s+(?:ocrun|compact|team watch|say)\b", re.I),
    re.compile(r"^(?:&\s*)?Wait-Process\b", re.I),
)


def segments(command: str):
    return [part.strip() for part in SEGMENT_SPLIT.split(command or "") if part.strip()]


def _runner(segment: str, pattern: str) -> bool:
    return re.match(pattern, segment, re.I) is not None


def _suite_segment(segment: str) -> str | None:
    text = segment.lstrip()
    if _runner(text, r"^(?:&\s*)?(?:npx\s+)?vitest\b"):
        return "vitest"
    if _runner(text, r"^(?:&\s*)?(?:npx\s+)?tsc\b"):
        return "tsc"
    if _runner(text, r"^(?:&\s*)?(?:npx\s+)?eslint\b"):
        return "eslint"
    if _runner(text, r"^(?:&\s*)?npm\s+run\s+check\b"):
        return "check"
    if _runner(text, r"^(?:&\s*)?npm\s+run\s+test:scripts\b"):
        return "test:scripts"
    if _runner(text, r"^(?:&\s*)?npm\s+run\s+test:release\b"):
        return "test:release"
    if _runner(text, r"^(?:&\s*)?npm\s+run\s+e2e\b"):
        return "e2e"
    if _runner(text, r"^(?:&\s*)?npm\s+run\s+outfits:check\b"):
        return "outfits"
    if _runner(text, r"^(?:&\s*)?npm\s+run\s+creatures:check\b"):
        return "creatures"
    if _runner(text, r"^(?:&\s*)?node\s+--test\b"):
        return "node-test"
    if _runner(text, r"^(?:&\s*)?(?:npx\s+)?playwright\s+test\b"):
        return "e2e"
    if re.search(r"node_modules[/\\]@playwright[/\\]test[/\\]cli\.js\s+test", text, re.I):
        return "e2e"
    if _runner(text, r"^(?:&\s*)?node\s+scripts[/\\]e2e\.mjs\b"):
        return "e2e"
    if (_runner(text, r'^(?:&\s*)?"[^"]*\.exe"\s+-m\s+pytest\b')
            or _runner(text, r"^(?:&\s*)?\S*python\S*\s+.*-m\s+pytest\b")
            or _runner(text, r"^(?:&\s*)?(?:python|py)\s+-m\s+pytest\b")
            or _runner(text, r"^(?:&\s*)?pytest\b")):
        return "pytest"
    if _runner(text, r"^(?:&\s*)?(?:node|npm|npx|python)\b.*(?:outfits[/\\]check\.mjs|validate-catalogue|outfits:check)"):
        return "outfits"
    if _runner(text, r"^(?:&\s*)?(?:node|npm|npx|python)\b.*creatures[/\\]check\.mjs"):
        return "creatures"
    if _runner(text, r"^(?:&\s*)?(?:node|npm|npx|python)\b.*(?:golive|scripts[/\\]e2e)"):
        return "golive"
    if _runner(text, r"^(?:&\s*)?(?:python|node)\b.*run_evals\.py"):
        return "evals"
    if _runner(text, r"^(?:&\s*)?(?:python|node)\b.*(?:console_journey|askmas_journey|verify-jotarium[/\\]drive)"):
        return "verify"
    return None


def suite_of(command: str) -> str | None:
    for segment in segments(command):
        suite = _suite_segment(segment)
        if suite:
            return suite
    return None


def category(tool: str, command: str) -> str:
    if suite_of(command):
        return "tests"
    if tool in ("Task", "Agent", "AskUserQuestion", "task", "question"):
        return "waiting"
    for segment in segments(command):
        if any(pattern.search(segment) for pattern in WAIT_PATTERNS):
            return "waiting"
    return "other"


def _msys_path(value: str) -> str:
    match = re.match(r"^/([a-zA-Z])(/.*)$", value)
    if match:
        return match.group(1).upper() + ":" + match.group(2).replace("/", "\\")
    return value


def repo_of(command: str, workdir: str | None = None) -> str | None:
    paths = []
    for segment in segments(command):
        match = re.match(r"^cd\s+([\"\']?)(.+?)\1$", segment)
        if match:
            paths.append(_msys_path(match.group(2).strip()))
    if workdir:
        paths.append(_msys_path(str(workdir)))
    for candidate in reversed(paths):
        candidate = candidate.strip().strip('"\'')
        for root in REPO_HINTS:
            if candidate.lower().startswith(root.lower()):
                try:
                    out = subprocess.check_output(
                        ["git", "-C", candidate, "rev-parse", "--show-toplevel"],
                        stderr=subprocess.DEVNULL, text=True, timeout=10)
                    if out.strip():
                        return out.strip()
                except Exception:
                    continue
    return None


def day_window(day: dt.date) -> tuple[float, float]:
    start = dt.datetime.combine(day, dt.time.min).astimezone()
    end = start + dt.timedelta(days=1)
    return start.timestamp(), end.timestamp()


def parse_iso(value: str) -> float | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def ms(value) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    return float(value) / 1000.0


def commit_at(repo: str | None, ts: float) -> str | None:
    if not repo:
        return None
    stamp = dt.datetime.fromtimestamp(ts).astimezone().isoformat()
    try:
        out = subprocess.check_output(
            ["git", "-C", repo, "rev-list", "-1", f"--before={stamp}", "HEAD"],
            stderr=subprocess.DEVNULL, text=True, timeout=10)
        return out.strip() or None
    except Exception:
        return None


def add(rows, agent, source, tool, command, start, duration, workdir=None):
    if start is None or duration is None or duration <= 0:
        return
    rows.append({
        "agent": agent, "source": source, "tool": tool,
        "command": (command or "").replace("\n", " ")[:500],
        "start": start, "end": start + duration, "duration": duration,
        "category": category(tool, command or ""),
        "suite": suite_of(command or ""), "workdir": workdir,
    })


def claude_calls(start_ts, end_ts):
    uses, results = {}, {}
    if not CLAUDE.exists():
        return []
    for line in CLAUDE.open(encoding="utf-8", errors="replace"):
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if obj.get("isSidechain"):
            continue
        stamp = parse_iso(obj.get("timestamp", ""))
        if obj.get("type") == "assistant":
            for part in (obj.get("message") or {}).get("content") or []:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "tool_use":
                    uses[part.get("id")] = {
                        "t": stamp, "name": part.get("name"),
                        "input": part.get("input") or {}}
        elif obj.get("type") == "user":
            for part in (obj.get("message") or {}).get("content") or []:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "tool_result":
                    results[part.get("tool_use_id")] = stamp
    rows = []
    for tool_id, use in uses.items():
        result_ts = results.get(tool_id)
        if use["t"] is None or result_ts is None:
            continue
        if not (start_ts <= use["t"] < end_ts):
            continue
        tool = use["name"] or "?"
        data = use["input"] or {}
        command = data.get("command") if tool in ("Bash", "PowerShell") else ""
        if tool in ("Task", "Agent", "Skill", "AskUserQuestion"):
            command = tool + " " + json.dumps(data)[:400]
        add(rows, "lead-claude", "claude-jsonl", tool, str(command or ""),
            use["t"], result_ts - use["t"], data.get("workdir"))
    return rows


def atif_rows(path, agent, source, start_ts, end_ts):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    steps = [step for step in data.get("steps", []) if isinstance(step, dict)]
    events = []
    for step in steps:
        stamp = parse_iso(step.get("timestamp", ""))
        if stamp is None:
            continue
        calls = step.get("tool_calls") or []
        if not calls:
            continue
        events.append((stamp, calls))
    events.sort(key=lambda item: item[0])
    rows = []
    previous = events[0][0] if events else start_ts
    for stamp, calls in events:
        if not (start_ts <= stamp < end_ts):
            previous = stamp
            continue
        delta = max(0.0, min(stamp - previous, 3600.0))
        share = delta / max(1, len(calls))
        for call in calls:
            fn = call.get("function_name") or call.get("name") or "?"
            args = call.get("arguments") or call.get("input") or {}
            command = ""
            if isinstance(args, dict):
                command = str(args.get("command") or args.get("cmd") or "")
                workdir = args.get("workdir") or args.get("cwd")
            else:
                workdir = None
            add(rows, agent, source, fn, command, stamp - share, share, workdir)
        previous = stamp
    return rows


def devin_rows(start_ts, end_ts):
    rows = []
    if not DEVIN.exists():
        return rows
    for path in sorted(DEVIN.glob("transcript*.json")):
        rows += atif_rows(path, "devin", f"devin:{path.name}", start_ts, end_ts)
    return rows


def opencode_rows(start_ts, end_ts):
    rows = []
    if not OPENCODE_DB.exists():
        return rows
    try:
        db = sqlite3.connect(f"file:{OPENCODE_DB.as_posix()}?mode=ro", uri=True)
        sessions = db.execute(
            "select id, title, directory, time_created, time_updated from session "
            "where time_updated >= ? and time_created < ?",
            (int(start_ts * 1000), int(end_ts * 1000))).fetchall()
        for session_id, title, directory, created, updated in sessions:
            parts = db.execute(
                "select data, time_created, time_updated from part "
                "where session_id=? and time_updated >= ?",
                (session_id, int(start_ts * 1000))).fetchall()
            for raw, created_part, updated_part in parts:
                try:
                    part = json.loads(raw)
                except ValueError:
                    continue
                if part.get("type") != "tool":
                    continue
                state = part.get("state") or {}
                timing = state.get("time") or {}
                began = ms(timing.get("start"))
                if began is None:
                    began = ms(created_part)
                ended = ms(timing.get("end"))
                if ended is None:
                    ended = ms(updated_part)
                if not (start_ts <= began < end_ts):
                    continue
                if ended is not None and ended < began:
                    ended = began
                tool = str(part.get("tool") or "?")
                args = state.get("input") or {}
                command = str(args.get("command") or "") if isinstance(args, dict) else ""
                workdir = args.get("workdir") if isinstance(args, dict) else None
                add(rows, "opencode", f"opencode:{title[:40] or session_id}",
                    tool, command, began, (ended or began) - began,
                    workdir or directory)
        db.close()
    except sqlite3.Error:
        pass
    return rows


def codex_rows(start_ts, end_ts, day: dt.date):
    rows = []
    folder = CODEX / f"{day:%Y/%m/%d}"
    files = sorted(folder.glob("*.jsonl")) if folder.exists() else []
    for path in files:
        calls, outputs = {}, {}
        for line in path.open(encoding="utf-8", errors="replace"):
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            payload = obj.get("payload") or {}
            stamp = parse_iso(obj.get("timestamp", ""))
            if payload.get("type") == "function_call":
                calls[payload.get("call_id")] = (stamp, payload)
            elif payload.get("type") == "function_call_output":
                outputs[payload.get("call_id")] = stamp
        for call_id, (stamp, payload) in calls.items():
            end = outputs.get(call_id)
            if stamp is None or end is None or not (start_ts <= stamp < end_ts):
                continue
            args = payload.get("arguments") or ""
            command = ""
            try:
                parsed = json.loads(args) if isinstance(args, str) else args
                if isinstance(parsed, dict):
                    command = str(parsed.get("command") or parsed.get("cmd") or "")
            except ValueError:
                command = str(args)[:500]
            add(rows, "codex", f"codex:{path.name}", payload.get("name") or "?",
                command, stamp, end - stamp)
    return rows


def hx_rows(start_ts, end_ts):
    rows = []
    for root in HX_RUNS:
        if not root.exists():
            continue
        for run in sorted(root.iterdir()):
            if not run.is_dir():
                continue
            export = run / "export.json"
            if not export.exists():
                continue
            rows += atif_rows(export, "hx-one-off", f"hx:{run.name}", start_ts, end_ts)
    return rows


def summarise(rows):
    by = defaultdict(lambda: {"rows": 0, "busy": 0.0, "tests": 0.0,
                              "waiting": 0.0, "other": 0.0,
                              "first": None, "last": None,
                              "sources": set()})
    for row in rows:
        key = row["agent"]
        entry = by[key]
        entry["rows"] += 1
        entry["busy"] += row["duration"]
        entry[row["category"]] += row["duration"]
        entry["sources"].add(row["source"])
        entry["first"] = row["start"] if entry["first"] is None else min(entry["first"], row["start"])
        entry["last"] = row["end"] if entry["last"] is None else max(entry["last"], row["end"])
    result = []
    for agent, entry in sorted(by.items()):
        span = (entry["last"] - entry["first"]) / 60 if entry["first"] is not None else 0
        result.append({
            "agent": agent, "rows": entry["rows"],
            "session_minutes": round(span, 1),
            "busy_minutes": round(entry["busy"] / 60, 1),
            "test_minutes": round(entry["tests"] / 60, 1),
            "wait_minutes": round(entry["waiting"] / 60, 1),
            "other_minutes": round(entry["other"] / 60, 1),
            "test_share_busy": round(entry["tests"] / entry["busy"], 3) if entry["busy"] else 0,
            "test_share_session": round(entry["tests"] / (span * 60), 3) if span else 0,
            "sources": sorted(entry["sources"]),
        })
    return result


def duplicates(rows):
    groups = defaultdict(list)
    for row in rows:
        if row["category"] != "tests" or not row["suite"]:
            continue
        repo = repo_of(row["command"], row.get("workdir"))
        commit = commit_at(repo, row["start"])
        row["repo"] = repo
        row["commit"] = commit
        groups[(repo, commit, row["suite"])].append(row)
    found = []
    for (repo, commit, suite), items in groups.items():
        agents = sorted({item["agent"] for item in items})
        if len(agents) > 1:
            found.append({
                "repo": repo, "commit": (commit or "")[:10], "suite": suite,
                "agents": agents, "runs": len(items),
                "minutes": round(sum(item["duration"] for item in items) / 60, 1),
                "starts": [dt.datetime.fromtimestamp(item["start"]).strftime("%H:%M")
                           for item in items],
            })
    return found


def missing_sources(day: dt.date):
    missing = []
    if not CLAUDE.exists():
        missing.append("lead Claude transcript not found")
    if not any(DEVIN.glob("transcript*.json")):
        missing.append("Devin transcript not found")
    if not OPENCODE_DB.exists():
        missing.append("OpenCode database not found")
    codex = CODEX / f"{day:%Y/%m/%d}"
    if not codex.exists() or not list(codex.glob("*.jsonl")):
        missing.append(f"no Codex session files for {day.isoformat()}")
    if not any(root.exists() for root in HX_RUNS):
        missing.append("no hx runs roots")
    return missing


def main(argv=None):
    parser = argparse.ArgumentParser(description="Classify today's team tool time.")
    parser.add_argument("--date", help="local date YYYY-MM-DD; default today")
    parser.add_argument("--json", action="store_true", help="print the full JSON")
    args = parser.parse_args(argv)
    day = dt.date.fromisoformat(args.date) if args.date else dt.date.today()
    start, end = day_window(day)
    rows = []
    rows += claude_calls(start, end)
    rows += devin_rows(start, end)
    rows += opencode_rows(start, end)
    rows += codex_rows(start, end, day)
    rows += hx_rows(start, end)
    report = {
        "date": day.isoformat(),
        "window": [dt.datetime.fromtimestamp(start).isoformat(),
                   dt.datetime.fromtimestamp(end).isoformat()],
        "missing": missing_sources(day),
        "agents": summarise(rows),
        "duplicates": duplicates(rows),
        "test_runs": [
            {"agent": row["agent"], "suite": row["suite"],
             "start": dt.datetime.fromtimestamp(row["start"]).isoformat(),
             "minutes": round(row["duration"] / 60, 1),
             "repo": row.get("repo"), "commit": (row.get("commit") or "")[:10],
             "command": safe_command(row["command"])[:200]}
            for row in sorted(rows, key=lambda r: r["start"])
            if row["category"] == "tests"
        ],
    }
    if args.json:
        print(json.dumps(report, indent=2))
        return 0
    print(f"date {report['date']}  window {report['window'][0]} .. {report['window'][1]}")
    if report["missing"]:
        print("missing:", "; ".join(report["missing"]))
    print("\nagents")
    print(f"{'agent':16s} {'rows':>4s} {'session':>8s} {'busy':>7s} {'tests':>7s} "
          f"{'wait':>7s} {'other':>7s} {'test/busy':>9s}")
    for agent in report["agents"]:
        print(f"{agent['agent']:16s} {agent['rows']:4d} {agent['session_minutes']:8.1f} "
              f"{agent['busy_minutes']:7.1f} {agent['test_minutes']:7.1f} "
              f"{agent['wait_minutes']:7.1f} {agent['other_minutes']:7.1f} "
              f"{agent['test_share_busy']:9.1%}")
    print("\nslowest test runs")
    for run in sorted(report["test_runs"], key=lambda r: -r["minutes"])[:25]:
        print(f"{run['minutes']:7.1f}m {run['start'][11:16]} {run['agent']:14s} "
              f"{run['suite']:14s} {run['commit']:10s} {safe_command(run['command'])[:100]}")
    print("\nduplicates")
    for dup in report["duplicates"]:
        print(f"{dup['minutes']:7.1f}m {dup['suite']:14s} {dup['commit']:10s} "
              f"{'+'.join(dup['agents'])} {dup['starts']}")
    if not report["duplicates"]:
        print("none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
