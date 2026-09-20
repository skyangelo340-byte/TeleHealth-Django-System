from django.contrib.auth.models import User
from django.db import models
from django.db.models import F, Q


class Patient(models.Model):
    SEX_CHOICES = [("M", "Male"), ("F", "Female"), ("O", "Other")]
    STATUS_CHOICES = [("Active", "Active"), ("Inactive", "Inactive")]

    user = models.OneToOneField(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="patient_profile")
    patient_code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=150)
    date_of_birth = models.DateField()
    sex = models.CharField(max_length=1, choices=SEX_CHOICES)
    barangay = models.CharField(max_length=100, default="Lumang Bayan")
    contact_number = models.CharField(max_length=20)
    email = models.EmailField(blank=True)
    philhealth_number = models.CharField(max_length=30, blank=True)
    address = models.CharField(max_length=255, blank=True)
    allergies = models.TextField(blank=True)
    emergency_contact = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="Active")
    last_visit = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True, null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "name"]
        constraints = [
            models.CheckConstraint(condition=Q(sex__in=["M", "F", "O"]), name="patient_valid_sex"),
            models.CheckConstraint(condition=Q(status__in=["Active", "Inactive"]), name="patient_valid_status"),
            models.UniqueConstraint(fields=["philhealth_number"], condition=~Q(philhealth_number=""), name="patient_unique_philhealth"),
        ]

    def __str__(self):
        return f"{self.patient_code} - {self.name}"


class StaffProfile(models.Model):
    ROLE_CHOICES = [
        ("Specialist", "Specialist"), ("Physician", "Physician"), ("Nurse", "Nurse"), ("Midwife", "Midwife"),
        ("Admin", "Admin"), ("BHW", "Barangay Health Worker"),
    ]
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="staff_profile")
    role = models.CharField(max_length=30, choices=ROLE_CHOICES, default="BHW")
    employee_id = models.CharField(max_length=50, blank=True)
    specialty = models.CharField(max_length=100, default="General Medicine")
    STATUS_CHOICES = [("Active", "Active"), ("Inactive", "Inactive")]
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="Active")

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(role__in=["Specialist", "Physician", "Nurse", "Midwife", "Admin", "BHW"]), name="staff_valid_role"),
            models.CheckConstraint(condition=Q(status__in=["Active", "Inactive"]), name="staff_valid_status"),
            models.UniqueConstraint(fields=["employee_id"], condition=~Q(employee_id=""), name="staff_unique_employee_id"),
        ]

    def __str__(self):
        return f"{self.user.get_full_name() or self.user.username} - {self.specialty}"


class Appointment(models.Model):
    STATUS_CHOICES = [
        ("Pending", "Pending"), ("Approved", "Approved"), ("Scheduled", "Scheduled"),
        ("Rescheduled", "Rescheduled"), ("Completed", "Completed"),
        ("Cancelled", "Cancelled"), ("No-show", "No-show"),
    ]
    PRIORITY_CHOICES = [("Low", "Low"), ("Medium", "Medium"), ("High", "High")]
    TYPE_CHOICES = [("Online", "Online Consultation"), ("In-person", "In-person Consultation")]

    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name="appointments")
    specialist = models.ForeignKey(
        StaffProfile, on_delete=models.PROTECT, null=True, blank=True,
        related_name="assigned_appointments",
    )
    department = models.CharField(max_length=100, default="General Medicine")
    provider_name = models.CharField(max_length=150, blank=True, default="To be assigned")
    appointment_date = models.DateField()
    appointment_time = models.TimeField()
    queue_number = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default="Pending")
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default="Medium")
    consultation_type = models.CharField(max_length=20, choices=TYPE_CHOICES, default="Online")
    reason = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True, null=True, blank=True)
    specialist_confirmed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-appointment_date", "-appointment_time"]
        constraints = [
            models.CheckConstraint(condition=Q(queue_number__gt=0), name="appointment_positive_queue"),
            models.CheckConstraint(condition=Q(status__in=["Pending", "Approved", "Scheduled", "Rescheduled", "Completed", "Cancelled", "No-show"]), name="appointment_valid_status"),
            models.CheckConstraint(condition=Q(priority__in=["Low", "Medium", "High"]), name="appointment_valid_priority"),
            models.CheckConstraint(condition=Q(consultation_type__in=["Online", "In-person"]), name="appointment_valid_type"),
            models.UniqueConstraint(fields=["appointment_date", "queue_number"], name="appointment_unique_daily_queue"),
            models.UniqueConstraint(
                fields=["patient", "appointment_date", "appointment_time"],
                condition=Q(status__in=["Pending", "Approved", "Scheduled", "Rescheduled"]),
                name="appointment_unique_open_patient_slot",
            ),
            models.UniqueConstraint(
                fields=["specialist", "appointment_date", "appointment_time"],
                condition=Q(
                    specialist__isnull=False,
                    status__in=["Pending", "Approved", "Scheduled", "Rescheduled"],
                ),
                name="appointment_unique_open_specialist_slot",
            ),
        ]
        indexes = [models.Index(fields=["appointment_date", "status"], name="appointment_date_status_idx")]

    def __str__(self):
        return f"{self.patient} - {self.appointment_date} {self.appointment_time}"


