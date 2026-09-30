"""Widoki HTML. Bez frameworka i bez kroku budowania - same funkcje zwracajace tekst."""
from __future__ import annotations

import html
import json
import os
import threading
import time
import urllib.parse

from . import db, i18n, notify, render
from .i18n import _


def e(value) -> str:
    return html.escape(str(value if value is not None else ""))


# Kontekst biezacego zapytania - ustawia go dispatcher w app.py. Dzieki temu kazda strona
# sama pokazuje czerwony pasek bledu i pasek oczekiwania, bez przekazywania ich wszedzie.
REQUEST = threading.local()


def begin_request(path: str = "/", err: str = "", pending=None) -> None:
    REQUEST.__dict__.clear()
    REQUEST.path = path
    REQUEST.err = err
    REQUEST.pending_fn = pending


def ask(title: str, text: str, ok: str, kind: str = "danger", detail: str = "") -> str:
    """Atrybuty formularza otwierajace okno potwierdzenia - JEDYNY sposob pytania w panelu.

    Tekst trafia do atrybutow HTML, nie do kodu JavaScript, wiec apostrof w nazwie
    osoby (O'Brien) niczego nie psuje. Pytamy tylko przy akcjach nieodwracalnych
    albo zrywajacych polaczenie.
    """
    attrs = (f' data-confirm="{e(text)}" data-confirm-title="{e(title)}"'
             f' data-confirm-ok="{e(ok)}" data-confirm-kind="{e(kind)}"')
    if detail:
        attrs += f' data-confirm-detail="{e(detail)}"'
    return attrs


def discard_ask() -> str:
    return ask(_("Odrzucić zmiany w panelu?"),
               _("Panel wróci do stanu, który działa na interfejsie. Niezastosowane zmiany "
                 "znikną z panelu. Na samym interfejsie nic się nie zmieni."),
               _("Odrzuć zmiany"))


def _csrf(sess) -> str:
    return f'<input type="hidden" name="csrf" value="{e(sess["csrf"])}">'


def pending_bar(sess, state: dict) -> str:
    """Pasek na gorze KAZDEJ strony, dopoki zmiana czeka na potwierdzenie."""
    pending = state.get("pending")
    if not pending:
        return ""
    here = e(getattr(REQUEST, "path", "/") or "/")
    remaining = pending.get("remaining")
    if remaining is None:
        clock = ('<span>' + _("Panel nie może teraz sprawdzić helpera - licznik jest "
                               "niedostępny.") + '</span>')
        data = ""
    else:
        clock = '<span>' + _("Bez potwierdzenia wrócą same za {seconds}&nbsp;s.", seconds=(
            f'<strong id="wg-pending-left">{int(remaining)}</strong>')) + '</span>'
        data = f' data-remaining="{int(remaining)}"'
    auto = bool(pending.get("auto")) and remaining is not None
    css = "pending-bar"
    if auto:
        # ukryty na starcie (tylko z JS): w zwyklym przypadku potwierdzenie przychodzi
        # w sekunde i zamiast paska widac od razu zielone "Zastosowano zmiany."
        css += " auto-wait"
        data += ' data-auto="1"'
        lead = "<strong>" + _("Sprawdzam, czy nadal masz połączenie z panelem...") + "</strong>"
        hint = _("Trwa to dłużej niż zwykle. Jeśli widzisz tę stronę, możesz potwierdzić "
                 "ręcznie.")
    else:
        lead = "<strong>" + _("Zmiany czekają na potwierdzenie.") + "</strong>"
        hint = _("Potwierdź, gdy sprawdzisz, że nadal masz połączenie.")
    rollback_ask = ask(
        _("Cofnąć tę zmianę?"),
        _("Na interfejsie wróci konfiguracja sprzed zastosowania. Twoje zmiany zostaną "
          "w panelu jako niezastosowane."),
        _("Cofnij zmianę"))
    confirm_label, undo_label = _("Potwierdzam, działa"), _("Cofnij teraz")
    return f"""
<div class="{css}" id="wg-pending"{data} role="status" aria-live="polite">
  <div class="pending-inner">
    <p class="pending-text">{lead}
      {clock}
      <span class="muted">{hint}</span></p>
    <div class="pending-actions">
      <form method="post" action="/confirm" class="inline">
        {_csrf(sess)}<input type="hidden" name="next" value="{here}">
        <button class="primary">{confirm_label}</button>
      </form>
      <form method="post" action="/rollback" class="inline"{rollback_ask}>
        {_csrf(sess)}<input type="hidden" name="next" value="{here}">
        <input type="hidden" name="scope" value="pending">
        <button class="danger">{undo_label}</button>
      </form>
    </div>
  </div>
</div>"""


def confirm_dialog() -> str:
    return f"""
<dialog id="wg-confirm" class="wg-dialog" aria-labelledby="wg-confirm-title"
        aria-describedby="wg-confirm-text">
  <form method="dialog" class="wg-dialog-body">
    <h2 id="wg-confirm-title"></h2>
    <p id="wg-confirm-text"></p>
    <p id="wg-confirm-detail" class="muted" hidden></p>
    <div class="wg-dialog-actions">
      <button type="submit" value="cancel" id="wg-confirm-cancel">{_("Anuluj")}</button>
      <button type="submit" value="ok" id="wg-confirm-ok" class="danger">{_("Tak")}</button>
    </div>
  </form>
</dialog>"""


def js_texts() -> str:
    """Teksty dla skryptow w biezacym jezyku - skrypt nie ma w sobie zadnego jezyka."""
    return json.dumps({
        "sure": _("Na pewno?"), "yes": _("Tak"),
        "not_pending": _("Zmiana nie czeka już na potwierdzenie."),
        "diff_error": _("Nie udało się wczytać różnicy."), "never": _("nigdy"),
        "s_ago": _("{n} s temu"), "min_ago": _("{n} min temu"), "h_ago": _("{n} h temu"),
        "d_ago": _("{n} dni temu"), "copied": _("Skopiowano"),
        "theme_dark": _("ciemny"), "theme_light": _("jasny"),
    }, ensure_ascii=False).replace("</", "<\\/")


def lang_switch() -> str:
    """Przelacznik jezyka obok przelacznika motywu - flaga i skrot, biezacy oznaczony."""
    here = urllib.parse.quote(getattr(REQUEST, "path", "/") or "/", safe="/")
    items = []
    for code in i18n.LANGS:
        current = ' aria-current="true"' if code == i18n.lang() else ""
        items.append(f'<a class="lang" href="/lang/{code}?next={here}" hreflang="{code}" '
                     f'lang="{code}" title="{i18n.NAMES[code]}"{current}>'
                     f'{i18n.FLAGS[code]}<span>{code.upper()}</span></a>')
    label = e(_("Język"))
    return f'<span class="lang-switch" role="group" aria-label="{label}">{"".join(items)}</span>'

# Okno potwierdzenia, blokada podwojnego wyslania i pasek oczekiwania.
# Zwykly tekst (nie f-string), zeby nie podwajac klamer.
PANEL_JS = r"""
document.documentElement.classList.add("js");   // bez JS pasek zapisu jest widoczny zawsze

// --- niezapisane zmiany: pasek "Zapisz zmiany" dopiero po zmianie pola ---
function wgSnapshot(form) {
  var out = {};
  Array.prototype.forEach.call(form.elements, function (el) {
    if (!el.name || el.type === "hidden" || el.type === "submit" || el.type === "button") return;
    var key = el.name + (el.type === "radio" ? ":" + el.value : "");
    out[key] = (el.type === "checkbox" || el.type === "radio") ? el.checked : el.value;
  });
  return out;
}
function wgTrackChanges(form) {
  var bar = document.getElementById(form.dataset.trackChanges);
  var initial = wgSnapshot(form), submitting = false;
  function dirty() {
    var now = wgSnapshot(form);
    for (var key in initial) { if (initial[key] !== now[key]) return true; }
    return false;
  }
  function update() {
    var changed = dirty();
    if (bar) bar.classList.toggle("dirty", changed);
    document.body.classList.toggle("has-save-bar", changed);
  }
  // pola z atrybutem form= leza w innej karcie - ich zdarzenia nie dochodza do formularza
  ["input", "change"].forEach(function (type) {
    document.addEventListener(type, function (ev) { if (ev.target.form === form) update(); });
  });
  form.addEventListener("submit", function () { submitting = true; });
  window.addEventListener("beforeunload", function (ev) {
    if (!submitting && dirty()) { ev.preventDefault(); ev.returnValue = ""; }
  });
  document.querySelectorAll('[data-reset="' + form.id + '"]').forEach(function (button) {
    button.addEventListener("click", function () {
      form.reset();
      Array.prototype.forEach.call(form.elements, function (el) {   // pokaz/ukryj pola zalezne
        if (el.tagName === "SELECT" && el.getAttribute("onchange"))
          el.dispatchEvent(new Event("change", {bubbles: true}));
      });
      update();
    });
  });
}
document.addEventListener("DOMContentLoaded", function () {
  document.querySelectorAll("form[data-track-changes]").forEach(wgTrackChanges);
});

// --- kopie: tresc okna usuwania zalezy od tego, co zaznaczono ---
function wgBackupPick() {
  var form = document.getElementById("backup-delete");
  if (!form) return;
  var local = 0, smb = 0;
  document.querySelectorAll('input[form="backup-delete"]:checked').forEach(function (box) {
    var row = box.closest("tr");
    if (row && row.dataset.where === "smb") smb++; else local++;
  });
  form.dataset.confirm = smb ? form.dataset.textSmb : form.dataset.textLocal;
  form.dataset.confirmDetail = form.dataset.detail.replace("{n}", local + smb)
    .replace("{local}", local).replace("{smb}", smb);
  var button = form.querySelector("button.danger");
  if (button) button.disabled = local + smb === 0;
}
document.addEventListener("DOMContentLoaded", function () {
  document.querySelectorAll('input[form="backup-delete"]').forEach(function (box) {
    box.addEventListener("change", wgBackupPick);
  });
  wgBackupPick();
});

// --- telefon: menu nawigacji i tabele jako listy ---
document.addEventListener("DOMContentLoaded", function () {
  var menu = document.querySelector(".menu-toggle");
  if (menu) {
    var header = menu.closest("header");
    var close = function () {
      header.classList.remove("nav-open");
      menu.setAttribute("aria-expanded", "false");
    };
    menu.addEventListener("click", function () {
      var open = header.classList.toggle("nav-open");
      menu.setAttribute("aria-expanded", open ? "true" : "false");
    });
    document.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape" && header.classList.contains("nav-open")) { close(); menu.focus(); }
    });
  }
  // na waskim ekranie wiersz tabeli staje sie lista - podpisy bierzemy z naglowkow kolumn
  document.querySelectorAll("table.stack").forEach(function (table) {
    var heads = Array.prototype.map.call(table.querySelectorAll("thead th"), function (th) {
      return th.textContent.replace(/[\u25B2\u25BC]/g, "").trim();
    });
    table.querySelectorAll("tbody tr").forEach(function (tr) {
      Array.prototype.forEach.call(tr.children, function (td, i) {
        if (td.colSpan > 1 || !heads[i] || td.hasAttribute("data-label")) return;
        td.setAttribute("data-label", heads[i]);
      });
    });
  });
});

var wgAsking = null;
function wgOpenConfirm(form, submitter) {
  var d = form.dataset, dlg = document.getElementById("wg-confirm");
  if (!dlg || typeof dlg.showModal !== "function") {      // stara przegladarka
    var text = (d.confirmTitle ? d.confirmTitle + "\n\n" : "") + d.confirm
             + (d.confirmDetail ? "\n\n" + d.confirmDetail : "");
    if (window.confirm(text)) wgSubmitConfirmed(form, submitter);
    return;
  }
  wgAsking = {form: form, submitter: submitter};
  var kind = d.confirmKind === "apply" ? "apply" : "danger";
  document.getElementById("wg-confirm-title").textContent = d.confirmTitle || WG_T.sure;
  document.getElementById("wg-confirm-text").textContent = d.confirm;
  var detail = document.getElementById("wg-confirm-detail");
  detail.textContent = d.confirmDetail || "";
  detail.hidden = !d.confirmDetail;
  var ok = document.getElementById("wg-confirm-ok");
  ok.textContent = d.confirmOk || WG_T.yes;
  ok.className = kind === "apply" ? "primary" : "danger solid";
  dlg.className = "wg-dialog " + kind;
  dlg.returnValue = "";
  dlg.showModal();
  // przy akcji niszczacej Enter nie moze niczego skasowac - fokus na Anuluj
  (kind === "apply" ? ok : document.getElementById("wg-confirm-cancel")).focus();
}
function wgSubmitConfirmed(form, submitter) {
  form.dataset.confirmed = "1";
  if (form.requestSubmit) {
    if (submitter && submitter.form === form) form.requestSubmit(submitter);
    else form.requestSubmit();
  } else {
    form.submit();
  }
}
document.addEventListener("submit", function (ev) {
  var form = ev.target;
  if (!form || (form.getAttribute("method") || "").toLowerCase() !== "post") return;
  if (form.dataset.confirm && form.dataset.confirmed !== "1") {
    ev.preventDefault();
    wgOpenConfirm(form, ev.submitter || null);
    return;
  }
  form.dataset.confirmed = "";
  // drugie klikniecie w trakcie wysylania nie wysyla drugi raz (np. dwa zastosowania)
  if (form.dataset.busy === "1") { ev.preventDefault(); return; }
  form.dataset.busy = "1";
  var button = ev.submitter || form.querySelector("button:not([type=button])");
  if (button) button.classList.add("busy");
  setTimeout(function () {                       // pobieranie pliku nie zmienia strony
    form.dataset.busy = "";
    if (button) button.classList.remove("busy");
  }, 5000);
});
window.addEventListener("pageshow", function () {   // powrot przyciskiem "wstecz"
  document.querySelectorAll("form[data-busy]").forEach(function (form) {
    form.dataset.busy = "";
  });
  document.querySelectorAll("button.busy").forEach(function (b) { b.classList.remove("busy"); });
});
document.addEventListener("DOMContentLoaded", function () {
  var dlg = document.getElementById("wg-confirm");
  if (dlg) {
    dlg.addEventListener("close", function () {
      var asking = wgAsking;
      wgAsking = null;
      if (asking && dlg.returnValue === "ok") wgSubmitConfirmed(asking.form, asking.submitter);
    });
    dlg.addEventListener("click", function (ev) {       // klikniecie w tlo = Anuluj
      if (ev.target === dlg) dlg.close("cancel");
    });
  }
  document.querySelectorAll(".banner .banner-close").forEach(function (button) {
    button.addEventListener("click", function () { button.closest(".banner").remove(); });
  });

  var bar = document.getElementById("wg-pending");
  if (!bar) return;
  var left = document.getElementById("wg-pending-left");
  var known = bar.dataset.remaining !== undefined;
  var end = Date.now() + (parseInt(bar.dataset.remaining || "0", 10) * 1000);
  var done = false;
  function tick() {
    if (done || !known || !left) return;
    var s = Math.max(0, Math.ceil((end - Date.now()) / 1000));
    left.textContent = s;
    bar.classList.toggle("late", s <= 15);
  }
  function finish(data, stay) {
    done = true;
    if (bar.classList.contains("auto-wait") && data.notice_kind === "ok") {
      bar.remove();                 // pasek nie zdazyl sie pokazac - wystarczy komunikat
      wgFlash(data.notice);
      return;
    }
    bar.classList.remove("auto-wait");
    if (location.pathname === "/" && !stay) {  // lista peerow ma pokazac nowy stan
      var q = data.notice ? (data.notice_kind === "err" ? "?err=" : "?msg=")
                            + encodeURIComponent(data.notice) : "";
      location.replace("/" + q);
      return;
    }
    bar.classList.remove("late");
    bar.classList.add("resolved", data.notice_kind === "err" ? "err" : "ok");
    bar.querySelector(".pending-text").textContent =
      data.notice || WG_T.not_pending;
    var actions = bar.querySelector(".pending-actions");
    if (actions) actions.remove();
  }
  function poll() {
    fetch("/api/pending", {headers: {"Accept": "application/json"}, cache: "no-store"})
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (!data || done) return;
        if (!data.pending) { finish(data); return; }
        if (data.remaining !== null && data.remaining !== undefined) {
          known = true;
          end = Date.now() + data.remaining * 1000;
        }
      })
      .catch(function () {});
  }
  function loop() {
    if (done) return;
    var soon = known && end - Date.now() < 3000;
    setTimeout(function () { poll(); loop(); }, soon ? 1000 : 4000);
  }
  // tryb automatyczny: przegladarka sama potwierdza, ze po zmianie dociera do panelu.
  // Serwer przyjmuje tylko polaczenie nawiazane po zmianie - stare zamyka ("retry").
  function autoConfirm() {
    if (done) return;
    var form = bar.querySelector('form[action="/confirm"]');
    if (!form) return;
    var body = new URLSearchParams(new FormData(form));
    body.append("auto", "1");
    fetch("/confirm", {method: "POST", body: body, cache: "no-store",
                       headers: {"Accept": "application/json"}})
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (res) {
        if (done) return;
        if (res && res.ok) { finish({notice: res.message, notice_kind: "ok"}, true); return; }
        if (res && res.manual) return;                          // potwierdzenie reczne
        if (res && res.done) { finish({notice: res.error, notice_kind: "err"}); return; }
        setTimeout(autoConfirm, res && res.retry ? 200 : 2000);
      })
      .catch(function () { if (!done) setTimeout(autoConfirm, 2000); });
  }
  tick();
  setInterval(tick, 500);
  loop();
  if (bar.dataset.auto === "1") {
    setTimeout(autoConfirm, 150);
    // pasek tylko wtedy, gdy sprawdzenie sie przeciaga - wtedy trzeba wiedziec, ze zmiana czeka
    setTimeout(function () { if (!done) bar.classList.remove("auto-wait"); }, 3000);
  }
});
function wgFlash(text) {
  var main = document.querySelector("main");
  if (!main || !text) return;
  var banner = document.createElement("div");
  banner.className = "banner ok";
  banner.setAttribute("role", "status");
  banner.textContent = text;
  main.insertBefore(banner, main.firstChild);
  setTimeout(function () { banner.style.display = "none"; }, 6000);
}
"""


