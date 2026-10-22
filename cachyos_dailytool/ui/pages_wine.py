"""Wine & Prefixe: Programm starten/installieren und Prefixe verwalten.

Speicherorte frei wählbar (Standardordner änderbar, weitere Orte), Runner je Prefix/Anwendung (Proton über umu,
Proton direkt, Wine), Startoptionen in Steam-Syntax mit GOverlay-Übernahme, Umgebungsvariablen, Umbenennen/
Verschieben/Löschen, Installations-Dialog mit Zielordner-Abfrage und Verknüpfungen für Startmenü/Schreibtisch.
"""
from __future__ import annotations

import os
import re
import shutil
import time
from pathlib import Path

from .. import compat, goverlay, launch_options, launchers, shortcuts, system
from ..i18n import tr
from ..launcher import LaunchError
from ..registry import AppEntry, slugify
from .util import (Adw, Gdk, Gio, GLib, Gtk, button, choose_file, choose_folder, confirm, ask_text, copy_text, fmt_time,
                   icon_button, idle, label, pick_icon, run_async)
from .widgets import Page, PathRow, Tag, TaskRunner, action_row, button_flow, group

ARCHS = [tr("win64 (empfohlen)"), tr("win32 (ältere 32-Bit-Programme)")]
EXE_PATTERNS = [(tr("Windows-Programme (*.exe, *.msi)"), ["*.exe", "*.EXE", "*.msi", "*.MSI"]), (tr("Alle Dateien"), ["*"])]
SKIP_EXE = ("unins", "uninst", "setup", "install", "vcredist", "vc_redist", "dxsetup", "directx", "dotnet", "redist",
            "crash", "report", "updater", "update", "helper", "service", "bsod", "ndp", "oalinst", "7z", "unpack")


# ----------------------------------------------------------------------------- Hilfen
def guess_app_name(path: str) -> str:
    stem = Path(path).stem
    stem = re.sub(r"(?i)[-_ .]*(setup|installer|install|x64|x86|win64|win32|online|offline|web|full|v?\d+(\.\d+)*)$", "", stem)
    stem = re.sub(r"(?i)^(setup|install)[-_ .]*", "", stem)
    stem = re.sub(r"([a-z])([A-Z])", r"\1 \2", stem).replace("_", " ").replace("-", " ").strip()
    return stem or Path(path).stem


def tool_choices(launcher, prefix_default: str | None = None) -> tuple[list[str], list[str]]:
    """(Anzeigenamen, IDs). Erster Eintrag: „Automatisch“ bzw. „Wie Prefix“ (ID '')."""
    tools = launcher.tools()
    auto = launcher.default_tool()
    if prefix_default is not None:
        first, first_id = tr("Wie Prefix ({0})").format(prefix_default), ""
    else:
        first, first_id = (tr("Automatisch ({0})").format(auto.name) if auto else tr("Automatisch (kein Runner gefunden)")), compat.AUTO_ID
    return [first] + [t.label for t in tools], [first_id] + [t.id for t in tools]


def tool_name(launcher, tool_id: str) -> str:
    if not tool_id or tool_id == compat.AUTO_ID:
        t = launcher.default_tool()
        return t.name if t else tr("kein Runner")
    t = compat.resolve(tool_id, launcher.tools())
    return t.name if t else tool_id


def select_id(row: Adw.ComboRow, ids: list[str], wanted: str) -> None:
    row.set_selected(ids.index(wanted) if wanted in ids else 0)


def scan_exes(wine_dir: Path) -> set[str]:
    out: set[str] = set()
    drive_c = wine_dir / "drive_c"
    if not drive_c.is_dir():
        return out
    try:
        for dirpath, dirnames, filenames in os.walk(drive_c):
            rel = Path(dirpath).relative_to(drive_c)
            if rel.parts and rel.parts[0].lower() in ("windows", "programdata"):
                dirnames[:] = []
                continue
            for f in filenames:
                if f.lower().endswith(".exe"):
                    out.add(str(Path(dirpath) / f))
    except OSError:
        pass
    return out


def split_candidates(before: set[str], after: set[str], app_name: str) -> tuple[list[str], list[str]]:
    """(plausible Hauptprogramme, aussortierte wie Uninstaller/Updater/Setup) unter den neu hinzugekommenen .exe."""
    new = sorted(after - before)
    tokens = [t for t in re.split(r"[^a-z0-9]+", app_name.lower()) if len(t) > 2]
    good = [e for e in new if not any(s in Path(e).stem.lower() for s in SKIP_EXE)]
    good.sort(key=lambda e: (-sum(1 for t in tokens if t in Path(e).stem.lower()), len(Path(e).parts), e.lower()))
    rest = [e for e in new if e not in good]
    return good, rest


def candidate_exes(before: set[str], after: set[str], app_name: str) -> list[str]:
    good, rest = split_candidates(before, after, app_name)
    return good + rest


def add_goverlay_buttons(row: Adw.EntryRow, page: Page) -> None:
    """Preset-Auswahl, GOverlay-Übernahme und GOverlay-Start an eine Startoptionen-Zeile hängen."""
    dd = Gtk.DropDown.new_from_strings([tr("Vorlage …")] + [p[0] for p in goverlay.PRESETS])
    dd.set_valign(Gtk.Align.CENTER)
    dd.set_tooltip_text(tr("Vorlage einfügen"))

    def picked(*_):
        i = dd.get_selected()
        if i > 0:
            row.set_text(goverlay.PRESETS[i - 1][1])
            dd.set_selected(0)
    dd.connect("notify::selected", picked)
    row.add_suffix(dd)
    gb = icon_button(pick_icon("view-reveal-symbolic", "edit-paste-symbolic"), tr("Aus GOverlay übernehmen"), ["flat"],
                     on_click=lambda: (row.set_text(goverlay.import_from_goverlay()),
                                       page.toast(tr("Startoptionen aus der GOverlay-/MangoHud-Konfiguration übernommen."))))
    gb.set_valign(Gtk.Align.CENTER)
    row.add_suffix(gb)
    if system.have("goverlay"):
        ob = icon_button(pick_icon("preferences-system-symbolic", "emblem-system-symbolic"), tr("GOverlay öffnen"), ["flat"],
                         on_click=lambda: goverlay.open_goverlay() or page.toast(tr("GOverlay konnte nicht gestartet werden.")))
        ob.set_valign(Gtk.Align.CENTER)
        row.add_suffix(ob)


class LocationRow(Adw.ComboRow):
    """Speicherort für neue Prefixe: Standardordner, weitere bekannte Orte, anderer Ordner."""

    def __init__(self, page: Page, on_change=None) -> None:
        super().__init__(title=tr("Speicherort"))
        self.page = page
        self.on_change = on_change
        self._paths: list[str] = []
        self._refresh()
        self.connect("notify::selected", lambda *_: self.on_change and self.on_change())
        b = icon_button(pick_icon("folder-symbolic"), tr("Anderen Ordner wählen"), ["flat"], on_click=self._pick)
        b.set_valign(Gtk.Align.CENTER)
        self.add_suffix(b)

    def _refresh(self, select: str | None = None) -> None:
        root = str(self.page.cfg.wine_prefix_root)
        self._paths = [root] + [l for l in self.page.win.registry.locations if l != root]
        self.set_model(Gtk.StringList.new([tr("Standardordner · {0}").format(root)] + self._paths[1:]))
        if select and select in self._paths:
            self.set_selected(self._paths.index(select))

    def _pick(self) -> None:
        def picked(p: str) -> None:
            self.page.win.registry.add_location(p)
            self._refresh(select=p)
            if self.on_change:
                self.on_change()
        choose_folder(self.page.win, tr("Speicherort wählen"), self.get_path(), picked)

    def get_path(self) -> str:
        i = self.get_selected()
        return self._paths[i] if 0 <= i < len(self._paths) else self._paths[0]


class FormDialog(Adw.Dialog):
    last: "FormDialog | None" = None     # zuletzt geöffneter Dialog (für den Test-Steuerkanal)

    def __init__(self, title: str, ok_label: str, on_ok, width: int = 640, height: int = 640) -> None:
        super().__init__(title=title, content_width=width, content_height=height)
        FormDialog.last = self
        tv = Adw.ToolbarView()
        tv.add_top_bar(Adw.HeaderBar())
        self.page = Adw.PreferencesPage()
        tv.set_content(self.page)
        bar = Gtk.Box(spacing=8, halign=Gtk.Align.END, margin_top=10, margin_bottom=14, margin_start=14, margin_end=14)
        bar.append(button(tr("Abbrechen"), ["btn-secondary"], on_click=self.close))
        self.ok = button(ok_label, ["btn-primary"], on_click=lambda: on_ok(self))
        bar.append(self.ok)
        tv.add_bottom_bar(bar)
        self.set_child(tv)

    def add_group(self, g: Adw.PreferencesGroup) -> None:
        self.page.add(g)


# ----------------------------------------------------------------------------- Gemeinsame Abläufe
WAYLAND_MODES = ("", "on", "off")
WAYLAND_LABELS = {"": "", "on": tr("an"), "off": tr("aus (XWayland)")}
WAYLAND_SUBTITLE = tr("Behebt Vollbild-Probleme wie weiße Ränder unter XWayland")


def wayland_row(inherit_label: str, mode: str) -> Adw.ComboRow:
    """Auswahl Proton-Wayland-Treiber: erben / An / Aus (XWayland)."""
    row = Adw.ComboRow(title=tr("Wayland-Treiber von Proton"), subtitle=WAYLAND_SUBTITLE)
    row.set_model(Gtk.StringList.new([inherit_label, tr("An"), tr("Aus (XWayland)")]))
    row.set_selected(WAYLAND_MODES.index(mode) if mode in WAYLAND_MODES else 0)
    return row


CCD_MODES = ("", "cache", "frequency", "none")


