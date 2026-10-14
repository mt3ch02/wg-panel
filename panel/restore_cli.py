"""Odtworzenie calego Pi z pelnej kopii na NAS - uzywa go `install.sh --restore`.

Trzy kroki, bo instalator musi zrobic swoje pomiedzy nimi:

  fetch   (root, przed instalacja)  pyta o NAS i haslo kopii, pokazuje liste kopii,
                                     pobiera wybrana, sprawdza sume i odszyfrowuje; klucz
                                     serwera i urzad certyfikacji zapisuje od razu na miejsce,
                                     reszte odklada do pliku dla kroku "import"
  import  (konto wg-panel, przed startem uslug)  wpisuje kopie do bazy panelu
  apply   (konto wg-panel, po starcie uslug)     podnosi tunel z odtworzonej konfiguracji

Odpowiedzi mozna podac zmiennymi (testy, automatyzacja): WGPANEL_RESTORE_HOST, _SHARE,
_PATH, _USER, _PASS, _PASSPHRASE, _PICK (numer kopii, domyslnie 1 = najnowsza).
Kod wyjscia fetch: 0 - pelna kopia (klucz na miejscu), 3 - kopia bez klucza serwera
(instalator zapyta wtedy o klucz), inny - blad.
"""
from __future__ import annotations

import getpass
import json
import os
import sys

from . import db, export

ENV = "WGPANEL_RESTORE_"
WG_DIR = os.environ.get("WGPANEL_WGDIR", "/etc/wireguard")
CA_DIR = os.path.join(WG_DIR, "wg-panel-ca")
EN = not os.environ.get("INSTALL_LANG", "en").lower().startswith("pl")


def t(pl: str, en: str) -> str:
    return en if EN else pl


def ask(label: str, name: str, default: str = "", secret: bool = False) -> str:
    value = os.environ.get(ENV + name)
    if value is not None:
        return value
    prompt = f"    {label}" + (f" [{default}]" if default else "") + ": "
    answer = getpass.getpass(prompt) if secret else input(prompt)
    return answer.strip() or default


def fail(message: str, code: int = 1) -> int:
    print(f"\033[1;31m[x]\033[0m {message}", file=sys.stderr)
    return code


def write_private(path: str, content: str, mode: int = 0o600) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    os.chmod(os.path.dirname(path), 0o700)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf8") as handle:
        handle.write(content)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def fetch(out_path: str) -> int:
    print(t("\n    Odtworzenie z kopii na NAS. Potrzebne: dane NAS i hasło kopii\n"
            "    (to z menedżera haseł).\n",
            "\n    Restore from a backup on a NAS. You need: the NAS details and the\n"
            "    backup password (the one in your password manager).\n"))
    cfg = {
        "smb_host": ask(t("Adres NAS", "NAS address"), "HOST"),
        "smb_share": ask(t("Udział", "Share"), "SHARE", "backup"),
        "smb_path": ask(t("Katalog na udziale", "Folder on the share"), "PATH", "wg-panel"),
        "smb_user": ask(t("Użytkownik", "User"), "USER"),
        "smb_pass": ask(t("Hasło NAS (nie będzie widoczne)",
                          "NAS password (hidden)"), "PASS", secret=True),
    }
    try:
        names = export.list_smb(cfg)
    except Exception as exc:  # noqa: BLE001
        return fail(export.smb_problem(cfg, exc))
    if not names:
        return fail(t(f"W {cfg['smb_share']}/{cfg['smb_path']} nie ma kopii panelu.",
                      f"No panel backups in {cfg['smb_share']}/{cfg['smb_path']}."))
    print(t("\n    Kopie na NAS (najnowsza pierwsza):", "\n    Backups on the NAS (newest first):"))
    shown = names[:10]
    for number, name in enumerate(shown, 1):
        kind = t("zaszyfrowana", "encrypted") if name.endswith(".enc") else t("jawna", "plain")
        print(f"      {number}) {name}  ({kind})")
    pick = ask(t("Którą odtworzyć", "Which one to restore"), "PICK", "1")
    if not pick.isdigit() or not 1 <= int(pick) <= len(shown):
        return fail(t("nie ma takiej pozycji", "no such item"))
    name = shown[int(pick) - 1]
    try:
        blob = export.download_smb(cfg, name)
        try:
            expected = export.download_smb(cfg, name + ".sha256").decode().split()[0]
        except (ValueError, IndexError, UnicodeDecodeError):
            expected = ""
        if expected and export.sha256(blob) != expected:
            return fail(t("suma kontrolna kopii się nie zgadza - plik uszkodzony, nie odtwarzam",
                          "the backup checksum doesn't match - damaged file, not restoring"))
        passphrase = ""
        if name.endswith(".enc"):
            passphrase = ask(t("Hasło kopii (nie będzie widoczne)", "Backup password (hidden)"),
                             "PASSPHRASE", secret=True)
        data = export.parse(blob, passphrase)
    except Exception as exc:  # noqa: BLE001
        return fail(t(f"nie udało się otworzyć kopii: {exc}", f"couldn't open the backup: {exc}"))

    # dane NAS wpisane przed chwila - zeby kopie dzialaly od razu, takze z kopii podstawowej
    settings = {row["key"]: row for row in data.get("settings", [])}
    for key, value in cfg.items():
        if key not in settings or not settings[key].get("value"):
            data.setdefault("settings", []).append({"key": key, "value": value})
    system = data.pop("system", None) or {}
    write_private(out_path, json.dumps(data))
    if not system.get("server_key"):
        print(t(f"\n    Odczytano: {name} - to kopia BEZ klucza serwera (podstawowa albo ze starszej\n"
                "    wersji). Osoby i ustawienia wrócą, ale o klucz serwera zapytam za chwilę.",
                f"\n    Read: {name} - a backup WITHOUT the server key (basic or from an older\n"
                "    version). People and settings will come back; I'll ask about the server key next."))
        return 3
    write_private(os.path.join(WG_DIR, "server_private.key"), system["server_key"].strip() + "\n")
    ca = system.get("ca") or {}
    if ca.get("crt") and ca.get("key"):
        write_private(os.path.join(CA_DIR, "ca.key"), ca["key"])
        write_private(os.path.join(CA_DIR, "ca.crt"), ca["crt"], 0o644)
        write_private(os.path.join(CA_DIR, "ca.json"), ca.get("json") or "{}")
    peers = len(data.get("peers", []))
    print(t(f"\n    Odczytano: {name} - pełna kopia, {peers} osób. Klucz serwera i certyfikat\n"
            "    są na miejscu - dotychczasowe pliki osób będą działać.",
            f"\n    Read: {name} - a full backup, {peers} people. The server key and certificate\n"
            "    are in place - everyone's existing files will keep working."))
    return 0


