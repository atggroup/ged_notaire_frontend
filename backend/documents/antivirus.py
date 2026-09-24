"""Analyse antivirus des dépôts (ClamAV, protocole natif clamd INSTREAM).

Aucune dépendance : clamd est interrogé en TCP. Le service `clamav` du
docker-compose fournit le moteur et met ses signatures à jour tout seul.

Politique :
- fichier infecté → dépôt refusé, journalisé, alerte de sécurité ;
- moteur injoignable → dépôt REFUSÉ si l'antivirus est exigé (production par
  défaut) : une pièce n'est jamais acceptée sans avoir été analysée ;
- antivirus non configuré (développement) → pas d'analyse.
"""
import socket
import struct

from django.conf import settings

TAILLE_BLOC = 64 * 1024


class AntivirusIndisponible(Exception):
    pass


class FichierInfecte(Exception):
    def __init__(self, signature: str):
        super().__init__(signature)
        self.signature = signature


def antivirus_configure() -> bool:
    return bool(getattr(settings, "ANTIVIRUS_HOST", ""))


def analyser(data: bytes) -> str:
    """Renvoie « OK », lève FichierInfecte ou AntivirusIndisponible."""
    hote, port = settings.ANTIVIRUS_HOST, int(getattr(settings, "ANTIVIRUS_PORT", 3310))
    delai = float(getattr(settings, "ANTIVIRUS_TIMEOUT", 60))
    try:
        with socket.create_connection((hote, port), timeout=delai) as connexion:
            connexion.sendall(b"zINSTREAM\0")
            for debut in range(0, len(data), TAILLE_BLOC):
                bloc = data[debut:debut + TAILLE_BLOC]
                connexion.sendall(struct.pack("!L", len(bloc)) + bloc)
            connexion.sendall(struct.pack("!L", 0))
            reponse = b""
            while not reponse.endswith(b"\0"):
                morceau = connexion.recv(4096)
                if not morceau:
                    break
                reponse += morceau
    except OSError as exc:
        raise AntivirusIndisponible(f"moteur antivirus injoignable ({exc.__class__.__name__})") from exc
    texte = reponse.rstrip(b"\0").decode("utf-8", "replace").strip()
    if texte.endswith("OK"):
        return "OK"
    if texte.endswith("FOUND"):
        signature = texte.split(":", 1)[-1].replace("FOUND", "").strip()
        raise FichierInfecte(signature or "signature inconnue")
    # « INSTREAM size limit exceeded », erreur interne… : on ne conclut pas.
    raise AntivirusIndisponible(f"réponse inattendue du moteur : {texte[:120]}")


def controler_depot(request, data: bytes, nom: str):
    """Analyse un fichier déposé. Renvoie None si le dépôt peut continuer,
    sinon la Response d'erreur à renvoyer (le fichier n'est pas enregistré)."""
    from rest_framework.response import Response

    from audit.services import log_event
    if not antivirus_configure():
        return None
    try:
        analyser(data)
        return None
    except FichierInfecte as exc:
        log_event(request, "document_upload_blocked_virus", "document", "", result="failure",
                  metadata={"signature": exc.signature[:200], "nom": nom[:200]})
        from audit.tasks import lever_alerte
        lever_alerte("fichier_infecte", "haute", "Fichier infecté bloqué",
                     f"{request.user.display_name} a tenté de déposer un fichier infecté ({exc.signature}). "
                     "Le fichier a été refusé et n'a pas été enregistré. Faites vérifier le poste d'origine.",
                     f"virus:{request.user.pk}:{exc.signature}:{nom[:60]}", subject_user_id=request.user.pk,
                     details={"signature": exc.signature})
        return Response({"fichier": ["Fichier refusé : un logiciel malveillant a été détecté. Il n'a pas été enregistré ; "
                                     "faites vérifier le poste d'où il provient."]}, status=400)
    except AntivirusIndisponible as exc:
        log_event(request, "document_upload_scan_unavailable", "document", "", result="failure",
                  metadata={"erreur": str(exc)[:200], "exige": bool(getattr(settings, "ANTIVIRUS_REQUIRED", True))})
        if not getattr(settings, "ANTIVIRUS_REQUIRED", True):
            return None
        from django.utils import timezone
        from notifications.services import notify_admins
        notify_admins("antivirus_down", "Antivirus indisponible",
                      "Les dépôts de documents sont suspendus : le moteur antivirus ne répond pas. "
                      "Vérifiez le service « clamav » (écran Supervision / docker compose ps).",
                      severity="critique", event_key=f"antivirus-down:{timezone.localdate().isoformat()}")
        return Response({"detail": "Analyse antivirus momentanément indisponible : le dépôt est suspendu par sécurité. "
                                   "Réessayez dans quelques minutes ; le notaire a été prévenu."}, status=503)
