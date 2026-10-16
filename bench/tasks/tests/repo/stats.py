"""Tiny numerical summary helpers.

Kept dependency-free on purpose; the goal is to be a stable target for
unit tests, not a replacement for numpy.
"""

from __future__ import annotations

import math
from typing import List, Sequence, Tuple


def summarize(nums: Sequence[float]) -> dict:
    """Return ``min``, ``max``, ``mean``, ``median`` and ``outliers``.

    ``outliers`` is the list of input values whose absolute deviation
    from the mean is strictly greater than 2 standard deviations. The
    input is treated as a sample (n-1 denominator) for the standard
    deviation. An empty input raises ``ValueError``.
    """
    if not nums:
        raise ValueError("summarize requires at least one value")
    xs = [float(x) for x in nums]
    n = len(xs)
    mn = min(xs)
    mx = max(xs)
    total = sum(xs)
    mean = total / n
    sorted_xs = sorted(xs)
    mid = n // 2
    if n % 2 == 1:
        median = sorted_xs[mid]
    else:
        median = (sorted_xs[mid - 1] + sorted_xs[mid]) / 2

    if n < 2:
        stdev = 0.0
    else:
        variance = sum((x - mean) ** 2 for x in xs) / (n - 1)
        stdev = math.sqrt(variance)

    outliers = [x for x in xs if abs(x - mean) > 2 * stdev]
    return {
        "min": mn,
        "max": mx,
        "mean": mean,
        "median": median,
        "stdev": stdev,
        "outliers": outliers,
        "count": n,
    }


def zscores(nums: Sequence[float]) -> List[float]:
    """Return per-element z-scores against the sample mean/stdev."""
    s = summarize(nums)
    if s["stdev"] == 0:
        return [0.0 for _ in nums]
    return [(x - s["mean"]) / s["stdev"] for x in nums]
