"""hx say: message a teammate, never an agent of another team.

The sender's team is this pane's host identity: the prefix of its host name, or
the `team` label of its hc terminal. A dev-codex pane can reach dev-claude and
dev-opencode but not hx-claude. Agent permissions allow `hx say` and ask for a
raw host prompt or `hc send`, which makes this the only unattended send path.

Every message is written to a per-sender outbox before delivery. A send the
host reports as started is marked delivered; any other outcome (a blocked lead,
a finished terminal, an unknown response) leaves it pending, so a report is
never lost and `hx say --pending` can retry it.

A record may also carry the task id it belongs to, the destination session, an
expiry time and an optional `supersedes` id. Worker records without an explicit
expiry get the policy's default work TTL, measured from `created_at` for legacy
records. Inside the per-record delivery claim, the shared path marks a worker
record `cancelled` when it is expired, its task is closed, its destination
session changed, or a newer record to the same target supersedes it. Messages
resolved to the lead role are durable reports and are never cancelled by this
guard. `cancelled` is terminal. An `uncertain` record was already attempted:
it is never resent by --pending (a record with no valid age counts as uncertain); it waits for reconciliation.
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
# How long a second sender waits for the target's delivery reservation before
# leaving its record pending for an explicit --pending retry.
DELIVERY_WAIT_SECONDS = 2.0
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
    "cancelled": "cancelled",
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


def _write_outbox(sender_key: str, sender: str, target: str, body: str, text: str,
                  *, task_id: str | None = None, session_id: str | None = None,
                  expires_at: str | None = None, supersedes: str | None = None,
                  control: bool = False) -> Path:
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
        "task_id": task_id,
        "session_id": session_id,
        "expires_at": expires_at,
        "supersedes": supersedes,
        "control": bool(control),
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
    if state in ("pending", "delivered", "uncertain", "failed", "cancelled"):
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
        if record is not None and _record_state(record) in ("delivered", "failed", "cancelled"):
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


def _all_records(sender_key: str) -> list[dict]:
    """Every readable record in the sender's outboxes, terminal states included."""
    records = []
    directories = {_outbox_dir(sender_key), _outbox_dir(sender_key, legacy=True)}
    for directory in directories:
        for path in directory.glob("*.json"):
            record = _read_record(path)
            if record is not None:
                records.append(record)
    return records


def _task_closed(task_id: object) -> bool:
    """The record's task is closed; an unknown or unreadable task proves nothing."""
    if not isinstance(task_id, str) or not task_id:
        return False
    try:
        task = ledger.default_ledger().get_task(task_id)
    except Exception:
        return False
    return task.get("status") in ledger.Ledger.CLOSED_STATUSES


def _policy() -> dict:
    try:
        return usage.load_policy()
    except (OSError, ValueError, TypeError):
        return {}


def _is_lead(destination: host.Member, policy: dict) -> bool:
    return str(destination.get("role") or "") == str(policy.get("lead_role", "lead"))


# Used when policy.json is missing or unreadable, so worker briefs never become
# immortal by accident; policy say.work_ttl_seconds overrides it.
DEFAULT_WORK_TTL_SECONDS = 86400.0


def _work_ttl_seconds(policy: dict) -> float:
    say_policy = policy.get("say")
    raw = say_policy.get("work_ttl_seconds") if isinstance(say_policy, dict) else None
    if (isinstance(raw, (int, float)) and not isinstance(raw, bool)
            and math.isfinite(raw) and raw > 0):
        return float(raw)
    return DEFAULT_WORK_TTL_SECONDS


