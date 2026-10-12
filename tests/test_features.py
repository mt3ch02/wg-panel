#!/usr/bin/env python3
"""Testy funkcji dodanych po pierwszej wersji panelu."""
from __future__ import annotations

import email
import email.policy
import http.server
import json
import os
import socketserver
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from panel import cryptobox, db, export, notify, simulate  # noqa: E402
import testnet  # noqa: E402

testnet.use(db)

TMP = tempfile.mkdtemp(prefix="wgpanel-feat-")
FAILURES = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  [{'OK  ' if condition else 'BLAD'}] {label}"
          + (f"  ({detail})" if detail and not condition else ""))
    if not condition:
        FAILURES.append(label)


def fresh_db(name="panel.db"):
    path = os.path.join(TMP, name)
    if os.path.exists(path):
        os.remove(path)
    conn = db.connect(path)
    db.init_defaults(conn)
    return conn, path


def add_peer(conn, name, ip, tier_name="NAS", state="active", expires=None, key="A",
             mode="none", starts=None, days=None, from_use=0, auto=0, first_seen=None):
    tier = conn.execute("SELECT id FROM tiers WHERE name = ?", (tier_name,)).fetchone()
    if expires and mode == "none":
        mode = "window"
    with conn:
        conn.execute(
            """INSERT INTO peers(name, device, ip, pubkey, psk, privkey, tier_id,
                                 state, note, created_at, access_mode, starts_at,
                                 expires_at, expires_days, expires_from_use,
                                 first_seen, auto_state)
               VALUES (?, '', ?, ?, ?, 'priv', ?, ?, '', ?, ?, ?, ?, ?, ?, ?, ?)""",
            (name, ip, key * 43 + "=", "B" * 43 + "=", tier["id"] if tier else None,
             state, int(time.time()), mode, starts, expires, days, from_use,
             first_seen, auto),
        )
    return conn.execute("SELECT id FROM peers WHERE ip = ?", (ip,)).fetchone()["id"]


print("\n1. Symulator regul")
conn, _ = fresh_db()
add_peer(conn, "Admin", "10.0.0.2", "ADMIN", key="A")
add_peer(conn, "Kasia", "10.0.0.3", "NET", key="B")
marek = add_peer(conn, "Marek", "10.0.0.4", "NAS", key="C")
with conn:
    conn.execute(
        """INSERT INTO rules(peer_id, position, action, dest, proto, ports,
                             time_from, time_to, weekdays, comment)
           VALUES (?, 1, 'ACCEPT', '192.168.50.80', 'tcp', '8123',
                   '08:00', '18:00', 'Mon,Tue,Wed,Thu,Fri', 'HA w pracy')""",
        (marek,))

cfg = db.render_cfg(conn)
peers = []
for row in db.peers(conn):
    item = dict(row)
    item["custom_rules"] = [dict(r) for r in db.rules_for(conn, row["id"])]
    peers.append(item)

monday_noon = time.mktime(time.strptime("2026-03-16 12:00", "%Y-%m-%d %H:%M"))
monday_night = time.mktime(time.strptime("2026-03-16 23:00", "%Y-%m-%d %H:%M"))
sunday_noon = time.mktime(time.strptime("2026-03-15 12:00", "%Y-%m-%d %H:%M"))

cases = [
    ("admin ma pelny dostep do LAN", "10.0.0.2", "192.168.50.99", 22, "tcp",
     monday_noon, "ACCEPT"),
    ("NAS przechodzi dla tieru NAS", "10.0.0.4", "192.168.50.11", 445, "tcp",
     monday_noon, "ACCEPT"),
    ("inny port NAS-a juz nie", "10.0.0.4", "192.168.50.11", 22, "tcp",
     monday_noon, "DROP"),
    ("inny host w LAN zablokowany", "10.0.0.4", "192.168.50.99", 80, "tcp",
     monday_noon, "DROP"),
    ("NET nie wchodzi do LAN", "10.0.0.3", "192.168.50.11", 445, "tcp",
     monday_noon, "DROP"),
    ("peery sie nie widza", "10.0.0.4", "10.0.0.2", 22, "tcp", monday_noon, "DROP"),
    ("okno czasowe: poniedzialek poludnie", "10.0.0.4", "192.168.50.80", 8123, "tcp",
     monday_noon, "ACCEPT"),
    ("okno czasowe: poniedzialek noc", "10.0.0.4", "192.168.50.80", 8123, "tcp",
     monday_night, "DROP"),
    ("okno czasowe: niedziela", "10.0.0.4", "192.168.50.80", 8123, "tcp",
     sunday_noon, "DROP"),
    ("SSH na serwer: admin tak", "10.0.0.2", "10.0.0.1", 22, "tcp",
     monday_noon, "ACCEPT"),
    ("SSH na serwer: NAS nie", "10.0.0.4", "10.0.0.1", 22, "tcp", monday_noon, "DROP"),
]
for label, src, dst, port, proto, when, expected in cases:
    result = simulate.simulate(cfg, peers, src, dst, port, proto, when)
    check(label, result["verdict"] == expected,
          f'{result["verdict"]} zamiast {expected}: {result["reason"]}')

