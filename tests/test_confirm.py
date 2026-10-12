#!/usr/bin/env python3
"""Potwierdzenia i zmiana czekajaca na potwierdzenie - test regresji.

Pilnuje bledow, ktore juz raz byly:
- pasek oczekiwania nie znikal po potwierdzeniu (stan z pamieci podrecznej),
- rozna liczba potwierdzen zaleznie od drogi (checkbox bezpiecznika, zero w ustawieniach),
- nieaktualne "Cofnij teraz" cofalo potwierdzona zmiane do ostatniej kopii,
- po automatycznym cofnieciu: falszywy alarm o zmianie poza panelem i bezuzyteczne
  "Odrzuc zmiany",
- niejednolite pytania (okienka przegladarki, kroki w karcie, brak pytania),
- bledy na zielonym, znikajacym pasku.

Helper i panel dzialaja naprawde, zamiast wg/iptables sa atrapy. Trwa ok. minuty,
bo automatyczne cofniecie nastepuje najwczesniej po 30 s.
"""
from __future__ import annotations

import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
import http.client
import json
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="wgpanel-confirm-")
BIN = os.path.join(TMP, "bin")
WGDIR = os.path.join(TMP, "wireguard")
os.makedirs(BIN)
os.makedirs(WGDIR)
PORT = 8937
BASE = f"http://127.0.0.1:{PORT}"
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


stub("wg", """
case "$1" in
  show)    exit 0 ;;
  pubkey)  cat >/dev/null; head -c 32 /dev/urandom | base64; exit 0 ;;
  genkey|genpsk) head -c 32 /dev/urandom | base64; exit 0 ;;
esac
exit 0
""")
stub("wg-quick", 'if [ "$1" = "strip" ]; then cat %s/wg0.conf; fi\nexit 0\n' % WGDIR)
for tool in ("iptables", "ip6tables"):
    stub(tool, 'for a in "$@"; do if [ "$a" = "-C" ]; then exit 1; fi; done\nexit 0\n')
for tool in ("iptables-restore", "ip6tables-restore", "ip"):
    stub(tool, "exit 0\n")
stub("systemctl", 'if [ "$1" = "is-active" ]; then echo active; fi\nexit 0\n')
with open(os.path.join(WGDIR, "server_private.key"), "w", encoding="utf8") as handle:
    handle.write("KLUCZPRYWATNYSERWERA00000000000000000000000=\n")

DB = os.path.join(TMP, "panel.db")
env = dict(os.environ)
env.update({
    "PATH": BIN + os.pathsep + env["PATH"], "WGPANEL_DB": DB, "WGPANEL_HOST": "127.0.0.1",
    "WGPANEL_PORT": str(PORT), "WGPANEL_SOCKET": os.path.join(TMP, "helper.sock"),
    "WGPANEL_WGDIR": WGDIR, "PYTHONPATH": ROOT,
})
COOKIE = {"value": ""}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def request(path: str, data: dict | None = None):
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(BASE + path, data=body)
    if COOKIE["value"]:
        req.add_header("Cookie", COOKIE["value"])
    try:
        response = urllib.request.build_opener(NoRedirect).open(req, timeout=20)
        status, text, headers = response.status, response.read().decode("utf8"), response.headers
    except urllib.error.HTTPError as exc:
        status, text, headers = exc.code, exc.read().decode("utf8", "replace"), exc.headers
    cookie = headers.get("Set-Cookie")
    if cookie and "wgpanel=" in cookie:
        COOKIE["value"] = cookie.split(";")[0]
    return status, text, headers.get("Location", "")


