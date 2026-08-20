# scheduling/tests/test_scheduling_service.py
from datetime import datetime, date, time, timedelta

import pytest

from scheduling.models import (
    Appointment,
    AppointmentStatus,
    AvailableWindow,
    COPENHAGEN_TZ,
    CourseBookingFailedError,
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

@pytest.fixture
def course_with_four_appointments(repos, open_space):
    '''A course with appointments 1-4 already booked, 8 weeks apart, all SCHEDULED.'''
    course = repos["course_repo"].save(
        TreatmentCourse(id=None, patient_id=1, planned_treatments=4, status=CourseStatus.ACTIVE)
    )

    base = date(2026, 1, 5)  # a Monday
    appointments = []
    for i in range(4):
        appt_date = base + timedelta(days=56 * i)
        slot = repos["slot_repo"].save(
            TreatmentSlot(
                id=None, space_id=1,
                start_time=datetime.combine(appt_date, time(8, 0), tzinfo=COPENHAGEN_TZ),
                end_time=datetime.combine(appt_date, time(12, 30), tzinfo=COPENHAGEN_TZ),
            )
        )
        appt = repos["appointment_repo"].save(
            Appointment(
                id=None, course_id=course.id, slot_id=slot.id,
                treatment_number=i + 1, status=AppointmentStatus.SCHEDULED,
                type=TreatmentType.V1, note="",
            )
        )
        appointments.append(appt)

    return course, appointments


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
        # book out every day for ~10 weeks straight after the second appointment's floor
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

class TestCascadeReschedule:
    def test_raises_for_unknown_appointment_id(self, service):
        with pytest.raises(ValueError):
            service.cascade_reschedule(
                appointment_id=9999, min_interval_days=56, soft_preferred_days=77
            )

    def test_proposes_new_dates_for_the_missed_appointment_and_everything_after(
        self, service, course_with_four_appointments
    ):
        course, appointments = course_with_four_appointments
        missed = appointments[1]  # treatment_number 2

        result = service.cascade_reschedule(
            appointment_id=missed.id, min_interval_days=56, soft_preferred_days=77,
            today=date(2026, 3, 10),
        )
        assert result is not None
        proposals, flagged = result

        assert len(proposals) == 3
        assert [p.treatment_number for p in proposals] == [2, 3, 4]
        assert all(p.course_id == course.id for p in proposals)
        assert all(p.existing_appointment_id == a.id for p, a in zip(proposals, appointments[1:]))

    def test_subsequent_proposed_dates_respect_the_interval_floor(
        self, service, course_with_four_appointments
    ):
        course, appointments = course_with_four_appointments
        missed = appointments[1]

        result = service.cascade_reschedule(
            appointment_id=missed.id, min_interval_days=56, soft_preferred_days=77,
            today=date(2026, 3, 10),
        )
        assert result is not None
        proposals, _ = result

        for prev, nxt in zip(proposals, proposals[1:]):
            gap = (nxt.planned.window.start_time.date() - prev.planned.window.start_time.date()).days
            assert gap >= 56

    def test_no_show_in_the_past_searches_from_today_with_lead_time(
        self, service, course_with_four_appointments
    ):
        course, appointments = course_with_four_appointments
        missed = appointments[1]  # original date 2026-03-02

        fixed_today = date(2026, 3, 10)  # well after the missed appointment's original date

        result = service.cascade_reschedule(
            appointment_id=missed.id, min_interval_days=56, soft_preferred_days=77,
            today=fixed_today,
        )
        assert result is not None
        proposals, _ = result

        assert proposals[0].planned.window.start_time.date() >= fixed_today + timedelta(days=1)

    def test_future_cancellation_does_not_land_on_the_same_day_as_the_original(
        self, service, course_with_four_appointments
    ):
        course, appointments = course_with_four_appointments
        missed = appointments[1]  # original date 2026-03-02

        fixed_today = date(2026, 2, 25)  # cancelled 5 days ahead of the original date

        result = service.cascade_reschedule(
            appointment_id=missed.id, min_interval_days=56, soft_preferred_days=77,
            today=fixed_today,
        )
        assert result is not None
        proposals, _ = result

        original_date = date(2026, 3, 2)
        assert proposals[0].planned.window.start_time.date() > original_date

class TestBookCascade:
    def test_raises_on_empty_proposal_list(self, service):
        with pytest.raises(ValueError):
            service.book_cascade([])

    @pytest.mark.django_db
    def test_commits_proposals_cancelling_old_appointments_and_creating_new_ones(
        self, service, repos, course_with_four_appointments
    ):
        course, appointments = course_with_four_appointments
        missed = appointments[1]

        result = service.cascade_reschedule(
            appointment_id=missed.id, min_interval_days=56, soft_preferred_days=77
        )
        proposals, _ = result

        service.book_cascade(proposals)

        # old appointments 2, 3, 4 should now be cancelled
        for old_appt in appointments[1:]:
            updated = repos["appointment_repo"].get_by_id(old_appt.id)
            assert updated is None or updated.status == AppointmentStatus.CANCELLED

        # new appointments should exist for treatment numbers 2, 3, 4 with SCHEDULED status
        all_appointments = repos["appointment_repo"]._data.values()
        new_ones = [
            a for a in all_appointments
            if a.course_id == course.id and a.status == AppointmentStatus.SCHEDULED and a.treatment_number in (2, 3, 4)
        ]
        assert len(new_ones) == 3

    @pytest.mark.django_db
    def test_raises_course_booking_failed_error_on_collision(
        self, service, repos, course_with_four_appointments
    ):
        course, appointments = course_with_four_appointments
        missed = appointments[1]

        result = service.cascade_reschedule(
            appointment_id=missed.id, min_interval_days=56, soft_preferred_days=77
        )
        proposals, _ = result

        # sabotage: pre-book the exact window the first proposal wants, in a *different* course,
        # so book_cascade collides on it
        stolen_window = proposals[0].planned.window
        repos["slot_repo"].save(
            TreatmentSlot(id=None, space_id=stolen_window.space_id,
                          start_time=stolen_window.start_time, end_time=stolen_window.end_time)
        )

        with pytest.raises(CourseBookingFailedError):
            service.book_cascade(proposals)


class _FixedDate(date):
    _fixed_today = None

    @classmethod
    def today(cls):
        return cls._fixed_today


def _patch_today(monkeypatch, fixed_date):
    _FixedDate._fixed_today = fixed_date
    monkeypatch.setattr("scheduling.scheduling.date", _FixedDate)