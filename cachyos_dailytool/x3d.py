"""X3D-Kernzuteilung: Spiele erkennen und dem Cache- oder Frequenz-CCD zuweisen (ohne GTK).

Vorbild ist AMDs Windows-Lösung für Ryzen mit zwei CCDs (7950X3D/7900X3D, 9950X3D/9900X3D):
- „3D V-Cache Performance Optimizer“: Läuft ein Spiel (dort über die Xbox Game Bar erkannt), stellt der Treiber die
  CPPC-Rangfolge der Kerne auf den CCD mit dem großen L3-Cache um („prefer cache“); ohne Spiel gilt „prefer frequency“.
  Zusätzlich wird der zweite CCD für das Spiel „geparkt“, damit dessen Threads nicht zwischen den CCDs wandern.
- „Application Compatibility Database“: Liste bekannter Programme mit fester Zuordnung (Cache / Frequenz / keine).

Linux-Umsetzung in diesem Modul:
- Topologie aus sysfs: L3-Größe je CCD; der CCD mit dem größeren L3 ist der Cache-CCD.
- Rangfolge der Kerne über den Kernel-Treiber amd_3d_vcache (Kernel ≥ 6.13): /sys/bus/platform/drivers/amd_x3d_vcache/
  AMDI0101:00/amd_x3d_mode nimmt „cache“ oder „frequency“ an (BIOS-Einstellung „CPPC Dynamic Preferred Cores“ = Driver/Auto).
  Das Schreiben braucht Root; eine einmalige tmpfiles.d-Regel gibt der Gruppe des Nutzers Schreibrecht.
- „Parken“ = CPU-Affinität des gesamten Prozessbaums eines Spiels (alle Threads) auf den gewünschten CCD; für eigene
  Prozesse braucht sched_setaffinity keine Rechte. Hintergrundprogramme (Steam, Browser, Launcher) wandern auf den anderen CCD.
- Erkennung (Ersatz für die Game Bar): Wine/Proton-Programme mit offenem GPU-Gerät (DXVK/VKD3D), Launcher-Markierungen in der
  Umgebung (SteamAppId, UMU_ID/GAMEID, HEROIC_APP_NAME, LUTRIS_GAME_UUID), native Spiele in Spieleordnern; Katalog mit
  Ausschlüssen (Store-Clients, Wine-Systemprozesse, Installer) und eigenen Festlegungen des Nutzers.

Der Hintergrunddienst (``cachyos-dailytool --x3d-daemon``) schreibt seinen Zustand nach STATE_FILE; die Oberfläche liest ihn.
"""
from __future__ import annotations

import fcntl
import grp
import json
import logging
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from .config import CONF_DIR, Config
from .i18n import N_, tr
from .system import DATA_DIR, LOG_DIR

log = logging.getLogger(__name__)

MODES = ("cache", "frequency", "none")
# bleibt deutsch (Dienstprotokoll, Startprotokoll des Launchers) – Anzeige mit tr(MODE_LABELS[…])
MODE_LABELS = {"cache": N_("Cache-CCD"), "frequency": N_("Frequenz-CCD"), "none": N_("keine Zuweisung"), "": N_("Automatisch")}
IDLE_MODES = ("frequency", "cache", "keep")
KINDS = ("game", "launcher", "background", "system")
# Werte landen deutsch im Zustand des Dienstes (source_label) – Anzeige mit tr()
SOURCE_LABELS = {"steam": "Steam", "umu": "umu/Proton", "heroic": "Heroic", "lutris": "Lutris", "wine": "Wine",
                 "native": N_("Nativ"), "registry": "DailyTool"}
# deutschsprachige Anzeigenamen aus data/x3d_catalog.json – nur für den Übersetzungskatalog (Anzeige mit tr(name))
_CATALOG_NAMES = (N_("Wine-Systemprozesse"), N_("Steam (Windows-Teil in Proton)"), N_("Epic-Port (epic-linux)"),
                  N_("Galaxy-Port (galaxy-linux)"), N_("Ubisoft-Port (ubiconnect-linux)"), N_("Sonstige Hintergrundprogramme"),
                  N_("Indiana Jones und der Große Kreis"))

SYS_CPU = Path("/sys/devices/system/cpu")
CPUINFO = Path("/proc/cpuinfo")
MODE_DRIVER_DIR = Path("/sys/bus/platform/drivers/amd_x3d_vcache")
MODE_DEVICE = Path("/sys/devices/platform/AMDI0101:00/amd_x3d_mode")
CATALOG_FILE = Path(__file__).parent / "data" / "x3d_catalog.json"
USER_FILE = CONF_DIR / "x3d.json"
STATE_DIR = DATA_DIR / "x3d"
STATE_FILE = STATE_DIR / "state.json"
LOCK_FILE = STATE_DIR / "daemon.lock"
UNIT_NAME = "cachyos-dailytool-x3d.service"
TMPFILES_CONF = "/etc/tmpfiles.d/cachyos-dailytool-x3d.conf"
INTERVAL = 1.5
STATE_MAX_AGE = 8.0           # Sekunden: älterer Zustand = Dienst läuft nicht

CONFIG_DEFAULTS: dict[str, str] = {
    "X3D_ENABLED": "1",            # Erkennung/Zuweisung aktiv (der Dienst selbst wird über systemd --user gestartet)
    "X3D_DEFAULT_MODE": "cache",   # unbekannte Spiele: cache | frequency | none
    "X3D_IDLE_MODE": "frequency",  # Rangfolge ohne Spiel: frequency (AMD-Vorgabe) | cache | keep (nicht anfassen)
    "X3D_BACKGROUND": "1",         # Hintergrundprogramme auf den anderen CCD, solange ein Spiel läuft
    "X3D_ENFORCE": "1",            # Zuweisung erneuern, wenn ein Spiel eigene Affinität außerhalb des CCD setzt
    "X3D_GAME_DIRS": "",           # zusätzliche Spieleordner (durch : getrennt) für native Spiele
}

# Windows-Programme, die nie Spiele sind (Installer, Laufzeitkomponenten, Absturzmelder) – Teilstring im Dateinamen
NON_GAME_RE = re.compile(r"(^setup|^install|^unins|setup\.exe$|installer|vcredist|vc_redist|dxsetup|dotnet|prereq|redist"
                         r"|crashreport|crashhandler|crashpad|crash_handler|easyanticheat|anticheat|beservice|^wine|^msi"
                         r"|^python|^uninstall|updater|update\.exe$|^patcher|bootstrapper|launcher_helper|webhelper|webview2"
                         r"|^cef|^dotnetfx|^directx)", re.I)
# native Prozesse, die Spiele nur umhüllen (Steam-Runtime, gamescope, Wrapper) – nie selbst als Spiel zählen
NATIVE_WRAPPERS = {"reaper", "pressure-vessel-adverb", "pv-bwrap", "bwrap", "srt-bwrap", "steam-runtime-launcher-service",
                   "steam-runtime-launcher-interface-0", "steam-runtime-launch-client", "gamescope", "gamescopereaper",
                   "gamescope-wl", "mangoapp", "gamemoderun", "gamemoded", "umu-run", "umu_run.py", "python", "python3",
                   "sh", "bash", "zsh", "fish", "env", "taskset", "nice", "lutris-wrapper", "heroic", "steam", "steam.sh",
                   "wineserver", "wine", "wine64", "wine-preloader", "wine64-preloader", "xdg-open", "setsid"}
SYSTEM_PREFIXES = ("/usr/", "/bin/", "/sbin/", "/lib", "/opt/", "/app/", "/nix/", "/etc/", "/var/lib/flatpak/")
RUNTIME_MARKERS = ("/pressure-vessel/", "/SteamLinuxRuntime", "/ubuntu12_", "/steam-runtime", "/.steam/root/",
                   "/steamapps/common/Proton", "/compatibilitytools.d/", "/proton-", "/umu/")
# Umgebungsvariablen, die einen Prozessbaum als eine Spielsitzung zusammenhalten (Reihenfolge = Vorrang)
GROUP_ENV = ("SteamAppId", "STEAM_COMPAT_APP_ID", "UMU_ID", "GAMEID", "HEROIC_APP_NAME", "LUTRIS_GAME_UUID",
             "STEAM_COMPAT_DATA_PATH", "WINEPREFIX")
# Oberhalb dieser Prozesse endet die Suche nach der Sitzungswurzel
ROOT_STOP = {"systemd", "init", "plasmashell", "kwin_wayland", "kwin_x11", "gnome-shell", "sddm", "login", "konsole",
             "cachyos-dailytool", "cachy-toolkit", "steam", "steamwebhelper", "heroic", "lutris", "epic-linux", "galaxy-linux",
             "ubiconnect-linux", "dolphin", "nautilus", "krunner", "sshd", "tmux: server", "code", "claude-desktop"}


