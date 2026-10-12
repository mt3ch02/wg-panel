#!/usr/bin/env python3
"""Testy trzeciej tury: IPv6, katalog hostow, ruch, sumy kopii, integralnosc, QR."""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "helper"))

from panel import db, export, model, qrcode, render, simulate, traffic  # noqa: E402
import wg_panel_helper as H  # noqa: E402
import testnet  # noqa: E402

testnet.use(db)

TMP = tempfile.mkdtemp(prefix="wgpanel-extra-")
FAILURES = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  [{'OK  ' if condition else 'BLAD'}] {label}"
          + (f"  ({detail})" if detail and not condition else ""))
    if not condition:
        FAILURES.append(label)


def key(char: str) -> str:
    return base64.b64encode(bytes([ord(char)]) * 32).decode()


def fresh(name: str):
    path = os.path.join(TMP, name)
    if os.path.exists(path):
        os.remove(path)
    conn = db.connect(path)
    db.init_defaults(conn)
    return conn, path


def add_peer(conn, name, ip, tier="NAS", char="A", **extra):
    tier_row = conn.execute("SELECT id FROM tiers WHERE name = ?", (tier,)).fetchone()
    fields = {"state": "active", "access_mode": "none", "expires_at": None}
    fields.update(extra)
    with conn:
        conn.execute(
            """INSERT INTO peers(name, device, ip, pubkey, psk, privkey, tier_id, state, note,
                                 created_at, access_mode, expires_at)
               VALUES (?, '', ?, ?, ?, 'priv', ?, ?, '', ?, ?, ?)""",
            (name, ip, key(char), key("P"), tier_row["id"] if tier_row else None,
             fields["state"], int(time.time()), fields["access_mode"], fields["expires_at"]))
    return conn.execute("SELECT id FROM peers WHERE ip = ?", (ip,)).fetchone()["id"]


# ==========================================================================
print("\n1. Ostrzezenie o koncu okna")
now = time.time()
base = {"state": "active", "expires_at": None}
check("bez daty - bez ostrzezenia", db.expiry_warning(dict(base), 3, now) == "")
check("za 10 dni - bez ostrzezenia",
      db.expiry_warning(dict(base, expires_at=now + 10 * 86400), 3, now) == "")
check("za 2,5 dnia - ostrzezenie",
      db.expiry_warning(dict(base, expires_at=now + 2.5 * 86400), 3, now) == "kończy się za 2 dni")
check("za 1,5 dnia - jutro",
      db.expiry_warning(dict(base, expires_at=now + 1.5 * 86400), 3, now) == "kończy się jutro")
check("za 5 h - w godzinach",
      db.expiry_warning(dict(base, expires_at=now + 5 * 3600), 3, now) == "kończy się za 5 h")
check("minelo - bez ostrzezenia (od tego jest zawieszenie)",
      db.expiry_warning(dict(base, expires_at=now - 60), 3, now) == "")
check("zawieszony - bez ostrzezenia",
      db.expiry_warning(dict(base, state="suspended", expires_at=now + 3600), 3, now) == "")
check("prog 0 wylacza ostrzezenia",
      db.expiry_warning(dict(base, expires_at=now + 3600), 0, now) == "")

# ==========================================================================
print("\n2. Katalog hostow")
conn, _ = fresh("hosts.db")
check("NAS trafia do katalogu z ustawien", any(h["name"] == "nas" for h in db.hosts(conn)))
with conn:
    conn.execute("INSERT INTO hosts(name, address, address6) VALUES "
                 "('drukarka', '192.168.50.70', ''), ('ha', '192.168.50.80', 'fd00:50::80')")
cfg = db.render_cfg(conn)

rule = render.normalize_rule({"action": "ACCEPT", "dest": "drukarka"}, cfg)
check("nazwa hosta rozwija sie do adresu", rule["dest"] == "192.168.50.70/32", rule["dest"])
check("etykieta zachowuje nazwe", rule["label"] == "drukarka")
rule = render.normalize_rule({"action": "ACCEPT", "dest": "@drukarka"}, cfg)
check("zapis z @ tez dziala", rule["dest"] == "192.168.50.70/32")
try:
    render.normalize_rule({"action": "ACCEPT", "dest": "$NAS", "proto": "tcp", "ports": "445"}, cfg)
    alias_alive = True
except render.RuleError:
    alias_alive = False
check("alias $NAS usuniety z regul", not alias_alive)

# baza ze starszej wersji: $NAS w regulach i listach, odciety peer, brak hosta nas
conn_old, _ = fresh("legacy.db")
with conn_old:
    conn_old.execute("DELETE FROM hosts WHERE name = 'nas'")
    conn_old.execute("INSERT OR REPLACE INTO settings(key, value) VALUES ('nas', '192.168.50.51')")
    conn_old.execute("UPDATE tiers SET rules_json = ?, client_list = '$NAS, LAN' WHERE name = 'NAS'",
                     (json.dumps([{"action": "ACCEPT", "dest": "$NAS", "proto": "tcp",
                                   "ports": "445"}]),))
old_peer = add_peer(conn_old, "Stary", "10.0.0.9", state="revoked")
with conn_old:
    conn_old.execute("UPDATE peers SET tunnel_list = '$nas' WHERE id = ?", (old_peer,))
    conn_old.execute("INSERT INTO rules(peer_id, action, dest, proto, ports, comment) "
                     "VALUES (?, 'ACCEPT', '$NAS', 'tcp', '445', '')", (old_peer,))
changes = db.normalize_legacy(conn_old)
tier_old = conn_old.execute("SELECT rules_json, client_list FROM tiers WHERE name = 'NAS'").fetchone()
peer_old = conn_old.execute("SELECT state, privkey, tunnel_list FROM peers WHERE id = ?",
                            (old_peer,)).fetchone()
check("migracja: regula tieru $NAS -> nas", json.loads(tier_old["rules_json"])[0]["dest"] == "nas")
check("migracja: lista tieru $NAS -> nas", tier_old["client_list"] == "nas, LAN", tier_old["client_list"])
check("migracja: lista peera i jego regula", peer_old["tunnel_list"] == "nas" and conn_old.execute(
    "SELECT dest FROM rules WHERE peer_id = ?", (old_peer,)).fetchone()["dest"] == "nas")
check("migracja: odciety -> zawieszony bez klucza",
      peer_old["state"] == "suspended" and peer_old["privkey"] is None)
check("migracja: brakujacy host nas z dawnego ustawienia", conn_old.execute(
    "SELECT address FROM hosts WHERE name = 'nas'").fetchone()["address"] == "192.168.50.51")
check("migracja: po niej reguly sie renderuja", render.normalize_rule(
    json.loads(tier_old["rules_json"])[0], db.render_cfg(conn_old))["dest"] == "192.168.50.51/32")
check("migracja: druga proba nic nie zmienia", db.normalize_legacy(conn_old) == [], str(changes))
rule = render.normalize_rule({"action": "ACCEPT", "dest": "ha"}, cfg)
check("host z IPv6 ma cel w obu rodzinach",
      rule["dest"] == "192.168.50.80/32" and rule["dest6"] == ["fd00:50::80/128"], str(rule))
try:
    render.normalize_rule({"action": "ACCEPT", "dest": "nieistniejacy"}, cfg)
    check("nieznana nazwa odrzucona", False)
except render.RuleError as exc:
    check("nieznana nazwa odrzucona z podpowiedzia", "katalogu" in str(exc), str(exc))

peer_id = add_peer(conn, "Marek", "10.0.0.4", "NAS", "C")
with conn:
    conn.execute("INSERT INTO rules(peer_id, position, action, dest, proto, ports) "
                 "VALUES (?, 1, 'ACCEPT', 'drukarka', 'tcp', '631')", (peer_id,))
before = render.render_rules_file(db.render_cfg(conn), model.peers_for_render(conn))
with conn:
    conn.execute("UPDATE hosts SET address = '192.168.50.71' WHERE name = 'drukarka'")
    conn.execute("UPDATE hosts SET address = '192.168.50.55' WHERE name = 'nas'")
after = render.render_rules_file(db.render_cfg(conn), model.peers_for_render(conn))
check("zmiana adresu w katalogu zmienia regule wlasna",
      "192.168.50.70" in before and "192.168.50.71/32" in after and "192.168.50.70" not in after)
check("zmiana adresu NAS przestawia tier NAS",
      "192.168.50.55/32" in after and "192.168.50.11" not in after)

# ==========================================================================
print("\n3. IPv6 - generator")
conn6, _ = fresh("v6.db")
for k, v in {"ipv6_enabled": "1", "vpn6": "fd42:42:42::/64", "server_ip6": "fd42:42:42::1",
             "lan6": "fd00:50::/64, 2a02:a31:1::/64", "nat6": "1", "dns6": "fd00:50::5"}.items():
    db.set_setting(conn6, k, v)
with conn6:
    conn6.execute("UPDATE hosts SET address6 = 'fd00:50::50' WHERE name = 'nas'")
add_peer(conn6, "Ja", "10.0.0.2", "ADMIN", "A")
add_peer(conn6, "Kasia", "10.0.0.3", "NET", "B")
add_peer(conn6, "Marek", "10.0.0.4", "NAS", "C")
assigned = model.assign_ip6(conn6)
check("adresy IPv6 przydzielone wszystkim", assigned == 3, str(assigned))
ips6 = {r["name"]: r["ip6"] for r in conn6.execute("SELECT name, ip6 FROM peers")}
check("adres IPv6 odpowiada IPv4", ips6["Marek"] == "fd42:42:42::4", str(ips6))
check("ponowny przydzial niczego nie zmienia", model.assign_ip6(conn6) == 0)

