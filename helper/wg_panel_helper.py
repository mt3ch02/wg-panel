#!/usr/bin/env python3
"""wg-panel-helper - jedyny komponent dzialajacy jako root.

Slucha na gniezdzie unix i przyjmuje WYLACZNIE nazwane operacje ze
strukturalnym argumentem. Nigdy nie wykonuje komend przyslanych przez panel
i nie zapisuje przyslanego tekstu - wszystkie pliki generuje sam, z danych,
ktore najpierw sam zwalidowal.

Logika generowania jest celowo powielona wzgledem panel/render.py: panel liczy
podglad, helper liczy stan faktyczny. Gdyby panel zostal przejety, helper i tak
odrzuci wszystko, co nie przejdzie jego wlasnej walidacji.
"""
from __future__ import annotations

import base64
import grp
import hashlib
import ipaddress
import json
import logging
import os
import re
import secrets
import shutil
import socket
import socketserver
import ssl
import subprocess
import sys
import tempfile
import threading
import time

SOCKET_PATH = os.environ.get("WGPANEL_SOCKET", "/run/wg-panel/helper.sock")
SOCKET_GROUP = os.environ.get("WGPANEL_GROUP", "wg-panel")
WG_DIR = os.environ.get("WGPANEL_WGDIR", "/etc/wireguard")
SYSCTL_DIR = os.environ.get("WGPANEL_SYSCTL_DIR", "/etc/sysctl.d")
BACKUP_DIR = os.path.join(WG_DIR, "wg-panel-backups")
# oczekujaca zmiana i ostatnie automatyczne cofniecie - przezywaja restart helpera i Pi
STATE_PATH = os.path.join(WG_DIR, "wg-panel-pending.json")
# HTTPS: klucz urzedu certyfikacji tylko tu (root); panel dostaje wylacznie swoj certyfikat
CA_DIR = os.path.join(WG_DIR, "wg-panel-ca")
TLS_DIR = os.environ.get("WGPANEL_TLS_DIR", "/var/lib/wg-panel/tls")
CA_DAYS = 3650
SERVER_DAYS = 397          # iOS i przegladarki: certyfikat serwera najwyzej ~13 miesiecy
RENEW_BEFORE = 30 * 86400
# wlasny certyfikat (np. Let's Encrypt): TYLKO z tych katalogow. Inaczej ktos z dostepem do
# panelu kazalby helperowi (root) skopiowac w czytelne miejsce dowolny klucz, np. klucz SSH.
CERT_DIRS = [d for d in os.environ.get(
    "WGPANEL_CERT_DIRS", "/etc/letsencrypt/:/etc/ssl/wg-panel/").split(":") if d]
MAX_REQUEST = 4 * 1024 * 1024
WEEKDAYS = {"Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"}
SYSCTL_FILE = "99-wg-panel-ipv6.conf"

log = logging.getLogger("wg-panel-helper")

_state_lock = threading.Lock()
_pending: dict | None = None  # aktywny commit-confirm
# ostatnie automatyczne cofniecie: panel rozpoznaje po skrocie tokenu, ze jego zmiana
# wrocila sama, i mowi to wprost zamiast "nic nie czeka na potwierdzenie"
_last_auto_rollback: dict | None = None


class Rejected(Exception):
    """Dane nie przeszly walidacji - nic nie zostalo zmienione."""


# --------------------------------------------------------------------------
# walidacja
# --------------------------------------------------------------------------

def _b64key(value: str, label: str) -> str:
    try:
        raw = base64.b64decode(str(value), validate=True)
    except Exception as exc:
        raise Rejected(f"{label}: nie jest base64") from exc
    if len(raw) != 32:
        raise Rejected(f"{label}: zla dlugosc klucza")
    return str(value)


def _name(value: str, limit: int = 60) -> str:
    return "".join(ch for ch in str(value) if ch.isalnum() or ch in " .:-_/")[:limit]


def _ports(value, proto: str) -> list[int]:
    if isinstance(value, str):
        chunks = [c for c in value.replace(" ", "").split(",") if c]
    else:
        chunks = [str(c) for c in (value or [])]
    if chunks and proto == "any":
        raise Rejected("porty wymagaja protokolu tcp albo udp")
    out = []
    for chunk in chunks:
        if not chunk.isdigit():
            raise Rejected(f"zly port: {chunk!r}")
        port = int(chunk)
        if not 1 <= port <= 65535:
            raise Rejected(f"port poza zakresem: {port}")
        out.append(port)
    if len(out) > 15:
        raise Rejected("multiport przyjmuje najwyzej 15 portow")
    return out


