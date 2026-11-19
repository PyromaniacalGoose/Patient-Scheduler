# infra/views.py
from datetime import UTC, date, datetime, time, timedelta
from collections import defaultdict
import hashlib

from django.http import Http404, HttpResponseBadRequest, JsonResponse
# ERROR HANDLING: added imports
from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.views.decorators.http import require_POST, require_http_methods
from infra.models import Patient, SpaceSchedule as ORMSpaceSchedule
from infra.validators.cpr import validate_cpr
from infra.django_Repositories import DjangoCourseRepository, DjangoPatientRepository, DjangoScheduleRepository, DjangoSlotRepository, DjangoAppointmentRepository, DjangoSpaceRepository
from django.shortcuts import redirect, render
from django.contrib.auth.decorators import login_required, permission_required
from django.urls import reverse

from infra.services import build_scheduling_service
from patients.PatientService import PatientService
from scheduling.models import COPENHAGEN_TZ, TREATMENT_DURATIONS, AvailableWindow, CourseBookingFailedError, CourseStatus, LateReschedulingError, PlannedAppointment, RescheduleProposal, ScheduleClosure, ScheduleOverride, SlotUnavailableError, SpaceSchedule, TreatmentCourse, TreatmentType, RESCHEDULE_GRACE_DAYS
from scheduling.scheduling import compute_free_intervals, find_windows_for_duration


# ERROR HANDLING: shared parsing for expected input errors
def _bad_request(message):
    return HttpResponseBadRequest(message, content_type="text/plain; charset=utf-8")


def _aware_datetime(value, label):
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.utcoffset() is None:
            raise ValueError
        return parsed.astimezone(COPENHAGEN_TZ)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{label} must be a date and time with a timezone.") from None


def _calendar_range(query):
    start = _aware_datetime(query.get("start"), "Start")
    end = _aware_datetime(query.get("end"), "End")
    if end.astimezone(UTC) <= start.astimezone(UTC):
        raise ValueError("End must be after start.")
    return start, end


def _selected_window(post):
    try:
        space_id = int(post.get("space_id", ""))
    except ValueError:
        raise ValueError("Select a valid treatment room.") from None
    if not 0 < space_id <= 2**63 - 1 or DjangoSpaceRepository().get_by_id(space_id) is None:
        raise ValueError("The selected treatment room no longer exists.")
    start = _aware_datetime(post.get("start"), "Start")
    end = _aware_datetime(post.get("end"), "End")
    if end.astimezone(UTC) <= start.astimezone(UTC):
        raise ValueError("End must be after start.")
    return AvailableWindow(space_id=space_id, start_time=start, end_time=end)


def _hours(open_raw, close_raw):
    try:
        opens, closes = time.fromisoformat(open_raw), time.fromisoformat(close_raw)
    except ValueError:
        raise ValueError("Enter both opening and closing times.") from None
    if opens.tzinfo is not None or closes.tzinfo is not None or closes <= opens:
        raise ValueError("Closing time must be after opening time on the same day.")
    return opens, closes


WEEKDAYS = [
    (0, "Monday"),
    (1, "Tuesday"),
    (2, "Wednesday"),
    (3, "Thursday"),
    (4, "Friday"),
    (5, "Saturday"),
    (6, "Sunday"),
]

# Helper method to give each patient a unique and consistant appointment color
def patient_color(patient_number: str) -> str:
    digest = hashlib.sha256(
        str(patient_number).encode()
    ).hexdigest()

    hue = int(digest[:8], 16) % 360

    return f"hsl({hue}, 65%, 55%)"

@login_required
def calendar_page(request):
    return render(request, "calendar.html")

@login_required
def calendar_events(request):
    space_repo = DjangoSpaceRepository()
    slot_repo = DjangoSlotRepository()
    appointment_repo = DjangoAppointmentRepository()
    course_repo = DjangoCourseRepository()
    patient_repo = DjangoPatientRepository()

    # ERROR HANDLING: malformed ranges return JSON 400; opening hours use Copenhagen time.
    try:
        start, end = _calendar_range(request.GET)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    events = []
    spaces = space_repo.get_all()

    slots = []
    
    for space in spaces:
        slots.extend(
            slot_repo.get_booked_in_range(
                space.id,
                start,
                end,
            )
        )
    
    appointments = appointment_repo.get_by_slot_ids(
        [slot.id for slot in slots]
    )
    
    courses = course_repo.get_by_ids(
        list({appointment.course_id for appointment in appointments})
    )
    
    # PATIENT DEACTIVATION: Existing bookings still show the patient's name.
    patients = Patient.objects.filter(pk__in={course.patient_id for course in courses})
    # Dicts
    appointments_by_slot = {
        appointment.slot_id: appointment
        for appointment in appointments
    }

    courses_by_id = {
        course.id: course
        for course in courses
    }

    patients_by_id = {
        patient.id: patient
        for patient in patients
    }

    spaces_by_id = {
        s.id: s 
        for s in spaces
    }
        
    for slot in slots:
        appt = appointments_by_slot.get(slot.id)
        course = courses_by_id.get(appt.course_id) if appt else None
        space = spaces_by_id.get(slot.space_id)
        patient = patients_by_id.get(course.patient_id) if course else None
        title = f"{patient.first_name} {patient.last_name}: ({patient.patient_number}) in {space.name}: Treatment #{appt.treatment_number}" if appt and patient else f"{space.name}: Blocked"
        events.append({
            "id": slot.id,
            "start": slot.start_time.isoformat(),
            "end": slot.end_time.isoformat(),
            "title": title,
            "color": patient_color(patient.patient_number) if patient else "#888888",
            "extendedProps": {
                "appointment_id": appt.id if appt else None,
            },
        })

    # CLOSURE EVENTS: FullCalendar treats the end date as exclusive.
    closures = DjangoScheduleRepository().get_closures(start.date(), end.date())
    for index, closure in enumerate(closures):
        day_start = datetime.combine(closure.date, time.min, COPENHAGEN_TZ)
        day_end = datetime.combine(closure.date + timedelta(days=1), time.min, COPENHAGEN_TZ)
        if day_start >= end or day_end <= start:
            continue
        space = spaces_by_id.get(closure.space_id)
        label = f"{space.name} closed" if space else "Hospital closed"
        events.append({
            "id": f"closure-{closure.date}-{closure.space_id}-{index}",
            "start": closure.date.isoformat(),
            "end": (closure.date + timedelta(days=1)).isoformat(),
            "allDay": True,
            "title": f"{label}: {closure.reason}" if closure.reason else label,
            "color": "#8B3434",
            "extendedProps": {"closure": True},
        })

    return JsonResponse(events, safe=False)

@login_required
def calendar_availability(request):
    space_repo = DjangoSpaceRepository()
    schedule_repo = DjangoScheduleRepository()
    slot_repo = DjangoSlotRepository()
    # ERROR HANDLING: malformed ranges return JSON 400; opening hours use Copenhagen time.
    try:
        start, end = _calendar_range(request.GET)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    

    space_ids = [s.id for s in space_repo.get_all()]
    rules = []
    for sp_id in space_ids:
        rules.extend(schedule_repo.get_rules_for_space(sp_id))
    overrides = schedule_repo.get_schedule_overrides(start.date(), end.date())
    closures = schedule_repo.get_closures(start.date(), end.date())
    booked = []
    for sp_id in space_ids:
        booked.extend(slot_repo.get_booked_in_range(sp_id, start, end))

    free_intervals = compute_free_intervals(rules, overrides, closures, booked, start, end, space_ids)

    # FullCalendar background events — one per free interval
    events = [
        {
            "start": fi.start_time.isoformat(),
            "end": fi.end_time.isoformat(),
            "display": "background",
            "color": "#ffffff", 
        }
        for fi in free_intervals
    ]
    return JsonResponse(events, safe=False)

@login_required
def appointment_detail_page(request, appointment_id):
    appointment_repo = DjangoAppointmentRepository()
    slot_repo = DjangoSlotRepository()
    space_repo = DjangoSpaceRepository()
    course_repo = DjangoCourseRepository()
    patient_repo = DjangoPatientRepository()

    appt = appointment_repo.get_by_id(appointment_id)
    if appt is None:
        raise Http404
    slot = slot_repo.get_by_id(appt.slot_id)
    # ERROR HANDLING: missing related records cannot be displayed.
    if slot is None:
        raise Http404("Appointment slot not found.")
    space = space_repo.get_by_id(slot.space_id)
    course = course_repo.get_by_id(appt.course_id)
    if space is None or course is None:
        raise Http404("Appointment details are no longer available.")
    try:
        patient = patient_repo.get_by_id(course.patient_id)
    except Patient.DoesNotExist:
        patient = None
    if patient is None:
        raise Http404("Patient not found.")
    now = timezone.now()
    can_reschedule = (
        slot.start_time >= now - timedelta(days = RESCHEDULE_GRACE_DAYS)
    )

    return render(request, "appointment_detail.html", {
        "appointment": appt, 
        "slot": slot, 
        "space": space, 
        "course": course, 
        "patient": patient,
        "can_reschedule": can_reschedule
    })


@login_required
@permission_required("infra.can_book_appointments", raise_exception=True)
def register_patient(request):
    if request.method == "POST":
        patient_service = PatientService(DjangoPatientRepository())
        # ERROR HANDLING: validate all fields before registering or reactivating.
        first = request.POST.get("first_name", "").strip()
        last = request.POST.get("last_name", "").strip()
        cpr = request.POST.get("cpr_number", "").strip()
        gender = request.POST.get("gender")
        try:
            if not first or not last or len(first) > 30 or len(last) > 60:
                raise ValueError("Enter a first name (up to 30 characters) and last name (up to 60).")
            validate_cpr(cpr)
            if gender not in {"male", "female", "unassigned"}:
                raise ValueError("Select a valid gender.")
        except (ValueError, ValidationError) as exc:
            message = "Enter a CPR number with exactly 10 digits." if isinstance(exc, ValidationError) else str(exc)
            return render(request, "register_patient.html", {"error": message}, status=400)
        patient = patient_service.register_or_reactivate(first_name=first, last_name=last, cpr=cpr, gender=gender)
        return redirect("patient_detail", patient_id=patient.id)

    return render(request, "register_patient.html")

