"""Importing people into the Contacts directory from a CSV file (docs/contacts.md#import-from-a-csv-file).

Two calls, both owner only (``business:manage``, like the export: a file brings many people's
personal data into the workspace at once):

* **Preview** reads the file (UTF-8, with or without a byte order mark; comma, semicolon or tab
  separated; a header row), suggests which column holds the name, organisation, notes, phone
  numbers, e-mail addresses and do-not-contact marks (or uses the mapping the caller chose),
  checks every row like the person form does, and says per row what applying would do: add a new
  person, update the person in the directory who already holds the row's number or address
  under the same name, or skip it. A row whose number or address belongs to someone with another
  name, or to several people, is a **possible duplicate** (added as a new person with a merge
  proposal, as typing it by hand would, unless the caller skips it); a row repeating a number or
  address of an earlier row in the file is a **duplicate in the file** (skipped unless the
  caller adds it). Nothing is written.
* **Apply** takes the same file and mapping and the caller's decision per row, checks everything
  again against the directory as it is now, and writes all of it in one unit of work or nothing
  at all: every row that is not skipped must be valid, and a person chosen to update must still
  exist and still hold the row's number or address (409 otherwise, preview again).

Do-not-contact marks are never removed by an import: a person updated from the file keeps every
mark they have, and a row that marks a channel (``phone``, ``email``, ``whatsapp``, ``all``, or
yes/true/1 for all) adds that mark with the reason "Marked do not contact in the imported file".
The file itself is not stored. One audit record per apply (``contacts.imported``) holds counts only.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from datetime import datetime
from enum import StrEnum
from typing import Any

from fastapi import status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..schemas import TenantContext, new_id, utc_now
from .models import (
    MAX_IDENTIFIERS,
    MAX_NAME,
    MAX_NOTES,
    MAX_ORGANISATION,
    Channel,
    DoNotContact,
    IdentifierKind,
    Person,
    PersonSource,
)
from .store import ContactsStore

MAX_FILE_CHARS = 2_000_000
MAX_WORKBOOK_BASE64 = 2_700_000  # a 2 MB workbook in base64 (import_files.MAX_WORKBOOK_BYTES)
MAX_ROWS = 2000
MAX_COLUMNS = 60
MAX_HEADER = 120
IMPORT_REASON = "Marked do not contact in the imported file"
_SPLIT = re.compile(r"[;|\n]+")
_YES = {"yes", "y", "true", "1", "x", "نعم"}
_NO = {"", "no", "n", "false", "0", "لا"}
_CHANNEL_WORDS = {
    "phone": Channel.PHONE, "call": Channel.PHONE, "calls": Channel.PHONE, "هاتف": Channel.PHONE,
    "email": Channel.EMAIL, "e-mail": Channel.EMAIL, "mail": Channel.EMAIL, "بريد": Channel.EMAIL,
    "whatsapp": Channel.WHATSAPP, "واتساب": Channel.WHATSAPP,
    "all": Channel.ALL, "الكل": Channel.ALL,
}
# Header words that suggest each field (English and Arabic), compared after folding case and spaces.
_HINTS: dict[str, tuple[str, ...]] = {
    "name": ("name", "full name", "contact", "contact name", "person", "الاسم", "اسم", "الاسم الكامل"),
    "first_name": ("first name", "given name", "first", "الاسم الأول"),
    "last_name": ("last name", "surname", "family name", "last", "اسم العائلة"),
    "organisation": ("organisation", "organization", "company", "account", "business", "employer", "الشركة", "المؤسسة", "الجهة"),
    "notes": ("notes", "note", "comments", "comment", "ملاحظات"),
    "phones": ("phone", "phone number", "mobile", "mobile number", "telephone", "tel", "number", "whatsapp", "الهاتف", "الجوال", "رقم الهاتف", "الموبايل"),
    "emails": ("email", "e-mail", "email address", "mail", "البريد", "البريد الإلكتروني"),
    "do_not_contact": ("do not contact", "dnc", "opt out", "opted out", "unsubscribe", "unsubscribed", "عدم التواصل", "لا تتصل"),
}


class ImportAction(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    SKIP = "skip"


class RowStatus(StrEnum):
    NEW = "new"
    MATCH = "match"
    POSSIBLE_DUPLICATE = "possible_duplicate"
    DUPLICATE_IN_FILE = "duplicate_in_file"
    INVALID = "invalid"


class ImportMapping(BaseModel):
    """Which header holds what. ``name`` may be several columns (first and last name), joined with a space."""

    model_config = ConfigDict(extra="forbid")

    name: list[str] = Field(default_factory=list, max_length=3)
    organisation: str | None = Field(default=None, max_length=MAX_HEADER)
    notes: str | None = Field(default=None, max_length=MAX_HEADER)
    phones: list[str] = Field(default_factory=list, max_length=4)
    emails: list[str] = Field(default_factory=list, max_length=4)
    do_not_contact: str | None = Field(default=None, max_length=MAX_HEADER)

    def columns(self) -> list[str]:
        return [*self.name, *(c for c in (self.organisation, self.notes, self.do_not_contact) if c), *self.phones, *self.emails]


class ImportFormat(StrEnum):
    CSV = "csv"
    XLSX = "xlsx"
    VCF = "vcf"


class ImportFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: ImportFormat = Field(default=ImportFormat.CSV, description="csv and vcf send the text in csv; xlsx sends the workbook in file_base64")
    csv: str = Field(default="", max_length=MAX_FILE_CHARS, description="The file's text (CSV or vCard)")
    file_base64: str = Field(default="", max_length=MAX_WORKBOOK_BASE64, description="An Excel workbook (.xlsx), base64")
    file_name: str = Field(default="", max_length=200)
    mapping: ImportMapping | None = Field(default=None, description="Omitted: ANUM suggests one from the headers")

    @field_validator("file_name")
    @classmethod
    def _name(cls, value: str) -> str:
        return " ".join(value.split())[:200]

    @model_validator(mode="after")
    def _one_file(self) -> ImportFile:
        if self.format == ImportFormat.XLSX:
            if not self.file_base64 or self.csv:
                raise ValueError("An Excel file is sent in file_base64 (and csv is left empty).")
        elif not self.csv or self.file_base64:
            raise ValueError("A CSV or vCard file is sent as text in csv (and file_base64 is left empty).")
        return self

    def content(self) -> str:
        """What the file is, as sent: the text, or the workbook's base64 (also what the fingerprint covers)."""
        return self.file_base64 if self.format == ImportFormat.XLSX else self.csv


class RowDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    row: int = Field(ge=2)
    action: ImportAction
    target_id: str | None = Field(default=None, max_length=80, description="The person to update (action update)")


class ImportApply(ImportFile):
    mapping: ImportMapping  # type: ignore[assignment]
    decisions: list[RowDecision] = Field(default_factory=list, max_length=MAX_ROWS, description="Rows that differ from the suggested action")
    fingerprint: str = Field(min_length=64, max_length=64, description="The preview's fingerprint: the same file and mapping")


class MatchView(BaseModel):
    id: str
    name: str
    shared: list[str]
    do_not_contact: list[Channel]


class IssueCode(StrEnum):
    CONTROL = "control"
    NAME_LONG = "name_long"
    ORGANISATION_LONG = "organisation_long"
    NOTES_LONG = "notes_long"
    NOT_PHONE = "not_phone"
    NOT_EMAIL = "not_email"
    TOO_MANY = "too_many"
    BAD_DNC = "bad_dnc"
    EMPTY = "empty"


class RowIssue(BaseModel):
    code: IssueCode
    value: str = ""


class RowView(BaseModel):
    row: int
    name: str
    organisation: str
    phones: list[str]
    emails: list[str]
    do_not_contact: list[Channel]
    status: RowStatus
    action: ImportAction
    actions: list[ImportAction]
    target_id: str | None = None
    matches: list[MatchView] = Field(default_factory=list)
    duplicate_of: int | None = None
    problems: list[str] = Field(default_factory=list, description="Why the row cannot be imported, in English")
    issues: list[RowIssue] = Field(default_factory=list, description="The same as codes, for a client to word in its language")


class ImportCounts(BaseModel):
    rows: int = 0
    new: int = 0
    match: int = 0
    possible_duplicate: int = 0
    duplicate_in_file: int = 0
    invalid: int = 0


class ImportPreview(BaseModel):
    columns: list[str]
    mapping: ImportMapping
    suggested: bool
    rows: list[RowView]
    counts: ImportCounts
    fingerprint: str
    delimiter: str


class ImportResult(BaseModel):
    created: int = 0
    updated: int = 0
    skipped: int = 0
    marks_added: int = 0
    proposals_created: int = 0
    rows: int = 0


