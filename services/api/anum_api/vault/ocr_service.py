"""The OCR service: local Tesseract in its own container (docs/documents.md#the-ocr-service).

``python -m anum_api.vault.ocr_service`` serves, on port 8091 of an internal network:

* ``GET /healthz``: 200 when Tesseract and its English and Arabic data are installed here,
  503 with the reason otherwise.
* ``POST /v1/ocr`` with ``Authorization: Bearer <ANUM_OCR_SERVICE_TOKEN>``, an image as the body
  (PNG, JPEG, WebP, TIFF or GIF, at most 15 MB and 40 megapixels) and an optional
  ``X-OCR-Languages`` header (``eng``, ``ara`` or ``eng+ara``, the default): answers
  ``{"engine", "languages", "lines": [{"text", "confidence"}]}``.

Why a separate container: an image decoder or OCR bug must not run next to the API's database
login, secrets key or provider keys. This process reads no ANUM settings and has no route to the
internet or to the database; its only secret is the token the API sends. Nothing is written to
disk (the image goes to Tesseract on stdin) and neither images nor text are logged: the log line is
the outcome and the line count. Calls run one at a time (``ANUM_OCR_CONCURRENCY``, default 1); a
call that waits longer than 30 seconds for a free slot is answered 429.

Open-source and local only (owner decision, 4 Nov 2026): Tesseract 5 with the ``eng`` and ``ara``
traineddata from the distribution; no paid or hosted document AI.
"""

from __future__ import annotations

import asyncio
import hmac
import io
import logging
import os
import re
import shutil
import subprocess  # nosec B404 - runs the fixed tesseract binary with fixed arguments
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

logger = logging.getLogger("anum.ocr_service")

MAX_BODY_BYTES = 15 * 1024 * 1024
MAX_PIXELS = 40_000_000
QUEUE_WAIT_SECONDS = 30.0
RUN_TIMEOUT_SECONDS = 90
MIN_TOKEN_CHARS = 32
LANGUAGES = {"eng", "ara", "eng+ara", "ara+eng"}
IMAGE_FORMATS = {"PNG", "JPEG", "WEBP", "TIFF", "GIF", "MPO"}


class OcrRefused(ValueError):
    pass


def tesseract_status(binary: str | None = None) -> tuple[bool, str]:
    path = binary or shutil.which("tesseract")
    if not path:
        return False, "Tesseract is not installed in this container."
    try:
        out = subprocess.run([path, "--list-langs"], capture_output=True, text=True, timeout=10, check=False)  # nosec B603
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"Tesseract did not start ({type(exc).__name__})."
    langs = set((out.stdout + out.stderr).split())
    missing = [lang for lang in ("eng", "ara") if lang not in langs]
    if missing:
        return False, "Tesseract language data missing: " + ", ".join(missing)
    return True, "ok"


def tesseract_version(binary: str | None = None) -> str:
    path = binary or shutil.which("tesseract") or "tesseract"
    try:
        out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=10, check=False)  # nosec B603
    except (OSError, subprocess.SubprocessError):
        return "tesseract"
    first = (out.stdout or out.stderr).splitlines()[:1]
    return first[0].strip()[:80] if first else "tesseract"


