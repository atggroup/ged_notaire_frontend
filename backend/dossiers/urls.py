from django.urls import path
from .views import ClientDirectoryView, DossierAssignmentView, DossierChecklistView, DossierDetailView, DossierExportView, DossierView, PhysicalArchiveView

urlpatterns = [
    path("clients", ClientDirectoryView.as_view()),
    path("dossiers", DossierView.as_view()),
    path("dossiers/<str:reference>", DossierDetailView.as_view()),
    path("dossiers/<str:reference>/assignments", DossierAssignmentView.as_view()),
    path("dossiers/<str:reference>/checklist", DossierChecklistView.as_view()),
    path("dossiers/<str:reference>/physical-records", PhysicalArchiveView.as_view()),
    path("dossiers/<str:reference>/physical-records/<str:action>", PhysicalArchiveView.as_view()),
    path("dossiers/<str:reference>/export", DossierExportView.as_view()),
]
