"""Tests für Startoptionen, Registry, Startpläne, Verknüpfungen und Prefix-Erkennung."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cachy_toolkit import compat, config, launch_options, launcher, registry, shortcuts, system


class LaunchOptionsTests(unittest.TestCase):
    def test_parse_steam_syntax(self):
        o = launch_options.parse("ENABLE_VKBASALT=1 mangohud gamemoderun %command% -windowed -novid")
        self.assertEqual(o.env, {"ENABLE_VKBASALT": "1"})
        self.assertEqual(o.wrapper, ["mangohud", "gamemoderun"])
        self.assertEqual(o.args, ["-windowed", "-novid"])
        self.assertEqual(o.apply(["wine", "a.exe"]), ["mangohud", "gamemoderun", "wine", "a.exe", "-windowed", "-novid"])
        self.assertEqual(launch_options.parse("-windowed").args, ["-windowed"])
        self.assertEqual(launch_options.parse("").apply(["x"]), ["x"])

    def test_merge_and_env_line(self):
        merged = launch_options.merge("DXVK_HUD=fps %command%", "mangohud %command% -fs")
        o = launch_options.parse(merged)
        self.assertEqual(o.env, {"DXVK_HUD": "fps"})
        self.assertEqual(o.wrapper, ["mangohud"])
        self.assertEqual(o.args, ["-fs"])
        self.assertEqual(launch_options.parse_env_line('A=1 B="zwei drei" kein'), {"A": "1", "B": "zwei drei"})
        self.assertEqual(launch_options.format_env({"B": "x y", "A": "1"}), "A=1 B='x y'")


def _fake_tools():
    proton = compat.CompatTool("proton:/opt/proton-x", "Proton X", "proton", "/opt/proton-x", "9.0", "compatibilitytools.d", "")
    proton_slr = compat.CompatTool("proton:/opt/proton-slr", "Proton SLR", "proton", "/opt/proton-slr", "11", "System", "1628350")
    wine = compat.CompatTool("wine:/usr/bin/wine", "System-Wine", "wine", "/usr/bin/wine", "11", "System")
    return [proton_slr, proton, compat.UMU_GE, wine]


class LauncherPlanTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        base = Path(self.td.name)
        self.cfg = config.Config(base / "config.env")
        self.cfg.set("WINE_PREFIX_ROOT", str(base / "prefixes"), save=False)
        self.cfg.set("LAUNCH_OPTIONS", "", save=False)
        self.reg = registry.Registry(base / "registry.json")
        self.L = launcher.Launcher(self.cfg, self.reg)
        self.L._tools = _fake_tools()
        self.prefix = base / "prefixes" / "gothic"
        (self.prefix / "drive_c" / "Game").mkdir(parents=True)
        self.exe = self.prefix / "drive_c" / "Game" / "Gothic.exe"
        self.exe.write_bytes(b"MZ")

    def tearDown(self):
        self.td.cleanup()

    def test_wine_plan_with_options(self):
        self.reg.set_prefix_settings(self.prefix, tool="wine:/usr/bin/wine", launch_options="mangohud %command%", env={"DXVK_HUD": "fps"})
        plan = self.L.build_plan(self.prefix, self.exe, [self.cfg.get("LAUNCH_OPTIONS"), self.reg.prefix_settings(self.prefix).launch_options, "WINEDEBUG=-all %command% -win"])
        self.assertEqual(plan.runner, "wine")
        self.assertEqual(plan.argv, ["mangohud", "/usr/bin/wine", str(self.exe), "-win"])
        self.assertEqual(plan.env["WINEPREFIX"], str(self.prefix))
        self.assertEqual(plan.env["DXVK_HUD"], "fps")
        self.assertEqual(plan.env["WINEDEBUG"], "-all")
        self.assertIn("WINEPREFIX=", plan.shell_preview())

    def test_umu_plan_for_slr_proton(self):
        with mock.patch.object(compat, "umu_available", return_value=True), mock.patch.object(compat, "umu_path", return_value="/usr/bin/umu-run"), \
             mock.patch.object(compat, "find_runtime", return_value=None):
            plan = self.L.build_plan(self.prefix, self.exe, [], tool_override="proton:/opt/proton-slr")
        self.assertEqual(plan.runner, "umu")
        self.assertEqual(plan.argv[:2], ["/usr/bin/umu-run", str(self.exe)])
        self.assertEqual(plan.env["PROTONPATH"], "/opt/proton-slr")
        self.assertEqual(plan.env["WINEPREFIX"], str(self.prefix))
        self.assertTrue(plan.env["GAMEID"].startswith("umu-"))

    def test_proton_direct_plan_without_umu(self):
        self.cfg.set("PREFER_UMU", "0", save=False)
        with mock.patch.object(compat, "umu_available", return_value=False), mock.patch.object(compat, "steam_root", return_value=None):
            plan = self.L.build_plan(self.prefix, self.exe, [], tool_override="proton:/opt/proton-x")
        self.assertEqual(plan.runner, "proton")
        self.assertEqual(plan.argv[:3], ["/opt/proton-x/proton", "waitforexitandrun", str(self.exe)])
        self.assertEqual(plan.env["STEAM_COMPAT_DATA_PATH"], str(self.prefix))
        self.assertTrue(plan.env["STEAM_COMPAT_CLIENT_INSTALL_PATH"].endswith(".steam-client-stub"))

    def test_msi_uses_msiexec(self):
        msi = self.prefix / "drive_c" / "setup.msi"
        msi.write_bytes(b"x")
        plan = self.L.build_plan(self.prefix, msi, [], tool_override="wine:/usr/bin/wine")
        self.assertEqual(plan.argv, ["/usr/bin/wine", "msiexec", "/i", str(msi)])

    def test_create_prefix_steps_wine_suppresses_mono_dialogs(self):
        with mock.patch.object(compat, "wine_binary", return_value="/usr/bin/wine"):
            steps = self.L.create_prefix_steps(self.prefix.parent / "neu", compat.resolve("wine:/usr/bin/wine", self.L.tools()), "win32")
        argv, env, _check = steps[0]
        self.assertEqual(argv, ["/usr/bin/wine", "wineboot", "--init"])
        self.assertEqual(env["WINEARCH"], "win32")
        self.assertEqual(env["WINEDLLOVERRIDES"], "mscoree=d;mshtml=d")
        self.assertEqual(steps[1][0][1:], ["wineboot", "--end-session"])
        self.assertTrue(steps[2][0][0].endswith("wineserver"))

    def test_app_plan_merges_global_prefix_and_app_options(self):
        self.cfg.set("LAUNCH_OPTIONS", "gamemoderun %command%", save=False)
        self.reg.set_prefix_settings(self.prefix, tool="wine:/usr/bin/wine", launch_options="mangohud %command%")
        app = registry.AppEntry(id="gothic", name="Gothic", exe=str(self.exe), prefix=str(self.prefix), launch_options="%command% -zwindow", args="-game:x")
        self.reg.add_app(app)
        plan = self.L.app_plan(self.reg.get_app("gothic"))
        self.assertEqual(plan.argv, ["gamemoderun", "mangohud", "/usr/bin/wine", str(self.exe), "-game:x", "-zwindow"])


class RegistryTests(unittest.TestCase):
    def test_roundtrip_and_move(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "registry.json"
            r = registry.Registry(path)
            pfx = Path(td) / "ext" / "ubi"
            (pfx / "drive_c").mkdir(parents=True)
            r.register_prefix(pfx, tool="umu:GE-Proton", launch_options="mangohud %command%")
            r.add_location(pfx.parent)
            app = r.add_app(registry.AppEntry(id=r.new_app_id("Ubisoft Connect"), name="Ubisoft Connect", exe=str(pfx / "drive_c" / "u.exe"), prefix=str(pfx), menu=True))
            self.assertEqual(app.id, "ubisoft-connect")
            self.assertEqual(r.new_app_id("Ubisoft Connect"), "ubisoft-connect-2")
            r2 = registry.Registry(path)
            self.assertEqual(r2.prefix_settings(pfx).tool, "umu:GE-Proton")
            self.assertEqual([p.name for p in r2.registered_prefix_paths()], ["ubi"])
            self.assertEqual(r2.locations, [str(pfx.parent)])
            new = Path(td) / "ext" / "ubisoft"
            pfx.rename(new)
            r2.move_prefix(pfx, new)
            self.assertEqual(r2.get_app("ubisoft-connect").prefix, str(new))
            self.assertEqual(r2.get_app("ubisoft-connect").exe, str(new / "drive_c" / "u.exe"))
            removed = r2.forget_prefix(new)
            self.assertEqual([a.id for a in removed], ["ubisoft-connect"])
            self.assertEqual(r2.apps, {})


class PrefixDetectionTests(unittest.TestCase):
    def test_wine_and_proton_layouts_and_external(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "root"
            (root / "a" / "drive_c").mkdir(parents=True)
            (root / "b" / "pfx" / "drive_c").mkdir(parents=True)
            (root / "b" / "pfx" / "system.reg").write_text("#arch=win64\n")
            ext = Path(td) / "elsewhere" / "c"
            (ext / "drive_c").mkdir(parents=True)
            found = system.list_prefixes(root, [ext, root / "a"])
            self.assertEqual([(p.name, p.layout, p.external) for p in found], [("a", "wine", False), ("b", "proton", False), ("c", "wine", True)])
            self.assertEqual(found[1].wine_dir, root / "b" / "pfx")
            self.assertEqual(found[1].arch, "win64")


class LocationScanTests(unittest.TestCase):
    def test_prefixes_in_additional_locations_are_listed(self):
        """Fall vom Notebook: Standardordner leer, Prefix liegt in einem weiteren Speicherort."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "Wine_Prefix"
            root.mkdir()
            loc = Path(td) / "Ubisoft" / "01.Prefixes"
            (loc / "ubi-connect" / "drive_c").mkdir(parents=True)
            os.symlink(".", loc / "ubi-connect" / "pfx")      # so legt umu Prefixe an
            (loc / "kein-prefix").mkdir()
            found = system.list_prefixes(root, [loc / "ubi-connect"], [loc, root])
            self.assertEqual([(p.name, p.layout, p.external) for p in found], [("ubi-connect", "wine", True)])
            self.assertEqual(system.list_prefixes(root), [])


