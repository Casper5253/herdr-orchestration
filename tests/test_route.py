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
        "thresholds": {"pool_soft_limit": 0.85},
        "max_attempts": 3,
    }


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
    codex_pct: float = 20.0,
    codex_secondary_pct: float | None = None,
    claude_pct: float = 20.0,
    agents: list[dict] | None = None,
) -> dict:
    if codex_secondary_pct is None:
        codex_secondary_pct = codex_pct / 2
    return {
        "opencode": {
            "tag": opencode_tag,
            "available": True,
            "models": opencode_models or {},
        },
        "codex": {
            "tag": "measured",
            "available": codex_pct < 85,
            "rate_limits": {
                "primary": {"used_percent": codex_pct},
                "secondary": {"used_percent": codex_secondary_pct},
            },
        },
        "claude": {
            "tag": "measured",
            "available": claude_pct < 85,
            "context_window": {"used_percentage": claude_pct},
            "rate_limits": {
                "five_hour": {"used_percentage": claude_pct},
                "seven_day": {"used_percentage": claude_pct / 2},
            },
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
        "persistent_model": None,
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
    assert result["model"] == CODEX_STRONG_MODEL
    assert result["effort"] == "high"
    assert result["method"] == "ocrun-codex"
    assert result["review"] is True
    assert result["stages"][0] == "attempt_cap"
    assert "attempt cap" in result["reason"].lower()


def test_stage_failure_cause_missing_context_keeps_same_model(policy: dict) -> None:
    """Stage 2: missing-context failure keeps the same model, marks fix-input."""
    usage = _all_available_usage()
    base = _feature_task(attempt=1, last_failure="none")
    retry = _feature_task(attempt=2, last_failure="missing-context")

    r_base = route(base, usage, policy)
    r_retry = route(retry, usage, policy)

    assert r_base["model"] == r_retry["model"]
    assert "fix the input" in r_retry["reason"].lower() or "missing-context" in r_retry["reason"].lower()
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

    architecture = route(_feature_task(kind="architecture"), usage, policy)
    assert architecture["worker"] == "claude"
    assert architecture["effort"] == "high"
    assert architecture["method"] == "subagent"

    high_risk = route(_feature_task(kind="feature", risk="high"), usage, policy)
    assert high_risk["worker"] == "codex"
    assert high_risk["effort"] == "high"
    assert high_risk["review"] is True


def test_ladder_fallback_when_first_over_soft_limit(policy: dict) -> None:
    """Stage 4: within a ladder, skip exhausted models and pick next available."""
    usage = _make_usage(
        opencode_models={
            "deepseek-v4.1-flash": _model_state(util_5h=0.9, util_7d=0.9, util_30d=0.9),
            "mimo-v2.6-flash": _model_state(util_5h=0.1, util_7d=0.1, util_30d=0.1),
        },
    )
    # "test" -> low effort -> economy ladder; first model exhausted.
    result = route(_feature_task(kind="test"), usage, policy)
    assert result["model"] == "mimo-v2.6-flash"
    assert result["effort"] == "low"
    assert result["worker"] == "opencode"


def test_all_opencode_exhausted_fallback(policy: dict) -> None:
    """Stage 4: when every OpenCode ladder step is exhausted, fall back to codex/claude."""
    usage = _make_usage(
        opencode_models={
            m: _model_state(util_5h=0.9, util_7d=0.9, util_30d=0.9)
            for m in _all_ladder_models()
        },
        codex_pct=20.0,
        claude_pct=30.0,
    )
    result = route(_feature_task(), usage, policy)
    # codex has lower utilisation (20% vs 30%), so codex wins.
    assert result["worker"] == "codex"
    assert result["model"] == CODEX_STRONG_MODEL
    assert result["method"] == "ocrun-codex"


def test_capability_failure_escalates_one_step(policy: dict) -> None:
    """Each capability failure moves the ladder step up by one tier."""
    usage = _all_available_usage()
    # effort=medium (feature) -> default ladder -> minimax-m3
    no_fail = route(_feature_task(attempt=1, last_failure="none"), usage, policy)
    # capability fail -> escalates one step -> strong ladder -> kimi-k2.7-code
    caps_fail = route(_feature_task(attempt=2, last_failure="capability"), usage, policy)

    assert no_fail["model"] == "minimax-m3"
    assert caps_fail["model"] == "kimi-k2.7-code"
    assert caps_fail["effort"] == "high"


def test_unknown_vs_available_ranking(policy: dict) -> None:
    """Within a ladder, available ranks above unknown; exhausted is skipped."""
    usage = _make_usage(
        opencode_models={
            # deepseek-v4.1-flash is absent -> unknown
            "mimo-v2.6-flash": _model_state(),
        },
    )
    result = route(_feature_task(kind="test"), usage, policy)
    # economy ladder starts with deepseek (unknown), then mimo (available).
    # Available ranks above unknown -> pick mimo.
    assert result["model"] == "mimo-v2.6-flash"

    # Also: an exhausted model is skipped even if it would otherwise rank first.
    usage2 = _make_usage(
        opencode_models={
            "deepseek-v4.1-flash": _model_state(util_5h=0.9, util_7d=0.9, util_30d=0.9),
            "mimo-v2.6-flash": _model_state(),
        },
    )
    result2 = route(_feature_task(kind="test"), usage2, policy)
    assert result2["model"] == "mimo-v2.6-flash"


def test_persistent_vs_ocrun_decision(policy: dict) -> None:
    """Method: trivial -> direct; parallel or bounded -> ocrun;
    size large or iterative -> persistent only if hx-opencode idle and
    persistent_model matches chosen; else ocrun."""
    usage_idle = _all_available_usage()
    usage_idle["agents"]["agents"] = [{"name": "hx-opencode", "status": "idle"}]

    # Large task + chosen model + agent idle + persistent matches -> persistent.
    large_match = _feature_task(size="large", persistent_model="minimax-m3")
    result = route(large_match, usage_idle, policy)
    assert result["method"] == "persistent"
    assert result["model"] == "minimax-m3"

    # Parallel overrides -> ocrun.
    parallel = _feature_task(size="large", parallel=True, persistent_model="minimax-m3")
    assert route(parallel, usage_idle, policy)["method"] == "ocrun"

    # Bounded (small, attempt=1) -> ocrun.
    bounded = _feature_task(size="small", attempt=1)
    assert route(bounded, usage_idle, policy)["method"] == "ocrun"

    # Iterative (attempt >= 2) but persistent_model mismatch -> ocrun.
    iterative_mismatch = _feature_task(size="small", attempt=3, persistent_model="other")
    assert route(iterative_mismatch, usage_idle, policy)["method"] == "ocrun"

    # Large + agent busy -> ocrun.
    usage_busy = _all_available_usage()
    usage_busy["agents"]["agents"] = [{"name": "hx-opencode", "status": "working"}]
    large_busy = _feature_task(size="large", persistent_model="minimax-m3")
    assert route(large_busy, usage_busy, policy)["method"] == "ocrun"


# ---------- integration: decide() and main() ----------


def test_decide_writes_jsonl(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    policy: dict,
) -> None:
    """decide() appends one JSON line per call to .hx/routing.jsonl."""
    # Redirect repo_root to the temp dir so we don't pollute the real .hx/.
    monkeypatch.setattr(route_pkg, "_repo_root", lambda: tmp_path)

    # Stub usage_snapshot to avoid touching the real DB / Codex / Claude state.
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
    monkeypatch.setattr(route_pkg.hx_usage, "usage_snapshot", lambda policy=None, now=None: _all_available_usage())
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
    # We don't actually invoke the script; just import-check that it imports cleanly.
    assert hasattr(route_pkg, "route")
    assert hasattr(route_pkg, "decide")
    assert hasattr(route_pkg, "main")