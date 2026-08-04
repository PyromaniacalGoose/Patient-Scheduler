from .repositories import AppointmentRepository, CourseRepository, SlotRepository, ScheduleRepository
from.models import TreatmentType

#this makes scehduling dependant on django, if backend ever changes from django, import UnitOfWork or likevise to handle atomic transactiosn 
from django.db.transaction import atomic 
 

# Hardcoded, but I doubt it'll be an issue
TREATMENT_DURATIONS: dict[TreatmentType, int] = {
    TreatmentType.V1: 60, #Not the real times
    TreatmentType.V2: 90,
}

class SchedulingService:
    def __init__(
            self,
            appointment_repo: AppointmentRepository,
            slot_repo: SlotRepository,
            course_repo: CourseRepository,
            schedule_repo: ScheduleRepository,
    ):
        self._appointment_repo = appointment_repo
        self._slot_repo = slot_repo
        self._course_repo = course_repo
        self._schedule_repo = schedule_repo
