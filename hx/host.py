"""The one boundary to the team host.

`herdr` today; the Herdr app's `hc` core arrives as a second `Host` behind this
module. Nothing else in hx runs the host CLI or reads its wire format.
"""

from __future__ import annotations

import functools
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Protocol, TypedDict

TEAM_AGENT = re.compile(r"([a-z0-9][a-z0-9-]*-)[a-z0-9]+")

KINDS = frozenset({"claude", "codex", "opencode", "devin"})
STATUSES = frozenset({"idle", "working", "done", "blocked", "error"})
SETTLED = frozenset({"idle", "done"})
ERROR_TYPES = frozenset({"timeout", "agent_prompt_stalled", "agent_blocked", "error"})
WAIT_DEFAULT_MS = 300000
# A typed slash command's Enter press gets a beat before the composer check.
SLASH_ENTER_SETTLE_SECONDS = 0.75
# The line a TUI types into starts with one of these: Devin `❭`, Codex `›`,
# OpenCode/Claude `>`/`❯`. The last one on screen is the live composer.
_COMPOSER_PROMPTS = ("❭", "›", "❯", ">", "»")
# The 7501 `app` word, the only evidence of which provider runs in a terminal.
HC_APP_KINDS = {
    "claude-code": "claude",
    "codex": "codex",
    "opencode": "opencode",
    "devin": "devin",
}
# A team or role label that can flow into a path and a logical name.
HC_LABEL = re.compile(r"[a-z0-9][a-z0-9-]*\Z")


class IdentityError(ValueError):
    """HC_TERMINAL_ID could not be verified as this process's own terminal.

    Raised instead of returning another terminal's labels or letting a caller
    fall back to a guessed prefix: an unverifiable identity is a failure, not
    an empty answer.
    """


class SendNotAttempted(ValueError):
    """The send never ran: no hc call went out for this message.

    Distinct from transport failures, where the text may have left: a caller
    catching this can mark the message pending, never uncertain.
    """


class HostToolMissing(FileNotFoundError):
    """A required host executable was not found in PATH or the install dirs."""


class ReadText(str):
    """Terminal text plus the host's completeness metadata.

    `hc read --json` answers a screen that may be a truncated or in-progress
    view: `complete` and `why` say whether the caller got the whole output.
    Hosts that cannot report completeness leave them None.
    """

    complete: bool | None
    why: str | None
    how: str | None

    def __new__(cls, text: str, *, complete=None, why=None, how=None) -> ReadText:
        instance = super().__new__(cls, text)
        instance.complete = complete
        instance.why = why
        instance.how = how
        return instance


class Member(TypedDict):
    """One live teammate, host-neutral."""

    name: str
    team: str
    role: str
    kind: str
    status: str
    cwd: str
    address: str
    session_id: str | None
    session_reason: str | None
    focused: bool | None
    turn: int | None
    created_ms: int | float | None


class SendResult(TypedDict):
    """A checked send, with the wait outcome kept separate from process success."""

    started: bool | None
    status: str
    turn: int | None
    ended: bool
    reason: str | None


class Host(Protocol):
    def own(self) -> Member | None: ...

    def all_members(self) -> list[Member] | None: ...

    def team(self, prefix: str) -> dict[str, Member]: ...

    def info(self, name: str) -> Member: ...

    def send(self, name: str, text: str) -> int: ...

    def send_checked(
        self,
        name: str,
        text: str,
        *,
        wait: bool = False,
        until: tuple[str, ...] = (),
        timeout_ms: int | None = None,
    ) -> SendResult: ...

    def read(self, name: str, lines: int) -> str: ...

    def send_slash(self, member: Member | str, command: str) -> bool: ...

    def press(self, member: Member | str, *keys: str) -> bool: ...


def _split_name(name: str) -> tuple[str, str]:
    head, sep, role = name.rpartition("-")
    if not sep or not head or not role:
        return "", name
    return head, role


