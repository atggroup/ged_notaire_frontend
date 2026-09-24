from django.conf import settings
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from accounts.views import GoogleOAuthCallbackView
from .frontend import serve_frontend
from .health import health

urlpatterns = []

# Interface d'administration Django : DÉSACTIVÉE par défaut. Sa connexion ne
# passe ni par le second facteur, ni par le verrouillage de compte, ni par le
# journal d'audit de la GED. À n'activer que ponctuellement (maintenance),
# jamais exposée sur Internet (nginx ne la relaie plus).
if getattr(settings, "DJANGO_ADMIN_ENABLED", False):
    urlpatterns.append(path("admin/", admin.site.urls))

urlpatterns += [
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
    path("api/", include("core.urls")),
    path("api/health", health, name="health"),
]

# Schéma et documentation interactive de l'API : la carte complète des routes
# n'a pas à être offerte à un visiteur anonyme. Désactivés par défaut en
# production ; quand ils sont activés, une connexion est exigée
# (SPECTACULAR_SETTINGS["SERVE_PERMISSIONS"]).
if getattr(settings, "API_DOCS_ENABLED", False):
    urlpatterns += [
        path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
        path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    ]

urlpatterns += [
    path("", serve_frontend),
    path("<path:requested_path>", serve_frontend),
]
