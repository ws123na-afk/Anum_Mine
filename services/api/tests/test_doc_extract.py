"""Text extraction from PDF, DOCX, XLSX and PPTX for retrieval (docs/files.md#retrieval).

Every fixture is built here, in memory: tiny hand-written PDFs and OOXML zips, plus the
hostile cases (a zip bomb, an entity declaration, encrypted files, damaged bytes,
oversized input and a slow file).
"""

from __future__ import annotations

import asyncio
import io
import threading
import zipfile
from collections.abc import Iterator
from xml.sax.saxutils import escape

import pytest
from fastapi.testclient import TestClient

from anum_api import doc_extract
from anum_api.dependencies import memory_note_repository
from anum_api.doc_extract import (
    DocumentFormat,
    ExtractionError,
    ExtractionLimits,
    LocationUnit,
    Section,
    document_format,
    extract_document,
    extract_document_async,
    location_header,
    parse_location,
    strip_location,
)
from anum_api.files import file_store
from anum_api.knowledge_base import knowledge_store
from anum_api.main import app
from anum_api.model_gateway import LOCAL_EMBEDDING_MODEL, local_embedding
from anum_api.retrieval import (
    MAX_CHUNKS_PER_SOURCE,
    IndexStatus,
    InMemoryRetrievalStore,
    LocalEmbedder,
    RetrievalIndexer,
    SourceType,
    memory_retrieval_store,
)
from anum_api.schemas import TenantContext, utc_now

# --------------------------------------------------------------------------- fixtures


def make_pdf(pages: list[str]) -> bytes:
    """A minimal valid PDF with one line of Helvetica text per page."""
    count = len(pages)
    font_id = 3 + 2 * count
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids ["
        + b" ".join(f"{3 + 2 * i} 0 R".encode() for i in range(count))
        + f"] /Count {count} >>".encode(),
    ]
    for index, text in enumerate(pages):
        content_id = 4 + 2 * index
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {content_id} 0 R "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> >>".encode()
        )
        safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = f"BT /F1 12 Tf 72 720 Td ({safe}) Tj ET".encode()
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets:
        out.write(f"{offset:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


def make_zip(parts: dict[str, str | bytes], *, encrypted_flag: bool = False) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in parts.items():
            info = zipfile.ZipInfo(name)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, body.encode() if isinstance(body, str) else body)
    data = bytearray(out.getvalue())
    if encrypted_flag:
        # zipfile never writes the flag itself: set bit 0 in the central directory entry.
        central = data.index(b"PK\x01\x02")
        data[central + 8] |= 0x1
    return bytes(data)


W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
A = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
P = 'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
S = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
R = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
RELS = 'xmlns="http://schemas.openxmlformats.org/package/2006/relationships"'


def make_docx(paragraphs: list[str], *, page_break_before: int | None = None) -> bytes:
    body = []
    for index, text in enumerate(paragraphs):
        mark = '<w:r><w:lastRenderedPageBreak/></w:r>' if index == page_break_before else ""
        body.append(f"<w:p>{mark}<w:r><w:t>{escape(text)}</w:t></w:r></w:p>")
    document = f'<?xml version="1.0" encoding="UTF-8"?><w:document {W}><w:body>{"".join(body)}</w:body></w:document>'
    return make_zip({"[Content_Types].xml": "<Types/>", "word/document.xml": document})


def make_pptx(slides: list[str]) -> bytes:
    ids = "".join(f'<p:sldId id="{256 + i}" r:id="rId{i + 1}"/>' for i in range(len(slides)))
    rels = "".join(
        f'<Relationship Id="rId{i + 1}" Type="slide" Target="slides/slide{i + 1}.xml"/>' for i in range(len(slides))
    )
    parts: dict[str, str | bytes] = {
        "[Content_Types].xml": "<Types/>",
        "ppt/presentation.xml": f"<p:presentation {P} {R}><p:sldIdLst>{ids}</p:sldIdLst></p:presentation>",
        "ppt/_rels/presentation.xml.rels": f"<Relationships {RELS}>{rels}</Relationships>",
    }
    for index, text in enumerate(slides):
        parts[f"ppt/slides/slide{index + 1}.xml"] = (
            f"<p:sld {P} {A}><p:cSld><p:spTree><p:sp><p:txBody>"
            f"<a:p><a:r><a:t>{escape(text)}</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>"
        )
    return make_zip(parts)