cfg6 = db.render_cfg(conn6)
peers6 = model.peers_for_render(conn6)
conf = render.render_wg_conf(cfg6, peers6, "/etc/wireguard/wg0-panel-rules.v4")
check("serwer ma oba adresy", "Address    = 10.0.0.1/24, fd42:42:42::1/64" in conf)
check("peer ma oba adresy w AllowedIPs",
      "AllowedIPs   = 10.0.0.4/32, fd42:42:42::4/128" in conf)
check("wg0.conf podpina ip6tables", "ip6tables-restore -n /etc/wireguard/wg0-panel-rules.v6" in conf)
check("NAT66 wlaczony", "ip6tables -t nat -A POSTROUTING -s fd42:42:42::/64" in conf)

rules6 = render.render_rules_file(cfg6, peers6, 6)
lines6 = [l for l in rules6.split("\n") if l.startswith("-A WG-USERS ")]
check("reguly IPv6 zaczynaja od blokady miedzy peerami", "-o wg0" in lines6[0])
check("NAS po IPv6 dostepny dla tieru NAS",
      any("-s fd42:42:42::4/128 -d fd00:50::50/128" in l and "ACCEPT" in l for l in lines6))
lan_drops = [l for l in lines6 if "-s fd42:42:42::4/128" in l and "DROP" in l]
check("blokada LAN obejmuje OBA prefiksy IPv6 - brak furtki",
      any("fd00:50::/64" in l for l in lan_drops) and any("2a02:a31:1::/64" in l for l in lan_drops),
      str(lan_drops))
nas_pos = next(i for i, l in enumerate(lines6) if "fd00:50::50/128" in l)
drop_pos = next(i for i, l in enumerate(lines6) if "-s fd42:42:42::4/128" in l and "DROP" in l)
check("wyjatek IPv6 przed blokada", nas_pos < drop_pos)
check("NET nie wchodzi do LAN po IPv6",
      any("-s fd42:42:42::3/128 -d fd00:50::/64" in l and "DROP" in l for l in lines6))

rules4 = render.render_rules_file(cfg6, peers6, 4)
check("reguly IPv4 bez adresow IPv6", "fd42" not in rules4 and "fd00" not in rules4)

marek = next(p for p in peers6 if p["name"] == "Marek")
kasia = next(p for p in peers6 if p["name"] == "Kasia")
client = render.render_client_conf(cfg6, marek, "SERWER")
check("klient NAS ma NAS w obu rodzinach",
      "AllowedIPs   = 192.168.50.11/32, fd00:50::50/128" in client, client)
client_lan = render.render_client_conf(cfg6, dict(marek, tunnel_mode="lan"), "SERWER")
check("tryb calego LAN-u ma prefiksy IPv6",
      "AllowedIPs   = 10.0.0.0/24, 192.168.50.0/24, 2a02:a31:1::/64, fd00:50::/64, "
      "fd42:42:42::/64" in client_lan, client_lan)
check("klient ma adres IPv6", "Address    = 10.0.0.4/24, fd42:42:42::4/64" in client)
client_full = render.render_client_conf(cfg6, kasia, "SERWER")
check("klient full ma ::/0", "AllowedIPs   = 0.0.0.0/0, ::/0" in client_full)
check("klient full ma DNS IPv6", "fd00:50::5" in client_full)

db.set_setting(conn6, "ipv6_enabled", "0")
cfg_off = db.render_cfg(conn6)
conf_off = render.render_wg_conf(cfg_off, model.peers_for_render(conn6),
                                 "/etc/wireguard/wg0-panel-rules.v4")
check("wylaczone IPv6 znika z wg0.conf", "fd42" not in conf_off and "ip6tables" not in conf_off)
check("wylaczone IPv6: klient bez IPv6",
      "::" not in render.render_client_conf(cfg_off, marek, "S"))
db.set_setting(conn6, "ipv6_enabled", "1")

# ==========================================================================
print("\n4. IPv6 - panel i helper licza to samo")
payload = model.build(conn6)
hcfg = H.validate_settings(payload["settings"])
hpeers = H.validate_peers(payload["peers"], hcfg)
check("wg0.conf identyczny",
      H.render_wg_conf(hcfg, hpeers, render.PLACEHOLDER_KEY, "/etc/wireguard/wg0-panel-rules.v4")
      == render.render_wg_conf(db.render_cfg(conn6), model.peers_for_render(conn6),
                               "/etc/wireguard/wg0-panel-rules.v4"))
check("reguly IPv4 identyczne",
      H.render_rules_file(hcfg, hpeers, 4)
      == render.render_rules_file(db.render_cfg(conn6), model.peers_for_render(conn6), 4))
check("reguly IPv6 identyczne",
      H.render_rules_file(hcfg, hpeers, 6)
      == render.render_rules_file(db.render_cfg(conn6), model.peers_for_render(conn6), 6))

print("\n5. IPv6 - walidacja helpera")
bad_cases = [
    ("IPv6 bez prefiksu LAN", {**payload["settings"], "lan6": ""}),
    ("adres serwera spoza podsieci", {**payload["settings"], "server_ip6": "fd99::1"}),
    ("IPv4 w polu vpn6", {**payload["settings"], "vpn6": "10.9.0.0/24"}),
]
for label, settings in bad_cases:
    try:
        H.validate_settings(settings)
        check(f"odrzuca: {label}", False)
    except H.Rejected:
        check(f"odrzuca: {label}", True)

for label, mutate in [
    ("IPv6 peera spoza podsieci", lambda p: p[0].update(ip6="fd99::4")),
    ("zdublowany IPv6", lambda p: p[1].update(ip6=p[0]["ip6"])),
    ("IPv4 w celu IPv6", lambda p: p[2]["rules"].append(
        {"action": "ACCEPT", "dest": "", "dest6": ["192.168.1.1"]})),
    ("regula bez celu", lambda p: p[2]["rules"].append(
        {"action": "ACCEPT", "dest": "", "dest6": []})),
]:
    broken = json.loads(json.dumps(payload["peers"]))
    mutate(broken)
    try:
        H.validate_peers(broken, hcfg)
        check(f"odrzuca: {label}", False)
    except H.Rejected:
        check(f"odrzuca: {label}", True)

print("\n6. IPv6 - symulator")
cfg6 = db.render_cfg(conn6)
peers6 = model.peers_for_render(conn6)
cases = [
    ("NAS po IPv6 na 445", "fd42:42:42::4", "fd00:50::50", 445, "ACCEPT"),
    ("inny host LAN po IPv6", "fd42:42:42::4", "fd00:50::99", 80, "DROP"),
    ("drugi prefiks LAN tez blokowany", "fd42:42:42::4", "2a02:a31:1::10", 80, "DROP"),
    ("internet po IPv6 dla NAS tieru przechodzi", "fd42:42:42::4", "2001:4860::8888", 443, "ACCEPT"),
    ("NET nie wchodzi do LAN po IPv6", "fd42:42:42::3", "fd00:50::50", 445, "DROP"),
    ("admin ma LAN po IPv6", "fd42:42:42::2", "fd00:50::99", 22, "ACCEPT"),
    ("peery nie widza sie po IPv6", "fd42:42:42::4", "fd42:42:42::2", 22, "DROP"),
    ("SSH na serwer po IPv6 tylko admin", "fd42:42:42::4", "fd42:42:42::1", 22, "DROP"),
]
for label, src, dst, port, want in cases:
    result = simulate.simulate(cfg6, peers6, src, dst, port, "tcp")
    check(label, result["verdict"] == want, f'{result["verdict"]}: {result["reason"]}')

# ==========================================================================
print("\n7. Ruch")
conn_t, _ = fresh("traffic.db")
add_peer(conn_t, "Marek", "10.0.0.4", "NAS", "C")
pubkey = key("C")
hour = 3600
start = int(time.time()) // hour * hour - 7 * 24 * hour
rx = tx = 0
samples = []
for h in range(7 * 24):
    for m in range(0, 60, 15):
        rx += 2_000_000
        tx += 10_000_000
        samples.append((start + h * hour + m * 60, pubkey, rx, tx))
with conn_t:
    conn_t.executemany("INSERT INTO traffic(ts, pubkey, rx, tx) VALUES (?, ?, ?, ?)", samples)
series = traffic.hourly(conn_t, pubkey, 7, now=start + 7 * 24 * hour - 60)
full_hours = [p for p in series[1:-1] if p["tx"]]
check("szereg ma 168 godzin", len(series) == 168, str(len(series)))
check("godzina ma poprawny przyrost",
      all(p["tx"] == 40_000_000 for p in full_hours[1:]), str(full_hours[1:3]))

reset = [(start + 10, pubkey, 100, 500), (start + 20, pubkey, 300, 900),
         (start + 30, pubkey, 50, 60)]  # restart interfejsu: licznik spada
# ten sam przypadek przez hourly() - to ona naprawde liczy przyrosty
# (wczesniej test sprawdzal osobna funkcje _deltas, ktorej panel juz nie uzywal)
reset_key = "RESET" + "0" * 39
hour0 = start + 3 * hour
with conn_t:
    conn_t.executemany("INSERT INTO traffic(ts, pubkey, rx, tx) VALUES (?, ?, ?, ?)",
                       [(hour0 + 10, reset_key, 100, 500), (hour0 + 20, reset_key, 300, 900),
                        (hour0 + 30, reset_key, 50, 60)])   # restart interfejsu: licznik spada
