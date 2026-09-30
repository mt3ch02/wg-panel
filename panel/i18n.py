"""Języki panelu: polski (źródłowy) i angielski.

Tekst polski jest kluczem - w kodzie zostaje czytelny polski, a angielski siedzi w słowniku EN.
Brak tłumaczenia to nie błąd strony: panel pokaże tekst polski, a test zgłosi brak
(zbiór MISSING).

Użycie:  _("Usunąć {name}?", name=peer["name"])
"""
from __future__ import annotations

import threading

LANGS = ("pl", "en")
DEFAULT = "pl"
NAMES = {"pl": "Polski", "en": "English"}
COOKIE = "lang"

_state = threading.local()
MISSING: set[str] = set()


def set_lang(value: str | None) -> str:
    _state.lang = value if value in LANGS else DEFAULT
    return _state.lang


def lang() -> str:
    return getattr(_state, "lang", DEFAULT)


# Komentarze regul wbudowanych tierow trafiaja do zapory (iptables --comment), wiec w bazie
# zostaja bez zmian (inaczej panel zglosilby niezastosowane zmiany). Polskie znaki i angielski
# dostaja tylko przy wyswietlaniu.
PL_DISPLAY = {"brak dostepu do LAN": "brak dostępu do LAN"}


def maybe(text: str) -> str:
    """Dane, ktore moga byc tekstem domyslnym (opis wbudowanego tieru, komentarz jego reguly):
    tlumaczy tylko znany tekst i nie zglasza braku - tekst uzytkownika zostaje, jak jest."""
    if lang() == "en":
        return EN.get(text, text)
    return PL_DISPLAY.get(text, text)


def canonical(value: str, known) -> str:
    """Odwrotnosc maybe() przy zapisie: angielska wersja domyslnego tekstu wraca do bazy jako
    polski klucz - inaczej po przelaczeniu na polski opis zostalby po angielsku."""
    for key in known:
        if value in (EN.get(key), PL_DISPLAY.get(key)):
            return key
    return value


def pl(text: str, **values) -> str:
    """Tekst do dziennika: zawsze polski klucz, tlumaczony dopiero przy wyswietlaniu."""
    return text.format(**values) if values else text


# --------------------------------------------------------------------------
# dziennik: czytelne nazwy akcji i tlumaczenie zapisanych szczegolow
# --------------------------------------------------------------------------

AUDIT_ACTIONS = {
    "account.password": "zmiana hasła", "account.totp_off": "kod z aplikacji wyłączony",
    "account.totp_on": "kod z aplikacji włączony", "alerts.clear": "usunięto alerty",
    "alerts.settings": "ustawienia powiadomień", "alerts.test": "testowy alert",
    "apply.auto": "automatyczne zastosowanie", "apply.auto_rollback": "automatyczne cofnięcie",
    "apply.confirmed": "potwierdzono zmiany", "apply.confirmed.auto": "potwierdzono automatycznie",
    "apply.failed": "zastosowanie nieudane", "apply.ok": "zastosowano zmiany",
    "apply.orphaned": "zmiana nieznana helperowi", "backup.export": "pobrano kopię",
    "backup.failed": "kopia nieudana", "backup.ok": "kopia zapasowa", "backup.delete": "usunięto kopie",
    "backup.restore": "odtworzono kopię", "backup.settings": "ustawienia kopii",
    "backup.snapshot": "migawka przed odtworzeniem", "backup.verify": "weryfikacja kopii",
    "changes.discard": "odrzucono zmiany", "config.pull": "przejęto peerów z dysku",
    "delivery.used": "użyto linku jednorazowego", "history.prune": "czyszczenie historii",
    "host.create": "dodano host", "host.delete": "usunięto host", "host.edit": "zmieniono host",
    "integrity.skip": "integralność - bez alarmu", "ipv6.on": "IPv6 włączone",
    "ipv6.off": "IPv6 wyłączone", "login.failed": "nieudane logowanie", "login.ok": "logowanie",
    "migrate": "porządki po starszej wersji", "peer.activated": "start licznika dostępu",
    "peer.active": "wznowiono peera", "peer.suspended": "zawieszono peera",
    "peer.config_download": "pobrano plik konfiguracji", "peer.create": "dodano peera",
    "peer.delete": "usunięto peera", "peer.delivery_link": "link jednorazowy",
    "peer.edit": "zmieniono peera", "peer.expired": "dostęp wygasł",
    "peer.issued": "plik oznaczony jako wysłany", "peer.revoke": "odcięto peera",
    "peer.rotate_keys": "rotacja kluczy", "peer.window_open": "otwarcie okna dostępu",
    "peer.wipe_private_key": "skasowano klucz prywatny", "rollback": "cofnięto konfigurację",
    "rule.add": "dodano regułę", "rule.delete": "usunięto regułę",
    "settings.update": "zmiana ustawień", "setup.user_created": "utworzono konto",
    "tier.create": "dodano tier", "tier.delete": "usunięto tier", "tier.edit": "zmieniono tier",
    "tier.rule_add": "dodano regułę tieru", "tier.rule_delete": "usunięto regułę tieru",
    "tls.renew": "odnowiono certyfikat", "tls.settings": "zmiana trybu certyfikatu", "wg.restart": "restart wg-quick",
}
AUDIT_ACTORS = {"harmonogram": "harmonogram", "anonim": "anonim"}

# szczegoly zapisywane po polsku (czesc bez polskich znakow - starsze wpisy i helper)
LOGGED_TEMPLATES = [
    "brak potwierdzenia na czas", "helper nie zna oczekującej zmiany",
    "zaszyfrowana", "bez szyfrowania", "nieudany", "nie ma takiej kopii", "suma zgodna",
    "przydzielono {n} adresów", "{n} różnic, ale panel ma niezastosowane zmiany",
    "zmiana okien dostępu: {names}",
    "{n} odciętych peerów -> zawieszeni ze skasowanym kluczem",
    "alias $NAS -> nas w {n} miejscach", "dodano host nas z dawnego ustawienia", "dodano {n}",
    "{label}: {summary}; wcześniejszy stan w {safety}",
    "{n} (karta: {local}, NAS: {remote})",
    "Nie ma kopii do sprawdzenia.",
    "{bad} z {total} kopii NIE przeszło weryfikacji - szczegóły w tabeli.",
    "Sprawdzono {n} kopii - sumy kontrolne zgodne.",
    # kroki zastosowania zapisywane przez helpera
    "ip6tables: łańcuchy panelu usunięte", "ip6tables: łańcuchy przebudowane",
    "iptables: łańcuchy przebudowane", "ipv4: włączono przekazywanie pakietów",
    "ipv6: przekazywanie włączone (accept_ra=2 na WAN)", "ipv6: przekazywanie wyłączone",
    "nat6: dodano MASQUERADE", "nat: dodano MASQUERADE", "wg syncconf: peery zsynchronizowane",
    "commit-confirm: rollback za {n} s bez potwierdzenia", "ipv6: adres {address} na {iface}",
    "sysctl: nie zapisano {path}: {error}", "wg-quick@{iface}: interfejs podniesiony",
    "{unit}: włączony przy starcie systemu",
    "wg-quick@{iface}: zatrzymany (przed zmianą tunelu nie było)",
]
_FOLD = str.maketrans("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ", "acelnoszzACELNOSZZ")
_COMPILED: list = []


def _compiled() -> list:
    """Wzorce z szablonow: {n}/{bad}/{total} to liczby, reszta dowolny tekst."""
    import re
    if not _COMPILED:
        for template in LOGGED_TEMPLATES:
            pattern = ""
            for piece in re.split(r"(\{\w+\})", template.translate(_FOLD)):
                name = piece[1:-1] if piece.startswith("{") and piece.endswith("}") else ""
                if name:
                    pattern += rf"(?P<{name}>\d+)" if name in ("n", "bad", "total") else rf"(?P<{name}>.+?)"
                else:
                    pattern += re.escape(piece)
            _COMPILED.append((re.compile(pattern), template))
    return _COMPILED


def logged(text: str) -> str:
    """Zapisany po polsku szczegol w jezyku panelu. Wzorzec dopasowuje sie do tekstu bez
    polskich znakow (starsze wpisy), a wartosci bierze z oryginalu (nazwy z polskimi znakami)."""
    folded = text.translate(_FOLD)
    for regex, template in _compiled():
        match = regex.fullmatch(folded)
        if match:
            values = {k: text[match.start(k):match.end(k)] for k in match.groupdict()}
            return _(template, **values)
    return helper(text)


def audit_detail(text: str) -> str:
    """Szczegol wpisu: caly znany tekst albo lista krokow rozdzielona "; "."""
    text = str(text or "")
    whole = logged(text)
    if whole != text or "; " not in text:
        return whole
    return "; ".join(logged(part) for part in text.split("; "))


def audit_action(code: str) -> str:
    label = AUDIT_ACTIONS.get(code)
    return _(label) if label else code


def audit_actor(name: str) -> str:
    return _(AUDIT_ACTORS[name]) if name in AUDIT_ACTORS else name


def _(text: str, **values) -> str:
    if lang() == "en":
        translated = EN.get(text)
        if translated is None:
            MISSING.add(text)
            translated = text
    else:
        translated = text
    return translated.format(**values) if values else translated


# Komunikaty helpera: helper (root) pisze po polsku bez znakow diakrytycznych i zostaje
# nietkniety. Panel rozpoznaje te, ktore widac w codziennej pracy, i pokazuje je w jezyku
# panelu. Nieznany komunikat (np. rzadki blad walidacji) pokazuje sie bez zmian.
HELPER_PATTERNS = [
    (r"nic nie czeka na potwierdzenie", "nic nie czeka na potwierdzenie"),
    (r"nic nie czeka na potwierdzenie - nic nie cofnieto", "nic nie czeka na potwierdzenie - nic nie cofnięto"),
    (r"za pozno - brak potwierdzenia na czas, zmiany cofnely sie automatycznie",
     "za późno - brak potwierdzenia na czas, zmiany cofnęły się automatycznie"),
    (r"zmiany juz cofnely sie automatycznie - nic wiecej nie cofam",
     "zmiany już cofnęły się automatycznie - nic więcej nie cofam"),
    (r"zly token potwierdzenia", "zły token potwierdzenia"),
    (r"trwa juz zmiana czekajaca na potwierdzenie", "trwa już zmiana czekająca na potwierdzenie"),
    (r"pliki certyfikatu musza lezec w (?P<dirs>.+)", "pliki certyfikatu muszą leżeć w {dirs}"),
    (r"klucz nie pasuje do certyfikatu", "klucz nie pasuje do certyfikatu"),
    (r"to nie jest certyfikat w formacie PEM", "to nie jest certyfikat w formacie PEM"),
    (r"to nie jest klucz prywatny w formacie PEM", "to nie jest klucz prywatny w formacie PEM"),
    (r"nie moge odczytac pliku: (?P<error>.+)", "nie mogę odczytać pliku: {error}"),
    (r"kopia SMB: (?P<name>\S+) lezy pod innym adresem niz w ustawieniach",
     "kopia SMB: {name} leży pod innym adresem niż w ustawieniach - usuń ją ręcznie w DSM"),
    (r"brak dostepu do katalogu kopii (?P<path>\S+) - zainstaluj panel ponownie",
     "brak dostępu do katalogu kopii {path} - zainstaluj panel ponownie"),
    (r"kopia SMB: brak adresu albo udzialu NAS",
     "kopia SMB: brak adresu albo udziału NAS - uzupełnij je w Harmonogramie i celu"),
    (r"kopia SMB: brak uzytkownika albo hasla NAS",
     "kopia SMB: brak użytkownika albo hasła NAS - uzupełnij je w Harmonogramie i celu"),
    (r"brak smbclient - zainstaluj: apt install smbclient",
     "brak smbclient - zainstaluj na Pi: sudo apt install smbclient"),
    (r"brak programu openssl - zainstaluj: sudo apt install openssl",
     "brak programu openssl - zainstaluj: sudo apt install openssl"),
    (r"(?P<path>\S+) zmieniony poza panelem", "{path} zmieniony poza panelem"),
    (r"plik regul IPv4 zmieniony poza panelem", "plik reguł IPv4 zmieniony poza panelem"),
    (r"plik regul IPv6 zmieniony poza panelem", "plik reguł IPv6 zmieniony poza panelem"),
    (r"(?P<path>\S+) ma prawa (?P<mode>\S+) - czytelne dla innych kont",
     "{path} ma prawa {mode} - czytelne dla innych kont"),
    (r"na interfejsie sa peery spoza panelu: (?P<n>\d+) \(np\. (?P<key>\S+)\)",
     "na interfejsie są peery spoza panelu: {n} (np. {key})"),
    (r"brakuje (?P<n>\d+) peerow, ktore panel zastosowal", "brakuje {n} peerów, które panel zastosował"),
    (r"(?P<tool>\S+): brak lancucha (?P<chain>\S+)", "{tool}: brak łańcucha {chain}"),
    (r"(?P<tool>\S+): (?P<chain>\S+) ma (?P<have>\d+) regul zamiast (?P<want>\d+)",
     "{tool}: {chain} ma {have} reguł zamiast {want}"),
    (r"(?P<tool>\S+): pierwsza regula FORWARD to nie skok do (?P<chain>\S+) - cos zostalo wstawione "
     r"przed panelem", "{tool}: pierwsza reguła FORWARD to nie skok do {chain} - coś zostało "
     "wstawione przed panelem"),
]


