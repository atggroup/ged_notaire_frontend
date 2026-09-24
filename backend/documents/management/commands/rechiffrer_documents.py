"""Rechiffre les pièces avec la clé ACTIVE du trousseau (rotation de clé).

    python manage.py rechiffrer_documents --simulation   # compte, n'écrit rien
    python manage.py rechiffrer_documents                # rechiffre tout
    python manage.py rechiffrer_documents --lot 200      # par tranches

Indispensable quand une clé a pu être exposée : activer une nouvelle clé ne
protège que les NOUVEAUX dépôts ; les pièces existantes restent chiffrées
avec l'ancienne tant qu'elles ne sont pas rechiffrées.

Sûreté, pièce par pièce :
1. déchiffrement avec l'ancienne clé, puis contrôle de l'empreinte SHA-256
   d'origine — une pièce altérée n'est JAMAIS réécrite (signalée) ;
2. écriture d'un NOUVEAU fichier chiffré avec la clé active, relu et vérifié ;
3. bascule en base (fichier + identifiant de clé) dans une transaction ;
4. suppression de l'ancien fichier seulement après la bascule.
Une interruption ne perd rien : on relance, les pièces déjà traitées sont
sautées. Le contenu en clair est identique — y compris sous conservation
légale — seul son chiffrement change ; chaque rechiffrement est journalisé.

Les SAUVEGARDES antérieures restent chiffrées avec l'ancienne clé : conservez
celle-ci (hors trousseau actif, dans le paquet de récupération) tant que ces
sauvegardes sont dans la période de rétention.
"""
import hashlib

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from audit.services import log_system_event
from documents.crypto import LEGACY_KEY_ID, _keyring, active_key_id, decrypt, encrypt
from documents.models import Document


class Command(BaseCommand):
    help = "Rechiffre toutes les pièces avec la clé active du trousseau."

    def add_arguments(self, parser):
        parser.add_argument("--simulation", action="store_true", help="Compte les pièces concernées sans rien écrire.")
        parser.add_argument("--lot", type=int, default=0, help="Nombre maximal de pièces traitées (0 = toutes).")

    def handle(self, *args, **options):
        cible = active_key_id()
        if cible not in _keyring():
            raise CommandError(f"La clé active « {cible} » est absente du trousseau (DOCUMENT_ENCRYPTION_KEYS).")
        a_traiter = (Document.objects.exclude(fichier="").exclude(statut=Document.Status.DESTROYED)
                     .exclude(encryption_key_id=cible).order_by("pk"))
        if cible == LEGACY_KEY_ID:
            a_traiter = a_traiter.exclude(encryption_key_id="")
        total = a_traiter.count()
        if options["simulation"]:
            par_cle = {}
            for key_id in a_traiter.values_list("encryption_key_id", flat=True):
                par_cle[key_id or LEGACY_KEY_ID] = par_cle.get(key_id or LEGACY_KEY_ID, 0) + 1
            self.stdout.write(f"Clé active : {cible}. Pièces à rechiffrer : {total} {par_cle or ''}")
            return
        if options["lot"]:
            a_traiter = a_traiter[:options["lot"]]
        faits, anomalies = 0, []
        for doc in a_traiter.iterator():
            try:
                self._rechiffrer(doc, cible)
                faits += 1
            except _Anomalie as exc:
                anomalies.append(f"{doc.reference} : {exc}")
                log_system_event("document_reencryption_refused", "document", doc.reference, result="failure",
                                 metadata={"motif": str(exc)[:300]})
        log_system_event("documents_reencrypted", "encryption_key", cible, result="failure" if anomalies else "success",
                         metadata={"rechiffrees": faits, "anomalies": len(anomalies)})
        restant = Document.objects.exclude(fichier="").exclude(statut=Document.Status.DESTROYED).exclude(encryption_key_id=cible).count()
        for ligne in anomalies:
            self.stderr.write(f"  ANOMALIE {ligne}")
        message = f"{faits} pièce(s) rechiffrée(s) avec « {cible} » ; {len(anomalies)} anomalie(s) ; {restant} restante(s)."
        if anomalies:
            raise CommandError(message + " Les pièces en anomalie n'ont PAS été modifiées : examinez-les avant de retirer l'ancienne clé.")
        self.stdout.write(self.style.SUCCESS(message))

    def _rechiffrer(self, doc: Document, cible: str) -> None:
        ancienne_cle = doc.encryption_key_id or LEGACY_KEY_ID
        ancien_nom = doc.fichier.name
        stockage = doc.fichier.storage
        try:
            with stockage.open(ancien_nom, "rb") as source:
                clair = decrypt(source.read(), ancienne_cle)
        except Exception as exc:  # noqa: BLE001 — clé absente, fichier illisible
            raise _Anomalie(f"déchiffrement impossible ({exc.__class__.__name__})") from exc
        if hashlib.sha256(clair).hexdigest() != doc.sha256:
            raise _Anomalie("empreinte SHA-256 différente de celle du dépôt : pièce non réécrite")
        chiffre, key_id = encrypt(clair, key_id=cible)
        # Nouveau fichier : l'ancien reste intact jusqu'à la bascule en base.
        nouveau_nom = stockage.save(ancien_nom, ContentFile(chiffre))
        with stockage.open(nouveau_nom, "rb") as relu:
            if hashlib.sha256(decrypt(relu.read(), key_id)).hexdigest() != doc.sha256:
                stockage.delete(nouveau_nom)
                raise _Anomalie("relecture du nouveau fichier non conforme : bascule annulée")
        with transaction.atomic():
            bascule = Document.objects.filter(pk=doc.pk, encryption_key_id=doc.encryption_key_id, fichier=ancien_nom).update(
                fichier=nouveau_nom, encryption_key_id=key_id)
        if not bascule:
            # La pièce a changé entre-temps (autre exécution, destruction) :
            # on retire notre copie et on ne touche à rien d'autre.
            stockage.delete(nouveau_nom)
            raise _Anomalie("pièce modifiée pendant le rechiffrement : relancez la commande")
        log_system_event("document_reencrypted", "document", doc.reference,
                         metadata={"ancienne_cle": ancienne_cle, "nouvelle_cle": key_id})
        if nouveau_nom != ancien_nom and stockage.exists(ancien_nom):
            stockage.delete(ancien_nom)


class _Anomalie(Exception):
    pass
