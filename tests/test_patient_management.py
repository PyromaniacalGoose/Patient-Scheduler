"""Regression coverage for patient management, closures and export access."""
from datetime import date

import pytest
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.test import Client
from django.urls import reverse

from infra import models
from infra.django_Repositories import DjangoPatientRepository

pytestmark = [pytest.mark.django_db, pytest.mark.regression]


@pytest.fixture
def scheduler_client(client, django_user_model):
    """Exercise an ordinary booking user without the superuser bypass."""
    content_type = ContentType.objects.get_for_model(models.TreatmentAppointment)
    permission, _ = Permission.objects.get_or_create(
        content_type=content_type,
        codename="can_book_appointments",
        defaults={"name": "Can book appointments"},
    )
    user = django_user_model.objects.create_user("patient_scheduler", password="test-password")
    user.user_permissions.add(permission)
    client.force_login(user)
    return client


@pytest.mark.parametrize("query", [
    "test", "PATIENT", "Patient Test", "  Test   Patient  ", "000000-0000", "000000 0000",
])
def test_patient_search_matches_name_or_formatted_cpr(viewer_client, patient, query):
    models.Patient.objects.create(first_name="Other", last_name="Person", CPR_number="1111111111")
    response = viewer_client.get(reverse("patient_list"), {"q": query})
    assert response.status_code == 200
    assert [item.id for item in response.context["patients"]] == [patient.pk]


def test_patient_search_excludes_inactive_and_handles_no_matches(viewer_client, patient):
    patient.is_active = False
    patient.save()
    response = viewer_client.get(reverse("patient_list"), {"q": "Test"})
    assert response.context["patients"] == []
    assert b"No active patients match your search." in response.content


@pytest.mark.parametrize("route", [
    "deactivate_patient", "deactivate_patients_without_course",
])
def test_deactivation_requires_login_and_booking_permission(viewer_client, patient, route):
    kwargs = {"patient_id": patient.pk} if route == "deactivate_patient" else {}
    url = reverse(route, kwargs=kwargs)
    for method in ("get", "post"):
        anonymous = getattr(Client(), method)(url)
        assert anonymous.status_code == 302
        assert anonymous.url.startswith(reverse("login") + "?next=")
        assert getattr(viewer_client, method)(url, {"confirm": "yes"}).status_code == 403
    patient.refresh_from_db()
    assert patient.is_active


def test_single_deactivation_confirmation_preserves_bookings(scheduler_client, appointment):
    patient = appointment.course.patient
    url = reverse("deactivate_patient", kwargs={"patient_id": patient.pk})
    response = scheduler_client.get(url)
    assert response.status_code == 200
    assert b"Existing courses and appointments stay booked" in response.content
    patient.refresh_from_db()
    assert patient.is_active

    assert scheduler_client.post(url).status_code == 302
    assert scheduler_client.post(url).status_code == 302  # Safe to retry.
    patient.refresh_from_db()
    appointment.refresh_from_db()
    appointment.course.refresh_from_db()
    appointment.slot.refresh_from_db()
    assert not patient.is_active
    assert appointment.is_active and appointment.course.is_active and appointment.slot.is_active
    assert not DjangoPatientRepository().get_active_patients()

    events = scheduler_client.get(reverse("calendar_events"), {
        "start": "2026-10-26T00:00:00+01:00", "end": "2026-10-27T00:00:00+01:00",
    }).json()
    event, = events
    assert patient.first_name in event["title"]
    assert event["extendedProps"]["appointment_id"] == appointment.pk


def test_deactivation_rejects_missing_patient_and_wrong_method(scheduler_client, patient):
    assert scheduler_client.post(reverse("deactivate_patient", kwargs={"patient_id": 999999})).status_code == 404
    assert scheduler_client.put(reverse("deactivate_patient", kwargs={"patient_id": patient.pk})).status_code == 405


@pytest.mark.parametrize("status,course_active,protected", [
    (models.CourseStatus.PLANNED, True, True),
    (models.CourseStatus.ACTIVE, True, True),
    (models.CourseStatus.COMPLETED, True, False),
    (models.CourseStatus.CANCELLED, True, False),
    (models.CourseStatus.PLANNED, False, False),
    (models.CourseStatus.ACTIVE, False, False),
    (None, None, False),
])
def test_bulk_deactivation_eligibility(patient, status, course_active, protected):
    if status is not None:
        models.TreatmentCourse.objects.create(patient=patient, status=status, is_active=course_active)
    repo = DjangoPatientRepository()
    assert repo.count_patients_without_active_course() == int(not protected)
    assert repo.deactivate_patients_without_active_course() == int(not protected)
    patient.refresh_from_db()
    assert patient.is_active == protected


