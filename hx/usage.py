"""hx.usage: snapshot of provider availability and quota.

All data is tagged measured | estimated | unknown, and every sample carries an
age in seconds. Only the Python standard library is used.
"""

from __future__ import annotations

import datetime
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


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


def _window_fractions() -> dict[str, float]:
    return {"5h": 0.2, "7d": 0.5, "30d": 1.0}


def _window_deltas() -> dict[str, datetime.timedelta]:
    return {
        "5h": datetime.timedelta(hours=5),
        "7d": datetime.timedelta(days=7),
        "30d": datetime.timedelta(days=30),
    }


def _parse_message_data(data: str) -> dict[str, Any]:
    try:
        return json.loads(data)
    except json.JSONDecodeError:
        return {}


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
    """Return OpenCode Go per-model spend over 5h/7d/30d windows.

    Reads only the ``message`` table from ``opencode.db`` opened read-only.
    """
    if db_path is None:
        db_path = default_opencode_db_path()
    if now is None:
        now = _utcnow()

    plan_name = policy.get("active_plan", "go")
    plan = policy.get("plans", {}).get(plan_name, {})
    caps = plan.get("monthly_caps", {})
    models = _ladder_models(policy)
    fractions = _window_fractions()
    deltas = _window_deltas()

    windows = {
        name: {
            "spend": 0.0,
            "cap": 0.0,
            "remaining": 0.0,
            "fraction": fractions[name],
            "utilisation": 0.0,
        }
        for name in fractions
    }

    model_state: dict[str, Any] = {
        m: {
            "windows": {name: dict(w) for name, w in windows.items()},
            "last_error": None,
            "newest_ms": 0,
        }
        for m in models
    }

    if not db_path.exists():
        return {
            "tag": "unknown",
            "sample_age_seconds": None,
            "plan": plan_name,
            "db_path": str(db_path),
            "models": model_state,
            "available": False,
            "error": "opencode.db not found",
        }

    cutoff_ms = int((now - max(deltas.values())).timestamp() * 1000)
    total_rows = 0
    newest_overall_ms = 0

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            "SELECT time_created, data FROM message WHERE time_created > ?",
            (cutoff_ms,),
        )
        for row in cur:
            total_rows += 1
            ts_ms = row["time_created"]
            msg = _parse_message_data(row["data"])
            if msg.get("role") != "assistant":
                continue
            model_id = msg.get("modelID")
            if model_id not in model_state:
                continue
            cost = float(msg.get("cost") or 0.0)
            state = model_state[model_id]
            if ts_ms > state["newest_ms"]:
                state["newest_ms"] = ts_ms
            if ts_ms > newest_overall_ms:
                newest_overall_ms = ts_ms

            # Rate-limit / error text from the last 24h.
            error_text = _is_error_message(msg)
            if error_text and (now.timestamp() * 1000 - ts_ms) <= 24 * 3600 * 1000:
                state["last_error"] = {
                    "text": error_text,
                    "age_seconds": int((now.timestamp() * 1000 - ts_ms) / 1000),
                }

            for name, delta in deltas.items():
                if ts_ms >= int((now - delta).timestamp() * 1000):
                    state["windows"][name]["spend"] += cost
        conn.close()
    except Exception as exc:  # pragma: no cover - defensive
        return {
            "tag": "unknown",
            "sample_age_seconds": None,
            "plan": plan_name,
            "db_path": str(db_path),
            "models": model_state,
            "available": False,
            "error": f"database read failed: {exc}",
        }

    for m in models:
        state = model_state[m]
        monthly = caps.get(m, {}).get(plan_name)
        if monthly is None:
            monthly = 0.0
        for name, win in state["windows"].items():
            win["cap"] = monthly * fractions[name]
            win["remaining"] = max(0.0, win["cap"] - win["spend"])
            if win["cap"] > 0:
                win["utilisation"] = win["spend"] / win["cap"]
            else:
                win["utilisation"] = 0.0

    sample_age_seconds = None
    if newest_overall_ms > 0:
        sample_age_seconds = int((now.timestamp() * 1000 - newest_overall_ms) / 1000)

    soft_limit = policy.get("thresholds", {}).get("pool_soft_limit", 0.85)
    available = all(
        win["utilisation"] < soft_limit
        for state in model_state.values()
        for win in state["windows"].values()
    )

    return {
        "tag": "estimated",
        "sample_age_seconds": sample_age_seconds,
        "plan": plan_name,
        "db_path": str(db_path),
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
            "available": False,
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
            "available": False,
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
            "available": False,
            "error": f"Failed to read rollout: {exc}",
        }

    if last_token_count is None:
        return {
            "tag": "unknown",
            "sample_age_seconds": None,
            "rollout": str(rollout),
            "rate_limits": None,
            "context": None,
            "available": False,
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
        status_path = default_claude_status_path()
    if now is None:
        now = _utcnow()

    if not status_path.exists():
        return {
            "tag": "unknown",
            "sample_age_seconds": None,
            "status_path": str(status_path),
            "context_window": None,
            "rate_limits": None,
            "available": False,
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
            "available": False,
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
            "available": False,
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
            "available": False,
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
