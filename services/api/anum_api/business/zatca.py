"""ZATCA e-invoicing readiness for Saudi businesses (docs/business-tax.md#zatca-e-invoicing-readiness).

Not verified with ZATCA. ANUM does not claim compliance until the business completes onboarding and its invoices pass
ZATCA validation. Everything here is behind a per-business switch that is off by default (``TaxSettings.zatca_enabled``).

Phase 1 (generation): the QR code of a tax invoice is the base64 of a TLV string (tag byte, length byte, UTF-8 value)
holding the seller's name (1), VAT number (2), the invoice time stamp (3), the total with VAT (4) and the VAT total (5).

Phase 2 (integration), only as far as it goes without credentials: UBL 2.1 XML for a standard (B2B) or simplified (B2C)
tax invoice and for a credit note, its hash (SHA-256 of the canonicalised XML, base64), the previous document's hash
and a counter per business (``business_zatca_chains``, taken under the business's books lock so two generations never
share a counter), a UUID, and a check against the required fields this module knows. The cryptographic stamp, the
CSID onboarding (Fatoora portal OTP, CSR, CSID) and the clearance and reporting calls are an interface that refuses:
this release never calls a ZATCA endpoint and never stores a key, a certificate or an OTP.

Canonicalisation: the standard library's ``xml.etree.ElementTree.canonicalize`` (C14N 2.0). The XML ANUM writes has no
comments, processing instructions, DTD or ``xml:`` attributes, so its canonical form is the one C14N 1.1 gives; this is
a point to confirm with the ZATCA SDK during onboarding (docs/business-tax.md#what-is-not-built).
"""

from __future__ import annotations

import base64
import hashlib
import uuid as uuid_module
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Protocol
from xml.etree import (
    ElementTree as ET,  # nosec B405: writes XML ANUM builds itself; nothing untrusted is parsed
)

from ..schemas import TenantContext, new_id, utc_now
from .books_models import TaxSettings, ZatcaChain, ZatcaDocument
from .books_store import DuplicateError, open_books_store
from .tax_rules import SA_VAT, ZATCA_CATEGORY, Jurisdiction, split

NOT_VERIFIED_EN = ("Not verified with ZATCA. ANUM does not claim compliance until the business completes onboarding and the invoices pass "
                   "ZATCA validation.")
NOT_VERIFIED_AR = "لم يتم التحقق مع هيئة الزكاة والضريبة والجمارك. لا يدعي ANUM الامتثال حتى تكمل المنشأة التسجيل وتجتاز فواتيرها تحقق الهيئة."
ONBOARDING_REQUIRED = ("Sending invoices to ZATCA needs onboarding first: the business generates a one-time password on the Fatoora portal, "
                       "creates a certificate signing request and receives its cryptographic stamp identifier (CSID). ANUM has not been "
                       "onboarded, so nothing is sent to ZATCA.")

NS = {
    "": "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2",
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
}
STANDARD, SIMPLIFIED = "0100000", "0200000"
TYPE_INVOICE, TYPE_CREDIT = "388", "381"
# The previous-invoice hash of the first document: base64 of the hexadecimal SHA-256 of "0", as ZATCA's SDK samples show (to confirm at onboarding).
INITIAL_PREVIOUS_HASH = base64.b64encode(hashlib.sha256(b"0").hexdigest().encode()).decode()

# Phase 1 QR tags (docs/business-tax.md#qr-code).
TAG_SELLER, TAG_VAT_NUMBER, TAG_TIMESTAMP, TAG_TOTAL, TAG_VAT = 1, 2, 3, 4, 5