@login_required
def patient_detail(request, patient_id):
    patient_repo = DjangoPatientRepository()
    course_repo = DjangoCourseRepository()
    # ERROR HANDLING: support repositories that raise or return None on a miss.
    try:
        patient = patient_repo.get_by_id(patient_id)
    except Patient.DoesNotExist:
        patient = None
    if patient is None:
        raise Http404("Patient not found.")
    courses = course_repo.get_courses_by_patient_id(patient_id)

    return render(
        request,
        "patient_detail.html",
        {"patient": patient,
         "courses": courses,
         },
    )

@login_required
def patient_list(request):
    patient_repo = DjangoPatientRepository()
    search = request.GET.get("q", "").strip()
    patients = patient_repo.get_active_patients(search)

    return render(
        request,
        "patient_list.html",
        {"patients": patients, "search": search},
    )

# BULK DEACTIVATION: Explicit confirmation is required before updating patients.
@login_required
@permission_required("infra.can_book_appointments", raise_exception=True)
@require_http_methods(["GET", "POST"])
def deactivate_patients_without_course(request):
    repo = DjangoPatientRepository()
    context = {"count": repo.count_patients_without_active_course()}
    status = 200
    if request.method == "POST":
        if request.POST.get("confirm") != "yes":
            context["error"] = "Tick the confirmation box before deactivating patients."
            status = 400
        else:
            context["deactivated_count"] = repo.deactivate_patients_without_active_course()
    return render(request, "deactivate_patients_without_course.html", context, status=status)


# PATIENT DEACTIVATION: GET displays confirmation, POST performs the soft delete.
@login_required
@permission_required("infra.can_book_appointments", raise_exception=True)
@require_http_methods(["GET", "POST"])
def deactivate_patient(request, patient_id):
    try:
        patient = Patient.objects.get(pk=patient_id)
    except Patient.DoesNotExist:
        raise Http404("Patient not found.") from None
    if request.method == "POST":
        Patient.objects.filter(pk=patient_id, is_active=True).update(
            is_active=False, updated_at=timezone.now(),
        )
        return redirect("patient_list")
    return render(request, "deactivate_patient.html", {"patient": patient})


@login_required
@permission_required("infra.can_book_appointments", raise_exception=True)
def start_course(request):
    if request.method == "POST":
        service = build_scheduling_service()  # factory, see below
        patient_repo = DjangoPatientRepository()
        # ERROR HANDLING: reject incomplete or invalid planning parameters.
        try:
            patient_number = int(request.POST.get("patient_number", ""))
            count = int(request.POST.get("appointment_count", ""))
            minimum = int(request.POST.get("min_interval_days", ""))
            preferred = int(request.POST.get("soft_preferred_days", ""))
            treatment_type = TreatmentType(int(request.POST.get("treatment_type", "")))
            earliest = date.fromisoformat(request.POST.get("earliest_start", ""))
            if not 0 < patient_number <= 2**31 - 1 or not 0 < count <= 32767 or minimum < 0 or preferred < minimum:
                raise ValueError
        except ValueError:
            return render(request, "start_course.html", {
                "error": "Check the patient number, treatment count, intervals, treatment type, and start date.",
            }, status=400)
        try:
            patient = patient_repo.get_by_number(patient_number)
        except Patient.DoesNotExist:
            patient = None
        if patient is None:
            return render(request, "start_course.html", {"error": f"Patient: {patient_number} not found."})


        # ERROR HANDLING: extreme dates or intervals can overflow the planner.
        try:
            result = service.start_course(
                patient_id = patient.id,
                appointment_count=count,
                min_interval_days=minimum,
                soft_preferred_days=preferred,
                treatment_type=treatment_type,
                earliest_start=earliest,
            )
        except (ValueError, OverflowError):
            return render(request, "start_course.html", {
                "error": "The planning dates or intervals are out of range.",
            }, status=400)

        if result is None:
            return render(request, "start_course.html", {"error": "No feasible schedule found."})

        course, windows, flagged = result
        serialized_windows = [
            {
                "space_id": w.space_id,
                "start": w.start_time.isoformat(),
                "end": w.end_time.isoformat(),
            }
            for w in windows
        ]

        request.session["pending_course"] = {
            "patient_id": course.patient_id,
            "planned_treatments": course.planned_treatments,
            "treatment_type": int(treatment_type),
            "original_windows": serialized_windows,
            "windows": serialized_windows.copy(),
            "flagged": flagged,
        }
        return redirect("review_course")

    return render(request, "start_course.html")


