# infra/views.py
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from datetime import datetime
from infra.django_Repositories import DjangoSlotRepository, DjangoAppointmentRepository, DjangoSpaceRepository
from django.shortcuts import render


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