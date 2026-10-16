"""Wine & Prefixe: Programm starten/installieren und Prefixe verwalten.

Speicherorte frei wählbar (Standardordner änderbar, weitere Orte), Runner je Prefix/Anwendung (Proton über umu,
Proton direkt, Wine), Startoptionen in Steam-Syntax mit GOverlay-Übernahme, Umgebungsvariablen, Umbenennen/
Verschieben/Löschen, Installations-Dialog mit Zielordner-Abfrage und Verknüpfungen für Startmenü/Schreibtisch.
"""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from .. import compat, goverlay, launch_options, shortcuts, system
from ..launcher import LaunchError
from ..registry import AppEntry, slugify
from .util import (Adw, Gdk, Gio, Gtk, button, choose_file, choose_folder, confirm, ask_text, copy_text, fmt_time,
                   icon_button, label, pick_icon, run_async)
from .widgets import Page, PathRow, Tag, TaskRunner, action_row, group

ARCHS = ["win64 (empfohlen)", "win32 (ältere 32-Bit-Programme)"]
EXE_PATTERNS = [("Windows-Programme (*.exe, *.msi)", ["*.exe", "*.EXE", "*.msi", "*.MSI"]), ("Alle Dateien", ["*"])]
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
        first, first_id = f"Wie Prefix ({prefix_default})", ""
    else:
        first, first_id = (f"Automatisch ({auto.name})" if auto else "Automatisch (kein Runner gefunden)"), compat.AUTO_ID
    return [first] + [t.label for t in tools], [first_id] + [t.id for t in tools]


def tool_name(launcher, tool_id: str) -> str:
    if not tool_id or tool_id == compat.AUTO_ID:
        t = launcher.default_tool()
        return t.name if t else "kein Runner"
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


def candidate_exes(before: set[str], after: set[str], app_name: str) -> list[str]:
    new = sorted(after - before)
    tokens = [t for t in re.split(r"[^a-z0-9]+", app_name.lower()) if len(t) > 2]
    good = [e for e in new if not any(s in Path(e).stem.lower() for s in SKIP_EXE)]
    good.sort(key=lambda e: (-sum(1 for t in tokens if t in Path(e).stem.lower()), len(Path(e).parts), e.lower()))
    rest = [e for e in new if e not in good]
    return good + rest


def add_goverlay_buttons(row: Adw.EntryRow, page: Page) -> None:
    """Preset-Auswahl, GOverlay-Übernahme und GOverlay-Start an eine Startoptionen-Zeile hängen."""
    dd = Gtk.DropDown.new_from_strings(["Vorlage …"] + [p[0] for p in goverlay.PRESETS])
    dd.set_valign(Gtk.Align.CENTER)
    dd.set_tooltip_text("Vorlage einfügen")

    def picked(*_):
        i = dd.get_selected()
        if i > 0:
            row.set_text(goverlay.PRESETS[i - 1][1])
            dd.set_selected(0)
    dd.connect("notify::selected", picked)
    row.add_suffix(dd)
    gb = icon_button(pick_icon("view-reveal-symbolic", "edit-paste-symbolic"), "Aus GOverlay übernehmen", ["flat"],
                     on_click=lambda: (row.set_text(goverlay.import_from_goverlay()),
                                       page.toast("Startoptionen aus der GOverlay-/MangoHud-Konfiguration übernommen.")))
    gb.set_valign(Gtk.Align.CENTER)
    row.add_suffix(gb)
    if system.have("goverlay"):
        ob = icon_button(pick_icon("preferences-system-symbolic", "emblem-system-symbolic"), "GOverlay öffnen", ["flat"],
                         on_click=lambda: goverlay.open_goverlay() or page.toast("GOverlay konnte nicht gestartet werden."))
        ob.set_valign(Gtk.Align.CENTER)
        row.add_suffix(ob)


class LocationRow(Adw.ComboRow):
    """Speicherort für neue Prefixe: Standardordner, weitere bekannte Orte, anderer Ordner."""

    def __init__(self, page: Page, on_change=None) -> None:
        super().__init__(title="Speicherort")
        self.page = page
        self.on_change = on_change
        self._paths: list[str] = []
        self._refresh()
        self.connect("notify::selected", lambda *_: self.on_change and self.on_change())
        b = icon_button(pick_icon("folder-symbolic"), "Anderen Ordner wählen", ["flat"], on_click=self._pick)
        b.set_valign(Gtk.Align.CENTER)
        self.add_suffix(b)

    def _refresh(self, select: str | None = None) -> None:
        root = str(self.page.cfg.wine_prefix_root)
        self._paths = [root] + [l for l in self.page.win.registry.locations if l != root]
        self.set_model(Gtk.StringList.new([f"Standardordner · {root}"] + self._paths[1:]))
        if select and select in self._paths:
            self.set_selected(self._paths.index(select))

    def _pick(self) -> None:
        def picked(p: str) -> None:
            self.page.win.registry.add_location(p)
            self._refresh(select=p)
            if self.on_change:
                self.on_change()
        choose_folder(self.page.win, "Speicherort wählen", self.get_path(), picked)

    def get_path(self) -> str:
        i = self.get_selected()
        return self._paths[i] if 0 <= i < len(self._paths) else self._paths[0]


class FormDialog(Adw.Dialog):
    def __init__(self, title: str, ok_label: str, on_ok, width: int = 640, height: int = 640) -> None:
        super().__init__(title=title, content_width=width, content_height=height)
        tv = Adw.ToolbarView()
        tv.add_top_bar(Adw.HeaderBar())
        self.page = Adw.PreferencesPage()
        tv.set_content(self.page)
        bar = Gtk.Box(spacing=8, halign=Gtk.Align.END, margin_top=10, margin_bottom=14, margin_start=14, margin_end=14)
        bar.append(button("Abbrechen", ["btn-secondary"], on_click=self.close))
        self.ok = button(ok_label, ["btn-primary"], on_click=lambda: on_ok(self))
        bar.append(self.ok)
        tv.add_bottom_bar(bar)
        self.set_child(tv)

    def add_group(self, g: Adw.PreferencesGroup) -> None:
        self.page.add(g)