class ZatcaError(Exception):
    def __init__(self, status_code: int, message: str, code: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.code = code


# -- Phase 1: the QR code -----------------------------------------------------------------------------------------------


def tlv(fields: list[tuple[int, str]]) -> bytes:
    """Tag, length, value: one byte each for the tag and the length (of the UTF-8 bytes), then the bytes, with no separator."""
    out = bytearray()
    for tag, value in fields:
        data = value.encode("utf-8")
        if not 0 < tag < 256 or len(data) > 255:
            raise ValueError("a TLV tag and length are one byte each")
        out += bytes((tag, len(data))) + data
    return bytes(out)


def tlv_decode(data: bytes) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    index = 0
    while index < len(data):
        if index + 2 > len(data):
            raise ValueError("a TLV field is cut short")
        tag, length = data[index], data[index + 1]
        value = data[index + 2:index + 2 + length]
        if len(value) != length:
            raise ValueError("a TLV value is cut short")
        out.append((tag, value.decode("utf-8")))
        index += 2 + length
    return out


def amount(value: Decimal) -> str:
    return str(Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def timestamp(moment: datetime) -> str:
    """ISO 8601 in UTC, to the second (``2026-11-09T10:15:00Z``)."""
    value = moment.astimezone(UTC) if moment.tzinfo else moment.replace(tzinfo=UTC)
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def phase1_qr(seller_name: str, vat_number: str, moment: datetime, total: Decimal, vat_total: Decimal) -> str:
    return base64.b64encode(tlv([(TAG_SELLER, seller_name), (TAG_VAT_NUMBER, vat_number), (TAG_TIMESTAMP, timestamp(moment)),
                                 (TAG_TOTAL, amount(total)), (TAG_VAT, amount(vat_total))])).decode("ascii")


def qr_for_invoice(settings: TaxSettings | None, invoice: Any) -> str | None:
    """The QR text printed on a sent invoice of a Saudi business that turned e-invoicing on; None otherwise."""
    if settings is None or not settings.zatca_enabled or settings.jurisdiction != Jurisdiction.SA.value:
        return None
    seller = invoice.seller
    if seller is None or invoice.sent_at is None or invoice.tax <= 0:
        return None
    number = seller.tax_number.strip() or settings.registration_number
    if not seller.legal_name.strip() or not SA_VAT.match(number or ""):
        return None
    return phase1_qr(seller.legal_name.strip(), number, invoice.sent_at, invoice.total, invoice.tax)


def tax_settings_for(context: TenantContext) -> TaxSettings | None:
    """Read before an invoice unit of work: the QR is printed only when the business turned e-invoicing on."""
    try:
        with open_books_store(context) as store:
            return store.first("tax_settings")
    except Exception:  # noqa: BLE001 - a PDF never fails because the tax settings could not be read; it prints without the QR
        return None


# -- Phase 2: UBL 2.1 XML, hash, chain ---------------------------------------------------------------------------------


@dataclass
class XmlLine:
    name: str
    quantity: Decimal
    unit_price: Decimal
    net: Decimal
    tax: Decimal
    category: str | None  # S, Z, E, O; None for a line without a tax code
    rate: Decimal


@dataclass
class XmlDocument:
    number: str
    uuid: str
    counter: int
    previous_hash: str
    type_code: str
    subtype: str
    issued_at: datetime
    currency: str
    seller_name: str
    seller_vat: str
    seller_crn: str
    seller_street: str
    seller_building: str
    seller_district: str
    seller_city: str
    seller_postal_code: str
    buyer_name: str
    buyer_vat: str
    lines: list[XmlLine] = field(default_factory=list)
    billing_reference: str | None = None
    reason: str = ""


def _el(parent: ET.Element, tag: str, text: str | None = None, **attrs: str) -> ET.Element:
    prefix, _, local = tag.rpartition(":")
    element = ET.SubElement(parent, f"{{{NS[prefix]}}}{local}", attrs)
    if text is not None:
        element.text = text
    return element


def _party(parent: ET.Element, tag: str, *, name: str, vat: str, crn: str = "", street: str = "", building: str = "", district: str = "",
           city: str = "", postal: str = "") -> None:
    party = _el(_el(parent, tag), "cac:Party")
    if crn:
        _el(_el(party, "cac:PartyIdentification"), "cbc:ID", crn, schemeID="CRN")
    address = _el(party, "cac:PostalAddress")
    for child, value in (("cbc:StreetName", street), ("cbc:BuildingNumber", building), ("cbc:CitySubdivisionName", district),
                         ("cbc:CityName", city), ("cbc:PostalZone", postal)):
        if value:
            _el(address, child, value)
    _el(_el(address, "cac:Country"), "cbc:IdentificationCode", "SA")
    if vat:
        scheme = _el(party, "cac:PartyTaxScheme")
        _el(scheme, "cbc:CompanyID", vat)
        _el(_el(scheme, "cac:TaxScheme"), "cbc:ID", "VAT")
    _el(_el(party, "cac:PartyLegalEntity"), "cbc:RegistrationName", name)


def build_xml(document: XmlDocument) -> str:
    """The UBL 2.1 invoice (or credit note, type 381) as the XML implementation standard structures it, without the
    signature extension (ANUM cannot stamp it)."""
    for prefix, uri in NS.items():
        ET.register_namespace(prefix, uri)
    root = ET.Element(f"{{{NS['']}}}Invoice")
    _el(root, "cbc:ProfileID", "reporting:1.0")
    _el(root, "cbc:ID", document.number)
    _el(root, "cbc:UUID", document.uuid)
    moment = document.issued_at.astimezone(UTC) if document.issued_at.tzinfo else document.issued_at
    _el(root, "cbc:IssueDate", moment.strftime("%Y-%m-%d"))
    _el(root, "cbc:IssueTime", moment.strftime("%H:%M:%S"))
    _el(root, "cbc:InvoiceTypeCode", document.type_code, name=document.subtype)
    _el(root, "cbc:DocumentCurrencyCode", document.currency)
    _el(root, "cbc:TaxCurrencyCode", "SAR")
    if document.billing_reference:
        _el(_el(_el(root, "cac:BillingReference"), "cac:InvoiceDocumentReference"), "cbc:ID", document.billing_reference)
    counter = _el(root, "cac:AdditionalDocumentReference")
    _el(counter, "cbc:ID", "ICV")
    _el(counter, "cbc:UUID", str(document.counter))
    previous = _el(root, "cac:AdditionalDocumentReference")
    _el(previous, "cbc:ID", "PIH")
    _el(_el(previous, "cac:Attachment"), "cbc:EmbeddedDocumentBinaryObject", document.previous_hash, mimeCode="text/plain")
    _party(root, "cac:AccountingSupplierParty", name=document.seller_name, vat=document.seller_vat, crn=document.seller_crn,
           street=document.seller_street, building=document.seller_building, district=document.seller_district, city=document.seller_city,
           postal=document.seller_postal_code)
    _party(root, "cac:AccountingCustomerParty", name=document.buyer_name, vat=document.buyer_vat)
    if document.subtype == STANDARD:
        _el(_el(root, "cac:Delivery"), "cbc:ActualDeliveryDate", moment.strftime("%Y-%m-%d"))
    means = _el(root, "cac:PaymentMeans")
    _el(means, "cbc:PaymentMeansCode", "10")
    if document.reason:
        _el(means, "cbc:InstructionNote", document.reason)
    currency = document.currency
    net_total = sum((line.net for line in document.lines), Decimal(0))
    tax_total = sum((line.tax for line in document.lines), Decimal(0))
    _el(_el(root, "cac:TaxTotal"), "cbc:TaxAmount", amount(tax_total), currencyID="SAR")
    detailed = _el(root, "cac:TaxTotal")
    _el(detailed, "cbc:TaxAmount", amount(tax_total), currencyID=currency)
    groups: dict[tuple[str, Decimal], list[XmlLine]] = {}
    for line in document.lines:
        groups.setdefault((line.category or "S", line.rate), []).append(line)
    for (category, rate), members in sorted(groups.items()):
        subtotal = _el(detailed, "cac:TaxSubtotal")
        _el(subtotal, "cbc:TaxableAmount", amount(sum((line.net for line in members), Decimal(0))), currencyID=currency)
        _el(subtotal, "cbc:TaxAmount", amount(sum((line.tax for line in members), Decimal(0))), currencyID=currency)
        tax_category = _el(subtotal, "cac:TaxCategory")
        _el(tax_category, "cbc:ID", category)
        _el(tax_category, "cbc:Percent", amount(rate))
        _el(_el(tax_category, "cac:TaxScheme"), "cbc:ID", "VAT")
    totals = _el(root, "cac:LegalMonetaryTotal")
    _el(totals, "cbc:LineExtensionAmount", amount(net_total), currencyID=currency)
    _el(totals, "cbc:TaxExclusiveAmount", amount(net_total), currencyID=currency)
    _el(totals, "cbc:TaxInclusiveAmount", amount(net_total + tax_total), currencyID=currency)
    _el(totals, "cbc:AllowanceTotalAmount", "0.00", currencyID=currency)
    _el(totals, "cbc:PrepaidAmount", "0.00", currencyID=currency)
    _el(totals, "cbc:PayableAmount", amount(net_total + tax_total), currencyID=currency)
    for index, line in enumerate(document.lines, start=1):
        row = _el(root, "cac:InvoiceLine")
        _el(row, "cbc:ID", str(index))
        _el(row, "cbc:InvoicedQuantity", format(line.quantity.normalize(), "f"), unitCode="PCE")
        _el(row, "cbc:LineExtensionAmount", amount(line.net), currencyID=currency)
        line_tax = _el(row, "cac:TaxTotal")
        _el(line_tax, "cbc:TaxAmount", amount(line.tax), currencyID=currency)
        _el(line_tax, "cbc:RoundingAmount", amount(line.net + line.tax), currencyID=currency)
        item = _el(row, "cac:Item")
        _el(item, "cbc:Name", line.name)
        classified = _el(item, "cac:ClassifiedTaxCategory")
        _el(classified, "cbc:ID", line.category or "S")
        _el(classified, "cbc:Percent", amount(line.rate))
        _el(_el(classified, "cac:TaxScheme"), "cbc:ID", "VAT")
        _el(_el(row, "cac:Price"), "cbc:PriceAmount", amount(line.unit_price), currencyID=currency)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode")


def canonical(xml: str) -> str:
    body = xml.split("?>", 1)[1] if xml.startswith("<?xml") else xml
    return ET.canonicalize(body.strip())


def invoice_hash(xml: str) -> str:
    """Base64 of the SHA-256 of the canonical XML (bytes, UTF-8). The XML has no signature, QR or extension to strip."""
    return base64.b64encode(hashlib.sha256(canonical(xml).encode("utf-8")).digest()).decode("ascii")


# The required fields this module checks (docs/business-tax.md#required-fields-check). Always missing in this release: the stamp.
ALWAYS_MISSING = ("cryptographic_stamp", "csid", "qr_phase2")


def missing_fields(document: XmlDocument) -> list[str]:
    found: list[str] = []
    if not document.seller_name.strip():
        found.append("seller_name")
    if not SA_VAT.match(document.seller_vat or ""):
        found.append("seller_vat_number")
    if not document.seller_crn.strip():
        found.append("seller_crn")
    for name in ("seller_street", "seller_building", "seller_district", "seller_city", "seller_postal_code"):
        if not getattr(document, name).strip():
            found.append(name)
    if document.seller_building and not (document.seller_building.isdigit() and len(document.seller_building) == 4):
        found.append("seller_building_format")
    if document.seller_postal_code and not (document.seller_postal_code.isdigit() and len(document.seller_postal_code) == 5):
        found.append("seller_postal_code_format")
    if document.subtype == STANDARD:
        if not document.buyer_name.strip():
            found.append("buyer_name")
        found.append("buyer_address_structured")  # ANUM keeps the buyer's address as one text, not street, building, city
    if document.currency != "SAR":
        found.append("vat_total_in_sar")
    if not document.lines:
        found.append("lines")
    if any(line.category is None for line in document.lines):
        found.append("line_tax_codes")
    if any(line.category in ("Z", "E", "O") for line in document.lines):
        found.append("exemption_reason_codes")
    if document.type_code == TYPE_CREDIT and not document.billing_reference:
        found.append("billing_reference")
    return [*found, *ALWAYS_MISSING]


def _lines_of_invoice(invoice: Any) -> list[XmlLine]:
    return [XmlLine(line.description, Decimal(line.quantity), Decimal(line.unit_price), Decimal(line.net), Decimal(line.tax),
                    ZATCA_CATEGORY.get(line.tax_code) if line.tax_code else None, Decimal(line.tax_rate_pct)) for line in invoice.lines]


def _lines_of_credit(invoice: Any, note: Any) -> list[XmlLine]:
    """One line per tax category of the invoice, in the invoice's proportion (a credit note in ANUM has an amount, not lines)."""
    groups: dict[tuple[str | None, Decimal], list[Any]] = {}
    for line in invoice.lines:
        groups.setdefault((line.tax_code, Decimal(line.tax_rate_pct)), []).append(line)
    keys = sorted(groups, key=lambda key: (key[0] or "", key[1]))
    nets = split(Decimal(note.subtotal), [sum((Decimal(line.net) for line in groups[key]), Decimal(0)) for key in keys])
    taxes = split(Decimal(note.tax), [sum((Decimal(line.tax) for line in groups[key]), Decimal(0)) for key in keys])
    return [XmlLine(f"Credit for invoice {invoice.number}", Decimal(1), net, net, tax, ZATCA_CATEGORY.get(code) if code else None, rate)
            for (code, rate), net, tax in zip(keys, nets, taxes, strict=True) if net or tax]


def _document(settings: TaxSettings, invoice: Any, *, number: str, counter: int, previous: str, issued_at: datetime, note: Any = None) -> XmlDocument:
    seller = invoice.seller
    buyer_vat = (invoice.bill_to_tax_number or "").strip()
    return XmlDocument(
        number=number, uuid=str(uuid_module.uuid4()), counter=counter, previous_hash=previous,
        type_code=TYPE_CREDIT if note is not None else TYPE_INVOICE, subtype=STANDARD if buyer_vat else SIMPLIFIED, issued_at=issued_at,
        currency=getattr(invoice.currency, "value", invoice.currency), seller_name=(seller.legal_name if seller else "").strip(),
        seller_vat=((seller.tax_number if seller else "") or settings.registration_number).strip(), seller_crn=settings.seller_crn,
        seller_street=settings.seller_street, seller_building=settings.seller_building, seller_district=settings.seller_district,
        seller_city=settings.seller_city, seller_postal_code=settings.seller_postal_code, buyer_name=invoice.bill_to_name, buyer_vat=buyer_vat,
        lines=_lines_of_credit(invoice, note) if note is not None else _lines_of_invoice(invoice),
        billing_reference=invoice.number if note is not None else None, reason=note.reason if note is not None else "",
    )


def generate(context: TenantContext, source_kind: str, source_id: str, *, actor: str, now: datetime | None = None) -> tuple[ZatcaDocument, bool]:
    """Generate the e-invoice data of one sent invoice or credit note, once (a second call returns the first). Returns the
    document and whether it was made now. The counter and the previous hash are taken under the business's books lock."""
    from .invoice_models import CreditNoteStatus, InvoiceStatus
    from .store import open_business_store

    moment = now or utc_now()
    with open_business_store(context) as business:
        if source_kind == "invoice":
            invoice, note = business.get("invoices", source_id), None
            if invoice is None:
                raise ZatcaError(404, "Invoice not found.", "invoice_not_found")
            if invoice.status not in (InvoiceStatus.SENT, InvoiceStatus.PAID):
                raise ZatcaError(409, "Only a sent invoice gets e-invoice data.", "not_sent")
        elif source_kind == "credit_note":
            note = business.get("credit_notes", source_id)
            if note is None:
                raise ZatcaError(404, "Credit note not found.", "credit_note_not_found")
            if note.status != CreditNoteStatus.ISSUED:
                raise ZatcaError(409, "A void credit note gets no e-invoice data.", "credit_note_void")
            invoice = business.get("invoices", note.invoice_id)
        else:
            raise ZatcaError(422, "Choose an invoice or a credit note.", "invalid_source")
    with open_books_store(context) as store:
        settings = store.first("tax_settings")
        if settings is None or not settings.zatca_enabled or settings.jurisdiction != Jurisdiction.SA.value:
            raise ZatcaError(409, "E-invoicing is off for this business. A Saudi business turns it on in Books, Tax.", "zatca_off")
        if store.get_settings() is None:
            raise ZatcaError(409, "Set up the books first.", "books_not_set_up")
        store.lock_books()  # one generation at a time per business: the counter and the chain stay in order
        earlier = store.first("zatca_documents", source_kind=source_kind, source_id=source_id)
        if earlier is not None:
            return earlier, False
        chain = store.first("zatca_chain")
        if chain is None:
            chain = ZatcaChain(id=new_id("bzch"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, created_at=moment, updated_at=moment)
            try:
                store.add("zatca_chain", chain)
            except DuplicateError:
                chain = store.first("zatca_chain")
        chain = store.get("zatca_chain", chain.id, for_update=True)
        counter = chain.last_counter + 1
        previous = chain.last_hash or INITIAL_PREVIOUS_HASH
        issued_at = invoice.sent_at or moment
        if note is not None:
            issued_at = datetime.combine(note.issue_date, moment.timetz())
        number = note.number if note is not None else (invoice.number or invoice.id)
        document = _document(settings, invoice, number=number, counter=counter, previous=previous, issued_at=issued_at, note=note)
        xml = build_xml(document)
        digest = invoice_hash(xml)
        qr = qr_for_invoice(settings, invoice) if note is None else None
        record = ZatcaDocument(
            id=new_id("bzdoc"), tenant_id=context.tenant_id, workspace_id=context.workspace_id, created_at=moment, source_kind=source_kind,
            source_id=source_id, document_number=number, type_code=document.type_code, subtype=document.subtype, counter=counter, uuid=document.uuid,
            invoice_hash=digest, previous_hash=previous, issue_date=issued_at.date(), xml=xml, qr=qr or "", missing=missing_fields(document),
            created_by=actor,
        )
        store.add("zatca_documents", record)
        if not store.save("zatca_chain", chain.model_copy(update={"last_counter": counter, "last_hash": digest, "version": chain.version + 1,
                                                                 "updated_at": moment}), expected_version=chain.version):
            raise ZatcaError(409, "Another e-invoice was generated at the same time. Try again.", "stale")
    return record, True


# -- integration: an interface that refuses until onboarding ---------------------------------------------------------------


class ZatcaGateway(Protocol):
    """Clearance (standard invoices) and reporting (simplified invoices) with ZATCA. Needs a CSID; none is ever stored here."""

    def clear(self, document: ZatcaDocument) -> Any: ...
    def report(self, document: ZatcaDocument) -> Any: ...


class NotOnboarded:
    """The only gateway in this release: refuses with what the business must do first. Never opens a connection."""

    def clear(self, document: ZatcaDocument) -> Any:
        raise ZatcaError(409, ONBOARDING_REQUIRED, "zatca_onboarding_required")

    def report(self, document: ZatcaDocument) -> Any:
        raise ZatcaError(409, ONBOARDING_REQUIRED, "zatca_onboarding_required")


def gateway() -> ZatcaGateway:
    return NotOnboarded()


def readiness(settings: TaxSettings | None, billing: Any) -> dict[str, Any]:
    """What a Saudi business has and lacks before e-invoicing could go live (shown on the readiness panel)."""
    missing: list[str] = []
    if settings is None:
        missing.append("tax_settings")
    else:
        if settings.jurisdiction != Jurisdiction.SA.value:
            missing.append("jurisdiction_sa")
        if not settings.registered or not SA_VAT.match(settings.registration_number or ""):
            missing.append("seller_vat_number")
        for name in ("seller_crn", "seller_street", "seller_building", "seller_district", "seller_city", "seller_postal_code"):
            if not getattr(settings, name).strip():
                missing.append(name)
    if billing is None or not (billing.legal_name or "").strip():
        missing.append("seller_name")
    missing += list(ALWAYS_MISSING)
    return {"enabled": bool(settings and settings.zatca_enabled), "onboarding": settings.zatca_onboarding if settings else "not_started",
            "missing": missing, "notice": {"en": NOT_VERIFIED_EN, "ar": NOT_VERIFIED_AR}, "onboarding_required": ONBOARDING_REQUIRED}


def document_view(row: ZatcaDocument, *, xml: bool = False) -> dict[str, Any]:
    data = row.model_dump(mode="json", exclude={"tenant_id", "workspace_id", "xml"})
    if xml:
        data["xml"] = row.xml
    return data


__all__ = ["INITIAL_PREVIOUS_HASH", "NOT_VERIFIED_AR", "NOT_VERIFIED_EN", "ONBOARDING_REQUIRED", "NotOnboarded", "XmlDocument", "XmlLine",
           "ZatcaError", "build_xml", "canonical", "document_view", "gateway", "generate", "invoice_hash", "missing_fields", "phase1_qr",
           "qr_for_invoice", "readiness", "timestamp", "tlv", "tlv_decode"]
