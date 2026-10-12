#!/usr/bin/env python3
"""Aktualizacja nie zmienia danych: osoby, tiery, reguly, hosty i ustawienia sa po aktualizacji
dokladnie takie same, takze gdy nowa wersja ma inne ustawienia domyslne; skasowany tier
wbudowany nie wraca; instalator pamieta opcje i zapisuje stan sprzed aktualizacji."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from panel import db, export, ui  # noqa: E402

FAILURES = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  [{'OK  ' if condition else 'BLAD'}] {label}"
          + (f"  ({detail})" if detail and not condition else ""))
    if not condition:
        FAILURES.append(label)


TMP = tempfile.mkdtemp(prefix="wgpanel-update-")
DB = os.path.join(TMP, "panel.db")


def dump(conn) -> dict:
    out = {}
    for table in ("peers", "tiers", "rules", "hosts", "users", "backups"):
        out[table] = [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY id")]
    out["settings"] = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings")}
    return out


print("\n1. Dane po aktualizacji")
conn = db.connect(DB)
db.init_defaults(conn)
tiers = {r["name"]: r["id"] for r in conn.execute("SELECT name, id FROM tiers")}
with conn:
    conn.execute("INSERT INTO tiers(name, description, client_mode) VALUES ('Rodzina', 'moj tier', 'lan')")
    conn.execute("INSERT INTO peers(name, device, ip, pubkey, psk, tier_id, created_at) "
                 "VALUES ('Ola', 'telefon', '10.9.0.7', ?, ?, ?, 1)", ("O" * 43 + "=", "P" * 43 + "=", tiers["NET"]))
    peer = conn.execute("SELECT id FROM peers").fetchone()[0]
    conn.execute("INSERT INTO rules(peer_id, action, dest, proto, ports) VALUES (?, 'ACCEPT', 'nas', 'tcp', '445')", (peer,))
    conn.execute("INSERT INTO hosts(name, address) VALUES ('drukarka', '10.1.1.40')")
    conn.execute("DELETE FROM tiers WHERE name = 'NAS'")          # skasowany tier wbudowany
for key, value in (("lan", "10.1.1.0/24"), ("server_lan", "10.1.1.5"), ("smb_host", "10.1.1.2"),
                   ("endpoint", "dom.example.org:51820"), ("vpn", "10.9.0.0/24")):
    db.set_setting(conn, key, value)
before = dump(conn)
conn.close()

# nowa wersja: inne ustawienia domyslne (np. neutralne na GitHuba) i ponowny start panelu
original = dict(db.DEFAULT_SETTINGS)
db.DEFAULT_SETTINGS.update({"lan": "192.168.1.0/24", "server_lan": "192.168.1.2", "smb_host": "",
                            "vpn": "10.0.0.0/24", "nowe_ustawienie": "x"})
try:
    conn = db.connect(DB)
    db.init_defaults(conn)
    db.init_defaults(conn)       # panel startuje wiele razy, wgctl tez wola init_defaults
    after = dump(conn)
finally:
    db.DEFAULT_SETTINGS.clear()
    db.DEFAULT_SETTINGS.update(original)

for table in ("peers", "tiers", "rules", "hosts", "users", "backups"):
    check(f"{table}: bez zmian", before[table] == after[table],
          f"{len(before[table])} -> {len(after[table])}")
changed = {k: (v, after["settings"].get(k)) for k, v in before["settings"].items()
           if after["settings"].get(k) != v}
check("ustawienia: Twoje wartości zostają mimo innych domyślnych", not changed, str(changed))
check("nowe ustawienie dochodzi z wartością domyślną", after["settings"].get("nowe_ustawienie") == "x")
check("skasowany tier wbudowany (NAS) nie wraca", "NAS" not in [t["name"] for t in after["tiers"]])
with conn:
    conn.execute("DELETE FROM hosts")
db.init_defaults(conn)
check("pusty katalog hostów zostaje pusty (bez „nas” z powrotem)",
      conn.execute("SELECT COUNT(*) FROM hosts").fetchone()[0] == 0)
fresh = db.connect(":memory:")
db.init_defaults(fresh)
check("na pustej bazie tiery wbudowane powstają",
      sorted(r["name"] for r in fresh.execute("SELECT name FROM tiers")) == ["ADMIN", "NAS", "NET"])

print("\n2. Stan sprzed aktualizacji")
db.set_setting(conn, "backup_dir", os.path.join(TMP, "kopie"))
conn.close()
env = dict(os.environ, WGPANEL_DB=DB, PYTHONPATH=ROOT)
out = subprocess.run([sys.executable, "-m", "panel.restore_cli", "snapshot"], cwd=ROOT, env=env,
                     capture_output=True, text=True, timeout=60)
conn = db.connect(DB)
row = conn.execute("SELECT * FROM backups ORDER BY id DESC LIMIT 1").fetchone()
check("instalator zapisuje stan sprzed aktualizacji", out.returncode == 0 and row is not None
      and row["detail"] == "kopia stanu przed aktualizacją" and os.path.exists(row["path"]),
      out.stdout + out.stderr)
html = ui.backup_row(ui.backup_items([row], [os.path.basename(row["path"])])[0])
check("na liście kopii: „sprzed aktualizacji”", "sprzed aktualizacji" in html)
data = export.parse(open(row["path"], "rb").read())
check("w środku osoby i tiery", [p["name"] for p in data["peers"]] == ["Ola"]
      and "Rodzina" in [t["name"] for t in data["tiers"]])
out = subprocess.run([sys.executable, "-m", "panel.restore_cli", "snapshot"], cwd=ROOT,
                     env=dict(env, WGPANEL_DB=os.path.join(TMP, "nie-ma.db")),
                     capture_output=True, text=True, timeout=60)
check("pierwsza instalacja (bez bazy): nic do zapisania, bez błędu", out.returncode == 0)

print("\n3. Instalator pamięta opcje")
source = open(os.path.join(ROOT, "install.sh"), encoding="utf8").read()
start = source.index("# --- opcje instalacji ---")
end = source.index('TLS="${TLS:-1}"', start) + len('TLS="${TLS:-1}"')
fragment = source[start:end]
bindir = os.path.join(TMP, "bin")
os.makedirs(bindir)
with open(os.path.join(bindir, "ufw"), "w", encoding="utf8") as handle:
    handle.write("#!/bin/sh\necho 'Status: active'\n"
                 "echo '8080/tcp                   ALLOW       192.168.50.0/24            # wg-panel LAN'\n")
os.chmod(os.path.join(bindir, "ufw"), 0o755)


def options(conf: str = "", unit: str = "", **extra) -> dict:
    conf_path, unit_path = os.path.join(TMP, "install.conf"), os.path.join(TMP, "wg-panel.service")
    for path, text in ((conf_path, conf), (unit_path, unit)):
        if text:
            open(path, "w", encoding="utf8").write(text)
        elif os.path.exists(path):
            os.remove(path)
    script = ('DATA=/nie-ma\nset -u\n' + fragment +
              '\necho "LAN=$LAN_ACCESS PORT=$BIND_PORT TLS=$TLS HOST=$BIND_HOST LANG=${INSTALL_LANG:-} FROM=$OPTIONS_FROM"\n')
    base = {k: v for k, v in os.environ.items()
            if k not in ("LAN_ACCESS", "BIND_PORT", "TLS", "INSTALL_LANG", "BIND_HOST")}
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                         env=dict(base, PATH=bindir + os.pathsep + os.environ["PATH"],
                                  WGPANEL_CONF=conf_path, WGPANEL_UNIT=unit_path, **extra))
    return dict(item.split("=", 1) for item in out.stdout.split())


got = options()
check("pierwsza instalacja bez opcji: tylko przez VPN, port 8080, HTTPS",
      got["LAN"] == "" and got["PORT"] == "8080" and got["TLS"] == "1" and got["HOST"] == "10.0.0.1", str(got))
saved = "LAN_ACCESS=192.168.50.0/24\nBIND_PORT=8443\nTLS=1\nINSTALL_LANG=en\n"
got = options(saved)
check("aktualizacja bez opcji: zapamiętany dostęp z LAN, port i język",
      got["LAN"] == "192.168.50.0/24" and got["PORT"] == "8443" and got["HOST"] == "0.0.0.0"
      and got["LANG"] == "en", str(got))
got = options(saved, LAN_ACCESS="10.1.1.0/24")
check("opcja podana jawnie wygrywa", got["LAN"] == "10.1.1.0/24", str(got))
got = options(saved, LAN_ACCESS="")
check("pusta LAN_ACCESS= wyłącza dostęp z LAN", got["LAN"] == "" and got["HOST"] == "10.0.0.1", str(got))
unit = "[Service]\nEnvironment=WGPANEL_HOST=0.0.0.0\nEnvironment=WGPANEL_PORT=8080\nEnvironment=WGPANEL_TLS=1\n"
got = options("", unit)
check("panel ze starszej wersji: dostęp z LAN odczytany z usługi i reguły zapory",
      got["LAN"] == "192.168.50.0/24" and got["HOST"] == "0.0.0.0", str(got))
got = options("LAN_ACCESS=\nBIND_PORT=8080\nTLS=1\nINSTALL_LANG=pl\nBIND_HOST=10.8.0.1\n")
check("zapamiętany adres panelu w VPN (sieć VPN inna niż 10.0.0.0/24)",
      got["HOST"] == "10.8.0.1", str(got))
got = options("", "[Service]\nEnvironment=WGPANEL_HOST=10.8.0.1\nEnvironment=WGPANEL_PORT=8080\n")
check("panel ze starszej wersji: adres w VPN odczytany z usługi", got["HOST"] == "10.8.0.1"
      and got["LAN"] == "", str(got))
check("instalator zapisuje adres w VPN i pokazuje go w podsumowaniu",
      "BIND_HOST=$VPN_HOST" in source and "$SCHEME://$VPN_HOST:$BIND_PORT" in source)
check("instalator: stan sprzed aktualizacji po skopiowaniu programu, przed restartem usług",
      source.index('cp -r "$SRC/panel"') < source.index("restore_cli snapshot")
      < source.index("systemctl restart wg-panel-helper"))
check("instalator: opcje zapisane po udanym starcie, plik tylko roota",
      source.index("systemctl is-active --quiet wg-panel ||") < source.index('cat > "$CONF"')
      and 'chmod 644 "$CONF"' in source)
check("instalator: złe opcje zatrzymują instalację, zanim cokolwiek zmieni",
      source.index("LAN_ACCESS ma być siecią") < source.index("Instaluję jednostki systemd"))
check("odinstalowanie --purge usuwa też zapamiętane opcje",
      '"$ROOT/etc/wg-panel"' in open(os.path.join(ROOT, "uninstall.sh"), encoding="utf8").read())
check("składnia instalatora", subprocess.run(["bash", "-n", os.path.join(ROOT, "install.sh")]).returncode == 0)

print("\n4. Nowa instalacja: sieć wykryta na Pi zamiast przykładowej")
with open(os.path.join(bindir, "ip"), "w", encoding="utf8") as handle:
    handle.write('#!/bin/sh\ncase "$*" in\n'
                 '  *"route get"*) echo "1.1.1.1 via 10.20.30.1 dev end0 src 10.20.30.44 uid 1000" ;;\n'
                 '  *"addr show"*) echo "2: end0    inet 10.20.30.44/24 brd 10.20.30.255 scope global end0" ;;\n'
                 'esac\n')
os.chmod(os.path.join(bindir, "ip"), 0o755)
new_db = os.path.join(TMP, "nowa.db")
fr_env = dict(os.environ, WGPANEL_DB=new_db, PYTHONPATH=ROOT, PATH=bindir + os.pathsep + os.environ["PATH"])
out = subprocess.run([sys.executable, "-m", "panel.firstrun"], cwd=ROOT, env=fr_env,
                     capture_output=True, text=True, timeout=60)
conn_new = db.connect(new_db)
db.init_defaults(conn_new)
cfg = db.settings(conn_new)
check("sieć domowa, adres Pi i interfejs z Pi", cfg["lan"] == "10.20.30.0/24"
      and cfg["server_lan"] == "10.20.30.44" and cfg["wan_if"] == "end0", out.stdout + out.stderr)
check("host „nas” (tier NAS) w tej samej sieci - do poprawienia w Hostach",
      conn_new.execute("SELECT address FROM hosts WHERE name = 'nas'").fetchone()[0] == "10.20.30.10")
check("host „pi” w katalogu ma prawdziwy adres",
      conn_new.execute("SELECT address FROM hosts WHERE name = 'pi'").fetchone()[0] == "10.20.30.44")
db.set_setting(conn_new, "lan", "10.20.30.0/25")       # uzytkownik poprawil
conn_new.close()
subprocess.run([sys.executable, "-m", "panel.firstrun"], cwd=ROOT, env=fr_env, timeout=60)
check("przy aktualizacji wykrywanie niczego nie zmienia",
      db.settings(db.connect(new_db))["lan"] == "10.20.30.0/25")
check("instalator: wykrywanie tylko na nowej instalacji, przed odtworzeniem z kopii",
      source.index('if [ ! -f "$DATA/panel.db" ]') < source.index("panel.firstrun")
      < source.index("restore_cli import"))
check("bez osobistych adresów w ustawieniach domyślnych",
      not any("192.168.50" in str(v) or "41194" in str(v) for v in db.DEFAULT_SETTINGS.values()))

print("\n5. Dziennik i alerty: osobne limity dni")
old = db.connect(os.path.join(TMP, "stara-retencja.db"))
with old:
    old.execute("DELETE FROM settings WHERE key = 'alerts_days'")
    old.execute("INSERT OR REPLACE INTO settings(key, value) VALUES ('audit_days', '7')")
db.init_defaults(old)
cfg = db.settings(old)
check("aktualizacja: alerty dostają dotychczasowy wspólny limit (7), nie nowy domyślny",
      cfg.get("audit_days") == "7" and cfg.get("alerts_days") == "7", str((cfg.get("audit_days"), cfg.get("alerts_days"))))
new = db.connect(":memory:")
db.init_defaults(new)
check("nowa instalacja: 90 dni dla dziennika i dla alertów",
      db.settings(new).get("audit_days") == "90" and db.settings(new).get("alerts_days") == "90")
now = 2_000_000_000
with new:
    for age in (1, 20, 100, 400):
        new.execute("INSERT INTO audit(ts, actor, action, detail) VALUES (?, 't', 'x', '')", (now - age * 86400,))
        new.execute("INSERT INTO alerts(ts, kind, severity, message) VALUES (?, 'k', 'info', 'm')", (now - age * 86400,))
db.set_setting(new, "audit_days", "30")
db.set_setting(new, "alerts_days", "0")
removed = db.prune_history(new, now)
left_audit = new.execute("SELECT COUNT(*) FROM audit").fetchone()[0]
left_alerts = new.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
check("dziennik 30 dni: znikają wpisy starsze niż 30 dni", removed[0] == 2 and left_audit == 2, str(removed))
check("alerty 0 = bez limitu: nic nie znika, niezależnie od dziennika", removed[1] == 0 and left_alerts == 4)
db.set_setting(new, "alerts_days", "200")
db.set_setting(new, "audit_days", "0")
removed = db.prune_history(new, now)
check("i odwrotnie: alerty 200 dni, dziennik bez limitu", removed == (0, 1), str(removed))
db.set_setting(new, "audit_days", "abc")
check("zła wartość w bazie nie psuje sprzątania - obowiązuje domyślna",
      db.retention_days(db.settings(new), "audit_days") == 90)
with new:
    new.execute("UPDATE settings SET value = '7' WHERE key = 'audit_days'")
    new.execute("UPDATE settings SET value = '90' WHERE key = 'alerts_days'")
backup = export.build(new)
backup["settings"] = [row for row in backup["settings"] if row["key"] != "alerts_days"]
target = db.connect(":memory:")
db.init_defaults(target)
export.restore(target, backup)
check("odtworzenie kopii sprzed rozdzielenia: alerty z limitem dziennika z kopii",
      db.settings(target).get("alerts_days") == "7", db.settings(target).get("alerts_days"))

print("\n" + "=" * 60)
if FAILURES:
    print(f"NIEUDANE ({len(FAILURES)}):")
    for item in FAILURES:
        print("  -", item)
    sys.exit(1)
print("Wszystkie testy przeszly.")
