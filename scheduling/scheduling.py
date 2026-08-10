from datetime import date, datetime, time, timedelta

from .repositories import AppointmentRepository, CourseRepository, SlotRepository, ScheduleRepository, SpaceRepository
from.models import FreeInterval, ScheduleClosure, ScheduleOverride, SpaceSchedule, TreatmentSlot, TreatmentType, AvailableWindow, TreatmentSpace

# This makes scehduling dependant on django, if backend ever changes from django, import UnitOfWork or likevise to handle atomic transactiosn 
from django.db.transaction import atomic 
 

# Hardcoded, but I doubt it'll be an issue
TREATMENT_DURATIONS: dict[TreatmentType, int] = {
    TreatmentType.V1: 270, #minutes
    TreatmentType.V2: 510,
}

class SchedulingService:
    def __init__(
            self,
            appointment_repo: AppointmentRepository,
            slot_repo: SlotRepository,
            course_repo: CourseRepository,
            schedule_repo: ScheduleRepository,
            space_repo: SpaceRepository,
    ):
        self._appointment_repo = appointment_repo
        self._slot_repo = slot_repo
        self._course_repo = course_repo
        self._schedule_repo = schedule_repo
        self._space_repo = space_repo

    '''Method to find the earliest available window from a given date, expands search in increments of 3 weeks with a max of 180 days'''
    def find_earliest_available_on_or_after(
            self,
            earliest: date, 
            duration_minutes: int,
            space_ids: list[int],
            initial_window_days: int = 21,
            max_search_days: int = 180,
            ) -> AvailableWindow | None: 
        window_days = initial_window_days
        search_start_date = earliest

        while window_days <= max_search_days: # To avoid extreme intervals
            search_end_date = search_start_date + timedelta(days=window_days)
            search_start = datetime.combine(search_start_date, time.min)
            search_end = datetime.combine(search_end_date, time.min)

            # Repo calls
            rules = []
            for sp_id in space_ids:
                rules.extend(self._schedule_repo.get_rules_for_space(sp_id))
            overrides = self._schedule_repo.get_schedule_overrides(search_start_date, search_end_date)
            closures = self._schedule_repo.get_closures(search_start_date, search_end_date)
            booked = []
            for sp_id in space_ids:
                booked.extend(self._slot_repo.get_booked_in_range(sp_id, search_start, search_end))

            free = compute_free_intervals(rules, overrides, closures, booked, search_start, search_end, space_ids)
            windows = find_windows_for_duration(free, duration_minutes)

            if windows: # Already sorted earliest first
                return windows[0]

            window_days += 21 # expand search by 3 weeks

        return None
            
    def plan_course_dates(
        self,
        earliest_start: date,
        appointment_count: int,
        min_interval_days: int, # 56 (8 weeks) hard floor
        soft_preferred_days: int, # 77 (11 weeks) informational, not a ceiling
        duration_minutes: int,
    ) -> tuple[list[AvailableWindow], list[bool]] | None: 
        windows: list[date] = []
        flagged: list[bool] = [] # Flag to indicate if we've gone above soft cealing 
        next_earliest = earliest_start 
        space_ids = self._space_repo.get_all()
        

        for _ in range(appointment_count): 
            found = self.find_earliest_available_on_or_after(
                next_earliest, duration_minutes, space_ids
            )
            if found is None:
                return None

            found_date = found.start_time.date()
            soft_limit = next_earliest + timedelta(days=(soft_preferred_days - min_interval_days))
            flagged.append(found_date > soft_limit)

            windows.append(found)
            next_earliest = found_date + timedelta(days=min_interval_days)

        return windows, flagged
            



