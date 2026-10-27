"""Logo image validation and re-encoding (anum_api/office_logo_image.py): pure unit tests, no database."""
from __future__ import annotations

import io
import struct
import zlib

import pytest
from PIL import Image

from anum_api import office_logo_image as imaging


def png(size=(64, 64), color=(10, 120, 70, 255), mode="RGBA", **save) -> bytes:
    buffer = io.BytesIO()
    Image.new(mode, size, color).save(buffer, "PNG", **save)
    return buffer.getvalue()


def refused(content: bytes) -> imaging.LogoRefused:
    with pytest.raises(imaging.LogoRefused) as caught:
        imaging.prepare(content)
    return caught.value


def test_png_jpeg_and_webp_are_accepted_and_come_out_as_png() -> None:
    for fmt in ("PNG", "JPEG", "WEBP"):
        buffer = io.BytesIO()
        Image.new("RGB", (120, 80), (200, 30, 30)).save(buffer, fmt)
        logo = imaging.prepare(buffer.getvalue())
        assert logo.png[:8] == b"\x89PNG\r\n\x1a\n"
        assert (logo.width, logo.height) == (120, 80)
        assert len(logo.sha256) == 64


def test_large_pictures_are_scaled_to_at_most_512_keeping_the_ratio() -> None:
    logo = imaging.prepare(png((2000, 1000)))
    assert (logo.width, logo.height) == (512, 256)
    assert Image.open(io.BytesIO(logo.png)).size == (512, 256)


def test_the_stored_png_is_rebuilt_from_pixels_only() -> None:
    # EXIF with GPS in a JPEG, a text chunk and trailing bytes after a PNG: none of it survives.
    exif = Image.Exif()
    exif[0x010F] = "SecretCamera"
    exif[0x8825] = {1: "N", 2: (25.0, 12.0, 0.0)}
    jpg = io.BytesIO()
    Image.new("RGB", (64, 64), (1, 2, 3)).save(jpg, "JPEG", exif=exif)
    out = imaging.prepare(jpg.getvalue()).png
    assert b"SecretCamera" not in out and b"Exif" not in out
    assert not Image.open(io.BytesIO(out)).getexif()

    tail = png() + b"<script>alert(1)</script>PAYLOAD"
    clean = imaging.prepare(tail).png
    assert b"PAYLOAD" not in clean and b"<script" not in clean

    marked = io.BytesIO()
    from PIL.PngImagePlugin import PngInfo

    info = PngInfo()
    info.add_text("Comment", "hidden-note")
    Image.new("RGBA", (64, 64), (1, 2, 3, 255)).save(marked, "PNG", pnginfo=info)
    assert b"hidden-note" not in imaging.prepare(marked.getvalue()).png


def test_svg_gif_and_unknown_content_are_refused_by_content_not_by_name() -> None:
    assert refused(b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"/>').status == 415
    assert refused(b"<svg onload=alert(1)></svg>").status == 415
    gif = io.BytesIO()
    Image.new("P", (20, 20)).save(gif, "GIF")
    assert refused(gif.getvalue()).status == 415
    assert refused(b"MZ\x90\x00 not a picture").status == 415
    assert refused(b"").status == 422


def test_more_than_one_megabyte_is_refused_before_decoding() -> None:
    error = refused(b"\x89PNG\r\n\x1a\n" + b"0" * (imaging.MAX_UPLOAD_BYTES + 1))
    assert error.status == 413


def test_corrupt_and_truncated_files_get_one_fixed_sentence() -> None:
    good = png((200, 200))
    for broken in (good[: len(good) // 2], good[:40] + b"\x00" * 200, b"\x89PNG\r\n\x1a\n" + b"junk" * 50):
        error = refused(broken)
        assert error.status == 422 and "could not be read" in error.message
        assert "PIL" not in error.message and "Traceback" not in error.message


def test_a_small_file_that_declares_a_huge_canvas_is_refused_without_decoding_it() -> None:
    # 1-bit 30000 x 30000 compresses to a few tens of kilobytes but would be 900 million pixels.
    bomb = io.BytesIO()
    Image.new("1", (30000, 30000), 0).save(bomb, "PNG", optimize=False, compress_level=9)
    assert len(bomb.getvalue()) < imaging.MAX_UPLOAD_BYTES
    assert refused(bomb.getvalue()).status == 422

    # A hand-written header that claims 60000 x 60000 and carries no usable data.
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = chunk(b"IHDR", struct.pack(">IIBBBBB", 60000, 60000, 8, 6, 0, 0, 0))
    forged = b"\x89PNG\r\n\x1a\n" + header + chunk(b"IDAT", zlib.compress(b"\x00")) + chunk(b"IEND", b"")
    assert refused(forged).status == 422


def test_animated_pictures_are_refused() -> None:
    frames = [Image.new("RGBA", (32, 32), (i * 40, 0, 0, 255)) for i in range(3)]
    apng = io.BytesIO()
    frames[0].save(apng, "PNG", save_all=True, append_images=frames[1:], duration=50)
    assert "Animated" in refused(apng.getvalue()).message
    webp = io.BytesIO()
    frames[0].save(webp, "WEBP", save_all=True, append_images=frames[1:], duration=50)
    assert "Animated" in refused(webp.getvalue()).message


def test_tiny_and_fully_transparent_pictures_are_refused() -> None:
    assert "too small" in refused(png((8, 8))).message
    assert "transparent" in refused(png((64, 64), (0, 0, 0, 0))).message


def test_the_rotation_flag_of_a_phone_photo_is_applied_then_dropped() -> None:
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90 degrees: a 120 x 60 picture is shown 60 x 120
    jpg = io.BytesIO()
    Image.new("RGB", (120, 60), (9, 9, 9)).save(jpg, "JPEG", exif=exif)
    logo = imaging.prepare(jpg.getvalue())
    assert (logo.width, logo.height) == (60, 120)


@pytest.mark.parametrize("value,expected", [("#1F6F4A", "#1f6f4a"), ("  #abcdef ", "#abcdef"), (None, None)])
def test_brand_colour_is_normalised(value, expected) -> None:
    assert imaging.normalise_brand_color(value) == expected


@pytest.mark.parametrize("value", ["red", "#fff", "#12345g", "1f6f4a", "#1f6f4a00", "javascript:1", ""])
def test_brand_colour_rejects_anything_but_six_hex_digits(value) -> None:
    with pytest.raises(ValueError):
        imaging.normalise_brand_color(value)
