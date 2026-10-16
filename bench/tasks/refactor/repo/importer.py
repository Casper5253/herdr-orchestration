"""CSV-like row parsing used by the importer."""

from __future__ import annotations

import csv
import io
from typing import List


def _split_quoted(line: str) -> List[str]:
    out: List[str] = []
    buf: List[str] = []
    in_q = False
    for ch in line:
        if ch == '"':
            in_q = not in_q
            continue
        if ch == "," and not in_q:
            out.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    out.append("".join(buf))
    return out


def parse_line(line: str) -> List[str]:
    """Parse a single comma-separated row, honouring double-quoted fields."""
    s = line.lstrip("\ufeff").rstrip("\r\n")
    if s.startswith('"') or ',"' in s or s.endswith('"'):
        return _split_quoted(s)
    return next(csv.reader(io.StringIO(s)))


def parse_text(text: str) -> List[List[str]]:
    """Parse a whole CSV-ish text blob into rows of fields."""
    rows: List[List[str]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(parse_line(line))
    return rows