# ----------------------------------------------------------------------------- Dialog: neuer Prefix
def new_prefix_dialog(page: Page, task: TaskRunner, on_created, default_name: str = "") -> None:
    win = page.win
    L, R = win.launcher, win.registry
    if not L.tools():
        page.toast("Kein Proton/Wine gefunden – bitte proton-cachyos, umu-launcher oder wine installieren.")
        return
    names, ids = tool_choices(L)

    def ok(d: FormDialog) -> None:
        n = name.get_text().strip().replace("/", "-")
        if not n:
            page.toast("Bitte einen Namen eingeben.")
            return
        target = Path(loc.get_path()) / n
        if system.is_prefix(target):
            page.toast(f"{target} ist bereits ein Prefix.")
            return
        tool_id = ids[runner.get_selected()]
        tool = compat.resolve(tool_id, L.tools()) if tool_id != compat.AUTO_ID else None
        a = "win32" if arch.get_selected() == 1 else "win64"
        try:
            steps = L.create_prefix_steps(target, tool, a)
        except LaunchError as exc:
            page.toast(str(exc))
            return
        d.close()
        opts_text = opts.get_text().strip()
        external = str(target.parent) != str(page.cfg.wine_prefix_root)

        def done(rc: int) -> None:
            if rc != 0 or not system.is_prefix(target):
                page.toast("Prefix-Erstellung fehlgeschlagen – siehe Protokoll.")
                return
            R.set_prefix_settings(target, tool=tool_id, launch_options=opts_text, arch=a, registered=external)
            on_created(target)
        task.run_steps(steps, f"Erstelle Prefix „{n}“ mit {tool_name(L, tool_id)} …", on_done=done)

    dlg = FormDialog("Neuen Prefix anlegen", "Anlegen", ok, height=560)
    g = group("Prefix")
    name = Adw.EntryRow(title="Name", text=default_name)
    g.add(name)
    loc = LocationRow(page)
    g.add(loc)
    result = action_row("Wird angelegt unter", "–", pick_icon("folder-new-symbolic"))
    g.add(result)
    dlg.add_group(g)
    g2 = group("Runner & Startoptionen")
    runner = Adw.ComboRow(title="Runner (Proton/Wine)")
    runner.set_model(Gtk.StringList.new(names))
    g2.add(runner)
    arch = Adw.ComboRow(title="Architektur", subtitle="nur bei System-Wine relevant; Proton ist immer 64-Bit")
    arch.set_model(Gtk.StringList.new(ARCHS))
    g2.add(arch)
    opts = Adw.EntryRow(title="Startoptionen für alle Programme im Prefix (Steam-Syntax)")
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
        page.toast("Kein Proton/Wine gefunden – bitte proton-cachyos, umu-launcher oder wine installieren.")
        return
    names, ids = tool_choices(L)
    app_default = guess_app_name(installer)

    def ok(d: FormDialog) -> None:
        app_name = name.get_text().strip() or app_default
        i = existing.get_selected()
        tool_id = ids[runner.get_selected()]
        a = "win32" if arch.get_selected() == 1 else "win64"
        if i > 0:
            prefix = prefixes[i - 1].path
            create_steps: list = []
            tool = compat.resolve(tool_id, L.tools()) if tool_id != compat.AUTO_ID else None
        else:
            pn = pname.get_text().strip().replace("/", "-") or slugify(app_name)
            prefix = Path(loc.get_path()) / pn
            tool = compat.resolve(tool_id, L.tools()) if tool_id != compat.AUTO_ID else None
            try:
                create_steps = [] if system.is_prefix(prefix) else L.create_prefix_steps(prefix, tool, a)
            except LaunchError as exc:
                page.toast(str(exc))
                return
        opts_text = opts.get_text().strip()
        try:
            plan = L.build_plan(prefix, installer, [page.cfg.get("LAUNCH_OPTIONS", ""), R.prefix_settings(prefix).launch_options, opts_text],
                                tool=tool)
        except LaunchError as exc:
            page.toast(str(exc))
            return
        d.close()
        wine_dir = L.wine_prefix_dir(prefix)
        before = scan_exes(wine_dir)
        want_menu, want_desktop = menu.get_active(), desktop.get_active()
        newly = bool(create_steps)
        external = str(prefix.parent) != str(page.cfg.wine_prefix_root)

        def after(rc: int) -> None:
            if newly and system.is_prefix(prefix):
                R.set_prefix_settings(prefix, tool=tool_id, arch=a, registered=external)
                if external:
                    R.add_location(str(prefix.parent))
            if not system.is_prefix(prefix):
                page.toast("Prefix konnte nicht angelegt werden – siehe Protokoll.")
                return
            if rc != 0:
                page.toast(f"Installer endete mit Code {rc} – Programmsuche läuft trotzdem.")
            cands = candidate_exes(before, scan_exes(L.wine_prefix_dir(prefix)), app_name)
            choose_exe_dialog(page, cands, app_name, prefix, opts_text, want_menu, want_desktop, on_finished)

        steps = create_steps + [(plan.argv, plan.env)]
        task.run_steps(steps, f"Installiere „{app_name}“ in {prefix} ({plan.description}) …", on_done=after)

    dlg = FormDialog("Programm installieren", "Installieren", ok, height=720)
    g = group("Installation")
    g.add(action_row(Path(installer).name, installer, pick_icon("application-x-executable-symbolic")))
    name = Adw.EntryRow(title="Name der Anwendung", text=app_default)
    g.add(name)
    dlg.add_group(g)

    g2 = group("Zielordner", "Jede Installation bekommt ihren eigenen Prefix; der Ordner wird bei Bedarf angelegt.")
    existing = Adw.ComboRow(title="Prefix")
    existing.set_model(Gtk.StringList.new(["Neuen Prefix anlegen"] + [f"{p.name}  ·  {p.path}" for p in prefixes]))
    g2.add(existing)
    loc = LocationRow(page)
    g2.add(loc)
    pname = Adw.EntryRow(title="Prefix-Name", text=slugify(app_default))
    g2.add(pname)
    result = action_row("Installationsziel", "–", pick_icon("folder-new-symbolic"))
    g2.add(result)
    dlg.add_group(g2)

    g3 = group("Runner & Startoptionen")
    runner = Adw.ComboRow(title="Runner (Proton/Wine)")
    runner.set_model(Gtk.StringList.new(names))
    g3.add(runner)
    arch = Adw.ComboRow(title="Architektur", subtitle="nur bei System-Wine relevant")
    arch.set_model(Gtk.StringList.new(ARCHS))
    g3.add(arch)
    opts = Adw.EntryRow(title="Startoptionen für die Anwendung (Steam-Syntax)")
    add_goverlay_buttons(opts, page)
    g3.add(opts)
    dlg.add_group(g3)

    g4 = group("Nach der Installation", "Das Hauptprogramm wird automatisch gesucht; du bestätigst die Auswahl.")
    menu = Adw.SwitchRow(title="Verknüpfung im Startmenü anlegen", active=True)
    g4.add(menu)
    desktop = Adw.SwitchRow(title="Verknüpfung auf dem Schreibtisch anlegen", active=False)
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
            result.set_subtitle(f"{p.path} (vorhanden, {p.arch}, {tool_name(L, R.prefix_settings(p.path).tool)})")
    name.connect("changed", lambda *_: (pname.set_text(slugify(name.get_text())) if existing.get_selected() == 0 else None, update()))
    pname.connect("changed", update)
    existing.connect("notify::selected", update)
    loc.on_change = update
    update()
    dlg.present(page)


