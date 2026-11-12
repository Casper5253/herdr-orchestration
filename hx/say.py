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
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

from hx import context, devin, host, ledger, paths, usage

# A process that dies between claiming and marking a record leaves the claim;
# after this long the delivery is unknown, not unsent.
CLAIM_STALE_SECONDS = 120
# Live probe on 9 Nov: a 103-char message arrived automatically; 1120-1312 char
# report bodies stayed in Codex's composer as pasted content although hc
# returned started=true. Long reports to an hc Codex lead travel as a short
# pointer to the full outbox record instead.
UNCONFIRMED_LONG_CHARS = 1000
# The pointer must stay inside the transport bound that the probe showed works.
POINTER_MAX_CHARS = 280
# How long `hx say` watches an idle receiver for a turn to start before it
# checks for unsubmitted text, and how long the Enter press gets to settle.
SUBMIT_POLL_SECONDS = 10.0  # a busy receiver's turn advances at its next provider turn
ENTER_SETTLE_SECONDS = 0.75
# The host explicitly refused or never accepted the text; a retry is safe.
UNSENT_REASONS = frozenset({
    "blocked", "finished", "unavailable", "status_mismatch", "not_found",
    "machine_unknown", "invalid_params", "config", "stalled",
    "agent_blocked", "agent_prompt_stalled",
})

def resolve(sender: host.Member, target: str) -> tuple[str, host.Member]:
    """Teammate name and Member for a role or full name, unless it is not on the sender's team.

    Authorization comes from the sender's authoritative team label, never from
    reparsing the joined display name: role `foo-bar` in team `dev` must not
    resolve to team `dev-foo`.
    """
    team = str(sender["team"])
    if not host.HC_LABEL.fullmatch(team):
        raise ValueError("sender has no valid team label")
    prefix = team + "-"
    name = target if target.startswith(prefix) else prefix + target
    members = usage.team_agents(prefix)
    member = members.get(name)
    if member is None:
        live = ", ".join(sorted(members)) or "none"
        raise ValueError(f"{target} is not a live agent on team {team}; live teammates: {live}")
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


_LEASE_READ_ERRORS = (OSError, ValueError, TypeError, KeyError, RuntimeError, AttributeError)


def _compaction_hold(led: ledger.Ledger, name: str) -> str | None:
    """The recorded session state when hx itself is compacting the target, else None.

    `hx compact` writes `compacting` on the session lease before typing
    /compact and leaves `compact-unknown` when the outcome is unproven. A
    message sent meanwhile dies in the compaction, so say holds on that
    recorded lease state — never on screen text. The caller holds the
    leases lock across this check and the delivery claim, so no `hx
    compact` reservation can interleave before the delivery is reserved.
    Unreadable leases raise: the caller fails closed, since a state it
    cannot read is a state it cannot rule out.
    """
    sessions = led._load_leases().get("sessions", {})
    states = [
        str(entry.get("state"))
        for key, entry in sessions.items()
        if isinstance(entry, dict) and (key == name or entry.get("agent") == name)
    ]
    for state in ("compacting", "compact-unknown"):
        if state in states:
            print(
                f"Held: {name} session is {state}; hx recorded the compaction. "
                f"Resend with `hx say --pending` once it clears "
                f"(`hx context recover {name}` releases compact-unknown). "
                "Use --force only to send an intentional recovery/control message.",
                file=sys.stderr,
            )
            return state
    return None


def _denied_line_for(destination: host.Member) -> str:
    """The deny line for a Devin pane target, or "" for anything else.

    A denied call cancels a Devin pane's turn and panes get no run preamble,
    so the pane's own deny list rides on the message. The kind comes from the
    validated Member; any failure reading the config leaves the message unchanged.
    """
    try:
        if destination["kind"] != devin.PROVIDER:
            return ""
        return devin.pane_denied_line(destination["name"])
    except (OSError, ValueError, TypeError, KeyError, RuntimeError, AttributeError, subprocess.SubprocessError):
        return ""


def _host_member(detected: host.Host, name: str) -> host.Member | None:
    try:
        return detected.info(name)
    except Exception:
        return None


def _host_status(detected: host.Host, name: str) -> str:
    member = _host_member(detected, name)
    return str(member["status"]) if member else "unknown"


