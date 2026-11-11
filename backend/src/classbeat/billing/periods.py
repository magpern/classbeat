"""Billing period arithmetic (ADR 0031 section 5). Pure: no I/O, no clock."""

from __future__ import annotations

import calendar
from datetime import UTC, datetime


def as_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        raise ValueError("naive datetime: billing times are timezone-aware UTC")
    return moment.astimezone(UTC)


def period_end(start: datetime, anchor_day: int) -> datetime:
    """One calendar month after `start`, same time of day, day = anchor clamped to month end.

    Computed from the anchor, not from a previous end, so 31 Jan gives 28/29 Feb, 31 Mar,
    30 Apr with no drift.
    """
    if not 1 <= anchor_day <= 31:
        raise ValueError("anchor_day must be 1 to 31")
    start = as_utc(start)
    year, month = (start.year + 1, 1) if start.month == 12 else (start.year, start.month + 1)
    day = min(anchor_day, calendar.monthrange(year, month)[1])
    return start.replace(year=year, month=month, day=day)


def days_in_period(start: datetime, end: datetime) -> int:
    return (as_utc(end).date() - as_utc(start).date()).days


def days_left(end: datetime, now: datetime) -> int:
    """Whole UTC days to the period end, at least 1: the day of the change counts."""
    return max(1, (as_utc(end).date() - as_utc(now).date()).days)
