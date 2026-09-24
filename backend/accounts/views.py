"""JWT authentication, invitations, OTP registration and Google Sign-In endpoints."""
import secrets
from datetime import timedelta
from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core import signing
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponseRedirect
from django.utils import timezone
from rest_framework import permissions, status, serializers as drf_serializers
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import AccessToken
from ged_backend.api import ContractSerializer
from .google_oauth import GoogleOAuthError, build_authorization_url, exchange_code_for_identity
from .models import InviteCode, OTPCode, PendingRegistration, User
from .serializers import LoginSerializer, PasswordSerializer, RegisterStartSerializer
from notifications.services import notify


def notify_account_created(user: User) -> None:
    notify(
        user,
        "account",
        "Bienvenue sur GED",
        f"Votre compte {user.get_role_display()} a été créé avec succès. Vous pouvez dès maintenant vous connecter.",
    )


class APIView(GenericAPIView):
    """Generic API base so the generated OpenAPI schema includes contract routes."""
    serializer_class = ContractSerializer


OTP_TTL_SECONDS = 600
OTP_MAX_ATTEMPTS = 5


def audit(request, action: str, target_type: str = "", target_id: str = "", result: str = "success") -> None:
    from audit.services import log_event
    log_event(request, action, target_type, target_id, result)


def auth_response(user: User, *, mfa_verified: bool = False) -> dict[str, str]:
    """Émet le jeton de session et mémorise si un second facteur l'a couverte.

    Cette mémoire sert à révoquer une session ouverte par simple mot de passe
    le jour où une habilitation confidentielle lui est accordée (voir
    `accounts.services.reevaluer_exigence_mfa`)."""
    if user.session_mfa_verified != mfa_verified:
        user.session_mfa_verified = mfa_verified
        user.save(update_fields=["session_mfa_verified"])
    token = AccessToken.for_user(user)
    token["sv"] = user.session_version
    return {"token": str(token), "role": user.role, "name": user.display_name, "email": user.email}


def latest_otp(email: str, purpose: str) -> OTPCode | None:
    return OTPCode.objects.filter(email__iexact=email, purpose=purpose, used_at__isnull=True).order_by("-created_at").first()


def exige_second_facteur(user: User) -> bool:
    """La politique de second facteur est fondée sur le RISQUE : toujours pour
    le notaire, et pour tout compte détenant un accès à du « Confidentiel » ou
    du « Très confidentiel ».

    Cette décision vaut pour TOUS les chemins d'authentification. Elle vivait
    auparavant dans le seul `LoginView` : la connexion Google délivrait donc
    une session de notaire complète, valable 8 h, sans second facteur — une
    politique ne vaut que ce que vaut son chemin le plus faible.
    """
    if user.role == User.Role.ADMIN:
        return True
    sensibles = ["Confidentiel", "Très confidentiel"]
    from permissions_app.models import Permission
    habilite = Permission.objects.filter(user=user).filter(
        Q(document__niveau_de_confidentialite__in=sensibles)
        | Q(dossier__niveau_de_confidentialite__in=sensibles)
    ).exists()
    return habilite or user.dossier_assignments.filter(
        dossier__niveau_de_confidentialite__in=sensibles
    ).exists()


def defi_second_facteur(request, user: User) -> Response:
    """Envoie le code à usage unique et renvoie la réponse 202 attendue par
    l'interface (même contrat quel que soit le chemin d'authentification)."""
    # Ticket de défi signé, délivré UNIQUEMENT après la première étape
    # (mot de passe ou Google). Sans lui, un code d'application seul
    # suffirait à se connecter : le second facteur deviendrait le premier.
    defi = signing.dumps({"uid": user.pk, "etat": _etat_compte(user)}, salt=MFA_CHALLENGE_SALT)
    if user.totp_enabled:
        audit(request, "mfa_challenge_totp", "user", str(user.pk))
        return Response({"mfaRequired": True, "method": "totp", "email": user.email, "challenge": defi}, status=202)
    send_otp(request, user.email, OTPCode.Purpose.LOGIN_MFA)
    audit(request, "mfa_challenge_sent", "user", str(user.pk))
    return Response(
        {"mfaRequired": True, "method": "email", "email": user.email, "expiresInSeconds": OTP_TTL_SECONDS, "challenge": defi},
        status=202,
    )


MFA_CHALLENGE_SALT = "mfa-challenge"
MFA_CHALLENGE_TTL = 600
MFA_MAX_ECHECS = 5


def _defi_valide(user: User, defi: str) -> bool:
    try:
        contenu = signing.loads(defi or "", salt=MFA_CHALLENGE_SALT, max_age=MFA_CHALLENGE_TTL)
    except signing.BadSignature:
        return False
    return contenu.get("uid") == user.pk and contenu.get("etat") == _etat_compte(user)


