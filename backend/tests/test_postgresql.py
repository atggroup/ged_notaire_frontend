"""Tests propres à PostgreSQL (ignorés sur SQLite).

SQLite sérialise toutes les écritures : les courses entre requêtes
simultanées n'y apparaissent jamais. Ces tests ne tournent donc que sur le
vrai moteur de production (voir docker-compose.test.yml).
"""
import threading

import pytest
from django.db import connection, connections

pytestmark = pytest.mark.skipif(connection.vendor != "postgresql", reason="concurrence vérifiable uniquement sur PostgreSQL")


def _en_parallele(fonction, nombre=10):
    resultats, erreurs = [], []
    depart = threading.Barrier(nombre)

    def tache():
        try:
            depart.wait()
            resultats.append(fonction())
        except Exception as exc:  # noqa: BLE001
            erreurs.append(exc)
        finally:
            connections.close_all()

    fils = [threading.Thread(target=tache) for _ in range(nombre)]
    for f in fils:
        f.start()
    for f in fils:
        f.join()
    return resultats, erreurs


@pytest.mark.django_db(transaction=True)
def test_des_creations_simultanees_de_dossiers_recoivent_des_references_distinctes():
    from dossiers.views import next_dossier_reference
    resultats, erreurs = _en_parallele(lambda: next_dossier_reference("VEN"))
    assert not erreurs, erreurs
    assert len(set(resultats)) == len(resultats) == 10


@pytest.mark.django_db(transaction=True)
def test_un_travail_n_est_jamais_execute_deux_fois_en_parallele():
    from core.coordination import acquire_lock
    resultats, erreurs = _en_parallele(lambda: acquire_lock("job:concurrence", 600))
    assert not erreurs, erreurs
    assert sum(1 for r in resultats if r) == 1, "un seul exécutant doit obtenir le verrou"


@pytest.mark.django_db(transaction=True)
def test_une_action_unique_n_est_revendiquee_qu_une_fois():
    from core.coordination import claim_once
    resultats, erreurs = _en_parallele(lambda: claim_once("rappel:concurrence"))
    assert not erreurs, erreurs
    assert resultats.count(True) == 1


@pytest.mark.django_db
def test_la_recherche_plein_texte_dispose_de_ses_index_trigrammes():
    with connection.cursor() as cursor:
        cursor.execute("SELECT indexname FROM pg_indexes WHERE tablename = 'documents_document' AND indexname LIKE 'doc_trgm_%%'")
        index = {ligne[0] for ligne in cursor.fetchall()}
    assert {"doc_trgm_extracted_text_idx", "doc_trgm_nom_idx"} <= index


@pytest.mark.django_db(transaction=True)
def test_le_journal_d_audit_reste_chaine_sous_ecritures_simultanees():
    from audit.services import log_system_event, verify_chain
    resultats, erreurs = _en_parallele(lambda: log_system_event("concurrence", "test", "x"), nombre=15)
    assert not erreurs, erreurs
    assert verify_chain()["ok"], "des écritures simultanées ne doivent jamais rompre la chaîne"