def choose_exe_dialog(page: Page, cands: list[str], app_name: str, prefix: Path, opts_text: str, want_menu: bool,
                      want_desktop: bool, on_finished) -> None:
    win = page.win
    R = win.registry
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    labels = [f"{Path(c).name}  ·  {Path(c).relative_to(win.launcher.wine_prefix_dir(prefix) / 'drive_c') if str(c).startswith(str(win.launcher.wine_prefix_dir(prefix))) else c}" for c in cands]
    dd = Gtk.DropDown.new_from_strings(labels + ["Andere Datei wählen …"])
    box.append(dd)
    chosen = {"path": cands[0] if cands else ""}
    hint = label("Keine neuen Programme gefunden – bitte Datei wählen." if not cands else
                 f"{len(cands)} neue Programme gefunden; das wahrscheinlichste ist vorausgewählt.", css=["hint"], wrap=True)
    box.append(hint)
    dlg = Adw.AlertDialog(heading=f"Hauptprogramm für „{app_name}“", body="Dieses Programm wird in Verknüpfungen und auf der Prefix-Seite verwendet.",
                          extra_child=box)
    dlg.add_response("later", "Später festlegen")
    dlg.add_response("ok", "Übernehmen")
    dlg.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
    dlg.set_default_response("ok")
    dlg.set_close_response("later")

    def on_dd(*_: object) -> None:
        i = dd.get_selected()
        if i == len(cands):
            def picked(p: str) -> None:
                chosen["path"] = p
                hint.set_text(p)
            choose_file(win, "Hauptprogramm wählen", str(win.launcher.wine_prefix_dir(prefix) / "drive_c"), picked, EXE_PATTERNS)
        elif 0 <= i < len(cands):
            chosen["path"] = cands[i]
    dd.connect("notify::selected", on_dd)
    if not cands:
        dd.set_selected(0)
        on_dd()

    def resp(_d, response: str) -> None:
        if response != "ok" or not chosen["path"]:
            page.toast("Installation abgeschlossen – Verknüpfung kann später über „Prefixe verwalten“ angelegt werden.", 6)
            on_finished()
            return
        app_id = R.new_app_id(app_name)
        app = AppEntry(id=app_id, name=app_name, exe=chosen["path"], prefix=str(prefix), launch_options=opts_text,
                       menu=want_menu, desktop=want_desktop)

        def work() -> AppEntry:
            app.icon = shortcuts.extract_icon(app.exe, app_id) or ""
            R.add_app(app)
            if want_menu or want_desktop:
                shortcuts.create(app, want_menu, want_desktop)
            return app
        run_async(work, lambda a: (page.toast(f"„{a.name}“ eingerichtet" + (" – Verknüpfungen angelegt." if (want_menu or want_desktop) else "."), 6),
                                   on_finished()),
                  lambda exc: (page.toast(f"Fehler: {exc}"), on_finished()))
    dlg.connect("response", resp)
    dlg.present(page)