def page(title: str, body: str, sess=None, flash: str = "", error: str = "",
         alerts: int = 0) -> str:
    toggle = ('<button class="theme-toggle" id="theme-toggle" type="button" '
              f'onclick="wgTheme()">{_("motyw")}</button>')
    switch = lang_switch()
    alert_badge = f' <span class="badge dead">{alerts}</span>' if alerts else ""
    menu = ""
    if sess:
        here = (getattr(REQUEST, "path", "/") or "/").split("?")[0]
        peers_section = here == "/" or here.startswith(("/peers", "/apply", "/disk"))

        def link(href: str, label: str) -> str:
            active = peers_section if href == "/" else here.startswith(href)
            current = ' aria-current="page"' if active else ""
            return f'<a href="{href}"{current}>{label}</a>'

        links = "".join(link(href, label) for href, label in (
            ("/", _("Peery")), ("/tiers", _("Tiery")), ("/hosts", _("Hosty")),
            ("/simulator", _("Symulator")), ("/alerts", _("Alerty") + alert_badge),
            ("/backups", _("Kopie")), ("/settings", _("Ustawienia")), ("/audit", _("Dziennik"))))
        # na telefonie linki chowaja sie pod przyciskiem - licznik alertow zostaje widoczny
        menu = ('<button type="button" class="menu-toggle" aria-expanded="false" '
                f'aria-controls="main-nav">{_("Menu")}{alert_badge}</button>')
        nav_label, logout_label = e(_("Główna nawigacja")), _("wyloguj")
        nav = f"""
        <nav id="main-nav" aria-label="{nav_label}">
          {links}
          <span class="spacer"></span>
          <span class="nav-account">
            {switch}{toggle}
            <span class="who">{e(sess['username'])}</span>
            <form method="post" action="/logout" class="inline">
              <input type="hidden" name="csrf" value="{e(sess['csrf'])}">
              <button class="link">{logout_label}</button>
            </form>
          </span>
        </nav>"""
    else:
        nav = f'<nav><span class="spacer"></span><span class="nav-account">{switch}{toggle}</span></nav>' 
    banner = ""
    state = {}
    if sess and getattr(REQUEST, "pending_fn", None):
        state = REQUEST.pending_fn() or {}
    errors = [text for text in (error, getattr(REQUEST, "err", ""), state.get("notice", ""))
              if text]
    for text in dict.fromkeys(errors):            # bez powtorzen tego samego komunikatu
        banner += (f'<div class="banner err" role="alert"><span>{e(text)}</span>'
                   f'<button type="button" class="banner-close" aria-label="{e(_("Zamknij"))}">'
                   '&times;</button></div>')
    if flash:
        banner += f'<div class="banner ok">{e(flash)}</div>'
    extras = (pending_bar(sess, state) + confirm_dialog()) if sess else ""
    return f"""<!doctype html>
<html lang="{i18n.lang()}" data-theme="dark"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark light">
<title>{e(title)} — wg-panel</title>
<script>
// ustawiamy motyw przed pierwszym malowaniem, zeby nie mrugalo bialym
(function () {{
  var saved = null;
  try {{ saved = localStorage.getItem("wgpanel-theme"); }} catch (err) {{}}
  if (!saved) {{
    saved = (window.matchMedia && matchMedia("(prefers-color-scheme: light)").matches)
      ? "light" : "dark";
  }}
  document.documentElement.setAttribute("data-theme", saved);
}})();
function wgTheme() {{
  var root = document.documentElement;
  var next = root.getAttribute("data-theme") === "light" ? "dark" : "light";
  root.setAttribute("data-theme", next);
  try {{ localStorage.setItem("wgpanel-theme", next); }} catch (err) {{}}
  wgThemeLabel();
}}
function wgThemeLabel() {{
  var btn = document.getElementById("theme-toggle");
  if (btn) {{
    btn.textContent =
      document.documentElement.getAttribute("data-theme") === "light" ? WG_T.theme_dark : WG_T.theme_light;
  }}
}}
document.addEventListener("DOMContentLoaded", wgThemeLabel);
function wgLoadDiff() {{
  var box = document.getElementById("wg-diff");
  if (!box || box.dataset.loaded) return;
  fetch("/apply/diff")
    .then(function (r) {{ return r.text(); }})
    .then(function (text) {{ box.textContent = text; box.dataset.loaded = "1"; }})
    .catch(function () {{ box.textContent = WG_T.diff_error; }});
}}
function wgDiff(button) {{
  var box = document.getElementById("krok-szczegoly");
  if (!box) return;
  box.hidden = !box.hidden;
  button.setAttribute("aria-expanded", box.hidden ? "false" : "true");
  if (!box.hidden) wgLoadDiff();
}}
function wgToggle(id, button) {{
  var box = document.getElementById(id);
  if (!box) return;
  var open = box.classList.toggle("hidden") === false;
  button.textContent = open ? "Zwin" : button.dataset.label || button.textContent;
  if (!button.dataset.label && !open) button.dataset.label = button.textContent;
}}
document.addEventListener("DOMContentLoaded", function () {{
  // zielony pasek znika sam i nie wraca po odswiezeniu strony
  var flash = document.querySelector(".banner.ok, .flash");
  if (flash) setTimeout(function () {{ flash.style.display = "none"; }}, 6000);
  if (window.history.replaceState && /[?&](msg|err)=/.test(location.search)) {{
    var url = new URL(location.href);
    url.searchParams.delete("msg");
    url.searchParams.delete("err");
    window.history.replaceState({{}}, "", url.pathname + (url.search || "") + url.hash);
  }}
}});
function wgHandshakeText(seconds, now) {{
  if (!seconds) return [WG_T.never, "dead"];
  var d = now - seconds;
  var state = d < 180 ? "live" : (d < 3600 ? "stale" : "dead");
  if (d < 60) return [WG_T.s_ago.replace("{{n}}", d), state];
  if (d < 3600) return [WG_T.min_ago.replace("{{n}}", Math.floor(d / 60)), state];
  if (d < 86400) return [WG_T.h_ago.replace("{{n}}", Math.floor(d / 3600)), state];
  return [WG_T.d_ago.replace("{{n}}", Math.floor(d / 86400)), state];
}}
function wgBytes(value) {{
  var units = ["B", "KiB", "MiB", "GiB", "TiB"], i = 0;
  while (value >= 1024 && i < units.length - 1) {{ value /= 1024; i++; }}
  return (i === 0 ? value : value.toFixed(1)) + " " + units[i];
}}
function wgTick() {{
  var now = Math.floor(Date.now() / 1000);
  document.querySelectorAll("td.hs[data-handshake]").forEach(function (cell) {{
    var out = wgHandshakeText(parseInt(cell.dataset.handshake || "0", 10), now);
    cell.textContent = out[0];
    cell.className = "hs " + out[1] + " c-hs";   // c-hs: uklad karty na telefonie
  }});
}}
function wgPoll() {{
  fetch("/api/peers", {{ headers: {{ "Accept": "application/json" }} }})
    .then(function (r) {{ return r.ok ? r.json() : null; }})
    .then(function (data) {{
      if (!data) return;
      var iface = document.getElementById("stat-iface");
      if (iface) iface.textContent = iface.textContent.split(" ")[0] + (data.up ? " up" : " down");
      var svc = document.getElementById("stat-service");
      if (svc) svc.textContent = data.service;
      var act = document.getElementById("stat-active");
      if (act) act.textContent = data.active;
      data.peers.forEach(function (peer) {{
        var row = document.querySelector('tr[data-peer="' + peer.id + '"]');
        if (!row) return;
        var cell = row.querySelector("td.hs");
        if (cell) cell.dataset.handshake = peer.handshake;
        var transfer = row.querySelector("td[data-transfer]");
        if (transfer && (peer.rx || peer.tx)) {{
          transfer.textContent = wgBytes(peer.rx) + " / " + wgBytes(peer.tx);
        }}
      }});
      wgTick();
    }})
    .catch(function () {{}});
}}
document.addEventListener("DOMContentLoaded", function () {{
  if (!document.querySelector("td.hs")) return;   // tylko lista peerow
  wgTick();
  setInterval(wgTick, 1000);      // sekundy licza sie lokalnie, bez ruchu w sieci
  setInterval(wgPoll, 5000);      // swieze dane z interfejsu co 5 s
}});
function wgCopy(id, button) {{
  var text = document.getElementById(id).textContent;
  var done = function () {{ var old = button.textContent; button.textContent = WG_T.copied;
    setTimeout(function () {{ button.textContent = old; }}, 1500); }};
  if (navigator.clipboard && window.isSecureContext) {{
    navigator.clipboard.writeText(text).then(done); return;
  }}
  var area = document.createElement("textarea");  /* http:// - bez Clipboard API */
  area.value = text; area.style.position = "fixed"; area.style.opacity = "0";
  document.body.appendChild(area); area.select();
  try {{ document.execCommand("copy"); done(); }} catch (err) {{}}
  document.body.removeChild(area);
}}
function wgPickFailed() {{
  document.querySelectorAll('tr[data-failed] input[name="id"]').forEach(function (box) {{
    box.checked = true;
  }});
  wgBackupPick();
}}

function wgTlsMode(select) {{
  select.form.querySelector(".tls-custom").hidden = select.value !== "custom";
}}

function wgTunnelList(select) {{
  // na stronie peera lista wyboru nalezy do wspolnego formularza, ale lezy w swojej karcie
  var scope = select.closest("form") || select.closest(".card") || document;
  var box = scope.querySelector(".tunnel-list");
  if (box) box.style.display = select.value === "custom" ? "" : "none";
}}
document.addEventListener("DOMContentLoaded", function () {{
  document.querySelectorAll('select[name="tunnel_mode"], select[name="client_mode"]')
    .forEach(wgTunnelList);
}});
function wgAccessMode(select) {{
  var form = select.closest("form");
  form.querySelectorAll(".acc").forEach(function (item) {{
    var forWindow = item.classList.contains("acc-window");
    item.style.display =
      (select.value === "window" && forWindow) ||
      (select.value === "from_use" && !forWindow) ? "" : "none";
  }});
}}
document.addEventListener("DOMContentLoaded", function () {{
  document.querySelectorAll('select[name="access_mode"]').forEach(wgAccessMode);
}});
</script>
<script>var WG_T = {js_texts()};</script>
<script>{PANEL_JS}</script>
<link rel="stylesheet" href="/static/style.css">
</head><body>
<header><a href="/" class="logo">wg-panel</a>{menu}{nav}</header>
{extras}
<main>{banner}{body}</main>
</body></html>"""


TLS_HELP = False     # ustawia app.py, gdy HTTPS jest wlaczone


def _tls_until(info: dict) -> str:
    stamp = info.get("not_after")
    return time.strftime("%Y-%m-%d", time.localtime(stamp)) if stamp else "—"


