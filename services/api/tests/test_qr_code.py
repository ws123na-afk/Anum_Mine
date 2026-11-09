"""The dependency-free QR encoder (``anum_api/qr_code.py``, docs/business-tax.md#qr-code).

Each symbol is read back the way a scanner does: the format information (BCH checked) gives the mask, the data
modules are unmasked and read in the zigzag order, the blocks are de-interleaved and each block's Reed-Solomon
remainder must equal its error-correction codewords; then the byte-mode segment must give the input back.
"""

from __future__ import annotations

import base64
import os

import pytest

from anum_api import qr_code as Q
from anum_api.business.zatca import phase1_qr


def _format_mask(matrix: list[list[bool]]) -> int:
    bits = 0
    for i in range(6):
        bits |= int(matrix[i][8]) << i
    bits |= int(matrix[7][8]) << 6
    bits |= int(matrix[8][8]) << 7
    bits |= int(matrix[8][7]) << 8
    for i in range(9, 15):
        bits |= int(matrix[8][14 - i]) << i
    value = bits ^ 0x5412
    data, remainder = value >> 10, data_remainder(value >> 10)
    assert value & 0x3FF == remainder, "format information fails its BCH check"
    assert data >> 3 == Q.FORMAT_M
    return data & 7


def data_remainder(data: int) -> int:
    remainder = data
    for _ in range(10):
        remainder = (remainder << 1) ^ ((remainder >> 9) * 0x537)
    return remainder & 0x3FF


def decode(matrix: list[list[bool]]) -> bytes:
    size = len(matrix)
    version = (size - 17) // 4
    mask = _format_mask(matrix)
    symbol = Q._Symbol(version)
    symbol.draw_patterns()
    bits: list[int] = []
    right = size - 1
    while right >= 1:
        if right == 6:
            right = 5
        for vertical in range(size):
            for j in range(2):
                x = right - j
                y = size - 1 - vertical if ((right + 1) & 2) == 0 else vertical
                if not symbol.function[y][x]:
                    bits.append(int(matrix[y][x] ^ Q.mask_bit(mask, x, y)))
        right -= 2
    raw = Q.raw_modules(version) // 8
    words = [int("".join(map(str, bits[i * 8:i * 8 + 8])), 2) for i in range(raw)]
    count, ecc = Q.BLOCKS_M[version], Q.ECC_PER_BLOCK_M[version]
    short_count, short_len = count - raw % count, raw // count
    lengths = [short_len - ecc + (0 if i < short_count else 1) for i in range(count)]
    blocks: list[list[int]] = [[] for _ in range(count)]
    index = 0
    for i in range(max(lengths)):
        for j in range(count):
            if i < lengths[j]:
                blocks[j].append(words[index])
                index += 1
    for i in range(ecc):
        for j in range(count):
            assert words[index] == Q.rs_remainder(blocks[j], Q.rs_divisor(ecc))[i], "Reed-Solomon check failed"
            index += 1
    stream = "".join(f"{byte:08b}" for block in blocks for byte in block)
    assert stream[:4] == "0100"
    width = 8 if version < 10 else 16
    length = int(stream[4:4 + width], 2)
    return bytes(int(stream[4 + width + 8 * i:12 + width + 8 * i], 2) for i in range(length))


@pytest.mark.parametrize("length", [1, 14, 40, 120, 300, 700, 1200])
def test_every_symbol_reads_back_to_its_bytes(length: int) -> None:
    data = os.urandom(length)
    matrix = Q.encode(data)
    assert len(matrix) == Q.choose_version(length) * 4 + 17
    assert decode(matrix) == data


def test_finder_timing_and_every_mask_decode() -> None:
    data = b"SYNTHETIC QR CHECK"
    for mask in range(8):
        matrix = Q.encode(data, mask=mask)
        assert _format_mask(matrix) == mask and decode(matrix) == data
        assert all(matrix[0][x] for x in range(7)) and all(matrix[6][x] for x in range(7)) and not matrix[1][1]
        assert [matrix[6][x] for x in range(8, len(matrix) - 8)] == [x % 2 == 0 for x in range(8, len(matrix) - 8)]


def test_a_zatca_phase1_payload_fits_and_reads_back() -> None:
    text = phase1_qr("شركة اختبار تجريبية للتجارة", "300000000000003", __import__("datetime").datetime(2026, 11, 9, 10, 15), 1150, 150)
    matrix = Q.encode(text.encode("ascii"))
    assert decode(matrix).decode("ascii") == text and base64.b64decode(text)[0] == 1


def test_too_much_data_is_refused() -> None:
    with pytest.raises(Q.QrError):
        Q.encode(b"x" * 3000)
