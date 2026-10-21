"""HTTP contracts use real middleware, templates, sessions and repositories."""
from datetime import date, timedelta
import pytest
from django.contrib.auth.models import Group, Permission
from django.test import Client
from django.urls import reverse
from infra import models

pytestmark = pytest.mark.django_db

READ_ROUTES = [
    ("calendar_page", {}), ("calendar_events", {}), ("calendar_availability", {}),
    ("patient_list", {}), ("patient_detail", {"patient_id": 999999}),
    ("appointment_detail_page", {"appointment_id": 999999}),
    ("course_detail", {"course_id": 999999}),
]
WRITE_ROUTES = [
    ("register_patient", {}), ("start_course", {}), ("review_course", {}),
    ("schedule_management", {}), ("review_cascade", {}),
    ("appointment_alternatives", {"appointment_index": 0}),
    ("select_window", {"appointment_index": 0}),
    ("reschedule_alternatives", {"appointment_id": 999999}),
    ("select_reschedule_window", {"appointment_id": 999999}),
    ("reschedule_appointment_page", {"appointment_id": 999999}),
    ("start_cascade", {"appointment_id": 999999}),
    ("cascade_alternatives", {"cascade_index": 0}),
    ("select_cascade_window", {"cascade_index": 0}),
]


@pytest.mark.parametrize("name,kwargs", READ_ROUTES + WRITE_ROUTES)
def test_anonymous_requests_redirect_to_login(client, name, kwargs):
    response = client.get(reverse(name, kwargs=kwargs))
    assert response.status_code == 302
    assert response.url.startswith(reverse("login") + "?next=")


@pytest.mark.parametrize("name,kwargs", WRITE_ROUTES)
def test_viewer_cannot_access_booking_routes(viewer_client, name, kwargs):
    assert viewer_client.get(reverse(name, kwargs=kwargs)).status_code == 403
    assert viewer_client.post(reverse(name, kwargs=kwargs), {}).status_code == 403


@pytest.mark.regression
def test_scheduler_group_has_booking_permission():
    permission = Permission.objects.filter(content_type__app_label="infra", codename="can_book_appointments").first()
    assert permission is not None, "Declare can_book_appointments in a model Meta and migrate"
    assert Group.objects.get(name="Scheduler").permissions.filter(pk=permission.pk).exists()
    assert not Group.objects.get(name="Viewer").permissions.filter(pk=permission.pk).exists()


@pytest.mark.parametrize("name", ["calendar_events", "calendar_availability"])
def test_empty_calendar_returns_empty_json(viewer_client, name):
    response = viewer_client.get(reverse(name), {"start": "2026-10-26T00:00:00+01:00", "end": "2026-10-27T00:00:00+01:00"})
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.regression
@pytest.mark.parametrize("name", ["calendar_events", "calendar_availability"])
@pytest.mark.parametrize("query", [
    {}, {"start": "invalid", "end": "invalid"},
    {"start": "2026-10-27T00:00:00+01:00", "end": "2026-10-26T00:00:00+01:00"},
    {"start": "2026-10-26", "end": "2026-10-27"},
])
def test_calendar_rejects_invalid_ranges(viewer_client, name, query):
    assert viewer_client.get(reverse(name), query).status_code == 400


@pytest.mark.regression
@pytest.mark.parametrize("name,kwargs", [
    ("patient_detail", {"patient_id": 999999}),
    ("appointment_detail_page", {"appointment_id": 999999}),
    ("course_detail", {"course_id": 999999}),
])
def test_unknown_detail_returns_404(viewer_client, name, kwargs):
    assert viewer_client.get(reverse(name, kwargs=kwargs)).status_code == 404


def test_calendar_event_contains_correct_appointment(viewer_client, appointment):
    response = viewer_client.get(reverse("calendar_events"), {
        "start": "2026-10-26T00:00:00+01:00", "end": "2026-10-27T00:00:00+01:00",
    })
    assert response.status_code == 200
    event, = response.json()
    assert event["extendedProps"]["appointment_id"] == appointment.pk
    assert str(appointment.course.patient.patient_number) in event["title"]
    assert "CPR" not in event["title"]
    assert appointment.course.patient.CPR_number not in response.content.decode()


