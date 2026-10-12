#!/usr/bin/env python3
"""Prawa plikow miedzy kontami: wg-panel (panel, straznik) i czlonek grupy (np. pi z wgctl).

Wymaga roota (sudo) - dzieci procesu przechodza na udawane konta (numery uid/gid), bez
zakladania prawdziwych uzytkownikow. Odtwarza blad: "wgctl guard" jako pi konczyl sie
PermissionError, bo straznik (bez UMask) zalozyl blokade z prawami 0644.
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
if os.geteuid() != 0:
    print("Pomijam: test praw wymaga roota (sudo python3 tests/test_permissions.py).")
    sys.exit(0)

from panel import db  # noqa: E402

WGP, PI = 65101, 65102            # "wg-panel" oraz "pi" w grupie wg-panel
FAILURES = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  [{'OK  ' if condition else 'BLAD'}] {label}"
          + (f"  ({detail})" if detail and not condition else ""))
    if not condition:
        FAILURES.append(label)


def setup(backups_mode: int) -> tuple:
    base = tempfile.mkdtemp(prefix="wgpanel-perm-")
    os.chmod(base, 0o755)
    data = os.path.join(base, "data")
    os.makedirs(os.path.join(data, "backups"))
    path = os.path.join(data, "panel.db")
    conn = db.connect(path)
    db.init_defaults(conn)
    db.set_setting(conn, "backup_dir", os.path.join(data, "backups"))
    conn.close()
    for root, dirs, files in os.walk(data):
        for name in dirs + files:
            os.chown(os.path.join(root, name), WGP, WGP)
    os.chown(data, WGP, WGP)
    os.chmod(data, 0o2770)
    os.chmod(os.path.join(data, "backups"), backups_mode)
    for name in os.listdir(data):
        if name.startswith("panel.db"):
            os.chmod(os.path.join(data, name), 0o660)
    # stan jak na Pi przed poprawka: straznik bez UMask zalozyl pliki 0644
    for name, content in (("panel.db.schedule.lock", ""), ("watchdog.json", "{}")):
        with open(os.path.join(data, name), "w", encoding="utf8") as handle:
            handle.write(content)
        os.chown(os.path.join(data, name), WGP, WGP)
        os.chmod(os.path.join(data, name), 0o644)
    return base, data, path


def as_account(uid: int, gid: int):
    def switch():
        os.setgroups([WGP])
        os.setgid(gid)
        os.setuid(uid)
        os.umask(0o022)            # zwykla maska konta w terminalu
    return switch


def wgctl(path: str, base: str, account, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, WGPANEL_DB=path, WGPANEL_SOCKET=os.path.join(base, "brak.sock"),
               PYTHONPATH=ROOT, HOME=base)
    return subprocess.run([sys.executable, os.path.join(ROOT, "wgctl"), *args], env=env,
                          preexec_fn=account, capture_output=True, text=True, timeout=60)


PI_ACC = as_account(PI, PI)
WGP_ACC = as_account(WGP, WGP)

print("\n1. wgctl jako pi przy plikach zalozonych przez straznika (0644)")
base, data, path = setup(0o2770)
for args in (("guard",), ("expire",), ("backup",), ("backup", "--verify")):
    out = wgctl(path, base, PI_ACC, *args)
    check(f"wgctl {' '.join(args)} - bez bledu Pythona", "Traceback" not in out.stderr
          and out.returncode == 0, (out.stdout + out.stderr).strip().splitlines()[-1:])

print("\n2. Oba kierunki: pliki jednego konta dzialaja dla drugiego")
backups = os.path.join(data, "backups")
made = sorted(f for f in os.listdir(backups) if f.endswith(".json"))
info = os.stat(os.path.join(backups, made[-1])) if made else None
check("kopia zrobiona przez pi nalezy do grupy wg-panel, prawa 640",
      info is not None and info.st_gid == WGP and stat.S_IMODE(info.st_mode) == 0o640,
      f"{info and info.st_gid} {info and oct(stat.S_IMODE(info.st_mode))}")
out = wgctl(path, base, WGP_ACC, "backup", "--verify")
check("panel (wg-panel) sprawdza kopie zrobiona przez pi", out.returncode == 0
      and "zgodna" in out.stdout, out.stdout + out.stderr)
out = wgctl(path, base, WGP_ACC, "guard")
state = os.stat(os.path.join(data, "watchdog.json"))
check("straznik (wg-panel) nadpisuje stan zapisany przez pi", "Traceback" not in out.stderr
      and state.st_uid == WGP and stat.S_IMODE(state.st_mode) == 0o660,
      f"{state.st_uid} {oct(stat.S_IMODE(state.st_mode))}")
out = wgctl(path, base, PI_ACC, "guard")
check("i odwrotnie: pi nadpisuje stan straznika", "Traceback" not in out.stderr
      and os.stat(os.path.join(data, "watchdog.json")).st_uid == PI)
shutil.rmtree(base)

print("\n3. Katalog kopii z wczesniejszej wersji (2700)")
base, data, path = setup(0o2700)
out = wgctl(path, base, PI_ACC, "backup")
check("wgctl backup: czytelny komunikat zamiast bledu Pythona",
      "Traceback" not in out.stderr and "zainstaluj panel ponownie" in (out.stdout + out.stderr),
      (out.stdout + out.stderr)[-200:])
shutil.rmtree(base)

print("\n4. Instalator i uslugi")
unit = open(os.path.join(ROOT, "systemd", "wg-panel-guard.service"), encoding="utf8").read()
check("usluga straznika ma UMask=0007 jak panel", "UMask=0007" in unit)
source = open(os.path.join(ROOT, "install.sh"), encoding="utf8").read()
check("instalator: katalog kopii 2770 i naprawa praw z wczesniejszych wersji",
      'chmod 2770 "$DATA/backups"' in source and "watchdog.json" in source
      and "panel.db.schedule.lock" in source)

print("\n" + "=" * 60)
if FAILURES:
    print(f"NIEUDANE ({len(FAILURES)}):")
    for item in FAILURES:
        print("  -", item)
    sys.exit(1)
print("Wszystkie testy przeszly.")
