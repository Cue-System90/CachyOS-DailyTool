"""Startpläne für Windows-Programme in Prefixen: umu-launcher, Proton direkt oder Wine – adaptiert aus dem Galaxy-Port."""
from __future__ import annotations

import logging
import os
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from . import compat, launch_options, launchers, system
from .compat import CompatTool
from .registry import AppEntry, Registry

log = logging.getLogger(__name__)


class LaunchError(Exception):
    pass


@dataclass
class LaunchPlan:
    argv: list[str]
    env: dict[str, str]
    cwd: str
    tool: CompatTool | None
    prefix: str
    runner: str                  # umu | proton | wine
    description: str

    def shell_preview(self) -> str:
        keys = ("WINEPREFIX", "PROTONPATH", "GAMEID", "STORE", "PROTON_VERB", "STEAM_COMPAT_DATA_PATH",
                "MANGOHUD", "ENABLE_VKBASALT", "PROTON_LOG", "DXVK_HUD", "WINEDEBUG", "WINEDLLOVERRIDES")
        env = " ".join(f"{k}={shlex.quote(self.env[k])}" for k in keys if k in self.env and self.env[k] != os.environ.get(k))
        extra = {k: v for k, v in self.env.items() if k not in keys and os.environ.get(k) != v and not k.startswith("STEAM_COMPAT")}
        env2 = " ".join(f"{k}={shlex.quote(v)}" for k, v in sorted(extra.items())[:8])
        return " ".join(x for x in (env, env2, shlex.join(self.argv)) if x)


