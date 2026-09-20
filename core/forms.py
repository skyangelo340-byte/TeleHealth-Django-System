from django import forms
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils import timezone

from .models import (
    Appointment,
    ConsultationRecord,
    InventoryItem,
    Patient,
    SMSCampaign,
    StaffProfile,
)
from .workflows import TERMINAL_APPOINTMENT_STATUSES, validate_appointment_transition, validate_future_slot


class StyledFormMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs.setdefault("class", "checkbox")
            else:
                field.widget.attrs.setdefault("class", "form-control")
            descriptions = []
            if field.help_text:
                descriptions.append(f"id_{name}_help")
            if descriptions:
                field.widget.attrs.setdefault("aria-describedby", " ".join(descriptions))


class PatientRegistrationForm(StyledFormMixin, forms.Form):
    first_name = forms.CharField(max_length=150)
    last_name = forms.CharField(max_length=150)
    email = forms.EmailField()
    password = forms.CharField(widget=forms.PasswordInput)
    confirm_password = forms.CharField(widget=forms.PasswordInput)
    date_of_birth = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    sex = forms.ChoiceField(choices=Patient.SEX_CHOICES)
    contact_number = forms.CharField(max_length=20)
    barangay = forms.CharField(max_length=100, initial="Lumang Bayan")
    philhealth_number = forms.CharField(max_length=30, required=False)
    address = forms.CharField(max_length=255, required=False)
    emergency_contact = forms.CharField(max_length=100, required=False)

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError("An account with this email already exists.")
        if Patient.objects.filter(email__iexact=email).exists():
            raise ValidationError("A patient record already uses this email. Contact the health center to link your existing record.")
        return email

    def clean_date_of_birth(self):
        value = self.cleaned_data["date_of_birth"]
        if value >= timezone.localdate():
            raise ValidationError("Date of birth must be in the past.")
        return value

    def clean_philhealth_number(self):
        value = self.cleaned_data.get("philhealth_number", "").strip()
        if value.lower() in {"n/a", "na", "none", "not applicable", "-"}:
            return ""
        if value and Patient.objects.filter(philhealth_number__iexact=value).exists():
            raise ValidationError("This PhilHealth number is already linked to a patient record.")
        return value

    def clean(self):
        cleaned = super().clean()
        password = cleaned.get("password")
        if password and password != cleaned.get("confirm_password"):
            self.add_error("confirm_password", "Passwords do not match.")
        if password:
            try:
                validate_password(password)
            except ValidationError as error:
                self.add_error("password", error)
        return cleaned


class PatientProfileForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Patient
        fields = [
            "name", "date_of_birth", "sex", "barangay", "contact_number", "email",
            "philhealth_number", "address", "allergies", "emergency_contact",
        ]
        widgets = {
            "date_of_birth": forms.DateInput(attrs={"type": "date"}),
            "allergies": forms.Textarea(attrs={"rows": 3}),
        }

    def clean_date_of_birth(self):
        value = self.cleaned_data["date_of_birth"]
        if value >= timezone.localdate():
            raise ValidationError("Date of birth must be in the past.")
        return value

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.user_id:
            self.fields["email"].required = True
            self.fields["email"].help_text = "This email is also used to sign in to the patient portal."

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if self.instance.user_id and not email:
            raise ValidationError("An email address is required for a portal-enabled patient.")
        existing = User.objects.filter(Q(email__iexact=email) | Q(username__iexact=email))
        if self.instance.user_id:
            existing = existing.exclude(pk=self.instance.user_id)
        if existing.exists():
            raise ValidationError("This email is already used by another account.")
        return email


class PatientForm(PatientProfileForm):
    class Meta(PatientProfileForm.Meta):
        fields = ["patient_code", *PatientProfileForm.Meta.fields]


class AppointmentForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Appointment
        fields = ["appointment_date", "appointment_time", "department", "consultation_type", "reason"]
        widgets = {
            "appointment_date": forms.DateInput(attrs={"type": "date"}),
            "appointment_time": forms.TimeInput(attrs={"type": "time"}),
            "reason": forms.Textarea(attrs={"rows": 3}),
        }

    def clean_appointment_date(self):
        value = self.cleaned_data["appointment_date"]
        unchanged_historical_date = self.instance.pk and value == self.instance.appointment_date
        if value < timezone.localdate() and not unchanged_historical_date:
            raise ValidationError("Appointment date cannot be in the past.")
        return value

    def clean(self):
        cleaned = super().clean()
        appointment_date = cleaned.get("appointment_date")
        appointment_time = cleaned.get("appointment_time")
        slot_changed = not self.instance.pk or (
            appointment_date,
            appointment_time,
        ) != (self.instance.appointment_date, self.instance.appointment_time)
        if appointment_date and appointment_time and slot_changed:
            try:
                validate_future_slot(appointment_date, appointment_time)
            except ValidationError as error:
                self.add_error("appointment_time", error)
        return cleaned


class StaffAppointmentForm(AppointmentForm):
    class Meta(AppointmentForm.Meta):
        fields = [
            "patient", "appointment_date", "appointment_time", "department", "provider_name",
            "consultation_type", "priority", "status", "reason", "notes",
        ]
        widgets = {
            **AppointmentForm.Meta.widgets,
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def clean(self):
        cleaned = super().clean()
        if self.instance.pk:
            if self.instance.status in TERMINAL_APPOINTMENT_STATUSES:
                raise ValidationError(f"A {self.instance.status.lower()} appointment is locked and cannot be edited.")
            new_status = cleaned.get("status")
            if new_status:
                try:
                    validate_appointment_transition(self.instance.status, new_status)
                except ValidationError as error:
                    self.add_error("status", error)
        elif cleaned.get("status") not in {"Pending", "Approved", "Scheduled"}:
            self.add_error("status", "A new appointment must be pending, approved, or scheduled.")
        return cleaned


class SymptomAssessmentForm(StyledFormMixin, forms.Form):
    symptoms = forms.CharField(
        help_text="Separate multiple symptoms with commas.",
        widget=forms.Textarea(attrs={"rows": 4, "placeholder": "Example: fever, cough, headache"}),
    )
    temperature = forms.DecimalField(required=False, min_value=30, max_value=45, decimal_places=1)
    chest_pain = forms.BooleanField(required=False)
    difficulty_breathing = forms.BooleanField(required=False)
    severe_bleeding = forms.BooleanField(required=False)
    unconscious = forms.BooleanField(required=False, label="Loss of consciousness")


class AppointmentAssignmentForm(StyledFormMixin, forms.Form):
    specialist = forms.ModelChoiceField(queryset=StaffProfile.objects.none())
    appointment_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    appointment_time = forms.TimeField(widget=forms.TimeInput(attrs={"type": "time"}))

    def __init__(self, *args, appointment, **kwargs):
        self.appointment = appointment
        super().__init__(*args, **kwargs)
        conflict_ids = list(Appointment.objects.filter(
            appointment_date=appointment.appointment_date,
            appointment_time=appointment.appointment_time,
            specialist__isnull=False,
            status__in=["Pending", "Approved", "Scheduled", "Rescheduled"],
        ).exclude(pk=appointment.pk).values_list("specialist_id", flat=True))
        specialists = StaffProfile.objects.filter(
            status="Active", user__is_active=True,
        ).exclude(role="Admin").exclude(pk__in=conflict_ids).select_related("user")
        matching = specialists.filter(specialty__iexact=appointment.department)
        self.fields["specialist"].queryset = matching if matching.exists() else specialists

    def clean(self):
        cleaned = super().clean()
        appointment_date = cleaned.get("appointment_date")
        appointment_time = cleaned.get("appointment_time")
        if appointment_date and appointment_time:
            validate_future_slot(appointment_date, appointment_time)
        return cleaned


class StaffUserForm(StyledFormMixin, forms.Form):
    first_name = forms.CharField(max_length=150)
    last_name = forms.CharField(max_length=150)
    email = forms.EmailField()
    role = forms.ChoiceField(choices=StaffProfile.ROLE_CHOICES, initial="Specialist")
    employee_id = forms.CharField(max_length=50)
    specialty = forms.CharField(max_length=100, initial="General Medicine")
    password = forms.CharField(widget=forms.PasswordInput, required=False, help_text="Required for new users.")
    is_active = forms.BooleanField(required=False, initial=True)

    def __init__(self, *args, user_instance=None, **kwargs):
        self.user_instance = user_instance
        super().__init__(*args, **kwargs)

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        existing = User.objects.filter(email__iexact=email)
        if self.user_instance:
            existing = existing.exclude(pk=self.user_instance.pk)
        if existing.exists():
            raise ValidationError("A user with this email already exists.")
        return email

    def clean_password(self):
        password = self.cleaned_data.get("password")
        if not self.user_instance and not password:
            raise ValidationError("A password is required for a new user.")
        if password:
            validate_password(password, self.user_instance)
        return password


class ConsultationForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = ConsultationRecord
        fields = ["appointment", "diagnosis", "treatment", "prescription", "clinical_notes", "follow_up_date"]
        widgets = {
            "diagnosis": forms.Textarea(attrs={"rows": 2}),
            "treatment": forms.Textarea(attrs={"rows": 2}),
            "prescription": forms.Textarea(attrs={"rows": 2}),
            "clinical_notes": forms.Textarea(attrs={"rows": 3}),
            "follow_up_date": forms.DateInput(attrs={"type": "date"}),
        }


    def __init__(self, *args, specialist=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["diagnosis"].required = True
        self.fields["clinical_notes"].required = True
        self.fields["clinical_notes"].help_text = "Record the clinical findings and decisions supporting this encounter."
        eligible = Appointment.objects.filter(
            status__in=["Approved", "Scheduled", "Rescheduled"],
            appointment_date__lte=timezone.localdate(),
            consultation_record__isnull=True,
        ).select_related("patient")
        if specialist and not self.instance.pk:
            eligible = eligible.filter(specialist=specialist)
        if self.instance.pk:
            eligible = Appointment.objects.filter(pk=self.instance.appointment_id)
            self.fields["appointment"].disabled = True
        self.fields["appointment"].queryset = eligible

    def clean_follow_up_date(self):
        value = self.cleaned_data.get("follow_up_date")
        unchanged_historical_date = self.instance.pk and value == self.instance.follow_up_date
        if value and value < timezone.localdate() and not unchanged_historical_date:
            raise ValidationError("Follow-up date cannot be in the past.")
        return value

    def clean(self):
        cleaned = super().clean()
        appointment = cleaned.get("appointment")
        follow_up_date = cleaned.get("follow_up_date")
        if appointment and follow_up_date and follow_up_date < appointment.appointment_date:
            self.add_error("follow_up_date", "Follow-up date cannot be earlier than the consultation date.")
        return cleaned


class SMSCampaignForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = SMSCampaign
        fields = ["title", "message", "target_group", "scheduled_date"]
        widgets = {
            "message": forms.Textarea(attrs={"rows": 4}),
            "scheduled_date": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["scheduled_date"].input_formats = ["%Y-%m-%dT%H:%M"]

    def clean_scheduled_date(self):
        value = self.cleaned_data.get("scheduled_date")
        if value and value <= timezone.now():
            raise ValidationError("Scheduled campaign time must be in the future.")
        return value


class InventoryForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = InventoryItem
        fields = ["name", "category", "unit", "quantity_on_hand", "reorder_level", "last_restocked"]
        widgets = {"last_restocked": forms.DateInput(attrs={"type": "date"})}
