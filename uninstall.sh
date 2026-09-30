#!/bin/bash
# Odinstalowanie wg-panel. Trzy poziomy:
#
#   sudo ./uninstall.sh            usuwa panel (program, uslugi, wgctl, reguly ufw panelu, urzad
#                                  certyfikacji). VPN dziala dalej z ostatnia konfiguracja,
#                                  dane panelu zostaja - ponowna instalacja przywroci panel.
#   sudo ./uninstall.sh --purge    jak wyzej + dane panelu (baza, kopie na karcie, certyfikaty),
#                                  kopie plikow z /etc/wireguard i konto wg-panel. VPN dalej dziala.
#   sudo ./uninstall.sh --restore  jak --purge + powrot do konfiguracji sprzed panelu
#                                  (/root/wg-panel-before z Kroku 2). Po nim: sudo reboot.
#
#   --yes            bez pytania o potwierdzenie
#   INSTALL_LANG=pl  komunikaty po polsku (domyslnie angielski)
#
# Nigdy nie rusza: klucza serwera, uslugi wg-quick@wg0, kopii na NAS, /root/wg-panel-before.
set -eu

ROOT="${WGPANEL_ROOT:-}"          # tylko testy: katalog udajacy "/"
case "${INSTALL_LANG:-en}" in
    en*|EN*) UI=en ;;
    *) UI=pl ;;
esac
t() { if [ "$UI" = en ]; then printf '%s' "$2"; else printf '%s' "$1"; fi; }
info() { printf '\033[1;34m==>\033[0m %s\n' "$1"; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$1" >&2; exit 1; }

MODE=keep
YES=0
for arg in "$@"; do
    case "$arg" in
        --purge) MODE=purge ;;
        --restore) MODE=restore ;;
        --yes|-y) YES=1 ;;
        --uninstall) ;;                       # gdy wywolane przez install.sh --uninstall
        *) die "$(t "nieznana opcja: $arg (dozwolone: --purge, --restore, --yes)" "unknown option: $arg (allowed: --purge, --restore, --yes)")" ;;
    esac
done

[ "$(id -u)" -eq 0 ] || die "$(t "uruchom przez sudo" "run it with sudo")"

DEST="$ROOT/opt/wg-panel"
DATA="$ROOT/var/lib/wg-panel"
WG="$ROOT/etc/wireguard"
UNITS="$ROOT/etc/systemd/system"
SYSCTL="$ROOT/etc/sysctl.d"
BEFORE="$ROOT/root/wg-panel-before"
AFTER="$ROOT/root/wg-panel-after"

# zmiana czekajaca na potwierdzenie: po usunieciu helpera nikt by jej nie cofnal
if [ -f "$WG/wg-panel-pending.json" ] && grep -q '"pending"' "$WG/wg-panel-pending.json"; then
    die "$(t "zmiana w panelu czeka na potwierdzenie - potwierdź ją albo cofnij, potem odinstaluj" "a panel change is waiting for confirmation - confirm or undo it first, then uninstall")"
fi
if [ "$MODE" = restore ] && [ ! -f "$BEFORE/wg0.conf" ]; then
    die "$(t "nie ma kopii sprzed panelu ($BEFORE/wg0.conf) - bez niej powrót nie jest możliwy. Użyj --purge: VPN zostanie z obecną konfiguracją." "there is no pre-panel copy ($BEFORE/wg0.conf) - going back is not possible without it. Use --purge: the VPN keeps its current configuration.")"
fi

echo
echo "$(t "Zostanie usunięte:" "Will be removed:")"
echo "  - $(t "usługi wg-panel, wg-panel-helper i strażnik (wg-panel-guard)" "services wg-panel, wg-panel-helper and the guard (wg-panel-guard)")"
echo "  - $(t "program" "program") /opt/wg-panel, $(t "polecenie" "command") /usr/local/bin/wgctl"
echo "  - $(t "reguły ufw panelu, urząd certyfikacji panelu" "the panel's ufw rules, the panel's certificate authority")"
if [ "$MODE" != keep ]; then
    echo "  - $(t "dane panelu" "panel data") /var/lib/wg-panel ($(t "baza, kopie na karcie, certyfikaty" "database, backups on the card, certificates"))"
    echo "  - /etc/wireguard/wg-panel-backups, $(t "konto" "account") wg-panel"
fi
if [ "$MODE" = restore ]; then
    echo "  - $(t "konfiguracja z panelu - wraca" "the panel's configuration - replaced by") /root/wg-panel-before/wg0.conf"
fi
echo "$(t "Zostaje:" "Stays:")"
if [ "$MODE" = restore ]; then
    echo "  - $(t "klucz serwera, wg-quick@wg0; obecna konfiguracja panelu trafi do" "server key, wg-quick@wg0; the panel's current configuration is saved to") /root/wg-panel-after"
