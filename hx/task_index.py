"""hx.task_index: user-level discovery index for open ledger tasks.

Every project ledger stays the source of truth. This index only makes open
tasks discoverable across repos; readers verify each entry against the repo
ledger before trusting it, so a stale entry for closed work never blocks a
new unit. Writes are whole-repo syncs under one user-level lock, which makes
them idempotent and safe for concurrent hx processes.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from hx import ledger as ledger_mod

VERSION = 1


def default_index_path() -> Path:
    value = os.environ.get("HX_TASK_INDEX_PATH")
    return Path(value) if value else Path.home() / ".hx" / "task-index.json"


def default_lock_path() -> Path:
    value = os.environ.get("HX_TASK_INDEX_LOCK_PATH")
    if value:
        return Path(value)
    return default_index_path().with_suffix(".lock")


def _load_index(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"version": VERSION, "tasks": {}}
    if not isinstance(data, dict) or not isinstance(data.get("tasks"), dict):
        return {"version": VERSION, "tasks": {}}
    return data


def _task_key(repo_root: str, task_id: str) -> str:
    return repo_root + "::" + task_id


def _entry(repo_root: str, ledger_path: Path, task: dict[str, Any]) -> dict[str, Any]:
    return {
        "repo_root": repo_root,
        "ledger_path": str(ledger_path),
        "id": str(task.get("id") or ""),
        "title": str(task.get("title") or ""),
        "owner": str(task.get("owner") or ""),
        "status": str(task.get("status") or ""),
        "weight": str(task.get("weight") or ""),
        "jotarium": task.get("jotarium"),
        "updated": str(task.get("updated") or ""),
    }


def sync_repo(
    *,
    repo_root: Path | str,
    ledger_path: Path | str,
    tasks: list[dict[str, Any]],
    open_statuses: set[str] | frozenset[str],
    index_path: Path | str | None = None,
    lock_path: Path | str | None = None,
) -> int:
    """Replace this repo's index entries with its current open tasks.

    The sync is total for the repo, so repeated calls and completed tasks both
    converge to the same state. The lock serializes concurrent writers; the
    ledger's lock ordering is preserved because no ledger lock is taken here.
    """
    root = ledger_mod._canonical_path(Path(repo_root))
    ledger_file = Path(ledger_path)
    index_file = Path(index_path) if index_path is not None else default_index_path()
    lock_file = Path(lock_path) if lock_path is not None else default_lock_path()
    with ledger_mod.LeaseLock(lock_file):
        data = _load_index(index_file)
        indexed = data.setdefault("tasks", {})
        for key in [key for key, entry in indexed.items()
                    if isinstance(entry, dict) and entry.get("repo_root") == root]:
            indexed.pop(key, None)
        count = 0
        for task in tasks:
            if task.get("status") not in open_statuses:
                continue
            entry = _entry(root, ledger_file, task)
            if not entry["id"]:
                continue
            indexed[_task_key(root, entry["id"])] = entry
            count += 1
        data["version"] = VERSION
        ledger_mod._atomic_write_json(index_file, data)
        return count


def _verified_entry(entry: dict[str, Any], open_statuses: set[str] | frozenset[str]) -> dict[str, Any] | None:
    """The entry refreshed from its repo ledger, or None when the task is not open."""
    try:
        ledger_data = json.loads(Path(str(entry.get("ledger_path") or "")).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    tasks = ledger_data.get("tasks") if isinstance(ledger_data, dict) else None
    if not isinstance(tasks, dict):
        return None
    task = tasks.get(str(entry.get("id") or ""))
    if not isinstance(task, dict) or task.get("status") not in open_statuses:
        return None
    fresh = dict(entry)
    for key in ("title", "owner", "status", "weight", "jotarium", "updated"):
        if key in task:
            fresh[key] = task.get(key)
    return fresh


def open_entries(
    *,
    index_path: Path | str | None = None,
    verify: bool = True,
    open_statuses: set[str] | frozenset[str] | None = None,
) -> list[dict[str, Any]]:
    """Open tasks across every indexed repo, verified against their ledgers by default."""
    statuses = open_statuses or ledger_mod.Ledger.OPEN_STATUSES
    index_file = Path(index_path) if index_path is not None else default_index_path()
    data = _load_index(index_file)
    rows: list[dict[str, Any]] = []
    for entry in data.get("tasks", {}).values():
        if not isinstance(entry, dict):
            continue
        fresh = _verified_entry(entry, statuses) if verify else dict(entry)
        if fresh is not None:
            rows.append(fresh)
    rows.sort(key=lambda row: (str(row.get("repo_root")), str(row.get("id"))))
    return rows