def _verifier_totp(request, user: User) -> Response | None:
    """Vérifie le code d'application (ou un code de secours). Renvoie une
    Response d'erreur, ou None si le second facteur est franchi."""
    from . import totp
    if user.locked_until and user.locked_until > timezone.now():
        audit(request, "login_locked", "user", str(user.pk), result="failure")
        return Response({"detail": "Compte temporairement verrouillé après des tentatives répétées."}, status=429)
    if not _defi_valide(user, str(request.data.get("challenge", ""))):
        return Response({"detail": "Vérification expirée : reconnectez-vous avec votre mot de passe."}, status=400)
    code = str(request.data.get("code", "")).strip()
    secours = str(request.data.get("recoveryCode", "")).strip()
    methode = None
    if code:
        compteur = totp.verifier(totp.dechiffrer_secret(user.totp_secret), code, user.totp_last_counter)
        if compteur is not None:
            user.totp_last_counter = compteur
            methode = "totp"
    if methode is None and secours:
        empreinte = totp.empreinte_secours(user.pk, secours)
        if empreinte in (user.totp_recovery_hashes or []):
            user.totp_recovery_hashes = [h for h in user.totp_recovery_hashes if h != empreinte]
            methode = "code_de_secours"
    if methode is None:
        user.failed_login_attempts += 1
        if user.failed_login_attempts >= MFA_MAX_ECHECS:
            user.locked_until = timezone.now() + timedelta(minutes=15)
            user.failed_login_attempts = 0
        user.save(update_fields=["failed_login_attempts", "locked_until"])
        audit(request, "mfa_failed", "user", str(user.pk), result="failure")
        return Response({"detail": "Code invalide."}, status=400)
    user.failed_login_attempts = 0
    user.save(update_fields=["totp_last_counter", "totp_recovery_hashes", "failed_login_attempts"])
    if methode == "code_de_secours":
        restants = len(user.totp_recovery_hashes)
        notify(user, "security", "Code de secours utilisé",
               f"Un code de secours vient d'être utilisé pour vous connecter. Il vous en reste {restants}. "
               "Si ce n'était pas vous, prévenez immédiatement le notaire.", severity="haute", email=True)
    from audit.services import log_event
    log_event(request, "login_mfa", "user", str(user.pk), metadata={"methode": methode})
    return None


class LoginView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        email = str(request.data.get("email", "")).lower()
        candidate = User.objects.filter(email__iexact=email).first()
        if candidate and candidate.locked_until and candidate.locked_until > timezone.now():
            audit(request, "login_locked", "user", str(candidate.pk), result="failure")
            return Response({"detail": "Compte temporairement verrouillé après des tentatives répétées."}, status=429)
        serializer = LoginSerializer(data=request.data)
        if not serializer.is_valid():
            if candidate:
                candidate.failed_login_attempts += 1
                if candidate.failed_login_attempts >= 5:
                    candidate.locked_until = timezone.now() + timedelta(minutes=15)
                    candidate.failed_login_attempts = 0
                candidate.save(update_fields=["failed_login_attempts", "locked_until"])
            # La cible est enregistrée : sans elle, la surveillance ne pouvait
            # pas distinguer une attaque ciblée sur UN compte d'erreurs éparses.
            audit(request, "login_failed", "user" if candidate else "", str(candidate.pk) if candidate else "", result="failure")
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        user = serializer.validated_data["user"]
        user.failed_login_attempts = 0
        user.locked_until = None
        user.last_login = timezone.now()
        user.save(update_fields=["last_login", "failed_login_attempts", "locked_until"])
        if exige_second_facteur(user):
            # A password alone is never sufficient for a notaire/admin or an
            # account entrusted with confidential documents.
            return defi_second_facteur(request, user)
        audit(request, "login", "user", str(user.pk))
        return Response(auth_response(user))


