"""Übersichtsseite: Kennzahlen, Schnellzugriff, Hinweise."""
from __future__ import annotations

from .. import system
from .util import Gtk, button, label, pick_icon, run_async
from .widgets import Card, Page, StatTile, button_flow


class DashboardPage(Page):
    key = "dashboard"
    title = "Übersicht"
    subtitle = ""
    icons = ("speedometer-symbolic", "utilities-system-monitor-symbolic", "emblem-system-symbolic")

    def build(self) -> None:
        self.add_refresh_button()
        row1 = Gtk.Box(spacing=12, homogeneous=True)
        row2 = Gtk.Box(spacing=12, homogeneous=True)
        self.t_gpu = StatTile("GPU")
        self.t_mem = StatTile("Arbeitsspeicher")
        self.t_up = StatTile("Laufzeit")
        self.t_upd = StatTile("Paket-Updates", "…")
        self.t_pfx = StatTile("Wine-Prefixe")
        self.t_drv = StatTile("Zusätzliche Laufwerke")
        for t in (self.t_gpu, self.t_mem, self.t_up):
            row1.append(t)
        for t in (self.t_upd, self.t_pfx, self.t_drv):
            row2.append(t)
        self.content.append(row1)
        self.content.append(row2)

        quick = Card("Schnellzugriff")
        qbuttons = []
        for text, icon, key in (
            ("Launcher installieren", ("applications-games-symbolic", "input-gaming-symbolic"), "launchers"),
            ("Programm starten", ("system-run-symbolic", "media-playback-start-symbolic"), "launch"),
            ("Laufwerk einbinden", ("drive-harddisk-symbolic",), "drives"),
            ("System-Update", ("software-update-available-symbolic", "system-software-update-symbolic"), "update"),
            ("Logs & Diagnose", ("utilities-terminal-symbolic", "text-x-generic-symbolic"), "logs"),
            ("Shader-Cache", ("edit-clear-all-symbolic", "user-trash-symbolic"), "cache"),
        ):
            qbuttons.append(button(text, ["btn-secondary"], icon=pick_icon(*icon), on_click=lambda k=key: self.win.navigate(k)))
        quick.append(button_flow(qbuttons, margin=0))
        self.content.append(quick)

        self.hints = Card("Hinweise")
        self.hints_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.hints.append(self.hints_box)
        self.content.append(self.hints)

        self.sys_card = Card("System")
        self.sys_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.sys_card.append(self.sys_box)
        self.content.append(self.sys_card)

    def on_show(self) -> None:
        def work() -> dict:
            info = system.system_info()
            return {
                "info": info,
                "gpus": system.gpu_status(),
                "prefixes": self.win.prefix_list(),
                "parts": system.list_partitions(),
                "reboot": system.kernel_needs_reboot(),
                "protonplus": system.protonplus_status(),
            }
        run_async(work, self._apply)
        run_async(system.pending_updates, self._apply_updates)

    def _apply(self, d: dict) -> None:
        info: system.SystemInfo = d["info"]
        self.header.set_subtitle(f"{info.hostname} · {info.distro} · Kernel {info.kernel} · {info.desktop} ({info.session})")
        gpus = d["gpus"]
        if gpus:
            g = gpus[0]
            parts = []
            if g.temp is not None:
                parts.append(f"{g.temp:.0f} °C")
            if g.util is not None:
                parts.append(f"{g.util:.0f} % Last")
            if g.vram_used is not None and g.vram_total:
                parts.append(f"VRAM {system.human_bytes(g.vram_used)} / {system.human_bytes(g.vram_total)}")
            self.t_gpu.set(g.name, " · ".join(parts) or g.driver,
                           "danger" if (g.temp or 0) >= 85 else ("warn" if (g.temp or 0) >= 75 else None))
        else:
            self.t_gpu.set("Keine GPU-Daten", "nvidia-smi/sysfs nicht verfügbar")
        if info.mem_total:
            used = info.mem_total - info.mem_avail
            pct = used / info.mem_total * 100
            self.t_mem.set(f"{system.human_bytes(used)} belegt", f"von {system.human_bytes(info.mem_total)} ({pct:.0f} %)",
                           "warn" if pct > 90 else None)
        self.t_up.set(system.fmt_uptime(info.uptime_s), "seit dem letzten Start")
        prefixes = d["prefixes"]
        self.t_pfx.set(str(len(prefixes)), str(self.cfg.wine_prefix_root))
        parts: list[system.Partition] = d["parts"]
        pending = [p for p in parts if p.fstype and not p.in_fstab]
        self.t_drv.set(str(len(parts)), f"{len(pending)} ohne fstab-Eintrag" if pending else "alle dauerhaft eingebunden",
                       "warn" if pending else None)

        # Hinweise
        child = self.hints_box.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.hints_box.remove(child)
            child = nxt
        hints: list[tuple[str, str, str | None]] = []
        if d["reboot"]:
            hints.append(("warn", "Ein neuer Kernel wurde installiert, läuft aber noch nicht – Neustart empfohlen.", "update"))
        for p in parts:
            if p.fstype and p.temp_mounted and not p.in_fstab:
                hints.append(("warn", f"{p.path} ({p.label or p.fstype}) ist nur temporär unter {p.mountpoint} eingebunden.", "drives"))
            elif p.fstype and not p.mountpoint and not p.in_fstab:
                hints.append(("info", f"{p.path} ({p.label or p.fstype}, {system.human_bytes(p.size)}) ist nicht eingebunden.", "drives"))
        if not prefixes:
            hints.append(("info", "Noch kein Wine-Prefix vorhanden – beim ersten Programmstart anlegen.", "launch"))
        if d["protonplus"] is None:
            hints.append(("info", "ProtonPlus ist nicht installiert (verwaltet Proton-GE, Wine-GE und weitere Runner).", "compat"))
        if info.mem_total and info.mem_avail / info.mem_total < 0.1:
            hints.append(("warn", "Weniger als 10 % Arbeitsspeicher frei.", None))
        self._hints_static = hints
        self._render_hints()

        # System
        child = self.sys_box.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.sys_box.remove(child)
            child = nxt
        for k, v in (("Rechner", info.hostname), ("System", info.distro), ("Kernel", info.kernel),
                     ("Desktop", f"{info.desktop} · {info.session}"), ("Prozessor", info.cpu),
                     ("Grafik", ", ".join(f"{g.name} ({g.driver})" for g in gpus) or "–")):
            row = Gtk.Box(spacing=12)
            kl = label(k, css=["stat-key"])
            kl.set_size_request(110, -1)
            row.append(kl)
            row.append(label(v, css=["body-text"], ellipsize=True, selectable=True))
            self.sys_box.append(row)

    _hints_static: list = []
    _hints_updates: list = []

    def _render_hints(self) -> None:
        child = self.hints_box.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.hints_box.remove(child)
            child = nxt
        hints = self._hints_updates + self._hints_static
        if not hints:
            self.hints_box.append(label("Alles in Ordnung – keine Hinweise.", css=["body-text", "ok"]))
            return
        for level, text, key in hints:
            row = Gtk.Box(spacing=10)
            icon = pick_icon("dialog-warning-symbolic") if level == "warn" else pick_icon("dialog-information-symbolic")
            img = Gtk.Image(icon_name=icon, pixel_size=16)
            img.add_css_class("warn" if level == "warn" else "dim")
            row.append(img)
            lbl = label(text, css=["body-text"], wrap=True)
            lbl.set_hexpand(True)
            row.append(lbl)
            if key:
                row.append(button("Öffnen", ["flat"], on_click=lambda k=key: self.win.navigate(k)))
            self.hints_box.append(row)

    def _apply_updates(self, res) -> None:
        ups, note = res
        if ups is None:
            self.t_upd.set("?", note)
            self._hints_updates = []
        elif ups:
            self.t_upd.set(str(len(ups)), f"verfügbar ({note})", "warn")
            self._hints_updates = [("info", f"{len(ups)} Paket-Updates verfügbar.", "update")]
        else:
            self.t_upd.set("0", "System ist aktuell", "ok")
            self._hints_updates = []
        self._render_hints()
