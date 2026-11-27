"""Tests for hx.muse: the Muse Mail bridge (Q-1154).

Fakes stand in for IMAP and SMTP, so no test touches the network. The journal
lives in the test temp dir; no real mailbox or credential is read.
"""

from __future__ import annotations

import datetime as dt
import json
from email.message import EmailMessage
from pathlib import Path

import pytest

from hx import __main__ as cli
from hx import muse

CREDS = ("agents@example.com", "app-pass-secret")
MUSE_ADDR = "muse@muse.ai"


def _cfg(**overrides) -> muse.Config:
    values = dict(address="agents@example.com", muse_address=MUSE_ADDR)
    values.update(overrides)
    return muse.Config(**values)


def _mail(**overrides) -> muse.Inbound:
    values = dict(
        message_id="<m1@muse.ai>",
        sender=f"Muse <{MUSE_ADDR}>",
        subject="[Q-1154] check the plan",
        body="team, please check the plan.",
        auth="mx.google.com; dkim=pass header.d=muse.ai",
        size=28,
    )
    values.update(overrides)
    return muse.Inbound(**values)


class FakeMailbox:
    def __init__(self, messages: list[muse.Inbound]) -> None:
        self.messages = list(messages)
        self.fetches = 0

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        pass

    def fetch(self, limit: int) -> tuple[list[muse.Inbound], int]:
        self.fetches += 1
        return self.messages[:limit], len(self.messages)

    def acknowledge(self, _mail):
        pass


class FakeSender:
    def __init__(self) -> None:
        self.messages: list = []

    def send(self, message) -> None:
        self.messages.append(message)


@pytest.fixture(autouse=True)
def _muse_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HX_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("HX_MUSE_CONFIG", str(tmp_path / "state" / "muse" / "config.json"))
    monkeypatch.delenv("HX_MUSE_MAIL_USER", raising=False)
    monkeypatch.delenv("HX_MUSE_MAIL_APP_PASSWORD", raising=False)


@pytest.fixture
def journal(tmp_path: Path) -> Path:
    return tmp_path / "journal.jsonl"


def _poll(journal: Path, messages: list[muse.Inbound], *, cfg=None, sent=None, limit=20):
    box = FakeMailbox(messages)
    delivered: list[str] = sent if sent is not None else []
    result = muse.poll(
        config=cfg or _cfg(),
        credentials_=CREDS,
        mailbox=box,
        deliver=delivered.append,
        journal=journal,
        limit=limit,
    )
    return result, delivered, box


def test_poll_delivers_allowed_authenticated_mail(journal: Path) -> None:
    result, sent, _ = _poll(journal, [_mail()])
    assert result["delivered"] == 1
    assert result["quarantined"] == 0
    assert sent and sent[0].startswith("[muse] Q-1154 [Q-1154] check the plan:")
    assert str(journal) in sent[0] and "entry" in sent[0]
    records = muse.read_journal(journal)
    assert [r["status"] for r in records] == ["pending", "delivered"]
    assert records[0]["thread"] == "Q-1154"
    assert records[0]["recipient"] == "agents@example.com"
    assert records[0]["sender"] == "Muse <muse@muse.ai>"


def test_poll_quarantines_unknown_sender(journal: Path) -> None:
    result, sent, _ = _poll(journal, [_mail(sender="Mallory <mallory@example.com>")])
    assert result["delivered"] == 0
    assert result["quarantined"] == 1
    assert sent == []
    records = muse.read_journal(journal)
    assert records[0]["status"] == "quarantined"
    assert records[0]["reason"] == "sender not allowed"


def test_poll_quarantines_missing_authentication(journal: Path) -> None:
    result, sent, _ = _poll(journal, [_mail(auth="mx.google.com; spf=fail")])
    assert result["quarantined"] == 1
    assert sent == []
    assert muse.read_journal(journal)[0]["reason"] == "no trusted DMARC or aligned DKIM pass"


