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
import sys
from contextlib import closing
from pathlib import Path
from typing import Any

from hx import devin, host, opencode_go, paths
from hx.host import TEAM_AGENT

READY = frozenset({"idle", "done"})
_AGENTS_UNSET = object()


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
    prefix = team_prefix(policy)
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


def _month_start(now: datetime.datetime) -> datetime.datetime:
    """The OpenCode Go monthly allowance resets at the start of the month."""
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _window_start(name: str, now: datetime.datetime) -> datetime.datetime:
    """Start of a usage window: 5h and 7d roll; the monthly 30d window is calendar."""
    if name == "30d":
        return _month_start(now)
    return now - _window_deltas()[name]


# A dashboard percentage is a point-in-time snapshot of a rolling window. It
# cannot be carried forward as a flat baseline: the spend that produced it
# starts rolling out immediately, so an old snapshot overstates the current
# window. Local spend is the rolling measurement; keep the snapshot only long
# enough to correct a recent local-cost drift.
MAX_DASHBOARD_AGE = datetime.timedelta(hours=24)


def _dashboard_reading(
    policy: dict[str, Any], now: datetime.datetime, deltas: dict[str, datetime.timedelta]
) -> dict[str, Any] | None:
    """The user's last OpenCode dashboard reading, if it is still fresh enough.

    A reading of window W is only a calibration point. The local spend is a
    rolling sum, so a reading older than MAX_DASHBOARD_AGE is ignored rather
    than added on top of later spend; otherwise the old baseline would keep
    inflating a window after its spend had rolled out.
    """
    raw = policy.get("opencode_pool", {}).get("dashboard")
    if not isinstance(raw, dict) or not raw.get("at"):
        return None
    at = datetime.datetime.fromisoformat(str(raw["at"]).replace("Z", "+00:00"))
    if at.tzinfo is None:
        at = at.replace(tzinfo=datetime.timezone.utc)
    if at > now or now - at > MAX_DASHBOARD_AGE:
        return None
    used = {
        name: float(pct)
        for name, pct in (raw.get("used_percent") or {}).items()
        if name in deltas and at >= _window_start(name, now)
    }
    if not used:
        return None
    return {"at": at.isoformat(), "at_ms": int(at.timestamp() * 1000), "used_percent": used}


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
    """Prefer provider quota; retain local spend estimates on API failure."""
    now = now or _utcnow()
    local = _local_opencode_usage(policy, db_path, now)
    auth_path = _env_path("HX_OPENCODE_AUTH", (db_path or default_opencode_db_path()).with_name("auth.json"))
    windows = opencode_go.fetch_usage(auth_path)
    if windows is None:
        return local
    result = {**local, "tag": "measured", "sample_age_seconds": 0,
              "sampled_at": now.isoformat(), "source": opencode_go.ENDPOINT,
              "pool": {"tag": "measured", "source": opencode_go.ENDPOINT, "windows": windows},
              "models_tag": local["tag"], "local_estimate": local["pool"],
              "available": all(w["utilisation"] < policy.get("thresholds", {}).get("pool_soft_limit", .85)
                               for w in windows.values())}
    result.pop("error", None)
    return result


