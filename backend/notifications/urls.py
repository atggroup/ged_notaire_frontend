from django.urls import path
from .views import MarkNotificationReadView, NotificationsView, TasksView, UnreadNotificationsCountView
urlpatterns = [
    path("notifications", NotificationsView.as_view()),
    path("notifications/unread-count", UnreadNotificationsCountView.as_view()),
    path("notifications/read", MarkNotificationReadView.as_view()),
    path("tasks", TasksView.as_view()),
]
