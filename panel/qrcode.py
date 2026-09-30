"""Enkoder kodow QR w czystym Pythonie - bez qrencode i bez bibliotek.

Tryb bajtowy, wersje 1-40, automatyczny dobor wersji i maski wg ISO/IEC 18004.
Struktura za referencyjna implementacja Project Nayuki (licencja MIT).

Konfiguracja WireGuarda ma ~300 znakow, co daje wersje 10-13 przy poziomie M.
"""
from __future__ import annotations

# --- tablice ze standardu: [poziom][wersja] ---
# poziomy: 0=L, 1=M, 2=Q, 3=H

_ECC_CODEWORDS_PER_BLOCK = (
    (-1, 7, 10, 15, 20, 26, 18, 20, 24, 30, 18, 20, 24, 26, 30, 22, 24, 28, 30, 28, 28,
     28, 28, 30, 30, 26, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30),
    (-1, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26, 26, 26,
     26, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28),
    (-1, 13, 22, 18, 26, 18, 24, 18, 22, 20, 24, 28, 26, 24, 20, 30, 24, 28, 28, 26, 30,
     28, 30, 30, 30, 30, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30),
    (-1, 17, 28, 22, 16, 22, 28, 26, 26, 24, 28, 24, 28, 22, 24, 24, 30, 28, 28, 26, 28,
     30, 24, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30),
)

_NUM_ERROR_CORRECTION_BLOCKS = (
    (-1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 4, 4, 4, 4, 4, 6, 6, 6, 6, 7, 8,
     8, 9, 9, 10, 12, 12, 12, 13, 14, 15, 16, 17, 18, 19, 19, 20, 21, 22, 24, 25),
    (-1, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5, 5, 8, 9, 9, 10, 10, 11, 13, 14, 16,
     17, 17, 18, 20, 21, 23, 25, 26, 28, 29, 31, 33, 35, 37, 38, 40, 43, 45, 47, 49),
    (-1, 1, 1, 2, 2, 4, 4, 6, 6, 8, 8, 8, 10, 12, 16, 12, 17, 16, 18, 21, 20,
     23, 23, 25, 27, 29, 34, 34, 35, 38, 40, 43, 45, 48, 51, 53, 56, 59, 62, 65, 68),
    (-1, 1, 1, 2, 4, 4, 4, 5, 6, 8, 8, 11, 11, 16, 16, 18, 16, 19, 21, 25, 25,
     25, 34, 30, 32, 35, 37, 40, 42, 45, 48, 51, 54, 57, 60, 63, 66, 70, 74, 77, 81),
)

# bity formatu dla poziomow L, M, Q, H
_FORMAT_BITS = (1, 0, 3, 2)

_PENALTY_N1, _PENALTY_N2, _PENALTY_N3, _PENALTY_N4 = 3, 3, 40, 10


def _num_raw_data_modules(ver: int) -> int:
    result = (16 * ver + 128) * ver + 64
    if ver >= 2:
        numalign = ver // 7 + 2
        result -= (25 * numalign - 10) * numalign - 55
        if ver >= 7:
            result -= 36
    return result


def _num_data_codewords(ver: int, ecl: int) -> int:
    return (_num_raw_data_modules(ver) // 8
            - _ECC_CODEWORDS_PER_BLOCK[ecl][ver] * _NUM_ERROR_CORRECTION_BLOCKS[ecl][ver])


# --- Reed-Solomon nad GF(2^8), wielomian 0x11D ---

def _rs_multiply(x: int, y: int) -> int:
    z = 0
    for i in reversed(range(8)):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z


def _rs_divisor(degree: int) -> list[int]:
    result = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(degree):
            result[j] = _rs_multiply(result[j], root)
            if j + 1 < degree:
                result[j] ^= result[j + 1]
        root = _rs_multiply(root, 0x02)
    return result


