"""Surveillance de sécurité et intégrité du journal.

Les règles lisent le journal d'audit sur une fenêtre glissante. Chaque alerte
porte une clé de dédoublonnage (règle + sujet + créneau) : un passage toutes
les 5 minutes ne répète pas la même alerte. Aucune règle ne suspend un
compte ni ne retire un droit — la décision reste au notaire.
"""
from collections import Counter, defaultdict
from datetime import time, timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from core.jobs import job

from .models import AuditLog, SecurityAlert
from .services import log_system_event, verify_chain

FENETRE = timedelta(minutes=10)


def _creneau(now, minutes=60) -> str:
    local = timezone.localtime(now)
    return f"{local:%Y%m%d}-{(local.hour * 60 + local.minute) // minutes}"


def lever_alerte(rule, severity, title, message, dedup_key, *, subject_user_id=None, details=None) -> SecurityAlert | None:
    from notifications.services import active_admins, emit
    try:
        with transaction.atomic():
            alerte = SecurityAlert.objects.create(rule=rule, severity=severity, title=title[:255], message=message,
                                                  subject_user_id=subject_user_id, dedup_key=dedup_key[:190], details=details or {})
    except IntegrityError:
        return None
    log_system_event("security_alert_raised", "security_alert", str(alerte.pk),
                     metadata={"regle": rule, "gravite": severity, "sujet": subject_user_id})
    emit(active_admins(), "security_alert", f"Alerte sécurité : {title}", message,
         severity={"moyenne": "attention", "haute": "haute", "critique": "critique"}[severity],
         target_type="security_alert", target_id=alerte.pk)
    return alerte


def _nom(user_id) -> str:
    from accounts.models import User
    user = User.objects.filter(pk=user_id).first()
    return user.display_name if user else f"compte #{user_id}"


def _hors_horaires(instant) -> bool:
    try:
        debut, fin = (time.fromisoformat(x) for x in getattr(settings, "SECURITY_OFFICE_HOURS", "07:00-20:00").split("-"))
    except ValueError:
        debut, fin = time(7), time(20)
    local = timezone.localtime(instant)
    return local.weekday() >= 5 or not (debut <= local.time() <= fin)


