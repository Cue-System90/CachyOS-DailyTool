"""Wiederverwendbare Bausteine: Navigation, Seitenkopf, Karten, Kennzahlen, Protokoll-/Aufgabenansicht."""
from __future__ import annotations

import subprocess
import threading
from typing import Any, Callable

from .. import system
from ..i18n import tr
from .util import Adw, GLib, Gtk, Pango, button, icon_button, idle, label, pick_icon, run_async


class NavItem(Gtk.Button):
    def __init__(self, title: str, icon: str) -> None:
        super().__init__()
        self.add_css_class("nav-item")
        self.add_css_class("flat")
        box = Gtk.Box(spacing=10)
        box.append(Gtk.Image(icon_name=icon, pixel_size=16))
        self.lbl = label(title)
        self.lbl.set_ellipsize(Pango.EllipsizeMode.END)
        self.lbl.set_hexpand(True)
        box.append(self.lbl)
        self.badge = label("", css=["nav-badge"])
        self.badge.set_visible(False)
        box.append(self.badge)
        self.set_child(box)

    def set_badge(self, text: str, style: str | None = None) -> None:
        self.badge.set_text(text)
        self.badge.set_visible(bool(text))
        for c in ("accent", "ok"):
            self.badge.remove_css_class(c)
        if style:
            self.badge.add_css_class(style)

    def set_selected(self, sel: bool) -> None:
        if sel:
            self.add_css_class("selected")
        else:
            self.remove_css_class("selected")


class PageHeader(Gtk.Box):
    def __init__(self, title: str, subtitle: str = "") -> None:
        super().__init__(spacing=12, margin_top=22, margin_bottom=6, margin_start=28, margin_end=28)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        self.title = label(title, css=["page-title"])
        col.append(self.title)
        self.subtitle = label(subtitle, css=["page-subtitle"], wrap=True)
        self.subtitle.set_visible(bool(subtitle))
        col.append(self.subtitle)
        self.append(col)
        self.actions = Gtk.Box(spacing=8, valign=Gtk.Align.CENTER)
        self.append(self.actions)

    def set_subtitle(self, text: str) -> None:
        self.subtitle.set_text(text)
        self.subtitle.set_visible(bool(text))


class Card(Gtk.Box):
    def __init__(self, title: str | None = None, spacing: int = 10, css: list[str] | None = None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=spacing)
        self.add_css_class("card")
        for c in css or []:
            self.add_css_class(c)
        if title:
            self.append(label(title.upper(), css=["card-title"]))


class StatTile(Card):
    def __init__(self, key: str, value: str = "–", sub: str = "") -> None:
        super().__init__(spacing=4)
        self.set_hexpand(True)
        self.append(label(key.upper(), css=["stat-key"]))
        self.val = label(value, css=["stat-val"], ellipsize=True)
        self.append(self.val)
        self.sub = label(sub, css=["stat-sub"], ellipsize=True)
        self.sub.set_visible(bool(sub))
        self.append(self.sub)

    def set(self, value: str, sub: str | None = None, style: str | None = None) -> None:
        self.val.set_text(value)
        for c in ("ok", "warn", "danger"):
            self.val.remove_css_class(c)
        if style:
            self.val.add_css_class(style)
        if sub is not None:
            self.sub.set_text(sub)
            self.sub.set_visible(bool(sub))


class Tag(Gtk.Label):
    def __init__(self, text: str = "", style: str | None = None) -> None:
        super().__init__(label=text)
        self.add_css_class("tag")
        self.set_valign(Gtk.Align.CENTER)
        self.set_style(style)

    def set_style(self, style: str | None) -> None:
        for c in ("ok", "warn", "danger", "accent"):
            self.remove_css_class(c)
        if style:
            self.add_css_class(style)


def button_flow(buttons: list[Gtk.Widget], margin: int = 10) -> Gtk.FlowBox:
    """Knopfleiste, die bei wenig Platz umbricht (statt das Fenster zu verbreitern oder Beschriftungen zu quetschen)."""
    flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=False, column_spacing=6, row_spacing=6,
                       min_children_per_line=1, max_children_per_line=12, valign=Gtk.Align.CENTER)
    flow.set_margin_top(margin)
    flow.set_margin_bottom(margin)
    flow.set_margin_start(12)
    flow.set_margin_end(12)
    for b in buttons:
        flow.append(b)
    for child in list(flow):
        child.set_focusable(False)
    return flow


def group(title: str, description: str | None = None) -> Adw.PreferencesGroup:
    # Titel ist Pango-Markup: „&“ muss maskiert werden
    g = Adw.PreferencesGroup(title=GLib.markup_escape_text(title))
    if description:
        g.set_description(description)
    return g


