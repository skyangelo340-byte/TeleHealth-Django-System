from .models import Patient, Appointment, TriageCase, ConsultationRecord, Notification, FollowUpReminder


def patient_to_dict(p: Patient):
    return {
        "id": p.id, "patient_code": p.patient_code, "name": p.name,
        "date_of_birth": p.date_of_birth.isoformat(), "sex": p.sex,
        "barangay": p.barangay, "contact_number": p.contact_number,
        "email": p.email, "philhealth_number": p.philhealth_number,
        "address": p.address, "allergies": p.allergies,
        "emergency_contact": p.emergency_contact, "status": p.status,
        "last_visit": p.last_visit.isoformat() if p.last_visit else None,
        "has_account": bool(p.user_id),
    }


def appointment_to_dict(a: Appointment):
    return {
        "id": a.id, "patient_id": a.patient_id, "patient_code": a.patient.patient_code,
        "patient_name": a.patient.name, "department": a.department,
        "provider_name": a.provider_name, "specialist_id": a.specialist_id,
        "appointment_date": a.appointment_date.isoformat(),
        "appointment_time": a.appointment_time.strftime("%H:%M"), "queue_number": a.queue_number,
        "status": a.status, "priority": a.priority, "consultation_type": a.consultation_type,
        "reason": a.reason, "notes": a.notes,
        "specialist_confirmed_at": a.specialist_confirmed_at.isoformat() if a.specialist_confirmed_at else None,
    }


def triage_to_dict(t: TriageCase):
    return {
        "id": t.id, "patient_id": t.patient_id, "patient_code": t.patient.patient_code,
        "patient_name": t.patient.name, "symptoms": t.symptoms, "questionnaire": t.questionnaire,
        "predicted_specialty": t.predicted_specialty, "confidence": float(t.confidence),
        "priority": t.priority, "recommendation": t.recommendation,
        "created_at": t.created_at.isoformat(),
    }


def consultation_to_dict(c: ConsultationRecord):
    return {
        "id": c.id, "appointment_id": c.appointment_id,
        "appointment_date": c.appointment.appointment_date.isoformat(),
        "provider_name": c.appointment.provider_name, "department": c.appointment.department,
        "diagnosis": c.diagnosis, "treatment": c.treatment, "prescription": c.prescription,
        "clinical_notes": c.clinical_notes,
        "follow_up_date": c.follow_up_date.isoformat() if c.follow_up_date else None,
    }


def notification_to_dict(n: Notification):
    return {
        "id": n.id, "channel": n.channel, "title": n.title, "message": n.message,
        "status": n.status, "is_read": n.is_read,
        "scheduled_for": n.scheduled_for.isoformat() if n.scheduled_for else None,
        "sent_at": n.sent_at.isoformat() if n.sent_at else None,
        "created_at": n.created_at.isoformat(),
    }


def reminder_to_dict(r: FollowUpReminder):
    return {
        "id": r.id, "reminder_date": r.reminder_date.isoformat(), "message": r.message,
        "status": r.status, "consultation_id": r.consultation_id,
    }
