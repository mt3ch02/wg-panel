#!/bin/bash
# Odinstalowanie wg-panel. Trzy poziomy:
#
#   sudo ./uninstall.sh            usuwa panel (program, uslugi, wgctl, reguly ufw panelu, urzad
#                                  certyfikacji). VPN dziala dalej z ostatnia konfiguracja,
#                                  dane panelu zostaja - ponowna instalacja przywroci panel.
#   sudo ./uninstall.sh --purge    jak wyzej + dane panelu (baza, kopie na karcie, certyfikaty,
#                                  zapamietane opcje instalacji z /etc/wg-panel),
#                                  kopie plikow z /etc/wireguard i konto wg-panel. VPN dalej dziala.
#   sudo ./uninstall.sh --restore  jak --purge + powrot do konfiguracji sprzed panelu
#                                  (/root/wg-panel-before - instalator robi ja przy przejeciu
#                                  istniejacego WireGuarda). Po nim: sudo reboot.
#   sudo ./uninstall.sh --wireguard   jak --purge + caly WireGuard: tunele zatrzymane, konfiguracja
#                                  i klucz serwera z /etc/wireguard usuniete, pakiety WireGuarda
#                                  odinstalowane, regula ufw portu WireGuarda usunieta. VPN przestaje
#                                  dzialac. Potwierdzenie osobna fraza, takze bez --yes.
#
#   Bez opcji (w terminalu) skrypt pyta, ktory poziom wybrac.
#   --yes            bez pytania o potwierdzenie (bez opcji poziomu: tylko panel)
#   INSTALL_LANG=pl  komunikaty po polsku (domyslnie angielski)
#
# Poza --wireguard nigdy nie rusza: klucza serwera, uslugi wg-quick@wg0, reguly ufw portu WireGuarda.
# Nigdy nie rusza: kopii na NAS, /root/wg-panel-before (albo przed-wg-panel ze starszych wersji).
set -eu

ROOT="${WGPANEL_ROOT:-}"          # tylko testy: katalog udajacy "/"
case "${INSTALL_LANG:-en}" in
    en*|EN*) UI=en ;;
    *) UI=pl ;;
esac
t() { if [ "$UI" = en ]; then printf '%s' "$2"; else printf '%s' "$1"; fi; }
info() { printf '\033[1;34m==>\033[0m %s\n' "$1"; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$1" >&2; exit 1; }

MODE=""
YES=0
for arg in "$@"; do
    case "$arg" in
        --purge) MODE=purge ;;
        --restore) MODE=restore ;;
        --wireguard) MODE=wireguard ;;
        --yes|-y) YES=1 ;;
        --uninstall) ;;                       # gdy wywolane przez install.sh --uninstall
        *) die "$(t "nieznana opcja: $arg (dozwolone: --purge, --restore, --wireguard, --yes)" "unknown option: $arg (allowed: --purge, --restore, --wireguard, --yes)")" ;;
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
# starsze wersje zapisywaly stan sprzed panelu w /root/przed-wg-panel (i po-wg-panel)
if [ ! -f "$BEFORE/wg0.conf" ] && [ -f "$ROOT/root/przed-wg-panel/wg0.conf" ]; then
    BEFORE="$ROOT/root/przed-wg-panel"
    AFTER="$ROOT/root/po-wg-panel"
fi

# zmiana czekajaca na potwierdzenie: po usunieciu helpera nikt by jej nie cofnal
if [ -f "$WG/wg-panel-pending.json" ] && grep -q '"pending"' "$WG/wg-panel-pending.json"; then
    die "$(t "zmiana w panelu czeka na potwierdzenie - potwierdź ją albo cofnij, potem odinstaluj" "a panel change is waiting for confirmation - confirm or undo it first, then uninstall")"
