#!/usr/bin/env python3
"""Pełna kopia i odtworzenie całego Pi (install.sh --restore).

1. Kopia bez hasła: podstawowa, jawna, bez kluczy i haseł. Z hasłem: pełna i zaszyfrowana -
   hasła, historia, klucz serwera i urząd certyfikacji (z helpera, na atrapach).
2. Odtworzenie w panelu: klucz serwera i urząd wracają na miejsce, werdykt „pliki działają”;
   kopia bez klucza przy innym kluczu Pi - werdykt „każdy potrzebuje nowego pliku”.
3. restore_cli: fetch z atrapy NAS (lista, suma, hasło), import do nowej bazy, apply
   przez helpera; błędy NAS po ludzku.
4. Instalator: pytanie o klucz serwera (wklej / nowy / przerwij) bez interakcji.
5. Panel: informacja, jaka kopia powstaje (pełna / podstawowa / słabe hasło).
"""
from __future__ import annotations

import base64
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
TMP = tempfile.mkdtemp(prefix="wgpanel-full-")
os.chmod(TMP, 0o755)
BIN, WGDIR, SMB = (os.path.join(TMP, d) for d in ("bin", "wireguard", "smb"))
for path in (BIN, WGDIR, os.path.join(SMB, "backup", "nasbackup"), os.path.join(SMB, "backup", "photos")):
    os.makedirs(path)
FAILURES = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  [{'OK  ' if condition else 'BLAD'}] {label}"
          + (f"  ({detail})" if detail and not condition else ""))
    if not condition:
        FAILURES.append(label)


def wg_key() -> str:
    raw = bytearray(os.urandom(32))
    raw[0] &= 248
    raw[31] = (raw[31] & 127) | 64
    return base64.b64encode(bytes(raw)).decode()


def stub(name: str, script: str) -> None:
    path = os.path.join(BIN, name)
    with open(path, "w", encoding="utf8") as handle:
        handle.write(script)
    os.chmod(path, 0o755)


# atrapa NAS: katalogi w TMP/smb/<udzial>/..., login z FAKE_SMB_USER / FAKE_SMB_PASS
stub("smbclient", r'''#!%s
import os, shutil, sys
root = %r
args = sys.argv[1:]
share = args[0].split("/")[-1]
user = args[args.index("-U") + 1] if "-U" in args else ""
cmds = args[args.index("-c") + 1] if "-c" in args else ""
if user != os.environ.get("FAKE_SMB_USER", "nasbackup") or os.environ.get("PASSWD") != os.environ.get("FAKE_SMB_PASS", "tajne"):
    print("session setup failed: NT_STATUS_LOGON_FAILURE"); sys.exit(1)
base = os.path.join(root, share)
if not os.path.isdir(base):
    print("tree connect failed: NT_STATUS_BAD_NETWORK_NAME"); sys.exit(1)
cwd = base
for raw in [c.strip() for c in cmds.split(";") if c.strip()]:
    parts = [p.strip('"') for p in raw.split('" "')] if '"' in raw else raw.split()
    word, _, rest = raw.partition(" ")
    rest = [p.strip('"') for p in rest.split('" "')] if rest else []
    if word == "prompt":
        continue
    if word == "cd":
        target = os.path.join(cwd, rest[0])
        if not os.path.isdir(target):
            print("cd \\%%s\\: NT_STATUS_OBJECT_NAME_NOT_FOUND" %% rest[0]); sys.exit(1)
        cwd = target
    elif word == "mkdir":
        os.makedirs(os.path.join(cwd, rest[0]), exist_ok=True)
    elif word == "ls":
        for name in sorted(os.listdir(cwd)):
            kind = "D" if os.path.isdir(os.path.join(cwd, name)) else "A"
            print("  %%-40s %%s  0  Mon Oct 12 10:00:00 2026" %% (name, kind))
    elif word == "get":
        src = os.path.join(cwd, rest[0])
        if not os.path.exists(src):
            print("NT_STATUS_OBJECT_NAME_NOT_FOUND opening remote file"); sys.exit(1)
        shutil.copy(src, rest[1])
    elif word == "put":
        shutil.copy(rest[0], os.path.join(cwd, rest[1]))
    elif word == "del":
        os.remove(os.path.join(cwd, rest[0]))
''' % (sys.executable, SMB))
stub("wg", '#!/bin/bash\ncase "$1" in show) exit 0;; pubkey) python3 -c "import sys,hashlib,base64;'
           'k=sys.stdin.read().strip();ok=len(k)==44 and len(base64.b64decode(k,validate=True))==32;'
           'print(base64.b64encode(hashlib.sha256(k.encode()).digest()).decode()) if ok else sys.exit(1)";;'
           ' genkey) python3 -c "import os,base64;b=bytearray(os.urandom(32));b[0]&=248;b[31]=(b[31]&127)|64;'
           'print(base64.b64encode(bytes(b)).decode())";; esac\nexit 0\n')
stub("wg-quick", "#!/bin/bash\nexit 0\n")
for tool in ("iptables", "ip6tables"):
    stub(tool, '#!/bin/bash\nfor a in "$@"; do if [ "$a" = "-C" ]; then exit 1; fi; done\nexit 0\n')
