"""Claude status line hook: snapshot context + quota to the shared state dir.

Reads the status line JSON payload on stdin, writes it atomically to
``~/.herdr-team/state/claude-status-<session_id>.json`` (and the aggregate
``claude-status.json`` consumed by ``hx usage``), prints a one-line status.

Sets HX_STATE_DIR to redirect the state dir (used by tests).
Hooks must never fail the session: every error is caught and exit code is 0.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import sys
from pathlib import Path
from typing import Any


def state_dir() -> Path:
    env = os.environ.get("HX_STATE_DIR")
    if env:
        return Path(env)
    return Path.home() / ".herdr-team" / "state"


def _utcnow() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _safe_session_id(session_id: Any) -> str:
    text = str(session_id or "").strip()
    if not text:
        return "unknown"
    cleaned = re.sub(r"[^A-Za-z0-9_.-]", "_", text)
    return cleaned[-80:]


def _pct(bucket: Any) -> int | None:
    if isinstance(bucket, dict):
        for key in ("used_percentage", "utilization", "used_percent"):
            value = bucket.get(key)
            if isinstance(value, (int, float)):
                return int(round(value))
    if isinstance(bucket, (int, float)):
        return int(round(bucket))
    return None


def status_line(record: dict[str, Any], written: str) -> str:
    ctx = record["context_window"] or {}
    used = ctx.get("used_percentage") if isinstance(ctx, dict) else None
    limits = record["rate_limits"] or {}
    five = _pct(limits.get("five_hour")) if isinstance(limits, dict) else None
    week = _pct(limits.get("seven_day")) if isinstance(limits, dict) else None
    ctx_txt = f"ctx {used:.0f}%" if isinstance(used, (int, float)) else "ctx ?"
    five_txt = f"{five}%" if five is not None else "?"
    week_txt = f"{week}%" if week is not None else "?"
    return f"{ctx_txt} | 5h {five_txt} wk {week_txt} | saved {written}"


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    record: dict[str, Any] = {
        "session_id": payload.get("session_id") or payload.get("sessionId") or None,
        "timestamp": _utcnow(),
        "context_window": payload.get("context_window"),
        "rate_limits": payload.get("rate_limits"),
    }

    target_dir = state_dir()
    target_dir.mkdir(parents=True, exist_ok=True)

    session_name = _safe_session_id(record["session_id"])
    per_session = target_dir / f"claude-status-{session_name}.json"
    _atomic_write_json(per_session, record)
    _atomic_write_json(target_dir / "claude-status.json", record)

    print(status_line(record, per_session.name))
    return 0


def _entry() -> int:
    try:
        return main()
    except BaseException as exc:
        try:
            print(f"statusline hook failed: {exc}")
        except Exception:
            pass
        return 0


if __name__ == "__main__":
    sys.exit(_entry())