fi
# bez opcji poziomu: pytamy (w terminalu); z --yes - tylko panel, jak dotad
if [ -z "$MODE" ]; then
    if [ "$YES" = 1 ] || [ ! -t 0 ]; then
        MODE=keep
    else
        echo
        echo "$(t "Co odinstalować?" "What to uninstall?")"
        echo "  1) $(t "tylko panel - VPN działa dalej, dane panelu zostają (ponowna instalacja je przywróci)" "the panel only - the VPN keeps working, panel data stays (reinstalling brings it back)")"
        echo "  2) $(t "panel i jego dane - VPN działa dalej z ostatnią konfiguracją" "the panel and its data - the VPN keeps working with its last configuration")"
        if [ -f "$BEFORE/wg0.conf" ]; then
            echo "  3) $(t "panel i powrót do konfiguracji sprzed panelu (${BEFORE#"$ROOT"})" "the panel and back to the pre-panel configuration (${BEFORE#"$ROOT"})")"
        fi
        echo "  4) $(t "panel i cały WireGuard - VPN przestaje działać, klucz serwera zostanie usunięty" "the panel and all of WireGuard - the VPN stops, the server key is deleted")"
        printf '%s ' "$(t "Wybierz [1]:" "Choose [1]:")"
        read -r choice
        case "${choice:-1}" in
            1) MODE=keep ;;
            2) MODE=purge ;;
            3) [ -f "$BEFORE/wg0.conf" ] && MODE=restore || die "$(t "nie ma takiej pozycji" "no such item")" ;;
            4) MODE=wireguard ;;
            *) die "$(t "nie ma takiej pozycji - nic nie zmieniono" "no such item - nothing was changed")" ;;
        esac
    fi
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
    echo "  - $(t "konfiguracja z panelu - wraca" "the panel's configuration - replaced by") ${BEFORE#"$ROOT"}/wg0.conf"
fi
# tunele WireGuarda na tym serwerze (pliki *.conf w /etc/wireguard)
TUNNELS=""
for conf in "$WG"/*.conf; do
    [ -e "$conf" ] && TUNNELS="$TUNNELS $(basename "$conf" .conf)"
done
WG_PORT="$(sed -n 's/^ListenPort *= *\([0-9]*\).*/\1/p' "$WG"/*.conf 2>/dev/null | head -n 1)"
if [ "$MODE" = wireguard ]; then
    echo "  - $(t "cały WireGuard: tunele" "all of WireGuard: tunnels")${TUNNELS:- -} $(t "zatrzymane i wyłączone przy starcie" "stopped and disabled at boot")"
    echo "  - $(t "wszystko z /etc/wireguard - konfiguracja, reguły i KLUCZ SERWERA" "everything in /etc/wireguard - configuration, rules and the SERVER KEY")"
    echo "  - $(t "pakiety wireguard i wireguard-tools, przekazywanie pakietów panelu, reguła ufw portu WireGuarda" "the wireguard and wireguard-tools packages, the panel's packet forwarding, the ufw rule for the WireGuard port")"
fi
echo "$(t "Zostaje:" "Stays:")"
case "$MODE" in
    restore)
        echo "  - $(t "klucz serwera, wg-quick@wg0; obecna konfiguracja panelu trafi do" "server key, wg-quick@wg0; the panel's current configuration is saved to") ${AFTER#"$ROOT"}" ;;
    wireguard)
        echo "  - $(t "nic z VPN - żadne urządzenie się nie połączy" "nothing of the VPN - no device will be able to connect")" ;;
    *)
        echo "  - $(t "VPN z ostatnią konfiguracją (wg0.conf, reguły, przekazywanie pakietów) - działa jak teraz" "the VPN with its last configuration (wg0.conf, rules, packet forwarding) - works as now")" ;;
esac
[ "$MODE" = keep ] && echo "  - $(t "dane panelu - ponowna instalacja przywróci panel" "panel data - reinstalling brings the panel back")"
echo "  - $(t "kopie na NAS" "backups on the NAS")"
[ -d "$BEFORE" ] && echo "  - ${BEFORE#"$ROOT"} ($(t "Twoja kopia sprzed panelu" "your pre-panel snapshot"))"
[ "$MODE" != wireguard ] && echo "  - $(t "reguła ufw portu WireGuarda (# wg-panel WireGuard)" "the ufw rule for the WireGuard port (# wg-panel WireGuard)")"
echo

if [ "$MODE" = wireguard ]; then
    # nieodwracalne: klucz serwera znika, kazda osoba potrzebowalaby nowego pliku - zawsze fraza,
    # takze z --yes (w skrypcie: WGPANEL_CONFIRM="usun wireguard" / "remove wireguard")
    echo
    echo "$(t "UWAGA: klucz serwera zostanie usunięty. Masz go w menedżerze haseł albo w pełnej kopii?" "WARNING: the server key will be deleted. Do you have it in a password manager or a full backup?")"
    PHRASE="$(t "usuń wireguard" "remove wireguard")"
    answer="${WGPANEL_CONFIRM:-}"
    if [ -z "$answer" ]; then
        [ -t 0 ] || die "$(t "brak terminala do potwierdzenia - nic nie zmieniono" "no terminal to confirm - nothing was changed")"
        printf '%s ' "$(t "Wpisz \"$PHRASE\", żeby kontynuować:" "Type \"$PHRASE\" to continue:")"
        read -r answer
    fi
    [ "$answer" = "$PHRASE" ] || [ "$answer" = "$(t "usun wireguard" "remove wireguard")" ] || \
        die "$(t "przerwano - nic nie zmieniono" "cancelled - nothing was changed")"
