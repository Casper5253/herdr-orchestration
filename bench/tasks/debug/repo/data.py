"""Row loading helpers.

These functions are the only place the rest of the app sees raw dict
rows, so they are the natural place to enforce "required field" rules.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List


REQUIRED_FIELDS = ("id", "name")


def parse_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Coerce one raw row into a normalised dict.

    Falls back to ``None`` for missing required fields so downstream
    code can detect them with simple truthiness checks.
    """
    out: Dict[str, Any] = {}
    for k in REQUIRED_FIELDS:
        out[k] = row.get(k)
    out["score"] = row.get("score", 0)
    out["tags"] = list(row.get("tags") or [])
    return out


def load(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [parse_row(r) for r in rows]
