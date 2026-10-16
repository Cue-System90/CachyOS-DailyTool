"""Registry: bekannte Prefixe (auch außerhalb des Standardordners), Einstellungen je Prefix, Anwendungen/Verknüpfungen."""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .config import CONF_DIR

REGISTRY_FILE = CONF_DIR / "registry.json"


@dataclass
class PrefixSettings:
    tool: str = "auto"              # CompatTool-ID oder "auto"
    launch_options: str = ""        # Steam-Syntax, gilt für alle Programme im Prefix
    env: dict[str, str] = field(default_factory=dict)
    arch: str = "win64"
    registered: bool = False        # außerhalb des Standardordners ausdrücklich eingetragen


@dataclass
class AppEntry:
    id: str
    name: str
    exe: str                        # Unix-Pfad der .exe innerhalb des Prefix (oder beliebig)
    prefix: str                     # Prefix-Verzeichnis
    launch_options: str = ""
    env: dict[str, str] = field(default_factory=dict)
    tool: str = ""                  # Runner-Override, "" = Einstellung des Prefix
    args: str = ""                  # feste Programmargumente
    icon: str = ""                  # Pfad zum PNG oder Icon-Name
    menu: bool = False
    desktop: bool = False
    created: float = 0.0


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s or "app"


class Registry:
    def __init__(self, path: Path | str = REGISTRY_FILE) -> None:
        self.path = Path(path)
        self.prefixes: dict[str, PrefixSettings] = {}
        self.apps: dict[str, AppEntry] = {}
        self.locations: list[str] = []
        self.load()

    # ------------------------------------------------------------------ Persistenz
    def load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        self.prefixes = {}
        for k, v in (data.get("prefixes") or {}).items():
            try:
                self.prefixes[k] = PrefixSettings(**{f: v.get(f, getattr(PrefixSettings(), f)) for f in PrefixSettings.__dataclass_fields__})
            except TypeError:
                continue
        self.apps = {}
        for k, v in (data.get("apps") or {}).items():
            try:
                v = dict(v)
                v["id"] = k
                self.apps[k] = AppEntry(**{f: v.get(f, getattr(AppEntry("", "", "", ""), f)) for f in AppEntry.__dataclass_fields__})
            except TypeError:
                continue
        self.locations = [str(x) for x in (data.get("locations") or []) if x]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": 1,
            "prefixes": {k: asdict(v) for k, v in self.prefixes.items()},
            "apps": {k: {kk: vv for kk, vv in asdict(v).items() if kk != "id"} for k, v in self.apps.items()},
            "locations": self.locations,
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    # ------------------------------------------------------------------ Prefixe
    @staticmethod
    def _key(path: str | Path) -> str:
        return str(Path(os.path.expanduser(str(path))).resolve()) if Path(os.path.expanduser(str(path))).exists() else str(Path(os.path.expanduser(str(path))))

    def prefix_settings(self, path: str | Path) -> PrefixSettings:
        return self.prefixes.get(self._key(path), PrefixSettings())

    def set_prefix_settings(self, path: str | Path, **changes) -> PrefixSettings:
        key = self._key(path)
        ps = self.prefixes.get(key) or PrefixSettings()
        for k, v in changes.items():
            if hasattr(ps, k):
                setattr(ps, k, v)
        self.prefixes[key] = ps
        self.save()
        return ps

    def register_prefix(self, path: str | Path, **settings) -> None:
        self.set_prefix_settings(path, registered=True, **settings)

    def forget_prefix(self, path: str | Path, remove_apps: bool = True) -> list[AppEntry]:
        key = self._key(path)
        self.prefixes.pop(key, None)
        removed: list[AppEntry] = []
        if remove_apps:
            for app_id, app in list(self.apps.items()):
                if self._key(app.prefix) == key:
                    removed.append(self.apps.pop(app_id))
        self.save()
        return removed

    def move_prefix(self, old: str | Path, new: str | Path) -> None:
        """Einstellungen und Anwendungen auf den neuen Pfad umschreiben (nach Umbenennen/Verschieben)."""
        ok, nk = self._key(old), str(Path(os.path.expanduser(str(new))))
        if ok in self.prefixes:
            self.prefixes[nk] = self.prefixes.pop(ok)
        for app in self.apps.values():
            if self._key(app.prefix) == ok:
                app.prefix = nk
                if app.exe.startswith(ok + os.sep):
                    app.exe = nk + app.exe[len(ok):]
        self.save()

    def registered_prefix_paths(self) -> list[Path]:
        return [Path(k) for k, v in self.prefixes.items() if v.registered]

    # ------------------------------------------------------------------ Speicherorte
    def add_location(self, path: str | Path) -> None:
        p = str(Path(os.path.expanduser(str(path))))
        if p not in self.locations:
            self.locations.append(p)
            self.save()

    def remove_location(self, path: str | Path) -> None:
        p = str(Path(os.path.expanduser(str(path))))
        if p in self.locations:
            self.locations.remove(p)
            self.save()

    # ------------------------------------------------------------------ Anwendungen
    def new_app_id(self, name: str) -> str:
        base = slugify(name)
        app_id, n = base, 2
        while app_id in self.apps:
            app_id = f"{base}-{n}"
            n += 1
        return app_id

    def add_app(self, app: AppEntry) -> AppEntry:
        if not app.created:
            app.created = time.time()
        self.apps[app.id] = app
        self.save()
        return app

    def update_app(self, app_id: str, **changes) -> AppEntry | None:
        app = self.apps.get(app_id)
        if not app:
            return None
        for k, v in changes.items():
            if hasattr(app, k):
                setattr(app, k, v)
        self.save()
        return app

    def remove_app(self, app_id: str) -> AppEntry | None:
        app = self.apps.pop(app_id, None)
        self.save()
        return app

    def get_app(self, app_id: str) -> AppEntry | None:
        return self.apps.get(app_id)

    def apps_for_prefix(self, path: str | Path) -> list[AppEntry]:
        key = self._key(path)
        return [a for a in self.apps.values() if self._key(a.prefix) == key]

    def all_apps(self) -> list[AppEntry]:
        return sorted(self.apps.values(), key=lambda a: a.name.lower())
