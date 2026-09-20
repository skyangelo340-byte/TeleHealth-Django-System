import json
from urllib import request as urlrequest
from urllib.error import URLError, HTTPError

from django.conf import settings
from django.utils import timezone

from .models import Notification, Patient


def deliver_sms_notification(notification: Notification) -> Notification:
    if notification.status == "Sent":
        return notification
    webhook_url = getattr(settings, "SMS_WEBHOOK_URL", "").strip()
    if not webhook_url:
        notification.status = "Pending"
        notification.save(update_fields=["status"])
        return notification
    payload = json.dumps({"to": notification.patient.contact_number, "message": notification.message, "title": notification.title}).encode("utf-8")
    req = urlrequest.Request(webhook_url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlrequest.urlopen(req, timeout=10) as response:
            if 200 <= response.status < 300:
                notification.status = "Sent"
                notification.sent_at = timezone.now()
            else:
                notification.status = "Failed"
    except (URLError, HTTPError, TimeoutError):
        notification.status = "Failed"
    notification.save(update_fields=["status", "sent_at"])
    return notification


def send_sms_notification(patient: Patient, title: str, message: str) -> Notification:
    """Create an SMS notification and optionally deliver it through a configured webhook.

    Expected webhook request JSON:
        {"to": "+639xxxxxxxxx", "message": "...", "title": "..."}

    Any provider or middleware that accepts this payload can be connected with
    the SMS_WEBHOOK_URL environment variable. Without a webhook, the message
    remains Pending in the built-in notification outbox for safe local demos.
    """
    notification = Notification.objects.create(
        patient=patient, channel="SMS", title=title, message=message, status="Pending"
    )
    return deliver_sms_notification(notification)


def send_system_notification(patient: Patient, title: str, message: str) -> Notification:
    return Notification.objects.create(
        patient=patient,
        channel="System",
        title=title,
        message=message,
        status="Sent",
        sent_at=timezone.now(),
    )
