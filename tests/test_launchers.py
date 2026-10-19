"""Tests für den Launcher-Katalog: Pfadübersetzung, Erkennung, Installer-Zuordnung, Download."""
from __future__ import annotations

import io
import os
import tempfile
import unittest
from pathlib import Path

from cachy_toolkit import launchers, registry, system


class FakeResp(io.BytesIO):
    def __init__(self, data: bytes, headers: dict, url: str, status: int = 200):
        super().__init__(data)
        self.headers = headers
        self._url = url
        self.status = status

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def make_prefix(base: Path, user: str = "steamuser") -> Path:
    (base / "drive_c" / "users" / user / "AppData" / "Local").mkdir(parents=True)
    (base / "drive_c" / "users" / "Public").mkdir(parents=True)
    (base / "drive_c" / "windows" / "system32").mkdir(parents=True)
    return base


class PathTests(unittest.TestCase):
    def test_win_to_unix_case_insensitive_and_tokens(self):
        with tempfile.TemporaryDirectory() as td:
            w = make_prefix(Path(td) / "p")
            exe = w / "drive_c" / "Program Files (x86)" / "ubisoft" / "Ubisoft Game Launcher" / "UbisoftConnect.exe"
            exe.parent.mkdir(parents=True)
            exe.write_bytes(b"MZ")
            hits = launchers.win_to_unix(w, r"C:\Program Files (x86)\Ubisoft\Ubisoft Game Launcher\UbisoftConnect.exe")
            self.assertEqual(hits, [exe])
            amazon = w / "drive_c" / "users" / "steamuser" / "AppData" / "Local" / "Amazon Games" / "App" / "Amazon Games.exe"
            amazon.parent.mkdir(parents=True)
            amazon.write_bytes(b"MZ")
            self.assertEqual(launchers.win_to_unix(w, r"%LOCALAPPDATA%\Amazon Games\App\Amazon Games.exe"), [amazon])
            self.assertEqual(launchers.unix_to_win(w, exe), r"C:\Program Files (x86)\ubisoft\Ubisoft Game Launcher\UbisoftConnect.exe")
            self.assertNotIn("Public", launchers.prefix_users(w))

    def test_detect_and_find_with_search(self):
        with tempfile.TemporaryDirectory() as td:
            w = make_prefix(Path(td) / "p")
            gal = w / "drive_c" / "Program Files (x86)" / "GOG Galaxy" / "GalaxyClient.exe"
            gal.parent.mkdir(parents=True)
            gal.write_bytes(b"MZ")
            found = launchers.detect_installed(w)
            self.assertEqual([(s.id, e) for s, e in found], [("gog-galaxy", gal)])
            # Versionsordner der EA App → nur mit Suche; fremde Datei gleichen Namens außerhalb „Electronic Arts“ wird ignoriert
            stray = w / "drive_c" / "Games" / "EA" / "EADesktop.exe"
            stray.parent.mkdir(parents=True)
            stray.write_bytes(b"MZ")
            spec = launchers.get("ea-app")
            self.assertIsNone(launchers.find_exe(spec, w, search=True))
            versioned = w / "drive_c" / "Program Files" / "Electronic Arts" / "EA Desktop" / "13.400.0" / "EA Desktop" / "EALauncher.exe"
            versioned.parent.mkdir(parents=True)
            versioned.write_bytes(b"MZ")
            self.assertIsNone(launchers.find_exe(spec, w))
            self.assertEqual(launchers.find_exe(spec, w, search=True), versioned)
            # Rockstar: allgemeines Launcher.exe nur im Rockstar-Ordner
            other = w / "drive_c" / "Tools" / "Launcher.exe"
            other.parent.mkdir(parents=True)
            other.write_bytes(b"MZ")
            self.assertIsNone(launchers.find_exe(launchers.get("rockstar"), w, search=True))

    def test_post_install_files_written_once(self):
        with tempfile.TemporaryDirectory() as td:
            w = make_prefix(Path(td) / "p")
            spec = launchers.get("ubisoft-connect")
            written = launchers.write_post_install(spec, w)
            target = w / "drive_c" / "users" / "steamuser" / "AppData" / "Local" / "Ubisoft Game Launcher" / "settings.yaml"
            self.assertEqual(written, [target])
            self.assertIn("closebehavior: CloseBehavior_Close", target.read_text())
            target.write_text("eigene Einstellungen")
            self.assertEqual(launchers.write_post_install(spec, w), [])
            self.assertEqual(target.read_text(), "eigene Einstellungen")

    def test_match_exe_and_installer(self):
        self.assertEqual(launchers.match_exe("/x/drive_c/Program Files (x86)/Battle.net/Battle.net Launcher.exe").id, "battlenet")
        self.assertEqual(launchers.match_exe("/x/drive_c/Program Files/Rockstar Games/Launcher/Launcher.exe").id, "rockstar")
        self.assertIsNone(launchers.match_exe("/x/drive_c/Foo/Launcher.exe"))
        self.assertEqual(launchers.spec_for_installer("/dl/UbisoftConnectInstaller.exe").id, "ubisoft-connect")
        self.assertEqual(launchers.spec_for_installer("/dl/EpicInstaller-20.3.3.msi").id, "epic-games")
        self.assertEqual(launchers.spec_for_installer("/dl/Battle.net-Setup (1).exe").id, "battlenet")
        self.assertIsNone(launchers.spec_for_installer("/dl/setup.exe"))

    def test_catalog_consistency(self):
        ids = [s.id for s in launchers.CATALOG]
        self.assertEqual(len(ids), len(set(ids)))
        for s in launchers.CATALOG:
            self.assertTrue(s.url.startswith("https://"), s.id)
            self.assertTrue(s.exe_paths and s.exe_names, s.id)
            self.assertTrue(s.filename.lower().endswith((".exe", ".msi")), s.id)
            self.assertTrue(s.uninstall_match, s.id)
            for wp in s.exe_paths:
                self.assertRegex(wp, r"^([A-Za-z]:\\|%[A-Z()0-9]+%\\)", s.id)

    def test_chromium_launchers_stay_on_xwayland(self):
        """Bewusste Ausnahme von der Wayland-Vorgabe: Store-Launcher (Chromium/CEF) bleiben weiß unter dem
        Proton-Wayland-Treiber (Echttest Ubisoft Connect/Rockstar 2026-10-17, GE-Proton #188 für die EA App).
        GOG Galaxy zeichnet mit jedem getesteten Proton (wayland_ok "any") und bekommt die Vorgabe nicht mehr."""
        for s in launchers.CATALOG:
            self.assertIn(s.wayland_ok, ("", "ge", "any"), s.id)
            want = None if s.wayland_ok == "any" else "0"
            self.assertEqual(s.env_dict.get("PROTON_ENABLE_WAYLAND"), want, s.id)
        self.assertEqual({s.id for s in launchers.CATALOG if s.wayland_ok}, {"ubisoft-connect", "rockstar", "gog-galaxy"})


