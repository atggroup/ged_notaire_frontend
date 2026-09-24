"""AES-256-GCM envelope encryption for document bytes at rest.

Key management (cadrage "Attention à la phrase fichier chiffré..." du
correctif GED) : une seule clé statique ne permet ni rotation, ni
distinction entre les documents chiffrés avant/après un changement de
clé. On tient donc un véritable trousseau : chaque document conserve
l'identifiant (``key_id``) de la clé qui l'a chiffré, et une clé active
sert à tous les nouveaux chiffrements. Faire tourner la clé active ne
casse jamais les documents déjà déposés : ils restent lisibles avec
leur ancienne clé, encore présente dans le trousseau.
"""
import base64
import json
import os
from django.conf import settings
from django.db.utils import DatabaseError
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

LEGACY_KEY_ID = "legacy"


def _decode(value: str) -> bytes:
    try:
        key = base64.urlsafe_b64decode(value.encode())
    except Exception as exc:
        raise ValueError("Une clé de chiffrement doit être encodée en base64.") from exc
    if len(key) != 32:
        raise ValueError("Une clé de chiffrement doit correspondre à 32 octets (AES-256).")
    return key


def _keyring() -> dict[str, bytes]:
    """Build {key_id: 32-byte key} from configuration.

    ``DOCUMENT_ENCRYPTION_KEYS`` is a JSON object mapping a short key_id to
    a base64-encoded key, e.g. ``{"2026-q3": "...", "2026-q1": "..."}`` —
    this is what allows several generations of keys to coexist during a
    rotation. ``DOCUMENT_ENCRYPTION_KEY`` (singular, legacy) is still
    honoured as the implicit ``"legacy"`` entry so existing deployments and
    already-encrypted documents keep working without any migration step.
    """
    ring: dict[str, bytes] = {}
    raw = getattr(settings, "DOCUMENT_ENCRYPTION_KEYS", "") or ""
    if raw:
        try:
            parsed = json.loads(raw)
        except Exception as exc:
            raise ValueError("DOCUMENT_ENCRYPTION_KEYS doit être un objet JSON {key_id: clé_base64}.") from exc
        for key_id, value in parsed.items():
            ring[str(key_id)] = _decode(value)
    legacy = getattr(settings, "DOCUMENT_ENCRYPTION_KEY", "") or ""
    if legacy:
        ring.setdefault(LEGACY_KEY_ID, _decode(legacy))
    return ring


def active_key_id() -> str:
    configured = getattr(settings, "DOCUMENT_ENCRYPTION_ACTIVE_KEY_ID", "") or ""
    try:
        from settings_app.models import CabinetSettings
        persisted = CabinetSettings.objects.filter(pk=1).values_list("data", flat=True).first() or {}
        configured = persisted.get("active_key_id") or configured
    except DatabaseError:
        # Table pas encore migrée (ex. premier `manage.py check` avant `migrate`) :
        # on retombe sur la clé configurée par variable d'environnement. Toute
        # autre exception (bug de code, erreur de type) doit remonter au lieu
        # d'être avalée silencieusement.
        pass
    return configured or LEGACY_KEY_ID


def _key_for(key_id: str) -> bytes:
    ring = _keyring()
    if not ring:
        raise ValueError("Aucune clé de chiffrement n'est configurée (DOCUMENT_ENCRYPTION_KEY[S]).")
    key = ring.get(key_id)
    if key is None:
        raise ValueError(f"Clé de chiffrement « {key_id} » introuvable dans le trousseau : rotation incomplète ?")
    return key


def key_ring_status() -> dict:
    """Non-secret summary for the admin security dashboard: which key ids
    exist, which one is active — never the key material itself."""
    ring = _keyring()
    active = active_key_id()
    return {"activeKeyId": active if active in ring else None, "knownKeyIds": sorted(ring.keys()), "keyCount": len(ring)}


def encrypt(data: bytes, *, key_id: str | None = None) -> tuple[bytes, str]:
    """Encrypt with the active key (or an explicitly chosen one) and return
    ``(blob, key_id)``. The key_id must be stored alongside the ciphertext
    (``Document.encryption_key_id``) — it is not secret and is required to
    decrypt later, including after the active key has rotated."""
    key_id = key_id or active_key_id()
    nonce = os.urandom(12)
    ciphertext = nonce + AESGCM(_key_for(key_id)).encrypt(nonce, data, None)
    return ciphertext, key_id


def decrypt(ciphertext: bytes, key_id: str | None = None) -> bytes:
    if len(ciphertext) < 29:
        raise ValueError("Encrypted document is corrupt.")
    # Documents encrypted before this key-rotation feature existed have no
    # stored key_id: they were necessarily encrypted with the legacy key.
    resolved = key_id or LEGACY_KEY_ID
    return AESGCM(_key_for(resolved)).decrypt(ciphertext[:12], ciphertext[12:], None)
