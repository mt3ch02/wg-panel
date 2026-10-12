#!/usr/bin/env python3
"""Terminy dostępu niezależne od panelu (`wgctl expire`) i zmiana punktowa.

Helper działa naprawdę (atrapy zamiast wg/iptables), panelu NIE ma - tak jak po awarii.
Najważniejsze: wygaśnięcie dostępu nie wypycha na interfejs niezastosowanych zmian
administratora (wcześniej wypychało cały stan panelu bez bezpiecznika).
"""
from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
TMP = tempfile.mkdtemp(prefix="wgpanel-schedule-")
BIN, WGDIR = os.path.join(TMP, "bin"), os.path.join(TMP, "wireguard")
os.makedirs(BIN)
os.makedirs(WGDIR)
FAILURES = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  [{'OK  ' if condition else 'BLAD'}] {label}"
          + (f"  ({detail})" if detail and not condition else ""))
    if not condition:
        FAILURES.append(label)


def stub(name: str, script: str) -> None:
    path = os.path.join(BIN, name)
    with open(path, "w", encoding="utf8") as handle:
        handle.write("#!/bin/bash\n" + script)
    os.chmod(path, 0o755)


stub("wg", 'case "$1" in show) exit 0;; pubkey) cat >/dev/null; head -c 32 /dev/urandom | base64;; '
           'genkey|genpsk) head -c 32 /dev/urandom | base64;; esac\nexit 0\n')
stub("wg-quick", 'if [ "$1" = "strip" ]; then cat %s/wg0.conf; fi\nexit 0\n' % WGDIR)
for tool in ("iptables", "ip6tables"):
    stub(tool, 'for a in "$@"; do if [ "$a" = "-C" ]; then exit 1; fi; done\nexit 0\n')
for tool in ("iptables-restore", "ip6tables-restore", "ip"):
    stub(tool, "exit 0\n")
stub("systemctl", 'if [ "$1" = "is-active" ]; then echo active; fi\nexit 0\n')
with open(os.path.join(WGDIR, "server_private.key"), "w", encoding="utf8") as handle:
    handle.write("KLUCZPRYWATNYSERWERA00000000000000000000000=\n")

DB = os.path.join(TMP, "panel.db")
env = dict(os.environ)
env.update({"PATH": BIN + os.pathsep + env["PATH"], "WGPANEL_DB": DB,
            "WGPANEL_SOCKET": os.path.join(TMP, "helper.sock"), "WGPANEL_WGDIR": WGDIR,
            "PYTHONPATH": ROOT})
os.environ.update({"WGPANEL_SOCKET": env["WGPANEL_SOCKET"], "WGPANEL_DB": DB})

from panel import db, helperclient, model  # noqa: E402