def tls_card(sess, info: dict, cfg: dict | None = None) -> str:
    if not info.get("enabled"):
        return ""
    cfg = cfg or {}
    mode = info.get("mode") or cfg.get("tls_mode", "self")
    until = "<strong>" + _tls_until(info) + "</strong>"
    if not info.get("on"):
        state = '<p class="banner err">' + _("HTTPS włączone, ale certyfikatu jeszcze nie ma.") + '</p>'
    elif mode == "custom":
        state = "<p>" + _("Panel działa po HTTPS z Twoim certyfikatem, ważnym do {date}. Gdy certbot "
                          "go odnowi, panel sam weźmie nowy (sprawdza co 12 h).", date=until) + "</p>"
    else:
        state = "<p>" + _("Panel działa po HTTPS. Certyfikat ważny do {date} - odnawia się sam "
                          "na 30 dni przed końcem i po zmianie adresu Pi.", date=until) + "</p>"
    error = (f'<p class="banner err"><span>{e(i18n.helper(info["error"]))}</span></p>'
             if info.get("error") else "")
    names = ", ".join(info.get("ips", []) + info.get("dns", []) + info.get("names", []))
    actions = f"""
        <div class="actions">
          <a class="button" href="/tls">{_("Certyfikat do zainstalowania na urządzeniach")}</a>
          <form method="post" action="/tls/renew" class="inline">
            {_csrf(sess)}
            <button>{_("Odnów certyfikat teraz")}</button>
          </form>
        </div>""" if mode == "self" else ""
    options = "".join(
        f'<option value="{key}"{" selected" if key == mode else ""}>{e(label)}</option>'
        for key, label in (("self", _("własny urząd certyfikacji panelu (domyślnie)")),
                           ("custom", _("własny certyfikat z plików (np. Let's Encrypt)"))))
    pi = cfg.get("server_lan") or "192.168.1.10"
    dns = (info.get("dns") or ["raspberrypi.local"])[-1]
    caddy = (f"@wgpanel host panel.example.com\nhandle @wgpanel {{\n"
             f"    reverse_proxy https://{pi}:{TLS_PORT} {{\n        transport http {{\n"
             f"            tls_trusted_ca_certs /etc/caddy/wg-panel-ca.crt\n        }}\n    }}\n}}")
    nginx = (f"location / {{\n    proxy_pass https://{pi}:{TLS_PORT};\n    proxy_ssl_verify on;\n"
             f"    proxy_ssl_trusted_certificate /etc/nginx/wg-panel-ca.crt;\n"
             f"    proxy_ssl_server_name on;\n    proxy_ssl_name {dns};\n"
             f"    proxy_set_header Host $host;\n"
             f"    proxy_set_header X-Forwarded-For $remote_addr;\n"
             f"    proxy_set_header X-Forwarded-Proto https;\n}}")
    proxies = cfg.get("proxy_trusted", "")
    ca_step = (_("Pobierz certyfikat urzędu panelu ({link}) i skopiuj go na serwer proxy - proxy "
                 "łączy się z Pi po HTTPS i sprawdza certyfikat panelu, więc odcinek do Pi też jest "
                 "szyfrowany.", link='<a href="/tls/ca.crt">wg-panel-ca.crt</a>') if mode == "self" else
               _("Przy własnym certyfikacie zamiast {option} ustaw w proxy nazwę z certyfikatu "
                 "({server_name}).", option="<code>tls_trusted_ca_certs</code>",
                 server_name="<code>tls_server_name</code>"))
    hide_custom = "" if mode == "custom" else " hidden"
    return f"""
      <div class="card">
        <h2>HTTPS</h2>
        {state}{error}
        <p class="muted">{_("Adresy w certyfikacie")}: {e(names) or "—"}</p>
        {actions}
        <form method="post" action="/tls/settings" class="row" id="tls-form">
          {_csrf(sess)}
          <label class="full">{_("Certyfikat")}<select name="tls_mode" onchange="wgTlsMode(this)">{options}</select></label>
          <div class="sub-row full tls-custom"{hide_custom}>
            <label>{_("Plik certyfikatu (pełny łańcuch)")}<input name="tls_cert_path"
                   value="{e(cfg.get('tls_cert_path', ''))}"
                   placeholder="/etc/letsencrypt/live/vpn.example.com/fullchain.pem"></label>
            <label>{_("Plik klucza prywatnego")}<input name="tls_key_path"
                   value="{e(cfg.get('tls_key_path', ''))}"
                   placeholder="/etc/letsencrypt/live/vpn.example.com/privkey.pem"></label>
            <p class="muted full">{_("Pliki muszą leżeć w {dirs}. Helper kopiuje je dla panelu, "
                                     "sprawdza, czy klucz pasuje do certyfikatu, i bierze nowe po "
                                     "odnowieniu przez certbota.",
                                     dirs="<code>/etc/letsencrypt/</code>, <code>/etc/ssl/wg-panel/</code>")}</p>
          </div>
          <details class="full proxy-box"{" open" if proxies else ""}>
            <summary>{_("Reverse proxy na innym serwerze (opcjonalnie)")}</summary>
            <p class="muted">{_("Np. Caddy na osobnym serwerze. Panel zostaje przy swoim HTTPS, a nagłówkom proxy "
                                "(X-Forwarded-For, X-Forwarded-Proto) ufa wyłącznie od podanych adresów - "
                                "od innych urządzeń są ignorowane.")}</p>
            <div class="sub-row">
              <label class="full">{_("Adres serwera proxy")}<input name="proxy_trusted"
                     value="{e(proxies)}" placeholder="192.168.1.20"></label>
            </div>
            <p class="muted">{ca_step}</p>
            <p class="muted">Caddy ({_("do bloku z Twoją domeną")}):</p>
            <pre>{e(caddy)}</pre>
            <details><summary>nginx</summary><pre>{e(nginx)}</pre></details>
            <p class="muted">{_("Uwaga: przez proxy stronę logowania zobaczy każdy, kto ma dostęp do "
                                "proxy - także osoby z VPN, którym panel sam by jej nie pokazał. Włącz "
                                "kod z aplikacji (TOTP) i w proxy ogranicz, kto może otworzyć panel. "
                                "Zapora Pi musi wpuszczać serwer proxy na port panelu (instalator: "
                                "{lan}).", lan="<code>LAN_ACCESS</code>")}</p>
          </details>
          <label class="full">{_("Publiczny adres panelu (do linków w powiadomieniach)")}
            <input name="panel_url" value="{e(cfg.get('panel_url', ''))}" placeholder="https://panel.example.com"></label>
          <button class="primary">{_("Zapisz")}</button>
        </form>
      </div>"""


def tls_page(sess, info: dict, via_tls: bool) -> str:
    """Publiczna: certyfikat instaluje sie, zanim da sie bezpiecznie zalogowac."""
    title = _("Certyfikat panelu")
    if info.get("enabled") and info.get("mode") == "custom":
        text = _("Panel używa certyfikatu z zaufanego urzędu (np. Let's Encrypt) - na urządzeniach "
                 "nic nie trzeba instalować.")
        return page(title, f"""
      <div class="card narrow">
        <h1>{title}</h1>
        <p>{text}</p>
      </div>""", sess)
    if not info.get("enabled") or not info.get("ca_fingerprint"):
        return page(title, f"""
      <div class="card narrow">
        <h1>{title}</h1>
        <p>{_("HTTPS nie jest włączone w tej instalacji panelu.")}</p>
      </div>""", sess)
    permitted = ", ".join(info.get("permitted", []) + info.get("dns", []))
    ips = info.get("ips", [])
    address = f"https://{ips[0]}:{TLS_PORT}" if ips else _("serwera w VPN")
    lead = (_("To połączenie jest już szyfrowane.") if via_tls else
            _("Otwórz tę stronę przez https://, żeby połączenie było szyfrowane."))

    def steps(items):
        return "<ol>" + "".join(f"<li>{item}</li>" for item in items) + "</ol>"

    ios = steps([_("Otwórz tę stronę w Safari i pobierz certyfikat. Zatwierdź „Pozwól”."),
                 _("Ustawienia → Pobrany profil (na samej górze) → Zainstaluj."),
                 _("Ustawienia → Ogólne → Informacje → Ustawienia zaufania certyfikatów → włącz "
                   "„wg-panel CA”. Bez tego kroku iPhone nadal ostrzega.")])
    android = steps([_("Pobierz certyfikat."),
                     _("Ustawienia → wyszukaj „certyfikat CA” (zwykle Bezpieczeństwo → Szyfrowanie "
                       "i dane logowania → Zainstaluj certyfikat → Certyfikat CA) → wybierz pobrany plik.")])
    windows = steps([_("Pobierz i otwórz plik → Zainstaluj certyfikat → Bieżący użytkownik."),
                     _("Umieść w magazynie „Zaufane główne urzędy certyfikacji” → Zakończ."),
                     _("Firefox ma własny magazyn: Ustawienia → Prywatność → Certyfikaty → Wyświetl → "
                       "Organy certyfikacji → Importuj.")])
    macos = steps([_("Pobierz i otwórz plik - trafi do Pęku kluczy."),
                   _("Kliknij dwa razy „wg-panel CA” → Zaufanie → „Zawsze ufaj”.")])
    return page(title, f"""
      <div class="card tls-page">
        <h1>{title}</h1>
        <p>{lead} {_("Żeby przeglądarka przestała ostrzegać, zainstaluj raz na każdym urządzeniu "
                     "certyfikat urzędu, który wystawia certyfikat panelu.")}</p>
        <p><a class="button primary" href="/tls/ca.crt">{_("Pobierz certyfikat")}</a></p>
        <h2>{_("Sprawdź, czy to ten sam certyfikat")}</h2>
        <p>{_("Odcisk SHA-256 musi się zgadzać z tym, co wypisał instalator na Pi (albo polecenie "
              "{command}):", command="<code>wgctl tls</code>")}</p>
        <p><code class="fingerprint">{e(info["ca_fingerprint"])}</code></p>
        <p class="muted">{_("Ten urząd może poświadczać wyłącznie: {names}. Nawet gdyby ktoś wykradł "
                            "jego klucz, nie podrobi nim żadnej innej strony.", names=e(permitted))}</p>
        <h2>{_("Instalacja")}</h2>
        <details><summary>{_("iPhone i iPad")}</summary>{ios}</details>
        <details><summary>Android</summary>{android}</details>
        <details><summary>{_("Windows (Chrome, Edge)")}</summary>{windows}</details>
        <details><summary>macOS</summary>{macos}</details>
        <p class="muted">{_("Po instalacji otwieraj panel przez https:// - przez VPN pod adresem "
                            "{address}, w domu pod adresem Pi w sieci lokalnej.", address=e(address))}</p>
      </div>""", sess)


TLS_PORT = 8080      # ustawia app.py


def login(error: str = "", totp: bool = True) -> str:
    tls_help = ('<p class="muted tls-help">' + _("Przeglądarka ostrzega o certyfikacie?") +
                ' <a href="/tls">' + _("Zainstaluj certyfikat panelu") + '</a> - ' +
                _("raz na każdym urządzeniu.") + '</p>' if TLS_HELP else "")
    totp_field = ('<label>' + _("Kod TOTP") + '<input name="totp" inputmode="numeric" '
                  'autocomplete="one-time-code"></label>' if totp else "")
    error_box = '<div class="banner err">' + e(error) + '</div>' if error else ""
    return page(_("Logowanie"), f"""
      <div class="card narrow">
        <h1>{_("Logowanie")}</h1>
        {error_box}
        <form method="post" action="/login">
          <label>{_("Użytkownik")}<input name="username" autofocus autocomplete="username"></label>
          <label>{_("Hasło")}<input type="password" name="password" autocomplete="current-password"></label>
          {totp_field}
          <button class="primary">{_("Zaloguj")}</button>
        </form>
        {tls_help}
      </div>""")


def setup(error: str = "") -> str:
    error_box = '<div class="banner err">' + e(error) + '</div>' if error else ""
    return page(_("Konfiguracja początkowa"), f"""
      <div class="card narrow">
        <h1>{_("Pierwsze uruchomienie")}</h1>
        <p>{_("Załóż konto administratora. Kod z aplikacji (TOTP) jest opcjonalny - możesz go "
              "włączyć albo wyłączyć później w Ustawieniach.")}</p>
        {error_box}
        <form method="post" action="/setup">
          <label>{_("Użytkownik")}<input name="username" value="admin"></label>
          <label>{_("Hasło")}<input type="password" name="password" autocomplete="new-password"></label>
          <label>{_("Powtórz hasło")}<input type="password" name="password2" autocomplete="new-password"></label>
          <label class="check"><input type="checkbox" name="totp" value="1">
            {_("Kod z aplikacji przy logowaniu (opcjonalnie)")}</label>
          <button class="primary">{_("Załóż konto")}</button>
        </form>
      </div>""")


def totp_enrolled(secret: str, uri: str, qr_svg: str) -> str:
    return page("TOTP", f"""
      <div class="card narrow">
        <h1>{_("Dodaj do aplikacji TOTP")}</h1>
        <div class="qr">{qr_svg}</div>
        <p>{_("Sekret")}: <code>{e(secret)}</code></p>
        <p class="muted">{e(uri)}</p>
        <p><a class="button primary" href="/login">{_("Przejdź do logowania")}</a></p>
      </div>""")


