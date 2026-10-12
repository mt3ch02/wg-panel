"""Alerty: zapis do bazy i wyslanie na ntfy albo wlasny webhook.

Zdarzenia, ktore wykrywamy:
  new_endpoint - ten sam klucz laczy sie z nowego adresu; pierwszy sygnal,
                 ze plik konfiguracyjny wyciekl
  two_devices  - ten sam plik dziala na dwoch urzadzeniach naraz (panel/dual.py)
  dead_peer    - brak handshake'u dluzej niz ustawiony prog
  expired      - peer stracil waznosc i zostal zawieszony
  backup       - kopia zapasowa sie nie powiodla
  apply        - zmiana zastosowana albo cofnieta bezpiecznikiem
"""
from __future__ import annotations

import json
import os
import smtplib
import sqlite3
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage

from . import db, i18n, stats


def raise_alert(conn: sqlite3.Connection, kind: str, subject: str, message: str,
                severity: str = "warn") -> None:
    cfg = db.settings(conn)
    quiet = int(cfg.get("alerts_quiet_minutes", "30") or 0) * 60
    if quiet:
        recent = conn.execute(
            "SELECT ts FROM alerts WHERE kind = ? AND subject = ? ORDER BY id DESC LIMIT 1",
            (kind, subject),
        ).fetchone()
        if recent and time.time() - recent["ts"] < quiet:
            return  # to samo zdarzenie juz zglaszalismy przed chwila

    with conn:
        cursor = conn.execute(
            """INSERT INTO alerts(ts, kind, subject, message, severity, sent)
               VALUES (?, ?, ?, ?, ?, 0)""",
            (int(time.time()), kind, subject, message, severity),
        )
    alert_id = cursor.lastrowid

    if send(cfg, subject, message, severity, kind):
        with conn:
            conn.execute("UPDATE alerts SET sent = 1 WHERE id = ?", (alert_id,))


# najpowazniejsze zdarzenia: telefon ma zadzwonic mimo wyciszenia zwyklych powiadomien
CRITICAL_KINDS = {"new_endpoint", "two_devices", "tamper", "watchdog_wg"}
PRIORITY = {"critical": 5, "alarm": 4, "warn": 3}
TAGS = {"critical": ["rotating_light"], "alarm": ["rotating_light"], "warn": ["warning"]}


def panel_link(cfg: dict, page: str = "/alerts") -> str:
    """Adres panelu przez VPN - klikniecie w powiadomienie otwiera strone alertow."""
    public = (cfg.get("panel_url") or "").rstrip("/")
    if public:
        return public + page         # np. za nginx: adres, pod ktorym panel widac z zewnatrz
    scheme = "https" if os.environ.get("WGPANEL_TLS") == "1" else "http"
    port = os.environ.get("WGPANEL_PORT") or cfg.get("panel_port") or "8080"
    return f"{scheme}://{cfg.get('server_ip') or '10.0.0.1'}:{port}{page}"


def ntfy_target(url: str) -> tuple:
    """https://ntfy.sh/temat -> ("https://ntfy.sh/", "temat")."""
    parsed = urllib.parse.urlsplit(url)
    base, _sep, topic = parsed.path.rstrip("/").rpartition("/")
    return f"{parsed.scheme}://{parsed.netloc}{base}/", topic


def weak_topic(url: str) -> bool:
    """Temat na publicznym ntfy.sh czyta kazdy, kto zna jego nazwe."""
    base, topic = ntfy_target(url)
    public = urllib.parse.urlsplit(base).netloc.lower() in ("ntfy.sh", "www.ntfy.sh")
    return public and (len(topic) < 12 or topic.isalpha())


def send(cfg: dict, subject: str, message: str, severity: str = "warn", kind: str = "") -> bool:
    """Glowny kanal (ntfy albo webhook) i - jesli wlaczony - e-mail, niezaleznie od siebie."""
    # "znowu dziala" / "ustalo" tego samego rodzaju to informacja - nie budzi telefonu
    level = "critical" if kind in CRITICAL_KINDS and severity != "info" else severity
    sent = _send_channel(cfg, subject, message, level)
    if cfg.get("alerts_email") == "1":
        sent = send_email(cfg, subject, message) or sent
    return sent


def send_email(cfg: dict, subject: str, message: str) -> bool:
    """Opcjonalny e-mail przez zwykla skrzynke (SMTP z logowaniem)."""
    host = (cfg.get("smtp_host") or "").strip()
    recipients = [a.strip() for a in (cfg.get("smtp_to") or "").split(",") if a.strip()]
    if not host or not recipients:
        return False
    mail = EmailMessage()
    mail["Subject"] = f"[wg-panel] {subject}"
    mail["From"] = (cfg.get("smtp_from") or cfg.get("smtp_user") or "wg-panel").strip()
    mail["To"] = ", ".join(recipients)
    mail.set_content(f"{message}\n\n{panel_link(cfg)}\n")
    security = cfg.get("smtp_security", "starttls")
    try:
        port = int(cfg.get("smtp_port") or (465 if security == "ssl" else 587))
        if security == "ssl":
            server = smtplib.SMTP_SSL(host, port, timeout=15, context=ssl.create_default_context())
        else:
            server = smtplib.SMTP(host, port, timeout=15)
            if security == "starttls":
                server.starttls(context=ssl.create_default_context())
        with server:
            if cfg.get("smtp_user"):
                server.login(cfg["smtp_user"], cfg.get("smtp_pass", ""))
            server.send_message(mail)
        return True
    except (smtplib.SMTPException, OSError, ValueError):
        return False


