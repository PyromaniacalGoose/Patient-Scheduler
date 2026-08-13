import pytest
from infra.models import TreatmentSpace as ORMSpace

@pytest.fixture
def space():
    return ORMSpace.objects.create(name="Test Room")