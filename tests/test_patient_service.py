from dataclasses import replace
import pytest
from patients.PatientService import PatientService
from patients.models import Gender, PatientDetail


class PatientRepo:
    """Small domain-only fake; database races are not tested with this fake."""
    def __init__(self, existing=None):
        self.existing = existing
        self.saved = []
        self.reactivated = []

    def get_by_cpr(self, cpr):
        return self.existing if self.existing and self.existing.CPR_number == cpr else None

    def save(self, patient):
        patient = replace(patient, id=1, patient_number=100001)
        self.saved.append(patient)
        return patient

    def reactivate(self, patient_id):
        self.reactivated.append(patient_id)
        self.existing = replace(self.existing, is_active=True)
        return self.existing


@pytest.mark.parametrize("value,expected", [("male", Gender.MALE), ("female", Gender.FEMALE), ("unassigned", Gender.UNASSIGNED)])
def test_new_patient_maps_gender_and_persists(value, expected):
    repo = PatientRepo()
    result = PatientService(repo).register_or_reactivate("Test", "Patient", "0000000000", value)
    assert result.gender == expected
    assert result.is_active
    assert repo.saved == [result]


@pytest.mark.parametrize("active", [True, False])
def test_existing_patient_reused_or_reactivated_without_duplicate(active):
    patient = PatientDetail(1, 100001, "Test", "Patient", "0000000000", Gender.UNASSIGNED, active)
    repo = PatientRepo(patient)
    result = PatientService(repo).register_or_reactivate("Other", "Name", patient.CPR_number, "unassigned")
    assert result.id == patient.id
    assert result.patient_number == patient.patient_number
    assert result.is_active
    assert not repo.saved
    assert repo.reactivated == ([] if active else [patient.id])


def test_invalid_gender_does_not_write():
    repo = PatientRepo()
    with pytest.raises(ValueError):
        PatientService(repo).register_or_reactivate("Test", "Patient", "0000000000", "invalid")
    assert not repo.saved