class StepSequenceTests(unittest.TestCase):
    def test_nonzero_rc_tolerated_when_check_passes(self):
        lines: list[str] = []
        rc = system.run_step_sequence([(["sh", "-c", "echo eins; exit 1"], None, lambda: True), ["true"]], lines.append)
        self.assertEqual(rc, 0)
        self.assertTrue(any("ignoriert" in l for l in lines))
        self.assertIn("eins", lines)

    def test_nonzero_rc_aborts_without_check(self):
        lines: list[str] = []
        rc = system.run_step_sequence([(["sh", "-c", "exit 3"], None, lambda: False), ["sh", "-c", "echo nie"]], lines.append)
        self.assertEqual(rc, 3)
        self.assertNotIn("nie", lines)

    def test_umu_creation_uses_wineboot_and_check(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = config.Config(Path(td) / "c.env")
            reg = registry.Registry(Path(td) / "r.json")
            L = launcher.Launcher(cfg, reg)
            L._tools = _fake_tools()
            target = Path(td) / "pfx" / "ubi-connect"
            with mock.patch.object(compat, "umu_available", return_value=True), mock.patch.object(compat, "umu_path", return_value="/usr/bin/umu-run"):
                steps = L.create_prefix_steps(target, compat.UMU_GE)
            self.assertEqual(len(steps), 1)
            argv, env, check = steps[0]
            self.assertEqual(argv, ["/usr/bin/umu-run", "wineboot", "-u"])
            self.assertEqual(env["WINEPREFIX"], str(target))
            self.assertFalse(check())
            (target / "drive_c").mkdir(parents=True)
            self.assertTrue(check())


class ShortcutTests(unittest.TestCase):
    def test_desktop_entry_and_files(self):
        with tempfile.TemporaryDirectory() as td:
            apps = Path(td) / "apps"
            desk = Path(td) / "Schreibtisch"
            desk.mkdir()
            app = registry.AppEntry(id="ubi", name="Ubisoft Connect", exe="/p/drive_c/u.exe", prefix="/p", icon="")
            with mock.patch.object(shortcuts, "APPS_DIR", apps), mock.patch.object(shortcuts, "desktop_dir", return_value=desk), \
                 mock.patch.object(shortcuts, "launcher_command", return_value="/usr/bin/cachy-toolkit"), \
                 mock.patch.object(shortcuts, "_refresh_menu", lambda: None):
                content = shortcuts.desktop_entry(app)
                self.assertIn("Exec=/usr/bin/cachy-toolkit --run ubi", content)
                self.assertIn("Name=Ubisoft Connect", content)
                written = shortcuts.create(app, menu=True, desktop=True)
                self.assertEqual(len(written), 2)
                self.assertTrue((apps / "cachy-app-ubi.desktop").exists())
                self.assertTrue(os.access(desk / "cachy-app-ubi.desktop", os.X_OK))
                shortcuts.create(app, menu=True, desktop=False)
                self.assertFalse((desk / "cachy-app-ubi.desktop").exists())
                removed = shortcuts.remove("ubi")
                self.assertEqual(len(removed), 1)

    def test_extract_icon_from_wine_notepad_if_available(self):
        exe = Path("/usr/lib/wine/x86_64-windows/notepad.exe")
        if not exe.exists():
            self.skipTest("Wine-notepad nicht vorhanden")
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(shortcuts, "ICON_DIR", Path(td)):
                out = shortcuts.extract_icon(exe, "notepad-test")
            if out is None:
                self.skipTest("pefile/Pillow fehlen oder keine Icon-Ressource")
            self.assertTrue(Path(out).exists())
            from PIL import Image
            with Image.open(out) as im:
                self.assertGreaterEqual(min(im.size), 16)


if __name__ == "__main__":
    unittest.main()