@pytest.mark.parametrize(
    ("result", "accepted"),
    [
        ("dkim=pass header.d=other.example", False),
        ("spf=pass smtp.mailfrom=muse.ai", False),
        ("dmarc=pass header.from=muse.ai", True),
        ("dkim=pass header.d=muse.ai", True),
        ('dkim=pass (verified) header.d="relay.muse.ai"', True),
        ("dkim=pass header.d=notmuse.ai", False),
        ("dkim=fail header.d=muse.ai (internal dmarc=pass)", False),
    ],
)
def test_provider_authentication_policy(journal: Path, result: str, accepted: bool) -> None:
    outcome, delivered, _ = _poll(journal, [_mail(auth=f"mx.google.com; {result}")])
    assert outcome["delivered"] == int(accepted)
    assert bool(delivered) is accepted


def test_poll_quarantines_mail_without_message_id(journal: Path) -> None:
    result, sent, _ = _poll(journal, [_mail(message_id="")])
    assert result["quarantined"] == 1
    assert sent == []
    assert "Message-ID" in (muse.read_journal(journal)[0]["reason"] or "")


def test_poll_is_idempotent_by_message_id(journal: Path) -> None:
    first, sent, _ = _poll(journal, [_mail()])
    second, sent2, _ = _poll(journal, [_mail()], sent=sent)
    assert first["delivered"] == 1
    assert second["fetched"] == 1
    assert second["delivered"] == 0
    assert second["skipped"] == 1
    assert len(sent2) == 1
    assert len(muse.read_journal(journal)) == 2


def test_poll_strips_quoted_history(journal: Path) -> None:
    body = "Please run the suite.\nOn Fri, Nov 27, 2026 at 10:00 AM Muse <muse@muse.ai> wrote:\n> old line\n> old line two"
    _poll(journal, [_mail(body=body)])
    record = muse.read_journal(journal)[0]
    assert record["body"] == "Please run the suite."


def test_poll_caps_message_size(journal: Path) -> None:
    result, sent, _ = _poll(journal, [_mail(body="x" * 200, size=200)], cfg=_cfg(max_message_bytes=64))
    assert result["quarantined"] == 1
    assert sent == []
    assert "too large" in (muse.read_journal(journal)[0]["reason"] or "")


def test_poll_limits_messages_and_reports_dropped(journal: Path) -> None:
    messages = [_mail(message_id=f"<m{i}@muse.ai>") for i in range(3)]
    result, sent, _ = _poll(journal, messages, limit=2)
    assert result["fetched"] == 3
    assert result["delivered"] == 2
    assert result["dropped"] == 1
    assert len(sent) == 2


class FakeImap:
    def __init__(self, *messages: bytes, trailing_size=False) -> None:
        self.messages = {str(i).encode(): raw for i, raw in enumerate(messages, 1)}
        self.seen = set()
        self.fetches = []
        self.trailing_size = trailing_size

    def login(self, *_args):
        pass

    def select(self, _mailbox):
        return "OK", [str(len(self.messages)).encode()]

    def response(self, name):
        assert name == "UIDVALIDITY"
        return name, [b"42"]

    def search(self, *_args):
        return "OK", [b" ".join(number for number in self.messages if number not in self.seen)]

    def fetch(self, number, query):
        self.fetches.append(number)
        raw = self.messages[number]
        if "BODY.PEEK" in query:
            limit = int(query.split("<0.")[1].split(">")[0])
            data = raw[:limit]
        else:
            self.seen.add(number)
            data = raw
        literal = f"BODY[]<0> {{{len(data)}}}"
        size = f"RFC822.SIZE {len(raw)}"
        if self.trailing_size:
            return "OK", [(f"1 ({literal}".encode(), data), f" {size} UID 1)".encode()]
        return "OK", [(f"1 ({size} {literal}".encode(), data), b")"]

    def store(self, numbers, _mode, _flag):
        self.seen.update(numbers.split(b","))
        return "OK", []

    def uid(self, command, *args):
        return getattr(self, command.lower())(*args)

    def logout(self):
        pass


