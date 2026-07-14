from django.db import transaction
from .models import TreatmentAppointment as ORMAppointment
from scheduling.models import Appointment, AppointmentStatus, TreatmentType 

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