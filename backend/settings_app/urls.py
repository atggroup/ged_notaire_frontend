from django.urls import path
from .views import BackupStatusView, FilialeContextView, RestoreTestView, RunBackupView, SettingsView
from .key_views import KeyStatusView, GenerateKeyView, ActivateKeyView, RetireKeyView, RecoveryExportView, RecoveryImportView
urlpatterns = [
    path("settings", SettingsView.as_view()),
    path("backups", BackupStatusView.as_view()), path("backups/run", RunBackupView.as_view()), path("backups/restore-test", RestoreTestView.as_view()),
    path("key-management/status", KeyStatusView.as_view()), path("key-management/generate", GenerateKeyView.as_view()), path("key-management/activate", ActivateKeyView.as_view()), path("key-management/retire", RetireKeyView.as_view()),
    path("key-management/recovery-export", RecoveryExportView.as_view()), path("key-management/recovery-import", RecoveryImportView.as_view()),
    path("context/filiale", FilialeContextView.as_view()),
]