def _error(code: int, detail: str) -> Exception:
    from .service import http_error

    return http_error(code, detail)


def _fold(text: str) -> str:
    return " ".join(text.replace("_", " ").replace("-", " ").split()).casefold()


def check_columns(header: list[str] | None) -> list[str]:
    """The header row's column names, tidied, or 422 when they cannot name the columns (any file format)."""
    if not header or not any(cell.strip() for cell in header):
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "The file is empty. The first row must name the columns (Name, Phone, Email...).")
    columns = [" ".join(cell.split())[:MAX_HEADER] for cell in header]
    if len(columns) > MAX_COLUMNS:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, f"The file has {len(columns)} columns; ANUM reads at most {MAX_COLUMNS}.")
    if len({c.casefold() for c in columns if c}) != len([c for c in columns if c]):
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "Two columns have the same name. Rename one in the first row and choose the file again.")
    return columns


def load_table(payload: ImportFile) -> tuple[list[str], list[tuple[int, list[str]]], str]:
    """The file's table whatever its format: CSV text, an Excel workbook (base64) or vCard text."""
    from . import import_files

    if payload.format == ImportFormat.XLSX:
        header, rows, kind = import_files.read_workbook(import_files.decode_workbook(payload.content()), max_rows=MAX_ROWS, max_columns=MAX_COLUMNS)
    elif payload.format == ImportFormat.VCF:
        header, rows, kind = import_files.read_vcards(payload.content(), max_rows=MAX_ROWS)
    else:
        return read_table(payload.content())
    columns = check_columns(header)
    if not rows:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "The file has a header row but no people under it.")
    return columns, rows, kind


def read_table(text: str) -> tuple[list[str], list[tuple[int, list[str]]], str]:
    """The header, the rows (each with its record number in the file, the header being 1; blank
    records are left out but keep their number) and the separator. 422 with the reason when it is
    not a table ANUM can read."""
    text = text.lstrip("﻿")
    if "\x00" in text:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "This is not a text CSV file. Save the sheet as CSV (UTF-8) and choose it again.")
    first = text.split("\n", 1)[0]
    delimiter = max((",", ";", "\t"), key=first.count)
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    try:
        header = next(reader, None)
        columns = check_columns(header)
        rows: list[tuple[int, list[str]]] = []
        for number, cells in enumerate(reader, start=2):
            if not any(cell.strip() for cell in cells):
                continue
            rows.append((number, cells))
            if len(rows) > MAX_ROWS:
                raise _error(status.HTTP_413_CONTENT_TOO_LARGE, f"The file has more than {MAX_ROWS} people. Split it into files of at most {MAX_ROWS} rows.")
    except csv.Error as exc:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, f"The file could not be read as CSV ({exc}). Save the sheet as CSV (UTF-8) and choose it again.") from exc
    if not rows:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "The file has a header row but no people under it.")
    return columns, rows, delimiter


def suggest_mapping(columns: list[str]) -> ImportMapping:
    """A first mapping from the header words (English and Arabic)."""
    folded = {column: _fold(column) for column in columns if column}
    taken: set[str] = set()

    def pick(field: str, *, many: bool = False) -> list[str]:
        found = [c for c, f in folded.items() if c not in taken and f in _HINTS[field]]
        if not found:
            found = [c for c, f in folded.items() if c not in taken and any(hint in f for hint in _HINTS[field] if len(hint) > 3)]
        found = found if many else found[:1]
        taken.update(found)
        return found

    dnc = pick("do_not_contact")
    first, last = pick("first_name"), pick("last_name")
    name = pick("name") or [*first, *last]
    organisation, notes = pick("organisation"), pick("notes")
    emails = pick("emails", many=True)[:4]
    phones = pick("phones", many=True)[:4]
    return ImportMapping(name=name[:3], organisation=(organisation or [None])[0], notes=(notes or [None])[0], phones=phones, emails=emails,
                         do_not_contact=(dnc or [None])[0])


def _check_mapping(mapping: ImportMapping, columns: list[str]) -> None:
    known = set(columns)
    missing = [c for c in mapping.columns() if c not in known]
    if missing:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, f"The file has no column named {missing[0]!r}. Choose the columns again.")
    if not mapping.name and not mapping.phones and not mapping.emails:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "Choose the column with the name, or at least one column with phone numbers or e-mail addresses.")