def _raw_mail(*auth: str, ids=("<m1@muse.ai>",), body="hello\r\n", extra="") -> bytes:
    headers = "".join(f"Authentication-Results: {value}\r\n" for value in auth)
    headers += "".join(f"Message-ID: {value}\r\n" for value in ids)
    return (f"{headers}From: {MUSE_ADDR}\r\nSubject: [Q-1154] fixture\r\n{extra}\r\n{body}").encode()


def _imap_poll(journal, monkeypatch, server, *, cfg=None, limit=20):
    monkeypatch.setattr(muse.imaplib, "IMAP4_SSL", lambda *_args, **_kwargs: server)
    delivered = []
    outcome = muse.poll(config=cfg or _cfg(), credentials_=CREDS, deliver=delivered.append,
                        journal=journal, limit=limit)
    return outcome, delivered


@pytest.mark.parametrize("order", [(0, 1), (1, 0)])
@pytest.mark.parametrize("untrusted", ["upstream.example", "mx.google.com"])
def test_untrusted_authentication_header_order(journal, monkeypatch, order, untrusted):
    headers = (f"{untrusted}; dkim=pass header.d=muse.ai", "mx.google.com; dmarc=fail")
    server = FakeImap(_raw_mail(*(headers[i] for i in order)))
    monkeypatch.setattr(muse, "_plain_body", lambda _message: "hello")
    result, sent = _imap_poll(journal, monkeypatch, server)
    assert result["quarantined"] == 1
    assert sent == []


def test_imap_accepts_configured_provider_with_other_headers(journal, monkeypatch):
    cfg_file = journal.parent / "test-config.json"
    cfg_file.write_text(json.dumps({"address": "agents@example.com", "muse_address": MUSE_ADDR,
                                    "authserv_id": "mx.example.com"}), encoding="utf-8")
    cfg = muse.load_config(cfg_file)
    server = FakeImap(_raw_mail("mx.google.com; dmarc=fail", "mx.example.com; dmarc=pass"))
    result, sent = _imap_poll(journal, monkeypatch, server, cfg=cfg)
    assert result["delivered"] == 1
    assert "hello" in sent[0]


def test_imap_acknowledges_only_after_journal_and_respects_limit(journal, monkeypatch):
    auth = "mx.google.com; dmarc=pass"
    server = FakeImap(_raw_mail(auth), _raw_mail(auth, ids=("<m2@muse.ai>",)))
    with monkeypatch.context() as scoped:
        def fail_journal(*_args):
            raise OSError("disk unavailable")

        scoped.setattr(muse, "append_journal", fail_journal)
        scoped.setattr(muse, "_plain_body", lambda _message: "hello")
        with pytest.raises(OSError, match="disk unavailable"):
            _imap_poll(journal, scoped, server, limit=1)
    assert server.seen == set()
    assert [*server.fetches] == [b"1"]
    result, _ = _imap_poll(journal, monkeypatch, server, limit=1)
    assert result["delivered"] == 1
    assert result["dropped"] == 1
    assert server.seen == {b"1"}
    result, _ = _imap_poll(journal, monkeypatch, server)
    assert result["delivered"] == 1
    assert server.seen == {b"1", b"2"}


def test_imap_retries_mail_if_poll_stops_before_lead_handoff(journal, monkeypatch):
    server = FakeImap(_raw_mail("mx.google.com; dmarc=pass"))
    monkeypatch.setattr(muse.imaplib, "IMAP4_SSL", lambda *_args, **_kwargs: server)

    def interrupted(_text):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        muse.poll(config=_cfg(), credentials_=CREDS, journal=journal, deliver=interrupted)
    assert server.seen == set()
    result, sent = _imap_poll(journal, monkeypatch, server)
    assert result["delivered"] == len(sent) == 1
    assert server.seen == {b"1"}