def _await_advance(detected: host.Host, name: str, sent_turn: int | None, seconds: float) -> bool:
    """Submission evidence: the receiver's turn number advanced past ours.

    `hc get` reports no progress counter finer than `turn`. Status alone is
    never proof — a stale or unrelated working status would mark an
    unsubmitted message delivered and prevent retry.
    """
    deadline = time.monotonic() + seconds
    while True:
        member = _host_member(detected, name)
        if member is None:
            return False
        turn = member.get("turn")
        if (isinstance(sent_turn, int) and not isinstance(sent_turn, bool)
                and isinstance(turn, int) and not isinstance(turn, bool)
                and turn > sent_turn):
            return True
        if member["status"] in ("blocked", "error"):
            return False
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.25)


def _sits_unsubmitted(detected: host.Host, name: str, text: str) -> bool:
    """The pasted text is still in the receiver's composer.

    `hc send` wraps the body in <hc from=...> tags; a stuck paste shows the
    whole blob above the prompt line, ending in </hc>. Enter is pressed only
    when that shape is visible, and never twice for one delivery.
    """
    try:
        view = str(detected.read(name, 60))
    except Exception:
        return False
    tail = text[-20:]
    return bool(tail) and tail in view[-400:] and "</hc>" in view[-200:]


def _submission_evidence(
    detected: host.Host,
    name: str,
    text: str,
    result: host.SendResult,
) -> str:
    """What an accepted hc send actually did: submitted, queued, or uncertain.

    hc's exit code says nothing about submission: a paste can sit in the
    receiver's composer while hc reports started. A receiver already working
    got the text queued to its turn; an idle one is watched briefly, then the
    unsubmitted-paste check decides whether one Enter press is owed.
    """
    if result["status"] == "working":
        if _await_advance(detected, name, result["turn"], SUBMIT_POLL_SECONDS):
            return "queued"
        return "uncertain"
    if _await_advance(detected, name, result["turn"], SUBMIT_POLL_SECONDS):
        return "submitted"
    member = _host_member(detected, name)
    if member is None or member["status"] not in ("idle", "done"):
        return "uncertain"
    # Settled with no visible paste and no turn advance proves nothing: an
    # incomplete read, an unavailable composer, or a vanished paste would all
    # look identical — the outcome stays uncertain, never delivered.
    if not _sits_unsubmitted(detected, name, text):
        return "uncertain"
    if member["status"] != "idle":
        return "uncertain"
    try:
        pressed = detected.press(member, "enter")
    except Exception:
        pressed = False
    if not pressed:
        return "uncertain"
    time.sleep(ENTER_SETTLE_SECONDS)
    fresh = _host_member(detected, name)
    if (fresh is not None and isinstance(fresh.get("turn"), int)
            and isinstance(result["turn"], int) and not isinstance(result["turn"], bool)
            and fresh["turn"] > result["turn"]):
        return "submitted"
    return "uncertain"


_OUTCOME_WORDS = {
    "delivered": "submitted",
    "pending": "pending",
    "uncertain": "uncertain",
    "failed": "failed",
}


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _sender_key(member: host.Member) -> str:
    """A path-safe outbox key built only from validated team and role labels.

    The separator is not allowed in a label, so tuples that join to the same
    display name (`dev-foo`+`bar` and `dev`+`foo-bar`) get distinct keys.
    """
    team = str(member["team"])
    role = str(member["role"])
    if not host.HC_LABEL.fullmatch(team) or not host.HC_LABEL.fullmatch(role):
        raise ValueError("sender has no valid team and role labels for an outbox key")
    return f"{team}__{role}"


def _outbox_dir(sender_key: str, *, legacy: bool = False) -> Path:
    team, sep, role = sender_key.partition("__")
    if not sep or not host.HC_LABEL.fullmatch(team) or not host.HC_LABEL.fullmatch(role):
        raise ValueError("invalid outbox sender key")
    base = (paths.hx_dir() / "outbox").resolve()
    directory = (base / (f"{team}-{role}" if legacy else sender_key)).resolve()
    if directory.parent != base:
        raise ValueError("outbox path escapes its sender directory")
    return directory


