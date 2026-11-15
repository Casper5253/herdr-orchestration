"""Read-only Jotarium audit for in-progress work nobody holds."""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
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
MAX_PAGES = 20
_REF_TEXT = re.compile(r"\b([PFQ])[-\s]?(\d+)\b", re.IGNORECASE)


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


def in_progress_items(query: Callable[[str, dict[str, Any]], dict[str, Any]]) -> list[dict[str, Any]]:
    summaries = _pages(query, "listItems", {"status": ["in_progress"]}, "items")
    details: list[dict[str, Any]] = []
    for summary in summaries:
        ref = summary.get("ref")
        if not isinstance(ref, str) or not ref:
            raise JotariumError("Jotarium listItems returned an item without a ref")
        data = query("getItem", {"ref": ref})
        item = data.get("item")
        if not isinstance(item, dict):
            raise JotariumError(f"Jotarium getItem {ref} returned no item")
        if item.get("status") == "in_progress":
            details.append(item)
    return details


def canonical_ref(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    match = _REF_TEXT.search(value)
    if match is None:
        return None
    return f"{match.group(1).upper()}-{int(match.group(2))}"


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
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    open_tasks: dict[str, list[str]] = {}
    for task in tasks:
        if task.get("status") not in ledger.Ledger.OPEN_STATUSES:
            continue
        task_id = str(task.get("id") or "unknown")
        for ref in refs_for_task(task):
            open_tasks.setdefault(ref, []).append(task_id)

    flagged: list[dict[str, Any]] = []
    covered: list[dict[str, Any]] = []
    for item in items:
        if item.get("status") != "in_progress":
            continue
        ref = item.get("ref")
        if not isinstance(ref, str) or not ref:
            continue
        if open_tasks.get(ref):
            covered.append({"ref": ref, "by": "hx task " + ", ".join(open_tasks[ref])})
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


def audit(client: JotariumClient | None = None) -> dict[str, Any]:
    client = client or JotariumClient()
    token_env = next((name for name in TOKEN_ENVS if os.environ.get(name)), None)
    details = in_progress_items(client.query)
    policy = usage.load_policy()
    prefix = usage.team_prefix(policy)
    live = list(usage.team_agents(prefix).values())
    tasks = ledger.default_ledger().list_tasks()
    flagged, covered = find_unattended(details, tasks, live)
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