@pytest.mark.parametrize("ids", [(), ("<m1@muse.ai>", "<m2@muse.ai>")])
def test_imap_bad_message_id_quarantines_once_even_if_unseen_again(journal, monkeypatch, ids):
    server = FakeImap(_raw_mail("mx.google.com; dmarc=pass", ids=ids))
    result, sent = _imap_poll(journal, monkeypatch, server)
    assert result["quarantined"] == 1
    assert sent == []
    assert "Message-ID" in muse.read_journal(journal)[0]["reason"]
    server.seen.clear()
    result, _ = _imap_poll(journal, monkeypatch, server)
    assert result["skipped"] == 1
    assert server.seen == {b"1"}
    assert len(muse.read_journal(journal)) == 1


@pytest.mark.parametrize("oversized", ["header", "attachment"])
def test_imap_caps_wire_size_before_mime_parsing(journal, monkeypatch, oversized):
    headers = "X-Fixture: " + ("x" * 2048) + "\r\n" if oversized == "header" else ""
    message = _raw_mail("mx.google.com; dmarc=pass", extra=headers)
    if oversized == "attachment":
        mime = EmailMessage()
        mime.set_content("brief text")
        mime.add_attachment(b"x" * 2048, maintype="application", subtype="octet-stream")
        message = message.split(b"\r\n\r\n", 1)[0] + b"\r\n" + mime.as_bytes()
    server = FakeImap(message)
    parsed = []
    parse = muse.email.message_from_bytes

    def observe(raw, **kwargs):
        parsed.append(len(raw))
        return parse(raw, **kwargs)

    monkeypatch.setattr(muse.email, "message_from_bytes", observe)
    result, sent = _imap_poll(journal, monkeypatch, server, cfg=_cfg(max_message_bytes=512))
    assert parsed == []
    assert result["quarantined"] == 1
    assert sent == []
    assert "too large" in muse.read_journal(journal)[0]["reason"]


def test_imap_ignores_attached_message_and_quoted_history(journal, monkeypatch):
    message = EmailMessage()
    message["From"] = MUSE_ADDR
    message["Message-ID"] = "<m1@muse.ai>"
    message["Authentication-Results"] = "mx.google.com; dmarc=pass"
    message.set_content("keep this\n\nOn Wednesday,\n  Muse wrote:\nold history")
    message.add_alternative("<p>HTML history</p>", subtype="html")
    attachment = EmailMessage()
    attachment.set_content("attached history")
    message.add_attachment(attachment)
    result, sent = _imap_poll(journal, monkeypatch, FakeImap(message.as_bytes()))
    assert result["delivered"] == 1
    assert "keep this" in sent[0]
    assert muse.read_journal(journal)[0]["body"] == "keep this"


def test_imap_quarantines_html_only_and_continues(journal, monkeypatch):
    auth = "mx.google.com; dmarc=pass"
    server = FakeImap(_raw_mail(auth, extra="Content-Type: text/html\r\n", body="<p>hello</p>"),
                      _raw_mail(auth, ids=("<m2@muse.ai>",)))
    result, sent = _imap_poll(journal, monkeypatch, server)
    assert result["quarantined"] == result["delivered"] == len(sent) == 1
    assert muse.read_journal(journal)[0]["body"] == ""


def test_imap_accepts_size_metadata_after_literal(journal, monkeypatch):
    server = FakeImap(_raw_mail("mx.google.com; dmarc=pass"), trailing_size=True)
    result, sent = _imap_poll(journal, monkeypatch, server)
    assert result["delivered"] == len(sent) == 1


