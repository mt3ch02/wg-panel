#!/usr/bin/env python3
"""Języki panelu: kompletność tłumaczeń i brak polskiego tekstu na stronach po angielsku.

Część statyczna czyta kod: każde _("...") ma angielski odpowiednik, zmienne {x} zgadzają się
w obu językach, nikt nie przypisuje do "_" (to psuło logowanie - w funkcji z przypisaniem
"_" staje się zmienną lokalną i _("...") przestaje działać).
Część na żywo stawia panel na atrapach i ogląda strony po angielsku oraz po polsku.
"""
from __future__ import annotations

import ast
import html as htmllib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from panel import i18n  # noqa: E402

FAILURES = []
POLISH_CHARS = re.compile(r"[ąćęłńóśźżĄĆĘŁŃÓŚŹŻ]")
POLISH_WORDS = re.compile(
    r"\b(się|sie|nie|jest|albo|przez|żeby|zeby|tylko|który|która|które|ktory|ktora|może|moze|oraz|"
    r"jako|jeśli|jesli|gdy|czy|już|juz|więc|wiec|temu|nigdy|bezterminowo|"
    r"Zapisz|Usuń|Usun|Dodaj|Pokaż|Pokaz|Brak|Kopiuj|Pobierz|Wygeneruj|Oznacz|Porzuć|Porzuc|"
    r"Zamknij|Zastosuj|Cofnij|Anuluj|wyloguj|Wznów|Wznow|Zawieś|Zawies|"
    r"Ustawienia|Dziennik|Kopie|Hosty|Tiery|Peery|Nazwa|Notatka|Tryb|Urządzenie|Urzadzenie|"
    r"zmian|zmiany|zmianę|zmiane|peera|peerów|peerow|tieru|osoby|osoba|plik|pliku|klucz|klucza|"
    r"reguły|reguly|regułę|regule|dostęp|dostep|adres|stan|ważny|wazny|ruch|"
    r"bez|nieudany|udany|zgodna|kopii|kopia|szyfrowania|"
    r"\w+(?:anie|enie|ania|owac|ować|cja|cji|ości|osci))\b")
MODULES = ("panel/app.py", "panel/ui.py", "panel/db.py", "panel/model.py", "panel/render.py",
           "panel/simulate.py")


def polish_left_text(text: str) -> list:
    return POLISH_CHARS.findall(text) + POLISH_WORDS.findall(text)


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  [{'OK  ' if condition else 'BLAD'}] {label}"
          + (f"  ({detail})" if detail and not condition else ""))
    if not condition:
        FAILURES.append(label)


def literal(node) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


