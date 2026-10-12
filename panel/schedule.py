"""Harmonogram dostępu: wygasanie, okna dostępu, licznik od pierwszego połączenia.

Wspólny dla panelu (wątek harmonogramu) i `wgctl expire` (timer systemd co minutę) -
dzięki temu osoba po terminie traci dostęp także wtedy, gdy panel nie działa.

Zmiana trafia na interfejs PUNKTOWO: bierzemy to, co naprawdę działa (ostatni zastosowany
stan), i zmieniamy wyłącznie stan osób, którym minął albo zaczął się termin. Niezastosowane
zmiany administratora zostają niezastosowane - wcześniej wygaśnięcie wypychało na interfejs
cały bieżący stan panelu, bez bezpiecznika, i oznaczało go jako zastosowany.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import sqlite3
import time

from . import db, export, helperclient, i18n, model, notify


def update_states(conn: sqlite3.Connection, now: int | None = None) -> list:
    """Zapis w bazie: zamyka dostęp po terminie i otwiera okna, które się zaczęły."""
    now = int(now or time.time())
    cfg = db.settings(conn)
    changed = []
    expired = list(conn.execute(
        """SELECT id, name, ip FROM peers
           WHERE state = 'active' AND expires_at IS NOT NULL AND expires_at <= ?
                 AND access_mode != 'none'""", (now,)))
    for peer in expired:
        with conn:
            conn.execute("UPDATE peers SET state='suspended', auto_state=1 WHERE id=?", (peer["id"],))
        db.log(conn, "harmonogram", "peer.expired", f'{peer["name"]} {peer["ip"]}')
        changed.append(peer["name"])
        if cfg.get("alert_expiry", "1") == "1":
            notify.raise_alert(
                conn, "expired", peer["name"],
                i18n.pl(i18n.ALERTS["expired"], name=peer["name"], ip=peer["ip"]),
                severity="info")
    # start okna: wlaczamy tylko to, co sami wczesniej wylaczylismy albo co zostalo
    # zalozone z data startu w przyszlosci
    starting = list(conn.execute(
        """SELECT id, name, ip FROM peers
           WHERE state = 'suspended' AND access_mode = 'window'
                 AND starts_at IS NOT NULL AND starts_at <= ?
                 AND (expires_at IS NULL OR expires_at > ?)
                 AND auto_state = 1""", (now, now)))
    for peer in starting:
        with conn:
            conn.execute("UPDATE peers SET state='active', auto_state=0 WHERE id=?", (peer["id"],))
        db.log(conn, "harmonogram", "peer.window_open", f'{peer["name"]} {peer["ip"]}')
        changed.append(peer["name"])
    return changed


def activate_first_use(conn: sqlite3.Connection, status: dict | None) -> None:
    """Peery liczone od pierwszego połączenia dostają datę dopiero po użyciu."""
    waiting = list(conn.execute(
        """SELECT id, name, ip, pubkey, expires_days FROM peers
           WHERE expires_from_use = 1 AND first_seen IS NULL
                 AND expires_days IS NOT NULL AND state = 'active'"""))
    live = (status or {}).get("peers", {})
    for peer in waiting:
        handshake = int((live.get(peer["pubkey"]) or {}).get("latest_handshake") or 0)
        if not handshake:
            continue
        local = time.localtime(handshake + peer["expires_days"] * 86400)
        expires_at = int(time.mktime((local.tm_year, local.tm_mon, local.tm_mday,
                                      23, 59, 59, 0, 0, -1)))
        with conn:
            conn.execute("UPDATE peers SET first_seen = ?, expires_at = ? WHERE id = ?",
                         (handshake, expires_at, peer["id"]))
        stamp = time.strftime("%Y-%m-%d", time.localtime(expires_at))
        db.log(conn, "harmonogram", "peer.activated",
               f'{peer["name"]}: pierwsze polaczenie, dostep do {stamp}')


def scheduled_state(peer, now: int) -> str | None:
    """Stan wynikający z terminów (albo None, gdy terminy o niczym nie przesądzają)."""
    if peer["access_mode"] != "none" and peer["expires_at"] and peer["expires_at"] <= now:
        return "suspended"
    if (peer["access_mode"] == "window" and peer["state"] == "active"
            and (not peer["starts_at"] or peer["starts_at"] <= now)
            and (not peer["expires_at"] or peer["expires_at"] > now)):
        return "active"
    return None


def plan(conn: sqlite3.Connection, now: int | None = None):
    """Ostatni zastosowany stan z poprawionymi terminami: (stan, model, baza_tymczasowa, nazwy),
    albo None, gdy na interfejsie wszystko zgadza się z terminami."""
    now = int(now or time.time())
    raw = db.settings(conn).get("applied_state")
    if not raw:
        return None          # nic jeszcze nie zastosowano - nie ma czego zmieniac punktowo
    snapshot = json.loads(raw)
    current = {row["pubkey"]: row for row in conn.execute("SELECT * FROM peers")}
    names = []
    for row in snapshot.get("peers", []):
        live = current.get(row.get("pubkey"))
        want = scheduled_state(live, now) if live is not None else None
        if want and row.get("state") != want:
            row["state"] = want
            names.append(row.get("name"))
    if not names:
        return None
    temp = db.connect(":memory:")
    export.restore(temp, snapshot)
    return snapshot, model.build(temp), temp, names


def enforce(conn: sqlite3.Connection, call=helperclient.call, now: int | None = None) -> dict:
    """Wypycha na interfejs wyłącznie zmiany wynikające z terminów."""
    planned = plan(conn, now)
    if planned is None:
        return {"applied": False, "names": []}
    snapshot, value, temp, names = planned
    try:
        result = call("apply", dict(value, confirm_seconds=0), timeout=120)
    except helperclient.HelperError as exc:
        if "czekajaca na potwierdzenie" in str(exc):
            # zmiana administratora czeka na potwierdzenie - nie mieszamy sie, za minute znowu
            return {"applied": False, "waiting": True, "names": names}
        raise
    # dziala teraz: ostatni zastosowany stan + terminy. Zmiany administratora dalej czekaja.
    db.set_setting(conn, "applied_state", json.dumps(snapshot, ensure_ascii=False))
    db.set_setting(conn, "applied_hash", model.render_hash(temp))
    if result.get("fingerprint"):
        db.set_setting(conn, "applied_fingerprint", json.dumps(result["fingerprint"]))
    db.log(conn, "harmonogram", "apply.auto", f"zmiana okien dostepu: {', '.join(names)}")
    return {"applied": True, "names": names}


@contextlib.contextmanager
def _exclusive(conn: sqlite3.Connection):
    """Panel i timer nie pracują naraz - drugi po prostu pomija tę minutę."""
    path = next((row[2] for row in conn.execute("PRAGMA database_list") if row[1] == "main"), "")
    if not path:
        yield True
        return
    # tylko do odczytu: flock nie potrzebuje zapisu, a plik mogl zalozyc ktos inny
    # (straznik jako wg-panel, a wgctl uruchamia czlonek grupy - np. pi)
    try:
        handle = os.open(path + ".schedule.lock", os.O_RDONLY | os.O_CREAT, 0o660)
    except OSError:
        yield True           # bez blokady - ponowne przejscie i tak niczego nie psuje
        return
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(handle)
        yield False
        return
    try:
        yield True
    finally:
        fcntl.flock(handle, fcntl.LOCK_UN)
        os.close(handle)


def run(conn: sqlite3.Connection, call=helperclient.call, status: dict | None = None) -> dict:
    """Jedno przejście harmonogramu: zapis w bazie, potem punktowe wypchnięcie na interfejs."""
    with _exclusive(conn) as mine:
        if not mine:
            return {"skipped": True, "changed": []}
        changed = update_states(conn)
        if status:
            activate_first_use(conn, status)
        result = {"changed": changed}
        if db.settings(conn).get("expiry_auto_apply", "1") == "1":
            try:
                result.update(enforce(conn, call))
            except Exception as exc:  # noqa: BLE001
                result["error"] = str(exc)
                notify.raise_alert(
                    conn, "apply", "automatyczne zastosowanie",
                    i18n.pl(i18n.ALERTS["apply_failed"], error=exc),
                    severity="alarm")
        return result