def _channels(raw: str) -> tuple[list[Channel], str | None]:
    words = [w.strip().casefold() for w in re.split(r"[;,|/\n]+", raw) if w.strip()]
    if not words or all(w in _NO for w in words):
        return [], None
    out: list[Channel] = []
    for word in words:
        if word in _YES:
            out.append(Channel.ALL)
        elif word in _CHANNEL_WORDS:
            out.append(_CHANNEL_WORDS[word])
        else:
            return [], f"The do-not-contact value {raw.strip()[:40]!r} is not phone, email, whatsapp, all or yes."
    channels = sorted(set(out), key=list(Channel).index)
    return ([Channel.ALL] if Channel.ALL in channels else channels), None


class _Row:
    def __init__(self, number: int) -> None:
        self.number = number
        self.name = ""
        self.organisation = ""
        self.notes = ""
        self.phones: list[tuple[str, str]] = []  # (value, label)
        self.emails: list[tuple[str, str]] = []
        self.channels: list[Channel] = []
        self.problems: list[str] = []
        self.issues: list[RowIssue] = []

    def problem(self, code: IssueCode, text: str, value: str = "") -> None:
        self.problems.append(text)
        self.issues.append(RowIssue(code=code, value=value))


def _cell(cells: list[str], index: dict[str, int], column: str | None) -> str:
    if not column:
        return ""
    position = index[column]
    return cells[position] if position < len(cells) else ""


def parse_rows(columns: list[str], rows: list[tuple[int, list[str]]], mapping: ImportMapping) -> list[_Row]:
    """Every row read through the mapping and checked like the person form (names, lengths,
    numbers with their country code, addresses), problems kept per row."""
    from .service import normalise_email, normalise_phone

    index = {column: i for i, column in enumerate(columns)}
    out: list[_Row] = []
    for number, cells in rows:
        row = _Row(number)
        row.name = " ".join(" ".join(_cell(cells, index, c) for c in mapping.name).split())
        row.organisation = " ".join(_cell(cells, index, mapping.organisation).split())
        row.notes = _cell(cells, index, mapping.notes).strip()
        for text in (row.name, row.organisation, row.notes):
            if any(ord(ch) < 32 for ch in text if ch not in "\n\t"):
                row.problem(IssueCode.CONTROL, "The row has control characters.")
                break
        if len(row.name) > MAX_NAME:
            row.problem(IssueCode.NAME_LONG, f"The name is longer than {MAX_NAME} characters.", str(MAX_NAME))
        if len(row.organisation) > MAX_ORGANISATION:
            row.problem(IssueCode.ORGANISATION_LONG, f"The organisation is longer than {MAX_ORGANISATION} characters.", str(MAX_ORGANISATION))
        if len(row.notes) > MAX_NOTES:
            row.problem(IssueCode.NOTES_LONG, f"The notes are longer than {MAX_NOTES} characters.", str(MAX_NOTES))
        for columns_of, normalise, target, what, code in (
            (mapping.phones, normalise_phone, row.phones, "phone number with its country code (+971...)", IssueCode.NOT_PHONE),
            (mapping.emails, normalise_email, row.emails, "e-mail address", IssueCode.NOT_EMAIL),
        ):
            for column in columns_of:
                for raw in _SPLIT.split(_cell(cells, index, column)):
                    label = " ".join(raw.split())
                    if not label:
                        continue
                    value = normalise(label)
                    if value is None:
                        row.problem(code, f"{label[:60]!r} is not a {what}.", label[:60])
                    elif value not in {v for v, _ in target}:
                        target.append((value, label[:200]))
        if len(row.phones) > MAX_IDENTIFIERS or len(row.emails) > MAX_IDENTIFIERS:
            row.problem(IssueCode.TOO_MANY, f"A person can have at most {MAX_IDENTIFIERS} numbers and {MAX_IDENTIFIERS} addresses.", str(MAX_IDENTIFIERS))
        dnc_cell = _cell(cells, index, mapping.do_not_contact)
        row.channels, problem = _channels(dnc_cell)
        if problem:
            row.problem(IssueCode.BAD_DNC, problem, dnc_cell.strip()[:40])
        if not row.name:
            first = row.phones[0][0] if row.phones else (row.emails[0][0] if row.emails else "")
            if first:
                row.name = first[:MAX_NAME]
            else:
                row.problem(IssueCode.EMPTY, "The row has no name, number or address.")
        out.append(row)
    return out


