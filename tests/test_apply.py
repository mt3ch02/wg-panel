#!/usr/bin/env python3
"""Test sciezki apply -> confirm -> rollback na atrapach wg/iptables.

Podmieniamy narzedzia systemowe na skrypty logujace wywolania, dzieki czemu
mozna sprawdzic kolejnosc operacji i dzialanie bezpiecznika bez ruszania sieci.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="wgpanel-apply-")
BIN = os.path.join(TMP, "bin")
WGDIR = os.path.join(TMP, "wireguard")
CALLS = os.path.join(TMP, "calls.log")
os.makedirs(BIN)
os.makedirs(WGDIR)

FAILURES = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  [{'OK  ' if condition else 'BLAD'}] {label}" + (f"  ({detail})" if detail and not condition else ""))
    if not condition:
        FAILURES.append(label)


def stub(name: str, script: str) -> None:
    path = os.path.join(BIN, name)
    with open(path, "w", encoding="utf8") as handle:
        handle.write("#!/bin/bash\necho \"$0 $*\" >> " + CALLS + "\n" + script)
    os.chmod(path, 0o755)


stub("wg", """
case "$1" in
  show)   [ -f %s/iface_up ] || exit 1
          printf 'PRIV\\tPUB\\t41194\\toff\\n'; exit 0 ;;
  pubkey) echo "SERWEROWYKLUCZPUBLICZNY0000000000000000000="; exit 0 ;;
  syncconf) exit 0 ;;
