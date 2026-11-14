"""Target-filtered pending delivery shares the guarded say path."""
from __future__ import annotations

from hx import host, say


def _member(name):
    return host.member({"name": name, "agent": "codex", "agent_status": "idle",
                        "agent_session": {"value": "sid"}, "cwd": "C:\\repo"})


class FakeHost(host.HerdrHost):
    def __init__(self):
        super().__init__("w1:p1")
        self.owner = _member("dev-codex")
        self.members = {name: _member(name)
                        for name in ("dev-codex", "dev-opencode", "dev-claude")}
        self.sent = []

    def own(self):
        return self.owner

    def team(self, prefix):
        return {n: m for n, m in self.members.items() if n.startswith(prefix)}

    def send_checked(self, name, text, **kwargs):
        self.sent.append((name, text))
        return host.SendResult(started=True, status="idle", turn=1,
                               ended=False, reason=None)


def test_pending_delivery_only_reaches_the_named_worker(tmp_path, monkeypatch):
    monkeypatch.setenv("HX_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(host, "detect", lambda: fake)
    monkeypatch.setattr(say, "_under_pressure", lambda name: False)
    monkeypatch.setattr(say, "_denied_line_for", lambda destination: "")
    fake = FakeHost()
    say._write_outbox("dev__codex", "dev-codex", "opencode", "to opencode", "to opencode")
    say._write_outbox("dev__codex", "dev-codex", "claude", "to claude", "to claude")

    counts = say.retry_pending_for(fake, "dev-opencode")

    assert counts["considered"] == 1
    assert counts["delivered"] == 1
    assert counts["unresolved"] == 0
    assert [name for name, _ in fake.sent] == ["dev-opencode"]
    assert fake.sent[0][1] == "[team from dev-codex] to opencode"
    remaining = say.records_for("dev__codex")
    assert len(remaining) == 1
    path, record, state = remaining[0]
    assert record["target"] == "claude" and state == "pending"