"""Katalog bekannter Windows-Launcher: offizielle Installer, unbeaufsichtigte Installation, Pfad des Hauptprogramms,
Proton-Eigenheiten. Dazu Download mit Fortschritt, Erkennung installierter Launcher in Prefixen und Pfadübersetzung."""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "cachy-daily-toolkit" / "installers"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
MZ = b"MZ"
OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"   # MSI


@dataclass(frozen=True)
class LauncherSpec:
    id: str
    name: str
    description: str
    url: str
    filename: str
    kind: str = "exe"                       # exe | msi
    install_args: tuple[str, ...] = ()      # immer übergeben (z. B. Installationspfad, Autostart aus)
    silent_args: tuple[str, ...] = ()       # zusätzlich bei „Unbeaufsichtigt installieren“ – ohne eingebettete Anführungszeichen
    silent_default: bool = False            # unbeaufsichtigt standardmäßig an (nur wenn belegt zuverlässig)
    url_resolver: str = ""                  # "gog-remote-config": aktuelle Offline-Installer-URL dynamisch ermitteln
    recommended_tool: str = ""              # CompatTool-ID, die der Assistent vorschlägt (falls verfügbar)
    runner_note: str = ""
    search_hint: str = ""                   # Namenssuche nur in Pfaden, die diesen Teil enthalten (z. B. "Rockstar Games")
    post_install_files: tuple[tuple[str, str], ...] = ()   # (Windows-Pfad, Inhalt) – nur anlegen, wenn nicht vorhanden
    exe_paths: tuple[str, ...] = ()         # Windows-Pfade, wichtigster zuerst (%LOCALAPPDATA%, %USERNAME% erlaubt)
    exe_names: tuple[str, ...] = ()         # für die Suche, falls der Pfad abweicht
    launch_args: str = ""
    env: tuple[tuple[str, str], ...] = ()
    winetricks: tuple[str, ...] = ()
    umu_gameid: str = ""
    umu_store: str = ""
    uninstall_match: tuple[str, ...] = ()   # Teile des Anzeigenamens in der Windows-Programmliste
    install_notes: str = ""
    known_issues: tuple[str, ...] = ()

    @property
    def env_dict(self) -> dict[str, str]:
        d = dict(self.env)
        if self.umu_gameid:
            d.setdefault("GAMEID", self.umu_gameid)
        if self.umu_store:
            d.setdefault("STORE", self.umu_store)
        return d


NO_WAYLAND = (("PROTON_ENABLE_WAYLAND", "0"),)   # CEF-Oberflächen der Launcher laufen unter XWayland stabiler (Faugus, GE-Proton #188)
GE = "umu:GE-Proton"

UBISOFT_SETTINGS = """overlay:
  enabled: false
  forceunhookgame: false
  fps_enabled: false
  warning_enabled: false
user:
  closebehavior: CloseBehavior_Close
"""

