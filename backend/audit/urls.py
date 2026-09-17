from django.urls import path
from .views import AuditExportView, AuditIntegrityView, AuditListView
urlpatterns = [path("audit", AuditListView.as_view()), path("audit/export", AuditExportView.as_view()), path("audit/integrity", AuditIntegrityView.as_view())]
