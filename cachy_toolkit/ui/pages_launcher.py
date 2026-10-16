"""Launcher & Speicherorte: Migration per rsync+Symlink, ProtonPlus/Compat-Tools, Speicherplatz."""
from __future__ import annotations

from pathlib import Path

from .. import system
from .util import Adw, Gtk, button, confirm, icon_button, label, pick_icon, run_async
from .widgets import Card, Page, PathRow, Tag, TaskRunner, action_row, group


class StoragePage(Page):
    key = "storage"
    title = "Speicherorte & Migration"
    subtitle = "Launcher-Daten (Steam, Lutris, Heroic, Bottles, Compat-Tools, Launcher in Prefixen) auf ein anderes Laufwerk verschieben – Original wird zum Symlink."
    icons = ("drive-multidisk-symbolic", "folder-remote-symbolic", "folder-symbolic")

    def build(self) -> None:
        self.add_refresh_button("Launcher neu suchen")
        self.found: list[system.Launcher] = []
        self.checks: dict[str, Gtk.CheckButton] = {}
        self.rows: list[Gtk.Widget] = []

        g = group("Zielordner", "Dorthin werden ausgewählte Ordner verschoben; am alten Ort bleibt ein Symlink.")
        self.target_row = PathRow("Gaming-Zielordner", str(self.cfg.games_target), on_change=self._set_target,
                                  parent_getter=lambda: self.win, choose_title="Zielordner wählen")
        g.add(self.target_row)
        self.free_row = action_row("Freier Platz am Ziel", "…", pick_icon("drive-harddisk-symbolic"))
        g.add(self.free_row)
        self.content.append(g)

        self.lgroup = group("Gefundene Launcher", "Bitte die betreffenden Programme vorher beenden.")
        self.content.append(self.lgroup)

        bar = Gtk.Box(spacing=8)
        self.move_btn = button("AUSGEWÄHLTE VERSCHIEBEN", ["btn-primary"], icon=pick_icon("go-jump-symbolic", "edit-cut-symbolic"), on_click=self._migrate)
        bar.append(self.move_btn)
        self.content.append(bar)
        self.task = TaskRunner(min_height=160, idle_text="rsync-Fortschritt und Ergebnis erscheinen hier.")
        self.task.on_busy_changed = lambda b: self.move_btn.set_sensitive(not b)
        self.content.append(self.task)

    def on_show(self) -> None:
        self.target_row.set_path(str(self.cfg.games_target))
        self._update_free()
        run_async(lambda: system.scan_launchers(self.cfg.wine_prefix_root), self._fill, heavy=True)

    def _set_target(self, p: str) -> None:
        self.cfg.set("GAMES_TARGET_DRIVE", p)
        self.cfg.ensure_dirs()
        self._update_free()

    def _update_free(self) -> None:
        def work() -> str:
            t = str(self.cfg.games_target)
            for m in system.mounted_filesystems():
                pass
            r = system.run(["findmnt", "-T", t, "-n", "-b", "-o", "TARGET,SOURCE,FSTYPE,AVAIL,SIZE"], timeout=10)
            parts = r.stdout.split()
            if len(parts) >= 5:
                return f"{system.human_bytes(int(parts[3]))} frei von {system.human_bytes(int(parts[4]))} · {parts[0]} ({parts[1]}, {parts[2]})"
            return "Ziel ist nicht erreichbar."
        run_async(work, self.free_row.set_subtitle)

    def _fill(self, found: list[system.Launcher]) -> None:
        for r in self.rows:
            self.lgroup.remove(r)
        self.rows.clear()
        self.checks.clear()
        self.found = found
        if not found:
            lbl = label("Keine bekannten Launcher gefunden.", css=["hint"])
            self.lgroup.add(lbl)
            self.rows.append(lbl)
            return
        for l in found:
            row = action_row(l.name, f"{l.kind} · {l.path}", pick_icon("input-gaming-symbolic", "applications-games-symbolic"))
            cb = Gtk.CheckButton(valign=Gtk.Align.CENTER)
            row.add_prefix(cb)
            row.set_activatable_widget(cb)
            if l.is_symlink:
                cb.set_sensitive(False)
                row.add_suffix(Tag("BEREITS VERSCHOBEN", "ok"))
                row.set_subtitle(f"{l.kind} · {l.path} → {l.target}")
            else:
                size_lbl = label("…", css=["hint"])
                size_lbl.set_valign(Gtk.Align.CENTER)
                row.add_suffix(size_lbl)
                run_async(lambda ll=l: system.dir_size(ll.path), lambda s, sl=size_lbl, ll=l: (setattr(ll, "size", s), sl.set_text(system.human_bytes(s))), heavy=True)
            ob = icon_button(pick_icon("folder-open-symbolic"), "Öffnen", ["flat"], on_click=lambda ll=l: system.open_path(ll.path))
            ob.set_valign(Gtk.Align.CENTER)
            row.add_suffix(ob)
            self.checks[l.key] = cb
            self.lgroup.add(row)
            self.rows.append(row)

    def _migrate(self) -> None:
        sel = [l for l in self.found if l.key in self.checks and self.checks[l.key].get_active()]
        if not sel:
            self.toast("Nichts ausgewählt.")
            return
        dest = self.cfg.games_target
        total = sum(l.size or 0 for l in sel)
        body = "\n".join(f"• {l.name}: {l.path} → {dest / l.path.name}" for l in sel)
        body += f"\n\nGesamt etwa {system.human_bytes(total)}. Die Programme müssen beendet sein. Nach erfolgreichem Kopieren wird das Original gelöscht und durch einen Symlink ersetzt."

        def do() -> None:
            def work(log) -> list[str]:
                results = []
                for l in sel:
                    log(f"=== {l.name} ===")
                    try:
                        d = system.migrate_dir(l.path, dest, log)
                        results.append(f"{l.name}: → {d}")
                    except Exception as exc:
                        log(f"FEHLER: {exc}")
                        results.append(f"{l.name}: FEHLER – {exc}")
                return results
            self.task.run_callable(work, "Verschiebe …", on_done=lambda res: (self.toast("; ".join(res)[:300], 8), self.on_show()),
                                   on_error=lambda exc: self.on_show())
        confirm(self, f"{len(sel)} Ordner verschieben?", body, "Verschieben", do, destructive=True)


