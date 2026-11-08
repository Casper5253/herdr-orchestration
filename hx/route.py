"""hx.route: deterministic task router.

Staged routing: attempt cap -> failure cause -> pool -> model ladder.
While ``policy.routing_balance`` is active the pool stage balances Devin and
OpenCode (idle first, then fewer recent decision lines). ``route(task, usage,
policy)`` is a pure function. ``decide(task)`` loads ``policy.json``, snapshots
``hx.usage`` and appends one JSON line to ``.hx/routing.jsonl``. ``main(argv)``
parses CLI flags and prints the result.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from hx import capability
from hx import devin as hx_devin
from hx import paths
from hx import usage as hx_usage


DEFAULT_MAX_ATTEMPTS = 3
RATE_LIMIT_WINDOW_SECONDS = 30 * 60
DEFAULT_BALANCE_WINDOW_SECONDS = 24 * 60 * 60
BALANCED_WORKERS: tuple[str, ...] = ("devin", "opencode")
DEFAULT_CODEX_ECONOMY_MODEL = "gpt-6-luna"
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


def _variant_for_opencode(model: str | None, effort: str | None, policy: dict[str, Any]) -> str | None:
    """Per-model --variant: the effort, but only when this model declares it.

    ``policy.opencode_variants`` (measured with ``opencode models --verbose``)
    maps model -> declared variants. Efforts the model does not declare are
    expressed through the tier/ladder choice only, so the variant is null.
    """
    if not model or not effort:
        return None
    declared = policy.get("opencode_variants", {}).get(model) or []
    return effort if effort in declared else None


def _task_variant(vehicle: dict[str, Any], policy: dict[str, Any]) -> tuple[str | None, str]:
    """(variant, reason suffix) for an opencode decision from effort."""
    model = vehicle.get("model")
    effort = vehicle.get("effort")
    variant = _variant_for_opencode(model, effort, policy)
    suffix = ""
    if variant is None and effort:
        suffix = f"; effort {effort} expressed by tier only"
    return variant, suffix


def _is_quota_error(last_error: dict[str, Any] | None) -> bool:
    if not last_error:
        return False
    text = last_error.get("message", "") or last_error.get("text", "") or ""
    text = text.lower()
    patterns = ("rate limit", "429", "quota", "usage limit", "insufficient balance")
    return any(p in text for p in patterns)


def _opencode_pool_status(oc: dict[str, Any], policy: dict[str, Any]) -> tuple[str, float]:
    """All models share the same rolling spend windows."""
    thresholds = policy.get("thresholds", {})
    soft_limit = thresholds.get("pool_soft_limit", 0.85)
    stale_seconds = thresholds.get("stale_seconds", 3600)

    sample_age = oc.get("sample_age_seconds")
    if sample_age is None or sample_age > stale_seconds:
        return "unknown", 0.0
    if oc.get("tag") == "unknown":
        return "unknown", 0.0

    max_util = 0.0
    complete = True
    for win_name in WINDOWS:
        win = (oc.get("pool") or {}).get("windows", {}).get(win_name) or {}
        util = win.get("utilisation")
        if not isinstance(util, (int, float)) or not math.isfinite(util) or util < 0:
            complete = False
            continue
        if util >= soft_limit:
            return "exhausted", util
        max_util = max(max_util, util)
    return ("available" if complete else "unknown"), max_util


def _model_status_opencode(
    model_id: str, oc: dict[str, Any], policy: dict[str, Any]
) -> tuple[str, float]:
    status, util = _opencode_pool_status(oc, policy)
    if status == "exhausted":
        return status, util
    state = oc.get("models", {}).get(model_id) or {}
    last_error = state.get("last_error")
    if _is_quota_error(last_error) and last_error.get("age_seconds", 10**9) < RATE_LIMIT_WINDOW_SECONDS:
        return "exhausted", util
    return status, util


def _pick_opencode(
    start_tier: str, oc: dict[str, Any], policy: dict[str, Any]
) -> tuple[str | None, str, float, list[str]]:
    """Pick the first capable ladder model; model spend never grants headroom."""
    if start_tier not in TIER_ORDER:
        start_tier = TIER_ORDER[0]
    idx = TIER_ORDER.index(start_tier)

    tried: list[str] = []

    for tier_idx in range(idx, len(TIER_ORDER)):
        tier = TIER_ORDER[tier_idx]
        tried.append(tier)
        ladder = policy.get("ladders", {}).get(tier, [])
        for model_id in ladder:
            status, util = _model_status_opencode(model_id, oc, policy)
            if status == "exhausted":
                continue
            return model_id, status, util, tried
    return None, "", 0.0, tried


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

    rl = pool.get("rate_limits") or {}
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
                f"opencode shared pool exhausted or candidate models rate-limited; codex fallback "
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
            f"opencode shared pool exhausted or candidate models rate-limited; claude subagent fallback "
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
    if worker == "devin":
        # busy counts as unavailable: re-dispatching would only be refused.
        return (usage.get("devin") or {}).get("status") != "available"
    return True


def _idle_devin_pane(usage: dict[str, Any], policy: dict[str, Any]) -> str | None:
    """The team's ``<team>-devin`` pane when the live session list shows it idle."""
    prefix = (usage.get("agents") or {}).get("prefix") or policy.get("team_prefix", "hx-")
    pane = f"{prefix}devin"
    for session in (usage.get("devin") or {}).get("live_sessions") or []:
        if (
            isinstance(session, dict)
            and session.get("name") == pane
            and session.get("status") in hx_usage.READY
        ):
            return pane
    return None