bucket = next(p for p in traffic.hourly(conn_t, reset_key, 7, now=start + 7 * 24 * hour - 60)
              if p["ts"] == hour0)
check("restart licznika liczony jako nowa wartosc", (bucket["rx"], bucket["tx"]) == (250, 460),
      str(bucket))

conn_a, _ = fresh("anomaly.db")
add_peer(conn_a, "Marek", "10.0.0.4", "NAS", "C")
now_a = int(time.time()) // hour * hour + 600
base_ts = now_a - 7 * 24 * hour
rows, total = [], 0
for h in range(7 * 24 - 1):
    total += 5_000_000  # ~5 MB na godzine przez tydzien
    rows.append((base_ts + h * hour, pubkey, total, 0))
total += 3_000_000_000  # skok: 3 GB w ostatniej pelnej godzinie
rows.append((now_a - hour - 60, pubkey, total, 0))
rows.append((now_a - 30, pubkey, total, 0))
with conn_a:
    conn_a.executemany("INSERT INTO traffic(ts, pubkey, rx, tx) VALUES (?, ?, ?, ?)", rows)
found = traffic.detect_anomalies(conn_a, now=now_a)
check("anomalia wykryta przy skoku ruchu", found == ["Marek"], str(found))
check("anomalia trafia do alertow",
      any(r["kind"] == "traffic_anomaly" for r in db.alerts_tail(conn_a)))

conn_b, _ = fresh("normal.db")
add_peer(conn_b, "Marek", "10.0.0.4", "NAS", "C")
with conn_b:
    conn_b.executemany("INSERT INTO traffic(ts, pubkey, rx, tx) VALUES (?, ?, ?, ?)",
                       [(base_ts + h * hour, pubkey, (h + 1) * 5_000_000, 0)
                        for h in range(7 * 24)])
check("rowny ruch nie daje falszywego alarmu",
      traffic.detect_anomalies(conn_b, now=now_a) == [])

with conn_t:
    conn_t.execute("INSERT INTO traffic(ts, pubkey, rx, tx) VALUES (1000, ?, 1, 1)", (pubkey,))
traffic.prune(conn_t)
check("stare probki sa kasowane",
      conn_t.execute("SELECT COUNT(*) c FROM traffic WHERE ts = 1000").fetchone()["c"] == 0)

from panel import ui  # noqa: E402
svg = ui.traffic_chart(series)
check("wykres to poprawny SVG z opisem godzin", "<svg" in svg and "<title>" in svg)

# ==========================================================================
print("\n8. Sumy kontrolne kopii - lokalnie")
conn_k, _ = fresh("bak.db")
add_peer(conn_k, "Ja", "10.0.0.2", "ADMIN", "A")
bdir = os.path.join(TMP, "kopie")
db.set_setting(conn_k, "backup_dir", bdir)
db.set_setting(conn_k, "backup_passphrase", "haslo")
record = export.run(conn_k, actor="test")
check("kopia ma sume SHA-256", len(record["sha256"]) == 64)
check("kopia zweryfikowana od razu po zapisie", record.get("verify", {}).get("ok"),
      str(record.get("verify")))
check("weryfikacja otwiera zaszyfrowana kopie", "otwiera sie" in record["verify"]["detail"])

sidecar = record["path"] + ".sha256"
check("plik .sha256 lezy obok kopii", os.path.exists(sidecar))
result = subprocess.run(["sha256sum", "-c", os.path.basename(sidecar)], cwd=bdir,
                        capture_output=True, text=True)
check("sha256sum -c potwierdza kopie bez panelu", result.returncode == 0,
      result.stdout + result.stderr)

with open(record["path"], "r+b") as handle:  # ktos grzebie w pliku
    handle.seek(50)
    byte = handle.read(1)
    handle.seek(50)
    handle.write(bytes([byte[0] ^ 0xFF]))
check_result = export.verify(conn_k, record["id"])
check("zmieniony plik wykryty", not check_result["ok"] and "nie zgadza" in check_result["detail"],
      check_result["detail"])
row = conn_k.execute("SELECT verify_ok FROM backups WHERE id = ?", (record["id"],)).fetchone()
check("wynik zapisany w historii", row["verify_ok"] == 0)
check("niezgodna kopia daje alarm",
      any(r["kind"] == "backup" and r["severity"] == "alarm" for r in db.alerts_tail(conn_k)))

print("\n9. Sumy kontrolne kopii - NAS (atrapa smbclient)")
share = os.path.join(TMP, "udzial")
bindir = os.path.join(TMP, "bin")
os.makedirs(share, exist_ok=True)
os.makedirs(bindir, exist_ok=True)
with open(os.path.join(bindir, "smbclient"), "w") as handle:
    handle.write(f'''#!/usr/bin/env python3
import os, shlex, shutil, sys
share = {share!r}
args = sys.argv[1:]
commands = args[args.index("-c") + 1]
cwd = share
for cmd in [c.strip() for c in commands.split(";") if c.strip()]:
    parts = shlex.split(cmd)
    if parts[0] == "mkdir":
        os.makedirs(os.path.join(share, parts[1]), exist_ok=True)
    elif parts[0] == "cd":
        cwd = os.path.join(share, parts[1])
        if not os.path.isdir(cwd):
            print("NT_STATUS_OBJECT_NAME_NOT_FOUND", file=sys.stderr); sys.exit(1)
    elif parts[0] == "put":
        shutil.copy(parts[1], os.path.join(cwd, parts[2]))
    elif parts[0] == "ls":
        for name in sorted(os.listdir(cwd)):
            print(f"  {{name}}    A    {{os.path.getsize(os.path.join(cwd, name))}}  Mon Sep 21 10:00:00 2026")
    elif parts[0] == "del":
        victim = os.path.join(cwd, parts[1])
        if not os.path.exists(victim):
            print("NT_STATUS_NO_SUCH_FILE", file=sys.stderr); sys.exit(1)
        os.remove(victim)
    elif parts[0] == "get":
        src = os.path.join(cwd, parts[1])
        if not os.path.exists(src):
            print("NT_STATUS_OBJECT_NAME_NOT_FOUND", file=sys.stderr); sys.exit(1)
        shutil.copy(src, parts[2])
''')
os.chmod(os.path.join(bindir, "smbclient"), 0o755)
os.environ["PATH"] = bindir + os.pathsep + os.environ["PATH"]

db.set_setting(conn_k, "backup_target", "smb")
db.set_setting(conn_k, "smb_host", "192.168.50.11")
db.set_setting(conn_k, "smb_share", "backup")
db.set_setting(conn_k, "smb_path", "wg-panel")
db.set_setting(conn_k, "smb_user", "backup-wg")
db.set_setting(conn_k, "smb_pass", "tajne")
record = export.run(conn_k, actor="test")
check("kopia trafila na udzial", record["ok"] == 1, record["detail"])
staging_k = export.staging_dir(db.settings(conn_k))
check("tylko NAS: na Pi nic nie zostaje (ani kopia, ani .sha256)",
      not any(n.startswith(os.path.basename(record["path"])) for n in os.listdir(staging_k)),
      str(os.listdir(staging_k)))
db.set_setting(conn_k, "backup_target", "local")
once = export.run(conn_k, actor="test", target="smb")
check("jednorazowa kopia na NAS przy celu 'katalog na dysku'",
      once["ok"] == 1 and once["target"] == "smb", once["detail"])
check("cel kopii automatycznych bez zmian", db.settings(conn_k)["backup_target"] == "local")
check("dwie kopie 'tylko NAS' w tej samej sekundzie nie nadpisuja sie",
      os.path.basename(once["path"]) != os.path.basename(record["path"]), once["path"])
db.set_setting(conn_k, "smb_pass", "")
before = set(os.listdir(staging_k))
missing = export.run(conn_k, actor="test", target="smb")
check("brak hasla NAS - czytelny komunikat", missing["ok"] == 0
      and "brak uzytkownika albo hasla" in missing["detail"], missing["detail"])
check("nieudana wysylka 'tylko NAS': na Pi tez nic nie zostaje",
      set(os.listdir(staging_k)) == before and missing["path"] == "", str(set(os.listdir(staging_k)) - before))
db.set_setting(conn_k, "smb_pass", "tajne")
db.set_setting(conn_k, "smb_host", "")
no_host = export.run(conn_k, actor="test", target="smb")
check("brak adresu NAS - czytelny komunikat", "brak adresu albo udzialu" in no_host["detail"],
      no_host["detail"])
db.set_setting(conn_k, "smb_host", "192.168.50.11")
db.set_setting(conn_k, "backup_target", "smb")
remote = os.path.join(share, "wg-panel", os.path.basename(record["path"]))
check("plik kopii na NAS", os.path.exists(remote))
check("plik .sha256 na NAS", os.path.exists(remote + ".sha256"))
check("weryfikacja pobrala plik z NAS i sie zgadza", record["verify"]["ok"],
      str(record["verify"]))

print("\n9a. Odtwarzanie z historii i z NAS")
good_id = record["id"]
with conn_k:
    conn_k.execute("UPDATE peers SET name = 'Zmieniony' WHERE ip = '10.0.0.2'")
