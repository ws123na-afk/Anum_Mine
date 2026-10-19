"""Text extraction from PDF, DOCX, XLSX, PPTX, ODT, ODS and ODP files for retrieval
(docs/files.md#document-text-extraction).

Uploaded documents are hostile input. Extraction therefore:

* refuses input over :attr:`ExtractionLimits.max_input_bytes`;
* checks every OOXML (DOCX, XLSX, PPTX) and OpenDocument (ODT, ODS, ODP) zip before
  reading it: entry count, the total
  uncompressed size, the per-entry compression ratio (zip bombs) and the encryption flag,
  and reads each XML part with a bounded read. XML parts that declare a ``DOCTYPE`` are
  refused, so no entity can be declared or expanded (OOXML never uses one);
* refuses password-protected files: an encrypted Office file is an OLE container, not a
  zip, an encrypted OpenDocument file lists ``encryption-data`` in its manifest, and a
  PDF that does not open with the empty password is refused (a PDF that only carries
  permission flags opens with it and is read);
* reports a PDF whose pages carry no text layer (usually a scan) as ``no_text_layer``:
  there is no OCR, and the file says so instead of silently indexing nothing;
* stops reading after :attr:`max_pages` PDF pages or PPTX slides, :attr:`max_sheets`
  sheets, :attr:`max_rows_per_sheet` rows and :attr:`max_chars` characters, and marks the
  result ``truncated``;
* checks a wall-clock deadline between pages, slides, rows and paragraphs.

Callers in async code use :func:`extract_document_async`, which runs the work on a small
dedicated thread pool (never on the event loop, and never in the default executor that
database work shares) and gives up after the deadline. A running Python thread cannot be
killed, so the cooperative deadline inside the extractor is what ends a slow file; the
pool size bounds how many can run at once.

PDF parsing uses pypdf (BSD-3-Clause, pure Python), which caps every stream
decompression itself. The OOXML formats are read with ``zipfile`` and defusedxml
(PSF licence, pure Python; the standard library parser with DTDs and entities
forbidden), so no native XML library is involved.

Every extracted section carries its location (PDF page, slide, sheet, or the page Word
last laid out for a DOCX), which retrieval writes into each chunk's first line so answers
can cite file name and page (:func:`location_header`, :func:`parse_location`).
"""

from __future__ import annotations

import asyncio
import io
import posixpath
import re
import time
import zipfile
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import ParseError, iterparse

OLE_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


class DocumentFormat(StrEnum):
    PDF = "pdf"
    DOCX = "docx"
    XLSX = "xlsx"
    PPTX = "pptx"
    ODT = "odt"
    ODS = "ods"
    ODP = "odp"


DOCUMENT_SUFFIXES: dict[str, DocumentFormat] = {
    ".pdf": DocumentFormat.PDF,
    ".docx": DocumentFormat.DOCX,
    ".xlsx": DocumentFormat.XLSX,
    ".pptx": DocumentFormat.PPTX,
    ".odt": DocumentFormat.ODT,
    ".ods": DocumentFormat.ODS,
    ".odp": DocumentFormat.ODP,
}
DOCUMENT_CONTENT_TYPES: dict[str, DocumentFormat] = {
    "application/pdf": DocumentFormat.PDF,
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": DocumentFormat.DOCX,
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": DocumentFormat.XLSX,
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": DocumentFormat.PPTX,
    "application/vnd.oasis.opendocument.text": DocumentFormat.ODT,
    "application/vnd.oasis.opendocument.spreadsheet": DocumentFormat.ODS,
    "application/vnd.oasis.opendocument.presentation": DocumentFormat.ODP,
}


def document_format(name: str, content_type: str) -> DocumentFormat | None:
    """The document format of a file, by its extension; by content type only without one."""
    suffix = posixpath.splitext(name.lower())[1]
    if suffix:
        return DOCUMENT_SUFFIXES.get(suffix)
    return DOCUMENT_CONTENT_TYPES.get(content_type.split(";", 1)[0].strip().lower())


@dataclass(frozen=True)
class ExtractionLimits:
    max_input_bytes: int = 25 * 1024 * 1024
    max_pages: int = 300
    max_sheets: int = 50
    max_rows_per_sheet: int = 20_000
    max_chars: int = 500_000
    max_seconds: float = 20.0
    max_zip_entries: int = 2_000
    max_part_bytes: int = 32 * 1024 * 1024
    max_uncompressed_bytes: int = 128 * 1024 * 1024
    max_compression_ratio: int = 200


