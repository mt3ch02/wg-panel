#!/bin/bash
# Instalator wg-panel. Uruchom jako root na Raspberry Pi.
set -euo pipefail

DEST=/opt/wg-panel
DATA=/var/lib/wg-panel
SRC="$(cd "$(dirname "$0")" && pwd)"

# --- opcje instalacji ---
# Pierwsza instalacja zapisuje je w $CONF, aktualizacja to potem samo "sudo ./install.sh".
# Opcja podana jawnie wygrywa (tak sie ja zmienia). Dostep z LAN jest domyslnie wlaczony przy
# NOWEJ instalacji (LAN_ACCESS=auto - siec tego serwera); wylacza go LAN_ACCESS=off (albo jawnie
# pusta: LAN_ACCESS=). Aktualizacja i odtworzenie z kopii zachowuja wczesniejszy wybor.
# Plik nalezy do roota - konto panelu nie moze zmienic tego, co instalator zrobi jako root.
CONF="${WGPANEL_CONF:-/etc/wg-panel/install.conf}"
# opcje podane jawnie w tym uruchomieniu - wygrywaja tez z opcjami z odtwarzanej kopii
EXPLICIT=" "
for option in LAN_ACCESS BIND_PORT TLS INSTALL_LANG BIND_HOST; do
    [ -n "${!option+x}" ] && EXPLICIT="$EXPLICIT$option "
done
# jawnie pusta LAN_ACCESS= to swiadome wylaczenie - zapamietujemy je jako "off". Pusta wartosc
# z pliku opcji albo z kopii to tez "off": starsze wersje zapisywaly tak "tylko przez VPN" (wtedy
# domyslne) - aktualizacja nie moze po cichu otworzyc panelu na siec domowa.
if [ -n "${LAN_ACCESS+x}" ] && [ -z "$LAN_ACCESS" ]; then LAN_ACCESS=off; fi
UNIT_FILE="${WGPANEL_UNIT:-/etc/systemd/system/wg-panel.service}"
OPTIONS_FROM=""
saved_option() { sed -n "s/^$1=//p" "$CONF" 2>/dev/null | tail -n 1; }
unit_option() { sed -n "s/^Environment=$1=//p" "$UNIT_FILE" 2>/dev/null | tail -n 1; }
if [ -f "$CONF" ]; then
    [ -n "${LAN_ACCESS+x}" ] || LAN_ACCESS="$(saved_option LAN_ACCESS)"
    [ -n "${BIND_PORT+x}" ] || BIND_PORT="$(saved_option BIND_PORT)"
    [ -n "${TLS+x}" ] || TLS="$(saved_option TLS)"
    [ -n "${INSTALL_LANG+x}" ] || INSTALL_LANG="$(saved_option INSTALL_LANG)"
    [ -n "${BIND_HOST+x}" ] || BIND_HOST="$(saved_option BIND_HOST)"
    [ -n "$LAN_ACCESS" ] || LAN_ACCESS=off
    OPTIONS_FROM="$CONF"