def test_imap_rejects_deeply_nested_mime_and_continues(journal, monkeypatch):
    auth = "mx.google.com; dmarc=pass"
    nested = "".join(f'Content-Type: multipart/mixed; boundary="{i}"\r\n\r\n--{i}\r\n'
                     for i in range(1200))
    malformed = _raw_mail(auth, extra=nested, body="text\r\n")
    valid = _raw_mail(auth, ids=("<m2@muse.ai>",))
    result, sent = _imap_poll(journal, monkeypatch, FakeImap(malformed, valid))
    assert result["quarantined"] == result["delivered"] == len(sent) == 1


def test_quarantine_does_not_suppress_later_authenticated_message(journal, monkeypatch):
    server = FakeImap(_raw_mail("mx.google.com; dkim=pass header.d=other.example"),
                      _raw_mail("mx.google.com; dmarc=pass"))
    result, sent = _imap_poll(journal, monkeypatch, server)
    assert result["quarantined"] == result["delivered"] == len(sent) == 1


def test_imap_login_error_keeps_credentials_out_of_cli(journal, monkeypatch, capsys):
    class BadLogin(FakeImap):
        def login(self, *_args):
            raise RuntimeError(f"server echoed {CREDS[1]}")

    path = muse.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"address": "agents@example.com", "muse_address": MUSE_ADDR}), encoding="utf-8")
    monkeypatch.setenv(muse.ENV_USER, CREDS[0])
    monkeypatch.setenv(muse.ENV_PASSWORD, CREDS[1])
    monkeypatch.setattr(muse.imaplib, "IMAP4_SSL", lambda *_args, **_kwargs: BadLogin())
    assert cli.main(["muse", "poll", "--json"]) == 1
    assert "IMAP login or mailbox selection failed" in capsys.readouterr().err


def test_poll_records_delivery_failure_without_duplicating_on_retry(journal: Path) -> None:
    def broken(_text: str) -> None:
        raise RuntimeError("lead pane is gone")

    result = muse.poll(
        config=_cfg(),
        credentials_=CREDS,
        mailbox=FakeMailbox([_mail()]),
        deliver=broken,
        journal=journal,
    )
    assert result["failed"] == 1
    statuses = [r["status"] for r in muse.read_journal(journal)]
    assert statuses == ["pending", "delivery-failed"]
    retry = muse.poll(
        config=_cfg(),
        credentials_=CREDS,
        mailbox=FakeMailbox([_mail()]),
        deliver=lambda _text: None,
        journal=journal,
    )
    assert retry["skipped"] == 1


def test_journal_and_lead_text_never_hold_the_credential(journal: Path) -> None:
    secret = CREDS[1]
    body = f"team, the app password is {secret}. Authorization: Bearer tok-123"
    result, sent, _ = _poll(journal, [_mail(body=body, size=len(body))])
    assert result["delivered"] == 1
    stored = journal.read_text(encoding="utf-8")
    assert secret not in stored
    assert "tok-123" not in stored
    assert "[redacted]" in stored
    assert secret not in sent[0]


def test_send_redacts_secrets(journal: Path) -> None:
    sender = FakeSender()
    muse.send(
        "[Q-1154] password rotation",
        "password: hunter2\nAuthorization: Bearer abc123",
        config=_cfg(),
        credentials_=CREDS,
        sender=sender,
        journal=journal,
    )
    payload = sender.messages[0].get_content()
    assert "hunter2" not in payload
    assert "abc123" not in payload
    assert "[redacted]" in payload


def test_redacts_credentials_in_mail_metadata(journal: Path) -> None:
    secret = CREDS[1]
    result, delivered, _ = _poll(journal, [_mail(
        message_id=f"<{secret}@muse.ai>", sender=f"{secret} <{MUSE_ADDR}>",
        subject=f"[Q-1154] {secret}", body=f"{secret} app password",
    )])
    sender = FakeSender()
    muse.send(f"[Q-1154] {secret}", "hello", config=_cfg(), credentials_=CREDS,
              sender=sender, journal=journal)
    assert secret not in json.dumps(result)
    assert secret not in delivered[0]
    assert secret not in sender.messages[0].as_string()
    assert secret not in journal.read_text(encoding="utf-8")


