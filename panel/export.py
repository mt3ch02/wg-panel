"""Eksport calej konfiguracji, odtwarzanie i kopie zapasowe.

Kopia to pojedynczy JSON: ustawienia, tiery, peery, reguly i konta.
Z tego pliku da sie odtworzyc panel na czystej karcie - wg0.conf i reguly
iptables zostana wygenerowane od nowa przy pierwszym zastosowaniu zmian.

Cele kopii:
  local - katalog na dysku (moze byc punkt montowania udzialu z NAS)
  smb   - wysylka przez smbclient wprost na udzial NAS
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import re
import sqlite3
import subprocess
import time

from . import cryptobox, db

FORMAT_VERSION = 1
SAFE_PATH = re.compile(r"^[A-Za-z0-9._/-]*$")
SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")

# stan roboczy panelu - nie nalezy do konfiguracji i nie moze trafiac do migawek,
# bo migawka siedzi w tej samej tabeli i zaczelaby zawierac sama siebie
RUNTIME_SETTINGS = {
    "applied_state", "applied_hash", "applied_fingerprint",
    "drift_findings", "drift_checked_at", "confirm_token", "rolled_back",
    # stan sprzed zastosowania, trzymany do potwierdzenia, i tryb potwierdzania tej zmiany
    "before_applied_state", "before_applied_hash", "before_applied_fingerprint",
    "confirm_auto", "confirm_since",
}

EXPORT_TABLES = {
    "settings": "SELECT key, value FROM settings",
    "tiers": "SELECT * FROM tiers",
    "peers": "SELECT * FROM peers",
    "rules": "SELECT * FROM rules",
    "users": "SELECT * FROM users",
    "hosts": "SELECT * FROM hosts",
}

# tych ustawien nie wynosimy poza maszyne
SECRET_SETTINGS = {"smb_pass", "backup_passphrase", "alerts_token", "smtp_pass"}


def build(conn: sqlite3.Connection, include_secrets: bool = False) -> dict:
    out: dict = {
        "format": FORMAT_VERSION,
        "created_at": int(time.time()),
        "created_by": "wg-panel",
    }
    for name, query in EXPORT_TABLES.items():
        rows = [dict(row) for row in conn.execute(query)]
        if name == "settings":
            # stan roboczy nigdy nie idzie do kopii ani do migawek - to nie konfiguracja
            rows = [r for r in rows if r["key"] not in RUNTIME_SETTINGS]
            if not include_secrets:
                rows = [r for r in rows if r["key"] not in SECRET_SETTINGS]
        out[name] = rows
    counts = {name: len(out[name]) for name in EXPORT_TABLES}
    out["counts"] = counts
    return out


def serialize(conn: sqlite3.Connection, passphrase: str = "") -> tuple[bytes, bool]:
    payload = json.dumps(build(conn), ensure_ascii=False, indent=1).encode("utf8")
    if passphrase:
        return cryptobox.encrypt(payload, passphrase), True
    return payload, False


def filename(encrypted: bool, moment: float | None = None) -> str:
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(moment or time.time()))
    return f"wg-panel-{stamp}.json" + (".enc" if encrypted else "")


def parse(blob: bytes, passphrase: str = "") -> dict:
    if cryptobox.is_encrypted(blob):
        if not passphrase:
            raise ValueError("kopia jest zaszyfrowana - podaj haslo")
        blob = cryptobox.decrypt(blob, passphrase)
    data = json.loads(blob.decode("utf8"))
    if not isinstance(data, dict) or "peers" not in data:
        raise ValueError("to nie wyglada na kopie wg-panel")
    if int(data.get("format", 0)) > FORMAT_VERSION:
        raise ValueError("kopia pochodzi z nowszej wersji panelu")
    return data


def restore(conn: sqlite3.Connection, data: dict, with_users: bool = False) -> dict:
    """Zastepuje konfiguracje zawartoscia kopii. Konta zostaja, chyba ze poproszono inaczej."""
    summary = {}
    with conn:
        conn.execute("DELETE FROM rules")
        conn.execute("DELETE FROM peers")
        conn.execute("DELETE FROM tiers")
        if "hosts" in data:
            conn.execute("DELETE FROM hosts")
            for row in data["hosts"]:
                conn.execute(
                    """INSERT INTO hosts(id, name, address, address6, description)
                       VALUES (?, ?, ?, ?, ?)""",
                    (row.get("id"), row["name"], row.get("address", ""),
                     row.get("address6", ""), row.get("description", "")))
        for row in data.get("tiers", []):
            conn.execute(
                """INSERT INTO tiers(id, name, description, client_mode, client_list,
                                     host_access, rules_json, builtin)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (row.get("id"), row["name"], row.get("description", ""),
                 {"split": "lan"}.get(row.get("client_mode", "auto"), row.get("client_mode", "auto")),
                 row.get("client_list", ""), row.get("host_access", ""),
                 row.get("rules_json", "[]"), row.get("builtin", 0)),
            )
        for row in data.get("peers", []):
            conn.execute(
                """INSERT INTO peers(id, name, device, ip, pubkey, psk, privkey, tier_id,
                                     state, note, created_at, revoked_at, access_mode,
                                     starts_at, expires_at, expires_days,
                                     expires_from_use, first_seen, auto_state, ip6,
                                     tunnel_mode, tunnel_list, issued_allowed, panel_access)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (row.get("id"), row["name"], row.get("device", ""), row["ip"],
                 row["pubkey"], row.get("psk", ""), row.get("privkey"),
                 row.get("tier_id"), row.get("state", "active"), row.get("note", ""),
                 row.get("created_at", int(time.time())), row.get("revoked_at"),
                 row.get("access_mode", "none"), row.get("starts_at"),
                 row.get("expires_at"), row.get("expires_days"),
                 row.get("expires_from_use", 0), row.get("first_seen"),
                 row.get("auto_state", 0), row.get("ip6"),
                 row.get("tunnel_mode", ""), row.get("tunnel_list", ""),
                 row.get("issued_allowed"), row.get("panel_access", 0)),
            )
        for row in data.get("rules", []):
            conn.execute(
                """INSERT INTO rules(id, peer_id, position, action, dest, proto, ports,
                                     time_from, time_to, weekdays, comment, enabled)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (row.get("id"), row["peer_id"], row.get("position", 0), row["action"],
                 row["dest"], row.get("proto", "any"), row.get("ports", ""),
                 row.get("time_from", ""), row.get("time_to", ""),
                 row.get("weekdays", ""), row.get("comment", ""), row.get("enabled", 1)),
            )
        for row in data.get("settings", []):
            if row["key"] in SECRET_SETTINGS and not row.get("value"):
                continue
            if row["key"] in RUNTIME_SETTINGS:
                continue    # kopia z wersji, ktora je eksportowala - nie wskrzeszamy starego stanu
            conn.execute(
                "INSERT INTO settings(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (row["key"], row["value"]),
            )
        if with_users:
            conn.execute("DELETE FROM sessions")
            conn.execute("DELETE FROM users")
            for row in data.get("users", []):
                conn.execute(
                    """INSERT INTO users(id, username, pwd_hash, pwd_salt, totp_secret,
                                         created_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (row.get("id"), row["username"], row["pwd_hash"], row["pwd_salt"],
                     row.get("totp_secret"), row.get("created_at", int(time.time()))),
                )
        conn.execute("DELETE FROM settings WHERE key = 'applied_hash'")
        conn.execute("DELETE FROM settings WHERE key = 'applied_fingerprint'")

    db.normalize_legacy(conn)      # kopia ze starszej wersji: odcieci peerzy, alias $NAS
    for name in ("tiers", "peers", "rules", "hosts"):
        summary[name] = len(data.get(name, []))
    return summary


# --------------------------------------------------------------------------
# kopie zapasowe
# --------------------------------------------------------------------------

def sha256(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def run(conn: sqlite3.Connection, actor: str = "harmonogram", target: str | None = None) -> dict:
    """Robi kopie zgodnie z ustawieniami, liczy sume kontrolna i od razu ja sprawdza.

    target nadpisuje cel z ustawien dla tej jednej kopii (przyciski "Kopia lokalnie" / "Kopia na NAS"),
    bez zmiany celu kopii automatycznych."""
    cfg = db.settings(conn)
    blob, encrypted = serialize(conn, cfg.get("backup_passphrase", ""))
    name = filename(encrypted)
    digest = sha256(blob)
    staging = cfg.get("backup_dir") or "/var/lib/wg-panel/backups"
    target = target or cfg.get("backup_target", "local")

    record = {"ts": int(time.time()), "target": target, "path": "", "bytes": len(blob),
              "encrypted": int(encrypted), "ok": 0, "detail": "", "sha256": digest}
    try:
        os.makedirs(staging, exist_ok=True)
        try:
            # grupa wg-panel czyta kopie tak jak baze (te same sekrety, baza ma 660);
            # setgid - kopie zrobione z wgctl przez czlonka grupy tez naleza do wg-panel
            os.chmod(staging, 0o2770)
        except OSError:
            pass             # katalog innego konta - prawa nadal instalator
        local_path = os.path.join(staging, name)
        if os.path.exists(local_path):  # dwie kopie w tej samej sekundzie
            stem, _, suffix = name.partition(".")
            counter = 1
            while os.path.exists(local_path):
                name = f"{stem}-{counter}.{suffix}"
                local_path = os.path.join(staging, name)
                counter += 1
        with open(local_path, "wb") as handle:
            handle.write(blob)
        os.chmod(local_path, 0o640)
        sidecar = write_sidecar(local_path, name, digest)
        record["path"] = local_path

        if target == "smb":
            record["path"] = upload_smb(cfg, local_path, name)
            upload_smb(cfg, sidecar, name + ".sha256")
        record["ok"] = 1
        record["detail"] = "zaszyfrowana" if encrypted else "bez szyfrowania"
    except PermissionError:
        # np. wgctl uruchomione przez czlonka grupy przy prawach z wczesniejszej wersji
        record["detail"] = f"brak dostepu do katalogu kopii {staging} - zainstaluj panel ponownie"
    except Exception as exc:  # noqa: BLE001
        record["detail"] = str(exc)[:400]

    with conn:
        cursor = conn.execute(
            """INSERT INTO backups(ts, target, path, bytes, encrypted, ok, detail, sha256)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (record["ts"], record["target"], record["path"], record["bytes"],
             record["encrypted"], record["ok"], record["detail"], digest),
        )
    record["id"] = cursor.lastrowid
    db.log(conn, actor, "backup.ok" if record["ok"] else "backup.failed", record["detail"])
    prune(cfg, staging)

    if record["ok"] and cfg.get("backup_verify", "1") == "1":
        record["verify"] = verify(conn, record["id"])
    return record