def _handshake(seconds: int) -> tuple:
    if not seconds:
        return _("nigdy"), "dead"
    delta = int(time.time()) - seconds
    if delta < 180:
        state = "live"
    elif delta < 3600:
        state = "stale"
    else:
        state = "dead"
    if delta < 60:
        return _("{n} s temu", n=delta), state
    if delta < 3600:
        return _("{n} min temu", n=delta // 60), state
    if delta < 86400:
        return _("{n} h temu", n=delta // 3600), state
    return _("{n} dni temu", n=delta // 86400), state


def _bytes(value: int) -> str:
    step = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if step < 1024:
            return f"{step:.1f} {unit}"
        step /= 1024
    return f"{step:.1f} PiB"


def dashboard(sess, cfg, peers, status, dirty: bool, pending, helper_error: str = "",
              flash: str = "", alerts: int = 0, expiry: dict | None = None,
              warnings: dict | None = None, drift: list | None = None,
              chart: list | None = None, rolled_back: bool = False,
              can_discard: bool = False, diff_parts: list | None = None,
              discard_enough: bool = False) -> str:
    expiry = expiry or {}
    warnings = warnings or {}
    rows = []
    live = (status or {}).get("peers", {})
    labels = {"ip": "IP", "tier": _("Tier"), "hs": "Handshake", "traffic": _("Ruch"),
              "until": _("Ważny do")}
    for peer in peers:
        stat = live.get(peer["pubkey"], {})
        when, css = _handshake(stat.get("latest_handshake", 0))
        state_badge = {
            "active": '<span class="badge ok">' + _("aktywny") + '</span>',
            "suspended": '<span class="badge warn">' + _("zawieszony") + '</span>',
        }.get(peer["state"], "")
        transfer = ""
        if stat:
            transfer = f'{_bytes(stat.get("rx", 0))} / {_bytes(stat.get("tx", 0))}'
        warning = ('<span class="badge warn">' + e(warnings[peer["id"]]) + '</span>'
                   if warnings.get(peer["id"]) else "")
        rows.append(f"""
          <tr data-peer="{peer['id']}">
            <td class="c-name"><a href="/peers/{peer['id']}"><strong>{e(peer['name'])}</strong></a>
                {warning}
                <div class="muted">{e(peer['device'])}</div></td>
            <td class="c-ip" data-label="{labels['ip']}"><code>{e(peer['ip'])}</code></td>
            <td class="c-tier" data-label="{e(labels['tier'])}">{e(peer['tier_name'] or '—')}</td>
            <td class="c-state">{state_badge}</td>
            <td class="hs {css} c-hs" data-label="{labels['hs']}"
                data-handshake="{stat.get('latest_handshake', 0)}">{e(when)}</td>
            <td class="muted transfer c-transfer" data-label="{e(labels['traffic'])}" data-transfer="1">{e(transfer)}</td>
            <td class="muted c-exp" data-label="{e(labels['until'])}">{e(expiry.get(peer['id'], '—'))}</td>
          </tr>""")

    if not rows:
        rows.append('<tr><td colspan="7" class="muted">' + _("Brak peerów. Dodaj pierwszego.") +
                    '</td></tr>')

    apply_box = ""
    # gdy zmiana czeka na potwierdzenie, mowi o tym tylko pasek u gory - bez drugiej karty
    if dirty and not pending:
        parts = list(diff_parts or [])
        what = ("<p>" + _("Różni się: {what}.", what="<strong>" + e(", ".join(parts)) + "</strong>")
                + "</p>" if parts else "")
        if rolled_back:
            css, title = "pending", _("Zmiany zostały cofnięte")
            lead = "<p>" + _("Na interfejsie działa poprzednia konfiguracja. W panelu została "
                             "nowsza wersja - ta, którą widzisz na liście poniżej.") + "</p>"
            hint = _("Chcesz wrócić w panelu do tego, co działa: odrzuć zmiany. Chcesz jednak "
                     "wprowadzić nową wersję: zastosuj ją ponownie.")
        else:
            css, title, lead = "dirty", _("Panel ma zmiany, których nie ma na interfejsie"), ""
            if not can_discard:
                hint = _("Panel nie zna jeszcze stanu sprzed zmian - po pierwszym zastosowaniu "
                         "będzie można do niego wracać.")
            elif discard_enough:
                hint = _("Różnica pochodzi z Twoich zmian w panelu.")
            else:
                hint = _("Część różnicy nie pochodzi z Twoich zmian - pliki na dysku zapisała "
                         "starsza wersja panelu. Ta część zniknie po jednym zastosowaniu.")
        discard_form = f"""
            <form method="post" action="/discard" class="inline"{discard_ask()}>
              {_csrf(sess)}
              <button class="danger">{_("Odrzuć zmiany")}</button>
            </form>""" if can_discard else ""
        apply_label = _("Zastosuj ponownie") if rolled_back else _("Zastosuj zmiany")
        details = _("Co dokładnie się zmieni.")
        details_hint = _("Po lewej stan obecny, po prawej to, co panel zapisze.")
        disk_hint = _("{preview}. {disk} - kto jest tylko w panelu, a kto tylko w działającej "
                      "konfiguracji.",
                      preview='<a href="/apply">' + _("Pełny podgląd zmian") + '</a>',
                      disk='<a href="/disk">' + _("Porównanie peerów z dyskiem") + '</a>')
        apply_box = f"""
        <div class="card {css}" id="okno-zmian">
          <h2>{title}</h2>
          {lead}{what}
          <p class="muted">{e(hint)}</p>
          <div class="actions">
            <form method="post" action="/apply" class="inline">
              {_csrf(sess)}
              <button class="primary">{apply_label}</button>
            </form>{discard_form}
            <button type="button" aria-expanded="false" aria-controls="krok-szczegoly"
                    onclick="wgDiff(this)">{_("Szczegóły")}</button>
          </div>
          <div id="krok-szczegoly" class="diff-box" hidden>
            <p><strong>{details}</strong> {details_hint}</p>
            <pre id="wg-diff" class="diff">{_("wczytuję...")}</pre>
            <p class="muted">{disk_hint}</p>
          </div>
        </div>"""

    svc = (status or {}).get("service", "?")
    up = (status or {}).get("up")
    active = sum(1 for p in peers if p["state"] == "active")
    health = f"""
      <div class="stats">
        <div><span class="muted">{_("interfejs")}</span><strong id="stat-iface">{e(cfg['wg_iface'])} {'up' if up else 'down'}</strong></div>
        <div><span class="muted">{_("usługa")}</span><strong id="stat-service">{e(svc)}</strong></div>
        <div><span class="muted">{_("port")}</span><strong>{e(cfg['listen_port'])}/udp</strong></div>
        <div><span class="muted">{_("peery aktywne")}</span><strong id="stat-active">{active}</strong></div>
      </div>"""

    drift_box = ""
    if drift:
        items = "".join(f"<li>{e(i18n.helper(f['message']))}</li>" for f in drift[:6])
        drift_box = f"""
        <div class="card tamper">
          <h2>{_("Konfiguracja zmieniona poza panelem")}</h2>
          <ul>{items}</ul>
          <p class="muted">{_("Jeśli to nie Ty, ktoś ma dostęp do Raspberry Pi. Zobacz szczegóły "
                              "i przywróć stan z panelu.")}</p>
          <a class="button danger" href="/integrity">{_("Szczegóły")}</a>
        </div>"""

    chart_box = ""
    if chart and any(p["rx"] or p["tx"] for p in chart):
        chart_box = f"""
        <div class="card">
          <h2>{_("Ruch w tunelu - ostatnie 7 dni")}</h2>
          {traffic_chart(chart)}
        </div>"""

    heads = "".join(f"<th>{h}</th>" for h in (
        _("Nazwa"), "IP", _("Tier"), _("Stan"), "Handshake", "rx / tx", _("Ważny do")))
    body = f"""
      {health}
      {drift_box}
      {apply_box}
      <div class="card">
        <div class="head">
          <h1>{_("Peery")}</h1>
          <a class="button" href="/peers/new">{_("Dodaj peera")}</a>
        </div>
        <table class="peers-list">
          <thead><tr>{heads}</tr></thead>
          <tbody>{''.join(rows)}</tbody>
        </table>
      </div>
      {chart_box}"""
    return page(_("Peery"), body, sess, flash=flash, error=helper_error, alerts=alerts)


def peer_new(sess, tiers, suggested_ip: str, error: str = "", alerts: int = 0) -> str:
    options = "".join(
        f'<option value="{t["id"]}">{e(t["name"])} — {e(i18n.maybe(t["description"]))}</option>'
        for t in tiers
    )
    body = f"""
      <div class="card narrow">
        <h1>{_("Nowy peer")}</h1>
        <form method="post" action="/peers/new">
          <input type="hidden" name="csrf" value="{e(sess['csrf'])}">
          <label>{_("Nazwa")}<input name="name" required autofocus placeholder="Marek"></label>
          <label>{_("Urządzenie")}<input name="device" placeholder="{e(_("laptop / telefon"))}"></label>
          <label>{_("Adres VPN")}<input name="ip" value="{e(suggested_ip)}"></label>
          <label>{_("Tier")}<select name="tier_id">{options}</select></label>
          <label>{_("Tryb dostępu")}<select name="access_mode" onchange="wgAccessMode(this)">
            <option value="none" selected>{_("bezterminowo")}</option>
            <option value="window">{_("okno terminowe (od - do)")}</option>
            <option value="from_use">{_("N dni od pierwszego połączenia")}</option>
          </select></label>
          <label class="acc acc-window">{_("Od dnia")}<input type="date" name="starts"
                 value="" title="{e(_("puste = od zaraz"))}"></label>
          <label class="acc acc-window">{_("Do dnia")}<input type="date" name="expires"
                 value="" title="{e(_("puste = bez końca"))}"></label>
          <label class="acc acc-use">{_("Ile dni")}<input name="expires_days"
                 value="" inputmode="numeric" placeholder="{e(_("np. 7"))}"></label>
          <label>{_("Notatka")}<input name="note"></label>
          <button class="primary">{_("Utwórz")}</button>
          <a class="button" href="/">{_("Anuluj")}</a>
        </form>
      </div>"""
    return page(_("Nowy peer"), body, sess, error=error, alerts=alerts)


def peer_detail(sess, cfg, peer, rules, tiers, client_conf, qr_svg, stat, delivery_url,
                flash: str = "", error: str = "", alerts: int = 0,
                expiry_text: str = "", warning: str = "",
                chart: list | None = None, route: dict | None = None,
                issued: dict | None = None, just_created: bool = False) -> str:
    route = route or {"mode": "auto", "own": False, "allowed": [], "notes": []}
    issued = issued or {"state": "unknown"}
    expiry_text = expiry_text or _("bezterminowo")
    csrf = _csrf(sess)
    tier_options = "".join(
        f'<option value="{t["id"]}"{" selected" if t["id"] == peer["tier_id"] else ""}>'
        f'{e(t["name"])}</option>' for t in tiers
    )
    rule_rows = []
    for rule in rules:
        window = ""
        if rule["time_from"]:
            window = f'{e(rule["time_from"])}–{e(rule["time_to"])} {e(rule["weekdays"])}'
        ports = f':{e(rule["ports"])}' if rule["ports"] else ""
        target = f"{rule['dest']}{(':' + str(rule['ports'])) if rule['ports'] else ''}"
        rule_ask = ask(_("Usunąć regułę?"),
                       _("{action} do {target}. Zmiana zadziała po zastosowaniu.",
                         action=rule["action"], target=target),
                       _("Usuń regułę"))
        badge = "ok" if rule["action"] == "ACCEPT" else "dead"
        rule_rows.append(f"""
          <tr>
            <td><span class="badge {badge}">{e(rule['action'])}</span></td>
            <td><code>{e(rule['dest'])}{ports}</code> <span class="muted">{e(rule['proto'])}</span></td>
            <td class="muted">{window}</td>
            <td class="muted">{e(rule['comment'])}</td>
            <td>
              <form method="post" action="/peers/{peer['id']}/rules/{rule['id']}/delete" class="inline"{rule_ask}>
                {csrf}
                <button class="link danger">{_("usuń")}</button>
              </form>
            </td>
          </tr>""")
    if not rule_rows:
        rule_rows.append('<tr><td colspan="5" class="muted">' +
                         _("Brak reguł własnych - obowiązuje sam tier.") + '</td></tr>')

    when, css = _handshake((stat or {}).get("latest_handshake", 0))
    endpoint = (stat or {}).get("endpoint", "—")
    expires_value = (time.strftime("%Y-%m-%d", time.localtime(peer["expires_at"]))
                     if peer["expires_at"] else "")
    starts_value = (time.strftime("%Y-%m-%d", time.localtime(peer["starts_at"]))
                    if peer["starts_at"] else "")
    days_value = peer["expires_days"] or ""
    mode = peer["access_mode"]
    m_none = " selected" if mode == "none" else ""
    m_window = " selected" if mode == "window" else ""
    m_use = " selected" if mode == "from_use" else ""
    expires_label = expiry_text
    if peer["first_seen"]:
        expires_label += " · " + _("pierwsze połączenie {date}", date=time.strftime(
            "%Y-%m-%d", time.localtime(peer["first_seen"])))
    state_names = {"active": _("aktywny"), "suspended": _("zawieszony")}

    suspend_label = _("Wznów") if peer["state"] == "suspended" else _("Zawieś")
    wipe_ask = ask(_("Skasować klucz prywatny?"),
                   _("Panel nie wygeneruje już dla tej osoby pliku ani kodu QR. Nowy plik będzie "
                     "możliwy tylko przez rotację kluczy."), _("Skasuj klucz"))
    rotate_ask = ask(_("Wymienić klucze: {name}?", name=peer["name"]),
                     _("Panel wygeneruje nową parę kluczy. Po zastosowaniu zmian obecny plik tej "
                       "osoby przestanie działać - trzeba jej przekazać nowy."), _("Wymień klucze"))
    delete_ask = ask(_("Usunąć {name}?", name=peer["name"]),
                     _("Plik tej osoby przestanie działać po zastosowaniu zmian, a adres {ip} "
                       "wróci do puli wolnych.", ip=peer["ip"]), _("Usuń peera"))
    key_block = ""
    if peer["privkey"]:
        key_block = f"""
        <div class="qr">{qr_svg}</div>
        <details><summary>{_("Pokaż plik konfiguracyjny")}</summary>
          <pre id="conf-{peer['id']}">{e(client_conf)}</pre></details>
        <p><button type="button" onclick="wgCopy('conf-{peer['id']}', this)">{_("Kopiuj plik")}</button>
           <a class="button" href="/peers/{peer['id']}/config">{_("Pobierz .conf")}</a></p>
        <p class="muted">{_("Kopiuj - wklejasz tekst np. w wiadomości; osoba w aplikacji WireGuard "
                            "wybiera „Dodaj tunel → Utwórz od zera” i wkleja. Pobierz - plik do "
                            "zaimportowania. Kod QR - osoba skanuje go telefonem.")}</p>
        <form method="post" action="/peers/{peer['id']}/delivery" class="inline">
          {csrf}
          <button>{_("Wygeneruj link jednorazowy")}</button>
        </form>
        <p class="muted">{_("Link otwiera się tylko przez VPN - przydaje się przy wymianie pliku "
                            "komuś, kto już jest połączony (np. po rotacji kluczy). Nowej osobie "
                            "wyślij plik albo pokaż kod QR.")}</p>
        <p class="muted">{_("Klucz prywatny jest jeszcze w bazie. Po przekazaniu konfiguracji użyj "
                            "„Wydano - skasuj klucz”, żeby zniknął z serwera.")}</p>
        <form method="post" action="/peers/{peer['id']}/wipe" class="inline"{wipe_ask}>
          {csrf}
          <button class="danger">{_("Wydano - skasuj klucz")}</button>
        </form>"""
    else:
        key_block = f"""
        <div class="banner">{_("Panel nie ma klucza prywatnego tej osoby, więc nie zrobi jej kodu "
                               "QR ani pliku. Tak jest przy osobach przejętych ze starej "
                               "konfiguracji i po użyciu „Wydano - skasuj klucz”.")}</div>
        <p class="muted">{_("Żeby wydać nowy plik, zrób {rotation}: panel wygeneruje nową parę "
                            "kluczy i klucz wspólny (PSK), pokaże kod QR i plik do pobrania. Stary "
                            "plik tej osoby przestanie działać po zastosowaniu zmian.",
                            rotation="<strong>" + _("rotację kluczy") + "</strong>")}</p>
        <form method="post" action="/peers/{peer['id']}/rotate" class="inline"{rotate_ask}>
          {csrf}
          <button class="primary">{_("Rotacja kluczy i nowy plik")}</button>
        </form>"""

    if delivery_url:
        key_block += f"""
        <div class="banner ok">{_("Link jednorazowy (wygasa za 24 h, działa raz):")}<br>
          <code>{e(delivery_url)}</code></div>"""

    if issued["state"] == "missing":
        issued_box = '<div class="banner err">' + _(
            "{stale} - nie ma w nim: {missing}, więc tam nie dojdzie. Wyślij nowy plik albo niech "
            "w aplikacji WireGuard zmieni linię AllowedIPs na powyższą. Po wysłaniu kliknij "
            "„Oznacz jako wysłany”.",
            stale="<strong>" + _("Plik u tej osoby jest nieaktualny") + "</strong>",
            missing="<code>" + e(", ".join(issued["missing"])) + "</code>") + '</div>'
    elif issued["state"] == "wider":
        issued_box = '<div class="banner">' + _(
            "Plik u tej osoby kieruje do VPN więcej niż trzeba ({issued}). Działa, ale możesz "
            "wysłać nowy.", issued="<code>" + e(issued["issued"]) + "</code>") + '</div>'
    elif issued["state"] == "unknown":
        issued_box = '<p class="muted">' + _(
            "Panel nie wie, jaki plik ma ta osoba (np. przejęta z poprzedniej konfiguracji). "
            "Jeśli jej obecny plik działa - nic nie trzeba robić.") + '</p>'
    else:
        issued_box = '<p class="muted">' + _("Plik u tej osoby jest aktualny.") + '</p>'
    if issued["state"] != "ok":
        issued_box += f"""
        <form method="post" action="/peers/{peer['id']}/issued" class="inline">
          {csrf}
          <button>{_("Oznacz jako wysłany")}</button>
        </form>"""

    more = ('<a class="button" href="/peers/new">' + _("Dodaj kolejnego peera") + '</a>'
            if just_created else "")
    warning_badge = '<span class="badge warn">' + e(warning) + '</span>' if warning else ""
    ip6 = ('<div><span class="muted">' + _("adres IPv6") + '</span><strong>' + e(peer['ip6']) +
           '</strong></div>' if peer['ip6'] and cfg.get('ipv6_enabled') == '1' else "")
    chart_html = (traffic_chart(chart) if chart and any(p["rx"] or p["tx"] for p in chart)
                  else '<p class="muted">' + _("Brak danych. Próbki zbierane są co kilka minut, "
                                                 "wykres pojawi się po pierwszej godzinie ruchu.") + '</p>')
    route_source = (_("ustawione u tej osoby") if route["own"]
                    else _("z tieru {tier}", tier=e(peer["tier_name"] or "")))
    notes = "".join('<div class="banner err">' + e(n) + '</div>' for n in route["notes"])
    mode_options = "".join(
        f'<option value="{k}"{" selected" if route["own"] and route["mode"] == k else ""}>{e(_(v))}</option>'
        for k, v in render.TUNNEL_MODES.items())
    body = f"""
      <nav class="crumbs"><a class="button" href="/">← {_("Lista peerów")}</a>{more}</nav>
      <div class="peer-layout">
       <div class="peer-main">
      <div class="card">
        <div class="head">
          <h1>{e(peer['name'])}
            {warning_badge}</h1>
          <div>
            <form method="post" action="/peers/{peer['id']}/suspend" class="inline">
              {csrf}
              <button>{suspend_label}</button>
            </form>
            <form method="post" action="/peers/{peer['id']}/rotate" class="inline"{rotate_ask}>
              {csrf}
              <button>{_("Rotacja kluczy")}</button>
            </form>
            <form method="post" action="/peers/{peer['id']}/delete" class="inline"{delete_ask}>
              {csrf}
              <button class="danger">{_("Usuń")}</button>
            </form>
          </div>
        </div>
        <div class="stats">
          <div><span class="muted">{_("adres")}</span><strong>{e(peer['ip'])}</strong></div>
          {ip6}
          <div><span class="muted">handshake</span><strong class="{css}">{e(when)}</strong></div>
          <div><span class="muted">endpoint</span><strong>{e(endpoint)}</strong></div>
          <div><span class="muted">{_("stan")}</span><strong>{e(state_names.get(peer['state'], peer['state']))}</strong></div>
          <div><span class="muted">{_("ważny do")}</span><strong>{e(expires_label)}</strong></div>
        </div>
        <form method="post" action="/peers/{peer['id']}/edit" class="row" id="peer-form"
              data-track-changes="peer-save">
          {csrf}
          <label>{_("Nazwa")}<input name="name" value="{e(peer['name'])}" required></label>
          <label>{_("Urządzenie")}<input name="device" value="{e(peer['device'])}"></label>
          <label>{_("Tier")}<select name="tier_id">{tier_options}</select>
            <small class="muted"><a href="/tiers#tier-{peer['tier_id'] or ''}">{_("reguły i ustawienia tego tieru")} →</a></small></label>
          <label>{_("Tryb dostępu")}<select name="access_mode" onchange="wgAccessMode(this)">
            <option value="none"{m_none}>{_("bezterminowo")}</option>
            <option value="window"{m_window}>{_("okno terminowe (od - do)")}</option>
            <option value="from_use"{m_use}>{_("N dni od pierwszego połączenia")}</option>
          </select></label>
          <label class="acc acc-window">{_("Od dnia")}<input type="date" name="starts"
                 value="{e(starts_value)}" title="{e(_("puste = od zaraz"))}"></label>
          <label class="acc acc-window">{_("Do dnia")}<input type="date" name="expires"
                 value="{e(expires_value)}" title="{e(_("puste = bez końca"))}"></label>
          <label class="acc acc-use">{_("Ile dni")}<input name="expires_days"
                 value="{e(days_value)}" inputmode="numeric" placeholder="{e(_("np. 7"))}"></label>
          <label>{_("Notatka")}<input name="note" value="{e(peer['note'])}"></label>
        </form>
      </div>

      <div class="card">
        <div class="head">
          <h2>{_("Reguły własne")} <span class="badge">{len(rules)}</span></h2>
          <button type="button" onclick="wgToggle('reguly-{peer['id']}', this)">{_("Pokaż / ukryj")}</button>
        </div>
        <div id="reguly-{peer['id']}" class="{'hidden' if not rules else ''}">
        <p class="muted">{_("Sprawdzane {before} regułami tieru, w kolejności od góry - dzięki temu "
                            "wyjątek działa mimo blokady z tieru. ACCEPT wpuszcza, DROP blokuje; "
                            "pierwsza pasująca wygrywa.", before="<strong>" + _("przed") + "</strong>")}</p>
        <table class="stack">
          <thead><tr><th>{_("Akcja")}</th><th>{_("Cel")}</th><th>{_("Okno")}</th><th>{_("Komentarz")}</th><th></th></tr></thead>
          <tbody>{''.join(rule_rows)}</tbody>
        </table>
        <form method="post" action="/peers/{peer['id']}/rules" class="row">
          {csrf}
          <label>{_("Akcja")}<select name="action"><option>ACCEPT</option><option>DROP</option></select></label>
          <label>{_("Cel")}<input name="dest" placeholder="192.168.1.60 / LAN / ANY" required></label>
          <label>{_("Proto")}<select name="proto"><option>any</option><option>tcp</option><option>udp</option></select></label>
          <label>{_("Porty")}<input name="ports" placeholder="80,443"></label>
          <label>{_("Od")}<input name="time_from" placeholder="08:00"></label>
          <label>{_("Do")}<input name="time_to" placeholder="18:00"></label>
          <label>{_("Dni")}<input name="weekdays" placeholder="Mon,Tue,Wed,Thu,Fri"></label>
          <label>{_("Komentarz")}<input name="comment"></label>
          <button class="primary">{_("Dodaj regułę")}</button>
        </form>
        </div>
      </div>

      <div class="card">
        <h2>{_("Ruch - ostatnie 7 dni")}</h2>
        {chart_html}
      </div>

      <div class="card">
        <h2>{_("Co idzie przez VPN")}</h2>
        <p class="muted">{_("Decyduje o tym linia {line} w pliku osoby. Reszta ruchu idzie przez jej "
                            "własny internet. Uprawnienia i tak pilnuje serwer - przerobiony plik "
                            "niczego nie otworzy.", line="<code>AllowedIPs</code>")}</p>
        <p><strong>{e(_(render.TUNNEL_MODES[route["mode"]]))}</strong>
           <span class="muted">({route_source})</span></p>
        <pre id="allowed-{peer['id']}">AllowedIPs = {e(", ".join(route["allowed"]))}</pre>
        <p><button type="button" onclick="wgCopy('allowed-{peer['id']}', this)">{_("Kopiuj linię AllowedIPs")}</button></p>
        {notes}
        {issued_box}
        <div class="row">
          <label>{_("Tryb")}<select name="tunnel_mode" form="peer-form" onchange="wgTunnelList(this)">
            <option value=""{'' if route['own'] else ' selected'}>{_("jak w tierze")}</option>
            {mode_options}
          </select></label>
          <label class="tunnel-list">{_("Własna lista")}<input name="tunnel_list" form="peer-form"
                 value="{e(peer['tunnel_list'] or '')}" placeholder="nas, drukarka, 192.168.1.80"></label>
        </div>
      </div>

       </div>
       <aside class="peer-side">
         <div class="card">
           <h2>{_("Konfiguracja klienta")}</h2>
           {key_block}
         </div>
       </aside>
      </div>"""
    body += f"""
      <div class="save-bar" id="peer-save" role="region" aria-label="{e(_("Niezapisane zmiany"))}">
        <div class="save-inner">
          <p><strong>{_("Masz niezapisane zmiany.")}</strong>
             <span class="muted">{_("Zapisz je, zanim wyjdziesz z tej strony.")}</span></p>
          <div class="save-actions">
            <button type="button" data-reset="peer-form">{_("Porzuć zmiany")}</button>
            <button type="submit" form="peer-form" class="primary">{_("Zapisz zmiany")}</button>
          </div>
        </div>
      </div>"""
    return page(peer["name"], body, sess, flash=flash, error=error, alerts=alerts)


def apply_preview(sess, cfg, diff_conf: str, diff_rules: str, rules_text: str,
                  error: str = "", alerts: int = 0, pending: bool = False) -> str:
    if pending:
        action = ('<p class="muted">' + _("Poprzednia zmiana czeka na potwierdzenie - potwierdź ją "
                                          "albo cofnij na pasku u góry, zanim zastosujesz kolejną.") +
                  '</p><a class="button" href="/">' + _("Wróć do listy peerów") + '</a>')
    else:
        action = f"""
        <form method="post" action="/apply" class="inline">
          {_csrf(sess)}
          <button class="primary">{_("Zastosuj zmiany")}</button>
        </form>
        <a class="button" href="/">{_("Anuluj")}</a>"""
    window = _("Po zastosowaniu masz {seconds}&nbsp;s na potwierdzenie z działającego połączenia. "
               "Bez potwierdzenia panel sam cofnie zmiany.", seconds=db.confirm_window(cfg))
    body = f"""
      <div class="card">
        <h1>{_("Podgląd zmian")}</h1>
        <p class="muted">{window}</p>
        <h2>wg0.conf</h2><pre class="diff">{e(diff_conf)}</pre>
        <h2>{_("Reguły")}</h2><pre class="diff">{e(diff_rules)}</pre>
        <details><summary>{_("Pełny zestaw reguł")}</summary><pre>{e(rules_text)}</pre></details>
        {action}
      </div>"""
    return page(_("Podgląd zmian"), body, sess, error=error, alerts=alerts)


def tiers_page(sess, tiers, flash: str = "", error: str = "", alerts: int = 0) -> str:
    csrf = _csrf(sess)
    mode_options = "".join(
        f'<option value="{k}">{e(_(v))}</option>' for k, v in render.TUNNEL_MODES.items())
    labels = {"action": _("Akcja"), "target": _("Cel"), "comment": _("Komentarz"),
              "ports": _("Porty"), "desc": _("Opis"), "tunnel": _("Co idzie przez VPN"),
              "list": _("Własna lista"), "host": _("Porty na samym Pi")}
    cards = []
    for tier in tiers:
        rules = json.loads(tier["rules_json"] or "[]")
        rows = []
        for index, rule in enumerate(rules):
            ports = f':{e(rule.get("ports"))}' if rule.get("ports") else ""
            proto = e(rule.get("proto", "any"))
            target = (f"{rule.get('dest', 'ANY')}"
                      f"{(':' + str(rule.get('ports'))) if rule.get('ports') else ''}")
            rule_ask = ask(
                _("Usunąć regułę z tieru {tier}?", tier=tier["name"]),
                _("{action} do {target}. Dotyczy wszystkich osób w tym tierze. Zmiana zadziała "
                  "po zastosowaniu.", action=rule.get("action"), target=target),
                _("Usuń regułę"))
            badge = "ok" if rule.get("action") == "ACCEPT" else "dead"
            rows.append(f"""
              <tr>
                <td><span class="badge {badge}">
                    {e(rule.get('action'))}</span></td>
                <td><code>{e(rule.get('dest', 'ANY'))}{ports}</code>
                    <span class="muted">{proto}</span></td>
                <td class="muted">{e(i18n.maybe(rule.get('comment', '')))}</td>
                <td><form method="post" action="/tiers/{tier['id']}/rules/{index}/delete"
                          class="inline"{rule_ask}>
                      {csrf}
                      <button class="link danger">{_("usuń")}</button>
                    </form></td>
              </tr>""")
        if not rows:
            rows.append('<tr><td colspan="4" class="muted">' +
                        _("Brak reguł - peerzy tego tieru mają pełny dostęp.") + '</td></tr>')

        selected = "".join(
            f'<option value="{k}"{" selected" if tier["client_mode"] == k else ""}>{e(_(v))}</option>'
            for k, v in render.TUNNEL_MODES.items())
        builtin = '<span class="badge">' + _("wbudowany") + '</span>' if tier["builtin"] else ""
        delete_ask = ask(_("Usunąć tier {tier}?", tier=tier["name"]),
                         _("Tier da się usunąć tylko wtedy, gdy nikt nie jest do niego przypisany."),
                         _("Usuń tier"))
        cards.append(f"""
          <div class="tier" id="tier-{tier['id']}">
            <div class="head">
              <h3>{e(tier['name'])}
                {builtin}</h3>
              <form method="post" action="/tiers/{tier['id']}/delete" class="inline"{delete_ask}>
                {csrf}
                <button class="link danger">{_("usuń tier")}</button>
              </form>
            </div>
            <p class="muted">{e(i18n.maybe(tier['description']))}</p>

            <table class="rules stack">
              <thead><tr><th>{labels['action']}</th><th>{labels['target']}</th><th>{labels['comment']}</th><th></th></tr></thead>
              <tbody>{''.join(rows)}</tbody>
            </table>

            <form method="post" action="/tiers/{tier['id']}/rules" class="row compact">
              {csrf}
              <label>{labels['action']}<select name="action">
                <option value="ACCEPT">ACCEPT</option><option value="DROP">DROP</option>
              </select></label>
              <label>{labels['target']}<input name="dest" placeholder="nas / LAN / 192.168.1.70"></label>
              <label>Proto<select name="proto">
                <option value="any">any</option><option value="tcp">tcp</option>
                <option value="udp">udp</option></select></label>
              <label>{labels['ports']}<input name="ports" placeholder="80,443"></label>
              <label>{labels['comment']}<input name="comment"></label>
              <button>{_("Dodaj regułę")}</button>
            </form>

            <form method="post" action="/tiers/{tier['id']}/edit" class="row compact">
              {csrf}
              <label>{labels['desc']}<input name="description" value="{e(i18n.maybe(tier['description']))}"></label>
              <label>{labels['tunnel']}<select name="client_mode" onchange="wgTunnelList(this)">
                {selected}</select></label>
              <label class="tunnel-list">{labels['list']}<input name="client_list"
                     value="{e(tier['client_list'])}" placeholder="nas, drukarka"></label>
              <label>{labels['host']}<input name="host_access"
                     value="{e(tier['host_access'])}" placeholder="22,3389,8080">
                <small class="muted">{_("22 SSH, 3389 RDP, 8080 panel")}</small></label>
              <button class="primary">{_("Zapisz")}</button>
            </form>
          </div>""")

    help_text = _("Tier to nazwany zestaw reguł. Zmiana tieru przestawia wszystkich przypisanych do "
                  "niego peerów naraz. Reguły sprawdzane są od góry, pierwsza pasująca wygrywa. "
                  "W polu {target} wpisz nazwę z katalogu hostów ({nas}), słowo {lan} (cała sieć "
                  "domowa), {any} (wszystko) albo adres. Każda zmiana działa po zastosowaniu.",
                  target="<strong>" + labels["target"] + "</strong>", nas="<code>nas</code>",
                  lan="<code>LAN</code>", any="<code>ANY</code>")
    body = f"""
      <div class="card">
        <div class="head">
          <h1>{_("Tiery")}</h1>
          <button type="button" class="primary" onclick="wgToggle('nowy-tier', this)">
            {_("Dodaj tier")}</button>
        </div>
        <div id="nowy-tier" class="hidden">
          <form method="post" action="/tiers/new" class="row">
            {csrf}
            <label>{_("Nazwa")}<input name="name" required placeholder="GOSC"></label>
            <label>{labels['desc']}<input name="description"></label>
            <label>{labels['tunnel']}<select name="client_mode" onchange="wgTunnelList(this)">
              {mode_options}</select></label>
            <label class="tunnel-list">{labels['list']}<input name="client_list"
                   placeholder="nas, drukarka"></label>
            <label>{labels['host']}<input name="host_access" placeholder="22,3389"></label>
            <button class="primary">{_("Utwórz")}</button>
          </form>
          <p class="muted">{_("Reguły dodasz przy tierze, gdy już powstanie.")}</p>
        </div>
        <p class="muted">{help_text}</p>
        {''.join(cards)}
      </div>"""
    return page(_("Tiery"), body, sess, flash=flash, error=error, alerts=alerts)


def settings_page(sess, cfg, server_pubkey: str, flash: str = "", error: str = "",
                  alerts: int = 0, has_totp: bool = False, tls: dict | None = None) -> str:
    csrf = _csrf(sess)
    # codzienne pola na wierzchu, reszta schowana pod "Zaawansowane"
    fields = [
        ("lan", _("Podsieć LAN")), ("vpn", _("Podsieć VPN")), ("server_ip", _("Adres serwera w VPN")),
        ("server_lan", _("Adres Pi w LAN")),
        ("wan_if", _("Interfejs WAN")), ("listen_port", _("Port nasłuchu")),
        ("endpoint", _("Endpoint klientów")), ("dns_full", _("DNS trybu full")),
    ]
    advanced = [
        ("wg_iface", _("Interfejs WireGuard")), ("chain", _("Nazwa łańcucha")),
        ("confirm_seconds", _("Czas na potwierdzenie [s, 30-900]")),
        ("keepalive", "PersistentKeepalive"), ("mtu", _("MTU klienta")),
        ("panel_port", _("Port panelu")),
        ("expiry_warn_days", _("Ostrzeżenie [dni]")),
        ("audit_days", _("Dziennik [dni]")),
        ("traffic_anomaly_factor", _("Anomalia [x mediana]")),
        ("traffic_anomaly_min_mb", _("Anomalia [MB]")),
    ]
    hints = {
        "endpoint": _("DDNS albo publiczny IP z portem"),
        "server_lan": _("adres Pi w sieci domowej"),
        "dns_full": _("trafia do plików z całym ruchem"),
        "mtu": _("puste = domyślne"),
        "audit_days": _("potem czyszczone"),
        "traffic_anomaly_factor": _("ile razy więcej niż zwykle"),
    }

    def field(key, label):
        # podpis i podpowiedz maja stala wysokosc, zeby pola w wierszu staly rowno
        hint = e(hints.get(key, ""))
        return (f'<label class="fixed"><span class="lbl">{e(label)}</span>'
                f'<input name="{key}" value="{e(cfg.get(key, ""))}">'
                f'<small class="muted">{hint}</small></label>')

    inputs = "".join(field(key, label) for key, label in fields)
    advanced_inputs = "".join(field(key, label) for key, label in advanced)
    mode = cfg.get("confirm_mode", "auto")
    advanced_inputs += (
        '<label class="fixed"><span class="lbl">' + _("Potwierdzanie zmian") + '</span>'
        '<select name="confirm_mode">'
        f'<option value="auto"{" selected" if mode != "manual" else ""}>{_("automatycznie")}</option>'
        f'<option value="manual"{" selected" if mode == "manual" else ""}>{_("ręcznie")}</option>'
        '</select><small class="muted">' + _("auto: panel sam sprawdza, czy masz połączenie") +
        '</small></label>')
    totp_state = _("włączony") if has_totp else _("wyłączony")
    rollback_ask = ask(_("Cofnąć do ostatniej kopii?"),
                       _("Pliki wg0.conf i reguły wrócą do wersji sprzed ostatniego zastosowania i od "
                         "razu trafią na interfejs. Zmiany w panelu zostają - pojawią się jako "
                         "niezastosowane."), _("Cofnij konfigurację"))
    restart_ask = ask(_("Zrestartować wg-quick?"),
                      _("Wszystkie tunele zerwą się na chwilę. Jeśli łączysz się przez VPN, panel też "
                        "na moment straci połączenie."), _("Zrestartuj"))
    ipv6_help = _("Po włączeniu każdy peer dostaje adres IPv6 odpowiadający IPv4 (10.0.0.4 → "
                  "fd42:42:42::4), a reguły działają w obu rodzinach naraz - blokada LAN obejmuje też "
                  "prefiks IPv6 sieci domowej. Peerom trzeba potem wydać nowe pliki konfiguracyjne.")
    hosts_hint = _("Adres NAS i innych urządzeń ustawiasz w zakładce {hosts} - stąd zniknął, "
                   "żeby nie było dwóch miejsc na to samo.",
                   hosts='<a href="/hosts">' + _("Hosty") + '</a>')
    body = f"""
      <div class="card">
        <h1>{_("Ustawienia")}</h1>
        <p class="muted">{_("Klucz publiczny serwera")}: <code>{e(server_pubkey or _('niedostępny'))}</code></p>
        <p class="muted">{hosts_hint}</p>
        <form method="post" action="/settings" class="row">
          {csrf}
          {inputs}
          <div class="full">
            <button type="button" onclick="wgToggle('zaawansowane', this)">{_("Zaawansowane")}</button>
          </div>
          <div id="zaawansowane" class="hidden full row">{advanced_inputs}</div>
          <button class="primary">{_("Zapisz")}</button>
        </form>
      </div>
      <div class="card">
        <h2>IPv6</h2>
        <p class="muted">{ipv6_help}</p>
        <form method="post" action="/settings/ipv6" class="row">
          {csrf}
          <label class="check" style="flex:1 1 100%">
            <input type="checkbox" name="ipv6_enabled" value="1"
              {'checked' if cfg.get('ipv6_enabled') == '1' else ''}> {_("Włącz IPv6 w tunelu")}</label>
          <label>{_("Podsieć VPN IPv6")}<input name="vpn6" value="{e(cfg.get('vpn6', ''))}"></label>
          <label>{_("Adres serwera")}<input name="server_ip6" value="{e(cfg.get('server_ip6', ''))}"></label>
          <label>{_("Prefiksy LAN IPv6")}<input name="lan6" value="{e(cfg.get('lan6', ''))}"
                 placeholder="fd00:77::/64, 2a02:.../64"></label>
          <label>DNS IPv6<input name="dns6" value="{e(cfg.get('dns6', ''))}"></label>
          <label class="check"><input type="checkbox" name="nat6" value="1"
              {'checked' if cfg.get('nat6', '1') == '1' else ''}> {_("NAT66 dla trybu pełnego")}</label>
          <button class="primary">{_("Zapisz")}</button>
        </form>
        <form method="post" action="/settings/ipv6/detect" class="inline">
          {csrf}
          <button>{_("Wykryj prefiks LAN")}</button>
        </form>
      </div>

      <div class="card">
        <h2>{_("Konto: {user}", user=e(sess['username']))}</h2>
        <p class="muted">{_("Kod z aplikacji przy logowaniu: {state}.", state="<strong>" + totp_state + "</strong>")}</p>
        <h3>{_("Zmiana hasła")}</h3>
        <form method="post" action="/account/password" class="row">
          {csrf}
          <label>{_("Obecne hasło")}<input type="password" name="password" required
                 autocomplete="current-password"></label>
          <label>{_("Nowe hasło")}<input type="password" name="new1" required
                 autocomplete="new-password"></label>
          <label>{_("Powtórz nowe")}<input type="password" name="new2" required
                 autocomplete="new-password"></label>
          <button class="primary">{_("Zmień hasło")}</button>
        </form>

        <h3>{_("Wyłączenie kodu z aplikacji") if has_totp else _("Włączenie kodu z aplikacji")}</h3>
        <p class="muted">{_("Po wyłączeniu logujesz się samym hasłem.") if has_totp else
                          _("Dodatkowy kod z aplikacji na telefonie przy każdym logowaniu.")}</p>
        <form method="post" action="/account/totp" class="row">
          {csrf}
          <input type="hidden" name="enable" value="{'0' if has_totp else '1'}">
          <label>{_("Potwierdź obecnym hasłem")}<input type="password" name="password" required
                 autocomplete="current-password"></label>
          <button>{_("Wyłącz kod") if has_totp else _("Włącz kod")}</button>
        </form>
      </div>

      <div class="card">
        <h2>{_("Integralność")}</h2>
        <p class="muted">{_("Panel co 5 minut porównuje to, co zastosował, z tym, co jest na dysku "
                            "i w jądrze: plik konfiguracji, reguły, listę peerów na interfejsie i to, "
                            "czy nikt nie wstawił reguły przed łańcuchem panelu.")}</p>
        <a class="button" href="/integrity">{_("Stan integralności")}</a>
      </div>

      {tls_card(sess, tls or {}, cfg)}
      <div class="card">
        <h2>{_("Operacje awaryjne")}</h2>
        <form method="post" action="/rollback" class="inline"{rollback_ask}>
          {csrf}
          <button class="danger">{_("Cofnij do ostatniej kopii")}</button>
        </form>
        <form method="post" action="/restart" class="inline"{restart_ask}>
          {csrf}
          <button class="danger">{_("Restart wg-quick")}</button>
        </form>
        <p class="muted">{_("Restart zrywa wszystkie tunele na ułamek sekundy. Jeśli łączysz się "
                            "przez VPN, sesja panelu też się na moment urwie.")}</p>
      </div>"""
    return page(_("Ustawienia"), body, sess, flash=flash, error=error, alerts=alerts)


def audit_page(sess, rows, newest_first: bool = True, days: int = 7, alerts: int = 0) -> str:
    def action(code):
        label = i18n.audit_action(code)
        extra = f' <code class="muted">{e(code)}</code>' if label != code else ""
        return f"{e(label)}{extra}"

    items = "".join(
        f"<tr><td class='muted'>{e(time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(r['ts'])))}</td>"
        f"<td>{e(i18n.audit_actor(r['actor']))}</td><td>{action(r['action'])}</td>"
        f"<td class='muted'>{e(i18n.audit_detail(r['detail']))}</td></tr>"
        for r in rows
    ) or '<tr><td colspan="4" class="muted">' + _("pusto") + '</td></tr>'
    arrow = "▼" if newest_first else "▲"
    flip = "asc" if newest_first else "desc"
    body = f"""
      <div class="card">
        <div class="head">
          <h1>{_("Dziennik")}</h1>
          <span class="muted">{_("{n} wpisów · trzymane {days} dni", n=len(rows), days=days)}</span>
        </div>
        <table class="stack">
          <thead><tr>
            <th><a href="/audit?order={flip}">{_("Kiedy")} {arrow}</a></th>
            <th>{_("Kto")}</th><th>{_("Co")}</th><th>{_("Szczegóły")}</th>
          </tr></thead>
          <tbody>{items}</tbody>
        </table>
      </div>"""
    return page(_("Dziennik"), body, sess, alerts=alerts)


def delivery_page(peer_name: str, conf: str, qr_svg: str) -> str:
    return page(_("Konfiguracja"), f"""
      <div class="card narrow">
        <h1>{e(peer_name)}</h1>
        <p class="muted">{_("Ten link działa tylko raz. Zeskanuj kod albo pobierz plik teraz.")}</p>
        <div class="qr">{qr_svg}</div>
        <p><a class="button primary" download="{e(peer_name)}.conf"
              href="data:application/octet-stream;base64,{_b64(conf)}">{_("Pobierz .conf")}</a></p>
        <details><summary>{_("Pokaż tekstem")}</summary><pre>{e(conf)}</pre></details>
      </div>""")


def _b64(text: str) -> str:
    import base64
    return base64.b64encode(text.encode()).decode()


def simple(title: str, message: str) -> str:
    return page(title, f'<div class="card narrow"><h1>{e(title)}</h1><p>{e(message)}</p></div>')


# --------------------------------------------------------------------------
# symulator regul
# --------------------------------------------------------------------------

def simulator_page(sess, cfg, peers, form: dict, result=None, alerts: int = 0) -> str:
    options = "".join(
        f'<option value="{e(p["ip"])}"'
        f'{" selected" if form.get("src") == p["ip"] else ""}>'
        f'{e(p["name"])} — {e(p["ip"])}</option>' for p in peers
    )
    verdict_html = ""
    if result:
        css = {"ACCEPT": "ok", "DROP": "dead", "BRAK": "warn"}.get(result["verdict"], "")
        verdict = _("BRAK") if result["verdict"] == "BRAK" else result["verdict"]
        stops = _("← tu się zatrzymuje")
        rows = "".join(
            f'<tr class="{"hit" if step["matched"] else ""}">'
            f'<td class="muted">{step["position"]}</td>'
            f'<td><code>{e(step["rule"])}</code></td>'
            f'<td>{stops if step["matched"] else ""}</td></tr>'
            for step in result.get("trace", [])
        )
        position = (", " + _("pozycja {n}", n=result["position"])) if result.get("position") else ""
        verdict_html = f"""
          <div class="card">
            <h2>{_("Werdykt")}</h2>
            <p class="verdict"><span class="badge {css}">{e(verdict)}</span>
               &nbsp; {e(result['reason'])}</p>
            <p class="muted">{_("łańcuch")} <code>{e(result.get('chain', '-'))}</code>{position}</p>
            <table><thead><tr><th>#</th><th>{_("Reguła")}</th><th></th></tr></thead>
              <tbody>{rows}</tbody></table>
          </div>"""

    body = f"""
      <div class="card">
        <h1>{_("Symulator reguł")}</h1>
        <p class="muted">{_("Sprawdza, która reguła zadecyduje o konkretnym połączeniu - bez ruszania "
                            "zapory. Liczy na tej konfiguracji, która jest w bazie, więc zadziała także "
                            "przed zastosowaniem zmian.")}</p>
        <form method="get" action="/simulator" class="row">
          <label>{_("Od kogo")}<select name="src">{options}</select></label>
          <label>{_("Do adresu")}<input name="dst" value="{e(form.get('dst') or form.get('dst_default', ''))}"
                 placeholder="192.168.1.11"></label>
          <label>Port<input name="port" value="{e(form.get('port', '445'))}"></label>
          <label>Proto<select name="proto">
            <option value="tcp"{' selected' if form.get('proto') != 'udp' else ''}>tcp</option>
            <option value="udp"{' selected' if form.get('proto') == 'udp' else ''}>udp</option>
          </select></label>
          <label>{_("O godzinie")}<input name="at" value="{e(form.get('at', ''))}"
                 placeholder="{e(_("teraz albo 2026-03-14 09:30"))}"></label>
          <button class="primary">{_("Sprawdź")}</button>
        </form>
      </div>
      {verdict_html}"""
    return page(_("Symulator"), body, sess, alerts=alerts)


# --------------------------------------------------------------------------
# alerty
# --------------------------------------------------------------------------

def alerts_page(sess, cfg, rows, flash: str = "", error: str = "", alerts: int = 0) -> str:
    csrf = _csrf(sess)
    items = []
    for row in rows:
        css = {"alarm": "dead", "warn": "warn"}.get(row["severity"], "ok")
        stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(row["ts"]))
        sent = _("wysłany") if row["sent"] else _("tylko w panelu")
        delete_ask = ask(_("Usunąć ten alert?"), row["message"], _("Usuń alert"))
        items.append(f"""
          <tr>
            <td class="muted">{e(stamp)}</td>
            <td><span class="badge {css}">{e(row['kind'])}</span></td>
            <td>{e(row['message'])}</td>
            <td class="muted">{e(sent)}</td>
            <td><form method="post" action="/alerts/{row['id']}/delete" class="inline"{delete_ask}>
                  {csrf}
                  <button class="link danger">{_("usuń")}</button>
                </form></td>
          </tr>""")
    if not items:
        items.append('<tr><td colspan="5" class="muted">' + _("Nic się nie działo.") + '</td></tr>')

    channel = cfg.get("alerts_channel", "none")
    weak = (channel == "ntfy" and cfg.get("alerts_url") and not cfg.get("alerts_token")
            and notify.weak_topic(cfg.get("alerts_url", "")))
    weak_box = ('<div class="banner err"><span>' + _(
        "Temat na publicznym ntfy.sh może przeczytać każdy, kto zna jego nazwę - a alerty "
        "zawierają nazwy peerów i ich adresy IP. Użyj długiej, losowej nazwy (np. {example}) "
        "albo tokenu dostępu.", example="<code>wg-" + e(os.urandom(6).hex()) + "</code>") +
        '</span></div>' if weak else "")
    clear_ask = ask(_("Usunąć wszystkie alerty?"),
                    _("Lista alertów w panelu zostanie wyczyszczona. Powiadomienia już wysłane "
                      "zostają tam, gdzie trafiły."), _("Usuń wszystkie"))
    heads = "".join(f"<th>{h}</th>" for h in (_("Kiedy"), _("Rodzaj"), _("Treść"), _("Kanał"), ""))
    body = f"""
      <div class="card">
        <div class="head">
          <h1>{_("Alerty")}</h1>
          <div>
            <form method="post" action="/alerts/seen" class="inline">
              {csrf}
              <button>{_("Oznacz jako przeczytane")}</button>
            </form>
            <form method="post" action="/alerts/clear" class="inline"{clear_ask}>
              {csrf}
              <button class="danger">{_("Usuń wszystkie")}</button>
            </form>
          </div>
        </div>
        <table class="stack"><thead><tr>{heads}</tr></thead>
          <tbody>{''.join(items)}</tbody></table>
      </div>

      <div class="card">
        <h2>{_("Ustawienia powiadomień")}</h2>
        {weak_box}
        <p class="muted">{_("Najważniejszy alert to {alert} - ten sam klucz łączy się z nowego "
                            "adresu. To pierwszy sygnał, że plik konfiguracyjny wyciekł.",
                            alert="<code>new_endpoint</code>")}</p>
        <form method="post" action="/alerts/settings" class="row">
          {csrf}
          <label>{_("Kanał")}<select name="alerts_channel">
            <option value="none"{' selected' if channel == 'none' else ''}>{_("wyłączone")}</option>
            <option value="ntfy"{' selected' if channel == 'ntfy' else ''}>ntfy</option>
            <option value="webhook"{' selected' if channel == 'webhook' else ''}>webhook</option>
          </select></label>
          <label>{_("Adres")}<input name="alerts_url" value="{e(cfg.get('alerts_url', ''))}"
                 placeholder="https://ntfy.sh/moj-tajny-temat"></label>
          <label>{_("Token ntfy")}<input type="password" name="alerts_token"
                 value="{e(cfg.get('alerts_token', ''))}" placeholder="{e(_("opcjonalnie - tk_..."))}"
                 autocomplete="off"></label>
          <label>{_("Cisza peera [h]")}<input name="alert_dead_hours"
                 value="{e(cfg.get('alert_dead_hours', '48'))}" title="{e(_("0 = nie pilnuj"))}"></label>
          <label>{_("Odstęp powtórek [min]")}<input name="alerts_quiet_minutes"
                 value="{e(cfg.get('alerts_quiet_minutes', '30'))}"></label>
          <details class="email-box full">
            <summary>{_("E-mail (opcjonalnie)")}</summary>
            <p class="muted">{_("Dodatkowy kanał obok ntfy albo webhooka. Wysyła przez zwykłą skrzynkę "
                                "(SMTP z logowaniem) - najlepiej osobne konto i hasło do aplikacji, bo "
                                "hasło leży na Pi.")}</p>
            <div class="sub-row">
              <label class="check full"><input type="checkbox" name="alerts_email"
                value="1"{' checked' if cfg.get('alerts_email') == '1' else ''}> {_("Wysyłaj alerty także e-mailem")}</label>
              <label>{_("Serwer SMTP")}<input name="smtp_host" value="{e(cfg.get('smtp_host', ''))}"
                     placeholder="smtp.gmail.com"></label>
              <label>Port<input name="smtp_port" value="{e(cfg.get('smtp_port', '587'))}"></label>
              <label>{_("Szyfrowanie")}<select name="smtp_security">
                <option value="starttls"{' selected' if cfg.get('smtp_security', 'starttls') == 'starttls' else ''}>STARTTLS (587)</option>
                <option value="ssl"{' selected' if cfg.get('smtp_security') == 'ssl' else ''}>SSL (465)</option>
                <option value="none"{' selected' if cfg.get('smtp_security') == 'none' else ''}>{_("brak")}</option>
              </select></label>
              <label>{_("Login")}<input name="smtp_user" value="{e(cfg.get('smtp_user', ''))}"
                     autocomplete="off"></label>
              <label>{_("Hasło")}<input type="password" name="smtp_pass"
                     value="{e(cfg.get('smtp_pass', ''))}" autocomplete="off"></label>
              <label>{_("Nadawca")}<input name="smtp_from" value="{e(cfg.get('smtp_from', ''))}"
                     placeholder="{e(_("puste = login"))}"></label>
              <label>{_("Adresaci")}<input name="smtp_to" value="{e(cfg.get('smtp_to', ''))}"
                     placeholder="ja@example.com"></label>
            </div>
          </details>
          <button class="primary">{_("Zapisz")}</button>
        </form>
        <label class="check"><input type="checkbox" disabled
          {' checked' if cfg.get('alert_new_endpoint') == '1' else ''}>
          {_("Zmiana adresu, z którego łączy się peer")}</label>
        <form method="post" action="/alerts/test" class="inline">
          {csrf}
          <button>{_("Wyślij testowy alert")}</button>
        </form>
      </div>"""
    return page(_("Alerty"), body, sess, flash=flash, error=error, alerts=alerts)


# --------------------------------------------------------------------------
# kopie zapasowe
# --------------------------------------------------------------------------

def _pre_restore(row) -> bool:
    """Kopia zapisana automatycznie tuz przed odtworzeniem. Poznajemy ja po opisie zapisanym
    w bazie ("kopia stanu przed odtworzeniem") - to dane, nie tekst interfejsu."""
    return "przed odtworzeniem" in (row["detail"] or "")


def backups_page(sess, cfg, rows, files, flash: str = "", error: str = "",
                 alerts: int = 0, nas_files: list | None = None) -> str:
    csrf = _csrf(sess)
    no_crypt = e(_("puste = bez szyfrowania"))
    items = []
    for row in rows:
        stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(row["ts"]))
        badge = ('<span class="badge ok">ok</span>' if row["ok"]
                 else '<span class="badge dead">' + _("błąd") + '</span>')
        size = f'{row["bytes"] / 1024:.1f} KiB'
        lock = _("zaszyfrowana") if row["encrypted"] else _("jawna")
        if row["verified_at"]:
            when = time.strftime("%m-%d %H:%M", time.localtime(row["verified_at"]))
            check = (f'<span class="badge ok">{_("zgodna")}</span> <span class="muted">{e(when)}</span>'
                     if row["verify_ok"] else
                     f'<span class="badge dead">{_("NIEZGODNA")}</span> '
                     f'<span class="muted">{e(i18n.audit_detail(row["verify_detail"]))}</span>')
        elif row["ok"]:
            check = '<span class="muted">' + _("niesprawdzona") + '</span>'
        else:
            check = ""
        digest = (f'<code title="{e(row["sha256"])}">{e(row["sha256"][:12])}</code>'
                  if row["sha256"] else "")
        failed = ("" if row["ok"] else " data-failed") + (
            ' data-where="smb"' if (row["path"] or "").startswith("//") else ' data-where="local"')
        pre_restore = ('<span class="badge warn" title="' + e(_(
            "Zapisana automatycznie tuż przed odtworzeniem - pozwala wrócić do poprzedniego stanu, "
            "jeśli odtworzenie okazało się pomyłką. Gdy wszystko się zgadza, możesz ją usunąć.")) +
            '">' + _("sprzed odtworzenia") + '</span> ') if _pre_restore(row) else ""
        items.append(f"""
          <tr{failed}><td class="pick"><input type="checkbox" name="id" value="{row['id']}"
                  form="backup-delete" aria-label="{e(_("zaznacz kopię"))}"></td>
              <td class="muted">{e(stamp)}</td><td>{badge}</td>
              <td>{e(row['target'])}</td>
              <td>{pre_restore}<code>{e(row['path'] or '—')}</code></td>
              <td class="muted">{e(size)} · {e(lock)}</td>
              <td>{digest}</td>
              <td>{check}</td></tr>""")
    if not items:
        items.append('<tr><td colspan="8" class="muted">' + _("Jeszcze żadnej kopii.") + '</td></tr>')

    options = []
    known = set()
    for row in rows:
        if not row["ok"]:
            continue
        known.add(os.path.basename(row["path"]))
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(row["ts"]))
        where = "NAS" if row["target"] == "smb" else _("karta SD")
        state = ("" if not row["verified_at"] else
                 (" · " + (_("zgodna") if row["verify_ok"] else _("NIEZGODNA"))))
        note = " · " + _("stan sprzed odtworzenia") if _pre_restore(row) else ""
        options.append(f'<option value="id:{row["id"]}">{e(when)} · {where}{state}{note}</option>')
    for name in files:
        if name not in known:  # np. plik wgrany recznie po awarii karty
            options.append(f'<option value="file:{e(name)}">{e(name)} · {_("plik na karcie")}</option>')
    if nas_files:
        for name in nas_files:
            if name not in known:
                options.append(f'<option value="smb:{e(name)}">{e(name)} · {_("na NAS")}</option>')
    restore_options = "".join(options) or '<option value="">' + _("brak kopii") + '</option>'
    target = cfg.get("backup_target", "local")
    enabled = cfg.get("backup_enabled") == "1"
    restore_ask = ask(_("Odtworzyć kopię?"),
                      _("Odtworzenie zastąpi wszystkie peery, tiery i reguły w panelu. Obecny stan "
                        "zostanie najpierw zapisany jako osobna kopia."), _("Odtwórz kopię"))
    # dwa przyciski - kazdy robi dokladnie to, co mowi; wyrozniony ten, gdzie ida kopie automatyczne
    local_css = ' class="primary"' if target != "smb" else ""
    smb_css = ' class="primary"' if target == "smb" else ""
    plain_ask = "" if cfg.get("backup_passphrase") else ask(
        _("Wysłać kopię na NAS bez szyfrowania?"),
        _("Kopia zawiera klucze PSK wszystkich peerów. Bez hasła kopii trafi na udział jawnym "
          "tekstem - ustaw hasło kopii w Harmonogramie i celu, żeby ją zaszyfrować."),
        _("Wyślij bez szyfrowania"))
    auto_where = _("NAS") if target == "smb" else _("katalog na dysku")
    auto_line = (_("Kopie automatyczne: codziennie o {time} → {where}.",
                   time=e(cfg.get("backup_time", "03:30")), where=auto_where)
                 if enabled else _("Kopie automatyczne są wyłączone - włączysz je w Harmonogramie i celu."))
    nas_note = (_("znaleziono {n}, są na liście poniżej", n=len(nas_files)) if nas_files is not None
                else _("przydaje się na nowym systemie, który nie ma jeszcze własnej historii"))
    text_local = _("Zaznaczone kopie zostaną usunięte z karty razem z plikami .sha256. Tego nie da "
                   "się cofnąć - zostaw przynajmniej jedną zgodną kopię.")
    text_smb = _("Zaznaczone kopie zostaną usunięte razem z plikami .sha256 - z karty, a kopie na "
                 "NAS także z NAS. Tego nie da się cofnąć - zostaw przynajmniej jedną "
                 "zgodną kopię.")
    delete_ask = ask(_("Usunąć zaznaczone kopie?"), text_local, _("Usuń kopie")) + (
        f' data-text-local="{e(text_local)}" data-text-smb="{e(text_smb)}"'
        f' data-detail="{e(_("Zaznaczone: {n} (z karty: {local}, z NAS: {smb}).", n="{n}", local="{local}", smb="{smb}"))}"')
    heads = "<th></th>" + "".join(f"<th>{h}</th>" for h in (_("Kiedy"), _("Wynik"), _("Cel"), _("Plik"),
                                               _("Rozmiar"), "SHA-256", _("Weryfikacja")))
    body = f"""
      <div class="card">
        <div class="head">
          <h1>{_("Kopie zapasowe")}</h1>
          <div>
            <form method="post" action="/backups/verify" class="inline">
              {csrf}
              <button>{_("Sprawdź kopie")}</button>
            </form>
            <form method="post" action="/backups/run" class="inline">
              {csrf}
              <input type="hidden" name="target" value="local">
              <button{local_css}>{_("Kopia lokalnie")}</button>
            </form>
            <form method="post" action="/backups/run" class="inline"{plain_ask}>
              {csrf}
              <input type="hidden" name="target" value="smb">
              <button{smb_css}>{_("Kopia na NAS")}</button>
            </form>
          </div>
        </div>
        <p><strong>{auto_line}</strong></p>
        <p class="muted">{_("Każda kopia dostaje sumę SHA-256 zapisaną w bazie i w pliku {file} obok "
                            "kopii - da się ją sprawdzić także bez panelu: {command}. Weryfikacja "
                            "pobiera plik z miejsca, w którym leży (także z NAS), i porównuje sumę.",
                            file="<code>.sha256</code>", command="<code>" + _("sha256sum -c plik.sha256") + "</code>")}</p>
        <p class="muted">{_("Kopia to cały stan panelu: ustawienia, tiery, peery, reguły i konta. Z niej "
                            "odtworzysz panel na czystej karcie - {conf} i reguły iptables zostaną "
                            "wygenerowane od nowa.", conf="<code>wg0.conf</code>")}</p>
        <table class="stack"><thead><tr>{heads}</tr></thead>
          <tbody>{''.join(items)}</tbody></table>
        <form method="post" action="/backups/delete" id="backup-delete" class="actions"{delete_ask}>
          {csrf}
          <button type="button" onclick="wgPickFailed()">{_("Zaznacz nieudane")}</button>
          <button class="danger">{_("Usuń zaznaczone")}</button>
        </form>
        <p class="muted">{_("„Trzymaj ostatnie” sprząta tylko katalog na karcie - kopie na NAS usuwasz tutaj.")}</p>
      </div>

      <div class="card">
        <h2>{_("Harmonogram i cel")}</h2>
        <form method="post" action="/backups/settings" class="row">
          {csrf}
          <label class="check" style="flex:1 1 100%">
            <input type="checkbox" name="backup_enabled" value="1"{' checked' if enabled else ''}>
            {_("Rób kopie automatycznie, codziennie")}</label>
          <label>{_("O godzinie")}<input name="backup_time"
                 value="{e(cfg.get('backup_time', '03:30'))}" placeholder="03:30"></label>
          <label>{_("Trzymaj ostatnie")}<input name="backup_keep"
                 value="{e(cfg.get('backup_keep', '14'))}"></label>
          <label>{_("Cel")}<select name="backup_target">
            <option value="local"{' selected' if target == 'local' else ''}>{_("katalog na dysku")}</option>
            <option value="smb"{' selected' if target == 'smb' else ''}>{_("NAS przez SMB")}</option>
          </select></label>
          <label>{_("Katalog roboczy")}<input name="backup_dir"
                 value="{e(cfg.get('backup_dir', ''))}"></label>
          <label>{_("Hasło kopii")}<input type="password" name="backup_passphrase"
                 value="{e(cfg.get('backup_passphrase', ''))}"
                 placeholder="{no_crypt}"></label>
          <label>{_("NAS: adres")}<input name="smb_host" value="{e(cfg.get('smb_host', ''))}"></label>
          <label>{_("Udział")}<input name="smb_share" value="{e(cfg.get('smb_share', ''))}"></label>
          <label>{_("Katalog na udziale")}<input name="smb_path"
                 value="{e(cfg.get('smb_path', ''))}"></label>
          <label>{_("Użytkownik")}<input name="smb_user" value="{e(cfg.get('smb_user', ''))}"></label>
          <label>{_("Hasło")}<input type="password" name="smb_pass"
                 value="{e(cfg.get('smb_pass', ''))}"></label>
          <button class="primary">{_("Zapisz")}</button>
        </form>
        <p class="muted">{_("Kopia zawiera klucze PSK wszystkich peerów. Jeśli ma leżeć na udziale "
                            "sieciowym, ustaw hasło - plik zostanie zaszyfrowany (ChaCha20 + HMAC, "
                            "klucz z scrypt). Bez hasła trafi tam jawnym tekstem.")}</p>
      </div>

      <div class="card">
        <h2>{_("Pobierz albo odtwórz")}</h2>
        <form method="post" action="/backups/export" class="row">
          {csrf}
          <label>{_("Hasło do pobieranego pliku")}<input type="password" name="passphrase"
                 placeholder="{no_crypt}"></label>
          <button>{_("Pobierz kopię")}</button>
        </form>
        <form method="post" action="/backups/restore" class="row"{restore_ask}>
          {csrf}
          <label>{_("Kopia")}<select name="source">{restore_options}</select></label>
          <label>{_("Hasło")}<input type="password" name="passphrase"></label>
          <label class="check"><input type="checkbox" name="with_users" value="1">
            {_("Odtwórz także konta")}</label>
          <button class="danger">{_("Odtwórz")}</button>
        </form>
        <p><a class="button" href="/backups?nas=1">{_("Pokaż też kopie leżące na NAS")}</a>
           <span class="muted">- {nas_note}</span></p>
        <p class="muted">{_("Kopie z NAS panel pobiera sam i przed odtworzeniem sprawdza ich sumę "
                            "kontrolną - niezgodnej nie odtworzy. Tuż przed odtworzeniem zapisuje "
                            "obecny stan jako osobną kopię, więc zawsze da się wrócić.")}</p>
        <p class="muted">{_("Odtworzenie zmienia tylko bazę. Żeby trafiło na interfejs, zastosuj potem "
                            "zmiany z listy peerów.")}</p>
      </div>"""
    return page(_("Kopie zapasowe"), body, sess, flash=flash, error=error, alerts=alerts)



