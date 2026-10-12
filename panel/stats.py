"""Ostatni handshake i laczny ruch peerow - pamietane w bazie, zeby przetrwaly restart.

WireGuard trzyma czas handshake'u i liczniki ruchu tylko w jadrze. Po restarcie Pi (albo
samego interfejsu) zaczynaja od zera: lista pokazywala "nigdy" i 0 B, choc wykres - liczony
z probek w bazie - mial historie. Panel co 30 s dopisuje przyrosty do tabeli peer_stats:

  przyrost = licznik - ostatnio widziany; gdy licznik spadl, zmienil sie klucz (rotacja) albo
  Pi uruchomilo sie od nowa (inny boot_id), licznik liczy od zera - przyrostem jest on caly.

Zapis tylko przy zmianie, wiec bezczynny peer nie pisze na karte. Wyswietlanie (view) dolicza
przyrost od ostatniego zapisu - lista jest aktualna, a nie sprzed 30 sekund.
Wiersz jest per osoba (peer_id), nie per klucz: rotacja kluczy nie zeruje historii.
"""
from __future__ import annotations

import sqlite3
import time

from . import db

BOOT_ID = "/proc/sys/kernel/random/boot_id"


def boot_id() -> str:
    """Identyfikator biezacego uruchomienia systemu - zmienia sie przy kazdym starcie Pi."""
    try:
        with open(BOOT_ID, encoding="ascii") as handle:
            return handle.read().strip()
    except OSError:
        return ""


def booted_at() -> int:
    """Kiedy Pi wystartowalo (z /proc/uptime) - liczniki jadra liczone sa od tej chwili."""
    try:
        with open("/proc/uptime", encoding="ascii") as handle:
            return int(time.time() - float(handle.read().split()[0]))
    except (OSError, ValueError, IndexError):
        return 0


def _history(conn: sqlite3.Connection, pubkey: str) -> tuple:
    """Probki wykresu (tabela traffic, do 8 dni): suma przyrostow liczona tak jak na wykresie,
    ostatnia probka (ts, rx, tx) i czas pierwszej. Start pamieci od historii, a nie od zera."""
    rx_sum = tx_sum = first = 0
    last = None
    for ts, rx, tx in conn.execute(
            "SELECT ts, rx, tx FROM traffic WHERE pubkey = ? ORDER BY ts", (pubkey,)):
        if last is None:
            first = ts
        else:
            rx_sum += rx - last[1] if rx >= last[1] else rx
            tx_sum += tx - last[2] if tx >= last[2] else tx
        last = (ts, rx, tx)
    return rx_sum, tx_sum, last, first


def _counters(stat: dict) -> tuple:
    return (int(stat.get("rx") or 0), int(stat.get("tx") or 0),
            int(stat.get("latest_handshake") or 0))


def _delta(row, pubkey: str, boot: str, rx: int, tx: int) -> tuple:
    """Przyrost od ostatniej obserwacji. Liczniki zaczely od zera -> przyrostem jest licznik."""
    if (row is None or row["pubkey"] != pubkey or row["boot_id"] != boot
            or rx < row["last_rx"] or tx < row["last_tx"]):
        return rx, tx
    return rx - row["last_rx"], tx - row["last_tx"]


def _rows(conn: sqlite3.Connection) -> dict:
    return {row["peer_id"]: row for row in conn.execute("SELECT * FROM peer_stats")}


