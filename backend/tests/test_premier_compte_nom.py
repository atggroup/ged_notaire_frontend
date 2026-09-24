"""Le premier compte porte le vrai nom du notaire et du cabinet."""
import pytest
from django.core.management import call_command

from accounts.models import User
from settings_app.models import CabinetSettings


@pytest.mark.django_db
def test_nom_du_notaire_et_du_cabinet_des_la_creation(monkeypatch):
    monkeypatch.setenv("INITIAL_ADMIN_PASSWORD", "Motdepasse-Solide-2026")
    call_command("create_first_admin", email="notaire@etude-test.ci",
                 prenom="Maître", nom="Kouassi Clotaire", cabinet="Cabinet de Maître Kouassi Clotaire")
    notaire = User.objects.get(email="notaire@etude-test.ci")
    assert notaire.display_name == "Maître Kouassi Clotaire"
    assert CabinetSettings.objects.get(pk=1).cabinet_name == "Cabinet de Maître Kouassi Clotaire"


@pytest.mark.django_db
def test_sans_nom_le_compte_reste_creable(monkeypatch):
    monkeypatch.setenv("INITIAL_ADMIN_PASSWORD", "Motdepasse-Solide-2026")
    for var in ("INITIAL_ADMIN_PRENOM", "INITIAL_ADMIN_NOM", "CABINET_NAME"):
        monkeypatch.delenv(var, raising=False)
    call_command("create_first_admin", email="notaire@etude-test.ci")
    assert User.objects.get(email="notaire@etude-test.ci").display_name == "Notaire Administrateur"
    assert not CabinetSettings.objects.filter(pk=1).exists()
