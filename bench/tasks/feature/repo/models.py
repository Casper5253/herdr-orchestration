"""Domain models for a tiny notes app."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass
class Note:
    note_id: int
    title: str
    body: str
    tags: List[str] = field(default_factory=list)

    def matches(self, query: str) -> bool:
        q = query.lower()
        return q in self.title.lower() or q in self.body.lower()
