"""System: Update & DKMS, GPU-Status, Logs & Diagnose, Shader-Cache, Skalierung."""
from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

from .. import system
from .util import Adw, GLib, Gio, Gtk, button, confirm, copy_text, icon_button, label, pick_icon, run_async
from .widgets import Card, LogView, Page, StatTile, Tag, TaskRunner, action_row, group


class UpdatePage(Page):
    key = "update"
    title = "Update & DKMS"
    subtitle = "pacman -Syu mit anschließender Prüfung von DKMS-Modulen und Kernel-Status."
    icons = ("software-update-available-symbolic", "system-software-update-symbolic")

    def build(self) -> None:
        self.add_refresh_button("Updates prüfen")
        row = Gtk.Box(spacing=12, homogeneous=True)
        self.t_upd = StatTile("Verfügbare Updates", "…")
        self.t_kernel = StatTile("Laufender Kernel", os.uname().release)
        self.t_nv = StatTile("NVIDIA-Modul")
        self.t_dkms = StatTile("DKMS")
        for t in (self.t_upd, self.t_kernel, self.t_nv, self.t_dkms):
            row.append(t)
        self.content.append(row)

        self.ugroup = group("Verfügbare Updates")
        self.content.append(self.ugroup)
        self.ulog = LogView(min_height=140)
        self.ulog.set_vexpand(False)
        self.ugroup.add(self.ulog)

        bar = Gtk.Box(spacing=8)
        self.run_btn = button("SYSTEM AKTUALISIEREN", ["btn-primary"], icon=pick_icon("software-update-available-symbolic"), on_click=self._update)
        bar.append(self.run_btn)
        bar.append(button("DKMS-Status", ["btn-secondary"], on_click=self._dkms))
        self.content.append(bar)
        self.task = TaskRunner(min_height=220, idle_text="Ausgabe von pacman erscheint hier.")
        self.task.on_busy_changed = lambda b: self.run_btn.set_sensitive(not b)
        self.content.append(self.task)

    def on_show(self) -> None:
        self.t_upd.set("…", "prüfe …")
        run_async(system.pending_updates, self._apply_updates)
        run_async(lambda: (system.kernel_needs_reboot(), system.nvidia_module_info(), system.dkms_status()), self._apply_sys)

    def refresh(self) -> None:
        system.invalidate_updates_cache()
        self.on_show()
        self.win.refresh_badges()

    def _apply_updates(self, res) -> None:
        ups, note = res
        if ups is None:
            self.t_upd.set("?", note, "warn")
            self.ulog.set_text(note)
        elif ups:
            self.t_upd.set(str(len(ups)), note, "warn")
            self.ulog.set_text("\n".join(ups))
        else:
            self.t_upd.set("0", "System ist aktuell", "ok")
            self.ulog.set_text("Keine Updates verfügbar.")

    def _apply_sys(self, res) -> None:
        reboot, nv, dkms = res
        self.t_kernel.set(os.uname().release, "Neustart nötig – neuer Kernel installiert" if reboot else "aktuell geladen",
                          "warn" if reboot else "ok")
        self.t_nv.set(nv or "nicht geladen", "geladenes Kernelmodul" if nv else "kein NVIDIA-Modul", "ok" if nv else None)
        first = dkms.splitlines()[0] if dkms else "–"
        self.t_dkms.set("ok" if "installed" in dkms else ("–" if "nicht installiert" in dkms else "prüfen"), first[:60],
                        "ok" if "installed" in dkms else None)

    def _dkms(self) -> None:
        self.task.log.clear()
        run_async(system.dkms_status, lambda s: (self.task.log.set_text(s), self.task.set_status("DKMS-Status geladen.", "ok")))

    def _update(self) -> None:
        def do() -> None:
            try:
                argv = system.update_argv()
            except RuntimeError as exc:
                self.toast(str(exc))
                return

            def done(rc: int) -> None:
                def after() -> str:
                    return f"\n=== DKMS-Status ===\n{system.dkms_status()}\n=== Kernel ===\n" + \
                        ("Neuer Kernel installiert – Neustart empfohlen." if system.kernel_needs_reboot() else f"Laufender Kernel {os.uname().release} ist aktuell.")
                system.invalidate_updates_cache()
                run_async(after, lambda s: (self.task.log.append(s), self.on_show(), self.win.refresh_badges(),
                                            self.toast("System-Update abgeschlossen." if rc == 0 else "pacman meldete einen Fehler – Protokoll prüfen.")))
            self.task.run(argv, "pacman -Syu läuft … (Passwortabfrage über pkexec)", on_done=done)
        confirm(self, "System jetzt aktualisieren?",
                "Führt „pacman -Syu --noconfirm“ als root aus. Bei Paketkonflikten oder Schlüsselproblemen bricht pacman ab; "
                "dann bitte im Terminal nacharbeiten. Danach werden DKMS- und Kernel-Status geprüft.", "Aktualisieren", do)


