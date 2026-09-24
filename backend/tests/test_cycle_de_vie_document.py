"""Garde-fous du cycle de vie documentaire.

Chaque test correspond à un défaut constaté puis corrigé : ils existent pour
que le comportement ne se reperde pas silencieusement.
"""
import base64
import hashlib

import pytest
from django.core.files.base import ContentFile
from django.test import override_settings
from rest_framework.test import APIClient

from accounts.models import User
from audit.models import AuditLog
from documents.crypto import encrypt
from documents.models import Document
from dossiers.models import Dossier
from ged_backend.confidentialite import niveau_effectif
from permissions_app.access import has_document_access

KEY = base64.urlsafe_b64encode(b"0123456789abcdef0123456789abcdef").decode()


@pytest.fixture(autouse=True)
def _sans_limitation_de_debit():
    """Le limiteur d'appels (20/min) est compté dans le cache partagé : sur une
    suite complète il finit par répondre 429 et masquer le comportement testé.
    On repart d'un compteur vide pour chaque test de ce module."""
    from django.core.cache import cache
    cache.clear()
    yield
    cache.clear()
PDF = b"%PDF-1.4\ntrailer<</Root 1 0 R>>\n%%EOF\n"


def _contexte(reference="AUT-2026-00001", statut=Document.Status.VALIDATED):
    notaire = User.objects.create_user(email="n@etude.ci", password="Mdp!123456", role="admin")
    dossier = Dossier.objects.create(
        reference=reference, domaine="AUT", nom="Dossier", client="C", created_by=notaire,
    )
    chiffre, key_id = encrypt(PDF)
    doc = Document.objects.create(
        reference="DOC_" + reference.replace("-", ""), dossier=dossier, type="Acte",
        nom="acte.pdf", original_filename="acte.pdf", content_type="application/pdf",
        size_bytes=len(PDF), sha256=hashlib.sha256(PDF).hexdigest(), uploaded_by=notaire,
        master_reference="DOC_" + reference.replace("-", ""), encryption_key_id=key_id,
        statut=statut, quality_passed=(statut == Document.Status.VALIDATED),
    )
    doc.fichier.save("acte.pdf.enc", ContentFile(chiffre), save=True)
    client = APIClient()
    client.force_authenticate(notaire)
    return notaire, dossier, doc, client


@pytest.mark.django_db
@override_settings(DOCUMENT_ENCRYPTION_KEY=KEY)
def test_une_action_inconnue_repond_404_et_non_500():
    """Le routeur accepte n'importe quel segment : la vue doit le refuser
    proprement au lieu d'éclater sur une variable non assignée."""
    _, _, doc, client = _contexte()
    reponse = client.post(f"/api/documents/{doc.reference}/nimporte-quoi", {}, format="json")
    assert reponse.status_code == 404
    assert "Action inconnue" in reponse.data["detail"]


@pytest.mark.django_db
@override_settings(DOCUMENT_ENCRYPTION_KEY=KEY)
def test_la_restauration_rend_le_statut_reel():
    """L'écran promet « le document retrouve son statut précédent » : un acte
    validé ne doit pas revenir « à indexer » en gardant sa signature."""
    _, _, doc, client = _contexte(statut=Document.Status.VALIDATED)
    assert client.post(f"/api/documents/{doc.reference}/trash", {}, format="json").status_code == 200
    doc.refresh_from_db()
    assert doc.statut == Document.Status.TRASHED

    assert client.post(f"/api/documents/{doc.reference}/restore", {}, format="json").status_code == 200
    doc.refresh_from_db()
    assert doc.statut == Document.Status.VALIDATED


@pytest.mark.django_db
@override_settings(DOCUMENT_ENCRYPTION_KEY=KEY)
def test_on_n_archive_pas_une_piece_jamais_validee():
    _, _, brouillon, client = _contexte(statut=Document.Status.TO_INDEX)
    reponse = client.post("/api/documents/archive", {"ref": brouillon.reference}, format="json")
    assert reponse.status_code == 409
    brouillon.refresh_from_db()
    assert brouillon.statut == Document.Status.TO_INDEX
    assert brouillon.is_archived is False


@pytest.mark.django_db
@override_settings(DOCUMENT_ENCRYPTION_KEY=KEY)
def test_un_second_notaire_ne_peut_pas_autoriser_sa_propre_demande():
    notaire, _, doc, client = _contexte()
    User.objects.create_user(email="n2@etude.ci", password="Mdp!123456", role="admin")

    client.post(f"/api/documents/{doc.reference}/trash", {}, format="json")
    assert client.post(
        f"/api/documents/{doc.reference}/request-destruction",
        {"motif": "Doublon."}, format="json",
    ).status_code == 200

    refus = client.post(f"/api/documents/{doc.reference}/authorize-destruction", {}, format="json")
    assert refus.status_code == 409
    assert "autre que le demandeur" in refus.data["detail"]


