"""Odtworzenie calego Pi z pelnej kopii na NAS - uzywa go `install.sh --restore`.

Trzy kroki, bo instalator musi zrobic swoje pomiedzy nimi:

  fetch   (root, przed instalacja)  kopia z pliku (podanego) albo z udzialu NAS (pyta o dane
                                     i pokazuje liste), sprawdza sume, pyta o haslo kopii
                                     i odszyfrowuje; porownuje nazwe i adres serwera z kopia;
                                     klucz serwera i urzad certyfikacji zapisuje od razu na
                                     miejsce, opcje instalatora do PLIK.options, reszte do
                                     PLIK dla kroku "import"
  check   (root, kiedykolwiek)      proba: to samo co fetch, ale niczego nie zapisuje
  import PLIK --test                 jak import, a do tego serwer testowy (bez kopii automatycznych,
                                     powiadomien i wydawania plikow osob)
  import  (konto wg-panel, przed startem uslug)  wpisuje kopie do bazy panelu
  apply   (konto wg-panel, po starcie uslug)     podnosi tunel z odtworzonej konfiguracji

Odpowiedzi mozna podac zmiennymi (testy, automatyzacja): WGPANEL_RESTORE_HOST, _SHARE,
_PATH, _USER, _PASS, _PASSPHRASE, _PICK (numer kopii, domyslnie 1 = najnowsza), _HOSTNAME
(t/n - ustawic nazwe z kopii), _LOCAL_IPS (adresy tego systemu, po przecinku - testy).
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


def _smb_settings() -> dict:
    print(t("\n    Kopia z udziału SMB (NAS). Potrzebne: dane udziału i hasło kopii\n"
            "    (to z menedżera haseł).\n",
            "\n    Backup from an SMB share (NAS). You need: the share details and the\n"
            "    backup password (the one in your password manager).\n"))
    return {
        "smb_host": ask(t("Adres NAS", "NAS address"), "HOST"),
        "smb_share": ask(t("Udział", "Share"), "SHARE", "backup"),
        "smb_path": ask(t("Katalog na udziale", "Folder on the share"), "PATH", "wg-panel"),
        "smb_user": ask(t("Użytkownik", "User"), "USER"),
        "smb_pass": ask(t("Hasło do udziału (nie będzie widoczne)",
                          "Share password (hidden)"), "PASS", secret=True),
    }


def _obtain(source: str) -> tuple:
    """(nazwa, zawartosc, dane udzialu) - z pliku albo z udzialu; suma sprawdzona.
    Blad -> wyjatek ValueError z komunikatem dla czlowieka."""
    if source:
        if not os.path.isfile(source):
            raise ValueError(t(f"nie ma pliku {source}", f"no such file: {source}"))
        with open(source, "rb") as handle:
            blob = handle.read()
        expected = ""
        if os.path.isfile(source + ".sha256"):
            with open(source + ".sha256", encoding="utf8", errors="replace") as handle:
                expected = (handle.read().split() or [""])[0]
        if expected and export.sha256(blob) != expected:
            raise ValueError(t("suma kontrolna kopii się nie zgadza (plik .sha256 obok) - plik uszkodzony",
                               "the backup checksum doesn't match (the .sha256 file next to it) - damaged file"))
        print(t(f"\n    Kopia z pliku: {source}" + ("" if expected else " (bez pliku .sha256 - suma niesprawdzona)"),
                f"\n    Backup from a file: {source}" + ("" if expected else " (no .sha256 file - checksum not checked)")))
        return os.path.basename(source), blob, {}
    cfg = _smb_settings()
    try:
        names = export.list_smb(cfg)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(export.smb_problem(cfg, exc)) from exc
    if not names:
        raise ValueError(t(f"W {cfg['smb_share']}/{cfg['smb_path']} nie ma kopii panelu.",
                           f"No panel backups in {cfg['smb_share']}/{cfg['smb_path']}."))
    print(t("\n    Kopie na udziale (najnowsza pierwsza):", "\n    Backups on the share (newest first):"))
    shown = names[:10]
    for number, name in enumerate(shown, 1):
        kind = t("zaszyfrowana", "encrypted") if name.endswith(".enc") else t("jawna", "plain")
        print(f"      {number}) {name}  ({kind})")
    pick = ask(t("Którą odtworzyć", "Which one to restore"), "PICK", "1")
    if not pick.isdigit() or not 1 <= int(pick) <= len(shown):
        raise ValueError(t("nie ma takiej pozycji", "no such item"))
    name = shown[int(pick) - 1]
    blob = export.download_smb(cfg, name)
    try:
        expected = export.download_smb(cfg, name + ".sha256").decode().split()[0]
    except Exception:  # noqa: BLE001 - starsza kopia bez pliku sumy
        expected = ""
    if expected and export.sha256(blob) != expected:
        raise ValueError(t("suma kontrolna kopii się nie zgadza - plik uszkodzony, nie odtwarzam",
                           "the backup checksum doesn't match - damaged file, not restoring"))
    return name, blob, cfg


def _open(source: str) -> tuple:
    """(nazwa, dane kopii, dane udzialu) - pobrana, sprawdzona i odszyfrowana."""
    name, blob, cfg = _obtain(source)
    passphrase = ""
    if export.cryptobox.is_encrypted(blob):
        passphrase = ask(t("Hasło kopii (nie będzie widoczne)", "Backup password (hidden)"),
                         "PASSPHRASE", secret=True)
    try:
        data = export.parse(blob, passphrase)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(t(f"nie udało się otworzyć kopii: {exc}", f"couldn't open the backup: {exc}")) from exc
    return name, data, cfg


def backup_host(data: dict) -> str:
    """Nazwa Pi, z ktorego jest kopia: zapisana w kopii albo (starsze kopie) z urzedu certyfikacji."""
    name = (data.get("host") or {}).get("name") or ""
    if name:
        return name
    ca = ((data.get("system") or {}).get("ca") or {})
    try:
        return (json.loads(ca.get("json") or "{}").get("dns") or [""])[0]
    except (ValueError, AttributeError, IndexError):
        return ""


def local_addresses() -> set:
    import subprocess
    try:
        out = subprocess.run(["ip", "-4", "-o", "addr", "show"], capture_output=True, text=True,
                             timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return set()
    return {part.split("/")[0] for line in out.splitlines() for part in line.split()[3:4]}


def differences(data: dict) -> dict:
    """Czym ten system rozni sie od Pi z kopii: nazwa (urzad certyfikacji) i adres w LAN."""
    settings = {row["key"]: row.get("value") for row in data.get("settings", [])}
    out = {}
    # nazwa liczy sie tylko wtedy, gdy kopia niesie urzad certyfikacji (helper go wtedy uzyje)
    has_ca = bool(((data.get("system") or {}).get("ca") or {}).get("crt"))
    was, now = backup_host(data), export.host_name()
    if has_ca and was and was != now:
        out["host"] = (was, now)
    lan_ip = os.environ.get(ENV + "LOCAL_IPS")
    here = set(lan_ip.split(",")) if lan_ip is not None else local_addresses()
    wanted = settings.get("server_lan") or ""
    if wanted and here and wanted not in here:
        out["address"] = (wanted, ", ".join(sorted(a for a in here if not a.startswith("127."))) or "?")
    return out


def describe(name: str, data: dict) -> None:
    import time
    settings = {row["key"]: row.get("value") for row in data.get("settings", [])}
    system = data.get("system") or {}
    made = data.get("created_at")
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(made)) if made else "?"
    yes, no = t("tak", "yes"), t("nie", "no")
    options = data.get("install") or {}
    rows = [
        (t("plik", "file"), name),
        (t("zrobiona", "made"), when),
        (t("wersja panelu", "panel version"), data.get("version") or t("starsza niż 1.4.0", "older than 1.4.0")),
        (t("rodzaj", "kind"), t("pełna", "full") if data.get("full") else t("podstawowa (bez kluczy i haseł)", "basic (no keys or passwords)")),
        (t("osoby", "people"), str(len(data.get("peers", [])))),
        (t("klucz serwera", "server key"), yes if system.get("server_key") else no),
        (t("urząd certyfikacji HTTPS", "HTTPS certificate authority"), yes if (system.get("ca") or {}).get("crt") else no),
        (t("nazwa serwera", "server name"), backup_host(data) or "?"),
        (t("adres serwera w LAN", "server address in the LAN"), settings.get("server_lan") or "?"),
        (t("port WireGuarda", "WireGuard port"), settings.get("listen_port") or "?"),
        (t("opcje instalatora", "installer options"),
         ", ".join(f"{k}={v}" for k, v in options.items()) if options
         else t("brak (starsza kopia)", "none (older backup)") if "install" not in data
         else t("brak - domyślne", "none - defaults")),
    ]
    width = max(len(label) for label, _value in rows)
    print()
    for label, value in rows:
        print(f"    {label.ljust(width)}  {value}")


def explain(diff: dict) -> None:
    if "host" in diff:
        was, now = diff["host"]
        print(t(f"\n    [!] Kopia jest z serwera o nazwie \"{was}\", a ten ma nazwę \"{now}\".\n"
                "        Z inną nazwą panel zrobi nowy urząd certyfikacji HTTPS i na każdym\n"
                "        urządzeniu trzeba będzie od nowa zainstalować ca.crt.",
                f"\n    [!] The backup is from a server named \"{was}\", this one is named \"{now}\".\n"
                "        With a different name the panel creates a new HTTPS certificate authority\n"
                "        and ca.crt has to be installed again on every device."))
    if "address" in diff:
        was, now = diff["address"]
        print(t(f"\n    [!] W kopii adres serwera w LAN to {was}, a ten ma: {now}.\n"
                "        Panel weźmie adres tej maszyny. Router musi przekierowywać port WireGuarda\n"
                f"        na nowy adres - popraw przekierowanie albo ustaw rezerwację DHCP na {was}.",
                f"\n    [!] In the backup the server address in the LAN is {was}, this one has: {now}.\n"
                "        The panel will use this machine's address. The router must forward the WireGuard\n"
                f"        port to the new address - fix the port forward or set a DHCP reservation for {was}."))


def set_hostname(name: str) -> bool:
    """Nazwa systemu jak w kopii: hostnamectl i wpis 127.0.1.1 w /etc/hosts (inaczej sudo
    narzeka, ze nie zna wlasnej nazwy)."""
    import re
    import subprocess
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", name):
        return False
    try:
        done = subprocess.run(["hostnamectl", "set-hostname", name], capture_output=True,
                              timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError):
        done = False
    if not done:
        return False
    hosts = os.environ.get("WGPANEL_HOSTS_FILE", "/etc/hosts")
    try:
        with open(hosts, encoding="utf8") as handle:
            lines = handle.read().splitlines()
        lines = [line for line in lines if not line.startswith("127.0.1.1")]
        lines.append(f"127.0.1.1\t{name}")
        with open(hosts, "w", encoding="utf8") as handle:
            handle.write("\n".join(lines) + "\n")
    except OSError:
        pass
    return True


def check(source: str) -> int:
    """Proba odtworzenia: pobiera, sprawdza sume, odszyfrowuje i opisuje kopie. Niczego nie
    zapisuje i nie zmienia - mozna na dzialajacym serwerze, zeby sprawdzic haslo kopii."""
    try:
        name, data, _cfg = _open(source)
    except ValueError as exc:
        return fail(str(exc))
    describe(name, data)
    if data.get("full") and (data.get("system") or {}).get("server_key"):
        print(t("\n    Kopia otwiera się tym hasłem i ma wszystko do odtworzenia serwera.",
                "\n    The backup opens with this password and has everything to restore the server."))
    else:
        print(t("\n    Kopia się otwiera, ale bez klucza serwera - po odtworzeniu każda osoba\n"
                "    potrzebowałaby nowego pliku. Do odtwarzania całego serwera używaj pełnej kopii.",
                "\n    The backup opens, but has no server key - after restoring everyone would need\n"
                "    a new file. To restore a whole server, use a full backup."))
    diff = differences(data)
    if diff:
        print(t("\n    Gdyby odtwarzać ją na TYM systemie:", "\n    If it were restored on THIS system:"))
        explain(diff)
    print(t("\n    Nic nie zostało zmienione.", "\n    Nothing was changed."))
    return 0


def fetch(out_path: str, source: str = "") -> int:
    try:
        name, data, cfg = _open(source)
    except ValueError as exc:
        return fail(str(exc))
    describe(name, data)
    diff = differences(data)
    explain(diff)
    if "host" in diff:
        was = diff["host"][0]
        answer = ask(t(f"Ustawić nazwę tego serwera na \"{was}\"? [T/n]",
                       f"Set this server's name to \"{was}\"? [Y/n]"), "HOSTNAME", "t").lower()
        if answer in ("t", "tak", "y", "yes"):
            if set_hostname(was):
                print(t(f"    Nazwa ustawiona: {was}", f"    Name set: {was}"))
            else:
                print(t(f"    Nie udało się ustawić nazwy - ustaw ją sam: sudo hostnamectl set-hostname {was}",
                        f"    Couldn't set the name - set it yourself: sudo hostnamectl set-hostname {was}"))
        else:
            print(t(f"    Nazwa zostaje: {diff['host'][1]} - panel zrobi nowy urząd certyfikacji.",
                    f"    The name stays: {diff['host'][1]} - the panel will create a new certificate authority."))
    # dane udzialu wpisane przed chwila - zeby kopie dzialaly od razu, takze z kopii podstawowej
    settings = {row["key"]: row for row in data.get("settings", [])}
    for key, value in cfg.items():
        if key not in settings or not settings[key].get("value"):
            data.setdefault("settings", []).append({"key": key, "value": value})
    options = data.pop("install", None) or {}
    data.pop("host", None)
    write_private(out_path + ".options", "".join(
        f"{key}={value}\n" for key, value in options.items()
        if key in export.INSTALL_KEYS and "\n" not in str(value)))
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


def do_import(path: str, test: bool = False) -> int:
    with open(path, encoding="utf8") as handle:
        data = json.load(handle)
    conn = db.connect(os.environ.get("WGPANEL_DB", "/var/lib/wg-panel/panel.db"))
    db.init_defaults(conn)
    summary = export.restore(conn, data, with_users=bool(data.get("users")))
    db.log(conn, "instalator", "backup.restore", f"install.sh --restore: {summary}")
    # adres, interfejs i (gdy nie pasuje) siec domowa - z tej maszyny, nie z kopii
    from . import firstrun
    labels = {"server_lan": t("adres serwera w LAN", "server address in the LAN"),
              "wan_if": t("interfejs do internetu", "internet interface"),
              "lan": t("sieć domowa", "home network")}
    for key, old, new in firstrun.follow(conn, restored=True):
        db.log(conn, "instalator", "settings.update", f"{key}: {old} -> {new} (z tej maszyny)")
        print(t(f"    {labels[key]}: {new} (w kopii było {old or '-'}) - wzięte z tej maszyny",
                f"    {labels[key]}: {new} (the backup had {old or '-'}) - taken from this machine"))
    print(t(f"    Baza panelu odtworzona: {summary['peers']} osób, {summary['tiers']} tierów, "
            f"{summary['rules']} reguł.",
            f"    Panel database restored: {summary['peers']} people, {summary['tiers']} tiers, "
            f"{summary['rules']} rules."))
    if test:
        db.set_setting(conn, "test_mode", "1")
        db.log(conn, "instalator", "test.on", "install.sh --restore --test")
        print(t("    Serwer TESTOWY: kopie automatyczne i powiadomienia wyłączone, pliki osób nie są\n"
                "    wydawane. Gdy ta maszyna ma zostać właściwym serwerem: przycisk w panelu albo\n"
                "    wgctl test off.",
                "    TEST server: automatic backups and notifications are off, people's files are not\n"
                "    issued. When this machine is to become the real server: the button in the panel\n"
                "    or wgctl test off."))
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
    usage = ("uzycie: python3 -m panel.restore_cli fetch PLIK [KOPIA] | check [KOPIA] | "
             "import PLIK | apply | snapshot")
    if len(argv) < 1 or argv[0] not in ("fetch", "check", "import", "apply", "snapshot"):
        return fail(usage)
    if argv[0] == "snapshot":
        return do_snapshot()
    if argv[0] == "check":
        return check(argv[1] if len(argv) > 1 else "")
    if argv[0] == "fetch":
        return fetch(argv[1], argv[2] if len(argv) > 2 else "")
    if argv[0] == "import":
        return do_import(argv[1], test="--test" in argv[2:])
    return do_apply()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
