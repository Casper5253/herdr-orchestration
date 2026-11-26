"""Q-1014 / I-132: hx next always hands out work that does not need the creator."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import pytest

from hx import jotarium, next as next_mod
from hx import __main__ as cli

FIXTURE = Path(__file__).parent / "fixtures" / "hx-next-jotarium.json"
NOW = dt.datetime(2026, 11, 26, 12, 0, tzinfo=dt.timezone.utc)
POLICY = {
    "next": {
        "heavy_cap": 1,
        "heavy_cap_when_memory_ok": 2,
        "free_memory_gb_threshold": 6.0,
        "answer_window_days": 14,
        "postpone_words": ["after", "later", "park", "not now", "wait", "keep", "no"],
    }
}


def _data() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _summary(item: dict[str, Any]) -> dict[str, Any]:
    acceptance = item.get("acceptance")
    return {
        "ref": item.get("ref"),
        "kind": item.get("kind"),
        "title": item.get("title"),
        "status": item.get("status"),
        "assignee": item.get("assignee"),
        "children": item.get("children"),
        "acceptance": acceptance.get("status") if isinstance(acceptance, dict) else acceptance,
        "questType": item.get("questType"),
    }


class FakeJotarium:
    """The Jotarium query surface, served from the fixture. Never the live server."""

    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data

    def __call__(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        if name == "recentActivity":
            since = str(payload.get("since") or "")
            before = payload.get("before")
            rows = [
                entry
                for entry in self.data["activity"]
                if str(entry["activity"]["createdAt"]) >= since
                and (before is None or str(entry["activity"]["createdAt"]) < before)
            ]
            rows.sort(key=lambda entry: entry["activity"]["createdAt"], reverse=True)
            return {"entries": rows[: payload.get("limit", 50)]}
        if name == "getItem":
            ref = str(payload.get("ref"))
            item = self.data["items"].get(ref)
            if item is None:
                raise AssertionError(f"unexpected getItem {ref}")
            return {"item": item, "comments": item.get("comments", [])}
        if name == "listItems":
            statuses = payload.get("status")
            rows = [
                _summary(item)
                for item in self.data["items"].values()
                if not statuses or item.get("status") in statuses
            ]
            return {"items": rows, "nextCursor": None}
        if name == "listIdeas":
            statuses = payload.get("status")
            ideas = [
                idea
                for idea in self.data["ideas"]
                if not statuses or idea.get("status") == statuses
            ]
            return {"ideas": ideas}
        raise AssertionError(f"unexpected query {name}")


class FakeLedger:
    def __init__(self, root: Path, tasks: list[dict[str, Any]]) -> None:
        self.repo_root = Path(root)
        self._tasks = list(tasks)

    def list_tasks(self, status: str | None = None) -> list[dict[str, Any]]:
        if status:
            return [task for task in self._tasks if task.get("status") == status]
        return list(self._tasks)


def _checkpoint(root: Path, task_id: str, next_action: str) -> str:
    folder = Path(root) / ".hx" / "checkpoints"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{task_id}.md"
    path.write_text(
        f"# Checkpoint: {task_id}\n\n## Next action\n{next_action}\n", encoding="utf-8"
    )
    return str(path)


def _sources(
    tmp_path: Path,
    *,
    tasks: list[dict[str, Any]] | None = None,
    live: list[dict[str, Any]] | None = None,
    free_gb: float | None = 32.0,
    policy: dict[str, Any] | None = None,
) -> next_mod.Sources:
    return next_mod.Sources(
        FakeJotarium(_data()),
        FakeLedger(tmp_path, tasks or []),
        policy=policy or POLICY,
        now=NOW,
        free_gb=free_gb,
        live=live or [],
    )


def test_creator_answer_is_top_of_ladder(tmp_path: Path) -> None:
    sources = _sources(
        tmp_path,
        tasks=[{"id": "q2-work", "status": "in_progress", "jotarium": "Q-2"}],
    )

    units = sources.creator_answers()

    assert [unit["ref"] for unit in units] == ["Q-1"]
    assert units[0]["source"] == "creator-answer"
    assert units[0]["weight"] == "light"
    assert sources.next_unit()["ref"] == "Q-1"


def test_creator_answer_always_starts_light(tmp_path: Path) -> None:
    units = {unit["ref"]: unit for unit in _sources(tmp_path).creator_answers()}

    assert units["Q-1"]["weight"] == "light"
    assert units["Q-2"]["weight"] == "light"


def test_answered_decide_card_starts_light(tmp_path: Path) -> None:
    data = _data()
    data["items"]["Q-852"]["comments"] = []
    data["items"]["Q-852"]["activity"] = [
        {
            "id": "a-852-resolve",
            "actor": "human",
            "command": "resolveBlocker",
            "createdAt": "2026-11-24T09:00:00.000Z",
        }
    ]
    sources = next_mod.Sources(
        FakeJotarium(data),
        FakeLedger(tmp_path, []),
        policy=POLICY,
        now=NOW,
        free_gb=32.0,
        live=[],
    )

    units = {unit["ref"]: unit for unit in sources.creator_answers()}

    assert units["Q-852"]["weight"] == "light"


def test_postponed_answer_is_not_work(tmp_path: Path) -> None:
    data = _data()
    data["items"]["Q-851"]["comments"] = []
    data["items"]["Q-851"]["activity"] = [
        {
            "id": "a-851-resolve",
            "actor": "human",
            "command": "resolveBlocker",
            "createdAt": "2026-11-24T10:00:00.000Z",
        }
    ]
    sources = next_mod.Sources(
        FakeJotarium(data),
        FakeLedger(tmp_path, []),
        policy=POLICY,
        now=NOW,
        free_gb=32.0,
        live=[],
    )

    refs = {unit["ref"] for unit in sources.creator_answers()}

    assert "Q-851" not in refs


def test_answer_that_says_do_now_is_not_postponed(tmp_path: Path) -> None:
    data = _data()
    data["items"]["Q-853"] = {
        "ref": "Q-853",
        "kind": "task",
        "status": "backlog",
        "title": "Adopt the shared parser in this release",
        "assignee": None,
        "acceptance": {"status": "none", "revision": 1},
        "children": {"open": 0, "done": 0},
        "blockers": {
            "open": None,
            "resolved": [
                {
                    "request": {
                        "kind": "decide",
                        "options": [
                            {"key": "keep", "label": "Keep it and build now"},
                            {"key": "after", "label": "Park it until after 1.0"},
                        ],
                    },
                    "revision": 1,
                    "resolvedAt": "2026-11-24T12:00:00.000Z",
                    "action": "choose",
                    "choice": "keep",
                }
            ],
        },
        "comments": [],
        "activity": [],
    }
    data["activity"].append(
        {
            "activity": {
                "id": "a-853-resolve",
                "actor": "human",
                "command": "resolveBlocker",
                "createdAt": "2026-11-24T12:00:00.000Z",
            },
            "item": {"ref": "Q-853", "title": "Adopt the shared parser in this release"},
        }
    )
    sources = next_mod.Sources(
        FakeJotarium(data),
        FakeLedger(tmp_path, []),
        policy=POLICY,
        now=NOW,
        free_gb=32.0,
        live=[],
    )

    refs = {unit["ref"] for unit in sources.creator_answers()}

    assert "Q-853" in refs


def test_answer_the_lead_already_acted_on_is_not_work(tmp_path: Path) -> None:
    refs = {unit["ref"] for unit in _sources(tmp_path).creator_answers()}

    assert "Q-852" not in refs


def test_sent_back_card_is_work_when_nothing_holds_it(tmp_path: Path) -> None:
    refs = {unit["ref"] for unit in _sources(tmp_path).creator_answers()}

    assert refs == {"Q-1", "Q-2"}


def test_creator_answer_ignores_follow_up_created_inside_resolve(tmp_path: Path) -> None:
    refs = {unit["ref"] for unit in _sources(tmp_path).creator_answers()}

    assert "Q-9" not in refs


def test_open_item_skips_pending_owned_and_parents(tmp_path: Path) -> None:
    sources = _sources(
        tmp_path,
        tasks=[{"id": "q5-work", "status": "in_progress", "jotarium": "Q-5"}],
        live=[{"name": "dev-claude", "role": "claude"}],
    )

    refs = {unit["ref"] for unit in sources.open_items()}

    assert "Q-3" in refs
    assert not refs & {"Q-4", "Q-5", "Q-6", "Q-7"}


def test_open_item_with_a_pending_request_is_not_work(tmp_path: Path) -> None:
    refs = {unit["ref"] for unit in _sources(tmp_path).open_items()}

    assert "Q-4" not in refs


def test_postponed_answer_does_not_resurface_as_open_item(tmp_path: Path) -> None:
    data = _data()
    data["items"]["Q-851"]["status"] = "todo"
    data["items"]["Q-851"]["comments"] = []
    sources = next_mod.Sources(
        FakeJotarium(data),
        FakeLedger(tmp_path, []),
        policy=POLICY,
        now=NOW,
        free_gb=32.0,
        live=[],
    )

    assert "Q-851" not in {unit["ref"] for unit in sources.open_items()}


def test_handled_answer_does_not_resurface_as_open_item(tmp_path: Path) -> None:
    data = _data()
    data["items"]["Q-852"]["status"] = "todo"
    sources = next_mod.Sources(
        FakeJotarium(data),
        FakeLedger(tmp_path, []),
        policy=POLICY,
        now=NOW,
        free_gb=32.0,
        live=[],
    )

    assert "Q-852" not in {unit["ref"] for unit in sources.open_items()}


@pytest.mark.parametrize("ref", ["Q-851", "Q-852"])
def test_old_suppressed_answer_stays_out_until_the_human_reopens_it(
    tmp_path: Path, ref: str
) -> None:
    data = _data()
    item = data["items"][ref]
    item["status"] = "todo"
    if ref == "Q-851":
        item["comments"] = []
        item["activity"] = [event for event in item["activity"] if event["actor"] == "human"]
    now = NOW + dt.timedelta(days=30)

    def query(name: str, payload: dict[str, Any]) -> dict[str, Any]:
        detail = FakeJotarium(data)(name, payload)
        if name == "getItem" and payload["ref"] == ref:
            detail["item"] = {key: value for key, value in item.items() if key != "activity"}
            detail["activity"] = item["activity"]
        return detail

    def open_refs() -> set[str]:
        sources = next_mod.Sources(
            query, FakeLedger(tmp_path, []), policy=POLICY, now=now,
        )
        return {unit["ref"] for unit in sources.open_items()}

    assert ref not in open_refs()
    item["activity"].append({
        "actor": "human", "command": "setStatus", "createdAt": now.isoformat(),
    })
    assert ref in open_refs()


@pytest.mark.parametrize(
    ("key", "label"),
    [
        ("no", "Do not build"),
        ("decline", "Do not build"),
        ("decline", "Skip this"),
        ("decline", "Drop this"),
        ("decline", "Not now, revisit after release"),
    ],
)
def test_declining_answer_is_suppressed(tmp_path: Path, key: str, label: str) -> None:
    data = _data()
    item = data["items"]["Q-851"]
    item["status"] = "todo"
    item["comments"] = []
    item["activity"] = [event for event in item["activity"] if event["actor"] == "human"]
    card = item["blockers"]["resolved"][0]
    card["choice"] = key
    card["request"]["options"] = [{"key": key, "label": label}]
    sources = next_mod.Sources(
        FakeJotarium(data), FakeLedger(tmp_path, []), policy=POLICY, now=NOW,
    )

    assert "Q-851" not in {unit["ref"] for unit in sources.creator_answers()}
    assert "Q-851" not in {unit["ref"] for unit in sources.open_items()}


def test_open_item_title_filters_explicit_creator_actions(tmp_path: Path) -> None:
    data = _data()
    titles = {
        "Q-26": "Independent review and two-model comparison; Casper edits",
        "Q-27": "Creator decides the release date",
        "Q-28": "Compare what Casper edits in each draft",
        "Q-29": "Review the Creator checks table",
    }
    for ref, title in titles.items():
        data["items"][ref] = {"ref": ref, "title": title, "status": "todo"}
    policy = {"next": {**POLICY["next"], "creator_names": ["Casper", "creator"]}}
    sources = next_mod.Sources(
        FakeJotarium(data), FakeLedger(tmp_path, []), policy=policy, now=NOW,
    )

    refs = {unit["ref"] for unit in sources.open_items()}
    assert refs.intersection(titles) == {"Q-28", "Q-29"}


def test_follow_up_from_a_done_task_checkpoint(tmp_path: Path) -> None:
    sources = _sources(
        tmp_path,
        tasks=[
            {
                "id": "old-task",
                "status": "done",
                "effort": "high",
                "updated": "2026-11-24T00:00:00Z",
                "checkpoint": _checkpoint(tmp_path, "old-task", "Ship the deferred migration"),
            }
        ],
    )

    units = sources.follow_ups()

    assert [unit["ref"] for unit in units] == ["old-task"]
    assert units[0]["source"] == "follow-up"
    assert units[0]["weight"] == "heavy"
    assert units[0]["title"] == "Ship the deferred migration"


def test_follow_up_skipped_when_an_open_task_owns_it(tmp_path: Path) -> None:
    sources = _sources(
        tmp_path,
        tasks=[
            {
                "id": "old-task",
                "status": "done",
                "updated": "2026-11-24T00:00:00Z",
                "checkpoint": _checkpoint(tmp_path, "old-task", "Ship it"),
            },
            {
                "id": "next-work",
                "status": "open",
                "depends": ["old-task"],
                "updated": "2026-11-25T00:00:00Z",
            },
        ],
    )

    assert sources.follow_ups() == []


def test_follow_up_ignores_placeholder_next_action(tmp_path: Path) -> None:
    sources = _sources(
        tmp_path,
        tasks=[
            {
                "id": "old-task",
                "status": "done",
                "updated": "2026-11-24T00:00:00Z",
                "checkpoint": _checkpoint(tmp_path, "old-task", "none"),
            }
        ],
    )

    assert sources.follow_ups() == []


@pytest.mark.parametrize(
    "next_action",
    [
        "Ask the creator whether the migration ships today",
        "Confirm with the creator before merging",
        "Wait for the creator to answer",
    ],
)
def test_follow_up_that_needs_the_creator_is_skipped(
    tmp_path: Path, next_action: str
) -> None:
    sources = _sources(
        tmp_path,
        tasks=[
            {
                "id": "old-task",
                "status": "done",
                "updated": "2026-11-24T00:00:00Z",
                "checkpoint": _checkpoint(tmp_path, "old-task", next_action),
            }
        ],
    )

    assert sources.follow_ups() == []


def test_idea_without_a_linked_item_is_light(tmp_path: Path) -> None:
    units = _sources(tmp_path).ideas()

    assert [unit["ref"] for unit in units] == ["I-1"]
    assert units[0]["weight"] == "light"
    assert units[0]["source"] == "idea"
    assert units[0]["worker_hint"] == "claude"


def test_improvement_audits_never_empty_for_both_weights(tmp_path: Path) -> None:
    units = _sources(tmp_path).audits()

    assert {unit["weight"] for unit in units} >= {"heavy", "light"}
    assert all(unit["source"] == "audit" for unit in units)


def test_housekeeping_never_empty_for_both_weights(tmp_path: Path) -> None:
    units = _sources(tmp_path).housekeeping()

    assert {unit["weight"] for unit in units} >= {"heavy", "light"}
    assert all(unit["source"] == "housekeeping" for unit in units)


def _fallback_sources(tmp_path: Path, moment: dt.datetime) -> next_mod.Sources:
    def failing(_name: str, _payload: dict[str, Any]) -> dict[str, Any]:
        raise jotarium.JotariumError("Jotarium is not reachable")

    return next_mod.Sources(
        failing,
        FakeLedger(tmp_path, []),
        policy=POLICY,
        now=moment,
        free_gb=32.0,
        live=[],
    )


def test_audit_and_housekeeping_alternate_by_day(tmp_path: Path) -> None:
    today = _fallback_sources(tmp_path, NOW).next_unit()["source"]
    tomorrow = _fallback_sources(tmp_path, NOW + dt.timedelta(days=1)).next_unit()["source"]

    assert {today, tomorrow} == {"audit", "housekeeping"}


@pytest.mark.parametrize("weight", ["heavy", "light"])
def test_fallback_rotation_returns_every_entry_of_the_requested_weight(
    tmp_path: Path, weight: str
) -> None:
    selected = set()
    for day in range(12):
        sources = _fallback_sources(tmp_path, NOW + dt.timedelta(days=day))
        unit = sources.next_unit(weight=weight)
        selected.add((unit["source"], unit["ref"]))

    assert selected == {
        (source, entry["id"])
        for source, entries in [("audit", next_mod.AUDITS), ("housekeeping", next_mod.HOUSEKEEPING)]
        for entry in entries
        if entry["weight"] == weight
    }


def test_unreachable_jotarium_falls_through_to_an_audit(tmp_path: Path) -> None:
    def failing(_name: str, _payload: dict[str, Any]) -> dict[str, Any]:
        raise jotarium.JotariumError("Jotarium is not reachable")

    sources = next_mod.Sources(
        failing,
        FakeLedger(tmp_path, []),
        policy=POLICY,
        now=NOW,
        free_gb=32.0,
    )

    unit = sources.next_unit(weight="heavy")

    assert unit["source"] in {"audit", "housekeeping"}
    assert unit["weight"] == "heavy"
    assert sources.errors


def test_at_the_heavy_cap_the_answer_is_light(tmp_path: Path) -> None:
    sources = _sources(tmp_path, free_gb=1.0)

    assert sources.heavy_cap() == 1
    assert sources.next_unit(running_heavy=1)["weight"] == "light"


def test_enough_free_memory_allows_a_second_heavy_unit(tmp_path: Path) -> None:
    sources = _sources(tmp_path, free_gb=32.0)

    assert sources.heavy_cap() == 2
    unit = sources.next_unit(weight="heavy", running_heavy=1)
    assert unit["weight"] == "heavy"


def test_missing_memory_reading_keeps_the_conservative_cap(tmp_path: Path) -> None:
    sources = _sources(tmp_path, free_gb=None)

    assert sources.heavy_cap() == 1


def test_asking_for_light_walks_the_ladder_for_light(tmp_path: Path) -> None:
    sources = _sources(
        tmp_path,
        tasks=[
            {"id": "q1-work", "status": "in_progress", "jotarium": "Q-1"},
            {"id": "q2-work", "status": "in_progress", "jotarium": "Q-2"},
        ],
    )

    unit = sources.next_unit(weight="light")

    assert unit["weight"] == "light"
    assert unit["ref"] == "Q-3"


def test_next_command_prints_a_unit_as_json(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    rc = next_mod.next_command(
        as_json=True,
        query=FakeJotarium(_data()),
        ledger=FakeLedger(tmp_path, []),
        policy=POLICY,
        now=NOW,
        free_gb=32.0,
        live=[],
    )

    assert rc == 0
    unit = json.loads(capsys.readouterr().out)
    assert set(unit) == {"source", "ref", "title", "weight", "worker_hint", "why"}
    assert unit["ref"] == "Q-2"


def test_next_command_prints_readable_text(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    rc = next_mod.next_command(
        query=FakeJotarium(_data()),
        ledger=FakeLedger(tmp_path, []),
        policy=POLICY,
        now=NOW,
        free_gb=32.0,
        live=[],
    )

    assert rc == 0
    out = capsys.readouterr().out
    assert "[light] creator-answer Q-2" in out
    assert "worker:" in out
    assert "why:" in out


def test_cli_dispatches_next(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, Any] = {}

    def fake(**kwargs: Any) -> int:
        calls.update(kwargs)
        return 0

    monkeypatch.setattr(next_mod, "next_command", fake)

    assert cli.main(["next", "--weight", "light", "--running-heavy", "2", "--json"]) == 0
    assert calls == {"weight": "light", "running_heavy": 2, "as_json": True}


def test_help_lists_next(capsys: pytest.CaptureFixture) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])

    assert exc.value.code == 0
    assert "next" in capsys.readouterr().out