@pytest.mark.django_db
@override_settings(DOCUMENT_ENCRYPTION_KEY=KEY)
def test_etude_a_notaire_unique_trace_l_absence_de_controle_croise():
    """Bloquer serait impraticable pour une étude à notaire unique ; l'écart
    doit alors être écrit noir sur blanc dans le journal d'audit."""
    _, _, doc, client = _contexte()
    client.post(f"/api/documents/{doc.reference}/trash", {}, format="json")
    client.post(f"/api/documents/{doc.reference}/request-destruction", {"motif": "Doublon."}, format="json")

    assert client.post(
        f"/api/documents/{doc.reference}/authorize-destruction", {}, format="json"
    ).status_code == 200

    trace = AuditLog.objects.filter(
        action="document_destruction_authorized", target_id=doc.reference
    ).first()
    assert trace is not None
    assert trace.metadata["separation_des_taches"] is False


@pytest.mark.django_db
@override_settings(DOCUMENT_ENCRYPTION_KEY=KEY)
def test_la_file_de_numerisation_liste_ce_qui_attend_un_traitement():
    """Elle montrait l'inverse : les pièces déjà contrôlées y figuraient, les
    pièces fraîchement déposées non."""
    notaire, dossier, doc, client = _contexte(statut=Document.Status.TO_INDEX)

    file_attente = client.get("/api/documents/queue")
    assert [x["reference"] for x in file_attente.data] == [doc.reference]

    controle = client.post(
        f"/api/documents/{doc.reference}/quality-check",
        {"checks": {k: True for k in (
            "complete", "ordered", "legible", "noMissingPage",
            "noDuplicate", "orientationCorrect", "dossierCorrect")}},
        format="json",
    )
    assert controle.status_code == 200
    assert controle.data["statut"] == Document.Status.PENDING
    # Une fois contrôlée, la pièce relève de la file du notaire, plus de celle-ci.
    assert client.get("/api/documents/queue").data == []


@pytest.mark.django_db
@override_settings(DOCUMENT_ENCRYPTION_KEY=KEY)
def test_le_statut_est_expose_avec_son_libelle_humain():
    _, _, doc, client = _contexte(statut=Document.Status.PENDING)
    # Sans ?format=json la vue renvoie le fichier lui-même.
    detail = client.get(f"/api/documents/{doc.reference}?format=json")
    assert detail.data["statut"] == "en_validation"
    assert detail.data["statutLabel"] == "En validation"


@pytest.mark.django_db
@override_settings(DOCUMENT_ENCRYPTION_KEY=KEY)
def test_une_piece_ne_peut_pas_etre_ramenee_sous_le_niveau_de_son_dossier():
    notaire, dossier, doc, client = _contexte()
    dossier.niveau_de_confidentialite = "Très confidentiel"
    dossier.save()

    refus = client.patch(
        f"/api/documents/{doc.reference}",
        {"niveau_de_confidentialite": "Standard"}, format="json",
    )
    assert refus.status_code == 409
    doc.refresh_from_db()
    # Le niveau stocké n'a pas bougé…
    assert doc.niveau_de_confidentialite == "Standard"
    # …et le niveau réellement opposable reste celui du dossier : la pièce
    # n'est pas lisible par l'étude pour autant.
    assert niveau_effectif(doc) == "Très confidentiel"
    temoin = User.objects.create_user(email="c@etude.ci", password="Mdp!123456", role="collaborateur")
    assert has_document_access(temoin, doc) is False


@pytest.mark.django_db
def test_le_journal_d_audit_resiste_aux_operations_ensemblistes():
    """`save()`/`delete()` du modèle ne sont pas appelés par un `.filter().delete()` :
    le caractère append-only doit tenir aussi à ce niveau."""
    notaire = User.objects.create_user(email="n9@etude.ci", password="Mdp!123456", role="admin")
    AuditLog.objects.create(user=notaire, action="document_viewed", target_type="document", target_id="X")

    with pytest.raises(RuntimeError):
        AuditLog.objects.filter(target_id="X").delete()
    with pytest.raises(RuntimeError):
        AuditLog.objects.filter(target_id="X").update(action="autre_chose")

    assert AuditLog.objects.filter(target_id="X", action="document_viewed").count() == 1
