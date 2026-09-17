"""Operational encryption-key administration without exposing key material in normal APIs.

Runtime document keys remain in the server secret store/environment. The GED
provides safe status, generation, retirement checks and password-protected
recovery packages so an operator can perform rotation and emergency recovery.
"""
import base64
import hashlib
import json
import os
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from django.conf import settings
from documents.crypto import _keyring, active_key_id
from documents.models import Document

PACKAGE_VERSION = "GED-KEY-RECOVERY-1"

def _derive(passphrase: str, salt: bytes) -> bytes:
    return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(passphrase.encode("utf-8"))

def key_status() -> dict:
    ring = _keyring(); active = active_key_id()
    references = {k: Document.objects.filter(encryption_key_id=k).count() for k in ring}
    return {"activeKeyId": active if active in ring else None, "knownKeyIds": sorted(ring), "keyCount": len(ring), "documentReferences": references, "storage": "secret-store/env", "materialExposed": False}

def generate_key(key_id: str | None = None) -> tuple[str, str]:
    key_id = (key_id or f"key-{__import__('datetime').datetime.utcnow():%Y%m%d%H%M%S}").strip()
    if not key_id or len(key_id) > 64 or not all(c.isalnum() or c in "-_" for c in key_id):
        raise ValueError("Identifiant de clé invalide.")
    if key_id in _keyring():
        raise ValueError("Cet identifiant de clé existe déjà.")
    value = base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")
    return key_id, value

def recovery_export(passphrase: str) -> bytes:
    if len(passphrase) < 12:
        raise ValueError("La phrase secrète de récupération doit contenir au moins 12 caractères.")
    raw = getattr(settings, "DOCUMENT_ENCRYPTION_KEYS", "") or ""
    keys = {}
    if raw:
        try: keys.update(json.loads(raw))
        except Exception as exc: raise ValueError("DOCUMENT_ENCRYPTION_KEYS est invalide.") from exc
    legacy = getattr(settings, "DOCUMENT_ENCRYPTION_KEY", "") or ""
    if legacy: keys.setdefault("legacy", legacy)
    payload = json.dumps({"format": PACKAGE_VERSION, "activeKeyId": active_key_id(), "keys": keys}, ensure_ascii=False, sort_keys=True).encode("utf-8")
    salt, nonce = os.urandom(16), os.urandom(12)
    encrypted = AESGCM(_derive(passphrase, salt)).encrypt(nonce, payload, PACKAGE_VERSION.encode())
    envelope = {"format": PACKAGE_VERSION, "kdf": "scrypt", "salt": base64.urlsafe_b64encode(salt).decode(), "nonce": base64.urlsafe_b64encode(nonce).decode(), "ciphertext": base64.urlsafe_b64encode(encrypted).decode()}
    return json.dumps(envelope, ensure_ascii=False, indent=2).encode("utf-8")

def recovery_import(blob: bytes, passphrase: str) -> dict:
    try: env = json.loads(blob.decode("utf-8"))
    except Exception as exc: raise ValueError("Paquet de récupération invalide.") from exc
    if env.get("format") != PACKAGE_VERSION: raise ValueError("Version de paquet non supportée.")
    salt = base64.urlsafe_b64decode(env["salt"]); nonce = base64.urlsafe_b64decode(env["nonce"]); ciphertext = base64.urlsafe_b64decode(env["ciphertext"])
    try: plain = AESGCM(_derive(passphrase, salt)).decrypt(nonce, ciphertext, PACKAGE_VERSION.encode())
    except Exception as exc: raise ValueError("Phrase secrète incorrecte ou paquet altéré.") from exc
    data=json.loads(plain.decode("utf-8")); return {"format":data["format"],"activeKeyId":data.get("activeKeyId"),"keyIds":sorted(data.get("keys",{}).keys()),"keyCount":len(data.get("keys",{})),"envJson":json.dumps(data.get("keys",{}), ensure_ascii=False)}