def prepare_image(data: bytes) -> bytes:
    """Decode, check and normalise an image for Tesseract: grayscale PNG, small scans enlarged."""
    from PIL import Image, ImageOps

    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:
        with Image.open(io.BytesIO(data)) as probe:
            if probe.format not in IMAGE_FORMATS:
                raise OcrRefused("Send a PNG, JPEG, WebP, TIFF or GIF image.")
            if probe.width * probe.height > MAX_PIXELS:
                raise OcrRefused("The image is larger than 40 megapixels.")
            probe.seek(0)
            image = ImageOps.exif_transpose(probe).convert("L")
    except OcrRefused:
        raise
    except Exception as exc:
        raise OcrRefused("The image could not be read.") from exc
    if image.width < 1200:
        factor = min(3.0, 1600 / max(1, image.width))
        image = image.resize((int(image.width * factor), int(image.height * factor)), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def lines_from_tsv(tsv: str) -> list[dict[str, Any]]:
    """Tesseract TSV to lines with their mean word confidence (0 to 100) and their words' boxes
    (pixels: ``left``, ``top``, ``width``, ``height``), which the statement reader lays out by position."""
    lines: dict[tuple[str, str, str, str], list[tuple[str, float, tuple[int, int, int, int]]]] = {}
    order: list[tuple[str, str, str, str]] = []
    for row in tsv.splitlines()[1:]:
        parts = row.split("\t")
        if len(parts) < 12 or parts[0] != "5":
            continue
        word = parts[11].strip()
        if not word:
            continue
        try:
            confidence = float(parts[10])
            box = (int(parts[6]), int(parts[7]), int(parts[8]), int(parts[9]))
        except ValueError:
            continue
        key = (parts[1], parts[2], parts[3], parts[4])
        if key not in lines:
            lines[key] = []
            order.append(key)
        lines[key].append((word, max(0.0, confidence), box))
    out = []
    for key in order:
        words = lines[key]
        out.append({
            "text": " ".join(word for word, _, _ in words)[:500],
            "confidence": round(sum(c for _, c, _ in words) / len(words), 1),
            "words": [{"text": word[:120], "confidence": round(c, 1), "left": b[0], "top": b[1], "width": b[2], "height": b[3]}
                      for word, c, b in words[:80]],
        })
    return out[:400]


def run_tesseract(png: bytes, languages: str, binary: str | None = None) -> list[dict[str, Any]]:
    path = binary or shutil.which("tesseract")
    if not path:
        raise OcrRefused("Tesseract is not installed in this container.")
    env = {"OMP_THREAD_LIMIT": "1", "PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    prefix = os.environ.get("TESSDATA_PREFIX")
    if prefix:
        env["TESSDATA_PREFIX"] = prefix
    completed = subprocess.run(  # nosec B603 - fixed binary and arguments, the image on stdin
        [path, "stdin", "stdout", "-l", languages, "--psm", "4", "tsv"],
        input=png, capture_output=True, timeout=RUN_TIMEOUT_SECONDS, check=False, env=env,
    )
    if completed.returncode != 0:
        raise OcrRefused("Tesseract could not read the image.")
    return lines_from_tsv(completed.stdout.decode("utf-8", errors="replace"))


class OcrService:
    def __init__(self, token: str, *, concurrency: int = 1, binary: str | None = None) -> None:
        if len(token) < MIN_TOKEN_CHARS:
            raise ValueError(f"ANUM_OCR_SERVICE_TOKEN must be at least {MIN_TOKEN_CHARS} characters")
        self._token = token.encode()
        self._slots = asyncio.Semaphore(max(1, concurrency))
        self.binary = binary

    def authorised(self, request: Request) -> bool:
        scheme, _, value = request.headers.get("authorization", "").partition(" ")
        return scheme.lower() == "bearer" and hmac.compare_digest(value.strip().encode(), self._token)

    async def healthz(self, _: Request) -> JSONResponse:
        ok, detail = await asyncio.to_thread(tesseract_status, self.binary)
        return JSONResponse({"status": "ok" if ok else "unavailable", "detail": detail}, status_code=200 if ok else 503)

    async def ocr(self, request: Request) -> JSONResponse:
        if not self.authorised(request):
            return JSONResponse({"detail": "Not authorised."}, status_code=401)
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
            return JSONResponse({"detail": "The image is larger than 15 MB."}, status_code=413)
        body = await request.body()
        if len(body) > MAX_BODY_BYTES:
            return JSONResponse({"detail": "The image is larger than 15 MB."}, status_code=413)
        languages = (request.headers.get("x-ocr-languages") or "eng+ara").strip()
        if languages not in LANGUAGES or not re.fullmatch(r"[a-z+]+", languages):
            return JSONResponse({"detail": "X-OCR-Languages must be eng, ara or eng+ara."}, status_code=422)
        try:
            await asyncio.wait_for(self._slots.acquire(), timeout=QUEUE_WAIT_SECONDS)
        except TimeoutError:
            return JSONResponse({"detail": "The OCR service is busy; try again shortly."}, status_code=429)
        try:
            png = await asyncio.to_thread(prepare_image, body)
            lines = await asyncio.to_thread(run_tesseract, png, languages, self.binary)
        except OcrRefused as exc:
            logger.info("ocr outcome=refused")
            return JSONResponse({"detail": str(exc)}, status_code=422)
        except subprocess.TimeoutExpired:
            logger.info("ocr outcome=timeout")
            return JSONResponse({"detail": "Reading the image took too long."}, status_code=504)
        finally:
            self._slots.release()
        version = await asyncio.to_thread(tesseract_version, self.binary)
        # Outcome and counts only: never the image or its text.
        logger.info("ocr outcome=ok lines=%s", len(lines))
        return JSONResponse({"engine": version, "languages": languages, "lines": lines})


def create_app(service: OcrService) -> Starlette:
    return Starlette(routes=[
        Route("/healthz", service.healthz, methods=["GET"]),
        Route("/v1/ocr", service.ocr, methods=["POST"]),
    ])


def app_from_environment() -> Starlette:
    return create_app(OcrService(
        os.environ.get("ANUM_OCR_SERVICE_TOKEN", ""),
        concurrency=int(os.environ.get("ANUM_OCR_CONCURRENCY", "1") or 1),
        binary=os.environ.get("ANUM_OCR_TESSERACT_PATH") or None,
    ))


def main() -> None:  # pragma: no cover - process entry point
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    uvicorn.run(app_from_environment(), host="0.0.0.0", port=int(os.environ.get("ANUM_OCR_PORT", "8091")), server_header=False)  # nosec B104: the internal network only


if __name__ == "__main__":  # pragma: no cover
    main()