def action_row(title: str, subtitle: str = "", icon: str | None = None) -> Adw.ActionRow:
    row = Adw.ActionRow(title=title, subtitle=subtitle)
    row.set_title_lines(1)
    row.set_subtitle_lines(2)
    if icon:
        row.add_prefix(Gtk.Image(icon_name=icon, pixel_size=16))
    return row


class LogView(Gtk.ScrolledWindow):
    def __init__(self, min_height: int = 180) -> None:
        super().__init__(hscrollbar_policy=Gtk.PolicyType.AUTOMATIC, vscrollbar_policy=Gtk.PolicyType.AUTOMATIC)
        self.add_css_class("logview")
        self.set_min_content_height(min_height)
        self.set_vexpand(True)
        self.view = Gtk.TextView(editable=False, cursor_visible=False, monospace=True, left_margin=10, right_margin=10,
                                 top_margin=8, bottom_margin=8)
        self.view.set_wrap_mode(Gtk.WrapMode.NONE)
        self.buf = self.view.get_buffer()
        self.set_child(self.view)
        self._autoscroll = True

    def clear(self) -> None:
        self.buf.set_text("")

    def set_text(self, text: str) -> None:
        self.buf.set_text(text)
        self._scroll_end()

    def get_text(self) -> str:
        return self.buf.get_text(self.buf.get_start_iter(), self.buf.get_end_iter(), False)

    def append(self, line: str) -> None:
        end = self.buf.get_end_iter()
        self.buf.insert(end, line + "\n")
        self._scroll_end()

    def _scroll_end(self) -> None:
        if self._autoscroll:
            mark = self.buf.create_mark(None, self.buf.get_end_iter(), False)
            self.view.scroll_mark_onscreen(mark)
            self.buf.delete_mark(mark)