# Quellen: Lutris-Installationsskripte, Faugus Launcher (umu-run), umu-database/umu-protonfixes, Proton-Quellcode,
# Herstellerseiten; Download-Adressen am 2026-10-16 per Header-Abruf geprüft.
CATALOG: tuple[LauncherSpec, ...] = (
    LauncherSpec(
        id="ubisoft-connect", name="Ubisoft Connect",
        description="Ubisofts Launcher (früher Uplay) für Assassin's Creed, Far Cry, Anno und weitere.",
        url="https://static3.cdn.ubi.com/orbit/launcher_installer/UbisoftConnectInstaller.exe",
        filename="UbisoftConnectInstaller.exe", silent_args=("/S",), silent_default=True,
        exe_paths=(r"C:\Program Files (x86)\Ubisoft\Ubisoft Game Launcher\UbisoftConnect.exe",
                   r"C:\Program Files (x86)\Ubisoft\Ubisoft Game Launcher\upc.exe",
                   r"C:\Program Files (x86)\Ubisoft\Ubisoft Game Launcher\Uplay.exe"),
        exe_names=("UbisoftConnect.exe", "upc.exe", "Uplay.exe"), env=NO_WAYLAND, uninstall_match=("Ubisoft Connect", "Uplay"),
        recommended_tool=GE, runner_note="Am besten belegt mit GE-Proton (half 2025 gegen „unsolvable error“).",
        post_install_files=((r"%LOCALAPPDATA%\Ubisoft Game Launcher\settings.yaml", UBISOFT_SETTINGS),),
        install_notes="Beim ersten Start aktualisiert sich Ubisoft Connect selbst und startet ggf. neu; erst danach erscheint die Anmeldung. "
                      "Overlay ist abgeschaltet, Schließen beendet den Client (statt im Tray weiterzulaufen).",
        known_issues=("Weißes Fenster oder „unsolvable error“: anderes Proton wählen (GE-Proton) bzw. den Prefix neu anlegen.",
                      "Spiele nicht unter /run/media installieren (dort meldet der Client zu wenig Platz) – besser im Prefix oder unter /mnt."),
    ),
    LauncherSpec(
        id="ea-app", name="EA App",
        description="Electronic Arts' Launcher (Nachfolger von Origin) für Battlefield, Die Sims, EA Sports FC u. a.",
        url="https://origin-a.akamaihd.net/EA-Desktop-Client-Download/installer-releases/EAappInstaller.exe",
        filename="EAappInstaller.exe", install_args=("EAX_LAUNCH_CLIENT=0",), silent_args=("/S",), silent_default=False,
        exe_paths=(r"C:\Program Files\Electronic Arts\EA Desktop\EA Desktop\EALauncher.exe",
                   r"C:\Program Files\Electronic Arts\EA Desktop\EA Desktop\EADesktop.exe"),
        exe_names=("EALauncher.exe", "EADesktop.exe"), search_hint="Electronic Arts", env=NO_WAYLAND,
        uninstall_match=("EA app", "EA Desktop"), recommended_tool=GE, runner_note="Belegt mit GE-Proton 10 (Lutris nutzt dwproton).",
        install_notes="Der Installer lädt die eigentliche App während der Installation aus dem Netz. Rückgabecode 768 ist unter Wine normal.",
        known_issues=("Prefix nicht auf NTFS anlegen – die Installation bricht dort ab (WRITE_OWNER).",
                      "Bei manchen Versionen liegt EALauncher.exe in einem Versionsordner; das Tool sucht ihn dann automatisch."),
    ),
    LauncherSpec(
        id="epic-games", name="Epic Games Launcher",
        description="Epic Games Store mit wöchentlichen Gratisspielen und Unreal-Titeln (Spiele mit Anti-Cheat laufen meist nicht).",
        url="https://launcher-public-service-prod06.ol.epicgames.com/launcher/api/installer/download/EpicGamesLauncherInstaller.msi",
        filename="EpicGamesLauncherInstaller.msi", kind="msi", silent_args=("/q",), silent_default=True,
        exe_paths=(r"C:\Program Files\Epic Games\Launcher\Portal\Binaries\Win64\EpicGamesLauncher.exe",
                   r"C:\Program Files (x86)\Epic Games\Launcher\Portal\Binaries\Win64\EpicGamesLauncher.exe",
                   r"C:\Program Files (x86)\Epic Games\Launcher\Portal\Binaries\Win32\EpicGamesLauncher.exe",
                   r"C:\Program Files\Epic Games\Launcher\Portal\Binaries\Win32\EpicGamesLauncher.exe"),
        exe_names=("EpicGamesLauncher.exe",), launch_args="-opengl -SkipBuildPatchPrereq", env=NO_WAYLAND,
        umu_gameid="umu-default", umu_store="egs", uninstall_match=("Epic Games Launcher",),
        recommended_tool=GE, runner_note="Am besten belegt mit GE-Proton über umu (umu-README, Lutris).",
        install_notes="Nach der stillen Installation öffnet sich der Launcher von selbst; die Verknüpfung wird dann schon angelegt. "
                      "Das Protokoll endet, wenn du den Launcher schließt.",
    ),
    LauncherSpec(
        id="gog-galaxy", name="GOG Galaxy 2.0",
        description="Offizieller GOG-Client unter Proton (Alternative zum nativen galaxy-linux).",
        url="https://content-system.gog.com/open_link/download?path=/open/galaxy/client/setup_galaxy_2.1.10.56.exe",
        url_resolver="gog-remote-config", filename="setup_galaxy.exe",
        silent_args=("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-"), silent_default=True,
        exe_paths=(r"C:\Program Files\GOG Galaxy\GalaxyClient.exe", r"C:\Program Files (x86)\GOG Galaxy\GalaxyClient.exe"),
        exe_names=("GalaxyClient.exe",), launch_args="--in-process-gpu /runWithoutUpdating /deelevated",
        env=NO_WAYLAND + (("WINEDLLOVERRIDES", "libglesv2=d"),), winetricks=("corefonts", "mfc140"),
        uninstall_match=("GOG GALAXY", "GOG Galaxy"), recommended_tool=GE,
        runner_note="Belegt mit GE-Proton (Lutris). Bei Flackern/hoher CPU-Last mit Proton 10 ein anderes Proton testen.",
        install_notes="Lädt den vollständigen Offline-Installer (rund 340 MB, MD5-geprüft). Beim ersten Start einen Spiele-Ordner "
                      "außerhalb von drive_c wählen.",
        known_issues=("Schwarzes Fenster: libglesv2 ist deaktiviert und corefonts wird installiert (WineHQ-Bug 50212).",
                      "Bei aktivierten Cloud-Speicherständen kann GalaxyCommunication.exe viel RAM belegen."),
    ),
    LauncherSpec(
        id="battlenet", name="Battle.net",
        description="Blizzards Launcher für Diablo, Overwatch, World of Warcraft, StarCraft und Call of Duty.",
        url="https://downloader.battle.net/download/getInstallerForGame?os=win&gameProgram=BATTLENET_APP&version=Live",
        filename="Battle.net-Setup.exe", install_args=("--lang=deDE", r"--installpath=C:\Program Files (x86)\Battle.net"),
        exe_paths=(r"C:\Program Files (x86)\Battle.net\Battle.net.exe", r"C:\Program Files (x86)\Battle.net\Battle.net Launcher.exe"),
        exe_names=("Battle.net.exe", "Battle.net Launcher.exe"),
        env=NO_WAYLAND + (("WINE_SIMULATE_WRITECOPY", "1"), ("WINEDLLOVERRIDES", "locationapi=d")),
        uninstall_match=("Battle.net",), runner_note="Proton ab Version 10 verwenden (proton-cachyos oder GE-Proton 10+).",
        install_notes="Kein echter Silent-Modus: Nach der Installation öffnet sich das Anmeldefenster – die Verknüpfung wird dann "
                      "schon angelegt; das Protokoll endet, wenn du es schließt.",
        known_issues=("Battle.net Launcher.exe stürzt mit Proton 9 ab – deshalb startet die Verknüpfung Battle.net.exe.",),
    ),
    LauncherSpec(
        id="rockstar", name="Rockstar Games Launcher",
        description="Rockstars Launcher für GTA V, Red Dead Redemption 2 und L.A. Noire.",
        url="https://gamedownloads.rockstargames.com/public/installer/Rockstar-Games-Launcher.exe",
        filename="Rockstar-Games-Launcher.exe",
        exe_paths=(r"C:\Program Files\Rockstar Games\Launcher\Launcher.exe", r"C:\Program Files (x86)\Rockstar Games\Launcher\Launcher.exe",
                   r"C:\ProgramData\Rockstar Games\Launcher\Launcher.exe"),
        exe_names=("Launcher.exe",), search_hint="Rockstar Games", env=NO_WAYLAND, winetricks=(),
        uninstall_match=("Rockstar Games Launcher",), runner_note="Belegt mit Valve-Proton 10/11 – proton-cachyos passt.",
        install_notes="Interaktive Installation: Standardpfad beibehalten und am Ende „Launcher starten“ abwählen; "
                      "startet er trotzdem, das Fenster schließen.",
        known_issues=("Spiele mit BattlEye (GTA Online) laufen unter Linux nicht.",),
    ),
    LauncherSpec(
        id="amazon-games", name="Amazon Games",
        description="Amazon-Launcher für Prime-Gaming-Titel.",
        url="https://download.amazongames.com/AmazonGamesSetup.exe",
        filename="AmazonGamesSetup.exe",
        exe_paths=(r"%LOCALAPPDATA%\Amazon Games\App\Amazon Games.exe",),
        exe_names=("Amazon Games.exe",), env=NO_WAYLAND, uninstall_match=("Amazon Games",),
        runner_note="Belegt mit proton-cachyos über umu (Faugus Launcher).",
        install_notes="Der Installer lädt die App online nach und öffnet am Ende das Anmeldefenster – die Verknüpfung wird dann "
                      "schon angelegt; das Protokoll endet, wenn du es schließt.",
    ),
)

