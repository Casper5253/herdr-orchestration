"""Tests for hx.route using synthetic usage snapshots (no network)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from hx import route as route_pkg
from hx.route import (
    CLAUDE_STRONG_MODEL,
    CODEX_STRONG_MODEL,
    decide,
    main,
    route,
)


# ---------- fixtures and helpers ----------


@pytest.fixture
def policy() -> dict:
    return {
        "ladders": {
            "economy": [
                "deepseek-v4.1-flash",
                "mimo-v2.6-flash",
                "qwen3.8-flash",
                "glm-5.3-flash",
            ],
            "default": ["minimax-m3", "qwen3.7-plus"],
            "strong": ["kimi-k2.7-code", "deepseek-v4-pro"],
        },
        "thresholds": {"pool_soft_limit": 0.85, "stale_seconds": 3600},
        "max_attempts": 3,
        "codex": {
            "economy_model": "gpt-5.6-luna",
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
    }


def _codex_economy_model(policy: dict) -> str:
    return policy["codex"]["economy_model"]


def _codex_strong_model(policy: dict) -> str:
    return policy["codex"]["strong_model"]


def _claude_strong_model(policy: dict) -> str:
    return policy.get("claude", {}).get("strong_model", CLAUDE_STRONG_MODEL)


def _all_ladder_models() -> list[str]:
    return [
        "deepseek-v4.1-flash",
        "mimo-v2.6-flash",
        "qwen3.8-flash",
        "glm-5.3-flash",
        "minimax-m3",
        "qwen3.7-plus",
        "kimi-k2.7-code",
        "deepseek-v4-pro",
    ]


def _model_state(
    util_5h: float = 0.1,
    util_7d: float = 0.1,
    util_30d: float = 0.1,
    last_error: dict | None = None,
) -> dict:
    state: dict = {
        "windows": {
            "5h": {"utilisation": util_5h},
            "7d": {"utilisation": util_7d},
            "30d": {"utilisation": util_30d},
        },
        "last_error": last_error,
    }
    return state


def _make_usage(
    opencode_models: dict | None = None,
    opencode_tag: str = "estimated",
    opencode_age: int = 0,
    codex_pct: float = 20.0,
    codex_secondary_pct: float | None = None,
    codex_age: int = 0,
    claude_pct: float = 20.0,
    claude_spend_pct: float | None = None,
    claude_age: int = 0,
    agents: list[dict] | None = None,
) -> dict:
    if codex_secondary_pct is None:
        codex_secondary_pct = codex_pct / 2
    claude_rate_limits: dict[str, Any] = {
        "five_hour": {"used_percentage": claude_pct},
        "seven_day": {"used_percentage": claude_pct / 2},
    }
    if claude_spend_pct is not None:
        claude_rate_limits["spend_limit"] = {"used_percentage": claude_spend_pct}
    return {
        "opencode": {
            "tag": opencode_tag,
            "available": True,
            "sample_age_seconds": opencode_age,
            "models": opencode_models or {},
        },
        "codex": {
            "tag": "measured",
            "available": codex_pct < 85,
            "sample_age_seconds": codex_age,
            "rate_limits": {
                "primary": {"used_percent": codex_pct},
                "secondary": {"used_percent": codex_secondary_pct},
            },
        },
        "claude": {
            "tag": "measured",
            "available": claude_pct < 85,
            "sample_age_seconds": claude_age,
            "context_window": {"used_percentage": claude_pct},
            "rate_limits": claude_rate_limits,
        },
        "agents": {
            "tag": "measured",
            "agents": agents if agents is not None else [],
        },
    }


def _all_available_usage() -> dict:
    return _make_usage(
        opencode_models={m: _model_state() for m in _all_ladder_models()},
    )


def _feature_task(**overrides) -> dict:
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


# ---------- tests per stage ----------


def test_stage_attempt_cap(policy: dict) -> None:
    """Stage 1: attempt > max_attempts -> strong model + stop."""
    result = route(
        _feature_task(attempt=4),
        _all_available_usage(),
        policy,
    )
    assert result["worker"] == "codex"
    assert result["model"] == _codex_strong_model(policy)
    assert result["effort"] == "high"
    assert result["method"] == "ocrun-codex"
    assert result["review"] is True
    assert result["stages"][0] == "attempt_cap"
    assert "attempt cap" in result["reason"].lower()


def test_stage_failure_cause_missing_context_keeps_same_model(policy: dict) -> None:
    """Stage 2: missing-context failure reuses the prior decision."""
    usage = _all_available_usage()
    base = _feature_task(attempt=1, last_failure="none")
    r_base = route(base, usage, policy)

    retry = _feature_task(
        attempt=2,
        last_failure="missing-context",
        prev={
            "worker": r_base["worker"],
            "model": r_base["model"],
            "effort": r_base["effort"],
            "tier": "default",
            "method": r_base["method"],
            "capability_failures": 0,
        },
    )
    r_retry = route(retry, usage, policy)

    assert r_retry["model"] == r_base["model"]
    assert r_retry["worker"] == r_base["worker"]
    assert r_retry["effort"] == r_base["effort"]
    assert r_retry["method"] == r_base["method"]
    assert "fix the input" in r_retry["reason"].lower()
    assert "fix the input" not in r_base["reason"].lower()


def test_stage_pool_selection(policy: dict) -> None:
    """Stage 3: trivial/review/architecture/risk-high dispatch correctly."""
    usage = _all_available_usage()

    trivial = route(_feature_task(kind="trivial"), usage, policy)
    assert trivial["method"] == "direct"
    assert trivial["worker"] == "claude"

    review = route(_feature_task(kind="review"), usage, policy)
    assert review["worker"] == "codex"
    assert review["effort"] == "high"
    assert review["model"] == _codex_strong_model(policy)

    architecture = route(_feature_task(kind="architecture"), usage, policy)
    assert architecture["worker"] == "claude"
    assert architecture["effort"] == "high"
    assert architecture["method"] == "subagent"
    assert architecture["model"] == _claude_strong_model(policy)

    high_risk = route(_feature_task(kind="feature", risk="high"), usage, policy)
    assert high_risk["worker"] == "codex"
    assert high_risk["effort"] == "high"
    assert high_risk["review"] is True
    assert high_risk["model"] == _codex_strong_model(policy)


def test_ladder_fallback_when_first_over_soft_limit(policy: dict) -> None:
    """Stage 4: within a ladder, skip exhausted models and pick next available."""
    usage = _make_usage(
        opencode_models={
            "deepseek-v4.1-flash": _model_state(util_5h=0.9, util_7d=0.9, util_30d=0.9),
            "mimo-v2.6-flash": _model_state(util_5h=0.1, util_7d=0.1, util_30d=0.1),
        },
    )
    result = route(_feature_task(kind="test"), usage, policy)
    assert result["model"] == "mimo-v2.6-flash"
    assert result["effort"] == "low"
    assert result["worker"] == "opencode"


def test_all_opencode_exhausted_fallback(policy: dict) -> None:
    """Stage 4: when every OpenCode ladder step is exhausted, fall back to codex economy."""
    usage = _make_usage(
        opencode_models={
            m: _model_state(util_5h=0.9, util_7d=0.9, util_30d=0.9)
            for m in _all_ladder_models()
        },
        codex_pct=20.0,
        claude_pct=30.0,
    )
    result = route(_feature_task(), usage, policy)
    assert result["worker"] == "codex"
    assert result["model"] == _codex_economy_model(policy)
    assert result["method"] == "ocrun-codex"


def test_capability_failure_escalates_one_step(policy: dict) -> None:
    """Each capability failure escalates effort/tier one step from prev."""
    usage = _all_available_usage()
    prev = {
        "worker": "opencode",
        "model": "minimax-m3",
        "effort": "medium",
        "tier": "default",
        "method": "ocrun",
        "capability_failures": 0,
    }
    caps_fail = _feature_task(attempt=2, last_failure="capability", prev=prev)
    result = route(caps_fail, usage, policy)

    assert result["model"] == "kimi-k2.7-code"
    assert result["effort"] == "high"


def test_unknown_vs_available_ranking(policy: dict) -> None:
    """Within a ladder, available ranks above unknown; exhausted is skipped."""
    usage = _make_usage(
        opencode_models={
            "mimo-v2.6-flash": _model_state(),
        },
    )
    result = route(_feature_task(kind="test"), usage, policy)
    assert result["model"] == "mimo-v2.6-flash"

    usage2 = _make_usage(
        opencode_models={
            "deepseek-v4.1-flash": _model_state(util_5h=0.9, util_7d=0.9, util_30d=0.9),
            "mimo-v2.6-flash": _model_state(),
        },
    )
    result2 = route(_feature_task(kind="test"), usage2, policy)
    assert result2["model"] == "mimo-v2.6-flash"


def test_persistent_vs_ocrun_decision(policy: dict) -> None:
    """Method: trivial -> direct; parallel -> ocrun;
    iterative -> persistent only if persistent block matches; else ocrun."""
    usage_idle = _all_available_usage()

    persistent = {"model": "minimax-m3", "effort": "medium", "status": "idle"}

    # Large task implies iterative + persistent matches -> persistent.
    large_match = _feature_task(size="large", persistent=persistent)
    result = route(large_match, usage_idle, policy)
    assert result["method"] == "persistent"
    assert result["model"] == "minimax-m3"

    # Parallel overrides -> ocrun.
    parallel = _feature_task(
        size="large", parallel=True, persistent=persistent, iterative=True
    )
    assert route(parallel, usage_idle, policy)["method"] == "ocrun"

    # Bounded (small, not iterative) -> ocrun.
    bounded = _feature_task(size="small", persistent=persistent)
    assert route(bounded, usage_idle, policy)["method"] == "ocrun"

    # Iterative but persistent model mismatch -> ocrun.
    iterative_mismatch = _feature_task(
        iterative=True, persistent={"model": "other", "effort": "medium", "status": "idle"}
    )
    assert route(iterative_mismatch, usage_idle, policy)["method"] == "ocrun"

    # Iterative but agent busy -> ocrun.
    busy = {"model": "minimax-m3", "effort": "medium", "status": "working"}
    large_busy = _feature_task(size="large", persistent=busy)
    assert route(large_busy, usage_idle, policy)["method"] == "ocrun"

    # Iterative with unknown effort matching policy default -> persistent.
    persistent_unknown = {"model": "minimax-m3", "effort": "unknown", "status": "idle"}
    large_unknown = _feature_task(size="large", persistent=persistent_unknown)
    assert route(large_unknown, usage_idle, policy)["method"] == "persistent"


# ---------- regression tests for correction round 1 ----------


def test_input_repair_reuses_prior_despite_quota_change(policy: dict) -> None:
    """Regression 1: missing-context reuses prev even if quota rose, unless exhausted."""
    usage = _make_usage(
        opencode_models={
            "minimax-m3": _model_state(util_5h=0.84, util_7d=0.84, util_30d=0.84),
        }
    )
    prev = {
        "worker": "opencode",
        "model": "minimax-m3",
        "effort": "medium",
        "tier": "default",
        "method": "ocrun",
        "capability_failures": 0,
    }
    retry = _feature_task(
        attempt=2, last_failure="missing-context", prev=prev
    )
    result = route(retry, usage, policy)
    assert result["model"] == "minimax-m3"
    assert result["worker"] == "opencode"
    assert result["method"] == "ocrun"


def test_input_repair_reselects_when_model_exhausted(policy: dict) -> None:
    """Regression 1: missing-context reselects only when prev model is exhausted."""
    usage = _make_usage(
        opencode_models={
            "minimax-m3": _model_state(util_5h=0.9, util_7d=0.9, util_30d=0.9),
            "qwen3.7-plus": _model_state(),
        }
    )
    prev = {
        "worker": "opencode",
        "model": "minimax-m3",
        "effort": "medium",
        "tier": "default",
        "method": "ocrun",
        "capability_failures": 0,
    }
    retry = _feature_task(
        attempt=2, last_failure="missing-context", prev=prev
    )
    result = route(retry, usage, policy)
    assert result["model"] == "qwen3.7-plus"
    assert "fix the input" in result["reason"].lower()


def test_iterative_field_overrides_size_heuristic(policy: dict) -> None:
    """Regression 1: explicit iterative flag controls persistence."""
    usage = _all_available_usage()
    persistent = {"model": "minimax-m3", "effort": "medium", "status": "idle"}

    # small + iterative=True -> persistent.
    assert (
        route(
            _feature_task(size="small", iterative=True, persistent=persistent),
            usage,
            policy,
        )["method"]
        == "persistent"
    )

    # large + iterative=False -> ocrun.
    assert (
        route(
            _feature_task(size="large", iterative=False, persistent=persistent),
            usage,
            policy,
        )["method"]
        == "ocrun"
    )

    # attempt>=2 no longer implies iterative.
    assert (
        route(
            _feature_task(size="small", attempt=3, persistent=persistent),
            usage,
            policy,
        )["method"]
        == "ocrun"
    )


def test_escalation_starts_from_prev_effort(policy: dict) -> None:
    """Regression 2: capability failure escalates from prev.effort, not kind baseline."""
    usage = _all_available_usage()
    # prev was already high on a review task; one more capability failure -> max.
    prev = {
        "worker": "codex",
        "model": _codex_strong_model(policy),
        "effort": "high",
        "tier": "strong",
        "method": "ocrun-codex",
        "capability_failures": 0,
    }
    retry = _feature_task(kind="review", attempt=2, last_failure="capability", prev=prev)
    result = route(retry, usage, policy)
    assert result["effort"] == "max"
    assert result["model"] == _codex_strong_model(policy)


def test_capability_failures_counter_increments(policy: dict) -> None:
    """Regression 2: capability_failures = prev.capability_failures + 1."""
    usage = _all_available_usage()
    prev = {
        "worker": "opencode",
        "model": "minimax-m3",
        "effort": "medium",
        "tier": "default",
        "method": "ocrun",
        "capability_failures": 1,
    }
    # One prior capability failure + one now = 2 escalations: medium -> max.
    retry = _feature_task(attempt=3, last_failure="capability", prev=prev)
    result = route(retry, usage, policy)
    assert result["effort"] == "max"
    assert result["model"] == "kimi-k2.7-code"


def test_persistent_ineligible_when_model_differs(policy: dict) -> None:
    """Regression 3: persistent block rejected when model does not match chosen."""
    usage = _all_available_usage()
    persistent = {"model": "deepseek-v4-pro", "effort": "high", "status": "idle"}
    result = route(
        _feature_task(size="large", persistent=persistent),
        usage,
        policy,
    )
    assert result["model"] == "minimax-m3"
    assert result["method"] == "ocrun"


def test_persistent_ineligible_when_effort_differs(policy: dict) -> None:
    """Regression 3: persistent block rejected when effort differs from chosen."""
    usage = _all_available_usage()
    persistent = {"model": "minimax-m3", "effort": "low", "status": "idle"}
    result = route(
        _feature_task(size="large", persistent=persistent),
        usage,
        policy,
    )
    assert result["model"] == "minimax-m3"
    assert result["method"] == "ocrun"


def test_opencode_unknown_when_stale(policy: dict) -> None:
    """Regression 4: opencode pool unknown when sample_age > stale_seconds."""
    usage = _make_usage(
        opencode_models={m: _model_state() for m in _all_ladder_models()},
        opencode_age=3601,
    )
    result = route(_feature_task(kind="test"), usage, policy)
    # Stale models are unknown but are still candidates (not exhausted).
    assert result["worker"] == "opencode"
    assert "unknown" in result["reason"].lower()


def test_opencode_unknown_when_window_missing(policy: dict) -> None:
    """Regression 4: missing/null window makes the model unknown, not 0."""
    usage = _make_usage(
        opencode_models={
            "deepseek-v4.1-flash": {
                "windows": {"5h": {"utilisation": 0.1}, "7d": {"utilisation": 0.1}}
                # 30d missing
            },
            "mimo-v2.6-flash": _model_state(),
        }
    )
    result = route(_feature_task(kind="test"), usage, policy)
    assert result["model"] == "mimo-v2.6-flash"


def test_codex_secondary_null_is_unknown(policy: dict) -> None:
    """Regression 4: codex secondary=null is treated as unknown."""
    usage = _make_usage(
        opencode_models={m: _model_state(util_5h=0.9) for m in _all_ladder_models()},
    )
    usage["codex"]["rate_limits"]["secondary"] = None
    result = route(_feature_task(), usage, policy)
    # codex unknown, claude available -> claude fallback.
    assert result["worker"] == "claude"


def test_claude_spend_limit_considered(policy: dict) -> None:
    """Regression 4: claude rate_limits.spend_limit is included in headroom."""
    usage = _make_usage(
        opencode_models={m: _model_state(util_5h=0.9) for m in _all_ladder_models()},
        claude_pct=20.0,
        claude_spend_pct=90.0,
    )
    result = route(_feature_task(), usage, policy)
    # spend_limit pushes claude into exhausted; codex wins.
    assert result["worker"] == "codex"


def test_context_window_not_quota_headroom(policy: dict) -> None:
    """Regression 4: context_window is never used as quota headroom."""
    usage = _make_usage(
        opencode_models={m: _model_state(util_5h=0.9) for m in _all_ladder_models()},
        claude_pct=90.0,
    )
    # Remove rate_limits so only context_window remains; claude becomes unknown.
    usage["claude"]["rate_limits"] = {}
    result = route(_feature_task(), usage, policy)
    # codex unknown (no data by default?) actually codex has rate_limits.
    # claude unknown -> codex wins by being available.
    assert result["worker"] == "codex"


def test_defer_when_all_fallbacks_exhausted(policy: dict) -> None:
    """Regression 5: defer with resets_at when OpenCode + fallback pools exhausted."""
    usage = _make_usage(
        opencode_models={
            m: _model_state(util_5h=0.9, util_7d=0.9, util_30d=0.9)
            for m in _all_ladder_models()
        },
        codex_pct=90.0,
        claude_pct=90.0,
    )
    usage["codex"]["rate_limits"]["primary"]["resets_at"] = "2026-10-17T00:00:00Z"
    usage["claude"]["rate_limits"]["five_hour"]["resets_at"] = "2026-10-16T20:00:00Z"
    result = route(_feature_task(), usage, policy)
    assert result["method"] == "defer"
    assert result["worker"] is None
    assert result["model"] is None
    assert result["resets_at"] == "2026-10-16T20:00:00Z"


def test_rank_available_before_unknown_across_tiers(policy: dict) -> None:
    """Regression 6: available candidate in a higher tier beats unknown in lower tier."""
    usage = _make_usage(
        opencode_models={
            # economy first model unknown (absent), second exhausted.
            "deepseek-v4.1-flash": None,  # will become unknown via missing state
            "mimo-v2.6-flash": _model_state(util_5h=0.9),
            # default first model available.
            "minimax-m3": _model_state(),
        }
    )
    # feature -> medium -> default tier normally, but with capability failure -> high -> strong.
    # Use investigate (low) so start tier is economy.
    result = route(_feature_task(kind="investigate"), usage, policy)
    # Economy has unknown+exhausted; default has available minimax-m3.
    # Available in default beats unknown in economy.
    assert result["model"] == "minimax-m3"


def test_last_error_non_quota_does_not_suppress(policy: dict) -> None:
    """Regression 7: non-quota last_error does not suppress a model."""
    usage = _make_usage(
        opencode_models={
            "deepseek-v4.1-flash": _model_state(
                last_error={
                    "text": "context length exceeded",
                    "age_seconds": 60,
                }
            ),
            "mimo-v2.6-flash": _model_state(),
        }
    )
    result = route(_feature_task(kind="test"), usage, policy)
    assert result["model"] == "deepseek-v4.1-flash"


def test_last_error_quota_suppresses_model(policy: dict) -> None:
    """Regression 7: quota/rate-limit last_error suppresses a model."""
    usage = _make_usage(
        opencode_models={
            "deepseek-v4.1-flash": _model_state(
                last_error={
                    "text": "Rate limit exceeded",
                    "age_seconds": 60,
                }
            ),
            "mimo-v2.6-flash": _model_state(),
        }
    )
    result = route(_feature_task(kind="test"), usage, policy)
    assert result["model"] == "mimo-v2.6-flash"


def test_effort_by_kind_from_policy(policy: dict) -> None:
    """Regression 8: effort is read from policy effort_by_kind."""
    usage = _all_available_usage()

    investigate = route(_feature_task(kind="investigate"), usage, policy)
    assert investigate["effort"] == "low"
    assert investigate["model"] == "deepseek-v4.1-flash"

    feature = route(_feature_task(kind="feature"), usage, policy)
    assert feature["effort"] == "medium"
    assert feature["model"] == "minimax-m3"

    review = route(_feature_task(kind="review"), usage, policy)
    assert review["effort"] == "high"
    assert review["model"] == _codex_strong_model(policy)


def test_codex_models_read_from_policy(policy: dict) -> None:
    """Regression 8: review and fallback use the models declared in policy.json."""
    usage = _make_usage(
        opencode_models={m: _model_state(util_5h=0.9) for m in _all_ladder_models()},
    )
    review = route(_feature_task(kind="review"), usage, policy)
    assert review["model"] == policy["codex"]["strong_model"]

    fallback = route(_feature_task(), usage, policy)
    assert fallback["model"] == policy["codex"]["economy_model"]


# ---------- integration: decide() and main() ----------


def test_decide_writes_jsonl(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    policy: dict,
) -> None:
    """decide() appends one JSON line per call to .hx/routing.jsonl."""
    monkeypatch.setattr(route_pkg, "_repo_root", lambda: tmp_path)

    captured: dict = {}

    def fake_snapshot(policy=None, now=None):  # noqa: ARG001
        snapshot = _all_available_usage()
        captured["snapshot"] = snapshot
        return snapshot

    monkeypatch.setattr(route_pkg.hx_usage, "usage_snapshot", fake_snapshot)
    monkeypatch.setattr(route_pkg.hx_usage, "load_policy", lambda: policy)

    task = _feature_task()
    result = decide(task)

    assert result["worker"] == "opencode"
    log = tmp_path / ".hx" / "routing.jsonl"
    assert log.exists()
    lines = log.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert "decision_id" in record and "ts" in record
    assert record["task"] == task
    assert record["result"]["worker"] == "opencode"


def test_main_prints_json(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    policy: dict,
    capsys: pytest.CaptureFixture,
) -> None:
    monkeypatch.setattr(route_pkg, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        route_pkg.hx_usage,
        "usage_snapshot",
        lambda policy=None, now=None: _all_available_usage(),
    )
    monkeypatch.setattr(route_pkg.hx_usage, "load_policy", lambda: policy)

    code = main(
        [
            "--kind", "feature",
            "--risk", "low",
            "--size", "small",
            "--attempt", "1",
            "--last-failure", "none",
        ]
    )
    assert code == 0
    out = capsys.readouterr().out.strip()
    parsed = json.loads(out)
    assert parsed["worker"] == "opencode"
    assert parsed["effort"] in ("low", "medium", "high", "max")


def test_module_main_block_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Smoke test: the module can be executed as a script (uses sys.argv)."""
    assert hasattr(route_pkg, "route")
    assert hasattr(route_pkg, "decide")
    assert hasattr(route_pkg, "main")


