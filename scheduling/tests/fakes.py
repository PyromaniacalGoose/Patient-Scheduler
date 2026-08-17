from dataclasses import replace
from datetime import date
from infra.models import ScheduleClosure, ScheduleOverride, SpaceSchedule, TreatmentSpace
from scheduling.models import Appointment

'''Fake repos for testing schedulingService'''

class FakeAppointmentRepository:
    def __init__(self):
        self._data: dict[int, Appointment] = {}
        self._next_id = 1

    def get_by_id(self, appointment_id):
        return self._data.get(appointment_id)

    def save(self, appointment):
        if appointment.id is None:
            appointment = replace(appointment, id=self._next_id)
            self._next_id += 1
        self._data[appointment.id] = appointment
        return appointment

    def get_planned_course_appointments(self, course_id: int):... # isn't used by scheduling service

    def cancel(self, appointment_id: int):
        self._data.pop(appointment_id)

    # --- test-only seeding ---
    def seed_appointments(self, *appointments: Appointment):
        for a in appointments:
            if a.id is None:
                raise ValueError("seeded appointments must have an id")
            self._data[a.id] = a
            self._next_id = max(self._next_id, a.id + 1)

class FakeSpaceRepository:
    def __init__(self):
        self._data: dict[int, TreatmentSpace] = {}

    def get_all(self) -> list[TreatmentSpace]:
        return list(self._data.values())

    def get_by_id(self, space_id: int) -> TreatmentSpace | None:
        return self._data.get(space_id)

    # --- test-only seeding ---
    def seed_spaces(self, *spaces: TreatmentSpace):
        for s in spaces:
            if s.id is None:
                raise ValueError("seeded courses must have an id")
            self._data[s.id] = s


class FakeScheduleRepository:
    def __init__(self):
        self._rules: list[SpaceSchedule] = []
        self._closures: list[ScheduleClosure] = []
        self._overrides: list[ScheduleOverride] = []

    def get_rules_for_space(self, space_id: int) -> list[SpaceSchedule]:
        return [r for r in self._rules if r.space_id == space_id]

    def get_closures(self, start: date, end: date) -> list[ScheduleClosure]:
        return [c for c in self._closures if start <= c.date <= end]

    def get_schedule_overrides(self, start: date, end: date) -> list[ScheduleOverride]:
        return [o for o in self._overrides if start <= o.date <= end]

    # --- Test-only seeding, not part of the Protocol ---
    def seed_rules(self, *rules: SpaceSchedule) -> None:
        self._rules.extend(rules)

    def seed_closures(self, *closures: ScheduleClosure) -> None:
        self._closures.extend(closures)

    def seed_overrides(self, *overrides: ScheduleOverride) -> None:
        self._overrides.extend(overrides)


from dataclasses import replace
from datetime import datetime

from scheduling.models import (
    Appointment,
    CourseStatus,
    TreatmentCourse,
    TreatmentSlot,
)


class FakeCourseRepository:
    def __init__(self):
        self._data: dict[int, TreatmentCourse] = {}
        self._next_id = 1

    def get_by_id(self, course_id: int) -> TreatmentCourse | None:
        return self._data.get(course_id)

    def get_active_course_by_patient_id(self, patient_id: int) -> TreatmentCourse | None:
        for course in self._data.values():
            if course.patient_id == patient_id and course.status == CourseStatus.ACTIVE:
                return course
        return None

    def cancel(self, course_id: int) -> None:
        course = self._data.get(course_id)
        if course is not None:
            self._data[course_id] = replace(course, status=CourseStatus.CANCELLED)

    def save(self, course: TreatmentCourse) -> TreatmentCourse:
        if course.id is None:
            course = replace(course, id=self._next_id)
            self._next_id += 1
        self._data[course.id] = course
        return course

    # --- test-only seeding ---
    def seed_courses(self, *courses: TreatmentCourse) -> None:
        for c in courses:
            if c.id is None:
                raise ValueError("seeded courses must have an id")
            self._data[c.id] = c
            self._next_id = max(self._next_id, c.id + 1)

class FakeSlotRepository:
    def __init__(self):
        self._data: dict[int, TreatmentSlot] = {}
        self._next_id = 1
        # slot_id -> Appointment, needed only to support unbook()'s return value.
        # Mirrors the real DjangoSlotRepository reaching into appointment data
        self._appointment_by_slot: dict[int, Appointment] = {}

    def get_by_id(self, slot_id: int) -> TreatmentSlot | None:
        return self._data.get(slot_id)

    def get_booked_in_range(
        self, space_id: int, start: datetime, end: datetime
    ) -> list[TreatmentSlot]:
        return [
            s for s in self._data.values()
            if s.space_id == space_id and s.start_time < end and s.end_time > start
        ]

    def unbook(self, slot_id: int) -> Appointment | None:
        appt = self._appointment_by_slot.pop(slot_id, None)
        self._data.pop(slot_id, None)
        return appt

    def save(self, slot: TreatmentSlot) -> TreatmentSlot:
        if slot.id is None:
            slot = replace(slot, id=self._next_id)
            self._next_id += 1
        self._data[slot.id] = slot
        return slot

    # --- test-only seeding ---
    def seed_slots(self, *slots: TreatmentSlot) -> None:
        for s in slots:
            if s.id is None:
                raise ValueError("seeded slots must have an id")
            self._data[s.id] = s
            self._next_id = max(self._next_id, s.id + 1)

    def link_appointment_to_slot(self, slot_id: int, appointment: Appointment) -> None:
        '''Test-only: registers which appointment occupies a slot, so unbook() can return it.'''
        self._appointment_by_slot[slot_id] = appointment