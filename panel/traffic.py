"""Historia ruchu: probki z `wg show dump`, szeregi godzinowe i wykrywanie anomalii.

Liczniki w WireGuardzie sa narastajace od podniesienia interfejsu. Przyrost
liczymy jako roznice kolejnych probek; spadek licznika oznacza restart
interfejsu i wtedy przyrostem jest sama nowa wartosc.

rx - to, co serwer odebral od klienta (wysylka klienta)
tx - to, co serwer wyslal do klienta (pobieranie przez klienta)
"""
from __future__ import annotations

import sqlite3
import statistics
import time

from . import db, i18n, notify

HOUR = 3600
KEEP_DAYS = 8


def record(conn: sqlite3.Connection, status: dict, now: int | None = None) -> int:
    live = (status or {}).get("peers", {})
    moment = int(now if now is not None else time.time())
    rows = [(moment, key, int(stat.get("rx", 0)), int(stat.get("tx", 0)))
            for key, stat in live.items()]
    if rows:
        with conn:
            conn.executemany("INSERT INTO traffic(ts, pubkey, rx, tx) VALUES (?, ?, ?, ?)", rows)
    return len(rows)


def prune(conn: sqlite3.Connection, now: int | None = None) -> None:
    cutoff = int(now if now is not None else time.time()) - KEEP_DAYS * 86400
    with conn:
        conn.execute("DELETE FROM traffic WHERE ts < ?", (cutoff,))


def hourly(conn: sqlite3.Connection, pubkey: str | None = None, days: int = 7,
           now: int | None = None) -> list[dict]:
    """Szereg godzinowy dla jednego peera albo sumy wszystkich (pubkey=None).

    Jedno zapytanie po wszystkie probki naraz i jedno przejscie po wyniku - wczesniej
    bylo osobne zapytanie na kazdego peera, co na Raspberry Pi kosztowalo sekundy.
    """
    moment = int(now if now is not None else time.time())
    end = moment - moment % HOUR + HOUR
    start = end - days * 24 * HOUR
    buckets = {start + i * HOUR: [0, 0] for i in range(days * 24)}

    if pubkey:
        rows = conn.execute(
            "SELECT pubkey, ts, rx, tx FROM traffic WHERE pubkey = ? AND ts >= ? "
            "ORDER BY pubkey, ts", (pubkey, start - HOUR))
    else:
        rows = conn.execute(
            "SELECT pubkey, ts, rx, tx FROM traffic WHERE ts >= ? ORDER BY pubkey, ts",
            (start - HOUR,))

    previous_key = None
    prev_rx = prev_tx = 0
    for key, ts, rx, tx in rows:
        if key != previous_key:
            previous_key, prev_rx, prev_tx = key, rx, tx
            continue
        d_rx = rx - prev_rx if rx >= prev_rx else rx
        d_tx = tx - prev_tx if tx >= prev_tx else tx
        prev_rx, prev_tx = rx, tx
        bucket = ts - ts % HOUR
        if bucket in buckets:
            buckets[bucket][0] += d_rx
            buckets[bucket][1] += d_tx
    return [{"ts": ts, "rx": v[0], "tx": v[1]} for ts, v in sorted(buckets.items())]


def totals(series: list[dict]) -> tuple[int, int]:
    return sum(p["rx"] for p in series), sum(p["tx"] for p in series)


def detect_anomalies(conn: sqlite3.Connection, now: int | None = None) -> list[str]:
    """Porownuje ostatnia pelna godzine z typowa godzina z ostatniego tygodnia."""
    cfg = db.settings(conn)
    factor = float(cfg.get("traffic_anomaly_factor", "10") or 10)
    minimum = float(cfg.get("traffic_anomaly_min_mb", "500") or 500) * 1024 * 1024
    moment = int(now if now is not None else time.time())
    found = []

    for peer in db.peers(conn):
        if peer["state"] != "active":
            continue
        series = hourly(conn, peer["pubkey"], 7, moment)
        if len(series) < 2:
            continue
        last = series[-2]  # ostatnia PELNA godzina
        history = [p["rx"] + p["tx"] for p in series[:-2] if p["rx"] + p["tx"] > 0]
        if len(history) < 12:
            continue  # za malo historii, zeby wiedziec, co jest normalne
        typical = statistics.median(history)
        current = last["rx"] + last["tx"]
        if current >= minimum and current > typical * factor:
            mb = current / 1024 / 1024
            found.append(peer["name"])
            notify.raise_alert(
                conn, "traffic_anomaly", peer["name"],
                i18n.pl(i18n.ALERTS["traffic"], name=peer["name"], ip=peer["ip"],
                        mb=f"{mb:.0f}", x=f"{current / max(typical, 1):.0f}"),
                severity="warn",
            )
    return found


def human(value: float) -> str:
    step = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if step < 1024:
            return f"{step:.0f} {unit}" if unit == "B" else f"{step:.1f} {unit}"
        step /= 1024
    return f"{step:.1f} PB"
