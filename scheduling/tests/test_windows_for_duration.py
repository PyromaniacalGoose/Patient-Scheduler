from datetime import date, datetime, time, timedelta
from scheduling.models import FreeInterval
from scheduling.scheduling import find_windows_for_duration
from zoneinfo import ZoneInfo

COPENHAGEN_TZ = ZoneInfo("Europe/Copenhagen")

start_t = datetime(2026, 8, 17, 0, 0, tzinfo=COPENHAGEN_TZ) 
end_t = datetime(2026, 8, 17, 1, 0, tzinfo=COPENHAGEN_TZ)
interval1 = FreeInterval(1, start_t, end_t)
interval2 = FreeInterval(2, start_t, end_t)

def test_one_interval_returns_one_window():
    result = find_windows_for_duration([interval1], 60)
    end_time_result = start_t + timedelta(hours=1)

    assert len(result) == 1
    assert result[0].start_time == start_t
    assert result[0].end_time == end_time_result

def test_one_interval_with_shorter_duration_returns_multiple_overlapping_candidates():
    result = find_windows_for_duration([interval1], 30)

    assert len(result) == 3
    assert result[0].start_time == start_t
    assert result[1].start_time == start_t + timedelta(minutes=15)
    assert result[2].start_time == start_t + timedelta(minutes=30)


def test_two_intervals_returns_one_window():
    end_t2 = datetime(2026, 8, 17, 0, 30, tzinfo=COPENHAGEN_TZ)
    interval_30_min = FreeInterval(2, start_t, end_t2)

    result = find_windows_for_duration([interval1, interval_30_min], 60)
    assert len(result) == 1
    

#def test_two_intervals_returns_two_window():...