# --------------------------------------------------------------------------
# wykres ruchu
# --------------------------------------------------------------------------

def _human(value: float) -> str:
    step = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if step < 1024:
            return f"{step:.0f} {unit}" if unit == "B" else f"{step:.1f} {unit}"
        step /= 1024
    return f"{step:.1f} PB"


def _weekday(stamp: float) -> str:
    """Skrot dnia tygodnia w jezyku panelu - strftime("%a") dawal zawsze angielski."""
    names = (_("pon"), _("wt"), _("śr"), _("czw"), _("pt"), _("sob"), _("nd"))
    return names[time.localtime(stamp).tm_wday]


def traffic_chart(series: list[dict], width: int = 940, height: int = 190) -> str:
    """Wykres slupkowy godzin: pobieranie przez klienta nad osia, wysylka pod nia."""
    if not series:
        return ""
    pad_l, pad_r, pad_t, pad_b = 54, 8, 10, 22
    inner_w = width - pad_l - pad_r
    inner_h = height - pad_t - pad_b
    mid = pad_t + inner_h * 0.62  # pobieranie zwykle wieksze - dostaje wiecej miejsca
    peak = max(max(p["tx"] for p in series), max(p["rx"] for p in series), 1)
    bar_w = inner_w / len(series)

    bars = []
    for index, point in enumerate(series):
        x = pad_l + index * bar_w
        down = (point["tx"] / peak) * (mid - pad_t)
        up = (point["rx"] / peak) * (pad_t + inner_h - mid)
        stamp = _weekday(point["ts"]) + time.strftime(" %H:00", time.localtime(point["ts"]))
        title = _("{when} - pobrane {down}, wysłane {up}", when=stamp, down=_human(point["tx"]),
                  up=_human(point["rx"]))
        if down > 0.3:
            bars.append(f'<rect x="{x:.1f}" y="{mid - down:.1f}" width="{max(bar_w - 0.6, 0.6):.1f}" '
                        f'height="{down:.1f}" class="bar-down"><title>{e(title)}</title></rect>')
        if up > 0.3:
            bars.append(f'<rect x="{x:.1f}" y="{mid:.1f}" width="{max(bar_w - 0.6, 0.6):.1f}" '
                        f'height="{up:.1f}" class="bar-up"><title>{e(title)}</title></rect>')

    labels = []
    for index, point in enumerate(series):
        if time.localtime(point["ts"]).tm_hour == 0:
            x = pad_l + index * bar_w
            labels.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{pad_t}" '
                          f'y2="{pad_t + inner_h}" class="grid"/>')
            labels.append(f'<text x="{x + 3:.1f}" y="{height - 6}" class="axis">'
                          f'{_weekday(point["ts"])} {time.strftime("%d", time.localtime(point["ts"]))}</text>')

    total_down = sum(p["tx"] for p in series)
    total_up = sum(p["rx"] for p in series)
    return f"""
      <div class="chart">
        <svg viewBox="0 0 {width} {height}" preserveAspectRatio="none" role="img"
             aria-label="{e(_("Ruch godzinowy z ostatnich 7 dni"))}">
          <line x1="{pad_l}" x2="{width - pad_r}" y1="{mid:.1f}" y2="{mid:.1f}" class="axis-line"/>
          {''.join(labels)}
          {''.join(bars)}
          <text x="{pad_l - 6}" y="{pad_t + 10}" class="axis" text-anchor="end">{e(_human(peak))}</text>
          <text x="{pad_l - 6}" y="{mid + 4:.1f}" class="axis" text-anchor="end">0</text>
        </svg>
        <div class="legend">
          <span><i class="sw sw-down"></i>{_("pobrane przez klienta")}: <strong>{e(_human(total_down))}</strong></span>
          <span><i class="sw sw-up"></i>{_("wysłane przez klienta")}: <strong>{e(_human(total_up))}</strong></span>
        </div>
      </div>"""


