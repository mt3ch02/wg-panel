"""Model wysylany do helpera. Jedno miejsce, z ktorego korzystaja panel i wgctl."""
from __future__ import annotations

import hashlib
import json
import sqlite3

from . import db, render
from .i18n import _

SETTINGS_FOR_HELPER = (
    "lan", "vpn", "server_ip", "wan_if", "listen_port", "wg_iface", "chain",
    "managed_host_ports", "ipv6_enabled", "vpn6", "server_ip6", "lan6", "nat6",
)


def peers_for_render(conn: sqlite3.Connection) -> list[dict]:
    """Peery z bazy razem z ich regulami wlasnymi - wejscie dla generatora."""
    panel_port = (db.settings(conn).get("panel_port") or "").strip()
    out = []
    for peer in db.peers(conn):
        row = dict(peer)
        row["custom_rules"] = [dict(r) for r in db.rules_for(conn, peer["id"])]
        row["host_access"] = host_ports(row, panel_port)
        out.append(row)
    return out


def build(conn: sqlite3.Connection) -> dict:
    cfg = db.render_cfg(conn)
    v6 = render.ipv6_on(cfg)
    peers_out = []
    for row in peers_for_render(conn):
        peers_out.append({
            "name": row["name"],
            "device": row["device"],
            "tier_name": row["tier_name"] or "",
            "ip": row["ip"],
            "ip6": (row.get("ip6") or "") if v6 else "",
            "pubkey": row["pubkey"],
            "psk": row["psk"],
            "state": row["state"],
            "host_access": row["host_access"],
            "rules": render.expand_peer_rules(row, cfg),
        })
    settings = {key: cfg.get(key, "") for key in SETTINGS_FOR_HELPER}
    return {"settings": settings, "peers": peers_out}


def host_ports(peer, panel_port: str = "") -> str:
    """Porty na samym Pi - bierzemy je wprost z tieru (tam jest tez port panelu)."""
    return ",".join(p.strip() for p in str(peer.get("host_access") or "").split(",") if p.strip())


def settings_only(conn: sqlite3.Connection) -> dict:
    cfg = db.settings(conn)
    return {key: cfg.get(key, "") for key in SETTINGS_FOR_HELPER}