def ccd_row(mode: str) -> Adw.ComboRow | None:
    """X3D-Kernzuteilung einer Anwendung – nur auf Dual-CCD-X3D-Prozessoren angeboten."""
    from .. import x3d
    if not x3d.topology().dual:
        return None
    row = Adw.ComboRow(title=tr("X3D-Kernzuteilung (CCD)"),
                       subtitle=tr("Fest beim Start (taskset) und für den Dienst; „Automatisch“ = Erkennung mit Vorgabe"))
    row.set_model(Gtk.StringList.new([tr("Automatisch (Erkennung)"), tr("Cache-CCD (3D V-Cache)"), tr("Frequenz-CCD"), tr("Keine Zuweisung")]))
    row.set_selected(CCD_MODES.index(mode) if mode in CCD_MODES else 0)
    return row


def register_app(win, *, name: str, exe: str, prefix: str | Path, launcher_id: str = "", launch_options_text: str = "",
                 args: str = "", env: dict[str, str] | None = None, tool: str = "", menu: bool = True, desktop: bool = False,
                 keep_existing: bool = False) -> AppEntry:
    """Anwendung eintragen (oder vorhandenen Eintrag desselben Launchers/derselben .exe im Prefix aktualisieren),
    Icon aus der .exe lesen und Verknüpfungen anlegen. keep_existing: Startoptionen, Argumente, Variablen und Runner
    eines vorhandenen Eintrags nicht überschreiben. Blockiert kurz (Icon) – aus einem Hintergrund-Thread aufrufen."""
    R = win.registry
    entry = R.find_app(prefix, launcher=launcher_id, exe=exe)
    existed = entry is not None
    if entry is None:
        entry = AppEntry(id=R.new_app_id(name), name=name, exe=str(exe), prefix=str(prefix))
    entry.exe, entry.prefix = str(exe), str(prefix)
    entry.launcher = launcher_id or entry.launcher
    if not (existed and keep_existing):
        entry.name = name
        entry.launch_options = launch_options_text
        entry.args = args
        entry.env = dict(env or {})
        entry.tool = tool
        entry.menu, entry.desktop = menu, desktop
    else:
        entry.menu, entry.desktop = entry.menu or menu, entry.desktop or desktop
    if not entry.icon or not Path(entry.icon).exists():
        entry.icon = shortcuts.extract_icon(entry.exe, entry.id) or ""
    shortcuts.create(entry, menu, desktop)
    return R.add_app(entry)


def toast_with_start(page: Page, app: AppEntry, text: str) -> None:
    t = Adw.Toast(title=text, timeout=8, button_label=tr("Starten"))

    def start(*_: object) -> None:
        try:
            plan = page.win.launcher.app_plan(app)
            page.win.launcher.launch(plan, f"app-{app.id}")
            page.toast(tr("„{0}“ gestartet über {1}.").format(app.name, plan.description))
        except (LaunchError, launch_options.LaunchOptionsError, OSError) as exc:
            page.toast(tr("Start fehlgeschlagen: {0}").format(exc))
    t.connect("button-clicked", start)
    page.win.toasts.add_toast(t)


BUSY_TEXT = tr("Es läuft noch ein Vorgang (z. B. ein nach der Installation geöffneter Launcher). "
               "Bitte erst beenden oder im Protokoll auf „Abbrechen“ klicken.")


def protected_locations(win) -> list[Path]:
    return [win.cfg.wine_prefix_root, win.cfg.backup_dir, win.cfg.games_target] + [Path(l) for l in win.registry.locations]


def match_programs(entries: list[tuple[str, str]], names: list[str]) -> list[tuple[str, str]]:
    wanted = [n.lower() for n in names if n]
    return [e for e in entries if any(w in e[1].lower() for w in wanted)]


def programs_dialog(page: Page, task: TaskRunner, prefix: str | Path, title: str = "", preferred: list[str] | None = None,
                    on_removed=None) -> None:
    """Liste der in der Windows-Programmliste des Prefix eingetragenen Programme mit Deinstallieren-Knopf je Eintrag."""
    win = page.win
    dlg = Adw.Dialog(title=title or tr("Windows-Programme in „{0}“").format(Path(prefix).name), content_width=620, content_height=520)
    tv = Adw.ToolbarView()
    tv.add_top_bar(Adw.HeaderBar())
    pg = Adw.PreferencesPage()
    grp = group(tr("Installierte Programme"), tr("Eintrag wählen – die Windows-Deinstallation des Programms startet im Prefix."))
    status = label(tr("Wird gelesen …"), css=["hint"])
    grp.add(status)
    pg.add(grp)
    tv.set_content(pg)
    dlg.set_child(tv)
    dlg.present(page)

    def fill(entries: list[tuple[str, str]]) -> None:
        grp.remove(status)
        if not entries:
            grp.add(label(tr("Keine Einträge in der Windows-Programmliste."), css=["hint"]))
            return
        pref = match_programs(entries, preferred or [])
        ordered = pref + [e for e in entries if e not in pref]
        for key, name in ordered:
            row = action_row(name, key, pick_icon("application-x-executable-symbolic"))
            if (key, name) in pref:
                row.add_suffix(Tag(tr("PASST"), "accent"))
            b = button(tr("Deinstallieren"), ["btn-danger"], on_click=lambda k=key, n=name: run(k, n))
            b.set_valign(Gtk.Align.CENTER)
            row.add_suffix(b)
            grp.add(row)

    def run(key: str, name: str) -> None:
        if task.busy:
            page.toast(BUSY_TEXT, 8)
            return
        dlg.close()
        try:
            steps = win.launcher.uninstall_steps(prefix, key)
        except LaunchError as exc:
            page.toast(str(exc))
            return
        task.run_steps(steps, tr("Deinstalliere „{0}“ – ggf. im Fenster des Deinstallationsprogramms bestätigen …").format(name),
                       on_done=lambda rc: on_removed(key, name, rc) if on_removed else page.toast(tr("Deinstallation von „{0}“ beendet.").format(name)))

    run_async(lambda: win.launcher.installed_programs(prefix), fill,
              lambda exc: (grp.remove(status), grp.add(label(tr("Konnte die Liste nicht lesen: {0}").format(exc), css=["hint"]))))


def uninstall_app_dialog(page: Page, task: TaskRunner, app: AppEntry, on_done) -> None:
    """Deinstallation in drei Stufen: Programm im Prefix (Vorauswahl), ganzer Prefix, nur Verknüpfung."""
    win = page.win
    R = win.registry
    if task.busy:
        page.toast(BUSY_TEXT, 8)
        return
    prefix = Path(app.prefix)
    others = [a for a in R.apps_for_prefix(prefix) if a.id != app.id]
    spec = launchers.get(app.launcher) if app.launcher else launchers.match_exe(app.exe)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    opt_program = Gtk.CheckButton(label=tr("Programm per Windows-Deinstallation entfernen, Prefix behalten (empfohlen)"))
    opt_prefix = Gtk.CheckButton(label=tr("Vollständig: Prefix „{0}“ samt allen Dateien löschen – auch darin installierte Spiele").format(prefix.name),
                                 group=opt_program)
    opt_link = Gtk.CheckButton(label=tr("Nur Verknüpfungen und Eintrag entfernen (Dateien bleiben)"), group=opt_program)
    for w in (opt_program, opt_prefix, opt_link):
        box.append(w)
    opt_program.set_active(True)
    if others:
        box.append(label(tr("Im selben Prefix liegen außerdem: {0} – beim Löschen des Prefix wären sie ebenfalls weg.")
                         .format(", ".join(a.name for a in others)), css=["hint"], wrap=True))
    box.append(label(tr("Prefix: {0}").format(prefix), css=["hint"], wrap=True))
    dlg = Adw.AlertDialog(heading=tr("„{0}“ deinstallieren?").format(app.name),
                          body=tr("Verknüpfungen im Startmenü und auf dem Schreibtisch werden in jedem Fall entfernt."), extra_child=box)
    dlg.add_response("cancel", tr("Abbrechen"))
    dlg.add_response("ok", tr("Deinstallieren"))
    dlg.set_response_appearance("ok", Adw.ResponseAppearance.DESTRUCTIVE)
    dlg.set_default_response("cancel")
    dlg.set_close_response("cancel")

    def drop_entry() -> None:
        shortcuts.remove(app.id)
        R.remove_app(app.id)

    def resp(_d, response: str) -> None:
        if response != "ok":
            return
        if task.busy:
            page.toast(BUSY_TEXT, 8)
            return
        if opt_link.get_active():
            run_async(drop_entry, lambda _r: (page.toast(tr("Verknüpfungen von „{0}“ entfernt – die Dateien im Prefix bleiben.").format(app.name)), on_done()),
                      lambda exc: page.toast(tr("Fehler: {0}").format(exc)))
        elif opt_prefix.get_active():
            err = system.safe_to_delete_prefix(prefix, protected_locations(win)) if prefix.exists() else None
            if err:
                page.toast(err, 8)
                return

            def work(log) -> int:
                system.kill_prefix_processes(prefix, log)
                if prefix.exists():
                    log(tr("Lösche {0} …").format(prefix))
                    shutil.rmtree(prefix)
                removed = R.forget_prefix(prefix)
                for a in removed:
                    shortcuts.remove(a.id)
                    log(tr("Verknüpfungen von „{0}“ entfernt").format(a.name))
                if app.id in R.apps:
                    drop_entry()
                return len(removed)
            task.run_callable(work, tr("Entferne „{0}“ samt Prefix …").format(app.name),
                              on_done=lambda _n: (page.toast(tr("„{0}“ vollständig entfernt (Prefix gelöscht).").format(app.name)), win.refresh_badges(), on_done()),
                              on_error=lambda exc: on_done())
        else:
            names = list(spec.uninstall_match) if spec else []
            names.append(app.name)

            def removed(_key: str, prog: str, _rc: int) -> None:
                def check() -> bool:
                    # manche Deinstaller räumen kurz nach dem Prozessende auf – bis zu 15 s nachsehen
                    for _ in range(30):
                        if not Path(app.exe).exists():
                            return True
                        time.sleep(0.5)
                    return False

                def checked(gone: bool) -> None:
                    if not gone:
                        page.toast(tr("„{0}“ ist noch vorhanden – Deinstallation abgebrochen? Die Verknüpfung bleibt.").format(app.name), 8)
                        on_done()
                        return
                    run_async(drop_entry, lambda _r: (page.toast(tr("„{0}“ deinstalliert, Verknüpfungen entfernt. Der Prefix bleibt erhalten.").format(prog)),
                                                      on_done()))
                run_async(check, checked)

            def listed(entries: list[tuple[str, str]]) -> None:
                hits = match_programs(entries, names)
                if len(hits) == 1:
                    key, prog = hits[0]
                    try:
                        steps = win.launcher.uninstall_steps(prefix, key)
                    except LaunchError as exc:
                        page.toast(str(exc))
                        return
                    task.run_steps(steps, tr("Deinstalliere „{0}“ – ggf. im Fenster des Deinstallationsprogramms bestätigen …").format(prog),
                                   on_done=lambda rc: removed(key, prog, rc))
                else:
                    programs_dialog(page, task, prefix, tr("Welches Programm gehört zu „{0}“?").format(app.name), names, removed)
            run_async(lambda: win.launcher.installed_programs(prefix), listed, lambda exc: page.toast(tr("Fehler: {0}").format(exc), 8))
    dlg.connect("response", resp)
    dlg.present(page)


