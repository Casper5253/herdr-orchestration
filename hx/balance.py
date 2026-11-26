"""hx.balance: heavy/light dispatch advice plus a read-only stuck-worker watchdog."""

from __future__ import annotations

import datetime as dt
import json
import math
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from hx import host, jotarium, ledger as ledger_mod, next as next_mod, paths, say, team_stalls, usage, context

ACTIVE_STATUSES = {"assigned", "in_progress", "review"}
WEIGHTS = {"heavy", "light"}
DEFAULT_STUCK_MINUTES = 45
SCREEN_LINES = 240
STATE_VERSION = 1


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _instant(value: Any) -> dt.datetime | None:
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)


def _iso(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat()


def _minutes(now: dt.datetime, then: dt.datetime | None) -> float:
    if then is None:
        return math.inf
    return max(0.0, (now - then.astimezone(dt.timezone.utc)).total_seconds() / 60.0)


def _int(value: Any, fallback: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else fallback


def _float(value: Any, fallback: float) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else fallback


def count_oneoff_processes(*, runner: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> int:
    """Live one-off `hx ocrun` processes, using the hook's self-excluding pattern."""
    if os.name == "nt":
        script = ("Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $PID -and "
                  "($_.CommandLine -match 'hx(\.exe)?\"? +ocrun' -or $_.CommandLine -match '-m hx ocrun') } "
                  "| Measure-Object | Select-Object -ExpandProperty Count")
        args = ["powershell", "-NoProfile", "-Command", script]
    else:
        script = "ps -eo pid=,args= | grep -E 'hx(\.exe)?\"? +ocrun|-m hx ocrun' | grep -v grep | wc -l"
        args = ["sh", "-c", script]
    try:
        completed = runner(args, capture_output=True, text=True, timeout=15, check=False)
        return max(0, int(str(completed.stdout).strip() or 0))
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0


def latest_report_time(worker: str, lead: str) -> dt.datetime | None:
    """Newest delivered outbox report from worker to lead, or None."""
    worker_role = worker.rsplit("-", 1)[-1]
    lead_role = lead.rsplit("-", 1)[-1]
    base = paths.hx_dir() / "outbox"
    if not base.is_dir():
        return None
    newest: dt.datetime | None = None
    for directory in base.iterdir():
        if not directory.is_dir():
            continue
        for path in directory.glob("*.json"):
            record = say._read_record(path)
            if record is None or say._record_state(record) != "delivered":
                continue
            if str(record.get("sender") or "") != worker:
                continue
            if str(record.get("target") or "") not in (lead, lead_role):
                continue
            stamp = context._timestamp(record.get("delivered_at") or record.get("updated_at"))
            if stamp is not None and (newest is None or stamp > newest):
                newest = stamp
    return newest


def _state_path() -> Path:
    return paths.hx_dir() / "balance-watchdog.json"


def _load_state(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"version": STATE_VERSION, "workers": {}}
    return data if isinstance(data, dict) and isinstance(data.get("workers"), dict) else {"version": STATE_VERSION, "workers": {}}


def _save_state(path: Path, state: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass


def _active_tasks(tasks: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    active: dict[str, dict[str, Any]] = {}
    for task in tasks:
        if task.get("status") not in ACTIVE_STATUSES:
            continue
        owner = str(task.get("owner") or "")
        if not owner:
            continue
        current = active.get(owner)
        if current is None or str(task.get("updated") or "") >= str(current.get("updated") or ""):
            active[owner] = task
    return active


def _weight(task: dict[str, Any]) -> str:
    weight = str(task.get("weight") or "").lower()
    return weight if weight in WEIGHTS else "heavy"


def _stuck_minutes(policy: dict[str, Any]) -> float:
    value = (policy.get("next") or {}).get("stuck_minutes", DEFAULT_STUCK_MINUTES)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        return float(DEFAULT_STUCK_MINUTES)
    return float(value)


def _default_next_unit(policy: dict[str, Any], query=None, ledger_obj=None, live=None, free_gb: float | None | object = next_mod._UNSET):
    if query is None:
        query = jotarium.JotariumClient().query
    if ledger_obj is None:
        ledger_obj = ledger_mod.default_ledger()
    sources = next_mod.Sources(query, ledger_obj, policy=policy, live=live or [], free_gb=free_gb)
    def choose(weight: str | None = None, running_heavy: int = 0, worker: str | None = None):
        return sources.next_unit(weight=weight, running_heavy=running_heavy, worker=worker)
    return choose


def snapshot(*, ledger: Any, members: list[host.Member], policy: dict[str, Any],
             now: dt.datetime | None = None, free_gb: float | None = None,
             oneoff_count: int = 0, next_unit: Callable[..., dict[str, Any]] | None = None,
             read_screen: Callable[[str, int], str] | None = None,
             report_time: Callable[[str, str], dt.datetime | None] | None = None,
             state_path: Path | None = None) -> dict[str, Any]:
    """One balanced dispatch snapshot. It never sends, types, kills or restarts."""
    now = now or _utcnow()
    prefix = usage.team_prefix(policy)
    lead = usage.lead_name(policy, prefix)
    threshold = _stuck_minutes(policy)
    state_file = Path(state_path) if state_path is not None else _state_path()
    stored = _load_state(state_file)
    previous = stored.get("workers") or {}
    current_state: dict[str, Any] = {}
    warnings: list[str] = []
    tasks = list(ledger.list_tasks()) if ledger is not None else []
    active = _active_tasks(tasks)
    running_heavy = sum(1 for task in active.values() if _weight(task) == "heavy") + max(0, int(oneoff_count))

    # hx next owns the memory rule; a missing memory reading leaves the base cap.
    sources = next_mod.Sources(lambda name, payload: {}, None, policy=policy, free_gb=free_gb)
    heavy_cap = max(1, sources.heavy_cap())
    if next_unit is None:
        next_unit = _default_next_unit(policy, ledger_obj=ledger, live=members, free_gb=free_gb)
    if report_time is None:
        report_time = latest_report_time

    slots: list[dict[str, Any]] = []
    stuck: list[dict[str, Any]] = []
    for member in sorted(members, key=lambda row: str(row.get("name") or "")):
        name = str(member.get("name") or "")
        if not name or name == lead or str(member.get("role") or "") == str(policy.get("lead_role", "lead")):
            continue
        owned = active.get(name)
        live_status = str(member.get("status") or "unknown")
        if owned is None:
            if live_status in {"idle", "done", "unknown"}:
                state = "idle"
            else:
                # Untracked work is treated conservatively as heavy; it must not be hidden.
                state = "heavy"
                running_heavy += 1
            slots.append({"worker": name, "state": state, "task_id": None,
                          "since": _iso(now), "live_status": live_status})
            continue

        weight = _weight(owned)
        updated = _instant(owned.get("updated")) or now
        try:
            screen = str(read_screen(name, SCREEN_LINES)) if read_screen else ""
        except Exception as exc:
            screen = ""
            warnings.append(f"{name}: screen unreadable: {exc}")
        digest = team_stalls.digest(screen) if screen else None
        old = previous.get(name) if isinstance(previous.get(name), dict) else None
        restarted = bool(old) and (
            old.get("task_id") != owned.get("id")
            or old.get("session_id") != member.get("session_id")
            or old.get("created_ms") != member.get("created_ms"))
        if old is None or restarted or old.get("hash") != digest or old.get("task_status") != owned.get("status"):
            output_since = now
        else:
            output_since = dt.datetime.fromtimestamp(float(old.get("output_since_epoch", now.timestamp())), dt.timezone.utc)
        current_state[name] = {
            "hash": digest,
            "output_since_epoch": output_since.timestamp(),
            "task_id": owned.get("id"),
            "task_status": owned.get("status"),
            "session_id": member.get("session_id"),
            "created_ms": member.get("created_ms"),
        }
        try:
            reported = report_time(name, lead)
        except Exception as exc:
            reported = None
            warnings.append(f"{name}: report scan failed: {exc}")
        status_age = _minutes(now, updated)
        output_age = _minutes(now, output_since)
        report_age = _minutes(now, reported)
        is_stuck = min(status_age, output_age, report_age) >= threshold
        state = "stuck" if is_stuck else weight
        slot = {"worker": name, "state": state, "task_id": str(owned.get("id") or ""),
                "since": str(owned.get("updated") or _iso(updated)), "live_status": live_status,
                "minutes_stuck": round(min(status_age, output_age, report_age), 1) if is_stuck else 0.0}
        slots.append(slot)
        if is_stuck:
            stuck.append({"worker": name, "task_id": slot["task_id"],
                          "minutes": slot["minutes_stuck"],
                          "recovery": {"step": "read-screen",
                                       "ladder": ["read its screen", "nudge it", "restart it at a task boundary"],
                                       "automatic_kill": False}})

    assignments: list[dict[str, Any]] = []
    planned_heavy = running_heavy
    for slot in slots:
        if slot["state"] != "idle":
            continue
        worker = str(slot["worker"]).rsplit("-", 1)[-1]
        wanted = "heavy" if planned_heavy < heavy_cap else "light"
        try:
            chosen = next_unit(weight=wanted, running_heavy=planned_heavy, worker=worker)
        except Exception as exc:
            warnings.append(f"{slot['worker']}: hx next failed: {exc}")
            continue
        actual = str(chosen.get("weight") or wanted)
        assignments.append({"worker": slot["worker"], "weight": actual, "unit": chosen,
                            "hint": "matched" if chosen.get("worker_hint") == worker else "fallback"})
        if actual == "heavy":
            planned_heavy += 1

    _save_state(state_file, {"version": STATE_VERSION, "workers": current_state})
    return {"heavy_cap": heavy_cap, "running_heavy": running_heavy,
            "planned_heavy": planned_heavy, "oneoff_count": int(oneoff_count),
            "free_memory_gb": free_gb, "slots": slots, "assignments": assignments,
            "stuck": stuck, "warnings": warnings}


def format_snapshot(rows: dict[str, Any]) -> str:
    lines = [f"heavy: {rows['running_heavy']} running / cap {rows['heavy_cap']} "
             f"({rows['oneoff_count']} one-off)"]
    for slot in rows["slots"]:
        task = f" task {slot['task_id']}" if slot.get("task_id") else ""
        lines.append(f"{slot['worker']}: {slot['state']}{task} since {slot['since']}")
    for assignment in rows["assignments"]:
        unit = assignment["unit"]
        lines.append(f"start on {assignment['worker']}: [{assignment['weight']}] "
                     f"{unit.get('source')} {unit.get('ref')}: {unit.get('title')} "
                     f"(hint {unit.get('worker_hint')}, {assignment['hint']})")
    for item in rows["stuck"]:
        lines.append(f"stuck {item['worker']} for {item['minutes']}m: "
                     + " -> ".join(item["recovery"]["ladder"]))
    return "\n".join(lines)


def command(as_json: bool = False) -> int:
    policy = usage.load_policy()
    prefix = usage.team_prefix(policy)
    members = list(usage.team_agents(prefix).values())
    rows = snapshot(ledger=ledger_mod.default_ledger(), members=members, policy=policy,
                    free_gb=usage.free_memory_gb(), oneoff_count=count_oneoff_processes())
    print(json.dumps(rows, indent=2) if as_json else format_snapshot(rows))
    return 0
