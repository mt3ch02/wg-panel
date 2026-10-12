# wg-panel — installation and operation guide

*[Wersja polska → `docs/pl/INSTALACJA.md`](../pl/INSTALACJA.md)*

This guide walks you step by step through installing the panel on a Raspberry Pi (or another Debian/Ubuntu machine with systemd), bringing up the VPN, setting up alerts and backups, and then day-to-day operation and troubleshooting. For how the panel is built internally, see [ARCHITECTURE.md](ARCHITECTURE.md).

> **Is the panel already running for you?** Don't go through the paths again — install a new version as described in "Updating the panel" at the end of this guide. It's two commands, and your peers and settings stay.

> **The addresses in this guide are examples:**
>
> | Item | Example |
> |---|---|
> | home network (LAN) | `192.168.1.0/24` |
> | Raspberry Pi on the LAN | `192.168.1.10` |
> | NAS / SMB file share | `192.168.1.11` |
> | VPN network / Pi's address inside the VPN | `10.0.0.0/24` / `10.0.0.1` |
> | WireGuard port | `51820/udp` |
> | internet-facing interface | `eth0` |
>
> **Substitute your own.** The installer detects your home network, the Pi's address and the interface by itself; on a takeover the port and the VPN network come from your `wg0.conf`. Check them in Settings (Step 9) — the "whole home network is blocked" rule only works if the panel knows what your network is.

# How to read this guide

## Pick your path first

| Situation | Path | What you do |
|---|---|---|
| a WireGuard server is already running on the Pi (`/etc/wireguard/wg0.conf`) and you want the panel to take it over without touching clients | **A** | Steps 1–21 |
| clean system, first VPN, or a fresh start with new keys | **B** | Steps B1–B13, then 18–21 |
| clean system (new SD card, new Pi), and you have a panel backup on your NAS | **C** | Steps C1–C5, then 21 |

> **How B and C differ for your users.** B creates a new server key, so everyone needs a new file. With a full backup, C brings back the old key and everyone — nobody changes anything on their side.

Do the steps in order, without skipping. Parts V–VII are reference material for when you need it.

Every step starts with a **Where** line that tells you what you are sitting in front of:

| Label | Meaning |
|---|---|
| **Pi** | a terminal on the Raspberry Pi — over SSH from a computer at home |
| **Computer** | a terminal on your computer on the home network |
| **Browser** | the panel open in a browser on your computer |
| **Phone** | your phone |
| **NAS** | your NAS admin UI (the examples use Synology DSM) |

Under a command you'll find a description of what you should see. A block quote (indented, with a bar on the left) explains *why* we do something — you can skip it while deploying. A block quote starting with ⚠ is something you must not skip.

> ⚠ Do the whole deployment **at home**, connected to the home network, **not through the VPN**. You are changing the configuration of that same VPN — LAN access is your emergency way back in.

> **Backup target.** Backups go over SMB to any network share — any NAS, or a Samba server on a Linux box.

> **Tiers.** The panel ships with three built-in access levels: **ADMIN** (whole LAN, SSH and RDP to the Pi, the panel, all traffic through the VPN), **NET** (internet through the VPN only, LAN blocked) and **NAS** (a single host — the NAS — on ports 80, 443, 445; everything else goes out through the person's own internet). You can edit them and add your own.

---

# PATH A — taking over an existing WireGuard setup

The panel takes over the same keys and the same addresses, so **clients change nothing** — nobody gets a new file or scans a new QR code.

> **What changes for you:** `wg0.conf` stops being a file you edit by hand — from the first apply on, the panel generates it. The rules from `PostUp` are replaced by two panel chains (`WG-USERS`, `WG-USERS-HOST`). Your ufw rules for SSH and RDP stay as they are.

# PART I — Preparation

## Step 1 — Check that the Pi has everything

**Where: Pi**

```bash
python3 -V
command -v wg wg-quick iptables
sudo wg show wg0 | head -5
sudo apt install -y smbclient
```

You should see: Python 3.9 or newer, three program paths, a running `wg0` interface with a list of peers.

> ⚠ Make sure your router has a **DHCP reservation** for the Pi's address (`192.168.1.10` in the examples). The router forwards the WireGuard port to that address — if the Pi got a different one after a reboot, the VPN would stop working for everyone.

> **smbclient** is only needed for backups to a NAS. Nothing else has to be installed — the panel draws QR codes itself.

## Step 2 — Take a snapshot of the current state

**Where: Pi**

```bash
sudo mkdir -p /root/wg-panel-before
sudo cp /etc/wireguard/wg0.conf /root/wg-panel-before/
sudo iptables-save | sudo tee /root/wg-panel-before/iptables.rules >/dev/null
sudo ufw status verbose | sudo tee /root/wg-panel-before/ufw.txt >/dev/null
sudo ls -l /root/wg-panel-before/
```

You should see three files: `wg0.conf`, `iptables.rules`, `ufw.txt`.

> **Why:** this is your way back. The `--restore` option of the uninstaller (Part VI) brings back exactly this state.

> The installer makes this snapshot by itself if `/root/wg-panel-before` doesn't exist yet. A manual snapshot before installing doesn't hurt — the installer won't overwrite it.
>
> **About the name:** `wg-panel-before` is Polish for "before wg-panel". The uninstaller looks for exactly this directory, so keep the name.

## Step 3 — Store the server's private key outside the Pi

**Where: Pi, then your password manager**

```bash
sudo cat /etc/wireguard/server_private.key
```

Copy the output into your password manager (Bitwarden, KeePassXC, 1Password…) as an entry called "WireGuard — server private key".

> ⚠ The key is in a **full backup** of the panel (a backup with a password — Step 20), but not in a basic one. Keep it in your password manager too — if the SD card died and there were neither a full backup nor the key, every VPN user would need a new file.

## Step 4 — Get the panel onto the Pi

**Where: Pi**

```bash
sudo apt install -y git
git clone https://github.com/mt3ch02/wg-panel.git ~/wg-panel
```

> Without Git: download the archive from GitHub (Code → Download ZIP, or the Releases page), copy it to the Pi with `scp` and unpack it to `~/wg-panel`.

---

# PART II — Installation

## Step 5 — Install the panel

**Where: Pi**

```bash
cd ~/wg-panel
sudo ./install.sh
```

At the end you should see a summary with the panel addresses and the words "path A (taking over the configuration)".

> **What the installer did:** created the `wg-panel` service account, copied the program to `/opt/wg-panel`, created the data directory `/var/lib/wg-panel`, started two services and the watchdog (a timer every minute — Step 18a), and added a ufw rule that lets the panel be reached only from the VPN.

> **Installer language:** English by default. For Polish messages run `sudo INSTALL_LANG=pl ./install.sh`.

> **The installation has not changed anything in the VPN yet.** `wg0.conf` is untouched, everyone stays connected.

## Step 6 — Check that the services run

**Where: Pi**

```bash
systemctl is-active wg-panel-helper wg-panel wg-panel-guard.timer
```

You should see `active` three times.

> ⚠ If a service says `failed`, stop. Run `journalctl -u wg-panel-helper -n 40` (or `-u wg-panel`) — the last lines tell you what is missing.

> **Why two services:** the panel's web server runs without administrator rights. Everything that needs root — changing `wg0.conf` and the firewall rules — is done by a separate small helper that accepts only a fixed set of commands. Taking over the web server does not give an attacker root.

## Step 7 — Open the panel from the home network

**Where: Computer, then Browser**

By default the panel is not exposed to the home network — it listens on the VPN address `10.0.0.1` and on the Pi itself. From home you reach it through an SSH tunnel:

```bash
ssh -L 8080:127.0.0.1:8080 pi@192.168.1.10
```

Leave that window open and browse to `http://localhost:8080` (over the SSH tunnel it stays plain HTTP — the traffic is encrypted by SSH anyway, and the certificate doesn't cover `localhost`).

**Or access from the whole home network**, without a tunnel — then install the panel like this:

```bash
sudo LAN_ACCESS=192.168.1.0/24 ./install.sh
```

The panel then listens on all of the Pi's addresses, and the firewall lets in only your home network. You browse to `https://192.168.1.10:8080` (the old `http://` address redirects to `https://`). From the VPN, only people you explicitly allow can open the panel (Step 12b).

> **The price:** every device on your Wi-Fi can see the login page. Traffic is encrypted (HTTPS) so nobody can sniff the password, but until you install the certificate (Step 7a) your browser will warn you. A lockout after five failed logins helps too.

> **Changing your mind later** just means running the installer again with or without `LAN_ACCESS` — your data and settings are untouched.

You should see the "First run" page.

> **Away from home:** only people you explicitly allow can open the panel through the VPN. Do that for yourself in Step 12b, otherwise you can't log in to the panel from outside.

## Step 7a — Install the panel certificate

**Where: every device you open the panel from (phone, computer)**

The panel has its own certificate authority — it is created on the Pi during installation. Until you install it, browsers warn "your connection is not private". Do this once per device:

1. Open the panel (accept the warning the first time — "Advanced → Proceed") and click **Install the panel certificate** under the login form (or go straight to `/tls`).
2. **Compare the SHA-256 fingerprint** on that page with the one the installer printed at the end (`wgctl tls` on the Pi shows the same). They must be identical — only then do you know it's your panel and not someone impersonating it on the network.
3. Click **Download certificate** and follow what the page says for your system. On an iPhone there are two places: installing the profile **and** separately enabling trust in Settings → General → About → Certificate Trust Settings.

You should see: after a refresh, a padlock with no warning.

> **Is it safe:** this authority can vouch only for the addresses of your home network, the VPN and the Pi's name (X.509 name constraints). Even if someone stole its key from the Pi, they couldn't forge a bank or any other site with it. The authority's key lives in `/etc/wireguard/wg-panel-ca/` and only root can read it.

> **The panel certificate renews itself** — 30 days before expiry and after the Pi's address changes. You do nothing on the devices. One exception: changing the home or VPN subnet in Settings creates a new authority (the old one doesn't cover it) — then install it again.

## Step 8 — Create an account

**Where: Browser**

Enter a name (e.g. `admin`) and a password of at least 10 characters. Leave "Authenticator code at login" unchecked. Save the password in your password manager, then sign in.

> **The password is the panel's only protection** against someone who got into the VPN or your home Wi-Fi — make it long and random. You can turn on the authenticator code later in Settings → Account.

## Step 9 — Enter your settings

**Where: Browser → Settings**

The installer detected your home network, the Pi's address and the interface. Check them and fill in the rest:

| Field | What to enter |
|---|---|
| **LAN subnet** | your home network, e.g. `192.168.1.0/24` — detected at install |
| VPN subnet | the tunnel network, e.g. `10.0.0.0/24` |
| Server address in the VPN | the Pi's address inside the tunnel, e.g. `10.0.0.1` |
| **Pi address in the LAN** | the Pi's fixed address, e.g. `192.168.1.10` — detected at install |
| WAN interface | the Pi's internet-facing interface: `eth0`, `end0`, `wlan0`… — detected at install |
| Listen port | the WireGuard port — `51820` by default; if your router forwards a different one, change it (on path A the import in Step 11 fills it in from `wg0.conf` anyway) |
| DNS for full mode | e.g. `9.9.9.9` — Quad9: blocks malicious domains and doesn't profile queries |
| **Client endpoint** | the public address and port at which your home is reachable, e.g. `203.0.113.10:51820` or `home.duckdns.org:51820` (on path A: the same as the `Endpoint` field in your existing client files) |

Click **Save**. Then the **Hosts** tab: the `nas` host should have your NAS's address (the installer suggests one — check it's the right device). The NAS tier uses it.

