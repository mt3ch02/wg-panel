"""Generowanie artefaktow z bazy: wg0.conf, pliki regul iptables/ip6tables, konfiguracje klientow.

Panel generuje to samo, co pozniej niezaleznie wygeneruje helper - panel po to,
zeby pokazac podglad i diff, helper po to, zeby nie ufac danym z panelu.
Algorytm musi byc identyczny w obu miejscach; pilnuje tego selftest.

Regula po normalizacji ma dwa cele:
  dest  - jeden cel IPv4 (CIDR albo "ANY"), albo "" gdy regula nie dotyczy IPv4
  dest6 - lista celow IPv6 (CIDR-y albo ["ANY"]), albo [] gdy nie dotyczy IPv6
Dzieki temu "LAN" albo nazwa hosta z katalogu rozwija sie na obie rodziny naraz
i blokada LAN-u w IPv4 nie zostawia otwartej furtki w IPv6.
"""
from __future__ import annotations

import ipaddress
import json

from .i18n import _

PLACEHOLDER_KEY = "__SERVER_PRIVATE_KEY__"
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


class RuleError(ValueError):
    pass


def ipv6_on(cfg: dict) -> bool:
    return str(cfg.get("ipv6_enabled", "0")) == "1"


def _prefix_len(cidr: str) -> int:
    return ipaddress.ip_network(cidr, strict=False).prefixlen


def _split_list(value: str) -> list[str]:
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def _net(value: str) -> ipaddress._BaseNetwork:
    return ipaddress.ip_network(value, strict=False)


def resolve_dest(dest: str, cfg: dict) -> tuple[str, list[str], str]:
    """Zamienia cel reguly na (dest4, dest6, etykieta)."""
    raw = str(dest or "ANY").strip() or "ANY"
    label = raw
    hosts = cfg.get("_hosts") or {}

    if raw.upper() == "ANY":
        return "ANY", ["ANY"], "ANY"

    if raw.upper() == "LAN":
        lan6 = [str(_net(item)) for item in _split_list(cfg.get("lan6", ""))]
        return str(_net(cfg["lan"])), lan6, "LAN"

    name = raw[1:] if raw.startswith("@") else raw
    entry = hosts.get(name.lower())
    if entry is not None:
        v4 = str(_net(entry["v4"])) if entry.get("v4") else ""
        v6 = [str(_net(item)) for item in _split_list(entry.get("v6", ""))]
        if not v4 and not v6:
            raise RuleError(f"host {name!r} nie ma zadnego adresu w katalogu")
        return v4, v6, name.lower()

    try:
        network = _net(raw)
    except ValueError as exc:
        raise RuleError(f"nieznany cel {raw!r} - podaj adres, LAN, ANY "
                        f"albo nazwe z katalogu hostow") from exc
    if network.version == 4:
        return str(network), [], label
    return "", [str(network)], label


def normalize_rule(raw: dict, cfg: dict) -> dict:
    """Sprowadza regule do postaci kanonicznej i waliduje kazde pole."""
    action = str(raw.get("action", "")).upper()
    if action not in ("ACCEPT", "DROP"):
        raise RuleError(f"niedozwolona akcja: {action!r}")

    dest, dest6, label = resolve_dest(raw.get("dest", "ANY"), cfg)

    proto = str(raw.get("proto", "any")).lower()
    if proto not in ("any", "tcp", "udp"):
        raise RuleError(f"niedozwolony protokol: {proto!r}")

    ports_raw = str(raw.get("ports", "")).replace(" ", "")
    ports: list[int] = []
    if ports_raw:
        if proto == "any":
            raise RuleError("porty wymagaja protokolu tcp albo udp")
        for chunk in ports_raw.split(","):
            if not chunk.isdigit():
                raise RuleError(f"zly port: {chunk!r}")
            port = int(chunk)
            if not 1 <= port <= 65535:
                raise RuleError(f"port poza zakresem: {port}")
            ports.append(port)
        if len(ports) > 15:
            raise RuleError("multiport przyjmuje najwyzej 15 portow")

    time_from = str(raw.get("time_from", "")).strip()
    time_to = str(raw.get("time_to", "")).strip()
    for value in (time_from, time_to):
        if value and not _valid_hhmm(value):
            raise RuleError(f"zly czas: {value!r}")
    if bool(time_from) != bool(time_to):
        raise RuleError("okno czasowe wymaga obu godzin")

    days = [d for d in str(raw.get("weekdays", "")).replace(" ", "").split(",") if d]
    for day in days:
        if day not in WEEKDAYS:
            raise RuleError(f"zly dzien tygodnia: {day!r}")

    comment = clean_comment(raw.get("comment", ""))

    return {
        "action": action,
        "dest": dest,
        "dest6": dest6,
        "label": label,
        "proto": proto,
        "ports": ports,
        "time_from": time_from,
        "time_to": time_to,
        "weekdays": days,
        "comment": comment,
    }