def fingerprint(text: str, mapping: ImportMapping, kind: ImportFormat = ImportFormat.CSV) -> str:
    """The same file, read as the same format, with the same mapping (apply refuses a preview of another file).
    A CSV file's fingerprint is the same as before Excel and vCard files were read."""
    tail = "" if kind == ImportFormat.CSV else f"\x1e{kind.value}"
    return hashlib.sha256(f"{text.lstrip(chr(0xFEFF))}\x1e{mapping.model_dump_json()}{tail}".encode()).hexdigest()


def _identifiers(row: _Row) -> list[tuple[IdentifierKind, str, str]]:
    return [(IdentifierKind.PHONE, v, label) for v, label in row.phones] + [(IdentifierKind.EMAIL, v, label) for v, label in row.emails]


def classify(store: ContactsStore, rows: list[_Row]) -> list[RowView]:
    """What applying would do with each row, against the directory as it is now."""
    from .service import same_name

    seen: dict[tuple[IdentifierKind, str], int] = {}
    views: list[RowView] = []
    for row in rows:
        identifiers = _identifiers(row)
        shared: dict[str, list[str]] = {}
        for kind, value, _label in identifiers:
            for existing in store.find_by_identifier(kind, value):
                shared.setdefault(existing.contact_id, []).append(value)
        people = {p.id: p for p in (store.get_person(cid) for cid in shared) if p is not None}
        dnc = store.dnc_of(list(people)) if people else {}
        matches = [MatchView(id=p.id, name=p.name, shared=sorted(set(shared[p.id])), do_not_contact=sorted({d.channel for d in dnc.get(p.id, [])}, key=list(Channel).index))
                   for p in sorted(people.values(), key=lambda p: (-len(shared[p.id]), p.name, p.id))]
        earlier = next((seen[(kind, value)] for kind, value, _ in identifiers if (kind, value) in seen), None)
        for kind, value, _ in identifiers:
            seen.setdefault((kind, value), row.number)
        same = [m for m in matches if same_name(m.name, row.name)]
        target: str | None = None
        if row.problems:
            state, action, allowed = RowStatus.INVALID, ImportAction.SKIP, [ImportAction.SKIP]
        elif earlier is not None:
            state, action, allowed = RowStatus.DUPLICATE_IN_FILE, ImportAction.SKIP, [ImportAction.SKIP, ImportAction.CREATE]
        elif len(matches) == 1 and same:
            state, action, target = RowStatus.MATCH, ImportAction.UPDATE, same[0].id
            allowed = [ImportAction.UPDATE, ImportAction.CREATE, ImportAction.SKIP]
        elif matches:
            state, action = RowStatus.POSSIBLE_DUPLICATE, ImportAction.CREATE
            allowed = [ImportAction.CREATE, ImportAction.UPDATE, ImportAction.SKIP]
        else:
            state, action, allowed = RowStatus.NEW, ImportAction.CREATE, [ImportAction.CREATE, ImportAction.SKIP]
        views.append(RowView(
            row=row.number, name=row.name, organisation=row.organisation, phones=[v for v, _ in row.phones], emails=[v for v, _ in row.emails],
            do_not_contact=row.channels, status=state, action=action, actions=allowed, target_id=target, matches=matches,
            duplicate_of=earlier, problems=row.problems, issues=row.issues,
        ))
    return views


def preview(store: ContactsStore, payload: ImportFile) -> ImportPreview:
    columns, table, delimiter = load_table(payload)
    suggested = payload.mapping is None
    mapping = payload.mapping or suggest_mapping(columns)
    if suggested and not mapping.columns():
        # Nothing recognisable: the caller chooses the columns (the preview still lists them).
        return ImportPreview(columns=columns, mapping=mapping, suggested=True, rows=[], counts=ImportCounts(rows=len(table)),
                             fingerprint=fingerprint(payload.content(), mapping, payload.format), delimiter=delimiter)
    _check_mapping(mapping, columns)
    rows = classify(store, parse_rows(columns, table, mapping))
    counts = ImportCounts(rows=len(rows))
    for row in rows:
        setattr(counts, row.status.value, getattr(counts, row.status.value) + 1)
    return ImportPreview(columns=columns, mapping=mapping, suggested=suggested, rows=rows, counts=counts,
                         fingerprint=fingerprint(payload.content(), mapping, payload.format), delimiter=delimiter)