def helper(text: str) -> str:
    """Komunikat helpera w jezyku panelu (znany wzorzec) albo bez zmian."""
    import re
    text = str(text)
    for pattern, template in HELPER_PATTERNS:
        match = re.fullmatch(pattern, text)
        if match:
            return _(template, **match.groupdict())
    return text


# Flagi jako SVG - emoji flag na Windowsie wyświetlają się jako same litery "PL".
FLAGS = {
    "pl": ('<svg class="flag" viewBox="0 0 16 10" aria-hidden="true">'
           '<rect width="16" height="5" fill="#fff"/><rect y="5" width="16" height="5" fill="#dc143c"/>'
           '<rect width="16" height="10" fill="none" stroke="rgba(0,0,0,.25)" stroke-width=".5"/></svg>'),
    "en": ('<svg class="flag" viewBox="0 0 60 30" aria-hidden="true">'
           '<clipPath id="flag-uk-t"><path d="M30,15h30v15zv15h-30zh-30v-15zv-15h30z"/></clipPath>'
           '<rect width="60" height="30" fill="#012169"/>'
           '<path d="M0,0L60,30M60,0L0,30" stroke="#fff" stroke-width="6"/>'
           '<path d="M0,0L60,30M60,0L0,30" clip-path="url(#flag-uk-t)" stroke="#c8102e" stroke-width="4"/>'
           '<path d="M30,0v30M0,15h60" stroke="#fff" stroke-width="10"/>'
           '<path d="M30,0v30M0,15h60" stroke="#c8102e" stroke-width="6"/></svg>'),
}


# --------------------------------------------------------------------------
# angielski - klucz to dokładny tekst polski
# --------------------------------------------------------------------------

EN: dict[str, str] = {}

# --- szkielet strony, pasek oczekiwania, okno potwierdzenia ---
EN.update({
    'Odrzucić zmiany w panelu?':
        'Discard changes in the panel?',
    'Panel wróci do stanu, który działa na interfejsie. Niezastosowane zmiany znikną z panelu. Na samym interfejsie nic się nie zmieni.':
        'The panel will go back to what is running on the interface. Unapplied changes will disappear from the panel. Nothing changes on the interface itself.',
    'Odrzuć zmiany':
        'Discard changes',
    'Panel nie może teraz sprawdzić helpera - licznik jest niedostępny.':
        "The panel can't reach the helper right now - the countdown is unavailable.",
    'Bez potwierdzenia wrócą same za {seconds}&nbsp;s.':
        'Without confirmation they revert in {seconds}&nbsp;s.',
    'Sprawdzam, czy nadal masz połączenie z panelem...':
        'Checking that you can still reach the panel...',
    'Trwa to dłużej niż zwykle. Jeśli widzisz tę stronę, możesz potwierdzić ręcznie.':
        'This is taking longer than usual. If you can see this page, you can confirm manually.',
    'Zmiany czekają na potwierdzenie.':
        'Changes are waiting for confirmation.',
    'Potwierdź, gdy sprawdzisz, że nadal masz połączenie.':
        'Confirm once you have checked that you are still connected.',
    'Cofnąć tę zmianę?':
        'Undo this change?',
    'Na interfejsie wróci konfiguracja sprzed zastosowania. Twoje zmiany zostaną w panelu jako niezastosowane.':
        'The interface will go back to the configuration from before this change. Your changes stay in the panel as unapplied.',
    'Cofnij zmianę':
        'Undo change',
    'Potwierdzam, działa':
        'Confirm, it works',
    'Cofnij teraz':
        'Undo now',
    'Anuluj':
        'Cancel',
    'Tak':
        'Yes',
    'Na pewno?':
        'Are you sure?',
    'Zmiana nie czeka już na potwierdzenie.':
        'The change is no longer waiting for confirmation.',
    'Nie udało się wczytać różnicy.':
        'Could not load the differences.',
    'nigdy':
        'never',
    '{n} s temu':
        '{n} s ago',
    '{n} min temu':
        '{n} min ago',
    '{n} h temu':
        '{n} h ago',
    '{n} dni temu':
        '{n} days ago',
    'Skopiowano':
        'Copied',
    'ciemny':
        'dark',
    'jasny':
        'light',
    'motyw':
        'theme',
    'Język':
        'Language',
    'Peery':
        'Peers',
    'Tiery':
        'Tiers',
    'Hosty':
        'Hosts',
    'Symulator':
        'Simulator',
    'Alerty':
        'Alerts',
    'Kopie':
        'Backups',
    'Ustawienia':
        'Settings',
    'Dziennik':
        'Audit log',
    'Menu':
        'Menu',
    'Główna nawigacja':
        'Main navigation',
    'wyloguj':
        'log out',
    'Zamknij':
        'Close',
    'Błąd CSRF':
        'CSRF error',
    'Odśwież stronę i spróbuj jeszcze raz.':
        'Refresh the page and try again.',
    'Helper niedostępny':
        'Helper unavailable',
    'Błąd serwera':
        'Server error',
    'Szczegóły w journalctl -u wg-panel.':
        'Details in journalctl -u wg-panel.',
    'Nie znaleziono':
        'Not found',
    'Nie ma takiej strony.':
        'There is no such page.',
})

# --- logowanie, konto, TOTP, certyfikat ---
EN.update({
    'Przeglądarka ostrzega o certyfikacie?':
        'Does your browser warn about the certificate?',
    'Zainstaluj certyfikat panelu':
        'Install the panel certificate',
    'raz na każdym urządzeniu.':
        'once on every device.',
    'Kod TOTP':
        'TOTP code',
    'Logowanie':
        'Sign in',
    'Użytkownik':
        'Username',
    'Hasło':
        'Password',
    'Zaloguj':
        'Sign in',
    'Konfiguracja początkowa':
        'Initial setup',
    'Pierwsze uruchomienie':
        'First run',
    'Załóż konto administratora. Kod z aplikacji (TOTP) jest opcjonalny - możesz go włączyć albo wyłączyć później w Ustawieniach.':
        'Create the administrator account. An authenticator app code (TOTP) is optional - you can turn it on or off later in Settings.',
    'Powtórz hasło':
        'Repeat password',
    'Kod z aplikacji przy logowaniu (opcjonalnie)':
        'Authenticator app code at sign-in (optional)',
    'Załóż konto':
        'Create account',
    'Dodaj do aplikacji TOTP':
        'Add to your authenticator app',
    'Sekret':
        'Secret',
    'Przejdź do logowania':
        'Go to sign-in',
    'Panel działa po HTTPS. Certyfikat ważny do {date} - odnawia się sam na 30 dni przed końcem i po zmianie adresu Pi.':
        "The panel runs over HTTPS. The certificate is valid until {date} - it renews itself 30 days before it expires and whenever the Pi's address changes.",
    'HTTPS włączone, ale certyfikatu jeszcze nie ma.':
        'HTTPS is on, but there is no certificate yet.',
    'Adresy w certyfikacie':
        'Addresses in the certificate',
    'Certyfikat do zainstalowania na urządzeniach':
        'Certificate to install on your devices',
    'Odnów certyfikat teraz':
        'Renew certificate now',
    'Certyfikat panelu':
        'Panel certificate',
    'HTTPS nie jest włączone w tej instalacji panelu.':
        'HTTPS is not enabled in this panel installation.',
    'serwera w VPN':
        'of the server in the VPN',
    'To połączenie jest już szyfrowane.':
        'This connection is already encrypted.',
    'Otwórz tę stronę przez https://, żeby połączenie było szyfrowane.':
        'Open this page over https:// so the connection is encrypted.',
    'Otwórz tę stronę w Safari i pobierz certyfikat. Zatwierdź „Pozwól”.':
        'Open this page in Safari and download the certificate. Tap “Allow”.',
    'Ustawienia → Pobrany profil (na samej górze) → Zainstaluj.':
        'Settings → Profile Downloaded (at the very top) → Install.',
    'Ustawienia → Ogólne → Informacje → Ustawienia zaufania certyfikatów → włącz „wg-panel CA”. Bez tego kroku iPhone nadal ostrzega.':
        'Settings → General → About → Certificate Trust Settings → turn on “wg-panel CA”. Without this step the iPhone keeps warning.',
    'Pobierz certyfikat.':
        'Download the certificate.',
    'Ustawienia → wyszukaj „certyfikat CA” (zwykle Bezpieczeństwo → Szyfrowanie i dane logowania → Zainstaluj certyfikat → Certyfikat CA) → wybierz pobrany plik.':
        'Settings → search for “CA certificate” (usually Security → Encryption & credentials → Install a certificate → CA certificate) → pick the downloaded file.',
    'Pobierz i otwórz plik → Zainstaluj certyfikat → Bieżący użytkownik.':
        'Download and open the file → Install Certificate → Current User.',
    'Umieść w magazynie „Zaufane główne urzędy certyfikacji” → Zakończ.':
        'Place it in the “Trusted Root Certification Authorities” store → Finish.',
    'Firefox ma własny magazyn: Ustawienia → Prywatność → Certyfikaty → Wyświetl → Organy certyfikacji → Importuj.':
        'Firefox has its own store: Settings → Privacy → Certificates → View Certificates → Authorities → Import.',
    'Pobierz i otwórz plik - trafi do Pęku kluczy.':
        'Download and open the file - it goes into Keychain Access.',
    'Kliknij dwa razy „wg-panel CA” → Zaufanie → „Zawsze ufaj”.':
        'Double-click “wg-panel CA” → Trust → “Always Trust”.',
    'Żeby przeglądarka przestała ostrzegać, zainstaluj raz na każdym urządzeniu certyfikat urzędu, który wystawia certyfikat panelu.':
        "To stop the browser warnings, install the certificate authority that issues the panel's certificate - once on every device.",
    'Pobierz certyfikat':
        'Download certificate',
    'Sprawdź, czy to ten sam certyfikat':
        "Check that it's the same certificate",
    'Odcisk SHA-256 musi się zgadzać z tym, co wypisał instalator na Pi (albo polecenie {command}):':
        'The SHA-256 fingerprint must match the one printed by the installer on the Pi (or by {command}):',
    'Ten urząd może poświadczać wyłącznie: {names}. Nawet gdyby ktoś wykradł jego klucz, nie podrobi nim żadnej innej strony.':
        "This authority can only vouch for: {names}. Even if someone stole its key, they couldn't fake any other website with it.",
    'Instalacja':
        'Installation',
    'iPhone i iPad':
        'iPhone and iPad',
    'Windows (Chrome, Edge)':
        'Windows (Chrome, Edge)',
    'Po instalacji otwieraj panel przez https:// - przez VPN pod adresem {address}, w domu pod adresem Pi w sieci lokalnej.':
        "After installing, open the panel over https:// - through the VPN at {address}, at home at the Pi's local address.",
})

