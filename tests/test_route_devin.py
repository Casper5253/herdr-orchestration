"""Tests for the devin branch of hx.route (synthetic usage, no network)."""

from __future__ import annotations

import pytest

from hx.route import route


@pytest.fixture
def policy() -> dict:
    return {
        "ladders": {
            "economy": ["deepseek-v4.1-flash", "mimo-v2.6-flash"],
            "default": ["minimax-m3", "qwen3.7-plus"],
            "strong": ["kimi-k2.7-code"],
        },
        "thresholds": {"pool_soft_limit": 0.85, "stale_seconds": 3600},
        "max_attempts": 3,
        "codex": {
            "economy_model": "gpt-6-luna",
            "strong_model": "gpt-6.1-sol",
        },
        "effort_by_kind": {
            "trivial": "low",
            "routine": "low",
            "test": "low",
            "investigate": "low",
            "feature": "medium",
            "debug": "medium",
            "refactor": "medium",
            "review": "high",
            "architecture": "high",
        },
        "default_effort": "medium",
        "devin": {
            "enabled": True,
            "kinds": ["routine", "investigate", "feature", "debug", "refactor", "test"],
            "models": {
                "low": "swe-2-high",
                "medium": "swe-2-high",
                "high": "swe-2-max",
                "max": "swe-2-max",
            },
            "allowed_models": ["swe-2-medium", "swe-2-high", "swe-2-max"],
            "promo": {"ends_at": "2026-11-30T10:00:00Z"},
        },
    }


def _model_state(last_error: dict | None = None) -> dict:
    return {
        "windows": {
            "5h": {"spend": 0.1},
            "7d": {"spend": 0.1},
            "30d": {"spend": 0.1},
        },
        "last_error": last_error,
    }


def _make_usage(devin_status: str | None = "available") -> dict:
    usage = {
        "opencode": {
            "tag": "estimated",
            "available": True,
            "sample_age_seconds": 0,
            "pool": {"windows": {
                "5h": {"utilisation": 0.1},
                "7d": {"utilisation": 0.1},
                "30d": {"utilisation": 0.1},
            }},
            "models": {
                m: _model_state()
                for m in (
                    "deepseek-v4.1-flash",
                    "mimo-v2.6-flash",
                    "minimax-m3",
                    "qwen3.7-plus",
                    "kimi-k2.7-code",
                )
            },
        },
        "codex": {
            "tag": "measured",
            "available": True,
            "sample_age_seconds": 0,
            "rate_limits": {
                "primary": {"used_percent": 20.0},
                "secondary": {"used_percent": 10.0},
            },
        },
        "claude": {
            "tag": "measured",
            "available": True,
            "sample_age_seconds": 0,
            "context_window": {"used_percentage": 20.0},
            "rate_limits": {
                "five_hour": {"used_percentage": 20.0},
                "seven_day": {"used_percentage": 10.0},
            },
        },
    }
    if devin_status is not None:
        usage["devin"] = {
            "status": devin_status,
            "promo": {"ends_at": "2026-11-30T10:00:00Z"},
        }
    return usage


def _task(**overrides) -> dict:
    task = {
        "kind": "feature",
        "risk": "low",
        "size": "small",
        "attempt": 1,
        "last_failure": "none",
        "parallel": False,
        "prev": None,
        "persistent": None,
        "iterative": None,
    }
    task.update(overrides)
    return task


@pytest.mark.parametrize(
    "kind,effort",
    [
        ("routine", "low"),
        ("investigate", "low"),
        ("test", "low"),
        ("feature", "medium"),
        ("debug", "medium"),
        ("refactor", "medium"),
    ],
)
def test_implementation_kinds_go_to_devin(policy: dict, kind: str, effort: str) -> None:
    """Free SWE-2 is the primary implementation worker: low/medium -> swe-2-high."""
    result = route(_task(kind=kind), _make_usage(), policy)
    assert result["worker"] == "devin"
    assert result["model"] == "swe-2-high"
    assert result["effort"] == effort
    assert result["method"] == "ocrun-devin"
    assert "pool:devin" in result["stages"]


def test_high_effort_maps_to_swe2_max(policy: dict) -> None:
    """A capability failure escalates effort medium -> high -> swe-2-max on devin."""
    prev = {
        "worker": "opencode",
        "model": "minimax-m3",
        "effort": "medium",
        "method": "ocrun",
        "capability_failures": 0,
    }
    task = _task(attempt=2, last_failure="capability", prev=prev)
    result = route(task, _make_usage(), policy)
    assert result["worker"] == "devin"
    assert result["model"] == "swe-2-max"
    assert result["effort"] == "high"
    assert result["method"] == "ocrun-devin"


