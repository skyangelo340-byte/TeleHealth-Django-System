import json
from decimal import Decimal

from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import JsonResponse, HttpResponseNotAllowed
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods

from .models import (
    Patient, StaffProfile, Appointment, TriageCase, ConsultationRecord,
    Notification, FollowUpReminder, SMSCampaign, AuditLog, InventoryItem,
)
from .forms import AppointmentForm, ConsultationForm, PatientForm, PatientProfileForm, PatientRegistrationForm, StaffAppointmentForm, StaffUserForm, SymptomAssessmentForm
from .services import send_sms_notification
from .serializers import (
    patient_to_dict, appointment_to_dict, triage_to_dict,
    consultation_to_dict, notification_to_dict, reminder_to_dict,
)
from .workflows import (
    cancel_appointment_workflow,
    create_appointment,
    generate_patient_code,
    save_consultation,
    save_patient_profile,
    set_patient_active,
    update_appointment,
)


def _json_body(request):
    if not request.body:
        return {}
    try:
        data = json.loads(request.body.decode("utf-8"))
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def _error(message, status=400, **extra):
    return JsonResponse({"ok": False, "error": message, **extra}, status=status)


def _client_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    return forwarded.split(",")[0].strip() if forwarded else request.META.get("REMOTE_ADDR", "127.0.0.1")


def _audit(request, action, target_model, target_id="", details=""):
    username = request.user.username if request.user.is_authenticated else "anonymous"
    AuditLog.objects.create(
        user=username, action=action, target_model=target_model, target_id=str(target_id),
        ip_address=_client_ip(request), details=details, timestamp=timezone.now(),
    )


def _patient_for_request(request):
    if not request.user.is_authenticated:
        return None
    return Patient.objects.filter(user=request.user, status="Active").first()


def _staff_for_request(request):
    if not request.user.is_authenticated:
        return None
    return StaffProfile.objects.filter(user=request.user, status="Active").first()


def _require_auth(request):
    return request.user.is_authenticated


def _require_staff(request):
    return request.user.is_superuser or _staff_for_request(request) is not None


def _require_admin(request):
    staff = _staff_for_request(request)
    return request.user.is_superuser or bool(staff and staff.role == "Admin")


def _require_role(request, *roles):
    staff = _staff_for_request(request)
    return request.user.is_superuser or bool(staff and staff.role in roles)


def _form_error(form, message="Please correct the submitted fields."):
    return _error(message, fields=form.errors.get_json_data())


def _object_for_id(model, value, **filters):
    if isinstance(value, bool):
        return None
    try:
        object_id = int(value)
    except (TypeError, ValueError):
        return None
    if object_id < 1:
        return None
    return model.objects.filter(pk=object_id, **filters).first()


@ensure_csrf_cookie
def spa(request, path=""):
    return render(request, "index.html")


@require_http_methods(["GET"])
def api_health(request):
    return JsonResponse({"ok": True, "service": "TeleHealth Django API", "time": timezone.now().isoformat()})


def _session_payload(request):
    patient = _patient_for_request(request)
    staff = _staff_for_request(request)
    return {
        "authenticated": request.user.is_authenticated,
        "user": {
            "id": request.user.id,
            "username": request.user.username,
            "email": request.user.email,
            "first_name": request.user.first_name,
            "last_name": request.user.last_name,
            "is_superuser": request.user.is_superuser,
        } if request.user.is_authenticated else None,
        "patient": patient_to_dict(patient) if patient else None,
        "staff": {"role": staff.role, "employee_id": staff.employee_id, "status": staff.status} if staff else None,
    }


@require_http_methods(["GET"])
def api_session(request):
    return JsonResponse(_session_payload(request))


