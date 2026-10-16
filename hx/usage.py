"""hx.usage: snapshot of provider availability and quota.

All data is tagged measured | estimated | unknown, and every sample carries an
age in seconds. Only the Python standard library is used.
"""

from __future__ import annotations

import datetime
import json
import math
import os
import re
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from typing import Any

from hx import paths


def _repo_root() -> Path:
    return paths.package_root()


def _env_path(var: str, default: Path) -> Path:
    value = os.environ.get(var)
    return Path(value) if value else default


def default_policy_path() -> Path:
    return _env_path("HX_POLICY_PATH", _repo_root() / "policy.json")


def default_opencode_db_path() -> Path:
    return _env_path("HX_OPENCODE_DB", Path.home() / ".local" / "share" / "opencode" / "opencode.db")


def default_codex_sessions_dir() -> Path:
    return _env_path("HX_CODEX_SESSIONS_DIR", Path.home() / ".codex" / "sessions")


def default_claude_status_path() -> Path:
    return _env_path("HX_CLAUDE_STATUS", Path.home() / ".herdr-team" / "state" / "claude-status.json")


def _claude_state_dir() -> Path:
    env = os.environ.get("HX_STATE_DIR")
    if env:
        return Path(env)
    return Path.home() / ".herdr-team" / "state"


def _safe_session_id(session_id: Any) -> str:
    text = str(session_id or "").strip()
    if not text:
        return "unknown"
    return re.sub(r"[^A-Za-z0-9_.-]", "_", text)[-80:]


def _lead_session_id(prefix: str) -> str | None:
    """Lead session id from the ledger's session leases (read-only peek)."""
    try:
        from hx import ledger as ledger_mod

        leases_path = ledger_mod.default_leases_path()
        if not leases_path.exists():
            return None
        data = json.loads(leases_path.read_text(encoding="utf-8"))
        sessions = data.get("sessions", {})
        entry = sessions.get(f"{prefix}claude")
        if entry is None and isinstance(sessions, dict):
            for value in sessions.values():
                if isinstance(value, dict) and value.get("agent") == f"{prefix}claude":
                    entry = value
                    break
        if isinstance(entry, dict):
            session_id = entry.get("session_id")
            return str(session_id) if session_id else None
    except Exception:
        return None
    return None


def resolve_claude_status_path(policy: dict[str, Any]) -> Path:
    """Pick the freshest trusted claude status file.

    Order: HX_CLAUDE_STATUS override, the per-session file of the lead's
    session id recorded in the ledger, the newest per-session file, then the
    aggregate ``claude-status.json`` as last resort.
    """
    env = os.environ.get("HX_CLAUDE_STATUS")
    if env:
        return Path(env)

    state_dir = _claude_state_dir()
    prefix = policy.get("team_prefix", "hx-")
    session_id = _lead_session_id(str(prefix))
    if session_id:
        candidate = state_dir / f"claude-status-{_safe_session_id(session_id)}.json"
        if candidate.exists():
            return candidate
    per_session = [
        p for p in state_dir.glob("claude-status-*.json") if p.name != "claude-status.json"
    ]
    if per_session:
        return max(per_session, key=lambda p: p.stat().st_mtime)
    return state_dir / "claude-status.json"


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def load_policy(path: Path | None = None) -> dict[str, Any]:
    if path is None:
        path = default_policy_path()
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _model_ids_in_policy(policy: dict[str, Any]) -> set[str]:
    ladders = policy.get("ladders", {})
    ids: set[str] = set()
    for tier in ladders.values():
        ids.update(tier)
    return ids


def _window_deltas() -> dict[str, datetime.timedelta]:
    return {
        "5h": datetime.timedelta(hours=5),
        "7d": datetime.timedelta(days=7),
        "30d": datetime.timedelta(days=30),
    }