summary = export.restore_from(conn_k, f"id:{good_id}", "haslo", False, "test")
check("odtworzenie kopii pobranej z NAS",
      conn_k.execute("SELECT name FROM peers WHERE ip='10.0.0.2'").fetchone()["name"] == "Ja",
      str(summary))
safety_rows = conn_k.execute(
    "SELECT * FROM backups WHERE detail = 'kopia stanu przed odtworzeniem'").fetchall()
check("przed odtworzeniem zapisany obecny stan", len(safety_rows) == 1)
back = export.restore_from(conn_k, f"id:{safety_rows[0]['id']}", "haslo", False, "test")
check("odtworzenie da sie cofnac kopia sprzed odtworzenia",
      conn_k.execute("SELECT name FROM peers WHERE ip='10.0.0.2'").fetchone()["name"]
      == "Zmieniony", str(back))
try:
    export.restore_from(conn_k, f"id:{good_id}", "zle-haslo", False, "test")
    check("zle haslo odrzucone", False)
except Exception:
    check("zle haslo odrzucone bez zmian w bazie",
          conn_k.execute("SELECT name FROM peers WHERE ip='10.0.0.2'").fetchone()["name"]
          == "Zmieniony")
check("lista plikow nie zawiera plikow .sha256",
      all(not f.endswith(".sha256") for f in export.local_files(db.settings(conn_k))))

with open(remote, "ab") as handle:  # ktos podmienia plik na NAS-ie
    handle.write(b"x")
try:
    export.restore_from(conn_k, f"id:{good_id}", "haslo", False, "test")
    check("podmieniona kopia nie zostaje odtworzona", False)
except ValueError as exc:
    check("podmieniona kopia nie zostaje odtworzona", "suma kontrolna" in str(exc), str(exc))

# "NAS i Pi": dwie kopie; odtworzenie bierze cala kopie z Pi
both = export.run(conn_k, actor="test", target="both")
both_row = conn_k.execute("SELECT * FROM backups WHERE id = ?", (both["id"],)).fetchone()
both_remote = os.path.join(share, "wg-panel", os.path.basename(both["path"]))
working = export.local_copy(db.settings(conn_k), both_row)
check("NAS i Pi: plik w obu miejscach", both["ok"] and os.path.exists(both_remote) and bool(working),
      both["detail"])
with open(both_remote, "ab") as handle:
    handle.write(b"x")
summary = export.restore_from(conn_k, f"id:{both['id']}", "haslo", False, "test")
check("podmiana na NAS-ie nie szkodzi, gdy kopia na Pi jest cala (z niej odtwarza)",
      summary["peers"] >= 1, str(summary))
with conn_k:
    conn_k.execute("UPDATE backups SET verify_ok = 1 WHERE id = ?", (both["id"],))
again = export.run(conn_k, actor="test", target="smb")      # ustawione "tylko NAS"
check("przy celu 'tylko NAS' sprawdzone kopie z Pi znikaja (wpis zostaje)",
      again["ok"] and not os.path.exists(working)
      and conn_k.execute("SELECT 1 FROM backups WHERE id = ?", (both["id"],)).fetchone())
check_result = export.verify(conn_k, record["id"])
check("podmiana pliku na NAS wykryta", not check_result["ok"], check_result["detail"])
os.remove(remote)
check_result = export.verify(conn_k, record["id"])
check("brak pliku na NAS wykryty", not check_result["ok"], check_result["detail"])

# ==========================================================================
print("\n9b. Usuwanie wybranych kopii (karta i NAS)")
db.set_setting(conn_k, "backup_target", "smb")
db.set_setting(conn_k, "smb_pass", "tajne")
good = export.run(conn_k, actor="test", target="both")
good_name = os.path.basename(good["path"])
db.set_setting(conn_k, "smb_pass", "")
bad = export.run(conn_k, actor="test")               # nieudana: na Pi nic nie zostaje
db.set_setting(conn_k, "smb_pass", "tajne")
staging_dir = db.settings(conn_k).get("backup_dir")
check("przed usunieciem: kopia na NAS i jej robocza kopia na karcie",
      os.path.exists(os.path.join(share, "wg-panel", good_name))
      and os.path.exists(os.path.join(staging_dir, good_name)))
summary = export.delete_backups(conn_k, [good["id"], bad["id"]])
check("usuniete z NAS: kopia i .sha256",
      not os.path.exists(os.path.join(share, "wg-panel", good_name))
      and not os.path.exists(os.path.join(share, "wg-panel", good_name + ".sha256"))
      and summary["remote"] == 2, str(summary))
check("usuniete z karty: kopie robocze", not os.path.exists(os.path.join(staging_dir, good_name))
      and not os.path.exists(bad["path"]), str(summary))
check("wpisy zniknely z historii", summary["deleted"] == 2 and not conn_k.execute(
    "SELECT 1 FROM backups WHERE id IN (?, ?)", (good["id"], bad["id"])).fetchone())
with conn_k:
    foreign = conn_k.execute(
        "INSERT INTO backups(ts, target, path, bytes, encrypted, ok, detail, sha256) "
        "VALUES (1, 'smb', '//10.9.9.9/inny/wg-panel-20260101-000000.json', 1, 0, 1, '', 'x')").lastrowid
summary = export.delete_backups(conn_k, [foreign])
check("kopia spod innego adresu NAS: wpis zostaje, problem zgloszony",
      summary["deleted"] == 0 and summary["problems"]
      and conn_k.execute("SELECT 1 FROM backups WHERE id = ?", (foreign,)).fetchone(), str(summary))

print("\n9c. Kopia sprzed odtworzenia ma widoczna etykiete")
from panel import ui as _ui  # noqa: E402
snap = export.snapshot_local(conn_k, "test")
twin = export.snapshot_local(conn_k, "test")       # ta sama sekunda: osobny plik, nie nadpisanie
check("dwie migawki w tej samej sekundzie: dwa pliki", twin != snap
      and os.path.exists(os.path.join(export.staging_dir(db.settings(conn_k)), twin)), twin)
plain = export.run(conn_k, actor="test", target="local")
page_html = _ui.backups_page({"username": "t", "csrf": "c"}, db.settings(conn_k),
                             db.backups_list(conn_k), export.local_files(db.settings(conn_k)))
snap_row = next(r for r in page_html.split("<tr") if snap in r)
plain_row = next(r for r in page_html.split("<tr") if os.path.basename(plain["path"]) in r)
check("kopia sprzed odtworzenia: etykieta w tabeli", "sprzed odtworzenia" in snap_row)
check("zwykla kopia bez etykiety", "sprzed odtworzenia" not in plain_row)
check("jedna lista - kazda kopia raz", page_html.count(snap) == 1, str(page_html.count(snap)))
check("lista od najnowszej (wedlug czasu kopii)",
      [r["ts"] for r in db.backups_list(conn_k)] == sorted((r["ts"] for r in db.backups_list(conn_k)),
                                                          reverse=True))

print("\n10. Integralnosc (atrapy wg i iptables)")
wgdir = os.path.join(TMP, "wireguard")
os.makedirs(wgdir, exist_ok=True)
H.WG_DIR = wgdir
H.BACKUP_DIR = os.path.join(wgdir, "wg-panel-backups")
H.SYSCTL_DIR = os.path.join(TMP, "sysctl.d")
with open(os.path.join(wgdir, "server_private.key"), "w") as handle:
    handle.write(key("S") + "\n")

state = {"peers": [], "chains": {}, "calls": []}


def fake_run(args, **kwargs):
    state["calls"].append(args)
    tool = args[0]
    if tool == "wg" and args[1:3] == ["show", "wg0"] and args[3:] == ["peers"]:
        return subprocess.CompletedProcess(args, 0, "\n".join(state["peers"]), "")
    if tool in ("iptables", "ip6tables") and args[1] == "-S":
        chain = args[2]
        lines = state["chains"].get((tool, chain))
        if lines is None:
            return subprocess.CompletedProcess(args, 1, "", "no chain")
        return subprocess.CompletedProcess(args, 0, "\n".join(lines), "")
    if tool in ("iptables", "ip6tables") and "-C" in args:
        return subprocess.CompletedProcess(args, 1, "", "")
    if tool == "wg-quick":
        return subprocess.CompletedProcess(args, 0, "[Interface]\n", "")
    return subprocess.CompletedProcess(args, 0, "", "")


H.run = fake_run
H.shutil.which = lambda name: "/usr/sbin/" + name

cfg_h = H.validate_settings(payload["settings"])
result = H.op_apply(dict(payload, confirm_seconds=0))
fp = result["fingerprint"]
check("apply zwraca odcisk stanu", fp.get("conf_sha256") and fp.get("rules6_sha256"))

sysctl_calls = [c for c in state["calls"] if c[0] == "sysctl"]
order = [c[2] for c in sysctl_calls]
check("accept_ra=2 ustawiane PRZED forwarding=1 (inaczej Pi traci trase IPv6)",
      order.index("net.ipv6.conf.eth0.accept_ra=2") < order.index("net.ipv6.conf.all.forwarding=1"),
      str(order))
check("ip6tables-restore wywolany",
      any(c[0] == "ip6tables-restore" for c in state["calls"]))

