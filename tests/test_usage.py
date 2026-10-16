"""Tests for hx.usage using only local fixtures."""

from __future__ import annotations

import datetime
import json
import os
import sqlite3
import subprocess
from pathlib import Path

import pytest

from hx import __main__ as cli
from hx.usage import (
    agents_usage,
    claude_usage,
    codex_usage,
    load_policy,
    opencode_usage,
    usage_snapshot,
)


@pytest.fixture
def policy() -> dict:
    return load_policy()


@pytest.fixture
def now() -> datetime.datetime:
    return datetime.datetime(2026, 10, 16, 12, 0, 0, tzinfo=datetime.timezone.utc)


def _make_opencode_db(db_path: Path, now: datetime.datetime) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE message (
            id TEXT PRIMARY KEY,
            session_id TEXT,
            time_created INTEGER,
            time_updated INTEGER,
            data TEXT
        )
        """
    )

    def insert(model_id: str, cost: float, minutes_ago: float, role: str = "assistant", finish: str | None = None) -> None:
        ts = int((now - datetime.timedelta(minutes=minutes_ago)).timestamp() * 1000)
        data = {
            "role": role,
            "modelID": model_id,
            "cost": cost,
            "time": {"created": ts},
        }
        if finish:
            data["finish"] = finish
            data["error"] = {"message": "rate limit"}
        conn.execute(
            "INSERT INTO message (id, session_id, time_created, time_updated, data) VALUES (?, ?, ?, ?, ?)",
            (f"msg_{minutes_ago}_{model_id}", "ses_1", ts, ts, json.dumps(data)),
        )

    insert("glm-5.3-flash", 1.0, 30)
    insert("glm-5.3-flash", 2.0, 360)  # 6h ago
    insert("glm-5.3-flash", 4.0, 8 * 24 * 60)  # 8d ago
    insert("mimo-v2.6-flash", 0.5, 10)
    # Error within last 24h.
    insert("glm-5.3-flash", 0.0, 60, finish="error")
    # User message should be ignored.
    insert("glm-5.3-flash", 99.0, 5, role="user")
    # Spend outside current ladders still consumes the shared subscription.
    insert("some-other-model", 50.0, 5)
    conn.commit()
    conn.close()


def test_load_policy() -> None:
    p = load_policy()
    assert p["active_plan"] == "go"
    assert p["opencode_pool"]["go"]["5h"] == 12
    assert p["opencode_pool"]["go_plus"]["7d"] == 120
    assert "monthly_caps" not in p["plans"]["go"]
    assert p["thresholds"]["pool_soft_limit"] == 0.85


def test_opencode_usage(tmp_path: Path, policy: dict, now: datetime.datetime) -> None:
    db = tmp_path / "opencode.db"
    _make_opencode_db(db, now)

    result = opencode_usage(policy, db_path=db, now=now)

    assert result["tag"] == "estimated"
    assert result["plan"] == "go"
    assert result["sample_age_seconds"] == 0
    assert result["newest_message_age_seconds"] == 5 * 60

    flash = result["models"]["glm-5.3-flash"]
    assert flash["windows"]["5h"]["spend"] == 1.0
    assert flash["windows"]["7d"]["spend"] == 3.0
    assert flash["windows"]["30d"]["spend"] == 7.0
    assert flash["last_error"] is not None
    assert "rate limit" in flash["last_error"]["text"]

    mimo = result["models"]["mimo-v2.6-flash"]
    assert mimo["windows"]["5h"]["spend"] == 0.5
    assert mimo["windows"]["7d"]["spend"] == 0.5

    pool = result["pool"]["windows"]
    assert pool["5h"]["spend"] == 51.5
    assert pool["7d"]["spend"] == 53.5
    assert pool["30d"]["spend"] == 57.5
    assert pool["5h"]["cap"] == 12
    assert pool["7d"]["utilisation"] == pytest.approx(53.5 / 30)
    assert result["available"] is False
    assert result["models"]["some-other-model"]["windows"]["5h"]["spend"] == 50
    assert "cap" not in flash["windows"]["5h"]
    assert "utilisation" not in flash["windows"]["5h"]


def test_opencode_usage_missing_db(tmp_path: Path, policy: dict, now: datetime.datetime) -> None:
    result = opencode_usage(policy, db_path=tmp_path / "missing.db", now=now)
    assert result["tag"] == "unknown"
    assert result["available"] == "unknown"


def test_shared_pool_dashboard_calibration_includes_models_outside_ladders(tmp_path, policy, now):
    db = tmp_path / "opencode.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE message (time_created INTEGER, data TEXT)")
        for model, cost, ago in [("old-model", 9.70, datetime.timedelta(hours=1)),
                                 ("excluded-model", 16.76, datetime.timedelta(days=6)),
                                 ("old-model", 1, datetime.timedelta(days=20))]:
            conn.execute("INSERT INTO message VALUES (?, ?)",
                         (int((now - ago).timestamp() * 1000),
                          json.dumps({"role": "assistant", "modelID": model, "cost": cost})))
    result = opencode_usage(policy, db_path=db, now=now)
    pool = result["pool"]["windows"]
    assert pool["5h"]["utilisation"] == pytest.approx(9.70 / 12)
    assert pool["7d"]["utilisation"] == pytest.approx(26.46 / 30)
    assert pool["30d"]["spend"] == pytest.approx(27.46)
    assert result["available"] is False
    policy["active_plan"] = "go_plus"
    plus = opencode_usage(policy, db_path=db, now=now)
    assert plus["tag"] == plus["pool"]["tag"] == "estimated"
    assert plus["pool"]["windows"]["5h"]["utilisation"] == pytest.approx(9.70 / 48)
    assert plus["available"] is True


def test_empty_database_is_a_fresh_available_pool(tmp_path, policy, now):
    db = tmp_path / "opencode.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE message (time_created INTEGER, data TEXT)")
    result = opencode_usage(policy, db_path=db, now=now)
    assert result["sample_age_seconds"] == 0
    assert result["newest_message_age_seconds"] is None
    assert result["pool"]["windows"]["5h"]["utilisation"] == 0
    assert result["available"] is True


def test_legacy_documentation_table_does_not_supply_missing_pool_caps(tmp_path, policy, now):
    db = tmp_path / "opencode.db"
    _make_opencode_db(db, now)
    del policy["opencode_pool"]
    result = opencode_usage(policy, db_path=db, now=now)
    assert result["tag"] == "unknown"
    assert result["available"] == "unknown"
    assert "shared pool caps" in result["error"]


def _make_codex_rollout(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps({"timestamp": "2026-10-16T10:00:00Z", "type": "event_msg", "payload": {"type": "token_count", "info": {}, "rate_limits": {}}}),
        json.dumps({
            "timestamp": "2026-10-16T11:30:00Z",
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "last_token_usage": {
                        "input_tokens": 1000,
                        "cached_input_tokens": 500,
                        "output_tokens": 200,
                    },
                    "model_context_window": 475000,
                },
                "rate_limits": {
                    "primary": {"used_percent": 25.0, "window_minutes": 300, "resets_at": 1792130450},
                    "secondary": {"used_percent": 5.0, "window_minutes": 10080, "resets_at": 1792717250},
                },
            },
        }),
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def test_codex_usage(tmp_path: Path, policy: dict, now: datetime.datetime) -> None:
    rollout = tmp_path / "2026" / "10" / "16" / "rollout.jsonl"
    _make_codex_rollout(rollout)
    result = codex_usage(policy, sessions_dir=tmp_path, now=now)

    assert result["tag"] == "measured"
    assert result["sample_age_seconds"] == 30 * 60
    assert result["rate_limits"]["primary"]["used_percent"] == 25.0
    assert result["context"]["input_tokens"] == 1000
    assert result["available"] is True


def test_codex_usage_missing_dir(tmp_path: Path, policy: dict, now: datetime.datetime) -> None:
    result = codex_usage(policy, sessions_dir=tmp_path / "nope", now=now)
    assert result["tag"] == "unknown"
    assert result["available"] == "unknown"


def test_claude_usage_measured(tmp_path: Path, policy: dict, now: datetime.datetime) -> None:
    status = tmp_path / "claude-status.json"
    status.write_text(json.dumps({
        "context_window": {"used_percentage": 40.0, "context_window_size": 200000},
        "rate_limits": {"five_hour": {"used_percentage": 10.0}, "seven_day": {"used_percentage": 20.0}},
    }), encoding="utf-8")
    # Set mtime to 10 minutes ago.
    mtime = (now - datetime.timedelta(minutes=10)).timestamp()
    os.utime(status, (mtime, mtime))

    result = claude_usage(policy, status_path=status, now=now)
    assert result["tag"] == "measured"
    assert result["sample_age_seconds"] == 10 * 60
    assert result["context_window"]["used_percentage"] == 40.0
    assert result["available"] is True


def test_claude_usage_stale(tmp_path: Path, policy: dict, now: datetime.datetime) -> None:
    status = tmp_path / "claude-status.json"
    status.write_text(json.dumps({"context_window": {"used_percentage": 40.0}}), encoding="utf-8")
    mtime = (now - datetime.timedelta(hours=2)).timestamp()
    os.utime(status, (mtime, mtime))

    result = claude_usage(policy, status_path=status, now=now)
    assert result["tag"] == "unknown"
    assert "older than 1 hour" in result["error"]


def test_agents_usage(monkeypatch: pytest.MonkeyPatch, policy: dict, now: datetime.datetime) -> None:
    def fake_list() -> dict:
        return {
            "result": {
                "agents": [
                    {"name": "hx-claude", "agent_status": "idle", "cwd": "/a", "focused": True, "agent_session": {"value": "ses-claude"}},
                    {"name": "hx-codex", "agent_status": "idle", "cwd": "/b", "focused": False, "agent_session": {"value": "ses-codex"}},
                    {"name": "dev-opencode", "agent_status": "working", "cwd": "/c"},
                ]
            }
        }

    monkeypatch.setattr("hx.usage._herdr_agent_list", fake_list)
    result = agents_usage(policy, prefix="hx-", now=now)
    assert result["tag"] == "measured"
    assert len(result["agents"]) == 2
    assert result["available"] is True
    assert result["agents"][0]["agent_session"] == "ses-claude"
    assert result["agents"][1]["agent_session"] == "ses-codex"


def test_agents_usage_blocked(monkeypatch: pytest.MonkeyPatch, policy: dict, now: datetime.datetime) -> None:
    def fake_list() -> dict:
        return {"result": {"agents": [{"name": "hx-claude", "agent_status": "blocked"}]}}

    monkeypatch.setattr("hx.usage._herdr_agent_list", fake_list)
    result = agents_usage(policy, prefix="hx-", now=now)
    assert result["available"] is False


def test_agents_usage_subprocess_failure(monkeypatch: pytest.MonkeyPatch, policy: dict, now: datetime.datetime) -> None:
    def fake_list() -> dict:
        raise subprocess.CalledProcessError(1, "herdr")

    monkeypatch.setattr("hx.usage._herdr_agent_list", fake_list)
    result = agents_usage(policy, prefix="hx-", now=now)
    assert result["tag"] == "unknown"
    assert result["available"] == "unknown"


def test_usage_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, now: datetime.datetime) -> None:
    db = tmp_path / "opencode.db"
    _make_opencode_db(db, now)
    rollout = tmp_path / "rollout.jsonl"
    _make_codex_rollout(rollout)
    status = tmp_path / "claude-status.json"
    status.write_text(json.dumps({"context_window": {"used_percentage": 10.0}}), encoding="utf-8")
    mtime = (now - datetime.timedelta(minutes=5)).timestamp()
    os.utime(status, (mtime, mtime))

    monkeypatch.setenv("HX_OPENCODE_DB", str(db))
    monkeypatch.setenv("HX_CODEX_SESSIONS_DIR", str(tmp_path))
    monkeypatch.setenv("HX_CLAUDE_STATUS", str(status))

    def fake_list() -> dict:
        return {"result": {"agents": [{"name": "hx-claude", "agent_status": "idle"}]}}

    monkeypatch.setattr("hx.usage._herdr_agent_list", fake_list)

    snapshot = usage_snapshot(now=now)
    assert snapshot["opencode"]["tag"] == "estimated"
    assert snapshot["codex"]["tag"] == "measured"
    assert snapshot["claude"]["tag"] == "measured"
    assert snapshot["agents"]["tag"] == "measured"


def test_cli_usage_prints_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, now: datetime.datetime
) -> None:
    db = tmp_path / "opencode.db"
    _make_opencode_db(db, now)
    rollout = tmp_path / "rollout.jsonl"
    _make_codex_rollout(rollout)
    status = tmp_path / "claude-status.json"
    status.write_text(json.dumps({"context_window": {"used_percentage": 10.0}}), encoding="utf-8")
    mtime = (now - datetime.timedelta(minutes=5)).timestamp()
    os.utime(status, (mtime, mtime))

    monkeypatch.setenv("HX_OPENCODE_DB", str(db))
    monkeypatch.setenv("HX_CODEX_SESSIONS_DIR", str(tmp_path))
    monkeypatch.setenv("HX_CLAUDE_STATUS", str(status))

    def fake_list() -> dict:
        return {"result": {"agents": [{"name": "hx-claude", "agent_status": "idle"}]}}

    monkeypatch.setattr("hx.usage._herdr_agent_list", fake_list)

    code = cli.main(["usage"])
    assert code == 0
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert parsed["opencode"]["tag"] == "estimated"
    assert parsed["codex"]["tag"] == "measured"


# ---------- claude status preference (per-session files) ----------------------


def _write_claude_status(path: Path, used: float, mtime_ts: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "session_id": path.stem.removeprefix("claude-status-"),
                "context_window": {"used_percentage": used},
                "rate_limits": {"five_hour": {"used_percentage": used / 2}},
            }
        ),
        encoding="utf-8",
    )
    os.utime(path, (mtime_ts, mtime_ts))


@pytest.fixture
def claude_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    state = tmp_path / "state"
    monkeypatch.delenv("HX_CLAUDE_STATUS", raising=False)
    monkeypatch.setenv("HX_STATE_DIR", str(state))
    monkeypatch.setenv("HX_LEASES_PATH", str(tmp_path / "leases.json"))
    monkeypatch.setenv("HX_LOCK_PATH", str(tmp_path / "lock.lock"))
    return state


def test_claude_prefers_lead_session_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, claude_state: Path, now: datetime.datetime
) -> None:
    mtime = now.timestamp() - 60
    _write_claude_status(claude_state / "claude-status.json", 90.0, mtime)
    _write_claude_status(claude_state / "claude-status-lead-sid.json", 10.0, mtime)
    _write_claude_status(claude_state / "claude-status-other.json", 80.0, mtime)
    (tmp_path / "leases.json").write_text(
        json.dumps(
            {
                "files": {},
                "sessions": {
                    "hx-claude": {
                        "agent": "hx-claude",
                        "session_id": "lead-sid",
                        "holder": "claude",
                        "state": "held",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    result = claude_usage({"team_prefix": "hx-", "thresholds": {"pool_soft_limit": 0.85}}, now=now)

    assert result["tag"] == "measured"
    assert result["status_path"].endswith("claude-status-lead-sid.json")
    assert result["context_window"]["used_percentage"] == 10.0


def test_claude_prefers_newest_per_session_without_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, claude_state: Path, now: datetime.datetime
) -> None:
    older = now.timestamp() - 600
    newer = now.timestamp() - 120
    _write_claude_status(claude_state / "claude-status-older.json", 70.0, older)
    _write_claude_status(claude_state / "claude-status-newer.json", 20.0, newer)

    result = claude_usage({"team_prefix": "hx-", "thresholds": {"pool_soft_limit": 0.85}}, now=now)

    assert result["status_path"].endswith("claude-status-newer.json")
    assert result["context_window"]["used_percentage"] == 20.0


def test_claude_aggregate_is_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, claude_state: Path, now: datetime.datetime
) -> None:
    mtime = now.timestamp() - 60
    _write_claude_status(claude_state / "claude-status.json", 50.0, mtime)

    result = claude_usage({"team_prefix": "hx-", "thresholds": {"pool_soft_limit": 0.85}}, now=now)

    assert result["status_path"].endswith("claude-status.json")
    assert result["context_window"]["used_percentage"] == 50.0


def test_claude_env_override_still_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, claude_state: Path, now: datetime.datetime
) -> None:
    mtime = now.timestamp() - 60
    _write_claude_status(claude_state / "claude-status-lead-sid.json", 99.0, mtime)
    explicit = tmp_path / "explicit.json"
    _write_claude_status(explicit, 5.0, mtime)
    monkeypatch.setenv("HX_CLAUDE_STATUS", str(explicit))

    result = claude_usage({"team_prefix": "hx-", "thresholds": {"pool_soft_limit": 0.85}}, now=now)

    assert result["status_path"] == str(explicit)
    assert result["context_window"]["used_percentage"] == 5.0