# ====================================================================== Topologie
def parse_cpu_list(text: str) -> list[int]:
    """„0-7,16-23“ → [0, …, 7, 16, …, 23]."""
    out: list[int] = []
    for part in (text or "").replace("\n", "").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return sorted(set(out))


def format_cpu_list(cpus: Iterable[int]) -> str:
    """[0, 1, 2, 7, 16] → „0-2,7,16“."""
    cs = sorted(set(int(c) for c in cpus))
    if not cs:
        return ""
    parts: list[str] = []
    start = prev = cs[0]
    for c in cs[1:] + [None]:
        if c is not None and c == prev + 1:
            prev = c
            continue
        parts.append(f"{start}-{prev}" if prev > start else str(start))
        if c is not None:
            start = prev = c
    return ",".join(parts)


def _parse_size(text: str) -> int:
    t = (text or "").strip().upper()
    m = re.match(r"^(\d+)\s*([KMG]?)B?$", t)
    if not m:
        return 0
    n, unit = int(m.group(1)), m.group(2)
    return n * {"": 1, "K": 1024, "M": 1024 ** 2, "G": 1024 ** 3}[unit]


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


@dataclass
class Ccd:
    id: int
    cpus: list[int]
    l3_bytes: int
    cores: int = 0

    @property
    def cpu_list(self) -> str:
        return format_cpu_list(self.cpus)

    @property
    def l3_text(self) -> str:
        mb = self.l3_bytes / (1024 ** 2)
        return f"{mb:.0f} MB L3"


@dataclass
class Topology:
    cpu_model: str = ""
    ccds: list[Ccd] = field(default_factory=list)

    @property
    def all_cpus(self) -> list[int]:
        return sorted({c for ccd in self.ccds for c in ccd.cpus})

    @property
    def dual(self) -> bool:
        """Zwei CCDs mit unterschiedlich großem L3 (X3D mit einem Cache-Die)."""
        sizes = {c.l3_bytes for c in self.ccds}
        return len(self.ccds) >= 2 and len(sizes) >= 2

    @property
    def cache_ccd(self) -> Ccd | None:
        if not self.dual:
            return None
        return max(self.ccds, key=lambda c: c.l3_bytes)

    @property
    def freq_ccd(self) -> Ccd | None:
        """Alle Kerne außerhalb des Cache-CCD (bei mehr als zwei CCDs zusammengefasst)."""
        cache = self.cache_ccd
        if cache is None:
            return None
        rest = [c for c in self.ccds if c.id != cache.id]
        if len(rest) == 1:
            return rest[0]
        return Ccd(id=-1, cpus=sorted({c for r in rest for c in r.cpus}), l3_bytes=max(r.l3_bytes for r in rest),
                   cores=sum(r.cores for r in rest))

    def ccd_of(self, cpu: int) -> int:
        """CCD-Nummer einer CPU (-1, wenn unbekannt)."""
        for c in self.ccds:
            if cpu in c.cpus:
                return c.id
        return -1

    def ccd_ids_for(self, mode: str) -> set[int]:
        """CCDs, die vollständig im Zielbereich des Modus liegen."""
        cpus = self.cpus_for(mode)
        if not cpus:
            return {c.id for c in self.ccds}
        return {c.id for c in self.ccds if set(c.cpus) <= cpus}

    def cpus_for(self, mode: str) -> set[int] | None:
        if mode == "cache" and self.cache_ccd:
            return set(self.cache_ccd.cpus)
        if mode == "frequency" and self.freq_ccd:
            return set(self.freq_ccd.cpus)
        return None

    def other_cpus(self, mode: str) -> set[int] | None:
        """Kerne des jeweils anderen CCD (für Hintergrundprogramme)."""
        if mode == "cache":
            return self.cpus_for("frequency")
        if mode == "frequency":
            return self.cpus_for("cache")
        return None

    def summary(self, localized: bool = True) -> str:
        """Kurzbeschreibung der CCDs; localized=False liefert sie deutsch (für das Dienstprotokoll)."""
        if not self.ccds:
            msg = N_("Keine Cache-Topologie gefunden")
            return tr(msg) if localized else msg
        fmt = N_("CCD{0}: {1} Kerne, {2}")
        fmt = tr(fmt) if localized else fmt
        parts = [fmt.format(c.id, c.cores or len(c.cpus), c.l3_text) for c in sorted(self.ccds, key=lambda c: c.id)]
        return " · ".join(parts)

    def to_dict(self) -> dict:
        return {"cpu_model": self.cpu_model, "dual": self.dual,
                "ccds": [asdict(c) for c in sorted(self.ccds, key=lambda c: c.id)],
                "cache": self.cache_ccd.id if self.cache_ccd else None}