def apply(store: ContactsStore, context: TenantContext, payload: ImportApply) -> ImportResult:
    """Check every decision against the directory as it is now, then write all of it. Nothing is
    written until every row has passed, so a refusal leaves the directory as it was (and the
    PostgreSQL unit of work rolls back if anything fails while writing)."""
    from .service import _add_identifiers

    if payload.fingerprint != fingerprint(payload.content(), payload.mapping, payload.format):
        raise _error(status.HTTP_409_CONFLICT, "The file or the columns changed since the preview. Preview the file again, then import.")
    columns, table, _delimiter = load_table(payload)
    _check_mapping(payload.mapping, columns)
    parsed = {row.number: row for row in parse_rows(columns, table, payload.mapping)}
    views = {view.row: view for view in classify(store, list(parsed.values()))}
    chosen: dict[int, RowDecision] = {}
    for decision in payload.decisions:
        if decision.row not in views:
            raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Row {decision.row} is not in the file.")
        chosen[decision.row] = decision
    plan: list[tuple[_Row, ImportAction, Person | None]] = []  # an update always carries its person
    problems: list[str] = []
    for number, view in views.items():
        decision = chosen.get(number)
        action = decision.action if decision else view.action
        target_id = (decision.target_id if decision else None) or view.target_id
        if action not in view.actions:
            problems.append(f"Row {number}: {view.problems[0] if view.problems else 'that action is not possible for this row'}.")
            continue
        target: Person | None = None
        if action == ImportAction.UPDATE:
            if not target_id or target_id not in {m.id for m in view.matches}:
                problems.append(f"Row {number}: the person to update no longer holds this row's number or address.")
                continue
            target = store.get_person(target_id)
            if target is None:
                problems.append(f"Row {number}: the person to update was deleted.")
                continue
        plan.append((parsed[number], action, target))
    if problems:
        shown = " ".join(problems[:5]) + (f" And {len(problems) - 5} more." if len(problems) > 5 else "")
        raise _error(status.HTTP_409_CONFLICT, f"Nothing was imported. {shown} Preview the file again, then import.")
    now = utc_now()
    result = ImportResult(rows=len(views))
    for row, action, target in plan:
        if action == ImportAction.SKIP:
            result.skipped += 1
            continue
        if target is None:
            person = store.add_person(Person(
                id=new_id("contact"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, name=row.name, organisation=row.organisation,
                notes=row.notes, source=PersonSource.MANUAL, created_by=context.user_id, created_at=now, updated_at=now,
            ))
            result.created += 1
            identifiers = _identifiers(row)
        else:
            person, identifiers = _update(store, target, row, now)
            result.updated += 1
        result.proposals_created += _add_identifiers(store, context, person, identifiers, now)
        result.marks_added += _mark(store, context, person, row.channels, now)
    return result


def _update(store: ContactsStore, target: Person, row: _Row, now: datetime) -> tuple[Person, list[tuple[IdentifierKind, str, str]]]:
    """Fill a missing organisation, add the row's notes, and return the numbers and addresses the
    person does not hold yet (up to the limit per kind). The name and every mark stay."""
    held = store.identifiers_of([target.id]).get(target.id, [])
    have = {(i.kind, i.value) for i in held}
    room = {kind: MAX_IDENTIFIERS - sum(1 for i in held if i.kind == kind) for kind in IdentifierKind}
    identifiers: list[tuple[IdentifierKind, str, str]] = []
    for kind, value, label in _identifiers(row):
        if (kind, value) in have or room[kind] <= 0:
            continue
        room[kind] -= 1
        identifiers.append((kind, value, label))
    changes: dict[str, Any] = {"updated_at": now}
    if not target.organisation and row.organisation:
        changes["organisation"] = row.organisation
    if row.notes and row.notes not in target.notes:
        changes["notes"] = f"{target.notes}\n\n{row.notes}".strip()[:MAX_NOTES]
    return store.save_person(target.model_copy(update=changes)), identifiers


def _mark(store: ContactsStore, context: TenantContext, person: Person, channels: list[Channel], now: datetime) -> int:
    """Add the file's do-not-contact marks; an existing mark (and its reason) is never replaced."""
    if not channels:
        return 0
    held = {d.channel for d in store.dnc_of([person.id]).get(person.id, [])}
    added = 0
    for channel in channels:
        if channel in held or Channel.ALL in held:
            continue
        store.set_dnc(DoNotContact(
            id=new_id("ctdnc"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, contact_id=person.id, channel=channel,
            reason=IMPORT_REASON, set_by=context.user_id, set_at=now,
        ))
        added += 1
    return added
