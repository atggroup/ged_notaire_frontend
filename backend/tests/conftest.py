import pytest


@pytest.fixture(autouse=True)
def _media_isole(settings, tmp_path):
    """Chaque test écrit ses pièces factices dans un répertoire temporaire.

    Sans ce garde-fou, chaque exécution de la suite déposait des centaines
    de fichiers chiffrés de test dans `backend/media/`, mêlés aux pièces
    réelles de l'étude en développement."""
    settings.MEDIA_ROOT = str(tmp_path / "media")
    settings.BACKUP_LOCAL_ROOT = str(tmp_path / "backup_store")