def _atomic_write(path: Path, text: str) -> None:
    """Write through a flushed sibling temp file, then replace the final path."""
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def _write_outbox(sender_key: str, sender: str, target: str, body: str, text: str) -> Path:
    directory = _outbox_dir(sender_key)
    directory.mkdir(parents=True, exist_ok=True)
    message_id = uuid.uuid4().hex
    record = {
        "id": message_id,
        "sender": sender,
        "sender_key": sender_key,
        "target": target,
        "body": body,
        "text": text,
        "created_at": _now(),
        "updated_at": _now(),
        "state": "pending",
        "delivered": False,
        "delivered_at": None,
    }
    path = directory / f"{message_id}.json"
    _atomic_write(path, json.dumps(record, indent=2) + "\n")
    return path


def _read_record(path: Path) -> dict | None:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def _record_state(record: dict) -> str:
    state = record.get("state")
    if state in ("pending", "delivered", "uncertain", "failed"):
        return state
    return "delivered" if record.get("delivered") else "pending"


def _write_state(path: Path, state: str, **fields: object) -> None:
    record = _read_record(path)
    if record is None:
        return
    record.update(fields)
    record["state"] = state
    record["delivered"] = state == "delivered"
    record["updated_at"] = _now()
    if state == "delivered":
        record["delivered_at"] = _now()
    _atomic_write(path, json.dumps(record, indent=2) + "\n")


def records_for(sender: str) -> list[tuple[str, Path, dict | None]]:
    """A sender's unresolved records, oldest first; a corrupt record is kept, never dropped."""
    entries = []
    directories = {_outbox_dir(sender), _outbox_dir(sender, legacy=True)}
    for path in (path for directory in directories for path in directory.glob("*.json")):
        record = _read_record(path)
        if record is not None and _record_state(record) in ("delivered", "failed"):
            continue
        stamp = str(record.get("created_at") or "") if record else ""
        try:
            stamp = stamp or f"{path.stat().st_mtime:020.6f}"
        except OSError:
            stamp = f"{stamp}~"
        entries.append((stamp, path.name, path, record))
    entries.sort()
    return [(path, record, record and _record_state(record) or "corrupt")
            for _, _, path, record in entries]


def _claim_path(path: Path) -> Path:
    return path.with_name(f"{path.name}.claim")


def _release_claim(path: Path) -> None:
    try:
        _claim_path(path).unlink()
    except OSError:
        pass


def _claim(path: Path) -> str:
    """Claim a record for this process: claimed, busy, or stale."""
    claim = _claim_path(path)
    try:
        fd = os.open(claim, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            age = time.time() - claim.stat().st_mtime
        except OSError:
            age = CLAIM_STALE_SECONDS + 1
        if age <= CLAIM_STALE_SECONDS:
            return "busy"
        # The claimant may have delivered before dying; never replay it.
        _write_state(path, "uncertain", note="stale delivery claim")
        _release_claim(path)
        return "stale"
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"pid": os.getpid(), "at": _now()}))
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        _release_claim(path)
        raise
    return "claimed"


def _delivery_state(result: host.SendResult) -> tuple[str, str | None]:
    """Transport acceptance is separate from the agent-work signal.

    A turn in the response means the text went in. An explicit host refusal
    means a retry is safe. Anything else is uncertain and needs reconciliation,
    never a blind replay.
    """
    reason = result["reason"]
    if reason in UNSENT_REASONS:
        return "pending", str(reason)
    turn = result["turn"]
    if isinstance(turn, int) and not isinstance(turn, bool):
        return "delivered", None
    if result["started"] is True and result["status"] not in ("blocked", "error"):
        return "delivered", None
    return "uncertain", str(reason or "delivery not confirmed")


def _pointer_message(sender: str, path: Path) -> str | None:
    """A short, sender-authenticated pointer to the full outbox record."""
    try:
        record = path.resolve()
    except OSError:
        return None
    text = with_header(sender, f"Long report ready. Read {record}.")
    return text if len(text) <= POINTER_MAX_CHARS else None


