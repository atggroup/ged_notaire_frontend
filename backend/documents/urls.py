from django.urls import path
from .views import (ArchiveDocumentView, DashboardSummaryView, DocumentDetailView, DocumentTrashView, DocumentsView, DocumentVersionView, ExportDocumentView, FavoriteDocumentView, OCRDocumentView, QualityCheckView, QueueView, ReferentielsView, SavedSearchView, SearchView, UploadDocumentView, ValidateDocumentView)

urlpatterns = [
    path("dashboard/summary", DashboardSummaryView.as_view()),
    path("referentiels", ReferentielsView.as_view()),
    path("documents", DocumentsView.as_view()), path("documents/upload", UploadDocumentView.as_view()),
    path("documents/archive", ArchiveDocumentView.as_view()), path("documents/favorite", FavoriteDocumentView.as_view()),
    path("documents/queue", QueueView.as_view()), path("documents/queue/<str:nom>", QueueView.as_view()), path("documents/<str:reference>/validate", ValidateDocumentView.as_view()),
    path("documents/<str:reference>/quality-check", QualityCheckView.as_view()), path("documents/<str:reference>/versions", DocumentVersionView.as_view()),
    path("documents/<str:reference>/ocr", OCRDocumentView.as_view()),
    path("documents/<str:reference>/export", ExportDocumentView.as_view()),
    path("documents/<str:reference>/<str:action>", DocumentTrashView.as_view()), path("documents/<str:reference>", DocumentDetailView.as_view()),
    path("search", SearchView.as_view()), path("searches/saved", SavedSearchView.as_view()),
]
