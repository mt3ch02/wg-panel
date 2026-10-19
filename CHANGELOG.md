# Changelog

All notable changes to wg-panel. The version is in the [`VERSION`](VERSION) file; the panel shows it
at the bottom of every page and `wgctl --version` prints it.

Updating is always the same: `cd wg-panel && git pull && sudo ./install.sh`. Your data, settings and
client files stay as they are unless a release says otherwise.

## [1.4.0] — 2026-10-19

### Restore
- **Restore from a backup file:** `sudo ./install.sh --restore FILE` — when the NAS is unavailable or
  the backup is on your computer. A `.sha256` file next to it is checked; only the backup password is asked.
- **Restore check:** `sudo ./install.sh --restore --check [FILE]` — downloads, verifies, decrypts and
  describes the backup (date, panel version, people, server key, server name and address, install
  options). Changes nothing, so it runs on a working server.
- **Test server:** `sudo ./install.sh --restore --test [FILE]` — for trying a restore on a second machine
  while the real server keeps running. Automatic backups and notifications are off, people's files,
  QR codes and links are not issued, every page shows a red bar and the server name gets "(test)".
  The button "This is now the real server" (or `wgctl test off`) turns it back into a normal server.
- **Install options travel with the full backup** (LAN access, panel port, HTTPS, language). Options
  given explicitly still win.
- **Name and address check before restoring.** A different server name would mean a new certificate
  authority (and installing `ca.crt` again on every device); the installer offers to set the old name.
- **Restoring on another machine** takes the server address, the network interface and (when it
  doesn't fit) the home network from that machine, not from the backup.

### Network
- **New installs are reachable from the home network by default** (`LAN_ACCESS=auto` — the server's own
  network only). `LAN_ACCESS=off` keeps the panel on the VPN and an SSH tunnel. Updates and restores
  keep the earlier choice: a panel set up for the VPN only is never opened to the LAN on its own.
- A saved network that no longer matches the server (another computer, a new router) switches to the
  server's network by itself; the old ufw rule is removed. Without a route to the internet, `auto`
  uses the server's first private network.
- **The server's address follows the machine.** Change it on the router and within a minute the panel
  updates the setting and the matching host in the catalog, renews the HTTPS certificate and raises an
  alert about the port forward. The field in Settings is read-only.
- **The WireGuard port in ufw** is kept by the panel (rule `# wg-panel WireGuard`), moved when the port
  changes, and left in place on uninstall.

### Uninstall
- Without an option `uninstall.sh` asks what to remove: the panel only / the panel and its data /
  back to the pre-panel configuration / **the panel and all of WireGuard** (`--wireguard`: tunnels,
  `/etc/wireguard` including the server key, packages and the ufw rule — confirmed with the phrase
  `remove wireguard`, even with `--yes`).

### Other
- Version number: `VERSION` file, shown at the bottom of every page (after signing in), in
  `wgctl --version`, by the installer ("Updating wg-panel: 1.3.2 -> 1.4.0") and stored in backups.
- Messages about the backup share use the device name from Settings (default "NAS") and generic hints.
- A pre-panel snapshot made by older versions in `/root/przed-wg-panel` is still used.
- The installer's summaries show whether LAN access is on and how to update (`git pull`).
- "Folder on share" label; aligned fields in Backup settings; new screenshots.

## [1.3.2] — 2026-10-18

### Fixed
- The traffic chart card is always on the peer list. Before there is data it shows a note with the
  sampling interval ("The panel samples traffic every 5 minutes. The chart appears once it has the
  first two samples.") instead of nothing.
- The chart appears without a reload, on the peer list and on a person's page.
- The note on a person's page no longer says "after the first hour of traffic" — it takes 5–10 minutes.
- No more negative handshake times ("-107 s ago") when the clock of the computer you sign in from is
  behind: the time is counted by the server's clock.

## [1.3.1] and earlier

See the [releases on GitHub](https://github.com/mt3ch02/wg-panel/releases).

[1.4.0]: https://github.com/mt3ch02/wg-panel/releases/tag/v1.4.0
[1.3.2]: https://github.com/mt3ch02/wg-panel/releases/tag/v1.3.2
[1.3.1]: https://github.com/mt3ch02/wg-panel/releases/tag/v1.3.1
