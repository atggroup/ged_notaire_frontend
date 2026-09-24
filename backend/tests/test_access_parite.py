"""Le filtre SQL d'habilitation doit dire EXACTEMENT ce que dit le contrôle
unitaire — sinon une liste laisserait passer ce qu'une vue de détail refuse.

Ce test balaie toutes les combinaisons (niveau du document × niveau du dossier
× habilitation explicite × affectation au dossier) et compare les deux formes.
"""
import base64
import hashlib
import itertools

import pytest
from django.core.files.base import ContentFile
from django.test import override_settings

from accounts.models import User
from documents.models import Document
from dossiers.models import Dossier, DossierAssignment
from ged_backend.confidentialite import NIVEAUX
from permissions_app.access import (
    documents_visibles_par,
    dossiers_visibles_par,
    has_document_access,
    has_dossier_access,
)
from permissions_app.models import Permission

KEY = base64.urlsafe_b64encode(b"0123456789abcdef0123456789abcdef").decode()
PDF = b"%PDF-1.4\ntrailer<</Root 1 0 R>>\n%%EOF\n"


def _document(reference, dossier, niveau, auteur):
    doc = Document.objects.create(
        reference=reference, dossier=dossier, type="Acte", nom=reference,
        original_filename="a.pdf", content_type="application/pdf",
        size_bytes=len(PDF), sha256=hashlib.sha256(PDF).hexdigest(),
        uploaded_by=auteur, master_reference=reference,
        niveau_de_confidentialite=niveau,
    )
    doc.fichier.save("a.pdf.enc", ContentFile(PDF), save=True)
    return doc


@pytest.mark.django_db
@override_settings(DOCUMENT_ENCRYPTION_KEY=KEY)
def test_filtre_sql_et_controle_unitaire_concordent():
    notaire = User.objects.create_user(email="n@x.ci", password="Mdp!123456", role="admin")
    temoin = User.objects.create_user(email="c@x.ci", password="Mdp!123456", role="collaborateur")

    attendus = {}
    combinaisons = itertools.product(NIVEAUX, NIVEAUX, [False, True], [False, True])
    for index, (niveau_doc, niveau_dossier, habilite, affecte) in enumerate(combinaisons):
        dossier = Dossier.objects.create(
            reference=f"AUT-2026-{index:05d}", domaine="AUT", nom=f"D{index}",
            client="C", created_by=notaire, niveau_de_confidentialite=niveau_dossier,
        )
        doc = _document(f"DOC_{index:05d}", dossier, niveau_doc, notaire)
        if habilite:
            Permission.objects.create(user=temoin, document=doc, granted_by=notaire)
        if affecte:
            DossierAssignment.objects.create(
                dossier=dossier, user=temoin, role=DossierAssignment.Role.COLLABORATOR,
                assigned_by=notaire,
            )
        attendus[doc.reference] = has_document_access(temoin, doc)

    visibles = set(
        documents_visibles_par(temoin, Document.objects.all()).values_list("reference", flat=True)
    )

    divergences = {
        reference: (unitaire, reference in visibles)
        for reference, unitaire in attendus.items()
        if unitaire != (reference in visibles)
    }
    assert not divergences, f"Le filtre SQL diverge du contrôle unitaire : {divergences}"
    # Garde-fou : le test perdrait tout son sens si tout était visible ou rien.
    assert 0 < len(visibles) < len(attendus)


@pytest.mark.django_db
def test_filtre_dossiers_concorde():
    notaire = User.objects.create_user(email="n2@x.ci", password="Mdp!123456", role="admin")
    temoin = User.objects.create_user(email="c2@x.ci", password="Mdp!123456", role="collaborateur")

    attendus = {}
    for index, (niveau, habilite, affecte) in enumerate(
        itertools.product(NIVEAUX, [False, True], [False, True])
    ):
        dossier = Dossier.objects.create(
            reference=f"SUC-2026-{index:05d}", domaine="SUC", nom=f"D{index}",
            client="C", created_by=notaire, niveau_de_confidentialite=niveau,
        )
        if habilite:
            Permission.objects.create(user=temoin, dossier=dossier, granted_by=notaire)
        if affecte:
            DossierAssignment.objects.create(
                dossier=dossier, user=temoin, role=DossierAssignment.Role.COLLABORATOR,
                assigned_by=notaire,
            )
        attendus[dossier.reference] = has_dossier_access(temoin, dossier)

    visibles = set(
        dossiers_visibles_par(temoin, Dossier.objects.all()).values_list("reference", flat=True)
    )
    divergences = {
        reference: (unitaire, reference in visibles)
        for reference, unitaire in attendus.items()
        if unitaire != (reference in visibles)
    }
    assert not divergences, f"Le filtre SQL diverge du contrôle unitaire : {divergences}"


@pytest.mark.django_db
@override_settings(DOCUMENT_ENCRYPTION_KEY=KEY)
def test_le_dossier_releve_le_niveau_de_la_piece():
    """Une pièce « Standard » dans un dossier « Très confidentiel » reste fermée."""
    notaire = User.objects.create_user(email="n3@x.ci", password="Mdp!123456", role="admin")
    temoin = User.objects.create_user(email="c3@x.ci", password="Mdp!123456", role="collaborateur")
    dossier = Dossier.objects.create(
        reference="SUC-2026-99999", domaine="SUC", nom="Sensible", client="C",
        created_by=notaire, niveau_de_confidentialite="Très confidentiel",
    )
    doc = _document("DOC_FUITE", dossier, "Standard", notaire)

    assert has_document_access(temoin, doc) is False
    assert "DOC_FUITE" not in set(
        documents_visibles_par(temoin, Document.objects.all()).values_list("reference", flat=True)
    )
