"""The PDF writer for tyre reports: ANUM's shared, dependency-free writer (``anum_api/pdf_writer.py``).

Latin text uses the built-in Helvetica fonts; Arabic (a site or vehicle name, for instance) is
shaped and drawn with the embedded Noto Sans Arabic (docs/tyre-planning.md#exports).
"""

from __future__ import annotations

from ..pdf_writer import MARGIN, PAGE_HEIGHT, PAGE_WIDTH, PdfDocument, fit, wrap

__all__ = ["MARGIN", "PAGE_HEIGHT", "PAGE_WIDTH", "PdfDocument", "fit", "wrap"]
