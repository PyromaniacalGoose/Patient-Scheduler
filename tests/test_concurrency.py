"""Independent connections and bounded synchronization, never sleeps.

These tests need a PostgreSQL database and pytest-django's transactional_db.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier
import pytest
from django.db import close_old_connections, connections, transaction
from infra import models
from infra.services import build_scheduling_service
from scheduling.models import AvailableWindow, COPENHAGEN_TZ, SlotUnavailableError

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.concurrency]


def run_workers(workers):
    def run(worker):
        close_old_connections()
        try:
            # Fail fast rather than leave the suite blocked on a row lock.
            with connections["default"].cursor() as cursor:
                cursor.execute("SET lock_timeout = '5s'")
                cursor.execute("SET statement_timeout = '10s'")
            return worker()
        finally:
            connections["default"].close()
    with ThreadPoolExecutor(max_workers=len(workers)) as pool:
        futures = [pool.submit(run, worker) for worker in workers]
        return [future.result(timeout=20) for future in futures]


def test_simultaneous_room_bookings_cannot_both_succeed(room):
    barrier = Barrier(2, timeout=5)
    start = datetime(2026, 10, 27, 8, tzinfo=COPENHAGEN_TZ)
    def worker():
        from django.db import IntegrityError
        barrier.wait()
        try:
            with transaction.atomic():
                models.TreatmentSlot.objects.create(space_id=room.pk, start_time=start, end_time=start + timedelta(hours=1))
            return "booked"
        except IntegrityError:
            return "conflict"
    assert sorted(run_workers([worker, worker])) == ["booked", "conflict"]
    assert models.TreatmentSlot.objects.filter(is_active=True).count() == 1


@pytest.mark.regression
def test_concurrent_reschedules_never_leave_orphan_active_slot(appointment, now, monkeypatch):
    # Synchronize the actual initial reads, exposing the lost-update race.
    # If a fix locks on the first read, PostgreSQL serializes those reads; skip
    # this barrier for select_for_update queries to avoid manufacturing deadlock.
    from django.db.models.query import QuerySet
    original_get = QuerySet.get
    barrier = Barrier(2, timeout=5)
    import threading
    local = threading.local()

    def synchronized_get(query, *args, **kwargs):
        result = original_get(query, *args, **kwargs)
        if query.model is models.TreatmentAppointment and not getattr(local, "seen", False):
            local.seen = True
            if not query.query.select_for_update:
                barrier.wait()
        return result
    monkeypatch.setattr(QuerySet, "get", synchronized_get)

    def worker(day):
        def run():
            start = datetime(2026, 10, day, 8, tzinfo=COPENHAGEN_TZ)
            try:
                build_scheduling_service().reschedule_single_appointment(
                    appointment.pk, AvailableWindow(appointment.slot.space_id, start, start + timedelta(minutes=270))
                )
                return "saved"
            except (ValueError, SlotUnavailableError):
                return "rejected"
        return run
    # Resolve lazy relations on the main connection before starting workers.
    appointment.slot
    outcomes = run_workers([worker(27), worker(28)])
    assert "saved" in outcomes
    active = models.TreatmentSlot.objects.filter(is_active=True)
    referenced = models.TreatmentAppointment.objects.filter(is_active=True).values_list("slot_id", flat=True)
    assert not active.exclude(pk__in=referenced).exists(), "Concurrent reschedules left an orphan slot"
    assert active.count() == 1


@pytest.mark.regression
def test_concurrent_registration_reuses_same_patient(transactional_db, monkeypatch):
    from infra.django_Repositories import DjangoPatientRepository
    from patients.PatientService import PatientService
    barrier = Barrier(2, timeout=5)
    original = DjangoPatientRepository.get_by_cpr
    import threading
    local = threading.local()
    def synchronized_lookup(repo, cpr):
        found = original(repo, cpr)
        if not getattr(local, "seen", False):
            local.seen = True
            barrier.wait()
        return found
    monkeypatch.setattr(DjangoPatientRepository, "get_by_cpr", synchronized_lookup)
    def worker():
        return PatientService(DjangoPatientRepository()).register_or_reactivate(
            "Test", "Patient", "0000000000", "unassigned"
        ).id
    ids = run_workers([worker, worker])
    assert ids[0] == ids[1]
    assert models.Patient.objects.count() == 1