# --------------------------------------------------------------------------
# katalog hostow
# --------------------------------------------------------------------------

def hosts_page(sess, cfg, rows, flash: str = "", alerts: int = 0) -> str:
    csrf = _csrf(sess)
    v6 = cfg.get("ipv6_enabled") == "1"
    labels = {"v4": _("Adres IPv4"), "v6": _("Adres IPv6"), "desc": _("Opis")}
    optional = e(_("opcjonalnie"))
    cards = []
    for row in rows:
        used = row["used"]
        usage = ('<span class="badge">' + _("{n} reguł", n=len(used)) + '</span>' if used
                 else '<span class="muted">' + _("nieużywany") + '</span>')
        where = ("<p class='muted'>" + _("Używany w: {rules}", rules=e(", ".join(used[:4]))) + "</p>"
                 if used else "")
        delete_ask = ask(_("Usunąć host {name}?", name=row["name"]),
                         _("Panel odmówi, jeśli host jest używany w regułach."), _("Usuń host"))
        v6_hint = optional if v6 else e(_("IPv6 wyłączone"))
        cards.append(f"""
          <div class="tier" id="host-{row['id']}">
            <div class="head">
              <h3><code>{e(row['name'])}</code> {usage}</h3>
              <form method="post" action="/hosts/{row['id']}/delete" class="inline"{delete_ask}>
                {csrf}
                <button class="link danger">{_("usuń")}</button>
              </form>
            </div>
            {where}
            <form method="post" action="/hosts/{row['id']}/edit" class="row compact">
              {csrf}
              <label>{labels['v4']}<input name="address" value="{e(row['address'])}"></label>
              <label>{labels['v6']}<input name="address6" value="{e(row['address6'])}"
                     placeholder="{v6_hint}"></label>
              <label>{labels['desc']}<input name="description" value="{e(row['description'])}"></label>
              <button class="primary">{_("Zapisz")}</button>
            </form>
          </div>""")
    if not cards:
        cards.append('<p class="muted">' + _("Katalog jest pusty.") + '</p>')

    help_text = _("W regułach wpisujesz nazwę zamiast adresu - {nas}, {printer}. Zmiana adresu tutaj "
                  "przestawia wszystkie reguły, które tej nazwy używają. Zastosuj zmiany, żeby "
                  "zadziałało.", nas="<code>nas</code>", printer="<code>drukarka</code>")
    body = f"""
      <div class="card">
        <div class="head">
          <h1>{_("Hosty w sieci domowej")}</h1>
          <button type="button" class="primary" onclick="wgToggle('nowy-host', this)">
            {_("Dodaj host")}</button>
        </div>
        <div id="nowy-host" class="hidden">
          <form method="post" action="/hosts/new" class="row">
            {csrf}
            <label>{_("Nazwa")}<input name="name" placeholder="drukarka" required></label>
            <label>{labels['v4']}<input name="address" placeholder="192.168.1.70"></label>
            <label>{labels['v6']}<input name="address6" placeholder="{optional}"></label>
            <label>{labels['desc']}<input name="description" placeholder="{e(_("Brother w biurze"))}"></label>
            <button class="primary">{_("Dodaj")}</button>
          </form>
        </div>
        <p class="muted">{help_text}</p>
        {''.join(cards)}
      </div>"""
    return page(_("Hosty"), body, sess, flash=flash, alerts=alerts)


