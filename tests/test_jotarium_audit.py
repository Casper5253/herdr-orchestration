"""Q-436: close must flag in-progress Jotarium work nobody holds.

Q-686: it must also flag creator send-backs that no follow-up answers.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from hx import jotarium, ledger, usage
from hx.__main__ import main

FIXTURE = Path(__file__).parent / "fixtures" / "jotarium-2026-11-14.json"
SEND_BACK_FIXTURES = [
    Path(__file__).parent / "fixtures" / "jotarium-2026-11-18-q396-sendback.json",
    Path(__file__).parent / "fixtures" / "jotarium-2026-11-18-q630-sendback.json",
]
SEND_BACK_IDS = ["q396-rejected-decide", "q630-sent-back-check"]


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _send_back_fixture(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _with_item(data: dict, item: dict[str, Any]) -> list[dict[str, Any]]:
    return [item] + [row for row in data["items"] if row["ref"] != item["ref"]]


def _task_for(ref: str) -> dict[str, Any]:
    return {
        "id": f"work-{ref.lower()}",
        "title": f"{ref} work",
        "owner": "dev-claude",
        "status": "in_progress",
        "jotarium": ref,
    }


def _card_item(
    resolved: list[dict[str, Any]] | None = None,
    open_card: dict[str, Any] | None = None,
    assignee: str | None = None,
) -> dict[str, Any]:
    return {
        "ref": "Q-700",
        "sharedNum": 700,
        "kind": "task",
        "title": "Card work",
        "status": "in_progress",
        "assignee": assignee,
        "acceptance": {"status": "none"},
        "blockers": {"open": open_card, "resolved": resolved or []},
        "followUp": None,
        "children": {"open": 0, "done": 0},
        "createdAt": "2026-11-17T09:00:00.000Z",
        "updatedAt": "2026-11-18T04:00:00.000Z",
    }


def _resolved_card(
    at: str = "2026-11-18T04:00:00.000Z",
    revision: int = 4,
    action: str = "choose",
    comment: str | None = "Also handle the empty save.",
) -> dict[str, Any]:
    card: dict[str, Any] = {
        "request": {"kind": "decide"},
        "revision": revision,
        "openedAt": "2026-11-18T03:00:00.000Z",
        "openedBy": "agent:dev-claude",
        "resolvedAt": at,
        "localDay": "2026-11-18",
        "action": action,
    }
    if comment is not None:
        card["comment"] = comment
    if action == "choose":
        card["choice"] = "b"
    return card


def _follow_up(
    ref: str = "Q-701",
    of: str = "Q-700",
    revision: int = 4,
    created_at: str = "2026-11-18T04:00:00.000Z",
    source: str = "blocker",
) -> dict[str, Any]:
    return {
        "ref": ref,
        "sharedNum": int(ref.split("-")[1]),
        "kind": "task",
        "title": "Follow-up from the creator's comment",
        "status": "todo",
        "assignee": None,
        "acceptance": {"status": "none"},
        "blockers": {"open": None, "resolved": []},
        "followUp": {"of": of, "source": source, "revision": revision, "reviewOnDone": "none"},
        "children": {"open": 0, "done": 0},
        "createdAt": created_at,
        "updatedAt": created_at,
    }


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


@pytest.mark.parametrize("path", SEND_BACK_FIXTURES, ids=SEND_BACK_IDS)
def test_a_creator_send_back_is_flagged_even_when_an_hx_task_covers_the_item(path: Path) -> None:
    data = _send_back_fixture(path)
    item = data["items"][0]
    flagged, _ = jotarium.find_unattended(data["items"], data["tasks"], data["live"])
    assert [row["ref"] for row in flagged] == [item["ref"]]
    assert flagged[0]["action"] == jotarium.SEND_BACK_ACTION
    assert flagged[0]["sendBackAt"] == item["acceptance"]["decidedAt"]


@pytest.mark.parametrize("path", SEND_BACK_FIXTURES, ids=SEND_BACK_IDS)
def test_a_child_created_after_the_send_back_answers_it(path: Path) -> None:
    data = _send_back_fixture(path)
    item = {**data["items"][0],
            "childItems": [{"ref": "Q-687", "status": "todo",
                            "createdAt": "2026-11-18T09:00:00.000Z"}]}
    flagged, covered = jotarium.find_unattended(_with_item(data, item), data["tasks"], data["live"])
    assert flagged == []
    assert any(row["ref"] == item["ref"] and "child Q-687" in row["by"] for row in covered)


def test_a_child_created_before_the_send_back_does_not_answer_it() -> None:
    data = _send_back_fixture(SEND_BACK_FIXTURES[0])
    item = {**data["items"][0],
            "childItems": [{"ref": "Q-650", "status": "todo",
                            "createdAt": "2026-11-17T10:00:00.000Z"}]}
    flagged, _ = jotarium.find_unattended(_with_item(data, item), data["tasks"], data["live"])
    assert [row["ref"] for row in flagged] == ["Q-396"]


def test_a_follow_up_item_created_after_the_send_back_answers_it() -> None:
    data = _send_back_fixture(SEND_BACK_FIXTURES[1])
    follow = _follow_up(ref="Q-688", of="Q-630", revision=2,
                        created_at="2026-11-18T09:05:00.000Z", source="review")
    items = data["items"] + [follow]
    flagged, covered = jotarium.find_unattended(items, data["tasks"], data["live"])
    assert flagged == []
    assert any(row["ref"] == "Q-630" and "follow-up item Q-688" in row["by"] for row in covered)


def test_a_follow_up_for_an_older_revision_does_not_answer_the_send_back() -> None:
    data = _send_back_fixture(SEND_BACK_FIXTURES[1])
    follow = _follow_up(ref="Q-688", of="Q-630", revision=1,
                        created_at="2026-11-18T09:05:00.000Z", source="review")
    items = data["items"] + [follow]
    flagged, _ = jotarium.find_unattended(items, data["tasks"], data["live"])
    assert [row["ref"] for row in flagged] == ["Q-630"]


def test_a_resolved_card_with_a_creator_comment_is_a_send_back() -> None:
    item = _card_item(resolved=[_resolved_card()])
    flagged, _ = jotarium.find_unattended([item], [_task_for("Q-700")], [])
    assert [row["ref"] for row in flagged] == ["Q-700"]
    assert flagged[0]["action"] == jotarium.SEND_BACK_ACTION
    assert flagged[0]["sendBackAt"] == "2026-11-18T04:00:00.000Z"


def test_the_follow_up_the_server_creates_with_the_answer_answers_it() -> None:
    item = _card_item(resolved=[_resolved_card()])
    flagged, covered = jotarium.find_unattended(
        [item, _follow_up(created_at="2026-11-18T04:00:00.000Z")],
        [_task_for("Q-700")],
        [],
    )
    assert flagged == []
    assert any(row["ref"] == "Q-700" and "follow-up item Q-701" in row["by"] for row in covered)


def test_a_follow_up_for_another_revision_does_not_answer_it() -> None:
    item = _card_item(resolved=[_resolved_card(revision=4)])
    flagged, _ = jotarium.find_unattended(
        [item, _follow_up(revision=3)],
        [_task_for("Q-700")],
        [],
    )
    assert [row["ref"] for row in flagged] == ["Q-700"]


def test_a_resolved_card_without_a_comment_is_not_a_send_back() -> None:
    item = _card_item(resolved=[_resolved_card(comment=None)])
    flagged, _ = jotarium.find_unattended([item], [_task_for("Q-700")], [])
    assert flagged == []


def test_a_later_plain_resolution_clears_an_earlier_commented_one() -> None:
    item = _card_item(resolved=[
        _resolved_card(),
        _resolved_card(at="2026-11-18T05:00:00.000Z", revision=5, action="accept", comment=None),
    ])
    flagged, _ = jotarium.find_unattended([item], [_task_for("Q-700")], [])
    assert flagged == []


def test_a_later_plain_card_does_not_clear_a_live_rejection() -> None:
    data = _send_back_fixture(SEND_BACK_FIXTURES[0])
    item = {**data["items"][0], "blockers": {"open": None, "resolved": [
        _resolved_card(at="2026-11-18T03:00:00.000Z", revision=4, action="accept", comment=None),
    ]}}
    flagged, _ = jotarium.find_unattended(_with_item(data, item), data["tasks"], data["live"])
    assert [row["ref"] for row in flagged] == ["Q-396"]
    assert flagged[0]["action"] == jotarium.SEND_BACK_ACTION


def test_an_agent_withdrawal_does_not_clear_a_creator_comment() -> None:
    withdrawal = _resolved_card(at="2026-11-18T05:00:00.000Z", revision=5, action="notNeeded", comment=None)
    withdrawal["reason"] = "not needed"
    withdrawal["withdrawnBy"] = "agent:dev-claude"
    item = _card_item(resolved=[_resolved_card(), withdrawal])
    flagged, _ = jotarium.find_unattended([item], [_task_for("Q-700")], [])
    assert [row["ref"] for row in flagged] == ["Q-700"]


def test_a_card_opened_after_the_send_back_answers_it() -> None:
    item = _card_item(resolved=[_resolved_card()],
                      open_card={"openedAt": "2026-11-18T05:00:00.000Z"})
    flagged, covered = jotarium.find_unattended([item], [_task_for("Q-700")], [])
    assert flagged == []
    assert any(row["ref"] == "Q-700" and "Needs-you card" in row["by"] for row in covered)


def test_an_old_card_does_not_answer_a_send_back() -> None:
    item = _card_item(resolved=[_resolved_card()],
                      open_card={"openedAt": "2026-11-18T03:00:00.000Z"})
    flagged, _ = jotarium.find_unattended([item], [_task_for("Q-700")], [])
    assert [row["ref"] for row in flagged] == ["Q-700"]


def test_a_live_worker_answers_a_send_back() -> None:
    item = _card_item(resolved=[_resolved_card()], assignee="agent:dev-claude")
    flagged, covered = jotarium.find_unattended([item], [], [{"name": "dev-claude"}])
    assert flagged == []
    assert covered == [{"ref": "Q-700", "by": "live worker dev-claude"}]


def test_timestamps_with_offsets_compare_as_instants() -> None:
    item = _card_item(resolved=[_resolved_card(at="2026-11-18T10:31:00+08:00")])
    follow = _follow_up(created_at="2026-11-18T02:35:00.000Z")
    flagged, _ = jotarium.find_unattended([item, follow], [_task_for("Q-700")], [])
    assert flagged == []


def test_the_same_instant_with_different_precision_answers_the_send_back() -> None:
    item = _card_item(resolved=[_resolved_card(at="2026-11-18T02:31:00Z")])
    follow = _follow_up(created_at="2026-11-18T02:31:00.000Z")
    flagged, _ = jotarium.find_unattended([item, follow], [_task_for("Q-700")], [])
    assert flagged == []


def test_a_cancelled_follow_up_does_not_answer_the_send_back() -> None:
    item = _card_item(resolved=[_resolved_card()])
    follow = {**_follow_up(), "status": "cancelled"}
    flagged, _ = jotarium.find_unattended([item, follow], [_task_for("Q-700")], [])
    assert [row["ref"] for row in flagged] == ["Q-700"]


def test_a_cancelled_child_does_not_answer_the_send_back() -> None:
    item = _card_item(resolved=[_resolved_card()])
    item["childItems"] = [{"ref": "Q-702", "status": "cancelled",
                           "createdAt": "2026-11-18T05:00:00.000Z"}]
    flagged, _ = jotarium.find_unattended([item], [_task_for("Q-700")], [])
    assert [row["ref"] for row in flagged] == ["Q-700"]


def test_a_done_item_with_an_unanswered_card_comment_is_audited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    done = {
        "ref": "Q-710", "sharedNum": 710, "kind": "task", "title": "Finished with an open ask",
        "status": "done", "assignee": None,
        "acceptance": {"status": "accepted", "decidedBy": "human",
                       "decidedAt": "2026-11-18T03:00:00.000Z", "localDay": "2026-11-18",
                       "revision": 2},
        "blockers": {"open": None, "resolved": [
            _resolved_card(at="2026-11-18T04:00:00.000Z", revision=3),
        ]},
        "followUp": None, "children": {"open": 0, "done": 0},
        "createdAt": "2026-11-17T09:00:00.000Z", "updatedAt": "2026-11-18T04:00:00.000Z",
    }

    class FakeClient:
        url = "http://127.0.0.1:4180"

        def query(self, name: str, payload: dict) -> dict:
            if name != "listItems":
                return {"item": done}
            if payload.get("parent"):
                return {"items": [], "nextCursor": None}
            return {"items": [done], "nextCursor": None}

    _patch_team(monkeypatch, [], [])
    result = jotarium.audit(FakeClient())
    assert result["ok"] is False
    assert [row["ref"] for row in result["flagged"]] == ["Q-710"]
    assert result["flagged"][0]["action"] == jotarium.SEND_BACK_ACTION


def test_a_detail_item_without_a_ref_fails_closed() -> None:
    with pytest.raises(jotarium.JotariumError):
        jotarium.find_unattended([{"status": "in_progress", "assignee": None}], [], [])


def test_the_audit_fails_closed_when_a_detail_has_no_ref(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeClient:
        url = "http://127.0.0.1:4180"

        def query(self, name: str, payload: dict) -> dict:
            if name == "listItems":
                return {"items": [{"ref": "Q-720", "status": "in_progress",
                                   "children": {"open": 0, "done": 0}}],
                        "nextCursor": None}
            return {"item": {"status": "in_progress", "assignee": None}}

    _patch_team(monkeypatch, [], [])
    with pytest.raises(jotarium.JotariumError):
        jotarium.audit(FakeClient())


def test_a_cancelled_newest_child_does_not_hide_an_older_live_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = _send_back_fixture(SEND_BACK_FIXTURES[1])
    item = data["items"][0]
    live_child = {"ref": "Q-901", "sharedNum": 901, "kind": "task", "title": "Notes v2",
                  "status": "todo", "assignee": None, "acceptance": {"status": "none"},
                  "blockers": {"open": None, "resolved": []}, "followUp": None,
                  "children": {"open": 0, "done": 0},
                  "createdAt": "2026-11-18T09:00:00.000Z",
                  "updatedAt": "2026-11-18T09:00:00.000Z"}
    cancelled = {**live_child, "ref": "Q-902", "sharedNum": 902, "status": "cancelled",
                 "createdAt": "2026-11-18T10:00:00.000Z",
                 "updatedAt": "2026-11-18T10:00:00.000Z"}
    rows = [item, live_child, cancelled]
    by_ref = {row["ref"]: row for row in rows}

    class FakeClient:
        url = "http://127.0.0.1:4180"

        def query(self, name: str, payload: dict) -> dict:
            if name != "listItems":
                return {"item": by_ref[payload["ref"]]}
            if payload.get("parent"):
                return {"items": [live_child, cancelled], "nextCursor": None}
            return {"items": rows, "nextCursor": None}

    _patch_team(monkeypatch, data["live"], data["tasks"])
    result = jotarium.audit(FakeClient())
    assert result["ok"] is True


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


def _patch_team(monkeypatch: pytest.MonkeyPatch, live: list[dict], tasks: list[dict]) -> None:
    class FakeLedger:
        def list_tasks(self) -> list[dict]:
            return tasks

    monkeypatch.setattr(usage, "load_policy", lambda: {"team_prefix": "dev-", "lead_role": "lead"})
    monkeypatch.setattr(usage, "team_prefix", lambda _policy: "dev-")
    monkeypatch.setattr(usage, "team_agents", lambda _prefix: {row["name"]: row for row in live})
    monkeypatch.setattr(ledger, "default_ledger", lambda: FakeLedger())


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

    _patch_team(monkeypatch, data["live"], data["tasks"])
    monkeypatch.setenv("QUESTLOG_TOKEN_CLAUDE", "never-print-me")
    result = jotarium.audit(FakeClient())
    assert result["ok"] is False
    assert [row["ref"] for row in result["flagged"]] == ["Q-412"]
    assert result["token_env"] == "QUESTLOG_TOKEN_CLAUDE"
    assert "never-print-me" not in json.dumps(result)


def test_the_audit_clears_a_send_back_once_a_child_landed_after_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = _send_back_fixture(SEND_BACK_FIXTURES[1])
    item = data["items"][0]
    child = {
        "ref": "Q-690", "sharedNum": 690, "kind": "task", "title": "Notes v2",
        "status": "todo", "assignee": "agent:dev-opencode",
        "acceptance": {"status": "none"}, "blockers": {"open": None, "resolved": []},
        "followUp": None, "children": {"open": 0, "done": 0},
        "createdAt": "2026-11-18T08:00:00.000Z", "updatedAt": "2026-11-18T08:00:00.000Z",
    }
    rows = [item, child]
    by_ref = {row["ref"]: row for row in rows}

    class FakeClient:
        url = "http://127.0.0.1:4180"

        def query(self, name: str, payload: dict) -> dict:
            if name != "listItems":
                return {"item": by_ref[payload["ref"]]}
            if payload.get("parent"):
                return {"items": [child], "nextCursor": None}
            return {"items": rows, "nextCursor": None}

    _patch_team(monkeypatch, data["live"], data["tasks"])
    result = jotarium.audit(FakeClient())
    assert result["ok"] is True
    assert result["flagged"] == []


def test_the_audit_finds_a_new_child_past_the_first_page_of_a_large_parent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = _send_back_fixture(SEND_BACK_FIXTURES[1])
    item = data["items"][0]
    newest = {
        "ref": "Q-900", "sharedNum": 900, "kind": "task", "title": "Notes v2",
        "status": "todo", "assignee": None, "acceptance": {"status": "none"},
        "blockers": {"open": None, "resolved": []}, "followUp": None,
        "children": {"open": 0, "done": 0},
        "createdAt": "2026-11-18T09:00:00.000Z", "updatedAt": "2026-11-18T09:00:00.000Z",
    }
    older = [{"ref": f"Q-{810 + index}", "status": "done"} for index in range(50)]
    rows = [item, newest]
    by_ref = {row["ref"]: row for row in rows}

    class FakeClient:
        url = "http://127.0.0.1:4180"

        def query(self, name: str, payload: dict) -> dict:
            if name != "listItems":
                return {"item": by_ref[payload["ref"]]}
            if payload.get("parent"):
                if payload.get("cursor") is None:
                    return {"items": older, "nextCursor": "50"}
                return {"items": [newest], "nextCursor": None}
            return {"items": rows, "nextCursor": None}

    _patch_team(monkeypatch, data["live"], data["tasks"])
    result = jotarium.audit(FakeClient())
    assert result["ok"] is True


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


def test_a_parent_with_open_children_is_a_container_not_unattended() -> None:
    item = {"ref": "Q-500", "status": "in_progress", "assignee": None,
            "children": {"open": 2, "done": 1},
            "blockers": {"open": None, "resolved": []}}
    flagged, covered = jotarium.find_unattended([item], [], [])
    assert flagged == []
    assert covered == [{"ref": "Q-500", "by": "2 open children"}]


def test_a_parent_with_only_finished_children_is_still_flagged() -> None:
    item = {"ref": "Q-501", "status": "in_progress", "assignee": None,
            "children": {"open": 0, "done": 3},
            "blockers": {"open": None, "resolved": []}}
    flagged, _ = jotarium.find_unattended([item], [], [])
    assert [row["ref"] for row in flagged] == ["Q-501"]


def test_in_progress_items_keep_the_child_counts_from_the_list_summary() -> None:
    summary = {"ref": "Q-502", "status": "in_progress", "children": {"open": 4, "done": 2}}
    detail = {"ref": "Q-502", "status": "in_progress", "assignee": None,
              "blockers": {"open": None, "resolved": []}}

    def query(name: str, payload: dict) -> dict:
        if name == "listItems":
            return {"items": [summary], "nextCursor": None}
        return {"item": detail}

    items = jotarium.in_progress_items(query)
    assert items == [{**detail, "children": {"open": 4, "done": 2}}]
