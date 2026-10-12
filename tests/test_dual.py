#!/usr/bin/env python3
"""Ten sam plik konfiguracyjny na dwóch urządzeniach naraz (panel/dual.py).

1. Logika na symulowanych próbkach `wg show dump` (co 5 s): dwa urządzenia - alarm, zwykła
   zmiana sieci i wielokrotne włączanie tunelu - cisza, koniec - wpis "ustało", restart panelu,
   przełącznik w ustawieniach, wyciszenie alertu o nowym adresie.
2. Helper naprawdę (atrapa wg): lekka operacja "peers" i brak szumu w dzienniku.
3. Harmonogram: szybka próbka co 5 s, reszta co 30 s, zapas na starszego helpera.
4. Panel na żywo: plakietka na liście, ramka na karcie peera, przełącznik - po polsku i angielsku.
"""
from __future__ import annotations

import http.server
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
TMP = tempfile.mkdtemp(prefix="wgpanel-dual-")
BIN, WGDIR = os.path.join(TMP, "bin"), os.path.join(TMP, "wireguard")
os.makedirs(BIN)
os.makedirs(WGDIR)
FAILURES = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  [{'OK  ' if condition else 'BLAD'}] {label}"
          + (f"  ({detail})" if detail and not condition else ""))
    if not condition:
        FAILURES.append(label)


# --- odbiornik ntfy ---
received: list = []