# --- lista peerow ---
EN.update({
    'Tier':
        'Tier',
    'Ruch':
        'Traffic',
    'Ważny do':
        'Valid until',
    'aktywny':
        'active',
    'zawieszony':
        'suspended',
    'Brak peerów. Dodaj pierwszego.':
        'No peers yet. Add the first one.',
    'Różni się: {what}.':
        'Differs: {what}.',
    'Zmiany zostały cofnięte':
        'Changes were rolled back',
    'Na interfejsie działa poprzednia konfiguracja. W panelu została nowsza wersja - ta, którą widzisz na liście poniżej.':
        'The interface is running the previous configuration. The panel still has the newer version - the one you see in the list below.',
    'Chcesz wrócić w panelu do tego, co działa: odrzuć zmiany. Chcesz jednak wprowadzić nową wersję: zastosuj ją ponownie.':
        'To go back to what is running: discard the changes. To bring in the new version after all: apply it again.',
    'Panel ma zmiany, których nie ma na interfejsie':
        'The panel has changes that are not on the interface',
    'Panel nie zna jeszcze stanu sprzed zmian - po pierwszym zastosowaniu będzie można do niego wracać.':
        "The panel doesn't know the state from before the changes yet - after the first apply you'll be able to return to it.",
    'Różnica pochodzi z Twoich zmian w panelu.':
        'The difference comes from your changes in the panel.',
    'Część różnicy nie pochodzi z Twoich zmian - pliki na dysku zapisała starsza wersja panelu. Ta część zniknie po jednym zastosowaniu.':
        'Part of the difference is not from your changes - an older panel version wrote the files on disk. That part disappears after one apply.',
    'Zastosuj ponownie':
        'Apply again',
    'Zastosuj zmiany':
        'Apply changes',
    'Co dokładnie się zmieni.':
        'What exactly will change.',
    'Po lewej stan obecny, po prawej to, co panel zapisze.':
        'Current state on the left, what the panel will write on the right.',
    '{preview}. {disk} - kto jest tylko w panelu, a kto tylko w działającej konfiguracji.':
        '{preview}. {disk} - who is only in the panel and who is only in the running configuration.',
    'Pełny podgląd zmian':
        'Full preview of changes',
    'Porównanie peerów z dyskiem':
        'Compare peers with disk',
    'Szczegóły':
        'Details',
    'wczytuję...':
        'loading...',
    'interfejs':
        'interface',
    'usługa':
        'service',
    'port':
        'port',
    'peery aktywne':
        'active peers',
    'Konfiguracja zmieniona poza panelem':
        'Configuration changed outside the panel',
    'Jeśli to nie Ty, ktoś ma dostęp do Raspberry Pi. Zobacz szczegóły i przywróć stan z panelu.':
        "If it wasn't you, someone has access to the Raspberry Pi. See the details and restore the panel's state.",
    'Ruch w tunelu - ostatnie 7 dni':
        'Tunnel traffic - last 7 days',
    'Nazwa':
        'Name',
    'Stan':
        'State',
    'Dodaj peera':
        'Add peer',
})

# --- waznosc dostepu, roznice ---
EN.update({
    'za {n} dni':
        'in {n} days',
    'dziś':
        'today',
    'minęło':
        'expired',
    '{n} dni od pierwszego połączenia (jeszcze nieużyty)':
        '{n} days from first connection (not used yet)',
    'start {start} do {end}':
        'starts {start} until {end}',
    'start {start}':
        'starts {start}',
    'od {start}':
        'from {start}',
    'bezterminowo':
        'no expiry',
    'kończy się za {n} h':
        'ends in {n} h',
    'kończy się jutro':
        'ends tomorrow',
    'kończy się za {n} dni':
        'ends in {n} days',
    'plik wg0.conf (peery i podpięcia)':
        'wg0.conf file (peers and hooks)',
    'reguły IPv4':
        'IPv4 rules',
    'reguły IPv6':
        'IPv6 rules',
})

# --- nowy peer, wykres ---
EN.update({
    'pon':
        'Mon',
    'wt':
        'Tue',
    'śr':
        'Wed',
    'czw':
        'Thu',
    'pt':
        'Fri',
    'sob':
        'Sat',
    'nd':
        'Sun',
    'Ruch godzinowy z ostatnich 7 dni':
        'Hourly traffic for the last 7 days',
    'pobrane przez klienta':
        'downloaded by the client',
    'wysłane przez klienta':
        'uploaded by the client',
    'Nowy peer':
        'New peer',
    'Urządzenie':
        'Device',
    'laptop / telefon':
        'laptop / phone',
    'Adres VPN':
        'VPN address',
    'Tryb dostępu':
        'Access mode',
    'okno terminowe (od - do)':
        'time window (from - to)',
    'N dni od pierwszego połączenia':
        'N days from first connection',
    'Od dnia':
        'From',
    'puste = od zaraz':
        'empty = starting now',
    'Do dnia':
        'Until',
    'puste = bez końca':
        'empty = no end',
    'Ile dni':
        'Number of days',
    'np. 7':
        'e.g. 7',
    'Notatka':
        'Note',
    'Utwórz':
        'Create',
})

# --- strona peera ---
EN.update({
    'Usunąć regułę?':
        'Delete this rule?',
    '{action} do {target}. Zmiana zadziała po zastosowaniu.':
        '{action} to {target}. The change takes effect after applying.',
    'Usuń regułę':
        'Delete rule',
    'usuń':
        'delete',
    'Brak reguł własnych - obowiązuje sam tier.':
        'No custom rules - only the tier applies.',
    'pierwsze połączenie {date}':
        'first connection {date}',
    'Wznów':
        'Resume',
    'Zawieś':
        'Suspend',
    'Skasować klucz prywatny?':
        'Delete the private key?',
    'Panel nie wygeneruje już dla tej osoby pliku ani kodu QR. Nowy plik będzie możliwy tylko przez rotację kluczy.':
        'The panel will no longer generate a file or QR code for this person. A new file will only be possible through key rotation.',
    'Skasuj klucz':
        'Delete key',
    'Wymienić klucze: {name}?':
        'Replace keys: {name}?',
    'Panel wygeneruje nową parę kluczy. Po zastosowaniu zmian obecny plik tej osoby przestanie działać - trzeba jej przekazać nowy.':
        "The panel will generate a new key pair. After applying, this person's current file stops working - they need a new one.",
    'Wymień klucze':
        'Replace keys',
    'Usunąć {name}?':
        'Delete {name}?',
    'Plik tej osoby przestanie działać po zastosowaniu zmian, a adres {ip} wróci do puli wolnych.':
        "This person's file stops working after applying, and the address {ip} goes back to the free pool.",
    'Usuń peera':
        'Delete peer',
    'Pokaż plik konfiguracyjny':
        'Show configuration file',
    'Kopiuj plik':
        'Copy file',
    'Pobierz .conf':
        'Download .conf',
    'Kopiuj - wklejasz tekst np. w wiadomości; osoba w aplikacji WireGuard wybiera „Dodaj tunel → Utwórz od zera” i wkleja. Pobierz - plik do zaimportowania. Kod QR - osoba skanuje go telefonem.':
        'Copy - paste the text e.g. into a message; in the WireGuard app the person picks “Add tunnel → Create from scratch” and pastes it. Download - a file to import. QR code - the person scans it with their phone.',
    'Wygeneruj link jednorazowy':
        'Generate one-time link',
    'Link otwiera się tylko przez VPN - przydaje się przy wymianie pliku komuś, kto już jest połączony (np. po rotacji kluczy). Nowej osobie wyślij plik albo pokaż kod QR.':
        'The link only opens through the VPN - useful for replacing the file for someone already connected (e.g. after key rotation). For a new person, send the file or show the QR code.',
    'Klucz prywatny jest jeszcze w bazie. Po przekazaniu konfiguracji użyj „Wydano - skasuj klucz”, żeby zniknął z serwera.':
        'The private key is still in the database. After handing over the configuration, use “Handed over - delete key” to remove it from the server.',
    'Wydano - skasuj klucz':
        'Handed over - delete key',
    'Panel nie ma klucza prywatnego tej osoby, więc nie zrobi jej kodu QR ani pliku. Tak jest przy osobach przejętych ze starej konfiguracji i po użyciu „Wydano - skasuj klucz”.':
        "The panel doesn't have this person's private key, so it can't make a QR code or file. That's the case for people imported from the old configuration and after using “Handed over - delete key”.",
    'Żeby wydać nowy plik, zrób {rotation}: panel wygeneruje nową parę kluczy i klucz wspólny (PSK), pokaże kod QR i plik do pobrania. Stary plik tej osoby przestanie działać po zastosowaniu zmian.':
        "To issue a new file, do a {rotation}: the panel generates a new key pair and a pre-shared key (PSK), and shows a QR code and a file to download. This person's old file stops working after applying.",
    'rotację kluczy':
        'key rotation',
    'Rotacja kluczy i nowy plik':
        'Key rotation and new file',
    'Link jednorazowy (wygasa za 24 h, działa raz):':
        'One-time link (expires in 24 h, works once):',
    '{stale} - nie ma w nim: {missing}, więc tam nie dojdzie. Wyślij nowy plik albo niech w aplikacji WireGuard zmieni linię AllowedIPs na powyższą. Po wysłaniu kliknij „Oznacz jako wysłany”.':
        "{stale} - it lacks: {missing}, so traffic there won't arrive. Send a new file, or have the person change the AllowedIPs line in the WireGuard app to the one above. After sending, click “Mark as sent”.",
    'Plik u tej osoby jest nieaktualny':
        "This person's file is out of date",
    'Plik u tej osoby kieruje do VPN więcej niż trzeba ({issued}). Działa, ale możesz wysłać nowy.':
        "This person's file routes more into the VPN than needed ({issued}). It works, but you can send a new one.",
    'Panel nie wie, jaki plik ma ta osoba (np. przejęta z poprzedniej konfiguracji). Jeśli jej obecny plik działa - nic nie trzeba robić.':
        "The panel doesn't know which file this person has (e.g. imported from the previous configuration). If their current file works, there's nothing to do.",
    'Plik u tej osoby jest aktualny.':
        "This person's file is up to date.",
    'Oznacz jako wysłany':
        'Mark as sent',
    'Dodaj kolejnego peera':
        'Add another peer',
    'adres IPv6':
        'IPv6 address',
    'Brak danych. Próbki zbierane są co kilka minut, wykres pojawi się po pierwszej godzinie ruchu.':
        'No data yet. Samples are collected every few minutes; the chart appears after the first hour of traffic.',
    'ustawione u tej osoby':
        'set for this person',
    'z tieru {tier}':
        'from tier {tier}',
    'Lista peerów':
        'Peer list',
    'Rotacja kluczy':
        'Key rotation',
    'Usuń':
        'Delete',
    'adres':
        'address',
    'stan':
        'state',
    'ważny do':
        'valid until',
    'reguły i ustawienia tego tieru':
        'rules and settings of this tier',
    'Reguły własne':
        'Custom rules',
    'Pokaż / ukryj':
        'Show / hide',
    'Sprawdzane {before} regułami tieru, w kolejności od góry - dzięki temu wyjątek działa mimo blokady z tieru. ACCEPT wpuszcza, DROP blokuje; pierwsza pasująca wygrywa.':
        'Checked {before} the tier rules, top to bottom - so an exception works despite a block from the tier. ACCEPT allows, DROP blocks; the first match wins.',
    'przed':
        'before',
    'Akcja':
        'Action',
    'Cel':
        'Target',
    'Okno':
        'Window',
    'Komentarz':
        'Comment',
    'Proto':
        'Proto',
    'Porty':
        'Ports',
    'Od':
        'From',
    'Do':
        'To',
    'Dni':
        'Days',
    'Dodaj regułę':
        'Add rule',
    'Ruch - ostatnie 7 dni':
        'Traffic - last 7 days',
    'Co idzie przez VPN':
        'What goes through the VPN',
    'Decyduje o tym linia {line} w pliku osoby. Reszta ruchu idzie przez jej własny internet. Uprawnienia i tak pilnuje serwer - przerobiony plik niczego nie otworzy.':
        "The {line} line in the person's file decides this. All other traffic goes through their own internet. The server enforces permissions anyway - an edited file won't open anything.",
    'Kopiuj linię AllowedIPs':
        'Copy the AllowedIPs line',
    'Tryb':
        'Mode',
    'jak w tierze':
        'as in the tier',
    'Własna lista':
        'Custom list',
    'Konfiguracja klienta':
        'Client configuration',
    'Niezapisane zmiany':
        'Unsaved changes',
    'Masz niezapisane zmiany.':
        'You have unsaved changes.',
    'Zapisz je, zanim wyjdziesz z tej strony.':
        'Save them before you leave this page.',
    'Porzuć zmiany':
        'Discard edits',
    'Zapisz zmiany':
        'Save changes',
    'Cały ruch przez VPN':
        'All traffic through the VPN',
    'Tylko to, do czego ma dostęp':
        'Only what they have access to',
    'Cała sieć domowa':
        'The whole home network',
    '{item}: ANY oznacza cały ruch - użyj trybu „Cały ruch przez VPN”':
        '{item}: ANY means all traffic - use the “All traffic through the VPN” mode',
    'reguła ACCEPT do ANY - tego nie da się zawęzić, dodano cały LAN':
        "ACCEPT rule to ANY - this can't be narrowed, the whole LAN was added",
    'pusta lista - dodano sam adres serwera, żeby tunel miał sens':
        'empty list - only the server address was added so the tunnel makes sense',
})