esac
exit 0
""" % TMP)

stub("wg-quick", """
if [ "$1" = "strip" ]; then cat %s/wg0.conf; fi
exit 0
""" % WGDIR)

stub("iptables", """
for a in "$@"; do if [ "$a" = "-C" ]; then exit 1; fi; done
exit 0
""")
stub("iptables-restore", "exit 0")
stub("systemctl", """
if [ "$1" = "is-active" ]; then echo active; fi
exit 0
""")
stub("ip", "exit 0")

# klucz prywatny serwera, tak jak po instrukcji z krok 3
with open(os.path.join(WGDIR, "server_private.key"), "w", encoding="utf8") as handle:
    handle.write("KLUCZPRYWATNYSERWERA00000000000000000000000=\n")
open(os.path.join(TMP, "iface_up"), "w").close()

env = dict(os.environ)
env.update({
    "PATH": BIN + os.pathsep + env["PATH"],
    "WGPANEL_SOCKET": os.path.join(TMP, "helper.sock"),
    "WGPANEL_WGDIR": WGDIR,
    "PYTHONPATH": ROOT,
})

helper = subprocess.Popen([sys.executable, os.path.join(ROOT, "helper", "wg_panel_helper.py")],
                          env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
time.sleep(1.2)

sys.path.insert(0, ROOT)
from panel import helperclient  # noqa: E402

helperclient.SOCKET_PATH = env["WGPANEL_SOCKET"]

SETTINGS = {
    "lan": "192.168.50.0/24", "vpn": "10.0.0.0/24", "server_ip": "10.0.0.1",
    "wan_if": "eth0", "listen_port": "41194", "wg_iface": "wg0",
    "chain": "WG-USERS", "managed_host_ports": "22,3389",
}


def peer(name, ip, rules, host_access="", state="active", key="A"):
    return {"name": name, "device": "", "tier_name": "T", "ip": ip,
            "pubkey": key * 43 + "=", "psk": "B" * 43 + "=", "state": state,
            "host_access": host_access, "rules": rules}


def calls() -> list[str]:
    if not os.path.exists(CALLS):
        return []
    with open(CALLS, encoding="utf8") as handle:
        return [line.strip() for line in handle]


try:
    print("\n1. Pierwszy apply")
    model = {"settings": SETTINGS, "peers": [
        peer("Admin", "10.0.0.2", [], host_access="22,3389", key="A"),
        peer("Marek", "10.0.0.4", [
            {"action": "ACCEPT", "dest": "192.168.50.11/32", "proto": "tcp",
             "ports": [80, 443, 445], "time_from": "", "time_to": "",
             "weekdays": [], "comment": "nas"},
            {"action": "DROP", "dest": "192.168.50.0/24", "proto": "any", "ports": [],
             "time_from": "", "time_to": "", "weekdays": [], "comment": "reszta"},
        ], key="C"),
    ]}
    result = helperclient.call("apply", dict(model, confirm_seconds=0))
    check("apply konczy sie sukcesem", "steps" in result)
    check("zsynchronizowano peery", any("syncconf" in s for s in result["steps"]),
          json.dumps(result["steps"]))
    check("przebudowano lancuchy", any("lancuchy" in s for s in result["steps"]))

    conf = open(os.path.join(WGDIR, "wg0.conf"), encoding="utf8").read()
    check("klucz prywatny wstawiony z pliku, nie z panelu",
          "KLUCZPRYWATNYSERWERA" in conf and "__SERVER_PRIVATE_KEY__" not in conf)
    check("wg0.conf ma obu peerow", conf.count("[Peer]") == 2)
    check("uprawnienia wg0.conf to 600",
          oct(os.stat(os.path.join(WGDIR, "wg0.conf")).st_mode)[-3:] == "600")

    rules = open(os.path.join(WGDIR, "wg0-panel-rules.v4"), encoding="utf8").read()
    check("plik regul zawiera lancuch hosta", "WG-USERS-HOST" in rules)
    check("admin ma dostep do 22,3389",
          "-s 10.0.0.2/32 -p tcp -m multiport --dports 22,3389" in rules)
    check("pozostali odciecie od hosta", "reszta peerow bez dostepu do hosta" in rules)

    log = calls()
    check("hook w FORWARD wstawiany na pozycje 1",
          any("iptables -I FORWARD 1 -i wg0 -j WG-USERS" in c for c in log))
    check("conntrack dla ruchu zwrotnego",
          any("-o wg0 -m conntrack" in c for c in log))
    check("hook w INPUT dla lancucha hosta",
          any("iptables -I INPUT 1 -i wg0 -j WG-USERS-HOST" in c for c in log))
    check("MASQUERADE zawezony do podsieci VPN",
          any("-t nat -A POSTROUTING -s 10.0.0.0/24 -o eth0 -j MASQUERADE" in c for c in log))

    print("\n2. Bezpiecznik: apply z potwierdzeniem")
    model2 = json.loads(json.dumps(model))
    model2["peers"].append(peer("Nowy", "10.0.0.9", [], key="D"))
    result = helperclient.call("apply", dict(model2, confirm_seconds=3))
    token = result.get("confirm_token")
    check("apply zwraca token potwierdzenia", bool(token))
    conf = open(os.path.join(WGDIR, "wg0.conf"), encoding="utf8").read()
    check("nowa konfiguracja jest na dysku", conf.count("[Peer]") == 3)

    status = helperclient.call("status", {"settings": SETTINGS})
    check("status raportuje oczekujace potwierdzenie", status.get("pending") is not None)

    print("\n3. Potwierdzenie utrwala zmiane")
    helperclient.call("confirm", {"token": token})
    status_after = helperclient.call("status", {"settings": model2["settings"]})
    files = status_after.get("files", {})
    check("po potwierdzeniu helper podaje odciski plikow z dysku",
          bool(files.get("conf")) and bool(files.get("rules4")), str(files))
    check("odcisk zgadza sie z plikiem na dysku",
          files.get("conf") == __import__("hashlib").sha256(
              "\n".join("PrivateKey = __SERVER_PRIVATE_KEY__"
                        if line.strip().startswith("PrivateKey") else line
                        for line in open(os.path.join(WGDIR, "wg0.conf"),
                                         encoding="utf8").read().split("\n")).encode()
          ).hexdigest())
    time.sleep(4)
    conf = open(os.path.join(WGDIR, "wg0.conf"), encoding="utf8").read()
    check("po potwierdzeniu zmiana zostaje", conf.count("[Peer]") == 3)

    print("\n4. Brak potwierdzenia cofa zmiane")
    model3 = json.loads(json.dumps(model2))
    model3["peers"].append(peer("Kolejny", "10.0.0.11", [], key="E"))
    helperclient.call("apply", dict(model3, confirm_seconds=2))
    conf = open(os.path.join(WGDIR, "wg0.conf"), encoding="utf8").read()
    check("zaraz po apply widac 4 peerow", conf.count("[Peer]") == 4)
    time.sleep(4)
    conf = open(os.path.join(WGDIR, "wg0.conf"), encoding="utf8").read()
    check("po uplywie czasu automatyczny rollback do 3 peerow",
          conf.count("[Peer]") == 3, f"jest {conf.count('[Peer]')}")

    print("\n5. Odrzucanie zlych danych")
    for label, mutate in [
        ("adres spoza VPN", lambda m: m["peers"][0].update(ip="1.2.3.4")),
        ("zdublowany klucz", lambda m: m["peers"][1].update(pubkey=m["peers"][0]["pubkey"])),
        ("zla akcja w regule",
         lambda m: m["peers"][1]["rules"].append({"action": "MASQUERADE", "dest": "ANY"})),
        ("port poza zakresem",
         lambda m: m["peers"][1]["rules"].append(
             {"action": "ACCEPT", "dest": "ANY", "proto": "tcp", "ports": [99999]})),
        ("nazwa lancucha z metaznakami", lambda m: m["settings"].update(chain="A;rm -rf /")),
    ]:
        broken = json.loads(json.dumps(model2))
        mutate(broken)
        try:
            helperclient.call("apply", dict(broken, confirm_seconds=0))
            check(f"odrzuca: {label}", False)
        except helperclient.HelperError:
            check(f"odrzuca: {label}", True)

    conf_after = open(os.path.join(WGDIR, "wg0.conf"), encoding="utf8").read()
    check("odrzucone dane nie zmienily konfiguracji", conf_after.count("[Peer]") == 3)

    print("\n6. Reczny rollback z kopii")
    backups = os.listdir(os.path.join(WGDIR, "wg-panel-backups"))
    check("kopie zapasowe sa archiwizowane", len(backups) > 0, str(backups))
    result = helperclient.call("rollback", {"settings": SETTINGS})
    check("rollback z kopii dziala", "rolled_back" in result)

    print("\n7. Blokada rownoleglych zmian")
    helperclient.call("apply", dict(model2, confirm_seconds=5))
    try:
        helperclient.call("apply", dict(model2, confirm_seconds=5))
        check("nie pozwala na druga zmiane w trakcie potwierdzania", False)
    except helperclient.HelperError as exc:
        check("nie pozwala na druga zmiane w trakcie potwierdzania", "potwierdzeni" in str(exc))
    helperclient.call("rollback", {"settings": SETTINGS})

    print("\n8. Nieznane operacje")
    for op in ("exec", "shell", "read_file"):
        try:
            helperclient.call(op, {})
            check(f"odrzuca operacje {op}", False)
        except helperclient.HelperError:
            check(f"odrzuca operacje {op}", True)

finally:
    helper.terminate()
    try:
        helper.wait(timeout=5)
    except subprocess.TimeoutExpired:
        helper.kill()

print("\n" + "=" * 60)
if FAILURES:
    print(f"NIEPOWODZENIA ({len(FAILURES)}): " + ", ".join(FAILURES))
    print(helper.stderr.read().decode("utf8", "replace")[-3000:])
    sys.exit(1)
print("Wszystkie testy przeszly.")
