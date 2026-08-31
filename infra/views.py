# infra/views.py
from datetime import date, datetime, time, timedelta
from collections import defaultdict

from django.http import Http404, HttpResponseBadRequest, JsonResponse
from django.utils import timezone
from infra.django_Repositories import DjangoCourseRepository, DjangoPatientRepository, DjangoScheduleRepository, DjangoSlotRepository, DjangoAppointmentRepository, DjangoSpaceRepository
from django.shortcuts import redirect, render
from django.contrib.auth.decorators import login_required, permission_required

from infra.models import CourseStatus, ScheduleClosure, ScheduleOverride, SpaceSchedule, TreatmentType
from infra.services import build_scheduling_service
from patients.PatientService import PatientService
from scheduling.models import COPENHAGEN_TZ, TREATMENT_DURATIONS, AvailableWindow, CourseBookingFailedError, PlannedAppointment, TreatmentCourse
from scheduling.scheduling import compute_free_intervals, find_windows_for_duration


WEEKDAYS = [
    (0, "Monday"),
    (1, "Tuesday"),
    (2, "Wednesday"),
    (3, "Thursday"),
    (4, "Friday"),
    (5, "Saturday"),
    (6, "Sunday"),
]

@login_required
def calendar_events(request):
    space_repo = DjangoSpaceRepository()
    slot_repo = DjangoSlotRepository()
    appointment_repo = DjangoAppointmentRepository()

    start = datetime.fromisoformat(request.GET.get("start"))
    end = datetime.fromisoformat(request.GET.get("end"))

    events = []
    for space in space_repo.get_all():
        slots = slot_repo.get_booked_in_range(space.id, start, end)
        for slot in slots:
            events.append({
                "id": slot.id,
                "start": slot.start_time.isoformat(),
                "end": slot.end_time.isoformat(),
                "title": f"{space.name}",
                "extendedProps": {"space_id": space.id},
            })

    return JsonResponse(events, safe=False)

@login_required
def calendar_page(request):
    return render(request, "calendar.html")

@login_required
def calendar_events(request):
    space_repo = DjangoSpaceRepository()
    slot_repo = DjangoSlotRepository()
    appointment_repo = DjangoAppointmentRepository()

    start = datetime.fromisoformat(request.GET.get("start"))
    end = datetime.fromisoformat(request.GET.get("end"))

    events = []
    spaces = space_repo.get_all()
    for space in spaces:
        slots = slot_repo.get_booked_in_range(space.id, start, end)
        if not slots:
            continue
        appointments = {a.slot_id: a for a in appointment_repo.get_by_slot_ids([s.id for s in slots])}

        for slot in slots:
            appt = appointments.get(slot.id)
            title = f"{space.name}: Treatment #{appt.treatment_number}" if appt else f"{space.name}: Blocked"
            events.append({
                "id": slot.id,
                "start": slot.start_time.isoformat(),
                "end": slot.end_time.isoformat(),
                "title": title,
                "color": "#3788d8" if appt else "#888888",
            })

    return JsonResponse(events, safe=False)

@login_required
def calendar_availability(request):
    space_repo = DjangoSpaceRepository()
    schedule_repo = DjangoScheduleRepository()
    slot_repo = DjangoSlotRepository()
    start = datetime.fromisoformat(request.GET.get("start"))
    end = datetime.fromisoformat(request.GET.get("end"))
    

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
@permission_required("infra.can_book_appointments", raise_exception=True)
def register_patient(request):
    if request.method == "POST":
        patient_service = PatientService(DjangoPatientRepository())
        patient = patient_service.register_or_reactivate(
            first_name=request.POST["first_name"],
            last_name=request.POST["last_name"],
            cpr=request.POST["cpr_number"],
            gender=request.POST["gender"],
        )
        return redirect("patient_detail", patient_id=patient.id)

    return render(request, "register_patient.html")

@login_required
def patient_detail(request, patient_id):
    patient_repo = DjangoPatientRepository()
    patient = patient_repo.get_by_id(patient_id)

    if patient is None:
        raise Http404("Patient not found")

    return render(
        request,
        "patient_detail.html",
        {"patient": patient},
    )

