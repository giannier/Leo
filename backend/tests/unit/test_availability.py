"""The rules of when a service can be booked (feature 019, phase D)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.scheduling.availability import day_windows, free_starts, overlaps, spread, step_for

LIMA = ZoneInfo("America/Lima")  # UTC-5, no daylight saving
HOURS = {"mon": [["09:00", "12:00"], ["14:00", "16:00"]], "tue": [["09:00", "10:00"]]}
MONDAY = date(2026, 10, 26)
HOUR = timedelta(hours=1)


def at(hour: int, minute: int = 0, day: date = MONDAY) -> datetime:
    """A Lima clock time as UTC."""
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=LIMA).astimezone(UTC)


def starts(**override: object) -> list[datetime]:
    args: dict[str, object] = {
        "hours": HOURS,
        "zone": LIMA,
        "day": MONDAY,
        "duration": HOUR,
        "buffer": timedelta(0),
        "busy": [],
        "earliest": at(0),
        "latest": at(0) + timedelta(days=30),
    }
    args.update(override)
    return free_starts(**args)  # type: ignore[arg-type]


class TestTheOpeningWindows:
    def test_a_day_has_its_windows_in_utc(self) -> None:
        windows = day_windows(HOURS, MONDAY, LIMA)

        assert windows == [(at(9), at(12)), (at(14), at(16))]
        assert windows[0][0].hour == 14  # 09:00 in Lima is 14:00 UTC

    def test_a_closed_day_has_none(self) -> None:
        assert day_windows(HOURS, date(2026, 10, 28), LIMA) == []  # Wednesday
        assert day_windows(None, MONDAY, LIMA) == []

    @pytest.mark.parametrize(
        "bad",
        [
            {"mon": "09:00-12:00"},
            {"mon": [["09:00"]]},
            {"mon": [["x", "y"]]},
            {"mon": [["12:00", "09:00"]]},
        ],
    )
    def test_a_malformed_entry_is_skipped_not_raised(self, bad: dict[str, object]) -> None:
        assert day_windows(bad, MONDAY, LIMA) == []

    def test_the_hours_are_read_in_the_zone_of_the_business(self) -> None:
        madrid = ZoneInfo("Europe/Madrid")

        (window,) = day_windows({"mon": [["09:00", "10:00"]]}, MONDAY, madrid)

        assert window[0] == datetime(2026, 10, 26, 8, 0, tzinfo=UTC)  # CET after the October change


class TestTheFreeStarts:
    def test_every_half_hour_that_fits(self) -> None:
        assert starts() == [
            at(9),
            at(9, 30),
            at(10),
            at(10, 30),
            at(11),
            at(14),
            at(14, 30),
            at(15),
        ]

    def test_a_service_never_runs_past_closing(self) -> None:
        assert at(11, 30) not in starts() and at(15, 30) not in starts()

    def test_an_appointment_takes_its_range_out(self) -> None:
        busy = [(at(10), at(11))]

        free = starts(busy=busy)

        assert at(9, 30) not in free and at(10, 30) not in free
        assert at(9) in free and at(11) in free

    def test_the_buffer_after_the_service_must_be_free_too(self) -> None:
        busy = [(at(11), at(12))]

        without = starts(busy=busy)
        with_buffer = starts(busy=busy, buffer=timedelta(minutes=30))

        assert at(10) in without  # ends 11:00, the next starts 11:00
        assert at(10) not in with_buffer  # needs until 11:30

    def test_a_blocked_lunch_is_not_offered(self) -> None:
        assert at(14, 30) not in starts(busy=[(at(14), at(15))])

    def test_the_minimum_notice_hides_what_is_too_soon(self) -> None:
        free = starts(earliest=at(10, 15))

        assert free[0] == at(10, 30)

    def test_the_horizon_hides_what_is_too_far(self) -> None:
        assert starts(latest=at(10)) == [at(9), at(9, 30), at(10)]

    def test_a_short_service_steps_every_quarter_hour(self) -> None:
        assert step_for(timedelta(minutes=20)) == timedelta(minutes=15)
        assert starts(duration=timedelta(minutes=20))[:3] == [at(9), at(9, 15), at(9, 30)]


class TestOverlap:
    def test_touching_ranges_do_not_overlap(self) -> None:
        assert not overlaps(at(10), at(11), [(at(11), at(12)), (at(8), at(10))])

    def test_crossing_ranges_do(self) -> None:
        assert overlaps(at(10), at(11), [(at(10, 30), at(12))])


class TestTheOptionsOffered:
    def test_not_three_neighbours(self) -> None:
        monday = starts()
        tuesday = starts(day=date(2026, 10, 27))

        offered = spread(monday + tuesday, LIMA)

        assert len(offered) == 3
        assert offered[0] == at(9)
        assert any(o.astimezone(LIMA).date() == date(2026, 10, 27) for o in offered)
        assert offered[1] - offered[0] > timedelta(minutes=30)

    def test_few_slots_give_few_options(self) -> None:
        assert spread([at(9)], LIMA) == [at(9)]
        assert spread([], LIMA) == []