def clean_comment(text) -> str:
    """Komentarz reguly w postaci bezpiecznej dla iptables (bez cudzyslowow i znakow powloki)."""
    return "".join(ch for ch in str(text) if ch.isalnum() or ch in " .:-_/")[:200]


def _valid_hhmm(value: str) -> bool:
    parts = value.split(":")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        return False
    hour, minute = int(parts[0]), int(parts[1])
    return 0 <= hour <= 23 and 0 <= minute <= 59


def rule_to_args(chain: str, source: str, dest: str, rule: dict) -> list[str]:
    """Jedna regula iptables. `source` z maska (/32 albo /128), `dest` CIDR albo ANY."""
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


def expand_peer_rules(peer: dict, cfg: dict) -> list[dict]:
    """Reguly wlasne peera, potem reguly tieru.

    Kolejnosc = kolejnosc sprawdzania w lancuchu, a regula bardziej szczegolowa
    musi byc wyzej. Tier NAS konczy sie blokada calego LAN-u, wiec gdyby reguly
    wlasne szly po nim, wyjatek na pojedynczy host nigdy by nie zadzialal.
    """
    out = []
    for raw in peer.get("custom_rules", []):
        if not raw.get("enabled", 1):
            continue
        rule = normalize_rule(raw, cfg)
        rule["_own"] = True
        out.append(rule)
    for raw in json.loads(peer.get("rules_json") or "[]"):
        out.append(normalize_rule(raw, cfg))
    return out


def _parse_ports(value) -> set[int]:
    out = set()
    chunks = value if isinstance(value, (list, tuple, set)) else \
        str(value or "").replace(" ", "").split(",")
    for chunk in chunks:
        chunk = str(chunk)
        if chunk.isdigit():
            port = int(chunk)
            if 1 <= port <= 65535:
                out.add(port)
    return out


def _name(value: str, limit: int = 60) -> str:
    return "".join(ch for ch in str(value) if ch.isalnum() or ch in " .:-_/")[:limit]


def _safe(value: str) -> str:
    return "".join(ch for ch in str(value) if ch.isalnum() or ch in " .:-_")[:40]


def build_ruleset(cfg: dict, peers: list[dict], family: int = 4) -> dict:
    """Komplet regul obu lancuchow dla jednej rodziny adresow, w kolejnosci docelowej."""
    chain = cfg.get("chain", "WG-USERS")
    host_chain = chain + "-HOST"
    iface = cfg.get("wg_iface", "wg0")
    mask = "/32" if family == 4 else "/128"
    address_key = "ip" if family == 4 else "ip6"

    active = [p for p in peers if p.get("state") == "active" and p.get(address_key)]

    forward: list[list[str]] = [
        ["-A", chain, "-o", iface, "-m", "comment", "--comment",
         "blokada ruchu miedzy peerami", "-j", "DROP"],
    ]
    for peer in active:
        source = peer[address_key] + mask
        for rule in peer.get("rules") or expand_peer_rules(peer, cfg):
            targets = [rule["dest"]] if family == 4 else list(rule["dest6"])
            for dest in targets:
                if dest:
                    forward.append(rule_to_args(chain, source, dest, rule))
    forward.append(["-A", chain, "-m", "comment", "--comment", "domyslnie przepusc",
                    "-j", "ACCEPT"])

    host: list[list[str]] = []
    managed = set(_parse_ports(cfg.get("managed_host_ports", "22,3389")))
    for peer in active:
        ports = _parse_ports(peer.get("host_access", ""))
        managed |= ports
        if ports:
            host.append([
                "-A", host_chain, "-i", iface, "-s", peer[address_key] + mask, "-p", "tcp",
                "-m", "multiport", "--dports", ",".join(str(p) for p in sorted(ports)),
                "-m", "comment", "--comment", f"host access: {_safe(peer['name'])}",
                "-j", "ACCEPT",
            ])
    if managed:
        host.append([
            "-A", host_chain, "-i", iface, "-p", "tcp", "-m", "multiport",
            "--dports", ",".join(str(p) for p in sorted(managed)),
            "-m", "comment", "--comment", "reszta peerow bez dostepu do hosta",
            "-j", "DROP",
        ])
    return {"chain": chain, "host_chain": host_chain, "forward": forward, "host": host}