@require_http_methods(["POST"])
def api_register(request):
    data = _json_body(request)
    if data is None:
        return _error("Invalid JSON body.")
    values = {
        field: data.get(field, "")
        for field in [
            "first_name", "last_name", "email", "password", "date_of_birth", "sex",
            "contact_number", "barangay", "philhealth_number", "address",
            "emergency_contact",
        ]
    }
    values["confirm_password"] = data.get("confirm_password", data.get("password", ""))
    form = PatientRegistrationForm(values)
    if not form.is_valid():
        duplicate_email = "email" in form.errors and User.objects.filter(email__iexact=str(data.get("email", "")).strip()).exists()
        return _form_error(form, "An account with this email already exists." if duplicate_email else "Please correct the submitted fields.")
    values = form.cleaned_data

    try:
        with transaction.atomic():
            user = User.objects.create_user(
                username=values["email"], email=values["email"], password=values["password"],
                first_name=values["first_name"], last_name=values["last_name"],
            )
            patient = Patient.objects.create(
                user=user, patient_code=generate_patient_code(),
                name=f'{values["last_name"]}, {values["first_name"]}',
                date_of_birth=values["date_of_birth"], sex=values["sex"], barangay=values["barangay"] or "Lumang Bayan",
                contact_number=values["contact_number"], email=values["email"],
                philhealth_number=values["philhealth_number"], address=values["address"],
                emergency_contact=values["emergency_contact"],
            )
    except IntegrityError:
        return _error("Unable to create account because one of the values is already in use.", status=409)
    login(request, user)
    _audit(request, "CREATE", "PatientAccount", patient.id, "Patient self-registration")
    return JsonResponse({"ok": True, "patient": patient_to_dict(patient)}, status=201)


@require_http_methods(["POST"])
def api_login(request):
    data = _json_body(request)
    if data is None:
        return _error("Invalid JSON body.")
    identifier = str(data.get("email") or data.get("username") or "").strip().lower()
    password = str(data.get("password") or "")
    user = authenticate(request, username=identifier, password=password)
    if user is None:
        candidate = User.objects.filter(email__iexact=identifier).first()
        if candidate:
            user = authenticate(request, username=candidate.username, password=password)
    if user is None:
        AuditLog.objects.create(
            user=identifier or "unknown", action="LOGIN_FAILED", target_model="User",
            ip_address=_client_ip(request), details="Invalid login attempt", timestamp=timezone.now(),
        )
        return _error("Invalid email/username or password.", status=401)
    login(request, user)
    _audit(request, "LOGIN", "User", user.id, "Successful login")
    return JsonResponse(_session_payload(request))


@require_http_methods(["POST"])
def api_logout(request):
    if request.user.is_authenticated:
        _audit(request, "UPDATE", "UserSession", request.user.id, "User logged out")
    logout(request)
    return JsonResponse({"ok": True})


@require_http_methods(["GET", "PUT"])
def api_profile(request):
    if not _require_auth(request):
        return _error("Authentication required.", status=401)
    patient = _patient_for_request(request)
    if not patient:
        return _error("This account does not have a patient profile.", status=404)
    if request.method == "GET":
        return JsonResponse({"ok": True, "patient": patient_to_dict(patient)})

    data = _json_body(request)
    if data is None:
        return _error("Invalid JSON body.")
    editable = ["name", "date_of_birth", "sex", "barangay", "contact_number", "email", "philhealth_number", "address", "allergies", "emergency_contact"]
    values = {field: data.get(field, getattr(patient, field)) for field in editable}
    form = PatientProfileForm(values, instance=patient)
    if not form.is_valid():
        return _form_error(form)
    with transaction.atomic():
        patient = save_patient_profile(form)
    _audit(request, "UPDATE", "Patient", patient.id, "Patient updated personal profile")
    return JsonResponse({"ok": True, "patient": patient_to_dict(patient)})