def make_xlsx(sheets: dict[str, list[list[str | int]]]) -> bytes:
    shared: list[str] = []
    parts: dict[str, str | bytes] = {"[Content_Types].xml": "<Types/>"}
    entries, rels = [], []
    for number, (name, rows) in enumerate(sheets.items(), start=1):
        entries.append(f'<sheet name="{escape(name)}" sheetId="{number}" r:id="rId{number}"/>')
        rels.append(f'<Relationship Id="rId{number}" Type="worksheet" Target="worksheets/sheet{number}.xml"/>')
        xml_rows = []
        for row in rows:
            cells = []
            for value in row:
                if isinstance(value, int):
                    cells.append(f"<c><v>{value}</v></c>")
                else:
                    shared.append(value)
                    cells.append(f'<c t="s"><v>{len(shared) - 1}</v></c>')
            xml_rows.append(f"<row>{''.join(cells)}</row>")
        parts[f"xl/worksheets/sheet{number}.xml"] = f"<worksheet {S}><sheetData>{''.join(xml_rows)}</sheetData></worksheet>"
    parts["xl/workbook.xml"] = f"<workbook {S} {R}><sheets>{''.join(entries)}</sheets></workbook>"
    parts["xl/_rels/workbook.xml.rels"] = f"<Relationships {RELS}>{''.join(rels)}</Relationships>"
    parts["xl/sharedStrings.xml"] = f"<sst {S}>" + "".join(f"<si><t>{escape(s)}</t></si>" for s in shared) + "</sst>"
    return make_zip(parts)


def refused(data: bytes, fmt: DocumentFormat, limits: ExtractionLimits | None = None) -> str:
    with pytest.raises(ExtractionError) as caught:
        extract_document(data, fmt, limits or ExtractionLimits())
    assert caught.value.message  # a sentence for people, never empty
    return caught.value.reason


# --------------------------------------------------------------------------- formats


