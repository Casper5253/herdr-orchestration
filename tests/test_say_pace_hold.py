import pytest

from hx import pace, say, usage

pytestmark = pytest.mark.pace_hold

BRIEF = r"New brief: read C:\repo\.hx\briefs\Q-1.md and do exactly that."
POSIX_BRIEF = "New brief: read /repo/.hx/briefs/Q-1.md and do exactly that."


def test_posix_pointer_is_a_brief_too(monkeypatch):
    _pools(monkeypatch, "ahead")
    assert say._over_pace("dev-codex", POSIX_BRIEF)


def _pools(monkeypatch, codex, opencode="underused", hold=True):
    monkeypatch.setattr(usage, "load_policy", lambda: {"pace": {"hold_briefs": hold}})
    monkeypatch.setattr(usage, "usage_snapshot", lambda policy: {})
    monkeypatch.setattr(pace, "build", lambda snapshot, policy: {"pools": {
        "codex": {"status": codex, "reason": "ahead on 5h: defer optional work"},
        "opencode": {"status": opencode}, "devin": {"status": "free"}, "claude": {"status": "on_pace"}}})


def test_brief_to_pool_ahead_of_pace_is_held_and_names_room(monkeypatch, capsys):
    _pools(monkeypatch, "ahead")
    assert say._over_pace("dev-codex", BRIEF)
    err = capsys.readouterr().err
    assert "ahead of pace" in err and "opencode" in err and "devin" in err


def test_brief_to_pool_on_pace_is_sent(monkeypatch):
    _pools(monkeypatch, "on_pace")
    assert not say._over_pace("dev-codex", BRIEF)


def test_plain_message_is_never_held(monkeypatch):
    _pools(monkeypatch, "ahead")
    assert not say._over_pace("dev-codex", "Change of order: pause now.")


def test_policy_can_turn_the_hold_off(monkeypatch):
    _pools(monkeypatch, "ahead", hold=False)
    assert not say._over_pace("dev-codex", BRIEF)


def test_unknown_usage_fails_open(monkeypatch):
    monkeypatch.setattr(usage, "load_policy", lambda: {})
    def boom(policy):
        raise OSError("usage unavailable")
    monkeypatch.setattr(usage, "usage_snapshot", boom)
    assert not say._over_pace("dev-codex", BRIEF)
