"""Nazwy urzadzen: serwer, na ktorym dziala panel, i miejsce na kopie (udzial SMB).

To etykiety ustawiane w panelu (Kopie -> Ustawienia kopii -> Zmien nazwe), a nie tekst
wpisany na sztywno - kazdy nazywa swoje urzadzenia po swojemu. Teksty panelu stawiaja nazwe
zawsze w formie podstawowej ("Kopia trafia tylko tutaj. Na urzadzeniu {server} nic nie
zostaje."), wiec dziala kazda nazwa, bez odmiany.

Nazwy sa zwyklymi ustawieniami (name_server, name_storage): pelna kopia zabiera je ze soba,
a odtworzenie przywraca.
"""
from __future__ import annotations

DEFAULTS = {"name_server": "Pi", "name_storage": "NAS"}
MAX_LENGTH = 24

_current = dict(DEFAULTS)


def clean(value, key: str) -> str:
    """Nazwa do zapisania: bez znakow sterujacych i zbednych spacji, najwyzej MAX_LENGTH
    znakow. Pusta - nazwa domyslna."""
    text = "".join(ch for ch in str(value or "") if ch.isprintable())
    text = " ".join(text.split())[:MAX_LENGTH].strip()
    return text or DEFAULTS[key]


def load(cfg: dict) -> None:
    """Biezace nazwy z ustawien - panel wola to na poczatku kazdego zadania."""
    for key in DEFAULTS:
        _current[key] = clean(cfg.get(key), key)


def server(cfg: dict | None = None) -> str:
    return clean(cfg.get("name_server"), "name_server") if cfg is not None else _current["name_server"]


def storage(cfg: dict | None = None) -> str:
    return clean(cfg.get("name_storage"), "name_storage") if cfg is not None else _current["name_storage"]


def kw(cfg: dict | None = None) -> dict:
    """Do tekstow z {server} i {storage}: _("...", **names.kw())."""
    return {"server": server(cfg), "storage": storage(cfg)}