> ⚠ **The LAN subnet must match your real network.** The NAS tier ends with a "block the LAN" rule, and `LAN` is exactly the subnet from Settings. With a wrong value, a person in the NAS tier will **not** be blocked on your real network. Verify it with the simulator (Step 13).

> **Endpoint** goes only into the new files and QR codes the panel will issue from now on. It doesn't change existing clients — but if you enter a wrong address here, every new user gets a file that doesn't work.

> **A VPN subnet other than `10.0.0.0/24`?** The panel listens on `10.0.0.1` by default. Install it with the right address: `sudo BIND_HOST=10.8.0.1 ./install.sh` (Step 5).

---

# PART III — Taking over the current configuration

## Step 10 — Trial import

**Where: Pi**

```bash
cd /opt/wg-panel
sudo cat /etc/wireguard/wg0.conf | sudo -u wg-panel \
     WGPANEL_DB=/var/lib/wg-panel/panel.db \
     python3 -m panel.import_conf - --tier NAS --dry-run
```

You should see one `+` line per person in `wg0.conf` (name from the comment, address in the VPN) and a summary `to add: N`. Nothing has been saved.

> **Why through `sudo cat`:** only root can read `wg0.conf`, while the import runs as the panel's account. `sudo cat` reads the file as root and pipes it to the import — the panel account never gets access to the file itself.

## Step 11 — The real import

**Where: Pi**

The same command, without `--dry-run`:

```bash
sudo cat /etc/wireguard/wg0.conf | sudo -u wg-panel \
     WGPANEL_DB=/var/lib/wg-panel/panel.db \
     python3 -m panel.import_conf - --tier NAS
```

> **Why everyone goes to NAS:** the file doesn't say who is allowed what — that lives in the `PostUp` rules, not on the peers. The import gives everyone the narrowest level, and in Step 12 you upgrade the people who should have more.

## Step 12 — Assign access levels and names

**Where: Pi**

The installer added your account to the `wg-panel` group, so `wgctl` works without `sudo` — but only from your next login (Linux reads groups at login). Log out and in again, or in the current terminal run:

```bash
newgrp wg-panel
```

Then assign levels (substitute your own addresses and names):

```bash
wgctl list
wgctl set 10.0.0.2 --tier ADMIN --rename "Me" --device "laptop"
wgctl set 10.0.0.3 --tier NET   --rename "Marek"
wgctl list
```

You should see: `10.0.0.2` in tier ADMIN, `10.0.0.3` in NET, the rest still in NAS.

You can rename people to real names right away and add devices: `wgctl set 10.0.0.4 --rename "Ola" --device "iPhone"`. You can do the same in the panel by clicking a person.

> **The ADMIN tier alone gives access to SSH and RDP on the Pi** (ports 22 and 3389).

> **The pre-shared key (PSK) is optional.** If your `wg0.conf` didn't have one, the panel won't add one either and peers keep working unchanged. A PSK is an extra layer against quantum computers — add it to someone during a key rotation, since they get a new file then anyway.

## Step 12b — Check that you can reach the panel through the VPN

**Where: Browser → Tiers**

Through the VPN, the panel opens only for people in a tier that lists the panel's port (`8080`) under **Ports on the Pi itself**. By default that's the **ADMIN** tier (`22,3389,8080`), and you're in it since Step 12 — there's nothing to do. Just check that the ADMIN card has `8080`.

> **Why:** the panel is reachable from the home network, but through the VPN only for the tiers you choose. Everyone else gets a block on the panel port even though they're on the same VPN network. Without this, you can't log in to the panel from outside your home.

## Step 12a — Remove people who shouldn't stay

If `wg0.conf` contains people you don't want to carry over into the panel (e.g. old, unused devices):

```bash
wgctl delete 10.0.0.9 --yes
wgctl delete 10.0.0.10 --yes
wgctl list
```

Deleted people disappear from `wg0.conf` at the next apply and their addresses go back to the pool. If you'd rather leave someone on the list for now (say, until you find out who it is), use `wgctl suspend 10.0.0.9` — a suspended person has no access but can be resumed with one command.

> **Why through the panel and not by editing the file:** the audit log records who did it and when, and suspended people stay in the panel in case someone asks "why doesn't it work for me?".

> ⚠ After you apply the changes, their files stop working. Warn those people beforehand.

> **People in the NAS tier usually have your whole LAN** in `AllowedIPs` in their (old) files, while the panel computes only the NAS for new files. Existing files keep working unchanged — they cover more than needed, and the server lets them reach only the NAS anyway. You don't have to send anyone anything.

## Step 13 — Check permissions with the simulator

**Where: Pi**

Before you apply anything, check that the panel allows and blocks exactly what you want. Syntax: `wgctl check WHO TARGET PORT` (WHO — a name or a VPN address). Examples for the layout from Step 12 (substitute your addresses):

```bash
wgctl check Me        192.168.1.10 22     # ADMIN -> SSH to the Pi
wgctl check Marek     1.1.1.1       443    # NET   -> internet
wgctl check Marek     192.168.1.11 445    # NET   -> NAS (LAN blocked)
wgctl check 10.0.0.4  192.168.1.11 445    # NAS   -> NAS
wgctl check 10.0.0.4  192.168.1.11 22     # NAS   -> wrong port
wgctl check 10.0.0.4  192.168.1.20 22     # NAS   -> another LAN host
wgctl check 10.0.0.4  10.0.0.1      22     # NAS   -> SSH to the Pi
```

Expected results, in order: ACCEPT, ACCEPT, DROP, ACCEPT, DROP, DROP, DROP.

> **What the simulator does:** it walks the rules exactly the way the kernel will and tells you which rule decided. Any other result means something in Steps 9–12 is off (most often the LAN subnet in Settings) — fix it now, before anything changes live.

## Step 14 — Look at what will change

**Where: Browser → Peers → the "The panel has changes…" card → Details → "Full diff"**

Check in the preview that:

- the new `wg0.conf` has as many `[Peer]` sections as people remain, with the same public keys;
- the rules contain `-d <NAS address>/32` with ports `80,443,445` for people in the NAS tier;
- the `WG-USERS-HOST` chain has an ACCEPT rule for people in the ADMIN tier on ports `22,3389`.

## Step 15 — Apply the changes with the safety fuse

**Where: Browser, plus a second terminal on the Pi**

The first time, it's worth checking everything **before** confirming, so first switch the panel to manual confirmation: **Settings → Advanced → Confirming changes: manually → Save**. (Day to day, leave it on "automatically" — the panel checks by itself that you still have a connection and confirms in a second.)

Click **Apply changes**. From that moment you have 90 seconds: a yellow bar with a countdown hangs at the top of every page (it turns red for the last 15 seconds).

In the second terminal on the Pi, check:

```bash
sudo wg show wg0 | grep -c peer
sudo iptables -L WG-USERS -n | head -5
```