elif [ -f "$UNIT_FILE" ]; then
    # panel ze starszej wersji (bez pliku opcji): to, co jest w zainstalowanej usludze
    [ -n "${BIND_PORT+x}" ] || BIND_PORT="$(unit_option WGPANEL_PORT)"
    [ -n "${TLS+x}" ] || TLS="$(unit_option WGPANEL_TLS)"
    if [ -z "${LAN_ACCESS+x}" ] && [ "$(unit_option WGPANEL_HOST)" = "0.0.0.0" ]; then
        # dostep z LAN byl wlaczony - siec bierzemy z reguly zapory albo z ustawien panelu
        LAN_ACCESS="$(ufw status 2>/dev/null | sed -n 's|.*ALLOW[[:space:]]*\([0-9./]*\)[[:space:]]*# wg-panel LAN.*|\1|p' | head -n 1)"
        [ -n "$LAN_ACCESS" ] || LAN_ACCESS="$(python3 -c "import sqlite3; print(sqlite3.connect('file:$DATA/panel.db?mode=ro', uri=True).execute(\"SELECT value FROM settings WHERE key='lan'\").fetchone()[0])" 2>/dev/null || true)"
        [ -n "$LAN_ACCESS" ] || LAN_ACCESS=auto     # byl wlaczony, sieci nie odczytalismy
    elif [ -z "${LAN_ACCESS+x}" ]; then
        LAN_ACCESS=off      # usluga sluchala tylko na VPN - tak zostaje
    fi
    if [ -z "${BIND_HOST+x}" ]; then
        # adres panelu w VPN: z uslugi, a przy dostepie z LAN (0.0.0.0) - adres serwera z ustawien
        BIND_HOST="$(unit_option WGPANEL_HOST)"
        if [ "$BIND_HOST" = "0.0.0.0" ]; then
            BIND_HOST="$(python3 -c "import sqlite3; print(sqlite3.connect('file:$DATA/panel.db?mode=ro', uri=True).execute(\"SELECT value FROM settings WHERE key='server_ip'\").fetchone()[0])" 2>/dev/null || true)"
        fi
    fi
    OPTIONS_FROM="$UNIT_FILE"
fi
# --- koniec opcji instalacji ---

# BIND_HOST=10.8.0.1 - adres panelu w VPN, gdy siec VPN jest inna niz 10.0.0.0/24 (zapamietywany)
VPN_HOST="${BIND_HOST:-}"
# czy panel ma byc dostepny z LAN (LAN_ACCESS to wtedy konkretna siec)
lan_on() { [ -n "$LAN_ACCESS" ] && [ "$LAN_ACCESS" != off ] && [ "$LAN_ACCESS" != auto ]; }
# co pokazac i zapamietac: "off" zamiast pustej wartosci
lan_label() { if lan_on; then printf '%s' "$LAN_ACCESS"; else t "nie" "no"; fi; }
derive_options() {
    # LAN_ACCESS - siec, z ktorej poza VPN wolno otworzyc panel:
    #   auto (domyslnie) - siec tego serwera, 192.168.1.0/24 - podana wprost, off - tylko VPN
    LAN_ACCESS="${LAN_ACCESS:-auto}"
    case "$LAN_ACCESS" in
        off|OFF|no|NO|none|0|false) LAN_ACCESS=off ;;
        AUTO|yes|YES|1|true|on) LAN_ACCESS=auto ;;
    esac
    VPN_HOST="${VPN_HOST:-10.0.0.1}"
    BIND_HOST="$VPN_HOST"
    # z dostepem z LAN panel nasluchuje na wszystkich adresach, a wpuszcza zapora
    lan_on && BIND_HOST="0.0.0.0"
    BIND_PORT="${BIND_PORT:-8080}"
    # TLS=0 ./install.sh - panel bez HTTPS (domyslnie HTTPS z wlasnego urzedu certyfikacji)
    TLS="${TLS:-1}"
    # jezyk komunikatow: INSTALL_LANG=pl ./install.sh - polski; domyslnie angielski.
    case "${INSTALL_LANG:-en}" in
        en*|EN*) UI=en ;;
        *) UI=pl ;;
    esac
}
derive_options
# t "polski" "english" - tekst w wybranym jezyku
t() { if [ "$UI" = en ]; then printf '%s' "$2"; else printf '%s' "$1"; fi; }

# odinstalowanie: sudo ./install.sh --uninstall [--purge | --restore] [--yes]
case " $* " in
    *" --uninstall "*) exec bash "$SRC/uninstall.sh" "$@" ;;
esac
# odtworzenie calego serwera z pelnej kopii: sudo ./install.sh --restore [PLIK_KOPII]
#   bez pliku - kopia z udzialu NAS (instalator pyta o dane i pokazuje liste)
#   --check   - proba: pobiera, sprawdza i opisuje kopie, niczego nie zmienia
#   --test    - odtworzenie na maszynie testowej: bez kopii automatycznych, powiadomien
#               i wydawania plikow osob (wylaczasz w panelu albo: wgctl test off)
RESTORE=0
RESTORE_SRC=""
CHECK=0
TEST=0
ARGS=("$@")
for ((i = 0; i < ${#ARGS[@]}; i++)); do
    case "${ARGS[$i]}" in
        --restore)
            RESTORE=1
            next="${ARGS[$((i + 1))]:-}"
            if [ -n "$next" ] && [ "${next#--}" = "$next" ]; then
                RESTORE_SRC="$next"
                i=$((i + 1))
            fi
            ;;
        --check) CHECK=1 ;;
        --test) TEST=1 ;;
    esac
done

