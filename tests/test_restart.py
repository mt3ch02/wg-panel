#!/usr/bin/env python3
"""Po restarcie Pi: kolejność reguł przy Dockerze i pamięć handshake'u oraz ruchu.

1. Docker: atrapa iptables z prawdziwym stanem (łańcuchy, kolejność, -I/-D/-C/-S), prawdziwe
   linie PostUp/PostDown z wg0.conf wykonywane przez bash, start Dockera odtworzony tak jak
   robi to dockerd (DOCKER-USER zawsze na samą górę FORWARD). Sprawdza: stan ze zrzutu
   użytkownika jest wykrywany, „Przywróć stan z panelu” go naprawia, restart Pi w obu
   kolejnościach i restart Dockera nie psują już kolejności, bez Dockera nic się nie zmienia.
2. Pamięć: łączny ruch i ostatni handshake przez restart Pi, rotację kluczy i restart
   interfejsu; alert o ciszy raz na przerwę; lista, karta peera, API i wgctl na żywo.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "helper"))
TMP = tempfile.mkdtemp(prefix="wgpanel-restart-")
os.chmod(TMP, 0o755)
FAILURES = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  [{'OK  ' if condition else 'BLAD'}] {label}"
          + (f"  ({detail})" if detail and not condition else ""))
    if not condition:
        FAILURES.append(label)


# --------------------------------------------------------------------------
# atrapa iptables ze stanem
# --------------------------------------------------------------------------

BIN = os.path.join(TMP, "bin")              # narzędzia bez dockerd
DOCKER_BIN = os.path.join(TMP, "docker")    # sam dockerd - dokładany do PATH, gdy "jest Docker"
WGDIR = os.path.join(TMP, "wireguard")
for path in (BIN, DOCKER_BIN, WGDIR):
    os.makedirs(path)
STATE = os.path.join(TMP, "ipt.json")
BUILTIN = {"INPUT", "FORWARD", "OUTPUT"}
TARGETS = {"ACCEPT", "DROP", "RETURN", "REJECT", "LOG", "MASQUERADE"}

FAKE_IPTABLES = r'''#!%(python)s
import json, os, sys
STATE = %(state)r
BUILTIN = {"INPUT", "FORWARD", "OUTPUT"}
TARGETS = {"ACCEPT", "DROP", "RETURN", "REJECT", "LOG", "MASQUERADE"}
tool = os.path.basename(sys.argv[0])
args = sys.argv[1:]
data = json.load(open(STATE)) if os.path.exists(STATE) else {}
chains = data.setdefault(tool, {name: [] for name in BUILTIN})
def save():
    json.dump(data, open(STATE, "w"))
def refs(name):
    return any(rule.endswith("-j " + name) for rules in chains.values() for rule in rules)
if args[:2] == ["-t", "nat"]:
    sys.exit(1 if "-C" in args else 0)
if tool.endswith("-restore"):
    owner = tool[:-len("-restore")]
    chains = data.setdefault(owner, {name: [] for name in BUILTIN})
    for line in open(args[-1]):
        line = line.strip()
        if line.startswith(":"):
            chains[line[1:].split()[0]] = []
        elif line.startswith("-A "):
            name, _sep, spec = line[3:].partition(" ")
            chains.setdefault(name, []).append(spec)
    save(); sys.exit(0)
op, name, rest = args[0], args[1] if len(args) > 1 else "", args[2:]
if op == "-S":
    if name not in chains:
        sys.exit(1)
    head = "-P %%s ACCEPT" %% name if name in BUILTIN else "-N %%s" %% name
    print("\n".join([head] + ["-A %%s %%s" %% (name, r) for r in chains[name]])); sys.exit(0)
if op == "-N":
    if name in chains:
        sys.exit(1)
    chains[name] = []; save(); sys.exit(0)
if op in ("-F", "-X"):
    if name not in chains:
        sys.exit(1)
    if op == "-F":
        chains[name] = []
    elif name in BUILTIN or refs(name) or chains[name]:
        sys.exit(1)
    else:
        del chains[name]
    save(); sys.exit(0)
if name not in chains:
    sys.exit(1)
if op in ("-I", "-A", "-C", "-D") and rest and rest[0].isdigit() and op != "-C":
    number = int(rest[0]); rest = rest[1:]
else:
    number = None
spec = " ".join(rest)
if op == "-C":
    sys.exit(0 if spec in chains[name] else 1)
if op in ("-I", "-A"):
    target = rest[rest.index("-j") + 1] if "-j" in rest else ""
    if target and target not in TARGETS and target not in chains:
        sys.exit(2)                       # skok do nieistniejącego łańcucha
    if op == "-A":
        chains[name].append(spec)
    else:
        chains[name].insert((number or 1) - 1, spec)
    save(); sys.exit(0)
if op == "-D":
    if number is not None and not spec:
        if number > len(chains[name]):
            sys.exit(1)
        del chains[name][number - 1]
    elif spec in chains[name]:
        chains[name].remove(spec)
    else:
        sys.exit(1)
    save(); sys.exit(0)
sys.exit(0)
'''


def tool_script(name: str, body: str, where: str = BIN) -> None:
    path = os.path.join(where, name)
    with open(path, "w", encoding="utf8") as handle:
        handle.write(body)
    os.chmod(path, 0o755)


for name in ("iptables", "ip6tables", "iptables-restore", "ip6tables-restore"):
    tool_script(name, FAKE_IPTABLES % {"python": sys.executable, "state": STATE})
tool_script("dockerd", "#!/bin/sh\nexit 0\n", DOCKER_BIN)
WG_DUMP = os.path.join(TMP, "dump")
tool_script("wg", f'#!/bin/bash\ncase "$1" in show) [ "$3" = "dump" ] && cat {WG_DUMP} 2>/dev/null; '
                  'exit 0;; pubkey) cat >/dev/null; head -c 32 /dev/urandom | base64;; '
                  'genkey|genpsk) head -c 32 /dev/urandom | base64;; esac\nexit 0\n')
tool_script("wg-quick", "#!/bin/bash\nexit 0\n")
tool_script("systemctl", '#!/bin/bash\nif [ "$1" = "is-active" ]; then echo active; fi\nexit 0\n')
tool_script("ip", "#!/bin/bash\nexit 0\n")

SYSTEM_PATH = os.environ["PATH"]


def use_docker(present: bool) -> str:
    """PATH tylko z atrapami (bez systemowego katalogu - tu dockerd bywa naprawdę)."""
    path = BIN + (os.pathsep + DOCKER_BIN if present else "")
    os.environ["PATH"] = path
    return path


def chains(tool: str = "iptables") -> dict:
    return json.load(open(STATE)).get(tool, {}) if os.path.exists(STATE) else {}


def put(state: dict) -> None:
    json.dump({"iptables": state}, open(STATE, "w"))


def boot() -> None:
    """Świeży start Pi: puste reguły (ufw dokłada swoje łańcuchy na koniec FORWARD)."""
    put({"INPUT": [], "FORWARD": [], "OUTPUT": [],
         "ufw-before-forward": [], "ufw-after-forward": []})
    data = chains()
    data["FORWARD"] += ["-j ufw-before-forward", "-j ufw-after-forward"]
    put(data)


def docker_start() -> None:
    """To, co robi dockerd przy starcie (Docker 28): DOCKER-USER z RETURN na końcu, skoki
    przesuwane na samą górę FORWARD - najpierw DOCKER-FORWARD, potem DOCKER-USER nad nim."""
    data = chains()
    data.setdefault("DOCKER-USER", [])
    if "-j RETURN" not in data["DOCKER-USER"]:
        data["DOCKER-USER"].append("-j RETURN")
    data["DOCKER-FORWARD"] = ["-o docker0 -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT",
                              "-i docker0 -j ACCEPT"]
    for target in ("DOCKER-FORWARD", "DOCKER-USER"):
        jump = f"-j {target}"
        data["FORWARD"] = [r for r in data["FORWARD"] if r != jump]
        data["FORWARD"].insert(0, jump)
    put(data)


import wg_panel_helper as H  # noqa: E402
from panel import db, model, notify, render, scheduler, stats  # noqa: E402

H.WG_DIR = WGDIR
conn = db.connect(os.path.join(TMP, "logic.db"))
db.init_defaults(conn)
cfg = H.validate_settings(model.settings_only(conn))
RULES = H.rules_path(cfg)
CONF = H.render_wg_conf(cfg, [], "KLUCZ=", RULES)
with open(RULES, "w", encoding="utf8") as handle:
    handle.write(H.render_rules_file(cfg, [], 4))
with open(H.conf_path(cfg), "w", encoding="utf8") as handle:
    handle.write(CONF)
EXPECTED = H.fingerprint(cfg)
UP = [line.split("=", 1)[1].strip() for line in CONF.splitlines() if line.startswith("PostUp")]
DOWN = [line.split("=", 1)[1].strip() for line in CONF.splitlines() if line.startswith("PostDown")]


def wg_quick(lines: list, docker: bool) -> None:
    """Tak jak wg-quick: każda linia przez bash, błędy pojedynczych poleceń nie przerywają."""
    for line in lines:
        subprocess.run(["/bin/bash", "-c", line], env={"PATH": use_docker(docker)},
                       capture_output=True)


def hook_findings() -> list:
    return [f["message"] for f in H.detect_drift(cfg, EXPECTED) if f["kind"] == "hook"]


JUMP = "-i wg0 -j WG-USERS"

print("\n1. Docker i kolejność reguł")
check("renderer panelu i helpera dają te same podpięcia",
      render._hook_lines("iptables", dict(model.settings_only(conn), wan_if=cfg["wan_if"]),
                         RULES, cfg["vpn"], True)
      == H._hook_lines("iptables", cfg, RULES, cfg["vpn"], True))
check("wg0.conf: skok w DOCKER-USER tylko, gdy jest dockerd",
      any("command -v dockerd" in line and "DOCKER-USER" in line for line in UP))

# a) dokładnie to, co pokazał zrzut: Docker wystartował po wg-quick ze starą konfiguracją
use_docker(True)
put({"INPUT": ["-i wg0 -j WG-USERS-HOST"], "OUTPUT": [],
     "FORWARD": ["-j DOCKER-USER", "-j DOCKER-FORWARD", JUMP,
                 "-o wg0 -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT",
                 "-j ufw-before-forward"],
     "DOCKER-USER": ["-j RETURN"], "DOCKER-FORWARD": ["-i docker0 -j ACCEPT"],
     "ufw-before-forward": [], "WG-USERS": [], "WG-USERS-HOST": []})
subprocess.run([os.path.join(BIN, "iptables-restore"), "-n", RULES])
check("stan ze zrzutu: integralność zgłasza regułę przed panelem", bool(hook_findings()))
H.apply_family("iptables", cfg, RULES)          # „Przywróć stan z panelu” / „Zastosuj zmiany”
state = chains()
check("„Przywróć stan z panelu” naprawia: integralność bez uwag", not hook_findings(),
      str(hook_findings()))
check("skok panelu pierwszy w FORWARD i tylko jeden",
      state["FORWARD"][0] == JUMP and state["FORWARD"].count(JUMP) == 1, str(state["FORWARD"]))
check("skok panelu pierwszy w DOCKER-USER, RETURN Dockera zostaje na końcu",
      state["DOCKER-USER"][0] == JUMP and state["DOCKER-USER"][-1] == "-j RETURN",
      str(state["DOCKER-USER"]))
docker_start()
check("potem restart Dockera: integralność dalej bez uwag", not hook_findings(),
      str(chains()["FORWARD"][:3]))

# b) restart Pi: najpierw wg-quick, potem Docker (tak było u użytkownika)
boot()
wg_quick(UP, docker=True)
docker_start()
state = chains()
check("restart Pi, wg-quick przed Dockerem: Docker na górze FORWARD (jak na zrzucie)",
      state["FORWARD"][0] == "-j DOCKER-USER", str(state["FORWARD"]))
check("...ale w DOCKER-USER pierwszy jest skok panelu", state["DOCKER-USER"][0] == JUMP,
      str(state["DOCKER-USER"]))
check("...więc integralność bez uwag", not hook_findings(), str(hook_findings()))

# c) restart Pi: najpierw Docker, potem wg-quick
boot()
docker_start()
wg_quick(UP, docker=True)
state = chains()
check("restart Pi, Docker przed wg-quick: skok panelu pierwszy w FORWARD i DOCKER-USER",
      state["FORWARD"][0] == JUMP and state["DOCKER-USER"][0] == JUMP
      and not hook_findings(), str(state["FORWARD"]))
docker_start()
docker_start()
check("kolejne restarty Dockera nie psują kolejności", not hook_findings())

# d) ktoś wstawia własną regułę na górę DOCKER-USER - to ma zostać wykryte
data = chains()
data["DOCKER-USER"].insert(0, "-j ACCEPT")
put(data)
check("cudza reguła na górze DOCKER-USER jest wykrywana", bool(hook_findings()))
H.apply_family("iptables", cfg, RULES)
check("i naprawiana przez „Przywróć”", not hook_findings() and chains()["DOCKER-USER"][0] == JUMP)

# e) zatrzymanie tunelu: PostDown sprząta skoki i łańcuch, nie rusza Dockera
wg_quick(DOWN, docker=True)
state = chains()
check("PostDown: łańcuch WG-USERS usunięty, skoki z FORWARD i DOCKER-USER też",
      "WG-USERS" not in state and JUMP not in state["FORWARD"]
      and JUMP not in state["DOCKER-USER"], str(state.get("DOCKER-USER")))
check("PostDown: reguły Dockera zostają", "-j DOCKER-USER" in state["FORWARD"]
      and state["DOCKER-USER"] == ["-j ACCEPT", "-j RETURN"], str(state["DOCKER-USER"]))
wg_quick(UP, docker=True)
H.teardown_family("iptables", cfg)
state = chains()
check("wyłączenie tunelu przez helpera też sprząta oba skoki i łańcuch",
      JUMP not in state["FORWARD"] and JUMP not in state["DOCKER-USER"]
      and "WG-USERS" not in state, str(state.get("DOCKER-USER")))

# f) bez Dockera: nic nowego się nie pojawia
boot()
wg_quick(UP, docker=False)
state = chains()
check("bez Dockera: PostUp nie tworzy DOCKER-USER", "DOCKER-USER" not in state, str(list(state)))
check("bez Dockera: skok panelu pierwszy, integralność bez uwag",
      state["FORWARD"][0] == JUMP and not hook_findings(), str(state["FORWARD"]))
use_docker(False)
H.apply_family("iptables", cfg, RULES)
check("bez Dockera: „Przywróć” też nie tworzy DOCKER-USER", "DOCKER-USER" not in chains())

# g) zdublowane skoki (np. ręczne wg-quick up dwa razy) - zostaje jeden, na górze
data = chains()
data["FORWARD"] = ["-j ufw-before-forward", JUMP, "-j ufw-after-forward", JUMP]
put(data)
H.apply_family("iptables", cfg, RULES)
check("zdublowane skoki: jeden, pierwszy", chains()["FORWARD"].count(JUMP) == 1
      and chains()["FORWARD"][0] == JUMP, str(chains()["FORWARD"]))
os.environ["PATH"] = SYSTEM_PATH

print("\n2. Pamięć handshake'u i ruchu")
T = int(time.time())


def add_peer(name: str, ip: str, char: str) -> tuple:
    key = char * 43 + "="
    tier = conn.execute("SELECT id FROM tiers WHERE name = 'ADMIN'").fetchone()["id"]
    with conn:
        conn.execute("INSERT INTO peers(name, ip, pubkey, psk, tier_id, state, access_mode, "
                     "created_at) VALUES (?, ?, ?, 'P', ?, 'active', 'none', ?)",
                     (name, ip, key, tier, T))
    return key, conn.execute("SELECT id FROM peers WHERE pubkey = ?", (key,)).fetchone()["id"]


key_j, id_j = add_peer("Ja", "10.0.0.2", "J")
key_m, id_m = add_peer("Anna", "10.0.0.4", "M")


def sample(rx: int, tx: int, hs: int, key: str = key_j) -> dict:
    return {"peers": {key: {"rx": rx, "tx": tx, "latest_handshake": hs, "endpoint": "1.2.3.4:5"}}}


GIB = 1024 ** 3
stats.update(conn, sample(1000, 5000, T - 60), now=T, boot="A")
stats.update(conn, sample(3000, 9 * GIB, T - 30), now=T + 30, boot="A")
seen = stats.view(conn, sample(3000, 9 * GIB, T - 30), boot="A")[id_j]
check("przed restartem: łączny ruch = liczniki jądra", (seen["rx"], seen["tx"]) == (3000, 9 * GIB),
      str(seen))
after = sample(0, 0, 0)
seen = stats.view(conn, after, boot="B")[id_j]
check("po restarcie Pi (liczniki 0, handshake 0): pamięta handshake",
      seen["handshake"] == T - 30, str(seen))
check("po restarcie Pi: pamięta 9 GiB", seen["tx"] == 9 * GIB and seen["rx"] == 3000, str(seen))
stats.update(conn, after, now=T + 60, boot="B")
stats.update(conn, sample(500, 700, 0), now=T + 90, boot="B")
seen = stats.view(conn, sample(800, 1000, 0), boot="B")[id_j]
check("po restarcie ruch dolicza się do starego (zapis + bieżące, bez czekania na zapis)",
      (seen["rx"], seen["tx"]) == (3000 + 800, 9 * GIB + 1000), str(seen))
stats.update(conn, sample(800, 1000, 0), now=T + 120, boot="B")
check("bezczynny peer nie pisze do bazy",
      stats.update(conn, sample(800, 1000, 0), now=T + 150, boot="B") == 0)
stats.update(conn, sample(50, 60, T + 170), now=T + 180, boot="B")      # restart samego wg0
seen = stats.view(conn, sample(50, 60, T + 170), boot="B")[id_j]
check("restart samego interfejsu (licznik spadł): dolicza, nie odejmuje",
      (seen["rx"], seen["tx"]) == (3850, 9 * GIB + 1060) and seen["handshake"] == T + 170, str(seen))
new_key = "N" * 43 + "="
with conn:
    conn.execute("UPDATE peers SET pubkey = ? WHERE id = ?", (new_key, id_j))
stats.update(conn, sample(10, 20, T + 200, new_key), now=T + 210, boot="B")
seen = stats.view(conn, sample(10, 20, T + 200, new_key), boot="B")[id_j]
check("rotacja kluczy: historia osoby zostaje, nowy klucz liczy się od zera",
      (seen["rx"], seen["tx"]) == (3860, 9 * GIB + 1080), str(seen))
seen = stats.view(conn, {"peers": {}}, boot="B")[id_j]
check("peer zawieszony (nie ma go na interfejsie): widać zapamiętane",
      seen["tx"] == 9 * GIB + 1080 and seen["handshake"] == T + 200, str(seen))
check("od kiedy liczymy", seen["since"] == T, str(seen))

# start pamięci z historii: próbki wykresu i ostatni handshake widziany przez alerty
key_c, id_c = add_peer("Tom", "10.0.0.3", "C")
with conn:
    for ts, rx, tx in ((T - 3000, 100, 1000), (T - 2700, 400, GIB), (T - 2400, 900, 2 * GIB)):
        conn.execute("INSERT INTO traffic(ts, pubkey, rx, tx) VALUES (?, ?, ?, ?)", (ts, key_c, rx, tx))
    conn.execute("INSERT INTO peer_seen(pubkey, last_endpoint, last_handshake, updated_at) "
                 "VALUES (?, '5.6.7.8:9', ?, ?)", (key_c, T - 2450, T - 2400))
stats.update(conn, sample(30, 70, 0, key_c), now=T, boot="B", started=T - 600)
seen = stats.view(conn, sample(30, 70, 0, key_c), boot="B")[id_c]
check("pierwszy wpis po restarcie: ruch z próbek wykresu + to, co po restarcie",
      (seen["rx"], seen["tx"]) == (800 + 30, 2 * GIB - 1000 + 70), str(seen))
check("pierwszy wpis: handshake sprzed restartu (z alertów), liczone od pierwszej próbki",
      seen["handshake"] == T - 2450 and seen["since"] == T - 3000, str(seen))
key_d, id_d = add_peer("Ola", "10.0.0.5", "D")
with conn:
    for ts, rx, tx in ((T - 200, 100, 100), (T - 100, 300, 500)):
        conn.execute("INSERT INTO traffic(ts, pubkey, rx, tx) VALUES (?, ?, ?, ?)", (ts, key_d, rx, tx))
stats.update(conn, sample(350, 600, T - 5, key_d), now=T, boot="B", started=T - 600)
seen = stats.view(conn, sample(350, 600, T - 5, key_d), boot="B")[id_d]
check("pierwszy wpis bez restartu: bez podwójnego liczenia",
      (seen["rx"], seen["tx"]) == (200 + 50, 400 + 100), str(seen))

# alert o ciszy: raz na przerwę, także po restarcie Pi
for key, value in (("alert_dead_hours", "48"), ("alerts_quiet_minutes", "0")):
    db.set_setting(conn, key, value)
stats.update(conn, sample(0, 0, T - 3 * 86400, key_m), now=T - 3 * 86400 + 10, boot="A")
reboot = {"peers": {key_m: {"rx": 0, "tx": 0, "latest_handshake": 0, "endpoint": "(none)"}}}
for _round in range(3):
    notify.check_peers(conn, reboot)
dead = [r["message"] for r in conn.execute("SELECT * FROM alerts WHERE kind = 'dead_peer'")]
check("cisza po restarcie Pi zauważona (handshake z pamięci)", len(dead) >= 1, str(dead))
check("alert o ciszy raz, nie co 30 minut", len(dead) == 1, str(dead))
stats.update(conn, sample(5, 5, T - 2 * 3600, key_m), now=T - 2 * 3600, boot="B")
notify.check_peers(conn, sample(5, 5, T - 2 * 3600, key_m))
check("połączył się niedawno: bez nowego alertu",
      conn.execute("SELECT COUNT(*) FROM alerts WHERE kind = 'dead_peer'").fetchone()[0] == 1)
stats.update(conn, sample(5, 5, T - 50 * 3600, key_m), now=T, boot="B")
with conn:
    conn.execute("UPDATE peer_stats SET last_handshake = ? WHERE peer_id = ?", (T - 50 * 3600, id_m))
notify.check_peers(conn, sample(5, 5, T - 50 * 3600, key_m))
check("kolejna przerwa po ponownym połączeniu: nowy alert",
      conn.execute("SELECT COUNT(*) FROM alerts WHERE kind = 'dead_peer'").fetchone()[0] == 2)

worker = scheduler.Scheduler(conn, lambda: (sample(99, 99, T, new_key), ""), log=lambda m: None)
before = conn.execute("SELECT tx_total FROM peer_stats WHERE peer_id = ?", (id_j,)).fetchone()[0]
worker.check_alerts()
after_total = conn.execute("SELECT tx_total FROM peer_stats WHERE peer_id = ?", (id_j,)).fetchone()[0]
check("harmonogram zapisuje przyrosty co obrót", after_total > before, f"{before} -> {after_total}")

# odtworzenie kopii z innego systemu nie przenosi zapamiętanego klucza serwera
from panel import export  # noqa: E402
db.set_setting(conn, "server_pubkey", "STARYKLUCZSERWERA")
blob, _enc = export.serialize(conn)
db.set_setting(conn, "server_pubkey", "NOWYKLUCZSERWERA")
export.restore(conn, json.loads(blob))
check("odtworzenie kopii nie wpisuje starego klucza serwera do plików klientów",
      db.settings(conn).get("server_pubkey") == "NOWYKLUCZSERWERA", db.settings(conn).get("server_pubkey"))


print("\n3. Panel na żywo (restart Pi: jądro mówi „nigdy” i 0 B)")
from panel import helperclient  # noqa: E402

PORT = 8953
BASE = f"http://127.0.0.1:{PORT}"
PANEL_DB = os.path.join(TMP, "panel.db")
env = dict(os.environ, PATH=BIN + os.pathsep + SYSTEM_PATH, WGPANEL_DB=PANEL_DB,
           WGPANEL_HOST="127.0.0.1", WGPANEL_PORT=str(PORT), WGPANEL_WGDIR=WGDIR,
           WGPANEL_SOCKET=os.path.join(TMP, "helper.sock"), PYTHONPATH=ROOT)
helperclient.SOCKET_PATH = env["WGPANEL_SOCKET"]
with open(os.path.join(WGDIR, "server_private.key"), "w", encoding="utf8") as handle:
    handle.write("KLUCZPRYWATNYSERWERA00000000000000000000000=\n")
KEY, KEY2 = "Q" * 43 + "=", "W" * 43 + "="
with open(WG_DUMP, "w", encoding="utf8") as handle:
    handle.write(f"PRIV=\tPUB=\t41194\toff\n{KEY}\tPSK=\t(none)\t10.0.0.4/32\t0\t0\t0\t25\n"
                 f"{KEY2}\tPSK=\t(none)\t10.0.0.3/32\t0\t0\t0\t25\n")
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


helper = subprocess.Popen([sys.executable, os.path.join(ROOT, "helper", "wg_panel_helper.py")],
                          env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
panel = subprocess.Popen([sys.executable, os.path.join(ROOT, "panel", "app.py")],
                         env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(1.8)
try:
    request("/setup", {"username": "admin", "password": "bardzo-dlugie-haslo",
                       "password2": "bardzo-dlugie-haslo"})
    request("/login", {"username": "admin", "password": "bardzo-dlugie-haslo"})
    live = sqlite3.connect(PANEL_DB)
    tier = live.execute("SELECT id FROM tiers WHERE name = 'ADMIN'").fetchone()[0]
    live.execute("INSERT INTO peers(name, ip, pubkey, psk, tier_id, state, access_mode, created_at) "
                 "VALUES ('Anna', '10.0.0.4', ?, 'P', ?, 'active', 'none', 0)", (KEY, tier))
    pid = live.execute("SELECT id FROM peers WHERE name = 'Anna'").fetchone()[0]
    remembered = int(time.time()) - 2 * 86400 - 100
    since = int(time.time()) - 10 * 86400
    live.execute("INSERT INTO peer_stats(peer_id, pubkey, boot_id, last_rx, last_tx, rx_total, "
                 "tx_total, last_handshake, since) VALUES (?, ?, 'przed-restartem', 5, 5, ?, ?, ?, ?)",
                 (pid, KEY, 21 * 1024 * 1024, int(1.8 * GIB), remembered, since))
    live.execute("INSERT INTO peers(name, ip, pubkey, psk, tier_id, state, access_mode, created_at) "
                 "VALUES ('Tom', '10.0.0.3', ?, 'P', ?, 'active', 'none', 0)", (KEY2, tier))
    pid2 = live.execute("SELECT id FROM peers WHERE name = 'Tom'").fetchone()[0]
    old = int(time.time()) - 5 * 86400 - 3600  # próbki sprzed restartu (starsze niż start Pi)
    for step, (rx, tx) in enumerate(((0, 0), (10 * 1024 ** 2, GIB), (21 * 1024 ** 2, 2 * GIB))):
        live.execute("INSERT INTO traffic(ts, pubkey, rx, tx) VALUES (?, ?, ?, ?)",
                     (old + step * 300, KEY2, rx, tx))
    live.execute("INSERT INTO peer_seen(pubkey, last_endpoint, last_handshake, updated_at) "
                 "VALUES (?, '5.6.7.8:9', ?, ?)", (KEY2, old + 590, old + 600))
    live.commit()
    since_text = time.strftime("%Y-%m-%d", time.localtime(since))

    _c, page, _l = request("/")
    row = next(r for r in page.split("<tr") if f'data-peer="{pid}"' in r)
    check("lista: handshake z pamięci zamiast „nigdy”",
          "2 dni temu" in row and "nigdy" not in row and f'data-handshake="{remembered}"' in row, row[:400])
    check("lista: łączny ruch zamiast 0 B (najpierw pobrane, potem wysłane - jak na wykresie)",
          '<i>↓</i>1.8<span class="unit">GB</span>' in row
          and '<i>↑</i>21.0<span class="unit">MB</span>' in row
          and row.index("<i>↓</i>") < row.index("<i>↑</i>"), row[:600])
    check("lista: podpowiedź, od kiedy liczone", f"łącznie od {since_text}" in row)
    _c, body, _l = request("/api/peers")
    entry = next(p for p in json.loads(body)["peers"] if p["id"] == pid)
    check("odświeżanie listy (API) nie wraca do zer",
          entry["handshake"] == remembered and entry["tx"] == int(1.8 * GIB), str(entry))
    _c, page, _l = request(f"/peers/{pid}")
    check("karta peera: handshake z pamięci i ruch łącznie",
          "2 dni temu" in page and "ruch łącznie" in page and '<i>↓</i>1.8<span class="unit">GB</span>' in page)
    time.sleep(9)       # harmonogram panelu zdąży zapisać pierwszą próbkę po „restarcie”
    total = live.execute("SELECT tx_total, last_handshake FROM peer_stats WHERE peer_id = ?",
                         (pid,)).fetchone()
    check("harmonogram po restarcie nie zeruje pamięci", total == (int(1.8 * GIB), remembered),
          str(total))
    _c, page, _l = request("/")
    row = next(r for r in page.split("<tr") if f'data-peer="{pid2}"' in r)
    check("po aktualizacji pamięć startuje z historii: handshake sprzed restartu i ruch z wykresu",
          "5 dni temu" in row and '<i>↓</i>2.0<span class="unit">GB</span>' in row
          and '<i>↑</i>21.0<span class="unit">MB</span>' in row, row[300:1200])
    live.execute("INSERT INTO settings(key, value) VALUES ('server_pubkey', 'STARYKLUCZ') "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value")
    live.commit()
    _c, page, _l = request("/settings")
    stored = live.execute("SELECT value FROM settings WHERE key = 'server_pubkey'").fetchone()[0]
    check("zły zapamiętany klucz serwera poprawiany z helpera (Ustawienia)",
          stored != "STARYKLUCZ" and stored in page and "STARYKLUCZ" not in page, stored)
    request("/lang/en?next=/")
    _c, page, _l = request(f"/peers/{pid}")
    check("EN: „total traffic”, bez polskiego", "total traffic" in page and "ruch łącznie" not in page
          and f"total since {since_text}" in page)
    result = subprocess.run([sys.executable, os.path.join(ROOT, "wgctl"), "list"], env=env,
                            capture_output=True, text=True)
    check("wgctl list: handshake z pamięci", "2 dni temu" in result.stdout,
          result.stdout + result.stderr)
    result = subprocess.run([sys.executable, os.path.join(ROOT, "wgctl"), "traffic", "Anna"],
                            env=env, capture_output=True, text=True)
    check("wgctl traffic: łącznie od początku liczenia",
          f"lacznie od {since_text}" in result.stdout and "1.8 GB" in result.stdout,
          result.stdout + result.stderr)
finally:
    panel.terminate()
    helper.terminate()

print("\n" + "=" * 60)
if FAILURES:
    print(f"NIEUDANE ({len(FAILURES)}):")
    for item in FAILURES:
        print("  -", item)
    sys.exit(1)
print("Wszystkie testy przeszly.")
