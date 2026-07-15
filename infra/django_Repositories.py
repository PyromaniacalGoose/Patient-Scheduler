from datetime import datetime

from django.db import transaction

from .models import TreatmentAppointment as ORMAppointment
from .models import TreatmentSlot as ORMSlot
from scheduling.models import Appointment, AppointmentStatus, TreatmentSlot, TreatmentType 

class DjangoAppointmentRepository:
    def get_by_id(self, appointment_id: int) -> Appointment | None:
        try:
            orm_obj = ORMAppointment.objects.get(id=appointment_id, is_active=True)
        except ORMAppointment.DoesNotExist:
            return None
        return self._to_domain(orm_obj)

    def save(self, appointment: Appointment) -> Appointment:
        if appointment.id is None:
            orm_obj = ORMAppointment.objects.create(
                course_id=appointment.course_id,
                slot_id=appointment.slot_id,
                treatment_number=appointment.treatment_number,
                status=appointment.status.value,
                treatment_type=appointment.type.value,
                note=appointment.note,
            )
        else:
            orm_obj = ORMAppointment.objects.get(id=appointment.id)
            orm_obj.course_id = appointment.course_id
            orm_obj.slot_id = appointment.slot_id
            orm_obj.treatment_number = appointment.treatment_number
            orm_obj.status = appointment.status.value
            orm_obj.treatment_type = appointment.type.value
            orm_obj.note = appointment.note
            orm_obj.save()
        return self._to_domain(orm_obj)

    def get_planned_course_appointments(self, course_id: int) -> list[Appointment]:
        orm_appointments = ORMAppointment.objects.filter(
            course_id=course_id,
            status=AppointmentStatus.SCHEDULED.value,
            is_active=True, #For safety, but shouldn't be needed
        ).order_by("treatment_number")
        return [self._to_domain(a) for a in orm_appointments]

    #Soft delete, for loggin purposes 
    #TODO: ADD a way to cleanup the DB of soft deleted entries
    def cancel(self, appointment_id: int) -> None:
        ORMAppointment.objects.filter(id=appointment_id).update(
            is_active=False, status=AppointmentStatus.CANCELLED.value
        )

    #Map ORM to to domain model
    @staticmethod
    def _to_domain(orm_obj: ORMAppointment) -> Appointment:
        return Appointment(
            id=orm_obj.id,
            course_id=orm_obj.course_id,
            slot_id=orm_obj.slot_id,
            treatment_number=orm_obj.treatment_number,
            status=AppointmentStatus(orm_obj.status),
            type=TreatmentType(orm_obj.treatment_type),
            note=orm_obj.note,
        )

class DjangoSlotRepository:
    def get_by_id(self, slot_id: int) -> TreatmentSlot | None:
        try:
            orm_obj = ORMSlot.objects.get(id=slot_id)
        except ORMSlot.DoesNotExist:
            return None
        return self._to_domain(orm_obj)

    def get_booked_in_range(self, space_id: int, start: datetime, end: datetime) -> list[TreatmentSlot]:
        orm_slots = ORMSlot.objects.filter(
            space_id=space_id, start_time__lt=end, end_time__gt=start
        ).order_by("start_time")
        return [self._to_domain(s) for s in orm_slots]
    
    def save(self, treatment_slot: TreatmentSlot) -> TreatmentSlot:
        if treatment_slot.id is None:
            orm_obj = ORMSlot.objects.create(
                space_id = treatment_slot.space_id,
                start_time = treatment_slot.start_time,
                end_time = treatment_slot.end_time,
            )
        else:
            orm_obj = ORMSlot.objects.get(id=treatment_slot.id)
            orm_obj.space_id = treatment_slot.space_id
            orm_obj.start_time = treatment_slot.start_time
            orm_obj.end_time = treatment_slot.end_time
            orm_obj.save()
        return self._to_domain(orm_obj)

    def unbook(self, slot_id: int) -> Appointment | None:
        with transaction.atomic():
            try:
                orm_appt = ORMAppointment.objects.select_related("slot").get(
                    slot_id=slot_id, is_active=True
                )
            except ORMAppointment.DoesNotExist:
                return None
            appt = DjangoAppointmentRepository._to_domain(orm_appt)
            ORMSlot.objects.filter(id=slot_id).delete()
        return appt

    @staticmethod
    def _to_domain(orm_obj: ORMSlot) -> TreatmentSlot:
        return TreatmentSlot(
            id=orm_obj.id,
            space_id=orm_obj.space_id,
            start_time=orm_obj.start_time,
            end_time=orm_obj.end_time,
        )