class GpuPage(Page):
    key = "gpu"
    title = "GPU-Status"
    subtitle = "Live-Werte über nvidia-smi bzw. sysfs (amdgpu/Intel)."
    icons = ("video-display-symbolic", "display-projector-symbolic")

    def build(self) -> None:
        self.add_refresh_button()
        self.auto = Gtk.Switch(valign=Gtk.Align.CENTER, active=self.cfg.get("GPU_AUTOREFRESH") == "1",
                               tooltip_text="Alle 2 Sekunden aktualisieren")
        self.auto.connect("notify::active", self._toggle_auto)
        ab = Gtk.Box(spacing=8)
        ab.append(label("Automatisch", css=["hint"]))
        ab.append(self.auto)
        self.header.actions.prepend(ab)
        self.cards_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.content.append(self.cards_box)
        self.cards: dict[int, dict] = {}
        self.raw_btn = button("Vollständige nvidia-smi-Ausgabe", ["btn-secondary"], on_click=self._raw)
        self.raw_btn.set_visible(system.have("nvidia-smi"))
        rb = Gtk.Box()
        rb.append(self.raw_btn)
        self.content.append(rb)
        self.raw = LogView(min_height=200)
        self.raw.set_visible(False)
        self.content.append(self.raw)
        self._timer: int | None = None

    def on_show(self) -> None:
        self._poll()
        self._start_timer()

    def on_hide(self) -> None:
        self._stop_timer()

    def _toggle_auto(self, *_: object) -> None:
        self.cfg.set("GPU_AUTOREFRESH", "1" if self.auto.get_active() else "0")
        self._start_timer() if self.auto.get_active() else self._stop_timer()

    def _start_timer(self) -> None:
        self._stop_timer()
        if self.auto.get_active():
            self._timer = GLib.timeout_add(2000, self._tick)

    def _stop_timer(self) -> None:
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = None

    def _tick(self) -> bool:
        self._poll()
        return GLib.SOURCE_CONTINUE

    def _poll(self) -> None:
        run_async(system.gpu_status, self._apply)

    def _card(self, i: int, g: system.GpuStatus) -> dict:
        card = Card()
        head = Gtk.Box(spacing=10)
        head.append(label(g.name, css=["page-title"]))
        head.append(Tag(g.vendor, "accent"))
        drv = Tag(f"Treiber {g.driver}" if g.driver else "")
        head.append(drv)
        card.append(head)
        tiles = Gtk.Box(spacing=10, homogeneous=True)
        t = {k: StatTile(k) for k in ("Temperatur", "Auslastung", "Leistung", "GPU-Takt", "VRAM-Takt", "Lüfter")}
        for tile in t.values():
            tiles.append(tile)
        card.append(tiles)
        vram_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        vram_lbl = label("VRAM –", css=["stat-key"])
        vram_box.append(vram_lbl)
        vram_pb = Gtk.ProgressBar()
        vram_box.append(vram_pb)
        card.append(vram_box)
        self.cards_box.append(card)
        return {"card": card, "tiles": t, "vram_lbl": vram_lbl, "vram_pb": vram_pb, "drv": drv}

    def _apply(self, gpus: list[system.GpuStatus]) -> None:
        if not gpus and not self.cards:
            self.cards_box.append(Adw.StatusPage(title="Keine GPU-Daten", icon_name=pick_icon("video-display-symbolic"),
                                                 description="Weder nvidia-smi noch amdgpu/Intel-sysfs liefern Werte."))
            self.cards[-1] = {}
            return
        for i, g in enumerate(gpus):
            c = self.cards.get(i) or self._card(i, g)
            self.cards[i] = c
            t = c["tiles"]
            temp = g.temp
            t["Temperatur"].set(f"{temp:.0f} °C" if temp is not None else "–", None,
                                "danger" if (temp or 0) >= 85 else ("warn" if (temp or 0) >= 75 else "ok" if temp is not None else None))
            t["Auslastung"].set(f"{g.util:.0f} %" if g.util is not None else "–")
            t["Leistung"].set(f"{g.power:.0f} W" if g.power is not None else "–")
            t["GPU-Takt"].set(f"{g.clock_gpu} MHz" if g.clock_gpu else "–")
            t["VRAM-Takt"].set(f"{g.clock_mem} MHz" if g.clock_mem else "–")
            t["Lüfter"].set(f"{g.fan} %" if g.fan is not None and g.vendor == "NVIDIA" else (f"{g.fan} U/min" if g.fan is not None else "–"))
            if g.vram_total:
                used = g.vram_used or 0
                c["vram_lbl"].set_text(f"VRAM {system.human_bytes(used)} / {system.human_bytes(g.vram_total)} ({used / g.vram_total * 100:.0f} %)")
                c["vram_pb"].set_fraction(min(1.0, used / g.vram_total))
            else:
                c["vram_lbl"].set_text("VRAM –")
                c["vram_pb"].set_fraction(0)

    def _raw(self) -> None:
        self.raw.set_visible(True)
        run_async(lambda: system.run(["nvidia-smi"], timeout=15).stdout, self.raw.set_text)


