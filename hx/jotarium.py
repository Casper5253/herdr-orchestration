"""Read-only Jotarium audit for work nobody holds.

Covers in-progress items with no owner, creator send-backs that no follow-up
item, child item, fresh Needs-you card or live worker answers, and open items
whose ref landed in a commit on main in the last 14 days.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable

from hx import ledger, usage

DEFAULT_URL = "http://127.0.0.1:4180"
TOKEN_ENVS = (
    "JOTARIUM_TOKEN",
    "QUESTLOG_TOKEN_CLAUDE",
    "QUESTLOG_TOKEN_CODEX",
    "QUESTLOG_TOKEN_OPENCODE",
    "QUESTLOG_TOKEN_DEVIN",
)
TIMEOUT_SECONDS = 10.0
MAX_PAGES = 100
SEND_BACK_ACTION = "Create a follow-up item for the creator's send-back, or open a new card."
LANDED_ACTION = "Work for this item landed; close it or say what is left"
LANDED_STATUSES = frozenset({"todo", "in_progress", "blocked"})
LANDED_KINDS = frozenset({"task", "subtask"})
ACCEPT_ACTIONS = frozenset({"accept", "choose", "confirm"})
_REF_TEXT = re.compile(r"\b([PFQ])[-\s]?(\d+)\b", re.IGNORECASE)
# A commit subject names a ref only as written: uppercase, hyphen, digits.
_COMMIT_REF = re.compile(r"\b([PFQ])-(\d+)\b")


class JotariumError(RuntimeError):
    """Jotarium could not give a trustworthy audit answer."""


def base_url() -> str:
    raw = os.environ.get("JOTARIUM_URL") or os.environ.get("QUESTLOG_URL") or DEFAULT_URL
    value = raw.rstrip("/")
    if not value.startswith(("http://", "https://")):
        raise JotariumError("Jotarium URL must start with http:// or https://")
    return value


def token_from_env() -> tuple[str, str]:
    for name in TOKEN_ENVS:
        value = os.environ.get(name)
        if value:
            return name, value
    raise JotariumError(
        "No Jotarium token in the environment. Set JOTARIUM_TOKEN or a QUESTLOG_TOKEN_* variable."
    )


class JotariumClient:
    def __init__(self, url: str | None = None, token: str | None = None) -> None:
        self.url = (url or base_url()).rstrip("/")
        self._token = token

    def query(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        token = self._token if self._token is not None else token_from_env()[1]
        request = urllib.request.Request(
            f"{self.url}/api/queries/{name}",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "authorization": f"Bearer {token}",
                "content-type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise JotariumError(f"Jotarium query {name} failed with HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise JotariumError(f"Jotarium is not reachable at {self.url}") from exc
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise JotariumError(f"Jotarium query {name} returned invalid JSON") from exc
        if not isinstance(body, dict) or body.get("ok") is not True:
            code = body.get("error", {}).get("code") if isinstance(body, dict) else None
            raise JotariumError(f"Jotarium query {name} was refused ({code or 'unknown error'})")
        data = body.get("data")
        if not isinstance(data, dict):
            raise JotariumError(f"Jotarium query {name} returned no data object")
        return data


def _pages(
    query: Callable[[str, dict[str, Any]], dict[str, Any]],
    name: str,
    payload: dict[str, Any],
    key: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    cursor: str | None = None
    for _ in range(MAX_PAGES):
        body = {**payload, "limit": 50}
        if cursor:
            body["cursor"] = cursor
        data = query(name, body)
        page = data.get(key)
        if not isinstance(page, list) or any(not isinstance(row, dict) for row in page):
            raise JotariumError(f"Jotarium query {name} returned a malformed {key} list")
        rows.extend(page)
        next_cursor = data.get("nextCursor")
        if next_cursor is None:
            return rows
        if not isinstance(next_cursor, str) or not next_cursor:
            raise JotariumError(f"Jotarium query {name} returned an invalid cursor")
        cursor = next_cursor
    raise JotariumError(f"Jotarium query {name} exceeded {MAX_PAGES} pages")


def _item_detail(
    query: Callable[[str, dict[str, Any]], dict[str, Any]],
    ref: str,
) -> dict[str, Any]:
    data = query("getItem", {"ref": ref})
    item = data.get("item")
    if not isinstance(item, dict):
        raise JotariumError(f"Jotarium getItem {ref} returned no item")
    return item


def _item_ref(item: dict[str, Any]) -> str:
    ref = item.get("ref")
    if not isinstance(ref, str) or not ref:
        raise JotariumError("Jotarium returned an item without a ref")
    return ref


def in_progress_items(query: Callable[[str, dict[str, Any]], dict[str, Any]]) -> list[dict[str, Any]]:
    summaries = _pages(query, "listItems", {"status": ["in_progress"]}, "items")
    details: list[dict[str, Any]] = []
    for summary in summaries:
        ref = _item_ref(summary)
        item = _item_detail(query, ref)
        children = summary.get("children")
        if isinstance(children, dict):
            item["children"] = children
        if item.get("status") == "in_progress":
            details.append(item)
    return details


def _instant(value: object) -> datetime | None:
    """An ISO timestamp as an aware UTC instant, or None when unreadable.

    Payloads may carry offsets or different fractional-second precision, so the
    text is parsed once here and every comparison works on the instant.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _revision(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _review_event(value: object, revision: object) -> dict[str, Any] | None:
    if not isinstance(value, str) or not value:
        return None
    instant = _instant(value)
    if instant is None:
        return None
    return {"at": value, "instant": instant, "revision": _revision(revision)}


def latest_send_back(item: dict[str, Any]) -> dict[str, Any] | None:
    """The newest outstanding creator send-back, or None.

    A rejection stays live until a new acceptance replaces it, so a later card
    answer cannot clear it. A card counts only while it is the newest card the
    creator resolved; an agent withdrawing or superseding a later card does not
    answer an earlier send-back.
    """
    events: list[dict[str, Any]] = []
    acceptance = item.get("acceptance")
    if isinstance(acceptance, dict) and acceptance.get("status") == "rejected":
        event = _review_event(acceptance.get("decidedAt"), acceptance.get("revision"))
        if event is not None:
            events.append(event)
    blockers = item.get("blockers")
    resolved = blockers.get("resolved") if isinstance(blockers, dict) else None
    cards: list[tuple[datetime, dict[str, Any]]] = []
    for card in resolved if isinstance(resolved, list) else []:
        if not isinstance(card, dict) or card.get("action") not in ACCEPT_ACTIONS:
            continue
        instant = _instant(card.get("resolvedAt"))
        if instant is not None:
            cards.append((instant, card))
    if cards:
        _, newest = max(cards, key=lambda pair: pair[0])
        comment = newest.get("comment")
        if isinstance(comment, str) and comment.strip():
            event = _review_event(newest.get("resolvedAt"), newest.get("revision"))
            if event is not None:
                events.append(event)
    if not events:
        return None
    return max(events, key=lambda event: event["instant"])


def _follow_up_after(
    item: dict[str, Any],
    review: dict[str, Any],
    items: list[dict[str, Any]],
) -> str | None:
    keys = {canonical_ref(item.get("ref")), _item_key(item)} - {None}
    revision = review.get("revision")
    for other in items:
        link = other.get("followUp")
        if not isinstance(link, dict) or canonical_ref(link.get("of")) not in keys:
            continue
        if other.get("status") == "cancelled":
            continue
        if revision is not None and link.get("revision") != revision:
            continue
        created = _instant(other.get("createdAt"))
        if created is None or created < review["instant"]:
            continue
        ref = other.get("ref")
        if isinstance(ref, str) and ref:
            return f"follow-up item {ref} after the send-back"
        return "a follow-up item after the send-back"
    return None


def _child_after(item: dict[str, Any], at: datetime) -> str | None:
    children = item.get("childItems")
    for child in children if isinstance(children, list) else []:
        if not isinstance(child, dict) or child.get("status") == "cancelled":
            continue
        created = _instant(child.get("createdAt"))
        if created is None or created < at:
            continue
        ref = child.get("ref")
        if isinstance(ref, str) and ref:
            return f"child {ref} after the send-back"
        return "a child item after the send-back"
    return None


def _new_card_after(item: dict[str, Any], at: datetime) -> str | None:
    blockers = item.get("blockers")
    open_card = blockers.get("open") if isinstance(blockers, dict) else None
    if not isinstance(open_card, dict):
        return None
    opened = _instant(open_card.get("openedAt"))
    if opened is None or opened < at:
        return None
    return "Needs-you card opened after the send-back"


def send_back_coverage(
    item: dict[str, Any],
    review: dict[str, Any],
    items: list[dict[str, Any]],
    live: list[dict[str, Any]],
) -> str | None:
    """Why a creator send-back needs no flag, or None when it does."""
    at = review["instant"]
    follow_up = _follow_up_after(item, review, items)
    if follow_up is not None:
        return follow_up
    child = _child_after(item, at)
    if child is not None:
        return child
    card = _new_card_after(item, at)
    if card is not None:
        return card
    worker = live_worker(item.get("assignee"), live)
    return f"live worker {worker}" if worker is not None else None


def canonical_ref(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    match = _REF_TEXT.search(value)
    if match is None:
        return None
    return f"{match.group(1).upper()}-{int(match.group(2))}"


def refs_in(text: str) -> set[str]:
    """Refs exactly as a commit writes them: uppercase letter, hyphen, digits.

    Case, a missing hyphen or a space is not a ref, so `f3`, `P1`, `q-642` and
    `Q642` never match.
    """
    return {
        f"{match.group(1)}-{int(match.group(2))}"
        for match in _COMMIT_REF.finditer(text)
    }


def _item_key(item: dict[str, Any]) -> str | None:
    shared = item.get("sharedNum")
    if isinstance(shared, int) and not isinstance(shared, bool) and shared > 0:
        return f"Q-{shared}"
    return canonical_ref(item.get("ref"))


def refs_for_task(task: dict[str, Any]) -> set[str]:
    refs: set[str] = set()
    explicit = task.get("jotarium")
    values = explicit if isinstance(explicit, list) else [explicit]
    for value in values:
        ref = canonical_ref(value)
        if ref:
            refs.add(ref)
    for key in ("id", "title"):
        ref = canonical_ref(task.get(key))
        if ref:
            refs.add(ref)
    return refs


def live_worker(assignee: object, live: list[dict[str, Any]]) -> str | None:
    if not isinstance(assignee, str) or not assignee.startswith("agent:"):
        return None
    name = assignee.split(":", 1)[1].strip().lower()
    if not name:
        return None
    for member in live:
        labels = {
            str(member.get("name") or "").lower(),
            str(member.get("role") or "").lower(),
        }
        if name in labels:
            return str(member.get("name") or name)
    return None


def find_unattended(
    items: list[dict[str, Any]],
    tasks: list[dict[str, Any]],
    live: list[dict[str, Any]],
    landed: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    open_tasks: dict[str, list[str]] = {}
    for task in tasks:
        if task.get("status") not in ledger.Ledger.OPEN_STATUSES:
            continue
        task_id = str(task.get("id") or "unknown")
        for ref in refs_for_task(task):
            open_tasks.setdefault(ref, []).append(task_id)

    landed_by_ref: dict[str, dict[str, Any]] = {}
    for commit in landed or []:
        refs = commit.get("refs")
        if not isinstance(refs, (set, frozenset, list, tuple)):
            continue
        for ref in refs:
            if isinstance(ref, str):
                landed_by_ref.setdefault(ref, commit)

    flagged: list[dict[str, Any]] = []
    covered: list[dict[str, Any]] = []
    for item in items:
        ref = _item_ref(item)
        review = latest_send_back(item)
        if review is not None:
            answer = send_back_coverage(item, review, items, live)
            if answer is not None:
                covered.append({"ref": ref, "by": answer})
            else:
                flagged.append(
                    {
                        "ref": ref,
                        "title": str(item.get("title") or ""),
                        "assignee": item.get("assignee"),
                        "action": SEND_BACK_ACTION,
                        "sendBackAt": review["at"],
                    }
                )
            continue
        if (item.get("kind") in LANDED_KINDS
                and item.get("status") in LANDED_STATUSES and ref in landed_by_ref):
            commit = landed_by_ref[ref]
            flagged.append(
                {
                    "ref": ref,
                    "title": str(item.get("title") or ""),
                    "assignee": item.get("assignee"),
                    "action": LANDED_ACTION,
                    "commit": {"sha": commit.get("sha"), "subject": commit.get("subject")},
                }
            )
            continue
        if item.get("status") != "in_progress":
            continue
        if open_tasks.get(ref):
            covered.append({"ref": ref, "by": "hx task " + ", ".join(open_tasks[ref])})
            continue
        children = item.get("children")
        open_children = children.get("open") if isinstance(children, dict) else 0
        if (isinstance(open_children, int) and not isinstance(open_children, bool)
                and open_children > 0):
            covered.append({"ref": ref, "by": f"{open_children} open children"})
            continue
        worker = live_worker(item.get("assignee"), live)
        if worker is not None:
            covered.append({"ref": ref, "by": f"live worker {worker}"})
            continue
        blockers = item.get("blockers")
        open_card = blockers.get("open") if isinstance(blockers, dict) else None
        if open_card is not None:
            covered.append({"ref": ref, "by": "open Needs-you card"})
            continue
        flagged.append(
            {
                "ref": ref,
                "title": str(item.get("title") or ""),
                "assignee": item.get("assignee"),
                "action": "Open a Needs-you card on this item, or name the live worker who holds it.",
            }
        )
    return flagged, covered


def _newest_child(
    query: Callable[[str, dict[str, Any]], dict[str, Any]],
    ref: str,
    cache: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """The parent's newest live child, which alone decides whether a child answers it.

    Child refs come off the shared queue, so list order is creation order and
    the last live item of the last page is the newest, whatever the parent size.
    getItem caps children at 25, so a parent with more would hide a new child.
    """
    children = [
        child
        for child in _pages(query, "listItems", {"parent": ref, "order": "oldest"}, "items")
        if child.get("status") != "cancelled"
    ]
    if not children:
        return []
    child_ref = children[-1].get("ref")
    if not isinstance(child_ref, str) or not child_ref:
        raise JotariumError(f"Jotarium listItems {ref} returned a child without a ref")
    detail = cache.get(child_ref)
    if detail is None:
        detail = _item_detail(query, child_ref)
        cache[child_ref] = detail
    return [detail]


def send_back_scope(
    query: Callable[[str, dict[str, Any]], dict[str, Any]],
    skip: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Every item that can hold a send-back, as getItem details.

    Blocker history and creation times exist only in the detail, so each item
    needs one call. A closed item is included too: it can still carry a card the
    creator answered with a comment that nothing followed up.
    """
    skip = skip or set()
    items: dict[str, dict[str, Any]] = {}
    for summary in _pages(query, "listItems", {}, "items"):
        ref = _item_ref(summary)
        if ref in skip:
            continue
        item = _item_detail(query, ref)
        children = summary.get("children")
        if isinstance(children, dict):
            item["children"] = children
        items[ref] = item
    return list(items.values())


def audited_items(query: Callable[[str, dict[str, Any]], dict[str, Any]]) -> list[dict[str, Any]]:
    items: dict[str, dict[str, Any]] = {}
    for item in in_progress_items(query):
        items[_item_ref(item)] = item
    for item in send_back_scope(query, skip=set(items)):
        items[_item_ref(item)] = item
    cache = dict(items)
    for item in items.values():
        ref = _item_ref(item)
        if "childItems" in item or latest_send_back(item) is None:
            continue
        item["childItems"] = _newest_child(query, ref, cache)
    return list(items.values())


def landed_commits(cwd: str | None = None) -> list[dict[str, Any]]:
    """Commits on main in the last 14 days that name item refs, newest first.

    The project repo is the checkout the audit runs in. Outside one, or without
    a main branch, git fails and the check has nothing to say.
    """
    try:
        completed = subprocess.run(
            ["git", "-C", cwd or os.getcwd(), "log", "main", "--since=14.days", "--format=%h%x09%s"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if completed.returncode != 0:
        return []
    commits: list[dict[str, Any]] = []
    for line in completed.stdout.splitlines():
        sha, _, subject = line.partition("\t")
        if not sha or not subject:
            continue
        commits.append({"sha": sha, "subject": subject, "refs": refs_in(subject)})
    return commits


def audit(client: JotariumClient | None = None) -> dict[str, Any]:
    client = client or JotariumClient()
    token_env = next((name for name in TOKEN_ENVS if os.environ.get(name)), None)
    details = audited_items(client.query)
    policy = usage.load_policy()
    prefix = usage.team_prefix(policy)
    live = list(usage.team_agents(prefix).values())
    tasks = ledger.default_ledger().list_tasks()
    landed = landed_commits()
    flagged, covered = find_unattended(details, tasks, live, landed)
    return {
        "ok": not flagged,
        "url": client.url,
        "token_env": token_env,
        "checked": len(details),
        "flagged": flagged,
        "covered": covered,
    }


def audit_command() -> int:
    try:
        result = audit()
    except JotariumError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        return 1
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1
