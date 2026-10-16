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
    # Model outside policy ladders should be ignored.
    insert("some-other-model", 50.0, 5)
    conn.commit()
    conn.close()


def test_load_policy() -> None:
    p = load_policy()
    assert p["active_plan"] == "go"
    assert "glm-5.3-flash" in p["plans"]["go"]["monthly_caps"]
    assert p["thresholds"]["pool_soft_limit"] == 0.85


def test_opencode_usage(tmp_path: Path, policy: dict, now: datetime.datetime) -> None:
    db = tmp_path / "opencode.db"
    _make_opencode_db(db, now)

    result = opencode_usage(policy, db_path=db, now=now)

    assert result["tag"] == "estimated"
    assert result["plan"] == "go"
    assert result["sample_age_seconds"] == 10 * 60  # newest message is 10 min old

    flash = result["models"]["glm-5.3-flash"]
    assert flash["windows"]["5h"]["spend"] == 1.0
    assert flash["windows"]["7d"]["spend"] == 3.0
    assert flash["windows"]["30d"]["spend"] == 7.0
    assert flash["last_error"] is not None
    assert "rate limit" in flash["last_error"]["text"]

    mimo = result["models"]["mimo-v2.6-flash"]
    assert mimo["windows"]["5h"]["spend"] == 0.5
    assert mimo["windows"]["7d"]["spend"] == 0.5

    # 5h cap for glm-5.3-flash on go is 60 * 0.2 = 12
    assert flash["windows"]["5h"]["cap"] == 12.0


def test_opencode_usage_missing_db(tmp_path: Path, policy: dict, now: datetime.datetime) -> None:
    result = opencode_usage(policy, db_path=tmp_path / "missing.db", now=now)
    assert result["tag"] == "unknown"
    assert not result["available"]


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
    assert not result["available"]


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
                    {"name": "hx-claude", "agent_status": "idle", "cwd": "/a", "focused": True},
                    {"name": "hx-codex", "agent_status": "idle", "cwd": "/b", "focused": False},
                    {"name": "dev-opencode", "agent_status": "working", "cwd": "/c"},
                ]
            }
        }

    monkeypatch.setattr("hx.usage._herdr_agent_list", fake_list)
    result = agents_usage(policy, prefix="hx-", now=now)
    assert result["tag"] == "measured"
    assert len(result["agents"]) == 2
    assert result["available"] is True


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
    assert not result["available"]


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