class LogsPage(Page):
    key = "logs"
    title = "Logs & Diagnose"
    subtitle = "Systemd-Journal des aktuellen oder vorherigen Boots – mit Vorfiltern für Grafik, Gaming und Desktop."
    icons = ("utilities-terminal-symbolic", "text-x-generic-symbolic")

    def build(self) -> None:
        self.add_refresh_button("Neu laden")
        g = group("Filter")
        self.preset = Adw.ComboRow(title="Vorfilter")
        self.preset_keys = list(system.JOURNAL_PRESETS)
        self.preset.set_model(Gtk.StringList.new([system.JOURNAL_PRESETS[k][0] for k in self.preset_keys]))
        if system.have("nvidia-smi"):
            self.preset.set_selected(self.preset_keys.index("nvidia"))
        else:
            self.preset.set_selected(self.preset_keys.index("errors"))
        g.add(self.preset)
        self.pattern = Adw.EntryRow(title="Zusätzlicher Suchbegriff (regulärer Ausdruck, Groß/Klein egal)")
        self.pattern.connect("entry-activated", lambda *_: self.load())
        g.add(self.pattern)
        self.prev = Adw.SwitchRow(title="Vorheriger Boot", subtitle="journalctl -b -1 – hilfreich nach einem Absturz/Reboot")
        g.add(self.prev)
        self.limit = Adw.SpinRow.new_with_range(200, 20000, 200)
        self.limit.set_title("Maximale Zeilen")
        self.limit.set_value(3000)
        g.add(self.limit)
        self.content.append(g)
        bar = Gtk.Box(spacing=8)
        bar.append(button("LADEN", ["btn-primary"], icon=pick_icon("view-refresh-symbolic"), on_click=self.load))
        bar.append(button("Kopieren", ["btn-secondary"], icon=pick_icon("edit-copy-symbolic"), on_click=lambda: copy_text(self, self.log.get_text())))
        bar.append(button("Speichern …", ["btn-secondary"], icon=pick_icon("document-save-symbolic"), on_click=self._save))
        self.count = label("", css=["hint"])
        self.count.set_valign(Gtk.Align.CENTER)
        bar.append(self.count)
        self.content.append(bar)
        self.log = LogView(min_height=380)
        self.content.append(self.log)
        self._loaded = False

    def on_show(self) -> None:
        if not self._loaded:
            self.load()

    def refresh(self) -> None:
        self.load()

    def load(self) -> None:
        self._loaded = True
        preset = self.preset_keys[self.preset.get_selected()]
        pattern = self.pattern.get_text()
        limit = int(self.limit.get_value())
        prev = self.prev.get_active()
        self.count.set_text("lädt …")
        run_async(lambda: system.journal(preset, pattern, limit, prev),
                  lambda txt: (self.log.set_text(txt), self.count.set_text(f"{txt.count(chr(10)) + 1} Zeilen")))

    def _save(self) -> None:
        dlg = Gtk.FileDialog(title="Journal speichern", initial_name="journal.log")
        dlg.set_initial_folder(Gio.File.new_for_path(str(Path.home())))

        def done(d, res) -> None:
            try:
                f = d.save_finish(res)
            except GLib.Error:
                return
            if f and f.get_path():
                Path(f.get_path()).write_text(self.log.get_text(), encoding="utf-8")
                self.toast(f"Gespeichert: {f.get_path()}")
        dlg.save(self.win, None, done)


