#!/usr/bin/env python3
"""Jednorazowy import istniejacego wg0.conf do bazy panelu.

Czyta peery z pliku i zaklada je w bazie. Kluczy prywatnych klientow nie ma
w wg0.conf i nie da sie ich odtworzyc - zaimportowani peerowie beda dzialac
dalej bez zmian, ale zeby wydac komus nowy plik konfiguracyjny, trzeba zrobic
rotacje kluczy w panelu.

Uzycie:
    sudo python3 -m panel.import_conf /etc/wireguard/wg0.conf [--tier NAS]
"""
from __future__ import annotations

import argparse
import ipaddress
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from panel import db  # noqa: E402

DB_PATH = os.environ.get("WGPANEL_DB", "/var/lib/wg-panel/panel.db")


def parse(path: str) -> tuple[dict, list[dict]]:
    handle = sys.stdin if path == "-" else open(path, encoding="utf8")
    with handle:
        return parse_stream(handle)


def parse_text(text: str) -> tuple[dict, list[dict]]:
    """Ta sama logika na tekscie - panel czyta wg0.conf przez helpera."""
    import io
    return parse_stream(io.StringIO(text))


def parse_stream(handle) -> tuple[dict, list[dict]]:
    interface: dict[str, str] = {}
    peers: list[dict] = []
    current: dict | None = None
    section = ""
    comment = ""

    for raw in handle:
        line = raw.strip()
        if line.startswith("#"):
            text = line.lstrip("# ").strip()
            if current is not None and not current.get("_inline"):
                # komentarz POD naglowkiem [Peer] opisuje tego peera i ma
                # pierwszenstwo przed komentarzem sprzed naglowka (np. "--- UZYTKOWNICY ---")
                current["comment"] = text
                current["_inline"] = True
            else:
                comment = text
            continue
        if line.lower() == "[interface]":
            section, current = "interface", None
            continue
        if line.lower() == "[peer]":
            section = "peer"
            current = {"comment": comment, "_inline": False}
            peers.append(current)
            comment = ""
            continue
        if "=" not in line or not line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if section == "interface":
            interface[key] = value
        elif current is not None:
            current[key] = value
    return interface, peers


def main() -> int:
    parser = argparse.ArgumentParser(description="Import wg0.conf do panelu")
    parser.add_argument("config", help="sciezka do wg0.conf albo - (standardowe wejscie)")
    parser.add_argument("--tier", default="", help="tier przypisany zaimportowanym peerom")
    parser.add_argument("--dry-run", action="store_true", help="tylko pokaz, nic nie zapisuj")
    args = parser.parse_args()

    interface, peers = parse(args.config)
    if not peers:
        print("Nie znalazlem zadnej sekcji [Peer].")
        return 1

    conn = db.connect(DB_PATH)
    db.init_defaults(conn)

    if interface.get("ListenPort"):
        db.set_setting(conn, "listen_port", interface["ListenPort"])
    if interface.get("Address"):
        address = interface["Address"].split(",")[0].strip()
        server_ip = address.split("/")[0]
        db.set_setting(conn, "server_ip", server_ip)
        db.set_setting(conn, "vpn", str(ipaddress.ip_network(address, strict=False)))

    tier_id = None
    if args.tier:
        row = conn.execute("SELECT id FROM tiers WHERE name = ?", (args.tier,)).fetchone()
        if not row:
            print(f"Nie ma tieru {args.tier!r}. Dostepne: "
                  + ", ".join(t["name"] for t in db.tiers(conn)))
            return 1
        tier_id = row["id"]

    added = skipped = 0
    for index, peer in enumerate(peers, start=1):
        pubkey = peer.get("PublicKey", "")
        allowed = peer.get("AllowedIPs", "").split(",")[0].strip()
        if not pubkey or not allowed:
            print(f"  pomijam peera #{index}: brak PublicKey albo AllowedIPs")
            skipped += 1
            continue
        ip = allowed.split("/")[0]
        name = peer.get("comment") or f"import-{index}"
        exists = conn.execute(
            "SELECT id FROM peers WHERE pubkey = ? OR ip = ?", (pubkey, ip)
        ).fetchone()
        if exists:
            print(f"  pomijam {name}: juz jest w bazie")
            skipped += 1
            continue
        print(f"  + {name}  {ip}")
        if not args.dry_run:
            with conn:
                conn.execute(
                    """INSERT INTO peers(name, device, ip, pubkey, psk, privkey, tier_id,
                                         state, note, created_at)
                       VALUES (?, '', ?, ?, ?, NULL, ?, 'active', ?, ?)""",
                    (name[:60], ip, pubkey, peer.get("PresharedKey", ""), tier_id,
                     "zaimportowany z wg0.conf", int(time.time())),
                )
        added += 1

    if args.dry_run:
        print(f"\n[dry-run] do dodania: {added}, pominietych: {skipped}")
        return 0

    db.log(conn, "import", "config.import", f"{args.config}: +{added}")
    print(f"\nDodano {added}, pominieto {skipped}.")
    print("Sprawdz tiery i reguly w panelu, potem zrob apply.")
    without_psk = sum(1 for peer in peers if not peer.get("PresharedKey"))
    if without_psk:
        print(f"Uwaga: {without_psk} peerow nie ma PresharedKey - to w porzadku, panel tez "
              f"go nie dopisze i beda dzialac bez zmian.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
