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
        self.assertTrue(callable(steps[0]))                      # Ordner erst beim Ausführen anlegen
        self.assertFalse((self.prefix.parent / "neu").exists())
        steps = steps[1:]
        argv, env, _check = steps[0]
        self.assertEqual(argv, ["/usr/bin/wine", "wineboot", "--init"])
        self.assertEqual(env["WINEARCH"], "win32")
        self.assertEqual(env["WINEDLLOVERRIDES"], "mscoree=d;mshtml=d")
        self.assertEqual(steps[1][0][1:], ["wineboot", "--end-session"])
        self.assertTrue(steps[2][0][0].endswith("wineserver"))

    def _umu_plan(self, **kw):
        with mock.patch.object(compat, "umu_available", return_value=True), mock.patch.object(compat, "umu_path", return_value="/usr/bin/umu-run"), \
             mock.patch.object(compat, "find_runtime", return_value=None):
            return self.L.build_plan(self.prefix, self.exe, kw.pop("options", []), tool_override="proton:/opt/proton-slr", **kw)

    @mock.patch.dict(os.environ, {"PROTON_ENABLE_WAYLAND": ""})
    def test_wayland_default_and_override(self):
        del os.environ["PROTON_ENABLE_WAYLAND"]
        self.assertEqual(self._umu_plan().env.get("PROTON_ENABLE_WAYLAND"), "1")                  # Vorgabe: KDE-Wayland
        self.reg.set_prefix_settings(self.prefix, wayland_driver="off")
        self.assertEqual(self._umu_plan().env.get("PROTON_ENABLE_WAYLAND"), "0")                  # Prefix: XWayland
        self.assertEqual(self._umu_plan(wayland_driver="on").env.get("PROTON_ENABLE_WAYLAND"), "1")  # Anwendung schlägt Prefix
        self.reg.set_prefix_settings(self.prefix, wayland_driver="")
        self.assertEqual(self._umu_plan(wayland_driver="off").env.get("PROTON_ENABLE_WAYLAND"), "0")
        self.cfg.set("WAYLAND_DRIVER", "0", save=False)
        self.assertNotIn("PROTON_ENABLE_WAYLAND", self._umu_plan().env)                           # global aus: Proton-Standard
        self.assertEqual(self._umu_plan(wayland_driver="on").env.get("PROTON_ENABLE_WAYLAND"), "1")
        self.cfg.set("WAYLAND_DRIVER", "1", save=False)
        # eigene Variable gewinnt immer: Anwendungs-/Prefix-Umgebung und Startoptionen
        self.assertEqual(self._umu_plan(wayland_driver="on", extra_env={"PROTON_ENABLE_WAYLAND": "0"}).env["PROTON_ENABLE_WAYLAND"], "0")
        self.reg.set_prefix_settings(self.prefix, env={"PROTON_ENABLE_WAYLAND": "0"})
        self.assertEqual(self._umu_plan().env["PROTON_ENABLE_WAYLAND"], "0")
        self.reg.set_prefix_settings(self.prefix, env={})
        self.assertEqual(self._umu_plan(options=["PROTON_ENABLE_WAYLAND=0 %command%"]).env["PROTON_ENABLE_WAYLAND"], "0")
        # reines Wine: kein Proton-Schalter
        plan = self.L.build_plan(self.prefix, self.exe, [], tool_override="wine:/usr/bin/wine")
        self.assertNotIn("PROTON_ENABLE_WAYLAND", plan.env)

    @mock.patch.dict(os.environ, {"PROTON_ENABLE_WAYLAND": ""})
    def test_running_prefix_uses_runinprefix_and_wayland_desktop(self):
        """Läuft der Prefix (z. B. ein Launcher unter XWayland), startet ein Wayland-Programm per runinprefix auf dem
        eigenen Wine-Desktop „root“; ein XWayland-Programm per runinprefix direkt (kein Warten bis zum Launcher-Ende)."""
        del os.environ["PROTON_ENABLE_WAYLAND"]
        # Prefix ruht: unverändert waitforexitandrun mit der Exe selbst
        with mock.patch.object(system, "prefix_wineserver_pids", return_value=[]):
            plan = self._umu_plan(options=["%command% -windowed"])
        self.assertEqual(plan.env["PROTON_VERB"], "waitforexitandrun")
        self.assertEqual(plan.argv, ["/usr/bin/umu-run", str(self.exe), "-windowed"])
        # Prefix läuft + Wayland: eigener Desktop, Arbeitsordner und Exe als Windows-Pfade, Argumente ans Spiel
        with mock.patch.object(system, "prefix_wineserver_pids", return_value=[4711]):
            plan = self._umu_plan(options=["%command% -windowed"])
        self.assertEqual(plan.env["PROTON_VERB"], "runinprefix")
        self.assertEqual(plan.env["PROTON_ENABLE_WAYLAND"], "1")
        explorer = str(self.prefix / "drive_c" / "windows" / "explorer.exe")
        self.assertEqual(plan.argv, ["/usr/bin/umu-run", explorer, "/desktop=root,1920x1080,wayland",
                                     r"C:\windows\system32\start.exe", "/d", r"C:\Game", r"C:\Game\Gothic.exe",
                                     "-windowed"])
        self.assertNotIn("", plan.argv)                       # kein leerer Titel für start.exe
        self.assertIn("eigenem Desktop", plan.description)
        # Prefix läuft + XWayland: runinprefix, Exe direkt
        with mock.patch.object(system, "prefix_wineserver_pids", return_value=[4711]):
            plan = self._umu_plan(wayland_driver="off")
        self.assertEqual(plan.env["PROTON_VERB"], "runinprefix")
        self.assertEqual(plan.argv, ["/usr/bin/umu-run", str(self.exe)])
        # Startoptionen schalten Wayland ab -> kein eigener Desktop
        with mock.patch.object(system, "prefix_wineserver_pids", return_value=[4711]):
            plan = self._umu_plan(options=["PROTON_ENABLE_WAYLAND=0 %command%"])
        self.assertEqual(plan.argv, ["/usr/bin/umu-run", str(self.exe)])
        # Proton direkt (ohne umu) nutzt dasselbe Verb
        self.cfg.set("PREFER_UMU", "0", save=False)
        with mock.patch.object(compat, "umu_available", return_value=False), \
                mock.patch.object(compat, "steam_root", return_value=None), \
                mock.patch.object(system, "prefix_wineserver_pids", return_value=[4711]):
            plan = self.L.build_plan(self.prefix, self.exe, [], tool_override="proton:/opt/proton-x")
        self.assertEqual(plan.argv[:4], ["/opt/proton-x/proton", "runinprefix", explorer, "/desktop=root,1920x1080,wayland"])

    def test_windows_path(self):
        self.assertEqual(self.L.windows_path(self.prefix, self.exe), r"C:\Game\Gothic.exe")
        self.assertEqual(self.L.windows_path(self.prefix, "/mnt/Spiele/Mein Spiel/x.exe"), r"Z:\mnt\Spiele\Mein Spiel\x.exe")

    @mock.patch.dict(os.environ, {"PROTON_ENABLE_WAYLAND": ""})
    def test_app_wayland_setting_and_catalog_exception(self):
        del os.environ["PROTON_ENABLE_WAYLAND"]
        from cachy_toolkit import launchers
        self.reg.add_app(registry.AppEntry(id="spiel", name="Spiel", exe=str(self.exe), prefix=str(self.prefix),
                                           tool="proton:/opt/proton-slr", wayland_driver="off"))
        rs = launchers.get("rockstar")
        self.reg.add_app(registry.AppEntry(id="rs", name="Rockstar", exe=str(self.exe), prefix=str(self.prefix),
                                           tool="proton:/opt/proton-slr", env=rs.env_dict, launcher="rockstar"))
        with mock.patch.object(compat, "umu_available", return_value=True), mock.patch.object(compat, "umu_path", return_value="/usr/bin/umu-run"), \
             mock.patch.object(compat, "find_runtime", return_value=None):
            self.assertEqual(self.L.app_plan(self.reg.get_app("spiel")).env["PROTON_ENABLE_WAYLAND"], "0")
            self.reg.update_app("spiel", wayland_driver="")
            self.assertEqual(self.L.app_plan(self.reg.get_app("spiel")).env["PROTON_ENABLE_WAYLAND"], "1")
            self.assertEqual(self.L.app_plan(self.reg.get_app("rs")).env["PROTON_ENABLE_WAYLAND"], "0")   # CEF-Launcher: XWayland
        reloaded = registry.Registry(self.reg.path)
        self.assertEqual(reloaded.get_app("spiel").wayland_driver, "")
        self.assertEqual(registry.Registry(self.reg.path).prefix_settings(self.prefix).wayland_driver, "")

    def test_cef_wayland_ok(self):
        ge = compat.CompatTool("proton:/x/GE-Proton11-9-x86_64", "GE-Proton11-9-x86_64", "proton", "/x/GE-Proton11-9-x86_64", "GE-Proton11-9", "compatibilitytools.d", "")
        old = compat.CompatTool("proton:/x/GE-Proton11-5", "GE-Proton11-5", "proton", "/x/GE-Proton11-5", "", "compatibilitytools.d", "")
        ten = compat.CompatTool("proton:/x/GE-Proton10-34", "GE-Proton10-34", "proton", "/x/GE-Proton10-34", "GE-Proton10-34", "compatibilitytools.d", "")
        cachy = compat.CompatTool("proton:/x/proton-cachyos-slr", "proton-cachyos-11.0", "proton", "/x/proton-cachyos-slr", "cachyos-11.0-20261005-slr", "System", "")
        self.assertTrue(compat.cef_wayland_ok(ge))
        self.assertTrue(compat.cef_wayland_ok(compat.UMU_GE))
        for t in (old, ten, cachy, compat.UMU_UMU, None):
            self.assertFalse(compat.cef_wayland_ok(t))

    @mock.patch.dict(os.environ, {"PROTON_ENABLE_WAYLAND": ""})
    def test_catalog_launcher_on_wayland_with_ge_proton(self):
        del os.environ["PROTON_ENABLE_WAYLAND"]
        from cachy_toolkit import launchers
        ge = compat.CompatTool("proton:/opt/GE-Proton11-9", "GE-Proton11-9", "proton", "/opt/GE-Proton11-9", "GE-Proton11-9", "compatibilitytools.d", "")
        self.L._tools = _fake_tools() + [ge]
        for spec_id in ("rockstar", "ea-app"):
            spec = launchers.get(spec_id)
            self.reg.add_app(registry.AppEntry(id=spec_id, name=spec.name, exe=str(self.exe), prefix=str(self.prefix),
                                               tool=ge.id, env=spec.env_dict, launcher=spec_id))
        # eigene Abschaltung ohne Katalog-Launcher bleibt unangetastet
        self.reg.add_app(registry.AppEntry(id="own", name="Eigenes", exe=str(self.exe), prefix=str(self.prefix),
                                           tool=ge.id, env={"PROTON_ENABLE_WAYLAND": "0"}))
        wl = lambda app_id: self.L.app_plan(self.reg.get_app(app_id)).env["PROTON_ENABLE_WAYLAND"]
        with mock.patch.object(compat, "umu_available", return_value=True), mock.patch.object(compat, "umu_path", return_value="/usr/bin/umu-run"), \
             mock.patch.object(compat, "find_runtime", return_value=None), \
             mock.patch.object(system, "prefix_wineserver_pids", return_value=[]):
            self.assertEqual(wl("rockstar"), "1")                                   # belegt: Wayland mit GE-Proton 11-9
            self.assertEqual(self.reg.get_app("rockstar").env["PROTON_ENABLE_WAYLAND"], "0")   # Eintrag selbst unverändert
            self.assertEqual(wl("ea-app"), "0")                                     # ungeprüft: bleibt XWayland
            self.assertEqual(wl("own"), "0")
            self.reg.update_app("rockstar", wayland_driver="off")
            self.assertEqual(wl("rockstar"), "0")
            self.reg.update_app("rockstar", wayland_driver="", tool="umu:GE-Proton")
            self.assertEqual(wl("rockstar"), "1")
            for tool in ("umu:UMU-Proton", "proton:/opt/proton-slr"):
                self.reg.update_app("rockstar", tool=tool)
                self.assertEqual(wl("rockstar"), "0")
            self.assertEqual(launchers.get("ubisoft-connect").wayland_ok, "ge")
            # GOG Galaxy zeichnet mit jedem getesteten Proton: neue Einträge ohne 0, alte Einträge (Katalog setzte 0) heben ab
            gog = launchers.get("gog-galaxy")
            self.assertNotIn("PROTON_ENABLE_WAYLAND", gog.env_dict)
            self.reg.add_app(registry.AppEntry(id="gog", name=gog.name, exe=str(self.exe), prefix=str(self.prefix), tool="proton:/opt/proton-slr",
                                               env={"PROTON_ENABLE_WAYLAND": "0", "WINEDLLOVERRIDES": "libglesv2=d"}, launcher="gog-galaxy"))
            self.assertEqual(wl("gog"), "1")
            self.assertIn("libglesv2=d", self.L.app_plan(self.reg.get_app("gog")).env["WINEDLLOVERRIDES"])
            self.reg.update_app("gog", wayland_driver="off")
            self.assertEqual(wl("gog"), "0")

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
            self.assertEqual(launcher.Launcher.wine_prefix_dir(loc / "ubi-connect"), loc / "ubi-connect")
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
            self.assertEqual(len(steps), 3)
            self.assertTrue(callable(steps[0]))
            self.assertTrue(callable(steps[2]))       # Windows-DPI an die Desktop-Skalierung anpassen
            argv, env, check = steps[1]
            self.assertEqual(argv, ["/usr/bin/umu-run", "wineboot", "-u"])
            self.assertEqual(env["WINEPREFIX"], str(target))
            self.assertFalse(check())
            (target / "drive_c").mkdir(parents=True)
            self.assertTrue(check())


