"""Excel downloads for the calendar and scheduling records."""

from collections import defaultdict
from datetime import date, datetime, time, timedelta
from hashlib import sha256
from io import BytesIO
from textwrap import wrap

from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse, HttpResponseBadRequest
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_safe
from xlsxwriter import Workbook

from infra import models
from scheduling.models import COPENHAGEN_TZ


PATIENT_COLORS = (
    "#BDE6F2", "#F7C6D0", "#C3E8C6", "#DED0F5",
    "#FFD7B1", "#C9D6FA", "#F1C9EA", "#DCEBB0",
    "#FAC5B9", "#BCE7DF", "#F8E3AA", "#D7DDE8",
)


def _local_time(value):
    return value.astimezone(COPENHAGEN_TZ).replace(tzinfo=None)


def _write_sheet(workbook, name, title, columns, rows, empty_message, *, room_grid=False):
    sheet = workbook.add_worksheet(name)
    title_format = workbook.add_format({"bold": True, "font_size": 15, "font_color": "#17365D"})
    header_format = workbook.add_format({"bold": True, "bg_color": "#17365D", "font_color": "#FFFFFF"})
    formats = {
        date: workbook.add_format({"num_format": "dd/mm/yyyy"}),
        time: workbook.add_format({"num_format": "hh:mm"}),
        datetime: workbook.add_format({"num_format": "dd/mm/yyyy hh:mm"}),
    }
    if room_grid:
        formats[date] = workbook.add_format({"num_format": "ddd dd/mm/yyyy", "valign": "top"})
        patient_formats = {
            color: workbook.add_format({"bg_color": color, "font_color": "#202124", "text_wrap": True, "valign": "top"})
            for color in PATIENT_COLORS
        }

    sheet.write(0, 0, title, title_format)
    sheet.set_row(0, 24)
    for index, (label, width) in enumerate(columns):
        sheet.set_column(index, index, width)
        sheet.write(2, index, label, header_format)
    sheet.freeze_panes(3, int(room_grid))
    last_row = 2
    for values in rows:
        last_row += 1
        if not room_grid:
            for index, value in enumerate(values):
                sheet.write(last_row, index, value, formats.get(type(value)))
            continue

        day, *room_bookings = values
        day_rows = max(1, max(map(len, room_bookings), default=0))
        if day_rows == 1:
            sheet.write(last_row, 0, day, formats[date])
        else:
            sheet.merge_range(last_row, 0, last_row + day_rows - 1, 0, day, formats[date])

        row_lines = [1] * day_rows
        for index, bookings in enumerate(room_bookings, start=1):
            width = columns[index][1] - 8
            for offset, (color, label) in enumerate(bookings):
                sheet.write_string(last_row + offset, index, label, patient_formats[color])
                lines = sum(max(1, len(wrap(line, width=width))) for line in label.splitlines())
                row_lines[offset] = max(row_lines[offset], lines)
        for offset, lines in enumerate(row_lines):
            sheet.set_row(last_row + offset, max(27, 15 * lines + 7))
        last_row += day_rows - 1
    if last_row == 2:
        sheet.write(3, 0, empty_message)
    elif not room_grid:
        sheet.autofilter(2, 0, last_row, len(columns) - 1)
    sheet.set_landscape()
    sheet.fit_to_pages(1, 0)
    sheet.repeat_rows(0, 2)