You should see the number of people (as in `wgctl list`) and the beginning of the panel's chain. If everything checks out, go back to the browser and click **"Confirm, it works"** on the bar — the bar disappears at once and the panel stays on the page you were on. Now you can switch back to automatic confirmation in Settings.

> ⚠ If anything looks wrong — do nothing, or click **"Roll back now"** on the bar. After 90 seconds the panel restores the previous configuration by itself and says so on the bar. Not confirming is safe; confirming a broken configuration is not.

## Step 16 — Test from real devices

**Where: Phone**

Turn off Wi-Fi on your phone (so you connect from outside, over mobile data) and:

1. Connect to the VPN as the administrator and open `https://10.0.0.1:8080` — the panel should open.
2. If you can, ask one person in the NAS tier to open the NAS — it should work.

> **Why from mobile data:** from your home Wi-Fi some routers can't reach themselves via their public address. A test from outside is the only test that shows how the VPN really works.

## Step 17 — Reboot the Pi

**Where: Pi**

The rules from the old `wg0.conf` are still in memory — the panel doesn't see or remove them because they were added before it. They do no harm, since the panel's rules are checked first, but the cleanest way to get rid of them is a reboot:

```bash
sudo reboot
```

After a minute, reconnect and check:

```bash
systemctl is-active wg-quick@wg0 wg-panel-helper wg-panel
wgctl status
sudo iptables -S FORWARD | head -4
```

You should see: `active` three times; no unapplied changes in `wgctl status`; a jump to `WG-USERS` in `FORWARD` and none of the old rules with `10.0.0.x` addresses.

> **Why it's safe:** after the reboot `wg-quick` already reads the file generated by the panel. The ufw and NAT rules from `/etc/ufw/before.rules` come back by themselves because ufw stores them persistently.

**Next: Step 18** (Part IV). Skip paths B and C — they are for a clean system.

---
# PATH B — clean system, brand-new VPN

You set up WireGuard from scratch. At the end everyone gets new files.

## Step B1 — The system

**Where: Computer, then Pi**

1. Flash **Raspberry Pi OS Lite (64-bit)** with Raspberry Pi Imager. In the Imager's settings enable SSH and set a user with a password.
2. Insert the card and boot the Pi connected to the router by cable.
3. On the router set a **DHCP reservation**: the Pi always gets `192.168.1.10` (or your address).
4. Log in and update:

```bash
ssh pi@192.168.1.10
sudo apt update && sudo apt full-upgrade -y
sudo reboot
```

## Step B2 — Packages

**Where: Pi**

```bash
sudo apt install -y wireguard iptables ufw smbclient
command -v wg wg-quick iptables ip6tables ufw smbclient
```

You should see six paths.

## Step B3 — Server key

**Where: Pi, then your password manager**

```bash
sudo mkdir -p /etc/wireguard
sudo chmod 700 /etc/wireguard
wg genkey | sudo tee /etc/wireguard/server_private.key >/dev/null
sudo chmod 600 /etc/wireguard/server_private.key
sudo cat /etc/wireguard/server_private.key
```

Copy the output into your password manager as "WireGuard — server private key".

> ⚠ This key is your VPN's identity. Lose it and everyone needs new files. If it leaks, someone can impersonate your server. It is only in a full backup of the panel (with a backup password), not in a basic one — so keep a second copy in your password manager.

## Step B4 — Firewall

**Where: Pi**

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow from 192.168.1.0/24 to any port 22 proto tcp
sudo ufw allow 51820/udp
sudo ufw enable
sudo ufw status verbose
```

You should see: `deny (incoming)`, a rule for port 22 from your LAN and a rule for `51820/udp`.

> **Why only this:** the only port open to the internet is the WireGuard port, and SSH works only from the home network. VPN access — to the NAS, to SSH and RDP on the Pi — is set up by the panel in its own chains. Packet forwarding stays blocked in ufw: if the panel's chains ever disappear, the VPN stops passing traffic instead of passing everything.

> If you use a remote desktop (xRDP) on the Pi from the home network, add: `sudo ufw allow from 192.168.1.0/24 to any port 3389 proto tcp`.

## Step B5 — Router

**Where: your router's admin panel**

1. Port forwarding: **UDP 51820 → 192.168.1.10:51820**.
2. Write down the public address at which your home is reachable: a fixed IP from your ISP or a DDNS name (e.g. `home.duckdns.org`). This will be the **Endpoint** in Step B9.

> If your ISP changes your public IP, set up DDNS — otherwise the VPN breaks for everyone at once when the address changes.

## Step B6 — Get and install the panel

**Where: Pi**

Do **Step 4** and **Step 5** from path A. The installer should say "path B/C (new system)".

## Step B7 — Check the services

**Where: Pi**

```bash
systemctl is-active wg-panel-helper wg-panel
journalctl -u wg-panel -n 5 --no-pager
```

You should see `active` twice, and in the log a line saying that `10.0.0.1` doesn't exist yet (wg0 is down) and the panel is waiting. That is correct — there is no tunnel yet; the panel brings it up in Step B11.

## Step B8 — Open the panel and create an account

**Where: Computer, then Browser**

Do **Step 7** and **Step 8** from path A.

## Step B9 — Settings

**Where: Browser → Settings**

As in **Step 9** from path A. The most important fields are **Client endpoint** — the address from Step B5 with the port, e.g. `home.duckdns.org:51820` — and **Listen port**: the same port your router forwards (`51820` by default). Also check **Pi address in the LAN** and the **LAN subnet**.

## Step B10 — Add people

**Where: Pi (or Browser → Peers → Add peer)**

An example layout (substitute your own names, levels and addresses):

```bash
wgctl add "Me"    --tier ADMIN --ip 10.0.0.2 --device laptop
wgctl add "Marek" --tier NET   --ip 10.0.0.3
wgctl add "Ola"   --tier NAS   --ip 10.0.0.4 --device iPhone
wgctl list
```

`wgctl` works without `sudo` from your next login (the installer added you to the `wg-panel` group) — or right away after `newgrp wg-panel` in the current terminal.

Check with the simulator as in **Step 13**.

## Step B11 — First apply — bringing the tunnel up

**Where: Browser → Peers → Apply changes**

Switch **Settings → Advanced → Confirming changes** to **manually** (as in Step 15), then click **Apply changes**. The panel writes `wg0.conf`, enables packet forwarding, enables the tunnel at boot and brings up `wg0`. In a second terminal:

```bash
ip -brief addr show wg0
sudo wg show wg0 | grep -c peer
```

You should see `wg0` with the address `10.0.0.1/24` and the number of people from `wgctl list`. Go back to the browser, click **Confirm, it works** on the top bar, and then switch confirmation back to **automatically**.

> Without confirmation the panel rolls the change back after 90 seconds — on a clean system that means stopping the tunnel. Nothing breaks; you simply apply again.

## Step B12 — Hand out the files

**Where: Browser → Peers → each person**

For each person: the **QR code** if they're next to you, or **Copy file** and send it through an encrypted messenger. When they confirm it works — **Issued — delete the key**. Details in Part V, "Sending the configuration file".

Start with yourself — import your own file on your phone.

## Step B13 — Test and reboot

**Where: Phone, then Pi**

Do **Step 16** from path A (test from mobile data), then check that everything comes up on its own:

```bash
sudo reboot
```

After a minute connect with your phone through the VPN and open `https://10.0.0.1:8080` — it should work without your help.

**Next: Steps 18–21** (alerts, backups, check).

---

# PATH C — new system from a panel backup

You rebuild everything on a clean card with one command. What exactly comes back depends on **which backup you have** on the NAS:

| Backup | Where it comes from | What comes back |
|---|---|---|
| **full** (a `….json.enc` file, "full" in the backup list) | every backup made with a **backup password** set | everything: peers, settings, accounts, NAS and ntfy passwords, history, the **server key** and the **HTTPS certificate**. Nobody changes anything on their side |
| **basic** (no password, or from an older panel version) | a backup without a password, or from before full backups existed | peers, tiers, rules, settings. **It has no server key** — the installer asks for one |

You need: the **backup password** from your password manager and the details of the `wg-backup` account on the NAS. With a basic backup, also the **server key** from your password manager — without it the restore still works, but everyone gets a new file (Step C5).

> **The server key** is your VPN's identity — it is written into every person's file. With a different server key the existing files won't work: the phone "knocks" and the Pi doesn't answer.

## Step C1 — System

**Where: Computer, Pi**

1. Do **Step B1** (Raspberry Pi OS Lite). Give the Pi **the same hostname** as before — then the HTTPS certificate from the backup matches and phones don't need a new `ca.crt`.
2. On the router the Pi must get **the same address** as before (DHCP reservation) — the WireGuard port forward points at it.
3. Firewall: **Step B4**.

You don't need to install WireGuard or iptables — the installer adds them.

## Step C2 — Restore

**Where: Pi**

```bash
sudo apt install -y git
git clone https://github.com/mt3ch02/wg-panel.git ~/wg-panel
cd ~/wg-panel
sudo LAN_ACCESS=192.168.1.0/24 ./install.sh --restore
```

Use the same `LAN_ACCESS` as in the previous install (or leave it out if the panel was reachable only through the VPN).

