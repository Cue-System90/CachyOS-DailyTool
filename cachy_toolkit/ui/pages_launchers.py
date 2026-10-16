"""Launcher-Assistent: bekannte Windows-Launcher auswählen, offiziellen Installer automatisch laden, Prefix/Ort, Runner (Proton),
Startoptionen und Verknüpfungen festlegen, installieren – und genauso wieder deinstallieren."""
from __future__ import annotations

from pathlib import Path

from .. import compat, launch_options, launchers, system
from ..launcher import LaunchError
from ..launchers import LauncherSpec
from ..registry import AppEntry, slugify
from .pages_wine import (FormDialog, LocationRow, add_goverlay_buttons, app_dialog, candidate_exes, choose_exe_dialog, register_app,
                         scan_exes, tool_choices, tool_name, toast_with_start, uninstall_app_dialog)
from .util import Adw, GLib, Gtk, button, label, pick_icon, run_async
from .widgets import Page, Tag, TaskRunner, action_row, group


def launcher_install_dialog(page: Page, task: TaskRunner, spec: LauncherSpec, prefixes: list[system.Prefix], on_finished) -> None:
    win = page.win
    L, R = win.launcher, win.registry
    if not L.tools():
        page.toast("Kein Proton/Wine gefunden – bitte proton-cachyos, umu-launcher oder wine installieren.")
        return
    if task.busy:
        page.toast("Es läuft bereits eine Installation – bitte warten.")
        return
    names, ids = tool_choices(L)

    def ok(d: FormDialog) -> None:
        i = existing.get_selected()
        tool_id = ids[runner.get_selected()]
        tool = compat.resolve(tool_id, L.tools()) if tool_id != compat.AUTO_ID else None
        if i > 0:
            prefix = prefixes[i - 1].path
            newly = False
        else:
            pn = pname.get_text().strip().replace("/", "-") or spec.id
            err = system.prefix_name_error(pn)
            if err:
                page.toast(err)
                return
            prefix = Path(loc.get_path()) / pn
            newly = not system.is_prefix(prefix)
        if tool is None and L.default_tool() is None:
            page.toast("Kein Proton/Wine gefunden – bitte proton-cachyos, umu-launcher oder wine installieren.")
            return
        # Runner steht erst zur Laufzeit fest (GE-Proton „neueste“ kann z. B. wegen des GitHub-Limits nicht ladbar sein)
        holder: dict = {"tool": tool, "tool_id": tool_id}
        create_steps = [L.ensure_runner_step(holder)] + ([L.runtime_create_step(prefix, holder, "win64")] if newly else [])
        opts_text = opts.get_text().strip()
        try:
            env = launch_options.parse_env_line(envrow.get_text())
            launch_options.parse(opts_text)
        except launch_options.LaunchOptionsError as exc:
            page.toast(str(exc))
            return
        args_text = args.get_text().strip()
        want_menu, want_desktop = menu.get_active(), desktop.get_active()
        use_silent = silent.get_active() if silent is not None else False
        use_wt = bool(wt is not None and wt.get_active() and wt.get_sensitive())
        if task.busy:
            page.toast("Es läuft bereits ein Vorgang – bitte warten.")
            return
        d.close()

        external = str(prefix.parent) != str(page.cfg.wine_prefix_root)
        # Neuer Prefix: Runner wird Prefix-Einstellung. Vorhandener Prefix: ein ausdrücklich gewählter Runner gilt für den Launcher.
        app_tool = lambda: holder["tool_id"] if (not newly and holder["tool_id"] != compat.AUTO_ID) else ""  # noqa: E731
        # Das Wine-Verzeichnis (<prefix> oder <prefix>/pfx bei Proton direkt) steht erst nach der Erstellung fest → immer frisch ermitteln
        wdir = lambda: L.wine_prefix_dir(prefix)  # noqa: E731
        state: dict = {"installer": None, "app": None, "installed": False,
                       "before": scan_exes(wdir()) if system.is_prefix(prefix) else set()}

        def download_step(log, ctx) -> int:
            try:
                state["installer"] = launchers.download(spec, log, ctx.cancelled)
            except launchers.DownloadError as exc:
                if ctx.cancelled():
                    return 130
                log(str(exc))
                return 1
            return 0

        def prefix_ready_step(log, ctx) -> int:
            if newly and system.is_prefix(prefix):
                R.set_prefix_settings(prefix, tool=holder["tool_id"], arch="win64", registered=external)
                if external:
                    R.add_location(str(prefix.parent))
                log(f"Prefix bereit: {prefix}")
            if system.is_prefix(prefix):
                for f in launchers.write_post_install(spec, wdir()):
                    log(f"Empfohlene Einstellungen angelegt: {f}")
            return 0

        def install_step(log, ctx) -> int:
            inst = state["installer"]
            plan = L.build_plan(prefix, inst, [], extra_env=env, tool=holder["tool"],
                                args=list(spec.install_args) + (list(spec.silent_args) if use_silent else []))
            log(f"$ {plan.shell_preview()}")
            if not use_silent:
                log("Das Installationsfenster öffnet sich gleich – bitte dort die Installation abschließen.")
            if spec.install_notes:
                log(f"Hinweis: {spec.install_notes}")
            rc = system.stream(plan.argv, log, env=plan.env, cwd=plan.cwd, on_start=ctx.on_start)
            state["installed"] = True
            if ctx.cancelled():
                return 130
            if rc != 0:
                log(f"Installer endete mit Code {rc} – das Ergebnis wird trotzdem geprüft.")
            return 0

        def register(exe: Path) -> None:
            if state["app"] is not None:
                return
            state["app"] = "pending"
            run_async(lambda: register_app(win, name=spec.name, exe=str(exe), prefix=prefix, launcher_id=spec.id,
                                           launch_options_text=opts_text, args=args_text, env=env, tool=app_tool(),
                                           menu=want_menu, desktop=want_desktop),
                      registered, failed)

        def registered(app: AppEntry) -> None:
            state["app"] = app
            task.log.append(f"Verknüpfung angelegt: {app.name} → {app.exe}")
            if task.busy:
                task.log.append(f"{spec.name} läuft noch (hat sich nach der Installation geöffnet). Du kannst ihn nutzen; "
                                "das Protokoll endet, sobald er geschlossen ist.")
            note = (f" Hinweis: {holder['fallback']} – installiert mit {tool_name(L, holder['tool_id'])}; "
                    "den Runner kannst du später unter „Einstellungen“ ändern.") if holder.get("fallback") else ""
            toast_with_start(page, app, f"{spec.name} ist eingerichtet" +
                             (" – Verknüpfungen im Startmenü/auf dem Schreibtisch angelegt." if (want_menu or want_desktop) else ".") + note)
            on_finished()

        def failed(exc: Exception) -> None:
            state["app"] = "failed"          # nicht bei jedem Poll erneut versuchen
            page.toast(f"Verknüpfung konnte nicht angelegt werden: {exc}", 8)

        def poll() -> bool:
            # Viele Launcher starten sich nach der Installation selbst und halten den Vorgang offen: sobald ein NEU
            # installiertes Hauptprogramm existiert, wird die Verknüpfung angelegt (vorhandene Installationen zählen nicht).
            if state["app"] is None and system.is_prefix(prefix):
                exe = launchers.find_exe(spec, wdir())
                if exe and (str(exe) not in state["before"] or state["installed"]):
                    register(exe)
            return GLib.SOURCE_CONTINUE if task.busy else GLib.SOURCE_REMOVE

        def done(rc: int) -> None:
            if rc == 130:
                page.toast("Abgebrochen.")
                on_finished()
                return
            if state["installer"] is None:
                page.toast("Download fehlgeschlagen – Details im Protokoll.", 8)
                on_finished()
                return
            if not system.is_prefix(prefix):
                page.toast("Prefix konnte nicht angelegt werden – siehe Protokoll.")
                on_finished()
                return
            if not state["installed"]:
                page.toast("Die Installation wurde nicht ausgeführt – Details im Protokoll.", 8)
                on_finished()
                return
            if state["app"] is not None:
                if state["app"] == "failed":
                    on_finished()
                return

            def final():
                w = wdir()
                exe = launchers.find_exe(spec, w, search=True)
                return exe, candidate_exes(state["before"], scan_exes(w), spec.name)

            def decided(res) -> None:
                exe, cands = res
                if exe:
                    register(exe)
                else:
                    page.toast(f"{spec.name} wurde nicht am erwarteten Ort gefunden – bitte das Hauptprogramm wählen.", 8)
                    choose_exe_dialog(page, cands, spec.name, prefix, opts_text, want_menu, want_desktop, on_finished,
                                      env=env, args=args_text, launcher_id=spec.id, tool=app_tool())
            run_async(final, decided)

        steps = [download_step] + create_steps + [prefix_ready_step]
        if use_wt and spec.winetricks:
            steps.append(lambda log, ctx: L.winetricks_step(prefix, list(spec.winetricks), holder["tool"])(log, ctx))
        steps.append(install_step)
        task.run_steps(steps, f"Installiere {spec.name} in {prefix} mit {tool_name(L, tool_id)} …", on_done=done)
        GLib.timeout_add_seconds(3, poll)

    dlg = FormDialog(f"{spec.name} installieren", "Herunterladen und installieren", ok, width=680, height=760)
    g = group(spec.name, spec.description)
    g.add(action_row("Offizieller Installer", f"{spec.filename} · {spec.url}", pick_icon("folder-download-symbolic", "document-save-symbolic")))
    for note in ((spec.install_notes,) if spec.install_notes else ()) + spec.known_issues:
        g.add(label(note, css=["hint"], wrap=True))
    dlg.add_group(g)

    g2 = group("Zielordner (Prefix)", "Der Launcher bekommt einen eigenen Prefix; der Ordner wird bei Bedarf angelegt.")
    existing = Adw.ComboRow(title="Prefix")
    existing.set_model(Gtk.StringList.new(["Neuen Prefix anlegen"] + [f"{p.name}  ·  {p.path}" for p in prefixes]))
    g2.add(existing)
    loc = LocationRow(page)
    g2.add(loc)
    def free_name() -> str:
        base = Path(loc.get_path())
        n, k = spec.id, 2
        while (base / n).exists():
            n, k = f"{spec.id}-{k}", k + 1
        return n
    pname = Adw.EntryRow(title="Prefix-Name", text=free_name())
    g2.add(pname)
    result = action_row("Installationsziel", "–", pick_icon("folder-new-symbolic"))
    g2.add(result)
    fswarn = action_row("Ungeeignetes Dateisystem", "", pick_icon("dialog-warning-symbolic"))
    fswarn.add_css_class("warn")
    fswarn.set_visible(False)
    g2.add(fswarn)
    dlg.add_group(g2)

    g3 = group("Runner & Startoptionen", "Gelten für die Verknüpfung; später unter „Prefixe verwalten“ änderbar.")
    runner = Adw.ComboRow(title="Runner (Proton/Wine)")
    if spec.recommended_tool in ids:
        i_rec = ids.index(spec.recommended_tool)
        names = list(names)
        names[i_rec] = names[i_rec] + "  (empfohlen)"
    runner.set_model(Gtk.StringList.new(names))
    if spec.recommended_tool in ids:
        runner.set_selected(ids.index(spec.recommended_tool))
    if spec.runner_note:
        runner.set_subtitle(spec.runner_note)
    g3.add(runner)
    opts = Adw.EntryRow(title="Startoptionen (Steam-Syntax, z. B. mangohud %command%)")
    add_goverlay_buttons(opts, page)
    g3.add(opts)
    args = Adw.EntryRow(title="Programmargumente des Launchers", text=spec.launch_args)
    g3.add(args)
    envrow = Adw.EntryRow(title="Umgebungsvariablen (KEY=WERT …)", text=launch_options.format_env(spec.env_dict))
    g3.add(envrow)
    dlg.add_group(g3)

    g4 = group("Installation & Verknüpfungen")
    silent = None
    if spec.silent_args:
        silent = Adw.SwitchRow(title="Unbeaufsichtigt installieren", subtitle="Installer-Argumente: " + " ".join(spec.silent_args),
                               active=spec.silent_default)
        g4.add(silent)
    wt = None
    if spec.winetricks:
        usable = system.have("winetricks") or compat.umu_available()
        wt = Adw.SwitchRow(title="Benötigte Komponenten installieren", subtitle="winetricks: " + " ".join(spec.winetricks)
                           + ("" if usable else " – winetricks bzw. umu-launcher fehlt"), active=usable)
        wt.set_sensitive(usable)
        g4.add(wt)
    menu = Adw.SwitchRow(title="Verknüpfung im Startmenü", active=True)
    g4.add(menu)
    desktop = Adw.SwitchRow(title="Verknüpfung auf dem Schreibtisch", active=True)
    g4.add(desktop)
    dlg.add_group(g4)

    fs_cache: dict[str, str] = {}
    state_ui: dict = {"last": None}

    def update(*_: object) -> None:
        i = existing.get_selected()
        new = i == 0
        loc.set_sensitive(new)
        pname.set_sensitive(new)
        if new:
            pn = pname.get_text().strip().replace("/", "-") or spec.id
            target = Path(loc.get_path()) / pn
            result.set_subtitle(f"{target}" + (" (vorhandener Prefix wird verwendet)" if system.is_prefix(target) else " (wird angelegt)"))
        else:
            p = prefixes[i - 1]
            target = p.path
            ptool = tool_name(L, R.prefix_settings(p.path).tool)
            result.set_subtitle(f"{p.path} (vorhanden, Runner {ptool})")
            if state_ui.get("last") != i:
                runner.set_selected(0)
                runner.set_subtitle(f"„Automatisch“ = Runner des Prefix ({ptool}); eine andere Wahl gilt nur für diesen Launcher.")
        if new and state_ui.get("last") not in (None, 0):
            if spec.recommended_tool in ids:
                runner.set_selected(ids.index(spec.recommended_tool))
            runner.set_subtitle(spec.runner_note or "")
        state_ui["last"] = i
        base = str(target.parent)
        if base not in fs_cache:
            fs_cache[base] = system.fs_type(base)
        fst = fs_cache[base]
        bad = fst in system.UNSUITABLE_PREFIX_FS
        fswarn.set_visible(bad)
        if bad:
            fswarn.set_subtitle(f"{base} liegt auf {fst}. Wine-Prefixe brauchen ein Linux-Dateisystem (ext4, btrfs, xfs) – "
                                "Installationen brechen sonst ab. Bitte einen anderen Speicherort wählen.")
    pname.connect("changed", update)
    existing.connect("notify::selected", update)
    loc.on_change = update
    update()
    dlg.present(page)


