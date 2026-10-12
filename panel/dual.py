"""Ten sam plik konfiguracyjny na dwoch urzadzeniach naraz.

WireGuard nie zna pojecia "jedna sesja na klucz": serwer odpowiada temu urzadzeniu, ktore
odezwalo sie ostatnio, a handshake jednego uniewaznia sesje drugiego. Dwa urzadzenia z tym
samym plikiem wyrzucaja sie wiec nawzajem co minute-dwie (np. komputer w domu zostal
z wlaczonym tunelem, a ta sama osoba laczy sie tym samym plikiem z pracy).

Zablokowac sie tego nie da (zmiana adresu to dla WireGuarda normalny roaming), ale da sie
zauwazyc. Panel co kilka sekund patrzy na `wg show dump` i liczy dwa rodzaje zdarzen:

  powrot            adres peera (IP:port) wraca do wartosci sprzed chwili: A -> B -> A.
                    Zmiana sieci to A -> B i koniec; ponowne wlaczenie tunelu daje nowy port.
  szybki handshake  dwa handshake'i w odstepie ponizej 90 s. Jedno urzadzenie odnawia klucze
                    co 2 minuty; czesciej - gdy drugie co chwile uniewaznia mu sesje.

Alarm: w ciagu 10 minut co najmniej 3 zdarzenia, w tym co najmniej 2 powroty (sam handshake
to za malo - tunel wlaczany i wylaczany kilka razy z rzedu tez daje szybkie handshake'i).
Koniec: 10 minut bez zdarzen - wtedy wpis "ustalo".

Historia probek jest tylko w pamieci; w bazie (tabela dual_use) trzymamy trwajace incydenty,
zeby karta peera i lista mogly je pokazac i zeby restart panelu ich nie gubil.
"""
from __future__ import annotations

import json
import sqlite3
import time

from . import db, i18n, notify

KIND = "two_devices"
SETTING = "alert_two_devices"
WINDOW = 600            # okno liczenia zdarzen [s]
FAST_HANDSHAKE = 90     # jedno urzadzenie odnawia klucze co ~120 s
MIN_EVENTS = 3
MIN_RETURNS = 2
CALM = 600              # tyle bez zdarzen = przeskakiwanie ustalo
MAX_SHOWN = 3           # ile adresow pokazac w alercie


def enabled(cfg: dict) -> bool:
    return cfg.get(SETTING, "1") == "1"


def incidents(conn: sqlite3.Connection) -> dict:
    """Trwajace incydenty: peer_id -> {"since": ..., "endpoints": [...]}."""
    found = {}
    for row in conn.execute("SELECT peer_id, since, endpoints FROM dual_use"):
        try:
            endpoints = json.loads(row["endpoints"] or "[]")
        except ValueError:
            endpoints = []
        found[row["peer_id"]] = {"since": row["since"], "endpoints": endpoints}
    return found


def _join(items: list) -> str:
    items = list(items)
    if len(items) < 2:
        return "".join(items)
    return ", ".join(items[:-1]) + " i " + items[-1]


