"""hx.route: deterministic task router.

Staged routing: attempt cap -> failure cause -> pool -> model ladder.
``route(task, usage, policy)`` is a pure function. ``decide(task)`` loads
``policy.json``, snapshots ``hx.usage`` and appends one JSON line to
``.hx/routing.jsonl``. ``main(argv)`` parses CLI flags and prints the result.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hx import paths
from hx import usage as hx_usage


DEFAULT_MAX_ATTEMPTS = 3
RATE_LIMIT_WINDOW_SECONDS = 30 * 60
DEFAULT_CODEX_ECONOMY_MODEL = "gpt-5.6-luna"
DEFAULT_CODEX_STRONG_MODEL = "gpt-6.1-sol"
CODEX_STRONG_MODEL = DEFAULT_CODEX_STRONG_MODEL
CLAUDE_STRONG_MODEL = "sonnet"
PERSISTENT_AGENT_NAME = "hx-opencode"
WINDOWS: tuple[str, ...] = ("5h", "7d", "30d")
TIER_ORDER: tuple[str, ...] = ("economy", "default", "strong")
EFFORT_TO_TIER: dict[str, str] = {
    "low": "economy",
    "medium": "default",
    "high": "strong",
    "max": "strong",
}


def _repo_root() -> Path:
    return paths.project_root()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _codex_economy_model(policy: dict[str, Any]) -> str:
    return policy.get("codex", {}).get("economy_model", DEFAULT_CODEX_ECONOMY_MODEL)


def _codex_strong_model(policy: dict[str, Any]) -> str:
    return policy.get("codex", {}).get("strong_model", DEFAULT_CODEX_STRONG_MODEL)


def _claude_strong_model(policy: dict[str, Any]) -> str:
    return policy.get("claude", {}).get("strong_model", CLAUDE_STRONG_MODEL)


def _initial_effort(task: dict[str, Any], policy: dict[str, Any]) -> str:
    kind = task.get("kind")
    effort = policy.get("effort_by_kind", {}).get(kind or "", policy.get("default_effort", "medium"))
    if task.get("risk") == "high":
        effort = "high"
    return effort


def _apply_capability_escalation(effort: str, capability_failures: int) -> str:
    if capability_failures <= 0:
        return effort
    order = ["low", "medium", "high", "max"]
    idx = order.index(effort) if effort in order else 1
    return order[min(idx + capability_failures, len(order) - 1)]


def _tier_for_effort(effort: str) -> str:
    return EFFORT_TO_TIER.get(effort, "default")


def _is_quota_error(last_error: dict[str, Any] | None) -> bool:
    if not last_error:
        return False
    text = last_error.get("message", "") or last_error.get("text", "") or ""
    text = text.lower()
    patterns = ("rate limit", "429", "quota", "usage limit", "insufficient balance")
    return any(p in text for p in patterns)


def _model_status_opencode(
    model_id: str, oc: dict[str, Any], policy: dict[str, Any]
) -> tuple[str, float]:
    """Return ('available'|'exhausted'|'unknown', max utilisation 0..1)."""
    thresholds = policy.get("thresholds", {})
    soft_limit = thresholds.get("pool_soft_limit", 0.85)
    stale_seconds = thresholds.get("stale_seconds", 3600)

    sample_age = oc.get("sample_age_seconds")
    if sample_age is None or sample_age > stale_seconds:
        return "unknown", 0.0
    if oc.get("tag") == "unknown":
        return "unknown", 0.0

    state = oc.get("models", {}).get(model_id)
    if state is None:
        return "unknown", 0.0

    last_error = state.get("last_error")
    if _is_quota_error(last_error):
        age = 10**9
        if isinstance(last_error, dict):
            age = last_error.get("age_seconds", 10**9)
        if age < RATE_LIMIT_WINDOW_SECONDS:
            return "exhausted", 1.0

    max_util = None
    for win_name in WINDOWS:
        win = state.get("windows", {}).get(win_name)
        if win is None:
            return "unknown", 0.0
        util = win.get("utilisation")
        if util is None:
            return "unknown", 0.0
        if util >= soft_limit:
            return "exhausted", util
        if max_util is None or util > max_util:
            max_util = util

    return "available", max_util if max_util is not None else 0.0


def _pick_opencode(
    start_tier: str, oc: dict[str, Any], policy: dict[str, Any]
) -> tuple[str | None, str, float, list[str]]:
    """Rank candidates across all reachable ladder tiers.

    All available candidates (in tier order, then ladder order) are ranked
    before any unknown candidate.
    """
    if start_tier not in TIER_ORDER:
        start_tier = TIER_ORDER[0]
    idx = TIER_ORDER.index(start_tier)

    available: list[tuple[int, int, float, str]] = []
    unknown: list[tuple[int, int, float, str]] = []
    tried: list[str] = []

    for tier_idx in range(idx, len(TIER_ORDER)):
        tier = TIER_ORDER[tier_idx]
        tried.append(tier)
        ladder = policy.get("ladders", {}).get(tier, [])
        for model_idx, model_id in enumerate(ladder):
            status, util = _model_status_opencode(model_id, oc, policy)
            if status == "exhausted":
                continue
            candidate = (tier_idx, model_idx, util, model_id)
            if status == "available":
                available.append(candidate)
            else:
                unknown.append(candidate)

    if available:
        candidates = available
        status = "available"
    elif unknown:
        candidates = unknown
        status = "unknown"
    else:
        return None, "", 0.0, tried

    candidates.sort()
    _, _, util, model_id = candidates[0]
    return model_id, status, util, tried


def _pool_status(pool: dict[str, Any], policy: dict[str, Any]) -> tuple[str, float]:
    """Return ('available'|'exhausted'|'unknown', max utilisation 0..1)."""
    thresholds = policy.get("thresholds", {})
    soft_limit = thresholds.get("pool_soft_limit", 0.85)
    stale_seconds = thresholds.get("stale_seconds", 3600)

    sample_age = pool.get("sample_age_seconds")
    if sample_age is None or sample_age > stale_seconds:
        return "unknown", 1.0
    tag = pool.get("tag")
    if tag == "unknown":
        return "unknown", 1.0

    rl = pool.get("rate_limits", {})
    max_util = None

    if "primary" in rl:
        primary_obj = rl.get("primary")
        if not primary_obj:
            return "unknown", 1.0
        primary = primary_obj.get("used_percent")
        if primary is None:
            return "unknown", 1.0
        secondary_obj = rl.get("secondary")
        if secondary_obj is None:
            return "unknown", 1.0
        secondary = secondary_obj.get("used_percent", 0) / 100.0
        max_util = max(primary / 100.0, secondary)
    elif "five_hour" in rl or "seven_day" in rl or "spend_limit" in rl:
        values: list[float] = []
        for key in ("five_hour", "seven_day", "spend_limit"):
            obj = rl.get(key)
            if obj is None:
                continue
            val = obj.get("used_percentage")
            if val is None:
                return "unknown", 1.0
            values.append(val / 100.0)
        if not values:
            return "unknown", 1.0
        max_util = max(values)
    else:
        return "unknown", 1.0

    if max_util >= soft_limit:
        return "exhausted", max_util
    return "available", max_util


def _collect_resets(usage: dict[str, Any], policy: dict[str, Any]) -> list[str]:
    resets: list[str] = []
    oc = usage.get("opencode", {})
    for tier in policy.get("ladders", {}).values():
        for model_id in tier:
            state = oc.get("models", {}).get(model_id)
            if not state:
                continue
            for win_name in WINDOWS:
                win = state.get("windows", {}).get(win_name)
                if isinstance(win, dict) and win.get("resets_at"):
                    resets.append(win["resets_at"])
            last_error = state.get("last_error")
            if isinstance(last_error, dict) and last_error.get("resets_at"):
                resets.append(last_error["resets_at"])
    for pool_name in ("codex", "claude"):
        rl = usage.get(pool_name, {}).get("rate_limits", {})
        for obj in rl.values():
            if isinstance(obj, dict) and obj.get("resets_at"):
                resets.append(obj["resets_at"])
    return resets


def _fallback_decision(
    usage: dict[str, Any], policy: dict[str, Any], effort: str, stages: list[str]
) -> dict[str, Any]:
    codex_status, codex_util = _pool_status(usage.get("codex", {}), policy)
    claude_status, claude_util = _pool_status(usage.get("claude", {}), policy)

    if codex_status == "exhausted" and claude_status == "exhausted":
        resets = _collect_resets(usage, policy)
        return {
            "worker": None,
            "model": None,
            "effort": effort,
            "method": "defer",
            "reason": "opencode ladder exhausted; all fallback pools exhausted",
            "stages": stages + ["fallback:defer"],
            "resets_at": min(resets) if resets else None,
        }

    rank = {"available": 0, "unknown": 1, "exhausted": 2}

    codex_rank = rank[codex_status]
    claude_rank = rank[claude_status]

    if codex_rank < claude_rank:
        pool = "codex"
    elif claude_rank < codex_rank:
        pool = "claude"
    elif codex_util <= claude_util:
        pool = "codex"
    else:
        pool = "claude"

    if pool == "codex":
        return {
            "worker": "codex",
            "model": _codex_economy_model(policy),
            "effort": effort,
            "method": "ocrun-codex",
            "reason": (
                f"opencode ladder exhausted; codex fallback "
                f"({codex_status}, util {codex_util:.2f})"
            ),
            "stages": stages + ["fallback:codex"],
        }
    return {
        "worker": "claude",
        "model": _claude_strong_model(policy),
        "effort": effort,
        "method": "subagent",
        "reason": (
            f"opencode ladder exhausted; claude subagent fallback "
            f"({claude_status}, util {claude_util:.2f})"
        ),
        "stages": stages + ["fallback:claude"],
    }


def _persistent_eligible(
    task: dict[str, Any], chosen_model: str, chosen_effort: str, policy: dict[str, Any]
) -> bool:
    persistent = task.get("persistent")
    if not persistent:
        return False
    if persistent.get("status") != "idle":
        return False
    if persistent.get("model") != chosen_model:
        return False
    p_effort = persistent.get("effort")
    order = ["low", "medium", "high", "max"]
    if p_effort == chosen_effort or (
        p_effort in order and chosen_effort in order and order.index(p_effort) >= order.index(chosen_effort)
    ):
        return True
    if p_effort == "unknown" and chosen_effort == policy.get("default_effort", "medium"):
        return True
    return False


def _warm_persistent_model(
    task: dict[str, Any], tier: str, effort: str, oc: dict[str, Any], policy: dict[str, Any]
) -> tuple[str, str, float] | None:
    """(model, status, util) of the idle persistent session if it can take iterative work."""
    iterative = task.get("iterative")
    if iterative is None:
        iterative = task.get("size") == "large"
    persistent = task.get("persistent") or {}
    model = persistent.get("model")
    if not iterative or task.get("parallel") or persistent.get("status") != "idle" or not model:
        return None
    if model not in policy.get("ladders", {}).get(tier, []):
        return None
    order = ["low", "medium", "high", "max"]
    p_effort = persistent.get("effort")
    if p_effort in order and order.index(p_effort) < order.index(effort):
        return None
    status, util = _model_status_opencode(model, oc, policy)
    if status != "available":
        return None
    return model, status, util


def _decide_method(
    task: dict[str, Any], chosen_model: str, chosen_effort: str, policy: dict[str, Any]
) -> str:
    kind = task.get("kind")
    if kind == "trivial":
        return "direct"
    if task.get("parallel"):
        return "ocrun"
    iterative = task.get("iterative")
    if iterative is None:
        iterative = task.get("size") == "large"
    if iterative and _persistent_eligible(task, chosen_model, chosen_effort, policy):
        return "persistent"
    return "ocrun"


def _prev_model_exhausted(
    prev: dict[str, Any], usage: dict[str, Any], policy: dict[str, Any]
) -> bool:
    worker = prev.get("worker")
    if worker == "opencode":
        model_id = prev.get("model")
        if not model_id:
            return True
        status, _ = _model_status_opencode(model_id, usage.get("opencode", {}), policy)
        return status == "exhausted"
    if worker in ("codex", "claude"):
        status, _ = _pool_status(usage.get(worker, {}), policy)
        return status == "exhausted"
    return True


def route(task: dict[str, Any], usage: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    stages: list[str] = []

    # Stage 1: attempt cap.
    max_attempts = int(policy.get("max_attempts", DEFAULT_MAX_ATTEMPTS))
    attempt = int(task.get("attempt", 0))
    if attempt > max_attempts:
        stages.append("attempt_cap")
        return {
            "worker": "codex",
            "model": _codex_strong_model(policy),
            "effort": "high",
            "method": "ocrun-codex",
            "reason": (
                f"attempt cap exceeded (attempt={attempt} > {max_attempts}); "
                "stop and report"
            ),
            "stages": stages,
            "review": True,
        }
    stages.append("attempt_cap_ok")

    # Stage 2: failure cause.
    prev = task.get("prev")
    last_failure = task.get("last_failure", "none")
    fix_input = last_failure in ("missing-context", "unclear", "tool")
    capability_failure = last_failure == "capability"
    stages.append("failure_cause")

    # Input repair reuses the prior worker/model/effort unless the model is
    # exhausted, but re-runs dispatch eligibility (persistent vs ocrun).
    if fix_input and prev:
        if not _prev_model_exhausted(prev, usage, policy):
            chosen_worker = prev.get("worker")
            chosen_model = prev.get("model")
            chosen_effort = prev.get("effort")

            if chosen_worker == "opencode":
                method = _decide_method(task, chosen_model, chosen_effort, policy)
            elif chosen_worker == "codex":
                method = "ocrun-codex"
            elif chosen_worker == "claude":
                method = "subagent"
            else:
                method = prev.get("method")

            decision = {
                "worker": chosen_worker,
                "model": chosen_model,
                "effort": chosen_effort,
                "method": method,
                "reason": (
                    f"retry after input failure; reuse prior decision; "
                    f"last_failure={last_failure} -> fix the input, not the model"
                ),
                "stages": stages + ["failure_cause:fix_input"],
            }
            return decision

    # Compute effort starting from prev or kind baseline.
    if prev:
        base_effort = prev.get("effort", "medium")
        capability_failures = prev.get("capability_failures", 0)
    else:
        base_effort = _initial_effort(task, policy)
        capability_failures = 0
    if capability_failure:
        capability_failures += 1
    effort = _apply_capability_escalation(base_effort, capability_failures)

    # Stage 3: pool selection.
    kind = task.get("kind")
    oc = usage.get("opencode", {})
    decision: dict[str, Any]

    if kind == "trivial":
        stages.append("pool:direct")
        decision = {
            "worker": "claude",
            "model": None,
            "effort": effort,
            "method": "direct",
            "reason": "trivial -> lead does it directly",
            "stages": stages,
        }
    elif task.get("risk") == "high":
        stages.append("pool:codex")
        decision = {
            "worker": "codex",
            "model": _codex_strong_model(policy),
            "effort": effort,
            "method": "ocrun-codex",
            "reason": "risk=high -> codex (codex review required)",
            "stages": stages,
            "review": True,
        }
    elif kind == "review":
        stages.append("pool:codex")
        decision = {
            "worker": "codex",
            "model": _codex_strong_model(policy),
            "effort": effort,
            "method": "ocrun-codex",
            "reason": "review -> codex",
            "stages": stages,
        }
    elif kind == "architecture":
        stages.append("pool:claude")
        decision = {
            "worker": "claude",
            "model": _claude_strong_model(policy),
            "effort": effort,
            "method": "subagent",
            "reason": "architecture -> claude subagent",
            "stages": stages,
        }
    else:
        stages.append("pool:opencode")
        start_tier = _tier_for_effort(effort)
        stages.append(f"tier:{start_tier}")
        model_id, status, util, tried = _pick_opencode(start_tier, oc, policy)
        warm = _warm_persistent_model(task, start_tier, effort, oc, policy)
        if warm is not None and model_id is not None:
            # Iterative work prefers the warm persistent session when its model is in the
            # chosen tier and available: reusing cached context beats ladder order.
            stages.append("persistent:warm")
            model_id, status, util = warm
        if model_id is None:
            for t in tried:
                stages.append(f"tried:{t}")
            decision = _fallback_decision(usage, policy, effort, stages)
        else:
            stages.append(f"model:{model_id}")
            method = _decide_method(task, model_id, effort, policy)
            decision = {
                "worker": "opencode",
                "model": model_id,
                "effort": effort,
                "method": method,
                "reason": (
                    f"opencode ladder ({start_tier}) -> {model_id} "
                    f"({status}, max util {util:.2f})"
                ),
                "stages": stages,
            }

    if fix_input:
        decision["reason"] = (
            decision.get("reason", "")
            + f"; last_failure={last_failure} -> fix the input, not the model"
        )
        decision["stages"] = list(decision.get("stages", stages)) + ["failure_cause:fix_input"]

    return decision


def decide(task: dict[str, Any]) -> dict[str, Any]:
    policy = hx_usage.load_policy()
    snapshot = hx_usage.usage_snapshot(policy=policy)
    result = route(task, snapshot, policy)

    decision_id = str(uuid.uuid4())
    task_id = task.get("task_id")
    result["decision_id"] = decision_id
    result["task_id"] = task_id

    ts = _now().isoformat()
    record = {
        "kind": "decision",
        "decision_id": decision_id,
        "task_id": task_id,
        "ts": ts,
        "task": task,
        "result": result,
    }

    hx_dir = _repo_root() / ".hx"
    hx_dir.mkdir(parents=True, exist_ok=True)
    with open(hx_dir / "routing.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")

    return result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hx route")
    parser.add_argument("--kind", required=True)
    parser.add_argument("--risk", default="low")
    parser.add_argument("--size", default="small")
    parser.add_argument("--attempt", type=int, default=0)
    parser.add_argument("--last-failure", default="none")
    parser.add_argument("--parallel", action="store_true")
    parser.add_argument("--prev", default=None)
    parser.add_argument("--persistent", default=None)
    parser.add_argument("--iterative", action="store_true")
    parser.add_argument("--task-id", default=None, help="correlation id for the task")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    prev = json.loads(args.prev) if args.prev else None
    persistent = json.loads(args.persistent) if args.persistent else None
    task = {
        "kind": args.kind,
        "risk": args.risk,
        "size": args.size,
        "attempt": args.attempt,
        "last_failure": args.last_failure,
        "parallel": args.parallel,
        "prev": prev,
        "persistent": persistent,
        "iterative": args.iterative,
        "task_id": args.task_id,
    }
    result = decide(task)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
