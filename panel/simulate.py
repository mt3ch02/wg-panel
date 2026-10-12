"""Symulator: ktora regula zadecyduje o konkretnym pakiecie.

Przechodzi wygenerowany lancuch dokladnie tak, jak zrobilby to iptables -
od gory do pierwszego dopasowania - i zwraca werdykt razem z numerem
i opisem reguly, ktora zadecydowala.
"""
from __future__ import annotations

import ipaddress
import time as _time

from . import i18n, render
from .i18n import _


def _comment(text: str) -> str:
    """Komentarz wbudowanej reguly po tlumaczeniu. Regula dociera tu juz oczyszczona dla
    iptables (np. bez '+'), wiec tekst wbudowany rozpoznajemy po oczyszczonej postaci."""
    from .db import BUILTIN_TIERS
    for tier in BUILTIN_TIERS:
        for rule in tier["rules"]:
            if render.clean_comment(rule["comment"]) == text:
                return i18n.maybe(rule["comment"])
    return i18n.maybe(text)

WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _in(address: str, network: str) -> bool:
    if network == "ANY":
        return True
    try:
        return ipaddress.ip_address(address) in ipaddress.ip_network(network, strict=False)
    except ValueError:
        return False


def _time_matches(rule: dict, when: _time.struct_time) -> bool:
    if rule["weekdays"] and WEEKDAY_NAMES[when.tm_wday] not in rule["weekdays"]:
        return False
    if not rule["time_from"]:
        return True
    current = when.tm_hour * 60 + when.tm_min
    start_h, start_m = (int(x) for x in rule["time_from"].split(":"))
    stop_h, stop_m = (int(x) for x in rule["time_to"].split(":"))
    start, stop = start_h * 60 + start_m, stop_h * 60 + stop_m
    if start <= stop:
        return start <= current <= stop
    return current >= start or current <= stop  # okno przez polnoc


def _describe(rule: dict) -> str:
    target = rule.get("label") or rule["dest"] or ",".join(rule.get("dest6", []))
    parts = [rule["action"], "->", target]
    if rule["proto"] != "any":
        parts.append(rule["proto"])
    if rule["ports"]:
        parts.append(",".join(str(p) for p in rule["ports"]))
    if rule["time_from"]:
        parts.append(f'{rule["time_from"]}-{rule["time_to"]}')
    if rule["weekdays"]:
        parts.append(",".join(rule["weekdays"]))
    if rule["comment"]:
        parts.append(f'({_comment(rule["comment"])})')
    return " ".join(parts)


def simulate(cfg: dict, peers: list[dict], src_ip: str, dest_ip: str,
             port: int | None = None, proto: str = "tcp",
             when: float | None = None) -> dict:
    """Zwraca werdykt dla pakietu src -> dest:port."""
    moment = _time.localtime(when if when is not None else _time.time())
    trace: list[dict] = []

    try:
        family = ipaddress.ip_address(dest_ip).version
    except ValueError:
        return {"verdict": "BRAK", "reason": _("{address} to nie jest adres IP", address=repr(dest_ip)),
                "trace": trace, "chain": "-"}
    if family == 6 and not render.ipv6_on(cfg):
        return {"verdict": "DROP", "reason": _("IPv6 jest wyłączone - tunel nie przenosi IPv6"),
                "trace": trace, "chain": "-"}

    source = next((p for p in peers if src_ip in (p["ip"], p.get("ip6"))), None)
    if source is None:
        return {"verdict": "BRAK", "reason": _("nie ma peera o adresie {address}", address=src_ip),
                "trace": trace, "chain": "-"}
    if source.get("state") != "active":
        return {"verdict": "DROP", "reason":
                _("peer jest zawieszony - nie ma go w konfiguracji interfejsu"),
                "trace": trace, "chain": "-"}

    if family == 6 and not source.get("ip6"):
        return {"verdict": "DROP", "reason": _("peer nie ma adresu IPv6"), "trace": trace,
                "chain": "-"}

    # ruch do samego serwera idzie przez INPUT, nie przez FORWARD
    # samo Pi - po adresie VPN albo po adresie w sieci domowej - to INPUT, nie FORWARD
    if dest_ip in (cfg.get("server_ip"), cfg.get("server_ip6"), cfg.get("server_lan")):
        allowed = render._parse_ports(source.get("host_access", ""))
        managed = allowed | render._parse_ports(cfg.get("managed_host_ports", "22,3389"))
        chain = cfg.get("chain", "WG-USERS") + "-HOST"
        if proto != "tcp":
            return {"verdict": "ACCEPT", "chain": chain,
                    "reason": _("łańcuch hosta filtruje tylko TCP"), "trace": trace}
        if port in allowed:
            return {"verdict": "ACCEPT", "chain": chain,
                    "reason": _("port {port} jest na liście dostępu do hosta dla tego peera", port=port),
                    "trace": trace}
        if port in managed:
            return {"verdict": "DROP", "chain": chain,
                    "reason": _("port {port} jest zarządzany, a ten peer nie ma do niego prawa", port=port),
                    "trace": trace}
        return {"verdict": "ACCEPT", "chain": chain,
                "reason": _("port {port} nie jest zarządzany przez panel - decyduje ufw", port=port),
                "trace": trace}

    chain = cfg.get("chain", "WG-USERS")
    position = 1
    peer_targets = {p["ip"] for p in peers if p.get("state") == "active"} | \
        {p["ip6"] for p in peers if p.get("state") == "active" and p.get("ip6")}

    trace.append({"position": position, "rule": "-o wg0 -j DROP (" + _("ruch między peerami") + ")",
                  "matched": dest_ip in peer_targets})
    if dest_ip in peer_targets:
        return {"verdict": "DROP", "chain": chain, "position": position,
                "reason": _("peery nie widzą się nawzajem"), "trace": trace}

    for peer in peers:
        if peer.get("state") != "active":
            continue
        for rule in render.expand_peer_rules(peer, cfg):
            targets = [rule["dest"]] if family == 4 else list(rule["dest6"])
            targets = [t for t in targets if t]
            if peer is not source:
                position += len(targets)
                continue
            if not targets:
                continue
            position += len(targets)
            matched = (
                any(_in(dest_ip, target) for target in targets)
                and (rule["proto"] in ("any", proto))
                and (not rule["ports"] or (port is not None and port in rule["ports"]))
                and _time_matches(rule, moment)
            )
            trace.append({"position": position, "rule": _describe(rule), "matched": matched})
            if matched:
                reason = (_("reguła własna: {rule}", rule=_describe(rule)) if rule.get("_own")
                          else _("reguła tieru: {rule}", rule=_describe(rule)))
                if rule["time_from"]:
                    reason += "  [okno czasowe pasuje do podanej chwili]"
                return {"verdict": rule["action"], "chain": chain,
                        "position": position, "reason": reason, "trace": trace}

    position += 1
    trace.append({"position": position, "rule": "-j ACCEPT (domyslnie przepusc)",
                  "matched": True})
    return {"verdict": "ACCEPT", "chain": chain, "position": position,
            "reason": _("żaden wyjątek ani blokada nie pasuje - peer ma pełny dostęp"),
            "trace": trace}