class CompatPage(Page):
    key = "compat"
    title = "ProtonPlus & Compat-Tools"
    subtitle = "ProtonPlus verwaltet Proton-GE, Wine-GE, DXVK & Co. für Steam, Lutris, Heroic und Bottles."
    icons = ("preferences-other-symbolic", "emblem-system-symbolic", "applications-engineering-symbolic")

    def build(self) -> None:
        self.add_refresh_button()
        self.status_card = Card("ProtonPlus")
        srow = Gtk.Box(spacing=12)
        self.status_lbl = label("…", css=["body-text"])
        self.status_lbl.set_hexpand(True)
        srow.append(self.status_lbl)
        self.launch_btn = button("ProtonPlus starten", ["btn-primary"], icon=pick_icon("media-playback-start-symbolic"), on_click=self._launch)
        self.install_btn = button("Installieren / Aktualisieren (Flatpak)", ["btn-secondary"], icon=pick_icon("software-update-available-symbolic"),
                                  on_click=self._install)
        srow.append(self.launch_btn)
        srow.append(self.install_btn)
        self.status_card.append(srow)
        self.tags = Gtk.Box(spacing=6)
        self.status_card.append(self.tags)
        self.content.append(self.status_card)

        self.tgroup = group("Installierte Compat-Tools", "Proton-/Wine-Versionen in den Tool-Ordnern der Launcher")
        self.content.append(self.tgroup)
        self.pgroup = group("Pfade", "Wo die Launcher ihre Compat-Tools erwarten")
        self.content.append(self.pgroup)
        self.trows: list[Gtk.Widget] = []
        self.prows: list[Gtk.Widget] = []
        self.task = TaskRunner(min_height=120, idle_text="Flatpak-Ausgabe erscheint hier.")
        self.content.append(self.task)
        self.status: str | None = None

    def on_show(self) -> None:
        def work() -> dict:
            return {"status": system.protonplus_status(),
                    "tools": system.list_compat_tools(self.cfg.wine_prefix_root),
                    "dirs": system.compat_tool_dirs(self.cfg.wine_prefix_root),
                    "umu": system.have("umu-run"), "flatpak": system.have("flatpak"),
                    "cachyos_proton": sorted(p.name for p in Path("/usr/share/steam/compatibilitytools.d").glob("proton-cachyos*")) if Path("/usr/share/steam/compatibilitytools.d").is_dir() else []}
        run_async(work, self._apply)

    def _apply(self, d: dict) -> None:
        self.status = d["status"]
        if self.status == "native":
            self.status_lbl.set_text("Installiert (natives Paket).")
        elif self.status == "flatpak":
            self.status_lbl.set_text(f"Installiert als Flatpak ({system.PROTONPLUS_FLATPAK}).")
        else:
            self.status_lbl.set_text("Nicht installiert." + ("" if d["flatpak"] else " Flatpak fehlt ebenfalls (sudo pacman -S flatpak)."))
        self.launch_btn.set_sensitive(self.status is not None)
        self.install_btn.set_sensitive(d["flatpak"])
        child = self.tags.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.tags.remove(child)
            child = nxt
        self.tags.append(Tag("umu-launcher " + ("installiert" if d["umu"] else "fehlt"), "ok" if d["umu"] else "warn"))
        for name in d["cachyos_proton"]:
            self.tags.append(Tag(name, "ok"))
        if not d["cachyos_proton"]:
            self.tags.append(Tag("proton-cachyos nicht gefunden", "warn"))

        for r in self.trows:
            self.tgroup.remove(r)
        self.trows.clear()
        tools: list[system.CompatTool] = d["tools"]
        if not tools:
            lbl = label("Keine Compat-Tools in den bekannten Ordnern.", css=["hint"])
            self.tgroup.add(lbl)
            self.trows.append(lbl)
        for t in tools:
            row = action_row(t.name, f"{t.location} · {t.path}", pick_icon("package-x-generic-symbolic"))
            ob = icon_button(pick_icon("folder-open-symbolic"), "Öffnen", ["flat"], on_click=lambda tt=t: system.open_path(tt.path))
            ob.set_valign(Gtk.Align.CENTER)
            row.add_suffix(ob)
            self.tgroup.add(row)
            self.trows.append(row)

        for r in self.prows:
            self.pgroup.remove(r)
        self.prows.clear()
        for lbl_text, p in d["dirs"]:
            row = action_row(lbl_text, str(p), pick_icon("folder-symbolic"))
            row.add_suffix(Tag("VORHANDEN" if p.is_dir() else "FEHLT", "ok" if p.is_dir() else None))
            if p.is_dir():
                ob = icon_button(pick_icon("folder-open-symbolic"), "Öffnen", ["flat"], on_click=lambda pp=p: system.open_path(pp))
                ob.set_valign(Gtk.Align.CENTER)
                row.add_suffix(ob)
            self.pgroup.add(row)
            self.prows.append(row)

    def _launch(self) -> None:
        if self.status:
            system.protonplus_launch(self.status)
            self.toast("ProtonPlus wird gestartet …")

    def _install(self) -> None:
        confirm(self, "ProtonPlus per Flatpak installieren/aktualisieren?",
                "Flathub wird bei Bedarf als Benutzer-Remote hinzugefügt; dann wird com.vysp3r.ProtonPlus installiert oder aktualisiert.",
                "Installieren", lambda: self.task.run_steps(system.protonplus_install_steps(), "Installiere ProtonPlus …",
                                                             on_done=lambda rc: self.on_show()))