def _deliver(
    detected: host.Host,
    sender_member: host.Member,
    target: str,
    body: str,
    path: Path,
    *,
    force: bool = False,
) -> tuple[str, str | None, tuple[str, str]]:
    """The one guarded delivery path, shared by a first send and a retry.

    Returns the durable record state, a reason, and a `(where, outcome)` receipt:
    the destination address plus `submitted`, `queued`, `pending`, `uncertain`
    or `failed` — the short line `hx say` prints.
    """
    sender = sender_member["name"]
    try:
        name, destination = resolve(sender_member, target)
    except ValueError as exc:
        # The role is gone (a lead handover left records for the old name):
        # report it once with the reason and resolve the record, never reprint.
        _write_state(path, "failed", note=f"undeliverable: {exc}")
        return "failed", f"undeliverable: {exc}", (target, "failed")
    try:
        text = with_header(sender, body)
    except ValueError as exc:
        _write_state(path, "failed", note=f"undeliverable: {exc}")
        return "failed", f"undeliverable: {exc}", (name, "failed")
    where = str(destination["address"]) or name
    if not force and _under_pressure(name):
        return "pending", "pressure", (where, "pending")
    line = _denied_line_for(destination)
    if line:
        text += " " + line
    pointer = (
        isinstance(detected, host.HcHost)
        and destination["kind"] == "codex"
        and len(body) > UNCONFIRMED_LONG_CHARS
    )
    if pointer:
        short = _pointer_message(sender, path)
        if short is None:
            _write_state(path, "pending", note="pointer too long")
            return "pending", "pointer too long", (where, "pending")
        text = short
    claimed = _claim(path)
    if claimed == "busy":
        return "pending", "delivery in flight", (where, "pending")
    if claimed == "stale":
        return "uncertain", "stale delivery claim", (where, "uncertain")
    release = True
    try:
        current = _read_record(path)
        if current is None:
            return "uncertain", "record unreadable under claim", (where, "uncertain")
        current_state = _record_state(current)
        if current_state != "pending":
            return current_state, current.get("note"), (where, _OUTCOME_WORDS.get(current_state, "uncertain"))
        _write_state(path, "pending", body=body, target=name, text=text,
                     pointer=pointer)
        if destination["status"] == "blocked":
            return "pending", "blocked", (where, "pending")
        led = ledger.default_ledger()
        deliver_key = f"deliver:{name}"
        deliver_token = str(uuid.uuid4())
        try:
            # The hold check and the delivery claim share one leases-lock
            # critical section, so a compact reservation cannot interleave;
            # the lock is released before host I/O so a stalled send never
            # blocks unrelated sends or compactions. The claim itself stops a
            # compaction of this agent mid-send; it heartbeats while the send
            # runs so a dead sender is reclaimed instead of holding forever.
            # An unreadable leases file fails closed — a state that cannot
            # be read cannot be ruled out.
            with led._leases_lock():
                claimed = False
                try:
                    if not force:
                        hold = _compaction_hold(led, name)
                        if hold:
                            return "pending", f"session {hold}", (where, "pending")
                    leases = led._load_leases()
                    sessions = leases.setdefault("sessions", {})
                    existing = sessions.get(deliver_key)
                    if isinstance(existing, dict) and not ledger._heartbeat_dead(existing):
                        print(
                            f"Held: a delivery to {name} is already in flight; "
                            "resend with `hx say --pending` once it finishes.",
                            file=sys.stderr,
                        )
                        return "pending", "delivery in flight", (where, "pending")
                    stamp = ledger._utcnow().isoformat()
                    sessions[deliver_key] = {
                        "agent": name, "session_id": context._session(destination),
                        "state": "delivering", "token": deliver_token,
                        "started_at": stamp, "heartbeat_at": stamp}
                    led._save_leases(leases)
                    claimed = True
                except _LEASE_READ_ERRORS as exc:
                    if not force:
                        print(
                            f"Held: cannot read session leases at {led.leases_path} "
                            f"({exc}); resend with `hx say --pending` once readable, "
                            "or --force to bypass.",
                            file=sys.stderr,
                        )
                        return "pending", f"leases unreadable: {exc}", (where, "pending")
                    # --force is the intentional bypass: an unwritable
                    # reservation degrades to an unreserved send.
            heartbeat = ledger.LeaseHeartbeat(led, deliver_key, deliver_token)
            if claimed:
                heartbeat.start()
            try:
                try:
                    result = detected.send_checked(name, text)
                except host.SendNotAttempted as exc:
                    # Provably never sent (vanished target, missing hc): pending, and
                    # the claim releases so --pending can retry it immediately.
                    try:
                        _write_state(path, "pending", note=str(exc))
                    except OSError:
                        pass
                    return "pending", str(exc), (where, "pending")
                except Exception:
                    # The send may have happened. Keep the claim so a later retry sees
                    # an in-flight or stale claim and never replays it blindly.
                    release = False
                    try:
                        _write_state(path, "uncertain", note="send raised")
                    except OSError:
                        pass
                    raise
            finally:
                heartbeat.stop()

                def release_delivery(leases_data: dict) -> None:
                    entry = leases_data.get("sessions", {}).get(deliver_key)
                    if isinstance(entry, dict) and entry.get("token") == deliver_token:
                        del leases_data["sessions"][deliver_key]

                try:
                    led.mutate_leases(release_delivery)
                except Exception:
                    pass  # an unreleased reservation heartbeats out and recovers
        except ledger.LockTimeout as exc:
            print(
                f"Held: session leases lock at {led.leases_path} is unavailable "
                f"({exc}); resend with `hx say --pending` once free, or --force to bypass.",
                file=sys.stderr,
            )
            return "pending", f"leases lock unavailable: {exc}", (where, "pending")
        state, reason = _delivery_state(result)
        outcome = _OUTCOME_WORDS.get(state, "uncertain")
        if state == "delivered":
            # Turn/paste evidence applies to every host except the standalone
            # herdr CLI, which reports the provider response inline and can
            # press no keys.
            if not isinstance(detected, host.HerdrHost):
                outcome = _submission_evidence(detected, name, text, result)
            if outcome == "uncertain":
                state = "uncertain"
                reason = "sent; no submission evidence"
        # Keep the claim until the outcome is durable: releasing it first lets a
        # second sender claim the same still-pending record and send twice.
        try:
            _write_state(path, state, note=reason, receipt=outcome)
        except OSError:
            try:
                _write_state(path, "uncertain", note="outcome write failed")
            except OSError:
                # No durable state and no durable outcome: retain the claim so
                # the next retry treats it as in flight, then as uncertain.
                release = False
                return "uncertain", "outcome write failed; claim retained", (where, "uncertain")
            return "uncertain", "outcome write failed", (where, "uncertain")
        return state, reason, (where, outcome)
    finally:
        if release:
            _release_claim(path)