class MFAVerifyView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        email = str(request.data.get("email", "")).lower()
        user = User.objects.filter(email__iexact=email, is_active=True).first()
        if not user:
            return Response({"detail": "Challenge MFA invalide."}, status=400)
        if user.totp_enabled:
            # Application d'authentification : indépendante de la messagerie.
            # Une boîte mail compromise ne suffit plus à prendre le compte.
            refus = _verifier_totp(request, user)
            if refus is not None:
                return refus
            return Response(auth_response(user, mfa_verified=True))
        ok, reason = verify_otp(email, str(request.data.get("code", "")), OTPCode.Purpose.LOGIN_MFA)
        if not ok:
            audit(request, "mfa_failed", "user", str(user.pk), result="failure")
            return Response({"detail": reason}, status=400)
        audit(request, "login_mfa", "user", str(user.pk))
        if user.role == User.Role.ADMIN:
            # Le notaire reste protégé par la seule messagerie tant qu'il n'a
            # pas activé l'application : on le lui rappelle (une fois par jour).
            notify(user, "security", "Renforcez votre connexion",
                   "Votre second facteur repose encore sur l'e-mail : si votre messagerie était compromise, votre compte "
                   "le serait aussi. Activez l'application d'authentification dans Profil → Sécurité.",
                   severity="attention", event_key=f"totp-rappel:{timezone.localdate().isoformat()}")
        return Response(auth_response(user, mfa_verified=True))


class LogoutView(APIView):
    """Invalidate every bearer token issued to the current user."""
    def post(self, request):
        # JWT access tokens are stateless; incrementing this server-side
        # version invalidates the current token and every other open session.
        request.user.session_version += 1
        request.user.save(update_fields=["session_version"])
        audit(request, "logout_all_sessions", "user", str(request.user.pk))
        return Response({"ok": True})


class MeView(APIView):
    def get(self, request):
        u = request.user
        return Response({"id": u.id, "role": u.role, "name": u.display_name, "email": u.email, "firstName": u.first_name, "lastName": u.last_name, "phone": u.phone, "jobTitle": u.job_title, "canScan": u.can_scan,
                         "totpEnabled": u.totp_enabled, "totpEnabledAt": u.totp_enabled_at.isoformat() if u.totp_enabled_at else None,
                         "recoveryCodesRemaining": len(u.totp_recovery_hashes or []) if u.totp_enabled else 0})

    def patch(self, request):
        import re
        editable = {"firstName": ("first_name", 150), "lastName": ("last_name", 150), "phone": ("phone", 32), "jobTitle": ("job_title", 150)}
        changed: list[str] = []
        erreurs = {}
        full_name = str(request.data.get("name", "")).strip()
        if full_name:
            parts = full_name.split(maxsplit=1)
            if any(len(p) > 150 for p in parts):
                erreurs["name"] = ["Nom trop long (150 caractères maximum par partie)."]
            else:
                request.user.first_name = parts[0]
                request.user.last_name = parts[1] if len(parts) > 1 else ""
                changed.extend(["first_name", "last_name"])
        for api_name, (field_name, maximum) in editable.items():
            if api_name in request.data:
                valeur = str(request.data[api_name] or "").strip()
                # Valeurs bornées ici : au-delà de la colonne, PostgreSQL
                # refusait l'écriture et l'utilisateur recevait une erreur 500.
                if len(valeur) > maximum:
                    erreurs[api_name] = [f"{maximum} caractères maximum."]
                    continue
                if api_name == "phone" and valeur and not re.fullmatch(r"[+0-9 ().-]{6,32}", valeur):
                    erreurs[api_name] = ["Numéro de téléphone invalide."]
                    continue
                setattr(request.user, field_name, valeur)
                changed.append(field_name)
        if erreurs:
            return Response(erreurs, status=400)
        if changed:
            request.user.save(update_fields=list(dict.fromkeys(changed)))
            audit(request, "profile_updated", "user", str(request.user.pk))
        return Response({"ok": True, "name": request.user.display_name, "email": request.user.email, "role": request.user.role})


class ChangePasswordView(APIView):
    """POST /api/me/password { currentPassword, newPassword }

    Changement volontaire du mot de passe par son titulaire : l'ancien est
    exigé, la politique de l'étude s'applique, et les AUTRES sessions tombent
    (la session courante reçoit un jeton neuf)."""

    def post(self, request):
        user = request.user
        if not user.has_usable_password():
            return Response({"detail": "Ce compte se connecte avec Google : il n'a pas de mot de passe à modifier."}, status=400)
        if not user.check_password(str(request.data.get("currentPassword", ""))):
            audit(request, "password_change_refused", "user", str(user.pk), result="failure")
            return Response({"currentPassword": ["Mot de passe actuel incorrect."]}, status=400)
        serializer = PasswordSerializer(data={"password": request.data.get("newPassword", "")})
        if not serializer.is_valid():
            return Response({"newPassword": serializer.errors.get("password", ["Mot de passe invalide."])}, status=400)
        if user.check_password(serializer.validated_data["password"]):
            return Response({"newPassword": ["Le nouveau mot de passe doit être différent de l'actuel."]}, status=400)
        mfa = user.session_mfa_verified
        user.set_password(serializer.validated_data["password"])
        user.session_version += 1
        user.save(update_fields=["password", "session_version"])
        audit(request, "password_changed", "user", str(user.pk))
        notify(user, "security", "Mot de passe modifié",
               "Votre mot de passe a été modifié et vos autres sessions ont été fermées. "
               "Si vous n'êtes pas à l'origine de ce changement, prévenez immédiatement le notaire.",
               severity="haute", email=True)
        return Response(auth_response(user, mfa_verified=mfa))


