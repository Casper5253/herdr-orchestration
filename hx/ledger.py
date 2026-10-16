"""hx.ledger: atomic task ledger with file ownership and O_EXCL locking.

The ledger lives in ``<repo>/.hx/ledger.json``; cross-agent leases live in
``~/.herdr-team/state/leases.json`` with two maps: ``files`` (per-file
ownership recomputed from the open tasks) and ``sessions`` (per-agent session
leases, written only through the public session functions). Every mutation
takes an O_EXCL lock and does an atomic read-check-mutate-replace cycle.
Locks are considered stale after 60 seconds and are stolen by the next waiter.

Closing a task goes through :meth:`Ledger.complete_task`: the revision of the
closing transition is computed inside the lock, the checkpoint is written with
it, and only then is the ledger committed at that revision.
"""

from __future__ import annotations

import datetime
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from hx import paths


class OwnershipConflict(Exception):
    """Raised when a file is already owned by another open task."""


class TaskNotFound(Exception):
    """Raised when a task id does not exist in the ledger."""


class SessionLeaseConflict(Exception):
    """Raised when a session lease blocks this agent from being dispatched to."""


class LockTimeout(Exception):
    """Raised when the O_EXCL lock cannot be acquired."""


def _repo_root() -> Path:
    return paths.project_root()


def _env_path(var: str, default: Path) -> Path:
    value = os.environ.get(var)
    return Path(value) if value else default


def default_ledger_path() -> Path:
    return _env_path("HX_LEDGER_PATH", _repo_root() / ".hx" / "ledger.json")


def default_leases_path() -> Path:
    return _env_path(
        "HX_LEASES_PATH",
        Path.home() / ".herdr-team" / "state" / "leases.json",
    )


def default_lock_path() -> Path:
    return _env_path("HX_LOCK_PATH", _repo_root() / ".hx" / "ledger.lock")


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _canonical_path(path: str | Path) -> str:
    """Return a normalised, case-folded absolute path for ownership checks.

    On Windows ``os.path.normcase`` lowercases the path, which matches the
    case-insensitive filesystem. The result is always absolute so that
    relative paths entered from different working directories collide correctly.
    """
    return os.path.normcase(os.path.normpath(os.path.abspath(str(path))))


def _git_head(repo_root: Path) -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root),
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
        return out.decode("utf-8").strip()
    except Exception:
        return ""