@require_http_methods(["GET", "POST"])
def api_patients(request):
    if not _require_role(request, "Admin", "BHW", "Nurse", "Midwife", "Physician"):
        return _error("Staff authentication required.", status=403 if request.user.is_authenticated else 401)
    if request.method == "POST" and not _require_staff(request):
        return _error("Staff authentication required for patient creation.", status=403)
    qs = Patient.objects.all()
    if request.method == "GET":
        q = request.GET.get("q", "").strip()
        status = request.GET.get("status", "").strip()
        sex = request.GET.get("sex", "").strip()
        barangay = request.GET.get("barangay", "").strip()
        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(patient_code__icontains=q) | Q(contact_number__icontains=q) | Q(philhealth_number__icontains=q))
        if status:
            qs = qs.filter(status=status)
        if sex:
            qs = qs.filter(sex=sex)
        if barangay:
            qs = qs.filter(barangay__icontains=barangay)
        try:
            limit = max(1, min(int(request.GET.get("limit", 100)), 500))
        except (TypeError, ValueError):
            return _error("Limit must be a positive integer.")
        return JsonResponse({"ok": True, "count": qs.count(), "results": [patient_to_dict(p) for p in qs[:limit]]})

    if not _require_role(request, "Admin", "BHW", "Nurse", "Midwife"):
        return _error("Your staff role cannot create patient records.", status=403)
    data = _json_body(request)
    if data is None:
        return _error("Invalid JSON body.")
    values = {**data, "patient_code": data.get("patient_code") or generate_patient_code(), "barangay": data.get("barangay", "Lumang Bayan")}
    form = PatientForm(values)
    if not form.is_valid():
        return _form_error(form)
    patient = save_patient_profile(form)
    _audit(request, "CREATE", "Patient", patient.id, "Patient created through API")
    return JsonResponse({"ok": True, "patient": patient_to_dict(patient)}, status=201)


@require_http_methods(["GET", "PUT", "DELETE"])
def api_patient_detail(request, patient_id):
    patient = Patient.objects.filter(pk=patient_id).first()
    if not patient:
        return _error("Patient not found.", status=404)
    own = _patient_for_request(request)
    if not (_require_staff(request) or (own and own.id == patient.id)):
        return _error("You do not have permission to access this patient.", status=403 if request.user.is_authenticated else 401)
    if request.method == "GET":
        return JsonResponse({"ok": True, "patient": patient_to_dict(patient)})
    if request.method == "DELETE":
        if not _require_admin(request):
            return _error("Administrator authentication required for archival.", status=403)
        patient, _, _ = set_patient_active(patient, active=False)
        _audit(request, "UPDATE", "Patient", patient.id, "Patient archived through API")
        return JsonResponse({"ok": True, "patient": patient_to_dict(patient)})
    data = _json_body(request)
    if data is None:
        return _error("Invalid JSON body.")
    own_update = bool(own and own.id == patient.id and not _require_staff(request))
    if own_update:
        editable = ["name", "date_of_birth", "sex", "barangay", "contact_number", "email", "philhealth_number", "address", "allergies", "emergency_contact"]
        values = {field: data.get(field, getattr(patient, field)) for field in editable}
        form = PatientProfileForm(values, instance=patient)
    else:
        if not _require_role(request, "Admin", "BHW", "Nurse", "Midwife"):
            return _error("Your staff role cannot update patient records.", status=403)
        fields = [field for field in PatientForm.Meta.fields]
        values = {field: data.get(field, getattr(patient, field)) for field in fields}
        form = PatientForm(values, instance=patient)
    if not form.is_valid():
        return _form_error(form)
    patient = save_patient_profile(form)
    _audit(request, "UPDATE", "Patient", patient.id, "Patient updated through API")
    return JsonResponse({"ok": True, "patient": patient_to_dict(patient)})


