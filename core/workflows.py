from datetime import datetime
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Max
from django.utils import timezone

from .models import Appointment, ConsultationRecord, FollowUpReminder, Patient, StaffNotification


TERMINAL_APPOINTMENT_STATUSES = {"Completed", "Cancelled", "No-show"}
OPEN_APPOINTMENT_STATUSES = {"Pending", "Approved", "Scheduled", "Rescheduled"}
APPOINTMENT_TRANSITIONS = {
    "Pending": {"Pending", "Approved", "Scheduled", "Rescheduled", "Cancelled"},
    "Approved": {"Approved", "Scheduled", "Rescheduled", "Cancelled"},
    "Scheduled": {"Scheduled", "Rescheduled", "Cancelled", "No-show"},
    "Rescheduled": {"Rescheduled", "Scheduled", "Cancelled", "No-show"},
    "Completed": {"Completed"},
    "Cancelled": {"Cancelled"},
    "No-show": {"No-show"},
}


def validate_future_slot(appointment_date, appointment_time):
    value = timezone.make_aware(datetime.combine(appointment_date, appointment_time), timezone.get_current_timezone())
    if value <= timezone.now():
        raise ValidationError("Appointment date and time must be in the future.")


def validate_appointment_transition(current_status, new_status):
    if new_status == "Completed":
        raise ValidationError("Complete an appointment by saving its consultation record.")
    if new_status not in APPOINTMENT_TRANSITIONS.get(current_status, set()):
        raise ValidationError(f"An appointment cannot move from {current_status} to {new_status}.")


def generate_patient_code():
    while True:
        code = f"PAT-{uuid4().hex[:10].upper()}"
        if not Patient.objects.filter(patient_code=code).exists():
            return code


def save_patient_profile(form):
    with transaction.atomic():
        patient = form.save(commit=False)
        if patient.pk:
            Patient.objects.select_for_update().get(pk=patient.pk)
        patient.save()
        if patient.user_id:
            if not patient.email:
                raise ValidationError("An email address is required for a portal-enabled patient.")
            user = patient.user.__class__.objects.select_for_update().get(pk=patient.user_id)
            user.email = patient.email
            user.username = patient.email
            user.save(update_fields=["email", "username"])
        return patient


def set_patient_active(patient, *, active):
    with transaction.atomic():
        patient = Patient.objects.select_for_update().get(pk=patient.pk)
        desired_status = "Active" if active else "Inactive"
        if patient.status == desired_status and (not patient.user_id or patient.user.is_active == active):
            return patient, False, 0
        patient.status = desired_status
        patient.save(update_fields=["status", "updated_at"])
        if patient.user_id:
            user = patient.user.__class__.objects.select_for_update().get(pk=patient.user_id)
            user.is_active = active
            user.save(update_fields=["is_active"])
        cancelled = 0
        if not active:
            cancelled = Appointment.objects.filter(
                patient=patient,
                appointment_date__gte=timezone.localdate(),
                status__in=OPEN_APPOINTMENT_STATUSES,
            ).update(status="Cancelled", updated_at=timezone.now())
        return patient, True, cancelled


def create_appointment(*, patient, appointment_date, appointment_time, department, consultation_type, reason="", specialist=None, provider_name="To be assigned", priority="Medium", status="Pending", notes=""):
    validate_future_slot(appointment_date, appointment_time)
    if status not in {"Pending", "Approved", "Scheduled"}:
        raise ValidationError("A new appointment must be pending, approved, or scheduled.")
    for attempt in range(3):
        try:
            with transaction.atomic():
                if Appointment.objects.filter(
                    patient=patient,
                    appointment_date=appointment_date,
                    appointment_time=appointment_time,
                    status__in=OPEN_APPOINTMENT_STATUSES,
                ).exists():
                    raise ValidationError("This patient already has an appointment in the selected time slot.")
                queue = (Appointment.objects.filter(appointment_date=appointment_date).aggregate(value=Max("queue_number"))["value"] or 0) + 1
                return Appointment.objects.create(
                    patient=patient, appointment_date=appointment_date, appointment_time=appointment_time,
                    department=department, consultation_type=consultation_type, reason=reason,
                    specialist=specialist,
                    provider_name=(specialist.user.get_full_name() or specialist.user.username) if specialist else (provider_name or "To be assigned"),
                    priority=priority, status=status,
                    notes=notes, queue_number=queue,
                )
        except IntegrityError:
            if attempt == 2:
                raise ValidationError("The selected queue or time slot was just taken. Please try again.")


def update_appointment(appointment, cleaned_data, *, is_patient=False):
    with transaction.atomic():
        locked = Appointment.objects.select_for_update().get(pk=appointment.pk)
        if locked.status in TERMINAL_APPOINTMENT_STATUSES:
            raise ValidationError(f"A {locked.status.lower()} appointment can no longer be changed.")
        old_date = locked.appointment_date
        new_date = cleaned_data.get("appointment_date", locked.appointment_date)
        new_time = cleaned_data.get("appointment_time", locked.appointment_time)
        if (new_date, new_time) != (locked.appointment_date, locked.appointment_time):
            validate_future_slot(new_date, new_time)
        if is_patient:
            allowed_fields = {"appointment_date", "appointment_time", "department", "consultation_type", "reason"}
            new_status = "Rescheduled" if (new_date, new_time) != (locked.appointment_date, locked.appointment_time) else locked.status
        else:
            allowed_fields = {"appointment_date", "appointment_time", "department", "specialist", "consultation_type", "priority", "status", "reason", "notes"}
            new_status = cleaned_data.get("status", locked.status)
            validate_appointment_transition(locked.status, new_status)
        for field in allowed_fields:
            if field in cleaned_data:
                setattr(locked, field, cleaned_data[field])
        locked.status = new_status
        if locked.specialist_id:
            locked.provider_name = locked.specialist.user.get_full_name() or locked.specialist.user.username
        else:
            locked.provider_name = "To be assigned"
        if new_date != old_date:
            locked.queue_number = (Appointment.objects.filter(appointment_date=new_date).aggregate(value=Max("queue_number"))["value"] or 0) + 1
        try:
            locked.save()
        except IntegrityError as error:
            raise ValidationError("The selected appointment time or queue is already in use.") from error
        return locked