result = simulate.simulate(cfg, peers, "10.0.0.4", "192.168.50.11", 445, "tcp", monday_noon)
check("symulator pokazuje przejscie lancucha", len(result["trace"]) > 1)
check("symulator wskazuje pozycje reguly", result.get("position", 0) > 1)

add_peer(conn, "Zawieszony", "10.0.0.9", "NAS", state="suspended", key="D")
peers_s = peers + [dict(conn.execute("SELECT * FROM peers WHERE ip='10.0.0.9'").fetchone(),
                        custom_rules=[])]
result = simulate.simulate(cfg, peers_s, "10.0.0.9", "192.168.50.11", 445)
check("zawieszony peer nie przechodzi", result["verdict"] == "DROP", result["reason"])

print("\n2. Wygasanie dostepu")
conn2, path2 = fresh_db("expiry.db")
add_peer(conn2, "Gosc", "10.0.0.5", "NAS", expires=int(time.time()) - 60, key="E")
add_peer(conn2, "Staly", "10.0.0.6", "NAS", expires=int(time.time()) + 86400, key="F")
add_peer(conn2, "Bezterminowy", "10.0.0.7", "NAS", key="G")

from panel import schedule, scheduler  # noqa: E402
from panel import model as _model  # noqa: E402

applied = {"count": 0}


def fake_call(op, payload=None, timeout=None):
    """Atrapa helpera: liczy zastosowania wyslane przez harmonogram."""
    if op == "apply":
        applied["count"] += 1
    return {"steps": ["test"]}


# stan wyjsciowy jak po zastosowaniu w panelu - harmonogram zmienia go punktowo
with conn2:
    conn2.execute("UPDATE peers SET expires_at = ? WHERE name = 'Gosc'", (int(time.time()) + 3600,))
_model.mark_applied(conn2, _model.build(conn2), {})
with conn2:
    conn2.execute("UPDATE peers SET expires_at = ? WHERE name = 'Gosc'", (int(time.time()) - 60,))
schedule.run(conn2, call=fake_call)
states = {r["name"]: r["state"] for r in conn2.execute("SELECT name, state FROM peers")}
check("wygasly peer zawieszony", states["Gosc"] == "suspended", states["Gosc"])
check("peer z data w przyszlosci nietkniety", states["Staly"] == "active")
check("peer bezterminowy nietkniety", states["Bezterminowy"] == "active")
check("wygasniecie wyzwolilo zastosowanie zmian", applied["count"] == 1)
schedule.run(conn2, call=fake_call)
check("drugi przebieg nie stosuje zmian bez potrzeby", applied["count"] == 1)
log = [r["action"] for r in db.audit_tail(conn2, 10)]
check("wygasniecie trafia do dziennika", "peer.expired" in log, str(log))

db.set_setting(conn2, "expiry_auto_apply", "0")
add_peer(conn2, "Gosc2", "10.0.0.8", "NAS", expires=int(time.time()) - 60, key="H")
schedule.run(conn2, call=fake_call)
check("przy wylaczonym auto-apply tylko zawieszenie", applied["count"] == 1)

print("\n2b. Okna dostepu")
conn2b, _ = fresh_db("windows.db")
now = int(time.time())
add_peer(conn2b, "Ekipa", "10.0.0.20", starts=now + 3600, expires=now + 86400 * 30,
         mode="window", state="suspended", auto=1, key="I")
add_peer(conn2b, "Trwajace", "10.0.0.21", starts=now - 3600, expires=now + 86400,
         mode="window", state="suspended", auto=1, key="J")
add_peer(conn2b, "Reczne", "10.0.0.22", starts=now - 3600, expires=now + 86400,
         mode="window", state="suspended", auto=0, key="K")
add_peer(conn2b, "Gosc", "10.0.0.23", mode="from_use", days=7, from_use=1, key="L")

applied2 = {"n": 0}


def fake_call2(op, payload=None, timeout=None):
    if op == "apply":
        applied2["n"] += 1
    return {}