class TriageCase(models.Model):
    PRIORITY_CHOICES = [("Low", "Low"), ("Medium", "Medium"), ("High", "High")]

    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name="triage_cases")
    symptoms = models.JSONField(default=list)
    questionnaire = models.JSONField(default=dict, blank=True)
    predicted_specialty = models.CharField(max_length=50)
    confidence = models.DecimalField(max_digits=5, decimal_places=2)
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES)
    recommendation = models.TextField(blank=True)
    created_at = models.DateTimeField()

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(condition=Q(priority__in=["Low", "Medium", "High"]), name="triage_valid_priority"),
            models.CheckConstraint(condition=Q(confidence__gte=0, confidence__lte=100), name="triage_confidence_range"),
        ]

    def __str__(self):
        return f"Triage {self.pk} - {self.patient} - {self.predicted_specialty}"


class ConsultationRecord(models.Model):
    appointment = models.OneToOneField(Appointment, on_delete=models.PROTECT, related_name="consultation_record")
    diagnosis = models.TextField(blank=True)
    treatment = models.TextField(blank=True)
    prescription = models.TextField(blank=True)
    clinical_notes = models.TextField(blank=True)
    follow_up_date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Consultation record for {self.appointment}"


class Notification(models.Model):
    CHANNEL_CHOICES = [("SMS", "SMS"), ("System", "System")]
    STATUS_CHOICES = [("Pending", "Pending"), ("Sent", "Sent"), ("Failed", "Failed")]

    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name="notifications")
    channel = models.CharField(max_length=10, choices=CHANNEL_CHOICES, default="System")
    title = models.CharField(max_length=150)
    message = models.TextField()
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="Pending")
    is_read = models.BooleanField(default=False)
    scheduled_for = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(condition=Q(channel__in=["SMS", "System"]), name="notification_valid_channel"),
            models.CheckConstraint(condition=Q(status__in=["Pending", "Sent", "Failed"]), name="notification_valid_status"),
        ]
        indexes = [models.Index(fields=["patient", "is_read", "created_at"], name="notification_patient_read_idx")]


class StaffNotification(models.Model):
    recipient = models.ForeignKey(StaffProfile, on_delete=models.CASCADE, related_name="notifications")
    appointment = models.ForeignKey(Appointment, on_delete=models.CASCADE, null=True, blank=True, related_name="staff_notifications")
    title = models.CharField(max_length=150)
    message = models.TextField()
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["recipient", "is_read", "created_at"], name="staff_notice_recipient_idx")]


class FollowUpReminder(models.Model):
    STATUS_CHOICES = [
        ("Pending", "Pending delivery"), ("Sent", "Sent"), ("Failed", "Delivery failed"),
        ("Completed", "Follow-up completed"), ("Cancelled", "Cancelled"),
    ]
    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name="follow_up_reminders")
    consultation = models.ForeignKey(ConsultationRecord, on_delete=models.PROTECT, null=True, blank=True, related_name="reminders")
    reminder_date = models.DateField()
    message = models.TextField()
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="Pending")
    notification = models.OneToOneField(Notification, on_delete=models.SET_NULL, null=True, blank=True, related_name="follow_up_reminder")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["reminder_date"]
        constraints = [
            models.CheckConstraint(condition=Q(status__in=["Pending", "Sent", "Failed", "Completed", "Cancelled"]), name="reminder_valid_status"),
            models.UniqueConstraint(fields=["consultation"], condition=Q(consultation__isnull=False), name="reminder_unique_consultation"),
        ]
        indexes = [models.Index(fields=["status", "reminder_date"], name="reminder_status_date_idx")]


class SMSCampaign(models.Model):
    STATUS_CHOICES = [("Draft", "Draft"), ("Scheduled", "Scheduled"), ("Sent", "Sent"), ("Failed", "Failed")]
    title = models.CharField(max_length=150)
    message = models.TextField()
    target_group = models.CharField(max_length=100)
    recipient_count = models.PositiveIntegerField(default=0)
    sent_count = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default="Draft")
    scheduled_date = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.title

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(status__in=["Draft", "Scheduled", "Sent", "Failed"]), name="campaign_valid_status"),
            models.CheckConstraint(condition=Q(sent_count__lte=F("recipient_count")), name="campaign_sent_within_recipient_count"),
        ]
        indexes = [models.Index(fields=["status", "scheduled_date"], name="campaign_status_date_idx")]


class AuditLog(models.Model):
    ACTION_CHOICES = [
        ("CREATE", "Create"), ("UPDATE", "Update"), ("DELETE", "Delete"),
        ("LOGIN", "Login"), ("LOGIN_FAILED", "Login Failed"),
    ]
    user = models.CharField(max_length=150)
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    target_model = models.CharField(max_length=50)
    target_id = models.CharField(max_length=50, blank=True)
    ip_address = models.GenericIPAddressField(default="127.0.0.1")
    details = models.TextField(blank=True)
    timestamp = models.DateTimeField()

    class Meta:
        ordering = ["-timestamp"]

    def __str__(self):
        return f"{self.timestamp} {self.user} {self.action} {self.target_model}"


class InventoryItem(models.Model):
    CATEGORY_CHOICES = [("Medicine", "Medicine"), ("Supplies", "Supplies"), ("Equipment", "Equipment")]
    name = models.CharField(max_length=150)
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES)
    unit = models.CharField(max_length=20)
    quantity_on_hand = models.PositiveIntegerField()
    reorder_level = models.PositiveIntegerField()
    last_restocked = models.DateField()

    def __str__(self):
        return self.name

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(category__in=["Medicine", "Supplies", "Equipment"]), name="inventory_valid_category"),
            models.UniqueConstraint(fields=["name", "unit"], name="inventory_unique_name_unit"),
        ]