def member(raw: dict[str, Any]) -> Member:
    """herdr agent JSON -> Member. The one place the wire shape is read."""
    name = str(raw.get("name") or "")
    team, role = _split_name(name)
    kind = str(raw.get("agent") or "unknown")
    status = str(raw.get("agent_status") or "unknown")
    session = (raw.get("agent_session") or {}).get("value")
    session = session if isinstance(session, str) and session else None
    focused = raw.get("focused")
    return Member(
        name=name,
        team=team,
        role=role,
        kind=kind if kind in KINDS else "unknown",
        status=status if status in STATUSES else "unknown",
        cwd=str(raw.get("cwd") or ""),
        address=str(raw.get("pane_id") or ""),
        session_id=session,
        session_reason="agent_session" if session else "no agent_session reported",
        focused=focused if isinstance(focused, bool) else None,
        turn=raw.get("turn") if isinstance(raw.get("turn"), int)
            and not isinstance(raw.get("turn"), bool) else None,
        created_ms=raw.get("created_ms") if isinstance(
            raw.get("created_ms"), (int, float))
            and not isinstance(raw.get("created_ms"), bool) else None,
    )


def _unknown_send(reason: str, *, started: bool | None = None, status: str = "unknown") -> SendResult:
    return SendResult(started=started, status=status, turn=None, ended=False, reason=reason)


def _error_send(error: Any, fallback: str) -> SendResult:
    """A Herdr error envelope, or its older result-type form, as a SendResult."""
    if not isinstance(error, dict):
        return _unknown_send(fallback)
    code = str(error.get("type") or error.get("code") or "")
    explicit = error.get("started")
    started = explicit if isinstance(explicit, bool) else None
    if code == "timeout":
        return _unknown_send("timeout", started=started)
    if code == "agent_prompt_stalled":
        return _unknown_send("stalled", started=False)
    if code == "agent_blocked":
        return _unknown_send("blocked", started=False, status="blocked")
    return _unknown_send(code or fallback, started=started)


def _send_result(data: Any, wait: bool, fallback: str) -> SendResult:
    """A `herdr agent prompt` response as SendResult, with no invented evidence."""
    if not isinstance(data, dict):
        return _unknown_send(fallback)
    if data.get("error") is not None:
        return _error_send(data["error"], fallback)
    result = data.get("result")
    if not isinstance(result, dict):
        return _unknown_send(fallback)
    if result.get("type") in ERROR_TYPES:
        return _error_send(result, fallback)
    if result.get("type") != "agent_prompted":
        return _unknown_send(fallback)
    agent = result.get("agent")
    if not isinstance(agent, dict):
        return _unknown_send("malformed response")
    status = agent.get("agent_status")
    status = status if status in STATUSES else "unknown"
    turn = agent.get("turn")
    turn = turn if isinstance(turn, int) and not isinstance(turn, bool) else None
    if status == "blocked":
        return SendResult(started=True, status=status, turn=turn, ended=False, reason="blocked")
    ended = bool(wait and status in SETTLED)
    return SendResult(started=True, status=status, turn=turn, ended=ended, reason=None)


