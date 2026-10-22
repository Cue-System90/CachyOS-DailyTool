"""Verknüpfungen für Anwendungen im Startmenü und auf dem Schreibtisch; Icon-Extraktion aus .exe (pefile + Pillow)."""
from __future__ import annotations

import io
import logging
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

from . import APP_ID
from .registry import AppEntry
from .i18n import tr
from .system import DATA_DIR

log = logging.getLogger(__name__)

APPS_DIR = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "applications"
ICON_DIR = DATA_DIR / "icons"
_SAFE = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_./:=,+@%")


def desktop_dir() -> Path:
    try:
        out = subprocess.run(["xdg-user-dir", "DESKTOP"], capture_output=True, text=True, timeout=5).stdout.strip()
        if out and Path(out).is_dir() and Path(out) != Path.home():
            return Path(out)
    except (OSError, subprocess.SubprocessError):
        pass
    for name in ("Schreibtisch", "Desktop"):
        p = Path.home() / name
        if p.is_dir():
            return p
    return Path.home() / "Desktop"


def desktop_file_name(app_id: str) -> str:
    return f"cachy-app-{app_id}.desktop"


def menu_path(app_id: str) -> Path:
    return APPS_DIR / desktop_file_name(app_id)


def desktop_path(app_id: str) -> Path:
    return desktop_dir() / desktop_file_name(app_id)


def has_menu(app_id: str) -> bool:
    return menu_path(app_id).exists()


def has_desktop(app_id: str) -> bool:
    return desktop_path(app_id).exists()


