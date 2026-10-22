"""Tests der X3D-Kernzuteilung: Topologie, Katalog, Festlegungen, Erkennung (nachgebautes /proc), Affinität, Dienst-Logik."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from cachyos_dailytool import config, x3d


def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def fake_sysfs(root: Path, ccds: list[tuple[list[int], str]]) -> Path:
    """sysfs-Nachbau: je CCD (CPU-Liste, L3-Größe); SMT-Geschwister = cpu und cpu+16."""
    cpu = root / "cpu"
    for cid, (cpus, size) in enumerate(ccds):
        for c in cpus:
            d = cpu / f"cpu{c}"
            _write(d / "cache/index3/level", "3\n")
            _write(d / "cache/index3/id", f"{cid}\n")
            _write(d / "cache/index3/size", f"{size}\n")
            _write(d / "cache/index3/shared_cpu_list", x3d.format_cpu_list(cpus) + "\n")
            _write(d / "cache/index2/level", "2\n")
            sib = sorted({c, c + 16 if c < 16 else c - 16} & set(cpus))
            _write(d / "topology/thread_siblings_list", x3d.format_cpu_list(sib) + "\n")
    _write(root / "cpuinfo", "processor\t: 0\nmodel name\t: AMD Ryzen 9 7950X3D 16-Core Processor\n")
    return cpu


class CpuListTests(unittest.TestCase):
    def test_roundtrip(self):
        self.assertEqual(x3d.parse_cpu_list("0-7,16-23"), list(range(0, 8)) + list(range(16, 24)))
        self.assertEqual(x3d.format_cpu_list([0, 1, 2, 7, 16, 17]), "0-2,7,16-17")
        self.assertEqual(x3d.format_cpu_list([]), "")
        self.assertEqual(x3d.parse_cpu_list(""), [])
        self.assertEqual(x3d._parse_size("98304K"), 98304 * 1024)
        self.assertEqual(x3d._parse_size("32M"), 32 * 1024 ** 2)


class TopologyTests(unittest.TestCase):
    def test_dual_ccd_x3d(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cpu = fake_sysfs(root, [(list(range(0, 8)) + list(range(16, 24)), "98304K"),
                                    (list(range(8, 16)) + list(range(24, 32)), "32768K")])
            topo = x3d.read_topology(cpu, root / "cpuinfo")
        self.assertTrue(topo.dual)
        self.assertEqual(topo.cache_ccd.id, 0)
        self.assertEqual(topo.cache_ccd.cpu_list, "0-7,16-23")
        self.assertEqual(topo.freq_ccd.cpu_list, "8-15,24-31")
        self.assertEqual(topo.cache_ccd.cores, 8)
        self.assertEqual(topo.cpus_for("cache"), set(range(0, 8)) | set(range(16, 24)))
        self.assertEqual(topo.other_cpus("cache"), topo.cpus_for("frequency"))
        self.assertIsNone(topo.cpus_for("none"))
        self.assertIn("96 MB L3", topo.summary())
        self.assertEqual(topo.cpu_model, "AMD Ryzen 9 7950X3D 16-Core Processor")
        self.assertEqual(topo.to_dict()["cache"], 0)

    def test_single_ccd_is_not_dual(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cpu = fake_sysfs(root, [(list(range(0, 16)), "98304K")])
            topo = x3d.read_topology(cpu, root / "cpuinfo")
        self.assertFalse(topo.dual)
        self.assertIsNone(topo.cache_ccd)
        self.assertIsNone(topo.cpus_for("cache"))

    def test_equal_l3_is_not_x3d(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cpu = fake_sysfs(root, [(list(range(0, 8)), "32768K"), (list(range(8, 16)), "32768K")])
            topo = x3d.read_topology(cpu, root / "cpuinfo")
        self.assertFalse(topo.dual)


class CatalogRulesTests(unittest.TestCase):
    def test_catalog_loads_and_indexes(self):
        cat = x3d.Catalog()
        self.assertGreater(len(cat.entries), 50)
        self.assertEqual(cat.get("exe:services.exe").kind, "system")
        self.assertEqual(cat.get("exe:epicgameslauncher.exe").kind, "launcher")
        self.assertEqual(cat.get("comm:steam").kind, "background")
        self.assertEqual(cat.get("exe:eldenring.exe").kind, "game")
        self.assertEqual(cat.get("steam:1245620").name, "Elden Ring")
        self.assertIsNone(cat.get("exe:unbekannt.exe"))
        kinds = {e.kind for e in cat.entries}
        self.assertTrue(kinds <= set(x3d.KINDS))

    def test_user_rules_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "x3d.json"
            r = x3d.UserRules(p)
            r.set_override("exe:game.exe", "frequency", name="Mein Spiel", source="wine")
            r.set_ignore("exe:tool.exe", True, name="Werkzeug")
            r2 = x3d.UserRules(p)
            self.assertEqual(r2.overrides["exe:game.exe"]["mode"], "frequency")
            self.assertEqual(r2.label("exe:game.exe"), "Mein Spiel")
            self.assertIn("exe:tool.exe", r2.ignore)
            self.assertEqual(r2.label("exe:tool.exe"), "Werkzeug")
            r2.set_override("exe:tool.exe", "cache")          # hebt den Ausschluss auf
            self.assertNotIn("exe:tool.exe", r2.ignore)
            r2.clear_override("exe:game.exe")
            self.assertNotIn("exe:game.exe", x3d.UserRules(p).overrides)
            with self.assertRaises(ValueError):
                r2.set_override("exe:x.exe", "turbo")

    def test_settings_from_config(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = config.Config(Path(td) / "config.env")
            s = x3d.Settings.from_config(cfg)
            self.assertTrue(s.enabled and s.background and s.enforce)
            self.assertEqual((s.default_mode, s.idle_mode), ("cache", "frequency"))
            cfg.set("X3D_DEFAULT_MODE", "unsinn", save=False)
            cfg.set("X3D_IDLE_MODE", "keep", save=False)
            cfg.set("X3D_GAME_DIRS", "/mnt/spiele:~/Spiele", save=False)
            s = x3d.Settings.from_config(cfg)
            self.assertEqual(s.default_mode, "cache")
            self.assertEqual(s.idle_mode, "keep")
            self.assertEqual(s.game_dirs[0], "/mnt/spiele")
            self.assertTrue(s.game_dirs[1].endswith("/Spiele"))

    def test_pretty_name(self):
        self.assertEqual(x3d.pretty_name("FactoryGame-Win64-Shipping.exe"), "Factory Game")
        self.assertEqual(x3d.pretty_name("surviving_mars.exe"), "surviving mars")
        self.assertEqual(x3d.pretty_name("Cyberpunk2077.exe"), "Cyberpunk 2077")
        self.assertEqual(x3d.pretty_name("eldenring.exe"), "eldenring")


class FakeProc:
    """Nachgebautes /proc: pid → (ppid, comm, exe, cmdline, env, gpu)."""

    def __init__(self) -> None:
        self.table: dict[int, dict] = {}

    def add(self, pid: int, ppid: int, comm: str, exe: str, cmdline: list[str] | None = None,
            env: dict[str, str] | None = None, gpu: bool = False) -> int:
        self.table[pid] = {"ppid": ppid, "comm": comm, "exe": exe, "cmdline": cmdline or [exe], "env": env or {}, "gpu": gpu}
        return pid

    def detector(self, topo: x3d.Topology, settings: x3d.Settings, rules: x3d.UserRules, apps=None,
                 game_dirs: list[str] | None = None) -> x3d.Detector:
        det = x3d.Detector(topo, settings, x3d.Catalog(), rules, apps or [], uid=1000,
                           gpu_check=lambda pid: self.table.get(pid, {}).get("gpu", False),
                           env_reader=lambda pid: dict(self.table.get(pid, {}).get("env", {})),
                           steam_names={"1245620": "ELDEN RING", "4242": "Testspiel"})
        det.game_dirs = game_dirs if game_dirs is not None else ["/home/u/Games", "/home/u/.local/share/Steam/steamapps/common"]

        def scan() -> dict[int, x3d.Proc]:
            procs = {}
            for pid, t in self.table.items():
                p = x3d.Proc(pid=pid, ppid=t["ppid"], comm=t["comm"], start=pid * 10, exe=t["exe"], cmdline=t["cmdline"])
                base = Path(p.exe).name
                if base.startswith("wine") and p.cmdline:
                    first = x3d._win_basename(p.cmdline[0])
                    if first.lower().endswith(".exe"):
                        p.win_exe = first
                procs[pid] = p
            det.procs = procs
            return procs
        det.scan = scan  # type: ignore[method-assign]
        return det


def _topo() -> x3d.Topology:
    return x3d.Topology(cpu_model="Test X3D", ccds=[
        x3d.Ccd(0, list(range(0, 8)) + list(range(16, 24)), 96 * 1024 ** 2, 8),
        x3d.Ccd(1, list(range(8, 16)) + list(range(24, 32)), 32 * 1024 ** 2, 8)])


class DetectionTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.rules = x3d.UserRules(Path(self.td.name) / "x3d.json")
        self.settings = x3d.Settings()
        self.fp = FakeProc()
        self.fp.add(1, 0, "systemd", "/usr/lib/systemd/systemd")
        self.fp.add(100, 1, "plasmashell", "/usr/bin/plasmashell", gpu=True)
        self.fp.add(200, 1, "steam", "/home/u/.local/share/Steam/ubuntu12_32/steam", gpu=True)
        self.fp.add(201, 200, "steamwebhelper", "/home/u/.local/share/Steam/ubuntu12_64/steamwebhelper", gpu=True)

    def tearDown(self):
        self.td.cleanup()

    def _detect(self, apps=None):
        det = self.fp.detector(_topo(), self.settings, self.rules, apps)
        return det, *det.detect()

    def test_proton_game_tree_is_one_session_on_cache_ccd(self):
        env = {"SteamAppId": "1245620", "STEAM_COMPAT_DATA_PATH": "/home/u/.local/share/Steam/steamapps/compatdata/1245620",
               "WINEPREFIX": "/home/u/.local/share/Steam/steamapps/compatdata/1245620/pfx"}
        self.fp.add(300, 200, "reaper", "/home/u/.local/share/Steam/ubuntu12_32/reaper", env=env)
        self.fp.add(301, 300, "pressure-vessel-adverb", "/home/u/.local/share/Steam/steamapps/common/SteamLinuxRuntime_sniper/pressure-vessel/bin/pressure-vessel-adverb", env=env)
        self.fp.add(302, 301, "wineserver", "/home/u/.local/share/Steam/steamapps/common/Proton 9.0/files/bin/wineserver", env=env)
        self.fp.add(303, 301, "services.exe", "/home/u/.local/share/Steam/steamapps/common/Proton 9.0/files/bin/wine64-preloader",
                    ["C:\\windows\\system32\\services.exe"], env=env, gpu=False)
        self.fp.add(304, 301, "eldenring.exe", "/home/u/.local/share/Steam/steamapps/common/Proton 9.0/files/bin/wine64-preloader",
                    ["Z:\\home\\u\\.local\\share\\Steam\\steamapps\\common\\ELDEN RING\\Game\\eldenring.exe"], env=env, gpu=True)
        self.fp.add(305, 304, "explorer.exe", "/home/u/.local/share/Steam/steamapps/common/Proton 9.0/files/bin/wine64-preloader",
                    ["C:\\windows\\system32\\explorer.exe", "/desktop=steam"], env=env, gpu=True)
        det, sessions, matches = self._detect()
        self.assertEqual(list(matches), [304])
        self.assertEqual(len(sessions), 1)
        s = sessions[0]
        self.assertEqual(s.root, 300, "Wurzel ist der reaper (gleiche SteamAppId), nicht der Steam-Client")
        self.assertEqual(s.name, "Elden Ring")
        self.assertEqual(s.source, "steam")
        self.assertEqual(s.mode, "cache")
        self.assertEqual(s.pids, {300, 301, 302, 303, 304, 305})
        self.assertEqual(s.reason, "Katalog")
        bg = det.background_pids(sessions)
        self.assertEqual(bg, {200, 201}, "Steam-Client samt Helfer gilt als Hintergrund, nicht aber der Spielbaum")

    def test_unknown_wine_program_needs_gpu(self):
        env = {"WINEPREFIX": "/home/u/Games/wine-prefixes/test"}
        self.fp.add(400, 1, "wine64-preloader", "/usr/bin/wine64-preloader", ["Z:\\home\\u\\spiel\\MyGame-Win64-Shipping.exe"], env=env, gpu=False)
        det, sessions, matches = self._detect()
        self.assertEqual(sessions, [])
        self.fp.table[400]["gpu"] = True
        det, sessions, matches = self._detect()
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].name, "My Game")
        self.assertEqual(sessions[0].source, "wine")
        self.assertEqual(sessions[0].key, "exe:mygame-win64-shipping.exe")
        self.assertEqual(sessions[0].priority, 0)

    def test_launchers_installers_and_wrappers_are_never_games(self):
        env = {"WINEPREFIX": "/mnt/games/epic"}
        self.fp.add(500, 1, "EpicGamesLauncher", "/opt/proton/bin/wine64-preloader", ["C:\\Program Files (x86)\\Epic Games\\Launcher\\Portal\\Binaries\\Win64\\EpicGamesLauncher.exe"], env=env, gpu=True)
        self.fp.add(501, 1, "setup.exe", "/opt/proton/bin/wine64-preloader", ["Z:\\tmp\\GameSetup.exe"], env=env, gpu=True)
        self.fp.add(502, 1, "vcredist_x64.ex", "/opt/proton/bin/wine64-preloader", ["Z:\\tmp\\vcredist_x64.exe"], env=env, gpu=True)
        self.fp.add(503, 1, "gamescope", "/usr/bin/gamescope", env={"SteamAppId": "4242"}, gpu=True)
        self.fp.add(504, 1, "python3", "/usr/bin/python3.13", ["python3", "/usr/bin/umu-run", "x.exe"], env={"GAMEID": "umu-x"}, gpu=False)
        self.fp.add(505, 1, "firefox", "/usr/lib/firefox/firefox", gpu=True)
        det, sessions, matches = self._detect()
        self.assertEqual(sessions, [])

    def test_native_game_from_games_dir_and_steam_marker(self):
        self.fp.add(600, 1, "factorio", "/home/u/Games/factorio/bin/x64/factorio", gpu=True)
        self.fp.add(601, 1, "mygame.x86_64", "/mnt/other/mygame/mygame.x86_64", gpu=True)                       # kein Spieleordner, keine Markierung
        self.fp.add(602, 200, "reaper", "/home/u/.local/share/Steam/ubuntu12_32/reaper", env={"SteamAppId": "4242"})
        self.fp.add(603, 602, "testgame", "/mnt/other/steamlib/steamapps/common/Test/testgame", env={"SteamAppId": "4242"}, gpu=True)
        det, sessions, matches = self._detect()
        names = {s.name: s for s in sessions}
        self.assertIn("Factorio", names)
        self.assertEqual(names["Factorio"].reason, "Katalog")
        self.assertIn("Testspiel", names)
        self.assertEqual(names["Testspiel"].root, 602)
        self.assertEqual(names["Testspiel"].source, "steam")
        self.assertNotIn("mygame", {s.name.lower() for s in sessions})

    def test_override_registry_and_ignore_precedence(self):
        env = {"WINEPREFIX": "/mnt/games/pfx"}
        self.fp.add(700, 1, "game.exe", "/opt/proton/bin/wine64-preloader", ["Z:\\mnt\\games\\pfx\\drive_c\\G\\Game.exe"], env=env, gpu=True)
        apps = [x3d.RegistryApp("Mein Spiel", "/mnt/games/pfx/drive_c/G/Game.exe", "/mnt/games/pfx", ccd="")]
        det, sessions, _ = self._detect(apps)
        self.assertEqual(sessions[0].name, "Mein Spiel")
        self.assertEqual(sessions[0].mode, "cache")
        self.assertEqual(sessions[0].priority, 2)
        apps[0].ccd = "frequency"
        det, sessions, _ = self._detect(apps)
        self.assertEqual(sessions[0].mode, "frequency")
        self.rules.set_override("exe:game.exe", "none", name="Game (frei)")
        det, sessions, _ = self._detect(apps)
        self.assertEqual((sessions[0].mode, sessions[0].priority, sessions[0].name), ("none", 3, "Game (frei)"))
        self.rules.set_ignore("exe:game.exe", True)
        det, sessions, _ = self._detect(apps)
        self.assertEqual(sessions, [])

    def test_catalog_system_entry_beats_steam_override(self):
        env = {"SteamAppId": "1245620"}
        self.rules.set_override("steam:1245620", "frequency")
        self.fp.add(800, 1, "services.exe", "/opt/proton/bin/wine64-preloader", ["C:\\windows\\system32\\services.exe"], env=env, gpu=True)
        self.fp.add(801, 1, "eldenring.exe", "/opt/proton/bin/wine64-preloader", ["Z:\\g\\eldenring.exe"], env=env, gpu=True)
        det, sessions, matches = self._detect()
        self.assertEqual(list(matches), [801])
        self.assertEqual(sessions[0].mode, "frequency")
        self.assertEqual(sessions[0].reason, "Eigene Festlegung")

    def test_nested_sessions_merge_and_launcher_child_games(self):
        env = {"WINEPREFIX": "/mnt/games/rockstar/pfx"}
        self.fp.add(900, 1, "Launcher.exe", "/opt/proton/bin/wine64-preloader", ["C:\\Program Files\\Rockstar Games\\Launcher\\Launcher.exe"], env=env, gpu=True)
        self.fp.add(901, 900, "GTA5.exe", "/opt/proton/bin/wine64-preloader", ["Z:\\mnt\\games\\GTAV\\GTA5.exe"], env=env, gpu=True)
        det, sessions, matches = self._detect()
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].name, "Grand Theft Auto V")
        self.assertEqual(sessions[0].root, 900, "Launcher im selben Prefix gehört zum Spielbaum")
        self.assertEqual(sessions[0].pids, {900, 901})
        self.assertEqual(det.background_pids(sessions), {200, 201})


class AffinityTests(unittest.TestCase):
    def test_pin_and_release_real_process(self):
        ncpu = os.cpu_count() or 1
        if ncpu < 2:
            self.skipTest("braucht mindestens zwei CPUs")
        allowed = sorted(os.sched_getaffinity(0))
        if len(allowed) < 2:
            self.skipTest("Affinität dieser Umgebung lässt nur eine CPU zu")
        target = set(allowed[:1])
        proc = subprocess.Popen(["sleep", "30"])
        try:
            pinned: dict[int, frozenset[int]] = {}
            n = x3d.set_affinity([proc.pid], target, pinned=pinned, enforce=True)
            self.assertEqual(n, 1)
            self.assertEqual(set(os.sched_getaffinity(proc.pid)), target)
            # zweiter Durchlauf: nichts zu tun
            self.assertEqual(x3d.set_affinity([proc.pid], target, pinned=pinned, enforce=True), 0)
            # Teilmenge innerhalb des Ziels bleibt unangetastet (eigene Thread-Affinität des Spiels)
            self.assertEqual(x3d.set_affinity([proc.pid], set(allowed), pinned=pinned, enforce=True), 0)
            self.assertEqual(set(os.sched_getaffinity(proc.pid)), target)
            # außerhalb des Ziels → wird zurückgeholt
            os.sched_setaffinity(proc.pid, set(allowed))
            self.assertEqual(x3d.set_affinity([proc.pid], target, pinned=pinned, enforce=True), 1)
            self.assertEqual(set(os.sched_getaffinity(proc.pid)), target)
            # Freigabe nur, wenn die Affinität noch unsere ist
            pinned = {}
            x3d.set_affinity([proc.pid], target, pinned=pinned, enforce=True)
            freed = x3d.release_affinity([proc.pid], set(allowed), pinned=pinned)
            self.assertEqual(freed, 1)
            self.assertEqual(set(os.sched_getaffinity(proc.pid)), set(allowed))
            self.assertEqual(pinned, {})
            # Probelauf ändert nichts
            x3d.set_affinity([proc.pid], target, pinned=pinned, enforce=True, dry_run=True)
            self.assertEqual(set(os.sched_getaffinity(proc.pid)), set(allowed))
        finally:
            proc.kill()
            proc.wait()

    def test_gpu_fd_detection_on_self(self):
        # Der Testprozess selbst hat kein Render-Gerät offen
        self.assertFalse(x3d.process_uses_gpu(os.getpid()))
        self.assertFalse(x3d.process_uses_gpu(99999999))
        self.assertGreaterEqual(len(x3d.list_threads(os.getpid())), 1)
        p = x3d.read_proc(os.getpid())
        self.assertEqual(p.pid, os.getpid())
        self.assertEqual(p.ppid, os.getppid())
        self.assertTrue(p.cmdline)
        self.assertEqual(p.win_exe, "")
        env = x3d.read_env(os.getpid())
        self.assertIn("PATH", env)


class ControllerTests(unittest.TestCase):
    """Dienst-Logik mit nachgebauten Prozessen, Rangfolge-Datei und Zustand im Temp-Ordner."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        base = Path(self.td.name)
        self.cfg = config.Config(base / "config.env")
        self.cfg.save()
        self.rules = x3d.UserRules(base / "x3d.json")
        self.fp = FakeProc()
        self.fp.add(1, 0, "systemd", "/usr/lib/systemd/systemd")
        self.fp.add(200, 1, "steam", "/home/u/.local/share/Steam/ubuntu12_32/steam", gpu=True)
        self.mode = {"value": "frequency", "writes": []}
        self.topo = _topo()
        self.state = base / "state.json"
        self.affinity_calls: list = []

    def tearDown(self):
        self.td.cleanup()

    def _controller(self, writable: bool = True, dry_run: bool = False) -> x3d.Controller:
        det = self.fp.detector(self.topo, x3d.Settings.from_config(self.cfg), self.rules)

        def writer(m: str) -> str | None:
            self.mode["writes"].append(m)
            self.mode["value"] = m
            return None
        ctl = x3d.Controller(self.cfg, dry_run=dry_run, topo=self.topo, rules=self.rules, apps=[], detector=det,
                             state_file=self.state, mode_reader=lambda: self.mode["value"], mode_writer=writer,
                             mode_writable_fn=lambda: writable)
        # Affinität nicht wirklich setzen (fremde PIDs existieren nicht) – nur Aufrufe mitschreiben
        orig = x3d.set_affinity

        def fake_set(pids, cpus, **kw):
            self.affinity_calls.append((set(pids), frozenset(cpus)))
            return 0
        x3d.set_affinity = fake_set
        self.addCleanup(lambda: setattr(x3d, "set_affinity", orig))
        return ctl

    def test_mode_follows_game_and_returns_to_idle(self):
        ctl = self._controller()
        ctl.tick()
        self.assertEqual(self.mode["writes"], [], "ohne Spiel bleibt die Vorgabe „frequency“ unangetastet")
        st = json.loads(self.state.read_text())
        self.assertTrue(st["running"])
        self.assertEqual(st["sessions"], [])
        env = {"SteamAppId": "1245620"}
        self.fp.add(300, 200, "reaper", "/home/u/.local/share/Steam/ubuntu12_32/reaper", env=env)
        self.fp.add(301, 300, "eldenring.exe", "/opt/p/bin/wine64-preloader", ["Z:\\g\\eldenring.exe"], env=env, gpu=True)
        ctl.tick()
        self.assertEqual(self.mode["writes"], ["cache"])
        self.assertEqual(len(ctl.sessions), 1)
        game_calls = [c for c in self.affinity_calls if 301 in c[0]]
        self.assertTrue(game_calls and game_calls[-1][1] == frozenset(self.topo.cpus_for("cache")))
        bg_calls = [c for c in self.affinity_calls if c[0] == {200}]
        self.assertTrue(bg_calls and bg_calls[-1][1] == frozenset(self.topo.cpus_for("frequency")),
                        "Steam wandert auf den Frequenz-CCD")
        st = json.loads(self.state.read_text())
        self.assertEqual(st["sessions"][0]["name"], "Elden Ring")
        self.assertEqual(st["desired_mode"], "cache")
        self.assertTrue(any("Spiel erkannt" in e[1] for e in st["events"]))
        del self.fp.table[300], self.fp.table[301]
        ctl.tick()
        self.assertEqual(self.mode["writes"], ["cache", "frequency"], "nach dem Spiel zurück zur Ruhe-Vorgabe")
        self.assertTrue(any("Spiel beendet" in e[1] for e in ctl.events))

    def test_idle_keep_and_unwritable_mode(self):
        self.cfg.set("X3D_IDLE_MODE", "keep")
        ctl = self._controller(writable=False)
        env = {"SteamAppId": "1245620"}
        self.fp.add(301, 1, "eldenring.exe", "/opt/p/bin/wine64-preloader", ["Z:\\g\\eldenring.exe"], env=env, gpu=True)
        ctl.tick()
        self.assertEqual(self.mode["writes"], [])
        self.assertTrue(any("kein Schreibrecht" in e[1] for e in ctl.events))
        del self.fp.table[301]
        ctl.tick()
        self.assertIsNone(ctl.desired_mode, "„keep“ stellt im Leerlauf nichts um")

    def test_disabled_releases_and_frequency_game(self):
        self.cfg.set("X3D_DEFAULT_MODE", "frequency")
        self.cfg.set("X3D_BACKGROUND", "0")
        ctl = self._controller()
        self.fp.add(301, 1, "wine64-preloader", "/opt/p/bin/wine64-preloader", ["Z:\\g\\Unknown.exe"], env={"WINEPREFIX": "/p"}, gpu=True)
        ctl.tick()
        self.assertEqual(self.mode["writes"], ["frequency"] if self.mode["value"] != "frequency" else [])
        self.assertEqual(ctl.sessions[0].mode, "frequency")
        self.assertFalse([c for c in self.affinity_calls if c[0] == {200}], "Hintergrund abgeschaltet")
        self.cfg.set("X3D_ENABLED", "0")
        time.sleep(0.01)
        ctl.tick()
        self.assertEqual(ctl.sessions, [])
        st = json.loads(self.state.read_text())
        self.assertFalse(st["enabled"])

    def test_dry_run_writes_nothing(self):
        ctl = self._controller(dry_run=True)
        self.fp.add(301, 1, "eldenring.exe", "/opt/p/bin/wine64-preloader", ["Z:\\g\\eldenring.exe"], env={"SteamAppId": "1245620"}, gpu=True)
        ctl.tick()
        self.assertEqual(self.mode["writes"], [])
        self.assertTrue(any("Probelauf" in e[1] for e in ctl.events))
        self.assertTrue(json.loads(self.state.read_text())["dry_run"])

    def test_state_helpers(self):
        ctl = self._controller()
        ctl.tick()
        self.assertTrue(x3d.daemon_alive(self.state))
        st = x3d.read_state(self.state)
        st["ts"] -= 100
        self.state.write_text(json.dumps(st))
        self.assertFalse(x3d.daemon_alive(self.state))
        ctl.shutdown()
        self.assertFalse(x3d.read_state(self.state)["running"])