@login_required
@permission_required("infra.can_book_appointments", raise_exception=True)
def review_course(request):
    pending = request.session.get("pending_course")
    if pending is None:
        return redirect("start_course")
    if request.method == "POST":
        service = build_scheduling_service()
        # ERROR HANDLING: check note length and draft references before booking.
        if not pending["windows"]:
            return _bad_request("There are no appointments to book.")
        if any(len(request.POST.get(f"note_{i}", "")) > 200 for i in range(len(pending["windows"]))):
            return _bad_request("Appointment notes may contain at most 200 characters.")
        appointments = build_review_appointments(pending)
        try:
            if DjangoPatientRepository().get_by_id(pending["patient_id"]) is None:
                raise Http404("The patient for this draft is no longer available.")
        except Patient.DoesNotExist:
            raise Http404("The patient for this draft is no longer available.") from None
        try:
            treatment_type = TreatmentType(pending["treatment_type"])
            planned = [
                PlannedAppointment(
                    window=AvailableWindow(
                        space_id=row["window"]["space_id"],
                        start_time=row["start"], end_time=row["end"],
                    ),
                    treatment_type=treatment_type,
                    note=request.POST.get(f"note_{i}", ""),
                )
                for i, row in enumerate(appointments)
            ]
        except ValueError as exc:
            return _bad_request(str(exc))

        # This is the DOMAIN TreatmentCourse.
        course = TreatmentCourse(
            id=None,
            patient_id=pending["patient_id"],
            planned_treatments=len(planned),
            status=CourseStatus.PLANNED,
        )

        try:
            saved_course = service.book_course(
                course,
                planned,
                len(planned),
            )
        except CourseBookingFailedError as e:
            return render(
                request,
                "review_course.html",
                {
                    "pending": pending,
                    "appointments": appointments,
                    "error": (
                        f"Slot no longer available at appointment "
                        f"#{e.failed_at_appointment}, please re-plan."
                    ),
                },
            )

        del request.session["pending_course"]

        return redirect(
            "course_detail",
            course_id=saved_course.id,
        )
    appointments = build_review_appointments(pending)
    return render(
        request,
        "review_course.html",
        {
            "pending": pending,
            "appointments": appointments,
        },
    )

# Helper method
def build_review_appointments(pending):
    space_repo = DjangoSpaceRepository()
    appointments = []

    for i, window in enumerate(pending["windows"]):
        original = pending["original_windows"][i]

        # ERROR HANDLING: drafts saved before input validation can be invalid.
        try:
            start = _aware_datetime(window["start"], "Start")
            end = _aware_datetime(window["end"], "End")
            original_start = _aware_datetime(original["start"], "Original start")
        except ValueError:
            raise Http404("This course draft has invalid dates. Re-plan the course.") from None

        difference_days = (
            start.date() - original_start.date()
        ).days

        space = space_repo.get_by_id(window["space_id"])
        # ERROR HANDLING: the draft cannot be displayed after its room is removed.
        if space is None:
            raise Http404("A treatment room in this draft no longer exists. Re-plan the course.")

        appointments.append({
            "index": i,
            "alternatives_base_url": reverse("appointment_alternatives", kwargs={"appointment_index": i}),
            "window": window,
            "start": start,
            "end": end,
            "space_name": space.name,
            "difference_days": difference_days,
            "difference_absolute": abs(difference_days),
            "flagged": pending["flagged"][i],
        })

    return appointments