@require_http_methods(["GET", "POST"])
def api_appointments(request):
    qs = Appointment.objects.select_related("patient")
    current_patient = _patient_for_request(request)
    if request.method == "GET":
        if current_patient:
            qs = qs.filter(patient=current_patient)
        elif not _require_staff(request):
            return _error("Authentication required.", status=401)
        q = request.GET.get("q", "").strip()
        status = request.GET.get("status", "").strip()
        department = request.GET.get("department", "").strip()
        consultation_type = request.GET.get("type", "").strip()
        if q:
            qs = qs.filter(Q(patient__name__icontains=q) | Q(patient__patient_code__icontains=q) | Q(provider_name__icontains=q) | Q(reason__icontains=q))
        if status:
            qs = qs.filter(status=status)
        if department:
            qs = qs.filter(department__icontains=department)
        if consultation_type:
            qs = qs.filter(consultation_type=consultation_type)
        results = [appointment_to_dict(a) for a in qs[:300]]
        if current_patient:
            for item in results:
                item.pop("notes", None)
        return JsonResponse({"ok": True, "count": qs.count(), "results": results})

    if not _require_auth(request):
        return _error("Authentication required to schedule an appointment.", status=401)
    data = _json_body(request)
    if data is None:
        return _error("Invalid JSON body.")
    patient = current_patient
    if _require_staff(request) and data.get("patient_id"):
        if not _require_admin(request):
            return _error("Only administrators can schedule on behalf of a patient.", status=403)
        patient = _object_for_id(Patient, data["patient_id"])
    elif _require_staff(request) and not current_patient and not _require_admin(request):
        return _error("Only administrators can create staff-managed appointments.", status=403)
    if not patient:
        return _error("A patient profile is required to schedule an appointment.", status=400)
    values = {
        "appointment_date": data.get("appointment_date"), "appointment_time": data.get("appointment_time"),
        "department": data.get("department", "General Medicine"), "consultation_type": data.get("consultation_type", "Online"),
        "reason": data.get("reason", ""),
    }
    if current_patient and not _require_staff(request):
        form = AppointmentForm(values)
        status_value = "Pending"
        extra = {}
    else:
        specialist = _object_for_id(StaffProfile, data.get("specialist_id")) if data.get("specialist_id") else None
        if data.get("specialist_id") and not specialist:
            return _error("A valid specialist is required.")
        values.update({"patient": patient.pk, "specialist": specialist.pk if specialist else "", "status": data.get("status", "Scheduled"), "priority": data.get("priority", "Medium"), "notes": data.get("notes", "")})
        form = StaffAppointmentForm(values)
        status_value = values["status"]
        extra = {"specialist": specialist, "priority": values["priority"], "notes": values["notes"]}
    if not form.is_valid():
        return _form_error(form)
    try:
        appt = create_appointment(patient=patient, status=status_value, **{key: form.cleaned_data[key] for key in ["appointment_date", "appointment_time", "department", "consultation_type", "reason"]}, **extra)
    except ValidationError as error:
        return _error(" ".join(error.messages))
    send_sms_notification(
        patient, "Consultation request received",
        f"Your {appt.consultation_type.lower()} consultation request for {appt.appointment_date} at {appt.appointment_time.strftime('%H:%M')} is now {appt.status}.",
    )
    _audit(request, "CREATE", "Appointment", appt.id, "Appointment scheduled through API")
    result = appointment_to_dict(appt)
    if current_patient and not _require_staff(request):
        result.pop("notes", None)
    return JsonResponse({"ok": True, "appointment": result}, status=201)


