"""hx reconcile settles each send record against the receiver's own log."""
from __future__ import annotations

import datetime as dt
import json
import sqlite3

import pytest

from hx import reconcile

T = dt.datetime(2026, 11, 15, 12, 0, tzinfo=dt.timezone.utc)
DAY = dt.timedelta(days=1)
MINUTE = dt.timedelta(minutes=1)


def iso(when: dt.datetime) -> str:
    return when.isoformat()


def epoch_ms(when: dt.datetime) -> int:
    return int(when.timestamp() * 1000)


def outbox(tmp_path, *specs) -> object:
    hx = tmp_path / ".hx"
    directory = hx / "outbox" / "dev__lead"
    directory.mkdir(parents=True, exist_ok=True)
    for spec in specs:
        record_id, target, text, when = spec[:4]
        state = spec[4] if len(spec) > 4 else "uncertain"
        (directory / f"{record_id}.json").write_text(json.dumps({
            "id": record_id,
            "sender": "dev-lead",
            "target": target,
            "text": text,
            "body": text,
            "created_at": iso(when),
            "state": state,
        }), encoding="utf-8")
    return hx


def roots(tmp_path) -> dict:
    return {
        "claude": tmp_path / "claude",
        "codex": tmp_path / "codex",
        "opencode": tmp_path / "opencode.db",
        "devin": tmp_path / "devin",
    }


def settle(hx, roots):
    return reconcile.reconcile(reconcile.load_records(hx), reconcile.load_logs(roots))


def statuses(summary) -> dict:
    return {item["id"]: item["status"] for item in summary["findings"]}


def claude_session(tmp_path, text: str) -> dict:
    session = roots(tmp_path)["claude"] / "C--Users-test" / "11111111-1111-4111-8111-111111111111.jsonl"
    session.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        {"type": "user", "timestamp": iso(T - 10 * MINUTE), "message": {"role": "user", "content": "earlier"}},
        {"type": "user", "timestamp": iso(T + 30 * dt.timedelta(seconds=1)), "message": {"role": "user", "content": text}},
        {"type": "assistant", "timestamp": iso(T + 10 * MINUTE), "message": {"role": "assistant", "content": []}},
    ]
    session.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")
    return str(session)


