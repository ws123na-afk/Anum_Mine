"""Text for ANUM's PDF writer: Arabic shaping, right-to-left order and the embedded Arabic font.

The writer (``tyre/pdf.py``) needs no external library and downloads nothing. Latin text uses
the built-in Helvetica fonts (WinAnsi); Arabic uses Noto Sans Arabic, embedded from
``pdf_fonts/`` (SIL Open Font License, subset to Arabic, see ``pdf_fonts/SOURCE.txt``), only
when a document has Arabic in it. See docs/business.md#printing-and-pdf.

How a line of text becomes glyphs:

1. **Shaping.** Each Arabic letter takes its isolated, initial, medial or final form from the
   Unicode Arabic Presentation Forms (the joining rules of Unicode chapter 9.2: dual-joining
   and right-joining letters, harakat are transparent), and lam followed by an alef becomes
   the lam-alef ligature. The embedded font maps every presentation form to its glyph.
2. **Order.** A small implementation of the Unicode bidirectional algorithm (UAX 9: weak types
   W1 to W7, bracket pairs N0, neutrals N1 and N2, implicit levels I1 and I2, reordering L2 and mirrored
   brackets L4; no explicit embeddings, which ANUM's documents never contain) puts the
   characters in the order they are drawn. The paragraph direction comes from the first
   strong letter, or is given. Numbers stay left to right inside Arabic text.
3. **Runs.** The visual string is split into runs per font; the PDF shows each run in turn.

Copying text out of the PDF gives the Arabic letters back: the font's ToUnicode map turns every
presentation form into its plain letter (a lam-alef into the two letters).
"""

from __future__ import annotations

import struct
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

FONT_DIR = Path(__file__).with_name("pdf_fonts")
FONT_FILES = {False: "NotoSansArabic-PDF-Regular.ttf", True: "NotoSansArabic-PDF-Bold.ttf"}

# ---------------------------------------------------------------------------------------------
# Shaping: base letter -> (isolated, final, initial, medial); two forms = right-joining only.

_FORMS: dict[int, tuple[int, ...]] = {
    0x0621: (0xFE80,),
    0x0622: (0xFE81, 0xFE82), 0x0623: (0xFE83, 0xFE84), 0x0624: (0xFE85, 0xFE86), 0x0625: (0xFE87, 0xFE88),
    0x0626: (0xFE89, 0xFE8A, 0xFE8B, 0xFE8C), 0x0627: (0xFE8D, 0xFE8E), 0x0628: (0xFE8F, 0xFE90, 0xFE91, 0xFE92),
    0x0629: (0xFE93, 0xFE94), 0x062A: (0xFE95, 0xFE96, 0xFE97, 0xFE98), 0x062B: (0xFE99, 0xFE9A, 0xFE9B, 0xFE9C),
    0x062C: (0xFE9D, 0xFE9E, 0xFE9F, 0xFEA0), 0x062D: (0xFEA1, 0xFEA2, 0xFEA3, 0xFEA4), 0x062E: (0xFEA5, 0xFEA6, 0xFEA7, 0xFEA8),
    0x062F: (0xFEA9, 0xFEAA), 0x0630: (0xFEAB, 0xFEAC), 0x0631: (0xFEAD, 0xFEAE), 0x0632: (0xFEAF, 0xFEB0),
    0x0633: (0xFEB1, 0xFEB2, 0xFEB3, 0xFEB4), 0x0634: (0xFEB5, 0xFEB6, 0xFEB7, 0xFEB8), 0x0635: (0xFEB9, 0xFEBA, 0xFEBB, 0xFEBC),
    0x0636: (0xFEBD, 0xFEBE, 0xFEBF, 0xFEC0), 0x0637: (0xFEC1, 0xFEC2, 0xFEC3, 0xFEC4), 0x0638: (0xFEC5, 0xFEC6, 0xFEC7, 0xFEC8),
    0x0639: (0xFEC9, 0xFECA, 0xFECB, 0xFECC), 0x063A: (0xFECD, 0xFECE, 0xFECF, 0xFED0), 0x0641: (0xFED1, 0xFED2, 0xFED3, 0xFED4),
    0x0642: (0xFED5, 0xFED6, 0xFED7, 0xFED8), 0x0643: (0xFED9, 0xFEDA, 0xFEDB, 0xFEDC), 0x0644: (0xFEDD, 0xFEDE, 0xFEDF, 0xFEE0),
    0x0645: (0xFEE1, 0xFEE2, 0xFEE3, 0xFEE4), 0x0646: (0xFEE5, 0xFEE6, 0xFEE7, 0xFEE8), 0x0647: (0xFEE9, 0xFEEA, 0xFEEB, 0xFEEC),
    0x0648: (0xFEED, 0xFEEE), 0x0649: (0xFEEF, 0xFEF0), 0x064A: (0xFEF1, 0xFEF2, 0xFEF3, 0xFEF4),
    # Persian and Urdu letters used in names.
    0x067E: (0xFB56, 0xFB57, 0xFB58, 0xFB59), 0x0686: (0xFB7A, 0xFB7B, 0xFB7C, 0xFB7D), 0x0698: (0xFB8A, 0xFB8B),
    0x06A9: (0xFB8E, 0xFB8F, 0xFB90, 0xFB91), 0x06AF: (0xFB92, 0xFB93, 0xFB94, 0xFB95), 0x06CC: (0xFBFC, 0xFBFD, 0xFBFE, 0xFBFF),
}
_TATWEEL = 0x0640
_LAM = 0x0644
# Lam + alef variant -> (isolated, final) ligature.
_LAM_ALEF = {0x0622: (0xFEF5, 0xFEF6), 0x0623: (0xFEF7, 0xFEF8), 0x0625: (0xFEF9, 0xFEFA), 0x0627: (0xFEFB, 0xFEFC)}