# --- komunikaty: logowanie, peerzy, zastosowanie, potwierdzanie ---
EN.update({
    'Zmiany cofnęły się automatycznie - nie było potwierdzenia na czas. Na interfejsie działa poprzednia konfiguracja, a Twoje zmiany czekają w panelu jako niezastosowane.':
        'The changes rolled back automatically - there was no confirmation in time. The interface is running the previous configuration, and your changes are waiting in the panel as unapplied.',
    'Helper nie ma już informacji o tej zmianie (np. uszkodzony plik stanu), więc nie wiadomo, czy została cofnięta. Sprawdź, czy wszystko działa - w razie potrzeby użyj Ustawienia → Cofnij do ostatniej kopii.':
        "The helper no longer has information about this change (e.g. a damaged state file), so it's unknown whether it was rolled back. Check that everything works - if needed, use Settings → Roll back to the last backup.",
    'Za dużo prób. Odczekaj 5 minut.':
        'Too many attempts. Wait 5 minutes.',
    'Złe dane logowania.':
        'Wrong sign-in details.',
    'Hasło musi mieć co najmniej 10 znaków.':
        'The password must be at least 10 characters.',
    'Hasła się różnią.':
        "The passwords don't match.",
    'Nazwa jest wymagana.':
        'A name is required.',
    'Nie udało się dodać: {error}':
        'Could not add: {error}',
    'Peer utworzony. Dodaj kolejnych, jeśli trzeba - wszystkie zmiany zastosujesz raz, z listy peerów.':
        "Peer created. Add more if you need to - you'll apply all changes once, from the peer list.",
    'Nie ma takiego peera':
        'No such peer',
    'Sprawdź adres.':
        'Check the address.',
    'kod QR z konfiguracją':
        'QR code with the configuration',
    'Oznaczono jako wysłany.':
        'Marked as sent.',
    'Nie ma takiego peera.':
        'No such peer.',
    'Nazwa nie może być pusta.':
        "The name can't be empty.",
    'Nic się nie zmieniło.':
        'Nothing changed.',
    'Nazwa {name} jest już zajęta.':
        'The name {name} is already taken.',
    'Zapisano. To zmienia tylko plik klienta - serwera nie trzeba stosować. Jeśli pojawi się ostrzeżenie o nieaktualnym pliku, wyślij nowy.':
        'Saved. This only changes the client file - nothing to apply on the server. If a warning about an out-of-date file appears, send a new one.',
    'Zapisano. Plik klienta tej osoby się zmienił - przekaż jej nowy.':
        "Saved. This person's client file changed - send them the new one.",
    'Zapisano.':
        'Saved.',
    'Stan zmieniony na: {state}.':
        'State changed to: {state}.',
    'Usunięto {name} ({ip}). Zastosuj zmiany, żeby stracił dostęp. Jego adres jest znowu wolny.':
        'Deleted {name} ({ip}). Apply the changes to revoke their access. The address is free again.',
    'Nowe klucze. Przekaż konfigurację i zastosuj zmiany.':
        'New keys. Hand over the configuration and apply the changes.',
    'Klucz prywatny skasowany z serwera.':
        'Private key deleted from the server.',
    'Brak klucza':
        'No key',
    'Klucz prywatny został już skasowany.':
        'The private key has already been deleted.',
    'Link nieaktualny':
        'Link expired',
    'Został już użyty albo wygasł. Poproś o nowy.':
        'It was already used or has expired. Ask for a new one.',
    'Brak konfiguracji':
        'No configuration',
    'Klucz prywatny został skasowany.':
        'The private key has been deleted.',
    'Reguła odrzucona: {error}':
        'Rule rejected: {error}',
    'Reguła dodana.':
        'Rule added.',
    'Reguła usunięta.':
        'Rule deleted.',
    'Poprzednia zmiana wciąż czeka na potwierdzenie - potwierdź ją albo cofnij na pasku u góry.':
        'The previous change is still waiting for confirmation - confirm or undo it in the bar at the top.',
    'Zastosowanie nieudane: {error}':
        'Apply failed: {error}',
    'Nic nie czeka na potwierdzenie.':
        'Nothing is waiting for confirmation.',
    'Nie potwierdzono: {error}':
        'Not confirmed: {error}',
    'Zastosowano zmiany.':
        'Changes applied.',
    'Za późno - zmiany zostały już cofnięte. Możesz je zastosować ponownie z listy peerów.':
        'Too late - the changes were already rolled back. You can apply them again from the peer list.',
    'Potwierdzone - zmiany zostają na stałe.':
        'Confirmed - the changes are permanent.',
    'Najpierw potwierdź albo cofnij zmianę czekającą na pasku u góry - dopiero potem można odrzucać zmiany w panelu.':
        'First confirm or undo the change waiting in the bar at the top - only then can you discard changes in the panel.',
    'Nie mogę odrzucić zmian: {error}':
        "Can't discard the changes: {error}",
    'Odrzucono zmiany w panelu - wrócił stan z ostatniego zastosowania ({peers} peerów, {tiers} tierów).':
        'Discarded the panel changes - back to the state from the last apply ({peers} peers, {tiers} tiers).',
    'Nie cofnięto: {error}':
        'Not rolled back: {error}',
    'Cofnięto zmianę - na interfejsie działa poprzednia konfiguracja. Twoje zmiany zostały w panelu jako niezastosowane.':
        'Change undone - the interface is running the previous configuration. Your changes stay in the panel as unapplied.',
    'Cofnięto do ostatniej kopii konfiguracji.':
        'Rolled back to the last configuration backup.',
})

# --- opisy wbudowanych tierow ---
EN.update({
    'Pełny dostęp do LAN, SSH/RDP i panel na serwerze, cały ruch przez VPN.':
        'Full access to the LAN, SSH/RDP and the panel on the server, all traffic through the VPN.',
    'Internet przez VPN, LAN całkowicie zablokowany.':
        'Internet through the VPN, LAN fully blocked.',
    'Tylko NAS na portach 80/443/445, split tunnel.':
        'Only the NAS on ports 80/443/445, split tunnel.',
})

# --- podglad zmian, tiery ---
EN.update({
    'Poprzednia zmiana czeka na potwierdzenie - potwierdź ją albo cofnij na pasku u góry, zanim zastosujesz kolejną.':
        'The previous change is still waiting for confirmation - confirm or undo it in the bar at the top before applying another one.',
    'Wróć do listy peerów':
        'Back to the peer list',
    'Po zastosowaniu masz {seconds}&nbsp;s na potwierdzenie z działającego połączenia. Bez potwierdzenia panel sam cofnie zmiany.':
        'After applying you have {seconds}&nbsp;s to confirm from a working connection. Without confirmation the panel rolls the changes back itself.',
    'Podgląd zmian':
        'Preview of changes',
    'Reguły':
        'Rules',
    'Pełny zestaw reguł':
        'Full rule set',
    'Usunąć regułę z tieru {tier}?':
        'Delete a rule from tier {tier}?',
    '{action} do {target}. Dotyczy wszystkich osób w tym tierze. Zmiana zadziała po zastosowaniu.':
        '{action} to {target}. Affects everyone in this tier. The change takes effect after applying.',
    'Brak reguł - peerzy tego tieru mają pełny dostęp.':
        'No rules - peers in this tier have full access.',
    'wbudowany':
        'built-in',
    'Usunąć tier {tier}?':
        'Delete tier {tier}?',
    'Tier da się usunąć tylko wtedy, gdy nikt nie jest do niego przypisany.':
        'A tier can only be deleted when nobody is assigned to it.',
    'Usuń tier':
        'Delete tier',
    'usuń tier':
        'delete tier',
    'Opis':
        'Description',
    'Porty na samym Pi':
        'Ports on the Pi itself',
    '22 SSH, 3389 RDP, 8080 panel':
        '22 SSH, 3389 RDP, 8080 panel',
    'Zapisz':
        'Save',
    'Tier to nazwany zestaw reguł. Zmiana tieru przestawia wszystkich przypisanych do niego peerów naraz. Reguły sprawdzane są od góry, pierwsza pasująca wygrywa. W polu {target} wpisz nazwę z katalogu hostów ({nas}), słowo {lan} (cała sieć domowa), {any} (wszystko) albo adres. Każda zmiana działa po zastosowaniu.':
        'A tier is a named set of rules. Changing a tier switches all peers assigned to it at once. Rules are checked top to bottom, the first match wins. In the {target} field enter a name from the host catalog ({nas}), the word {lan} (the whole home network), {any} (everything) or an address. Every change takes effect after applying.',
    'Dodaj tier':
        'Add tier',
    'Reguły dodasz przy tierze, gdy już powstanie.':
        "You'll add rules to the tier once it exists.",
})

