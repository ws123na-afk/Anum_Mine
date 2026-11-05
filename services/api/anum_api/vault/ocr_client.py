"""Reading a vault document's text for extraction (docs/documents.md#scanning-and-extraction).

* A PDF with a text layer: its own text, read in the API's document extraction pool
  (``doc_extract``), no OCR needed.
* An image (or a scanned PDF page sent as an image by the iPhone Shortcut): the OCR service, a
  separate container running local Tesseract (``vault/ocr_service.py``). The API sends the bytes
  and its token over the internal network and gets lines with confidences back.

Nothing here keeps the text: the caller turns the lines into proposed values (``ocr_parse``) and
stores those only. The text never reaches a model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit

import httpx

from ..settings import settings
from .ocr_parse import OcrLine, lines_from_text

NOT_CONNECTED = "OCR is not connected on this server, so scanned images cannot be read yet. A PDF with its own text still can."
IMAGE_TYPES = frozenset({"image/png", "image/jpeg", "image/webp", "image/gif", "image/tiff"})


class OcrUnavailable(Exception):
    """No OCR service, or it did not answer: the extraction is recorded as failed with this reason."""


@dataclass
class OcrText:
    lines: list[OcrLine]
    engine: str
    languages: str
    warnings: list[str] = field(default_factory=list)
    # Word boxes per line (pixels), when the service sends them: the statement reader's geometry.
    boxes: list[list[dict[str, float | str]]] = field(default_factory=list)


def service_url() -> str | None:
    url, token = settings.ocr_service_url.strip(), settings.ocr_service_token.strip()
    if not url or len(token) < 32:
        return None
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password or parts.query:
        return None
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))


def ocr_connected() -> bool:
    return service_url() is not None


async def read_text(content: bytes, content_type: str, *, client: httpx.AsyncClient | None = None) -> OcrText:
    """The lines of a document, from its PDF text layer or from the OCR service."""
    if content_type == "application/pdf":
        from ..doc_extract import DocumentFormat, ExtractionError, extract_document_async

        try:
            document = await extract_document_async(content, DocumentFormat.PDF)
        except ExtractionError as exc:
            raise OcrUnavailable(exc.message) from exc
        text = "\n".join(section.text for section in document.sections)
        if not text.strip():
            raise OcrUnavailable(
                "This PDF has no text of its own (it is a scan). Scan it as a photo with the iPhone Shortcut, or upload a picture of it."
            )
        return OcrText(lines_from_text(text)[:400], "pdf-text-layer", "", [])
    if content_type not in IMAGE_TYPES:
        raise OcrUnavailable("Only images and PDFs can be read.")
    base = service_url()
    if base is None:
        raise OcrUnavailable(NOT_CONNECTED)
    own = client is None
    http = client or httpx.AsyncClient(timeout=settings.ocr_timeout_seconds, follow_redirects=False, trust_env=False)
    try:
        response = await http.post(
            f"{base}/v1/ocr", content=content,
            headers={"Authorization": f"Bearer {settings.ocr_service_token.strip()}", "Content-Type": content_type, "X-OCR-Languages": "eng+ara"},
        )
    except (httpx.TimeoutException, TimeoutError) as exc:
        raise OcrUnavailable("The OCR service took too long. Try again.") from exc
    except httpx.HTTPError as exc:
        raise OcrUnavailable("The OCR service did not answer. Ask the operator to check the ocr container.") from exc
    finally:
        if own:
            await http.aclose()
    if response.status_code == 429:
        raise OcrUnavailable("The OCR service is busy. Try again in a minute.")
    if response.status_code == 401:
        raise OcrUnavailable("The OCR service refused the API's token. Ask the operator to check ANUM_OCR_SERVICE_TOKEN.")
    if response.status_code != 200:
        detail = ""
        try:
            detail = str(response.json().get("detail", ""))[:200]
        except ValueError:
            detail = ""
        raise OcrUnavailable(f"The OCR service could not read this image. {detail}".strip())
    try:
        body = response.json()
        rows = body.get("lines") or []
        kept = [row for row in rows[:400] if isinstance(row, dict)]
        lines = [OcrLine(str(row.get("text", ""))[:500], float(row.get("confidence", 0))) for row in kept]
        boxes = [[{"text": str(w.get("text", ""))[:120], "confidence": float(w.get("confidence", 0)), "left": float(w.get("left", 0)),
                   "top": float(w.get("top", 0)), "width": float(w.get("width", 0)), "height": float(w.get("height", 0))}
                  for w in (row.get("words") or [])[:80] if isinstance(w, dict)] for row in kept]
        return OcrText(lines, str(body.get("engine", "tesseract"))[:80], str(body.get("languages", ""))[:40], boxes=boxes)
    except (ValueError, TypeError, AttributeError) as exc:
        raise OcrUnavailable("The OCR service answered something unexpected.") from exc


__all__ = ["IMAGE_TYPES", "NOT_CONNECTED", "OcrText", "OcrUnavailable", "ocr_connected", "read_text"]
