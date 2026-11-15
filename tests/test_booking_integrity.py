"""Real PostgreSQL integration tests; never substitute fake rollback behavior."""
from dataclasses import replace
from datetime import datetime, timedelta, date
import pytest
from django.db import IntegrityError
from infra import models
from infra.django_Repositories import DjangoCourseRepository
from scheduling.models import (
    AvailableWindow, COPENHAGEN_TZ, CourseBookingFailedError, PlannedAppointment,
    RescheduleProposal, SlotUnavailableError, TreatmentType,
)

pytestmark = pytest.mark.django_db


def window(room_id, day=27, hour=8, minutes=270):
    start = datetime(2026, 10, day, hour, tzinfo=COPENHAGEN_TZ)
    return AvailableWindow(room_id, start, start + timedelta(minutes=minutes))


def domain_course(course):
    return DjangoCourseRepository().get_by_id(course.pk)


def snapshot():
    return tuple(list(model.objects.order_by("pk").values()) for model in (
        models.TreatmentCourse, models.TreatmentSlot, models.TreatmentAppointment,
    ))


def proposal(appointment, target):
    return RescheduleProposal(
        appointment.pk, appointment.slot_id,
        PlannedAppointment(target, TreatmentType(appointment.treatment_type)),
        appointment.course_id, appointment.treatment_number,
    )


def test_course_collision_rolls_back_course_and_earlier_appointments(service, course, appointment, now):
    before = snapshot()
    free = PlannedAppointment(window(appointment.slot.space_id), TreatmentType.V1)
    taken = PlannedAppointment(AvailableWindow(appointment.slot.space_id, appointment.slot.start_time, appointment.slot.end_time), TreatmentType.V1)
    unsaved_course = replace(domain_course(course), id=None)
    with pytest.raises(CourseBookingFailedError):
        service.book_course(unsaved_course, [free, taken], 2)
    assert snapshot() == before


def test_cascade_collision_restores_cancelled_appointment_and_slot(service, appointment, now):
    other = models.TreatmentSlot.objects.create(
        space=appointment.slot.space, start_time=window(appointment.slot.space_id).start_time,
        end_time=window(appointment.slot.space_id).end_time,
    )
    before = snapshot()
    with pytest.raises(CourseBookingFailedError):
        service.book_cascade([proposal(appointment, AvailableWindow(other.space_id, other.start_time, other.end_time))])
    assert snapshot() == before


def test_reschedule_persistence_failure_does_not_leave_new_slot(service, appointment, now, monkeypatch):
    before = snapshot()
    def fail(_appointment):
        raise IntegrityError("injected appointment persistence failure")
    monkeypatch.setattr(service._appointment_repo, "save", fail)
    with pytest.raises(IntegrityError):
        service.reschedule_single_appointment(appointment.pk, window(appointment.slot.space_id))
    assert snapshot() == before


def test_successful_reschedule_preserves_appointment_fields_and_releases_old_slot(service, appointment, now):
    appointment.note = "Keep this note"
    appointment.save()
    target = window(appointment.slot.space_id)
    old_slot_id = appointment.slot_id
    result = service.reschedule_single_appointment(appointment.pk, target)
    appointment.refresh_from_db()
    assert result.id == appointment.pk
    assert appointment.slot_id != old_slot_id
    assert appointment.note == "Keep this note"
    assert appointment.treatment_number == 1
    assert not models.TreatmentSlot.objects.get(pk=old_slot_id).is_active
    assert models.TreatmentSlot.objects.filter(is_active=True).count() == 1


@pytest.mark.regression
@pytest.mark.parametrize("day,hour,minutes", [(25, 8, 270), (27, 2, 270)])
def test_single_reschedule_rejects_closed_hours(service, appointment, now, day, hour, minutes):
    before = snapshot()
    with pytest.raises((ValueError, SlotUnavailableError)):
        service.reschedule_single_appointment(appointment.pk, window(appointment.slot.space_id, day, hour, minutes))
    assert snapshot() == before


@pytest.mark.regression
def test_single_reschedule_rejects_closed_date(service, appointment, now):
    models.ScheduleClosure.objects.create(date=date(2026, 10, 27), reason="Closed")
    before = snapshot()
    with pytest.raises((ValueError, SlotUnavailableError)):
        service.reschedule_single_appointment(appointment.pk, window(appointment.slot.space_id))
    assert snapshot() == before


@pytest.mark.regression
def test_course_commit_rechecks_new_closure(service, course, room, now):
    target = window(room.pk)
    models.ScheduleClosure.objects.create(space=room, date=target.start_time.date(), reason="Closed after preview")
    before = snapshot()
    with pytest.raises((ValueError, SlotUnavailableError, CourseBookingFailedError)):
        service.book_course(domain_course(course), [PlannedAppointment(target, TreatmentType.V1)], 1)
    assert snapshot() == before


@pytest.mark.regression
def test_stale_cascade_cannot_cancel_rescheduled_appointment(service, appointment, now):
    stale = proposal(appointment, window(appointment.slot.space_id, day=28))
    service.reschedule_single_appointment(appointment.pk, window(appointment.slot.space_id, day=27))
    before = snapshot()
    with pytest.raises((ValueError, SlotUnavailableError, CourseBookingFailedError)):
        service.book_cascade([stale])
    assert snapshot() == before


@pytest.mark.regression
def test_zero_appointment_course_rejected(service, course):
    before = snapshot()
    with pytest.raises(ValueError):
        service.book_course(domain_course(course), [], 0)
    assert snapshot() == before


@pytest.mark.regression
def test_moving_appointment_within_its_own_slot_is_allowed(service, appointment, now):
    target = AvailableWindow(appointment.slot.space_id, appointment.slot.start_time + timedelta(minutes=15), appointment.slot.end_time + timedelta(minutes=15))
    saved = service.reschedule_single_appointment(appointment.pk, target)
    assert saved.id == appointment.pk
    assert models.TreatmentSlot.objects.filter(is_active=True).count() == 1
