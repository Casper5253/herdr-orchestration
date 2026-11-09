"""hx say: message a teammate, never an agent of another team.

The sender's team is this pane's host identity: the prefix of its host name, or
the `team` label of its hc terminal. A dev-codex pane can reach dev-claude and
dev-opencode but not hx-claude. Agent permissions allow `hx say` and ask for a
raw host prompt or `hc send`, which makes this the only unattended send path.

Every message is written to a per-sender outbox before delivery. A send the
host reports as started is marked delivered; any other outcome (a blocked lead,
a finished terminal, an unknown response) leaves it pending, so a report is
never lost and `hx say --pending` can retry it.
"""
from __future__ import annotations

import datetime
import json
import math
import subprocess
import sys
import uuid
from pathlib import Path

from hx import context, devin, host, paths, usage

def resolve(sender: str, target: str) -> tuple[str, host.Member]:
    """Teammate name and Member for a role or full name, unless it is not on the sender's team."""
    prefix = usage.TEAM_AGENT.fullmatch(sender)[1]
    name = target if target.startswith(prefix) else prefix + target
    team = usage.team_agents(prefix)
    member = team.get(name)
    if member is None:
        live = ", ".join(sorted(team)) or "none"
        raise ValueError(f"{target} is not a live agent on team {prefix[:-1]}; live teammates: {live}")
    return name, member


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
        session_id, sample = context.session_telemetry(agent, policy)
        value = sample.get("utilisation")
        threshold = policy.get("context", {}).get("pressure_threshold", 0.75)
        if (not context._fresh(sample, session_id, context._now())
                or not isinstance(value, (int, float)) or not math.isfinite(value)
                or not isinstance(threshold, (int, float)) or not math.isfinite(threshold)
                or value < threshold):
            return False
        if context.reset_pending(name, session_id, policy):
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
        if context.agent_info(name).get("kind") != devin.PROVIDER:
            return ""
        return devin.pane_denied_line(name)
    except (OSError, ValueError, TypeError, KeyError, RuntimeError, AttributeError, subprocess.SubprocessError):
        return ""


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _outbox_dir(sender: str) -> Path:
    return paths.hx_dir() / "outbox" / sender


def _write_outbox(sender: str, target: str, text: str) -> Path:
    directory = _outbox_dir(sender)
    directory.mkdir(parents=True, exist_ok=True)
    message_id = uuid.uuid4().hex
    record = {
        "id": message_id,
        "sender": sender,
        "target": target,
        "text": text,
        "created_at": _now(),
        "delivered": False,
        "delivered_at": None,
    }
    path = directory / f"{message_id}.json"
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return path


def _read_record(path: Path) -> dict | None:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def _mark_delivered(path: Path) -> None:
    record = _read_record(path)
    if record is None:
        return
    record["delivered"] = True
    record["delivered_at"] = _now()
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")


def pending_records(sender: str) -> list[tuple[Path, dict]]:
    """A sender's undelivered messages, oldest first."""
    directory = _outbox_dir(sender)
    if not directory.is_dir():
        return []
    pending = []
    for path in directory.glob("*.json"):
        record = _read_record(path)
        if record is not None and not record.get("delivered"):
            pending.append((str(record.get("created_at") or ""), path.name, path, record))
    pending.sort()
    return [(path, record) for _, _, path, record in pending]


def retry_pending() -> int:
    """List and retry this sender's pending messages; anything still unstarted stays pending."""
    detected = host.detect()
    if detected is None:
        raise ValueError("hx say runs only in a team host (an agent named <team>-<role>)")
    sender_member = detected.own()
    if not sender_member:
        raise ValueError("hx say runs only in a team host (an agent named <team>-<role>)")
    sender = sender_member["name"]
    pending = pending_records(sender)
    if not pending:
        print(f"No pending messages for {sender}.")
        return 0
    remaining = 0
    for path, record in pending:
        message_id = str(record.get("id") or path.stem)
        target = str(record.get("target") or "")
        text = str(record.get("text") or "")
        print(f"Pending {message_id} -> {target} ({record.get('created_at')})")
        result = detected.send_checked(target, text) if target and text else _started(False, "invalid")
        if result["started"] is True:
            _mark_delivered(path)
            print(f"Delivered {message_id} -> {target}")
        else:
            remaining += 1
            print(f"Still pending {message_id} -> {target} ({result['reason'] or 'not started'})")
    return 0 if remaining == 0 else 1


def _started(value: bool, reason: str | None) -> host.SendResult:
    return host.SendResult(
        started=value, status="unknown", turn=None, ended=False, reason=reason)


def say(target: str | None, message: str, *, force: bool = False) -> int:
    if not target:
        raise ValueError("hx say needs a teammate role or name, or --pending")
    detected = host.detect()
    if detected is None:
        raise ValueError("hx say runs only in a team host (an agent named <team>-<role>)")
    sender_member = detected.own()
    if not sender_member:
        raise ValueError("hx say runs only in a team host (an agent named <team>-<role>)")
    sender = sender_member["name"]
    name, destination = resolve(sender, target)
    text = with_header(sender, message)
    if not force and _under_pressure(name):
        return 1
    line = _denied_line_for(name)
    if line:
        text += " " + line
    path = _write_outbox(sender, name, text)
    if destination["status"] == "blocked":
        print(
            f"hx: {name} is blocked; message {path.stem} stays pending",
            file=sys.stderr,
        )
        return 1
    result = detected.send_checked(name, text)
    if result["started"] is True:
        _mark_delivered(path)
        return 0
    reason = result["reason"] or "not started"
    print(f"hx: message {path.stem} to {name} stays pending ({reason})", file=sys.stderr)
    return 1