The installer asks, in order:

| Question | Enter |
|---|---|
| NAS address | e.g. `192.168.1.11` |
| Share | `backup` (Enter) |
| Directory on the share | the directory with the backups (Enter = `wg-panel`) |
| NAS user / password | `wg-backup` / the password from your password manager (not shown) |
| Which one to restore | Enter = the newest (it shows the list) |
| Backup password | from your password manager |

You should see: "a full backup, N people. The server key and certificate are in place", then the usual installation, "Tunnel is up" and at the end "Restored from a full backup: sign in with your existing account".

When something doesn't match, the installer says what to fix. These NAS messages are currently in Polish — the table gives the meaning:

| Message (Polish) | Meaning and what to do |
|---|---|
| `NAS odrzucił login albo hasło` | the NAS rejected the login or password: a typo in the password or the account name; in the NAS settings check that the account isn't disabled and that the Pi's address isn't on a block list (e.g. automatic IP blocking) |
| `na udziale backup nie ma katalogu … - są tam: …` | the directory doesn't exist on the share: run it again and enter one of the listed directories |
| `na NAS nie ma udziału …` | wrong share name — it must match the shared folder on the NAS |
| `NAS nie odpowiada pod adresem …` | wrong address, or SMB is turned off on the NAS |
| `couldn't open the backup` | wrong backup password |

> **A backup without the server key** (basic, or from an older version): the installer says so and asks: **1) paste the old key** (from your password manager — everyone keeps connecting with their old files) or **2) generate a new one** (peers and settings come back, but everyone needs a new file — Step C5).

> **From the panel instead of the installer:** when the panel is already installed — **Backups**, at the bottom in **Backup settings** enter the NAS details and **Save settings**, then in the **Backup list** click **Refresh list from NAS**. Select the backup (click the row), **Restore selected…** — in the dialog tick **Sign in with the old password**, enter the backup password and click **Restore backup**. After the restore the panel tells you whether the server key came back and whether people's existing files will work.

## Step C3 — Check

**Where: Browser, Phone**

1. Open `https://<pi-address>:8080` — no certificate warning (if the hostname matches); sign in with your **existing** account.
2. **Settings → Server public key** must match `PublicKey` in the `[Peer]` section of the file on your phone.
3. Phone on mobile data (Wi-Fi off), turn the tunnel on. In the peer list you should get a handshake "X s ago".

If the tunnel doesn't connect — **Part VI, "The VPN doesn't connect after a restore"**.

## Step C4 — Reboot

**Where: Pi**

```bash
sudo reboot
```

After a minute the VPN should work without you touching anything, and **Integrity → Check now** should report nothing.

## Step C5 — Only with a new key: new files for everyone

If you generated a new server key in Step C2:

1. **Save it in your password manager right away** (`sudo cat /etc/wireguard/server_private.key`) and set a **backup password** — from now on backups are full and this step never repeats.
2. For every person: **Key rotation**, then **Apply changes** once and **Confirm, it works**.
3. Hand out the files: everyone **deletes the old tunnel** from the app and imports the new one (QR or **Copy file** / one-time link) — on one device.

**Next: Step 21** (check). Alerts and the backup schedule came back with the backup — check in Alerts that the test alert arrives.

---

# PART IV — Alerts and backups (all paths)

## Step 18 — Phone notifications

**Where: Phone, then Browser → Alerts**

1. Install the **ntfy** app on your phone (no account needed).
2. Make up a long random topic name, e.g. `wg-home-k7f2q9x4m1` — at least 12 characters, with digits.
3. In the app: **+** → enter that name → **Subscribe**.
4. In the panel, **Alerts** tab → Notification settings: channel `ntfy`, address `https://ntfy.sh/wg-home-k7f2q9x4m1`, **Save**.
5. Click **Send test alert**.

You should see on your phone: "wg-panel: test powiadomień" (the notification text is currently in Polish).

> **Why a random name:** anyone who knows a topic's name on the public ntfy.sh can read it — and alerts contain people's names and their public IP addresses. If the name is short or easy to guess, the panel shows a red warning above the settings.

> **Optional — access token:** if you create an account on ntfy.sh and reserve the topic (or run your own ntfy server), enter the `tk_...` token in the **ntfy token** field. Then the topic is protected regardless of its name.

> **Priorities:** the most serious events — the same key from a new address, the same file on two devices at once, configuration changed outside the panel, the `wg0` interface down — arrive with the highest priority (your phone rings louder). The rest arrive normally, including "working again" and "stopped".

> **The most important alert is "address change".** It fires when someone connects with somebody's key from a new place — the first sign that a configuration file leaked.

> **The "tunnel on two devices" alert.** It fires when the same configuration file is switched on on two devices at once — e.g. a computer at home was left with the VPN on while the same person connects with the same file from work. WireGuard answers whichever device spoke last, so the two kick each other out and the connection keeps dropping (e.g. RDP disconnects when you click). The panel usually notices within a few minutes. The alert lists both addresses, the person gets a red **2 devices** badge in the peer list, and their page explains what to do. When the second device hasn't been seen for 10 minutes, a "stopped hopping" entry appears in Alerts. This alert also catches a copied file used at the same time as its owner's. You can turn it off in Alerts → Notification settings → **Tunnel on two devices at once** (on by default).

**Optionally — also by e-mail:** Alerts → **E-mail (optional)**: check "Also send alerts by e-mail", enter the SMTP server (e.g. `smtp.gmail.com`, port 587, STARTTLS), login, password, recipient. Use a **separate account and an app password** — the password is stored on the Pi. E-mail works alongside ntfy, not instead of it.

## Step 18a — The watchdog: a notification when something goes down

**Where: Pi, then Phone**

The watchdog is a systemd timer that every minute — **independently of the panel** — checks: the panel, the helper, the `wg0` interface, the Pi's temperature, free space on the card, under-voltage and the HTTPS certificate's validity. The notification goes to the same channel as alerts (Step 18), only after two failed checks in a row, once; after the fix — one "working again".

Check it by hand:

```bash
wgctl guard
```

You should see the line saying everything works (the output is currently in Polish: `straznik: wszystko dziala`).

A live test (the VPN keeps running normally meanwhile):

```bash
sudo systemctl stop wg-panel
```

After 2–3 minutes a **"panel: problem"** notification should arrive on your phone. Then:

```bash
sudo systemctl start wg-panel
```

After a minute or two: **"panel: znowu działa (przerwa około N min)"** — i.e. "working again".

> **Access deadlines work without the panel too.** The same timer cuts people off after their deadline and opens access windows even when the panel is down (the same by hand: `wgctl expire`). The change goes onto the interface surgically — your unapplied changes in the panel stay unapplied.

> **What the watchdog cannot detect:** the whole Pi going down (power loss, a dead card) — because it goes down with it. For that you need monitoring from another machine (Uptime Kuma — Part IV-B below).

## Step 19 — Prepare a place for backups on the NAS

**Where: NAS**

The steps below use Synology DSM as an example; on another NAS do the equivalent.

1. **Control Panel → Shared Folder → Create**: name `backup` (or use an existing one).
2. **Control Panel → User & Group → Create**: name `wg-backup`, save a long password in your password manager.
3. In permissions give that user **read and write to the `backup` folder only**, nothing else.
4. Open the `backup` folder and create a directory `wg-panel` in it.
5. **Control Panel → File Services → SMB** must be enabled.

> **Why a separate user:** its password is stored in the panel's database. If it leaked, it would give access only to the panel's backups, not to your whole disk.

## Step 20 — Turn on backups in the panel

**Where: Pi, then Browser → Backups**

The tool that uploads to the NAS (`smbclient`) is installed by the installer. If you have a panel from an older version, install it again first (Step 5 — your data stays), because the older version neither installed it nor could run it.

The **Backups** page has three parts: at the top the **status** (what kind of backup is made, when automatically, how the last one went) and **Back up now – where?**, in the middle the **Backup list**, at the bottom the **Backup settings**. At the bottom, in **Backup settings**:

| Field | Enter |
|---|---|
| Back up automatically, every day | checked |
| At time | `03:30` |
| Where to | NAS only |
| Backup password | **at least 12 characters, random — save it in your password manager** |
| NAS: Address | e.g. `192.168.1.11` |
| Share | `backup` |
| Directory on the share | `wg-panel` |
| Username / Password | `wg-backup` / the password from Step 19 |
| On the Pi: Backup folder on the Pi | leave empty (default) |
| Keep on the Pi (last) | `14` |

**Save settings.** At the top of the page, under **Back up now – where?**, there are three buttons: **To the NAS**, **To the Pi** and **To this computer**. Each makes one backup exactly there and nowhere else; the **Where to** field only applies to automatic backups. Click **To the NAS**.

You should see: three green dots at the top — **Backup type: FULL, encrypted**, **Automatic: daily at 03:30 → NAS only** and **Last backup: … → NAS** with "checksum matches". In the **Backup list** a new row: Where "NAS", Type "full", State "checked". On the NAS, in the `backup/wg-panel` share, two files appear: the backup and its `.sha256`.

**Where a backup lands** is up to you; the panel makes no extra copies behind your back:

