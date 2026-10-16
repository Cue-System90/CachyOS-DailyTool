"""Anbindung an GOverlay / MangoHud / vkBasalt / GameMode / Gamescope (wie im Galaxy-Port).

GOverlay erzeugt für Steam eine Startoptionszeile wie ``ENABLE_VKBASALT=1 mangohud %command%``.
Dieses Tool versteht exakt diese Zeile und leitet sie zusätzlich aus der vorhandenen
GOverlay-/MangoHud-Konfiguration ab.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import launch_options

HOME = Path.home()
CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME", HOME / ".config"))
MANGOHUD_CONF = CONFIG_HOME / "MangoHud" / "MangoHud.conf"
VKBASALT_CONF = CONFIG_HOME / "vkBasalt" / "vkBasalt.conf"


@dataclass(frozen=True)
class ToolStatus:
    goverlay: bool
    mangohud: bool
    mangohud32: bool
    vkbasalt: bool
    gamemode: bool
    gamescope: bool
    mangohud_config: bool
    vkbasalt_config: bool


def _vulkan_layer_present(needle: str) -> bool:
    for d in (Path("/usr/share/vulkan/implicit_layer.d"), Path("/usr/share/vulkan/explicit_layer.d"),
              Path("/etc/vulkan/implicit_layer.d"), HOME / ".local/share/vulkan/implicit_layer.d"):
        try:
            for f in d.iterdir():
                if needle.lower() in f.name.lower():
                    return True
        except OSError:
            continue
    return False


def status() -> ToolStatus:
    return ToolStatus(
        goverlay=shutil.which("goverlay") is not None,
        mangohud=shutil.which("mangohud") is not None,
        mangohud32=Path("/usr/lib32/mangohud/libMangoHud.so").exists() or Path("/usr/lib32/libMangoHud.so").exists(),
        vkbasalt=_vulkan_layer_present("vkbasalt"),
        gamemode=shutil.which("gamemoderun") is not None,
        gamescope=shutil.which("gamescope") is not None,
        mangohud_config=MANGOHUD_CONF.exists(),
        vkbasalt_config=VKBASALT_CONF.exists(),
    )


def build_launch_options(mangohud: bool = True, vkbasalt: bool = False, gamemode: bool = False,
                         gamescope_args: str = "", extra_env: dict[str, str] | None = None, args: str = "") -> str:
    parts: list[str] = []
    if vkbasalt:
        parts.append("ENABLE_VKBASALT=1")
    for k, v in (extra_env or {}).items():
        parts.append(f"{k}={v}")
    if gamescope_args:
        parts.append(f"gamescope {gamescope_args.strip()} --")
    if gamemode:
        parts.append("gamemoderun")
    if mangohud:
        parts.append("mangohud")
    parts.append(launch_options.COMMAND_TOKEN)
    if args:
        parts.append(args.strip())
    return " ".join(parts)


def import_from_goverlay() -> str:
    """Dieselbe Zeile, die GOverlay unter „Steam launch options“ anzeigt."""
    st = status()
    use_mangohud = st.mangohud and (st.mangohud_config or st.goverlay)
    use_vkbasalt = st.vkbasalt and st.vkbasalt_config
    return build_launch_options(mangohud=use_mangohud, vkbasalt=use_vkbasalt)


PRESETS: list[tuple[str, str]] = [
    ("MangoHud (GOverlay-Standard)", "mangohud %command%"),
    ("MangoHud + vkBasalt", "ENABLE_VKBASALT=1 mangohud %command%"),
    ("MangoHud + GameMode", "gamemoderun mangohud %command%"),
    ("MangoHud nur per Variable", "MANGOHUD=1 %command%"),
    ("Gamescope Vollbild + MangoHud", "gamescope -f -- mangohud %command%"),
    ("Proton-Log aktivieren", "PROTON_LOG=1 %command%"),
    ("DXVK-HUD (FPS)", "DXVK_HUD=fps %command%"),
    ("Wayland nativ (Proton 9+)", "PROTON_ENABLE_WAYLAND=1 %command%"),
    ("Wine-Debug-Ausgabe aus", "WINEDEBUG=-all %command%"),
    ("Keine Startoptionen", ""),
]


def open_goverlay() -> bool:
    exe = shutil.which("goverlay")
    if not exe:
        return False
    env = {k: v for k, v in os.environ.items() if k not in ("LD_PRELOAD", "LD_LIBRARY_PATH")}
    try:
        subprocess.Popen([exe], env=env, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError:
        return False
