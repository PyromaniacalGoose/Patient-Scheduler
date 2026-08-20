from datetime import datetime
from zoneinfo import ZoneInfo
from scheduling.models import AvailableWindow, PlannedAppointment, TreatmentType
from scheduling.scheduling import edit_planned_appointment

COPENHAGEN_TZ = ZoneInfo("Europe/Copenhagen")

start_t = datetime(2026, 8, 17, 0, 0, tzinfo=COPENHAGEN_TZ) 
end_t = datetime(2026, 8, 17, 4, 30, tzinfo=COPENHAGEN_TZ)
window = AvailableWindow(1, start_t, end_t)
p_appointment = PlannedAppointment(window, TreatmentType.V1, "test")

def test_change_note():
    result = edit_planned_appointment(
        planned=p_appointment,
        new_note="New note test"
    )
    assert result.note == "New note test"

def test_change_window():
    start_t2 = datetime(2026, 8, 18, 0, 0, tzinfo=COPENHAGEN_TZ) 
    end_t2 = datetime(2026, 8, 18, 4, 30, tzinfo=COPENHAGEN_TZ)
    window2 = AvailableWindow(1, start_t2, end_t2)
    result = edit_planned_appointment(
        planned=p_appointment,
        new_window=window2
    )
    assert result.window == window2