def _is_error_message(msg: dict[str, Any]) -> str | None:
    finish = msg.get("finish")
    error = msg.get("error")
    if finish == "error" or error:
        if isinstance(error, dict):
            text = error.get("message") or error.get("text") or json.dumps(error)
        elif isinstance(error, str):
            text = error
        else:
            text = "error"
        return text
    return None


def _ladder_models(policy: dict[str, Any]) -> list[str]:
    ladders = policy.get("ladders", {})
    order = ["economy", "default", "strong"]
    seen: set[str] = set()
    result: list[str] = []
    for tier in order:
        for m in ladders.get(tier, []):
            if m not in seen:
                seen.add(m)
                result.append(m)
    return result


def opencode_usage(
    policy: dict[str, Any],
    db_path: Path | None = None,
    now: datetime.datetime | None = None,
) -> dict[str, Any]:
    """Estimate shared OpenCode spend over 5h/7d/30d windows, across all models.

    Reads only the ``message`` table from ``opencode.db`` opened read-only.
    """
    if db_path is None:
        db_path = default_opencode_db_path()
    if now is None:
        now = _utcnow()

    plan_name = policy.get("active_plan", "go")
    caps = policy.get("opencode_pool", {}).get(plan_name, {})
    deltas = _window_deltas()
    windows = {name: {"spend": 0.0, "cap": caps.get(name),
                      "remaining": None, "utilisation": None} for name in deltas}

    model_state: dict[str, Any] = {
        m: {
            "windows": {name: {"spend": 0.0} for name in deltas},
            "last_error": None,
            "newest_ms": 0,
        }
        for m in _ladder_models(policy)
    }

    def unknown(error: str) -> dict[str, Any]:
        return {"tag": "unknown", "sample_age_seconds": None, "plan": plan_name,
                "db_path": str(db_path), "pool": {"tag": "unknown", "windows": windows},
                "models": model_state, "available": "unknown", "error": error}

    if not db_path.exists():
        return unknown("opencode.db not found")

    cutoff_ms = int((now - max(deltas.values())).timestamp() * 1000)
    newest_overall_ms = 0

    try:
        with closing(sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
            rows = conn.execute(
                "SELECT time_created, data FROM message WHERE time_created >= ? AND time_created <= ? ORDER BY time_created",
                (cutoff_ms, int(now.timestamp() * 1000)),
            )
            for ts_ms, raw in rows:
                msg = json.loads(raw)
                if msg.get("role") != "assistant":
                    continue
                cost = float(msg.get("cost") or 0.0)
                if not math.isfinite(cost) or cost < 0:
                    raise ValueError("invalid assistant message cost")
                model_id = msg.get("modelID") or "unknown-model"
                state = model_state.setdefault(model_id, {
                    "windows": {name: {"spend": 0.0} for name in deltas},
                    "last_error": None, "newest_ms": 0})
                state["newest_ms"] = max(state["newest_ms"], ts_ms)
                newest_overall_ms = max(newest_overall_ms, ts_ms)
                error_text = _is_error_message(msg)
                if error_text and now.timestamp() * 1000 - ts_ms <= 24 * 3600 * 1000:
                    state["last_error"] = {"text": error_text,
                        "age_seconds": int((now.timestamp() * 1000 - ts_ms) / 1000)}
                for name, delta in deltas.items():
                    if ts_ms >= int((now - delta).timestamp() * 1000):
                        windows[name]["spend"] += cost
                        state["windows"][name]["spend"] += cost
    except Exception as exc:  # pragma: no cover - defensive
        return unknown(f"database read failed: {exc}")

    for win in windows.values():
        cap = win["cap"]
        if not isinstance(cap, (float, int)) or not math.isfinite(cap) or cap <= 0:
            return unknown("missing or invalid shared pool caps for plan " + plan_name)
        win["remaining"] = max(0.0, cap - win["spend"])
        win["utilisation"] = win["spend"] / cap

    soft_limit = policy.get("thresholds", {}).get("pool_soft_limit", 0.85)
    available = all(
        win["utilisation"] < soft_limit
        for win in windows.values()
    )

    return {
        "tag": "estimated",
        "sample_age_seconds": 0,
        "sampled_at": now.isoformat(),
        "newest_message_age_seconds": int((now.timestamp() * 1000 - newest_overall_ms) / 1000) if newest_overall_ms else None,
        "plan": plan_name,
        "db_path": str(db_path),
        "pool": {"tag": "estimated", "windows": windows},
        "models": model_state,
        "available": available,
    }


def _newest_codex_rollout(sessions_dir: Path) -> Path | None:
    newest: Path | None = None
    newest_mtime = 0.0
    for root, _dirs, files in os.walk(sessions_dir):
        for fname in files:
            if fname.endswith(".jsonl"):
                path = Path(root) / fname
                mtime = path.stat().st_mtime
                if mtime > newest_mtime:
                    newest_mtime = mtime
                    newest = path
    return newest


def _parse_iso_timestamp(ts: str) -> datetime.datetime | None:
    try:
        return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def codex_usage(
    policy: dict[str, Any],
    sessions_dir: Path | None = None,
    now: datetime.datetime | None = None,
) -> dict[str, Any]:
    if sessions_dir is None:
        sessions_dir = default_codex_sessions_dir()
    if now is None:
        now = _utcnow()

    if not sessions_dir.exists():
        return {
            "tag": "unknown",
            "sample_age_seconds": None,
            "sessions_dir": str(sessions_dir),
            "rate_limits": None,
            "context": None,
            "available": "unknown",
            "error": "Codex sessions directory not found",
        }

    rollout = _newest_codex_rollout(sessions_dir)
    if rollout is None:
        return {
            "tag": "unknown",
            "sample_age_seconds": None,
            "sessions_dir": str(sessions_dir),
            "rate_limits": None,
            "context": None,
            "available": "unknown",
            "error": "No Codex rollout JSONL found",
        }

    last_token_count: dict[str, Any] | None = None
    last_sample_ts: datetime.datetime | None = None
    try:
        with open(rollout, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("type") != "event_msg":
                    continue
                payload = event.get("payload", {})
                if payload.get("type") != "token_count":
                    continue
                last_token_count = payload
                sample = _parse_iso_timestamp(event.get("timestamp"))
                if sample:
                    last_sample_ts = sample
    except Exception as exc:  # pragma: no cover
        return {
            "tag": "unknown",
            "sample_age_seconds": None,
            "rollout": str(rollout),
            "rate_limits": None,
            "context": None,
            "available": "unknown",
            "error": f"Failed to read rollout: {exc}",
        }

    if last_token_count is None:
        return {
            "tag": "unknown",
            "sample_age_seconds": None,
            "rollout": str(rollout),
            "rate_limits": None,
            "context": None,
            "available": "unknown",
            "error": "No token_count event found",
        }

    sample_age_seconds = None
    if last_sample_ts:
        sample_age_seconds = int((now - last_sample_ts).total_seconds())

    info = last_token_count.get("info", {})
    rate_limits = last_token_count.get("rate_limits", {})
    context = {
        "input_tokens": info.get("last_token_usage", {}).get("input_tokens"),
        "cached_input_tokens": info.get("last_token_usage", {}).get("cached_input_tokens"),
        "output_tokens": info.get("last_token_usage", {}).get("output_tokens"),
        "model_context_window": info.get("model_context_window"),
    }

    soft_limit = policy.get("thresholds", {}).get("pool_soft_limit", 0.85)
    primary_pct = rate_limits.get("primary", {}).get("used_percent", 0)
    secondary_pct = rate_limits.get("secondary", {}).get("used_percent", 0)
    available = primary_pct < soft_limit * 100 and secondary_pct < soft_limit * 100

    return {
        "tag": "measured",
        "sample_age_seconds": sample_age_seconds,
        "rollout": str(rollout),
        "rate_limits": rate_limits,
        "context": context,
        "available": available,
    }


def claude_usage(
    policy: dict[str, Any],
    status_path: Path | None = None,
    now: datetime.datetime | None = None,
) -> dict[str, Any]:
    if status_path is None:
        status_path = resolve_claude_status_path(policy)
    if now is None:
        now = _utcnow()

    if not status_path.exists():
        return {
            "tag": "unknown",
            "sample_age_seconds": None,
            "status_path": str(status_path),
            "context_window": None,
            "rate_limits": None,
            "available": "unknown",
            "error": "Claude status file not found",
        }

    try:
        mtime = datetime.datetime.fromtimestamp(
            status_path.stat().st_mtime, tz=datetime.timezone.utc
        )
        with open(status_path, "r", encoding="utf-8") as fh:
            status = json.load(fh)
    except Exception as exc:
        return {
            "tag": "unknown",
            "sample_age_seconds": None,
            "status_path": str(status_path),
            "context_window": None,
            "rate_limits": None,
            "available": "unknown",
            "error": f"Failed to read status file: {exc}",
        }

    sample_age_seconds = int((now - mtime).total_seconds())
    if sample_age_seconds > 3600:
        return {
            "tag": "unknown",
            "sample_age_seconds": sample_age_seconds,
            "status_path": str(status_path),
            "context_window": status.get("context_window"),
            "rate_limits": status.get("rate_limits"),
            "available": "unknown",
            "error": "Claude status file is older than 1 hour",
        }

    context_window = status.get("context_window", {})
    rate_limits = status.get("rate_limits", {})

    soft_limit = policy.get("thresholds", {}).get("pool_soft_limit", 0.85)
    used_pct = context_window.get("used_percentage", 0)
    if used_pct is None:
        used_pct = 0
    available = used_pct < soft_limit * 100

    return {
        "tag": "measured",
        "sample_age_seconds": sample_age_seconds,
        "status_path": str(status_path),
        "context_window": context_window,
        "rate_limits": rate_limits,
        "available": available,
    }


def _herdr_agent_list() -> dict[str, Any]:
    raw = subprocess.check_output(
        ["herdr", "agent", "list"],
        stderr=subprocess.DEVNULL,
        timeout=30,
    )
    return json.loads(raw.decode("utf-8"))


def agents_usage(
    policy: dict[str, Any],
    prefix: str | None = None,
    now: datetime.datetime | None = None,
) -> dict[str, Any]:
    if now is None:
        now = _utcnow()
    if prefix is None:
        prefix = os.environ.get("HX_HERDR_PREFIX", policy.get("team_prefix", "hx-"))

    try:
        data = _herdr_agent_list()
    except Exception as exc:
        return {
            "tag": "unknown",
            "sample_age_seconds": None,
            "prefix": prefix,
            "agents": [],
            "available": "unknown",
            "error": f"herdr agent list failed: {exc}",
        }

    agents = data.get("result", {}).get("agents", [])
    team = [a for a in agents if a.get("name", "").startswith(prefix)]
    sample_age_seconds = 0

    available = all(a.get("agent_status") not in ("blocked", "error") for a in team)

    return {
        "tag": "measured",
        "sample_age_seconds": sample_age_seconds,
        "prefix": prefix,
        "agents": [
            {
                "name": a.get("name"),
                "status": a.get("agent_status"),
                "cwd": a.get("cwd"),
                "focused": a.get("focused"),
                "agent_session": (a.get("agent_session") or {}).get("value"),
            }
            for a in team
        ],
        "available": available,
    }


def usage_snapshot(
    policy: dict[str, Any] | None = None,
    now: datetime.datetime | None = None,
) -> dict[str, Any]:
    if policy is None:
        policy = load_policy()
    if now is None:
        now = _utcnow()

    return {
        "timestamp": now.isoformat(),
        "opencode": opencode_usage(policy, now=now),
        "codex": codex_usage(policy, now=now),
        "claude": claude_usage(policy, now=now),
        "agents": agents_usage(policy, now=now),
    }


def print_usage() -> None:
    snapshot = usage_snapshot()
    print(json.dumps(snapshot, indent=2))
