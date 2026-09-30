"""Sesje, CSRF i throttling logowania."""
from __future__ import annotations

import secrets
import sqlite3
import threading
import time

from . import wgcrypto

SESSION_TTL = 12 * 3600
COOKIE = "wgpanel"

_attempts: dict[str, list[float]] = {}
_attempts_lock = threading.Lock()
MAX_ATTEMPTS = 5
ATTEMPT_WINDOW = 300


def throttled(key: str) -> bool:
    now = time.time()
    with _attempts_lock:
        hits = [t for t in _attempts.get(key, []) if now - t < ATTEMPT_WINDOW]
        _attempts[key] = hits
        return len(hits) >= MAX_ATTEMPTS


def record_failure(key: str) -> None:
    with _attempts_lock:
        _attempts.setdefault(key, []).append(time.time())


def clear_failures(key: str) -> None:
    with _attempts_lock:
        _attempts.pop(key, None)


def create_user(conn: sqlite3.Connection, username: str, password: str,
                totp_secret: str | None = None) -> int:
    digest, salt = wgcrypto.hash_password(password)
    with conn:
        cursor = conn.execute(
            """INSERT INTO users(username, pwd_hash, pwd_salt, totp_secret, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (username, digest, salt, totp_secret, int(time.time())),
        )
    return int(cursor.lastrowid)


def has_users(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"] > 0


def authenticate(conn: sqlite3.Connection, username: str, password: str,
                 totp_code: str = "") -> sqlite3.Row | None:
    row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    if row is None:
        wgcrypto.hash_password(password)  # wyrownanie czasu odpowiedzi
        return None
    if not wgcrypto.verify_password(password, row["pwd_hash"], row["pwd_salt"]):
        return None
    if row["totp_secret"] and not wgcrypto.totp_verify(row["totp_secret"], totp_code):
        return None
    return row


def start_session(conn: sqlite3.Connection, user_id: int) -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(24)
    now = int(time.time())
    with conn:
        conn.execute(
            """INSERT INTO sessions(token, user_id, csrf, created_at, expires_at)
               VALUES (?, ?, ?, ?, ?)""",
            (token, user_id, csrf, now, now + SESSION_TTL),
        )
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
    return token, csrf


def session(conn: sqlite3.Connection, token: str | None) -> sqlite3.Row | None:
    if not token:
        return None
    row = conn.execute(
        """SELECT s.token, s.csrf, s.expires_at, u.id AS user_id, u.username
           FROM sessions s JOIN users u ON u.id = s.user_id
           WHERE s.token = ? AND s.expires_at > ?""",
        (token, int(time.time())),
    ).fetchone()
    return row


def end_session(conn: sqlite3.Connection, token: str) -> None:
    with conn:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))


def check_csrf(sess: sqlite3.Row, supplied: str) -> bool:
    return bool(supplied) and secrets.compare_digest(sess["csrf"], supplied)


def any_totp(conn: sqlite3.Connection) -> bool:
    """Czy ktokolwiek ma wlaczony kod z aplikacji - wtedy logowanie pokazuje pole na kod."""
    return bool(conn.execute(
        "SELECT 1 FROM users WHERE totp_secret IS NOT NULL AND totp_secret != '' LIMIT 1"
    ).fetchone())


def check_password(conn: sqlite3.Connection, user_id: int, password: str) -> bool:
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not row:
        return False
    return wgcrypto.verify_password(password, row["pwd_hash"], row["pwd_salt"])


def set_password(conn: sqlite3.Connection, user_id: int, password: str) -> None:
    digest, salt = wgcrypto.hash_password(password)
    with conn:
        conn.execute("UPDATE users SET pwd_hash = ?, pwd_salt = ? WHERE id = ?",
                     (digest, salt, user_id))
        # inne sesje tego konta wylatuja - stare haslo moglo wyciec
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))


def set_totp(conn: sqlite3.Connection, user_id: int, secret: str | None) -> None:
    with conn:
        conn.execute("UPDATE users SET totp_secret = ? WHERE id = ?", (secret, user_id))
