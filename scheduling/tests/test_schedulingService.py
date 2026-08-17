# scheduling/tests/test_scheduling_service.py
from datetime import datetime, date, time, timedelta

import pytest

from scheduling.models import (
    AppointmentStatus,
    AvailableWindow,
    COPENHAGEN_TZ,
    CourseStatus,
    PlannedAppointment,
    SlotUnavailableError,
    SpaceSchedule,
    TreatmentCourse,
    TreatmentSlot,
    TreatmentSpace,
    TreatmentType,
)
from scheduling.scheduling import SchedulingService
from scheduling.tests.fakes import (
    FakeAppointmentRepository,
    FakeCourseRepository,
    FakeScheduleRepository,
    FakeSlotRepository,
    FakeSpaceRepository,
)


@pytest.fixture
def repos():
    return {
        "appointment_repo": FakeAppointmentRepository(),
        "slot_repo": FakeSlotRepository(),
        "course_repo": FakeCourseRepository(),
        "schedule_repo": FakeScheduleRepository(),
        "space_repo": FakeSpaceRepository(),
    }


@pytest.fixture
def service(repos):
    return SchedulingService(**repos)


@pytest.fixture
def open_space(repos):
    """A space open every weekday 08:00-16:00, no closures/overrides."""
    space = TreatmentSpace(id=1, name="Room 1")
    repos["space_repo"].seed_spaces(space)
    repos["schedule_repo"].seed_rules(
        *[
            SpaceSchedule(space_id=1, weekday=wd, open_time=time(8, 0), close_time=time(16, 0))
            for wd in range(5)  # Mon-Fri
        ]
    )
    return space


def dt(y, m, d, h=0, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=COPENHAGEN_TZ)


class TestBookAppointment:
    def test_books_successfully_into_a_free_window(self, service, repos, open_space):
        window = AvailableWindow(space_id=1, start_time=dt(2026, 8, 17, 9, 0), end_time=dt(2026, 8, 17, 13, 30))
        planned = PlannedAppointment(window=window, treatment_type=TreatmentType.V1, note="first session")

        appt = service.book_appointment(course_id=1, planned_appointment=planned, treatment_number=1)

        assert appt.id is not None
        assert appt.course_id == 1
        assert appt.treatment_number == 1
        assert appt.status == AppointmentStatus.SCHEDULED
        assert appt.note == "first session"

        slot = repos["slot_repo"].get_by_id(appt.slot_id)
        assert slot.start_time == window.start_time
        assert slot.end_time == window.end_time

    def test_raises_when_window_overlaps_an_existing_booking(self, service, repos, open_space):
        existing = TreatmentSlot(id=None, space_id=1, start_time=dt(2026, 8, 17, 9, 0), end_time=dt(2026, 8, 17, 13, 30))
        repos["slot_repo"].save(existing)

        overlapping_window = AvailableWindow(
            space_id=1, start_time=dt(2026, 8, 17, 11, 0), end_time=dt(2026, 8, 17, 15, 30)
        )
        planned = PlannedAppointment(window=overlapping_window, treatment_type=TreatmentType.V1)

        with pytest.raises(SlotUnavailableError):
            service.book_appointment(course_id=1, planned_appointment=planned, treatment_number=1)

    def test_does_not_raise_for_a_back_to_back_non_overlapping_window(self, service, repos, open_space):
        existing = TreatmentSlot(id=None, space_id=1, start_time=dt(2026, 8, 17, 9, 0), end_time=dt(2026, 8, 17, 13, 30))
        repos["slot_repo"].save(existing)

        adjacent_window = AvailableWindow(
            space_id=1, start_time=dt(2026, 8, 17, 13, 30), end_time=dt(2026, 8, 17, 18, 0)
        )
        planned = PlannedAppointment(window=adjacent_window, treatment_type=TreatmentType.V1)

        appt = service.book_appointment(course_id=1, planned_appointment=planned, treatment_number=2)
        assert appt.id is not None