DEFAULT_LIMITS = ExtractionLimits()

# Why extraction refused a file; recorded as the index row's ``error``.
TOO_LARGE = "too_large"
TOO_COMPLEX = "too_complex"
ENCRYPTED = "encrypted"
MALFORMED = "malformed"
TIMEOUT = "timeout"
NO_TEXT_LAYER = "no_text_layer"


class ExtractionError(Exception):
    """A document that is not read, with a stable ``reason`` and a sentence for people."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


class LocationUnit(StrEnum):
    PAGE = "page"
    SLIDE = "slide"
    SHEET = "sheet"


@dataclass(frozen=True)
class Section:
    """The text of one page, slide or sheet (or of a whole DOCX without page marks)."""

    text: str
    number: int | None = None
    unit: LocationUnit | None = None
    label: str | None = None


@dataclass
class ExtractedDocument:
    format: DocumentFormat
    sections: list[Section] = field(default_factory=list)
    truncated: bool = False

    @property
    def char_count(self) -> int:
        return sum(len(section.text) for section in self.sections)


class _Budget:
    """The deadline and the character allowance of one extraction."""

    def __init__(self, limits: ExtractionLimits) -> None:
        self.limits = limits
        self.deadline = time.monotonic() + limits.max_seconds
        self.chars = 0
        self.truncated = False

    def check(self) -> None:
        if time.monotonic() > self.deadline:
            raise ExtractionError(TIMEOUT, "Reading this document took too long.")

    @property
    def full(self) -> bool:
        return self.chars >= self.limits.max_chars

    def take(self, text: str) -> str:
        """``text`` cut to what is left of the character allowance."""
        room = self.limits.max_chars - self.chars
        if len(text) > room:
            text = text[: max(room, 0)]
            self.truncated = True
        self.chars += len(text)
        return text


_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_BLANK_LINES = re.compile(r"\n{3,}")
_TRAILING_SPACE = re.compile(r"[ \t]+\n")


def _clean(text: str) -> str:
    text = _CONTROL.sub("", text.replace("\r\n", "\n").replace("\r", "\n"))
    return _BLANK_LINES.sub("\n\n", _TRAILING_SPACE.sub("\n", text)).strip()


# --------------------------------------------------------------------------- PDF


def _extract_pdf(data: bytes, budget: _Budget, result: ExtractedDocument) -> None:
    from pypdf import PasswordType, PdfReader

    reader = PdfReader(io.BytesIO(data), strict=False)
    if reader.is_encrypted:
        try:
            opened = reader.decrypt("")
        except Exception as exc:
            raise ExtractionError(ENCRYPTED, "This PDF is password protected.") from exc
        if opened == PasswordType.NOT_DECRYPTED:
            raise ExtractionError(ENCRYPTED, "This PDF is password protected.")
    page_count = len(reader.pages)
    for index in range(min(page_count, budget.limits.max_pages)):
        budget.check()
        if budget.full:
            budget.truncated = True
            return
        text = _clean(reader.pages[index].extract_text() or "")
        if text:
            result.sections.append(Section(budget.take(text), index + 1, LocationUnit.PAGE))
    if page_count > budget.limits.max_pages:
        budget.truncated = True
    if page_count and not result.sections:
        raise ExtractionError(
            NO_TEXT_LAYER, "This PDF has no extractable text (it may be a scan), and OCR is not available."
        )


# --------------------------------------------------------------------------- OOXML


class _Package:
    """A checked OOXML zip whose parts are read with bounded reads."""

    def __init__(self, data: bytes, limits: ExtractionLimits) -> None:
        if data.startswith(OLE_SIGNATURE):
            raise ExtractionError(ENCRYPTED, "This document is password protected.")
        try:
            self.zip = zipfile.ZipFile(io.BytesIO(data))
            infos = self.zip.infolist()
        except (zipfile.BadZipFile, zipfile.LargeZipFile, ValueError, OSError) as exc:
            raise ExtractionError(MALFORMED, "This document is damaged or is not an Office file.") from exc
        if len(infos) > limits.max_zip_entries:
            raise ExtractionError(TOO_COMPLEX, "This document has too many parts to read safely.")
        total = 0
        for info in infos:
            if info.flag_bits & 0x1:
                raise ExtractionError(ENCRYPTED, "This document is password protected.")
            total += info.file_size
            ratio = info.file_size / max(info.compress_size, 1)
            if info.file_size > 1024 * 1024 and ratio > limits.max_compression_ratio:
                raise ExtractionError(TOO_COMPLEX, "This document expands too much to read safely.")
        if total > limits.max_uncompressed_bytes:
            raise ExtractionError(TOO_COMPLEX, "This document expands too much to read safely.")
        self.names = {info.filename for info in infos}
        self.limits = limits

    def read(self, name: str) -> bytes | None:
        if name not in self.names:
            return None
        try:
            with self.zip.open(name) as part:
                data = part.read(self.limits.max_part_bytes + 1)
        except (zipfile.BadZipFile, ValueError, OSError, EOFError) as exc:
            raise ExtractionError(MALFORMED, "This document is damaged.") from exc
        if len(data) > self.limits.max_part_bytes:
            raise ExtractionError(TOO_COMPLEX, "A part of this document is too large to read safely.")
        return data

    def events(self, name: str, kinds: tuple[str, ...] = ("end",)) -> Iterator[tuple[str, _Element]]:
        """Parse events (``end`` by default) of one XML part (an absent part yields nothing)."""
        data = self.read(name)
        if data is None:
            return
        if _DECLARATION.search(data):
            raise ExtractionError(MALFORMED, "This document contains XML that is not allowed.")
        try:
            for event, element in iterparse(io.BytesIO(data), events=kinds, forbid_dtd=True):
                yield event, element
        except (ParseError, DefusedXmlException) as exc:
            raise ExtractionError(MALFORMED, "This document is damaged.") from exc

    def relationships(self, part: str) -> dict[str, str]:
        """Relationship id to the target part name, for ``part`` (``xl/workbook.xml``)."""
        folder, base = posixpath.split(part)
        targets: dict[str, str] = {}
        for _, element in self.events(posixpath.join(folder, "_rels", base + ".rels")):
            if _local(element.tag) != "Relationship" or element.get("TargetMode") == "External":
                continue
            target = element.get("Target") or ""
            resolved = target.lstrip("/") if target.startswith("/") else posixpath.join(folder, target)
            targets[element.get("Id") or ""] = posixpath.normpath(resolved)
        return targets


_DECLARATION = re.compile(rb"<!(?i:doctype|entity)")


# An xml.etree element; defusedxml builds the same type.
_Element = Any


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _attr(element: _Element, name: str) -> str | None:
    """An attribute by local name, whatever its namespace (transitional or strict OOXML)."""
    for key, value in element.attrib.items():
        if _local(key) == name:
            return value
    return None


def _relationship_id(element: _Element) -> str:
    """The namespaced ``r:id`` of an element (``sldId`` also has a plain ``id``)."""
    for key, value in element.attrib.items():
        if key.startswith("{") and _local(key) == "id":
            return value
    return ""


def _paragraph_text(paragraph: _Element) -> str:
    parts: list[str] = []
    for element in paragraph.iter():
        name = _local(element.tag)
        if name == "t" and element.text:
            parts.append(element.text)
        elif name == "tab":
            parts.append("\t")
        elif name in {"br", "cr"}:
            parts.append("\n")
    return "".join(parts)


def _extract_docx(package: _Package, budget: _Budget, result: ExtractedDocument) -> None:
    page = 1
    page_marks = 0
    lines: list[str] = []
    sections: list[Section] = []

    def flush() -> None:
        text = _clean("\n".join(lines))
        lines.clear()
        if text:
            sections.append(Section(budget.take(text), page, LocationUnit.PAGE))

    for _, element in package.events("word/document.xml"):
        name = _local(element.tag)
        if name != "p":
            continue
        budget.check()
        # Word records where it last broke pages; a manual page break counts too.
        breaks = sum(
            1
            for child in element.iter()
            if _local(child.tag) == "lastRenderedPageBreak"
            or (_local(child.tag) == "br" and _attr(child, "type") == "page")
        )
        if breaks:
            flush()
            page += breaks
            page_marks += breaks
        lines.append(_paragraph_text(element))
        element.clear()
        if budget.full:
            budget.truncated = True
            break
        if sum(len(line) for line in lines) > 50_000:
            flush()
    flush()
    if page_marks == 0:
        sections = [Section(section.text) for section in sections]
    result.sections.extend(sections)


def _extract_pptx(package: _Package, budget: _Budget, result: ExtractedDocument) -> None:
    relationships = package.relationships("ppt/presentation.xml")
    slides: list[str] = []
    for _, element in package.events("ppt/presentation.xml"):
        if _local(element.tag) == "sldId":
            target = relationships.get(_relationship_id(element))
            if target:
                slides.append(target)
    if len(slides) > budget.limits.max_pages:
        budget.truncated = True
    for number, slide in enumerate(slides[: budget.limits.max_pages], start=1):
        budget.check()
        if budget.full:
            budget.truncated = True
            return
        paragraphs: list[str] = []
        for _, element in package.events(slide):
            if _local(element.tag) == "p":
                paragraphs.append(_paragraph_text(element))
                element.clear()
        text = _clean("\n".join(paragraphs))
        if text:
            result.sections.append(Section(budget.take(text), number, LocationUnit.SLIDE))


def _shared_strings(package: _Package, budget: _Budget) -> list[str]:
    strings: list[str] = []
    for _, element in package.events("xl/sharedStrings.xml"):
        if _local(element.tag) == "si":
            # Phonetic runs (rPh) repeat the reading of East Asian text; leave them out.
            for child in list(element):
                if _local(child.tag) == "rPh":
                    element.remove(child)
            strings.append("".join(t.text or "" for t in element.iter() if _local(t.tag) == "t"))
            element.clear()
            if len(strings) % 1000 == 0:
                budget.check()
    return strings


def _cell_value(cell: _Element, strings: list[str]) -> str:
    kind = cell.get("t")
    if kind == "inlineStr":
        return "".join(t.text or "" for t in cell.iter() if _local(t.tag) == "t")
    value = next((child.text or "" for child in cell if _local(child.tag) == "v"), "")
    if kind == "s":
        try:
            return strings[int(value)]
        except (ValueError, IndexError):
            return ""
    if kind == "b":
        return "TRUE" if value == "1" else "FALSE"
    return value


def _extract_xlsx(package: _Package, budget: _Budget, result: ExtractedDocument) -> None:
    relationships = package.relationships("xl/workbook.xml")
    sheets: list[tuple[str, str]] = []
    for _, element in package.events("xl/workbook.xml"):
        if _local(element.tag) == "sheet":
            target = relationships.get(_relationship_id(element))
            if target:
                sheets.append((element.get("name") or "", target))
    if len(sheets) > budget.limits.max_sheets:
        budget.truncated = True
    strings = _shared_strings(package, budget)
    for number, (sheet_name, part) in enumerate(sheets[: budget.limits.max_sheets], start=1):
        budget.check()
        if budget.full:
            budget.truncated = True
            return
        rows: list[str] = []
        for _, element in package.events(part):
            if _local(element.tag) != "row":
                continue
            if len(rows) >= budget.limits.max_rows_per_sheet:
                budget.truncated = True
                break
            values = [_cell_value(cell, strings) for cell in element if _local(cell.tag) == "c"]
            element.clear()
            line = "\t".join(values).rstrip("\t")
            if line.strip():
                rows.append(line)
            if len(rows) % 500 == 0:
                budget.check()
        text = _clean("\n".join(rows))
        if text:
            result.sections.append(Section(budget.take(text), number, LocationUnit.SHEET, sheet_name[:100] or None))


# --------------------------------------------------------------------------- OpenDocument

# A repeated cell or row with content is expanded at most this many times (an empty
# repeat, often a million columns or rows, is never expanded).
ODF_MAX_REPEAT = 100


def _odf_text(element: _Element) -> str:
    """The text of one ``text:p`` or ``text:h``: mixed content with spaces, tabs and breaks."""
    parts: list[str] = [element.text or ""]
    for child in element:
        name = _local(child.tag)
        if name == "s":
            try:
                count = int(_attr(child, "c") or "1")
            except ValueError:
                count = 1
            parts.append(" " * max(1, min(count, 100)))
        elif name == "tab":
            parts.append("\t")
        elif name == "line-break":
            parts.append("\n")
        elif name not in {"annotation", "note-citation"}:
            parts.append(_odf_text(child))
        parts.append(child.tail or "")
    return "".join(parts)


def _odf_paragraphs(element: _Element) -> list[str]:
    """The paragraphs under ``element`` (a paragraph inside a paragraph is part of it)."""
    found: list[str] = []
    for child in element:
        name = _local(child.tag)
        if name in {"p", "h"}:
            found.append(_odf_text(child))
        elif name not in {"notes", "annotation"}:  # speaker notes and comments stay out
            found.extend(_odf_paragraphs(child))
    return found


def _odf_check(package: _Package) -> None:
    manifest = package.read("META-INF/manifest.xml") or b""
    if b"encryption-data" in manifest:
        raise ExtractionError(ENCRYPTED, "This document is password protected.")
    if "content.xml" not in package.names:
        raise ExtractionError(MALFORMED, "This document is damaged or is not an OpenDocument file.")


def _repeat(element: _Element, name: str) -> int:
    try:
        return max(1, int(_attr(element, name) or "1"))
    except ValueError:
        return 1


def _extract_odt(package: _Package, budget: _Budget, result: ExtractedDocument) -> None:
    _odf_check(package)
    page = 1
    page_marks = 0
    pending_breaks = 0
    lines: list[str] = []
    sections: list[Section] = []

    def flush() -> None:
        text = _clean("\n".join(lines))
        lines.clear()
        if text:
            sections.append(Section(budget.take(text), page, LocationUnit.PAGE))

    in_body = False
    for event, element in package.events("content.xml", ("start", "end")):
        name = _local(element.tag)
        if name == "text" and event == "start":
            in_body = True  # office:text, the document body
        if event != "end" or not in_body:
            continue
        if name == "soft-page-break":
            # Where LibreOffice last broke pages, like Word's lastRenderedPageBreak.
            pending_breaks += 1
        elif name in {"p", "h"}:
            budget.check()
            if pending_breaks:
                flush()
                page += pending_breaks
                page_marks += pending_breaks
                pending_breaks = 0
            lines.append(_odf_text(element))
            element.clear()
            if budget.full:
                budget.truncated = True
                break
            if sum(len(line) for line in lines) > 50_000:
                flush()
    flush()
    if page_marks == 0:
        sections = [Section(section.text) for section in sections]
    result.sections.extend(sections)


def _extract_ods(package: _Package, budget: _Budget, result: ExtractedDocument) -> None:
    _odf_check(package)
    rows: list[str] = []
    sheets = 0
    for _, element in package.events("content.xml"):
        name = _local(element.tag)
        if name == "table-row":
            if len(rows) >= budget.limits.max_rows_per_sheet:
                budget.truncated = True
                element.clear()
                continue
            row_repeat = min(_repeat(element, "number-rows-repeated"), ODF_MAX_REPEAT)
            values: list[str] = []
            for cell in element:
                if _local(cell.tag) not in {"table-cell", "covered-table-cell"}:
                    continue
                text = "\n".join(_odf_paragraphs(cell)).strip()
                repeat = min(_repeat(cell, "number-columns-repeated"), ODF_MAX_REPEAT) if text else 1
                values.extend([text] * repeat)
            element.clear()
            line = "\t".join(values).rstrip("\t")
            if line.strip():
                for _ in range(min(row_repeat, budget.limits.max_rows_per_sheet - len(rows))):
                    rows.append(line)
            if len(rows) % 500 == 0:
                budget.check()
        elif name == "table":
            budget.check()
            sheets += 1
            text = _clean("\n".join(rows))
            rows = []
            if sheets > budget.limits.max_sheets or budget.full:
                budget.truncated = True
                break
            if text:
                sheet_name = (_attr(element, "name") or "")[:100] or None
                result.sections.append(Section(budget.take(text), sheets, LocationUnit.SHEET, sheet_name))
            element.clear()


def _extract_odp(package: _Package, budget: _Budget, result: ExtractedDocument) -> None:
    _odf_check(package)
    slides = 0
    for _, element in package.events("content.xml"):
        if _local(element.tag) != "page":
            continue
        budget.check()
        slides += 1
        if slides > budget.limits.max_pages or budget.full:
            budget.truncated = True
            break
        text = _clean("\n".join(_odf_paragraphs(element)))
        element.clear()
        if text:
            result.sections.append(Section(budget.take(text), slides, LocationUnit.SLIDE))


# --------------------------------------------------------------------------- entry points


_POOL_WORKERS = 2
_pool = ThreadPoolExecutor(max_workers=_POOL_WORKERS, thread_name_prefix="anum-doc-extract")


def extract_document(
    data: bytes, fmt: DocumentFormat, limits: ExtractionLimits = DEFAULT_LIMITS
) -> ExtractedDocument:
    """Extract the text of one document, synchronously. Raises :class:`ExtractionError`."""
    if len(data) > limits.max_input_bytes:
        raise ExtractionError(TOO_LARGE, "This document is too large to read.")
    budget = _Budget(limits)
    result = ExtractedDocument(format=fmt)
    try:
        if fmt == DocumentFormat.PDF:
            _extract_pdf(data, budget, result)
        else:
            package = _Package(data, limits)
            {
                DocumentFormat.DOCX: _extract_docx,
                DocumentFormat.XLSX: _extract_xlsx,
                DocumentFormat.PPTX: _extract_pptx,
                DocumentFormat.ODT: _extract_odt,
                DocumentFormat.ODS: _extract_ods,
                DocumentFormat.ODP: _extract_odp,
            }[fmt](package, budget, result)
    except ExtractionError:
        raise
    except RecursionError as exc:
        raise ExtractionError(TOO_COMPLEX, "This document is nested too deeply to read safely.") from exc
    except MemoryError as exc:
        raise ExtractionError(TOO_COMPLEX, "This document is too large to read.") from exc
    except Exception as exc:
        # pypdf and the XML reader raise many types for damaged input; all mean the same.
        raise ExtractionError(MALFORMED, "This document is damaged or could not be read.") from exc
    result.truncated = budget.truncated
    return result


async def extract_document_async(
    data: bytes, fmt: DocumentFormat, limits: ExtractionLimits = DEFAULT_LIMITS
) -> ExtractedDocument:
    """:func:`extract_document` on the extraction pool, off the event loop."""
    if len(data) > limits.max_input_bytes:
        raise ExtractionError(TOO_LARGE, "This document is too large to read.")
    loop = asyncio.get_running_loop()
    future = loop.run_in_executor(_pool, extract_document, data, fmt, limits)
    # Queueing behind other extractions counts too; the extractor's own deadline ends the work.
    try:
        return await asyncio.wait_for(future, timeout=limits.max_seconds * (_POOL_WORKERS + 1))
    except TimeoutError as exc:
        raise ExtractionError(TIMEOUT, "Reading this document took too long.") from exc


# --------------------------------------------------------------------------- locations


@dataclass(frozen=True)
class Location:
    unit: LocationUnit
    number: int
    label: str | None = None

    def describe(self) -> str:
        text = f"{self.unit.value} {self.number}"
        return f"{text} ({self.label})" if self.label else text


def location_header(name: str, section: Section) -> str:
    """The first line of every chunk of a document: ``[report.pdf, page 3]``."""
    name = name.replace("\n", " ").replace("]", ")")[:200]
    if section.unit is None or section.number is None:
        return f"[{name}]"
    label = (section.label or "").replace("\n", " ").replace("]", ")")
    location = Location(section.unit, section.number, label or None)
    return f"[{name}, {location.describe()}]"


_HEADER = re.compile(
    r"\A\[(?P<name>[^\n]*?), (?P<unit>page|slide|sheet) (?P<number>\d{1,6})(?: \((?P<label>[^\n]*)\))?\]\n"
)
_PLAIN_HEADER = re.compile(r"\A\[[^\n]*\]\n")


def parse_location(content: str) -> tuple[Location | None, str]:
    """A chunk's location from its first line, and the chunk text without that line.

    The name in the header is never trusted for anything; only the unit and number are used.
    """
    match = _HEADER.match(content)
    if match:
        location = Location(LocationUnit(match["unit"]), int(match["number"]), match["label"] or None)
        return location, content[match.end():]
    return None, content


def strip_location(content: str, *, document: bool) -> str:
    """The chunk text without its location line (also the bare ``[name]`` line of a document)."""
    location, body = parse_location(content)
    if location is None and document:
        plain = _PLAIN_HEADER.match(content)
        if plain:
            return content[plain.end():]
    return body