def render_rules_file(cfg: dict, peers: list[dict], family: int = 4) -> str:
    """Fragment w formacie iptables-restore --noflush (albo ip6tables-restore)."""
    rs = build_ruleset(cfg, peers, family)
    lines = ["# wygenerowane przez wg-panel - nie edytuj recznie", "*filter",
             f":{rs['chain']} - [0:0]", f":{rs['host_chain']} - [0:0]"]
    lines += [" ".join(_quote(a) for a in args) for args in rs["forward"]]
    lines += [" ".join(_quote(a) for a in args) for args in rs["host"]]
    lines.append("COMMIT")
    return "\n".join(lines) + "\n"


def _quote(arg: str) -> str:
    return f'"{arg}"' if " " in arg else arg


DOCKER_CHAIN = "DOCKER-USER"  # lancuch Dockera na reguly uzytkownika


def _hook_lines(tool: str, cfg: dict, rules_path: str, nat_source: str, masquerade: bool):
    chain = cfg.get("chain", "WG-USERS")
    host_chain = chain + "-HOST"
    iface = cfg.get("wg_iface", "wg0")
    wan = cfg["wan_if"]
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


def render_wg_conf(cfg: dict, peers: list[dict], rules_path: str) -> str:
    """wg0.conf z podpieciem lancuchow. Klucz prywatny podstawia helper."""
    v6 = ipv6_on(cfg)
    addresses = [f"{cfg['server_ip']}/{_prefix_len(cfg['vpn'])}"]
    if v6:
        addresses.append(f"{cfg['server_ip6']}/{_prefix_len(cfg['vpn6'])}")

    up4, down4 = _hook_lines("iptables", cfg, rules_path, cfg["vpn"], True)
    out = [
        "# Wygenerowane przez wg-panel. Zmiany rob w panelu, nie tutaj.",
        "[Interface]",
        f"Address    = {', '.join(addresses)}",
        f"ListenPort = {cfg['listen_port']}",
        f"PrivateKey = {PLACEHOLDER_KEY}",
        "",
        "# --- lancuchy zarzadzane przez panel (IPv4) ---",
        *up4,
    ]
    down = list(down4)
    if v6:
        rules6 = rules_path[:-3] + ".v6" if rules_path.endswith(".v4") else rules_path + "6"
        up6, down6 = _hook_lines("ip6tables", cfg, rules6, cfg["vpn6"],
                                 str(cfg.get("nat6", "1")) == "1")
        out += ["", "# --- lancuchy zarzadzane przez panel (IPv6) ---", *up6]
        down += down6
    out += ["", *down, ""]

    for peer in peers:
        if peer.get("state") != "active":
            continue
        # te same limity i znaki co _name() w helperze - komentarz musi wyjsc identyczny
        label = f"{_name(peer['name'], 60)} / {_name(peer.get('tier_name') or '', 30) or 'brak tieru'}"
        if peer.get("device"):
            label += f" / {_name(peer['device'], 60)}"
        allowed = [f"{peer['ip']}/32"]
        if v6 and peer.get("ip6"):
            allowed.append(f"{peer['ip6']}/128")
        out += ["[Peer]", f"# {label}", f"PublicKey    = {peer['pubkey']}"]
        if peer.get("psk"):
            out.append(f"PresharedKey = {peer['psk']}")
        out += [f"AllowedIPs   = {', '.join(allowed)}", ""]
    return "\n".join(out)


# nazwy do pokazania - tlumaczy je interfejs (_), tu zostaja polskie klucze
TUNNEL_MODES = {
    "full": "Cały ruch przez VPN",
    "auto": "Tylko to, do czego ma dostęp",
    "lan": "Cała sieć domowa",
    "custom": "Własna lista",
}


def tunnel_mode(peer: dict) -> tuple[str, str, bool]:
    """(tryb, lista dla custom, czy nadpisany u peera)."""
    own = (peer.get("tunnel_mode") or "").strip()
    if own in TUNNEL_MODES:
        return own, peer.get("tunnel_list") or "", True
    mode = {"split": "lan"}.get(peer.get("client_mode") or "auto", peer.get("client_mode") or "auto")
    if mode not in TUNNEL_MODES:
        mode = "auto"
    return mode, peer.get("client_list") or "", False


def _collapse(items: list[str]) -> list[str]:
    nets4 = [_net(i) for i in items if _net(i).version == 4]
    nets6 = [_net(i) for i in items if _net(i).version == 6]
    return ([str(n) for n in ipaddress.collapse_addresses(nets4)]
            + [str(n) for n in ipaddress.collapse_addresses(nets6)])


