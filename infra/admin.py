from django.contrib import admin
from infra.models import TreatmentSpace, SpaceSchedule, ScheduleClosure, ScheduleOverride


@admin.register(TreatmentSpace)
class TreatmentSpaceAdmin(admin.ModelAdmin):
    list_display = ("id", "name")


@admin.register(SpaceSchedule)
class SpaceScheduleAdmin(admin.ModelAdmin):
    list_display = ("space", "weekday", "open_time", "close_time")
    list_filter = ("space", "weekday")


@admin.register(ScheduleClosure)
class ScheduleClosureAdmin(admin.ModelAdmin):
    list_display = ("date", "space", "reason")
    list_filter = ("space",)
    ordering = ("date",)


@admin.register(ScheduleOverride)
class ScheduleOverrideAdmin(admin.ModelAdmin):
    list_display = ("date", "space", "open_time", "close_time")
    list_filter = ("space",)
    ordering = ("date",)