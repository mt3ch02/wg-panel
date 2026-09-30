"""Kody QR. Enkoder w czystym Pythonie - qrencode nie jest juz potrzebny.

Kodowanie to kilkadziesiat tysiecy operacji w Pythonie (dobor maski sprawdza osiem
wariantow), co na Raspberry Pi zajmuje ulamek sekundy. Strona peera wyswietla ten sam
kod przy kazdym wejsciu, wiec trzymamy gotowe SVG w pamieci.
"""
from __future__ import annotations

import hashlib
import html
import threading

from . import qrcode

_CACHE: dict[str, str] = {}
_CACHE_LOCK = threading.Lock()
_CACHE_LIMIT = 64


def svg(text: str) -> str:
    key = hashlib.sha256(text.encode()).hexdigest()
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
    if cached is not None:
        return cached
    try:
        out = qrcode.svg(text)
    except ValueError as exc:
        return f'<p class="muted">Nie udalo sie wygenerowac kodu QR: {html.escape(str(exc))}</p>'
    with _CACHE_LOCK:
        if len(_CACHE) >= _CACHE_LIMIT:
            _CACHE.clear()
        _CACHE[key] = out
    return out


def terminal(text: str) -> str:
    return qrcode.to_terminal(qrcode.encode(text))