info() { printf '\033[1;34m==>\033[0m %s\n' "$1"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$1"; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$1" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "$(t "uruchom przez sudo" "run it with sudo")"
NEW_VERSION="$(cat "$SRC/VERSION" 2>/dev/null || echo "?")"
info "$(t "wg-panel $NEW_VERSION" "wg-panel $NEW_VERSION")"

# dostep z LAN dopasowany do tego serwera: LAN_ACCESS=auto to siec, w ktorej serwer stoi;
# siec zapamietana albo z kopii, ktora nie pasuje do zadnej sieci serwera (kopia z innego
# komputera, przeprowadzka) tez zamienia sie na siec serwera. Jawnie podanej sieci nie ruszamy.
LAN_SAVE=""
fit_lan_access() {
    LAN_SAVE="$LAN_ACCESS"
    [ "$LAN_ACCESS" != off ] || return 0
    if [ "$LAN_ACCESS" != auto ]; then
        case "$EXPLICIT" in *" LAN_ACCESS "*) return 0 ;; esac
    fi
    local fitted
    fitted="$(PYTHONPATH="$SRC" python3 -m panel.firstrun --fit-lan "$LAN_ACCESS" 2>/dev/null)" || fitted=""
    [ "$fitted" = "$LAN_ACCESS" ] && return 0
    if [ "$LAN_ACCESS" = auto ] && [ -n "$fitted" ]; then
        info "$(t "Dostęp z LAN: sieć tego serwera ($fitted)" "LAN access: this server's network ($fitted)")"
    elif [ "$LAN_ACCESS" = auto ]; then
        warn "$(t "Nie wykryłem sieci tego serwera - panel tylko przez VPN i tunel SSH (podaj LAN_ACCESS=SIEĆ, np. 192.168.1.0/24)" "Couldn't detect this server's network - panel through the VPN and an SSH tunnel only (give LAN_ACCESS=NETWORK, e.g. 192.168.1.0/24)")"
        # "auto" zostaje zapamietane - nastepne uruchomienie sprobuje wykryc siec jeszcze raz
        LAN_ACCESS=off
        derive_options
        return 0
    elif [ -z "$fitted" ]; then
        return 0
    else
        warn "$(t "Dostęp z LAN: $LAN_ACCESS nie pasuje do żadnej sieci tego serwera - biorę jego sieć $fitted" "LAN access: $LAN_ACCESS doesn't match any network of this server - using its network $fitted")"
    fi
    LAN_ACCESS="$fitted"
    LAN_SAVE="$fitted"
    derive_options
}