'''Function to find all unbooked free time per day, per room. this doesn't check if that time is actually useable, 
(ex: could return 10 minutes for a room on thursday if that is within open hours and unbooked), but can be fed to find_windows_for_duration
To find actually usable windows for a given treatment lenght. '''
def compute_free_intervals(
    schedule_rules: list[SpaceSchedule],
    overrides: list[ScheduleOverride],
    closures: list[ScheduleClosure],
    booked_slots: list[TreatmentSlot],
    start: datetime,
    end: datetime,
    space_ids: list[int],
    space_id: int | None = None,
) -> list[FreeInterval]:
    # If space is given, reduce parameters to only search for the relevent
    if space_id is not None:
        space_ids = [space_id]
        schedule_rules = [r for r in schedule_rules if r.space_id == space_id]
        overrides = [o for o in overrides if o.space_id == space_id]
        booked_slots = [s for s in booked_slots if s.space_id == space_id]

    # Build dicts for easier lookup
    rules_by_space_and_weekday: dict[tuple[int, int], SpaceSchedule] = {
        (r.space_id, r.weekday): r for r in schedule_rules
    }

    overrides_by_space_and_date: dict[tuple[int, date], ScheduleOverride] = {
        (o.space_id, o.date): o for o in overrides if o.space_id is not None
    }

    closures_by_date: dict[date, list[ScheduleClosure]] = {}
    for c in closures:
        closures_by_date.setdefault(c.date, []).append(c)

    booked_by_space: dict[int, list[TreatmentSlot]] = {}
    for s in booked_slots:
        booked_by_space.setdefault(s.space_id, []).append(s)

    intervals: list[FreeInterval] = []
    current_date = start.date()
    last_date = end.date()

    # Outer day loop
    while current_date <= last_date:
        weekday = current_date.weekday()
        day_closures = closures_by_date.get(current_date, [])
        hospital_wide_closure = any(c.space_id is None for c in day_closures)

        # Check if hospital is closed today, hierarchy goes: hospital wide closure -> room closure -> schedule override -> weekly schedule
        if not hospital_wide_closure:
            # Inner loop (is only a loop if space wasn't defined)
            for sp_id in space_ids:
                if any(c.space_id == sp_id for c in day_closures):
                    continue  # This space specifically closed today

                override = overrides_by_space_and_date.get((sp_id, current_date))
                if override is not None:
                    day_open_time, day_close_time = override.open_time, override.close_time
                else: # If no override refer to usual schedule
                    rule = rules_by_space_and_weekday.get((sp_id, weekday))
                    if rule is None:
                        continue  # Space not open on this weekday at all, no override either
                    day_open_time, day_close_time = rule.open_time, rule.close_time

                day_open = datetime.combine(current_date, day_open_time)
                day_close = datetime.combine(current_date, day_close_time)

                # Clip to the caller's requested range
                day_open = max(day_open, start)
                day_close = min(day_close, end)
                if day_open >= day_close:
                    continue

                # Adding unbooked time to the intervals
                intervals.extend(
                    _subtract_booked(
                        sp_id, day_open, day_close, booked_by_space.get(sp_id, [])
                    )
                )

        current_date += timedelta(days=1)


    #intervals.sort(key=lambda i: (i.space_id, i.start_time)) # Redundant sort
    return intervals


def _subtract_booked(
    space_id: int,
    day_open: datetime,
    day_close: datetime,
    booked: list[TreatmentSlot],
) -> list[FreeInterval]:
    # only care about bookings that actually fall within today's open window
    relevant = sorted(
        (b for b in booked if b.start_time < day_close and b.end_time > day_open),
        key=lambda b: b.start_time,
    )

    free: list[FreeInterval] = []
    cursor = day_open

    for booking in relevant:
        booked_start = max(booking.start_time, day_open)
        booked_end = min(booking.end_time, day_close)

        # Free time before/inbetween bookings
        if booked_start > cursor:
            free.append(FreeInterval(space_id=space_id, start_time=cursor, end_time=booked_start))

        cursor = max(cursor, booked_end)
    # Free time after all bookings
    if cursor < day_close:
        free.append(FreeInterval(space_id=space_id, start_time=cursor, end_time=day_close))

    return free

'''Returns a list of windows with a viable lenght for a given duration'''
def find_windows_for_duration(
    free_intervals: list[FreeInterval],
    duration_minutes: int,
    granularity_minutes: int = 15, # To avoid wierd times like 8:43
) -> list[AvailableWindow]:
    duration = timedelta(minutes=duration_minutes)
    step = timedelta(minutes=granularity_minutes)

    windows: list[AvailableWindow] = []
    for interval in free_intervals:
        candidate_start = interval.start_time
        while candidate_start + duration <= interval.end_time:
            windows.append(
                AvailableWindow(
                    space_id=interval.space_id,
                    start_time=candidate_start,
                    end_time=candidate_start + duration,
                )
            )
            candidate_start += step

    windows.sort(key=lambda w: (w.start_time, w.space_id))
    return windows