class CachePage(Page):
    key = "cache"
    title = "Shader-Cache leeren"
    subtitle = "Sinnvoll nach Treiberwechsel (z. B. nvidia ↔ nvidia-open) oder bei Grafikfehlern nach Updates. Shader werden beim nächsten Start neu gebaut."
    icons = ("edit-clear-all-symbolic", "user-trash-symbolic")

    def build(self) -> None:
        self.add_refresh_button("Größen neu berechnen")
        self.group = group("Caches")
        self.content.append(self.group)
        self.rows: list[Gtk.Widget] = []
        self.checks: dict[str, Gtk.CheckButton] = {}
        self.entries: list[system.CacheEntry] = []
        bar = Gtk.Box(spacing=8)
        self.clear_btn = button("AUSGEWÄHLTE LEEREN", ["btn-danger"], icon=pick_icon("edit-clear-all-symbolic"), on_click=self._clear)
        bar.append(self.clear_btn)
        self.content.append(bar)
        self.task = TaskRunner(min_height=100, idle_text="Ergebnis erscheint hier.")
        self.content.append(self.task)

    def on_show(self) -> None:
        self.group.set_description("Größen werden berechnet …")
        run_async(lambda: system.shader_caches(self.cfg.wine_prefix_root, self.cfg.games_target), self._fill, heavy=True)

    def _fill(self, entries: list[system.CacheEntry]) -> None:
        for r in self.rows:
            self.group.remove(r)
        self.rows.clear()
        self.checks.clear()
        self.entries = entries
        total = sum(e.size for e in entries)
        self.group.set_description(f"Gesamt {system.human_bytes(total)}")
        for e in entries:
            row = action_row(e.title, e.description, pick_icon("folder-symbolic"))
            cb = Gtk.CheckButton(valign=Gtk.Align.CENTER)
            cb.set_sensitive(e.exists and e.size > 0)
            row.add_prefix(cb)
            row.set_activatable_widget(cb)
            row.add_suffix(label(system.human_bytes(e.size) if e.exists else "nicht vorhanden", css=["body-text" if e.exists else "hint"]))
            if e.files:
                row.add_suffix(Tag(f"{len(e.files)} DATEIEN"))
            self.checks[e.key] = cb
            self.group.add(row)
            self.rows.append(row)

    def _clear(self) -> None:
        sel = [e for e in self.entries if self.checks.get(e.key) and self.checks[e.key].get_active()]
        if not sel:
            self.toast("Nichts ausgewählt.")
            return
        body = "\n".join(f"• {e.title}: {system.human_bytes(e.size)}" for e in sel)

        def do() -> None:
            def work(log) -> int:
                freed = 0
                for e in sel:
                    f, n = system.clear_cache(e)
                    freed += f
                    log(f"{e.title}: {n} Einträge, {system.human_bytes(f)} freigegeben")
                return freed
            self.task.run_callable(work, "Leere Caches …", on_done=lambda f: (self.toast(f"{system.human_bytes(f)} freigegeben."), self.on_show()))
        confirm(self, "Shader-Caches leeren?", body, "Leeren", do, destructive=True)


