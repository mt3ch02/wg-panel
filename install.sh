#!/bin/bash
# Instalator wg-panel. Uruchom jako root na Raspberry Pi.
set -euo pipefail

DEST=/opt/wg-panel
DATA=/var/lib/wg-panel
BIND_HOST="${BIND_HOST:-10.0.0.1}"
# LAN_ACCESS=192.168.1.0/24 - panel dostepny takze z sieci domowej
LAN_ACCESS="${LAN_ACCESS:-}"
# z dostepem z LAN panel nasluchuje na wszystkich adresach, a wpuszcza zapora
[ -n "$LAN_ACCESS" ] && BIND_HOST="0.0.0.0"
BIND_PORT="${BIND_PORT:-8080}"
# TLS=0 ./install.sh - panel bez HTTPS (domyslnie HTTPS z wlasnego urzedu certyfikacji)
TLS="${TLS:-1}"
SRC="$(cd "$(dirname "$0")" && pwd)"
# jezyk komunikatow: INSTALL_LANG=pl ./install.sh - polski; domyslnie angielski.
case "${INSTALL_LANG:-en}" in
    en*|EN*) UI=en ;;
    *) UI=pl ;;
esac
# t "polski" "english" - tekst w wybranym jezyku
t() { if [ "$UI" = en ]; then printf '%s' "$2"; else printf '%s' "$1"; fi; }

# odinstalowanie: sudo ./install.sh --uninstall [--purge | --restore] [--yes]
case " $* " in
    *" --uninstall "*) exec bash "$SRC/uninstall.sh" "$@" ;;
esac

info() { printf '\033[1;34m==>\033[0m %s\n' "$1"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$1"; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$1" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "$(t "uruchom przez sudo" "run it with sudo")"

info "$(t "Sprawdzam zależności" "Checking dependencies")"
for tool in python3 wg wg-quick iptables iptables-restore ip6tables; do
    command -v "$tool" >/dev/null || die "$(t "brak $tool - doinstaluj, zanim ruszysz dalej" "$tool is missing - install it before continuing")"
done
# smbclient: kopie na NAS. Bez pytan - pakiet Samby potrafi zapytac o ustawienia WINS
if ! command -v smbclient >/dev/null; then
    info "$(t "Instaluję smbclient (kopie na NAS)" "Installing smbclient (backups to a NAS)")"
    DEBIAN_FRONTEND=noninteractive apt-get install -y smbclient >/dev/null 2>&1 || \
        warn "$(t "nie udało się zainstalować smbclient - kopie na NAS nie zadziałają; spróbuj: sudo apt update && sudo apt install smbclient" "could not install smbclient - backups to a NAS will not work; try: sudo apt update && sudo apt install smbclient")"
fi
python3 - <<'PY' || die "$(t "wymagany Python 3.9 lub nowszy" "Python 3.9 or newer is required")"
import sys
sys.exit(0 if sys.version_info >= (3, 9) else 1)
PY

if [ -f /etc/wireguard/wg0.conf ]; then
    info "$(t "Wykryto istniejący /etc/wireguard/wg0.conf - ścieżka A (przejęcie konfiguracji)" "Found an existing /etc/wireguard/wg0.conf - path A (taking over the configuration)")"
elif [ -f /etc/wireguard/server_private.key ]; then
    info "$(t "Brak wg0.conf, jest klucz serwera - ścieżka B/C (nowy system); tunel podniesie panel" "No wg0.conf, server key present - path B/C (new system); the panel will bring the tunnel up")"
else
    die "$(t "Nie ma ani /etc/wireguard/wg0.conf, ani /etc/wireguard/server_private.key.
    Najpierw wygeneruj klucz serwera (docs/pl/INSTALACJA.md, Krok B3) albo odtwórz go z menedżera haseł (Krok C1)." "Neither /etc/wireguard/wg0.conf nor /etc/wireguard/server_private.key exists.
    Generate a server key first (docs/en/INSTALL.md, step B3) or restore it from your password manager (step C1).")"
fi

info "$(t "Zakładam konto usługi wg-panel" "Creating the wg-panel service account")"
getent group wg-panel >/dev/null || groupadd --system wg-panel
getent passwd wg-panel >/dev/null || \
    useradd --system --gid wg-panel --home-dir "$DATA" --shell /usr/sbin/nologin wg-panel

# konto, z ktorego uruchomiono instalator (np. pi), do grupy wg-panel - wgctl bez sudo.
# Po --purge grupa powstaje od nowa, wiec czlonkostwo trzeba odtworzyc - robimy to tu.
GROUP_ADDED=0
if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != root ] && id "$SUDO_USER" >/dev/null 2>&1; then
    if ! id -nG "$SUDO_USER" | tr ' ' '\n' | grep -qx wg-panel; then
        usermod -aG wg-panel "$SUDO_USER"
        GROUP_ADDED=1
    fi