# --------------------------------------------------------------------------
# integralnosc
# --------------------------------------------------------------------------

def integrity_page(sess, findings, checked_at: int, has_baseline: bool, alerts: int = 0) -> str:
    when = (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(checked_at))
            if checked_at else _("jeszcze nie sprawdzano"))
    if not has_baseline:
        status = ('<div class="banner err">' + _("Nic jeszcze nie zostało zastosowane z panelu, więc "
                                                 "nie ma wzorca do porównania. Zastosuj zmiany raz.") +
                  '</div>')
    elif findings:
        rows = "".join(
            f"<tr><td><span class='badge {'dead' if f.get('severity') == 'alarm' else 'warn'}'>"
            f"{e(f['kind'])}</span></td><td>{e(i18n.helper(f['message']))}</td></tr>" for f in findings)
        status = f"""
          <div class="card tamper">
            <h2>{_("Wykryto rozbieżności: {n}", n=len(findings))}</h2>
            <table><tbody>{rows}</tbody></table>
            <p class="muted">{_("Przywrócenie zastosuje ponownie konfigurację z bazy - z bezpiecznikiem, "
                                "więc jeśli coś pójdzie nie tak, wróci sama.")}</p>
            <a class="button primary" href="/apply">{_("Przywróć stan z panelu")}</a>
          </div>"""
    else:
        status = '<div class="banner ok">' + _("Stan zgodny z tym, co zastosował panel.") + '</div>'
    checks = [
        (_("plik {conf} i pliki reguł", conf="<code>wg0.conf</code>"),
         _("SHA-256 w porównaniu z tym, co zapisał helper")),
        (_("prawa dostępu do plików"), _("czy klucze nie stały się czytelne dla innych kont")),
        (_("peery na interfejsie"), _("czy nikt nie dodał klucza przez {cmd}", cmd="<code>wg set</code>")),
        (_("liczba reguł w łańcuchach"), _("czy nikt nie dopisał reguł do łańcuchów panelu")),
        (_("pierwsza reguła FORWARD"), _("czy nikt nie wstawił ACCEPT przed łańcuchem panelu")),
    ]
    check_rows = "".join(f'<tr><td>{a}</td><td class="muted">{b}</td></tr>' for a, b in checks)
    body = f"""
      <div class="card">
        <div class="head">
          <h1>{_("Integralność konfiguracji")}</h1>
          <a class="button" href="/integrity?now=1">{_("Sprawdź teraz")}</a>
        </div>
        <p class="muted">{_("Ostatnie sprawdzenie: {when}", when=e(when))}</p>
        {status}
        <h2>{_("Co jest sprawdzane")}</h2>
        <table><tbody>{check_rows}</tbody></table>
        <p class="muted">{_("Gdy panel ma niezastosowane zmiany, różnice są oczekiwane - panel je "
                            "pokazuje, ale nie zasypuje Cię alarmami. Alarmy wracają po zastosowaniu "
                            "zmian.")}</p>
        <p class="muted">{_("Wykrywanie działa przeciw zmianom robionym ręcznie albo przez kogoś, kto "
                            "nie wie o panelu. Przeciw komuś, kto ma roota i wie, co robi, nie ochroni "
                            "żadne oprogramowanie na tej samej maszynie - on może wyłączyć także sam "
                            "panel.")}</p>
      </div>"""
    return page(_("Integralność"), body, sess, alerts=alerts)


