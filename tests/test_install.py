#!/usr/bin/env python3
"""Instalator: komunikaty po angielsku (domyslnie) i po polsku (INSTALL_LANG=pl).

Instalatora nie da sie tu uruchomic naprawde (instaluje uslugi systemowe), wiec atrapa `id`
udaje zwyklego uzytkownika - skrypt konczy sie na pierwszym sprawdzeniu, niczego nie zmieniajac.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "install.sh")
FAILURES = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  [{'OK  ' if condition else 'BLAD'}] {label}"
          + (f"  ({detail})" if detail and not condition else ""))
    if not condition:
        FAILURES.append(label)


source = open(SCRIPT, encoding="utf8").read()
check("skladnia bash poprawna", subprocess.run(["bash", "-n", SCRIPT]).returncode == 0)
plain = [line.strip() for line in source.splitlines()
         if re.match(r'\s*(info|warn|die) "[^$]', line)]
check("kazdy komunikat przechodzi przez t (dwa jezyki)", not plain, "; ".join(plain[:3]))
check("podsumowanie w obu jezykach", "\nGotowe.\n" in source and "\nDone.\n" in source)
check("przejecie istniejacego wg0.conf: stan sprzed panelu zapisany raz (droga powrotna --restore)",
      'BEFORE=/root/wg-panel-before' in source and '[ ! -f "$BEFORE/wg0.conf" ]' in source
      and 'cp -p /etc/wireguard/wg0.conf "$BEFORE/"' in source
      and "/root/wg-panel-before" in open(os.path.join(ROOT, "uninstall.sh"), encoding="utf8").read())
check("bez nieistniejacej opcji wgctl set --panel w podpowiedziach", "--panel on" not in source)
check("instalator sam instaluje smbclient, bez pytan (debconf Samby)",
      "DEBIAN_FRONTEND=noninteractive apt-get install -y smbclient" in source)
unit = open(os.path.join(ROOT, "systemd", "wg-panel.service"), encoding="utf8").read()
families = next(line for line in unit.splitlines() if line.startswith("RestrictAddressFamilies="))
check("usluga panelu pozwala na AF_NETLINK (smbclient czyta nim interfejsy)", "AF_NETLINK" in families,
      families)

fake = tempfile.mkdtemp(prefix="wgpanel-install-")
with open(os.path.join(fake, "id"), "w", encoding="utf8") as handle:
    handle.write("#!/bin/sh\necho 1000\n")
os.chmod(os.path.join(fake, "id"), 0o755)


def run(**extra) -> str:
    env = {k: v for k, v in os.environ.items() if k != "INSTALL_LANG"}
    env.update(PATH=fake + os.pathsep + os.environ["PATH"], **extra)
    out = subprocess.run(["bash", SCRIPT], env=env, capture_output=True, text=True, timeout=30)
    return out.stdout + out.stderr


check("domyslnie po angielsku", "run it with sudo" in run())
check("domyslnie po angielsku takze przy LANG=pl_PL",
      "run it with sudo" in run(LANG="pl_PL.UTF-8"))
check("INSTALL_LANG=pl - po polsku", "uruchom przez sudo" in run(INSTALL_LANG="pl"))

# instalator sam dodaje konto (np. pi) do grupy wg-panel - fragment z atrapami id/usermod
start = source.index("GROUP_ADDED=0")
end = source.index("fi\nfi\n", start) + len("fi\nfi\n")
snippet = source[start:end] + 'echo "GROUP_ADDED=$GROUP_ADDED"\n'


def group_case(sudo_user: str, groups: str) -> tuple:
    bindir = tempfile.mkdtemp(prefix="wgpanel-grp-")
    log = os.path.join(bindir, "log")
    for name, body in (("id", f'if [ "$1" = "-nG" ]; then echo "{groups}"; fi\nexit 0\n'),
                       ("usermod", f'echo "usermod $*" >> {log}\n')):
        with open(os.path.join(bindir, name), "w", encoding="utf8") as handle:
            handle.write("#!/bin/sh\n" + body)
        os.chmod(os.path.join(bindir, name), 0o755)
    env = dict(os.environ, PATH=bindir + os.pathsep + os.environ["PATH"], SUDO_USER=sudo_user)
    out = subprocess.run(["bash", "-c", snippet], env=env, capture_output=True, text=True)
    calls = open(log).read() if os.path.exists(log) else ""
    return out.stdout.strip(), calls


check("instalator: pi spoza grupy - dodany do wg-panel",
      group_case("pi", "pi adm sudo") == ("GROUP_ADDED=1", "usermod -aG wg-panel pi\n"))
check("instalator: pi juz w grupie - bez zmian", group_case("pi", "pi adm wg-panel") == ("GROUP_ADDED=0", ""))
check("instalator uruchomiony bez sudo - nikogo nie dodaje", group_case("", "") == ("GROUP_ADDED=0", ""))
check("podsumowanie instalatora mowi, co zrobiono z grupa (oba jezyki)", source.count("$GROUP_LINE") == 2
      and "newgrp wg-panel" in source)

# wgctl: rozroznia "nie jestes w grupie" od "jestes, ale sesja zalogowala sie wczesniej"
import runpy  # noqa: E402
import types  # noqa: E402
tool = runpy.run_path(os.path.join(ROOT, "wgctl"), run_name="wgctl_test")


def hint(members: list, session: list, exists: bool = True) -> str:
    real_grp, real_pwd, real_groups = sys.modules.get("grp"), sys.modules.get("pwd"), os.getgroups

    def getgrnam(_name):
        if not exists:
            raise KeyError(_name)
        return types.SimpleNamespace(gr_mem=members, gr_gid=4242)
    sys.modules["grp"] = types.SimpleNamespace(getgrnam=getgrnam)
    sys.modules["pwd"] = types.SimpleNamespace(getpwuid=lambda _uid: types.SimpleNamespace(pw_name="pi"))
    os.getgroups = lambda: session
    try:
        return tool["group_hint"]("/var/lib/wg-panel")
    finally:
        sys.modules["grp"], sys.modules["pwd"], os.getgroups = real_grp, real_pwd, real_groups


check("wgctl: w grupie, ale stara sesja - mowi o ponownym zalogowaniu / newgrp",
      "jestes juz w grupie" in hint(["pi"], [1000]) and "newgrp wg-panel" in hint(["pi"], [1000]))
check("wgctl: spoza grupy - podaje usermod z nazwa konta",
      "sudo usermod -aG wg-panel pi" in hint([], [1000]))
check("wgctl: brak grupy - pyta, czy panel jest zainstalowany", "nie ma grupy" in hint([], [], exists=False))

print("\n" + "=" * 60)
if FAILURES:
    print(f"NIEUDANE ({len(FAILURES)}):")
    for item in FAILURES:
        print("  -", item)
    sys.exit(1)
print("Wszystkie testy przeszly.")