_model.mark_applied(conn2b, _model.build(conn2b), {})
worker2 = scheduler.Scheduler(conn2b, lambda: ({}, ""), log=lambda m: None)
schedule.run(conn2b, call=fake_call2)
states = {r["name"]: r["state"] for r in conn2b.execute("SELECT name, state FROM peers")}
check("okno jeszcze nieotwarte zostaje zawieszone", states["Ekipa"] == "suspended")
check("okno trwajace otwiera sie samo", states["Trwajace"] == "active", states["Trwajace"])
check("recznie zawieszony nie jest wznawiany", states["Reczne"] == "suspended",
      states["Reczne"])
check("otwarcie okna wyzwala zastosowanie zmian", applied2["n"] == 1)

pubkey_gosc = "L" * 43 + "="
worker2.activate_pending({"peers": {pubkey_gosc: {"latest_handshake": 0}}})
row = conn2b.execute("SELECT * FROM peers WHERE name='Gosc'").fetchone()
check("bez polaczenia licznik nie rusza", row["first_seen"] is None)
check("opis mowi, ze peer nieuzyty",
      "nieużyty" in db.describe_expiry(row), db.describe_expiry(row))

worker2.activate_pending({"peers": {pubkey_gosc: {"latest_handshake": now}}})
row = conn2b.execute("SELECT * FROM peers WHERE name='Gosc'").fetchone()
check("pierwsze polaczenie uruchamia odliczanie", row["first_seen"] == now)
check("termin konca to pierwsze polaczenie + N dni",
      row["expires_at"] and 6 * 86400 < row["expires_at"] - now <= 8 * 86400,
      str(row["expires_at"] - now if row["expires_at"] else None))

worker2.activate_pending({"peers": {pubkey_gosc: {"latest_handshake": now + 100}}})
row2 = conn2b.execute("SELECT * FROM peers WHERE name='Gosc'").fetchone()
check("kolejne polaczenia nie przesuwaja terminu", row2["first_seen"] == now)

add_peer(conn2b, "Minelo", "10.0.0.24", mode="from_use", days=7, from_use=1,
         first_seen=now - 86400 * 10, expires=now - 86400 * 3, key="M")
worker2.check_expiry()
state = conn2b.execute("SELECT state FROM peers WHERE name='Minelo'").fetchone()["state"]
check("po uplywie N dni peer jest zawieszany", state == "suspended")

add_peer(conn2b, "Bezterminowy", "10.0.0.25", mode="none", key="N")
worker2.check_expiry()
state = conn2b.execute("SELECT state FROM peers WHERE name='Bezterminowy'").fetchone()["state"]
check("bezterminowy nigdy nie wygasa", state == "active")

print("\n2c. Opisy okien")
mk = lambda **kw: dict({"access_mode": "none", "starts_at": None, "expires_at": None,
                        "expires_days": None, "first_seen": None}, **kw)
check("opis: bezterminowo", db.describe_expiry(mk()) == "bezterminowo")
check("opis: okno w przyszlosci",
      db.describe_expiry(mk(access_mode="window", starts_at=now + 86400 * 3,
                            expires_at=now + 86400 * 10), now).startswith("start"))
check("opis: minelo",
      "minęło" in db.describe_expiry(mk(access_mode="window",
                                        expires_at=now - 86400), now))


print("\n2d. Sygnal o niezastosowanych zmianach")
from panel import model as _model  # noqa: E402
conn2d, _ = fresh_db("dirty.db")
add_peer(conn2d, "Ja", "10.0.0.2", "ADMIN", key="Q")
add_peer(conn2d, "Marek", "10.0.0.4", "NAS", key="R")
_model.mark_applied(conn2d, _model.build(conn2d), {})
check("po zastosowaniu nie ma sygnalu zmian", not _model.is_dirty(conn2d))
with conn2d:
    conn2d.execute("UPDATE peers SET note = 'nowa notatka' WHERE ip = '10.0.0.2'")
check("zmiana notatki nie udaje zmiany konfiguracji", not _model.is_dirty(conn2d))
with conn2d:
    conn2d.execute("UPDATE peers SET state = 'suspended' WHERE ip = '10.0.0.4'")
check("zawieszenie peera to realna zmiana", _model.is_dirty(conn2d))
with conn2d:
    conn2d.execute("UPDATE peers SET state = 'active' WHERE ip = '10.0.0.4'")
check("przywrocenie peera gasi sygnal zmian", not _model.is_dirty(conn2d))
with conn2d:
    conn2d.execute("UPDATE peers SET name = 'Marek K.' WHERE ip = '10.0.0.4'")
