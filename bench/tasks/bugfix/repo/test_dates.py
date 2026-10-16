"""Visible smoke tests for the dates module.

Run with `pytest test_dates.py` from this directory. The last test
fails on the current code; it is the one that documents the bug.
"""

from datetime import date

import pytest

from dates import DateRange, daterange, list_dates, parse_date


def test_parse_date_roundtrip():
    assert parse_date("2024-01-15") == date(2024, 1, 15)


def test_daterange_days_inclusive():
    r = daterange("2024-01-01", "2024-01-10")
    assert r.days() == 10
    assert r.start == date(2024, 1, 1)
    assert r.end == date(2024, 1, 10)


def test_daterange_rejects_inverted():
    with pytest.raises(ValueError):
        daterange("2024-02-10", "2024-02-01")


def test_list_dates_includes_endpoints():
    # Both endpoints must be present; the current code drops the last day.
    assert list_dates("2024-01-01", "2024-01-03") == [
        "2024-01-01",
        "2024-01-02",
        "2024-01-03",
    ]


def test_iter_yields_all_days():
    r = daterange("2024-01-30", "2024-02-01")
    assert list(r) == [date(2024, 1, 30), date(2024, 1, 31), date(2024, 2, 1)]
