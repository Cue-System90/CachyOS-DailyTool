"""Hauptfenster: Kopfzeile, Seitenleiste mit Abschnitten und Zählern, Seiten-Stack."""
from __future__ import annotations

from pathlib import Path

from .. import APP_ID, APP_NAME, __version__, i18n, shortcuts, system, x3d
from ..i18n import tr
from ..launcher import Launcher
from ..registry import Registry
from .dashboard import DashboardPage
from .pages_drives import DrivesPage, FoldersPage
from .pages_launcher import CompatPage, StoragePage, UsagePage
from .pages_system import CachePage, GpuPage, LogsPage, ScalingPage, UpdatePage
from .pages_launchers import LaunchersPage
from .pages_wine import LaunchPage, PrefixesPage
from .pages_x3d import X3dPage
from .util import Adw, Gio, Gtk, icon_button, label, pick_icon, run_async
from .widgets import NavItem, Page

SIDEBAR_WIDTH = 272
SECTIONS: list[tuple[str, list[type[Page]]]] = [
    (tr("ÜBERSICHT"), [DashboardPage]),
    (tr("WINE & PREFIXE"), [LaunchersPage, LaunchPage, PrefixesPage]),
    (tr("LAUNCHER & SPEICHERORTE"), [StoragePage, CompatPage, UsagePage]),
    (tr("LAUFWERKE"), [DrivesPage, FoldersPage]),
    (tr("SYSTEM"), [UpdatePage, GpuPage, X3dPage, LogsPage, CachePage, ScalingPage]),
]


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app, cfg) -> None:
        super().__init__(application=app, title=APP_NAME)
        self.app = app
        self.cfg = cfg
        self.registry = Registry()
        self.launcher = Launcher(cfg, self.registry)
        w, h = cfg.window_size
        self.set_default_size(w, h)
        self.set_size_request(900, 600)
        self.add_css_class("cachy")
        self.pages: dict[str, Page] = {}
        self.page_classes: dict[str, type[Page]] = {}
        self.nav: dict[str, NavItem] = {}
        self.current: str | None = None

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.append(self._build_header())
        body = Gtk.Box(vexpand=True)
        body.append(self._build_sidebar())
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=120,
                               hexpand=True, vexpand=True)
        body.append(self.stack)
        root.append(body)
        self.toasts = Adw.ToastOverlay()
        self.toasts.set_child(root)
        self.set_content(self.toasts)
        self.connect("close-request", self._on_close)

        last = cfg.get("LAST_PAGE", "dashboard")
        self.navigate(last if last in self.page_classes else "dashboard")
        self.refresh_badges()
        # Verknüpfungen mit veraltetem Startbefehl (z. B. nach der Umbenennung von cachy-toolkit) im Hintergrund erneuern
        run_async(lambda: shortcuts.sync_all(list(self.registry.apps.values())))

    # ------------------------------------------------------------------ Aufbau
    def _build_header(self) -> Gtk.Widget:
        handle = Gtk.WindowHandle()
        bar = Gtk.Box()
        bar.add_css_class("titlebar-ct")
        left = Gtk.Box(spacing=10)
        left.add_css_class("sidebar-head")
        left.set_size_request(SIDEBAR_WIDTH, 46)
        logo = Gtk.Image(icon_name=pick_icon(APP_ID, "applications-games-symbolic"), pixel_size=26,
                         margin_start=14, valign=Gtk.Align.CENTER)
        left.append(logo)
        tcol = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
        tcol.append(label("CACHYOS", css=["app-title"]))
        tcol.append(label("DAILYTOOL", css=["app-subtitle"]))
        left.append(tcol)
        bar.append(left)
        spacer = Gtk.Box(hexpand=True)
        bar.append(spacer)
        right = Gtk.Box(spacing=2, margin_end=4, valign=Gtk.Align.CENTER)
        # Sprach-Symbol: ein Klick auf eine Sprache speichert sie und startet das Programm neu (app.language)
        langs = Gio.Menu()
        for code, name in i18n.LANGUAGES.items():
            langs.append(tr(name), f"app.language::{code}")
        lang_menu = Gio.Menu()
        lang_menu.append_section(tr("Sprache der Oberfläche"), langs)
        lang_btn = Gtk.MenuButton(menu_model=lang_menu, tooltip_text=tr("Sprache der Oberfläche"))
        lang_btn.set_child(Adw.ButtonContent(icon_name=pick_icon("preferences-desktop-locale-symbolic", "languages-symbolic",
                                                                 "globe-symbolic"), label=i18n.current().upper()))
        lang_btn.add_css_class("flat")
        lang_btn.add_css_class("hdr-btn")
        right.append(lang_btn)
        right.append(icon_button(pick_icon("view-refresh-symbolic"), tr("Seite aktualisieren (Strg+R)"), ["flat", "hdr-btn"],
                                 on_click=self.refresh_current))
        menu = Gio.Menu()
        menu.append(tr("Einstellungen …"), "app.preferences")
        menu.append(tr("Protokollordner öffnen"), "app.logs")
        menu.append(tr("Über CachyOS DailyTool"), "app.about")
        menu.append(tr("Deinstallieren …"), "app.uninstall")
        menu.append(tr("Beenden"), "app.quit")
        mb = Gtk.MenuButton(icon_name=pick_icon("open-menu-symbolic"), tooltip_text=tr("Menü"), menu_model=menu)
        mb.add_css_class("flat")
        mb.add_css_class("hdr-btn")
        right.append(mb)
        right.append(Gtk.WindowControls(side=Gtk.PackType.END))
        bar.append(right)
        handle.set_child(bar)
        return handle

    def _build_sidebar(self) -> Gtk.Widget:
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroll.add_css_class("sidebar")
        scroll.set_size_request(SIDEBAR_WIDTH, -1)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_bottom=12)
        for section, classes in SECTIONS:
            box.append(label(section, css=["nav-section"]))
            for cls in classes:
                item = NavItem(cls.title, pick_icon(*cls.icons))
                item.connect("clicked", lambda _b, k=cls.key: self.navigate(k))
                self.nav[cls.key] = item
                self.page_classes[cls.key] = cls
                box.append(item)
        foot = label(f"v{__version__} · {system.system_info().hostname}", css=["app-subtitle"], xalign=0.0)
        foot.set_margin_start(22)
        foot.set_margin_top(18)
        box.append(foot)
        scroll.set_child(box)
        return scroll

    # ------------------------------------------------------------------ Navigation
    def navigate(self, key: str) -> None:
        cls = self.page_classes.get(key) or DashboardPage
        key = cls.key
        page = self.pages.get(key)
        if page is None:
            page = cls(self)
            self.pages[key] = page
            self.stack.add_named(page, key)
        if self.current and self.current != key:
            prev = self.pages.get(self.current)
            hide = getattr(prev, "on_hide", None)
            if hide:
                hide()
        page.ensure_built()
        self.stack.set_visible_child_name(key)
        for k, item in self.nav.items():
            item.set_selected(k == key)
        self.current = key
        self.cfg.set("LAST_PAGE", key, save=False)
        page.on_show()

    def refresh_current(self) -> None:
        page = self.pages.get(self.current or "")
        if page:
            page.refresh()

    def invalidate_pages(self) -> None:
        """Nach Einstellungsänderungen: sichtbare Seite neu laden, andere beim nächsten Öffnen."""
        for key, page in self.pages.items():
            if key == self.current:
                page.refresh()
            else:
                page.stale = True

    def prefix_list(self) -> list:
        """Alle Prefixe: Standardordner, weitere Speicherorte, einzeln eingetragene Pfade (im Hintergrund aufrufen)."""
        return system.list_prefixes(self.cfg.wine_prefix_root, self.registry.registered_prefix_paths(),
                                    [Path(l) for l in self.registry.locations])

    def toast(self, text: str, timeout: int = 4) -> None:
        self.toasts.add_toast(Adw.Toast(title=text, timeout=timeout))

    def open_preferences(self) -> None:
        from .dialogs import PreferencesDialog
        PreferencesDialog(self).present(self)

    # ------------------------------------------------------------------ Zähler in der Seitenleiste
    def refresh_badges(self) -> None:
        def work() -> tuple[int, list, int]:
            st = x3d.read_state() if x3d.daemon_alive() else None
            return (len(self.prefix_list()),
                    system.list_partitions(),
                    len((st or {}).get("sessions", [])))

        def done(res) -> None:
            n, parts, games = res
            self.nav["prefixes"].set_badge(str(n) if n else "")
            pending = [p for p in parts if p.fstype and not p.in_fstab]
            self.nav["drives"].set_badge(str(len(parts)) if parts else "", "accent" if pending else None)
            self.nav["x3d"].set_badge(str(games) if games else "", "accent" if games else None)

        run_async(work, done)

        def done_upd(res) -> None:
            ups, _ = res
            if ups is None:
                self.nav["update"].set_badge("")
            else:
                self.nav["update"].set_badge(str(len(ups)) if ups else "", "accent" if ups else "ok")

        run_async(system.pending_updates, done_upd)

    def save_state(self) -> None:
        """Fenstergröße und zuletzt geöffnete Seite sichern (beim Schließen und beim Beenden über das Menü)."""
        try:
            w, h = self.get_width(), self.get_height()
            if w > 0 and h > 0:
                self.cfg.set_window_size(w, h)
            else:
                self.cfg.save()
        except Exception:
            pass

    def _on_close(self, *_: object) -> bool:
        self.save_state()
        return False