def write_sidecar(local_path: str, name: str, digest: str) -> str:
    """Plik w formacie sha256sum -c: kopie da sie sprawdzic takze bez panelu."""
    sidecar = local_path + ".sha256"
    with open(sidecar, "w", encoding="utf8") as handle:
        handle.write(f"{digest}  {name}\n")
    os.chmod(sidecar, 0o640)
    return sidecar


def fetch(cfg: dict, record) -> bytes:
    """Pobiera plik kopii z miejsca, w ktorym faktycznie lezy."""
    path = record["path"]
    if record["target"] == "smb" and path.startswith("//"):
        name = path.rsplit("/", 1)[-1]
        return download_smb(cfg, name)
    with open(path, "rb") as handle:
        return handle.read()


def verify(conn: sqlite3.Connection, backup_id: int) -> dict:
    """Sprawdza sume kontrolna kopii i to, czy da sie ja otworzyc."""
    row = conn.execute("SELECT * FROM backups WHERE id = ?", (backup_id,)).fetchone()
    if row is None:
        return {"ok": False, "detail": "nie ma takiej kopii"}
    cfg = db.settings(conn)
    result = {"ok": False, "detail": ""}
    try:
        if not row["ok"]:
            raise ValueError("kopia nie zostala zapisana")
        if not row["sha256"]:
            raise ValueError("kopia nie ma zapisanej sumy kontrolnej (starsza wersja)")
        blob = fetch(cfg, row)
        actual = sha256(blob)
        if actual != row["sha256"]:
            raise ValueError(f"suma kontrolna sie nie zgadza: {actual[:12]}... "
                             f"zamiast {row['sha256'][:12]}... - plik zmieniony albo uszkodzony")
        detail = "suma zgodna"
        passphrase = cfg.get("backup_passphrase", "")
        if cryptobox.is_encrypted(blob) and passphrase:
            data = parse(blob, passphrase)
            detail += f", otwiera sie ({len(data.get('peers', []))} peerow)"
        elif not cryptobox.is_encrypted(blob):
            data = parse(blob)
            detail += f", otwiera sie ({len(data.get('peers', []))} peerow)"
        result = {"ok": True, "detail": detail}
    except Exception as exc:  # noqa: BLE001
        result = {"ok": False, "detail": str(exc)[:300]}

    with conn:
        conn.execute(
            "UPDATE backups SET verified_at = ?, verify_ok = ?, verify_detail = ? WHERE id = ?",
            (int(time.time()), int(result["ok"]), result["detail"], backup_id),
        )
    if not result["ok"]:
        from . import notify
        notify.raise_alert(conn, "backup", f"weryfikacja kopii {backup_id}",
                           f"Kopia #{backup_id} nie przeszla weryfikacji: {result['detail']}",
                           severity="alarm")
    return result