def test_format_is_chosen_by_extension_then_content_type() -> None:
    assert document_format("Report.PDF", "application/octet-stream") == DocumentFormat.PDF
    assert document_format("plan.docx", "") == DocumentFormat.DOCX
    assert document_format("notes.txt", "application/pdf") is None  # the extension decides
    assert document_format("upload", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet") == DocumentFormat.XLSX
    assert document_format("legacy.doc", "application/msword") is None


def test_pdf_text_per_page() -> None:
    document = extract_document(make_pdf(["Load index 91 carries 615 kg.", "Speed rating H is 210 km/h."]), DocumentFormat.PDF)
    assert [(s.number, s.unit) for s in document.sections] == [(1, LocationUnit.PAGE), (2, LocationUnit.PAGE)]
    assert "615 kg" in document.sections[0].text and "210 km/h" in document.sections[1].text
    assert document.truncated is False


def test_docx_paragraphs_with_and_without_page_marks() -> None:
    plain = extract_document(make_docx(["Winter tyres", "Fit them from November."]), DocumentFormat.DOCX)
    assert len(plain.sections) == 1 and plain.sections[0].number is None
    assert plain.sections[0].text == "Winter tyres\nFit them from November."
    paged = extract_document(make_docx(["First page", "Second page"], page_break_before=1), DocumentFormat.DOCX)
    assert [(s.number, s.text) for s in paged.sections] == [(1, "First page"), (2, "Second page")]


def test_pptx_slides_in_presentation_order() -> None:
    document = extract_document(make_pptx(["Quarterly review", "Costs & margins"]), DocumentFormat.PPTX)
    assert [(s.number, s.unit, s.text) for s in document.sections] == [
        (1, LocationUnit.SLIDE, "Quarterly review"),
        (2, LocationUnit.SLIDE, "Costs & margins"),
    ]


def test_xlsx_sheets_rows_and_shared_strings() -> None:
    document = extract_document(
        make_xlsx({"Budget": [["Item", "Cost"], ["Tyres", 4200]], "Notes": [["Approved"]]}), DocumentFormat.XLSX
    )
    assert [(s.number, s.unit, s.label) for s in document.sections] == [
        (1, LocationUnit.SHEET, "Budget"),
        (2, LocationUnit.SHEET, "Notes"),
    ]
    assert document.sections[0].text == "Item\tCost\nTyres\t4200"


def test_page_sheet_row_and_character_limits_truncate() -> None:
    pdf = extract_document(make_pdf(["one", "two", "three"]), DocumentFormat.PDF, ExtractionLimits(max_pages=2))
    assert [s.number for s in pdf.sections] == [1, 2] and pdf.truncated
    slides = extract_document(make_pptx(["a", "b", "c"]), DocumentFormat.PPTX, ExtractionLimits(max_pages=1))
    assert len(slides.sections) == 1 and slides.truncated
    xlsx = make_xlsx({"A": [["x"]], "B": [["y"]], "C": [["z"]]})
    sheets = extract_document(xlsx, DocumentFormat.XLSX, ExtractionLimits(max_sheets=2))
    assert [s.label for s in sheets.sections] == ["A", "B"] and sheets.truncated
    rows = extract_document(
        make_xlsx({"A": [[f"row {n}"] for n in range(10)]}), DocumentFormat.XLSX, ExtractionLimits(max_rows_per_sheet=3)
    )
    assert rows.sections[0].text.count("\n") == 2 and rows.truncated
    chars = extract_document(make_docx(["x" * 500]), DocumentFormat.DOCX, ExtractionLimits(max_chars=100))
    assert chars.char_count == 100 and chars.truncated


# --------------------------------------------------------------------------- hostile input


def test_zip_bomb_is_refused() -> None:
    # 4 MiB of one byte compresses about a thousand to one: far over the ratio limit.
    bomb = make_zip({"word/document.xml": b"<w:document>" + b" " * (4 * 1024 * 1024) + b"</w:document>"})
    assert len(bomb) < 64 * 1024
    assert refused(bomb, DocumentFormat.DOCX) == "too_complex"
    # The total uncompressed size counts too, whatever the ratio.
    assert refused(make_docx(["hello"] * 50), DocumentFormat.DOCX, ExtractionLimits(max_uncompressed_bytes=1024)) == "too_complex"
    many = make_zip({f"part{n}.xml": "<x/>" for n in range(30)})
    assert refused(many, DocumentFormat.DOCX, ExtractionLimits(max_zip_entries=10)) == "too_complex"


def test_entity_declarations_are_refused() -> None:
    laughs = (
        '<?xml version="1.0"?><!DOCTYPE w [<!ENTITY a "lol"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;">]>'
        f"<w:document {W}><w:body><w:p><w:r><w:t>&b;</w:t></w:r></w:p></w:body></w:document>"
    )
    assert refused(make_zip({"word/document.xml": laughs}), DocumentFormat.DOCX) == "malformed"


def test_encrypted_files_are_refused() -> None:
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.encrypt(user_password="secret", owner_password="owner", algorithm="RC4-128")
    locked = io.BytesIO()
    writer.write(locked)
    assert refused(locked.getvalue(), DocumentFormat.PDF) == "encrypted"
    # An Office "encrypt with password" file is an OLE container, not a zip.
    assert refused(doc_extract.OLE_SIGNATURE + b"\x00" * 512, DocumentFormat.XLSX) == "encrypted"
    # A zip entry with the encryption flag set.
    assert refused(make_zip({"word/document.xml": "<x/>"}, encrypted_flag=True), DocumentFormat.DOCX) == "encrypted"


def test_pdf_with_only_permission_flags_is_read() -> None:
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(make_pdf(["Open to read"]))))
    writer.encrypt(user_password="", owner_password="owner", algorithm="RC4-128")
    out = io.BytesIO()
    writer.write(out)
    document = extract_document(out.getvalue(), DocumentFormat.PDF)
    assert "Open to read" in document.sections[0].text


@pytest.mark.parametrize("fmt", list(DocumentFormat))
def test_damaged_files_are_refused(fmt: DocumentFormat) -> None:
    assert refused(b"%PDF-1.7 this is not really a PDF" if fmt == DocumentFormat.PDF else b"PK\x03\x04garbage", fmt) == "malformed"


def test_damaged_xml_and_oversized_parts_are_refused() -> None:
    assert refused(make_zip({"word/document.xml": "<w:document><w:p>"}), DocumentFormat.DOCX) == "malformed"
    big = make_docx(["y" * 4000])
    assert refused(big, DocumentFormat.DOCX, ExtractionLimits(max_part_bytes=1000)) == "too_complex"


def test_oversized_input_is_refused_before_parsing() -> None:
    assert refused(make_pdf(["x"]), DocumentFormat.PDF, ExtractionLimits(max_input_bytes=100)) == "too_large"
    with pytest.raises(ExtractionError) as caught:
        asyncio.run(extract_document_async(b"%PDF" + b"0" * 200, DocumentFormat.PDF, ExtractionLimits(max_input_bytes=100)))
    assert caught.value.reason == "too_large"