check("zmiana nazwy to zmiana - idzie do komentarza w wg0.conf", _model.is_dirty(conn2d))


print("\n2e. Alarmy o ingerencji a niezastosowane zmiany")
conn2e, _ = fresh_db("integrity.db")
worker2e = scheduler.Scheduler(conn2e, lambda: ({}, ""), log=lambda m: None)
worker2e.drift_fn = lambda: [{"kind": "conf", "message": "plik zmieniony poza panelem",
                              "severity": "alarm"}]
worker2e.dirty_fn = lambda: True
worker2e.last_integrity = 0
worker2e.check_integrity()
check("przy niezastosowanych zmianach panel nie zasypuje alarmami",
      len(db.alerts_tail(conn2e)) == 0)
check("roznice sa mimo to zapamietane",
      db.settings(conn2e).get("drift_findings", "") != "")
worker2e.dirty_fn = lambda: False
worker2e.last_integrity = 0
worker2e.check_integrity()
check("po zastosowaniu alarmy wracaja", len(db.alerts_tail(conn2e)) == 1)


print("\n2f. Migawka stanu nie moze puchnac")
conn2f, _ = fresh_db("snapshot.db")
for index, letter in enumerate("QRSTUV"):
    add_peer(conn2f, f"U{index}", f"10.0.0.{index + 2}", key=letter)
rozmiary = []
for _ in range(6):
    _model.mark_applied(conn2f, _model.build(conn2f), {})
    rozmiary.append(len(db.settings(conn2f).get("applied_state", "")))
check("migawka ma staly rozmiar (nie zawiera sama siebie)",
      len(set(rozmiary)) == 1 and rozmiary[0] > 0, str(rozmiary))
check("odczyt ustawien zostaje maly",
      sum(len(v) for v in db.settings(conn2f).values()) < 50_000)
check("kopia zapasowa nie zawiera stanu roboczego",
      "applied_state" not in {r["key"] for r in export.build(conn2f)["settings"]})


conn_mode, _ = fresh_db("tlsmode.db")
db.set_setting(conn_mode, "tls_mode", "proxy")
db.normalize_legacy(conn_mode)
check("usuniety tryb 'nginx na Pi' wraca do wlasnego urzedu",
      db.settings(conn_mode)["tls_mode"] == "self")

print("\n3. Alerty")
received = []


class Sink(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf8")
        try:
            data = json.loads(raw)
        except ValueError:
            data = {}
        # ntfy dostaje JSON (tytul z polskimi znakami), webhook - wlasny JSON
        received.append({"path": self.path, "body": data.get("message", raw), "raw": data,
                         "title": data.get("title"), "auth": self.headers.get("Authorization")})
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *a):
        pass


sink = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Sink)
threading.Thread(target=sink.serve_forever, daemon=True).start()
sink_url = f"http://127.0.0.1:{sink.server_address[1]}/alerty"

conn3, _ = fresh_db("alerts.db")
db.set_setting(conn3, "alerts_channel", "ntfy")
db.set_setting(conn3, "alerts_url", sink_url)
db.set_setting(conn3, "alert_dead_hours", "24")
add_peer(conn3, "Marek", "10.0.0.4", "NAS", key="C")
pubkey = "C" * 43 + "="

now = int(time.time())
notify.check_peers(conn3, {"peers": {pubkey: {"endpoint": "203.0.113.10:39887",
                                              "latest_handshake": now}}})
check("pierwsze widzenie nie alarmuje", len(received) == 0, str(received))

notify.check_peers(conn3, {"peers": {pubkey: {"endpoint": "203.0.113.10:41000",
                                              "latest_handshake": now}}})
check("zmiana samego portu nie alarmuje", len(received) == 0)

notify.check_peers(conn3, {"peers": {pubkey: {"endpoint": "185.90.3.12:41000",
                                              "latest_handshake": now}}})
check("zmiana adresu wyzwala alert", len(received) == 1, str(received))
if received:
    check("alert wyslany na kanal", "185.90.3.12" in received[0]["body"])
    check("alert ma tytul", bool(received[0]["title"]))
    check("ntfy: temat wyciagniety z adresu, wysylka na serwer",
          received[0]["path"] == "/" and received[0]["raw"].get("topic") == "alerty", str(received[0]))
    check("ntfy: nowy adres klucza to najwyzszy priorytet",
          received[0]["raw"].get("priority") == 5, str(received[0]["raw"].get("priority")))
    check("ntfy: klikniecie otwiera strone alertow panelu",
          str(received[0]["raw"].get("click", "")).endswith("/alerts"))