def verify_recent(conn: sqlite3.Connection, count: int = 3) -> list[dict]:
    rows = list(conn.execute(
        "SELECT id FROM backups WHERE ok = 1 ORDER BY id DESC LIMIT ?", (count,)))
    return [dict(verify(conn, row["id"]), id=row["id"]) for row in rows]


def upload_smb(cfg: dict, local_path: str, name: str) -> str:
    """Wysyla plik na udzial NAS przez smbclient."""
    host = (cfg.get("smb_host") or "").strip()
    share = (cfg.get("smb_share") or "").strip()
    path = (cfg.get("smb_path") or "").strip().strip("/")
    user = (cfg.get("smb_user") or "").strip()
    password = cfg.get("smb_pass") or ""

    for value, label in ((host, "adres"), (share, "udzial")):
        if not value:
            raise ValueError("kopia SMB: brak adresu albo udzialu NAS")
        if not SAFE_NAME.match(value.replace(":", "")):
            raise ValueError(f"kopia SMB: zly {label}")
    if path and (not SAFE_PATH.match(path) or ".." in path.split("/")):
        raise ValueError("kopia SMB: zla sciezka na udziale")
    if not SAFE_NAME.match(name):
        raise ValueError("kopia SMB: zla nazwa pliku")
    if not user or not password:
        raise ValueError("kopia SMB: brak uzytkownika albo hasla NAS")

    commands = ["prompt OFF"]
    if path:
        # katalog moze nie istniec - probujemy go zalozyc, blad ignorujemy
        commands.append(f'mkdir "{path}"')
        commands.append(f'cd "{path}"')
    commands.append(f'put "{local_path}" "{name}"')

    args = ["smbclient", f"//{host}/{share}"]
    env = dict(os.environ)
    if user:
        args += ["-U", user]
        env["PASSWD"] = password  # przez srodowisko, zeby nie bylo w liscie procesow
    else:
        args.append("-N")
    args += ["-c", "; ".join(commands)]

    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=180, env=env)
    except FileNotFoundError as exc:
        raise ValueError("brak smbclient - zainstaluj: apt install smbclient") from exc
    except subprocess.TimeoutExpired as exc:
        raise ValueError("smbclient nie odpowiedzial w 180 s") from exc

    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().split("\n")[-1]
        raise ValueError(f"smbclient: {detail}")
    return f"//{host}/{share}/{path + '/' if path else ''}{name}"


