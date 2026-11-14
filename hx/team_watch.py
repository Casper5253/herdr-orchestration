"""hx team watch: one wake when a live teammate finishes work.

`hc wait --children` only follows terminals this one parented; team workers the
lead did not start (parent=null, like a manually opened codex) can never wake it.
This observer instead rescans the label-canonical roster every cycle and emits
one JSON line per event when a member goes from working (or an unknown status)
to idle, done, blocked, error, or exits the roster entirely. A member first seen
idle only sets a baseline: startup idle is never finished work.

On a transition into a ready status the watcher runs the guarded decide for that
worker once. When the decision is compact, a registered task is compacted with
`hx compact`; when it is compact-needed, a watcher-owned checkpoint compacts the
same session without inventing a ledger task. The lead and busy workers are
never touched. A successful compaction delivers that worker's pending messages.
Every action prints as one JSON line.
"""

from __future__ import annotations

import datetime as dt
import json
import time
from typing import Any

from hx import context, host, say, usage

# A member settling into one of these counts as finished work when it was busy.
SETTLED = frozenset({"idle", "done", "blocked", "error"})
BUSY = frozenset({"working", "unknown"})
COMPACT_ACTIONS = frozenset({"compact", "compact-needed"})


def _member_turn(member: host.Member) -> int | None:
    turn = member.get("turn")
    return turn if isinstance(turn, int) and not isinstance(turn, bool) else None


def _decision(controller: context.Controller, name: str) -> dict[str, Any]:
    """One guarded decide for the newly idle worker; refusals are data, not crashes."""
    try:
        result = controller.decide(name)
    except Exception as exc:
        return {"disposition": "refused", "reason": str(exc)}
    return {
        key: result.get(key)
        for key in ("disposition", "action", "reason", "task_id", "session_id")
        if result.get(key) is not None
    }


def _compact(controller: context.Controller, name: str,
             decision: dict[str, Any]) -> dict[str, Any]:
    """Run the matching guarded compaction and return its outcome as data."""
    try:
        if decision.get("action") == "compact":
            return controller.compact(name)
        if decision.get("action") == "compact-needed":
            return controller.compact_watch(name)
    except Exception as exc:
        return {"action": "error", "agent": name, "reason": str(exc)}
    return {"action": "keep", "agent": name, "reason": "no compaction action"}


def _outcome(result: dict[str, Any]) -> dict[str, Any]:
    return {
        key: result.get(key)
        for key in ("action", "reason", "task_id", "session_id", "telemetry",
                    "watch_checkpoint")
        if result.get(key) is not None
    }


def _deliver_pending(detected: host.Host, name: str) -> dict[str, Any]:
    try:
        return say.retry_pending_for(detected, name)
    except Exception as exc:
        return {"error": str(exc)}


def watch(
    team: str | None = None,
    *,
    interval: float = 1.0,
    timeout: float | None = None,
    detected: host.Host | None = None,
    controller: context.Controller | None = None,
    emit=None,
    sleep=time.sleep,
    clock=time.monotonic,
) -> int:
    """Watch the team until a worker finishes work, then return.

    Every event prints as one JSON line with address, name, role, from, to and
    turn; a ready transition carries the guarded decision outcome. A recoverable
    decision prints a second line with the compaction outcome and, after a
    verified success, the pending-delivery counts. Returns 0 whether an event
    fired or the timeout elapsed, 1 on misuse.
    """
    if emit is None:
        emit = lambda line: print(line, flush=True)
    detected = detected if detected is not None else context._detect()
    controller = controller or context.Controller()
    # The caller is never a worker event - exclude own even under --team.
    try:
        own = detected.own()
    except Exception:
        own = None
    if team is None:
        if not own:
            raise ValueError("hx team watch needs a team identity or --team")
        team = str(own["team"])
    own_name = str(own["name"]) if own else None
    prefix = team.rstrip("-")
    lead = usage.lead_name(getattr(controller, "policy", None) or {}, prefix + "-")

    seen: dict[str, dict[str, Any]] = {}
    retried: set[str] = set()
    deadline = clock() + timeout if timeout else None
    while True:
        members = detected.team(prefix) or {}
        events: list[dict[str, Any]] = []
        pending: list[tuple[str, dict[str, Any]]] = []
        for name, member in members.items():
            if name == own_name or name == lead:
                continue
            status = str(member.get("status") or "unknown")
            if status not in SETTLED | BUSY:
                status = "unknown"
            if status == "unknown":
                continue  # telemetry gap: keep the last known baseline
            previous = seen.get(name)
            seen[name] = {"status": status, "turn": _member_turn(member)}
            if previous is None:
                continue  # first sight only baselines; startup idle is not an event
            # A turn advance is observed completed work only when the member
            # settled: idle@4 -> working@5 is a task starting, not a finish.
            advanced = (
                isinstance(previous["turn"], int) and isinstance(seen[name]["turn"], int)
                and not isinstance(seen[name]["turn"], bool)
                and seen[name]["turn"] > previous["turn"]
            )
            if status == previous["status"] and not advanced:
                continue  # a heartbeat is never finished work
            if status in SETTLED and (advanced or previous["status"] == "working"):
                event = {
                    "address": str(member.get("address") or ""),
                    "name": name,
                    "role": str(member.get("role") or ""),
                    "from": previous["status"],
                    "to": status,
                    "turn": seen[name]["turn"],
                    "at": dt.datetime.now(dt.timezone.utc).isoformat(),
                }
                if status in usage.READY and name not in retried:
                    retried.add(name)
                    decision = _decision(controller, name)
                    event["decision"] = decision
                    if decision.get("action") in COMPACT_ACTIONS:
                        pending.append((name, decision))
                events.append(event)
        for name in list(seen):
            if name in members or name == own_name:
                continue
            # A vanished member exited: surface it once, then forget the baseline.
            events.append({
                "address": "",
                "name": name,
                "role": name.rsplit("-", 1)[-1],
                "from": seen[name]["status"],
                "to": "exit",
                "turn": None,
                "at": dt.datetime.now(dt.timezone.utc).isoformat(),
            })
            del seen[name]
        if events:
            for event in events:
                emit(json.dumps(event))
            for name, decision in pending:
                recovery = {
                    "type": "recovery",
                    "name": name,
                    "at": dt.datetime.now(dt.timezone.utc).isoformat(),
                }
                result = _compact(controller, name, decision)
                recovery.update(_outcome(result))
                if result.get("action") == "compacted":
                    recovery["delivery"] = _deliver_pending(detected, name)
                emit(json.dumps(recovery))
            return 0
        if deadline is not None and clock() >= deadline:
            return 0
        sleep(interval)