@login_required
@permission_required("infra.can_book_appointments", raise_exception=True)
def appointment_alternatives(request, appointment_index=None, appointment_id=None, cascade_index=None):
    if cascade_index is not None:
        # cascade appointments context 
        pending = request.session.get("pending_cascade")
        if pending is None:
            return redirect("calendar_page")
        if cascade_index < 0 or cascade_index >= len(pending):
            return _bad_request("Invalid cascade index.")
        proposed = pending[cascade_index]
        default_date_source = datetime.fromisoformat(proposed["start"]).date()
        treatment_type = TreatmentType(proposed["treatment_type"])
        select_target = "#cascade-rows"
        select_swap = "outerHTML"
        context_id = cascade_index
        alternatives_base_url = reverse("cascade_alternatives", kwargs={"cascade_index": cascade_index})
        select_url = reverse("select_cascade_window", kwargs={"cascade_index": cascade_index})
    elif appointment_id is not None:
        # single-appointment reschedule context
        appointment_repo = DjangoAppointmentRepository()
        slot_repo = DjangoSlotRepository()

        appt = appointment_repo.get_by_id(appointment_id)
        if appt is None:
            raise Http404

        current_slot = slot_repo.get_by_id(appt.slot_id)
        # ERROR HANDLING: the original slot may no longer be available.
        if current_slot is None:
            raise Http404("Appointment slot not found.")

        context_id = appointment_id

        default_date_source = current_slot.start_time.date()
        treatment_type = appt.type

        select_target = f"#proposed-{context_id}"
        select_swap = "outerHTML"

        alternatives_base_url = reverse(
            "reschedule_alternatives",
            kwargs={"appointment_id": appointment_id},
        )
        select_url = reverse(
            "select_reschedule_window",
            kwargs={"appointment_id": appointment_id},
        )

    else:
        # course-review context
        pending = request.session.get("pending_course")

        if pending is None:
            return redirect("start_course")

        if appointment_index < 0 or appointment_index >= len(pending["windows"]):
            return _bad_request("Invalid appointment index.")

        proposed = pending["windows"][appointment_index]

        context_id = appointment_index

        default_date_source = datetime.fromisoformat(proposed["start"]).date()
        treatment_type = TreatmentType(pending["treatment_type"])

        select_target = f"#proposed-{context_id}"
        select_swap = "outerHTML"

        alternatives_base_url = reverse(
            "appointment_alternatives",
            kwargs={"appointment_index": appointment_index},
        )
        select_url = reverse(
            "select_window",
            kwargs={"appointment_index": appointment_index},
        )

    week_start_str = request.GET.get("week_start")
    # ERROR HANDLING: check the whole navigation range before querying.
    try:
        week_start = date.fromisoformat(week_start_str) if week_start_str else (
            default_date_source - timedelta(days=default_date_source.weekday())
        )
        week_end = week_start + timedelta(days=7)
        week_start - timedelta(days=7)
    except (ValueError, OverflowError):
        return _bad_request("Enter a valid week start date.")

    schedule_repo = DjangoScheduleRepository()
    slot_repo = DjangoSlotRepository()
    space_repo = DjangoSpaceRepository()

    space_ids = [s.id for s in space_repo.get_all()]
    rules = []

    for sp_id in space_ids:
        rules.extend(schedule_repo.get_rules_for_space(sp_id))

    tz_start = datetime.combine(week_start, time.min, tzinfo=COPENHAGEN_TZ)
    tz_end = datetime.combine(week_end, time.min, tzinfo=COPENHAGEN_TZ)
    overrides = schedule_repo.get_schedule_overrides(week_start, week_end)
    closures = schedule_repo.get_closures(week_start, week_end)
    booked = []

    for sp_id in space_ids:
        booked.extend(slot_repo.get_booked_in_range(sp_id, tz_start, tz_end))

    duration = TREATMENT_DURATIONS[treatment_type]
    free = compute_free_intervals(rules, overrides, closures, booked, tz_start, tz_end, space_ids)
    windows = find_windows_for_duration(free, duration)

    windows_by_day = {}
    for w in windows:
        windows_by_day.setdefault(w.start_time.date(), []).append(w)

    days = [
        {"date": week_start + timedelta(days=offset), "windows": windows_by_day.get(week_start + timedelta(days=offset), [])}
        for offset in range(7)
    ]

    return render(request, "_alternatives_fragment.html", {
        "days": days,
        "context_id": context_id,
        "select_target": select_target,  
        "select_swap": select_swap, 
        "alternatives_base_url": alternatives_base_url,
        "select_url": select_url,
        "week_start": week_start,
        "prev_week": week_start - timedelta(days=7),
        "next_week": week_start + timedelta(days=7),
    })

@login_required
@permission_required( "infra.can_book_appointments", raise_exception=True)
@require_POST  # ERROR HANDLING: reject accidental GET requests
def select_window(request, appointment_index):
    pending = request.session.get("pending_course")

    if pending is None:
        return redirect("start_course")

    # ERROR HANDLING: validate selection before modifying the draft.
    if not 0 <= appointment_index < len(pending["windows"]):
        return _bad_request("Invalid appointment index.")
    try:
        window = _selected_window(request.POST)
        PlannedAppointment(window=window, treatment_type=TreatmentType(pending["treatment_type"]))
    except ValueError as exc:
        return _bad_request(str(exc))
    pending["windows"][appointment_index] = {
        "space_id": window.space_id,
        "start": window.start_time.isoformat(),
        "end": window.end_time.isoformat(),
    }

    request.session["pending_course"] = pending
    request.session.modified = True

    # Build the same view-model used by review_course
    appointments = build_review_appointments(pending)

    appointment = appointments[appointment_index]

    return render(
        request,
        "_proposed_row.html",
        {
            "context_id": appointment["index"],
            "window": appointment["window"],
            "start": appointment["start"],
            "end": appointment["end"],
            "space_name": appointment["space_name"],
            "difference_days": appointment["difference_days"],
            "difference_absolute": appointment["difference_absolute"],
            "alternatives_base_url": reverse("appointment_alternatives", kwargs={"appointment_index": appointment_index}),
            "select_url": reverse("select_window",kwargs={"appointment_index": appointment_index}),
        },
    )

