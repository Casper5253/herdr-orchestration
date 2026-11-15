"""Q-436: close must flag in-progress Jotarium work nobody holds."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from hx import jotarium, ledger, usage
from hx.__main__ import main

FIXTURE = Path(__file__).parent / "fixtures" / "jotarium-2026-11-14.json"


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_q412_was_unattended_on_14_november() -> None:
    data = _fixture()
    flagged, covered = jotarium.find_unattended(data["items"], data["tasks"], data["live"])
    assert [row["ref"] for row in flagged] == ["Q-412"]
    assert {row["ref"] for row in covered} == {"Q-435", "Q-437", "Q-438", "Q-439"}


def test_an_open_task_with_a_jotarium_field_covers_its_item() -> None:
    data = _fixture()
    tasks = data["tasks"] + [{"id": "outfit", "title": "Outfit work", "owner": "dev-opencode",
                              "status": "open", "jotarium": "Q-412"}]
    flagged, covered = jotarium.find_unattended(data["items"], tasks, data["live"])
    assert [row["ref"] for row in flagged] == []
    assert any(row["ref"] == "Q-412" and "outfit" in row["by"] for row in covered)


def test_a_finished_item_is_not_flagged() -> None:
    data = _fixture()
    data["items"] = [row for row in data["items"] if row["ref"] == "Q-440"]
    flagged, _ = jotarium.find_unattended(data["items"], [], [])
    assert flagged == []


def test_an_agent_assignee_that_is_not_live_is_not_a_holder() -> None:
    item = {"ref": "Q-1", "status": "in_progress", "assignee": "agent:dev-gone",
            "blockers": {"open": None, "resolved": []}}
    flagged, _ = jotarium.find_unattended([item], [], [])
    assert [row["ref"] for row in flagged] == ["Q-1"]


class _Response:
    def __init__(self, body: dict) -> None:
        self._raw = json.dumps(body).encode("utf-8")

    def read(self) -> bytes:
        return self._raw

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def test_query_posts_an_agent_read_with_the_bearer_token(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return _Response({"ok": True, "data": {"items": []}})

    monkeypatch.setattr(jotarium.urllib.request, "urlopen", fake_urlopen)
    client = jotarium.JotariumClient("http://127.0.0.1:4180", "secret-token")
    assert client.query("listItems", {"status": ["in_progress"]}) == {"items": []}
    request = captured["request"]
    assert request.full_url == "http://127.0.0.1:4180/api/queries/listItems"
    assert dict(request.header_items())["Authorization"] == "Bearer secret-token"
    assert captured["timeout"] == jotarium.TIMEOUT_SECONDS


def test_a_refused_query_never_echoes_the_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        jotarium.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response({"ok": False, "error": {"code": "forbidden"}}),
    )
    client = jotarium.JotariumClient("http://127.0.0.1:4180", "top-secret-token")
    with pytest.raises(jotarium.JotariumError) as caught:
        client.query("listItems", {})
    assert "top-secret-token" not in str(caught.value)
    assert "forbidden" in str(caught.value)


def test_the_audit_uses_the_fixture_and_marks_the_unattended_item(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = _fixture()
    by_ref = {row["ref"]: row for row in data["items"]}

    class FakeClient:
        url = "http://127.0.0.1:4180"

        def query(self, name: str, payload: dict) -> dict:
            if name == "listItems":
                return {"items": [row for row in data["items"] if row["status"] == "in_progress"],
                        "nextCursor": None}
            return {"item": by_ref[payload["ref"]]}

    class FakeLedger:
        def list_tasks(self) -> list[dict]:
            return data["tasks"]

    monkeypatch.setattr(usage, "load_policy", lambda: {"team_prefix": "dev-", "lead_role": "lead"})
    monkeypatch.setattr(usage, "team_prefix", lambda _policy: "dev-")
    monkeypatch.setattr(usage, "team_agents", lambda _prefix: {row["name"]: row for row in data["live"]})
    monkeypatch.setattr(ledger, "default_ledger", lambda: FakeLedger())
    monkeypatch.setenv("QUESTLOG_TOKEN_CLAUDE", "never-print-me")
    result = jotarium.audit(FakeClient())
    assert result["ok"] is False
    assert [row["ref"] for row in result["flagged"]] == ["Q-412"]
    assert result["token_env"] == "QUESTLOG_TOKEN_CLAUDE"
    assert "never-print-me" not in json.dumps(result)


def test_the_cli_exits_one_when_it_flags_work(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setattr(jotarium, "audit", lambda: {
        "ok": False,
        "flagged": [{"ref": "Q-412", "title": "Outfit studio", "assignee": None}],
        "covered": [],
        "checked": 1,
        "token_env": "QUESTLOG_TOKEN_CLAUDE",
        "url": "http://127.0.0.1:4180",
    })
    assert main(["jotarium", "audit"]) == 1
    assert "Q-412" in capsys.readouterr().out


def test_the_cli_exits_zero_on_a_clean_audit(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setattr(jotarium, "audit", lambda: {
        "ok": True,
        "flagged": [],
        "covered": [],
        "checked": 0,
        "token_env": "QUESTLOG_TOKEN_CLAUDE",
        "url": "http://127.0.0.1:4180",
    })
    assert main(["jotarium", "audit"]) == 0
    assert '"ok": true' in capsys.readouterr().out