# opcje (takze te zapamietane) - zanim cokolwiek zmienimy
validate_options() {
    if lan_on && ! [[ "$LAN_ACCESS" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}/[0-9]{1,2}$ ]]; then
        die "$(t "LAN_ACCESS ma być siecią, np. 192.168.1.0/24 (jest: $LAN_ACCESS)" "LAN_ACCESS must be a network, e.g. 192.168.1.0/24 (got: $LAN_ACCESS)")"
    fi
    [[ "$BIND_PORT" =~ ^[0-9]{2,5}$ ]] || die "$(t "BIND_PORT ma być numerem portu (jest: $BIND_PORT)" "BIND_PORT must be a port number (got: $BIND_PORT)")"
    [[ "$TLS" =~ ^[01]$ ]] || die "$(t "TLS ma być 0 albo 1 (jest: $TLS)" "TLS must be 0 or 1 (got: $TLS)")"
    [[ "$VPN_HOST" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || die "$(t "BIND_HOST ma być adresem IPv4 (jest: $VPN_HOST)" "BIND_HOST must be an IPv4 address (got: $VPN_HOST)")"
}
fit_lan_access
validate_options

# --restore: plik kopii podany sciezka - sprawdzamy od razu, zanim cokolwiek zmienimy
if [ -n "$RESTORE_SRC" ]; then
    [ -f "$RESTORE_SRC" ] || die "$(t "nie ma pliku kopii: $RESTORE_SRC" "no such backup file: $RESTORE_SRC")"
    RESTORE_SRC="$(readlink -f "$RESTORE_SRC")"
fi
if [ "$TEST" = 1 ] && [ "$RESTORE" != 1 ]; then
    die "$(t "--test działa razem z --restore: sudo ./install.sh --restore --test [PLIK]" "--test works together with --restore: sudo ./install.sh --restore --test [FILE]")"
fi
if [ "$CHECK" = 1 ]; then
    [ "$RESTORE" = 1 ] || die "$(t "--check działa razem z --restore: sudo ./install.sh --restore --check [PLIK]" "--check works together with --restore: sudo ./install.sh --restore --check [FILE]")"
    if [ -z "$RESTORE_SRC" ] && ! command -v smbclient >/dev/null; then
        die "$(t "brak smbclient - sudo apt install smbclient (albo podaj plik kopii)" "smbclient is missing - sudo apt install smbclient (or give a backup file)")"
    fi
    info "$(t "Próba odtworzenia - nic nie zostanie zmienione" "Restore check - nothing will be changed")"
    PYTHONPATH="$SRC" INSTALL_LANG="$UI" python3 -m panel.restore_cli check "$RESTORE_SRC"
    exit $?
fi
if [ -n "$OPTIONS_FROM" ]; then
    info "$(t "Opcje z poprzedniej instalacji ($OPTIONS_FROM): dostęp z LAN: $(lan_label), port $BIND_PORT, HTTPS: $TLS" "Options from the previous install ($OPTIONS_FROM): LAN access: $(lan_label), port $BIND_PORT, HTTPS: $TLS")"
fi

info "$(t "Sprawdzam zależności" "Checking dependencies")"
# na swiezym systemie brakuje zwykle WireGuarda i iptables - doinstalowujemy sami
MISSING_PKGS=""
command -v wg >/dev/null && command -v wg-quick >/dev/null || MISSING_PKGS="wireguard-tools"
command -v iptables-restore >/dev/null && command -v ip6tables >/dev/null || \
    MISSING_PKGS="$MISSING_PKGS iptables"
if [ -n "$MISSING_PKGS" ]; then
    info "$(t "Doinstalowuję:$MISSING_PKGS" "Installing:$MISSING_PKGS")"
    # shellcheck disable=SC2086
    DEBIAN_FRONTEND=noninteractive apt-get install -y $MISSING_PKGS >/dev/null 2>&1 || true
fi
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

KEY_FILE="${KEY_FILE:-/etc/wireguard/server_private.key}"
NEW_KEY=0

# --- klucz serwera: bez niego nie ma VPN. Pytamy wprost, zamiast konczyc bledem ---
key_menu() {
    local choice="${SERVER_KEY:-}" answer="" key="" pub=""
    if [ -z "$choice" ] && [ -t 0 ]; then
        printf '\n%s\n' "$(t "[!] Na tym Pi nie ma klucza serwera.

    Klucz serwera to tożsamość Twojego VPN - jest wpisany w plik każdej osoby.
    W kopii podstawowej go nie ma (jest tylko w pełnej - sudo ./install.sh --restore).

      1) Mam stary klucz (np. w menedżerze haseł) - wkleję go
         -> wszyscy łączą się dalej swoimi dotychczasowymi plikami
      2) Nie mam go - wygeneruj nowy
         -> osoby i ustawienia odtworzysz z kopii, ale każda osoba dostanie NOWY plik
      3) Przerwij" "[!] This Pi has no server key.

    The server key is your VPN's identity - it is written into everyone's file.
    A basic backup doesn't contain it (only a full one does - sudo ./install.sh --restore).

      1) I have the old key (e.g. in a password manager) - I'll paste it
         -> everyone keeps connecting with their existing files
      2) I don't have it - generate a new one
         -> restore people and settings from a backup, but everyone gets a NEW file
      3) Abort")"
        read -r -p "    $(t "Wybór" "Choice") [1/2/3]: " answer || true
        case "$answer" in
            1) choice=paste ;;
            2) choice=new ;;
            *) choice=abort ;;
        esac
    fi
    case "$choice" in
        paste)
            if [ -t 0 ]; then
                read -r -s -p "    $(t "Wklej klucz prywatny serwera (nie będzie widoczny): " "Paste the server private key (hidden): ")" key || true
                echo
            else
                read -r key || true
            fi
            pub="$(printf '%s' "$key" | wg pubkey 2>/dev/null)" || pub=""
            [ -n "$pub" ] || die "$(t "to nie jest klucz prywatny WireGuard (jedna linia, 44 znaki, kończy się =)" "this is not a WireGuard private key (one line, 44 characters, ends with =)")"
            install -d -m 700 "$(dirname "$KEY_FILE")"
            ( umask 077; printf '%s\n' "$key" > "$KEY_FILE" )
            info "$(t "Klucz publiczny serwera: $pub" "Server public key: $pub")"
            info "$(t "Porównaj go z PublicKey w sekcji [Peer] pliku na telefonie - muszą być takie same." "Compare it with PublicKey in the [Peer] section of the file on your phone - they must match.")"
            ;;
        new)
            install -d -m 700 "$(dirname "$KEY_FILE")"
            ( umask 077; wg genkey > "$KEY_FILE" )
            NEW_KEY=1
            warn "$(t "Nowy klucz serwera. Każda osoba potrzebuje nowego pliku (po odtworzeniu kopii: Rotacja kluczy przy osobie, potem Zastosuj zmiany)." "New server key. Everyone needs a new file (after restoring a backup: Key rotation on each person, then Apply changes).")"
            ;;
        *)
            die "$(t "Nie ma klucza serwera ($KEY_FILE). Uruchom instalator ponownie i wybierz,
    albo bez pytań: SERVER_KEY=paste (klucz na wejściu) / SERVER_KEY=new,
    albo odtwórz całe Pi z pełnej kopii: sudo ./install.sh --restore" "There is no server key ($KEY_FILE). Run the installer again and choose,
    or without questions: SERVER_KEY=paste (key on stdin) / SERVER_KEY=new,
    or restore the whole Pi from a full backup: sudo ./install.sh --restore")"
            ;;
    esac
}
# --- koniec key_menu ---