def _transparent(code: int) -> bool:
    return unicodedata.category(chr(code)) == "Mn"


def _joins_left(code: int | None) -> bool:
    """Connects to the following letter (dual-joining or tatweel)."""
    return code is not None and (code == _TATWEEL or len(_FORMS.get(code, ())) == 4)


def _joins_right(code: int | None) -> bool:
    """Connects to the preceding letter (right- or dual-joining or tatweel)."""
    return code is not None and (code == _TATWEEL or len(_FORMS.get(code, ())) >= 2)


def shape(text: str) -> str:
    """Arabic letters in logical order -> their contextual presentation forms (still logical order)."""
    codes = [ord(c) for c in text]
    if not any(0x0600 <= c <= 0x06FF for c in codes):
        return text
    out: list[str] = []
    count = len(codes)

    def neighbour(index: int, step: int) -> int | None:
        index += step
        while 0 <= index < count:
            if not _transparent(codes[index]):
                return codes[index]
            index += step
        return None

    skip: set[int] = set()
    for index, code in enumerate(codes):
        if index in skip:
            continue
        forms = _FORMS.get(code)
        if forms is None:
            out.append(chr(code))
            continue
        previous = neighbour(index, -1)
        joins_previous = _joins_left(previous) and _joins_right(code)
        if code == _LAM:
            # The next non-mark letter; marks between lam and alef stay after the ligature.
            nxt = index + 1
            while nxt < count and _transparent(codes[nxt]):
                nxt += 1
            if nxt < count and codes[nxt] in _LAM_ALEF:
                isolated, final = _LAM_ALEF[codes[nxt]]
                out.append(chr(final if joins_previous else isolated))
                out.extend(chr(codes[k]) for k in range(index + 1, nxt))
                skip.update(range(index + 1, nxt + 1))
                continue
        joins_next = _joins_left(code) and _joins_right(neighbour(index, 1))
        if len(forms) == 4:
            form = forms[3] if joins_previous and joins_next else forms[1] if joins_previous else forms[2] if joins_next else forms[0]
        elif len(forms) == 2:
            form = forms[1] if joins_previous else forms[0]
        else:
            form = forms[0]
        out.append(chr(form))
    return "".join(out)


# ---------------------------------------------------------------------------------------------
# Bidirectional order (UAX 9 without explicit embeddings).

_MIRROR = {"(": ")", ")": "(", "[": "]", "]": "[", "{": "}", "}": "{", "<": ">", ">": "<", "«": "»", "»": "«"}
_OPENING = {"(": ")", "[": "]", "{": "}"}
_ES = set("+-")
_CS = set(",.:/ ،")
_ET = set("#$%¢£¤¥°٪‰€")


def is_rtl_char(char: str) -> bool:
    code = ord(char)
    if 0x0660 <= code <= 0x0669 or 0x06F0 <= code <= 0x06F9 or code in (0x060C, 0x066B, 0x066C):
        return False
    return (0x0590 <= code <= 0x08FF) or (0xFB1D <= code <= 0xFDFF) or (0xFE70 <= code <= 0xFEFF)