def surveiller(now=None) -> dict:
    now = now or timezone.now()
    depuis = now - FENETRE
    recents = AuditLog.objects.filter(timestamp__gte=depuis)
    levees = []

    # 1. Échecs de connexion répétés sur un même compte.
    seuil = getattr(settings, "SECURITY_FAILED_LOGIN_THRESHOLD", 5)
    echecs = Counter(recents.filter(action="login_failed").exclude(target_id="").values_list("target_id", flat=True))
    for cible, n in echecs.items():
        if n >= seuil:
            levees.append(lever_alerte("force_brute", "haute", "Tentatives de connexion répétées",
                                       f"{n} échecs de connexion en 10 min sur le compte de {_nom(cible)}.",
                                       f"force_brute:{cible}:{_creneau(now)}", subject_user_id=int(cible) if cible.isdigit() else None,
                                       details={"echecs": n}))
    total = recents.filter(action="login_failed").count()
    if total >= 4 * seuil:
        levees.append(lever_alerte("echecs_globaux", "haute", "Vague d'échecs de connexion",
                                   f"{total} échecs de connexion en 10 min sur l'ensemble de l'étude.",
                                   f"echecs_globaux:{_creneau(now)}", details={"echecs": total}))

    # 2. Compte verrouillé.
    for cible in set(recents.filter(action="login_locked").values_list("target_id", flat=True)):
        levees.append(lever_alerte("compte_verrouille", "moyenne", "Compte verrouillé",
                                   f"Le compte de {_nom(cible)} a été verrouillé après des échecs répétés.",
                                   f"compte_verrouille:{cible}:{timezone.localdate(now)}",
                                   subject_user_id=int(cible) if str(cible).isdigit() else None))

    # 3. Un compte vu depuis 3 adresses ou plus en une heure (compte partagé ?).
    heure = AuditLog.objects.filter(timestamp__gte=now - timedelta(hours=1), user__isnull=False,
                                    action__in=["login", "login_mfa", "login_google"]).exclude(ip_address__isnull=True)
    adresses = defaultdict(set)
    for user_id, ip in heure.values_list("user_id", "ip_address"):
        adresses[user_id].add(ip)
    for user_id, ips in adresses.items():
        if len(ips) >= 3:
            levees.append(lever_alerte("adresses_multiples", "moyenne", "Connexions depuis plusieurs adresses",
                                       f"{_nom(user_id)} s'est connecté depuis {len(ips)} adresses IP distinctes en une heure.",
                                       f"adresses_multiples:{user_id}:{timezone.localdate(now)}", subject_user_id=user_id,
                                       details={"adresses": sorted(ips)}))

    # 4. Consultation / téléchargement massif.
    seuil_vues = getattr(settings, "SECURITY_MASS_VIEW_THRESHOLD", 40)
    vues = Counter(recents.filter(action__in=["document_viewed", "document_exported"], user__isnull=False).values_list("user_id", flat=True))
    for user_id, n in vues.items():
        if n >= seuil_vues:
            levees.append(lever_alerte("consultation_massive", "haute", "Consultation massive de documents",
                                       f"{_nom(user_id)} a ouvert ou téléchargé {n} pièces en 10 minutes.",
                                       f"consultation_massive:{user_id}:{_creneau(now)}", subject_user_id=user_id, details={"pieces": n}))

    # 5. Exports de dossiers : en rafale, ou hors des heures d'ouverture.
    exports = list(recents.filter(action="dossier_exported", user__isnull=False).values_list("pk", "user_id", "target_id", "timestamp"))
    par_user = Counter(e[1] for e in exports)
    for user_id, n in par_user.items():
        if n >= 3:
            levees.append(lever_alerte("export_massif", "haute", "Exports de dossiers en rafale",
                                       f"{_nom(user_id)} a exporté {n} dossiers complets en 10 minutes.",
                                       f"export_massif:{user_id}:{_creneau(now)}", subject_user_id=user_id, details={"exports": n}))
    for pk, user_id, cible, instant in exports:
        if _hors_horaires(instant):
            levees.append(lever_alerte("export_hors_horaires", "moyenne", "Export hors des heures d'ouverture",
                                       f"{_nom(user_id)} a exporté le dossier {cible} le {timezone.localtime(instant):%d/%m/%Y à %H:%M}.",
                                       f"export_hors_horaires:{pk}", subject_user_id=user_id))

    # 6. Mises à la corbeille en série.
    seuil_corbeille = getattr(settings, "SECURITY_MASS_TRASH_THRESHOLD", 5)
    corbeille = Counter(recents.filter(action="document_trashed", user__isnull=False).values_list("user_id", flat=True))
    for user_id, n in corbeille.items():
        if n >= seuil_corbeille:
            levees.append(lever_alerte("suppression_massive", "haute", "Mises à la corbeille en série",
                                       f"{_nom(user_id)} a placé {n} pièces à la corbeille en 10 minutes.",
                                       f"suppression_massive:{user_id}:{_creneau(now)}", subject_user_id=user_id, details={"pieces": n}))

    # 7. Événements de privilège sensibles (chacun signalé une fois).
    sensibles = {
        "user_role_changed": ("moyenne", "Changement de rôle"),
        "encryption_key_activated": ("haute", "Clé de chiffrement activée"),
        "encryption_keys_recovery_exported": ("haute", "Export du paquet de récupération des clés"),
        "permission_revoked": ("moyenne", "Révocation d'habilitations"),
    }
    for entree in recents.filter(action__in=list(sensibles)):
        if entree.action == "permission_revoked" and int((entree.metadata or {}).get("nombre", 0) or 0) < 5:
            continue
        gravite, titre = sensibles[entree.action]
        levees.append(lever_alerte(entree.action, gravite, titre,
                                   f"{titre} par {entree.user.display_name if entree.user_id else 'le système'} "
                                   f"le {timezone.localtime(entree.timestamp):%d/%m/%Y à %H:%M} (cible : {entree.target_id or '—'}).",
                                   f"privilege:{entree.pk}", subject_user_id=entree.user_id))

    nouvelles = [a for a in levees if a is not None]
    ouvertes = SecurityAlert.objects.filter(status=SecurityAlert.Status.OPEN).count()
    return {"items": len(nouvelles), "message": f"{len(nouvelles)} nouvelle(s) alerte(s) ; {ouvertes} alerte(s) ouverte(s) au total."}


@job("surveillance_securite", "Surveillance de sécurité (connexions, consultations, exports)", every=300, lock_ttl=600)
def surveillance_securite():
    return surveiller()


@job("integrite_audit", "Contrôle d'intégrité du journal d'audit", daily_at="03:30", retries=1, retry_delay=1800, lock_ttl=3600)
def integrite_audit():
    resultat = verify_chain()
    if not resultat["ok"]:
        lever_alerte("integrite_audit", "critique", "Journal d'audit altéré",
                     f"La chaîne d'intégrité du journal est rompue à l'entrée #{resultat['invalidLogId']} "
                     f"({resultat['checked']} entrées vérifiées avant la rupture). Une entrée a été modifiée ou supprimée hors application.",
                     f"integrite_audit:{resultat['invalidLogId']}", details=resultat)
        raise RuntimeError(f"Chaîne d'audit rompue à l'entrée #{resultat['invalidLogId']}.")
    return {"items": resultat["checked"], "message": f"Chaîne d'audit intègre : {resultat['checked']} entrée(s) vérifiée(s)."}
