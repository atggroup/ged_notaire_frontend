"""Primitives de coordination portables (PostgreSQL et SQLite).

- `acquire_lock` / `release_lock` : verrou à bail ;
- `claim_once` : « faire une seule fois » — l'idempotence des automatisations ;
- `next_sequence` : compteur verrouillé pour les références métier.
"""
import uuid
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import AutomationMark, JobLock, ReferenceSequence


def acquire_lock(name: str, ttl_seconds: int) -> str | None:
    """Renvoie un jeton si le verrou est obtenu, None s'il est déjà tenu."""
    token = uuid.uuid4().hex
    now = timezone.now()
    for _ in range(2):
        try:
            with transaction.atomic():
                lock, _created = JobLock.objects.select_for_update().get_or_create(name=name)
                if lock.holder and lock.expires_at and lock.expires_at > now:
                    return None
                lock.holder = token
                lock.expires_at = now + timedelta(seconds=ttl_seconds)
                lock.save(update_fields=["holder", "expires_at"])
                return token
        except IntegrityError:
            # Deux exécutants ont créé la ligne au même instant : on relit.
            continue
    return None


def extend_lock(name: str, token: str, ttl_seconds: int) -> bool:
    return bool(JobLock.objects.filter(name=name, holder=token).update(
        expires_at=timezone.now() + timedelta(seconds=ttl_seconds)))


def release_lock(name: str, token: str) -> None:
    JobLock.objects.filter(name=name, holder=token).update(holder="", expires_at=None)


def claim_once(key: str) -> bool:
    """True la première fois qu'une clé est revendiquée, False ensuite.

    À appeler dans la même transaction que l'effet qu'elle protège : si
    l'effet échoue, la revendication est annulée avec lui et sera retentée."""
    key = key[:190]
    if AutomationMark.objects.filter(key=key).exists():
        return False
    try:
        with transaction.atomic():
            AutomationMark.objects.create(key=key)
        return True
    except IntegrityError:
        return False


def forget(key: str) -> None:
    """Rend de nouveau possible une action « une seule fois » (ex. une pièce
    rouverte doit pouvoir être relancée)."""
    AutomationMark.objects.filter(key=key[:190]).delete()


def next_sequence(scope: str, *, floor=None) -> int:
    """Prochaine valeur du compteur `scope`.

    `floor` (valeur ou fonction) n'est évalué qu'à la création du compteur :
    il reprend l'existant pour qu'une base déjà peuplée ne reparte pas à 1."""
    with transaction.atomic():
        seq = ReferenceSequence.objects.select_for_update().filter(scope=scope).first()
        if seq is None:
            start = floor() if callable(floor) else (floor or 0)
            try:
                with transaction.atomic():
                    seq = ReferenceSequence.objects.create(scope=scope, last_value=int(start or 0))
            except IntegrityError:
                seq = ReferenceSequence.objects.select_for_update().get(scope=scope)
        seq.last_value += 1
        seq.save(update_fields=["last_value"])
        return seq.last_value
