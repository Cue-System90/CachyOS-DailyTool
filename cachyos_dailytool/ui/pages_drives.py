"""Laufwerke: Partitionen erkennen, per UUID in fstab eintragen und mounten, formatieren; Ordner mit Benutzerrechten anlegen."""
from __future__ import annotations

import os
from pathlib import Path

from .. import system
from ..i18n import tr
from .util import Adw, Gtk, button, confirm, copy_text, icon_button, label, pick_icon, run_async, choose_folder
from .widgets import Page, Tag, TaskRunner, action_row, group

NTFS_DRIVERS = [tr("ntfs3 (Kernel-Treiber, empfohlen)"), tr("ntfs-3g (FUSE, ältere Kompatibilität)")]


class DrivesPage(Page):
    key = "drives"
    title = tr("Laufwerke einbinden")
    subtitle = tr("Zusätzliche NVMe-/SATA-/USB-Partitionen per UUID dauerhaft in /etc/fstab eintragen. Das Systemlaufwerk ist ausgeblendet.")
    icons = ("drive-harddisk-symbolic", "drive-harddisk-solidstate-symbolic")

    def build(self) -> None:
        self.add_refresh_button(tr("Laufwerke neu einlesen"))
        self.parts: list[system.Partition] = []
        self.sel: system.Partition | None = None
        self.uid, self.gid = os.getuid(), os.getgid()

        self.show_sys = Gtk.Switch(valign=Gtk.Align.CENTER, tooltip_text=tr("Systemlaufwerk anzeigen (nur Ansicht)"))
        self.show_sys.connect("notify::active", lambda *_: self.on_show())
        sw_box = Gtk.Box(spacing=8)
        sw_box.append(label(tr("Systemlaufwerk anzeigen"), css=["hint"]))
        sw_box.append(self.show_sys)
        self.header.actions.prepend(sw_box)

        self.lgroup = group(tr("Partitionen"))
        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.listbox.add_css_class("boxed-list")
        self.listbox.connect("row-selected", self._on_select)
        self.lgroup.add(self.listbox)
        self.content.append(self.lgroup)

        # Formular Einbinden
        self.fgroup = group(tr("Einbinden"), tr("Zuerst oben eine Partition wählen."))
        self.uuid_row = action_row("UUID", "–", pick_icon("dialog-password-symbolic", "emblem-system-symbolic"))
        cb = icon_button(pick_icon("edit-copy-symbolic"), tr("UUID kopieren"), ["flat"], on_click=lambda: copy_text(self, self.sel.uuid if self.sel else ""))
        cb.set_valign(Gtk.Align.CENTER)
        self.uuid_row.add_suffix(cb)
        self.fgroup.add(self.uuid_row)
        self.fs_row = action_row(tr("Dateisystem"), "–", pick_icon("drive-harddisk-symbolic"))
        self.fgroup.add(self.fs_row)
        self.ntfs_row = Adw.ComboRow(title=tr("NTFS-Treiber"))
        self.ntfs_row.set_model(Gtk.StringList.new(NTFS_DRIVERS))
        self.ntfs_row.connect("notify::selected", lambda *_: self._fill_defaults())
        self.ntfs_row.set_visible(False)
        self.fgroup.add(self.ntfs_row)
        self.base_row = Adw.EntryRow(title=tr("Basisverzeichnis"), text=self.cfg.get("MOUNT_BASE", "/mnt"))
        self.base_row.connect("changed", lambda *_: self._update_preview())
        self.fgroup.add(self.base_row)
        self.name_row = Adw.EntryRow(title=tr("Ordnername"))
        self.name_row.connect("changed", lambda *_: self._update_preview())
        self.fgroup.add(self.name_row)
        self.mp_row = action_row(tr("Mountpoint"), "–", pick_icon("folder-symbolic"))
        self.fgroup.add(self.mp_row)
        self.opts_row = Adw.EntryRow(title=tr("Mount-Optionen"))
        self.opts_row.connect("changed", lambda *_: self._update_preview())
        self.fgroup.add(self.opts_row)
        self.line_row = action_row(tr("fstab-Zeile"), "–", pick_icon("text-x-generic-symbolic"))
        self.line_row.add_css_class("mono")
        self.fgroup.add(self.line_row)
        self.content.append(self.fgroup)

        bar = Gtk.Box(spacing=8)
        self.mount_btn = button(tr("IN FSTAB EINTRAGEN UND MOUNTEN"), ["btn-primary"], icon=pick_icon("list-add-symbolic"), on_click=self._mount)
        self.mount_btn.set_sensitive(False)
        bar.append(self.mount_btn)
        self.content.append(bar)

        # Formatieren
        self.xgroup = group(tr("Formatieren"), tr("Löscht ALLE Daten auf der gewählten Partition. Danach kann sie oben eingebunden werden."))
        self.fmt_fs = Adw.ComboRow(title=tr("Neues Dateisystem"))
        self.fmt_fs.set_model(Gtk.StringList.new(list(system.FORMAT_FS.values())))
        self.xgroup.add(self.fmt_fs)
        self.fmt_label = Adw.EntryRow(title=tr("Datenträgerbezeichnung (Label)"), text="DATA")
        self.xgroup.add(self.fmt_label)
        self.fmt_btn = button(tr("Formatieren …"), ["btn-danger"], icon=pick_icon("edit-clear-all-symbolic"), on_click=self._format)
        self.fmt_btn.set_sensitive(False)
        fb = Gtk.Box()
        fb.append(self.fmt_btn)
        self.content.append(self.xgroup)
        self.content.append(fb)

        self.task = TaskRunner(min_height=120, idle_text=tr("Ausgabe der Root-Befehle (pkexec) erscheint hier."))
        self.content.append(self.task)

    def on_show(self) -> None:
        include = self.show_sys.get_active()
        run_async(lambda: system.list_partitions(include_system=include), self._fill)

    def _fill(self, parts: list[system.Partition]) -> None:
        prev = self.sel.name if self.sel else None
        self.parts = parts
        self.listbox.remove_all()
        self.sel = None
        if not parts:
            row = Adw.ActionRow(title=tr("Keine zusätzlichen Partitionen gefunden"),
                                subtitle=tr("Unpartitionierte Laufwerke zuerst mit KDE-Partitionsverwaltung/gparted partitionieren."))
            row.set_selectable(False)
            self.listbox.append(row)
            self._clear_form()
            return
        select_row = None
        for p in parts:
            title = f"{p.path}" + (f" · {p.label}" if p.label else "")
            fs = p.fstype or tr("kein Dateisystem")
            sub = f"{system.human_bytes(p.size)} · {fs} · {p.model or '–'} · {p.tran_label}"
            row = Adw.ActionRow(title=title, subtitle=sub)
            row.add_prefix(Gtk.Image(icon_name=pick_icon("drive-harddisk-symbolic"), pixel_size=16))
            if p.is_system_disk:
                row.add_suffix(Tag(tr("SYSTEMLAUFWERK"), "danger"))
            elif not p.fstype:
                row.add_suffix(Tag(tr("KEIN DATEISYSTEM"), "danger"))
            elif p.in_fstab:
                row.add_suffix(Tag(tr("IN FSTAB"), "ok"))
            elif p.temp_mounted:
                row.add_suffix(Tag(tr("TEMPORÄR GEMOUNTET"), "warn"))
            elif p.mountpoint:
                row.add_suffix(Tag(tr("GEMOUNTET"), None))
            else:
                row.add_suffix(Tag(tr("NICHT EINGEBUNDEN"), "accent"))
            if p.mountpoint:
                row.add_suffix(label(p.mountpoint, css=["hint"]))
            row.part = p  # type: ignore[attr-defined]
            self.listbox.append(row)
            if p.name == prev:
                select_row = row
        if select_row:
            self.listbox.select_row(select_row)
        else:
            self._clear_form()

    def _clear_form(self) -> None:
        self.sel = None
        self.fgroup.set_description(tr("Zuerst oben eine Partition wählen."))
        for r in (self.uuid_row, self.fs_row, self.mp_row, self.line_row):
            r.set_subtitle("–")
        self.name_row.set_text("")
        self.opts_row.set_text("")
        self.mount_btn.set_sensitive(False)
        self.fmt_btn.set_sensitive(False)
        self.ntfs_row.set_visible(False)

    def _on_select(self, _lb, row) -> None:
        p = getattr(row, "part", None) if row else None
        if p is None:
            self._clear_form()
            return
        self.sel = p
        self.fgroup.set_description(tr("{0} – aktuell unter {1}").format(p.path, p.mountpoint) if p.mountpoint
                                    else tr("{0} – derzeit nicht gemountet").format(p.path))
        self.uuid_row.set_subtitle(p.uuid or tr("unbekannt"))
        self.fs_row.set_subtitle(p.fstype or tr("kein Dateisystem – unten formatieren"))
        self.ntfs_row.set_visible(p.fstype == "ntfs")
        suggested = p.label or p.name
        self.name_row.set_text(suggested)
        self.base_row.set_text(self.cfg.get("MOUNT_BASE", "/mnt"))
        self.fmt_label.set_text((p.label or "DATA")[:16])
        self._fill_defaults()
        self.fmt_btn.set_sensitive(not p.is_system_disk)

    def _fill_defaults(self) -> None:
        if not self.sel:
            return
        driver = "ntfs-3g" if self.ntfs_row.get_selected() == 1 else "ntfs3"
        opts, _dp, _fst = system.mount_options_for(self.sel.fstype, self.uid, self.gid, driver)
        self.opts_row.set_text(opts)
        self._update_preview()

    def _current(self) -> tuple[str, str, str, str] | None:
        if not self.sel or not self.sel.fstype or not self.sel.uuid:
            return None
        driver = "ntfs-3g" if self.ntfs_row.get_selected() == 1 else "ntfs3"
        _opts, dump_pass, fstab_fs = system.mount_options_for(self.sel.fstype, self.uid, self.gid, driver)
        mp = system.normalize_mountpoint(self.base_row.get_text(), self.name_row.get_text())
        opts = self.opts_row.get_text().strip() or _opts
        return mp, fstab_fs, opts, dump_pass

    def _update_preview(self) -> None:
        cur = self._current()
        if not cur or not self.sel:
            self.mp_row.set_subtitle("–")
            self.line_row.set_subtitle("–")
            self.mount_btn.set_sensitive(False)
            return
        mp, fstab_fs, opts, dump_pass = cur
        self.mp_row.set_subtitle(mp)
        self.line_row.set_subtitle(system.fstab_line(self.sel.uuid, mp, fstab_fs, opts, dump_pass))
        ok = not self.sel.in_fstab and not self.sel.is_system_disk and mp not in ("/", "") and not system.fstab_has_uuid(self.sel.uuid)
        self.mount_btn.set_sensitive(ok and not self.task.busy)
        self.mount_btn.set_tooltip_text(tr("Für diese UUID existiert bereits ein fstab-Eintrag.") if self.sel.in_fstab else "")

    def _mount(self) -> None:
        cur = self._current()
        if not cur or not self.sel:
            return
        p = self.sel
        mp, fstab_fs, opts, dump_pass = cur
        line = system.fstab_line(p.uuid, mp, fstab_fs, opts, dump_pass)
        chown = (self.uid, self.gid) if system.needs_chown(fstab_fs) else None
        unmount = bool(p.mountpoint) and p.mountpoint != mp
        cmd = system.mount_shell_command(p.path, line, mp, chown, unmount)
        body = (tr("Diese Zeile wird an /etc/fstab angehängt (Backup /etc/fstab.bak.<Zeit> wird angelegt):\n\n{line}\n\n"
                   "Danach: systemctl daemon-reload, mount {mp}").format(line=line, mp=mp)
                + (f", chown {self.uid}:{self.gid} {mp}" if chown else "")
                + (tr(".\nDie temporäre Einbindung unter {0} wird vorher gelöst.").format(p.mountpoint) if unmount else "."))
        self.cfg.set("MOUNT_BASE", self.base_row.get_text().strip() or "/mnt")

        def do() -> None:
            try:
                argv = system.root_argv(cmd)
            except RuntimeError as exc:
                self.toast(str(exc))
                return
            self.task.run(argv, tr("Binde {0} unter {1} ein …").format(p.path, mp),
                          on_done=lambda rc: (self.toast(tr("{0} dauerhaft unter {1} eingebunden.").format(p.path, mp) if rc == 0
                                                         else tr("Fehler beim Einbinden – /etc/fstab prüfen (Backup vorhanden).")),
                                              self.on_show(), self.win.refresh_badges()))
        confirm(self, tr("{0} dauerhaft einbinden?").format(p.path), body, tr("Eintragen und mounten"), do)

    def _format(self) -> None:
        p = self.sel
        if not p or p.is_system_disk:
            return
        fstype = list(system.FORMAT_FS)[self.fmt_fs.get_selected()]
        label_ = self.fmt_label.get_text().strip() or "DATA"
        cmd = system.format_shell_command(p.path, fstype, label_, mounted=bool(p.mountpoint))
        entry = Gtk.Entry(placeholder_text=tr("Zur Bestätigung „{0}“ eingeben").format(p.name))
        dlg = Adw.AlertDialog(heading=tr("{0} wirklich formatieren?").format(p.path),
                              body=tr("ALLE DATEN auf {path} ({size}, {fs}{label}) werden unwiderruflich gelöscht.\n\nBefehl: {cmd}").format(
                                  path=p.path, size=system.human_bytes(p.size), fs=p.fstype or tr("ohne Dateisystem"),
                                  label=", " + p.label if p.label else "", cmd=cmd),
                              extra_child=entry)
        dlg.add_response("cancel", tr("Abbrechen"))
        dlg.add_response("ok", tr("Formatieren"))
        dlg.set_response_appearance("ok", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.set_response_enabled("ok", False)
        dlg.set_default_response("cancel")
        dlg.set_close_response("cancel")
        entry.connect("changed", lambda e: dlg.set_response_enabled("ok", e.get_text().strip() == p.name))

        def resp(_d, response: str) -> None:
            if response != "ok":
                return
            try:
                argv = system.root_argv(cmd)
            except RuntimeError as exc:
                self.toast(str(exc))
                return
            self.task.run(argv, tr("Formatiere {0} als {1} …").format(p.path, fstype),
                          on_done=lambda rc: (self.toast(tr("{0} als {1} formatiert.").format(p.path, fstype) if rc == 0
                                                         else tr("Formatieren fehlgeschlagen.")),
                                              self.on_show(), self.win.refresh_badges()))
        dlg.connect("response", resp)
        dlg.present(self)


class FoldersPage(Page):
    key = "folders"
    title = tr("Ordner anlegen")
    subtitle = tr("Ordner auf einem eingebundenen Laufwerk mit dir als Besitzer anlegen (statt root) oder den Besitz eines Ordners übernehmen.")
    icons = ("folder-new-symbolic", "folder-symbolic")

    def build(self) -> None:
        self.add_refresh_button(tr("Mountpoints neu einlesen"))
        self.uid, self.gid = os.getuid(), os.getgid()
        self.mounts: list[system.MountedFs] = []

        g = group(tr("Neuer Ordner"))
        self.mount_row = Adw.ComboRow(title=tr("Laufwerk / Mountpoint"))
        self.mount_row.set_model(Gtk.StringList.new([]))
        self.mount_row.connect("notify::selected", lambda *_: self._preview())
        pick = icon_button(pick_icon("folder-symbolic"), tr("Anderen Ordner wählen"), ["flat"], on_click=self._pick_other)
        pick.set_valign(Gtk.Align.CENTER)
        self.mount_row.add_suffix(pick)
        g.add(self.mount_row)
        self.name_row = Adw.EntryRow(title=tr("Ordnername"), text="Steam")
        self.name_row.connect("changed", lambda *_: self._preview())
        g.add(self.name_row)
        self.result_row = action_row(tr("Ergebnis"), "–", pick_icon("folder-new-symbolic"))
        g.add(self.result_row)
        self.content.append(g)
        bar = Gtk.Box()
        self.create_btn = button(tr("ORDNER ANLEGEN"), ["btn-primary"], icon=pick_icon("folder-new-symbolic"), on_click=self._create)
        bar.append(self.create_btn)
        self.content.append(bar)

        g2 = group(tr("Besitz übernehmen"), tr("Macht dich rekursiv zum Besitzer eines vorhandenen Ordners (chown -R), z. B. nach einem Mount als root."))
        self.own_row = action_row(tr("Ordner"), "–", pick_icon("folder-symbolic"))
        ob = icon_button(pick_icon("folder-symbolic"), tr("Ordner wählen"), ["flat"], on_click=self._pick_own)
        ob.set_valign(Gtk.Align.CENTER)
        self.own_row.add_suffix(ob)
        g2.add(self.own_row)
        self.content.append(g2)
        bar2 = Gtk.Box()
        self.own_btn = button(tr("Besitz übernehmen"), ["btn-secondary"], on_click=self._chown)
        self.own_btn.set_sensitive(False)
        bar2.append(self.own_btn)
        self.content.append(bar2)
        self.own_path = ""

        self.task = TaskRunner(min_height=100, idle_text=tr("Ausgabe der Root-Befehle erscheint hier."))
        self.content.append(self.task)
        self.other = ""

    def on_show(self) -> None:
        def done(fss: list[system.MountedFs]) -> None:
            self.mounts = [m for m in fss if m.fstype not in ("tmpfs", "devtmpfs", "efivarfs", "autofs", "overlay", "vfat") and m.target not in ("/boot", "/efi")]
            names = [tr("{0}  ({1}, {2} frei)").format(m.target, m.fstype, system.human_bytes(m.avail)) for m in self.mounts]
            if self.other:
                names.append(tr("{0}  (gewählt)").format(self.other))
            self.mount_row.set_model(Gtk.StringList.new(names or [tr("(keine Mountpoints)")]))
            for i, m in enumerate(self.mounts):
                if m.target.startswith("/mnt") or m.target.startswith("/media"):
                    self.mount_row.set_selected(i)
                    break
            self._preview()
        run_async(system.mounted_filesystems, done)

    def _base(self) -> str:
        i = self.mount_row.get_selected()
        if 0 <= i < len(self.mounts):
            return self.mounts[i].target
        if self.other and i == len(self.mounts):
            return self.other
        return ""

    def _preview(self) -> None:
        base, name = self._base(), self.name_row.get_text().strip().strip("/")
        if base and name:
            full = system.normalize_mountpoint(base, name)
            exists = Path(full).exists()
            self.result_row.set_subtitle(tr("{0} – existiert bereits").format(full) if exists
                                         else tr("{0} (Besitzer {1})").format(full, os.environ.get("USER", self.uid)))
            self.create_btn.set_sensitive(not exists)
        else:
            self.result_row.set_subtitle("–")
            self.create_btn.set_sensitive(False)

    def _pick_other(self) -> None:
        def picked(p: str) -> None:
            self.other = p
            self.on_show()
            self.mount_row.set_selected(len(self.mounts))
        choose_folder(self.win, tr("Basisordner wählen"), "/mnt", picked)

    def _create(self) -> None:
        base, name = self._base(), self.name_row.get_text().strip().strip("/")
        if not base or not name:
            return
        full = system.normalize_mountpoint(base, name)
        if not system.is_mountpoint(base) and not Path(base).is_dir():
            self.toast(tr("{0} ist kein gültiger Ordner.").format(base))
            return
        try:
            argv = system.root_argv(system.create_folder_shell_command(full, self.uid, self.gid))
        except RuntimeError as exc:
            self.toast(str(exc))
            return
        self.task.run(argv, tr("Lege {0} an …").format(full),
                      on_done=lambda rc: (self.toast(tr("Angelegt: {0}").format(full) if rc == 0 else tr("Fehler beim Anlegen.")), self._preview()))

    def _pick_own(self) -> None:
        def picked(p: str) -> None:
            self.own_path = p
            self.own_row.set_subtitle(p)
            self.own_btn.set_sensitive(True)
        choose_folder(self.win, tr("Ordner wählen"), "/mnt", picked)

    def _chown(self) -> None:
        p = self.own_path
        if not p:
            return
        q = system.shlex.quote(p)
        confirm(self, tr("Besitz übernehmen?"),
                tr("chown -R {0}:{1} {2}\n\nAlle Dateien darunter gehören danach dir.").format(self.uid, self.gid, p), tr("Übernehmen"),
                lambda: self.task.run(system.root_argv(f"chown -R {self.uid}:{self.gid} {q}"), tr("Übernehme Besitz von {0} …").format(p),
                                      on_done=lambda rc: self.toast(tr("Besitz übernommen.") if rc == 0 else tr("Fehler bei chown."))))
