from datetime import date, datetime

from django.db import IntegrityError, transaction

from patients.models import PatientDetail, Gender

from .models import TreatmentAppointment as ORMAppointment
from .models import TreatmentSlot as ORMSlot
from .models import TreatmentCourse as ORMCourse
from .models import TreatmentSpace as ORMSpace
from .models import Patient as ORMPatient
from .models import SpaceSchedule as ORMSpaceSchedule, ScheduleClosure as ORMScheduleClosure, ScheduleOverride as ORMScheduleOverride

from scheduling.models import Appointment, AppointmentStatus, CourseStatus, ScheduleOverride, SlotUnavailableError, TreatmentCourse, TreatmentSlot, TreatmentSpace, TreatmentType, SpaceSchedule, ScheduleClosure

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

    def get_by_slot_ids(self, slot_ids: list[int]) -> list[Appointment]:
        orm_appointments = ORMAppointment.objects.filter(slot_id__in=slot_ids, is_active=True)
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
        try:
            if treatment_slot.id is None:
                orm_obj = ORMSlot.objects.create(
                    space_id=treatment_slot.space_id,
                    start_time=treatment_slot.start_time,
                    end_time=treatment_slot.end_time,
                )
            else:
                orm_obj = ORMSlot.objects.get(id=treatment_slot.id)
                orm_obj.space_id = treatment_slot.space_id
                orm_obj.start_time = treatment_slot.start_time
                orm_obj.end_time = treatment_slot.end_time
                orm_obj.save()
        except IntegrityError:
            raise SlotUnavailableError(treatment_slot.space_id, treatment_slot.start_time) from None

        return self._to_domain(orm_obj)

    def unbook(self, slot_id: int) -> None:
        ORMSlot.objects.filter(id=slot_id).delete()

    @staticmethod
    def _to_domain(orm_obj: ORMSlot) -> TreatmentSlot:
        return TreatmentSlot(
            id=orm_obj.id,
            space_id=orm_obj.space_id,
            start_time=orm_obj.start_time,
            end_time=orm_obj.end_time,
        )

class DjangoCourseRepository:
    def get_by_id(self, course_id: int) -> TreatmentCourse | None: 
        try:
            orm_obj = ORMCourse.objects.get(id=course_id, is_active=True)
        except ORMCourse.DoesNotExist:
            return None
        return self._to_domain(orm_obj)

    #TODO Make sure this either can't return multiple values, or handle a scenario where it does.
    def get_active_course_by_patient_id(self, patient_id: int) -> TreatmentCourse | None: 
        try:
            orm_obj = ORMCourse.objects.get(patient_id=patient_id, status=CourseStatus.ACTIVE.value, is_active=True)
        except ORMCourse.DoesNotExist:
            return None
        return self._to_domain(orm_obj)

    def cancel(self, course_id: int) -> None: 
        ORMCourse.objects.filter(id=course_id).update(
                is_active=False, status=CourseStatus.CANCELLED.value
            ) #Same as with appoinments
        
    def save(self, course: TreatmentCourse) -> TreatmentCourse:
        if course.id is None:
            orm_obj = ORMCourse.objects.create(
                patient_id = course.patient_id,
                planned_treatments = course.planned_treatments,
                status = course.status.value,
            )
        else:
            orm_obj = ORMCourse.objects.get(id=course.id)
            orm_obj.patient_id = course.patient_id
            orm_obj.planned_treatments = course.planned_treatments
            orm_obj.status = course.status.value
            orm_obj.save()
        return self._to_domain(orm_obj)

    @staticmethod
    def _to_domain(orm_obj: ORMCourse) -> TreatmentCourse:
        return TreatmentCourse(
            id=orm_obj.id,
            patient_id=orm_obj.patient_id,
            planned_treatments=orm_obj.planned_treatments,
            status=CourseStatus(orm_obj.status),
        )

