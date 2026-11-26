"""Q-1080: pool pacing from fake usage samples; underused, ahead, unknown, free."""
import datetime as dt

from hx import pace

NOW = dt.datetime(2026, 11, 26, 12, 0, tzinfo=dt.timezone.utc)
POLICY = {"pace": {"under": 0.10, "over": 0.10, "reset_soon": 0.50}}


def snapshot(codex_primary=5.0, codex_secondary=34.0, devin_active=True):
    now = NOW
    return {
        "timestamp": now.isoformat(),
        "codex": {
            "tag": "measured",
            "rate_limits": {
                "primary": {
                    "used_percent": codex_primary,
                    "window_minutes": 300,
                    "resets_at": int((now + dt.timedelta(hours=1)).timestamp()),
                },
                "secondary": {
                    "used_percent": codex_secondary,
                    "window_minutes": 10080,
                    "resets_at": int((now + dt.timedelta(days=3)).timestamp()),
                },
            },
        },
        "claude": {
            "tag": "measured",
            "rate_limits": {
                "five_hour": {"used_percentage": 10.0},
                "seven_day": {"used_percentage": 20.0},
            },
        },
        "opencode": {
            "tag": "measured",
            "pool": {
                "windows": {
                    "5h": {
                        "utilisation": 0.05,
                        "resets_at": (now + dt.timedelta(hours=1)).isoformat(),
                    },
                    "7d": {
                        "utilisation": 0.08,
                        "resets_at": (now + dt.timedelta(days=3)).isoformat(),
                    },
                    "30d": {
                        "utilisation": 0.04,
                        "resets_at": (now + dt.timedelta(days=20)).isoformat(),
                    },
                }
            },
        },
        "devin": {
            "tag": "measured",
            "status": "available" if devin_active else "disabled",
            "promo": {"active": devin_active},
        },
    }


def test_underused_codex_when_far_under_with_a_near_reset():
    report = pace.build(snapshot(), POLICY, NOW)
    pool = report["pools"]["codex"]
    assert pool["status"] == "underused"
    window = pool["windows"][0]
    assert window["name"] == "5h"
    assert round(window["used"], 2) == 0.05
    assert round(window["elapsed"], 2) == 0.80
    assert round(window["pace"], 2) == -0.75
    assert "underused" in report["line"]


def test_pool_over_pace_is_ahead():
    report = pace.build(snapshot(codex_primary=95.0), POLICY, NOW)
    assert report["pools"]["codex"]["status"] == "ahead"
    assert "ahead" in report["line"]


def test_unknown_is_not_exhausted():
    snap = snapshot()
    snap["codex"] = {"tag": "unknown", "rate_limits": None}
    report = pace.build(snap, POLICY, NOW)
    pool = report["pools"]["codex"]
    assert pool["status"] == "unknown"
    assert "not treated as exhausted" in pool["reason"]


def test_devin_promotion_reads_as_free():
    report = pace.build(snapshot(), POLICY, NOW)
    assert report["pools"]["devin"]["status"] == "free"
    assert "devin free" in report["line"]


def test_claude_without_resets_stays_unknown():
    report = pace.build(snapshot(), POLICY, NOW)
    pool = report["pools"]["claude"]
    assert pool["status"] == "unknown"
    assert pool["windows"][0]["used"] == 0.10


def test_pool_for_worker_names_the_quota_pool():
    assert pace.pool_for_worker("dev-codex") == "codex"
    assert pace.pool_for_worker("dev-devin") == "devin"
    assert pace.pool_for_worker("dev-opencode") == "opencode"
    assert pace.pool_for_worker("dev-lead") is None