helperclient.SOCKET_PATH = env["WGPANEL_SOCKET"]
helper = subprocess.Popen([sys.executable, os.path.join(ROOT, "helper", "wg_panel_helper.py")],
                          env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(1.2)


def conf() -> str:
    return open(os.path.join(WGDIR, "wg0.conf"), encoding="utf8").read()


def add_peer(conn, name: str, ip: str, char: str, **extra) -> str:
    key = char * 43 + "="
    fields = {"state": "active", "access_mode": "none", "expires_at": None, "starts_at": None,
              "auto_state": 0, "created_at": int(time.time())}
    fields.update(extra)
    tier = conn.execute("SELECT id FROM tiers WHERE name = 'NET'").fetchone()["id"]
    with conn:
        conn.execute(
            f"""INSERT INTO peers(name, ip, pubkey, psk, tier_id, {', '.join(fields)})
                VALUES (?, ?, ?, ?, ?, {', '.join('?' for _ in fields)})""",
            (name, ip, key, "P" * 43 + "=", tier, *fields.values()))
    return key


def apply_like_panel(conn, seconds: int = 0) -> dict:
    """To, co robi panel przy "Zastosuj zmiany" (bez przegladarki)."""
    value = model.build(conn)
    result = helperclient.call("apply", dict(value, confirm_seconds=seconds), timeout=60)
    model.mark_applied(conn, value, result)
    return result


def expire() -> str:
    out = subprocess.run([sys.executable, os.path.join(ROOT, "wgctl"), "expire"], env=env,
                         capture_output=True, text=True, timeout=60)
    return (out.stdout + out.stderr).strip()


try:
    conn = db.connect(DB)
    db.init_defaults(conn)
    now = int(time.time())
    guest = add_peer(conn, "Gosc", "10.0.0.20", "G", access_mode="window", expires_at=now + 3600)
    steady = add_peer(conn, "Staly", "10.0.0.21", "S")
    apply_like_panel(conn)
    check("stan wyjsciowy na dysku", guest in conf() and steady in conf())

    print("\n1. Wygasniecie bez panelu, bez wypychania niezastosowanych zmian")
    draft = add_peer(conn, "Szkic", "10.0.0.22", "D")      # administrator jeszcze nie zastosowal
    with conn:
        conn.execute("UPDATE peers SET expires_at = ? WHERE pubkey = ?", (now - 60, guest))
    out = expire()
    check("wgctl expire dziala bez panelu", "zastosowano terminy: Gosc" in out, out)
    check("osoba po terminie zniknela z interfejsu", guest not in conf())
    check("niezastosowana zmiana NIE trafila na interfejs", draft not in conf())
    check("reszta bez zmian", steady in conf())
    row = conn.execute("SELECT state, auto_state FROM peers WHERE pubkey = ?", (guest,)).fetchone()
    check("w bazie: zawieszona przez harmonogram", tuple(row) == ("suspended", 1), str(tuple(row)))
    applied = json.loads(db.settings(conn)["applied_state"])
    states = {p["pubkey"]: p["state"] for p in applied["peers"]}
    check("zapamietany stan: odciecie tak, szkic nie", states.get(guest) == "suspended"
          and draft not in states, str(states))
    check("panel nadal pokazuje niezastosowane zmiany",
          model.render_hash(conn) != db.settings(conn)["applied_hash"])
    log = [tuple(r) for r in conn.execute("SELECT actor, action FROM audit ORDER BY id")]
    check("wpis w dzienniku", ("harmonogram", "apply.auto") in log and ("harmonogram", "peer.expired") in log)
    out = expire()
    check("drugie przejscie niczego nie stosuje", "nic do zrobienia" in out, out)

    print("\n2. Zmiana czekajaca na potwierdzenie")
    with conn:
        conn.execute("DELETE FROM peers WHERE pubkey = ?", (draft,))
    second = add_peer(conn, "Drugi", "10.0.0.23", "E", access_mode="window", expires_at=now + 3600)
    result = apply_like_panel(conn, seconds=30)
    token = result.get("confirm_token", "")
    with conn:
        conn.execute("UPDATE peers SET expires_at = ? WHERE pubkey = ?", (now - 60, second))
    out = expire()
    check("przy oczekujacej zmianie harmonogram czeka", "czeka" in out and second in conf(), out)
    helperclient.call("confirm", {"token": token})
    out = expire()
    check("po potwierdzeniu odcina", "Drugi" in out and second not in conf(), out)

    print("\n3. Otwarcie okna dostepu")
    opening = add_peer(conn, "Okno", "10.0.0.24", "O", access_mode="window", state="suspended",
                       auto_state=1, starts_at=now + 3600, expires_at=now + 86400)
    apply_like_panel(conn)
    check("przed startem okna: brak na interfejsie", opening not in conf())
    with conn:
        conn.execute("UPDATE peers SET starts_at = ? WHERE pubkey = ?", (now - 60, opening))
    out = expire()
    check("start okna: osoba wlaczona na interfejsie", opening in conf(), out)

    print("\n4. Blokada przed podwojnym uruchomieniem")
    with conn:
        conn.execute("UPDATE peers SET expires_at = ? WHERE pubkey = ?", (now - 60, opening))
    with open(DB + ".schedule.lock", "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        out = expire()
        fcntl.flock(lock, fcntl.LOCK_UN)
    check("zajete - pomija minute, niczego nie zmienia", "pominieto" in out and opening in conf(), out)
    out = expire()
    check("po zwolnieniu odcina", opening not in conf(), out)
finally:
    helper.terminate()

print("\n" + "=" * 60)
if FAILURES:
    print(f"NIEUDANE ({len(FAILURES)}):")
    for item in FAILURES:
        print("  -", item)
    sys.exit(1)
print("Wszystkie testy przeszly.")