USER_REG = """WINE REGISTRY Version 2
;; All keys relative to REGISTRY\\\\User\\\\S-1-5-21-0-0-0-1000

#arch=win64

[Control Panel\\\\Desktop] 1792223717
#time=1dd5e0cd99de928
"ActiveWndTrackTimeout"=dword:00000000

[Software\\\\Wine\\\\Fonts] 1792223715
#time=1dd5e0cd99de928
"LogPixels"=dword:00000060
"""


class AutoDpiTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        base = Path(self.td.name)
        self.cfg = config.Config(base / "config.env")
        self.reg = registry.Registry(base / "registry.json")
        self.L = launcher.Launcher(self.cfg, self.reg)
        self.L._tools = _fake_tools()
        self.prefix = base / "rockstar"
        (self.prefix / "drive_c").mkdir(parents=True)
        (self.prefix / "user.reg").write_text(USER_REG)
        p = mock.patch.object(system, "prefix_wineserver_pids", return_value=[])
        p.start()
        self.addCleanup(p.stop)

    def tearDown(self):
        self.td.cleanup()

    def test_sets_desktop_dpi_when_unset(self):
        self.assertIsNone(system.prefix_dpi(self.prefix))      # Fonts-Wert (96) zählt nicht als gesetzt
        with mock.patch.object(system, "wine_dpi_for_desktop", return_value=216):
            self.assertEqual(self.L.auto_dpi(self.prefix), 216)
            self.assertEqual(system.prefix_dpi(self.prefix), 216)
            self.assertIsNone(self.L.auto_dpi(self.prefix))    # nur einmal – danach gilt der Wert des Prefix

    def test_keeps_own_dpi_and_respects_switch(self):
        system.write_prefix_dpi(self.prefix, 120)
        with mock.patch.object(system, "wine_dpi_for_desktop", return_value=216):
            self.assertIsNone(self.L.auto_dpi(self.prefix))
            self.assertEqual(system.prefix_dpi(self.prefix), 120)
        (self.prefix / "user.reg").write_text(USER_REG)
        self.cfg.set("AUTO_DPI", "0", save=False)
        with mock.patch.object(system, "wine_dpi_for_desktop", return_value=216):
            self.assertIsNone(self.L.auto_dpi(self.prefix))
        self.assertIsNone(system.prefix_dpi(self.prefix))

    def test_no_change_at_100_percent(self):
        with mock.patch.object(system, "wine_dpi_for_desktop", return_value=96):
            self.assertIsNone(self.L.auto_dpi(self.prefix))
        self.assertIsNone(system.prefix_dpi(self.prefix))

    def test_launch_applies_dpi_before_start(self):
        plan = launcher.LaunchPlan(argv=["true"], env=dict(os.environ), cwd=str(self.prefix), tool=None,
                                   prefix=str(self.prefix), runner="wine", description="test")
        logs = Path(self.td.name) / "logs"
        with mock.patch.object(system, "wine_dpi_for_desktop", return_value=216), mock.patch.object(system, "LOG_DIR", logs):
            self.L.launch(plan, "app-rockstar").wait(10)
        self.assertEqual(system.prefix_dpi(self.prefix), 216)
        self.assertIn("Windows-DPI des Prefix auf 216", (logs / "app-rockstar.log").read_text())


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