def _hhmm(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    parts = value.split(":")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        raise Rejected(f"zly czas: {value!r}")
    if not (0 <= int(parts[0]) <= 23 and 0 <= int(parts[1]) <= 59):
        raise Rejected(f"czas poza zakresem: {value!r}")
    return f"{int(parts[0]):02d}:{int(parts[1]):02d}"


def _network(value: str, label: str, version: int | None = None) -> str:
    try:
        network = ipaddress.ip_network(str(value), strict=False)
    except ValueError as exc:
        raise Rejected(f"{label}: zly adres {value!r}") from exc
    if version and network.version != version:
        raise Rejected(f"{label}: oczekiwano IPv{version}, dostalem {value!r}")
    return str(network)


def _iface(value: str, label: str) -> str:
    value = str(value or "")
    if not value or len(value) > 15 or not all(c.isalnum() or c in "._-" for c in value):
        raise Rejected(f"{label}: zla nazwa interfejsu")
    return value


def _chain(value: str) -> str:
    value = str(value or "")
    if not value or len(value) > 24 or not all(c.isalnum() or c in "-_" for c in value):
        raise Rejected("zla nazwa lancucha")
    return value


def validate_settings(raw: dict) -> dict:
    cfg = {
        "lan": _network(raw.get("lan"), "lan", 4),
        "vpn": _network(raw.get("vpn"), "vpn", 4),
        "wan_if": _iface(raw.get("wan_if"), "wan_if"),
        "wg_iface": _iface(raw.get("wg_iface", "wg0"), "wg_iface"),
        "chain": _chain(raw.get("chain", "WG-USERS")),
    }
    try:
        server_ip = ipaddress.ip_address(str(raw.get("server_ip")))
    except ValueError as exc:
        raise Rejected("server_ip: zly adres") from exc
    if server_ip not in ipaddress.ip_network(cfg["vpn"], strict=False):
        raise Rejected("server_ip poza podsiecia VPN")
    cfg["server_ip"] = str(server_ip)

    port = str(raw.get("listen_port", ""))
    if not port.isdigit() or not 1 <= int(port) <= 65535:
        raise Rejected("listen_port poza zakresem")
    cfg["listen_port"] = port
    cfg["managed_host_ports"] = _ports(raw.get("managed_host_ports", "22,3389"), "tcp")
    cfg["host_chain"] = cfg["chain"] + "-HOST"

    cfg["ipv6"] = str(raw.get("ipv6_enabled", "0")) == "1"
    if cfg["ipv6"]:
        cfg["vpn6"] = _network(raw.get("vpn6"), "vpn6", 6)
        try:
            server_ip6 = ipaddress.ip_address(str(raw.get("server_ip6")))
        except ValueError as exc:
            raise Rejected("server_ip6: zly adres") from exc
        if server_ip6 not in ipaddress.ip_network(cfg["vpn6"], strict=False):
            raise Rejected("server_ip6 poza podsiecia VPN IPv6")
        cfg["server_ip6"] = str(server_ip6)
        cfg["lan6"] = [_network(item.strip(), "lan6", 6)
                       for item in str(raw.get("lan6", "")).split(",") if item.strip()]
        if not cfg["lan6"]:
            # bez prefiksu LAN-u blokada "LAN" nie mialaby odpowiednika w IPv6
            raise Rejected("IPv6 wlaczone, ale nie podano prefiksu IPv6 sieci domowej (lan6)")
        cfg["nat6"] = str(raw.get("nat6", "1")) == "1"
    return cfg


def validate_rule(raw: dict, cfg: dict) -> dict:
    action = str(raw.get("action", "")).upper()
    if action not in ("ACCEPT", "DROP"):
        raise Rejected(f"niedozwolona akcja: {action!r}")

    dest = str(raw.get("dest", "ANY")).strip()
    if dest not in ("", "ANY"):
        dest = _network(dest, "dest", 4)

    dest6_raw = raw.get("dest6", [])
    if isinstance(dest6_raw, str):
        dest6_raw = [d.strip() for d in dest6_raw.split(",") if d.strip()]
    if not isinstance(dest6_raw, list) or len(dest6_raw) > 8:
        raise Rejected("dest6 musi byc lista (najwyzej 8 celow)")
    dest6 = ["ANY" if str(d).strip() == "ANY" else _network(d, "dest6", 6) for d in dest6_raw]
    if not dest and not dest6:
        raise Rejected("regula bez celu")

    proto = str(raw.get("proto", "any")).lower()
    if proto not in ("any", "tcp", "udp"):
        raise Rejected(f"niedozwolony protokol: {proto!r}")

    time_from = _hhmm(raw.get("time_from", ""))
    time_to = _hhmm(raw.get("time_to", ""))
    if bool(time_from) != bool(time_to):
        raise Rejected("okno czasowe wymaga obu godzin")

    days = raw.get("weekdays") or []
    if isinstance(days, str):
        days = [d for d in days.replace(" ", "").split(",") if d]
    for day in days:
        if day not in WEEKDAYS:
            raise Rejected(f"zly dzien tygodnia: {day!r}")

    return {
        "action": action, "dest": dest, "dest6": dest6, "proto": proto,
        "ports": _ports(raw.get("ports", ""), proto),
        "time_from": time_from, "time_to": time_to, "weekdays": list(days),
        "comment": _name(raw.get("comment", ""), 200),
    }


def validate_peers(raw_peers, cfg: dict) -> list[dict]:
    if not isinstance(raw_peers, list):
        raise Rejected("peers musi byc lista")
    if len(raw_peers) > 500:
        raise Rejected("za duzo peerow")

    vpn = ipaddress.ip_network(cfg["vpn"], strict=False)
    vpn6 = ipaddress.ip_network(cfg["vpn6"], strict=False) if cfg["ipv6"] else None
    seen_ip: set[str] = {cfg["server_ip"]}
    seen_ip6: set[str] = {cfg["server_ip6"]} if cfg["ipv6"] else set()
    seen_key: set[str] = set()
    out = []
    for raw in raw_peers:
        state = str(raw.get("state", "active"))
        if state not in ("active", "suspended"):
            raise Rejected(f"zly stan peera: {state!r}")
        try:
            addr = ipaddress.ip_address(str(raw.get("ip")))
        except ValueError as exc:
            raise Rejected("peer: zly adres IP") from exc
        if addr not in vpn:
            raise Rejected(f"adres {addr} poza podsiecia VPN")
        if str(addr) in seen_ip:
            raise Rejected(f"adres {addr} uzyty dwa razy")
        seen_ip.add(str(addr))

        ip6 = ""
        if cfg["ipv6"] and raw.get("ip6"):
            try:
                addr6 = ipaddress.ip_address(str(raw.get("ip6")))
            except ValueError as exc:
                raise Rejected("peer: zly adres IPv6") from exc
            if addr6.version != 6 or addr6 not in vpn6:
                raise Rejected(f"adres {addr6} poza podsiecia VPN IPv6")
            if str(addr6) in seen_ip6:
                raise Rejected(f"adres {addr6} uzyty dwa razy")
            seen_ip6.add(str(addr6))
            ip6 = str(addr6)

        pubkey = _b64key(raw.get("pubkey"), "pubkey")
        if pubkey in seen_key:
            raise Rejected("ten sam klucz publiczny u dwoch peerow")
        seen_key.add(pubkey)

        out.append({
            "name": _name(raw.get("name", "")),
            "device": _name(raw.get("device", "")),
            "tier_name": _name(raw.get("tier_name", ""), 30),
            "ip": str(addr),
            "ip6": ip6,
            "pubkey": pubkey,
            # PSK jest opcjonalny - konfiguracje sprzed panelu czesto go nie maja
            "psk": _b64key(raw.get("psk"), "psk") if str(raw.get("psk") or "").strip() else "",
            "state": state,
            "host_access": _ports(raw.get("host_access", ""), "tcp"),
            "rules": [validate_rule(r, cfg) for r in (raw.get("rules") or [])],
        })
    return out


# --------------------------------------------------------------------------
# generowanie plikow (musi dawac to samo co panel/render.py)
# --------------------------------------------------------------------------

def _safe_comment(value: str) -> str:
    return "".join(ch for ch in str(value) if ch.isalnum() or ch in " .:-_")[:40]


def rule_to_args(chain: str, source: str, dest: str, rule: dict) -> list[str]:
    args = ["-A", chain, "-s", source]
    if dest != "ANY":
        args += ["-d", dest]
    if rule["proto"] != "any":
        args += ["-p", rule["proto"]]
        if len(rule["ports"]) == 1:
            args += ["--dport", str(rule["ports"][0])]
        elif rule["ports"]:
            args += ["-m", "multiport", "--dports", ",".join(str(p) for p in rule["ports"])]
    if rule["time_from"]:
        args += ["-m", "time", "--timestart", rule["time_from"], "--timestop", rule["time_to"]]
        if rule["weekdays"]:
            args += ["--weekdays", ",".join(rule["weekdays"])]
    elif rule["weekdays"]:
        args += ["-m", "time", "--weekdays", ",".join(rule["weekdays"])]
    if rule["comment"]:
        args += ["-m", "comment", "--comment", rule["comment"]]
    args += ["-j", rule["action"]]
    return args


def render_rules_file(cfg: dict, peers: list[dict], family: int = 4) -> str:
    chain, host_chain, iface = cfg["chain"], cfg["host_chain"], cfg["wg_iface"]
    mask = "/32" if family == 4 else "/128"
    key = "ip" if family == 4 else "ip6"
    lines = ["# wygenerowane przez wg-panel - nie edytuj recznie", "*filter",
             f":{chain} - [0:0]", f":{host_chain} - [0:0]"]

    def emit(args):
        lines.append(" ".join(f'"{a}"' if " " in a else a for a in args))

    emit(["-A", chain, "-o", iface, "-m", "comment", "--comment",
          "blokada ruchu miedzy peerami", "-j", "DROP"])
    active = [p for p in peers if p["state"] == "active" and p.get(key)]
    for peer in active:
        for rule in peer["rules"]:
            targets = [rule["dest"]] if family == 4 else list(rule["dest6"])
            for dest in targets:
                if dest:
                    emit(rule_to_args(chain, peer[key] + mask, dest, rule))
    emit(["-A", chain, "-m", "comment", "--comment", "domyslnie przepusc", "-j", "ACCEPT"])

    managed = set(cfg["managed_host_ports"])
    for peer in active:
        managed |= set(peer["host_access"])
        if peer["host_access"]:
            emit(["-A", host_chain, "-i", iface, "-s", peer[key] + mask, "-p", "tcp",
                  "-m", "multiport", "--dports",
                  ",".join(str(p) for p in sorted(peer["host_access"])),
                  "-m", "comment", "--comment", f"host access: {_safe_comment(peer['name'])}",
                  "-j", "ACCEPT"])
    if managed:
        emit(["-A", host_chain, "-i", iface, "-p", "tcp", "-m", "multiport", "--dports",
              ",".join(str(p) for p in sorted(managed)),
              "-m", "comment", "--comment", "reszta peerow bez dostepu do hosta", "-j", "DROP"])
    lines.append("COMMIT")
    return "\n".join(lines) + "\n"


DOCKER_CHAIN = "DOCKER-USER"  # lancuch Dockera na reguly uzytkownika


def _hook_lines(tool: str, cfg: dict, rules_path: str, nat_source: str, masquerade: bool):
    chain, host_chain, iface, wan = cfg["chain"], cfg["host_chain"], cfg["wg_iface"], \
        cfg["wan_if"]
    up = [
        f"PostUp = {tool} -N {chain} 2>/dev/null; {tool} -F {chain}",
        f"PostUp = {tool} -N {host_chain} 2>/dev/null; {tool} -F {host_chain}",
        f"PostUp = {tool} -C FORWARD -i {iface} -j {chain} 2>/dev/null || "
        f"{tool} -I FORWARD 1 -i {iface} -j {chain}",
        # Docker przy starcie wstawia swoje skoki na sama gore FORWARD; DOCKER-USER to lancuch,
        # ktory Docker przepuszcza pierwszy i ktorego nie czysci - tam nasz skok zostaje pierwszy
        f"PostUp = if command -v dockerd >/dev/null; then {tool} -N {DOCKER_CHAIN} 2>/dev/null; "
        f"{tool} -C {DOCKER_CHAIN} -i {iface} -j {chain} 2>/dev/null || "
        f"{tool} -I {DOCKER_CHAIN} 1 -i {iface} -j {chain}; fi",
        f"PostUp = {tool} -C FORWARD -o {iface} -m conntrack --ctstate RELATED,ESTABLISHED "
        f"-j ACCEPT 2>/dev/null || {tool} -I FORWARD 2 -o {iface} -m conntrack "
        f"--ctstate RELATED,ESTABLISHED -j ACCEPT",
        f"PostUp = {tool} -C INPUT -i {iface} -j {host_chain} 2>/dev/null || "
        f"{tool} -I INPUT 1 -i {iface} -j {host_chain}",
        f"PostUp = {tool}-restore -n {rules_path}",
    ]
    if masquerade:
        up.append(
            f"PostUp = {tool} -t nat -C POSTROUTING -s {nat_source} -o {wan} -j MASQUERADE "
            f"2>/dev/null || {tool} -t nat -A POSTROUTING -s {nat_source} -o {wan} "
            f"-j MASQUERADE")
    down = [
        f"PostDown = {tool} -D FORWARD -i {iface} -j {chain} 2>/dev/null; "
        f"{tool} -D {DOCKER_CHAIN} -i {iface} -j {chain} 2>/dev/null; "
        f"{tool} -F {chain}; {tool} -X {chain} 2>/dev/null",
        f"PostDown = {tool} -D INPUT -i {iface} -j {host_chain} 2>/dev/null; "
        f"{tool} -F {host_chain}; {tool} -X {host_chain} 2>/dev/null",
    ]
    return up, down


def render_wg_conf(cfg: dict, peers: list[dict], private_key: str, rules_path: str) -> str:
    v6 = cfg["ipv6"]
    prefix = ipaddress.ip_network(cfg["vpn"], strict=False).prefixlen
    addresses = [f"{cfg['server_ip']}/{prefix}"]
    if v6:
        prefix6 = ipaddress.ip_network(cfg["vpn6"], strict=False).prefixlen
        addresses.append(f"{cfg['server_ip6']}/{prefix6}")

    up4, down4 = _hook_lines("iptables", cfg, rules_path, cfg["vpn"], True)
    out = [
        "# Wygenerowane przez wg-panel. Zmiany rob w panelu, nie tutaj.",
        "[Interface]",
        f"Address    = {', '.join(addresses)}",
        f"ListenPort = {cfg['listen_port']}",
        f"PrivateKey = {private_key}",
        "",
        "# --- lancuchy zarzadzane przez panel (IPv4) ---",
        *up4,
    ]
    down = list(down4)
    if v6:
        up6, down6 = _hook_lines("ip6tables", cfg, rules6_path_for(rules_path),
                                 cfg["vpn6"], cfg["nat6"])
        out += ["", "# --- lancuchy zarzadzane przez panel (IPv6) ---", *up6]
        down += down6
    out += ["", *down, ""]

    for peer in peers:
        if peer["state"] != "active":
            continue
        label = f"{peer['name']} / {peer['tier_name'] or 'brak tieru'}"
        if peer["device"]:
            label += f" / {peer['device']}"
        allowed = [f"{peer['ip']}/32"]
        if v6 and peer.get("ip6"):
            allowed.append(f"{peer['ip6']}/128")
        out += ["[Peer]", f"# {label}", f"PublicKey    = {peer['pubkey']}"]
        if peer["psk"]:
            out.append(f"PresharedKey = {peer['psk']}")
        out += [f"AllowedIPs   = {', '.join(allowed)}", ""]
    return "\n".join(out)


# --------------------------------------------------------------------------
# operacje na systemie
# --------------------------------------------------------------------------

def run(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Uruchamia narzedzie systemowe. Brak binarki to blad srodowiska, nie wyjatek."""
    log.debug("run: %s", args)
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=60, **kwargs)
    except FileNotFoundError:
        return subprocess.CompletedProcess(args, 127, "", f"brak polecenia: {args[0]}")
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args, 124, "", f"przekroczony czas: {args[0]}")


def conf_path(cfg: dict) -> str:
    return os.path.join(WG_DIR, f"{cfg['wg_iface']}.conf")


def rules_path(cfg: dict) -> str:
    return os.path.join(WG_DIR, f"{cfg['wg_iface']}-panel-rules.v4")


def rules6_path_for(path: str) -> str:
    return path[:-3] + ".v6" if path.endswith(".v4") else path + "6"


def managed_files(cfg: dict) -> list[str]:
    path = rules_path(cfg)
    return [conf_path(cfg), path, rules6_path_for(path)]


def read_private_key(cfg: dict) -> str:
    """Klucz prywatny serwera nigdy nie opuszcza tej maszyny ani tego procesu."""
    path = conf_path(cfg)
    if os.path.exists(path):
        with open(path, encoding="utf8") as handle:
            for line in handle:
                if line.strip().startswith("PrivateKey"):
                    value = line.split("=", 1)[1].strip()
                    if value and not value.startswith("__"):
                        return value
    for candidate in ("server_private.key", f"{cfg['wg_iface']}_private.key"):
        candidate_path = os.path.join(WG_DIR, candidate)
        if os.path.exists(candidate_path):
            with open(candidate_path, encoding="utf8") as handle:
                return handle.read().strip()
    raise Rejected("nie znaleziono klucza prywatnego serwera")


def write_private(path: str, content: str) -> None:
    handle, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
    with os.fdopen(handle, "w", encoding="utf8") as fh:
        fh.write(content)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def snapshot(cfg: dict) -> dict:
    out = {}
    for path in managed_files(cfg):
        out[path] = open(path, encoding="utf8").read() if os.path.exists(path) else None
    return out


def archive(cfg: dict) -> None:
    os.makedirs(BACKUP_DIR, exist_ok=True)
    os.chmod(BACKUP_DIR, 0o700)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for path in managed_files(cfg):
        if os.path.exists(path):
            shutil.copy2(path, os.path.join(BACKUP_DIR, f"{os.path.basename(path)}.{stamp}"))
    files = sorted(os.listdir(BACKUP_DIR))
    for stale in files[:-60]:
        os.remove(os.path.join(BACKUP_DIR, stale))


def restore(files: dict) -> None:
    for path, content in files.items():
        if content is None:
            if os.path.exists(path):
                os.remove(path)
        else:
            write_private(path, content)


def ensure_hook(tool: str, spec: list[str], chain: str, position: int) -> None:
    if run([tool, "-C", chain] + spec).returncode != 0:
        run([tool, "-I", chain, str(position)] + spec)


def _rules(tool: str, chain: str) -> list[str] | None:
    """Reguly lancucha w postaci `iptables -S` (bez -P/-N), None gdy lancucha nie ma."""
    result = run([tool, "-S", chain])
    if result.returncode != 0:
        return None
    return [line.strip() for line in result.stdout.split("\n") if line.startswith("-A ")]


def ensure_first(tool: str, spec: list[str], chain: str) -> None:
    """Podpiecie ma byc PIERWSZA regula lancucha.

    Samo "czy jest" (ensure_hook) nie wystarcza: Docker przy starcie wstawia swoje skoki na
    sama gore FORWARD i nasz skok laduje nizej. Najpierw wstawiamy nowy na gore, potem usuwamy
    stare kopie po numerach - filtrowanie nie ma przerwy nawet na ulamek sekundy.
    """
    rules = _rules(tool, chain)
    if rules is None:
        return
    want = " ".join(["-A", chain] + spec)
    if rules and rules[0] == want:
        return
    run([tool, "-I", chain, "1"] + spec)
    # po wstawieniu stare reguly przesunely sie o jeden: indeks i (od 0) to numer i + 2
    for number in sorted((i + 2 for i, line in enumerate(rules) if line == want), reverse=True):
        run([tool, "-D", chain, str(number)])


def docker_present(tool: str) -> bool:
    """Jest Docker (program dockerd albo juz jego lancuch DOCKER-USER)."""
    return shutil.which("dockerd") is not None or _rules(tool, DOCKER_CHAIN) is not None


def remove_hook(tool: str, spec: list[str], chain: str, limit: int = 20) -> None:
    """Usuwa podpiecie, takze gdy zostalo dodane kilka razy.

    Limit jest po to, zeby narzedzie, ktore zglasza sukces, ale reguly nie usuwa,
    nie zapetlilo helpera na zawsze.
    """
    for _ in range(limit):
        if run([tool, "-C", chain] + spec).returncode != 0:
            return
        if run([tool, "-D", chain] + spec).returncode != 0:
            return
    log.warning("%s: nie udalo sie usunac podpiecia %s po %d probach", tool, chain, limit)


def apply_family(tool: str, cfg: dict, path: str) -> None:
    iface = cfg["wg_iface"]
    for chain in (cfg["chain"], cfg["host_chain"]):
        run([tool, "-N", chain])
        run([tool, "-F", chain])
    ensure_first(tool, ["-i", iface, "-j", cfg["chain"]], "FORWARD")
    ensure_hook(tool, ["-o", iface, "-m", "conntrack", "--ctstate", "RELATED,ESTABLISHED",
                       "-j", "ACCEPT"], "FORWARD", 2)
    if docker_present(tool):
        run([tool, "-N", DOCKER_CHAIN])          # Docker go nie czysci i przepuszcza pierwszy
        ensure_first(tool, ["-i", iface, "-j", cfg["chain"]], DOCKER_CHAIN)
    ensure_hook(tool, ["-i", iface, "-j", cfg["host_chain"]], "INPUT", 1)
    result = run([f"{tool}-restore", "-n", path])
    if result.returncode != 0:
        raise Rejected(f"{tool}-restore: {result.stderr.strip()}")


def teardown_family(tool: str, cfg: dict) -> None:
    iface = cfg["wg_iface"]
    remove_hook(tool, ["-i", iface, "-j", cfg["chain"]], "FORWARD")
    remove_hook(tool, ["-i", iface, "-j", cfg["chain"]], DOCKER_CHAIN)
    remove_hook(tool, ["-o", iface, "-m", "conntrack", "--ctstate", "RELATED,ESTABLISHED",
                       "-j", "ACCEPT"], "FORWARD")
    remove_hook(tool, ["-i", iface, "-j", cfg["host_chain"]], "INPUT")
    for chain in (cfg["chain"], cfg["host_chain"]):
        run([tool, "-F", chain])
        run([tool, "-X", chain])


def ensure_nat(tool: str, source: str, wan: str) -> bool:
    spec = ["-s", source, "-o", wan, "-j", "MASQUERADE"]
    if run([tool, "-t", "nat", "-C", "POSTROUTING"] + spec).returncode != 0:
        run([tool, "-t", "nat", "-A", "POSTROUTING"] + spec)
        return True
    return False


def ipv6_sysctl(cfg: dict, enable: bool) -> list[str]:
    """Wlacza przekazywanie IPv6. accept_ra=2 MUSI byc przed forwarding=1,
    inaczej Pi przestanie przyjmowac ogloszenia routera i straci trase domyslna IPv6."""
    steps = []
    path = os.path.join(SYSCTL_DIR, SYSCTL_FILE)
    wan = cfg["wan_if"]
    if enable:
        run(["sysctl", "-qw", f"net.ipv6.conf.{wan}.accept_ra=2"])
        run(["sysctl", "-qw", "net.ipv6.conf.all.forwarding=1"])
        content = (f"# wg-panel: przekazywanie IPv6 dla tunelu\n"
                   f"net.ipv6.conf.{wan}.accept_ra = 2\n"
                   f"net.ipv6.conf.all.forwarding = 1\n")
        try:
            os.makedirs(SYSCTL_DIR, exist_ok=True)
            with open(path, "w", encoding="utf8") as handle:
                handle.write(content)
        except OSError as exc:
            steps.append(f"sysctl: nie zapisano {path}: {exc}")
        steps.append("ipv6: przekazywanie wlaczone (accept_ra=2 na WAN)")
    elif os.path.exists(path):
        os.remove(path)
        run(["sysctl", "-qw", "net.ipv6.conf.all.forwarding=0"])
        steps.append("ipv6: przekazywanie wylaczone")
    return steps


FORWARD_FILE = "99-wg-panel-forward.conf"


def ensure_base(cfg: dict) -> list[str]:
    """To, co na recznej konfiguracji robila instrukcja v2: przekazywanie IPv4
    i uruchamianie tunelu przy starcie systemu. Bez tego VPN sie laczy, ale nic nie przechodzi."""
    steps = []
    current = run(["sysctl", "-n", "net.ipv4.ip_forward"])
    if current.stdout.strip() != "1":
        run(["sysctl", "-qw", "net.ipv4.ip_forward=1"])
        steps.append("ipv4: wlaczono przekazywanie pakietow")
    path = os.path.join(SYSCTL_DIR, FORWARD_FILE)
    if not os.path.exists(path):
        try:
            os.makedirs(SYSCTL_DIR, exist_ok=True)
            with open(path, "w", encoding="utf8") as handle:
                handle.write("# wg-panel: przekazywanie pakietow dla VPN\nnet.ipv4.ip_forward = 1\n")
        except OSError as exc:
            steps.append(f"sysctl: nie zapisano {path}: {exc}")
    unit = f"wg-quick@{cfg['wg_iface']}"
    if run(["systemctl", "is-enabled", unit]).stdout.strip() != "enabled":
        run(["systemctl", "enable", unit])
        steps.append(f"{unit}: wlaczony przy starcie systemu")
    steps += ufw_wireguard(cfg["listen_port"])
    return steps


UFW_COMMENT = "wg-panel WireGuard"


def ufw_wireguard(port: str) -> list[str]:
    """Port WireGuarda w ufw: regula z komentarzem panelu dla obecnego portu, bez starych.

    Bez niej, przy wlaczonym ufw, klienci nie dobija sie do serwera - na nowym systemie trzeba
    bylo pamietac o recznym "ufw allow PORT/udp". Wlasnych regul uzytkownika nie ruszamy.
    Regula zostaje takze po odinstalowaniu panelu - VPN dziala dalej (uninstall.sh)."""
    if shutil.which("ufw") is None:
        return []
    listed = run(["ufw", "status", "numbered"])
    if listed.returncode != 0:
        return [f"ufw: nie odczytano regul: {listed.stderr.strip()}"]
    want = f"{port}/udp"
    keep, stale = False, []
    for line in listed.stdout.splitlines():
        if not line.rstrip().endswith("# " + UFW_COMMENT):
            continue
        match = re.match(r"\[\s*(\d+)\]\s+(\S+)", line)
        if not match:
            continue
        if match.group(2) == want:
            keep = True
        else:
            stale.append(int(match.group(1)))
    steps = []
    # od konca - po usunieciu numery kolejnych regul sie przesuwaja
    for number in sorted(stale, reverse=True):
        run(["ufw", "--force", "delete", str(number)])
    if stale:
        steps.append("ufw: usunieto regule starego portu WireGuarda")
    if not keep:
        result = run(["ufw", "allow", want, "comment", UFW_COMMENT])
        if result.returncode != 0:
            return steps + [f"ufw: nie otwarto portu {want}: {result.stderr.strip()}"]
        steps.append(f"ufw: otwarto port WireGuarda {want}")
    return steps


def apply_files(cfg: dict) -> list[str]:
    """Wgrywa to, co jest juz zapisane na dysku. Zwraca log krokow."""
    iface = cfg["wg_iface"]
    steps = []

    if cfg["ipv6"] and shutil.which("ip6tables") is None:
        raise Rejected("IPv6 wlaczone, ale brak ip6tables (apt install iptables)")

    steps += ensure_base(cfg)

    if run(["ip", "link", "show", iface]).returncode != 0:
        # czysty system albo tunel wylaczony: wg-quick podniesie interfejs z nowego
        # pliku i sam wykona PostUp, czyli zbuduje lancuchy panelu
        result = run(["systemctl", "restart", f"wg-quick@{iface}"])
        if result.returncode != 0:
            raise Rejected(f"wg-quick@{iface} nie wstal: {result.stderr.strip()}")
        steps.append(f"wg-quick@{iface}: interfejs podniesiony")
        return steps

    stripped = run(["wg-quick", "strip", iface])
    if stripped.returncode != 0:
        raise Rejected(f"wg-quick strip: {stripped.stderr.strip()}")
    handle, tmp = tempfile.mkstemp()
    try:
        with os.fdopen(handle, "w", encoding="utf8") as fh:
            fh.write(stripped.stdout)
        os.chmod(tmp, 0o600)
        result = run(["wg", "syncconf", iface, tmp])
        if result.returncode != 0:
            raise Rejected(f"wg syncconf: {result.stderr.strip()}")
        steps.append("wg syncconf: peery zsynchronizowane")
    finally:
        os.remove(tmp)

    # adres IPv6 na interfejsie: syncconf nie rusza adresow, wiec dokladamy recznie
    if cfg["ipv6"]:
        prefix6 = ipaddress.ip_network(cfg["vpn6"], strict=False).prefixlen
        address = f"{cfg['server_ip6']}/{prefix6}"
        shown = run(["ip", "-6", "addr", "show", "dev", iface])
        if cfg["server_ip6"] not in shown.stdout:
            run(["ip", "-6", "addr", "add", address, "dev", iface])
            steps.append(f"ipv6: adres {address} na {iface}")

    apply_family("iptables", cfg, rules_path(cfg))
    steps.append("iptables: lancuchy przebudowane")
    if ensure_nat("iptables", cfg["vpn"], cfg["wan_if"]):
        steps.append("nat: dodano MASQUERADE")

    if cfg["ipv6"]:
        steps += ipv6_sysctl(cfg, True)
        apply_family("ip6tables", cfg, rules6_path_for(rules_path(cfg)))
        steps.append("ip6tables: lancuchy przebudowane")
        if cfg["nat6"] and ensure_nat("ip6tables", cfg["vpn6"], cfg["wan_if"]):
            steps.append("nat6: dodano MASQUERADE")
    else:
        if shutil.which("ip6tables") and \
                run(["ip6tables", "-L", cfg["chain"], "-n"]).returncode == 0:
            teardown_family("ip6tables", cfg)
            steps.append("ip6tables: lancuchy panelu usuniete")
        steps += ipv6_sysctl(cfg, False)
    return steps


# --------------------------------------------------------------------------
# odcisk stanu - do wykrywania zmian dokonanych poza panelem
# --------------------------------------------------------------------------

def _sha(path: str) -> str:
    if not os.path.exists(path):
        return ""
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def _count_rules(path: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    if not os.path.exists(path):
        return counts
    with open(path, encoding="utf8") as handle:
        for line in handle:
            match = re.match(r"-A (\S+) ", line)
            if match:
                counts[match.group(1)] = counts.get(match.group(1), 0) + 1
    return counts


def _expected_pubkeys(path: str) -> list[str]:
    keys = []
    if os.path.exists(path):
        with open(path, encoding="utf8") as handle:
            for line in handle:
                if line.strip().startswith("PublicKey"):
                    keys.append(line.split("=", 1)[1].strip())
    return sorted(keys)


def fingerprint(cfg: dict) -> dict:
    """Co powinno byc na dysku i w jadrze po zastosowaniu zmian."""
    path4 = rules_path(cfg)
    state = {
        "conf_sha256": _sha(conf_path(cfg)),
        "rules4_sha256": _sha(path4),
        "rules4_counts": _count_rules(path4),
        "pubkeys": _expected_pubkeys(conf_path(cfg)),
        "ipv6": cfg["ipv6"],
    }
    if cfg["ipv6"]:
        path6 = rules6_path_for(path4)
        state["rules6_sha256"] = _sha(path6)
        state["rules6_counts"] = _count_rules(path6)
    return state


def _live_counts(tool: str, chain: str) -> int | None:
    result = run([tool, "-S", chain])
    if result.returncode != 0:
        return None
    return sum(1 for line in result.stdout.split("\n") if line.startswith("-A "))


def _hook_first(tool: str, chain: str, target: str, iface: str) -> bool:
    """Czy pierwsza regula w FORWARD/INPUT to skok do naszego lancucha."""
    rules = _rules(tool, chain)
    if not rules:
        return False
    return f"-i {iface}" in rules[0] and f"-j {target}" in rules[0]


def _forward_guarded(tool: str, target: str, iface: str) -> bool:
    """Ruch z VPN trafia do naszego lancucha przed czymkolwiek innym: skok jest pierwszy
    w FORWARD albo pierwszy jest skok Dockera do DOCKER-USER, a tam pierwszy jest nasz."""
    if _hook_first(tool, "FORWARD", target, iface):
        return True
    rules = _rules(tool, "FORWARD") or []
    return (bool(rules) and rules[0] == f"-A FORWARD -j {DOCKER_CHAIN}"
            and _hook_first(tool, DOCKER_CHAIN, target, iface))


def detect_drift(cfg: dict, expected: dict) -> list[dict]:
    findings = []

    def add(kind: str, message: str, severity: str = "alarm") -> None:
        findings.append({"kind": kind, "message": message, "severity": severity})

    if not expected:
        return findings

    if _sha(conf_path(cfg)) != expected.get("conf_sha256"):
        add("conf", f"{conf_path(cfg)} zmieniony poza panelem")
    if _sha(rules_path(cfg)) != expected.get("rules4_sha256"):
        add("rules4", "plik regul IPv4 zmieniony poza panelem")
    if cfg["ipv6"] and _sha(rules6_path_for(rules_path(cfg))) != expected.get("rules6_sha256"):
        add("rules6", "plik regul IPv6 zmieniony poza panelem")

    for path in managed_files(cfg):
        if os.path.exists(path):
            mode = os.stat(path).st_mode & 0o777
            if mode & 0o077:
                add("perm", f"{path} ma prawa {oct(mode)} - czytelne dla innych kont")

    live = run(["wg", "show", cfg["wg_iface"], "peers"])
    if live.returncode == 0:
        live_keys = sorted(k for k in live.stdout.split() if k)
        expected_keys = expected.get("pubkeys", [])
        extra = set(live_keys) - set(expected_keys)
        missing = set(expected_keys) - set(live_keys)
        if extra:
            add("peers", f"na interfejsie sa peery spoza panelu: {len(extra)} "
                         f"(np. {sorted(extra)[0][:12]}...)")
        if missing:
            add("peers", f"brakuje {len(missing)} peerow, ktore panel zastosowal", "warn")

    families = [("iptables", "rules4_counts")]
    if cfg["ipv6"]:
        families.append(("ip6tables", "rules6_counts"))
    for tool, key in families:
        for chain in (cfg["chain"], cfg["host_chain"]):
            want = (expected.get(key) or {}).get(chain)
            have = _live_counts(tool, chain)
            if want is not None and have is None:
                add("chain", f"{tool}: brak lancucha {chain}")
            elif want is not None and have != want:
                add("chain", f"{tool}: {chain} ma {have} regul zamiast {want}")
        if not _forward_guarded(tool, cfg["chain"], cfg["wg_iface"]):
            add("hook", f"{tool}: pierwsza regula FORWARD to nie skok do {cfg['chain']} "
                        f"- cos zostalo wstawione przed panelem")
    return findings


# --------------------------------------------------------------------------
# operacje wystawione panelowi
# --------------------------------------------------------------------------

def op_ping(_payload: dict) -> dict:
    return {"pong": True, "pid": os.getpid()}


def op_server_pubkey(payload: dict) -> dict:
    cfg = validate_settings(payload["settings"])
    result = run(["wg", "pubkey"], input=read_private_key(cfg))
    if result.returncode != 0:
        raise Rejected(f"nie udalo sie wyliczyc klucza publicznego: {result.stderr.strip()}")
    return {"pubkey": result.stdout.strip()}


def _wg_dump(iface: str) -> tuple:
    """`wg show <iface> dump` -> (dziala, port nasluchu, {pubkey: pola peera})."""
    dump = run(["wg", "show", iface, "dump"])
    if dump.returncode != 0:
        return False, "", {}
    port, peers = "", {}
    for index, line in enumerate(dump.stdout.strip().split("\n")):
        fields = line.split("\t")
        if index == 0:
            port = fields[2] if len(fields) > 2 else ""
            continue
        if len(fields) < 8:
            continue
        peers[fields[0]] = {
            "endpoint": fields[2], "allowed_ips": fields[3],
            "latest_handshake": int(fields[4] or 0),
            "rx": int(fields[5] or 0), "tx": int(fields[6] or 0),
        }
    return True, port, peers


def op_peers(payload: dict) -> dict:
    """Same adresy i handshake'i - lekka probka co kilka sekund (wykrywanie tego samego pliku
    na dwoch urzadzeniach). Bez systemctl i sum plikow, w przeciwienstwie do status."""
    up, _port, peers = _wg_dump(_iface(payload.get("wg_iface", "wg0"), "wg_iface"))
    return {"up": up, "peers": {key: {"endpoint": value["endpoint"],
                                      "latest_handshake": value["latest_handshake"]}
                                for key, value in peers.items()}}


def op_status(payload: dict) -> dict:
    cfg = validate_settings(payload["settings"])
    iface = cfg["wg_iface"]
    out: dict = {"iface": iface, "peers": {}, "up": False, "pending": None}

    up, port, peers = _wg_dump(iface)
    if up:
        out.update(up=True, listen_port=port, peers=peers)

    # odcisk plikow na dysku - panel porownuje go z tym, co wygenerowalby teraz,
    # dzieki czemu "niezastosowane zmiany" pokazuje stan faktyczny, a nie zapamietany
    path4 = rules_path(cfg)
    conf_text = ""
    if os.path.exists(conf_path(cfg)):
        with open(conf_path(cfg), encoding="utf8") as handle:
            conf_text = _mask_key(handle.read())
    out["files"] = {
        "rules_path": path4,   # panel renderuje z ta sama sciezka, inaczej PostUp by sie roznil
        "conf": hashlib.sha256(conf_text.encode()).hexdigest() if conf_text else "",
        "rules4": _sha(path4),
        "rules6": _sha(rules6_path_for(path4)),
    }

    unit = run(["systemctl", "is-active", f"wg-quick@{iface}"])
    out["service"] = unit.stdout.strip() or unit.stderr.strip() or "nieznany"
    tools = ["wg", "wg-quick", "iptables", "iptables-restore"]
    if cfg["ipv6"]:
        tools += ["ip6tables", "ip6tables-restore"]
    missing = [tool for tool in tools if shutil.which(tool) is None]
    if missing:
        out["missing_tools"] = missing

    with _state_lock:
        if _pending:
            out["pending"] = {"deadline": _pending["deadline"],
                              "remaining": max(0, int(_pending["deadline"] - time.time())),
                              "token": _pending["digest"]}
        if _last_auto_rollback:
            out["auto_rollback"] = dict(_last_auto_rollback)
    return out


def return_to(cfg: dict, previous: dict) -> list[str]:
    """Przywraca pliki sprzed zmiany i stan tunelu. Na czystym systemie poprzedni stan
    to brak wg0.conf - wtedy tunel sie zatrzymuje, zamiast probowac go podniesc."""
    restore(previous)
    if previous.get(conf_path(cfg)) is None:
        run(["systemctl", "stop", f"wg-quick@{cfg['wg_iface']}"])
        return [f"wg-quick@{cfg['wg_iface']}: zatrzymany (przed zmiana tunelu nie bylo)"]
    return apply_files(cfg)


def _render_all(cfg: dict, peers: list[dict], private_key: str) -> tuple[str, str, str]:
    conf = render_wg_conf(cfg, peers, private_key, rules_path(cfg))
    rules4 = render_rules_file(cfg, peers, 4)
    rules6 = render_rules_file(cfg, peers, 6) if cfg["ipv6"] else ""
    return conf, rules4, rules6


def op_apply(payload: dict) -> dict:
    global _pending
    cfg = validate_settings(payload["settings"])
    peers = validate_peers(payload.get("peers"), cfg)
    confirm_seconds = int(payload.get("confirm_seconds") or 0)
    if not 0 <= confirm_seconds <= 900:
        raise Rejected("confirm_seconds poza zakresem 0-900")

    with _state_lock:
        if _pending:
            raise Rejected("trwa juz zmiana czekajaca na potwierdzenie")

    conf, rules4, rules6 = _render_all(cfg, peers, read_private_key(cfg))
    previous = snapshot(cfg)
    archive(cfg)
    write_private(conf_path(cfg), conf)
    write_private(rules_path(cfg), rules4)
    path6 = rules6_path_for(rules_path(cfg))
    if cfg["ipv6"]:
        write_private(path6, rules6)
    elif os.path.exists(path6):
        os.remove(path6)

    try:
        steps = apply_files(cfg)
    except Exception as exc:
        try:
            return_to(cfg, previous)
        except Exception:
            log.exception("rollback po nieudanym apply tez sie nie powiodl")
        raise Rejected(f"apply nieudany, przywrocono poprzedni stan: {exc}") from exc

    token = base64.urlsafe_b64encode(os.urandom(18)).decode().rstrip("=")
    if confirm_seconds:
        _arm(token_digest(token), cfg, payload["settings"], previous, confirm_seconds)
        steps.append(f"commit-confirm: rollback za {confirm_seconds} s bez potwierdzenia")

    return {"steps": steps, "confirm_token": token if confirm_seconds else None,
            "peers_active": sum(1 for p in peers if p["state"] == "active"),
            "fingerprint": fingerprint(cfg)}


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()[:16] if token else ""


def _save_state(state: dict | None) -> None:
    """Zapis na dysk (tylko root). Oczekujaca zmiana zostaje w pliku az do potwierdzenia
    albo cofniecia - gdyby helper padl w trakcie cofania, po starcie cofnie jeszcze raz."""
    try:
        if state is None:
            if os.path.exists(STATE_PATH):
                os.remove(STATE_PATH)
        else:
            write_private(STATE_PATH, json.dumps(state))
    except OSError:
        log.exception("nie udalo sie zapisac stanu oczekujacej zmiany")


def _arm(digest: str, cfg: dict, raw_settings: dict, previous: dict, seconds: int,
         persist: bool = True) -> None:
    """Uzbraja licznik automatycznego cofniecia dla zmiany o danym skrocie tokenu."""
    global _pending
    timer = threading.Timer(seconds, _auto_rollback, args=(cfg, previous, digest))
    timer.daemon = True
    with _state_lock:
        _pending = {"digest": digest, "cfg": cfg, "previous": previous, "timer": timer,
                    "deadline": time.time() + seconds}
    if persist:
        _save_state({"pending": {"digest": digest, "settings": raw_settings,
                                 "previous": previous, "seconds": seconds}})
    timer.start()


def restore_state() -> None:
    """Start helpera: wraca oczekujaca zmiana (ze swiezym oknem) i slad po auto-cofnieciu.

    Swieze okno zamiast zapisanego terminu - Raspberry Pi nie ma zegara sprzetowego,
    wiec zaraz po restarcie godzina bywa zla, dopoki nie zsynchronizuje sie z siecia.
    """
    global _last_auto_rollback
    try:
        with open(STATE_PATH, encoding="utf8") as handle:
            state = json.load(handle)
    except FileNotFoundError:
        return
    except (OSError, ValueError):
        log.exception("uszkodzony plik stanu %s - pomijam", STATE_PATH)
        return
    if state.get("auto_rollback"):
        _last_auto_rollback = state["auto_rollback"]
    pending = state.get("pending")
    if not pending:
        return
    try:
        cfg = validate_settings(pending["settings"])
    except Exception:  # noqa: BLE001
        log.exception("nie moge odtworzyc oczekujacej zmiany - zle ustawienia")
        return
    seconds = max(30, min(900, int(pending.get("seconds") or 90)))
    _arm(pending["digest"], cfg, pending["settings"], pending["previous"], seconds,
         persist=False)
    log.warning("po restarcie: zmiana czeka na potwierdzenie, cofne ja za %s s", seconds)


def _auto_rollback(cfg: dict, previous: dict, digest: str) -> None:
    global _pending, _last_auto_rollback
    with _state_lock:
        if not _pending or _pending["digest"] != digest:
            return
        _pending = None
        # zapis od razu, zanim zaczniemy cofac - panel pytajacy w tej chwili wie, co sie dzieje
        _last_auto_rollback = {"token": digest, "at": int(time.time()), "done": False}
    log.warning("brak potwierdzenia - automatyczny rollback")
    try:
        return_to(cfg, previous)
    except Exception:
        log.exception("automatyczny rollback nie powiodl sie")
    # odcisk tego, co naprawde wrocilo na dysk - bez niego kontrola integralnosci
    # uznalaby cofniecie za zmiane zrobiona poza panelem
    try:
        state = fingerprint(cfg)
    except Exception:  # noqa: BLE001
        state = None
    with _state_lock:
        if _last_auto_rollback and _last_auto_rollback["token"] == digest:
            _last_auto_rollback.update(done=True, fingerprint=state)
            record = dict(_last_auto_rollback)
        else:
            record = None
    _save_state({"auto_rollback": record} if record else None)


def op_confirm(payload: dict) -> dict:
    global _pending
    token = str(payload.get("token", ""))
    with _state_lock:
        if not _pending:
            if _last_auto_rollback and _last_auto_rollback["token"] == token_digest(token):
                raise Rejected("za pozno - brak potwierdzenia na czas, zmiany cofnely sie "
                               "automatycznie")
            raise Rejected("nic nie czeka na potwierdzenie")
        if _pending["digest"] != token_digest(token):
            raise Rejected("zly token potwierdzenia")
        _pending["timer"].cancel()
        cfg = _pending["cfg"]
        _pending = None
    _save_state(None)
    return {"confirmed": True, "fingerprint": fingerprint(cfg)}


def op_rollback(payload: dict) -> dict:
    global _pending
    with _state_lock:
        pending = _pending
        _pending = None
    if pending:
        pending["timer"].cancel()
        steps = return_to(pending["cfg"], pending["previous"])
        _save_state(None)
        return {"rolled_back": "pending", "steps": steps,
                "fingerprint": fingerprint(pending["cfg"])}
    if payload.get("only_pending"):
        # pasek oczekiwania mogl byc nieaktualny - bez tego cofnelibysmy do ostatniej
        # kopii, czyli wycofali zmiane, ktora wlasnie zostala potwierdzona
        if _last_auto_rollback and payload.get("token") and \
                _last_auto_rollback["token"] == token_digest(str(payload["token"])):
            raise Rejected("zmiany juz cofnely sie automatycznie - nic wiecej nie cofam")
        raise Rejected("nic nie czeka na potwierdzenie - nic nie cofnieto")

    cfg = validate_settings(payload["settings"])
    if not os.path.isdir(BACKUP_DIR):
        raise Rejected("brak kopii zapasowych")
    base = os.path.basename(conf_path(cfg))
    candidates = sorted(f for f in os.listdir(BACKUP_DIR) if f.startswith(base + "."))
    if not candidates:
        raise Rejected("brak kopii wg0.conf")
    stamp = candidates[-1].rsplit(".", 1)[1]
    files = {}
    for path in managed_files(cfg):
        candidate = os.path.join(BACKUP_DIR, f"{os.path.basename(path)}.{stamp}")
        files[path] = open(candidate, encoding="utf8").read() if os.path.exists(candidate) else None
    restore(files)
    return {"rolled_back": stamp, "steps": apply_files(cfg), "fingerprint": fingerprint(cfg)}


def op_restart(payload: dict) -> dict:
    cfg = validate_settings(payload["settings"])
    result = run(["systemctl", "restart", f"wg-quick@{cfg['wg_iface']}"])
    if result.returncode != 0:
        raise Rejected(f"restart nieudany: {result.stderr.strip()}")
    return {"restarted": True}


def op_preview(payload: dict) -> dict:
    """To, co helper by zapisal - bez zapisywania czegokolwiek."""
    cfg = validate_settings(payload["settings"])
    peers = validate_peers(payload.get("peers"), cfg)
    conf, rules4, rules6 = _render_all(cfg, peers, "__SERVER_PRIVATE_KEY__")

    def current(path: str) -> str:
        return open(path, encoding="utf8").read() if os.path.exists(path) else ""

    return {
        "wg_conf": conf,
        "rules": rules4,
        "rules6": rules6,
        "current_wg_conf": _mask_key(current(conf_path(cfg))),
        "current_rules": current(rules_path(cfg)),
        "current_rules6": current(rules6_path_for(rules_path(cfg))),
    }


def op_drift(payload: dict) -> dict:
    cfg = validate_settings(payload["settings"])
    expected = payload.get("expected") or {}
    if not isinstance(expected, dict):
        raise Rejected("expected musi byc obiektem")
    return {"findings": detect_drift(cfg, expected), "checked_at": int(time.time())}


def op_detect_lan6(payload: dict) -> dict:
    """Podpowiedz prefiksu IPv6 sieci domowej na podstawie tras na WAN."""
    cfg = validate_settings(dict(payload["settings"], ipv6_enabled="0"))
    result = run(["ip", "-6", "route", "show", "dev", cfg["wan_if"]])
    prefixes = []
    for line in result.stdout.split("\n"):
        token = line.split(" ")[0] if line else ""
        if not token or token == "default" or token.startswith("fe80") or "/" not in token:
            continue
        try:
            network = ipaddress.ip_network(token, strict=False)
        except ValueError:
            continue
        if network.version == 6 and network.prefixlen < 128:
            prefixes.append(str(network))
    return {"prefixes": sorted(set(prefixes))}


def _mask_key(text: str) -> str:
    return "\n".join(
        "PrivateKey = __SERVER_PRIVATE_KEY__" if line.strip().startswith("PrivateKey") else line
        for line in text.split("\n")
    )


# --------------------------------------------------------------------------
# HTTPS: wlasny urzad certyfikacji z ograniczeniem nazw
# --------------------------------------------------------------------------

_tls_lock = threading.Lock()


def _openssl(*args: str) -> str:
    try:
        result = subprocess.run(["openssl", *args], capture_output=True, text=True, timeout=60)
    except FileNotFoundError as exc:
        raise Rejected("brak programu openssl - zainstaluj: sudo apt install openssl") from exc
    if result.returncode != 0:
        lines = (result.stderr or "").strip().splitlines() or ["nieznany blad"]
        raise Rejected(f"openssl: {lines[-1]}")
    return result.stdout


def _read_json(path: str) -> dict:
    try:
        with open(path, encoding="utf8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def _fingerprint(pem_path: str) -> str:
    with open(pem_path, encoding="ascii") as handle:
        der = ssl.PEM_cert_to_DER_cert(handle.read())
    digest = hashlib.sha256(der).hexdigest().upper()
    return ":".join(digest[i:i + 2] for i in range(0, len(digest), 2))


def _tls_names(cfg: dict) -> tuple:
    """Adresy i nazwy w certyfikacie panelu oraz sieci, do ktorych ograniczony jest urzad.

    Urzad zainstalowany na telefonie jest zaufany dla kazdej strony - dlatego dostaje
    ograniczenie nazw (X.509 Name Constraints): nawet wykradziony klucz pozwala wystawic
    certyfikat wylacznie dla adresow sieci domowej, VPN i nazwy Pi.
    """
    nets = [ipaddress.ip_network(cfg["lan"], strict=False),
            ipaddress.ip_network(cfg["vpn"], strict=False)]
    ips = {cfg["server_ip"]}
    if cfg.get("ipv6"):
        nets.append(ipaddress.ip_network(cfg["vpn6"], strict=False))
        nets += [ipaddress.ip_network(item, strict=False) for item in cfg.get("lan6", [])]
        ips.add(cfg["server_ip6"])
    listing = run(["ip", "-o", "addr", "show", "scope", "global"]).stdout or ""
    for match in re.finditer(r"\binet6?\s+([0-9a-fA-F:.]+)/\d+", listing):
        try:
            address = ipaddress.ip_address(match.group(1))
        except ValueError:
            continue
        if any(address.version == net.version and address in net for net in nets):
            ips.add(str(address))
    host = re.sub(r"[^a-z0-9-]", "", socket.gethostname().split(".")[0].lower()) or "wg-panel"
    dns = [host, host + ".local"]
    ordered = sorted(ips, key=lambda a: (ipaddress.ip_address(a).version, ipaddress.ip_address(a)))
    return ordered, dns, sorted({str(net) for net in nets})


def _ensure_ca(dns: list, permitted: list) -> bool:
    """Tworzy urzad, gdy go nie ma albo gdy jego ograniczenia nie obejmuja sieci panelu."""
    crt, key = os.path.join(CA_DIR, "ca.crt"), os.path.join(CA_DIR, "ca.key")
    meta = _read_json(os.path.join(CA_DIR, "ca.json"))
    if (os.path.exists(crt) and os.path.exists(key)
            and set(permitted) <= set(meta.get("permitted", []))
            and set(dns) <= set(meta.get("dns", []))):
        return False
    os.makedirs(CA_DIR, exist_ok=True)
    os.chmod(CA_DIR, 0o700)
    constraints = []
    for item in permitted:
        net = ipaddress.ip_network(item, strict=False)
        constraints.append(f"permitted;IP:{net.network_address}/{net.netmask}")
    constraints += [f"permitted;DNS:{name}" for name in dns]
    with tempfile.TemporaryDirectory(dir=CA_DIR) as tmp:
        new_key, new_crt = os.path.join(tmp, "ca.key"), os.path.join(tmp, "ca.crt")
        _openssl("req", "-x509", "-new", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
                 "-nodes", "-sha256", "-keyout", new_key, "-out", new_crt, "-days", str(CA_DAYS),
                 "-subj", f"/CN=wg-panel CA ({dns[0]})",
                 "-addext", "basicConstraints=critical,CA:TRUE,pathlen:0",
                 "-addext", "keyUsage=critical,keyCertSign,cRLSign",
                 "-addext", "nameConstraints=critical," + ",".join(constraints))
        os.chmod(new_key, 0o600)
        os.replace(new_key, key)
        os.replace(new_crt, crt)
    write_private(os.path.join(CA_DIR, "ca.json"), json.dumps(
        {"permitted": permitted, "dns": dns, "created": int(time.time())}))
    log.warning("utworzono nowy urzad certyfikacji panelu - trzeba go zainstalowac na urzadzeniach")
    return True


def _tls_publish(path: str, content: bytes, mode: int) -> None:
    """Plik dla panelu: root:wg-panel, zapis atomowy."""
    handle, tmp = tempfile.mkstemp(dir=TLS_DIR)
    with os.fdopen(handle, "wb") as out:
        out.write(content)
    try:
        os.chown(tmp, 0, grp.getgrnam(SOCKET_GROUP).gr_gid)
    except (KeyError, OSError):
        pass
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def _tls_dir() -> None:
    os.makedirs(TLS_DIR, exist_ok=True)
    try:
        os.chown(TLS_DIR, 0, grp.getgrnam(SOCKET_GROUP).gr_gid)
    except (KeyError, OSError):
        pass
    os.chmod(TLS_DIR, 0o750)


def _ensure_server(ips: list, dns: list, force: bool) -> bool:
    crt, key = os.path.join(TLS_DIR, "panel.crt"), os.path.join(TLS_DIR, "panel.key")
    ca_crt, ca_key = os.path.join(CA_DIR, "ca.crt"), os.path.join(CA_DIR, "ca.key")
    ca_print = _fingerprint(ca_crt)
    meta = _read_json(os.path.join(TLS_DIR, "panel.json"))
    if (not force and os.path.exists(crt) and os.path.exists(key)
            and meta.get("ips") == ips and meta.get("dns") == dns and meta.get("ca") == ca_print
            and meta.get("not_after", 0) - time.time() > RENEW_BEFORE):
        return False
    _tls_dir()
    san = ",".join([f"IP:{ip}" for ip in ips] + [f"DNS:{name}" for name in dns])
    with tempfile.TemporaryDirectory(dir=CA_DIR) as tmp:
        new_key, csr = os.path.join(tmp, "panel.key"), os.path.join(tmp, "panel.csr")
        new_crt, ext = os.path.join(tmp, "panel.crt"), os.path.join(tmp, "ext.cnf")
        with open(ext, "w", encoding="ascii") as handle:
            handle.write("basicConstraints=critical,CA:FALSE\n"
                         "keyUsage=critical,digitalSignature\n"
                         "extendedKeyUsage=serverAuth\n"
                         f"subjectAltName={san}\n"
                         "authorityKeyIdentifier=keyid\n"
                         "subjectKeyIdentifier=hash\n")
        _openssl("req", "-new", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
                 "-nodes", "-keyout", new_key, "-out", csr, "-subj", "/CN=wg-panel")
        _openssl("x509", "-req", "-in", csr, "-CA", ca_crt, "-CAkey", ca_key,
                 "-set_serial", "0x" + secrets.token_hex(16), "-days", str(SERVER_DAYS),
                 "-sha256", "-extfile", ext, "-out", new_crt)
        end = _openssl("x509", "-in", new_crt, "-noout", "-enddate").strip().split("=", 1)[1]
        with open(new_key, "rb") as handle:
            key_bytes = handle.read()
        with open(new_crt, "rb") as handle:
            crt_bytes = handle.read()
    with open(ca_crt, "rb") as handle:
        _tls_publish(os.path.join(TLS_DIR, "ca.crt"), handle.read(), 0o644)
    _tls_publish(key, key_bytes, 0o640)
    _tls_publish(crt, crt_bytes, 0o644)
    _tls_publish(os.path.join(TLS_DIR, "panel.json"), json.dumps(
        {"ips": ips, "dns": dns, "ca": ca_print,
         "not_after": int(ssl.cert_time_to_seconds(end))}).encode(), 0o644)
    return True


def _cert_allowed(path: str) -> str:
    real = os.path.realpath(path)
    for base in CERT_DIRS:
        root = os.path.realpath(base).rstrip("/") + "/"
        if os.path.isabs(path) and real.startswith(root):
            return real
    raise Rejected("pliki certyfikatu musza lezec w " + " albo ".join(CERT_DIRS))


def _custom_cert(cert: str, key: str) -> dict:
    """Wlasny certyfikat z plikow: sprawdza i kopiuje dla panelu (przy kazdym odswiezeniu,
    wiec odnowienie przez certbota trafia do panelu samo)."""
    cert_real, key_real = _cert_allowed(cert), _cert_allowed(key)
    try:
        with open(cert_real, "rb") as handle:
            chain = handle.read()
        with open(key_real, "rb") as handle:
            key_bytes = handle.read()
    except OSError as exc:
        raise Rejected(f"nie moge odczytac pliku: {exc.strerror}") from exc
    if b"-----BEGIN CERTIFICATE-----" not in chain:
        raise Rejected("to nie jest certyfikat w formacie PEM")
    if b"PRIVATE KEY-----" not in key_bytes:
        raise Rejected("to nie jest klucz prywatny w formacie PEM")
    if _openssl("x509", "-in", cert_real, "-noout", "-pubkey").strip() != \
            _openssl("pkey", "-in", key_real, "-pubout").strip():
        raise Rejected("klucz nie pasuje do certyfikatu")
    end = _openssl("x509", "-in", cert_real, "-noout", "-enddate").strip().split("=", 1)[1]
    try:
        names = re.findall(r"(?:DNS|IP Address):([^,\s]+)",
                           _openssl("x509", "-in", cert_real, "-noout", "-ext", "subjectAltName"))
    except Rejected:
        names = []
    _tls_dir()
    changed = False
    for name, content, mode in (("panel.crt", chain, 0o644), ("panel.key", key_bytes, 0o640)):
        target = os.path.join(TLS_DIR, name)
        try:
            with open(target, "rb") as handle:
                same = handle.read() == content
        except OSError:
            same = False
        if not same:
            _tls_publish(target, content, mode)
            changed = True
    meta = {"mode": "custom", "not_after": int(ssl.cert_time_to_seconds(end)), "names": names,
            "source": cert}
    _tls_publish(os.path.join(TLS_DIR, "panel.json"), json.dumps(meta).encode(), 0o644)
    return dict(meta, renewed=changed)


def op_tls(payload: dict) -> dict:
    """Pilnuje certyfikatu panelu: tworzy, odnawia przed wygasnieciem i po zmianie adresu.
    Tryb "custom": zamiast wlasnego urzedu - certyfikat z plikow (np. Let's Encrypt)."""
    if payload.get("mode") == "custom":
        with _tls_lock:
            return _custom_cert(str(payload.get("cert", "")), str(payload.get("key", "")))
    cfg = validate_settings(payload["settings"])
    ips, dns, permitted = _tls_names(cfg)
    with _tls_lock:        # osobna blokada - certyfikat nie wstrzymuje potwierdzania zmian
        ca_new = _ensure_ca(dns, permitted)
        renewed = _ensure_server(ips, dns, bool(payload.get("renew")) or ca_new)
    meta = _read_json(os.path.join(TLS_DIR, "panel.json"))
    ca_meta = _read_json(os.path.join(CA_DIR, "ca.json"))
    return {"ca_fingerprint": _fingerprint(os.path.join(CA_DIR, "ca.crt")),
            "ca_created": ca_meta.get("created"), "ca_new": ca_new, "renewed": renewed,
            "not_after": meta.get("not_after"), "ips": ips, "dns": dns,
            "permitted": permitted}


# --------------------------------------------------------------------------
# pelna kopia: klucz serwera i urzad certyfikacji (panel szyfruje je haslem kopii)
# --------------------------------------------------------------------------

KEY_RE = re.compile(r"^[A-Za-z0-9+/]{42}[AEIMQUYcgkosw048]=$")


def op_system_export(payload: dict) -> dict:
    """Tozsamosc tego Pi do pelnej kopii: klucz prywatny serwera i urzad certyfikacji panelu.
    Panel wola to tylko przy kopii z haslem - bez hasla klucz nie wychodzi z helpera."""
    cfg = validate_settings(payload["settings"])
    out = {"server_key": read_private_key(cfg), "ca": None}
    crt, key = os.path.join(CA_DIR, "ca.crt"), os.path.join(CA_DIR, "ca.key")
    if os.path.exists(crt) and os.path.exists(key):
        out["ca"] = {"crt": open(crt, encoding="utf8").read(),
                     "key": open(key, encoding="utf8").read(),
                     "json": json.dumps(_read_json(os.path.join(CA_DIR, "ca.json")))}
    log.info("pelna kopia: wydano klucz serwera i urzad certyfikacji")
    return out


def _set_conf_key(path: str, key: str) -> bool:
    """Podmienia PrivateKey w istniejacym wg0.conf (helper czyta klucz najpierw stamtad)."""
    if not os.path.exists(path):
        return False
    with open(path, encoding="utf8") as handle:
        lines = handle.read().split("\n")
    changed = False
    for index, line in enumerate(lines):
        if line.strip().startswith("PrivateKey"):
            lines[index] = f"PrivateKey = {key}"
            changed = True
    if changed:
        write_private(path, "\n".join(lines))
    return changed


def op_system_import(payload: dict) -> dict:
    """Odtworzenie tozsamosci z pelnej kopii: klucz serwera (stare pliki osob dzialaja dalej)
    i urzad certyfikacji (nie trzeba ponownie instalowac ca.crt na urzadzeniach)."""
    cfg = validate_settings(payload["settings"])
    result = {"key_changed": False, "ca_restored": False}
    key = str(payload.get("server_key") or "").strip()
    if key:
        if not KEY_RE.match(key):
            raise Rejected("to nie jest klucz prywatny WireGuard")
        try:
            current = read_private_key(cfg)
        except Rejected:
            current = ""
        if key != current:
            os.makedirs(WG_DIR, exist_ok=True)
            os.chmod(WG_DIR, 0o700)
            write_private(os.path.join(WG_DIR, "server_private.key"), key + "\n")
            _set_conf_key(conf_path(cfg), key)
            result["key_changed"] = True
            log.warning("pelna kopia: przywrocono klucz serwera")
    ca = payload.get("ca")
    if isinstance(ca, dict) and ca.get("crt") and ca.get("key"):
        if "BEGIN CERTIFICATE" not in ca["crt"] or "PRIVATE KEY" not in ca["key"]:
            raise Rejected("urzad certyfikacji w kopii jest uszkodzony")
        with _tls_lock:
            os.makedirs(CA_DIR, exist_ok=True)
            os.chmod(CA_DIR, 0o700)
            write_private(os.path.join(CA_DIR, "ca.key"), ca["key"])
            write_private(os.path.join(CA_DIR, "ca.crt"), ca["crt"])
            write_private(os.path.join(CA_DIR, "ca.json"), ca.get("json") or "{}")
            os.chmod(os.path.join(CA_DIR, "ca.crt"), 0o644)
            # certyfikat panelu od nowa z przywroconego urzedu (albo nowy urzad, jesli stary
            # nie obejmuje obecnej nazwy Pi lub sieci - wtedy trzeba zainstalowac nowy ca.crt)
            if cfg.get("tls", True):
                try:
                    ips, dns, permitted = _tls_names(cfg)
                    result["ca_new"] = _ensure_ca(dns, permitted)
                    _ensure_server(ips, dns, True)
                except Exception as exc:  # noqa: BLE001 - certyfikat nie moze zatrzymac odtwarzania
                    result["tls_error"] = str(exc)
        result["ca_restored"] = not result.get("ca_new", False)
    return result


OPS = {
    "ping": op_ping,
    "status": op_status,
    "peers": op_peers,
    "server_pubkey": op_server_pubkey,
    "preview": op_preview,
    "apply": op_apply,
    "confirm": op_confirm,
    "rollback": op_rollback,
    "restart": op_restart,
    "drift": op_drift,
    "detect_lan6": op_detect_lan6,
    "tls": op_tls,
    "system_export": op_system_export,
    "system_import": op_system_import,
}


# --------------------------------------------------------------------------
# serwer
# --------------------------------------------------------------------------

QUIET_OPS = {"peers"}


class Handler(socketserver.StreamRequestHandler):
    timeout = 120

    def handle(self) -> None:
        raw = self.rfile.readline(MAX_REQUEST)
        if not raw:
            return
        try:
            request = json.loads(raw.decode("utf8"))
            op = str(request.get("op", ""))
            if op not in OPS:
                raise Rejected(f"nieznana operacja: {op!r}")
            payload = request.get("payload") or {}
            if not isinstance(payload, dict):
                raise Rejected("payload musi byc obiektem")
            # szybka probka idzie co 5 s - w dzienniku bylaby szumem (i zapisami na karte)
            (log.debug if op in QUIET_OPS else log.info)("op=%s", op)
            response = {"ok": True, "result": OPS[op](payload)}
        except Rejected as exc:
            log.warning("odrzucone: %s", exc)
            response = {"ok": False, "error": str(exc)}
        except Exception as exc:  # noqa: BLE001
            log.exception("blad operacji")
            response = {"ok": False, "error": f"blad wewnetrzny: {exc}"}
        self.wfile.write(json.dumps(response).encode("utf8") + b"\n")


class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True


def main() -> int:
    logging.basicConfig(level=os.environ.get("WGPANEL_LOGLEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(message)s")
    if os.geteuid() != 0:
        print("helper musi dzialac jako root", file=sys.stderr)
        return 1

    directory = os.path.dirname(SOCKET_PATH)
    os.makedirs(directory, exist_ok=True)
    if os.path.exists(SOCKET_PATH):
        os.remove(SOCKET_PATH)

    server = Server(SOCKET_PATH, Handler)
    try:
        gid = grp.getgrnam(SOCKET_GROUP).gr_gid
        # katalog tez musi nalezec do grupy - inaczej panel nie wejdzie do srodka
        # i dostanie "Permission denied" mimo dobrych praw na samym gniezdzie
        os.chown(directory, 0, gid)
        os.chmod(directory, 0o750)
        os.chown(SOCKET_PATH, 0, gid)
        os.chmod(SOCKET_PATH, 0o660)
    except KeyError:
        os.chmod(SOCKET_PATH, 0o600)
        log.warning("grupa %s nie istnieje - gniazdo tylko dla roota", SOCKET_GROUP)
    except OSError as exc:
        log.warning("nie udalo sie ustawic praw gniazda: %s", exc)

    restore_state()
    log.info("nasluchuje na %s", SOCKET_PATH)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if os.path.exists(SOCKET_PATH):
            os.remove(SOCKET_PATH)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
