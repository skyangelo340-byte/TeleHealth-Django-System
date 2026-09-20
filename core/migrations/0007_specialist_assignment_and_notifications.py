from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("core", "0006_open_appointment_slot_constraint")]

    operations = [
        migrations.AddField(
            model_name="staffprofile",
            name="specialty",
            field=models.CharField(default="General Medicine", max_length=100),
        ),
        migrations.AlterField(
            model_name="staffprofile",
            name="role",
            field=models.CharField(
                choices=[
                    ("Specialist", "Specialist"), ("Physician", "Physician"),
                    ("Nurse", "Nurse"), ("Midwife", "Midwife"),
                    ("Admin", "Admin"), ("BHW", "Barangay Health Worker"),
                ],
                default="BHW", max_length=30,
            ),
        ),
        migrations.RemoveConstraint(model_name="staffprofile", name="staff_valid_role"),
        migrations.AddConstraint(
            model_name="staffprofile",
            constraint=models.CheckConstraint(
                condition=models.Q(role__in=["Specialist", "Physician", "Nurse", "Midwife", "Admin", "BHW"]),
                name="staff_valid_role",
            ),
        ),
        migrations.AddField(
            model_name="appointment",
            name="specialist",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name="assigned_appointments", to="core.staffprofile",
            ),
        ),
        migrations.AddField(
            model_name="appointment",
            name="specialist_confirmed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddConstraint(
            model_name="appointment",
            constraint=models.UniqueConstraint(
                condition=models.Q(
                    specialist__isnull=False,
                    status__in=["Pending", "Approved", "Scheduled", "Rescheduled"],
                ),
                fields=("specialist", "appointment_date", "appointment_time"),
                name="appointment_unique_open_specialist_slot",
            ),
        ),
        migrations.CreateModel(
            name="StaffNotification",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=150)),
                ("message", models.TextField()),
                ("is_read", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("appointment", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="staff_notifications", to="core.appointment")),
                ("recipient", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="notifications", to="core.staffprofile")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddIndex(
            model_name="staffnotification",
            index=models.Index(fields=["recipient", "is_read", "created_at"], name="staff_notice_recipient_idx"),
        ),
    ]
