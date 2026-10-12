#!/usr/bin/env python3
"""HTTPS panelu: wlasny urzad certyfikacji z ograniczeniem nazw, jeden port dla HTTPS i HTTP.

Helper i panel dzialaja naprawde (zamiast wg/iptables sa atrapy), polaczenia ida tak jak
z telefonu: klient ufa wylacznie urzedowi panelu. Wymaga programu openssl.
"""
from __future__ import annotations

import http.client
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if not shutil.which("openssl"):
    print("Pomijam: brak programu openssl.")
    sys.exit(0)

TMP = tempfile.mkdtemp(prefix="wgpanel-tls-")
BIN, WGDIR, TLSDIR = (os.path.join(TMP, d) for d in ("bin", "wireguard", "tls"))
os.makedirs(BIN)
os.makedirs(WGDIR)
PORT = 8939
FAILURES = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  [{'OK  ' if condition else 'BLAD'}] {label}"
          + (f"  ({detail})" if detail and not condition else ""))
    if not condition:
        FAILURES.append(label)


def stub(name: str, script: str) -> None:
    path = os.path.join(BIN, name)
    with open(path, "w", encoding="utf8") as handle:
        handle.write("#!/bin/bash\n" + script)
    os.chmod(path, 0o755)


stub("wg", 'case "$1" in show) exit 0;; pubkey) cat >/dev/null; head -c 32 /dev/urandom | base64;; '
           'genkey|genpsk) head -c 32 /dev/urandom | base64;; esac\nexit 0\n')
stub("wg-quick", 'if [ "$1" = "strip" ]; then cat %s/wg0.conf; fi\nexit 0\n' % WGDIR)
for tool in ("iptables", "ip6tables"):
    stub(tool, 'for a in "$@"; do if [ "$a" = "-C" ]; then exit 1; fi; done\nexit 0\n')
for tool in ("iptables-restore", "ip6tables-restore"):
    stub(tool, "exit 0\n")
stub("systemctl", 'if [ "$1" = "is-active" ]; then echo active; fi\nexit 0\n')
# "ip addr" udaje Pi z adresem w sieci domowej - ma trafic do certyfikatu
stub("ip", 'if [ "$1" = "-o" ]; then echo "2: eth0    inet 192.168.50.10/24 brd 192.168.50.255 '
           'scope global eth0"; fi\nexit 0\n')
with open(os.path.join(WGDIR, "server_private.key"), "w", encoding="utf8") as handle:
    handle.write("KLUCZPRYWATNYSERWERA00000000000000000000000=\n")

DB = os.path.join(TMP, "panel.db")
env = dict(os.environ)
env.update({
    "PATH": BIN + os.pathsep + env["PATH"], "WGPANEL_DB": DB, "WGPANEL_HOST": "0.0.0.0",
    "WGPANEL_PORT": str(PORT), "WGPANEL_SOCKET": os.path.join(TMP, "helper.sock"),
    "WGPANEL_WGDIR": WGDIR, "WGPANEL_TLS": "1", "WGPANEL_TLS_DIR": TLSDIR, "PYTHONPATH": ROOT,
    # w testach "dozwolony katalog" na wlasne certyfikaty to katalog tymczasowy
    "WGPANEL_CERT_DIRS": os.path.join(TMP, "certs") + "/",
})
CA_FILE = os.path.join(WGDIR, "wg-panel-ca", "ca.crt")
COOKIE = {"value": ""}


def tls_context() -> ssl.SSLContext:
    """Tak jak telefon po zainstalowaniu urzedu: ufa tylko jemu."""
    ctx = ssl.create_default_context(cafile=CA_FILE)
    ctx.check_hostname = False          # laczymy sie przez 127.0.0.1 - lancuch sprawdzamy w pelni
    return ctx


def https(method: str, path: str, data: dict | None = None, conn=None):
    own = conn is None
    conn = conn or http.client.HTTPSConnection("127.0.0.1", PORT, context=tls_context(), timeout=15)
    headers = {"Cookie": COOKIE["value"]} if COOKIE["value"] else {}
    body = None
    if data is not None:
        body = urllib.parse.urlencode(data)
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    conn.request(method, path, body, headers)
    response = conn.getresponse()
    payload = response.read()
    cookie = response.getheader("Set-Cookie") or ""
    if "wgpanel=" in cookie:
        COOKIE["value"] = cookie.split(";")[0]
    result = (response.status, payload, response.getheader("Location") or "", cookie,
              response.getheader("Content-Type") or "", conn.sock.getpeercert() if conn.sock else None)
    if own:
        conn.close()
    return result


