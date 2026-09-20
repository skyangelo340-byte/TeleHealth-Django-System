from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0005_alter_appointment_patient_and_more")]

    operations = [
        migrations.RemoveConstraint(
            model_name="appointment",
            name="appointment_unique_patient_slot",
        ),
        migrations.AddConstraint(
            model_name="appointment",
            constraint=models.UniqueConstraint(
                condition=models.Q(status__in=["Pending", "Approved", "Scheduled", "Rescheduled"]),
                fields=("patient", "appointment_date", "appointment_time"),
                name="appointment_unique_open_patient_slot",
            ),
        ),
    ]
