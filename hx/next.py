"""hx.next: the next unit of work that does not need the creator.

``hx next`` walks a fixed ladder of sources and returns the first unit the team
can take on its own. The last two rungs rotate a fixed list, so the command
never comes back empty.

A unit is ``{source, ref, title, weight, worker_hint, why}``. ``weight`` is
heavy (builds, test suites, implementation) or light (research, reviews,
writing, small fixes). ``worker_hint`` names the worker family best suited.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable, TypedDict

from hx import jotarium, ledger as ledger_mod, usage

LADDER = (
    ("creator-answer", "creator_answers"),
    ("open-item", "open_items"),
    ("follow-up", "follow_ups"),
    ("idea", "ideas"),
    ("audit", "audits"),
    ("housekeeping", "housekeeping"),
)

PAGE_SIZE = 50
MAX_ITEM_PAGES = 40
RESOLVE_COMMANDS = frozenset({"resolveRequest", "resolveBlocker"})
ACCEPT_ACTIONS = frozenset({"accept", "choose", "confirm"})
STATUS_COMMANDS = frozenset({"setStatus"})
EMPTY_NEXT = frozenset({"", "none", "n/a", "na", "no follow-up", "nothing"})
LIGHT_QUEST_TYPES = frozenset({"research", "habit", "quick"})
LIGHT_WORDS = (
    "review",
    "research",
    "investigate",
    "audit",
    "document",
    "docs",
    "write",
    "notes",
    "rename",
    "typo",
    "cleanup",
    "small fix",
)

AUDITS = (
    {
        "id": "repeat-mistakes",
        "title": "Turn recurring mistakes from recent sessions into automatic checks",
        "weight": "heavy",
        "worker_hint": "codex",
        "why": "audits never empty: past sessions keep repeating the same mistakes",
    },
    {
        "id": "flaky-tests",
        "title": "Hunt flaky tests and pin the failure",
        "weight": "heavy",
        "worker_hint": "codex",
        "why": "audits never empty: a flaky suite wastes every heavy task",
    },
    {
        "id": "stale-skills",
        "title": "Audit skill files for stale or duplicated instructions",
        "weight": "light",
        "worker_hint": "claude",
        "why": "audits never empty: skills drift as the team changes",
    },
    {
        "id": "docs-drift",
        "title": "Sweep docs and briefs for drift from the code",
        "weight": "light",
        "worker_hint": "claude",
        "why": "audits never empty: docs lag behind merged code",
    },
)

HOUSEKEEPING = (
    {
        "id": "agentmem-backlog",
        "title": "Fold the agentmem consolidation backlog",
        "weight": "heavy",
        "worker_hint": "claude",
        "why": "housekeeping never empty: captured sessions wait to be consolidated",
    },
    {
        "id": "worktrees",
        "title": "Prune stale git worktrees and scratch output",
        "weight": "light",
        "worker_hint": "opencode",
        "why": "housekeeping never empty: worktrees and scratch dirs accumulate",
    },
    {
        "id": "old-briefs",
        "title": "Archive old briefs and handoff files",
        "weight": "light",
        "worker_hint": "opencode",
        "why": "housekeeping never empty: finished briefs pile up",
    },
)

_NEXT_ACTION_RE = re.compile(r"^## Next action\n(.*?)\n(?:## |\Z)", re.MULTILINE | re.DOTALL)
_CREATOR_NEED_RE = re.compile(
    r"\b(?:ask|confirm|check|wait|speak|talk|discuss|consult)\b[^.\n]{0,60}\bcreator\b"
    r"|\bcreator\b[^.\n]{0,40}\b(?:confirm|decide|answer|choose|approve)\b",
    re.IGNORECASE,
)
_DECLINE_RE = re.compile(r"^(?:no|do not|not now|skip|drop)\b")
_PHASE_RE = re.compile(r"^\s*phase\s+(\d+)\b", re.IGNORECASE)
_AFTER_ISO_RE = re.compile(r"\bafter\s+(\d{4})-(\d{1,2})-(\d{1,2})\b", re.IGNORECASE)
_AFTER_DAY_RE = re.compile(
    r"\bafter\s+(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})\b\.?(?:\s+(\d{4}))?",
    re.IGNORECASE,
)
_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}
_UNSET = object()


class Unit(TypedDict):
    """One piece of work the ladder can hand out."""

    source: str
    ref: str
    title: str
    weight: str
    worker_hint: str
    why: str


class NextError(RuntimeError):
    """No unit could be produced at all."""


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _instant(value: Any) -> dt.datetime | None:
    """An ISO timestamp as an aware UTC instant, or None when unreadable."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def _int(value: Any, fallback: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else fallback