def _devin_persistent_eligible(task: dict[str, Any], pane: str) -> bool:
    """Persistent dispatch is for iterative work, or a --persistent block naming the pane."""
    if task.get("parallel"):
        return False
    persistent = task.get("persistent") or {}
    if persistent.get("name") == pane or persistent.get("agent") == pane:
        return persistent.get("status", "idle") in hx_usage.READY
    iterative = task.get("iterative")
    if iterative is None:
        iterative = task.get("size") == "large"
    return bool(iterative)


def _routing_order(policy: dict[str, Any]) -> list[str]:
    """Pool order for implementation work, data-driven from ``policy.routing_order``.

    ``opencode`` is the default first choice, with free ``devin`` as the
    fallback. A caller asks for Devin explicitly by putting ``devin`` first.
    """
    raw = policy.get("routing_order")
    if isinstance(raw, list):
        order: list[str] = []
        for item in raw:
            if not isinstance(item, str):
                continue
            name = item.strip().lower()
            if name and name not in order:
                order.append(name)
        if order:
            return order
    return ["opencode", "devin"]


def _devin_covers(kind: str | None, policy: dict[str, Any]) -> bool:
    conf = hx_devin.settings(policy)
    return kind in (conf.get("kinds") or hx_devin.DEFAULT_KINDS)


def _parse_ts(text: Any) -> datetime | None:
    if not isinstance(text, str) or not text:
        return None
    try:
        value = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return value if value.tzinfo is not None else None


def _balance_plan(policy: dict[str, Any], now: datetime | None = None) -> dict[str, Any] | None:
    """The active routing plan from ``policy.routing_balance``, or None.

    While the window is open the plan is ``balanced`` between its workers.
    At/after ``until`` it collapses to ``then`` (OpenCode), so Devin is not
    routed once the promotion ends.
    """
    raw = policy.get("routing_balance")
    if not isinstance(raw, dict) or str(raw.get("mode", "")).lower() != "balanced":
        return None
    workers: list[str] = []
    for item in raw.get("workers") or BALANCED_WORKERS:
        if not isinstance(item, str):
            continue
        name = item.strip().lower()
        if name in BALANCED_WORKERS and name not in workers:
            workers.append(name)
    if not workers:
        workers = list(BALANCED_WORKERS)
    now = now or _now()
    until = _parse_ts(raw.get("until"))
    window = raw.get("window_seconds")
    if not isinstance(window, (int, float)) or window <= 0:
        window = DEFAULT_BALANCE_WINDOW_SECONDS
    if until is not None and now >= until:
        then = str(raw.get("then") or "opencode").strip().lower()
        if then not in BALANCED_WORKERS:
            then = "opencode"
        return {
            "mode": "single",
            "workers": workers,
            "until": until,
            "then": then,
            "window_seconds": int(window),
        }
    return {
        "mode": "balanced",
        "workers": workers,
        "until": until,
        "window_seconds": int(window),
    }