def read_topology(sys_cpu: Path = SYS_CPU, cpuinfo: Path = CPUINFO) -> Topology:
    """L3-Instanzen aus sysfs: eine je CCD (Zen) – Größe, zugehörige CPUs, physische Kerne."""
    ccds: dict[int, Ccd] = {}
    siblings: dict[int, frozenset[int]] = {}
    for cpu_dir in sorted(sys_cpu.glob("cpu[0-9]*"), key=lambda p: int(p.name[3:])):
        n = int(cpu_dir.name[3:])
        sib = _read(cpu_dir / "topology" / "thread_siblings_list") or _read(cpu_dir / "topology" / "core_cpus_list")
        siblings[n] = frozenset(parse_cpu_list(sib) or [n])
        for idx in sorted(cpu_dir.glob("cache/index*")):
            if _read(idx / "level") != "3":
                continue
            shared = parse_cpu_list(_read(idx / "shared_cpu_list"))
            if not shared:
                continue
            raw_id = _read(idx / "id")
            cid = int(raw_id) if raw_id.isdigit() else (shared[0] // max(len(shared), 1))
            if cid not in ccds:
                ccds[cid] = Ccd(id=cid, cpus=shared, l3_bytes=_parse_size(_read(idx / "size")))
    for ccd in ccds.values():
        ccd.cores = len({siblings.get(c, frozenset([c])) for c in ccd.cpus})
    model = ""
    for line in _read(cpuinfo).splitlines():
        if line.lower().startswith("model name"):
            model = line.split(":", 1)[1].strip()
            break
    return Topology(cpu_model=re.sub(r"\s+", " ", model), ccds=list(ccds.values()))


_topology: Topology | None = None


def topology(refresh: bool = False) -> Topology:
    global _topology
    if _topology is None or refresh:
        try:
            _topology = read_topology()
        except Exception:  # noqa: BLE001 – Topologie ist Komfort
            log.exception("CPU-Topologie konnte nicht gelesen werden")
            _topology = Topology()
    return _topology


# ====================================================================== Rangfolge (Kernel-Treiber amd_3d_vcache)
def mode_path() -> Path | None:
    try:
        for p in sorted(MODE_DRIVER_DIR.glob("*/amd_x3d_mode")):
            if p.exists():
                return p
    except OSError:
        pass
    return MODE_DEVICE if MODE_DEVICE.exists() else None


def read_mode() -> str | None:
    p = mode_path()
    if p is None:
        return None
    val = _read(p)
    return val if val in ("cache", "frequency") else None


def mode_writable() -> bool:
    p = mode_path()
    return bool(p) and os.access(p, os.W_OK)


def write_mode(mode: str) -> str | None:
    """Rangfolge umstellen; liefert eine Fehlermeldung oder None."""
    if mode not in ("cache", "frequency"):
        return tr("Ungültiger Modus: {0}").format(mode)
    p = mode_path()
    if p is None:
        return tr("Kernel-Treiber amd_3d_vcache nicht vorhanden (Kernel ≥ 6.13, BIOS: CPPC Dynamic Preferred Cores = Driver/Auto)")
    try:
        p.write_text(mode + "\n", encoding="utf-8")
    except PermissionError:
        return tr("Kein Schreibrecht auf amd_x3d_mode – Zugriff zuerst einrichten")
    except OSError as exc:
        return tr("Schreiben fehlgeschlagen: {0}").format(exc)
    return None


def user_group() -> str:
    try:
        return grp.getgrgid(os.getgid()).gr_name
    except KeyError:
        return str(os.getgid())


def access_setup_shell(group: str | None = None) -> str:
    """Root-Befehl (für pkexec): tmpfiles.d-Regel, die der Gruppe des Nutzers Schreibrecht auf amd_x3d_mode gibt –
    sofort und bei jedem Start."""
    p = mode_path() or MODE_DEVICE
    g = group or user_group()
    q = shlex.quote
    done = tr("Schreibrecht für Gruppe {0} auf $f eingerichtet ({1}).").format(g, TMPFILES_CONF)
    return (f"set -e; f={q(str(p))}; printf 'z %s 0664 root %s -\\n' \"$f\" {q(g)} > {q(TMPFILES_CONF)}; "
            f"chgrp {q(g)} \"$f\"; chmod 0664 \"$f\"; systemd-tmpfiles --create {q(TMPFILES_CONF)} >/dev/null 2>&1 || true; "
            f"echo \"{done}\"")


def access_remove_shell() -> str:
    p = mode_path() or MODE_DEVICE
    q = shlex.quote
    return f"rm -f {q(TMPFILES_CONF)}; chgrp root {q(str(p))} 2>/dev/null; chmod 0644 {q(str(p))} 2>/dev/null; echo {q(tr('Zugriffsregel entfernt.'))}"


# ====================================================================== Einstellungen, Katalog, eigene Festlegungen
@dataclass
class Settings:
    enabled: bool = True
    default_mode: str = "cache"
    idle_mode: str = "frequency"
    background: bool = True
    enforce: bool = True
    game_dirs: list[str] = field(default_factory=list)

    @classmethod
    def from_config(cls, cfg: Config) -> "Settings":
        default = cfg.get("X3D_DEFAULT_MODE", "cache")
        idle = cfg.get("X3D_IDLE_MODE", "frequency")
        dirs = [os.path.expanduser(d) for d in cfg.get("X3D_GAME_DIRS", "").split(":") if d.strip()]
        return cls(enabled=cfg.get("X3D_ENABLED", "1") == "1",
                   default_mode=default if default in MODES else "cache",
                   idle_mode=idle if idle in IDLE_MODES else "frequency",
                   background=cfg.get("X3D_BACKGROUND", "1") == "1",
                   enforce=cfg.get("X3D_ENFORCE", "1") == "1",
                   game_dirs=dirs)


@dataclass
class CatalogEntry:
    name: str
    kind: str = "game"
    mode: str = ""
    exe: list[str] = field(default_factory=list)
    comm: list[str] = field(default_factory=list)
    steam: list[str] = field(default_factory=list)

    def keys(self) -> list[str]:
        return ([f"exe:{e.lower()}" for e in self.exe] + [f"comm:{c}" for c in self.comm]
                + [f"steam:{s}" for s in self.steam])


class Catalog:
    """Mitgelieferte Kompatibilitätsliste (data/x3d_catalog.json)."""

    def __init__(self, path: Path = CATALOG_FILE) -> None:
        self.path = Path(path)
        self.entries: list[CatalogEntry] = []
        self.index: dict[str, CatalogEntry] = {}
        self.load()

    def load(self) -> None:
        self.entries, self.index = [], {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            log.warning("Katalog %s nicht lesbar", self.path)
            return
        for raw in data.get("entries") or []:
            if not isinstance(raw, dict) or not raw.get("name"):
                continue
            kind = raw.get("kind", "game")
            mode = raw.get("mode", "")
            e = CatalogEntry(name=str(raw["name"]), kind=kind if kind in KINDS else "game", mode=mode if mode in MODES else "",
                             exe=[str(x) for x in raw.get("exe", [])], comm=[str(x) for x in raw.get("comm", [])],
                             steam=[str(x) for x in raw.get("steam", [])])
            self.entries.append(e)
            for k in e.keys():
                self.index.setdefault(k, e)

    def get(self, key: str) -> CatalogEntry | None:
        return self.index.get(key)

    def games(self) -> list[CatalogEntry]:
        return [e for e in self.entries if e.kind == "game"]


class UserRules:
    """Eigene Festlegungen (~/.config/cachy-daily-toolkit/x3d.json): Schlüssel → Modus/Name; Ausschlüsse („kein Spiel“)."""

    def __init__(self, path: Path = USER_FILE) -> None:
        self.path = Path(path)
        self.overrides: dict[str, dict] = {}
        self.ignore: set[str] = set()
        self.names: dict[str, str] = {}      # Anzeigename je Schlüssel (auch für Ausschlüsse)
        self._mtime: float = -1.0
        self.load()

    def mtime(self) -> float:
        try:
            return self.path.stat().st_mtime
        except OSError:
            return -1.0

    def changed(self) -> bool:
        return self.mtime() != self._mtime

    def load(self) -> None:
        self._mtime = self.mtime()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        self.overrides = {}
        for k, v in (data.get("overrides") or {}).items():
            if isinstance(v, dict) and v.get("mode") in MODES:
                self.overrides[str(k)] = {"mode": v["mode"], "name": str(v.get("name", "")), "source": str(v.get("source", ""))}
        self.ignore = {str(k) for k in (data.get("ignore") or []) if k}
        self.names = {str(k): str(v) for k, v in (data.get("names") or {}).items() if v}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"version": 1, "overrides": self.overrides, "ignore": sorted(self.ignore),
                                   "names": self.names}, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)
        self._mtime = self.mtime()

    def set_override(self, key: str, mode: str, name: str = "", source: str = "") -> None:
        if mode not in MODES:
            raise ValueError(mode)
        old = self.overrides.get(key, {})
        self.overrides[key] = {"mode": mode, "name": name or old.get("name", ""), "source": source or old.get("source", "")}
        if name:
            self.names[key] = name
        self.ignore.discard(key)
        self.save()

    def clear_override(self, key: str) -> None:
        self.overrides.pop(key, None)
        self.ignore.discard(key)
        self.names.pop(key, None)
        self.save()

    def set_ignore(self, key: str, on: bool = True, name: str = "") -> None:
        if on:
            self.ignore.add(key)
            self.overrides.pop(key, None)
            if name:
                self.names[key] = name
        else:
            self.ignore.discard(key)
        self.save()

    def label(self, key: str) -> str:
        return self.names.get(key) or (self.overrides.get(key) or {}).get("name") or key.split(":", 1)[-1]


@dataclass
class RegistryApp:
    """Anwendung aus der DailyTool-Registry (Verknüpfung) mit CCD-Einstellung."""
    name: str
    exe: str
    prefix: str
    ccd: str = ""        # "" = Automatik, cache | frequency | none

    @property
    def exe_base(self) -> str:
        return Path(self.exe).name.lower()


def registry_apps(path: Path | None = None) -> list[RegistryApp]:
    """Verknüpfungen aus registry.json lesen (ohne das Registry-Modul zu laden – der Dienst soll leichtgewichtig bleiben)."""
    from .registry import REGISTRY_FILE
    p = Path(path) if path else REGISTRY_FILE
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out: list[RegistryApp] = []
    for v in (data.get("apps") or {}).values():
        if isinstance(v, dict) and v.get("exe"):
            ccd = str(v.get("ccd", ""))
            out.append(RegistryApp(name=str(v.get("name", "")), exe=str(v["exe"]), prefix=str(v.get("prefix", "")),
                                   ccd=ccd if ccd in MODES else ""))
    return out


# ====================================================================== Steam-Namen
def steam_app_names(steam_roots: Iterable[Path] | None = None) -> dict[str, str]:
    """AppID → Name aus den appmanifest-Dateien aller Steam-Bibliotheken."""
    roots = list(steam_roots) if steam_roots is not None else [
        Path.home() / ".local/share/Steam", Path.home() / ".steam/steam",
        Path.home() / ".var/app/com.valvesoftware.Steam/.local/share/Steam"]
    libs: set[Path] = set()
    for r in roots:
        sa = r / "steamapps"
        if sa.is_dir():
            libs.add(sa)
        for m in re.finditer(r'"path"\s+"([^"]+)"', _read(sa / "libraryfolders.vdf")):
            lp = Path(m.group(1).replace("\\\\", "\\")) / "steamapps"
            if lp.is_dir():
                libs.add(lp)
    names: dict[str, str] = {}
    for lib in libs:
        try:
            files = list(lib.glob("appmanifest_*.acf"))
        except OSError:
            continue
        for f in files:
            text = _read(f)
            a = re.search(r'"appid"\s+"(\d+)"', text)
            n = re.search(r'"name"\s+"([^"]+)"', text)
            if a and n:
                names[a.group(1)] = n.group(1)
    return names


# ====================================================================== Prozesse
@dataclass
class Proc:
    pid: int
    ppid: int
    comm: str
    start: int
    exe: str = ""
    cmdline: list[str] = field(default_factory=list)
    env: dict[str, str] | None = None
    win_exe: str = ""        # Dateiname der Windows-.exe (Wine/Proton), sonst ""

    @property
    def exe_base(self) -> str:
        return Path(self.exe).name if self.exe else self.comm

    @property
    def ident(self) -> tuple[int, int]:
        return (self.pid, self.start)