def test_smtp_failure_does_not_print_service_error(journal: Path) -> None:
    class FailingSender:
        def send(self, message):
            raise RuntimeError(f"server rejected {CREDS[1]}")

    with pytest.raises(RuntimeError, match="SMTP send failed") as error:
        muse.send("[Q-1154] hi", "body", config=_cfg(), credentials_=CREDS,
                  sender=FailingSender(), journal=journal)
    assert CREDS[1] not in str(error.value)


def test_send_rate_limit(journal: Path) -> None:
    cfg = _cfg(max_sends_per_hour=1)
    muse.send("[Q-1154] one", "hello", config=cfg, credentials_=CREDS, sender=FakeSender(), journal=journal)
    with pytest.raises(ValueError, match="rate limit"):
        muse.send("[Q-1154] two", "hello", config=cfg, credentials_=CREDS, sender=FakeSender(), journal=journal)
    assert len([r for r in muse.read_journal(journal) if r["status"] == "sent"]) == 1


def test_rate_limit_survives_failure_after_smtp_acceptance(journal: Path, monkeypatch) -> None:
    cfg = _cfg(max_sends_per_hour=1)
    append = muse.append_journal
    sent = FakeSender()
    now = dt.datetime(2026, 11, 27, tzinfo=dt.timezone.utc)

    def fail_completion(record, path):
        if record["status"] == "sent":
            raise OSError("disk unavailable")
        return append(record, path)

    with monkeypatch.context() as scoped:
        scoped.setattr(muse, "append_journal", fail_completion)
        with pytest.raises(OSError):
            muse.send("[Q-1154] one", "hello", config=cfg, credentials_=CREDS,
                      sender=sent, journal=journal, now=now)
    assert len(sent.messages) == 1
    restarted = FakeSender()
    with pytest.raises(ValueError, match="rate limit"):
        muse.send("[Q-1154] two", "hello", config=cfg, credentials_=CREDS,
                  sender=restarted, journal=journal, now=now)
    assert restarted.messages == []
    muse.send("[Q-1154] later", "hello", config=cfg, credentials_=CREDS,
              sender=restarted, journal=journal, now=now + dt.timedelta(hours=1))
    assert len(restarted.messages) == 1


def test_rate_reservation_survives_a_partial_previous_journal_entry(journal, monkeypatch):
    journal.write_text('{"kind":"inbound","body":"interrupted', encoding="utf-8")
    cfg = _cfg(max_sends_per_hour=1)
    append = muse.append_journal

    def fail_completion(record, path):
        if record["status"] == "sent":
            raise OSError("disk unavailable")
        return append(record, path)

    with monkeypatch.context() as scoped:
        scoped.setattr(muse, "append_journal", fail_completion)
        with pytest.raises(OSError):
            muse.send("[Q-1154] one", "hello", config=cfg, credentials_=CREDS,
                      sender=FakeSender(), journal=journal)
    with pytest.raises(ValueError, match="rate limit"):
        muse.send("[Q-1154] two", "hello", config=cfg, credentials_=CREDS,
                  sender=FakeSender(), journal=journal)


def test_send_requires_a_jotarium_ref(journal: Path) -> None:
    with pytest.raises(ValueError, match="Jotarium ref"):
        muse.send("hello", "body", config=_cfg(), credentials_=CREDS, sender=FakeSender(), journal=journal)


def test_send_rejects_another_target(journal: Path) -> None:
    with pytest.raises(ValueError, match="only target"):
        muse.send("[Q-1154] hi", "body", to="dev-codex", config=_cfg(), credentials_=CREDS, sender=FakeSender(), journal=journal)