# ----------------------------------------------------------------------------- Dialog: neuer Prefix
def new_prefix_dialog(page: Page, task: TaskRunner, on_created, default_name: str = "") -> None:
    win = page.win
    L, R = win.launcher, win.registry
    if not L.tools():
        page.toast(tr("Kein Proton/Wine gefunden – bitte proton-cachyos, umu-launcher oder wine installieren."))
        return
    names, ids = tool_choices(L)

    def ok(d: FormDialog) -> None:
        n = name.get_text().strip().replace("/", "-")
        err = system.prefix_name_error(n)
        if err:
            page.toast(err)
            return
        target = Path(loc.get_path()) / n
        if system.is_prefix(target):
            page.toast(tr("{0} ist bereits ein Prefix.").format(target))
            return
        tool_id = ids[runner.get_selected()]
        tool = compat.resolve(tool_id, L.tools()) if tool_id != compat.AUTO_ID else None
        a = "win32" if arch.get_selected() == 1 else "win64"
        if tool is None and L.default_tool() is None:
            page.toast(tr("Kein Proton/Wine gefunden – bitte proton-cachyos, umu-launcher oder wine installieren."))
            return
        holder: dict = {"tool": tool, "tool_id": tool_id}
        steps = [L.ensure_runner_step(holder), L.runtime_create_step(target, holder, a)]
        d.close()
        opts_text = opts.get_text().strip()
        external = str(target.parent) != str(page.cfg.wine_prefix_root)

        def done(rc: int) -> None:
            if not system.is_prefix(target):
                page.toast(tr("Prefix-Erstellung fehlgeschlagen – siehe Protokoll."))
                return
            R.set_prefix_settings(target, tool=holder["tool_id"], launch_options=opts_text, arch=a, registered=external)
            if holder.get("fallback"):
                page.toast(tr("Hinweis: {0} – Prefix mit {1} angelegt.").format(holder['fallback'], tool_name(L, holder['tool_id'])), 10)
            on_created(target)
        task.run_steps(steps, tr("Erstelle Prefix „{0}“ mit {1} …").format(n, tool_name(L, tool_id)), on_done=done)

    dlg = FormDialog(tr("Neuen Prefix anlegen"), tr("Anlegen"), ok, height=560)
    g = group(tr("Prefix"))
    name = Adw.EntryRow(title=tr("Name"), text=default_name)
    g.add(name)
    loc = LocationRow(page)
    g.add(loc)
    result = action_row(tr("Wird angelegt unter"), "–", pick_icon("folder-new-symbolic"))
    g.add(result)
    dlg.add_group(g)
    g2 = group(tr("Runner & Startoptionen"))
    runner = Adw.ComboRow(title=tr("Runner (Proton/Wine)"))
    runner.set_model(Gtk.StringList.new(names))
    g2.add(runner)
    arch = Adw.ComboRow(title=tr("Architektur"), subtitle=tr("nur bei System-Wine relevant; Proton ist immer 64-Bit"))
    arch.set_model(Gtk.StringList.new(ARCHS))
    g2.add(arch)
    opts = Adw.EntryRow(title=tr("Startoptionen für alle Programme im Prefix (Steam-Syntax)"))
    add_goverlay_buttons(opts, page)
    g2.add(opts)
    dlg.add_group(g2)

    def update(*_: object) -> None:
        n = name.get_text().strip().replace("/", "-")
        result.set_subtitle(f"{loc.get_path()}/{n}" if n else "–")
    name.connect("changed", update)
    loc.on_change = update
    update()
    dlg.present(page)
    name.grab_focus()