def _send_channel(cfg: dict, subject: str, message: str, level: str) -> bool:
    channel = cfg.get("alerts_channel", "none")
    url = (cfg.get("alerts_url") or "").strip()
    if channel == "none" or not url:
        return False
    if not url.startswith(("http://", "https://")):
        return False

    try:
        if channel == "ntfy":
            # JSON zamiast naglowkow HTTP: naglowki sa w latin-1, wiec polskie znaki
            # w tytule przychodzily jako krzaki ("Łukasz" -> "Åukasz")
            base, topic = ntfy_target(url)
            if not topic:
                return False
            payload = {"topic": topic, "title": subject, "message": message,
                       "priority": PRIORITY.get(level, 2),
                       "tags": TAGS.get(level, ["information_source"]),
                       "click": panel_link(cfg)}
            request = urllib.request.Request(base, data=json.dumps(payload).encode("utf8"),
                                             method="POST")
            request.add_header("Content-Type", "application/json")
            token = (cfg.get("alerts_token") or "").strip()
            if token:
                request.add_header("Authorization", f"Bearer {token}")
        else:
            payload = json.dumps({
                "subject": subject, "message": message,
                "severity": level, "source": "wg-panel",
                "ts": int(time.time()),
            }).encode("utf8")
            request = urllib.request.Request(url, data=payload, method="POST")
            request.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(request, timeout=10) as response:
            return 200 <= response.status < 300
    except (urllib.error.URLError, OSError, ValueError):
        return False


def check_peers(conn: sqlite3.Connection, status: dict, skip_endpoint=frozenset()) -> None:
    """Porownuje biezacy stan z ostatnio widzianym i zglasza roznice.

    skip_endpoint: klucze, ktore przeskakuja miedzy dwoma urzadzeniami (panel/dual.py) - dla
    nich zamiast alertu o nowym adresie przychodzi jeden, czytelniejszy alert two_devices."""
    cfg = db.settings(conn)
    live = (status or {}).get("peers", {})
    if not live:
        return

    now = int(time.time())
    dead_after = int(cfg.get("alert_dead_hours", "0") or 0) * 3600
    watch_endpoint = cfg.get("alert_new_endpoint", "1") == "1"

    known = {row["pubkey"]: row for row in conn.execute("SELECT * FROM peer_seen")}
    # po restarcie Pi jadro zna tylko handshake'i od startu - cisze liczymy od zapamietanego
    remembered = stats.last_handshakes(conn, status) if dead_after else {}
    for peer in db.peers(conn):
        if peer["state"] != "active":
            continue
        stat = live.get(peer["pubkey"])
        if not stat:
            continue
        endpoint = (stat.get("endpoint") or "").strip()
        handshake = int(stat.get("latest_handshake") or 0)
        previous = known.get(peer["pubkey"])

        if (watch_endpoint and peer["pubkey"] not in skip_endpoint
                and previous and endpoint and endpoint != "(none)"
                and previous["last_endpoint"] and endpoint != previous["last_endpoint"]):
            old_host = previous["last_endpoint"].rsplit(":", 1)[0]
            new_host = endpoint.rsplit(":", 1)[0]
            if old_host != new_host:
                raise_alert(
                    conn, "new_endpoint", peer["name"],
                    i18n.pl(i18n.ALERTS["new_endpoint"], name=peer["name"], ip=peer["ip"],
                            new=new_host, old=old_host),
                    severity="alarm",
                )

        last = max(handshake, remembered.get(peer["pubkey"], 0))
        if dead_after and last and now - last > dead_after and stats.mark_dead(conn, peer, last):
            hours = (now - last) // 3600
            raise_alert(
                conn, "dead_peer", peer["name"],
                i18n.pl(i18n.ALERTS["dead_peer"], name=peer["name"], ip=peer["ip"], n=hours),
                severity="warn",
            )

        if endpoint and endpoint != "(none)":
            with conn:
                conn.execute(
                    """INSERT INTO peer_seen(pubkey, last_endpoint, last_handshake, updated_at)
                       VALUES (?, ?, ?, ?)
                       ON CONFLICT(pubkey) DO UPDATE SET
                         last_endpoint = excluded.last_endpoint,
                         last_handshake = excluded.last_handshake,
                         updated_at = excluded.updated_at""",
                    (peer["pubkey"], endpoint, handshake, now),
                )


def mark_seen(conn: sqlite3.Connection) -> None:
    with conn:
        conn.execute("UPDATE alerts SET seen = 1 WHERE seen = 0")