elif [ "$YES" != 1 ]; then
    [ -t 0 ] || die "$(t "brak terminala do potwierdzenia - dodaj --yes" "no terminal to confirm - add --yes")"
    printf '%s ' "$(t "Wpisz \"tak\", żeby kontynuować:" "Type \"yes\" to continue:")"
    read -r answer
    [ "$answer" = "$(t tak yes)" ] || die "$(t "przerwano - nic nie zmieniono" "cancelled - nothing was changed")"
fi

if [ "$MODE" = wireguard ]; then
    info "$(t "Zatrzymuję tunele WireGuarda" "Stopping the WireGuard tunnels")"
    for iface in $TUNNELS; do
        systemctl disable --now "wg-quick@$iface" >/dev/null 2>&1 || true
    done
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
    # port WireGuarda zostaje - VPN dziala dalej bez panelu (poza --wireguard)
    KEEP_RULE="# wg-panel WireGuard"
    [ "$MODE" = wireguard ] && KEEP_RULE="# nic-nie-zostaje"
    for number in $(ufw status numbered 2>/dev/null | grep "# wg-panel" | grep -v -- "$KEEP_RULE" \
                    | sed -n 's/^\[ *\([0-9][0-9]*\)\].*/\1/p' | sort -rn); do
        ufw --force delete "$number" >/dev/null 2>&1 || true
    done
fi

rm -rf "$WG/wg-panel-ca"
rm -f "$WG/wg-panel-pending.json"

if [ "$MODE" = wireguard ]; then
    info "$(t "Usuwam WireGuarda: /etc/wireguard i pakiety" "Removing WireGuard: /etc/wireguard and the packages")"
    rm -rf "${WG:?}"/* "$WG"/.[!.]* 2>/dev/null || true
    rm -f "$SYSCTL/99-wg-panel-forward.conf" "$SYSCTL/99-wg-panel-ipv6.conf"
    if command -v apt-get >/dev/null; then
        DEBIAN_FRONTEND=noninteractive apt-get purge -y wireguard wireguard-tools >/dev/null 2>&1 || true
    fi
fi

if [ "$MODE" != keep ]; then
    info "$(t "Usuwam dane panelu i konto wg-panel" "Removing panel data and the wg-panel account")"
    rm -rf "$DATA" "$WG/wg-panel-backups" "$ROOT/etc/wg-panel"
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
        echo "$(t "Uruchom ponownie Pi: sudo reboot - po restarcie działa konfiguracja sprzed panelu." "Restart the Pi: sudo reboot - after the restart the pre-panel configuration is running.")" ;;
    wireguard)
        echo "$(t "WireGuard usunięty. Uruchom ponownie serwer (sudo reboot), żeby zniknęło przekazywanie pakietów włączone dla VPN." "WireGuard removed. Restart the server (sudo reboot) so the packet forwarding enabled for the VPN is gone.")"
        if [ -n "$WG_PORT" ]; then
            echo "$(t "Na routerze usuń przekierowanie portu $WG_PORT/udp. Jeśli kiedyś otworzyłeś ten port w ufw ręcznie: sudo ufw delete allow $WG_PORT/udp" "On the router remove the port forward for $WG_PORT/udp. If you ever opened this port in ufw by hand: sudo ufw delete allow $WG_PORT/udp")"
        fi ;;
esac
echo "$(t "Jeśli instalowałeś na urządzeniach certyfikat panelu (wg-panel CA), możesz go teraz z nich usunąć." "If you installed the panel certificate (wg-panel CA) on devices, you can remove it from them now.")"