def plain(path: str, host: str = "127.0.0.1"):
    conn = http.client.HTTPConnection(host, PORT, timeout=15)
    conn.request("GET", path, headers={"Host": f"{host}:{PORT}"})
    response = conn.getresponse()
    response.read()
    conn.close()
    return response.status, response.getheader("Location") or ""


def csrf(html: bytes) -> str:
    match = re.search(rb'name="csrf" value="([^"]+)"', html)
    return match.group(1).decode() if match else ""


def openssl(*args: str) -> str:
    return subprocess.run(["openssl", *args], capture_output=True, text=True).stdout


def served_serial() -> str:
    raw = socket.create_connection(("127.0.0.1", PORT), timeout=10)
    with tls_context().wrap_socket(raw) as sock:
        return sock.getpeercert()["serialNumber"]


sys.path.insert(0, ROOT)
import testnet  # noqa: E402
testnet.seed_file(env["WGPANEL_DB"])
helper = subprocess.Popen([sys.executable, os.path.join(ROOT, "helper", "wg_panel_helper.py")],
                          env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(1.2)
panel = subprocess.Popen([sys.executable, os.path.join(ROOT, "panel", "app.py")],
                         env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(3)

try:
    print("\n1. Urzad certyfikacji i certyfikat panelu")
    ca_key = os.path.join(WGDIR, "wg-panel-ca", "ca.key")
    check("urzad powstal, klucz tylko dla roota", os.path.exists(ca_key)
          and oct(os.stat(ca_key).st_mode & 0o777) == "0o600")
    check("klucz urzedu nie trafia do katalogu panelu", not os.path.exists(os.path.join(TLSDIR, "ca.key")))
    check("klucz panelu 640, certyfikaty 644",
          oct(os.stat(os.path.join(TLSDIR, "panel.key")).st_mode & 0o777) == "0o640"
          and oct(os.stat(os.path.join(TLSDIR, "panel.crt")).st_mode & 0o777) == "0o644")
    ca_text = openssl("x509", "-in", CA_FILE, "-noout", "-text")
    check("urzad ma krytyczne ograniczenie nazw",
          "Name Constraints: critical" in ca_text and "IP:192.168.50.0/255.255.255.0" in ca_text
          and "IP:10.0.0.0/255.255.255.0" in ca_text)
    crt = os.path.join(TLSDIR, "panel.crt")
    crt_text = openssl("x509", "-in", crt, "-noout", "-text")
    check("certyfikat panelu: tylko uwierzytelnianie serwera", "TLS Web Server Authentication" in crt_text
          and "CA:FALSE" in crt_text)
    check("adres w VPN i adres Pi w sieci domowej w certyfikacie",
          "IP Address:10.0.0.1" in crt_text and "IP Address:192.168.50.10" in crt_text, crt_text[-400:])
    end = openssl("x509", "-in", crt, "-noout", "-enddate").strip().split("=", 1)[1]
    days = (ssl.cert_time_to_seconds(end) - time.time()) / 86400
    check("waznosc najwyzej 398 dni (wymog iOS i przegladarek)", 390 < days <= 398, f"{days:.0f}")

    print("\n2. Wykradziony klucz urzedu nie podrobi innych stron")
    for target in ("DNS:bank.pl", "IP:8.8.8.8", "IP:192.168.50.20"):
        rogue = os.path.join(TMP, "rogue")
        with open(rogue + ".cnf", "w", encoding="ascii") as handle:
            handle.write(f"basicConstraints=CA:FALSE\nextendedKeyUsage=serverAuth\nsubjectAltName={target}\n")
        subprocess.run(["openssl", "req", "-new", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
                        "-nodes", "-keyout", rogue + ".key", "-out", rogue + ".csr", "-subj", "/CN=x"],
                       capture_output=True)
        subprocess.run(["openssl", "x509", "-req", "-in", rogue + ".csr", "-CA", CA_FILE, "-CAkey", ca_key,
                        "-set_serial", "9", "-days", "30", "-extfile", rogue + ".cnf", "-out", rogue + ".crt"],
                       capture_output=True)
        verified = subprocess.run(["openssl", "verify", "-CAfile", CA_FILE, rogue + ".crt"],
                                  capture_output=True, text=True).returncode == 0
        allowed = target.startswith("IP:192.168.50.")
        check(f"{target}: {'dozwolony (siec domowa)' if allowed else 'odrzucony'}", verified == allowed)

    print("\n3. Jeden port: HTTPS, przekierowanie z HTTP, tunel SSH")
    status, body, _, _, _, cert = https("GET", "/login")
    check("HTTPS z zaufaniem tylko do urzedu panelu", status in (200, 303) and cert is not None)
    check("klient widzi adres z certyfikatu",
          ("IP Address", "10.0.0.1") in (cert or {}).get("subjectAltName", ()))
    status, _ = plain("/login")
    check("127.0.0.1 po HTTP dziala (tunel SSH - ruch nie opuszcza Pi)", status in (200, 303), str(status))
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:                       # adres nie-lokalny z tablicy tras (bez wysylania czegokolwiek)
        probe.connect(("10.255.255.255", 1))
        lan_ip = probe.getsockname()[0]
    except OSError:
        lan_ip = ""
    finally:
        probe.close()
    lan_ip = "" if lan_ip.startswith("127.") else lan_ip
    if lan_ip:
        status, location = plain("/peers/3?x=1", host=lan_ip)
        check("zwykle HTTP z sieci -> przekierowanie na HTTPS",
              status == 301 and location == f"https://{lan_ip}:{PORT}/peers/3?x=1", f"{status} {location}")
    else:
        print("  [--  ] brak adresu sieciowego w tym srodowisku - przekierowanie niesprawdzone")
    garbage = socket.create_connection(("127.0.0.1", PORT), timeout=5)
    garbage.sendall(b"\x16\x03\x01\x00\x05smiec")
    garbage.close()
    check("zepsuty uscisk TLS nie kladzie panelu", https("GET", "/login")[0] in (200, 303))

    print("\n4. Logowanie, strona certyfikatu, odnowienie")
    https("POST", "/setup", {"username": "admin", "password": "bardzo-dlugie-haslo",
                             "password2": "bardzo-dlugie-haslo"})
    COOKIE["value"] = ""
    _, _, _, cookie, _, _ = https("POST", "/login", {"username": "admin", "password": "bardzo-dlugie-haslo"})
    check("ciasteczko sesji z flaga Secure po HTTPS", "Secure" in cookie and "HttpOnly" in cookie, cookie)
    status, body, _, _, _, _ = https("GET", "/login")
    check("strona logowania podpowiada instalacje certyfikatu", b'href="/tls"' in body)
    status, body, _, _, _, _ = https("GET", "/tls")
    printed = openssl("x509", "-in", CA_FILE, "-noout", "-fingerprint", "-sha256").strip().split("=", 1)[1]
    check("strona /tls pokazuje odcisk zgodny z openssl", status == 200 and printed.encode() in body)
    saved = COOKIE["value"]
    COOKIE["value"] = ""
    status, body, _, _, ctype, _ = https("GET", "/tls/ca.crt")
    COOKIE["value"] = saved
    check("certyfikat do pobrania bez logowania, typ dla telefonow",
          status == 200 and ctype == "application/x-x509-ca-cert"
          and body == open(CA_FILE, "rb").read(), ctype)
    status, body, _, _, _, _ = https("GET", "/settings")
    check("ustawienia: karta HTTPS z data waznosci", b"<h2>HTTPS</h2>" in body and "ważny do".encode() in body)
    before = served_serial()
    https("POST", "/tls/renew", {"csrf": csrf(body)})
    after = served_serial()
    check("odnowienie: nowy certyfikat bez restartu panelu", before != after, f"{before} -> {after}")
    _, body, _, _, _, _ = https("GET", "/peers/new")
    _, _, location, _, _, _ = https("POST", "/peers/new", {"csrf": csrf(body), "name": "Ola", "tier_id": "3"})
    peer = location.split("/peers/")[1].split("?")[0]
    _, body, _, _, _, _ = https("GET", f"/peers/{peer}")
    _, _, location, _, _, _ = https("POST", f"/peers/{peer}/delivery", {"csrf": csrf(body)})
    check("link jednorazowy po https://", "https%3A%2F%2F" in location, location)

    print("\n5. Automatyczne potwierdzanie po HTTPS")
    _, body, _, _, _, _ = https("GET", "/")
    https("POST", "/apply", {"csrf": csrf(body)})
    _, body, _, _, _, _ = https("GET", "/settings")
    _, payload, _, _, _, _ = https("POST", "/confirm", {"csrf": csrf(body), "auto": "1"})
    result = json.loads(payload)
    check("potwierdzenie nowym polaczeniem TLS", result.get("ok") is True, str(result))

    print("\n6. wgctl tls")
    out = subprocess.run([sys.executable, os.path.join(ROOT, "wgctl"), "tls"], env=env,
                         capture_output=True, text=True, timeout=60)
    check("wgctl tls wypisuje ten sam odcisk", printed in out.stdout, out.stdout + out.stderr)

    print("\n7. Wlasny certyfikat z plikow (np. Let's Encrypt)")
    certs = os.path.join(TMP, "certs")
    os.makedirs(certs)

    def make_cert(name: str) -> tuple:
        crt, key = os.path.join(certs, name + ".pem"), os.path.join(certs, name + ".key")
        subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt",
                        "ec_paramgen_curve:prime256v1", "-nodes", "-keyout", key, "-out", crt,
                        "-days", "90", "-subj", "/CN=vpn.example.com",
                        "-addext", "subjectAltName=DNS:vpn.example.com"], capture_output=True)
        return crt, key

    def served_der() -> bytes:
        ctx = ssl.create_default_context()
        ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
        with ctx.wrap_socket(socket.create_connection(("127.0.0.1", PORT), timeout=10)) as sock:
            return sock.getpeercert(binary_form=True)

    def file_der(path: str) -> bytes:
        return ssl.PEM_cert_to_DER_cert(open(path, encoding="ascii").read())

    def unverified(method: str, path: str, data: dict | None = None):
        ctx = ssl.create_default_context()
        ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
        conn_u = http.client.HTTPSConnection("127.0.0.1", PORT, context=ctx, timeout=15)
        return https(method, path, data, conn=conn_u)

    crt1, key1 = make_cert("a")
    crt2, key2 = make_cert("b")
    _s, body, _l, _c, _t, _p = https("GET", "/settings")
    token = csrf(body)
    _s, _b, location, _c, _t, _p = https("POST", "/tls/settings", {
        "csrf": token, "tls_mode": "custom", "tls_cert_path": crt1, "tls_key_path": key2, "panel_url": ""})
    check("klucz od innego certyfikatu odrzucony", "pasuje" in urllib.parse.unquote(location), location)
    _s, _b, location, _c, _t, _p = https("POST", "/tls/settings", {
        "csrf": token, "tls_mode": "custom", "tls_cert_path": "/etc/hostname",
        "tls_key_path": "/etc/hostname", "panel_url": ""})
    check("plik spoza dozwolonych katalogow odrzucony (helper to root)",
          "leżeć" in urllib.parse.unquote(location), location)
    _s, _b, location, _c, _t, _p = https("POST", "/tls/settings", {
        "csrf": token, "tls_mode": "custom", "tls_cert_path": crt1, "tls_key_path": key1,
        "panel_url": "https://vpn.example.com"})
    check("wlasny certyfikat przyjety", "msg=" in location, location)
    check("panel serwuje wlasny certyfikat", served_der() == file_der(crt1))
    status, body, _l, _c, _t, _p = unverified("GET", "/tls")
    check("strona /tls: nic nie trzeba instalowac", "nic nie trzeba instalować".encode() in body)
    os.replace(crt2, crt1)                # "certbot odnowil" - te same sciezki, nowa tresc
    os.replace(key2, key1)
    unverified("POST", "/tls/renew", {"csrf": token})
    check("odnowiony certyfikat trafia do panelu bez restartu", served_der() == file_der(crt1))

    print("\n8. Reverse proxy na innym serwerze (np. Caddy na osobnym komputerze)")

    def from_host(host: str, method: str, path: str, data: dict | None = None,
                  extra: dict | None = None):
        """Polaczenie HTTPS z danego adresu - jak Caddy z osobnego komputera (ufa urzedowi panelu)."""
        conn_h = http.client.HTTPSConnection(host, PORT, context=tls_context(), timeout=15)
        headers = dict(extra or {})
        if COOKIE["value"]:
            headers["Cookie"] = COOKIE["value"]
        body_h = urllib.parse.urlencode(data) if data is not None else None
        if body_h:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        conn_h.request(method, path, body_h, headers)
        response = conn_h.getresponse()
        payload = response.read()
        cookie_h = response.getheader("Set-Cookie") or ""
        if "wgpanel=" in cookie_h:
            COOKIE["value"] = cookie_h.split(";")[0]
        return response.status, payload, response.getheader("Location") or "", cookie_h

    audit_db = __import__("sqlite3").connect(env["WGPANEL_DB"])

    def last_login() -> str:
        return audit_db.execute("SELECT detail FROM audit WHERE action = 'login.ok' "
                                "ORDER BY id DESC LIMIT 1").fetchone()[0]

    _s, body, _l, _c, _t, _p = unverified("GET", "/settings")
    _s, _b, location, _c, _t, _p = unverified("POST", "/tls/settings", {
        "csrf": csrf(body), "tls_mode": "self", "proxy_trusted": "0.0.0.0/0", "panel_url": ""})
    check("zaufanie do calego internetu odrzucone", "każdego" in urllib.parse.unquote(location), location)
    _s, _b, location, _c, _t, _p = unverified("POST", "/tls/settings", {
        "csrf": csrf(body), "tls_mode": "self", "proxy_trusted": "", "panel_url": ""})
    status, _b, _l, _c, _t, cert = https("GET", "/login")
    check("powrot do wlasnego urzedu: certyfikat panelu, bez restartu", cert is not None)
    forwarded = {"X-Forwarded-For": "6.6.6.6, 5.6.7.8", "X-Forwarded-Proto": "https"}
    login = {"username": "admin", "password": "bardzo-dlugie-haslo"}
    if lan_ip:
        COOKIE["value"] = ""
        from_host(lan_ip, "POST", "/login", login, forwarded)
        check("zanim adres proxy jest wpisany - naglowki ignorowane", last_login() == lan_ip,
              last_login())
        _s, body, _l, _c = from_host(lan_ip, "GET", "/settings")
        from_host(lan_ip, "POST", "/tls/settings", {"csrf": csrf(body), "tls_mode": "self",
                                                   "proxy_trusted": lan_ip, "panel_url": ""})
        COOKIE["value"] = ""
        _s, _b, _l, cookie = from_host(lan_ip, "POST", "/login", login, forwarded)
        check("z zaufanego proxy: adres klienta = ostatni z X-Forwarded-For (dopisany przez proxy)",
              last_login() == "5.6.7.8", last_login())
        check("przez proxy ciasteczko ma flage Secure", "Secure" in cookie, cookie)
        COOKIE["value"] = ""
        https("POST", "/login", login)
        check("z adresu spoza listy (tu 127.0.0.1) naglowki nadal ignorowane",
              last_login() == "127.0.0.1", last_login())
        status, location = plain("/login", host=lan_ip)
        check("proxy po zwyklym HTTP dostaje przekierowanie na HTTPS (odcinek do Pi szyfrowany)",
              status == 301 and location.startswith("https://"), f"{status} {location}")
        _s, body, _l, _c, _t, _p = https("GET", "/settings")
        check("w ustawieniach gotowy fragment dla Caddy z adresem Pi",
              b"reverse_proxy https://" in body and b"tls_trusted_ca_certs" in body)
    else:
        print("  [--  ] brak adresu sieciowego w tym srodowisku - proxy z innego adresu niesprawdzone")
finally:
    panel.terminate()
    helper.terminate()

print("\n" + "=" * 60)
if FAILURES:
    print(f"NIEUDANE ({len(FAILURES)}):")
    for item in FAILURES:
        print("  -", item)
    sys.exit(1)
print("Wszystkie testy przeszly.")
