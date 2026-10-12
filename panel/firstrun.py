"""Pierwsze uruchomienie: siec domowa wykryta na Pi zamiast wartosci domyslnych.

Instalator wola to tylko przy nowej instalacji (bazy jeszcze nie ma). Wpisuje do bazy siec
domowa (lan), adres Pi w niej (server_lan) i interfejs (wan_if) - zanim panel pierwszy raz
wystartuje, wiec katalog hostow i reguly od razu dotycza prawdziwej sieci. Istniejacej bazy nie
rusza: przy aktualizacji Twoje ustawienia zostaja.

    python3 -m panel.firstrun          wykryj i zapisz (tylko nowa baza)
    python3 -m panel.firstrun --show   tylko pokaz, co wykryto
"""
from __future__ import annotations

import ipaddress
import os
import re
import subprocess
import sys

from . import db

KEYS = ("lan", "server_lan", "wan_if", "nas", "smb_host")


def _run(args: list) -> str:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def detect() -> dict:
    """Interfejs i adres, przez ktore Pi wychodzi do internetu, i siec tego interfejsu."""
    route = _run(["ip", "-4", "route", "get", "1.1.1.1"])
    dev = re.search(r"\bdev (\S+)", route)
    src = re.search(r"\bsrc (\d+\.\d+\.\d+\.\d+)", route)
    if not dev or not src:
        return {}
    found = {"wan_if": dev.group(1), "server_lan": src.group(1)}
    for line in _run(["ip", "-4", "-o", "addr", "show", "dev", dev.group(1)]).splitlines():
        match = re.search(r"\binet (\d+\.\d+\.\d+\.\d+/\d+)", line)
        if match and match.group(1).startswith(src.group(1) + "/"):
            network = ipaddress.ip_interface(match.group(1)).network
            if network.is_private and network.prefixlen < 31:
                found["lan"] = str(network)
                # przykladowy adres NAS-a w tej samej sieci (tier NAS go uzywa) - do poprawienia
                # w katalogu hostow; jesli wypada na adres Pi, bierzemy nastepny
                guess = network.network_address + 10
                if str(guess) == src.group(1):
                    guess += 1
                if guess in network:
                    found["nas"] = str(guess)
            break
    return found


def seed(conn, **values) -> dict:
    """Zapisuje podane wartosci (lan, server_lan, wan_if, nas, smb_host) - przed pierwszym
    init_defaults, zeby domyslne ich nie zastapily."""
    written = {}
    for key in KEYS:
        value = values.get(key)
        if value:
            db.set_setting(conn, key, value)
            written[key] = value
    return written


def is_new(conn) -> bool:
    return conn.execute("SELECT 1 FROM settings WHERE key = 'tiers_seeded'").fetchone() is None \
        and conn.execute("SELECT 1 FROM peers LIMIT 1").fetchone() is None


def main(argv: list) -> int:
    found = detect()
    if "--show" in argv:
        print(found)
        return 0
    conn = db.connect(os.environ.get("WGPANEL_DB", "/var/lib/wg-panel/panel.db"))
    if not is_new(conn):
        return 0                      # aktualizacja - niczego nie zmieniamy
    written = seed(conn, **found)
    if written:
        print("    " + ", ".join(f"{k}={v}" for k, v in written.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