WINE_BINARIES = {"wine", "wine64", "wine-preloader", "wine64-preloader", "wineserver", "wine64-wow64"}


def _win_basename(arg: str) -> str:
    return re.split(r"[\\/]", arg)[-1]


def read_proc(pid: int, procfs: str = "/proc", old: Proc | None = None) -> Proc | None:
    """Einen Prozess lesen; bei unverändertem Start (gleiche pid/starttime) bleiben cmdline/exe/env erhalten."""
    try:
        with open(f"{procfs}/{pid}/stat", "rb") as fh:
            stat = fh.read().decode("utf-8", "replace")
    except OSError:
        return None
    try:
        lpar, rpar = stat.index("("), stat.rindex(")")
        comm = stat[lpar + 1:rpar]
        rest = stat[rpar + 2:].split()
        ppid, start = int(rest[1]), int(rest[19])
    except (ValueError, IndexError):
        return None
    if old is not None and old.start == start and old.comm == comm:
        old.ppid = ppid
        return old
    p = Proc(pid=pid, ppid=ppid, comm=comm, start=start)
    try:
        p.exe = os.readlink(f"{procfs}/{pid}/exe")
        if p.exe.endswith(" (deleted)"):
            p.exe = p.exe[:-10]
    except OSError:
        p.exe = ""
    try:
        with open(f"{procfs}/{pid}/cmdline", "rb") as fh:
            p.cmdline = [a.decode("utf-8", "replace") for a in fh.read().split(b"\0") if a]
    except OSError:
        p.cmdline = []
    base = Path(p.exe).name if p.exe else ""
    if (base in WINE_BINARIES or base.startswith("wine")) and p.cmdline:
        first = _win_basename(p.cmdline[0])
        if first.lower().endswith((".exe", ".com", ".bat", ".msi")):
            p.win_exe = first
        elif len(p.cmdline) > 1 and _win_basename(p.cmdline[1]).lower().endswith(".exe"):
            p.win_exe = _win_basename(p.cmdline[1])
    elif base and base.lower().endswith(".exe"):  # Wine ohne Preloader zeigt direkt die .exe
        p.win_exe = base
    return p


def read_env(pid: int, procfs: str = "/proc") -> dict[str, str]:
    try:
        with open(f"{procfs}/{pid}/environ", "rb") as fh:
            raw = fh.read()
    except OSError:
        return {}
    env: dict[str, str] = {}
    for item in raw.split(b"\0"):
        if b"=" in item:
            k, _, v = item.partition(b"=")
            env[k.decode("utf-8", "replace")] = v.decode("utf-8", "replace")
    return env


def process_uses_gpu(pid: int, procfs: str = "/proc") -> bool:
    """Hat der Prozess ein Render-Gerät offen (DRI renderD*, NVIDIA)? – das Spiel-Kriterium für Wine-Programme."""
    try:
        fds = os.listdir(f"{procfs}/{pid}/fd")
    except OSError:
        return False
    for fd in fds:
        try:
            target = os.readlink(f"{procfs}/{pid}/fd/{fd}")
        except OSError:
            continue
        if target.startswith("/dev/dri/renderD"):
            return True
        if target.startswith("/dev/nvidia") and not target.startswith("/dev/nvidia-caps"):
            return True
    return False


def list_threads(pid: int, procfs: str = "/proc") -> list[int]:
    try:
        return [int(t) for t in os.listdir(f"{procfs}/{pid}/task") if t.isdigit()]
    except OSError:
        return []


def pretty_name(exe: str) -> str:
    """„FactoryGame-Win64-Shipping.exe“ → „Factory Game“, „surviving_mars.exe“ → „Surviving Mars“."""
    stem = re.sub(r"\.(exe|x86_64|x86|bin|sh|com)$", "", exe, flags=re.I)
    stem = re.sub(r"[-_](win64|win32|wingdx12|windows|shipping|dx11|dx12|vulkan|vk|steam|gog|epic|egs|ws|x64|x86|64|32)$", "",
                  stem, flags=re.I)
    stem = re.sub(r"[-_](win64|win32|wingdx12|windows|shipping|dx11|dx12|vulkan|vk|steam|gog|epic|egs|ws|x64|x86|64|32)$", "",
                  stem, flags=re.I)
    stem = re.sub(r"[_\-.]+", " ", stem)
    stem = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", stem)
    stem = re.sub(r"(?<=[A-Za-z])(?=\d)", " ", stem)
    return re.sub(r"\s+", " ", stem).strip() or exe


@dataclass
class Match:
    mode: str
    name: str
    source: str            # steam | umu | heroic | lutris | wine | native | registry
    key: str               # Schlüssel für Festlegungen (exe:…, steam:…, path:…)
    reason: str            # deutsch (Kennung, auch im Dienstprotokoll) – Anzeige mit tr()
    priority: int          # 3 eigene Festlegung, 2 DailyTool-Verknüpfung, 1 Katalog, 0 Heuristik
    keys: list[str] = field(default_factory=list)


@dataclass
class Session:
    root: int
    name: str
    source: str
    key: str
    mode: str
    reason: str
    priority: int
    pids: set[int] = field(default_factory=set)
    matched: set[int] = field(default_factory=set)
    threads: int = 0
    since: float = 0.0
    keys: list[str] = field(default_factory=list)
    threads_on_ccd: int = 0                                   # Threads, deren Affinität im zugewiesenen CCD liegt
    placement: dict[int, int] = field(default_factory=dict)   # CCD → Threads, die zuletzt dort liefen
    cpu_ticks: dict[int, int] = field(default_factory=dict)   # CCD → CPU-Zeit (Ticks) seit Erkennung
    share: float | None = None                                # Anteil der CPU-Zeit auf dem zugewiesenen CCD (%)

    def to_dict(self) -> dict:
        return {"root": self.root, "name": self.name, "source": self.source, "source_label": SOURCE_LABELS.get(self.source, self.source),
                "key": self.key, "keys": self.keys, "mode": self.mode, "reason": self.reason, "priority": self.priority,
                "pids": len(self.pids), "threads": self.threads, "since": self.since,
                "threads_on_ccd": self.threads_on_ccd, "placement": {str(k): v for k, v in self.placement.items()},
                "cpu_ticks": {str(k): v for k, v in self.cpu_ticks.items()}, "share": self.share}