@pytest.mark.regression
def test_calendar_handles_active_slot_without_appointment(viewer_client, room, now):
    models.TreatmentSlot.objects.create(space=room, start_time=now, end_time=now + timedelta(hours=1))
    response = viewer_client.get(reverse("calendar_events"), {
        "start": "2026-10-19T00:00:00+02:00", "end": "2026-10-20T00:00:00+02:00",
    })
    assert response.status_code == 200
    event, = response.json()
    assert event["extendedProps"]["appointment_id"] is None


def test_registration_is_idempotent(staff_client):
    payload = {"first_name": "Test", "last_name": "Patient", "cpr_number": "0000000000", "gender": "female"}
    first = staff_client.post(reverse("register_patient"), payload)
    second = staff_client.post(reverse("register_patient"), payload)
    assert first.status_code == second.status_code == 302
    assert first.url == second.url
    assert models.Patient.objects.count() == 1


@pytest.mark.regression
@pytest.mark.parametrize("changes", [
    {"cpr_number": "bad"}, {"first_name": ""}, {"gender": "unknown"},
    {"last_name": "x" * 61},
])
def test_invalid_registration_is_rejected_without_writes(staff_client, changes):
    payload = {"first_name": "Test", "last_name": "Patient", "cpr_number": "0000000000", "gender": "female"}
    payload.update(changes)
    response = staff_client.post(reverse("register_patient"), payload)
    assert response.status_code in (200, 400, 422)
    assert models.Patient.objects.count() == 0


def test_review_without_pending_session_redirects(staff_client):
    assert staff_client.get(reverse("review_course")).url == reverse("start_course")
    assert staff_client.get(reverse("review_cascade")).url == reverse("calendar_page")


def test_course_planning_does_not_write_until_confirmation(staff_client, patient, room, now):
    response = staff_client.post(reverse("start_course"), {
        "patient_number": patient.patient_number, "appointment_count": 2,
        "min_interval_days": 56, "soft_preferred_days": 77,
        "treatment_type": 1, "earliest_start": "2026-10-26",
    })
    assert response.status_code == 302
    assert response.url == reverse("review_course")
    assert len(staff_client.session["pending_course"]["windows"]) == 2
    assert models.TreatmentCourse.objects.count() == models.TreatmentSlot.objects.count() == 0
    assert staff_client.get(reverse("review_course")).status_code == 200
    confirmed = staff_client.post(reverse("review_course"), {"note_0": "test note"})
    assert confirmed.status_code == 302
    assert models.TreatmentAppointment.objects.count() == 2
    assert models.TreatmentAppointment.objects.get(treatment_number=1).note == "test note"
    assert "pending_course" not in staff_client.session
    # A browser retry cannot create another course from the consumed draft.
    assert staff_client.post(reverse("review_course")).status_code == 302
    assert models.TreatmentCourse.objects.count() == 1


@pytest.mark.regression
def test_clearing_weekday_removes_existing_rule(staff_client, room):
    response = staff_client.post(reverse("schedule_management"), {
        "action": "save_weekly_schedule", "space_id": room.pk,
        **{f"{kind}_{day}": ("08:00" if kind == "open" else "18:00")
           for day in range(1, 5) for kind in ("open", "close")},
        "open_0": "", "close_0": "",
    })
    assert response.status_code == 302
    assert not models.SpaceSchedule.objects.filter(space=room, weekday=0).exists()
    assert models.SpaceSchedule.objects.filter(space=room).count() == 4


@pytest.mark.regression
def test_weekly_schedule_invalid_last_day_does_not_partially_save(staff_client, room):
    original = list(models.SpaceSchedule.objects.filter(space=room).order_by("weekday").values_list("weekday", "open_time", "close_time"))
    payload = {"action": "save_weekly_schedule", "space_id": room.pk}
    for day in range(7):
        payload.update({f"open_{day}": "09:00", f"close_{day}": "17:00"})
    payload["close_6"] = "invalid"
    response = staff_client.post(reverse("schedule_management"), payload)
    assert response.status_code in (200, 400, 422)
    assert list(models.SpaceSchedule.objects.filter(space=room).order_by("weekday").values_list("weekday", "open_time", "close_time")) == original


def test_csrf_protects_booking_confirmation(staff_client):
    protected = Client(enforce_csrf_checks=True)
    protected.cookies = staff_client.cookies
    assert protected.post(reverse("review_course"), {}).status_code == 403


