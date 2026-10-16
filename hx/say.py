"""hx say: message a teammate, never an agent of another team.

The sender's team is the prefix of this pane's herdr agent (usage.own_agent_name),
so a dev-codex pane can reach dev-claude and dev-opencode but not hx-claude.
Agent permissions allow `hx say` and ask for raw `herdr agent prompt`, which
makes this the only unattended way to send a team message.
"""
from __future__ import annotations

import subprocess

from hx import usage

ROLES = ("claude", "codex", "opencode")


def resolve(sender: str, target: str) -> str:
    """Teammate name for a role or full name; ValueError outside the sender's team."""
    prefix = usage.TEAM_AGENT.fullmatch(sender)[1]
    name = prefix + target if target in ROLES else target
    if not (name.startswith(prefix) and name[len(prefix):] in ROLES):
        raise ValueError(f"{target} is not on team {prefix[:-1]}; hx say only reaches {', '.join(prefix + r for r in ROLES)}")
    return name


def with_header(sender: str, message: str) -> str:
    """Team messages start with a header naming the real sender; a header naming anyone else is refused."""
    if not message.startswith("[team"):
        return f"[team from {sender}] {message}"
    header = message.split("]", 1)[0]
    if not header.endswith(f"from {sender}"):
        raise ValueError(f"message header {header}] must name the sender {sender}")
    return message


def say(target: str, message: str) -> int:
    sender = usage.own_agent_name()
    if not sender:
        raise ValueError("hx say runs only in a herdr team pane (<team>-claude, -codex or -opencode)")
    name = resolve(sender, target)
    return subprocess.run(["herdr", "agent", "prompt", name, with_header(sender, message)], timeout=60).returncode