received.clear()
notify.send(db.settings(conn3), "Łukasz: dostęp wygasł", "treść ąęłńóśźż", "warn")
check("ntfy: polskie znaki w tytule i tresci dochodza bez zmian",
      received and received[0]["title"] == "Łukasz: dostęp wygasł"
      and received[0]["body"] == "treść ąęłńóśźż", str(received))
db.set_setting(conn3, "alerts_token", "tk_testowy")
received.clear()
notify.send(db.settings(conn3), "t", "m", "warn")
check("ntfy: token dostepu w naglowku", received and received[0]["auth"] == "Bearer tk_testowy",
      str(received))
db.set_setting(conn3, "alerts_token", "")
mails = []


class SmtpSink(socketserver.StreamRequestHandler):
    """Minimalny serwer SMTP - smtpd zniknal z Pythona 3.12."""
    def handle(self):
        self.wfile.write(b"220 test\r\n")
        data, lines = False, []
        while True:
            line = self.rfile.readline()
            if not line:
                break
            if data:
                if line in (b".\r\n", b".\n"):
                    mails.append(b"".join(lines))
                    data, lines = False, []
                    self.wfile.write(b"250 ok\r\n")
                else:
                    lines.append(line)
                continue
            command = line.strip().upper()
            if command.startswith(b"DATA"):
                data = True
                self.wfile.write(b"354 dalej\r\n")
            elif command.startswith(b"QUIT"):
                self.wfile.write(b"221 pa\r\n")
                break
            else:
                self.wfile.write(b"250 ok\r\n")


smtp = socketserver.ThreadingTCPServer(("127.0.0.1", 0), SmtpSink)
threading.Thread(target=smtp.serve_forever, daemon=True).start()
for key, value in (("alerts_email", "1"), ("smtp_host", "127.0.0.1"),
                   ("smtp_port", str(smtp.server_address[1])), ("smtp_security", "none"),
                   ("smtp_to", "ja@example.com"), ("smtp_from", "pi@example.com"),
                   ("smtp_pass", "tajne-haslo")):
    db.set_setting(conn3, key, value)
received.clear()
notify.send(db.settings(conn3), "Łukasz: dostęp wygasł", "treść ąęłńóśźż", "warn")
time.sleep(0.3)
mail = email.message_from_bytes(mails[0], policy=email.policy.default) if mails else None
check("e-mail: dochodzi, temat z polskimi znakami", mail is not None
      and mail["Subject"] == "[wg-panel] Łukasz: dostęp wygasł", str(mail and mail["Subject"]))
check("e-mail: tresc i link do panelu", mail is not None and "treść ąęłńóśźż" in mail.get_content()
      and "/alerts" in mail.get_content())
check("e-mail i ntfy rownolegle", len(received) == 1)
db.set_setting(conn3, "alerts_email", "0")
mails.clear()
notify.send(db.settings(conn3), "t", "m", "warn")
time.sleep(0.2)
check("e-mail wylaczony - nic nie idzie", not mails)
check("haslo do poczty nie trafia do kopii zapasowej", "tajne-haslo" not in json.dumps(export.build(conn3)))
smtp.shutdown()
received.clear()
check("slaby temat na ntfy.sh rozpoznany", notify.weak_topic("https://ntfy.sh/alerty")
      and not notify.weak_topic("https://ntfy.sh/wg-a8f3k2x9q7m1")
      and not notify.weak_topic("https://ntfy.moj-serwer.pl/alerty"))
received.clear()

rows = db.alerts_tail(conn3)
check("alert zapisany w bazie", any(r["kind"] == "new_endpoint" for r in rows))
check("alert oznaczony jako wyslany", rows[0]["sent"] == 1)
check("alert ma wysoki priorytet", rows[0]["severity"] == "alarm")

notify.check_peers(conn3, {"peers": {pubkey: {"endpoint": "77.253.14.9:41000",
                                              "latest_handshake": now}}})
# lista wyczyszczona po testach ntfy - zadnej nowej wiadomosci
check("dlawik nie powtarza alertu", len(received) == 0, str(len(received)))

conn3.execute("DELETE FROM alerts")
notify.check_peers(conn3, {"peers": {pubkey: {"endpoint": "77.253.14.9:41000",
                                              "latest_handshake": now - 40 * 3600}}})
kinds = [r["kind"] for r in db.alerts_tail(conn3)]
check("cisza peera wyzwala alert", "dead_peer" in kinds, str(kinds))

