"""hx.muse: the Muse Mail bridge (Q-1154).

The team mailbox is the transport and the lead is the router. ``hx muse poll``
reads new mail over IMAP, keeps only mail from the configured Muse address whose
provider reports DMARC or aligned DKIM pass, caps the size, strips quoted
history, redacts secrets, journals every message outside any repo, and hands the
text to the lead as data. ``hx muse send`` sends one plain-text message on one
thread per Jotarium ref. Credentials live only in the Windows user environment
and never reach the journal, a log or a printed line.

Data shape:

  config = {
    "address": str,             # From address of the team mailbox
    "muse_address": str,        # the only allowed sender
    "max_sends_per_hour": int,  # default 6
    "max_message_bytes": int,   # default 131072
    "authserv_id": str,         # default mx.google.com
    "imap_host": str, "imap_port": int,
    "smtp_host": str, "smtp_port": int,
  }

  journal record (JSON per line, append-only) = {
    "at": iso8601, "entry": hex, "kind": "inbound" | "outbound",
    "message_id": str | None, "status": str, "sender": str, "recipient": str,
    "subject": str, "thread": "Q-1154" | None, "body": str,
    "bytes": int, "reason": str | None, "mailbox_key": str | None,
  }

Inbound status is "pending", "delivered", "quarantined" or "delivery-failed"; outbound is
"sending" reserves a quota slot before SMTP and "sent" confirms its completion.
Polling tracks accepted Message-IDs and the IMAP identity of each handled item.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import email
import email.utils
import hashlib
import imaplib
import json
import os
import re
import smtplib
import ssl
import uuid
from email import policy
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Callable

from hx import handoff, ledger

JOURNAL_NAME = "journal.jsonl"
CONFIG_NAME = "config.json"
ENV_CONFIG = "HX_MUSE_CONFIG"
ENV_USER = "HX_MUSE_MAIL_USER"
ENV_PASSWORD = "HX_MUSE_MAIL_APP_PASSWORD"

REF_RE = re.compile(r"\b([PFQ]-\d+)\b")
AUTH_TOKEN_RE = re.compile(r'\s+|\\.|[();=]|"(?:\\.|[^"\\])*"|[^\s();="\\]+')
IMAP_SIZE_RE = re.compile(rb"\bRFC822\.SIZE (\d+)\b")
MESSAGE_ID_RE = re.compile(r"<[^<>\s@]+@[^<>\s@]+>")
QUOTE_END_RE = re.compile(r"(?i)^on\b.*\bwrote:$|^-{2,}\s*original message\s*-{2,}$")
JOURNAL_BODY_LIMIT = 8000
SUMMARY_CHARS = 280
RATE_WINDOW_SECONDS = 3600.0


@dataclasses.dataclass(frozen=True)
class Config:
    address: str
    muse_address: str
    max_sends_per_hour: int = 6
    max_message_bytes: int = 131_072
    imap_host: str = "imap.gmail.com"
    imap_port: int = 993
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 465
    authserv_id: str = "mx.google.com"


@dataclasses.dataclass(frozen=True)
class Inbound:
    message_id: str
    sender: str
    subject: str
    body: str
    auth: tuple[str, ...] | str = ()
    size: int = 0
    uid: bytes = b""
    mailbox_key: str = ""
    content_error: str = ""


def state_dir() -> Path:
    base = Path(os.environ.get("HX_STATE_DIR", Path.home() / ".herdr-team" / "state"))
    return base / "muse"


def config_path() -> Path:
    override = os.environ.get(ENV_CONFIG)
    return Path(override) if override else state_dir() / CONFIG_NAME


def journal_path(state: Path | None = None) -> Path:
    return (state or state_dir()) / JOURNAL_NAME


def load_config(path: Path | None = None) -> Config:
    target = path or config_path()
    if not target.exists():
        raise ValueError(
            f"hx muse config missing at {target}; write {{address, muse_address}} there "
            f"or point {ENV_CONFIG} at it (see docs/MUSE_MAIL.md)"
        )
    try:
        raw = json.loads(target.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"hx muse config {target} is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"hx muse config {target} must be a JSON object")
    address = str(raw.get("address") or "").strip()
    muse_address = str(raw.get("muse_address") or "").strip()
    if not address or not muse_address:
        raise ValueError(f"hx muse config {target} needs address and muse_address")
    try:
        limits = {name: int(raw.get(name, default)) for name, default in (
            ("max_sends_per_hour", 6), ("max_message_bytes", 131_072),
            ("imap_port", 993), ("smtp_port", 465),
        )}
    except (TypeError, ValueError, OverflowError):
        raise ValueError("hx muse limits and ports must be integers") from None
    if limits["max_sends_per_hour"] < 0 or limits["max_message_bytes"] <= 0:
        raise ValueError("hx muse send limit must be nonnegative and message size must be positive")
    return Config(
        address=address,
        muse_address=muse_address,
        imap_host=str(raw.get("imap_host") or "imap.gmail.com"),
        smtp_host=str(raw.get("smtp_host") or "smtp.gmail.com"),
        authserv_id=str(raw.get("authserv_id") or "mx.google.com").strip().lower(),
        **limits,
    )


def credentials() -> tuple[str, str]:
    user = (os.environ.get(ENV_USER) or "").strip()
    password = os.environ.get(ENV_PASSWORD) or ""
    if not user or not password:
        raise ValueError(
            f"hx muse needs {ENV_USER} and {ENV_PASSWORD} in your user environment; "
            f"see docs/MUSE_MAIL.md for the app password steps"
        )
    return user, password


def read_journal(path: Path | None = None) -> list[dict[str, Any]]:
    target = path or journal_path()
    if not target.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in target.read_text(encoding="utf-8", errors="replace").split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            loaded = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(loaded, dict):
            records.append(loaded)
    return records


def append_journal(record: dict[str, Any], path: Path | None = None) -> Path:
    target = path or journal_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    line = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
    with ledger.LeaseLock(target.with_name(target.name + ".append.lock")):
        with target.open("a+b") as handle:
            if handle.tell():
                handle.seek(-1, os.SEEK_END)
                if handle.read(1) != b"\n":
                    line = b"\n" + line
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
    return target


def redact(text: str, *secrets: str) -> str:
    out = text or ""
    for secret in secrets:
        if secret:
            out = out.replace(secret, "[redacted]")
    return handoff.redact(out)


def strip_quoted(body: str) -> str:
    lines = (body or "").splitlines()
    kept: list[str] = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(">"):
            continue
        if QUOTE_END_RE.match(stripped):
            break
        if stripped.lower().startswith("on "):
            for following in lines[index + 1:index + 4]:
                if not following.strip():
                    break
                stripped += " " + following.strip()
                if QUOTE_END_RE.match(stripped):
                    return "\n".join(kept).strip()
        kept.append(line)
    return "\n".join(kept).strip()


def thread_ref(subject: str) -> str | None:
    match = REF_RE.search(subject or "")
    return match.group(1) if match else None


def _auth_clauses(value: str) -> list[list[str]]:
    value = value.lower()
    tokens = AUTH_TOKEN_RE.findall(value)
    if "".join(tokens) != value:
        return []
    clauses: list[list[str]] = [[]]
    depth = 0
    for token in tokens:
        if token == "(":
            depth += 1
        elif token == ")":
            depth -= 1
            if depth < 0:
                return []
        elif depth == 0 and not token.isspace():
            if token == ";":
                clauses.append([])
            else:
                clauses[-1].append(token)
    return clauses if depth == 0 else []


def auth_passes(config: Config, headers: tuple[str, ...] | str) -> bool:
    trusted = []
    for value in (headers,) if isinstance(headers, str) else headers:
        # Count even a malformed result that claims the provider ID as ambiguous.
        clauses = _auth_clauses(value)
        prefix = clauses or _auth_clauses(value.partition(";")[0])
        service = prefix[0][0].strip('"') if prefix and prefix[0] else ""
        if service == config.authserv_id.lower():
            trusted.append(clauses)
    if len(trusted) != 1 or not trusted[0]:
        return False
    service, *results = trusted[0]
    if len(service) > 2 or (len(service) == 2 and not re.fullmatch(r"[0-9]+", service[1])):
        return False
    domain = config.muse_address.lower().rpartition("@")[2]
    for result in results:
        method = result[:3]
        if method == ["dmarc", "=", "pass"]:
            return True
        if method != ["dkim", "=", "pass"]:
            continue
        properties = result[3:]
        if len(properties) % 3 or any(token != "=" for token in properties[1::3]):
            continue
        domains = [value.strip('"') for name, value in zip(properties[::3], properties[2::3])
                   if name == "header.d"]
        if len(domains) == 1 and re.fullmatch(r"(?:[a-z0-9-]+\.)*" + re.escape(domain), domains[0]):
            return True
    return False


def sender_allowed(config: Config, sender: str) -> bool:
    _, address = email.utils.parseaddr(sender or "")
    return bool(address) and address.strip().lower() == config.muse_address.lower()


def verdict(config: Config, mail: Inbound) -> tuple[str, str | None]:
    if mail.size > config.max_message_bytes:
        return "quarantined", f"too large ({mail.size} bytes over {config.max_message_bytes})"
    if mail.content_error:
        return "quarantined", mail.content_error
    if not sender_allowed(config, mail.sender):
        return "quarantined", "sender not allowed"
    if not auth_passes(config, mail.auth):
        return "quarantined", "no trusted DMARC or aligned DKIM pass"
    if not MESSAGE_ID_RE.fullmatch(mail.message_id.strip()):
        return "quarantined", "missing, invalid or repeated Message-ID"
    return "ok", None


def _at(record: dict[str, Any]) -> dt.datetime | None:
    try:
        parsed = dt.datetime.fromisoformat(str(record.get("at") or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)


def _sends_in_window(records: list[dict[str, Any]], moment: dt.datetime) -> int:
    return len({
        record.get("message_id") or record["entry"]
        for record in records
        if record.get("kind") == "outbound" and record.get("status") in {"sending", "sent"}
        and (stamp := _at(record)) is not None
        and 0 <= (moment - stamp).total_seconds() < RATE_WINDOW_SECONDS
    })


def _record(at: dt.datetime | None = None, **fields: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "at": (at or dt.datetime.now(dt.timezone.utc)).isoformat(timespec="seconds"),
        "entry": uuid.uuid4().hex,
        "kind": None,
        "message_id": None,
        "status": None,
        "sender": "",
        "recipient": "",
        "subject": "",
        "thread": None,
        "body": "",
        "bytes": 0,
        "reason": None,
    }
    record.update(fields)
    return record


def _lead_text(record: dict[str, Any], journal: Path) -> str:
    summary = " ".join(str(record.get("body") or "").split())
    if len(summary) > SUMMARY_CHARS:
        summary = summary[: SUMMARY_CHARS - 3] + "..."
    ref = record.get("thread") or "no ref"
    return (
        f"[muse] {ref} {record.get('subject') or '(no subject)'}: {summary} "
        f"| journal {journal} entry {record.get('entry')}"
    )


def _say_lead(text: str) -> Any:
    from hx import say as say_mod
    return say_mod.say("lead", text)


def poll(
    *,
    config: Config | None = None,
    credentials_: tuple[str, str] | None = None,
    mailbox: Any | None = None,
    deliver: Callable[[str], Any] | None = None,
    journal: Path | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    cfg = config or load_config()
    user, password = credentials_ or credentials()
    box = mailbox or ImapMailbox(cfg, user, password)
    send_to_lead = deliver or _say_lead
    target = journal or journal_path()
    with ledger.LeaseLock(target.with_name(target.name + ".poll.lock")), box:
        messages, total = box.fetch(max(0, limit))
        return _process_mail(cfg, box, messages, total, send_to_lead, target, password)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()


def _process_mail(
    cfg: Config, box: ImapMailbox, messages: list[Inbound], total: int,
    send_to_lead: Callable[[str], Any], target: Path, password: str,
) -> dict[str, Any]:
    records = [record for record in read_journal(target) if record.get("kind") == "inbound"]
    seen = {record.get("message_key") or _digest(record["message_id"])
            for record in records if record.get("status") in {"delivered", "delivery-failed"}
            and record.get("message_id")}
    handled = {record["mailbox_key"] for record in records
               if record.get("mailbox_key") and record.get("status") != "pending"}
    pending = {record["mailbox_key"]: record for record in records
               if record.get("mailbox_key") and record.get("status") == "pending"}
    result: dict[str, Any] = {
        "fetched": total,
        "delivered": 0,
        "quarantined": 0,
        "skipped": 0,
        "failed": 0,
        "dropped": total - len(messages),
        "journal": str(target),
        "records": [],
    }
    for mail in messages:
        message_id = (mail.message_id or "").strip()
        message_key = _digest(message_id) if message_id else None
        status, reason = verdict(cfg, mail)
        if mail.mailbox_key in handled or (status == "ok" and message_key in seen):
            box.acknowledge(mail)
            result["skipped"] += 1
            continue
        body = redact(strip_quoted(mail.body), password)[:JOURNAL_BODY_LIMIT] if status == "ok" else ""
        subject = " ".join(redact(mail.subject, password).split())[:SUMMARY_CHARS]
        previous = pending.get(mail.mailbox_key) if status == "ok" else None
        record = _record(
            kind="inbound",
            message_id=redact(message_id, password) or None,
            message_key=message_key,
            mailbox_key=mail.mailbox_key or None,
            status="quarantined" if status != "ok" else "pending",
            sender=redact(mail.sender, password)[:SUMMARY_CHARS],
            recipient=redact(cfg.address, password),
            subject=subject,
            thread=thread_ref(subject),
            body=body,
            bytes=mail.size or len(body.encode("utf-8", "replace")),
            reason=reason,
        )
        if previous is None:
            append_journal(record, target)
        else:
            record["entry"] = previous["entry"]
        result["records"].append({"entry": record["entry"], "status": record["status"],
                                  "message_id": record["message_id"], "reason": reason})
        if record["status"] == "quarantined":
            result["quarantined"] += 1
            box.acknowledge(mail)
            continue
        try:
            if send_to_lead(_lead_text(record, target)) not in (0, None):
                raise RuntimeError("lead delivery did not confirm")
        except Exception as exc:
            final = dict(
                record, status="delivery-failed",
                reason=redact(str(exc), password)[:200],
            )
            result["failed"] += 1
        else:
            final = dict(record, status="delivered")
            result["delivered"] += 1
        append_journal(final, target)
        result["records"][-1].update(status=final["status"], reason=final["reason"])
        seen.add(message_key)
        box.acknowledge(mail)
    return result


def send(
    subject: str,
    body: str,
    *,
    to: str = "muse",
    config: Config | None = None,
    credentials_: tuple[str, str] | None = None,
    sender: Any | None = None,
    journal: Path | None = None,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    cfg = config or load_config()
    if to != "muse":
        raise ValueError("hx muse send --to muse is the only target; the other side is the Muse address")
    if "\n" in subject or "\r" in subject:
        raise ValueError("hx muse send subject must be one line")
    ref = thread_ref(subject)
    if not ref:
        raise ValueError("hx muse send needs a Jotarium ref in the subject, such as [Q-1154]")
    user, password = credentials_ or credentials()
    target = journal or journal_path()
    moment = now or dt.datetime.now(dt.timezone.utc)
    clean = redact(body, password).strip()
    subject = redact(subject, password)
    if password and (password in cfg.address or password in cfg.muse_address):
        raise ValueError("hx muse mailbox addresses contain the app password")
    if not clean:
        raise ValueError("hx muse send body is empty after redaction")
    size = len(clean.encode("utf-8"))
    if size > cfg.max_message_bytes:
        raise ValueError(f"hx muse send body is {size} bytes over {cfg.max_message_bytes}")
    domain = (cfg.address.rsplit("@", 1)[-1] or "hx-muse.local").strip("> ")
    message = EmailMessage()
    message["From"] = cfg.address
    message["To"] = cfg.muse_address
    message["Subject"] = subject
    message["Date"] = email.utils.format_datetime(moment)
    message["Message-ID"] = f"<hx-{uuid.uuid4().hex}@{domain}>"
    message.set_content(clean)
    with ledger.LeaseLock(target.with_name(target.name + ".send.lock")):
        records = read_journal(target)
        recent = _sends_in_window(records, moment)
        if recent >= cfg.max_sends_per_hour:
            raise ValueError(
                f"hx muse rate limit: {cfg.max_sends_per_hour} sends per hour; "
                f"{recent} slots used in the last hour"
            )
        previous_id = next(
            (record["message_id"] for record in reversed(records)
             if record.get("thread") == ref and record.get("status") in {"delivered", "sent"}
             and MESSAGE_ID_RE.fullmatch(record.get("message_id") or "")
             and redact(record["message_id"], password) == record["message_id"]
             and "[redacted]" not in record["message_id"]),
            None,
        )
        if previous_id is not None:
            message["In-Reply-To"] = previous_id
            message["References"] = previous_id
        append_journal(_record(at=moment, kind="outbound", status="sending",
                               message_id=message["Message-ID"]), target)
    try:
        (sender or SmtpSender(cfg, user, password)).send(message)
    except Exception:
        raise RuntimeError("SMTP send failed") from None
    record = _record(
        at=moment,
        kind="outbound",
        message_id=message["Message-ID"],
        status="sent",
        sender=cfg.address,
        recipient=cfg.muse_address,
        subject=subject,
        thread=ref,
        body=clean[:JOURNAL_BODY_LIMIT],
        bytes=size,
        reason=None,
    )
    append_journal(record, target)
    return {
        "message_id": message["Message-ID"],
        "thread": ref,
        "to": cfg.muse_address,
        "bytes": size,
        "reply_to": message["In-Reply-To"],
        "journal": str(target),
        "entry": record["entry"],
    }


def status(*, config: Config | None = None, journal: Path | None = None,
           now: dt.datetime | None = None) -> dict[str, Any]:
    target = journal or journal_path()
    cfg: Config | None
    error: str | None = None
    try:
        cfg = config or load_config()
    except ValueError as exc:
        cfg = None
        error = str(exc)
    user = (os.environ.get(ENV_USER) or "").strip()
    password = os.environ.get(ENV_PASSWORD) or ""
    records = read_journal(target)
    moment = now or dt.datetime.now(dt.timezone.utc)
    counts: dict[str, int] = {}
    for record in records:
        key = f"{record.get('kind')}:{record.get('status')}"
        counts[key] = counts.get(key, 0) + 1
    return {
        "config": str(config_path()),
        "config_ok": cfg is not None,
        "config_error": error,
        "address": cfg.address if cfg else None,
        "muse_address": cfg.muse_address if cfg else None,
        "credentials": "set" if user and password else "missing",
        "journal": str(target),
        "journal_exists": target.exists(),
        "entries": len(records),
        "counts": counts,
        "sends_last_hour": _sends_in_window(records, moment),
        "max_sends_per_hour": cfg.max_sends_per_hour if cfg else None,
    }


def render_poll(result: dict[str, Any]) -> str:
    lines = [
        f"hx muse poll: fetched {result['fetched']}, delivered {result['delivered']}, "
        f"quarantined {result['quarantined']}, skipped {result['skipped']}, "
        f"failed {result['failed']}, dropped {result['dropped']}",
    ]
    for record in result["records"]:
        suffix = f" ({record['reason']})" if record.get("reason") else ""
        lines.append(f"  {record['status']} {record['entry']}{suffix}")
    lines.append(f"journal {result['journal']}")
    return "\n".join(lines)


def render_send(result: dict[str, Any]) -> str:
    return (
        f"hx muse send: sent {result['bytes']} bytes to {result['to']} "
        f"on thread {result['thread']} ({result['message_id']})"
    )


def render_status(result: dict[str, Any]) -> str:
    lines = ["hx muse status"]
    if result["config_ok"]:
        lines.append(f"config {result['config']}")
        lines.append(f"address {result['address']}  muse {result['muse_address']}")
    else:
        lines.append(f"config missing or invalid at {result['config']}")
        if result["config_error"]:
            lines.append(f"  {result['config_error']}")
    lines.append(f"credentials {result['credentials']}")
    lines.append(
        f"journal {result['journal']} ({result['entries']} entries, exists {result['journal_exists']})"
    )
    for key in sorted(result["counts"]):
        lines.append(f"  {key} {result['counts'][key]}")
    limit = result["max_sends_per_hour"]
    lines.append(
        f"sends last hour {result['sends_last_hour']}"
        + (f" of {limit}" if limit is not None else "")
    )
    return "\n".join(lines)


class ImapMailbox:
    """The only place that logs in to the mailbox; credentials never leave it."""

    def __init__(self, config: Config, user: str, password: str) -> None:
        self._config = config
        self._user = user
        self._password = password

    def __enter__(self) -> ImapMailbox:
        self._connection = None
        try:
            self._connection = imaplib.IMAP4_SSL(
                self._config.imap_host, self._config.imap_port, ssl_context=ssl.create_default_context(),
                timeout=30,
            )
            self._connection.login(self._user, self._password)
            self._require_ok(self._connection.select("INBOX"))
            _, validity = self._connection.response("UIDVALIDITY")
            if not validity or not validity[0] or not validity[0].isdigit():
                raise RuntimeError("IMAP did not report UIDVALIDITY")
            self._namespace = f"{self._config.imap_host}/{self._user}/{validity[0].decode('ascii')}"
        except Exception:
            self.__exit__(None, None, None)
            raise RuntimeError("IMAP login or mailbox selection failed") from None
        return self

    def __exit__(self, *_exc: Any) -> None:
        if self._connection is not None:
            try:
                self._connection.logout()
            except Exception:
                pass

    @staticmethod
    def _require_ok(result):
        status, data = result
        if status != "OK":
            raise RuntimeError("IMAP command failed")
        return data

    def _uid(self, command: str, *args):
        try:
            return self._require_ok(self._connection.uid(command, *args))
        except Exception:
            raise RuntimeError(f"IMAP {command} failed") from None

    def fetch(self, limit: int) -> tuple[list[Inbound], int]:
        data = self._uid("SEARCH", None, "UNSEEN")
        numbers = (data[0] or b"").split()
        found = []
        max_bytes = self._config.max_message_bytes
        for uid in numbers[:limit]:
            if not uid.isdigit():
                raise RuntimeError("IMAP returned an invalid UID")
            mailbox_key = _digest(f"{self._namespace}/{uid.decode('ascii')}")
            raw = self._uid("FETCH", uid, f"(RFC822.SIZE BODY.PEEK[]<0.{max_bytes + 1}>)")
            literal = next((part for part in raw if isinstance(part, tuple)), None)
            if literal is None:
                continue
            metadata = b" ".join(part[0] if isinstance(part, tuple) else part
                                 for part in raw if isinstance(part, (tuple, bytes)))
            size_match = IMAP_SIZE_RE.search(metadata)
            if not size_match:
                raise RuntimeError("IMAP did not report message size")
            size = int(size_match[1])
            if size > max_bytes:
                found.append(Inbound("", "", "", "", size=size, uid=uid, mailbox_key=mailbox_key))
                continue
            if len(literal[1]) != size:
                raise RuntimeError("IMAP returned an incomplete message")
            found.append(_parse_mail(literal[1], uid, mailbox_key))
        return found, len(numbers)

    def acknowledge(self, mail: Inbound) -> None:
        self._uid("STORE", mail.uid, "+FLAGS.SILENT", "\\Seen")


class SmtpSender:
    def __init__(self, config: Config, user: str, password: str) -> None:
        self._config = config
        self._user = user
        self._password = password

    def send(self, message: EmailMessage) -> None:
        with smtplib.SMTP_SSL(self._config.smtp_host, self._config.smtp_port, timeout=30,
                              context=ssl.create_default_context()) as client:
            client.login(self._user, self._password)
            client.send_message(message)


def _parse_mail(raw: bytes, uid: bytes, mailbox_key: str) -> Inbound:
    try:
        message = email.message_from_bytes(raw, policy=policy.default)
        from_headers = message.get_all("From", [])
        sender = ""
        if len(from_headers) == 1 and not from_headers[0].defects and len(from_headers[0].addresses) == 1:
            sender = str(from_headers[0].addresses[0])
        ids = message.get_all("Message-ID", [])
        body = _plain_body(message)
        return Inbound(
            message_id=str(ids[0]).strip() if len(ids) == 1 and not ids[0].defects else "",
            sender=sender,
            subject=str(message.get("Subject", "")),
            body=body,
            auth=tuple(str(value) for value in message.get_all("Authentication-Results", [])),
            size=len(raw),
            uid=uid,
            mailbox_key=mailbox_key,
            content_error="" if body.strip() else "no plain-text body",
        )
    except (RecursionError, ValueError, TypeError, IndexError, email.errors.MessageError):
        return Inbound("", "", "", "", size=len(raw), uid=uid, mailbox_key=mailbox_key,
                       content_error="malformed MIME message")


def _plain_body(message: EmailMessage) -> str:
    part = message.get_body(preferencelist=("plain",))
    if part is None or part.get_filename():
        return ""
    try:
        return part.get_content()
    except (LookupError, UnicodeDecodeError):
        payload = part.get_payload(decode=True) or b""
        return payload.decode("utf-8", "replace")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="hx muse", description="Muse Mail bridge: poll Muse mail and send as the lead."
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    poll_p = sub.add_parser("poll", help="fetch new Muse mail and hand it to the lead")
    poll_p.add_argument("--limit", type=int, default=20)
    poll_p.add_argument("--json", action="store_true", dest="as_json")
    send_p = sub.add_parser("send", help="send one plain-text message to Muse")
    send_p.add_argument("--to", default="muse")
    send_p.add_argument("--subject", required=True)
    send_p.add_argument("--body-file", required=True)
    send_p.add_argument("--json", action="store_true", dest="as_json")
    status_p = sub.add_parser("status", help="show config, journal and rate-limit state")
    status_p.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)
    if args.cmd == "poll":
        result = poll(limit=args.limit)
        print(json.dumps(result, indent=2) if args.as_json else render_poll(result))
        return 0
    if args.cmd == "send":
        body = Path(args.body_file).read_text(encoding="utf-8-sig")
        result = send(args.subject, body, to=args.to)
        print(json.dumps(result, indent=2) if args.as_json else render_send(result))
        return 0
    result = status()
    print(json.dumps(result, indent=2) if args.as_json else render_status(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