def _rs_remainder(data: list[int], divisor: list[int]) -> list[int]:
    result = [0] * len(divisor)
    for b in data:
        factor = b ^ result.pop(0)
        result.append(0)
        for i, coef in enumerate(divisor):
            result[i] ^= _rs_multiply(coef, factor)
    return result


class QrCode:
    def __init__(self, version: int, ecl: int, data_codewords: list[int], mask: int = -1):
        self.version = version
        self.size = version * 4 + 17
        self.ecl = ecl
        self.modules = [[False] * self.size for _ in range(self.size)]
        self.is_function = [[False] * self.size for _ in range(self.size)]

        self._draw_function_patterns()
        all_codewords = self._add_ecc_and_interleave(data_codewords)
        self._draw_codewords(all_codewords)

        if mask == -1:
            best_penalty = None
            for candidate in range(8):
                self._apply_mask(candidate)
                self._draw_format_bits(candidate)
                penalty = self._penalty_score()
                if best_penalty is None or penalty < best_penalty:
                    mask, best_penalty = candidate, penalty
                self._apply_mask(candidate)  # XOR jest odwracalny
        self.mask = mask
        self._apply_mask(mask)
        self._draw_format_bits(mask)

    # --- wzorce stale ---

    def _set_function(self, x: int, y: int, dark: bool) -> None:
        self.modules[y][x] = dark
        self.is_function[y][x] = True

    def _draw_function_patterns(self) -> None:
        for i in range(self.size):
            self._set_function(6, i, i % 2 == 0)
            self._set_function(i, 6, i % 2 == 0)

        self._draw_finder(3, 3)
        self._draw_finder(self.size - 4, 3)
        self._draw_finder(3, self.size - 4)

        positions = self._alignment_positions()
        count = len(positions)
        for i in range(count):
            for j in range(count):
                if not ((i == 0 and j == 0) or (i == 0 and j == count - 1)
                        or (i == count - 1 and j == 0)):
                    self._draw_alignment(positions[i], positions[j])

        self._draw_format_bits(0)
        self._draw_version()

    def _draw_finder(self, x: int, y: int) -> None:
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                xx, yy = x + dx, y + dy
                if 0 <= xx < self.size and 0 <= yy < self.size:
                    dist = max(abs(dx), abs(dy))
                    self._set_function(xx, yy, dist not in (2, 4))

    def _draw_alignment(self, x: int, y: int) -> None:
        for dy in range(-2, 3):
            for dx in range(-2, 3):
                self._set_function(x + dx, y + dy, max(abs(dx), abs(dy)) != 1)

    def _alignment_positions(self) -> list[int]:
        ver = self.version
        if ver == 1:
            return []
        numalign = ver // 7 + 2
        step = (ver * 8 + numalign * 3 + 5) // (numalign * 4 - 4) * 2
        result = [self.size - 7 - i * step for i in range(numalign - 1)] + [6]
        return list(reversed(result))

    def _draw_format_bits(self, mask: int) -> None:
        data = _FORMAT_BITS[self.ecl] << 3 | mask
        rem = data
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        bits = (data << 10 | rem) ^ 0x5412

        for i in range(0, 6):
            self._set_function(8, i, (bits >> i) & 1 != 0)
        self._set_function(8, 7, (bits >> 6) & 1 != 0)
        self._set_function(8, 8, (bits >> 7) & 1 != 0)
        self._set_function(7, 8, (bits >> 8) & 1 != 0)
        for i in range(9, 15):
            self._set_function(14 - i, 8, (bits >> i) & 1 != 0)

        for i in range(0, 8):
            self._set_function(self.size - 1 - i, 8, (bits >> i) & 1 != 0)
        for i in range(8, 15):
            self._set_function(8, self.size - 15 + i, (bits >> i) & 1 != 0)
        self._set_function(8, self.size - 8, True)  # zawsze ciemny modul

    def _draw_version(self) -> None:
        if self.version < 7:
            return
        rem = self.version
        for _ in range(12):
            rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
        bits = self.version << 12 | rem
        for i in range(18):
            bit = (bits >> i) & 1 != 0
            a = self.size - 11 + i % 3
            b = i // 3
            self._set_function(a, b, bit)
            self._set_function(b, a, bit)

    # --- dane ---

    def _add_ecc_and_interleave(self, data: list[int]) -> list[int]:
        ver, ecl = self.version, self.ecl
        numblocks = _NUM_ERROR_CORRECTION_BLOCKS[ecl][ver]
        blockecclen = _ECC_CODEWORDS_PER_BLOCK[ecl][ver]
        rawcodewords = _num_raw_data_modules(ver) // 8
        numshortblocks = numblocks - rawcodewords % numblocks
        shortblocklen = rawcodewords // numblocks

        blocks = []
        divisor = _rs_divisor(blockecclen)
        k = 0
        for i in range(numblocks):
            length = shortblocklen - blockecclen + (0 if i < numshortblocks else 1)
            chunk = data[k:k + length]
            k += length
            ecc = _rs_remainder(chunk, divisor)
            if i < numshortblocks:
                chunk = chunk + [0]
            blocks.append(chunk + ecc)

        result = []
        for i in range(len(blocks[0])):
            for j, block in enumerate(blocks):
                if i != shortblocklen - blockecclen or j >= numshortblocks:
                    result.append(block[i])
        return result

    def _draw_codewords(self, data: list[int]) -> None:
        i = 0
        right = self.size - 1
        while right >= 1:
            if right == 6:
                right = 5
            for vert in range(self.size):
                for j in range(2):
                    x = right - j
                    upward = (right + 1) & 2 == 0
                    y = (self.size - 1 - vert) if upward else vert
                    if not self.is_function[y][x] and i < len(data) * 8:
                        self.modules[y][x] = (data[i >> 3] >> (7 - (i & 7))) & 1 != 0
                        i += 1
            right -= 2

    def _apply_mask(self, mask: int) -> None:
        masks = (
            lambda x, y: (x + y) % 2,
            lambda x, y: y % 2,
            lambda x, y: x % 3,
            lambda x, y: (x + y) % 3,
            lambda x, y: (x // 3 + y // 2) % 2,
            lambda x, y: x * y % 2 + x * y % 3,
            lambda x, y: (x * y % 2 + x * y % 3) % 2,
            lambda x, y: ((x + y) % 2 + x * y % 3) % 2,
        )
        fn = masks[mask]
        for y in range(self.size):
            for x in range(self.size):
                if not self.is_function[y][x] and fn(x, y) == 0:
                    self.modules[y][x] = not self.modules[y][x]

    # --- punktacja masek ---

    def _penalty_score(self) -> int:
        result = 0
        size = self.size
        modules = self.modules

        for y in range(size):
            run_color = False
            run_x = 0
            history = [0] * 7
            for x in range(size):
                if modules[y][x] == run_color:
                    run_x += 1
                    if run_x == 5:
                        result += _PENALTY_N1
                    elif run_x > 5:
                        result += 1
                else:
                    self._history_add(run_x, history)
                    if not run_color:
                        result += self._finder_count(history) * _PENALTY_N3
                    run_color = modules[y][x]
                    run_x = 1
            result += self._finder_terminate(run_color, run_x, history) * _PENALTY_N3

        for x in range(size):
            run_color = False
            run_y = 0
            history = [0] * 7
            for y in range(size):
                if modules[y][x] == run_color:
                    run_y += 1
                    if run_y == 5:
                        result += _PENALTY_N1
                    elif run_y > 5:
                        result += 1
                else:
                    self._history_add(run_y, history)
                    if not run_color:
                        result += self._finder_count(history) * _PENALTY_N3
                    run_color = modules[y][x]
                    run_y = 1
            result += self._finder_terminate(run_color, run_y, history) * _PENALTY_N3

        for y in range(size - 1):
            for x in range(size - 1):
                color = modules[y][x]
                if color == modules[y][x + 1] == modules[y + 1][x] == modules[y + 1][x + 1]:
                    result += _PENALTY_N2

        dark = sum(1 for row in modules for cell in row if cell)
        total = size * size
        k = (abs(dark * 20 - total * 10) + total - 1) // total - 1
        result += k * _PENALTY_N4
        return result

    def _history_add(self, current: int, history: list[int]) -> None:
        if history[0] == 0:
            current += self.size
        history.pop()
        history.insert(0, current)

    def _finder_count(self, history: list[int]) -> int:
        n = history[1]
        core = n > 0 and history[2] == history[4] == history[5] == n and history[3] == n * 3
        return ((1 if core and history[0] >= n * 4 and history[6] >= n else 0)
                + (1 if core and history[6] >= n * 4 and history[0] >= n else 0))

    def _finder_terminate(self, run_color: bool, run: int, history: list[int]) -> int:
        if run_color:
            self._history_add(run, history)
            run = 0
        run += self.size
        self._history_add(run, history)
        return self._finder_count(history)


def encode(text: str, ecl: int = 1) -> QrCode:
    """Najmniejsza wersja mieszczaca tekst; poziom podnoszony, jesli sie zmiesci."""
    data = text.encode("utf8")
    for version in range(1, 41):
        count_bits = 8 if version <= 9 else 16
        needed = 4 + count_bits + len(data) * 8
        if needed <= _num_data_codewords(version, ecl) * 8:
            break
    else:
        raise ValueError("tekst za dlugi na kod QR")

    # podnies poziom korekcji, jesli dane nadal sie mieszcza w tej samej wersji
    for better in (1, 2, 3):
        if better > ecl and needed <= _num_data_codewords(version, better) * 8:
            ecl = better

    bits: list[int] = []

    def append(value: int, length: int) -> None:
        for i in reversed(range(length)):
            bits.append((value >> i) & 1)

    append(0b0100, 4)                 # tryb bajtowy
    append(len(data), count_bits)
    for byte in data:
        append(byte, 8)

    capacity = _num_data_codewords(version, ecl) * 8
    append(0, min(4, capacity - len(bits)))
    append(0, -len(bits) % 8)
    pad = 0xEC
    while len(bits) < capacity:
        append(pad, 8)
        pad ^= 0xEC ^ 0x11

    codewords = [0] * (len(bits) // 8)
    for i, bit in enumerate(bits):
        codewords[i >> 3] |= bit << (7 - (i & 7))
    return QrCode(version, ecl, codewords)


def to_svg(qr: QrCode, border: int = 2, scale: int = 1) -> str:
    """SVG jedna sciezka - kilka KB, skaluje sie bez rozmycia."""
    size = qr.size + border * 2
    parts = []
    for y in range(qr.size):
        for x in range(qr.size):
            if qr.modules[y][x]:
                parts.append(f"M{x + border},{y + border}h1v1h-1z")
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" '
        f'shape-rendering="crispEdges" width="{size * scale}" height="{size * scale}">'
        f'<rect width="100%" height="100%" fill="#ffffff"/>'
        f'<path d="{"".join(parts)}" fill="#000000"/></svg>'
    )


def svg(text: str) -> str:
    return to_svg(encode(text))


def to_terminal(qr: QrCode, border: int = 2) -> str:
    """Kod QR w terminalu - dwa rzedy modulow na jeden wiersz znakow."""
    size = qr.size + border * 2

    def dark(x: int, y: int) -> bool:
        x -= border
        y -= border
        return 0 <= x < qr.size and 0 <= y < qr.size and qr.modules[y][x]

    lines = []
    for y in range(0, size, 2):
        row = []
        for x in range(size):
            top, bottom = dark(x, y), dark(x, y + 1)
            # jasne tlo, ciemne moduly - czytelne na ciemnym i jasnym terminalu
            if top and bottom:
                row.append(" ")
            elif top:
                row.append("\u2584")
            elif bottom:
                row.append("\u2580")
            else:
                row.append("\u2588")
        lines.append("".join(row))
    return "\n".join(lines)
