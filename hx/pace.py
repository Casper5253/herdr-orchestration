"""hx.pace: proactive pool pacing from the usage snapshot.

The usage snapshot is the measurement (hx.usage). This module only derives the
used share, the elapsed share of each window and the pace (used minus elapsed),
then a pool status the dispatcher reads. Data shape:

  report = {
    "generated_at": iso8601,
    "policy": {"under": float, "over": float, "reset_soon": float},
    "line": "pace: ...",
    "pools": {
      "claude": {
        "pool": "claude", "label": "Claude", "source": tag,
        "status": "underused" | "ahead" | "on_pace" | "free" | "unknown",
        "reason": str,
        "windows": [
          {"name": "5h", "used": float | None, "elapsed": float | None,
           "pace": float | None, "reset_at": iso8601 | None,
           "remaining_seconds": int | None, "reset_soon": bool,
           "status": "underused" | "ahead" | "on_pace" | "unknown"},
        ],
      },
      ...
    },
  }

used and elapsed are shares in [0, 1]. A window is underused when it is far
below pace and its reset is near, ahead when it is far above pace, and unknown
when the snapshot cannot support the comparison. Unknown is never treated as
exhausted. A free pool (an active Devin promotion) is reported as free.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

from hx import usage

POOL_ORDER = ("claude", "codex", "opencode", "devin")
POOL_LABELS = {
    "claude": "Claude",
    "codex": "Codex",
    "opencode": "OpenCode Go",
    "devin": "Devin",
}
DEFAULT_UNDER = 0.10
DEFAULT_OVER = 0.10
DEFAULT_RESET_SOON = 0.50
FIVE_HOURS = 5 * 3600
SEVEN_DAYS = 7 * 24 * 3600
THIRTY_DAYS = 30 * 24 * 3600
WINDOW_SECONDS = {
    "claude": {"5h": FIVE_HOURS, "7d": SEVEN_DAYS},
    "codex": {"5h": FIVE_HOURS, "7d": SEVEN_DAYS},
    "opencode": {"5h": FIVE_HOURS, "7d": SEVEN_DAYS, "30d": THIRTY_DAYS},
}
CODEX_WINDOWS = (("primary", "5h", 300), ("secondary", "7d", 10080))
CLAUDE_WINDOWS = (("five_hour", "5h"), ("seven_day", "7d"))
OPENCODE_WINDOWS = ("5h", "7d", "30d")


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _instant(value: Any) -> dt.datetime | None:
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return dt.datetime.fromtimestamp(float(value), dt.timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)


def _share(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return max(0.0, min(1.0, float(value)))


def _percent(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) / 100.0


def _minutes_seconds(value: Any, fallback: int) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return float(value) * 60.0
    return float(fallback) * 60.0


def knobs(policy: dict[str, Any] | None) -> dict[str, float]:
    """The pace thresholds: under, over and reset_soon."""
    conf = (policy or {}).get("pace") or {}
    result: dict[str, float] = {}
    for name, default in (
        ("under", DEFAULT_UNDER),
        ("over", DEFAULT_OVER),
        ("reset_soon", DEFAULT_RESET_SOON),
    ):
        value = conf.get(name, default)
        result[name] = (
            float(value)
            if isinstance(value, (int, float)) and not isinstance(value, bool)
            else default
        )
    return result


def _window(
    name: str,
    used: Any,
    reset_at: Any,
    length_seconds: float,
    now: dt.datetime,
    thresholds: dict[str, float],
) -> dict[str, Any]:
    used_share = _share(used)
    reset = _instant(reset_at)
    remaining: float | None = None
    elapsed: float | None = None
    if reset is not None:
        remaining = max(0.0, (reset - now).total_seconds())
        if length_seconds > 0:
            elapsed = max(0.0, min(1.0, 1.0 - remaining / length_seconds))
    pace = (
        used_share - elapsed
        if used_share is not None and elapsed is not None
        else None
    )
    reset_soon = (
        remaining is not None
        and length_seconds > 0
        and remaining <= length_seconds * thresholds["reset_soon"]
    )
    if pace is None:
        status = "unknown"
    elif pace <= -thresholds["under"] and reset_soon:
        status = "underused"
    elif pace >= thresholds["over"]:
        status = "ahead"
    else:
        status = "on_pace"
    return {
        "name": name,
        "used": used_share,
        "elapsed": elapsed,
        "pace": pace,
        "reset_at": reset.isoformat() if reset is not None else None,
        "remaining_seconds": int(remaining) if remaining is not None else None,
        "reset_soon": bool(reset_soon),
        "status": status,
    }


def _windows(
    snapshot: dict[str, Any],
    pool: str,
    now: dt.datetime,
    thresholds: dict[str, float],
) -> list[dict[str, Any]]:
    if pool == "codex":
        limits = (snapshot.get("codex") or {}).get("rate_limits") or {}
        rows = []
        for key, name, minutes in CODEX_WINDOWS:
            win = limits.get(key)
            if not isinstance(win, dict):
                continue
            rows.append(
                _window(
                    name,
                    _percent(win.get("used_percent")),
                    win.get("resets_at"),
                    _minutes_seconds(win.get("window_minutes"), minutes),
                    now,
                    thresholds,
                )
            )
        return rows
    if pool == "claude":
        limits = (snapshot.get("claude") or {}).get("rate_limits") or {}
        rows = []
        for key, name in CLAUDE_WINDOWS:
            win = limits.get(key)
            if not isinstance(win, dict):
                continue
            reset = win.get("resets_at") or win.get("reset_at")
            if reset is None:
                seconds = win.get("resets_in_seconds")
                if (
                    isinstance(seconds, (int, float))
                    and not isinstance(seconds, bool)
                    and seconds >= 0
                ):
                    reset = now + dt.timedelta(seconds=float(seconds))
            rows.append(
                _window(
                    name,
                    _percent(win.get("used_percentage")),
                    reset,
                    WINDOW_SECONDS["claude"][name],
                    now,
                    thresholds,
                )
            )
        return rows
    if pool == "opencode":
        windows = ((snapshot.get("opencode") or {}).get("pool") or {}).get("windows") or {}
        rows = []
        for name in OPENCODE_WINDOWS:
            win = windows.get(name)
            if not isinstance(win, dict):
                continue
            rows.append(
                _window(
                    name,
                    win.get("utilisation"),
                    win.get("resets_at"),
                    WINDOW_SECONDS["opencode"][name],
                    now,
                    thresholds,
                )
            )
        return rows
    return []


def _reason(status: str, windows: list[dict[str, Any]]) -> str:
    if status == "underused":
        names = ", ".join(w["name"] for w in windows if w["status"] == "underused")
        return f"underused on {names}: give it work"
    if status == "ahead":
        names = ", ".join(w["name"] for w in windows if w["status"] == "ahead")
        return f"ahead on {names}: hold back"
    if status == "on_pace":
        return "on pace"
    return "usage unknown; not treated as exhausted"


def _pool(
    name: str,
    windows: list[dict[str, Any]],
    source: Any,
) -> dict[str, Any]:
    statuses = [window["status"] for window in windows]
    if "ahead" in statuses:
        status = "ahead"
    elif "underused" in statuses:
        status = "underused"
    elif "on_pace" in statuses:
        status = "on_pace"
    else:
        status = "unknown"
    return {
        "pool": name,
        "label": POOL_LABELS[name],
        "source": source,
        "status": status,
        "reason": _reason(status, windows),
        "windows": windows,
    }


def _devin(snapshot: dict[str, Any]) -> dict[str, Any]:
    block = snapshot.get("devin") or {}
    promo = block.get("promo") or {}
    if promo.get("active"):
        return {
            "pool": "devin",
            "label": POOL_LABELS["devin"],
            "source": block.get("tag"),
            "status": "free",
            "reason": "free SWE-2.1 promotion active",
            "windows": [],
        }
    if not block or block.get("tag") == "unknown":
        return {
            "pool": "devin",
            "label": POOL_LABELS["devin"],
            "source": block.get("tag"),
            "status": "unknown",
            "reason": "usage unknown; not treated as exhausted",
            "windows": [],
        }
    return {
        "pool": "devin",
        "label": POOL_LABELS["devin"],
        "source": block.get("tag"),
        "status": "on_pace",
        "reason": "no free promotion; outside the quota pools",
        "windows": [],
    }


def _best_window(
    windows: list[dict[str, Any]], status: str
) -> dict[str, Any] | None:
    scored = [
        window
        for window in windows
        if window["status"] == status and window["pace"] is not None
    ]
    if not scored:
        return None
    if status == "underused":
        return min(scored, key=lambda window: window["pace"])
    if status == "ahead":
        return max(scored, key=lambda window: window["pace"])
    return max(scored, key=lambda window: abs(window["pace"]))


def _pct(value: Any) -> str:
    return f"{round(float(value) * 100)}%"


def format_line(report: dict[str, Any]) -> str:
    chunks = []
    for name in POOL_ORDER:
        pool = (report.get("pools") or {}).get(name) or {}
        status = str(pool.get("status") or "unknown")
        if status == "free":
            chunks.append(f"{name} free")
            continue
        if status == "unknown":
            chunks.append(f"{name} unknown")
            continue
        best = _best_window(pool.get("windows") or [], status)
        if best is None:
            chunks.append(f"{name} {status}")
            continue
        chunks.append(
            f"{name} {status} ({best['name']} {_pct(best['used'])} used, "
            f"{_pct(best['elapsed'])} elapsed)"
        )
    return "pace: " + "; ".join(chunks)


def build(
    snapshot: dict[str, Any],
    policy: dict[str, Any] | None = None,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    """The pace report from one usage snapshot; no measurement here."""
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    now = now or _instant(snapshot.get("timestamp")) or _utcnow()
    thresholds = knobs(policy)
    pools = {}
    for name in ("claude", "codex", "opencode"):
        windows = _windows(snapshot, name, now, thresholds)
        source = (snapshot.get(name) or {}).get("tag")
        pools[name] = _pool(name, windows, source)
    pools["devin"] = _devin(snapshot)
    report = {
        "generated_at": now.isoformat(),
        "policy": thresholds,
        "pools": pools,
    }
    report["line"] = format_line(report)
    return report


def pool_for_worker(worker: Any) -> str | None:
    role = str(worker or "").rsplit("-", 1)[-1].lower()
    if role.startswith("devin"):
        return "devin"
    return role if role in ("claude", "codex", "opencode") else None


def status_for(report: dict[str, Any] | None, worker: Any) -> str:
    pool = pool_for_worker(worker)
    if not report or not pool:
        return "on_pace"
    entry = (report.get("pools") or {}).get(pool) or {}
    return str(entry.get("status") or "unknown")


def reason_for(report: dict[str, Any] | None, worker: Any) -> str:
    pool = pool_for_worker(worker)
    if not report or not pool:
        return ""
    entry = (report.get("pools") or {}).get(pool) or {}
    return str(entry.get("reason") or "")


def command(as_json: bool = False) -> int:
    policy = usage.load_policy()
    report = build(usage.usage_snapshot(policy), policy)
    print(json.dumps(report, indent=2, sort_keys=True) if as_json else report["line"])
    return 0