@require_http_methods(["GET", "PUT", "DELETE"])
def api_appointment_detail(request, appointment_id):
    appt = Appointment.objects.select_related("patient").filter(pk=appointment_id).first()
    if not appt:
        return _error("Appointment not found.", status=404)
    current_patient = _patient_for_request(request)
    own = current_patient and current_patient.id == appt.patient_id
    if not (_require_staff(request) or own):
        return _error("You do not have permission to access this appointment.", status=403)
    if request.method == "GET":
        result = appointment_to_dict(appt)
        if own and not _require_staff(request):
            result.pop("notes", None)
        return JsonResponse({"ok": True, "appointment": result})
    if request.method == "DELETE":
        if not own and not _require_admin(request):
            return _error("Only the patient or an administrator can cancel this appointment.", status=403)
        try:
            appt, changed = cancel_appointment_workflow(appt)
        except ValidationError as error:
            return _error(" ".join(error.messages), status=409)
        if changed:
            send_sms_notification(appt.patient, "Appointment cancelled", f"Your appointment on {appt.appointment_date} was cancelled.")
            _audit(request, "UPDATE", "Appointment", appt.id, "Appointment cancelled")
        result = appointment_to_dict(appt)
        if own and not _require_staff(request):
            result.pop("notes", None)
        return JsonResponse({"ok": True, "appointment": result})

    data = _json_body(request)
    if data is None:
        return _error("Invalid JSON body.")
    patient_edit = bool(own and not _require_staff(request))
    if not patient_edit and not _require_admin(request):
        return _error("Only administrators can update appointment assignments.", status=403)
    if patient_edit:
        values = {field: data.get(field, getattr(appt, field)) for field in ["appointment_date", "appointment_time", "department", "consultation_type", "reason"]}
        form = AppointmentForm(values, instance=appt)
    else:
        values = {field: data.get(field, getattr(appt, field)) for field in StaffAppointmentForm.Meta.fields}
        values["patient"] = appt.patient_id
        values["specialist"] = data.get("specialist_id", appt.specialist_id or "")
        form = StaffAppointmentForm(values, instance=appt)
    if not form.is_valid():
        return _form_error(form)
    try:
        appt = update_appointment(appt, form.cleaned_data, is_patient=patient_edit)
    except ValidationError as error:
        return _error(" ".join(error.messages), status=409)
    send_sms_notification(
        appt.patient, "Appointment updated",
        f"Your appointment is now {appt.status} for {appt.appointment_date} at {appt.appointment_time.strftime('%H:%M')}.",
    )
    _audit(request, "UPDATE", "Appointment", appt.id, "Appointment updated")
    result = appointment_to_dict(appt)
    if patient_edit:
        result.pop("notes", None)
    return JsonResponse({"ok": True, "appointment": result})


def _triage_recommendation(data):
    symptoms = [str(s).strip() for s in data.get("symptoms", []) if str(s).strip()]
    lowered = " ".join(symptoms).lower()
    chest_pain = bool(data.get("chest_pain")) or "chest pain" in lowered
    breathing = bool(data.get("difficulty_breathing")) or any(x in lowered for x in ["difficulty breathing", "shortness of breath", "dyspnea"])
    severe_bleeding = bool(data.get("severe_bleeding")) or "severe bleeding" in lowered
    unconscious = bool(data.get("unconscious")) or "unconscious" in lowered
    try:
        fever = float(data.get("temperature") or 0)
    except (TypeError, ValueError):
        fever = 0

    if unconscious or severe_bleeding or (chest_pain and breathing):
        return "High", "Emergency", Decimal("94.00"), "Seek immediate emergency assessment at the Barangay Health Center or nearest emergency facility."
    if chest_pain or breathing or fever >= 39.0:
        return "High", "General Medicine", Decimal("87.00"), "Urgent same-day clinical assessment is recommended."
    if fever >= 38.0 or any(x in lowered for x in ["persistent vomiting", "severe headache", "dizziness"]):
        return "Medium", "General Medicine", Decimal("79.00"), "Schedule a consultation soon and monitor symptoms closely."
    if any(x in lowered for x in ["rash", "skin", "itch"]):
        return "Low", "Dermatology", Decimal("76.00"), "A routine consultation is appropriate unless symptoms worsen."
    return "Low", "General Medicine", Decimal("72.00"), "A routine online consultation is appropriate. Seek urgent care if severe symptoms develop."