class Sink(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


sink = http.server.HTTPServer(("127.0.0.1", 0), Sink)
threading.Thread(target=sink.serve_forever, daemon=True).start()
NTFY = f"http://127.0.0.1:{sink.server_port}/wg-test-k7f2q9x4m1"

from panel import db, dual, helperclient, notify, scheduler  # noqa: E402

conn = db.connect(os.path.join(TMP, "logic.db"))
db.init_defaults(conn)
for key, value in (("alerts_channel", "ntfy"), ("alerts_url", NTFY), ("alerts_quiet_minutes", "0")):
    db.set_setting(conn, key, value)


def add_peer(name: str, ip: str, char: str) -> tuple:
    key = char * 43 + "="
    tier = conn.execute("SELECT id FROM tiers WHERE name = 'NET'").fetchone()["id"]
    with conn:
        conn.execute(
            """INSERT INTO peers(name, ip, pubkey, psk, tier_id, state, access_mode, created_at)
               VALUES (?, ?, ?, ?, ?, 'active', 'none', ?)""",
            (name, ip, key, "P" * 43 + "=", tier, int(time.time())))
    peer_id = conn.execute("SELECT id FROM peers WHERE pubkey = ?", (key,)).fetchone()["id"]
    return key, peer_id


def alerts(kind: str = dual.KIND) -> list:
    return list(conn.execute("SELECT * FROM alerts WHERE kind = ? ORDER BY id", (kind,)))


def last_handshake(times: list, t: float) -> int:
    done = [h for h in times if h <= t]
    return int(max(done)) if done else 0


def simulate(watch, key: str, start: int, seconds: int, endpoint_at, handshakes: list,
             step: int = 5) -> list:
    """Próbki co `step` sekund. endpoint_at(t) -> adres widziany przez serwer w chwili t."""
    changes = []
    for t in range(start, start + seconds, step):
        sample = {key: {"endpoint": endpoint_at(t), "latest_handshake": last_handshake(handshakes, t)}}
        for change in watch.observe(conn, sample, now=t):
            changes.append((t,) + change)
    return changes


HOME, WORK = "203.0.113.21:51820", "198.51.100.22:40311"
T0 = 1_800_000_000

print("\n1. Logika wykrywania")

# --- a) dwa urządzenia, oba bezczynne: keepalive co 25 s każde, przesunięte w fazie ---
received.clear()
key_m, id_m = add_peer("Anna", "10.0.0.4", "M")
watch = dual.Watch()


def two_idle(t: float) -> str:
    return HOME if (t - T0) % 25 < 12 else WORK


hs_two = [T0 + 3 + 121 * i for i in range(20)] + [T0 + 61 + 123 * i for i in range(20)]
changes = simulate(watch, key_m, T0, 600, two_idle, hs_two)
starts = [c for c in changes if c[1] == "start"]
check("dwa urządzenia: alarm", len(starts) == 1, str(changes))
check("dwa urządzenia: wykryte w ciągu 3 minut", bool(starts) and starts[0][0] - T0 <= 180,
      str(starts))
rows = alerts()
check("alert w bazie: rodzaj two_devices, waga alarm",
      len(rows) == 1 and rows[0]["severity"] == "alarm", str([dict(r) for r in rows]))
check("treść alertu podaje oba adresy",
      bool(rows) and "203.0.113.21:51820" in rows[0]["message"] and "198.51.100.22:40311" in rows[0]["message"],
      rows[0]["message"] if rows else "")
check("ntfy: tytuł z imieniem, priorytet 5 jak przy nowym adresie",
      any(m["title"] == "Anna: tunel na dwóch urządzeniach" and m["priority"] == 5 for m in received),
      str(received))
incident = dual.incidents(conn).get(id_m)
check("incydent zapisany w bazie z oboma adresami",
      bool(incident) and set(incident["endpoints"]) == {HOME, WORK}, str(incident))
check("klucz na liście podejrzanych (wycisza alert o nowym adresie)", key_m in watch.suspects)
check("trwające przeskakiwanie nie wysyła kolejnych alertów",
      len(alerts()) == 1 and len(received) == 1, str(len(received)))

# --- wyciszenie new_endpoint dla tego klucza, a dla innych nie ---
key_j, id_j = add_peer("Ja", "10.0.0.2", "J")
with conn:
    for key, endpoint in ((key_m, HOME), (key_j, "5.5.5.5:1000")):
        conn.execute("INSERT INTO peer_seen(pubkey, last_endpoint, last_handshake, updated_at) "
                     "VALUES (?, ?, 0, 0)", (key, endpoint))
notify.check_peers(conn, {"peers": {key_m: {"endpoint": WORK, "latest_handshake": T0},
                                    key_j: {"endpoint": "6.6.6.6:2000", "latest_handshake": T0}}},
                   skip_endpoint=watch.suspects)
endpoint_alerts = [r["subject"] for r in alerts("new_endpoint")]
check("przy przeskakiwaniu nie ma osobnego alertu o nowym adresie", "Anna" not in endpoint_alerts,
      str(endpoint_alerts))
check("zwykła zmiana adresu innej osoby nadal daje alert", "Ja" in endpoint_alerts, str(endpoint_alerts))

# --- koniec: drugie urządzenie wyłączone, adres stały ---
received.clear()
T1 = T0 + 600
hs_one = [T1 + 2 + 121 * i for i in range(20)]
changes = simulate(watch, key_m, T1, 1500, lambda t: WORK, hs_one)
ends = [c for c in changes if c[1] == "end"]
check("koniec przeskakiwania: wpis \"ustało\"", len(ends) == 1, str(changes))
check("\"ustało\" po 10 minutach spokoju, nie wcześniej",
      bool(ends) and 600 <= ends[0][0] - T1 <= 700, str(ends))
end_rows = [r for r in alerts() if r["severity"] == "info"]
check("\"ustało\" w alertach jako informacja",
      len(end_rows) == 1 and "przeskakiwanie trwało" in end_rows[0]["message"], str([dict(r) for r in end_rows]))
check("\"ustało\" na ntfy bez dzwonienia (priorytet 2, nie 5)",
      len(received) == 1 and received[0]["priority"] == 2
      and received[0]["title"] == "Anna: przeskakiwanie ustało", str(received))
check("incydent usunięty z bazy", id_m not in dual.incidents(conn))
check("\"ustało\" nie podbija licznika nieprzeczytanych",
      conn.execute("SELECT COUNT(*) FROM alerts WHERE seen = 0 AND severity != 'info' "
                   "AND kind = ?", (dual.KIND,)).fetchone()[0] == 1)

# --- b) jedno urządzenie zmienia sieć raz: dom -> praca ---
key_c, id_c = add_peer("Tom", "10.0.0.3", "C")
watch_c = dual.Watch()
before = len(alerts())
changes = simulate(watch_c, key_c, T0, 1800, lambda t: HOME if t < T0 + 300 else WORK,
                   [T0 + 1 + 121 * i for i in range(20)])
check("zmiana sieci raz: bez alarmu", not changes and len(alerts()) == before, str(changes))

# --- c) tunel włączany i wyłączany co ~70 s: szybkie handshake'i, ale za każdym razem nowy port ---
watch_c = dual.Watch()
restarts = [T0 + 70 * i for i in range(6)]
changes = simulate(watch_c, key_c, T0, 600,
                   lambda t: f"203.0.113.21:{50000 + sum(1 for r in restarts if r <= t)}",
                   restarts)
check("wielokrotne włączanie tunelu: bez alarmu (nowy port to nie drugie urządzenie)",
      not changes, str(changes))

# --- d) jedno urządzenie zajęte (RDP), drugie bezczynne: drugie widać rzadko ---
key_b, id_b = add_peer("Ola", "10.0.0.5", "B")
watch_b = dual.Watch()
b_handshakes = [T0 + 31 + 122 * i for i in range(20)]
a_handshakes = [t + 15 for t in b_handshakes]   # A traci sesję i łączy się ponownie po 15 s


def busy(t: float) -> str:
    # drugie urządzenie ma adres tylko przez 3 s po swoim handshake'u
    return WORK if any(h + 0.5 <= t < h + 3.5 for h in b_handshakes) else HOME


changes = simulate(watch_b, key_b, T0, 1800, busy, b_handshakes + a_handshakes)
starts = [c for c in changes if c[1] == "start"]
check("jedno urządzenie zajęte, drugie rzadko widoczne: alarm w ciągu 15 minut",
      len(starts) == 1 and starts[0][0] - T0 <= 900, str(starts))

# --- e) restart panelu w trakcie incydentu: stan z bazy, koniec 10 min po restarcie ---
fresh = dual.Watch()
T2 = T0 + 4000
changes = simulate(fresh, key_b, T2, 900, lambda t: HOME, [T2 + 5 + 121 * i for i in range(10)])
ends = [c for c in changes if c[1] == "end"]
check("po restarcie panelu incydent kończy się po 10 min spokoju",
      len(ends) == 1 and 600 <= ends[0][0] - T2 <= 610, str(changes))

# --- f) przełącznik wyłączony: nic nie wykrywa, trwające incydenty znikają ---
watch_off = dual.Watch()
key_o, id_o = add_peer("Ewa", "10.0.0.6", "E")
simulate(watch_off, key_o, T0, 300, two_idle, hs_two)
check("(przygotowanie) incydent dla Ewy", id_o in dual.incidents(conn))
db.set_setting(conn, dual.SETTING, "0")
before = len(alerts())
changes = simulate(watch_off, key_o, T0 + 300, 900, two_idle, hs_two)
check("wyłączone: brak alarmów i wpisów \"ustało\"", not changes and len(alerts()) == before,
      str(changes))
check("wyłączone: trwający incydent usunięty bez wpisu", not dual.incidents(conn))
db.set_setting(conn, dual.SETTING, "1")

# --- g) zawieszony peer nie jest pilnowany, usunięty znika z incydentów ---
watch_g = dual.Watch()
with conn:
    conn.execute("UPDATE peers SET state = 'suspended' WHERE id = ?", (id_o,))
check("zawieszony peer: bez alarmu", not simulate(watch_g, key_o, T0, 600, two_idle, hs_two))
with conn:
    conn.execute("UPDATE peers SET state = 'active' WHERE id = ?", (id_o,))
simulate(watch_g, key_o, T0 + 600, 300, two_idle, hs_two)
with conn:
    conn.execute("DELETE FROM peers WHERE id = ?", (id_o,))
check("usunięcie peera usuwa jego incydent", id_o not in dual.incidents(conn))

# --- h) odstęp powtórek: kolejny incydent tej samej osoby nie zasypuje telefonu ---
db.set_setting(conn, "alerts_quiet_minutes", "30")
received.clear()
watch_h = dual.Watch()
with conn:
    conn.execute("DELETE FROM alerts")
simulate(watch_h, key_m, T0 + 9000, 300, two_idle, hs_two)
simulate(watch_h, key_m, T0 + 9300, 700, lambda t: WORK, [T0 + 9300 + 121 * i for i in range(10)])
simulate(watch_h, key_m, T0 + 10000, 300, two_idle, hs_two)
titles = [m["title"] for m in received]
check("drugi incydent w ciągu 30 min nie wysyła drugiego alarmu",
      titles.count("Anna: tunel na dwóch urządzeniach") == 1, str(titles))
db.set_setting(conn, "alerts_quiet_minutes", "0")

# --- i) poziom ważności: informacja z rodzaju krytycznego nie dzwoni ---
received.clear()
notify.send(db.settings(conn), "x", "y", "info", "watchdog_wg")
notify.send(db.settings(conn), "x", "y", "alarm", "watchdog_wg")
check("\"znowu działa\" rodzaju krytycznego: priorytet 2, problem: 5",
      [m["priority"] for m in received] == [2, 5], str(received))

print("\n2. Helper: lekka operacja \"peers\"")


def stub(name: str, script: str) -> None:
    path = os.path.join(BIN, name)
    with open(path, "w", encoding="utf8") as handle:
        handle.write("#!/bin/bash\n" + script)
    os.chmod(path, 0o755)


DUMP = os.path.join(TMP, "dump")
with open(DUMP, "w", encoding="utf8") as handle:
    handle.write("PRIV=\tPUB=\t41194\toff\n"
                 f"{key_m}\tPSK=\t{HOME}\t10.0.0.4/32\t{T0}\t1000\t2000\t25\n"
                 f"{key_j}\tPSK=\t(none)\t10.0.0.2/32\t0\t0\t0\t25\n")
stub("wg", f'case "$1" in show) [ "$3" = "dump" ] && cat {DUMP}; exit 0;; '
           'pubkey) cat >/dev/null; head -c 32 /dev/urandom | base64;; '
           'genkey|genpsk) head -c 32 /dev/urandom | base64;; esac\nexit 0\n')
stub("wg-quick", 'if [ "$1" = "strip" ]; then cat %s/wg0.conf 2>/dev/null; fi\nexit 0\n' % WGDIR)
for tool in ("iptables", "ip6tables"):
    stub(tool, 'for a in "$@"; do if [ "$a" = "-C" ]; then exit 1; fi; done\nexit 0\n')
for tool in ("iptables-restore", "ip6tables-restore", "ip"):
    stub(tool, "exit 0\n")
stub("systemctl", 'if [ "$1" = "is-active" ]; then echo active; fi\nexit 0\n')
with open(os.path.join(WGDIR, "server_private.key"), "w", encoding="utf8") as handle:
    handle.write("KLUCZPRYWATNYSERWERA00000000000000000000000=\n")

env = dict(os.environ)
env.update({"PATH": BIN + os.pathsep + env["PATH"], "WGPANEL_SOCKET": os.path.join(TMP, "helper.sock"),
            "WGPANEL_WGDIR": WGDIR, "PYTHONPATH": ROOT})
helperclient.SOCKET_PATH = env["WGPANEL_SOCKET"]
HELPER_LOG = os.path.join(TMP, "helper.log")
log_handle = open(HELPER_LOG, "w", encoding="utf8")
helper = subprocess.Popen([sys.executable, os.path.join(ROOT, "helper", "wg_panel_helper.py")],
                          env=env, stdout=log_handle, stderr=log_handle)
time.sleep(1.2)
panel = None
try:
    result = helperclient.call("peers", {"wg_iface": "wg0"})
    check("peers: adres i handshake dla każdego klucza",
          result["up"] and result["peers"][key_m] == {"endpoint": HOME, "latest_handshake": T0}
          and result["peers"][key_j]["endpoint"] == "(none)", str(result))
    try:
        helperclient.call("peers", {"wg_iface": "wg0; rm -rf /"})
        check("peers: zła nazwa interfejsu odrzucona", False)
    except helperclient.HelperError as exc:
        check("peers: zła nazwa interfejsu odrzucona", "interfejsu" in str(exc), str(exc))
    status = helperclient.call("status", {"settings": __import__("panel.model", fromlist=["x"])
                                          .settings_only(conn)})
    check("status po wspólnym parsowaniu bez zmian (port, ruch, AllowedIPs)",
          status["listen_port"] == "41194" and status["peers"][key_m]["rx"] == 1000
          and status["peers"][key_m]["allowed_ips"] == "10.0.0.4/32", str(status.get("peers")))
    for _round in range(3):
        helperclient.call("peers", {"wg_iface": "wg0"})
    time.sleep(0.3)
    log_text = open(HELPER_LOG, encoding="utf8").read()
    check("dziennik helpera: status zapisany, częsta próbka peers nie",
          "op=status" in log_text and "op=peers" not in log_text, log_text[-300:])

    print("\n3. Harmonogram: próbka co 5 s, reszta co 30 s")

    class FakeWatch:
        def __init__(self):
            self.calls, self.suspects = [], set()

        def observe(self, _conn, live, now=None):
            self.calls.append(live)

    sample = {"peers": {key_m: {"endpoint": HOME, "latest_handshake": T0}}}
    worker = scheduler.Scheduler(conn, lambda: (sample, ""), log=lambda m: None,
                                 peers_fn=lambda: None)
    worker.dual = FakeWatch()
    worker.check_devices()
    worker.check_alerts()
    check("starszy helper bez \"peers\": próbka z odpytania co 30 s",
          worker.dual.calls == [sample["peers"]], str(worker.dual.calls))
    worker.peers_fn = lambda: {"k": {"endpoint": WORK, "latest_handshake": 1}}
    worker.dual = FakeWatch()
    worker.check_devices()
    worker.check_alerts()
    check("z szybką próbką: jedna obserwacja, bez dubla z odpytania",
          worker.dual.calls == [{"k": {"endpoint": WORK, "latest_handshake": 1}}], str(worker.dual.calls))
    counts = {"fast": 0, "slow": 0}
    worker.check_devices = lambda: counts.__setitem__("fast", counts["fast"] + 1)
    for name in ("check_expiry", "check_alerts", "check_backup", "check_integrity", "check_anomalies"):
        setattr(worker, name, lambda: None)
    worker.check_expiry = lambda: counts.__setitem__("slow", counts["slow"] + 1)
    for _round in range(4):
        worker.tick()
    check("pierwszy obrót: wszystko; kolejne szybkie: tylko próbka",
          counts == {"fast": 4, "slow": 1}, str(counts))
    worker.last_tick -= scheduler.TICK
    worker.tick()
    check("po 30 s znowu cały obrót", counts == {"fast": 5, "slow": 2}, str(counts))
    check("szybka próbka co 5 s, reszta co 30 s", scheduler.FAST == 5 and scheduler.TICK == 30)

    print("\n4. Panel na żywo")
    PORT = 8947
    BASE = f"http://127.0.0.1:{PORT}"
    PANEL_DB = os.path.join(TMP, "panel.db")
    penv = dict(env, WGPANEL_DB=PANEL_DB, WGPANEL_HOST="127.0.0.1", WGPANEL_PORT=str(PORT))
    COOKIES: dict = {}

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    def request(path: str, data: dict | None = None):
        body = urllib.parse.urlencode(data).encode() if data is not None else None
        req = urllib.request.Request(BASE + path, data=body)
        if COOKIES:
            req.add_header("Cookie", "; ".join(f"{k}={v}" for k, v in COOKIES.items()))
        try:
            response = urllib.request.build_opener(NoRedirect).open(req, timeout=20)
            code, text, headers = response.status, response.read().decode("utf8"), response.headers
        except urllib.error.HTTPError as exc:
            code, text, headers = exc.code, exc.read().decode("utf8", "replace"), exc.headers
        for cookie in headers.get_all("Set-Cookie") or []:
            name, _sep, value = cookie.split(";")[0].partition("=")
            COOKIES[name] = value
        return code, text, headers.get("Location", "")

    def csrf(page: str) -> str:
        match = re.search(r'name="csrf" value="([^"]+)"', page)
        return match.group(1) if match else ""

    panel = subprocess.Popen([sys.executable, os.path.join(ROOT, "panel", "app.py")],
                             env=penv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.8)
    request("/setup", {"username": "admin", "password": "bardzo-dlugie-haslo",
                       "password2": "bardzo-dlugie-haslo"})
    request("/login", {"username": "admin", "password": "bardzo-dlugie-haslo"})
    live = sqlite3.connect(PANEL_DB)
    tier = live.execute("SELECT id FROM tiers WHERE name = 'NET'").fetchone()[0]
    live.execute("INSERT INTO peers(name, ip, pubkey, psk, tier_id, state, access_mode, created_at) "
                 "VALUES ('Anna', '10.0.0.4', ?, 'P', ?, 'active', 'none', 0)", (key_m, tier))
    pid = live.execute("SELECT id FROM peers WHERE name = 'Anna'").fetchone()[0]
    live.execute("INSERT INTO dual_use(peer_id, since, endpoints) VALUES (?, ?, ?)",
                 (pid, int(time.time()) - 300, json.dumps([HOME, WORK])))
    live.commit()

    _c, page, _l = request("/")
    check("lista peerów: plakietka „2 urządzenia”", "2 urządzenia" in page, page[:200])
    _c, page, _l = request(f"/peers/{pid}")
    check("karta peera: ramka z oboma adresami i radą",
          "Ten sam plik konfiguracyjny działa na dwóch urządzeniach naraz." in page
          and HOME in page and WORK in page and "Rotacja kluczy" in page)
    _c, page, _l = request("/alerts")
    check("alerty: przełącznik „Tunel na dwóch urządzeniach naraz”, domyślnie włączony",
          re.search(r'name="alert_two_devices" value="1" checked', page) is not None
          and "Tunel na dwóch urządzeniach naraz" in page)

    request("/lang/en?next=/")
    _c, page, _l = request("/")
    check("EN: plakietka „2 devices”", "2 devices" in page and "2 urządzenia" not in page)
    _c, page, _l = request(f"/peers/{pid}")
    check("EN: ramka po angielsku",
          "The same config file is active on two devices at once." in page
          and "przeskakuje" not in page and "urządzeniach" not in page)
    _c, page, _l = request("/alerts")
    check("EN: przełącznik i opis po angielsku", "Tunnel on two devices at once" in page
          and "fires when the same file is active on two devices" in page
          and "urządzeniach" not in page.split("<tbody>")[0])

    fields = {"csrf": csrf(page), "alerts_channel": "none", "alerts_url": "", "alerts_token": "",
              "alert_dead_hours": "48", "alerts_quiet_minutes": "30", "smtp_security": "starttls"}
    request("/alerts/settings", fields)
    value = live.execute("SELECT value FROM settings WHERE key = ?", (dual.SETTING,)).fetchone()[0]
    check("odznaczenie zapisuje 0", value == "0", value)
    deadline = time.time() + 15
    while time.time() < deadline and live.execute("SELECT COUNT(*) FROM dual_use").fetchone()[0]:
        time.sleep(0.5)
    check("harmonogram panelu (próbka co 5 s przez helpera) sprząta incydent po wyłączeniu",
          live.execute("SELECT COUNT(*) FROM dual_use").fetchone()[0] == 0)
    _c, page, _l = request("/alerts")
    request("/alerts/settings", dict(fields, csrf=csrf(page), alert_two_devices="1"))
    value = live.execute("SELECT value FROM settings WHERE key = ?", (dual.SETTING,)).fetchone()[0]
    check("zaznaczenie zapisuje 1", value == "1", value)
    check("alert o nowym adresie nadal zawsze włączony",
          live.execute("SELECT value FROM settings WHERE key = 'alert_new_endpoint'").fetchone()[0] == "1")
finally:
    if panel:
        panel.terminate()
    helper.terminate()
    log_handle.close()

print("\n" + "=" * 60)
if FAILURES:
    print(f"NIEUDANE ({len(FAILURES)}):")
    for item in FAILURES:
        print("  -", item)
    sys.exit(1)
print("Wszystkie testy przeszly.")
