"""hx reconcile: settle send records against the receiver's own transcript.

Read-only. A record is delivered when the receiver's log holds the message
text near the send time, lost when the receiver's log covers the send time and
does not, and unconfirmed when no log covers that time. Nothing here sends,
writes a receipt, or touches the outbox.
"""
from __future__ import annotations

import datetime as dt
import glob
import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hx import paths, usage

DATE_FORMAT = "%Y-%m-%d"
STATUSES = ("delivered", "lost", "unconfirmed")
ROLE_FAMILY = {"claude": "claude", "codex": "codex", "opencode": "opencode", "devin": "devin"}
# The stated figure this unit is measured against (Q-1118): briefs with no matching reply.
STATED_UPPER_BOUND = "128 of 316"
# A message lands in the transcript when it is submitted, so a match much later
# is the receiver quoting it, not delivery. The slack absorbs queueing.
MATCH_BEFORE = dt.timedelta(minutes=5)
MATCH_AFTER = dt.timedelta(hours=2)
# A session created by a just-arrived message puts its first event shortly after the send.
COVER_SLACK = dt.timedelta(minutes=2)
SEARCH_AFTER = dt.timedelta(minutes=10)
SEARCH_BEFORE = dt.timedelta(minutes=1)
# A prefix this long identifies a message whose tail the TUI shortened or rewrapped.
FRAGMENT_CHARS = 160

Entry = tuple[dt.datetime | None, str]


@dataclass(frozen=True)
class SessionLog:
    family: str
    session_id: str
    path: str
    first: dt.datetime | None
    last: dt.datetime | None
    entries: tuple[Entry, ...]


@dataclass(frozen=True)
class Finding:
    status: str
    evidence: str | None
    evidence_at: dt.datetime | None = None


def _squash(text: Any) -> str:
    return "".join(str(text).split())


