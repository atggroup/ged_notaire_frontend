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


def auth_response(user: User) -> dict[str, str]:
    token = AccessToken.for_user(user)
    token["sv"] = user.session_version
    return {"token": str(token), "role": user.role, "name": user.display_name, "email": user.email}


def latest_otp(email: str, purpose: str) -> OTPCode | None:
    return OTPCode.objects.filter(email__iexact=email, purpose=purpose, used_at__isnull=True).order_by("-created_at").first()


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
            audit(request, "login_failed", result="failure")
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        user = serializer.validated_data["user"]
        user.failed_login_attempts = 0
        user.locked_until = None
        user.last_login = timezone.now()
        user.save(update_fields=["last_login", "failed_login_attempts", "locked_until"])
        from permissions_app.models import Permission
        protected_access = Permission.objects.filter(user=user).filter(
            Q(document__niveau_de_confidentialite__in=["Confidentiel", "Très confidentiel"]) |
            Q(dossier__niveau_de_confidentialite__in=["Confidentiel", "Très confidentiel"])
        ).exists() or user.dossier_assignments.filter(dossier__niveau_de_confidentialite__in=["Confidentiel", "Très confidentiel"]).exists()
        if user.role == User.Role.ADMIN or protected_access:
            # A password alone is never sufficient for a notaire/admin or an
            # account entrusted with confidential documents.
            send_otp(request, user.email, OTPCode.Purpose.LOGIN_MFA)
            audit(request, "mfa_challenge_sent", "user", str(user.pk))
            return Response({"mfaRequired": True, "email": user.email, "expiresInSeconds": OTP_TTL_SECONDS}, status=202)
        audit(request, "login", "user", str(user.pk))
        return Response(auth_response(user))


class MFAVerifyView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        email = str(request.data.get("email", "")).lower()
        user = User.objects.filter(email__iexact=email, is_active=True).first()
        if not user:
            return Response({"detail": "Challenge MFA invalide."}, status=400)
        ok, reason = verify_otp(email, str(request.data.get("code", "")), OTPCode.Purpose.LOGIN_MFA)
        if not ok:
            audit(request, "mfa_failed", "user", str(user.pk), result="failure")
            return Response({"detail": reason}, status=400)
        audit(request, "login_mfa", "user", str(user.pk))
        return Response(auth_response(user))


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
        return Response({"id": u.id, "role": u.role, "name": u.display_name, "email": u.email, "firstName": u.first_name, "lastName": u.last_name, "phone": u.phone, "jobTitle": u.job_title, "canScan": u.can_scan})

    def patch(self, request):
        editable = {"firstName": "first_name", "lastName": "last_name", "phone": "phone", "jobTitle": "job_title"}
        changed: list[str] = []
        full_name = str(request.data.get("name", "")).strip()
        if full_name:
            parts = full_name.split(maxsplit=1)
            request.user.first_name = parts[0]
            request.user.last_name = parts[1] if len(parts) > 1 else ""
            changed.extend(["first_name", "last_name"])
        for api_name, field_name in editable.items():
            if api_name in request.data:
                setattr(request.user, field_name, request.data[api_name])
                changed.append(field_name)
        if changed:
            request.user.save(update_fields=list(dict.fromkeys(changed)))
            audit(request, "profile_updated", "user", str(request.user.pk))
        return Response({"ok": True, "name": request.user.display_name, "email": request.user.email, "role": request.user.role})


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
        return Response({"resetToken": signing.dumps({"email": email}, salt="password-reset")})


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
        user.set_password(serializer.validated_data["password"])
        user.save(update_fields=["password"])
        audit(request, "password_reset", "user", str(user.pk))
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
            user.is_active = bool(request.data["isActive"])
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
        if "can_scan" in fields:
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
        else:
            audit(request, "user_revoked" if not user.is_active else "sessions_revoked", "user", str(user.pk))
        return Response({"ok": True, "id": user.id, "isActive": user.is_active, "canScan": user.can_scan})