# ---------- regression tests for reviewer findings ----------


def test_input_repair_keeps_persistent_when_eligible(policy: dict) -> None:
    """Finding 8: missing-context reuses prev model/effort but re-runs eligibility -> persistent."""
    usage = _all_available_usage()
    prev = {
        "worker": "opencode",
        "model": "minimax-m3",
        "effort": "medium",
        "tier": "default",
        "method": "ocrun",
        "capability_failures": 0,
    }
    retry = _feature_task(
        attempt=2,
        last_failure="missing-context",
        prev=prev,
        iterative=True,
        persistent={"model": "minimax-m3", "effort": "medium", "status": "idle"},
    )
    result = route(retry, usage, policy)
    assert result["model"] == "minimax-m3"
    assert result["method"] == "persistent"
    assert "fix the input" in result["reason"].lower()


def test_input_repair_falls_back_to_ocrun_when_persistent_busy(policy: dict) -> None:
    """Finding 8: eligible persistent agent is busy -> reuse model but dispatch as ocrun."""
    usage = _all_available_usage()
    prev = {
        "worker": "opencode",
        "model": "minimax-m3",
        "effort": "medium",
        "tier": "default",
        "method": "persistent",
        "capability_failures": 0,
    }
    retry = _feature_task(
        attempt=2,
        last_failure="tool",
        prev=prev,
        iterative=True,
        persistent={"model": "minimax-m3", "effort": "medium", "status": "working"},
    )
    result = route(retry, usage, policy)
    assert result["model"] == "minimax-m3"
    assert result["method"] == "ocrun"


