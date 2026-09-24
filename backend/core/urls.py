from django.urls import path

from .views import JobListView, JobRunsView

urlpatterns = [
    path("automation/jobs", JobListView.as_view()),
    path("automation/jobs/<str:name>/runs", JobRunsView.as_view()),
]
