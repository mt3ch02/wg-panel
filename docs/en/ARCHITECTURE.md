# wg-panel — architecture and internals

*[Wersja polska → `docs/pl/ARCHITEKTURA.md`](../pl/ARCHITEKTURA.md)*

Technical documentation: how the panel is built, what each module does, how the rules, the safety fuse, HTTPS, and backups work. Useful when changing the code or diagnosing problems. **To install the panel, start with [INSTALL.md](INSTALL.md).** The Polish version ([ARCHITEKTURA.md](../pl/ARCHITEKTURA.md)) is the more detailed one; this one covers the same ground more concisely.

Contents: [Architecture](#architecture) · [Rule model](#rule-model) · [Safety fuse](#safety-fuse) · [HTTPS](#https) · [Backups](#backups) · [Alerts and watchdog](#alerts-and-watchdog) · [Security layers](#security-layers) · [Layout and files](#layout-and-files) · [Known limitations](#known-limitations)

## Architecture

```
browser                 wgctl (terminal)
   |  HTTPS                |
   |  (wg0 and LAN only)   |
   v                       v
   +--- SQLite: source of truth ---+
                |
                |  unix socket, JSON, allow-list of operations
                v
      helper/wg_panel_helper.py      root
                |
                v
/etc/wireguard/wg0.conf  +  chains WG-USERS / WG-USERS-HOST
```

**SQLite is the source of truth.** `wg0.conf` and the rules file are *generated*. Don't edit them by hand — the next apply overwrites them.

**The panel never sends commands or ready-made text to be written.** It sends a data model: settings and a list of peers with normalized rules. The helper validates everything again and renders the files itself. Even if someone took over the web process, they still couldn't run anything the helper didn't anticipate.

**The generation logic is deliberately duplicated** in `panel/render.py` and in the helper. The panel computes the preview, the helper computes the actual state. 

**The server's private key never reaches the database or the panel.** The helper reads it from disk on every render and substitutes it for a placeholder.

## Access windows

Every peer has one of three modes, chosen explicitly:

| Mode | When to use | What the scheduler does |
|---|---|---|
| **no expiry** | household, your own devices | nothing |
| **time window (from–to)** | a crew from 1 to 31 October | opens access on the start day, closes after the end date |
| **N days from first use** | a guest whose file you hand out today and who connects whenever | the countdown starts at the first handshake |

A peer created with a start date in the future waits suspended and switches on by itself. The scheduler only touches states it set itself: if **you** suspend someone by hand, they are not resumed when their window opens. After every automatic window change the panel applies the configuration itself — without the fuse, because nobody is around at 3 a.m. to confirm. That is safe because closing someone else's window never affects the administrator's access.

## Access to the panel

The panel listens on the addresses in `WGPANEL_HOST` (a comma-separated list or `0.0.0.0`); `127.0.0.1` is always included, and an address that doesn't exist yet (`10.0.0.1` before `wg0` is up) is watched in the background. Installing with `LAN_ACCESS=192.168.1.0/24` exposes the panel to the home network and adds a ufw rule.

From the VPN, **only peers with panel access ticked** (`panel_access`) can open the panel: the panel port is in the managed ports of the `WG-USERS-HOST` chain, so everybody else gets DROP despite being on the VPN. The switch is on the person's page; in a terminal `wgctl set NAME --panel on`.

## What goes through the VPN on the client

The `AllowedIPs` line in the client file is computed from a mode — the tier's, or overridden per person.

| Mode | AllowedIPs |
|---|---|
| `full` — all traffic | `0.0.0.0/0` (+ `::/0` with IPv6), with DNS |
| `auto` — only what they have access to | targets of the person's and tier's ACCEPT rules, collapsed; + the Pi's address when the tier has host ports |
| `lan` — the whole home network | VPN + LAN |
| `custom` — a custom list | names from the catalog and addresses |

By default ADMIN and NET are `full`, NAS is `auto`. Adding an ACCEPT rule in `auto` mode widens the client file by itself. The panel remembers what scope a person got at the last issue and warns when the current file lacks something. `AllowedIPs` is routing, not permissions — only the rules on the server decide access.

## LAN host catalog

The **Hosts** tab. Every entry has a name, an IPv4 address, optionally an IPv6 address, and a description. In rules you write the name — `nas`, `printer` — and the generator substitutes the address on every apply. Changing the NAS address is one field instead of going through every rule and tier.

You can't delete a host that has at least one rule on it — the panel shows where it is used. The names `LAN` and `ANY` are reserved.

## IPv6

Off by default. After turning it on (Settings → IPv6):

- every peer gets an address matching its IPv4: `10.0.0.4` → `fd42:42:42::4`;
- `wg0.conf` gets a second server address and `AllowedIPs` with `/128`;
- rules are generated a second time, for `ip6tables`, with the same chains;
- `LAN` in a rule expands to **both** the IPv4 and all IPv6 prefixes of the home network;
- a catalog host that has an IPv6 address works in both families.

**The home network's IPv6 prefix is required.** Without it the `LAN` block would have no IPv6 counterpart and the NAS tier would see the whole LAN over IPv6 — the panel refuses to enable it. The "Detect LAN prefix" button reads the WAN interface's routes and suggests one.

⚠ The helper sets `accept_ra=2` on the WAN interface **before** `forwarding=1`. The order matters: enabling forwarding disables accepting router advertisements by default, and the Pi would lose its IPv6 default route. The settings go into `/etc/sysctl.d/99-wg-panel-ipv6.conf`; turning IPv6 off in the panel removes that file and the `ip6tables` chains.

After enabling IPv6, peers need **new configuration files** — the old ones have no IPv6 address.

## Traffic history

The scheduler saves counters from `wg show dump` every 5 minutes. Hourly series are computed from the differences between consecutive samples; a counter drop means an interface restart and is handled. Samples older than 8 days are deleted.

**Anomaly:** the last full hour is larger than N times the median of the week's hours (default 10×) and above a threshold (default 500 MB). The median is computed only from hours with any traffic, and only after 12 such hours — so a new peer doesn't alarm right away.

## Integrity

Every 5 minutes the panel compares the state with what it applied itself:

| Check | Detects |
|---|---|
| SHA-256 of `wg0.conf` and the rules files | manual file edits |
| file permissions | keys readable by other accounts |
| peer list on the interface | a key added with `wg set`, bypassing the panel |
| number of rules in the chains | rules appended to the panel's chains |
| the first `FORWARD` rule | an ACCEPT inserted before the panel's chain |

Any discrepancy is an alert and a red banner on the peer list. Restoring = re-applying from the database, with the fuse.

⚠ This is detection, not protection. It works against changes made by hand or by someone who doesn't know about the panel. Someone with root on a running Pi who knows what they're doing can disable the panel itself too — no software on the same machine protects against that.

## Account

Sign-in with a password; the authenticator code (TOTP) is optional and toggled in Settings → Account. Both changes — TOTP and password — require the current password. Changing the password ends all of that account's sessions.

The database on the SD card is deliberately not encrypted: the Pi sits at home, and encrypting with a key from outside the card would mean the tunnel doesn't come back on its own after a power cut.

## QR codes

A pure-Python encoder (`panel/qrcode.py`): byte mode, versions 1–40, automatic choice of version, error-correction level and mask per ISO/IEC 18004. A configuration with IPv6 fits in about version 14. Verified by decoding with OpenCV on texts of various lengths and all eight masks. `wgctl qr` draws the code in the terminal with half-block characters.

## wgctl

The same as the panel, from a terminal. The same database, the same helper, no parallel logic.

```bash
wgctl status                      # tunnel state, unapplied changes
wgctl list                        # peers with handshake and expiry date
wgctl add Marek --tier NAS --device laptop
wgctl add Crew --tier NAS --start 2026-10-01 --until 2026-10-31
wgctl add Guest --tier NAS --days 7
wgctl config Marek                # client file to stdout
wgctl qr Marek                    # QR code in the terminal
wgctl rule add Marek --accept 192.168.1.60 --proto tcp --port 9091 --comment nas
wgctl rule list Marek
wgctl check Marek 192.168.1.11 445 -v    # simulation with the whole chain walk
wgctl suspend Marek / resume / rotate / delete --yes
wgctl expire                      # access deadlines now (without the panel)
wgctl guard                       # the watchdog by hand
wgctl diff                        # what the next apply changes
wgctl apply                       # with the fuse; then wgctl confirm
wgctl backup                      # a backup per settings, verified at once
wgctl backup --verify             # check the checksums of the latest backups
wgctl host list / host add printer 192.168.1.70 --ip6 fd00:77::70
wgctl integrity                   # did someone change the config outside the panel (exit 3 = yes)
wgctl totp off admin              # emergency: turn off the authenticator code
wgctl traffic Marek               # traffic for the last 7 days, day by day
wgctl export /mnt/nas/backup.json.enc --pass secret
```

`wgctl check` returns exit code 0 for ACCEPT and 2 for DROP, so it suits scripts and monitoring. (Output and messages of `wgctl` are currently in Polish.)

## Rule model

Rules go into the `WG-USERS` chain hooked at the start of `FORWARD`. The order in the chain is the order of checking:

```
1. -o wg0 -j DROP                      traffic between peers blocked
2. peer 1's custom rules               (more specific - higher)
3. peer 1's tier rules
4. ... the same for the next peers
5. -j ACCEPT                           no match = full access
```

**A peer's custom rules are checked before the tier's rules.** That matters: the NAS tier ends with a block of the whole LAN, so if a custom rule came after it, an exception for a single host would never work. With this order a custom rule overrides the tier both ways — it can add an exception to a block and it can take away access the tier gives.

Point 5 implements the ADMIN tier: a peer with no rules falls through to the final ACCEPT.

Access to the server itself (SSH, RDP) lives in a separate `WG-USERS-HOST` chain on `INPUT` — because that traffic never passes through `FORWARD`. You set it with the "host ports" field of a tier.

In the `dest` field: a specific address, a CIDR, `LAN` (expanded to the subnet from Settings), a host name from the catalog (e.g. `nas`) or `ANY`.

## Safety fuse

By default every `apply` arms a timer. Without confirmation within the time window (default 90 s) the helper restores the previous configuration and applies it again. This protects against the one error you can't fix remotely — a rule that cuts you off from the panel.

Backups: the last 40 versions in `/etc/wireguard/wg-panel-backups/`.

**Automatic confirmation (default).** After applying, the browser itself checks that it still reaches the panel and confirms — usually in a fraction of a second, without clicking. You see only a green "Changes applied.". The yellow bar with a countdown appears only when the check takes longer than 3 seconds (e.g. when bringing the tunnel up for the first time or when something is wrong). Proof is only a connection made **after** the apply: while waiting, every panel response closes the connection (`Connection: close`), and a confirmation sent over an older, already-open connection is rejected with a request to retry — such a connection could pass through rules that a new one is blocked by. Changes applied by `wgctl apply` are always confirmed by hand (`wgctl confirm` or the button on the bar).

**A restart while waiting.** A pending change survives a restart of the helper and of the whole Pi: the helper keeps it in `/etc/wireguard/wg-panel-pending.json` (root only, the token only as a hash) and after startup gives a fresh window for confirmation — without confirmation it rolls back as usual. A fresh window rather than the saved deadline, because the Pi has no hardware clock and right after boot the time is often wrong. The trace of an automatic rollback also stays on disk, so the panel learns about it even after another restart.

**Manual confirmation** (Settings → Advanced → Confirming changes): the bar waits for a click on "Confirm, it works". Useful when you want to check something outside the panel before confirming, e.g. NAS access from a phone.

From the panel, applying **always** goes through the fuse — there is no switch to disable it, and the time from settings is clamped to 30–900 s. Without the fuse only in an emergency: `wgctl apply --no-safe`.

While a change waits, a bar with a counter and the buttons "Confirm, it works" and "Roll back now" hangs at the top of **every** page. The bar polls the panel (`/api/pending`), so it disappears at once after confirmation (also from another tab or `wgctl confirm`) and says so plainly after an automatic rollback. "Roll back now" rolls back only a change that is really pending — never a backup.

## HTTPS

The panel serves HTTPS from its own certificate authority. The installer creates it, and the helper (root) issues the panel certificate (ECDSA P-256, 397 days) and renews it — 30 days before expiry and after the Pi's address changes. The panel reloads the certificate without a restart. The authority has **name constraints** (X.509 Name Constraints): it can vouch only for the home subnet, the VPN and the Pi's name, so installed on a phone it can't be used to forge any other site — even with a stolen key. The authority's key is in `/etc/wireguard/wg-panel-ca/` (root only); the panel gets only its own certificate in `/var/lib/wg-panel/tls/`.

HTTPS and HTTP share one port: plain HTTP from the network is redirected to HTTPS, while from `127.0.0.1` (the SSH tunnel) it stays HTTP — that traffic never leaves the Pi. The authority certificate with instructions for iPhone, Android, Windows and macOS is on the `/tls` page (no login); the SHA-256 fingerprint to compare is printed by the installer and `wgctl tls` (`wgctl tls --renew` renews at once).

**Certificate mode** (Settings → HTTPS → Certificate):
- **the panel's own authority** (default) — as above;
- **your own certificate from files** (e.g. Let's Encrypt) — you give paths to the full chain and the key. The files must live in `/etc/letsencrypt/` or `/etc/ssl/wg-panel/` (the helper runs as root, so it won't copy a file from just anywhere) and the key must match the certificate. The helper checks them every 12 h — a renewal by certbot reaches the panel by itself, no restart. Nothing has to be installed on devices.

**Reverse proxy on another server** (optional, e.g. Caddy) — Settings → HTTPS → "Reverse proxy on another server". The panel keeps its own HTTPS; you enter the proxy's address, and the panel trusts `X-Forwarded-For` / `X-Forwarded-Proto` headers **only** from that address (from other devices they are ignored) and takes the **last** address from `X-Forwarded-For` — the proxy appends that one; earlier ones can be supplied by the client to get around the lockout after failed logins. The proxy connects to the Pi over HTTPS and verifies the panel's certificate (the `wg-panel-ca.crt` file from the `/tls` page), so the leg to the Pi is encrypted too. A snippet for `Caddyfile` and an nginx version are in the panel. You install neither nginx nor Caddy on the Pi itself.

Through a proxy, anyone who can reach the proxy sees the login page — including VPN people the panel itself would not show it. Turn on the authenticator code (TOTP) and restrict access to the panel in the proxy itself. The Pi's firewall must let the proxy in on the panel port (`LAN_ACCESS` in the installer). "Public panel address" (e.g. `https://vpn.example.com`) goes into links in notifications.

Without HTTPS: `TLS=0 ./install.sh`. Installer messages in Polish: `INSTALL_LANG=pl ./install.sh` (English by default).

## Alerts and watchdog

**ntfy** (the main channel): the message goes as JSON, so Polish characters in the title arrive correctly. The most serious events — the same key from a new address, a configuration change outside the panel, `wg0` down — have the highest priority; tapping a notification opens the panel's alerts page. An optional token (`tk_...`) lets you use a protected topic (ntfy.sh with a reserved topic, or your own ntfy server). Anyone who knows a topic's name on the public ntfy.sh can read it — the panel warns if the name is short or easy to guess.

**E-mail** (optional, alongside ntfy): plain SMTP with login (STARTTLS or SSL); preferably a separate account and an app password, since the password is stored on the Pi. Like other secrets it is left out of backups. Alerts can also go to a **webhook** (JSON).

**The watchdog** (`wg-panel-guard.timer`, every minute, outside the panel process — `wgctl guard`) checks the panel (the service and the page's response), the helper, the `wg0` interface, the Pi's temperature, free space on the card, under-voltage and the HTTPS certificate's validity. The alarm fires only after two failed checks in a row (a short restart won't trigger it), once; after the fix — one "working again". During an update the installer pauses the watchdog. It won't notify you if the whole Pi goes down — that needs monitoring from outside (e.g. Uptime Kuma on another machine).

**Access deadlines independent of the panel:** the same timer cuts people off after their deadline and opens access windows even when the panel is down (`wgctl expire` does the same by hand). The change goes onto the interface surgically — it takes what is really running and changes only the state of people whose deadline changed. Your unapplied changes stay unapplied. When a change is waiting for confirmation, the scheduler waits a minute. The panel and the timer don't work at the same time (a file lock).

Panel and helper services have `Restart=always` — systemd brings them up after every exit.

| Event | When | Severity |
|---|---|---|
| `new_endpoint` | the same key connects from a different IP address | alarm |
| `dead_peer` | no handshake for longer than the threshold | warning |
| `expired` | a peer's access ran out and it was suspended | info |
| `window_open` | a peer's time window opened (in the log) | — |
| `backup` | a backup failed | alarm |
| `apply` | an automatic apply failed | alarm |

`new_endpoint` is the most important — it is the first sign that a configuration file leaked. A change of port alone is ignored (normal with NAT); a change of address counts. The same alert doesn't repeat more often than `alerts_quiet_minutes`.

## Languages

The panel comes in Polish and English. The switch with flags is next to the theme switch — also on the login page and in the phone menu. The browser remembers the choice (the `lang` cookie); messages are produced right away in the language of the person who clicked.

Polish text is the translation key (`panel/i18n.py`, the `EN` dictionary), so readable Polish stays in the code and a missing translation never leaves a blank. `wgctl`, notification text and the comments in the code are in Polish.

## Phone

The panel works in a phone browser without sideways scrolling (checked from 320 px). Below 720 px the navigation hides behind a "Menu" button (with an alert counter), the peer list turns into cards and other tables into "label: value" lists. On touch screens form fields are 16 px, so an iPhone doesn't zoom in at login, and buttons are finger-sized. On a desktop with a mouse nothing changes.

## Action confirmations

One confirmation dialog for the whole panel (the `ui.ask()` function → `data-confirm*` attributes). It asks only for irreversible actions or ones that cut the connection: deleting, rotating and deleting a key, discarding changes, rolling back, restarting, restoring a backup. Saving, adding and suspending a peer work without asking. The button in the dialog names the action ("Delete peer", not "OK"); for destructive actions focus starts on "Cancel" so Enter deletes nothing. Errors show on a red bar that doesn't disappear by itself; green — success only.

## Backups

A backup is a single JSON: settings, tiers, peers, rules and accounts. From it you rebuild the panel on a clean card — `wg0.conf` and the iptables rules are generated anew at the first apply.

Targets: **a directory on disk** (may be a mount point of a share) or **a NAS over SMB** (`smbclient`, the password passed through an environment variable, not in arguments — so it doesn't leak into the process list).

⚠ A backup contains the PSKs of all peers. If it is to live on a network share, set a password in the "backup password" field — the file is then encrypted. Without it, it lands there in plain text.

Encryption construction: scrypt (N=2¹⁵) → two keys, ChaCha20 (RFC 8439) for encryption, HMAC-SHA256 to authenticate the ciphertext. The implementation was checked against the RFC test vectors.

**Checksums.** Every backup gets a SHA-256 stored in the database and in a `.sha256` file next to it, in `sha256sum -c` format. Right after writing, the panel fetches the backup from where it really lives — the NAS too — and checks the sum; for an encrypted backup it also opens it. After every daily backup the three previous ones are checked as well, catching later corruption or a swapped file on the NAS. A mismatch is an alarm.

SMB passwords and the backup password **don't leave in an export** — after a restore on a new machine you enter them once more.

## Security layers

| Layer | What it protects against |
|---|---|
| Process separation | taking over the panel doesn't give root |
| Allow-list of operations | the helper has no notion of "run a command" |
| Validation on the helper side | injection in addresses, ports, comments, chain names |
| Arguments as a list, never a shell | metacharacters in data are harmless |
| SMB path validation | escaping the directory on the share |
| scrypt, attempt lockout, optional TOTP | password guessing |
| Login throttling | 5 attempts per 5 minutes per address |
| CSRF token on every POST | requests from foreign sites |
| HttpOnly + SameSite=Strict cookie | session theft |
| Bind only on wg0 (and the LAN if you enable it) | access from the internet |
| systemd unit hardening | consequences of a break-in on the web process |
| Server key outside the database | a database leak doesn't hand over the server's identity |
| Deleting client keys | a database leak doesn't hand over the peers' identities |
| Backup encryption | a leak of a file from a network share |
| Address-change alert | detecting a leaked configuration file |
| Integrity check | manual configuration changes bypassing the panel |
| Backup checksums | swapping or corrupting a backup on the NAS |
| Required IPv6 LAN prefix | a back door in IPv6 next to the IPv4 block |

## Layout and files

```
wg-panel/
├── panel/            web panel, database, scheduler, backups, alerts, simulator
├── helper/           wg_panel_helper.py - the only component with root
├── systemd/          units: helper, panel, watchdog (+ timer)
├── wgctl             CLI (the same operations as the panel)
├── install.sh        installer (copies the program to /opt/wg-panel)
├── uninstall.sh      uninstaller (three levels)
└── docs/             documentation in Polish and English, screenshots
```

### Components and who can do what

| Component | Service | Runs as | What it does |
|---|---|---|---|
| WireGuard | `wg-quick@wg0` | the kernel | the tunnel itself; works without the panel too |
| helper | `wg-panel-helper` | root | **the only part with privileges** — writes files in `/etc/wireguard`, calls `wg`, `iptables`, `systemctl`, guards the fuse |
| panel | `wg-panel` | account `wg-panel` | web pages, database, scheduler, backups, alerts; no root — asks the helper for anything needing privileges |
| watchdog | `wg-panel-guard.timer` | account `wg-panel` | every minute: access deadlines and a check that everything works |
| `wgctl` | — | you (group `wg-panel`) | the same operations as the panel, from a terminal |

### How they connect

```
 your devices                       Raspberry Pi
 browser ── HTTPS :8080 ─────────▶ panel (wg-panel)
                                     │  database /var/lib/wg-panel/panel.db
 wgctl (terminal) ─────────────────▶ │
 watchdog (every minute) ──────────▶ │
                                     ▼  socket /run/wg-panel/helper.sock
                                   helper (root)
                                     │  writes wg0.conf and rules,
                                     │  calls wg, iptables, systemctl
                                     ▼
 WireGuard app ─ UDP :51820 ─────▶ WireGuard (wg-quick@wg0)
                                     │
                                     ▼
                                   home network
 NAS ◀── SMB: backups ───────────── panel
 phone ◀── ntfy: alerts ─────────── panel and watchdog
```

### Directory structure after installation

```
/opt/wg-panel/                the program (root, read-only)
├── panel/  helper/  wgctl  uninstall.sh
/usr/local/bin/wgctl          wrapper - wgctl without paths

/etc/systemd/system/
├── wg-panel-helper.service   helper (root)
├── wg-panel.service          web panel (account wg-panel)
├── wg-panel-guard.service    watchdog and access deadlines
└── wg-panel-guard.timer      runs the watchdog every minute

/var/lib/wg-panel/            panel data (account wg-panel)
├── panel.db                  the database - source of truth
├── panel.db.schedule.lock    lock: panel and watchdog not at once
├── backups/                  backups on the card (+ .sha256)
├── tls/                      panel certificate, the authority to download
└── watchdog.json             the watchdog's memory

/run/wg-panel/helper.sock     the helper's socket (created at start)

/etc/wireguard/               root only
├── wg0.conf                  WireGuard configuration - from the panel
├── wg0-panel-rules.v4, .v6   firewall rules - loaded by wg0.conf
├── server_private.key        the server key (copy in your password manager)
├── wg-panel-backups/         files from before every change
├── wg-panel-ca/              the panel's certificate authority
└── wg-panel-pending.json     a change waiting for confirmation

/etc/sysctl.d/
├── 99-wg-panel-forward.conf  packet forwarding (VPN → LAN)
└── 99-wg-panel-ipv6.conf     IPv6 in the tunnel (when enabled)

/root/wg-panel-before/         the pre-panel snapshot (path A, Step 2)
```

**The database is the source of truth.** The files in `/etc/wireguard` are generated from it on every "Apply changes" — a manual change in `wg0.conf` disappears at the next apply (and the watchdog reports it as a change outside the panel).

### Program — what is in which file

| File | What it does |
|---|---|
| `panel/app.py` | web server: page addresses, login, HTTPS, applying and confirming changes |
| `panel/ui.py` | the look of the pages (HTML), `panel/static/style.css` — styles |
| `panel/i18n.py` | languages: Polish and English |
| `panel/db.py` | SQLite database: tables, default settings, cleanups after older versions |
| `panel/model.py` | exactly what goes to the helper on apply |
| `panel/render.py` | generating `wg0.conf`, firewall rules and client files |
| `panel/schedule.py`, `scheduler.py` | access deadlines; a background thread: alerts, daily backups, integrity |
| `panel/watchdog.py` | the watchdog |
| `panel/notify.py` | notifications: ntfy, webhook, e-mail |
| `panel/export.py`, `cryptobox.py` | backups, encryption, NAS, restore |
| `panel/auth.py`, `wgcrypto.py` | sessions, CSRF, lockout after failed logins; WireGuard keys and TOTP |
| `panel/simulate.py` | the rule simulator |
| `panel/traffic.py` | traffic history and anomaly detection |
| `panel/qr.py`, `qrcode.py` | QR codes for phones |
| `panel/import_conf.py` | one-off import of an existing `wg0.conf` (path A) |
| `panel/helperclient.py` | the panel's conversation with the helper over the socket |
| `helper/wg_panel_helper.py` | the helper (root) |

### What happens after "Apply changes"

1. The panel builds a full picture from the database: people, keys, tiers, rules, settings — and sends it to the helper.
2. The helper stores the current files in `wg-panel-backups/`, generates the new `wg0.conf` and rules.
3. It introduces them without dropping connections: `wg syncconf` (people) and `iptables-restore` (rules).
4. It arms the fuse (90 s by default) and writes `wg-panel-pending.json`.
5. The browser confirms the change with a **new connection** — if it gets through, the change stays. If not (e.g. a rule cut off your access), after the time the helper restores the previous files.

### What happens at Pi startup

1. `wg-quick@wg0` brings the tunnel up from `wg0.conf`; its `PostUp` creates the firewall chains and loads the rules — **the VPN works before the panel is up**.
2. `wg-panel-helper` — if a change was waiting for confirmation before the restart, it gives a fresh window and rolls it back if nobody confirms.
3. `wg-panel` — the web panel; checks the HTTPS certificate.
4. `wg-panel-guard.timer` — the watchdog first 2 minutes after boot, then every minute.

### Where to look when something doesn't work

| What you're looking for | Command |
|---|---|
| the web panel (page errors, login) | `journalctl -u wg-panel -f` |
| the helper (applying changes, files, certificate) | `journalctl -u wg-panel-helper -f` |
| the watchdog and access deadlines | `journalctl -u wg-panel-guard -n 20` |
| WireGuard itself (tunnel start) | `journalctl -u wg-quick@wg0 -n 50` |
| who changed what in the panel | panel → **Audit log** |

### Can you keep working in text mode?

Yes. `wgctl` covers everything the panel does. Besides:

- **the database is plain SQLite** — `sqlite3 /var/lib/wg-panel/panel.db` works, you'll see changes in the panel after a refresh, and on the interface after `wgctl apply`;
- **iptables outside the `WG-USERS` chain** are left alone by the panel — an ad-hoc rule in `FORWARD` survives, because only its own chains are cleaned;
- **`wg set`** works as always, with the same caveat: until a restart;
- **`wg0.conf` is generated** — the only place it makes no sense to edit by hand.

## Known limitations

- **The defaults assume a typical home network** (`192.168.1.0/24`, NAS `192.168.1.11`, Pi `192.168.1.10`, WireGuard port `51820`). Check them in Settings before the first apply — above all the LAN subnet (Step 9 of the install guide).
- **`wgctl`, notification text and code comments are in Polish.** The panel UI and the installer come in Polish and English.
- In the simulator, the comments of the built-in tier rules (e.g. "reszta LAN zablokowana") appear in Polish even in the English UI.
- Tested on Raspberry Pi OS (Debian). Other distributions with systemd, `iptables` and WireGuard should work, but haven't been checked.
- IPv4 only by default; IPv6 is an option (see the IPv6 section above).

## Ideas for the future

1. **An audit log shipped off the Pi** — a copy of the audit on the NAS that can't be wiped from the Pi.
2. **A notification about the end of an access window to the alert channel** — today it's a badge in the panel.
3. **Network auto-detection at install time** — LAN subnet, Pi address and WAN interface suggested instead of example values.
4. **English messages in `wgctl` and notifications.**
