from .models import Gender, PatientDetail
from .repositories import PatientRepository
        
gender_map = {
    "male": Gender.MALE,
    "female": Gender.FEMALE,
    "unassigned": Gender.UNASSIGNED,
}

class PatientService:
    def __init__(self, patient_repo: PatientRepository):
        self._patient_repo = patient_repo

    def register_or_reactivate(self, first_name, last_name, cpr, gender: Gender) -> PatientDetail:
        existing = self._patient_repo.get_by_cpr(cpr)
        if existing is not None:
            if not existing.is_active:  
                return self._patient_repo.reactivate(existing.id)
            return existing  # already active, don't duplicate
        try:
            gender_enum = gender_map[gender]
        except KeyError:
            raise ValueError(f"Invalid gender: {gender}")
        new_patient = PatientDetail(id=None, patient_number=None, first_name=first_name,
                                     last_name=last_name, CPR_number=cpr, gender=gender_enum, is_active=True)
        return self._patient_repo.save(new_patient)