class ScalingPage(Page):
    key = "scaling"
    title = "Skalierung"
    subtitle = "Desktop-Skalierung (unter Plasma Wayland je Bildschirm) und Windows-DPI je Wine-Prefix."
    icons = ("preferences-desktop-display-symbolic", "zoom-in-symbolic", "video-display-symbolic")

    def build(self) -> None:
        self.add_refresh_button()
        g = group("Desktop-Skalierung")
        self.cur_row = action_row("Aktueller Wert", "…", pick_icon("preferences-desktop-display-symbolic"))
        g.add(self.cur_row)
        self.screens: list[system.ScreenScale] = []
        self.screen_row = Adw.ComboRow(title="Bildschirm", visible=False)
        self.screen_row.set_model(Gtk.StringList.new([]))
        self.screen_row.connect("notify::selected", lambda *_: self._sync_scale())
        g.add(self.screen_row)
        sr = Adw.ActionRow(title="Neue Skalierung")
        self.scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 100, 250, 5)
        self.scale.set_draw_value(True)
        self.scale.set_value_pos(Gtk.PositionType.RIGHT)
        self.scale.set_hexpand(True)
        self.scale.set_size_request(320, -1)
        self.scale.set_value(100)
        for v in (100, 125, 150, 175, 200):
            self.scale.add_mark(v, Gtk.PositionType.BOTTOM, f"{v} %")
        sr.add_suffix(self.scale)
        g.add(sr)
        self.content.append(g)
        bar = Gtk.Box(spacing=8)
        bar.append(button("ANWENDEN", ["btn-primary"], on_click=self._apply))
        bar.append(button("Anzeige-Einstellungen öffnen", ["btn-secondary"], icon=pick_icon("preferences-desktop-display-symbolic"),
                          on_click=lambda: system.open_display_settings() or self.toast("Keine Anzeige-Einstellungen gefunden.")))
        self.content.append(bar)
        self.hint = label("", css=["hint"], wrap=True)
        self.content.append(self.hint)

        g2 = group("Windows-DPI je Wine-Prefix", "Für unscharfe oder zu kleine Oberflächen in Windows-Programmen (LogPixels).")
        self.auto_dpi = Adw.SwitchRow(title="Automatisch an die Desktop-Skalierung anpassen",
                                      subtitle="Neue Prefixe und Prefixe ohne eigene DPI bekommen sie beim Anlegen bzw. vor dem Start.",
                                      active=self.cfg.get("AUTO_DPI", "1") == "1")
        self.auto_dpi.connect("notify::active", lambda r, _p: self.cfg.set("AUTO_DPI", "1" if r.get_active() else "0"))
        g2.add(self.auto_dpi)
        self.prefix_row = Adw.ComboRow(title="Prefix")
        self.prefix_row.set_model(Gtk.StringList.new([]))
        self.prefix_row.connect("notify::selected", lambda *_: self._sync_dpi())
        g2.add(self.prefix_row)
        self.dpi = Adw.SpinRow.new_with_range(96, 240, 12)
        self.dpi.set_title("DPI")
        self.dpi.set_subtitle("96 = 100 %, 120 = 125 %, 144 = 150 %, 192 = 200 %")
        ab = button("Anwenden", ["btn-secondary"], on_click=self._apply_dpi)
        ab.set_valign(Gtk.Align.CENTER)
        self.dpi.add_suffix(ab)
        g2.add(self.dpi)
        self.content.append(g2)
        self.prefixes: list[system.Prefix] = []

        g3 = group("Dieses Fenster", "Die Fenstergröße wird beim Schließen automatisch gespeichert.")
        rr = action_row("Fenstergröße zurücksetzen", "1180 × 760")
        rb = button("Zurücksetzen", ["btn-secondary"], on_click=lambda: (self.win.set_default_size(1180, 760), self.cfg.set_window_size(1180, 760)))
        rb.set_valign(Gtk.Align.CENTER)
        rr.add_suffix(rb)
        g3.add(rr)
        self.content.append(g3)

    def on_show(self) -> None:
        def read():
            screens = system.screen_scales()
            return screens, (None if screens else system.desktop_scale_get())

        def done(res) -> None:
            screens, legacy = res
            self.screens = screens
            if screens:
                self.cur_row.set_subtitle(", ".join(f"{s.percent} % · {s.label}" for s in screens) + " · Plasma Wayland (je Bildschirm)")
                keep = self.screen_row.get_selected()
                self.screen_row.set_model(Gtk.StringList.new([s.label for s in screens]))
                self.screen_row.set_selected(keep if 0 <= keep < len(screens) else 0)
                self.screen_row.set_visible(len(screens) > 1)
                self.hint.set_label("„Anwenden“ setzt die Skalierung des Bildschirms sofort – wie in den Anzeige-Einstellungen. "
                                    "X11-Programme und Wine bekommen sie über Xft.dpi; Wine-Prefixe ohne eigene DPI passt das Tool "
                                    "beim nächsten Start an.")
                self._sync_scale()
                return
            val, be = legacy
            name = {"kde": "KDE Plasma (kdeglobals)", "gnome": "GNOME (text-scaling-factor)", "other": "GDK_SCALE"}.get(be, be)
            self.cur_row.set_subtitle(f"{val} % · {name}" if val else f"nicht gesetzt (100 %) · {name}")
            self.screen_row.set_visible(False)
            self.hint.set_label("Hinweis: Der globale Wert (kdeglobals/GNOME/GDK_SCALE) wird nach Ab- und Anmelden wirksam.")
            self.scale.set_value(val or 100)
        run_async(read, done)

        def done_dpi(dpi: int | None) -> None:
            base = "Neue Prefixe und Prefixe ohne eigene DPI bekommen sie beim Anlegen bzw. vor dem Start."
            self.auto_dpi.set_subtitle(f"{base} Passend zum Desktop: {dpi} DPI." if dpi else base)
        run_async(system.wine_dpi_for_desktop, done_dpi)

        def done_p(prefixes: list[system.Prefix]) -> None:
            self.prefixes = prefixes
            self.prefix_row.set_model(Gtk.StringList.new([p.name for p in prefixes] or ["(kein Prefix vorhanden)"]))
            self._sync_dpi()
        run_async(self.win.prefix_list, done_p)

    def _sync_dpi(self) -> None:
        i = self.prefix_row.get_selected()
        if 0 <= i < len(self.prefixes):
            self.dpi.set_value(self.prefixes[i].dpi or 96)

    def _sync_scale(self) -> None:
        i = self.screen_row.get_selected()
        if 0 <= i < len(self.screens):
            self.scale.set_value(self.screens[i].percent)

    def _apply(self) -> None:
        pct = int(self.scale.get_value())
        i = self.screen_row.get_selected()
        screen = self.screens[i].name if 0 <= i < len(self.screens) else ""
        run_async(lambda: system.desktop_scale_set(pct, screen), lambda msg: (self.toast(msg, 8), self.on_show()))

    def _apply_dpi(self) -> None:
        i = self.prefix_row.get_selected()
        if not (0 <= i < len(self.prefixes)):
            self.toast("Kein Prefix vorhanden.")
            return
        p = self.prefixes[i]
        dpi = int(self.dpi.get_value())
        run_async(lambda: self.win.launcher.set_dpi(p.path, dpi),
                  lambda r: self.toast(f"DPI für „{p.name}“ auf {dpi} gesetzt." if r.returncode == 0 else f"Fehler: {r.stderr.strip()[:200]}"),
                  lambda exc: self.toast(f"Fehler: {exc}"))
