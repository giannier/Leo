"""When a service can be booked: the pure rules, with no database.

A slot is a start time on a resource. It is free when the service (its duration, then the buffer
kept after it) fits inside the resource's opening hours of that local day and overlaps nothing else:
not an appointment (whose range already includes its own buffer), not a block. Everything is in
aware datetimes; the day's opening hours are read in the business's own time zone, so a different
zone gives the right hours.

The writer (`service.py`) loads the rows and calls these functions; the database has the last word
on a double booking (`ex_appointments_resource_no_overlap`).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

# `date.weekday()`: Monday is 0. The keys are the ones the business's opening hours use.
DAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DEFAULT_STEP = timedelta(minutes=30)
SHORT_STEP = timedelta(minutes=15)

Interval = tuple[datetime, datetime]
Hours = Mapping[str, object]


def _clock(text: object) -> time | None:
    try:
        hour, minute = str(text).split(":")[:2]
        return time(int(hour), int(minute))
    except (ValueError, TypeError):
        return None


def day_windows(hours: Hours | None, day: date, zone: ZoneInfo) -> list[Interval]:
    """The opening windows of a local `day`, as aware UTC datetimes.

    `hours` is `{"mon": [["09:00", "13:00"], ["15:00", "19:00"]], ...}`. A malformed entry is
    skipped rather than raising: this data comes from a form, and one bad row must not stop every
    booking.
    """
    ranges = (hours or {}).get(DAY_KEYS[day.weekday()])
    windows: list[Interval] = []
    for span in ranges if isinstance(ranges, list | tuple) else []:
        if not isinstance(span, list | tuple) or len(span) < 2:
            continue
        start, end = _clock(span[0]), _clock(span[1])
        if start is None or end is None or end <= start:
            continue
        windows.append(
            (
                datetime.combine(day, start, tzinfo=zone).astimezone(UTC),
                datetime.combine(day, end, tzinfo=zone).astimezone(UTC),
            )
        )
    return sorted(windows)


def is_open(hours: Hours | None, zone: ZoneInfo, now: datetime) -> bool:
    """Whether `now` falls inside an opening window of its local day."""
    day = now.astimezone(zone).date()
    return any(start <= now < end for start, end in day_windows(hours, day, zone))


def next_opening(
    hours: Hours | None, zone: ZoneInfo, now: datetime, *, days: int = 8
) -> datetime | None:
    """The next moment the business opens, looking `days` local days ahead; None if never."""
    first = now.astimezone(zone).date()
    for offset in range(days):
        for start, _ in day_windows(hours, first + timedelta(days=offset), zone):
            if start > now:
                return start
    return None


def step_for(duration: timedelta) -> timedelta:
    """Slots start every half hour; a short service (under 30 minutes) every quarter."""
    return SHORT_STEP if duration < DEFAULT_STEP else DEFAULT_STEP


def overlaps(start: datetime, end: datetime, busy: Sequence[Interval]) -> bool:
    return any(start < b_end and b_start < end for b_start, b_end in busy)


def free_starts(
    *,
    hours: Hours | None,
    zone: ZoneInfo,
    day: date,
    duration: timedelta,
    buffer: timedelta,
    busy: Sequence[Interval],
    earliest: datetime,
    latest: datetime,
) -> list[datetime]:
    """The start times (UTC, ascending) a service of `duration` can take on one local day."""
    step = step_for(duration)
    starts: list[datetime] = []
    for window_start, window_end in day_windows(hours, day, zone):
        moment = window_start
        while moment + duration <= window_end:
            if earliest <= moment <= latest and not overlaps(
                moment, moment + duration + buffer, busy
            ):
                starts.append(moment)
            moment += step
    return starts


def spread(starts: Sequence[datetime], zone: ZoneInfo, limit: int = 3) -> list[datetime]:
    """Up to `limit` options a person can choose from: not three neighbours.

    The earliest of the first day, one from the middle of that day when it has room, then the
    earliest of the next days. A customer is offered "mañana 10:00, mañana 4:00 pm o el viernes
    9:00", not three consecutive half hours.
    """
    by_day: dict[date, list[datetime]] = {}
    for start in sorted(starts):
        by_day.setdefault(start.astimezone(zone).date(), []).append(start)
    chosen: list[datetime] = []
    for index, day_starts in enumerate(by_day.values()):
        chosen.append(day_starts[0])
        if index == 0 and len(day_starts) >= 3:
            chosen.append(day_starts[len(day_starts) // 2])
        if len(chosen) >= limit:
            break
    return sorted(chosen)[:limit]
