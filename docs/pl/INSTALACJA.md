# wg-panel — instrukcja instalacji i obsługi

*[English version → `docs/en/INSTALL.md`](../en/INSTALL.md)*

Ten przewodnik prowadzi krok po kroku przez instalację panelu na Raspberry Pi (albo innym Debianie/Ubuntu z systemd), przez uruchomienie VPN, powiadomienia i kopie zapasowe, aż po codzienną obsługę i rozwiązywanie problemów. Budowa wewnętrzna: [ARCHITEKTURA.md](ARCHITEKTURA.md).

> **Panel już u Ciebie działa?** Nie przechodź ścieżek od nowa — nową wersję wgrywasz według sekcji „Aktualizacja panelu” na końcu instrukcji. To dwa polecenia, osoby i ustawienia zostają.

> **Adresy w tej instrukcji są przykładowe:**
>
> | Element | Przykład |
> |---|---|
> | sieć domowa (LAN) | `192.168.1.0/24` |
> | Raspberry Pi w LAN-ie | `192.168.1.10` |
> | NAS / udział sieciowy SMB | `192.168.1.11` |
> | sieć VPN / adres Pi w VPN | `10.0.0.0/24` / `10.0.0.1` |
> | port WireGuarda | `51820/udp` |
> | interfejs do internetu | `eth0` |
>
> **Podstaw swoje.** Sieć domową, adres Pi i interfejs instalator wykrywa sam; port i sieć VPN przy przejęciu bierze z Twojego `wg0.conf`. Sprawdź je w Ustawieniach (Krok 9) — reguła „cała sieć domowa zablokowana” działa tylko wtedy, gdy panel wie, jaka jest Twoja sieć.

# Jak czytać tę instrukcję

## Najpierw wybierz ścieżkę

| Sytuacja | Ścieżka | Co robisz |
|---|---|---|
| masz już działający serwer WireGuard na Pi (`/etc/wireguard/wg0.conf`) i chcesz go „przejąć” panelem, bez zmian u klientów | **A** | Kroki 1–21 |
| czysty system, pierwszy raz stawiasz VPN albo zaczynasz od nowa z nowymi kluczami | **B** | Kroki B1–B13, potem 18–21 |
| czysty system (nowa karta SD, nowe Pi), a masz kopię panelu na NAS-ie | **C** | Kroki C1–C5, potem 21 |

> **Czym różnią się B i C dla użytkowników.** W B powstaje nowy klucz serwera, więc każda osoba musi dostać nowy plik. W C z pełnej kopii wracają stary klucz i wszystkie osoby — nikt niczego nie zmienia u siebie.

Kroki wykonujesz po kolei, bez przeskakiwania. Części V–VII to materiał do sięgania w razie potrzeby.

Każdy krok zaczyna się od linii **Gdzie**, która mówi, przy czym siedzisz:

| Oznaczenie | Znaczy |
|---|---|
| **Pi** | terminal na Raspberry Pi — przez SSH z komputera w domu |
| **Komputer** | terminal na Twoim komputerze w domowej sieci |
| **Przeglądarka** | panel otwarty w przeglądarce na komputerze |
| **Telefon** | Twój telefon |
| **NAS** | panel administracyjny Twojego NAS-a (w przykładach: Synology DSM) |

Pod komendą jest opis tego, co masz zobaczyć. Cytat (wcięty, z kreską po lewej) mówi, dlaczego coś robimy — przy wdrażaniu możesz go pominąć. Cytat zaczynający się od ⚠ to rzecz, której pominąć nie wolno.

> ⚠ Całe wdrożenie rób **w domu**, podłączony do domowej sieci, nie przez VPN. Zmieniasz konfigurację tego samego VPN-u — dostęp z LAN-u to Twoja droga awaryjna.

> **Cel kopii zapasowych.** Kopie idą przez SMB na dowolny udział sieciowy — dowolny NAS albo serwer Samby na komputerze z Linuksem.

> **Co się dzieje z tierami.** Panel ma trzy wbudowane poziomy dostępu: **ADMIN** (cały LAN, SSH i RDP na Pi, panel, cały ruch przez VPN), **NET** (tylko internet przez VPN, LAN zablokowany) i **NAS** (tylko jeden host — NAS — na portach 80, 443, 445; reszta ruchu idzie przez własny internet osoby). Możesz je zmieniać i dodawać własne.

---

# ŚCIEŻKA A — przejęcie istniejącej konfiguracji WireGuarda

Panel przejmuje te same klucze i te same adresy, więc **klienci niczego nie zmieniają** — nikt nie dostaje nowego pliku ani nie skanuje nowego kodu.

> **Co się zmienia u Ciebie:** `wg0.conf` przestaje być plikiem, który edytujesz ręcznie — od pierwszego zastosowania zmian generuje go panel. Reguły z `PostUp` zastępują dwa łańcuchy panelu (`WG-USERS`, `WG-USERS-HOST`). Reguły ufw dla SSH i RDP zostają bez zmian.


# CZĘŚĆ I — Przygotowanie

## Krok 1 — Sprawdź, czy Pi ma wszystko

**Gdzie: Pi**

```bash
python3 -V
command -v wg wg-quick iptables
sudo wg show wg0 | head -5
sudo apt install -y smbclient
```

Masz zobaczyć: Python 3.9 lub nowszy, trzy ścieżki do programów, działający interfejs `wg0` z listą peerów.

> ⚠ Sprawdź na routerze, że Pi ma **rezerwację DHCP** na `192.168.1.10`. Na ten adres router przekierowuje port `51820` — gdyby Pi po restarcie dostało inny adres, VPN przestanie działać dla wszystkich.

> **smbclient** jest potrzebny tylko do kopii zapasowych na NAS. Nic innego nie trzeba instalować — kody QR panel robi sam.

## Krok 2 — Zrób kopię obecnego stanu

**Gdzie: Pi**

```bash
sudo mkdir -p /root/wg-panel-before
sudo cp /etc/wireguard/wg0.conf /root/wg-panel-before/
sudo iptables-save | sudo tee /root/wg-panel-before/iptables.rules >/dev/null
sudo ufw status verbose | sudo tee /root/wg-panel-before/ufw.txt >/dev/null
sudo ls -l /root/wg-panel-before/
```

Masz zobaczyć trzy pliki: `wg0.conf`, `iptables.rules`, `ufw.txt`.

> **Dlaczego:** to Twoja droga powrotna. Gdyby panel Ci nie pasował, opcja `--restore` odinstalowania (część VI) przywraca dokładnie ten stan.

> Instalator robi tę kopię sam, jeśli w `/root/wg-panel-before` jeszcze jej nie ma. Ręczna kopia przed instalacją nie zaszkodzi — instalator jej nie nadpisze.

## Krok 3 — Zapisz klucz prywatny serwera poza Pi

**Gdzie: Pi, potem menedżer haseł**

```bash
sudo cat /etc/wireguard/server_private.key
```

Skopiuj wynik do menedżera haseł (np. Bitwarden, KeePassXC, 1Password) jako wpis „WireGuard — klucz prywatny serwera".

> ⚠ Klucz jest w **pełnej kopii** panelu (kopia z hasłem — Krok 20), ale nie w podstawowej. Trzymaj go też w menedżerze haseł — gdyby karta SD padła, a nie byłoby ani pełnej kopii, ani klucza, wszyscy użytkownicy VPN musieliby dostać nowe pliki.


## Krok 4 — Pobierz panel na Pi

**Gdzie: Pi**

```bash
sudo apt install -y git
git clone https://github.com/mt3ch02/wg-panel.git ~/wg-panel
```

> Bez Gita: pobierz archiwum z GitHuba (Code → Download ZIP albo strona Releases), skopiuj je na Pi przez `scp` i rozpakuj do `~/wg-panel`.

---


# CZĘŚĆ II — Instalacja

## Krok 5 — Zainstaluj panel

**Gdzie: Pi**

```bash
cd ~/wg-panel
sudo INSTALL_LANG=pl ./install.sh
```

Na końcu masz zobaczyć podsumowanie z adresami panelu i napis „ścieżka A (przejęcie konfiguracji)".

> **Co zrobił instalator:** założył konto usługi `wg-panel`, skopiował program do `/opt/wg-panel`, utworzył katalog danych `/var/lib/wg-panel`, uruchomił dwie usługi i strażnika (timer co minutę — Krok 18a) i dodał regułę ufw wpuszczającą panel tylko z VPN.

> **Język instalatora:** w tej instrukcji podajemy `INSTALL_LANG=pl`, więc komunikaty są po polsku. Bez tej zmiennej instalator i skrypt odinstalowania mówią po angielsku (i pytają o `yes` zamiast `tak`).

> **Instalacja niczego jeszcze nie zmieniła w VPN.** `wg0.conf` jest nietknięty, wszyscy są połączeni jak byli.

## Krok 6 — Sprawdź, czy usługi działają

**Gdzie: Pi**

```bash
systemctl is-active wg-panel-helper wg-panel wg-panel-guard.timer
```

Masz zobaczyć trzy razy `active`.

> ⚠ Jeśli któraś usługa jest `failed`, nie idź dalej. Uruchom `journalctl -u wg-panel-helper -n 40` (albo `-u wg-panel`) — ostatnie linie mówią, czego brakuje.

> **Dlaczego dwie usługi:** strona internetowa panelu działa bez uprawnień administratora. Wszystko, co wymaga roota — zmiana `wg0.conf` i reguł — robi osobny, mały pomocnik, który przyjmuje tylko z góry określone polecenia. Przejęcie strony nie daje roota.

## Krok 7 — Otwórz panel z domowej sieci

**Gdzie: Komputer, potem Przeglądarka**

Domyślnie panel nie jest wystawiony na sieć domową — słucha na adresie VPN `10.0.0.1` i na samym Pi. Z domu wchodzisz wtedy przez tunel SSH:

```bash
ssh -L 8080:127.0.0.1:8080 pi@192.168.1.10
```

Zostaw to okno otwarte i otwórz `http://localhost:8080` (przez tunel SSH zostaje zwykłe HTTP — ruch i tak idzie szyfrowanym SSH, a certyfikat nie obejmuje adresu `localhost`).

**Albo dostęp z całej sieci domowej**, bez tunelu — wtedy instalujesz panel tak:

```bash
sudo INSTALL_LANG=pl LAN_ACCESS=192.168.1.0/24 ./install.sh
```

Panel nasłuchuje wtedy na wszystkich adresach Pi, a wpuszcza go zapora — tylko z sieci domowej. Wchodzisz na `https://192.168.1.10:8080` (stary adres z `http://` sam przekieruje na `https://`). Z VPN panel otworzą wyłącznie osoby, którym to włączysz (Krok 12b).

> **Czym to płacisz:** stronę logowania widzi każde urządzenie w Twoim Wi-Fi. Ruch jest szyfrowany (HTTPS), hasła nikt nie podejrzy, ale do czasu zainstalowania certyfikatu (Krok 7a) przeglądarka będzie ostrzegać. Chroni też blokada po pięciu nieudanych próbach logowania.

> **Zmiana zdania później** to ponowne uruchomienie instalatora z `LAN_ACCESS` albo bez niego — dane i ustawienia zostają nietknięte.

Masz zobaczyć stronę „Pierwsze uruchomienie".

> **Poza domem:** panel przez VPN otworzą tylko osoby, którym to wyraźnie włączysz. Zrób to sobie w Kroku 12b, inaczej spoza domu do panelu nie wejdziesz.

## Krok 7a — Zainstaluj certyfikat panelu

**Gdzie: każde urządzenie, z którego wchodzisz do panelu (telefon, komputer)**

Panel ma własny urząd certyfikacji — powstaje na Pi przy instalacji. Dopóki go nie zainstalujesz, przeglądarka ostrzega „połączenie nie jest prywatne". Zrób to raz na każdym urządzeniu:

1. Otwórz panel (przy pierwszym wejściu zaakceptuj ostrzeżenie — „Zaawansowane → Przejdź dalej") i kliknij pod formularzem logowania **Zainstaluj certyfikat panelu** (albo wejdź wprost na `/tls`).
2. **Porównaj odcisk SHA-256** na tej stronie z tym, który wypisał instalator na końcu (to samo pokaże `wgctl tls` na Pi). Muszą być identyczne — dopiero wtedy wiesz, że to Twój panel, a nie ktoś podszywający się w sieci.
3. Kliknij **Pobierz certyfikat** i zrób to, co opisuje strona dla Twojego systemu. Na iPhonie są dwa miejsca: instalacja profilu **i** osobne włączenie zaufania w Ustawieniach → Ogólne → Informacje → Ustawienia zaufania certyfikatów.

Masz zobaczyć: po odświeżeniu panelu kłódkę bez ostrzeżenia.

> **Czy to bezpieczne:** ten urząd może poświadczać wyłącznie adresy Twojej sieci domowej, VPN i nazwę Pi (ograniczenie nazw w certyfikacie). Nawet gdyby ktoś wykradł jego klucz z Pi, nie podrobi nim banku ani żadnej innej strony. Klucz urzędu leży w `/etc/wireguard/wg-panel-ca/` i czyta go tylko root.

> **Certyfikat panelu odnawia się sam** — na 30 dni przed wygaśnięciem i po zmianie adresu Pi. Na urządzeniach nic nie robisz. Wyjątek: zmiana podsieci domowej albo VPN w Ustawieniach tworzy nowy urząd (stary jej nie obejmuje) — wtedy zainstaluj go ponownie.

## Krok 8 — Załóż konto

**Gdzie: Przeglądarka**

Podaj nazwę (np. `admin`) i hasło co najmniej 10 znaków. Pole „Kod z aplikacji przy logowaniu" zostaw odznaczone. Zapisz hasło w menedżerze haseł, potem zaloguj się.

> **Hasło to jedyne zabezpieczenie panelu** przed kimś, kto dostał się do VPN albo do domowego Wi-Fi — niech będzie długie i losowe. Kod z aplikacji możesz włączyć później w Ustawieniach → Konto.

## Krok 9 — Wpisz ustawienia

**Gdzie: Przeglądarka → Ustawienia**

Sieć domową, adres Pi i interfejs instalator wykrył sam. Sprawdź je i uzupełnij resztę:

| Pole | Co wpisać |
|---|---|
| **Podsieć LAN** | Twoja sieć domowa, np. `192.168.1.0/24` — wykryte przy instalacji |
| Podsieć VPN | sieć tunelu, np. `10.0.0.0/24` |
| Adres serwera w VPN | adres Pi w tunelu, np. `10.0.0.1` |
| **Adres serwera w LAN** | stały adres Pi, np. `192.168.1.10` — wykryte przy instalacji |
| Interfejs WAN | interfejs Pi do internetu: `eth0`, `end0`, `wlan0`… — wykryte przy instalacji |
| Port nasłuchu | port WireGuarda — domyślnie `51820`; jeśli router przekierowuje inny, popraw (na ścieżce A import w Kroku 11 i tak wpisze go z `wg0.conf`) |
| DNS trybu full | np. `9.9.9.9` — Quad9: blokuje złośliwe domeny i nie profiluje zapytań |
| **Endpoint klientów** | publiczny adres i port, pod którym widać Twój dom, np. `203.0.113.10:51820` albo `home.duckdns.org:51820` (na ścieżce A: to samo, co w polu `Endpoint` w plikach klientów) |

Kliknij **Zapisz**. Potem zakładka **Hosty**: host `nas` ma mieć adres Twojego NAS-a (instalator podpowiada adres — sprawdź, czy to ten właściwy). Z niego korzysta tier NAS.

> ⚠ **Podsieć LAN musi się zgadzać z Twoją prawdziwą siecią.** Tier NAS kończy się regułą „zablokuj LAN”, a `LAN` to dokładnie ta podsieć z Ustawień. Przy błędnej wartości osoba z tierem NAS nie zostanie zablokowana w Twojej prawdziwej sieci. Sprawdź to symulatorem (Krok 13).

> **Endpoint** trafia tylko do nowych plików i kodów QR, które panel wyda w przyszłości. Obecnych klientów nie zmienia — ale jeśli wpiszesz tu zły adres, każdy nowy użytkownik dostanie niedziałający plik.

> **Podsieć VPN inna niż `10.0.0.0/24`?** Panel domyślnie słucha na `10.0.0.1`. Zainstaluj go z właściwym adresem: `sudo INSTALL_LANG=pl BIND_HOST=10.8.0.1 ./install.sh` (Krok 5).

---


# CZĘŚĆ III — Przejęcie obecnej konfiguracji

## Krok 10 — Import na próbę

**Gdzie: Pi**

```bash
cd /opt/wg-panel
sudo cat /etc/wireguard/wg0.conf | sudo -u wg-panel \
     WGPANEL_DB=/var/lib/wg-panel/panel.db \
     python3 -m panel.import_conf - --tier NAS --dry-run
```

Masz zobaczyć po jednej linii z `+` dla każdej osoby z `wg0.conf` (nazwa z komentarza, adres w VPN) i podsumowanie `do dodania: N`. Nic nie zostało zapisane.

> **Dlaczego przez `sudo cat`:** `wg0.conf` może czytać tylko root, a import działa jako konto panelu. `sudo cat` czyta plik z uprawnieniami roota i przekazuje go importowi — konto panelu nie dostaje przy tym dostępu do pliku.

> **Dlaczego wszyscy do NAS:** plik nie mówi, kto ma jakie uprawnienia — to są reguły w `PostUp`, nie przy peerach. Import daje wszystkim najwęższy poziom, a w Kroku 12 poprawiasz tych, którzy mają mieć więcej.

## Krok 11 — Import właściwy

**Gdzie: Pi**

Ta sama komenda, bez `--dry-run`:

```bash
sudo cat /etc/wireguard/wg0.conf | sudo -u wg-panel \
     WGPANEL_DB=/var/lib/wg-panel/panel.db \
     python3 -m panel.import_conf - --tier NAS
```

## Krok 12 — Nadaj poziomy dostępu i nazwy

**Gdzie: Pi**

Instalator dodał Twoje konto do grupy `wg-panel`, więc `wgctl` działa bez `sudo` — ale dopiero od nowego logowania (Linux wczytuje grupy przy logowaniu). Wyloguj się i zaloguj ponownie albo w bieżącym terminalu wpisz:

```bash
newgrp wg-panel
```

Potem przypisz poziomy (adresy i nazwy podstaw swoje):

```bash
wgctl list
wgctl set 10.0.0.2 --tier ADMIN --rename "Ja" --device "laptop"
wgctl set 10.0.0.3 --tier NET   --rename "Marek"
wgctl list
```

Masz zobaczyć: `10.0.0.2` z tierem ADMIN, `10.0.0.3` z NET, pozostałe nadal z NAS.

Nazwy możesz od razu zmienić na prawdziwe imiona i dopisać urządzenia: `wgctl set 10.0.0.4 --rename "Ola" --device "iPhone"`. To samo zrobisz w panelu, klikając w osobę.

> **Poziom ADMIN sam daje dostęp do SSH i RDP na Pi** (porty 22 i 3389).

> **Klucz współdzielony (PSK) jest opcjonalny.** Jeśli Twój `wg0.conf` go nie miał, panel też go nie dopisze i peery działają dalej bez zmian. PSK to dodatkowa warstwa na wypadek komputerów kwantowych — dodasz ją komuś przy okazji rotacji kluczy, bo wtedy i tak dostaje nowy plik.

## Krok 12b — Sprawdź, że masz dostęp do panelu przez VPN

**Gdzie: Przeglądarka → Tiery**

Przez VPN panel otwierają tylko osoby z tieru, który ma port panelu (`8080`) w polu **Porty na samym serwerze**. Domyślnie to tier **ADMIN** (`22,3389,8080`), a Ty jesteś w nim od Kroku 12 — nic nie musisz robić. Sprawdź tylko, że w karcie ADMIN jest `8080`.

> **Dlaczego tak:** panel jest dostępny z sieci domowej, a przez VPN tylko dla wybranych tierów. Reszta użytkowników dostaje blokadę na porcie panelu, mimo że są w tej samej sieci VPN. Bez tego, będąc poza domem, nie zalogujesz się do panelu.

## Krok 12a — Usuń osoby, które nie mają zostać

Jeśli w `wg0.conf` są osoby, których nie chcesz przenosić do panelu (np. dawne, nieużywane urządzenia):

```bash
wgctl delete 10.0.0.9 --yes
wgctl delete 10.0.0.10 --yes
wgctl list
```

Usunięte osoby znikną z `wg0.conf` przy najbliższym zastosowaniu zmian, a ich adresy wrócą do puli. Jeśli wolisz kogoś na razie zostawić na liście (np. do wyjaśnienia), użyj `wgctl suspend 10.0.0.9` — zawieszony nie ma dostępu, ale da się go wznowić jednym poleceniem.

> **Dlaczego przez panel, a nie edycją pliku:** zostaje ślad w dzienniku, kto i kiedy to zrobił, a zawieszone osoby zostają w panelu na wypadek pytania „czemu mi nie działa".

> ⚠ Po zastosowaniu zmian ich pliki przestaną działać. Uprzedź te osoby wcześniej.

> **Osoby z tierem NAS mają w swoich (starych) plikach zwykle cały LAN** w `AllowedIPs`, a panel dla nowych plików wylicza już tylko NAS. Obecne pliki działają dalej bez zmian — obejmują więcej, niż trzeba, a serwer i tak wpuszcza je tylko do NAS-a. Nie musisz nikomu nic wysyłać.

## Krok 13 — Sprawdź uprawnienia symulatorem

**Gdzie: Pi**

Zanim cokolwiek zastosujesz, sprawdź, czy panel przepuści i zablokuje dokładnie to, co chcesz. Składnia: `wgctl check KTO CEL PORT` (KTO — nazwa albo adres w VPN). Przykłady dla układu z Kroku 12 (adresy podstaw swoje):

```bash
wgctl check Ja        192.168.1.10 22     # ADMIN -> SSH na Pi
wgctl check Marek     1.1.1.1       443    # NET   -> internet
wgctl check Marek     192.168.1.11 445    # NET   -> NAS (LAN zablokowany)
wgctl check 10.0.0.4  192.168.1.11 445    # NAS   -> NAS
wgctl check 10.0.0.4  192.168.1.11 22     # NAS   -> zły port
wgctl check 10.0.0.4  192.168.1.20 22     # NAS   -> inny host w LAN
wgctl check 10.0.0.4  10.0.0.1      22     # NAS   -> SSH na Pi
```

Oczekiwane wyniki, po kolei: ACCEPT, ACCEPT, DROP, ACCEPT, DROP, DROP, DROP.

> **Co symulator sprawdza:** przechodzi reguły dokładnie tak, jak zrobi to jądro, i mówi, która reguła zadecydowała. Wynik inny niż oczekiwany znaczy, że coś w Krokach 9–12 jest źle (najczęściej podsieć LAN w Ustawieniach) — popraw to teraz, zanim cokolwiek zmieni się na żywo.

## Krok 14 — Obejrzyj, co się zmieni

**Gdzie: Przeglądarka → Peery → karta „Panel ma zmiany…” → Szczegóły → „Pełny podgląd zmian”**

Sprawdź w podglądzie:

- nowy `wg0.conf` ma tyle sekcji `[Peer]`, ile osób zostało, z tymi samymi kluczami publicznymi;
- w regułach jest `-d <adres NAS>/32` z portami `80,443,445` dla osób z tierem NAS;
- w łańcuchu `WG-USERS-HOST` jest reguła ACCEPT dla osób z tierem ADMIN na portach `22,3389`.

## Krok 15 — Zastosuj zmiany z bezpiecznikiem

**Gdzie: Przeglądarka, plus drugie okno terminala na Pi**

Przy pierwszym zastosowaniu warto sprawdzić wszystko **przed** potwierdzeniem, więc najpierw przestaw panel na ręczne potwierdzanie: **Ustawienia → Zaawansowane → Potwierdzanie zmian: ręcznie → Zapisz**. (Na co dzień zostaw „automatycznie" — panel sam sprawdza, czy nadal masz połączenie, i potwierdza w sekundę.)

Kliknij **Zastosuj zmiany**. Od tej chwili masz 90 sekund: u góry każdej strony wisi żółty pasek z licznikiem (w ostatnich 15 sekundach robi się czerwony).

W drugim oknie terminala na Pi sprawdź:

```bash
sudo wg show wg0 | grep -c peer
sudo iptables -L WG-USERS -n | head -5
```

Masz zobaczyć liczbę osób (tyle, ile w `wgctl list`) i początek łańcucha panelu. Jeśli wszystko się zgadza, wróć do przeglądarki i kliknij na pasku **„Potwierdzam, działa"** — pasek znika od razu, a panel zostaje na tej stronie, na której jesteś. Teraz możesz wrócić do automatycznego potwierdzania w Ustawieniach.

> ⚠ Jeśli coś wygląda źle — nic nie klikaj albo kliknij na pasku **„Cofnij teraz"**. Po 90 sekundach panel sam przywróci poprzednią konfigurację i powie to wprost na pasku. Brak potwierdzenia jest bezpieczny; potwierdzenie zepsutej konfiguracji nie jest.

## Krok 16 — Sprawdź z prawdziwych urządzeń

**Gdzie: Telefon**

Wyłącz na telefonie Wi-Fi (żeby łączyć się z zewnątrz, przez sieć komórkową) i:

1. Połącz się VPN-em jako administrator i otwórz `https://10.0.0.1:8080` — panel ma się otworzyć.
2. Jeśli możesz, poproś jedną osobę z tierem NAS, żeby weszła na NAS — ma działać.

> **Dlaczego z sieci komórkowej:** z domowego Wi-Fi część routerów nie łączy się sama ze sobą po publicznym adresie. Test z zewnątrz to jedyny test, który pokazuje, jak VPN działa naprawdę.

## Krok 17 — Uruchom Pi ponownie

**Gdzie: Pi**

Reguły ze starego `wg0.conf` nadal siedzą w pamięci — panel ich nie widzi i nie usuwa, bo zostały dodane przed nim. Nie szkodzą, bo reguły panelu są sprawdzane pierwsze, ale najczyściej jest je wyczyścić restartem:

```bash
sudo reboot
```

Po minucie połącz się ponownie i sprawdź:

```bash
systemctl is-active wg-quick@wg0 wg-panel-helper wg-panel
wgctl status
sudo iptables -S FORWARD | head -4
```

Masz zobaczyć: trzy razy `active`; w `wgctl status` brak niezastosowanych zmian; w `FORWARD` skok do `WG-USERS` i brak starych reguł z adresami `10.0.0.x`.

> **Dlaczego to bezpieczne:** po restarcie `wg-quick` czyta już plik wygenerowany przez panel. Reguły ufw i NAT z `/etc/ufw/before.rules` wracają same, bo ufw zapisuje je trwale.

**Dalej: Krok 18** (część IV). Ścieżki B i C pomiń — są dla czystego systemu.

---

# ŚCIEŻKA B — czysty system, nowy VPN

Stawiasz WireGuarda od zera. Na końcu wszystkie osoby dostają nowe pliki.

## Krok B1 — System

**Gdzie: Komputer, potem Pi**

1. Nagraj **Raspberry Pi OS Lite (64-bit)** programem Raspberry Pi Imager. W ustawieniach Imagera włącz SSH i ustaw użytkownika z hasłem.
2. Włóż kartę, uruchom Pi podłączone kablem do routera.
3. Na routerze ustaw **rezerwację DHCP**: Pi zawsze dostaje `192.168.1.10`.
4. Zaloguj się i zaktualizuj system:

```bash
ssh pi@192.168.1.10
sudo apt update && sudo apt full-upgrade -y
sudo reboot
```

## Krok B2 — Pakiety

**Gdzie: Pi**

```bash
sudo apt install -y wireguard iptables ufw smbclient
command -v wg wg-quick iptables ip6tables ufw smbclient
```

Masz zobaczyć sześć ścieżek.

## Krok B3 — Klucz serwera

**Gdzie: Pi, potem menedżer haseł**

```bash
sudo mkdir -p /etc/wireguard
sudo chmod 700 /etc/wireguard
wg genkey | sudo tee /etc/wireguard/server_private.key >/dev/null
sudo chmod 600 /etc/wireguard/server_private.key
sudo cat /etc/wireguard/server_private.key
```

Skopiuj wynik do menedżera haseł jako „WireGuard — klucz prywatny serwera".

> ⚠ Ten klucz to tożsamość Twojego VPN. Jeśli go stracisz, wszyscy muszą dostać nowe pliki. Jeśli wycieknie — ktoś może podszyć się pod Twój serwer. Jest tylko w pełnej kopii panelu (z hasłem kopii), w podstawowej go nie ma — dlatego drugi egzemplarz trzymaj w menedżerze haseł.

## Krok B4 — Zapora

**Gdzie: Pi**

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow from 192.168.1.0/24 to any port 22 proto tcp
sudo ufw allow 51820/udp
sudo ufw enable
sudo ufw status verbose
```

Masz zobaczyć: `deny (incoming)`, reguła dla portu 22 z `192.168.1.0/24` i reguła dla `51820/udp`.

> **Dlaczego tylko tyle:** z internetu otwarty jest wyłącznie port WireGuarda, SSH działa tylko z domowej sieci. Dostęp przez VPN — do NAS, do SSH i RDP na Pi — ustawia panel we własnych łańcuchach. Przekazywanie ruchu zostaje w ufw zablokowane: jeśli łańcuchy panelu z jakiegoś powodu znikną, VPN przestanie przepuszczać ruch, zamiast przepuszczać wszystko.

> Jeśli używasz pulpitu zdalnego (xRDP) na Pi z domowej sieci, dodaj: `sudo ufw allow from 192.168.1.0/24 to any port 3389 proto tcp`.

## Krok B5 — Router

**Gdzie: panel administracyjny routera**

1. Przekierowanie portu: **UDP 51820 → 192.168.1.10:51820**.
2. Zapisz publiczny adres, pod którym widać Twój dom: stały IP od dostawcy albo nazwa DDNS (np. `home.duckdns.org`). To będzie **Endpoint** w Kroku B9.

> Jeśli dostawca zmienia Ci publiczny IP, ustaw DDNS — inaczej po zmianie adresu VPN przestanie działać wszystkim naraz.

## Krok B6 — Skopiuj i zainstaluj panel

**Gdzie: Komputer, potem Pi**

Wykonaj **Krok 4** i **Krok 5** ze ścieżki A. Instalator ma napisać „ścieżka B/C (nowy system)".

## Krok B7 — Sprawdź usługi

**Gdzie: Pi**

```bash
systemctl is-active wg-panel-helper wg-panel
journalctl -u wg-panel -n 5 --no-pager
```

Masz zobaczyć dwa razy `active`, a w logu: `10.0.0.1 jeszcze nie istnieje (wg0 nie dziala) - czekam`. To jest prawidłowe — tunelu jeszcze nie ma, panel podniesie go w Kroku B11.

## Krok B8 — Otwórz panel i załóż konto

**Gdzie: Komputer, potem Przeglądarka**

Wykonaj **Krok 7** i **Krok 8** ze ścieżki A.

## Krok B9 — Ustawienia

**Gdzie: Przeglądarka → Ustawienia**

Tak jak **Krok 9** ze ścieżki A. Najważniejsze pola to **Endpoint klientów** — adres z Kroku B5 z portem, np. `home.duckdns.org:51820` — i **Port nasłuchu**: ten sam, który przekierowuje router (domyślny `51820`). Sprawdź też **Adres serwera w LAN**.

## Krok B10 — Dodaj osoby

**Gdzie: Pi (albo Przeglądarka → Peery → Dodaj peera)**

Przykładowy układ (nazwy, poziomy i adresy podstaw swoje):

```bash
wgctl add "Ja"    --tier ADMIN --ip 10.0.0.2 --device laptop
wgctl add "Marek" --tier NET   --ip 10.0.0.3
wgctl add "Ola"   --tier NAS   --ip 10.0.0.4 --device iPhone
wgctl list
```

`wgctl` bez `sudo` działa od nowego logowania (instalator dodał Cię do grupy `wg-panel`) — albo od razu po `newgrp wg-panel` w bieżącym terminalu.

Sprawdź symulatorem tak samo jak w **Kroku 13**.

## Krok B11 — Pierwsze zastosowanie — podniesienie tunelu

**Gdzie: Przeglądarka → Peery → Zastosuj zmiany**

Przestaw **Ustawienia → Zaawansowane → Potwierdzanie zmian** na **ręcznie** (jak w Kroku 15), potem kliknij **Zastosuj zmiany**. Panel zapisze `wg0.conf`, włączy przekazywanie pakietów, włączy tunel przy starcie systemu i podniesie `wg0`. W drugim oknie terminala:

```bash
ip -brief addr show wg0
sudo wg show wg0 | grep -c peer
```

Masz zobaczyć `wg0` z adresem `10.0.0.1/24` i liczbę osób z `wgctl list`. Wróć do przeglądarki i kliknij na pasku u góry **Potwierdzam, działa**, a potem przestaw potwierdzanie z powrotem na **automatycznie**.

> Bez potwierdzenia panel po 90 sekundach cofnie zmianę — na czystym systemie oznacza to zatrzymanie tunelu. Nic się nie psuje, po prostu zastosujesz jeszcze raz.

## Krok B12 — Rozdaj pliki

**Gdzie: Przeglądarka → Peery → każda osoba**

Dla każdej osoby: **kod QR**, jeśli jest obok, albo **Kopiuj plik** i wyślij szyfrowanym komunikatorem. Gdy potwierdzi, że działa — **Wydano — skasuj klucz**. Szczegóły w części V, „Wysłanie pliku konfiguracyjnego".

Zacznij od siebie — swój plik zaimportuj na telefonie.

## Krok B13 — Test i restart

**Gdzie: Telefon, potem Pi**

Zrób **Krok 16** ze ścieżki A (test z sieci komórkowej), a potem sprawdź, czy wszystko wstaje samo:

```bash
sudo reboot
```

Po minucie połącz się telefonem przez VPN i otwórz `https://10.0.0.1:8080` — ma działać bez Twojej pomocy.

**Dalej: Kroki 18–21** (powiadomienia, kopie, kontrola).

---

# ŚCIEŻKA C — nowy system z kopii panelu

Odtwarzasz wszystko na czystej karcie jednym poleceniem. Co dokładnie wróci, zależy od tego, **jaką kopię masz** na NAS-ie:

| Kopia | Skąd się bierze | Co wraca |
|---|---|---|
| **pełna** (plik `….json.enc`, na liście kopii „pełna") | każda kopia zrobiona z ustawionym **hasłem kopii** | wszystko: osoby, ustawienia, konta, hasła do NAS-a i ntfy, historia, **klucz serwera** i **certyfikat HTTPS**. Nikt z użytkowników niczego nie zmienia |
| **podstawowa** (bez hasła albo ze starszej wersji panelu) | kopia bez hasła albo sprzed wersji z pełnymi kopiami | osoby, tiery, reguły, ustawienia. **Klucza serwera w niej nie ma** — instalator zapyta o niego |

Potrzebujesz: **hasła kopii** z menedżera haseł i danych konta `wg-backup` na NAS-ie. Przy kopii podstawowej dodatkowo **klucza serwera** z menedżera haseł — jeśli go nie masz, da się, ale każda osoba dostanie nowy plik (Krok C5).

> **Klucz serwera** to tożsamość Twojego VPN — jest wpisany w plik każdej osoby. Z innym kluczem serwera dotychczasowe pliki nie zadziałają: telefon „puka”, a Pi nie odpowiada.

## Krok C1 — System

**Gdzie: Komputer, Pi**

1. Wykonaj **Krok B1** (Raspberry Pi OS Lite). Ustaw **tę samą nazwę Pi** co wcześniej — wtedy certyfikat HTTPS z kopii pasuje i telefony nie potrzebują nowego `ca.crt`.
2. Na routerze Pi ma dostać **ten sam adres** co wcześniej (rezerwacja DHCP) — na niego kieruje przekierowanie portu WireGuarda.
3. Zapora: **Krok B4**.

WireGuarda i iptables nie musisz instalować — instalator doinstaluje je sam.

## Krok C2 — Odtworzenie

**Gdzie: Pi**

```bash
sudo apt install -y git
git clone https://github.com/mt3ch02/wg-panel.git ~/wg-panel
cd ~/wg-panel
sudo INSTALL_LANG=pl LAN_ACCESS=192.168.1.0/24 ./install.sh --restore
```

`LAN_ACCESS` podaj taki, jak przy poprzedniej instalacji (albo pomiń, jeśli panel był dostępny tylko przez VPN).

Instalator zapyta po kolei:

| Pytanie | Wpisz |
|---|---|
| Adres NAS-a | np. `192.168.1.11` |
| Udział | `backup` (Enter) |
| Katalog na udziale | katalog, w którym leżą kopie (Enter = `wg-panel`) |
| Użytkownik / hasło do NAS-a | `wg-backup` / hasło z menedżera haseł (nie będzie widoczne) |
| Którą odtworzyć | Enter = najnowsza (pokaże listę) |
| Hasło kopii | z menedżera haseł |

Masz zobaczyć: „pełna kopia, N osób. Klucz serwera i certyfikat są na miejscu”, potem zwykłą instalację, „Tunel podniesiony” i na końcu „Odtworzone z pełnej kopii: zaloguj się swoim dotychczasowym kontem”.

Gdy coś się nie zgadza, instalator mówi wprost, co poprawić:

| Komunikat | Co zrobić |
|---|---|
| NAS odrzucił login albo hasło | literówka w haśle albo nazwie konta; w panelu NAS-a sprawdź, czy konto nie jest wyłączone i czy adres Pi nie trafił na listę zablokowanych (np. automatyczna blokada adresów) |
| na udziale backup nie ma katalogu … — są tam: … | uruchom ponownie i wpisz jeden z wypisanych katalogów |
| na NAS nie ma udziału … | zła nazwa udziału — taka jak folder współdzielony na NAS-ie |
| NAS nie odpowiada pod adresem … | zły adres albo SMB wyłączone na NAS-ie |
| nie udało się otworzyć kopii | złe hasło kopii |

> **Kopia bez klucza serwera** (podstawowa albo ze starszej wersji): instalator napisze to wprost i zapyta: **1) wklej stary klucz** (z menedżera haseł — wszyscy łączą się dalej starymi plikami) albo **2) wygeneruj nowy** (osoby i ustawienia wrócą, ale każda osoba dostanie nowy plik — Krok C5).

> **Z panelu zamiast z instalatora:** gdy panel jest już zainstalowany — **Kopie**, na dole strony w **Ustawieniach kopii** wpisz dane NAS-a i **Zapisz ustawienia**, potem na **Liście kopii** kliknij **Odśwież listę · NAS**. Zaznacz kopię (kliknięcie w wiersz), **Odtwórz zaznaczoną…** — w oknie zaznacz **Logowanie starym hasłem**, wpisz hasło kopii i **Odtwórz kopię**. Panel po odtworzeniu powie, czy klucz serwera wrócił z kopii i czy dotychczasowe pliki osób zadziałają.

## Krok C3 — Sprawdź

**Gdzie: Przeglądarka, Telefon**

1. Otwórz `https://<adres-pi>:8080` — bez ostrzeżenia o certyfikacie (jeśli nazwa Pi się zgadza), zaloguj się **dotychczasowym** kontem.
2. **Ustawienia → Klucz publiczny serwera** ma być taki sam jak `PublicKey` w sekcji `[Peer]` pliku na Twoim telefonie.
3. Telefon na danych komórkowych (Wi-Fi wyłączone), włącz tunel. Na liście peerów przy Tobie pojawi się handshake „X s temu”.

Jeśli tunel się nie łączy — **CZĘŚĆ VI, „VPN nie łączy się po odtworzeniu”**.

## Krok C4 — Restart

**Gdzie: Pi**

```bash
sudo reboot
```

Po minucie VPN ma działać bez dotykania czegokolwiek, a **Integralność → Sprawdź teraz** — bez uwag.

## Krok C5 — Tylko z nowym kluczem: nowe pliki dla wszystkich

Jeśli w Kroku C2 wygenerowałeś nowy klucz serwera:

1. **Zapisz go od razu w menedżerze haseł** (`sudo cat /etc/wireguard/server_private.key`) i ustaw **hasło kopii** — od teraz kopie są pełne i ten krok nigdy się nie powtórzy.
2. Przy każdej osobie: **Rotacja kluczy**, potem raz **Zastosuj zmiany** i **Potwierdzam, działa**.
3. Rozdaj pliki: każdy **usuwa stary tunel** z aplikacji i importuje nowy (QR albo **Kopiuj plik** / link jednorazowy) — na jednym urządzeniu.

**Dalej: Krok 21** (kontrola). Powiadomienia i harmonogram kopii wróciły z kopii — sprawdź w Alertach, czy testowy alert dochodzi.

---

---


# CZĘŚĆ IV — Powiadomienia i kopie zapasowe (wszystkie ścieżki)

## Krok 18 — Powiadomienia na telefon

**Gdzie: Telefon, potem Przeglądarka → Alerty**

1. Zainstaluj na telefonie aplikację **ntfy** (bez zakładania konta).
2. Wymyśl długą, losową nazwę tematu, np. `wg-dom-k7f2q9x4m1` — co najmniej 12 znaków, z cyframi.
3. W aplikacji: **+** → wpisz tę nazwę → **Subskrybuj**.
4. W panelu, zakładka **Alerty** → Ustawienia powiadomień: kanał `ntfy`, adres `https://ntfy.sh/wg-dom-k7f2q9x4m1`, **Zapisz**.
5. Kliknij **Wyślij testowy alert**.

Masz zobaczyć: na telefonie „wg-panel: test powiadomień" **z polskimi znakami** w treści. Stuknięcie w powiadomienie otwiera stronę alertów panelu (przez VPN).

> **Dlaczego losowa nazwa:** temat na publicznym ntfy.sh może przeczytać każdy, kto zna jego nazwę — a alerty zawierają imiona osób i ich publiczne adresy IP. Jeśli nazwa jest krótka albo łatwa do zgadnięcia, panel pokaże czerwone ostrzeżenie nad ustawieniami.

> **Opcjonalnie — token dostępu:** jeśli założysz konto na ntfy.sh i zarezerwujesz temat (albo postawisz własny serwer ntfy), wpisz token `tk_...` w polu **Token ntfy**. Wtedy temat jest chroniony niezależnie od nazwy.

> **Priorytety:** najpoważniejsze zdarzenia — ten sam klucz z nowego adresu, ten sam plik na dwóch urządzeniach naraz, konfiguracja zmieniona poza panelem, interfejs `wg0` nie działa — przychodzą z najwyższym priorytetem (telefon zadzwoni głośniej). Reszta ze zwykłym, także „znowu działa” i „ustało”.

> **Najważniejszy alert to „zmiana adresu".** Przychodzi, gdy ktoś łączy się czyimś kluczem z nowego miejsca — to pierwszy sygnał, że plik konfiguracyjny wyciekł.

> **Alert „tunel na dwóch urządzeniach”.** Przychodzi, gdy ten sam plik konfiguracyjny jest włączony na dwóch urządzeniach naraz — np. komputer w domu został z włączonym VPN, a ta sama osoba łączy się tym samym plikiem z pracy. WireGuard odpowiada temu urządzeniu, które odezwało się ostatnio, więc oba wyrzucają się nawzajem i połączenie co chwilę się zrywa (np. RDP rozłącza się przy kliknięciu). Panel zauważa to zwykle w ciągu kilku minut. W alercie są oba adresy, na liście peerów osoba dostaje czerwoną plakietkę **2 urządzenia**, a na jej karcie jest opis, co zrobić. Gdy przez 10 minut drugiego urządzenia nie widać, w Alertach pojawia się wpis „przeskakiwanie ustało”. Ten alert wychwytuje też skopiowany plik używany w tym samym czasie co plik właściciela. Wyłączysz go w Alerty → Ustawienia powiadomień → **Tunel na dwóch urządzeniach naraz** (domyślnie włączony).

**Opcjonalnie — także e-mailem:** Alerty → **E-mail (opcjonalnie)**: zaznacz „Wysyłaj alerty także e-mailem", podaj serwer SMTP (np. `smtp.gmail.com`, port 587, STARTTLS), login, hasło, adresata. Użyj **osobnego konta i hasła do aplikacji** — hasło leży na Pi. E-mail działa obok ntfy, nie zamiast.

## Krok 18a — Strażnik: powiadomienie, gdy coś padnie

**Gdzie: Pi, potem Telefon**

Strażnik to timer systemd, który co minutę — **niezależnie od panelu** — sprawdza: panel, helpera, interfejs `wg0`, temperaturę Pi, miejsce na karcie, zbyt niskie napięcie zasilania i ważność certyfikatu HTTPS. Powiadomienie idzie na ten sam kanał co alerty (Krok 18), dopiero przy dwóch nieudanych sprawdzeniach z rzędu, raz; po naprawie — jedno „znowu działa".

Sprawdź ręcznie:

```bash
wgctl guard
```

Masz zobaczyć: `straznik: wszystko dziala`.

Test na żywo (VPN przez ten czas działa normalnie):

```bash
sudo systemctl stop wg-panel
```

Po 2–3 minutach na telefon ma przyjść **„panel: problem"**. Potem:

```bash
sudo systemctl start wg-panel
```

Po minucie–dwóch: **„panel: znowu działa (przerwa około N min)"**.

> **Terminy dostępu działają także bez panelu.** Ten sam timer odcina osoby po terminie i otwiera okna dostępu, nawet gdy panel leży (to samo ręcznie: `wgctl expire`). Zmiana trafia na interfejs punktowo — Twoje niezastosowane zmiany w panelu zostają niezastosowane.

> **Czego strażnik nie wykryje:** padnięcia całego Pi (brak prądu, zepsuta karta) — bo padnie razem z nim. Na to jest zewnętrzny monitoring, np. Uptime Kuma na osobnej maszynie (część IV-B niżej).

## Krok 19 — Przygotuj miejsce na kopie na NAS

**Gdzie: NAS**

Poniższe kroki opisują Synology DSM jako przykład; na innym NAS-ie zrób odpowiednik.

1. **Panel sterowania → Folder udostępniony → Utwórz**: nazwa `backup` (albo użyj istniejącego).
2. **Panel sterowania → Użytkownik i grupa → Utwórz**: nazwa `wg-backup`, długie hasło zapisz w menedżerze haseł.
3. Przy uprawnieniach daj temu użytkownikowi **odczyt i zapis tylko do folderu `backup`**, do niczego więcej.
4. Wejdź do folderu `backup` i utwórz w nim katalog `wg-panel`.
5. **Panel sterowania → Usługi plików → SMB** musi być włączone.

> **Dlaczego osobny użytkownik:** hasło do niego leży w bazie panelu. Gdyby wyciekło, daje dostęp tylko do kopii panelu, a nie do całego dysku.

## Krok 20 — Włącz kopie w panelu

**Gdzie: Pi, potem Przeglądarka → Kopie**

Program do wysyłania na NAS (`smbclient`) instaluje instalator. Jeśli masz panel z wcześniejszej wersji, najpierw zainstaluj go ponownie (Krok 5 — dane zostają), bo starsza wersja ani go nie instalowała, ani nie mogła go uruchomić.

Strona **Kopie** ma trzy części: u góry **stan** (jaka kopia powstaje, kiedy automatycznie, jak poszła ostatnia) i **Zrób kopię teraz – gdzie?**, w środku **Lista kopii**, na dole **Ustawienia kopii**. Na dole, w **Ustawieniach kopii**:

| Pole | Wpisz |
|---|---|
| Rób kopie automatycznie, codziennie | zaznaczone |
| O godzinie | `03:30` |
| Dokąd | tylko NAS |
| Hasło kopii | **co najmniej 12 znaków, losowe — zapisz w menedżerze haseł** |
| NAS: Adres | np. `192.168.1.11` |
| Udział | `backup` |
| Katalog na udziale | `wg-panel` |
| Użytkownik / Hasło | `wg-backup` / hasło z Kroku 19 |
| Pi: Katalog kopii | zostaw pusty (domyślny) |
| Trzymaj ostatnie | `14` |

**Zapisz ustawienia.** U góry strony, pod **Zrób kopię teraz – gdzie?**, są trzy przyciski: **NAS**, **Pi** i **Ten komputer**. Każdy robi jedną kopię dokładnie tam i nigdzie więcej; pole **Dokąd** dotyczy tylko kopii automatycznych. Kliknij **NAS**.

Masz zobaczyć: u góry trzy zielone kropki — **Rodzaj kopii: PEŁNA, zaszyfrowana**, **Automatycznie: codziennie o 03:30 → tylko NAS** i **Ostatnia kopia: … → NAS** z dopiskiem „suma zgodna”. Na **Liście kopii** nowy wiersz: Gdzie „NAS”, Rodzaj „pełna”, Stan „sprawdzona”. Na NAS-ie, w udziale `backup/wg-panel`, pojawią się dwa pliki: kopia i jej `.sha256`.

**Gdzie ląduje kopia** — decydujesz Ty; panel nie robi dodatkowych kopii po cichu:

| Wybór | Gdzie leży kopia |
|---|---|
| tylko NAS | tylko na NAS-ie — plik na Pi powstaje na chwilę do wysłania i zaraz znika (także gdy wysyłka się nie uda) |
| tylko Pi | w katalogu kopii na Pi (najwyżej ostatnie 14) — nie chroni przed awarią karty |
| NAS i Pi (dwie kopie) | w obu miejscach — odtworzenie bierze plik z Pi i nie musi budzić NAS-a |
| Ten komputer (tylko przycisk) | plik pobiera przeglądarka; panel go nie przechowuje i nie ma go na liście |

Jeśli kopia się nie udała, czerwony pasek pokaże powód:

| Komunikat | Co zrobić |
|---|---|
| brak adresu albo udziału NAS | uzupełnij **Adres** i **Udział** w Ustawieniach kopii, **Zapisz ustawienia** |
| brak użytkownika albo hasła NAS | uzupełnij **Użytkownik** i **Hasło** w Ustawieniach kopii, **Zapisz ustawienia** |
| brak smbclient | zainstaluj panel ponownie (Krok 5) albo `sudo apt install -y smbclient` |
| NAS odrzucił login albo hasło (`NT_STATUS_LOGON_FAILURE`) | złe hasło albo nazwa użytkownika `wg-backup`; w panelu NAS-a sprawdź, czy konto nie jest wyłączone, a adres Pi zablokowany |
| na udziale … nie ma katalogu … — są tam: … | wpisz w **Katalog na udziale** jeden z wypisanych katalogów albo utwórz katalog na NAS-ie |
| `NT_STATUS_BAD_NETWORK_NAME` | zła nazwa udziału — ma być dokładnie taka jak folder współdzielony na NAS-ie (`backup`) |
| `NT_STATUS_OBJECT_NAME_NOT_FOUND` albo `…PATH_NOT_FOUND` | w udziale nie ma katalogu `wg-panel` — utwórz go |
| `NT_STATUS_ACCESS_DENIED` | `wg-backup` nie ma prawa zapisu do `backup` — popraw uprawnienia folderu na NAS-ie |
| `NT_STATUS_IO_TIMEOUT` albo `NT_STATUS_CONNECTION_REFUSED` | usługa SMB wyłączona na NAS-ie albo zły adres |

> **Hasło kopii robi z kopii pełną kopię.** Z hasłem kopia jest zaszyfrowana i zawiera wszystko: osoby, ustawienia, konta, hasła do NAS-a i ntfy, historię, **klucz serwera** i **certyfikat HTTPS** — po awarii odtworzysz z niej całe Pi jednym poleceniem (ścieżka C), a nikt z użytkowników niczego nie zmienia. Bez hasła kopia jest podstawowa i jawna (bez kluczy i haseł) — panel pokazuje to na górze strony Kopie czerwoną kropką „Rodzaj kopii: PODSTAWOWA, jawna”.

> ⚠ Pełna kopia zawiera tożsamość Twojego VPN. Dlatego hasło **długie i losowe**, trzymane tylko w menedżerze haseł (panel ostrzeże przy krótszym niż 12 znaków), a konto `wg-backup` z dostępem tylko do udziału `backup`. Zgubione hasło kopii = kopia nie do odczytania.

> **„Sprawdzona” znaczy:** panel pobrał kopię z powrotem z NAS-a, policzył sumę kontrolną, porównał z zapisaną i otworzył plik hasłem. Obok każdej kopii leży plik `.sha256`, więc możesz ją sprawdzić także bez panelu.

## Krok 21 — Sprawdź, że wszystko gra

**Gdzie: Pi**

```bash
wgctl status
wgctl integrity
wgctl backup --verify
wgctl guard
```

Masz zobaczyć: brak niezastosowanych zmian; „stan zgodny z tym, co zastosował panel"; kopie oznaczone `OK`; `straznik: wszystko dziala`.

Zrób też raz **szybki test kopii** z sekcji „Test kopii zapasowej" (CZĘŚĆ V).

**Wdrożenie skończone.**

---

# CZĘŚĆ IV-B — Opcjonalnie: reverse proxy i monitoring z zewnątrz

Ta część jest opcjonalna — dla osób, które mają w domu osobny serwer z reverse proxy (tu: Caddy) i chcą wystawić panel pod ładną nazwą. Na samym Pi niczego nie instalujesz: panel zostaje przy swoim HTTPS, a Caddy łączy się z nim po HTTPS.

## Krok P1 — Najpierw kod z aplikacji

**Gdzie: Przeglądarka → Ustawienia → Konto**

Włącz kod z aplikacji (TOTP), jeśli jeszcze nie jest włączony.

> **Dlaczego przed proxy:** zapora Pi wpuszcza na port panelu tylko osoby, którym dałeś dostęp (Krok 12b). Przez proxy stronę logowania zobaczy każdy, kto dostanie się do Caddy — także osoby z VPN, którym panel sam by jej nie pokazał. Hasło plus kod z telefonu to wtedy główna ochrona.

## Krok P2 — Certyfikat panelu na serwerze proxy

**Gdzie: Przeglądarka, potem serwer proxy (np. kontener z Caddy)**

1. Ustawienia → HTTPS → rozwiń **Reverse proxy na innym serwerze** → pobierz `wg-panel-ca.crt`.
2. Skopiuj plik do kontenera z Caddy, np. do `/etc/caddy/wg-panel-ca.crt`.

> **Po co:** Caddy łączy się z Pi po HTTPS i sprawdza, czy rozmawia z prawdziwym panelem. Dzięki temu odcinek serwer proxy → Pi też jest szyfrowany, a hasło nie idzie przez domową sieć jawnym tekstem.

## Krok P3 — Caddyfile

**Gdzie: serwer proxy**

Skopiuj z panelu gotowy fragment (Ustawienia → HTTPS → Reverse proxy — ma już wpisany adres Pi i port) i wklej go do bloku `*.example.com` w `Caddyfile`, przed końcowym `handle { abort }`:

```
@wgpanel host panel.example.com
handle @wgpanel {
    reverse_proxy https://192.168.1.10:8080 {
        transport http {
            tls_trusted_ca_certs /etc/caddy/wg-panel-ca.crt
        }
    }
}
```

Przeładuj Caddy (`caddy reload` albo restart kontenera).

## Krok P4 — Nazwa w lokalnym DNS i zapora

**Gdzie: serwer DNS / router, potem Pi**

1. Lokalny DNS (np. Pi-hole → Local DNS Records albo router): `panel.example.com` → adres serwera proxy (tak jak dla innych usług za Caddy).
2. Zapora Pi musi wpuszczać serwer proxy na port panelu. Jeśli instalowałeś z `LAN_ACCESS=192.168.1.0/24` — już wpuszcza. Jeśli bez — zainstaluj ponownie z samym adresem serwera proxy:

```bash
cd ~/wg-panel && sudo INSTALL_LANG=pl LAN_ACCESS=192.168.1.20/32 ./install.sh
```

## Krok P5 — Wpisz adres proxy w panelu

**Gdzie: Przeglądarka → Ustawienia → HTTPS → Reverse proxy na innym serwerze**

Adres serwera proxy: jego adres w LAN (np. `192.168.1.20`). W polu **Publiczny adres panelu** wpisz `https://panel.example.com` — trafi do linków w powiadomieniach. **Zapisz.**

> **Dlaczego trzeba wpisać adres:** panel ufa nagłówkom `X-Forwarded-For` / `X-Forwarded-Proto` wyłącznie od tego adresu. Od innych urządzeń są ignorowane — nikt nie podszyje się pod inny adres, żeby obejść blokadę po nieudanych logowaniach.

## Krok P6 — Test

**Gdzie: Telefon (przez VPN)**

1. Otwórz `https://panel.example.com` — bez ostrzeżenia o certyfikacie (to certyfikat Caddy), zaloguj się.
2. Dziennik → ostatni wpis „logowanie" — w szczegółach ma być adres Twojego urządzenia, **nie** adres serwera proxy. Jeśli jest adres serwera proxy, sprawdź Krok P5.

> ⚠ Bezpośredni dostęp zostaje: `https://10.0.0.1:8080` przez VPN działa dalej. To Twoje wejście awaryjne, gdy serwer proxy leży.

## Krok P7 — Uptime Kuma: gdy padnie całe Pi

**Gdzie: Uptime Kuma na osobnej maszynie**

Strażnik na Pi nie powie, że padło całe Pi. Uptime Kuma na osobnej maszynie może:

1. **Add New Monitor** → typ **HTTP(s)** → URL `https://192.168.1.10:8080/login` (adres Pi w sieci domowej), interwał 60 s. Zaznacz **Ignore TLS/SSL error** (Uptime Kuma nie zna urzędu panelu) albo użyj monitora typu **Ping** na adres Pi.
2. **Notifications** → **ntfy** → ten sam temat co w Kroku 18 (i token, jeśli używasz).

Masz zobaczyć: zielony monitor. Test: wyłącz Pi z prądu — w ciągu kilku minut powiadomienie z Uptime Kuma.

---

---


# CZĘŚĆ V — Codzienna obsługa

## Nowa osoba — krok po kroku

1. **Przeglądarka → Peery → Dodaj peera.** Imię, urządzenie, poziom (NET albo NAS), tryb dostępu.
2. **Zastosuj zmiany** — panel sam sprawdzi połączenie i potwierdzi (zielone „Zastosowano zmiany.”).
3. Otwórz stronę nowej osoby. Sprawdź na karcie **Co idzie przez VPN**, czy zakres się zgadza.
4. Przekaż plik: **kod QR**, jeśli osoba jest obok, albo **Kopiuj plik** i wyślij go szyfrowanym komunikatorem (szczegóły w „Wysłanie pliku konfiguracyjnego").
5. Gdy potwierdzi, że działa — kliknij **Wydano — skasuj klucz**.

> **Skasowanie klucza** sprawia, że nawet wyciek bazy nie pozwoli podszyć się pod tę osobę. Panel zapamiętuje przy tym, jaki zakres dostała — dzięki temu później ostrzeże, gdy jej plik się zestarzeje.

> **Jeden peer = jedno urządzenie.** Kto potrzebuje VPN na laptopie i w pracy, dostaje dwóch peerów, np. „Anna laptop” i „Anna praca” — każdy z własnym plikiem. Jednego pliku nie da się używać na dwóch urządzeniach naraz: wyrzucają się nawzajem, a panel wyśle alert „tunel na dwóch urządzeniach”. Powiedz to osobie, której wysyłasz plik — plik jest jak klucz do domu, nie kopiuje się go na inne urządzenia.

## Dostęp na określony czas

W formularzu osoby pole **Tryb dostępu**:

| Tryb | Przykład | Co się dzieje |
|---|---|---|
| bezterminowo | domownicy | nic |
| okno terminowe (od–do) | ekipa od 1 do 31 października | dostęp włącza się i wyłącza sam |
| N dni od pierwszego połączenia | gość, 7 dni | licznik startuje przy pierwszym połączeniu, nie w dniu wydania |

Trzy dni przed końcem przy osobie pojawia się pomarańczowa plakietka „kończy się za N dni".

## Zmiana danych osoby

**Przeglądarka → Peery → kliknij osobę.** Na górze strony jest formularz:

| Pole | Co zmienia |
|---|---|
| Nazwa, Urządzenie, Notatka | tylko opis w panelu — osoba niczego nie zauważy |
| Tier | poziom dostępu: ADMIN, NET albo NAS |
| Tryb dostępu, Od dnia, Do dnia, Ile dni | na jak długo ma dostęp |

Kliknij **Zapisz zmiany** na pasku u dołu strony (pojawia się, gdy coś zmienisz), potem na liście peerów **Zastosuj zmiany**. Nazwy i notatki działają od razu; zmiana tieru i okna dostępu — po zastosowaniu.

Z terminala: `wgctl set Marek --rename "Marek K." --tier NET --device "iPad"`.

## Zmiana uprawnień

Są dwa sposoby, zależnie od tego, czy zmiana dotyczy jednej osoby, czy całej grupy.

**Jedna osoba ma mieć więcej albo mniej** — reguła własna na jej stronie, sekcja **Reguły własne**. Przykład: Marek (NAS) ma dodatkowo dostać drukarkę: Akcja `ACCEPT`, Cel `drukarka`, Proto `tcp`, Porty `631`, **Dodaj regułę**. Reguła własna jest sprawdzana przed regułami tieru, więc działa mimo blokady LAN-u. Reguły nie da się edytować — usuń ją (**usuń** w wierszu) i dodaj nową.

**Cała grupa ma mieć inaczej** — **Tiery → formularz tieru**. Zmiana tieru NAS zmienia uprawnienia wszystkim pięciu osobom naraz. Tu są też porty dostępu do samego Pi (SSH, RDP) — mają je tylko osoby z tieru, który je ma, dlatego SSH dla jednej dodatkowej osoby to przeniesienie jej do ADMIN albo nowy tier.

Po każdej zmianie sprawdź ją w **Symulatorze**, dopiero potem **Zastosuj zmiany**.

## Co idzie przez VPN — cały ruch czy tylko wybrane adresy

**Przeglądarka → Peery → kliknij osobę → karta „Co idzie przez VPN".**

O tym decyduje linia `AllowedIPs` w pliku osoby. Do wyboru są cztery tryby:

| Tryb | AllowedIPs | Kiedy |
|---|---|---|
| **Cały ruch przez VPN** | `0.0.0.0/0` | np. administrator i osoby, którym ma iść cały ruch — internet idzie przez Twoje łącze |
| **Tylko to, do czego ma dostęp** | wyliczane z reguł, np. `192.168.1.11/32` | np. tier NAS — reszta idzie przez ich własny internet |
| **Cała sieć domowa** | `10.0.0.0/24, 192.168.1.0/24` | cały LAN i VPN — najszerzej, dla zaufanych |
| **Własna lista** | to, co wpiszesz, np. `nas, drukarka` | gdy chcesz ustawić ręcznie |

Domyślnie osoba dziedziczy tryb z tieru („jak w tierze"). Tryb całego tieru zmieniasz w zakładce **Tiery**, pole „Co idzie przez VPN". Na karcie osoby możesz to nadpisać tylko dla niej.

> **To nie są uprawnienia.** `AllowedIPs` mówi tylko, co telefon osoby wyśle do tunelu. Do czego ma dostęp, pilnuje serwer. Nawet jeśli ktoś sam dopisze sobie w aplikacji cały LAN, reguły na Pi i tak wpuszczą go tylko do NAS.

### Jak dać komuś dostęp do czegoś nowego

Przykład: Marek (NAS) ma dostać drukarkę.

1. **Hosty** — jeśli drukarki nie ma w katalogu, dodaj `drukarka = 192.168.1.70`.
2. Strona Marka → **Reguły własne**: `ACCEPT`, cel `drukarka`, `tcp`, port `631` → **Dodaj regułę**.
3. **Zastosuj zmiany** — panel sam potwierdzi, gdy nadal masz połączenie. Od tej chwili serwer go wpuszcza.
4. W trybie „Tylko to, do czego ma dostęp" drukarka **sama** pojawi się w jego `AllowedIPs`. Na karcie pojawi się czerwona ramka „Plik u tej osoby jest nieaktualny".
5. Przekaż mu zmianę jednym ze sposobów:
   - **Kopiuj linię AllowedIPs** i wyślij mu ją. Marek w aplikacji WireGuard: tunel → **Edytuj** → podmienia linię `AllowedIPs` → **Zapisz**. Klucze się nie zmieniają.
   - Albo wyślij mu cały nowy plik (sekcja niżej).
6. Kliknij **Oznacz jako wysłany** — ramka znika.

> **Dlaczego ramka jest czerwona, a czasem szara.** Czerwona: w pliku osoby brakuje czegoś, do czego ma teraz dostęp — bez nowego pliku tam nie dojdzie. Szara: plik obejmuje więcej, niż trzeba (np. cały LAN po zaimportowaniu starej konfiguracji) — działa, wysyłanie jest opcjonalne.

Z terminala: `wgctl allowed Marek` pokazuje gotową linię do wysłania. `wgctl set Marek --tunnel custom --tunnel-list nas,drukarka` ustawia własną listę, a `wgctl set Marek --tunnel tier` przywraca ustawienie z tieru.

## Wysłanie pliku konfiguracyjnego

Na stronie osoby, sekcja **Konfiguracja klienta**:

| Sposób | Jak | Kiedy |
|---|---|---|
| **Kopiuj plik** | kopiuje cały tekst; wysyłasz go np. w wiadomości, osoba w aplikacji WireGuard wybiera **Dodaj tunel → Utwórz od zera** i wkleja | osoba jest daleko |
| **Kod QR** | osoba skanuje ekran aplikacją WireGuard | osoba jest obok |
| **Pobierz .conf** | plik do zaimportowania w aplikacji na komputerze | komputer |
| **Link jednorazowy** | działa raz, wygasa po dobie, **otwiera się tylko przez VPN** | wymiana pliku komuś, kto już jest połączony, np. po rotacji kluczy |

Po potwierdzeniu, że działa: **Wydano — skasuj klucz**. Panel zapamiętuje przy tym, jaki zakres dostała osoba.

> ⚠ Dla osób zaimportowanych z istniejącego `wg0.conf` (ścieżka A) panel nie ma kluczy prywatnych — sam plik `wg0.conf` ich nie zawiera. „Kopiuj plik", kod QR i „Pobierz .conf" zadziałają dla nich dopiero po **Rotacji kluczy**. Do dopisania czegoś nowego rotacja nie jest potrzebna — wystarczy podmiana linii `AllowedIPs` w ich aplikacji.

> ⚠ Plik konfiguracyjny to klucz do Twojej sieci. Wysyłaj go komunikatorem z szyfrowaniem (Signal, WhatsApp), nie mailem, i poproś o usunięcie wiadomości po zaimportowaniu.

## Cofnięcie ostatniej zmiany

**Przeglądarka → Ustawienia → Operacje awaryjne → Cofnij do ostatniej kopii**, albo `wgctl rollback`. Przywraca pliki `wg0.conf` i reguły sprzed ostatniego zastosowania. Baza panelu zostaje, więc zmiana nadal czeka na liście — popraw ją i zastosuj jeszcze raz.

## Odtworzenie z kopii

**Przeglądarka → Kopie → Lista kopii.** Każda kopia jest na liście raz — z datą, miejscem (NAS / Pi / NAS + Pi), rodzajem (pełna / zaszyfrowana / podstawowa / nieudana) i stanem sumy kontrolnej. Kopie leżące tylko na NAS-ie (np. na nowym systemie) dojdą po kliknięciu **Odśwież listę · NAS** — to jedyny przycisk, który pyta NAS o listę, i panel nie robi tego w tle (NAS może spać).

**Co znaczą oznaczenia na liście kopii** (to samo jest w panelu pod listą, w „Co znaczą oznaczenia?”):

| Rodzaj | Znaczy |
|---|---|
| **pełna** | wszystko, razem z kluczem serwera — **po awarii odtwarzaj najnowszą taką** |
| **sprzed odtworzenia** | zapisana sama tuż przed odtworzeniem — do cofnięcia pomyłki |
| **sprzed aktualizacji** | zapisana przez instalator tuż przed aktualizacją panelu |
| **znaleziona** | leżała na NAS-ie albo na Pi, panel jej nie robił (np. sprzed reinstalacji) |
| **podstawowa** | bez klucza serwera, haseł i historii — zrobiona bez hasła kopii |
| **pełna bez klucza** | pełna, ale bez klucza serwera (helper wtedy nie odpowiadał) — rzadkie |

| Stan | Znaczy |
|---|---|
| **sprawdzona** | plik ma tę samą sumę SHA-256 co przy zapisie |
| niesprawdzona | jeszcze nie sprawdzana — zaznacz i kliknij **Sprawdź zaznaczone** |
| **uszkodzona** | plik zmieniony albo uszkodzony — panel go nie odtworzy |
| **nieudana** | kopia się nie zapisała (powód po najechaniu myszką) |
| **brak pliku** | pliku już nie ma (usunięty na NAS-ie albo sprzątnięty z Pi) — wpis usuń |

„NAS + Pi” w kolumnie **Gdzie** znaczy, że plik jest w obu miejscach — odtworzenie bierze go wtedy z Pi.

> Rodzaj kopii wynika z **hasła kopii**: z hasłem każda nowa kopia jest pełna i zaszyfrowana, bez hasła — podstawowa. Osobnego przełącznika nie ma: pełna kopia bez szyfrowania położyłaby klucz serwera jawnym tekstem na NAS-ie.

Zaznacz jedną kopię — kliknięcie w wiersz zaznacza ją, drugie kliknięcie odznacza — i kliknij **Odtwórz zaznaczoną…**. Otworzy się okno z datą kopii i informacją, czy to pełna kopia (wraca klucz serwera — stare pliki osób działają), czy kopia bez klucza. W oknie jest pole **Hasło kopii** (puste = hasło z ustawień) i **Logowanie starym hasłem**. Kliknij **Odtwórz kopię**.

Co panel robi po kliknięciu:

1. bierze plik — z Pi, jeśli tam jest (kopia „NAS + Pi” albo „Pi”), a jeśli leży tylko na NAS-ie, sam go ściąga;
2. sprawdza sumę kontrolną — podmienionej albo uszkodzonej kopii nie odtworzy;
3. otwiera ją hasłem — złe hasło kończy się tutaj, nic się nie zmienia;
4. zapisuje **obecny stan** jako osobną kopię (na liście z plakietką „sprzed odtworzenia”);
5. dopiero wtedy podmienia osoby, tiery, reguły i katalog hostów.

Potem **Zastosuj zmiany**. Gdyby odtworzenie okazało się pomyłką — zaznacz na liście kopię z plakietką „sprzed odtworzenia” i odtwórz ją.

Z terminala: `wgctl restore` pokazuje listę, `wgctl restore 12` odtwarza kopię nr 12.

> **„Logowanie starym hasłem”** zaznaczaj tylko na nowym systemie — wraca wtedy Twoje dotychczasowe konto panelu (login, hasło, kod z aplikacji), a panel Cię wyloguje. Na działającym panelu zostaw odznaczone, żeby nie zmienić sobie hasła do logowania.

## Test kopii zapasowej

Kopia, której nie próbowałeś odtworzyć, to tylko nadzieja. Są dwa poziomy.

### Szybki test (5 minut, bez ryzyka)

**Gdzie: Przeglądarka → Kopie, potem Pi**

1. Pod **Zrób kopię teraz** kliknij **Pi**, potem **NAS** — na Liście kopii mają się pojawić dwa nowe wiersze (Gdzie „Pi” i „NAS”).
2. Zaznacz oba i kliknij **Sprawdź zaznaczone** — panel pobiera pliki z miejsca, w którym leżą (także z NAS-a), i porównuje sumy SHA-256. Każdy wiersz ma mieć stan **sprawdzona**.
3. Na Pi:

```bash
wgctl backup --verify
wgctl restore --nas
```

Masz zobaczyć: kopie `OK` i listę kopii leżących na NAS (to ta sama lista, z której skorzystasz po awarii karty).

### Pełny test (około godziny, na zapasowej karcie SD)

Sprawdza to, co naprawdę ma znaczenie: czy z kopii na NAS-ie da się postawić Pi od zera. Robisz to na **zapasowej karcie** — obecna karta zostaje nietknięta.

1. Wyłącz Pi, włóż zapasową kartę z czystym Raspberry Pi OS (albo użyj drugiego Pi) i przejdź ścieżkę C: kroki **C1–C2** (`install.sh --restore`). Przy okazji sprawdzasz, że hasło kopii w menedżerze haseł jest aktualne.
2. **Zaraz po odtworzeniu, zanim zrobisz cokolwiek innego:**
   - Alerty → kanał **wyłączone** — inaczej testowe Pi wysyłałoby powiadomienia na Twój temat ntfy.
   - Kopie → Ustawienia kopii → odznacz **Rób kopie automatycznie, codziennie** i **Zapisz ustawienia** — pełna kopia przywróciła też hasło do NAS-a, więc testowe Pi zapisywałoby kopie do tego samego folderu i mogłoby usuwać starsze przy rotacji.
3. Sprawdź, że wszystko wróciło:

```bash
wgctl list
wgctl diff
```

Masz zobaczyć: te same osoby, tiery i nazwy co na prawdziwym Pi (porównaj z `wgctl list` na nim), a `wgctl diff` pokazuje wygenerowaną konfigurację bez błędów.

4. Możesz sprawdzić z telefonu, że VPN działa ze starym plikiem (Krok C3) — to najlepszy dowód, że kopia jest pełna. Nie rozdawaj plików z testowego Pi — ma te same klucze co prawdziwe.
5. Wyłącz testowe Pi i włóż z powrotem właściwą kartę. Zapasową kartę wyczyść albo odłóż jako gotową kopię systemu (wtedy trzymaj ją bezpiecznie — jest na niej klucz serwera).

> ⚠ Jeśli coś w pełnym teście nie zadziała — kopia się nie odtwarza, instalator mówi „kopia BEZ klucza serwera”, lista osób się nie zgadza — napraw to **teraz** (zwykle: ustaw hasło kopii i zrób nową kopię). Po prawdziwej awarii karty będzie za późno.

## Usuwanie kopii

**Gdzie: Przeglądarka → Kopie**

Zaznacz kopie na Liście kopii (kliknięcie w wiersz; kwadracik w nagłówku zaznacza wszystkie, **Zaznacz nieudane** — wszystkie nieudane) i kliknij **Usuń zaznaczone**. Panel usuwa pliki kopii razem z `.sha256` — z Pi i z NAS-a — i dopiero potem wpis z listy. Jeśli pliku nie da się usunąć (np. NAS jest wyłączony), wpis zostaje, żeby nie zgubić śladu po pliku, który dalej tam leży.

> **Kopie sprzed reinstalacji** (zrobione przez poprzedni system) panel na początku nie zna — widać je dopiero po **Odśwież listę · NAS**. Wtedy trafiają na listę jako „znaleziona” i można je sprawdzić, odtworzyć albo usunąć jak każdą inną. Odwrotnie: kopia, której pliku po odświeżeniu listy nie ma już na NAS-ie (np. usunięta ręcznie), dostaje stan „brak pliku” — jej wpis po prostu usuń.

> **„Trzymaj ostatnie” sprząta tylko Pi.** Kopie na NAS-ie zostają, dopóki ich nie usuniesz — jedna ma kilka KB, więc nawet codzienne kopie przez rok to około 2 MB.

> ⚠ Zostaw zawsze przynajmniej jedną pełną kopię w stanie „sprawdzona” — najlepiej na NAS-ie.

## Inne częste czynności

| Co chcesz zrobić | W panelu | W terminalu |
|---|---|---|
| zablokować na chwilę | Zawieś (drugie kliknięcie: Wznów) | `wgctl suspend Marek` / `wgctl resume Marek` |
| odciąć zgubiony telefon | Rotacja kluczy (stary plik przestaje działać, osoba dostaje nowy) albo Usuń, jeśli dostęp ma zniknąć | `wgctl rotate Marek` albo `wgctl delete Marek --yes` |
| wymienić klucze | Rotacja kluczy | `wgctl rotate Marek` |
| sprawdzić, czy ktoś ma dostęp | Symulator | `wgctl check Marek 192.168.1.11 445` |
| zmienić adres NAS | Hosty → `nas` | `wgctl host add nas 192.168.1.55` |
| zobaczyć ruch | wykres na stronie osoby | `wgctl traffic Marek` |
| zmienić hasło do panelu | Ustawienia → Konto | — |

Każda zmiana w ustawieniach, osobach i regułach czeka, aż klikniesz **Zastosuj zmiany** — wcześniej nic się nie dzieje na żywo.

> **IPv6** jest wyłączone i nie jest potrzebne do podstawowego działania. Jeśli kiedyś zechcesz je włączyć, opis jest w [ARCHITEKTURA.md](ARCHITEKTURA.md), w sekcji „IPv6”.

## Nazwy urządzeń zamiast adresów

Zakładka **Hosty**: wpisz raz `drukarka = 192.168.1.70`, `kamera = 192.168.1.80` itd. W regułach używasz nazw. Zmiana adresu w jednym miejscu przestawia wszystkie reguły.

> ⚠ Ustaw urządzeniom z katalogu stałe adresy (rezerwacja DHCP na routerze). Katalog tylko tłumaczy nazwę na adres — jeśli urządzenie samo zmieni IP, reguła wskaże w pustkę.

## Własne nazwy urządzeń

**Gdzie: Przeglądarka → Kopie → na dole Ustawienia kopii**

Panel nazywa dwa urządzenia: **serwer**, na którym działa (domyślnie „Pi”), i **miejsce na kopie** (udział SMB, domyślnie „NAS”). Obie nazwy możesz zmienić:

1. Przy nagłówku sekcji (np. **NAS**) kliknij **Zmień nazwę**.
2. Wpisz nazwę (najwyżej 24 znaki) — nagłówek zmienia się w trakcie pisania.
3. **Zapisz** (albo Enter) — strona się odświeży i nowa nazwa jest wszędzie. **Anuluj** (albo Esc) wraca do poprzedniej.

Nazwa zapisuje się sama, niezależnie od reszty ustawień. Jeśli w ustawieniach kopii masz inne niezapisane zmiany, strona się nie przeładuje, żeby ich nie zgubić. Puste pole przywraca nazwę domyślną.

> Nazwa to etykieta — zawsze stoi w formie podstawowej, więc pasuje każda. Nazwy zapisują się w pełnej kopii i wracają po odtworzeniu. Instalator (także `install.sh --restore`) używa nazw domyślnych, bo działa, zanim panel ma bazę.

## Jak długo panel pamięta historię

| Co | Jak długo | Gdzie zmienić |
|---|---|---|
| Dziennik (kto, co i kiedy zmienił) | 90 dni (przy aktualizacji ze starszej wersji zostaje dotychczasowe 7) | Ustawienia → **Zaawansowane** → **Dziennik [dni]** |
| Alerty | 90 dni (przy aktualizacji ze starszej wersji zostaje dotychczasowe 7) | Ustawienia → **Zaawansowane** → **Alerty [dni]** |
| Wykresy ruchu | ostatnie 7 dni | nie zmienia się |

Wpisane `0` znaczy **bez limitu** — panel niczego z tej listy nie kasuje. Baza rośnie wtedy powoli: dziennik to zwykle kilka–kilkanaście wpisów dziennie, alerty jeszcze mniej. Wpisy starsze niż ustawiona liczba dni panel usuwa raz na dobę. Ile dni obowiązuje, widać też na stronach Dziennik i Alerty.

> **Rada:** alerty ustaw na 90 albo 365 dni. Alert „zmiana adresu” sprzed miesiąca bywa jedynym śladem, skąd ktoś się łączył. Pełna kopia i tak zabiera całą historię ze sobą.

## Co jakiś czas

| Kiedy | Co |
|---|---|
| po każdym `ufw reload` | `sudo systemctl restart wg-quick@wg0` |
| raz w miesiącu | `wgctl list` — czy znasz wszystkie osoby na liście |
| raz w miesiącu | zakładka Alerty i wykresy ruchu |
| raz w miesiącu | Alerty → **Wyślij testowy alert** — czy powiadomienia nadal dochodzą |
| raz na kwartał | Kopie → kwadracik w nagłówku listy (zaznacz wszystkie) → **Sprawdź zaznaczone** — wszystkie „sprawdzona” |
| raz na pół roku | pełny test kopii na zapasowej karcie (CZĘŚĆ V) |

> ⚠ `ufw reload` przestawia kolejność reguł i odpina łańcuchy panelu. Po każdym reloadzie restart `wg-quick`. To najczęstsza przyczyna „wczoraj działało, dziś nie".

---

# CZĘŚĆ VI — Kiedy coś pójdzie źle

## Co zrobić, gdy…

| Objaw | Co zrobić |
|---|---|
| zastosowałeś zmianę i straciłeś połączenie | **nic nie rób przez 90 sekund** — bezpiecznik cofnie sam |
| bezpiecznik nie pomógł | z domu przez SSH: `wgctl rollback` |
| panel nie odpowiada, VPN działa | `sudo systemctl restart wg-panel` |
| czerwony baner „zmieniona poza panelem" | sekcja niżej |
| alarm o niezgodnej kopii | Kopie → zaznacz kopie → **Sprawdź zaznaczone**, zrób nową kopię, sprawdź, kto ma dostęp do folderu `backup` |
| alarm o nietypowym ruchu | strona osoby → wykres; jeśli to nie ona — rotacja kluczy |
| po restarcie Pi integralność: „pierwsza reguła FORWARD to nie skok do WG-USERS” | Docker wstawił swoje reguły przed panel — tak było w starszych wersjach panelu. Zaktualizuj panel i raz kliknij **Zastosuj zmiany** (sekcja „Konfiguracja zmieniona poza panelem”) |
| po restarcie Pi „Handshake nigdy” i ruch 0 | starsza wersja panelu — obecna pamięta ostatni handshake i ruch łącznie w bazie; zaktualizuj panel |
| zgubiony telefon z kodem logowania | `wgctl totp off admin` |
| `wgctl`: „jesteś już w grupie wg-panel, ale ta sesja…" | wyloguj się i zaloguj ponownie albo `newgrp wg-panel` |
| `wgctl`: „brak dostępu … dodaj się do grupy" | `sudo usermod -aG wg-panel $USER`, potem jak wyżej (ponowna instalacja robi to sama) |
| alert „tunel na dwóch urządzeniach” albo u jednej osoby połączenie co chwilę się zrywa | ten sam plik działa na dwóch urządzeniach — niech wyłączy tunel na jednym. Nie wiadomo, gdzie jest drugie: **Rotacja kluczy** na karcie osoby i nowy plik tylko na właściwe urządzenie. Gdy potrzebuje dwóch urządzeń, dodaj drugiego peera |
| u jednej osoby RDP rozłącza się przy klikaniu, a alertu o dwóch urządzeniach nie ma | prawdopodobnie MTU na jej łączu: w aplikacji WireGuard → Edytuj → w sekcji `[Interface]` dopisz `MTU = 1280` |
| powiadomienie „panel: problem" | `journalctl -u wg-panel -n 40`; VPN działa dalej, terminy dostępu też (strażnik) |
| „helper: problem" | `journalctl -u wg-panel-helper -n 40`; bez helpera panel nie zastosuje zmian |
| „interfejs WireGuard: problem" | **użytkownicy nie mają dostępu** — `systemctl status wg-quick@wg0`, potem `sudo systemctl restart wg-quick@wg0` |
| „zasilanie Pi" | za słaby zasilacz albo kabel — wymień na oryginalny (5 V, 3 A dla Pi 4 / 5 A dla Pi 5); grozi uszkodzeniem karty |
| „temperatura Pi" | sprawdź chłodzenie i czy obudowa nie stoi w zamkniętej szafce |
| „miejsce na karcie" | `df -h`; zmniejsz „Trzymaj ostatnie” w Ustawieniach kopii albo przenieś kopie na NAS |
| „certyfikat HTTPS" | `wgctl tls --renew`; jeśli błąd — `journalctl -u wg-panel-helper -n 40` |
| karta SD padła | ścieżka C z początku instrukcji |

> **VPN nie potrzebuje panelu do działania.** Tunel chodzi w jądrze, reguły są w iptables (wczytuje je sam `wg0.conf`, także po restarcie Pi). Awaria panelu odbiera Ci tylko możliwość zmian — nikt z użytkowników VPN jej nie zauważy. Terminy dostępu pilnuje w tym czasie strażnik, a panel i helper systemd sam podnosi po awarii.

## VPN nie łączy się po odtworzeniu

Objaw: tunel na telefonie włączony, a w panelu przy osobie brak handshake'u. Sprawdzaj po kolei.

1. **Czy pakiety z telefonu docierają do Pi.** Telefon na danych komórkowych (Wi-Fi wyłączone), na Pi:

```bash
sudo apt install -y tcpdump
sudo tcpdump -ni eth0 udp port 51820
```

Włącz tunel, odczekaj 10 s, **Ctrl+C**. Brak linii = problem z routerem (przekierowanie portu WireGuarda na adres Pi) albo `Endpoint` w pliku wskazuje inny adres niż `curl -4 ifconfig.me`. Są linie tylko w jedną stronę (`… > <adres-pi>.51820`) = Pi nie rozpoznaje pliku — punkt 2.

2. **Czy plik na telefonie pasuje do Pi.** W aplikacji WireGuard stuknij tunel:
   - sekcja **Peer → Klucz publiczny** = `sudo wg show wg0 public-key`,
   - sekcja **Interfejs → Klucz publiczny** = klucz przy adresie tej osoby w `sudo wg show wg0 allowed-ips`.

   Coś się różni → usuń tunel z aplikacji i zaimportuj nowy z karty osoby. Jeśli klucz serwera się zmienił (kopia bez klucza), każda osoba potrzebuje nowego pliku — Krok C5.

3. **Zapora.** Na chwilę `sudo ufw disable`, test, potem koniecznie `sudo ufw enable`. Jeśli bez ufw działa — brakuje reguły `51820/udp ALLOW` (Krok B4).

## Konfiguracja zmieniona poza panelem

Najpierw sprawdź, czy to nie Ty: ręczna edycja `wg0.conf`, `ufw reload`, `wg set` z terminala. Jeśli tak — kliknij **Przywróć stan z panelu**.

Jeśli to nie Ty:

1. Nie przywracaj od razu. Zapisz stan: `sudo wg show`, `sudo iptables -S`, `last`.
2. Zmień hasła do Pi i NAS, sprawdź `~/.ssh/authorized_keys`.
3. Dopiero wtedy: **Integralność → Przywróć stan z panelu**.

> **Docker na Pi.** Docker przy każdym swoim starcie wstawia reguły (`DOCKER-USER`, `DOCKER-FORWARD`) na samą górę `FORWARD`. Dlatego panel wpina się także do łańcucha `DOCKER-USER` — Docker zawsze sprawdza go jako pierwszy i nigdy go nie czyści. Ruch z VPN trafia więc do reguł panelu przed regułami Dockera, a restart Pi ani Dockera nie wywołuje alarmu „pierwsza reguła FORWARD to nie skok do WG-USERS”. Starsze wersje panelu tego nie robiły i po restarcie Pi zgłaszały ten alarm — po aktualizacji wystarczy raz kliknąć **Zastosuj zmiany** (albo **Przywróć stan z panelu**).

> ⚠ Jeśli ktoś miał roota na Pi, mógł zmienić także sam panel. Po takim incydencie jedyna pewna droga to czysta karta SD i odtworzenie z kopii — niżej.

## Karta SD padła albo nowe Pi

To jest **ścieżka C** z początku instrukcji: czysta karta i `sudo ./install.sh --restore` — instalator odtwarza z pełnej kopii na NAS-ie wszystko, łącznie z kluczem serwera. Wszystkie osoby łączą się bez zmian.

## Odinstalowanie

**Gdzie: Pi**

| Polecenie | Co usuwa | VPN |
|---|---|---|
| `sudo /opt/wg-panel/uninstall.sh` | program, usługi (także strażnika), `wgctl`, reguły ufw panelu, urząd certyfikacji panelu | działa dalej z ostatnią konfiguracją; **dane panelu zostają** — ponowna instalacja przywróci panel |
| `sudo /opt/wg-panel/uninstall.sh --purge` | jak wyżej + baza, kopie na karcie, certyfikaty, kopie plików w `/etc/wireguard`, konto `wg-panel`, zapamiętane opcje instalacji (`/etc/wg-panel`) | działa dalej; od teraz zmieniasz `wg0.conf` ręcznie |
| `sudo /opt/wg-panel/uninstall.sh --restore` | jak `--purge` + wraca `wg0.conf` z Kroku 2 (`/root/wg-panel-before`) | po `sudo reboot` działa konfiguracja sprzed panelu |

To samo z rozpakowanej paczki: `sudo INSTALL_LANG=pl ./install.sh --uninstall` (z `--purge` albo `--restore`).

Skrypt najpierw wypisuje, co usunie, a co zostaje, i czeka, aż wpiszesz `tak` (po polsku — poprzedź polecenie `INSTALL_LANG=pl`; domyślnie skrypt jest po angielsku i czeka na `yes`). Odmówi, gdy zmiana w panelu czeka na potwierdzenie — najpierw ją potwierdź albo cofnij. `--restore` odmówi, gdy nie ma kopii z Kroku 2.

> **Czego skrypt nigdy nie rusza:** klucza serwera, usługi `wg-quick@wg0`, kopii na NAS ani `/root/wg-panel-before`. Obecną konfigurację z panelu `--restore` odkłada do `/root/wg-panel-after`, gdybyś chciał wrócić.

> **Dlaczego przy zwykłym odinstalowaniu VPN działa dalej:** `wg0.conf` wygenerowany przez panel przy starcie sam wczytuje reguły z `/etc/wireguard/wg0-panel-rules.v4`, a przekazywanie pakietów włącza plik w `/etc/sysctl.d`. Skrypt zostawia te pliki — bez nich tunel po restarcie Pi by nie wstał albo nie sięgał do sieci domowej.

Po odinstalowaniu usuń z telefonów i komputerów certyfikat „wg-panel CA", jeśli go instalowałeś (Krok 7a).

Ręcznie, gdyby skryptu nie było:

```bash
sudo systemctl disable --now wg-panel wg-panel-helper wg-panel-guard.timer
sudo cp /root/wg-panel-before/wg0.conf /etc/wireguard/wg0.conf
sudo reboot
```

---

# CZĘŚĆ VII — Bezpieczeństwo

| Kto | Czy się dostanie |
|---|---|
| ktoś z internetu | nie — widać tylko port WireGuarda, który bez klucza milczy |
| osoba z VPN próbuje wejść dalej, niż ma | nie — reguły na serwerze |
| ktoś w VPN albo domowym Wi-Fi bez hasła do panelu | nie — hasło; po 5 nieudanych próbach logowanie blokuje się na 5 minut |
| zwykłe konto na Pi | nie — baza i klucze zablokowane uprawnieniami |
| ktoś ręcznie zmieni konfigurację | zostanie wykryty w 5 minut, dostaniesz alarm |
| ktoś podmieni kopię na NAS | zostanie wykryty sumą kontrolną |
| ktoś wyniesie kartę SD | tak — świadomie nie szyfrujemy, Pi stoi w domu |
| ktoś z rootem na Pi | tak — tego nie zatrzyma żadne oprogramowanie na tej maszynie |

## Menedżer haseł i kopia sekretów

W menedżerze haseł trzymaj: hasło do panelu, hasło kopii, klucz prywatny serwera, hasło użytkownika kopii na NAS-ie (`wg-backup`).

Menedżer haseł powinien stać **poza Pi i poza NAS-em** (osobna maszyna albo usługa w chmurze). Po awarii Pi dostaniesz się do niego z domowej sieci, bez VPN, więc odtworzenie (ścieżka C, robisz ją i tak w domu) nie zależy od działającego tunelu.

> ⚠ Jeśli menedżer haseł stanie na tym Pi albo na NAS-ie i będzie dostępny tylko przez ten VPN, po awarii Pi robi się błędne koło: do odtworzenia VPN potrzebujesz hasła kopii, a do sejfu — VPN. Hasło kopii i klucz serwera muszą mieć drugą kopię poza Pi i NAS-em.

> **Najprościej:** aplikacja menedżera haseł na telefonie zwykle trzyma lokalną kopię sejfu i działa bez połączenia z serwerem. Otwórz ją raz na jakiś czas, żeby kopia była świeża.


---


# Ściąga

| Cel | Komenda |
|---|---|
| stan i niezastosowane zmiany | `wgctl status` |
| lista osób | `wgctl list` |
| nowa osoba | `wgctl add Marek --tier NAS` |
| osoba na okres | `wgctl add Ekipa --tier NAS --start 2026-10-01 --until 2026-10-31` |
| osoba na N dni od pierwszego połączenia | `wgctl add Gosc --tier NAS --days 7` |
| zmiana poziomu / nazwy | `wgctl set Marek --tier NET --rename "Marek K."` |
| co u osoby idzie przez VPN | `wgctl allowed Marek` |
| zmiana tego, co idzie przez VPN | `wgctl set Marek --tunnel custom --tunnel-list nas,drukarka` |
| kod QR w terminalu | `wgctl qr Marek` |
| sprawdzenie dostępu | `wgctl check Marek 192.168.1.11 445` |
| co się zmieni | `wgctl diff` |
| zastosuj i potwierdź | `wgctl apply`, potem `wgctl confirm` |
| cofnij ostatnie zastosowanie | `wgctl rollback` |
| lista kopii / odtwórz | `wgctl restore` / `wgctl restore 12` |
| kopie na NAS (nowy system) | `wgctl restore --nas` |
| całe Pi z pełnej kopii (czysta karta) | `sudo INSTALL_LANG=pl ./install.sh --restore` |
| kopia teraz / sprawdź kopie | `wgctl backup` / `wgctl backup --verify` |
| katalog urządzeń | `wgctl host list`, `wgctl host add drukarka 192.168.1.70` |
| ruch | `wgctl traffic Marek` |
| integralność | `wgctl integrity` |
| strażnik ręcznie (co działa, co nie) | `wgctl guard` |
| terminy dostępu teraz (bez panelu) | `wgctl expire` |
| certyfikat HTTPS: odcisk, ważność / odnów | `wgctl tls` / `wgctl tls --renew` |
| instalator po polsku / po angielsku | `sudo INSTALL_LANG=pl ./install.sh` / bez zmiennej |
| odinstalowanie (VPN zostaje) | `sudo /opt/wg-panel/uninstall.sh` |
| powrót do konfiguracji sprzed panelu | `sudo /opt/wg-panel/uninstall.sh --restore` |
| wyłącz kod z aplikacji | `wgctl totp off admin` |
| logi | `journalctl -u wg-panel -f` |

## Pliki

Pełna struktura katalogów, opis każdego pliku i działania całości — [ARCHITEKTURA.md](ARCHITEKTURA.md).

---

---

# Aktualizacja i porządki

## Aktualizacja panelu

**Gdzie: Pi**

```bash
cd ~/wg-panel
git pull
sudo ./install.sh
```

Na początku instalator wypisze, z jakimi opcjami działa, np.:

```
Opcje z poprzedniej instalacji (/etc/wg-panel/install.conf): dostęp z LAN: 192.168.1.0/24, port 8080, HTTPS: 1
```

Instalator pamięta opcje (`LAN_ACCESS`, `BIND_PORT`, `TLS`, `INSTALL_LANG`) w `/etc/wg-panel/install.conf` — nie podajesz ich drugi raz. Przy pierwszej aktualizacji z wersji, która tego pliku nie miała, zamiast ścieżki pliku zobaczysz `/etc/systemd/system/wg-panel.service`: instalator odczytuje wtedy opcje z obecnej usługi i reguły ufw, a po udanej instalacji zapisuje je do `install.conf`. Jeśli zobaczysz tam `dostęp z LAN: nie`, a panel ma być dostępny z sieci domowej, uruchom instalator jeszcze raz z `sudo LAN_ACCESS=192.168.1.0/24 ./install.sh` (Twoja sieć) — ta wartość też zostanie zapamiętana.

Co zostaje:

- osoby, tiery, reguły, hosty, ustawienia, kopie, konto i historia — baza jest osobno od programu;
- klucz serwera, certyfikat HTTPS i pliki osób — nikt nie potrzebuje nowego pliku;
- tiery, które sam usunąłeś albo przemianowałeś — aktualizacja ich nie przywraca (wbudowane tiery i host `nas` powstają tylko przy pierwszej instalacji).

Przed uruchomieniem nowej wersji instalator zapisuje kopię bazy. W **Kopiach** ma plakietkę „sprzed aktualizacji” — gdyby nowa wersja coś namieszała, zaznacz ją i **Odtwórz zaznaczoną…**. VPN działa przez cały czas; instalator uruchamia ponownie panel i helpera — odśwież stronę w przeglądarce.

Nowa wersja czasem zmienia sposób zapisu `wg0.conf`. Wtedy na liście peerów pojawi się karta „Panel ma zmiany, których nie ma na interfejsie” — kliknij **Zastosuj zmiany** i potwierdź.

Żeby zmienić opcję (np. port), podaj ją przy aktualizacji: `sudo BIND_PORT=8443 ./install.sh`. Pusta wartość wyłącza: `sudo LAN_ACCESS= ./install.sh` — panel zostanie dostępny tylko przez VPN.

Katalog `~/wg-panel` (sklonowane repozytorium) nie jest potrzebny do działania panelu — instalator skopiował z niego wszystko do `/opt/wg-panel`. Możesz go zostawić na następną aktualizację (`git pull`) albo usunąć.

## Co musi zostać

| Ścieżka | Co to | Co się stanie po usunięciu |
|---|---|---|
| `/opt/wg-panel/` | program: panel, helper, `wgctl`, `uninstall.sh` | usługi nie wystartują — panel, helper i strażnik uruchamiają się stąd |
| `/var/lib/wg-panel/` | baza, kopie na karcie, certyfikat panelu | panel wystartuje pusty — nie będzie wiedział o żadnej osobie ani regule |
| `/usr/local/bin/wgctl` | polecenie `wgctl` | `wgctl` przestanie działać z dowolnego miejsca |
| `/etc/wireguard/` | konfiguracja VPN, reguły zapory, klucz serwera | **VPN przestanie działać** (po restarcie Pi albo od razu) |
| `/etc/systemd/system/wg-panel*` | usługi panelu, helpera i strażnika | nic nie wystartuje po restarcie Pi |
| `/root/wg-panel-before/` | Twoja kopia z Kroku 2 (ścieżka A) | nie da się wrócić do konfiguracji sprzed panelu (`uninstall.sh --restore`) |

> ⚠ Do usuwania panelu służy `sudo /opt/wg-panel/uninstall.sh` (część VI, „Odinstalowanie"). Nie kasuj tych katalogów ręcznie — np. usunięcie `/etc/wireguard/wg0-panel-rules.v4` sprawi, że tunel nie wstanie po restarcie Pi, bo `wg0.conf` wczytuje z niego reguły.

## wgctl — panel w terminalu

`wgctl` robi to samo co panel WWW, tylko poleceniami — pełna lista w Ściądze wyżej. Przydaje się:

- **awaryjnie** — gdy strona panelu nie działa, przez SSH nadal możesz zawiesić kogoś, zrobić kopię albo sprawdzić stan;
- **automatycznie** — strażnik co minutę uruchamia `/opt/wg-panel/wgctl guard`; dlatego zainstalowany `wgctl` musi zostać.

| Plik | Co to |
|---|---|
| `/opt/wg-panel/wgctl` | właściwy program |
| `/usr/local/bin/wgctl` | krótka nakładka: dzięki niej wpisujesz po prostu `wgctl`; ustawia ścieżki do bazy i helpera |

> **Dlaczego bez `sudo`:** instalator dodał Twoje konto do grupy `wg-panel`, która ma dostęp do bazy panelu. Działa to od nowego logowania — w bieżącym terminalu wystarczy `newgrp wg-panel`. W razie wątpliwości zawsze zadziała `sudo wgctl …`.