def _test_launcher_from_env() -> tuple[LauncherSpec, ...]:
    """Nur für automatisierte Oberflächentests (mit CACHY_TOOLKIT_CONTROL): zusätzlicher Ersatz-Launcher aus JSON."""
    path = os.environ.get("CACHY_TOOLKIT_TEST_LAUNCHER")
    if not (path and os.environ.get("CACHY_TOOLKIT_CONTROL")):
        return ()
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        fields = {k: (tuple(tuple(x) if isinstance(x, list) else x for x in v) if isinstance(v, list) else v)
                  for k, v in d.items() if k in LauncherSpec.__dataclass_fields__}
        return (LauncherSpec(**fields),)
    except (OSError, ValueError, TypeError):
        return ()


CATALOG = CATALOG + _test_launcher_from_env()

# Bewusst nicht im Katalog: Steam (Windows) – Proton ersetzt steam.exe zwingend durch seine eingebaute Version
# (DLL-Override steam.exe=b), der Windows-Client ist unter umu/Proton daher nicht nutzbar.


def get(spec_id: str) -> LauncherSpec | None:
    return next((s for s in CATALOG if s.id == spec_id), None)


# --------------------------------------------------------------------------- Pfade
def prefix_users(wine_dir: Path) -> list[str]:
    users_dir = wine_dir / "drive_c" / "users"
    names: list[str] = []
    try:
        for d in sorted(users_dir.iterdir()):
            if d.is_dir() and not d.is_symlink() and d.name.lower() not in ("public", "default", "default user", "all users"):
                names.append(d.name)
    except OSError:
        pass
    for fallback in ("steamuser", os.environ.get("USER", "")):
        if fallback and fallback not in names:
            names.append(fallback)
    return names


