"""Publish the v2 hc provider binding for a hook process.

Contract: ``.hx/contracts/hc-binding-v2.md``. One JSON file per terminal under
``<state dir>/hc-bindings/<terminal>.json``, written through a sibling temp file
and renamed into place. The writer refuses when the environment names no hc
terminal, or names one that is not live or not an ancestor of this process.
"""

from __future__ import annotations

import ctypes
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

PROVIDERS = {"claude", "codex", "opencode", "devin"}
TERMINAL = re.compile(r"[A-Za-z0-9_-]+")
MAX_ANCESTORS = 64


def state_dir() -> Path:
    env = os.environ.get("HX_STATE_DIR")
    return Path(env) if env else Path.home() / ".herdr-team" / "state"


def binding_path(terminal: str) -> Path:
    return state_dir() / "hc-bindings" / f"{terminal}.json"


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _warn(message: str) -> None:
    try:
        print(f"hc-binding: {message}", file=sys.stderr)
    except Exception:
        pass


def _windows_ancestors(pid: int) -> list[int]:
    from ctypes import wintypes

    class PROCESSENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_char * 260),
        ]

    kernel32 = ctypes.windll.kernel32
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    if snapshot in (-1, 0):
        return []
    parents: dict[int, int] = {}
    try:
        entry = PROCESSENTRY32()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32)
        found = kernel32.Process32First(snapshot, ctypes.byref(entry))
        while found:
            parents[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
            found = kernel32.Process32Next(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return _walk_parents(parents, pid)


def _posix_ancestors(pid: int) -> list[int]:
    parents: dict[int, int] = {}
    proc = Path("/proc")
    if not proc.is_dir():
        return []
    try:
        entries = list(proc.iterdir())
    except OSError:
        return []
    for child in entries:
        if not child.name.isdigit():
            continue
        try:
            stat = (child / "stat").read_text(encoding="utf-8")
            rest = stat[stat.rfind(")") + 2 :].split()
            parents[int(child.name)] = int(rest[1])
        except (OSError, ValueError, IndexError):
            continue
    return _walk_parents(parents, pid)


def _walk_parents(parents: dict[int, int], pid: int) -> list[int]:
    chain: list[int] = []
    current = pid
    for _ in range(MAX_ANCESTORS):
        parent = parents.get(current, 0)
        if parent <= 0 or parent in chain or parent == pid:
            break
        chain.append(parent)
        current = parent
    return chain


def ancestor_pids(pid: int | None = None) -> list[int]:
    start = os.getpid() if pid is None else pid
    return _windows_ancestors(start) if os.name == "nt" else _posix_ancestors(start)


def _default_run_hc(terminal: str) -> dict[str, Any] | None:
    import subprocess

    try:
        completed = subprocess.run(
            ["hc", "get", terminal, "--json"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    try:
        data = json.loads(completed.stdout)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def validate(terminal: str, observed: Any, ancestors: list[int], own_pid: int) -> str | None:
    if not isinstance(observed, dict):
        return "no terminal detail"
    if observed.get("state") != "live":
        return "terminal is not live"
    if observed.get("address") not in (None, terminal):
        return "detail belongs to another terminal"
    if not _is_int(observed.get("created_ms")):
        return "terminal has no created_ms generation"
    pid = observed.get("pid")
    if not _is_int(pid) or pid <= 0:
        return "terminal has no pid"
    if pid != own_pid and pid not in ancestors:
        return "terminal is not this process or its ancestor"
    return None


def process_image(pid: int) -> str | None:
    if os.name != "nt":
        try:
            return os.readlink(f"/proc/{pid}/exe")
        except OSError:
            return None

    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    try:
        size = wintypes.DWORD(32768)
        image = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(size)):
            return image.value
        return None
    finally:
        kernel32.CloseHandle(handle)


def provider_pid(provider: str, terminal_pid: int, own_pid: int, ancestors: list[int]) -> int | None:
    for pid in [own_pid, *ancestors]:
        image = process_image(pid)
        name = re.split(r"[\\/]", image or "")[-1].lower()
        if name in (provider, f"{provider}.exe"):
            return pid
        if pid == terminal_pid:
            break
    return None


def _atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f"{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def publish(
    provider: str,
    session_id: Any,
    *,
    environ: dict[str, str] | None = None,
    run_hc: Callable[[str], dict[str, Any] | None] | None = None,
    ancestors: list[int] | None = None,
    own_pid: int | None = None,
    now_ms: int | None = None,
) -> dict[str, Any] | None:
    env = os.environ if environ is None else environ
    terminal = str(env.get("HC_TERMINAL_ID") or "").strip()
    if not terminal:
        _warn("refusing: HC_TERMINAL_ID is not set")
        return None
    if not TERMINAL.fullmatch(terminal):
        _warn("refusing: HC_TERMINAL_ID is not a safe terminal address")
        return None
    if provider not in PROVIDERS:
        _warn(f"refusing: unknown provider {provider}")
        return None
    if not isinstance(session_id, str) or not session_id:
        return None
    pid = os.getpid() if own_pid is None else own_pid
    chain = ancestor_pids(pid) if ancestors is None else ancestors
    try:
        observed = (run_hc or _default_run_hc)(terminal)
    except Exception:
        observed = None
    reason = validate(terminal, observed, chain, pid)
    if reason:
        _warn(f"refusing {terminal}: {reason}")
        return None
    terminal_pid = observed["pid"]
    resolved_pid = provider_pid(provider, terminal_pid, pid, chain)
    if resolved_pid is None:
        _warn(f"refusing {terminal}: no {provider} executable in this terminal's ancestor chain")
        return None
    binding = {
        "v": 2,
        "terminal": terminal,
        "terminal_created_ms": observed["created_ms"],
        "provider": provider,
        "session_id": session_id,
        "provider_pid": resolved_pid,
        "written_ms": int(time.time() * 1000) if now_ms is None else now_ms,
    }
    try:
        _atomic_write_json(binding_path(terminal), binding)
    except OSError as exc:
        _warn(f"could not write {terminal}: {exc}")
        return None
    return binding