def retry_pending() -> int:
    """Retry the sender's pending records through the guarded path; never replay uncertain ones."""
    detected = host.detect()
    if detected is None:
        raise ValueError("hx say runs only in a team host (an agent named <team>-<role>)")
    sender_member = detected.own()
    if not sender_member:
        raise ValueError("hx say has no live sender identity; pending records were kept")
    sender = sender_member["name"]
    key = _sender_key(sender_member)
    records = records_for(key)
    if not records:
        print(f"No pending messages for {sender}.")
        return 0
    unresolved = 0
    for path, record, state in records:
        if record is None or state == "corrupt":
            print(f"Unresolved {path.name}: corrupt record, kept for reconciliation")
            unresolved += 1
            continue
        message_id = str(record.get("id") or path.stem)
        target = str(record.get("target") or "")
        body = record.get("body", record.get("text"))
        if record.get("sender") != sender:
            print(f"Unresolved {message_id}: record sender does not match {sender}")
            unresolved += 1
            continue
        record_key = record.get("sender_key")
        if ((record_key is not None and record_key != key)
                or (record_key is None and "-" in sender_member["role"])):
            print(f"Unresolved {message_id}: ambiguous legacy sender identity")
            unresolved += 1
            continue
        if not isinstance(body, str) or not body or not target:
            print(f"Unresolved {message_id}: record has no sender-validated target and body")
            unresolved += 1
            continue
        if state == "uncertain":
            print(f"Unresolved {message_id} -> {target}: uncertain delivery, reconcile before resend")
            unresolved += 1
            continue
        print(f"Pending {message_id} -> {target} ({record.get('created_at')})")
        outcome, reason, (where, word) = _deliver(detected, sender_member, target, body, path)
        suffix = f" ({reason})" if reason and outcome != "delivered" else ""
        print(f"{message_id} -> {where}: {word}{suffix}")
        if outcome != "delivered":
            unresolved += 1
    return 0 if unresolved == 0 else 1


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
    name, _destination = resolve(sender_member, target)
    text = with_header(sender, message)
    key = _sender_key(sender_member)
    path = _write_outbox(key, sender, name, message, text)
    outcome, reason, (where, word) = _deliver(
        detected, sender_member, target, message, path, force=force)
    suffix = f" ({reason})" if reason and outcome != "delivered" else ""
    print(f"{path.stem} -> {where}: {word}{suffix}")
    return 0 if outcome == "delivered" else 1