# stan zgodny: jadro wyglada tak, jak panel zastosowal
def healthy():
    state["peers"] = list(fp["pubkeys"])
    for tool, counts_key in (("iptables", "rules4_counts"), ("ip6tables", "rules6_counts")):
        for chain, count in fp[counts_key].items():
            state["chains"][(tool, chain)] = [f"-N {chain}"] + [f"-A {chain} x"] * count
        state["chains"][(tool, "FORWARD")] = ["-P FORWARD DROP",
                                              "-A FORWARD -i wg0 -j WG-USERS", "-A FORWARD y"]


healthy()
check("stan zgodny - brak rozbieznosci", H.detect_drift(cfg_h, fp) == [],
      str(H.detect_drift(cfg_h, fp)))

conf_file = os.path.join(wgdir, "wg0.conf")
with open(conf_file, "a") as handle:
    handle.write("\n# dopisane recznie\n")
kinds = [f["kind"] for f in H.detect_drift(cfg_h, fp)]
check("wykrywa edycje wg0.conf", "conf" in kinds, str(kinds))
H.op_apply(dict(payload, confirm_seconds=0))
fp = H.fingerprint(cfg_h)
healthy()

state["peers"].append(key("Z"))
findings = H.detect_drift(cfg_h, fp)
check("wykrywa peera dodanego przez wg set",
      any(f["kind"] == "peers" and "spoza panelu" in f["message"] for f in findings), str(findings))
healthy()

state["chains"][("iptables", "WG-USERS")].append("-A WG-USERS -s 10.0.0.4/32 -j ACCEPT")
findings = H.detect_drift(cfg_h, fp)
check("wykrywa regule dopisana do lancucha panelu",
      any(f["kind"] == "chain" for f in findings), str(findings))
healthy()

state["chains"][("iptables", "FORWARD")].insert(1, "-A FORWARD -j ACCEPT")
findings = H.detect_drift(cfg_h, fp)
check("wykrywa ACCEPT wstawiony przed panelem",
      any(f["kind"] == "hook" for f in findings), str(findings))
healthy()

os.chmod(conf_file, 0o644)
findings = H.detect_drift(cfg_h, fp)
check("wykrywa klucze czytelne dla innych kont",
      any(f["kind"] == "perm" for f in findings), str(findings))
os.chmod(conf_file, 0o600)

rules6_file = os.path.join(wgdir, "wg0-panel-rules.v6")
with open(rules6_file, "a") as handle:
    handle.write("-A WG-USERS -j ACCEPT\n")
findings = H.detect_drift(cfg_h, fp)
check("wykrywa edycje regul IPv6", any(f["kind"] == "rules6" for f in findings), str(findings))

payload_off = model.build(conn6)
payload_off["settings"]["ipv6_enabled"] = "0"
state["calls"].clear()
H.op_apply(dict(payload_off, confirm_seconds=0))
check("wylaczenie IPv6 kasuje plik regul v6", not os.path.exists(rules6_file))
check("wylaczenie IPv6 usuwa lancuchy ip6tables",
      any(c[:2] == ["ip6tables", "-X"] for c in state["calls"]))
check("wylaczenie IPv6 zdejmuje trwale przekazywanie",
      not os.path.exists(os.path.join(H.SYSCTL_DIR, H.SYSCTL_FILE)))

# ==========================================================================
print("\n11. QR bez qrencode")
try:
    import cv2
    import numpy as np
    have_cv = True
except ImportError:
    have_cv = False

conf_text = render.render_client_conf(cfg6, dict(marek, privkey=key("K")), key("S"))
qr = qrcode.encode(conf_text)
check("konfiguracja miesci sie w kodzie", qr.version <= 20, str(qr.version))
if have_cv:
    # dwa niezalezne dekodery: klasyczny OpenCV ma slabosc na niektore uklady modulow,
    # a poprawny kod musi dac sie odczytac przynajmniej jednym z nich
    detectors = [cv2.QRCodeDetector()]
    if hasattr(cv2, "QRCodeDetectorAruco"):
        detectors.append(cv2.QRCodeDetectorAruco())
    decoded = ""
    for scale, detector in [(s, d) for d in detectors for s in (4, 6, 8, 10)]:
        size = qr.size + 8
        img = np.full((size * scale, size * scale), 255, np.uint8)
        for y in range(qr.size):
            for x in range(qr.size):
                if qr.modules[y][x]:
                    img[(y + 4) * scale:(y + 5) * scale, (x + 4) * scale:(x + 5) * scale] = 0
        decoded, _, _ = detector.detectAndDecode(img)
        if decoded == conf_text:
            break
    check("kod z konfiguracja IPv6 da sie odczytac", decoded == conf_text)
else:
    print("  [--  ] brak OpenCV - pomijam odczyt kodu")
check("SVG bez zaleznosci zewnetrznych", qrcode.svg("x").startswith("<svg"))
check("tryb terminalowy dziala", "\u2588" in qrcode.to_terminal(qrcode.encode("x")))

# ==========================================================================
print("\n12. Eksport przenosi nowe dane")
data = export.build(conn6)
check("eksport zawiera katalog hostow", len(data["hosts"]) >= 1)
conn_r, _ = fresh("restore.db")
export.restore(conn_r, data)
restored = {r["name"]: r["ip6"] for r in conn_r.execute("SELECT name, ip6 FROM peers")}
check("odtworzenie zachowuje adresy IPv6", restored.get("Marek") == "fd42:42:42::4", str(restored))
check("odtworzenie zachowuje IPv6 katalogu",
      conn_r.execute("SELECT address6 FROM hosts WHERE name='nas'").fetchone()["address6"]
      == "fd00:50::50")

# ==========================================================================
print("\n13. wgctl")
conn_c, path_c = fresh("cli.db")
env = dict(os.environ, WGPANEL_DB=path_c, PYTHONPATH=ROOT)


def wgctl(*args):
    return subprocess.run([sys.executable, os.path.join(ROOT, "wgctl"), *args],
                          env=env, capture_output=True, text=True, timeout=60)


out = wgctl("host", "add", "drukarka", "192.168.50.70", "--description", "biuro")
check("wgctl host add", "dodano" in out.stdout, out.stdout + out.stderr)
out = wgctl("host", "list")
check("wgctl host list", "drukarka" in out.stdout and "nas" in out.stdout, out.stdout)
wgctl("add", "Marek", "--tier", "NAS")
out = wgctl("rule", "add", "Marek", "--accept", "drukarka", "--proto", "tcp", "--port", "631")
check("wgctl przyjmuje nazwe hosta w regule", "dodano" in out.stdout, out.stdout + out.stderr)
out = wgctl("check", "Marek", "192.168.50.70", "631")
check("wgctl check po nazwie hosta", "ACCEPT" in out.stdout, out.stdout)
out = wgctl("host", "add", "drukarka", "192.168.50.71")
check("wgctl host add zmienia istniejacy", "zmieniono" in out.stdout)
out = wgctl("check", "Marek", "192.168.50.71", "631")
check("po zmianie adresu regula idzie za hostem", "ACCEPT" in out.stdout, out.stdout)
out = wgctl("traffic")
check("wgctl traffic dziala bez danych", out.returncode == 0, out.stderr)
out = wgctl("integrity")
check("wgctl integrity bez wzorca mowi dlaczego", "wzorca" in out.stderr, out.stderr)
from panel import auth as _auth  # noqa: E402
_auth.create_user(conn_c, "admin", "bardzo-dlugie-haslo", "JBSWY3DPEHPK3PXP")
out = wgctl("totp", "off", "admin")
check("wgctl totp off wylacza kod awaryjnie", out.returncode == 0 and not
      conn_c.execute("SELECT totp_secret FROM users").fetchone()["totp_secret"], out.stderr)
# import obecnego wg0.conf przez standardowe wejscie (plik root-only)
conf_v2 = "\n".join(["[Interface]", "Address = 10.0.0.1/24", "ListenPort = 41194",
                      f"PrivateKey = {key('S')}", "PostUp = iptables -A FORWARD -i wg0 -j ACCEPT", ""]
                     + sum([["[Peer]", f"# User {i}", f"PublicKey = {key(chr(80 + i))}",
                             f"PresharedKey = {key('Q')}", f"AllowedIPs = 10.0.0.{i + 1}/32", ""]
                            for i in range(1, 8)], []))
conn_i, path_i = fresh("import.db")
result = subprocess.run([sys.executable, "-m", "panel.import_conf", "-", "--tier", "NAS"],
                        input=conf_v2, env=dict(env, WGPANEL_DB=path_i), cwd=ROOT,
                        capture_output=True, text=True, timeout=60)
# komentarze sekcji przed [Peer] nie moga przesuwac nazw (blad z prawdziwego wdrozenia)
conf_sekcje = ("[Interface]\nAddress = 10.0.0.1/24\nListenPort = 41194\n"
               "# --- OGRANICZENIA PER USER ---\n"
               "PostUp = iptables -I FORWARD -s 10.0.0.3 -j DROP\n"
               "# --- UZYTKOWNICY ---\n\n"
               f"[Peer]\n# User 1 - admin\nPublicKey = {key('X')}\nAllowedIPs = 10.0.0.2/32\n\n"
               f"[Peer]\n# User 2 - bez LAN\nPublicKey = {key('Y')}\nAllowedIPs = 10.0.0.3/32\n\n"
               f"[Peer]\nPublicKey = {key('Z')}\nAllowedIPs = 10.0.0.4/32\n")
