#!/usr/bin/env python3
"""wg-panel - proces webowy. Dziala BEZ roota; wszystko, co wymaga uprawnien,
idzie przez gniazdo do helpera.
"""
from __future__ import annotations

import gzip
import hashlib
import ipaddress
import http.cookies
import json
import os
import re
import secrets
import socket
import sqlite3
import ssl
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from panel import i18n  # noqa: E402
from panel.i18n import _  # noqa: E402
from panel import (auth, db, dual, export, helperclient, import_conf, model,  # noqa: E402
                   names, notify, qr, stats,
                   render, scheduler, simulate, traffic, ui, wgcrypto)

DB_PATH = os.environ.get("WGPANEL_DB", "/var/lib/wg-panel/panel.db")
BIND_HOST = os.environ.get("WGPANEL_HOST", "10.0.0.1")
BIND_PORT = int(os.environ.get("WGPANEL_PORT", "8080"))
# HTTPS wlacza instalator (WGPANEL_TLS=1); certyfikat wystawia i odnawia helper
TLS_ON = os.environ.get("WGPANEL_TLS", "0") == "1"
TLS_DIR = os.environ.get("WGPANEL_TLS_DIR", "/var/lib/wg-panel/tls")
TLS = {"ctx": None, "mtime": 0.0, "info": {}, "error": "", "proxies": [], "mode": "self"}
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

CONN: sqlite3.Connection = None  # ustawiane w main()
ROUTES: list[tuple[str, re.Pattern, str, bool]] = []


def route(method: str, pattern: str, login_required: bool = True):
    def decorator(func):
        ROUTES.append((method, re.compile(f"^{pattern}$"), func.__name__, login_required))
        globals()[func.__name__] = func
        return func
    return decorator


class Request:
    def __init__(self, handler: BaseHTTPRequestHandler, method: str, path: str, query: dict,
                 form: dict, sess):
        self.handler = handler
        self.method = method
        self.path = path
        self.query = query
        self.form = form
        self.sess = sess

    def get(self, key: str, default: str = "") -> str:
        if key in self.form:
            values = self.form[key]
            return values[0] if isinstance(values, list) else values
        return self.query.get(key, default)

    def get_all(self, key: str) -> list[str]:
        values = self.form.get(key, [])
        return values if isinstance(values, list) else [values]

    @property
    def actor(self) -> str:
        return self.sess["username"] if self.sess else "anonim"


class Response:
    def __init__(self, body: str | bytes = "", status: int = 200,
                 content_type: str = "text/html; charset=utf-8",
                 headers: list[tuple[str, str]] | None = None):
        self.body = body.encode("utf8") if isinstance(body, str) else body
        self.status = status
        self.content_type = content_type
        self.headers = headers or []


def redirect(location: str, headers=None) -> Response:
    return Response("", 303, headers=[("Location", location)] + (headers or []))


def redirect_err(location: str, text: str) -> Response:
    """Niepowodzenie: czerwony pasek, ktory nie znika sam. Zielony jest tylko dla sukcesu."""
    path, _unused, fragment = location.partition("#")
    sep = "&" if "?" in path else "?"
    return redirect(f"{path}{sep}err={urllib.parse.quote(text)}"
                    + (f"#{fragment}" if fragment else ""))


def with_msg(location: str, text: str) -> str:
    sep = "&" if "?" in location else "?"
    return f"{location}{sep}msg={urllib.parse.quote(text)}"


def safe_next(value: str, default: str = "/") -> str:
    """Powrot na strone, z ktorej kliknieto - wylacznie sciezki wewnatrz panelu."""
    value = (value or "").strip()
    if (not value.startswith("/") or value.startswith("//") or "\\" in value
            or "\r" in value or "\n" in value):
        return default
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme or parsed.netloc:
        return default
    query = [(k, v) for k, v in urllib.parse.parse_qsl(parsed.query) if k not in ("msg", "err")]
    return parsed.path + (("?" + urllib.parse.urlencode(query)) if query else "")


# --------------------------------------------------------------------------
# model wysylany do helpera
# --------------------------------------------------------------------------

def build_model(conn) -> dict:
    return model.build(conn)


def model_hash(value: dict) -> str:
    return model.model_hash(value)


def is_dirty(conn, value: dict, status: dict | None = None) -> bool:
    return model.is_dirty(conn, value, status)


def peers_for_render(conn) -> list[dict]:
    return model.peers_for_render(conn)


def drift_findings() -> list[dict]:
    import json as _json
    raw = db.settings(CONN).get("drift_findings", "")
    try:
        return _json.loads(raw) if raw else []
    except _json.JSONDecodeError:
        return []


def alert_count(conn) -> int:
    try:
        return db.unseen_alerts(conn)
    except sqlite3.Error:
        return 0


def end_of_day(moment: float) -> int:
    """Zaokraglenie w gore do konca dnia - dostep konczy sie o polnocy, nie w polowie dnia."""
    local = time.localtime(moment)
    return int(time.mktime((local.tm_year, local.tm_mon, local.tm_mday,
                            23, 59, 59, 0, 0, -1)))


def parse_date(value: str, end_of_day: bool = True) -> int | None:
    """YYYY-MM-DD -> poczatek albo koniec tego dnia. Puste = brak."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        parsed = time.strptime(value, "%Y-%m-%d")
    except ValueError:
        return None
    clock = (23, 59, 59) if end_of_day else (0, 0, 0)
    return int(time.mktime((parsed.tm_year, parsed.tm_mon, parsed.tm_mday,
                            *clock, 0, 0, -1)))


def access_fields(req: "Request") -> dict:
    """Pola okna dostepu z formularza. Tryb wybiera uzytkownik, nie heurystyka."""
    mode = req.get("access_mode", "none")
    if mode not in ("none", "window", "from_use"):
        mode = "none"

    fields = {"access_mode": mode, "starts_at": None, "expires_at": None,
              "expires_days": None, "expires_from_use": 0}

    if mode == "window":
        fields["starts_at"] = parse_date(req.get("starts"), end_of_day=False)
        fields["expires_at"] = parse_date(req.get("expires"), end_of_day=True)
        if fields["starts_at"] and fields["expires_at"] \
                and fields["starts_at"] > fields["expires_at"]:
            fields["starts_at"], fields["expires_at"] = \
                fields["expires_at"], fields["starts_at"]
        if not fields["starts_at"] and not fields["expires_at"]:
            fields["access_mode"] = "none"
    elif mode == "from_use":
        raw = (req.get("expires_days") or "").strip()
        days = int(raw) if raw.isdigit() and int(raw) > 0 else 0
        if days:
            fields["expires_days"] = min(days, 3650)
            fields["expires_from_use"] = 1
        else:
            fields["access_mode"] = "none"
    return fields


def access_values(fields: dict) -> tuple:
    return (fields["access_mode"], fields["starts_at"], fields["expires_at"],
            fields["expires_days"], fields["expires_from_use"])


_STATUS_CACHE: dict = {"at": 0.0, "value": ({}, "")}
STATUS_TTL = 2.0      # sekundy - tyle status jest wazny


def safe_status(conn, max_age: float = STATUS_TTL) -> tuple[dict, str]:
    """Stan z helpera, z krotka pamiecia podreczna.

    Kazde pytanie to uruchomienie `wg show`, `systemctl` i policzenie sum plikow.
    Strona i odswiezanie w tle pytaja czesto, wiec bez tego panel robil sie ospaly.
    """
    now = time.time()
    if now - _STATUS_CACHE["at"] < max_age:
        return _STATUS_CACHE["value"]
    try:
        value = helperclient.call("status", {"settings": model.settings_only(conn)}), ""
    except helperclient.HelperError as exc:
        value = {}, str(exc)
    _STATUS_CACHE.update(at=now, value=value)
    return value


def status_changed() -> None:
    """Po zmianie konfiguracji nie czekamy na wygasniecie pamieci podrecznej."""
    _STATUS_CACHE["at"] = 0.0


# --------------------------------------------------------------------------
# zmiana czekajaca na potwierdzenie (commit-confirm)
# --------------------------------------------------------------------------

_PENDING_LOCK = threading.Lock()
NO_PENDING = {"pending": None, "notice": "", "notice_kind": ""}


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()[:16] if token else ""


def remember_before_apply(conn) -> None:
    """Przed zastosowaniem odklada na bok stan, do ktorego wracamy przy cofnieciu.

    Bez tego po cofnieciu "Odrzuc zmiany" przywracaloby wlasnie wycofana wersje.
    """
    cfg = db.settings(conn)
    if cfg.get("confirm_token"):
        return      # poprzednia zmiana wciaz czeka - jej stan sprzed jest juz odlozony
    for key in ("applied_state", "applied_hash", "applied_fingerprint"):
        db.set_setting(conn, "before_" + key, cfg.get(key, ""))


def forget_before_apply(conn) -> None:
    for key in ("applied_state", "applied_hash", "applied_fingerprint"):
        db.set_setting(conn, "before_" + key, "")
    db.set_setting(conn, "confirm_auto", "")
    db.set_setting(conn, "confirm_since", "")


def restore_before_apply(conn, fingerprint: dict | None = None) -> None:
    """Zmiana zostala cofnieta: panel znowu zna to, co naprawde dziala na interfejsie."""
    cfg = db.settings(conn)
    if cfg.get("before_applied_state"):
        db.set_setting(conn, "applied_state", cfg["before_applied_state"])
    db.set_setting(conn, "applied_hash", cfg.get("before_applied_hash", ""))
    if fingerprint:
        model.store_fingerprint(conn, {"fingerprint": fingerprint})
    elif cfg.get("before_applied_fingerprint"):
        db.set_setting(conn, "applied_fingerprint", cfg["before_applied_fingerprint"])
    forget_before_apply(conn)
    db.set_setting(conn, "confirm_token", "")
    db.set_setting(conn, "rolled_back", "1")
    db.set_setting(conn, "drift_findings", "")
    status_changed()


def pending_state(conn) -> dict:
    """Czy jakas zmiana czeka na potwierdzenie - dla paska na kazdej stronie.

    Bez zastosowanej zmiany (pusty token) helpera nie pytamy wcale, wiec zwykle strony
    nic nie kosztuja. Gdy zmiana przestala czekac bez udzialu panelu (cofnela sie sama
    albo helper zostal zrestartowany), porzadkujemy stan i raz zwracamy komunikat.
    """
    with _PENDING_LOCK:
        cfg = db.settings(conn)
        token = cfg.get("confirm_token", "")
        if not token:
            return dict(NO_PENDING)
        status, error = safe_status(conn)
        if error or not status:
            return {"pending": {"remaining": None}, "notice": "", "notice_kind": ""}
        live = status.get("pending")
        if live and live.get("token", token_digest(token)) == token_digest(token):
            return {"pending": {"remaining": int(live.get("remaining") or 0),
                                "auto": cfg.get("confirm_auto") == token_digest(token)},
                    "notice": "", "notice_kind": ""}
        auto = status.get("auto_rollback") or {}
        if auto.get("token") == token_digest(token):
            if not auto.get("done"):
                return {"pending": {"remaining": 0}, "notice": "", "notice_kind": ""}
            restore_before_apply(conn, auto.get("fingerprint"))
            db.log(conn, "helper", "apply.auto_rollback", "brak potwierdzenia na czas")
            return {"pending": None, "notice_kind": "err", "notice": _(
                "Zmiany cofnęły się automatycznie - nie było potwierdzenia na czas. "
                "Na interfejsie działa poprzednia konfiguracja, a Twoje zmiany czekają "
                "w panelu jako niezastosowane.")}
        # helper nie zna tej zmiany: zostal uruchomiony ponownie w trakcie oczekiwania
        db.set_setting(conn, "confirm_token", "")
        forget_before_apply(conn)
        db.log(conn, "panel", "apply.orphaned", "helper nie zna oczekujacej zmiany")
        # helper pamieta oczekujaca zmiane takze po restarcie - tu trafiamy tylko, gdy jego
        # plik stanu zniknal albo byl uszkodzony
        return {"pending": None, "notice_kind": "err", "notice": _(
            "Helper nie ma już informacji o tej zmianie (np. uszkodzony plik stanu), więc "
            "nie wiadomo, czy została cofnięta. Sprawdź, czy wszystko działa - w razie "
            "potrzeby użyj Ustawienia → Cofnij do ostatniej kopii.")}


def request_pending() -> dict:
    """Ten sam wynik dla calego zapytania - komunikat pokazujemy tylko raz."""
    cached = getattr(ui.REQUEST, "pending_state", None)
    if cached is None:
        cached = pending_state(CONN)
        ui.REQUEST.pending_state = cached
    return cached


# --------------------------------------------------------------------------
# uwierzytelnianie
# --------------------------------------------------------------------------

@route("GET", "/login", login_required=False)
def get_login(req: Request) -> Response:
    zwolnione = db.shrink_runtime_settings(CONN)
    if zwolnione:
        sys.stderr.write(f"wg-panel: usunieto rozdmuchana migawke stanu ({zwolnione // 1024} KB) "
                         f"- powstanie na nowo przy najblizszym zastosowaniu\n")
    if not auth.has_users(CONN):
        return redirect("/setup")
    return Response(ui.login(req.query.get("error", ""), totp=auth.any_totp(CONN)))


@route("POST", "/login", login_required=False)
def post_login(req: Request) -> Response:
    peer_ip = client_ip(req.handler)
    if auth.throttled(peer_ip):
        return Response(ui.login(_("Za dużo prób. Odczekaj 5 minut."),
                                 totp=auth.any_totp(CONN)), 429)
    user = auth.authenticate(
        CONN, req.get("username"), req.get("password"), req.get("totp")
    )
    if not user:
        auth.record_failure(peer_ip)
        db.log(CONN, req.get("username") or "?", "login.failed", peer_ip)
        return Response(ui.login(_("Złe dane logowania."), totp=auth.any_totp(CONN)), 401)
    auth.clear_failures(peer_ip)
    token, _unused = auth.start_session(CONN, user["id"])
    db.log(CONN, user["username"], "login.ok", peer_ip)
    cookie = (f"{auth.COOKIE}={token}; HttpOnly; SameSite=Strict; Path=/; "
              f"Max-Age={auth.SESSION_TTL}" + ("; Secure" if req.handler.is_tls else ""))
    return redirect("/", [("Set-Cookie", cookie)])


@route("POST", "/logout")
def post_logout(req: Request) -> Response:
    auth.end_session(CONN, req.sess["token"])
    return redirect("/login", [("Set-Cookie", f"{auth.COOKIE}=; Max-Age=0; Path=/")])


@route("GET", "/setup", login_required=False)
def get_setup(req: Request) -> Response:
    if auth.has_users(CONN):
        return redirect("/login")
    return Response(ui.setup())


@route("POST", "/setup", login_required=False)
def post_setup(req: Request) -> Response:
    if auth.has_users(CONN):
        return redirect("/login")
    username = req.get("username").strip()
    password = req.get("password")
    if not username or len(password) < 10:
        return Response(ui.setup(_("Hasło musi mieć co najmniej 10 znaków.")), 400)
    if password != req.get("password2"):
        return Response(ui.setup(_("Hasła się różnią.")), 400)
    secret = wgcrypto.totp_secret() if req.get("totp") else None
    auth.create_user(CONN, username, password, secret)
    db.log(CONN, username, "setup.user_created")
    if secret:
        uri = wgcrypto.totp_uri(secret, username)
        return Response(ui.totp_enrolled(secret, uri, qr.svg(uri)))
    return redirect("/login")


# --------------------------------------------------------------------------
# widok glowny
# --------------------------------------------------------------------------

def timed(label: str, parts: dict, fn):
    """Mierzy czas fragmentu strony - rozbicie trafia do logu, gdy strona jest wolna."""
    start = time.time()
    try:
        return fn()
    finally:
        parts[label] = time.time() - start


@route("GET", "/")
def get_dashboard(req: Request) -> Response:
    parts: dict = {}
    cfg = db.settings(CONN)
    status, helper_error = timed("status", parts, lambda: safe_status(CONN))
    current = timed("model", parts, lambda: build_model(CONN))
    warn_days = int(cfg.get("expiry_warn_days", "3") or 0)
    # takze odciete: zajmuja swoje adresy, wiec musza byc widoczne, zeby dalo sie je usunac
    peers = timed("peery", parts, lambda: db.peers(CONN))
    dirty = timed("porownanie", parts, lambda: is_dirty(CONN, current, status))
    diff_parts = timed("co sie rozni", parts,
                       lambda: model.diff_summary(CONN, status) if dirty else [])
    # przycisk jest zawsze, gdy panel ma do czego wracac; osobno mowimy, czy to wystarczy
    can_discard = timed("czy odrzucic", parts,
                        lambda: model.can_discard(CONN) if dirty else False)
    discard_enough = timed("czy odrzucenie wystarczy", parts,
                           lambda: model.discard_helps(CONN, status) if can_discard else False)
    chart = timed("wykres", parts, lambda: traffic.hourly(CONN, None, 7))
    html = timed("html", parts, lambda: ui.dashboard(
        req.sess, cfg, peers, status, dirty,
        request_pending()["pending"], helper_error, req.query.get("msg", ""),
        alert_count(CONN),
        {p["id"]: db.describe_expiry(p) for p in peers},
        warnings={p["id"]: db.expiry_warning(p, warn_days) for p in peers},
        drift=drift_findings(),
        chart=chart,
        rolled_back=cfg.get("rolled_back") == "1",
        can_discard=can_discard,
        discard_enough=discard_enough,
        diff_parts=diff_parts,
        dual_use=dual.incidents(CONN),
        seen=stats.view(CONN, status),
    ))
    total = sum(parts.values())
    if total > 1.0:
        sys.stderr.write("wolna lista peerow: "
                         + ", ".join(f"{k} {v * 1000:.0f} ms" for k, v in parts.items())
                         + f" (razem {total:.1f} s)\n")
    return Response(html)


# --------------------------------------------------------------------------
# peery
# --------------------------------------------------------------------------

@route("GET", "/peers/new")
def get_peer_new(req: Request) -> Response:
    return Response(ui.peer_new(req.sess, db.tiers(CONN), db.next_free_ip(CONN),
                                alerts=alert_count(CONN)))


@route("POST", "/peers/new")
def post_peer_new(req: Request) -> Response:
    name = req.get("name").strip()
    if not name:
        return Response(ui.peer_new(req.sess, db.tiers(CONN), db.next_free_ip(CONN),
                                    _("Nazwa jest wymagana.")), 400)
    ip = req.get("ip").strip() or db.next_free_ip(CONN)
    fields = access_fields(req)
    # peer z oknem zaczynajacym sie w przyszlosci czeka zawieszony
    initial_state = ("suspended" if fields["starts_at"]
                     and fields["starts_at"] > time.time() else "active")
    private = wgcrypto.genkey()
    public = wgcrypto.pubkey(private)
    psk = wgcrypto.genpsk()
    try:
        with CONN:
            CONN.execute(
                """INSERT INTO peers(name, device, ip, pubkey, psk, privkey, tier_id,
                                     state, note, created_at, access_mode, starts_at,
                                     expires_at, expires_days, expires_from_use, auto_state)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (name, req.get("device").strip(), ip, public, psk, private,
                 int(req.get("tier_id") or 0) or None, initial_state,
                 req.get("note").strip(), int(time.time()), *access_values(fields),
                 1 if initial_state == "suspended" else 0),
            )
    except sqlite3.IntegrityError as exc:
        return Response(ui.peer_new(req.sess, db.tiers(CONN), db.next_free_ip(CONN),
                                    _("Nie udało się dodać: {error}", error=exc)), 400)
    peer_id = CONN.execute("SELECT id FROM peers WHERE ip = ?", (ip,)).fetchone()["id"]
    if render.ipv6_on(db.settings(CONN)):
        model.assign_ip6(CONN)
    db.log(CONN, req.actor, "peer.create", f"{name} {ip}")
    return redirect(with_msg(f"/peers/{peer_id}?nowy=1",
                             _("Peer utworzony. Dodaj kolejnych, jeśli trzeba - wszystkie zmiany "
                               "zastosujesz raz, z listy peerów.")))