@login_required
@permission_required("infra.can_book_appointments", raise_exception=True)
@require_POST  # ERROR HANDLING: reject accidental GET requests
def select_reschedule_window(request, appointment_id):
    service = build_scheduling_service()
    slot_repo = DjangoSlotRepository()
    space_repo = DjangoSpaceRepository()
    appointment_repo = DjangoAppointmentRepository()

    appt = appointment_repo.get_by_id(appointment_id)
    if appt is None:
        raise Http404

    old_slot = slot_repo.get_by_id(appt.slot_id)  # fetch BEFORE rescheduling — it gets unbooked inside

    # ERROR HANDLING: report invalid input before attempting a write.
    if old_slot is None:
        raise Http404("Appointment slot not found.")
    try:
        new_window = _selected_window(request.POST)
        PlannedAppointment(window=new_window, treatment_type=appt.type)
    except ValueError as exc:
        return _bad_request(str(exc))

    try:
        updated = service.reschedule_single_appointment(
            appointment_id, new_window
        )
    except LateReschedulingError:
        return _bad_request("The original appointment is outside the rescheduling grace period.")
    except SlotUnavailableError:
        return _bad_request(
            "That slot is no longer available."
        )

    new_slot = slot_repo.get_by_id(updated.slot_id)
    space = space_repo.get_by_id(new_slot.space_id)

    return render(request, "_rescheduled_row.html", {
        "context_id": appointment_id,
        "start": new_slot.start_time,
        "end": new_slot.end_time,
        "space_name": space.name,
        "old_start": old_slot.start_time,
        "alternatives_base_url": reverse("reschedule_alternatives", kwargs={"appointment_id": appointment_id}),
    })

@login_required
@permission_required("infra.can_book_appointments", raise_exception=True)
def reschedule_appointment_page(request, appointment_id):
    appointment_repo = DjangoAppointmentRepository()
    appt = appointment_repo.get_by_id(appointment_id)
    if appt is None:
        raise Http404
    return render(request, "reschedule_appointment.html", {
        "appointment_id": appointment_id,
        "alternatives_base_url": reverse("reschedule_alternatives", kwargs={"appointment_id": appointment_id}),
    })

@login_required
@permission_required("infra.can_book_appointments", raise_exception=True)
def schedule_management(request):

    schedule_repo = DjangoScheduleRepository()
    space_repo = DjangoSpaceRepository()

    spaces = space_repo.get_all()

    if not spaces:
        return render(
            request,
            "schedule_management.html",
            {
                "spaces": [],
                "weekday_rows": [],
            },
        )

    # ERROR HANDLING: validate the complete submission before writing any rows.
    space_ids = {space.id for space in spaces}

    def read_space(raw, *, optional=False):
        if optional and not raw:
            return None
        try:
            space_id = int(raw)
        except (TypeError, ValueError):
            raise ValueError("Select a valid treatment room.") from None
        if space_id not in space_ids:
            raise ValueError("The selected treatment room no longer exists.")
        return space_id

    try:
        selected_space_id = read_space(request.GET.get("space_id", spaces[0].id))
        if request.method == "POST":
            action = request.POST.get("action")
            if action == "save_weekly_schedule":
                selected_space_id = read_space(request.POST.get("space_id"))
                rules, closed_days = [], []
                for weekday, label in WEEKDAYS:
                    opens = request.POST.get(f"open_{weekday}", "")
                    closes = request.POST.get(f"close_{weekday}", "")
                    if not opens and not closes:
                        closed_days.append(weekday)
                        continue
                    try:
                        open_time, close_time = _hours(opens, closes)
                    except ValueError as exc:
                        raise ValueError(f"{label}: {exc}") from None
                    rules.append(SpaceSchedule(
                        space_id=selected_space_id, weekday=weekday,
                        open_time=open_time, close_time=close_time,
                    ))
                with transaction.atomic():
                    ORMSpaceSchedule.objects.filter(
                        space_id=selected_space_id, weekday__in=closed_days,
                    ).delete()
                    for rule in rules:
                        schedule_repo.save_rule(rule)
                return redirect(f"{reverse('schedule_management')}?space_id={selected_space_id}")

            if action not in {"create_closure", "create_override"}:
                raise ValueError("Unknown schedule action.")
            space_id = read_space(request.POST.get("space_id"), optional=True)
            try:
                day = date.fromisoformat(request.POST.get("date", ""))
            except ValueError:
                raise ValueError("Enter a valid schedule date.") from None
            if action == "create_closure":
                reason = request.POST.get("reason", "")
                if len(reason) > 200:
                    raise ValueError("The closure reason may contain at most 200 characters.")
                schedule_repo.save_closure(ScheduleClosure(space_id=space_id, date=day, reason=reason))
            else:
                opens, closes = _hours(request.POST.get("open_time", ""), request.POST.get("close_time", ""))
                schedule_repo.save_override(ScheduleOverride(
                    space_id=space_id, date=day, open_time=opens, close_time=closes,
                ))
            return redirect("schedule_management")
    except ValueError as exc:
        return _bad_request(str(exc))

    # GET EXISTING RULES FOR SELECTED SPACE

    existing_rules = {
        rule.weekday: rule
        for rule in schedule_repo.get_rules_for_space(
            selected_space_id
        )
    }

    # Build exactly seven rows for the template
    weekday_rows = []

    for weekday_number, weekday_name in WEEKDAYS:

        rule = existing_rules.get(weekday_number)

        weekday_rows.append({
            "number": weekday_number,
            "name": weekday_name,
            "open_time": (
                rule.open_time.strftime("%H:%M")
                if rule
                else ""
            ),
            "close_time": (
                rule.close_time.strftime("%H:%M")
                if rule
                else ""
            ),
        })

    return render(
        request,
        "schedule_management.html",
        {
            "spaces": spaces,
            "selected_space_id": selected_space_id,
            "weekday_rows": weekday_rows,

            # This is an aware datetime if USE_TZ=True.
            "current_time": timezone.localtime(),
        },
    )