class RegisterStartView(APIView):
    """Begin activation of an account invited by an authorised notaire."""
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = RegisterStartSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        email = data["email"].lower()
        if User.objects.filter(email__iexact=email).exists():
            return Response({"email": ["Un compte existe déjà pour cette adresse."]}, status=400)
        invite = InviteCode.objects.filter(code=data["inviteCode"]).first()
        if not invite or not invite.is_valid_for(email, data["role"]):
            audit(request, "invitation_rejected", "email", email, result="failure")
            return Response({"inviteCode": ["Invitation invalide, expirée ou non attribuée à cette adresse." ]}, status=400)
        PendingRegistration.objects.update_or_create(email=email, defaults={
            "role": data["role"], "first_name": data["firstName"], "last_name": data["lastName"],
            "phone": data.get("phone", ""), "job_title": data.get("jobTitle", ""),
            "invite": invite, "invite_checked_at": timezone.now(), "verified_at": None,
        })
        audit(request, "invitation_activation_started", "invitation", str(invite.pk))
        return Response({"ok": True})


class CheckInviteView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        email = str(request.data.get("email", "")).lower()
        role = request.data.get("role")
        invite = InviteCode.objects.filter(code=str(request.data.get("inviteCode", ""))).first()
        if not invite or not invite.is_valid_for(email, role):
            return Response({"valid": False}, status=400)
        return Response({"valid": True, "role": invite.role})


class SendRegisterCodeView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        email = str(request.data.get("email", "")).lower()
        if not PendingRegistration.objects.filter(email__iexact=email).exists():
            return Response({"email": ["Inscription non initialisée."]}, status=400)
        return send_otp(request, email, OTPCode.Purpose.REGISTER)


def send_otp(request, email: str, purpose: str) -> Response:
    existing = latest_otp(email, purpose)
    if existing and existing.created_at > timezone.now() - timedelta(seconds=45):
        return Response({"detail": "Veuillez patienter avant de demander un nouveau code."}, status=429)
    code = f"{secrets.randbelow(1_000_000):06d}"
    OTPCode.objects.filter(email__iexact=email, purpose=purpose, used_at__isnull=True).update(used_at=timezone.now())
    OTPCode.objects.create(email=email, purpose=purpose, code_hash=make_password(code), expires_at=timezone.now() + timedelta(seconds=OTP_TTL_SECONDS))
    send_mail("Votre code GED", f"Votre code de vérification est : {code}. Il expire dans 10 minutes.", settings.DEFAULT_FROM_EMAIL, [email], fail_silently=False)
    audit(request, f"otp_sent_{purpose}", "email", email)
    return Response({"expiresInSeconds": OTP_TTL_SECONDS})


def verify_otp(email: str, code: str, purpose: str) -> tuple[bool, str]:
    otp = latest_otp(email, purpose)
    if not otp or otp.expires_at <= timezone.now():
        return False, "Code expiré ou introuvable."
    if otp.attempts >= OTP_MAX_ATTEMPTS:
        return False, "Nombre maximal d'essais dépassé."
    otp.attempts += 1
    otp.save(update_fields=["attempts"])
    if not check_password(code, otp.code_hash):
        return False, "Code invalide."
    otp.used_at = timezone.now()
    otp.save(update_fields=["used_at"])
    return True, ""


class VerifyRegisterCodeView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        email = str(request.data.get("email", "")).lower()
        ok, reason = verify_otp(email, str(request.data.get("code", "")), OTPCode.Purpose.REGISTER)
        if not ok:
            audit(request, "otp_register_failed", result="failure")
            return Response({"detail": reason}, status=400)
        PendingRegistration.objects.filter(email__iexact=email).update(verified_at=timezone.now())
        return Response({"ok": True})


class RegisterCompleteView(APIView):
    permission_classes = [permissions.AllowAny]

    @transaction.atomic
    def post(self, request):
        serializer = PasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = str(request.data.get("email", "")).lower()
        pending = PendingRegistration.objects.select_for_update().filter(email__iexact=email).first()
        if not pending or not pending.verified_at or not pending.invite.is_valid_for(email, pending.role):
            return Response({"detail": "Inscription non vérifiée. Recommencez la vérification par e-mail."}, status=400)
        user = User.objects.create_user(email=email, password=serializer.validated_data["password"], first_name=pending.first_name, last_name=pending.last_name, phone=pending.phone, job_title=pending.job_title, role=pending.role)
        pending.invite.used_at = timezone.now()
        pending.invite.save(update_fields=["used_at"])
        pending.delete()
        audit(request, "account_created", "user", str(user.pk))
        notify_account_created(user)
        return Response(auth_response(user), status=201)


