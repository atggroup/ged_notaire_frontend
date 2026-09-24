"""Identification du format RÉEL d'un dépôt (jamais par l'extension).

Formats admis : PDF, JPEG, PNG, TIFF, et les formats bureautiques ouverts
Word (.docx) et Excel (.xlsx) — indispensables à une étude (projets d'actes).

Garde-fous des fichiers bureautiques (ce sont des archives ZIP) :
- structure OOXML vérifiée ([Content_Types].xml + partie principale) ;
- MACROS REFUSÉES (vbaProject.bin, types « macroEnabled », ActiveX, objets
  OLE embarqués) : c'est la première voie d'infection par document ;
- anti « bombe zip » : nombre d'entrées, taille décompressée et taux de
  compression bornés ;
- chemins d'entrée absolus ou remontants (`../`) refusés.
Les anciens formats binaires (.doc, .xls) sont refusés : ils portent
couramment des macros et ne se contrôlent pas de façon fiable.
"""
import io
import zipfile

MIME_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MIME_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MIMES_BUREAUTIQUES = {MIME_DOCX, MIME_XLSX}

SIGNATURES = [(b"%PDF-", "application/pdf"), (b"\xff\xd8\xff", "image/jpeg"), (b"\x89PNG\r\n\x1a\n", "image/png"),
              (b"II*\x00", "image/tiff"), (b"MM\x00*", "image/tiff")]

MAX_ENTREES = 3000
MAX_DECOMPRESSE = 250 * 1024 * 1024
MAX_TAUX = 200


class FormatRefuse(ValueError):
    pass


def identifier(data: bytes) -> str:
    """Renvoie le type MIME réel, ou lève FormatRefuse avec un message clair."""
    for signature, mime in SIGNATURES:
        if data.startswith(signature):
            return mime
    if data.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        raise FormatRefuse("Ancien format Word/Excel (.doc/.xls) refusé : enregistrez le fichier en .docx, .xlsx ou PDF.")
    if data.startswith(b"PK\x03\x04"):
        return _bureautique(data)
    raise FormatRefuse("Type réel non autorisé : PDF, JPEG, PNG, TIFF, Word (.docx) ou Excel (.xlsx) uniquement.")


def _bureautique(data: bytes) -> str:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
        entrees = archive.infolist()
    except (zipfile.BadZipFile, OSError, ValueError) as exc:
        raise FormatRefuse("Archive illisible : fichier bureautique corrompu.") from exc
    if len(entrees) > MAX_ENTREES:
        raise FormatRefuse("Fichier bureautique refusé : structure anormale (trop d'éléments).")
    total = sum(e.file_size for e in entrees)
    compresse = max(1, sum(e.compress_size for e in entrees))
    if total > MAX_DECOMPRESSE or total / compresse > MAX_TAUX:
        raise FormatRefuse("Fichier bureautique refusé : taux de compression anormal (possible « bombe zip »).")
    noms = {e.filename for e in entrees}
    minuscules = {n.lower() for n in noms}
    for nom in noms:
        if nom.startswith(("/", "\\")) or ".." in nom.replace("\\", "/").split("/"):
            raise FormatRefuse("Fichier bureautique refusé : chemin interne invalide.")
    if "[Content_Types].xml" not in noms:
        raise FormatRefuse("Archive ZIP refusée : seuls les documents Word (.docx) et Excel (.xlsx) sont admis.")
    types = archive.read("[Content_Types].xml").decode("utf-8", "replace").lower()
    if (any(n.endswith("vbaproject.bin") for n in minuscules) or "macroenabled" in types or "vbaproject" in types
            or any("/activex/" in n for n in minuscules) or any("/embeddings/" in n for n in minuscules)):
        raise FormatRefuse("Fichier refusé : il contient des macros ou des objets actifs. Enregistrez-le sans macros (.docx/.xlsx) ou en PDF.")
    if "word/document.xml" in noms:
        return MIME_DOCX
    if "xl/workbook.xml" in noms:
        return MIME_XLSX
    raise FormatRefuse("Document bureautique non pris en charge : Word (.docx) ou Excel (.xlsx) uniquement.")


def extraire_texte_bureautique(data: bytes, mime: str) -> str:
    """Texte brut d'un .docx / .xlsx, pour la recherche (aucune exécution)."""
    import xml.etree.ElementTree as ET
    archive = zipfile.ZipFile(io.BytesIO(data))
    parties = []
    if mime == MIME_DOCX:
        noms = ["word/document.xml"] + sorted(n for n in archive.namelist() if n.startswith(("word/header", "word/footer")) and n.endswith(".xml"))
        balise = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"
        paragraphe = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"
        for nom in noms:
            racine = ET.fromstring(archive.read(nom))
            for p in racine.iter(paragraphe):
                ligne = "".join(t.text or "" for t in p.iter(balise))
                if ligne.strip():
                    parties.append(ligne)
    elif mime == MIME_XLSX:
        balise = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t"
        for nom in ["xl/sharedStrings.xml"] + sorted(n for n in archive.namelist() if n.startswith("xl/worksheets/") and n.endswith(".xml")):
            if nom in archive.namelist():
                racine = ET.fromstring(archive.read(nom))
                parties.extend(t.text for t in racine.iter(balise) if t.text and t.text.strip())
    return "\n".join(parties).strip()