def _expand_tokens(winpath: str, user: str) -> str:
    rep = {
        "%LOCALAPPDATA%": rf"C:\users\{user}\AppData\Local",
        "%APPDATA%": rf"C:\users\{user}\AppData\Roaming",
        "%USERPROFILE%": rf"C:\users\{user}",
        "%USERNAME%": user,
        "%PROGRAMFILES(X86)%": r"C:\Program Files (x86)",
        "%PROGRAMFILES%": r"C:\Program Files",
        "%PROGRAMDATA%": r"C:\ProgramData",
    }
    out = winpath
    for k, v in rep.items():
        out = re.sub(re.escape(k), lambda _m, v=v: v, out, flags=re.I)
    return out


def resolve_ci(base: Path, parts: list[str]) -> Path | None:
    """Pfad unter base Groß-/Kleinschreibung-unabhängig auflösen (Windows-Semantik auf Linux-Dateisystem)."""
    cur = base
    for part in parts:
        if not part:
            continue
        direct = cur / part
        if direct.exists():
            cur = direct
            continue
        try:
            match = next((c for c in cur.iterdir() if c.name.lower() == part.lower()), None)
        except OSError:
            return None
        if match is None:
            return None
        cur = match
    return cur


def win_to_unix(wine_dir: Path, winpath: str) -> list[Path]:
    """Alle vorhandenen Unix-Pfade zu einem Windows-Pfad im Prefix (mehrere Benutzer möglich)."""
    found: list[Path] = []
    for user in prefix_users(wine_dir) or ["steamuser"]:
        p = _expand_tokens(winpath, user).replace("/", "\\")
        m = re.match(r"^([A-Za-z]):\\(.*)$", p)
        if not m:
            continue
        drive, rest = m.group(1).lower(), m.group(2)
        base = wine_dir / f"drive_{drive}"
        hit = resolve_ci(base, rest.split("\\"))
        if hit and hit.exists() and hit not in found:
            found.append(hit)
    return found


def unix_to_win(wine_dir: Path, path: Path) -> str:
    try:
        rel = Path(path).relative_to(wine_dir / "drive_c")
    except ValueError:
        return str(path)
    return "C:\\" + "\\".join(rel.parts)