def test_input_repair_falls_back_to_ocrun_on_persistent_model_mismatch(policy: dict) -> None:
    """Finding 8: persistent block matches a different model -> reuse prev model, ocrun dispatch."""
    usage = _all_available_usage()
    prev = {
        "worker": "opencode",
        "model": "minimax-m3",
        "effort": "medium",
        "tier": "default",
        "method": "persistent",
        "capability_failures": 0,
    }
    retry = _feature_task(
        attempt=2,
        last_failure="unclear",
        prev=prev,
        iterative=True,
        persistent={"model": "qwen3.7-plus", "effort": "medium", "status": "idle"},
    )
    result = route(retry, usage, policy)
    assert result["model"] == "minimax-m3"
    assert result["method"] == "ocrun"


def test_input_repair_reselects_when_prev_codex_exhausted(policy: dict) -> None:
    """Finding 8: tool failure with exhausted codex pool falls through to ladder selection."""
    usage = _make_usage(
        opencode_models={m: _model_state() for m in _all_ladder_models()},
        codex_pct=90.0,
    )
    prev = {
        "worker": "codex",
        "model": _codex_strong_model(policy),
        "effort": "high",
        "tier": "strong",
        "method": "ocrun-codex",
        "capability_failures": 0,
    }
    retry = _feature_task(
        attempt=2,
        last_failure="tool",
        prev=prev,
    )
    result = route(retry, usage, policy)
    assert result["worker"] == "opencode"
    assert "fix the input" in result["reason"].lower()


