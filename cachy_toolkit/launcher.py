"""Startpläne für Windows-Programme in Prefixen: umu-launcher, Proton direkt oder Wine – adaptiert aus dem Galaxy-Port."""
from __future__ import annotations

import logging
import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import compat, launch_options, system
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
        p = Path(prefix)
        pfx = p / "pfx"
        return pfx if pfx.is_dir() else p

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
            env["GAMEID"] = game_id or f"umu-{system.re.sub(r'[^a-z0-9]+', '-', prefix.name.lower()).strip('-') or 'prefix'}"
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
    def build_plan(self, prefix: str | Path, exe: str | Path, option_texts: list[str] | None = None,
                   extra_env: dict[str, str] | None = None, args: list[str] | None = None,
                   tool: CompatTool | None = None, tool_override: str = "") -> LaunchPlan:
        prefix_p = Path(os.path.expanduser(str(prefix)))
        exe_p = Path(os.path.expanduser(str(exe)))
        if not exe_p.is_file():
            raise LaunchError(f"Startdatei nicht gefunden: {exe_p}")
        tool = tool or self.resolve_tool(prefix_p, tool_override)
        if tool is None:
            raise LaunchError("Kein Proton/Wine gefunden. Bitte proton-cachyos, umu-launcher oder wine installieren.")
        env = self._base_env(prefix_p, extra_env)
        exe_argv: list[str] = [str(exe_p), *(args or [])]
        if exe_p.suffix.lower() == ".msi":
            exe_argv = ["msiexec", "/i", str(exe_p), *(args or [])]
        runner, base = self._runner_env_argv(prefix_p, tool, exe_argv, env)
        texts = [t for t in (option_texts or []) if t and t.strip()]
        opts_text = launch_options.merge(*texts) if len(texts) > 1 else (texts[0] if texts else "")
        opts = launch_options.parse(opts_text)
        env.update(opts.env)
        argv = [*opts.wrapper, *base, *opts.args]
        return LaunchPlan(argv=argv, env=env, cwd=str(exe_p.parent), tool=tool, prefix=str(prefix_p), runner=runner,
                          description=f"{runner} · {tool.name}")

    def app_plan(self, app: AppEntry) -> LaunchPlan:
        ps = self.reg.prefix_settings(app.prefix)
        args = shlex.split(app.args) if app.args else []
        return self.build_plan(app.prefix, app.exe, [self.cfg.get("LAUNCH_OPTIONS", ""), ps.launch_options, app.launch_options],
                               extra_env=app.env, args=args, tool_override=app.tool)

    def launch(self, plan: LaunchPlan, logname: str) -> subprocess.Popen:
        Path(plan.prefix).mkdir(parents=True, exist_ok=True)
        system.LOG_DIR.mkdir(parents=True, exist_ok=True)
        logfile = system.LOG_DIR / f"{system.re.sub(r'[^A-Za-z0-9_.-]+', '_', logname)}.log"
        with open(logfile, "ab") as lf:
            lf.write(f"\n===== Start ({plan.description}) =====\n{plan.shell_preview()}\n".encode())
        fh = open(logfile, "ab")
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
        if tool and tool.kind == "proton":
            files = Path(tool.path) / ("files" if (Path(tool.path) / "files").is_dir() else "dist")
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

    def set_dpi(self, prefix: str | Path, dpi: int) -> subprocess.CompletedProcess:
        argv, env = self.wine_command(prefix, ["reg", "add", r"HKCU\Control Panel\Desktop", "/v", "LogPixels", "/t", "REG_DWORD",
                                               "/d", str(int(dpi)), "/f"])
        return subprocess.run(argv, env=env, text=True, capture_output=True, timeout=180)

    def create_prefix_steps(self, prefix: str | Path, tool: CompatTool | None, arch: str = "win64") -> list[tuple]:
        """Schritte zum Anlegen eines Prefix (für TaskRunner.run_steps): (argv, env, prüfung).

        Die Prüfung meldet, ob der Prefix danach vollständig vorhanden ist; dann gilt ein Rückgabecode ≠ 0 nicht als
        Fehler (Proton/umu melden bei der Ersteinrichtung teils Code 1, obwohl alles angelegt wurde).
        Mono/Gecko-Dialoge werden bei System-Wine unterdrückt.
        """
        prefix_p = Path(os.path.expanduser(str(prefix)))
        prefix_p.mkdir(parents=True, exist_ok=True)
        tool = tool or self.default_tool()
        if tool is None:
            raise LaunchError("Kein Proton/Wine gefunden. Bitte proton-cachyos, umu-launcher oder wine installieren.")
        runner = self.runner_for(tool)
        ready = lambda: system.is_prefix(prefix_p)  # noqa: E731
        if runner == "umu":
            # „umu-run ''“ legt den Prefix zwar an, startet danach aber ein leeres Programm und endet mit Code 1
            # („ShellExecuteEx fehlgeschlagen“). wineboot -u erzeugt denselben Prefix und endet sauber.
            env = self._base_env(prefix_p)
            env.update({"WINEPREFIX": str(prefix_p), "PROTONPATH": tool.path, "GAMEID": f"umu-{prefix_p.name}", "STORE": "none",
                        "PROTON_VERB": "run"})
            return [([compat.umu_path() or "umu-run", "wineboot", "-u"], env, ready)]
        if runner == "proton":
            argv, env = self.prefix_command(prefix_p, ["wineboot", "-u"], tool, verb="run")
            return [(argv, env, ready)]
        wine_argv, env = self.wine_command(prefix_p, ["wineboot", "--init"], tool)
        env["WINEARCH"] = arch if arch in ("win32", "win64") else "win64"
        env["WINEDLLOVERRIDES"] = "mscoree=d;mshtml=d"
        wineserver = str(Path(wine_argv[0]).parent / "wineserver") if (Path(wine_argv[0]).parent / "wineserver").exists() else (shutil.which("wineserver") or "wineserver")
        wineboot_end = [wine_argv[0], "wineboot", "--end-session"]
        return [(wine_argv, env, ready), (wineboot_end, env, ready), ([wineserver, "-w"], env, ready)]
