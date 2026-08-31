# infra/services.py
from infra.django_Repositories import (
    DjangoAppointmentRepository, DjangoSlotRepository, DjangoCourseRepository,
    DjangoScheduleRepository, DjangoSpaceRepository,
)
from scheduling.scheduling import SchedulingService

def build_scheduling_service() -> SchedulingService:
    return SchedulingService(
        appointment_repo=DjangoAppointmentRepository(),
        slot_repo=DjangoSlotRepository(),
        course_repo=DjangoCourseRepository(),
        schedule_repo=DjangoScheduleRepository(),
        space_repo=DjangoSpaceRepository(),
    )

