"""hx say: message a teammate, never an agent of another team.

The sender's team is the prefix of this pane's herdr agent (usage.own_agent_name),
so a dev-codex pane can reach dev-claude and dev-opencode but not hx-claude.
Agent permissions allow `hx say` and ask for raw `herdr agent prompt`, which
makes this the only unattended way to send a team message.
"""
from __future__ import annotations

import math
import subprocess
import sys

from hx import context, devin, usage

def resolve(sender: str, target: str) -> str:
    """Teammate name for a role or full name; ValueError unless it is a live agent on the sender's team."""
    prefix = usage.TEAM_AGENT.fullmatch(sender)[1]
    name = target if target.startswith(prefix) else prefix + target
    team = usage.team_agents(prefix)
    if name not in team:
        live = ", ".join(sorted(team)) or "none"
        raise ValueError(f"{target} is not a live agent on team {prefix[:-1]}; live teammates: {live}")
    return name


def with_header(sender: str, message: str) -> str:
    """Team messages start with a header naming the real sender; a header naming anyone else is refused."""
    if not message.startswith("[team"):
        return f"[team from {sender}] {message}"
    header = message.split("]", 1)[0]
    if not header.endswith(f"from {sender}"):
        raise ValueError(f"message header {header}] must name the sender {sender}")
    return message


def _under_pressure(name: str) -> bool:
    """Hold worker messages at fresh pressure; leave lead reporting reachable."""
    try:
        policy = usage.load_policy()
        agent = context.agent_info(name)
        if name == usage.lead_name(policy, usage.team_prefix(policy)):
            return False
        sample = context.telemetry(agent, policy)
        session_id = context._session(agent)
        value = sample.get("utilisation")
        threshold = policy.get("context", {}).get("pressure_threshold", 0.75)
        if (not context._fresh(sample, session_id, context._now())
                or not isinstance(value, (int, float)) or not math.isfinite(value)
                or not isinstance(threshold, (int, float)) or not math.isfinite(threshold)
                or value < threshold):
            return False
        print(
            f"Held: {name} context utilisation is {value:.2f} ({value:.0%}), "
            f"at or above the {threshold:.0%} pressure threshold. "
            f"Run `hx boundary {name}` for recovery steps, or `hx compact {name}` after updating its registered task checkpoint. "
            "Use --force only to send an intentional recovery/control message.",
            file=sys.stderr,
        )
        return True
    except (OSError, ValueError, TypeError, KeyError, RuntimeError, AttributeError, OverflowError, subprocess.SubprocessError):
        # Unknown or unavailable telemetry must not block team communication.
        return False


def _denied_line_for(name: str) -> str:
    """The deny line for a Devin pane target, or "" for anything else.

    A denied call cancels a Devin pane's turn and panes get no run preamble,
    so the pane's own deny list rides on the message. Any failure reading the
    agent kind or the config leaves the message unchanged.
    """
    try:
        if context.agent_info(name).get("agent") != devin.PROVIDER:
            return ""
        return devin.pane_denied_line(name)
    except (OSError, ValueError, TypeError, KeyError, RuntimeError, AttributeError, subprocess.SubprocessError):
        return ""


def say(target: str, message: str, *, force: bool = False) -> int:
    sender = usage.own_agent_name()
    if not sender:
        raise ValueError("hx say runs only in a herdr team pane (an agent named <team>-<role>)")
    name = resolve(sender, target)
    text = with_header(sender, message)
    if not force and _under_pressure(name):
        return 1
    line = _denied_line_for(name)
    if line:
        text += " " + line
    return subprocess.run(["herdr", "agent", "prompt", name, text], timeout=60).returncode
