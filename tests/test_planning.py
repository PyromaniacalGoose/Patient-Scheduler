"""Planner input and room-selection contracts, without a database."""
from datetime import date, time
import pytest
from scheduling.models import TreatmentSpace, SpaceSchedule, TreatmentType
from scheduling.scheduling import SchedulingService
from scheduling.tests.fakes import (
    FakeAppointmentRepository, FakeCourseRepository, FakeScheduleRepository,
    FakeSlotRepository, FakeSpaceRepository,
)


@pytest.fixture
def planner():
    schedules = FakeScheduleRepository()
    spaces = FakeSpaceRepository()
    spaces.seed_spaces(TreatmentSpace(1, "First"), TreatmentSpace(2, "Requested"))
    schedules.seed_rules(*[
        SpaceSchedule(room, weekday, time(8), time(18))
        for room in (1, 2) for weekday in range(5)
    ])
    return SchedulingService(FakeAppointmentRepository(), FakeSlotRepository(), FakeCourseRepository(), schedules, spaces)


@pytest.mark.regression
def test_start_course_honors_requested_rooms(planner):
    result = planner.start_course(1, 2, 56, 77, TreatmentType.V1, [2], date(2026, 10, 26))
    assert result is not None
    _, windows, _ = result
    assert [w.space_id for w in windows] == [2, 2]


@pytest.mark.regression
@pytest.mark.parametrize("count,interval", [(0, 56), (-1, 56), (2, 0), (2, -1)])
def test_invalid_planning_inputs_fail_before_producing_draft(planner, count, interval):
    with pytest.raises(ValueError):
        planner.start_course(1, count, interval, 77, TreatmentType.V1, [1], date(2026, 10, 26))


def test_no_requested_room_means_no_available_window(planner):
    assert planner.find_earliest_available_on_or_after(date(2026, 10, 26), 270, []) is None