class TestFindEarliestAvailableOnOrAfter:
    def test_finds_a_window_on_the_first_open_day(self, service, open_space):
        window = service.find_earliest_available_on_or_after(
            earliest=date(2026, 8, 17), duration_minutes=270, space_ids=[1]
        )
        assert window is not None
        assert window.space_id == 1
        assert window.start_time.date() == date(2026, 8, 17)

    def test_skips_forward_past_a_fully_booked_day(self, service, repos, open_space):
        # book the entire Monday 08:00-16:00 window
        repos["slot_repo"].save(
            TreatmentSlot(id=None, space_id=1, start_time=dt(2026, 8, 17, 8, 0), end_time=dt(2026, 8, 17, 16, 0))
        )
        window = service.find_earliest_available_on_or_after(
            earliest=date(2026, 8, 17), duration_minutes=270, space_ids=[1]
        )
        assert window is not None
        assert window.start_time.date() > date(2026, 8, 17)

    def test_returns_none_when_duration_never_fits(self, service, open_space):
        # V2 (510 min) can't fit in an 8-hour (480 min) day anywhere in this space
        window = service.find_earliest_available_on_or_after(
            earliest=date(2026, 8, 17), duration_minutes=510, space_ids=[1], max_search_days=21
        )
        assert window is None


class TestPlanCourseDates:
    def test_respects_the_minimum_interval_floor(self, service, open_space):
        result = service.plan_course_dates(
            earliest_start=date(2026, 8, 17),
            appointment_count=4,
            min_interval_days=56,
            soft_preferred_days=77,
            duration_minutes=270,
        )
        assert result is not None
        windows, flagged = result
        assert len(windows) == 4

        for prev, nxt in zip(windows, windows[1:]):
            gap = (nxt.start_time.date() - prev.start_time.date()).days
            assert gap >= 56

    def test_flags_appointments_that_land_beyond_the_soft_preference(self, service, repos, open_space):
        # Book out every day for ~10 weeks straight after the second appointment's floor
        # so the 3rd appointment is forced past the 11-week soft preference.
        blocked_start = date(2026, 8, 17) + timedelta(days=56 * 2)
        for i in range(40):
            day = blocked_start + timedelta(days=i)
            if day.weekday() < 5:
                repos["slot_repo"].save(
                    TreatmentSlot(
                        id=None, space_id=1,
                        start_time=datetime.combine(day, time(8, 0), tzinfo=COPENHAGEN_TZ),
                        end_time=datetime.combine(day, time(16, 0), tzinfo=COPENHAGEN_TZ),
                    )
                )

        result = service.plan_course_dates(
            earliest_start=date(2026, 8, 17),
            appointment_count=4,
            min_interval_days=56,
            soft_preferred_days=77,
            duration_minutes=270,
        )
        assert result is not None
        _, flagged = result
        assert any(flagged)

    def test_returns_none_when_no_feasible_plan_exists(self, service, open_space):
        result = service.plan_course_dates(
            earliest_start=date(2026, 8, 17),
            appointment_count=4,
            min_interval_days=56,
            soft_preferred_days=77,
            duration_minutes=510,  # never fits in this space's hours
        )
        assert result is None

@pytest.mark.django_db
class TestBookCourse:
    def test_books_all_planned_appointments_and_links_them_to_the_course(self, service, repos, open_space):
        windows = [
            AvailableWindow(space_id=1, start_time=dt(2026, 8, 17, 8, 0), end_time=dt(2026, 8, 17, 12, 30)),
            AvailableWindow(space_id=1, start_time=dt(2026, 9, 21, 8, 0), end_time=dt(2026, 9, 21, 12, 30)),
        ]
        planned = [PlannedAppointment(window=w, treatment_type=TreatmentType.V1) for w in windows]
        course = TreatmentCourse(id=None, patient_id=42, planned_treatments=0, status=CourseStatus.PLANNED)

        saved_course = service.book_course(course, planned, number_of_appointments=2)

        assert saved_course.id is not None
        assert saved_course.planned_treatments == 2

        appointments = [a for a in repos["appointment_repo"]._data.values() if a.course_id == saved_course.id]
        assert len(appointments) == 2
        assert {a.treatment_number for a in appointments} == {1, 2}

    def test_raises_on_appointment_count_mismatch(self, service, open_space):
        windows = [AvailableWindow(space_id=1, start_time=dt(2026, 8, 17, 8, 0), end_time=dt(2026, 8, 17, 12, 30))]
        planned = [PlannedAppointment(window=w, treatment_type=TreatmentType.V1) for w in windows]
        course = TreatmentCourse(id=None, patient_id=42, planned_treatments=0, status=CourseStatus.PLANNED)

        with pytest.raises(Exception):  # AppointmentMissmatchError
            service.book_course(course, planned, number_of_appointments=2)