def test_one_active_course_protects_patient_with_completed_courses(patient):
    models.TreatmentCourse.objects.create(patient=patient, status=models.CourseStatus.COMPLETED)
    models.TreatmentCourse.objects.create(patient=patient, status=models.CourseStatus.ACTIVE)
    assert DjangoPatientRepository().deactivate_patients_without_active_course() == 0
    patient.refresh_from_db()
    assert patient.is_active


@pytest.mark.parametrize("confirmation", [{}, {"confirm": "no"}])
def test_bulk_requires_explicit_confirmation(scheduler_client, patient, confirmation):
    url = reverse("deactivate_patients_without_course")
    preview = scheduler_client.get(url)
    assert preview.context["count"] == 1
    assert b"outside your current search" in preview.content
    assert b'name="confirm"' in preview.content
    assert scheduler_client.post(url, confirmation).status_code == 400
    patient.refresh_from_db()
    assert patient.is_active


def test_bulk_rechecks_courses_after_preview_and_reports_actual_count(scheduler_client, patient):
    other = models.Patient.objects.create(first_name="Other", last_name="Patient", CPR_number="1111111111")
    url = reverse("deactivate_patients_without_course")
    assert scheduler_client.get(url).context["count"] == 2
    models.TreatmentCourse.objects.create(patient=patient, status=models.CourseStatus.PLANNED)
    response = scheduler_client.post(url, {"confirm": "yes"})
    assert response.status_code == 200 and response.context["deactivated_count"] == 1
    patient.refresh_from_db()
    other.refresh_from_db()
    assert patient.is_active and not other.is_active
    assert scheduler_client.post(url, {"confirm": "yes"}).context["deactivated_count"] == 0


def test_bulk_ignores_inactive_patients_and_preserves_history(scheduler_client, appointment):
    course = appointment.course
    course.status = models.CourseStatus.COMPLETED
    course.save()
    inactive = models.Patient.objects.create(
        first_name="Inactive", last_name="Patient", CPR_number="1111111111", is_active=False,
    )
    updated_at = inactive.updated_at
    response = scheduler_client.post(reverse("deactivate_patients_without_course"), {"confirm": "yes"})
    assert response.context["deactivated_count"] == 1
    inactive.refresh_from_db()
    assert inactive.updated_at == updated_at
    appointment.refresh_from_db()
    course.refresh_from_db()
    appointment.slot.refresh_from_db()
    assert appointment.is_active and course.is_active and appointment.slot.is_active


def test_bulk_form_includes_csrf_protection(scheduler_client, patient):
    client = Client(enforce_csrf_checks=True)
    client.cookies = scheduler_client.cookies.copy()
    url = reverse("deactivate_patients_without_course")
    response = client.get(url)
    assert b"csrfmiddlewaretoken" in response.content
    assert client.post(url, {"confirm": "yes"}).status_code == 403
    patient.refresh_from_db()
    assert patient.is_active


def test_closure_events_include_reasons_and_exclusive_end_dates(viewer_client, room):
    models.ScheduleClosure.objects.create(date=date(2026, 10, 26), reason="Public holiday")
    models.ScheduleClosure.objects.create(date=date(2026, 10, 27), space=room, reason="Room maintenance")
    models.ScheduleClosure.objects.create(date=date(2026, 10, 28), reason="Outside range")
    response = viewer_client.get(reverse("calendar_events"), {
        "start": "2026-10-26T00:00:00+01:00", "end": "2026-10-28T00:00:00+01:00",
    })
    assert response.status_code == 200
    events = {event["start"]: event for event in response.json()}
    assert set(events) == {"2026-10-26", "2026-10-27"}
    assert events["2026-10-26"]["title"] == "Hospital closed: Public holiday"
    assert events["2026-10-27"]["title"] == f"{room.name} closed: Room maintenance"
    assert events["2026-10-27"]["end"] == "2026-10-28"
    assert all(event["allDay"] and event["extendedProps"]["closure"] for event in events.values())


@pytest.mark.parametrize("route", ["export_schedule", "export_scheduling_data"])
def test_exports_require_login_and_reject_viewers(viewer_client, route):
    url = reverse(route)
    assert Client().get(url).status_code == 302
    assert viewer_client.get(url).status_code == 403


def test_full_export_requires_superuser(scheduler_client):
    assert scheduler_client.get(reverse("export_scheduling_data")).status_code == 403


@pytest.mark.parametrize("query", [{"days": "30"}, {"start": "invalid"}, {"start": "9999-12-31"}])
def test_schedule_export_rejects_invalid_options(scheduler_client, query):
    assert scheduler_client.get(reverse("export_schedule"), query).status_code == 400


def test_bulk_with_no_eligible_patients_shows_no_confirmation_button(scheduler_client, course):
    response = scheduler_client.get(reverse("deactivate_patients_without_course"))
    assert response.status_code == 200
    assert response.context["count"] == 0
    assert b"Confirm bulk deactivation" not in response.content
    assert scheduler_client.put(reverse("deactivate_patients_without_course")).status_code == 405