# ----------------------------------------------------------------------------- Dialog: Installation
def install_dialog(page: Page, task: TaskRunner, installer: str, prefixes: list[system.Prefix], on_finished) -> None:
    win = page.win
    L, R = win.launcher, win.registry
    if not L.tools():
        page.toast(tr("Kein Proton/Wine gefunden – bitte proton-cachyos, umu-launcher oder wine installieren."))
        return
    names, ids = tool_choices(L)
    spec = launchers.spec_for_installer(installer)
    app_default = spec.name if spec else guess_app_name(installer)

    def ok(d: FormDialog) -> None:
        app_name = name.get_text().strip() or app_default
        i = existing.get_selected()
        tool_id = ids[runner.get_selected()]
        a = "win32" if arch.get_selected() == 1 else "win64"
        tool = compat.resolve(tool_id, L.tools()) if tool_id != compat.AUTO_ID else None
        holder: dict = {"tool": tool, "tool_id": tool_id}
        if i > 0:
            prefix = prefixes[i - 1].path
            create_steps: list = [L.ensure_runner_step(holder)]
        else:
            pn = pname.get_text().strip().replace("/", "-") or slugify(app_name)
            err = system.prefix_name_error(pn)
            if err:
                page.toast(err)
                return
            prefix = Path(loc.get_path()) / pn
            create_steps = [L.ensure_runner_step(holder)] + ([] if system.is_prefix(prefix) else [L.runtime_create_step(prefix, holder, a)])
        opts_text = opts.get_text().strip()
        app_env = spec.env_dict if spec else {}
        try:
            # Der Installer selbst läuft ohne Wrapper (mangohud & Co. gelten erst für das installierte Programm)
            plan_args = (list(spec.install_args) if spec else []) + \
                (list(spec.silent_args) if (spec and silent is not None and silent.get_active()) else [])
            plan = L.build_plan(prefix, installer, [], extra_env=app_env, tool=tool, args=plan_args)
        except LaunchError as exc:
            page.toast(str(exc))
            return
        d.close()
        before = scan_exes(L.wine_prefix_dir(prefix)) if system.is_prefix(prefix) else set()
        want_menu, want_desktop = menu.get_active(), desktop.get_active()
        newly = len(create_steps) > 1
        external = str(prefix.parent) != str(page.cfg.wine_prefix_root)
        # Bei einem vorhandenen Prefix gilt der gewählte Runner (falls nicht „Automatisch“) für die Anwendung selbst
        app_tool_fn = lambda: holder["tool_id"] if (not newly and holder["tool_id"] != compat.AUTO_ID) else ""  # noqa: E731
        state: dict = {"app": None}

        def early_register(s2, exe: str) -> None:
            if state["app"] is not None:
                return
            state["app"] = "pending"
            run_async(lambda: register_app(win, name=s2.name, exe=exe, prefix=prefix, launcher_id=s2.id, launch_options_text=opts_text,
                                           args=s2.launch_args, env=s2.env_dict, tool=app_tool_fn(), menu=want_menu, desktop=want_desktop),
                      lambda app: (state.__setitem__("app", app), task.log.append(tr("Verknüpfung angelegt: {0} → {1}").format(app.name, app.exe)),
                                   toast_with_start(page, app, tr("„{0}“ eingerichtet – Verknüpfungen angelegt.").format(app.name)), on_finished()),
                      lambda exc: (state.__setitem__("app", "failed"), page.toast(tr("Verknüpfung fehlgeschlagen: {0}").format(exc), 8)))

        def poll() -> bool:
            # Erkannte Launcher öffnen sich nach der Installation oft selbst und halten den Vorgang offen
            if spec and state["app"] is None and system.is_prefix(prefix):
                exe = launchers.find_exe(spec, L.wine_prefix_dir(prefix))
                if exe and str(exe) not in before:
                    early_register(spec, str(exe))
            return GLib.SOURCE_CONTINUE if task.busy else GLib.SOURCE_REMOVE

        def after(rc: int) -> None:
            if newly and system.is_prefix(prefix):
                R.set_prefix_settings(prefix, tool=holder["tool_id"], arch=a, registered=external)
            if holder.get("fallback"):
                page.toast(tr("Hinweis: {0} – installiert mit {1}.").format(holder['fallback'], tool_name(L, holder['tool_id'])), 10)
                if external:
                    R.add_location(str(prefix.parent))
            if not system.is_prefix(prefix):
                page.toast(tr("Prefix konnte nicht angelegt werden – siehe Protokoll."))
                return
            if state["app"] is not None:
                if state["app"] == "failed":
                    on_finished()
                return
            if rc == 130:
                page.toast(tr("Installation abgebrochen."))
                on_finished()
                return
            if rc != 0:
                page.toast(tr("Installer endete mit Code {0} – Programmsuche läuft trotzdem.").format(rc))
            wdir = L.wine_prefix_dir(prefix)
            good, rest = split_candidates(before, scan_exes(wdir), app_name)
            cands = good + rest

            def find() -> tuple:
                # 1. bekannter Launcher (sicher), 2. eine der neuen .exe gehört zu einem bekannten Launcher,
                # 3. wahrscheinlichstes neues Programm – nur aus den plausiblen (keine Uninstaller/Updater/Setups)
                if spec:
                    exe = launchers.find_exe(spec, wdir, search=True)
                    if exe:
                        return spec, str(exe)
                for c in cands:
                    s2 = launchers.match_exe(c)
                    if s2:
                        return s2, c
                return None, (good[0] if good else "")

            def found(res: tuple) -> None:
                s2, exe = res
                if not exe:
                    choose_exe_dialog(page, cands, app_name, prefix, opts_text, want_menu, want_desktop, on_finished, tool=app_tool_fn())
                    return
                nm = s2.name if s2 else app_name
                env2 = s2.env_dict if s2 else {}
                run_async(lambda: register_app(win, name=nm, exe=exe, prefix=prefix, launcher_id=s2.id if s2 else "",
                                               launch_options_text=opts_text, args=s2.launch_args if s2 else "", env=env2,
                                               tool=app_tool_fn(), menu=want_menu, desktop=want_desktop),
                          lambda app: (toast_with_start(page, app, (tr("„{0}“ eingerichtet – Programm {1}, Verknüpfungen angelegt.")
                                                                    if (want_menu or want_desktop) else tr("„{0}“ eingerichtet – Programm {1}."))
                                                        .format(app.name, Path(app.exe).name)
                                                        + ("" if s2 else tr(" Falls falsch: unter „Prefixe verwalten“ ändern."))),
                                       on_finished()),
                          lambda exc: (page.toast(tr("Fehler beim Anlegen der Verknüpfung: {0}").format(exc)), on_finished()))
            run_async(find, found)

        def settings_step(log, ctx) -> int:
            if spec and system.is_prefix(prefix):
                for f in launchers.write_post_install(spec, L.wine_prefix_dir(prefix)):
                    log(tr("Empfohlene Einstellungen angelegt: {0}").format(f))
            return 0
        def install_step(log, ctx) -> int:
            p2 = plan if holder["tool"] is tool else L.build_plan(prefix, installer, [], extra_env=app_env, tool=holder["tool"],
                                                                  args=plan_args)
            log("$ " + p2.shell_preview())
            if spec and spec.install_notes:
                log(tr("Hinweis: {0}").format(spec.install_notes))
            rc = system.stream(p2.argv, log, env=p2.env, cwd=p2.cwd, on_start=ctx.on_start)
            if ctx.cancelled():
                return 130
            if rc != 0:
                log(tr("Installer endete mit Code {0} – das Ergebnis wird trotzdem geprüft.").format(rc))
            return 0
        steps = create_steps + [settings_step, install_step]
        task.run_steps(steps, tr("Installiere „{0}“ in {1} ({2}) …").format(app_name, prefix, plan.description), on_done=after)
        GLib.timeout_add_seconds(3, poll)

    dlg = FormDialog(tr("Programm installieren"), tr("Installieren"), ok, height=720)
    g = group(tr("Installation"), tr("Erkannt: {0} – Installation, Programmpfad und Verknüpfung laufen automatisch.").format(spec.name) if spec else None)
    g.add(action_row(Path(installer).name, installer, pick_icon("application-x-executable-symbolic")))
    name = Adw.EntryRow(title=tr("Name der Anwendung"), text=app_default)
    g.add(name)
    silent = None
    if spec and spec.silent_args:
        silent = Adw.SwitchRow(title=tr("Unbeaufsichtigt installieren"), subtitle=tr("Installer-Argumente: {0}").format(" ".join(spec.silent_args)),
                               active=spec.silent_default)
        g.add(silent)
    dlg.add_group(g)

    g2 = group(tr("Zielordner"), tr("Jede Installation bekommt ihren eigenen Prefix; der Ordner wird bei Bedarf angelegt."))
    existing = Adw.ComboRow(title=tr("Prefix"))
    existing.set_model(Gtk.StringList.new([tr("Neuen Prefix anlegen")] + [f"{p.name}  ·  {p.path}" for p in prefixes]))
    g2.add(existing)
    loc = LocationRow(page)
    g2.add(loc)
    pname = Adw.EntryRow(title=tr("Prefix-Name"), text=slugify(app_default))
    g2.add(pname)
    result = action_row(tr("Installationsziel"), "–", pick_icon("folder-new-symbolic"))
    g2.add(result)
    dlg.add_group(g2)

    g3 = group(tr("Runner & Startoptionen"))
    runner = Adw.ComboRow(title=tr("Runner (Proton/Wine)"))
    runner.set_model(Gtk.StringList.new(names))
    g3.add(runner)
    arch = Adw.ComboRow(title=tr("Architektur"), subtitle=tr("nur bei System-Wine relevant"))
    arch.set_model(Gtk.StringList.new(ARCHS))
    g3.add(arch)
    opts = Adw.EntryRow(title=tr("Startoptionen für die Anwendung (Steam-Syntax)"))
    add_goverlay_buttons(opts, page)
    g3.add(opts)
    dlg.add_group(g3)

    g4 = group(tr("Nach der Installation"), tr("Das Hauptprogramm wird automatisch erkannt und verknüpft – bekannte Launcher sicher, "
                                               "sonst das wahrscheinlichste neue Programm (änderbar unter „Prefixe verwalten“)."))
    menu = Adw.SwitchRow(title=tr("Verknüpfung im Startmenü anlegen"), active=True)
    g4.add(menu)
    desktop = Adw.SwitchRow(title=tr("Verknüpfung auf dem Schreibtisch anlegen"), active=bool(spec))
    g4.add(desktop)
    dlg.add_group(g4)

    def update(*_: object) -> None:
        i = existing.get_selected()
        new = i == 0
        for w in (loc, pname, arch):
            w.set_sensitive(new)
        if new:
            pn = pname.get_text().strip().replace("/", "-") or slugify(name.get_text() or app_default)
            result.set_subtitle(f"{loc.get_path()}/{pn}")
        else:
            p = prefixes[i - 1]
            result.set_subtitle(tr("{0} (vorhanden, {1}, {2})").format(p.path, p.arch, tool_name(L, R.prefix_settings(p.path).tool)))
    name.connect("changed", lambda *_: (pname.set_text(slugify(name.get_text())) if existing.get_selected() == 0 else None, update()))
    pname.connect("changed", update)
    existing.connect("notify::selected", update)
    loc.on_change = update
    update()
    dlg.present(page)


def choose_exe_dialog(page: Page, cands: list[str], app_name: str, prefix: Path, opts_text: str, want_menu: bool,
                      want_desktop: bool, on_finished, *, env: dict[str, str] | None = None, args: str = "",
                      launcher_id: str = "", tool: str = "") -> None:
    win = page.win
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    labels = [f"{Path(c).name}  ·  {Path(c).relative_to(win.launcher.wine_prefix_dir(prefix) / 'drive_c') if str(c).startswith(str(win.launcher.wine_prefix_dir(prefix))) else c}" for c in cands]
    dd = Gtk.DropDown.new_from_strings(labels + [tr("Andere Datei wählen …")])
    box.append(dd)
    chosen = {"path": cands[0] if cands else ""}
    hint = label(tr("Keine neuen Programme gefunden – bitte Datei wählen.") if not cands else
                 tr("{0} neue Programme gefunden; das wahrscheinlichste ist vorausgewählt.").format(len(cands)), css=["hint"], wrap=True)
    box.append(hint)
    dlg = Adw.AlertDialog(heading=tr("Hauptprogramm für „{0}“").format(app_name),
                          body=tr("Dieses Programm wird in Verknüpfungen und auf der Prefix-Seite verwendet."), extra_child=box)
    dlg.add_response("later", tr("Später festlegen"))
    dlg.add_response("ok", tr("Übernehmen"))
    dlg.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
    dlg.set_default_response("ok")
    dlg.set_close_response("later")

    def on_dd(*_: object) -> None:
        i = dd.get_selected()
        if i == len(cands):
            def picked(p: str) -> None:
                chosen["path"] = p
                hint.set_text(p)
            choose_file(win, tr("Hauptprogramm wählen"), str(win.launcher.wine_prefix_dir(prefix) / "drive_c"), picked, EXE_PATTERNS)
        elif 0 <= i < len(cands):
            chosen["path"] = cands[i]
    dd.connect("notify::selected", on_dd)
    if not cands:
        dd.set_selected(0)
        on_dd()

    def resp(_d, response: str) -> None:
        if response != "ok" or not chosen["path"]:
            page.toast(tr("Installation abgeschlossen – Verknüpfung kann später über „Prefixe verwalten“ angelegt werden."), 6)
            on_finished()
            return
        exe = chosen["path"]
        s2 = launchers.get(launcher_id) if launcher_id else launchers.match_exe(exe)
        run_async(lambda: register_app(win, name=app_name, exe=exe, prefix=prefix, launcher_id=s2.id if s2 else "",
                                       launch_options_text=opts_text, args=args or (s2.launch_args if s2 else ""),
                                       env=env if env is not None else (s2.env_dict if s2 else {}), tool=tool,
                                       menu=want_menu, desktop=want_desktop),
                  lambda a: (toast_with_start(page, a, (tr("„{0}“ eingerichtet – Verknüpfungen angelegt.") if (want_menu or want_desktop)
                                                else tr("„{0}“ eingerichtet.")).format(a.name)),
                             on_finished()),
                  lambda exc: (page.toast(tr("Fehler: {0}").format(exc)), on_finished()))
    dlg.connect("response", resp)
    dlg.present(page)