fi

# straznik wstrzymany na czas aktualizacji - inaczej zglosilby restart uslug jako awarie
systemctl stop wg-panel-guard.timer 2>/dev/null || true

info "$(t "Kopiuję pliki do $DEST" "Copying files to $DEST")"
mkdir -p "$DEST"
rm -rf "$DEST/panel" "$DEST/helper"
cp -r "$SRC/panel" "$SRC/helper" "$DEST/"
install -m 755 "$SRC/wgctl" "$DEST/wgctl"
install -m 755 "$SRC/uninstall.sh" "$DEST/uninstall.sh"
cp "$SRC/README.md" "$DEST/" 2>/dev/null || true
find "$DEST" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
chown -R root:root "$DEST"
chmod -R go-w "$DEST"

mkdir -p "$DATA" "$DATA/backups"
chown -R wg-panel:wg-panel "$DATA"
# setgid: pliki tworzone w katalogu dziedzicza grupe, dzieki czemu wgctl
# dziala dla czlonkow grupy wg-panel bez sudo
chmod 2770 "$DATA"
chmod 2770 "$DATA/backups"
# naprawa praw z wczesniejszych wersji: kopie czyta grupa (jak baze), stan straznika
# i blokade zapisuje takze grupa - inaczej wgctl uruchomione jako pi konczylo sie bledem
find "$DATA/backups" -type f -exec chmod 640 {} + 2>/dev/null || true
for f in "$DATA/panel.db.schedule.lock" "$DATA/watchdog.json"; do
    [ -f "$f" ] && chmod 660 "$f"
done
[ -f "$DATA/panel.db" ] && chmod 660 "$DATA/panel.db"

info "$(t "Instaluję polecenie wgctl" "Installing the wgctl command")"
cat > /usr/local/bin/wgctl <<'WRAP'
#!/bin/sh
# nakladka na /opt/wg-panel/wgctl
export WGPANEL_DB="${WGPANEL_DB:-/var/lib/wg-panel/panel.db}"
export WGPANEL_SOCKET="${WGPANEL_SOCKET:-/run/wg-panel/helper.sock}"
exec /usr/bin/python3 /opt/wg-panel/wgctl "$@"
WRAP
chmod 755 /usr/local/bin/wgctl

info "$(t "Instaluję jednostki systemd" "Installing systemd units")"
install -m 644 "$SRC/systemd/wg-panel-helper.service" /etc/systemd/system/
install -m 644 "$SRC/systemd/wg-panel.service" /etc/systemd/system/
install -m 644 "$SRC/systemd/wg-panel-guard.service" /etc/systemd/system/
install -m 644 "$SRC/systemd/wg-panel-guard.timer" /etc/systemd/system/
if [ "$BIND_HOST" != "10.0.0.1" ] || [ "$BIND_PORT" != "8080" ]; then
    sed -i "s|Environment=WGPANEL_HOST=.*|Environment=WGPANEL_HOST=$BIND_HOST|" \
        /etc/systemd/system/wg-panel.service
    sed -i "s|Environment=WGPANEL_PORT=.*|Environment=WGPANEL_PORT=$BIND_PORT|" \
        /etc/systemd/system/wg-panel.service /etc/systemd/system/wg-panel-guard.service
fi
if [ "$TLS" != "1" ]; then
    sed -i "s|Environment=WGPANEL_TLS=.*|Environment=WGPANEL_TLS=0|" \
        /etc/systemd/system/wg-panel.service /etc/systemd/system/wg-panel-guard.service