RESTORE_FILE="$DATA/restore.json"
RESTORE_FULL=0
if [ "$RESTORE" = 1 ]; then
    [ -f /etc/wireguard/wg0.conf ] && die "$(t "--restore jest dla czystego systemu, a tu już działa VPN (/etc/wireguard/wg0.conf). Odtwórz kopię w panelu: Kopie - Odtwórz." "--restore is for a clean system, but a VPN already runs here (/etc/wireguard/wg0.conf). Restore the backup in the panel: Backups - Restore.")"
    if [ -n "$RESTORE_SRC" ]; then
        info "$(t "Odtwarzanie z pliku kopii" "Restoring from a backup file")"
    else
        info "$(t "Odtwarzanie z pełnej kopii na NAS" "Restoring from a full backup on a NAS")"
        command -v smbclient >/dev/null || die "$(t "brak smbclient - sudo apt install smbclient" "smbclient is missing - sudo apt install smbclient")"
    fi
    install -d -m 700 "$DATA"
    set +e
    PYTHONPATH="$SRC" INSTALL_LANG="$UI" python3 -m panel.restore_cli fetch "$RESTORE_FILE" "$RESTORE_SRC"
    FETCH_RC=$?
    set -e
    case "$FETCH_RC" in
        0) RESTORE_FULL=1 ;;
        3) ;;   # kopia bez klucza serwera - zapytamy o niego nizej
        *) rm -f "$RESTORE_FILE" "$RESTORE_FILE.options"
           die "$(t "odtwarzanie przerwane - nic nie zostało zainstalowane" "restore aborted - nothing was installed")" ;;
    esac
    # opcje instalatora z kopii (dostep z LAN, port, HTTPS, jezyk) - poza podanymi jawnie
    if [ -s "$RESTORE_FILE.options" ]; then
        while IFS='=' read -r key value; do
            case "$EXPLICIT" in *" $key "*) continue ;; esac
            case "$key" in
                LAN_ACCESS) LAN_ACCESS="${value:-off}" ;;   # pusta w kopii = "tylko VPN"
                BIND_PORT) BIND_PORT="$value" ;;
                TLS) TLS="$value" ;;
                INSTALL_LANG) INSTALL_LANG="$value" ;;
                BIND_HOST) VPN_HOST="$value" ;;
            esac
        done < "$RESTORE_FILE.options"
        derive_options
        fit_lan_access
        validate_options
        info "$(t "Opcje instalacji z kopii: dostęp z LAN: $(lan_label), port $BIND_PORT, HTTPS: $TLS" "Install options from the backup: LAN access: $(lan_label), port $BIND_PORT, HTTPS: $TLS")"
    fi
    rm -f "$RESTORE_FILE.options"
fi

PATH_A=0
UPDATE=0
if [ -f /etc/wireguard/wg0.conf ] && grep -q "wg-panel" /etc/wireguard/wg0.conf 2>/dev/null; then
    UPDATE=1
    info "$(t "Aktualizacja - konfiguracja panelu zostaje" "Update - the panel configuration stays")"
elif [ -f /etc/wireguard/wg0.conf ]; then
    PATH_A=1
    info "$(t "Wykryto istniejący /etc/wireguard/wg0.conf - ścieżka A (przejęcie konfiguracji)" "Found an existing /etc/wireguard/wg0.conf - path A (taking over the configuration)")"
    # droga powrotna (uninstall.sh --restore): stan sprzed panelu, zapisany tylko raz
    # starsze wersje zapisywaly ten stan w /root/przed-wg-panel - jesli tam jest, zostaje tam
    BEFORE=/root/wg-panel-before
    [ -f /root/przed-wg-panel/wg0.conf ] && BEFORE=/root/przed-wg-panel
    if [ ! -f "$BEFORE/wg0.conf" ]; then
        info "$(t "Zapisuję stan sprzed panelu w $BEFORE (droga powrotna)" "Saving the pre-panel state to $BEFORE (the way back)")"
        mkdir -p "$BEFORE"
        chmod 700 "$BEFORE"
        cp -p /etc/wireguard/wg0.conf "$BEFORE/"
        iptables-save > "$BEFORE/iptables.rules" 2>/dev/null || true
        if command -v ufw >/dev/null; then
            ufw status verbose > "$BEFORE/ufw.txt" 2>/dev/null || true
        fi
    fi
