# Contributing / Współtworzenie

**EN.** Bug reports, translations and pull requests are welcome.

- **No new dependencies.** The project is stdlib-only on purpose (Python 3.9+, so no `match`, no `X | Y` type syntax at runtime without `from __future__ import annotations`).
- **UI text:** Polish text is the translation key; add an English entry to `EN` in `panel/i18n.py`.
- **Rules are rendered twice** on purpose (`panel/render.py` and `helper/wg_panel_helper.py`); change both and keep them in agreement.
- **Helper changes** get extra scrutiny: it runs as root and must stay a strict allow-list that validates everything itself.
- Wanted: English `wgctl`/notification messages, network auto-detection at install time, other distributions.

**PL.** Zgłoszenia błędów, tłumaczenia i pull requesty są mile widziane.

- **Bez nowych zależności** — projekt celowo korzysta tylko ze standardowej biblioteki (Python 3.9+).
- **Teksty interfejsu:** polski tekst jest kluczem tłumaczenia; dodaj angielski odpowiednik do `EN` w `panel/i18n.py`.
- **Reguły są renderowane dwa razy** celowo (`panel/render.py` i helper) — zmieniaj oba i pilnuj, żeby były zgodne.
- **Zmiany w helperze** wymagają szczególnej uwagi: działa jako root i musi zostać ścisłą listą dozwolonych operacji z własną walidacją.
- Mile widziane: angielskie komunikaty `wgctl` i powiadomień, autowykrywanie sieci przy instalacji, inne dystrybucje.
