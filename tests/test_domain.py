from datetime import datetime, timedelta
import pytest
from scheduling.models import (
    AvailableWindow, COPENHAGEN_TZ, FreeInterval, PlannedAppointment,
    TreatmentSlot, TreatmentType, TREATMENT_DURATIONS,
)
from scheduling.scheduling import edit_planned_appointment

START = datetime(2026, 10, 26, 8, tzinfo=COPENHAGEN_TZ)


@pytest.mark.parametrize("model", [AvailableWindow, FreeInterval, TreatmentSlot])
@pytest.mark.parametrize("offset", [0, -1])
def test_time_ranges_must_have_positive_length(model, offset):
    kwargs = dict(space_id=1, start_time=START, end_time=START + timedelta(minutes=offset))
    if model is TreatmentSlot:
        kwargs["id"] = None
    with pytest.raises(ValueError):
        model(**kwargs)


@pytest.mark.parametrize("model", [AvailableWindow, FreeInterval, TreatmentSlot])
@pytest.mark.parametrize("naive_field", ["start_time", "end_time"])
def test_ranges_require_aware_datetimes(model, naive_field):
    kwargs = dict(space_id=1, start_time=START, end_time=START + timedelta(hours=1))
    kwargs[naive_field] = kwargs[naive_field].replace(tzinfo=None)
    if model is TreatmentSlot:
        kwargs["id"] = None
    with pytest.raises(ValueError):
        model(**kwargs)


@pytest.mark.parametrize("kind", list(TreatmentType))
def test_treatment_rejects_window_one_minute_too_short(kind):
    window = AvailableWindow(1, START, START + timedelta(minutes=TREATMENT_DURATIONS[kind] - 1))
    with pytest.raises(ValueError):
        PlannedAppointment(window, kind)


def test_edit_revalidates_duration_and_preserves_original():
    original = PlannedAppointment(AvailableWindow(1, START, START + timedelta(minutes=270)), TreatmentType.V1, "original")
    with pytest.raises(ValueError):
        edit_planned_appointment(original, new_window=AvailableWindow(1, START, START + timedelta(minutes=1)))
    assert original.note == "original"
    assert edit_planned_appointment(original, new_note="new").note == "new"
    assert original.note == "original"