elif [ -f "$KEY_FILE" ]; then
    info "$(t "Jest klucz serwera - nowy system; tunel podniesie panel" "Server key present - new system; the panel will bring the tunnel up")"
else
    key_menu
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

OLD_VERSION="$(cat "$DEST/VERSION" 2>/dev/null || true)"
if [ "$UPDATE" = 1 ]; then
    info "$(t "Aktualizacja wg-panel: ${OLD_VERSION:-starsza niż 1.4.0} -> $NEW_VERSION" "Updating wg-panel: ${OLD_VERSION:-older than 1.4.0} -> $NEW_VERSION")"
fi
info "$(t "Kopiuję pliki do $DEST" "Copying files to $DEST")"
mkdir -p "$DEST"
rm -rf "$DEST/panel" "$DEST/helper"
cp -r "$SRC/panel" "$SRC/helper" "$DEST/"
install -m 755 "$SRC/wgctl" "$DEST/wgctl"
install -m 755 "$SRC/uninstall.sh" "$DEST/uninstall.sh"
install -m 644 "$SRC/VERSION" "$DEST/VERSION" 2>/dev/null || true
cp "$SRC/README.md" "$DEST/" 2>/dev/null || true
find "$DEST" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
chown -R root:root "$DEST"
chmod -R go-w "$DEST"

# aktualizacja: stan panelu do pliku na Pi, zanim nowa wersja wystartuje - gdyby cos popsula,
# w Kopiach jest kopia z plakietka "sprzed aktualizacji"
if [ -f "$DATA/panel.db" ] && getent passwd wg-panel >/dev/null; then
    info "$(t "Zapisuję stan panelu sprzed aktualizacji" "Saving the panel state from before the update")"
    runuser -u wg-panel -- env WGPANEL_DB="$DATA/panel.db" PYTHONPATH="$DEST" INSTALL_LANG="$UI" \
        python3 -m panel.restore_cli snapshot || \
        warn "$(t "nie udało się zapisać stanu sprzed aktualizacji - instaluję dalej" "could not save the pre-update state - continuing")"
fi

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

if [ ! -f "$DATA/panel.db" ]; then
    # nowa instalacja: siec domowa wykryta na Pi zamiast przykladowej 192.168.1.0/24
    info "$(t "Nowa instalacja - wykrywam sieć domową Pi" "New install - detecting the Pi's home network")"
    runuser -u wg-panel -- env WGPANEL_DB="$DATA/panel.db" PYTHONPATH="$DEST" \
        python3 -m panel.firstrun || true
fi

if [ -f "$RESTORE_FILE" ]; then
    info "$(t "Odtwarzam bazę panelu z kopii" "Restoring the panel database from the backup")"
    chown wg-panel:wg-panel "$RESTORE_FILE"
    chmod 600 "$RESTORE_FILE"
    runuser -u wg-panel -- env WGPANEL_DB="$DATA/panel.db" PYTHONPATH="$DEST" INSTALL_LANG="$UI" \
        python3 -m panel.restore_cli import "$RESTORE_FILE" $([ "$TEST" = 1 ] && echo --test) || die "$(t "nie udało się odtworzyć bazy" "could not restore the database")"
fi

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

# opcje do nastepnej aktualizacji - tylko root moze je zmienic
mkdir -p "$(dirname "$CONF")"
chmod 755 "$(dirname "$CONF")"
cat > "$CONF" <<OPTS
# Opcje instalacji wg-panel. Aktualizacja (sudo ./install.sh) bierze je stad.
# Zmiana: podaj opcje przy instalacji, np. sudo LAN_ACCESS=192.168.1.0/24 ./install.sh
# LAN_ACCESS=off - panel tylko przez VPN i tunel SSH; auto - siec tego serwera
LAN_ACCESS=${LAN_SAVE:-$LAN_ACCESS}
BIND_PORT=$BIND_PORT
TLS=$TLS
INSTALL_LANG=$UI
BIND_HOST=$VPN_HOST
OPTS
chmod 644 "$CONF"

if [ -f "$RESTORE_FILE" ]; then
    info "$(t "Podnoszę tunel z odtworzonej konfiguracji" "Bringing the tunnel up from the restored configuration")"
    runuser -u wg-panel -- env WGPANEL_DB="$DATA/panel.db" PYTHONPATH="$DEST" INSTALL_LANG="$UI" \
        WGPANEL_SOCKET=/run/wg-panel/helper.sock python3 -m panel.restore_cli apply || \
        warn "$(t "tunel nie wstał sam - w panelu kliknij Zastosuj zmiany" "the tunnel did not come up - click Apply changes in the panel")"
    rm -f "$RESTORE_FILE"
fi

if command -v ufw >/dev/null; then
    # reguly panelu dla innej sieci albo innego portu (zmiana sieci, przeprowadzka, kopia z innego
    # komputera, wylaczony dostep z LAN) - od konca, numery kolejnych sie przesuwaja
    for number in $(ufw status numbered 2>/dev/null | grep "# wg-panel LAN" \
                    | grep -v -- "^\[ *[0-9]*\] $BIND_PORT/tcp .* ${LAN_ACCESS:-brak-dostepu-z-lan} " \
                    | sed -n 's/^\[ *\([0-9][0-9]*\)\].*/\1/p' | sort -rn); do
        ufw --force delete "$number" >/dev/null 2>&1 || true
    done
    if lan_on; then
        info "$(t "Dodaję regułę ufw dla panelu (z $LAN_ACCESS)" "Adding a ufw rule for the panel (from $LAN_ACCESS)")"
        ufw allow from "$LAN_ACCESS" to any port "$BIND_PORT" proto tcp comment 'wg-panel LAN' >/dev/null || true
    else
        info "$(t "Dodaję regułę ufw dla panelu (tylko z wg0)" "Adding a ufw rule for the panel (wg0 only)")"
        ufw allow in on wg0 to any port "$BIND_PORT" proto tcp comment 'wg-panel' >/dev/null || true
    fi
    # przez VPN panel wpuszcza tylko osoby zaznaczone w panelu - decyduje lancuch WG-USERS-HOST
    # port WireGuarda z ustawien panelu - bez niego, przy wlaczonym ufw, nikt sie nie polaczy;
    # zmiane portu w panelu helper przenosi na zapore przy zastosowaniu zmian
    WG_PORT="$(python3 -c "import sqlite3; print(sqlite3.connect('file:$DATA/panel.db?mode=ro', uri=True).execute(\"SELECT value FROM settings WHERE key='listen_port'\").fetchone()[0])" 2>/dev/null || true)"
    if [[ "$WG_PORT" =~ ^[0-9]{2,5}$ ]]; then
        info "$(t "Otwieram w ufw port WireGuarda ($WG_PORT/udp)" "Opening the WireGuard port in ufw ($WG_PORT/udp)")"
        ufw allow "$WG_PORT/udp" comment 'wg-panel WireGuard' >/dev/null || true
    fi
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

if lan_on; then
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

# co dalej - zalezy od tego, z czego startowalismy
if [ "$RESTORE" = 1 ] && [ "$RESTORE_FULL" = 1 ]; then
    NEXT_LINES="$(t "Odtworzone z pełnej kopii: zaloguj się swoim dotychczasowym kontem. VPN działa,
osoby łączą się swoimi dotychczasowymi plikami, certyfikat HTTPS jest ten sam co wcześniej." "Restored from a full backup: sign in with your existing account. The VPN is up,
everyone connects with their existing files, the HTTPS certificate is the same as before.")"
elif [ "$RESTORE" = 1 ]; then
    NEXT_LINES="$(t "Odtworzone z kopii bez klucza serwera: osoby i ustawienia wróciły, ale klucz serwera
jest inny niż wcześniej - każda osoba potrzebuje nowego pliku: w panelu przy każdej
osobie Rotacja kluczy, potem Zastosuj zmiany i przekaż nowy plik albo kod QR." "Restored from a backup without the server key: people and settings are back, but the server key
differs from before - everyone needs a new file: in the panel, Key rotation on each person,
then Apply changes and hand over the new file or QR code.")"
elif [ "$UPDATE" = 1 ]; then
    NEXT_LINES="$(t "Aktualizacja gotowa - osoby i ustawienia zostały. Jeśli lista peerów pokaże
„Panel ma zmiany, których nie ma na interfejsie”, kliknij Zastosuj zmiany i potwierdź." "Update done - people and settings are unchanged. If the peer list shows
\"The panel has changes that are not on the interface\", click Apply changes and confirm.")"
elif [ "$PATH_A" = 1 ]; then
    NEXT_LINES="$(t "Zaloguj się: otwórz panel i załóż konto - nazwa i hasło od 10 znaków.