else
    echo "  - $(t "VPN z ostatnią konfiguracją (wg0.conf, reguły, przekazywanie pakietów) - działa jak teraz" "the VPN with its last configuration (wg0.conf, rules, packet forwarding) - works as now")"
fi
[ "$MODE" = keep ] && echo "  - $(t "dane panelu - ponowna instalacja przywróci panel" "panel data - reinstalling brings the panel back")"
echo "  - $(t "kopie na NAS" "backups on the NAS")"
echo

if [ "$YES" != 1 ]; then
    [ -t 0 ] || die "$(t "brak terminala do potwierdzenia - dodaj --yes" "no terminal to confirm - add --yes")"
    printf '%s ' "$(t "Wpisz \"tak\", żeby kontynuować:" "Type \"yes\" to continue:")"
    read -r answer
    [ "$answer" = "$(t tak yes)" ] || die "$(t "przerwano - nic nie zmieniono" "cancelled - nothing was changed")"
fi

info "$(t "Zatrzymuję usługi" "Stopping services")"
systemctl disable --now wg-panel-guard.timer wg-panel-guard.service wg-panel.service \
    wg-panel-helper.service >/dev/null 2>&1 || true
rm -f "$UNITS/wg-panel.service" "$UNITS/wg-panel-helper.service" \
      "$UNITS/wg-panel-guard.service" "$UNITS/wg-panel-guard.timer"
systemctl daemon-reload >/dev/null 2>&1 || true

info "$(t "Usuwam program i polecenie wgctl" "Removing the program and the wgctl command")"
rm -rf "$DEST" "$ROOT/run/wg-panel"
rm -f "$ROOT/usr/local/bin/wgctl"

if command -v ufw >/dev/null; then
    info "$(t "Usuwam reguły ufw panelu" "Removing the panel's ufw rules")"
    # od konca - po usunieciu numery kolejnych regul sie przesuwaja
    for number in $(ufw status numbered 2>/dev/null | grep "# wg-panel" \
                    | sed -n 's/^\[ *\([0-9][0-9]*\)\].*/\1/p' | sort -rn); do
        ufw --force delete "$number" >/dev/null 2>&1 || true
    done
fi

rm -rf "$WG/wg-panel-ca"
rm -f "$WG/wg-panel-pending.json"

if [ "$MODE" != keep ]; then
    info "$(t "Usuwam dane panelu i konto wg-panel" "Removing panel data and the wg-panel account")"
    rm -rf "$DATA" "$WG/wg-panel-backups"
    userdel wg-panel >/dev/null 2>&1 || true
    groupdel wg-panel >/dev/null 2>&1 || true
fi

if [ "$MODE" = restore ]; then
    info "$(t "Wracam do konfiguracji sprzed panelu" "Going back to the pre-panel configuration")"
    mkdir -p "$AFTER"
    cp -p "$WG/wg0.conf" "$AFTER/" 2>/dev/null || true
    for rules in "$WG"/*-panel-rules.v4 "$WG"/*-panel-rules.v6; do
        [ -e "$rules" ] || continue
        cp -p "$rules" "$AFTER/"
        rm -f "$rules"
    done
    cp "$BEFORE/wg0.conf" "$WG/wg0.conf"
    chmod 600 "$WG/wg0.conf"
    # IPv6 to dodatek panelu; przekazywanie pakietow zostaje - VPN go potrzebuje
    rm -f "$SYSCTL/99-wg-panel-ipv6.conf"
fi

echo
echo "$(t "Gotowe." "Done.")"
case "$MODE" in
    keep)
        echo "$(t "VPN działa dalej. Dane panelu są w /var/lib/wg-panel - ponowna instalacja (install.sh) przywróci panel." "The VPN keeps working. Panel data is in /var/lib/wg-panel - reinstalling (install.sh) brings the panel back.")" ;;
    purge)
        echo "$(t "VPN działa dalej z ostatnią konfiguracją z panelu - od teraz zmieniasz ją ręcznie w /etc/wireguard/wg0.conf." "The VPN keeps working with the panel's last configuration - from now on you edit /etc/wireguard/wg0.conf by hand.")" ;;
    restore)
        echo "$(t "Uruchom ponownie Pi: sudo reboot - po restarcie działa konfiguracja z Kroku 2." "Restart the Pi: sudo reboot - after the restart the configuration from Step 2 is running.")" ;;
esac
echo "$(t "Jeśli instalowałeś na urządzeniach certyfikat panelu (wg-panel CA), możesz go teraz z nich usunąć." "If you installed the panel certificate (wg-panel CA) on devices, you can remove it from them now.")"
