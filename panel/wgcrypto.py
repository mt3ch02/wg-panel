"""Klucze WireGuard i TOTP - wylacznie biblioteka standardowa.

Jesli binarka `wg` jest dostepna, uzywamy jej. Jesli nie (np. panel chodzi na
koncie bez dostepu do PATH roota), liczymy X25519 w czystym Pythonie.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import shutil
import struct
import subprocess
import time

_P = 2 ** 255 - 19
_A24 = 121665
_BASEPOINT = (9).to_bytes(32, "little")


def _cswap(swap: int, a: int, b: int) -> tuple[int, int]:
    dummy = swap * ((a - b) % _P)
    return (a - dummy) % _P, (b + dummy) % _P


def x25519(scalar: bytes, point: bytes) -> bytes:
    """Mnozenie skalarne wg RFC 7748 (montgomery ladder, czas stały co do bitu klucza)."""
    k = bytearray(scalar)
    k[0] &= 248
    k[31] &= 127
    k[31] |= 64
    k_int = int.from_bytes(k, "little")
    x1 = int.from_bytes(point, "little") & ((1 << 255) - 1)

    x2, z2, x3, z3 = 1, 0, x1, 1
    swap = 0
    for t in range(254, -1, -1):
        kt = (k_int >> t) & 1
        swap ^= kt
        x2, x3 = _cswap(swap, x2, x3)
        z2, z3 = _cswap(swap, z2, z3)
        swap = kt

        a = (x2 + z2) % _P
        aa = a * a % _P
        b = (x2 - z2) % _P
        bb = b * b % _P
        e = (aa - bb) % _P
        c = (x3 + z3) % _P
        d = (x3 - z3) % _P
        da = d * a % _P
        cb = c * b % _P
        x3 = pow((da + cb) % _P, 2, _P)
        z3 = x1 * pow((da - cb) % _P, 2, _P) % _P
        x2 = aa * bb % _P
        z2 = e * ((aa + _A24 * e) % _P) % _P

    x2, x3 = _cswap(swap, x2, x3)
    z2, z3 = _cswap(swap, z2, z3)
    return (x2 * pow(z2, _P - 2, _P) % _P).to_bytes(32, "little")


def _wg_available() -> bool:
    return shutil.which("wg") is not None


def genkey() -> str:
    """Klucz prywatny w base64."""
    if _wg_available():
        out = subprocess.run(["wg", "genkey"], capture_output=True, text=True, check=True)
        return out.stdout.strip()
    raw = bytearray(secrets.token_bytes(32))
    raw[0] &= 248
    raw[31] &= 127
    raw[31] |= 64
    return base64.b64encode(bytes(raw)).decode()


def pubkey(private_b64: str) -> str:
    if _wg_available():
        out = subprocess.run(
            ["wg", "pubkey"], input=private_b64, capture_output=True, text=True, check=True
        )
        return out.stdout.strip()
    priv = base64.b64decode(private_b64)
    if len(priv) != 32:
        raise ValueError("klucz prywatny musi miec 32 bajty")
    return base64.b64encode(x25519(priv, _BASEPOINT)).decode()


def genpsk() -> str:
    if _wg_available():
        out = subprocess.run(["wg", "genpsk"], capture_output=True, text=True, check=True)
        return out.stdout.strip()
    return base64.b64encode(secrets.token_bytes(32)).decode()


# --- hasla i sesje ---

def hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    salt = salt or os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return base64.b64encode(digest).decode(), base64.b64encode(salt).decode()


def verify_password(password: str, digest_b64: str, salt_b64: str) -> bool:
    salt = base64.b64decode(salt_b64)
    calc, _ = hash_password(password, salt)
    return hmac.compare_digest(calc, digest_b64)


# --- TOTP (RFC 6238, SHA1/6 cyfr/30 s) ---

def totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def totp_now(secret_b32: str, at: float | None = None, skew: int = 0) -> str:
    padding = "=" * (-len(secret_b32) % 8)
    key = base64.b32decode(secret_b32 + padding, casefold=True)
    counter = int((at or time.time()) // 30) + skew
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    code = struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF
    return f"{code % 1000000:06d}"


def totp_verify(secret_b32: str, code: str, window: int = 1) -> bool:
    code = (code or "").strip().replace(" ", "")
    if not code.isdigit():
        return False
    for skew in range(-window, window + 1):
        if hmac.compare_digest(totp_now(secret_b32, skew=skew), code):
            return True
    return False


def totp_uri(secret_b32: str, account: str, issuer: str = "wg-panel") -> str:
    return (
        f"otpauth://totp/{issuer}:{account}?secret={secret_b32}"
        f"&issuer={issuer}&algorithm=SHA1&digits=6&period=30"
    )