class HerdrHost:
    """The standalone `herdr` CLI, behind the Host interface."""

    def __init__(self, pane_id: str | None = None) -> None:
        self.pane_id = pane_id if pane_id is not None else os.environ.get("HERDR_PANE_ID")

    def all_members(self) -> list[Member]:
        raw = subprocess.check_output(
            ["herdr", "agent", "list"],
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
        data = json.loads(raw.decode("utf-8"))
        agents = data.get("result", {}).get("agents")
        if not isinstance(agents, list) or any(not isinstance(agent, dict) for agent in agents):
            raise ValueError("Herdr returned no valid agent list")
        return [member(agent) for agent in agents]

    def own(self) -> Member | None:
        if not self.pane_id:
            return None
        try:
            agents = self.all_members()
        except Exception:
            return None
        for agent in agents:
            if agent["address"] == self.pane_id and TEAM_AGENT.fullmatch(agent["name"]):
                return agent
        return None

    def team(self, prefix: str) -> dict[str, Member]:
        return {
            agent["name"]: agent
            for agent in self.all_members()
            if agent["name"].startswith(prefix) and TEAM_AGENT.fullmatch(agent["name"])
        }

    def info(self, name: str) -> Member:
        return member(self._command("get", name)["result"]["agent"])

    def send(self, name: str, text: str) -> int:
        return subprocess.run(["herdr", "agent", "prompt", name, text], timeout=60).returncode

    def send_slash(self, member: Member | str, command: str) -> bool:
        """A native prompt carries the slash command on the standalone host."""
        name = str(member["name"]) if isinstance(member, dict) else member
        return self.send(name, command) == 0

    def press(self, member: Member | str, *keys: str) -> bool:
        # The standalone herdr CLI has no key-press transport.
        return False

    def send_checked(
        self,
        name: str,
        text: str,
        *,
        wait: bool = False,
        until: tuple[str, ...] = (),
        timeout_ms: int | None = None,
    ) -> SendResult:
        args = ["prompt", name, text]
        if wait:
            args.append("--wait")
            for state in until or ("idle", "done"):
                args.extend(("--until", state))
            timeout_ms = timeout_ms or WAIT_DEFAULT_MS
            args.extend(("--timeout", str(timeout_ms)))
        process_timeout = (timeout_ms or 60000) / 1000 + 10 if wait else 60
        try:
            completed = subprocess.run(
                ["herdr", "agent", *args], capture_output=True, text=True,
                encoding="utf-8", errors="replace", check=False,
                timeout=process_timeout, shell=False,
            )
        except subprocess.TimeoutExpired:
            return _unknown_send("timeout")
        except (OSError, subprocess.SubprocessError):
            return _unknown_send("host command failed")
        try:
            data = json.loads(completed.stdout)
        except (TypeError, ValueError):
            data = None
        if completed.returncode != 0:
            if isinstance(data, dict) and data.get("error") is not None:
                return _send_result(data, wait, f"herdr exited {completed.returncode}")
            return _unknown_send(f"herdr exited {completed.returncode}")
        return _send_result(data, wait, "malformed response")

    def read(self, name: str, lines: int = 200) -> ReadText:
        result = subprocess.run(
            ["herdr", "agent", "read", name, "--source", "recent-unwrapped", "--lines", str(lines)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=True, timeout=30, shell=False,
        )
        return ReadText(result.stdout, how="recent-unwrapped")

    def _command(self, *args: str, timeout: float = 30) -> dict[str, Any]:
        """A checked `herdr agent` call; callers go through the Host methods."""
        result = subprocess.run(
            ["herdr", "agent", *args], capture_output=True, text=True, encoding="utf-8",
            errors="replace", check=True, timeout=timeout, shell=False,
        )
        data = json.loads(result.stdout)
        if data.get("error") or (data.get("result") or {}).get("type") in ERROR_TYPES:
            raise RuntimeError("Herdr did not complete the request: " + result.stdout)
        return data


def _hc_state_dir() -> Path:
    env = os.environ.get("HX_STATE_DIR")
    return Path(env) if env else Path.home() / ".herdr-team" / "state"


# `%LOCALAPPDATA%/Programs/<dir>` install locations the PATH lookup falls back to.
_TOOL_INSTALL_DIRS = {
    "hc": "herdr-core",
    "herdr-app": "herdr-app",
}


def _tool_candidates(name: str) -> list[Path]:
    folder = _TOOL_INSTALL_DIRS.get(name)
    if not folder:
        return []
    programs = Path(os.environ.get("LOCALAPPDATA") or "") / "Programs"
    return [programs / folder / f"{name}.exe", programs / folder / name]


@functools.lru_cache(maxsize=None)
def tool_path(name: str) -> str:
    """A host executable's full path, resolved once: PATH, then known install dirs.

    A backend environment can lose PATH entries the direct command route had;
    a missing executable raises an error that names every searched location.
    """
    found = shutil.which(name)
    if found:
        return found
    for candidate in _tool_candidates(name):
        if candidate.is_file():
            return str(candidate)
    searched = [str(candidate) for candidate in _tool_candidates(name)] or ["<no known install dir>"]
    raise HostToolMissing(f"no {name} executable found; searched PATH and {'; '.join(searched)}")


def _windows_process_parents() -> dict[int, int]:
    """pid -> parent pid for every live process, from a Toolhelp32 snapshot."""
    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    snapshot = kernel32.CreateToolhelp32Snapshot(0x2, 0)
    if snapshot is None or snapshot == wintypes.HANDLE(-1).value:
        return {}
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        parents: dict[int, int] = {}
        if kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            while True:
                parents[entry.th32ProcessID] = entry.th32ParentProcessID
                if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                    break
        return parents
    finally:
        kernel32.CloseHandle(snapshot)


def _procfs_process_parents() -> dict[int, int]:
    """pid -> parent pid from /proc/<pid>/stat, for non-Windows hosts."""
    parents: dict[int, int] = {}
    try:
        entries = list(Path("/proc").iterdir())
    except OSError:
        return parents
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            tail = (entry / "stat").read_text().rpartition(")")[2].split()
            parents[int(entry.name)] = int(tail[1])
        except (OSError, ValueError, IndexError):
            continue
    return parents


def _process_parents() -> dict[int, int]:
    """pid -> parent pid for live processes; {} when the inventory is unavailable."""
    try:
        if os.name == "nt":
            return _windows_process_parents()
        return _procfs_process_parents()
    except Exception:
        return {}


def _is_descendant(pid: int, ancestor: int, parents: dict[int, int]) -> bool:
    """True when `ancestor` is `pid` itself or appears in its parent chain."""
    seen: set[int] = set()
    current = pid
    while current and current not in seen:
        if current == ancestor:
            return True
        seen.add(current)
        current = parents.get(current, 0)
    return False


def _label(value: Any) -> str:
    """A safe team/role label, or "" when it is missing or not a plain path-safe word."""
    text = str(value or "")
    return text if HC_LABEL.fullmatch(text) else ""


def _pid(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _hc_binding(raw: dict[str, Any], parents: dict[int, int] | None) -> tuple[str | None, str]:
    """(session_id, reason) from this terminal's binding file, per hc-binding v2.

    A v2 binding must name this terminal, this terminal generation
    (`created_ms`), this provider kind, and a provider pid that is the
    terminal's own, its foreground process, or a live descendant — the last
    covers a provider launched through a PowerShell wrapper. v1 plugin files
    still count under the old rule (plugin pid equal to the terminal pid)
    and the reason says so.
    """
    address = str(raw.get("address") or "")
    if not address:
        return None, "terminal has no address"
    try:
        data = json.loads(
            (_hc_state_dir() / "hc-bindings" / f"{address}.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None, "no readable binding file"
    if not isinstance(data, dict):
        return None, "binding file is not an object"
    terminal_pid = _pid(raw.get("pid"))
    version = data.get("v", 1)
    if type(version) is not int or version not in (1, 2):
        return None, "unsupported binding version"
    if version == 1:
        if (data.get("terminal") == address and terminal_pid is not None
                and data.get("pid") == terminal_pid):
            session = data.get("session_id")
            if isinstance(session, str) and session:
                return session, "v1 binding accepted by the pid rule"
            return None, "v1 binding has no session id"
        return None, "binding is not v2 and fails the v1 pid rule"
    if data.get("terminal") != address:
        return None, "binding names a different terminal"
    created_ms = raw.get("created_ms")
    if not isinstance(created_ms, (int, float)) or isinstance(created_ms, bool):
        return None, "terminal reports no generation"
    if data.get("terminal_created_ms") != created_ms:
        return None, "binding is for another terminal generation"
    status = raw.get("status")
    status = status if isinstance(status, dict) else {}
    kind = HC_APP_KINDS.get(str(status.get("app") or ""), "unknown")
    if data.get("provider") != kind:
        return None, f"binding provider {data.get('provider')!r} is not {kind!r}"
    provider_pid = _pid(data.get("provider_pid"))
    if provider_pid is None:
        return None, "binding has no provider pid"
    if parents is None:
        parents = _process_parents()
    if provider_pid not in parents:
        return None, "provider pid is not live"
    foreground = raw.get("foreground")
    foreground_pid = _pid(foreground.get("pid")) if isinstance(foreground, dict) else None
    if provider_pid != terminal_pid and provider_pid != foreground_pid:
        if terminal_pid is None:
            return None, "terminal has no live pid to compare"
        if not _is_descendant(provider_pid, terminal_pid, parents):
            return None, "provider pid is not this terminal's process or a live descendant"
    session = data.get("session_id")
    if not isinstance(session, str) or not session:
        return None, "binding has no session id"
    return session, "v2 binding"


def hc_member(raw: dict[str, Any], *, parents: dict[int, int] | None = None) -> Member:
    """One `hc get` Terminal -> Member.

    Team and role labels are the canonical logical identity. A raw terminal name
    never overrides them; when labels are absent or invalid the terminal is not
    a team member. A label that is not a safe path component is rejected here,
    at the host boundary.
    """
    labels = raw.get("labels")
    labels = labels if isinstance(labels, dict) else {}
    team = _label(labels.get("team"))
    role = _label(labels.get("role"))
    name = f"{team}-{role}" if team and role else str(raw.get("name") or "")
    status_raw = raw.get("status")
    status_raw = status_raw if isinstance(status_raw, dict) else {}
    app = str(status_raw.get("app") or "")
    state = str(status_raw.get("state") or "")
    session, session_reason = _hc_binding(raw, parents)
    return Member(
        name=name,
        team=team,
        role=role,
        kind=HC_APP_KINDS.get(app, "unknown"),
        status=state if state in STATUSES else "unknown",
        cwd=str(raw.get("cwd") or ""),
        address=str(raw.get("address") or ""),
        session_id=session,
        session_reason=session_reason,
        focused=None,
        turn=raw.get("turn") if isinstance(raw.get("turn"), int)
            and not isinstance(raw.get("turn"), bool) else None,
        created_ms=raw.get("created_ms") if isinstance(
            raw.get("created_ms"), (int, float))
            and not isinstance(raw.get("created_ms"), bool) else None,
    )


def _hc_error_result(kind: str, message: Any = None) -> SendResult:
    """An hc `{error: {kind}}` envelope as a SendResult. A refused send never started."""
    kind = str(kind or "")
    if kind == "blocked":
        return SendResult(started=False, status="blocked", turn=None, ended=False, reason="blocked")
    if kind:
        return SendResult(started=False, status="unknown", turn=None, ended=False, reason=kind)
    return _unknown_send(str(message or "hc send failed"), started=False)


def _hc_send_result(data: Any, until: tuple[str, ...] = ()) -> SendResult:
    """An hc SendResult as the host-neutral one, with the waited outcome kept.

    A turn in the response proves the text went in; `started` is only the
    agent-work signal. A waited turn counts as ended only when its final status
    is one the caller asked for (idle/done by default). Blocked, error, exit and
    timeout are never completion.
    """
    if not isinstance(data, dict):
        return _unknown_send("malformed response")
    error = data.get("error")
    if isinstance(error, dict):
        return _hc_error_result(str(error.get("kind") or ""), error.get("message"))
    if not {"started", "status", "turn", "wait"} & set(data):
        return _unknown_send("malformed response")
    started = data.get("started")
    started = started if isinstance(started, bool) else None
    status = data.get("status")
    status = status if status in STATUSES else "unknown"
    turn = data.get("turn")
    turn = turn if isinstance(turn, int) and not isinstance(turn, bool) else None
    waited = data.get("wait")
    if not isinstance(waited, dict):
        return SendResult(started=started, status=status, turn=turn, ended=False, reason=None)
    waited_status = waited.get("status")
    if waited_status in STATUSES:
        status = waited_status
    waited_turn = waited.get("turn")
    if isinstance(waited_turn, int) and not isinstance(waited_turn, bool):
        turn = waited_turn
    conditions = until or ("idle", "done")
    waited_reason = str(waited.get("reason") or "")
    # An exit can keep the last idle/done status. A blocked or error status is
    # never completion, even when the caller put it in `until`.
    ended = (
        waited.get("ended") is True
        and waited_reason != "exit"
        and status not in ("blocked", "error")
        and status in conditions
    )
    reason = None
    if not ended:
        if status in ("blocked", "error"):
            reason = status
        else:
            reason = waited_reason or "timeout"
    return SendResult(started=started, status=status, turn=turn, ended=ended, reason=reason)


class HcHost:
    """The Herdr app core (`hc`), behind the same Host interface."""

    def __init__(self, terminal_id: str | None = None) -> None:
        self.terminal_id = (
            terminal_id if terminal_id is not None else os.environ.get("HC_TERMINAL_ID")
        )

    def _run(self, *args: str, timeout: float = 30) -> subprocess.CompletedProcess:
        return subprocess.run(
            [tool_path("hc"), *args], capture_output=True, text=True, encoding="utf-8",
            errors="replace", check=False, timeout=timeout, shell=False,
        )

    @staticmethod
    def _json(completed: subprocess.CompletedProcess) -> Any:
        try:
            return json.loads(completed.stdout)
        except (TypeError, ValueError):
            return None

    def _detail(self, terminal: str) -> dict[str, Any]:
        completed = self._run("get", terminal, "--json")
        data = self._json(completed)
        if completed.returncode != 0 or not isinstance(data, dict):
            raise ValueError(f"hc could not read terminal {terminal}")
        return data

    def own(self) -> Member | None:
        """This process's own team member, validated against the live terminal.

        A stale HC_TERMINAL_ID must never adopt another terminal's labels or
        silently yield a fallback prefix. A finished terminal, details for a
        different address, or a terminal pid that is not an ancestor of this
        process is an IdentityError. Only a live terminal that is verifiably
        ours but carries no team labels still answers None: it is genuinely
        not a team member.
        """
        if not self.terminal_id:
            return None
        try:
            detail = self._detail(self.terminal_id)
        except HostToolMissing:
            raise
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            raise IdentityError(
                f"hc could not verify terminal {self.terminal_id}: {exc}") from exc
        if detail.get("state") == "finished":
            raise IdentityError(
                f"HC_TERMINAL_ID {self.terminal_id} names a finished terminal")
        if str(detail.get("address") or "") != self.terminal_id:
            raise IdentityError(
                f"HC_TERMINAL_ID {self.terminal_id} returned details for "
                f"{detail.get('address') or 'another terminal'}")
        pid = _pid(detail.get("pid"))
        parents = _process_parents()
        if pid is None or not _is_descendant(os.getpid(), pid, parents):
            raise IdentityError(
                f"terminal {self.terminal_id} pid {pid} is not this process or an ancestor")
        found = hc_member(detail, parents=parents)
        if not found["team"] or not found["role"]:
            return None
        return found

    def all_members(self) -> list[Member]:
        completed = self._run("ls", "--json")
        listing = self._json(completed)
        terminals = listing.get("terminals") if isinstance(listing, dict) else None
        if completed.returncode != 0 or not isinstance(terminals, list):
            raise ValueError("hc returned no valid terminal list")
        if any(not isinstance(row, dict) for row in terminals):
            raise ValueError("hc returned no valid terminal list")
        parents = _process_parents()
        members = []
        for row in terminals:
            address = row.get("address")
            if not isinstance(address, str) or not address:
                raise ValueError("hc returned a terminal without an address")
            members.append(hc_member(self._detail(address), parents=parents))
        return members

    def team(self, prefix: str) -> dict[str, Member]:
        team = _label(prefix[:-1] if prefix.endswith("-") else prefix)
        members: dict[str, Member] = {}
        if not team:
            return members
        for agent in self.all_members():
            if agent["team"] != team:
                continue
            name = agent["name"]
            if name in members:
                raise ValueError(
                    f"duplicate role {name} on team {team}: "
                    f"{members[name]['address']} and {agent['address']}"
                )
            members[name] = agent
        return members

    def _member_named(self, name: str) -> Member:
        """The one live member whose logical identity is `name`, resolved from labels.

        A global `hc get <name>` is never used: an unnamed lead has no hc name,
        and another team's terminal can carry the same raw name.
        """
        matches = [member for member in self.all_members() if member["name"] == name]
        if not matches:
            raise ValueError(f"no live terminal has the logical identity {name}")
        if len(matches) > 1:
            raise ValueError(f"logical identity {name} is ambiguous")
        return matches[0]

    def info(self, name: str) -> Member:
        return self._member_named(name)

    def _send_address(self, name: str) -> str:
        """The validated terminal address for a send.

        Resolution runs before any hc call, so a failure here provably means
        the text never left: SendNotAttempted, distinct from a transport error.
        """
        try:
            terminal = self._member_named(name)["address"]
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            raise SendNotAttempted(str(exc)) from exc
        if not terminal:
            raise SendNotAttempted(f"terminal {name} has no address")
        return terminal

    def send(self, name: str, text: str) -> int:
        return self._run("send", self._send_address(name), text, timeout=60).returncode

    def press(self, member: Member | str, *keys: str) -> bool:
        """Press `keys` in the member's terminal via the resolved `hc` path.

        `hc keys <address> <key names...>` types them exactly; True only when
        the key call was accepted. Resolution failures are SendNotAttempted:
        nothing was pressed.
        """
        if isinstance(member, dict):
            address = str(member.get("address") or "")
            if not address:
                raise SendNotAttempted("the member's terminal has no address")
        else:
            address = self._send_address(member)
        return self._run("keys", address, *keys, timeout=30).returncode == 0

    def send_slash(self, member: Member | str, command: str) -> bool:
        """Type a slash command into the member's terminal as keys, then Enter.

        `hc send` wraps text in `<hc from=...>`, so a slash command sent as a
        message never reaches the provider's native command handling. Typing
        the keys does. The first Enter can land in the provider's
        slash-command autocomplete instead of submitting: the screen is read,
        one more Enter goes out only while the command still sits in the
        composer, and True means the staged text is verifiably gone.
        """
        if not command.startswith("/"):
            raise ValueError("send_slash types slash commands like /compact or /clear")
        if isinstance(member, dict):
            address = str(member.get("address") or "")
            if not address:
                raise SendNotAttempted("the member's terminal has no address")
        else:
            address = self._send_address(member)
        if not (self._run("keys", address, *command, timeout=30).returncode == 0
                and self._run("keys", address, "enter", timeout=30).returncode == 0):
            return False
        time.sleep(SLASH_ENTER_SETTLE_SECONDS)
        staged = self._staged_command(address, command)
        if staged is False:
            return True
        if staged is None:
            return False  # keys went out but the composer state cannot be proven
        if self._run("keys", address, "enter", timeout=30).returncode != 0:
            return False
        time.sleep(SLASH_ENTER_SETTLE_SECONDS)
        return self._staged_command(address, command) is False

    def _staged_command(self, address: str, command: str) -> bool | None:
        """Whether the typed slash command still sits in the terminal's composer.

        The composer is the last prompt-glyph line on the screen (`❭` in
        Devin, `›` in Codex, `>` elsewhere): scrollback and output above it can
        echo the command but never stage it, and a staged `❭ /clearance` is
        not the `/clear` this call typed. None means the screen could not be
        read: unverified, never a reason to press Enter again.
        """
        try:
            completed = self._run("read", address, "--json", "--lines", "12")
            data = self._json(completed)
        except (OSError, subprocess.SubprocessError):
            return None
        text = data.get("text") if isinstance(data, dict) else None
        if not isinstance(text, str):
            return None
        for line in reversed(text.rstrip().splitlines()[-6:]):
            stripped = line.strip()
            if stripped[:1] in _COMPOSER_PROMPTS:
                return stripped[1:].strip() == command
        return False

    def send_checked(
        self,
        name: str,
        text: str,
        *,
        wait: bool = False,
        until: tuple[str, ...] = (),
        timeout_ms: int | None = None,
    ) -> SendResult:
        terminal = self._send_address(name)
        args = ["send", terminal, text, "--json"]
        process_timeout = 60.0
        if wait:
            args.append("--wait")
            args.extend(("--timeout", f"{(timeout_ms or WAIT_DEFAULT_MS) / 1000:g}"))
            process_timeout = (timeout_ms or WAIT_DEFAULT_MS) / 1000 + 15
        try:
            completed = self._run(*args, timeout=process_timeout)
        except HostToolMissing as exc:
            raise SendNotAttempted(str(exc)) from exc
        except subprocess.TimeoutExpired:
            return _unknown_send("timeout")
        except (OSError, subprocess.SubprocessError):
            return _unknown_send("host command failed")
        data = self._json(completed)
        if data is None:
            try:
                data = json.loads(completed.stderr)
            except (TypeError, ValueError):
                data = None
        if completed.returncode != 0:
            if isinstance(data, dict) and isinstance(data.get("error"), dict):
                error = data["error"]
                return _hc_error_result(str(error.get("kind") or ""), error.get("message"))
            return _unknown_send(f"hc exited {completed.returncode}")
        return _hc_send_result(data, until)

    def read(self, name: str, lines: int = 200) -> ReadText:
        terminal = self._member_named(name)["address"]
        if not terminal:
            raise ValueError(f"terminal {name} has no address")
        completed = self._run("read", terminal, "--json", "--lines", str(lines))
        data = self._json(completed)
        if (completed.returncode != 0 or not isinstance(data, dict)
                or not isinstance(data.get("text"), str)):
            raise RuntimeError(f"hc could not read terminal {name}")
        complete = data.get("complete")
        why = data.get("why")
        how = data.get("how")
        return ReadText(
            data["text"],
            complete=complete if isinstance(complete, bool) else None,
            why=why if isinstance(why, str) else None,
            how=how if isinstance(how, str) else None,
        )


def prompt_command(name: str, text: str) -> str:
    """The CLI text that sends `text` to `name`, for instructions hx prints.

    On the app core a standalone `herdr` command can never appear: a slash
    command is typed as keys (`hc send` would wrap it so the provider could
    not run it) and ordinary text goes through `hc send`.
    """
    if not os.environ.get("HC_TERMINAL_ID"):
        return f"herdr agent prompt {name} '{text}'"
    bare = name[1:-1] if len(name) > 2 and name.startswith("'") and name.endswith("'") else name
    address = HcHost()._member_named(bare)["address"]
    if not address:
        raise ValueError(f"terminal {name} has no address")
    executable = "& '" + tool_path("hc").replace("'", "''") + "'"
    if text.startswith("/"):
        keys = " ".join(text)
        return f"{executable} keys {address} {keys}; {executable} keys {address} enter"
    quoted_text = "'" + text.replace("'", "''") + "'"
    return f"{executable} send {address} {quoted_text}"


def detect() -> Host | None:
    """The host this process runs on.

    A terminal of the Herdr app's core (`HC_TERMINAL_ID`) uses `hc`; anything
    else uses the standalone `herdr` CLI, with or without the pane marker.
    """
    if os.environ.get("HC_TERMINAL_ID"):
        return HcHost()
    return HerdrHost()


def all_members() -> list[Member] | None:
    """Every live agent on the detected host, or None outside one.

    The full roster includes unnamed panes and other teams: Devin's concurrency
    cap is account-wide. An unknown roster is None, and admission refuses it.
    """
    detected = detect()
    if detected is None:
        return None
    return detected.all_members()