# ----------------------------------------------------------------------------- Dialog: Verknüpfung / Anwendung
def app_dialog(page: Page, prefix: system.Prefix, app: AppEntry | None, on_saved, preset: dict | None = None) -> None:
    win = page.win
    L, R = win.launcher, win.registry
    ps = R.prefix_settings(prefix.path)
    names, ids = tool_choices(L, prefix_default=tool_name(L, ps.tool))
    preset = preset or {}
    state = {"exe": app.exe if app else preset.get("exe", ""), "launcher": app.launcher if app else preset.get("launcher", "")}

    def ok(d: FormDialog) -> None:
        n = name.get_text().strip()
        if not n or not state["exe"]:
            page.toast(tr("Name und Programm sind erforderlich."))
            return
        tool_id = ids[runner.get_selected()]
        wl_mode = WAYLAND_MODES[wl.get_selected()]
        ccd_mode = CCD_MODES[ccd.get_selected()] if ccd is not None else (app.ccd if app else "")
        want_menu, want_desktop = menu.get_active(), desktop.get_active()
        d.close()

        def work() -> AppEntry:
            if app is None:
                entry = R.find_app(prefix.path, launcher=state["launcher"], exe=state["exe"]) or \
                    AppEntry(id=R.new_app_id(n), name=n, exe=state["exe"], prefix=str(prefix.path))
                entry.name, entry.exe = n, state["exe"]
            else:
                entry = app
                entry.name, entry.exe = n, state["exe"]
            entry.launcher = state["launcher"]
            entry.args = args.get_text().strip()
            entry.launch_options = opts.get_text().strip()
            entry.env = launch_options.parse_env_line(envrow.get_text())
            entry.tool = tool_id
            entry.wayland_driver = wl_mode
            entry.ccd = ccd_mode
            entry.menu, entry.desktop = want_menu, want_desktop
            if not entry.icon or not Path(entry.icon).exists():
                entry.icon = shortcuts.extract_icon(entry.exe, entry.id) or ""
            shortcuts.create(entry, want_menu, want_desktop)
            return R.add_app(entry)
        run_async(work, lambda e: (page.toast(tr("„{0}“ gespeichert.").format(e.name)), on_saved()), lambda exc: page.toast(tr("Fehler: {0}").format(exc)))

    dlg = FormDialog(tr("Anwendung bearbeiten") if app else tr("Verknüpfung anlegen"), tr("Speichern"), ok, height=680)
    g = group(tr("Anwendung"), tr("Prefix: {0}").format(prefix.path))
    name = Adw.EntryRow(title=tr("Name"), text=app.name if app else preset.get("name", ""))
    g.add(name)
    exe_row = action_row(tr("Programm (.exe)"), state["exe"] or tr("Noch keine Datei gewählt"), pick_icon("application-x-executable-symbolic"))
    pick = button(tr("Wählen …"), ["btn-secondary"], on_click=lambda: choose_file(
        win, tr("Programm im Prefix wählen"), str(prefix.wine_dir / "drive_c"),
        lambda p: (state.__setitem__("exe", p), state.__setitem__("launcher", (launchers.match_exe(p).id if launchers.match_exe(p) else "")),
                   exe_row.set_subtitle(p), name.set_text(name.get_text() or guess_app_name(p))), EXE_PATTERNS))
    pick.set_valign(Gtk.Align.CENTER)
    exe_row.add_suffix(pick)
    g.add(exe_row)
    args = Adw.EntryRow(title=tr("Programmargumente (optional)"), text=app.args if app else preset.get("args", ""))
    g.add(args)
    dlg.add_group(g)

    g2 = group(tr("Start"), tr("Startoptionen gelten zusätzlich zu den globalen und den Prefix-Optionen."))
    runner = Adw.ComboRow(title=tr("Runner"))
    runner.set_model(Gtk.StringList.new(names))
    select_id(runner, ids, app.tool if app else "")
    g2.add(runner)
    opts = Adw.EntryRow(title=tr("Startoptionen (Steam-Syntax)"), text=app.launch_options if app else "")
    add_goverlay_buttons(opts, page)
    g2.add(opts)
    envrow = Adw.EntryRow(title=tr("Umgebungsvariablen (KEY=WERT …)"),
                          text=launch_options.format_env(app.env if app else preset.get("env", {})))
    pfx_on = ps.wayland_driver == "on" or (ps.wayland_driver != "off" and L.wayland_default())
    wl = wayland_row(tr("Wie Prefix ({0})").format(tr("an") if pfx_on else tr("aus")), app.wayland_driver if app else "")

    def wl_hint(*_: object) -> None:
        own = "PROTON_ENABLE_WAYLAND" in launch_options.parse_env_line(envrow.get_text())
        wl.set_subtitle(tr("Variable PROTON_ENABLE_WAYLAND hat Vorrang") if own else WAYLAND_SUBTITLE)
    envrow.connect("changed", wl_hint)
    wl_hint()
    g2.add(wl)
    ccd = ccd_row(app.ccd if app else "")
    if ccd is not None:
        g2.add(ccd)
    g2.add(envrow)
    dlg.add_group(g2)

    g3 = group(tr("Verknüpfungen"))
    menu = Adw.SwitchRow(title=tr("Startmenü"), subtitle=str(shortcuts.APPS_DIR), active=app.menu if app else True)
    g3.add(menu)
    desktop = Adw.SwitchRow(title=tr("Schreibtisch"), subtitle=str(shortcuts.desktop_dir()), active=app.desktop if app else bool(preset.get("desktop")))
    g3.add(desktop)
    dlg.add_group(g3)
    dlg.present(page)