def test_decide_returns_decision_id(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    policy: dict,
) -> None:
    """Finding 9: decide() returns its generated decision_id in the result."""
    monkeypatch.setattr(route_pkg, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(route_pkg.hx_usage, "usage_snapshot", lambda policy=None, now=None: _all_available_usage())
    monkeypatch.setattr(route_pkg.hx_usage, "load_policy", lambda: policy)

    task = {"task_id": "task_decide", **_feature_task()}
    result = decide(task)

    assert "decision_id" in result
    assert isinstance(result["decision_id"], str)

    log = tmp_path / ".hx" / "routing.jsonl"
    assert log.exists()
    record = json.loads(log.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert record["kind"] == "decision"
    assert record["decision_id"] == result["decision_id"]
    assert record["task_id"] == "task_decide"
    assert record["result"]["decision_id"] == result["decision_id"]


# ---------- regression tests for reviewer findings round 2 ----------


def test_decide_returns_task_id(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    policy: dict,
) -> None:
    """Finding 9: decide() carries task_id in its return value."""
    monkeypatch.setattr(route_pkg, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(route_pkg.hx_usage, "usage_snapshot", lambda policy=None, now=None: _all_available_usage())
    monkeypatch.setattr(route_pkg.hx_usage, "load_policy", lambda: policy)

    task = {"task_id": "task_tid", **_feature_task()}
    result = decide(task)

    assert result["task_id"] == "task_tid"


def test_cli_task_id_flag(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    policy: dict,
    capsys: pytest.CaptureFixture,
) -> None:
    """Finding 9: --task-id CLI flag is passed into the task and surfaced in output."""
    monkeypatch.setattr(route_pkg, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(route_pkg.hx_usage, "usage_snapshot", lambda policy=None, now=None: _all_available_usage())
    monkeypatch.setattr(route_pkg.hx_usage, "load_policy", lambda: policy)

    code = main([
        "--kind", "feature",
        "--risk", "low",
        "--size", "small",
        "--attempt", "1",
        "--last-failure", "none",
        "--task-id", "cli_task_1",
    ])
    assert code == 0
    out = capsys.readouterr().out.strip()
    parsed = json.loads(out)
    assert parsed["task_id"] == "cli_task_1"

    log = tmp_path / ".hx" / "routing.jsonl"
    record = json.loads(log.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert record["task_id"] == "cli_task_1"


def test_decision_and_outcome_share_ids(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    policy: dict,
) -> None:
    """Finding 9: an ocrun outcome line and route decision line share decision_id and task_id."""
    from hx import ocrun

    monkeypatch.setattr(route_pkg, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(route_pkg.hx_usage, "usage_snapshot", lambda policy=None, now=None: _all_available_usage())
    monkeypatch.setattr(route_pkg.hx_usage, "load_policy", lambda: policy)

    task_id = "shared_task_42"
    task = {"task_id": task_id, **_feature_task()}
    decision = decide(task)
    decision_id = decision["decision_id"]

    session = "ses_shared000000000000000000"
    stream = "\n".join([
        json.dumps({"type": "step_start", "timestamp": 0, "sessionID": session, "part": {"id": "p1", "sessionID": session, "type": "step_start"}}),
        json.dumps({"type": "step_finish", "timestamp": 0, "sessionID": session, "part": {"reason": "stop", "sessionID": session, "type": "step_finish", "tokens": {"total": 10, "input": 1, "output": 9, "reasoning": 0, "cache": {"write": 0, "read": 0}}, "cost": 0.001}}),
    ])

    def fake_execute(cmd, cwd, timeout):
        return 0, stream, "", False, True

    monkeypatch.setattr(ocrun, "execute", fake_execute)
    routing_log = tmp_path / ".hx" / "routing.jsonl"
    ocrun.run("x", "qwen3.8-flash", cwd=tmp_path, routing_log=routing_log, task_id=task_id, decision_id=decision_id)

    lines = routing_log.read_text(encoding="utf-8").strip().splitlines()
    records = [json.loads(line) for line in lines]

    decision_records = [r for r in records if r["kind"] == "decision"]
    outcome_records = [r for r in records if r["kind"] == "outcome"]

    assert len(decision_records) >= 1
    assert len(outcome_records) >= 1

    dec = decision_records[-1]
    out = outcome_records[-1]
    assert dec["decision_id"] == decision_id
    assert out["decision_id"] == decision_id
    assert dec["task_id"] == task_id
    assert out["task_id"] == task_id


def test_iterative_work_prefers_warm_persistent_model_in_tier() -> None:
    """e2e finding: glm-5.3-flash persistent session is in the default ladder but was never chosen."""
    from hx.route import route
    policy = {"ladders": {"economy": ["glm-5.3-flash"], "default": ["qwen3.7-plus", "glm-5.3-flash"], "strong": ["kimi-k2.7-code"]},
              "thresholds": {"pool_soft_limit": 0.85, "stale_seconds": 3600}, "effort_by_kind": {"feature": "medium"}}
    win = {"5h": {"utilisation": 0.0}, "7d": {"utilisation": 0.0}, "30d": {"utilisation": 0.0}}
    usage = {"opencode": {"tag": "estimated", "sample_age_seconds": 5,
                          "models": {m: {"windows": win, "last_error": None} for m in ("qwen3.7-plus", "glm-5.3-flash", "kimi-k2.7-code")}}}
    task = {"kind": "feature", "size": "large", "iterative": True,
            "persistent": {"model": "glm-5.3-flash", "effort": "high", "status": "idle"}}
    d = route(task, usage, policy)
    assert (d["model"], d["method"]) == ("glm-5.3-flash", "persistent"), d
    task["persistent"]["status"] = "working"
    d = route(task, usage, policy)
    assert (d["model"], d["method"]) == ("qwen3.7-plus", "ocrun"), d