class Watch:
    """Pamiec probek. Jeden obiekt na proces panelu (watek harmonogramu)."""

    def __init__(self) -> None:
        self.memory: dict = {}        # pubkey -> {"endpoint", "handshake", "seen", "events"}
        self.last_event: dict = {}    # peer_id -> czas ostatniego zdarzenia w trwajacym incydencie
        self.suspects: set = set()    # pubkeye z co najmniej jednym powrotem w oknie

    def _track(self, pubkey: str, endpoint: str, handshake: int, now: int) -> dict:
        mem = self.memory.setdefault(
            pubkey, {"endpoint": "", "handshake": 0, "seen": {}, "events": []})
        if endpoint:
            last = mem["endpoint"]
            if last and endpoint != last:
                seen_at = mem["seen"].get(endpoint)
                if seen_at is not None and now - seen_at <= WINDOW:
                    mem["events"].append((now, "return"))
            mem["seen"][endpoint] = now
            mem["endpoint"] = endpoint
        if handshake:
            previous = mem["handshake"]
            # czasy handshake'ow podaje WireGuard - odstep jest dokladny niezaleznie od probek;
            # przegapiony handshake tylko wydluza widziany odstep, wiec nie da falszywego zdarzenia
            if previous and handshake > previous and handshake - previous < FAST_HANDSHAKE:
                mem["events"].append((now, "handshake"))
            mem["handshake"] = max(previous, handshake)
        cutoff = now - WINDOW
        mem["events"] = [event for event in mem["events"] if event[0] > cutoff]
        mem["seen"] = {ep: ts for ep, ts in mem["seen"].items() if ts > cutoff}
        return mem

    @staticmethod
    def _endpoints(mem: dict) -> list:
        """Adresy z okna, najswiezsze najpierw."""
        ordered = sorted(mem["seen"].items(), key=lambda item: item[1], reverse=True)
        return [endpoint for endpoint, _ts in ordered[:MAX_SHOWN]]

    def observe(self, conn: sqlite3.Connection, live: dict, now: float | None = None) -> list:
        """Jedna probka: {pubkey: {"endpoint", "latest_handshake"}}. Zwraca liste zdarzen
        (do testow i dziennika): ("start"|"end", peer_id)."""
        now = int(time.time() if now is None else now)
        cfg = db.settings(conn)
        open_now = incidents(conn)
        if not enabled(cfg):
            if open_now:
                with conn:
                    conn.execute("DELETE FROM dual_use")
            self.memory.clear()
            self.last_event.clear()
            self.suspects = set()
            return []

        changes = []
        active = {p["pubkey"]: p for p in db.peers(conn) if p["state"] == "active"}
        suspects = set()
        for pubkey, stat in (live or {}).items():
            peer = active.get(pubkey)
            if peer is None:
                continue
            endpoint = (stat.get("endpoint") or "").strip()
            if endpoint == "(none)":
                endpoint = ""
            mem = self._track(pubkey, endpoint, int(stat.get("latest_handshake") or 0), now)
            events = mem["events"]
            returns = sum(1 for _ts, kind in events if kind == "return")
            if returns:
                suspects.add(pubkey)
            peer_id = peer["id"]

            if peer_id in open_now:
                suspects.add(pubkey)
                if events:
                    self.last_event[peer_id] = max(self.last_event.get(peer_id, 0), events[-1][0])
                shown = self._endpoints(mem)
                if len(shown) >= 2 and shown != open_now[peer_id]["endpoints"]:
                    with conn:
                        conn.execute("UPDATE dual_use SET endpoints = ? WHERE peer_id = ?",
                                     (json.dumps(shown), peer_id))
                continue

            if len(events) >= MIN_EVENTS and returns >= MIN_RETURNS:
                shown = self._endpoints(mem)
                with conn:
                    conn.execute(
                        "INSERT OR REPLACE INTO dual_use(peer_id, since, endpoints) VALUES (?, ?, ?)",
                        (peer_id, now, json.dumps(shown)))
                self.last_event[peer_id] = now
                suspects.add(pubkey)
                changes.append(("start", peer_id))
                notify.raise_alert(
                    conn, KIND, f'{peer["name"]}: tunel na dwóch urządzeniach',
                    i18n.pl(i18n.ALERTS["two_devices"], name=peer["name"], ip=peer["ip"],
                            where=_join(shown)),
                    severity="alarm",
                )

        for pubkey in list(self.memory):
            if pubkey not in live or pubkey not in active:
                del self.memory[pubkey]
        self.suspects = suspects

        for peer_id, incident in open_now.items():
            # po restarcie panelu nie znamy ostatniego zdarzenia - liczymy spokoj od teraz
            last = self.last_event.setdefault(peer_id, now)
            if now - last < CALM:
                continue
            with conn:
                conn.execute("DELETE FROM dual_use WHERE peer_id = ?", (peer_id,))
            self.last_event.pop(peer_id, None)
            peer = db.peer(conn, peer_id)
            if peer is None:
                continue
            changes.append(("end", peer_id))
            minutes = max(1, (last - incident["since"]) // 60)
            notify.raise_alert(
                conn, KIND, f'{peer["name"]}: przeskakiwanie ustało',
                i18n.pl(i18n.ALERTS["calm"], name=peer["name"], ip=peer["ip"], n=CALM // 60,
                        m=minutes),
                severity="info",
            )
        return changes
