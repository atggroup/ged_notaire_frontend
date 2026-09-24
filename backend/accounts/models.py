"""Identity and one-time-code models."""
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.utils import timezone


class UserManager(BaseUserManager):
    def create_user(self, email: str, password: str | None = None, **extra_fields):
        if not email:
            raise ValueError("An email address is required")
        user = self.model(email=self.normalize_email(email), username=self.normalize_email(email), **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email: str, password: str, **extra_fields):
        extra_fields.setdefault("role", User.Role.ADMIN)
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        return self.create_user(email, password, **extra_fields)

    def create_google_user(self, *, email: str, google_sub: str, role: str, first_name: str = "", last_name: str = "", **extra_fields):
        """First-time Google Sign-In: the account has no classic password at
        all (``set_unusable_password``) — it can only ever be reached again
        through a verified Google identity, never through the e-mail/
        password form."""
        email = self.normalize_email(email)
        user = self.model(email=email, username=email, role=role, first_name=first_name, last_name=last_name, google_sub=google_sub, **extra_fields)
        user.set_unusable_password()
        user.save(using=self._db)
        return user


class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = "admin", "Notaire · Admin"
        CLERC = "clerc", "Clerc principal"
        COLLABORATEUR = "collaborateur", "Collaborateur"

    username = models.CharField(max_length=254, unique=True)
    email = models.EmailField(unique=True)
    role = models.CharField(max_length=20, choices=Role.choices)
    phone = models.CharField(max_length=32, blank=True)
    job_title = models.CharField(max_length=150, blank=True)
    active_filiale = models.CharField(max_length=150, blank=True)
    # Google Sign-In identity link. Set the first time the account is created
    # or logged into via Google; lets us recognise a returning Google user
    # even if they later change their display name, and lets a pre-existing
    # classic (password) account be linked to Google without ever creating
    # a duplicate user row for the same e-mail address.
    google_sub = models.CharField(max_length=255, blank=True, null=True, unique=True)
    # Incremented whenever an administrator revokes sessions.  It is embedded
    # in each access token so a previously issued token stops working at once.
    session_version = models.PositiveIntegerField(default=1)
    # Le second facteur est exigé selon le RISQUE (notaire, ou détenteur d'un
    # accès confidentiel) et cette décision est prise à la connexion. Retenir
    # ici si la session courante a franchi un second facteur permet de la
    # révoquer le jour où une habilitation confidentielle lui est accordée —
    # sans quoi un jeton obtenu par simple mot de passe ouvrirait, jusqu'à 8 h
    # durant, des pièces que la politique réserve aux sessions MFA.
    session_mfa_verified = models.BooleanField(default=False)
    failed_login_attempts = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    # Autorisation explicite, accordée par le notaire à un collaborateur précis,
    # pour numériser des documents. Sans effet pour les autres rôles (le
    # notaire et le clerc peuvent déjà numériser de par leur rôle).
    can_scan = models.BooleanField(default=False)
    # Second facteur par application d'authentification (TOTP). Secret
    # chiffré ; `totp_last_counter` interdit le rejeu d'un code déjà accepté ;
    # codes de secours conservés sous forme d'empreintes, à usage unique.
    totp_secret = models.TextField(blank=True)
    totp_pending_secret = models.TextField(blank=True)
    totp_enabled_at = models.DateTimeField(null=True, blank=True)
    totp_last_counter = models.BigIntegerField(null=True, blank=True)
    totp_recovery_hashes = models.JSONField(default=list, blank=True)
    deactivated_at = models.DateTimeField(null=True, blank=True)
    deactivated_by = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="deactivated_users")
    departure_reason = models.TextField(blank=True)
    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []
    objects = UserManager()

    @property
    def display_name(self) -> str:
        return (f"{self.first_name} {self.last_name}").strip() or self.email

    @property
    def is_google_linked(self) -> bool:
        return bool(self.google_sub)

    @property
    def totp_enabled(self) -> bool:
        return bool(self.totp_enabled_at and self.totp_secret)


class InviteCode(models.Model):
    """A named, single-use account activation authorization.

    An invitation belongs to an administrator, an e-mail address and a role;
    it is not a generic code that can be passed around.
    """
    code = models.CharField(max_length=96, unique=True)
    email = models.EmailField()
    role = models.CharField(max_length=20, choices=User.Role.choices)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name="created_invites")

    def is_valid_for(self, email: str, role: str) -> bool:
        return (
            self.email.lower() == email.lower() and self.role == role and
            not self.revoked_at and not self.used_at and self.expires_at > timezone.now()
        )


class PendingRegistration(models.Model):
    """An invited account in the short activation process."""
    email = models.EmailField(unique=True)
    role = models.CharField(max_length=20, choices=User.Role.choices)
    first_name = models.CharField(max_length=150)
    last_name = models.CharField(max_length=150)
    phone = models.CharField(max_length=32, blank=True)
    job_title = models.CharField(max_length=150, blank=True)
    invite = models.ForeignKey(InviteCode, on_delete=models.PROTECT)
    invite_checked_at = models.DateTimeField(null=True, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class OTPCode(models.Model):
    class Purpose(models.TextChoices):
        REGISTER = "register", "Register"
        FORGOT_PASSWORD = "forgot_password", "Forgot password"
        LOGIN_MFA = "login_mfa", "Login MFA"

    email = models.EmailField()
    code_hash = models.CharField(max_length=256)
    purpose = models.CharField(max_length=32, choices=Purpose.choices)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["email", "purpose", "created_at"])]