def _local_opencode_usage(
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

    cutoff_ms = min(int(_window_start(name, now).timestamp() * 1000) for name in deltas)
    reading = _dashboard_reading(policy, now, deltas)
    spend_since_reading = {name: 0.0 for name in deltas}
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
                for name in deltas:
                    if ts_ms >= int(_window_start(name, now).timestamp() * 1000):
                        windows[name]["spend"] += cost
                        state["windows"][name]["spend"] += cost
                if reading and ts_ms >= reading["at_ms"]:
                    for name in spend_since_reading:
                        spend_since_reading[name] += cost
    except Exception as exc:  # pragma: no cover - defensive
        return unknown(f"database read failed: {exc}")

    for win in windows.values():
        cap = win["cap"]
        if not isinstance(cap, (float, int)) or not math.isfinite(cap) or cap <= 0:
            return unknown("missing or invalid shared pool caps for plan " + plan_name)
        name = next(k for k, v in windows.items() if v is win)
        if reading and name in reading["used_percent"]:
            # The dashboard is ground truth; local spend only adds what happened after the reading.
            win["spend"] = cap * reading["used_percent"][name] / 100.0 + spend_since_reading[name]
            win["calibrated_at"] = reading["at"]
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
    if not isinstance(rate_limits, dict):
        rate_limits = {}
    for name in ("primary", "secondary"):
        if not isinstance(rate_limits.get(name), dict):
            rate_limits[name] = None
    context = {
        "input_tokens": info.get("last_token_usage", {}).get("input_tokens"),
        "cached_input_tokens": info.get("last_token_usage", {}).get("cached_input_tokens"),
        "output_tokens": info.get("last_token_usage", {}).get("output_tokens"),
        "model_context_window": info.get("model_context_window"),
    }

    soft_limit = policy.get("thresholds", {}).get("pool_soft_limit", 0.85)
    primary_pct = (rate_limits["primary"] or {}).get("used_percent")
    secondary_pct = (rate_limits["secondary"] or {}).get("used_percent")
    known = isinstance(primary_pct, (int, float)) and isinstance(secondary_pct, (int, float))
    available = (
        primary_pct < soft_limit * 100 and secondary_pct < soft_limit * 100
        if known else "unknown"
    )

    return {
        "tag": "measured" if known else "unknown",
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
    stale_seconds = int(policy.get("thresholds", {}).get("stale_seconds", 3600))
    if sample_age_seconds > stale_seconds:
        return {
            "tag": "unknown",
            "sample_age_seconds": sample_age_seconds,
            "status_path": str(status_path),
            "context_window": status.get("context_window"),
            "rate_limits": status.get("rate_limits"),
            "available": "unknown",
            "error": f"Claude status file is stale ({sample_age_seconds}s old; freshness limit {stale_seconds}s)",
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


def live_members() -> list[host.Member]:
    """Every live agent on the detected host: the one list call path."""
    members = host.all_members()
    if members is None:
        raise RuntimeError("hx is not running on a team host")
    return members


# A team agent is <team>-<role>, for example dev-claude or my-team-luna. Roles and models are not listed
# anywhere: membership is the team prefix, and the provider comes from the host's detected agent kind.


def lead_name(policy: dict[str, Any], prefix: str) -> str:
    """The team's lead agent: the one role the policy names (lead_role, default lead).

    The lead is a seat, not a model family: whichever terminal carries role=lead
    leads, so workers always report with `hx say lead`.
    """
    return prefix + str(policy.get("lead_role", "lead"))


def team_agents(prefix: str) -> dict[str, host.Member]:
    """Live host agents on this team, by name."""
    detected = host.detect()
    if detected is None:
        return {}
    return detected.team(prefix)


def team_prefix(policy: dict[str, Any]) -> str:
    """The prefix of the team hx runs in (any <team>- name), per the herdr-team skill.

    HX_HERDR_PREFIX wins; otherwise the host agent whose pane is this pane names
    the team; policy.team_prefix is the fallback outside a team host.
    """
    env = os.environ.get("HX_HERDR_PREFIX")
    if env:
        return env
    name = own_agent_name()
    if name:
        return TEAM_AGENT.fullmatch(name)[1]
    return str(policy.get("team_prefix", "hx-"))


def own_agent_name() -> str | None:
    """This pane's team agent name (e.g. dev-codex), or None outside a team pane."""
    detected = host.detect()
    if detected is None:
        return None
    try:
        found = detected.own()
    except Exception:
        return None
    return found["name"] if found else None


def agents_usage(
    policy: dict[str, Any],
    prefix: str | None = None,
    now: datetime.datetime | None = None,
    agents: Any = _AGENTS_UNSET,
) -> dict[str, Any]:
    if now is None:
        now = _utcnow()
    if prefix is None:
        prefix = team_prefix(policy)

    if agents is _AGENTS_UNSET:
        try:
            agents = live_members()
        except Exception as exc:
            return {
                "tag": "unknown",
                "sample_age_seconds": None,
                "prefix": prefix,
                "agents": [],
                "available": "unknown",
                "error": f"host agent list failed: {exc}",
            }

    if agents is None:
        return {"tag": "unknown", "sample_age_seconds": None, "prefix": prefix,
                "agents": [], "available": "unknown", "error": "Host agent list unavailable"}

    team = [a for a in agents if a["name"].startswith(prefix)]
    sample_age_seconds = 0

    available = all(a["status"] not in ("blocked", "error", "unknown") for a in team)

    return {
        "tag": "measured",
        "sample_age_seconds": sample_age_seconds,
        "prefix": prefix,
        "agents": [
            {
                "name": a["name"],
                "status": a["status"],
                "cwd": a["cwd"],
                "focused": a["focused"],
                "agent_session": a["session_id"],
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

    try:
        agents = live_members()
    except Exception:
        agents = None

    devin_block = devin.usage(policy, now=now, agents=agents)
    if agents is not None:
        # Keep a diagnostic snapshot; admission reads the host again.
        devin.mark_live_sessions(devin.live_sessions(agents) or [], now)

    return {
        "timestamp": now.isoformat(),
        "opencode": opencode_usage(policy, now=now),
        "codex": codex_usage(policy, now=now),
        "claude": claude_usage(policy, now=now),
        "devin": devin_block,
        "agents": agents_usage(policy, now=now, agents=agents),
    }


def print_usage() -> None:
    snapshot = usage_snapshot()
    print(json.dumps(snapshot, indent=2))
