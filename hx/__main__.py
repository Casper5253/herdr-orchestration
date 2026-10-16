"""Entry point: python -m hx <command>."""

from __future__ import annotations

import argparse
import sys

from hx import usage


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hx",
        description="Herdr team CLI: usage, routing, ledger, checkpoint, context.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    usage_parser = subparsers.add_parser(
        "usage",
        help="Print a JSON snapshot of provider availability and quota.",
    )
    usage_parser.set_defaults(func=usage.print_usage)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        args.func()
    except Exception as exc:
        print(f"hx: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