def _obsolete_state(
    path: Path,
    sender_key: str,
    record: dict,
    destination: host.Member,
    policy: dict,
) -> tuple[str, str] | None:
    """The worker-record transition this delivery must take, or None to send.

    Lead reports are classified by the resolved destination role before this
    runs. For worker records an explicit expiry wins; a missing expiry falls
    back to `created_at` plus the policy work TTL. An expiry that cannot be
    proven becomes `uncertain`, because reconciliation is safer than either
    terminal cancellation or a blind send.
    """
    expires_at = record.get("expires_at")
    expires = context._timestamp(expires_at) if expires_at is not None else None
    unknown = None
    if expires_at is not None and expires is None:
        unknown = "invalid expires_at; reconcile before resend"
    elif expires is None:
        created = context._timestamp(record.get("created_at"))
        ttl = _work_ttl_seconds(policy)
        if created is None:
            if ttl is not None:
                unknown = "missing or invalid created_at; reconcile before resend"
        elif ttl is not None:
            expires = created + datetime.timedelta(seconds=ttl)
    if expires is not None and expires <= datetime.datetime.now(datetime.timezone.utc):
        return "cancelled", f"expired {expires.isoformat()}"
    task_id = record.get("task_id")
    if _task_closed(task_id):
        return "cancelled", f"task {task_id} is closed"
    old_session = str(record.get("session_id") or "")
    new_session = str(destination.get("session_id") or "")
    if old_session and new_session and old_session != new_session:
        return "cancelled", f"destination session changed from {old_session} to {new_session}"
    record_id = str(record.get("id") or path.stem)
    target = str(record.get("target") or "")
    for sibling in _all_records(sender_key):
        if (str(sibling.get("id") or "") != record_id
                and str(sibling.get("supersedes") or "") == record_id
                and str(sibling.get("target") or "") == target
                and _record_state(sibling) not in ("cancelled", "failed")):
            return "cancelled", f"superseded by {sibling.get('id')}"
    if unknown is not None:
        return "uncertain", unknown
    return None


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


def _admit_delivery(
    name: str, holder: str, destination: host.Member, *, control: bool = False,
) -> tuple[bool, str]:
    """Reserve one send, allowing control to bypass session-operation holds."""
    try:
        led = ledger.default_ledger()
        return led.admit_delivery(
            name,
            holder,
            session_id=context._session(destination),
            wait_seconds=DELIVERY_WAIT_SECONDS,
            control=control,
        )
    except (OSError, ValueError, TypeError, KeyError, RuntimeError, ledger.LockTimeout) as exc:
        return False, f"admission unavailable: {exc}"


def _release_delivery(agent: str, holder: str) -> None:
    try:
        ledger.default_ledger().release_delivery(agent, holder)
    except Exception:
        pass


def _deliver(
    detected: host.Host,
    sender_member: host.Member,
    target: str,
    body: str,
    path: Path,
    *,
    force: bool = False,
    control: bool | None = None,
) -> tuple[str, str | None, tuple[str, str]]:
    """The one guarded delivery path, shared by a first send and a retry.

    Returns the durable record state, a reason, and a `(where, outcome)` receipt:
    the destination address plus `submitted`, `queued`, `pending`, `uncertain`
    or `failed` — the short line `hx say` prints.
    """
    sender = sender_member["name"]
    try:
        name, destination = resolve(sender_member, target)
        where = str(destination["address"]) or name
    except ValueError:
        where = target
    claimed = _claim(path)
    if claimed == "busy":
        return "pending", "delivery in flight", (where, "pending")
    if claimed == "stale":
        return "uncertain", "stale delivery claim", (where, "uncertain")
    release = True
    delivery_agent: str | None = None
    delivery_holder: str | None = None
    try:
        current = _read_record(path)
        if current is None:
            return "uncertain", "record unreadable under claim", (where, "uncertain")
        current_state = _record_state(current)
        if current_state != "pending":
            return current_state, current.get("note"), (where, _OUTCOME_WORDS.get(current_state, "uncertain"))
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
        policy = _policy()
        work = not _is_lead(destination, policy)
        transition = (
            _obsolete_state(path, _sender_key(sender_member), current, destination, policy)
            if work else None
        )
        if transition is not None:
            state, note = transition
            _write_state(path, state, note=note)
            return state, note, (where, _OUTCOME_WORDS.get(state, "uncertain"))
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
        _write_state(path, "pending", body=body, target=name, text=text,
                     pointer=pointer, session_id=destination.get("session_id"))
        if destination["status"] == "blocked":
            return "pending", "blocked", (where, "pending")
        if work:
            transition = _obsolete_state(
                path, _sender_key(sender_member), current, destination, policy)
            if transition is not None:
                state, note = transition
                _write_state(path, state, note=note)
                return state, note, (where, _OUTCOME_WORDS.get(state, "uncertain"))
        message_control = bool(
            force or (current.get("control") if control is None else control) or not work)
        holder = str(current.get("id") or path.stem)
        admitted, reason = _admit_delivery(name, holder, destination, control=message_control)
        # A control message must still arrive when the ledger itself is unreadable.
        unavailable = (reason or "").startswith("admission unavailable")
        if not admitted and not (message_control and unavailable):
            _write_state(path, "pending", note=reason or "compaction active")
            return "pending", reason or "compaction active", (where, "pending")
        if admitted:
            delivery_agent, delivery_holder = name, holder
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
        if delivery_agent is not None and delivery_holder is not None:
            _release_delivery(delivery_agent, delivery_holder)
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
        if outcome not in ("delivered", "cancelled"):
            unresolved += 1
    return 0 if unresolved == 0 else 1


