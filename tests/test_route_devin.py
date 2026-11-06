"""Tests for the devin branch of hx.route (synthetic usage, no network)."""

from __future__ import annotations

import pytest

from hx import route as route_pkg
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
        "routing_order": ["devin", "opencode"],
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
    """With devin first in routing_order: low/medium -> swe-2-high."""
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


# ---------- routing order: OpenCode first, Devin as fallback ----------


def _with_order(policy: dict, order: list[str] | None) -> dict:
    copy = dict(policy)
    if order is None:
        copy.pop("routing_order", None)
    else:
        copy["routing_order"] = list(order)
    return copy


def test_default_order_picks_opencode_deepseek_first(policy: dict) -> None:
    """policy.routing_order defaults to opencode before devin."""
    result = route(_task(kind="test"), _make_usage(), _with_order(policy, None))
    assert result["worker"] == "opencode"
    assert result["model"] == "deepseek-v4.1-flash"
    assert result["method"] == "ocrun"
    assert "pool:devin" not in result["stages"]


def test_devin_chosen_when_opencode_exhausted(policy: dict) -> None:
    """Devin is the fallback when the shared OpenCode pool is spent."""
    usage = _make_usage()
    usage["opencode"]["pool"]["windows"]["7d"]["utilisation"] = 0.9
    result = route(_task(kind="feature"), usage, _with_order(policy, ["opencode", "devin"]))
    assert result["worker"] == "devin"
    assert result["model"] == "swe-2-high"
    assert result["method"] == "ocrun-devin"
    assert "pool:opencode" in result["stages"]


def test_routing_order_read_from_policy(policy: dict) -> None:
    """The order is data: swapping it swaps the first worker."""
    usage = _make_usage()
    opencode_first = route(
        _task(kind="test"), usage, _with_order(policy, ["opencode", "devin"])
    )
    devin_first = route(
        _task(kind="test"), usage, _with_order(policy, ["devin", "opencode"])
    )
    assert opencode_first["worker"] == "opencode"
    assert opencode_first["model"] == "deepseek-v4.1-flash"
    assert devin_first["worker"] == "devin"
    assert devin_first["model"] == "swe-2-high"


# ---------- routing_balance: Devin and OpenCode equally until the promo ends ----------


def _with_balance(policy: dict, until: str = "2999-01-01T00:00:00Z") -> dict:
    copy = dict(policy)
    copy.pop("routing_order", None)
    copy["routing_balance"] = {
        "mode": "balanced",
        "workers": ["devin", "opencode"],
        "until": until,
        "then": "opencode",
        "window_seconds": 86400,
    }
    return copy


def _with_counts(usage: dict, devin: int, opencode: int) -> dict:
    copy = dict(usage)
    copy["routing"] = {"recent": {"devin": devin, "opencode": opencode}}
    return copy


def test_balance_both_idle_more_devin_routes_picks_opencode(policy: dict) -> None:
    usage = _with_counts(_make_usage(), devin=5, opencode=1)
    result = route(_task(kind="test"), usage, _with_balance(policy))
    assert result["worker"] == "opencode"
    assert result["model"] == "deepseek-v4.1-flash"
    assert result["method"] == "ocrun"
    assert "fewer recent routes" in result["reason"]
    assert "opencode" in result["reason"]
    assert "balance:chosen:opencode" in result["stages"]


def test_balance_both_idle_more_opencode_routes_picks_devin(policy: dict) -> None:
    usage = _with_counts(_make_usage(), devin=1, opencode=5)
    result = route(_task(kind="feature"), usage, _with_balance(policy))
    assert result["worker"] == "devin"
    assert result["model"] == "swe-2-high"
    assert result["method"] == "ocrun-devin"
    assert "fewer recent routes" in result["reason"]
    assert "devin" in result["reason"]
    assert "balance:chosen:devin" in result["stages"]


def test_balance_devin_busy_picks_opencode(policy: dict) -> None:
    usage = _with_counts(_make_usage(devin_status="busy"), devin=0, opencode=9)
    result = route(_task(kind="test"), usage, _with_balance(policy))
    assert result["worker"] == "opencode"
    assert result["model"] == "deepseek-v4.1-flash"
    assert "devin busy or unavailable" in result["reason"]


def test_balance_opencode_unavailable_picks_devin(policy: dict) -> None:
    usage = _make_usage()
    for window in ("5h", "7d", "30d"):
        usage["opencode"]["pool"]["windows"][window]["utilisation"] = 0.9
    usage = _with_counts(usage, devin=9, opencode=0)
    result = route(_task(kind="feature"), usage, _with_balance(policy))
    assert result["worker"] == "devin"
    assert result["model"] == "swe-2-high"
    assert "opencode busy or unavailable" in result["reason"]