db.set_setting(conn3, "alerts_channel", "none")
before = len(received)
notify.raise_alert(conn3, "test", "x", "tresc")
check("wylaczony kanal nic nie wysyla", len(received) == before)
check("ale alert i tak jest w bazie",
      any(r["kind"] == "test" for r in db.alerts_tail(conn3)))
check("nieprzeczytane sa liczone", db.unseen_alerts(conn3) > 0)
notify.mark_seen(conn3)
check("oznaczanie jako przeczytane dziala", db.unseen_alerts(conn3) == 0)

print("\n4. Eksport i odtwarzanie")
conn4, _ = fresh_db("exp.db")
add_peer(conn4, "Ja", "10.0.0.2", "ADMIN", key="A")
peer_id = add_peer(conn4, "Marek", "10.0.0.4", "NAS", key="C")
with conn4:
    conn4.execute(
        """INSERT INTO rules(peer_id, position, action, dest, proto, ports, comment)
           VALUES (?, 1, 'ACCEPT', '192.168.50.60', 'tcp', '9091', 'transmission')""",
        (peer_id,))
db.set_setting(conn4, "lan", "192.168.50.0/24")
db.set_setting(conn4, "smb_pass", "tajne-haslo-smb")

data = export.build(conn4)
check("eksport zawiera peery", len(data["peers"]) == 2)
check("eksport zawiera reguly", len(data["rules"]) == 1)
check("eksport zawiera tiery", len(data["tiers"]) >= 3)
keys = {row["key"] for row in data["settings"]}
check("hasla nie wychodza w eksporcie", "smb_pass" not in keys)

blob, encrypted = export.serialize(conn4)
check("eksport bez hasla jest jawny", not encrypted and blob.startswith(b"{"))
blob_enc, encrypted = export.serialize(conn4, "haslo-kopii")
check("eksport z haslem jest zaszyfrowany", encrypted and cryptobox.is_encrypted(blob_enc))
check("PSK nie widac w zaszyfrowanym pliku", b"B" * 43 not in blob_enc)

parsed = export.parse(blob_enc, "haslo-kopii")
check("odszyfrowanie kopii dziala", len(parsed["peers"]) == 2)
try:
    export.parse(blob_enc, "zle-haslo")
    check("zle haslo odrzucone", False)
except ValueError:
    check("zle haslo odrzucone", True)
try:
    export.parse(blob_enc)
    check("brak hasla odrzucony", False)
except ValueError:
    check("brak hasla odrzucony", True)

conn5, _ = fresh_db("restore.db")
add_peer(conn5, "Smiec", "10.0.0.50", "NAS", key="Z")
summary = export.restore(conn5, parsed)
names = sorted(r["name"] for r in conn5.execute("SELECT name FROM peers"))
check("odtwarzanie zastepuje stara zawartosc", names == ["Ja", "Marek"], str(names))
check("odtwarzanie przywraca reguly",
      conn5.execute("SELECT COUNT(*) c FROM rules").fetchone()["c"] == 1)
check("odtwarzanie przywraca ustawienia",
      db.settings(conn5)["lan"] == "192.168.50.0/24")
check("odtwarzanie kasuje znacznik zastosowania",
      db.settings(conn5).get("applied_hash", "") == "")
check("podsumowanie odtwarzania", summary["peers"] == 2)

print("\n5. Kopie zapasowe")
conn6, _ = fresh_db("bak.db")
add_peer(conn6, "Ja", "10.0.0.2", "ADMIN", key="A")
backup_dir = os.path.join(TMP, "kopie")
db.set_setting(conn6, "backup_dir", backup_dir)
db.set_setting(conn6, "backup_target", "local")
db.set_setting(conn6, "backup_keep", "3")

record = export.run(conn6, actor="test")
check("kopia lokalna sie udaje", record["ok"] == 1, record["detail"])
check("plik kopii istnieje", os.path.exists(record["path"]))
# 640: czyta ja grupa wg-panel tak jak baze (te same sekrety, baza ma 660) - inaczej kopia
# zrobiona z wgctl przez czlonka grupy bylaby nieczytelna dla panelu i odwrotnie
check("kopia ma prawa 640 (wlasciciel i grupa wg-panel)",
      oct(os.stat(record["path"]).st_mode)[-3:] == "640")
check("kopia trafia do historii", len(db.backups_tail(conn6)) == 1)

db.set_setting(conn6, "backup_passphrase", "sekret")
record = export.run(conn6, actor="test")
with open(record["path"], "rb") as handle:
    check("kopia z haslem jest zaszyfrowana", cryptobox.is_encrypted(handle.read()))
