"""Pure calculations: no database access, including override precedence."""
from datetime import date, datetime, time, timedelta
from itertools import permutations
import pytest
from scheduling.models import (
    COPENHAGEN_TZ, SpaceSchedule, ScheduleOverride, ScheduleClosure,
    TreatmentSlot, FreeInterval,
)
from scheduling.scheduling import compute_free_intervals, find_windows_for_duration

DAY = date(2026, 10, 26)


def dt(hour, minute=0):
    return datetime.combine(DAY, time(hour, minute), COPENHAGEN_TZ)


def free(*, overrides=(), closures=(), bookings=(), selected=None):
    return compute_free_intervals(
        [SpaceSchedule(i, 0, time(8), time(18)) for i in (1, 2)],
        list(overrides), list(closures), list(bookings), dt(0), dt(23), [1, 2], selected,
    )


@pytest.mark.regression
@pytest.mark.parametrize("selected", [None, 1])
def test_global_override_applies_with_and_without_space_filter(selected):
    result = free(overrides=[ScheduleOverride(None, DAY, time(10), time(12))], selected=selected)
    assert len(result) == (2 if selected is None else 1)
    assert all((x.start_time, x.end_time) == (dt(10), dt(12)) for x in result)


def test_space_override_takes_precedence_over_global():
    result = free(overrides=[
        ScheduleOverride(None, DAY, time(10), time(12)),
        ScheduleOverride(1, DAY, time(11), time(13)),
    ], selected=1)
    assert [(x.start_time, x.end_time) for x in result] == [(dt(11), dt(13))]


@pytest.mark.parametrize("space_id,expected", [(None, []), (1, [2])])
def test_closure_wins_over_overrides(space_id, expected):
    result = free(
        overrides=[ScheduleOverride(1, DAY, time(10), time(12))],
        closures=[ScheduleClosure(space_id, DAY, "Closed")],
    )
    assert [x.space_id for x in result] == expected


def test_unsorted_overlapping_bookings_are_subtracted_once():
    bookings = [
        TreatmentSlot(1, 1, dt(7), dt(10)),
        TreatmentSlot(2, 1, dt(9), dt(12)),
        TreatmentSlot(3, 1, dt(14), dt(19)),
    ]
    for ordering in permutations(bookings):
        result = free(bookings=ordering, selected=1)
        assert [(x.start_time, x.end_time) for x in result] == [(dt(12), dt(14))]


def test_bookings_in_other_rooms_do_not_reduce_capacity():
    result = free(bookings=[TreatmentSlot(1, 2, dt(8), dt(18))], selected=1)
    assert [(x.start_time, x.end_time) for x in result] == [(dt(8), dt(18))]


def test_request_range_clips_open_hours_and_excludes_end_day():
    result = compute_free_intervals(
        [SpaceSchedule(1, d, time(8), time(18)) for d in (0, 1)], [], [], [],
        dt(10), datetime.combine(DAY + timedelta(days=1), time.min, COPENHAGEN_TZ), [1],
    )
    assert [(x.start_time, x.end_time) for x in result] == [(dt(10), dt(18))]


@pytest.mark.regression
def test_window_starts_align_to_quarter_hour_after_irregular_booking():
    windows = find_windows_for_duration([FreeInterval(1, dt(8, 7), dt(10))], 30)
    assert windows
    assert windows[0].start_time == dt(8, 15)
    assert all(w.start_time.minute % 15 == 0 for w in windows)


@pytest.mark.regression
@pytest.mark.parametrize("duration", [0, -1])
def test_nonpositive_treatment_duration_is_rejected(duration):
    with pytest.raises(ValueError):
        find_windows_for_duration([FreeInterval(1, dt(8), dt(9))], duration)


def test_windows_never_extend_beyond_free_interval():
    interval = FreeInterval(1, dt(8), dt(13))
    windows = find_windows_for_duration([interval], 270)
    assert len(windows) == 3
    assert all(interval.start_time <= w.start_time < w.end_time <= interval.end_time for w in windows)
    assert all(w.end_time - w.start_time == timedelta(minutes=270) for w in windows)