@login_required
def patient_list(request):
    patient_repo = DjangoPatientRepository()
    patients = patient_repo.get_active_patients()

    return render(
        request,
        "patient_list.html",
        {"patients": patients},
    )

@login_required
@permission_required("infra.can_book_appointments", raise_exception=True)
def start_course(request):
    if request.method == "POST":
        service = build_scheduling_service()  # factory, see below
        patient_repo = DjangoPatientRepository()
        space_ids = [s.id for s in DjangoSpaceRepository().get_all()]
        patient_number = int(request.POST["patient_number"])
        patient = patient_repo.get_by_number(patient_number)
        if patient is None:
            return (request, "start_course.html", {"error": f"Patient: {patient_number} not found."})


        result = service.start_course(
            patient_id = patient.id,
            appointment_count=int(request.POST["appointment_count"]),
            min_interval_days=int(request.POST["min_interval_days"]),
            soft_preferred_days=int(request.POST["soft_preferred_days"]),
            treatment_type=TreatmentType(int(request.POST["treatment_type"])),
            space_ids=space_ids,
            earliest_start=date.fromisoformat(request.POST["earliest_start"]),
        )

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
            "treatment_type": int(request.POST["treatment_type"]),
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
        planned = []
        for i, w in enumerate(pending["windows"]):
            note = request.POST.get(
                f"note_{i}",
                "",
            )

            window = AvailableWindow(
                space_id=w["space_id"],
                start_time=datetime.fromisoformat(w["start"]),
                end_time=datetime.fromisoformat(w["end"]),
            )

            planned.append(
                PlannedAppointment(
                    window=window,
                    treatment_type=TreatmentType(
                        pending["treatment_type"]
                    ),
                    note=note,
                )
            )

        # This is the DOMAIN TreatmentCourse.
        course = TreatmentCourse(
            id=None,
            patient_id=pending["patient_id"],
            planned_treatments=len(planned),
            status=CourseStatus(
                pending["status"]
            ),
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
                    "appointments": build_review_appointments(pending),
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

        start = datetime.fromisoformat(window["start"])
        end = datetime.fromisoformat(window["end"])
        original_start = datetime.fromisoformat(original["start"])

        difference_days = (
            start.date() - original_start.date()
        ).days

        space = space_repo.get_by_id(window["space_id"])

        appointments.append({
            "index": i,
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
def appointment_alternatives(request, appointment_index):
    pending = request.session.get("pending_course")

    if pending is None:
        return redirect("start_course")

    if (
        appointment_index < 0
        or appointment_index >= len(pending["windows"])
    ):
        return HttpResponseBadRequest(
            "Invalid appointment index."
        )

    week_start_str = request.GET.get("week_start")

    if week_start_str:
        week_start = date.fromisoformat(week_start_str)
    else:
        proposed = pending["windows"][appointment_index]
        week_start = datetime.fromisoformat(
            proposed["start"]
        ).date()
        week_start -= timedelta(
            days=week_start.weekday()
        )
    week_end = week_start + timedelta(days=7)

    schedule_repo = DjangoScheduleRepository()
    slot_repo = DjangoSlotRepository()
    space_repo = DjangoSpaceRepository()

    space_ids = [
        s.id for s in space_repo.get_all()
    ]
    rules = []
    for space_id in space_ids:
        rules.extend(
            schedule_repo.get_rules_for_space(space_id)
        )
    tz_start = datetime.combine(
        week_start,
        time.min,
        tzinfo=COPENHAGEN_TZ,
    )
    tz_end = datetime.combine(
        week_end,
        time.min,
        tzinfo=COPENHAGEN_TZ,
    )
    overrides = schedule_repo.get_schedule_overrides(
        week_start,
        week_end,
    )
    closures = schedule_repo.get_closures(
        week_start,
        week_end,
    )
    booked = []
    for space_id in space_ids:
        booked.extend(
            slot_repo.get_booked_in_range(
                space_id,
                tz_start,
                tz_end,
            )
        )
    duration = TREATMENT_DURATIONS[
        TreatmentType(pending["treatment_type"])
    ]
    free = compute_free_intervals(
        rules,
        overrides,
        closures,
        booked,
        tz_start,
        tz_end,
        space_ids,
    )
    windows = find_windows_for_duration(free, duration)
    windows_by_day = {}

    for w in windows:
        day = w.start_time.date()

        if day not in windows_by_day:
            windows_by_day[day] = []

        windows_by_day[day].append(w)


    days = []

    for offset in range(7):
        day = week_start + timedelta(days=offset)

        day_windows = windows_by_day.get(day, [])

        days.append({
            "date": day,
            "windows": day_windows,
        })

    return render(
        request,
        "_alternatives_fragment.html",
        {
            "days": days,
            "appointment_index": appointment_index,
            "week_start": week_start,
            "prev_week": week_start - timedelta(days=7),
            "next_week": week_start + timedelta(days=7),
        },
    )

@login_required
@permission_required(
    "infra.can_book_appointments",
    raise_exception=True,
)
def select_window(request, appointment_index):
    pending = request.session.get("pending_course")

    if pending is None:
        return redirect("start_course")

    pending["windows"][appointment_index] = {
        "space_id": int(request.POST["space_id"]),
        "start": request.POST["start"],
        "end": request.POST["end"],
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
            "index": appointment["index"],
            "window": appointment["window"],
            "start": appointment["start"],
            "end": appointment["end"],
            "space_name": appointment["space_name"],
            "difference_days": appointment["difference_days"],
            "difference_absolute": appointment["difference_absolute"],
        },
    )

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

    # Which space are we currently editing?
    selected_space_id = int(
        request.GET.get("space_id", spaces[0].id)
    )
    if request.method == "POST":

        action = request.POST.get("action")

        # ------------------------------------------
        # SAVE ENTIRE WEEKLY SCHEDULE
        # ------------------------------------------

        if action == "save_weekly_schedule":

            selected_space_id = int(
                request.POST["space_id"]
            )

            for weekday, _ in WEEKDAYS:

                open_raw = request.POST.get(
                    f"open_{weekday}"
                )

                close_raw = request.POST.get(
                    f"close_{weekday}"
                )

                # Blank times = closed that day
                if not open_raw or not close_raw:
                    continue

                rule = SpaceSchedule(
                    space_id=selected_space_id,
                    weekday=weekday,
                    open_time=time.fromisoformat(open_raw),
                    close_time=time.fromisoformat(close_raw),
                )

                schedule_repo.save_rule(rule)

            return redirect(
                f"/calendar/schedule/?space_id={selected_space_id}"
            )

        # ------------------------------------------
        # CREATE CLOSURE
        # ------------------------------------------

        elif action == "create_closure":

            space_id_raw = request.POST.get("space_id")

            closure = ScheduleClosure(
                space_id=(
                    int(space_id_raw)
                    if space_id_raw
                    else None
                ),
                date=date.fromisoformat(
                    request.POST["date"]
                ),
                reason=request.POST.get("reason", ""),
            )

            schedule_repo.save_closure(closure)

            return redirect("schedule_management")

        # ------------------------------------------
        # CREATE OVERRIDE
        # ------------------------------------------

        elif action == "create_override":

            space_id_raw = request.POST.get("space_id")

            override = ScheduleOverride(
                space_id=(
                    int(space_id_raw)
                    if space_id_raw
                    else None
                ),
                date=date.fromisoformat(
                    request.POST["date"]
                ),
                open_time=time.fromisoformat(
                    request.POST["open_time"]
                ),
                close_time=time.fromisoformat(
                    request.POST["close_time"]
                ),
            )

            schedule_repo.save_override(override)

            return redirect("schedule_management")

    # ==========================================
    # GET EXISTING RULES FOR SELECTED SPACE
    # ==========================================

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

        space = space_repo.get_by_id(slot.space_id)

        if space is None:
            continue

        appointment_details.append({
            "appointment": appointment,
            "slot": slot,
            "space": space,
        })

    return render(request, "course_detail.html", {
        "patient": patient,
        "appointments": appointment_details,
    })