@route("GET", r"/peers/(\d+)")
def get_peer(req: Request, peer_id: str) -> Response:
    peer = db.peer(CONN, int(peer_id))
    if not peer:
        return Response(ui.simple(_("Nie ma takiego peera"), _("Sprawdź adres.")), 404)
    cfg = db.settings(CONN)
    status, _unused = safe_status(CONN)
    stat = (status or {}).get("peers", {}).get(peer["pubkey"], {})
    conf, route = model.client_conf(CONN, peer)
    # sam obrazek dociaga sie osobno - inaczej strona czekalaby na kodowanie QR
    # odcisk klucza i klucza serwera w adresie: po rotacji albo zmianie serwera przegladarka
    # nie moze pokazac starego obrazka z pamieci (pokazywala - i nowy plik "nie dzialal")
    version = hashlib.sha256(conf.encode()).hexdigest()[:12]
    qr_svg = (f'<img src="/peers/{peer["id"]}/qr.svg?v={version}" alt="{ui.e(_("kod QR z konfiguracją"))}" '
              f'width="260" height="260" loading="lazy">') if peer["privkey"] else ""
    return Response(ui.peer_detail(
        req.sess, cfg, peer, db.rules_for(CONN, peer["id"]), db.tiers(CONN),
        conf, qr_svg, stat, req.query.get("link", ""), req.query.get("msg", ""),
        alerts=alert_count(CONN), expiry_text=db.describe_expiry(peer),
        dual_use=dual.incidents(CONN).get(peer["id"]),
        seen=stats.view(CONN, status).get(peer["id"]),
        warning=db.expiry_warning(peer, int(cfg.get("expiry_warn_days", "3") or 0)),
        chart=traffic.hourly(CONN, peer["pubkey"], 7),
        route=route, issued=model.issued_state(peer, route),
        just_created=req.query.get("nowy") == "1",
    ))


@route("POST", r"/peers/(\d+)/issued")
def post_peer_issued(req: Request, peer_id: str) -> Response:
    peer = db.peer(CONN, int(peer_id))
    if peer:
        _unused, route = model.client_conf(CONN, peer)
        model.mark_issued(CONN, peer["id"], route["allowed"])
        db.log(CONN, req.actor, "peer.issued", peer["name"])
    return redirect(f"/peers/{peer_id}?msg=" + urllib.parse.quote(_("Oznaczono jako wysłany.")))


@route("POST", r"/peers/(\d+)/edit")
def post_peer_edit(req: Request, peer_id: str) -> Response:
    """Jeden zapis dla calej strony peera - zmienia wylacznie to, co naprawde sie zmienilo.

    Wczesniej kazdy zapis, takze bez zadnej zmiany, kasowal flage "czeka na start okna"
    (harmonogram nie wlaczal wtedy osoby o czasie) i termin dostepu liczonego od pierwszego
    polaczenia (dostep na 7 dni stawal sie bezterminowy).
    """
    peer = CONN.execute("SELECT * FROM peers WHERE id = ?", (int(peer_id),)).fetchone()
    if not peer:
        return redirect_err("/", _("Nie ma takiego peera."))
    back = f"/peers/{peer_id}"
    name = req.get("name").strip()
    if not name:
        return redirect_err(back, _("Nazwa nie może być pusta."))
    new = {"name": name, "device": req.get("device").strip(),
           "tier_id": int(req.get("tier_id") or 0) or None, "note": req.get("note").strip()}
    new.update(access_fields(req))
    if new["access_mode"] == "from_use" and peer["first_seen"]:
        if peer["access_mode"] == "from_use" and peer["expires_from_use"]:
            # licznik juz biegnie - termin liczymy od pierwszego polaczenia, nie od nowa
            new["expires_at"] = end_of_day(peer["first_seen"] + new["expires_days"] * 86400)
        else:
            new["first_seen"] = None    # tryb wlaczony teraz: licznik od nastepnego polaczenia
    if "tunnel_mode" in req.form:
        try:
            new["tunnel_mode"], new["tunnel_list"] = tunnel_values(req)
        except render.RuleError as exc:
            return redirect_err(back, _("Lista: {error}", error=exc))

    def norm(value):
        return "" if value is None else value

    changed = {key: value for key, value in new.items() if norm(peer[key]) != norm(value)}
    if not changed:
        return redirect(with_msg(back, _("Nic się nie zmieniło.")))
    try:
        with CONN:
            CONN.execute(f"UPDATE peers SET {', '.join(k + ' = ?' for k in changed)} WHERE id = ?",
                         (*changed.values(), int(peer_id)))
    except sqlite3.IntegrityError:
        return redirect_err(back, _("Nazwa {name} jest już zajęta.", name=name))
    db.log(CONN, req.actor, "peer.edit", f"{peer['name']}: {', '.join(sorted(changed))}")
    if set(changed) <= {"tunnel_mode", "tunnel_list"}:
        return redirect(with_msg(back, _(TUNNEL_SAVED)))
    if {"tunnel_mode", "tunnel_list"} & set(changed):
        return redirect(with_msg(back, _("Zapisano. Plik klienta tej osoby się zmienił - "
                                         "przekaż jej nowy.")))
    return redirect(with_msg(back, _("Zapisano.")))


# klucz tlumaczenia - tlumaczy sie w miejscu uzycia (stala powstaje raz, przy starcie)
TUNNEL_SAVED = ("Zapisano. To zmienia tylko plik klienta - serwera nie trzeba stosować. "
                "Jeśli pojawi się ostrzeżenie o nieaktualnym pliku, wyślij nowy.")


def tunnel_values(req: Request) -> tuple:
    """Tryb tunelu i wlasna lista z formularza; zla lista -> render.RuleError."""
    mode = req.get("tunnel_mode")
    if mode not in ("", *render.TUNNEL_MODES):
        mode = ""
    listing = ", ".join(i.strip() for i in req.get("tunnel_list").split(",") if i.strip())
    if mode == "custom":
        cfg = db.render_cfg(CONN)
        if not listing:
            raise render.RuleError(_("podaj co najmniej jeden adres albo nazwę"))
        for item in listing.split(","):
            render.resolve_dest(item.strip(), cfg)
    return mode, listing


