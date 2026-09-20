from django.core.management import BaseCommand
from django.db import transaction
from django.utils import timezone

from core.models import FollowUpReminder
from core.services import deliver_sms_notification, send_sms_notification


class Command(BaseCommand):
    help = "Create/send SMS notifications for follow-up reminders due today."

    def handle(self, *args, **options):
        today = timezone.localdate()
        reminder_ids = list(FollowUpReminder.objects.filter(reminder_date__lte=today, status__in=["Pending", "Failed"]).values_list("id", flat=True))
        count = 0
        for reminder_id in reminder_ids:
            with transaction.atomic():
                reminder = FollowUpReminder.objects.select_for_update().select_related("patient", "notification").get(pk=reminder_id)
                notification = reminder.notification
                if notification is None:
                    notification = send_sms_notification(reminder.patient, "Follow-up reminder", reminder.message)
                    reminder.notification = notification
                else:
                    notification = deliver_sms_notification(notification)
                reminder.status = "Sent" if notification.status == "Sent" else notification.status
                reminder.save(update_fields=["notification", "status"])
                count += 1
        self.stdout.write(self.style.SUCCESS(f"Processed {count} follow-up reminder(s); pending messages remain queued."))
