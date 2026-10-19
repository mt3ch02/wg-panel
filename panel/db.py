"""SQLite jako zrodlo prawdy. wg0.conf i reguly iptables sa generowane z tych tabel."""
from __future__ import annotations

import ipaddress
import json
import sqlite3

from . import names
from .i18n import _
import time

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id           INTEGER PRIMARY KEY,
    username     TEXT UNIQUE NOT NULL,
    pwd_hash     TEXT NOT NULL,
    pwd_salt     TEXT NOT NULL,
    totp_secret  TEXT,
    created_at   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token      TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    csrf       TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL
);

-- tier = nazwany zestaw szablonow regul, rozwijany przy generowaniu
CREATE TABLE IF NOT EXISTS tiers (
    id          INTEGER PRIMARY KEY,
    name        TEXT UNIQUE NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    -- co idzie przez VPN u klienta: full | auto | lan | custom
    client_mode TEXT NOT NULL DEFAULT 'auto',
    client_list TEXT NOT NULL DEFAULT '',   -- lista dla trybu custom
    host_access TEXT NOT NULL DEFAULT '',   -- porty na samym RPi, np. "22,3389"
    rules_json  TEXT NOT NULL DEFAULT '[]', -- szablony regul
    builtin     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS peers (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    device      TEXT NOT NULL DEFAULT '',
    ip          TEXT UNIQUE NOT NULL,
    pubkey      TEXT UNIQUE NOT NULL,
    psk         TEXT NOT NULL,
    privkey     TEXT,               -- kasowany po wydaniu konfiguracji
    tier_id     INTEGER REFERENCES tiers(id),
    state       TEXT NOT NULL DEFAULT 'active',  -- active | suspended
    note        TEXT NOT NULL DEFAULT '',
    created_at  INTEGER NOT NULL,
    revoked_at  INTEGER,
    -- tryb dostepu: none (bezterminowo) | window (od-do) | from_use (N dni od 1. uzycia)
    access_mode TEXT NOT NULL DEFAULT 'none',
    starts_at   INTEGER,          -- poczatek okna terminowego
    expires_at  INTEGER,          -- koniec okna albo wyliczony koniec po 1. uzyciu
    expires_days INTEGER,         -- dlugosc dostepu w dniach (tryb from_use)
    expires_from_use INTEGER NOT NULL DEFAULT 0,
    first_seen  INTEGER,          -- kiedy peer polaczyl sie pierwszy raz
    auto_state  INTEGER NOT NULL DEFAULT 0,  -- 1 = stan ustawil harmonogram, nie czlowiek
    ip6         TEXT,                       -- adres IPv6 w tunelu, gdy IPv6 wlaczone
    tunnel_mode TEXT NOT NULL DEFAULT '',   -- '' = jak w tierze, albo full|auto|lan|custom
    tunnel_list TEXT NOT NULL DEFAULT '',   -- lista dla trybu custom
    issued_allowed TEXT,                    -- AllowedIPs w ostatnio wydanym pliku
    panel_access INTEGER NOT NULL DEFAULT 0 -- 1 = ta osoba moze otworzyc panel przez VPN
);

-- reguly wlasne, ponad to co daje tier
CREATE TABLE IF NOT EXISTS rules (
    id        INTEGER PRIMARY KEY,
    peer_id   INTEGER NOT NULL REFERENCES peers(id) ON DELETE CASCADE,
    position  INTEGER NOT NULL DEFAULT 0,
    action    TEXT NOT NULL,              -- ACCEPT | DROP
    dest      TEXT NOT NULL,              -- IP, CIDR, 'LAN' albo 'ANY'
    proto     TEXT NOT NULL DEFAULT 'any',-- any | tcp | udp
    ports     TEXT NOT NULL DEFAULT '',   -- "80,443,445"
    time_from TEXT NOT NULL DEFAULT '',   -- "08:00"
    time_to   TEXT NOT NULL DEFAULT '',
    weekdays  TEXT NOT NULL DEFAULT '',   -- "Mon,Tue,..."
    comment   TEXT NOT NULL DEFAULT '',
    enabled   INTEGER NOT NULL DEFAULT 1
);

-- jednorazowe linki do pobrania konfiguracji
CREATE TABLE IF NOT EXISTS deliveries (
    token      TEXT PRIMARY KEY,
    peer_id    INTEGER NOT NULL REFERENCES peers(id) ON DELETE CASCADE,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    used_at    INTEGER
);

CREATE TABLE IF NOT EXISTS audit (
    id     INTEGER PRIMARY KEY,
    ts     INTEGER NOT NULL,
    actor  TEXT NOT NULL,
    action TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT ''
);

-- ostatnio widziany stan peera, do wykrywania zmian endpointu i ciszy
CREATE TABLE IF NOT EXISTS peer_seen (
    pubkey         TEXT PRIMARY KEY,
    last_endpoint  TEXT NOT NULL DEFAULT '',
    last_handshake INTEGER NOT NULL DEFAULT 0,
    updated_at     INTEGER NOT NULL DEFAULT 0
);

-- ostatni handshake i laczny ruch - przetrwaja restart Pi (panel/stats.py)
CREATE TABLE IF NOT EXISTS peer_stats (
    peer_id        INTEGER PRIMARY KEY REFERENCES peers(id) ON DELETE CASCADE,
    pubkey         TEXT NOT NULL DEFAULT '',    -- klucz, ktorego dotycza last_rx/last_tx
    boot_id        TEXT NOT NULL DEFAULT '',    -- uruchomienie Pi, z ktorego sa liczniki
    last_rx        INTEGER NOT NULL DEFAULT 0,  -- ostatnio widziane liczniki jadra
    last_tx        INTEGER NOT NULL DEFAULT 0,
    rx_total       INTEGER NOT NULL DEFAULT 0,  -- suma od "since", tez sprzed restartow
    tx_total       INTEGER NOT NULL DEFAULT 0,
    last_handshake INTEGER NOT NULL DEFAULT 0,
    since          INTEGER NOT NULL DEFAULT 0,  -- od kiedy liczymy
    dead_alerted   INTEGER NOT NULL DEFAULT 0   -- handshake, po ktorym poszedl alert o ciszy
);

-- trwajace incydenty "ten sam plik na dwoch urzadzeniach" (panel/dual.py)
CREATE TABLE IF NOT EXISTS dual_use (
    peer_id   INTEGER PRIMARY KEY REFERENCES peers(id) ON DELETE CASCADE,
    since     INTEGER NOT NULL,
    endpoints TEXT NOT NULL DEFAULT '[]'   -- adresy, miedzy ktorymi przeskakuje (JSON)
);

CREATE TABLE IF NOT EXISTS alerts (
    id       INTEGER PRIMARY KEY,
    ts       INTEGER NOT NULL,
    kind     TEXT NOT NULL,
    subject  TEXT NOT NULL DEFAULT '',
    message  TEXT NOT NULL DEFAULT '',
    severity TEXT NOT NULL DEFAULT 'info',
    sent     INTEGER NOT NULL DEFAULT 0,
    seen     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS backups (
    id        INTEGER PRIMARY KEY,
    ts        INTEGER NOT NULL,
    target    TEXT NOT NULL,
    path      TEXT NOT NULL DEFAULT '',
    bytes     INTEGER NOT NULL DEFAULT 0,
    encrypted INTEGER NOT NULL DEFAULT 0,
    ok        INTEGER NOT NULL DEFAULT 0,
    detail    TEXT NOT NULL DEFAULT ''
);

-- katalog hostow LAN: nazwy zamiast adresow w regulach
CREATE TABLE IF NOT EXISTS hosts (
    id          INTEGER PRIMARY KEY,
    name        TEXT UNIQUE NOT NULL,
    address     TEXT NOT NULL DEFAULT '',   -- IPv4 albo CIDR
    address6    TEXT NOT NULL DEFAULT '',   -- IPv6 albo CIDR, opcjonalnie
    description TEXT NOT NULL DEFAULT ''
);

-- probki licznikow z `wg show dump`; liczniki sa narastajace
CREATE TABLE IF NOT EXISTS traffic (
    ts     INTEGER NOT NULL,
    pubkey TEXT NOT NULL,
    rx     INTEGER NOT NULL,
    tx     INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS traffic_by_peer ON traffic(pubkey, ts);

CREATE TABLE IF NOT EXISTS config_versions (
    id      INTEGER PRIMARY KEY,
    ts      INTEGER NOT NULL,
    actor   TEXT NOT NULL,
    wg_conf TEXT NOT NULL,
    rules   TEXT NOT NULL,
    applied INTEGER NOT NULL DEFAULT 0,
    note    TEXT NOT NULL DEFAULT ''
);
"""

# adres NAS do pierwszego wpisu w katalogu hostow (tylko swieza instalacja)
SEED_NAS = "192.168.1.11"

DEFAULT_SETTINGS = {
    "lan": "192.168.1.0/24",           # instalator wpisuje siec wykryta na Pi
    "vpn": "10.0.0.0/24",
    "server_ip": "10.0.0.1",
    "wan_if": "eth0",
    "listen_port": "51820",
    "endpoint": "vpn.example.com:51820",  # DDNS albo publiczny IP - do zmiany w Ustawieniach
    "dns_full": "9.9.9.9",            # Quad9 - filtruje domeny zlosliwe, nie profiluje
    "wg_iface": "wg0",
    "chain": "WG-USERS",
    "confirm_seconds": "90",
    "confirm_mode": "auto",            # auto: przegladarka sprawdza polaczenie | manual
    "keepalive": "25",
    "managed_host_ports": "22,3389",
    # wygasanie dostepu
    "expiry_auto_apply": "1",
    # alerty
    "alerts_channel": "none",          # none | ntfy | webhook
    "alerts_url": "",
    "alerts_token": "",                # token dostepu ntfy (chroniony temat) - opcjonalny
    "tls_mode": "self",                # self (wlasny urzad) | custom (pliki, np. Let's Encrypt)
    "proxy_trusted": "",               # reverse proxy na innym serwerze (np. Caddy) - adresy
    "tls_cert_path": "", "tls_key_path": "",
    "panel_url": "",                   # publiczny adres panelu do linkow (np. za nginx)
    "alerts_email": "0",               # e-mail jako dodatkowy kanal - opcjonalny
    "smtp_host": "", "smtp_port": "587", "smtp_security": "starttls",
    "smtp_user": "", "smtp_pass": "", "smtp_from": "", "smtp_to": "",
    "alert_dead_hours": "48",          # 0 = wylaczone
    "alert_new_endpoint": "1",
    "alert_two_devices": "1",          # ten sam plik na dwoch urzadzeniach naraz
    "alert_expiry": "1",
    "alerts_quiet_minutes": "30",      # nie powtarzaj tego samego alertu czesciej
    # nazwy urzadzen - etykiety w calym panelu (panel/names.py)
    "name_server": names.DEFAULTS["name_server"],
    "name_storage": names.DEFAULTS["name_storage"],
    # kopie zapasowe
    "backup_enabled": "0",
    "test_mode": "0",                  # serwer testowy (install.sh --restore --test) - poza kopia
    "backup_target": "local",          # local | smb | both (export.TARGETS)
    "backup_dir": "/var/lib/wg-panel/backups",
    "backup_time": "03:30",
    "backup_keep": "14",
    "backup_passphrase": "",
    "smb_host": "",
    "smb_share": "backup",
    "smb_path": "wg-panel",
    "smb_user": "",
    "smb_pass": "",
    "backup_verify": "1",              # sprawdzaj sumy kontrolne po kazdej kopii
    # ostrzezenie przed koncem okna
    "expiry_warn_days": "3",
    # IPv6
    "ipv6_enabled": "0",
    "vpn6": "fd42:42:42::/64",
    "server_ip6": "fd42:42:42::1",
    "lan6": "",                        # prefiksy IPv6 LAN-u, po przecinku
    "nat6": "1",                       # maskarada IPv6 dla trybu pelnego
    "dns6": "",
    # ruch
    "traffic_sample_minutes": "5",
    "traffic_anomaly_factor": "10",    # godzina > N x mediana = anomalia
    "traffic_anomaly_min_mb": "500",   # ...i co najmniej tyle MB
    # integralnosc
    "drift_check": "1",
    # ile dni trzymac dziennik i alerty - osobno; 0 = bez limitu (nic nie jest kasowane)
    "audit_days": "90",
    "alerts_days": "90",
    "mtu": "",
    "server_lan": "192.168.1.10",      # adres Pi w sieci domowej (instalator wpisuje wykryty)
    "panel_port": "8080",              # port panelu; z VPN tylko dla wskazanych osob
}

BUILTIN_TIERS = [
    {
        "name": "ADMIN",
        "description": "Pełny dostęp do LAN, SSH/RDP i panel na serwerze, cały ruch przez VPN.",
        "client_mode": "full",
        "host_access": "22,3389,8080",
        "rules": [],  # brak regul = spada do koncowego ACCEPT
    },
    {
        "name": "NET",
        "description": "Internet przez VPN, LAN całkowicie zablokowany.",
        "client_mode": "full",
        "host_access": "",
        "rules": [
            {"action": "DROP", "dest": "LAN", "proto": "any", "ports": "",
             "comment": "brak dostepu do LAN"},
        ],
    },
    {
        "name": "NAS",
        "description": "Tylko NAS na portach 80/443/445, split tunnel.",
        "client_mode": "auto",
        "host_access": "",
        "rules": [
            {"action": "ACCEPT", "dest": "nas", "proto": "tcp", "ports": "80,443,445",
             "comment": "dysk sieciowy + panel"},
            {"action": "DROP", "dest": "LAN", "proto": "any", "ports": "",
             "comment": "reszta LAN zablokowana"},
        ],
    },
]


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    migrate(conn)
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    """Dokladanie kolumn do baz zalozonych wczesniejsza wersja panelu."""
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(peers)")}
    with conn:
        if "expires_at" not in columns:
            conn.execute("ALTER TABLE peers ADD COLUMN expires_at INTEGER")
        if "expires_days" not in columns:
            conn.execute("ALTER TABLE peers ADD COLUMN expires_days INTEGER")
        if "expires_from_use" not in columns:
            conn.execute(
                "ALTER TABLE peers ADD COLUMN expires_from_use INTEGER NOT NULL DEFAULT 0")
        if "first_seen" not in columns:
            conn.execute("ALTER TABLE peers ADD COLUMN first_seen INTEGER")
        if "access_mode" not in columns:
            conn.execute(
                "ALTER TABLE peers ADD COLUMN access_mode TEXT NOT NULL DEFAULT 'none'")
            # bazy z poprzedniej wersji: sama data waznosci = okno terminowe
            conn.execute(
                "UPDATE peers SET access_mode='window' WHERE expires_at IS NOT NULL")
            conn.execute(
                "UPDATE peers SET access_mode='from_use' WHERE expires_from_use=1")
        if "starts_at" not in columns:
            conn.execute("ALTER TABLE peers ADD COLUMN starts_at INTEGER")
        if "auto_state" not in columns:
            conn.execute(
                "ALTER TABLE peers ADD COLUMN auto_state INTEGER NOT NULL DEFAULT 0")
        if "ip6" not in columns:
            conn.execute("ALTER TABLE peers ADD COLUMN ip6 TEXT")
        if "tunnel_mode" not in columns:
            conn.execute("ALTER TABLE peers ADD COLUMN tunnel_mode TEXT NOT NULL DEFAULT ''")
            conn.execute("ALTER TABLE peers ADD COLUMN tunnel_list TEXT NOT NULL DEFAULT ''")
            conn.execute("ALTER TABLE peers ADD COLUMN issued_allowed TEXT")
        if "panel_access" not in columns:
            conn.execute("ALTER TABLE peers ADD COLUMN panel_access INTEGER NOT NULL DEFAULT 0")
        else:
            # port panelu to teraz zwykly port w tierze - przenosimy dawne uprawnienie
            port = settings(conn).get("panel_port", "8080")
            for row in conn.execute(
                    "SELECT DISTINCT tier_id FROM peers WHERE panel_access = 1 "
                    "AND tier_id IS NOT NULL"):
                tier = conn.execute("SELECT host_access FROM tiers WHERE id = ?",
                                    (row["tier_id"],)).fetchone()
                ports = [p.strip() for p in (tier["host_access"] or "").split(",") if p.strip()]
                if port and port not in ports:
                    conn.execute("UPDATE tiers SET host_access = ? WHERE id = ?",
                                 (",".join(ports + [port]), row["tier_id"]))

        tier_cols = {r["name"] for r in conn.execute("PRAGMA table_info(tiers)")}
        if "client_list" not in tier_cols:
            conn.execute("ALTER TABLE tiers ADD COLUMN client_list TEXT NOT NULL DEFAULT ''")
            # dawny 'split' = VPN + caly LAN; wbudowany NAS przechodzi na dokladny split
            conn.execute("UPDATE tiers SET client_mode='auto' "
                         "WHERE client_mode='split' AND name='NAS' AND builtin=1")
            conn.execute("UPDATE tiers SET client_mode='lan' WHERE client_mode='split'")

        backup_cols = {r["name"] for r in conn.execute("PRAGMA table_info(backups)")}
        for column, ddl in (("sha256", "TEXT NOT NULL DEFAULT ''"),
                            ("verified_at", "INTEGER"),
                            ("verify_ok", "INTEGER"),
                            ("verify_detail", "TEXT NOT NULL DEFAULT ''")):
            if column not in backup_cols:
                conn.execute(f"ALTER TABLE backups ADD COLUMN {column} {ddl}")


def init_defaults(conn: sqlite3.Connection) -> None:
    _init_defaults(conn)
    seed_hosts(conn)
    normalize_legacy(conn)


def _drop_alias(token: str) -> str:
    """$NAS -> nas: przedrostek $ byl aliasem z pierwszej wersji; nazwy hostow bez niego."""
    token = token.strip()
    return token[1:].lower() if token.startswith("$") and len(token) > 1 else token


def _drop_alias_list(value: str) -> str:
    items = [item.strip() for item in (value or "").split(",") if item.strip()]
    return ", ".join(_drop_alias(item) for item in items)


LEGACY_TIER_DESCRIPTIONS = {
    "Pelny dostep do LAN, SSH/RDP i panel na serwerze, caly ruch przez VPN.":
        "Pełny dostęp do LAN, SSH/RDP i panel na serwerze, cały ruch przez VPN.",
    # jeszcze wczesniejsza wersja domyslnego opisu ADMIN
    "Pelny dostep do LAN, SSH/RDP na serwer, caly ruch przez VPN.":
        "Pełny dostęp do LAN, SSH/RDP i panel na serwerze, cały ruch przez VPN.",
    "Internet przez VPN, LAN calkowicie zablokowany.": "Internet przez VPN, LAN całkowicie zablokowany.",
}


def normalize_legacy(conn: sqlite3.Connection) -> list[str]:
    """Porzadki po starszych wersjach - przy starcie panelu i wgctl oraz po odtworzeniu kopii.

    - Stan "odciety" usuniety: odcieci peerzy staja sie zawieszonymi ze skasowanym kluczem.
      Skutek ten sam - brak dostepu i brak mozliwosci wygenerowania pliku - ale nic nie znika.
    - Alias $NAS usuniety: reguly i listy wskazuja wprost host "nas" z katalogu. Gdy hosta
      nie ma, powstaje z dawnego ustawienia "nas", ktore obslugiwalo alias.
    """
    changes = []
    with conn:
        # tryb "nginx na tym samym Pi" usuniety - wraca wlasny urzad (proxy na innym serwerze
        # to osobne ustawienie proxy_trusted)
        conn.execute("UPDATE settings SET value = 'self' WHERE key = 'tls_mode' AND value = 'proxy'")
        # opisy wbudowanych tierow z polskimi znakami - tylko nietkniete przez uzytkownika
        for old, new in LEGACY_TIER_DESCRIPTIONS.items():
            conn.execute("UPDATE tiers SET description = ? WHERE builtin = 1 AND description = ?",
                         (new, old))
        count = conn.execute("UPDATE peers SET state = 'suspended', privkey = NULL, auto_state = 0 "
                             "WHERE state = 'revoked'").rowcount
        if count:
            changes.append(f"{count} odcietych peerow -> zawieszeni ze skasowanym kluczem")

        fixed = 0
        for row in conn.execute("SELECT id, dest FROM rules WHERE dest LIKE '$%'").fetchall():
            conn.execute("UPDATE rules SET dest = ? WHERE id = ?", (_drop_alias(row["dest"]), row["id"]))
            fixed += 1
        for row in conn.execute("SELECT id, tunnel_list FROM peers WHERE tunnel_list LIKE '%$%'").fetchall():
            conn.execute("UPDATE peers SET tunnel_list = ? WHERE id = ?",
                         (_drop_alias_list(row["tunnel_list"]), row["id"]))
            fixed += 1
        for row in conn.execute("SELECT id, rules_json, client_list FROM tiers").fetchall():
            rules = json.loads(row["rules_json"] or "[]")
            new_rules = [dict(rule, dest=_drop_alias(str(rule.get("dest", ""))))
                         if str(rule.get("dest", "")).strip().startswith("$") else rule
                         for rule in rules]
            new_list = _drop_alias_list(row["client_list"]) if "$" in (row["client_list"] or "") \
                else row["client_list"]
            if new_rules != rules or new_list != row["client_list"]:
                conn.execute("UPDATE tiers SET rules_json = ?, client_list = ? WHERE id = ?",
                             (json.dumps(new_rules), new_list, row["id"]))
                fixed += 1
        if fixed:
            changes.append(f"alias $NAS -> nas w {fixed} miejscach")
            legacy_nas = conn.execute("SELECT value FROM settings WHERE key = 'nas'").fetchone()
            has_nas = conn.execute("SELECT 1 FROM hosts WHERE lower(name) = 'nas'").fetchone()
            if not has_nas and legacy_nas and legacy_nas["value"]:
                conn.execute("INSERT INTO hosts(name, address, description) VALUES ('nas', ?, ?)",
                             (legacy_nas["value"], "NAS - z dawnego ustawienia $NAS"))
                changes.append("dodano host nas z dawnego ustawienia")
    for change in changes:
        log(conn, "panel", "migrate", change)
    return changes


def _init_defaults(conn: sqlite3.Connection) -> None:
    """Ustawienia domyslne tylko tam, gdzie ich nie ma - Twoich wartosci nic nie nadpisuje,
    takze gdy nowa wersja ma inne wartosci domyslne. Tiery wbudowane powstaja raz, na pustej
    bazie: skasowany tier nie wraca po restarcie ani po aktualizacji."""
    with conn:
        # alerty mialy wspolny limit z dziennikiem (audit_days) - po rozdzieleniu zostaje
        # ta sama liczba dni, zeby aktualizacja nie zmienila tego, jak dlugo leza alerty
        conn.execute("INSERT OR IGNORE INTO settings(key, value) "
                     "SELECT 'alerts_days', value FROM settings WHERE key = 'audit_days'")
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (key, value)
            )
        seeded = conn.execute("SELECT 1 FROM settings WHERE key = 'tiers_seeded'").fetchone()
        if not seeded:
            if not conn.execute("SELECT 1 FROM tiers LIMIT 1").fetchone():
                for tier in BUILTIN_TIERS:
                    conn.execute(
                        """INSERT OR IGNORE INTO tiers(name, description, client_mode,
                                                       host_access, rules_json, builtin)
                           VALUES (?, ?, ?, ?, ?, 1)""",
                        (tier["name"], tier["description"], tier["client_mode"],
                         tier["host_access"], json.dumps(tier["rules"])),
                    )
            conn.execute("INSERT INTO settings(key, value) VALUES ('tiers_seeded', '1')")


# --- ustawienia ---

def settings(conn: sqlite3.Connection) -> dict[str, str]:
    return {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings")}


CONFIRM_MIN, CONFIRM_MAX = 30, 900


def confirm_window(cfg: dict) -> int:
    """Czas na potwierdzenie zmian, zawsze w granicach 30-900 s.

    Zero albo smieci w ustawieniach oznaczalyby zastosowanie bez bezpiecznika -
    z panelu to niedozwolone (awaryjnie jest `wgctl apply --no-safe`).
    """
    try:
        value = int(str(cfg.get("confirm_seconds", "90")).strip())
    except (TypeError, ValueError):
        value = 90
    return max(CONFIRM_MIN, min(CONFIRM_MAX, value))


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    with conn:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


# --- peery ---

def peers(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(conn.execute(
        """SELECT p.*, t.name AS tier_name, t.client_mode, t.client_list, t.host_access, t.rules_json
           FROM peers p LEFT JOIN tiers t ON t.id = p.tier_id ORDER BY p.id"""))


def peer(conn: sqlite3.Connection, peer_id: int) -> sqlite3.Row | None:
    return conn.execute(
        """SELECT p.*, t.name AS tier_name, t.client_mode, t.client_list, t.host_access, t.rules_json
           FROM peers p LEFT JOIN tiers t ON t.id = p.tier_id WHERE p.id = ?""",
        (peer_id,),
    ).fetchone()


def next_free_ip(conn: sqlite3.Connection) -> str:
    cfg = settings(conn)
    net = ipaddress.ip_network(cfg["vpn"], strict=False)
    taken = {r["ip"] for r in conn.execute("SELECT ip FROM peers")}
    taken.add(cfg["server_ip"])
    for host in net.hosts():
        candidate = str(host)
        if candidate not in taken:
            return candidate
    raise RuntimeError("brak wolnych adresow w podsieci VPN")


def rules_for(conn: sqlite3.Connection, peer_id: int) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            "SELECT * FROM rules WHERE peer_id = ? ORDER BY position, id", (peer_id,)
        )
    )


def tiers(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(conn.execute("SELECT * FROM tiers ORDER BY id"))


def log(conn: sqlite3.Connection, actor: str, action: str, detail: str = "") -> None:
    with conn:
        conn.execute(
            "INSERT INTO audit(ts, actor, action, detail) VALUES (?, ?, ?, ?)",
            (int(time.time()), actor, action, detail),
        )


def shrink_runtime_settings(conn: sqlite3.Connection) -> int:
    """Kasuje rozdmuchane migawki z baz sprzed poprawki (migawka zawierala sama siebie)."""
    row = conn.execute("SELECT length(value) AS n FROM settings WHERE key='applied_state'").fetchone()
    size = row["n"] if row and row["n"] else 0
    if size > 200_000:
        with conn:
            conn.execute("DELETE FROM settings WHERE key = 'applied_state'")
        return size
    return 0


RETENTION_MAX = 36500   # 100 lat - wiecej nie ma sensu, a chroni przed literowka


def retention_days(cfg: dict, key: str) -> int:
    """Ile dni trzymac dziennik (audit_days) albo alerty (alerts_days); 0 = bez limitu.
    Zla wartosc w bazie (np. wpisana recznie) nie psuje strony ani harmonogramu -
    wtedy obowiazuje domyslna."""
    value = str(cfg.get(key, "")).strip()
    if value.isdigit() and int(value) <= RETENTION_MAX:
        return int(value)
    return int(DEFAULT_SETTINGS[key])


def prune_history(conn: sqlite3.Connection, now: float | None = None) -> tuple[int, int]:
    """Kasuje wpisy dziennika starsze niz audit_days i alerty starsze niz alerts_days.
    0 = bez limitu - wtedy ta czesc historii zostaje cala."""
    import time as _t
    cfg = settings(conn)
    moment = int(now if now is not None else _t.time())
    removed = []
    with conn:
        for table, key in (("audit", "audit_days"), ("alerts", "alerts_days")):
            days = retention_days(cfg, key)
            if days <= 0:
                removed.append(0)
                continue
            removed.append(conn.execute(f"DELETE FROM {table} WHERE ts < ?",
                                        (moment - days * 86400,)).rowcount)
    return removed[0], removed[1]


def audit_tail(conn: sqlite3.Connection, limit: int = 100,
               newest_first: bool = True, offset: int = 0) -> list[sqlite3.Row]:
    order = "DESC" if newest_first else "ASC"
    return list(conn.execute(
        f"SELECT * FROM audit ORDER BY id {order} LIMIT ? OFFSET ?", (limit, offset)))


def audit_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM audit").fetchone()[0]


def alerts_tail(conn: sqlite3.Connection, limit: int = 50, offset: int = 0) -> list[sqlite3.Row]:
    return list(conn.execute("SELECT * FROM alerts ORDER BY id DESC LIMIT ? OFFSET ?",
                             (limit, offset)))


def alerts_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]


def unseen_alerts(conn: sqlite3.Connection) -> int:
    return conn.execute(
        "SELECT COUNT(*) AS n FROM alerts WHERE seen = 0 AND severity != 'info'"
    ).fetchone()["n"]


def describe_expiry(peer, now: float | None = None) -> str:
    """Czytelny opis okna dostepu peera."""
    import time as _t
    moment = now if now is not None else _t.time()
    keys = peer.keys() if hasattr(peer, "keys") else peer
    mode = peer["access_mode"] if "access_mode" in keys else "none"

    def day(value):
        return _t.strftime("%Y-%m-%d", _t.localtime(value))

    def left_of(end_ts):
        left = int((end_ts - moment) // 86400)
        if left > 0:
            return _("za {n} dni", n=left)
        return _("dziś") if left == 0 else _("minęło")

    if mode == "from_use":
        days = peer["expires_days"] or 0
        if not peer["first_seen"]:
            return _("{n} dni od pierwszego połączenia (jeszcze nieużyty)", n=days)
        return f'{day(peer["expires_at"])} ({left_of(peer["expires_at"])})'

    if mode == "window":
        start_ts, end_ts = peer["starts_at"], peer["expires_at"]
        if start_ts and moment < start_ts:
            if end_ts:
                return _("start {start} do {end}", start=day(start_ts), end=day(end_ts))
            return _("start {start}", start=day(start_ts))
        if not end_ts:
            return _("od {start}", start=day(start_ts)) if start_ts else _("bezterminowo")
        return f'{day(end_ts)} ({left_of(end_ts)})'

    return _("bezterminowo")


def backups_tail(conn: sqlite3.Connection, limit: int = 20) -> list[sqlite3.Row]:
    return list(conn.execute("SELECT * FROM backups ORDER BY id DESC LIMIT ?", (limit,)))


def backups_list(conn: sqlite3.Connection, limit: int | None = None) -> list[sqlite3.Row]:
    """Kopie od najnowszej - wedlug czasu kopii, nie kolejnosci dopisania (kopie znalezione
    na NAS dopisuja sie pozniej, a bywaja starsze)."""
    sql = "SELECT * FROM backups ORDER BY ts DESC, id DESC"
    if limit:
        return list(conn.execute(sql + " LIMIT ?", (limit,)))
    return list(conn.execute(sql))


def backups_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM backups").fetchone()[0]



def hosts(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(conn.execute("SELECT * FROM hosts ORDER BY name COLLATE NOCASE"))


def host_map(conn: sqlite3.Connection) -> dict[str, dict]:
    return {row["name"].lower(): {"v4": row["address"], "v6": row["address6"]}
            for row in conn.execute("SELECT name, address, address6 FROM hosts")}


def render_cfg(conn: sqlite3.Connection) -> dict:
    """Ustawienia wzbogacone o katalog hostow - to, czego potrzebuje generator."""
    cfg = settings(conn)
    cfg["_hosts"] = host_map(conn)
    panel_port = (cfg.get("panel_port") or "").strip()
    if panel_port:
        # port panelu jest zarzadzany: kto nie ma do niego prawa, jest blokowany
        managed = [p.strip() for p in str(cfg.get("managed_host_ports", "")).split(",")
                   if p.strip()]
        if panel_port not in managed:
            managed.append(panel_port)
        cfg["managed_host_ports"] = ",".join(managed)
    return cfg


def seed_hosts(conn: sqlite3.Connection) -> None:
    """Katalog dostaje NAS i samo Pi z ustawien - jednorazowo, potem to Twoj katalog."""
    cfg = settings(conn)
    if cfg.get("seeded_nas") != "1":
        # raz: na pustym katalogu - skasowany "nas" nie wraca po restarcie
        if not conn.execute("SELECT COUNT(*) AS n FROM hosts").fetchone()["n"]:
            with conn:
                conn.execute(
                    "INSERT INTO hosts(name, address, description) VALUES ('nas', ?, ?)",
                    (cfg.get("nas") or SEED_NAS, "NAS - utworzony automatycznie z ustawien"))
        set_setting(conn, "seeded_nas", "1")
    if cfg.get("seeded_pi") != "1" and cfg.get("server_lan"):
        with conn:
            conn.execute(
                "INSERT OR IGNORE INTO hosts(name, address, description) VALUES ('pi', ?, ?)",
                (cfg["server_lan"], "Raspberry Pi - serwer VPN"))
        set_setting(conn, "seeded_pi", "1")


def expiry_warning(peer, warn_days: int, now: float | None = None) -> str:
    """Krotki tekst ostrzezenia, jesli okno konczy sie w ciagu warn_days dni."""
    import time as _t
    if not warn_days or peer["state"] != "active" or not peer["expires_at"]:
        return ""
    moment = now if now is not None else _t.time()
    left = peer["expires_at"] - moment
    if left <= 0 or left > warn_days * 86400:
        return ""
    days = int(left // 86400)
    if days == 0:
        hours = max(1, int(left // 3600))
        return _("kończy się za {n} h", n=hours)
    return _("kończy się jutro") if days == 1 else _("kończy się za {n} dni", n=days)