@login_required
def course_detail(request, course_id):
    course_repo = DjangoCourseRepository()
    appointment_repo = DjangoAppointmentRepository()
    patient_repo = DjangoPatientRepository()
    space_repo = DjangoSpaceRepository()
    slot_repo = DjangoSlotRepository()

    course = course_repo.get_by_id(course_id)
    if course is None:
        raise Http404

    patient = patient_repo.get_by_id(course.patient_id)
    if patient is None:
            raise Http404

    appointments = appointment_repo.get_planned_course_appointments(course_id)
    appointment_details = []

    for appointment in appointments:
        slot = slot_repo.get_by_id(appointment.slot_id)

        if slot is None:
            continue  # or raise Http404 / handle this as a data integrity error
        
        now = timezone.now()
        is_past = slot.end_time <= now
        can_reschedule = (
            slot.start_time >= now - timedelta(days = RESCHEDULE_GRACE_DAYS)
        )

        space = space_repo.get_by_id(slot.space_id)

        if space is None:
            continue

        appointment_details.append({
            "appointment": appointment,
            "slot": slot,
            "space": space,
            "is_past": is_past,
            "can_reschedule": can_reschedule,
        })

    return render(request, "course_detail.html", {
        "patient": patient,
        "appointments": appointment_details,
    })

def serialize_reschedule_proposal(proposal):
    return {
        "existing_appointment_id": proposal.existing_appointment_id,
        "old_slot_id": proposal.old_slot_id,
        "course_id": proposal.course_id,
        "treatment_number": proposal.treatment_number,
        "space_id": proposal.planned.window.space_id,
        "start": proposal.planned.window.start_time.isoformat(),
        "end": proposal.planned.window.end_time.isoformat(),
        "treatment_type": int(proposal.planned.treatment_type),
        "note": proposal.planned.note,
    }

@login_required
@permission_required("infra.can_book_appointments", raise_exception=True)
def start_cascade(request, appointment_id):
    service = build_scheduling_service()

    # ERROR HANDLING: surface expected cascade rejections.
    try:
        result = service.cascade_reschedule(
            appointment_id=appointment_id,
            min_interval_days=56,
            soft_preferred_days=77,
        )
    except LateReschedulingError:
        return _bad_request("The original appointment is outside the rescheduling grace period.")
    except (ValueError, OverflowError):
        return _bad_request("This appointment can no longer be replanned. Refresh the course and try again.")

    if result is None:
        return render(request, "cascade_review.html", {"error": "No feasible reschedule found.", "rows": []})

    # ERROR HANDLING: there is nothing to confirm for an empty cascade.
    proposals, flagged = result
    if not proposals:
        return _bad_request("No scheduled appointments to reschedule.")

    request.session["pending_cascade"] = [
        serialize_reschedule_proposal(p)
        for p in proposals
    ]
    request.session["pending_cascade_flagged"] = flagged

    return redirect("review_cascade")

@login_required
@permission_required("infra.can_book_appointments", raise_exception=True)
def review_cascade(request):
    pending = request.session.get("pending_cascade")
    if pending is None:
        return redirect("calendar_page")

    if request.method == "POST":
        service = build_scheduling_service()
        # ERROR HANDLING: validate old drafts before asking the service to commit.
        rows = build_cascade_rows(pending, request.session.get("pending_cascade_flagged", []))
        try:
            proposals = [
                RescheduleProposal(
                    existing_appointment_id=p["existing_appointment_id"],
                    old_slot_id=p["old_slot_id"],
                    course_id=p["course_id"],
                    treatment_number=p["treatment_number"],
                    planned=PlannedAppointment(
                        window=AvailableWindow(
                            space_id=p["space_id"],
                            start_time=datetime.fromisoformat(p["start"]),
                            end_time=datetime.fromisoformat(p["end"]),
                        ),
                        treatment_type=TreatmentType(p["treatment_type"]),
                        note=p["note"],
                    ),
                )
                for p in pending
            ]
        except (TypeError, ValueError):
            return _bad_request("This reschedule draft has invalid times. Re-plan the course.")

        # ERROR HANDLING: a draft from another tab may point to an old slot.
        appointment_repo = DjangoAppointmentRepository()
        for proposal in proposals:
            current = appointment_repo.get_by_id(proposal.existing_appointment_id)
            if current is None or (
                current.slot_id, current.course_id, current.treatment_number
            ) != (proposal.old_slot_id, proposal.course_id, proposal.treatment_number):
                return _bad_request("An appointment in this draft has changed. Open the course and re-plan.")

        try:
            course = service.book_cascade(proposals)
        # ERROR HANDLING: stale or expired cascades need a new proposal.
        except (ValueError, LateReschedulingError):
            return _bad_request("This proposal can no longer be booked. Open the course and re-plan.")
        except CourseBookingFailedError as e:
            return render(request, "cascade_review.html", {
                "rows": rows,
                "error": f"Slot no longer available for appointment #{e.failed_at_appointment}, please re-plan.",
            })

        del request.session["pending_cascade"]
        request.session.pop("pending_cascade_flagged", None)
        return redirect("course_detail", course_id=course.id)

    rows = build_cascade_rows(pending, request.session.get("pending_cascade_flagged", []))
    return render(request, "cascade_review.html", {"rows": rows})