class Detector:
    """Erkennt Spiele unter den eigenen Prozessen und fasst sie zu Sitzungen (Prozessbäumen) zusammen."""

    def __init__(self, topo: Topology, settings: Settings, catalog: Catalog, rules: UserRules,
                 apps: list[RegistryApp] | None = None, *, procfs: str = "/proc", uid: int | None = None,
                 gpu_check: Callable[[int], bool] | None = None, steam_names: dict[str, str] | None = None,
                 env_reader: Callable[[int], dict[str, str]] | None = None) -> None:
        self.topo = topo
        self.settings = settings
        self.catalog = catalog
        self.rules = rules
        self.apps = apps or []
        self.procfs = procfs
        self.uid = os.getuid() if uid is None else uid
        self._gpu_check = gpu_check or (lambda pid: process_uses_gpu(pid, self.procfs))
        self._env_reader = env_reader or (lambda pid: read_env(pid, self.procfs))
        self._steam_names = steam_names
        self._steam_names_ts = 0.0
        self.procs: dict[int, Proc] = {}
        self._gpu_cache: dict[tuple[int, int], tuple[float, bool]] = {}
        self.game_dirs = self._default_game_dirs() + list(settings.game_dirs)

    @staticmethod
    def _default_game_dirs() -> list[str]:
        home = Path.home()
        cands = [home / "Games", home / ".local/share/Steam/steamapps/common", home / ".steam/steam/steamapps/common",
                 home / ".var/app/com.valvesoftware.Steam/.local/share/Steam/steamapps/common",
                 home / ".local/share/lutris", home / "Games/Heroic", home / ".var/app/com.heroicgameslauncher.hgl"]
        return [str(c) for c in cands]

    # -- Prozesse ---------------------------------------------------------
    def scan(self) -> dict[int, Proc]:
        """Eigene Prozesse lesen (cmdline/exe/env bleiben für bekannte Prozesse im Cache)."""
        seen: dict[int, Proc] = {}
        try:
            names = os.listdir(self.procfs)
        except OSError:
            return seen
        for name in names:
            if not name.isdigit():
                continue
            pid = int(name)
            try:
                if os.stat(f"{self.procfs}/{pid}").st_uid != self.uid:
                    continue
            except OSError:
                continue
            p = read_proc(pid, self.procfs, self.procs.get(pid))
            if p is not None:
                seen[pid] = p
        self.procs = seen
        live = {p.ident for p in seen.values()}
        for k in [k for k in self._gpu_cache if k not in live]:
            self._gpu_cache.pop(k, None)
        return seen

    def env_of(self, p: Proc) -> dict[str, str]:
        if p.env is None:
            p.env = self._env_reader(p.pid)
        return p.env

    def uses_gpu(self, p: Proc) -> bool:
        now = time.monotonic()
        hit = self._gpu_cache.get(p.ident)
        if hit and (now - hit[0]) < (30.0 if hit[1] else 4.0):
            return hit[1]
        val = bool(self._gpu_check(p.pid))
        self._gpu_cache[p.ident] = (now, val)
        return val

    def steam_name(self, appid: str) -> str:
        now = time.monotonic()
        if self._steam_names is None or (appid not in self._steam_names and now - self._steam_names_ts > 30):
            self._steam_names = steam_app_names()
            self._steam_names_ts = now
        return self._steam_names.get(appid, "")

    # -- Identität ------------------------------------------------------------
    def keys_for(self, p: Proc) -> list[str]:
        keys: list[str] = []
        if p.exe:
            keys.append(f"path:{p.exe}")
        if p.win_exe:
            keys.append(f"exe:{p.win_exe.lower()}")
        elif p.exe:
            keys.append(f"exe:{Path(p.exe).name.lower()}")
        env = self.env_of(p)
        sid = env.get("SteamAppId") or env.get("STEAM_COMPAT_APP_ID") or ""
        if sid and sid != "0":
            keys.append(f"steam:{sid}")
        umu = env.get("UMU_ID") or env.get("GAMEID") or ""
        if umu and umu not in ("0", "umu-default"):
            keys.append(f"umu:{umu}")
        if env.get("HEROIC_APP_NAME"):
            keys.append(f"heroic:{env['HEROIC_APP_NAME']}")
        if env.get("LUTRIS_GAME_UUID"):
            keys.append(f"lutris:{env['LUTRIS_GAME_UUID']}")
        keys.append(f"comm:{p.comm}")
        return keys

    def source_of(self, p: Proc) -> str:
        env = self.env_of(p)
        if (env.get("SteamAppId") or env.get("STEAM_COMPAT_APP_ID") or "0") != "0" and "LUTRIS_GAME_UUID" not in env \
                and "HEROIC_APP_NAME" not in env and not env.get("UMU_ID"):
            return "steam"
        if env.get("HEROIC_APP_NAME"):
            return "heroic"
        if env.get("LUTRIS_GAME_UUID"):
            return "lutris"
        if env.get("UMU_ID") or env.get("GAMEID"):
            return "umu"
        return "wine" if p.win_exe else "native"

    def group_key(self, p: Proc) -> str | None:
        env = self.env_of(p)
        for k in GROUP_ENV:
            v = env.get(k)
            if v and not (k in ("SteamAppId", "STEAM_COMPAT_APP_ID") and v == "0"):
                return f"{k}={v}"
        return None

    def registry_match(self, p: Proc) -> RegistryApp | None:
        base = (p.win_exe or Path(p.exe).name).lower()
        if not base:
            return None
        cands = [a for a in self.apps if a.exe_base == base]
        if not cands:
            return None
        if len(cands) > 1:
            env = self.env_of(p)
            pfx = env.get("STEAM_COMPAT_DATA_PATH") or env.get("WINEPREFIX") or ""
            for a in cands:
                if pfx and a.prefix and pfx.startswith(a.prefix.rstrip("/")):
                    return a
        return cands[0]

    def is_native_wrapper(self, p: Proc) -> bool:
        base = Path(p.exe).name if p.exe else p.comm
        if base in NATIVE_WRAPPERS or p.comm in NATIVE_WRAPPERS or re.match(r"^python\d", base):
            return True
        if any(m in p.exe for m in RUNTIME_MARKERS):
            return True
        return bool(p.exe) and p.exe.startswith(SYSTEM_PREFIXES) and not self.in_game_dirs(p.exe)

    def in_game_dirs(self, exe: str) -> bool:
        return any(exe.startswith(d.rstrip("/") + "/") for d in self.game_dirs if d)

    # -- Einstufung ------------------------------------------------------------
    def classify(self, p: Proc) -> Match | None:
        """Spiel? Reihenfolge: Ausschlüsse (Katalog launcher/background/system, Installer-Muster, Wrapper) →
        eigene Festlegung → DailyTool-Verknüpfung → Katalog-Spiel → Heuristik (GPU-Zugriff + Herkunft)."""
        if p.pid <= 1 or p.pid == os.getpid():
            return None
        keys = self.keys_for(p)
        if any(k in self.rules.ignore for k in keys):
            return None
        for k in keys:
            if k.startswith(("exe:", "comm:", "path:")):
                e = self.catalog.get(k)
                if e is not None and e.kind != "game":
                    return None
        if p.win_exe and NON_GAME_RE.search(p.win_exe.lower()):
            return None
        if not p.win_exe and self.is_native_wrapper(p):
            return None
        source = self.source_of(p)
        for k in keys:
            ov = self.rules.overrides.get(k)
            if ov:
                return Match(ov["mode"], ov.get("name") or self._guess_name(p, keys), ov.get("source") or source, k,
                             N_("Eigene Festlegung"), 3, keys)
        reg = self.registry_match(p)
        if reg is not None:
            cat = next((self.catalog.get(k) for k in keys if (self.catalog.get(k) or CatalogEntry("", "")).kind == "game"), None)
            mode = reg.ccd or (cat.mode if cat and cat.mode else "") or self.settings.default_mode
            return Match(mode, reg.name or pretty_name(reg.exe_base), "registry", f"path:{reg.exe}",
                         N_("Verknüpfung im DailyTool"), 2, keys)
        for k in keys:
            e = self.catalog.get(k)
            if e is not None and e.kind == "game":
                return Match(e.mode or self.settings.default_mode, e.name, source, k, N_("Katalog"), 1, keys)
        if p.win_exe:
            if self.uses_gpu(p):
                return Match(self.settings.default_mode, self._guess_name(p, keys), source, f"exe:{p.win_exe.lower()}",
                             N_("Windows-Programm mit GPU-Zugriff"), 0, keys)
            return None
        marker = next((k for k in keys if k.startswith(("steam:", "heroic:", "lutris:", "umu:"))), "")
        if (marker or self.in_game_dirs(p.exe)) and p.exe and self.uses_gpu(p):
            return Match(self.settings.default_mode, self._guess_name(p, keys), source, marker or f"path:{p.exe}",
                         N_("Natives Spiel mit GPU-Zugriff") if not marker else N_("Vom Launcher gestartet, GPU-Zugriff"), 0, keys)
        return None

    def _guess_name(self, p: Proc, keys: list[str]) -> str:
        for k in keys:
            if k.startswith("steam:"):
                n = self.steam_name(k[6:])
                if n:
                    return n
        env = self.env_of(p)
        if env.get("HEROIC_APP_NAME") and env.get("HEROIC_APP_TITLE"):
            return env["HEROIC_APP_TITLE"]
        # Lutris: Wrapper-Prozess „lutris-wrapper: Name“ unter den Vorfahren
        cur = self.procs.get(p.ppid)
        hops = 0
        while cur and hops < 12:
            if cur.cmdline and cur.cmdline[0].startswith("lutris-wrapper:"):
                nm = " ".join(cur.cmdline).split(":", 1)[1].strip()
                if nm:
                    return nm
            cur = self.procs.get(cur.ppid)
            hops += 1
        return pretty_name(p.win_exe or Path(p.exe).name or p.comm)

    # -- Sitzungen ---------------------------------------------------------------
    def find_root(self, pid: int) -> int:
        p = self.procs.get(pid)
        if p is None:
            return pid
        key = self.group_key(p)
        cur = p
        hops = 0
        while key and hops < 40:
            parent = self.procs.get(cur.ppid)
            if parent is None or parent.pid <= 1 or parent.comm in ROOT_STOP or self.group_key(parent) != key:
                break
            cur = parent
            hops += 1
        return cur.pid

    def children_map(self) -> dict[int, list[int]]:
        ch: dict[int, list[int]] = {}
        for p in self.procs.values():
            ch.setdefault(p.ppid, []).append(p.pid)
        return ch

    def descendants(self, root: int, children: dict[int, list[int]] | None = None) -> set[int]:
        children = children if children is not None else self.children_map()
        out = {root}
        stack = [root]
        while stack:
            for c in children.get(stack.pop(), []):
                if c not in out:
                    out.add(c)
                    stack.append(c)
        return out

    def detect(self) -> tuple[list[Session], dict[int, Match]]:
        procs = self.scan()
        matches: dict[int, Match] = {}
        for p in procs.values():
            m = self.classify(p)
            if m is not None:
                matches[p.pid] = m
        children = self.children_map()
        sessions: dict[int, Session] = {}
        for pid, m in matches.items():
            root = self.find_root(pid)
            s = sessions.get(root)
            p = procs[pid]
            if s is None:
                s = Session(root=root, name=m.name, source=m.source, key=m.key, mode=m.mode, reason=m.reason,
                            priority=m.priority, since=time.time(), keys=list(m.keys))
                sessions[root] = s
            elif (m.priority, p.start) > (s.priority, procs[max(s.matched, key=lambda x: procs[x].start)].start
                                           if s.matched else -1):
                s.name, s.source, s.key, s.mode, s.reason, s.priority, s.keys = \
                    m.name, m.source, m.key, m.mode, m.reason, m.priority, list(m.keys)
            s.matched.add(pid)
        # Sitzungen, deren Wurzel innerhalb einer anderen Sitzung liegt, aufgehen lassen
        roots = sorted(sessions)
        trees = {r: self.descendants(r, children) for r in roots}
        for r in roots:
            for other in roots:
                if other != r and r in trees[other] and r in sessions:
                    outer, inner = sessions[other], sessions.pop(r)
                    outer.matched |= inner.matched
                    if (inner.priority, 1) > (outer.priority, 0):
                        outer.name, outer.source, outer.key, outer.mode, outer.reason, outer.priority, outer.keys = \
                            inner.name, inner.source, inner.key, inner.mode, inner.reason, inner.priority, list(inner.keys)
                    break
        for r, s in sessions.items():
            s.pids = trees[r]
        return sorted(sessions.values(), key=lambda s: s.root), matches

    def background_pids(self, sessions: list[Session]) -> set[int]:
        """Launcher und bekannte Hintergrundprogramme samt Kindern – ohne alles, was zu einer Spielsitzung gehört."""
        in_game = {pid for s in sessions for pid in s.pids}
        children = self.children_map()
        out: set[int] = set()
        for p in self.procs.values():
            if p.pid in in_game or p.pid == os.getpid():
                continue
            e = None
            if p.win_exe:
                e = self.catalog.get(f"exe:{p.win_exe.lower()}")
            if e is None:
                e = self.catalog.get(f"comm:{p.comm}")
            if e is None and p.exe:
                e = self.catalog.get(f"exe:{Path(p.exe).name.lower()}")
            if e is not None and e.kind in ("launcher", "background"):
                out |= {d for d in self.descendants(p.pid, children) if d not in in_game}
        return out