def _download(filename, build):
    output = BytesIO()
    with Workbook(output, {"strings_to_formulas": False, "strings_to_urls": False}) as workbook:
        build(workbook)
    response = HttpResponse(
        output.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@login_required
@permission_required("infra.can_book_appointments", raise_exception=True)
@require_safe
@never_cache
def export_schedule(request):
    try:
        raw_start = request.GET.get("start", "")
        if raw_start:
            first_day = date.fromisoformat(raw_start)
        else:
            today = timezone.localdate(timezone=COPENHAGEN_TZ)
            first_day = today + timedelta(days=7 - today.weekday())
        days = int(request.GET.get("days", "7"))
        if days not in (7, 14):
            raise ValueError
        end_day = first_day + timedelta(days=days)
    except (ValueError, OverflowError):
        return HttpResponseBadRequest("Choose a valid start date and either 7 or 14 days.")

    first = datetime.combine(first_day, time.min, COPENHAGEN_TZ)
    end = datetime.combine(end_day, time.min, COPENHAGEN_TZ)
    appointments = (
        models.TreatmentAppointment.objects.filter(
            is_active=True,
            status=models.AppointmentStatus.SCHEDULED,
            slot__is_active=True,
            course__is_active=True,
            course__patient__is_active=True,
            slot__start_time__gte=first,
            slot__start_time__lt=end,
        )
        .select_related("slot__space", "course__patient")
        .order_by("slot__start_time", "slot__space__name", "pk")
    )

    def build(workbook):
        spaces = {
            space.pk: space for space in models.TreatmentSpace.objects.order_by("name", "location", "pk")
        }
        bookings = defaultdict(list)
        for appointment in appointments.iterator():
            slot = appointment.slot
            patient = appointment.course.patient
            start = _local_time(slot.start_time)
            finish = _local_time(slot.end_time)
            end_label = f"{finish:%H:%M}" if finish.date() == start.date() else f"{finish:%d/%m %H:%M}"
            treatment = appointment.get_treatment_type_display()
            digest = sha256(str(patient.patient_number).encode()).digest()
            color = PATIENT_COLORS[int.from_bytes(digest[:2]) % len(PATIENT_COLORS)]
            label = (
                f"{start:%H:%M}–{end_label} · {treatment} #{appointment.treatment_number}\n"
                f"{patient.first_name} {patient.last_name} ({patient.patient_number})"
            )
            bookings[start.date(), slot.space_id].append((color, label))
            spaces.setdefault(slot.space_id, slot.space)

        columns = [("Date", 20)]
        for space in spaces.values():
            label = f"{space.name} ({space.location})" if space.location else space.name
            columns.append((label, 34))

        def rows():
            if not spaces:
                return
            for offset in range(days):
                day = first_day + timedelta(days=offset)
                yield (day, *(bookings.get((day, space_id), ()) for space_id in spaces))

        last_day = end_day - timedelta(days=1)
        _write_sheet(
            workbook, "Schedule", f"{first_day:%d/%m/%Y} to {last_day:%d/%m/%Y} · Copenhagen time",
            columns, rows(), "No treatment rooms are configured.", room_grid=True,
        )

    return _download(f"schedule-{first_day}-{days}-days.xlsx", build)


def _write_records(workbook, name, records, columns):
    def rows():
        for record in records.iterator():
            values = []
            for _, _, field in columns:
                value = record
                for part in field.split("__"):
                    value = getattr(value, part)
                if callable(value):
                    value = value()
                if isinstance(value, datetime):
                    value = _local_time(value)
                values.append(value)
            yield values

    _write_sheet(
        workbook, name, f"{name} · Copenhagen time",
        [(label, width) for label, width, _ in columns], rows(), "No records.",
    )


@login_required
@require_safe
@never_cache
def export_scheduling_data(request):
    if not request.user.is_superuser:
        raise PermissionDenied

    def build(workbook):
        appointments = models.TreatmentAppointment.objects.select_related(
            "slot__space", "course__patient"
        ).order_by("slot__start_time", "pk")
        _write_records(workbook, "Appointments", appointments, (
            ("Start", 22, "slot__start_time"), ("End", 22, "slot__end_time"),
            ("Room", 24, "slot__space__name"), ("Patient number", 17, "course__patient__patient_number"),
            ("Appointment ID", 17, "pk"), ("Course ID", 13, "course_id"),
            ("Slot ID", 12, "slot_id"), ("Treatment number", 20, "treatment_number"),
            ("Treatment type", 17, "get_treatment_type_display"),
            ("Status", 16, "get_status_display"), ("Active", 10, "is_active"),
            ("Note", 48, "note"), ("Created", 22, "created_at"), ("Updated", 22, "updated_at"),
        ))
        _write_records(workbook, "Patients", models.Patient.objects.order_by("pk"), (
            ("Patient ID", 14, "pk"), ("Patient number", 17, "patient_number"),
            ("First name", 23, "first_name"), ("Last name", 26, "last_name"),
            ("CPR number", 16, "CPR_number"), ("Gender", 17, "get_gender_display"),
            ("Active", 10, "is_active"), ("Created", 22, "created_at"), ("Updated", 22, "updated_at"),
        ))
        _write_records(workbook, "Courses", models.TreatmentCourse.objects.order_by("pk"), (
            ("Course ID", 13, "pk"), ("Patient ID", 14, "patient_id"),
            ("Planned treatments", 22, "planned_treatments"), ("Status", 16, "get_status_display"),
            ("Active", 10, "is_active"), ("Created", 22, "created_at"), ("Updated", 22, "updated_at"),
        ))
        _write_records(workbook, "Slots", models.TreatmentSlot.objects.order_by("pk"), (
            ("Slot ID", 12, "pk"), ("Room ID", 13, "space_id"), ("Start", 22, "start_time"),
            ("End", 22, "end_time"), ("Active", 10, "is_active"),
        ))
        _write_records(workbook, "Rooms", models.TreatmentSpace.objects.order_by("pk"), (
            ("Room ID", 13, "pk"), ("Name", 28, "name"), ("Location", 32, "location"),
        ))
        _write_records(workbook, "Opening hours", models.SpaceSchedule.objects.order_by("pk"), (
            ("Rule ID", 13, "pk"), ("Room ID", 13, "space_id"),
            ("Weekday", 14, "get_weekday_display"), ("Opens", 12, "open_time"),
            ("Closes", 12, "close_time"),
        ))
        _write_records(workbook, "Overrides", models.ScheduleOverride.objects.order_by("pk"), (
            ("Override ID", 15, "pk"), ("Room ID", 13, "space_id"), ("Date", 14, "date"),
            ("Opens", 12, "open_time"), ("Closes", 12, "close_time"),
        ))
        _write_records(workbook, "Closures", models.ScheduleClosure.objects.order_by("pk"), (
            ("Closure ID", 15, "pk"), ("Room ID", 13, "space_id"), ("Date", 14, "date"),
            ("Reason", 48, "reason"),
        ))

    return _download("scheduling-data.xlsx", build)
