#!/usr/bin/env python3
"""Strażnik (`wgctl guard`): panel, helper, wg0, zdrowie Pi - alarm raz, "wróciło" raz.

Pliki systemowe Pi, systemctl i panel są udawane, helper działa naprawdę (na atrapach),
powiadomienia trafiają do testowego odbiornika ntfy.
"""
from __future__ import annotations

import http.server
import json
import os
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
TMP = tempfile.mkdtemp(prefix="wgpanel-guard-")
BIN, WGDIR, SYS = (os.path.join(TMP, d) for d in ("bin", "wireguard", "root"))
for path in (BIN, WGDIR):
    os.makedirs(path)
FAILURES = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  [{'OK  ' if condition else 'BLAD'}] {label}"
          + (f"  ({detail})" if detail and not condition else ""))
    if not condition:
        FAILURES.append(label)


def write(relative: str, value: str) -> None:
    path = os.path.join(SYS, relative)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="ascii") as handle:
        handle.write(value + "\n")


def stub(name: str, script: str) -> None:
    path = os.path.join(BIN, name)
    with open(path, "w", encoding="utf8") as handle:
        handle.write("#!/bin/bash\n" + script)
    os.chmod(path, 0o755)


PANEL_STATE = os.path.join(TMP, "panel_state")
stub("systemctl", f'if [ "$1" = "is-active" ] && [ "$2" = "wg-panel" ]; then cat {PANEL_STATE}; '
                  'exit 0; fi\nif [ "$1" = "is-active" ]; then echo active; fi\nexit 0\n')
stub("wg", 'case "$1" in show) exit 0;; pubkey) cat >/dev/null; head -c 32 /dev/urandom | base64;; esac\nexit 0\n')
stub("wg-quick", "exit 0\n")
for tool in ("iptables", "ip6tables", "iptables-restore", "ip6tables-restore", "ip"):
    stub(tool, "exit 0\n")
with open(os.path.join(WGDIR, "server_private.key"), "w", encoding="utf8") as handle:
    handle.write("KLUCZPRYWATNYSERWERA00000000000000000000000=\n")

write("sys/class/net/wg0/flags", "0x1091")
write("sys/class/thermal/thermal_zone0/temp", "45000")
write("sys/devices/platform/soc/soc:firmware/get_throttled", "0x0")
write("proc/sys/kernel/random/boot_id", "boot-1")
with open(PANEL_STATE, "w", encoding="ascii") as handle:
    handle.write("active\n")

received = []
PANEL_UP = {"value": True}


class Sink(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):          # "panel": strona logowania
        self.send_response(200 if PANEL_UP["value"] else 500)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Sink)
threading.Thread(target=server.serve_forever, daemon=True).start()
PORT = str(server.server_address[1])

DB = os.path.join(TMP, "panel.db")
env = dict(os.environ)
env.update({"PATH": BIN + os.pathsep + env["PATH"], "WGPANEL_DB": DB,
            "WGPANEL_SOCKET": os.path.join(TMP, "helper.sock"), "WGPANEL_WGDIR": WGDIR,
            "WGPANEL_PORT": PORT, "PYTHONPATH": ROOT})
os.environ.update({k: env[k] for k in ("PATH", "WGPANEL_DB", "WGPANEL_SOCKET", "WGPANEL_PORT")})

from panel import db, helperclient, watchdog  # noqa: E402

