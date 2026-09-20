from datetime import timedelta
import warnings

from django.contrib.auth.models import User
from django.core.management import BaseCommand, call_command
from django.db.models import Max
from django.utils import timezone

from core.models import (
    Patient, StaffProfile, Appointment, ConsultationRecord,
    FollowUpReminder, Notification, SMSCampaign,
)


class Command(BaseCommand):
    help = "Load the supplied TeleHealth datasets and create demo user accounts."

    def handle(self, *args, **options):
        fixtures = ["patients", "appointments", "triage_cases", "sms_campaigns", "audit_logs", "inventory"]
        for fixture in fixtures:
            self.stdout.write(f"Loading {fixture}...")
            # Django interprets the supplied timezone-less fixture timestamps in TIME_ZONE.
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", message="DateTimeField .* received a naive datetime", category=RuntimeWarning)
                call_command("loaddata", fixture, verbosity=0)

        today = timezone.localdate()
        Appointment.objects.filter(status="Completed", appointment_date__gt=today, consultation_record__isnull=True).update(status="Scheduled")
        Appointment.objects.filter(status="Scheduled", appointment_date__lt=today, consultation_record__isnull=True).update(status="No-show")
        Appointment.objects.filter(
            patient__status="Inactive", appointment_date__gte=today,
            status__in=["Pending", "Approved", "Scheduled", "Rescheduled"],
        ).update(status="Cancelled")
        SMSCampaign.objects.filter(status="Draft").update(sent_count=0)
        SMSCampaign.objects.filter(status="Scheduled", scheduled_date__lt=timezone.now()).update(status="Failed")

        admin, _ = User.objects.get_or_create(
            username="admin@rhu.local",
            defaults={"email": "admin@rhu.local", "first_name": "Maria", "last_name": "Lourdes", "is_staff": True, "is_superuser": True},
        )
        admin.email = "admin@rhu.local"
        admin.first_name = "Maria"
        admin.last_name = "Lourdes"
        admin.is_staff = True
        admin.is_superuser = True
        admin.is_active = True
        admin.set_password("Admin123!")
        admin.save()
        StaffProfile.objects.update_or_create(user=admin, defaults={"role": "Admin", "employee_id": "RHU-ADMIN-001", "status": "Active"})

        staff_specs = [
            ("specialist@rhu.local", "Michael", "Tan", "Specialist", "RHU-SPC-001", "General Medicine"),
        ]
        for email, first, last, role, employee_id, specialty in staff_specs:
            user, _ = User.objects.get_or_create(username=email, defaults={"email": email, "first_name": first, "last_name": last, "is_staff": True})
            user.email, user.first_name, user.last_name, user.is_staff, user.is_active = email, first, last, True, True
            user.set_password("Specialist123!")
            user.save()
            StaffProfile.objects.update_or_create(user=user, defaults={"role": role, "employee_id": employee_id, "specialty": specialty, "status": "Active"})

        patient = Patient.objects.order_by("id").first()
        if patient:
            patient_user, _ = User.objects.get_or_create(
                username="patient@telehealth.local",
                defaults={"email": "patient@telehealth.local", "first_name": "Liza Marie", "last_name": "Villanueva"},
            )
            patient_user.email = "patient@telehealth.local"
            patient_user.first_name = "Liza Marie"
            patient_user.last_name = "Villanueva"
            patient_user.is_active = True
            patient_user.set_password("Patient123!")
            patient_user.save()
            patient.user = patient_user
            patient.email = "patient@telehealth.local"
            patient.save()

            completed = Appointment.objects.filter(patient=patient, status="Completed", appointment_date__lte=today).first()
            if not completed:
                completed_date = timezone.localdate() - timedelta(days=14)
                queue_number = (Appointment.objects.filter(appointment_date=completed_date).aggregate(value=Max("queue_number"))["value"] or 0) + 1
                completed = Appointment.objects.create(
                    patient=patient, department="General Medicine", provider_name="Dr. Michael Tan",
                    appointment_date=completed_date, appointment_time="09:30",
                    queue_number=queue_number, status="Completed", priority="Medium", consultation_type="Online",
                    reason="Follow-up consultation", notes="Completed demo consultation",
                )
            record, _ = ConsultationRecord.objects.get_or_create(
                appointment=completed,
                defaults={
                    "diagnosis": "Upper respiratory tract infection - resolved",
                    "treatment": "Hydration, rest, and symptom monitoring",
                    "prescription": "Paracetamol as directed when needed",
                    "clinical_notes": "Patient reported improvement during follow-up.",
                    "follow_up_date": timezone.localdate() + timedelta(days=7),
                },
            )
            if record.follow_up_date:
                FollowUpReminder.objects.update_or_create(
                    consultation=record,
                    defaults={
                        "patient": patient, "reminder_date": record.follow_up_date,
                        "message": "Follow-up consultation reminder. Please review your treatment progress.",
                        "status": "Pending", "notification": None,
                    },
                )
            Notification.objects.get_or_create(
                patient=patient, title="Welcome to TeleHealth RHU",
                defaults={
                    "channel": "SMS", "message": "Your patient portal is ready. You can schedule consultations and review your healthcare history.",
                    "status": "Sent", "sent_at": timezone.now(),
                },
            )

        Appointment.objects.filter(status="Completed", appointment_date__gt=today, consultation_record__isnull=True).update(status="Scheduled")
        Appointment.objects.filter(status="Scheduled", appointment_date__lt=today, consultation_record__isnull=True).update(status="No-show")
        Appointment.objects.filter(
            patient__status="Inactive", appointment_date__gte=today,
            status__in=["Pending", "Approved", "Scheduled", "Rescheduled"],
        ).update(status="Cancelled")
        SMSCampaign.objects.filter(status="Draft").update(sent_count=0)
        SMSCampaign.objects.filter(status="Scheduled", scheduled_date__lt=timezone.now()).update(status="Failed")
        for current_patient in Patient.objects.all():
            latest = Appointment.objects.filter(
                patient=current_patient, status="Completed", appointment_date__lte=today,
                consultation_record__isnull=False,
            ).aggregate(value=Max("appointment_date"))["value"]
            if current_patient.last_visit != latest:
                current_patient.last_visit = latest
                current_patient.save(update_fields=["last_visit", "updated_at"])

        self.stdout.write(self.style.SUCCESS("TeleHealth datasets and demo accounts are ready."))
        self.stdout.write("Admin: admin@rhu.local / Admin123!")
        self.stdout.write("Specialist: specialist@rhu.local / Specialist123!")
        self.stdout.write("Patient: patient@telehealth.local / Patient123!")
