"""Einstieg: python3 -m cachy_toolkit [--launch DATEI]"""
from __future__ import annotations

import argparse
import logging
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from . import __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cachy-toolkit", description="CachyOS Alltagstool (GTK4/libadwaita)")
    parser.add_argument("--launch", metavar="DATEI", help=".exe/.msi direkt mit Prefix-Auswahl öffnen")
    parser.add_argument("--run", metavar="ANWENDUNG", help="registrierte Anwendung (Verknüpfung) ohne Oberfläche starten")
    parser.add_argument("--page", metavar="SEITE", help="mit dieser Seite starten (z. B. prefixes)")
    parser.add_argument("--prefixes", action="store_const", const="prefixes", dest="page", help=argparse.SUPPRESS)
    parser.add_argument("--screenshot", metavar="ORDNER", help=argparse.SUPPRESS)
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
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=[logging.FileHandler(LOG_DIR / "toolkit.log", encoding="utf-8"),
                                  logging.StreamHandler(sys.stderr)])
    logging.getLogger().handlers[1].setLevel(logging.WARNING)

    from gi.repository import GLib
    from . import APP_ID, APP_NAME
    GLib.set_prgname(APP_ID)          # WM_CLASS / StartupWMClass → richtiges Icon in Taskleiste und Fensterwechsler
    GLib.set_application_name(APP_NAME)
    from .config import Config
    cfg = Config()
    cfg.ensure_dirs()
    if args.run:
        from .launcher import LaunchError, Launcher
        from .registry import Registry
        reg = Registry()
        entry = reg.get_app(args.run)
        if not entry:
            print(f"Anwendung „{args.run}“ ist nicht registriert.", file=sys.stderr)
            return 2
        try:
            launcher = Launcher(cfg, reg)
            plan = launcher.app_plan(entry)
            launcher.launch(plan, f"app-{entry.id}")
        except LaunchError as exc:
            print(f"Start fehlgeschlagen: {exc}", file=sys.stderr)
            return 1
        return 0
    from .ui.app import ToolkitApp
    app = ToolkitApp(cfg, launch_file=args.launch, screenshot_dir=args.screenshot, start_page=args.page)
    return app.run([sys.argv[0]])


if __name__ == "__main__":
    sys.exit(main())
