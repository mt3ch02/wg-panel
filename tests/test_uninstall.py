#!/usr/bin/env python3
"""Odinstalowanie (uninstall.sh) na udawanym systemie plikow - trzy poziomy i zabezpieczenia.

Skrypt dostaje WGPANEL_ROOT (katalog tymczasowy zamiast "/") i atrapy systemctl, ufw, userdel,
groupdel - zapisuja wywolania, niczego nie zmieniaja w prawdziwym systemie.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "uninstall.sh")
FAILURES = []

PANEL_CONF = "[Interface]\n# wygenerowane przez wg-panel\nListenPort = 41194\nPostUp = iptables-restore -n /etc/wireguard/wg0-panel-rules.v4\n"
V2_CONF = "[Interface]\n# konfiguracja z instrukcji v2\n"


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  [{'OK  ' if condition else 'BLAD'}] {label}"
          + (f"  ({detail})" if detail and not condition else ""))
    if not condition:
        FAILURES.append(label)


def build() -> tuple:
    """Udawany system po instalacji panelu."""
    base = tempfile.mkdtemp(prefix="wgpanel-uninstall-")
    fake, calls = os.path.join(base, "root"), os.path.join(base, "calls.log")
    files = {
        "opt/wg-panel/wgctl": "", "opt/wg-panel/panel/app.py": "", "usr/local/bin/wgctl": "",
        "etc/systemd/system/wg-panel.service": "", "etc/systemd/system/wg-panel-helper.service": "",
        "etc/systemd/system/wg-panel-guard.service": "", "etc/systemd/system/wg-panel-guard.timer": "",
        "etc/systemd/system/wg-quick@.service.d/keep": "",
        "var/lib/wg-panel/panel.db": "baza", "var/lib/wg-panel/backups/wg-panel-1.json": "{}",
        "var/lib/wg-panel/tls/panel.crt": "", "etc/wireguard/wg0.conf": PANEL_CONF,
        "etc/wireguard/wg0-panel-rules.v4": "*filter\n", "etc/wireguard/wg0-panel-rules.v6": "*filter\n",
        "etc/wireguard/server_private.key": "KLUCZ", "etc/wireguard/wg-panel-backups/wg0.conf.1": "",
        "etc/wireguard/wg-panel-ca/ca.key": "", "etc/sysctl.d/99-wg-panel-forward.conf": "",
        "etc/sysctl.d/99-wg-panel-ipv6.conf": "", "root/wg-panel-before/wg0.conf": V2_CONF,
        "run/wg-panel/helper.sock": "",
    }
    for rel, content in files.items():
        path = os.path.join(fake, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf8") as handle:
            handle.write(content)
    bindir = os.path.join(base, "bin")
    os.makedirs(bindir)
    stubs = {
        "systemctl": f'echo "systemctl $*" >> {calls}\n',
        "userdel": f'echo "userdel $*" >> {calls}\n',
        "groupdel": f'echo "groupdel $*" >> {calls}\n',
        "id": "echo 0\n",
        "apt-get": f'echo "apt-get $*" >> {calls}\n',
        # trzy reguly: dwie panelu (5 i 9) i jedna cudza (7) - ma zostac
        "ufw": ('if [ "$1" = status ]; then\n'
                'echo "[ 5] 8080/tcp  ALLOW IN  192.168.50.0/24   # wg-panel LAN"\n'
                'echo "[ 7] 22/tcp    ALLOW IN  Anywhere          # ssh"\n'
                'echo "[ 9] 8080/tcp on wg0  ALLOW IN  Anywhere  # wg-panel"\n'
                'echo "[11] 41194/udp  ALLOW IN  Anywhere  # wg-panel WireGuard"\n'
                f'else echo "ufw $*" >> {calls}; fi\n'),
    }
    for name, body in stubs.items():
        path = os.path.join(bindir, name)
        with open(path, "w", encoding="utf8") as handle:
            handle.write("#!/bin/sh\n" + body)
        os.chmod(path, 0o755)
    return base, fake, calls, bindir


def run(fake: str, bindir: str, *args: str, lang: str = "pl", stdin: str | None = None):
    env = {k: v for k, v in os.environ.items() if k != "INSTALL_LANG"}
    env.update(WGPANEL_ROOT=fake, PATH=bindir + os.pathsep + os.environ["PATH"], INSTALL_LANG=lang)
    return subprocess.run(["bash", SCRIPT, *args], env=env, input=stdin, capture_output=True,
                          text=True, timeout=60)


def exists(fake: str, rel: str) -> bool:
    return os.path.lexists(os.path.join(fake, rel))


def log(calls: str) -> str:
    return open(calls, encoding="utf8").read() if os.path.exists(calls) else ""


print("\n1. Domyslnie: panel znika, VPN i dane zostaja")
base, fake, calls, bindir = build()
out = run(fake, bindir, "--yes")
check("skrypt konczy sie powodzeniem", out.returncode == 0, out.stderr[-300:])
check("program, wgctl, uslugi i gniazdo usuniete", not any(exists(fake, p) for p in (
    "opt/wg-panel", "usr/local/bin/wgctl", "etc/systemd/system/wg-panel.service",
    "etc/systemd/system/wg-panel-guard.timer", "run/wg-panel")))
check("uslugi zatrzymane", "systemctl disable --now wg-panel-guard.timer" in log(calls), log(calls))
check("urzad certyfikacji panelu usuniety", not exists(fake, "etc/wireguard/wg-panel-ca"))
check("VPN zostaje: konfiguracja, reguly, klucz, przekazywanie pakietow", all(exists(fake, p) for p in (
    "etc/wireguard/wg0.conf", "etc/wireguard/wg0-panel-rules.v4", "etc/wireguard/server_private.key",
    "etc/sysctl.d/99-wg-panel-forward.conf")))
check("dane panelu zostaja (ponowna instalacja je przywroci)", exists(fake, "var/lib/wg-panel/panel.db"))
check("konto wg-panel zostaje", "userdel" not in log(calls))
check("reguly ufw panelu usuniete od konca, cudza zostaje",
      "ufw --force delete 9\nufw --force delete 5" in log(calls) and "delete 7" not in log(calls), log(calls))
check("nie rusza innych plikow systemd", exists(fake, "etc/systemd/system/wg-quick@.service.d/keep"))
check("port WireGuarda w ufw zostaje - VPN dziala dalej", "delete 11" not in log(calls)
      and "WireGuard" in out.stdout, log(calls))
shutil.rmtree(base)

print("\n2. --purge: takze dane i konto, VPN dalej dziala")
base, fake, calls, bindir = build()
out = run(fake, bindir, "--purge", "--yes")
check("dane panelu i kopie plikow usuniete", not exists(fake, "var/lib/wg-panel")
      and not exists(fake, "etc/wireguard/wg-panel-backups"))
check("konto i grupa wg-panel usuniete", "userdel wg-panel" in log(calls) and "groupdel wg-panel" in log(calls))
check("VPN zostaje z ostatnia konfiguracja z panelu",
      open(os.path.join(fake, "etc/wireguard/wg0.conf")).read() == PANEL_CONF
      and exists(fake, "etc/wireguard/wg0-panel-rules.v4"))
check("kopia z Kroku 2 nietknieta", exists(fake, "root/wg-panel-before/wg0.conf"))
shutil.rmtree(base)

print("\n3. --restore: powrot do konfiguracji sprzed panelu")
base, fake, calls, bindir = build()
out = run(fake, bindir, "--restore", "--yes")
check("wg0.conf z Kroku 2", open(os.path.join(fake, "etc/wireguard/wg0.conf")).read() == V2_CONF)
check("konfiguracja panelu zachowana w /root/wg-panel-after", exists(fake, "root/wg-panel-after/wg0.conf")
      and exists(fake, "root/wg-panel-after/wg0-panel-rules.v4"))
check("reguly panelu i IPv6 panelu usuniete", not exists(fake, "etc/wireguard/wg0-panel-rules.v4")
      and not exists(fake, "etc/sysctl.d/99-wg-panel-ipv6.conf"))
check("klucz serwera i przekazywanie pakietow zostaja", exists(fake, "etc/wireguard/server_private.key")
      and exists(fake, "etc/sysctl.d/99-wg-panel-forward.conf"))
check("mowi, zeby zrestartowac Pi", "sudo reboot" in out.stdout)
shutil.rmtree(base)

print("\n3b. --restore: stan sprzed panelu ze starszej wersji (/root/przed-wg-panel)")
base, fake, calls, bindir = build()
os.rename(os.path.join(fake, "root/wg-panel-before"), os.path.join(fake, "root/przed-wg-panel"))
out = run(fake, bindir, "--restore", "--yes")
check("wg0.conf z /root/przed-wg-panel", out.returncode == 0 and
      open(os.path.join(fake, "etc/wireguard/wg0.conf")).read() == V2_CONF, out.stderr[-300:])
check("konfiguracja panelu w /root/po-wg-panel (para do starej nazwy)",
      exists(fake, "root/po-wg-panel/wg0.conf") and not exists(fake, "root/wg-panel-after"))
check("komunikat podaje faktyczny folder", "/root/przed-wg-panel/wg0.conf" in out.stdout, out.stdout[-500:])
shutil.rmtree(base)

print("\n4. Zabezpieczenia")
base, fake, calls, bindir = build()
with open(os.path.join(fake, "etc/wireguard/wg-panel-pending.json"), "w") as handle:
    handle.write('{"pending": {"digest": "x"}}')
out = run(fake, bindir, "--yes")
check("zmiana czeka na potwierdzenie - odmowa, nic nie usuniete",
      out.returncode != 0 and exists(fake, "opt/wg-panel") and "potwierdzenie" in out.stderr, out.stderr)
os.remove(os.path.join(fake, "etc/wireguard/wg-panel-pending.json"))
os.remove(os.path.join(fake, "root/wg-panel-before/wg0.conf"))
out = run(fake, bindir, "--restore", "--yes")
check("--restore bez kopii z Kroku 2 - odmowa", out.returncode != 0 and exists(fake, "opt/wg-panel"),
      out.stderr)
out = run(fake, bindir)
check("bez --yes i bez terminala - odmowa", out.returncode != 0 and exists(fake, "opt/wg-panel"))
out = run(fake, bindir, "--cos")
check("nieznana opcja - odmowa", out.returncode != 0 and exists(fake, "opt/wg-panel"))
out = run(fake, bindir, "--yes", lang="en")
check("po angielsku", "Will be removed:" in out.stdout and "The VPN keeps working" in out.stdout,
      out.stdout[-200:])
shutil.rmtree(base)



def run_tty(fake: str, bindir: str, *steps, args: tuple = (), extra: dict | None = None) -> str:
    """Prawdziwy pseudoterminal - tak jak po uruchomieniu z klawiatury.
    steps: pary (fragment pytania, odpowiedz) w kolejnosci, w jakiej skrypt pyta."""
    import pty
    import time
    env = {k: v for k, v in os.environ.items() if k != "INSTALL_LANG"}
    env.update(WGPANEL_ROOT=fake, PATH=bindir + os.pathsep + os.environ["PATH"],
               INSTALL_LANG="pl", **(extra or {}))
    pid, fd = pty.fork()
    if pid == 0:
        os.execve("/bin/bash", ["bash", SCRIPT, *args], env)
    output = b""
    for expect, answer in steps:
        deadline = time.time() + 20
        while time.time() < deadline and expect.encode() not in output:
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            output += chunk
        os.write(fd, (answer + "\n").encode())
    while True:
        try:
            chunk = os.read(fd, 4096)
        except OSError:
            break
        if not chunk:
            break
        output += chunk
    os.waitpid(pid, 0)
    return output.decode("utf8", "replace")


print("\n5. Potwierdzenie z klawiatury")
base, fake, calls, bindir = build()
out = run_tty(fake, bindir, ("Wybierz", ""), ("kontynuować", "nie"))
check("menu: Enter = tylko panel; odpowiedz inna niz 'tak' - przerwano, nic nie usuniete",
      "Co odinstalować?" in out and "przerwano" in out and exists(fake, "opt/wg-panel"), out[-300:])
check("menu pokazuje cztery poziomy (3 - bo jest kopia sprzed panelu)",
      all(f"  {n}) " in out for n in "1234") and "cały WireGuard" in out, out[:900])
out = run_tty(fake, bindir, ("Wybierz", "1"), ("kontynuować", "tak"))
check("'1' i 'tak' - odinstalowano tylko panel", not exists(fake, "opt/wg-panel") and "Gotowe." in out
      and exists(fake, "var/lib/wg-panel/panel.db") and exists(fake, "etc/wireguard/wg0.conf"), out[-300:])
shutil.rmtree(base)

base, fake, calls, bindir = build()
out = run_tty(fake, bindir, ("Wybierz", "2"), ("kontynuować", "tak"))
check("menu '2' - panel i dane, VPN zostaje", not exists(fake, "var/lib/wg-panel")
      and exists(fake, "etc/wireguard/wg0.conf"), out[-300:])
shutil.rmtree(base)

base, fake, calls, bindir = build()
shutil.rmtree(os.path.join(fake, "root/wg-panel-before"))
out = run_tty(fake, bindir, ("Wybierz", "3"))
check("bez kopii sprzed panelu menu nie ma pozycji 3 (i jej nie przyjmuje)",
      "  3) " not in out and "nie ma takiej pozycji" in out and exists(fake, "opt/wg-panel"), out[-400:])
shutil.rmtree(base)

print("\n6. Panel i caly WireGuard")
base, fake, calls, bindir = build()
out = run_tty(fake, bindir, ("Wybierz", "4"), ("usuń wireguard", "tak"))
check("zwykle 'tak' nie wystarcza - przerwano, nic nie usuniete",
      "przerwano" in out and exists(fake, "etc/wireguard/server_private.key") and exists(fake, "opt/wg-panel"),
      out[-300:])
check("ostrzega o kluczu serwera i mowi, co zniknie", "klucz serwera zostanie usunięty" in out
      and "KLUCZ SERWERA" in out and "wg0" in out, out[-900:])
out = run_tty(fake, bindir, ("Wybierz", "4"), ("usuń wireguard", "usuń wireguard"))
check("fraza 'usuń wireguard' - WireGuard usuniety: /etc/wireguard pusty, klucz tez",
      not os.listdir(os.path.join(fake, "etc/wireguard")) and not exists(fake, "opt/wg-panel")
      and not exists(fake, "var/lib/wg-panel"), out[-400:])
check("tunel zatrzymany i wylaczony przy starcie", "systemctl disable --now wg-quick@wg0" in log(calls), log(calls))
check("pakiety WireGuarda odinstalowane", "apt-get purge -y wireguard wireguard-tools" in log(calls), log(calls))
check("regula ufw portu WireGuarda tez usunieta (11, 9, 5), cudza zostaje",
      "ufw --force delete 11\nufw --force delete 9\nufw --force delete 5" in log(calls)
      and "delete 7" not in log(calls), log(calls))
check("przekazywanie pakietow panelu usuniete", not exists(fake, "etc/sysctl.d/99-wg-panel-forward.conf"))
check("kopia sprzed panelu zostaje", exists(fake, "root/wg-panel-before/wg0.conf"))
check("mowi o routerze i porcie z konfiguracji", "41194/udp" in out and "sudo reboot" in out, out[-500:])
shutil.rmtree(base)

base, fake, calls, bindir = build()
out = run(fake, bindir, "--wireguard", "--yes")
check("--wireguard --yes bez terminala i bez frazy - odmowa, nic nie usuniete",
      out.returncode != 0 and exists(fake, "etc/wireguard/server_private.key"), out.stderr[-300:])
env_out = subprocess.run(["bash", SCRIPT, "--wireguard"], capture_output=True, text=True, timeout=60,
                         env={**{k: v for k, v in os.environ.items() if k != "INSTALL_LANG"},
                              "WGPANEL_ROOT": fake, "PATH": bindir + os.pathsep + os.environ["PATH"],
                              "INSTALL_LANG": "pl", "WGPANEL_CONFIRM": "usuń wireguard"})
check("--wireguard z fraza w WGPANEL_CONFIRM (skrypty) - usuniety",
      env_out.returncode == 0 and not exists(fake, "etc/wireguard/server_private.key"), env_out.stderr[-300:])
shutil.rmtree(base)

base, fake, calls, bindir = build()
out = run(fake, bindir, "--yes")
check("--yes bez poziomu - jak dotad tylko panel, bez pytan", out.returncode == 0
      and "Co odinstalować?" not in out.stdout and exists(fake, "etc/wireguard/wg0.conf"), out.stdout[-300:])
shutil.rmtree(base)

source = open(os.path.join(ROOT, "install.sh"), encoding="utf8").read()
check("install.sh --uninstall przekazuje do uninstall.sh",
      'exec bash "$SRC/uninstall.sh" "$@"' in source)
check("instalator kopiuje uninstall.sh do /opt/wg-panel",
      'install -m 755 "$SRC/uninstall.sh" "$DEST/uninstall.sh"' in source)

print("\n" + "=" * 60)
if FAILURES:
    print(f"NIEUDANE ({len(FAILURES)}):")
    for item in FAILURES:
        print("  -", item)
    sys.exit(1)
print("Wszystkie testy przeszly.")
