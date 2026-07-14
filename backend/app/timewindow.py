"""Shared date-window math for the activity and usage summary endpoints.

The callers deliberately disagree on what "today" means (reading activity is
bucketed in the client's local day, LLM usage in UTC) — only the window
arithmetic is common.
"""

from datetime import date, timedelta

RANGE_DAYS: dict[str, int] = {"week": 7, "month": 30, "year": 365}


def window_bounds(today: date, range_: str) -> tuple[int, date, date]:
    """(window, start, prev_start): the window length in days, its first day,
    and the first day of the equally-sized window preceding it (for the trend
    comparison)."""
    window = RANGE_DAYS[range_]
    start = today - timedelta(days=window - 1)
    return window, start, start - timedelta(days=window)
