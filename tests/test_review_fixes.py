"""Tests zu den Korrekturen aus dem Review (Namensprüfung, Löschschutz, Prefix-Prozesse, Deinstallation, winetricks, Kandidaten)."""
from __future__ import annotations

import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from cachyos_dailytool import compat, config, launcher, registry, shortcuts, system


def mkprefix(p: Path) -> Path:
    (p / "drive_c").mkdir(parents=True)
    return p


class NameAndDeleteTests(unittest.TestCase):
    def test_prefix_name_error(self):
        for bad in ("", "  ", ".", "..", ".versteckt"):
            self.assertIsNotNone(system.prefix_name_error(bad), bad)
        self.assertIsNone(system.prefix_name_error("ubisoft-connect"))

    def test_safe_to_delete_prefix(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "root"
            loc = Path(td) / "loc"
            pfx = mkprefix(loc / "ubi")
            self.assertIsNone(system.safe_to_delete_prefix(pfx, [root, loc]))
            self.assertIn("kein Wine-Prefix", system.safe_to_delete_prefix(loc, [root]))
            mkprefix(loc / "inner")
            # ein Speicherort, der selbst wie ein Prefix aussieht, darf nicht gelöscht werden
            weird = mkprefix(Path(td) / "weird")
            self.assertIn("Speicherort", system.safe_to_delete_prefix(weird, [weird]))
            self.assertIn("Speicherort", system.safe_to_delete_prefix(weird, [weird / "sub"]))


class PrefixProcessTests(unittest.TestCase):
    def test_detect_and_kill_processes_of_prefix(self):
        with tempfile.TemporaryDirectory() as td:
            pfx = mkprefix(Path(td) / "p")
            other = mkprefix(Path(td) / "q")
            p1 = subprocess.Popen(["sleep", "60"], env={**os.environ, "WINEPREFIX": str(pfx)})
            p2 = subprocess.Popen(["sleep", "60"], env={**os.environ, "STEAM_COMPAT_DATA_PATH": str(pfx) + "/"})
            p3 = subprocess.Popen(["sleep", "60"], env={**os.environ, "WINEPREFIX": str(other)})
            try:
                time.sleep(0.2)
                pids = system.prefix_pids(pfx)
                self.assertIn(p1.pid, pids)
                self.assertIn(p2.pid, pids)
                self.assertNotIn(p3.pid, pids)
                n = system.kill_prefix_processes(pfx, timeout=3)
                self.assertGreaterEqual(n, 2)
                self.assertIsNotNone(p1.wait(timeout=5))
                self.assertIsNotNone(p2.wait(timeout=5))
                self.assertIsNone(p3.poll())
            finally:
                for p in (p1, p2, p3):
                    if p.poll() is None:
                        p.kill()
                        p.wait()


def make_launcher(td: Path, tool_id: str):
    cfg = config.Config(td / "c.env")
    reg = registry.Registry(td / "r.json")
    L = launcher.Launcher(cfg, reg)
    L._tools = [compat.CompatTool("wine:/usr/bin/wine", "System-Wine", "wine", "/usr/bin/wine", "11", "System"), compat.UMU_GE]
    return L, reg


class UninstallAndWinetricksTests(unittest.TestCase):
    def test_uninstall_steps_wine_waits_for_wineserver(self):
        with tempfile.TemporaryDirectory() as td:
            L, reg = make_launcher(Path(td), "wine")
            pfx = mkprefix(Path(td) / "p")
            reg.set_prefix_settings(pfx, tool="wine:/usr/bin/wine")
            with mock.patch.object(compat, "wine_binary", return_value="/usr/bin/wine"):
                steps = L.uninstall_steps(pfx, "{GUID}")
            self.assertEqual(steps[0][0], ["/usr/bin/wine", "uninstaller", "--remove", "{GUID}"])
            self.assertTrue(steps[1][0][0].endswith("wineserver") and steps[1][0][1] == "-w")

    def test_uninstall_command_umu_uses_umu_run(self):
        with tempfile.TemporaryDirectory() as td:
            L, reg = make_launcher(Path(td), "umu")
            pfx = mkprefix(Path(td) / "p")
            reg.set_prefix_settings(pfx, tool="umu:GE-Proton")
            with mock.patch.object(compat, "umu_available", return_value=True), mock.patch.object(compat, "umu_path", return_value="/usr/bin/umu-run"):
                argv, env = L.uninstall_command(pfx, "{GUID}")
            self.assertEqual(argv, ["/usr/bin/umu-run", "uninstaller", "--remove", "{GUID}"])
            self.assertEqual(env["PROTON_VERB"], "waitforexitandrun")
            self.assertEqual(env["PROTONPATH"], "GE-Proton")

    def test_installed_programs_error_is_reported(self):
        with tempfile.TemporaryDirectory() as td:
            L, reg = make_launcher(Path(td), "wine")
            pfx = mkprefix(Path(td) / "p")
            reg.set_prefix_settings(pfx, tool="wine:/usr/bin/wine")
            with mock.patch.object(compat, "wine_binary", return_value="/bin/false"):
                with self.assertRaises(launcher.LaunchError):
                    L.installed_programs(pfx)
            with mock.patch.object(compat, "wine_binary", return_value="/bin/echo"):
                # /bin/echo gibt die Argumente aus → keine Einträge, aber Code 0 → leere Liste, kein Fehler
                self.assertEqual(L.installed_programs(pfx), [])

    def test_winetricks_step_tolerates_failure(self):
        with tempfile.TemporaryDirectory() as td:
            L, reg = make_launcher(Path(td), "wine")
            pfx = mkprefix(Path(td) / "p")
            lines: list[str] = []
            step = L.winetricks_step(pfx, ["corefonts"], compat.resolve("wine:/usr/bin/wine", L.tools()))
            with mock.patch.object(L, "winetricks_command", side_effect=launcher.LaunchError("winetricks ist nicht installiert.")):
                self.assertEqual(step(lines.append, system.StepContext(cancelled=lambda: False)), 0)
            self.assertTrue(any("übersprungen" in l for l in lines))
            with mock.patch.object(L, "winetricks_command", return_value=(["sh", "-c", "exit 4"], dict(os.environ))):
                self.assertEqual(step(lines.append, system.StepContext(cancelled=lambda: False)), 0)
            self.assertTrue(any("Code 4" in l for l in lines))


class CandidateAndRegisterTests(unittest.TestCase):
    def test_split_candidates_excludes_uninstallers(self):
        from cachyos_dailytool.ui.pages_wine import split_candidates
        before = {"/p/drive_c/old.exe"}
        after = before | {"/p/drive_c/App/uninst.exe", "/p/drive_c/App/MyApp.exe", "/p/drive_c/App/Updater.exe"}
        good, rest = split_candidates(before, after, "My App")
        self.assertEqual(good, ["/p/drive_c/App/MyApp.exe"])
        self.assertEqual(sorted(rest), ["/p/drive_c/App/Updater.exe", "/p/drive_c/App/uninst.exe"])

    def test_register_app_keep_existing(self):
        from cachyos_dailytool.ui.pages_wine import register_app
        with tempfile.TemporaryDirectory() as td:
            reg = registry.Registry(Path(td) / "r.json")
            win = SimpleNamespace(registry=reg)
            pfx = mkprefix(Path(td) / "p")
            exe = str(pfx / "drive_c" / "u.exe")
            with mock.patch.object(shortcuts, "create", return_value=[]), mock.patch.object(shortcuts, "extract_icon", return_value=None):
                a = register_app(win, name="Ubisoft Connect", exe=exe, prefix=pfx, launcher_id="ubisoft-connect",
                                 launch_options_text="mangohud %command%", tool="umu:GE-Proton", menu=True, desktop=False)
                b = register_app(win, name="Ubisoft Connect", exe=exe, prefix=pfx, launcher_id="ubisoft-connect",
                                 launch_options_text="", tool="", menu=True, desktop=True, keep_existing=True)
            self.assertEqual(a.id, b.id)
            self.assertEqual(len(reg.apps), 1)
            self.assertEqual(b.launch_options, "mangohud %command%")
            self.assertEqual(b.tool, "umu:GE-Proton")
            self.assertTrue(b.desktop)


if __name__ == "__main__":
    unittest.main()


class UmuLatestPreflightTests(unittest.TestCase):
    def _launcher(self, td: Path):
        cfg = config.Config(td / "c.env")
        reg = registry.Registry(td / "r.json")
        L = launcher.Launcher(cfg, reg)
        slr = compat.CompatTool("proton:/usr/share/steam/compatibilitytools.d/proton-cachyos-slr", "proton-cachyos", "proton",
                                "/usr/share/steam/compatibilitytools.d/proton-cachyos-slr", "11", "System", "")
        L._tools = [slr, compat.UMU_GE, compat.CompatTool("wine:/usr/bin/wine", "System-Wine", "wine", "/usr/bin/wine", "11", "System")]
        return L, slr

    def test_fallback_when_rate_limited_and_not_installed(self):
        with tempfile.TemporaryDirectory() as td:
            L, slr = self._launcher(Path(td))
            holder = {"tool": compat.UMU_GE, "tool_id": compat.UMU_GE.id}
            lines: list[str] = []
            with mock.patch.object(compat, "local_umu_tool", return_value=None), \
                 mock.patch.object(compat, "github_rate_limit", return_value=(0, 2000000000)):
                rc = L.ensure_runner_step(holder)(lines.append, system.StepContext(cancelled=lambda: False))
            self.assertEqual(rc, 0)
            self.assertEqual(holder["tool"], slr)
            self.assertEqual(holder["tool_id"], slr.id)
            self.assertIn("Abfragelimit", holder["fallback"])
            self.assertTrue(any("Weiche aus" in l for l in lines))

    def test_offline_and_local_copy(self):
        with tempfile.TemporaryDirectory() as td:
            L, slr = self._launcher(Path(td))
            # lokale Kopie vorhanden → kein Ausweichen, keine Netzabfrage
            holder = {"tool": compat.UMU_GE, "tool_id": compat.UMU_GE.id}
            with mock.patch.object(compat, "local_umu_tool", return_value=Path("/x/GE-Proton10-34")), \
                 mock.patch.object(compat, "github_rate_limit", side_effect=AssertionError("keine Abfrage erwartet")):
                self.assertEqual(L.ensure_runner_step(holder)(lambda l: None, system.StepContext(cancelled=lambda: False)), 0)
            self.assertIs(holder["tool"], compat.UMU_GE)
            # offline, nichts lokal → Ausweichen mit passender Begründung
            holder = {"tool": compat.UMU_GE, "tool_id": compat.UMU_GE.id}
            with mock.patch.object(compat, "local_umu_tool", return_value=None), mock.patch.object(compat, "github_rate_limit", return_value=None):
                L.ensure_runner_step(holder)(lambda l: None, system.StepContext(cancelled=lambda: False))
            self.assertIn("nicht erreichbar", holder["fallback"])
            # Limit frei → umu darf laden, kein Ausweichen
            holder = {"tool": compat.UMU_GE, "tool_id": compat.UMU_GE.id}
            lines: list[str] = []
            with mock.patch.object(compat, "local_umu_tool", return_value=None), mock.patch.object(compat, "github_rate_limit", return_value=(57, 0)):
                L.ensure_runner_step(holder)(lines.append, system.StepContext(cancelled=lambda: False))
            self.assertIs(holder["tool"], compat.UMU_GE)
            self.assertTrue(any("einmalig herunter" in l for l in lines))

    def test_launch_refuses_unavailable_umu_latest(self):
        with tempfile.TemporaryDirectory() as td:
            L, _slr = self._launcher(Path(td))
            plan = launcher.LaunchPlan(argv=["true"], env={}, cwd=td, tool=compat.UMU_GE, prefix=td, runner="umu", description="umu")
            with mock.patch.object(compat, "local_umu_tool", return_value=None), \
                 mock.patch.object(compat, "github_rate_limit", return_value=(0, 2000000000)):
                with self.assertRaises(launcher.LaunchError) as cm:
                    L.launch(plan, "test")
            self.assertIn("anderen Runner", str(cm.exception))

    def test_local_umu_tool_and_rate_limit_parsing(self):
        import io as _io
        import json as _json
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / "compat"
            for n in ("GE-Proton9-20", "GE-Proton10-4", "GE-Proton10-34", "UMU-Proton-9.0-4e"):
                (base / n).mkdir(parents=True)
                (base / n / "proton").write_text("")
            with mock.patch.object(compat, "UMU_STEAM_COMPAT", base), mock.patch.object(compat, "UMU_OWN_COMPAT", Path(td) / "nix"):
                self.assertEqual(compat.local_umu_tool("GE-Proton").name, "GE-Proton10-34")
                self.assertEqual(compat.local_umu_tool("UMU-Proton").name, "UMU-Proton-9.0-4e")

        class R(_io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        body = _json.dumps({"resources": {"core": {"limit": 60, "remaining": 0, "reset": 1792174416}}}).encode()
        self.assertEqual(compat.github_rate_limit(opener=lambda req, timeout=0: R(body)), (0, 1792174416))

        def boom(req, timeout=0):
            raise OSError("offline")
        self.assertIsNone(compat.github_rate_limit(opener=boom))