def _timestamp(value: Any) -> dt.datetime | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 1e11 else value
        try:
            return dt.datetime.fromtimestamp(seconds, dt.timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return parsed if parsed.tzinfo else None


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        out: list[str] = []
        for item in value.values():
            out.extend(_strings(item))
        return out
    if isinstance(value, list):
        out = []
        for item in value:
            out.extend(_strings(item))
        return out
    return []


def _span(stamps: list[dt.datetime]) -> tuple[dt.datetime | None, dt.datetime | None]:
    return (min(stamps), max(stamps)) if stamps else (None, None)


def _log(family: str, session_id: str, path: str, stamps: list[dt.datetime],
         entries: list[Entry]) -> SessionLog | None:
    first, last = _span(stamps)
    if first is None:
        return None
    return SessionLog(family, session_id, path, first, last, tuple(entries))


def claude_logs(projects_dir: Path) -> list[SessionLog]:
    logs = []
    for name in sorted(glob.glob(str(projects_dir / "*" / "*.jsonl"))):
        path = Path(name)
        stamps: list[dt.datetime] = []
        entries: list[Entry] = []
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(event, dict):
                        continue
                    stamp = _timestamp(event.get("timestamp"))
                    if stamp:
                        stamps.append(stamp)
                    if event.get("type") != "user":
                        continue
                    content = (event.get("message") or {}).get("content")
                    text = ""
                    if isinstance(content, str):
                        text = content
                    elif isinstance(content, list):
                        text = "".join(block.get("text") or "" for block in content
                                       if isinstance(block, dict) and block.get("type") == "text")
                    if text:
                        entries.append((stamp, _squash(text)))
        except OSError:
            continue
        log = _log("claude", path.stem, str(path), stamps, entries)
        if log is not None:
            logs.append(log)
    return logs


def codex_logs(sessions_dir: Path) -> list[SessionLog]:
    logs = []
    for name in sorted(glob.glob(str(sessions_dir / "**" / "rollout-*.jsonl"), recursive=True)):
        path = Path(name)
        stamps: list[dt.datetime] = []
        entries: list[Entry] = []
        session_id = ""
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(event, dict):
                        continue
                    stamp = _timestamp(event.get("timestamp"))
                    if stamp:
                        stamps.append(stamp)
                    payload = event.get("payload")
                    if not isinstance(payload, dict):
                        continue
                    text = ""
                    if event.get("type") == "session_meta" and not session_id:
                        session_id = str(payload.get("id") or "")
                    elif event.get("type") == "response_item" and payload.get("role") == "user":
                        text = "".join(_strings(payload.get("content")))
                    elif event.get("type") == "event_msg" and payload.get("type") == "user_message":
                        message = payload.get("message")
                        text = message if isinstance(message, str) else ""
                    if text:
                        entries.append((stamp, _squash(text)))
        except OSError:
            continue
        log = _log("codex", session_id or path.stem, str(path), stamps, entries)
        if log is not None:
            logs.append(log)
    return logs


def opencode_logs(db_path: Path, since: dt.datetime | None, until: dt.datetime | None) -> list[SessionLog]:
    if not db_path.exists():
        return []
    try:
        conn = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
    except sqlite3.Error:
        return []
    logs = []
    try:
        sessions = conn.execute("SELECT id, time_created, time_updated FROM session").fetchall()
        for session in sessions:
            session_id = str(session["id"])
            messages = conn.execute(
                "SELECT id, time_created, data FROM message WHERE session_id=? ORDER BY time_created",
                (session_id,)).fetchall()
            stamps = [stamp for stamp in (_timestamp(row["time_created"]) for row in messages) if stamp]
            if not stamps:
                stamps = [stamp for stamp in (
                    _timestamp(session["time_created"]), _timestamp(session["time_updated"])) if stamp]
            first, last = _span(stamps)
            if first is None:
                continue
            if since and last < since - COVER_SLACK:
                continue
            if until and first > until + COVER_SLACK:
                continue
            roles = {}
            for row in messages:
                try:
                    roles[str(row["id"])] = json.loads(row["data"]).get("role")
                except (ValueError, TypeError):
                    continue
            entries: list[Entry] = []
            for part in conn.execute(
                    "SELECT message_id, time_created, data FROM part WHERE session_id=? ORDER BY time_created",
                    (session_id,)):
                if roles.get(str(part["message_id"])) != "user":
                    continue
                try:
                    data = json.loads(part["data"])
                except ValueError:
                    continue
                if isinstance(data, dict) and data.get("type") == "text" and isinstance(data.get("text"), str):
                    entries.append((_timestamp(part["time_created"]), _squash(data["text"])))
            log = _log("opencode", session_id, f"{db_path}#{session_id}", stamps, entries)
            if log is not None:
                logs.append(log)
    except sqlite3.Error:
        return logs
    finally:
        conn.close()
    return logs


def devin_logs(cli_dir: Path) -> list[SessionLog]:
    logs = []
    for name in sorted(glob.glob(str(cli_dir / "transcripts" / "*.json"))):
        path = Path(name)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        steps = data.get("steps")
        if not isinstance(steps, list):
            continue
        stamps: list[dt.datetime] = []
        entries: list[Entry] = []
        for step in steps:
            if not isinstance(step, dict):
                continue
            stamp = _timestamp(step.get("timestamp"))
            if stamp:
                stamps.append(stamp)
            if step.get("source") == "user" and isinstance(step.get("message"), str):
                entries.append((stamp, _squash(step["message"])))
        log = _log("devin", str(data.get("session_id") or path.stem), str(path), stamps, entries)
        if log is not None:
            logs.append(log)
    return logs


def default_roots() -> dict[str, Path]:
    appdata = os.environ.get("APPDATA")
    devin = (Path(appdata) / "devin" / "cli" if appdata else Path.home() / ".devin" / "cli")
    return {
        "claude": Path(os.environ.get("HX_CLAUDE_PROJECTS_DIR", Path.home() / ".claude" / "projects")),
        "codex": usage.default_codex_sessions_dir(),
        "opencode": usage.default_opencode_db_path(),
        "devin": Path(os.environ.get("HX_DEVIN_CLI_DIR", devin)),
    }


def load_logs(roots: dict[str, Path], since: dt.datetime | None = None,
              until: dt.datetime | None = None) -> list[SessionLog]:
    return (claude_logs(roots["claude"]) + codex_logs(roots["codex"])
            + opencode_logs(roots["opencode"], since, until) + devin_logs(roots["devin"]))


def load_records(hx_dir: Path, since: dt.datetime | None = None,
                 until: dt.datetime | None = None) -> list[dict[str, Any]]:
    records = []
    for name in sorted((hx_dir / "outbox").glob("*/*.json")):
        try:
            data = json.loads(name.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        sent = _timestamp(data.get("created_at"))
        if sent is None:
            try:
                sent = dt.datetime.fromtimestamp(name.stat().st_mtime, dt.timezone.utc)
            except OSError:
                continue
        if since and sent < since:
            continue
        if until and sent > until:
            continue
        data["_sent"] = sent
        data["_path"] = str(name)
        records.append(data)
    return records


def _needles(record: dict[str, Any]) -> list[str]:
    out = []
    for key in ("text", "body"):
        value = record.get(key)
        if isinstance(value, str) and value:
            squashed = _squash(value)
            if squashed and squashed not in out:
                out.append(squashed)
    return out


def _entry_matches(text: str, needles: list[str]) -> bool:
    if not text:
        return False
    for needle in needles:
        if needle in text:
            return True
        if len(needle) >= FRAGMENT_CHARS + 40 and needle[:FRAGMENT_CHARS] in text:
            return True
    return False


def _find(log: SessionLog, needles: list[str], sent: dt.datetime) -> dt.datetime | None:
    """The timestamp of the entry that holds the message, within the delivery window."""
    for stamp, text in log.entries:
        if not _entry_matches(text, needles):
            continue
        if stamp is None or sent - MATCH_BEFORE <= stamp <= sent + MATCH_AFTER:
            return stamp
    return None


def _near(log: SessionLog, sent: dt.datetime) -> bool:
    """A log whose span could hold an event sent at this time."""
    return (log.first is not None and log.last is not None
            and log.first <= sent + SEARCH_AFTER and log.last >= sent - SEARCH_BEFORE)


def _covers(log: SessionLog, sent: dt.datetime) -> bool:
    """The log spans the send time and holds user text, so absence is evidence.

    A transcript with no user turns at all (an auto-started Devin session, a
    pruned log) cannot prove that a message was absent.
    """
    return (bool(log.entries) and log.first is not None and log.last is not None
            and log.first <= sent + COVER_SLACK and log.last >= sent - COVER_SLACK)


def role_family(target: str) -> str | None:
    return ROLE_FAMILY.get(target.rsplit("-", 1)[-1])


def infer_families(records: list[dict[str, Any]], logs: list[SessionLog]) -> dict[str, str]:
    """Resolve roles whose name is not a family (the lead seat) from what they received.

    A message addressed to an agent lands in that agent's transcript, so the
    family holding most of a role's inbound send text is that role's family.
    This is the only local evidence of the lead seat's family, and it catches
    a seat that moved between families.
    """
    hits: dict[str, dict[str, int]] = {}
    for record in records:
        target = str(record.get("target") or "")
        if not target or role_family(target) is not None:
            continue
        sent = record["_sent"]
        needles = _needles(record)
        for log in logs:
            if _near(log, sent) and _find(log, needles, sent) is not None:
                counts = hits.setdefault(target, {})
                counts[log.family] = counts.get(log.family, 0) + 1
    return {target: max(counts.items(), key=lambda item: item[1])[0]
            for target, counts in hits.items()}


def classify(record: dict[str, Any], family: str | None,
             logs: list[SessionLog]) -> Finding:
    sent = record["_sent"]
    needles = _needles(record)
    # Another family holding the text proves the text exists, not that this
    # receiver got it; only the receiver's family can settle the record.
    pool = [log for log in logs if family is None or log.family == family]
    near = [log for log in pool if _near(log, sent)]
    for log in near:
        stamp = _find(log, needles, sent)
        if stamp is not None:
            return Finding("delivered", log.path, stamp)
    if family is None:
        return Finding("unconfirmed", None)
    # A delivered receipt is positive transport evidence, so a missing text is
    # a disagreement between sources, never confirmed loss. Pending and failed
    # records were never submitted either, so only an uncertain attempt is lost.
    if record.get("state") == "uncertain" and any(_covers(log, sent) for log in near):
        return Finding("lost", None)
    return Finding("unconfirmed", None)


def reconcile(records: list[dict[str, Any]], logs: list[SessionLog]) -> dict[str, Any]:
    inferred = infer_families(records, logs)
    families: dict[str, str | None] = {}
    counts = {status: 0 for status in STATUSES}
    by_receiver: dict[str, dict[str, int]] = {}
    findings = []
    excluded = 0
    receipt_only = 0
    for record in records:
        if record.get("state") == "cancelled":
            excluded += 1
            continue
        target = str(record.get("target") or "")
        family = role_family(target) or inferred.get(target)
        families[target] = family
        finding = classify(record, family, logs)
        counts[finding.status] += 1
        if finding.status == "unconfirmed" and record.get("state") == "delivered":
            receipt_only += 1
        bucket = by_receiver.setdefault(target, {status: 0 for status in STATUSES})
        bucket[finding.status] += 1
        findings.append({
            "id": record.get("id") or Path(str(record.get("_path") or "")).stem,
            "sender": record.get("sender"),
            "target": target,
            "created_at": record.get("created_at"),
            "state": record.get("state"),
            "family": family,
            "status": finding.status,
            "evidence": finding.evidence,
            "evidence_at": finding.evidence_at.isoformat() if finding.evidence_at else None,
        })
    briefs = [item for item in findings if str(item.get("sender") or "").rsplit("-", 1)[-1] == "lead"]
    lost_briefs = sum(1 for item in briefs if item["status"] == "lost")
    return {
        "records": len(findings),
        "excluded": excluded,
        "receipt_only": receipt_only,
        "counts": counts,
        "by_receiver": by_receiver,
        "families": families,
        "inferred": inferred,
        "briefs": len(briefs),
        "lost_briefs": lost_briefs,
        "upper_bound": STATED_UPPER_BOUND,
        "findings": findings,
    }


def render_text(summary: dict[str, Any], since: str | None, until: str | None) -> str:
    window = f" {since or 'start'}..{until or 'now'}"
    lines = [f"hx reconcile{window}: {summary['records']} send records",
             "  delivered {delivered}  lost {lost}  unconfirmed {unconfirmed}".format(**summary["counts"])]
    for target in sorted(summary["by_receiver"]):
        row = summary["by_receiver"][target]
        lines.append(f"  {target:14} delivered {row['delivered']:4}  lost {row['lost']:4}  "
                     f"unconfirmed {row['unconfirmed']:4}  family {summary['families'].get(target) or '-'}")
    lines.append(f"  {summary['excluded']} cancelled records excluded (never sent); "
                 f"{summary['receipt_only']} delivered receipts missing from the transcript")
    lines.append(f"  upper bound {summary['upper_bound']} briefs with no matching reply; "
                 f"confirmed lost {summary['lost_briefs']} of {summary['briefs']} briefs "
                 f"({summary['counts']['lost']} of {summary['records']} send records)")
    return "\n".join(lines)


def command(since: str | None = None, until: str | None = None, as_json: bool = False) -> int:
    try:
        since_at = (dt.datetime.strptime(since, DATE_FORMAT).replace(tzinfo=dt.timezone.utc)
                    if since else None)
        until_at = (dt.datetime.strptime(until, DATE_FORMAT).replace(tzinfo=dt.timezone.utc)
                    + dt.timedelta(days=1) - dt.timedelta(microseconds=1) if until else None)
    except ValueError as exc:
        raise ValueError(f"dates must be YYYY-MM-DD: {exc}") from exc
    if since_at and until_at and since_at > until_at:
        raise ValueError("--since must not be after --until")
    records = load_records(paths.hx_dir(), since_at, until_at)
    logs = load_logs(default_roots(), since_at, until_at)
    summary = reconcile(records, logs)
    if as_json:
        print(json.dumps(summary, indent=2))
    else:
        print(render_text(summary, since, until))
    return 0