| Choice | Where the backup lives |
|---|---|
| NAS only | only on the NAS — the file on the Pi exists just long enough to be sent and is removed right after (also when sending fails) |
| Pi only | in the backup folder on the Pi (at most the last 14) — doesn't protect against a card failure |
| NAS and Pi (two copies) | in both places — a restore takes the file from the Pi and doesn't have to wake the NAS |
| To this computer (button only) | the browser downloads the file; the panel doesn't keep it and it isn't on the list |

If the backup fails, a red bar shows the reason:

| Message | What to do |
|---|---|
| missing NAS address or share | fill in **Address** and **Share** in Backup settings, **Save settings** |
| missing NAS user or password | fill in **Username** and **Password** in Backup settings, **Save settings** |
| missing smbclient | install the panel again (Step 5) or `sudo apt install -y smbclient` |
| the NAS rejected the login or password (`NT_STATUS_LOGON_FAILURE`) | wrong password or user name `wg-backup`; in the NAS settings check that the account isn't disabled and the Pi's address isn't blocked |
| the directory … doesn't exist on the share — there are: … | enter one of the listed directories in **Directory on the share**, or create the directory on the NAS |
| `NT_STATUS_BAD_NETWORK_NAME` | wrong share name — must be exactly the shared folder's name (`backup`) |
| `NT_STATUS_OBJECT_NAME_NOT_FOUND` or `…PATH_NOT_FOUND` | the share has no `wg-panel` directory — create it |
| `NT_STATUS_ACCESS_DENIED` | `wg-backup` lacks write permission on `backup` — fix the folder permissions on the NAS |
| `NT_STATUS_IO_TIMEOUT` or `NT_STATUS_CONNECTION_REFUSED` | SMB service is off on the NAS, or the address is wrong |

> **The backup password makes a backup full.** With a password the backup is encrypted and contains everything: peers, settings, accounts, the NAS and ntfy passwords, history, the **server key** and the **HTTPS certificate** — after a failure you rebuild the whole Pi from it with one command (path C), and nobody changes anything on their side. Without a password the backup is basic and unencrypted (no keys, no passwords) — the panel shows that at the top of the Backups page with a red dot "Backup type: BASIC, unencrypted".

> ⚠ A full backup contains your VPN's identity. So use a **long, random** password kept only in your password manager (the panel warns below 12 characters), and a `wg-backup` account with access to the `backup` share only. A lost backup password = an unreadable backup.

> **"Checked" means:** the panel downloaded the backup back from the NAS, computed a checksum, compared it with the stored one and opened the file with the password. Every backup has a `.sha256` next to it, so you can check it without the panel too.

## Step 21 — Check that everything works

**Where: Pi**

```bash
wgctl status
wgctl integrity
wgctl backup --verify
wgctl guard
```

You should see: no unapplied changes; state consistent with what the panel applied; backups marked `OK`; the watchdog reporting that everything works.

Also do the **quick backup test** once (Part V).

**Deployment finished.**

---

# PART IV-B — Optional: reverse proxy and monitoring from outside

This part is optional — for people who have a separate server at home with a reverse proxy (here: Caddy) and want to publish the panel under a nice name. You install nothing on the Pi itself: the panel keeps its own HTTPS and Caddy connects to it over HTTPS.

## Step P1 — The authenticator code first

**Where: Browser → Settings → Account**

Turn on the authenticator code (TOTP) if it isn't on yet.

> **Why before the proxy:** the Pi's firewall lets only people you gave access to (Step 12b) reach the panel port. Through a proxy, anyone who reaches Caddy sees the login page — including people on the VPN whom the panel itself would not show it. A password plus a code from the phone is then the main protection.

## Step P2 — The panel certificate on the proxy server

**Where: Browser, then the proxy server (e.g. the Caddy container)**

1. Settings → HTTPS → expand **Reverse proxy on another server** → download `wg-panel-ca.crt`.
2. Copy the file into the Caddy container, e.g. to `/etc/caddy/wg-panel-ca.crt`.

> **Why:** Caddy connects to the Pi over HTTPS and checks it is talking to the real panel. That way the proxy → Pi leg is encrypted too, and the password doesn't cross your home network in plain text.

## Step P3 — Caddyfile

**Where: proxy server**