def _bidi_class(char: str) -> str:
    code = ord(char)
    if "0" <= char <= "9" or 0x06F0 <= code <= 0x06F9:
        return "EN"
    if 0x0660 <= code <= 0x0669 or code in (0x066B, 0x066C):
        return "AN"
    if unicodedata.category(char) == "Mn":
        return "NSM"
    if is_rtl_char(char):
        return "R"
    if char in _ES:
        return "ES"
    if char in _CS:
        return "CS"
    if char in _ET:
        return "ET"
    if unicodedata.category(char)[0] == "L":
        return "L"
    return "N"


def has_rtl(text: str) -> bool:
    return any(is_rtl_char(char) for char in text)


def base_is_rtl(text: str) -> bool:
    """The paragraph direction from the first strong letter (UAX 9 P2 and P3)."""
    for char in text:
        kind = _bidi_class(char)
        if kind == "L":
            return False
        if kind == "R":
            return True
    return False


def visual(text: str, rtl: bool | None = None) -> str:
    """``text`` (logical order, already shaped) in drawing order, left to right."""
    if not text:
        return text
    if rtl is None:
        rtl = base_is_rtl(text)
    if not rtl and not has_rtl(text) and not any(_bidi_class(c) == "AN" for c in text):
        return text
    base = 1 if rtl else 0
    sos = "R" if rtl else "L"
    types = [_bidi_class(c) for c in text]
    n = len(types)
    # W1: a mark takes the type of what it follows.
    for i in range(n):
        if types[i] == "NSM":
            types[i] = types[i - 1] if i else sos
    # W2: a European number after Arabic text is an Arabic number.
    last_strong = sos
    for i in range(n):
        if types[i] in ("L", "R"):
            last_strong = types[i]
        elif types[i] == "EN" and last_strong == "R":
            types[i] = "AN"
    # W4: one separator between two numbers of the same kind joins them.
    for i in range(1, n - 1):
        if types[i] == "ES" and types[i - 1] == "EN" and types[i + 1] == "EN":
            types[i] = "EN"
        elif types[i] == "CS" and types[i - 1] in ("EN", "AN") and types[i + 1] == types[i - 1]:
            types[i] = types[i - 1]
    # W5: terminators next to a European number belong to it.
    i = 0
    while i < n:
        if types[i] == "ET":
            j = i
            while j < n and types[j] == "ET":
                j += 1
            if (i > 0 and types[i - 1] == "EN") or (j < n and types[j] == "EN"):
                for k in range(i, j):
                    types[k] = "EN"
            i = j
        else:
            i += 1
    # W6: remaining separators and terminators are neutral.
    types = ["N" if t in ("ES", "CS", "ET") else t for t in types]
    # W7: a European number after Latin text (or at the start of a left-to-right line) is Latin.
    last_strong = sos
    for i in range(n):
        if types[i] in ("L", "R"):
            last_strong = types[i]
        elif types[i] == "EN" and last_strong == "L":
            types[i] = "L"
    # N0: a bracket pair takes the direction of what it holds, so "(Gulf)" stays whole in Arabic text.
    embedding = "R" if rtl else "L"

    def strong(kind: str) -> str | None:
        return "R" if kind in ("R", "EN", "AN") else "L" if kind == "L" else None

    stack: list[tuple[str, int]] = []
    pairs: list[tuple[int, int]] = []
    for i, char in enumerate(text):
        if types[i] != "N":
            continue
        if char in _OPENING:
            stack.append((_OPENING[char], i))
        elif char in _OPENING.values():
            for depth in range(len(stack) - 1, -1, -1):
                if stack[depth][0] == char:
                    pairs.append((stack[depth][1], i))
                    del stack[depth:]
                    break
    for opening, closing in sorted(pairs):
        inside = {strong(types[k]) for k in range(opening + 1, closing)} - {None}
        if embedding in inside:
            fill = embedding
        elif inside:
            before = next((strong(types[k]) for k in range(opening - 1, -1, -1) if strong(types[k])), sos)
            fill = before if before != embedding else embedding
        else:
            continue
        types[opening] = types[closing] = fill
    # N1, N2: neutrals between two of the same direction take it (numbers count as R), else the base.
    i = 0
    while i < n:
        if types[i] != "N":
            i += 1
            continue
        j = i
        while j < n and types[j] == "N":
            j += 1
        before = sos if i == 0 else ("R" if types[i - 1] in ("R", "EN", "AN") else "L")
        after = sos if j == n else ("R" if types[j] in ("R", "EN", "AN") else "L")
        fill = before if before == after else ("R" if rtl else "L")
        for k in range(i, j):
            types[k] = fill
        i = j
    # I1, I2: implicit levels.
    levels = []
    for kind in types:
        if base == 0:
            levels.append(1 if kind == "R" else 2 if kind in ("AN", "EN") else 0)
        else:
            levels.append(2 if kind in ("L", "EN", "AN") else 1)
    # L1: trailing whitespace takes the paragraph level.
    k = n - 1
    while k >= 0 and text[k].isspace():
        levels[k] = base
        k -= 1
    # L2: reverse every run at or above each level, from the highest to the lowest odd level.
    order = list(range(n))
    highest = max(levels)
    lowest_odd = min(level for level in levels if level % 2) if any(level % 2 for level in levels) else highest + 1
    for level in range(highest, lowest_odd - 1, -1):
        i = 0
        while i < n:
            if levels[order[i]] >= level:
                j = i
                while j < n and levels[order[j]] >= level:
                    j += 1
                order[i:j] = reversed(order[i:j])
                i = j
            else:
                i += 1
    # L4: brackets in right-to-left runs are mirrored.
    return "".join(_MIRROR.get(text[i], text[i]) if levels[i] % 2 else text[i] for i in order)