class ForgotSendCodeView(APIView):
    permission_classes = [permissions.AllowAny]
    def post(self, request):
        email = str(request.data.get("email", "")).lower()
        # Avoid enumerating accounts; successful response is deliberately uniform.
        if User.objects.filter(email__iexact=email, is_active=True).exists():
            return send_otp(request, email, OTPCode.Purpose.FORGOT_PASSWORD)
        return Response({"expiresInSeconds": OTP_TTL_SECONDS})


class ForgotVerifyCodeView(APIView):
    permission_classes = [permissions.AllowAny]
    def post(self, request):
        email = str(request.data.get("email", "")).lower()
        ok, reason = verify_otp(email, str(request.data.get("code", "")), OTPCode.Purpose.FORGOT_PASSWORD)
        if not ok:
            return Response({"detail": reason}, status=400)
        user = User.objects.filter(email__iexact=email, is_active=True).first()
        if user is None:
            return Response({"detail": "Code expiré ou introuvable."}, status=400)
        return Response({"resetToken": signing.dumps({"email": email, "etat": _etat_compte(user)}, salt="password-reset")})


def _etat_compte(user: User) -> str:
    """Empreinte courte de l'état d'authentification du compte : change dès
    que le mot de passe ou la version de session change."""
    import hashlib
    return hashlib.sha256(f"{user.pk}:{user.password}:{user.session_version}".encode()).hexdigest()[:32]


class ForgotResetView(APIView):
    permission_classes = [permissions.AllowAny]
    def post(self, request):
        serializer = PasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            payload = signing.loads(request.data.get("resetToken", ""), salt="password-reset", max_age=900)
            user = User.objects.get(email__iexact=payload["email"], is_active=True)
        except Exception:
            return Response({"detail": "Jeton de réinitialisation invalide ou expiré."}, status=400)
        # Usage unique : le jeton est lié à l'état du compte (empreinte du mot
        # de passe et version de session) au moment où il a été émis. Dès que
        # le mot de passe change, il ne vaut plus rien — y compris s'il a été
        # intercepté. Les jetons émis avant ce correctif n'ont pas d'empreinte :
        # ils sont refusés.
        if payload.get("etat") != _etat_compte(user):
            return Response({"detail": "Ce lien de réinitialisation a déjà été utilisé ou n'est plus valable."}, status=400)
        user.set_password(serializer.validated_data["password"])
        # Toutes les sessions ouvertes tombent : un intrus qui détenait un
        # jeton (c'est souvent la raison même de la réinitialisation) le perd.
        user.session_version += 1
        user.session_mfa_verified = False
        user.failed_login_attempts = 0
        user.locked_until = None
        user.save(update_fields=["password", "session_version", "session_mfa_verified", "failed_login_attempts", "locked_until"])
        audit(request, "password_reset", "user", str(user.pk))
        notify(user, "security", "Mot de passe modifié",
               "Votre mot de passe vient d'être réinitialisé et toutes vos sessions ont été fermées. "
               "Si vous n'êtes pas à l'origine de cette opération, prévenez immédiatement le notaire.",
               severity="haute", email=True)
        return Response({"ok": True})


class OAuthStubView(APIView):
    """Fallback for any social-login provider we don't actually wire up.
    Google has its own real implementation below; nothing currently routes
    here for "google", only for hypothetical future/unknown providers."""
    permission_classes = [permissions.AllowAny]
    def get(self, request, provider):
        return Response({"detail": f"OAuth {provider} n'est pas configuré."}, status=status.HTTP_501_NOT_IMPLEMENTED)


# =============================================================================
# Google Sign-In — identity verification for already authorised accounts.
#
# Flow: GoogleOAuthStartView opens consent; the callback verifies the identity
# and accepts only an already-active account. GoogleOAuthConsumeView exchanges
# the short-lived ticket without putting a JWT in browser history.
# =============================================================================

GOOGLE_STATE_SALT = "google-oauth-state"
GOOGLE_STATE_TTL = 600            # 10 min to complete the Google consent screen
GOOGLE_PROFILE_SALT = "google-oauth-profile"
GOOGLE_PROFILE_TTL = 600          # 10 min to pick clerc/collaborateur afterwards
GOOGLE_SESSION_SALT = "google-oauth-session"
GOOGLE_SESSION_TTL = 120          # 2 min for the browser to hit /consume


