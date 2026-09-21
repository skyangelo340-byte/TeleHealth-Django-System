from functools import wraps
import logging

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .forms import (
    AppointmentAssignmentForm,
    AppointmentForm,
    ConsultationForm,
    InventoryForm,
    PatientForm,
    PatientProfileForm,
    PatientRegistrationForm,
    SMSCampaignForm,
    StaffAppointmentForm,
    StaffUserForm,
    SymptomAssessmentForm,
)
from .models import (
    Appointment,
    AuditLog,
    ConsultationRecord,
    FollowUpReminder,
    InventoryItem,
    Notification,
    Patient,
    SMSCampaign,
    StaffProfile,
    StaffNotification,
    TriageCase,
)
from .services import send_sms_notification, send_system_notification
from .views import _triage_recommendation
from .workflows import (
    cancel_appointment_workflow,
    acknowledge_appointment,
    confirm_appointment,
    create_appointment,
    generate_patient_code,
    save_consultation,
    save_patient_profile,
    set_patient_active,
    update_appointment,
)


logger = logging.getLogger(__name__)


def _dashboard_name(user):
    if user.is_superuser:
        return "admin_dashboard"
    if Patient.objects.filter(user=user).exists():
        return "patient_dashboard"
    profile = StaffProfile.objects.filter(user=user, status="Active").first()
    if profile and profile.role == "Admin":
        return "admin_dashboard"
    if profile:
        return "specialist_dashboard"
    return "login"


def _patient(request):
    if not request.user.is_authenticated:
        return None
    return Patient.objects.filter(user=request.user).first()


def _staff(request):
    if not request.user.is_authenticated:
        return None
    return StaffProfile.objects.filter(user=request.user, status="Active").first()