# --- ustawienia ---
EN.update({
    'Podsieć LAN':
        'LAN subnet',
    'Podsieć VPN':
        'VPN subnet',
    'Adres serwera w VPN':
        'Server address in the VPN',
    'Adres Pi w LAN':
        'Pi address in the LAN',
    'Interfejs WAN':
        'WAN interface',
    'Port nasłuchu':
        'Listen port',
    'Endpoint klientów':
        'Client endpoint',
    'DNS trybu full':
        'DNS for full mode',
    'Interfejs WireGuard':
        'WireGuard interface',
    'Nazwa łańcucha':
        'Chain name',
    'Czas na potwierdzenie [s, 30-900]':
        'Time to confirm [s, 30-900]',
    'MTU klienta':
        'Client MTU',
    'Port panelu':
        'Panel port',
    'Ostrzeżenie [dni]':
        'Warning [days]',
    'Dziennik [dni]':
        'Audit log [days]',
    'Anomalia [x mediana]':
        'Anomaly [x median]',
    'Anomalia [MB]':
        'Anomaly [MB]',
    'DDNS albo publiczny IP z portem':
        'DDNS or public IP with port',
    'adres Pi w sieci domowej':
        "the Pi's address in the home network",
    'trafia do plików z całym ruchem':
        'goes into full-tunnel files',
    'puste = domyślne':
        'empty = default',
    'potem czyszczone':
        'then cleared',
    'ile razy więcej niż zwykle':
        'how many times more than usual',
    'Potwierdzanie zmian':
        'Confirming changes',
    'automatycznie':
        'automatically',
    'ręcznie':
        'manually',
    'auto: panel sam sprawdza, czy masz połączenie':
        "auto: the panel checks by itself that you're still connected",
    'włączony':
        'on',
    'wyłączony':
        'off',
    'Cofnąć do ostatniej kopii?':
        'Roll back to the last backup?',
    'Pliki wg0.conf i reguły wrócą do wersji sprzed ostatniego zastosowania i od razu trafią na interfejs. Zmiany w panelu zostają - pojawią się jako niezastosowane.':
        "The wg0.conf and rule files go back to the version from before the last apply and go onto the interface right away. Panel changes stay - they'll show up as unapplied.",
    'Cofnij konfigurację':
        'Roll back configuration',
    'Zrestartować wg-quick?':
        'Restart wg-quick?',
    'Wszystkie tunele zerwą się na chwilę. Jeśli łączysz się przez VPN, panel też na moment straci połączenie.':
        "All tunnels drop for a moment. If you're connected through the VPN, the panel loses its connection briefly too.",
    'Zrestartuj':
        'Restart',
    'Po włączeniu każdy peer dostaje adres IPv6 odpowiadający IPv4 (10.0.0.4 → fd42:42:42::4), a reguły działają w obu rodzinach naraz - blokada LAN obejmuje też prefiks IPv6 sieci domowej. Peerom trzeba potem wydać nowe pliki konfiguracyjne.':
        "Once on, every peer gets an IPv6 address matching its IPv4 one (10.0.0.4 → fd42:42:42::4), and rules work in both families at once - the LAN block also covers the home network's IPv6 prefix. Peers then need new configuration files.",
    'Adres NAS i innych urządzeń ustawiasz w zakładce {hosts} - stąd zniknął, żeby nie było dwóch miejsc na to samo.':
        "You set the address of the NAS and other devices in the {hosts} tab - it was removed from here so there aren't two places for the same thing.",
    'Klucz publiczny serwera':
        'Server public key',
    'niedostępny':
        'unavailable',
    'Zaawansowane':
        'Advanced',
    'Włącz IPv6 w tunelu':
        'Enable IPv6 in the tunnel',
    'Podsieć VPN IPv6':
        'IPv6 VPN subnet',
    'Adres serwera':
        'Server address',
    'Prefiksy LAN IPv6':
        'IPv6 LAN prefixes',
    'NAT66 dla trybu pełnego':
        'NAT66 for full mode',
    'Wykryj prefiks LAN':
        'Detect LAN prefix',
    'Konto: {user}':
        'Account: {user}',
    'Kod z aplikacji przy logowaniu: {state}.':
        'Authenticator code at sign-in: {state}.',
    'Zmiana hasła':
        'Change password',
    'Obecne hasło':
        'Current password',
    'Nowe hasło':
        'New password',
    'Powtórz nowe':
        'Repeat new',
    'Zmień hasło':
        'Change password',
    'Wyłączenie kodu z aplikacji':
        'Turning off the authenticator code',
    'Włączenie kodu z aplikacji':
        'Turning on the authenticator code',
    'Po wyłączeniu logujesz się samym hasłem.':
        'Once off, you sign in with just your password.',
    'Dodatkowy kod z aplikacji na telefonie przy każdym logowaniu.':
        'An extra code from an app on your phone at every sign-in.',
    'Potwierdź obecnym hasłem':
        'Confirm with your current password',
    'Wyłącz kod':
        'Turn off code',
    'Włącz kod':
        'Turn on code',
    'Integralność':
        'Integrity',
    'Panel co 5 minut porównuje to, co zastosował, z tym, co jest na dysku i w jądrze: plik konfiguracji, reguły, listę peerów na interfejsie i to, czy nikt nie wstawił reguły przed łańcuchem panelu.':
        "Every 5 minutes the panel compares what it applied with what is on disk and in the kernel: the configuration file, the rules, the peer list on the interface, and whether anyone inserted a rule ahead of the panel's chain.",
    'Stan integralności':
        'Integrity status',
    'Operacje awaryjne':
        'Emergency operations',
    'Cofnij do ostatniej kopii':
        'Roll back to the last backup',
    'Restart wg-quick':
        'Restart wg-quick',
    'Restart zrywa wszystkie tunele na ułamek sekundy. Jeśli łączysz się przez VPN, sesja panelu też się na moment urwie.':
        "A restart drops all tunnels for a split second. If you're connected through the VPN, the panel session will also drop briefly.",
})

# --- dziennik, link jednorazowy, symulator ---
EN.update({
    'pusto':
        'empty',
    '{n} wpisów · trzymane {days} dni':
        '{n} entries · kept for {days} days',
    'Kiedy':
        'When',
    'Kto':
        'Who',
    'Co':
        'What',
    'Konfiguracja':
        'Configuration',
    'Ten link działa tylko raz. Zeskanuj kod albo pobierz plik teraz.':
        'This link works only once. Scan the code or download the file now.',
    'Pokaż tekstem':
        'Show as text',
    'BRAK':
        'NONE',
    '← tu się zatrzymuje':
        '← stops here',
    'pozycja {n}':
        'position {n}',
    'Werdykt':
        'Verdict',
    'łańcuch':
        'chain',
    'Reguła':
        'Rule',
    'Symulator reguł':
        'Rule simulator',
    'Sprawdza, która reguła zadecyduje o konkretnym połączeniu - bez ruszania zapory. Liczy na tej konfiguracji, która jest w bazie, więc zadziała także przed zastosowaniem zmian.':
        'Checks which rule will decide a specific connection - without touching the firewall. It works on the configuration in the database, so it works even before the changes are applied.',
    'Od kogo':
        'From',
    'Do adresu':
        'To address',
    'O godzinie':
        'At time',
    'teraz albo 2026-03-14 09:30':
        'now or 2026-03-14 09:30',
    'Sprawdź':
        'Check',
})

# --- symulator - werdykty ---
EN.update({
    '{address} to nie jest adres IP':
        '{address} is not an IP address',
    'IPv6 jest wyłączone - tunel nie przenosi IPv6':
        "IPv6 is off - the tunnel doesn't carry IPv6",
    'nie ma peera o adresie {address}':
        'there is no peer with address {address}',
    'peer jest zawieszony - nie ma go w konfiguracji interfejsu':
        "the peer is suspended - it isn't in the interface configuration",
    'peer nie ma adresu IPv6':
        'the peer has no IPv6 address',
    'łańcuch hosta filtruje tylko TCP':
        'the host chain filters TCP only',
    'port {port} jest na liście dostępu do hosta dla tego peera':
        "port {port} is on this peer's host access list",
    'port {port} jest zarządzany, a ten peer nie ma do niego prawa':
        'port {port} is managed and this peer has no right to it',
    'port {port} nie jest zarządzany przez panel - decyduje ufw':
        "port {port} isn't managed by the panel - ufw decides",
    'peery nie widzą się nawzajem':
        "peers can't see each other",
    'reguła własna: {rule}':
        'custom rule: {rule}',
    'reguła tieru: {rule}':
        'tier rule: {rule}',
    'żaden wyjątek ani blokada nie pasuje - peer ma pełny dostęp':
        'no exception or block matches - the peer has full access',
})

# --- alerty ---
EN.update({
    'wysłany':
        'sent',
    'tylko w panelu':
        'only in the panel',
    'Usunąć ten alert?':
        'Delete this alert?',
    'Usuń alert':
        'Delete alert',
    'Nic się nie działo.':
        'Nothing has happened.',
    'Usunąć wszystkie alerty?':
        'Delete all alerts?',
    'Lista alertów w panelu zostanie wyczyszczona. Powiadomienia już wysłane zostają tam, gdzie trafiły.':
        'The alert list in the panel will be cleared. Notifications already sent stay where they went.',
    'Usuń wszystkie':
        'Delete all',
    'Rodzaj':
        'Type',
    'Treść':
        'Message',
    'Kanał':
        'Channel',
    'Oznacz jako przeczytane':
        'Mark as read',
    'Ustawienia powiadomień':
        'Notification settings',
    'Najważniejszy alert to {alert} - ten sam klucz łączy się z nowego adresu. To pierwszy sygnał, że plik konfiguracyjny wyciekł.':
        "The most important alert is {alert} - the same key connecting from a new address. It's the first sign that a configuration file has leaked.",
    'wyłączone':
        'off',
    'Adres':
        'Address',
    'Cisza peera [h]':
        'Peer silence [h]',
    '0 = nie pilnuj':
        "0 = don't watch",
    'Odstęp powtórek [min]':
        'Repeat interval [min]',
    'Zmiana adresu, z którego łączy się peer':
        'Change of the address a peer connects from',
    'Wyślij testowy alert':
        'Send a test alert',
})