for tool in ("iptables-restore", "ip6tables-restore", "ip", "sysctl"):
    stub(tool, "#!/bin/bash\nexit 0\n")
stub("systemctl", '#!/bin/bash\nif [ "$1" = "is-active" ]; then echo active; fi\nexit 0\n')

OLD_KEY = wg_key()
with open(os.path.join(WGDIR, "server_private.key"), "w", encoding="utf8") as handle:
    handle.write(OLD_KEY + "\n")
CA_DIR = os.path.join(WGDIR, "wg-panel-ca")
os.makedirs(CA_DIR)
for name, body in (("ca.crt", "-----BEGIN CERTIFICATE-----\nSTARYURZAD\n-----END CERTIFICATE-----\n"),
                   ("ca.key", "-----BEGIN PRIVATE KEY-----\nSTARYKLUCZURZEDU\n-----END PRIVATE KEY-----\n"),
                   ("ca.json", '{"permitted": [], "dns": ["pi"]}')):
    with open(os.path.join(CA_DIR, name), "w", encoding="utf8") as handle:
        handle.write(body)

env = dict(os.environ)
env.update({"PATH": BIN + os.pathsep + env["PATH"], "WGPANEL_SOCKET": os.path.join(TMP, "helper.sock"),
            "WGPANEL_WGDIR": WGDIR, "WGPANEL_TLS_DIR": os.path.join(TMP, "tls"), "PYTHONPATH": ROOT})
os.environ.update(PATH=env["PATH"], WGPANEL_SOCKET=env["WGPANEL_SOCKET"], WGPANEL_WGDIR=WGDIR)

from panel import db, export, helperclient, i18n, model, ui  # noqa: E402