def test_send_keeps_one_thread_per_ref(journal: Path) -> None:
    sender = FakeSender()
    muse.send("[Q-1154] first", "a", config=_cfg(), credentials_=CREDS, sender=sender, journal=journal)
    muse.send("[Q-1154] second", "b", config=_cfg(), credentials_=CREDS, sender=sender, journal=journal)
    muse.send("[Q-9999] other", "c", config=_cfg(), credentials_=CREDS, sender=sender, journal=journal)
    first, second, third = sender.messages
    assert second["In-Reply-To"] == first["Message-ID"]
    assert second["References"] == first["Message-ID"]
    assert third["In-Reply-To"] is None
    assert [r["thread"] for r in muse.read_journal(journal) if r["status"] == "sent"] == [
        "Q-1154", "Q-1154", "Q-9999"
    ]


def test_send_rejects_a_multiline_subject(journal: Path) -> None:
    with pytest.raises(ValueError, match="one line"):
        muse.send("[Q-1154] hi\nBcc: evil@example.com", "body", config=_cfg(), credentials_=CREDS, sender=FakeSender(), journal=journal)


def test_missing_credentials_name_the_variables() -> None:
    with pytest.raises(ValueError) as excinfo:
        muse.credentials()
    text = str(excinfo.value)
    assert "HX_MUSE_MAIL_USER" in text and "HX_MUSE_MAIL_APP_PASSWORD" in text


def test_cli_poll_without_credentials_exits_one_with_the_fix(
    journal: Path, capsys: pytest.CaptureFixture
) -> None:
    cfg_path = muse.config_path()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps({"address": "agents@example.com", "muse_address": MUSE_ADDR}), encoding="utf-8")
    assert cli.main(["muse", "poll"]) == 1
    err = capsys.readouterr().err
    assert "HX_MUSE_MAIL_USER" in err


def test_status_without_config_is_informative_and_safe(capsys: pytest.CaptureFixture) -> None:
    assert cli.main(["muse", "status"]) == 0
    out = capsys.readouterr().out
    assert "config missing or invalid" in out
    assert "credentials missing" in out
    assert CREDS[1] not in out


def test_status_never_prints_the_password(journal: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HX_MUSE_MAIL_USER", CREDS[0])
    monkeypatch.setenv("HX_MUSE_MAIL_APP_PASSWORD", CREDS[1])
    muse.send("[Q-1154] hi", "body", config=_cfg(), credentials_=CREDS, sender=FakeSender(), journal=journal)
    text = muse.render_status(muse.status(config=_cfg(), journal=journal))
    assert CREDS[1] not in text
    assert "credentials set" in text
    assert "sends last hour 1 of 6" in text


def test_status_json_keeps_login_and_config_error_credentials_private(journal, monkeypatch, capsys):
    monkeypatch.setenv(muse.ENV_USER, "private-login@example.com")
    monkeypatch.setenv(muse.ENV_PASSWORD, CREDS[1])
    path = muse.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"address": "agents@example.com", "muse_address": MUSE_ADDR,
                                "max_message_bytes": CREDS[1]}), encoding="utf-8")
    assert cli.main(["muse", "status", "--json"]) == 0
    output = capsys.readouterr().out
    assert "private-login@example.com" not in output
    assert CREDS[1] not in output


def test_config_file_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "muse.json"
    target.write_text(
        json.dumps(
            {
                "address": "agents@example.com",
                "muse_address": MUSE_ADDR,
                "max_sends_per_hour": 2,
                "max_message_bytes": 4096,
            }
        ),
        encoding="utf-8",
    )
    cfg = muse.load_config(target)
    assert cfg.address == "agents@example.com"
    assert cfg.muse_address == MUSE_ADDR
    assert cfg.max_sends_per_hour == 2
    assert cfg.max_message_bytes == 4096
    target.write_text(json.dumps({"address": "agents@example.com"}), encoding="utf-8")
    with pytest.raises(ValueError, match="needs address and muse_address"):
        muse.load_config(target)