def client_allowed(cfg: dict, peer: dict) -> dict:
    """Co u klienta idzie przez VPN. Zwraca tryb, liste AllowedIPs i uwagi.

    AllowedIPs u klienta to tylko routing - nie uprawnienia. O tym, do czego ktos
    ma dostep, decyduja reguly na serwerze; przerobiony plik niczego nie otworzy.
    """
    mode, listing, own = tunnel_mode(peer)
    v6 = ipv6_on(cfg) and peer.get("ip6")
    notes: list[str] = []

    if mode == "full":
        allowed = ["0.0.0.0/0"] + (["::/0"] if v6 else [])
        return {"mode": mode, "own": own, "allowed": allowed, "notes": notes}

    targets: list[str] = []
    if mode == "lan":
        targets += [cfg["vpn"], cfg["lan"]]
        if v6:
            targets += [cfg["vpn6"], *_split_list(cfg.get("lan6", ""))]
    elif mode == "custom":
        for item in _split_list(listing):
            dest4, dest6, _unused = resolve_dest(item, cfg)
            if dest4 == "ANY":
                notes.append(_("{item}: ANY oznacza cały ruch - użyj trybu „Cały ruch przez VPN”",
                               item=item))
                continue
            if dest4:
                targets.append(dest4)
            if v6:
                targets += [d for d in dest6 if d != "ANY"]
    else:  # auto: cele regul ACCEPT osoby i jej tieru
        for rule in expand_peer_rules(peer, cfg):
            if rule["action"] != "ACCEPT":
                continue
            if rule["dest"] == "ANY":
                notes.append(_("reguła ACCEPT do ANY - tego nie da się zawęzić, dodano cały LAN"))
                targets.append(cfg["lan"])
                continue
            if rule["dest"]:
                targets.append(rule["dest"])
            if v6:
                targets += [d for d in rule["dest6"] if d != "ANY"]
        if _parse_ports(peer.get("host_access", "")):
            targets.append(f"{cfg['server_ip']}/32")  # SSH/RDP na samo Pi przez VPN

    if not targets:
        notes.append(_("pusta lista - dodano sam adres serwera, żeby tunel miał sens"))
        targets.append(f"{cfg['server_ip']}/32")
    return {"mode": mode, "own": own, "allowed": _collapse(targets), "notes": notes}


def render_client_conf(cfg: dict, peer: dict, server_pubkey: str) -> str:
    v6 = ipv6_on(cfg) and peer.get("ip6")
    route = client_allowed(cfg, peer)

    addresses = [f"{peer['ip']}/{_prefix_len(cfg['vpn'])}"]
    if v6:
        addresses.append(f"{peer['ip6']}/{_prefix_len(cfg['vpn6'])}")
    dns = []
    if route["mode"] == "full":
        dns = [d for d in (cfg.get("dns_full", ""), cfg.get("dns6", "") if v6 else "") if d]

    lines = [
        "[Interface]",
        f"Address    = {', '.join(addresses)}",
        f"PrivateKey = {peer.get('privkey') or '<KLUCZ_PRYWATNY_JUZ_WYDANY>'}",
    ]
    if dns:
        lines.append(f"DNS        = {', '.join(dns)}")
    if cfg.get("mtu"):
        lines.append(f"MTU        = {cfg['mtu']}")
    lines += [
        "",
        "[Peer]",
        f"PublicKey    = {server_pubkey or '<KLUCZ_PUBLICZNY_SERWERA>'}",
    ]
    if peer.get("psk"):
        lines.append(f"PresharedKey = {peer['psk']}")
    lines += [
        f"Endpoint     = {cfg['endpoint']}",
        f"AllowedIPs   = {', '.join(route['allowed'])}",
        f"PersistentKeepalive = {cfg.get('keepalive', '25')}",
        "",
    ]
    return "\n".join(lines)


def ip6_for(ip4: str, cfg: dict) -> str:
    """Adres IPv6 odpowiadajacy IPv4: 10.0.0.4 -> fd42:42:42::4. Czytelne i przewidywalne."""
    net4 = _net(cfg["vpn"])
    net6 = _net(cfg["vpn6"])
    offset = int(ipaddress.ip_address(ip4)) - int(net4.network_address)
    return str(net6.network_address + offset)


def diff(old: str, new: str) -> str:
    import difflib
    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True), new.splitlines(keepends=True),
            fromfile="obecne", tofile="nowe", n=3,
        )
    ) or "(bez zmian)"
