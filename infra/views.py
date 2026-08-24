# infra/views.py
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from datetime import datetime
from infra.django_Repositories import DjangoScheduleRepository, DjangoSlotRepository, DjangoAppointmentRepository, DjangoSpaceRepository
from django.shortcuts import render

from scheduling.scheduling import compute_free_intervals


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
            "color": "#c8f7c5",  # light green = open/free
        }
        for fi in free_intervals
    ]
    return JsonResponse(events, safe=False)