def confirm_appointment(appointment, *, specialist, appointment_date, appointment_time):
    validate_future_slot(appointment_date, appointment_time)
    with transaction.atomic():
        locked = Appointment.objects.select_for_update().get(pk=appointment.pk)
        if locked.status in TERMINAL_APPOINTMENT_STATUSES:
            raise ValidationError(f"A {locked.status.lower()} appointment cannot be confirmed.")
        conflict = Appointment.objects.filter(
            specialist=specialist,
            appointment_date=appointment_date,
            appointment_time=appointment_time,
            status__in=OPEN_APPOINTMENT_STATUSES,
        ).exclude(pk=locked.pk).exists()
        if conflict:
            raise ValidationError("The selected specialist is no longer available for this schedule.")
        locked.specialist = specialist
        locked.provider_name = specialist.user.get_full_name() or specialist.user.username
        locked.appointment_date = appointment_date
        locked.appointment_time = appointment_time
        locked.status = "Scheduled"
        locked.specialist_confirmed_at = None
        locked.save()
        StaffNotification.objects.create(
            recipient=specialist,
            appointment=locked,
            title="New appointment assigned",
            message=f"{locked.patient.name} is scheduled on {appointment_date} at {appointment_time:%H:%M}.",
        )
        return locked


def acknowledge_appointment(appointment, *, specialist):
    with transaction.atomic():
        locked = Appointment.objects.select_for_update().get(pk=appointment.pk)
        if locked.specialist_id != specialist.pk:
            raise ValidationError("This appointment is not assigned to your specialist account.")
        if locked.status not in {"Approved", "Scheduled", "Rescheduled"}:
            raise ValidationError("Only an active assigned appointment can be acknowledged.")
        if locked.specialist_confirmed_at:
            return locked, False
        locked.specialist_confirmed_at = timezone.now()
        locked.save(update_fields=["specialist_confirmed_at", "updated_at"])
        return locked, True


def cancel_appointment_workflow(appointment):
    with transaction.atomic():
        locked = Appointment.objects.select_for_update().get(pk=appointment.pk)
        if locked.status == "Cancelled":
            return locked, False
        if locked.status in {"Completed", "No-show"}:
            raise ValidationError(f"A {locked.status.lower()} appointment cannot be cancelled.")
        locked.status = "Cancelled"
        locked.save(update_fields=["status", "updated_at"])
        return locked, True


def save_consultation(*, record, appointment, cleaned_data):
    with transaction.atomic():
        appointment = Appointment.objects.select_for_update().select_related("patient").get(pk=appointment.pk)
        if record:
            record = ConsultationRecord.objects.select_for_update().get(pk=record.pk)
            if record.appointment_id != appointment.pk:
                raise ValidationError("The appointment linked to an existing consultation cannot be changed.")
        else:
            if appointment.appointment_date > timezone.localdate():
                raise ValidationError("A future appointment cannot be completed.")
            if appointment.status not in {"Approved", "Scheduled", "Rescheduled"}:
                raise ValidationError("Only an approved or scheduled appointment can be completed.")
            if ConsultationRecord.objects.filter(appointment=appointment).exists():
                raise ValidationError("This appointment already has a consultation record.")
            record = ConsultationRecord(appointment=appointment)
        follow_up_date = cleaned_data.get("follow_up_date")
        if follow_up_date and follow_up_date < appointment.appointment_date:
            raise ValidationError("Follow-up date cannot be earlier than the consultation date.")
        for field in ["diagnosis", "treatment", "prescription", "clinical_notes", "follow_up_date"]:
            setattr(record, field, cleaned_data.get(field, "") if field != "follow_up_date" else follow_up_date)
        record.save()
        appointment.status = "Completed"
        appointment.save(update_fields=["status", "updated_at"])
        latest = Appointment.objects.filter(
            patient=appointment.patient, status="Completed", appointment_date__lte=timezone.localdate(),
            consultation_record__isnull=False,
        ).aggregate(value=Max("appointment_date"))["value"]
        appointment.patient.last_visit = latest
        appointment.patient.save(update_fields=["last_visit", "updated_at"])
        if follow_up_date:
            message = f"Follow-up healthcare service scheduled for {follow_up_date}."
            reminder = FollowUpReminder.objects.select_for_update().filter(consultation=record).first()
            if reminder and reminder.status == "Completed" and reminder.reminder_date != follow_up_date:
                raise ValidationError("A completed follow-up cannot be rescheduled from the consultation record.")
            if not reminder:
                FollowUpReminder.objects.create(
                    consultation=record, patient=appointment.patient,
                    reminder_date=follow_up_date, message=message,
                )
            elif reminder.reminder_date != follow_up_date:
                reminder.reminder_date = follow_up_date
                reminder.message = message
                reminder.status = "Pending"
                reminder.notification = None
                reminder.save(update_fields=["reminder_date", "message", "status", "notification"])
        else:
            FollowUpReminder.objects.filter(consultation=record, status__in=["Pending", "Failed", "Sent"]).update(status="Cancelled")
        return record
