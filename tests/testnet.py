"""Siec domowa uzywana w testach: 192.168.50.0/24, Pi .10, NAS .11, port 41194.

Panel ma neutralne wartosci domyslne (192.168.1.0/24, port 51820), a przy instalacji wpisuje
siec wykryta na Pi. Testy maja swoja stala siec - ustawiaja ja tutaj, w jednym miejscu.
"""
from __future__ import annotations

NET = {"lan": "192.168.50.0/24", "server_lan": "192.168.50.10", "nas": "192.168.50.11",
       "smb_host": "192.168.50.11", "listen_port": "41194", "endpoint": "zmien.mnie.example:41194"}


def use(db) -> None:
    """Bazy tworzone w tym procesie dostaja siec testow."""
    db.DEFAULT_SETTINGS.update({k: v for k, v in NET.items() if k != "nas"})
    db.SEED_NAS = NET["nas"]


def seed_file(path: str) -> None:
    """Baza dla panelu albo wgctl uruchamianego osobno - przed jego pierwszym startem."""
    from panel import db
    conn = db.connect(path)
    for key, value in NET.items():
        db.set_setting(conn, key, value)
    conn.close()