check("nazwa zaszyfrowanej kopii ma .enc", record["path"].endswith(".enc"))

for index in range(4):
    export.run(conn6, actor="test")
files = [f for f in os.listdir(backup_dir)
         if f.startswith("wg-panel-") and not f.endswith(".sha256")]
sidecars = [f for f in os.listdir(backup_dir) if f.endswith(".sha256")]
check("stare kopie sa kasowane wg limitu", len(files) == 3, f"jest {len(files)}")
check("razem z kopiami znikaja ich pliki .sha256", len(sidecars) == 3, f"jest {len(sidecars)}")

db.set_setting(conn6, "backup_target", "smb")
db.set_setting(conn6, "smb_host", "192.168.50.11")
db.set_setting(conn6, "smb_share", "backup")
db.set_setting(conn6, "smb_user", "backup-wg")      # dane sa - sprawdzamy sam brak programu
db.set_setting(conn6, "smb_pass", "tajne")
record = export.run(conn6, actor="test")
check("brak smbclient daje czytelny blad",
      record["ok"] == 0 and "smbclient" in record["detail"], record["detail"])

for bad, label in [
    ({"smb_host": "nas; rm -rf /", "smb_share": "backup"}, "adres z metaznakami"),
    ({"smb_host": "192.168.50.11", "smb_share": "sh are"}, "udzial ze spacja"),
    ({"smb_host": "192.168.50.11", "smb_share": "backup", "smb_path": '../../etc'},
     "wyjscie ze sciezki"),
]:
    settings = dict(db.settings(conn6), **bad)
    try:
        export.upload_smb(settings, "/tmp/x", "wg-panel-test.json")
        check(f"odrzuca: {label}", False)
    except ValueError as exc:
        check(f"odrzuca: {label}", "zly" in str(exc) or "zla" in str(exc), str(exc))

cfg6 = db.settings(conn6)
db.set_setting(conn6, "backup_enabled", "1")
db.set_setting(conn6, "backup_time", "03:30")
cfg6 = db.settings(conn6)
at_time = time.mktime(time.strptime("2026-03-16 03:30", "%Y-%m-%d %H:%M"))
off_time = time.mktime(time.strptime("2026-03-16 03:31", "%Y-%m-%d %H:%M"))
check("harmonogram odpala sie o wyznaczonej minucie", export.due(cfg6, 0, at_time))
check("harmonogram nie odpala sie poza godzina", not export.due(cfg6, 0, off_time))
check("harmonogram nie powtarza w tej samej minucie",
      not export.due(cfg6, at_time - 10, at_time))
db.set_setting(conn6, "backup_enabled", "0")
check("wylaczony harmonogram nie odpala",
      not export.due(db.settings(conn6), 0, at_time))

print("\n6. wgctl")
conn7, path7 = fresh_db("cli.db")
env = dict(os.environ, WGPANEL_DB=path7, PYTHONPATH=ROOT)


def wgctl(*args, expect=0):
    result = subprocess.run([sys.executable, os.path.join(ROOT, "wgctl"), *args],
                            env=env, capture_output=True, text=True, timeout=60)
    if expect is not None and result.returncode != expect:
        print(f"    (rc={result.returncode}) {result.stdout}{result.stderr}")
    return result


out = wgctl("add", "Marek", "--tier", "NAS", "--device", "laptop",
            "--start", "2026-01-01", "--until", "2026-12-31")
check("wgctl add tworzy peera", "dodano" in out.stdout, out.stdout + out.stderr)
row = conn7.execute("SELECT * FROM peers WHERE name='Marek'").fetchone()
check("peer ma adres z puli", row and row["ip"] == "10.0.0.2", str(row and row["ip"]))
check("peer ma okno terminowe",
      row["access_mode"] == "window" and bool(row["expires_at"]) and bool(row["starts_at"]),
      str(row["access_mode"]))
check("peer ma poprawny klucz publiczny", len(row["pubkey"]) == 44)


out = wgctl("add", "Gosc", "--tier", "NAS", "--days", "7")
check("wgctl add --days ustawia tryb od pierwszego uzycia", "dodano" in out.stdout,
      out.stdout + out.stderr)
row = conn7.execute("SELECT * FROM peers WHERE name='Gosc'").fetchone()
check("tryb from_use zapisany", row["access_mode"] == "from_use" and row["expires_days"] == 7)
check("termin nieustalony do pierwszego uzycia", row["expires_at"] is None)
check("wgctl pokazuje opis dostepu", "pierwszego połączenia" in out.stdout, out.stdout)

