"""Eksport calej konfiguracji, odtwarzanie i kopie zapasowe.

Kopia to pojedynczy JSON: ustawienia, tiery, peery, reguly i konta.
Z tego pliku da sie odtworzyc panel na czystej karcie - wg0.conf i reguly
iptables zostana wygenerowane od nowa przy pierwszym zastosowaniu zmian.

Kopia z haslem jest PELNA: dochodza hasla (NAS, ntfy, e-mail), historia (ruch,
handshake'i, dziennik, alerty) i tozsamosc Pi z helpera - klucz prywatny serwera i urzad
certyfikacji HTTPS. Z pelnej kopii odtworzysz cale Pi: stare pliki osob dzialaja dalej,
certyfikat na telefonach tez (install.sh --restore albo Kopie -> Odtworz).
Bez hasla kopia jest podstawowa i jawna - bez kluczy i hasel.

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

from . import cryptobox, db, i18n

FORMAT_VERSION = 1
SAFE_PATH = re.compile(r"^[A-Za-z0-9._/-]*$")
SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")

# stan roboczy panelu - nie nalezy do konfiguracji i nie moze trafiac do migawek,
# bo migawka siedzi w tej samej tabeli i zaczelaby zawierac sama siebie
RUNTIME_SETTINGS = {
    # klucz publiczny serwera odczytany z tego Pi - po odtworzeniu na nowym systemie (nowy klucz)
    # stary wpisywal sie do plikow klientow i nikt nie mogl sie polaczyc
    "server_pubkey",
    "applied_state", "applied_hash", "applied_fingerprint",
    "drift_findings", "drift_checked_at", "confirm_token", "rolled_back",
    # stan sprzed zastosowania, trzymany do potwierdzenia, i tryb potwierdzania tej zmiany
    "before_applied_state", "before_applied_hash", "before_applied_fingerprint",
    "confirm_auto", "confirm_since",
    # ostatnie odswiezenie listy z NAS (przycisk) - inne Pi, inna lista
    "smb_scan",
}

EXPORT_TABLES = {
    "settings": "SELECT key, value FROM settings",
    "tiers": "SELECT * FROM tiers",
    "peers": "SELECT * FROM peers",
    "rules": "SELECT * FROM rules",
    "users": "SELECT * FROM users",
    "hosts": "SELECT * FROM hosts",
}

# tych ustawien nie wynosimy poza maszyne w kopii bez hasla
SECRET_SETTINGS = {"smb_pass", "backup_passphrase", "alerts_token", "smtp_pass"}

# historia - tylko w pelnej kopii; kolejnosc ma znaczenie (peer_stats wskazuje na peers)
HISTORY_TABLES = ("traffic", "peer_stats", "peer_seen", "audit", "alerts", "backups")
WEAK_PASSPHRASE = 12      # krotsze haslo kopii panel odradza (kopia zawiera klucz serwera)
STAGING = "/var/lib/wg-panel/backups"


def staging_dir(cfg: dict) -> str:
    """Katalog roboczy kopii na Pi - tu powstaje kazda kopia, takze ta wysylana na NAS."""
    return cfg.get("backup_dir") or STAGING


def system_identity(conn: sqlite3.Connection) -> dict | None:
    """Klucz serwera i urzad certyfikacji z helpera - do pelnej kopii. None, gdy helper
    nie odpowiada (wtedy kopia jest pelna bez tozsamosci, co zapisujemy w opisie)."""
    from . import helperclient, model
    try:
        result = helperclient.call("system_export", {"settings": model.settings_only(conn)},
                                   timeout=30)
    except Exception:  # noqa: BLE001
        return None
    return result if result.get("server_key") else None


def build(conn: sqlite3.Connection, include_secrets: bool = False,
          history: bool = False) -> dict:
    out: dict = {
        "format": FORMAT_VERSION,
        "created_at": int(time.time()),
        "created_by": "wg-panel",
        # klucz PUBLICZNY serwera z chwili kopii - po odtworzeniu panel porownuje go z kluczem
        # nowego Pi i mowi wprost, czy stare pliki osob zadzialaja
        "server_pubkey": db.settings(conn).get("server_pubkey", ""),
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
    if history:
        out["history"] = {name: [dict(row) for row in conn.execute(f"SELECT * FROM {name}")]
                          for name in HISTORY_TABLES}
    return out


def serialize(conn: sqlite3.Connection, passphrase: str = "") -> tuple[bytes, bool]:
    """Kopia podstawowa (migawki przed odtworzeniem i przed zmianami)."""
    payload = json.dumps(build(conn), ensure_ascii=False, indent=1).encode("utf8")
    if passphrase:
        return cryptobox.encrypt(payload, passphrase), True
    return payload, False


def pack(conn: sqlite3.Connection, passphrase: str = "") -> tuple[bytes, bool, str]:
    """Kopia do wyslania: z haslem pelna (zaszyfrowana), bez hasla podstawowa.
    Zwraca (plik, zaszyfrowana, rodzaj): rodzaj = "full" | "full-nokey" | "basic"."""
    if not passphrase:
        blob, _encrypted = serialize(conn)
        return blob, False, "basic"
    data = build(conn, include_secrets=True, history=True)
    data["full"] = True
    system = system_identity(conn)
    if system:
        data["system"] = {"server_key": system["server_key"], "ca": system.get("ca")}
    payload = json.dumps(data, ensure_ascii=False, indent=1).encode("utf8")
    return cryptobox.encrypt(payload, passphrase), True, "full" if system else "full-nokey"


KIND_DETAIL = {"full": "pełna kopia, zaszyfrowana",
               "full-nokey": "pełna kopia bez klucza serwera (helper nie odpowiadał), zaszyfrowana",
               "basic": "bez szyfrowania"}


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
        restored = {row["key"]: row["value"] for row in data.get("settings", [])}
        if "audit_days" in restored and "alerts_days" not in restored:
            # kopia sprzed rozdzielenia: alerty mialy ten sam limit co dziennik
            conn.execute("INSERT INTO settings(key, value) VALUES ('alerts_days', ?) "
                         "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                         (restored["audit_days"],))
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
        history = data.get("history") or {}
        for name in HISTORY_TABLES:
            if name not in history:
                continue
            columns = {row[1] for row in conn.execute(f"PRAGMA table_info({name})")}
            if name == "backups":
                # lista kopii: dokladamy te z kopii, a zostawiamy biezace (w tym migawke
                # sprzed tego odtworzenia - musi dac sie do niej wrocic)
                present = {row[0] for row in conn.execute("SELECT path FROM backups")}
                columns.discard("id")
            else:
                present = set()
                conn.execute(f"DELETE FROM {name}")
            for row in history[name]:
                if name == "backups" and row.get("path") in present:
                    continue
                keys = [k for k in row if k in columns]
                if keys:
                    conn.execute(f"INSERT INTO {name}({', '.join(keys)}) VALUES "
                                 f"({', '.join('?' for _k in keys)})", [row[k] for k in keys])

    db.normalize_legacy(conn)      # kopia ze starszej wersji: odcieci peerzy, alias $NAS
    for name in ("tiers", "peers", "rules", "hosts"):
        summary[name] = len(data.get(name, []))
    summary["full"] = bool(data.get("full"))
    return summary


def backup_pubkey(data: dict) -> str:
    """Klucz publiczny serwera zapisany w kopii (nowe kopie: pole, starsze: w ustawieniach)."""
    if data.get("server_pubkey"):
        return data["server_pubkey"]
    for row in data.get("settings", []):
        if row.get("key") == "server_pubkey":
            return row.get("value") or ""
    return ""


# --------------------------------------------------------------------------
# kopie zapasowe
# --------------------------------------------------------------------------

def sha256(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


# dokad ida kopie: smb - tylko NAS (plik na Pi tylko na czas wysylki), local - tylko Pi,
# both - NAS i Pi (dwie kopie)
TARGETS = ("smb", "local", "both")


def run(conn: sqlite3.Connection, actor: str = "harmonogram", target: str | None = None) -> dict:
    """Robi kopie zgodnie z ustawieniami, liczy sume kontrolna i od razu ja sprawdza.

    target nadpisuje cel z ustawien dla tej jednej kopii (przyciski "Na NAS" / "Na Pi"),
    bez zmiany celu kopii automatycznych. Kopia "tylko NAS" nie zostawia nic na Pi."""
    cfg = db.settings(conn)
    blob, encrypted, kind = pack(conn, cfg.get("backup_passphrase", ""))
    name = filename(encrypted)
    digest = sha256(blob)
    staging = staging_dir(cfg)
    target = target or cfg.get("backup_target", "local")
    if target not in TARGETS:
        target = "local"
    remote = target in ("smb", "both")
    keep_local = target in ("local", "both")

    record = {"ts": int(time.time()), "target": "smb" if remote else "local", "path": "",
              "bytes": len(blob), "encrypted": int(encrypted), "ok": 0, "detail": "",
              "sha256": digest}
    local_path = sidecar = ""
    try:
        os.makedirs(staging, exist_ok=True)
        try:
            # grupa wg-panel czyta kopie tak jak baze (te same sekrety, baza ma 660);
            # setgid - kopie zrobione z wgctl przez czlonka grupy tez naleza do wg-panel
            os.chmod(staging, 0o2770)
        except OSError:
            pass             # katalog innego konta - prawa nadal instalator
        local_path = os.path.join(staging, name)

        def taken(candidate: str) -> bool:
            # dwie kopie w tej samej sekundzie; kopia "tylko NAS" nie zostawia pliku na Pi,
            # wiec patrzymy tez w historie - inaczej druga nadpisalaby pierwsza na NAS
            return (os.path.exists(os.path.join(staging, candidate)) or conn.execute(
                "SELECT 1 FROM backups WHERE path = ? OR path LIKE ?",
                (candidate, "%/" + candidate)).fetchone() is not None)
        if taken(name):
            stem, _, suffix = name.partition(".")
            counter = 1
            while taken(name):
                name = f"{stem}-{counter}.{suffix}"
                counter += 1
            local_path = os.path.join(staging, name)
        with open(local_path, "wb") as handle:
            handle.write(blob)
        os.chmod(local_path, 0o640)
        sidecar = write_sidecar(local_path, name, digest)
        record["path"] = local_path

        if remote:
            record["path"] = upload_smb(cfg, local_path, name)
            upload_smb(cfg, sidecar, name + ".sha256")
        record["ok"] = 1
        record["detail"] = KIND_DETAIL[kind]
    except PermissionError:
        # np. wgctl uruchomione przez czlonka grupy przy prawach z wczesniejszej wersji
        record["detail"] = f"brak dostepu do katalogu kopii {staging} - zainstaluj panel ponownie"
    except Exception as exc:  # noqa: BLE001
        record["detail"] = str(exc)[:400]
    if not keep_local:
        # "tylko NAS": plik na Pi byl tylko do wysylki - takze gdy wysylka sie nie udala
        for victim in (local_path, sidecar):
            if victim:
                try:
                    os.remove(victim)
                except OSError:
                    pass
        if not record["ok"]:
            record["path"] = ""

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
    if record["ok"] and target == "smb" and cfg.get("backup_target") == "smb":
        drop_working_copies(conn, cfg)        # wybrane "tylko NAS" - stare kopie z Pi znikaja

    if record["ok"] and cfg.get("backup_verify", "1") == "1":
        record["verify"] = verify(conn, record["id"])
    return record


def drop_working_copies(conn: sqlite3.Connection, cfg: dict) -> int:
    """Ustawione "tylko NAS": kopie robocze na Pi kopii, ktore leza na NAS i przeszly
    weryfikacje, sa zbedne (starsza wersja zostawiala je zawsze) - usuwamy je z Pi. Kopie
    niesprawdzone albo niezgodne zostaja, wpisy na liscie tez."""
    removed = 0
    for row in conn.execute("SELECT * FROM backups WHERE ok = 1 AND verify_ok = 1 "
                            "AND path LIKE '//%'"):
        local = local_copy(cfg, row)
        if not local:
            continue
        for victim in (local, local + ".sha256"):
            try:
                os.remove(victim)
                removed += 1
            except OSError:
                pass
    return removed


def write_sidecar(local_path: str, name: str, digest: str) -> str:
    """Plik w formacie sha256sum -c: kopie da sie sprawdzic takze bez panelu."""
    sidecar = local_path + ".sha256"
    with open(sidecar, "w", encoding="utf8") as handle:
        handle.write(f"{digest}  {name}\n")
    os.chmod(sidecar, 0o640)
    return sidecar


def local_copy(cfg: dict, record) -> str:
    """Kopia robocza kopii z NAS w katalogu na Pi (zostaje tam ostatnich "trzymaj") -
    sciezka albo "" gdy juz jej nie ma."""
    path = record["path"] or ""
    if not path.startswith("//"):
        return ""
    candidate = os.path.join(staging_dir(cfg), path.rsplit("/", 1)[-1])
    return candidate if os.path.isfile(candidate) else ""


def fetch(cfg: dict, record, prefer_local: bool = False) -> bytes:
    """Pobiera plik kopii z miejsca, w ktorym faktycznie lezy.

    prefer_local (odtwarzanie): kopia z NAS, ktora ma jeszcze kopie robocza na Pi, jest
    czytana z Pi - NAS nie trzeba budzic. Tylko gdy suma sie zgadza; inaczej NAS.
    Weryfikacja zawsze siega tam, gdzie kopia ma lezec (NAS) - po to jest."""
    path = record["path"]
    if record["target"] == "smb" and path.startswith("//"):
        local = local_copy(cfg, record) if prefer_local else ""
        if local:
            with open(local, "rb") as handle:
                blob = handle.read()
            if record["sha256"] and sha256(blob) == record["sha256"]:
                return blob
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
                           i18n.pl(i18n.ALERTS["verify_failed"], n=backup_id,
                                   error=result["detail"]),
                           severity="alarm")
    return result


def verify_many(conn: sqlite3.Connection, ids: list) -> list[dict]:
    """Weryfikacja zaznaczonych kopii. Nieudane (bez pliku) pomijamy - nie ma czego sprawdzac,
    a weryfikacja takiej podnioslaby alarm."""
    out = []
    for backup_id in ids:
        row = conn.execute("SELECT id FROM backups WHERE id = ? AND ok = 1",
                           (backup_id,)).fetchone()
        if row is not None:
            out.append(dict(verify(conn, row["id"]), id=row["id"]))
    return out


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


def adopt_smb(conn: sqlite3.Connection, names: list) -> int:
    """Kopie lezace na NAS, ktorych panel nie zna (np. zrobione przed reinstalacja),
    dopisuje do historii - wtedy da sie je sprawdzic i usunac tak jak wlasne.
    Wola to tylko przycisk "Odswiez liste z NAS" - panel sam NAS nie odpytuje."""
    cfg = db.settings(conn)
    host = (cfg.get("smb_host") or "").strip()
    share = (cfg.get("smb_share") or "").strip()
    folder = (cfg.get("smb_path") or "").strip().strip("/")
    prefix = f"//{host}/{share}/" + (f"{folder}/" if folder else "")
    known = {os.path.basename(row[0] or "") for row in conn.execute("SELECT path FROM backups")}
    added = 0
    for name in names:
        if name in known or not is_backup_file(name):
            continue
        try:
            digest = download_smb(cfg, name + ".sha256").decode().split()[0]
        except (ValueError, IndexError, UnicodeDecodeError):
            digest = ""
        moment = _stamp(name) or int(time.time())
        with conn:
            conn.execute(
                """INSERT INTO backups(ts, target, path, bytes, encrypted, ok, detail, sha256)
                   VALUES (?, 'smb', ?, 0, ?, 1, 'znaleziona na NAS', ?)""",
                (moment, prefix + name, int(name.endswith(".enc")), digest))
        added += 1
    return added


def delete_backups(conn: sqlite3.Connection, ids: list) -> dict:
    """Usuwa wybrane kopie: pliki (z .sha256) na karcie i na NAS, potem wpis w historii.

    Wpis znika tylko wtedy, gdy pliki udalo sie usunac (albo juz ich nie bylo) - inaczej
    stracilibysmy slad po pliku, ktory dalej lezy na NAS. Na NAS usuwamy wylacznie
    pod adresem z biezacych ustawien.
    """
    cfg = db.settings(conn)
    staging = os.path.realpath(staging_dir(cfg))
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
    directory = staging_dir(cfg)
    try:
        names = os.listdir(directory)
    except OSError:
        return []
    return sorted((f for f in names if is_backup_file(f)), reverse=True)


def _stamp(name: str) -> int:
    """Czas z nazwy pliku kopii (wg-panel-RRRRMMDD-GGMMSS...) - 0, gdy nazwa inna."""
    match = re.search(r"wg-panel-(\d{8})-(\d{6})", name)
    try:
        return int(time.mktime(time.strptime("".join(match.groups()), "%Y%m%d%H%M%S")))
    except (AttributeError, ValueError):
        return 0


def adopt_local(conn: sqlite3.Connection) -> int:
    """Pliki kopii w katalogu roboczym, ktorych panel nie zna (np. skopiowane recznie na nowy
    system), dopisuje do historii - lista kopii jest wtedy jedna i wszystko na niej da sie
    sprawdzic, odtworzyc i usunac. Kopie robocze kopii z NAS maja te sama nazwe - znane."""
    cfg = db.settings(conn)
    staging = staging_dir(cfg)
    known = {os.path.basename(row[0] or "") for row in conn.execute("SELECT path FROM backups")}
    added = 0
    for name in local_files(cfg):
        if name in known:
            continue
        path = os.path.join(staging, name)
        try:
            size, moment = os.path.getsize(path), int(os.path.getmtime(path))
        except OSError:
            continue
        try:
            with open(path + ".sha256", encoding="utf8") as handle:
                digest = handle.read().split()[0]
        except (OSError, IndexError):
            digest = ""          # bez sumy: da sie odtworzyc, weryfikacja powie, czego brakuje
        with conn:
            conn.execute(
                """INSERT INTO backups(ts, target, path, bytes, encrypted, ok, detail, sha256)
                   VALUES (?, 'local', ?, ?, ?, 1, 'znaleziona na Pi', ?)""",
                (_stamp(name) or moment, path, size, int(name.endswith(".enc")), digest))
        added += 1
    return added


def scan_smb(conn: sqlite3.Connection) -> dict:
    """Przycisk "Odswiez liste z NAS": co lezy na NAS. Nieznane kopie trafiaja do
    historii, wynik zostaje zapamietany (smb_scan) - lista pokazuje potem, ktorych plikow na
    NAS juz nie ma, bez ponownego budzenia serwera. Panel sam NAS nie odpytuje."""
    names = list_smb(db.settings(conn))
    added = adopt_smb(conn, names)
    db.set_setting(conn, "smb_scan", json.dumps({"ts": int(time.time()), "names": names}))
    return {"count": len(names), "added": added}


def last_scan(cfg: dict) -> dict | None:
    try:
        scan = json.loads(cfg.get("smb_scan") or "")
    except ValueError:
        return None
    if not isinstance(scan, dict) or not isinstance(scan.get("names"), list):
        return None
    return {"ts": int(scan.get("ts") or 0), "names": [str(n) for n in scan["names"]]}


SNAPSHOT_DETAIL = {"przed": "kopia stanu przed odtworzeniem",
                   "przed-aktualizacja": "kopia stanu przed aktualizacją"}


def snapshot_local(conn: sqlite3.Connection, actor: str, label: str = "przed") -> str:
    """Kopia obecnego stanu tylko na Pi - zeby odtworzenie albo aktualizacje dalo sie cofnac."""
    cfg = db.settings(conn)
    blob, encrypted = serialize(conn, cfg.get("backup_passphrase", ""))
    staging = staging_dir(cfg)
    os.makedirs(staging, exist_ok=True)
    stem = time.strftime("wg-panel-%Y%m%d-%H%M%S")
    suffix = ".json" + (".enc" if encrypted else "")
    name = f"{stem}-{label}{suffix}"
    path = os.path.join(staging, name)
    counter = 1
    while os.path.exists(path):          # dwie migawki w tej samej sekundzie - nie nadpisujemy
        name = f"{stem}-{label}-{counter}{suffix}"
        path = os.path.join(staging, name)
        counter += 1
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
             SNAPSHOT_DETAIL.get(label, SNAPSHOT_DETAIL["przed"]), digest))
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


def _smb_dirs(cfg: dict) -> list[str]:
    """Katalogi w katalogu glownym udzialu - do podpowiedzi, gdy wpisany katalog nie istnieje."""
    args, env, _path = _smb_base(cfg)
    result = subprocess.run(args + ["-c", "ls"], capture_output=True, text=True, timeout=60,
                            env=env)
    found = []
    for line in result.stdout.split("\n"):
        parts = line.split()
        if len(parts) >= 2 and "D" in parts[1] and parts[0] not in (".", ".."):
            found.append(parts[0])
    return sorted(found)


def explain_smb(cfg: dict, exc: Exception) -> str:
    """Blad smbclient po ludzku: co poprawic, a nie sam kod NT_STATUS."""
    text = str(exc)
    share, path = cfg.get("smb_share", ""), (cfg.get("smb_path") or "").strip("/")
    if "LOGON_FAILURE" in text:
        return (f"NAS odrzucił login albo hasło (użytkownik {cfg.get('smb_user') or '-'}) - "
                "sprawdź je; w panelu NAS-a sprawdź też, czy konto nie jest wyłączone albo adres Pi "
                "zablokowany (np. automatyczna blokada adresów)")
    if "BAD_NETWORK_NAME" in text:
        return f"na NAS nie ma udziału {share}"
    if "OBJECT_NAME_NOT_FOUND" in text or "OBJECT_PATH_NOT_FOUND" in text:
        try:
            dirs = _smb_dirs(cfg)
        except Exception:  # noqa: BLE001
            dirs = []
        hint = f" - są tam: {', '.join(dirs)}" if dirs else ""
        return f"na udziale {share} nie ma katalogu {path}{hint}"
    if any(code in text for code in ("IO_TIMEOUT", "HOST_UNREACHABLE", "CONNECTION_REFUSED",
                                     "Connection to", "NETWORK_UNREACHABLE")):
        return (f"NAS nie odpowiada pod adresem {cfg.get('smb_host')} - sprawdź adres i czy "
                "na NAS-ie jest włączone SMB")
    return text


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
        blob = fetch(cfg, row, prefer_local=True)
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
        path = os.path.join(staging_dir(cfg), value)
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
    # tozsamosc (klucz serwera) idzie do wywolujacego, nigdy do dziennika
    return dict(summary, source=label, safety=safety, system=data.get("system"),
                backup_pubkey=backup_pubkey(data))


def apply_identity(conn: sqlite3.Connection, summary: dict, call=None) -> dict:
    """Po odtworzeniu: klucz serwera i urzad certyfikacji z pelnej kopii do helpera oraz
    werdykt, czy dotychczasowe pliki osob zadzialaja.

    verdict: restored (klucz z kopii jest na tym Pi) | same (kopia bez klucza, ale klucz sie
    zgadza) | changed (inny klucz - kazda osoba potrzebuje nowego pliku) | unknown."""
    from . import helperclient, model
    call = call or helperclient.call
    settings = model.settings_only(conn)
    out = {"verdict": "unknown", "error": "", "ca": ""}
    system = summary.get("system") or {}
    if system.get("server_key"):
        try:
            result = call("system_import", {"settings": settings,
                                            "server_key": system["server_key"],
                                            "ca": system.get("ca")}, timeout=90)
        except Exception as exc:  # noqa: BLE001
            out["error"] = str(exc)
        else:
            out["verdict"] = "restored"
            out["ca"] = ("new" if result.get("ca_new")
                         else "restored" if result.get("ca_restored") else "")
    try:
        live = call("server_pubkey", {"settings": settings}).get("pubkey", "")
    except Exception:  # noqa: BLE001
        live = ""
    if live:
        db.set_setting(conn, "server_pubkey", live)
    if out["verdict"] != "restored":
        old = summary.get("backup_pubkey") or ""
        if old and live:
            out["verdict"] = "same" if old == live else "changed"
    return out
