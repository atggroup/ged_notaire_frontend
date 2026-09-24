import hashlib
import json
from datetime import timedelta
from django.db import transaction
from django.utils import timezone
from .models import AuditLog


def log_event(request, action: str, target_type: str = "", target_id: str = "", result: str = "success", metadata: dict | None = None) -> AuditLog:
    # L'adresse NE peut PAS venir de `X-Forwarded-For` : nginx y concatène ce
    # que le navigateur envoie, l'intéressé dictait donc l'adresse inscrite à
    # son propre dossier. Un journal notarial dont l'adresse est choisie par
    # celui qu'il trace ne prouve rien. Voir `ged_backend/reseau.py`.
    from ged_backend.reseau import adresse_client
    ip = adresse_client(request)
    user = request.user if getattr(request, "user", None) and request.user.is_authenticated else None
    return _append(user, ip, action, target_type, target_id, result, metadata)


def log_system_event(action: str, target_type: str = "", target_id: str = "", result: str = "success", metadata: dict | None = None, *, user=None) -> AuditLog:
    """Journalise une action faite par l'application elle-même (travail
    planifié, rappel, alerte). Sans elle, les automatisations échappaient au
    journal : `log_event` exige une requête HTTP. `user` désigne, le cas
    échéant, la personne qui a demandé l'exécution depuis l'écran."""
    metadata = {"acteur": "système", **(metadata or {})}
    return _append(user, None, action, target_type, target_id, result, metadata)


def _append(user, ip, action, target_type, target_id, result, metadata) -> AuditLog:
    # A hash chain makes any removed or modified journal row detectable during
    # an integrity review.  The model still rejects normal updates/deletes.
    with transaction.atomic():
        previous = AuditLog.objects.select_for_update().order_by("-id").first()
        timestamp = timezone.now()
        payload = {
            "timestamp": timestamp.isoformat(), "user_id": user.pk if user else None,
            "action": action, "target_type": target_type, "target_id": target_id,
            "ip_address": ip, "result": result, "metadata": metadata or {},
            "previous_hash": previous.entry_hash if previous else "",
        }
        entry_hash = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        return AuditLog.objects.create(user=user, action=action, target_type=target_type, target_id=target_id, ip_address=ip, result=result, metadata=metadata or {}, timestamp=timestamp, previous_hash=payload["previous_hash"], entry_hash=entry_hash)


def verify_chain() -> dict:
    """Recalcule la chaîne complète. Partagé par l'écran et le contrôle
    quotidien planifié, pour que les deux ne puissent jamais diverger."""
    previous_hash = ""
    checked = 0
    for log in AuditLog.objects.order_by("id").iterator():
        # Records created before the migration are readable but could not
        # have a historic hash. Every later record is verified in order.
        if not log.entry_hash:
            continue
        payload = {
            "timestamp": log.timestamp.isoformat(), "user_id": log.user_id,
            "action": log.action, "target_type": log.target_type, "target_id": log.target_id,
            "ip_address": log.ip_address, "result": log.result, "metadata": log.metadata or {},
            "previous_hash": previous_hash,
        }
        expected = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        if log.previous_hash != previous_hash or log.entry_hash != expected:
            return {"ok": False, "checked": checked, "invalidLogId": log.pk}
        previous_hash = log.entry_hash
        checked += 1
    return {"ok": True, "checked": checked}


def security_alerts(window_hours: int = 24) -> list[dict]:
    """Signals surfaced on the notaire/admin dashboard — never blocking on
    their own, always reviewed by a human. Covers: recently locked accounts,
    a burst of failed logins, and the "comptes partagés" concern from the
    cadrage (one account authenticating from several distinct IP addresses
    within a short window is not proof of sharing, but is worth a look)."""
    since = timezone.now() - timedelta(hours=window_hours)
    alerts: list[dict] = []
    locked = AuditLog.objects.filter(action="login_locked", timestamp__gte=since).values("user_id").distinct().count()
    if locked:
        alerts.append({"type": "comptes_verrouilles", "message": f"{locked} compte(s) verrouillé(s) après échecs répétés sur les dernières {window_hours} h.", "severity": "haute"})
    failed = AuditLog.objects.filter(action="login_failed", timestamp__gte=since).count()
    if failed >= 10:
        alerts.append({"type": "echecs_connexion", "message": f"{failed} échecs de connexion sur les dernières {window_hours} h.", "severity": "moyenne"})
    # Group successful logins by user, then flag users seen from 2+ distinct IPs.
    logins = AuditLog.objects.filter(action__in=["login", "login_mfa", "login_google"], timestamp__gte=since, user__isnull=False).exclude(ip_address__isnull=True).values("user_id", "ip_address").distinct()
    ip_counts: dict[int, set[str]] = {}
    for row in logins:
        ip_counts.setdefault(row["user_id"], set()).add(row["ip_address"])
    shared_suspects = [uid for uid, ips in ip_counts.items() if len(ips) >= 2]
    if shared_suspects:
        alerts.append({"type": "sessions_multi_adresses", "message": f"{len(shared_suspects)} compte(s) connecté(s) depuis plusieurs adresses IP distinctes sur les dernières {window_hours} h — à vérifier (compte partagé interdit).", "severity": "moyenne", "userIds": shared_suspects})
    return alerts
