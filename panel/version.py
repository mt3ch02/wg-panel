"""Wersja panelu - z pliku VERSION obok katalogu panel/ (w repozytorium albo w /opt/wg-panel).

Jeden numer dla panelu, wgctl, instalatora i kopii. Brak pliku (np. uruchomienie z czesciowej
kopii) to "?" - nigdy blad."""
from __future__ import annotations

import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get(root: str | None = None) -> str:
    try:
        with open(os.path.join(root or ROOT, "VERSION"), encoding="utf8") as handle:
            return handle.read().strip() or "?"
    except OSError:
        return "?"