class Launcher:
    def __init__(self, cfg, registry: Registry) -> None:
        self.cfg = cfg
        self.reg = registry
        self._tools: list[CompatTool] | None = None

    # ------------------------------------------------------------------ Tools
    def tools(self, refresh: bool = False) -> list[CompatTool]:
        if self._tools is None or refresh:
            self._tools = compat.discover()
        return self._tools

    @property
    def prefer_umu(self) -> bool:
        return self.cfg.get("PREFER_UMU", "1") == "1"

    def default_tool(self) -> CompatTool | None:
        wanted = self.cfg.get("DEFAULT_TOOL", compat.AUTO_ID)
        if wanted and wanted != compat.AUTO_ID:
            t = compat.resolve(wanted, self.tools())
            if t:
                return t
        return compat.default_tool(self.tools(), self.prefer_umu)

    def resolve_tool(self, prefix: str | Path, override: str = "") -> CompatTool | None:
        """Runner für einen Prefix: Override (Anwendung) > Prefix-Einstellung > Standard."""
        for tool_id in (override, self.reg.prefix_settings(prefix).tool):
            if tool_id and tool_id != compat.AUTO_ID:
                t = compat.resolve(tool_id, self.tools())
                if t:
                    return t
                log.warning("Kompatibilitätstool %s nicht gefunden – nutze Vorgabe", tool_id)
        return self.default_tool()

    def fallback_tool(self) -> CompatTool | None:
        """Runner ohne Download: System-Proton (proton-cachyos) > installiertes Proton > Wine."""
        offline = [t for t in self.tools() if t.kind != "umu-latest"]
        return compat.default_tool(offline, prefer_umu=False)

    def umu_latest_problem(self, tool: CompatTool | None) -> str | None:
        """Warum ein umu-„neueste Version“-Runner gerade nicht nutzbar ist (None = nutzbar). Lokale Kopie genügt."""
        if tool is None or tool.kind != "umu-latest":
            return None
        if compat.local_umu_tool(tool.path):
            return None
        rl = compat.github_rate_limit()
        if rl is None:
            return f"{tool.path} ist nicht installiert und GitHub ist nicht erreichbar"
        remaining, reset = rl
        if remaining <= 0:
            when = time.strftime("%H:%M", time.localtime(reset)) if reset else "später"
            return f"{tool.path} ist nicht installiert und das GitHub-Abfragelimit dieser Internetverbindung ist erschöpft (wieder frei um {when} Uhr)"
        return None

    def ensure_runner_step(self, holder: dict):
        """Python-Schritt vor Prefix-Erstellung/Installation: prüft „GE-/UMU-Proton (neueste)“ und weicht bei Bedarf aus.

        holder: {"tool": CompatTool|None, "tool_id": str}; wird bei einem Ausweichen angepasst ("fallback" = Grund)."""
        def step(log, ctx) -> int:
            tool = holder.get("tool") or self.default_tool()
            if tool is None or tool.kind != "umu-latest":
                return 0
            local = compat.local_umu_tool(tool.path)
            if local:
                log(f"{tool.path}: lokale Version {local.name} vorhanden – umu nutzt sie (oder eine neuere, falls verfügbar).")
                return 0
            problem = self.umu_latest_problem(tool)
            if problem is None:
                log(f"umu lädt {tool.path} einmalig herunter (rund 500 MB) – das kann einige Minuten dauern.")
                return 0
            fb = self.fallback_tool()
            if fb is None:
                log(f"{problem}. Es ist auch kein anderes Proton/Wine installiert – bitte später erneut versuchen.")
                return 1
            log(f"{problem}. Weiche aus auf {fb.label}.")
            holder["tool"], holder["tool_id"], holder["fallback"] = fb, fb.id, problem
            return 0
        return step

    def runtime_create_step(self, prefix: str | Path, holder: dict, arch: str = "win64"):
        """Prefix-Erstellung als Python-Schritt: Befehle erst zur Laufzeit mit dem (ggf. ausgewichenen) Runner bauen."""
        def step(log, ctx) -> int:
            try:
                steps = self.create_prefix_steps(prefix, holder.get("tool"), arch)
            except LaunchError as exc:
                log(str(exc))
                return 1
            return system.run_step_sequence(steps, log, cancelled=ctx.cancelled, on_start=ctx.on_start)
        return step

    def runner_for(self, tool: CompatTool) -> str:
        if tool.kind == "umu-latest":
            return "umu"
        if tool.kind == "wine":
            return "wine"
        if compat.umu_available() and (self.prefer_umu or compat.tool_needs_umu(tool)):
            return "umu"
        return "proton"

    @staticmethod
    def wine_prefix_dir(prefix: str | Path) -> Path:
        """Verzeichnis mit drive_c: Proton-Layout <prefix>/pfx; umu legt pfx als Verweis auf den Prefix selbst an → Prefix."""
        p = Path(prefix)
        pfx = p / "pfx"
        try:
            if pfx.is_dir() and pfx.resolve() != p.resolve():
                return pfx
        except OSError:
            pass
        return p

    # ------------------------------------------------------------------ Umgebung
    def _base_env(self, prefix: str | Path, extra: dict[str, str] | None = None) -> dict[str, str]:
        env = os.environ.copy()
        env.pop("LD_PRELOAD", None)
        env.update(self.reg.prefix_settings(prefix).env)
        env.update(extra or {})
        return env

    def _runner_env_argv(self, prefix: Path, tool: CompatTool, exe_argv: list[str], env: dict[str, str],
                         verb: str = "waitforexitandrun", game_id: str = "") -> tuple[str, list[str]]:
        runner = self.runner_for(tool)
        if runner == "umu":
            umu = compat.umu_path()
            if not umu:
                raise LaunchError("umu-run nicht gefunden (Paket umu-launcher).")
            env["WINEPREFIX"] = str(prefix)
            env["PROTONPATH"] = tool.path
            env["GAMEID"] = game_id or env.get("GAMEID") or f"umu-{system.re.sub(r'[^a-z0-9]+', '-', prefix.name.lower()).strip('-') or 'prefix'}"
            env["STORE"] = env.get("STORE", "none")
            env["PROTON_VERB"] = verb
            return runner, [umu, *exe_argv]
        if runner == "proton":
            proton = Path(tool.path) / "proton"
            env["STEAM_COMPAT_DATA_PATH"] = str(prefix)
            root = compat.steam_root() or (Path(self.cfg.wine_prefix_root) / ".steam-client-stub")
            root.mkdir(parents=True, exist_ok=True)
            env["STEAM_COMPAT_CLIENT_INSTALL_PATH"] = str(root)
            env.setdefault("STEAM_COMPAT_APP_ID", "0")
            env.setdefault("SteamAppId", "0")
            env.setdefault("SteamGameId", f"cachy-{prefix.name}")
            if tool.requires_runtime:
                rt = compat.find_runtime(tool.requires_runtime)
                if rt is None:
                    raise LaunchError(f"{tool.name} benötigt die Steam Linux Runtime (AppID {tool.requires_runtime}). "
                                      "Bitte umu-launcher installieren oder ein anderes Proton wählen.")
                return runner, [str(rt / "_v2-entry-point"), f"--verb={verb}", "--", str(proton), verb, *exe_argv]
            return runner, [str(proton), verb, *exe_argv]
        # wine
        env["WINEPREFIX"] = str(self.wine_prefix_dir(prefix))
        env.setdefault("WINEDEBUG", "-all")
        return runner, [tool.path, *exe_argv]

    # ------------------------------------------------------------------ Plan
    def wayland_default(self) -> bool:
        return self.cfg.get("WAYLAND_DRIVER", "1") == "1"

    # Eigener Wine-Desktop für Programme mit Wayland-Treiber neben einem laufenden XWayland-Programm im selben Prefix.
    # Wine lädt den Grafiktreiber je Windows-Desktop (explorer.exe trägt ihn ein, jeder Prozess des Desktops übernimmt
    # ihn), Kindprozesse erben den Desktop. Läuft z. B. ein Store-Launcher (CEF, nur unter XWayland bedienbar) auf dem
    # Standard-Desktop, stirbt ein direkt mit PROTON_ENABLE_WAYLAND=1 gestartetes Spiel („no driver could be loaded“).
    # Auf „root“ (Wines Desktop ohne virtuelle Fläche) bekommt es den Wayland-Treiber. Belegt am 2026-10-17 mit
    # proton-cachyos 11 (Sandbox): siehe reference/wayland-test-2026-10-17/zweiter-desktop.txt.
    WAYLAND_DESKTOP_ARG = "/desktop=root,1920x1080,wayland"     # Größe ist bei „root“ ohne Wirkung, aber nötig
    START_EXE_WIN = r"C:\windows\system32\start.exe"

    def windows_path(self, prefix: str | Path, path: str | Path) -> str:
        """Linux-Pfad -> Windows-Pfad: unter drive_c als C:\\…, sonst über Z: (zeigt in jedem Prefix auf /)."""
        p = Path(os.path.abspath(Path(path).expanduser()))
        drive_c = self.wine_prefix_dir(prefix) / "drive_c"
        for base in (drive_c, drive_c.resolve() if drive_c.exists() else drive_c):
            try:
                rel = p.relative_to(base)
                return "C:\\" + "\\".join(rel.parts)
            except ValueError:
                continue
        return "Z:" + str(p).replace("/", "\\")

    def prefix_running(self, prefix: str | Path) -> bool:
        """wineserver des Prefix läuft (z. B. ein Launcher) – erkannt über /proc, ohne Wine zu starten."""
        try:
            return bool(system.prefix_wineserver_pids(self.wine_prefix_dir(prefix)))
        except Exception:  # noqa: BLE001 – Erkennung ist Komfort
            return False

    def wayland_desktop_argv(self, prefix: str | Path, exe: str | Path, args: list[str] | None = None) -> list[str]:
        """explorer.exe /desktop=root,…,wayland start.exe /d <Ordner> <Exe> [Argumente] – ohne leeren Titel ("" wäre für
        start.exe das Programm, es bekommt argv bereits ohne Anführungszeichen)."""
        exe_p = Path(exe)
        explorer = self.wine_prefix_dir(prefix) / "drive_c" / "windows" / "explorer.exe"
        return [str(explorer), self.WAYLAND_DESKTOP_ARG, self.START_EXE_WIN, "/d",
                self.windows_path(prefix, exe_p.parent), self.windows_path(prefix, exe_p), *(args or [])]

    def build_plan(self, prefix: str | Path, exe: str | Path, option_texts: list[str] | None = None,
                   extra_env: dict[str, str] | None = None, args: list[str] | None = None,
                   tool: CompatTool | None = None, tool_override: str = "", wayland_driver: str = "") -> LaunchPlan:
        prefix_p = Path(os.path.expanduser(str(prefix)))
        exe_p = Path(os.path.expanduser(str(exe)))
        if not exe_p.is_file():
            raise LaunchError(f"Startdatei nicht gefunden: {exe_p}")
        tool = tool or self.resolve_tool(prefix_p, tool_override)
        if tool is None:
            raise LaunchError("Kein Proton/Wine gefunden. Bitte proton-cachyos, umu-launcher oder wine installieren.")
        env = self._base_env(prefix_p, extra_env)
        exe_argv: list[str] = [str(exe_p), *(args or [])]
        is_msi = exe_p.suffix.lower() == ".msi"
        if is_msi:
            exe_argv = ["msiexec", "/i", str(exe_p), *(args or [])]
        runner = self.runner_for(tool)
        ps = self.reg.prefix_settings(prefix_p)
        if runner in ("umu", "proton") and "PROTON_ENABLE_WAYLAND" not in {**ps.env, **(extra_env or {})}:
            # Wayland-Treiber: Spiele, die im Fenstermodus starten und erst dann auf Vollbild wechseln, behalten unter
            # XWayland den weißen Wine-Fensterrahmen im Bild (z. B. Surviving Mars Relaunched). Vorrang: Anwendung,
            # dann Prefix, dann globale Vorgabe; eine eigene Variable (Umgebung oder Startoptionen) gewinnt immer.
            mode = wayland_driver or ps.wayland_driver
            if mode == "on" or (mode != "off" and self.wayland_default()):
                env["PROTON_ENABLE_WAYLAND"] = "1"
            elif mode == "off":
                env["PROTON_ENABLE_WAYLAND"] = "0"
        texts = [t for t in (option_texts or []) if t and t.strip()]
        opts_text = launch_options.merge(*texts) if len(texts) > 1 else (texts[0] if texts else "")
        opts = launch_options.parse(opts_text)
        # Läuft der Prefix schon (z. B. der Launcher), würde „waitforexitandrun“ bis zu dessen Ende warten. Dann
        # „runinprefix“ (neuer Container, derselbe wineserver – Lutris-Muster) und für den Wayland-Treiber den eigenen
        # Desktop, weil der Standard-Desktop schon den Treiber des ersten Programms hat.
        proton_like = runner in ("umu", "proton")
        busy = proton_like and self.prefix_running(prefix_p)
        wayland = proton_like and opts.env.get("PROTON_ENABLE_WAYLAND", env.get("PROTON_ENABLE_WAYLAND", "")) == "1"
        separate = busy and wayland and not is_msi
        if separate:
            exe_argv = self.wayland_desktop_argv(prefix_p, exe_p, args)
        runner, base = self._runner_env_argv(prefix_p, tool, exe_argv, env,
                                             verb="runinprefix" if busy else "waitforexitandrun")
        env.update(opts.env)
        argv = [*opts.wrapper, *base, *opts.args]
        desc = f"{runner} · {tool.name}"
        if separate:
            desc += " · KDE-Wayland auf eigenem Desktop (Prefix läuft bereits)"
        elif busy:
            desc += " · neben laufendem Prefix (runinprefix)"
        return LaunchPlan(argv=argv, env=env, cwd=str(exe_p.parent), tool=tool, prefix=str(prefix_p), runner=runner,
                          description=desc)

    def app_plan(self, app: AppEntry) -> LaunchPlan:
        ps = self.reg.prefix_settings(app.prefix)
        args = shlex.split(app.args) if app.args else []
        extra_env = dict(app.env)
        if self.lift_catalog_xwayland(app, extra_env):
            # Store-Launcher bekommen aus dem Katalog PROTON_ENABLE_WAYLAND=0 (weißes CEF-Fenster). Belegte Ausnahmen gelten
            # auch für bestehende Einträge: dann gilt die normale Wayland-Vorgabe, und Spiele, die der Launcher startet
            # (Kindprozesse, gleicher Wine-Desktop), laufen ebenfalls unter KDE-Wayland.
            extra_env.pop("PROTON_ENABLE_WAYLAND", None)
        return self.build_plan(app.prefix, app.exe, [self.cfg.get("LAUNCH_OPTIONS", ""), ps.launch_options, app.launch_options],
                               extra_env=extra_env, args=args, tool_override=app.tool, wayland_driver=app.wayland_driver)

    def lift_catalog_xwayland(self, app: AppEntry, env: dict[str, str]) -> bool:
        """PROTON_ENABLE_WAYLAND=0 eines Katalog-Launchers weglassen? Ja, wenn der Launcher unter dem Wayland-Treiber belegt
        zeichnet: LauncherSpec.wayland_ok "any" (z. B. GOG Galaxy; ältere Katalogversionen setzten dort noch 0) oder "ge"
        mit GE-Proton ab 11-6. XWayland erzwingt man an der Anwendung mit „Aus (XWayland)“ (wayland_driver "off")."""
        if app.wayland_driver == "off" or not app.launcher or env.get("PROTON_ENABLE_WAYLAND") != "0":
            return False
        spec = launchers.get(app.launcher)
        if spec is None or not spec.wayland_ok:
            return False
        if spec.wayland_ok == "any":
            return True
        return spec.wayland_ok == "ge" and compat.cef_wayland_ok(self.resolve_tool(app.prefix, app.tool))

    def launch(self, plan: LaunchPlan, logname: str) -> subprocess.Popen:
        problem = self.umu_latest_problem(plan.tool)
        if problem:
            raise LaunchError(f"{problem}. Bitte unter „Einstellungen“ bzw. „Prefixe verwalten“ einen anderen Runner "
                              "wählen (z. B. proton-cachyos) oder später erneut starten.")
        Path(plan.prefix).mkdir(parents=True, exist_ok=True)
        system.LOG_DIR.mkdir(parents=True, exist_ok=True)
        logfile = system.LOG_DIR / f"{system.re.sub(r'[^A-Za-z0-9_.-]+', '_', logname)}.log"
        try:
            dpi = self.auto_dpi(plan.prefix)
        except Exception:  # noqa: BLE001 – DPI ist Komfort, darf den Start nie verhindern
            log.exception("Automatische DPI fehlgeschlagen")
            dpi = None
        with open(logfile, "ab") as lf:
            lf.write(f"\n===== Start ({plan.description}) =====\n{plan.shell_preview()}\n".encode())
            if dpi:
                lf.write(f"Windows-DPI des Prefix auf {dpi} gesetzt (passend zur Desktop-Skalierung).\n".encode())
        with open(logfile, "ab") as fh:        # das Kind erbt eine eigene Kopie des Dateideskriptors
            return subprocess.Popen(plan.argv, env=plan.env, cwd=plan.cwd, stdout=fh, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, start_new_session=True)

    # ------------------------------------------------------------------ Im Prefix ausführen
    def prefix_command(self, prefix: str | Path, exe_argv: list[str], tool: CompatTool | None = None,
                       verb: str = "waitforexitandrun") -> tuple[list[str], dict[str, str]]:
        prefix_p = Path(os.path.expanduser(str(prefix)))
        tool = tool or self.resolve_tool(prefix_p)
        if tool is None:
            raise LaunchError("Kein Proton/Wine verfügbar.")
        env = self._base_env(prefix_p)
        _runner, argv = self._runner_env_argv(prefix_p, tool, exe_argv, env, verb=verb)
        return argv, env

    def wine_command(self, prefix: str | Path, wine_argv: list[str], tool: CompatTool | None = None) -> tuple[list[str], dict[str, str]]:
        """Wine des Runners direkt aufrufen (reg, winecfg, wineboot) – für umu/Proton über files/bin/wine."""
        prefix_p = Path(os.path.expanduser(str(prefix)))
        tool = tool or self.resolve_tool(prefix_p)
        wine = compat.wine_binary(tool)
        if not wine:
            raise LaunchError("Kein Wine-Binary gefunden (weder im Runner noch im System).")
        env = self._base_env(prefix_p)
        env["WINEPREFIX"] = str(self.wine_prefix_dir(prefix_p))
        env.setdefault("WINEDEBUG", "-all")
        files = Path(wine).parent.parent
        if files.name in ("files", "dist"):           # Wine aus einem Proton (auch lokal von umu geladenes GE-Proton)
            env["WINESERVER"] = str(files / "bin" / "wineserver")
            env["PATH"] = f"{files / 'bin'}:{env.get('PATH', '')}"
            env["LD_LIBRARY_PATH"] = f"{files / 'lib64'}:{files / 'lib'}:{env.get('LD_LIBRARY_PATH', '')}"
        return [wine, *wine_argv], env

    def winetricks_command(self, prefix: str | Path, verbs: list[str] | None = None, tool: CompatTool | None = None) -> tuple[list[str], dict[str, str]]:
        prefix_p = Path(os.path.expanduser(str(prefix)))
        tool = tool or self.resolve_tool(prefix_p)
        if tool is None:
            raise LaunchError("Kein Proton/Wine verfügbar.")
        verbs = list(verbs or [])
        if self.runner_for(tool) == "umu":
            env = self._base_env(prefix_p)
            env.update({"WINEPREFIX": str(prefix_p), "PROTONPATH": tool.path, "GAMEID": f"umu-{prefix_p.name}", "STORE": "none"})
            return [compat.umu_path() or "umu-run", "winetricks", *(verbs or ["--gui"])], env
        wt = shutil.which("winetricks")
        if not wt:
            raise LaunchError("winetricks ist nicht installiert.")
        argv, env = self.wine_command(prefix_p, [], tool)
        env["WINE"] = argv[0]
        return [wt, *(verbs or ["--gui"])], env

    # ------------------------------------------------------------------ Windows-Programme (Deinstallation)
    def _windows_tool_command(self, prefix: str | Path, wine_argv: list[str], verb: str) -> tuple[list[str], dict[str, str]]:
        """Wine-Hilfsprogramm (uninstaller …) mit demselben Runner wie der Prefix: umu-Prefixe über umu-run, sonst Wine des Runners."""
        prefix_p = Path(os.path.expanduser(str(prefix)))
        tool = self.resolve_tool(prefix_p)
        if tool is not None and self.runner_for(tool) == "umu":
            return self.prefix_command(prefix_p, wine_argv, tool, verb=verb)
        return self.wine_command(prefix_p, wine_argv, tool)

    def installed_programs(self, prefix: str | Path) -> list[tuple[str, str]]:
        """(GUID/Schlüssel, Anzeigename) aller in der Windows-Programmliste des Prefix eingetragenen Programme."""
        argv, env = self._windows_tool_command(prefix, ["uninstaller", "--list"], verb="run")
        r = subprocess.run(argv, env=env, text=True, capture_output=True, timeout=300)
        out: list[tuple[str, str]] = []
        for line in (r.stdout or "").splitlines():
            if "|||" in line:
                key, _, name = line.partition("|||")
                key, name = key.strip(), name.strip()
                if key and name and (key, name) not in out:
                    out.append((key, name))
        if not out and r.returncode != 0:
            tail = " ".join((r.stderr or r.stdout or "").strip().splitlines()[-3:])
            raise LaunchError(f"Windows-Programmliste nicht lesbar (Code {r.returncode}): {tail[:300]}")
        return out

    def uninstall_command(self, prefix: str | Path, key: str) -> tuple[list[str], dict[str, str]]:
        # waitforexitandrun: erst zurückkehren, wenn auch nachgelagerte Deinstallationsprozesse (NSIS) beendet sind
        return self._windows_tool_command(prefix, ["uninstaller", "--remove", key], verb="waitforexitandrun")

    def uninstall_steps(self, prefix: str | Path, key: str) -> list:
        argv, env = self.uninstall_command(prefix, key)
        steps: list = [(argv, env, lambda: True)]
        if argv and Path(argv[0]).name.startswith("wine") and "WINEPREFIX" in env:
            # Wine direkt: auf das Ende aller Prozesse des Prefix warten (NSIS-Deinstaller laufen als Kopie weiter)
            ws = env.get("WINESERVER") or str(Path(argv[0]).parent / "wineserver")
            if not Path(ws).exists():
                ws = shutil.which("wineserver") or "wineserver"
            steps.append(([ws, "-w"], env, lambda: True))
        return steps

    def winetricks_step(self, prefix: str | Path, verbs: list[str], tool: CompatTool | None = None):
        """winetricks als Python-Schritt: Befehl erst zur Laufzeit bauen (Prefix-Layout steht dann fest); Fehler brechen nicht ab."""
        def step(log, ctx) -> int:
            if not verbs:
                return 0
            try:
                argv, env = self.winetricks_command(prefix, ["-q", *verbs], tool)
            except LaunchError as exc:
                log(f"winetricks übersprungen: {exc}")
                return 0
            log("$ " + " ".join(shlex.quote(a) for a in argv))
            rc = system.stream(argv, log, env=env, on_start=ctx.on_start)
            if rc != 0:
                log(f"winetricks endete mit Code {rc} – Installation wird trotzdem fortgesetzt.")
            return 130 if ctx.cancelled() else 0
        return step

    def set_dpi(self, prefix: str | Path, dpi: int) -> subprocess.CompletedProcess:
        """Windows-DPI setzen: Prefix ruht → direkt in user.reg (schnell, kein Runner nötig, keine Prefix-Aktualisierung durch
        eine fremde Wine-Version); läuft dort gerade etwas → reg.exe des Runners über den laufenden wineserver."""
        wdir = self.wine_prefix_dir(prefix)
        if (wdir / "user.reg").is_file() and not system.prefix_wineserver_pids(wdir):
            try:
                system.write_prefix_dpi(wdir, dpi)
            except OSError as exc:
                return subprocess.CompletedProcess(["user.reg"], 1, "", str(exc))
            return subprocess.CompletedProcess(["user.reg"], 0, "", "")
        argv, env = self.wine_command(prefix, ["reg", "add", r"HKCU\Control Panel\Desktop", "/v", "LogPixels", "/t", "REG_DWORD",
                                               "/d", str(int(dpi)), "/f"])
        return subprocess.run(argv, env=env, text=True, capture_output=True, timeout=180)

    def auto_dpi(self, prefix: str | Path, wait: float = 0.0) -> int | None:
        """Windows-DPI eines Prefix an die Desktop-Skalierung anpassen, solange dort keine eigene DPI gesetzt ist.

        Ohne LogPixels rendert Wine mit 96 DPI – auf skalierten Bildschirmen (z. B. 4K mit 225 %) erscheinen Launcher dann
        winzig. wait: so lange auf das Ende eines noch laufenden wineserver warten (nach Prefix-Erstellung).
        Rückgabe: gesetzte DPI oder None."""
        if self.cfg.get("AUTO_DPI", "1") != "1":
            return None
        wdir = self.wine_prefix_dir(prefix)
        if not (wdir / "user.reg").is_file() or system.prefix_dpi(wdir) is not None:
            return None
        dpi = system.wine_dpi_for_desktop()
        if not dpi or dpi <= 96:
            return None
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline and system.prefix_wineserver_pids(wdir):
            time.sleep(0.5)
        r = self.set_dpi(prefix, dpi)
        if r.returncode != 0:
            log.warning("DPI %s für %s nicht gesetzt: %s", dpi, prefix, (r.stderr or r.stdout).strip()[:200])
            return None
        return dpi

    def create_prefix_steps(self, prefix: str | Path, tool: CompatTool | None, arch: str = "win64") -> list[tuple]:
        """Schritte zum Anlegen eines Prefix (für TaskRunner.run_steps): (argv, env, prüfung).

        Die Prüfung meldet, ob der Prefix danach vollständig vorhanden ist; dann gilt ein Rückgabecode ≠ 0 nicht als
        Fehler (Proton/umu melden bei der Ersteinrichtung teils Code 1, obwohl alles angelegt wurde).
        Mono/Gecko-Dialoge werden bei System-Wine unterdrückt.
        """
        prefix_p = Path(os.path.expanduser(str(prefix)))
        tool = tool or self.default_tool()
        if tool is None:
            raise LaunchError("Kein Proton/Wine gefunden. Bitte proton-cachyos, umu-launcher oder wine installieren.")
        runner = self.runner_for(tool)
        ready = lambda: system.is_prefix(prefix_p)  # noqa: E731

        def mkdir_step(log, ctx) -> int:
            # erst beim Ausführen anlegen – schlägt ein früherer Schritt (z. B. Download) fehl, bleibt kein leerer Ordner zurück
            prefix_p.mkdir(parents=True, exist_ok=True)
            return 0

        def dpi_step(log, ctx) -> int:
            if ready():
                dpi = self.auto_dpi(prefix_p, wait=20)
                if dpi:
                    log(f"Windows-DPI auf {dpi} gesetzt (passend zur Desktop-Skalierung) – sonst erscheinen Fenster winzig.")
            return 0
        if runner == "umu":
            # „umu-run ''“ legt den Prefix zwar an, startet danach aber ein leeres Programm und endet mit Code 1
            # („ShellExecuteEx fehlgeschlagen“). wineboot -u erzeugt denselben Prefix und endet sauber.
            env = self._base_env(prefix_p)
            env.update({"WINEPREFIX": str(prefix_p), "PROTONPATH": tool.path, "GAMEID": f"umu-{prefix_p.name}", "STORE": "none",
                        "PROTON_VERB": "run"})
            return [mkdir_step, ([compat.umu_path() or "umu-run", "wineboot", "-u"], env, ready), dpi_step]
        if runner == "proton":
            argv, env = self.prefix_command(prefix_p, ["wineboot", "-u"], tool, verb="run")
            return [mkdir_step, (argv, env, ready), dpi_step]
        wine_argv, env = self.wine_command(prefix_p, ["wineboot", "--init"], tool)
        env["WINEARCH"] = arch if arch in ("win32", "win64") else "win64"
        env["WINEDLLOVERRIDES"] = "mscoree=d;mshtml=d"
        wineserver = str(Path(wine_argv[0]).parent / "wineserver") if (Path(wine_argv[0]).parent / "wineserver").exists() else (shutil.which("wineserver") or "wineserver")
        wineboot_end = [wine_argv[0], "wineboot", "--end-session"]
        return [mkdir_step, (wine_argv, env, ready), (wineboot_end, env, ready), ([wineserver, "-w"], env, ready), dpi_step]