out = wgctl("add", "Zly", "--tier", "NAS", "--days", "7", "--start", "2026-01-01", expect=1)
check("wgctl odrzuca laczenie --days z oknem", "nie laczy" in out.stderr, out.stderr)

out = wgctl("add", "Odwrotnie", "--tier", "NAS", "--start", "2026-12-31",
            "--until", "2026-01-01", expect=1)
check("wgctl odrzuca odwrocone daty", "pozniej" in out.stderr, out.stderr)

out = wgctl("add", "Przyszly", "--tier", "NAS", "--start", "2030-01-01")
row = conn7.execute("SELECT * FROM peers WHERE name='Przyszly'").fetchone()
check("peer z data startu w przyszlosci czeka zawieszony",
      row["state"] == "suspended" and row["auto_state"] == 1, str(row["state"]))

out = wgctl("list")
check("wgctl list pokazuje peera", "Marek" in out.stdout and "10.0.0.2" in out.stdout)

out = wgctl("add", "Kasia", "--tier", "NIEMA", expect=1)
check("wgctl odrzuca nieznany tier", "nie ma tieru" in out.stderr, out.stderr)

out = wgctl("rule", "add", "Marek", "--accept", "192.168.50.60",
            "--proto", "tcp", "--port", "9091", "--comment", "transmission")
check("wgctl rule add dziala", "dodano" in out.stdout, out.stdout + out.stderr)
out = wgctl("rule", "list", "Marek")
check("wgctl rule list pokazuje regule", "192.168.50.60" in out.stdout)

out = wgctl("rule", "add", "Marek", "--accept", "nie-adres", expect=1)
check("wgctl waliduje regule", "adres" in out.stderr, out.stderr)

out = wgctl("check", "Marek", "192.168.50.11", "445")
check("wgctl check: NAS przechodzi", "ACCEPT" in out.stdout, out.stdout)
out = wgctl("check", "Marek", "192.168.50.99", "80", expect=2)
check("wgctl check: obcy host blokowany", "DROP" in out.stdout, out.stdout)
check("wgctl check zwraca kod 2 przy DROP", out.returncode == 2)
out = wgctl("check", "Marek", "192.168.50.11", "445", "-v")
check("wgctl check -v pokazuje lancuch", "blokada" in out.stdout or "->" in out.stdout)

out = wgctl("show", "Marek")
check("wgctl show pokazuje reguly", "transmission" in out.stdout)
check("wgctl show mowi o kluczu prywatnym", "w bazie" in out.stdout)

out = wgctl("config", "Marek")
check("wgctl config zwraca plik klienta", "[Interface]" in out.stdout)
check("plik klienta ma PSK", "PresharedKey" in out.stdout)

old_key = conn7.execute("SELECT pubkey FROM peers WHERE name='Marek'").fetchone()["pubkey"]
wgctl("rotate", "Marek")
new_key = conn7.execute("SELECT pubkey FROM peers WHERE name='Marek'").fetchone()["pubkey"]
check("wgctl rotate zmienia klucz", old_key != new_key)

wgctl("suspend", "Marek")
check("wgctl suspend dziala",
      conn7.execute("SELECT state FROM peers WHERE name='Marek'").fetchone()["state"]
      == "suspended")
wgctl("resume", "Marek")
check("wgctl resume dziala",
      conn7.execute("SELECT state FROM peers WHERE name='Marek'").fetchone()["state"]
      == "active")

out = wgctl("revoke", "Marek", "--yes", expect=None)
check("wgctl revoke usuniete (zostaja suspend / rotate / delete)", out.returncode != 0,
      out.stdout + out.stderr)

export_path = os.path.join(TMP, "z-cli.json.enc")
out = wgctl("export", export_path, "--pass", "abc")
check("wgctl export zapisuje plik", os.path.exists(export_path), out.stdout + out.stderr)
with open(export_path, "rb") as handle:
    check("wgctl export szyfruje gdy podano haslo", cryptobox.is_encrypted(handle.read()))

out = wgctl("status", expect=None)
check("wgctl status obsluguje brak helpera",
      "helper" in (out.stdout + out.stderr).lower(), out.stdout + out.stderr)

log = [r["action"] for r in db.audit_tail(conn7, 30)]
for action in ("peer.create", "rule.add", "peer.rotate_keys"):
    check(f"wgctl pisze do dziennika: {action}", action in log, str(log))

sink.shutdown()

print("\n" + "=" * 60)
if FAILURES:
    print(f"NIEPOWODZENIA ({len(FAILURES)}): " + ", ".join(FAILURES))
    sys.exit(1)
print("Wszystkie testy przeszly.")
