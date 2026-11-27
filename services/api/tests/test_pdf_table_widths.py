"""PDF table column widths (``pdf_writer.column_widths``): amounts and short headers are never cut."""

import re

import pytest
from test_vehicles_fleet import FLEET, OWNER, add_costs, clean, client, fleet_vehicles, month_ago  # noqa: F401

from anum_api import pdf_writer
from anum_api.pdf_text import text_width
from anum_api.pdf_writer import MARGIN, PAGE_WIDTH, column_widths, fit


def test_room_left_is_shared_and_every_column_gets_its_need() -> None:
    widths = column_widths([40, 60, 100], 400)
    assert abs(sum(widths) - 400) < 1e-6
    assert all(width >= need for width, need in zip(widths, [40, 60, 100]))


def test_too_wide_a_table_shrinks_only_its_widest_columns() -> None:
    widths = column_widths([30, 40, 50, 400, 300], 400)
    assert abs(sum(widths) - 400) < 1e-6
    assert widths[:3] == [30, 40, 50]
    assert widths[3] == widths[4] < 300


def test_the_fleet_totals_table_prints_every_amount_whole() -> None:
    # The monthly client report's totals: ten columns, amounts in the thousands. Before, "946.50" printed as "946.~".
    size = 7.0
    headers = ["Currency", "Fuel", "Service", "Tyres", "Other", "Total", "Last month", "Change", "Per km", "Linked", "Recorded"]
    row = ["SAR", "12,946.50", "3,410.00", "1,250.00", "880.25", "18,486.75", "17,902.10", "+3.3%", "0.268", "16,000.00", "2,486.75"]
    needs = [max(text_width(h, size, True), text_width(v, size)) + 4 for h, v in zip(headers, row)]
    widths = column_widths(needs, PAGE_WIDTH - 2 * MARGIN)
    for header, value, width in zip(headers, row, widths):
        assert fit(value, width - 4, size) == value
        assert fit(header, width - 4, size, True) == header


def test_the_monthly_fleet_pdf_cuts_no_number(monkeypatch: pytest.MonkeyPatch) -> None:
    # The real client report in both languages; before, "2,400.00" printed as "2,400.~" and "Other" as "Ot~".
    cut: list[str] = []
    real = pdf_writer.fit

    def watch(text: str, width: float, size: float, bold: bool = False) -> str:
        out = real(text, width, size, bold)
        if out != text:
            cut.append(text)
        return out

    monkeypatch.setattr(pdf_writer, "fit", watch)
    add_costs(fleet_vehicles())
    for language in ("en", "ar"):
        response = client.post(f"{FLEET}/report", headers=OWNER, json={"month": month_ago(1, 1).strftime("%Y-%m"), "company_name": "Gulf Mixers LLC",
                                                                        "format": "pdf", "language": language})
        assert response.status_code == 200, response.text
    assert [text for text in cut if re.search(r"\d", text)] == []
    assert "Other" not in cut and "Currency" not in cut