def _smb_base(cfg: dict) -> tuple[list[str], dict, str]:
    host = (cfg.get("smb_host") or "").strip()
    share = (cfg.get("smb_share") or "").strip()
    path = (cfg.get("smb_path") or "").strip().strip("/")
    user = (cfg.get("smb_user") or "").strip()
    for value, label in ((host, "adres"), (share, "udzial")):
        if not value or not SAFE_NAME.match(value.replace(":", "")):
            raise ValueError(f"kopia SMB: zly {label}")
    if path and (not SAFE_PATH.match(path) or ".." in path.split("/")):
        raise ValueError("kopia SMB: zla sciezka na udziale")
    args = ["smbclient", f"//{host}/{share}"]
    env = dict(os.environ)
    if user:
        args += ["-U", user]
        env["PASSWD"] = cfg.get("smb_pass") or ""
    else:
        args.append("-N")
    return args, env, path


def download_smb(cfg: dict, name: str) -> bytes:
    if not SAFE_NAME.match(name):
        raise ValueError("kopia SMB: zla nazwa pliku")
    args, env, path = _smb_base(cfg)
    handle, tmp = tempfile.mkstemp()
    os.close(handle)
    try:
        commands = ["prompt OFF"]
        if path:
            commands.append(f'cd "{path}"')
        commands.append(f'get "{name}" "{tmp}"')
        try:
            result = subprocess.run(args + ["-c", "; ".join(commands)], capture_output=True,
                                    text=True, timeout=180, env=env)
        except FileNotFoundError as exc:
            raise ValueError("brak smbclient - zainstaluj: apt install smbclient") from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip().split("\n")[-1]
            raise ValueError(f"smbclient: {detail}")
        with open(tmp, "rb") as handle_in:
            return handle_in.read()
    finally:
        os.remove(tmp)


