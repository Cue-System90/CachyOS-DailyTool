"""Konfiguration – kompatibel zur config.env der Bash-Version (v1–v4)."""
from __future__ import annotations

import os
import shlex
from pathlib import Path

CONF_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "cachy-daily-toolkit"
CONF_FILE = CONF_DIR / "config.env"

DEFAULTS: dict[str, str] = {
    "WINE_PREFIX_ROOT": str(Path.home() / "Games" / "wine-prefixes"),
    "GAMES_TARGET_DRIVE": str(Path.home() / "Games"),
    "PREFIX_BACKUP_DIR": str(Path.home() / "Games" / "prefix-backups"),
    "UI_WIDTH": "1180",
    "UI_HEIGHT": "760",
    "MOUNT_BASE": "/mnt",
    "GPU_AUTOREFRESH": "1",
    "LAST_PAGE": "dashboard",
    "LAUNCH_OPTIONS": "",
    "DEFAULT_TOOL": "auto",
    "PREFER_UMU": "1",
    "UI_LANGUAGE": "auto",         # Sprache der Oberfläche: auto (Systemsprache), de, en
    "AUTO_DPI": "1",                # Windows-DPI neuer/unveränderter Prefixe an die Desktop-Skalierung anpassen
    "WAYLAND_DRIVER": "1",          # Proton-Wayland-Treiber (PROTON_ENABLE_WAYLAND=1) als Vorgabe – KDE-Wayland ist Standard
    # X3D-Kernzuteilung (siehe x3d.py): Erkennung aktiv, Vorgabe für unbekannte Spiele, Rangfolge ohne Spiel,
    # Hintergrundprogramme auf den anderen CCD, Zuweisung erzwingen, zusätzliche Spieleordner
    "X3D_ENABLED": "1",
    "X3D_DEFAULT_MODE": "cache",
    "X3D_IDLE_MODE": "frequency",
    "X3D_BACKGROUND": "1",
    "X3D_ENFORCE": "1",
    "X3D_GAME_DIRS": "",
}


class Config:
    """Liest/schreibt KEY="value"-Zeilen. Unbekannte Schlüssel bleiben erhalten."""

    def __init__(self, path: Path | str = CONF_FILE) -> None:
        self.path = Path(path)
        self.values: dict[str, str] = dict(DEFAULTS)
        self.load()

    def load(self) -> None:
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError:
            return
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            if not key.replace("_", "").isalnum():
                continue
            try:
                parts = shlex.split(val, posix=True)
                val = parts[0] if parts else ""
            except ValueError:
                val = val.strip().strip('"').strip("'")
            self.values[key] = os.path.expanduser(val) if key.endswith(("_ROOT", "_DRIVE", "_DIR")) else val

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lines = [f'{k}="{v}"' for k, v in sorted(self.values.items())]
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        tmp.replace(self.path)

    def get(self, key: str, default: str = "") -> str:
        return self.values.get(key, default)

    def set(self, key: str, value: str, save: bool = True) -> None:
        self.values[key] = str(value)
        if save:
            self.save()

    # Komfort-Eigenschaften -------------------------------------------------
    @property
    def wine_prefix_root(self) -> Path:
        return Path(self.values["WINE_PREFIX_ROOT"])

    @property
    def games_target(self) -> Path:
        return Path(self.values["GAMES_TARGET_DRIVE"])

    @property
    def backup_dir(self) -> Path:
        return Path(self.values["PREFIX_BACKUP_DIR"])

    @property
    def window_size(self) -> tuple[int, int]:
        try:
            w, h = int(self.values["UI_WIDTH"]), int(self.values["UI_HEIGHT"])
        except ValueError:
            w, h = 1180, 760
        return max(900, w), max(600, h)

    def set_window_size(self, w: int, h: int) -> None:
        self.values["UI_WIDTH"], self.values["UI_HEIGHT"] = str(w), str(h)
        self.save()

    def ensure_dirs(self) -> None:
        for p in (self.wine_prefix_root, self.games_target, self.backup_dir):
            try:
                p.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass
