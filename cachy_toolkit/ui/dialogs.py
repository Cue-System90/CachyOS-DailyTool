"""Einstellungen (Pfade und Vorgaben)."""
from __future__ import annotations

from .. import compat
from .util import Adw, Gtk
from .widgets import PathRow, group


class PreferencesDialog(Adw.PreferencesDialog):
    def __init__(self, win) -> None:
        super().__init__(title="Einstellungen")
        self.win = win
        cfg = win.cfg
        page = Adw.PreferencesPage(title="Pfade & Vorgaben", icon_name="folder-symbolic")

        g = group("Verzeichnisse", f"Änderungen werden sofort gespeichert ({cfg.path}).")
        for key, title in (("WINE_PREFIX_ROOT", "Wine-Prefix-Root"), ("GAMES_TARGET_DRIVE", "Gaming-Zielordner"),
                           ("PREFIX_BACKUP_DIR", "Prefix-Backups")):
            g.add(PathRow(title, cfg.get(key), on_change=lambda p, k=key: self._set(k, p), parent_getter=lambda: win))
        page.add(g)

        g2 = group("Vorgaben")
        mb = Adw.EntryRow(title="Basisverzeichnis für neue Mountpoints", text=cfg.get("MOUNT_BASE"), show_apply_button=True)
        mb.connect("apply", lambda r: self._set("MOUNT_BASE", r.get_text().strip() or "/mnt"))
        g2.add(mb)
        lo = Adw.EntryRow(title="Standard-Startoptionen (Steam-Syntax)", text=cfg.get("LAUNCH_OPTIONS"), show_apply_button=True)
        lo.connect("apply", lambda r: self._set("LAUNCH_OPTIONS", r.get_text().strip()))
        g2.add(lo)
        sw = Adw.SwitchRow(title="GPU-Status automatisch aktualisieren", subtitle="Alle 2 Sekunden, solange die GPU-Seite offen ist",
                           active=cfg.get("GPU_AUTOREFRESH") == "1")
        sw.connect("notify::active", lambda r, _p: self._set("GPU_AUTOREFRESH", "1" if r.get_active() else "0", quiet=True))
        g2.add(sw)
        page.add(g2)

        g3 = group("Runner (Proton/Wine)", "Vorgabe für neue Prefixe und für Prefixe mit Runner „Automatisch“.")
        tools = win.launcher.tools(refresh=True)
        auto = compat.default_tool(tools, cfg.get("PREFER_UMU", "1") == "1")
        names = [f"Automatisch ({auto.name})" if auto else "Automatisch"] + [t.label for t in tools]
        self.tool_ids = [compat.AUTO_ID] + [t.id for t in tools]
        tr = Adw.ComboRow(title="Standard-Runner")
        tr.set_model(Gtk.StringList.new(names))
        cur = cfg.get("DEFAULT_TOOL", compat.AUTO_ID)
        tr.set_selected(self.tool_ids.index(cur) if cur in self.tool_ids else 0)
        tr.connect("notify::selected", lambda r, _p: self._set("DEFAULT_TOOL", self.tool_ids[r.get_selected()], quiet=True))
        g3.add(tr)
        umu = Adw.SwitchRow(title="umu-launcher bevorzugen", subtitle="Proton über umu starten (nötig für proton-cachyos-slr ohne Steam-Runtime)",
                            active=cfg.get("PREFER_UMU", "1") == "1")
        umu.set_sensitive(compat.umu_available())
        umu.connect("notify::active", lambda r, _p: self._set("PREFER_UMU", "1" if r.get_active() else "0", quiet=True))
        g3.add(umu)
        wl = Adw.SwitchRow(title="Wayland-Treiber von Proton als Vorgabe",
                           subtitle="Programme laufen direkt unter Wayland statt XWayland (PROTON_ENABLE_WAYLAND=1); "
                                    "je Prefix und Anwendung abschaltbar. Store-Launcher bleiben auf XWayland (weißes Fenster) – "
                                    "außer GOG Galaxy sowie Ubisoft Connect und Rockstar mit GE-Proton ab 11-6.",
                           active=cfg.get("WAYLAND_DRIVER", "1") == "1")
        wl.connect("notify::active", lambda r, _p: self._set("WAYLAND_DRIVER", "1" if r.get_active() else "0", quiet=True))
        g3.add(wl)
        page.add(g3)
        self.add(page)

    def _set(self, key: str, value: str, quiet: bool = False) -> None:
        self.win.cfg.set(key, value)
        self.win.cfg.ensure_dirs()
        self.win.refresh_badges()
        self.win.invalidate_pages()
        if not quiet:
            self.win.toast("Gespeichert.")
