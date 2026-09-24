"""Activation / désactivation de l'application d'authentification (TOTP).

GET  /api/auth/totp            état (activée, codes de secours restants)
POST /api/auth/totp/setup      { password } → secret + lien otpauth:// (en attente)
POST /api/auth/totp/confirm    { code } → activation + 10 codes de secours (affichés UNE fois)
POST /api/auth/totp/disable    { password, code } → désactivation

Chaque étape sensible exige le mot de passe (ou, pour un compte Google sans
mot de passe, la session en cours) : un poste laissé ouvert ne suffit pas à
changer le second facteur. Tout est journalisé et notifié.
"""
from django.utils import timezone
from rest_framework.response import Response

from audit.services import log_event
from notifications.services import notify, notify_admins

from . import totp
from .views import APIView


def _mot_de_passe_confirme(request) -> bool:
    user = request.user
    if not user.has_usable_password():
        return True  # compte Google : la session authentifiée fait foi
    return user.check_password(str(request.data.get("password", "")))


class TotpStatusView(APIView):
    def get(self, request):
        u = request.user
        return Response({"enabled": u.totp_enabled, "enabledAt": u.totp_enabled_at.isoformat() if u.totp_enabled_at else None,
                         "recoveryCodesRemaining": len(u.totp_recovery_hashes or []) if u.totp_enabled else 0})


class TotpSetupView(APIView):
    def post(self, request):
        if not _mot_de_passe_confirme(request):
            log_event(request, "totp_setup_refused", "user", str(request.user.pk), result="failure")
            return Response({"password": ["Mot de passe incorrect."]}, status=400)
        secret = totp.nouveau_secret()
        request.user.totp_pending_secret = totp.chiffrer_secret(secret)
        request.user.save(update_fields=["totp_pending_secret"])
        log_event(request, "totp_setup_started", "user", str(request.user.pk))
        # Le secret n'est montré qu'à cette étape, à son titulaire, pour être
        # saisi dans l'application. Il n'est jamais journalisé.
        return Response({"secret": " ".join(secret[i:i + 4] for i in range(0, len(secret), 4)),
                         "otpauthUri": totp.uri_otpauth(secret, request.user.email)})


class TotpConfirmView(APIView):
    def post(self, request):
        user = request.user
        if not user.totp_pending_secret:
            return Response({"detail": "Commencez par « Activer » pour obtenir une clé."}, status=400)
        secret = totp.dechiffrer_secret(user.totp_pending_secret)
        compteur = totp.verifier(secret, str(request.data.get("code", "")), None)
        if compteur is None:
            log_event(request, "totp_confirm_failed", "user", str(user.pk), result="failure")
            return Response({"code": ["Code incorrect. Vérifiez l'heure de votre téléphone et réessayez."]}, status=400)
        codes = totp.codes_de_secours()
        user.totp_secret = user.totp_pending_secret
        user.totp_pending_secret = ""
        user.totp_enabled_at = timezone.now()
        user.totp_last_counter = compteur
        user.totp_recovery_hashes = [totp.empreinte_secours(user.pk, c) for c in codes]
        user.save(update_fields=["totp_secret", "totp_pending_secret", "totp_enabled_at", "totp_last_counter", "totp_recovery_hashes"])
        log_event(request, "totp_enabled", "user", str(user.pk))
        notify(user, "security", "Application d'authentification activée",
               "Votre connexion exige désormais le code de votre application d'authentification. "
               "Conservez vos codes de secours en lieu sûr.", severity="attention", email=True)
        return Response({"enabled": True, "recoveryCodes": codes})


class TotpDisableView(APIView):
    def post(self, request):
        user = request.user
        if not user.totp_enabled:
            return Response({"detail": "L'application d'authentification n'est pas activée."}, status=400)
        compteur = totp.verifier(totp.dechiffrer_secret(user.totp_secret), str(request.data.get("code", "")), user.totp_last_counter)
        if not _mot_de_passe_confirme(request) or compteur is None:
            log_event(request, "totp_disable_refused", "user", str(user.pk), result="failure")
            return Response({"detail": "Mot de passe ou code incorrect."}, status=400)
        user.totp_secret = ""
        user.totp_enabled_at = None
        user.totp_last_counter = None
        user.totp_recovery_hashes = []
        user.save(update_fields=["totp_secret", "totp_enabled_at", "totp_last_counter", "totp_recovery_hashes"])
        log_event(request, "totp_disabled", "user", str(user.pk))
        notify(user, "security", "Application d'authentification désactivée",
               "Votre second facteur repose de nouveau sur l'e-mail. Si ce n'était pas vous, prévenez immédiatement le notaire.",
               severity="haute", email=True)
        if user.role == "admin":
            notify_admins("security", "Second facteur affaibli",
                          f"{user.display_name} a désactivé son application d'authentification.",
                          exclude=user, severity="haute")
        return Response({"enabled": False})