Copy the ready snippet from the panel (Settings → HTTPS → Reverse proxy — it already has the Pi's address and port) and paste it into the `*.home.example.com` block in your `Caddyfile`, before the final `handle { abort }`:

```
@wgpanel host panel.home.example.com
handle @wgpanel {
    reverse_proxy https://192.168.1.10:8080 {
        transport http {
            tls_trusted_ca_certs /etc/caddy/wg-panel-ca.crt
        }
    }
}
```

Reload Caddy (`caddy reload` or restart the container).

## Step P4 — Name in local DNS and the firewall

**Where: DNS server / router, then Pi**

1. Local DNS (e.g. Pi-hole → Local DNS Records, or your router): `panel.home.example.com` → the proxy server's address (as for other services behind Caddy).
2. The Pi's firewall must let the proxy server in on the panel port. If you installed with `LAN_ACCESS=192.168.1.0/24` it already does. If not, reinstall with just the proxy's address:

```bash
cd ~/wg-panel && sudo LAN_ACCESS=192.168.1.20/32 ./install.sh
```

## Step P5 — Enter the proxy address in the panel

**Where: Browser → Settings → HTTPS → Reverse proxy on another server**

Proxy server address: its LAN address (e.g. `192.168.1.20`). In **Public panel address** enter `https://panel.home.example.com` — it goes into links in notifications. **Save.**

> **Why you have to enter the address:** the panel trusts the `X-Forwarded-For` / `X-Forwarded-Proto` headers only from that address. From other devices they are ignored — nobody can pretend to be another address to get around the lockout after failed logins.

## Step P6 — Test

**Where: Phone (through the VPN)**

1. Open `https://panel.home.example.com` — no certificate warning (that's Caddy's certificate), log in.
2. Audit log → the latest "login" entry — its details should show your device's address, **not** the proxy's address. If it shows the proxy's address, check Step P5.

> ⚠ Direct access stays: `https://10.0.0.1:8080` through the VPN keeps working. It is your emergency entrance when the proxy server is down.

## Step P7 — Uptime Kuma: when the whole Pi goes down

**Where: Uptime Kuma on a separate machine**

The watchdog on the Pi can't tell you the whole Pi went down. Uptime Kuma on another machine can:

1. **Add New Monitor** → type **HTTP(s)** → URL `https://192.168.1.10:8080/login` (the Pi's home-network address), interval 60 s. Check **Ignore TLS/SSL error** (Uptime Kuma doesn't know the panel's authority) or use a **Ping** monitor on the Pi's address.
2. **Notifications** → **ntfy** → the same topic as in Step 18 (and the token, if you use one).

You should see: a green monitor. Test: unplug the Pi — within a few minutes a notification arrives from Uptime Kuma.

---

# PART V — Day-to-day operation

## A new person — step by step

1. **Browser → Peers → Add peer.** Name, device, level (NET or NAS), access mode.
2. **Apply changes** — the panel checks the connection itself and confirms (green "Changes applied.").
3. Open the new person's page. On the **What goes through the VPN** card check that the scope is right.
4. Hand over the file: **QR code** if the person is next to you, or **Copy file** and send it through an encrypted messenger (details in "Sending the configuration file").
5. When they confirm it works — click **Issued — delete the key**.

> **Deleting the key** means that even a leaked database can't be used to impersonate that person. The panel also remembers what scope they were given, so it can later warn you when their file gets out of date.

> **One peer = one device.** Someone who needs the VPN on a laptop and at work gets two peers, e.g. "Anna laptop" and "Anna work" — each with its own file. One file can't be used on two devices at once: they kick each other out, and the panel will send a "tunnel on two devices" alert. Tell the person you hand a file to — the file is like a house key, you don't copy it onto other devices.

## Time-limited access

In the person's form, the **Access mode** field:

| Mode | Example | What happens |
|---|---|---|
| no expiry | household members | nothing |
| time window (from–to) | a crew from 1 to 31 October | access switches on and off by itself |
| N days from first connection | a guest, 7 days | the clock starts at the first connection, not the day you issue it |

Three days before the end an orange badge "ends in N days" shows next to the person.

## Changing a person's details

**Browser → Peers → click a person.** At the top of the page there is a form:

| Field | What it changes |
|---|---|
| Name, Device, Note | only the description in the panel — the person notices nothing |
| Tier | access level: ADMIN, NET or NAS |
| Access mode, From, To, Days | how long they have access |

Click **Save changes** on the bar at the bottom of the page (it appears once you change something), then **Apply changes** in the peer list. Names and notes take effect at once; tier and access-window changes — after applying.

From a terminal: `wgctl set Marek --rename "Marek K." --tier NET --device "iPad"`.

## Changing permissions

There are two ways, depending on whether the change concerns one person or a whole group.

**One person needs more or less** — a custom rule on their page, section **Custom rules**. Example: Marek (NAS) should also get the printer: Action `ACCEPT`, Target `printer`, Proto `tcp`, Ports `631`, **Add rule**. A custom rule is checked before the tier's rules, so it works despite the LAN block. You can't edit a rule — delete it (**delete** in the row) and add a new one.

**A whole group needs it differently** — **Tiers → the tier's form**. Changing the NAS tier changes permissions for everyone in it at once. This is also where the ports for access to the Pi itself (SSH, RDP) live — only people in a tier that has them get them, so SSH for one extra person means moving them to ADMIN or creating a new tier.

After every change, check it in the **Simulator**, and only then **Apply changes**.

## What goes through the VPN — everything or only selected addresses

**Browser → Peers → click a person → the "What goes through the VPN" card.**

This is decided by the `AllowedIPs` line in the person's file. There are four modes:

| Mode | AllowedIPs | When |
|---|---|---|
| **All traffic through the VPN** | `0.0.0.0/0` | e.g. the admin and people who should route all internet through your line |
| **Only what they have access to** | computed from the rules, e.g. `192.168.1.11/32` | e.g. the NAS tier — the rest goes through their own internet |
| **The whole home network** | `10.0.0.0/24, 192.168.1.0/24` | the whole LAN and the VPN — the widest, for trusted people |
| **Custom list** | whatever you enter, e.g. `nas, printer` | when you want to set it by hand |

By default a person inherits the mode from their tier ("as in the tier"). You change a whole tier's mode in the **Tiers** tab, field "What goes through the VPN". On a person's card you can override it for that person only.

> **These are not permissions.** `AllowedIPs` only says what the person's phone will send into the tunnel. What they may reach is enforced by the server. Even if someone adds your whole LAN to their app by hand, the rules on the Pi still let them reach the NAS only.

### How to give someone access to something new

Example: Marek (NAS) should get the printer.

1. **Hosts** — if the printer isn't in the catalog, add `printer = 192.168.1.70`.
2. Marek's page → **Custom rules**: `ACCEPT`, target `printer`, `tcp`, port `631` → **Add rule**.
3. **Apply changes** — the panel confirms by itself when you still have a connection. From now on the server lets him in.
4. In "Only what they have access to" mode the printer appears **by itself** in his `AllowedIPs`. A red frame "The file on this person's device is out of date" appears on the card.
5. Pass the change on in one of two ways:
   - **Copy the AllowedIPs line** and send it to him. In the WireGuard app he opens the tunnel → **Edit** → replaces the `AllowedIPs` line → **Save**. The keys don't change.
   - Or send him a whole new file (next section).
6. Click **Mark as sent** — the frame disappears.

> **Why the frame is red and sometimes grey.** Red: the person's file lacks something they now have access to — without a new file they won't reach it. Grey: the file covers more than necessary (e.g. the whole LAN) — it works, sending is optional.

From a terminal: `wgctl allowed Marek` prints the ready line to send. `wgctl set Marek --tunnel custom --tunnel-list nas,printer` sets a custom list and `wgctl set Marek --tunnel tier` returns to the tier's setting.

## Sending the configuration file

On the person's page, the **Client configuration** section:

| Method | How | When |
|---|---|---|
| **Copy file** | copies the whole text; you send it in a message, and in the WireGuard app the person picks **Add tunnel → Create from scratch** and pastes it | the person is far away |
| **QR code** | the person scans the screen with the WireGuard app | the person is next to you |
| **Download .conf** | a file to import into the desktop app | a computer |
| **One-time link** | works once, expires after a day, **opens only through the VPN** | replacing the file for someone already connected, e.g. after a key rotation |

After they confirm it works: **Issued — delete the key**. The panel remembers what scope the person was given.

> ⚠ For people imported from an existing `wg0.conf` (path A) the panel has no private keys — the `wg0.conf` file itself doesn't contain them. "Copy file", the QR code and "Download .conf" work for them only after a **Key rotation**. You don't need a rotation to add something new — replacing the `AllowedIPs` line in their app is enough.

> ⚠ The configuration file is the key to your network. Send it through an encrypted messenger (Signal, WhatsApp), not e-mail, and ask them to delete the message after importing.

## Undoing the last change

**Browser → Settings → Emergency operations → Roll back to the last backup**, or `wgctl rollback`. It restores the `wg0.conf` files and rules from before the last apply. The panel's database stays, so the change is still waiting in the list — fix it and apply again.

## Restoring from a backup

**Browser → Backups → Backup list.** Every backup is on the list once — with its date, location (NAS / Pi / NAS + Pi), type (full / encrypted / basic / failed) and checksum state. Backups that exist only on the NAS (e.g. on a new system) join the list after you click **Refresh list from NAS** — the only button that asks the NAS for its list; the panel never does it in the background (the NAS may be asleep).

**What the labels in the backup list mean** (the same is in the panel under the list, in "What do the labels mean?"):

| Type | Meaning |
|---|---|
| **full** | everything, including the server key — **after a failure restore the newest one of these** |
| **before restore** | saved automatically right before a restore — to undo a mistake |
| **before update** | saved by the installer right before a panel update |
| **found** | it was on the NAS or the Pi but the panel didn't make it (e.g. from before a reinstall) |
| **basic** | no server key, passwords or history — made without a backup password |
| **full, no key** | full, but without the server key (the helper didn't answer at the time) — rare |

| State | Meaning |
|---|---|
| **checked** | the file has the same SHA-256 checksum as when it was saved |
| not checked | not checked yet — select it and click **Check selected** |
| **damaged** | the file changed or is corrupted — the panel won't restore it |
| **failed** | the backup wasn't saved (hover for the reason) |
| **file missing** | the file is gone (deleted on the NAS or cleaned up on the Pi) — delete the entry |

"NAS + Pi" in the **Where** column means the file is in both places — a restore then takes it from the Pi.

> The backup type follows from the **backup password**: with a password every new backup is full and encrypted, without one — basic. There is no separate switch: a full backup without encryption would put the server key on the NAS in plain text.

Select one backup — clicking a row selects it, clicking again deselects — and click **Restore selected…**. A dialog opens with the backup's date and whether it's a full backup (the server key comes back — people's old files keep working) or one without the key. It has a **Backup password** field (empty = the password from settings) and **Sign in with the old password**. Click **Restore backup**.

What the panel does after the click:

1. takes the file — from the Pi if it's there ("NAS + Pi" or "Pi"), and if it's only on the NAS, it downloads it itself;
2. checks the checksum — a swapped or corrupted backup won't be restored;
3. opens it with the password — a wrong password ends here and nothing changes;
4. saves the **current state** as a separate backup (labelled "before restore" in the list);
5. only then replaces the people, tiers, rules and host catalog.

Then **Apply changes**. If the restore turns out to be a mistake — select the backup labelled "before restore" and restore it.

From a terminal: `wgctl restore` shows the list, `wgctl restore 12` restores backup no. 12.

> **"Sign in with the old password"** — tick it only on a new system: your previous panel account (login, password, authenticator code) comes back and the panel signs you out. On a working panel leave it unticked so you don't change your own login password.

## Testing the backup

A backup you have never tried to restore is just hope. There are two levels.

### Quick test (5 minutes, no risk)

**Where: Browser → Backups, then Pi**

1. Under **Back up now** click **To the Pi**, then **To the NAS** — two new rows should appear in the Backup list (Where "Pi" and "NAS").
2. Select both and click **Check selected** — the panel downloads the files from where they live (the NAS too) and compares SHA-256 checksums. Every row should have the state **checked**.
3. On the Pi:

```bash
wgctl backup --verify
wgctl restore --nas
```

You should see: backups `OK` and a list of backups on the NAS (the same list you'll use after a card failure).

### Full test (about an hour, on a spare SD card)

It checks what really matters: whether you can rebuild the Pi from scratch from the backup on your NAS. You do it on a **spare card** — your current card stays untouched.

1. Turn the Pi off, put in a spare card with clean Raspberry Pi OS (or use a second Pi) and go through path C: steps **C1–C2** (`install.sh --restore`). Along the way you check that the backup password in your password manager is current.
2. **Right after the restore, before doing anything else:**
   - Alerts → channel **off** — otherwise the test Pi would send notifications to your ntfy topic.
   - Backups → Backup settings → untick **Back up automatically, every day** and **Save settings** — the full backup also restored the NAS password, so the test Pi would write backups to the same folder and might delete older ones during rotation.
3. Check that everything came back:

```bash
wgctl list
wgctl diff
```

You should see: the same people, tiers and names as on the real Pi (compare with `wgctl list` there), and `wgctl diff` showing the generated configuration with no errors.

4. You can check from your phone that the VPN works with your old file (Step C3) — the best proof that the backup is full. Don't hand out files from the test Pi — it has the same keys as the real one.
5. Shut the test Pi down and put the right card back. Wipe the spare card or keep it as a ready system copy (then store it safely — it holds the server key).

> ⚠ If anything in the full test fails — the backup won't restore, the installer says "a backup WITHOUT the server key", the list of people doesn't match — fix it **now** (usually: set a backup password and make a new backup). After a real card failure it will be too late.

## Deleting backups

**Where: Browser → Backups**

Select backups in the Backup list (click a row; the box in the header selects all, **Select failed** — all failed ones) and click **Delete selected**. The panel deletes the backup files together with `.sha256` — from the Pi and from the NAS — and only then the entry from the list. If a file can't be deleted (e.g. the NAS is off), the entry stays, so you don't lose track of a file that is still there.

> **Backups from before a reinstall** (made by the previous system) are unknown to the panel at first — they appear after **Refresh list from NAS**, labelled "found", and can be checked, restored or deleted like any other. The other way round: a backup whose file is no longer on the NAS after a refresh (e.g. deleted by hand) gets the state "file missing" — just delete its entry.

> **"Keep on the Pi (last)" cleans only the Pi.** Backups on the NAS stay until you delete them — one is a few KB, so even daily backups for a year come to about 2 MB.

> ⚠ Always keep at least one full backup in the state "checked" — preferably on the NAS.

## Other common tasks

| What you want to do | In the panel | In the terminal |
|---|---|---|
| block for a moment | Suspend (second click: Resume) | `wgctl suspend Marek` / `wgctl resume Marek` |
| cut off a lost phone | Key rotation (the old file stops working, the person gets a new one) or Delete if access should vanish | `wgctl rotate Marek` or `wgctl delete Marek --yes` |
| replace keys | Key rotation | `wgctl rotate Marek` |
| check whether someone has access | Simulator | `wgctl check Marek 192.168.1.11 445` |
| change the NAS address | Hosts → `nas` | `wgctl host add nas 192.168.1.55` |
| see traffic | the chart on the person's page | `wgctl traffic Marek` |
| change the panel password | Settings → Account | — |

Every change to settings, people and rules waits until you click **Apply changes** — nothing happens live before that.

> **IPv6** is off and isn't needed for basic operation. If you ever want to turn it on, see [ARCHITECTURE.md](ARCHITECTURE.md), the "IPv6" section.

## Device names instead of addresses

The **Hosts** tab: enter once `printer = 192.168.1.70`, `camera = 192.168.1.80` and so on. In rules you use names. Changing an address in one place updates every rule.

> ⚠ Give the devices in the catalog fixed addresses (a DHCP reservation on the router). The catalog only translates a name to an address — if a device changes its IP by itself, the rule points at nothing.

## How long the panel keeps history

| What | How long | Where to change it |
|---|---|---|
| Audit log (who changed what and when) | 90 days (an update from an older version keeps the previous 7) | Settings → **Advanced** → **Audit log [days]** |
| Alerts | 90 days (an update from an older version keeps the previous 7) | Settings → **Advanced** → **Alerts [days]** |
| Traffic charts | the last 7 days | fixed |

`0` means **no limit** — the panel deletes nothing from this list. The database grows slowly then: the audit log is usually a handful of entries a day, alerts even fewer. Entries older than the set number of days are deleted once a day. The Audit log and Alerts pages also show the current setting.

> **Tip:** set alerts to 90 or 365 days. An "address change" alert from a month ago may be the only trace of where someone connected from. A full backup takes the whole history with it anyway.

## Every now and then

| When | What |
|---|---|
| after every `ufw reload` | `sudo systemctl restart wg-quick@wg0` |
| once a month | `wgctl list` — do you know everyone on the list |
| once a month | the Alerts tab and the traffic charts |
| once a month | Alerts → **Send test alert** — do notifications still arrive |
| once a quarter | Backups → the box in the list header (select all) → **Check selected** — all "checked" |
| once every six months | the full backup test on a spare card (Part V) |

> ⚠ `ufw reload` reorders the rules and detaches the panel's chains. After every reload, restart `wg-quick`. This is the most common cause of "it worked yesterday, not today".

---

# PART VI — When something goes wrong

## What to do when…

| Symptom | What to do |
|---|---|
| you applied a change and lost your connection | **do nothing for 90 seconds** — the safety fuse rolls back by itself |
| the fuse didn't help | from home over SSH: `wgctl rollback` |
| the panel doesn't respond, the VPN works | `sudo systemctl restart wg-panel` |
| red banner "changed outside the panel" | section below |
| alarm about a mismatched backup | Backups → select backups → **Check selected**, make a new backup, check who has access to the `backup` folder |
| alarm about unusual traffic | the person's page → chart; if it isn't them — key rotation |
| after a Pi reboot, integrity: "first FORWARD rule is not a jump to WG-USERS" | Docker inserted its rules in front of the panel — older panel versions did that. Update the panel and click **Apply changes** once (section "Configuration changed outside the panel") |
| after a Pi reboot, "Handshake never" and traffic 0 | an older panel version — the current one keeps the last handshake and total traffic in its database; update the panel |
| lost phone with the login code | `wgctl totp off admin` |
| `wgctl`: "you are already in the wg-panel group, but this session…" | log out and in again, or `newgrp wg-panel` |
| `wgctl`: "no access … add yourself to the group" | `sudo usermod -aG wg-panel $USER`, then as above (reinstalling does it too) |
| "tunnel on two devices" alert, or one person's connection keeps dropping | the same file is running on two devices — ask them to switch the tunnel off on one. If you can't tell where the other one is: **Key rotation** on their page and a new file only on the right device. If they need two devices, add a second peer |
| one person's RDP disconnects when clicking, and there is no two-devices alert | probably MTU on their link: in the WireGuard app → Edit → under `[Interface]` add `MTU = 1280` |
| notification "panel: problem" | `journalctl -u wg-panel -n 40`; the VPN keeps working, and so do access deadlines (the watchdog) |
| "helper: problem" | `journalctl -u wg-panel-helper -n 40`; without the helper the panel can't apply changes |
| "WireGuard interface: problem" | **users have no access** — `systemctl status wg-quick@wg0`, then `sudo systemctl restart wg-quick@wg0` |
| "Pi power" | power supply or cable too weak — replace with an original one (5 V, 3 A for Pi 4 / 5 A for Pi 5); risks corrupting the card |
| "Pi temperature" | check the cooling and that the case isn't in a closed cupboard |
| "disk space" | `df -h`; lower "Keep on the Pi (last)" in Backup settings or move backups to the NAS |
| "HTTPS certificate" | `wgctl tls --renew`; on error — `journalctl -u wg-panel-helper -n 40` |
| the SD card died | path C at the start of this guide |

> **The VPN doesn't need the panel to work.** The tunnel runs in the kernel, the rules are in iptables (loaded by `wg0.conf` itself, also after a Pi reboot). A panel failure only takes away your ability to make changes — no VPN user will notice it. Access deadlines are enforced by the watchdog in the meantime, and systemd restarts the panel and the helper after a crash.

## The VPN doesn't connect after a restore

Symptom: the tunnel is on on the phone, but the panel shows no handshake for that person. Check in this order.

1. **Do packets from the phone reach the Pi?** Phone on mobile data (Wi-Fi off), on the Pi:

```bash
sudo apt install -y tcpdump
sudo tcpdump -ni eth0 udp port 51820
```

Turn the tunnel on, wait 10 s, **Ctrl+C**. No lines = a router problem (the WireGuard port isn't forwarded to the Pi's address) or the `Endpoint` in the file points somewhere other than `curl -4 ifconfig.me`. Lines in one direction only (`… > <pi-address>.51820`) = the Pi doesn't recognise the file — point 2.

2. **Does the file on the phone match the Pi?** In the WireGuard app tap the tunnel:
   - section **Peer → Public key** = `sudo wg show wg0 public-key`,
   - section **Interface → Public key** = the key next to that person's address in `sudo wg show wg0 allowed-ips`.

   Something differs → delete the tunnel from the app and import a new one from the person's page. If the server key changed (a backup without the key), everyone needs a new file — Step C5.

3. **Firewall.** Briefly `sudo ufw disable`, test, then make sure to `sudo ufw enable`. If it works without ufw — the `51820/udp ALLOW` rule is missing (Step B4).

## Configuration changed outside the panel

First check that it wasn't you: manual editing of `wg0.conf`, `ufw reload`, `wg set` from a terminal. If it was — click **Restore the state from the panel**.

If it wasn't you:

1. Don't restore right away. Save the state: `sudo wg show`, `sudo iptables -S`, `last`.
2. Change your Pi and NAS passwords and check `~/.ssh/authorized_keys`.
3. Only then: **Integrity → Restore the state from the panel**.

> **Docker on the Pi.** Every time Docker starts, it inserts its rules (`DOCKER-USER`, `DOCKER-FORWARD`) at the very top of `FORWARD`. That's why the panel also hooks into the `DOCKER-USER` chain — Docker always checks it first and never flushes it. VPN traffic therefore reaches the panel's rules before Docker's, and a reboot of the Pi or of Docker no longer raises the alarm "first FORWARD rule is not a jump to WG-USERS". Older panel versions didn't do this and raised that alarm after a reboot — after updating, click **Apply changes** once (or **Restore the state from the panel**).

> ⚠ If someone had root on the Pi, they could have changed the panel itself as well. After such an incident the only sure way is a clean SD card and a restore from a backup — below.

## The SD card died or a new Pi

This is **path C** at the start of this guide: a clean card and `sudo ./install.sh --restore` — the installer restores everything from a full backup on the NAS, including the server key. Everyone connects unchanged.

## Uninstalling

**Where: Pi**

| Command | What it removes | The VPN |
|---|---|---|
| `sudo /opt/wg-panel/uninstall.sh` | the program, the services (the watchdog too), `wgctl`, the panel's ufw rules, the panel's certificate authority | keeps working with the last configuration; **panel data stays** — a reinstall brings the panel back |
| `sudo /opt/wg-panel/uninstall.sh --purge` | as above + database, backups on the card, certificates, file copies in `/etc/wireguard`, the `wg-panel` account, the saved install options (`/etc/wg-panel`) | keeps working; from now on you edit `wg0.conf` by hand |
| `sudo /opt/wg-panel/uninstall.sh --restore` | as `--purge` + brings back `wg0.conf` from Step 2 (`/root/wg-panel-before`) | after `sudo reboot` the pre-panel configuration runs |

The same from the unpacked repository: `sudo ./install.sh --uninstall` (with `--purge` or `--restore`).

The script first lists what it will remove and what stays, and waits until you type `yes`. It refuses when a change in the panel is waiting for confirmation — confirm or roll it back first. `--restore` refuses when the Step 2 snapshot doesn't exist.

> **What the script never touches:** the server key, the `wg-quick@wg0` service, the backups on the NAS, or `/root/wg-panel-before`. `--restore` puts the panel's current configuration into `/root/wg-panel-after` in case you want to go back.

> **Why the VPN keeps running after a plain uninstall:** the `wg0.conf` generated by the panel loads the rules from `/etc/wireguard/wg0-panel-rules.v4` at startup by itself, and packet forwarding is enabled by a file in `/etc/sysctl.d`. The script leaves those files — without them the tunnel wouldn't come up after a Pi reboot or wouldn't reach the home network.

After uninstalling, remove the "wg-panel CA" certificate from phones and computers if you installed it (Step 7a).

By hand, if the script isn't available:

```bash
sudo systemctl disable --now wg-panel wg-panel-helper wg-panel-guard.timer
sudo cp /root/wg-panel-before/wg0.conf /etc/wireguard/wg0.conf
sudo reboot
```

---

# PART VII — Security

| Who | Can they get in |
|---|---|
| someone on the internet | no — only the WireGuard port is visible, and it stays silent without a key |
| a VPN person tries to go further than allowed | no — rules on the server |
| someone on the VPN or home Wi-Fi without the panel password | no — the password; after 5 failed attempts login is blocked for 5 minutes |
| an ordinary account on the Pi | no — the database and keys are locked by file permissions |
| someone changes the configuration by hand | it will be detected within 5 minutes and you'll get an alarm |
| someone swaps a backup on the NAS | it will be detected by the checksum |
| someone carries off the SD card | yes — we deliberately don't encrypt, the Pi sits at home |
| someone with root on the Pi | yes — no software on that machine can stop that |

## Password manager and a copy of secrets

Keep in your password manager: the panel password, the backup password, the server's private key, and the password of the backup user on the NAS (`wg-backup`).

The password manager should live **outside the Pi and outside the NAS** (a separate machine or a cloud service). After a Pi failure you reach it from the home network, without the VPN, so a restore (path C, which you do at home anyway) doesn't depend on a working tunnel.

> ⚠ If the password manager runs on this Pi or on the NAS and is reachable only through this VPN, a failure of the Pi creates a vicious circle: to restore the VPN you need the backup password, and to reach the vault you need the VPN. The backup password and the server key must have a second copy outside the Pi and the NAS.

> **Simplest:** a password manager's phone app usually keeps a local copy of the vault and works without a connection to the server. Open it now and then so the copy stays fresh.

---

# Cheat sheet

| Goal | Command |
|---|---|
| state and unapplied changes | `wgctl status` |
| list of people | `wgctl list` |
| new person | `wgctl add Marek --tier NAS` |
| a person for a period | `wgctl add Crew --tier NAS --start 2026-10-01 --until 2026-10-31` |
| a person for N days from first connection | `wgctl add Guest --tier NAS --days 7` |
| change level / name | `wgctl set Marek --tier NET --rename "Marek K."` |
| what goes through the VPN for a person | `wgctl allowed Marek` |
| change what goes through the VPN | `wgctl set Marek --tunnel custom --tunnel-list nas,printer` |
| QR code in the terminal | `wgctl qr Marek` |
| check access | `wgctl check Marek 192.168.1.11 445` |
| what will change | `wgctl diff` |
| apply and confirm | `wgctl apply`, then `wgctl confirm` |
| undo the last apply | `wgctl rollback` |
| list backups / restore | `wgctl restore` / `wgctl restore 12` |
| backups on the NAS (new system) | `wgctl restore --nas` |
| the whole Pi from a full backup (clean card) | `sudo ./install.sh --restore` |
| backup now / verify backups | `wgctl backup` / `wgctl backup --verify` |
| device catalog | `wgctl host list`, `wgctl host add printer 192.168.1.70` |
| traffic | `wgctl traffic Marek` |
| integrity | `wgctl integrity` |
| watchdog by hand | `wgctl guard` |
| access deadlines now (without the panel) | `wgctl expire` |
| HTTPS certificate: fingerprint, validity / renew | `wgctl tls` / `wgctl tls --renew` |
| installer in Polish | `sudo INSTALL_LANG=pl ./install.sh` |
| uninstall (VPN stays) | `sudo /opt/wg-panel/uninstall.sh` |
| back to the pre-panel configuration | `sudo /opt/wg-panel/uninstall.sh --restore` |
| turn off the authenticator code | `wgctl totp off admin` |
| logs | `journalctl -u wg-panel -f` |

The full directory structure and a description of every file: [ARCHITECTURE.md](ARCHITECTURE.md).

---

# Updating and housekeeping

## Updating the panel

**Where: Pi**

```bash
cd ~/wg-panel
git pull
sudo ./install.sh
```

First the installer prints the options it runs with, for example:

```
Options from the previous install (/etc/wg-panel/install.conf): LAN access: 192.168.1.0/24, port 8080, HTTPS: 1
```

The installer remembers the options (`LAN_ACCESS`, `BIND_PORT`, `TLS`, `INSTALL_LANG`) in `/etc/wg-panel/install.conf` — you don't pass them again. On the first update from a version that didn't have this file, you'll see `/etc/systemd/system/wg-panel.service` instead of the file path: the installer then reads the options from the current service and the ufw rule, and after a successful install saves them to `install.conf`. If it says `LAN access: no` but the panel should be reachable from your home network, run the installer once more with `sudo LAN_ACCESS=192.168.1.0/24 ./install.sh` (your network) — that value is remembered too.

What stays:

- peers, tiers, rules, hosts, settings, backups, accounts and history — the database is separate from the program;
- the server key, the HTTPS certificate and people's files — nobody needs a new file;
- tiers you deleted or renamed — an update doesn't bring them back (the built-in tiers and the `nas` host are created only on the first install).

Before the new version starts, the installer saves a copy of the database. In **Backups** it's labelled "before update" — if the new version messes something up, select it and **Restore selected…**. The VPN keeps running the whole time; the installer restarts the panel and the helper — refresh the page in your browser.

A new version sometimes writes `wg0.conf` slightly differently. Then the peer list shows the card "The panel has changes that are not on the interface" — click **Apply changes** and confirm.

To change an option (e.g. the port), pass it when updating: `sudo BIND_PORT=8443 ./install.sh`. An empty value turns it off: `sudo LAN_ACCESS= ./install.sh` — the panel will then be reachable only through the VPN.

The `~/wg-panel` directory (the cloned repository) isn't needed for the panel to run — the installer copied everything from it into `/opt/wg-panel`. Keep it for the next update (`git pull`) or delete it.

## What must stay

| Path | What it is | What happens if you delete it |
|---|---|---|
| `/opt/wg-panel/` | the program: panel, helper, `wgctl`, `uninstall.sh` | the services won't start — the panel, helper and watchdog start from here |
| `/var/lib/wg-panel/` | the database, backups on the card, the panel certificate | the panel starts empty — it won't know any person or rule |
| `/usr/local/bin/wgctl` | the `wgctl` command | `wgctl` stops working from any location |
| `/etc/wireguard/` | VPN configuration, firewall rules, the server key | **the VPN stops working** (after a Pi reboot or at once) |
| `/etc/systemd/system/wg-panel*` | the panel, helper and watchdog services | nothing starts after a Pi reboot |
| `/root/wg-panel-before/` | your snapshot from Step 2 (path A) | you can't go back to the pre-panel configuration (`uninstall.sh --restore`) |

> ⚠ To remove the panel use `sudo /opt/wg-panel/uninstall.sh` (Part VI, "Uninstalling"). Don't delete these directories by hand — e.g. removing `/etc/wireguard/wg0-panel-rules.v4` means the tunnel won't come up after a Pi reboot, because `wg0.conf` loads its rules from it.

## wgctl — the panel in a terminal

`wgctl` does the same as the web panel, only with commands — the full list is in the cheat sheet above. It is useful:

- **in an emergency** — when the panel's web page doesn't work, over SSH you can still suspend someone, make a backup or check the state;
- **automatically** — every minute the watchdog runs `/opt/wg-panel/wgctl guard`; that's why the installed `wgctl` has to stay.

| File | What it is |
|---|---|
| `/opt/wg-panel/wgctl` | the actual program |
| `/usr/local/bin/wgctl` | a short wrapper: it lets you type just `wgctl`; it sets the paths to the database and the helper |

> **Why without `sudo`:** the installer added your account to the `wg-panel` group, which has access to the panel's database. It works from a new login — in the current terminal `newgrp wg-panel` is enough. When in doubt, `sudo wgctl …` always works.
