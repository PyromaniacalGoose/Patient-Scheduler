"""
Dataclasses for domain level model representation.
patiant info isn't needed in scheduling logic, so we don't expose it domain side.
OBS it is important to keep this along with the repositories translating between it and the django ORM in-sync
"""
from dataclasses import dataclass, replace
from datetime import datetime, time, date
from enum import IntEnum

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

@dataclass(frozen=True)
class SpaceSchedule:
    space_id: int
    weekday: int  #0=Monday ... 6=Sunday
    open_time: time
    close_time: time
    slot_duration_minutes: int

@dataclass(frozen=True)
class ScheduleClosure:
    space_id: int | None
    date: date
    reason: str

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

@dataclass(frozen=True)
class TreatmentSlot:
    id: int | None
    space_id: int
    start_time: datetime
    end_time: datetime

    def __post_init__(self):
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")
        
@dataclass(frozen=True) #evaluted empty slots, exists only domain side, not persisted
class AvailableWindow:
    space_id: int
    start_time: datetime
    end_time: datetime

@dataclass(frozen=True)
class TreatmentCourse:
    id: int | None
    patient_id: int
    planned_treatments: int
    status: CourseStatus
        
    def __post_init__(self):
        if self.planned_treatments < 0:
            raise ValueError("planned_treatments cannot be negative")


