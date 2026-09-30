# wg-panel — architektura i budowa

*[English version → `docs/en/ARCHITECTURE.md`](../en/ARCHITECTURE.md)*

To dokumentacja techniczna: jak panel jest zbudowany, co robi każdy moduł, jak działają reguły, bezpiecznik, HTTPS, i kopie zapasowe. Przydaje się przy zmianach w kodzie albo diagnozie. **Jeśli chcesz zainstalować panel — zacznij od [INSTALACJA.md](INSTALACJA.md).**

Spis: [Architektura](#architektura) · [Model reguł](#model-reguł) · [Bezpiecznik](#bezpiecznik) · [HTTPS](#https) · [Kopie zapasowe](#kopie-zapasowe) · [Warstwy bezpieczeństwa](#warstwy-bezpieczeństwa) · [Budowa i pliki](#budowa-i-pliki) · [Znane ograniczenia](#znane-ograniczenia)

## Architektura

```
przegladarka            wgctl (terminal)
     |  HTTP                 |
     |  (tylko wg0 i LAN)    |
     v                       v
     +--- SQLite: zrodlo prawdy ---+
                  |
                  |  gniazdo unix, JSON, lista dozwolonych operacji
                  v
        helper/wg_panel_helper.py    root
                  |
                  v
  /etc/wireguard/wg0.conf  +  lancuchy WG-USERS / WG-USERS-HOST
```

**SQLite jest źródłem prawdy.** `wg0.conf` i plik reguł są *generowane*. Nie edytuj ich ręcznie —
przy najbliższym zastosowaniu zmian zostaną nadpisane.

**Panel nigdy nie wysyła komend ani gotowego tekstu do zapisu.** Wysyła model danych: ustawienia
i listę peerów ze znormalizowanymi regułami. Helper waliduje wszystko od nowa i sam renderuje
pliki. Gdyby ktoś przejął proces webowy, nadal nie wykona niczego, czego helper nie przewidział.

**Logika generowania jest celowo zduplikowana** w `panel/render.py` i w helperze. Panel liczy
podgląd, helper liczy stan faktyczny. 

**Klucz prywatny serwera nigdy nie trafia do bazy ani do panelu.** Helper czyta go z dysku przy
każdym renderowaniu i sam podstawia w miejsce placeholdera.

## Okna dostępu

Każdy peer ma jeden z trzech trybów. Tryb wybierasz jawnie — panel niczego nie zgaduje
z wypełnionych pól.

| Tryb | Kiedy używać | Co robi harmonogram |
|---|---|---|
| **bezterminowo** | domownicy, Twoje urządzenia | nic |
| **okno terminowe (od–do)** | ekipa remontowa od 1 do 31 października, biuro na czas rozliczenia | otwiera dostęp w dniu startu, zamyka po dacie końca |
| **N dni od pierwszego użycia** | gość, któremu dajesz plik dziś, a on połączy się kiedyś | odliczanie rusza przy pierwszym handshake'u |

Trzeci tryb jest wygodny wtedy, gdy nie wiesz, kiedy ktoś faktycznie skorzysta. Wydajesz kod QR
w poniedziałek, gość włącza tunel w czwartek — siedem dni liczy się od czwartku, nie od
poniedziałku. Do pierwszego połączenia peer pokazuje się jako „jeszcze nieużyty".

Peer założony z datą startu w przyszłości czeka zawieszony i włącza się sam. Harmonogram rusza
tylko te stany, które sam ustawił: jeśli **Ty** kogoś zawiesisz ręcznie, nie zostanie wznowiony
przy otwarciu okna. Zmiana trybu w panelu zeruje ten znacznik, więc kontrola wraca do harmonogramu.

Po każdej automatycznej zmianie okien panel sam stosuje konfigurację — bez bezpiecznika, bo
o trzeciej w nocy nie ma kto potwierdzić. To bezpieczne, bo zamknięcie cudzego okna nie dotyka
dostępu administratora.

## Dostęp do panelu

Panel słucha na adresach z `WGPANEL_HOST` (lista po przecinku albo `0.0.0.0`); `127.0.0.1` jest
zawsze, a adresu, którego jeszcze nie ma (`10.0.0.1` przed podniesieniem `wg0`), panel pilnuje
w tle. Instalacja z `LAN_ACCESS=192.168.1.0/24` wystawia panel na sieć domową i zakłada regułę ufw.

Z VPN panel otworzą **tylko peery z zaznaczonym dostępem** (`panel_access`): port panelu trafia
do portów zarządzanych łańcucha `WG-USERS-HOST`, więc pozostali dostają DROP mimo bycia w VPN.
Przełącznik jest na stronie osoby, w terminalu `wgctl set NAZWA --panel on`.

## Co idzie przez VPN u klienta

Linia `AllowedIPs` w pliku klienta jest liczona z trybu: tieru albo nadpisanego u osoby.

| Tryb | AllowedIPs |
|---|---|
| `full` — cały ruch | `0.0.0.0/0` (+ `::/0` przy IPv6), z DNS |
| `auto` — tylko to, do czego ma dostęp | cele reguł ACCEPT osoby i tieru, zwinięte; + adres Pi, gdy tier ma porty hosta |
| `lan` — cała sieć domowa | VPN + LAN (jak w pierwszej wersji) |
| `custom` — własna lista | nazwy z katalogu i adresy |

Domyślnie ADMIN i NET mają `full`, NAS — `auto` (`192.168.1.11/32`). Dodanie reguły ACCEPT
w trybie `auto` samo rozszerza plik klienta. Panel pamięta, jaki zakres dostała osoba przy
ostatnim wydaniu pliku, i ostrzega, gdy obecny plik czegoś nie obejmuje. `AllowedIPs` to routing,
nie uprawnienia — o dostępie decydują wyłącznie reguły na serwerze.

## Katalog hostów LAN

Zakładka **Hosty**. Każdy wpis ma nazwę, adres IPv4, opcjonalnie IPv6 i opis. W regułach
wpisujesz nazwę — `nas`, `drukarka`, `@ha` — a generator podstawia adres przy każdym zastosowaniu.

Zmiana adresu NAS to jedno pole w katalogu zamiast przejścia po wszystkich regułach i tierach.
Alias `$NAS` z pierwszej wersji panelu został usunięty — przy starcie panel sam przepisuje go w regułach i listach na host `nas` z katalogu (tak samo po odtworzeniu starej kopii).

Nie da się skasować hosta, na którym stoi choć jedna reguła — panel pokaże, gdzie jest używany.
Nazwy `LAN` i `ANY` są zarezerwowane.

## IPv6

Wyłączone domyślnie. Po włączeniu (Ustawienia → IPv6):

- każdy peer dostaje adres odpowiadający IPv4: `10.0.0.4` → `fd42:42:42::4`;
- `wg0.conf` dostaje drugi adres serwera i `AllowedIPs` z `/128`;
- reguły są generowane drugi raz, do `ip6tables`, z tymi samymi łańcuchami;
- `LAN` w regule rozwija się na **oba** prefiksy — IPv4 i wszystkie prefiksy IPv6 sieci domowej;
- host z katalogu, który ma adres IPv6, działa w obu rodzinach.

**Prefiks IPv6 sieci domowej jest wymagany.** Bez niego blokada `LAN` nie miałaby odpowiednika
w IPv6 i tier NAS widziałby cały LAN po IPv6 — panel odmówi włączenia. Przycisk „Wykryj prefiks
LAN" czyta trasy z interfejsu WAN i podpowiada.

⚠ Helper ustawia `accept_ra=2` na interfejsie WAN **przed** `forwarding=1`. Kolejność jest
istotna: włączenie przekazywania wyłącza domyślnie przyjmowanie ogłoszeń routera i Pi straciłby
trasę domyślną IPv6. Ustawienia trafiają do `/etc/sysctl.d/99-wg-panel-ipv6.conf`; wyłączenie
IPv6 w panelu usuwa ten plik i łańcuchy `ip6tables`.

Po włączeniu IPv6 peerom trzeba wydać **nowe pliki konfiguracyjne** — stare nie mają adresu IPv6.

## Historia ruchu

Harmonogram co 5 minut zapisuje liczniki z `wg show dump`. Z różnic kolejnych próbek powstaje
szereg godzinowy; spadek licznika oznacza restart interfejsu i jest obsługiwany. Próbki starsze niż
8 dni są kasowane.

Wykres: suma na liście peerów, osobny na stronie każdego peera. Nad osią pobieranie przez klienta,
pod osią wysyłka.

**Anomalia**: ostatnia pełna godzina większa niż N-krotność mediany godzin z tygodnia (domyślnie
10×) i jednocześnie powyżej progu (domyślnie 500 MB). Mediana liczona jest tylko z godzin, w których
był jakikolwiek ruch, i dopiero po 12 takich godzinach — żeby nowy peer nie alarmował od razu.

## Integralność

Co 5 minut panel porównuje stan z tym, co sam zastosował:

| Sprawdzenie | Wykrywa |
|---|---|
| SHA-256 `wg0.conf` i plików reguł | ręczną edycję plików |
| prawa dostępu | klucze czytelne dla innych kont |
| lista peerów na interfejsie | klucz dodany przez `wg set` z pominięciem panelu |
| liczba reguł w łańcuchach | reguły dopisane do łańcuchów panelu |
| pierwsza reguła `FORWARD` | ACCEPT wstawiony przed łańcuch panelu |

Każda rozbieżność to alarm i czerwony baner na liście peerów. Przywrócenie = ponowne zastosowanie
z bazy, z bezpiecznikiem.

⚠ To wykrywanie, nie ochrona. Działa przeciw zmianom zrobionym ręcznie albo przez kogoś, kto
nie wie o panelu. Kto ma roota na działającym Pi i wie, co robi, może wyłączyć także sam panel —
żadne oprogramowanie na tej samej maszynie przed tym nie ochroni.

## Konto

Logowanie hasłem; kod z aplikacji (TOTP) jest opcjonalny i przełączany w Ustawieniach → Konto.
Obie zmiany — TOTP i hasło — wymagają podania obecnego hasła. Zmiana hasła kończy wszystkie
sesje tego konta. Pole na kod widać na stronie logowania tylko wtedy, gdy ktoś ma TOTP włączony.

Szyfrowania bazy na karcie SD świadomie nie ma: Pi stoi w domu, a szyfrowanie kluczem spoza
karty oznaczałoby, że po zaniku prądu tunel nie wstaje sam.

## Kody QR

Enkoder w czystym Pythonie (`panel/qrcode.py`): tryb bajtowy, wersje 1–40, automatyczny dobór
wersji, poziomu korekcji i maski według ISO/IEC 18004. Konfiguracja z IPv6 mieści się w wersji
ok. 14. Sprawdzony dekodowaniem przez OpenCV na tekstach różnej długości i wszystkich ośmiu maskach.

`qrencode` przestał być potrzebny. `wgctl qr` rysuje kod w terminalu znakami półbloków.

## wgctl

To samo co panel, z terminala. Ta sama baza, ten sam helper, żadnej równoległej logiki.

```bash
wgctl status                      # stan tunelu, czy są niezastosowane zmiany
wgctl list                        # peery z handshakiem i datą ważności
wgctl add Marek --tier NAS --device laptop                 # bezterminowo
wgctl add Ekipa --tier NAS --start 2026-10-01 --until 2026-10-31
wgctl add Gosc  --tier NAS --days 7      # 7 dni od pierwszego polaczenia
wgctl config Marek                # plik klienta na stdout
wgctl qr Marek                    # kod QR w terminalu
wgctl rule add Marek --accept 192.168.1.60 --proto tcp --port 9091 --comment nas
wgctl rule list Marek
wgctl check Marek 192.168.1.11 445 -v    # symulacja z całym przejściem łańcucha
wgctl suspend Marek / resume / rotate / delete --yes
wgctl expire               # terminy dostępu teraz (bez panelu)
wgctl guard                # strażnik ręcznie: co działa, co nie
wgctl diff                        # co zmieni najbliższe zastosowanie
wgctl apply                       # z bezpiecznikiem; potem wgctl confirm
wgctl backup                      # kopia zgodnie z ustawieniami, od razu weryfikowana
wgctl backup --verify             # sprawdz sumy kontrolne ostatnich kopii
wgctl host list                   # katalog hostow
wgctl host add drukarka 192.168.1.70 --ip6 fd00:77::70
wgctl integrity                   # czy ktos zmienil konfiguracje poza panelem (kod 3 = tak)
wgctl totp off admin              # awaryjnie wylacz kod z aplikacji (zgubiony telefon)
wgctl traffic Marek               # ruch z ostatnich 7 dni, dzien po dniu
wgctl export /mnt/nas/kopia.json.enc --pass haslo
```

`wgctl check` zwraca kod 0 przy ACCEPT i 2 przy DROP, więc nadaje się do skryptów i monitoringu.

## Model reguł

Reguły trafiają do łańcucha `WG-USERS` podpiętego na początku `FORWARD`. Kolejność w łańcuchu to
kolejność sprawdzania:

```
1. -o wg0 -j DROP                      ruch miedzy peerami zablokowany
2. reguly wlasne peera 1               (bardziej szczegolowe - wyzej)
3. reguly tieru peera 1
4. ... to samo dla kolejnych peerow
5. -j ACCEPT                           brak dopasowania = pelny dostep
```

**Reguły własne peera są sprawdzane przed regułami tieru.** To istotne: tier NAS kończy się
blokadą całego LAN-u, więc gdyby reguła własna szła po nim, wyjątek na pojedynczy host nigdy by
nie zadziałał. Dzięki temu porządkowi reguła własna nadpisuje tier w obie strony — może dołożyć
wyjątek do blokady i może zabrać dostęp, który daje tier.

Punkt 5 realizuje tier ADMIN: peer bez żadnych reguł spada do końcowego ACCEPT.

Dostęp do samego serwera (SSH, RDP) jest w osobnym łańcuchu `WG-USERS-HOST` na `INPUT` — bo ten
ruch nigdy nie przechodzi przez `FORWARD`. Ustawiasz go polem „porty hosta" w tierze.

W polu `dest`: konkretny adres, CIDR, `LAN` (rozwijane do podsieci z ustawień), nazwa hosta z katalogu (np. `nas`) albo `ANY`.

## Bezpiecznik

Domyślnie każdy `apply` uzbraja timer. Bez potwierdzenia w oknie czasowym (domyślnie 90 s) helper
przywraca poprzednią konfigurację i ponownie ją stosuje. To zabezpieczenie przed jedynym błędem,
którego nie da się naprawić zdalnie — regułą, która odcina Cię od panelu.

Kopie: ostatnie 40 wersji w `/etc/wireguard/wg-panel-backups/`.

**Potwierdzanie automatyczne (domyślne).** Po zastosowaniu przeglądarka sama sprawdza, czy nadal
dociera do panelu, i potwierdza — zwykle w ułamku sekundy, bez klikania. Widzisz wtedy tylko
zielone „Zastosowano zmiany.”. Żółty pasek z licznikiem pojawia się dopiero, gdy sprawdzenie
trwa dłużej niż 3 sekundy (np. przy pierwszym podniesieniu tunelu albo gdy coś jest nie tak) —
wtedy trzeba wiedzieć, że zmiana czeka i może się cofnąć. Dokładnie to sprawdza bezpiecznik:
czy zmiana nie odcięła Cię od panelu. Jeśli odcięła, potwierdzenie nie dojdzie i po czasie wszystko
wróci samo. Dowodem jest wyłącznie połączenie nawiązane **po** zastosowaniu: w czasie oczekiwania
każda odpowiedź panelu zamyka połączenie (`Connection: close`), a potwierdzenie przysłane starym,
wcześniej otwartym połączeniem jest odrzucane z prośbą o ponowienie — takie połączenie mogłoby
przejść przez reguły, które nowe blokują. Zmiany zastosowane przez `wgctl apply` potwierdza się
zawsze ręcznie (`wgctl confirm` albo przycisk na pasku).

**Restart w trakcie oczekiwania.** Oczekująca zmiana przeżywa restart helpera i całego Pi:
helper trzyma ją w `/etc/wireguard/wg-panel-pending.json` (tylko root, token wyłącznie jako skrót)
i po starcie daje świeże okno na potwierdzenie — bez potwierdzenia cofa zmianę jak zwykle.
Świeże okno zamiast zapisanego terminu, bo Pi nie ma zegara sprzętowego i zaraz po starcie
godzina bywa zła. Ślad po automatycznym cofnięciu też zostaje na dysku, więc panel dowie się
o nim nawet po kolejnym restarcie.

**Potwierdzanie ręczne** (Ustawienia → Zaawansowane → Potwierdzanie zmian): pasek czeka na
kliknięcie „Potwierdzam, działa”. Przydaje się, gdy chcesz przed potwierdzeniem sprawdzić coś
poza panelem, np. dostęp do NAS z telefonu.

Z panelu zastosowanie idzie **zawsze** z bezpiecznikiem — nie ma przełącznika, który go wyłącza,
a czas z ustawień jest przycinany do 30–900 s. Bez bezpiecznika tylko awaryjnie: `wgctl apply --no-safe`.

Dopóki zmiana czeka, u góry **każdej** strony wisi pasek z licznikiem oraz przyciskami
„Potwierdzam, działa” i „Cofnij teraz”. Pasek sam pyta panel o stan (`/api/pending`), więc:
znika od razu po potwierdzeniu (także z innej karty albo z `wgctl confirm`), a po automatycznym
cofnięciu mówi to wprost. „Cofnij teraz” cofa wyłącznie zmianę, która naprawdę czeka — nigdy
kopii zapasowej. Do potwierdzenia panel trzyma na boku stan sprzed zastosowania, więc po cofnięciu
„Odrzuć zmiany” wraca dokładnie do tego, co działa na interfejsie, a kontrola integralności nie
zgłasza cofnięcia jako zmiany poza panelem.

## HTTPS

Panel działa po HTTPS z własnego urzędu certyfikacji. Instalator go tworzy, a helper (root)
wystawia certyfikat panelu (ECDSA P-256, 397 dni) i sam go odnawia — na 30 dni przed końcem
i po zmianie adresu Pi. Panel przeładowuje certyfikat bez restartu. Urząd ma **ograniczenie nazw**
(X.509 Name Constraints): może poświadczać tylko podsieć domową, VPN i nazwę Pi, więc
zainstalowany na telefonie nie pozwala podrobić żadnej innej strony — nawet przy wykradzionym
kluczu. Klucz urzędu jest w `/etc/wireguard/wg-panel-ca/` (tylko root), panel dostaje wyłącznie
swój certyfikat w `/var/lib/wg-panel/tls/`.

HTTPS i HTTP działają na jednym porcie: zwykłe HTTP z sieci dostaje przekierowanie na HTTPS,
a z `127.0.0.1` (tunel SSH) zostaje HTTP — ten ruch nie opuszcza Pi. Certyfikat urzędu z instrukcją
dla iPhone'a, Androida, Windows i macOS jest na stronie `/tls` (bez logowania); odcisk SHA-256 do
porównania wypisują instalator i `wgctl tls` (`wgctl tls --renew` odnawia od razu).
**Tryb certyfikatu** (Ustawienia → HTTPS → Certyfikat):
- **własny urząd certyfikacji panelu** (domyślnie) — jak wyżej;
- **własny certyfikat z plików** (np. Let's Encrypt) — podajesz ścieżki do pełnego łańcucha i klucza.
  Pliki muszą leżeć w `/etc/letsencrypt/` albo `/etc/ssl/wg-panel/` (helper działa jako root, więc
  nie skopiuje pliku z dowolnego miejsca) i klucz musi pasować do certyfikatu. Helper sprawdza je
  co 12 h — odnowienie przez certbota trafia do panelu samo, bez restartu. Na urządzeniach nic nie
  trzeba instalować;

**Reverse proxy na innym serwerze** (opcjonalnie, np. Caddy) — Ustawienia → HTTPS →
„Reverse proxy na innym serwerze”. Panel zostaje przy swoim HTTPS; wpisujesz adres serwera proxy,
a panel ufa nagłówkom `X-Forwarded-For` / `X-Forwarded-Proto` **wyłącznie** od tego adresu
(od innych urządzeń są ignorowane) i bierze **ostatni** adres z `X-Forwarded-For` — ten dopisuje
proxy; wcześniejsze może podać sam klient, żeby obejść blokadę po nieudanych logowaniach.
Proxy łączy się z Pi po HTTPS i sprawdza certyfikat panelu (plik `wg-panel-ca.crt` ze strony
`/tls`), więc odcinek do Pi też jest szyfrowany — zwykłe HTTP z sieci panel i tak przekierowuje
na HTTPS. Fragment do `Caddyfile` (z adresem Pi) i wersja dla nginx są w panelu. Na samym Pi nie
instalujesz ani nginx, ani Caddy.

Przez proxy stronę logowania zobaczy każdy, kto ma dostęp do proxy — także osoby z VPN, którym
panel sam by jej nie pokazał (zapora Pi wpuszcza na port panelu tylko osoby z dostępem, ale proxy
to inna maszyna). Włącz kod z aplikacji (TOTP) i ogranicz dostęp do panelu w samym proxy. Zapora
Pi musi wpuszczać serwer proxy na port panelu (`LAN_ACCESS` w instalatorze).

„Publiczny adres panelu” (np. `https://vpn.example.com`) trafia do linków w powiadomieniach.

Bez HTTPS: `TLS=0 ./install.sh`. Komunikaty instalatora po polsku: `INSTALL_LANG=pl ./install.sh` (domyślnie po angielsku).

Odinstalowanie: `sudo /opt/wg-panel/uninstall.sh` — usuwa panel, VPN działa dalej, dane zostają;
`--purge` usuwa też dane i konto `wg-panel`; `--restore` wraca do konfiguracji sprzed panelu.


## Powiadomienia i strażnik

**ntfy** (główny kanał): wiadomość idzie w formacie JSON, więc polskie znaki w tytule dochodzą
poprawnie. Najpoważniejsze zdarzenia — ten sam klucz z nowego adresu, zmiana konfiguracji poza
panelem, `wg0` nie działa — mają najwyższy priorytet; stuknięcie w powiadomienie otwiera stronę
alertów panelu. Opcjonalny token (`tk_...`) pozwala używać chronionego tematu (ntfy.sh
z zarezerwowanym tematem albo własny serwer ntfy). Temat na publicznym ntfy.sh czyta każdy, kto
zna jego nazwę — panel ostrzega, jeśli nazwa jest krótka albo łatwa do zgadnięcia.

**E-mail** (opcjonalnie, obok ntfy): Alerty → Ustawienia powiadomień → E-mail. Zwykłe SMTP
z logowaniem (STARTTLS albo SSL); najlepiej osobne konto i hasło do aplikacji, bo hasło leży na Pi.
Hasło, jak inne sekrety, jest pomijane w kopiach zapasowych.

**Strażnik** (`wg-panel-guard.timer`, co minutę, poza procesem panelu — `wgctl guard`) sprawdza
panel (usługę i odpowiedź strony), helpera, interfejs `wg0`, temperaturę Pi, miejsce na karcie,
zbyt niskie napięcie zasilania i ważność certyfikatu HTTPS. Alarm idzie dopiero przy dwóch
nieudanych sprawdzeniach z rzędu (krótki restart go nie wywoła), raz; po naprawie — jedno
„znowu działa”. Na czas aktualizacji instalator wstrzymuje strażnika. Nie powiadomi, jeśli padnie
całe Pi — do tego potrzebny jest monitoring z zewnątrz (np. Uptime Kuma na osobnej maszynie).

**Terminy dostępu niezależne od panelu**: ten sam timer odcina osoby po terminie i otwiera okna
dostępu także wtedy, gdy panel nie działa (`wgctl expire` robi to samo ręcznie). Zmiana trafia na
interfejs punktowo — bierze to, co naprawdę działa, i zmienia tylko stan osób, którym zmienił się
termin. Twoje niezastosowane zmiany zostają niezastosowane (wcześniej wygaśnięcie wypychało cały
stan panelu bez bezpiecznika). Gdy jakaś zmiana czeka na potwierdzenie, harmonogram czeka minutę.
Panel i timer nie pracują naraz (blokada pliku).

Usługi panelu i helpera mają `Restart=always` — systemd podnosi je po każdym zakończeniu.

## Języki

Panel jest po polsku (z polskimi znakami) i po angielsku. Przełącznik z flagami jest obok
przełącznika motywu — także na stronie logowania i w menu na telefonie. Wybór zapamiętuje
przeglądarka (ciasteczko `lang`); komunikaty powstają od razu w języku osoby, która kliknęła.

Tekst polski jest kluczem tłumaczenia (`panel/i18n.py`, słownik `EN`), więc w kodzie zostaje
czytelny polski, a brak tłumaczenia nigdy nie daje pustego miejsca. Opisy wbudowanych tierów
i komentarze ich reguł tłumaczą się tylko, dopóki ich nie zmienisz (komentarze trafiają do
zapory, więc w bazie zostają bez zmian — tłumaczenie jest wyłącznie na ekranie). Dziennik pokazuje
czytelne nazwy akcji (kod zostaje obok) i tłumaczy znane szczegóły — także starsze wpisy. Komunikaty helpera (root) widoczne w codziennej
pracy — integralność, potwierdzanie i cofanie — panel rozpoznaje i pokazuje w swoim języku;
rzadkie błędy techniczne helpera zostają po polsku. `wgctl` i treść powiadomień są po polsku;
dokumentacja jest po polsku i po angielsku.

zgadzały się w obu językach, na stronach po angielsku nie było polskiego tekstu i żeby nikt nie
przypisał do `_` (w funkcji z takim przypisaniem `_("...")` przestaje działać — tak raz
zepsuło się logowanie).

## Telefon

Panel działa w przeglądarce telefonu bez przewijania strony w bok (sprawdzone od 320 px).
Poniżej 720 px nawigacja chowa się pod przyciskiem „Menu” (z licznikiem alertów), lista peerów
zamienia się w karty, a pozostałe tabele w listy „podpis: wartość” — podpisy biorą się
z nagłówków kolumn. Na ekranach dotykowych (także telefon w poziomie i tablet) pola formularzy
mają 16 px, więc iPhone nie przybliża strony przy logowaniu, a przyciski są pod palec.
Na komputerze z myszką wygląd się nie zmienia.

## Potwierdzenia akcji

Jedno okno potwierdzenia dla całego panelu (funkcja `ui.ask()` → atrybuty `data-confirm*`).
Pyta wyłącznie przy akcjach nieodwracalnych albo zrywających połączenie: usuwanie, rotacja
i kasowanie klucza, odrzucenie zmian, cofanie, restart, odtwarzanie kopii. Zapisy, dodawanie
i zawieszanie peera działają bez pytania. „Zastosuj zmiany” też — karta na liście peerów już
pokazuje, co się zmieni, a przed odcięciem chroni bezpiecznik z automatycznym sprawdzeniem.

Strona peera ma jeden zapis dla wszystkich pól (także „Co idzie przez VPN”). Pasek „Zapisz zmiany”
pojawia się dopiero, gdy coś zmienisz; wyjście z niezapisanymi zmianami pyta przeglądarka. Serwer
zapisuje wyłącznie pola, które naprawdę się zmieniły — zapis bez zmian niczego nie rusza, więc nie
kasuje ani „czeka na start okna dostępu”, ani terminu liczonego od pierwszego połączenia. Wielu
peerów możesz dodać jeden po drugim („Dodaj kolejnego peera”) i zastosować raz, na końcu. Przycisk w oknie nazywa czynność
(„Usuń peera”, nie „OK”); przy akcjach niszczących fokus startuje na „Anuluj”, żeby Enter niczego
nie skasował. Błędy pokazują się na czerwonym pasku, który nie znika sam; zielony — tylko sukces.

Automatyczne zastosowanie po wygaśnięciu peera celowo nie używa bezpiecznika — nie ma tam nikogo,
kto by potwierdził. Usunięcie wygasłego peera nie może odciąć administratora, bo nie dotyka
łańcucha hosta dla pozostałych.

## Alerty

| Zdarzenie | Kiedy | Waga |
|---|---|---|
| `new_endpoint` | ten sam klucz łączy się z innego adresu IP | alarm |
| `dead_peer` | brak handshake'u dłużej niż próg | ostrzeżenie |
| `expired` | peer stracił ważność i został zawieszony | informacja |
| `window_open` | okno terminowe peera się otworzyło (w dzienniku) | — |
| `backup` | kopia zapasowa się nie powiodła | alarm |
| `apply` | automatyczne zastosowanie zmian się nie powiodło | alarm |

`new_endpoint` jest najważniejszy — to pierwszy sygnał, że plik konfiguracyjny wyciekł. Zmiana
samego portu jest ignorowana (normalne przy NAT), liczy się zmiana adresu. Ten sam alert nie
powtarza się częściej niż co `alerts_quiet_minutes`.

Kanały: **ntfy** (POST z nagłówkami Title/Priority/Tags) albo **webhook** (JSON). Bez SMTP.

## Kopie zapasowe

Kopia to jeden JSON: ustawienia, tiery, peery, reguły i konta. Z niej odtworzysz panel na czystej
karcie — `wg0.conf` i reguły iptables zostaną wygenerowane od nowa przy pierwszym zastosowaniu.

Cele: **katalog na dysku** (może być punkt montowania udziału) albo **NAS przez SMB**
(`smbclient`, hasło przekazywane przez zmienną środowiskową, nie w argumentach — żeby nie
wyciekło do listy procesów).

⚠ Kopia zawiera PSK wszystkich peerów. Jeśli ma leżeć na udziale sieciowym, ustaw hasło w polu
„hasło kopii" — plik zostanie zaszyfrowany. Bez hasła trafia tam jawnym tekstem.

Konstrukcja szyfrowania: scrypt (N=2¹⁵) → dwa klucze, ChaCha20 (RFC 8439) do szyfrowania,
HMAC-SHA256 do uwierzytelnienia szyfrogramu. Implementacja sprawdzona wektorami testowymi z RFC.

**Sumy kontrolne.** Każda kopia dostaje SHA-256 zapisane w bazie i w pliku `.sha256` obok kopii,
w formacie `sha256sum -c`. Zaraz po zapisie panel pobiera kopię z miejsca, w którym faktycznie
leży — także z NAS — i sprawdza sumę; przy zaszyfrowanej kopii dodatkowo ją otwiera. Po
każdej kopii dobowej sprawdzane są też trzy poprzednie, co wyłapuje późniejsze uszkodzenia albo
podmianę pliku na NAS-ie. Niezgodność to alarm.

Hasła SMB i hasło kopii **nie wychodzą w eksporcie** — po odtworzeniu na nowej maszynie trzeba
je wpisać raz jeszcze.

## Warstwy bezpieczeństwa

| Warstwa | Przed czym chroni |
|---|---|
| Rozdział procesów | przejęcie panelu nie daje roota |
| Lista dozwolonych operacji | helper nie zna pojęcia „wykonaj polecenie" |
| Walidacja po stronie helpera | wstrzyknięcia w adresach, portach, komentarzach, nazwach łańcuchów |
| Argumenty jako lista, nigdy shell | metaznaki w danych są nieszkodliwe |
| Walidacja ścieżek SMB | wyjście poza katalog na udziale |
| scrypt, blokada prób, opcjonalny TOTP | zgadywanie hasła |
| Throttling logowania | 5 prób na 5 minut z adresu |
| Token CSRF na każdym POST | żądania z obcych stron |
| Ciasteczko HttpOnly + SameSite=Strict | kradzież sesji |
| Bind tylko na wg0 | dostęp z internetu |
| Hardening jednostek systemd | skutki włamania na proces webowy |
| Klucz serwera poza bazą | wyciek bazy nie oddaje tożsamości serwera |
| Kasowanie kluczy klientów | wyciek bazy nie oddaje tożsamości peerów |
| Szyfrowanie kopii | wyciek pliku z udziału sieciowego |
| Alert o zmianie adresu | wykrycie wycieku pliku konfiguracyjnego |
| Kontrola integralności | ręczne zmiany konfiguracji z pominięciem panelu |
| Sumy kontrolne kopii | podmiana albo uszkodzenie kopii na NAS-ie |
| Wymagany prefiks LAN IPv6 | furtka w IPv6 obok blokady w IPv4 |


## Budowa i pliki

### Układ repozytorium

```
wg-panel/
├── panel/            panel WWW, baza, harmonogram, kopie, alerty, symulator
├── helper/           wg_panel_helper.py - jedyny komponent z rootem
├── systemd/          jednostki: helper, panel, strażnik (+ timer)
├── wgctl             CLI (te same operacje co panel)
├── install.sh        instalator (kopiuje program do /opt/wg-panel)
├── uninstall.sh      odinstalowanie (trzy poziomy)
└── docs/             dokumentacja PL i EN, zrzuty ekranu
```

### Elementy i kto co może

| Element | Usługa | Działa jako | Co robi |
|---|---|---|---|
| WireGuard | `wg-quick@wg0` | jądro systemu | sam tunel; działa także bez panelu |
| helper | `wg-panel-helper` | root | **jedyna część z uprawnieniami** — zapisuje pliki w `/etc/wireguard`, wywołuje `wg`, `iptables`, `systemctl`, pilnuje bezpiecznika |
| panel | `wg-panel` | konto `wg-panel` | strony WWW, baza, harmonogram, kopie, alerty; nie ma roota — o wszystko, co wymaga uprawnień, prosi helpera |
| strażnik | `wg-panel-guard.timer` | konto `wg-panel` | co minutę: terminy dostępu i sprawdzenie, czy wszystko działa (Krok 18a) |
| `wgctl` | — | Ty (grupa `wg-panel`) | te same operacje co panel, z terminala |

Podział na panel bez uprawnień i mały helper z rootem jest celowy: nawet gdyby ktoś przejął stronę WWW, może poprosić helpera tylko o kilka ściśle sprawdzanych rzeczy, a nie wykonać dowolne polecenie jako root.

### Jak się ze sobą łączą

```
 Twoje urządzenia                  Raspberry Pi
 przeglądarka ── HTTPS :8080 ────▶ panel (wg-panel)
                                     │  baza /var/lib/wg-panel/panel.db
 wgctl (terminal) ─────────────────▶ │
 strażnik (co minutę) ─────────────▶ │
                                     ▼  gniazdo /run/wg-panel/helper.sock
                                   helper (root)
                                     │  zapisuje wg0.conf i reguły,
                                     │  wywołuje wg, iptables, systemctl
                                     ▼
 aplikacja WireGuard ─ UDP :51820 ▶ WireGuard (wg-quick@wg0)
                                     │
                                     ▼
                                   sieć domowa
 NAS ◀── SMB: kopie ────────── panel
 telefon  ◀── ntfy: alerty ──────── panel i strażnik
```

### Struktura katalogów po instalacji

```
/opt/wg-panel/                program (root, tylko odczyt)
├── panel/                    panel WWW, harmonogram, kopie
├── helper/                   helper - jedyna część z rootem
├── wgctl                     polecenie terminalowe
└── uninstall.sh              odinstalowanie
/usr/local/bin/wgctl          nakładka - wgctl bez ścieżek

/etc/systemd/system/
├── wg-panel-helper.service   helper (root)
├── wg-panel.service          panel WWW (konto wg-panel)
├── wg-panel-guard.service    strażnik i terminy dostępu
└── wg-panel-guard.timer      uruchamia strażnika co minutę

/var/lib/wg-panel/            dane panelu (konto wg-panel)
├── panel.db                  baza - źródło prawdy
├── panel.db.schedule.lock    blokada: panel i strażnik nie naraz
├── backups/                  kopie na karcie (+ .sha256)
├── tls/                      certyfikat panelu, urząd do pobrania
└── watchdog.json             pamięć strażnika

/run/wg-panel/helper.sock     gniazdo helpera (tworzone przy starcie)

/etc/wireguard/               tylko root
├── wg0.conf                  konfiguracja WireGuard - z panelu
├── wg0-panel-rules.v4, .v6   reguły zapory - wczytuje je wg0.conf
├── server_private.key        klucz serwera (kopia w menedżerze haseł)
├── wg-panel-backups/         pliki sprzed każdej zmiany
├── wg-panel-ca/              urząd certyfikacji panelu
└── wg-panel-pending.json     zmiana czekająca na potwierdzenie

/etc/sysctl.d/
├── 99-wg-panel-forward.conf  przekazywanie pakietów (VPN → LAN)
└── 99-wg-panel-ipv6.conf     IPv6 w tunelu (gdy włączone)

/root/wg-panel-before/         kopia sprzed wdrożenia (Krok 2)
NAS (udział backup)/wg-panel/ kopie na NAS-ie (+ .sha256)
```

**Źródłem prawdy jest baza.** Pliki w `/etc/wireguard` panel generuje z bazy przy każdym „Zastosuj zmiany" — ręczna zmiana w `wg0.conf` zniknie przy najbliższym zastosowaniu (a strażnik zgłosi ją jako zmianę poza panelem).

### Program — co jest w którym pliku

| Plik | Co robi |
|---|---|
| `panel/app.py` | serwer WWW: adresy stron, logowanie, HTTPS, zastosowanie i potwierdzanie zmian |
| `panel/ui.py` | wygląd stron (HTML), `panel/static/style.css` — style |
| `panel/i18n.py` | języki: polski i angielski |
| `panel/db.py` | baza SQLite: tabele, ustawienia domyślne, porządki po starszych wersjach |
| `panel/model.py` | co dokładnie idzie do helpera przy zastosowaniu |
| `panel/render.py` | generowanie `wg0.conf`, reguł zapory i plików dla klientów |
| `panel/schedule.py`, `scheduler.py` | terminy dostępu; wątek w tle: alerty, kopie dobowe, integralność |
| `panel/watchdog.py` | strażnik |
| `panel/notify.py` | powiadomienia: ntfy, webhook, e-mail |
| `panel/export.py`, `cryptobox.py` | kopie zapasowe, szyfrowanie, NAS, odtwarzanie |
| `panel/auth.py`, `wgcrypto.py` | sesje, CSRF, blokada po nieudanych logowaniach; klucze WireGuard i TOTP |
| `panel/simulate.py` | symulator reguł |
| `panel/traffic.py` | historia ruchu i wykrywanie anomalii |
| `panel/qr.py`, `qrcode.py` | kody QR dla telefonów |
| `panel/import_conf.py` | jednorazowy import starego `wg0.conf` (ścieżka A) |
| `panel/helperclient.py` | rozmowa panelu z helperem przez gniazdo |
| `helper/wg_panel_helper.py` | helper (root) |

### Co się dzieje po „Zastosuj zmiany"

1. Panel buduje z bazy pełny obraz: osoby, klucze, tiery, reguły, ustawienia — i wysyła go do helpera.
2. Helper odkłada obecne pliki do `wg-panel-backups/`, generuje nowe `wg0.conf` i reguły.
3. Wprowadza je bez zrywania połączeń: `wg syncconf` (osoby) i `iptables-restore` (reguły).
4. Uzbraja bezpiecznik (domyślnie 90 s) i zapisuje `wg-panel-pending.json`.
5. Przeglądarka sama potwierdza zmianę **nowym połączeniem** — jeśli dojdzie, zmiana zostaje. Jeśli nie (np. reguła odcięła Ci dostęp), po czasie helper przywraca poprzednie pliki.

### Co się dzieje przy starcie Pi

1. `wg-quick@wg0` podnosi tunel z `wg0.conf`; jego `PostUp` tworzy łańcuchy zapory i wczytuje reguły — **VPN działa, zanim wstanie panel**.
2. `wg-panel-helper` — jeśli przed restartem zmiana czekała na potwierdzenie, daje świeże okno i cofa ją, gdy nikt nie potwierdzi.
3. `wg-panel` — panel WWW; sprawdza certyfikat HTTPS.
4. `wg-panel-guard.timer` — strażnik pierwszy raz 2 minuty po starcie, potem co minutę.

### Gdzie szukać, gdy coś nie działa

| Czego szukasz | Polecenie |
|---|---|
| panel WWW (błędy stron, logowanie) | `journalctl -u wg-panel -f` |
| helper (zastosowanie zmian, pliki, certyfikat) | `journalctl -u wg-panel-helper -f` |
| strażnik i terminy dostępu | `journalctl -u wg-panel-guard -n 20` |
| sam WireGuard (start tunelu) | `journalctl -u wg-quick@wg0 -n 50` |
| kto co zmienił w panelu | panel → **Dziennik** |

`journalctl -u USŁUGA` pokazuje zapisy danej usługi. `-f` śledzi je na bieżąco (wyjście: **Ctrl+C**), `-n 20` pokazuje ostatnie 20 linii. Instalator wypisuje te polecenia na końcu tylko jako ściągę — nic nie musisz uruchamiać, dopóki wszystko działa.

---

## Czy da się dalej pracować tekstowo

Tak. `wgctl` pokrywa wszystko, co robi panel. Poza tym:

- **baza to zwykły SQLite** — `sqlite3 /var/lib/wg-panel/panel.db` działa, zmiany zobaczysz
  w panelu po odświeżeniu, a na interfejsie po `wgctl apply`;
- **iptables poza łańcuchem `WG-USERS`** panel zostawia w spokoju — doraźna reguła w `FORWARD`
  przeżyje, bo czyszczone są tylko własne łańcuchy;
- **`wg set`** działa jak zawsze, z tym samym zastrzeżeniem: do restartu;
- **`wg0.conf` jest generowany** — to jedyne miejsce, którego nie ma sensu edytować ręcznie.


## Znane ograniczenia

- **Domyślne ustawienia zakładają typową sieć domową** (`192.168.1.0/24`, NAS `192.168.1.11`, Pi `192.168.1.10`, port WireGuarda `51820`). Sprawdź je w Ustawieniach przed pierwszym zastosowaniem — przede wszystkim podsieć LAN (Krok 9 instrukcji).
- **`wgctl`, treść powiadomień i komentarze w kodzie są po polsku.** Interfejs panelu i instalator mają polski i angielski.
- W symulatorze komentarze wbudowanych reguł tierów (np. „reszta LAN zablokowana”) pokazują się po polsku także w interfejsie angielskim.
- Testowane na Raspberry Pi OS (Debian). Inne dystrybucje z systemd, `iptables` i WireGuardem powinny działać, ale nie były sprawdzane.
- Tylko IPv4 jest włączone domyślnie; IPv6 to opcja (sekcja IPv6 wyżej).

## Pomysły na rozwój

1. **Dziennik wysyłany poza Pi** — kopia audytu na NAS, której nie da się wyczyścić z Pi.
2. **Powiadomienie o końcu okna na kanał alertów** — dziś plakietka w panelu.
3. **Auto-wykrywanie sieci przy instalacji** — podsieć LAN, adres Pi i interfejs WAN podpowiadane zamiast wartości przykładowych.
4. **Angielskie komunikaty `wgctl` i powiadomień.**