def update(conn: sqlite3.Connection, status: dict, now: float | None = None,
           boot: str | None = None, started: int | None = None) -> int:
    """Dopisuje przyrosty z jednego odpytania helpera. Zwraca liczbe zapisanych wierszy."""
    live = (status or {}).get("peers") or {}
    if not live:
        return 0        # helper nie odpowiedzial albo interfejs lezy - nic nowego nie wiemy
    now = int(time.time() if now is None else now)
    boot = boot_id() if boot is None else boot
    rows = _rows(conn)
    written = 0
    with conn:
        for peer in db.peers(conn):
            stat = live.get(peer["pubkey"])
            if stat is None:
                continue        # np. zawieszony - nie ma go na interfejsie
            rx, tx, handshake = _counters(stat)
            row = rows.get(peer["id"])
            if row is None:
                total_rx, total_tx, since, handshake = _seed(
                    conn, peer["pubkey"], rx, tx, handshake, now,
                    booted_at() if started is None else started)
                conn.execute(
                    """INSERT INTO peer_stats(peer_id, pubkey, boot_id, last_rx, last_tx,
                                              rx_total, tx_total, last_handshake, since)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (peer["id"], peer["pubkey"], boot, rx, tx, total_rx, total_tx,
                     handshake, since))
                written += 1
                continue
            d_rx, d_tx = _delta(row, peer["pubkey"], boot, rx, tx)
            last_handshake = max(row["last_handshake"], handshake)
            if (d_rx or d_tx or last_handshake != row["last_handshake"]
                    or (row["pubkey"], row["boot_id"], row["last_rx"], row["last_tx"])
                    != (peer["pubkey"], boot, rx, tx)):
                conn.execute(
                    """UPDATE peer_stats SET pubkey = ?, boot_id = ?, last_rx = ?, last_tx = ?,
                              rx_total = rx_total + ?, tx_total = tx_total + ?,
                              last_handshake = ?
                       WHERE peer_id = ?""",
                    (peer["pubkey"], boot, rx, tx, d_rx, d_tx, last_handshake, peer["id"]))
                written += 1
    return written


def _seed(conn: sqlite3.Connection, pubkey: str, rx: int, tx: int, handshake: int, now: int,
          started: int) -> tuple:
    """Pierwszy wpis osoby: ruch z probek wykresu plus to, co jadro naliczylo po ostatniej
    probce; handshake - ostatni widziany przez alerty (peer_seen), jesli pozniejszy."""
    hist_rx, hist_tx, last, first = _history(conn, pubkey)
    seen = conn.execute("SELECT last_handshake FROM peer_seen WHERE pubkey = ?",
                        (pubkey,)).fetchone()
    handshake = max(handshake, seen[0] if seen else 0)
    if last is None:
        return rx, tx, now, handshake
    ts, last_rx, last_tx = last
    reset = ts < started or rx < last_rx or tx < last_tx   # probka sprzed restartu Pi
    d_rx, d_tx = (rx, tx) if reset else (rx - last_rx, tx - last_tx)
    return hist_rx + d_rx, hist_tx + d_tx, first, handshake


def view(conn: sqlite3.Connection, status: dict | None, boot: str | None = None) -> dict:
    """peer_id -> {"handshake", "rx", "tx", "since"}: zapamietane plus to, co jadro
    naliczylo od ostatniego zapisu. Bez zapisu do bazy (strony i wgctl tylko czytaja)."""
    live = (status or {}).get("peers") or {}
    boot = boot_id() if boot is None else boot
    rows = _rows(conn)
    out = {}
    for peer in db.peers(conn):
        stat = live.get(peer["pubkey"])
        rx, tx, handshake = _counters(stat or {})
        row = rows.get(peer["id"])
        if row is None:
            out[peer["id"]] = {"handshake": handshake, "rx": rx, "tx": tx, "since": 0}
            continue
        d_rx, d_tx = _delta(row, peer["pubkey"], boot, rx, tx) if stat else (0, 0)
        out[peer["id"]] = {"handshake": max(row["last_handshake"], handshake),
                           "rx": row["rx_total"] + d_rx, "tx": row["tx_total"] + d_tx,
                           "since": row["since"]}
    return out


def last_handshakes(conn: sqlite3.Connection, status: dict | None) -> dict:
    """pubkey -> ostatni znany handshake (do alertu o ciszy)."""
    seen = view(conn, status)
    return {peer["pubkey"]: seen[peer["id"]]["handshake"] for peer in db.peers(conn)}


def mark_dead(conn: sqlite3.Connection, peer, handshake: int) -> bool:
    """Alert o ciszy raz na kazda przerwe: True tylko za pierwszym razem dla tego handshake'u.
    Wczesniej alert wracal co "odstep powtorek" (30 min) az do ponownego polaczenia."""
    with conn:
        changed = conn.execute(
            "UPDATE peer_stats SET dead_alerted = ? WHERE peer_id = ? AND dead_alerted != ?",
            (handshake, peer["id"], handshake)).rowcount
        if changed:
            return True
        if conn.execute("SELECT 1 FROM peer_stats WHERE peer_id = ?", (peer["id"],)).fetchone():
            return False
        conn.execute(
            """INSERT INTO peer_stats(peer_id, pubkey, last_handshake, since, dead_alerted)
               VALUES (?, ?, ?, ?, ?)""",
            (peer["id"], peer["pubkey"], handshake, int(time.time()), handshake))
        return True