class DjangoScheduleRepository:
    def get_rules_for_space(self, space_id: int) -> list[SpaceSchedule]:
        orm_rules = ORMSpaceSchedule.objects.filter(space_id=space_id).order_by("weekday")
        return [self._rule_to_domain(r) for r in orm_rules]

    def get_closures(self, start: date, end: date) -> list[ScheduleClosure]:
        orm_closures = ORMScheduleClosure.objects.filter(
            date__gte=start, date__lte=end
        ).order_by("date")
        return [self._closure_to_domain(c) for c in orm_closures]

    def get_schedule_overrides(self, start: date, end: date) -> list[ScheduleOverride]:
        orm_overrides = ORMScheduleOverride.objects.filter(
            date__gte=start, date__lte=end
        ).order_by("date")
        return [self._overrides_to_domain(c) for c in orm_overrides]
        
    @staticmethod
    def _rule_to_domain(orm_obj: ORMSpaceSchedule) -> SpaceSchedule:
        return SpaceSchedule(
            space_id=orm_obj.space_id,
            weekday=orm_obj.weekday,
            open_time=orm_obj.open_time,
            close_time=orm_obj.close_time,
        )

    @staticmethod
    def _closure_to_domain(orm_obj: ORMScheduleClosure) -> ScheduleClosure:
        return ScheduleClosure(
            space_id=orm_obj.space_id,
            date=orm_obj.date,
            reason=orm_obj.reason,
        )

    @staticmethod
    def _override_to_domain(orm_obj: ORMScheduleOverride) -> ScheduleOverride:
        return ScheduleOverride(
            space_id=orm_obj.space_id,
            date=orm_obj.date,
            open_time=orm_obj.open_time,
            close_time=orm_obj.close_time,
        )

    #Infra side only
    def save_rule(self, rule: SpaceSchedule) -> SpaceSchedule:
        orm_obj, _ = ORMSpaceSchedule.objects.update_or_create(
            space_id=rule.space_id,
            weekday=rule.weekday,
            defaults={
                "open_time": rule.open_time,
                "close_time": rule.close_time,
            },
        )

        return self._rule_to_domain(orm_obj)

    def save_closure(self, closure: ScheduleClosure) -> ScheduleClosure:
        orm_obj = ORMScheduleClosure.objects.create(
            space_id=closure.space_id,
            date=closure.date,
            reason=closure.reason,
        )

        return self._closure_to_domain(orm_obj)

    def save_override(self, override: ScheduleOverride) -> ScheduleOverride:
        orm_obj = ORMScheduleOverride.objects.create(
            space_id=override.space_id,
            date=override.date,
            open_time=override.open_time,
            close_time=override.close_time,
        )
    
        return self._overrides_to_domain(orm_obj)

class DjangoSpaceRepository: 
    def get_all(self) -> list[TreatmentSpace]:
        orm_spaces = ORMSpace.objects.all()
        return [self._to_domain(a) for a in orm_spaces]

    def get_by_id(self, space_id: int) -> TreatmentSpace | None:
        try:
            orm_obj = ORMSpace.objects.get(id=space_id, is_active=True)
        except ORMSpace.DoesNotExist:
            return None
        return self._to_domain(orm_obj)
    
    @staticmethod
    def _to_domain(orm_obj: ORMSpace) -> TreatmentSpace:
        return TreatmentSpace(
            id=orm_obj.id,
            name=orm_obj.name,
        )

class DjangoPatientRepository:
    def get_by_cpr(self, cpr: str) -> PatientDetail | None:
        try:
            orm_obj = ORMPatient.objects.get(CPR_number=cpr)  # no is_active filter — intentional
        except ORMPatient.DoesNotExist:
            return None
        return self._to_domain(orm_obj)

    def get_by_id(self, patient_id: int) -> PatientDetail | None:
        try:
            orm_obj = ORMPatient.objects.get(id=patient_id, is_active=True)
        except ORMCourse.DoesNotExist:
            return None
        return self._to_domain(orm_obj)

    def get_by_number(self, number: int) -> PatientDetail | None:
        try:
            orm_obj = ORMPatient.objects.get(patient_number=number, is_active=True)
        except ORMCourse.DoesNotExist:
            return None
        return self._to_domain(orm_obj)

    def get_active_patients(self) -> list[PatientDetail]:
        orm_patients = ORMPatient.objects.filter(
            is_active=True,
        )
        return [self._to_domain(a) for a in orm_patients]


    def reactivate(self, patient_id: int) -> PatientDetail:
        orm_obj = ORMPatient.objects.get(id=patient_id)
        orm_obj.is_active = True
        orm_obj.save()
        return self._to_domain(orm_obj)
    
    def save(self, patient: PatientDetail) -> PatientDetail:
        if patient.id is None:
            orm_obj = ORMPatient.objects.create(
                first_name = patient.first_name,
                last_name = patient.last_name,
                gender = patient.gender.value,
                CPR_number = patient.CPR_number,
                is_active = patient.is_active,
            )
        else:
            orm_obj = ORMPatient.objects.get(id=patient.id)
            orm_obj.patient_number = patient.patient_number
            orm_obj.first_name = patient.first_name
            orm_obj.last_name = patient.last_name
            orm_obj.gender = patient.gender.value
            orm_obj.CPR_number = patient.CPR_number
            orm_obj.is_active = patient.is_active
            orm_obj.save()
        return self._to_domain(orm_obj)
    
    @staticmethod
    def _to_domain(orm_obj: ORMPatient) -> PatientDetail:
        return PatientDetail(
            id=orm_obj.id,
            patient_number=orm_obj.patient_number,
            first_name=orm_obj.first_name,
            last_name=orm_obj.last_name,
            CPR_number=orm_obj.CPR_number,
            gender=Gender(orm_obj.gender),
            is_active=orm_obj.is_active,
        )