# ----------------------------------------------------------------------------- Dialog: Verknüpfung / Anwendung
def app_dialog(page: Page, prefix: system.Prefix, app: AppEntry | None, on_saved) -> None:
    win = page.win
    L, R = win.launcher, win.registry
    ps = R.prefix_settings(prefix.path)
    names, ids = tool_choices(L, prefix_default=tool_name(L, ps.tool))
    state = {"exe": app.exe if app else ""}

    def ok(d: FormDialog) -> None:
        n = name.get_text().strip()
        if not n or not state["exe"]:
            page.toast("Name und Programm sind erforderlich.")
            return
        tool_id = ids[runner.get_selected()]
        want_menu, want_desktop = menu.get_active(), desktop.get_active()
        d.close()

        def work() -> AppEntry:
            if app is None:
                app_id = R.new_app_id(n)
                entry = AppEntry(id=app_id, name=n, exe=state["exe"], prefix=str(prefix.path))
            else:
                entry = app
                entry.name, entry.exe = n, state["exe"]
            entry.args = args.get_text().strip()
            entry.launch_options = opts.get_text().strip()
            entry.env = launch_options.parse_env_line(envrow.get_text())
            entry.tool = tool_id
            entry.menu, entry.desktop = want_menu, want_desktop
            if not entry.icon or not Path(entry.icon).exists():
                entry.icon = shortcuts.extract_icon(entry.exe, entry.id) or ""
            shortcuts.create(entry, want_menu, want_desktop)
            return R.add_app(entry)
        run_async(work, lambda e: (page.toast(f"„{e.name}“ gespeichert."), on_saved()), lambda exc: page.toast(f"Fehler: {exc}"))

    dlg = FormDialog("Anwendung bearbeiten" if app else "Verknüpfung anlegen", "Speichern", ok, height=680)
    g = group("Anwendung", f"Prefix: {prefix.path}")
    name = Adw.EntryRow(title="Name", text=app.name if app else "")
    g.add(name)
    exe_row = action_row("Programm (.exe)", state["exe"] or "Noch keine Datei gewählt", pick_icon("application-x-executable-symbolic"))
    pick = button("Wählen …", ["btn-secondary"], on_click=lambda: choose_file(
        win, "Programm im Prefix wählen", str(prefix.wine_dir / "drive_c"),
        lambda p: (state.__setitem__("exe", p), exe_row.set_subtitle(p), name.set_text(name.get_text() or guess_app_name(p))), EXE_PATTERNS))
    pick.set_valign(Gtk.Align.CENTER)
    exe_row.add_suffix(pick)
    g.add(exe_row)
    args = Adw.EntryRow(title="Programmargumente (optional)", text=app.args if app else "")
    g.add(args)
    dlg.add_group(g)

    g2 = group("Start", "Startoptionen gelten zusätzlich zu den globalen und den Prefix-Optionen.")
    runner = Adw.ComboRow(title="Runner")
    runner.set_model(Gtk.StringList.new(names))
    select_id(runner, ids, app.tool if app else "")
    g2.add(runner)
    opts = Adw.EntryRow(title="Startoptionen (Steam-Syntax)", text=app.launch_options if app else "")
    add_goverlay_buttons(opts, page)
    g2.add(opts)
    envrow = Adw.EntryRow(title="Umgebungsvariablen (KEY=WERT …)", text=launch_options.format_env(app.env) if app else "")
    g2.add(envrow)
    dlg.add_group(g2)

    g3 = group("Verknüpfungen")
    menu = Adw.SwitchRow(title="Startmenü", subtitle=str(shortcuts.APPS_DIR), active=app.menu if app else True)
    g3.add(menu)
    desktop = Adw.SwitchRow(title="Schreibtisch", subtitle=str(shortcuts.desktop_dir()), active=app.desktop if app else False)
    g3.add(desktop)
    dlg.add_group(g3)
    dlg.present(page)