# ----------------------------------------------------------------------------- Seite: Programm starten
class LaunchPage(Page):
    key = "launch"
    title = tr("Programm starten")
    subtitle = tr("Windows-Programme (.exe/.msi) in einem Prefix starten oder installieren – Runner, Startoptionen in Steam-Syntax, GOverlay.")
    icons = ("system-run-symbolic", "media-playback-start-symbolic", "application-x-executable-symbolic")

    def build(self) -> None:
        self.file = ""
        self.prefixes: list[system.Prefix] = []
        self.header.actions.append(icon_button(pick_icon("view-refresh-symbolic"), tr("Prefixe und Runner neu einlesen"), ["flat", "hdr-btn"],
                                               on_click=lambda: (self.win.launcher.tools(refresh=True), self._reload())))
        L = self.win.launcher

        g = group(tr("Programm"))
        self.file_row = action_row(tr("Keine Datei gewählt"), tr("Datei auswählen oder eine .exe/.msi hierher ziehen"),
                                   pick_icon("document-open-symbolic", "application-x-executable-symbolic"))
        pick = button(tr("Auswählen …"), ["btn-secondary"], on_click=self._pick)
        pick.set_valign(Gtk.Align.CENTER)
        self.file_row.add_suffix(pick)
        g.add(self.file_row)
        self.content.append(g)
        drop = Gtk.DropTarget.new(Gio.File, Gdk.DragAction.COPY)
        drop.connect("drop", self._on_drop)
        self.add_controller(drop)

        g2 = group(tr("Prefix"))
        self.prefix_row = Adw.ComboRow(title=tr("Prefix"))
        self.prefix_row.set_model(Gtk.StringList.new([]))
        self.prefix_row.connect("notify::selected", lambda *_: self._prefix_changed())
        nb = button(tr("Neuer Prefix"), ["btn-secondary"], icon=pick_icon("list-add-symbolic"), on_click=self._new_prefix)
        nb.set_valign(Gtk.Align.CENTER)
        self.prefix_row.add_suffix(nb)
        g2.add(self.prefix_row)
        self.info_row = action_row(tr("Details"), "–", pick_icon("dialog-information-symbolic"))
        g2.add(self.info_row)
        self.content.append(g2)

        g3 = group(tr("Runner & Startoptionen"), tr("Steam-Syntax: Programme vor %command%, Argumente dahinter – z. B. mangohud gamemoderun %command% -windowed"))
        self.runner_row = Adw.ComboRow(title=tr("Runner"))
        self.runner_row.set_model(Gtk.StringList.new([]))
        self.runner_row.connect("notify::selected", lambda *_: self._preview())
        g3.add(self.runner_row)
        self.opts = Adw.EntryRow(title=tr("Startoptionen für diesen Start"), text="")
        self.opts.connect("changed", lambda *_: self._preview())
        add_goverlay_buttons(self.opts, self)
        g3.add(self.opts)
        self.envrow = Adw.EntryRow(title=tr("Umgebungsvariablen für diesen Start (KEY=WERT …)"))
        self.envrow.connect("changed", lambda *_: self._preview())
        g3.add(self.envrow)
        self.preview_row = action_row(tr("Befehlsvorschau"), "–", pick_icon("utilities-terminal-symbolic"))
        self.preview_row.add_css_class("mono")
        cb = icon_button(pick_icon("edit-copy-symbolic"), tr("Befehl kopieren"), ["flat"], on_click=lambda: copy_text(self, self.preview_row.get_subtitle() or ""))
        cb.set_valign(Gtk.Align.CENTER)
        self.preview_row.add_suffix(cb)
        g3.add(self.preview_row)
        self.content.append(g3)

        self.content.append(button_flow([
            button(tr("STARTEN"), ["btn-primary"], icon=pick_icon("media-playback-start-symbolic"), on_click=self._start),
            button(tr("INSTALLIEREN …"), ["btn-primary"], icon=pick_icon("document-save-symbolic", "folder-download-symbolic"),
                   on_click=self._install, tooltip=tr("Installer in einem (neuen) Prefix ausführen, Hauptprogramm erkennen, Verknüpfungen anlegen")),
            button("winecfg", ["btn-secondary"], on_click=lambda: self._tool("winecfg")),
            button("winetricks", ["btn-secondary"], on_click=lambda: self._tool("winetricks")),
            button(tr("drive_c öffnen"), ["btn-secondary"], icon=pick_icon("folder-open-symbolic"), on_click=lambda: self._tool("explorer")),
        ], margin=0))

        self.task = TaskRunner(min_height=150, idle_text=tr("Protokoll von Prefix-Erstellung und Installationen erscheint hier."))
        self.content.append(self.task)

        g4 = group(tr("Dateimanager-Verknüpfung"), tr("Ein Doppelklick auf .exe/.msi im Dateimanager öffnet dann diese Seite mit der Datei."))
        self.assoc_row = action_row(tr("Aktuelle Standardanwendung für .exe"), "–", pick_icon("emblem-shared-symbolic", "insert-link-symbolic"))
        rb = button(tr("Dieses Tool als Standard setzen"), ["btn-secondary"], on_click=self._register)
        rb.set_valign(Gtk.Align.CENTER)
        self.assoc_row.add_suffix(rb)
        g4.add(self.assoc_row)
        self.content.append(g4)
        self._pending_prefix: str | None = None

    def on_show(self) -> None:
        self._reload()
        run_async(system.exe_handler_default, lambda d: self.assoc_row.set_subtitle(d or tr("keine")))

    # ------------------------------------------------------------------ Datei / Prefix
    def set_file(self, path: str) -> None:
        self.file = path
        self.file_row.set_title(Path(path).name)
        self.file_row.set_subtitle(path)
        self._preview()

    def set_prefix(self, path: str | Path) -> None:
        self._pending_prefix = str(path)
        for i, p in enumerate(self.prefixes):
            if str(p.path) == str(path):
                self.prefix_row.set_selected(i)
                self._pending_prefix = None
                break

    def _pick(self) -> None:
        start = str(Path(self.file).parent) if self.file else str(Path.home())
        choose_file(self.win, tr("Windows-Programm auswählen"), start, self.set_file, EXE_PATTERNS)

    def _on_drop(self, _t, value, _x, _y) -> bool:
        if isinstance(value, Gio.File) and value.get_path():
            self.set_file(value.get_path())
            return True
        return False

    def _reload(self, select: str | None = None) -> None:
        def done(prefixes: list[system.Prefix]) -> None:
            current = self._selected_prefix()
            self.prefixes = prefixes
            self.prefix_row.set_model(Gtk.StringList.new(
                [f"{p.name}  ·  {p.path}" if p.external else p.name for p in prefixes] or [tr("(kein Prefix vorhanden – „Neuer Prefix“)")]))
            want = select or self._pending_prefix or (str(current.path) if current else None)
            for i, p in enumerate(prefixes):
                if str(p.path) == want:
                    self.prefix_row.set_selected(i)
                    break
            self._pending_prefix = None
            self._prefix_changed()
        run_async(self.win.prefix_list, done)

    def _selected_prefix(self) -> system.Prefix | None:
        i = self.prefix_row.get_selected()
        return self.prefixes[i] if 0 <= i < len(self.prefixes) else None

    def _prefix_changed(self) -> None:
        p = self._selected_prefix()
        L, R = self.win.launcher, self.win.registry
        if not p:
            self.info_row.set_subtitle(tr("Kein Prefix vorhanden – mit „Neuer Prefix“ anlegen oder „Installieren …“ nutzen."))
            self.runner_row.set_model(Gtk.StringList.new(tool_choices(L)[0]))
            self._preview()
            return
        ps = R.prefix_settings(p.path)
        names, ids = tool_choices(L, prefix_default=tool_name(L, ps.tool))
        self._runner_ids = ids
        self.runner_row.set_model(Gtk.StringList.new(names))
        dpi = f"DPI {p.dpi}" if p.dpi else "DPI 96"
        self.info_row.set_subtitle(tr("{0} · {1} · {2} · Prefix-Optionen: {3} · {4}").format(p.layout, p.arch, dpi, ps.launch_options or '–', p.path))
        self._preview()

    def _runner_override(self) -> str:
        i = self.runner_row.get_selected()
        ids = getattr(self, "_runner_ids", [""])
        return ids[i] if 0 <= i < len(ids) else ""

    def _option_texts(self) -> list[str]:
        p = self._selected_prefix()
        ps = self.win.registry.prefix_settings(p.path) if p else None
        return [self.cfg.get("LAUNCH_OPTIONS", ""), ps.launch_options if ps else "", self.opts.get_text().strip()]

    def _preview(self) -> None:
        p = self._selected_prefix()
        if not p or not self.file:
            self.preview_row.set_subtitle(tr("Datei und Prefix wählen, dann erscheint hier der vollständige Befehl."))
            return
        try:
            plan = self.win.launcher.build_plan(p.path, self.file, self._option_texts(),
                                                extra_env=launch_options.parse_env_line(self.envrow.get_text()),
                                                tool_override=self._runner_override())
            self.preview_row.set_subtitle(plan.shell_preview())
        except (LaunchError, launch_options.LaunchOptionsError) as exc:
            self.preview_row.set_subtitle(str(exc))

    # ------------------------------------------------------------------ Aktionen
    def _new_prefix(self) -> None:
        new_prefix_dialog(self, self.task, lambda path: (self._reload(select=str(path)), self.win.refresh_badges(),
                                                        self.toast(tr("Prefix „{0}“ angelegt.").format(path.name))))

    def _start(self) -> None:
        if not self.file or not Path(self.file).is_file():
            self.toast(tr("Bitte zuerst eine .exe/.msi auswählen."))
            return
        p = self._selected_prefix()
        if not p:
            self.toast(tr("Bitte zuerst einen Prefix anlegen (oder „Installieren …“ nutzen)."))
            return
        try:
            plan = self.win.launcher.build_plan(p.path, self.file, self._option_texts(),
                                                extra_env=launch_options.parse_env_line(self.envrow.get_text()),
                                                tool_override=self._runner_override())
            self.win.launcher.launch(plan, f"wine-{p.name}")
        except (LaunchError, launch_options.LaunchOptionsError, OSError) as exc:
            self.toast(tr("Start fehlgeschlagen: {0}").format(exc))
            return
        self.task.log.append(tr("Gestartet ({0}): {1}").format(plan.description, plan.shell_preview()))
        self.toast(tr("{0} gestartet in „{1}“ über {2}.").format(Path(self.file).name, p.name, plan.description), 6)

    def _install(self) -> None:
        if not self.file or not Path(self.file).is_file():
            self.toast(tr("Bitte zuerst die Installationsdatei (.exe/.msi) auswählen."))
            return
        install_dialog(self, self.task, self.file, self.prefixes,
                       lambda: (self._reload(), self.win.refresh_badges(), self.win.invalidate_pages()))

    def _tool(self, mode: str) -> None:
        p = self._selected_prefix()
        if not p:
            self.toast(tr("Bitte zuerst einen Prefix anlegen."))
            return
        L = self.win.launcher
        try:
            if mode == "explorer":
                system.open_path(p.wine_dir / "drive_c")
                return
            if mode == "winetricks":
                argv, env = L.winetricks_command(p.path, tool=compat.resolve(self._runner_override(), L.tools()) if self._runner_override() else None)
            else:
                argv, env = L.wine_command(p.path, ["winecfg"], tool=compat.resolve(self._runner_override(), L.tools()) if self._runner_override() else None)
            system.detach(argv, env={k: v for k, v in env.items() if os.environ.get(k) != v}, logname=f"{mode}-{p.name}")
        except LaunchError as exc:
            self.toast(str(exc))

    def _register(self) -> None:
        path = system.register_exe_handler(self.win.app.launcher_command())
        self.toast(tr(".exe/.msi werden jetzt mit diesem Tool geöffnet ({0}).").format(Path(path).name))
        run_async(system.exe_handler_default, lambda d: self.assoc_row.set_subtitle(d or tr("keine")))


