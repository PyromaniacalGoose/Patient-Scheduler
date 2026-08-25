from dataclasses import dataclass
from enum import IntEnum

class Gender(IntEnum):
    MALE = 1, 
    FEMALE = 2, 
    UNASSIGNED = 3, 

@dataclass(frozen=True)
class PatientDetail:
    id: int | None
    patient_number: int
    first_name: str
    last_name: str
    CPR_number: str
    gender: Gender 
    is_active: bool