@route("POST", r"/peers/(\d+)/suspend")
def post_peer_suspend(req: Request, peer_id: str) -> Response:
    peer = db.peer(CONN, int(peer_id))
    new_state = "active" if peer["state"] == "suspended" else "suspended"
    with CONN:
        CONN.execute("UPDATE peers SET state=?, auto_state=0 WHERE id=?",
                     (new_state, int(peer_id)))
    db.log(CONN, req.actor, f"peer.{new_state}", peer["name"])
    state_name = _("zawieszony") if new_state == "suspended" else _("aktywny")
    return redirect(with_msg(f"/peers/{peer_id}", _("Stan zmieniony na: {state}.", state=state_name)))


@route("POST", r"/peers/(\d+)/delete")
def post_peer_delete(req: Request, peer_id: str) -> Response:
    peer = db.peer(CONN, int(peer_id))
    if not peer:
        return redirect("/")
    with CONN:
        CONN.execute("DELETE FROM rules WHERE peer_id = ?", (int(peer_id),))
        CONN.execute("DELETE FROM peers WHERE id = ?", (int(peer_id),))
    db.log(CONN, req.actor, "peer.delete", f'{peer["name"]} {peer["ip"]}')
    return redirect(with_msg("/", _("Usunięto {name} ({ip}). Zastosuj zmiany, żeby stracił "
                                    "dostęp. Jego adres jest znowu wolny.",
                                    name=peer["name"], ip=peer["ip"])))


@route("POST", r"/peers/(\d+)/rotate")
def post_peer_rotate(req: Request, peer_id: str) -> Response:
    private = wgcrypto.genkey()
    with CONN:
        CONN.execute(
            "UPDATE peers SET privkey=?, pubkey=?, psk=? WHERE id=?",
            (private, wgcrypto.pubkey(private), wgcrypto.genpsk(), int(peer_id)),
        )
    db.log(CONN, req.actor, "peer.rotate_keys", peer_id)
    return redirect(with_msg(f"/peers/{peer_id}",
                             _("Nowe klucze. Przekaż konfigurację i zastosuj zmiany.")))


@route("POST", r"/peers/(\d+)/wipe")
def post_peer_wipe(req: Request, peer_id: str) -> Response:
    peer = db.peer(CONN, int(peer_id))
    if peer and peer["privkey"]:
        _unused, route = model.client_conf(CONN, peer)
        model.mark_issued(CONN, peer["id"], route["allowed"])
    with CONN:
        CONN.execute("UPDATE peers SET privkey=NULL WHERE id=?", (int(peer_id),))
    db.log(CONN, req.actor, "peer.wipe_private_key", peer_id)
    return redirect(with_msg(f"/peers/{peer_id}", _("Klucz prywatny skasowany z serwera.")))


def test_refusal() -> Response | None:
    """Serwer testowy nie wydaje plikow osob (plik, QR, link) - jest kopia z prawdziwego,
    ale osoby maja dostawac pliki tylko z jednego miejsca."""
    if not names.is_test(db.settings(CONN)):
        return None
    return Response(ui.simple(_("Serwer testowy"), _(
        "Ten serwer jest testowy - plików, kodów QR i linków dla osób stąd nie wydajemy. "
        "Wydawaj je z właściwego serwera.")), 403)


@route("POST", "/test/off")
def post_test_off(req: Request) -> Response:
    db.set_setting(CONN, "test_mode", "0")
    db.log(CONN, req.actor, "test.off", "")
    return redirect(with_msg("/", _("To jest teraz właściwy serwer: kopie automatyczne i "
                                    "powiadomienia działają, pliki osób można wydawać.")))


@route("GET", r"/peers/(\d+)/qr.svg")
def get_peer_qr(req: Request, peer_id: str) -> Response:
    if test_refusal():
        return Response("", 403)
    peer = db.peer(CONN, int(peer_id))
    if not peer or not peer["privkey"]:
        return Response("", 404)
    conf, _unused = model.client_conf(CONN, peer)
    return Response(qr.svg(conf), content_type="image/svg+xml; charset=utf-8",
                    headers=[("Cache-Control", "no-store")])


@route("GET", r"/peers/(\d+)/config")
def get_peer_config(req: Request, peer_id: str) -> Response:
    refused = test_refusal()
    if refused:
        return refused
    peer = db.peer(CONN, int(peer_id))
    if not peer or not peer["privkey"]:
        return Response(ui.simple(_("Brak klucza"), _("Klucz prywatny został już skasowany.")), 404)
    conf, route = model.client_conf(CONN, peer)
    model.mark_issued(CONN, peer["id"], route["allowed"])
    filename = re.sub(r"[^A-Za-z0-9_.-]", "_", peer["name"]) or "peer"
    db.log(CONN, req.actor, "peer.config_download", peer["name"])
    return Response(conf, content_type="text/plain; charset=utf-8",
                    headers=[("Content-Disposition", f'attachment; filename="{filename}.conf"')])


@route("POST", r"/peers/(\d+)/delivery")
def post_peer_delivery(req: Request, peer_id: str) -> Response:
    refused = test_refusal()
    if refused:
        return refused
    token = secrets.token_urlsafe(24)
    now = int(time.time())
    with CONN:
        CONN.execute(
            "INSERT INTO deliveries(token, peer_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (token, int(peer_id), now, now + 86400),
        )
    db.log(CONN, req.actor, "peer.delivery_link", peer_id)
    host = req.handler.headers.get("Host", f"{BIND_HOST}:{BIND_PORT}")
    scheme = "https" if req.handler.is_tls else "http"
    link = urllib.parse.quote(f"{scheme}://{host}/d/{token}", safe="")
    return redirect(f"/peers/{peer_id}?link={link}")


@route("GET", r"/d/([A-Za-z0-9_-]+)", login_required=False)
def get_delivery(req: Request, token: str) -> Response:
    refused = test_refusal()          # takze link zrobiony jeszcze na prawdziwym serwerze
    if refused:
        return refused
    row = CONN.execute(
        "SELECT * FROM deliveries WHERE token = ? AND used_at IS NULL AND expires_at > ?",
        (token, int(time.time())),
    ).fetchone()
    if not row:
        return Response(ui.simple(_("Link nieaktualny"),
                                  _("Został już użyty albo wygasł. Poproś o nowy.")), 404)
    peer = db.peer(CONN, row["peer_id"])
    if not peer or not peer["privkey"]:
        return Response(ui.simple(_("Brak konfiguracji"), _("Klucz prywatny został skasowany.")), 404)
    conf, route = model.client_conf(CONN, peer)
    model.mark_issued(CONN, peer["id"], route["allowed"])
    with CONN:
        CONN.execute("UPDATE deliveries SET used_at=? WHERE token=?", (int(time.time()), token))
    db.log(CONN, "link", "delivery.used", peer["name"])
    return Response(ui.delivery_page(peer["name"], conf, qr.svg(conf)))


# --------------------------------------------------------------------------
# reguly
# --------------------------------------------------------------------------

