from django.urls import path
from .views import AuditExportView, AuditIntegrityView, AuditListView, SecurityAlertDetailView, SecurityAlertListView
urlpatterns = [
    path("audit", AuditListView.as_view()), path("audit/export", AuditExportView.as_view()), path("audit/integrity", AuditIntegrityView.as_view()),
    path("security/alerts", SecurityAlertListView.as_view()), path("security/alerts/<int:pk>", SecurityAlertDetailView.as_view()),
]