@require_http_methods(["GET", "POST"])
def api_triage(request):
    qs = TriageCase.objects.select_related("patient")
    current_patient = _patient_for_request(request)
    if request.method == "GET":
        if current_patient:
            qs = qs.filter(patient=current_patient)
        elif not _require_staff(request):
            return _error("Authentication required.", status=401)
        q = request.GET.get("q", "").strip()
        priority = request.GET.get("priority", "").strip()
        specialty = request.GET.get("specialty", "").strip()
        if q:
            qs = qs.filter(Q(patient__name__icontains=q) | Q(patient__patient_code__icontains=q) | Q(predicted_specialty__icontains=q))
        if priority:
            qs = qs.filter(priority=priority)
        if specialty:
            qs = qs.filter(predicted_specialty__icontains=specialty)
        return JsonResponse({"ok": True, "count": qs.count(), "results": [triage_to_dict(t) for t in qs[:200]]})

    if not _require_auth(request):
        return _error("Authentication required for symptom assessment.", status=401)
    data = _json_body(request)
    if data is None:
        return _error("Invalid JSON body.")
    patient = current_patient
    if _require_staff(request) and data.get("patient_id"):
        patient = _object_for_id(Patient, data["patient_id"])
    if not patient:
        return _error("A patient profile is required.", status=400)
    symptoms = data.get("symptoms", [])
    if not isinstance(symptoms, list) or not all(isinstance(value, str) for value in symptoms):
        return _error("Symptoms must be a list of text values.")
    form = SymptomAssessmentForm({
        "symptoms": ", ".join(symptoms), "temperature": data.get("temperature", ""),
        "chest_pain": data.get("chest_pain", False), "difficulty_breathing": data.get("difficulty_breathing", False),
        "severe_bleeding": data.get("severe_bleeding", False), "unconscious": data.get("unconscious", False),
    })
    if not form.is_valid():
        return _form_error(form)
    assessment = form.cleaned_data
    assessment["symptoms"] = [value.strip() for value in assessment["symptoms"].split(",") if value.strip()]
    priority, specialty, confidence, recommendation = _triage_recommendation(assessment)
    with transaction.atomic():
        triage = TriageCase.objects.create(
            patient=patient, symptoms=assessment["symptoms"], questionnaire={key: str(value) for key, value in assessment.items()},
            predicted_specialty=specialty, confidence=confidence, priority=priority,
            recommendation=recommendation, created_at=timezone.now(),
        )
        Notification.objects.create(
            patient=patient, channel="System", title="Symptom assessment completed",
            message=f"Preliminary triage: {priority} priority. {recommendation}", status="Sent", sent_at=timezone.now(),
        )
    _audit(request, "CREATE", "TriageCase", triage.id, "Symptom assessment submitted")
    return JsonResponse({"ok": True, "triage": triage_to_dict(triage), "disclaimer": "This is a preliminary triage recommendation and is not a medical diagnosis."}, status=201)


@require_http_methods(["GET", "POST"])
def api_consultations(request):
    qs = ConsultationRecord.objects.select_related("appointment", "appointment__patient")
    patient = _patient_for_request(request)
    if request.method == "GET":
        mine = request.GET.get("mine") == "1"
        if mine:
            if not patient:
                return _error("Patient authentication required.", status=401)
            qs = qs.filter(appointment__patient=patient)
        elif not _require_role(request, "Specialist", "Physician", "Nurse", "Midwife"):
            return _error("Clinical staff authentication required.", status=403)
        elif not request.user.is_superuser:
            qs = qs.filter(appointment__specialist=_staff_for_request(request))
        q = request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(Q(appointment__patient__name__icontains=q) | Q(diagnosis__icontains=q) | Q(treatment__icontains=q))
        results = [consultation_to_dict(c) for c in qs[:200]]
        if patient:
            for item in results:
                item.pop("clinical_notes", None)
        return JsonResponse({"ok": True, "count": qs.count(), "results": results})

    if not _require_role(request, "Specialist", "Physician", "Nurse", "Midwife"):
        return _error("Clinical staff authentication required.", status=403)
    data = _json_body(request)
    appt = _object_for_id(Appointment, data.get("appointment_id")) if data else None
    if not appt:
        return _error("A valid appointment is required.")
    current_staff = _staff_for_request(request)
    if not request.user.is_superuser and appt.specialist_id != getattr(current_staff, "pk", None):
        return _error("This appointment is assigned to another specialist.", status=403)
    record = ConsultationRecord.objects.filter(appointment=appt).first()
    was_existing = record is not None
    values = {
        "appointment": appt.pk, "diagnosis": data.get("diagnosis", ""), "treatment": data.get("treatment", ""),
        "prescription": data.get("prescription", ""), "clinical_notes": data.get("clinical_notes", ""),
        "follow_up_date": data.get("follow_up_date", ""),
    }
    form = ConsultationForm(values, instance=record)
    if not form.is_valid():
        return _form_error(form)
    try:
        record = save_consultation(record=record, appointment=appt, cleaned_data=form.cleaned_data)
    except ValidationError as error:
        return _error(" ".join(error.messages), status=409)
    _audit(request, "UPDATE" if was_existing else "CREATE", "ConsultationRecord", record.id, "Consultation record saved")
    return JsonResponse({"ok": True, "consultation": consultation_to_dict(record)}, status=201)


