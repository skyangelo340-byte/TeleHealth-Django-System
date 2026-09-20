import json
from datetime import timedelta

from django.contrib.auth.models import User
from django.core.management import call_command
from django.db.models.deletion import ProtectedError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Appointment, ConsultationRecord, FollowUpReminder, Notification, Patient, StaffNotification, StaffProfile


class TeleHealthAPITests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="patient@test.local", email="patient@test.local", password="StrongPass123!")
        self.patient = Patient.objects.create(
            user=self.user, patient_code="PAT-TEST01", name="Test, Patient", date_of_birth="1995-01-01",
            sex="F", barangay="Lumang Bayan", contact_number="+639000000000", email="patient@test.local",
        )

    def test_health_endpoint(self):
        response = self.client.get("/api/health/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])

    def test_patient_login_and_profile(self):
        response = self.client.post(
            "/api/auth/login/", data=json.dumps({"email": "patient@test.local", "password": "StrongPass123!"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        response = self.client.get("/api/profile/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["patient"]["patient_code"], "PAT-TEST01")

    def test_portal_patient_cannot_clear_login_email(self):
        self.client.force_login(self.user)
        response = self.client.put(
            "/api/profile/",
            data=json.dumps({"email": ""}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.patient.refresh_from_db()
        self.user.refresh_from_db()
        self.assertEqual(self.patient.email, "patient@test.local")
        self.assertEqual(self.user.username, "patient@test.local")

    def test_patient_can_schedule_and_filter_appointment(self):
        self.client.login(username="patient@test.local", password="StrongPass123!")
        future = timezone.localdate() + timedelta(days=2)
        response = self.client.post(
            "/api/appointments/",
            data=json.dumps({
                "appointment_date": future.isoformat(), "appointment_time": "10:30",
                "department": "General Medicine", "consultation_type": "Online", "reason": "Persistent cough",
            }),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["appointment"]["status"], "Pending")
        response = self.client.get("/api/appointments/?mine=1&status=Pending")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 1)

    def test_symptom_assessment(self):
        self.client.login(username="patient@test.local", password="StrongPass123!")
        response = self.client.post(
            "/api/triage/",
            data=json.dumps({"symptoms": ["chest pain", "difficulty breathing"], "chest_pain": True, "difficulty_breathing": True}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["triage"]["priority"], "High")

    def test_anonymous_users_cannot_read_health_data_collections(self):
        for path in ["/api/patients/", "/api/appointments/", "/api/triage/", "/api/users/", "/api/dashboard/"]:
            with self.subTest(path=path):
                self.assertIn(self.client.get(path).status_code, {401, 403})

    def test_malformed_json_types_return_validation_errors(self):
        response = self.client.post("/api/auth/register/", data=json.dumps([]), content_type="application/json")
        self.assertEqual(response.status_code, 400)
        self.client.login(username="patient@test.local", password="StrongPass123!")
        response = self.client.post(
            "/api/appointments/",
            data=json.dumps({"appointment_date": [], "appointment_time": {}}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_registration_does_not_duplicate_existing_clinical_patient(self):
        Patient.objects.create(
            patient_code="PAT-UNLINKED",
            name="Existing, Patient",
            date_of_birth="1990-01-01",
            sex="F",
            contact_number="+639123456789",
            email="existing@test.local",
        )
        response = self.client.post(
            "/api/auth/register/",
            data=json.dumps({
                "first_name": "Existing",
                "last_name": "Patient",
                "email": "existing@test.local",
                "password": "NewStrongPass123!",
                "date_of_birth": "1990-01-01",
                "sex": "F",
                "contact_number": "+639123456789",
                "barangay": "Lumang Bayan",
            }),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Patient.objects.filter(email="existing@test.local").count(), 1)

    def test_malformed_json_identifiers_do_not_raise_server_errors(self):
        self.client.force_login(self.user)
        response = self.client.post(
            "/api/notifications/",
            data=json.dumps({"id": []}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)

    def test_patient_cannot_change_protected_appointment_fields(self):
        appointment = Appointment.objects.create(
            patient=self.patient,
            appointment_date=timezone.localdate() + timedelta(days=3),
            appointment_time="09:00",
            status="Pending",
            priority="Medium",
            queue_number=1,
        )
        self.client.login(username="patient@test.local", password="StrongPass123!")
        response = self.client.put(
            f"/api/appointments/{appointment.pk}/",
            data=json.dumps({"status": "Completed", "priority": "High", "provider_name": "Fake Provider"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        appointment.refresh_from_db()
        self.assertEqual(appointment.status, "Pending")
        self.assertEqual(appointment.priority, "Medium")
        self.assertEqual(appointment.provider_name, "To be assigned")


class ServerRenderedPageTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="admin@test.local", email="admin@test.local", password="AdminPass123!",
            first_name="Test", last_name="Admin", is_staff=True, is_superuser=True,
        )
        StaffProfile.objects.create(user=self.admin, role="Admin", employee_id="ADMIN-TEST")
        self.patient_user = User.objects.create_user(
            username="portal@test.local", email="portal@test.local", password="PortalPass123!",
            first_name="Portal", last_name="Patient",
        )
        self.patient = Patient.objects.create(
            user=self.patient_user, patient_code="PAT-WEB01", name="Patient, Portal",
            date_of_birth="1990-02-03", sex="F", barangay="Lumang Bayan",
            contact_number="+639111111111", email="portal@test.local",
        )

    def test_public_authentication_pages_render(self):
        self.assertEqual(self.client.get("/login/").status_code, 200)
        self.assertEqual(self.client.get("/register/").status_code, 200)

    def test_all_staff_pages_render_from_django_templates(self):
        self.client.force_login(self.admin)
        paths = [
            "/admin-dashboard/", "/users/", "/patients/", "/appointments/", "/teleconsultation/",
            "/ehr/", "/triage/", "/communications/", "/reports/", "/admin/",
        ]
        for path in paths:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "TeleHealth")

    def test_patient_portal_renders_and_staff_pages_are_denied(self):
        self.client.force_login(self.patient_user)
        self.assertEqual(self.client.get("/patient-portal/").status_code, 200)
        self.assertEqual(self.client.get("/patients/").status_code, 403)

    def test_patient_can_schedule_from_django_form(self):
        self.client.force_login(self.patient_user)
        response = self.client.post(
            "/patient-portal/appointments/schedule/",
            {
                "appointment_date": (timezone.localdate() + timedelta(days=4)).isoformat(),
                "appointment_time": "13:30",
                "department": "General Medicine",
                "consultation_type": "Online",
                "reason": "Follow-up request",
            },
        )
        self.assertRedirects(response, "/patient-portal/schedule/")
        self.assertTrue(Appointment.objects.filter(patient=self.patient, reason="Follow-up request").exists())

    def test_login_rejects_external_next_redirect(self):
        response = self.client.post(
            "/login/",
            {"email": "admin@test.local", "password": "AdminPass123!", "next": "https://malicious.example/phish"},
        )
        self.assertRedirects(response, "/admin-dashboard/", fetch_redirect_response=False)

    def test_each_account_type_redirects_to_its_dashboard(self):
        response = self.client.post("/login/", {"email": "admin@test.local", "password": "AdminPass123!"})
        self.assertRedirects(response, "/admin-dashboard/", fetch_redirect_response=False)
        self.client.post("/logout/")

        specialist = User.objects.create_user(
            username="specialist@test.local", email="specialist@test.local",
            password="SpecialistPass123!", first_name="Care", last_name="Specialist",
        )
        StaffProfile.objects.create(user=specialist, role="Specialist", employee_id="SP-TEST")
        response = self.client.post("/login/", {"email": "specialist@test.local", "password": "SpecialistPass123!"})
        self.assertRedirects(response, "/specialist-dashboard/", fetch_redirect_response=False)
        self.client.post("/logout/")

        response = self.client.post("/login/", {"email": "portal@test.local", "password": "PortalPass123!"})
        self.assertRedirects(response, "/patient-dashboard/", fetch_redirect_response=False)

    def test_patient_registration_redirects_to_login_without_allergies(self):
        registration_page = self.client.get("/register/")
        self.assertNotContains(registration_page, 'name="allergies"')
        response = self.client.post("/register/", {
            "first_name": "New",
            "last_name": "Patient",
            "email": "new.patient@test.local",
            "password": "NewPatientPass123!",
            "confirm_password": "NewPatientPass123!",
            "date_of_birth": "1995-04-03",
            "sex": "F",
            "contact_number": "+639123456700",
            "barangay": "Lumang Bayan",
            "philhealth_number": "",
            "address": "",
            "emergency_contact": "",
        })
        self.assertRedirects(response, "/login/")
        account = User.objects.get(email="new.patient@test.local")
        self.assertTrue(Patient.objects.filter(user=account).exists())
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_duplicate_patient_registration_shows_form_error(self):
        response = self.client.post("/register/", {
            "first_name": "Portal",
            "last_name": "Patient",
            "email": "portal@test.local",
            "password": "PortalPass123!",
            "confirm_password": "PortalPass123!",
            "date_of_birth": "1990-02-03",
            "sex": "F",
            "contact_number": "+639111111111",
            "barangay": "Lumang Bayan",
        })
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "already exists", status_code=400)

    def test_registration_normalizes_optional_philhealth_placeholders(self):
        response = self.client.post("/register/", {
            "first_name": "No",
            "last_name": "PhilHealth",
            "email": "no.philhealth@test.local",
            "password": "NoPhilhealthPass123!",
            "confirm_password": "NoPhilhealthPass123!",
            "date_of_birth": "1993-05-06",
            "sex": "M",
            "contact_number": "+639222333444",
            "barangay": "Lumang Bayan",
            "philhealth_number": "N/A",
        })
        self.assertRedirects(response, "/login/")
        patient = Patient.objects.get(email="no.philhealth@test.local")
        self.assertEqual(patient.philhealth_number, "")

    def test_portal_tab_links_are_canonical(self):
        self.client.force_login(self.patient_user)
        response = self.client.get("/patient-portal/schedule/")
        self.assertContains(response, 'href="/patient-portal/profile/"')
        self.assertNotContains(response, "?tab=profile")

    def test_invalid_schedule_keeps_values_and_field_errors(self):
        self.client.force_login(self.patient_user)
        response = self.client.post(
            "/patient-portal/appointments/schedule/",
            {
                "appointment_date": (timezone.localdate() - timedelta(days=1)).isoformat(),
                "appointment_time": "08:00", "department": "General Medicine",
                "consultation_type": "Online", "reason": "Keep this entered reason",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Appointment date cannot be in the past", status_code=400)
        self.assertContains(response, "Keep this entered reason", status_code=400)

    def test_inactive_patient_cannot_use_self_service(self):
        self.patient.status = "Inactive"
        self.patient.save(update_fields=["status"])
        self.client.force_login(self.patient_user)
        self.assertContains(self.client.get("/patient-portal/"), "Patient access is currently inactive")
        response = self.client.post("/patient-portal/appointments/schedule/", {})
        self.assertEqual(response.status_code, 403)


class WorkflowIntegrityTests(TestCase):
    def setUp(self):
        self.patient_user = User.objects.create_user(username="flow@patient.local", email="flow@patient.local", password="FlowPass123!")
        self.patient = Patient.objects.create(
            user=self.patient_user, patient_code="PAT-FLOW", name="Flow, Patient", date_of_birth="1992-01-01",
            sex="F", contact_number="+639222222222", email="flow@patient.local",
        )
        self.admin = User.objects.create_user(username="flow@admin.local", email="flow@admin.local", password="AdminFlow123!", is_staff=True, is_superuser=True)
        StaffProfile.objects.create(user=self.admin, role="Admin", employee_id="FLOW-ADMIN")
        self.bhw = User.objects.create_user(username="flow@bhw.local", email="flow@bhw.local", password="BhwFlow123!", is_staff=True)
        StaffProfile.objects.create(user=self.bhw, role="BHW", employee_id="FLOW-BHW")
        self.doctor = User.objects.create_user(username="flow@doctor.local", email="flow@doctor.local", password="DoctorFlow123!", is_staff=True)
        StaffProfile.objects.create(user=self.doctor, role="Physician", employee_id="FLOW-DOC")

    def make_appointment(self, *, status="Scheduled", days=1, queue=1):
        return Appointment.objects.create(
            patient=self.patient, appointment_date=timezone.localdate() + timedelta(days=days),
            appointment_time="10:00", queue_number=queue, status=status,
        )

    def test_bhw_cannot_write_clinical_records_or_archive_patients(self):
        self.client.force_login(self.bhw)
        self.assertEqual(self.client.get("/ehr/").status_code, 403)
        self.assertEqual(self.client.post(f"/patients/{self.patient.pk}/archive/").status_code, 403)

    def test_api_patient_delete_archives_and_retains_history(self):
        appointment = self.make_appointment()
        self.client.force_login(self.admin)
        response = self.client.delete(f"/api/patients/{self.patient.pk}/")
        self.assertEqual(response.status_code, 200)
        self.patient.refresh_from_db()
        self.patient_user.refresh_from_db()
        self.assertEqual(self.patient.status, "Inactive")
        self.assertFalse(self.patient_user.is_active)
        appointment.refresh_from_db()
        self.assertEqual(appointment.status, "Cancelled")
        self.assertTrue(Appointment.objects.filter(pk=appointment.pk).exists())
        with self.assertRaises(ProtectedError):
            self.patient.delete()

    def test_admin_can_reactivate_patient_and_portal_account(self):
        self.patient.status = "Inactive"
        self.patient.save(update_fields=["status"])
        self.patient_user.is_active = False
        self.patient_user.save(update_fields=["is_active"])
        self.client.force_login(self.admin)
        response = self.client.post(f"/patients/{self.patient.pk}/reactivate/")
        self.assertRedirects(response, "/patients/")
        self.patient.refresh_from_db()
        self.patient_user.refresh_from_db()
        self.assertEqual(self.patient.status, "Active")
        self.assertTrue(self.patient_user.is_active)

    def test_admin_confirmation_notifies_patient_and_specialist(self):
        appointment = self.make_appointment(status="Pending", days=3)
        doctor_profile = self.doctor.staff_profile
        self.client.force_login(self.admin)
        response = self.client.post(
            f"/appointments/{appointment.pk}/confirm/",
            {
                "specialist": doctor_profile.pk,
                "appointment_date": appointment.appointment_date.isoformat(),
                "appointment_time": "10:00",
            },
        )
        self.assertRedirects(response, "/admin-dashboard/")
        appointment.refresh_from_db()
        self.assertEqual(appointment.status, "Scheduled")
        self.assertEqual(appointment.specialist, doctor_profile)
        self.assertEqual(appointment.provider_name, self.doctor.get_full_name() or self.doctor.username)
        self.assertTrue(StaffNotification.objects.filter(recipient=doctor_profile, appointment=appointment).exists())
        self.assertTrue(Notification.objects.filter(patient=self.patient, title="Appointment confirmed", channel="System").exists())

    def test_specialist_acknowledgement_notifies_patient(self):
        appointment = self.make_appointment(status="Scheduled", days=2)
        appointment.specialist = self.doctor.staff_profile
        appointment.provider_name = self.doctor.username
        appointment.save(update_fields=["specialist", "provider_name"])
        self.client.force_login(self.doctor)
        response = self.client.post(f"/specialist/appointments/{appointment.pk}/acknowledge/")
        self.assertRedirects(response, "/specialist-dashboard/")
        appointment.refresh_from_db()
        self.assertIsNotNone(appointment.specialist_confirmed_at)
        self.assertTrue(Notification.objects.filter(patient=self.patient, title="Specialist confirmed your appointment").exists())

    def test_terminal_appointment_cannot_be_cancelled_or_rescheduled(self):
        appointment = self.make_appointment(status="Completed")
        self.client.force_login(self.patient_user)
        response = self.client.delete(f"/api/appointments/{appointment.pk}/")
        self.assertEqual(response.status_code, 409)
        response = self.client.put(
            f"/api/appointments/{appointment.pk}/",
            data=json.dumps({"appointment_date": (timezone.localdate() + timedelta(days=4)).isoformat(), "appointment_time": "11:00"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 409)
        appointment.refresh_from_db()
        self.assertEqual(appointment.status, "Completed")

    def test_duplicate_patient_slot_is_rejected(self):
        future = timezone.localdate() + timedelta(days=3)
        self.client.force_login(self.patient_user)
        payload = {"appointment_date": future.isoformat(), "appointment_time": "10:30", "department": "General Medicine", "consultation_type": "Online", "reason": "First"}
        self.assertEqual(self.client.post("/api/appointments/", data=json.dumps(payload), content_type="application/json").status_code, 201)
        payload["reason"] = "Duplicate"
        self.assertEqual(self.client.post("/api/appointments/", data=json.dumps(payload), content_type="application/json").status_code, 400)

    def test_cancelled_slot_can_be_rebooked(self):
        appointment = self.make_appointment(status="Scheduled", days=3)
        self.client.force_login(self.patient_user)
        self.assertEqual(self.client.delete(f"/api/appointments/{appointment.pk}/").status_code, 200)
        response = self.client.post(
            "/api/appointments/",
            data=json.dumps({
                "appointment_date": appointment.appointment_date.isoformat(),
                "appointment_time": "10:00",
                "department": "General Medicine",
                "consultation_type": "Online",
                "reason": "Replacement appointment",
            }),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)

    def test_staff_can_mark_elapsed_appointment_no_show(self):
        appointment = self.make_appointment(status="Scheduled", days=-1)
        self.client.force_login(self.admin)
        response = self.client.put(
            f"/api/appointments/{appointment.pk}/",
            data=json.dumps({"status": "No-show"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        appointment.refresh_from_db()
        self.assertEqual(appointment.status, "No-show")

    def test_consultation_completion_synchronizes_visit_and_reminder(self):
        appointment = self.make_appointment(status="Scheduled", days=0)
        appointment.specialist = self.doctor.staff_profile
        appointment.save(update_fields=["specialist"])
        self.client.force_login(self.doctor)
        follow_up = timezone.localdate() + timedelta(days=7)
        response = self.client.post(
            "/ehr/new/",
            {"appointment": appointment.pk, "diagnosis": "Test diagnosis", "treatment": "Test plan", "prescription": "None", "clinical_notes": "Stable", "follow_up_date": follow_up.isoformat()},
        )
        self.assertRedirects(response, "/ehr/")
        appointment.refresh_from_db()
        self.patient.refresh_from_db()
        self.assertEqual(appointment.status, "Completed")
        self.assertEqual(self.patient.last_visit, timezone.localdate())
        self.assertTrue(FollowUpReminder.objects.filter(consultation__appointment=appointment, reminder_date=follow_up, status="Pending").exists())

    def test_consultation_api_creates_clinical_record(self):
        appointment = self.make_appointment(status="Scheduled", days=0)
        appointment.specialist = self.doctor.staff_profile
        appointment.save(update_fields=["specialist"])
        self.client.force_login(self.doctor)
        response = self.client.post(
            "/api/consultations/",
            data=json.dumps({
                "appointment_id": appointment.pk,
                "diagnosis": "Upper respiratory infection",
                "treatment": "Supportive care",
                "prescription": "None",
                "clinical_notes": "Patient stable during remote assessment.",
            }),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        appointment.refresh_from_db()
        self.assertEqual(appointment.status, "Completed")
        self.assertTrue(ConsultationRecord.objects.filter(appointment=appointment).exists())

    def test_pending_sms_reminder_is_not_falsely_completed_or_duplicated(self):
        appointment = self.make_appointment(status="Completed", days=-1)
        record = ConsultationRecord.objects.create(appointment=appointment)
        reminder = FollowUpReminder.objects.create(
            patient=self.patient, consultation=record, reminder_date=timezone.localdate(), message="Due follow-up",
        )
        call_command("process_reminders")
        reminder.refresh_from_db()
        self.assertEqual(reminder.status, "Pending")
        self.assertIsNotNone(reminder.notification_id)
        call_command("process_reminders")
        self.assertEqual(Notification.objects.filter(title="Follow-up reminder").count(), 1)