def _google_configured() -> bool:
    return bool(settings.GOOGLE_OAUTH_CLIENT_ID and settings.GOOGLE_OAUTH_CLIENT_SECRET)


def _landing(**params) -> HttpResponseRedirect:
    query = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
    url = settings.FRONTEND_OAUTH_LANDING_PATH + (f"?{query}" if query else "")
    return HttpResponseRedirect(url)


def _session_ticket_for(user: User) -> str:
    return signing.dumps({"user_id": user.pk}, salt=GOOGLE_SESSION_SALT)


def _link_or_touch(user: User, sub: str) -> None:
    """Attach a verified Google identity to an existing (classic or
    already-linked) account instead of ever creating a second row for the
    same e-mail address."""
    changed = []
    if user.google_sub != sub:
        user.google_sub = sub
        changed.append("google_sub")
    user.last_login = timezone.now()
    changed.append("last_login")
    user.save(update_fields=changed)


class GoogleOAuthStartView(APIView):
    """GET /api/auth/oauth/google/start[?role=clerc|collaborateur]"""
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        if not _google_configured():
            return Response({"detail": "La connexion Google n'est pas encore configurée pour cette étude. Contactez votre administrateur."}, status=503)
        # Google is an identity provider, not a source of authorization.  It
        # can authenticate an account already created by invitation only.
        state = signing.dumps({"nonce": secrets.token_urlsafe(12)}, salt=GOOGLE_STATE_SALT)
        url = build_authorization_url(client_id=settings.GOOGLE_OAUTH_CLIENT_ID, redirect_uri=settings.GOOGLE_OAUTH_REDIRECT_URI, state=state)
        return HttpResponseRedirect(url)


class GoogleOAuthCallbackView(APIView):
    """GET /auth/oauth/google/callback — deliberately mounted outside /api/
    so it matches, byte for byte, the redirect URI registered in Google
    Cloud Console."""
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        error = request.query_params.get("error")
        if error:
            audit(request, "login_google_denied", result="failure")
            return _landing(oauthError="access_denied")
        code = request.query_params.get("code")
        state_raw = request.query_params.get("state", "")
        if not code:
            return _landing(oauthError="missing_code")
        try:
            state = signing.loads(state_raw, salt=GOOGLE_STATE_SALT, max_age=GOOGLE_STATE_TTL)
        except signing.BadSignature:
            return _landing(oauthError="invalid_state")

        try:
            identity = exchange_code_for_identity(
                code=code,
                client_id=settings.GOOGLE_OAUTH_CLIENT_ID,
                client_secret=settings.GOOGLE_OAUTH_CLIENT_SECRET,
                redirect_uri=settings.GOOGLE_OAUTH_REDIRECT_URI,
            )
        except GoogleOAuthError:
            audit(request, "login_google_failed", result="failure")
            return _landing(oauthError="google_exchange_failed")

        if not identity.email_verified:
            audit(request, "login_google_unverified_email", result="failure")
            return _landing(oauthError="email_not_verified")

        existing = User.objects.filter(Q(google_sub=identity.sub) | Q(email__iexact=identity.email)).first()
        if existing is not None:
            if not existing.is_active:
                audit(request, "login_google_inactive", "user", str(existing.pk), result="failure")
                return _landing(oauthError="account_disabled")
            was_classic = not existing.is_google_linked
            _link_or_touch(existing, identity.sub)
            audit(request, "account_linked_google" if was_classic else "login_google", "user", str(existing.pk))
            return _landing(oauthTicket=_session_ticket_for(existing))

        audit(request, "login_google_uninvited", "email", identity.email, result="failure")
        return _landing(oauthError="account_not_authorized")


class GoogleOAuthCompleteView(APIView):
    """POST /api/auth/oauth/google/complete { googleTicket, role } — final
    step of a first-time Google sign-in once the applicant has chosen
    clerc or collaborateur. No password, no OTP: Google already vouched
    for the e-mail address."""
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        try:
            profile = signing.loads(str(request.data.get("googleTicket", "")), salt=GOOGLE_PROFILE_SALT, max_age=GOOGLE_PROFILE_TTL)
        except signing.BadSignature:
            return Response({"detail": "Lien Google expiré ou invalide. Recommencez la connexion avec Google."}, status=400)

        return Response({"detail": "La création de compte via Google est désactivée. Demandez une invitation à l'étude."}, status=403)


