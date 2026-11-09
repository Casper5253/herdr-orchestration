"""The one boundary to the team host.

`herdr` today; the Herdr app's `hc` core arrives as a second `Host` behind this
module. Nothing else in hx runs the host CLI or reads its wire format.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Protocol, TypedDict

TEAM_AGENT = re.compile(r"([a-z0-9][a-z0-9-]*-)[a-z0-9]+")

KINDS = frozenset({"claude", "codex", "opencode", "devin"})
STATUSES = frozenset({"idle", "working", "done", "blocked", "error"})
SETTLED = frozenset({"idle", "done"})
ERROR_TYPES = frozenset({"timeout", "agent_prompt_stalled", "agent_blocked", "error"})
WAIT_DEFAULT_MS = 300000
# The 7501 `app` word, the only evidence of which provider runs in a terminal.
HC_APP_KINDS = {
    "claude-code": "claude",
    "codex": "codex",
    "opencode": "opencode",
    "devin": "devin",
}
# A team or role label that can flow into a path and a logical name.
HC_LABEL = re.compile(r"[a-z0-9][a-z0-9-]*\Z")


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
    focused: bool | None


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
    focused = raw.get("focused")
    return Member(
        name=name,
        team=team,
        role=role,
        kind=kind if kind in KINDS else "unknown",
        status=status if status in STATUSES else "unknown",
        cwd=str(raw.get("cwd") or ""),
        address=str(raw.get("pane_id") or ""),
        session_id=session if isinstance(session, str) and session else None,
        focused=focused if isinstance(focused, bool) else None,
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

    def read(self, name: str, lines: int = 200) -> str:
        result = subprocess.run(
            ["herdr", "agent", "read", name, "--source", "recent-unwrapped", "--lines", str(lines)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=True, timeout=30, shell=False,
        )
        return result.stdout

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


def _label(value: Any) -> str:
    """A safe team/role label, or "" when it is missing or not a plain path-safe word."""
    text = str(value or "")
    return text if HC_LABEL.fullmatch(text) else ""


def _hc_binding_session(address: str, pid: Any) -> str | None:
    """The provider session bound to this terminal, or None when the binding is absent or stale.

    The OpenCode plugin writes <state>/hc-bindings/<terminal>.json with the
    process pid it saw. A binding for another terminal, or for a different pid
    (a relaunched program), is not this terminal's session.
    """
    if not address or not isinstance(pid, int) or isinstance(pid, bool):
        return None
    try:
        data = json.loads(
            (_hc_state_dir() / "hc-bindings" / f"{address}.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("terminal") != address or data.get("pid") != pid:
        return None
    session = data.get("session_id")
    return session if isinstance(session, str) and session else None


def hc_member(raw: dict[str, Any]) -> Member:
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
    address = str(raw.get("address") or "")
    return Member(
        name=name,
        team=team,
        role=role,
        kind=HC_APP_KINDS.get(app, "unknown"),
        status=state if state in STATUSES else "unknown",
        cwd=str(raw.get("cwd") or ""),
        address=address,
        session_id=_hc_binding_session(address, raw.get("pid")),
        focused=None,
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
            ["hc", *args], capture_output=True, text=True, encoding="utf-8",
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
        if not self.terminal_id:
            return None
        try:
            detail = self._detail(self.terminal_id)
            if detail.get("state") == "finished":
                return None
            found = hc_member(detail)
        except (OSError, ValueError, subprocess.SubprocessError):
            return None
        if found["address"] != self.terminal_id or not found["team"] or not found["role"]:
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
        members = []
        for row in terminals:
            address = row.get("address")
            if not isinstance(address, str) or not address:
                raise ValueError("hc returned a terminal without an address")
            members.append(hc_member(self._detail(address)))
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

    def send(self, name: str, text: str) -> int:
        terminal = self._member_named(name)["address"]
        if not terminal:
            raise ValueError(f"terminal {name} has no address")
        return self._run("send", terminal, text, timeout=60).returncode

    def send_checked(
        self,
        name: str,
        text: str,
        *,
        wait: bool = False,
        until: tuple[str, ...] = (),
        timeout_ms: int | None = None,
    ) -> SendResult:
        terminal = self._member_named(name)["address"]
        if not terminal:
            raise ValueError(f"terminal {name} has no address")
        args = ["send", terminal, text, "--json"]
        process_timeout = 60.0
        if wait:
            args.append("--wait")
            args.extend(("--timeout", f"{(timeout_ms or WAIT_DEFAULT_MS) / 1000:g}"))
            process_timeout = (timeout_ms or WAIT_DEFAULT_MS) / 1000 + 15
        try:
            completed = self._run(*args, timeout=process_timeout)
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

    def read(self, name: str, lines: int = 200) -> str:
        terminal = self._member_named(name)["address"]
        if not terminal:
            raise ValueError(f"terminal {name} has no address")
        completed = self._run("read", terminal, "--lines", str(lines))
        if completed.returncode != 0:
            raise RuntimeError(f"hc could not read terminal {name}")
        return completed.stdout


def prompt_command(name: str, text: str) -> str:
    """The CLI text that sends `text` to `name`, for instructions hx prints."""
    return f"herdr agent prompt {name} '{text}'"


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
