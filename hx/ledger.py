"""hx.ledger: atomic task ledger with file ownership and locking.

The ledger lives in ``<repo>/.hx/ledger.json``; cross-agent leases live in
``~/.herdr-team/state/leases.json`` with two maps: ``files`` partitioned per
project root (only this project's partition is rebuilt on mutation) and
``sessions`` (per-agent session leases, written only through the public
session functions). Every mutation takes the repo lock (``.hx/ledger.lock``)
and, because leases.json is machine-wide, a shared lock next to leases.json
(``leases.lock``) in a stable order. Lock files record the owning pid; a lock
is only stolen when its pid is dead (never a live lock by age alone).

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


def _partition_files(files: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Group flat file leases (legacy format) under partition keys.

    File leases are now stored per project root: ``{"files": {<project root>:
    {<absolute file path>: <lease>}}}``. Legacy flat leases (path -> lease)
    migrate under the canonical parent directory of every leased file so an
    upgrade preserves ownership.
    """
    partitioned: dict[str, dict[str, Any]] = {}
    for path, lease in dict(files or {}).items():
        if isinstance(lease, dict) and "task_id" in lease:
            parent = os.path.dirname(str(path)) or "."
            partitioned.setdefault(_canonical_path(parent), {})[path] = lease
        else:
            partitioned[path] = lease if isinstance(lease, dict) else {}
    return partitioned


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