def build_cascade_rows(pending, flagged):
    space_repo = DjangoSpaceRepository()
    slot_repo = DjangoSlotRepository()
    rows = []
    for i, p in enumerate(pending):
        space = space_repo.get_by_id(p["space_id"])

        old_slot = slot_repo.get_by_id(p["old_slot_id"])
        # ERROR HANDLING: stale drafts may reference unavailable records.
        if space is None or old_slot is None:
            raise Http404("This reschedule draft is out of date. Open the course and re-plan.")

        try:
            start = _aware_datetime(p["start"], "Start")
            end = _aware_datetime(p["end"], "End")
        except ValueError:
            raise Http404("This reschedule draft has invalid dates. Re-plan the course.") from None

        difference_days = (
            start.date() - old_slot.start_time.astimezone(COPENHAGEN_TZ).date()
        ).days
        now = timezone.now()

        is_past = old_slot.end_time <= now
        can_reschedule = (
            old_slot.start_time >= now - timedelta(days = RESCHEDULE_GRACE_DAYS)
        )

        rows.append({
            "context_id": i,
            "existing_appointment_id": p["existing_appointment_id"],
            "treatment_number": p["treatment_number"],
            "start": start,
            "end": end,
            "space_name": space.name,
            "difference_days": difference_days,
            "difference_absolute": abs(difference_days),
            "is_past": is_past,
            "can_reschedule": can_reschedule,
            "flagged": flagged[i] if i < len(flagged) else False,
            "alternatives_base_url": reverse("cascade_alternatives", kwargs={"cascade_index": i}),
        })
    return rows

@login_required
@permission_required("infra.can_book_appointments", raise_exception=True)
@require_POST  # ERROR HANDLING: reject accidental GET requests
def select_cascade_window(request, cascade_index):
    pending = request.session.get("pending_cascade")
    if pending is None:
        return redirect("calendar_page")

    # ERROR HANDLING: invalid edits leave the existing session draft intact.
    if not 0 <= cascade_index < len(pending):
        return _bad_request("Invalid cascade index.")
    pending = pending.copy()
    edited = pending[cascade_index].copy()
    try:
        window = _selected_window(request.POST)
        PlannedAppointment(window=window, treatment_type=TreatmentType(edited["treatment_type"]))
    except ValueError as exc:
        return _bad_request(str(exc))
    edited.update(space_id=window.space_id, start=window.start_time.isoformat(), end=window.end_time.isoformat())

    service = build_scheduling_service()
    new_start_date = datetime.fromisoformat(edited["start"]).date()

    # ERROR HANDLING: surface expected cascade rejections.
    try:
        result = service.cascade_reschedule(
            appointment_id=edited["existing_appointment_id"],
            min_interval_days=56,
            soft_preferred_days=77,
            earliest_start=new_start_date,
        )
    except LateReschedulingError:
        return _bad_request("The original appointment is outside the rescheduling grace period.")
    except (ValueError, OverflowError):
        return _bad_request("This appointment can no longer be replanned. Refresh the course and try again.")
    if result is None:
        return _bad_request("Could not find valid dates for the remaining appointments.")

    new_proposals, new_flagged = result
    if not new_proposals:
        return _bad_request("No scheduled appointments to reschedule.")

    updated_tail = [
        serialize_reschedule_proposal(p)
        for p in new_proposals
    ]

    pending[cascade_index] = edited # Not nessecarily the one from scheduling.cascade_reschedule but will still uphold the minimum distance, as it cannot pick any earlier 
    pending[cascade_index + 1:] = updated_tail[1:]       
    flagged = request.session.get("pending_cascade_flagged", []).copy()
    flagged[cascade_index + 1:] = new_flagged[1:]

    rows = build_cascade_rows(pending, flagged)
    request.session["pending_cascade"] = pending
    request.session["pending_cascade_flagged"] = flagged
    request.session.modified = True
    return render(request, "_cascade_rows.html", {"rows": rows})