helperclient.SOCKET_PATH = env["WGPANEL_SOCKET"]
helper = subprocess.Popen([sys.executable, os.path.join(ROOT, "helper", "wg_panel_helper.py")],
                          env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(1.2)


def fresh(name: str):
    conn = db.connect(os.path.join(TMP, name))
    db.init_defaults(conn)
    return conn


def add_peer(conn, name: str, ip: str, char: str) -> None:
    tier = conn.execute("SELECT id FROM tiers WHERE name = 'ADMIN'").fetchone()["id"]
    with conn:
        conn.execute("INSERT INTO peers(name, ip, pubkey, psk, tier_id, state, access_mode, created_at) "
                     "VALUES (?, ?, ?, ?, ?, 'active', 'none', ?)",
                     (name, ip, char * 43 + "=", wg_key(), tier, 1))


try:
    print("\n1. Kopia podstawowa i pełna")
    conn = fresh("old.db")
    for key, value in (("smb_host", "127.0.0.1"), ("smb_share", "backup"), ("smb_path", "nasbackup"),
                       ("smb_user", "nasbackup"), ("smb_pass", "tajne"), ("alerts_token", "tk_tajny")):
        db.set_setting(conn, key, value)
    add_peer(conn, "Ja", "10.0.0.2", "J")
    add_peer(conn, "Anna", "10.0.0.4", "M")
    with conn:
        conn.execute("INSERT INTO users(username, pwd_hash, pwd_salt, created_at) VALUES ('admin','h','s',1)")
        conn.execute("INSERT INTO traffic(ts, pubkey, rx, tx) VALUES (5, ?, 10, 20)", ("J" * 43 + "=",))
        conn.execute("INSERT INTO audit(ts, actor, action, detail) VALUES (5, 'admin', 'login.ok', '')")
    db.set_setting(conn, "server_pubkey", helperclient.call(
        "server_pubkey", {"settings": model.settings_only(conn)})["pubkey"])

    blob, encrypted, kind = export.pack(conn, "")
    data = json.loads(blob)
    secrets = {r["key"] for r in data["settings"]} & export.SECRET_SETTINGS
    check("bez hasła: kopia podstawowa, jawna", kind == "basic" and not encrypted)
    check("bez hasła: bez haseł, historii i klucza serwera",
          not secrets and "system" not in data and "history" not in data and OLD_KEY not in blob.decode())

    blob, encrypted, kind = export.pack(conn, "dlugie-haslo-kopii")
    check("z hasłem: pełna i zaszyfrowana", kind == "full" and encrypted)
    check("zaszyfrowana naprawdę (klucza nie widać w pliku)", OLD_KEY.encode() not in blob)
    data = export.parse(blob, "dlugie-haslo-kopii")
    settings = {r["key"]: r["value"] for r in data["settings"]}
    check("pełna: hasła NAS i ntfy", settings.get("smb_pass") == "tajne"
          and settings.get("alerts_token") == "tk_tajny")
    check("pełna: historia (ruch, dziennik)", data["history"]["traffic"] and data["history"]["audit"])
    check("pełna: klucz serwera i urząd certyfikacji",
          data["system"]["server_key"] == OLD_KEY and "STARYURZAD" in data["system"]["ca"]["crt"])
    check("pełna: konta panelu", [u["username"] for u in data["users"]] == ["admin"])
    try:
        export.parse(blob, "zle-haslo")
        check("złe hasło odrzucone", False)
    except Exception:
        check("złe hasło odrzucone", True)

    # kopie na atrapę NAS: pełna i podstawowa (jak ze starszej wersji)
    db.set_setting(conn, "backup_passphrase", "dlugie-haslo-kopii")
    db.set_setting(conn, "backup_target", "smb")
    db.set_setting(conn, "backup_dir", os.path.join(TMP, "staging"))
    record = export.run(conn, actor="test")
    check("kopia na NAS: pełna, zaszyfrowana", record["ok"] and record["detail"] == "pełna kopia, zaszyfrowana",
          record["detail"])
    check("opis kopii przetłumaczony w dzienniku", i18n.logged("pełna kopia, zaszyfrowana") == "pełna kopia, zaszyfrowana")
    time.sleep(1.1)
    db.set_setting(conn, "backup_passphrase", "")
    record_basic = export.run(conn, actor="test", target="both")      # NAS i Pi
    check("kopia bez hasła: podstawowa", record_basic["detail"] == "bez szyfrowania", record_basic["detail"])
    names = export.list_smb(db.settings(conn))
    check("na NAS dwie kopie", len(names) == 2, str(names))
    full_name = next(n for n in names if n.endswith(".enc"))
    basic_name = next(n for n in names if not n.endswith(".enc"))

    # "NAS i Pi": odtworzenie bierze kopie z Pi, NAS moze spac
    first = fresh("local-first.db")
    for key in ("smb_host", "smb_share", "smb_path", "smb_user", "backup_dir"):
        db.set_setting(first, key, db.settings(conn)[key])
    db.set_setting(first, "smb_pass", "zle")          # NAS odrzuca - nie powinno byc potrzebne
    source = conn.execute("SELECT * FROM backups WHERE id = ?", (record_basic["id"],)).fetchone()
    with first:
        first.execute("INSERT INTO backups(ts, target, path, bytes, encrypted, ok, detail, sha256) "
                      "VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
                      (source["ts"], source["target"], source["path"], source["bytes"],
                       source["encrypted"], source["detail"], source["sha256"]))
    copy_id = first.execute("SELECT MAX(id) FROM backups").fetchone()[0]
    summary = export.restore_from(first, f"id:{copy_id}", "", False, "test")
    check("kopia „NAS + Pi”: odtworzenie bierze plik z Pi (bez NAS)", summary["peers"] == 2,
          str(summary))
    working = os.path.join(TMP, "staging", basic_name)
    original = open(working, "rb").read()
    with open(working, "wb") as handle:
        handle.write(b"{}")
    try:
        export.restore_from(first, f"id:{copy_id}", "", False, "test")
        reached = False
    except ValueError as exc:
        reached = "LOGON_FAILURE" in str(exc)
    check("uszkodzona kopia na Pi: panel jej nie ufa i sięga na NAS", reached)
    with open(working, "wb") as handle:
        handle.write(original)

    print("\n2. Odtworzenie w panelu (nowe Pi z nowym kluczem)")
    NEW_KEY = wg_key()
    with open(os.path.join(WGDIR, "server_private.key"), "w", encoding="utf8") as handle:
        handle.write(NEW_KEY + "\n")
    os.remove(os.path.join(CA_DIR, "ca.key"))
    new = fresh("new.db")
    for key in ("smb_host", "smb_share", "smb_path", "smb_user", "smb_pass", "backup_dir"):
        db.set_setting(new, key, db.settings(conn)[key])
    summary = export.restore_from(new, "smb:" + basic_name, "", False, "test")
    identity = export.apply_identity(new, summary)
    check("kopia podstawowa na Pi z innym kluczem: werdykt „każdy potrzebuje nowego pliku”",
          identity["verdict"] == "changed", str(identity))
    summary = export.restore_from(new, "smb:" + full_name, "dlugie-haslo-kopii", True, "test")
    check("klucz serwera nie trafia do dziennika",
          not any(OLD_KEY in r["detail"] for r in new.execute("SELECT detail FROM audit")))
    identity = export.apply_identity(new, summary)
    check("pełna kopia: klucz serwera przywrócony", identity["verdict"] == "restored", str(identity))
    on_disk = open(os.path.join(WGDIR, "server_private.key"), encoding="utf8").read().strip()
    check("klucz z kopii jest na dysku Pi", on_disk == OLD_KEY)
    check("urząd certyfikacji: przywrócony albo nowy, gdy nie obejmuje nazwy Pi",
          identity["ca"] in ("restored", "new") and os.path.exists(os.path.join(CA_DIR, "ca.key")),
          str(identity))
    names = helperclient.call("tls", {"settings": model.settings_only(new)})
    result = helperclient.call("system_import", {
        "settings": model.settings_only(new), "server_key": OLD_KEY,
        "ca": {"crt": "-----BEGIN CERTIFICATE-----\nSTARYURZAD\n-----END CERTIFICATE-----\n",
               "key": "-----BEGIN PRIVATE KEY-----\nX\n-----END PRIVATE KEY-----\n",
               "json": json.dumps({"permitted": names["permitted"], "dns": names["dns"]})}})
    check("urząd z kopii obejmujący nazwę i sieć Pi zostaje (bez nowego ca.crt na telefonach)",
          result["ca_restored"] and "STARYURZAD" in open(os.path.join(CA_DIR, "ca.crt")).read(),
          str(result))
    settings = db.settings(new)
    check("hasła wróciły (NAS, ntfy)", settings.get("smb_pass") == "tajne"
          and settings.get("alerts_token") == "tk_tajny")
    check("historia wróciła", new.execute("SELECT COUNT(*) FROM traffic").fetchone()[0] == 1)
    check("konta wróciły", [r[0] for r in new.execute("SELECT username FROM users")] == ["admin"])
    labels = [r["detail"] for r in new.execute("SELECT detail FROM backups")]
    check("lista kopii: migawka sprzed odtworzenia zostaje", "kopia stanu przed odtworzeniem" in labels,
          str(labels))
    check("zapamiętany klucz publiczny = klucz z kopii", db.settings(new).get("server_pubkey")
          == helperclient.call("server_pubkey", {"settings": model.settings_only(new)})["pubkey"])
    summary = export.restore_from(new, "smb:" + basic_name, "", False, "test")
    check("kopia podstawowa przy tym samym kluczu: werdykt „pliki działają”",
          export.apply_identity(new, summary)["verdict"] == "same")
    try:
        helperclient.call("system_import", {"settings": model.settings_only(new), "server_key": "zly"})
        check("helper odrzuca coś, co nie jest kluczem", False)
    except helperclient.HelperError as exc:
        check("helper odrzuca coś, co nie jest kluczem", "klucz" in str(exc), str(exc))

    adopt = fresh("adopt.db")
    for key in ("smb_host", "smb_share", "smb_path", "smb_user", "smb_pass", "backup_passphrase"):
        db.set_setting(adopt, key, db.settings(conn)[key] or "dlugie-haslo-kopii")
    listed = export.list_smb(db.settings(adopt))
    check("„Sprawdź kopie na NAS”: nieznane kopie trafiają do historii",
          export.adopt_smb(adopt, listed) == 2 and export.adopt_smb(adopt, listed) == 0)
    adopted = list(adopt.execute("SELECT * FROM backups ORDER BY ts DESC"))
    check("z sumą kontrolną z pliku .sha256 (da się sprawdzić)", all(r["sha256"] for r in adopted)
          and export.verify(adopt, adopted[0]["id"])["ok"], str([dict(r) for r in adopted]))
    scanned = export.scan_smb(adopt)
    check("„Odśwież listę z NAS”: wynik zapamiętany (bez ponownego budzenia NAS)",
          scanned["count"] == 2 and scanned["added"] == 0
          and export.last_scan(db.settings(adopt))["names"] == listed)
    check("zapamiętana lista nie trafia do kopii (inne Pi, inna lista)", "smb_scan" in export.RUNTIME_SETTINGS)
    workdir = os.path.join(TMP, "roboczy")
    os.makedirs(workdir, exist_ok=True)
    db.set_setting(adopt, "backup_dir", workdir)
    dropped = "wg-panel-20260101-120000.json"
    with open(os.path.join(workdir, dropped), "w", encoding="utf8") as handle:
        handle.write("{}")
    export.write_sidecar(os.path.join(workdir, dropped), dropped, export.sha256(b"{}"))
    check("plik położony ręcznie w katalogu roboczym trafia na listę (raz)",
          export.adopt_local(adopt) == 1 and export.adopt_local(adopt) == 0)
    found = adopt.execute("SELECT * FROM backups WHERE path LIKE ?", ("%" + dropped,)).fetchone()
    check("… z sumą z pliku .sha256 i opisem „znaleziona: Pi” (nazwa serwera z ustawień)",
          found["sha256"] == export.sha256(b"{}") and found["detail"] == "znaleziona: Pi"
          and found["target"] == "local")
    victim = next(r for r in adopted if not r["path"].endswith(".enc"))
    summary = export.delete_backups(adopt, [victim["id"]])
    check("kopię sprzed reinstalacji da się usunąć z panelu (także z NAS)",
          summary["remote"] == 2 and basic_name not in export.list_smb(db.settings(adopt)), str(summary))
    # przywracamy plik dla dalszych testow
    record_basic = export.run(conn, actor="test")
    names = export.list_smb(db.settings(conn))
    basic_name = next(n for n in names if not n.endswith(".enc"))

    print("\n3. restore_cli (install.sh --restore)")
    os.remove(os.path.join(WGDIR, "server_private.key"))
    for name in ("ca.key", "ca.crt"):
        os.remove(os.path.join(CA_DIR, name))
    answers = {"WGPANEL_RESTORE_HOST": "127.0.0.1", "WGPANEL_RESTORE_SHARE": "backup",
               "WGPANEL_RESTORE_PATH": "nasbackup", "WGPANEL_RESTORE_USER": "nasbackup",
               "WGPANEL_RESTORE_PASS": "tajne", "WGPANEL_RESTORE_PASSPHRASE": "dlugie-haslo-kopii",
               # nigdy nie zmieniamy nazwy systemu, na ktorym biegna testy; adres jak w kopii
               "WGPANEL_RESTORE_HOSTNAME": "n",
               "WGPANEL_RESTORE_LOCAL_IPS": db.settings(conn).get("server_lan") or ""}
    names = export.list_smb(db.settings(conn))
    pick = str(names.index(full_name) + 1)

    def cli(*args, **extra):
        return subprocess.run([sys.executable, "-m", "panel.restore_cli", *args], cwd=ROOT,
                              env={**env, "INSTALL_LANG": "pl", **answers, **extra}, capture_output=True, text=True,
                              timeout=60)

    out_file = os.path.join(TMP, "restore.json")
    result = cli("fetch", out_file, WGPANEL_RESTORE_PICK=pick)
    check("fetch pełnej kopii: kod 0", result.returncode == 0, result.stdout + result.stderr)
    check("fetch: klucz serwera na miejscu przed instalacją",
          open(os.path.join(WGDIR, "server_private.key")).read().strip() == OLD_KEY)
    check("fetch: urząd certyfikacji na miejscu", os.path.exists(os.path.join(CA_DIR, "ca.key")))
    stored = open(out_file).read()
    check("fetch: plik dla instalatora bez klucza serwera", OLD_KEY not in stored and "system" not in json.loads(stored))
    check("fetch: plik tylko dla właściciela", oct(os.stat(out_file).st_mode)[-3:] == "600")
    check("fetch: mówi, że stare pliki osób zadziałają", "dotychczasowe pliki" in result.stdout, result.stdout)

    result = cli("fetch", out_file, WGPANEL_RESTORE_PICK=str(names.index(basic_name) + 1))
    check("fetch kopii podstawowej: kod 3 (instalator zapyta o klucz)", result.returncode == 3,
          result.stdout + result.stderr)
    result = cli("fetch", out_file, WGPANEL_RESTORE_PICK=pick, WGPANEL_RESTORE_PASSPHRASE="zle")
    check("fetch ze złym hasłem kopii: przerwane", result.returncode == 1)
    result = cli("fetch", out_file, WGPANEL_RESTORE_PASS="zle")
    check("złe hasło NAS: po ludzku", result.returncode == 1 and "NAS: odrzucony login albo hasło" in result.stderr
          and "NAS: NAS" not in result.stderr,
          result.stderr)
    result = cli("fetch", out_file, WGPANEL_RESTORE_PATH="wg-panel")
    check("zły katalog: podpowiada istniejące", "nie ma katalogu wg-panel - są tam: nasbackup, photos"
          in result.stderr, result.stderr)
    result = cli("fetch", out_file, WGPANEL_RESTORE_SHARE="kopie")
    check("zły udział: po ludzku", "nie ma udziału kopie" in result.stderr, result.stderr)

    cli("fetch", out_file, WGPANEL_RESTORE_PICK=pick)
    target_db = os.path.join(TMP, "installed.db")
    machine = {"server_lan": "10.0.2.15", "lan": "10.0.2.0/24", "wan_if": "enp0s3"}
    result = cli("import", out_file, WGPANEL_DB=target_db, WGPANEL_DETECT=json.dumps(machine))
    check("import do nowej bazy", result.returncode == 0, result.stdout + result.stderr)
    imported = dict(sqlite3.connect(target_db).execute(
        "SELECT key, value FROM settings WHERE key IN ('server_lan', 'lan', 'wan_if')").fetchall())
    check("import na innej maszynie: adres, interfejs i sieć z maszyny, nie z kopii",
          imported == machine and "wzięte z tej maszyny" in result.stdout, str(imported) + result.stdout)
    test_db = os.path.join(TMP, "testowy.db")
    result = cli("import", out_file, "--test", WGPANEL_DB=test_db, WGPANEL_DETECT=json.dumps(machine))
    check("import --test: serwer testowy", result.returncode == 0 and "Serwer TESTOWY" in result.stdout
          and sqlite3.connect(test_db).execute("SELECT value FROM settings WHERE key='test_mode'").fetchone()[0] == "1",
          result.stdout + result.stderr)
    check("zwykły import: właściwy serwer", sqlite3.connect(target_db).execute(
        "SELECT value FROM settings WHERE key='test_mode'").fetchone()[0] == "0")
    installed = sqlite3.connect(target_db)
    check("import: osoby, konta, hasła", [r[0] for r in installed.execute("SELECT name FROM peers ORDER BY ip")]
          == ["Ja", "Anna"] and installed.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
          and installed.execute("SELECT value FROM settings WHERE key='smb_pass'").fetchone()[0] == "tajne")
    result = cli("apply", WGPANEL_DB=target_db)
    check("apply: tunel z odtworzonej konfiguracji", result.returncode == 0 and "Tunel podniesiony" in result.stdout,
          result.stdout + result.stderr)
    conf = open(os.path.join(WGDIR, "wg0.conf"), encoding="utf8").read()
    check("wg0.conf z kluczem z kopii i osobami", OLD_KEY in conf and "J" * 43 + "=" in conf)

    print("\n3b. Kopia z pliku, próba odtworzenia, opcje instalatora")
    install_conf = os.path.join(TMP, "install.conf")
    with open(install_conf, "w", encoding="utf8") as handle:
        handle.write("# opcje\nLAN_ACCESS=192.168.50.0/24\nBIND_PORT=8443\nTLS=1\nINSTALL_LANG=pl\n"
                     "BIND_HOST=10.0.0.1\nCOS_INNEGO=x\n")
    export.INSTALL_CONF = install_conf
    blob, encrypted, kind = export.pack(conn, "dlugie-haslo-kopii")
    data = export.parse(blob, "dlugie-haslo-kopii")
    check("pełna kopia niesie opcje instalatora (tylko znane klucze)",
          data.get("install") == {"LAN_ACCESS": "192.168.50.0/24", "BIND_PORT": "8443", "TLS": "1",
                                  "INSTALL_LANG": "pl", "BIND_HOST": "10.0.0.1"}, str(data.get("install")))
    check("pełna kopia niesie nazwę serwera", data.get("host") == {"name": export.host_name()}, str(data.get("host")))
    basic = export.parse(export.pack(conn, "")[0])
    check("kopia podstawowa bez opcji i nazwy (jak dotąd)", "install" not in basic and "host" not in basic)
    local = os.path.join(TMP, "z-pendrive", "wg-panel-kopia.json.enc")
    os.makedirs(os.path.dirname(local))
    open(local, "wb").write(blob)
    open(local + ".sha256", "w").write(f"{export.sha256(blob)}  {os.path.basename(local)}\n")

    before = {name: open(os.path.join(WGDIR, name), "rb").read() for name in ("server_private.key",)}
    listing = sorted(os.listdir(WGDIR)), sorted(os.listdir(CA_DIR))
    result = cli("check", local)
    check("próba: pokazuje wersję panelu z kopii", "wersja panelu" in result.stdout
          and open(os.path.join(ROOT, "VERSION")).read().strip() in result.stdout, result.stdout)
    check("próba z pliku: kod 0, opis zawartości", result.returncode == 0 and "8443" in result.stdout
          and "pełna" in result.stdout and "tak" in result.stdout and "Nic nie zostało zmienione" in result.stdout,
          result.stdout + result.stderr)
    check("próba niczego nie zapisuje", (sorted(os.listdir(WGDIR)), sorted(os.listdir(CA_DIR))) == listing
          and all(open(os.path.join(WGDIR, n), "rb").read() == v for n, v in before.items()))
    result = cli("check", local, WGPANEL_RESTORE_PASSPHRASE="zle-haslo")
    check("próba ze złym hasłem: kod 1, po ludzku", result.returncode == 1
          and "nie udało się otworzyć kopii" in result.stderr, result.stderr)
    result = cli("check", WGPANEL_RESTORE_PICK=pick)
    check("próba z udziału NAS (bez pliku)", result.returncode == 0 and "Nic nie zostało zmienione" in result.stdout,
          result.stdout + result.stderr)
    result = cli("check", os.path.join(TMP, "nie-ma.enc"))
    check("próba: brak pliku - kod 1", result.returncode == 1 and "nie ma pliku" in result.stderr, result.stderr)
    bad = os.path.join(TMP, "z-pendrive", "uszkodzona.json.enc")
    open(bad, "wb").write(blob[:-5] + b"xxxxx")
    open(bad + ".sha256", "w").write(f"{export.sha256(blob)}  uszkodzona.json.enc\n")
    result = cli("check", bad)
    check("uszkodzony plik (suma .sha256 obok się nie zgadza): kod 1", result.returncode == 1
          and "suma kontrolna" in result.stderr, result.stderr)

    out_file = os.path.join(TMP, "restore2.json")
    result = cli("fetch", out_file, local)
    check("fetch z pliku: kod 0, bez pytań o udział", result.returncode == 0 and "Kopia z pliku" in result.stdout
          and "Adres NAS" not in result.stdout, result.stdout + result.stderr)
    options = open(out_file + ".options", encoding="utf8").read()
    check("fetch: opcje instalatora dla install.sh (plik tylko dla właściciela)",
          "BIND_PORT=8443\n" in options and "LAN_ACCESS=192.168.50.0/24\n" in options and "COS_INNEGO" not in options
          and oct(os.stat(out_file + ".options").st_mode)[-3:] == "600", options)
    check("fetch: plik dla importu bez opcji i nazwy", not {"install", "host", "system"} & set(json.load(open(out_file))))

    real_host = export.host_name
    export.host_name = lambda: "stary-pi"
    try:
        other = export.pack(conn, "dlugie-haslo-kopii")[0]
    finally:
        export.host_name = real_host
    other_path = os.path.join(TMP, "z-pendrive", "z-innego-pi.json.enc")
    open(other_path, "wb").write(other)
    result = cli("check", other_path, WGPANEL_RESTORE_LOCAL_IPS="192.168.50.99")
    check("próba: inna nazwa serwera - ostrzega o ca.crt", 'nazwie "stary-pi"' in result.stdout
          and "ca.crt" in result.stdout, result.stdout)
    server_lan = db.settings(conn).get("server_lan")
    check("próba: inny adres w LAN - mówi, co zrobić", f"adres serwera w LAN to {server_lan}" in result.stdout
          and "192.168.50.99" in result.stdout and "rezerwację DHCP" in result.stdout, result.stdout)
    result = cli("fetch", out_file, other_path)
    check("fetch: inna nazwa - pyta o ustawienie nazwy (odpowiedź n: bez zmian)",
          result.returncode == 0 and "Nazwa zostaje" in result.stdout
          and "Nazwa ustawiona" not in result.stdout, result.stdout)
    old_style = dict(data)
    old_style.pop("host")
    old_style["system"] = dict(old_style["system"], ca=dict(old_style["system"]["ca"], json='{"dns": ["dawny"]}'))
    from panel import restore_cli
    check("starsza kopia (bez nazwy): nazwa z urzędu certyfikacji", restore_cli.backup_host(old_style) == "dawny")
    no_ca = dict(data, host={"name": "inny"}, system=dict(data["system"], ca=None))
    os.environ["WGPANEL_RESTORE_LOCAL_IPS"] = server_lan or ""
    check("kopia bez urzędu: nazwa nie ma znaczenia", "host" not in restore_cli.differences(no_ca))
    del os.environ["WGPANEL_RESTORE_LOCAL_IPS"]

    print("\n4. Instalator: pytanie o klucz serwera")
    source = open(os.path.join(ROOT, "install.sh"), encoding="utf8").read()
    fragment = source[source.index("key_menu() {"):source.index("# --- koniec key_menu ---")]
    prelude = ('t() { printf "%s" "$1"; }\ninfo() { printf "%s\\n" "$1"; }\nwarn() { printf "%s\\n" "$1"; }\n'
               'die() { printf "DIE %s\\n" "$1"; exit 1; }\nNEW_KEY=0\n')

    def menu(key_file: str, stdin: str = "", **extra) -> tuple:
        script = prelude + fragment + '\nkey_menu\necho "NEW_KEY=$NEW_KEY"\n'
        out = subprocess.run(["bash", "-c", script], input=stdin, capture_output=True, text=True,
                             env=dict(env, KEY_FILE=key_file, **extra))
        return out.returncode, out.stdout + out.stderr

    key_file = os.path.join(TMP, "k1", "server_private.key")
    code, out = menu(key_file, SERVER_KEY="new")
    check("SERVER_KEY=new: nowy klucz, ostrzeżenie o nowych plikach",
          code == 0 and os.path.exists(key_file) and "NEW_KEY=1" in out and "nowego pliku" in out, out)
    check("nowy klucz tylko dla roota (600)", oct(os.stat(key_file).st_mode)[-3:] == "600")
    key_file = os.path.join(TMP, "k2", "server_private.key")
    code, out = menu(key_file, OLD_KEY + "\n", SERVER_KEY="paste")
    check("SERVER_KEY=paste: wklejony klucz zapisany, pokazany klucz publiczny",
          code == 0 and open(key_file).read().strip() == OLD_KEY and "Klucz publiczny serwera" in out, out)
    code, out = menu(os.path.join(TMP, "k3", "k"), "to-nie-klucz\n", SERVER_KEY="paste")
    check("wklejone śmieci: odmowa", code == 1 and "to nie jest klucz prywatny" in out, out)
    code, out = menu(os.path.join(TMP, "k4", "k"))
    check("bez odpowiedzi: jasna instrukcja (SERVER_KEY albo --restore)",
          code == 1 and "SERVER_KEY=new" in out and "--restore" in out, out)
    check("instalator: --restore przed pytaniem o klucz, import przed startem usług",
          source.index("restore_cli fetch") < source.index("\n    key_menu\n")
          < source.index("restore_cli import") < source.index("systemctl restart wg-panel-helper")
          < source.index("restore_cli apply"))
    check("instalator: składnia", subprocess.run(["bash", "-n", os.path.join(ROOT, "install.sh")]).returncode == 0)

    print("\n5. Panel: jaka kopia powstaje")
    box = ui.backup_kind_box({"backup_passphrase": ""})
    check("bez hasła: stała informacja „PODSTAWOWA”, czerwona kropka (nie znikający pasek)",
          "dot dead" in box and "PODSTAWOWA" in box and "banner" not in box)
    box = ui.backup_kind_box({"backup_passphrase": "krotkie"})
    check("krótkie hasło: ostrzeżenie", "dot warn" in box and "słabe hasło" in box)
    box = ui.backup_kind_box({"backup_passphrase": "dlugie-haslo-kopii"})
    check("dobre hasło: kopie pełne, przypomnienie o menedżerze haseł",
          "dot ok" in box and "PEŁNA" in box and "menedżerze haseł" in box)
    i18n.set_lang("en")
    check("EN bez polskiego", "FULL, encrypted" in ui.backup_kind_box({"backup_passphrase": "dlugie-haslo-kopii"}))
    i18n.set_lang("pl")
    rows = [{"ok": 1, "path": "//nas/backup/x/wg-panel-20261012-105012.json.enc", "id": 7, "target": "smb",
             "detail": "pełna kopia, zaszyfrowana", "encrypted": 1, "verified_at": 1, "verify_ok": 1,
             "ts": 1791795012, "bytes": 9000, "sha256": "ab" * 32},
            {"ok": 0, "path": "", "id": 8, "target": "smb", "detail": "blad", "encrypted": 1,
             "verified_at": None, "verify_ok": None, "ts": 1791795100, "bytes": 0, "sha256": ""},
            {"ok": 1, "path": "//nas/backup/x/wg-panel-20261001-033000.json.enc", "id": 9, "target": "smb",
             "detail": "pełna kopia bez klucza serwera (helper nie odpowiadał), zaszyfrowana", "encrypted": 1,
             "verified_at": None, "verify_ok": None, "ts": 1790000000, "bytes": 9000, "sha256": "cd" * 32},
            {"ok": 1, "path": os.path.join(TMP, "nie-ma", "wg-panel-20261002-033000.json"), "id": 10,
             "target": "local", "detail": "bez szyfrowania", "encrypted": 0, "verified_at": None,
             "verify_ok": None, "ts": 1790100000, "bytes": 9000, "sha256": "ef" * 32},
            {"ok": 1, "path": "//nas/backup/x/wg-panel-20261013-033000.json.enc", "id": 11, "target": "smb",
             "detail": "pełna kopia, zaszyfrowana", "encrypted": 1, "verified_at": None, "verify_ok": None,
             "ts": 1791900000, "bytes": 9000, "sha256": "01" * 32}]
    scan = {"ts": 1791795050, "names": ["wg-panel-20261012-105012.json.enc"]}
    items = {item["id"]: item for item in ui.backup_items(rows, ["wg-panel-20261012-105012.json.enc"], scan)}
    check("jedna lista: kopia na NAS z kopią roboczą to jeden wiersz „NAS + Pi”",
          items[7]["where"] == {"smb", "local"} and items[7]["kind"] == "full" and items[7]["restorable"],
          str(items[7]))
    check("nieudana: na liście (da się usunąć), ale nie do odtworzenia",
          items[8]["kind"] == "failed" and not items[8]["restorable"])
    check("starsza od odświeżenia listy i nie ma jej na NAS: „brak na NAS”",
          items[9]["gone"] == {"smb"} and not items[9]["restorable"])
    check("kopia bez klucza serwera nie udaje pełnej: „pełna bez klucza”", items[9]["kind"] == "nokey"
          and "pełna bez klucza" in ui.backup_row(items[9]))
    check("nowsza od odświeżenia listy: nie oznaczona jako brakująca",
          items[11]["where"] == {"smb"} and not items[11]["gone"])
    check("plik na Pi sprzątnięty: „plik usunięty z Pi”, nie do odtworzenia",
          items[10]["gone"] == {"local"} and items[10]["kind"] == "basic" and not items[10]["restorable"])
    row_html = ui.backup_row(items[7])
    check("wiersz: pole wyboru (da się odznaczyć), nie radio",
          'type="checkbox"' in row_html and 'type="radio"' not in row_html and 'form="backup-pick"' in row_html)
    cfg_page = {"backup_passphrase": "dlugie-haslo-kopii", "backup_target": "smb", "backup_enabled": "1",
                "smb_host": "nas", "smb_share": "backup"}
    page_html = ui.backups_page({"username": "t", "csrf": "c"}, cfg_page, rows, [], scan=scan)
    check("jeden przycisk NAS (odświeżenie listy) i żadnego „Sprawdź kopie”",
          page_html.count('action="/backups/scan"') == 1 and "Sprawdź kopie" not in page_html
          and page_html.count("Odśwież listę · NAS") == 1)
    check("akcje na zaznaczonych: sprawdź, odtwórz, usuń (usuwanie pyta)",
          'data-verify' in page_html and 'data-restore' in page_html
          and re.search(r'formaction="/backups/delete"[^>]*data-confirm=', page_html))
    check("okno odtwarzania: hasło kopii i „Logowanie starym hasłem”",
          'id="restore-dialog"' in page_html and 'name="passphrase"' in page_html
          and "Logowanie starym hasłem" in page_html and 'name="with_users"' in page_html)
    check("legenda pod listą: co znaczy każda plakietka (zwinięta)",
          '<details class="more legend">' in page_html and "Po awarii odtwarzaj najnowszą" in page_html
          and "brak pliku" in page_html and "uszkodzona" in page_html)
    check("podpowiedź przy plakietce rodzaju", 'title="Wszystko, razem z kluczem serwera' in ui.backup_row(items[7]))
    row9 = ui.backup_row(items[9])
    check("wiersz: jedna plakietka rodzaju i jedna stanu, bez dopisków",
          row9.count('class="badge') == 2 and "brak pliku" in row9 and "<td class=\"state\">" in row9, row9)
    check("stan na górze: rodzaj, harmonogram, ostatnia kopia",
          "Rodzaj kopii" in page_html and "codziennie o 03:30" in page_html and "Ostatnia kopia: " in page_html)
    failed_first = [dict(rows[1], ts=1792000000)] + rows
    check("ostatnia kopia nieudana: czerwona kropka na górze",
          "Ostatnia kopia NIEUDANA" in ui.backup_status(cfg_page, failed_first))
    page_html = ui.backups_page({"username": "t", "csrf": "c"}, {}, [], [])
    check("nowy system: podpowiedź, a przycisk NAS nieaktywny bez danych NAS",
          "Panel nie zna jeszcze żadnej kopii" in page_html
          and re.search(r"<button disabled[^>]*>Odśwież listę · NAS", page_html))
finally:
    helper.terminate()

print("\n" + "=" * 60)
if FAILURES:
    print(f"NIEUDANE ({len(FAILURES)}):")
    for item in FAILURES:
        print("  -", item)
    sys.exit(1)
print("Wszystkie testy przeszly.")
