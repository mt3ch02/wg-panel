"""Strażnik: co minutę (timer systemd, `wgctl guard`) sprawdza, czy wszystko działa.

Działa poza procesem panelu, więc powiadomi także wtedy, gdy panel leży. Sprawdza: panel
(usługa i odpowiedź strony), helpera, interfejs WireGuard oraz zdrowie Pi (napięcie
zasilania, temperatura, miejsce na karcie) - z plików systemowych, bez uprawnień roota.

Alarm idzie dopiero przy dwóch nieudanych sprawdzeniach z rzędu (krótki restart albo
aktualizacja nie budzą nikogo), raz; po naprawie - jedno "wróciło".
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

from . import db, helperclient, notify

FAILS_BEFORE_ALERT = 2
MIN_FREE_MB = 500
MIN_FREE_RATIO = 0.10
MAX_THRESHOLD_MB = 2048
HOT_CELSIUS = 80.0


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _read(path: str) -> str | None:
    try:
        with open(path, encoding="ascii") as handle:
            return handle.read().strip()
    except OSError:
        return None


def service_active(unit: str) -> bool:
    try:
        out = subprocess.run(["systemctl", "is-active", unit], capture_output=True, text=True,
                             timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return out.stdout.strip() == "active"


def panel_answers(port: str) -> bool:
    """Strona logowania na 127.0.0.1 - lokalnie panel mowi zwyklym HTTP takze przy HTTPS."""
    try:
        opener = urllib.request.build_opener(_NoRedirect)
        response = opener.open(f"http://127.0.0.1:{port}/login", timeout=5)
        return response.status == 200
    except urllib.error.HTTPError as exc:
        return exc.code in (302, 303)
    except (urllib.error.URLError, OSError):
        return False


def checks(cfg: dict, sysroot: str = "/", port: str = "8080") -> dict:
    """{nazwa: (ok, opis problemu)} - tylko rzeczy, ktore da sie tu sprawdzic."""
    out = {}
    if service_active("wg-panel") and panel_answers(port):
        out["panel"] = (True, "")
    else:
        out["panel"] = (False, "Panel nie odpowiada. Tunel może działać dalej - sprawdź "
                               "journalctl -u wg-panel.")
    try:
        helperclient.call("ping", timeout=5)
        out["helper"] = (True, "")
    except helperclient.HelperError:
        out["helper"] = (False, "Helper nie odpowiada - panel nie zastosuje zmian ani nie "
                                "odetnie osób po terminie. Sprawdź journalctl -u wg-panel-helper.")
    iface = cfg.get("wg_iface", "wg0")
    flags = _read(os.path.join(sysroot, "sys/class/net", iface, "flags"))
    up = flags is not None and int(flags, 16) & 1 == 1
    out["wg"] = (up, "" if up else f"Interfejs {iface} nie działa - użytkownicy nie mają dostępu "
                                    f"przez VPN. Sprawdź: systemctl status wg-quick@{iface}.")
    # za nginx certyfikatem zarzadza proxy - panel.json moze byc stary, nie ma czego pilnowac
    if os.environ.get("WGPANEL_TLS") == "1":
        meta = _read(os.path.join(os.environ.get("WGPANEL_TLS_DIR", "/var/lib/wg-panel/tls"),
                                  "panel.json"))
        try:
            days = (json.loads(meta)["not_after"] - time.time()) / 86400 if meta else None
        except (ValueError, KeyError, TypeError):
            days = None
        if days is not None:
            # odnawia sie sam na 30 dni przed koncem - ponizej 14 znaczy, ze odnawianie nie dziala
            out["tls"] = (days >= 14, f"Certyfikat HTTPS panelu wygasa za {max(0, math.ceil(days))} dni "
                                      "i nie odnowił się sam. Sprawdź: wgctl tls --renew.")
    temp = _read(os.path.join(sysroot, "sys/class/thermal/thermal_zone0/temp"))
    if temp is not None:
        celsius = int(temp) / 1000
        out["temp"] = (celsius < HOT_CELSIUS,
                       f"Raspberry Pi się przegrzewa: {celsius:.0f}°C. Sprawdź chłodzenie.")
    try:
        stat = os.statvfs(os.path.dirname(os.environ.get("WGPANEL_DB", "/var/lib/wg-panel/panel.db")))
        free_mb = stat.f_bavail * stat.f_frsize / 1048576
        total_mb = stat.f_blocks * stat.f_frsize / 1048576
        # 10% pojemnosci, ale nie mniej niz 500 MB i nie wiecej niz 2 GB - sam procent
        # alarmowalby na duzej karcie (10 GB wolnego na 256 GB to tylko 4%)
        ok = free_mb >= max(MIN_FREE_MB, min(MIN_FREE_RATIO * total_mb, MAX_THRESHOLD_MB))
        out["disk"] = (ok, f"Na karcie zostało {free_mb:.0f} MB wolnego miejsca. Baza i kopie "
                           "mogą przestać się zapisywać.")
    except OSError:
        pass
    return out


LABELS = {"panel": "panel", "helper": "helper", "wg": "interfejs WireGuard",
          "temp": "temperatura Pi", "disk": "miejsce na karcie", "tls": "certyfikat HTTPS"}


def undervoltage(sysroot: str) -> bool | None:
    """Czy od startu Pi bylo za niskie napiecie (bit 16) albo jest teraz (bit 0)."""
    raw = _read(os.path.join(sysroot, "sys/devices/platform/soc/soc:firmware/get_throttled"))
    if raw is None:
        return None          # to nie jest Raspberry Pi albo jadro tego nie udostepnia
    value = int(raw, 16)
    return bool(value & 0x1 or value & 0x10000)


def run(conn: sqlite3.Connection, sysroot: str = "/", port: str | None = None,
        state_path: str | None = None) -> dict:
    """Jedno sprawdzenie; zwraca wyniki i to, co wyslano."""
    cfg = db.settings(conn)
    port = port or os.environ.get("WGPANEL_PORT") or cfg.get("panel_port") or "8080"
    state_path = state_path or os.path.join(
        os.path.dirname(os.environ.get("WGPANEL_DB", "/var/lib/wg-panel/panel.db")), "watchdog.json")
    try:
        with open(state_path, encoding="utf8") as handle:
            state = json.load(handle)
    except (OSError, ValueError):
        state = {}
    memory = state.setdefault("checks", {})
    sent = []
    now = int(time.time())
    for name, (ok, problem) in checks(cfg, sysroot, port).items():
        entry = memory.setdefault(name, {"fails": 0, "alerted": False, "since": 0})
        label = LABELS.get(name, name)
        if ok:
            if entry["alerted"]:
                minutes = max(1, (now - entry["since"]) // 60)
                notify.raise_alert(conn, f"watchdog_{name}", f"{label}: znowu działa",
                                   f"{label} znowu działa (przerwa około {minutes} min).",
                                   severity="info")
                sent.append(f"{name}: wróciło")
            memory[name] = {"fails": 0, "alerted": False, "since": 0}
            continue
        entry["fails"] += 1
        entry["since"] = entry["since"] or now
        if entry["fails"] >= FAILS_BEFORE_ALERT and not entry["alerted"]:
            severity = "alarm" if name in ("panel", "helper", "wg") else "warn"
            notify.raise_alert(conn, f"watchdog_{name}", f"{label}: problem", problem,
                               severity=severity)
            entry["alerted"] = True
            sent.append(f"{name}: alarm")
    # napiecie: raz na uruchomienie Pi - to zdarzenie, a nie stan, ktory "wraca"
    boot = _read(os.path.join(sysroot, "proc/sys/kernel/random/boot_id")) or ""
    if undervoltage(sysroot) and state.get("undervoltage_boot") != boot:
        notify.raise_alert(conn, "watchdog_power", "zasilanie Pi",
                           "Raspberry Pi zgłosiło zbyt niskie napięcie zasilania. To częsta "
                           "przyczyna uszkodzenia karty SD - użyj mocniejszego zasilacza.",
                           severity="alarm")
        state["undervoltage_boot"] = boot
        sent.append("power: alarm")
    try:
        # unikalny plik i prawo zapisu dla grupy - straznik (wg-panel) i wgctl uruchamiane
        # recznie (czlonek grupy, np. pi) musza moc nadpisywac sobie nawzajem ten stan
        handle, tmp = tempfile.mkstemp(dir=os.path.dirname(state_path), prefix=".watchdog-")
        with os.fdopen(handle, "w", encoding="utf8") as out:
            json.dump(state, out)
        os.chmod(tmp, 0o660)
        os.replace(tmp, state_path)
    except OSError:
        pass
    return {"checks": {k: v["fails"] == 0 for k, v in memory.items()}, "sent": sent}
