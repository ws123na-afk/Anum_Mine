"""The PDF bank statement reader (docs/finance-statements.md).

Layers, each in its own module and tested on its own:

* ``extract``: the file opened (caps, password in memory only) and every character's box turned into
  positioned words in reading orientation; page images of a scan.
* ``ocr``: scanned pages through the vault's OCR service, with word boxes.
* ``columns``: lines, tokens, the header (English and Arabic) and column bands, or columns found from
  the content when there is no header.
* ``dates`` and ``amounts``: every date and money format the Gulf banks print.
* ``engine``: rows, signs (from the columns, CR/DR or the running balance), confidence, and the balance
  checks for the whole statement, every row and every page.

Everything here is pure except ``ocr.ocr_page`` (the OCR service call). No bank's layout is built in:
the owner's corrected column mapping is kept as a template (``finance/pdf_templates.py``).
"""

from .amounts import money_pattern
from .columns import MONEY_ROLES, ROLES, Column, fingerprint
from .dates import hijri_to_gregorian
from .engine import LOW_CONFIDENCE, Check, Row, Statement, apply_roles, mask_numbers, parse, verify
from .errors import PdfStatementError
from .extract import MAX_OCR_PAGES, MAX_PAGES, MAX_PASSWORD, MAX_PDF_BYTES, Extracted, PageImage, extract
from .ocr import ocr_page, read_scanned, words_from_ocr
from .words import Word

__all__ = [
    "LOW_CONFIDENCE", "MAX_OCR_PAGES", "MAX_PAGES", "MAX_PASSWORD", "MAX_PDF_BYTES", "MONEY_ROLES", "ROLES", "Check", "Column", "Extracted",
    "PageImage", "PdfStatementError", "Row", "Statement", "Word", "apply_roles", "extract", "fingerprint", "hijri_to_gregorian",
    "mask_numbers", "money_pattern", "ocr_page", "parse", "read_scanned", "verify", "words_from_ocr",
]