def _worker_targets(sender_member: host.Member, record_target: str, worker: str) -> bool:
    try:
        resolved, _ = resolve(sender_member, record_target)
    except ValueError:
        resolved = record_target
    return resolved == worker or record_target == worker


def retry_pending_for(detected: host.Host, worker: str) -> dict[str, int]:
    """Deliver the sender's pending records addressed to one worker."""
    sender_member = detected.own()
    if not sender_member:
        raise ValueError("hx say has no live sender identity; pending records were kept")
    sender = sender_member["name"]
    key = _sender_key(sender_member)
    counts = {"considered": 0, "delivered": 0, "cancelled": 0,
              "pending": 0, "uncertain": 0, "failed": 0, "unresolved": 0}
    for path, record, state in records_for(key):
        if record is None or state == "corrupt":
            counts["unresolved"] += 1
            continue
        target = str(record.get("target") or "")
        if not _worker_targets(sender_member, target, worker):
            continue
        counts["considered"] += 1
        body = record.get("body", record.get("text"))
        if record.get("sender") != sender:
            counts["unresolved"] += 1
            continue
        record_key = record.get("sender_key")
        if ((record_key is not None and record_key != key)
                or (record_key is None and "-" in sender_member["role"])):
            counts["unresolved"] += 1
            continue
        if not isinstance(body, str) or not body or not target:
            counts["unresolved"] += 1
            continue
        if state == "uncertain":
            counts["unresolved"] += 1
            continue
        outcome, _, _ = _deliver(detected, sender_member, target, body, path)
        if outcome in counts:
            counts[outcome] += 1
        else:
            counts["unresolved"] += 1
    return counts


def say(target: str | None, message: str, *, force: bool = False,
        control: bool = False, task: str | None = None, ttl: float | None = None,
        supersedes: str | None = None) -> int:
    if not target:
        raise ValueError("hx say needs a teammate role or name, or --pending")
    if ttl is not None and not (math.isfinite(ttl) and ttl > 0):
        raise ValueError("--ttl must be a positive, finite number of seconds")
    detected = host.detect()
    if detected is None:
        raise ValueError("hx say runs only in a team host (an agent named <team>-<role>)")
    sender_member = detected.own()
    if not sender_member:
        raise ValueError("hx say runs only in a team host (an agent named <team>-<role>)")
    sender = sender_member["name"]
    name, destination = resolve(sender_member, target)
    text = with_header(sender, message)
    key = _sender_key(sender_member)
    policy = _policy()
    lifetime = ttl
    if lifetime is None and not _is_lead(destination, policy):
        lifetime = _work_ttl_seconds(policy)
    expires_at = (
        (datetime.datetime.now(datetime.timezone.utc)
         + datetime.timedelta(seconds=lifetime)).isoformat()
        if lifetime is not None else None)
    path = _write_outbox(key, sender, name, message, text,
                         task_id=task, session_id=destination.get("session_id"),
                         expires_at=expires_at, supersedes=supersedes,
                         control=bool(control or force))
    outcome, reason, (where, word) = _deliver(
        detected, sender_member, target, message, path, force=force, control=control)
    suffix = f" ({reason})" if reason and outcome != "delivered" else ""
    print(f"{path.stem} -> {where}: {word}{suffix}")
    return 0 if outcome == "delivered" else 1
