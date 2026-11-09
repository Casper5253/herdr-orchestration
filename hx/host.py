"""The one boundary to the team host.

`herdr` today; the Herdr app's `hc` core arrives as a second `Host` behind this
module. Nothing else in hx runs the host CLI or reads its wire format.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Any, Protocol, TypedDict

TEAM_AGENT = re.compile(r"([a-z0-9][a-z0-9-]*-)[a-z0-9]+")

KINDS = frozenset({"claude", "codex", "opencode", "devin"})
STATUSES = frozenset({"idle", "working", "done", "blocked", "error"})
SETTLED = frozenset({"idle", "done"})
ERROR_TYPES = frozenset({"timeout", "agent_prompt_stalled", "agent_blocked", "error"})
WAIT_DEFAULT_MS = 300000


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


def prompt_command(name: str, text: str) -> str:
    """The CLI text that sends `text` to `name`, for instructions hx prints."""
    return f"herdr agent prompt {name} '{text}'"


def detect() -> Host | None:
    """The host this process runs on.

    Outside an hc terminal the old herdr CLI is the host, with or without the
    pane marker: that is the behaviour hx shipped with. HcHost (HC_TERMINAL_ID)
    lands in unit U2; until then an hc terminal has no host.
    """
    if os.environ.get("HC_TERMINAL_ID"):
        return None
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