def disk_page(sess, rows, diff_parts, flash: str = "", alerts: int = 0) -> str:
    """Co jest w panelu, a co w konfiguracji dzialajacej na Pi."""
    # "zgodne" / "tylko na dysku" / "tylko w panelu" to klucze z app.py - tlumaczymy przy wyswietlaniu
    names = {"zgodne": _("zgodne"), "tylko na dysku": _("tylko na dysku"),
             "tylko w panelu": _("tylko w panelu")}
    order = {"tylko na dysku": 0, "tylko w panelu": 1}
    rows = sorted(rows, key=lambda r: (order.get(r["where"], 2), r["ip"]))
    items = []
    for row in rows:
        label = e(names.get(row["where"], row["where"]))
        if row["where"] == "zgodne":
            mark = f'<span class="badge ok">{label}</span>'
            action = '<span class="muted">' + _("nic do zrobienia") + '</span>'
        elif row["where"] == "tylko na dysku":
            mark = f'<span class="badge warn">{label}</span>'
            action = (f'<label class="check"><input type="checkbox" name="pubkey" '
                      f'value="{e(row["pubkey"])}" checked> {_("dodaj do panelu")}</label>')
        else:
            mark = f'<span class="badge dead">{label}</span>'
            action = '<span class="muted">' + _("zniknie z dysku po zastosowaniu") + '</span>'
        items.append(f"""
          <tr>
            <td>{mark}</td>
            <td><strong>{e(row['name'])}</strong></td>
            <td><code>{e(row['ip'] or '—')}</code></td>
            <td><code class="muted">{e(row['pubkey'][:16])}…</code></td>
            <td>{action}</td>
          </tr>""")
    if not items:
        items.append('<tr><td colspan="5" class="muted">' + _("Brak peerów po obu stronach.") +
                     '</td></tr>')

    only_disk = sum(1 for r in rows if r["where"] == "tylko na dysku")
    parts = ", ".join(diff_parts or [])
    files = ("<p>" + _("Różniące się pliki: {files}.", files="<strong>" + e(parts) + "</strong>") +
             "</p>" if parts else
             '<p class="banner ok">' + _("Pliki na dysku zgadzają się z panelem.") + '</p>')
    pull = ('<button class="primary">' + _("Dodaj zaznaczonych do panelu") + '</button>'
            if only_disk else "")
    heads = "".join(f"<th>{h}</th>" for h in (_("Stan"), _("Nazwa"), _("Adres"), _("Klucz"),
                                               _("Co zrobić")))
    body = f"""
      <nav class="crumbs"><a class="button" href="/">← {_("Lista peerów")}</a></nav>
      <div class="card">
        <h1>{_("Porównanie z konfiguracją na dysku")}</h1>
        <p class="muted">{_("Po lewej to, co panel by zapisał, po prawej to, co działa teraz na Pi. "
                            "Panel jest źródłem prawdy: przy zastosowaniu nadpisze plik swoją wersją.")}</p>
        {files}
        <form method="post" action="/disk/pull">
          {_csrf(sess)}
          <table class="stack">
            <thead><tr>{heads}</tr></thead>
            <tbody>{''.join(items)}</tbody>
          </table>
          {pull}
        </form>
        <p class="muted">{_("„Tylko na dysku” to osoby dopisane z pominięciem panelu - możesz je "
                            "przejąć, żeby nimi zarządzać, albo zostawić, a znikną przy najbliższym "
                            "zastosowaniu. „Tylko w panelu” trafi na dysk przy zastosowaniu.")}</p>
        <a class="button primary" href="/apply">{_("Podgląd i zastosowanie")}</a>
      </div>"""
    return page(_("Porównanie z dyskiem"), body, sess, flash=flash, alerts=alerts)