def recent_route_counts(
    path: Path, window_seconds: int, now: datetime | None = None
) -> dict[str, int]:
    """Count decision lines per balanced worker in the recent window."""
    now = now or _now()
    cutoff = now - timedelta(seconds=window_seconds)
    counts = {worker: 0 for worker in BALANCED_WORKERS}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return counts
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except (TypeError, ValueError):
            continue
        if not isinstance(record, dict) or record.get("kind") != "decision":
            continue
        ts = _parse_ts(record.get("ts"))
        if ts is None or ts < cutoff:
            continue
        result = record.get("result")
        if not isinstance(result, dict):
            continue
        worker = result.get("worker")
        if isinstance(worker, str) and worker in counts:
            counts[worker] += 1
    return counts


def _recent_route_counts(
    policy: dict[str, Any], now: datetime | None = None
) -> dict[str, int]:
    raw = policy.get("routing_balance") or {}
    window = raw.get("window_seconds") if isinstance(raw, dict) else None
    if not isinstance(window, (int, float)) or window <= 0:
        window = DEFAULT_BALANCE_WINDOW_SECONDS
    return recent_route_counts(
        _repo_root() / ".hx" / "routing.jsonl", int(window), now
    )


def _provided_route_counts(usage: dict[str, Any]) -> dict[str, int]:
    raw = (usage.get("routing") or {}).get("recent")
    counts = {worker: 0 for worker in BALANCED_WORKERS}
    if isinstance(raw, dict):
        for worker in BALANCED_WORKERS:
            value = raw.get(worker)
            if isinstance(value, int) and value >= 0:
                counts[worker] = value
    return counts


def _opencode_idle(
    task: dict[str, Any], usage: dict[str, Any], policy: dict[str, Any], effort: str
) -> bool:
    """A ladder model is available now; the persistent pane only changes method."""
    model_id, _, _, _ = _pick_opencode(
        _tier_for_effort(effort), usage.get("opencode", {}), policy
    )
    return model_id is not None


def _devin_idle(
    task: dict[str, Any], usage: dict[str, Any], policy: dict[str, Any]
) -> bool:
    """A free one-off slot or an idle team pane can take Devin work now."""
    block = usage.get("devin") or {}
    status = block.get("status")
    pane = _idle_devin_pane(usage, policy)
    reuse = bool(
        pane
        and _devin_persistent_eligible(task, pane)
        and block.get("reuse_available", status == "available")
    )
    return status == "available" or reuse


def _devin_capability_escalation(
    task: dict[str, Any], policy: dict[str, Any], stages: list[str]
) -> dict[str, Any] | None:
    """A capability failure on the strongest SWE-2 model still goes to Codex."""
    prev = task.get("prev") or {}
    strongest = hx_devin.model_for_effort("max", policy)
    if (
        task.get("last_failure") == "capability"
        and prev.get("worker") == "devin"
        and prev.get("model") == strongest
    ):
        stages.append("devin:capability-at-max")
        return {
            "worker": "codex",
            "model": _codex_strong_model(policy),
            "effort": "high",
            "method": "ocrun-codex",
            "reason": f"capability failure on {strongest} -> codex strong",
            "stages": stages + ["pool:codex"],
        }
    return None


def _dispatch_worker(
    worker: str,
    task: dict[str, Any],
    usage: dict[str, Any],
    policy: dict[str, Any],
    effort: str,
    stages: list[str],
    capability_failure: bool,
) -> dict[str, Any] | None:
    if worker == "devin":
        return _devin_decision(task, usage, policy, effort, stages, capability_failure)
    if worker == "opencode":
        return _opencode_decision(task, usage, policy, effort, stages)
    return None


