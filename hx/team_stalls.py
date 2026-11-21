"""hx team stalls: one alert when a working teammate's screen freezes.

A worker is stalled when its status is `working` and its normalised screen
has not changed for `policy.stall_minutes` (default 30). The normalised
screen removes ANSI, spinner glyphs, and every run of digits, so a ticking
timer or token counter is not progress. Idle, done and blocked workers are
never stalled. The check is one shot: a scheduled task runs it every ten
minutes, and the alert goes to the lead once per stall. It reads the Herdr
app through `hc`, so it works from a scheduled task with no terminal
identity. Every run appends one status line to `stalls.log`; a failed alert
is logged with its reason.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
import re
import time
from pathlib import Path
from typing import Any

from hx import host, paths, usage

DEFAULT_STALL_MINUTES = 30
SCREEN_LINES = 240
STATE_VERSION = 1
LAST_LINE_CHARS = 160

# Spinner/frame glyphs providers rotate while busy. Braille covers the
# common spinner, the rest are the frame sets seen in Herdr panes.
SPINNER_GLYPHS = frozenset(
    "⠁⠂⠄⡀⢀⠠⠐⠈⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    "⣾⣽⣻⢿⡿⣟⣯⣷"
    "◐◓◑◒◜◝◞◟"
    "✶✳✢✻✽"
    "⏳⌛"
)
_ANSI = re.compile(
    r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\)|[@-_])"
)
_DIGITS = re.compile(r"[0-9]+")
# Devin animates two-cell Braille frames from the whole block (21 Nov freeze:
# "⢰⠀" then "⠤⠄"), and OpenCode animates a bar of ⬝ and ■.
_BAR_GLYPHS = frozenset("⬝■□▪▫")


def _is_spinner(char: str) -> bool:
    return char in SPINNER_GLYPHS or char in _BAR_GLYPHS or "⠀" <= char <= "⣿"
_SPACES = re.compile(r"[ \t]+")


def normalise(screen: str) -> str:
    """The screen without ANSI, spinner glyphs, digit runs or trailing space."""
    text = _ANSI.sub("", screen or "")
    text = "".join(char for char in text if not _is_spinner(char))
    text = _DIGITS.sub("#", text)
    lines = [_SPACES.sub(" ", line).rstrip() for line in text.splitlines()]
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def digest(screen: str) -> str:
    return hashlib.sha256(normalise(screen).encode("utf-8")).hexdigest()


def last_line(screen: str) -> str:
    for line in reversed((screen or "").splitlines()):
        clean = _SPACES.sub(" ", _ANSI.sub("", line)).strip()
        if clean:
            return clean[:LAST_LINE_CHARS]
    return ""


def _threshold(policy: dict[str, Any]) -> float:
    value = policy.get("stall_minutes", DEFAULT_STALL_MINUTES)
    if isinstance(value, bool) or not isinstance(value, (int, float)) \
            or not math.isfinite(value) or value < 0:
        return float(DEFAULT_STALL_MINUTES)
    return float(value)


def _prefix(team: str | None, policy: dict[str, Any]) -> str:
    prefix = str(team if team is not None else usage.team_prefix(policy))
    return prefix if prefix.endswith("-") else prefix + "-"


def _live_prefix(detected: host.Host, team: str | None,
                 policy: dict[str, Any]) -> str:
    """The team to watch: the flag, this pane's team, else the one live lead team."""
    if team:
        return _prefix(team, policy)
    prefix = _prefix(None, policy)
    try:
        if detected.team(prefix):
            return prefix
    except Exception:
        return prefix
    try:
        members = detected.all_members()
    except Exception:
        return prefix
    lead_role = str(policy.get("lead_role", "lead"))
    teams = {
        str(member.get("team")) for member in members
        if member.get("role") == lead_role and member.get("team")
    }
    if len(teams) == 1:
        found = teams.pop()
        return found if found.endswith("-") else found + "-"
    return prefix


def _state_path() -> Path:
    return paths.hx_dir() / "team-stalls.json"


def _log_path() -> Path:
    return paths.hx_dir() / "stalls.log"


def _load_state(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"version": STATE_VERSION, "workers": {}}
    if not isinstance(data, dict):
        return {"version": STATE_VERSION, "workers": {}}
    workers = data.get("workers")
    return {"version": STATE_VERSION,
            "workers": workers if isinstance(workers, dict) else {}}


