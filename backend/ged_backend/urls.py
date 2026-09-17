from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from accounts.views import GoogleOAuthCallbackView
from .frontend import serve_frontend
from .health import health

urlpatterns = [
    path("admin/", admin.site.urls),
    # Mounted at the site root — must match, byte for byte, the redirect URI
    # registered on the Google OAuth client (see accounts/views.py).
    path("auth/oauth/google/callback", GoogleOAuthCallbackView.as_view()),
    path("api/", include("accounts.urls")),
    path("api/", include("documents.urls")),
    path("api/", include("dossiers.urls")),
    path("api/", include("permissions_app.urls")),
    path("api/", include("audit.urls")),
    path("api/", include("notifications.urls")),
    path("api/", include("settings_app.urls")),
    path("api/health", health, name="health"),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("", serve_frontend),
    path("<path:requested_path>", serve_frontend),
]
