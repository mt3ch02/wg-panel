"""Watek tla: pilnuje wygasania dostepu, zbiera alerty i robi kopie dobowe.

Chodzi w procesie panelu, budzi sie co 30 sekund. Zadna z tych rzeczy nie
wymaga roota - apply idzie przez helpera tak samo jak z przegladarki.
"""
from __future__ import annotations

import sqlite3
import threading
import time

import json

from . import schedule, db, export, notify, traffic

TICK = 30


class Scheduler(threading.Thread):
    daemon = True

    def __init__(self, conn: sqlite3.Connection, status_fn, log=print):
        super().__init__(name="wg-panel-scheduler")
        self.conn = conn
        self.status_fn = status_fn    # () -> (status, error)
        self.log = log
        self.last_backup = 0.0
        self.last_sample = 0.0
        self.last_anomaly_hour = 0
        self.last_integrity = 0.0
        self.last_prune = 0.0
        self.drift_fn = None         # (expected) -> findings, ustawiane przez panel
        self.dirty_fn = None         # czy panel ma niezastosowane zmiany
        self.stop_event = threading.Event()

    def run(self) -> None:
        time.sleep(3)  # niech panel wstanie do konca
        while not self.stop_event.wait(TICK):
            for step in (self.check_expiry, self.check_alerts, self.check_backup,
                         self.check_integrity, self.check_anomalies):
                try:
                    step()
                except Exception as exc:  # noqa: BLE001
                    self.log(f"scheduler: {step.__name__}: {exc!r}")

    def stop(self) -> None:
        self.stop_event.set()

    # --- okna dostepu ---

    def check_expiry(self) -> None:
        """Terminy dostepu - wspolna logika z wgctl expire (timer systemd), zmiana punktowa."""
        schedule.run(self.conn)

    # --- alerty ---

    def check_alerts(self) -> None:
        """Jedno odpytanie helpera obsluguje i aktywacje, i alerty."""
        status, error = self.status_fn()
        if error or not status:
            return
        self.activate_pending(status)
        cfg = db.settings(self.conn)

        interval = max(1, int(cfg.get("traffic_sample_minutes", "5") or 5)) * 60
        if time.time() - self.last_sample >= interval:
            traffic.record(self.conn, status)
            self.last_sample = time.time()
        if time.time() - self.last_prune > 86400:
            traffic.prune(self.conn)
            audit, alerts = db.prune_history(self.conn)
            if audit or alerts:
                db.log(self.conn, "harmonogram", "history.prune",
                       f"dziennik: {audit}, alerty: {alerts}")
            self.last_prune = time.time()

        if cfg.get("alerts_channel", "none") == "none" and \
                cfg.get("alert_dead_hours", "0") in ("", "0"):
            return
        notify.check_peers(self.conn, status)

    def activate_pending(self, status: dict) -> None:
        schedule.activate_first_use(self.conn, status)

    # --- kopie zapasowe ---

    def check_backup(self) -> None:
        cfg = db.settings(self.conn)
        if not export.due(cfg, self.last_backup):
            return
        self.last_backup = time.time()
        record = export.run(self.conn, actor="harmonogram")
        if record["ok"] and cfg.get("backup_verify", "1") == "1":
            # starsze kopie tez - wykrywa zmiany i uszkodzenia na NAS po fakcie
            export.verify_recent(self.conn, 3)
        if not record["ok"]:
            notify.raise_alert(
                self.conn, "backup", "kopia zapasowa",
                f'Kopia zapasowa sie nie powiodla: {record["detail"]}',
                severity="alarm",
            )

    # --- integralnosc konfiguracji ---

    def check_integrity(self) -> None:
        cfg = db.settings(self.conn)
        if cfg.get("drift_check", "1") != "1" or self.drift_fn is None:
            return
        if time.time() - self.last_integrity < 300:
            return
        self.last_integrity = time.time()
        findings = self.drift_fn()
        if findings is None:
            return
        db.set_setting(self.conn, "drift_findings", json.dumps(findings))
        db.set_setting(self.conn, "drift_checked_at", str(int(time.time())))

        # gdy panel ma niezastosowane zmiany, roznica wzgledem dysku jest oczekiwana -
        # alarmowanie o niej to halas, ktory przykrywa prawdziwe ingerencje
        if self.dirty_fn and self.dirty_fn():
            db.log(self.conn, "harmonogram", "integrity.skip",
                   f"{len(findings)} roznic, ale panel ma niezastosowane zmiany")
            return

        for finding in findings:
            notify.raise_alert(
                self.conn, "tamper", finding["kind"],
                "Konfiguracja zmieniona poza panelem: " + finding["message"],
                severity=finding.get("severity", "alarm"),
            )

    # --- anomalie ruchu ---

    def check_anomalies(self) -> None:
        hour = int(time.time()) // 3600
        if hour == self.last_anomaly_hour:
            return
        # kilka minut po pelnej godzinie, zeby ostatnia godzina miala komplet probek
        if int(time.time()) % 3600 < 300:
            return
        self.last_anomaly_hour = hour
        traffic.detect_anomalies(self.conn)
