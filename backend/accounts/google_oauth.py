"""Google Sign-In (OpenID Connect, authorization-code flow) support.

Verifies Google-issued ID tokens against Google's published JSON Web Key
Set ourselves, using only the standard library, ``cryptography`` (already
a project dependency for document encryption) and Django's cache
framework — no extra "google-auth" SDK to install, which matters for a
small office that will deploy this with a plain ``pip install -r
requirements.txt``.
"""
from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from django.core.cache import cache

GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_ISSUERS = {"https://accounts.google.com", "accounts.google.com"}
JWKS_CACHE_KEY = "google_oauth_jwks_v1"
JWKS_CACHE_SECONDS = 3600
HTTP_TIMEOUT_SECONDS = 8


class GoogleOAuthError(Exception):
    """Raised whenever a Google identity or exchange cannot be trusted."""


@dataclass(frozen=True)
class GoogleIdentity:
    sub: str
    email: str
    email_verified: bool
    given_name: str
    family_name: str


def _b64url_to_int(data: str) -> int:
    padded = data + "=" * (-len(data) % 4)
    return int.from_bytes(base64.urlsafe_b64decode(padded), "big")


def _b64url_decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _fetch_jwks(force_refresh: bool = False) -> list[dict]:
    if not force_refresh:
        cached = cache.get(JWKS_CACHE_KEY)
        if cached:
            return cached
    try:
        with urllib.request.urlopen(GOOGLE_JWKS_URL, timeout=HTTP_TIMEOUT_SECONDS) as resp:
            keys = json.loads(resp.read())["keys"]
    except (urllib.error.URLError, ValueError, KeyError) as exc:
        raise GoogleOAuthError("Impossible de joindre le service d'identité Google. Réessayez.") from exc
    cache.set(JWKS_CACHE_KEY, keys, JWKS_CACHE_SECONDS)
    return keys


def _public_key_for(kid: str) -> rsa.RSAPublicKey:
    for refresh in (False, True):
        for key in _fetch_jwks(force_refresh=refresh):
            if key.get("kid") == kid:
                return rsa.RSAPublicNumbers(_b64url_to_int(key["e"]), _b64url_to_int(key["n"])).public_key()
    raise GoogleOAuthError("Clé de signature Google introuvable (jeton refusé).")


def verify_id_token(id_token: str, client_id: str) -> GoogleIdentity:
    """Verify signature, issuer, audience and expiry of a Google ID token
    (RS256 JWT) and return the identity it vouches for."""
    try:
        header_b64, payload_b64, sig_b64 = id_token.split(".")
    except ValueError as exc:
        raise GoogleOAuthError("Jeton d'identité Google malformé.") from exc

    header = json.loads(_b64url_decode(header_b64))
    payload = json.loads(_b64url_decode(payload_b64))
    signature = _b64url_decode(sig_b64)
    signed_data = f"{header_b64}.{payload_b64}".encode("ascii")

    if header.get("alg") != "RS256":
        raise GoogleOAuthError("Algorithme de signature Google inattendu.")

    public_key = _public_key_for(header.get("kid", ""))
    try:
        public_key.verify(signature, signed_data, padding.PKCS1v15(), hashes.SHA256())
    except InvalidSignature as exc:
        raise GoogleOAuthError("Signature du jeton Google invalide.") from exc

    if payload.get("iss") not in GOOGLE_ISSUERS:
        raise GoogleOAuthError("Émetteur du jeton Google inattendu.")
    if payload.get("aud") != client_id:
        raise GoogleOAuthError("Ce jeton Google n'est pas destiné à cette application.")
    if float(payload.get("exp", 0)) < time.time():
        raise GoogleOAuthError("Jeton Google expiré, merci de réessayer.")
    if not payload.get("email"):
        raise GoogleOAuthError("Aucune adresse e-mail associée à ce compte Google.")

    return GoogleIdentity(
        sub=str(payload["sub"]),
        email=str(payload["email"]).lower(),
        email_verified=bool(payload.get("email_verified")),
        given_name=str(payload.get("given_name", "")),
        family_name=str(payload.get("family_name", "")),
    )


def exchange_code_for_identity(*, code: str, client_id: str, client_secret: str, redirect_uri: str) -> GoogleIdentity:
    """Server-side leg of the OAuth authorization-code flow: trade the
    one-time ``code`` Google sent to our callback for tokens, then verify
    the resulting ID token."""
    data = urllib.parse.urlencode({
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }).encode("ascii")
    request = urllib.request.Request(GOOGLE_TOKEN_URL, data=data, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as resp:
            token_response = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise GoogleOAuthError("Google a refusé l'échange du code d'autorisation (lien expiré, réessayez).") from exc
    except urllib.error.URLError as exc:
        raise GoogleOAuthError("Impossible de joindre Google pour finaliser la connexion.") from exc

    id_token = token_response.get("id_token")
    if not id_token:
        raise GoogleOAuthError("Réponse Google incomplète : identité non transmise.")
    return verify_id_token(id_token, client_id)


def build_authorization_url(*, client_id: str, redirect_uri: str, state: str) -> str:
    params = urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "online",
        "include_granted_scopes": "true",
        "prompt": "select_account",
    })
    return f"{GOOGLE_AUTH_URL}?{params}"