conn_sek, path_sek = fresh("sekcje.db")
subprocess.run([sys.executable, "-m", "panel.import_conf", "-", "--tier", "NAS"],
               input=conf_sekcje, env=dict(env, WGPANEL_DB=path_sek), cwd=ROOT,
               capture_output=True, text=True, timeout=60)
imported = {r["ip"]: r["name"] for r in conn_sek.execute("SELECT ip, name FROM peers")}
check("nazwa z komentarza trafia do wlasciwej osoby",
      imported.get("10.0.0.2") == "User 1 - admin"
      and imported.get("10.0.0.3") == "User 2 - bez LAN", str(imported))
check("komentarz sekcji nie staje sie nazwa peera",
      not any("UZYTKOWNIC" in n.upper() for n in imported.values()), str(imported))
check("peer bez komentarza dostaje nazwe zastepcza",
      imported.get("10.0.0.4", "").startswith("import-"), str(imported))

check("import czyta wg0.conf ze standardowego wejscia",
      result.returncode == 0 and conn_i.execute("SELECT COUNT(*) c FROM peers").fetchone()["c"] == 7,
      result.stdout + result.stderr)
env_i = dict(env, WGPANEL_DB=path_i)
out = subprocess.run([sys.executable, os.path.join(ROOT, "wgctl"), "set", "10.0.0.2",
                      "--tier", "ADMIN", "--rename", "Ja"], env=env_i,
                     capture_output=True, text=True, timeout=60)
row = conn_i.execute("SELECT p.name, t.name AS tier FROM peers p JOIN tiers t ON t.id=p.tier_id "
                     "WHERE p.ip='10.0.0.2'").fetchone()
check("wgctl set zmienia tier i nazwe po adresie IP",
      row["name"] == "Ja" and row["tier"] == "ADMIN", out.stdout + out.stderr)

db.set_setting(conn_c, "backup_dir", os.path.join(TMP, "kopie-cli"))
db.set_setting(conn_c, "backup_passphrase", "")
out = wgctl("backup")
out = wgctl("restore")
check("wgctl restore bez argumentu pokazuje liste", "#" in out.stdout, out.stdout + out.stderr)
backup_id = conn_c.execute("SELECT MAX(id) m FROM backups WHERE ok=1").fetchone()["m"]
out = wgctl("restore", str(backup_id), "--yes", "--passphrase", "")
check("wgctl restore odtwarza kopie", "odtworzono" in out.stdout, out.stdout + out.stderr)

out = wgctl("qr", "Marek")
check("wgctl qr bez qrencode", out.returncode == 0 and "\u2588" in out.stdout, out.stderr)


# ==========================================================================
print("\n14. Co idzie przez VPN (split / caly ruch)")
conn_s, path_s = fresh("split.db")
with conn_s:
    conn_s.execute("INSERT INTO hosts(name, address) VALUES ('drukarka', '192.168.50.70')")
ja = add_peer(conn_s, "Ja", "10.0.0.2", "ADMIN", "A")
u2 = add_peer(conn_s, "User2", "10.0.0.3", "NET", "B")
marek = add_peer(conn_s, "Marek", "10.0.0.4", "NAS", "C")


def route_of(peer_id):
    return model.client_conf(conn_s, db.peer(conn_s, peer_id))[1]


check("Ty - caly ruch", route_of(ja)["allowed"] == ["0.0.0.0/0"])
check("User 2 - caly ruch", route_of(u2)["allowed"] == ["0.0.0.0/0"])
check("NAS - tylko NAS", route_of(marek)["allowed"] == ["192.168.50.11/32"],
      str(route_of(marek)))
conf_m = model.client_conf(conn_s, db.peer(conn_s, marek))[0]
check("plik NAS ma AllowedIPs tylko do NAS i brak DNS",
      "AllowedIPs   = 192.168.50.11/32" in conf_m and "DNS" not in conf_m, conf_m)
conf_j = model.client_conf(conn_s, db.peer(conn_s, ja))[0]
check("plik z calym ruchem ma DNS", "DNS        = 9.9.9.9" in conf_j)

model.mark_issued(conn_s, marek, route_of(marek)["allowed"])
check("po wydaniu plik aktualny",
      model.issued_state(db.peer(conn_s, marek), route_of(marek))["state"] == "ok")
with conn_s:
    conn_s.execute("INSERT INTO rules(peer_id, position, action, dest, proto, ports) "
                   "VALUES (?, 1, 'ACCEPT', 'drukarka', 'tcp', '631')", (marek,))
check("regula wlasna dopisuje drukarke do pliku",
      route_of(marek)["allowed"] == ["192.168.50.11/32", "192.168.50.70/32"],
      str(route_of(marek)["allowed"]))
state = model.issued_state(db.peer(conn_s, marek), route_of(marek))
check("panel wie, ze plik u osoby jest nieaktualny",
      state["state"] == "missing" and state["missing"] == ["192.168.50.70/32"], str(state))

with conn_s:
    conn_s.execute("UPDATE peers SET issued_allowed = '10.0.0.0/24, 192.168.50.0/24' "
                   "WHERE id = ?", (marek,))
check("plik z v2 (caly LAN) obejmuje nowy zakres - dziala",
      model.issued_state(db.peer(conn_s, marek), route_of(marek))["state"] == "wider")

with conn_s:
    conn_s.execute("UPDATE peers SET tunnel_mode = 'custom', tunnel_list = 'nas, pi' "
                   "WHERE id = ?", (marek,))
# pi (.10) i nas (.11) sasiaduja - generator laczy je w jeden zakres /31 (te same dwa adresy)
check("wlasna lista z nazw katalogu",
      route_of(marek)["allowed"] == ["192.168.50.10/31"],
      str(route_of(marek)["allowed"]))
check("wlasna lista oznaczona jako ustawiona u osoby", route_of(marek)["own"])
with conn_s:
    conn_s.execute("UPDATE peers SET tunnel_mode = 'lan' WHERE id = ?", (marek,))
check("tryb calej sieci domowej jak w v2",
      route_of(marek)["allowed"] == ["10.0.0.0/24", "192.168.50.0/24"])
with conn_s:
    conn_s.execute("UPDATE peers SET tunnel_mode = '' WHERE id = ?", (marek,))
    conn_s.execute("UPDATE tiers SET client_mode = 'full' WHERE name = 'NAS'")
check("zmiana tieru na caly ruch przestawia osobe", route_of(marek)["allowed"] == ["0.0.0.0/0"])
with conn_s:
    conn_s.execute("UPDATE tiers SET client_mode = 'auto' WHERE name = 'NAS'")

check("Pi jest w katalogu hostow", any(h["name"] == "pi" and h["address"] == "192.168.50.10"
                                        for h in db.hosts(conn_s)))
cfg_s = db.render_cfg(conn_s)
peers_s = model.peers_for_render(conn_s)
for label, src, port, want in [("NAS nie wejdzie SSH na Pi po adresie domowym", "10.0.0.4", 22, "DROP"),
                               ("Ty wejdziesz SSH na Pi po adresie domowym", "10.0.0.2", 22, "ACCEPT")]:
    result = simulate.simulate(cfg_s, peers_s, src, "192.168.50.10", port, "tcp")
    check(label, result["verdict"] == want, f'{result["verdict"]}: {result["reason"]}')

# migracja starej bazy: 'split' -> NAS dokladny, inne caly LAN
conn_m, path_m = fresh("migr.db")
with conn_m:
    conn_m.execute("UPDATE tiers SET client_mode = 'split' WHERE name = 'NAS'")
    conn_m.execute("INSERT INTO tiers(name, client_mode) VALUES ('GOSC', 'split')")
    conn_m.execute("ALTER TABLE tiers DROP COLUMN client_list")
conn_m.close()
conn_m = db.connect(path_m)
modes = {r["name"]: r["client_mode"] for r in conn_m.execute("SELECT name, client_mode FROM tiers")}
check("migracja: NAS przechodzi na 'tylko NAS'", modes["NAS"] == "auto", str(modes))
check("migracja: inny split zostaje calym LAN-em jak dawniej", modes["GOSC"] == "lan", str(modes))

env_s = dict(os.environ, WGPANEL_DB=path_s, PYTHONPATH=ROOT)
run_s = lambda *a: subprocess.run([sys.executable, os.path.join(ROOT, "wgctl"), *a],
                                  env=env_s, capture_output=True, text=True, timeout=60)
out = run_s("allowed", "Marek")
check("wgctl allowed pokazuje linie do wklejenia",
      "AllowedIPs = 192.168.50.11/32, 192.168.50.70/32" in out.stdout, out.stdout + out.stderr)
out = run_s("set", "Marek", "--tunnel", "custom", "--tunnel-list", "nas")
out = run_s("allowed", "Marek")
check("wgctl set --tunnel custom", "AllowedIPs = 192.168.50.11/32" in out.stdout, out.stdout)
out = run_s("set", "Marek", "--tunnel", "custom", "--tunnel-list", "nieznany")
check("wgctl odrzuca nieznana nazwe na liscie", out.returncode != 0, out.stdout)
out = run_s("set", "Marek", "--tunnel", "tier")
out = run_s("allowed", "Marek")
check("wgctl set --tunnel tier wraca do ustawien tieru", "z tieru NAS" in out.stdout, out.stdout)


# ==========================================================================
print("\n15. Sciezka B - czysty system")
import socket  # noqa: E402