def test_deadline_stops_a_slow_document() -> None:
    assert refused(make_pdf(["slow"]), DocumentFormat.PDF, ExtractionLimits(max_seconds=0.0)) == "timeout"
    assert refused(make_xlsx({"A": [["x"]]}), DocumentFormat.XLSX, ExtractionLimits(max_seconds=0.0)) == "timeout"


def test_async_extraction_runs_on_the_extraction_pool_not_the_loop() -> None:
    seen: list[str] = []
    original = doc_extract.extract_document

    def spy(data: bytes, fmt: DocumentFormat, limits: ExtractionLimits) -> doc_extract.ExtractedDocument:
        seen.append(threading.current_thread().name)
        return original(data, fmt, limits)

    async def run() -> doc_extract.ExtractedDocument:
        loop_thread = threading.current_thread().name
        doc_extract.extract_document = spy  # type: ignore[assignment]
        try:
            document = await extract_document_async(make_pptx(["Off the loop"]), DocumentFormat.PPTX)
        finally:
            doc_extract.extract_document = original  # type: ignore[assignment]
        assert seen and seen[0] != loop_thread
        return document

    document = asyncio.run(run())
    assert seen[0].startswith("anum-doc-extract") and document.sections[0].text == "Off the loop"


# --------------------------------------------------------------------------- locations


def test_location_header_round_trip() -> None:
    header = location_header("Budget [final].xlsx", Section("x", 2, LocationUnit.SHEET, "Q3 (draft)"))
    assert header == "[Budget [final).xlsx, sheet 2 (Q3 (draft))]"
    location, body = parse_location(f"{header}\nTyres\t4200")
    assert location is not None and (location.unit, location.number, location.label) == (LocationUnit.SHEET, 2, "Q3 (draft)")
    assert body == "Tyres\t4200" and location.describe() == "sheet 2 (Q3 (draft))"
    assert location_header("plan.docx", Section("x")) == "[plan.docx]"
    assert strip_location("[plan.docx]\nWinter", document=True) == "Winter"
    assert parse_location("no header here") == (None, "no header here")


# --------------------------------------------------------------------------- retrieval wiring


def _context() -> TenantContext:
    return TenantContext(tenant_id="tenant_docs", workspace_id="workspace_docs", user_id="user_docs", roles=["owner"])


class _Record:
    def __init__(self, file_id: str, name: str, content_type: str = "application/octet-stream") -> None:
        context = _context()
        self.id, self.name, self.content_type = file_id, name, content_type
        self.tenant_id, self.workspace_id = context.tenant_id, context.workspace_id
        self.sha256 = file_id.ljust(64, "0")


def test_indexer_chunks_documents_per_page_with_location_lines() -> None:
    store = InMemoryRetrievalStore()
    context = _context()
    indexer = RetrievalIndexer(store, LocalEmbedder())
    outcome = asyncio.run(indexer.index_file(context, _Record("file_pdf", "specs.pdf"), make_pdf(["Load index 91", "Speed rating H"])))
    assert outcome.status == IndexStatus.INDEXED and outcome.chunk_count == 2
    found = store.search(context, local_embedding("Speed rating H"), LOCAL_EMBEDDING_MODEL, 5, utc_now())
    assert {c.content.split("\n", 1)[0] for c in found} == {"[specs.pdf, page 1]", "[specs.pdf, page 2]"}
    # The same bytes again reuse the index: nothing is extracted or embedded twice.
    again = asyncio.run(indexer.index_file(context, _Record("file_pdf", "specs.pdf"), make_pdf(["changed"])))
    assert again.reused is True


def test_indexer_records_why_a_document_was_not_indexed() -> None:
    store = InMemoryRetrievalStore()
    context = _context()
    indexer = RetrievalIndexer(store, LocalEmbedder())
    locked = asyncio.run(indexer.index_file(context, _Record("file_ole", "locked.docx"), doc_extract.OLE_SIGNATURE + b"\0" * 64))
    assert (locked.status, locked.error) == (IndexStatus.SKIPPED, "encrypted")
    damaged = asyncio.run(indexer.index_file(context, _Record("file_bad", "bad.pptx"), b"PK\x03\x04nope"))
    assert damaged.error == "malformed"
    source = store.get_source(context, SourceType.FILE, "file_bad")
    assert source is not None and source.status == IndexStatus.SKIPPED and source.error == "malformed"
    blank = asyncio.run(indexer.index_file(context, _Record("file_blank", "blank.xlsx"), make_xlsx({"A": []})))
    assert blank.error == "empty"