# ====================================================================== Messung (Thread-Lage, Auslastung)
def thread_stats(pid: int, procfs: str = "/proc") -> list[tuple[int, int, int]]:
    """Je Thread: (tid, zuletzt genutzte CPU, CPU-Zeit in Ticks) aus /proc/<pid>/task/<tid>/stat."""
    out: list[tuple[int, int, int]] = []
    for tid in list_threads(pid, procfs):
        try:
            with open(f"{procfs}/{pid}/task/{tid}/stat", "rb") as fh:
                st = fh.read().decode("utf-8", "replace")
            rest = st[st.rindex(")") + 2:].split()
            out.append((tid, int(rest[36]), int(rest[11]) + int(rest[12])))
        except (OSError, ValueError, IndexError):
            continue
    return out


def cpu_times(procfs: str = "/proc") -> dict[int, tuple[int, int]]:
    """CPU → (beschäftigt, gesamt) in Ticks aus /proc/stat."""
    out: dict[int, tuple[int, int]] = {}
    try:
        lines = Path(f"{procfs}/stat").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return out
    for line in lines:
        if not line.startswith("cpu") or not line[3:4].isdigit():
            continue
        f = line.split()
        try:
            n = int(f[0][3:])
            vals = [int(x) for x in f[1:]]
        except ValueError:
            continue
        idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
        out[n] = (sum(vals) - idle, sum(vals))
    return out


def cpu_usage(prev: dict[int, tuple[int, int]], cur: dict[int, tuple[int, int]]) -> dict[int, float]:
    """Auslastung je CPU in Prozent zwischen zwei Messungen."""
    pct: dict[int, float] = {}
    for cpu, (busy, total) in cur.items():
        pb, pt = prev.get(cpu, (busy, total))
        dt = total - pt
        pct[cpu] = max(0.0, min(100.0, 100.0 * (busy - pb) / dt)) if dt > 0 else 0.0
    return pct


def ccd_usage(topo: Topology, pct: dict[int, float]) -> dict[int, float]:
    """Mittlere Auslastung je CCD."""
    out: dict[int, float] = {}
    for ccd in topo.ccds:
        vals = [pct[c] for c in ccd.cpus if c in pct]
        out[ccd.id] = sum(vals) / len(vals) if vals else 0.0
    return out


# ====================================================================== Affinität
def set_affinity(pids: Iterable[int], cpus: set[int], *, pinned: dict[int, frozenset[int]], enforce: bool,
                 procfs: str = "/proc", dry_run: bool = False) -> int:
    """Alle Threads der Prozesse auf cpus beschränken. Threads, die schon innerhalb von cpus laufen (z. B. eigene
    Thread-Affinität des Spiels), bleiben unangetastet. Liefert die Zahl geänderter Threads."""
    target = frozenset(cpus)
    changed = 0
    for pid in pids:
        for tid in list_threads(pid, procfs):
            prev = pinned.get(tid)
            if prev == target and not enforce:
                continue
            try:
                cur = os.sched_getaffinity(tid)
            except OSError:
                continue
            if cur and set(cur).issubset(target):
                pinned[tid] = target
                continue
            if not dry_run:
                try:
                    os.sched_setaffinity(tid, target)
                except OSError:
                    continue
            pinned[tid] = target
            changed += 1
    return changed


def release_affinity(tids: Iterable[int], all_cpus: set[int], *, pinned: dict[int, frozenset[int]],
                     dry_run: bool = False) -> int:
    """Zuvor gesetzte Beschränkung aufheben (nur Threads, deren Affinität noch unsere ist)."""
    freed = 0
    for tid in list(tids):
        target = pinned.pop(tid, None)
        if target is None:
            continue
        try:
            cur = os.sched_getaffinity(tid)
        except OSError:
            continue
        if set(cur) == set(target):
            if not dry_run:
                try:
                    os.sched_setaffinity(tid, all_cpus)
                except OSError:
                    continue
            freed += 1
    return freed


