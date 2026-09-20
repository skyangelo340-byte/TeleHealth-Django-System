from django.db import migrations
from django.db.models import Max
from django.utils import timezone


def normalize_workflow_data(apps, schema_editor):
    Appointment = apps.get_model("core", "Appointment")
    Patient = apps.get_model("core", "Patient")
    SMSCampaign = apps.get_model("core", "SMSCampaign")
    today = timezone.localdate()

    Appointment.objects.filter(
        status="Completed", appointment_date__gt=today, consultation_record__isnull=True,
    ).update(status="Scheduled")
    Appointment.objects.filter(
        status="Scheduled", appointment_date__lt=today, consultation_record__isnull=True,
    ).update(status="No-show")
    Appointment.objects.filter(
        patient__status="Inactive", appointment_date__gte=today,
        status__in=["Pending", "Approved", "Scheduled", "Rescheduled"],
    ).update(status="Cancelled")

    SMSCampaign.objects.filter(status="Draft").update(sent_count=0)
    SMSCampaign.objects.filter(status="Scheduled", scheduled_date__lt=timezone.now()).update(status="Failed")

    for patient in Patient.objects.all():
        latest = Appointment.objects.filter(
            patient=patient, status="Completed", appointment_date__lte=today,
            consultation_record__isnull=False,
        ).aggregate(value=Max("appointment_date"))["value"]
        if patient.last_visit != latest:
            patient.last_visit = latest
            patient.save(update_fields=["last_visit"])


class Migration(migrations.Migration):
    dependencies = [("core", "0003_followupreminder_notification_and_more")]
    operations = [migrations.RunPython(normalize_workflow_data, migrations.RunPython.noop)]