def exec_quote(arg: str) -> str:
    if arg and all(c in _SAFE for c in arg):
        return arg
    out = arg.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`")
    return '"' + out + '"'


def launcher_command() -> str:
    """Startbefehl des Tools für Exec= (Paket, Benutzer-Installation oder Quellbaum)."""
    exe = shutil.which("cachyos-dailytool")
    if exe:
        return exec_quote(exe)
    user_bin = Path.home() / ".local" / "bin" / "cachyos-dailytool"
    if user_bin.exists():
        return exec_quote(str(user_bin))
    pkg_root = Path(__file__).resolve().parents[1]
    return f"env {exec_quote('PYTHONPATH=' + str(pkg_root))} {exec_quote(sys.executable)} -m cachyos_dailytool"


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", " ").strip()


def desktop_entry(app: AppEntry) -> str:
    cmd = launcher_command()
    lines = [
        "[Desktop Entry]", "Type=Application", "Version=1.5",
        f"Name={_escape(app.name)}",
        "Comment=" + _escape(tr("Über CachyOS DailyTool starten ({0})").format(Path(app.prefix).name)),
        f"Exec={cmd} --run {exec_quote(app.id)}",
        f"Icon={app.icon or 'wine'}",
        "Terminal=false", "Categories=Game;", "StartupNotify=true",
        f"X-Cachy-Prefix={_escape(app.prefix)}",
        "Actions=Prefix;",
        "", "[Desktop Action Prefix]", "Name=" + tr("Prefix in CachyOS DailyTool öffnen"),
        f"Exec={cmd} --prefixes",
    ]
    return "\n".join(lines) + "\n"


def create(app: AppEntry, menu: bool, desktop: bool) -> list[Path]:
    content = desktop_entry(app)
    written: list[Path] = []
    if menu:
        APPS_DIR.mkdir(parents=True, exist_ok=True)
        p = menu_path(app.id)
        p.write_text(content, encoding="utf-8")
        p.chmod(0o755)
        written.append(p)
    elif menu_path(app.id).exists():
        menu_path(app.id).unlink()
    if desktop:
        d = desktop_dir()
        d.mkdir(parents=True, exist_ok=True)
        p = desktop_path(app.id)
        p.write_text(content, encoding="utf-8")
        p.chmod(0o755)   # KDE/GNOME verlangen ausführbare .desktop-Dateien auf dem Schreibtisch
        written.append(p)
    elif desktop_path(app.id).exists():
        desktop_path(app.id).unlink()
    _refresh_menu()
    return written


def sync_all(apps) -> list[Path]:
    """Vorhandene Verknüpfungen neu schreiben, wenn ihr Inhalt veraltet ist (z. B. anderer Startbefehl nach einer
    Umbenennung oder einem Wechsel zwischen Paket- und Benutzer-Installation). Legt keine neuen an."""
    written: list[Path] = []
    for app in apps:
        targets = [p for p in (menu_path(app.id), desktop_path(app.id)) if p.exists()]
        if not targets:
            continue
        content = desktop_entry(app)
        for p in targets:
            try:
                if p.read_text(encoding="utf-8") != content:
                    p.write_text(content, encoding="utf-8")
                    p.chmod(0o755)
                    written.append(p)
            except OSError as exc:
                log.warning("Verknüpfung %s konnte nicht erneuert werden: %s", p, exc)
    if written:
        _refresh_menu()
    return written


def remove(app_id: str) -> list[Path]:
    removed: list[Path] = []
    for p in (menu_path(app_id), desktop_path(app_id)):
        if p.exists():
            try:
                p.unlink()
                removed.append(p)
            except OSError as exc:
                log.warning("Verknüpfung %s konnte nicht entfernt werden: %s", p, exc)
    for f in ICON_DIR.glob(f"{app_id}.*"):
        try:
            f.unlink()
        except OSError:
            pass
    if removed:
        _refresh_menu()
    return removed


def _refresh_menu() -> None:
    tool = shutil.which("update-desktop-database")
    if tool:
        try:
            subprocess.run([tool, str(APPS_DIR)], capture_output=True, timeout=20)
        except (OSError, subprocess.SubprocessError):
            pass
    k = shutil.which("kbuildsycoca6")
    if k:
        try:
            subprocess.run([k], capture_output=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            pass


# --------------------------------------------------------------------------- Icon aus .exe
def extract_icon(exe: str | Path, app_id: str) -> str | None:
    """Größtes Icon aus den PE-Ressourcen holen und als PNG ablegen (pefile + Pillow). None, wenn nicht möglich."""
    try:
        import pefile  # type: ignore
        from PIL import Image  # type: ignore
    except ImportError:
        log.info("pefile/Pillow fehlen – kein Icon aus %s", exe)
        return None
    try:
        pe = pefile.PE(str(exe), fast_load=True)
        pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_RESOURCE"]])
    except Exception as exc:
        log.info("PE-Datei %s nicht lesbar: %s", exe, exc)
        return None
    try:
        root = pe.DIRECTORY_ENTRY_RESOURCE
    except AttributeError:
        return None
    RT_ICON, RT_GROUP_ICON = 3, 14
    icons: dict[int, bytes] = {}
    group: bytes | None = None
    for entry in root.entries:
        if entry.id == RT_ICON:
            for e in entry.directory.entries:
                for lang in e.directory.entries:
                    data = pe.get_data(lang.data.struct.OffsetToData, lang.data.struct.Size)
                    icons[e.id] = data
        elif entry.id == RT_GROUP_ICON and group is None:
            e = entry.directory.entries[0]
            lang = e.directory.entries[0]
            group = pe.get_data(lang.data.struct.OffsetToData, lang.data.struct.Size)
    if not group or not icons:
        return None
    try:
        _res, _type, count = struct.unpack_from("<HHH", group, 0)
        entries = []
        for i in range(count):
            w, h, colors, _r, planes, bits, size, icon_id = struct.unpack_from("<BBBBHHIH", group, 6 + i * 14)
            entries.append(((w or 256) * (h or 256), bits, w, h, colors, planes, bits, size, icon_id))
        entries.sort(reverse=True)
        best = None
        for _area, _bits, w, h, colors, planes, bits, size, icon_id in entries:
            if icon_id in icons:
                best = (w, h, colors, planes, bits, icons[icon_id])
                break
        if best is None:
            return None
        w, h, colors, planes, bits, data = best
        ico = struct.pack("<HHH", 0, 1, 1) + struct.pack("<BBBBHHII", w, h, colors, 0, planes, bits, len(data), 22) + data
        img = Image.open(io.BytesIO(ico))
        img = img.convert("RGBA")
        if max(img.size) > 256:
            img.thumbnail((256, 256))
        ICON_DIR.mkdir(parents=True, exist_ok=True)
        target = ICON_DIR / f"{app_id}.png"
        img.save(target, "PNG")
        return str(target)
    except Exception as exc:
        log.info("Icon aus %s konnte nicht gelesen werden: %s", exe, exc)
        return None