class DownloadTests(unittest.TestCase):
    def test_download_progress_cache_and_validation(self):
        spec = launchers.get("battlenet")
        data = b"MZ" + b"\0" * 300000
        calls = []

        def opener(req, timeout=0):
            calls.append(req.full_url)
            return FakeResp(data, {"Content-Length": str(len(data)),
                                   "Content-Disposition": 'attachment; filename="Battle.net-Setup.exe"'},
                            "https://downloader.battle.net/download/installer/win/1.0.66/Battle.net-Setup.exe")
        with tempfile.TemporaryDirectory() as td:
            lines: list[str] = []
            p = launchers.download(spec, lines.append, cache_dir=Path(td), opener=opener)
            self.assertEqual(p.name, "Battle.net-Setup.exe")
            self.assertEqual(p.read_bytes(), data)
            self.assertTrue(any("Download fertig" in l for l in lines))
            lines.clear()
            p2 = launchers.download(spec, lines.append, cache_dir=Path(td), opener=opener)
            self.assertEqual(p2, p)
            self.assertTrue(any("Zwischenspeicher" in l for l in lines))

    def test_gog_resolver_and_md5(self):
        import hashlib, json as _json
        spec = launchers.get("gog-galaxy")
        payload = b"MZ" + b"\1" * 5000
        md5 = hashlib.md5(payload).hexdigest()
        link = "https://content-system.gog.com/open_link/download?path=/open/galaxy/client/setup_galaxy_9.9.9.exe"

        def opener(req, timeout=0, good=True):
            if "remote-config" in req.full_url:
                return FakeResp(_json.dumps({"content": {"windows": {"downloadLink": link, "installerMd5": md5 if good else "0" * 32,
                                                                    "version": "9.9.9"}}}).encode(), {}, req.full_url)
            self.assertEqual(req.full_url, link)
            return FakeResp(payload, {"Content-Length": str(len(payload))}, link)
        with tempfile.TemporaryDirectory() as td:
            lines: list[str] = []
            p = launchers.download(spec, lines.append, cache_dir=Path(td), opener=opener)
            self.assertEqual(p.name, "setup_galaxy_9.9.9.exe")
            self.assertTrue(any("9.9.9" in l for l in lines))
            self.assertTrue(any("MD5" in l for l in lines))
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(launchers.DownloadError):
                launchers.download(spec, lambda l: None, cache_dir=Path(td), opener=lambda r, timeout=0: opener(r, timeout, good=False))

    def test_download_rejects_html(self):
        spec = launchers.get("ea-app")

        def opener(req, timeout=0):
            return FakeResp(b"<html>nope</html>", {"Content-Length": "17"}, spec.url)
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(launchers.DownloadError):
                launchers.download(spec, lambda l: None, cache_dir=Path(td), opener=opener)
            self.assertEqual(list(Path(td).iterdir()), [])

    def test_download_cancel(self):
        spec = launchers.get("rockstar")

        def opener(req, timeout=0):
            return FakeResp(b"MZ" + b"\0" * 200000, {"Content-Length": "200002"}, spec.url)
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(launchers.DownloadError):
                launchers.download(spec, lambda l: None, cancelled=lambda: True, cache_dir=Path(td), opener=opener)