# ---------------------------------------------------------------------------------------------
# The embedded TrueType font.


@dataclass(frozen=True)
class TrueTypeFont:
    name: str
    data: bytes
    units_per_em: int
    bbox: tuple[int, int, int, int]
    ascent: int
    descent: int
    cap_height: int
    cmap: dict[int, int]
    advances: list[int]

    def glyph(self, code: int) -> int:
        return self.cmap.get(code, 0)

    def advance(self, glyph: int) -> float:
        """The glyph's advance in 1/1000 of the font size."""
        value = self.advances[glyph] if glyph < len(self.advances) else self.advances[-1]
        return value * 1000.0 / self.units_per_em

    def covers(self, char: str) -> bool:
        return ord(char) in self.cmap


def _tables(data: bytes) -> dict[str, tuple[int, int]]:
    count = struct.unpack(">H", data[4:6])[0]
    found = {}
    for index in range(count):
        tag, _, offset, length = struct.unpack(">4sIII", data[12 + 16 * index: 28 + 16 * index])
        found[tag.decode("latin-1")] = (offset, length)
    return found


def _cmap_format4(data: bytes, offset: int) -> dict[int, int]:
    segments = struct.unpack(">H", data[offset + 6: offset + 8])[0] // 2
    ends_at = offset + 14
    starts_at = ends_at + 2 * segments + 2
    deltas_at = starts_at + 2 * segments
    ranges_at = deltas_at + 2 * segments
    mapping: dict[int, int] = {}
    for seg in range(segments):
        end = struct.unpack(">H", data[ends_at + 2 * seg: ends_at + 2 * seg + 2])[0]
        start = struct.unpack(">H", data[starts_at + 2 * seg: starts_at + 2 * seg + 2])[0]
        delta = struct.unpack(">h", data[deltas_at + 2 * seg: deltas_at + 2 * seg + 2])[0]
        range_offset = struct.unpack(">H", data[ranges_at + 2 * seg: ranges_at + 2 * seg + 2])[0]
        if start == 0xFFFF:
            continue
        for code in range(start, end + 1):
            if range_offset == 0:
                glyph = (code + delta) & 0xFFFF
            else:
                where = ranges_at + 2 * seg + range_offset + 2 * (code - start)
                glyph = struct.unpack(">H", data[where: where + 2])[0]
                if glyph:
                    glyph = (glyph + delta) & 0xFFFF
            if glyph:
                mapping[code] = glyph
    return mapping


