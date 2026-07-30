"""
Django ORM for persistent data storage, striving to adhere to 3NF

"""

from django.db import models
from django.utils import timezone
from .validators import validate_cpr

#Gender choices, change here if you want them in english instead
class Gender(models.IntegerChoices):
    MALE = 1, "Mand"
    FEMALE = 2, "Kvinde"
    UNASSIGNED = 3, "Ikke tildelt"

class AppointmentStatus(models.IntegerChoices):
    SCHEDULED = 1, "Planlagt"
    FINISHED = 2, "Fuldført"
    CANCELLED = 3, "Aflyst"
    NO_SHOW = 4, "Udeblevet"

class CourseStatus(models.IntegerChoices):
    PLANNED = 1, "Planlagt"
    ACTIVE = 2, "Igangværende"
    COMPLETED = 3, "Fuldført"
    CANCELLED = 4, "Aflyst"

#Add more treatment types if needed
class TreatmentType(models.IntegerChoices):
    V1 = 1, "V1"
    V2 = 2, "V2"

#This can both represent different treatment rooms, or multiple spots within a singular room that allows for treatement of multiple patients at once
class TreatmentSpace(models.Model):
    name = models.CharField(max_length=50)
    location = models.CharField(max_length=100, blank=True)

class Patient(models.Model):
    patient_number = models.IntegerField(unique=True)
    first_name = models.CharField(max_length=30)
    last_name = models.CharField(max_length=60)
    gender = models.IntegerField(choices=Gender.choices, default=Gender.UNASSIGNED)
    #Because it can start with 0, we can't store it as an int
    CPR_number = models.CharField(max_length=10, validators=[validate_cpr], unique=True, db_index=True)
    #Logging
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(blank=True, auto_now=True)
    #Soft delete
    is_active = models.BooleanField(default=True)
  
class TreatmentSlot(models.Model):
    space = models.ForeignKey(TreatmentSpace, on_delete=models.CASCADE)
    start_time = models.DateTimeField(db_index=True)
    end_time = models.DateTimeField()

#For representing holidays or other workdays with no open slots, can also model closure of individual treatment spaces
class ScheduleClosure(models.Model):
    space = models.ForeignKey(
        TreatmentSpace, null=True, blank=True, on_delete=models.CASCADE
    )  #null = hospital-wide closure (e.g. a public holiday)
    date = models.DateField()
    reason = models.CharField(max_length=200, blank=True)

class TreatmentCourse(models.Model):
    patient = models.ForeignKey(Patient, on_delete=models.PROTECT)
    planned_treatments = models.SmallIntegerField(default=0)
    #Logging
    status = models.IntegerField(choices=CourseStatus.choices, default=CourseStatus.PLANNED)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(blank=True, auto_now=True)
    #Soft delete
    is_active = models.BooleanField(default=True)

class TreatmentAppointment(models.Model):
    course = models.ForeignKey(TreatmentCourse, on_delete=models.PROTECT)
    slot = models.OneToOneField(TreatmentSlot, on_delete=models.PROTECT)
    treatment_number = models.SmallIntegerField()
    treatment_type = models.IntegerField(choices=TreatmentType.choices, default=TreatmentType.V1)
    note = models.CharField(max_length=200, blank=True)
    #Logging
    status = models.IntegerField(choices=AppointmentStatus.choices, default=AppointmentStatus.SCHEDULED)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(blank=True, auto_now=True)
    #Soft delete
    is_active = models.BooleanField(default=True)

class SpaceSchedule(models.Model):
    space = models.ForeignKey(TreatmentSpace, on_delete=models.CASCADE)
    weekday = models.IntegerField(choices=[(0,"Man"),(1,"Tir"),(2,"Ons"),(3,"Tor"),(4,"Fre"),(5,"Lør"),(6,"Søn")])
    open_time = models.TimeField()
    close_time = models.TimeField()
    slot_duration_minutes = models.PositiveSmallIntegerField(default=30)

    class Meta:
        unique_together = ("space", "weekday")

class ScheduleClosure(models.Model):
    space = models.ForeignKey(TreatmentSpace, null=True, blank=True, on_delete=models.CASCADE)
    date = models.DateField()
    reason = models.CharField(max_length=200, blank=True)