@pytest.mark.regression
@pytest.mark.parametrize("name,kwargs", [
    ("select_window", {"appointment_index": 0}),
    ("select_cascade_window", {"cascade_index": 0}),
    ("select_reschedule_window", {"appointment_id": 1}),
])
def test_mutating_selection_routes_require_post(staff_client, name, kwargs):
    assert staff_client.get(reverse(name, kwargs=kwargs)).status_code == 405


@pytest.mark.regression
def test_selection_out_of_range_is_bad_request(staff_client, room):
    window = {"space_id": room.pk, "start": "2026-10-26T08:00:00+01:00", "end": "2026-10-26T12:30:00+01:00"}
    session = staff_client.session
    session["pending_course"] = {"windows": [window], "original_windows": [window], "flagged": [False]}
    session.save()
    assert staff_client.post(reverse("select_window", kwargs={"appointment_index": 99}), window).status_code == 400


@pytest.mark.regression
@pytest.mark.parametrize("field,value", [
    ("appointment_count", "0"), ("appointment_count", "-1"),
    ("min_interval_days", "0"), ("min_interval_days", "-1"),
    ("treatment_type", "99"), ("earliest_start", "invalid"),
    ("patient_number", "not-a-number"),
])
def test_invalid_course_parameters_do_not_create_pending_draft(staff_client, patient, room, now, field, value):
    payload = {
        "patient_number": patient.patient_number, "appointment_count": 2,
        "min_interval_days": 56, "soft_preferred_days": 77,
        "treatment_type": 1, "earliest_start": "2026-10-26",
    }
    payload[field] = value
    response = staff_client.post(reverse("start_course"), payload)
    assert response.status_code in (200, 400, 422)
    assert "pending_course" not in staff_client.session
    assert models.TreatmentCourse.objects.count() == 0


@pytest.mark.regression
def test_unknown_patient_number_shows_error_instead_of_500(staff_client, room):
    response = staff_client.post(reverse("start_course"), {"patient_number": 999999})
    assert response.status_code in (200, 400, 404)
    assert models.TreatmentCourse.objects.count() == 0


def test_booking_conflict_keeps_pending_draft_and_shows_error(staff_client, patient, room, now):
    from datetime import datetime
    response = staff_client.post(reverse("start_course"), {
        "patient_number": patient.patient_number, "appointment_count": 2,
        "min_interval_days": 56, "soft_preferred_days": 77,
        "treatment_type": 1, "earliest_start": "2026-10-26",
    })
    assert response.status_code == 302
    draft = staff_client.session["pending_course"]
    taken = draft["windows"][1]
    models.TreatmentSlot.objects.create(space_id=taken["space_id"], start_time=datetime.fromisoformat(taken["start"]), end_time=datetime.fromisoformat(taken["end"]))
    response = staff_client.post(reverse("review_course"))
    assert response.status_code == 200
    assert response.context["error"]
    assert staff_client.session["pending_course"] == draft
    assert models.TreatmentCourse.objects.count() == models.TreatmentAppointment.objects.count() == 0
    assert models.TreatmentSlot.objects.count() == 1


@pytest.mark.regression
def test_availability_uses_copenhagen_hours_when_calendar_sends_utc(viewer_client, room):
    from datetime import datetime
    from scheduling.models import COPENHAGEN_TZ
    response = viewer_client.get(reverse("calendar_availability"), {
        "start": "2026-10-26T00:00:00+00:00", "end": "2026-10-27T00:00:00+00:00",
    })
    assert response.status_code == 200
    event, = response.json()
    assert datetime.fromisoformat(event["start"]).astimezone(COPENHAGEN_TZ).hour == 8
    assert datetime.fromisoformat(event["end"]).astimezone(COPENHAGEN_TZ).hour == 18


@pytest.mark.regression
def test_expired_reschedule_shows_client_error_and_does_not_write(staff_client, appointment, now):
    appointment.slot.start_time = now - timedelta(days=8)
    appointment.slot.end_time = appointment.slot.start_time + timedelta(minutes=270)
    appointment.slot.save()
    original_slot = appointment.slot_id
    response = staff_client.post(reverse("select_reschedule_window", kwargs={"appointment_id": appointment.pk}), {
        "space_id": appointment.slot.space_id,
        "start": "2026-10-27T08:00:00+01:00", "end": "2026-10-27T12:30:00+01:00",
    })
    assert response.status_code in (400, 409, 422)
    appointment.refresh_from_db()
    assert appointment.slot_id == original_slot
    assert models.TreatmentSlot.objects.count() == 1