elif ! command -v openssl >/dev/null; then
    info "$(t "Instaluję openssl (certyfikat HTTPS)" "Installing openssl (HTTPS certificate)")"
    apt-get install -y openssl >/dev/null || die "$(t "brak openssl: sudo apt install openssl" "openssl is missing: sudo apt install openssl")"
fi

systemctl daemon-reload
systemctl enable wg-panel-helper.service wg-panel.service >/dev/null
# restart, a nie "enable --now": przy ponownej instalacji usluga juz dziala
# i "--now" zostawiloby ja ze starymi ustawieniami
systemctl restart wg-panel-helper.service
sleep 1
systemctl restart wg-panel.service
sleep 1

# baza powstaje przy pierwszym starcie panelu - prawa ustawiamy dopiero teraz,
# zeby wgctl dzialal z konta w grupie wg-panel bez sudo
sleep 1
for f in "$DATA"/panel.db "$DATA"/panel.db-wal "$DATA"/panel.db-shm; do
    [ -f "$f" ] && chown wg-panel:wg-panel "$f" && chmod 660 "$f"
done

info "$(t "Sprawdzam stan" "Checking status")"
systemctl is-active --quiet wg-panel-helper || die "$(t "helper nie wstał: journalctl -u wg-panel-helper -n 40" "the helper did not start: journalctl -u wg-panel-helper -n 40")"
systemctl is-active --quiet wg-panel || die "$(t "panel nie wstał: journalctl -u wg-panel -n 40" "the panel did not start: journalctl -u wg-panel -n 40")"
# straznik i terminy dostepu: co minute, takze gdy panel nie dziala
systemctl enable --now wg-panel-guard.timer >/dev/null

if command -v ufw >/dev/null; then
    if [ -n "$LAN_ACCESS" ]; then
        info "$(t "Dodaję regułę ufw dla panelu (z $LAN_ACCESS)" "Adding a ufw rule for the panel (from $LAN_ACCESS)")"
        ufw allow from "$LAN_ACCESS" to any port "$BIND_PORT" proto tcp comment 'wg-panel LAN' >/dev/null || true
    else
        info "$(t "Dodaję regułę ufw dla panelu (tylko z wg0)" "Adding a ufw rule for the panel (wg0 only)")"
        ufw allow in on wg0 to any port "$BIND_PORT" proto tcp comment 'wg-panel' >/dev/null || true
    fi
    # przez VPN panel wpuszcza tylko osoby zaznaczone w panelu - decyduje lancuch WG-USERS-HOST
fi

LAN_IP="$(ip -4 route get 1.1.1.1 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p' | head -1)"
[ -n "$LAN_IP" ] || LAN_IP="$(t "<adres-pi>" "<pi-address>")"

SCHEME=http
TLS_LINES=""
if [ "$TLS" = "1" ]; then
    SCHEME=https
    for _ in 1 2 3 4 5 6 7 8 9 10; do
        [ -f "$DATA/tls/ca.crt" ] && break
        sleep 1
    done
    if [ -f "$DATA/tls/ca.crt" ]; then
        FINGERPRINT="$(openssl x509 -in "$DATA/tls/ca.crt" -noout -fingerprint -sha256 | cut -d= -f2)"
        TLS_LINES="
