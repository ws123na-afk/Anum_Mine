"""A small, dependency-free QR code encoder (ISO/IEC 18004): byte mode, error correction level M, versions 1 to 40.

Used to print the ZATCA QR code on Saudi invoices (docs/business-tax.md#qr-code), the way ``pdf_writer.py`` keeps the
PDFs dependency free. ``encode`` returns the module matrix (``True`` = dark), without the quiet zone; the caller draws
it with a margin of four modules. The mask is chosen with the standard's four penalty rules.

The algorithm follows the standard's structure: data codewords, Reed-Solomon error correction over GF(256) with the
polynomial 0x11D, block interleaving, function patterns, the zigzag placement, masking, and the BCH-coded format and
version information. ``tests/test_qr_code.py`` decodes the matrix back (format bits, unmasking, de-interleaving and the
Reed-Solomon check) so a placement error cannot pass.
"""

from __future__ import annotations

# Error correction codewords per block and number of blocks, level M, versions 1 to 40 (index 0 unused).
ECC_PER_BLOCK_M = (-1, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26, 26, 26, 26, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28,
                   28, 28, 28, 28, 28, 28, 28, 28, 28)
BLOCKS_M = (-1, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5, 5, 8, 9, 9, 10, 10, 11, 13, 14, 16, 17, 17, 18, 20, 21, 23, 25, 26, 28, 29, 31, 33, 35, 37, 38, 40, 43,
            45, 47, 49)
FORMAT_M = 0  # the two error-correction bits of level M in the format information


class QrError(ValueError):
    """The data does not fit in a version 40 symbol."""


def raw_modules(version: int) -> int:
    """Modules left for data and error correction once the function patterns are drawn."""
    result = (16 * version + 128) * version + 64
    if version >= 2:
        count = version // 7 + 2
        result -= (25 * count - 10) * count - 55
        if version >= 7:
            result -= 36
    return result


def data_codewords(version: int) -> int:
    return raw_modules(version) // 8 - ECC_PER_BLOCK_M[version] * BLOCKS_M[version]


def _mul(x: int, y: int) -> int:
    z = 0
    for i in reversed(range(8)):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z


def rs_divisor(degree: int) -> list[int]:
    result = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(degree):
            result[j] = _mul(result[j], root)
            if j + 1 < degree:
                result[j] ^= result[j + 1]
        root = _mul(root, 0x02)
    return result


def rs_remainder(data: list[int], divisor: list[int]) -> list[int]:
    result = [0] * len(divisor)
    for byte in data:
        factor = byte ^ result.pop(0)
        result.append(0)
        for i, coefficient in enumerate(divisor):
            result[i] ^= _mul(coefficient, factor)
    return result


def choose_version(length: int) -> int:
    for version in range(1, 41):
        count_bits = 8 if version < 10 else 16
        if 4 + count_bits + 8 * length <= data_codewords(version) * 8:
            return version
    raise QrError("too much data for a QR code")


def codewords(data: bytes, version: int) -> list[int]:
    """The final sequence: data codewords with their padding, split in blocks, error correction added and interleaved."""
    capacity = data_codewords(version) * 8
    count_bits = 8 if version < 10 else 16
    bits: list[int] = []

    def put(value: int, length: int) -> None:
        bits.extend((value >> i) & 1 for i in reversed(range(length)))

    put(0b0100, 4)
    put(len(data), count_bits)
    for byte in data:
        put(byte, 8)
    if len(bits) > capacity:
        raise QrError("too much data for this version")
    put(0, min(4, capacity - len(bits)))
    put(0, (-len(bits)) % 8)
    pad = 0xEC
    while len(bits) < capacity:
        put(pad, 8)
        pad ^= 0xEC ^ 0x11
    payload = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    blocks_count, ecc_len = BLOCKS_M[version], ECC_PER_BLOCK_M[version]
    raw = raw_modules(version) // 8
    short_count = blocks_count - raw % blocks_count
    short_len = raw // blocks_count
    divisor = rs_divisor(ecc_len)
    blocks: list[list[int]] = []
    k = 0
    for i in range(blocks_count):
        size = short_len - ecc_len + (0 if i < short_count else 1)
        part = payload[k:k + size]
        k += size
        ecc = rs_remainder(part, divisor)
        if i < short_count:
            part = [*part, 0]  # a placeholder so every block has the same length while interleaving
        blocks.append(part + ecc)
    out: list[int] = []
    for i in range(len(blocks[0])):
        for j, block in enumerate(blocks):
            if i != short_len - ecc_len or j >= short_count:
                out.append(block[i])
    return out


def alignment_positions(version: int) -> list[int]:
    if version == 1:
        return []
    count = version // 7 + 2
    step = (version * 8 + count * 3 + 5) // (count * 4 - 4) * 2
    size = version * 4 + 17
    return [6] + [size - 7 - i * step for i in reversed(range(count - 1))]