def find_exe(spec: LauncherSpec, wine_dir: Path, search: bool = False) -> Path | None:
    """Hauptprogramm des Launchers im Prefix: bekannte Pfade, optional Namenssuche unter drive_c (ohne Windows-Ordner)."""
    for wp in spec.exe_paths:
        hits = win_to_unix(wine_dir, wp)
        if hits:
            return hits[0]
    if not search or not spec.exe_names:
        return None
    names = {n.lower() for n in spec.exe_names}
    drive_c = wine_dir / "drive_c"
    best: Path | None = None
    try:
        for dirpath, dirnames, filenames in os.walk(drive_c):
            rel = Path(dirpath).relative_to(drive_c)
            if rel.parts and rel.parts[0].lower() == "windows":
                dirnames[:] = []
                continue
            for f in filenames:
                if f.lower() in names:
                    cand = Path(dirpath) / f
                    if spec.search_hint and spec.search_hint.lower() not in str(cand).lower():
                        continue
                    if best is None or len(cand.parts) < len(best.parts):
                        best = cand
    except OSError:
        pass
    return best


def write_post_install(spec: LauncherSpec, wine_dir: Path) -> list[Path]:
    """Empfohlene Einstellungsdateien anlegen (nur wenn noch nicht vorhanden)."""
    written: list[Path] = []
    users = prefix_users(wine_dir)
    if not users:
        return written
    for winpath, content in spec.post_install_files:
        p = _expand_tokens(winpath, users[0]).replace("/", "\\")
        m = re.match(r"^([A-Za-z]):\\(.*)$", p)
        if not m:
            continue
        target = wine_dir / f"drive_{m.group(1).lower()}" / Path(*m.group(2).split("\\"))
        existing = resolve_ci(wine_dir / f"drive_{m.group(1).lower()}", m.group(2).split("\\"))
        if existing and existing.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written.append(target)
    return written


def match_exe(path: str | Path, wine_dir: Path | None = None) -> LauncherSpec | None:
    """Gehört eine gefundene .exe zu einem bekannten Launcher? (Pfad-Endung oder Dateiname)"""
    p = str(path).replace("\\", "/").lower()
    for spec in CATALOG:
        for wp in spec.exe_paths:
            tail = re.sub(r"^([a-z]:|%[^%]+%)", "", wp.replace("\\", "/").lower())
            if tail and p.endswith(tail):
                return spec
    name = Path(str(path)).name.lower()
    for spec in CATALOG:
        if spec.id == "rockstar" and name == "launcher.exe" and "rockstar games" not in p:
            continue        # "Launcher.exe" ist zu allgemein
        if name in {n.lower() for n in spec.exe_names}:
            return spec
    return None


def spec_for_installer(path: str | Path) -> LauncherSpec | None:
    """Bekannter Launcher anhand des Installer-Dateinamens (z. B. UbisoftConnectInstaller.exe, EpicInstaller-20.3.3.msi)."""
    name = Path(str(path)).name.lower()
    stem = re.sub(r"\s*\(\d+\)$", "", Path(name).stem)              # Browser-Duplikat „… (1)“
    stem = re.sub(r"[-_ ]?v?\d+(\.\d+)*$", "", stem)                 # Versionsnummer
    for spec in CATALOG:
        fn = spec.filename.lower()
        if name == fn or stem == Path(fn).stem.lower():
            return spec
    if name.startswith("epicinstaller") and name.endswith(".msi"):
        return get("epic-games")
    if name.startswith("battle.net-setup"):
        return get("battlenet")
    return None


def detect_installed(wine_dir: Path) -> list[tuple[LauncherSpec, Path]]:
    out: list[tuple[LauncherSpec, Path]] = []
    for spec in CATALOG:
        exe = find_exe(spec, wine_dir)
        if exe:
            out.append((spec, exe))
    return out


# --------------------------------------------------------------------------- Download
class DownloadError(RuntimeError):
    pass


def _filename_from(resp, url: str, fallback: str) -> str:
    cd = resp.headers.get("Content-Disposition", "")
    m = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)\"?", cd, re.I)
    final = urllib.parse.urlparse(resp.geturl() or url)
    if m:
        name = urllib.parse.unquote(m.group(1)).strip()
    else:
        name = Path(final.path).name
        if not name.lower().endswith((".exe", ".msi")):
            # z. B. GOG: …/open_link/download?path=/open/galaxy/client/setup_galaxy_2.1.10.56.exe
            for v in urllib.parse.parse_qs(final.query).get("path", []):
                if v.lower().endswith((".exe", ".msi")):
                    name = Path(v).name
    name = re.sub(r"[^A-Za-z0-9._ ()+-]", "_", name or "")
    if not name or not name.lower().endswith((".exe", ".msi")):
        name = fallback
    return name