def staff_required(view):
    @wraps(view)
    @login_required(login_url="login")
    def wrapped(request, *args, **kwargs):
        if not (request.user.is_superuser or _staff(request)):
            return HttpResponseForbidden("Staff access is required.")
        return view(request, *args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    @staff_required
    def wrapped(request, *args, **kwargs):
        profile = _staff(request)
        if not (request.user.is_superuser or (profile and profile.role == "Admin")):
            return HttpResponseForbidden("Administrator access is required.")
        return view(request, *args, **kwargs)
    return wrapped


def roles_required(*roles):
    def decorator(view):
        @wraps(view)
        @login_required(login_url="login")
        def wrapped(request, *args, **kwargs):
            profile = _staff(request)
            if not (request.user.is_superuser or (profile and profile.role in roles)):
                raise PermissionDenied("Your staff role does not have access to this workflow.")
            return view(request, *args, **kwargs)
        return wrapped
    return decorator


def _active_patient(request):
    patient = _patient(request)
    if not patient:
        raise PermissionDenied("This account does not have a patient profile.")
    if patient.status != "Active" or not request.user.is_active:
        raise PermissionDenied("This patient account is inactive. Contact the health center for assistance.")
    return patient


def _audit(request, action, model, object_id="", details=""):
    AuditLog.objects.create(
        user=request.user.username if request.user.is_authenticated else "anonymous",
        action=action,
        target_model=model,
        target_id=str(object_id),
        ip_address=request.META.get("REMOTE_ADDR") or "127.0.0.1",
        details=details,
        timestamp=timezone.now(),
    )


def login_view(request):
    if request.user.is_authenticated:
        return redirect(_dashboard_name(request.user))

    if request.method == "POST":
        identifier = request.POST.get("email", "").strip().lower()
        password = request.POST.get("password", "")

        candidate = User.objects.filter(
            Q(username__iexact=identifier) | Q(email__iexact=identifier)
        ).first()

        user = authenticate(
            request,
            username=candidate.username if candidate else identifier,
            password=password,
        )

        if user:
            login(request, user)
            _audit(request, "LOGIN", "User", user.pk, "Successful web login")

            next_url = request.POST.get("next", "")
            if not url_has_allowed_host_and_scheme(
                next_url,
                allowed_hosts={request.get_host()},
                require_https=request.is_secure(),
            ):
                next_url = ""

            return redirect(next_url or _dashboard_name(user))

        _audit(
            request,
            "LOGIN_FAILED",
            "User",
            details=f"Failed web login for {identifier or 'unknown'}",
        )
        messages.error(request, "Invalid email or password.")

    return render(
        request,
        "auth/login.html",
        {
            "public_page": True,
            "identifier": request.POST.get("email", ""),
        },
    )


def register_view(request):
    if request.user.is_authenticated:
        return redirect(_dashboard_name(request.user))
    form = PatientRegistrationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            with transaction.atomic():
                user = User.objects.create_user(
                    username=data["email"], email=data["email"], password=data["password"],
                    first_name=data["first_name"], last_name=data["last_name"],
                )
                patient = Patient.objects.create(
                    user=user, patient_code=generate_patient_code(),
                    name=f'{data["last_name"]}, {data["first_name"]}',
                    date_of_birth=data["date_of_birth"], sex=data["sex"],
                    contact_number=data["contact_number"], barangay=data["barangay"],
                    email=data["email"], philhealth_number=data["philhealth_number"], address=data["address"],
                    emergency_contact=data["emergency_contact"],
                )
        except IntegrityError as error:
            conflict = str(error).lower()
            if "philhealth" in conflict:
                form.add_error("philhealth_number", "This PhilHealth number is already linked to a patient record.")
            elif "username" in conflict or "email" in conflict:
                form.add_error("email", "An account with this email already exists.")
            else:
                logger.exception("Unexpected patient registration integrity conflict")
                form.add_error(None, "A temporary data conflict prevented registration. Please submit the form again.")
            messages.error(request, "Your account was not created. Please review the highlighted information.")
            return render(request, "auth/register.html", {"form": form, "public_page": True}, status=400)
        AuditLog.objects.create(
            user=user.username,
            action="CREATE",
            target_model="PatientAccount",
            target_id=str(patient.pk),
            ip_address=request.META.get("REMOTE_ADDR") or "127.0.0.1",
            details="Patient self-registration",
            timestamp=timezone.now(),
        )
        messages.success(request, "Your patient account was created successfully. Sign in to continue.")
        return redirect("login")
    if request.method == "POST":
        messages.error(request, "Your account was not created. Please correct the highlighted fields.")
        return render(request, "auth/register.html", {"form": form, "public_page": True}, status=400)
    return render(request, "auth/register.html", {"form": form, "public_page": True})


@require_POST
def logout_view(request):
    if request.user.is_authenticated:
        _audit(request, "UPDATE", "UserSession", request.user.pk, "Secure logout")
    logout(request)
    messages.success(request, "You have been securely logged out.")
    return redirect("login")


@login_required(login_url="login")
def dashboard(request):
    return redirect(_dashboard_name(request.user))


@admin_required
def admin_dashboard(request):
    today = timezone.localdate()
    upcoming = Appointment.objects.select_related("patient").filter(
        appointment_date__gte=today, status__in=["Pending", "Approved", "Scheduled", "Rescheduled"],
    ).order_by("appointment_date", "appointment_time")[:8]
    context = {
        "stats": {
            "patients": Patient.objects.count(),
            "active_patients": Patient.objects.filter(status="Active").count(),
            "appointments_today": Appointment.objects.filter(appointment_date=today).count(),
            "pending": Appointment.objects.filter(status="Pending").count(),
            "triage": TriageCase.objects.count(),
            "high_triage": TriageCase.objects.filter(priority="High").count(),
            "campaigns": SMSCampaign.objects.count(),
            "low_stock": sum(i.quantity_on_hand <= i.reorder_level for i in InventoryItem.objects.all()),
        },
        "appointments": upcoming,
        "triage_cases": TriageCase.objects.select_related("patient")[:6],
        "pending_requests": Appointment.objects.select_related("patient").filter(status="Pending").order_by("created_at")[:8],
    }
    return render(request, "dashboard.html", context)


@staff_required
def specialist_dashboard(request):
    profile = _staff(request)
    if request.user.is_superuser or (profile and profile.role == "Admin"):
        return redirect("admin_dashboard")
    today = timezone.localdate()
    appointments = Appointment.objects.select_related("patient").filter(
        specialist=profile,
        appointment_date__gte=today,
        status__in=["Approved", "Scheduled", "Rescheduled"],
    ).order_by("appointment_date", "appointment_time")
    notification_queryset = StaffNotification.objects.filter(recipient=profile).select_related("appointment", "appointment__patient")
    unread_count = notification_queryset.filter(is_read=False).count()
    notifications = notification_queryset[:20]
    return render(request, "specialist/dashboard.html", {
        "profile": profile,
        "appointments": appointments,
        "appointments_today": appointments.filter(appointment_date=today).count(),
        "upcoming_count": appointments.count(),
        "notifications": notifications,
        "unread_count": unread_count,
        "today": today,
    })


@login_required(login_url="login")
def patient_dashboard(request):
    return patient_portal(request, tab="overview")


PORTAL_TABS = {"overview", "profile", "schedule", "triage", "appointments", "history", "notifications"}


def _portal_context(request, patient, *, active_tab="overview", profile_form=None, appointment_form=None, triage_form=None):
    status = request.GET.get("status", "").strip()
    search = request.GET.get("q", "").strip()
    appointments = Appointment.objects.filter(patient=patient)
    if status:
        appointments = appointments.filter(status=status)
    if search:
        appointments = appointments.filter(Q(department__icontains=search) | Q(provider_name__icontains=search) | Q(reason__icontains=search))
    reminders = patient.follow_up_reminders.all()
    return {
        "patient": patient,
        "profile_form": profile_form or PatientProfileForm(instance=patient),
        "appointment_form": appointment_form or AppointmentForm(),
        "triage_form": triage_form or SymptomAssessmentForm(),
        "appointments": appointments,
        "all_appointment_count": patient.appointments.count(),
        "next_appointment": patient.appointments.filter(
            appointment_date__gte=timezone.localdate(), status__in=["Pending", "Approved", "Scheduled", "Rescheduled"],
        ).order_by("appointment_date", "appointment_time").first(),
        "triage_cases": patient.triage_cases.all()[:10],
        "consultations": ConsultationRecord.objects.filter(appointment__patient=patient).select_related("appointment"),
        "reminders": reminders,
        "pending_reminder_count": reminders.filter(status__in=["Pending", "Failed"]).count(),
        "notifications": patient.notifications.all()[:30],
        "active_tab": active_tab if active_tab in PORTAL_TABS else "overview",
        "status_filter": status,
        "search": search,
    }


@login_required(login_url="login")
def patient_portal(request, tab=None):
    patient = _patient(request)
    if not patient:
        if _staff(request) or request.user.is_superuser:
            return redirect("dashboard")
        return HttpResponseForbidden("This account does not have a patient profile.")
    if patient.status != "Active":
        return render(request, "portal/inactive.html", {"patient": patient})
    active_tab = tab or request.GET.get("tab", "overview")
    return render(request, "portal/index.html", _portal_context(request, patient, active_tab=active_tab))


@login_required(login_url="login")
@require_POST
def update_profile(request):
    patient = _active_patient(request)
    form = PatientProfileForm(request.POST, instance=patient)
    if form.is_valid():
        patient = save_patient_profile(form)
        _audit(request, "UPDATE", "Patient", patient.pk, "Patient updated personal profile")
        messages.success(request, "Profile updated successfully.")
    else:
        messages.error(request, "Profile was not updated. Please correct the highlighted fields.")
        return render(request, "portal/index.html", _portal_context(request, patient, active_tab="profile", profile_form=form), status=400)
    return redirect("patient_portal_tab", tab="profile")


@login_required(login_url="login")
@require_POST
def schedule_appointment(request):
    patient = _active_patient(request)
    form = AppointmentForm(request.POST)
    if form.is_valid():
        try:
            appointment = create_appointment(patient=patient, status="Pending", **form.cleaned_data)
        except ValidationError as error:
            form.add_error(None, error)
            messages.error(request, "Unable to schedule the consultation. Please correct the highlighted details.")
            return render(request, "portal/index.html", _portal_context(request, patient, active_tab="schedule", appointment_form=form), status=400)
        send_sms_notification(patient, "Consultation request received", f"Your consultation request for {appointment.appointment_date} is pending review.")
        _audit(request, "CREATE", "Appointment", appointment.pk, "Patient scheduled consultation")
        messages.success(request, "Consultation request submitted. A notification record was created.")
    else:
        messages.error(request, "Unable to schedule the consultation. Please correct the highlighted details.")
        return render(request, "portal/index.html", _portal_context(request, patient, active_tab="schedule", appointment_form=form), status=400)
    return redirect("patient_portal_tab", tab="schedule")


@login_required(login_url="login")
@require_POST
def cancel_appointment(request, pk):
    patient = _active_patient(request)
    appointment = get_object_or_404(Appointment, pk=pk, patient=patient)
    try:
        appointment, changed = cancel_appointment_workflow(appointment)
        if changed:
            send_sms_notification(patient, "Appointment cancelled", f"Your appointment on {appointment.appointment_date} was cancelled.")
            _audit(request, "UPDATE", "Appointment", appointment.pk, "Patient cancelled appointment")
            messages.success(request, "Appointment cancelled.")
        else:
            messages.info(request, "This appointment was already cancelled.")
    except ValidationError as error:
        messages.error(request, error.message)
    return redirect("patient_portal_tab", tab="appointments")


@login_required(login_url="login")
@require_POST
def submit_assessment(request):
    patient = _active_patient(request)
    form = SymptomAssessmentForm(request.POST)
    if form.is_valid():
        data = form.cleaned_data
        data["symptoms"] = [value.strip() for value in data["symptoms"].split(",") if value.strip()]
        priority, specialty, confidence, recommendation = _triage_recommendation(data)
        with transaction.atomic():
            case = TriageCase.objects.create(
                patient=patient, symptoms=data["symptoms"], questionnaire={k: str(v) for k, v in data.items()},
                predicted_specialty=specialty, confidence=confidence, priority=priority,
                recommendation=recommendation, created_at=timezone.now(),
            )
            Notification.objects.create(
                patient=patient, channel="System", title="Symptom assessment completed",
                message=f"Preliminary triage: {priority} priority. {recommendation}", status="Sent", sent_at=timezone.now(),
            )
        _audit(request, "CREATE", "TriageCase", case.pk, "Patient submitted symptom assessment")
        messages.success(request, f"{priority} priority: {recommendation} This is not a medical diagnosis.")
    else:
        messages.error(request, "Please correct the symptom questionnaire.")
        return render(request, "portal/index.html", _portal_context(request, patient, active_tab="triage", triage_form=form), status=400)
    return redirect("patient_portal_tab", tab="triage")


@login_required(login_url="login")
@require_POST
def read_notification(request, pk):
    patient = _active_patient(request)
    notification = get_object_or_404(Notification, pk=pk, patient=patient)
    notification.is_read = True
    notification.save(update_fields=["is_read"])
    return redirect("patient_portal_tab", tab="notifications")


@staff_required
def patient_list(request):
    patients = Patient.objects.all()
    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    sex = request.GET.get("sex", "").strip()
    if query:
        patients = patients.filter(Q(name__icontains=query) | Q(patient_code__icontains=query) | Q(contact_number__icontains=query))
    if status:
        patients = patients.filter(status=status)
    if sex:
        patients = patients.filter(sex=sex)
    return render(request, "patients/list.html", {"patients": patients, "q": query, "status_filter": status, "sex_filter": sex})


@staff_required
def patient_detail(request, pk):
    patient = get_object_or_404(Patient, pk=pk)
    context = {
        "patient": patient,
        "appointments": patient.appointments.all()[:8],
        "triage_cases": patient.triage_cases.all()[:5],
        "consultations": ConsultationRecord.objects.filter(appointment__patient=patient).select_related("appointment")[:5],
        "can_view_clinical": request.user.is_superuser or bool(
            _staff(request) and _staff(request).role in {"Specialist", "Physician", "Nurse", "Midwife"}
        ),
    }
    return render(request, "patients/detail.html", context)


@roles_required("Admin", "BHW", "Nurse", "Midwife")
def patient_edit(request, pk=None):
    patient = get_object_or_404(Patient, pk=pk) if pk else None
    initial = {} if patient else {"patient_code": generate_patient_code()}
    form = PatientForm(request.POST or None, instance=patient, initial=initial)
    if request.method == "POST" and form.is_valid():
        saved = save_patient_profile(form)
        _audit(request, "UPDATE" if patient else "CREATE", "Patient", saved.pk, "Patient saved through staff interface")
        messages.success(request, "Patient record saved.")
        return redirect("patients")
    return render(request, "shared/form_page.html", {"form": form, "title": "Edit Patient" if patient else "Register Patient", "module": "PATIENT RECORD", "cancel_url": "patients"})


@admin_required
@require_POST
def patient_archive(request, pk):
    patient = get_object_or_404(Patient, pk=pk)
    patient, changed, cancelled = set_patient_active(patient, active=False)
    _audit(request, "UPDATE", "Patient", patient.pk, "Patient archived")
    if changed:
        messages.success(request, f"Patient archived and {cancelled} future appointment(s) cancelled. Medical history was retained.")
    else:
        messages.info(request, "This patient was already archived.")
    return redirect("patients")


@admin_required
@require_POST
def patient_reactivate(request, pk):
    patient = get_object_or_404(Patient, pk=pk)
    patient, changed, _ = set_patient_active(patient, active=True)
    _audit(request, "UPDATE", "Patient", patient.pk, "Patient reactivated")
    messages.success(request, "Patient and portal access reactivated." if changed else "This patient is already active.")
    return redirect("patients")


@staff_required
def appointment_list(request):
    appointments = Appointment.objects.select_related("patient")
    profile = _staff(request)
    if not request.user.is_superuser and profile and profile.role != "Admin":
        appointments = appointments.filter(specialist=profile)
    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    kind = request.GET.get("type", "").strip()
    if query:
        appointments = appointments.filter(Q(patient__name__icontains=query) | Q(provider_name__icontains=query) | Q(department__icontains=query))
    if status:
        appointments = appointments.filter(status=status)
    if kind:
        appointments = appointments.filter(consultation_type=kind)
    return render(request, "appointments/list.html", {"appointments": appointments, "q": query, "status_filter": status, "type_filter": kind})


@admin_required
def appointment_edit(request, pk=None):
    appointment = get_object_or_404(Appointment, pk=pk) if pk else None
    form = StaffAppointmentForm(request.POST or None, instance=appointment)
    if request.method == "POST" and form.is_valid():
        try:
            if appointment:
                saved = update_appointment(appointment, form.cleaned_data)
            else:
                saved = create_appointment(**form.cleaned_data)
        except ValidationError as error:
            form.add_error(None, error)
            return render(request, "shared/form_page.html", {"form": form, "title": "Edit Appointment" if appointment else "Create Appointment", "module": "CARE SCHEDULE", "cancel_url": "appointments"}, status=400)
        send_sms_notification(saved.patient, "Appointment updated", f"Your appointment is {saved.status} for {saved.appointment_date} at {saved.appointment_time:%H:%M}.")
        _audit(request, "UPDATE" if appointment else "CREATE", "Appointment", saved.pk, "Staff saved appointment")
        messages.success(request, "Appointment saved and the patient notification was recorded.")
        return redirect("appointments")
    return render(request, "shared/form_page.html", {"form": form, "title": "Edit Appointment" if appointment else "Create Appointment", "module": "CARE SCHEDULE", "cancel_url": "appointments"})


@admin_required
def appointment_confirm(request, pk):
    appointment = get_object_or_404(Appointment.objects.select_related("patient"), pk=pk)
    if appointment.status in {"Completed", "Cancelled", "No-show"}:
        messages.error(request, "A closed appointment cannot be confirmed.")
        return redirect("appointments")
    initial = {
        "specialist": appointment.specialist_id,
        "appointment_date": appointment.appointment_date,
        "appointment_time": appointment.appointment_time,
    }
    form = AppointmentAssignmentForm(request.POST or None, appointment=appointment, initial=initial)
    if request.method == "POST" and form.is_valid():
        try:
            appointment = confirm_appointment(appointment, **form.cleaned_data)
        except ValidationError as error:
            form.add_error(None, error)
        else:
            schedule = f"{appointment.appointment_date} at {appointment.appointment_time:%H:%M}"
            send_system_notification(
                appointment.patient,
                "Appointment confirmed",
                f"{appointment.provider_name} is available and your appointment is confirmed for {schedule}.",
            )
            send_sms_notification(
                appointment.patient,
                "Appointment confirmed",
                f"Your RHU appointment with {appointment.provider_name} is confirmed for {schedule}.",
            )
            _audit(request, "UPDATE", "Appointment", appointment.pk, "Admin confirmed specialist and schedule")
            messages.success(request, "Schedule confirmed. The patient and specialist were notified.")
            return redirect("admin_dashboard")
    return render(request, "shared/form_page.html", {
        "form": form,
        "title": f"Confirm appointment for {appointment.patient.name}",
        "module": "SCHEDULE MATCHING",
        "cancel_url": "admin_dashboard",
    }, status=400 if request.method == "POST" else 200)


@staff_required
@require_POST
def specialist_acknowledge(request, pk):
    profile = _staff(request)
    if not profile or profile.role == "Admin":
        raise PermissionDenied("A specialist account is required.")
    appointment = get_object_or_404(Appointment, pk=pk)
    try:
        appointment, changed = acknowledge_appointment(appointment, specialist=profile)
    except ValidationError as error:
        messages.error(request, error.message)
    else:
        if changed:
            send_system_notification(
                appointment.patient,
                "Specialist confirmed your appointment",
                f"{appointment.provider_name} acknowledged your appointment on {appointment.appointment_date} at {appointment.appointment_time:%H:%M}.",
            )
            StaffNotification.objects.filter(recipient=profile, appointment=appointment).update(is_read=True)
            _audit(request, "UPDATE", "Appointment", appointment.pk, "Specialist acknowledged schedule")
            messages.success(request, "Appointment acknowledged and the patient was notified.")
        else:
            messages.info(request, "You already acknowledged this appointment.")
    return redirect("specialist_dashboard")


@staff_required
@require_POST
def read_staff_notification(request, pk):
    profile = _staff(request)
    notification = get_object_or_404(StaffNotification, pk=pk, recipient=profile)
    notification.is_read = True
    notification.save(update_fields=["is_read"])
    return redirect("specialist_dashboard")


@staff_required
def triage_list(request):
    cases = TriageCase.objects.select_related("patient")
    query = request.GET.get("q", "").strip()
    priority = request.GET.get("priority", "").strip()
    if query:
        cases = cases.filter(Q(patient__name__icontains=query) | Q(predicted_specialty__icontains=query))
    if priority:
        cases = cases.filter(priority=priority)
    return render(request, "triage/list.html", {"cases": cases, "q": query, "priority_filter": priority})


@roles_required("Specialist", "Physician", "Nurse", "Midwife")
def records(request):
    records = ConsultationRecord.objects.select_related("appointment", "appointment__patient")
    if not request.user.is_superuser:
        records = records.filter(appointment__specialist=_staff(request))
    query = request.GET.get("q", "").strip()
    if query:
        records = records.filter(Q(appointment__patient__name__icontains=query) | Q(diagnosis__icontains=query) | Q(treatment__icontains=query))
    return render(request, "records/list.html", {"records": records, "q": query})


@roles_required("Specialist", "Physician", "Nurse", "Midwife")
def consultation_edit(request, pk=None):
    record = get_object_or_404(ConsultationRecord, pk=pk) if pk else None
    if record and not request.user.is_superuser and record.appointment.specialist_id != _staff(request).pk:
        raise PermissionDenied("This consultation is assigned to another specialist.")
    initial = {}
    if not record and request.method == "GET" and request.GET.get("appointment"):
        appointment = get_object_or_404(Appointment, pk=request.GET["appointment"])
        initial["appointment"] = appointment
    specialist = None if request.user.is_superuser else _staff(request)
    form = ConsultationForm(request.POST or None, instance=record, initial=initial, specialist=specialist)
    if request.method == "POST" and form.is_valid():
        try:
            saved = save_consultation(record=record, appointment=form.cleaned_data["appointment"], cleaned_data=form.cleaned_data)
        except ValidationError as error:
            form.add_error(None, error)
            return render(request, "shared/form_page.html", {"form": form, "title": "Edit Consultation Record" if record else "New Consultation Record", "module": "CLINICAL RECORD", "cancel_url": "records"}, status=400)
        _audit(request, "UPDATE" if record else "CREATE", "ConsultationRecord", saved.pk, "Consultation record saved")
        messages.success(request, "Consultation record and follow-up details saved.")
        return redirect("records")
    return render(request, "shared/form_page.html", {"form": form, "title": "Edit Consultation Record" if record else "New Consultation Record", "module": "CLINICAL RECORD", "cancel_url": "records"})


@admin_required
def user_list(request):
    users = User.objects.select_related("staff_profile").filter(patient_profile__isnull=True).order_by("-date_joined")
    query = request.GET.get("q", "").strip()
    role = request.GET.get("role", "").strip()
    if query:
        users = users.filter(Q(first_name__icontains=query) | Q(last_name__icontains=query) | Q(email__icontains=query))
    if role:
        users = users.filter(staff_profile__role=role)
    return render(request, "users/list.html", {"users": users, "q": query, "role_filter": role})


@admin_required
def user_edit(request, pk=None):
    user = get_object_or_404(User, pk=pk, patient_profile__isnull=True) if pk else None
    profile = StaffProfile.objects.filter(user=user).first() if user else None
    initial = None
    if user:
        initial = {"first_name": user.first_name, "last_name": user.last_name, "email": user.email, "role": profile.role if profile else "Specialist", "employee_id": profile.employee_id if profile else "", "specialty": profile.specialty if profile else "General Medicine", "is_active": user.is_active}
    form = StaffUserForm(request.POST or None, initial=initial, user_instance=user)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        with transaction.atomic():
            if not user:
                user = User(username=data["email"])
            user.username = data["email"]
            user.email = data["email"]
            user.first_name = data["first_name"]
            user.last_name = data["last_name"]
            user.is_active = data["is_active"]
            user.is_staff = True
            if data["password"]:
                user.set_password(data["password"])
            user.save()
            StaffProfile.objects.update_or_create(user=user, defaults={"role": data["role"], "employee_id": data["employee_id"], "specialty": data["specialty"], "status": "Active" if data["is_active"] else "Inactive"})
        _audit(request, "UPDATE" if pk else "CREATE", "StaffProfile", user.pk, "Staff account saved")
        messages.success(request, "Staff user saved.")
        return redirect("users")
    return render(request, "shared/form_page.html", {"form": form, "title": "Edit Staff User" if user else "Create Staff User", "module": "ACCESS CONTROL", "cancel_url": "users"})


@staff_required
def communications(request):
    campaigns = SMSCampaign.objects.all().order_by("-scheduled_date", "-id")
    query = request.GET.get("q", "").strip()
    if query:
        campaigns = campaigns.filter(Q(title__icontains=query) | Q(message__icontains=query) | Q(target_group__icontains=query))
    return render(request, "communications/list.html", {"campaigns": campaigns, "q": query})


@admin_required
def campaign_edit(request, pk=None):
    campaign = get_object_or_404(SMSCampaign, pk=pk) if pk else None
    form = SMSCampaignForm(request.POST or None, instance=campaign)
    if request.method == "POST" and form.is_valid():
        saved = form.save(commit=False)
        saved.status = "Scheduled" if saved.scheduled_date else "Draft"
        saved.recipient_count = Patient.objects.filter(status="Active").count() if saved.target_group in {"All active patients", "All patients"} else 0
        saved.sent_count = 0
        saved.save()
        _audit(request, "UPDATE" if campaign else "CREATE", "SMSCampaign", saved.pk, "SMS campaign saved")
        messages.success(request, "Communication campaign saved.")
        return redirect("communications")
    return render(request, "shared/form_page.html", {"form": form, "title": "Edit Campaign" if campaign else "New Campaign", "module": "PATIENT OUTREACH", "cancel_url": "communications"})


@staff_required
def reports(request):
    status_counts = Appointment.objects.values("status").annotate(total=Count("id")).order_by("status")
    barangay_counts = Patient.objects.values("barangay").annotate(total=Count("id")).order_by("-total")[:8]
    return render(request, "reports.html", {"status_counts": status_counts, "barangay_counts": barangay_counts})


@admin_required
def administration(request):
    inventory = InventoryItem.objects.all().order_by("name")
    logs = AuditLog.objects.all()[:50]
    return render(request, "administration.html", {"inventory": inventory, "logs": logs})


@admin_required
def inventory_edit(request, pk=None):
    item = get_object_or_404(InventoryItem, pk=pk) if pk else None
    form = InventoryForm(request.POST or None, instance=item)
    if request.method == "POST" and form.is_valid():
        saved = form.save()
        _audit(request, "UPDATE" if item else "CREATE", "InventoryItem", saved.pk, "Inventory item saved")
        messages.success(request, "Inventory item saved.")
        return redirect("administration")
    return render(request, "shared/form_page.html", {"form": form, "title": "Edit Inventory Item" if item else "Add Inventory Item", "module": "INVENTORY", "cancel_url": "administration"})


@staff_required
def teleconsultation(request):
    today = timezone.localdate()
    appointments = Appointment.objects.select_related("patient").filter(
        consultation_type="Online", status__in=["Approved", "Scheduled", "Rescheduled"], appointment_date__gte=today,
    ).order_by("appointment_date", "appointment_time")
    staff = _staff(request)
    if not request.user.is_superuser and staff and staff.role != "Admin":
        appointments = appointments.filter(specialist=staff)
    can_record = request.user.is_superuser or bool(staff and staff.role in {"Specialist", "Physician", "Nurse", "Midwife"})
    return render(request, "teleconsultation.html", {"appointments": appointments, "today": today, "can_record": can_record})
