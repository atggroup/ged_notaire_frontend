"""Index de recherche plein texte (PostgreSQL uniquement).

La recherche utilise `icontains`, que PostgreSQL traduit en
`UPPER(col::text) LIKE UPPER('%terme%')`. Sans index, chaque recherche lit
toute la table, texte OCR compris : quelques secondes à 10 000 pièces,
inutilisable à 100 000. Un index GIN à trigrammes sur `UPPER(col)` sert
exactement cette forme de requête, sans rien changer au code de recherche.

L'extension `pg_trgm` est « de confiance » depuis PostgreSQL 13 : le
propriétaire de la base peut l'activer. Sur SQLite (développement), la
migration ne fait rien.
"""
from django.db import migrations

COLONNES = ("extracted_text", "nom", "code_notarial", "reference")


def creer(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    for colonne in COLONNES:
        schema_editor.execute(
            f'CREATE INDEX IF NOT EXISTS doc_trgm_{colonne}_idx ON documents_document '
            f'USING gin (UPPER("{colonne}"::text) gin_trgm_ops)'
        )


def supprimer(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    for colonne in COLONNES:
        schema_editor.execute(f"DROP INDEX IF EXISTS doc_trgm_{colonne}_idx")


class Migration(migrations.Migration):
    dependencies = [("documents", "0010_document_ocr_attempts_document_ocr_next_retry_at_and_more")]
    operations = [migrations.RunPython(creer, supprimer)]