port = None
with socket.socket() as probe:
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
fresh_db = os.path.join(TMP, "fresh-app.db")
proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "panel", "app.py")],
                        env=dict(os.environ, WGPANEL_DB=fresh_db, WGPANEL_HOST="10.254.254.254",
                                 WGPANEL_PORT=str(port), PYTHONPATH=ROOT,
                                 WGPANEL_SOCKET=os.path.join(TMP, "brak.sock")),
                        stderr=subprocess.PIPE, text=True)
import urllib.request  # noqa: E402
page = ""
for _ in range(50):
    try:
        page = urllib.request.urlopen(f"http://127.0.0.1:{port}/setup", timeout=2).read().decode()
        break
    except Exception:
        time.sleep(0.2)
proc.terminate()
_, err = proc.communicate(timeout=10)
check("panel startuje, choc adresu VPN jeszcze nie ma", "Pierwsze uruchomienie" in page, err[-300:])
check("panel mowi, ze czeka na wg0", "czekam" in err, err[-300:])

# lista adresow: 127.0.0.2 zastepuje tu adres LAN, 127.0.0.1 adres VPN
with socket.socket() as probe:
    probe.bind(("127.0.0.1", 0))
    port2 = probe.getsockname()[1]
proc2 = subprocess.Popen([sys.executable, os.path.join(ROOT, "panel", "app.py")],
                         env=dict(os.environ, WGPANEL_DB=os.path.join(TMP, "lista.db"),
                                  WGPANEL_HOST="127.0.0.1, 127.0.0.2", WGPANEL_PORT=str(port2),
                                  PYTHONPATH=ROOT, WGPANEL_SOCKET=os.path.join(TMP, "brak.sock")),
                         stderr=subprocess.PIPE, text=True)
reached = []
for address in ("127.0.0.1", "127.0.0.2"):
    for _ in range(50):
        try:
            urllib.request.urlopen(f"http://{address}:{port2}/setup", timeout=2)
            reached.append(address)
            break
        except Exception:
            time.sleep(0.2)
proc2.terminate()
proc2.communicate(timeout=10)
check("panel slucha na kilku adresach naraz (LAN i VPN)",
      reached == ["127.0.0.1", "127.0.0.2"], str(reached))

fresh_wg = os.path.join(TMP, "fresh-wireguard")
os.makedirs(fresh_wg, exist_ok=True)
H.WG_DIR = fresh_wg
H.BACKUP_DIR = os.path.join(fresh_wg, "wg-panel-backups")
H.SYSCTL_DIR = os.path.join(TMP, "fresh-sysctl")
with open(os.path.join(fresh_wg, "server_private.key"), "w") as handle:
    handle.write(key("S") + "\n")
fresh_state = {"up": False, "calls": [], "restart_rc": 0}


def fresh_run(args, **kwargs):
    fresh_state["calls"].append(args)
    if args[:3] == ["ip", "link", "show"]:
        return subprocess.CompletedProcess(args, 0 if fresh_state["up"] else 1, "", "")
    if args[:2] == ["sysctl", "-n"]:
        return subprocess.CompletedProcess(args, 0, "0\n", "")
    if args[:2] == ["systemctl", "is-enabled"]:
        return subprocess.CompletedProcess(args, 1, "disabled\n", "")
    if args[:2] == ["systemctl", "restart"]:
        return subprocess.CompletedProcess(args, fresh_state["restart_rc"], "",
                                           "" if fresh_state["restart_rc"] == 0 else "blad")
    return subprocess.CompletedProcess(args, 0, "", "")


H.run = fresh_run
conn_b, _ = fresh("sciezka-b.db")
add_peer(conn_b, "Ja", "10.0.0.2", "ADMIN", "A")
add_peer(conn_b, "Marek", "10.0.0.4", "NAS", "C")
payload_b = model.build(conn_b)
result = H.op_apply(dict(payload_b, confirm_seconds=0))
conf_path_b = os.path.join(fresh_wg, "wg0.conf")
check("pierwsze zastosowanie tworzy wg0.conf z kluczem serwera",
      os.path.exists(conf_path_b) and key("S") in open(conf_path_b).read())
check("wg-quick podnosi interfejs", ["systemctl", "restart", "wg-quick@wg0"] in fresh_state["calls"])
check("tunel wlaczony przy starcie systemu", ["systemctl", "enable", "wg-quick@wg0"] in fresh_state["calls"])
check("przekazywanie IPv4 wlaczone", ["sysctl", "-qw", "net.ipv4.ip_forward=1"] in fresh_state["calls"])
check("przekazywanie IPv4 trwale", os.path.exists(os.path.join(H.SYSCTL_DIR, H.FORWARD_FILE)))

os.remove(conf_path_b)
fresh_state["restart_rc"] = 1
try:
    H.op_apply(dict(payload_b, confirm_seconds=0))
    check("nieudany start tunelu zglaszany jako blad", False)
except H.Rejected as exc:
    check("nieudany start tunelu zglaszany jako blad", "nie wstal" in str(exc), str(exc))
check("po nieudanym starcie nie zostaje polowiczny wg0.conf", not os.path.exists(conf_path_b))
check("cofniecie na czystym systemie zatrzymuje tunel zamiast go podnosic",
      ["systemctl", "stop", "wg-quick@wg0"] in fresh_state["calls"])
fresh_state["restart_rc"] = 0

# ==========================================================================
print("\n16. Sciezka C - nowy system z kopii na NAS")
conn_old, _ = fresh("stary-system.db")
add_peer(conn_old, "Ja", "10.0.0.2", "ADMIN", "A")
add_peer(conn_old, "Marek", "10.0.0.4", "NAS", "C")
add_peer(conn_old, "Kasia", "10.0.0.5", "NAS", "D")
with conn_old:
    conn_old.execute("INSERT INTO hosts(name, address) VALUES ('drukarka', '192.168.50.70')")
smb = {"backup_target": "smb", "smb_host": "192.168.50.11", "smb_share": "backup",
       "smb_path": "sciezka-c", "smb_user": "backup-wg", "smb_pass": "tajne",
       "backup_passphrase": "haslo-kopii", "backup_dir": os.path.join(TMP, "kopie-stare")}
os.makedirs(os.path.join(share, "sciezka-c"), exist_ok=True)
for k, v in smb.items():
    db.set_setting(conn_old, k, v)
record = export.run(conn_old, actor="test")
check("stary system zrobil kopie na NAS", record["ok"] == 1, record["detail"])

conn_new, _ = fresh("nowy-system.db")
for k, v in dict(smb, backup_dir=os.path.join(TMP, "kopie-nowe")).items():
    db.set_setting(conn_new, k, v)
check("nowy system nie ma wlasnej historii kopii",
      conn_new.execute("SELECT COUNT(*) c FROM backups").fetchone()["c"] == 0)
listed = export.list_smb(db.settings(conn_new))
check("nowy system widzi kopie lezace na NAS",
      os.path.basename(record["path"]) in listed, str(listed))
summary = export.restore_from(conn_new, "smb:" + listed[0], "haslo-kopii", True, "test")
check("odtworzenie prosto z NAS",
      summary["peers"] == 3 and summary["source"].startswith("NAS"), str(summary))
check("katalog hostow wrocil",
      any(h["name"] == "drukarka" for h in db.hosts(conn_new)))
check("haslo SMB zostalo (nie jest w kopii, a bylo wpisane recznie)",
      db.settings(conn_new)["smb_pass"] == "tajne")

old_model = model.build(conn_old)
new_model = model.build(conn_new)
check("te same osoby, klucze i PSK po odtworzeniu",
      [(p["ip"], p["pubkey"], p["psk"]) for p in old_model["peers"]]
      == [(p["ip"], p["pubkey"], p["psk"]) for p in new_model["peers"]])

fresh_state["up"] = False
os.remove(os.path.join(H.SYSCTL_DIR, H.FORWARD_FILE))
H.op_apply(dict(new_model, confirm_seconds=0))
new_conf = open(conf_path_b).read()
check("nowy wg0.conf z tym samym kluczem serwera - klienci lacza sie bez zmian",
      key("S") in new_conf and all(p["pubkey"] in new_conf for p in old_model["peers"]))

name_c = listed[0]
with open(os.path.join(share, "sciezka-c", name_c + ".sha256"), "w") as handle:
    handle.write("0" * 64 + "  " + name_c + "\n")
try:
    export.restore_from(conn_new, "smb:" + name_c, "haslo-kopii", True, "test")
    check("niezgodna suma na NAS blokuje odtworzenie", False)
except ValueError as exc:
    check("niezgodna suma na NAS blokuje odtworzenie", "suma" in str(exc), str(exc))
try:
    export.restore_from(conn_new, "smb:../../etc/passwd", "x", True, "test")
    check("nazwa spoza kopii odrzucona", False)
except ValueError:
    check("nazwa spoza kopii odrzucona", True)


# ==========================================================================
print("\n17. Peer bez klucza wspolnego (PSK)")
conn_np, _ = fresh("bez-psk.db")
tier_np = conn_np.execute("SELECT id FROM tiers WHERE name='NAS'").fetchone()["id"]
with conn_np:
    conn_np.execute("INSERT INTO peers(name, device, ip, pubkey, psk, tier_id, state, note, "
                    "created_at) VALUES ('Stary', '', '10.0.0.4', ?, '', ?, 'active', '', 0)",
                    (key("C"), tier_np))
    conn_np.execute("INSERT INTO peers(name, device, ip, pubkey, psk, tier_id, state, note, "
                    "created_at) VALUES ('Nowy', '', '10.0.0.5', ?, ?, ?, 'active', '', 0)",
                    (key("D"), key("P"), tier_np))