class MeasurementTests(unittest.TestCase):
    def test_cpu_usage_math_and_ccd_usage(self):
        prev = {0: (100, 1000), 1: (50, 1000), 2: (0, 1000)}
        cur = {0: (300, 1200), 1: (50, 1200), 2: (250, 1200), 3: (10, 10)}
        pct = x3d.cpu_usage(prev, cur)
        self.assertEqual(pct[0], 100.0)
        self.assertEqual(pct[1], 0.0)
        self.assertEqual(pct[2], 100.0)
        self.assertEqual(pct[3], 0.0, "ohne Vorwert keine Auslastung")
        topo = _topo()
        usage = {c: (50.0 if c in topo.cache_ccd.cpus else 10.0) for c in topo.all_cpus}
        self.assertEqual(x3d.ccd_usage(topo, usage), {0: 50.0, 1: 10.0})
        self.assertEqual(topo.ccd_of(16), 0)
        self.assertEqual(topo.ccd_of(31), 1)
        self.assertEqual(topo.ccd_of(99), -1)
        self.assertEqual(topo.ccd_ids_for("cache"), {0})
        self.assertEqual(topo.ccd_ids_for("frequency"), {1})
        self.assertEqual(topo.ccd_ids_for("none"), {0, 1})

    def test_cpu_times_parses_proc_stat(self):
        with tempfile.TemporaryDirectory() as td:
            Path(td, "stat").write_text("cpu  10 0 10 80 0 0 0 0 0 0\ncpu0 5 0 5 40 2 0 0 0 0 0\ncpu1 5 0 5 40 0 0 0 0 0 0\nintr 1 2 3\n")
            t = x3d.cpu_times(td)
        self.assertEqual(t, {0: (10, 52), 1: (10, 50)})
        real = x3d.cpu_times()
        self.assertIn(0, real)
        self.assertGreater(real[0][1], 0)

    def test_thread_stats_on_self(self):
        rows = x3d.thread_stats(os.getpid())
        self.assertTrue(rows)
        tid, psr, ticks = rows[0]
        self.assertGreater(tid, 0)
        self.assertGreaterEqual(psr, 0)
        self.assertGreaterEqual(ticks, 0)

    def test_session_placement_with_real_busy_process(self):
        """Echter Prozess (kopiertes yes) wird gebunden; nach kurzer Laufzeit liegt seine CPU-Zeit im Ziel-CCD."""
        allowed = sorted(os.sched_getaffinity(0))
        if len(allowed) < 2:
            self.skipTest("braucht mindestens zwei CPUs")
        half = len(allowed) // 2
        topo = x3d.Topology(cpu_model="Test", ccds=[x3d.Ccd(0, allowed[:half], 96 * 1024 ** 2, half),
                                                     x3d.Ccd(1, allowed[half:], 32 * 1024 ** 2, len(allowed) - half)])
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            exe = base / "games" / "Busy" / "busygame"
            exe.parent.mkdir(parents=True)
            import shutil
            shutil.copy(shutil.which("yes") or "/usr/bin/yes", exe)
            exe.chmod(0o755)
            cfg = config.Config(base / "config.env")
            cfg.set("X3D_GAME_DIRS", str(base / "games"))
            rules = x3d.UserRules(base / "x3d.json")
            proc = subprocess.Popen([str(exe)], stdout=subprocess.DEVNULL, env={**os.environ, "SteamAppId": "4242"})
            try:
                time.sleep(0.2)
                det = x3d.Detector(topo, x3d.Settings.from_config(cfg), x3d.Catalog(), rules, [],
                                   gpu_check=lambda pid: pid == proc.pid, steam_names={"4242": "Busy Game"})
                orig = det.scan
                det.scan = lambda: {pid: p for pid, p in orig().items() if pid == proc.pid}  # type: ignore[method-assign]
                ctl = x3d.Controller(cfg, topo=topo, rules=rules, apps=[], detector=det, state_file=base / "state.json",
                                     mode_reader=lambda: None, mode_writer=lambda m: None, mode_writable_fn=lambda: False)
                ctl.tick()
                self.assertEqual(len(ctl.sessions), 1)
                s = ctl.sessions[0]
                self.assertEqual((s.name, s.mode), ("Busy Game", "cache"))
                self.assertEqual(set(os.sched_getaffinity(proc.pid)), set(allowed[:half]))
                self.assertEqual((s.threads, s.threads_on_ccd), (1, 1))
                time.sleep(0.6)
                ctl.tick()
                s = ctl.sessions[0]
                self.assertIsNotNone(s.share, "nach Laufzeit gibt es CPU-Zeit")
                self.assertEqual(s.share, 100.0)
                self.assertEqual(set(s.placement), {0})
                self.assertGreater(s.cpu_ticks.get(0, 0), 0)
                st = json.loads((base / "state.json").read_text())
                self.assertEqual(st["sessions"][0]["threads_on_ccd"], 1)
                self.assertEqual(st["sessions"][0]["share"], 100.0)
                proc.kill()
                proc.wait()
                ctl.tick()
                self.assertEqual(ctl.sessions, [])
                self.assertEqual(ctl._session_ticks, {})
                self.assertEqual(ctl._thread_ticks, {})
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()


class UnitAndAccessTests(unittest.TestCase):
    def test_unit_text_and_access_shell(self):
        text = x3d.unit_text()
        self.assertIn("[Service]", text)
        self.assertIn("--x3d-daemon", text)
        self.assertIn("WantedBy=default.target", text)
        sh = x3d.access_setup_shell("spieler")
        self.assertIn("tmpfiles.d/cachyos-dailytool-x3d.conf", sh)
        self.assertIn("0664 root %s", sh)
        self.assertIn("spieler", sh)
        self.assertIn("chgrp spieler", sh)
        self.assertIn("rm -f", x3d.access_remove_shell())
        argv, cwd = x3d.daemon_argv()
        self.assertEqual(argv[-1], "--x3d-daemon")


if __name__ == "__main__":
    unittest.main()