@lru_cache(maxsize=2)
def arabic_font(bold: bool = False) -> TrueTypeFont:
    data = (FONT_DIR / FONT_FILES[bold]).read_bytes()
    tables = _tables(data)
    head = tables["head"][0]
    units = struct.unpack(">H", data[head + 18: head + 20])[0]
    bbox = struct.unpack(">hhhh", data[head + 36: head + 44])
    hhea = tables["hhea"][0]
    ascent, descent = struct.unpack(">hh", data[hhea + 4: hhea + 8])
    metrics = struct.unpack(">H", data[hhea + 34: hhea + 36])[0]
    glyphs = struct.unpack(">H", data[tables["maxp"][0] + 4: tables["maxp"][0] + 6])[0]
    hmtx = tables["hmtx"][0]
    advances = [struct.unpack(">H", data[hmtx + 4 * i: hmtx + 4 * i + 2])[0] for i in range(metrics)]
    advances += [advances[-1]] * (glyphs - metrics)
    cmap_at = tables["cmap"][0]
    cmap: dict[int, int] = {}
    subtables = struct.unpack(">H", data[cmap_at + 2: cmap_at + 4])[0]
    for index in range(subtables):
        platform, encoding, offset = struct.unpack(">HHI", data[cmap_at + 4 + 8 * index: cmap_at + 12 + 8 * index])
        if (platform, encoding) in ((3, 1), (0, 3)) and struct.unpack(">H", data[cmap_at + offset: cmap_at + offset + 2])[0] == 4:
            cmap = _cmap_format4(data, cmap_at + offset)
            break
    cap_height = ascent
    if "OS/2" in tables:
        os2 = tables["OS/2"][0]
        version = struct.unpack(">H", data[os2: os2 + 2])[0]
        if version >= 2:
            cap_height = struct.unpack(">h", data[os2 + 88: os2 + 90])[0] or ascent
    name = "NotoSansArabic-Bold" if bold else "NotoSansArabic-Regular"
    return TrueTypeFont(name, data, units, bbox, ascent, descent, cap_height, cmap, advances)


def to_unicode(code: int) -> str:
    """What a shaped glyph means as text: a presentation form gives back its plain letter(s)."""
    return unicodedata.normalize("NFKC", chr(code)) if 0xFB50 <= code <= 0xFEFF else chr(code)


# ---------------------------------------------------------------------------------------------
# Helvetica widths (Adobe's AFM, 1/1000 of the size) for layout; other WinAnsi characters 556.

_HELVETICA = dict(zip(
    " !\"#$%&'()*+,-./0123456789:;<=>?@ABCDEFGHIJKLMNOPQRSTUVWXYZ[\\]^_`abcdefghijklmnopqrstuvwxyz{|}~",
    [278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278, 556, 556, 556, 556, 556, 556, 556, 556, 556, 556,
     278, 278, 584, 584, 584, 556, 1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778, 667, 778, 722, 667,
     611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556, 333, 556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833,
     556, 556, 556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584],
    strict=True,
))
_BOLD_SCALE = 1.06


def latin_width(char: str, bold: bool) -> float:
    return _HELVETICA.get(char, 556) * (_BOLD_SCALE if bold else 1.0)


def winansi(char: str) -> bytes:
    """One character in the built-in fonts' WinAnsi encoding; what it cannot show becomes "?"."""
    code = ord(char)
    if 32 <= code < 127:
        return bytes([code])
    if code in (0x2013, 0x2014, 0x2212):
        return b"-"
    if code in (0x2018, 0x2019):
        return b"'"
    if code in (0x201C, 0x201D):
        return b'"'
    if code == 0x00A0:
        return b" "
    try:
        encoded = char.encode("cp1252")
    except UnicodeEncodeError:
        return b"?"
    return encoded if len(encoded) == 1 and encoded[0] >= 0xA0 else b"?"


@dataclass(frozen=True)
class Run:
    """One stretch of a drawn line in one font: ``arabic`` runs carry glyph ids, Latin runs WinAnsi bytes."""

    arabic: bool
    content: bytes | tuple[int, ...]
    codes: tuple[int, ...]
    width: float  # in 1/1000 of the font size


def layout(text: str, bold: bool = False, rtl: bool | None = None) -> tuple[list[Run], float]:
    """The runs of one line, in drawing order, and its width in 1/1000 of the font size."""
    drawn = visual(shape(text), rtl)
    font = arabic_font(bold) if has_rtl(drawn) else None
    runs: list[Run] = []
    total = 0.0
    current: list[str] = []
    current_arabic = False

    def flush() -> None:
        nonlocal total
        if not current:
            return
        if current_arabic and font is not None:
            glyphs = tuple(font.glyph(ord(c)) for c in current)
            width = sum(font.advance(g) for g in glyphs)
            runs.append(Run(True, glyphs, tuple(ord(c) for c in current), width))
        else:
            width = sum(latin_width(c, bold) for c in current)
            runs.append(Run(False, b"".join(winansi(c) for c in current), tuple(ord(c) for c in current), width))
        total += width
        current.clear()

    for char in drawn:
        arabic = font is not None and ord(char) >= 0x0600 and font.covers(char)
        # A space between two Arabic words stays in the Arabic run.
        if char == " " and current and current_arabic:
            arabic = True
        if current and arabic != current_arabic:
            flush()
        current_arabic = arabic
        current.append(char)
    flush()
    return runs, total


def text_width(text: str, size: float, bold: bool = False) -> float:
    return layout(text, bold)[1] * size / 1000.0
