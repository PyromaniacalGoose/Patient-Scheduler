from datetime import date, datetime, time, timedelta
import pytest
from django.db import IntegrityError, transaction
from infra import models
from infra.django_Repositories import DjangoPatientRepository, DjangoScheduleRepository, DjangoSlotRepository
from scheduling.models import COPENHAGEN_TZ, ScheduleOverride, TreatmentSlot, SlotUnavailableError

pytestmark = pytest.mark.django_db


@pytest.mark.regression
@pytest.mark.parametrize("lookup", ["get_by_id", "get_by_number"])
def test_missing_patient_returns_none(lookup):
    assert getattr(DjangoPatientRepository(), lookup)(999999) is None


@pytest.mark.regression
@pytest.mark.parametrize("lookup", ["get_by_id", "get_by_number"])
def test_inactive_patient_returns_none(patient, lookup):
    patient.is_active = False
    patient.save()
    identifier = patient.pk if lookup == "get_by_id" else patient.patient_number
    assert getattr(DjangoPatientRepository(), lookup)(identifier) is None


@pytest.mark.regression
def test_override_save_and_read_roundtrip(room):
    repo = DjangoScheduleRepository()
    override = ScheduleOverride(room.pk, date(2026, 10, 26), time(10), time(14))
    assert repo.save_override(override) == override
    assert repo.get_schedule_overrides(override.date, override.date) == [override]


@pytest.mark.regression
def test_existing_override_is_readable(room):
    models.ScheduleOverride.objects.create(space=room, date=date(2026, 10, 26), open_time=time(10), close_time=time(14))
    result = DjangoScheduleRepository().get_schedule_overrides(date(2026, 10, 26), date(2026, 10, 26))
    assert len(result) == 1
    assert result[0].space_id == room.pk


@pytest.mark.parametrize("offset_minutes,overlaps", [(0, True), (30, True), (60, False), (-60, False)])
def test_database_enforces_half_open_room_intervals(room, offset_minutes, overlaps):
    start = datetime(2026, 10, 26, 8, tzinfo=COPENHAGEN_TZ)
    models.TreatmentSlot.objects.create(space=room, start_time=start, end_time=start + timedelta(hours=1))
    def insert():
        with transaction.atomic():
            return models.TreatmentSlot.objects.create(
                space=room, start_time=start + timedelta(minutes=offset_minutes),
                end_time=start + timedelta(minutes=offset_minutes + 60),
            )
    if overlaps:
        with pytest.raises(IntegrityError):
            insert()
    else:
        assert insert().pk


def test_inactive_slots_do_not_block_rebooking(appointment):
    slot = appointment.slot
    DjangoSlotRepository().unbook(slot.pk)
    copy = DjangoSlotRepository().save(TreatmentSlot(None, slot.space_id, slot.start_time, slot.end_time))
    assert copy.id != slot.pk


def test_overlap_repository_error_is_domain_error(appointment):
    slot = appointment.slot
    with pytest.raises(SlotUnavailableError), transaction.atomic():
        DjangoSlotRepository().save(TreatmentSlot(None, slot.space_id, slot.start_time, slot.end_time))


def test_active_treatment_number_unique_but_cancelled_history_allowed(appointment):
    slot = models.TreatmentSlot.objects.create(
        space=appointment.slot.space, start_time=appointment.slot.start_time + timedelta(days=56),
        end_time=appointment.slot.end_time + timedelta(days=56),
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        models.TreatmentAppointment.objects.create(course=appointment.course, slot=slot, treatment_number=1)
    appointment.is_active = False
    appointment.save()
    assert models.TreatmentAppointment.objects.create(course=appointment.course, slot=slot, treatment_number=1).pk
