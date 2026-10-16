"""hx.ledger: atomic task ledger with file ownership and O_EXCL locking.

The ledger lives in ``<repo>/.hx/ledger.json``; cross-agent leases live in
``~/.herdr-team/state/leases.json``. Every mutation takes an O_EXCL lock and
does an atomic read-check-mutate-replace cycle. Locks are considered stale
after 60 seconds and are stolen by the next waiter.
"""

from __future__ import annotations

import datetime
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any


class OwnershipConflict(Exception):
    """Raised when a file is already owned by another open task."""


class TaskNotFound(Exception):
    """Raised when a task id does not exist in the ledger."""


class LockTimeout(Exception):
    """Raised when the O_EXCL lock cannot be acquired."""


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


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
            return {"files": {}}
        if "files" not in data:
            data = {"files": {}}
        return data

    def _save(self, ledger: dict[str, Any], leases: dict[str, Any]) -> None:
        self._ensure_dirs()
        _atomic_write_json(self.ledger_path, ledger)
        _atomic_write_json(self.leases_path, leases)

    def _recompute_leases(self, ledger: dict[str, Any]) -> dict[str, Any]:
        leases: dict[str, Any] = {"files": {}}
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
            leases = self._recompute_leases(ledger)
            self._save(ledger, leases)
            return task

    def assign_task(
        self,
        task_id: str,
        owner: str,
        worktree: str | None = None,
    ) -> dict[str, Any]:
        with LeaseLock(self.lock_path):
            ledger = self._load_ledger()
            leases = self._load_leases()
            task = ledger.get("tasks", {}).get(task_id)
            if task is None:
                raise TaskNotFound(task_id)
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
            leases = self._recompute_leases(ledger)
            self._save(ledger, leases)
            return task

    def set_status(self, task_id: str, status: str) -> dict[str, Any]:
        if status not in {*self.OPEN_STATUSES, *self.CLOSED_STATUSES}:
            raise ValueError(f"Invalid status: {status}")
        with LeaseLock(self.lock_path):
            ledger = self._load_ledger()
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
            leases = self._recompute_leases(ledger)
            self._save(ledger, leases)
            return task

    def update_task(self, task_id: str, **fields: Any) -> dict[str, Any]:
        with LeaseLock(self.lock_path):
            ledger = self._load_ledger()
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
            leases = self._recompute_leases(ledger)
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