# --- kopie zapasowe ---
EN.update({
    'puste = bez szyfrowania':
        'empty = no encryption',
    'błąd':
        'error',
    'zaszyfrowana':
        'encrypted',
    'jawna':
        'plain',
    'zgodna':
        'matches',
    'NIEZGODNA':
        'MISMATCH',
    'niesprawdzona':
        'not checked',
    'Jeszcze żadnej kopii.':
        'No backups yet.',
    'karta SD':
        'SD card',
    'stan sprzed odtworzenia':
        'state before restore',
    'plik na karcie':
        'file on the card',
    'na NAS':
        'on the NAS',
    'brak kopii':
        'no backups',
    'Odtworzyć kopię?':
        'Restore this backup?',
    'Odtworzenie zastąpi wszystkie peery, tiery i reguły w panelu. Obecny stan zostanie najpierw zapisany jako osobna kopia.':
        'Restoring replaces all peers, tiers and rules in the panel. The current state is saved as a separate backup first.',
    'Odtwórz kopię':
        'Restore backup',
    'znaleziono {n}, są na liście poniżej':
        "found {n}, they're in the list below",
    'przydaje się na nowym systemie, który nie ma jeszcze własnej historii':
        'useful on a new system that has no history of its own yet',
    'Wynik':
        'Result',
    'Plik':
        'File',
    'Rozmiar':
        'Size',
    'Weryfikacja':
        'Verification',
    'Kopie zapasowe':
        'Backups',
    'Sprawdź kopie':
        'Verify backups',
    'Każda kopia dostaje sumę SHA-256 zapisaną w bazie i w pliku {file} obok kopii - da się ją sprawdzić także bez panelu: {command}. Weryfikacja pobiera plik z miejsca, w którym leży (także z NAS), i porównuje sumę.':
        'Every backup gets a SHA-256 checksum stored in the database and in a {file} file next to the backup - you can check it without the panel too: {command}. Verification fetches the file from where it is (the NAS too) and compares the checksum.',
    'Kopia to cały stan panelu: ustawienia, tiery, peery, reguły i konta. Z niej odtworzysz panel na czystej karcie - {conf} i reguły iptables zostaną wygenerowane od nowa.':
        "A backup is the panel's entire state: settings, tiers, peers, rules and accounts. From it you can restore the panel on a clean card - {conf} and the iptables rules are generated from scratch.",
    'Harmonogram i cel':
        'Schedule and destination',
    'Rób kopie automatycznie, codziennie':
        'Back up automatically, every day',
    'Trzymaj ostatnie':
        'Keep the last',
    'katalog na dysku':
        'directory on disk',
    'NAS przez SMB':
        'NAS over SMB',
    'Katalog roboczy':
        'Working directory',
    'Hasło kopii':
        'Backup password',
    'NAS: adres':
        'NAS: address',
    'Udział':
        'Share',
    'Katalog na udziale':
        'Directory on the share',
    'Kopia zawiera klucze PSK wszystkich peerów. Jeśli ma leżeć na udziale sieciowym, ustaw hasło - plik zostanie zaszyfrowany (ChaCha20 + HMAC, klucz z scrypt). Bez hasła trafi tam jawnym tekstem.':
        "A backup contains every peer's PSK. If it's going to sit on a network share, set a password - the file will be encrypted (ChaCha20 + HMAC, key from scrypt). Without a password it goes there as plain text.",
    'Pobierz albo odtwórz':
        'Download or restore',
    'Hasło do pobieranego pliku':
        'Password for the downloaded file',
    'Pobierz kopię':
        'Download backup',
    'Kopia':
        'Backup',
    'Odtwórz także konta':
        'Restore accounts too',
    'Odtwórz':
        'Restore',
    'Pokaż też kopie leżące na NAS':
        'Also show backups stored on the NAS',
    'Kopie z NAS panel pobiera sam i przed odtworzeniem sprawdza ich sumę kontrolną - niezgodnej nie odtworzy. Tuż przed odtworzeniem zapisuje obecny stan jako osobną kopię, więc zawsze da się wrócić.':
        "The panel fetches NAS backups itself and checks their checksum before restoring - it won't restore a mismatching one. Right before restoring it saves the current state as a separate backup, so you can always go back.",
    'Odtworzenie zmienia tylko bazę. Żeby trafiło na interfejs, zastosuj potem zmiany z listy peerów.':
        'Restoring changes only the database. To get it onto the interface, apply the changes from the peer list afterwards.',
})

# --- komunikaty helpera ---
EN.update({
    'nic nie czeka na potwierdzenie':
        'nothing is waiting for confirmation',
    'nic nie czeka na potwierdzenie - nic nie cofnięto':
        'nothing is waiting for confirmation - nothing was rolled back',
    'za późno - brak potwierdzenia na czas, zmiany cofnęły się automatycznie':
        'too late - no confirmation in time, the changes rolled back automatically',
    'zmiany już cofnęły się automatycznie - nic więcej nie cofam':
        'the changes already rolled back automatically - nothing more to undo',
    'zły token potwierdzenia':
        'wrong confirmation token',
    'trwa już zmiana czekająca na potwierdzenie':
        'a change is already waiting for confirmation',
    'brak programu openssl - zainstaluj: sudo apt install openssl':
        'openssl is missing - install it: sudo apt install openssl',
    '{path} zmieniony poza panelem':
        '{path} changed outside the panel',
    'plik reguł IPv4 zmieniony poza panelem':
        'IPv4 rules file changed outside the panel',
    'plik reguł IPv6 zmieniony poza panelem':
        'IPv6 rules file changed outside the panel',
    '{path} ma prawa {mode} - czytelne dla innych kont':
        '{path} has mode {mode} - readable by other accounts',
    'na interfejsie są peery spoza panelu: {n} (np. {key})':
        'the interface has peers from outside the panel: {n} (e.g. {key})',
    'brakuje {n} peerów, które panel zastosował':
        '{n} peers the panel applied are missing',
    '{tool}: brak łańcucha {chain}':
        '{tool}: chain {chain} is missing',
    '{tool}: {chain} ma {have} reguł zamiast {want}':
        '{tool}: {chain} has {have} rules instead of {want}',
    '{tool}: pierwsza reguła FORWARD to nie skok do {chain} - coś zostało wstawione przed panelem':
        '{tool}: the first FORWARD rule is not a jump to {chain} - something was inserted ahead of the panel',
})

# --- hosty, integralnosc, dysk ---
EN.update({
    'Adres IPv4':
        'IPv4 address',
    'Adres IPv6':
        'IPv6 address',
    'opcjonalnie':
        'optional',
    '{n} reguł':
        '{n} rules',
    'nieużywany':
        'unused',
    'Używany w: {rules}':
        'Used in: {rules}',
    'Usunąć host {name}?':
        'Delete host {name}?',
    'Panel odmówi, jeśli host jest używany w regułach.':
        'The panel will refuse if the host is used in rules.',
    'Usuń host':
        'Delete host',
    'IPv6 wyłączone':
        'IPv6 off',
    'Katalog jest pusty.':
        'The catalog is empty.',
    'W regułach wpisujesz nazwę zamiast adresu - {nas}, {printer}. Zmiana adresu tutaj przestawia wszystkie reguły, które tej nazwy używają. Zastosuj zmiany, żeby zadziałało.':
        'In rules you type a name instead of an address - {nas}, {printer}. Changing the address here updates every rule that uses that name. Apply the changes for it to take effect.',
    'Hosty w sieci domowej':
        'Hosts in the home network',
    'Dodaj host':
        'Add host',
    'Brother w biurze':
        'Brother in the office',
    'Dodaj':
        'Add',
    'jeszcze nie sprawdzano':
        'not checked yet',
    'Nic jeszcze nie zostało zastosowane z panelu, więc nie ma wzorca do porównania. Zastosuj zmiany raz.':
        "Nothing has been applied from the panel yet, so there's no baseline to compare against. Apply the changes once.",
    'Wykryto rozbieżności: {n}':
        'Discrepancies found: {n}',
    'Przywrócenie zastosuje ponownie konfigurację z bazy - z bezpiecznikiem, więc jeśli coś pójdzie nie tak, wróci sama.':
        'Restoring re-applies the configuration from the database - with the safety net, so if something goes wrong it reverts by itself.',
    'Przywróć stan z panelu':
        "Restore the panel's state",
    'Stan zgodny z tym, co zastosował panel.':
        'The state matches what the panel applied.',
    'plik {conf} i pliki reguł':
        'the {conf} file and the rule files',
    'SHA-256 w porównaniu z tym, co zapisał helper':
        'SHA-256 compared with what the helper wrote',
    'prawa dostępu do plików':
        'file permissions',
    'czy klucze nie stały się czytelne dla innych kont':
        'whether keys became readable by other accounts',
    'peery na interfejsie':
        'peers on the interface',
    'czy nikt nie dodał klucza przez {cmd}':
        'whether anyone added a key with {cmd}',
    'liczba reguł w łańcuchach':
        'number of rules in the chains',
    'czy nikt nie dopisał reguł do łańcuchów panelu':
        "whether anyone added rules to the panel's chains",
    'pierwsza reguła FORWARD':
        'first FORWARD rule',
    'czy nikt nie wstawił ACCEPT przed łańcuchem panelu':
        "whether anyone inserted an ACCEPT ahead of the panel's chain",
    'Integralność konfiguracji':
        'Configuration integrity',
    'Sprawdź teraz':
        'Check now',
    'Ostatnie sprawdzenie: {when}':
        'Last check: {when}',
    'Co jest sprawdzane':
        'What is checked',
    'Gdy panel ma niezastosowane zmiany, różnice są oczekiwane - panel je pokazuje, ale nie zasypuje Cię alarmami. Alarmy wracają po zastosowaniu zmian.':
        "When the panel has unapplied changes, differences are expected - the panel shows them but doesn't flood you with alarms. Alarms come back after the changes are applied.",
    'Wykrywanie działa przeciw zmianom robionym ręcznie albo przez kogoś, kto nie wie o panelu. Przeciw komuś, kto ma roota i wie, co robi, nie ochroni żadne oprogramowanie na tej samej maszynie - on może wyłączyć także sam panel.':
        "Detection works against changes made by hand or by someone who doesn't know about the panel. No software on the same machine can protect against someone with root who knows what they're doing - they can switch off the panel itself too.",
    'zgodne':
        'in sync',
    'tylko na dysku':
        'only on disk',
    'nic do zrobienia':
        'nothing to do',
    'dodaj do panelu':
        'add to the panel',
    'zniknie z dysku po zastosowaniu':
        'will disappear from disk after applying',
    'Brak peerów po obu stronach.':
        'No peers on either side.',
    'Różniące się pliki: {files}.':
        'Files that differ: {files}.',
    'Pliki na dysku zgadzają się z panelem.':
        'The files on disk match the panel.',
    'Dodaj zaznaczonych do panelu':
        'Add the selected ones to the panel',
    'Klucz':
        'Key',
    'Co zrobić':
        'What to do',
    'Porównanie z konfiguracją na dysku':
        'Comparison with the configuration on disk',
    'Po lewej to, co panel by zapisał, po prawej to, co działa teraz na Pi. Panel jest źródłem prawdy: przy zastosowaniu nadpisze plik swoją wersją.':
        'On the left, what the panel would write; on the right, what is running on the Pi right now. The panel is the source of truth: when applying, it overwrites the file with its own version.',
    '„Tylko na dysku” to osoby dopisane z pominięciem panelu - możesz je przejąć, żeby nimi zarządzać, albo zostawić, a znikną przy najbliższym zastosowaniu. „Tylko w panelu” trafi na dysk przy zastosowaniu.':
        "“Only on disk” are people added bypassing the panel - you can take them over to manage them, or leave them and they'll disappear at the next apply. “Only in the panel” goes to disk when applying.",
    'Podgląd i zastosowanie':
        'Preview and apply',
    'Porównanie z dyskiem':
        'Comparison with disk',
})

