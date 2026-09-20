from django.urls import path
from . import views

urlpatterns = [
    path("health/", views.api_health),
    path("auth/session/", views.api_session),
    path("auth/register/", views.api_register),
    path("auth/login/", views.api_login),
    path("auth/logout/", views.api_logout),
    path("profile/", views.api_profile),
    path("patients/", views.api_patients),
    path("patients/<int:patient_id>/", views.api_patient_detail),
    path("appointments/", views.api_appointments),
    path("appointments/<int:appointment_id>/", views.api_appointment_detail),
    path("triage/", views.api_triage),
    path("consultations/", views.api_consultations),
    path("notifications/", views.api_notifications),
    path("followups/", views.api_followups),
    path("users/", views.api_users),
    path("inventory/", views.api_inventory),
    path("sms-campaigns/", views.api_sms_campaigns),
    path("dashboard/", views.api_dashboard),
]