def delete_smb(cfg: dict, name: str) -> bool:
    """Usuwa plik z udzialu NAS. False, gdy pliku juz tam nie bylo."""
    if not SAFE_NAME.match(name):
        raise ValueError("kopia SMB: zla nazwa pliku")
    args, env, path = _smb_base(cfg)
    commands = ["prompt OFF"] + ([f'cd "{path}"'] if path else []) + [f'del "{name}"']
    try:
        result = subprocess.run(args + ["-c", "; ".join(commands)], capture_output=True,
                                text=True, timeout=120, env=env)
    except FileNotFoundError as exc:
        raise ValueError("brak smbclient - zainstaluj: apt install smbclient") from exc
    except subprocess.TimeoutExpired as exc:
        raise ValueError("smbclient nie odpowiedzial w 120 s") from exc
    if result.returncode != 0:
        output = (result.stderr or "") + (result.stdout or "")
        if "NO_SUCH_FILE" in output or "OBJECT_NAME_NOT_FOUND" in output:
            return False
        raise ValueError("smbclient: " + output.strip().split("\n")[-1])
    return True


def delete_backups(conn: sqlite3.Connection, ids: list) -> dict:
    """Usuwa wybrane kopie: pliki (z .sha256) na karcie i na NAS, potem wpis w historii.

    Wpis znika tylko wtedy, gdy pliki udalo sie usunac (albo juz ich nie bylo) - inaczej
    stracilibysmy slad po pliku, ktory dalej lezy na NAS. Na NAS usuwamy wylacznie
    pod adresem z biezacych ustawien.
    """
    cfg = db.settings(conn)
    staging = os.path.realpath(cfg.get("backup_dir") or "/var/lib/wg-panel/backups")
    host = (cfg.get("smb_host") or "").strip()
    share = (cfg.get("smb_share") or "").strip()
    folder = (cfg.get("smb_path") or "").strip().strip("/")
    remote_prefix = f"//{host}/{share}/" + (f"{folder}/" if folder else "")
    summary = {"deleted": 0, "local": 0, "remote": 0, "problems": []}
    for backup_id in ids:
        row = conn.execute("SELECT * FROM backups WHERE id = ?", (backup_id,)).fetchone()
        if row is None:
            continue
        where = row["path"] or ""
        name = where.replace("\\", "/").rsplit("/", 1)[-1]
        try:
            if where.startswith("//"):
                if not host or not where.startswith(remote_prefix):
                    raise ValueError(f"kopia SMB: {name} lezy pod innym adresem niz w ustawieniach")
                for victim in (name, name + ".sha256"):
                    summary["remote"] += int(delete_smb(cfg, victim))
            # kopia robocza na karcie - zostaje tez przy kopii na NAS
            if name and is_backup_file(name):
                for victim in (name, name + ".sha256"):
                    local = os.path.realpath(os.path.join(staging, victim))
                    if os.path.dirname(local) == staging and os.path.exists(local):
                        os.remove(local)
                        summary["local"] += 1
        except (ValueError, OSError) as exc:
            summary["problems"].append(str(exc))
            continue
        with conn:
            conn.execute("DELETE FROM backups WHERE id = ?", (backup_id,))
        summary["deleted"] += 1
    return summary


