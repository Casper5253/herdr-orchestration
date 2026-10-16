"""In-memory store for notes."""

from __future__ import annotations

from typing import Iterable, List, Optional

from models import Note


class NoteStore:
    def __init__(self) -> None:
        self._notes: List[Note] = []
        self._next_id: int = 1

    def add(self, title: str, body: str, tags: Optional[List[str]] = None) -> Note:
        note = Note(self._next_id, title, body, list(tags or []))
        self._next_id += 1
        self._notes.append(note)
        return note

    def all(self) -> List[Note]:
        return list(self._notes)

    def search(self, query: str) -> List[Note]:
        return [n for n in self._notes if n.matches(query)]

    def extend(self, notes: Iterable[Note]) -> None:
        for n in notes:
            self._notes.append(n)
