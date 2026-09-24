"""Les exécutants ne lancent aucun travail tant que la base n'est pas migrée.

Constaté sur la pile de test : au premier démarrage, les travaux partaient
pendant que le conteneur web appliquait encore les migrations, échouaient
tous (« relation … does not exist ») et envoyaient chacun une alerte.
"""
from unittest import mock

import pytest

from core.management.commands.run_worker import Command


@pytest.mark.django_db
def test_base_a_jour_les_travaux_demarrent_sans_attendre():
    commande = Command()
    commande._stop = False
    with mock.patch("core.management.commands.run_worker.time.sleep") as dormir:
        commande._attendre_migrations()
    dormir.assert_not_called()


@pytest.mark.django_db
def test_migrations_en_attente_les_travaux_attendent():
    commande = Command()
    commande._stop = False
    plans = iter([["migration en attente"], ["migration en attente"], []])
    with mock.patch("django.db.migrations.executor.MigrationExecutor.migration_plan", side_effect=lambda *_: next(plans)), \
            mock.patch("core.management.commands.run_worker.time.sleep") as dormir:
        commande._attendre_migrations()
    assert dormir.call_count == 2, "l'exécutant doit patienter tant que des migrations restent à appliquer"


@pytest.mark.django_db
def test_aucun_travail_n_est_lance_avant_la_fin_de_l_attente():
    commande = Command()
    ordre = []
    with mock.patch.object(Command, "_attendre_migrations", lambda self: ordre.append("attente")), \
            mock.patch.object(Command, "_passage", lambda self, choisis: ordre.append("passage")), \
            mock.patch.object(Command, "_battement", lambda self, nom, groupes: None), \
            mock.patch("core.surveillance.signaler"):
        commande.handle(groupe=[], sauf_groupe=[], une_fois=True, travail=None, liste=False, intervalle=1, nom="test")
    assert ordre == ["attente", "passage"]