def _balanced_decision(
    task: dict[str, Any],
    usage: dict[str, Any],
    policy: dict[str, Any],
    effort: str,
    stages: list[str],
    capability_failure: bool,
    plan: dict[str, Any],
) -> dict[str, Any] | None:
    """Balance the workers, preferring idle, then fewer recent routes."""
    escalation = _devin_capability_escalation(task, policy, stages)
    if escalation is not None:
        return escalation

    workers = list(plan["workers"])
    previous_worker = (task.get("prev") or {}).get("worker")
    if capability_failure and previous_worker in workers:
        retry = _dispatch_worker(
            previous_worker, task, usage, policy, effort, stages, capability_failure
        )
        if retry is not None:
            retry["reason"] = f"capability retry on prior worker {previous_worker}; {retry['reason']}"
            return retry

    idle = {
        worker: (
            _devin_idle(task, usage, policy)
            if worker == "devin"
            else _opencode_idle(task, usage, policy, effort)
        )
        for worker in workers
    }
    idle_workers = [worker for worker in workers if idle[worker]]
    stages.append("balance:" + ("both-idle" if len(idle_workers) == len(workers) else "partial"))
    if not idle_workers:
        stages.append("balance:none-idle")
        return None

    counts = _provided_route_counts(usage)
    if len(idle_workers) == 1:
        chosen = idle_workers[0]
        reason = (
            f"balanced: {', '.join(w for w in workers if w != chosen)} busy or unavailable; "
            f"{chosen} idle"
        )
    else:
        chosen = min(idle_workers, key=lambda w: (counts.get(w, 0), workers.index(w)))
        hours = max(1, round(plan.get("window_seconds", DEFAULT_BALANCE_WINDOW_SECONDS) / 3600))
        tally = f"{counts['devin']} devin vs {counts['opencode']} opencode in the last {hours}h"
        if counts[chosen] < min(counts[w] for w in idle_workers if w != chosen):
            reason = f"balanced: both idle; {chosen} has fewer recent routes ({tally})"
        else:
            reason = (
                f"balanced: both idle with equal recent routes ({tally}); "
                f"policy order puts {chosen} first"
            )

    decision = _dispatch_worker(
        chosen, task, usage, policy, effort, stages, capability_failure
    )
    if decision is None:
        remaining = [worker for worker in idle_workers if worker != chosen]
        for alt in remaining:
            decision = _dispatch_worker(
                alt, task, usage, policy, effort, stages, capability_failure
            )
            if decision is not None:
                reason = f"balanced: {chosen} refused; {alt} idle"
                chosen = alt
                break
    if decision is None:
        return None
    decision["reason"] = f"{reason}; {decision.get('reason', '')}"
    decision["stages"] = list(decision.get("stages", stages)) + [f"balance:chosen:{chosen}"]
    return decision


def _opencode_decision(
    task: dict[str, Any],
    usage: dict[str, Any],
    policy: dict[str, Any],
    effort: str,
    stages: list[str],
) -> dict[str, Any] | None:
    """Pick the first capable model on the ladder; None when the ladder is spent."""
    stages.append("pool:opencode")
    start_tier = _tier_for_effort(effort)
    stages.append(f"tier:{start_tier}")
    model_id, status, util, tried = _pick_opencode(
        start_tier, usage.get("opencode", {}), policy
    )
    if model_id is None:
        for t in tried:
            stages.append(f"tried:{t}")
        return None
    stages.append(f"model:{model_id}")
    method = _decide_method(task, model_id, effort, policy)
    variant, tier_reason = _task_variant(
        {"model": model_id, "effort": effort}, policy
    )
    return {
        "worker": "opencode",
        "model": model_id,
        "effort": effort,
        "variant": variant,
        "method": method,
        "reason": (
            f"opencode ladder ({start_tier}) -> {model_id} "
            f"({status}, max util {util:.2f})" + tier_reason
        ),
        "stages": stages,
    }


