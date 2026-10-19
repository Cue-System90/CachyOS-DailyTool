"""Tests der GTK-freien Logik (python3 -m unittest discover -s tests -t .)."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cachy_toolkit import config, system


def _cp(stdout: str = "", rc: int = 0, stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], rc, stdout, stderr)


class MountpointTests(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(system.normalize_mountpoint("/mnt", "games"), "/mnt/games")
        self.assertEqual(system.normalize_mountpoint("/mnt/", "/games/"), "/mnt/games")
        self.assertEqual(system.normalize_mountpoint("/mnt/ssd_DATA/", "ssd_DATA"), "/mnt/ssd_DATA")
        self.assertEqual(system.normalize_mountpoint("/mnt//x", "y"), "/mnt/x/y")
        self.assertEqual(system.normalize_mountpoint("", "data"), "/data")
        self.assertEqual(system.normalize_mountpoint("/mnt", ""), "/mnt")

    def test_mount_options(self):
        self.assertEqual(system.mount_options_for("ext4", 1000, 1000), ("defaults,noatime", "0 2", "ext4"))
        self.assertIn("discard=async", system.mount_options_for("btrfs", 1000, 1000)[0])
        opts, dp, fst = system.mount_options_for("ntfs", 1000, 1000)
        self.assertEqual(fst, "ntfs3")
        self.assertIn("windows_names", opts)
        opts, dp, fst = system.mount_options_for("ntfs", 1000, 1000, ntfs_driver="ntfs-3g")
        self.assertEqual(fst, "ntfs-3g")
        self.assertNotIn("windows_names", opts)
        self.assertIn("umask=022", system.mount_options_for("exfat", 1000, 1000)[0])
        self.assertEqual(system.mount_options_for("", 1, 1)[2], "auto")

    def test_fstab_line_and_detection(self):
        line = system.fstab_line("abc-123", "/mnt/x", "ext4", "defaults,noatime", "0 2")
        self.assertEqual(line, "UUID=abc-123  /mnt/x  ext4  defaults,noatime  0 2")
        text = "# kommentar\nUUID=abc-123 /mnt/x ext4 defaults 0 2\nUUID=\"def\" /y xfs defaults 0 0\n"
        self.assertTrue(system.fstab_has_uuid("abc-123", text))
        self.assertTrue(system.fstab_has_uuid("def", text))
        self.assertFalse(system.fstab_has_uuid("abc", text))
        self.assertFalse(system.fstab_has_uuid("", text))

    def test_mount_shell_command(self):
        cmd = system.mount_shell_command("/dev/sdb1", "UUID=u  /mnt/a b  ext4  defaults  0 2", "/mnt/a b", (1000, 1000), True)
        self.assertTrue(cmd.startswith("umount /dev/sdb1 || true && mkdir -p '/mnt/a b'"))
        self.assertIn("cp /etc/fstab /etc/fstab.bak.", cmd)
        self.assertIn(">> /etc/fstab", cmd)
        self.assertIn("systemctl daemon-reload", cmd)
        self.assertTrue(cmd.endswith("chown 1000:1000 '/mnt/a b'"))
        cmd2 = system.mount_shell_command("/dev/sdb1", "x", "/mnt/b", None, False)
        self.assertNotIn("umount", cmd2)
        self.assertNotIn("chown", cmd2)

    def test_format_shell_command(self):
        cmd = system.format_shell_command("/dev/sdb1", "ext4", "Spiele Daten!", mounted=True)
        self.assertEqual(cmd, "umount /dev/sdb1 && mkfs.ext4 -F -L SpieleDaten /dev/sdb1")
        self.assertEqual(system.format_shell_command("/dev/sdb1", "btrfs", "", mounted=False),
                         "mkfs.btrfs -f -L DATA /dev/sdb1")

    def test_root_source_strips_btrfs_subvolume(self):
        with mock.patch.object(system, "run", return_value=_cp("/dev/nvme1n1p2[/@]\n")):
            self.assertEqual(system.root_source(), "/dev/nvme1n1p2")
        with mock.patch.object(system, "run", return_value=_cp("zroot/ROOT/default\n")):
            self.assertIsNone(system.root_source())


LSBLK = {
    "blockdevices": [
        {"name": "sda", "size": "2000398934016", "fstype": None, "mountpoint": None, "type": "disk", "pkname": None,
         "uuid": None, "label": None, "tran": "sata", "model": "Samsung SSD 870",
         "children": [{"name": "sda1", "size": "2000397795328", "fstype": "ntfs", "mountpoint": "/run/media/u/SSD",
                       "type": "part", "pkname": "sda", "uuid": "043EFFFC3EFFE498", "label": "SSD-DATA", "tran": None, "model": None}]},
        {"name": "zram0", "size": "67", "fstype": "swap", "mountpoint": "[SWAP]", "type": "disk", "pkname": None,
         "uuid": "z", "label": None, "tran": None, "model": None},
        {"name": "nvme1n1", "size": "1", "fstype": None, "mountpoint": None, "type": "disk", "pkname": None, "uuid": None,
         "label": None, "tran": "nvme", "model": "KINGSTON",
         "children": [{"name": "nvme1n1p1", "size": "1", "fstype": "vfat", "mountpoint": "/boot", "type": "part",
                       "pkname": "nvme1n1", "uuid": "AAAA", "label": None, "tran": None, "model": None},
                      {"name": "nvme1n1p2", "size": "1", "fstype": "btrfs", "mountpoint": "/", "type": "part",
                       "pkname": "nvme1n1", "uuid": "BBBB", "label": None, "tran": None, "model": None}]},
        {"name": "nvme0n1", "size": "4000787030016", "fstype": None, "mountpoint": None, "type": "disk", "pkname": None,
         "uuid": None, "label": None, "tran": "nvme", "model": "Lexar NM790",
         "children": [{"name": "nvme0n1p1", "size": "4000785964544", "fstype": "ext4", "mountpoint": None, "type": "part",
                       "pkname": "nvme0n1", "uuid": "CCCC", "label": "games", "tran": None, "model": None}]},
        {"name": "sdb", "size": "1", "fstype": None, "mountpoint": None, "type": "disk", "pkname": None, "uuid": None,
         "label": None, "tran": "usb", "model": "Stick",
         "children": [{"name": "sdb1", "size": "1", "fstype": "exfat", "mountpoint": None, "type": "part",
                       "pkname": "sdb", "uuid": "DDDD", "label": None, "tran": None, "model": None}]},
    ]
}


class PartitionTests(unittest.TestCase):
    def test_list_partitions_excludes_system_disk_and_maps_tran(self):
        with mock.patch.object(system, "run", return_value=_cp(json.dumps(LSBLK))), \
             mock.patch.object(system, "system_disk", return_value="nvme1n1"), \
             mock.patch.object(system, "read_fstab", return_value="UUID=CCCC /mnt/games ext4 defaults 0 2\n"):
            parts = system.list_partitions()
            names = [p.name for p in parts]
            self.assertEqual(names, ["sda1", "nvme0n1p1", "sdb1"])
            sda1 = parts[0]
            self.assertEqual(sda1.tran, "sata")
            self.assertEqual(sda1.tran_label, "SATA")
            self.assertEqual(sda1.model, "Samsung SSD 870")
            self.assertTrue(sda1.temp_mounted)
            self.assertFalse(sda1.in_fstab)
            self.assertTrue(parts[1].in_fstab)
            self.assertEqual(parts[1].size, 4000785964544)
            self.assertEqual(parts[2].tran_label, "USB")
            all_parts = system.list_partitions(include_system=True)
            self.assertIn("nvme1n1p2", [p.name for p in all_parts])
            self.assertTrue([p for p in all_parts if p.name == "nvme1n1p2"][0].is_system_disk)


class LaunchTests(unittest.TestCase):
    def test_build_launch_argv_steam_syntax(self):
        self.assertEqual(system.build_launch_argv("/g/a.exe", ""), ["wine", "/g/a.exe"])
        self.assertEqual(system.build_launch_argv("/g/a.exe", "mangohud %command% -windowed"),
                         ["mangohud", "wine", "/g/a.exe", "-windowed"])
        self.assertEqual(system.build_launch_argv("/g/a.exe", "gamemoderun"), ["gamemoderun", "wine", "/g/a.exe"])
        self.assertEqual(system.build_launch_argv("/g/s.msi", "", runner=["wine", "msiexec", "/i"]),
                         ["wine", "msiexec", "/i", "/g/s.msi"])


class PrefixTests(unittest.TestCase):
    def test_list_prefixes_reads_arch_and_dpi(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p = root / "gothic"
            (p / "drive_c").mkdir(parents=True)
            (p / "system.reg").write_text("WINE REGISTRY Version 2\n;; All keys relative to \\\\Machine\n\n#arch=win64\n")
            (p / "user.reg").write_text('[Control Panel\\\\Desktop] 1700000000\n"LogPixels"=dword:00000078\n')
            (root / "notaprefix").mkdir()
            (root / "file.txt").write_text("x")
            prefixes = system.list_prefixes(root)
            self.assertEqual([x.name for x in prefixes], ["gothic"])
            self.assertEqual(prefixes[0].arch, "win64")
            self.assertEqual(prefixes[0].dpi, 120)

    def test_write_prefix_dpi_only_touches_desktop_key(self):
        reg = ('WINE REGISTRY Version 2\n\n#arch=win64\n\n'
               '[Control Panel\\\\Desktop] 1792223717\n#time=1dd5e0cd99de928\n"CaretWidth"=dword:00000001\n\n'
               '[Control Panel\\\\Desktop\\\\WindowMetrics] 1792223717\n"LogPixels"=dword:00000060\n\n'
               '[Software\\\\Wine\\\\Fonts] 1792223715\n"LogPixels"=dword:00000060\n')
        with tempfile.TemporaryDirectory() as td:
            w = Path(td)
            (w / "user.reg").write_text(reg)
            self.assertIsNone(system.prefix_dpi(w))
            system.write_prefix_dpi(w, 216)
            self.assertEqual(system.prefix_dpi(w), 216)
            text = (w / "user.reg").read_text()
            self.assertIn('#time=1dd5e0cd99de928\n"LogPixels"=dword:000000d8\n"CaretWidth"', text)
            system.write_prefix_dpi(w, 144)
            text = (w / "user.reg").read_text()
            self.assertEqual(system.prefix_dpi(w), 144)
            self.assertEqual(text.count('"LogPixels"='), 3)       # Desktop + WindowMetrics + Fonts, keine Dublette
            self.assertEqual(text.count('"LogPixels"=dword:00000060'), 2)

    def test_write_prefix_dpi_adds_missing_key(self):
        with tempfile.TemporaryDirectory() as td:
            w = Path(td)
            (w / "user.reg").write_text("WINE REGISTRY Version 2\n\n#arch=win64\n")
            system.write_prefix_dpi(w, 192)
            self.assertEqual(system.prefix_dpi(w), 192)

    def test_wine_dpi_for_desktop_sources(self):
        def fake_run(argv, **kw):
            out = {"xrdb": "Xft.antialias:\t1\nXft.dpi:\t216\n", "kreadconfig6": "2.25\n"}.get(argv[0], "")
            return subprocess.CompletedProcess(argv, 0, out, "")
        with mock.patch.object(system, "run", side_effect=fake_run), mock.patch.object(system, "have", return_value=True):
            with mock.patch.dict(os.environ, {"DISPLAY": ":0", "XDG_SESSION_TYPE": "wayland"}):
                self.assertEqual(system.wine_dpi_for_desktop(), 216)
            with mock.patch.dict(os.environ, {"DISPLAY": "", "XDG_SESSION_TYPE": "wayland"}):
                self.assertEqual(system.wine_dpi_for_desktop(), 216)      # kwinrc [Xwayland] Scale=2.25
        none = lambda argv, **kw: subprocess.CompletedProcess(argv, 0, "", "")  # noqa: E731
        with mock.patch.object(system, "run", side_effect=none), mock.patch.object(system, "have", return_value=True), \
             mock.patch.dict(os.environ, {"DISPLAY": ":0", "XDG_SESSION_TYPE": "wayland"}):
            self.assertIsNone(system.wine_dpi_for_desktop())

    def test_backup_argv_dereferences_symlinks(self):
        argv = system.backup_argv(Path("/r"), "p", Path("/b/p.tar.gz"))
        self.assertEqual(argv, ["tar", "-czhf", "/b/p.tar.gz", "-C", "/r", "p"])


class LauncherScanTests(unittest.TestCase):
    def test_scan_detects_native_flatpak_and_prefix_launchers(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            steam_real = home / "steam-real"
            steam_real.mkdir()
            (home / ".steam").mkdir()
            os.symlink(steam_real, home / ".steam" / "steam")
            (home / ".var/app/net.lutris.Lutris/data/lutris").mkdir(parents=True)
            (home / ".var/app/com.heroicgameslauncher.hgl/config/heroic").mkdir(parents=True)
            prefix_root = home / "Games" / "wine-prefixes"
            (prefix_root / "gothic" / "drive_c" / "Program Files (x86)" / "Epic Games" / "Launcher" / "Portal").mkdir(parents=True)
            (prefix_root / "gothic" / "drive_c" / "Program Files (x86)" / "Epic Games" / "Launcher" / "Portal" / "EpicGamesLauncher.exe").write_text("")
            (prefix_root / "default" / "drive_c" / "Program Files" / "Battle.net").mkdir(parents=True)
            found = system.scan_launchers(prefix_root, home=home)
            names = {(l.name, l.kind) for l in found}
            self.assertIn(("Steam", "Nativ (~/.steam/steam)"), names)
            self.assertIn(("Lutris", "Flatpak"), names)
            self.assertIn(("Heroic (Epic/GOG/Amazon)", "Flatpak"), names)
            self.assertIn(("Epic Games Store", "Wine-Prefix: gothic"), names)
            self.assertIn(("Battle.net", "Wine-Prefix: default"), names)
            steam = [l for l in found if l.name == "Steam"][0]
            self.assertTrue(steam.is_symlink)
            self.assertEqual(Path(steam.target), steam_real.resolve())


class CacheTests(unittest.TestCase):
    def test_shader_caches_find_dxvk_files(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            pr = home / "prefixes"
            (pr / "a" / "drive_c" / "game").mkdir(parents=True)
            f = pr / "a" / "drive_c" / "game" / "Game.dxvk-cache"
            f.write_bytes(b"x" * 10)
            (home / ".cache" / "mesa_shader_cache_db").mkdir(parents=True)
            entries = {e.key: e for e in system.shader_caches(pr, home / "Games", home=home, with_sizes=True)}
            self.assertEqual(entries["dxvk"].files, [f])
            self.assertEqual(entries["dxvk"].size, 10)
            self.assertTrue(entries["mesa"].exists)
            self.assertFalse(entries["steam"].exists)
            freed, n = system.clear_cache(entries["dxvk"])
            self.assertEqual((freed, n), (10, 1))
            self.assertFalse(f.exists())


class JournalTests(unittest.TestCase):
    def test_journal_filters_with_preset_and_pattern(self):
        out = "Oct 16 kernel: nvidia 580 loaded\nOct 16 kwin: hello\nOct 16 NVRM: Xid 79\n"
        with mock.patch.object(system, "run", return_value=_cp(out)):
            txt = system.journal("nvidia")
            self.assertIn("nvidia 580", txt)
            self.assertIn("Xid 79", txt)
            self.assertNotIn("kwin", txt)
            self.assertEqual(system.journal("nvidia", pattern="xid"), "Oct 16 NVRM: Xid 79")
            self.assertEqual(system.journal("boot", pattern="nichtda"), "Keine passenden Einträge gefunden.")

    def test_pending_updates_checkupdates_codes(self):
        with mock.patch.object(system, "have", return_value=True), \
             mock.patch.object(system, "run", return_value=_cp("", rc=2)):
            self.assertEqual(system.pending_updates(max_age=0)[0], [])
        with mock.patch.object(system, "have", return_value=True), \
             mock.patch.object(system, "run", return_value=_cp("linux 1 -> 2\nmesa 1 -> 2\n", rc=0)):
            self.assertEqual(len(system.pending_updates(max_age=0)[0]), 2)


KSCREEN_JSON = json.dumps({"outputs": [
    {"id": 2, "name": "eDP-1", "enabled": True, "connected": True, "scale": 1.5, "priority": 2, "currentModeId": "3",
     "modes": [{"id": "3", "size": {"width": 2560, "height": 1600}}]},
    {"id": 1, "name": "HDMI-A-1", "enabled": True, "connected": True, "scale": 2.25, "priority": 1, "currentModeId": "10",
     "modes": [{"id": "10", "size": {"width": 3840, "height": 2160}}], "size": {"width": 3840, "height": 2160}},
    {"id": 3, "name": "DP-1", "enabled": False, "connected": True, "scale": 1, "priority": 0},
    {"id": 4, "name": "DP-2", "enabled": True, "connected": False, "scale": 1},
]})


class ScreenScaleTests(unittest.TestCase):
    def test_parse_kscreen_json_active_outputs_primary_first(self):
        screens = system.parse_kscreen_json(KSCREEN_JSON)
        self.assertEqual([(s.name, s.percent, s.label) for s in screens],
                         [("HDMI-A-1", 225, "HDMI-A-1 (3840×2160)"), ("eDP-1", 150, "eDP-1 (2560×1600)")])
        self.assertEqual(system.parse_kscreen_json("kein json"), [])
        self.assertEqual(system.parse_kscreen_json('{"outputs": [{"name": "X", "enabled": true, "connected": true, "scale": 1}]}')[0].label, "X")

    def test_wayland_reads_and_sets_per_screen(self):
        calls: list[list[str]] = []

        def fake_run(argv, **kw):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, KSCREEN_JSON if argv[:2] == ["kscreen-doctor", "-j"] else "", "")
        with mock.patch.object(system, "run", side_effect=fake_run), mock.patch.object(system, "have", return_value=True), \
             mock.patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": "KDE", "XDG_SESSION_TYPE": "wayland"}):
            self.assertEqual(system.desktop_scale_get(), (225, "kde-wayland"))
            msg = system.desktop_scale_set(200, "HDMI-A-1")
            self.assertIn(["kscreen-doctor", "output.HDMI-A-1.scale.2"], calls)
            system.desktop_scale_set(125, "eDP-1")
            self.assertIn(["kscreen-doctor", "output.eDP-1.scale.1.25"], calls)
        self.assertIn("sofort wirksam", msg)
        self.assertFalse(any(c[0] == "kwriteconfig6" for c in calls))

    def test_x11_session_keeps_kdeglobals(self):
        calls: list[list[str]] = []

        def fake_run(argv, **kw):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, "1.5\n" if argv[0] == "kreadconfig6" else "", "")
        with mock.patch.object(system, "run", side_effect=fake_run), mock.patch.object(system, "have", return_value=True), \
             mock.patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": "KDE", "XDG_SESSION_TYPE": "x11"}):
            self.assertEqual(system.screen_scales(), [])
            self.assertEqual(system.desktop_scale_get(), (150, "kde"))
        self.assertFalse(any(c[0] == "kscreen-doctor" for c in calls))


class ConfigTests(unittest.TestCase):
    def test_legacy_format_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "config.env"
            path.write_text('WINE_PREFIX_ROOT="/data/prefixes"\nUI_WIDTH=800\nUI_HEIGHT=500\nUNKNOWN_KEY="bleibt"\n')
            cfg = config.Config(path)
            self.assertEqual(str(cfg.wine_prefix_root), "/data/prefixes")
            self.assertEqual(cfg.window_size, (900, 600))  # Mindestgröße greift
            self.assertEqual(cfg.get("UNKNOWN_KEY"), "bleibt")
            cfg.set("MOUNT_BASE", "/media")
            cfg2 = config.Config(path)
            self.assertEqual(cfg2.get("MOUNT_BASE"), "/media")
            self.assertEqual(cfg2.get("UNKNOWN_KEY"), "bleibt")
            self.assertEqual(str(cfg2.backup_dir), config.DEFAULTS["PREFIX_BACKUP_DIR"])

    def test_human_bytes(self):
        self.assertEqual(system.human_bytes(0), "0 B")
        self.assertEqual(system.human_bytes(1536), "2 KiB")
        self.assertEqual(system.human_bytes(5 * 1024**3), "5.0 GiB")
        self.assertEqual(system.human_bytes(None), "–")


if __name__ == "__main__":
    unittest.main()