def test_non_implementation_pools_unchanged(policy: dict) -> None:
    """review/architecture/risk=high/trivial never reach the devin branch."""
    usage = _make_usage()

    review = route(_task(kind="review"), usage, policy)
    assert review["worker"] == "codex"
    assert review["model"] == policy["codex"]["strong_model"]
    assert review["method"] == "ocrun-codex"

    architecture = route(_task(kind="architecture"), usage, policy)
    assert architecture["worker"] == "claude"
    assert architecture["method"] == "subagent"

    high_risk = route(_task(kind="feature", risk="high"), usage, policy)
    assert high_risk["worker"] == "codex"
    assert high_risk["review"] is True

    trivial = route(_task(kind="trivial"), usage, policy)
    assert trivial["worker"] == "claude"
    assert trivial["method"] == "direct"


@pytest.mark.parametrize(
    "devin_status,stage",
    [
        ("busy", "devin:busy"),
        ("disabled", "devin:disabled"),
        ("exhausted", "devin:exhausted"),
        (None, "devin:absent"),
    ],
)
def test_devin_unavailable_falls_through_to_opencode(
    policy: dict, devin_status: str | None, stage: str
) -> None:
    result = route(_task(), _make_usage(devin_status=devin_status), policy)
    assert result["worker"] == "opencode"
    assert result["model"] == "minimax-m3"
    assert result["method"] == "ocrun"
    assert stage in result["stages"]


def test_capability_failure_on_swe2_max_goes_codex_strong(policy: dict) -> None:
    """A capability failure on the strongest SWE-2 model escalates to Codex."""
    prev = {
        "worker": "devin",
        "model": "swe-2-max",
        "effort": "high",
        "method": "ocrun-devin",
        "capability_failures": 0,
    }
    task = _task(attempt=2, last_failure="capability", prev=prev)
    result = route(task, _make_usage(), policy)
    assert result["worker"] == "codex"
    assert result["model"] == policy["codex"]["strong_model"]
    assert result["effort"] == "high"
    assert result["method"] == "ocrun-codex"
    assert "devin:capability-at-max" in result["stages"]


def test_capability_failure_on_swe2_high_retries_devin_stronger(policy: dict) -> None:
    """A capability failure below the strongest model retries devin one step up."""
    prev = {
        "worker": "devin",
        "model": "swe-2-high",
        "effort": "medium",
        "method": "ocrun-devin",
        "capability_failures": 0,
    }
    task = _task(attempt=2, last_failure="capability", prev=prev)
    result = route(task, _make_usage(), policy)
    assert result["worker"] == "devin"
    assert result["model"] == "swe-2-max"
    assert result["effort"] == "high"
    assert result["method"] == "ocrun-devin"


def test_fix_input_retry_keeps_devin(policy: dict) -> None:
    """missing-context reuses the prior devin decision unchanged."""
    prev = {
        "worker": "devin",
        "model": "swe-2-high",
        "effort": "medium",
        "method": "ocrun-devin",
        "capability_failures": 0,
    }
    task = _task(attempt=2, last_failure="missing-context", prev=prev)
    result = route(task, _make_usage(), policy)
    assert result["worker"] == "devin"
    assert result["model"] == "swe-2-high"
    assert result["effort"] == "medium"
    assert result["method"] == "ocrun-devin"
    assert "fix the input" in result["reason"].lower()


def test_fix_input_retry_reselects_when_devin_exhausted(policy: dict) -> None:
    """_prev_model_exhausted treats a devin status outside available/busy as gone."""
    prev = {
        "worker": "devin",
        "model": "swe-2-high",
        "effort": "medium",
        "method": "ocrun-devin",
        "capability_failures": 0,
    }
    task = _task(attempt=2, last_failure="missing-context", prev=prev)
    result = route(task, _make_usage(devin_status="exhausted"), policy)
    assert result["worker"] == "opencode"
    assert result["model"] == "minimax-m3"


def test_fix_input_retry_reselects_when_devin_busy(policy: dict) -> None:
    """A busy devin pool cannot take the retry either: _prev_model_exhausted
    counts anything but available as gone, so the task re-dispatches."""
    prev = {
        "worker": "devin",
        "model": "swe-2-high",
        "effort": "medium",
        "method": "ocrun-devin",
        "capability_failures": 0,
    }
    task = _task(attempt=2, last_failure="tool", prev=prev)
    result = route(task, _make_usage(devin_status="busy"), policy)
    assert result["worker"] != "devin"
    assert result["worker"] == "opencode"
    assert result["model"] == "minimax-m3"
    assert "devin:busy" in result["stages"]