def do_import(path: str) -> int:
    with open(path, encoding="utf8") as handle:
        data = json.load(handle)
    conn = db.connect(os.environ.get("WGPANEL_DB", "/var/lib/wg-panel/panel.db"))
    db.init_defaults(conn)
    summary = export.restore(conn, data, with_users=bool(data.get("users")))
    db.log(conn, "instalator", "backup.restore", f"install.sh --restore: {summary}")
    print(t(f"    Baza panelu odtworzona: {summary['peers']} osób, {summary['tiers']} tierów, "
            f"{summary['rules']} reguł.",
            f"    Panel database restored: {summary['peers']} people, {summary['tiers']} tiers, "
            f"{summary['rules']} rules."))
    return 0


def do_apply() -> int:
    from . import helperclient, model
    conn = db.connect(os.environ.get("WGPANEL_DB", "/var/lib/wg-panel/panel.db"))
    try:
        pubkey = helperclient.call("server_pubkey", {"settings": model.settings_only(conn)})
        db.set_setting(conn, "server_pubkey", pubkey.get("pubkey", ""))
        value = model.build(conn)
        result = helperclient.call("apply", dict(value, confirm_seconds=0), timeout=120)
        model.mark_applied(conn, value, result)
    except Exception as exc:  # noqa: BLE001
        return fail(t(f"tunel nie wstał: {exc} - w panelu kliknij Zastosuj zmiany",
                      f"the tunnel did not come up: {exc} - click Apply changes in the panel"))
    db.log(conn, "instalator", "apply.ok", "install.sh --restore")
    print(t(f"    Tunel podniesiony. Klucz publiczny serwera: {pubkey.get('pubkey', '?')}",
            f"    Tunnel is up. Server public key: {pubkey.get('pubkey', '?')}"))
    return 0


def do_snapshot() -> int:
    """Instalator przed aktualizacja: stan panelu do pliku na Pi - gdyby nowa wersja cos
    popsula, wracasz do niego w Kopiach (plakietka "sprzed aktualizacji")."""
    path = os.environ.get("WGPANEL_DB", "/var/lib/wg-panel/panel.db")
    if not os.path.exists(path):
        return 0
    conn = db.connect(path)
    name = export.snapshot_local(conn, "instalator", label="przed-aktualizacja")
    print(t(f"    Stan sprzed aktualizacji zapisany: {name}", f"    Pre-update state saved: {name}"))
    return 0


def main(argv: list) -> int:
    if len(argv) < 1 or argv[0] not in ("fetch", "import", "apply", "snapshot"):
        return fail("uzycie: python3 -m panel.restore_cli fetch PLIK | import PLIK | apply | snapshot")
    if argv[0] == "snapshot":
        return do_snapshot()
    if argv[0] == "fetch":
        return fetch(argv[1])
    if argv[0] == "import":
        return do_import(argv[1])
    return do_apply()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
