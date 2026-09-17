"""Google Sign-In: first-time account creation, role disambiguation and
account linking (no duplicate accounts for an e-mail that already has a
classic password)."""
import base64
from unittest.mock import patch

import pytest
from django.core import signing
from django.test import override_settings
from rest_framework.test import APIClient

from accounts.google_oauth import GoogleIdentity
from accounts.models import User

KEY = base64.urlsafe_b64encode(b"0123456789abcdef0123456789abcdef").decode()

GOOGLE_SETTINGS = dict(
    DOCUMENT_ENCRYPTION_KEY=KEY,
    GOOGLE_OAUTH_CLIENT_ID="test-client-id.apps.googleusercontent.com",
    GOOGLE_OAUTH_CLIENT_SECRET="test-secret",
    GOOGLE_OAUTH_REDIRECT_URI="http://testserver/auth/oauth/google/callback",
    FRONTEND_OAUTH_LANDING_PATH="/login.html",
)


def _identity(email="awa@test.ci", sub="google-sub-1", verified=True):
    return GoogleIdentity(sub=sub, email=email, email_verified=verified, given_name="Awa", family_name="Koné")


@pytest.mark.django_db
@override_settings(**GOOGLE_SETTINGS)
def test_start_redirects_to_google_and_signs_state():
    response = APIClient().get("/api/auth/oauth/google/start?role=clerc")
    assert response.status_code == 302
    assert response["Location"].startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "state=" in response["Location"]


@pytest.mark.django_db
@override_settings(**GOOGLE_SETTINGS)
def test_first_time_google_login_without_invitation_is_rejected():
    state = signing.dumps({"role": None, "nonce": "n"}, salt="google-oauth-state")
    with patch("accounts.views.exchange_code_for_identity", return_value=_identity()):
        response = APIClient().get("/auth/oauth/google/callback", {"code": "abc", "state": state})
    assert response.status_code == 302
    assert "oauthError=account_not_authorized" in response["Location"]
    assert User.objects.filter(email="awa@test.ci").exists() is False


@pytest.mark.django_db
@override_settings(**GOOGLE_SETTINGS)
def test_callback_never_creates_account_from_google_identity_only():
    state = signing.dumps({"role": "collaborateur", "nonce": "n"}, salt="google-oauth-state")
    with patch("accounts.views.exchange_code_for_identity", return_value=_identity(email="new.collab@test.ci", sub="sub-2")):
        response = APIClient().get("/auth/oauth/google/callback", {"code": "abc", "state": state})
    assert response.status_code == 302
    assert "oauthError=account_not_authorized" in response["Location"]
    assert not User.objects.filter(email="new.collab@test.ci").exists()


@pytest.mark.django_db
@override_settings(**GOOGLE_SETTINGS)
def test_google_completion_cannot_create_an_account():
    profile_ticket = signing.dumps({"sub": "sub-3", "email": "x@test.ci", "given_name": "X", "family_name": "Y"}, salt="google-oauth-profile")
    response = APIClient().post("/api/auth/oauth/google/complete", {"googleTicket": profile_ticket, "role": "admin"}, format="json")
    assert response.status_code == 403
    assert User.objects.filter(email="x@test.ci").exists() is False


@pytest.mark.django_db
@override_settings(**GOOGLE_SETTINGS)
def test_google_completion_cannot_create_collaborateur():
    profile_ticket = signing.dumps({"sub": "sub-4", "email": "b@test.ci", "given_name": "B", "family_name": "K"}, salt="google-oauth-profile")
    response = APIClient().post("/api/auth/oauth/google/complete", {"googleTicket": profile_ticket, "role": "collaborateur"}, format="json")
    assert response.status_code == 403
    assert not User.objects.filter(email="b@test.ci").exists()


@pytest.mark.django_db
@override_settings(**GOOGLE_SETTINGS)
def test_existing_classic_account_is_linked_not_duplicated():
    """The core anti-duplicate requirement: an e-mail that already has a
    classic (password) account must log the person into THAT account via
    Google, never create a second row."""
    existing = User.objects.create_user(email="notaire@test.ci", password="Secret123", role="admin", first_name="Me", last_name="Konan")
    state = signing.dumps({"role": None, "nonce": "n"}, salt="google-oauth-state")
    with patch("accounts.views.exchange_code_for_identity", return_value=_identity(email="notaire@test.ci", sub="sub-5")):
        response = APIClient().get("/auth/oauth/google/callback", {"code": "abc", "state": state})
    assert response.status_code == 302
    assert User.objects.filter(email="notaire@test.ci").count() == 1
    existing.refresh_from_db()
    assert existing.google_sub == "sub-5"
    assert existing.role == "admin"  # role from the pre-existing account is preserved, never overwritten
    assert existing.has_usable_password() is True  # classic password login still works


@pytest.mark.django_db
@override_settings(**GOOGLE_SETTINGS)
def test_ticket_consume_returns_login_payload():
    user = User.objects.create_google_user(email="c@test.ci", google_sub="sub-6", role="collaborateur", first_name="C", last_name="D")
    ticket = signing.dumps({"user_id": user.pk}, salt="google-oauth-session")
    response = APIClient().post("/api/auth/oauth/google/consume", {"ticket": ticket}, format="json")
    assert response.status_code == 200
    assert response.data["email"] == "c@test.ci"
    assert response.data["role"] == "collaborateur"


@pytest.mark.django_db
@override_settings(**GOOGLE_SETTINGS)
def test_unverified_google_email_is_rejected():
    state = signing.dumps({"role": "collaborateur", "nonce": "n"}, salt="google-oauth-state")
    with patch("accounts.views.exchange_code_for_identity", return_value=_identity(email="unverified@test.ci", sub="sub-7", verified=False)):
        response = APIClient().get("/auth/oauth/google/callback", {"code": "abc", "state": state})
    assert response.status_code == 302
    assert "oauthError=email_not_verified" in response["Location"]
    assert User.objects.filter(email="unverified@test.ci").exists() is False
