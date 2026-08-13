from datetime import date, datetime, time
from scheduling.models import SpaceSchedule, ScheduleClosure, TreatmentSlot, ScheduleClosure
from scheduling.scheduling import compute_free_intervals
from zoneinfo import ZoneInfo

COPENHAGEN_TZ = ZoneInfo("Europe/Copenhagen")

rule1 = SpaceSchedule(space_id=1, weekday=0, open_time=time(8, 0), close_time=time(16, 0))

def test_fully_open_day_with_no_bookings_returns_one_interval():
    start = datetime(2026, 8, 17, 0, 0, tzinfo=COPENHAGEN_TZ)   # a Monday
    end = datetime(2026, 8, 18, 0, 0, tzinfo=COPENHAGEN_TZ)

    result = compute_free_intervals(
        schedule_rules=[rule1], overrides=[], closures=[], booked_slots=[],
        start=start, end=end, space_ids=[1],
    )

    assert len(result) == 1
    assert result[0].start_time == datetime(2026, 8, 17, 8, 0, tzinfo=COPENHAGEN_TZ)
    assert result[0].end_time == datetime(2026, 8, 17, 16, 0, tzinfo=COPENHAGEN_TZ)

def test_closed_first_day_with_no_bookings_returns_one_interval():
    rule2 = SpaceSchedule(space_id=1, weekday=1, open_time=time(8, 0), close_time=time(16, 0)) # Open tuesday
    closure = ScheduleClosure(None, date(2026, 8, 17), "Test") # Monday closed
    start = datetime(2026, 8, 17, 0, 0, tzinfo=COPENHAGEN_TZ)   # a Monday
    end = datetime(2026, 8, 19, 0, 0, tzinfo=COPENHAGEN_TZ)

    result = compute_free_intervals(
        schedule_rules=[rule1, rule2], overrides=[], closures=[closure], booked_slots=[],
        start=start, end=end, space_ids=[1],
    )

    assert len(result) == 1
    assert result[0].start_time == datetime(2026, 8, 18, 8, 0, tzinfo=COPENHAGEN_TZ) # Should return tuesday as monday is closed
    assert result[0].end_time == datetime(2026, 8, 18, 16, 0,tzinfo=COPENHAGEN_TZ)

def test_closed_room_with_no_bookings_returns_one_interval():
    rule2 = SpaceSchedule(space_id=2, weekday=0, open_time=time(8, 0), close_time=time(16, 0)) # secondary treatment space
    closure = ScheduleClosure(1, date(2026, 8, 17), "Test") # Monday closed
    start = datetime(2026, 8, 17, 0, 0, tzinfo=COPENHAGEN_TZ)   # a Monday
    end = datetime(2026, 8, 18, 0, 0, tzinfo=COPENHAGEN_TZ)

    result = compute_free_intervals(
        schedule_rules=[rule1, rule2], overrides=[], closures=[closure], booked_slots=[],
        start=start, end=end, space_ids=[1, 2],
    )

    assert len(result) == 1
    assert result[0].space_id == 2 # Check that it booked space with id 2, as 1 was closed

def test_fully_open_days_with_no_bookings_returns_2_interval():
    rule2 = SpaceSchedule(space_id=1, weekday=1, open_time=time(8, 0), close_time=time(16, 0)) # Open tuesday
    start = datetime(2026, 8, 17, 0, 0, tzinfo=COPENHAGEN_TZ)   # a Monday
    end = datetime(2026, 8, 19, 0, 0, tzinfo=COPENHAGEN_TZ)

    result = compute_free_intervals(
        schedule_rules=[rule1, rule2], overrides=[], closures=[], booked_slots=[],
        start=start, end=end, space_ids=[1],
    )

    assert len(result) == 2
    assert result[0].start_time == datetime(2026, 8, 17, 8, 0, tzinfo=COPENHAGEN_TZ) 
    assert result[0].end_time == datetime(2026, 8, 17, 16, 0, tzinfo=COPENHAGEN_TZ)
    assert result[1].start_time == datetime(2026, 8, 18, 8, 0, tzinfo=COPENHAGEN_TZ) 
    assert result[1].end_time == datetime(2026, 8, 18, 16, 0, tzinfo=COPENHAGEN_TZ)