def model_hash(model: dict) -> str:
    return hashlib.sha256(
        json.dumps(model, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def render_hash(conn: sqlite3.Connection) -> str:
    """Odcisk tego, co faktycznie trafia na dysk: wg0.conf i pliki regul.

    Porownywanie calego modelu dawalo falszywe alarmy - zmiana notatki albo
    przywrocenie peera, ktory i tak byl na interfejsie, wygladaly jak zmiana
    konfiguracji, choc generowane pliki byly identyczne.
    """
    cfg = db.render_cfg(conn)
    peers = peers_for_render(conn)
    parts = [render.render_wg_conf(cfg, peers, "/etc/wireguard/rules.v4"),
             render.render_rules_file(cfg, peers, 4)]
    if render.ipv6_on(cfg):
        parts.append(render.render_rules_file(cfg, peers, 6))
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


def rendered_files(conn: sqlite3.Connection, rules_path: str = "") -> dict:
    """To, co panel zapisalby teraz na dysk - z zaslonietym kluczem serwera."""
    import hashlib as _h
    cfg = db.render_cfg(conn)
    peers = peers_for_render(conn)
    rules_path = rules_path or f"/etc/wireguard/{cfg.get('wg_iface', 'wg0')}-panel-rules.v4"
    conf = render.render_wg_conf(cfg, peers, rules_path)
    files = {
        "conf": _h.sha256(conf.encode()).hexdigest(),
        "rules4": _h.sha256(render.render_rules_file(cfg, peers, 4).encode()).hexdigest(),
        "rules6": (_h.sha256(render.render_rules_file(cfg, peers, 6).encode()).hexdigest()
                   if render.ipv6_on(cfg) else ""),
    }
    return files


def diff_summary(conn: sqlite3.Connection, status: dict | None = None) -> list[str]:
    """Co dokladnie rozni sie miedzy panelem a dyskiem - do pokazania czlowiekowi."""
    on_disk = (status or {}).get("files")
    if not on_disk:
        return []
    mine = rendered_files(conn, on_disk.get("rules_path", ""))
    names = {"conf": _("plik wg0.conf (peery i podpięcia)"),
             "rules4": _("reguły IPv4"), "rules6": _("reguły IPv6")}
    return [names[key] for key in ("conf", "rules4", "rules6")
            if mine.get(key, "") != on_disk.get(key, "")]


def discard_helps(conn: sqlite3.Connection, status: dict | None = None) -> bool:
    """Czy odrzucenie Twoich zmian zlikwiduje roznice.

    Odtwarzamy zapamietany stan w bazie tymczasowej i sprawdzamy, czy wygenerowalby
    dokladnie to, co lezy na dysku. Jesli nie - roznica nie pochodzi od Twoich zmian,
    tylko stad, ze pliki zapisala starsza wersja panelu. Wtedy pomaga tylko zastosowanie.
    """
    on_disk = (status or {}).get("files")
    raw = db.settings(conn).get("applied_state", "")
    if not on_disk or not raw:
        return False
    from . import export
    try:
        probe = db.connect(":memory:")
        db.init_defaults(probe)
        for key in ("lan", "vpn", "server_ip", "wan_if", "listen_port", "wg_iface", "chain",
                    "managed_host_ports", "panel_port", "ipv6_enabled", "vpn6", "server_ip6",
                    "lan6", "nat6"):
            db.set_setting(probe, key, db.settings(conn).get(key, ""))
        export.restore(probe, json.loads(raw), with_users=False)
        mine = rendered_files(probe, on_disk.get("rules_path", ""))
    except Exception:  # noqa: BLE001
        return False
    return mine == {k: on_disk.get(k, "") for k in ("conf", "rules4", "rules6")}


def is_dirty(conn: sqlite3.Connection, model: dict | None = None,
             status: dict | None = None) -> bool:
    """Czy to, co w panelu, rozni sie od tego, co lezy na dysku.

    Gdy helper odpowiada, porownujemy odciski plikow - to stan faktyczny.
    Bez helpera zostaje zapamietany odcisk z ostatniego zastosowania.
    """
    on_disk = (status or {}).get("files")
    if on_disk:
        mine = rendered_files(conn, on_disk.get("rules_path", ""))
        return mine != {k: on_disk.get(k, "") for k in ("conf", "rules4", "rules6")}
    return render_hash(conn) != db.settings(conn).get("applied_hash", "")


def mark_applied(conn: sqlite3.Connection, model: dict, result: dict) -> None:
    """Zapamietuje, co zostalo zastosowane: odcisk plus pelny stan panelu.

    Stan pozwala pozniej odrzucic zmiany, ktorych nie chcesz wprowadzac - panel wraca
    do tego, co faktycznie dziala na interfejsie.
    """
    from . import export
    db.set_setting(conn, "applied_hash", render_hash(conn))
    snapshot = {key: value for key, value in export.build(conn).items() if key != "users"}
    db.set_setting(conn, "applied_state", json.dumps(snapshot, ensure_ascii=False))
    if result.get("fingerprint"):
        db.set_setting(conn, "applied_fingerprint", json.dumps(result["fingerprint"]))


def store_fingerprint(conn: sqlite3.Connection, result: dict) -> None:
    if result.get("fingerprint"):
        db.set_setting(conn, "applied_fingerprint", json.dumps(result["fingerprint"]))


def fingerprint(conn: sqlite3.Connection) -> dict:
    raw = db.settings(conn).get("applied_fingerprint", "")
    try:
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {}


def assign_ip6(conn: sqlite3.Connection) -> int:
    """Nadaje adresy IPv6 peerom, ktore ich nie maja. Zwraca liczbe przydzielonych."""
    cfg = db.settings(conn)
    taken = {row["ip6"] for row in conn.execute("SELECT ip6 FROM peers WHERE ip6 IS NOT NULL")}
    taken.add(cfg.get("server_ip6", ""))
    assigned = 0
    for row in list(conn.execute("SELECT id, ip FROM peers WHERE ip6 IS NULL OR ip6 = ''")):
        candidate = render.ip6_for(row["ip"], cfg)
        if candidate in taken:
            continue
        with conn:
            conn.execute("UPDATE peers SET ip6 = ? WHERE id = ?", (candidate, row["id"]))
        taken.add(candidate)
        assigned += 1
    return assigned


def client_peer(conn: sqlite3.Connection, peer) -> dict:
    """Peer z regulami wlasnymi - tego potrzebuje tryb 'tylko to, do czego ma dostep'."""
    row = dict(peer)
    row["custom_rules"] = [dict(r) for r in db.rules_for(conn, row["id"])]
    return row


def client_conf(conn: sqlite3.Connection, peer) -> tuple[str, dict]:
    cfg = db.render_cfg(conn)
    row = client_peer(conn, peer)
    return (render.render_client_conf(cfg, row, cfg.get("server_pubkey", "")),
            render.client_allowed(cfg, row))


def mark_issued(conn: sqlite3.Connection, peer_id: int, allowed: list[str]) -> None:
    """Zapamietuje, jaki zakres dostal klient - zeby wiedziec, kiedy jego plik sie zestarzal."""
    with conn:
        conn.execute("UPDATE peers SET issued_allowed = ? WHERE id = ?",
                     (", ".join(allowed), peer_id))


def issued_state(peer, route: dict) -> dict:
    """Czy plik, ktory ma osoba, obejmuje to, co powinien.

    missing - brakuje czegos, do czego osoba ma teraz dostep: trzeba wyslac nowy plik
    wider   - plik kieruje do VPN wiecej niz trzeba: dziala, mozna wyslac nowy
    unknown - panel nie wydawal tej osobie pliku (np. przejeta z v2)
    """
    import ipaddress
    if not peer["issued_allowed"]:
        return {"state": "unknown"}
    issued = [ipaddress.ip_network(i.strip(), strict=False)
              for i in peer["issued_allowed"].split(",") if i.strip()]
    current = [ipaddress.ip_network(i, strict=False) for i in route["allowed"]]
    missing = [str(net) for net in current
               if not any(net.version == old.version and net.subnet_of(old) for old in issued)]
    if missing:
        return {"state": "missing", "missing": missing, "issued": peer["issued_allowed"]}
    if sorted(str(n) for n in issued) != sorted(route["allowed"]):
        return {"state": "wider", "issued": peer["issued_allowed"]}
    return {"state": "ok"}


def can_discard(conn: sqlite3.Connection) -> bool:
    return bool(db.settings(conn).get("applied_state"))


def discard_changes(conn: sqlite3.Connection, actor: str) -> dict:
    """Wraca do stanu z ostatniego zastosowania - bez ruszania kont panelu."""
    from . import export
    raw = db.settings(conn).get("applied_state", "")
    if not raw:
        raise ValueError("panel nie zna jeszcze stanu z ostatniego zastosowania - "
                         "zastosuj zmiany raz, wtedy bedzie do czego wracac")
    data = json.loads(raw)
    summary = export.restore(conn, data, with_users=False)
    db.set_setting(conn, "applied_state", raw)      # restore kasuje ustawienia pomocnicze
    db.set_setting(conn, "applied_hash", render_hash(conn))
    db.log(conn, actor, "changes.discard", str(summary))
    return summary
