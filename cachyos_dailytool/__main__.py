"""Einstieg: python3 -m cachyos_dailytool [--launch DATEI | --run ANWENDUNG | --x3d-daemon | --x3d-status | --x3d-unit | --refresh-shortcuts]"""
from __future__ import annotations

import argparse
import logging
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from . import __version__


def main(argv: list[str] | None = None) -> int:
    from . import i18n
    from .config import Config
    cfg = Config()
    i18n.setup(setting=cfg.get("UI_LANGUAGE", "auto"))   # vor allen UI-Importen – Modulkonstanten werden beim Import übersetzt
    from .i18n import tr
    parser = argparse.ArgumentParser(prog="cachyos-dailytool", description=tr("CachyOS DailyTool (GTK4/libadwaita)"))
    parser.add_argument("--launch", metavar=tr("DATEI"), help=tr(".exe/.msi direkt mit Prefix-Auswahl öffnen"))
    parser.add_argument("--run", metavar=tr("ANWENDUNG"), help=tr("registrierte Anwendung (Verknüpfung) ohne Oberfläche starten"))
    parser.add_argument("--page", metavar=tr("SEITE"), help=tr("mit dieser Seite starten (z. B. prefixes)"))
    parser.add_argument("--prefixes", action="store_const", const="prefixes", dest="page", help=argparse.SUPPRESS)
    parser.add_argument("--screenshot", metavar="ORDNER", help=argparse.SUPPRESS)
    parser.add_argument("--x3d-daemon", action="store_true", help=tr("X3D-Kernzuteilung als Hintergrunddienst ausführen (ohne Oberfläche)"))
    parser.add_argument("--x3d-dry-run", action="store_true", help=tr("wie --x3d-daemon, aber nur protokollieren (keine Zuweisung)"))
    parser.add_argument("--x3d-status", action="store_true", help=tr("Topologie, Rangfolge und erkannte Spiele ausgeben"))
    parser.add_argument("--x3d-unit", action="store_true", help=argparse.SUPPRESS)            # Benutzer-Unit (neu) schreiben
    parser.add_argument("--refresh-shortcuts", action="store_true", help=argparse.SUPPRESS)   # Verknüpfungen neu schreiben
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args, rest = parser.parse_known_args(sys.argv[1:] if argv is None else argv)
    # Datei als positionales Argument (z. B. über „Öffnen mit“ / %f) wie --launch behandeln
    positional = [r for r in rest if not r.startswith("-")]
    if positional and not args.launch:
        args.launch = positional[0]

    import faulthandler
    import signal
    faulthandler.register(signal.SIGUSR1, all_threads=True)  # Stack-Dump nach stderr für Fehlersuche (kill -USR1 <pid>)

    from .system import LOG_DIR
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if args.x3d_daemon or args.x3d_dry_run:
        # Dienst: eigenes Protokoll, Ausgabe zusätzlich nach stderr (landet im Journal von systemd --user)
        from . import x3d
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                            handlers=[logging.FileHandler(x3d.daemon_log_path(), encoding="utf-8"),
                                      logging.StreamHandler(sys.stderr)])
        return x3d.run_daemon(dry_run=args.x3d_dry_run)
    if args.x3d_status:
        from . import x3d
        return x3d.print_status()
    if args.x3d_unit:
        # Installer: nach einem Wechsel des Installationsorts zeigt ExecStart der Unit auf den neuen Befehl
        from . import x3d
        print(x3d.install_unit())
        return 0
    if args.refresh_shortcuts:
        # Installer: Startmenü-/Schreibtisch-Verknüpfungen registrierter Anwendungen auf den neuen Befehl umschreiben
        from . import shortcuts
        from .registry import Registry
        n = 0
        for entry in Registry().apps.values():
            if entry.menu or entry.desktop:
                shortcuts.create(entry, entry.menu, entry.desktop)
                n += 1
        print(tr("{0} Verknüpfungen erneuert").format(n))
        return 0
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=[logging.FileHandler(LOG_DIR / "toolkit.log", encoding="utf-8"),
                                  logging.StreamHandler(sys.stderr)])
    logging.getLogger().handlers[1].setLevel(logging.WARNING)

    from gi.repository import GLib
    from . import APP_ID, APP_NAME
    GLib.set_prgname(APP_ID)          # WM_CLASS / StartupWMClass → richtiges Icon in Taskleiste und Fensterwechsler
    GLib.set_application_name(APP_NAME)
    cfg.ensure_dirs()
    if args.run:
        from .launcher import LaunchError, Launcher
        from .registry import Registry
        reg = Registry()
        entry = reg.get_app(args.run)
        if not entry:
            print(tr("Anwendung „{0}“ ist nicht registriert.").format(args.run), file=sys.stderr)
            return 2
        try:
            launcher = Launcher(cfg, reg)
            plan = launcher.app_plan(entry)
            launcher.launch(plan, f"app-{entry.id}")
        except LaunchError as exc:
            print(tr("Start fehlgeschlagen: {0}").format(exc), file=sys.stderr)
            return 1
        return 0
    from .ui.app import ToolkitApp
    app = ToolkitApp(cfg, launch_file=args.launch, screenshot_dir=args.screenshot, start_page=args.page)
    return app.run([sys.argv[0]])


if __name__ == "__main__":
    sys.exit(main())
