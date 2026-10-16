"""UI-Hilfen: GI-Importe, Threads → Hauptschleife, Labels, Icons, Dialoge."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

log = logging.getLogger(__name__)
_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="ct-ui")
# Langsame Aufgaben (du, Ordner-Scans) bekommen einen eigenen Pool, damit schnelle Abfragen nicht warten müssen
_heavy_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ct-heavy")


def idle(fn: Callable, *args: Any, **kw: Any) -> None:
    """Ruft fn in der GTK-Hauptschleife auf."""
    def _cb() -> bool:
        try:
            fn(*args, **kw)
        except Exception:  # pragma: no cover
            log.exception("UI-Callback fehlgeschlagen")
        return GLib.SOURCE_REMOVE
    GLib.idle_add(_cb)


def run_async(fn: Callable[[], Any], on_done: Callable[[Any], None] | None = None,
              on_error: Callable[[Exception], None] | None = None, heavy: bool = False) -> None:
    """Führt fn in einem Hintergrund-Thread aus und liefert das Ergebnis in die Hauptschleife.

    heavy=True für langsame Aufgaben (Größenberechnung, Ordner-Scans)."""
    def _work() -> None:
        try:
            res = fn()
        except Exception as exc:
            log.debug("Hintergrundaufgabe fehlgeschlagen", exc_info=True)
            if on_error:
                idle(on_error, exc)
            else:
                log.warning("Hintergrundaufgabe fehlgeschlagen: %s", exc)
            return
        if on_done:
            idle(on_done, res)
    (_heavy_pool if heavy else _pool).submit(_work)


_icon_cache: dict[tuple, str] = {}


def pick_icon(*names: str, default: str = "image-missing-symbolic") -> str:
    """Ersten im Icon-Theme vorhandenen Namen wählen (Adwaita-/Breeze-Namen unterscheiden sich)."""
    key = tuple(names)
    if key in _icon_cache:
        return _icon_cache[key]
    try:
        theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
        for n in names:
            if theme.has_icon(n):
                _icon_cache[key] = n
                return n
    except Exception:
        pass
    _icon_cache[key] = names[0] if names else default
    return _icon_cache[key]


def label(text: str = "", css: list[str] | None = None, xalign: float = 0.0, wrap: bool = False,
          ellipsize: bool = False, markup: bool = False, selectable: bool = False) -> Gtk.Label:
    lbl = Gtk.Label(xalign=xalign)
    if markup:
        lbl.set_markup(text)
    else:
        lbl.set_text(text)
    for c in css or []:
        lbl.add_css_class(c)
    if wrap:
        lbl.set_wrap(True)
        lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    if ellipsize:
        lbl.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
    lbl.set_selectable(selectable)
    return lbl


def button(text: str, css: list[str] | None = None, icon: str | None = None,
           on_click: Callable[[], None] | None = None, tooltip: str | None = None) -> Gtk.Button:
    b = Gtk.Button()
    if icon:
        content = Adw.ButtonContent(label=text, icon_name=icon)
        b.set_child(content)
    else:
        b.set_label(text)
    for c in css or []:
        b.add_css_class(c)
    if tooltip:
        b.set_tooltip_text(tooltip)
    if on_click:
        b.connect("clicked", lambda *_: on_click())
    return b


def icon_button(icon: str, tooltip: str, css: list[str] | None = None,
                on_click: Callable[[], None] | None = None) -> Gtk.Button:
    b = Gtk.Button(icon_name=icon, tooltip_text=tooltip)
    for c in css or []:
        b.add_css_class(c)
    if on_click:
        b.connect("clicked", lambda *_: on_click())
    return b


def choose_folder(parent: Gtk.Window | None, title: str, initial: str, callback: Callable[[str], None]) -> None:
    dlg = Gtk.FileDialog(title=title, modal=True)
    try:
        if initial:
            dlg.set_initial_folder(Gio.File.new_for_path(initial))
    except Exception:
        pass

    def done(d: Gtk.FileDialog, res) -> None:
        try:
            f = d.select_folder_finish(res)
        except GLib.Error:
            return
        if f and f.get_path():
            callback(f.get_path())
    dlg.select_folder(parent, None, done)


def choose_file(parent: Gtk.Window | None, title: str, initial: str, callback: Callable[[str], None],
                patterns: list[tuple[str, list[str]]] | None = None) -> None:
    dlg = Gtk.FileDialog(title=title, modal=True)
    try:
        if initial:
            dlg.set_initial_folder(Gio.File.new_for_path(initial))
    except Exception:
        pass
    if patterns:
        store = Gio.ListStore.new(Gtk.FileFilter)
        for name, globs in patterns:
            ff = Gtk.FileFilter()
            ff.set_name(name)
            for g in globs:
                ff.add_pattern(g)
            store.append(ff)
        dlg.set_filters(store)
        dlg.set_default_filter(store.get_item(0))

    def done(d: Gtk.FileDialog, res) -> None:
        try:
            f = d.open_finish(res)
        except GLib.Error:
            return
        if f and f.get_path():
            callback(f.get_path())
    dlg.open(parent, None, done)


def confirm(parent: Gtk.Widget, heading: str, body: str, ok_label: str, on_ok: Callable[[], None],
            destructive: bool = False, extra: Gtk.Widget | None = None) -> None:
    dlg = Adw.AlertDialog(heading=heading, body=body)
    dlg.add_response("cancel", "Abbrechen")
    dlg.add_response("ok", ok_label)
    dlg.set_response_appearance("ok", Adw.ResponseAppearance.DESTRUCTIVE if destructive else Adw.ResponseAppearance.SUGGESTED)
    dlg.set_default_response("cancel" if destructive else "ok")
    dlg.set_close_response("cancel")
    if extra is not None:
        dlg.set_extra_child(extra)

    def _resp(_d, response: str) -> None:
        if response == "ok":
            on_ok()
    dlg.connect("response", _resp)
    dlg.present(parent)


def ask_text(parent: Gtk.Widget, heading: str, body: str, default: str, ok_label: str,
             on_ok: Callable[[str], None], placeholder: str = "") -> None:
    entry = Gtk.Entry(text=default, placeholder_text=placeholder, activates_default=True)
    dlg = Adw.AlertDialog(heading=heading, body=body, extra_child=entry)
    dlg.add_response("cancel", "Abbrechen")
    dlg.add_response("ok", ok_label)
    dlg.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
    dlg.set_default_response("ok")
    dlg.set_close_response("cancel")

    def _resp(_d, response: str) -> None:
        if response == "ok" and entry.get_text().strip():
            on_ok(entry.get_text().strip())
    dlg.connect("response", _resp)
    dlg.present(parent)
    entry.grab_focus()


def copy_text(widget: Gtk.Widget, text: str) -> None:
    widget.get_clipboard().set(text)


def fmt_time(ts: float) -> str:
    import time as _t
    if not ts:
        return "–"
    return _t.strftime("%d.%m.%Y %H:%M", _t.localtime(ts))
