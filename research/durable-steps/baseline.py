#!/usr/bin/env python3
"""Measure the durable-steps baseline for hx.

Five measures over a half-open window [since 00:00 UTC, until 00:00 UTC):

1. sends by receipt, per destination kind.
2. lost and doubled reports, matched to brief files.
3. worker stalls and how they were recovered.
4. compactions attempted, succeeded, unknown, and the resumed task.
5. time between a lost report or failed run and the lead's next action.

Stdlib only. Run:
  python -I research/durable-steps/baseline.py --since 2026-11-08 --until 2026-11-21
  python -I research/durable-steps/baseline.py --since 2026-11-14 --until 2026-11-21 --json
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shlex
import statistics
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROJECTS = ROOT.parent
TODO = PROJECTS / "todo-rpg"
CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"

DEFAULT_OUTBOX = (TODO / ".hx" / "outbox", ROOT / ".hx" / "outbox")
DEFAULT_BRIEFS = (TODO / ".hx" / "briefs", ROOT / ".hx" / "briefs")
DEFAULT_REPORTS = (TODO / ".hx" / "reports", ROOT / ".hx" / "reports")
DEFAULT_ROUTING = (TODO / ".hx" / "routing.jsonl", ROOT / ".hx" / "routing.jsonl")
DEFAULT_AFK = (TODO / ".hx",)
DEFAULT_BOUNDARIES = (
    TODO / ".hx" / "context-boundaries.jsonl",
    ROOT / ".hx" / "context-boundaries.jsonl",
)
LEAD_SESSION_IDS = (
    "6363b92e-c30b-4a81-a5d4-d382a2848927",
    "9633d81c-2b0a-4528-bbf7-753635f6f109",
    "b695d635-94c5-45b5-8306-15c85ee22c6f",
    "dab6c5ce-9924-482a-9bcc-77d010b3131c",
)

RECEIPTS = ("submitted", "queued", "pending", "uncertain", "failed", "cancelled", "unknown")
BRIEF_RE = re.compile(r"(?:\.hx[/\\]briefs[/\\]|brief\s+)([A-Za-z0-9._-]+?)\.md", re.IGNORECASE)
QID_RE = re.compile(r"\bQ-(\d+)\b", re.IGNORECASE)
REPORT_RE = re.compile(r"(?:\.hx[/\\]reports[/\\]|report at\s+)([A-Za-z0-9._-]+?)\.md", re.IGNORECASE)
STEM_RE = re.compile(r"\b(q\d+[A-Za-z0-9._-]*)\b", re.IGNORECASE)
ACTION_RE = re.compile(r'"action"\s*:\s*"([^"]+)"')
TASK_RE = re.compile(r'"task_id"\s*:\s*"([^"]+)"')


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
    outbox: list
    briefs: list
    reports: list
    routing: list
    afk: list
    boundaries: list
    lead_sessions: list


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


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def read_text(path) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


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
            except ValueError:
                continue


def record_created(record: dict):
    return parse_ts(record.get("created_at") or record.get("updated_at"))


def destination_kind(target) -> str:
    name = str(target or "")
    if name.endswith("lead") or name in ("dev-claude", "dev-opus"):
        return "claude lead"
    if "codex" in name:
        return "codex"
    if "devin" in name:
        return "devin"
    if "opencode" in name:
        return "opencode"
    return "other"


def receipt_of(record: dict) -> str:
    receipt = record.get("receipt")
    if receipt:
        return str(receipt)
    state = record.get("state")
    return {
        "delivered": "submitted",
        "pending": "pending",
        "uncertain": "uncertain",
        "failed": "failed",
        "cancelled": "cancelled",
    }.get(state, "unknown")


def load_outbox(roots) -> list:
    records = []
    for root in map(Path, roots):
        if not root.is_dir():
            continue
        repo = root.parent.parent.name
        for sender_dir in root.iterdir():
            if not sender_dir.is_dir():
                continue
            for path in sender_dir.glob("*.json"):
                record = read_json(path)
                if not isinstance(record, dict):
                    continue
                record["_path"] = str(path)
                record["_sender_dir"] = sender_dir.name
                record["_repo"] = repo
                records.append(record)
    records.sort(key=lambda r: str(r.get("created_at") or ""))
    return records


def measure_sends(outbox_roots, since: datetime, until: datetime) -> list:
    records = [r for r in load_outbox(outbox_roots) if in_window(record_created(r), since, until)]
    matrix = Counter((destination_kind(r.get("target")), receipt_of(r)) for r in records)
    rows = []
    for kind in ("claude lead", "codex", "devin", "opencode"):
        counts = {name: matrix.get((kind, name), 0) for name in RECEIPTS}
        total = sum(counts.values())
        if not total:
            continue
        rate = counts["uncertain"] / total
        rows.append(
            Measure(
                "sends_" + kind.replace(" ", "_"),
                f"{counts['uncertain']}/{total} uncertain ({rate:.1%})",
                counts["uncertain"],
                total,
                "hx outbox (todo-rpg + herdr-orchestration)",
                {"destination": kind, "receipts": counts},
            )
        )
    return rows


def brief_key(record: dict):
    body = str(record.get("body") or "")
    match = BRIEF_RE.search(body)
    return match.group(1).lower() if match else None


def report_keys(record: dict):
    text = str(record.get("body") or "") + " " + str(record.get("text") or "")
    qids = {f"q-{number}" for number in QID_RE.findall(text)}
    stems = {stem.lower() for stem in REPORT_RE.findall(text)}
    stems |= {stem.lower() for stem in STEM_RE.findall(text)}
    return qids, stems


def find_brief_file(stem: str, brief_dirs):
    for directory in map(Path, brief_dirs):
        candidate = directory / f"{stem}.md"
        if candidate.exists():
            return candidate
    return None


def lead_briefs(records, brief_dirs, since: datetime, until: datetime) -> list:
    briefs = []
    for record in records:
        if not str(record.get("sender") or "").startswith("dev-lead"):
            continue
        if destination_kind(record.get("target")) == "claude lead":
            continue
        key = brief_key(record)
        if not key:
            continue
        created = record_created(record)
        if not in_window(created, since, until):
            continue
        path = find_brief_file(key, brief_dirs)
        qid = None
        if path is not None:
            match = QID_RE.search(read_text(path))
            if match:
                qid = f"q-{match.group(1)}"
        if qid is None:
            number = re.match(r"q(\d+)", key)
            if number:
                qid = f"q-{number.group(1)}"
        briefs.append(
            {
                "key": key,
                "qid": qid,
                "created": created,
                "target": record.get("target"),
                "file": str(path) if path is not None else None,
                "record": record,
            }
        )
    return briefs


def worker_reports(records, report_dirs, since: datetime, until: datetime) -> list:
    reports = []
    for record in records:
        if str(record.get("sender") or "").startswith("dev-lead"):
            continue
        if destination_kind(record.get("target")) != "claude lead":
            continue
        created = record_created(record)
        if not in_window(created, since, until):
            continue
        qids, stems = report_keys(record)
        reports.append(
            {
                "created": created,
                "sender": record.get("sender"),
                "qids": qids,
                "stems": stems,
                "text": str(record.get("body") or ""),
                "record": record,
                "kind": "outbox",
            }
        )
    for directory in map(Path, report_dirs):
        if not directory.is_dir():
            continue
        for path in directory.glob("*.md"):
            try:
                created = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            except OSError:
                continue
            if not in_window(created, since, until):
                continue
            text = read_text(path)
            # A report file answers the brief it is named after, never every Q-ref its text
            # mentions: the Q-732 baseline report lists "lost" briefs by Q-ref and would
            # otherwise mark all of them answered.
            qids = set()
            stem_match = re.match(r"(q\d+[A-Za-z0-9._-]*)", path.stem, re.IGNORECASE)
            stems = {stem_match.group(1).lower()} if stem_match else set()
            reports.append(
                {
                    "created": created,
                    "sender": "file",
                    "qids": qids,
                    "stems": stems,
                    "text": text,
                    "record": None,
                    "kind": "file",
                    "path": str(path),
                }
            )
    return reports


# A reply counts only if it arrives within this long after the brief's last send. Without a
# limit, a report written days later (for example a summary that names old briefs) "answers"
# them, and the result depends on where the window ends (20 Nov: 121 lost to 14 by moving
# --until one day).
ANSWER_WINDOW = timedelta(hours=24)


def answers_in_time(brief: dict, report: dict) -> bool:
    return brief["created"] <= report["created"] <= brief["last_created"] + ANSWER_WINDOW


def report_matches(brief: dict, report: dict) -> bool:
    if brief["qid"] and brief["qid"] in report["qids"]:
        return True
    if brief["key"] in report["text"].lower():
        return True
    return brief["key"] in report["stems"]


def brief_report_stats(outbox_roots, brief_dirs, report_dirs, since: datetime, until: datetime) -> dict:
    records = load_outbox(outbox_roots)
    raw_briefs = lead_briefs(records, brief_dirs, since, until)
    reports = worker_reports(records, report_dirs, since, until)
    grouped = {}
    for brief in raw_briefs:
        key = (brief["key"], brief["target"])
        if key not in grouped:
            grouped[key] = dict(brief)
            grouped[key]["sends"] = 1
            grouped[key]["last_created"] = brief["created"]
        else:
            grouped[key]["sends"] += 1
            if brief["created"] > grouped[key]["last_created"]:
                grouped[key]["last_created"] = brief["created"]
    briefs = sorted(grouped.values(), key=lambda item: item["created"])
    workers = {}
    for brief in briefs:
        stats = workers.setdefault(
            str(brief["target"]),
            {"sends": 0, "distinct": 0, "matched_key": 0,
             "matched_timing": 0, "lost": 0},
        )
        stats["sends"] += brief["sends"]
        stats["distinct"] += 1
    used = set()
    lost = []
    matched_key = 0
    matched_timing = 0
    for brief in briefs:
        stats = workers[str(brief["target"])]
        key_matches = [i for i, report in enumerate(reports)
                       if report_matches(brief, report) and answers_in_time(brief, report)]
        if key_matches:
            matched_key += 1
            stats["matched_key"] += 1
            used.update(key_matches)
            continue
        same_target = [b for b in briefs
                       if b["target"] == brief["target"]
                       and b["created"] > brief["last_created"]]
        next_time = min((b["created"] for b in same_target), default=None)
        timing_match = None
        for index, report in enumerate(reports):
            if index in used:
                continue
            if str(report["sender"]).endswith(
                    str(brief["target"]).split("-")[-1]):
                if answers_in_time(brief, report) and (
                        next_time is None or report["created"] < next_time):
                    timing_match = index
                    break
        if timing_match is not None:
            matched_timing += 1
            stats["matched_timing"] += 1
            used.add(timing_match)
            continue
        if brief["last_created"] < until - timedelta(hours=2):
            lost.append(brief)
            stats["lost"] += 1
    for stats in workers.values():
        stats["answered"] = stats["matched_key"] + stats["matched_timing"]
        stats["share"] = (
            stats["answered"] / stats["distinct"] if stats["distinct"] else 0.0
        )
    groups = defaultdict(list)
    for index, report in enumerate(reports):
        body = re.sub(r"\s+", " ", report["text"]).strip().lower()[:160]
        if body:
            groups[(report["sender"], body)].append(index)
    doubled = []
    for (sender, _body), indexes in groups.items():
        if len(indexes) > 1:
            doubled.append(
                {
                    "sender": sender,
                    "count": len(indexes),
                    "ids": [
                        str(reports[i]["record"].get("id")) if reports[i]["record"] else reports[i].get("path")
                        for i in indexes
                    ],
                    "created": [reports[i]["created"].isoformat() for i in indexes],
                }
            )
    lost_detail = [
        {
            "brief": brief["key"],
            "file": brief["file"],
            "target": brief["target"],
            "first": brief["created"].isoformat(),
            "last": brief["last_created"].isoformat(),
            "sends": brief["sends"],
            "record": brief["record"].get("id"),
        }
        for brief in lost
    ]
    return {
        "distinct_briefs": len(briefs),
        "brief_sends": len(raw_briefs),
        "reports": len(reports),
        "matched_key": matched_key,
        "matched_timing": matched_timing,
        "lost": lost_detail,
        "doubled": doubled,
        "workers": workers,
    }


def measure_reports(outbox_roots, brief_dirs, report_dirs, since: datetime, until: datetime) -> Measure:
    stats = brief_report_stats(outbox_roots, brief_dirs, report_dirs, since, until)
    return Measure(
        "reports",
        f"{len(stats['lost'])} lost / {stats['distinct_briefs']} distinct briefs",
        len(stats["lost"]),
        stats["distinct_briefs"],
        "hx outbox + .hx/briefs + .hx/reports",
        {
            "distinct_briefs": stats["distinct_briefs"],
            "brief_sends": stats["brief_sends"],
            "reports": stats["reports"],
            "matched_key": stats["matched_key"],
            "matched_timing": stats["matched_timing"],
            "lost": stats["lost"],
            "doubled": stats["doubled"],
            "workers": stats["workers"],
        },
    )


def lead_report_receipts(outbox_roots, since: datetime, until: datetime) -> dict:
    records = load_outbox(outbox_roots)
    rows = [
        record
        for record in records
        if not str(record.get("sender") or "").startswith("dev-lead")
        and destination_kind(record.get("target")) == "claude lead"
        and in_window(record_created(record), since, until)
    ]
    receipts = Counter(receipt_of(record) for record in rows)
    timeout = sum(
        1 for record in rows
        if receipt_of(record) == "uncertain"
        and "timeout" in str(record.get("note") or "").lower()
    )
    confirmed = receipts.get("submitted", 0) + receipts.get("queued", 0)
    uncertain = receipts.get("uncertain", 0) - timeout
    return {
        "total": len(rows),
        "confirmed": confirmed,
        "uncertain": uncertain,
        "timeout": timeout,
        "other": len(rows) - confirmed - uncertain - timeout,
        "receipts": dict(receipts),
    }
def afk_rows(directories, since: datetime, until: datetime) -> list:
    rows = []
    for directory in map(Path, directories):
        if not directory.is_dir():
            continue
        for path in directory.glob("afk-*.tsv"):
            match = re.search(r"afk-(\d{4}-\d{2}-\d{2})", path.name)
            if not match:
                continue
            day = parse_day(match.group(1))
            if not (since <= day < until):
                continue
            try:
                handle = open(path, encoding="utf-8", errors="replace")
            except OSError:
                continue
            with handle:
                reader = csv.reader(handle, delimiter="\t")
                header = next(reader, None)
                for cells in reader:
                    if not cells:
                        continue
                    rows.append({"file": path.name, "cells": cells, "header": header})
    return rows


STALL_WORDS = ("stopped mid", "stopped the", "hung", "exited", "died", "crash", "stalled", "stall", "lost on", "mid-brief", "mid-way", "never arrived", "timed out", "timeout", "blocked on", "window has reset", "partial edits")
RECOVERY_WORDS = ("re-brief", "rebrief", "restart", "resume", "retry", "nudged", "continue", "compacted", "recovery")


def measure_stalls(routing_paths, boundary_paths, afk_dirs, outbox_roots, since: datetime, until: datetime) -> Measure:
    outcomes = []
    for path in map(Path, routing_paths):
        for record in read_jsonl(path):
            if record.get("kind") != "outcome":
                continue
            if not in_window(record.get("ts"), since, until):
                continue
            outcomes.append(record)
    failed = [r for r in outcomes if not r.get("ok")]
    failure_kinds = Counter((r.get("failure") or {}).get("kind") or "?" for r in failed)
    failure_workers = Counter(r.get("worker") or "?" for r in failed)
    dispositions = Counter()
    boundary_actions = Counter()
    for path in map(Path, boundary_paths):
        for record in read_jsonl(path):
            if not in_window(record.get("recorded_at"), since, until):
                continue
            dispositions[str(record.get("disposition") or "?")] += 1
            boundary_actions[str(record.get("action") or "?")] += 1
    afk = afk_rows(afk_dirs, since, until)
    stall_rows = []
    recovery_rows = []
    for row in afk:
        text = " ".join(str(cell) for cell in row["cells"]).lower()
        if any(word in text for word in STALL_WORDS):
            stall_rows.append(row)
        if any(word in text for word in RECOVERY_WORDS):
            recovery_rows.append(row)
    records = load_outbox(outbox_roots)
    rebriefs = []
    for record in records:
        if not str(record.get("sender") or "").startswith("dev-lead"):
            continue
        if not in_window(record_created(record), since, until):
            continue
        body = str(record.get("body") or "").lower()
        if any(
            word in body
            for word in ("queued brief", "start it only after", "continue from", "window has reset", "compacted", "retry")
        ):
            rebriefs.append(
                {
                    "created": record_created(record).isoformat(),
                    "target": record.get("target"),
                    "body": str(record.get("body") or "")[:180],
                }
            )
    return Measure(
        "stalls",
        f"{len(failed)} failed runs, {len(stall_rows)} stall rows, {len(rebriefs)} re-briefs",
        len(failed),
        len(outcomes),
        "routing.jsonl + context-boundaries.jsonl + afk-*.tsv + outbox",
        {
            "outcomes": len(outcomes),
            "failed": len(failed),
            "failure_kinds": dict(failure_kinds),
            "failure_workers": dict(failure_workers),
            "dispositions": dict(dispositions),
            "boundary_actions": dict(boundary_actions),
            "afk_stall_rows": [{"file": row["file"], "cells": row["cells"][:3]} for row in stall_rows[:20]],
            "afk_recovery_rows": [{"file": row["file"], "cells": row["cells"][:3]} for row in recovery_rows[:20]],
            "rebriefs": rebriefs[:20],
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
        return " ".join(str(item.get("text", "")) for item in content if isinstance(item, dict))
    return str(content or "")


def first_action(text: str):
    match = ACTION_RE.search(text)
    if match:
        return match.group(1)
    lowered = text.lower()
    for word in ("compact-unknown", "compacted", "compact-needed", "clear-needed", "keep"):
        if re.search(rf"\b{word}\b", lowered):
            return word
    return None


def measure_compactions(session_paths, boundary_paths, afk_dirs, since: datetime, until: datetime) -> Measure:
    attempts = 0
    succeeded = 0
    unknown = 0
    refused = 0
    resumed = 0
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
                    if "compact" in kinds:
                        pending[block.get("id")] = record.get("timestamp")
            elif kind == "user" and isinstance(content, list):
                for block in content:
                    if not (isinstance(block, dict) and block.get("type") == "tool_result"):
                        continue
                    if block.get("tool_use_id") not in pending:
                        continue
                    stamp = pending.pop(block.get("tool_use_id"))
                    if not in_window(stamp, since, until):
                        continue
                    text = tool_result_text(block)
                    action = first_action(text) or "?"
                    actions[action] += 1
                    attempts += 1
                    if action == "compacted":
                        succeeded += 1
                        if TASK_RE.search(text):
                            resumed += 1
                    elif action == "compact-unknown":
                        unknown += 1
                    elif action == "keep":
                        refused += 1
    afk_attempts = 0
    afk_succeeded = 0
    afk_unknown = 0
    afk_rows_list = []
    for row in afk_rows(afk_dirs, since, until):
        text = " ".join(str(cell) for cell in row["cells"]).lower()
        if "compact" not in text:
            continue
        afk_attempts += 1
        afk_rows_list.append({"file": row["file"], "cells": row["cells"][:3]})
        if "compact-unknown" in text or "compaction failed" in text or "compact did not drop" in text:
            afk_unknown += 1
        elif "compacted" in text:
            afk_succeeded += 1
    boundary_actions = Counter()
    for path in map(Path, boundary_paths):
        for record in read_jsonl(path):
            if not in_window(record.get("recorded_at"), since, until):
                continue
            boundary_actions[str(record.get("action") or "?")] += 1
    total_attempts = attempts + afk_attempts
    total_succeeded = succeeded + afk_succeeded
    total_unknown = unknown + afk_unknown
    return Measure(
        "compactions",
        f"{total_succeeded} succeeded, {total_unknown} unknown, {refused} refused / {total_attempts} attempts",
        total_succeeded,
        total_attempts,
        f"Claude lead sessions ({len(used)} files) + afk logs + context-boundaries.jsonl",
        {
            "session_attempts": attempts,
            "session_succeeded": succeeded,
            "session_unknown": unknown,
            "session_refused": refused,
            "afk_attempts": afk_attempts,
            "afk_succeeded": afk_succeeded,
            "afk_unknown": afk_unknown,
            "resumed_task_session_only": resumed,
            "actions": dict(actions),
            "boundary_actions": dict(boundary_actions),
            "afk_rows": afk_rows_list[:25],
        },
    )


def next_lead_record(records, target, after):
    for record in records:
        if not str(record.get("sender") or "").startswith("dev-lead"):
            continue
        if record.get("target") != target:
            continue
        created = record_created(record)
        if created and created > after:
            return record
    return None


def measure_time_lost(outbox_roots, routing_paths, brief_dirs, report_dirs, since: datetime, until: datetime) -> Measure:
    records = load_outbox(outbox_roots)
    raw_briefs = lead_briefs(records, brief_dirs, since, until)
    reports = worker_reports(records, report_dirs, since, until)
    grouped = {}
    for brief in raw_briefs:
        key = (brief["key"], brief["target"])
        if key not in grouped:
            grouped[key] = dict(brief)
            grouped[key]["last_created"] = brief["created"]
        elif brief["created"] > grouped[key]["last_created"]:
            grouped[key]["last_created"] = brief["created"]
    cases = []
    for brief in sorted(grouped.values(), key=lambda item: item["created"]):
        if any(report_matches(brief, report) for report in reports):
            continue
        same_target = [b for b in grouped.values() if b["target"] == brief["target"] and b["created"] > brief["last_created"]]
        next_time = min((b["created"] for b in same_target), default=None)
        timing = any(
            str(report["sender"]).endswith(str(brief["target"]).split("-")[-1])
            and report["created"] >= brief["created"]
            and (next_time is None or report["created"] < next_time)
            for report in reports
        )
        if timing:
            continue
        if brief["last_created"] >= until - timedelta(hours=2):
            continue
        nxt = next_lead_record(records, brief["target"], brief["last_created"])
        if nxt is None:
            continue
        gap = (record_created(nxt) - brief["last_created"]).total_seconds() / 60
        cases.append(
            {
                "kind": "lost report",
                "brief": brief["key"],
                "target": brief["target"],
                "from": brief["last_created"].isoformat(),
                "to": record_created(nxt).isoformat(),
                "gap_minutes": round(gap, 1),
            }
        )
    for path in map(Path, routing_paths):
        for record in read_jsonl(path):
            if record.get("kind") != "outcome" or record.get("ok"):
                continue
            if not in_window(record.get("ts"), since, until):
                continue
            worker = str(record.get("worker") or "")
            target = f"dev-{worker}" if worker else ""
            if not target:
                continue
            stamp = parse_ts(record.get("ts"))
            nxt = next_lead_record(records, target, stamp)
            if nxt is None:
                continue
            gap = (record_created(nxt) - stamp).total_seconds() / 60
            cases.append(
                {
                    "kind": "failed run",
                    "task": record.get("task_id"),
                    "worker": worker,
                    "from": stamp.isoformat(),
                    "to": record_created(nxt).isoformat(),
                    "gap_minutes": round(gap, 1),
                }
            )
    gaps = [case["gap_minutes"] for case in cases]
    value = f"median {statistics.median(gaps):.1f} min, max {max(gaps):.1f} min over {len(gaps)} cases" if gaps else "no cases"
    return Measure(
        "time_lost",
        value,
        statistics.median(gaps) if gaps else 0,
        len(cases),
        "hx outbox + routing.jsonl",
        {"cases": cases},
    )


def measure_all(options: Options) -> list:
    rows = []
    rows.extend(measure_sends(options.outbox, options.since, options.until))
    rows.append(measure_reports(options.outbox, options.briefs, options.reports, options.since, options.until))
    rows.append(measure_stalls(options.routing, options.boundaries, options.afk, options.outbox, options.since, options.until))
    rows.append(measure_compactions(options.lead_sessions, options.boundaries, options.afk, options.since, options.until))
    rows.append(measure_time_lost(options.outbox, options.routing, options.briefs, options.reports, options.since, options.until))
    return rows


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", required=True, help="first day, inclusive (YYYY-MM-DD)")
    parser.add_argument("--until", required=True, help="last day, exclusive (YYYY-MM-DD)")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--outbox", action="append")
    parser.add_argument("--briefs", action="append")
    parser.add_argument("--reports", action="append")
    parser.add_argument("--routing", action="append")
    parser.add_argument("--afk", action="append")
    parser.add_argument("--boundaries", action="append")
    parser.add_argument("--lead-session", action="append")
    return parser


def options_from_args(args) -> Options:
    since = parse_day(args.since)
    until = parse_day(args.until)
    if until <= since:
        raise SystemExit("--until must be after --since")
    default_leads = [CLAUDE_PROJECTS / "C--Users-caspe" / f"{sid}.jsonl" for sid in LEAD_SESSION_IDS]
    return Options(
        since,
        until,
        [Path(p) for p in args.outbox] if args.outbox else list(DEFAULT_OUTBOX),
        [Path(p) for p in args.briefs] if args.briefs else list(DEFAULT_BRIEFS),
        [Path(p) for p in args.reports] if args.reports else list(DEFAULT_REPORTS),
        [Path(p) for p in args.routing] if args.routing else list(DEFAULT_ROUTING),
        [Path(p) for p in args.afk] if args.afk else list(DEFAULT_AFK),
        [Path(p) for p in args.boundaries] if args.boundaries else list(DEFAULT_BOUNDARIES),
        [Path(p) for p in args.lead_session] if args.lead_session else default_leads,
    )


def render_text(rows, since: datetime, until: datetime) -> str:
    days = (until - since).days
    lines = [
        f"window {since:%Y-%m-%d} -> {until:%Y-%m-%d} ({days} days, until exclusive)",
        "",
        f"{'measure':<26} {'value':<44} {'numerator':>9} {'denominator':>11}  source",
    ]
    for row in rows:
        lines.append(
            f"{row.name:<26} {row.value:<44} {row.numerator:>9g} "
            f"{row.denominator:>11g}  {row.source}"
        )
    for row in rows:
        if row.detail:
            lines.append("")
            lines.append(f"{row.name} detail: {json.dumps(row.detail, ensure_ascii=False, sort_keys=True)}")
    return "\n".join(lines)


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    options = options_from_args(args)
    rows = measure_all(options)
    if args.json:
        print(
            json.dumps(
                {
                    "window": {"since": args.since, "until": args.until, "untilExclusive": True},
                    "measures": [row.as_json() for row in rows],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(render_text(rows, options.since, options.until))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())