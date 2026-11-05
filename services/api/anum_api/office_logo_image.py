"""Logo images for office building signs: sniff, validate and re-encode (docs/ANUM_MINE_HQ_LOGO_SIGNS.md).

An owner uploads a PNG, JPEG or WebP. Nothing the client says is trusted: the real type comes from the leading
bytes AND from the decoder, the pixel count is checked from the header before any pixel is decoded (a small file
that declares a huge canvas is refused), animation is refused, and the picture is always re-encoded to a fresh
PNG of at most 512 x 512 pixels built from the decoded pixels only. Metadata (EXIF, GPS, ICC profiles, text
chunks, trailing bytes, embedded payloads) therefore never reaches storage. SVG is never accepted: it is a
document that can carry script.
"""
from __future__ import annotations

import hashlib
import io
import re
from dataclasses import dataclass
from typing import Literal

from PIL import Image, ImageOps

SignStyle = Literal["plain", "plaque", "illuminated", "banner"]
SIGN_STYLES: tuple[str, ...] = ("plain", "plaque", "illuminated", "banner")

MAX_UPLOAD_BYTES = 1_048_576
MAX_SIDE = 512
MIN_SIDE = 16
# Declared canvas limits, read from the header before decoding.
MAX_DECLARED_SIDE = 4096
MAX_DECLARED_PIXELS = 12_000_000
MAX_STORED_BYTES = 1_048_576

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


class LogoRefused(Exception):
    """The upload cannot be used. ``status`` is the HTTP status; ``message`` is one fixed English sentence."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass(frozen=True, slots=True)
class Logo:
    png: bytes
    sha256: str
    width: int
    height: int


def normalise_brand_color(value: str | None) -> str | None:
    """``#RRGGBB`` in lower case, or None. Anything else is a validation error."""
    if value is None:
        return None
    text = value.strip()
    if not _HEX.match(text):
        raise ValueError("Use a colour like #1f6f4a (a hash sign and six hexadecimal digits)")
    return text.lower()


def sniff(content: bytes) -> Literal["PNG", "JPEG", "WEBP"]:
    """The real format from the leading bytes, whatever the file name or header says."""
    if not content:
        raise LogoRefused(422, "The file is empty. Choose a logo picture.")
    if content[:8] == b"\x89PNG\r\n\x1a\n":
        return "PNG"
    if content[:3] == b"\xff\xd8\xff":
        return "JPEG"
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "WEBP"
    head = content[:512].lstrip().lower()
    if head.startswith((b"<?xml", b"<svg", b"<!doctype svg")) or b"<svg" in head:
        raise LogoRefused(415, "SVG logos are not accepted because they can carry script. Export the logo as PNG, JPEG or WebP.")
    if content[:6] in (b"GIF87a", b"GIF89a"):
        raise LogoRefused(415, "GIF logos are not accepted. Use a PNG, JPEG or WebP picture.")
    raise LogoRefused(415, "This file is not a PNG, JPEG or WebP picture. Choose a logo in one of those formats.")


def prepare(content: bytes) -> Logo:
    """Validate ``content`` and return the re-encoded logo. Raises :class:`LogoRefused`."""
    if not content:
        raise LogoRefused(422, "The file is empty. Choose a logo picture.")
    if len(content) > MAX_UPLOAD_BYTES:
        raise LogoRefused(413, "The logo is larger than 1 MB. Export a smaller picture (512 by 512 pixels is plenty).")
    expected = sniff(content)
    try:
        image = Image.open(io.BytesIO(content), formats=[expected])
        width, height = image.size
        if image.format != expected:
            raise LogoRefused(415, "The content of this file does not match a PNG, JPEG or WebP picture.")
        if width > MAX_DECLARED_SIDE or height > MAX_DECLARED_SIDE or width * height > MAX_DECLARED_PIXELS:
            raise LogoRefused(422, "The picture is too large (over 4096 pixels on a side). Export it at 512 by 512 pixels.")
        if getattr(image, "is_animated", False) or getattr(image, "n_frames", 1) > 1:
            raise LogoRefused(422, "Animated pictures are not accepted. Use a still PNG, JPEG or WebP picture.")
        if width < MIN_SIDE or height < MIN_SIDE:
            raise LogoRefused(422, "The picture is too small. Use at least 16 by 16 pixels, 256 by 256 or more looks best.")
        image.load()
        image = ImageOps.exif_transpose(image) or image
        rgba = image.convert("RGBA")
        rgba.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
        # A fresh image from the decoded pixels: no info dict, no profile, no text chunk, no trailing bytes.
        clean = Image.new("RGBA", rgba.size)
        clean.paste(rgba)
        buffer = io.BytesIO()
        clean.save(buffer, format="PNG", optimize=True)
    except LogoRefused:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise LogoRefused(422, "The picture is too large (over 4096 pixels on a side). Export it at 512 by 512 pixels.") from None
    except Exception:  # noqa: BLE001  corrupt, truncated or hostile input: one fixed sentence, never the decoder text
        raise LogoRefused(422, "This picture could not be read. It may be damaged. Export it again as PNG, JPEG or WebP.") from None
    png = buffer.getvalue()
    if len(png) > MAX_STORED_BYTES:
        raise LogoRefused(422, "The picture is too detailed to store. Export a simpler version of the logo.")
    if clean.getbbox() is None and rgba.getchannel("A").getextrema() == (0, 0):
        raise LogoRefused(422, "The picture is fully transparent. Choose a logo that shows something.")
    return Logo(png=png, sha256=hashlib.sha256(png).hexdigest(), width=clean.width, height=clean.height)