def _save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _append_log(path: Path, entry: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")


def _timestamp(clock: float) -> str:
    return dt.datetime.fromtimestamp(clock, dt.timezone.utc).isoformat()


def _run_record(started: float, status: str, team: str | None, dry_run: bool,
                rows: list[dict[str, Any]] | None = None,
                error: str | None = None) -> dict[str, Any]:
    record: dict[str, Any] = {
        "at": _timestamp(started), "event": "run", "status": status,
        "team": team, "dry_run": dry_run,
    }
    if rows is not None:
        named = [row for row in rows if "name" in row]
        record.update({
            "workers": len(named),
            "stalled": sum(1 for row in named if row.get("stalled")),
            "alerts": sum(1 for row in named
                          if row.get("alert") in ("sent", "would-alert")),
        })
    if error:
        record["error"] = error
    return record


def _alert_text(name: str, minutes: float, line: str) -> str:
    text = (f"[hx] stall: {name} has been working for {minutes:.0f}m "
            f"without a screen change. Last line: {line or '(none)'}")
    return _SPACES.sub(" ", text.replace("\n", " ")).strip()


def _send_alert(detected: host.Host, lead: str, text: str) -> tuple[bool, str | None]:
    try:
        result = detected.send_checked(lead, text)
    except Exception as exc:
        return False, str(exc)
    if not isinstance(result, dict):
        return False, "malformed send result"
    started = result.get("started")
    status = str(result.get("status") or "")
    if started is False and status in ("blocked", "error"):
        return False, status or "refused"
    return True, status or None


def check(
    detected: host.Host,
    *,
    team: str | None = None,
    policy: dict[str, Any] | None = None,
    now: float | None = None,
    state_path: Path | None = None,
    log_path: Path | None = None,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    """One stall pass over the live team. Returns one row per non-lead worker."""
    policy = usage.load_policy() if policy is None else policy
    prefix = _live_prefix(detected, team, policy)
    lead = usage.lead_name(policy, prefix)
    threshold = _threshold(policy)
    clock = time.time() if now is None else float(now)
    state_file = Path(state_path) if state_path is not None else _state_path()
    log_file = Path(log_path) if log_path is not None else _log_path()
    previous = _load_state(state_file).get("workers") or {}
    workers: dict[str, Any] = {}
    rows: list[dict[str, Any]] = []

    for name, member in sorted(detected.team(prefix).items()):
        if name == lead:
            continue
        status = str(member.get("status") or "unknown")
        try:
            screen = str(detected.read(name, SCREEN_LINES))
        except Exception:
            screen = None
        last = last_line(screen or "")
        row: dict[str, Any] = {"name": name, "status": status, "last_line": last}
        if status != "working" or screen is None:
            row.update({"stalled": False, "minutes": 0.0, "alerted": False,
                        "note": "screen unreadable" if screen is None else "not working"})
            rows.append(row)
            continue
        current = digest(screen)
        entry = previous.get(name)
        session = member.get("session_id")
        created = member.get("created_ms")
        turn = member.get("turn")
        restarted = bool(entry) and (
            (entry.get("session_id") is not None and session is not None
             and entry.get("session_id") != session)
            or (entry.get("created_ms") is not None and created is not None
                and entry.get("created_ms") != created)
            or (entry.get("turn") is not None and turn is not None
                and entry.get("turn") != turn)
        )
        if entry is None or restarted or entry.get("hash") != current:
            entry = {"hash": current, "since": clock, "alerted": False,
                     "alerted_at": None, "status": status,
                     "session_id": session, "created_ms": created, "turn": turn}
            workers[name] = entry
            row.update({"stalled": False, "minutes": 0.0, "alerted": False,
                        "note": "restarted" if restarted else "new screen"})
            rows.append(row)
            continue
        entry.update({"status": status, "session_id": session,
                      "created_ms": created, "turn": turn})
        since = entry.get("since")
        minutes = 0.0
        if isinstance(since, (int, float)) and not isinstance(since, bool):
            minutes = max(0.0, (clock - float(since)) / 60.0)
        stalled = minutes >= threshold
        alert: str | None = None
        if stalled and not entry.get("alerted"):
            if dry_run:
                alert = "would-alert"
            else:
                sent, reason = _send_alert(
                    detected, lead, _alert_text(name, minutes, last))
                if sent:
                    entry["alerted"] = True
                    entry["alerted_at"] = clock
                    _append_log(log_file, {
                        "at": _timestamp(clock), "event": "alert",
                        "worker": name, "minutes": round(minutes, 1),
                        "last_line": last,
                    })
                    alert = "sent"
                else:
                    alert = "pending"
                    row["send_reason"] = reason or "unknown"
                    _append_log(log_file, {
                        "at": _timestamp(clock), "event": "send_failed",
                        "worker": name, "minutes": round(minutes, 1),
                        "reason": row["send_reason"], "last_line": last,
                    })
        elif stalled:
            alert = "already"
        workers[name] = entry
        row.update({"stalled": stalled, "minutes": round(minutes, 1),
                    "alerted": bool(entry.get("alerted")), "alert": alert})
        rows.append(row)

    if not dry_run:
        _save_state(state_file, {"version": STATE_VERSION, "workers": workers})
    if not rows:
        rows.append({"team": prefix, "workers": 0, "note": "no live workers"})
    return rows


def run(team: str | None = None, *, dry_run: bool = False,
        state_path: Path | None = None, log_path: Path | None = None) -> int:
    log_file = Path(log_path) if log_path is not None else _log_path()
    started = time.time()
    try:
        detected = host.HcHost()
        policy = usage.load_policy()
        rows = check(detected, team=team, policy=policy,
                     state_path=state_path, log_path=log_file,
                     dry_run=dry_run)
        for row in rows:
            print(json.dumps(row))
        _append_log(log_file, _run_record(started, "ok", team, dry_run, rows))
        return 0
    except Exception as exc:
        _append_log(log_file, _run_record(
            started, "error", team, dry_run, error=str(exc)))
        print(json.dumps({"error": str(exc)}))
        return 1