def check_magic(path: Path) -> bool:
    try:
        with open(path, "rb") as fh:
            head = fh.read(8)
    except OSError:
        return False
    return head.startswith(MZ) or head.startswith(OLE)


GOG_REMOTE_CONFIG = "https://remote-config.gog.com/components/webinstaller?component_version=2.0.0"


def resolve_url(spec: LauncherSpec, log: Callable[[str], None], opener=urllib.request.urlopen) -> tuple[str, str]:
    """(URL, erwartete MD5 oder ""). Bei Fehlern der Abfrage gilt die feste URL aus dem Katalog."""
    if spec.url_resolver == "gog-remote-config":
        try:
            req = urllib.request.Request(GOG_REMOTE_CONFIG, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
            with opener(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace"))
            win = (data.get("content") or {}).get("windows") or {}
            link = win.get("downloadLink") or ""
            if link.startswith("https://"):
                log(f"Aktuelle Version laut GOG: {win.get('version', '?')}")
                return link, str(win.get("installerMd5") or "")
        except Exception as exc:
            log(f"GOG-Versionsabfrage fehlgeschlagen ({exc}) – nutze bekannte Version.")
    return spec.url, ""


def _md5(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(spec: LauncherSpec, log: Callable[[str], None], cancelled: Callable[[], bool] = lambda: False,
             cache_dir: Path = CACHE_DIR, max_age_days: float = 3.0, opener=urllib.request.urlopen) -> Path:
    """Installer herunterladen (oder aus dem Cache nehmen, wenn jünger als max_age_days und gleich groß)."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    url, md5 = resolve_url(spec, log, opener)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    log(f"Lade {spec.name}-Installer von {urllib.parse.urlparse(url).netloc} …")
    try:
        resp = opener(req, timeout=60)
    except Exception as exc:  # urllib.error.URLError, socket.timeout, ...
        raise DownloadError(f"Download fehlgeschlagen: {exc}") from exc
    with resp:
        status = getattr(resp, "status", 200)
        if status and status >= 400:
            raise DownloadError(f"Server antwortete mit HTTP {status}")
        name = _filename_from(resp, url, spec.filename)
        target = cache_dir / name
        total = int(resp.headers.get("Content-Length") or 0)
        if target.exists() and check_magic(target) and (time.time() - target.stat().st_mtime) < max_age_days * 86400 \
                and (not total or target.stat().st_size == total) and (not md5 or _md5(target) == md5.lower()):
            log(f"Installer aus dem Zwischenspeicher: {target} ({target.stat().st_size / 1048576:.1f} MiB)")
            return target
        part = target.with_suffix(target.suffix + ".part")
        done = 0
        last = 0.0
        with open(part, "wb") as fh:
            while True:
                if cancelled():
                    fh.close()
                    part.unlink(missing_ok=True)
                    raise DownloadError("Download abgebrochen.")
                chunk = resp.read(1 << 16)
                if not chunk:
                    break
                fh.write(chunk)
                done += len(chunk)
                now = time.monotonic()
                if now - last > 0.7:
                    last = now
                    if total:
                        log(f"Download {done * 100 // total} % ({done / 1048576:.1f} / {total / 1048576:.1f} MiB)")
                    else:
                        log(f"Download {done / 1048576:.1f} MiB")
    if total and done != total:
        part.unlink(missing_ok=True)
        raise DownloadError(f"Download unvollständig ({done} von {total} Bytes).")
    if not check_magic(part):
        part.unlink(missing_ok=True)
        raise DownloadError("Die heruntergeladene Datei ist kein Windows-Installer (Server lieferte vermutlich eine Webseite).")
    if md5:
        got = _md5(part)
        if got != md5.lower():
            part.unlink(missing_ok=True)
            raise DownloadError(f"Prüfsumme stimmt nicht (MD5 {got}, erwartet {md5}).")
        log("MD5-Prüfsumme stimmt.")
    part.replace(target)
    log(f"Download fertig: {target} ({done / 1048576:.1f} MiB)")
    return target