def _run_git(args: list[str], cwd: Path) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=30,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except Exception as exc:
        return 1, "", str(exc)


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
    """Acquire an O_EXCL lock file, stealing it only when the owner pid is dead.

    A live lock is never stolen by age alone: the waiting never takes the lock
    until the holder releases it or the waiting retries are exhausted. Lock
    files without a parsable pid (corrupt or very old format) fall back to the
    old age-based staleness rule.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + (retries * delay) + 1.0
    payload = json.dumps({"pid": os.getpid(), "ts": _utcnow().isoformat()}).encode("utf-8")
    while time.monotonic() < deadline:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(fd, payload)
                os.fsync(fd)
            finally:
                os.close(fd)
            return
        except FileExistsError:
            if _stealable(lock_path, stale_seconds):
                try:
                    os.remove(lock_path)
                except FileNotFoundError:
                    pass
                continue
        time.sleep(delay)
    raise LockTimeout(f"Could not acquire lock {lock_path}")


def _stealable(lock_path: Path, stale_seconds: float) -> bool:
    """Decide whether an existing lock file may be stolen."""
    try:
        info = json.loads(lock_path.read_text(encoding="utf-8"))
    except Exception:
        info = None
    pid = info.get("pid") if isinstance(info, dict) else None
    if isinstance(pid, int) and pid > 0:
        return not _pid_alive(pid)
    try:
        age = time.time() - lock_path.stat().st_mtime
    except FileNotFoundError:
        return False
    return age > stale_seconds


def _pid_alive(pid: int) -> bool:
    """True when a process with this pid exists on this machine."""
    if pid <= 0:
        return False
    try:
        import ctypes

        k32 = ctypes.windll.kernel32
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not handle:
            # Access denied means the process exists but is not queryable.
            return bool(ctypes.GetLastError() == 5)
        try:
            exit_code = ctypes.c_ulong()
            if not k32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return True
            return exit_code.value == 259  # STILL_ACTIVE
        finally:
            k32.CloseHandle(handle)
    except AttributeError:
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False
    except Exception:
        return True


def _release_lock(lock_path: Path) -> None:
    try:
        os.remove(lock_path)
    except FileNotFoundError:
        pass


class LeaseLock:
    """Context manager that holds one O_EXCL lock."""

    def __init__(self, lock_path: Path | None = None, stale_seconds: float = 60.0):
        self.lock_path = lock_path if lock_path is not None else default_lock_path()
        self.stale_seconds = stale_seconds

    def __enter__(self) -> "LeaseLock":
        _acquire_lock(self.lock_path, stale_seconds=self.stale_seconds)
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        _release_lock(self.lock_path)


class DualLock:
    """Hold several O_EXCL locks in a stable order; release in reverse.

    Every ledger mutation takes the repo lock first, then the shared
    machine-wide leases lock, so two projects sharing leases.json can never
    deadlock against each other.
    """

    def __init__(self, lock_paths: list[Path], stale_seconds: float = 60.0):
        paths: list[Path] = []
        for path in lock_paths:
            if path not in paths:
                paths.append(Path(path))
        self.lock_paths = paths
        self.stale_seconds = stale_seconds

    def __enter__(self) -> "DualLock":
        acquired: list[Path] = []
        try:
            for path in self.lock_paths:
                _acquire_lock(path, stale_seconds=self.stale_seconds)
                acquired.append(path)
        except BaseException:
            for path in reversed(acquired):
                _release_lock(path)
            raise
        self._acquired = acquired
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        for path in reversed(getattr(self, "_acquired", self.lock_paths)):
            _release_lock(path)


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
        env_leases_lock = os.environ.get("HX_LEASES_LOCK_PATH")
        self.leases_lock_path = (
            Path(env_leases_lock)
            if env_leases_lock
            else self.leases_path.parent / "leases.lock"
        )
        self.repo_root = Path(repo_root) if repo_root is not None else _repo_root()

    def _dual_lock(self) -> DualLock:
        """Repo lock plus the shared leases lock, in stable order."""
        return DualLock([self.lock_path, self.leases_lock_path])

    def _leases_lock(self) -> LeaseLock:
        return LeaseLock(self.leases_lock_path)

    def mutate_leases(self, mutator: Callable[[dict[str, Any]], Any]) -> Any:
        """Apply ``mutator(leases_data)`` to leases.json under the shared lock.

        leases.json is machine-wide and read/written by hx.context outside the
        ledger flow, so any leases change must go through here, never through
        a repo-only lock. The shared lock is held for the whole call: never
        call herdr commands or model operations from inside ``mutator``.
        Other agents sleep while the caller computes.
        """
        with self._leases_lock():
            data = self._load_leases()
            result = mutator(data)
            self._save_leases(data)
            return result

    def mutate_leases_with_repo(
        self,
        mutator: Callable[[dict[str, Any], dict[str, Any]], Any],
    ) -> Any:
        """Task-bound leases changes: ``mutator(ledger_data, leases_data)``.

        Acquires the repo lock first, then the shared leases lock (the same
        stable order as every ledger mutation), and commits both files at the
        end. Rules are the same as for :meth:`mutate_leases`: no herdr or
        model work while the locks are held.
        """
        with self._dual_lock():
            ledger_data = self._load_ledger()
            leases_data = self._load_leases()
            result = mutator(ledger_data, leases_data)
            self._save(ledger_data, leases_data)
            return result

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
            data = {}
        if "files" not in data:
            data["files"] = {}
        if "sessions" not in data:
            data["sessions"] = {}
        data["files"] = _partition_files(data["files"])
        return data

    def _project_partition_key(self) -> str:
        return _canonical_path(self.repo_root)

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
        """Rebuild only this project's file-lease partition.

        leases.json is machine-wide: partitions of other projects and the
        ``sessions`` map are preserved untouched.
        """
        key = self._project_partition_key()
        partitions = dict(existing.get("files", {}))
        mine: dict[str, Any] = {}
        for task_id, task in ledger.get("tasks", {}).items():
            if task.get("status") in self.CLOSED_STATUSES:
                continue
            owner = task.get("owner", "")
            worktree = task.get("worktree")
            canonical_worktree = _canonical_path(worktree) if worktree else None
            for f in task.get("files", []):
                fkey = _canonical_path(f)
                mine[fkey] = {
                    "task_id": task_id,
                    "owner": owner,
                    "worktree": canonical_worktree,
                    "leased_at": task.get("updated", _utcnow().isoformat()),
                }
        partitions[key] = mine
        leases: dict[str, Any] = {"files": partitions, "sessions": existing.get("sessions", {})}
        return leases

    def _check_conflicts(
        self,
        files: list[str],
        leases: dict[str, Any],
        exclude_task_id: str | None = None,
        new_worktree: str | None = None,
    ) -> None:
        partition = leases.get("files", {}).get(self._project_partition_key(), {})
        # ``worktree`` None means the primary tree, so tasks may claim the
        # primary root (or omit worktree) and stay in the same tree.
        primary = _canonical_path(self.repo_root)
        new_wt = _canonical_path(new_worktree) if new_worktree else primary
        for f in files:
            key = _canonical_path(f)
            lease = partition.get(key)
            if not lease or lease.get("task_id") == exclude_task_id:
                continue
            lease_wt_raw = lease.get("worktree")
            lease_wt = _canonical_path(lease_wt_raw) if lease_wt_raw else primary
            if lease_wt != new_wt:
                # Different worktrees may clone the same files in parallel.
                continue
            raise OwnershipConflict(
                f"{f} is owned by task {lease['task_id']} ({lease.get('owner')})"
            )

    def _verify_worktree(self, worktree: str | None) -> None:
        """Refuse worktree claims that are not a git worktree of this repo."""
        if not worktree:
            return
        if not (self.repo_root / ".git").exists():
            # Not a git repo: verification is not applicable here; the
            # normalised intra-worktree conflict checks still apply.
            return
        claimed = _canonical_path(worktree)
        rc, out, err = _run_git(["worktree", "list", "--porcelain"], self.repo_root)
        if rc != 0:
            # Fail closed: if git cannot enumerate worktrees we cannot verify
            # the claim and must refuse it.
            raise ValueError(
                f"cannot verify git worktrees for {self.repo_root}: {err.strip() or 'git failed'}"
            )
        entries = {
            _canonical_path(line[len("worktree "):].strip())
            for line in out.splitlines()
            if line.startswith("worktree ")
        }
        if claimed not in entries:
            raise ValueError(f"{worktree} is not a git worktree of this repo")

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
        with self._dual_lock():
            ledger = self._load_ledger()
            leases = self._load_leases()
            if task_id in ledger.get("tasks", {}):
                raise OwnershipConflict(f"Task {task_id} already exists")
            self._verify_worktree(worktree)
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

    @staticmethod
    def _resolve_session(sessions: dict[str, Any], agent: str) -> tuple[str | None, dict[str, Any] | None]:
        """Find a session lease by agent key or by its ``agent`` field.

        Entries are keyed by agent name (dispatch leases) or by provider
        session id (hx.context compaction leases carrying ``agent`` and
        ``token`` fields); both shapes resolve here.
        """
        if agent in sessions:
            return agent, sessions[agent]
        for key, entry in sessions.items():
            if isinstance(entry, dict) and entry.get("agent") == agent:
                return key, entry
        return None, None

    def assign_task(
        self,
        task_id: str,
        owner: str,
        worktree: str | None = None,
        method: str | None = None,
        agent: str | None = None,
    ) -> dict[str, Any]:
        with self._dual_lock():
            ledger = self._load_ledger()
            leases = self._load_leases()
            task = ledger.get("tasks", {}).get(task_id)
            if task is None:
                raise TaskNotFound(task_id)
            self._verify_worktree(worktree if worktree is not None else task.get("worktree"))
            sessions = leases["sessions"]
            if method == "persistent":
                if not agent:
                    raise ValueError("persistent assignment requires --agent")
                # There may be no session yet: reserve it atomically here so a
                # concurrent dispatch cannot also claim the agent's session.
                key, entry = self._resolve_session(sessions, agent)
                if entry is not None:
                    if entry.get("state") in ("compacting", "compact-unknown"):
                        raise SessionLeaseConflict(
                            f"Agent {agent} session is {entry['state']}; refusing dispatch"
                        )
                    reserved_task = entry.get("task_id")
                    if (
                        "token" not in entry
                        and reserved_task is not None
                        and reserved_task != task_id
                        and entry.get("holder") == owner
                    ):
                        raise SessionLeaseConflict(
                            f"Agent {agent} session is reserved for task {reserved_task} "
                            f"by {entry.get('holder')}; refusing dispatch for task {task_id}"
                        )
                    entry_holder = entry.get("holder")
                    if entry_holder and entry_holder != owner:
                        raise SessionLeaseConflict(
                            f"Agent {agent} session is held by {entry_holder}; "
                            f"refusing dispatch"
                        )
                    if "token" in entry:
                        # hx.context owns this compaction lease and verifies its
                        # token later; leave the key untouched.
                        sessions[key] = {**entry, "state": "held"}
                    else:
                        if key != agent:
                            sessions.pop(key, None)
                        sessions[agent] = {
                            "agent": agent,
                            "session_id": entry.get("session_id"),
                            "holder": owner,
                            "state": "held",
                            "acquired": _utcnow().isoformat(),
                            "task_id": task_id,
                        }
                else:
                    sessions[agent] = {
                        "agent": agent,
                        "session_id": None,
                        "holder": owner,
                        "state": "held",
                        "acquired": _utcnow().isoformat(),
                        "task_id": task_id,
                    }
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
        with self._dual_lock():
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
                self._release_task_reservations(leases, task_id)
            task["rev"] = task.get("rev", 0) + 1
            self._next_rev(ledger)
            leases = self._recompute_leases(ledger, leases)
            self._save(ledger, leases)
            return task

    def _release_task_reservations(self, leases: dict[str, Any], task_id: str) -> int:
        """Drop held dispatch reservations linked to ``task_id``.

        Compaction leases (``token`` entries) belong to hx.context and are
        never touched here.
        """
        sessions = leases.get("sessions", {})
        doomed = [
            key
            for key, entry in sessions.items()
            if isinstance(entry, dict)
            and "token" not in entry
            and entry.get("state") == "held"
            and entry.get("task_id") == task_id
        ]
        for key in doomed:
            del sessions[key]
        return len(doomed)

    def release_task_reservations(self, task_id: str) -> int:
        """Release stranded session reservations for a closed/lost task."""
        with self._leases_lock():
            leases = self._load_leases()
            released = self._release_task_reservations(leases, task_id)
            self._save_leases(leases)
            return released

    def update_task(self, task_id: str, **fields: Any) -> dict[str, Any]:
        with self._dual_lock():
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
        """Acquire a session lease for ``agent`` under the shared leases lock.

        Returns False without changes when a compaction lease (``token``
        entry) owns the session, when the agent's lease is in state
        ``compact-unknown``, or when its session is held by a different
        holder. An existing lease held by the same holder is refreshed in
        place (e.g. a fresh session id after compaction); sibling legacy
        entries for the same agent are re-keyed under the agent name.
        """
        with self._leases_lock():
            leases = self._load_leases()
            sessions = leases["sessions"]
            key, entry = self._resolve_session(sessions, agent)
            if entry is not None:
                if "token" in entry:
                    return False
                if entry.get("state") == "compact-unknown":
                    return False
                entry_holder = entry.get("holder")
                if entry_holder and entry_holder != holder:
                    return False
                if key != agent:
                    sessions.pop(key, None)
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
        with self._leases_lock():
            leases = self._load_leases()
            sessions = leases["sessions"]
            key, entry = self._resolve_session(sessions, agent)
            if entry is None or "token" in entry:
                return False
            entry_holder = entry.get("holder")
            if entry_holder and entry_holder != holder:
                return False
            del sessions[key]
            self._save_leases(leases)
        return True

    def set_session_state(self, agent: str, state: str) -> bool:
        """Set the state (``held`` or ``compact-unknown``) of an existing session lease."""
        if state not in self.SESSION_LEASE_STATES:
            raise ValueError(f"Invalid session lease state: {state}")
        with self._leases_lock():
            leases = self._load_leases()
            sessions = leases["sessions"]
            key, entry = self._resolve_session(sessions, agent)
            if entry is None:
                return False
            entry["state"] = state
            self._save_leases(leases)
        return True

    def get_session(self, agent: str) -> dict[str, Any] | None:
        with self._leases_lock():
            leases = self._load_leases()
            _key, entry = self._resolve_session(leases["sessions"], agent)
            return entry

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
        with self._dual_lock():
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
            self._release_task_reservations(leases, task_id)
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
