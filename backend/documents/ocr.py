"""Best-effort OCR adapters.

OCR is intentionally non-blocking for document preservation: a scan is kept
and marked for human indexing even when the locally configured OCR engine is
not available.  Text extracted here is only a search aid, never a substitute
for the human quality check.

Two extraction paths for PDF:
  1. Native text layer via ``pypdf`` — fast, no external binary, works for
     PDFs produced by word processors or "print to PDF".
  2. Image OCR via ``pdf2image`` (rasterise each page, needs the system
     ``poppler-utils`` package) + ``pytesseract`` — used as a fallback when
     the native layer is empty or near-empty, which is the case for a PDF
     coming out of a scanner (the page is one big embedded image).
Both optional dependencies degrade gracefully: if they are not installed,
the document is still kept and simply marked "indisponible" for search.
"""
from io import BytesIO
import os

# Below this many characters, a PDF's native text layer is treated as
# "effectively empty" (e.g. a lone header/footer added by the scanner
# software) and the OCR fallback is attempted.
_MIN_NATIVE_TEXT_CHARS = 20

# Hard cap on pages rasterised for OCR, so a very large scanned bundle
# cannot block the upload request for an unbounded time.
_MAX_OCR_PAGES = max(1, int(os.getenv("OCR_MAX_PAGES", "50")))


def _extract_pdf_native(data: bytes) -> str:
    from pypdf import PdfReader
    reader = PdfReader(BytesIO(data))
    return "\n".join(page.extract_text() or "" for page in reader.pages).strip()


def _extract_pdf_via_ocr(data: bytes) -> str:
    from pdf2image import convert_from_bytes
    import pytesseract

    dpi = max(100, min(int(os.getenv("OCR_DPI", "300")), 400))
    pages = convert_from_bytes(data, dpi=dpi, first_page=1, last_page=_MAX_OCR_PAGES)
    texts = []
    for image in pages[:_MAX_OCR_PAGES]:
        try:
            text = pytesseract.image_to_string(image, lang=os.getenv("OCR_LANG", "fra+eng"))
        except Exception:
            # If the configured language pack is missing, retry with Tesseract's
            # guaranteed base language instead of making the upload unusable.
            text = pytesseract.image_to_string(image, lang="eng")
        texts.append(text.strip())
    return "\n".join(t for t in texts if t).strip()


def _extract_pdf(data: bytes) -> tuple[str, str]:
    native_text = ""
    native_available = True
    try:
        native_text = _extract_pdf_native(data)
    except ImportError:
        native_available = False

    if len(native_text) >= _MIN_NATIVE_TEXT_CHARS:
        return native_text, "extrait"

    # Native layer empty (typical of a scanned PDF) or pypdf missing:
    # fall back to rasterising the pages and running OCR on the images.
    try:
        ocr_text = _extract_pdf_via_ocr(data)
    except ImportError:
        # pdf2image / pytesseract / poppler not installed on this machine.
        if native_text:
            return native_text, "extrait"
        return "", "indisponible" if native_available else "indisponible"
    except Exception:
        # poppler failed to render (corrupt/odd PDF) — keep whatever native
        # text we had rather than losing the document's searchability.
        if native_text:
            return native_text, "extrait"
        raise

    if ocr_text:
        return ocr_text, "extrait"
    if native_text:
        return native_text, "extrait"
    return "", "indisponible"


def extract_text(data: bytes, content_type: str) -> tuple[str, str]:
    """Return ``(text, status)``.  Optional dependencies keep a minimal
    installation usable while production can install the OCR tools."""
    if content_type == "application/pdf":
        return _extract_pdf(data)
    from .formats import MIMES_BUREAUTIQUES, extraire_texte_bureautique
    if content_type in MIMES_BUREAUTIQUES:
        texte = extraire_texte_bureautique(data, content_type)
        return texte, "extrait" if texte else "indisponible"
    if content_type.startswith("image/"):
        try:
            from PIL import Image
            import pytesseract
            image = Image.open(BytesIO(data))
            try:
                text = pytesseract.image_to_string(image, lang=os.getenv("OCR_LANG", "fra+eng"))
            except Exception:
                text = pytesseract.image_to_string(image, lang="eng")
            return text.strip(), "extrait"
        except ImportError:
            return "", "indisponible"
    return "", "indisponible"