@require_http_methods(["GET", "POST"])
def api_notifications(request):
    patient = _patient_for_request(request)
    if request.method == "GET":
        if not patient:
            return _error("Patient authentication required.", status=401)
        qs = Notification.objects.filter(patient=patient)
        unread = request.GET.get("unread")
        if unread == "1":
            qs = qs.filter(is_read=False)
        return JsonResponse({"ok": True, "count": qs.count(), "results": [notification_to_dict(n) for n in qs[:100]]})
    if not patient:
        return _error("Patient authentication required.", status=401)
    data = _json_body(request)
    notification = _object_for_id(Notification, data.get("id"), patient=patient) if data else None
    if not notification:
        return _error("Notification not found.", status=404)
    is_read = data.get("is_read", True)
    if not isinstance(is_read, bool):
        return _error("is_read must be true or false.")
    notification.is_read = is_read
    notification.save(update_fields=["is_read"])
    return JsonResponse({"ok": True, "notification": notification_to_dict(notification)})


@require_http_methods(["GET"])
def api_followups(request):
    patient = _patient_for_request(request)
    if not patient:
        return _error("Patient authentication required.", status=401)
    qs = FollowUpReminder.objects.filter(patient=patient)
    status = request.GET.get("status", "").strip()
    if status:
        qs = qs.filter(status=status)
    return JsonResponse({"ok": True, "count": qs.count(), "results": [reminder_to_dict(r) for r in qs[:100]]})


@require_http_methods(["GET", "POST"])
def api_users(request):
    if not _require_admin(request):
        return _error("Administrator authentication required.", status=403 if request.user.is_authenticated else 401)
    if request.method == "GET":
        qs = User.objects.all().order_by("-date_joined")
        q = request.GET.get("q", "").strip()
        role = request.GET.get("role", "").strip()
        status = request.GET.get("status", "").strip()
        if q:
            qs = qs.filter(Q(username__icontains=q) | Q(email__icontains=q) | Q(first_name__icontains=q) | Q(last_name__icontains=q))
        if role == "Patient":
            qs = qs.filter(patient_profile__isnull=False)
        elif role:
            qs = qs.filter(staff_profile__role=role)
        if status == "Active":
            qs = qs.filter(is_active=True)
        elif status == "Inactive":
            qs = qs.filter(is_active=False)
        results = []
        for u in qs[:200]:
            staff = StaffProfile.objects.filter(user=u).first()
            patient = Patient.objects.filter(user=u).first()
            results.append({
                "id": u.id, "username": u.username, "name": u.get_full_name() or (patient.name if patient else u.username),
                "email": u.email, "role": "Patient" if patient else (staff.role if staff else ("Admin" if u.is_superuser else "User")),
                "status": "Active" if u.is_active else "Inactive",
                "last_login": u.last_login.isoformat() if u.last_login else None,
            })
        return JsonResponse({"ok": True, "count": qs.count(), "results": results})

    data = _json_body(request)
    if not data:
        return _error("Invalid JSON body.")
    form = StaffUserForm({
        "email": data.get("email", ""), "password": data.get("password", ""),
        "first_name": data.get("first_name", ""), "last_name": data.get("last_name", ""),
        "role": data.get("role", "Specialist"), "employee_id": data.get("employee_id", ""),
        "specialty": data.get("specialty", "General Medicine"),
        "is_active": data.get("is_active", True),
    })
    if not form.is_valid():
        return _form_error(form)
    values = form.cleaned_data
    with transaction.atomic():
        user = User.objects.create_user(
            username=values["email"], email=values["email"], password=values["password"],
            first_name=values["first_name"], last_name=values["last_name"], is_staff=True, is_active=values["is_active"],
        )
        profile = StaffProfile.objects.create(user=user, role=values["role"], employee_id=values["employee_id"], specialty=values["specialty"], status="Active" if values["is_active"] else "Inactive")
    _audit(request, "CREATE", "StaffProfile", profile.id, "Staff user created")
    return JsonResponse({"ok": True, "user": {"id": user.id, "email": user.email, "role": profile.role}}, status=201)


