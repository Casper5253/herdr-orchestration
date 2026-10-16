"""hx.route: deterministic task router.

Stages (first match wins): attempt cap -> failure cause -> pool -> model ladder.
``route(task, usage, policy)`` is a pure function. ``decide(task)`` is the thin
wrapper that loads ``policy.json``, snapshots ``hx.usage`` and appends one JSON
line to ``.hx/routing.jsonl``. ``main(argv)`` parses CLI flags and prints the
result as JSON; the lead wires it into ``hx/__main__.py``.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hx import usage as hx_usage


DEFAULT_MAX_ATTEMPTS = 3
RATE_LIMIT_WINDOW_SECONDS = 30 * 60
CODEX_STRONG_MODEL = "gpt-5.6-luna"
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
KIND_EFFORT: dict[str, str] = {
    "trivial": "low",
    "routine": "low",
    "test": "low",
    "investigate": "medium",
    "feature": "medium",
    "debug": "medium",
    "refactor": "medium",
    "review": "high",
    "architecture": "high",
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _initial_effort(task: dict[str, Any]) -> str:
    kind = task.get("kind")
    effort = KIND_EFFORT.get(kind or "", "medium")
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


def _model_status_opencode(
    model_id: str, oc: dict[str, Any], soft_limit: float
) -> tuple[str, float]:
    """Return ('available'|'exhausted'|'unknown', max utilisation 0..1)."""
    if oc.get("tag") == "unknown":
        return "unknown", 0.0
    state = oc.get("models", {}).get(model_id)
    if state is None:
        return "unknown", 0.0
    last_error = state.get("last_error")
    if (
        last_error is not None
        and last_error.get("age_seconds", 10**9) < RATE_LIMIT_WINDOW_SECONDS
    ):
        return "exhausted", 1.0
    max_util = 0.0
    for win_name in WINDOWS:
        util = state.get("windows", {}).get(win_name, {}).get("utilisation", 0.0)
        if util >= soft_limit:
            return "exhausted", util
        if util > max_util:
            max_util = util
    return "available", max_util


def _pick_opencode_in_tier(
    tier: str, oc: dict[str, Any], policy: dict[str, Any]
) -> tuple[str | None, str, float]:
    soft_limit = policy.get("thresholds", {}).get("pool_soft_limit", 0.85)
    ladder = policy.get("ladders", {}).get(tier, [])
    candidates: list[tuple[int, float, int, str]] = []
    for i, model_id in enumerate(ladder):
        status, util = _model_status_opencode(model_id, oc, soft_limit)
        if status == "exhausted":
            continue
        rank = 0 if status == "available" else 1
        candidates.append((rank, util, i, model_id))
    if not candidates:
        return None, "", 0.0
    candidates.sort()
    rank, util, _, model_id = candidates[0]
    return model_id, "available" if rank == 0 else "unknown", util


def _pick_opencode(
    start_tier: str, oc: dict[str, Any], policy: dict[str, Any]
) -> tuple[str | None, str, float, list[str]]:
    """Try start_tier, then escalate one step at a time up to strong."""
    if start_tier not in TIER_ORDER:
        start_tier = TIER_ORDER[0]
    idx = TIER_ORDER.index(start_tier)
    tried: list[str] = []
    for i in range(idx, len(TIER_ORDER)):
        tier = TIER_ORDER[i]
        tried.append(tier)
        model_id, status, util = _pick_opencode_in_tier(tier, oc, policy)
        if model_id is not None:
            return model_id, status, util, tried
    return None, "", 0.0, tried


def _pool_status(pool: dict[str, Any], soft_limit: float) -> tuple[str, float]:
    """Return ('available'|'exhausted'|'unknown', max utilisation 0..1)."""
    tag = pool.get("tag")
    if tag == "unknown":
        return "unknown", 1.0
    rl = pool.get("rate_limits", {})
    if "primary" in rl:
        primary = rl.get("primary", {}).get("used_percent", 0) / 100.0
        secondary = rl.get("secondary", {}).get("used_percent", 0) / 100.0
        max_util = max(primary, secondary)
    elif "five_hour" in rl:
        five_hour = rl.get("five_hour", {}).get("used_percentage", 0) / 100.0
        seven_day = rl.get("seven_day", {}).get("used_percentage", 0) / 100.0
        ctx = pool.get("context_window", {}).get("used_percentage", 0) / 100.0
        max_util = max(five_hour, seven_day, ctx)
    else:
        return "unknown", 1.0
    if max_util >= soft_limit:
        return "exhausted", max_util
    return "available", max_util


def _fallback_decision(
    usage: dict[str, Any], policy: dict[str, Any], effort: str, stages: list[str]
) -> dict[str, Any]:
    soft_limit = policy.get("thresholds", {}).get("pool_soft_limit", 0.85)
    codex_status, codex_util = _pool_status(usage.get("codex", {}), soft_limit)
    claude_status, claude_util = _pool_status(usage.get("claude", {}), soft_limit)

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
            "model": CODEX_STRONG_MODEL,
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
        "model": CLAUDE_STRONG_MODEL,
        "effort": effort,
        "method": "subagent",
        "reason": (
            f"opencode ladder exhausted; claude subagent fallback "
            f"({claude_status}, util {claude_util:.2f})"
        ),
        "stages": stages + ["fallback:claude"],
    }


def _agent_status(usage: dict[str, Any], name: str) -> str | None:
    agents = usage.get("agents", {}).get("agents", [])
    for a in agents:
        if a.get("name") == name:
            return a.get("status")
    return None


def _decide_method(task: dict[str, Any], usage: dict[str, Any], chosen_model: str) -> str:
    kind = task.get("kind")
    if kind == "trivial":
        return "direct"
    if task.get("parallel"):
        return "ocrun"
    size = task.get("size")
    attempt = task.get("attempt", 0)
    bounded = size != "large" and attempt <= 1
    if bounded:
        return "ocrun"
    iterative = size == "large" or attempt >= 2
    if iterative:
        agent_status = _agent_status(usage, PERSISTENT_AGENT_NAME)
        persistent_model = task.get("persistent_model")
        if agent_status == "idle" and persistent_model == chosen_model:
            return "persistent"
        return "ocrun"
    return "ocrun"


def route(task: dict[str, Any], usage: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    stages: list[str] = []

    # Stage 1: attempt cap.
    max_attempts = int(policy.get("max_attempts", DEFAULT_MAX_ATTEMPTS))
    attempt = int(task.get("attempt", 0))
    if attempt > max_attempts:
        stages.append("attempt_cap")
        return {
            "worker": "codex",
            "model": CODEX_STRONG_MODEL,
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
    last_failure = task.get("last_failure", "none")
    capability_failures = 1 if last_failure == "capability" else 0
    fix_input = last_failure in ("missing-context", "unclear", "tool")
    stages.append("failure_cause")

    # Stage 3: pool selection.
    kind = task.get("kind")
    risk = task.get("risk")
    oc = usage.get("opencode", {})
    decision: dict[str, Any]

    if kind == "trivial":
        stages.append("pool:direct")
        effort = _initial_effort(task)
        decision = {
            "worker": "claude",
            "model": None,
            "effort": effort,
            "method": "direct",
            "reason": "trivial -> lead does it directly",
            "stages": stages,
        }
    elif risk == "high":
        stages.append("pool:codex")
        effort = _apply_capability_escalation("high", capability_failures)
        decision = {
            "worker": "codex",
            "model": CODEX_STRONG_MODEL,
            "effort": effort,
            "method": "ocrun-codex",
            "reason": "risk=high -> codex (effort high, codex review required)",
            "stages": stages,
            "review": True,
        }
    elif kind == "review":
        stages.append("pool:codex")
        effort = _apply_capability_escalation("high", capability_failures)
        decision = {
            "worker": "codex",
            "model": CODEX_STRONG_MODEL,
            "effort": effort,
            "method": "ocrun-codex",
            "reason": "review -> codex (effort high)",
            "stages": stages,
        }
    elif kind == "architecture":
        stages.append("pool:claude")
        effort = _apply_capability_escalation("high", capability_failures)
        decision = {
            "worker": "claude",
            "model": CLAUDE_STRONG_MODEL,
            "effort": effort,
            "method": "subagent",
            "reason": "architecture -> claude subagent (effort high)",
            "stages": stages,
        }
    else:
        # OpenCode pool.
        stages.append("pool:opencode")
        effort = _initial_effort(task)
        effort = _apply_capability_escalation(effort, capability_failures)
        start_tier = _tier_for_effort(effort)
        stages.append(f"tier:{start_tier}")
        model_id, status, util, tried = _pick_opencode(start_tier, oc, policy)
        if model_id is None:
            for t in tried:
                stages.append(f"tried:{t}")
            decision = _fallback_decision(usage, policy, effort, stages)
        else:
            stages.append(f"model:{model_id}")
            method = _decide_method(task, usage, model_id)
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
    ts = _now().isoformat()
    record = {
        "decision_id": decision_id,
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
    parser.add_argument("--persistent-model", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    task = {
        "kind": args.kind,
        "risk": args.risk,
        "size": args.size,
        "attempt": args.attempt,
        "last_failure": args.last_failure,
        "parallel": args.parallel,
        "persistent_model": args.persistent_model,
    }
    result = decide(task)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))