def prune(cfg: dict, directory: str) -> None:
    keep = int(cfg.get("backup_keep", "14") or 0)
    if keep <= 0 or not os.path.isdir(directory):
        return
    try:
        names = os.listdir(directory)
    except OSError:
        return               # brak dostepu - sprzatanie przy nastepnej kopii panelu
    files = sorted(
        f for f in names
        if f.startswith("wg-panel-") and (f.endswith(".json") or f.endswith(".json.enc"))
    )
    for stale in files[:-keep]:
        for victim in (stale, stale + ".sha256"):
            try:
                os.remove(os.path.join(directory, victim))
            except OSError:
                pass


def due(cfg: dict, last_run: float, now: float | None = None) -> bool:
    """Czy wybila pora na kopie dobowa."""
    if cfg.get("backup_enabled") != "1":
        return False
    moment = time.localtime(now if now is not None else time.time())
    wanted = (cfg.get("backup_time") or "03:30").split(":")
    try:
        hour, minute = int(wanted[0]), int(wanted[1])
    except (ValueError, IndexError):
        hour, minute = 3, 30
    if (moment.tm_hour, moment.tm_min) != (hour, minute):
        return False
    return (now if now is not None else time.time()) - last_run > 90



def is_backup_file(name: str) -> bool:
    return name.startswith("wg-panel-") and (name.endswith(".json") or name.endswith(".json.enc"))


def local_files(cfg: dict) -> list[str]:
    directory = cfg.get("backup_dir") or ""
    if not os.path.isdir(directory):
        return []
    return sorted((f for f in os.listdir(directory) if is_backup_file(f)), reverse=True)


def snapshot_local(conn: sqlite3.Connection, actor: str, label: str = "przed") -> str:
    """Kopia obecnego stanu tylko na karte - zeby odtworzenie dalo sie cofnac."""
    cfg = db.settings(conn)
    blob, encrypted = serialize(conn, cfg.get("backup_passphrase", ""))
    staging = cfg.get("backup_dir") or "/var/lib/wg-panel/backups"
    os.makedirs(staging, exist_ok=True)
    stem = time.strftime("wg-panel-%Y%m%d-%H%M%S")
    name = f"{stem}-{label}.json" + (".enc" if encrypted else "")
    path = os.path.join(staging, name)
    with open(path, "wb") as handle:
        handle.write(blob)
    os.chmod(path, 0o640)
    digest = sha256(blob)
    write_sidecar(path, name, digest)
    with conn:
        conn.execute(
            """INSERT INTO backups(ts, target, path, bytes, encrypted, ok, detail, sha256)
               VALUES (?, 'local', ?, ?, ?, 1, ?, ?)""",
            (int(time.time()), path, len(blob), int(encrypted),
             "kopia stanu przed odtworzeniem", digest))
    db.log(conn, actor, "backup.snapshot", name)
    return name


