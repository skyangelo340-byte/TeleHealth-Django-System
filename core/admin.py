from django.contrib import admin
from .models import (
    Patient, StaffProfile, Appointment, TriageCase, ConsultationRecord,
    Notification, FollowUpReminder, SMSCampaign, AuditLog, InventoryItem, StaffNotification,
)

@admin.register(Patient)
class PatientAdmin(admin.ModelAdmin):
    list_display = ("patient_code", "name", "sex", "barangay", "contact_number", "status", "last_visit")
    list_filter = ("sex", "barangay", "status")
    search_fields = ("patient_code", "name", "contact_number", "philhealth_number", "email")

    def has_delete_permission(self, request, obj=None):
        return False

@admin.register(Appointment)
class AppointmentAdmin(admin.ModelAdmin):
    list_display = ("patient", "appointment_date", "appointment_time", "consultation_type", "department", "status", "priority")
    list_filter = ("status", "priority", "consultation_type", "department")
    search_fields = ("patient__patient_code", "patient__name", "provider_name", "reason")

    def has_delete_permission(self, request, obj=None):
        return False

@admin.register(StaffProfile)
class StaffProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "role", "specialty", "employee_id", "status")
    list_filter = ("role", "specialty", "status")
    search_fields = ("user__first_name", "user__last_name", "user__email", "employee_id", "specialty")


admin.site.register(SMSCampaign)
admin.site.register(InventoryItem)


class RetainedClinicalRecordAdmin(admin.ModelAdmin):
    def has_delete_permission(self, request, obj=None):
        return False


admin.site.register(TriageCase, RetainedClinicalRecordAdmin)
admin.site.register(ConsultationRecord, RetainedClinicalRecordAdmin)
admin.site.register(Notification, RetainedClinicalRecordAdmin)
admin.site.register(FollowUpReminder, RetainedClinicalRecordAdmin)
admin.site.register(StaffNotification, RetainedClinicalRecordAdmin)


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("timestamp", "user", "action", "target_model", "target_id")
    list_filter = ("action", "target_model")
    search_fields = ("user", "target_model", "target_id", "details")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