class GoogleOAuthConsumeView(APIView):
    """POST /api/auth/oauth/google/consume { ticket } — exchanges the
    short-lived, signed ticket the callback put in the redirect URL for an
    actual session, without ever exposing the JWT itself in the address
    bar or browser history."""
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        try:
            payload = signing.loads(str(request.data.get("ticket", "")), salt=GOOGLE_SESSION_SALT, max_age=GOOGLE_SESSION_TTL)
        except signing.BadSignature:
            return Response({"detail": "Session Google expirée. Merci de recommencer la connexion."}, status=400)
        user = User.objects.filter(pk=payload.get("user_id"), is_active=True).first()
        if user is None:
            return Response({"detail": "Compte introuvable ou désactivé."}, status=404)
        # Google atteste l'adresse e-mail ; il n'atteste pas la possession du
        # second facteur exigé par la politique de l'étude. Sans ce contrôle,
        # quiconque contrôle le compte Google du notaire obtenait une session
        # notaire complète — le chemin Google devenait le maillon faible.
        if exige_second_facteur(user):
            audit(request, "login_google_mfa_required", "user", str(user.pk))
            return defi_second_facteur(request, user)
        audit(request, "login_google", "user", str(user.pk))
        return Response(auth_response(user))


class UserCreateView(APIView):
    """Admin-only user directory and invitation issue/revocation."""
    def get(self, request):
        if request.user.role != User.Role.ADMIN:
            return Response({"detail": "Liste des utilisateurs réservée au notaire."}, status=403)
        return Response([
            {
                "id": u.id,
                "name": u.display_name,
                "email": u.email,
                "role": u.role,
                "roleLabel": u.get_role_display(),
                "isActive": u.is_active,
                "canScan": u.can_scan,
                "deactivatedAt": u.deactivated_at.isoformat() if u.deactivated_at else None,
                "departureReason": u.departure_reason,
            }
            for u in User.objects.all().order_by("first_name", "last_name")
        ])

    def post(self, request):
        if request.user.role != User.Role.ADMIN:
            return Response({"detail": "Création de compte réservée au notaire."}, status=403)
        email = str(request.data.get("email", "")).lower()
        role = request.data.get("role")
        if not email or role not in set(User.Role.values):
            return Response({"detail": "Email et rôle valides requis."}, status=400)
        if User.objects.filter(email__iexact=email).exists():
            return Response({"email": ["Un compte existe déjà."]}, status=400)
        invite = InviteCode.objects.create(
            code=secrets.token_urlsafe(32), email=email, role=role,
            expires_at=timezone.now() + timedelta(days=7), created_by=request.user,
        )
        send_mail("Invitation GED", f"Votre code d'activation GED est : {invite.code}. Il expire dans 7 jours.", settings.DEFAULT_FROM_EMAIL, [email], fail_silently=False)
        audit(request, "invitation_created", "invitation", str(invite.pk))
        return Response({"ok": True, "id": invite.id, "email": invite.email, "role": invite.role, "expiresAt": invite.expires_at.isoformat()}, status=201)

    def patch(self, request):
        if request.user.role != User.Role.ADMIN:
            return Response({"detail": "Gestion des comptes réservée au notaire."}, status=403)
        user = User.objects.filter(pk=request.data.get("id")).first()
        if not user:
            return Response({"detail": "Utilisateur introuvable."}, status=404)
        fields = []
        if "isActive" in request.data:
            actif_demande = bool(request.data["isActive"])
            if not actif_demande:
                # Suspendre son propre compte, c'est se fermer la porte au nez :
                # même garde-fou que pour le changement de rôle.
                if user.pk == request.user.pk:
                    return Response({"detail": "Vous ne pouvez pas suspendre votre propre accès. Demandez-le à un autre notaire."}, status=409)
                # Sans notaire actif, plus personne ne valide un acte, ne gère
                # les comptes ni n'accède aux clés : l'étude est bloquée. Le
                # garde-fou existait pour le changement de rôle, pas ici.
                if user.role == User.Role.ADMIN and user.is_active and not User.objects.filter(
                    role=User.Role.ADMIN, is_active=True
                ).exclude(pk=user.pk).exists():
                    return Response({"detail": "Ce compte est le dernier notaire actif : son accès ne peut pas être suspendu. Désignez d'abord un autre notaire."}, status=409)
            user.is_active = actif_demande
            fields.append("is_active")
            if not user.is_active:
                user.deactivated_at = timezone.now(); user.deactivated_by = request.user; user.departure_reason = str(request.data.get("departureReason", request.data.get("reason", "")))[:2000]
                fields.extend(["deactivated_at", "deactivated_by", "departure_reason"])
            else:
                user.deactivated_at = None; user.deactivated_by = None; user.departure_reason = ""
                fields.extend(["deactivated_at", "deactivated_by", "departure_reason"])
        if request.data.get("revokeSessions") or "isActive" in request.data:
            user.session_version += 1
            fields.append("session_version")
        # --- Changement de rôle -------------------------------------------
        # Un rôle commande le portail d'atterrissage, la politique de second
        # facteur et chaque contrôle d'habilitation côté serveur. Le modifier
        # est une modification de privilèges : elle se justifie, se trace avec
        # l'ancienne et la nouvelle valeur, et révoque les sessions en cours.
        role_precedent = None
        if "role" in request.data:
            nouveau_role = str(request.data.get("role", ""))
            if nouveau_role not in set(User.Role.values):
                return Response({"role": ["Rôle inconnu."]}, status=400)
            if user.pk == request.user.pk:
                return Response({"detail": "Vous ne pouvez pas modifier votre propre rôle. Demandez-le à un autre notaire."}, status=409)
            motif = str(request.data.get("motif", "")).strip()
            if not motif:
                return Response({"motif": ["Le motif du changement de rôle est obligatoire."]}, status=400)
            if nouveau_role != user.role:
                # Filet de sécurité : l'étude doit toujours conserver un
                # notaire actif, sans quoi plus personne ne peut valider un
                # acte, gérer les comptes ni accéder aux clés.
                # En l'état, la garde au-dessus (interdiction de modifier son
                # propre rôle) rend ce cas inatteignable — le demandeur est
                # lui-même un notaire actif. On le conserve pour le jour où
                # une autre voie ouvrirait le changement de rôle.
                if user.role == User.Role.ADMIN and not User.objects.filter(
                    role=User.Role.ADMIN, is_active=True
                ).exclude(pk=user.pk).exists():
                    return Response({"detail": "Ce compte est le dernier notaire actif : son rôle ne peut pas être retiré. Désignez d'abord un autre notaire."}, status=409)
                role_precedent = user.role
                user.role = nouveau_role
                fields.append("role")
                # `can_scan` ne concerne que les collaborateurs : le laisser
                # armé le ferait resurgir en silence lors d'un retour à ce rôle.
                if nouveau_role != User.Role.COLLABORATEUR and user.can_scan:
                    user.can_scan = False
                    fields.append("can_scan")
                # Les sessions ouvertes portaient les anciens privilèges.
                if "session_version" not in fields:
                    user.session_version += 1
                    fields.append("session_version")
                # Le second facteur est réévalué à la reconnexion (LoginView) :
                # on repart donc d'une session non couverte.
                user.session_mfa_verified = False
                fields.append("session_mfa_verified")

        if "canScan" in request.data:
            # Autorisation de numérisation : réservée aux collaborateurs, le
            # notaire et le clerc numérisent déjà de par leur rôle.
            if user.role != User.Role.COLLABORATEUR:
                return Response({"canScan": ["Cette autorisation ne concerne que les collaborateurs."]}, status=400)
            user.can_scan = bool(request.data["canScan"])
            fields.append("can_scan")
        if not fields:
            return Response({"detail": "Aucune modification demandée."}, status=400)
        user.save(update_fields=fields)
        if role_precedent is not None:
            # Le cahier des charges demande explicitement ancienne et nouvelle
            # valeur pour ce type d'événement.
            from audit.services import log_event
            log_event(
                request, "user_role_changed", "user", str(user.pk),
                metadata={
                    "previous": role_precedent,
                    "next": user.role,
                    "motif": str(request.data.get("motif", "")).strip()[:2000],
                    "sessions_revoquees": True,
                },
            )
            notify(
                user,
                "role_changed",
                "Votre rôle a changé",
                f"{request.user.display_name} a modifié votre rôle : « {dict(User.Role.choices).get(role_precedent, role_precedent)} » → "
                f"« {user.get_role_display()} ». Reconnectez-vous pour accéder à votre nouvel espace.",
            )
        if "can_scan" in fields and role_precedent is None:
            audit(request, "user_scan_authorized" if user.can_scan else "user_scan_revoked", "user", str(user.pk))
            notify(
                user,
                "permission_granted" if user.can_scan else "permission_revoked",
                "Numérisation autorisée" if user.can_scan else "Numérisation retirée",
                (
                    f"{request.user.display_name} vous a autorisé à numériser des documents."
                    if user.can_scan
                    else f"{request.user.display_name} a retiré votre autorisation de numériser des documents."
                ),
            )
        elif role_precedent is None:
            audit(request, "user_revoked" if not user.is_active else "sessions_revoked", "user", str(user.pk))
        return Response({"ok": True, "id": user.id, "isActive": user.is_active, "canScan": user.can_scan, "role": user.role, "roleLabel": user.get_role_display()})
