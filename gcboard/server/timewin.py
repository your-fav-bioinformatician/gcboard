"""Day and week boundaries in the group time zone (pure functions).

All server timestamps are integer or float Unix epoch seconds (UTC). Calendar
logic happens here and nowhere else.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo


def parse_hhmm(value: str) -> time:
    """Parse "HH:MM" (24-hour) into a time. Raises ValueError on bad input."""
    try:
        hh, mm = value.split(":")
        return time(int(hh), int(mm))
    except (ValueError, TypeError) as exc:
        raise ValueError(f"expected HH:MM, got {value!r}") from exc


def _local_epoch(d: date, t: time, tz: ZoneInfo) -> int:
    return int(datetime.combine(d, t, tzinfo=tz).timestamp())


@dataclass(frozen=True)
class TaskWindow:
    task_date: date
    opens_at: int
    closes_at: int


def window_for_date(d: date, tz: ZoneInfo, post_time: time) -> TaskWindow:
    """The daily-task window dated `d`: from its post time to the next day's."""
    return TaskWindow(
        task_date=d,
        opens_at=_local_epoch(d, post_time, tz),
        closes_at=_local_epoch(d + timedelta(days=1), post_time, tz),
    )


def current_window(now: float, tz: ZoneInfo, post_time: time) -> TaskWindow:
    """The daily-task window that contains `now` (FR-TASK-1)."""
    local = datetime.fromtimestamp(now, tz)
    d = local.date()
    if local.time() < post_time:
        d -= timedelta(days=1)
    w = window_for_date(d, tz, post_time)
    # DST edge cases: make sure the returned window really contains `now`.
    if now < w.opens_at:
        w = window_for_date(d - timedelta(days=1), tz, post_time)
    elif now >= w.closes_at:
        w = window_for_date(d + timedelta(days=1), tz, post_time)
    return w


@dataclass(frozen=True)
class Week:
    monday: date
    sunday: date
    starts_at: int
    ends_at: int  # exclusive: next Monday 00:00 local
    iso_week: int

    def contains(self, d: date) -> bool:
        return self.monday <= d <= self.sunday


def week_of(now: float, tz: ZoneInfo) -> Week:
    """Monday 00:00 to Sunday 23:59:59 in the group time zone (FR-BOARD-1)."""
    today = datetime.fromtimestamp(now, tz).date()
    monday = today - timedelta(days=today.weekday())
    next_monday = monday + timedelta(days=7)
    return Week(
        monday=monday,
        sunday=monday + timedelta(days=6),
        starts_at=_local_epoch(monday, time(0, 0), tz),
        ends_at=_local_epoch(next_monday, time(0, 0), tz),
        iso_week=monday.isocalendar().week,
    )