def _devin_decision(
    task: dict[str, Any],
    usage: dict[str, Any],
    policy: dict[str, Any],
    effort: str,
    stages: list[str],
    capability_failure: bool,
) -> dict[str, Any] | None:
    """Free SWE-2 first for implementation work; None hands over to OpenCode.

    A ready pane can be reused when new sessions are capped. Unknown pane
    data, quota errors, disabled policy and ended promotions fall through
    to the unchanged OpenCode ladder. A capability
    failure on the strongest SWE-2 model escalates to Codex instead of
    retrying Devin.
    """
    conf = hx_devin.settings(policy)
    if task.get("kind") not in (conf.get("kinds") or hx_devin.DEFAULT_KINDS):
        return None
    plan = _balance_plan(policy)
    if plan is not None and plan["mode"] == "single" and plan["then"] != "devin":
        stages.append("devin:balance-period-ended")
        return None
    status = (usage.get("devin") or {}).get("status")
    pane = _idle_devin_pane(usage, policy)
    reuse = pane and _devin_persistent_eligible(task, pane) and (
        (usage.get("devin") or {}).get("reuse_available", status == "available")
    )
    if status != "available" and not reuse:
        stages.append(f"devin:{status or 'absent'}")
        return None
    prev = task.get("prev") or {}
    strongest = hx_devin.model_for_effort("max", policy)
    if capability_failure and prev.get("worker") == "devin" and prev.get("model") == strongest:
        stages.append("devin:capability-at-max")
        return {
            "worker": "codex",
            "model": _codex_strong_model(policy),
            "effort": "high",
            "method": "ocrun-codex",
            "reason": f"capability failure on {strongest} -> codex strong",
            "stages": stages + ["pool:codex"],
        }
    model = hx_devin.model_for_effort(effort, policy)
    method = "persistent-devin" if reuse else "ocrun-devin"
    stages.append("pool:devin")
    decision = {
        "worker": "devin",
        "model": model,
        "effort": effort,
        "method": method,
        "reason": (
            f"free SWE-2 promotion until {usage['devin'].get('promo', {}).get('ends_at')} -> devin {model}"
            + (f" via idle pane {pane}" if method == "persistent-devin" else "")
        ),
        "stages": stages,
    }
    if method == "persistent-devin":
        decision["agent"] = pane
    return decision