# ----------------------------------------------------------------------------- Seite: Prefixe verwalten
class PrefixesPage(Page):
    key = "prefixes"
    title = tr("Prefixe verwalten")
    subtitle = tr("Speicherorte, Runner (Proton/Wine), Startoptionen, DPI, Backups, Verknüpfungen – anlegen, umbenennen, verschieben, löschen.")
    icons = ("package-x-generic-symbolic", "folder-symbolic")

    def build(self) -> None:
        self.add_refresh_button()
        self.header.actions.append(button(tr("Neuer Prefix"), ["btn-primary"], icon=pick_icon("list-add-symbolic"), on_click=self._new))
        self.rows: list[Gtk.Widget] = []
        self.loc_rows: list[Gtk.Widget] = []
        self.backup_rows: list[Gtk.Widget] = []
        self.prefixes: list[system.Prefix] = []

        self.lgroup = group(tr("Speicherorte"), tr("Neue Prefixe landen im Standardordner; weitere Orte stehen in den Dialogen zur Auswahl."))
        ab = button(tr("Ort hinzufügen"), ["flat"], icon=pick_icon("list-add-symbolic"), on_click=self._add_location)
        self.lgroup.set_header_suffix(ab)
        self.content.append(self.lgroup)

        self.pgroup = group(tr("Prefixe"))
        self.content.append(self.pgroup)
        self.bgroup = group(tr("Backups"))
        ob = icon_button(pick_icon("folder-open-symbolic"), tr("Backup-Ordner öffnen"), ["flat"], on_click=lambda: system.open_path(self.cfg.backup_dir))
        self.bgroup.set_header_suffix(ob)
        self.content.append(self.bgroup)
        self.task = TaskRunner(min_height=140, idle_text=tr("Protokoll von Erstellung, Backup, Verschieben erscheint hier."))
        self.content.append(self.task)

    def on_show(self) -> None:
        self._fill_locations()
        self.bgroup.set_description(tr("{0} · tar.gz mit aufgelösten Symlinks").format(self.cfg.backup_dir))
        run_async(self.win.prefix_list, self._fill)
        run_async(lambda: system.list_backups(self.cfg.backup_dir), self._fill_backups)

    def _clear(self, grp: Adw.PreferencesGroup, rows: list[Gtk.Widget]) -> None:
        for r in rows:
            grp.remove(r)
        rows.clear()

    # ------------------------------------------------------------------ Speicherorte
    def _fill_locations(self) -> None:
        self._clear(self.lgroup, self.loc_rows)
        root = PathRow(tr("Standardordner für neue Prefixe"), str(self.cfg.wine_prefix_root), on_change=self._change_root,
                       parent_getter=lambda: self.win, choose_title=tr("Neuen Standardordner wählen"))
        self.lgroup.add(root)
        self.loc_rows.append(root)
        for loc in self.win.registry.locations:
            if loc == str(self.cfg.wine_prefix_root):
                continue
            row = PathRow(tr("Weiterer Speicherort"), loc, parent_getter=lambda: self.win)
            rb = icon_button(pick_icon("list-remove-symbolic", "edit-delete-symbolic"), tr("Aus der Liste entfernen (Dateien bleiben)"), ["flat"],
                             on_click=lambda l=loc: (self.win.registry.remove_location(l), self.on_show(), self.win.refresh_badges()))
            rb.set_valign(Gtk.Align.CENTER)
            row.add_suffix(rb)
            self.lgroup.add(row)
            self.loc_rows.append(row)

    def _add_location(self) -> None:
        choose_folder(self.win, tr("Speicherort hinzufügen"), str(self.cfg.wine_prefix_root),
                      lambda p: (self.win.registry.add_location(p), self.on_show(), self.win.refresh_badges()))

    def _change_root(self, new: str) -> None:
        old = self.cfg.wine_prefix_root
        if Path(new) == old:
            return
        movable = [p for p in self.prefixes if not p.external]
        check = Gtk.CheckButton(label=tr("{0} vorhandene Prefixe dorthin verschieben").format(len(movable)), active=bool(movable))
        check.set_sensitive(bool(movable))

        def do() -> None:
            move = check.get_active() and movable

            def work(log) -> int:
                n = 0
                Path(new).mkdir(parents=True, exist_ok=True)
                if move:
                    for p in movable:
                        dest = Path(new) / p.name
                        system.move_dir(p.path, dest, log)
                        self.win.registry.move_prefix(p.path, dest)
                        n += 1
                return n
            self.task.run_callable(work, tr("Stelle Standardordner auf {0} um …").format(new),
                                   on_done=lambda n: (self.cfg.set("WINE_PREFIX_ROOT", new), self.cfg.ensure_dirs(),
                                                      self.toast(tr("Standardordner geändert, {0} Prefixe verschoben.").format(n) if n
                                                                 else tr("Standardordner geändert.")),
                                                      self.on_show(), self.win.refresh_badges(), self.win.invalidate_pages()),
                                   on_error=lambda exc: self.on_show())
        confirm(self, tr("Standardordner ändern?"), tr("Neue Prefixe werden künftig unter\n{0}\nangelegt.").format(new), tr("Ändern"), do, extra=check)

    # ------------------------------------------------------------------ Prefixe
    def _fill(self, prefixes: list[system.Prefix]) -> None:
        self._clear(self.pgroup, self.rows)
        self.prefixes = prefixes
        n_loc = len([l for l in self.win.registry.locations if l != str(self.cfg.wine_prefix_root)])
        self.pgroup.set_description(tr("{0} Prefixe · Standardordner {1}").format(len(prefixes), self.cfg.wine_prefix_root)
                                    + (tr(" + {0} weiterer Speicherort").format(n_loc) if n_loc == 1
                                       else tr(" + {0} weitere Speicherorte").format(n_loc) if n_loc else ""))
        if not prefixes:
            lbl = label(tr("Keine Prefixe gefunden – oben rechts „Neuer Prefix“ oder auf der Startseite „Installieren …“."), css=["hint"])
            self.pgroup.add(lbl)
            self.rows.append(lbl)
            return
        L, R = self.win.launcher, self.win.registry
        for p in prefixes:
            ps = R.prefix_settings(p.path)
            row = Adw.ExpanderRow(title=p.name)
            row.set_subtitle(self._subtitle(p, None))
            row.add_suffix(Tag(tool_name(L, ps.tool)[:28], "accent"))
            row.add_suffix(Tag(p.arch if p.layout == "wine" else "PROTON-PFX"))
            if p.external:
                row.add_suffix(Tag(tr("WEITERER ORT"), "warn"))

            acts = [
                button(tr("Starten"), ["btn-secondary"], icon=pick_icon("media-playback-start-symbolic"), on_click=lambda pp=p: self._goto_launch(pp)),
                button("winecfg", ["btn-secondary"], on_click=lambda pp=p: self._wine_tool(pp, "winecfg")),
                button("winetricks", ["btn-secondary"], on_click=lambda pp=p: self._wine_tool(pp, "winetricks")),
                button("drive_c", ["btn-secondary"], icon=pick_icon("folder-open-symbolic"), on_click=lambda pp=p: system.open_path(pp.wine_dir / "drive_c")),
                button(tr("Backup"), ["btn-secondary"], icon=pick_icon("document-save-symbolic"), on_click=lambda pp=p: self._backup(pp)),
                button(tr("Umbenennen"), ["btn-secondary"], on_click=lambda pp=p: self._rename(pp)),
                button(tr("Verschieben …"), ["btn-secondary"], on_click=lambda pp=p: self._move(pp)),
                button(tr("Verknüpfung anlegen"), ["btn-secondary"], icon=pick_icon("list-add-symbolic"),
                       on_click=lambda pp=p: app_dialog(self, pp, None, self.on_show)),
                button(tr("Windows-Programme …"), ["btn-secondary"], tooltip=tr("Installierte Programme im Prefix anzeigen und deinstallieren"),
                       on_click=lambda pp=p: programs_dialog(self, self.task, pp.path, on_removed=lambda _k, n, _rc: (
                           self.toast(tr("Deinstallation von „{0}“ beendet.").format(n)), self.on_show()))),
                button(tr("Löschen"), ["btn-danger"], icon=pick_icon("user-trash-symbolic"), on_click=lambda pp=p: self._delete(pp)),
            ]
            row.add_row(button_flow(acts))

            names, ids = tool_choices(L)
            runner = Adw.ComboRow(title=tr("Runner (Proton/Wine)"))
            runner.set_model(Gtk.StringList.new(names))
            select_id(runner, ids, ps.tool)
            runner.connect("notify::selected", lambda r, _pp, pp=p, ids_=ids: self._set_tool(pp, ids_[r.get_selected()]))
            row.add_row(runner)
            wl = wayland_row(tr("Globale Vorgabe ({0})").format(tr("an") if L.wayland_default() else tr("aus")), ps.wayland_driver)
            wl.connect("notify::selected", lambda r, _pp, pp=p: self._set_wayland(pp, WAYLAND_MODES[r.get_selected()]))
            row.add_row(wl)
            opts = Adw.EntryRow(title=tr("Startoptionen für alle Programme im Prefix"), text=ps.launch_options, show_apply_button=True)
            opts.connect("apply", lambda r, pp=p: (R.set_prefix_settings(pp.path, launch_options=r.get_text().strip()), self.toast(tr("Startoptionen gespeichert."))))
            add_goverlay_buttons(opts, self)
            row.add_row(opts)
            envrow = Adw.EntryRow(title=tr("Umgebungsvariablen (KEY=WERT …)"), text=launch_options.format_env(ps.env), show_apply_button=True)
            envrow.connect("apply", lambda r, pp=p: (R.set_prefix_settings(pp.path, env=launch_options.parse_env_line(r.get_text())), self.toast(tr("Umgebungsvariablen gespeichert."))))
            row.add_row(envrow)
            dpi = Adw.SpinRow.new_with_range(96, 240, 12)
            dpi.set_title(tr("Windows-DPI"))
            dpi.set_subtitle("96 = 100 %, 120 = 125 %, 144 = 150 %, 192 = 200 %")
            dpi.set_value(p.dpi or 96)
            ab = button(tr("Anwenden"), ["btn-secondary"], on_click=lambda pp=p, sr=dpi: self._set_dpi(pp, int(sr.get_value())))
            ab.set_valign(Gtk.Align.CENTER)
            dpi.add_suffix(ab)
            row.add_row(dpi)

            for app in R.apps_for_prefix(p.path):
                arow = Adw.ActionRow(title=app.name, subtitle=f"{Path(app.exe).name} · {app.launch_options or tr('keine Startoptionen')}")
                arow.add_prefix(Gtk.Image.new_from_file(app.icon) if app.icon and Path(app.icon).exists() else Gtk.Image(icon_name="wine", pixel_size=16))
                if app.menu:
                    arow.add_suffix(Tag(tr("STARTMENÜ"), "ok"))
                if app.desktop:
                    arow.add_suffix(Tag(tr("SCHREIBTISCH"), "ok"))
                ab2 = Gtk.Box(spacing=4, valign=Gtk.Align.CENTER)
                ab2.append(button(tr("Starten"), ["btn-secondary"], on_click=lambda a=app: self._run_app(a)))
                ab2.append(icon_button(pick_icon("document-edit-symbolic", "edit-symbolic"), tr("Bearbeiten"), ["flat"],
                                       on_click=lambda a=app, pp=p: app_dialog(self, pp, a, self.on_show)))
                ab2.append(icon_button(pick_icon("user-trash-symbolic"), tr("Deinstallieren …"), ["flat"],
                                       on_click=lambda a=app: uninstall_app_dialog(self, self.task, a, self._after_uninstall)))
                arow.add_suffix(ab2)
                row.add_row(arow)

            for spec, exe in launchers.detect_installed(p.wine_dir):
                if R.find_app(p.path, launcher=spec.id, exe=str(exe)) is not None:
                    continue
                drow = Adw.ActionRow(title=tr("{0} gefunden").format(spec.name), subtitle=tr("{0} · noch ohne Verknüpfung").format(exe))
                drow.add_prefix(Gtk.Image(icon_name=pick_icon("dialog-information-symbolic"), pixel_size=16))
                db = button(tr("Verknüpfung anlegen"), ["btn-primary"], on_click=lambda s=spec, e=exe, pp=p: self._link_detected(pp, s, e))
                db.set_valign(Gtk.Align.CENTER)
                drow.add_suffix(db)
                row.add_row(drow)
                row.set_expanded(True)

            self.pgroup.add(row)
            self.rows.append(row)
            run_async(lambda pp=p: system.dir_size(pp.path), lambda size, r=row, pp=p: r.set_subtitle(self._subtitle(pp, size)), heavy=True)

    @staticmethod
    def _subtitle(p: system.Prefix, size: int | None) -> str:
        dpi = f"DPI {p.dpi}" if p.dpi else "DPI 96"
        s = f" · {system.human_bytes(size)}" if size is not None else ""
        return tr("{0} · {1} · geändert {2}{3}").format(p.path, dpi, fmt_time(p.mtime), s)

    # ------------------------------------------------------------------ Backups
    def _fill_backups(self, backups: list[system.Backup]) -> None:
        self._clear(self.bgroup, self.backup_rows)
        if not backups:
            lbl = label(tr("Keine Backups vorhanden."), css=["hint"])
            self.bgroup.add(lbl)
            self.backup_rows.append(lbl)
            return
        for b in backups:
            row = action_row(b.name, f"{system.human_bytes(b.size)} · {fmt_time(b.mtime)}", pick_icon("package-x-generic-symbolic"))
            rb = button(tr("Wiederherstellen"), ["btn-secondary"], on_click=lambda bb=b: self._restore(bb))
            rb.set_valign(Gtk.Align.CENTER)
            row.add_suffix(rb)
            db = icon_button(pick_icon("user-trash-symbolic"), tr("Backup löschen"), ["flat"], on_click=lambda bb=b: self._delete_backup(bb))
            db.set_valign(Gtk.Align.CENTER)
            row.add_suffix(db)
            self.bgroup.add(row)
            self.backup_rows.append(row)

    # ------------------------------------------------------------------ Aktionen
    def _new(self) -> None:
        new_prefix_dialog(self, self.task, lambda path: (self.on_show(), self.win.refresh_badges(), self.toast(tr("Prefix „{0}“ angelegt.").format(path.name))))

    def _goto_launch(self, p: system.Prefix) -> None:
        self.win.navigate("launch")
        self.win.pages["launch"].set_prefix(p.path)

    def _wine_tool(self, p: system.Prefix, mode: str) -> None:
        L = self.win.launcher
        try:
            argv, env = L.winetricks_command(p.path) if mode == "winetricks" else L.wine_command(p.path, ["winecfg"])
            system.detach(argv, env={k: v for k, v in env.items() if os.environ.get(k) != v}, logname=f"{mode}-{p.name}")
        except LaunchError as exc:
            self.toast(str(exc))

    def _set_wayland(self, p: system.Prefix, mode: str) -> None:
        self.win.registry.set_prefix_settings(p.path, wayland_driver=mode)
        self.toast(tr("Wayland-Treiber für „{0}“: {1}").format(p.name, WAYLAND_LABELS[mode] or tr("globale Vorgabe")))

    def _set_tool(self, p: system.Prefix, tool_id: str) -> None:
        self.win.registry.set_prefix_settings(p.path, tool=tool_id)
        self.toast(tr("Runner für „{0}“: {1}").format(p.name, tool_name(self.win.launcher, tool_id)))

    def _set_dpi(self, p: system.Prefix, dpi: int) -> None:
        self.task.set_status(tr("Setze DPI {0} für „{1}“ …").format(dpi, p.name))
        run_async(lambda: self.win.launcher.set_dpi(p.path, dpi),
                  lambda r: (self.toast(tr("DPI für „{0}“ auf {1} gesetzt – Programm neu starten.").format(p.name, dpi)) if r.returncode == 0
                             else self.toast(tr("Fehler: {0}").format((r.stderr or r.stdout).strip()[:200])), self.on_show()),
                  lambda exc: self.toast(tr("Fehler: {0}").format(exc)))

    def _run_app(self, app: AppEntry) -> None:
        try:
            plan = self.win.launcher.app_plan(app)
            self.win.launcher.launch(plan, f"app-{app.id}")
            self.task.log.append(tr("Gestartet ({0}): {1}").format(plan.description, plan.shell_preview()))
            self.toast(tr("„{0}“ gestartet über {1}.").format(app.name, plan.description))
        except (LaunchError, launch_options.LaunchOptionsError, OSError) as exc:
            self.toast(tr("Start fehlgeschlagen: {0}").format(exc))

    def _after_uninstall(self) -> None:
        self.on_show()
        self.win.refresh_badges()
        self.win.invalidate_pages()

    def _link_detected(self, p: system.Prefix, spec, exe: Path) -> None:
        run_async(lambda: register_app(self.win, name=spec.name, exe=str(exe), prefix=p.path, launcher_id=spec.id,
                                       args=spec.launch_args, env=spec.env_dict, menu=True, desktop=True, keep_existing=True),
                  lambda app: (toast_with_start(self, app, tr("Verknüpfung für „{0}“ angelegt (Startmenü und Schreibtisch).").format(app.name)),
                               self.on_show()),
                  lambda exc: self.toast(tr("Fehler: {0}").format(exc)))

    def _remove_app(self, app: AppEntry) -> None:
        def do() -> None:
            shortcuts.remove(app.id)
            self.win.registry.remove_app(app.id)
            self.toast(tr("„{0}“ und Verknüpfungen entfernt (Programmdateien bleiben im Prefix).").format(app.name))
            self.on_show()
        confirm(self, tr("„{0}“ entfernen?").format(app.name), tr("Entfernt den Eintrag und seine Verknüpfungen im Startmenü/auf dem Schreibtisch. "
                                                                   "Die installierten Dateien im Prefix bleiben erhalten."), tr("Entfernen"), do, destructive=True)

    def _rename(self, p: system.Prefix) -> None:
        def do(new_name: str) -> None:
            new_name = new_name.replace("/", "-")
            dest = p.path.parent / new_name
            if dest.exists():
                self.toast(tr("{0} existiert bereits.").format(dest))
                return

            def work(log) -> Path:
                d = system.move_dir(p.path, dest, log)
                self.win.registry.move_prefix(p.path, d)
                for a in self.win.registry.apps_for_prefix(d):
                    if a.menu or a.desktop:
                        shortcuts.create(a, a.menu, a.desktop)
                return d
            self.task.run_callable(work, tr("Benenne „{0}“ um …").format(p.name),
                                   on_done=lambda d: (self.toast(tr("Umbenannt in „{0}“.").format(d.name)), self.on_show(), self.win.invalidate_pages()),
                                   on_error=lambda exc: self.on_show())
        ask_text(self, tr("Prefix „{0}“ umbenennen").format(p.name), tr("Neuer Name (Ordnername im selben Speicherort):"), p.name, tr("Umbenennen"), do)

    def _move(self, p: system.Prefix) -> None:
        def picked(parent: str) -> None:
            dest = Path(parent) / p.name
            if dest.exists():
                self.toast(tr("{0} existiert bereits.").format(dest))
                return
            external = str(parent) != str(self.cfg.wine_prefix_root)

            def do() -> None:
                def work(log) -> Path:
                    d = system.move_dir(p.path, dest, log)
                    self.win.registry.move_prefix(p.path, d)
                    self.win.registry.set_prefix_settings(d, registered=external)
                    if external:
                        self.win.registry.add_location(parent)
                    for a in self.win.registry.apps_for_prefix(d):
                        if a.menu or a.desktop:
                            shortcuts.create(a, a.menu, a.desktop)
                    return d
                self.task.run_callable(work, tr("Verschiebe „{0}“ nach {1} …").format(p.name, parent),
                                       on_done=lambda d: (self.toast(tr("Verschoben nach {0}.").format(d)), self.on_show(), self.win.refresh_badges(), self.win.invalidate_pages()),
                                       on_error=lambda exc: self.on_show())
            confirm(self, tr("Prefix „{0}“ verschieben?").format(p.name),
                    tr("{0}\n→ {1}\n\nAuf demselben Dateisystem sofort, sonst Kopie per rsync. "
                       "Verknüpfungen werden angepasst.").format(p.path, dest), tr("Verschieben"), do)
        choose_folder(self.win, tr("Neuen Speicherort wählen"), str(p.path.parent), picked)

    def _backup(self, p: system.Prefix) -> None:
        self.cfg.backup_dir.mkdir(parents=True, exist_ok=True)
        archive = self.cfg.backup_dir / system.backup_archive_name(p.name)
        self.task.run(system.backup_argv(p.path.parent, p.name, archive), tr("Sichere „{0}“ nach {1} …").format(p.name, archive.name),
                      on_done=lambda rc: (self.toast(tr("Backup erstellt: {0}").format(archive)) if rc == 0 else None, self.on_show()))

    def _restore(self, b: system.Backup) -> None:
        confirm(self, tr("Backup wiederherstellen?"),
                tr("{0} wird nach {1} entpackt. Ein gleichnamiger Prefix wird überschrieben.").format(b.name, self.cfg.wine_prefix_root),
                tr("Wiederherstellen"), lambda: self.task.run(system.restore_argv(b.path, self.cfg.wine_prefix_root), tr("Stelle {0} wieder her …").format(b.name),
                                                          on_done=lambda rc: (self.on_show(), self.win.refresh_badges())),
                destructive=True)

    def _delete_backup(self, b: system.Backup) -> None:
        def do() -> None:
            try:
                b.path.unlink()
                self.toast(tr("{0} gelöscht.").format(b.name))
            except OSError as exc:
                self.toast(tr("Fehler: {0}").format(exc))
            self.on_show()
        confirm(self, tr("Backup löschen?"), tr("{0} ({1}) wird unwiderruflich gelöscht.").format(b.name, system.human_bytes(b.size)), tr("Löschen"), do, destructive=True)

    def _delete(self, p: system.Prefix) -> None:
        apps = self.win.registry.apps_for_prefix(p.path)

        err = system.safe_to_delete_prefix(p.path, protected_locations(self.win))
        if err:
            self.toast(err, 8)
            return

        def do() -> None:
            def work() -> None:
                system.kill_prefix_processes(p.path)
                shutil.rmtree(p.path)
                for a in self.win.registry.forget_prefix(p.path):
                    shortcuts.remove(a.id)
            run_async(work, lambda _r: (self.toast(tr("Prefix „{0}“ gelöscht.").format(p.name)), self.on_show(), self.win.refresh_badges(), self.win.invalidate_pages()),
                      lambda exc: self.toast(tr("Fehler: {0}").format(exc)))
        extra = tr("\n\nAußerdem werden {0} Anwendungseinträge samt Verknüpfungen entfernt.").format(len(apps)) if apps else ""
        confirm(self, tr("Prefix „{0}“ löschen?").format(p.name),
                tr("{0} wird mit allen darin installierten Programmen und Spielständen unwiderruflich gelöscht.{1} Vorher ein Backup anlegen?").format(p.path, extra),
                tr("Endgültig löschen"), do, destructive=True)