# ----------------------------------------------------------------------------- Seite: Programm starten
class LaunchPage(Page):
    key = "launch"
    title = "Programm starten"
    subtitle = "Windows-Programme (.exe/.msi) in einem Prefix starten oder installieren – Runner, Startoptionen in Steam-Syntax, GOverlay."
    icons = ("system-run-symbolic", "media-playback-start-symbolic", "application-x-executable-symbolic")

    def build(self) -> None:
        self.file = ""
        self.prefixes: list[system.Prefix] = []
        self.header.actions.append(icon_button(pick_icon("view-refresh-symbolic"), "Prefixe und Runner neu einlesen", ["flat", "hdr-btn"],
                                               on_click=lambda: (self.win.launcher.tools(refresh=True), self._reload())))
        L = self.win.launcher

        g = group("Programm")
        self.file_row = action_row("Keine Datei gewählt", "Datei auswählen oder eine .exe/.msi hierher ziehen",
                                   pick_icon("document-open-symbolic", "application-x-executable-symbolic"))
        pick = button("Auswählen …", ["btn-secondary"], on_click=self._pick)
        pick.set_valign(Gtk.Align.CENTER)
        self.file_row.add_suffix(pick)
        g.add(self.file_row)
        self.content.append(g)
        drop = Gtk.DropTarget.new(Gio.File, Gdk.DragAction.COPY)
        drop.connect("drop", self._on_drop)
        self.add_controller(drop)

        g2 = group("Prefix")
        self.prefix_row = Adw.ComboRow(title="Prefix")
        self.prefix_row.set_model(Gtk.StringList.new([]))
        self.prefix_row.connect("notify::selected", lambda *_: self._prefix_changed())
        nb = button("Neuer Prefix", ["btn-secondary"], icon=pick_icon("list-add-symbolic"), on_click=self._new_prefix)
        nb.set_valign(Gtk.Align.CENTER)
        self.prefix_row.add_suffix(nb)
        g2.add(self.prefix_row)
        self.info_row = action_row("Details", "–", pick_icon("dialog-information-symbolic"))
        g2.add(self.info_row)
        self.content.append(g2)

        g3 = group("Runner & Startoptionen", "Steam-Syntax: Programme vor %command%, Argumente dahinter – z. B. mangohud gamemoderun %command% -windowed")
        self.runner_row = Adw.ComboRow(title="Runner")
        self.runner_row.set_model(Gtk.StringList.new([]))
        self.runner_row.connect("notify::selected", lambda *_: self._preview())
        g3.add(self.runner_row)
        self.opts = Adw.EntryRow(title="Startoptionen für diesen Start", text="")
        self.opts.connect("changed", lambda *_: self._preview())
        add_goverlay_buttons(self.opts, self)
        g3.add(self.opts)
        self.envrow = Adw.EntryRow(title="Umgebungsvariablen für diesen Start (KEY=WERT …)")
        self.envrow.connect("changed", lambda *_: self._preview())
        g3.add(self.envrow)
        self.preview_row = action_row("Befehlsvorschau", "–", pick_icon("utilities-terminal-symbolic"))
        self.preview_row.add_css_class("mono")
        cb = icon_button(pick_icon("edit-copy-symbolic"), "Befehl kopieren", ["flat"], on_click=lambda: copy_text(self, self.preview_row.get_subtitle() or ""))
        cb.set_valign(Gtk.Align.CENTER)
        self.preview_row.add_suffix(cb)
        g3.add(self.preview_row)
        self.content.append(g3)

        actions = Gtk.Box(spacing=8)
        actions.append(button("STARTEN", ["btn-primary"], icon=pick_icon("media-playback-start-symbolic"), on_click=self._start))
        actions.append(button("INSTALLIEREN …", ["btn-primary"], icon=pick_icon("document-save-symbolic", "folder-download-symbolic"),
                              on_click=self._install, tooltip="Installer in einem (neuen) Prefix ausführen, Hauptprogramm suchen, Verknüpfungen anlegen"))
        actions.append(button("winecfg", ["btn-secondary"], on_click=lambda: self._tool("winecfg")))
        actions.append(button("winetricks", ["btn-secondary"], on_click=lambda: self._tool("winetricks")))
        actions.append(button("drive_c öffnen", ["btn-secondary"], icon=pick_icon("folder-open-symbolic"), on_click=lambda: self._tool("explorer")))
        self.content.append(actions)

        self.task = TaskRunner(min_height=150, idle_text="Protokoll von Prefix-Erstellung und Installationen erscheint hier.")
        self.content.append(self.task)

        g4 = group("Dateimanager-Verknüpfung", "Ein Doppelklick auf .exe/.msi im Dateimanager öffnet dann diese Seite mit der Datei.")
        self.assoc_row = action_row("Aktuelle Standardanwendung für .exe", "–", pick_icon("emblem-shared-symbolic", "insert-link-symbolic"))
        rb = button("Dieses Tool als Standard setzen", ["btn-secondary"], on_click=self._register)
        rb.set_valign(Gtk.Align.CENTER)
        self.assoc_row.add_suffix(rb)
        g4.add(self.assoc_row)
        self.content.append(g4)
        self._pending_prefix: str | None = None

    def on_show(self) -> None:
        self._reload()
        run_async(system.exe_handler_default, lambda d: self.assoc_row.set_subtitle(d or "keine"))

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
        choose_file(self.win, "Windows-Programm auswählen", start, self.set_file, EXE_PATTERNS)

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
                [f"{p.name}  ·  {p.path}" if p.external else p.name for p in prefixes] or ["(kein Prefix vorhanden – „Neuer Prefix“)"]))
            want = select or self._pending_prefix or (str(current.path) if current else None)
            for i, p in enumerate(prefixes):
                if str(p.path) == want:
                    self.prefix_row.set_selected(i)
                    break
            self._pending_prefix = None
            self._prefix_changed()
        run_async(lambda: system.list_prefixes(self.cfg.wine_prefix_root, self.win.registry.registered_prefix_paths()), done)

    def _selected_prefix(self) -> system.Prefix | None:
        i = self.prefix_row.get_selected()
        return self.prefixes[i] if 0 <= i < len(self.prefixes) else None

    def _prefix_changed(self) -> None:
        p = self._selected_prefix()
        L, R = self.win.launcher, self.win.registry
        if not p:
            self.info_row.set_subtitle("Kein Prefix vorhanden – mit „Neuer Prefix“ anlegen oder „Installieren …“ nutzen.")
            self.runner_row.set_model(Gtk.StringList.new(tool_choices(L)[0]))
            self._preview()
            return
        ps = R.prefix_settings(p.path)
        names, ids = tool_choices(L, prefix_default=tool_name(L, ps.tool))
        self._runner_ids = ids
        self.runner_row.set_model(Gtk.StringList.new(names))
        dpi = f"DPI {p.dpi}" if p.dpi else "DPI 96"
        self.info_row.set_subtitle(f"{p.layout} · {p.arch} · {dpi} · Prefix-Optionen: {ps.launch_options or '–'} · {p.path}")
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
            self.preview_row.set_subtitle("Datei und Prefix wählen, dann erscheint hier der vollständige Befehl.")
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
                                                        self.toast(f"Prefix „{path.name}“ angelegt.")))

    def _start(self) -> None:
        if not self.file or not Path(self.file).is_file():
            self.toast("Bitte zuerst eine .exe/.msi auswählen.")
            return
        p = self._selected_prefix()
        if not p:
            self.toast("Bitte zuerst einen Prefix anlegen (oder „Installieren …“ nutzen).")
            return
        try:
            plan = self.win.launcher.build_plan(p.path, self.file, self._option_texts(),
                                                extra_env=launch_options.parse_env_line(self.envrow.get_text()),
                                                tool_override=self._runner_override())
            self.win.launcher.launch(plan, f"wine-{p.name}")
        except (LaunchError, launch_options.LaunchOptionsError, OSError) as exc:
            self.toast(f"Start fehlgeschlagen: {exc}")
            return
        self.task.log.append(f"Gestartet ({plan.description}): {plan.shell_preview()}")
        self.toast(f"{Path(self.file).name} gestartet in „{p.name}“ über {plan.description}.", 6)

    def _install(self) -> None:
        if not self.file or not Path(self.file).is_file():
            self.toast("Bitte zuerst die Installationsdatei (.exe/.msi) auswählen.")
            return
        install_dialog(self, self.task, self.file, self.prefixes,
                       lambda: (self._reload(), self.win.refresh_badges(), self.win.invalidate_pages()))

    def _tool(self, mode: str) -> None:
        p = self._selected_prefix()
        if not p:
            self.toast("Bitte zuerst einen Prefix anlegen.")
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
        self.toast(f".exe/.msi werden jetzt mit diesem Tool geöffnet ({Path(path).name}).")
        run_async(system.exe_handler_default, lambda d: self.assoc_row.set_subtitle(d or "keine"))