def route(task: dict[str, Any], usage: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    """Pure routing decision, then the retry-by-cause admission check."""
    decision = _route(task, usage, policy)
    return _admission_retry_check(task, decision, policy)


def _previous_failure_reason(task: dict[str, Any]) -> str | None:
    """The failure text of the previous attempt, from its outcome or prev block."""
    outcome = task.get("last_outcome") or {}
    reason = (outcome.get("failure") or {}).get("reason")
    if reason:
        return reason
    prev = task.get("prev") or {}
    return (
        (prev.get("failure") or {}).get("reason")
        or prev.get("failure_reason")
        or task.get("last_failure_reason")
    )


def _admission_retry_check(
    task: dict[str, Any], decision: dict[str, Any], policy: dict[str, Any]
) -> dict[str, Any]:
    """Refuse a route that repeats the denied command the previous run died on.

    Same-cause retries were the measured fault behind C4: a denied call ends
    a worker run, and the router sent the same denied work again. When the
    previous failure names a command or tool the newly chosen worker still
    denies, the decision is refused before launch with the fix spelled out.
    """
    if task.get("last_failure") != "tool":
        return decision
    worker = decision.get("worker")
    if not worker or decision.get("method") in ("defer", "direct"):
        return decision
    profile = capability.worker_profile(worker, policy)
    if profile is None:
        return decision
    cause = capability.retry_denied_cause(_previous_failure_reason(task), profile)
    if cause is None:
        return decision
    return {
        "worker": None,
        "model": None,
        "effort": decision.get("effort"),
        "method": "refuse",
        "reason": (
            f"admission: `{cause}` is still denied for {worker}; the previous run "
            "died on it (last_failure=tool). Fix the brief: the lead runs that "
            "command first or drops it, or the work goes to a worker that may."
        ),
        "stages": list(decision.get("stages") or []) + ["admission:denied-cause"],
        "review": True,
    }


def _route(task: dict[str, Any], usage: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
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
        if prev.get("worker") == "devin":
            retry = _devin_decision(task, usage, policy, prev.get("effort"), stages, False)
            if retry is not None:
                retry.update(model=prev.get("model"), reason="retry after input failure; fix the input, not the model; recheck pane eligibility")
                return retry
        if prev.get("worker") != "devin" and not _prev_model_exhausted(prev, usage, policy):
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

            variant, tier_reason = (None, "")
            if chosen_worker == "opencode":
                variant, tier_reason = _task_variant(
                    {"model": chosen_model, "effort": chosen_effort}, policy
                )
            else:
                variant, tier_reason = None, ""
            decision = {
                "worker": chosen_worker,
                "model": chosen_model,
                "effort": chosen_effort,
                "variant": variant,
                "method": method,
                "reason": (
                    f"retry after input failure; reuse prior decision; "
                    f"last_failure={last_failure} -> fix the input, not the model"
                    + tier_reason
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
    elif _devin_covers(kind, policy):
        plan = _balance_plan(policy)
        if plan is not None and plan["mode"] == "balanced":
            # While free SWE-2 lasts, keep Devin and OpenCode about even.
            decision = _balanced_decision(
                task, usage, policy, effort, stages, capability_failure, plan
            )
            if decision is None:
                decision = _fallback_decision(usage, policy, effort, stages)
        elif plan is not None and plan["mode"] == "single":
            # The balance window is over: OpenCode is the default, Devin is off.
            escalation = _devin_capability_escalation(task, policy, stages)
            if escalation is not None:
                return escalation
            chosen = plan["then"]
            stages.append(f"balance:ended:{chosen}")
            decision = _dispatch_worker(
                chosen, task, usage, policy, effort, stages, capability_failure
            )
            if decision is None:
                decision = _fallback_decision(usage, policy, effort, stages)
            else:
                decision["reason"] = (
                    f"balanced period ended (then={chosen}); "
                    + decision.get("reason", "")
                )
                decision["stages"] = list(decision.get("stages", stages)) + [
                    f"balance:then:{chosen}"
                ]
        else:
            # No balance policy: fall back to the declared pool order.
            decision = None
            for pool in _routing_order(policy):
                if pool == "opencode":
                    decision = _opencode_decision(task, usage, policy, effort, stages)
                elif pool == "devin":
                    decision = _devin_decision(
                        task, usage, policy, effort, stages, capability_failure
                    )
                if decision is not None:
                    break
            if decision is None:
                decision = _fallback_decision(usage, policy, effort, stages)
    else:
        decision = _opencode_decision(task, usage, policy, effort, stages)
        if decision is None:
            decision = _fallback_decision(usage, policy, effort, stages)

    if fix_input:
        decision["reason"] = (
            decision.get("reason", "")
            + f"; last_failure={last_failure} -> fix the input, not the model"
        )
        decision["stages"] = list(decision.get("stages", stages)) + ["failure_cause:fix_input"]

    return decision


def _last_outcome(task_id: str | None) -> dict[str, Any] | None:
    """The newest recorded outcome for a task in .hx/routing.jsonl, or None."""
    if not task_id:
        return None
    try:
        lines = (_repo_root() / ".hx" / "routing.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    found: dict[str, Any] | None = None
    for line in lines:
        try:
            record = json.loads(line)
        except (TypeError, ValueError):
            continue
        if (
            isinstance(record, dict)
            and record.get("kind") == "outcome"
            and record.get("task_id") == task_id
        ):
            found = record
    return found


def decide(task: dict[str, Any]) -> dict[str, Any]:
    policy = hx_usage.load_policy()
    snapshot = hx_usage.usage_snapshot(policy=policy)
    if isinstance(snapshot, dict):
        snapshot = dict(snapshot)
        snapshot["routing"] = {"recent": _recent_route_counts(policy)}
    enriched = dict(task)
    enriched["last_outcome"] = _last_outcome(task.get("task_id"))
    result = route(enriched, snapshot, policy)

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
