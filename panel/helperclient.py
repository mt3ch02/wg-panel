"""Cienki klient gniazda helpera. Jedno zapytanie = jedno polaczenie."""
from __future__ import annotations

import json
import os
import socket

SOCKET_PATH = os.environ.get("WGPANEL_SOCKET", "/run/wg-panel/helper.sock")


class HelperError(RuntimeError):
    pass


def call(op: str, payload: dict | None = None, timeout: float = 60.0) -> dict:
    request = json.dumps({"op": op, "payload": payload or {}}).encode("utf8") + b"\n"
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(SOCKET_PATH)
            sock.sendall(request)
            buffer = b""
            while not buffer.endswith(b"\n"):
                chunk = sock.recv(65536)
                if not chunk:
                    break
                buffer += chunk
    except FileNotFoundError as exc:
        raise HelperError(
            f"helper nie dziala (brak gniazda {SOCKET_PATH}) - "
            "sprawdz: systemctl status wg-panel-helper"
        ) from exc
    except OSError as exc:
        raise HelperError(f"blad polaczenia z helperem: {exc}") from exc

    if not buffer.strip():
        raise HelperError("helper nie odpowiedzial")
    response = json.loads(buffer.decode("utf8"))
    if not response.get("ok"):
        raise HelperError(response.get("error", "nieznany blad helpera"))
    return response.get("result") or {}