# --- komunikaty: tiery, hosty, ustawienia, kopie, alerty, dysk ---
EN.update({
    'podaj co najmniej jeden adres albo nazwę':
        'enter at least one address or name',
    'Nie zaznaczyłeś nikogo.':
        "You didn't select anyone.",
    'Helper niedostępny: {error}':
        'Helper unavailable: {error}',
    'Dodano {n} osób do panelu. Sprawdź im tier i nazwę, potem zastosuj zmiany.':
        'Added {n} people to the panel. Check their tier and name, then apply the changes.',
    'Nic nie doszło - panel zna już te osoby.':
        'Nothing was added - the panel already knows these people.',
    'Restart nieudany: {error}':
        'Restart failed: {error}',
    'Nie udało się wysłać - sprawdź kanał i adres.':
        'Sending failed - check the channel and address.',
    'Wysłano.':
        'Sent.',
    'Kopia się nie powiodła: {error}':
        'Backup failed: {error}',
    'Kopia zrobiona: {path}':
        'Backup made: {path}',
    'Odtwarzanie nieudane: {error}':
        'Restore failed: {error}',
    'Odtworzono z {source}: {peers} peerów, {tiers} tierów, {rules} reguł. Poprzedni stan zapisany jako {safety}. Zastosuj zmiany, żeby trafiły na interfejs.':
        'Restored from {source}: {peers} peers, {tiers} tiers, {rules} rules. The previous state was saved as {safety}. Apply the changes to get them onto the interface.',
    'reguła peera {name}':
        'rule of peer {name}',
    'podaj adres IPv4, IPv6 albo oba':
        'enter an IPv4 address, an IPv6 address or both',
    'Host {name} już istnieje.':
        'Host {name} already exists.',
    'Zapisano {name}.':
        'Saved {name}.',
    'Zmiana dotyczy {n} miejsc - zastosuj zmiany.':
        'The change affects {n} places - apply the changes.',
    'Nie usunę {name} - używają go: {users}.':
        "Won't delete {name} - it's used by: {users}.",
    'podsieć VPN IPv6 musi być IPv6':
        'the IPv6 VPN subnet must be IPv6',
    'podsieć VPN IPv6 jest za mała':
        'the IPv6 VPN subnet is too small',
    'adres serwera IPv6 poza podsiecią VPN':
        "the server's IPv6 address is outside the VPN subnet",
    'podaj prefiks IPv6 sieci domowej - bez niego blokada LAN nie miałaby odpowiednika w IPv6 i tier NAS widziałby cały LAN po IPv6':
        "enter the home network's IPv6 prefix - without it the LAN block would have no IPv6 counterpart and the NAS tier would see the whole LAN over IPv6",
    '{item} to nie jest prefiks IPv6':
        '{item} is not an IPv6 prefix',
    'IPv6 wyłączone. Zastosuj zmiany.':
        'IPv6 off. Apply the changes.',
    'IPv6 włączone, przydzielono {n} adresów. Zastosuj zmiany, a potem wydaj peerom nowe pliki konfiguracyjne.':
        'IPv6 on, {n} addresses assigned. Apply the changes, then give peers new configuration files.',
    'IPv6 włączone. Zastosuj zmiany, a potem wydaj peerom nowe pliki konfiguracyjne.':
        'IPv6 on. Apply the changes, then give peers new configuration files.',
    'Nie znalazłem prefiksu IPv6 na interfejsie WAN - LAN może nie mieć IPv6.':
        'No IPv6 prefix found on the WAN interface - the LAN may not have IPv6.',
    'Nie ma kopii do sprawdzenia.':
        'There are no backups to verify.',
    '{bad} z {total} kopii NIE przeszło weryfikacji - szczegóły w tabeli.':
        '{bad} of {total} backups FAILED verification - details in the table.',
    'Sprawdzono {n} kopii - sumy kontrolne zgodne.':
        'Verified {n} backups - checksums match.',
    'Złe reguły: {error}':
        'Invalid rules: {error}',
    'Dodano regułę. Zastosuj zmiany, żeby zadziałała.':
        'Rule added. Apply the changes for it to take effect.',
    'Usunięto regułę. Zastosuj zmiany.':
        'Rule deleted. Apply the changes.',
    'Nie usunę tieru {tier} - jest przypisany do {n} osób. Najpierw przenieś je do innego tieru.':
        "Won't delete tier {tier} - it's assigned to {n} people. Move them to another tier first.",
    'Usunięto tier {tier}.':
        'Deleted tier {tier}.',
    'Brak certyfikatu - HTTPS nie jest włączone.':
        'No certificate - HTTPS is not enabled.',
    'Kod z aplikacji wyłączony - logujesz się samym hasłem.':
        'Authenticator code off - you sign in with just your password.',
    'Nowe hasło musi mieć co najmniej 10 znaków.':
        'The new password must be at least 10 characters.',
    'Nowe hasła się różnią.':
        "The new passwords don't match.",
    'Hasło zmienione. Zaloguj się nowym.':
        'Password changed. Sign in with the new one.',
    'nie mogę wczytać certyfikatu: {error}':
        "can't load the certificate: {error}",
    'Złe obecne hasło.':
        'Wrong current password.',
})

# --- komunikaty: pozostale ---
EN.update({
    'Lista: {error}':
        'List: {error}',
    'Zrestartowano wg-quick.':
        'wg-quick restarted.',
    'Usunięto alerty: {n}.':
        'Alerts deleted: {n}.',
    'Nazwa: małe litery, cyfry, - i _, do 32 znaków. LAN i ANY są zajęte.':
        'Name: lowercase letters, digits, - and _, up to 32 characters. LAN and ANY are reserved.',
    'Dodano {name}.':
        'Added {name}.',
    'Usunięto {name}.':
        'Deleted {name}.',
    'Wykrywanie: {error}':
        'Detection: {error}',
    'Wykryto: {found}. Sprawdź i zapisz.':
        'Detected: {found}. Check and save.',
    'Tier utworzony.':
        'Tier created.',
    'Tier zapisany.':
        'Tier saved.',
    'Reguła: {error}':
        'Rule: {error}',
    'Certyfikat: {error}':
        'Certificate: {error}',
    'Zapisano. Zastosuj zmiany, żeby trafiły na interfejs.':
        'Saved. Apply the changes to get them onto the interface.',
})

# --- wykres - podpowiedz ---
EN.update({
    '{when} - pobrane {down}, wysłane {up}':
        '{when} - downloaded {down}, uploaded {up}',
})

# --- symulator i kopie - drobne ---
EN.update({
    'ruch między peerami':
        'traffic between peers',
    'sha256sum -c plik.sha256':
        'sha256sum -c file.sha256',
})

# --- wbudowane komentarze regul, dziennik ---
EN.update({
    'brak dostepu do LAN':
        'no LAN access',
    'dysk sieciowy + panel':
        'network drive + panel',
    'reszta LAN zablokowana':
        'rest of the LAN blocked',
    'zmiana hasła':
        'password changed',
    'kod z aplikacji wyłączony':
        'authenticator code off',
    'kod z aplikacji włączony':
        'authenticator code on',
    'usunięto alerty':
        'alerts deleted',
    'ustawienia powiadomień':
        'notification settings',
    'testowy alert':
        'test alert',
    'automatyczne zastosowanie':
        'automatic apply',
    'automatyczne cofnięcie':
        'automatic rollback',
    'potwierdzono zmiany':
        'changes confirmed',
    'potwierdzono automatycznie':
        'confirmed automatically',
    'zastosowanie nieudane':
        'apply failed',
    'zastosowano zmiany':
        'changes applied',
    'zmiana nieznana helperowi':
        'change unknown to the helper',
    'pobrano kopię':
        'backup downloaded',
    'kopia nieudana':
        'backup failed',
    'kopia zapasowa':
        'backup made',
    'odtworzono kopię':
        'backup restored',
    'ustawienia kopii':
        'backup settings',
    'migawka przed odtworzeniem':
        'snapshot before restore',
    'weryfikacja kopii':
        'backup verification',
    'odrzucono zmiany':
        'changes discarded',
    'przejęto peerów z dysku':
        'peers taken over from disk',
    'użyto linku jednorazowego':
        'one-time link used',
    'czyszczenie historii':
        'history cleanup',
    'dodano host':
        'host added',
    'usunięto host':
        'host deleted',
    'zmieniono host':
        'host changed',
    'integralność - bez alarmu':
        'integrity - no alarm',
    'IPv6 włączone':
        'IPv6 on',
    'nieudane logowanie':
        'failed sign-in',
    'logowanie':
        'sign-in',
    'porządki po starszej wersji':
        'cleanup after an older version',
    'start licznika dostępu':
        'access countdown started',
    'wznowiono peera':
        'peer resumed',
    'zawieszono peera':
        'peer suspended',
    'pobrano plik konfiguracji':
        'configuration file downloaded',
    'dodano peera':
        'peer added',
    'usunięto peera':
        'peer deleted',
    'link jednorazowy':
        'one-time link created',
    'zmieniono peera':
        'peer changed',
    'dostęp wygasł':
        'access expired',
    'plik oznaczony jako wysłany':
        'file marked as sent',
    'odcięto peera':
        'peer revoked',
    'rotacja kluczy':
        'key rotation',
    'otwarcie okna dostępu':
        'access window opened',
    'skasowano klucz prywatny':
        'private key deleted',
    'cofnięto konfigurację':
        'configuration rolled back',
    'dodano regułę':
        'rule added',
    'usunięto regułę':
        'rule deleted',
    'zmiana ustawień':
        'settings changed',
    'utworzono konto':
        'account created',
    'dodano tier':
        'tier added',
    'usunięto tier':
        'tier deleted',
    'zmieniono tier':
        'tier changed',
    'dodano regułę tieru':
        'tier rule added',
    'usunięto regułę tieru':
        'tier rule deleted',
    'odnowiono certyfikat':
        'certificate renewed',
    'restart wg-quick':
        'wg-quick restart',
    'harmonogram':
        'scheduler',
    'anonim':
        'anonymous',
    'brak potwierdzenia na czas':
        'no confirmation in time',
    'helper nie zna oczekującej zmiany':
        "the helper doesn't know the pending change",
    'przydzielono {n} adresów':
        '{n} addresses assigned',
    '{n} różnic, ale panel ma niezastosowane zmiany':
        '{n} differences, but the panel has unapplied changes',
    'zmiana okien dostępu: {names}':
        'access window change: {names}',
    '{n} odciętych peerów -> zawieszeni ze skasowanym kluczem':
        '{n} revoked peers -> suspended with the key deleted',
    'alias $NAS -> nas w {n} miejscach':
        'alias $NAS -> nas in {n} places',
    'dodano host nas z dawnego ustawienia':
        'added host nas from the old setting',
    'dodano {n}':
        'added {n}',
    '{label}: {summary}; wcześniejszy stan w {safety}':
        '{label}: {summary}; previous state in {safety}',
    'ip6tables: łańcuchy panelu usunięte':
        'ip6tables: panel chains removed',
    'ip6tables: łańcuchy przebudowane':
        'ip6tables: chains rebuilt',
    'iptables: łańcuchy przebudowane':
        'iptables: chains rebuilt',
    'ipv4: włączono przekazywanie pakietów':
        'ipv4: packet forwarding enabled',
    'ipv6: przekazywanie włączone (accept_ra=2 na WAN)':
        'ipv6: forwarding enabled (accept_ra=2 on WAN)',
    'ipv6: przekazywanie wyłączone':
        'ipv6: forwarding disabled',
    'nat6: dodano MASQUERADE':
        'nat6: MASQUERADE added',
    'nat: dodano MASQUERADE':
        'nat: MASQUERADE added',
    'wg syncconf: peery zsynchronizowane':
        'wg syncconf: peers synchronized',
    'commit-confirm: rollback za {n} s bez potwierdzenia':
        'commit-confirm: rollback in {n} s without confirmation',
    'ipv6: adres {address} na {iface}':
        'ipv6: address {address} on {iface}',
    'sysctl: nie zapisano {path}: {error}':
        'sysctl: could not save {path}: {error}',
    'wg-quick@{iface}: interfejs podniesiony':
        'wg-quick@{iface}: interface up',
    '{unit}: włączony przy starcie systemu':
        '{unit}: enabled at boot',
    'wg-quick@{iface}: zatrzymany (przed zmianą tunelu nie było)':
        'wg-quick@{iface}: stopped (there was no tunnel before the change)',
})