print("\n1. Kod: kompletność i spójność tłumaczeń")
calls, assigned = [], []
for path in MODULES + ("panel/i18n.py",):
    tree = ast.parse(open(os.path.join(ROOT, path), encoding="utf8").read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_":
            text = literal(node.args[0]) if node.args else None
            if text is not None:
                calls.append((path, node.lineno, text))
        if isinstance(node, ast.Name) and node.id == "_" and isinstance(node.ctx, ast.Store):
            assigned.append(f"{path}:{node.lineno}")
missing = sorted({text for _p, _l, text in calls if text not in i18n.EN})
check(f"każde _(\"...\") w kodzie ma angielski odpowiednik ({len(calls)} wywołań)", not missing,
      "; ".join(missing[:5]))
check("nikt nie przypisuje do _ (psułoby tłumaczenia w całej funkcji)", not assigned, ", ".join(assigned))
fields = re.compile(r"\{(\w+)\}")
mismatch = [k for k, v in i18n.EN.items() if set(fields.findall(k)) != set(fields.findall(v))]
check("zmienne {x} te same w obu językach", not mismatch, "; ".join(mismatch[:3]))
polish_in_en = [v for v in i18n.EN.values() if POLISH_CHARS.search(v)]
check("tłumaczenia bez polskich znaków", not polish_in_en, "; ".join(polish_in_en[:3]))
check("żadne tłumaczenie nie jest puste", all(v.strip() for v in i18n.EN.values()))
# teksty tłumaczone pośrednio (przez zmienną): nazwy trybów tunelu, stała komunikatu,
# domyślne opisy wbudowanych tierów (i18n.maybe)
from panel import app as panel_app, db as panel_db, render as panel_render  # noqa: E402
# komentarze regul wbudowanych tierow - tlumaczone przy wyswietlaniu (i18n.maybe)
builtin_comments = {rule["comment"] for tier in panel_db.BUILTIN_TIERS for rule in tier["rules"]
                    if rule.get("comment")}
indirect = (set(panel_render.TUNNEL_MODES.values()) | {panel_app.TUNNEL_SAVED}
            | {tier["description"] for tier in panel_db.BUILTIN_TIERS}
            | {template for _pattern, template in i18n.HELPER_PATTERNS}
            | builtin_comments | set(i18n.AUDIT_ACTIONS.values())
            | set(i18n.AUDIT_ACTORS.values()) | set(i18n.LOGGED_TEMPLATES)
            | set(i18n.ALERT_KINDS.values()))
audit_missing = [t for t in (list(i18n.AUDIT_ACTIONS.values()) + list(i18n.AUDIT_ACTORS.values())
                             + i18n.LOGGED_TEMPLATES + sorted(builtin_comments)) if t not in i18n.EN]
check("dziennik i dane wbudowane mają angielskie odpowiedniki", not audit_missing,
      "; ".join(audit_missing[:3]))
_conn = panel_db.connect(":memory:")
panel_db.init_defaults(_conn)
with _conn:
    _conn.execute("UPDATE tiers SET description = ? WHERE name = 'ADMIN'",
                  ("Pelny dostep do LAN, SSH/RDP na serwer, caly ruch przez VPN.",))
panel_db.normalize_legacy(_conn)
_admin = _conn.execute("SELECT description FROM tiers WHERE name = 'ADMIN'").fetchone()[0]
check("najstarszy opis ADMIN przechodzi migrację", _admin == panel_db.BUILTIN_TIERS[0]["description"],
      _admin)
helper_missing = [t for _pattern, t in i18n.HELPER_PATTERNS if t not in i18n.EN]
check("komunikaty helpera mają angielskie odpowiedniki", not helper_missing, "; ".join(helper_missing[:3]))
unused = sorted(set(i18n.EN) - {t for _p, _l, t in calls} - indirect)
check("słownik bez martwych wpisów (po zmianie tekstu stary wpis nie zostaje)", not unused,
      "; ".join(unused[:3]))

alert_kinds_missing = [k for k in i18n.ALERT_KINDS.values() if k not in i18n.EN]
check("rodzaje alertów mają angielskie nazwy", not alert_kinds_missing, str(alert_kinds_missing))
i18n.set_lang("en")
sample = i18n.pl(i18n.ALERTS["new_endpoint"], name="Ola", ip="10.0.0.9", new="1.2.3.4", old="5.6.7.8")
check("treść alertu w panelu po angielsku", i18n.logged(sample).startswith("Ola (10.0.0.9) is connecting"),
      i18n.logged(sample))
check("stary alert bez polskich znaków też się tłumaczy",
      i18n.logged("Babcia (10.0.0.9) nie laczyl sie od 166 h.") == "Babcia (10.0.0.9) hasn't connected for 166 h.")
from panel import simulate as _simulate, ui as _ui
check("symulator: komentarz wbudowanej reguły przetłumaczony mimo oczyszczenia dla iptables ('+')",
      _simulate._comment("dysk sieciowy  panel") == "network drive + panel",
      _simulate._comment("dysk sieciowy  panel"))
import time as _time
check("handshake sprzed doby: '1 day ago', nie '1 days ago'",
      _ui._handshake(int(_time.time()) - 90000)[0] == "1 day ago", _ui._handshake(int(_time.time()) - 90000)[0])
i18n.set_lang("pl")
check("stary alert po polsku dostaje polskie znaki",
      i18n.logged("Babcia (10.0.0.9) nie laczyl sie od 166 h.") == "Babcia (10.0.0.9) nie łączył się od 166 h.")

check("wykrywacz polskiego tekstu łapie teksty bez tłumaczenia",
      bool(polish_left_text("Kopiuj plik")) and bool(polish_left_text("Wygeneruj link jednorazowy"))
      and bool(polish_left_text("bez szyfrowania")) and bool(polish_left_text("nieudany"))
      and bool(polish_left_text("Konfiguracja klienta")) and not polish_left_text(
          "Save changes Copy file Key rotation Peers Settings Delete Alice? no expiry"))

print("\n2. Panel na żywo")
TMP = tempfile.mkdtemp(prefix="wgpanel-i18n-")
BIN, WGDIR = os.path.join(TMP, "bin"), os.path.join(TMP, "wireguard")
os.makedirs(BIN)
os.makedirs(WGDIR)


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
for tool in ("iptables-restore", "ip6tables-restore", "ip"):
    stub(tool, "exit 0\n")
stub("systemctl", 'if [ "$1" = "is-active" ]; then echo active; fi\nexit 0\n')
with open(os.path.join(WGDIR, "server_private.key"), "w", encoding="utf8") as handle:
    handle.write("KLUCZPRYWATNYSERWERA00000000000000000000000=\n")
PORT = 8941
BASE = f"http://127.0.0.1:{PORT}"
env = dict(os.environ)
env.update({"PATH": BIN + os.pathsep + env["PATH"], "WGPANEL_DB": os.path.join(TMP, "panel.db"),
            "WGPANEL_HOST": "127.0.0.1", "WGPANEL_PORT": str(PORT),
            "WGPANEL_SOCKET": os.path.join(TMP, "helper.sock"), "WGPANEL_WGDIR": WGDIR,
            "PYTHONPATH": ROOT})
COOKIES: dict[str, str] = {}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def request(path: str, data: dict | None = None):
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(BASE + path, data=body)
    if COOKIES:
        req.add_header("Cookie", "; ".join(f"{k}={v}" for k, v in COOKIES.items()))
    try:
        response = urllib.request.build_opener(NoRedirect).open(req, timeout=20)
        status, text, headers = response.status, response.read().decode("utf8"), response.headers
    except urllib.error.HTTPError as exc:
        status, text, headers = exc.code, exc.read().decode("utf8", "replace"), exc.headers
    for cookie in headers.get_all("Set-Cookie") or []:
        name, _sep, value = cookie.split(";")[0].partition("=")
        COOKIES[name] = value
    return status, text, headers.get("Location", "")


def csrf(page: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', page)
    return match.group(1) if match else ""


def visible(page: str, data_tables: bool = False) -> str:
    """Tekst, który widzi użytkownik: treść, podpowiedzi, okna potwierdzeń, teksty skryptów.
    data_tables=True pomija zawartość tabel z danymi (dziennik, alerty, kopie - tam są zapisane
    po polsku wpisy, a nie tekst interfejsu)."""
    if data_tables:
        page = re.sub(r"<tbody>.*?</tbody>", " ", page, flags=re.S)
    js = re.search(r"var WG_T = (\{.*?\});", page)
    texts = list(json.loads(js.group(1)).values()) if js else []
    for attr in ("data-confirm", "data-confirm-title", "data-confirm-ok", "data-confirm-detail",
                 "aria-label", "title", "placeholder", "alt"):
        texts += re.findall(attr + r'="([^"]*)"', page)
    body = re.sub(r"<(script|style|svg|pre)\b.*?</\1>", " ", page, flags=re.S)
    body = re.sub(r"<[^>]+>", " ", body)
    return htmllib.unescape(" ".join(texts) + " " + body)


def polish_left(page: str, data_tables: bool = False) -> list:
    text = visible(page, data_tables)
    found = POLISH_CHARS.findall(text) + POLISH_WORDS.findall(text)
    if not found:
        return []
    spots = [m.start() for m in re.finditer(POLISH_CHARS.pattern + "|" + POLISH_WORDS.pattern, text)]
    return [re.sub(r"\s+", " ", text[max(0, i - 30):i + 30]) for i in spots[:3]]


helper = subprocess.Popen([sys.executable, os.path.join(ROOT, "helper", "wg_panel_helper.py")],
                          env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
panel = subprocess.Popen([sys.executable, os.path.join(ROOT, "panel", "app.py")],
                         env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(1.8)
try:
    status, _p, location = request("/lang/en?next=/login")
    check("przełącznik: zapamiętuje język i wraca na stronę",
          status == 303 and location == "/login" and COOKIES.get("lang") == "en", f"{status} {location}")
    status, page, _l = request("/setup")
    check("zakładanie konta po angielsku", "Create account" in page and not polish_left(page),
          str(polish_left(page)))
    request("/setup", {"username": "admin", "password": "bardzo-dlugie-haslo",
                       "password2": "bardzo-dlugie-haslo"})
    status, page, _l = request("/login")
    check("logowanie po angielsku", '<html lang="en"' in page and "Sign in" in page
          and not polish_left(page), str(polish_left(page)))
    status, page, _l = request("/login", {"username": "admin", "password": "zle"})
    check("komunikat o złym logowaniu po angielsku", "Wrong sign-in details." in page, str(status))
    request("/login", {"username": "admin", "password": "bardzo-dlugie-haslo"})
    with_db = __import__("sqlite3").connect(os.path.join(TMP, "panel.db"))
    with_db.execute("INSERT OR REPLACE INTO settings(key, value) VALUES ('confirm_mode', 'manual')")
    with_db.commit()

    status, page, _l = request("/peers/new")
    check("nowy peer po angielsku (z opisami wbudowanych tierów)",
          "New peer" in page and "Full access to the LAN" in page and not polish_left(page),
          str(polish_left(page)))
    _s, _p, location = request("/peers/new", {"csrf": csrf(page), "name": "Alice", "tier_id": "1",
                                               "access_mode": "from_use", "expires_days": "7"})
    check("komunikat po dodaniu po angielsku", "Peer%20created" in location, location)
    peer = location.split("/peers/")[1].split("?")[0]
    _s, page, _l = request(f"/peers/{peer}")
    request(f"/peers/{peer}/rules", {"csrf": csrf(page), "action": "ACCEPT", "dest": "192.168.50.11",
                                     "proto": "tcp", "ports": "445", "comment": "office"})
    status, page, _l = request(f"/peers/{peer}?nowy=1")
    check("strona peera po angielsku (reguły, pytania, pasek zapisu)",
          "Custom rules" in page and "Delete Alice?" in page and "Save changes" in page
          and not polish_left(page), str(polish_left(page)))
    fields_now = {"csrf": csrf(page), "name": "Alice", "device": "", "tier_id": "1", "note": "",
                  "access_mode": "from_use", "expires_days": "7", "tunnel_mode": "", "tunnel_list": ""}
    _s, _p, location = request(f"/peers/{peer}/edit", fields_now)
    check("komunikat tworzony od razu po angielsku", "Nothing%20changed." in location, location)

    status, page, _l = request("/")
    check("lista peerów z kartą zmian po angielsku",
          "The panel has changes" in page and "Apply changes" in page and not polish_left(page),
          str(polish_left(page)))
    request("/apply", {"csrf": csrf(page)})
    status, page, _l = request(f"/peers/{peer}")
    check("pasek oczekiwania po angielsku", "Changes are waiting for confirmation." in page
          and "Confirm, it works" in page and not polish_left(page), str(polish_left(page)))
    _s, _p, location = request("/rollback", {"csrf": csrf(page), "scope": "pending", "next": "/"})
    status, page, _l = request("/")
    check("karta cofniętych zmian po angielsku", "Changes were rolled back" in page
          and not polish_left(page), str(polish_left(page)))

    print("\n3. Pozostałe strony po angielsku")
    pages = {"/tiers": ("Add tier", False), "/hosts": ("Hosts in the home network", False),
             "/alerts": ("Notification settings", True), "/backups": ("Back up now - where?", True),
             "/settings": ("Emergency operations", False), "/audit": ("Audit log", False),
             "/apply": ("Preview of changes", False), "/disk": ("Comparison with the configuration on disk", True),
             "/integrity": ("Configuration integrity", False), "/tls": ("Panel certificate", False),
             f"/simulator?src=10.0.0.2&dst=192.168.50.11&port=445&proto=tcp": ("Verdict", False)}
    request("/alerts/test", {"csrf": csrf(request("/alerts")[1])})
    request("/backups/run", {"csrf": csrf(request("/backups")[1])})
    for path, (marker, data_tables) in pages.items():
        status, page, _l = request(path)
        left = polish_left(page, data_tables)
        check(f"{path.split('?')[0]} po angielsku", status == 200 and marker in page and not left,
              f"{status} {marker in page} {left}")
    _s, page, _l = request("/tiers")
    check("wbudowane komentarze reguł i opisy po angielsku",
          "no LAN access" in page and "network drive + panel" in page
          and 'value="Full access to the LAN' in page, "")
    admin_id = re.search(r'id="tier-(\d+)"', page).group(1)
    request(f"/tiers/{admin_id}/edit", {"csrf": csrf(page), "description": "Full access to the LAN, SSH/RDP "
            "and the panel on the server, all traffic through the VPN.", "client_mode": "full",
            "client_list": "", "host_access": "22,3389,8080"})
    stored = with_db.execute("SELECT description FROM tiers WHERE id = ?", (admin_id,)).fetchone()[0]
    check("zapis angielskiego opisu wraca do bazy jako polski klucz",
          stored.startswith("Pełny dostęp do LAN"), stored)
    _s, page, _l = request("/audit")
    check("dziennik: czytelne nazwy akcji po angielsku", "peer added" in page and "sign-in" in page)
    _s, _p, location = request("/tiers/1/delete", {"csrf": csrf(page)})
    check("komunikat odmowy usunięcia tieru po angielsku", "Won%27t%20delete%20tier" in location
          or "Won't delete tier" in urllib.parse.unquote(location), location)
    check("werdykt symulatora po angielsku", "custom rule" in request(
        "/simulator?src=10.0.0.2&dst=192.168.50.11&port=445&proto=tcp")[1]
        or "tier rule" in request("/simulator?src=10.0.0.2&dst=192.168.50.11&port=445&proto=tcp")[1]
        or "full access" in request("/simulator?src=10.0.0.2&dst=192.168.50.11&port=445&proto=tcp")[1])

    request("/lang/pl?next=/")
    status, page, _l = request(f"/peers/{peer}")
    check("po polsku: polskie znaki na miejscu", '<html lang="pl"' in page and "Usuń" in page
          and "Reguły własne" in page and "Zapisz zmiany" in page)
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
