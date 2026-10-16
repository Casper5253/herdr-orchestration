"""Command-line interface for the notes app."""

from __future__ import annotations

import argparse
import sys

from store import NoteStore


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="notes", description="Tiny notes CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="add a note")
    a.add_argument("title")
    a.add_argument("body")

    s = sub.add_parser("search", help="search by keyword")
    s.add_argument("query")

    l = sub.add_parser("list", help="list all notes")

    return p


def main(argv: list[str] | None = None) -> int:
    store = NoteStore()
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.cmd == "add":
        n = store.add(args.title, args.body)
        print(f"added #{n.note_id}: {n.title}")
        return 0
    if args.cmd == "search":
        for n in store.search(args.query):
            print(f"#{n.note_id} {n.title}")
        return 0
    if args.cmd == "list":
        for n in store.all():
            print(f"#{n.note_id} {n.title}")
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