class LaunchersPage(Page):
    key = "launchers"
    title = "Launcher"
    subtitle = "Bekannte Windows-Launcher mit einem Klick installieren – offizieller Installer, eigener Prefix, Proton, Startoptionen, Verknüpfung."
    icons = ("applications-games-symbolic", "input-gaming-symbolic")

    def build(self) -> None:
        self.add_refresh_button("Prefixe neu durchsuchen")
        self.lgroup = group("Launcher", "Installierte Launcher werden in allen Prefixen erkannt – auch manuell installierte.")
        self.content.append(self.lgroup)
        self.rows: list[Gtk.Widget] = []
        self.task = TaskRunner(min_height=170, idle_text="Download- und Installationsprotokoll erscheint hier.")
        self.content.append(self.task)
        self.prefixes: list[system.Prefix] = []

    def on_show(self) -> None:
        def work():
            prefixes = self.win.prefix_list()
            apps = self.win.registry.all_apps()
            found: dict[str, list[tuple[system.Prefix, Path]]] = {}
            for p in prefixes:
                for spec, exe in launchers.detect_installed(p.wine_dir):
                    found.setdefault(spec.id, []).append((p, exe))
            return prefixes, apps, found
        run_async(work, self._fill)

    def refresh(self) -> None:
        self.win.launcher.tools(refresh=True)
        self.on_show()

    def _fill(self, res) -> None:
        prefixes, apps, found = res
        self.prefixes = prefixes
        for r in self.rows:
            self.lgroup.remove(r)
        self.rows.clear()
        for spec in launchers.CATALOG:
            mine = [a for a in apps if a.launcher == spec.id or (not a.launcher and launchers.match_exe(a.exe) is spec)]
            reg = self.win.registry
            loose = [(p, exe) for p, exe in found.get(spec.id, [])
                     if reg.find_app(p.path, launcher=spec.id, exe=str(exe)) is None
                     and not any(reg._key(a.prefix) == reg._key(p.path) for a in mine)]
            row = Adw.ExpanderRow(title=spec.name, subtitle=spec.description)
            row.add_prefix(Gtk.Image(icon_name=pick_icon("applications-games-symbolic", "input-gaming-symbolic"), pixel_size=22))
            if mine:
                row.add_suffix(Tag("INSTALLIERT" if len(mine) == 1 else f"{len(mine)}× INSTALLIERT", "ok"))
                sb = button("Starten", ["btn-primary"], icon=pick_icon("media-playback-start-symbolic"), on_click=lambda a=mine[0]: self._run(a))
            elif loose:
                row.add_suffix(Tag("GEFUNDEN", "warn"))
                sb = button("Verknüpfung anlegen", ["btn-primary"], on_click=lambda s=spec, pe=loose[0]: self._link(s, pe[0], pe[1]))
            else:
                sb = button("Installieren …", ["btn-primary"], icon=pick_icon("folder-download-symbolic", "document-save-symbolic"),
                            on_click=lambda s=spec: self._install(s))
            sb.set_valign(Gtk.Align.CENTER)
            row.add_suffix(sb)

            for a in mine:
                ar = Adw.ActionRow(title=f"In „{Path(a.prefix).name}“", subtitle=f"{a.exe}")
                if a.icon and Path(a.icon).exists():
                    ar.add_prefix(Gtk.Image.new_from_file(a.icon))
                tags = [t for t, on in (("STARTMENÜ", a.menu), ("SCHREIBTISCH", a.desktop)) if on]
                for t in tags:
                    ar.add_suffix(Tag(t, "ok"))
                box = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
                box.append(button("Starten", ["btn-secondary"], on_click=lambda x=a: self._run(x)))
                box.append(button("Einstellungen", ["btn-secondary"], tooltip="Runner (Proton), Startoptionen, Umgebungsvariablen, Verknüpfungen",
                                  on_click=lambda x=a: self._edit(x)))
                box.append(button("Deinstallieren …", ["btn-danger"], on_click=lambda x=a: uninstall_app_dialog(self, self.task, x, self._changed)))
                ar.add_suffix(box)
                row.add_row(ar)
            for p, exe in loose:
                lr = Adw.ActionRow(title=f"Gefunden in „{p.name}“ – noch ohne Verknüpfung", subtitle=str(exe))
                lb = button("Verknüpfung anlegen", ["btn-secondary"], on_click=lambda s=spec, pp=p, e=exe: self._link(s, pp, e))
                lb.set_valign(Gtk.Align.CENTER)
                lr.add_suffix(lb)
                row.add_row(lr)
            more = Adw.ActionRow(title="Installieren" if not (mine or loose) else "Weitere Installation",
                                 subtitle="Offiziellen Installer laden und in einem (neuen) Prefix installieren")
            mb = button("Installieren …", ["btn-secondary"], on_click=lambda s=spec: self._install(s))
            mb.set_valign(Gtk.Align.CENTER)
            more.add_suffix(mb)
            row.add_row(more)
            if spec.known_issues:
                row.add_row(Adw.ActionRow(title="Hinweise", subtitle=" · ".join(spec.known_issues)))
            self.lgroup.add(row)
            self.rows.append(row)

    # ------------------------------------------------------------------ Aktionen
    def _changed(self) -> None:
        self.on_show()
        self.win.refresh_badges()
        self.win.invalidate_pages()

    def _install(self, spec: LauncherSpec) -> None:
        launcher_install_dialog(self, self.task, spec, self.prefixes, self._changed)

    def _run(self, app: AppEntry) -> None:
        try:
            plan = self.win.launcher.app_plan(app)
            self.win.launcher.launch(plan, f"app-{app.id}")
            self.task.log.append(f"Gestartet ({plan.description}): {plan.shell_preview()}")
            self.toast(f"„{app.name}“ gestartet über {plan.description}.")
        except (LaunchError, launch_options.LaunchOptionsError, OSError) as exc:
            self.toast(f"Start fehlgeschlagen: {exc}")

    def _edit(self, app: AppEntry) -> None:
        p = system.describe_prefix(Path(app.prefix))
        if p is None:
            self.toast(f"Prefix {app.prefix} nicht gefunden.")
            return
        app_dialog(self, p, app, self._changed)

    def _link(self, spec: LauncherSpec, p: system.Prefix, exe: Path) -> None:
        """Ein Klick: Verknüpfung mit den empfohlenen Einstellungen anlegen (anpassen danach über „Einstellungen“)."""
        run_async(lambda: register_app(self.win, name=spec.name, exe=str(exe), prefix=p.path, launcher_id=spec.id,
                                       args=spec.launch_args, env=spec.env_dict, menu=True, desktop=True, keep_existing=True),
                  lambda app: (toast_with_start(self, app, f"Verknüpfung für „{app.name}“ angelegt (Startmenü und Schreibtisch)."),
                               self._changed()),
                  lambda exc: self.toast(f"Fehler: {exc}", 8))