def _float(value: Any, fallback: float) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return fallback


def _truthy(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def _ref_number(ref: Any) -> int:
    match = re.search(r"(\d+)$", str(ref or ""))
    return int(match.group(1)) if match else 0


def _acceptance_status(value: Any) -> str:
    if isinstance(value, dict):
        status = value.get("status")
        return str(status) if status else "none"
    return str(value) if value else "none"


def _unit(
    source: str,
    ref: str,
    title: str,
    weight: str,
    worker_hint: str,
    why: str,
) -> Unit:
    return {
        "source": source,
        "ref": ref,
        "title": title,
        "weight": weight,
        "worker_hint": worker_hint,
        "why": why,
    }


def _worker_for(source: str, weight: str) -> str:
    if source in {"idea", "creator-answer"}:
        return "claude"
    if source == "housekeeping":
        return "opencode"
    if source == "follow-up":
        return "codex" if weight == "heavy" else "opencode"
    if source == "audit":
        return "codex" if weight == "heavy" else "claude"
    return "codex" if weight == "heavy" else "claude"


def _item_weight(item: dict[str, Any]) -> str:
    """Heavy is the default; only explicit light signals flip it."""
    if str(item.get("questType") or "").lower() in LIGHT_QUEST_TYPES:
        return "light"
    if str(item.get("duration") or "").lower() == "quick":
        return "light"
    if str(item.get("complexity") or "").lower() == "simple":
        return "light"
    if str(item.get("kind") or "").lower() in {"feature", "project"}:
        return "heavy"
    title = str(item.get("title") or "").lower()
    if any(word in title for word in LIGHT_WORDS):
        return "light"
    return "heavy"


def _phase_number(title: Any) -> int | None:
    match = _PHASE_RE.match(str(title or ""))
    return int(match.group(1)) if match else None


def _after_date(text: Any, year: int) -> dt.date | None:
    """The date a title gates on, from "after 27 Nov" or "after 2026-11-27"."""
    value = str(text or "")
    match = _AFTER_ISO_RE.search(value)
    if match:
        try:
            return dt.date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            return None
    match = _AFTER_DAY_RE.search(value)
    if not match:
        return None
    month = _MONTHS.get(match.group(2).lower())
    if month is None:
        return None
    try:
        return dt.date(int(match.group(3) or year), month, int(match.group(1)))
    except ValueError:
        return None


def _follow_up_weight(task: dict[str, Any]) -> str:
    effort = str(task.get("effort") or "").lower()
    return "light" if effort in {"low", "minimal", "light", "none"} else "heavy"


def _item_order(item: dict[str, Any]) -> tuple[int, int]:
    return (0 if item.get("status") == "in_progress" else 1, _ref_number(item.get("ref")))


def _idea_title(idea: dict[str, Any]) -> str:
    text = " ".join(str(idea.get("text") or "").split())
    return text[:157] + "..." if len(text) > 160 else text


def _answer_verdict(activity: dict[str, Any], item: dict[str, Any]) -> str | None:
    """How the creator answered this card, or None while it still waits."""
    blockers = item.get("blockers")
    if isinstance(blockers, dict) and blockers.get("open") is not None:
        return None
    command = activity.get("command")
    acceptance = item.get("acceptance")
    status = _acceptance_status(acceptance)
    if command == "resolveRequest":
        if status == "accepted":
            choice = acceptance.get("choice") if isinstance(acceptance, dict) else None
            return f"chose {choice}" if choice else "accepted the card"
        if status == "rejected":
            return "sent the card back"
        return None
    if command == "resolveBlocker":
        resolved = blockers.get("resolved") if isinstance(blockers, dict) else None
        if not isinstance(resolved, list) or not resolved:
            return None
        last = resolved[-1]
        if not isinstance(last, dict) or last.get("action") not in ACCEPT_ACTIONS:
            return None
        choice = last.get("choice")
        return f"chose {choice}" if choice else f"answered the card ({last.get('action')})"
    return None


def _chosen_card(activity: dict[str, Any], item: dict[str, Any]) -> dict[str, Any] | None:
    """The blocker card the creator chose on, or None for a request answer."""
    if activity.get("command") != "resolveBlocker":
        return None
    blockers = item.get("blockers")
    resolved = blockers.get("resolved") if isinstance(blockers, dict) else None
    if not isinstance(resolved, list) or not resolved:
        return None
    last = resolved[-1]
    if not isinstance(last, dict) or last.get("action") not in ACCEPT_ACTIONS:
        return None
    return last


def _has_word(text: str, word: str) -> bool:
    return re.search(r"(?<!\w)" + re.escape(word) + r"(?!\w)", text) is not None


def _says_do_now(label: str) -> bool:
    """True when a label asks to build or do it now rather than postpone."""
    lowered = label.lower()
    if _has_word(lowered, "build") or _has_word(lowered, "do"):
        return True
    without_not_now = re.sub(r"(?<!\w)not now(?!\w)", " ", lowered)
    return _has_word(without_not_now, "now")


def _phrase_set(words: list[Any]) -> set[str]:
    return {
        " ".join(str(raw).lower().split())
        for raw in words
        if isinstance(raw, str) and raw.strip()
    }


def _chosen_option(
    activity: dict[str, Any], item: dict[str, Any]
) -> dict[str, Any] | None:
    """The option dict the creator chose, when the answer names one."""
    card = _chosen_card(activity, item)
    if card is None and activity.get("command") == "resolveRequest":
        acceptance = item.get("acceptance")
        if isinstance(acceptance, dict):
            card = acceptance
    if card is None:
        return None
    choice = card.get("choice")
    request = card.get("request")
    options = request.get("options") if isinstance(request, dict) else None
    for option in options if isinstance(options, list) else []:
        if isinstance(option, dict) and option.get("key") == choice:
            return option
    return None


def _postpones(
    activity: dict[str, Any], item: dict[str, Any], words: list[Any]
) -> bool:
    """Recognize explicit declines before interpreting positive action words."""
    acceptance = item.get("acceptance")
    key = str(acceptance.get("choice") or "") if isinstance(acceptance, dict) else ""
    card = _chosen_card(activity, item)
    if card is not None:
        key = str(card.get("choice") or "") or key
    option = _chosen_option(activity, item)
    label = str(option.get("label") or "") if option is not None else ""
    key = " ".join(key.lower().split())
    label = " ".join(label.lower().split())
    if _DECLINE_RE.match(re.sub(r"[-_]+", " ", key)) or _DECLINE_RE.match(label):
        return True
    if label and _says_do_now(label):
        return False
    phrases = _phrase_set(words)
    return (key in phrases) or (label in phrases)


def _acted_after(activity: dict[str, Any], item: dict[str, Any]) -> bool:
    """True when an agent comment or status change followed the creator's answer."""
    answered = _instant(activity.get("createdAt"))
    if answered is None:
        return False
    comments = item.get("comments")
    for comment in comments if isinstance(comments, list) else []:
        if not isinstance(comment, dict):
            continue
        if not str(comment.get("author") or "").lower().startswith("agent"):
            continue
        at = _instant(comment.get("createdAt"))
        if at is not None and at > answered:
            return True
    events = item.get("activity")
    for event in events if isinstance(events, list) else []:
        if not isinstance(event, dict):
            continue
        if not str(event.get("actor") or "").lower().startswith("agent"):
            continue
        if event.get("command") not in STATUS_COMMANDS:
            continue
        at = _instant(event.get("createdAt"))
        if at is not None and at > answered:
            return True
    return False


def _answer_is_suppressed(
    activity: dict[str, Any], item: dict[str, Any], postpone_words: list[Any]
) -> bool:
    return _acted_after(activity, item) or _postpones(activity, item, postpone_words)


def _latest_human_activity(
    item: dict[str, Any], answer: dict[str, Any] | None = None
) -> dict[str, Any]:
    latest = answer or {}
    latest_at = _instant(latest.get("createdAt"))
    for field, actor_field in (("activity", "actor"), ("comments", "author")):
        events = item.get(field)
        for event in events if isinstance(events, list) else []:
            if not isinstance(event, dict) or event.get(actor_field) != "human":
                continue
            at = _instant(event.get("createdAt"))
            if at is not None and (latest_at is None or at > latest_at):
                latest = event
                latest_at = at
    return latest


def _item_with_history(detail: Any) -> dict[str, Any] | None:
    item = detail.get("item") if isinstance(detail, dict) else None
    if not isinstance(item, dict):
        return None
    history = {
        field: detail[field]
        for field in ("activity", "comments")
        if isinstance(detail.get(field), list)
    }
    return {**item, **history}


def _checkpoint_next_action(task: dict[str, Any], repo_root: Path) -> str:
    raw = task.get("checkpoint")
    path = (
        Path(raw)
        if isinstance(raw, str) and raw
        else Path(repo_root) / ".hx" / "checkpoints" / f"{task.get('id')}.md"
    )
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    match = _NEXT_ACTION_RE.search(text)
    if match is None:
        return ""
    value = match.group(1).strip()
    return "" if value.lower() in EMPTY_NEXT else value


def _needs_creator(text: str) -> bool:
    """True when a checkpoint next action asks the creator to decide."""
    return _CREATOR_NEED_RE.search(text) is not None


def _follow_up_covered(task: dict[str, Any], open_tasks: list[dict[str, Any]]) -> bool:
    task_id = str(task.get("id") or "")
    ref = jotarium.canonical_ref(task.get("jotarium"))
    for other in open_tasks:
        other_id = str(other.get("id") or "")
        if task_id and (other_id == task_id or other_id.startswith(task_id + "-")):
            return True
        if task_id and task_id in (other.get("depends") or []):
            return True
        if task_id and task_id in str(other.get("title") or ""):
            return True
        if ref and ref in jotarium.refs_for_task(other):
            return True
    return False


def _rotate(
    source: str,
    entries: tuple[dict[str, str], ...],
    now: dt.datetime,
) -> list[Unit]:
    # The fallback sources take turns, so each source advances every two days.
    start = (now.toordinal() // 2) % len(entries) if entries else 0
    ordered = list(entries[start:]) + list(entries[:start])
    return [
        _unit(
            source,
            entry["id"],
            entry["title"],
            entry["weight"],
            entry["worker_hint"],
            entry["why"],
        )
        for entry in ordered
    ]


class Sources:
    """The six-rung ladder, with every outside reader injected for tests."""

    def __init__(
        self,
        query: Callable[[str, dict[str, Any]], dict[str, Any]],
        ledger: Any = None,
        *,
        policy: dict[str, Any] | None = None,
        now: dt.datetime | None = None,
        free_gb: float | None | object = _UNSET,
        live: list[dict[str, Any]] | None = None,
    ) -> None:
        self.query = query
        self.ledger = ledger
        self.policy = policy or {}
        self.now = now or _utcnow()
        self._free_gb = free_gb
        self.live = list(live or [])
        self.errors: list[str] = []
        self._open_refs: set[str] | None = None
        self._siblings: dict[str, list[dict[str, Any]]] = {}
        config = self.policy.get("next") or {}
        self._postpone_words = config.get("postpone_words") or []
        names = _phrase_set(config.get("creator_names") or []) | {"creator"}
        alternatives = "|".join(re.escape(name) for name in sorted(names))
        self._creator_action = re.compile(
            rf"(?:^|[;:!?\.\n]|\s[-–—]\s)\s*(?:{alternatives})"
            r"\s+(?:edits|decides|reviews|checks|picks)\b",
            re.IGNORECASE,
        )

    def safe_units(self, method: str) -> list[Unit]:
        try:
            return list(getattr(self, method)())
        except Exception as exc:
            message = f"{method}: {exc}"
            if message not in self.errors:
                self.errors.append(message)
            return []

    def _ladder(self) -> tuple[tuple[str, str], ...]:
        """The ladder for today, with audits and housekeeping alternating first."""
        if self.now.toordinal() % 2 == 0:
            return LADDER
        rungs = list(LADDER)
        audit = next(i for i, (name, _method) in enumerate(rungs) if name == "audit")
        house = next(
            i for i, (name, _method) in enumerate(rungs) if name == "housekeeping"
        )
        rungs[audit], rungs[house] = rungs[house], rungs[audit]
        return tuple(rungs)

    def next_unit(self, weight: str | None = None, running_heavy: int = 0, worker: str | None = None, exclude_refs: set[str] | None = None) -> Unit:
        at_cap = running_heavy >= self.heavy_cap()
        excluded = set(exclude_refs or set())
        for _source, method in self._ladder():
            units = self.safe_units(method)
            if at_cap:
                units = [unit for unit in units if unit["weight"] == "light"]
            elif weight:
                units = [unit for unit in units if unit["weight"] == weight]
            else:
                units = [unit for unit in units if unit["weight"] == "heavy"] + [
                    unit for unit in units if unit["weight"] == "light"
                ]
            if excluded:
                units = [unit for unit in units if unit["ref"] not in excluded]
            if worker:
                units = sorted(units, key=lambda unit: 0 if unit["worker_hint"] == worker else 1)
            if units:
                return units[0]
        raise NextError("no unit could be produced; the audit and housekeeping rungs failed")

    def free_gb(self) -> float | None:
        if self._free_gb is _UNSET:
            try:
                self._free_gb = usage.free_memory_gb()
            except Exception:
                self._free_gb = None
        return self._free_gb if isinstance(self._free_gb, (int, float)) else None

    def heavy_cap(self) -> int:
        config = self.policy.get("next") or {}
        base = _int(config.get("heavy_cap"), 1)
        boosted = _int(config.get("heavy_cap_when_memory_ok"), 2)
        threshold = _float(config.get("free_memory_gb_threshold"), 6.0)
        free = self.free_gb()
        if free is not None and free >= threshold:
            return max(base, boosted)
        return base

    def open_refs(self) -> set[str]:
        if self._open_refs is None:
            refs: set[str] = set()
            if self.ledger is not None:
                for task in self.ledger.list_tasks():
                    if task.get("status") in ledger_mod.Ledger.OPEN_STATUSES:
                        refs |= jotarium.refs_for_task(task)
            self._open_refs = refs
        return self._open_refs

    def _covered(self, ref: str, item: dict[str, Any]) -> bool:
        if ref in self.open_refs():
            return True
        if item.get("status") in {"done", "cancelled"}:
            return True
        blockers = item.get("blockers")
        if isinstance(blockers, dict) and blockers.get("open") is not None:
            return True
        children = item.get("children")
        if isinstance(children, dict) and _truthy(children.get("open")):
            return True
        return jotarium.live_worker(item.get("assignee"), self.live) is not None

    def creator_answers(self) -> list[Unit]:
        config = self.policy.get("next") or {}
        days = _int(config.get("answer_window_days"), 14)
        since = (
            (self.now - dt.timedelta(days=days))
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )
        newest: dict[str, tuple[str, dict[str, Any]]] = {}
        for entry in _recent_activity(self.query, since):
            activity = entry.get("activity") or {}
            if activity.get("actor") != "human":
                continue
            if activity.get("command") not in RESOLVE_COMMANDS:
                continue
            ref = (entry.get("item") or {}).get("ref")
            if not isinstance(ref, str) or not ref:
                continue
            stamp = str(activity.get("createdAt") or "")
            current = newest.get(ref)
            if current is None or stamp > current[0]:
                newest[ref] = (stamp, entry)
        units: list[Unit] = []
        for ref, (_stamp, entry) in sorted(
            newest.items(), key=lambda pair: pair[1][0], reverse=True
        ):
            detail = self.query(
                "getItem", {"ref": ref, "include": ["comments", "activity"]}
            )
            item = _item_with_history(detail)
            if item is None:
                continue
            title = str(item.get("title") or "")
            if self._creator_action.search(title):
                continue
            activity = entry.get("activity") or {}
            if _latest_human_activity(item, activity) is not activity:
                continue
            verdict = _answer_verdict(activity, item)
            if verdict is None or self._covered(ref, item):
                continue
            if _answer_is_suppressed(activity, item, self._postpone_words):
                continue
            units.append(
                _unit(
                    "creator-answer",
                    ref,
                    title,
                    "light",
                    _worker_for("creator-answer", "light"),
                    f"creator {verdict}; answer starts light: record it, then plan "
                    "or park; no open task, live worker, open child or Needs-you "
                    "card holds it",
                )
            )
        return units

    def _sibling_summaries(self, parent_ref: str) -> list[dict[str, Any]]:
        if parent_ref not in self._siblings:
            self._siblings[parent_ref] = _pages(
                self.query, "listItems", {"parent": parent_ref}, "items"
            )
        return self._siblings[parent_ref]

    def _blocked_by_phase_or_date(
        self, summary: dict[str, Any], title: str
    ) -> bool:
        """A later phase waits for earlier ones, and a dated item waits for its day."""
        path = summary.get("path")
        parent = (
            path[-1]
            if isinstance(path, list) and path and isinstance(path[-1], dict)
            else None
        )
        parent_title = str(parent.get("title") or "") if parent else ""
        today = self.now.date()
        for text in (title, parent_title):
            gate = _after_date(text, today.year)
            if gate is not None and today < gate:
                return True
        phase = _phase_number(title)
        parent_ref = parent.get("ref") if parent else None
        if phase is None or not isinstance(parent_ref, str) or not parent_ref:
            return False
        for sibling in self._sibling_summaries(parent_ref):
            number = _phase_number(sibling.get("title"))
            if number is None or number >= phase:
                continue
            if sibling.get("status") not in {"done", "cancelled"}:
                return True
        return False

    def open_items(self) -> list[Unit]:
        summaries = _pages(
            self.query,
            "listItems",
            {"status": ["todo", "in_progress"]},
            "items",
        )
        rows: list[dict[str, Any]] = []
        for summary in summaries:
            ref = summary.get("ref")
            if not isinstance(ref, str) or not ref:
                continue
            if summary.get("status") not in {"todo", "in_progress"}:
                continue
            children = summary.get("children")
            if isinstance(children, dict) and _truthy(children.get("open")):
                continue
            if ref in self.open_refs():
                continue
            if jotarium.live_worker(summary.get("assignee"), self.live) is not None:
                continue
            rows.append(summary)
        rows.sort(key=_item_order)
        units: list[Unit] = []
        for summary in rows:
            ref = summary["ref"]
            detail = self.query("getItem", {"ref": ref, "include": ["comments", "activity"]})
            item = _item_with_history(detail)
            if item is None:
                continue
            if item.get("status") not in {"todo", "in_progress"}:
                continue
            if _acceptance_status(item.get("acceptance")) == "pending":
                continue
            blockers = item.get("blockers")
            if isinstance(blockers, dict) and blockers.get("open") is not None:
                continue
            children = item.get("children")
            if isinstance(children, dict) and _truthy(children.get("open")):
                continue
            if ref in self.open_refs():
                continue
            if jotarium.live_worker(item.get("assignee"), self.live) is not None:
                continue
            title = str(item.get("title") or summary.get("title") or "")
            if self._creator_action.search(title):
                continue
            activity = _latest_human_activity(item)
            if _answer_verdict(activity, item) and _answer_is_suppressed(
                activity, item, self._postpone_words
            ):
                continue
            if self._blocked_by_phase_or_date(summary, title):
                continue
            weight = _item_weight(item)
            units.append(
                _unit(
                    "open-item",
                    ref,
                    title,
                    weight,
                    _worker_for("open-item", weight),
                    f"{item.get('status')} item with no pending request and no owner",
                )
            )
        return units

    def follow_ups(self) -> list[Unit]:
        if self.ledger is None:
            return []
        tasks = self.ledger.list_tasks()
        tasks.sort(key=lambda task: str(task.get("updated") or ""), reverse=True)
        open_tasks = [
            task
            for task in tasks
            if task.get("status") in ledger_mod.Ledger.OPEN_STATUSES
        ]
        units: list[Unit] = []
        for task in tasks:
            if task.get("status") not in {"done", "failed"}:
                continue
            text = _checkpoint_next_action(task, Path(self.ledger.repo_root))
            if not text:
                continue
            if _needs_creator(text) or self._creator_action.search(text):
                continue
            if _follow_up_covered(task, open_tasks):
                continue
            weight = _follow_up_weight(task)
            units.append(
                _unit(
                    "follow-up",
                    str(task.get("id") or ""),
                    text.splitlines()[0][:200],
                    weight,
                    _worker_for("follow-up", weight),
                    f"{task.get('status')} task left a next action and no open task owns it",
                )
            )
        return units

    def ideas(self) -> list[Unit]:
        data = self.query("listIdeas", {"status": "open"})
        ideas = data.get("ideas") if isinstance(data, dict) else None
        if not isinstance(ideas, list):
            return []
        open_ideas = [
            idea
            for idea in ideas
            if isinstance(idea, dict)
            and idea.get("status") == "open"
            and not idea.get("plannedInto")
        ]
        open_ideas.sort(key=lambda idea: str(idea.get("createdAt") or ""))
        return [
            _unit(
                "idea",
                str(idea.get("id") or ""),
                _idea_title(idea),
                "light",
                "claude",
                "open idea with no linked item; research it and return a verdict",
            )
            for idea in open_ideas
        ]

    def audits(self) -> list[Unit]:
        return _rotate("audit", AUDITS, self.now)

    def housekeeping(self) -> list[Unit]:
        return _rotate("housekeeping", HOUSEKEEPING, self.now)


def _recent_activity(
    query: Callable[[str, dict[str, Any]], dict[str, Any]],
    since: str,
) -> list[dict[str, Any]]:
    """Activity since ``since``, walking older pages by timestamp."""
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    before: str | None = None
    for _ in range(20):
        payload: dict[str, Any] = {"since": since, "limit": PAGE_SIZE}
        if before is not None:
            payload["before"] = before
        data = query("recentActivity", payload)
        page = data.get("entries") if isinstance(data, dict) else None
        if not isinstance(page, list):
            return entries
        fresh = 0
        for entry in page:
            activity = entry.get("activity") if isinstance(entry, dict) else None
            if not isinstance(activity, dict):
                continue
            key = str(activity.get("id") or "")
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            if str(activity.get("createdAt") or "") < since:
                continue
            entries.append(entry)
            fresh += 1
        if len(page) < PAGE_SIZE or not page:
            break
        stamps = [
            str((entry.get("activity") or {}).get("createdAt") or "")
            for entry in page
            if isinstance(entry, dict)
        ]
        oldest = min((stamp for stamp in stamps if stamp), default="")
        if not oldest or (before is not None and oldest >= before):
            break
        before = oldest
        if fresh == 0:
            break
    return entries


def _pages(
    query: Callable[[str, dict[str, Any]], dict[str, Any]],
    name: str,
    payload: dict[str, Any],
    key: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    cursor: str | None = None
    for _ in range(MAX_ITEM_PAGES):
        body = {**payload, "limit": PAGE_SIZE}
        if cursor:
            body["cursor"] = cursor
        data = query(name, body)
        page = data.get(key) if isinstance(data, dict) else None
        if not isinstance(page, list):
            return rows
        rows.extend(row for row in page if isinstance(row, dict))
        cursor = data.get("nextCursor") if isinstance(data, dict) else None
        if not cursor:
            return rows
    return rows


def select_next(
    sources: Sources,
    weight: str | None = None,
    running_heavy: int = 0,
    worker: str | None = None,
    exclude_refs: set[str] | None = None,
) -> Unit:
    return sources.next_unit(weight=weight, running_heavy=running_heavy, worker=worker, exclude_refs=exclude_refs)


def format_unit(unit: Unit) -> str:
    return (
        f"[{unit['weight']}] {unit['source']} {unit['ref']}: {unit['title']}\n"
        f"  worker: {unit['worker_hint']}\n"
        f"  why: {unit['why']}"
    )


def _live_members(policy: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        prefix = usage.team_prefix(policy)
        return list(usage.team_agents(prefix).values())
    except Exception:
        return []


def next_command(
    weight: str | None = None,
    running_heavy: int = 0,
    as_json: bool = False,
    *,
    query: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
    ledger: Any = None,
    policy: dict[str, Any] | None = None,
    now: dt.datetime | None = None,
    free_gb: float | None | object = _UNSET,
    live: list[dict[str, Any]] | None = None,
) -> int:
    policy = usage.load_policy() if policy is None else policy
    if query is None:
        query = jotarium.JotariumClient().query
    if ledger is None:
        ledger = ledger_mod.default_ledger()
    sources = Sources(
        query,
        ledger,
        policy=policy,
        now=now,
        free_gb=free_gb,
        live=_live_members(policy) if live is None else live,
    )
    unit = select_next(sources, weight=weight, running_heavy=running_heavy)
    if as_json:
        print(json.dumps(unit, indent=2))
    else:
        print(format_unit(unit))
    for error in sources.errors:
        print(f"hx next: {error}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="hx next",
        description="Print the next unit of work that does not need the creator.",
    )
    parser.add_argument(
        "--weight",
        choices=("heavy", "light"),
        help="Ask for one queue. Without it, the first ladder source wins, heavy first.",
    )
    parser.add_argument(
        "--running-heavy",
        type=int,
        default=0,
        help="Heavy units already running; at the cap the answer is light only.",
    )
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)
    return next_command(
        weight=args.weight,
        running_heavy=args.running_heavy,
        as_json=args.as_json,
    )


if __name__ == "__main__":
    raise SystemExit(main())