@require_http_methods(["GET"])
def api_inventory(request):
    if not _require_staff(request):
        return _error("Staff authentication required.", status=403 if request.user.is_authenticated else 401)
    qs = InventoryItem.objects.all().order_by("name")
    q = request.GET.get("q", "").strip()
    category = request.GET.get("category", "").strip()
    stock = request.GET.get("stock", "").strip()
    if q:
        qs = qs.filter(name__icontains=q)
    if category:
        qs = qs.filter(category=category)
    items = list(qs)
    if stock == "low":
        items = [i for i in items if i.quantity_on_hand <= i.reorder_level]
    return JsonResponse({"ok": True, "count": len(items), "results": [{
        "id": i.id, "name": i.name, "category": i.category, "unit": i.unit,
        "quantity_on_hand": i.quantity_on_hand, "reorder_level": i.reorder_level,
        "last_restocked": i.last_restocked.isoformat(), "low_stock": i.quantity_on_hand <= i.reorder_level,
    } for i in items]})


@require_http_methods(["GET"])
def api_sms_campaigns(request):
    if not _require_staff(request):
        return _error("Staff authentication required.", status=403 if request.user.is_authenticated else 401)
    qs = SMSCampaign.objects.all().order_by("-scheduled_date", "-id")
    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(message__icontains=q) | Q(target_group__icontains=q))
    if status:
        qs = qs.filter(status=status)
    return JsonResponse({"ok": True, "count": qs.count(), "results": [{
        "id": x.id, "title": x.title, "message": x.message, "target_group": x.target_group,
        "recipient_count": x.recipient_count, "sent_count": x.sent_count, "status": x.status,
        "scheduled_date": x.scheduled_date.isoformat() if x.scheduled_date else None,
    } for x in qs[:200]]})


@require_http_methods(["GET"])
def api_dashboard(request):
    if not _require_staff(request):
        return _error("Staff authentication required.", status=403 if request.user.is_authenticated else 401)
    today = timezone.localdate()
    appointments_today = Appointment.objects.filter(appointment_date=today)
    upcoming = Appointment.objects.select_related("patient").filter(appointment_date__gte=today).order_by("appointment_date", "appointment_time")[:8]
    recent_triage = TriageCase.objects.select_related("patient")[:5]
    return JsonResponse({
        "ok": True,
        "stats": {
            "patients": Patient.objects.count(),
            "active_patients": Patient.objects.filter(status="Active").count(),
            "appointments_today": appointments_today.count(),
            "pending_appointments": Appointment.objects.filter(status="Pending").count(),
            "triage_cases": TriageCase.objects.count(),
            "high_priority_triage": TriageCase.objects.filter(priority="High").count(),
            "sms_campaigns": SMSCampaign.objects.count(),
            "low_stock_items": sum(1 for i in InventoryItem.objects.all() if i.quantity_on_hand <= i.reorder_level),
        },
        "upcoming_appointments": [appointment_to_dict(a) for a in upcoming],
        "recent_triage": [triage_to_dict(t) for t in recent_triage],
    })