def list_smb(cfg: dict) -> list[str]:
    """Kopie lezace na NAS - najnowsze pierwsze. Potrzebne na nowym systemie,
    ktory nie ma jeszcze wlasnej historii kopii."""
    args, env, path = _smb_base(cfg)
    commands = [f'cd "{path}"'] if path else []
    commands.append("ls")
    try:
        result = subprocess.run(args + ["-c", "; ".join(commands)], capture_output=True,
                                text=True, timeout=60, env=env)
    except FileNotFoundError as exc:
        raise ValueError("brak smbclient - zainstaluj: apt install smbclient") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().split("\n")[-1]
        raise ValueError(f"smbclient: {detail}")
    names = []
    for line in result.stdout.split("\n"):
        parts = line.split()
        if parts and is_backup_file(parts[0]):
            names.append(parts[0])
    return sorted(set(names), reverse=True)


def restore_from(conn: sqlite3.Connection, source: str, passphrase: str,
                 with_users: bool, actor: str) -> dict:
    """Odtwarza z kopii z historii ("id:N") albo z pliku w katalogu kopii ("file:nazwa").

    Najpierw pobiera plik (z NAS, jesli tam lezy) i sprawdza sume kontrolna,
    potem robi kopie obecnego stanu, dopiero na koncu nadpisuje baze.
    """
    cfg = db.settings(conn)
    kind, _, value = source.partition(":")
    if kind == "id" and value.isdigit():
        row = conn.execute("SELECT * FROM backups WHERE id = ? AND ok = 1",
                           (int(value),)).fetchone()
        if row is None:
            raise ValueError("nie ma takiej kopii w historii")
        blob = fetch(cfg, row)
        if row["sha256"] and sha256(blob) != row["sha256"]:
            raise ValueError("suma kontrolna sie nie zgadza - plik zmieniony albo uszkodzony, "
                             "nie odtwarzam")
        label = os.path.basename(row["path"])
    elif kind == "smb" and is_backup_file(value) and SAFE_NAME.match(value):
        blob = download_smb(cfg, value)
        try:
            expected = download_smb(cfg, value + ".sha256").decode().split()[0]
        except (ValueError, IndexError, UnicodeDecodeError):
            expected = ""
        if expected and sha256(blob) != expected:
            raise ValueError("suma kontrolna z pliku .sha256 na NAS sie nie zgadza - "
                             "nie odtwarzam")
        label = f"NAS: {value}"
    elif kind == "file" and value in local_files(cfg):
        path = os.path.join(cfg.get("backup_dir", ""), value)
        with open(path, "rb") as handle:
            blob = handle.read()
        sidecar = path + ".sha256"
        if os.path.exists(sidecar):
            expected = open(sidecar, encoding="utf8").read().split()[0]
            if sha256(blob) != expected:
                raise ValueError("suma kontrolna z pliku .sha256 sie nie zgadza - nie odtwarzam")
        label = value
    else:
        raise ValueError("nie ma takiej kopii")

    data = parse(blob, passphrase)          # zle haslo konczy sie tutaj, przed zmianami
    safety = snapshot_local(conn, actor)
    summary = restore(conn, data, with_users=with_users)
    db.log(conn, actor, "backup.restore", f"{label}: {summary}; wczesniejszy stan w {safety}")
    return dict(summary, source=label, safety=safety)
