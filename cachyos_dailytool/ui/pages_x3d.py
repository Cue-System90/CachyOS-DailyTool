"""X3D-Kernzuteilung (CCD): Topologie, Rangfolge der Kerne, Hintergrunddienst, erkannte Spiele, Katalog, eigene Festlegungen."""
from __future__ import annotations

import logging
import time

from .. import system, x3d
from ..i18n import tr
from .util import Adw, GLib, Gtk, button, confirm, fmt_time, label, pick_icon, run_async
from .widgets import Card, LogView, Page, StatTile, Tag, TaskRunner, group

log = logging.getLogger(__name__)

MODE_CHOICES = ("cache", "frequency", "none")
MODE_TITLES = {"cache": tr("Cache-CCD"), "frequency": tr("Frequenz-CCD"), "none": tr("Frei")}
MODE_LONG = {"cache": tr("Cache-CCD (3D V-Cache)"), "frequency": tr("Frequenz-CCD"), "none": tr("Frei (alle Kerne)")}
TAG_STYLE = {"cache": "accent", "frequency": "ok", "none": None}
IDLE_CHOICES = ("frequency", "cache", "keep")
IDLE_TITLES = [tr("Frequenz-CCD (AMD-Vorgabe)"), tr("Cache-CCD"), tr("Unverändert lassen")]
DEFAULT_TITLES = [tr("Cache-CCD (3D V-Cache) – wie AMDs Treiber"), tr("Frequenz-CCD"), tr("Nicht zuweisen (nur beobachten)")]


class CpuBars(Gtk.DrawingArea):
    """Balken je CPU (Auslastung in Prozent) mit CPU-Nummern darunter."""

    def __init__(self, cpus: list[int], color: tuple[float, float, float]) -> None:
        super().__init__()
        self.cpus = list(cpus)
        self.values: dict[int, float] = {}
        self.color = color
        self.set_content_height(68)
        self.set_hexpand(True)
        self.set_draw_func(self._draw)

    def set_values(self, pct: dict[int, float]) -> None:
        self.values = pct
        self.queue_draw()

    def _draw(self, _area, cr, w: int, h: int) -> None:
        n = len(self.cpus)
        if not n or w <= 0:
            return
        gap = 3.0
        bw = max(2.0, (w - gap * (n - 1)) / n)
        label_h = 13.0
        bar_h = h - label_h - 2
        cr.set_font_size(8.5)
        for i, cpu in enumerate(self.cpus):
            x = i * (bw + gap)
            v = max(0.0, min(1.0, self.values.get(cpu, 0.0) / 100.0))
            cr.set_source_rgba(1, 1, 1, 0.07)
            cr.rectangle(x, 0, bw, bar_h)
            cr.fill()
            hh = max(1.5, bar_h * v)
            cr.set_source_rgba(self.color[0], self.color[1], self.color[2], 0.35 + 0.65 * v)
            cr.rectangle(x, bar_h - hh, bw, hh)
            cr.fill()
            text = str(cpu)
            ext = cr.text_extents(text)
            cr.set_source_rgba(0.62, 0.62, 0.66, 1)
            cr.move_to(x + (bw - ext.width) / 2, h - 2)
            cr.show_text(text)


def _clear(box: Gtk.Widget) -> None:
    child = box.get_first_child()
    while child:
        nxt = child.get_next_sibling()
        box.remove(child)
        child = nxt