# ====================================================================== Dienst
class Controller:
    """Ein Durchlauf pro Takt: Spiele erkennen, Affinität setzen, Rangfolge umschalten, Zustand schreiben."""

    def __init__(self, cfg: Config | None = None, *, dry_run: bool = False, topo: Topology | None = None,
                 catalog: Catalog | None = None, rules: UserRules | None = None, apps: list[RegistryApp] | None = None,
                 detector: Detector | None = None, state_file: Path = STATE_FILE,
                 mode_reader: Callable[[], str | None] = read_mode, mode_writer: Callable[[str], str | None] = write_mode,
                 mode_writable_fn: Callable[[], bool] = mode_writable) -> None:
        self.cfg = cfg or Config()
        self.dry_run = dry_run
        self.topo = topo or topology()
        self.catalog = catalog or Catalog()
        self.rules = rules or UserRules()
        self.settings = Settings.from_config(self.cfg)
        self.apps = apps if apps is not None else registry_apps()
        self.detector = detector or Detector(self.topo, self.settings, self.catalog, self.rules, self.apps)
        self.state_file = Path(state_file)
        self._read_mode, self._write_mode, self._mode_writable = mode_reader, mode_writer, mode_writable_fn
        self.pinned: dict[int, frozenset[int]] = {}
        self.bg_pinned: dict[int, frozenset[int]] = {}
        self.sessions: list[Session] = []
        self.session_since: dict[int, float] = {}
        self._thread_ticks: dict[int, int] = {}               # tid → zuletzt gemessene CPU-Zeit
        self._session_ticks: dict[int, dict[int, int]] = {}   # Sitzungswurzel → CCD → CPU-Zeit seit Erkennung
        self.events: deque[tuple[float, str]] = deque(maxlen=60)
        self.desired_mode: str | None = None
        self.mode_changed_by_us = False
        self._last_state = ""
        self._last_state_ts = 0.0
        self._cfg_mtime = self._mtime(self.cfg.path)
        self._reg_mtime = self._mtime(self._registry_path())

    @staticmethod
    def _registry_path() -> Path:
        from .registry import REGISTRY_FILE
        return REGISTRY_FILE

    @staticmethod
    def _mtime(p: Path) -> float:
        try:
            return Path(p).stat().st_mtime
        except OSError:
            return -1.0

    def event(self, text: str) -> None:
        self.events.append((time.time(), text))
        log.info(text)

    # -- Einstellungen nachladen -------------------------------------------------
    def reload_if_changed(self) -> None:
        m = self._mtime(self.cfg.path)
        if m != self._cfg_mtime:
            self._cfg_mtime = m
            self.cfg.load()
            self.settings = Settings.from_config(self.cfg)
            self.detector.settings = self.settings
            self.detector.game_dirs = self.detector._default_game_dirs() + list(self.settings.game_dirs)
            self.event("Einstellungen neu geladen")
        if self.rules.changed():
            self.rules.load()
            self.event("Eigene Festlegungen neu geladen")
        r = self._mtime(self._registry_path())
        if r != self._reg_mtime:
            self._reg_mtime = r
            self.apps = registry_apps()
            self.detector.apps = self.apps

    # -- Takt ----------------------------------------------------------------------
    def tick(self) -> None:
        self.reload_if_changed()
        all_cpus = set(self.topo.all_cpus) or set(os.sched_getaffinity(0))
        if not self.settings.enabled or not self.topo.dual:
            if self.pinned or self.bg_pinned:
                self._release_all(all_cpus)
            self.sessions = []
            self._apply_mode(None)
            self.write_state()
            return
        sessions, _matches = self.detector.detect()
        now = time.time()
        prev_roots = {s.root: s for s in self.sessions}
        for s in sessions:
            s.since = self.session_since.setdefault(s.root, now)
            old = prev_roots.get(s.root)
            if old is None:
                self.event(f"Spiel erkannt: {s.name} ({SOURCE_LABELS.get(s.source, s.source)}, {s.reason}) → {MODE_LABELS[s.mode]}")
            elif old.mode != s.mode:
                self.event(f"{s.name}: Zuweisung geändert → {MODE_LABELS[s.mode]}")
                if s.mode == "none":
                    release_affinity([t for pid in s.pids for t in list_threads(pid, self.detector.procfs)], all_cpus,
                                     pinned=self.pinned, dry_run=self.dry_run)
        live_roots = {s.root for s in sessions}
        for root, old in prev_roots.items():
            if root not in live_roots:
                self.event(f"Spiel beendet: {old.name}")
                self.session_since.pop(root, None)
                self._session_ticks.pop(root, None)
        self.sessions = sessions
        # Spiel-Prozessbäume auf ihren CCD
        game_pids: set[int] = set()
        live_tids: set[int] = set()
        for s in sessions:
            cpus = self.topo.cpus_for(s.mode)
            game_pids |= s.pids
            if cpus:
                n = set_affinity(s.pids, cpus, pinned=self.pinned, enforce=self.settings.enforce,
                                 procfs=self.detector.procfs, dry_run=self.dry_run)
                if n:
                    log.debug("%s: %d Threads auf CPUs %s", s.name, n, format_cpu_list(cpus))
            live_tids |= self._sample_session(s, cpus)
        for tid in [t for t in self._thread_ticks if t not in live_tids]:
            self._thread_ticks.pop(tid, None)
        # Hintergrundprogramme auf den anderen CCD
        lead = next((s for s in sessions if s.mode in ("cache", "frequency")), None)
        if lead is not None and self.settings.background:
            other = self.topo.other_cpus(lead.mode)
            if other:
                bg = self.detector.background_pids(sessions)
                set_affinity(bg, other, pinned=self.bg_pinned, enforce=False, procfs=self.detector.procfs, dry_run=self.dry_run)
                live_tids = {t for pid in bg for t in list_threads(pid, self.detector.procfs)}
                for tid in [t for t in self.bg_pinned if t not in live_tids]:
                    self.bg_pinned.pop(tid, None)
        elif self.bg_pinned:
            n = release_affinity(list(self.bg_pinned), all_cpus, pinned=self.bg_pinned, dry_run=self.dry_run)
            self.bg_pinned.clear()
            if n:
                self.event(f"Hintergrundprogramme wieder auf alle Kerne ({n} Threads)")
        # verwaiste Einträge
        live_game_tids = {t for pid in game_pids for t in list_threads(pid, self.detector.procfs)}
        for tid in [t for t in self.pinned if t not in live_game_tids]:
            self.pinned.pop(tid, None)
        # Rangfolge der Kerne (CPPC) wie der AMD-Treiber: Spiel → dessen CCD, sonst Ruhe-Vorgabe
        self._apply_mode(lead.mode if lead else None)
        self.write_state()

    def _sample_session(self, s: Session, cpus: set[int] | None) -> set[int]:
        """Thread-Lage einer Sitzung messen: Affinität im Ziel-CCD, zuletzt genutzter CCD, CPU-Zeit je CCD seit Erkennung."""
        target = frozenset(cpus) if cpus else None
        ticks = self._session_ticks.setdefault(s.root, {})
        placement: dict[int, int] = {}
        on_ccd = total = 0
        seen: set[int] = set()
        for pid in s.pids:
            for tid, psr, t in thread_stats(pid, self.detector.procfs):
                seen.add(tid)
                total += 1
                ccd = self.topo.ccd_of(psr)
                placement[ccd] = placement.get(ccd, 0) + 1
                prev = self._thread_ticks.get(tid)
                if prev is not None and t > prev:
                    ticks[ccd] = ticks.get(ccd, 0) + (t - prev)
                self._thread_ticks[tid] = t
                if target is None:
                    on_ccd += 1
                else:
                    try:
                        if set(os.sched_getaffinity(tid)) <= target:
                            on_ccd += 1
                    except OSError:
                        pass
        s.threads, s.threads_on_ccd, s.placement, s.cpu_ticks = total, on_ccd, placement, dict(ticks)
        all_ticks = sum(ticks.values())
        if all_ticks > 0 and target is not None:
            wanted = self.topo.ccd_ids_for(s.mode)
            s.share = 100.0 * sum(v for k, v in ticks.items() if k in wanted) / all_ticks
        else:
            s.share = None
        return seen

    def _apply_mode(self, game_mode: str | None) -> None:
        if game_mode in ("cache", "frequency"):
            desired = game_mode
        elif self.settings.idle_mode in ("cache", "frequency"):
            desired = self.settings.idle_mode
        else:
            desired = None
        if desired == self.desired_mode:
            return
        self.desired_mode = desired
        if desired is None:
            return
        current = self._read_mode()
        if current is None or current == desired:
            return
        if not self._mode_writable():
            self.event(f"Rangfolge „{MODE_LABELS[desired]}“ nicht gesetzt – kein Schreibrecht auf amd_x3d_mode")
            return
        if self.dry_run:
            self.event(f"[Probelauf] Rangfolge → {MODE_LABELS[desired]}")
            return
        err = self._write_mode(desired)
        if err:
            self.event(f"Rangfolge nicht gesetzt: {err}")
        else:
            self.mode_changed_by_us = True
            self.event(f"Bevorzugte Kerne → {MODE_LABELS[desired]} (amd_x3d_mode)")

    def _release_all(self, all_cpus: set[int]) -> None:
        release_affinity(list(self.pinned), all_cpus, pinned=self.pinned, dry_run=self.dry_run)
        release_affinity(list(self.bg_pinned), all_cpus, pinned=self.bg_pinned, dry_run=self.dry_run)
        self.pinned.clear()
        self.bg_pinned.clear()

    def shutdown(self) -> None:
        all_cpus = set(self.topo.all_cpus) or set(os.sched_getaffinity(0))
        self._release_all(all_cpus)
        if self.mode_changed_by_us and self.settings.idle_mode in ("cache", "frequency") and not self.dry_run:
            if self._read_mode() != self.settings.idle_mode and self._mode_writable():
                self._write_mode(self.settings.idle_mode)
        self.event("Dienst beendet")
        self.write_state(stopped=True, force=True)

    # -- Zustand für die Oberfläche ---------------------------------------------------
    def state(self, stopped: bool = False) -> dict:
        return {
            "ts": time.time(), "pid": os.getpid(), "running": not stopped, "dry_run": self.dry_run,
            "enabled": self.settings.enabled, "mode": self._read_mode(), "mode_writable": self._mode_writable(),
            "desired_mode": self.desired_mode, "topology": self.topo.to_dict(),
            "settings": asdict(self.settings),
            "sessions": [s.to_dict() for s in self.sessions],
            "background_threads": len(self.bg_pinned), "game_threads": len(self.pinned),
            "events": [[ts, text] for ts, text in self.events],
        }

    def write_state(self, stopped: bool = False, force: bool = False) -> None:
        st = self.state(stopped)
        key = json.dumps({k: v for k, v in st.items() if k not in ("ts", "mode")}, sort_keys=True, default=str)
        now = time.time()
        if not force and key == self._last_state and now - self._last_state_ts < 4.0:
            return
        self._last_state, self._last_state_ts = key, now
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(st, ensure_ascii=False, default=str), encoding="utf-8")
            tmp.replace(self.state_file)
        except OSError as exc:
            log.warning("Zustand nicht geschrieben: %s", exc)


