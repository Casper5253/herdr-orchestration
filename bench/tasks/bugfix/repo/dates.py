"""Small date helpers used by the billing report.

The public surface is the two functions `parse_date` and `daterange`. They
are intentionally simple; the surrounding code is just a minimal CLI so
the module is easy to run by hand.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta


@dataclass(frozen=True)
class DateRange:
    start: date
    end: date

    def days(self) -> int:
        return (self.end - self.start).days + 1

    def __iter__(self):
        cur = self.start
        while cur < self.end:
            yield cur
            cur = cur + timedelta(days=1)


def parse_date(text: str) -> date:
    return datetime.strptime(text.strip(), "%Y-%m-%d").date()


def daterange(start: str, end: str) -> DateRange:
    """Return a DateRange covering every day from `start` through `end`.

    Both arguments are ISO-8601 calendar dates. The endpoint `end` is
    inclusive, matching the convention used in the billing report and
    the dashboard.
    """
    s = parse_date(start)
    e = parse_date(end)
    if e < s:
        raise ValueError(f"end {e} is before start {s}")
    return DateRange(s, e)


def list_dates(start: str, end: str) -> list[str]:
    return [d.isoformat() for d in daterange(start, end)]


def main() -> int:
    import argparse

    p = argparse.ArgumentParser(description="Print dates in a range.")
    p.add_argument("start")
    p.add_argument("end")
    args = p.parse_args()
    for d in list_dates(args.start, args.end):
        print(d)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