class StepAndRegistryTests(unittest.TestCase):
    def test_python_step_in_sequence(self):
        lines: list[str] = []
        seen = {}

        def step(log, ctx):
            seen["ctx"] = ctx
            log("python-schritt")
            return 0
        rc = system.run_step_sequence([step, ["sh", "-c", "echo danach"]], lines.append)
        self.assertEqual(rc, 0)
        self.assertEqual(lines[0], "python-schritt")
        self.assertIn("danach", lines)
        self.assertFalse(seen["ctx"].cancelled())
        self.assertEqual(system.run_step_sequence([lambda log, ctx: 5, ["true"]], lines.append), 5)
        self.assertEqual(system.run_step_sequence([lambda log, ctx: 1 / 0], lines.append), 1)

    def test_find_app_prevents_duplicates(self):
        with tempfile.TemporaryDirectory() as td:
            r = registry.Registry(Path(td) / "r.json")
            pfx = Path(td) / "pfx"
            pfx.mkdir()
            r.add_app(registry.AppEntry(id="ubi", name="Ubisoft Connect", exe=str(pfx / "u.exe"), prefix=str(pfx), launcher="ubisoft-connect"))
            self.assertEqual(r.find_app(pfx, launcher="ubisoft-connect").id, "ubi")
            self.assertEqual(r.find_app(pfx, exe=str(pfx / "u.exe")).id, "ubi")
            self.assertIsNone(r.find_app(pfx, launcher="ea-app"))
            r2 = registry.Registry(Path(td) / "r.json")
            self.assertEqual(r2.get_app("ubi").launcher, "ubisoft-connect")


if __name__ == "__main__":
    unittest.main()