def read_state(path: Path = STATE_FILE) -> dict | None:
    try:
        st = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return st if isinstance(st, dict) else None


def daemon_alive(path: Path = STATE_FILE, max_age: float = STATE_MAX_AGE) -> bool:
    st = read_state(path)
    if not st or not st.get("running"):
        return False
    if time.time() - float(st.get("ts", 0)) > max_age:
        return False
    try:
        os.kill(int(st.get("pid", 0)), 0)
    except (OSError, ValueError):
        return False
    return True


def run_daemon(dry_run: bool = False, interval: float = INTERVAL, max_ticks: int | None = None,
               cfg: Config | None = None) -> int:
    """Hauptschleife des Dienstes (blockiert). Nur eine Instanz je Nutzer (Lock-Datei)."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    lock = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print(tr("Der X3D-Dienst läuft bereits."), file=sys.stderr)
        return 3
    topo = topology(refresh=True)
    ctl = Controller(cfg, dry_run=dry_run, topo=topo)
    if not topo.dual:
        ctl.event(f"Kein X3D-Prozessor mit zwei unterschiedlichen CCDs erkannt ({topo.summary(localized=False)}) – Dienst wartet nur.")
    else:
        ctl.event(f"X3D-Dienst gestartet: {topo.cpu_model} – {topo.summary(localized=False)} – Cache-CCD = CCD{topo.cache_ccd.id}"
                  + (" (Probelauf)" if dry_run else ""))
        if mode_path() is None:
            ctl.event("Hinweis: Kernel-Treiber amd_3d_vcache fehlt – nur Affinität, keine CPPC-Rangfolge")
        elif not mode_writable():
            ctl.event("Hinweis: kein Schreibrecht auf amd_x3d_mode – Rangfolge wird nicht umgeschaltet (Zugriff einrichten)")
    stop = {"flag": False}

    def _stop(*_: object) -> None:
        stop["flag"] = True
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, _stop)
    ticks = 0
    try:
        while not stop["flag"]:
            t0 = time.monotonic()
            try:
                ctl.tick()
            except Exception:  # noqa: BLE001 – ein fehlerhafter Takt darf den Dienst nicht beenden
                log.exception("Takt fehlgeschlagen")
            ticks += 1
            if max_ticks is not None and ticks >= max_ticks:
                break
            rest = interval - (time.monotonic() - t0)
            if rest > 0:
                time.sleep(rest)
    finally:
        ctl.shutdown()
        try:
            fcntl.flock(lock, fcntl.LOCK_UN)
        finally:
            lock.close()
    return 0


# ====================================================================== systemd --user
def user_unit_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "systemd" / "user"


def daemon_argv() -> tuple[list[str], str | None]:
    """Startbefehl des Dienstes und ggf. Arbeitsverzeichnis (Start aus dem Quellbaum)."""
    exe = shutil.which("cachyos-dailytool") or shutil.which("cachy-toolkit")
    if not exe:
        for cand in (Path.home() / ".local/bin/cachyos-dailytool", Path("/usr/bin/cachyos-dailytool")):
            if cand.exists():
                exe = str(cand)
                break
    if exe:
        return [exe, "--x3d-daemon"], None
    root = Path(__file__).resolve().parents[1]
    return [sys.executable or "python3", "-m", "cachyos_dailytool", "--x3d-daemon"], str(root)


def unit_text() -> str:
    argv, cwd = daemon_argv()
    lines = ["[Unit]", "Description=CachyOS DailyTool – X3D-Kernzuteilung für Spiele (CCD)",
             "After=graphical-session.target", "", "[Service]", "Type=simple", f"ExecStart={shlex.join(argv)}",
             "Restart=on-failure", "RestartSec=3", "Environment=PYTHONUNBUFFERED=1"]
    if cwd:
        lines.append(f"WorkingDirectory={cwd}")
    lines += ["", "[Install]", "WantedBy=default.target", ""]
    return "\n".join(lines)


def _systemctl(*args: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["systemctl", "--user", *args], text=True, capture_output=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(list(args), 1, "", str(exc))


def systemd_user_available() -> bool:
    return shutil.which("systemctl") is not None and _systemctl("is-system-running").returncode in (0, 1)


def unit_installed() -> bool:
    return (user_unit_dir() / UNIT_NAME).exists()


def autostart_enabled() -> bool:
    return _systemctl("is-enabled", UNIT_NAME).stdout.strip() == "enabled"


def service_active() -> bool:
    return _systemctl("is-active", UNIT_NAME).stdout.strip() == "active"


def install_unit() -> Path:
    d = user_unit_dir()
    d.mkdir(parents=True, exist_ok=True)
    p = d / UNIT_NAME
    p.write_text(unit_text(), encoding="utf-8")
    _systemctl("daemon-reload")
    return p


def set_autostart(on: bool) -> str | None:
    """Dienst beim Anmelden starten (systemd --user). Liefert eine Fehlermeldung oder None."""
    if not systemd_user_available():
        return tr("systemd --user ist nicht verfügbar")
    if on:
        install_unit()
        r = _systemctl("enable", UNIT_NAME)
    else:
        r = _systemctl("disable", UNIT_NAME)
    return None if r.returncode == 0 else (r.stderr.strip() or r.stdout.strip() or tr("systemctl meldete einen Fehler"))


def start_service() -> str | None:
    if systemd_user_available():
        install_unit()
        r = _systemctl("start", UNIT_NAME)
        if r.returncode == 0:
            return None
        return r.stderr.strip() or tr("systemctl start fehlgeschlagen")
    argv, cwd = daemon_argv()
    try:
        subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    except OSError as exc:
        return str(exc)
    return None


def stop_service() -> str | None:
    if systemd_user_available() and service_active():
        r = _systemctl("stop", UNIT_NAME)
        if r.returncode != 0:
            return r.stderr.strip() or tr("systemctl stop fehlgeschlagen")
    st = read_state()
    if st and st.get("running"):
        try:
            os.kill(int(st.get("pid", 0)), signal.SIGTERM)
        except (OSError, ValueError):
            pass
    return None


def remove_unit() -> None:
    _systemctl("disable", "--now", UNIT_NAME)
    try:
        (user_unit_dir() / UNIT_NAME).unlink()
    except OSError:
        pass
    _systemctl("daemon-reload")


def daemon_log_path() -> Path:
    return LOG_DIR / "x3d.log"


def print_status() -> int:
    """Kommandozeile: Topologie, Rangfolge, Dienst und erkannte Spiele (einmaliger Durchlauf ohne Eingriff)."""
    topo = topology(refresh=True)
    print(tr("Prozessor : {0}").format(topo.cpu_model or "?"))
    print(tr("Topologie : {0}").format(topo.summary())
          + (tr(" – Cache-CCD = CCD{0}").format(topo.cache_ccd.id) if topo.dual else tr(" – kein Dual-CCD-X3D")))
    mp = mode_path()
    print(tr("Rangfolge : {0}").format(read_mode() or tr("kein Treiber"))
          + (f" ({mp}, {tr('schreibbar') if mode_writable() else tr('nur lesbar')})" if mp else ""))
    st = read_state()
    alive = daemon_alive()
    print(tr("Dienst    : {0}").format(tr("läuft") if alive else tr("läuft nicht"))
          + (f" (PID {st.get('pid')})" if alive and st else ""))
    if not topo.dual:
        return 0
    cfg = Config()
    det = Detector(topo, Settings.from_config(cfg), Catalog(), UserRules(), registry_apps())
    sessions, _ = det.detect()
    if not sessions:
        print(tr("Spiele    : keine erkannt"))
    for s in sessions:
        cpus = topo.cpus_for(s.mode)
        on = 0
        total = 0
        for pid in s.pids:
            for tid, _psr, _t in thread_stats(pid):
                total += 1
                try:
                    if not cpus or set(os.sched_getaffinity(tid)) <= set(cpus):
                        on += 1
                except OSError:
                    pass
        print(tr("Spiel     : {name} [{source}] → {mode}{cpus} – {reason}; Wurzel {root}, {procs} Prozesse, "
                 "{on}/{total} Threads im Ziel-CCD").format(
            name=tr(s.name), source=tr(SOURCE_LABELS.get(s.source, s.source)), mode=tr(MODE_LABELS[s.mode]),
            cpus=(" (CPUs " + format_cpu_list(cpus) + ")") if cpus else "", reason=tr(s.reason), root=s.root,
            procs=len(s.pids), on=on, total=total))
    return 0
