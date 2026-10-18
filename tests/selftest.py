#!/usr/bin/env python3
"""Test dymny: przechodzi pelna sciezke uzytkownika przez HTTP."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="wgpanel-test-")
PORT = 8931
BASE = f"http://127.0.0.1:{PORT}"

env = dict(os.environ)
env.update({
    "WGPANEL_DB": os.path.join(TMP, "panel.db"),
    "WGPANEL_HOST": "127.0.0.1",
    "WGPANEL_PORT": str(PORT),
    "WGPANEL_SOCKET": os.path.join(TMP, "helper.sock"),
    "WGPANEL_WGDIR": os.path.join(TMP, "wireguard"),
    "PYTHONPATH": ROOT,
})
os.makedirs(env["WGPANEL_WGDIR"], exist_ok=True)

COOKIE = {"value": ""}
FAILURES = []


def check(label: str, condition: bool, detail: str = "") -> None:
    mark = "OK  " if condition else "BLAD"
    print(f"  [{mark}] {label}" + (f"  ({detail})" if detail and not condition else ""))
    if not condition:
        FAILURES.append(label)


def request(path: str, data: dict | None = None, follow: bool = False):
    url = BASE + path
    body = None
    if data is not None:
        body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body)
    if COOKIE["value"]:
        req.add_header("Cookie", COOKIE["value"])

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    opener = urllib.request.build_opener() if follow else \
        urllib.request.build_opener(NoRedirect)
    try:
        response = opener.open(req, timeout=15)
        status, text, headers = response.status, response.read().decode("utf8", "replace"), \
            response.headers
    except urllib.error.HTTPError as exc:
        status, text, headers = exc.code, exc.read().decode("utf8", "replace"), exc.headers
    set_cookie = headers.get("Set-Cookie")
    if set_cookie and "wgpanel=" in set_cookie:
        COOKIE["value"] = set_cookie.split(";")[0]
    return status, text, headers


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    return match.group(1) if match else ""


import urllib.parse  # noqa: E402

sys.path.insert(0, ROOT)
import testnet  # noqa: E402
testnet.seed_file(env["WGPANEL_DB"])
print("uruchamiam helpera i panel...")
helper = subprocess.Popen([sys.executable, os.path.join(ROOT, "helper", "wg_panel_helper.py")],
                          env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
panel = subprocess.Popen([sys.executable, os.path.join(ROOT, "panel", "app.py")],
                         env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
time.sleep(1.5)

try:
    print("\n1. Pierwsze uruchomienie")
    status, html, _ = request("/")
    check("bez sesji przekierowuje na /login", status == 303)
    status, html, _ = request("/login", follow=True)
    check("/login odsyla do /setup gdy brak kont", "Pierwsze uruchomienie" in html)

    status, html, _ = request("/setup", {"username": "admin", "password": "krotkie"})
    check("odrzuca krotkie haslo", status == 400 and "10 znak" in html)

    status, html, _ = request("/setup", {
        "username": "admin", "password": "bardzo-dlugie-haslo",
        "password2": "bardzo-dlugie-haslo", "totp": "1"})
    check("zaklada konto i pokazuje sekret TOTP", "Dodaj do aplikacji TOTP" in html)
    secret = re.search(r"Sekret: <code>([A-Z2-7]+)</code>", html)
    check("sekret TOTP obecny", bool(secret))

    print("\n2. Logowanie")
    status, html, _ = request("/login", {"username": "admin", "password": "bardzo-dlugie-haslo"})
    check("odrzuca logowanie bez TOTP", status == 401)

    sys.path.insert(0, ROOT)
    from panel import wgcrypto
    code = wgcrypto.totp_now(secret.group(1))
    status, html, _ = request("/login", {
        "username": "admin", "password": "bardzo-dlugie-haslo", "totp": code})
    check("loguje z poprawnym TOTP", status == 303)

    status, html, _ = request("/")
    check("dashboard dostepny", status == 200 and "Peery" in html)
    check("pokazuje brak helpera lub status", "wg-panel" in html)
    token = csrf_from(html)
    check("token CSRF w formularzu", bool(token))

    print("\n3. CSRF")
    status, html, _ = request("/peers/new", {"name": "X", "csrf": "podrobiony"})
    check("odrzuca zly token CSRF", status == 403 and "CSRF" in html)

    print("\n4. Peery")
    status, html, _ = request("/peers/new")
    suggested = re.search(r'name="ip" value="([\d.]+)"', html)
    check("proponuje wolny adres", bool(suggested), html[:200])
    check("pierwszy wolny to .2", suggested and suggested.group(1).endswith(".2"),
          suggested.group(1) if suggested else "")
    token = csrf_from(html)

    status, _, headers = request("/peers/new", {
        "csrf": token, "name": "Marek", "device": "laptop", "ip": "10.0.0.2", "tier_id": "3"})
    check("tworzy peera", status == 303)
    location = headers.get("Location", "")
    peer_id = location.split("/")[2].split("?")[0] if "/peers/" in location else ""
    check("przekierowuje na strone peera", bool(peer_id), location)

    status, html, _ = request(f"/peers/{peer_id}")
    check("strona peera dziala", status == 200 and "Marek" in html)
    check("pokazuje konfiguracje klienta", "[Interface]" in html)
    check("NAS dostaje w pliku tylko NAS",
          "AllowedIPs   = 192.168.50.11/32" in html and "0.0.0.0/0" not in html)
    check("klucz prywatny obecny przed wydaniem", "Wydano" in html)
    token = csrf_from(html)

    print("\n5. Reguly")
    status, _, _ = request(f"/peers/{peer_id}/rules", {
        "csrf": token, "action": "ACCEPT", "dest": "192.168.50.60",
        "proto": "tcp", "ports": "9091", "comment": "transmission"})
    status, html, _ = request(f"/peers/{peer_id}")
    check("dodaje regule wlasna", "192.168.50.60" in html and "9091" in html)

    status, html, _ = request(f"/peers/{peer_id}/rules", {
        "csrf": token, "action": "ACCEPT", "dest": "nie-adres", "proto": "tcp"}, follow=True)
    check("odrzuca zly adres w regule", "odrzucona" in html or "Regula+odrzucona" in html)

    status, html, _ = request(f"/peers/{peer_id}/rules", {
        "csrf": token, "action": "DROP", "dest": "LAN", "proto": "any",
        "ports": "80"}, follow=True)
    check("odrzuca porty bez protokolu", "odrzucona" in html)

    print("\n6. Generowanie konfiguracji")
    from panel import db as pdb, render
    conn = pdb.connect(env["WGPANEL_DB"])
    cfg = pdb.render_cfg(conn)
    peers = []
    for row in pdb.peers(conn):
        item = dict(row)
        item["custom_rules"] = [dict(r) for r in pdb.rules_for(conn, row["id"])]
        peers.append(item)
    rules_text = render.render_rules_file(cfg, peers)
    conf_text = render.render_wg_conf(cfg, peers, "/etc/wireguard/wg0-panel-rules.v4")

    lines = [ln for ln in rules_text.split("\n") if ln.startswith("-A")]
    check("pierwsza regula blokuje ruch miedzy peerami",
          lines[0].endswith("-j DROP") and "-o wg0" in lines[0], lines[0])
    users = [ln for ln in lines if ln.startswith("-A WG-USERS ")]
    check("ostatnia regula lancucha to domyslny ACCEPT",
          users[-1].endswith("-j ACCEPT"), users[-1])
    nas_index = next(i for i, ln in enumerate(lines) if "192.168.50.11" in ln)
    lan_index = next(i for i, ln in enumerate(lines)
                     if "192.168.50.0/24" in ln and "-j DROP" in ln)
    check("wyjatek do NAS jest przed blokada LAN", nas_index < lan_index,
          f"{nas_index} vs {lan_index}")
    custom_index = next(i for i, ln in enumerate(lines) if "192.168.50.60" in ln)
    check("regula wlasna przed regulami tieru", custom_index < nas_index,
          f"{custom_index} vs {nas_index}")
    check("wg0.conf ma podpiecie lancucha", "-j WG-USERS" in conf_text)
    check("wg0.conf ma placeholder klucza", "__SERVER_PRIVATE_KEY__" in conf_text)
    check("peer w wg0.conf", "AllowedIPs   = 10.0.0.2/32" in conf_text)

    print("\n7. Walidacja po stronie helpera")
    sys.path.insert(0, os.path.join(ROOT, "helper"))
    import wg_panel_helper as H
    hcfg = H.validate_settings({"lan": "192.168.50.0/24", "vpn": "10.0.0.0/24",
                                "server_ip": "10.0.0.1", "wan_if": "eth0",
                                "listen_port": "41194", "wg_iface": "wg0",
                                "chain": "WG-USERS", "managed_host_ports": "22,3389"})
    check("waliduje poprawne ustawienia", hcfg["chain"] == "WG-USERS")

    for label, payload in [
        ("adres spoza VPN", {"ip": "192.168.1.9", "pubkey": "A" * 43 + "=", "psk": "B" * 43 + "="}),
        ("zly klucz", {"ip": "10.0.0.5", "pubkey": "krotki", "psk": "B" * 43 + "="}),
    ]:
        try:
            H.validate_peers([payload], hcfg)
            check(f"odrzuca: {label}", False)
        except H.Rejected:
            check(f"odrzuca: {label}", True)

    try:
        H.validate_peers([
            {"ip": "10.0.0.5", "pubkey": "A" * 43 + "=", "psk": "B" * 43 + "="},
            {"ip": "10.0.0.5", "pubkey": "C" * 43 + "=", "psk": "B" * 43 + "="},
        ], hcfg)
        check("odrzuca zdublowany adres", False)
    except H.Rejected:
        check("odrzuca zdublowany adres", True)

    for label, rule in [
        ("wstrzykniecie w komentarzu",
         {"action": "ACCEPT", "dest": "ANY", "comment": "a; iptables -F; echo"}),
        ("zla akcja", {"action": "LOG", "dest": "ANY"}),
        ("zly protokol", {"action": "ACCEPT", "dest": "ANY", "proto": "icmp"}),
    ]:
        try:
            out = H.validate_rule(rule, hcfg)
            check(f"neutralizuje: {label}", ";" not in out["comment"],
                  json.dumps(out.get("comment", "")))
        except H.Rejected:
            check(f"neutralizuje: {label}", True)

    print("\n8. Helper po HTTP")
    from panel import helperclient
    helperclient.SOCKET_PATH = env["WGPANEL_SOCKET"]
    check("helper odpowiada na ping", helperclient.call("ping", timeout=3) is not None)

    model = {"settings": {k: cfg.get(k, "") for k in
                          ("lan", "vpn", "server_ip", "wan_if", "listen_port",
                           "wg_iface", "chain", "managed_host_ports")},
             "peers": [{"name": p["name"], "device": p["device"],
                        "tier_name": p["tier_name"] or "", "ip": p["ip"],
                        "pubkey": p["pubkey"], "psk": p["psk"], "state": p["state"],
                        "host_access": p["host_access"] or "",
                        "rules": render.expand_peer_rules(p, cfg)} for p in peers]}
    preview = helperclient.call("preview", model)
    check("preview zwraca wg0.conf", "[Interface]" in preview["wg_conf"])
    check("panel i helper generuja identyczne reguly",
          preview["rules"] == rules_text,
          "rozjazd miedzy panel/render.py a helperem")

    try:
        bad = json.loads(json.dumps(model))
        bad["peers"][0]["ip"] = "8.8.8.8"
        helperclient.call("preview", bad)
        check("helper odrzuca adres spoza VPN", False)
    except helperclient.HelperError:
        check("helper odrzuca adres spoza VPN", True)

    print("\n9. Cykl zycia peera")
    status, html, _ = request(f"/peers/{peer_id}")
    token = csrf_from(html)
    request(f"/peers/{peer_id}/suspend", {"csrf": token})
    row = conn.execute("SELECT state FROM peers WHERE id=?", (peer_id,)).fetchone()
    check("zawieszenie zmienia stan", row["state"] == "suspended")
    request(f"/peers/{peer_id}/suspend", {"csrf": token})
    row = conn.execute("SELECT state FROM peers WHERE id=?", (peer_id,)).fetchone()
    check("wznowienie wraca do active", row["state"] == "active")

    old_key = conn.execute("SELECT pubkey FROM peers WHERE id=?", (peer_id,)).fetchone()["pubkey"]
    request(f"/peers/{peer_id}/rotate", {"csrf": token})
    new_key = conn.execute("SELECT pubkey FROM peers WHERE id=?", (peer_id,)).fetchone()["pubkey"]
    check("rotacja zmienia klucz publiczny", old_key != new_key)

    status, text, headers = request(f"/peers/{peer_id}/config")
    check("pobieranie .conf dziala", "[Interface]" in text)
    check("naglowek pobierania", "attachment" in (headers.get("Content-Disposition") or ""))

    status, _, headers = request(f"/peers/{peer_id}/delivery", {"csrf": token})
    link = urllib.parse.unquote(headers.get("Location", "")).split("link=")[-1]
    delivery_token = link.rstrip("/").split("/")[-1]
    COOKIE_BACKUP = COOKIE["value"]
    COOKIE["value"] = ""
    status, html, _ = request(f"/d/{delivery_token}")
    check("link jednorazowy dziala bez logowania", status == 200 and "[Interface]" in html)
    status, html, _ = request(f"/d/{delivery_token}")
    check("link jednorazowy dziala tylko raz", status == 404)
    COOKIE["value"] = COOKIE_BACKUP

    request(f"/peers/{peer_id}/wipe", {"csrf": token})
    row = conn.execute("SELECT privkey FROM peers WHERE id=?", (peer_id,)).fetchone()
    check("kasowanie klucza prywatnego", row["privkey"] is None)

    status, _, _ = request(f"/peers/{peer_id}/revoke", {"csrf": token})
    check("martwa trasa odciecia usunieta z panelu", status == 404, str(status))
    with conn:      # baza ze starszej wersji: peer odciety
        conn.execute("UPDATE peers SET state='revoked', privkey=NULL WHERE id=?", (peer_id,))
    pdb.normalize_legacy(conn)
    row = conn.execute("SELECT state, privkey FROM peers WHERE id=?", (peer_id,)).fetchone()
    check("stary odciety peer staje sie zawieszonym bez klucza",
          row["state"] == "suspended" and row["privkey"] is None, str(dict(row)))

    print("\n10. Dziennik i stan")
    status, html, _ = request("/audit")
    check("dziennik zapisuje operacje", "peer.create" in html and "peer.wipe_private_key" in html)
    status, _, _ = request("/status.json")
    check("stary endpoint status.json usuniety (lista uzywa /api/peers)", status == 404, str(status))
    status, html, _ = request("/tiers")
    check("strona tierow dziala", "ADMIN" in html and "NAS" in html)
    status, html, _ = request("/settings")
    check("ustawienia dzialaja", "Podsieć LAN" in html)
    check("osobne pola: dziennik i alerty [dni], z opisem zera",
          'name="audit_days"' in html and 'name="alerts_days"' in html and "0 = bez limitu" in html)
    token = csrf_from(html)
    status, _, headers = request("/settings", {"csrf": token, "audit_days": "tydzien",
                                               "alerts_days": "30"})
    check("zla liczba dni - blad i nic nie zapisane",
          "0 = bez limitu" in urllib.parse.unquote(headers.get("Location", ""))
          and pdb.settings(conn).get("alerts_days") != "30")
    request("/settings", {"csrf": token, "audit_days": "0", "alerts_days": "30"})
    status, html, _ = request("/audit")
    check("dziennik z 0: trzymane bez limitu", "trzymane bez limitu" in html)
    status, html, _ = request("/alerts")
    check("alerty: osobny limit widoczny na stronie", "Alerty: trzymane 30 dni" in html)
    request("/settings", {"csrf": token, "audit_days": "90", "alerts_days": "90"})

    status, html, _ = request("/")
    check("dashboard sygnalizuje roznice wzgledem interfejsu",
          "nie ma na interfejsie" in html or "Podglad i zastosowanie" in html)

    print("\n11. Katalog hostow, IPv6, integralnosc")
    status, html, _ = request("/hosts")
    check("strona hostow dziala", status == 200 and "nas" in html)
    token = csrf_from(html)
    request("/hosts/new", {"csrf": token, "name": "drukarka", "address": "192.168.50.70",
                           "address6": "", "description": "biuro"})
    status, html, _ = request("/hosts")
    check("dodanie hosta", "drukarka" in html)
    status, _, headers = request("/hosts/new", {"csrf": token, "name": "LAN",
                                                "address": "1.2.3.4"})
    check("nazwa LAN zarezerwowana",
          "zajęte" in urllib.parse.unquote(headers.get("Location", "")))

    status, _, headers = request("/peers/new", {"csrf": token, "name": "Tomek",
                                                "ip": "10.0.0.5", "tier_id": "3"})
    tomek = headers.get("Location", "").split("/")[2].split("?")[0]
    request(f"/peers/{tomek}/rules", {"csrf": token, "action": "ACCEPT", "dest": "drukarka",
                                      "proto": "tcp", "ports": "631"})
    host_id = conn.execute("SELECT id FROM hosts WHERE name='drukarka'").fetchone()["id"]
    status, _, headers = request(f"/hosts/{host_id}/delete", {"csrf": token})
    check("nie da sie skasowac hosta uzywanego w regule",
          "używają" in urllib.parse.unquote(headers.get("Location", "")),
          urllib.parse.unquote(headers.get("Location", "")))
    check("host nadal w katalogu",
          conn.execute("SELECT COUNT(*) c FROM hosts WHERE name='drukarka'").fetchone()["c"] == 1)

    status, _, headers = request("/settings/ipv6", {"csrf": token, "ipv6_enabled": "1",
                                                    "vpn6": "fd42:42:42::/64",
                                                    "server_ip6": "fd42:42:42::1",
                                                    "lan6": "", "dns6": ""})
    check("IPv6 bez prefiksu LAN odrzucone",
          "furtki" in urllib.parse.unquote(headers.get("Location", ""))
          or "prefiks" in urllib.parse.unquote(headers.get("Location", "")))
    check("IPv6 zostaje wylaczone po odmowie", pdb.settings(conn)["ipv6_enabled"] == "0")

    request("/settings/ipv6", {"csrf": token, "ipv6_enabled": "1", "vpn6": "fd42:42:42::/64",
                               "server_ip6": "fd42:42:42::1", "lan6": "fd00:50::/64",
                               "dns6": "", "nat6": "1"})
    check("IPv6 wlaczone", pdb.settings(conn)["ipv6_enabled"] == "1")
    ip6 = conn.execute("SELECT ip6 FROM peers WHERE id=?", (tomek,)).fetchone()["ip6"]
    check("peer dostal adres IPv6", ip6 == "fd42:42:42::5", str(ip6))
    status, html, _ = request(f"/peers/{tomek}")
    check("strona peera pokazuje adres IPv6", "fd42:42:42::5" in html)
    check("konfiguracja klienta ma adres IPv6", "fd42:42:42::5/64" in html)

    status, html, _ = request("/integrity")
    check("strona integralnosci dziala", status == 200 and "Integralność" in html)
    status, html, _ = request("/backups")
    check("strona kopii ma kolumne stanu (sprawdzona/niesprawdzona)", "<th>Stan</th>" in html)
    status, _, headers = request("/backups/verify", {"csrf": token})
    check("weryfikacja kopii bez kopii nie wybucha", status == 303)
    status, _, headers = request("/backups/run", {"csrf": token})
    status, html, _ = request("/backups")
    backup_id = conn.execute("SELECT MAX(id) m FROM backups WHERE ok=1").fetchone()["m"]
    check("jedna lista kopii z polami wyboru",
          f'name="id" value="{backup_id}" form="backup-pick"' in html, html[html.find("backup-list"):][:300])
    status, _, headers = request("/backups/verify", {"csrf": token, "id": str(backup_id)})
    check("sprawdzenie zaznaczonej kopii",
          "Sprawdzono 1 kopii" in urllib.parse.unquote(headers.get("Location", "")),
          urllib.parse.unquote(headers.get("Location", "")))
    status, _, headers = request("/backups/scan", {"csrf": token})
    check("odswiezenie listy bez ustawionego NAS: czerwony komunikat, nie blad 500",
          status == 303 and "err=" in headers.get("Location", ""), str(status))
    status, _, headers = request("/backups/restore", {"csrf": token,
                                                      "source": f"id:{backup_id}",
                                                      "passphrase": ""})
    check("odtworzenie z historii przez panel",
          "Odtworzono" in urllib.parse.unquote(headers.get("Location", "")),
          urllib.parse.unquote(headers.get("Location", "")))

    print("\n11b. Szybkosc: kompresja, styl w pamieci przegladarki, strony list")
    import gzip as _gzip

    def raw(path: str, encoding: str = "gzip"):
        req = urllib.request.Request(BASE + path, headers={"Accept-Encoding": encoding,
                                                           "Cookie": COOKIE["value"]})
        response = urllib.request.urlopen(req, timeout=15)
        return response.headers, response.read()
    headers, blob = raw("/")
    check("strona skompresowana gzip, gdy przegladarka to przyjmuje",
          headers.get("Content-Encoding") == "gzip" and b"<!doctype html>" in _gzip.decompress(blob))
    headers, _blob = raw("/?msg=" + urllib.parse.quote("Zapisano."))
    check("bez kompresji, gdy adres niesie tekst wstawiany na strone (BREACH)",
          headers.get("Content-Encoding") is None)
    headers, _blob = raw("/", encoding="identity")
    check("bez gzip, gdy przegladarka go nie chce", headers.get("Content-Encoding") is None)
    status, html, _ = request("/")
    version = re.search(r'/static/style.css\?v=([0-9a-f]+)', html)
    headers, _blob = raw("/static/style.css?v=" + (version.group(1) if version else "x"))
    check("styl z odciskiem w adresie trzymany w przegladarce na stale",
          version and "immutable" in (headers.get("Cache-Control") or ""), headers.get("Cache-Control"))
    status, html, _ = request("/audit?page=2")
    check("dziennik po stronach", status == 200)
    status, html, _ = request("/alerts?page=abc")
    check("zly numer strony = pierwsza strona", status == 200)

    print("\n11a. Co idzie przez VPN")
    status, html, _ = request(f"/peers/{tomek}")
    check("strona osoby ma karte tunelu", "Co idzie przez VPN" in html and "Kopiuj plik" in html)
    token = csrf_from(html)
    tomek_row = conn.execute("SELECT * FROM peers WHERE id=?", (tomek,)).fetchone()
    tomek_form = {"csrf": token, "name": tomek_row["name"], "device": tomek_row["device"] or "",
                  "tier_id": str(tomek_row["tier_id"] or ""), "note": tomek_row["note"] or "",
                  "access_mode": "none"}
    request(f"/peers/{tomek}/edit", dict(tomek_form, tunnel_mode="custom",
                                         tunnel_list="nas, drukarka"))
    row = conn.execute("SELECT tunnel_mode, tunnel_list FROM peers WHERE id=?", (tomek,)).fetchone()
    check("zapis wlasnej listy", row["tunnel_mode"] == "custom" and "drukarka" in row["tunnel_list"])
    status, _, headers = request(f"/peers/{tomek}/edit", dict(tomek_form, tunnel_mode="custom",
                                                             tunnel_list="nie-ma-takiego"))
    check("zla nazwa na liscie odrzucona",
          "Lista" in urllib.parse.unquote(headers.get("Location", "")))
    status, _, headers = request(f"/peers/{tomek}/delivery", {"csrf": token})
    check("link jednorazowy dziala z przycisku", status == 303 and "link=" in headers.get("Location", ""))
    request(f"/peers/{tomek}/issued", {"csrf": token})
    check("oznaczenie pliku jako wyslanego", conn.execute(
        "SELECT issued_allowed FROM peers WHERE id=?", (tomek,)).fetchone()["issued_allowed"])

    status, html, _ = request(f"/peers/{tomek}")
    check("strona osoby ma przycisk usuwania", 'Usuń</button>' in html)
    token = csrf_from(html)
    request("/peers/new", {"csrf": token, "name": "Do skasowania", "ip": "10.0.0.66",
                           "tier_id": str(conn.execute(
                               "SELECT id FROM tiers LIMIT 1").fetchone()["id"])})
    skasuj = conn.execute("SELECT id FROM peers WHERE ip='10.0.0.66'").fetchone()["id"]
    before_del = conn.execute("SELECT COUNT(*) c FROM peers").fetchone()["c"]
    request(f"/peers/{skasuj}/delete", {"csrf": token})
    check("usuniecie peera kasuje go z bazy", conn.execute(
        "SELECT COUNT(*) c FROM peers").fetchone()["c"] == before_del - 1)
    request("/peers/new", {"csrf": token, "name": "Zawieszony", "ip": "10.0.0.67",
                           "tier_id": str(conn.execute(
                               "SELECT id FROM tiers LIMIT 1").fetchone()["id"])})
    odciety = conn.execute("SELECT id FROM peers WHERE ip='10.0.0.67'").fetchone()["id"]
    conn.execute("UPDATE peers SET state='suspended' WHERE id=?", (odciety,))
    conn.commit()
    status, html, _ = request("/")
    check("zawieszony peer widoczny na liscie", "10.0.0.67" in html and "zawieszony" in html)
    check("zawieszony peer nadal zajmuje adres", pdb.next_free_ip(conn) != "10.0.0.67")
    request(f"/peers/{odciety}/delete", {"csrf": token})
    check("po usunieciu adres wolny", "10.0.0.67" not in
          [r["ip"] for r in conn.execute("SELECT ip FROM peers")])

    check("usuniety adres wraca do puli",
          "10.0.0.66" not in [r["ip"] for r in conn.execute("SELECT ip FROM peers")])

    print("\n11b. Tiery i odswiezanie listy")
    status, html, _ = request("/tiers")
    token = csrf_from(html)
    request("/tiers/new", {"csrf": token, "name": "TEST", "description": "do skasowania",
                           "client_mode": "auto", "client_list": "", "host_access": "22",
                           "rules_json": "[]"})
    tier_id = conn.execute("SELECT id FROM tiers WHERE name='TEST'").fetchone()["id"]
    request(f"/tiers/{tier_id}/edit", {"csrf": token, "description": "zmieniony",
                                       "client_mode": "lan", "client_list": "",
                                       "host_access": "22,3389", "rules_json": "[]"})
    row = conn.execute("SELECT * FROM tiers WHERE id=?", (tier_id,)).fetchone()
    check("edycja tieru zapisuje porty i tryb",
          row["host_access"] == "22,3389" and row["client_mode"] == "lan"
          and row["description"] == "zmieniony", dict(row).__str__())

    request(f"/tiers/{tier_id}/rules", {"csrf": token, "action": "ACCEPT", "dest": "nas",
                                       "proto": "tcp", "ports": "445", "comment": "dysk"})
    rules = json.loads(conn.execute("SELECT rules_json FROM tiers WHERE id=?",
                                    (tier_id,)).fetchone()["rules_json"])
    check("regula tieru dodana z formularza",
          len(rules) == 1 and rules[0]["dest"] == "nas", str(rules))
    status, _, headers = request(f"/tiers/{tier_id}/rules", {"csrf": token, "action": "ACCEPT",
                                                             "dest": "nie-ma-takiego",
                                                             "proto": "any", "ports": "",
                                                             "comment": ""})
    check("zly cel reguly tieru odrzucony",
          "Reguła" in urllib.parse.unquote(headers.get("Location", "")))
    request(f"/tiers/{tier_id}/rules/0/delete", {"csrf": token})
    check("regula tieru usunieta", json.loads(conn.execute(
        "SELECT rules_json FROM tiers WHERE id=?", (tier_id,)).fetchone()["rules_json"]) == [])

    nas_tier = conn.execute("SELECT id, host_access FROM tiers WHERE name='NAS'").fetchone()
    request(f"/tiers/{nas_tier['id']}/edit", {"csrf": token, "description": "NAS",
                                              "client_mode": "auto", "client_list": "",
                                              "host_access": "445", "rules_json": "[]"})
    check("edycja wbudowanego tieru tez dziala", conn.execute(
        "SELECT host_access FROM tiers WHERE id=?", (nas_tier["id"],)).fetchone()["host_access"]
        == "445")

    conn.execute("UPDATE peers SET tier_id=? WHERE id=?", (tier_id, tomek))
    conn.commit()
    status, _, headers = request(f"/tiers/{tier_id}/delete", {"csrf": token})
    check("nie da sie skasowac tieru z przypisanymi osobami",
          "przypisany" in urllib.parse.unquote(headers.get("Location", "")),
          urllib.parse.unquote(headers.get("Location", "")))
    conn.execute("UPDATE peers SET tier_id=? WHERE id=?", (nas_tier["id"], tomek))
    conn.commit()
    request(f"/tiers/{tier_id}/delete", {"csrf": token})
    check("pusty tier da sie skasowac", conn.execute(
        "SELECT COUNT(*) c FROM tiers WHERE id=?", (tier_id,)).fetchone()["c"] == 0)

    nas_id = conn.execute("SELECT id FROM tiers WHERE name='NAS'").fetchone()["id"]
    conn.execute("UPDATE peers SET tier_id=NULL WHERE tier_id=?", (nas_id,))
    conn.commit()
    request(f"/tiers/{nas_id}/delete", {"csrf": token})
    check("wbudowany tier bez przypisanych osob tez da sie skasowac", conn.execute(
        "SELECT COUNT(*) c FROM tiers WHERE id=?", (nas_id,)).fetchone()["c"] == 0)

    request("/alerts/clear", {"csrf": token})
    check("kasowanie alertow", conn.execute("SELECT COUNT(*) c FROM alerts").fetchone()["c"] == 0)
    status, html_a, _ = request("/alerts")
    check("alerty maja przycisk usuwania wszystkich", "Usuń wszystkie" in html_a)
    check("formularze dodawania sa zwijane", "wgToggle" in request("/tiers")[1])

    status, body, _ = request("/api/peers")
    data = json.loads(body)
    check("endpoint odswiezania zwraca peery",
          status == 200 and len(data["peers"]) >= 1 and "now" in data, body[:200])
    status, html, _ = request("/")
    check("lista peerow odswieza sie sama", "wgPoll" in html and "setInterval" in html)

    check("lista liczy czas wedlug zegara serwera, nie komputera",
          "WG_SKEW" in html and "Math.max(0, now - seconds)" in html)
    check("karta wykresu jest na liscie zawsze", 'data-chart-src="/api/chart"' in html)
    first_peer = data["peers"][0]["id"]
    pub = conn.execute("SELECT pubkey FROM peers WHERE id = ?", (first_peer,)).fetchone()["pubkey"]
    with conn:
        conn.execute("DELETE FROM traffic")
    status, html_p, _ = request(f"/peers/{first_peer}")
    check("strona peera bez danych: nowa notka zamiast „pierwszej godziny”",
          "Wykres pojawi się, gdy zbierze pierwsze dwie." in html_p and "pierwszej godzinie" not in html_p
          and f'data-chart-src="/api/peers/{first_peer}/chart"' in html_p, html_p[-400:])
    status, body, _ = request("/api/chart")
    check("bez probek wykres jeszcze niegotowy", json.loads(body)["ready"] is False, body[:200])
    t0 = int(time.time()) - 600
    with conn:
        conn.executemany("INSERT INTO traffic(ts, pubkey, rx, tx) VALUES (?, ?, ?, ?)",
                         [(t0, pub, 1000, 2000), (t0 + 300, pub, 50000, 900000)])
    status, body, _ = request("/api/chart")
    got = json.loads(body)
    check("po dwoch probkach z ruchem /api/chart oddaje wykres", got["ready"] and "<svg" in got["html"],
          body[:200])
    status, body, _ = request(f"/api/peers/{first_peer}/chart")
    got = json.loads(body)
    check("to samo dla strony peera", got["ready"] and "<svg" in got["html"], body[:200])
    check("nieznany peer: 404", request("/api/peers/999999/chart")[0] == 404)
    with conn:
        conn.execute("DELETE FROM traffic")

    print("\n11c. Odrzucanie zmian i dziennik")
    import panel.model as pmodel
    pmodel.mark_applied(conn, pmodel.build(conn), {})
    before = conn.execute("SELECT COUNT(*) c FROM peers").fetchone()["c"]
    status, html, _ = request("/")
    token = csrf_from(html)
    tier_any = conn.execute("SELECT id FROM tiers LIMIT 1").fetchone()["id"]
    status, _, headers = request("/peers/new", {"csrf": token, "name": "Do odrzucenia",
                                                "ip": "10.0.0.77", "tier_id": str(tier_any)})
    check("nowy peer zwieksza liczbe",
          conn.execute("SELECT COUNT(*) c FROM peers").fetchone()["c"] == before + 1,
          urllib.parse.unquote(headers.get("Location", "")))
    check("panel widzi roznice", pmodel.is_dirty(conn))
    status, html, _ = request("/")
    check("okno zmian widoczne", "nie ma na interfejsie" in html)
    # atrapa helpera nie podaje odciskow plikow, wiec przycisk moze byc ukryty -
    # samo odrzucanie sprawdzamy wprost
    request("/discard", {"csrf": csrf_from(html)})
    check("odrzucenie przywraca stan sprzed zmian",
          conn.execute("SELECT COUNT(*) c FROM peers").fetchone()["c"] == before)
    check("po odrzuceniu nie ma juz roznicy", not pmodel.is_dirty(conn))

    status, html, _ = request("/audit?order=asc")
    check("dziennik sortuje po dacie", status == 200 and "order=desc" in html)

    print("\n11d. Wczytanie peerow z dysku")
    import base64 as _b64
    obcy = _b64.b64encode(b"Q" * 32).decode()
    # ktos dopisal peera prosto do pliku, z pominieciem panelu
    conf_path = os.path.join(env["WGPANEL_WGDIR"], "wg0.conf")
    with open(conf_path, "a", encoding="utf8") as handle:
        handle.write("\n[Peer]\n# Dopisany recznie\nPublicKey = " + obcy +
                     "\nAllowedIPs = 10.0.0.88/32\n")
    status, body_qr, _ = request(f"/peers/{tomek}/qr.svg")
    check("kod QR dostepny jako osobny obrazek",
          status == 200 and body_qr.startswith("<svg"), str(status))
    status, html_p, _ = request(f"/peers/{tomek}")
    check("strona osoby nie czeka na kod QR", '/qr.svg?v=' in html_p)
    old_qr = re.search(r'/qr\.svg\?v=(\w+)', html_p).group(1)
    _s, _b, _l = request(f"/peers/{tomek}/rotate", {"csrf": csrf_from(html_p)})
    status, html_p, _ = request(f"/peers/{tomek}")
    new_qr = re.search(r'/qr\.svg\?v=(\w+)', html_p)
    check("po rotacji kluczy nowy adres obrazka QR (przegladarka nie pokaze starego)",
          bool(new_qr) and new_qr.group(1) != old_qr, f"{old_qr} {new_qr and new_qr.group(1)}")

    status, diff_text, _ = request("/apply/diff")
    check("roznice da sie wczytac bez zmiany strony",
          status == 200 and "wg0.conf" in diff_text, diff_text[:120])
    status, html, _ = request("/")
    okno = html.split('id="okno-zmian"')[1].split('id="krok-szczegoly"')[0]
    check("okno zmian ma zastosowanie, odrzucenie i szczegoly",
          'action="/apply"' in okno and 'action="/discard"' in okno and "wgDiff(this)" in okno)
    check("zastosowanie jednym kliknieciem (chroni je automatyczne sprawdzenie)",
          "data-confirm" not in okno.split('action="/apply"')[1].split(">")[0])
    check("odrzucenie tez pyta przez okno panelu",
          "data-confirm=" in okno.split('action="/discard"')[1].split(">")[0])
    check("zadna akcja nie otwiera systemowego okienka", "onsubmit=" not in html)
    check("jest dokladnie jedno okno potwierdzenia", html.count('id="wg-confirm"') == 1)
    check("szczegoly wczytuja roznice bez zmiany strony", "wgLoadDiff" in html)
    check("etykiety przyciskow sie nie zmieniaja",
          "textContent =" not in html.split("function wgDiff")[1].split("function ")[0])
    check("stary mechanizm krokow usuniety", "step-box" not in html and "wgStep" not in html)

    # telefon: przegladarki tu nie ma - pilnujemy tego, na czym opiera sie uklad
    check("telefon: przycisk menu sterujacy nawigacja",
          'class="menu-toggle" aria-expanded="false" aria-controls="main-nav"' in html
          and '<nav id="main-nav"' in html)
    check("telefon: biezaca strona oznaczona w menu", '<a href="/" aria-current="page">Peery' in html)
    check("telefon: lista peerow jako karty z podpisami",
          'class="peers-list"' in html and 'class="c-ip" data-label="IP"' in html
          and 'data-label="Handshake"' in html)
    check("telefon: odswiezanie w tle zachowuje uklad karty", '" c-hs"' in html)
    css = open(os.path.join(ROOT, "panel", "static", "style.css"), encoding="utf8").read()
    check("telefon: siatka strony peera nie rozpycha sie (minmax 0)",
          ".peer-layout { grid-template-columns: minmax(0, 1fr); }" in css)
    check("telefon: pola 16 px na ekranach dotykowych (iOS nie przybliza)",
          "(pointer: coarse)" in css and "font-size: 16px" in css.split("(pointer: coarse) {")[1])
    check("telefon: tabele jako listy, sortowanie dziennika zostaje",
          ".js table.stack td[data-label]::before" in css and "th:has(a)" in css)
    status, audit_html, _ = request("/audit")
    check("telefon: dziennik oznaczony jako lista", 'class="stack"' in audit_html)

    # pelna sciezka apply -> confirm dziala tylko przy prawdziwym wg-quick,
    # wiec tutaj sprawdzamy sama trase; reszte pokrywa test_apply.py na atrapach
    status, _, headers = request("/apply", {"csrf": token, "safe": "1"})
    check("zastosowanie z okna zmian odpowiada przekierowaniem", status == 303)

    status, html, _ = request("/disk")
    check("strona porownania pokazuje peera tylko z dysku",
          "tylko na dysku" in html and "10.0.0.88" in html)
    check("strona porownania pokazuje peery znane tylko panelowi", "tylko w panelu" in html)
    token = csrf_from(html)
    request("/disk/pull", {"csrf": token, "pubkey": obcy})
    row = conn.execute("SELECT name, tier_id FROM peers WHERE ip='10.0.0.88'").fetchone()
    check("przejecie peera z dysku", row is not None and row["name"] == "Dopisany recznie",
          str(dict(row) if row else None))
    before_again = conn.execute("SELECT COUNT(*) c FROM peers").fetchone()["c"]
    request("/disk/pull", {"csrf": token, "pubkey": obcy})
    check("powtorne przejecie niczego nie dubluje",
          conn.execute("SELECT COUNT(*) c FROM peers").fetchone()["c"] == before_again)
    status, html, _ = request("/disk")
    check("przejety peer jest juz zgodny", "10.0.0.88" in html and "tylko na dysku" not in html)
    print("\n12. Konto: kod z aplikacji i zmiana hasla")
    COOKIE_SAVE = COOKIE["value"]
    COOKIE["value"] = ""
    status, html, _ = request("/login")
    check("z wlaczonym TOTP logowanie pokazuje pole na kod", 'name="totp"' in html)
    COOKIE["value"] = COOKIE_SAVE

    status, html, _ = request("/settings")
    token = csrf_from(html)
    check("ustawienia pokazuja stan kodu", "włączony" in html)
    status, _, headers = request("/account/totp", {"csrf": token, "enable": "0",
                                                   "password": "zle-haslo-123"})
    check("wylaczenie TOTP wymaga obecnego hasla",
          "Złe obecne" in urllib.parse.unquote(headers.get("Location", "")))
    request("/account/totp", {"csrf": token, "enable": "0", "password": "bardzo-dlugie-haslo"})
    check("TOTP wylaczony w bazie", conn.execute(
        "SELECT totp_secret FROM users WHERE username='admin'").fetchone()["totp_secret"] is None)

    COOKIE["value"] = ""
    status, html, _ = request("/login")
    check("bez TOTP logowanie nie pokazuje pola na kod", 'name="totp"' not in html)
    status, _, headers = request("/login", {"username": "admin",
                                            "password": "bardzo-dlugie-haslo"})
    check("logowanie samym haslem dziala", status == 303 and COOKIE["value"] != "")

    status, html, _ = request("/settings")
    token = csrf_from(html)
    status, _, headers = request("/account/password", {
        "csrf": token, "password": "bardzo-dlugie-haslo",
        "new1": "nowe-dlugie-haslo", "new2": "nowe-dlugie-haslo"})
    check("zmiana hasla wylogowuje", "/login" in headers.get("Location", ""))
    COOKIE["value"] = ""
    status, _, _ = request("/login", {"username": "admin", "password": "bardzo-dlugie-haslo"})
    check("stare haslo juz nie dziala", status == 401)
    status, _, _ = request("/login", {"username": "admin", "password": "nowe-dlugie-haslo"})
    check("nowe haslo dziala", status == 303)

finally:
    for process in (panel, helper):
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()

print("\n" + "=" * 60)
if FAILURES:
    print(f"NIEPOWODZENIA ({len(FAILURES)}): " + ", ".join(FAILURES))
    print("\n--- stderr panelu ---")
    print(panel.stderr.read().decode("utf8", "replace")[-3000:])
    print("\n--- stderr helpera ---")
    print(helper.stderr.read().decode("utf8", "replace")[-2000:])
    sys.exit(1)
print("Wszystkie testy przeszly.")
