"""Top-level report pipeline.

Reads rows from a JSON file (path on argv), runs them through
``data.load``, and prints a one-line summary per row.
"""

from __future__ import annotations

import json
import sys

import data


def _label_for(row: dict) -> str:
    return f"#{row['id']:>3}  {row['name']:<20}  score={row['score']}"


def build_report(rows):
    out = []
    total = 0
    for r in rows:
        total += r["score"] + len(r["tags"])
        out.append(_label_for(r))
    out.append(f"---- total={total}")
    return out


def main(argv: list[str]) -> int:
    path = argv[1]
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    rows = data.load(raw)
    for line in build_report(rows):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