class _Symbol:
    def __init__(self, version: int) -> None:
        self.version = version
        self.size = version * 4 + 17
        self.dark = [[False] * self.size for _ in range(self.size)]
        self.function = [[False] * self.size for _ in range(self.size)]

    def set_function(self, x: int, y: int, dark: bool) -> None:
        self.dark[y][x] = dark
        self.function[y][x] = True

    def draw_patterns(self) -> None:
        size = self.size
        for i in range(size):
            self.set_function(6, i, i % 2 == 0)
            self.set_function(i, 6, i % 2 == 0)
        for cx, cy in ((3, 3), (size - 4, 3), (3, size - 4)):
            for dy in range(-4, 5):
                for dx in range(-4, 5):
                    x, y = cx + dx, cy + dy
                    if 0 <= x < size and 0 <= y < size:
                        distance = max(abs(dx), abs(dy))
                        self.set_function(x, y, distance not in (2, 4))
        positions = alignment_positions(self.version)
        last = len(positions) - 1
        for i, cx in enumerate(positions):
            for j, cy in enumerate(positions):
                if (i == 0 and j == 0) or (i == 0 and j == last) or (i == last and j == 0):
                    continue
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        self.set_function(cx + dx, cy + dy, max(abs(dx), abs(dy)) != 1)
        self.draw_format(0)  # reserved now, written with the chosen mask later
        if self.version >= 7:
            remainder = self.version
            for _ in range(12):
                remainder = (remainder << 1) ^ ((remainder >> 11) * 0x1F25)
            bits = self.version << 12 | remainder
            for i in range(18):
                bit = (bits >> i) & 1 == 1
                a, b = size - 11 + i % 3, i // 3
                self.set_function(a, b, bit)
                self.set_function(b, a, bit)

    def draw_format(self, mask: int) -> None:
        data = FORMAT_M << 3 | mask
        remainder = data
        for _ in range(10):
            remainder = (remainder << 1) ^ ((remainder >> 9) * 0x537)
        bits = (data << 10 | remainder) ^ 0x5412
        size = self.size

        def bit(i: int) -> bool:
            return (bits >> i) & 1 == 1

        for i in range(6):
            self.set_function(8, i, bit(i))
        self.set_function(8, 7, bit(6))
        self.set_function(8, 8, bit(7))
        self.set_function(7, 8, bit(8))
        for i in range(9, 15):
            self.set_function(14 - i, 8, bit(i))
        for i in range(8):
            self.set_function(size - 1 - i, 8, bit(i))
        for i in range(8, 15):
            self.set_function(8, size - 15 + i, bit(i))
        self.set_function(8, size - 8, True)

    def place(self, data: list[int]) -> None:
        size = self.size
        i = 0
        right = size - 1
        while right >= 1:
            if right == 6:
                right = 5
            for vertical in range(size):
                for j in range(2):
                    x = right - j
                    upward = ((right + 1) & 2) == 0
                    y = size - 1 - vertical if upward else vertical
                    if not self.function[y][x] and i < len(data) * 8:
                        self.dark[y][x] = (data[i >> 3] >> (7 - (i & 7))) & 1 == 1
                        i += 1
            right -= 2

    def apply_mask(self, mask: int) -> None:
        for y in range(self.size):
            for x in range(self.size):
                if not self.function[y][x] and mask_bit(mask, x, y):
                    self.dark[y][x] = not self.dark[y][x]


def mask_bit(mask: int, x: int, y: int) -> bool:
    if mask == 0:
        return (x + y) % 2 == 0
    if mask == 1:
        return y % 2 == 0
    if mask == 2:
        return x % 3 == 0
    if mask == 3:
        return (x + y) % 3 == 0
    if mask == 4:
        return (x // 3 + y // 2) % 2 == 0
    if mask == 5:
        return x * y % 2 + x * y % 3 == 0
    if mask == 6:
        return (x * y % 2 + x * y % 3) % 2 == 0
    return ((x + y) % 2 + x * y % 3) % 2 == 0


def penalty(dark: list[list[bool]]) -> int:
    """The standard's four penalty rules: runs of five or more, 2x2 blocks, finder-like patterns, dark/light balance."""
    size = len(dark)
    score = 0
    lines = [row for row in dark] + [[dark[y][x] for y in range(size)] for x in range(size)]
    finder_a = [True, False, True, True, True, False, True, False, False, False, False]
    finder_b = finder_a[::-1]
    for line in lines:
        run = 1
        for i in range(1, size):
            if line[i] == line[i - 1]:
                run += 1
            else:
                if run >= 5:
                    score += 3 + run - 5
                run = 1
        if run >= 5:
            score += 3 + run - 5
        for i in range(size - 10):
            window = line[i:i + 11]
            if window == finder_a or window == finder_b:
                score += 40
    for y in range(size - 1):
        for x in range(size - 1):
            if dark[y][x] == dark[y][x + 1] == dark[y + 1][x] == dark[y + 1][x + 1]:
                score += 3
    total = size * size
    dark_count = sum(sum(row) for row in dark)
    score += 10 * max(0, (abs(dark_count * 20 - total * 10) + total - 1) // total - 1)
    return score


def encode(data: bytes, *, version: int | None = None, mask: int | None = None) -> list[list[bool]]:
    """The matrix of a QR code holding ``data`` (byte mode, level M), smallest version unless one is given."""
    chosen = version or choose_version(len(data))
    words = codewords(data, chosen)
    best: tuple[int, list[list[bool]]] | None = None
    for candidate in (range(8) if mask is None else (mask,)):
        symbol = _Symbol(chosen)
        symbol.draw_patterns()
        symbol.place(words)
        symbol.apply_mask(candidate)
        symbol.draw_format(candidate)
        score = penalty(symbol.dark)
        if best is None or score < best[0]:
            best = (score, symbol.dark)
    if best is None:
        raise QrError("no mask")
    return best[1]


__all__ = ["QrError", "alignment_positions", "choose_version", "codewords", "data_codewords", "encode", "mask_bit", "raw_modules", "rs_divisor",
           "rs_remainder"]