Dalej: docs/pl/INSTALACJA.md, ścieżka A. Import obecnej konfiguracji (najpierw na próbę):
  cd $DEST && sudo cat /etc/wireguard/wg0.conf | sudo -u wg-panel \\
       WGPANEL_DB=$DATA/panel.db python3 -m panel.import_conf - --tier NAS --dry-run" "Sign in: open the panel and create an account - a name and a password of at least 10 characters.
The panel switches to English with the PL / EN switch next to the theme button.

Next: docs/en/INSTALL.md, path A. Import the current configuration (dry run first):
  cd $DEST && sudo cat /etc/wireguard/wg0.conf | sudo -u wg-panel \\
       WGPANEL_DB=$DATA/panel.db python3 -m panel.import_conf - --tier NAS --dry-run")"
else
    NEXT_LINES="$(t "Zaloguj się: otwórz panel (przy pierwszym wejściu założysz konto). Masz kopię na NAS?
Zakładka Kopie - Odśwież listę z NAS - zaznacz kopię - Odtwórz zaznaczoną. Albo dodaj osoby od zera." "Sign in: open the panel (you'll create an account on the first visit). Have a backup on a NAS?
Backups tab - Refresh list from NAS - select a backup - Restore selected. Or add people from scratch.")"
fi
if [ "$NEW_KEY" = 1 ]; then
    NEXT_LINES="$NEXT_LINES

$(t "WAŻNE: zapisz nowy klucz serwera w menedżerze haseł (albo ustaw hasło kopii - pełna kopia
zawiera klucz): sudo cat $KEY_FILE" "IMPORTANT: save the new server key in a password manager (or set a backup password - a full backup
contains the key): sudo cat $KEY_FILE")"
fi

if [ "$UI" = en ]; then
cat <<EOF

Done. wg-panel $NEW_VERSION

Panel:
$ACCESS_LINE
  $SCHEME://$VPN_HOST:$BIND_PORT   - through the VPN, only tiers with port $BIND_PORT in "Ports on the server itself" (ADMIN by default)
$TLS_LINES

$NEXT_LINES

$GROUP_LINE

Note: the first apply overwrites /etc/wireguard/wg0.conf with the generated version.
File backups: /etc/wireguard/wg-panel-backups/

Guard and access expiry run every minute (wg-panel-guard.timer), also when the panel is down.

Update later: cd $SRC && git pull && sudo ./install.sh
  (the install options are remembered in $CONF)
Uninstall: sudo /opt/wg-panel/uninstall.sh   (it asks what to remove - see docs/en/INSTALL.md)

Logs, if something doesn't work (-f follows live, Ctrl+C ends the view):
  journalctl -u wg-panel -f
  journalctl -u wg-panel-helper -f
  journalctl -u wg-panel-guard -n 20
EOF
else
cat <<EOF

Gotowe. wg-panel $NEW_VERSION

Panel:
$ACCESS_LINE
  $SCHEME://$VPN_HOST:$BIND_PORT   - przez VPN, tylko tiery z portem $BIND_PORT w „Porty na samym serwerze” (domyślnie ADMIN)
$TLS_LINES

$NEXT_LINES

$GROUP_LINE

Uwaga: pierwszy apply nadpisze /etc/wireguard/wg0.conf wersją generowaną.
Kopie plików: /etc/wireguard/wg-panel-backups/

Strażnik i terminy dostępu działają co minutę (wg-panel-guard.timer), także gdy panel leży.

Aktualizacja w przyszłości: cd $SRC && git pull && sudo ./install.sh
  (opcje instalacji są zapamiętane w $CONF)
Odinstalowanie: sudo /opt/wg-panel/uninstall.sh   (zapyta, co usunąć - opis w docs/pl/INSTALACJA.md)

Logi, gdyby coś nie działało (-f pokazuje na bieżąco, Ctrl+C kończy podgląd):
  journalctl -u wg-panel -f
  journalctl -u wg-panel-helper -f
  journalctl -u wg-panel-guard -n 20
EOF
fi

if [ "$TEST" = 1 ]; then
    warn "$(t "To jest SERWER TESTOWY: kopie automatyczne i powiadomienia wyłączone, pliki osób nie są wydawane. Właściwy serwer: przycisk w panelu albo wgctl test off." "This is a TEST SERVER: automatic backups and notifications are off, people's files are not issued. To make it the real server: the button in the panel or wgctl test off.")"
fi