class TaskRunner(Gtk.Box):
    """Statuszeile + Protokoll. Führt Befehle oder Python-Funktionen im Hintergrund aus und streamt die Ausgabe."""

    def __init__(self, min_height: int = 180, idle_text: str | None = None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        idle_text = idle_text or tr("Bereit.")
        self.idle_text = idle_text
        head = Gtk.Box(spacing=10)
        self.spinner = Adw.Spinner()
        self.spinner.set_visible(False)
        head.append(self.spinner)
        self.status = label(idle_text, css=["task-status"], ellipsize=True)
        self.status.set_hexpand(True)
        head.append(self.status)
        self.cancel_btn = icon_button(pick_icon("process-stop-symbolic", "window-close-symbolic"), tr("Abbrechen"), ["flat"])
        self.cancel_btn.set_visible(False)
        self.cancel_btn.connect("clicked", lambda *_: self.cancel())
        head.append(self.cancel_btn)
        copy_btn = icon_button(pick_icon("edit-copy-symbolic"), tr("Protokoll kopieren"), ["flat"])
        copy_btn.connect("clicked", lambda *_: self.log.get_clipboard().set(self.log.get_text()))
        head.append(copy_btn)
        clear_btn = icon_button(pick_icon("edit-clear-all-symbolic", "edit-clear-symbolic"), tr("Protokoll leeren"), ["flat"])
        clear_btn.connect("clicked", lambda *_: self.log.clear())
        head.append(clear_btn)
        self.append(head)
        self.log = LogView(min_height)
        self.append(self.log)
        self.busy = False
        self._proc: subprocess.Popen | None = None
        self._cancelled = False
        self.on_busy_changed: Callable[[bool], None] | None = None

    # -- Status -------------------------------------------------------------
    def set_status(self, text: str, style: str | None = None) -> None:
        self.status.set_text(text)
        for c in ("ok", "danger"):
            self.status.remove_css_class(c)
        if style:
            self.status.add_css_class(style)

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.spinner.set_visible(busy)
        self.cancel_btn.set_visible(busy)
        if self.on_busy_changed:
            self.on_busy_changed(busy)

    def cancel(self) -> None:
        self._cancelled = True
        p = self._proc
        if p and p.poll() is None:
            try:
                p.terminate()
            except OSError:
                pass

    def log_line(self, line: str) -> None:
        self.log.append(line)

    # -- Ausführung ---------------------------------------------------------
    def run(self, argv: list[str], title: str, env: dict[str, str] | None = None, cwd: str | None = None,
            on_done: Callable[[int], None] | None = None, clear: bool = True) -> None:
        self.run_steps([argv], title, env=env, cwd=cwd, on_done=on_done, clear=clear)

    def run_steps(self, steps: list, title: str, env: dict[str, str] | None = None, cwd: str | None = None,
                  on_done: Callable[[int], None] | None = None, clear: bool = True) -> None:
        """steps: argv-Listen oder (argv, env[, prüfung])-Tupel; env ersetzt die Umgebung des Schritts, eine Prüfung,
        die True liefert, lässt einen Rückgabecode ≠ 0 zu (siehe system.run_step_sequence)."""
        if self.busy:
            return
        if clear:
            self.log.clear()
        self._cancelled = False
        self._set_busy(True)
        self.set_status(title)

        def work() -> int:
            rc = system.run_step_sequence(steps, lambda l: idle(self.log.append, l), env=env, cwd=cwd,
                                          on_start=self._on_start, cancelled=lambda: self._cancelled)
            self._proc = None
            return rc

        def done(rc: int) -> None:
            self._set_busy(False)
            if self._cancelled:
                self.set_status(tr("Abgebrochen."), "danger")
            elif rc == 0:
                self.set_status(tr("Fertig."), "ok")
            else:
                self.set_status(tr("Fehlgeschlagen (Code {0}).").format(rc), "danger")
            if on_done:
                on_done(rc)

        def error(exc: Exception) -> None:
            self._set_busy(False)
            self.log.append(tr("Fehler: {0}").format(exc))
            self.set_status(tr("Fehler: {0}").format(exc), "danger")
            if on_done:
                on_done(1)

        run_async(work, done, error)

    def _on_start(self, proc: subprocess.Popen) -> None:
        self._proc = proc

    def run_callable(self, fn: Callable[[Callable[[str], None]], Any], title: str,
                     on_done: Callable[[Any], None] | None = None, on_error: Callable[[Exception], None] | None = None,
                     clear: bool = True) -> None:
        """fn(log) läuft im Hintergrund; log(zeile) ist threadsicher."""
        if self.busy:
            return
        if clear:
            self.log.clear()
        self._cancelled = False
        self._set_busy(True)
        self.set_status(title)

        def work() -> Any:
            return fn(lambda l: idle(self.log.append, l))

        def done(res: Any) -> None:
            self._set_busy(False)
            self.set_status(tr("Fertig."), "ok")
            if on_done:
                on_done(res)

        def error(exc: Exception) -> None:
            self._set_busy(False)
            self.log.append(tr("Fehler: {0}").format(exc))
            self.set_status(tr("Fehler: {0}").format(exc), "danger")
            if on_error:
                on_error(exc)

        run_async(work, done, error)


class PathRow(Adw.ActionRow):
    """Zeile mit Pfad, Ordner-Auswahl und Öffnen-Knopf."""

    def __init__(self, title: str, path: str, on_change: Callable[[str], None] | None = None,
                 parent_getter: Callable[[], Gtk.Window | None] | None = None, choose_title: str | None = None) -> None:
        super().__init__(title=title, subtitle=path)
        choose_title = choose_title or tr("Ordner wählen")
        self.set_subtitle_lines(1)
        self.set_subtitle_selectable(True)
        self.path = path
        self.on_change = on_change
        self._parent_getter = parent_getter or (lambda: None)
        self.choose_title = choose_title
        open_btn = icon_button(pick_icon("folder-open-symbolic", "document-open-symbolic"), tr("Im Dateimanager öffnen"), ["flat"])
        open_btn.set_valign(Gtk.Align.CENTER)
        open_btn.connect("clicked", lambda *_: system.open_path(self.path))
        self.add_suffix(open_btn)
        if on_change:
            pick = icon_button(pick_icon("folder-symbolic"), choose_title, ["flat"])
            pick.set_valign(Gtk.Align.CENTER)
            pick.connect("clicked", lambda *_: self._choose())
            self.add_suffix(pick)

    def _choose(self) -> None:
        from .util import choose_folder

        def picked(p: str) -> None:
            self.set_path(p)
            if self.on_change:
                self.on_change(p)
        choose_folder(self._parent_getter(), self.choose_title, self.path, picked)

    def set_path(self, p: str) -> None:
        self.path = p
        self.set_subtitle(p)


class Page(Gtk.Box):
    """Basisklasse aller Seiten: Kopfzeile + scrollbarer Inhalt."""

    key = ""
    title = ""
    subtitle = ""
    icon = "applications-system-symbolic"

    def __init__(self, win) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.win = win
        self.cfg = win.cfg
        self.header = PageHeader(self.title, self.subtitle)
        self.append(self.header)
        self.scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, margin_top=12, margin_bottom=28,
                               margin_start=28, margin_end=28)
        self.scroll.set_child(self.content)
        self.append(self.scroll)
        self._built = False

    def ensure_built(self) -> None:
        if not self._built:
            self._built = True
            self.build()

    def build(self) -> None:  # pragma: no cover - überschrieben
        pass

    def on_show(self) -> None:
        pass

    def refresh(self) -> None:
        self.on_show()

    def toast(self, text: str, timeout: int = 4) -> None:
        self.win.toast(text, timeout)

    def add_refresh_button(self, tooltip: str | None = None) -> Gtk.Button:
        b = icon_button(pick_icon("view-refresh-symbolic"), tooltip or tr("Aktualisieren"), ["flat", "hdr-btn"])
        b.connect("clicked", lambda *_: self.refresh())
        self.header.actions.append(b)
        return b