$(t "HTTPS: zainstaluj certyfikat panelu raz na każdym urządzeniu - strona /tls panelu
       (np. $SCHEME://$LAN_IP:$BIND_PORT/tls). Odcisk SHA-256 do porównania:" "HTTPS: install the panel certificate once on every device - the panel's /tls page
       (e.g. $SCHEME://$LAN_IP:$BIND_PORT/tls). SHA-256 fingerprint to compare:")
       $FINGERPRINT
       $(t "To samo pokaże: wgctl tls" "The same is shown by: wgctl tls")"
    else
        TLS_LINES="
$(t "HTTPS: certyfikat jeszcze nie powstał - sprawdź: journalctl -u wg-panel-helper -n 40" "HTTPS: the certificate has not been created yet - check: journalctl -u wg-panel-helper -n 40")"
    fi
fi

if [ -n "$LAN_ACCESS" ]; then
    ACCESS_LINE="  $SCHEME://$LAN_IP:$BIND_PORT   - $(t "z sieci domowej" "from the home network")"
else
    ACCESS_LINE="  ssh -L $BIND_PORT:127.0.0.1:$BIND_PORT $SUDO_USER@$LAN_IP   - $(t "potem" "then") http://localhost:$BIND_PORT"
fi

if [ "$GROUP_ADDED" = 1 ]; then
    GROUP_LINE="$(t "wgctl bez sudo: dodałem $SUDO_USER do grupy wg-panel. Działa po ponownym zalogowaniu - w tym terminalu wystarczy: newgrp wg-panel" "wgctl without sudo: $SUDO_USER was added to the wg-panel group. It works after logging in again - in this terminal just run: newgrp wg-panel")"
elif [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != root ]; then
    GROUP_LINE="$(t "wgctl bez sudo: działa (konto $SUDO_USER jest w grupie wg-panel)" "wgctl without sudo: works (account $SUDO_USER is in the wg-panel group)")"
else
    GROUP_LINE="$(t "wgctl bez sudo: dodaj swoje konto do grupy: sudo usermod -aG wg-panel NAZWA (potem zaloguj się ponownie)" "wgctl without sudo: add your account to the group: sudo usermod -aG wg-panel NAME (then log in again)")"
fi

if [ "$UI" = en ]; then
cat <<EOF

Done.

Panel:
$ACCESS_LINE
  $SCHEME://10.0.0.1:$BIND_PORT   - through the VPN, only people with access (wgctl set NAME --panel on)
$TLS_LINES

Sign in: open the panel and create an account - a name and a password of at least 10 characters.
The panel switches to English with the PL / EN switch next to the theme button.

Next: docs/en/INSTALL.md - create the account, check the settings, then import or create peers.
To import an existing wg0.conf (dry run first):
  cd $DEST && sudo cat /etc/wireguard/wg0.conf | sudo -u wg-panel \\
       WGPANEL_DB=$DATA/panel.db python3 -m panel.import_conf - --tier NAS --dry-run

$GROUP_LINE

Note: the first apply overwrites /etc/wireguard/wg0.conf with the generated version.
File backups: /etc/wireguard/wg-panel-backups/

Guard and access expiry run every minute (wg-panel-guard.timer), also when the panel is down.

Uninstall: sudo /opt/wg-panel/uninstall.sh   (--purge, --restore - see docs/en/INSTALL.md)

Logs, if something doesn't work (-f follows live, Ctrl+C ends the view):
  journalctl -u wg-panel -f
  journalctl -u wg-panel-helper -f
  journalctl -u wg-panel-guard -n 20
EOF
else
cat <<EOF

Gotowe.

Panel:
$ACCESS_LINE
  $SCHEME://10.0.0.1:$BIND_PORT   - przez VPN, tylko osoby z dostępem (wgctl set NAZWA --panel on)
$TLS_LINES

Zaloguj się: otwórz panel i załóż konto - nazwa i hasło od 10 znaków.

Dalej: docs/pl/INSTALACJA.md - załóż konto, sprawdź ustawienia, potem zaimportuj albo dodaj osoby.
Import istniejącego wg0.conf (najpierw na próbę):
  cd $DEST && sudo cat /etc/wireguard/wg0.conf | sudo -u wg-panel \\
       WGPANEL_DB=$DATA/panel.db python3 -m panel.import_conf - --tier NAS --dry-run

$GROUP_LINE

Uwaga: pierwszy apply nadpisze /etc/wireguard/wg0.conf wersją generowaną.
Kopie plików: /etc/wireguard/wg-panel-backups/

Strażnik i terminy dostępu działają co minutę (wg-panel-guard.timer), także gdy panel leży.

Odinstalowanie: sudo /opt/wg-panel/uninstall.sh   (--purge, --restore - opis w docs/pl/INSTALACJA.md)

Logi, gdyby coś nie działało (-f pokazuje na bieżąco, Ctrl+C kończy podgląd):
  journalctl -u wg-panel -f
  journalctl -u wg-panel-helper -f
  journalctl -u wg-panel-guard -n 20
EOF
fi