class X3dPage(Page):
    key = "x3d"
    title = tr("X3D-Kerne (CCD)")
    subtitle = tr("Spiele automatisch dem CCD mit dem 3D V-Cache zuweisen – nach dem Muster von AMDs 3D-V-Cache-Treiber und "
                  "Anwendungs-Kompatibilitätsdatenbank unter Windows.")
    icons = ("cpu-symbolic", "processor-symbolic", "applications-engineering-symbolic", "emblem-system-symbolic")

    # ------------------------------------------------------------------ Aufbau
    def build(self) -> None:
        self.add_refresh_button()
        self._timer: int | None = None
        self._loading = False
        self._state: dict | None = None
        self._alive = False
        self._writable = False
        self._mode: str | None = None
        self._last_events = ""
        self._search = ""
        self.topo = x3d.topology()
        self.rules = x3d.UserRules()
        self.catalog = x3d.Catalog()
        cfg = self.cfg

        row = Gtk.Box(spacing=12, homogeneous=True)
        self.t_cpu = StatTile(tr("Prozessor"))
        self.t_mode = StatTile(tr("Bevorzugte Kerne"))
        self.t_service = StatTile(tr("Hintergrunddienst"))
        self.t_games = StatTile(tr("Erkannte Spiele"), "0")
        for t in (self.t_cpu, self.t_mode, self.t_service, self.t_games):
            row.append(t)
        self.content.append(row)

        self.notice = label("", css=["body-text", "warn"], wrap=True)
        self.notice.set_visible(False)
        self.content.append(self.notice)

        # CCDs und Rangfolge
        self.ccd_card = Card(tr("Core Complex Dies (CCD) und Rangfolge der Kerne"))
        self.ccd_box = Gtk.Box(spacing=12, homogeneous=True)
        self.ccd_card.append(self.ccd_box)
        self.ccd_widgets: dict[int, dict] = {}
        self._prev_cpu: dict[int, tuple[int, int]] = {}
        self._build_ccds()
        bar = Gtk.Box(spacing=8)
        self.btn_cache = button(tr("CACHE-CCD BEVORZUGEN"), ["btn-secondary"], on_click=lambda: self._set_mode("cache"),
                                tooltip=tr("amd_x3d_mode = cache: der Kernel bevorzugt die Kerne mit 3D V-Cache"))
        self.btn_freq = button(tr("FREQUENZ-CCD BEVORZUGEN"), ["btn-secondary"], on_click=lambda: self._set_mode("frequency"),
                               tooltip=tr("amd_x3d_mode = frequency: der Kernel bevorzugt die höher taktenden Kerne (AMD-Vorgabe)"))
        self.btn_access = button(tr("ZUGRIFF EINRICHTEN …"), ["btn-primary"], icon=pick_icon("dialog-password-symbolic", "changes-prevent-symbolic"),
                                 on_click=self._setup_access, tooltip=tr("Einmalig als Root: Schreibrecht auf amd_x3d_mode für deine Gruppe"))
        for b in (self.btn_cache, self.btn_freq, self.btn_access):
            bar.append(b)
        self.ccd_card.append(bar)
        self.ccd_hint = label("", css=["hint"], wrap=True)
        self.ccd_card.append(self.ccd_hint)
        self.content.append(self.ccd_card)

        # Automatik
        self.auto_group = group(tr("Automatik (wie AMDs Treiber)"),
                                tr("Läuft ein Spiel, stellt der Dienst die Rangfolge auf dessen CCD und hält den gesamten Prozessbaum des Spiels "
                                   "dort (alle Threads). Steam, Launcher, Discord und Browser wechseln währenddessen auf den anderen CCD. "
                                   "Nach dem Spiel gilt wieder die Ruhe-Vorgabe."))
        self.sw_enabled = Adw.SwitchRow(title=tr("Erkennung und Zuweisung aktiv"),
                                        subtitle=tr("Aus: der Dienst läuft weiter, weist aber nichts zu und hebt bestehende Zuweisungen auf"))
        self.sw_service = Adw.SwitchRow(title=tr("Hintergrunddienst läuft"),
                                        subtitle=tr("cachyos-dailytool --x3d-daemon über systemd --user ({0})").format(x3d.UNIT_NAME))
        self.sw_autostart = Adw.SwitchRow(title=tr("Beim Anmelden starten"), subtitle="systemctl --user enable")
        self.cb_default = Adw.ComboRow(title=tr("Unbekannte Spiele"),
                                       subtitle=tr("Erkannt an GPU-Zugriff und Herkunft: Proton/Wine, Steam, umu, Heroic, Lutris, Spieleordner"))
        self.cb_default.set_model(Gtk.StringList.new(DEFAULT_TITLES))
        self.cb_idle = Adw.ComboRow(title=tr("Ohne laufendes Spiel bevorzugen"),
                                    subtitle=tr("Rangfolge der Kerne (amd_x3d_mode), sobald kein Spiel mehr läuft"))
        self.cb_idle.set_model(Gtk.StringList.new(IDLE_TITLES))
        self.sw_bg = Adw.SwitchRow(title=tr("Hintergrundprogramme auf den anderen CCD"),
                                   subtitle=tr("Steam, Store-Launcher, Discord, Browser – nur solange ein Spiel läuft; danach wieder alle Kerne"))
        self.sw_enforce = Adw.SwitchRow(title=tr("Zuweisung erzwingen"),
                                        subtitle=tr("Threads zurückholen, die ein Spiel selbst außerhalb seines CCD platziert"))
        self.en_dirs = Adw.EntryRow(title=tr("Zusätzliche Spieleordner für native Spiele (durch : getrennt)"), show_apply_button=True)
        for w in (self.sw_enabled, self.sw_service, self.sw_autostart, self.cb_default, self.cb_idle, self.sw_bg, self.sw_enforce, self.en_dirs):
            self.auto_group.add(w)
        self.content.append(self.auto_group)
        self.sw_enabled.connect("notify::active", lambda r, _p: self._cfg("X3D_ENABLED", "1" if r.get_active() else "0"))
        self.sw_bg.connect("notify::active", lambda r, _p: self._cfg("X3D_BACKGROUND", "1" if r.get_active() else "0"))
        self.sw_enforce.connect("notify::active", lambda r, _p: self._cfg("X3D_ENFORCE", "1" if r.get_active() else "0"))
        self.cb_default.connect("notify::selected", lambda r, _p: self._cfg("X3D_DEFAULT_MODE", MODE_CHOICES[r.get_selected()]))
        self.cb_idle.connect("notify::selected", lambda r, _p: self._cfg("X3D_IDLE_MODE", IDLE_CHOICES[r.get_selected()]))
        self.en_dirs.connect("apply", lambda r: self._cfg("X3D_GAME_DIRS", r.get_text().strip()))
        self.sw_service.connect("notify::active", self._toggle_service)
        self.sw_autostart.connect("notify::active", self._toggle_autostart)
        self._load_settings(cfg)

        # Erkannte Spiele
        self.games_card = Card(tr("Erkannte Spiele (live)"))
        self.games_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.games_list.add_css_class("boxed-list")
        self.games_empty = label(tr("Kein Spiel erkannt. Der Dienst prüft alle 1,5 s die eigenen Prozesse."), css=["hint"])
        self.games_card.append(self.games_empty)
        self.games_card.append(self.games_list)
        self.content.append(self.games_card)

        # Katalog
        self.cat_card = Card(tr("Katalog und eigene Festlegungen"))
        top = Gtk.Box(spacing=8)
        self.search = Gtk.SearchEntry(placeholder_text=tr("Spiel, .exe oder Steam-AppID suchen …"), hexpand=True)
        self.search.connect("search-changed", lambda e: self._set_search(e.get_text()))
        top.append(self.search)
        top.append(button(tr("Eintrag hinzufügen …"), ["btn-secondary"], icon=pick_icon("list-add-symbolic"), on_click=self._add_dialog))
        self.cat_card.append(top)
        self.cat_info = label("", css=["hint"], wrap=True)
        self.cat_card.append(self.cat_info)
        self.cat_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.cat_list.add_css_class("boxed-list")
        self.cat_card.append(self.cat_list)
        self.content.append(self.cat_card)

        # Protokoll + Root-Aufgaben
        self.log_card = Card(tr("Protokoll des Dienstes"))
        self.log = LogView(min_height=150)
        self.log.set_vexpand(False)
        self.log_card.append(self.log)
        self.content.append(self.log_card)
        self.task = TaskRunner(min_height=90, idle_text=tr("Ausgabe von Einrichtungsschritten erscheint hier."))
        self.task.set_visible(False)
        self.content.append(self.task)
        self._render_catalog()

    def _load_settings(self, cfg) -> None:
        self._loading = True
        try:
            self.sw_enabled.set_active(cfg.get("X3D_ENABLED", "1") == "1")
            self.sw_bg.set_active(cfg.get("X3D_BACKGROUND", "1") == "1")
            self.sw_enforce.set_active(cfg.get("X3D_ENFORCE", "1") == "1")
            d = cfg.get("X3D_DEFAULT_MODE", "cache")
            self.cb_default.set_selected(MODE_CHOICES.index(d) if d in MODE_CHOICES else 0)
            i = cfg.get("X3D_IDLE_MODE", "frequency")
            self.cb_idle.set_selected(IDLE_CHOICES.index(i) if i in IDLE_CHOICES else 0)
            self.en_dirs.set_text(cfg.get("X3D_GAME_DIRS", ""))
        finally:
            self._loading = False

    # ------------------------------------------------------------------ Anzeige/Aktualisierung
    def on_show(self) -> None:
        self.rules.load()
        self._collect(full=True)
        self._start_timer()

    def on_hide(self) -> None:
        self._stop_timer()

    def refresh(self) -> None:
        self.rules.load()
        self.topo = x3d.topology(refresh=True)
        self._build_ccds()
        self._collect(full=True)
        self._render_catalog()

    def _start_timer(self) -> None:
        self._stop_timer()
        self._timer = GLib.timeout_add(2000, self._tick)

    def _stop_timer(self) -> None:
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = None

    def _tick(self) -> bool:
        self._collect(full=False)
        return GLib.SOURCE_CONTINUE

    def _collect(self, full: bool) -> None:
        def work() -> dict:
            cur = x3d.cpu_times()
            usage = x3d.cpu_usage(self._prev_cpu, cur) if self._prev_cpu else {}
            self._prev_cpu = cur
            d = {"state": x3d.read_state(), "mode": x3d.read_mode(), "writable": x3d.mode_writable(),
                 "alive": x3d.daemon_alive(), "has_driver": x3d.mode_path() is not None, "usage": usage}
            if full:
                d["autostart"] = x3d.autostart_enabled() if x3d.systemd_user_available() else None
            return d
        run_async(work, lambda d: self._apply(d, full))

    def _apply(self, d: dict, full: bool) -> None:
        self._state, self._mode, self._writable, self._alive = d["state"], d["mode"], d["writable"], d["alive"]
        topo = self.topo
        st = d["state"] or {}
        sessions = st.get("sessions", []) if self._alive else []
        import re as _re
        model = _re.sub(r"^AMD\s+Ryzen\s+\d\s+", "", _re.sub(r"\s+\d+-Core Processor$|\s+Processor$", "", topo.cpu_model)) or tr("Unbekannt")
        if topo.dual:
            sizes = " + ".join(f"{c.l3_bytes // 1024 ** 2}" for c in sorted(topo.ccds, key=lambda c: c.id))
            self.t_cpu.set(model, f"{len(topo.ccds)} CCDs · {sizes} MB L3", "ok")
        else:
            self.t_cpu.set(model, tr("{0} CCD(s) · kein Dual-CCD-X3D").format(len(topo.ccds)), "warn")
        if not topo.dual:
            self.notice.set_text(tr("Kein Prozessor mit zwei unterschiedlich großen L3-Caches erkannt (Dual-CCD-X3D wie 7950X3D/7900X3D/"
                                    "9950X3D/9900X3D). Die Zuweisung bleibt hier wirkungslos; Einstellungen können trotzdem vorbereitet werden."))
            self.notice.set_visible(True)
        else:
            self.notice.set_visible(False)
        if not d["has_driver"]:
            self.t_mode.set(tr("Kein Treiber"), tr("amd_3d_vcache fehlt"), "warn")
        else:
            self.t_mode.set(MODE_TITLES.get(self._mode or "", "?"), "amd_x3d_mode · " + (tr("schreibbar") if self._writable else tr("nur lesbar")),
                            "ok" if self._writable else "warn")
        if self._alive:
            sub = tr("{0} Spiel- · {1} Hintergrund-Threads").format(st.get("game_threads", 0), st.get("background_threads", 0))
            if not st.get("enabled", True):
                sub = tr("Erkennung aus – beobachtet nur")
            if st.get("dry_run"):
                sub = tr("Probelauf – ändert nichts")
            self.t_service.set(tr("Läuft"), sub, "ok")
        else:
            self.t_service.set(tr("Gestoppt"), tr("unten einschalten"), "warn" if topo.dual else None)
        if sessions:
            first = sessions[0]
            sub = tr(first["name"])
            if first.get("threads"):
                sub += tr(" · {0}/{1} auf {2}").format(first.get("threads_on_ccd", 0), first["threads"],
                                                       self._ccd_label(first.get("mode", "")))
            if len(sessions) > 1:
                sub += f" · +{len(sessions) - 1}"
            ok = first.get("threads", 0) == 0 or first.get("threads_on_ccd", 0) == first.get("threads", 0) or first.get("mode") == "none"
            self.t_games.set(str(len(sessions)), sub, "ok" if ok else "warn")
        else:
            self.t_games.set("0", "–", None)
        self.win.nav["x3d"].set_badge(str(len(sessions)) if sessions else "", "accent" if sessions else None)
        self._update_ccds(d.get("usage") or {})
        self._render_games(sessions)
        events = st.get("events", [])
        text = "\n".join(f"{time.strftime('%H:%M:%S', time.localtime(ts))}  {msg}" for ts, msg in events)
        if text != self._last_events:
            self._last_events = text
            self.log.set_text(text or tr("Noch keine Einträge – der Dienst protokolliert erkannte Spiele und Umschaltungen."))
        if full:
            self._loading = True
            try:
                self.sw_service.set_active(self._alive)
                self.sw_autostart.set_sensitive(d.get("autostart") is not None)
                self.sw_autostart.set_active(bool(d.get("autostart")))
            finally:
                self._loading = False
        elif self.sw_service.get_active() != self._alive:
            self._loading = True
            try:
                self.sw_service.set_active(self._alive)
            finally:
                self._loading = False

    def _ccd_label(self, mode: str) -> str:
        topo = self.topo
        if mode == "cache" and topo.cache_ccd:
            return f"CCD{topo.cache_ccd.id}"
        if mode == "frequency" and topo.freq_ccd:
            return f"CCD{topo.freq_ccd.id}" if topo.freq_ccd.id >= 0 else tr("Frequenz-CCDs")
        return tr("allen Kernen")

    def _build_ccds(self) -> None:
        """CCD-Karten einmal anlegen; Werte (Auslastung, „bevorzugt“) aktualisiert _update_ccds alle 2 s."""
        _clear(self.ccd_box)
        self.ccd_widgets = {}
        topo = self.topo
        cache = topo.cache_ccd
        for ccd in sorted(topo.ccds, key=lambda c: c.id):
            is_cache = cache is not None and ccd.id == cache.id
            card = Card(spacing=4)
            card.set_hexpand(True)
            head = Gtk.Box(spacing=8)
            head.append(label(f"CCD{ccd.id}", css=["stat-val"]))
            tag = Tag(tr("bevorzugt"), "accent")
            tag.set_visible(False)
            head.append(tag)
            usage = label("", css=["stat-val"], xalign=1.0)
            usage.set_hexpand(True)
            head.append(usage)
            card.append(head)
            role = tr("Cache-CCD · 3D V-Cache") if is_cache else (tr("Frequenz-CCD · höherer Takt") if topo.dual else "CCD")
            card.append(label(role, css=["body-text"]))
            card.append(label(tr("{0} Kerne / {1} Threads · {2} · CPUs {3}").format(ccd.cores or len(ccd.cpus), len(ccd.cpus),
                                                                                    ccd.l3_text, ccd.cpu_list),
                              css=["stat-sub"], selectable=True))
            bars = CpuBars(ccd.cpus, (0.79, 0.51, 0.25) if is_cache else (0.74, 0.76, 0.80))
            bars.set_margin_top(6)
            card.append(bars)
            self.ccd_box.append(card)
            self.ccd_widgets[ccd.id] = {"tag": tag, "usage": usage, "bars": bars, "is_cache": is_cache}

    def _update_ccds(self, usage: dict[int, float]) -> None:
        topo = self.topo
        per_ccd = x3d.ccd_usage(topo, usage) if usage else {}
        for cid, w in self.ccd_widgets.items():
            preferred = (self._mode == "cache" and w["is_cache"]) or (self._mode == "frequency" and topo.dual and not w["is_cache"])
            w["tag"].set_visible(preferred)
            w["bars"].set_values(usage)
            w["usage"].set_text(f"{per_ccd[cid]:.0f} %" if cid in per_ccd else "")
        has_driver = x3d.mode_path() is not None
        for b, mode in ((self.btn_cache, "cache"), (self.btn_freq, "frequency")):
            b.set_sensitive(self._writable and topo.dual)
            b.remove_css_class("btn-primary")
            b.remove_css_class("btn-secondary")
            b.add_css_class("btn-primary" if self._mode == mode else "btn-secondary")
        self.btn_access.set_visible(has_driver and not self._writable)
        if not has_driver:
            hint = tr("Der Kernel-Treiber amd_3d_vcache ist nicht aktiv. Ohne ihn setzt der Dienst nur die Affinität der Spiele; "
                      "die Rangfolge der Kerne (CPPC) bleibt, wie das BIOS sie vorgibt.")
        elif not self._writable:
            hint = tr("Die Rangfolge gehört root. „Zugriff einrichten“ legt per pkexec eine tmpfiles.d-Regel an, die deiner Gruppe "
                      "({0}) Schreibrecht gibt – dauerhaft, ohne Passwortabfrage beim Spielen.").format(x3d.user_group())
        else:
            hint = tr("Manuelles Umschalten gilt bis zum nächsten Spielstart bzw. -ende; dann setzt der Dienst die Rangfolge wieder "
                      "nach seinen Regeln.")
        hint += tr(" Balken: Auslastung je CPU der letzten 2 Sekunden; Linux zählt die SMT-Geschwister getrennt (z. B. 0-7 und 16-23 = CCD0).")
        if self.ccd_hint.get_text() != hint:
            self.ccd_hint.set_text(hint)

    def _render_games(self, sessions: list[dict]) -> None:
        _clear(self.games_list)
        self.games_empty.set_visible(not sessions)
        self.games_list.set_visible(bool(sessions))
        for s in sessions:
            mode = s.get("mode", "")
            since = fmt_time(s["since"]) if s.get("since") else ""
            sub = tr("{0} · {1} · {2} Prozesse, {3} Threads").format(tr(s.get("source_label", "")), tr(s.get("reason", "")),
                                                                      s.get("pids", 0), s.get("threads", 0))
            if since:
                sub += tr(" · seit {0}").format(since)
            cpus = self.topo.cpus_for(mode)
            if cpus:
                sub += f" · CPUs {x3d.format_cpu_list(cpus)}"
            threads = s.get("threads", 0)
            on = s.get("threads_on_ccd", 0)
            placement = s.get("placement") or {}
            if threads:
                where = self._ccd_label(mode)
                sub += "\n" + tr("Threads: {0}/{1} auf {2} gebunden").format(on, threads, where)
                last = ", ".join(f"CCD{k}: {v}" for k, v in sorted(placement.items()) if k != "-1")
                if last:
                    sub += tr(" · zuletzt gelaufen auf {0}").format(last)
            row = Adw.ActionRow(title=GLib.markup_escape_text(tr(s["name"])), subtitle=GLib.markup_escape_text(sub))
            row.set_subtitle_lines(3)
            row.add_prefix(Gtk.Image(icon_name=pick_icon("applications-games-symbolic", "input-gaming-symbolic"), pixel_size=16))
            share = s.get("share")
            if share is not None and mode in ("cache", "frequency"):
                row.add_suffix(Tag(tr("{0:.0f} % CPU-Zeit auf {1}").format(share, self._ccd_label(mode)), "ok" if share >= 95 else "warn"))
            elif threads and mode in ("cache", "frequency"):
                row.add_suffix(Tag(tr("alle Threads gebunden") if on == threads else tr("{0} Threads außerhalb").format(threads - on),
                                   "ok" if on == threads else "warn"))
            row.add_suffix(Tag(MODE_TITLES.get(mode, mode), TAG_STYLE.get(mode)))
            row.add_suffix(self._mode_buttons(s["key"], s["name"], s.get("source", ""), mode, allow_ignore=True))
            self.games_list.append(row)

    def _mode_buttons(self, key: str, name: str, source: str, current: str, allow_ignore: bool = False,
                      has_override: bool = False) -> Gtk.Widget:
        box = Gtk.Box(spacing=2, valign=Gtk.Align.CENTER)
        for mode in MODE_CHOICES:
            b = button(MODE_TITLES[mode], ["btn-secondary" if mode == current else "flat"],
                       on_click=lambda m=mode: self._override(key, m, name, source),
                       tooltip=tr("„{name}“ fest dem {mode} zuweisen").format(name=tr(name), mode=MODE_LONG[mode]))
            box.append(b)
        if has_override:
            box.append(button(tr("Zurücksetzen"), ["flat"], on_click=lambda: self._reset(key), tooltip=tr("Eigene Festlegung entfernen")))
        if allow_ignore:
            box.append(button(tr("Kein Spiel"), ["flat"], on_click=lambda: self._ignore(key, name),
                              tooltip=tr("Dieses Programm nie als Spiel behandeln")))
        return box

    # ------------------------------------------------------------------ Katalogliste
    def _set_search(self, text: str) -> None:
        self._search = text.strip().lower()
        self._render_catalog()

    def _render_catalog(self) -> None:
        _clear(self.cat_list)
        q = self._search
        rows: list[tuple[str, str, str, str, bool, bool]] = []  # name, key, sub, mode, has_override, ignored
        for key, ov in sorted(self.rules.overrides.items(), key=lambda kv: (kv[1].get("name") or kv[0]).lower()):
            rows.append((self.rules.label(key), key, tr("Eigene Festlegung · {0}").format(key), ov["mode"], True, False))
        for key in sorted(self.rules.ignore):
            rows.append((self.rules.label(key), key, tr("Kein Spiel (Ausschluss) · {0}").format(key), "", True, True))
        for e in sorted(self.catalog.games(), key=lambda e: e.name.lower()):
            keys = e.keys()
            key = next((k for k in keys if k.startswith("exe:")), keys[0] if keys else "")
            if not key or key in self.rules.overrides or key in self.rules.ignore:
                continue
            rows.append((e.name, key, tr("Katalog · {0}").format(", ".join(e.exe[:3] + e.steam[:1])), e.mode, False, False))
        if q:
            rows = [r for r in rows if q in tr(r[0]).lower() or q in r[2].lower() or q in r[1].lower()]
        shown = rows[:60]
        for name, key, sub, mode, has_override, ignored in shown:
            row = Adw.ActionRow(title=GLib.markup_escape_text(tr(name)), subtitle=GLib.markup_escape_text(sub))
            row.set_subtitle_lines(1)
            if ignored:
                row.add_suffix(Tag(tr("kein Spiel"), "danger"))
                row.add_suffix(button(tr("Wieder zulassen"), ["flat"], on_click=lambda k=key: self._reset(k)))
            else:
                row.add_suffix(Tag(MODE_TITLES.get(mode, tr("Vorgabe")) if mode else tr("Vorgabe"), TAG_STYLE.get(mode) if mode else None))
                row.add_suffix(self._mode_buttons(key, name, "", mode, has_override=has_override))
            self.cat_list.append(row)
        n_user = len(self.rules.overrides) + len(self.rules.ignore)
        info = tr("{0} Spiele im Katalog, {1} eigene Festlegungen").format(len(self.catalog.games()), n_user)
        if len(rows) > len(shown):
            info += tr(" · {0} weitere Treffer – Suche eingrenzen").format(len(rows) - len(shown))
        info += tr(". Spiele ohne Eintrag bekommen die Vorgabe für unbekannte Spiele; AMDs eigene Liste ist nicht öffentlich.")
        self.cat_info.set_text(info)

    # ------------------------------------------------------------------ Aktionen
    def _cfg(self, key: str, value: str) -> None:
        if self._loading:
            return
        self.cfg.set(key, value)
        self.toast(tr("Gespeichert – der Dienst übernimmt die Einstellung beim nächsten Durchlauf."), 3)

    def _override(self, key: str, mode: str, name: str, source: str) -> None:
        self.rules.set_override(key, mode, name=name, source=source)
        self.toast(tr("„{0}“ → {1}").format(tr(name), MODE_LONG[mode]), 3)
        self._render_catalog()
        self._collect(full=False)

    def _reset(self, key: str) -> None:
        self.rules.clear_override(key)
        self.toast(tr("Festlegung entfernt – es gilt wieder Katalog bzw. Vorgabe."), 3)
        self._render_catalog()

    def _ignore(self, key: str, name: str) -> None:
        self.rules.set_ignore(key, True, name=name)
        self.toast(tr("„{0}“ wird nicht mehr als Spiel behandelt (Zuweisung wird aufgehoben).").format(tr(name)), 4)
        self._render_catalog()

    def _set_mode(self, mode: str) -> None:
        def done(err: str | None) -> None:
            if err:
                self.toast(err, 6)
            else:
                self.toast(tr("Bevorzugte Kerne: {0}").format(MODE_LONG[mode]), 3)
            self._collect(full=False)
        run_async(lambda: x3d.write_mode(mode), done)

    def _setup_access(self) -> None:
        def do() -> None:
            try:
                argv = system.root_argv(x3d.access_setup_shell())
            except RuntimeError as exc:
                self.toast(str(exc))
                return
            self.task.set_visible(True)
            self.task.run(argv, tr("Richte Schreibrecht auf amd_x3d_mode ein (Passwortabfrage über pkexec) …"),
                          on_done=lambda rc: (self._collect(full=False),
                                              self.toast(tr("Zugriff eingerichtet.") if rc == 0
                                                         else tr("Einrichtung fehlgeschlagen – Protokoll prüfen."))))
        confirm(self, tr("Schreibrecht auf die Kern-Rangfolge einrichten?"),
                tr("Legt als root die Datei {0} an (Gruppe {1}, Modus 0664 für amd_x3d_mode) und wendet sie "
                   "sofort an. Danach kann der Dienst ohne Passwort zwischen Cache- und Frequenz-CCD umschalten – wie AMDs Treiber unter Windows.")
                .format(x3d.TMPFILES_CONF, x3d.user_group()),
                tr("Einrichten"), do)

    def _toggle_service(self, row, _p) -> None:
        if self._loading:
            return
        want = row.get_active()

        def done(err: str | None) -> None:
            if err:
                self.toast(tr("Dienst: {0}").format(err), 6)
            else:
                self.toast(tr("Dienst gestartet.") if want else tr("Dienst gestoppt."), 3)
            GLib.timeout_add(1500, lambda: (self._collect(full=False), GLib.SOURCE_REMOVE)[1])
        run_async(x3d.start_service if want else x3d.stop_service, done)

    def _toggle_autostart(self, row, _p) -> None:
        if self._loading:
            return
        want = row.get_active()
        run_async(lambda: x3d.set_autostart(want),
                  lambda err: self.toast(tr("Autostart: {0}").format(err) if err
                                         else (tr("Startet künftig beim Anmelden.") if want else tr("Autostart aus.")), 4))

    def _add_dialog(self) -> None:
        from .pages_wine import FormDialog

        def ok(d: FormDialog) -> None:
            ident = exe.get_text().strip()
            nm = name.get_text().strip()
            if not ident:
                self.toast(tr("Programmdatei oder Steam-AppID angeben."))
                return
            if ident.isdigit():
                key = f"steam:{ident}"
            elif "." in ident:
                key = f"exe:{ident.lower()}"
            else:
                key = f"comm:{ident}"
            mode = MODE_CHOICES[cb.get_selected()]
            d.close()
            self._override(key, mode, nm or x3d.pretty_name(ident), "")

        dlg = FormDialog(tr("Festlegung hinzufügen"), tr("Speichern"), ok, height=420)
        g = group(tr("Programm"), tr("Dateiname der Windows-.exe (z. B. eldenring.exe), Linux-Prozessname oder Steam-AppID."))
        name = Adw.EntryRow(title=tr("Anzeigename (optional)"))
        exe = Adw.EntryRow(title=tr("Programmdatei / Prozessname / Steam-AppID"))
        cb = Adw.ComboRow(title=tr("Zuweisung"))
        cb.set_model(Gtk.StringList.new([MODE_LONG[m] for m in MODE_CHOICES]))
        for w in (name, exe, cb):
            g.add(w)
        dlg.add_group(g)
        dlg.present(self.win)