helperclient.SOCKET_PATH = env["WGPANEL_SOCKET"]
helper = subprocess.Popen([sys.executable, os.path.join(ROOT, "helper", "wg_panel_helper.py")],
                          env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(1.2)
conn = db.connect(DB)
db.init_defaults(conn)
for key, value in (("alerts_channel", "ntfy"), ("alerts_url", f"http://127.0.0.1:{PORT}/wg-test-temat"),
                   ("alerts_quiet_minutes", "0")):
    db.set_setting(conn, key, value)


# progi miejsca na dysku zaleza od maszyny, na ktorej leci test - tu ustawiamy je jawnie
watchdog.MIN_FREE_MB, watchdog.MIN_FREE_RATIO = 0, 0.0
check("prog miejsca rozsadny na duzej karcie (256 GB, 10 GB wolnego = OK)",
      10240 >= max(500, min(0.10 * 262144, 2048)))
check("prog miejsca rozsadny na malej karcie (16 GB, 1 GB wolnego = alarm)",
      1024 < max(500, min(0.10 * 16384, 2048)))


def guard() -> dict:
    return watchdog.run(conn, sysroot=SYS, port=PORT)


def titles() -> list:
    return [m.get("title") for m in received]


try:
    print("\n1. Wszystko dziala")
    report = guard()
    check("brak powiadomien, wszystkie sprawdzenia OK", not received and all(report["checks"].values()),
          str(report))

    print("\n2. Panel pada i wraca")
    PANEL_UP["value"] = False
    with open(PANEL_STATE, "w", encoding="ascii") as handle:
        handle.write("inactive\n")
    guard()
    check("pierwsze nieudane sprawdzenie - jeszcze cisza", not received, str(titles()))
    guard()
    check("drugie z rzedu - alarm", titles() == ["panel: problem"], str(titles()))
    check("alarm z wysokim priorytetem", received and received[0]["priority"] == 4)
    guard()
    check("bez powtarzania co minute", len(received) == 1, str(titles()))
    PANEL_UP["value"] = True
    with open(PANEL_STATE, "w", encoding="ascii") as handle:
        handle.write("active\n")
    guard()
    check("po naprawie jedno 'znowu dziala'", titles()[-1] == "panel: znowu działa"
          and "przerwa" in received[-1]["message"], str(titles()))
    received.clear()

    print("\n3. Interfejs WireGuard")
    write("sys/class/net/wg0/flags", "0x1090")
    guard()
    guard()
    check("wg0 nie dziala - alarm", titles() == ["interfejs WireGuard: problem"], str(titles()))
    check("najwyzszy priorytet (uzytkownicy bez dostepu)", received and received[0]["priority"] == 5)
    write("sys/class/net/wg0/flags", "0x1091")
    guard()
    received.clear()

    print("\n4. Zdrowie Pi")
    write("sys/class/thermal/thermal_zone0/temp", "85000")
    guard()
    guard()
    check("przegrzanie - ostrzezenie", titles() == ["temperatura (Pi): problem"]
          and "85" in received[0]["message"], str(received))
    write("sys/class/thermal/thermal_zone0/temp", "45000")
    guard()
    received.clear()
    write("sys/devices/platform/soc/soc:firmware/get_throttled", "0x50005")
    guard()
    check("za niskie napiecie - od razu (to zdarzenie, nie stan)", titles() == ["zasilanie (Pi)"],
          str(titles()))
    guard()
    check("raz na uruchomienie Pi", len(received) == 1, str(titles()))
    write("proc/sys/kernel/random/boot_id", "boot-2")
    guard()
    check("po ponownym uruchomieniu znowu", len(received) == 2, str(titles()))
    write("sys/devices/platform/soc/soc:firmware/get_throttled", "0x0")
    received.clear()
    watchdog.MIN_FREE_MB = 10 ** 9
    guard()
    guard()
    check("malo miejsca na karcie - ostrzezenie", titles() == ["miejsce na dysku: problem"],
          str(titles()))
    watchdog.MIN_FREE_MB = 0
    guard()
    received.clear()

    print("\n4b. Certyfikat HTTPS")
    tls_dir = os.path.join(TMP, "tls")
    os.makedirs(tls_dir)
    with open(os.path.join(tls_dir, "panel.json"), "w", encoding="utf8") as handle:
        json.dump({"not_after": time.time() + 5 * 86400}, handle)
    os.environ.update({"WGPANEL_TLS": "1", "WGPANEL_TLS_DIR": tls_dir})
    guard()
    guard()
    check("certyfikat blisko konca - alarm", titles() == ["certyfikat HTTPS: problem"]
          and "5 dni" in received[0]["message"], str(received))
    os.environ["WGPANEL_TLS"] = "0"
    guard()
    received.clear()

    print("\n5. Helper pada - wgctl guard")
    helper.terminate()
    helper.wait(timeout=10)
    out = ""
    for _attempt in range(2):
        run = subprocess.run([sys.executable, os.path.join(ROOT, "wgctl"), "guard", "--quiet"],
                             env=env, capture_output=True, text=True, timeout=60)
        out = run.stdout + run.stderr
    check("wgctl guard dziala bez helpera i zglasza problem", "problem: helper" in out
          and run.returncode == 0, out)
    check("alarm o helperze wyslany", "helper: problem" in titles(), str(titles()))
    check("stan pamietany miedzy uruchomieniami", os.path.exists(os.path.join(TMP, "watchdog.json")))
    alerts = [r["kind"] for r in conn.execute("SELECT kind FROM alerts")]
    check("alarmy widoczne tez w panelu (tabela alertow)", "watchdog_helper" in alerts
          and "watchdog_panel" in alerts, str(alerts))
finally:
    if helper.poll() is None:
        helper.terminate()
    server.shutdown()

print("\n" + "=" * 60)
if FAILURES:
    print(f"NIEUDANE ({len(FAILURES)}):")
    for item in FAILURES:
        print("  -", item)
    sys.exit(1)
print("Wszystkie testy przeszly.")
