from datetime import datetime, time
import pytest
from django.utils import timezone
from infra import models
from infra.services import build_scheduling_service
from scheduling.models import COPENHAGEN_TZ


@pytest.fixture
def now(monkeypatch):
    value = datetime(2026, 10, 19, 8, tzinfo=COPENHAGEN_TZ)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return value.astimezone(tz) if tz else value.replace(tzinfo=None)

    monkeypatch.setattr("scheduling.scheduling.datetime", Clock)
    monkeypatch.setattr(timezone, "now", lambda: value)
    return value


@pytest.fixture
def room(db):
    room = models.TreatmentSpace.objects.create(name="Test room")
    for weekday in range(5):
        models.SpaceSchedule.objects.create(
            space=room, weekday=weekday, open_time=time(8), close_time=time(18)
        )
    return room


@pytest.fixture
def patient(db):
    # Synthetic format-only identifier, never real patient data.
    return models.Patient.objects.create(
        first_name="Test", last_name="Patient", CPR_number="0000000000"
    )


@pytest.fixture
def course(patient):
    return models.TreatmentCourse.objects.create(patient=patient, planned_treatments=2)


@pytest.fixture
def appointment(course, room):
    slot = models.TreatmentSlot.objects.create(
        space=room,
        start_time=datetime(2026, 10, 26, 8, tzinfo=COPENHAGEN_TZ),
        end_time=datetime(2026, 10, 26, 12, 30, tzinfo=COPENHAGEN_TZ),
    )
    return models.TreatmentAppointment.objects.create(
        course=course, slot=slot, treatment_number=1
    )


@pytest.fixture
def service():
    return build_scheduling_service()


@pytest.fixture
def staff_client(client, django_user_model):
    # Superuser bypass is intentional for business-flow tests. Role provisioning
    # is checked separately and must not be masked by creating the permission here.
    user = django_user_model.objects.create_superuser("test_admin", password="test-password")
    client.force_login(user)
    return client


@pytest.fixture
def viewer_client(client, django_user_model):
    user = django_user_model.objects.create_user("viewer", password="test-password")
    client.force_login(user)
    return client
