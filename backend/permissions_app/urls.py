from django.urls import path
from .views import AccessRequestView, PermissionView
urlpatterns = [path("permissions", PermissionView.as_view()), path("access-requests", AccessRequestView.as_view())]