class UsagePage(Page):
    key = "usage"
    title = "Speicherplatz"
    subtitle = "Belegung der Dateisysteme und Größe der Gaming-Ordner."
    icons = ("drive-harddisk-symbolic", "disk-usage-symbolic", "folder-symbolic")

    def build(self) -> None:
        self.add_refresh_button()
        self.fgroup = group("Dateisysteme")
        self.content.append(self.fgroup)
        self.dgroup = group("Gaming-Ordner", "Größen werden im Hintergrund berechnet (du)")
        self.content.append(self.dgroup)
        self.frows: list[Gtk.Widget] = []
        self.drows: list[Gtk.Widget] = []
        self.total_lbl = label("", css=["body-text"])
        self.dgroup.set_header_suffix(self.total_lbl)

    def on_show(self) -> None:
        run_async(system.mounted_filesystems, self._fill_fs)
        self._fill_dirs()

    def _fill_fs(self, fss: list[system.MountedFs]) -> None:
        for r in self.frows:
            self.fgroup.remove(r)
        self.frows.clear()
        for m in fss:
            if m.fstype in ("tmpfs", "devtmpfs", "efivarfs", "autofs", "overlay") and not m.target.startswith("/mnt"):
                continue
            row = action_row(m.target, f"{m.source} · {m.fstype}", pick_icon("drive-harddisk-symbolic"))
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, valign=Gtk.Align.CENTER)
            box.set_size_request(220, -1)
            pb = Gtk.ProgressBar()
            pb.set_fraction(min(1.0, m.use_pct / 100))
            box.append(pb)
            style = "danger" if m.use_pct >= 95 else ("warn" if m.use_pct >= 85 else "hint")
            box.append(label(f"{system.human_bytes(m.avail)} frei von {system.human_bytes(m.size)} ({m.use_pct} %)", css=[style], xalign=1.0))
            row.add_suffix(box)
            ob = icon_button(pick_icon("folder-open-symbolic"), "Öffnen", ["flat"], on_click=lambda t=m.target: system.open_path(t))
            ob.set_valign(Gtk.Align.CENTER)
            row.add_suffix(ob)
            self.fgroup.add(row)
            self.frows.append(row)

    def _fill_dirs(self) -> None:
        for r in self.drows:
            self.dgroup.remove(r)
        self.drows.clear()
        self.total_lbl.set_text("")
        dirs = system.gaming_dirs(self.cfg.wine_prefix_root, self.cfg.games_target, self.cfg.backup_dir)
        self._sizes: dict[str, int] = {}
        for lbl_text, p in dirs:
            exists = p.exists()
            row = action_row(lbl_text, str(p) + ("" if exists else " (nicht vorhanden)"), pick_icon("folder-symbolic"))
            size_lbl = label("…" if exists else "–", css=["body-text"])
            size_lbl.set_valign(Gtk.Align.CENTER)
            row.add_suffix(size_lbl)
            if p.is_symlink():
                row.add_suffix(Tag("SYMLINK", "ok"))
            if exists:
                ob = icon_button(pick_icon("folder-open-symbolic"), "Öffnen", ["flat"], on_click=lambda pp=p: system.open_path(pp))
                ob.set_valign(Gtk.Align.CENTER)
                row.add_suffix(ob)
                run_async(lambda pp=p: system.dir_size(pp), lambda s, sl=size_lbl, key=str(p): self._size_done(key, s, sl), heavy=True)
            self.dgroup.add(row)
            self.drows.append(row)

    def _size_done(self, key: str, size: int, lbl: Gtk.Label) -> None:
        lbl.set_text(system.human_bytes(size))
        self._sizes[key] = size
        self.total_lbl.set_text(f"Summe: {system.human_bytes(sum(self._sizes.values()))}")
