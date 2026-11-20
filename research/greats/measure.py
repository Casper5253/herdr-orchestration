#!/usr/bin/env python3
"""Measure the learn-from-the-greats baseline and any later window.

Four measures, each reading the same source the 8 November baseline used:

- failed_run_rate  .hx/routing.jsonl decision and outcome records in both repos.
- compaction       the four lead Claude session JSONLs, counting hx context
                   decide and hx compact invocations and the action they print.
- review_coverage  .hx/ledger.json tasks created in the window against the
                   number of commits in the todo-rpg checkout.
- skill_use        Skill tool_use across every Claude session JSONL.

Windows are half-open: [since 00:00 UTC, until 00:00 UTC). The baseline window
is --since 2026-10-25 --until 2026-11-08.

Stdlib only. Run:
  python -I research/greats/measure.py --since 2026-10-25 --until 2026-11-08
  python -I research/greats/measure.py --since 2026-11-08 --until 2026-11-21 --json
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROJECTS = ROOT.parent
CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"

# The four lead transcripts the baseline inventory used. They are main
# sessions, not subagents, and they cover Jotarium and herdr work.
LEAD_SESSION_IDS = (
    "6363b92e-c30b-4a81-a5d4-d382a2848927",
    "9633d81c-2b0a-4528-bbf7-753635f6f109",
    "b695d635-94c5-45b5-8306-15c85ee22c6f",
    "dab6c5ce-9924-482a-9bcc-77d010b3131c",
)
DEFAULT_ROUTING = (
    ROOT / ".hx" / "routing.jsonl",
    PROJECTS / "todo-rpg-worktrees" / "devin-pane" / ".hx" / "routing.jsonl",
)
DEFAULT_LEDGER = ROOT / ".hx" / "ledger.json"
DEFAULT_TODO_REPO = PROJECTS / "todo-rpg"
ACTION_RE = re.compile(r'"action"\s*:\s*"([^"]+)"')


@dataclass
class Measure:
    name: str
    value: str
    numerator: float
    denominator: float
    source: str
    detail: dict = field(default_factory=dict)

    def as_json(self) -> dict:
        return asdict(self)


@dataclass
class Options:
    since: datetime
    until: datetime
    routing: list
    ledger: Path
    todo_repo: Path
    lead_sessions: list
    session_paths: list
    commit_counter: object = None


def parse_day(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def parse_ts(value):
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def in_window(value, since: datetime, until: datetime) -> bool:
    stamp = parse_ts(value)
    return stamp is not None and since <= stamp < until


def read_jsonl(path, needles=None):
    try:
        handle = open(path, encoding="utf-8")
    except OSError:
        return
    with handle:
        for line in handle:
            if needles and not any(needle in line for needle in needles):
                continue
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def measure_failed_runs(routing_paths, since: datetime, until: datetime) -> Measure:
    decisions = 0
    outcomes = 0
    failed = 0
    failure_kinds = Counter()
    workers = Counter()
    used = []
    for path in map(Path, routing_paths):
        if not path.exists():
            continue
        used.append(str(path))
        for record in read_jsonl(path):
            if not in_window(record.get("ts"), since, until):
                continue
            kind = record.get("kind")
            if kind == "decision":
                decisions += 1
            elif kind == "outcome":
                outcomes += 1
                worker = record.get("worker") or record.get("provider") or "?"
                workers[worker] += 1
                if record.get("ok"):
                    continue
                failed += 1
                failure = record.get("failure") or {}
                failure_kinds[failure.get("kind") or "?"] += 1
    rate = failed / outcomes if outcomes else 0.0
    value = f"{failed}/{outcomes} failed ({rate:.1%})" if outcomes else "no outcomes"
    return Measure(
        "failed_run_rate",
        value,
        failed,
        outcomes,
        f".hx/routing.jsonl ({len(used)} files)",
        {
            "decisions": decisions,
            "failure_kinds": dict(failure_kinds),
            "workers": dict(workers),
        },
    )


def strip_heredocs(command: str) -> str:
    kept = []
    terminator = None
    for line in command.splitlines():
        if terminator is None:
            match = re.search(r"<<-?\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?", line)
            if match:
                terminator = match.group(1)
                kept.append(line.split("<<", 1)[0])
                continue
            kept.append(line)
        elif line.strip() == terminator:
            terminator = None
    return "\n".join(kept)


def context_invocations(command: str) -> list:
    if not command:
        return []
    text = strip_heredocs(command)
    try:
        tokens = shlex.split(text, posix=True)
    except ValueError:
        tokens = text.split()
    kinds = []
    for index, token in enumerate(tokens):
        if token != "hx" or index + 1 >= len(tokens):
            continue
        following = tokens[index + 1]
        if following == "compact":
            if index + 2 < len(tokens) and tokens[index + 2] in ("--help", "-h"):
                continue
            kinds.append("compact")
        elif following == "context" and index + 2 < len(tokens) and tokens[index + 2] == "decide":
            if index + 3 < len(tokens) and tokens[index + 3] in ("--help", "-h"):
                continue
            kinds.append("decide")
    return kinds


def tool_result_text(result: dict) -> str:
    content = result.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text", "")))
            else:
                parts.append(str(item))
        return " ".join(parts)
    return str(content or "")


def first_action(text: str):
    match = ACTION_RE.search(text)
    if match:
        return match.group(1)
    lowered = text.lower()
    for word in ("clear-needed", "compact-needed", "self-compact", "keep"):
        if re.search(rf"\b{re.escape(word)}\b", lowered):
            return word
    return None


def measure_compaction(session_paths, since: datetime, until: datetime) -> Measure:
    decisions = 0
    attempts = 0
    refused = 0
    sent = 0
    unknown = 0
    actions = Counter()
    used = []
    for path in map(Path, session_paths):
        if not path.exists():
            continue
        used.append(path.name)
        pending = {}
        for record in read_jsonl(path, needles=('"tool_use"', '"tool_result"')):
            kind = record.get("type")
            message = record.get("message") or {}
            content = message.get("content")
            if kind == "assistant" and isinstance(content, list):
                for block in content:
                    if not (isinstance(block, dict) and block.get("type") == "tool_use"):
                        continue
                    if block.get("name") not in ("Bash", "PowerShell"):
                        continue
                    kinds = context_invocations((block.get("input") or {}).get("command", ""))
                    if kinds:
                        pending[block.get("id")] = (record.get("timestamp"), kinds)
            elif kind == "user" and isinstance(content, list):
                for block in content:
                    if not (isinstance(block, dict) and block.get("type") == "tool_result"):
                        continue
                    tool_id = block.get("tool_use_id")
                    if tool_id not in pending:
                        continue
                    stamp, kinds = pending.pop(tool_id)
                    if not in_window(stamp, since, until):
                        continue
                    action = first_action(tool_result_text(block)) or "?"
                    actions[action] += 1
                    for item in kinds:
                        if item == "decide":
                            decisions += 1
                            continue
                        attempts += 1
                        if action == "keep":
                            refused += 1
                        elif action in ("compact", "compacted", "send", "sent"):
                            sent += 1
                        else:
                            unknown += 1
    value = f"{sent} sent / {attempts} attempts"
    if refused:
        value += f" ({refused} refused)"
    if unknown:
        value += f" ({unknown} unknown)"
    return Measure(
        "compaction",
        value,
        sent,
        attempts,
        f"Claude lead sessions ({len(used)} files)",
        {
            "decisions": decisions,
            "actions": dict(actions),
            "sessions": used,
        },
    )


def count_commits(repo: Path, since: datetime, until: datetime) -> int:
    result = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "rev-list",
            "--count",
            f"--since={since:%Y-%m-%d}",
            f"--until={until:%Y-%m-%d}",
            "HEAD",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return 0
    try:
        return int(result.stdout.strip())
    except ValueError:
        return 0


def measure_review_coverage(
    ledger_path,
    repo,
    since: datetime,
    until: datetime,
    commit_counter=None,
) -> Measure:
    ledger = read_json(ledger_path)
    tasks = []
    for task_id, task in (ledger.get("tasks") or {}).items():
        created = parse_ts(task.get("created"))
        if created is None or not (since <= created < until):
            continue
        label = f"{task_id} {task.get('title') or ''}".lower()
        if "review" in label or "test" in label:
            tasks.append(task_id)
    counter = commit_counter or count_commits
    commits = counter(Path(repo), since, until)
    rate = len(tasks) / commits if commits else 0.0
    return Measure(
        "review_coverage",
        f"{len(tasks)}/{commits} commits ({rate:.1%})",
        len(tasks),
        commits,
        ".hx/ledger.json + git log todo-rpg",
        {"tasks": tasks},
    )


def measure_skill_use(session_paths, since: datetime, until: datetime) -> Measure:
    distribution = Counter()
    files_with_tool = 0
    files_with_skill = 0
    subagent_invocations = 0
    scanned = 0
    for path in map(Path, session_paths):
        if not path.exists():
            continue
        scanned += 1
        had_tool = False
        had_skill = False
        for record in read_jsonl(path, needles=('"type":"assistant"',)):
            if record.get("type") != "assistant":
                continue
            if not in_window(record.get("timestamp"), since, until):
                continue
            content = (record.get("message") or {}).get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not (isinstance(block, dict) and block.get("type") == "tool_use"):
                    continue
                had_tool = True
                if block.get("name") != "Skill":
                    continue
                had_skill = True
                skill = (block.get("input") or {}).get("skill") or "<unknown>"
                distribution[skill] += 1
                if record.get("isSidechain"):
                    subagent_invocations += 1
        if had_tool:
            files_with_tool += 1
        if had_skill:
            files_with_skill += 1
    total = sum(distribution.values())
    return Measure(
        "skill_use",
        f"{total} invocations",
        total,
        files_with_tool,
        f"Claude session JSONLs ({scanned} files)",
        {
            "distribution": dict(distribution.most_common()),
            "files_with_skill": files_with_skill,
            "subagent_invocations": subagent_invocations,
        },
    )


def measure_all(options: Options) -> list:
    return [
        measure_failed_runs(options.routing, options.since, options.until),
        measure_compaction(options.lead_sessions, options.since, options.until),
        measure_review_coverage(
            options.ledger,
            options.todo_repo,
            options.since,
            options.until,
            options.commit_counter,
        ),
        measure_skill_use(options.session_paths, options.since, options.until),
    ]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", required=True, help="first day, inclusive (YYYY-MM-DD)")
    parser.add_argument("--until", required=True, help="last day, exclusive (YYYY-MM-DD)")
    parser.add_argument("--json", action="store_true", help="print JSON instead of a table")
    parser.add_argument("--sessions-root", default=str(CLAUDE_PROJECTS))
    parser.add_argument("--routing", action="append", help="routing.jsonl path (repeatable)")
    parser.add_argument("--ledger", help="ledger.json path")
    parser.add_argument("--todo-repo", help="todo-rpg checkout for the commit count")
    parser.add_argument("--lead-session", action="append", help="lead session JSONL (repeatable)")
    parser.add_argument("--commits", type=int, help="override the git commit count (tests and offline)")
    return parser


def options_from_args(args) -> Options:
    since = parse_day(args.since)
    until = parse_day(args.until)
    if until <= since:
        raise SystemExit("--until must be after --since")
    routing = [Path(item) for item in args.routing] if args.routing else list(DEFAULT_ROUTING)
    ledger = Path(args.ledger) if args.ledger else DEFAULT_LEDGER
    todo_repo = Path(args.todo_repo) if args.todo_repo else DEFAULT_TODO_REPO
    default_leads = [CLAUDE_PROJECTS / "C--Users-caspe" / f"{sid}.jsonl" for sid in LEAD_SESSION_IDS]
    lead_sessions = [Path(item) for item in args.lead_session] if args.lead_session else default_leads
    session_paths = sorted(Path(args.sessions_root).rglob("*.jsonl"))
    commit_counter = (lambda repo, start, end: args.commits) if args.commits is not None else None
    return Options(
        since,
        until,
        routing,
        ledger,
        todo_repo,
        lead_sessions,
        session_paths,
        commit_counter,
    )


def render_text(measures, since: datetime, until: datetime) -> str:
    days = (until - since).days
    lines = [
        f"window {since:%Y-%m-%d} -> {until:%Y-%m-%d} ({days} days, until exclusive)",
        "",
        f"{'measure':<18} {'value':<34} {'numerator':>9} {'denominator':>11}  source",
    ]
    for item in measures:
        lines.append(
            f"{item.name:<18} {item.value:<34} {item.numerator:>9g} "
            f"{item.denominator:>11g}  {item.source}"
        )
    for item in measures:
        if item.detail:
            lines.append("")
            lines.append(
                f"{item.name} detail: {json.dumps(item.detail, ensure_ascii=False, sort_keys=True)}"
            )
    return "\n".join(lines)


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    options = options_from_args(args)
    measures = measure_all(options)
    if args.json:
        print(
            json.dumps(
                {
                    "window": {
                        "since": args.since,
                        "until": args.until,
                        "untilExclusive": True,
                    },
                    "measures": [item.as_json() for item in measures],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(render_text(measures, options.since, options.until))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())