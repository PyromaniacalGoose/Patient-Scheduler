import pytest
from scheduling.models import TreatmentSlot as ORMSlot
from infra.django_Repositories import DjangoSlotRepository
from datetime import datetime
from zoneinfo import ZoneInfo

COPENHAGEN_TZ = ZoneInfo("Europe/Copenhagen")

@pytest.mark.django_db
def test_slot_round_trip_preserves_fields(space):
    repo = DjangoSlotRepository()
    start = datetime(2026, 9, 1, 9, 0, tzinfo=COPENHAGEN_TZ)
    end = datetime(2026, 9, 1, 10, 0, tzinfo=COPENHAGEN_TZ)
    slot = ORMSlot(id=None, space_id=space.id, start_time=start,
                          end_time=end)

    saved = repo.save(slot)
    fetched = repo.get_by_id(saved.id)

    assert fetched.space_id == slot.space_id
    assert fetched.start_time == slot.start_time
    assert fetched.end_time == slot.end_time