def test_a_slow_document_is_failed_so_it_is_tried_again(monkeypatch: pytest.MonkeyPatch) -> None:
    async def slow(data: bytes, fmt: DocumentFormat, limits: ExtractionLimits = ExtractionLimits()) -> None:
        raise ExtractionError("timeout", "Reading this document took too long.")

    monkeypatch.setattr("anum_api.retrieval.extract_document_async", slow)
    store = InMemoryRetrievalStore()
    outcome = asyncio.run(RetrievalIndexer(store, LocalEmbedder()).index_file(_context(), _Record("file_slow", "slow.pdf"), b"%PDF"))
    assert (outcome.status, outcome.error) == (IndexStatus.FAILED, "timeout")


def test_long_documents_stop_at_the_chunk_cap() -> None:
    store = InMemoryRetrievalStore()
    pages = [f"Page {n} " + "word " * 400 for n in range(MAX_CHUNKS_PER_SOURCE + 20)]
    outcome = asyncio.run(
        RetrievalIndexer(store, LocalEmbedder()).index_file(_context(), _Record("file_long", "long.pdf"), make_pdf(pages))
    )
    assert outcome.status == IndexStatus.INDEXED and outcome.chunk_count == MAX_CHUNKS_PER_SOURCE


# --------------------------------------------------------------------------- knowledge base citations

H = {"x-tenant-id": "tenant_docs", "x-workspace-id": "workspace_docs", "x-user-id": "user_docs", "x-user-roles": "member"}


@pytest.fixture
def client() -> Iterator[TestClient]:
    for store in (knowledge_store, memory_retrieval_store):
        store.clear()
    memory_note_repository._notes.clear()
    file_store.clear()
    yield TestClient(app)
    app.dependency_overrides.clear()
    for store in (knowledge_store, memory_retrieval_store):
        store.clear()
    memory_note_repository._notes.clear()
    file_store.clear()


def test_knowledge_base_cites_file_name_and_page(client: TestClient) -> None:
    collection = client.post("/api/v1/knowledge/collections", headers=H, json={"name": "Manuals"}).json()
    pdf = make_pdf(["Warranty covers five years.", "Load index 91 carries 615 kg per tyre."])
    upload = client.post(
        f"/api/v1/knowledge/collections/{collection['id']}/documents",
        headers={**H, "content-type": "application/pdf"},
        params={"name": "manual.pdf"},
        content=pdf,
    )
    assert upload.status_code == 201, upload.text
    assert upload.json()["index"]["state"] == "indexed"
    sheet = client.post(
        f"/api/v1/knowledge/collections/{collection['id']}/documents",
        headers=H,
        params={"name": "loads.xlsx"},
        content=make_xlsx({"Loads": [["Load index 91", "615 kg"]]}),
    )
    assert sheet.status_code == 201, sheet.text

    answer = client.post("/api/v1/knowledge/ask", headers=H, json={"question": "What does load index 91 carry?"}).json()
    assert answer["status"] == "answered"
    by_title = {c["title"]: c for c in answer["citations"]}
    manual = by_title["manual.pdf"]
    assert manual["page_unit"] == "page" and manual["page"] in {1, 2}
    assert not manual["snippet"].startswith("[")  # the location line is not part of the quote
    loads = by_title["loads.xlsx"]
    assert (loads["page_unit"], loads["page"], loads["page_label"]) == ("sheet", 1, "Loads")

    search = client.get("/api/v1/knowledge/search", headers=H, params={"q": "load index 91", "mode": "semantic"}).json()
    assert search["hits"] and all(not (h["snippet"] or "").startswith("[") for h in search["hits"])


def test_a_hostile_document_uploads_but_is_not_indexed(client: TestClient) -> None:
    collection = client.post("/api/v1/knowledge/collections", headers=H, json={"name": "Inbox"}).json()
    bomb = make_zip({"word/document.xml": b"<w:document>" + b" " * (4 * 1024 * 1024) + b"</w:document>"})
    upload = client.post(
        f"/api/v1/knowledge/collections/{collection['id']}/documents", headers=H, params={"name": "bomb.docx"}, content=bomb
    )
    assert upload.status_code == 201, upload.text
    files = client.get("/api/v1/files", headers=H).json()
    index = next(f["index"] for f in files if f["name"] == "bomb.docx")
    assert index["status"] == "skipped" and index["error"] == "too_complex"