def csrf(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    return match.group(1) if match else ""


def post(path: str, data: dict | None = None, page: str = "/settings"):
    """Wysyla formularz z tokenem CSRF pobranym z podanej strony."""
    token = csrf(request(page)[1])
    return request(path, dict(data or {}, csrf=token))


def setting(key: str) -> str:
    conn = sqlite3.connect(DB)
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row[0] if row else ""


def set_setting(key: str, value: str) -> None:
    conn = sqlite3.connect(DB)
    conn.execute("INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()


def disk_conf() -> str:
    path = os.path.join(WGDIR, "wg0.conf")
    return open(path, encoding="utf8").read() if os.path.exists(path) else ""


def add_peer(name: str) -> str:
    _, _, location = post("/peers/new", {"name": name, "tier_id": "3"}, page="/peers/new")
    return location.split("/peers/")[1].split("?")[0] if "/peers/" in location else ""


def pending_now() -> bool:
    return 'id="wg-pending"' in request("/")[1]


helper = subprocess.Popen([sys.executable, os.path.join(ROOT, "helper", "wg_panel_helper.py")],
                          env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
panel = subprocess.Popen([sys.executable, os.path.join(ROOT, "panel", "app.py")],
                         env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(1.5)

try:
    request("/setup", {"username": "admin", "password": "bardzo-dlugie-haslo",
                       "password2": "bardzo-dlugie-haslo"})
    request("/login", {"username": "admin", "password": "bardzo-dlugie-haslo"})
    set_setting("drift_check", "0")     # atrapy nie udaja wg show - bylby szum

    sys.path.insert(0, ROOT)
    from panel import app as panel_app  # noqa: E402
    from panel import db as panel_db  # noqa: E402
    from panel import helperclient, model  # noqa: E402
    helperclient.SOCKET_PATH = env["WGPANEL_SOCKET"]

    print("\n1. Jeden mechanizm pytania w calym panelu")
    marek = add_peer("O'Brien \"Test\"")
    post(f"/peers/{marek}/rules", {"action": "ACCEPT", "dest": "192.168.50.11", "proto": "tcp",
                                   "ports": "445"}, page=f"/peers/{marek}")
    post("/hosts/new", {"name": "nas", "address": "192.168.50.11"}, page="/hosts")
    post("/alerts/test", page="/alerts")
    conn = sqlite3.connect(DB)
    conn.execute("INSERT INTO alerts(ts, kind, subject, message, severity, sent, seen) "
                 "VALUES (1, 'x', 'y', 'testowy', 'info', 0, 0)")
    conn.commit()
    conn.close()
    pages = {path: request(path)[1] for path in (
        "/", f"/peers/{marek}", "/tiers", "/hosts", "/alerts", "/backups", "/settings", "/apply")}
    forms = {}
    for path, html in pages.items():
        for tag in re.findall(r'<form method="post"[^>]*>', html):
            action = re.sub(r"/\d+", "/N", re.search(r'action="([^"]+)"', tag).group(1))
            # okno, ktore samo jest pytaniem (odtwarzanie kopii: haslo i konto w oknie)
            forms.setdefault(action, []).append(tag.replace('data-asks="dialog"', 'data-confirm="okno"'))
        # jeden formularz, kilka akcji: pytanie siedzi na przycisku z formaction
        for tag in re.findall(r'<button[^>]*formaction="[^"]+"[^>]*>', html):
            action = re.sub(r"/\d+", "/N", re.search(r'formaction="([^"]+)"', tag).group(1))
            forms.setdefault(action, []).append(tag)
    must_ask = ["/peers/N/delete", "/peers/N/wipe", "/peers/N/rotate", "/peers/N/rules/N/delete",
                "/tiers/N/delete", "/tiers/N/rules/N/delete", "/hosts/N/delete", "/alerts/clear",
                "/alerts/N/delete", "/backups/restore", "/backups/delete", "/rollback", "/restart"]
    never_ask = ["/peers/N/suspend", "/peers/N/edit", "/peers/N/rules", "/tiers/new",
                 "/hosts/new", "/settings", "/alerts/seen", "/logout", "/backups/run", "/apply"]
    # nieodwracalne: kazdy formularz pyta; zwykle: jest wersja bez pytania
    missing = [a for a in must_ask
               if not forms.get(a) or any("data-confirm=" not in t for t in forms[a])]
    check("kazda akcja nieodwracalna pyta oknem panelu", not missing, str(missing))
    extra = [a for a in never_ask if all("data-confirm=" in t for t in forms.get(a, ["data-confirm="]))]
    check("zwykle akcje nie pytaja", not extra, str(extra))
    smb_forms = [t for t in forms.get("/backups/run", []) if "data-confirm=" in t]
    check("kopia na NAS bez hasla kopii pyta (poszlaby jawnym tekstem)",
          len(smb_forms) == 1 and "szyfrowania" in smb_forms[0], str(smb_forms)[:120])
    check("zadnych okienek przegladarki (onsubmit)",
          not any("onsubmit=" in html for html in pages.values()))
    check("obie rotacje kluczy pytaja tak samo",
          pages[f"/peers/{marek}"].count(f'action="/peers/{marek}/rotate" class="inline" data-confirm=')
          == pages[f"/peers/{marek}"].count(f'action="/peers/{marek}/rotate"'))
    check("apostrof w nazwie nie psuje pytania",
          'data-confirm-title="Usunąć O&#x27;Brien &quot;Test&quot;?"' in pages[f"/peers/{marek}"])
    check("jedno okno potwierdzenia na strone",
          all(html.count('id="wg-confirm"') == 1 for html in pages.values()))
    check("strona podgladu nie ma checkboxa wylaczajacego bezpiecznik",
          'name="safe"' not in pages["/apply"])

    print("\n2. Zastosowanie zawsze z bezpiecznikiem")
    set_setting("confirm_seconds", "0")
    status, _, location = post("/apply", {"safe": "0"}, page="/")
    check("zastosowanie wraca na liste bez zielonego paska", status == 303 and location == "/",
          location)
    check("mimo safe=0 i zera w ustawieniach zmiana czeka na potwierdzenie",
          bool(setting("confirm_token")) and pending_now())
    remaining = int(re.search(r'data-remaining="(\d+)"', request("/")[1]).group(1))
    check("minimalny czas na potwierdzenie to 30 s", 25 <= remaining <= 30, str(remaining))
    for path in ("/tiers", f"/peers/{marek}", "/settings"):
        html = request(path)[1]
        check(f"pasek oczekiwania na {path}", 'id="wg-pending"' in html
              and f'name="next" value="{path}"' in html and 'name="scope" value="pending"' in html)
    html = request("/")[1]
    check("na liscie tylko pasek - bez drugiej karty", 'id="okno-zmian"' not in html
          and "Zmiana czeka na potwierdzenie" not in html)
    _, _, location = post("/apply", page="/")
    check("drugie zastosowanie w trakcie oczekiwania: czerwony komunikat",
          "err=" in location and "czeka" in urllib.parse.unquote(location), location)
    _, _, location = post("/discard", page="/")
    check("odrzucanie zmian zablokowane w trakcie oczekiwania", "err=" in location, location)

    print("\n3. Pasek znika od razu po potwierdzeniu")
    request("/")                                   # pamiec podreczna swiezo napelniona
    _, _, location = post("/confirm", {"next": f"/peers/{marek}"}, page="/")
    check("powrot na strone, z ktorej potwierdzono", location.startswith(f"/peers/{marek}?msg="),
          location)
    check("zaraz potem pasek nie istnieje", not pending_now())
    set_setting("confirm_seconds", "90")
    stuck = 0
    for attempt in range(5):
        add_peer(f"Petla {attempt}")
        post("/apply", page="/")
        request("/")
        request("/api/peers")                      # to, co robi lista w tle co 5 s
        post("/confirm", page="/")
        stuck += pending_now()
    check("5 potwierdzen z rzedu - pasek zawsze znika", stuck == 0, f"zostal {stuck} razy")
    _, _, location = post("/confirm", {"next": "//zly.example/x"}, page="/")
    check("next spoza panelu jest ignorowany", location.startswith("/?"), location)

    print("\n4. Nieaktualne 'Cofnij teraz' nie cofa potwierdzonej zmiany")
    add_peer("Potwierdzony")
    post("/apply", page="/")
    post("/confirm", page="/")
    before = disk_conf()
    _, _, location = post("/rollback", {"scope": "pending", "next": "/tiers"}, page="/tiers")
    check("czerwony komunikat: nic nie czeka", location.startswith("/tiers?err=")
          and "nic nie czeka" in urllib.parse.unquote(location), location)
    check("konfiguracja na dysku bez zmian", disk_conf() == before and "Potwierdzony" in before)
    check("panel nie oznaczyl niczego jako cofniete", setting("rolled_back") == "")

    print("\n5. 'Cofnij teraz' przy prawdziwym oczekiwaniu")
    add_peer("DoCofniecia")
    post("/apply", page="/")
    check("zmiana trafila na dysk", "DoCofniecia" in disk_conf())
    _, _, location = post("/rollback", {"scope": "pending", "next": "/"}, page="/")
    check("cofniete, zielony komunikat", "msg=" in location, location)
    check("dysk wrocil do poprzedniej wersji", "DoCofniecia" not in disk_conf())
    html = request("/")[1]
    check("karta 'Zmiany zostaly cofniete' z 'Zastosuj ponownie' i 'Odrzuc zmiany'",
          "Zmiany zostały cofnięte" in html and "Zastosuj ponownie" in html
          and 'action="/discard"' in html)
    post("/discard", page="/")
    names = [r[0] for r in sqlite3.connect(DB).execute("SELECT name FROM peers")]
    check("'Odrzuc zmiany' wraca do tego, co dziala", "DoCofniecia" not in names
          and "Potwierdzony" in names, str(names))
    check("panel zgodny z interfejsem", 'id="okno-zmian"' not in request("/")[1])

    print("\n6. Automatyczne cofniecie (czekam ok. 35 s)")
    set_setting("confirm_seconds", "30")
    add_peer("BezPotwierdzenia")
    post("/apply", page="/")
    token = setting("confirm_token")
    deadline = time.time() + 45
    while time.time() < deadline and "BezPotwierdzenia" in disk_conf():
        time.sleep(1)
    check("helper cofnal zmiane", "BezPotwierdzenia" not in disk_conf())
    # pliki wracaja pierwsze, potem helper przywraca reguly i dopiero konczy - czekamy na koniec,
    # zamiast zakladac, ze sekunda zawsze wystarczy (pod obciazeniem nie wystarczala)
    deadline = time.time() + 15
    while True:
        status, body, _ = request("/api/pending")
        if '"pending": false' in body or time.time() > deadline:
            break
        time.sleep(0.5)
    check("/api/pending mowi wprost o automatycznym cofnieciu",
          '"pending": false' in body and "cofnęły się automatycznie" in json.loads(body)["notice"],
          body[:200])
    check("komunikat pokazany tylko raz", json.loads(request("/api/pending")[1])["notice"] == "")
    check("panel wie, ze zmiana wrocila", setting("rolled_back") == "1"
          and setting("confirm_token") == "")
    pconn = panel_db.connect(DB)
    findings = helperclient.call("drift", {"settings": model.settings_only(pconn),
                                           "expected": model.fingerprint(pconn)})["findings"]
    check("brak falszywego alarmu o zmianie wg0.conf poza panelem",
          not [f for f in findings if f["kind"] == "conf"],
          str([f["message"] for f in findings if f["kind"] == "conf"]))
    _, _, location = post("/confirm", page="/")
    check("potwierdzenie z nieaktualnej karty: 'za późno'",
          "za późno" in urllib.parse.unquote(location).lower(), location)
    page_token = csrf(request("/settings")[1])
    set_setting("confirm_token", token)          # klik tuz po terminie, zanim pasek zapytal
    _, _, location = request("/confirm", {"csrf": page_token})
    check("potwierdzenie tuz po terminie: komunikat helpera przetlumaczony ('za późno')",
          "za późno" in urllib.parse.unquote(location).lower(), location)
    check("i porzadek w stanie", setting("confirm_token") == "")

    print("\n7a. Automatyczne potwierdzenie")
    set_setting("confirm_seconds", "90")
    set_setting("confirm_mode", "auto")

    def auto_confirm(conn: http.client.HTTPConnection | None = None):
        token = csrf(request("/settings")[1])
        body = urllib.parse.urlencode({"csrf": token, "auto": "1"})
        own = conn is None
        conn = conn or http.client.HTTPConnection("127.0.0.1", PORT, timeout=10)
        conn.request("POST", "/confirm", body, {"Cookie": COOKIE["value"],
                     "Content-Type": "application/x-www-form-urlencoded"})
        response = conn.getresponse()
        payload = json.loads(response.read().decode())
        closing = (response.getheader("Connection") or "").lower() == "close"
        if own:
            conn.close()
        return payload, closing

    add_peer("Automat")
    post("/apply", page="/")
    html = request("/")[1]
    check("pasek w trybie auto ma znacznik dla przegladarki", 'data-auto="1"' in html
          and "Sprawdzam" in html)
    check("w trybie auto pasek startuje ukryty (pokaze sie dopiero po 3 s)",
          'class="pending-bar auto-wait"' in html)
    payload, _ = auto_confirm()
    check("nowe polaczenie po zmianie potwierdza automatycznie", payload.get("ok") is True
          and setting("confirm_token") == "", str(payload))
    check("komunikat nazywa czynnosc tak jak przycisk",
          payload.get("message") == "Zastosowano zmiany.", str(payload))
    add_peer("StarePolaczenie")
    old = http.client.HTTPConnection("127.0.0.1", PORT, timeout=10)
    old.request("GET", "/api/pending", headers={"Cookie": COOKIE["value"]})
    first = old.getresponse()
    first.read()
    check("bez oczekujacej zmiany polaczenie zostaje otwarte (keep-alive)",
          (first.getheader("Connection") or "").lower() != "close")
    post("/apply", page="/")
    status_page = urllib.request.build_opener(NoRedirect).open(
        urllib.request.Request(BASE + "/api/pending", headers={"Cookie": COOKIE["value"]}))
    check("w trakcie oczekiwania kazda odpowiedz zamyka polaczenie",
          (status_page.headers.get("Connection") or "").lower() == "close")
    payload, closing = auto_confirm(old)
    check("polaczenie sprzed zmiany niczego nie dowodzi: 'sprobuj ponownie' i zamkniecie",
          payload.get("retry") is True and closing and setting("confirm_token") != "", str(payload))
    old.close()
    payload, _ = auto_confirm()
    check("ponowna proba nowym polaczeniem potwierdza", payload.get("ok") is True, str(payload))
    set_setting("confirm_mode", "manual")
    add_peer("Recznie")
    post("/apply", page="/")
    html = request("/")[1]
    check("tryb reczny: pasek bez automatu i widoczny od razu", 'data-auto="1"' not in html
          and 'class="pending-bar"' in html)
    payload, _ = auto_confirm()
    check("tryb reczny: przegladarka nie potwierdza sama", payload.get("manual") is True
          and setting("confirm_token") != "", str(payload))
    set_setting("confirm_mode", "auto")
    payload, _ = auto_confirm()
    check("zmiana zastosowana recznie zostaje reczna mimo zmiany trybu",
          payload.get("manual") is True, str(payload))
    post("/confirm", page="/")
    check("reczne potwierdzenie dziala jak dotad", setting("confirm_token") == "")
    payload, _ = auto_confirm()
    check("automat bez oczekujacej zmiany: jasna odpowiedz", payload.get("done") is True
          and payload.get("ok") is False, str(payload))

    print("\n7b. Wspolny zapis peera")
    peer_page = request(f"/peers/{marek}")[1]
    check("strona peera: jeden formularz, pola tunelu dolaczone do niego",
          'id="peer-form"' in peer_page and 'name="tunnel_mode" form="peer-form"' in peer_page
          and f'action="/peers/{marek}/tunnel"' not in peer_page)
    check("brak przyciskow 'Zapisz' w kartach - zapis z paska",
          ">Zapisz<" not in peer_page and 'form="peer-form" class="primary">Zapisz zmiany' in peer_page)
    base_form = {"name": "Stazysta", "device": "", "tier_id": "3", "note": "",
                 "access_mode": "window", "starts": time.strftime("%Y-%m-%d",
                                                                  time.localtime(time.time() + 86400)),
                 "expires": "", "tunnel_mode": "", "tunnel_list": ""}
    _, _, location = post("/peers/new", base_form, page="/peers/new")
    trainee = location.split("/peers/")[1].split("?")[0]
    check("nowy peer: podpowiedz o dodawaniu kolejnych", "nowy=1" in location
          and "zastosujesz raz" in urllib.parse.unquote(location), location)
    check("strona swiezego peera ma 'Dodaj kolejnego peera'",
          "Dodaj kolejnego peera" in request(location)[1])
    _, _, location = post(f"/peers/{trainee}/edit", base_form, page=f"/peers/{trainee}")
    check("zapis bez zmian: 'Nic się nie zmieniło'", "Nic się nie zmieniło" in
          urllib.parse.unquote(location), location)
    row = sqlite3.connect(DB).execute("SELECT state, auto_state FROM peers WHERE id = ?",
                                      (trainee,)).fetchone()
    check("zapis bez zmian nie kasuje 'czeka na start okna'", row == ("suspended", 1), str(row))
    post(f"/peers/{trainee}/edit", dict(base_form, note="od jutra"), page=f"/peers/{trainee}")
    row = sqlite3.connect(DB).execute("SELECT note, auto_state FROM peers WHERE id = ?",
                                      (trainee,)).fetchone()
    check("zapis innego pola tez go nie kasuje", row == ("od jutra", 1), str(row))
    guest_form = dict(base_form, name="Gosc", access_mode="from_use", expires_days="7", starts="")
    _, _, location = post("/peers/new", guest_form, page="/peers/new")
    guest = location.split("/peers/")[1].split("?")[0]
    first_seen = int(time.time()) - 3 * 86400
    expires = panel_app.end_of_day(first_seen + 7 * 86400)
    conn = sqlite3.connect(DB)
    conn.execute("UPDATE peers SET first_seen = ?, expires_at = ? WHERE id = ?",
                 (first_seen, expires, guest))
    conn.commit()
    conn.close()
    post(f"/peers/{guest}/edit", dict(guest_form, note="gosc"), page=f"/peers/{guest}")
    stored = sqlite3.connect(DB).execute("SELECT expires_at FROM peers WHERE id = ?",
                                         (guest,)).fetchone()[0]
    check("dostep 'N dni od pierwszego polaczenia' nie staje sie bezterminowy", stored == expires,
          str(stored))
    post(f"/peers/{guest}/edit", dict(guest_form, expires_days="10"), page=f"/peers/{guest}")
    stored = sqlite3.connect(DB).execute("SELECT expires_at FROM peers WHERE id = ?",
                                         (guest,)).fetchone()[0]
    check("zmiana liczby dni liczy od pierwszego polaczenia",
          stored == panel_app.end_of_day(first_seen + 10 * 86400), str(stored))
    _, _, location = post(f"/peers/{guest}/edit", dict(guest_form, expires_days="10", tunnel_mode="lan"),
                          page=f"/peers/{guest}")
    check("tryb tunelu zapisuje sie tym samym formularzem", sqlite3.connect(DB).execute(
        "SELECT tunnel_mode FROM peers WHERE id = ?", (guest,)).fetchone()[0] == "lan")
    check("zapis samego tunelu: komunikat o pliku klienta",
          "tylko plik klienta" in urllib.parse.unquote(location), location)
    _, _, location = post(f"/peers/{guest}/edit", dict(guest_form, name=" "), page=f"/peers/{guest}")
    check("pusta nazwa odrzucona na czerwono", "err=" in location)

    print("\n7c. Migawki stanu nie zagniezdzaja sie")
    for attempt in range(3):
        add_peer(f"Migawka {attempt}")
        post("/apply", page="/")
        post("/confirm", page="/")
    state = setting("applied_state")
    check("migawka nie zawiera poprzedniej migawki", "before_applied_state" not in state
          and "confirm_auto" not in state, f"{len(state)} znakow")
    from panel import export  # noqa: E402
    snapshot = export.build(panel_db.connect(DB))
    keys = {row["key"] for row in snapshot["settings"]}
    check("kopia zapasowa bez stanu roboczego", not keys & export.RUNTIME_SETTINGS,
          str(keys & export.RUNTIME_SETTINGS))

    print("\n7d. Oczekujaca zmiana przezywa restart helpera (czekam ok. 35 s)")

    def restart_helper():
        global helper
        helper.terminate()
        helper.wait(timeout=10)
        helper = subprocess.Popen(
            [sys.executable, os.path.join(ROOT, "helper", "wg_panel_helper.py")],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1.5)

    state_path = os.path.join(WGDIR, "wg-panel-pending.json")
    set_setting("confirm_mode", "manual")
    set_setting("confirm_seconds", "90")
    add_peer("PoRestarcie")
    post("/apply", page="/")
    token = setting("confirm_token")
    stored = open(state_path, encoding="utf8").read() if os.path.exists(state_path) else ""
    check("oczekujaca zmiana zapisana na dysku, token tylko jako skrot",
          '"pending"' in stored and token and token not in stored)
    check("plik stanu tylko dla roota", oct(os.stat(state_path).st_mode & 0o777) == "0o600")
    restart_helper()
    check("po restarcie helpera zmiana nadal czeka", 'id="wg-pending"' in request("/")[1])
    _, _, location = post("/confirm", page="/")
    check("potwierdzenie po restarcie dziala", "msg=" in location
          and setting("confirm_token") == "", location)
    check("po potwierdzeniu plik stanu znika", not os.path.exists(state_path))

    set_setting("confirm_seconds", "30")
    add_peer("RestartBezPotwierdzenia")
    post("/apply", page="/")
    restart_helper()
    check("zmiana na dysku po restarcie", "RestartBezPotwierdzenia" in disk_conf())
    deadline = time.time() + 45
    while time.time() < deadline and "RestartBezPotwierdzenia" in disk_conf():
        time.sleep(1)
    check("bez potwierdzenia po restarcie zmiana cofa sie sama",
          "RestartBezPotwierdzenia" not in disk_conf())
    time.sleep(1)
    restart_helper()      # kolejny restart, zanim panel zapytal - slad cofniecia zostaje
    _, body, _ = request("/api/pending")
    check("panel wie o cofnieciu mimo kolejnego restartu",
          "cofnęły się automatycznie" in json.loads(body)["notice"],
          body[:160])
    set_setting("confirm_mode", "auto")
    set_setting("confirm_seconds", "90")

    print("\n7. Bledy na czerwono, kod bez zdublowanych tras")
    _, _, location = post("/hosts/new", {"name": "ZLA NAZWA", "address": "1.2.3.4"}, page="/hosts")
    check("blad przekazany jako err=", location.startswith("/hosts?err="), location)
    html = request(location)[1]
    check("czerwony pasek z przyciskiem zamkniecia",
          'class="banner err"' in html and "banner-close" in html)
    keys = [(method, pattern.pattern) for method, pattern, _, _ in panel_app.ROUTES]
    check("kazda trasa zdefiniowana raz", len(keys) == len(set(keys)),
          str([k for k in keys if keys.count(k) > 1][:3]))
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