def _atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _load_json(path: Path) -> Any:
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _acquire_lock(lock_path: Path, stale_seconds: float = 60.0, retries: int = 100, delay: float = 0.05) -> None:
    """Acquire an O_EXCL lock file, stealing it if it is stale."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + (retries * delay) + 1.0
    while time.monotonic() < deadline:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                ts = _utcnow().isoformat()
                os.write(fd, ts.encode("utf-8"))
                os.fsync(fd)
            finally:
                os.close(fd)
            return
        except FileExistsError:
            try:
                stat = lock_path.stat()
                age = time.time() - stat.st_mtime
                if age > stale_seconds:
                    try:
                        os.remove(lock_path)
                    except FileNotFoundError:
                        pass
                    continue
            except FileNotFoundError:
                continue
        time.sleep(delay)
    raise LockTimeout(f"Could not acquire lock {lock_path}")


def _release_lock(lock_path: Path) -> None:
    try:
        os.remove(lock_path)
    except FileNotFoundError:
        pass


class LeaseLock:
    """Context manager that holds the O_EXCL ledger lock."""

    def __init__(self, lock_path: Path | None = None, stale_seconds: float = 60.0):
        self.lock_path = lock_path if lock_path is not None else default_lock_path()
        self.stale_seconds = stale_seconds

    def __enter__(self) -> "LeaseLock":
        _acquire_lock(self.lock_path, stale_seconds=self.stale_seconds)
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        _release_lock(self.lock_path)


class Ledger:
    """Atomic task ledger with per-file lease tracking."""

    OPEN_STATUSES = {"open", "assigned", "in_progress", "review"}
    CLOSED_STATUSES = {"done", "failed", "blocked"}
    SESSION_LEASE_STATES = {"held", "compact-unknown"}

    def __init__(
        self,
        ledger_path: Path | str | None = None,
        leases_path: Path | str | None = None,
        lock_path: Path | str | None = None,
        repo_root: Path | str | None = None,
    ):
        self.ledger_path = (
            Path(ledger_path) if ledger_path is not None else default_ledger_path()
        )
        self.leases_path = (
            Path(leases_path) if leases_path is not None else default_leases_path()
        )
        self.lock_path = Path(lock_path) if lock_path is not None else default_lock_path()
        self.repo_root = Path(repo_root) if repo_root is not None else _repo_root()

    def _ensure_dirs(self) -> None:
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        self.leases_path.parent.mkdir(parents=True, exist_ok=True)

    def _load_ledger(self) -> dict[str, Any]:
        data = _load_json(self.ledger_path)
        if data is None:
            return {"rev": 0, "tasks": {}}
        if "tasks" not in data:
            data = {"rev": 0, "tasks": data}
        return data

    def _load_leases(self) -> dict[str, Any]:
        data = _load_json(self.leases_path)
        if data is None:
            return {"files": {}, "sessions": {}}
        if "files" not in data:
            data = {"files": {}, "sessions": {}}
        if "sessions" not in data:
            data["sessions"] = {}
        return data

    def _save(self, ledger: dict[str, Any], leases: dict[str, Any]) -> None:
        self._ensure_dirs()
        _atomic_write_json(self.ledger_path, ledger)
        _atomic_write_json(self.leases_path, leases)

    def _save_leases(self, leases: dict[str, Any]) -> None:
        self.leases_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(self.leases_path, leases)

    def _recompute_leases(
        self,
        ledger: dict[str, Any],
        existing: dict[str, Any],
    ) -> dict[str, Any]:
        """Rebuild the ``files`` map from open tasks, leaving ``sessions`` alone."""
        sessions = existing.get("sessions", {})
        leases: dict[str, Any] = {"files": {}, "sessions": sessions}
        for task_id, task in ledger.get("tasks", {}).items():
            if task.get("status") in self.CLOSED_STATUSES:
                continue
            owner = task.get("owner", "")
            worktree = task.get("worktree")
            for f in task.get("files", []):
                key = _canonical_path(f)
                leases["files"][key] = {
                    "task_id": task_id,
                    "owner": owner,
                    "worktree": worktree,
                    "leased_at": task.get("updated", _utcnow().isoformat()),
                }
        return leases

    def _check_conflicts(
        self,
        files: list[str],
        leases: dict[str, Any],
        exclude_task_id: str | None = None,
        new_worktree: str | None = None,
    ) -> None:
        if new_worktree:
            # Parallel work in its own worktree bypasses conflicts.
            return
        for f in files:
            key = _canonical_path(f)
            lease = leases.get("files", {}).get(key)
            if lease and lease.get("task_id") != exclude_task_id:
                raise OwnershipConflict(
                    f"{f} is owned by task {lease['task_id']} ({lease.get('owner')})"
                )

    def _next_rev(self, ledger: dict[str, Any]) -> int:
        ledger["rev"] = ledger.get("rev", 0) + 1
        return ledger["rev"]

    def add_task(
        self,
        task_id: str,
        title: str,
        owner: str,
        files: list[str],
        depends: list[str] | None = None,
        base_commit: str | None = None,
        worktree: str | None = None,
        model: str | None = None,
        effort: str | None = None,
        method: str | None = None,
    ) -> dict[str, Any]:
        with LeaseLock(self.lock_path):
            ledger = self._load_ledger()
            leases = self._load_leases()
            if task_id in ledger.get("tasks", {}):
                raise OwnershipConflict(f"Task {task_id} already exists")
            file_base = Path(worktree) if worktree else self.repo_root
            canonical_files = [_canonical_path(Path(file_base) / f) for f in files]
            self._check_conflicts(canonical_files, leases, new_worktree=worktree)
            now = _utcnow().isoformat()
            task: dict[str, Any] = {
                "id": task_id,
                "title": title,
                "owner": owner,
                "model": model,
                "effort": effort,
                "method": method,
                "files": canonical_files,
                "depends": list(depends) if depends else [],
                "status": "open",
                "review": None,
                "attempts": 0,
                "created": now,
                "updated": now,
                "checkpoint": None,
                "base_commit": base_commit if base_commit is not None else _git_head(self.repo_root),
                "worktree": worktree,
                "rev": 1,
            }
            ledger["tasks"][task_id] = task
            self._next_rev(ledger)
            leases = self._recompute_leases(ledger, leases)
            self._save(ledger, leases)
            return task

    def assign_task(
        self,
        task_id: str,
        owner: str,
        worktree: str | None = None,
        method: str | None = None,
        agent: str | None = None,
    ) -> dict[str, Any]:
        with LeaseLock(self.lock_path):
            ledger = self._load_ledger()
            leases = self._load_leases()
            task = ledger.get("tasks", {}).get(task_id)
            if task is None:
                raise TaskNotFound(task_id)
            if method == "persistent" and agent:
                # Entries are keyed by agent (dispatch leases) or by provider session id
                # (hx.context compaction leases, which carry an "agent" field).
                for key, entry in leases.get("sessions", {}).items():
                    if key != agent and entry.get("agent") != agent:
                        continue
                    if entry.get("state") in ("compacting", "compact-unknown"):
                        raise SessionLeaseConflict(
                            f"Agent {agent} session is {entry['state']}; refusing dispatch"
                        )
                    entry_holder = entry.get("holder")
                    if entry_holder and entry_holder != owner:
                        raise SessionLeaseConflict(
                            f"Agent {agent} session is held by {entry_holder}; "
                            f"refusing dispatch"
                        )
            if worktree is not None:
                task["worktree"] = worktree
            canonical_files = task.get("files", [])
            self._check_conflicts(
                canonical_files,
                leases,
                exclude_task_id=task_id,
                new_worktree=task.get("worktree"),
            )
            task["owner"] = owner
            task["status"] = "assigned"
            task["updated"] = _utcnow().isoformat()
            task["rev"] = task.get("rev", 0) + 1
            self._next_rev(ledger)
            leases = self._recompute_leases(ledger, leases)
            self._save(ledger, leases)
            return task

    def set_status(self, task_id: str, status: str) -> dict[str, Any]:
        if status not in {*self.OPEN_STATUSES, *self.CLOSED_STATUSES}:
            raise ValueError(f"Invalid status: {status}")
        with LeaseLock(self.lock_path):
            ledger = self._load_ledger()
            leases = self._load_leases()
            task = ledger.get("tasks", {}).get(task_id)
            if task is None:
                raise TaskNotFound(task_id)
            task["status"] = status
            task["updated"] = _utcnow().isoformat()
            if status in self.CLOSED_STATUSES:
                if status == "failed":
                    task["attempts"] = task.get("attempts", 0) + 1
            task["rev"] = task.get("rev", 0) + 1
            self._next_rev(ledger)
            leases = self._recompute_leases(ledger, leases)
            self._save(ledger, leases)
            return task

    def update_task(self, task_id: str, **fields: Any) -> dict[str, Any]:
        with LeaseLock(self.lock_path):
            ledger = self._load_ledger()
            leases = self._load_leases()
            task = ledger.get("tasks", {}).get(task_id)
            if task is None:
                raise TaskNotFound(task_id)
            for key, value in fields.items():
                if key in {"id", "created", "rev"}:
                    continue
                if value is not None:
                    task[key] = value
            task["updated"] = _utcnow().isoformat()
            task["rev"] = task.get("rev", 0) + 1
            self._next_rev(ledger)
            leases = self._recompute_leases(ledger, leases)
            self._save(ledger, leases)
            return task

    def acquire_session(
        self,
        agent: str,
        session_id: str,
        holder: str,
        task_id: str | None = None,
    ) -> bool:
        """Acquire a session lease for ``agent`` under the ledger lock.

        Returns False without changes when the agent already holds a lease in
        state ``compact-unknown`` or when its session is held by a different
        holder. An existing lease held by the same holder is updated in place
        (e.g. a fresh session id after compaction).
        """
        with LeaseLock(self.lock_path):
            leases = self._load_leases()
            sessions = leases["sessions"]
            entry = sessions.get(agent)
            if entry is not None:
                if entry.get("state") == "compact-unknown":
                    return False
                entry_holder = entry.get("holder")
                if entry_holder and entry_holder != holder:
                    return False
            sessions[agent] = {
                "agent": agent,
                "session_id": session_id,
                "holder": holder,
                "state": "held",
                "acquired": _utcnow().isoformat(),
                "task_id": task_id,
            }
            self._save_leases(leases)
        return True

    def release_session(self, agent: str, holder: str) -> bool:
        """Release the session lease for ``agent``; False when absent or held by another holder."""
        with LeaseLock(self.lock_path):
            leases = self._load_leases()
            sessions = leases["sessions"]
            entry = sessions.get(agent)
            if entry is None:
                return False
            entry_holder = entry.get("holder")
            if entry_holder and entry_holder != holder:
                return False
            del sessions[agent]
            self._save_leases(leases)
        return True

    def set_session_state(self, agent: str, state: str) -> bool:
        """Set the state (``held`` or ``compact-unknown``) of an existing session lease."""
        if state not in self.SESSION_LEASE_STATES:
            raise ValueError(f"Invalid session lease state: {state}")
        with LeaseLock(self.lock_path):
            leases = self._load_leases()
            sessions = leases["sessions"]
            entry = sessions.get(agent)
            if entry is None:
                return False
            entry["state"] = state
            self._save_leases(leases)
        return True

    def get_session(self, agent: str) -> dict[str, Any] | None:
        with LeaseLock(self.lock_path):
            leases = self._load_leases()
            return leases["sessions"].get(agent)

    def complete_task(
        self,
        task_id: str,
        status: str,
        checkpoint_writer: Callable[[dict[str, Any], int], Path],
    ) -> dict[str, Any]:
        """Close a task as ``done``/``failed``/``blocked`` under one lock.

        The revision the closing transition produces is computed first, then
        ``checkpoint_writer(task, new_rev)`` records the checkpoint with it,
        and only afterwards is the ledger committed at that revision, so
        validation passes right after the close. A crash before the commit
        leaves the ledger intact at the old revision and the checkpoint
        validates as stale. The writer must not call back into the ledger:
        the lock is held and not re-entrant.
        """
        if status not in self.CLOSED_STATUSES:
            raise ValueError(f"Invalid status: {status}")
        with LeaseLock(self.lock_path):
            ledger = self._load_ledger()
            leases = self._load_leases()
            task = ledger.get("tasks", {}).get(task_id)
            if task is None:
                raise TaskNotFound(task_id)
            task["status"] = status
            if status == "failed":
                task["attempts"] = task.get("attempts", 0) + 1
            task["updated"] = _utcnow().isoformat()
            task["rev"] = task.get("rev", 0) + 1
            ledger["rev"] = ledger.get("rev", 0) + 1
            path = checkpoint_writer(task, task["rev"])
            task["checkpoint"] = str(path)
            leases = self._recompute_leases(ledger, leases)
            self._save(ledger, leases)
            return task

    def get_task(self, task_id: str) -> dict[str, Any]:
        with LeaseLock(self.lock_path):
            ledger = self._load_ledger()
            task = ledger.get("tasks", {}).get(task_id)
            if task is None:
                raise TaskNotFound(task_id)
            return task

    def list_tasks(self, status: str | None = None) -> list[dict[str, Any]]:
        with LeaseLock(self.lock_path):
            ledger = self._load_ledger()
            tasks = ledger.get("tasks", {}).values()
            if status:
                tasks = [t for t in tasks if t.get("status") == status]
            return list(tasks)

    def raw(self) -> dict[str, Any]:
        with LeaseLock(self.lock_path):
            return self._load_ledger()


def default_ledger() -> Ledger:
    return Ledger()