def codex_session(tmp_path, text: str) -> dict:
    session = roots(tmp_path)["codex"] / "2026" / "11" / "15" / "rollout-2026-11-15T12-00-00-22222222.jsonl"
    session.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        {"type": "session_meta", "timestamp": iso(T - 20 * MINUTE), "payload": {"id": "22222222"}},
        {"type": "response_item", "timestamp": iso(T - 10 * MINUTE),
         "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "earlier"}]}},
        {"type": "response_item", "timestamp": iso(T + 30 * dt.timedelta(seconds=1)),
         "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}},
        {"type": "response_item", "timestamp": iso(T + 10 * MINUTE),
         "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "done"}]}},
    ]
    session.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")
    return str(session)


def opencode_session(tmp_path, text: str) -> dict:
    db = roots(tmp_path)["opencode"]
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE session (id TEXT, time_created INTEGER, time_updated INTEGER);"
        "CREATE TABLE message (id TEXT, session_id TEXT, time_created INTEGER, data TEXT);"
        "CREATE TABLE part (message_id TEXT, session_id TEXT, time_created INTEGER, data TEXT);"
    )
    session_id = "ses_test"
    conn.execute("INSERT INTO session VALUES (?, ?, ?)",
                 (session_id, epoch_ms(T - 10 * MINUTE), epoch_ms(T + 10 * MINUTE)))
    rows = [("m1", "user", T - 10 * MINUTE, "earlier"),
            ("m2", "user", T + 30 * dt.timedelta(seconds=1), text),
            ("m3", "assistant", T + 10 * MINUTE, "done")]
    for message_id, role, when, content in rows:
        conn.execute("INSERT INTO message VALUES (?, ?, ?, ?)",
                     (message_id, session_id, epoch_ms(when), json.dumps({"role": role})))
        conn.execute("INSERT INTO part VALUES (?, ?, ?, ?)",
                     (message_id, session_id, epoch_ms(when),
                      json.dumps({"type": "text", "text": content})))
    conn.commit()
    conn.close()
    return f"{db}#{session_id}"


def devin_session(tmp_path, text: str) -> dict:
    session = roots(tmp_path)["devin"] / "transcripts" / "ready-otter.json"
    session.parent.mkdir(parents=True, exist_ok=True)
    session.write_text(json.dumps({"session_id": "ready-otter", "steps": [
        {"timestamp": iso(T - 10 * MINUTE), "source": "user", "message": "earlier"},
        {"timestamp": iso(T + 30 * dt.timedelta(seconds=1)), "source": "user", "message": text},
        {"timestamp": iso(T + 10 * MINUTE), "source": "agent", "message": "done"},
    ]}), encoding="utf-8")
    return str(session)


@pytest.mark.parametrize("family,builder", [
    ("claude", claude_session),
    ("codex", codex_session),
    ("opencode", opencode_session),
    ("devin", devin_session),
])
def test_each_family_settles_delivered_lost_and_unconfirmed(tmp_path, family, builder):
    log = builder(tmp_path, "ARRIVED " + family)
    hx = outbox(
        tmp_path,
        ("d", "dev-" + family, "ARRIVED " + family, T),
        ("l", "dev-" + family, "MISSING " + family, T),
        ("u", "dev-" + family, "MISSING LATER " + family, T + 3 * DAY),
    )
    summary = settle(hx, roots(tmp_path))
    assert statuses(summary) == {"d": "delivered", "l": "lost", "u": "unconfirmed"}
    evidence = {item["id"]: item["evidence"] for item in summary["findings"]}
    assert evidence["d"] == log
    assert evidence["l"] is None


def test_lead_seat_family_is_inferred_from_inbound_messages(tmp_path):
    claude_session(tmp_path, "REPORT TO LEAD")
    hx = outbox(
        tmp_path,
        ("d", "dev-lead", "REPORT TO LEAD", T),
        ("l", "dev-lead", "REPORT NEVER SEEN", T),
    )
    summary = settle(hx, roots(tmp_path))
    assert summary["families"]["dev-lead"] == "claude"
    assert summary["inferred"] == {"dev-lead": "claude"}
    assert statuses(summary) == {"d": "delivered", "l": "lost"}


def test_a_transcript_with_no_user_turns_cannot_prove_loss(tmp_path):
    session = roots(tmp_path)["devin"] / "transcripts" / "empty-otter.json"
    session.parent.mkdir(parents=True, exist_ok=True)
    session.write_text(json.dumps({"session_id": "empty-otter", "steps": [
        {"timestamp": iso(T - 10 * MINUTE), "source": "agent", "message": "started"},
        {"timestamp": iso(T + 10 * MINUTE), "source": "agent", "message": "done"},
    ]}), encoding="utf-8")
    hx = outbox(tmp_path, ("a", "dev-devin", "NEVER SEEN", T))
    assert statuses(settle(hx, roots(tmp_path))) == {"a": "unconfirmed"}


def test_a_delivered_receipt_missing_from_the_transcript_is_not_confirmed_lost(tmp_path):
    claude_session(tmp_path, "ARRIVED")
    hx = outbox(tmp_path, ("r", "dev-claude", "NEVER SEEN", T, "delivered"))
    summary = settle(hx, roots(tmp_path))
    assert statuses(summary) == {"r": "unconfirmed"}
    assert summary["receipt_only"] == 1
    assert summary["counts"]["lost"] == 0


@pytest.mark.parametrize("state", ["cancelled", "expired"])
def test_unsent_terminal_records_are_excluded_from_the_measure(tmp_path, state):
    claude_session(tmp_path, "ARRIVED")
    hx = outbox(tmp_path, ("c", "dev-claude", "NEVER SEEN", T, state))
    summary = settle(hx, roots(tmp_path))
    assert summary["records"] == 0
    assert summary["excluded"] == 1


def test_reconcile_reads_the_outbox_without_writing(tmp_path):
    claude_session(tmp_path, "ARRIVED")
    hx = outbox(tmp_path, ("a", "dev-claude", "ARRIVED", T))
    record = hx / "outbox" / "dev__lead" / "a.json"
    before = (record.read_bytes(), sorted(str(path) for path in hx.rglob("*")))
    settle(hx, roots(tmp_path))
    after = (record.read_bytes(), sorted(str(path) for path in hx.rglob("*")))
    assert after == before


def test_render_prints_the_upper_bound_beside_confirmed_loss(tmp_path):
    claude_session(tmp_path, "ARRIVED")
    hx = outbox(
        tmp_path,
        ("d", "dev-claude", "ARRIVED", T),
        ("l", "dev-claude", "NEVER SEEN", T),
    )
    text = reconcile.render_text(settle(hx, roots(tmp_path)), "2026-11-08", "2026-11-20")
    assert "upper bound 128 of 316" in text
    assert "confirmed lost 1 of 2 briefs" in text


def test_command_filters_by_date_and_prints_json(tmp_path, monkeypatch, capsys):
    claude_session(tmp_path, "ARRIVED")
    hx = outbox(
        tmp_path,
        ("in", "dev-claude", "ARRIVED", T),
        ("out", "dev-claude", "ARRIVED", T - 10 * DAY),
    )
    monkeypatch.setattr(reconcile.paths, "hx_dir", lambda: hx)
    monkeypatch.setattr(reconcile, "default_roots", lambda: roots(tmp_path))
    assert reconcile.command(since="2026-11-15", until="2026-11-15", as_json=True) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["records"] == 1
    assert summary["counts"]["delivered"] == 1
