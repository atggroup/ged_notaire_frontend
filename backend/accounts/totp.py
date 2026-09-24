"""Codes à usage unique basés sur le temps (TOTP, RFC 6238), sans dépendance.

Compatible avec Google Authenticator, Microsoft Authenticator, FreeOTP… :
secret base32, HMAC-SHA1, 6 chiffres, pas de 30 secondes.

Le secret est stocké CHIFFRÉ (trousseau documentaire, AES-256-GCM) : une
copie de la base ne suffit pas à fabriquer des codes.
"""
import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

PAS = 30
CHIFFRES = 6
TOLERANCE = 1  # ±30 s de dérive d'horloge du téléphone
EMETTEUR = "GED notariale"


def nouveau_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _cle(secret: str) -> bytes:
    secret = secret.strip().replace(" ", "").upper()
    return base64.b32decode(secret + "=" * (-len(secret) % 8))


def code_pour(secret: str, compteur: int, *, digest=hashlib.sha1, chiffres: int = CHIFFRES) -> str:
    mac = hmac.new(_cle(secret), struct.pack(">Q", compteur), digest).digest()
    decalage = mac[-1] & 0x0F
    valeur = struct.unpack(">I", mac[decalage:decalage + 4])[0] & 0x7FFFFFFF
    return str(valeur % (10 ** chiffres)).zfill(chiffres)


def verifier(secret: str, code: str, dernier_compteur: int | None, instant: float | None = None) -> int | None:
    """Renvoie le compteur accepté, ou None. Refuse la réutilisation d'un code
    déjà accepté (rejeu) : un code intercepté ne sert pas deux fois."""
    code = (code or "").strip().replace(" ", "")
    if not code.isdigit() or len(code) != CHIFFRES:
        return None
    courant = int((instant if instant is not None else time.time()) // PAS)
    for compteur in range(courant - TOLERANCE, courant + TOLERANCE + 1):
        if dernier_compteur is not None and compteur <= dernier_compteur:
            continue
        if hmac.compare_digest(code_pour(secret, compteur), code):
            return compteur
    return None


def uri_otpauth(secret: str, compte: str) -> str:
    libelle = quote(f"{EMETTEUR}:{compte}")
    return f"otpauth://totp/{libelle}?secret={secret}&issuer={quote(EMETTEUR)}&algorithm=SHA1&digits={CHIFFRES}&period={PAS}"


# --- Stockage chiffré du secret -------------------------------------------

def chiffrer_secret(secret: str) -> str:
    from documents.crypto import encrypt
    blob, key_id = encrypt(secret.encode("ascii"))
    return f"{key_id}:{base64.urlsafe_b64encode(blob).decode('ascii')}"


def dechiffrer_secret(valeur: str) -> str:
    from documents.crypto import decrypt
    key_id, blob = valeur.split(":", 1)
    return decrypt(base64.urlsafe_b64decode(blob), key_id).decode("ascii")


# --- Codes de secours ------------------------------------------------------

def codes_de_secours(nombre: int = 10) -> list[str]:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # sans 0/O ni 1/I
    return ["-".join("".join(secrets.choice(alphabet) for _ in range(5)) for _ in range(2)) for _ in range(nombre)]


def empreinte_secours(user_pk: int, code: str) -> str:
    normalise = code.strip().upper().replace(" ", "")
    return hashlib.sha256(f"ged-secours:{user_pk}:{normalise}".encode()).hexdigest()