def test_balance_after_until_picks_opencode(policy: dict) -> None:
    usage = _with_counts(_make_usage(), devin=0, opencode=9)
    result = route(
        _task(kind="test"), usage, _with_balance(policy, until="2020-01-01T00:00:00Z")
    )
    assert result["worker"] == "opencode"
    assert result["model"] == "deepseek-v4.1-flash"
    assert "balanced period ended" in result["reason"]
    assert "balance:then:opencode" in result["stages"]


def test_decide_counts_recent_log_routes(
    monkeypatch: pytest.MonkeyPatch, tmp_path, policy: dict
) -> None:
    """decide() reads .hx/routing.jsonl, so the tie-break sees real history."""
    import json
    from datetime import datetime, timezone

    monkeypatch.setattr(route_pkg, "_repo_root", lambda: tmp_path)
    log = tmp_path / ".hx" / "routing.jsonl"
    log.parent.mkdir(parents=True)
    now = datetime.now(timezone.utc).isoformat()
    with open(log, "w", encoding="utf-8") as fh:
        for _ in range(3):
            fh.write(json.dumps({"kind": "decision", "ts": now, "result": {"worker": "devin"}}) + "\n")
        fh.write(json.dumps({"kind": "decision", "ts": now, "result": {"worker": "opencode"}}) + "\n")
    monkeypatch.setattr(route_pkg.hx_usage, "load_policy", lambda: _with_balance(policy))
    monkeypatch.setattr(
        route_pkg.hx_usage, "usage_snapshot", lambda policy=None, now=None: _make_usage()
    )
    result = route_pkg.decide(_task(kind="feature"))
    assert result["worker"] == "opencode"


def test_balance_keeps_capability_escalation_to_codex(policy: dict) -> None:
    """Balancing does not swallow the SWE-2 max capability escalation."""
    prev = {
        "worker": "devin",
        "model": "swe-2-max",
        "effort": "high",
        "method": "ocrun-devin",
        "capability_failures": 0,
    }
    usage = _with_counts(_make_usage(), devin=0, opencode=9)
    task = _task(kind="feature", attempt=2, last_failure="capability", prev=prev)
    result = route(task, usage, _with_balance(policy))
    assert result["worker"] == "codex"
    assert result["model"] == policy["codex"]["strong_model"]
    assert "devin:capability-at-max" in result["stages"]


def test_balance_keeps_capability_retry_on_devin_before_max(policy: dict) -> None:
    prev = {"worker": "devin", "model": "swe-2-high", "effort": "medium"}
    result = route(
        _task(kind="feature", attempt=1, last_failure="capability", prev=prev),
        _with_counts(_make_usage(), devin=9, opencode=0),
        _with_balance(policy),
    )
    assert (result["worker"], result["model"]) == ("devin", "swe-2-max")


def test_balance_end_blocks_input_retry_with_pre_cutoff_snapshot(policy: dict) -> None:
    prev = {"worker": "devin", "model": "swe-2-high", "effort": "medium"}
    result = route(
        _task(kind="feature", attempt=1, last_failure="tool", prev=prev),
        _make_usage(),
        _with_balance(policy, until="2020-01-01T00:00:00Z"),
    )
    assert result["worker"] == "opencode"
    assert "balanced period ended" in result["reason"]


@pytest.mark.parametrize("result", ["incomplete record", ["partial"], {"worker": []}])
def test_balance_log_ignores_invalid_result_shape(tmp_path, result) -> None:
    import json
    from datetime import datetime, timezone

    log = tmp_path / "routing.jsonl"
    log.write_text(json.dumps({
        "kind": "decision", "ts": datetime.now(timezone.utc).isoformat(),
        "result": result,
    }) + "\n", encoding="utf-8")
    assert route_pkg.recent_route_counts(log, 86400) == {"devin": 0, "opencode": 0}


def test_balance_keeps_opencode_capability_retry(policy: dict) -> None:
    prev = {"worker": "opencode", "model": "deepseek-v4.1-flash", "effort": "low"}
    result = route(
        _task(kind="test", attempt=1, last_failure="capability", prev=prev),
        _with_counts(_make_usage(), devin=0, opencode=9),
        _with_balance(policy),
    )
    assert (result["worker"], result["model"], result["effort"]) == (
        "opencode", "minimax-m3", "medium",
    )


def test_balance_end_keeps_max_capability_escalation(policy: dict) -> None:
    prev = {"worker": "devin", "model": "swe-2-max", "effort": "high"}
    result = route(
        _task(kind="feature", attempt=1, last_failure="capability", prev=prev),
        _make_usage(devin_status="expired"),
        _with_balance(policy, until="2020-01-01T00:00:00Z"),
    )
    assert (result["worker"], result["model"]) == ("codex", policy["codex"]["strong_model"])
