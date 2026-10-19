"""Pierwsze uruchomienie: siec domowa wykryta na Pi zamiast wartosci domyslnych.

Instalator wola to tylko przy nowej instalacji (bazy jeszcze nie ma). Wpisuje do bazy siec
domowa (lan), adres Pi w niej (server_lan) i interfejs (wan_if) - zanim panel pierwszy raz
wystartuje, wiec katalog hostow i reguly od razu dotycza prawdziwej sieci. Istniejacej bazy nie
rusza: przy aktualizacji Twoje ustawienia zostaja.

    python3 -m panel.firstrun          wykryj i zapisz (tylko nowa baza)
    python3 -m panel.firstrun --show   tylko pokaz, co wykryto
    python3 -m panel.firstrun --fit-lan SIEC
                                       LAN_ACCESS dopasowany do tego serwera (dla install.sh):
                                       "auto" -> siec serwera; siec, ktora nie styka sie z zadna
                                       siecia serwera (np. z kopii innego komputera) -> siec serwera
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
    fake = os.environ.get("WGPANEL_DETECT")          # testy: wynik wykrywania jako JSON
    if fake is not None:
        import json
        return json.loads(fake) if fake else {}
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


# interfejsy, ktore nie sa siecia domowa: petla, tunel VPN, mosty i interfejsy Dockera i maszyn
NOT_LAN = re.compile(r"^(lo|wg|docker|br-|veth|virbr|tun|tap)")


def local_networks() -> list:
    """Sieci IPv4 interfejsow tego serwera (bez petli, VPN i Dockera)."""
    fake = os.environ.get("WGPANEL_LOCAL_NETS")      # testy: sieci po przecinku
    if fake is not None:
        return [ipaddress.ip_network(item, strict=False) for item in fake.split(",") if item]
    nets = []
    for line in _run(["ip", "-4", "-o", "addr", "show"]).splitlines():
        parts = line.split()
        match = re.search(r"\binet (\d+\.\d+\.\d+\.\d+/\d+)", line)
        if match and len(parts) > 1 and not NOT_LAN.match(parts[1]):
            nets.append(ipaddress.ip_interface(match.group(1)).network)
    return nets


def fit_lan_access(value: str, local: list | None = None, detected: str | None = None) -> str:
    """LAN_ACCESS pasujacy do tego serwera.

    "auto" - siec, przez ktora serwer wychodzi do internetu (pusta, gdy nie da sie jej wykryc).
    Siec, ktora styka sie z ktoras siecia serwera (takze wezsza, np. jeden adres /32), zostaje.
    Siec spoza wszystkich sieci serwera - np. z kopii odtwarzanej na innym komputerze albo po
    przeprowadzce do innej sieci - zamienia sie na siec serwera: z niej panel i tak nie bylby
    osiagalny. Gdy sieci serwera nie da sie odczytac, wartosc zostaje bez zmian."""
    value = (value or "").strip()
    if not value:
        return ""
    if detected is None:
        detected = detect().get("lan", "")
    if value == "auto":
        if detected:
            return detected
        # bez trasy do internetu (siec bez bramy) - pierwsza prywatna siec tego serwera
        for net in (local_networks() if local is None else local):
            if net.is_private and 8 <= net.prefixlen < 31:
                return str(net)
        return ""
    try:
        wanted = ipaddress.ip_network(value, strict=False)
    except ValueError:
        return value                  # zly zapis - odrzuci go walidacja instalatora
    local = local_networks() if local is None else local
    if not local or any(wanted.overlaps(net) for net in local):
        return value
    return detected or value


def follow(conn, found: dict | None = None, restored: bool = False) -> list:
    """Fakty o tej maszynie w ustawieniach - to nie sa wybory uzytkownika, tylko stan serwera.

    Zawsze: adres serwera w LAN (server_lan) i host z tym adresem w katalogu (zwykle "pi") -
    zmiana adresu na routerze (np. .10 -> .99) nie zostawia w panelu starego adresu.
    Po odtworzeniu z kopii (restored=True) takze interfejs wyjscia do internetu (wan_if) i siec
    domowa (lan), jesli adres serwera z niej wypada - kopia mogla przyjsc z innego komputera.
    Zwraca liste (klucz, stara wartosc, nowa wartosc); pusta, gdy nic sie nie zmienilo albo
    adresu nie da sie wykryc (wtedy niczego nie ruszamy)."""
    found = detect() if found is None else found
    new = found.get("server_lan") or ""
    if not new:
        return []
    cfg = db.settings(conn)
    changes = []
    old = cfg.get("server_lan") or ""
    if new != old:
        db.set_setting(conn, "server_lan", new)
        if old:
            with conn:
                conn.execute("UPDATE hosts SET address = ? WHERE address = ?", (new, old))
        changes.append(("server_lan", old, new))
    if restored:
        if found.get("wan_if") and found["wan_if"] != cfg.get("wan_if"):
            db.set_setting(conn, "wan_if", found["wan_if"])
            changes.append(("wan_if", cfg.get("wan_if") or "", found["wan_if"]))
        lan = cfg.get("lan") or ""
        if found.get("lan") and not in_network(new, lan):
            db.set_setting(conn, "lan", found["lan"])
            changes.append(("lan", lan, found["lan"]))
    return changes


def in_network(address: str, network: str) -> bool:
    try:
        return ipaddress.ip_address(address) in ipaddress.ip_network(network, strict=False)
    except ValueError:
        return False


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
    if argv[:1] == ["--fit-lan"]:
        print(fit_lan_access(argv[1] if len(argv) > 1 else ""))
        return 0
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
