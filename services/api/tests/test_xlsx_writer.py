"""The .xlsx writer is deterministic: an approved attachment is rendered again at send time and must hash the same."""

import hashlib
import time
import zipfile

import pytest

from anum_api import xlsx_writer


def _book() -> bytes:
    sheet = xlsx_writer.Sheet("Summary")
    sheet.row(["Vehicles", 3])
    return xlsx_writer.workbook([sheet])


def test_the_same_sheets_give_the_same_bytes_whatever_the_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    # zipfile stamps each entry with the local time unless told otherwise; move the clock between renders.
    clock = iter([time.struct_time((2026, 11, 27, 21, 0, s, 4, 331, 0)) for s in (0, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22)])
    monkeypatch.setattr(zipfile.time, "localtime", lambda *_: next(clock))
    first, second = _book(), _book()
    assert hashlib.sha256(first).hexdigest() == hashlib.sha256(second).hexdigest()
    with zipfile.ZipFile(__import__("io").BytesIO(first)) as archive:
        assert {info.date_time for info in archive.infolist()} == {(1980, 1, 1, 0, 0, 0)}
        assert all(info.compress_type == zipfile.ZIP_DEFLATED for info in archive.infolist())