# ----------------------------------------------------------------------------- Seite: Prefixe verwalten
class PrefixesPage(Page):
    key = "prefixes"
    title = "Prefixe verwalten"
    subtitle = "Speicherorte, Runner (Proton/Wine), Startoptionen, DPI, Backups, Verknüpfungen – anlegen, umbenennen, verschieben, löschen."
    icons = ("package-x-generic-symbolic", "folder-symbolic")

    def build(self) -> None:
        self.add_refresh_button()
        self.header.actions.append(button("Neuer Prefix", ["btn-primary"], icon=pick_icon("list-add-symbolic"), on_click=self._new))
        self.rows: list[Gtk.Widget] = []
        self.loc_rows: list[Gtk.Widget] = []
        self.backup_rows: list[Gtk.Widget] = []
        self.prefixes: list[system.Prefix] = []

        self.lgroup = group("Speicherorte", "Neue Prefixe landen im Standardordner; weitere Orte stehen in den Dialogen zur Auswahl.")
        ab = button("Ort hinzufügen", ["flat"], icon=pick_icon("list-add-symbolic"), on_click=self._add_location)
        self.lgroup.set_header_suffix(ab)
        self.content.append(self.lgroup)

        self.pgroup = group("Prefixe")
        self.content.append(self.pgroup)
        self.bgroup = group("Backups")
        ob = icon_button(pick_icon("folder-open-symbolic"), "Backup-Ordner öffnen", ["flat"], on_click=lambda: system.open_path(self.cfg.backup_dir))
        self.bgroup.set_header_suffix(ob)
        self.content.append(self.bgroup)
        self.task = TaskRunner(min_height=140, idle_text="Protokoll von Erstellung, Backup, Verschieben erscheint hier.")
        self.content.append(self.task)

    def on_show(self) -> None:
        self._fill_locations()
        self.bgroup.set_description(f"{self.cfg.backup_dir} · tar.gz mit aufgelösten Symlinks")
        run_async(lambda: system.list_prefixes(self.cfg.wine_prefix_root, self.win.registry.registered_prefix_paths()), self._fill)
        run_async(lambda: system.list_backups(self.cfg.backup_dir), self._fill_backups)

    def _clear(self, grp: Adw.PreferencesGroup, rows: list[Gtk.Widget]) -> None:
        for r in rows:
            grp.remove(r)
        rows.clear()

    # ------------------------------------------------------------------ Speicherorte
    def _fill_locations(self) -> None:
        self._clear(self.lgroup, self.loc_rows)
        root = PathRow("Standardordner für neue Prefixe", str(self.cfg.wine_prefix_root), on_change=self._change_root,
                       parent_getter=lambda: self.win, choose_title="Neuen Standardordner wählen")
        self.lgroup.add(root)
        self.loc_rows.append(root)
        for loc in self.win.registry.locations:
            if loc == str(self.cfg.wine_prefix_root):
                continue
            row = PathRow("Weiterer Speicherort", loc, parent_getter=lambda: self.win)
            rb = icon_button(pick_icon("list-remove-symbolic", "edit-delete-symbolic"), "Aus der Liste entfernen (Dateien bleiben)", ["flat"],
                             on_click=lambda l=loc: (self.win.registry.remove_location(l), self._fill_locations()))
            rb.set_valign(Gtk.Align.CENTER)
            row.add_suffix(rb)
            self.lgroup.add(row)
            self.loc_rows.append(row)

    def _add_location(self) -> None:
        choose_folder(self.win, "Speicherort hinzufügen", str(self.cfg.wine_prefix_root),
                      lambda p: (self.win.registry.add_location(p), self._fill_locations()))

    def _change_root(self, new: str) -> None:
        old = self.cfg.wine_prefix_root
        if Path(new) == old:
            return
        movable = [p for p in self.prefixes if not p.external]
        check = Gtk.CheckButton(label=f"{len(movable)} vorhandene Prefixe dorthin verschieben", active=bool(movable))
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
            self.task.run_callable(work, f"Stelle Standardordner auf {new} um …",
                                   on_done=lambda n: (self.cfg.set("WINE_PREFIX_ROOT", new), self.cfg.ensure_dirs(),
                                                      self.toast(f"Standardordner geändert" + (f", {n} Prefixe verschoben." if n else ".")),
                                                      self.on_show(), self.win.refresh_badges(), self.win.invalidate_pages()),
                                   on_error=lambda exc: self.on_show())
        confirm(self, "Standardordner ändern?", f"Neue Prefixe werden künftig unter\n{new}\nangelegt.", "Ändern", do, extra=check)

    # ------------------------------------------------------------------ Prefixe
    def _fill(self, prefixes: list[system.Prefix]) -> None:
        self._clear(self.pgroup, self.rows)
        self.prefixes = prefixes
        self.pgroup.set_description(f"{len(prefixes)} Prefixe · Standardordner {self.cfg.wine_prefix_root}")
        if not prefixes:
            lbl = label("Keine Prefixe gefunden – oben rechts „Neuer Prefix“ oder auf der Startseite „Installieren …“.", css=["hint"])
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
                row.add_suffix(Tag("EXTERN", "warn"))

            act = Adw.ActionRow(title="Aktionen")
            bb = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
            bb.append(button("Starten", ["btn-secondary"], icon=pick_icon("media-playback-start-symbolic"), on_click=lambda pp=p: self._goto_launch(pp)))
            bb.append(button("winecfg", ["btn-secondary"], on_click=lambda pp=p: self._wine_tool(pp, "winecfg")))
            bb.append(button("winetricks", ["btn-secondary"], on_click=lambda pp=p: self._wine_tool(pp, "winetricks")))
            bb.append(button("drive_c", ["btn-secondary"], icon=pick_icon("folder-open-symbolic"), on_click=lambda pp=p: system.open_path(pp.wine_dir / "drive_c")))
            bb.append(button("Backup", ["btn-secondary"], icon=pick_icon("document-save-symbolic"), on_click=lambda pp=p: self._backup(pp)))
            act.add_suffix(bb)
            row.add_row(act)
            act2 = Adw.ActionRow(title="Verwalten")
            bb2 = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
            bb2.append(button("Umbenennen", ["btn-secondary"], on_click=lambda pp=p: self._rename(pp)))
            bb2.append(button("Verschieben …", ["btn-secondary"], on_click=lambda pp=p: self._move(pp)))
            bb2.append(button("Verknüpfung anlegen", ["btn-secondary"], icon=pick_icon("list-add-symbolic"),
                              on_click=lambda pp=p: app_dialog(self, pp, None, self.on_show)))
            bb2.append(button("Löschen", ["btn-danger"], icon=pick_icon("user-trash-symbolic"), on_click=lambda pp=p: self._delete(pp)))
            act2.add_suffix(bb2)
            row.add_row(act2)

            names, ids = tool_choices(L)
            runner = Adw.ComboRow(title="Runner (Proton/Wine)")
            runner.set_model(Gtk.StringList.new(names))
            select_id(runner, ids, ps.tool)
            runner.connect("notify::selected", lambda r, _pp, pp=p, ids_=ids: self._set_tool(pp, ids_[r.get_selected()]))
            row.add_row(runner)
            opts = Adw.EntryRow(title="Startoptionen für alle Programme im Prefix", text=ps.launch_options, show_apply_button=True)
            opts.connect("apply", lambda r, pp=p: (R.set_prefix_settings(pp.path, launch_options=r.get_text().strip()), self.toast("Startoptionen gespeichert.")))
            add_goverlay_buttons(opts, self)
            row.add_row(opts)
            envrow = Adw.EntryRow(title="Umgebungsvariablen (KEY=WERT …)", text=launch_options.format_env(ps.env), show_apply_button=True)
            envrow.connect("apply", lambda r, pp=p: (R.set_prefix_settings(pp.path, env=launch_options.parse_env_line(r.get_text())), self.toast("Umgebungsvariablen gespeichert.")))
            row.add_row(envrow)
            dpi = Adw.SpinRow.new_with_range(96, 240, 12)
            dpi.set_title("Windows-DPI")
            dpi.set_subtitle("96 = 100 %, 120 = 125 %, 144 = 150 %, 192 = 200 %")
            dpi.set_value(p.dpi or 96)
            ab = button("Anwenden", ["btn-secondary"], on_click=lambda pp=p, sr=dpi: self._set_dpi(pp, int(sr.get_value())))
            ab.set_valign(Gtk.Align.CENTER)
            dpi.add_suffix(ab)
            row.add_row(dpi)

            for app in R.apps_for_prefix(p.path):
                arow = Adw.ActionRow(title=app.name, subtitle=f"{Path(app.exe).name} · {app.launch_options or 'keine Startoptionen'}")
                arow.add_prefix(Gtk.Image.new_from_file(app.icon) if app.icon and Path(app.icon).exists() else Gtk.Image(icon_name="wine", pixel_size=16))
                if app.menu:
                    arow.add_suffix(Tag("STARTMENÜ", "ok"))
                if app.desktop:
                    arow.add_suffix(Tag("SCHREIBTISCH", "ok"))
                ab2 = Gtk.Box(spacing=4, valign=Gtk.Align.CENTER)
                ab2.append(button("Starten", ["btn-secondary"], on_click=lambda a=app: self._run_app(a)))
                ab2.append(icon_button(pick_icon("document-edit-symbolic", "edit-symbolic"), "Bearbeiten", ["flat"],
                                       on_click=lambda a=app, pp=p: app_dialog(self, pp, a, self.on_show)))
                ab2.append(icon_button(pick_icon("user-trash-symbolic"), "Anwendung und Verknüpfungen entfernen", ["flat"],
                                       on_click=lambda a=app: self._remove_app(a)))
                arow.add_suffix(ab2)
                row.add_row(arow)

            self.pgroup.add(row)
            self.rows.append(row)
            run_async(lambda pp=p: system.dir_size(pp.path), lambda size, r=row, pp=p: r.set_subtitle(self._subtitle(pp, size)), heavy=True)

    @staticmethod
    def _subtitle(p: system.Prefix, size: int | None) -> str:
        dpi = f"DPI {p.dpi}" if p.dpi else "DPI 96"
        s = f" · {system.human_bytes(size)}" if size is not None else ""
        return f"{p.path} · {dpi} · geändert {fmt_time(p.mtime)}{s}"

    # ------------------------------------------------------------------ Backups
    def _fill_backups(self, backups: list[system.Backup]) -> None:
        self._clear(self.bgroup, self.backup_rows)
        if not backups:
            lbl = label("Keine Backups vorhanden.", css=["hint"])
            self.bgroup.add(lbl)
            self.backup_rows.append(lbl)
            return
        for b in backups:
            row = action_row(b.name, f"{system.human_bytes(b.size)} · {fmt_time(b.mtime)}", pick_icon("package-x-generic-symbolic"))
            rb = button("Wiederherstellen", ["btn-secondary"], on_click=lambda bb=b: self._restore(bb))
            rb.set_valign(Gtk.Align.CENTER)
            row.add_suffix(rb)
            db = icon_button(pick_icon("user-trash-symbolic"), "Backup löschen", ["flat"], on_click=lambda bb=b: self._delete_backup(bb))
            db.set_valign(Gtk.Align.CENTER)
            row.add_suffix(db)
            self.bgroup.add(row)
            self.backup_rows.append(row)

    # ------------------------------------------------------------------ Aktionen
    def _new(self) -> None:
        new_prefix_dialog(self, self.task, lambda path: (self.on_show(), self.win.refresh_badges(), self.toast(f"Prefix „{path.name}“ angelegt.")))

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

    def _set_tool(self, p: system.Prefix, tool_id: str) -> None:
        self.win.registry.set_prefix_settings(p.path, tool=tool_id)
        self.toast(f"Runner für „{p.name}“: {tool_name(self.win.launcher, tool_id)}")

    def _set_dpi(self, p: system.Prefix, dpi: int) -> None:
        self.task.set_status(f"Setze DPI {dpi} für „{p.name}“ …")
        run_async(lambda: self.win.launcher.set_dpi(p.path, dpi),
                  lambda r: (self.toast(f"DPI für „{p.name}“ auf {dpi} gesetzt – Programm neu starten.") if r.returncode == 0
                             else self.toast(f"Fehler: {(r.stderr or r.stdout).strip()[:200]}"), self.on_show()),
                  lambda exc: self.toast(f"Fehler: {exc}"))

    def _run_app(self, app: AppEntry) -> None:
        try:
            plan = self.win.launcher.app_plan(app)
            self.win.launcher.launch(plan, f"app-{app.id}")
            self.task.log.append(f"Gestartet ({plan.description}): {plan.shell_preview()}")
            self.toast(f"„{app.name}“ gestartet über {plan.description}.")
        except (LaunchError, launch_options.LaunchOptionsError, OSError) as exc:
            self.toast(f"Start fehlgeschlagen: {exc}")

    def _remove_app(self, app: AppEntry) -> None:
        def do() -> None:
            shortcuts.remove(app.id)
            self.win.registry.remove_app(app.id)
            self.toast(f"„{app.name}“ und Verknüpfungen entfernt (Programmdateien bleiben im Prefix).")
            self.on_show()
        confirm(self, f"„{app.name}“ entfernen?", "Entfernt den Eintrag und seine Verknüpfungen im Startmenü/auf dem Schreibtisch. "
                "Die installierten Dateien im Prefix bleiben erhalten.", "Entfernen", do, destructive=True)

    def _rename(self, p: system.Prefix) -> None:
        def do(new_name: str) -> None:
            new_name = new_name.replace("/", "-")
            dest = p.path.parent / new_name
            if dest.exists():
                self.toast(f"{dest} existiert bereits.")
                return

            def work(log) -> Path:
                d = system.move_dir(p.path, dest, log)
                self.win.registry.move_prefix(p.path, d)
                for a in self.win.registry.apps_for_prefix(d):
                    if a.menu or a.desktop:
                        shortcuts.create(a, a.menu, a.desktop)
                return d
            self.task.run_callable(work, f"Benenne „{p.name}“ um …",
                                   on_done=lambda d: (self.toast(f"Umbenannt in „{d.name}“."), self.on_show(), self.win.invalidate_pages()),
                                   on_error=lambda exc: self.on_show())
        ask_text(self, f"Prefix „{p.name}“ umbenennen", "Neuer Name (Ordnername im selben Speicherort):", p.name, "Umbenennen", do)

    def _move(self, p: system.Prefix) -> None:
        def picked(parent: str) -> None:
            dest = Path(parent) / p.name
            if dest.exists():
                self.toast(f"{dest} existiert bereits.")
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
                self.task.run_callable(work, f"Verschiebe „{p.name}“ nach {parent} …",
                                       on_done=lambda d: (self.toast(f"Verschoben nach {d}."), self.on_show(), self.win.refresh_badges(), self.win.invalidate_pages()),
                                       on_error=lambda exc: self.on_show())
            confirm(self, f"Prefix „{p.name}“ verschieben?", f"{p.path}\n→ {dest}\n\nAuf demselben Dateisystem sofort, sonst Kopie per rsync. "
                    "Verknüpfungen werden angepasst.", "Verschieben", do)
        choose_folder(self.win, "Neuen Speicherort wählen", str(p.path.parent), picked)

    def _backup(self, p: system.Prefix) -> None:
        self.cfg.backup_dir.mkdir(parents=True, exist_ok=True)
        archive = self.cfg.backup_dir / system.backup_archive_name(p.name)
        self.task.run(system.backup_argv(p.path.parent, p.name, archive), f"Sichere „{p.name}“ nach {archive.name} …",
                      on_done=lambda rc: (self.toast(f"Backup erstellt: {archive}") if rc == 0 else None, self.on_show()))

    def _restore(self, b: system.Backup) -> None:
        confirm(self, "Backup wiederherstellen?",
                f"{b.name} wird nach {self.cfg.wine_prefix_root} entpackt. Ein gleichnamiger Prefix wird überschrieben.",
                "Wiederherstellen", lambda: self.task.run(system.restore_argv(b.path, self.cfg.wine_prefix_root), f"Stelle {b.name} wieder her …",
                                                          on_done=lambda rc: (self.on_show(), self.win.refresh_badges())),
                destructive=True)

    def _delete_backup(self, b: system.Backup) -> None:
        def do() -> None:
            try:
                b.path.unlink()
                self.toast(f"{b.name} gelöscht.")
            except OSError as exc:
                self.toast(f"Fehler: {exc}")
            self.on_show()
        confirm(self, "Backup löschen?", f"{b.name} ({system.human_bytes(b.size)}) wird unwiderruflich gelöscht.", "Löschen", do, destructive=True)

    def _delete(self, p: system.Prefix) -> None:
        apps = self.win.registry.apps_for_prefix(p.path)

        def do() -> None:
            def work() -> None:
                shutil.rmtree(p.path)
                for a in self.win.registry.forget_prefix(p.path):
                    shortcuts.remove(a.id)
            run_async(work, lambda _r: (self.toast(f"Prefix „{p.name}“ gelöscht."), self.on_show(), self.win.refresh_badges(), self.win.invalidate_pages()),
                      lambda exc: self.toast(f"Fehler: {exc}"))
        extra = f"\n\nAußerdem werden {len(apps)} Anwendungseinträge samt Verknüpfungen entfernt." if apps else ""
        confirm(self, f"Prefix „{p.name}“ löschen?",
                f"{p.path} wird mit allen darin installierten Programmen und Spielständen unwiderruflich gelöscht.{extra} Vorher ein Backup anlegen?",
                "Endgültig löschen", do, destructive=True)
