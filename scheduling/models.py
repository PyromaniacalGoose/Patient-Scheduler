"""
Dataclasses for domain level model representation.
patiant info isn't needed in scheduling logic, so we don't expose it domain side.
OBS it is important to keep this along with the repositories translating between it and the django ORM in-sync
"""
from dataclasses import dataclass, replace
from datetime import datetime, time, date, timedelta
from enum import IntEnum
from zoneinfo import ZoneInfo

# Global timezone
COPENHAGEN_TZ = ZoneInfo("Europe/Copenhagen")

class AppointmentStatus(IntEnum):
    SCHEDULED = 1
    FINISHED = 2
    CANCELLED = 3
    NO_SHOW = 4

class CourseStatus(IntEnum):
    PLANNED = 1
    ACTIVE = 2
    COMPLETED = 3
    CANCELLED = 4

class TreatmentType(IntEnum):
    V1 = 1
    V2 = 2


TREATMENT_DURATIONS: dict[TreatmentType, int] = {
    TreatmentType.V1: 270,   # 4.5 hours, in minutes
    TreatmentType.V2: 510,   # 8.5 hours, in minutes
}

@dataclass(frozen=True)
class SpaceSchedule:
    space_id: int
    weekday: int  # 0=Monday ... 6=Sunday
    open_time: time
    close_time: time

@dataclass(frozen=True)
class ScheduleClosure:
    space_id: int | None
    date: date
    reason: str

@dataclass(frozen=True)
class ScheduleOverride:
    space_id: int | None
    date: date
    open_time: time
    close_time: time

@dataclass(frozen=True)
class TreatmentSpace:
    id: int | None
    name: str

@dataclass(frozen=True)
class Appointment:
    id: int | None
    course_id: int
    slot_id: int
    treatment_number: int
    status: AppointmentStatus
    type: TreatmentType
    note: str

    def __post_init__(self):
        if self.treatment_number <= 0:
            raise ValueError("treatment_number must be above 0")

def _require_aware(*values: datetime) -> None:
    for v in values:
        if v.tzinfo is None:
            raise ValueError(f"datetime {v!r} must be timezone-aware")

@dataclass(frozen=True)
class TreatmentSlot:
    id: int | None
    space_id: int
    start_time: datetime
    end_time: datetime

    def __post_init__(self):
        _require_aware(self.start_time, self.end_time)
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")

@dataclass(frozen=True)
class TreatmentCourse:
    id: int | None
    patient_id: int
    planned_treatments: int
    status: CourseStatus

    def __post_init__(self):
        if self.planned_treatments < 0:
            raise ValueError("planned_treatments cannot be negative")

@dataclass(frozen=True)  # Evaluated time that adheres to a treatment length, exists only domain side, not persisted
class AvailableWindow:
    space_id: int
    start_time: datetime
    end_time: datetime

    def __post_init__(self):
        _require_aware(self.start_time, self.end_time)
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")

@dataclass(frozen=True)  # Evaluated arbitrary length of time existing within open hours, exists only domain side, not persisted
class FreeInterval:
    space_id: int
    start_time: datetime
    end_time: datetime

    def __post_init__(self):
        _require_aware(self.start_time, self.end_time)
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")

@dataclass(frozen=True)  # User input
class PlannedAppointment:
    window: AvailableWindow
    treatment_type: TreatmentType
    note: str = ""

    def __post_init__(self):
        window_duration = self.window.end_time - self.window.start_time
        required = timedelta(minutes=TREATMENT_DURATIONS[self.treatment_type])
        if window_duration < required:
            raise ValueError(
                f"window duration {window_duration} is too short for "
                f"{self.treatment_type.name} (needs {required})"
            )

class SlotUnavailableError(Exception):
    # Raised when attempting to book a space-time window that's already taken.
    def __init__(self, space_id: int, start_time: datetime):
        self.space_id = space_id
        self.start_time = start_time
        super().__init__(f"Slot at space {space_id}, {start_time} is no longer available")

class AppointmentMissmatchError(Exception):
    # Raised when wrong amount of appointments are attempted booked
    def __init__(self, p_appointments: list[PlannedAppointment], appointments: int):
        self.p_appointments = p_appointments
        self.appointments = appointments

        p_appointment_str = ", ".join(
            f"space {p.window.space_id}: "
            f"{p.window.start_time:%Y-%m-%d %H:%M} - "
            f"{p.window.end_time:%H:%M}"
            for p in p_appointments
        )

        treatment_label = p_appointments[0].treatment_type.name if p_appointments else "unknown"
        super().__init__(
            f"Could not book {appointments} of type {treatment_label} appointments in timeslots: {p_appointment_str}"
        )

class CourseBookingFailedError(Exception):
    # Raised to propagate a bit more info up the chain on a booking failure
    def __init__(self, failed_at_appointment: int, underlying: SlotUnavailableError):
        self.failed_at_appointment = failed_at_appointment
        self.space_id = underlying.space_id
        self.start_time = underlying.start_time
        super().__init__(
            f"Booking failed at appointment {failed_at_appointment}: "
            f"slot at space {underlying.space_id}, {underlying.start_time} is no longer available"
        )