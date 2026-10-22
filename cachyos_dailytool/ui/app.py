"""Adw.Application: dunkles Thema, CSS, Aktionen, Fenster, Screenshot-Modus für Tests/Doku."""
from __future__ import annotations

import logging
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from .. import APP_ID, APP_NAME, __version__, i18n, system
from ..i18n import tr
from .util import Adw, Gdk, Gio, GLib, Gtk, confirm, run_async

log = logging.getLogger(__name__)


class ToolkitApp(Adw.Application):
    def __init__(self, cfg, launch_file: str | None = None, screenshot_dir: str | None = None,
                 start_page: str | None = None) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.cfg = cfg
        self.launch_file = launch_file
        self.start_page = start_page
        self.screenshot_dir = screenshot_dir
        self.window = None

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        display = Gdk.Display.get_default()
        css = Gtk.CssProvider()
        css.load_from_path(str(Path(__file__).parent / "style.css"))
        Gtk.StyleContext.add_provider_for_display(display, css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        # Icon auch ohne Installation finden (Quellbaum: data/icons/hicolor/...)
        icons = Path(__file__).resolve().parents[2] / "data" / "icons"
        if icons.is_dir():
            Gtk.IconTheme.get_for_display(display).add_search_path(str(icons))
        for name, cb, accel in (
            ("refresh", lambda *_: self.window and self.window.refresh_current(), "<primary>r"),
            ("preferences", lambda *_: self.window and self.window.open_preferences(), "<primary>comma"),
            ("logs", lambda *_: self._open_logs(), None),
            ("about", lambda *_: self._about(), None),
            ("uninstall", lambda *_: self._uninstall_dialog(), None),
            ("quit", lambda *_: self.quit(), "<primary>q"),
        ):
            a = Gio.SimpleAction.new(name, None)
            a.connect("activate", cb)
            self.add_action(a)
            if accel:
                self.set_accels_for_action(f"app.{name}", [accel])
        # Sprache: Auswahl über das Sprach-Symbol in der Kopfzeile (Menüeinträge „app.language::<code>“)
        cur = self.cfg.get("UI_LANGUAGE", "auto")
        lang = Gio.SimpleAction.new_stateful("language", GLib.VariantType.new("s"),
                                             GLib.Variant.new_string(cur if cur in i18n.LANGUAGES else "auto"))
        lang.connect("change-state", self._on_language)
        self.add_action(lang)

    def do_shutdown(self) -> None:
        if self.window is not None:
            self.window.save_state()
        Adw.Application.do_shutdown(self)

    def do_activate(self) -> None:
        if self.window is None:
            from .window import MainWindow
            self.window = MainWindow(self, self.cfg)
        self.window.present()
        if self.start_page:
            self.window.navigate(self.start_page)
            self.start_page = None
        if self.launch_file:
            self.window.navigate("launch")
            self.window.pages["launch"].set_file(self.launch_file)
            self.launch_file = None
        if self.screenshot_dir:
            GLib.timeout_add(1200, self._start_screenshots)
        control = os.environ.get("CACHYOS_DAILYTOOL_CONTROL")
        if control:
            self._control_path = Path(control)
            # Testmodus: Animationen aus, damit Aufnahmen keine Übergangszustände zeigen
            Gtk.Settings.get_default().set_property("gtk-enable-animations", False)
            # Paintable früh anlegen: er wird erst nach dem nächsten gerenderten Frame befüllt (Steuerbefehl „snap“)
            self._paintable = Gtk.WidgetPaintable.new(self.window)
            GLib.timeout_add(300, self._poll_control)

    # ------------------------------------------------------------------ Sprache: Wechsel mit automatischem Neustart
    def _on_language(self, action: Gio.SimpleAction, value: GLib.Variant) -> None:
        code = value.get_string()
        if code not in i18n.LANGUAGES or code == action.get_state().get_string():
            return
        pages = self.window.pages.values() if self.window is not None else []
        busy = [p for p in pages if getattr(getattr(p, "task", None), "busy", False)]
        if busy:
            confirm(self.window, tr("Sprache wechseln und neu starten?"),
                    tr("Gerade läuft noch eine Aufgabe ({0}). Nach dem Neustart erscheint ihre Ausgabe nicht mehr im Protokoll, "
                       "und ein laufender Befehl kann abbrechen.").format(", ".join(p.title for p in busy)),
                    tr("Neu starten"), lambda: self._apply_language(action, code), destructive=True)
            return
        self._apply_language(action, code)

    def _apply_language(self, action: Gio.SimpleAction, code: str) -> None:
        self.cfg.set("UI_LANGUAGE", code)
        action.set_state(GLib.Variant.new_string(code))
        self.restart()

    def restart(self) -> None:
        """Programm mit derselben Installation neu starten und die aktuelle Seite wieder öffnen."""
        page = self.window.current if self.window is not None else None
        if self.window is not None:
            self.window.save_state()
        argv, env = i18n.restart_command(page)
        try:
            subprocess.Popen(argv, env=env, cwd=str(Path.home()), start_new_session=True,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as exc:
            log.warning("Neustart fehlgeschlagen: %s", exc)
            if self.window is not None:
                self.window.toast(tr("Neustart fehlgeschlagen: {0}").format(exc))
            return
        log.info("Neustart nach Sprachwechsel: %s", " ".join(argv))
        self.quit()

    # ------------------------------------------------------------------ Deinstallation aus der App
    def _uninstall_dialog(self) -> None:
        script = Path(__file__).resolve().parents[1] / "uninstall.sh"
        if not script.is_file():
            self.window.toast(tr("Deinstallationsskript nicht gefunden: {0}").format(script))
            return
        purge = Gtk.CheckButton(label=tr("Einstellungen und Protokolle ebenfalls löschen"))
        dlg = Adw.AlertDialog(
            heading=tr("CachyOS DailyTool deinstallieren?"),
            body=tr("Entfernt das Programm (pacman-Paket und/oder ~/.local), den Menüeintrag, die Icons, die .exe/.msi-Verknüpfung "
                    "und Reste der alten Bash-Version. Wine-Prefixe, Backups und Spiele bleiben erhalten. "
                    "Für ein installiertes Paket fragt pkexec nach dem Passwort."),
            extra_child=purge)
        dlg.add_response("cancel", tr("Abbrechen"))
        dlg.add_response("ok", tr("Deinstallieren"))
        dlg.set_response_appearance("ok", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.set_default_response("cancel")
        dlg.set_close_response("cancel")

        def resp(_d, response: str) -> None:
            if response != "ok":
                return
            args = ["bash", str(script), "--yes", "--quiet"] + (["--purge"] if purge.get_active() else [])
            env = {**os.environ, "CACHY_ROOT_HELPER": "pkexec"}
            run_async(lambda: subprocess.run(args, text=True, capture_output=True, env=env, timeout=600), self._uninstall_done,
                      lambda exc: self._uninstall_done(None, str(exc)))
        dlg.connect("response", resp)
        dlg.present(self.window)

    def _uninstall_done(self, result, error: str = "") -> None:
        if result is None:
            heading, body = tr("Deinstallation fehlgeschlagen"), error
        else:
            out = (result.stdout + "\n" + result.stderr).strip()
            heading = tr("Deinstallation abgeschlossen") if result.returncode == 0 else tr("Deinstallation mit Resten")
            body = out[-1500:] or tr("Keine Ausgabe.")
        dlg = Adw.AlertDialog(heading=heading, body=body)
        dlg.add_response("quit", tr("Programm beenden"))
        dlg.set_default_response("quit")
        dlg.set_close_response("quit")
        dlg.connect("response", lambda *_: self.quit())
        dlg.present(self.window)

    # ------------------------------------------------------------------ Test-Steuerkanal (nur mit CACHYOS_DAILYTOOL_CONTROL=Datei)
    def _poll_control(self) -> bool:
        try:
            if self._control_path.exists():
                text = self._control_path.read_text(encoding="utf-8").strip()
                self._control_path.unlink()
                for line in text.splitlines():
                    parts = line.split()
                    if not parts:
                        continue
                    if parts[0] == "navigate" and len(parts) > 1 and self.window:
                        self.window.navigate(parts[1])
                    elif parts[0] == "refresh" and self.window:
                        self.window.refresh_current()
                    elif parts[0] == "scroll" and self.window and self.window.current:
                        pg = self.window.pages.get(self.window.current)
                        adj = pg.scroll.get_vadjustment() if pg else None
                        if adj is not None:
                            frac = float(parts[1]) if len(parts) > 1 else 1.0
                            adj.set_value(adj.get_lower() + frac * max(adj.get_upper() - adj.get_page_size() - adj.get_lower(), 0))
                    elif parts[0] == "prefs" and self.window:
                        self.window.open_preferences()
                    elif parts[0] == "wizard" and len(parts) > 1 and self.window:
                        from .. import launchers as _l
                        from .pages_launchers import launcher_install_dialog
                        self.window.navigate("launchers")
                        pg = self.window.pages["launchers"]
                        spec = _l.get(parts[1])
                        if spec:
                            launcher_install_dialog(pg, pg.task, spec, pg.prefixes, pg._changed)
                    elif parts[0] == "click-ok":
                        from .pages_wine import FormDialog
                        if FormDialog.last is not None:
                            FormDialog.last.ok.emit("clicked")
                    elif parts[0] == "tour" and self.window:
                        self._tour(int(parts[1]) if len(parts) > 1 else 3000)
                    elif parts[0] == "lang" and len(parts) > 1:
                        self.activate_action("language", GLib.Variant.new_string(parts[1]))
                    elif parts[0] == "snap" and len(parts) > 1 and self.window:
                        self._snapshot(Path(parts[1]), spectacle=False)
                    elif parts[0] == "quit":
                        self.quit()
        except Exception:
            log.exception("Steuerbefehl fehlgeschlagen")
        return GLib.SOURCE_CONTINUE

    # ------------------------------------------------------------------ Hilfen
    def launcher_command(self) -> str:
        """Kommando für .desktop-Einträge (z. B. .exe-Verknüpfung)."""
        exe = shutil.which("cachyos-dailytool")
        if exe:
            return exe
        pkg_parent = Path(__file__).resolve().parents[2]
        return f"env PYTHONPATH={shlex.quote(str(pkg_parent))} {shlex.quote(sys.executable)} -m cachyos_dailytool"

    def _open_logs(self) -> None:
        system.LOG_DIR.mkdir(parents=True, exist_ok=True)
        system.open_path(system.LOG_DIR)

    def _about(self) -> None:
        dlg = Adw.AboutDialog(application_name=APP_NAME, version=__version__, developer_name="CueSystem",
                              application_icon=APP_ID, license_type=Gtk.License.MIT_X11,
                              comments=tr("Alltagswerkzeug für CachyOS: Wine-Prefixe, Launcher-Migration, ProtonPlus, "
                                          "X3D-Kernzuteilung (CCD) für Spiele, Laufwerke per fstab einbinden, GPU-Status, Journal, "
                                          "Shader-Caches, System-Update mit DKMS-Prüfung und Skalierung – in einer Oberfläche."),
                              website="https://github.com/Cue-System90/CachyOS-DailyTool")
        dlg.add_link(tr("Konfiguration"), f"file://{self.cfg.path}")
        dlg.present(self.window)

    def _tour(self, interval_ms: int) -> None:
        keys = list(self.window.page_classes)
        state = {"i": 0}

        def step() -> bool:
            if state["i"] >= len(keys):
                return GLib.SOURCE_REMOVE
            self.window.navigate(keys[state["i"]])
            state["i"] += 1
            return GLib.SOURCE_CONTINUE
        step()
        GLib.timeout_add(interval_ms, step)

    # ------------------------------------------------------------------ Screenshots (Tests/Doku)
    def _start_screenshots(self) -> bool:
        assert self.window is not None
        out = Path(self.screenshot_dir)
        out.mkdir(parents=True, exist_ok=True)
        keys = list(self.window.page_classes)
        state = {"i": 0}
        # Im Hintergrund liegende Fenster bekommen vom Compositor keine Frame-Callbacks → Vollbild erzwingt Sichtbarkeit.
        self.window.fullscreen()
        # Der Paintable wird erst nach dem nächsten gerenderten Frame befüllt – daher früh anlegen.
        self._paintable = Gtk.WidgetPaintable.new(self.window)

        def step() -> bool:
            if state["i"] >= len(keys):
                self.quit()
                return GLib.SOURCE_REMOVE
            key = keys[state["i"]]
            self.window.navigate(key)
            GLib.timeout_add(1400, lambda: (self._snapshot(out / f"{state['i']:02d}-{key}.png"), advance())[1])
            return GLib.SOURCE_REMOVE

        def advance() -> bool:
            state["i"] += 1
            GLib.timeout_add(150, step)
            return GLib.SOURCE_REMOVE

        step()
        return GLib.SOURCE_REMOVE

    def _snapshot(self, path: Path, spectacle: bool = True) -> None:
        # Bevorzugt Spectacle (KDE, auch unter Wayland); GTK-eigener Paintable als Fallback bzw. mit spectacle=False
        if spectacle and shutil.which("spectacle"):
            import subprocess
            r = subprocess.run(["spectacle", "-a", "-b", "-n", "-o", str(path)], capture_output=True, text=True, timeout=30)
            if r.returncode == 0 and path.exists():
                log.info("Screenshot (spectacle): %s", path)
                return
            log.warning("spectacle fehlgeschlagen (%s): %s", r.returncode, r.stderr.strip()[:200])
        try:
            import gi
            gi.require_version("Graphene", "1.0")
            from gi.repository import Graphene
            win = self.window
            w, h = win.get_width(), win.get_height()
            paintable = self._paintable
            snap = Gtk.Snapshot()
            paintable.snapshot(snap, w, h)
            node = snap.to_node()
            if node is None:
                log.warning("Kein Render-Knoten für %s", path)
                return
            renderer = win.get_renderer()
            tex = renderer.render_texture(node, Graphene.Rect().init(0, 0, w, h))
            tex.save_to_png(str(path))
            log.info("Screenshot: %s", path)
        except Exception:
            log.exception("Screenshot fehlgeschlagen")