payload_np = model.build(conn_np)
hcfg_np = H.validate_settings(payload_np["settings"])
hpeers_np = H.validate_peers(payload_np["peers"], hcfg_np)
check("helper przyjmuje peera bez PSK", len(hpeers_np) == 2)
conf_np = H.render_wg_conf(hcfg_np, hpeers_np, render.PLACEHOLDER_KEY,
                           "/etc/wireguard/wg0-panel-rules.v4")
check("wg0.conf: peer bez PSK nie dostaje pustej linii PresharedKey",
      conf_np.count("PresharedKey") == 1, conf_np)
check("panel i helper licza to samo przy braku PSK",
      conf_np == render.render_wg_conf(db.render_cfg(conn_np), model.peers_for_render(conn_np),
                                       "/etc/wireguard/wg0-panel-rules.v4"))
client_np = model.client_conf(conn_np, db.peer(conn_np, 1))[0]
check("plik klienta bez PSK nie ma pustej linii", "PresharedKey" not in client_np, client_np)
broken = json.loads(json.dumps(payload_np["peers"]))
broken[0]["psk"] = "to-nie-jest-klucz"
try:
    H.validate_peers(broken, hcfg_np)
    check("bledny PSK nadal odrzucany", False)
except H.Rejected:
    check("bledny PSK nadal odrzucany", True)


# ==========================================================================
print("\n18. Dostep do panelu przez tier i usuwanie peerow")
conn_pa, path_pa = fresh("panel-access.db")
ja_pa = add_peer(conn_pa, "Ja", "10.0.0.2", "ADMIN", "A")
marek_pa = add_peer(conn_pa, "Marek", "10.0.0.4", "NAS", "C")
# dostep do panelu wynika z portow tieru ADMIN (22,3389,8080)
cfg_pa = db.render_cfg(conn_pa)
peers_pa = model.peers_for_render(conn_pa)
check("port panelu jest zarzadzany", "8080" in cfg_pa["managed_host_ports"])
host_lines = [l for l in render.render_rules_file(cfg_pa, peers_pa).split("\n")
              if "WG-USERS-HOST" in l and l.startswith("-A")]
check("tier ADMIN ma port panelu na liscie portow hosta",
      any("10.0.0.2/32" in l and "22,3389,8080" in l and "ACCEPT" in l for l in host_lines),
      str(host_lines))
check("reszta blokowana na porcie panelu",
      any("8080" in l and "DROP" in l for l in host_lines), str(host_lines))
for label, src, want in [("admin wejdzie do panelu przez VPN", "10.0.0.2", "ACCEPT"),
                         ("zwykla osoba nie wejdzie do panelu", "10.0.0.4", "DROP")]:
    result = simulate.simulate(cfg_pa, peers_pa, src, "10.0.0.1", 8080, "tcp")
    check(label, result["verdict"] == want, f'{result["verdict"]}: {result["reason"]}')

payload_pa = model.build(conn_pa)
hcfg_pa = H.validate_settings(payload_pa["settings"])
check("helper przyjmuje port panelu w portach zarzadzanych",
      8080 in hcfg_pa["managed_host_ports"], str(hcfg_pa["managed_host_ports"]))
check("panel i helper licza te same reguly hosta",
      H.render_rules_file(hcfg_pa, H.validate_peers(payload_pa["peers"], hcfg_pa))
      == render.render_rules_file(cfg_pa, peers_pa))

usun_id = conn_pa.execute("SELECT id FROM peers WHERE name='Marek'").fetchone()["id"]
with conn_pa:
    conn_pa.execute("DELETE FROM rules WHERE peer_id = ?", (usun_id,))
    conn_pa.execute("DELETE FROM peers WHERE id = ?", (usun_id,))
check("usuniety peer zwalnia swoj adres", db.next_free_ip(conn_pa) == "10.0.0.3",
      db.next_free_ip(conn_pa))
check("usuniety peer znika z regul",
      "10.0.0.4" not in render.render_rules_file(db.render_cfg(conn_pa),
                                                 model.peers_for_render(conn_pa)))

# ==========================================================================
print("\n19. Sygnal zmian liczony z plikow na dysku")
wg_live = os.path.join(TMP, "live-wireguard")
os.makedirs(wg_live, exist_ok=True)
H.WG_DIR = wg_live
H.BACKUP_DIR = os.path.join(wg_live, "wg-panel-backups")
H.SYSCTL_DIR = os.path.join(TMP, "live-sysctl")
with open(os.path.join(wg_live, "server_private.key"), "w") as handle:
    handle.write(key("S") + "\n")
live_state = {"up": True}


def live_run(args, **kwargs):
    if args[:3] == ["ip", "link", "show"]:
        return subprocess.CompletedProcess(args, 0 if live_state["up"] else 1, "", "")
    if args[:2] == ["wg-quick", "strip"]:
        return subprocess.CompletedProcess(args, 0, "[Interface]\n", "")
    if args[:2] == ["sysctl", "-n"]:
        return subprocess.CompletedProcess(args, 0, "1\n", "")
    if args[:2] == ["systemctl", "is-enabled"]:
        return subprocess.CompletedProcess(args, 0, "enabled\n", "")
    if args[0] == "wg" and args[1:3] == ["show", "wg0"] and args[3:] == ["dump"]:
        return subprocess.CompletedProcess(args, 0, "", "")
    return subprocess.CompletedProcess(args, 0, "", "")


H.run = live_run
conn_d, _ = fresh("dirty-live.db")
add_peer(conn_d, "Ja", "10.0.0.2", "ADMIN", "A")
add_peer(conn_d, "Marek", "10.0.0.4", "NAS", "C")
payload_d = model.build(conn_d)
H.op_apply(dict(payload_d, confirm_seconds=0))
status_d = H.op_status({"settings": payload_d["settings"]})
check("po zastosowaniu panel nie zglasza zmian",
      not model.is_dirty(conn_d, None, status_d),
      str(model.rendered_files(conn_d)) + " vs " + str(status_d.get("files")))

with conn_d:
    conn_d.execute("UPDATE peers SET state='suspended' WHERE ip='10.0.0.4'")
check("zawieszenie peera zglasza zmiane", model.is_dirty(conn_d, None, status_d))
with conn_d:
    conn_d.execute("UPDATE peers SET state='active' WHERE ip='10.0.0.4'")
check("cofniecie zmiany w panelu gasi sygnal",
      not model.is_dirty(conn_d, None, status_d))
with conn_d:
    conn_d.execute("UPDATE peers SET note='tylko notatka' WHERE ip='10.0.0.2'")
check("notatka nie zglasza zmiany", not model.is_dirty(conn_d, None, status_d))

# ktos przywrocil starsza konfiguracje na dysk - to JEST rozbieznosc
with open(os.path.join(wg_live, "wg0.conf"), "a") as handle:
    handle.write("\n[Peer]\n# ktos dopisal\nPublicKey = " + key("Z") + "\nAllowedIPs = 10.0.0.9/32\n")
status_d2 = H.op_status({"settings": payload_d["settings"]})
check("zmiana pliku na dysku zglasza rozbieznosc",
      model.is_dirty(conn_d, None, status_d2))


# ==========================================================================
print("\n20. Skad bierze sie roznica wzgledem interfejsu")
conn_w, _ = fresh("why.db")
add_peer(conn_w, "Ja", "10.0.0.2", "ADMIN", "A")
model.mark_applied(conn_w, model.build(conn_w), {})
disk = {"files": dict(model.rendered_files(conn_w, "/etc/wireguard/wg0-panel-rules.v4"),
                      rules_path="/etc/wireguard/wg0-panel-rules.v4")}
check("zaraz po zastosowaniu nie ma roznicy", not model.is_dirty(conn_w, None, disk))
add_peer(conn_w, "Nowy", "10.0.0.9", "NAS", "Z")
check("moja zmiana daje roznice", model.is_dirty(conn_w, None, disk))
check("panel wie, ze odrzucenie pomoze", model.discard_helps(conn_w, disk))
check("co sie rozni", model.diff_summary(conn_w, disk) != [], str(model.diff_summary(conn_w, disk)))
model.discard_changes(conn_w, "test")
check("po odrzuceniu roznica znika", not model.is_dirty(conn_w, None, disk))

with conn_w:      # plik zapisany starsza wersja panelu - inne porty hosta w tierze
    conn_w.execute("UPDATE tiers SET host_access = '22,3389' WHERE name = 'ADMIN'")
stare = {"files": dict(model.rendered_files(conn_w, "/etc/wireguard/wg0-panel-rules.v4"),
                       rules_path="/etc/wireguard/wg0-panel-rules.v4")}
with conn_w:
    conn_w.execute("UPDATE tiers SET host_access = '22,3389,8080' WHERE name = 'ADMIN'")
check("roznica po aktualizacji panelu jest widoczna", model.is_dirty(conn_w, None, stare))
check("panel wie, ze odrzucenie tu nie pomoze", not model.discard_helps(conn_w, stare))

print("\n" + "=" * 60)
if FAILURES:
    print(f"NIEPOWODZENIA ({len(FAILURES)}): " + ", ".join(FAILURES))
    sys.exit(1)
print("Wszystkie testy przeszly.")