# --- dziennik - opisy kopii i alertow ---
EN.update({
    'bez szyfrowania':
        'not encrypted',
    'nieudany':
        'failed',
    'nie ma takiej kopii':
        'no such backup',
    'suma zgodna':
        'checksum matches',
})

# --- ntfy: token, ostrzezenie ---
EN.update({
    'Temat na publicznym ntfy.sh może przeczytać każdy, kto zna jego nazwę - a alerty zawierają nazwy peerów i ich adresy IP. Użyj długiej, losowej nazwy (np. {example}) albo tokenu dostępu.':
        'Anyone who knows the name of a topic on the public ntfy.sh can read it - and alerts contain peer names and their IP addresses. Use a long, random name (e.g. {example}) or an access token.',
    'Token ntfy':
        'ntfy token',
    'opcjonalnie - tk_...':
        'optional - tk_...',
})

# --- e-mail ---
EN.update({
    'E-mail (opcjonalnie)':
        'E-mail (optional)',
    'Dodatkowy kanał obok ntfy albo webhooka. Wysyła przez zwykłą skrzynkę (SMTP z logowaniem) - najlepiej osobne konto i hasło do aplikacji, bo hasło leży na Pi.':
        'An extra channel next to ntfy or the webhook. It sends through a regular mailbox (SMTP with sign-in) - ideally a separate account and an app password, because the password is stored on the Pi.',
    'Wysyłaj alerty także e-mailem':
        'Also send alerts by e-mail',
    'Serwer SMTP':
        'SMTP server',
    'Szyfrowanie':
        'Encryption',
    'brak':
        'none',
    'Login':
        'Username',
    'Nadawca':
        'Sender',
    'puste = login':
        'empty = username',
    'Adresaci':
        'Recipients',
})

# --- tryb certyfikatu ---
EN.update({
    'Panel działa po HTTPS z Twoim certyfikatem, ważnym do {date}. Gdy certbot go odnowi, panel sam weźmie nowy (sprawdza co 12 h).':
        'The panel runs over HTTPS with your certificate, valid until {date}. When certbot renews it, the panel picks up the new one itself (it checks every 12 h).',
    'własny urząd certyfikacji panelu (domyślnie)':
        "the panel's own certificate authority (default)",
    "własny certyfikat z plików (np. Let's Encrypt)":
        "your own certificate from files (e.g. Let's Encrypt)",
    'Certyfikat':
        'Certificate',
    'Plik certyfikatu (pełny łańcuch)':
        'Certificate file (full chain)',
    'Plik klucza prywatnego':
        'Private key file',
    'Pliki muszą leżeć w {dirs}. Helper kopiuje je dla panelu, sprawdza, czy klucz pasuje do certyfikatu, i bierze nowe po odnowieniu przez certbota.':
        'The files must be in {dirs}. The helper copies them for the panel, checks that the key matches the certificate, and picks up new ones after certbot renews them.',
    'Publiczny adres panelu (do linków w powiadomieniach)':
        'Public panel address (for links in notifications)',
    "Panel używa certyfikatu z zaufanego urzędu (np. Let's Encrypt) - na urządzeniach nic nie trzeba instalować.":
        "The panel uses a certificate from a trusted authority (e.g. Let's Encrypt) - nothing needs to be installed on devices.",
    'zmiana trybu certyfikatu':
        'certificate mode changed',
})

# --- tryb certyfikatu - komunikaty ---
EN.update({
    'Adres panelu: np. https://vpn.example.com (bez ścieżki).':
        'Panel address: e.g. https://vpn.example.com (no path).',
    'Nieznany tryb certyfikatu.':
        'Unknown certificate mode.',
    'Podaj ścieżkę do certyfikatu i do klucza.':
        'Enter the path to the certificate and to the key.',
    'pliki certyfikatu muszą leżeć w {dirs}':
        'certificate files must be in {dirs}',
    'klucz nie pasuje do certyfikatu':
        "the key doesn't match the certificate",
    'to nie jest certyfikat w formacie PEM':
        'this is not a PEM certificate',
    'to nie jest klucz prywatny w formacie PEM':
        'this is not a PEM private key',
    'nie mogę odczytać pliku: {error}':
        "can't read the file: {error}",
})

# --- reverse proxy na innym serwerze ---
EN.update({
    'Adres proxy: {item} to nie jest adres IP ani podsieć.':
        'Proxy address: {item} is not an IP address or subnet.',
    'Adres proxy: {item} oznaczałby zaufanie do każdego - podaj adres serwera proxy.':
        "Proxy address: {item} would mean trusting everyone - enter the proxy server's address.",
    'Pobierz certyfikat urzędu panelu ({link}) i skopiuj go na serwer proxy - proxy łączy się z Pi po HTTPS i sprawdza certyfikat panelu, więc odcinek do Pi też jest szyfrowany.':
        "Download the panel's certificate authority ({link}) and copy it to the proxy server - the proxy connects to the Pi over HTTPS and checks the panel's certificate, so the hop to the Pi is encrypted too.",
    'Przy własnym certyfikacie zamiast {option} ustaw w proxy nazwę z certyfikatu ({server_name}).':
        'With your own certificate, instead of {option} set the name from the certificate in the proxy ({server_name}).',
    'Reverse proxy na innym serwerze (opcjonalnie)':
        'Reverse proxy on another server (optional)',
    'Np. Caddy na osobnym serwerze. Panel zostaje przy swoim HTTPS, a nagłówkom proxy (X-Forwarded-For, X-Forwarded-Proto) ufa wyłącznie od podanych adresów - od innych urządzeń są ignorowane.':
        'E.g. Caddy on a separate server. The panel keeps its own HTTPS and trusts proxy headers (X-Forwarded-For, X-Forwarded-Proto) only from the addresses given - from other devices they are ignored.',
    'Adres serwera proxy':
        'Proxy server address',
    'do bloku z Twoją domeną':
        'into the block with your domain',
    'Uwaga: przez proxy stronę logowania zobaczy każdy, kto ma dostęp do proxy - także osoby z VPN, którym panel sam by jej nie pokazał. Włącz kod z aplikacji (TOTP) i w proxy ogranicz, kto może otworzyć panel. Zapora Pi musi wpuszczać serwer proxy na port panelu (instalator: {lan}).':
        "Note: through the proxy, anyone who can reach the proxy will see the sign-in page - including VPN users the panel itself would not show it to. Turn on the authenticator code (TOTP) and restrict in the proxy who can open the panel. The Pi's firewall must let the proxy server reach the panel port (installer: {lan}).",
})

# --- kopia na NAS teraz ---
EN.update({
    'Wysłać kopię na NAS bez szyfrowania?':
        'Send the backup to the NAS unencrypted?',
    'Kopia zawiera klucze PSK wszystkich peerów. Bez hasła kopii trafi na udział jawnym tekstem - ustaw hasło kopii w Harmonogramie i celu, żeby ją zaszyfrować.':
        "The backup contains every peer's PSK. Without a backup password it goes to the share as plain text - set a backup password under Schedule and destination to encrypt it.",
    'Wyślij bez szyfrowania':
        'Send unencrypted',
    'kopia SMB: brak użytkownika albo hasła NAS - uzupełnij je w Harmonogramie i celu':
        'SMB backup: NAS username or password missing - fill them in under Schedule and destination',
    'brak smbclient - zainstaluj na Pi: sudo apt install smbclient':
        'smbclient is missing - install it on the Pi: sudo apt install smbclient',
})

# --- panel kopii - dwa przyciski ---
EN.update({
    'Kopia lokalnie':
        'Back up locally',
    'Kopia na NAS':
        'Back up to NAS',
    'NAS':
        'NAS',
    'Kopie automatyczne: codziennie o {time} → {where}.':
        'Automatic backups: daily at {time} → {where}.',
    'Kopie automatyczne są wyłączone - włączysz je w Harmonogramie i celu.':
        'Automatic backups are off - turn them on under Schedule and destination.',
    'kopia SMB: brak adresu albo udziału NAS - uzupełnij je w Harmonogramie i celu':
        'SMB backup: NAS address or share missing - fill them in under Schedule and destination',
})

# --- usuwanie kopii ---
EN.update({
    'zaznacz kopię':
        'select backup',
    'Usunąć zaznaczone kopie?':
        'Delete the selected backups?',
    'Usuń kopie':
        'Delete backups',
    'Zaznacz nieudane':
        'Select failed',
    'Usuń zaznaczone':
        'Delete selected',
    '„Trzymaj ostatnie” sprząta tylko katalog na karcie - kopie na NAS usuwasz tutaj.':
        '“Keep the last” only cleans the directory on the card - you delete backups on the NAS here.',
    'Nie zaznaczyłeś żadnej kopii.':
        "You didn't select any backup.",
    'Usunięto kopie: {n} (plików na karcie: {local}, na NAS: {remote}).':
        'Backups deleted: {n} (files on the card: {local}, on the NAS: {remote}).',
    'Nie usunięto: {problems}':
        'Not deleted: {problems}',
    'usunięto kopie':
        'backups deleted',
    '{n} (karta: {local}, NAS: {remote})':
        '{n} (card: {local}, NAS: {remote})',
    'kopia SMB: {name} leży pod innym adresem niż w ustawieniach - usuń ją ręcznie w DSM':
        'SMB backup: {name} is at a different address than in the settings - delete it manually in DSM',
})

# --- kopia sprzed odtworzenia ---
EN.update({
    'sprzed odtworzenia':
        'before restore',
    'Zapisana automatycznie tuż przed odtworzeniem - pozwala wrócić do poprzedniego stanu, jeśli odtworzenie okazało się pomyłką. Gdy wszystko się zgadza, możesz ją usunąć.':
        'Saved automatically right before a restore - it lets you go back to the previous state if the restore was a mistake. Once everything checks out, you can delete it.',
})

# --- okno usuwania kopii ---
EN.update({
    'Zaznaczone kopie zostaną usunięte z karty razem z plikami .sha256. Tego nie da się cofnąć - zostaw przynajmniej jedną zgodną kopię.':
        "The selected backups will be deleted from the card together with their .sha256 files. This can't be undone - keep at least one verified backup.",
    'Zaznaczone kopie zostaną usunięte razem z plikami .sha256 - z karty, a kopie na NAS także z NAS. Tego nie da się cofnąć - zostaw przynajmniej jedną zgodną kopię.':
        "The selected backups will be deleted together with their .sha256 files - from the card, and backups on the NAS from the NAS too. This can't be undone - keep at least one verified backup.",
    'Zaznaczone: {n} (z karty: {local}, z NAS: {smb}).':
        'Selected: {n} (from the card: {local}, from the NAS: {smb}).',
})

# --- kopie - brak dostepu ---
EN.update({
    'brak dostępu do katalogu kopii {path} - zainstaluj panel ponownie':
        'no access to the backup directory {path} - reinstall the panel',
})
