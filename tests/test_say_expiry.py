"""hx say: messages expire with their task; uncertain is never auto-resent.

On 12 Nov `hx say --pending` resent four stale 9 Nov briefs to a busy worker,
because outbox records carried no task identity, expiry or supersession and the
shared delivery path could not tell an obsolete brief from a current one.
"""
from __future__ import annotations

import datetime
import json
import threading
import uuid
from pathlib import Path

import pytest

from hx import host, say


def _member(name, role=None, kind="codex", status="idle", address="t1"):
    team = name.rsplit("-", 1)[0]
    return host.Member(
        name=name, team=team, role=role or name.rsplit("-", 1)[-1],
        kind=kind, status=status, cwd="C:\\repo", address=address,
        session_id=f"ses-{name}", session_reason=None, focused=None, turn=4)


class FakeHc:
    """An hc-shaped host: roster lookup, a send queue, turn-advance proof."""

    def __init__(self):
        self.members = {
            "dev-codex": _member("dev-codex", address="t9"),
            "dev-lead": _member("dev-lead", role="lead", kind="claude", address="t24"),
            "dev-opencode": _member("dev-opencode", kind="opencode", address="t13"),
        }
        self.sent = []

    def own(self):
        return self.members["dev-codex"]

    def all_members(self):
        return list(self.members.values())

    def team(self, prefix):
        return {m["name"]: m for m in self.members.values() if m["name"].startswith(prefix)}

    def info(self, name):
        return self.members[name]

    def send(self, name, text):
        return 0

    def send_checked(self, name, text, **_):
        self.sent.append((name, text))
        self.members[name]["turn"] += 1  # a real submission advances the turn
        return host.SendResult(started=True, status=self.members[name]["status"],
                               turn=4, ended=False, reason=None)

    def send_slash(self, member, command):
        return True

    def read(self, name, lines=200):
        return "some output\n>"

    def press(self, member, *keys):
        return True


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakeHc:
    hc = FakeHc()
    monkeypatch.setenv("HC_TERMINAL_ID", "t9")
    monkeypatch.setenv("HX_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("HX_LEDGER_PATH", str(tmp_path / ".hx" / "ledger.json"))
    monkeypatch.setenv("HX_LOCK_PATH", str(tmp_path / ".hx" / "ledger.lock"))
    monkeypatch.setattr(host, "detect", lambda: hc)
    monkeypatch.setattr(say, "_under_pressure", lambda name: False)
    return hc


def _ledger(tmp_path: Path, **task_status: str) -> None:
    """A ledger with each task at the given status."""
    tasks = {
        task_id: {"id": task_id, "status": status, "owner": "dev-codex",
                  "files": [], "updated": "2026-11-12T00:00:00+00:00"}
        for task_id, status in task_status.items()
    }
    path = tmp_path / ".hx" / "ledger.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"rev": 1, "tasks": tasks}), encoding="utf-8")


def _ago(**kwargs) -> str:
    return (datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(**kwargs)).isoformat()


def _ahead(**kwargs) -> str:
    return (datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(**kwargs)).isoformat()


def _record(tmp_path: Path, body: str = "stale brief", **fields) -> Path:
    """One pending record in dev-codex's outbox, in the real record shape."""
    message_id = fields.pop("id", uuid.uuid4().hex)
    record = {
        "id": message_id,
        "sender": "dev-codex",
        "sender_key": "dev__codex",
        "target": "dev-lead",
        "body": body,
        "text": f"[team from dev-codex] {body}",
        "created_at": fields.pop("created_at", _ago(days=4)),
        "updated_at": _ago(days=4),
        "state": "pending",
        "delivered": False,
        "delivered_at": None,
    }
    record.update(fields)
    directory = tmp_path / ".hx" / "outbox" / "dev__codex"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{message_id}.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    return path


def _state(path: Path) -> str:
    return json.loads(path.read_text(encoding="utf-8"))["state"]


def test_the_four_stale_9_nov_briefs_are_cancelled_not_resent(fake, tmp_path, capsys):
    """The 12 Nov incident shape: four metadata-free worker briefs."""
    paths = [
        _record(
            tmp_path,
            body=f"brief {i}",
            target="dev-opencode",
            created_at="2026-11-09T10:00:00+00:00",
            updated_at="2026-11-09T10:00:00+00:00",
        )
        for i in range(4)
    ]
    assert say.retry_pending() == 0
    assert fake.sent == []
    for path in paths:
        record = json.loads(path.read_text(encoding="utf-8"))
        assert record["state"] == "cancelled"
        assert "expired" in record["note"]
    assert "cancelled" in capsys.readouterr().out


def test_a_worker_brief_gets_the_policy_default_ttl(fake, tmp_path):
    fake.members["dev-opencode"]["status"] = "blocked"
    assert say.say("opencode", "brief without optional flags") == 1
    (path,) = sorted((tmp_path / ".hx" / "outbox" / "dev__codex").glob("*.json"))
    record = json.loads(path.read_text(encoding="utf-8"))
    created = datetime.datetime.fromisoformat(record["created_at"])
    expires = datetime.datetime.fromisoformat(record["expires_at"])
    assert datetime.timedelta(hours=23) < expires - created < datetime.timedelta(hours=25)


def test_a_pending_brief_for_a_closed_task_is_cancelled(fake, tmp_path):
    _ledger(tmp_path, **{"q-334": "failed"})
    path = _record(tmp_path, target="dev-opencode", task_id="q-334")
    assert say.retry_pending() == 0
    assert _state(path) == "cancelled"
    assert fake.sent == []


def test_an_expired_brief_is_cancelled(fake, tmp_path):
    path = _record(tmp_path, target="dev-opencode", expires_at=_ago(hours=1))
    assert say.retry_pending() == 0
    assert _state(path) == "cancelled"
    assert fake.sent == []


def test_an_uncertain_record_is_never_auto_resent(fake, tmp_path, capsys):
    path = _record(tmp_path, state="uncertain")
    assert say.retry_pending() == 1
    assert fake.sent == []
    assert _state(path) == "uncertain"
    assert "reconcile" in capsys.readouterr().out


def test_an_uncertain_record_waits_for_reconcile_even_after_expiry(fake, tmp_path):
    path = _record(tmp_path, target="dev-opencode", state="uncertain", expires_at=_ago(hours=1))
    assert say.retry_pending() == 1
    assert _state(path) == "uncertain"
    assert fake.sent == []


def test_a_superseded_brief_is_cancelled(fake, tmp_path):
    old = _record(tmp_path, body="stale brief", target="dev-opencode",
                  created_at=_ago(hours=1))
    old_id = json.loads(old.read_text(encoding="utf-8"))["id"]
    _record(tmp_path, body="corrected brief", target="dev-opencode", state="delivered",
            delivered=True, supersedes=old_id)
    assert say.retry_pending() == 0
    record = json.loads(old.read_text(encoding="utf-8"))
    assert record["state"] == "cancelled"
    assert "superseded" in record["note"]
    assert fake.sent == []


def test_a_pending_superseder_cancels_the_old_and_still_delivers(fake, tmp_path):
    old = _record(tmp_path, body="stale brief", target="dev-opencode",
                  created_at=_ago(hours=2))
    old_id = json.loads(old.read_text(encoding="utf-8"))["id"]
    new = _record(tmp_path, body="corrected brief", target="dev-opencode", supersedes=old_id,
                  created_at=_ago(hours=1))
    assert say.retry_pending() == 0
    assert _state(old) == "cancelled"
    assert _state(new) == "delivered"
    assert [text for _, text in fake.sent] == ["[team from dev-codex] corrected brief"]


def test_a_current_message_still_delivers(fake, tmp_path):
    path = _record(tmp_path, body="current report")
    assert say.retry_pending() == 0
    assert fake.sent == [("dev-lead", "[team from dev-codex] current report")]
    assert _state(path) == "delivered"


def test_an_open_task_brief_still_delivers(fake, tmp_path):
    _ledger(tmp_path, **{"q-334": "in_progress"})
    path = _record(tmp_path, target="dev-opencode", task_id="q-334",
                   created_at=_ago(hours=1))
    assert say.retry_pending() == 0
    assert len(fake.sent) == 1
    assert _state(path) == "delivered"


def test_a_first_send_to_a_closed_task_is_refused(fake, tmp_path, capsys):
    """The same guard covers a first send, not only retries."""
    _ledger(tmp_path, **{"q-289": "done"})
    assert say.say("opencode", "stale brief", task="q-289") == 1
    assert fake.sent == []
    assert "cancelled" in capsys.readouterr().out


def test_a_first_send_records_task_session_expiry_and_supersedes(fake, tmp_path):
    assert say.say("lead", "current brief", task="q-334", ttl=3600,
                   supersedes="abc123") == 0
    (record,) = sorted(
        (tmp_path / ".hx" / "outbox" / "dev__codex").glob("*.json"))
    data = json.loads(record.read_text(encoding="utf-8"))
    assert data["task_id"] == "q-334"
    assert data["session_id"] == "ses-dev-lead"
    assert data["supersedes"] == "abc123"
    expires = datetime.datetime.fromisoformat(data["expires_at"])
    assert expires > datetime.datetime.now(datetime.timezone.utc)


def test_a_first_send_lead_report_is_not_cancelled_by_task_metadata(fake, tmp_path):
    _ledger(tmp_path, **{"q-289": "done"})
    assert say.say(
        "lead",
        "[team report from dev-codex] STATUS: done",
        task="q-289",
        ttl=3600,
    ) == 0
    assert fake.sent == [("dev-lead", "[team report from dev-codex] STATUS: done")]


def test_a_lead_report_ignores_task_expiry_and_supersession(fake, tmp_path):
    """The destination role, not the metadata, makes this a durable report."""
    _ledger(tmp_path, **{"q-289": "done"})
    report = _record(
        tmp_path,
        body="[team report from dev-codex] STATUS: done",
        task_id="q-289",
        expires_at=_ago(hours=1),
    )
    report_id = json.loads(report.read_text(encoding="utf-8"))["id"]
    _record(
        tmp_path,
        body="newer status",
        target="dev-lead",
        state="delivered",
        delivered=True,
        supersedes=report_id,
    )
    assert say.retry_pending() == 0
    assert fake.sent == [("dev-lead", "[team report from dev-codex] STATUS: done")]
    assert _state(report) == "delivered"


def test_a_changed_worker_session_cancels_a_queued_brief(fake, tmp_path):
    path = _record(
        tmp_path,
        target="dev-opencode",
        session_id="old-session",
        created_at=_ago(hours=1),
    )
    assert say.retry_pending() == 0
    assert fake.sent == []
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["state"] == "cancelled"
    assert "session" in record["note"]


def test_a_task_closing_during_resolution_cancels_before_transport(fake, tmp_path, monkeypatch):
    _ledger(tmp_path, **{"fixture": "in_progress"})
    path = _record(tmp_path, target="dev-opencode", task_id="fixture",
                   created_at=_ago(hours=1))
    original = say.resolve

    def close_then_resolve(sender_member, target):
        _ledger(tmp_path, fixture="done")
        return original(sender_member, target)

    monkeypatch.setattr(say, "resolve", close_then_resolve)
    assert say.retry_pending() == 0
    assert fake.sent == []
    assert _state(path) == "cancelled"


def test_an_in_flight_send_wins_over_a_later_expiry(fake, tmp_path, monkeypatch):
    now = datetime.datetime.now(datetime.timezone.utc)
    path = _record(
        tmp_path,
        target="dev-opencode",
        body="expiring brief",
        created_at=now.isoformat(),
        updated_at=now.isoformat(),
        expires_at=(now + datetime.timedelta(days=1)).isoformat(),
    )

    class Clock(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            current = cls.current
            return current.astimezone(tz) if tz else current.replace(tzinfo=None)

    Clock.current = now

    entered = threading.Event()
    release = threading.Event()
    original_send = fake.send_checked

    def paused_send(name, text, **kwargs):
        entered.set()
        if not release.wait(5):
            raise RuntimeError("test timeout")
        return original_send(name, text, **kwargs)

    outcomes = []
    errors = []
    fake.send_checked = paused_send
    monkeypatch.setattr(say.datetime, "datetime", Clock)

    def first_retry():
        try:
            outcomes.append(say.retry_pending())
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=first_retry)
    thread.start()
    assert entered.wait(5)
    assert say._claim_path(path).exists()
    Clock.current = now + datetime.timedelta(days=2)
    competing_exit = say.retry_pending()
    intermediate = _state(path)
    release.set()
    thread.join(5)

    assert not thread.is_alive()
    assert errors == []
    assert outcomes == [0]
    assert competing_exit == 1
    assert intermediate == "pending"
    assert _state(path) == "delivered"
    assert fake.sent == [("dev-opencode", "[team from dev-codex] expiring brief")]


def test_ttl_must_be_finite_and_policy_default_never_disables_expiry():
    import math
    import pytest
    from hx import say as say_mod
    for bad in (math.nan, math.inf, 0, -1):
        with pytest.raises(ValueError):
            say_mod.say("devin", "x", ttl=bad)
    assert say_mod._work_ttl_seconds({}) == say_mod.DEFAULT_WORK_TTL_SECONDS
    assert say_mod._work_ttl_seconds({"say": {"work_ttl_seconds": 60}}) == 60.0