@route("POST", r"/peers/(\d+)/rules")
def post_rule_add(req: Request, peer_id: str) -> Response:
    cfg = db.render_cfg(CONN)
    raw = {
        "action": req.get("action"),
        "dest": req.get("dest"),
        "proto": req.get("proto"),
        "ports": req.get("ports"),
        "time_from": req.get("time_from"),
        "time_to": req.get("time_to"),
        "weekdays": req.get("weekdays"),
        "comment": req.get("comment"),
    }
    try:
        render.normalize_rule(raw, cfg)
    except render.RuleError as exc:
        return redirect_err(f"/peers/{peer_id}", _("Reguła odrzucona: {error}", error=exc))
    position = CONN.execute(
        "SELECT COALESCE(MAX(position), 0) + 1 AS p FROM rules WHERE peer_id = ?", (peer_id,)
    ).fetchone()["p"]
    with CONN:
        CONN.execute(
            """INSERT INTO rules(peer_id, position, action, dest, proto, ports,
                                 time_from, time_to, weekdays, comment)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (int(peer_id), position, raw["action"].upper(), raw["dest"], raw["proto"],
             raw["ports"], raw["time_from"], raw["time_to"], raw["weekdays"], raw["comment"]),
        )
    db.log(CONN, req.actor, "rule.add", f"peer={peer_id} {raw['action']} {raw['dest']}")
    return redirect(with_msg(f"/peers/{peer_id}", _("Reguła dodana.")))


@route("POST", r"/peers/(\d+)/rules/(\d+)/delete")
def post_rule_delete(req: Request, peer_id: str, rule_id: str) -> Response:
    with CONN:
        CONN.execute("DELETE FROM rules WHERE id=? AND peer_id=?", (int(rule_id), int(peer_id)))
    db.log(CONN, req.actor, "rule.delete", f"peer={peer_id} rule={rule_id}")
    return redirect(with_msg(f"/peers/{peer_id}", _("Reguła usunięta.")))


# --------------------------------------------------------------------------
# zastosowanie zmian
# --------------------------------------------------------------------------

@route("GET", "/apply/diff")
def get_apply_diff(req: Request) -> Response:
    """Sama roznica, jako zwykly tekst - lista peerow wczytuje ja bez zmiany strony."""
    try:
        preview = helperclient.call("preview", build_model(CONN))
    except helperclient.HelperError as exc:
        return Response(_("Helper niedostępny: {error}", error=i18n.helper(exc)), 503,
                        content_type="text/plain; charset=utf-8")
    parts = [render.diff(preview.get("current_wg_conf", ""), preview["wg_conf"])]
    rules = render.diff(preview.get("current_rules", ""), preview["rules"])
    if preview.get("rules6") or preview.get("current_rules6"):
        rules += "\n" + render.diff(preview.get("current_rules6", ""), preview.get("rules6", ""))
    text = f"--- wg0.conf ---\n{parts[0]}\n--- reguly ---\n{rules}"
    return Response(text, content_type="text/plain; charset=utf-8")


@route("GET", "/apply")
def get_apply(req: Request) -> Response:
    cfg = db.render_cfg(CONN)
    current = build_model(CONN)
    try:
        preview = helperclient.call("preview", current)
    except helperclient.HelperError as exc:
        return Response(ui.apply_preview(req.sess, cfg, "", "", "", str(exc),
                                         alerts=alert_count(CONN)), 503)
    diff_conf = render.diff(preview.get("current_wg_conf", ""), preview["wg_conf"])
    diff_rules = render.diff(preview.get("current_rules", ""), preview["rules"])
    rules_text = preview["rules"]
    if preview.get("rules6") or preview.get("current_rules6"):
        diff_rules += "\n\n# ---- IPv6 ----\n" + render.diff(
            preview.get("current_rules6", ""), preview.get("rules6", ""))
        rules_text += "\n# ---- IPv6 ----\n" + preview.get("rules6", "")
    return Response(ui.apply_preview(req.sess, cfg, diff_conf, diff_rules, rules_text,
                                     alerts=alert_count(CONN),
                                     pending=bool(request_pending()["pending"])))


@route("POST", "/apply")
def post_apply(req: Request) -> Response:
    # z panelu zawsze z bezpiecznikiem - bez niego tylko awaryjnie: wgctl apply --no-safe
    seconds = db.confirm_window(db.settings(CONN))
    current = build_model(CONN)
    try:
        result = helperclient.call("apply", dict(current, confirm_seconds=seconds), timeout=120)
    except helperclient.HelperError as exc:
        db.log(CONN, req.actor, "apply.failed", str(exc))
        status_changed()
        if "czekajaca na potwierdzenie" in str(exc):
            return redirect_err("/", _("Poprzednia zmiana wciąż czeka na potwierdzenie - "
                                       "potwierdź ją albo cofnij na pasku u góry."))
        return redirect_err("/", _("Zastosowanie nieudane: {error}", error=i18n.helper(exc)))
    remember_before_apply(CONN)
    model.mark_applied(CONN, current, result)
    status_changed()
    db.set_setting(CONN, "rolled_back", "")
    db.set_setting(CONN, "drift_findings", "")
    token = result.get("confirm_token") or ""
    db.set_setting(CONN, "confirm_token", token)
    auto = db.settings(CONN).get("confirm_mode", "auto") != "manual"
    db.set_setting(CONN, "confirm_auto", token_digest(token) if token and auto else "")
    # dopiero od tej chwili reguly sa na miejscu - starsze polaczenie niczego nie dowodzi
    db.set_setting(CONN, "confirm_since", f"{time.time():.3f}" if token else "")
    db.log(CONN, req.actor, "apply.ok", "; ".join(result.get("steps", [])))
    # bez zielonego paska: o potwierdzeniu mowi jeden pasek u gory, na kazdej stronie
    return redirect("/")


def finish_confirm(result: dict, actor: str, action: str = "apply.confirmed") -> None:
    model.store_fingerprint(CONN, result)
    db.set_setting(CONN, "confirm_token", "")
    forget_before_apply(CONN)
    # bez tego przez 2 s strona dostawala stan sprzed potwierdzenia i pasek nie znikal
    status_changed()
    db.log(CONN, actor, action)


def auto_confirm(req: Request) -> Response:
    """Przegladarka sama potwierdza, ze po zmianie nadal dociera do panelu.

    To jest dokladnie to, co sprawdza bezpiecznik: czy zmiana nie odciela Cie od panelu.
    Dowodem jest tylko polaczenie nawiazane PO zastosowaniu - stare, juz otwarte
    polaczenie mogloby przejsc przez reguly, ktore nowe blokuja.
    """
    def reply(**payload) -> Response:
        return Response(json.dumps(payload), content_type="application/json; charset=utf-8")

    cfg = db.settings(CONN)
    token = cfg.get("confirm_token", "")
    if not token:
        return reply(ok=False, done=True, error=_("Nic nie czeka na potwierdzenie."))
    if cfg.get("confirm_auto") != token_digest(token):
        return reply(ok=False, manual=True)       # tryb reczny albo zmiana z wgctl
    since = float(cfg.get("confirm_since") or 0)
    if getattr(req.handler, "connected_at", 0.0) <= since:
        req.handler.close_connection = True
        return reply(ok=False, retry=True)
    try:
        result = helperclient.call("confirm", {"token": token})
    except helperclient.HelperError as exc:
        status_changed()
        pending_state(CONN)
        return reply(ok=False, done=True, error=_("Nie potwierdzono: {error}", error=i18n.helper(exc)))
    finish_confirm(result, req.actor, "apply.confirmed.auto")
    # ta sama nazwa co na przycisku "Zastosuj zmiany"
    return reply(ok=True, message=_("Zastosowano zmiany."))


@route("POST", "/confirm")
def post_confirm(req: Request) -> Response:
    if req.get("auto") == "1":
        return auto_confirm(req)
    back = safe_next(req.get("next"))
    cfg = db.settings(CONN)
    token = cfg.get("confirm_token", "")
    if not token:
        # np. przycisk z nieaktualnej karty przegladarki, gdy pasek juz wiedzial o cofnieciu
        if cfg.get("rolled_back") == "1":
            return redirect_err(back, _("Za późno - zmiany zostały już cofnięte. Możesz je "
                                        "zastosować ponownie z listy peerów."))
        return redirect_err(back, _("Nic nie czeka na potwierdzenie."))
    try:
        result = helperclient.call("confirm", {"token": token})
    except helperclient.HelperError as exc:
        # stan i tak trzeba uporzadkowac (np. zmiana wlasnie cofnela sie sama);
        # komunikat z pending_state pomijamy - ten ponizej mowi to samo
        status_changed()
        pending_state(CONN)
        return redirect_err(back, _("Nie potwierdzono: {error}", error=i18n.helper(exc)))
    finish_confirm(result, req.actor)
    return redirect(with_msg(back, _("Potwierdzone - zmiany zostają na stałe.")))


@route("GET", "/disk")
def get_disk(req: Request) -> Response:
    """Porownanie: co jest w panelu, a co w konfiguracji dzialajacej na Pi."""
    try:
        preview = helperclient.call("preview", build_model(CONN))
    except helperclient.HelperError as exc:
        return Response(ui.simple(_("Helper niedostępny"), i18n.helper(exc)), 503)

    _unused, disk_peers = import_conf.parse_text(preview.get("current_wg_conf", ""))
    on_disk = {}
    for peer in disk_peers:
        key = peer.get("PublicKey", "")
        ip = (peer.get("AllowedIPs", "").split(",")[0] or "").split("/")[0].strip()
        if key:
            on_disk[key] = {"ip": ip, "name": peer.get("comment", ""), "pubkey": key}

    in_panel = {row["pubkey"]: row for row in db.peers(CONN)}
    rows = []
    for key, disk in on_disk.items():
        panel = in_panel.get(key)
        if panel is None:
            rows.append({"where": "tylko na dysku", "name": disk["name"] or "(bez nazwy)",
                         "ip": disk["ip"], "pubkey": key, "add": True})
        else:
            rows.append({"where": "zgodne", "name": panel["name"], "ip": panel["ip"],
                         "pubkey": key, "add": False})
    for key, panel in in_panel.items():
        if key not in on_disk:
            where = ("tylko w panelu" if panel["state"] == "active"
                     else f'tylko w panelu ({panel["state"]})')
            rows.append({"where": where, "name": panel["name"], "ip": panel["ip"],
                         "pubkey": key, "add": False})

    status, _unused = safe_status(CONN)
    return Response(ui.disk_page(req.sess, rows, model.diff_summary(CONN, status),
                                 req.query.get("msg", ""), alerts=alert_count(CONN)))


@route("POST", "/disk/pull")
def post_disk_pull(req: Request) -> Response:
    """Dopisuje do panelu wskazanych peerow z konfiguracji na dysku."""
    wanted = set(req.get_all("pubkey"))
    if not wanted:
        return redirect_err("/disk", _("Nie zaznaczyłeś nikogo."))
    try:
        preview = helperclient.call("preview", build_model(CONN))
    except helperclient.HelperError as exc:
        return redirect_err("/disk", _("Helper niedostępny: {error}", error=i18n.helper(exc)))

    _unused, disk_peers = import_conf.parse_text(preview.get("current_wg_conf", ""))
    known = {row["pubkey"] for row in CONN.execute("SELECT pubkey FROM peers")}
    taken = {row["ip"] for row in CONN.execute("SELECT ip FROM peers")}
    default_tier = CONN.execute(
        "SELECT id FROM tiers ORDER BY builtin DESC, id LIMIT 1").fetchone()
    added = 0
    for peer in disk_peers:
        pubkey = peer.get("PublicKey", "")
        ip = (peer.get("AllowedIPs", "").split(",")[0] or "").split("/")[0].strip()
        if pubkey not in wanted or pubkey in known or not ip or ip in taken:
            continue
        with CONN:
            CONN.execute(
                """INSERT INTO peers(name, device, ip, pubkey, psk, privkey, tier_id, state,
                                     note, created_at)
                   VALUES (?, '', ?, ?, ?, NULL, ?, 'active', ?, ?)""",
                ((peer.get("comment") or f"z dysku {ip}")[:60], ip, pubkey,
                 peer.get("PresharedKey", ""), default_tier["id"] if default_tier else None,
                 "wczytany z konfiguracji na dysku", int(time.time())),
            )
        known.add(pubkey)
        taken.add(ip)
        added += 1
    db.log(CONN, req.actor, "config.pull", f"dodano {added}")
    return redirect(with_msg("/disk", _(
        "Dodano {n} osób do panelu. Sprawdź im tier i nazwę, potem zastosuj zmiany.", n=added)
        if added else _("Nic nie doszło - panel zna już te osoby.")))


@route("POST", "/discard")
def post_discard(req: Request) -> Response:
    if db.settings(CONN).get("confirm_token"):
        return redirect_err("/", _("Najpierw potwierdź albo cofnij zmianę czekającą na pasku "
                                   "u góry - dopiero potem można odrzucać zmiany w panelu."))
    try:
        summary = model.discard_changes(CONN, req.actor)
        status_changed()
        db.set_setting(CONN, "rolled_back", "")
    except Exception as exc:  # noqa: BLE001
        return redirect_err("/", _("Nie mogę odrzucić zmian: {error}", error=exc))
    return redirect(with_msg("/", _("Odrzucono zmiany w panelu - wrócił stan z ostatniego "
                                    "zastosowania ({peers} peerów, {tiers} tierów).",
                                    peers=summary["peers"], tiers=summary["tiers"])))


@route("POST", "/rollback")
def post_rollback(req: Request) -> Response:
    """Dwa przyciski, dwa znaczenia.

    Pasek oczekiwania (scope=pending): cofa TYLKO zmiane czekajaca na potwierdzenie.
    Gdy nic nie czeka, nie robi nic - wczesniej nieaktualny pasek cofal do ostatniej
    kopii, czyli wycofywal zmiane, ktora wlasnie zostala potwierdzona.
    Ustawienia (bez scope): cofa do ostatniej kopii plikow, jak dotad.
    """
    only_pending = req.get("scope") == "pending"
    back = safe_next(req.get("next")) if only_pending else "/"
    token = db.settings(CONN).get("confirm_token", "")
    payload = {"settings": model.settings_only(CONN)}
    if only_pending:
        payload.update(only_pending=True, token=token)
    try:
        result = helperclient.call("rollback", payload, timeout=120)
    except helperclient.HelperError as exc:
        status_changed()
        pending_state(CONN)
        return redirect_err(back, _("Nie cofnięto: {error}", error=i18n.helper(exc)))
    if result.get("rolled_back") == "pending":
        restore_before_apply(CONN, result.get("fingerprint"))
        text = _("Cofnięto zmianę - na interfejsie działa poprzednia konfiguracja. "
                 "Twoje zmiany zostały w panelu jako niezastosowane.")
    else:
        db.set_setting(CONN, "applied_hash", "")
        db.set_setting(CONN, "confirm_token", "")
        forget_before_apply(CONN)
        db.set_setting(CONN, "rolled_back", "1")
        model.store_fingerprint(CONN, result)
        status_changed()
        text = _("Cofnięto do ostatniej kopii konfiguracji.")
    db.log(CONN, req.actor, "rollback", str(result.get("rolled_back")))
    return redirect(with_msg(back, text))


@route("POST", "/restart")
def post_restart(req: Request) -> Response:
    try:
        helperclient.call("restart", {"settings": model.settings_only(CONN)}, timeout=60)
    except helperclient.HelperError as exc:
        return redirect_err("/settings", _("Restart nieudany: {error}", error=i18n.helper(exc)))
    status_changed()
    db.log(CONN, req.actor, "wg.restart")
    return redirect(with_msg("/", _("Zrestartowano wg-quick.")))


# --------------------------------------------------------------------------
# symulator regul
# --------------------------------------------------------------------------

@route("GET", "/simulator")
def get_simulator(req: Request) -> Response:
    cfg = db.render_cfg(CONN)
    peers = peers_for_render(CONN)
    form = {k: req.query.get(k, "") for k in ("src", "dst", "port", "proto", "at")}
    if not req.query.get("dst"):
        nas = db.host_map(CONN).get("nas") or {}
        form["dst_default"] = nas.get("v4") or nas.get("address") or ""
    result = None
    if form["src"] and form["dst"]:
        when = None
        if form["at"].strip():
            for pattern in ("%Y-%m-%d %H:%M", "%H:%M"):
                try:
                    parsed = time.strptime(form["at"].strip(), pattern)
                except ValueError:
                    continue
                if pattern == "%H:%M":
                    today = time.localtime()
                    parsed = time.struct_time((today.tm_year, today.tm_mon, today.tm_mday,
                                               parsed.tm_hour, parsed.tm_min, 0, 0, 0, -1))
                when = time.mktime(parsed)
                break
        port = int(form["port"]) if form["port"].strip().isdigit() else None
        result = simulate.simulate(cfg, peers, form["src"], form["dst"].strip(),
                                   port, form["proto"] or "tcp", when)
    return Response(ui.simulator_page(req.sess, cfg, peers, form, result,
                                      alerts=alert_count(CONN)))


# --------------------------------------------------------------------------
# alerty
# --------------------------------------------------------------------------

def page_number(req: Request) -> int:
    value = req.query.get("page", "1")
    return max(1, min(int(value), 10000)) if value.isdigit() else 1


ALERTS_PER_PAGE = 50
AUDIT_PER_PAGE = 100


@route("GET", "/alerts")
def get_alerts(req: Request) -> Response:
    number = page_number(req)
    rows = db.alerts_tail(CONN, ALERTS_PER_PAGE, (number - 1) * ALERTS_PER_PAGE)
    cfg = db.settings(CONN)
    return Response(ui.alerts_page(req.sess, cfg, rows,
                                   req.query.get("msg", ""), alerts=alert_count(CONN),
                                   paging=(number, ALERTS_PER_PAGE, db.alerts_count(CONN)),
                                   days=db.retention_days(cfg, "alerts_days")))


@route("POST", "/alerts/seen")
def post_alerts_seen(req: Request) -> Response:
    notify.mark_seen(CONN)
    return redirect("/alerts")


@route("POST", r"/alerts/(\d+)/delete")
def post_alert_delete(req: Request, alert_id: str) -> Response:
    with CONN:
        CONN.execute("DELETE FROM alerts WHERE id = ?", (int(alert_id),))
    return redirect("/alerts")


@route("POST", "/alerts/clear")
def post_alerts_clear(req: Request) -> Response:
    with CONN:
        removed = CONN.execute("DELETE FROM alerts").rowcount
    db.log(CONN, req.actor, "alerts.clear", str(removed))
    return redirect(with_msg("/alerts", _("Usunięto alerty: {n}.", n=removed)))


@route("POST", "/alerts/settings")
def post_alerts_settings(req: Request) -> Response:
    for key in ("alerts_channel", "alerts_url", "alerts_token", "alert_dead_hours",
                "alerts_quiet_minutes", "smtp_host", "smtp_port", "smtp_user", "smtp_pass",
                "smtp_from", "smtp_to"):
        if key in req.form:
            db.set_setting(CONN, key, req.get(key).strip())
    security = req.get("smtp_security")
    db.set_setting(CONN, "smtp_security", security if security in ("starttls", "ssl", "none")
                   else "starttls")
    # pole wyboru: odznaczone w ogole nie przychodzi w formularzu
    db.set_setting(CONN, "alerts_email", "1" if req.get("alerts_email") == "1" else "0")
    db.set_setting(CONN, dual.SETTING, "1" if req.get(dual.SETTING) == "1" else "0")
    db.log(CONN, req.actor, "alerts.settings")
    return redirect(with_msg("/alerts", _("Zapisano.")))


@route("POST", "/alerts/test")
def post_alerts_test(req: Request) -> Response:
    cfg = db.settings(CONN)
    if names.is_test(cfg):
        return redirect_err("/alerts", _("Serwer testowy - powiadomienia są wyłączone. Alerty "
                                         "widać tylko tutaj."))
    # polskie znaki celowo - test pokazuje od razu, czy tytul i tresc dochodza poprawnie
    ok = notify.send(cfg, "wg-panel: test powiadomień",
                     "Testowy alert z panelu. Jeśli to widzisz z polskimi znakami (ąęłńóśźż), "
                     "kanał działa.", "info")
    db.log(CONN, req.actor, "alerts.test", "ok" if ok else "nieudany")
    if not ok:
        return redirect_err("/alerts", _("Nie udało się wysłać - sprawdź kanał i adres."))
    return redirect(with_msg("/alerts", _("Wysłano.")))


# --------------------------------------------------------------------------
# kopie zapasowe
# --------------------------------------------------------------------------

BACKUP_ROWS = 30          # tyle kopii na liscie; reszta po "Pokaz wszystkie"


@route("GET", "/backups")
def get_backups(req: Request) -> Response:
    # pliki polozone recznie w katalogu roboczym trafiaja na liste (jedna lista kopii);
    # NAS NIE jest tu odpytywane - tylko przyciskiem "Odswiez liste z NAS"
    try:
        export.adopt_local(CONN)
    except (OSError, sqlite3.Error):
        pass
    cfg = db.settings(CONN)
    rows = db.backups_list(CONN, None if req.query.get("all") else BACKUP_ROWS)
    return Response(ui.backups_page(req.sess, cfg, rows, export.local_files(cfg),
                                    req.query.get("msg", ""), alerts=alert_count(CONN),
                                    scan=export.last_scan(cfg), total=db.backups_count(CONN)))


def smb_message(cfg: dict, exc: Exception) -> str:
    """Blad SMB w jezyku panelu, zawsze z nazwa miejsca na kopie na poczatku (dokladnie raz)."""
    text = export.explain_smb(cfg, exc)
    shown = i18n.helper(text)
    name = names.storage(cfg)
    return shown if text.startswith(name + ":") else f"{name}: {shown}"


@route("POST", "/backups/scan")
def post_backup_scan(req: Request) -> Response:
    """Jedyne miejsce, ktore pyta NAS o liste kopii - na klikniecie, nigdy w tle."""
    cfg = db.settings(CONN)
    try:
        result = export.scan_smb(CONN)
    except Exception as exc:  # noqa: BLE001
        return redirect_err("/backups#lista",
                            smb_message(cfg, exc))
    message = _("{storage}: {n} kopii.", **names.kw(), n=result["count"])
    if result["added"]:
        message += " " + _("Dopisane do listy: {n} (panel ich nie znał, np. sprzed reinstalacji).",
                           n=result["added"])
    return redirect(with_msg("/backups", message) + "#lista")


@route("POST", "/backups/run")
def post_backup_run(req: Request) -> Response:
    # "Na NAS" / "Na Pi": cel tej jednej kopii; kopie automatyczne bez zmian
    target = req.get("target") if req.get("target") in export.TARGETS else None
    record = export.run(CONN, actor=req.actor, target=target)
    if not record["ok"]:
        return redirect_err("/backups", _("Kopia się nie powiodła: {error}",
                                          error=i18n.helper(record["detail"])))
    return redirect(with_msg("/backups", _("Kopia zrobiona: {path}", path=record["path"])))


@route("POST", "/backups/delete")
def post_backup_delete(req: Request) -> Response:
    ids = [int(value) for value in req.get_all("id") if value.isdigit()]
    if not ids:
        return redirect_err("/backups", _("Nie zaznaczyłeś żadnej kopii."))
    summary = export.delete_backups(CONN, ids)
    db.log(CONN, req.actor, "backup.delete",
           i18n.pl("{n} ({server}: {local}, {storage}: {remote})", n=summary["deleted"],
                   local=summary["local"], remote=summary["remote"], **names.kw()))
    message = _("Usunięto kopie: {n} (pliki - {server}: {local}, {storage}: {remote}).", **names.kw(),
                n=summary["deleted"], local=summary["local"], remote=summary["remote"])
    if summary["problems"]:
        problems = "; ".join(i18n.helper(problem) for problem in summary["problems"])
        return redirect_err("/backups", message + " " + _("Nie usunięto: {problems}", problems=problems))
    return redirect(with_msg("/backups", message))


@route("POST", "/backups/settings")
def post_backup_settings(req: Request) -> Response:
    db.set_setting(CONN, "backup_enabled", "1" if req.get("backup_enabled") else "0")
    if req.get("backup_target") and req.get("backup_target") not in export.TARGETS:
        return redirect_err("/backups#ustawienia", _("Nieznany cel kopii."))
    for key in ("backup_time", "backup_keep", "backup_target", "backup_dir",
                "backup_passphrase", "smb_host", "smb_share", "smb_path",
                "smb_user", "smb_pass"):
        if key in req.form:
            db.set_setting(CONN, key, req.get(key).strip())
    db.log(CONN, req.actor, "backup.settings")
    return redirect(with_msg("/backups", _("Zapisano.")))


@route("POST", "/backups/name")
def post_backup_name(req: Request) -> Response:
    """Zmien nazwe - Zapisz: sama nazwa, niezaleznie od reszty ustawien kopii."""
    key = req.get("key")
    if key not in names.DEFAULTS:
        return redirect_err("/backups#ustawienia", _("Nieznana nazwa."))
    value = names.clean(req.get("value"), key)
    db.set_setting(CONN, key, value)
    db.log(CONN, req.actor, "backup.name", value)
    return redirect(with_msg("/backups", _("Zapisano nazwę: {name}", name=value)) + "#ustawienia")


@route("POST", "/backups/export")
def post_backup_export(req: Request) -> Response:
    # puste pole = haslo kopii z ustawien (wtedy plik to pelna kopia, jak na NAS)
    secret = req.get("passphrase") or db.settings(CONN).get("backup_passphrase", "")
    blob, encrypted, kind = export.pack(CONN, secret)
    name = export.filename(encrypted)
    db.log(CONN, req.actor, "backup.export", export.KIND_DETAIL[kind])
    return Response(blob, content_type="application/octet-stream",
                    headers=[("Content-Disposition", f'attachment; filename="{name}"')])


@route("POST", "/backups/restore")
def post_backup_restore(req: Request) -> Response:
    try:
        source = req.get("source")
        if not source:
            # bez okna (np. bez JavaScriptu) przychodza zaznaczone wiersze listy
            ids = [value for value in req.get_all("id") if value.isdigit()]
            if len(ids) != 1:
                raise ValueError(_("zaznacz na liście jedną kopię"))
            source = f"id:{ids[0]}"
        secret = req.get("passphrase") or db.settings(CONN).get("backup_passphrase", "")
        summary = export.restore_from(CONN, source, secret,
                                      bool(req.get("with_users")), req.actor)
    except Exception as exc:  # noqa: BLE001
        return redirect_err("/backups", _("Odtwarzanie nieudane: {error}", error=exc))
    identity = export.apply_identity(CONN, summary)
    message = _("Odtworzono z {source}: {peers} peerów, {tiers} tierów, {rules} reguł. Poprzedni "
                "stan zapisany jako {safety}. Zastosuj zmiany, żeby trafiły na interfejs.",
                source=summary["source"], peers=summary["peers"], tiers=summary["tiers"],
                rules=summary["rules"], safety=summary["safety"])
    message += " " + identity_note(identity)
    if identity["verdict"] == "changed" or identity["error"]:
        return redirect_err("/", message)
    return redirect(with_msg("/", message))


def identity_note(identity: dict) -> str:
    """Jedno zdanie po odtworzeniu: czy dotychczasowe pliki osob zadzialaja."""
    notes = {
        "restored": _("Klucz serwera przywrócony z kopii - po zastosowaniu zmian wszyscy łączą "
                      "się swoimi dotychczasowymi plikami."),
        "same": _("Klucz serwera ({server}) jest taki sam jak w kopii - dotychczasowe pliki osób "
                  "działają.", **names.kw()),
        "changed": _("Klucz serwera ({server}) jest INNY niż w kopii, a kopia go nie zawiera. Dotychczasowe "
                     "pliki osób nie zadziałają - każdej osobie wydaj nowy plik (Rotacja kluczy na karcie "
                     "osoby, potem Zastosuj zmiany).", **names.kw()),
        "unknown": "",
    }
    text = notes[identity["verdict"]]
    if identity["error"]:
        text += " " + _("Nie udało się przywrócić klucza serwera: {error}",
                        error=i18n.helper(identity["error"]))
    if identity["ca"] == "new":
        text += " " + _("Certyfikat HTTPS: powstał nowy urząd (inna nazwa albo sieć serwera niż w kopii) - "
                        "zainstaluj nowy ca.crt na urządzeniach.")
    elif identity["ca"] == "restored":
        text += " " + _("Certyfikat HTTPS przywrócony - urządzenia nie potrzebują nowego ca.crt.")
    return text.strip()


# --------------------------------------------------------------------------
# katalog hostow LAN
# --------------------------------------------------------------------------

HOST_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


def host_usage(name: str) -> list[str]:
    """Gdzie nazwa hosta jest uzyta - zeby nie skasowac czegos, na czym stoja reguly."""
    wanted = {name.lower(), "@" + name.lower()}
    used = []
    for row in CONN.execute(
            "SELECT r.dest, p.name FROM rules r JOIN peers p ON p.id = r.peer_id"):
        if row["dest"].lower() in wanted:
            used.append(_("reguła peera {name}", name=row["name"]))
    for tier in db.tiers(CONN):
        for rule in json.loads(tier["rules_json"] or "[]"):
            if str(rule.get("dest", "")).lower() in wanted:
                used.append(f'tier {tier["name"]}')
                break
    return used


def validate_host(address: str, address6: str) -> str:
    """Zwraca komunikat bledu albo pusty tekst."""
    if not address and not address6:
        return _("podaj adres IPv4, IPv6 albo oba")
    try:
        if address and ipaddress.ip_network(address, strict=False).version != 4:
            return "pole IPv4 zawiera adres IPv6"
        for item in [a for a in address6.split(",") if a.strip()]:
            if ipaddress.ip_network(item.strip(), strict=False).version != 6:
                return "pole IPv6 zawiera adres IPv4"
    except ValueError as exc:
        return f"zly adres: {exc}"
    return ""


@route("GET", "/hosts")
def get_hosts(req: Request) -> Response:
    rows = [dict(row, used=host_usage(row["name"])) for row in db.hosts(CONN)]
    return Response(ui.hosts_page(req.sess, db.settings(CONN), rows,
                                  req.query.get("msg", ""), alerts=alert_count(CONN)))


@route("POST", "/hosts/new")
def post_host_new(req: Request) -> Response:
    name = req.get("name").strip().lower()
    address, address6 = req.get("address").strip(), req.get("address6").strip()
    if not HOST_NAME.match(name) or name in ("lan", "any"):
        return redirect_err(
            "/hosts", _("Nazwa: małe litery, cyfry, - i _, do 32 znaków. LAN i ANY są zajęte."))
    problem = validate_host(address, address6)
    if problem:
        return redirect_err("/hosts", problem)
    try:
        with CONN:
            CONN.execute(
                "INSERT INTO hosts(name, address, address6, description) VALUES (?, ?, ?, ?)",
                (name, address, address6, req.get("description").strip()))
    except sqlite3.IntegrityError:
        return redirect_err("/hosts", _("Host {name} już istnieje.", name=name))
    db.log(CONN, req.actor, "host.create", f"{name} {address} {address6}")
    return redirect(with_msg("/hosts", _("Dodano {name}.", name=name)))


@route("POST", r"/hosts/(\d+)/edit")
def post_host_edit(req: Request, host_id: str) -> Response:
    row = CONN.execute("SELECT * FROM hosts WHERE id = ?", (int(host_id),)).fetchone()
    if not row:
        return redirect("/hosts")
    address, address6 = req.get("address").strip(), req.get("address6").strip()
    problem = validate_host(address, address6)
    if problem:
        return redirect_err("/hosts", problem)
    with CONN:
        CONN.execute("UPDATE hosts SET address=?, address6=?, description=? WHERE id=?",
                     (address, address6, req.get("description").strip(), int(host_id)))
    db.log(CONN, req.actor, "host.edit",
           f'{row["name"]}: {row["address"]} -> {address} {address6}')
    used = host_usage(row["name"])
    message = _("Zapisano {name}.", name=row["name"])
    if used:
        message += " " + _("Zmiana dotyczy {n} miejsc - zastosuj zmiany.", n=len(used))
    return redirect("/hosts?msg=" + urllib.parse.quote(message) + f"#host-{host_id}")


@route("POST", r"/hosts/(\d+)/delete")
def post_host_delete(req: Request, host_id: str) -> Response:
    row = CONN.execute("SELECT * FROM hosts WHERE id = ?", (int(host_id),)).fetchone()
    if not row:
        return redirect("/hosts")
    used = host_usage(row["name"])
    if used:
        return redirect_err(
            "/hosts", _("Nie usunę {name} - używają go: {users}.", name=row["name"],
                        users=", ".join(used[:5])))
    with CONN:
        CONN.execute("DELETE FROM hosts WHERE id = ?", (int(host_id),))
    db.log(CONN, req.actor, "host.delete", row["name"])
    return redirect(with_msg("/hosts", _("Usunięto {name}.", name=row["name"])))


# --------------------------------------------------------------------------
# IPv6
# --------------------------------------------------------------------------

@route("POST", "/settings/ipv6")
def post_ipv6(req: Request) -> Response:
    enabled = "1" if req.get("ipv6_enabled") else "0"
    values = {key: req.get(key).strip() for key in ("vpn6", "server_ip6", "lan6", "dns6")}
    values["nat6"] = "1" if req.get("nat6") else "0"

    if enabled == "1":
        try:
            net6 = ipaddress.ip_network(values["vpn6"], strict=False)
            if net6.version != 6:
                raise ValueError(_("podsieć VPN IPv6 musi być IPv6"))
            if net6.prefixlen > 120:
                raise ValueError(_("podsieć VPN IPv6 jest za mała"))
            if ipaddress.ip_address(values["server_ip6"]) not in net6:
                raise ValueError(_("adres serwera IPv6 poza podsiecią VPN"))
            lan6 = [item.strip() for item in values["lan6"].split(",") if item.strip()]
            if not lan6:
                raise ValueError(_("podaj prefiks IPv6 sieci domowej - bez niego blokada LAN "
                                   "nie miałaby odpowiednika w IPv6 i tier NAS widziałby cały "
                                   "LAN po IPv6"))
            for item in lan6:
                if ipaddress.ip_network(item, strict=False).version != 6:
                    raise ValueError(_("{item} to nie jest prefiks IPv6", item=item))
        except ValueError as exc:
            return redirect_err("/settings", f"IPv6: {exc}")

    for key, value in values.items():
        db.set_setting(CONN, key, value)
    db.set_setting(CONN, "ipv6_enabled", enabled)
    assigned = model.assign_ip6(CONN) if enabled == "1" else 0
    db.log(CONN, req.actor, "ipv6." + ("on" if enabled == "1" else "off"),
           f"przydzielono {assigned} adresow")
    if enabled != "1":
        message = _("IPv6 wyłączone. Zastosuj zmiany.")
    elif assigned:
        message = _("IPv6 włączone, przydzielono {n} adresów. Zastosuj zmiany, a potem wydaj peerom "
                    "nowe pliki konfiguracyjne.", n=assigned)
    else:
        message = _("IPv6 włączone. Zastosuj zmiany, a potem wydaj peerom nowe pliki konfiguracyjne.")
    return redirect(with_msg("/settings", message))


@route("POST", "/settings/ipv6/detect")
def post_ipv6_detect(req: Request) -> Response:
    try:
        found = helperclient.call("detect_lan6",
                                  {"settings": model.settings_only(CONN)}).get("prefixes", [])
    except helperclient.HelperError as exc:
        return redirect_err("/settings", _("Wykrywanie: {error}", error=i18n.helper(exc)))
    if not found:
        return redirect_err(
            "/settings", _("Nie znalazłem prefiksu IPv6 na interfejsie WAN - LAN może nie mieć IPv6."))
    db.set_setting(CONN, "lan6", ", ".join(found))
    return redirect(with_msg("/settings", _("Wykryto: {found}. Sprawdź i zapisz.",
                                            found=", ".join(found))))


# --------------------------------------------------------------------------
# integralnosc
# --------------------------------------------------------------------------

@route("GET", "/integrity")
def get_integrity(req: Request) -> Response:
    findings = None
    checked_at = 0
    expected = model.fingerprint(CONN)
    if req.query.get("now") and expected:
        try:
            findings = helperclient.call("drift", {"settings": model.settings_only(CONN),
                                                   "expected": expected}).get("findings", [])
            db.set_setting(CONN, "drift_findings", json.dumps(findings))
            db.set_setting(CONN, "drift_checked_at", str(int(time.time())))
        except helperclient.HelperError as exc:
            return Response(ui.simple(_("Helper niedostępny"), i18n.helper(exc)), 503)
    if findings is None:
        findings = drift_findings()
    checked_at = int(db.settings(CONN).get("drift_checked_at", "0") or 0)
    return Response(ui.integrity_page(req.sess, findings, checked_at, bool(expected),
                                      alerts=alert_count(CONN)))


# --------------------------------------------------------------------------
# weryfikacja kopii
# --------------------------------------------------------------------------

@route("POST", "/backups/verify")
def post_backup_verify(req: Request) -> Response:
    ids = [int(value) for value in req.get_all("id") if value.isdigit()]
    if ids:
        results = export.verify_many(CONN, ids)          # zaznaczone na liscie
    else:
        results = export.verify_recent(CONN, int(req.get("count") or 5))
    bad = [r for r in results if not r["ok"]]
    if not results:
        template, values = "Nie ma kopii do sprawdzenia.", {}
    elif bad:
        template = "{bad} z {total} kopii NIE przeszło weryfikacji - szczegóły w tabeli."
        values = {"bad": len(bad), "total": len(results)}
    else:
        template, values = "Sprawdzono {n} kopii - sumy kontrolne zgodne.", {"n": len(results)}
    message = _(template, **values)
    # do dziennika zawsze polski klucz - tlumaczy sie przy wyswietlaniu, wiec dziennik nie
    # miesza jezykow zaleznie od tego, kto kliknal
    db.log(CONN, req.actor, "backup.verify", i18n.pl(template, **values))
    if bad:
        return redirect_err("/backups", message)
    return redirect("/backups?msg=" + urllib.parse.quote(message))


# --------------------------------------------------------------------------
# tiery, ustawienia, dziennik
# --------------------------------------------------------------------------

@route("GET", "/tiers")
def get_tiers(req: Request) -> Response:
    return Response(ui.tiers_page(req.sess, db.tiers(CONN), req.query.get("msg", ""),
                                  alerts=alert_count(CONN)))


@route("POST", "/tiers/new")
def post_tier_new(req: Request) -> Response:
    try:
        rules = json.loads(req.get("rules_json") or "[]")
        cfg = db.render_cfg(CONN)
        for rule in rules:
            render.normalize_rule(rule, cfg)
    except (json.JSONDecodeError, render.RuleError) as exc:
        return redirect_err("/tiers", _("Złe reguły: {error}", error=exc))
    mode, listing, problem = tier_tunnel(req)
    if problem:
        return redirect_err("/tiers", problem)
    with CONN:
        CONN.execute(
            """INSERT INTO tiers(name, description, client_mode, client_list, host_access,
                                 rules_json) VALUES (?, ?, ?, ?, ?, ?)""",
            (req.get("name").strip(), req.get("description").strip(),
             mode, listing, req.get("host_access").strip(), json.dumps(rules)),
        )
    db.log(CONN, req.actor, "tier.create", req.get("name"))
    return redirect(with_msg("/tiers", _("Tier utworzony.")))


def tier_tunnel(req: Request) -> tuple[str, str, str]:
    mode = req.get("client_mode")
    if mode not in render.TUNNEL_MODES:
        mode = "auto"
    listing = ", ".join(i.strip() for i in req.get("client_list").split(",") if i.strip())
    if mode == "custom":
        cfg = db.render_cfg(CONN)
        try:
            if not listing:
                raise render.RuleError("pusta lista")
            for item in listing.split(","):
                render.resolve_dest(item.strip(), cfg)
        except render.RuleError as exc:
            return mode, listing, f"Lista tunelu: {exc}"
    return mode, listing, ""


@route("POST", r"/tiers/(\d+)/edit")
def post_tier_edit(req: Request, tier_id: str) -> Response:
    mode, listing, problem = tier_tunnel(req)
    if problem:
        return redirect_err("/tiers", problem)
    with CONN:
        CONN.execute(
            """UPDATE tiers SET description=?, client_mode=?, client_list=?, host_access=?
               WHERE id=?""",
            # angielska wersja domyslnego opisu wraca do bazy jako polski klucz
            (i18n.canonical(req.get("description").strip(),
                            [t["description"] for t in db.BUILTIN_TIERS]),
             mode, listing, req.get("host_access").strip(), int(tier_id)),
        )
    db.log(CONN, req.actor, "tier.edit", tier_id)
    return redirect(with_msg("/tiers", _("Tier zapisany.")) + f"#tier-{tier_id}")


@route("POST", r"/tiers/(\d+)/rules")
def post_tier_rule_add(req: Request, tier_id: str) -> Response:
    tier = CONN.execute("SELECT * FROM tiers WHERE id = ?", (int(tier_id),)).fetchone()
    if not tier:
        return redirect("/tiers")
    raw = {
        "action": req.get("action"),
        "dest": req.get("dest").strip() or "ANY",
        "proto": req.get("proto"),
        "ports": req.get("ports").strip(),
        "comment": req.get("comment").strip(),
    }
    try:
        render.normalize_rule(raw, db.render_cfg(CONN))
    except render.RuleError as exc:
        return redirect_err("/tiers", _("Reguła: {error}", error=exc))
    rules = json.loads(tier["rules_json"] or "[]")
    rules.append(raw)
    with CONN:
        CONN.execute("UPDATE tiers SET rules_json = ? WHERE id = ?",
                     (json.dumps(rules), int(tier_id)))
    db.log(CONN, req.actor, "tier.rule_add", f'{tier["name"]}: {raw["action"]} {raw["dest"]}')
    return redirect(f"/tiers?msg=" + urllib.parse.quote(
        _("Dodano regułę. Zastosuj zmiany, żeby zadziałała.")) + f"#tier-{tier_id}")


@route("POST", r"/tiers/(\d+)/rules/(\d+)/delete")
def post_tier_rule_delete(req: Request, tier_id: str, index: str) -> Response:
    tier = CONN.execute("SELECT * FROM tiers WHERE id = ?", (int(tier_id),)).fetchone()
    if not tier:
        return redirect("/tiers")
    rules = json.loads(tier["rules_json"] or "[]")
    position = int(index)
    if 0 <= position < len(rules):
        removed = rules.pop(position)
        with CONN:
            CONN.execute("UPDATE tiers SET rules_json = ? WHERE id = ?",
                         (json.dumps(rules), int(tier_id)))
        db.log(CONN, req.actor, "tier.rule_delete",
               f'{tier["name"]}: {removed.get("action")} {removed.get("dest")}')
    return redirect(f"/tiers?msg=" + urllib.parse.quote(
        _("Usunięto regułę. Zastosuj zmiany.")) + f"#tier-{tier_id}")


@route("POST", r"/tiers/(\d+)/delete")
def post_tier_delete(req: Request, tier_id: str) -> Response:
    tier = CONN.execute("SELECT * FROM tiers WHERE id = ?", (int(tier_id),)).fetchone()
    if not tier:
        return redirect("/tiers")
    used = CONN.execute("SELECT COUNT(*) AS n FROM peers WHERE tier_id = ?",
                        (int(tier_id),)).fetchone()["n"]
    # wbudowane tiery tez mozna usunac - panel odtworzy je tylko na pustej bazie
    if used:
        return redirect_err(
            "/tiers", _("Nie usunę tieru {tier} - jest przypisany do {n} osób. Najpierw przenieś "
                        "je do innego tieru.", tier=tier["name"], n=used))
    with CONN:
        CONN.execute("DELETE FROM tiers WHERE id = ?", (int(tier_id),))
    db.log(CONN, req.actor, "tier.delete", tier["name"])
    return redirect(with_msg("/tiers", _("Usunięto tier {tier}.", tier=tier["name"])))


# --------------------------------------------------------------------------
# dane do odswiezania listy peerow
# --------------------------------------------------------------------------

@route("GET", r"/lang/(pl|en)", login_required=False)
def get_lang(req: Request, code: str) -> Response:
    """Wybor jezyka zapamietuje przegladarka - strony sklada serwer, wiec potrzebne ciasteczko."""
    back = safe_next(req.query.get("next", "/"))
    cookie = (f"{i18n.COOKIE}={code}; Path=/; Max-Age=31536000; SameSite=Lax"
              + ("; Secure" if req.handler.is_tls else ""))
    return redirect(back, [("Set-Cookie", cookie)])


@route("GET", "/tls", login_required=False)
def get_tls(req: Request) -> Response:
    return Response(ui.tls_page(req.sess, tls_details(), req.handler.is_tls))


@route("GET", "/tls/ca.crt", login_required=False)
def get_tls_ca(req: Request) -> Response:
    """Certyfikat urzedu - informacja publiczna; zgodnosc sprawdza sie odciskiem SHA-256."""
    try:
        with open(os.path.join(TLS_DIR, "ca.crt"), "rb") as handle:
            body = handle.read()
    except OSError:
        return Response(_("Brak certyfikatu - HTTPS nie jest włączone."), 404)
    return Response(body, content_type="application/x-x509-ca-cert",
                    headers=[("Content-Disposition", 'attachment; filename="wg-panel-ca.crt"')])


@route("POST", "/tls/settings")
def post_tls_settings(req: Request) -> Response:
    mode = req.get("tls_mode")
    if mode not in ("self", "custom"):
        return redirect_err("/settings", _("Nieznany tryb certyfikatu."))
    cert_path, key_path = req.get("tls_cert_path").strip(), req.get("tls_key_path").strip()
    panel_url = req.get("panel_url").strip().rstrip("/")
    if panel_url and not re.match(r"https?://[^\s/]+$", panel_url):
        return redirect_err("/settings", _("Adres panelu: np. https://vpn.example.com (bez ścieżki)."))
    proxies, problem = parse_proxies(req.get("proxy_trusted"))
    if problem:
        return redirect_err("/settings", problem)
    if mode == "custom":
        if not cert_path or not key_path:
            return redirect_err("/settings", _("Podaj ścieżkę do certyfikatu i do klucza."))
        try:
            helperclient.call("tls", {"mode": "custom", "cert": cert_path, "key": key_path},
                              timeout=60)
        except helperclient.HelperError as exc:
            return redirect_err("/settings", _("Certyfikat: {error}", error=i18n.helper(exc)))
    for name, value in (("tls_mode", mode), ("tls_cert_path", cert_path), ("tls_key_path", key_path),
                        ("panel_url", panel_url),
                        ("proxy_trusted", ", ".join(str(net) for net in proxies))):
        db.set_setting(CONN, name, value)
    TLS["proxies"] = proxies
    db.log(CONN, req.actor, "tls.settings", mode + (" + proxy" if proxies else ""))
    tls_refresh()
    if TLS["error"]:
        return redirect_err("/settings", _("Certyfikat: {error}", error=i18n.helper(TLS["error"])))
    return redirect(with_msg("/settings", _("Zapisano.")))


def parse_proxies(text: str) -> tuple:
    """Zaufane reverse proxy (adresy albo podsieci) - tylko od nich naglowki X-Forwarded-*."""
    nets = []
    for item in re.split(r"[\s,]+", text or ""):
        if not item:
            continue
        try:
            net = ipaddress.ip_network(item, strict=False)
        except ValueError:
            return [], _("Adres proxy: {item} to nie jest adres IP ani podsieć.", item=item)
        if net.prefixlen == 0:
            return [], _("Adres proxy: {item} oznaczałby zaufanie do każdego - podaj adres serwera "
                         "proxy.", item=item)
        nets.append(net)
    return nets, ""


def from_proxy(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return any(ip.version == net.version and ip in net for net in TLS["proxies"])


@route("POST", "/tls/renew")
def post_tls_renew(req: Request) -> Response:
    tls_refresh(renew=True)
    if TLS["error"]:
        return redirect_err("/settings", _("Certyfikat: {error}", error=i18n.helper(TLS["error"])))
    db.log(CONN, req.actor, "tls.renew")
    return redirect("/settings?msg=" + urllib.parse.quote("Certyfikat panelu odnowiony."))


@route("GET", "/api/pending")
def get_api_pending(req: Request) -> Response:
    """Pasek oczekiwania pyta tu co kilka sekund - tylko wtedy, gdy jest widoczny."""
    state = request_pending()
    pending = state["pending"]
    payload = {"pending": bool(pending),
               "remaining": pending.get("remaining") if pending else None,
               "notice": state["notice"], "notice_kind": state["notice_kind"]}
    return Response(json.dumps(payload), content_type="application/json; charset=utf-8")


@route("GET", "/api/peers")
def get_api_peers(req: Request) -> Response:
    """Lekki endpoint dla strony peerow - odswieza sie bez przeladowania strony."""
    status, error = safe_status(CONN)
    seen = stats.view(CONN, status)     # zapamietane - liczby nie zeruja sie po restarcie Pi
    rows = []
    for peer in db.peers(CONN):
        known = seen.get(peer["id"], {})
        rows.append({
            "id": peer["id"],
            "state": peer["state"],
            "handshake": known.get("handshake", 0),
            "rx": known.get("rx", 0),
            "tx": known.get("tx", 0),
        })
    payload = {
        "now": int(time.time()),
        "up": bool((status or {}).get("up")),
        "service": (status or {}).get("service", "?"),
        "active": sum(1 for r in rows if r["state"] == "active"),
        "error": error,
        "peers": rows,
    }
    return Response(json.dumps(payload), content_type="application/json; charset=utf-8")


def chart_json(series: list, title: str) -> Response:
    """Wnetrze karty wykresu dla strony, ktora otwarto, zanim byly dane (ui.wgChartWatch)."""
    ready = ui.chart_ready(series)
    payload = {"ready": ready, "html": ui.chart_body(series, title, db.settings(CONN)) if ready else ""}
    return Response(json.dumps(payload), content_type="application/json; charset=utf-8")


@route("GET", "/api/chart")
def get_api_chart(req: Request) -> Response:
    return chart_json(traffic.hourly(CONN, None, 7), _("Ruch w tunelu - ostatnie 7 dni"))


@route("GET", r"/api/peers/(\d+)/chart")
def get_api_peer_chart(req: Request, peer_id: str) -> Response:
    peer = db.peer(CONN, int(peer_id))
    if not peer:
        return Response(json.dumps({"ready": False, "html": ""}), 404,
                        content_type="application/json; charset=utf-8")
    return chart_json(traffic.hourly(CONN, peer["pubkey"], 7), _("Ruch - ostatnie 7 dni"))


def refresh_server_pubkey() -> str:
    """Klucz publiczny serwera prosto z helpera (z klucza prywatnego na tym Pi).

    Panel wpisuje go do kazdego pliku klienta, wiec zapamietana kopia musi sie zgadzac
    z tym, co naprawde jest na dysku - takze po nowym systemie z nowym kluczem."""
    try:
        pubkey = helperclient.call(
            "server_pubkey", {"settings": model.settings_only(CONN)}).get("pubkey", "")
    except helperclient.HelperError:
        return ""
    if pubkey and pubkey != db.settings(CONN).get("server_pubkey"):
        db.set_setting(CONN, "server_pubkey", pubkey)
    return pubkey


@route("GET", "/settings")
def get_settings(req: Request) -> Response:
    cfg = db.settings(CONN)
    pubkey = refresh_server_pubkey() or cfg.get("server_pubkey", "")
    row = CONN.execute("SELECT totp_secret FROM users WHERE id = ?",
                       (req.sess["user_id"],)).fetchone()
    return Response(ui.settings_page(req.sess, cfg, pubkey, req.query.get("msg", ""),
                                     alerts=alert_count(CONN),
                                     has_totp=bool(row and row["totp_secret"]),
                                     tls=tls_details()))


@route("POST", "/account/totp")
def post_account_totp(req: Request) -> Response:
    user_id = req.sess["user_id"]
    if not auth.check_password(CONN, user_id, req.get("password")):
        return redirect_err("/settings", _("Złe obecne hasło."))
    if req.get("enable") == "1":
        secret = wgcrypto.totp_secret()
        auth.set_totp(CONN, user_id, secret)
        db.log(CONN, req.actor, "account.totp_on")
        uri = wgcrypto.totp_uri(secret, req.sess["username"])
        return Response(ui.totp_enrolled(secret, uri, qr.svg(uri)))
    auth.set_totp(CONN, user_id, None)
    db.log(CONN, req.actor, "account.totp_off")
    return redirect(with_msg("/settings", _("Kod z aplikacji wyłączony - logujesz się samym hasłem.")))


@route("POST", "/account/password")
def post_account_password(req: Request) -> Response:
    user_id = req.sess["user_id"]
    if not auth.check_password(CONN, user_id, req.get("password")):
        return redirect_err("/settings", _("Złe obecne hasło."))
    new = req.get("new1")
    if len(new) < 10:
        return redirect_err("/settings", _("Nowe hasło musi mieć co najmniej 10 znaków."))
    if new != req.get("new2"):
        return redirect_err("/settings", _("Nowe hasła się różnią."))
    auth.set_password(CONN, user_id, new)
    db.log(CONN, req.actor, "account.password")
    return redirect("/login?error=" + urllib.parse.quote(
        _("Hasło zmienione. Zaloguj się nowym.")),
        [("Set-Cookie", f"{auth.COOKIE}=; Max-Age=0; Path=/")])


@route("POST", "/settings")
def post_settings(req: Request) -> Response:
    allowed = (set(db.DEFAULT_SETTINGS) | {"managed_host_ports"}) - ui.READONLY_SETTINGS
    for key in ("audit_days", "alerts_days"):
        value = req.get(key).strip() if key in req.form else ""
        if key in req.form and not (value.isdigit() and int(value) <= db.RETENTION_MAX):
            return redirect_err("/settings", _(
                "Liczba dni dziennika i alertów to liczba całkowita od 0 do {max} "
                "(0 = bez limitu).", max=db.RETENTION_MAX))
    for key in allowed:
        if key in req.form:
            db.set_setting(CONN, key, req.get(key).strip())
    if db.settings(CONN).get("confirm_mode") not in ("auto", "manual"):
        db.set_setting(CONN, "confirm_mode", "auto")
    db.log(CONN, req.actor, "settings.update")
    return redirect(with_msg("/settings", _("Zapisano. Zastosuj zmiany, żeby trafiły na interfejs.")))


@route("GET", "/audit")
def get_audit(req: Request) -> Response:
    newest = req.query.get("order", "desc") != "asc"
    days = db.retention_days(db.settings(CONN), "audit_days")
    number = page_number(req)
    rows = db.audit_tail(CONN, AUDIT_PER_PAGE, newest, (number - 1) * AUDIT_PER_PAGE)
    return Response(ui.audit_page(req.sess, rows, newest, days, alerts=alert_count(CONN),
                                  paging=(number, AUDIT_PER_PAGE, db.audit_count(CONN))))


@route("GET", "/static/style.css", login_required=False)
def get_style(req: Request) -> Response:
    # adres z odciskiem pliku (?v=...) - przegladarka trzyma styl, dopoki panel go nie zmieni
    cache = ("public, max-age=31536000, immutable" if req.query.get("v") == ui.style_version()
             else "max-age=300")
    with open(os.path.join(STATIC_DIR, "style.css"), "rb") as handle:
        return Response(handle.read(), content_type="text/css; charset=utf-8",
                        headers=[("Cache-Control", cache)])


# --------------------------------------------------------------------------
# serwer HTTP
# --------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "wg-panel"
    protocol_version = "HTTP/1.1"

    def setup(self):
        super().setup()
        # automatyczne potwierdzenie przyjmujemy tylko z polaczenia nawiazanego po zmianie
        self.connected_at = time.time()

    @property
    def is_tls(self) -> bool:
        if isinstance(self.connection, ssl.SSLSocket):
            return True
        # zaufane reverse proxy (np. Caddy na innym serwerze) moze konczyc HTTPS u siebie
        return (from_proxy(self.client_address[0])
                and (self.headers.get("X-Forwarded-Proto") or "").lower() == "https")

    def log_message(self, fmt, *args):
        # czas obslugi w logu - bez tego nie da sie ustalic, co spowalnia panel
        took = ""
        started = getattr(self, "_started", None)
        if started:
            took = f" {(time.time() - started) * 1000:.0f} ms"
        sys.stderr.write(f"{self.client_address[0]} {fmt % args}{took}\n")

    def _cookie(self, name: str) -> str:
        raw = self.headers.get("Cookie")
        if not raw:
            return ""
        cookie = http.cookies.SimpleCookie()
        try:
            cookie.load(raw)
        except http.cookies.CookieError:
            return ""
        return cookie[name].value if name in cookie else ""

    def _session(self):
        raw = self.headers.get("Cookie")
        if not raw:
            return None
        cookie = http.cookies.SimpleCookie()
        try:
            cookie.load(raw)
        except http.cookies.CookieError:
            return None
        morsel = cookie.get(auth.COOKIE)
        return auth.session(CONN, morsel.value) if morsel else None

    def _form(self) -> dict:
        # zachowujemy wszystkie wartosci pola - checkboxy przysylaja je wielokrotnie
        length = int(self.headers.get("Content-Length") or 0)
        if not 0 < length <= 1024 * 1024:
            return {}
        raw = self.rfile.read(length).decode("utf8", "replace")
        return urllib.parse.parse_qs(raw, keep_blank_values=True)

    def _dispatch(self, method: str):
        self._started = time.time()
        parsed = urllib.parse.urlparse(self.path)
        query = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        form = self._form() if method == "POST" else {}
        chosen = self._cookie(i18n.COOKIE)
        if chosen is None:
            # bez wyboru w ciasteczku: jezyk przegladarki (pl -> polski, reszta -> angielski);
            # klient bez naglowka Accept-Language dostaje domyslny
            header = (self.headers.get("Accept-Language") or "").strip().lower()
            if header:
                chosen = "pl" if header.startswith("pl") else "en"
        i18n.set_lang(chosen)
        sess = self._session()

        for route_method, pattern, name, login_required in ROUTES:
            if route_method != method:
                continue
            match = pattern.match(parsed.path)
            if not match:
                continue
            if login_required and not sess:
                return self._send(redirect("/login"))
            token = form.get("csrf", "")
            if isinstance(token, list):
                token = token[0] if token else ""
            if method == "POST" and sess and not auth.check_csrf(sess, token):
                return self._send(Response(ui.simple(
                    _("Błąd CSRF"), _("Odśwież stronę i spróbuj jeszcze raz.")), 403))
            request = Request(self, method, parsed.path, query, form, sess)
            names.load(db.settings(CONN))     # nazwy urzadzen do tekstow tej strony
            ui.begin_request(path=self.path, err=query.get("err", "") if method == "GET" else "",
                             pending=request_pending if sess else None)
            try:
                return self._send(globals()[name](request, *match.groups()))
            except helperclient.HelperError as exc:
                return self._send(Response(ui.simple(_("Helper niedostępny"), str(exc)), 503))
            except Exception as exc:  # noqa: BLE001
                sys.stderr.write(f"blad obslugi {parsed.path}: {exc!r}\n")
                return self._send(Response(ui.simple(
                    _("Błąd serwera"), _("Szczegóły w journalctl -u wg-panel.")), 500))
        self._send(Response(ui.simple(_("Nie znaleziono"), _("Nie ma takiej strony.")), 404))

    def _send(self, response: Response) -> None:
        body = response.body
        packed = self._gzip_ok(response)
        if packed:
            body = gzip.compress(body, 6)
        self.send_response(response.status)
        self.send_header("Content-Type", response.content_type)
        self.send_header("Content-Length", str(len(body)))
        if packed:
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Vary", "Accept-Encoding")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        for key, value in response.headers:
            self.send_header(key, value)
        if self._change_pending():
            # kazde kolejne zapytanie przejdzie przez nowe reguly jako NOWE polaczenie
            self.send_header("Connection", "close")
        self.end_headers()
        took = time.time() - getattr(self, "_started", time.time())
        if took > 0.5:      # tylko wolne zadania - inaczej log zalewa dziennik
            sys.stderr.write(f"wolne zadanie: {self.path} {took:.2f}s\n")
        if body:
            self.wfile.write(body)

    def _gzip_ok(self, response: Response) -> bool:
        """Kompresja stron i stylu - przez VPN na telefonie strona laduje sie kilka razy mniej
        danych. Bez kompresji, gdy adres niesie tekst odbijany na stronie (msg, err, symulator):
        strona ma tez token CSRF, a kompresja tekstu wpisanego przez kogos obok sekretu to
        znany atak (BREACH)."""
        if len(response.body) < 1024 or "gzip" not in (self.headers.get("Accept-Encoding") or ""):
            return False
        if not response.content_type.startswith(("text/", "application/json", "image/svg+xml")):
            return False
        if any(key.lower() == "content-encoding" for key, _value in response.headers):
            return False
        return "?" not in self.path

    @staticmethod
    def _change_pending() -> bool:
        try:
            return bool(CONN is not None and db.settings(CONN).get("confirm_token"))
        except Exception:  # noqa: BLE001
            return False

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")


class RedirectToHttps(BaseHTTPRequestHandler):
    """Zwykle HTTP z sieci na porcie z HTTPS: przekierowanie, zeby stare zakladki dzialaly."""
    server_version = "wg-panel"

    def _redirect(self):
        host = (self.headers.get("Host") or f"{BIND_HOST}:{BIND_PORT}").strip()
        if not re.fullmatch(r"[A-Za-z0-9.\-:\[\]]{1,255}", host):
            host = f"{BIND_HOST}:{BIND_PORT}"
        path = self.path if self.path.startswith("/") else "/"
        self.send_response(301)
        self.send_header("Location", f"https://{host}{path}")
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.end_headers()

    do_GET = do_POST = do_HEAD = do_PUT = do_DELETE = _redirect

    def log_message(self, fmt, *args):
        pass


class Server(ThreadingHTTPServer):
    """HTTPS i HTTP na jednym porcie - po pierwszym bajcie polaczenia.

    Uscisk TLS zaczyna sie bajtem 0x16. Zwykle HTTP z sieci dostaje przekierowanie na
    HTTPS; z 127.0.0.1 (tunel SSH) zostaje HTTP - ten ruch nie opuszcza Pi.
    """
    daemon_threads = True
    allow_reuse_address = True

    def finish_request(self, request, client_address):
        ctx = TLS["ctx"]
        if ctx is None:
            return super().finish_request(request, client_address)
        try:
            request.settimeout(15)
            first = request.recv(1, socket.MSG_PEEK)
        except OSError:
            return None
        if not first:
            return None
        if first[0] != 0x16:
            request.settimeout(None)
            if ipaddress.ip_address(client_address[0]).is_loopback:
                return super().finish_request(request, client_address)
            return RedirectToHttps(request, client_address, self)
        try:
            secure = ctx.wrap_socket(request, server_side=True)
        except (ssl.SSLError, OSError):
            return None        # np. przegladarka odrzucila certyfikat - nic do obslugi
        secure.settimeout(None)
        try:
            self.RequestHandlerClass(secure, client_address, self)
        finally:
            try:
                secure.close()
            except OSError:
                pass
        return None


def client_ip(handler) -> str:
    """Adres klienta. Przez reverse proxy wszystko przychodzi z adresu proxy - wtedy prawdziwy
    adres podaje proxy (X-Forwarded-For); bez tego blokada po nieudanych logowaniach dotyczylaby
    wszystkich naraz. Naglowkowi wierzymy wylacznie od zaufanego proxy."""
    address = handler.client_address[0]
    if from_proxy(address):
        # ostatni adres dopisal nasz nginx; wczesniejsze moze podac sam klient
        forwarded = (handler.headers.get("X-Forwarded-For") or "").split(",")[-1].strip()
        try:
            return str(ipaddress.ip_address(forwarded))
        except ValueError:
            pass
    return address


def address_check() -> list:
    """Adres serwera w sieci domowej idzie za maszyna: zmiana na routerze (np. .10 -> .99)
    przenosi sie na ustawienia i host w katalogu, certyfikat HTTPS dostaje nowy adres od razu,
    a alert mowi, co sprawdzic na routerze."""
    from panel import firstrun
    try:
        changes = firstrun.follow(CONN)
    except Exception as exc:  # noqa: BLE001 - wykrywanie nie moze zatrzymac panelu
        sys.stderr.write(f"wg-panel: nie udalo sie sprawdzic adresu serwera: {exc}\n")
        return []
    for key, old, new in changes:
        db.log(CONN, "panel", "address.change", f"{old or '-'} -> {new}")
        if old:      # pierwszy wpis (pusty) to nie zmiana
            notify.raise_alert(CONN, "address", "adres serwera",
                               i18n.pl(i18n.ALERTS["address"], old=old, new=new), severity="warn")
        lan = db.settings(CONN).get("lan", "")
        if lan and not firstrun.in_network(new, lan):
            notify.raise_alert(CONN, "address", "adres serwera poza siecia",
                               i18n.pl(i18n.ALERTS["address_out"], new=new, lan=lan), severity="warn")
    if changes:
        tls_refresh()
    return changes


def address_loop() -> None:
    while True:
        time.sleep(60)
        address_check()


def tls_refresh(renew: bool = False) -> None:
    """Certyfikat panelu: wlasny urzad (domyslnie) albo wlasne pliki (np. Let's Encrypt).
    Panel laduje certyfikat bez restartu."""
    if not TLS_ON:
        return
    cfg = db.settings(CONN)
    TLS["mode"] = cfg.get("tls_mode") if cfg.get("tls_mode") in ("self", "custom") else "self"
    # podpowiedz "zainstaluj certyfikat" przy logowaniu - tylko przy wlasnym urzedzie
    ui.TLS_HELP = TLS_ON and TLS["mode"] == "self"
    payload = {"settings": model.settings_only(CONN), "renew": renew}
    if TLS["mode"] == "custom":
        payload.update(mode="custom", cert=cfg.get("tls_cert_path", ""),
                       key=cfg.get("tls_key_path", ""))
    try:
        TLS["info"] = helperclient.call("tls", payload, timeout=120)
        TLS["error"] = ""
    except helperclient.HelperError as exc:
        TLS["error"] = str(exc)      # panel dziala dalej na certyfikacie, ktory juz ma
        notify.raise_alert(CONN, "tls", "certyfikat HTTPS",
                           i18n.pl(i18n.ALERTS["tls"], error=exc),
                           severity="warn")
    crt, key = os.path.join(TLS_DIR, "panel.crt"), os.path.join(TLS_DIR, "panel.key")
    try:
        mtime = os.path.getmtime(crt)
    except OSError:
        return
    if TLS["ctx"] is not None and mtime == TLS["mtime"]:
        return
    try:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(crt, key)
    except (OSError, ssl.SSLError) as exc:
        TLS["error"] = _("nie mogę wczytać certyfikatu: {error}", error=exc)
        return
    TLS["ctx"], TLS["mtime"] = ctx, mtime


def tls_details() -> dict:
    """Stan HTTPS do strony /tls i ustawien - z pliku, zeby dzialalo tez bez helpera."""
    info = dict(TLS["info"])
    if not info:
        try:
            with open(os.path.join(TLS_DIR, "panel.json"), encoding="utf8") as handle:
                info = json.load(handle)
        except (OSError, ValueError):
            info = {}
    if not info.get("ca_fingerprint"):
        try:
            with open(os.path.join(TLS_DIR, "ca.crt"), encoding="ascii") as handle:
                digest = hashlib.sha256(ssl.PEM_cert_to_DER_cert(handle.read())).hexdigest().upper()
            info["ca_fingerprint"] = ":".join(digest[i:i + 2] for i in range(0, 64, 2))
        except (OSError, ValueError):
            pass
    return {"on": TLS_ON and TLS["ctx"] is not None, "enabled": TLS_ON, "error": TLS["error"],
            **info, "mode": TLS["mode"]}


def main() -> int:
    global CONN
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    CONN = db.connect(DB_PATH)
    db.init_defaults(CONN)
    # grupa wg-panel - zeby wgctl dzialal bez sudo; pliki -wal i -shm tez,
    # bo bez prawa zapisu do nich SQLite uznaje cala baze za tylko do odczytu
    for path in (DB_PATH, DB_PATH + "-wal", DB_PATH + "-shm"):
        try:
            os.chmod(path, 0o660)
        except OSError:
            pass

    def drift_now():
        expected = model.fingerprint(CONN)
        if not expected:
            return None  # nic jeszcze nie zastosowano - nie ma z czym porownywac
        try:
            return helperclient.call("drift", {"settings": model.settings_only(CONN),
                                               "expected": expected}).get("findings", [])
        except helperclient.HelperError:
            return None

    def live_peers():
        try:
            return helperclient.call(
                "peers", {"wg_iface": db.settings(CONN).get("wg_iface", "wg0")},
                timeout=10).get("peers", {})
        except helperclient.HelperError:
            return None

    worker = scheduler.Scheduler(
        CONN, lambda: safe_status(CONN),
        log=lambda message: sys.stderr.write(message + "\n"),
        peers_fn=live_peers,
    )
    refresh_server_pubkey()        # przy kazdym starcie - np. po odtworzeniu kopii
    worker.drift_fn = drift_now
    worker.dirty_fn = lambda: model.is_dirty(CONN, None, safe_status(CONN, 30)[0])
    worker.start()

    ui.TLS_PORT = BIND_PORT
    TLS["proxies"] = parse_proxies(db.settings(CONN).get("proxy_trusted", ""))[0]
    tls_refresh()
    address_check()
    threading.Thread(target=address_loop, daemon=True).start()
    if TLS_ON:
        def tls_loop():
            while True:
                time.sleep(12 * 3600)
                tls_refresh()
        threading.Thread(target=tls_loop, daemon=True).start()

    servers = start_listeners()
    zwolnione = db.shrink_runtime_settings(CONN)
    if zwolnione:
        sys.stderr.write(f"wg-panel: usunieto rozdmuchana migawke stanu ({zwolnione // 1024} KB) "
                         f"- powstanie na nowo przy najblizszym zastosowaniu\n")
    if not auth.has_users(CONN):
        sys.stderr.write("wg-panel: brak kont - otworz /setup i zaloz administratora\n")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
    return 0


def start_listeners() -> list:
    """WGPANEL_HOST moze byc lista adresow po przecinku albo 0.0.0.0 (wszystkie interfejsy).

    127.0.0.1 jest zawsze - wejscie przez tunel SSH dziala nawet bez VPN.
    Adresu, ktorego jeszcze nie ma (10.0.0.1 przed podniesieniem wg0), panel pilnuje
    w tle i podlacza sie do niego, gdy tylko sie pojawi.
    Dostep ogranicza zapora, nie sam nasluch - regule zaklada instalator.
    """
    servers: list = []
    hosts = [h.strip() for h in BIND_HOST.split(",") if h.strip()]
    if "0.0.0.0" in hosts or "::" in hosts:
        hosts = ["0.0.0.0"]          # jedno gniazdo obejmuje wszystkie adresy
    elif "127.0.0.1" not in hosts:
        hosts.insert(0, "127.0.0.1")

    def serve(host: str) -> bool:
        try:
            server = Server((host, BIND_PORT), Handler)
        except OSError:
            return False
        servers.append(server)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        scheme = "https" if TLS["ctx"] is not None else "http"
        sys.stderr.write(f"wg-panel: {scheme}://{host}:{BIND_PORT}\n")
        return True

    if not serve(hosts[0]):
        raise SystemExit(f"wg-panel: nie moge nasluchiwac na {hosts[0]}:{BIND_PORT} "
                         f"- port zajety albo adres nie istnieje")
    for host in hosts[1:]:
        if serve(host):
            continue
        sys.stderr.write(f"wg-panel: {host} jeszcze nie istnieje (wg0 nie dziala) - czekam; "
                         f"do tego czasu panel jest pod {hosts[0]}\n")

        def wait_for(address: str = host) -> None:
            while not serve(address):
                time.sleep(10)

        threading.Thread(target=wait_for, daemon=True).start()
    return servers


if __name__ == "__main__":
    raise SystemExit(main())
