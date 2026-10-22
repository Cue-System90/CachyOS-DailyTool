# CachyOS DailyTool v5

**Deutsch:** [README.md](README.md)

An everyday tool for **CachyOS / Arch Linux** with a real graphical interface
(Python, GTK4, libadwaita) instead of the previous kdialog/zenity dialog chains. All functions
can be reached directly with a click: on the left the sidebar with sections and counters, on the right
the respective page with forms, lists, live values and a log window for running actions.
Dark appearance in anthracite with a copper accent, icon from the CueSystem logo.
The interface is available in **German and English**; it follows the system language and can be switched with the
language icon in the header bar (see [Language](#language)).

Up to v5.0.4 the project was called “CachyOS Alltagstool” (package and command `cachy-toolkit`). Since v5.1.0 the package
and command are called `cachyos-dailytool`; the new package replaces the old one, the installer cleans up the old installation and
existing app shortcuts are rewritten to the new command on the first start.

The Bash version v4 remains unchanged under `legacy/` and is replaced by the installer;
the configuration (`~/.config/cachy-daily-toolkit/config.env`) is carried over.

## Interface

```
OVERVIEW                  Overview: GPU, RAM, uptime, updates, prefixes, drives · Quick access · Notices · System information
WINE & PREFIXES           Launchers · Run program · Manage prefixes
LAUNCHERS & LOCATIONS     Locations & migration · ProtonPlus & compat tools · Disk usage
DRIVES                    Mount drives · Create folder
SYSTEM                    Update & DKMS · GPU status · X3D cores (CCD) · Logs & diagnostics · Clear shader caches · Scaling
```

Counters in the sidebar: number of Wine prefixes, number of additional partitions (copper if
one of them has no fstab entry yet), number of available package updates. Header bar: language
(icon showing DE/EN – clicking a language restarts the program in that language), refresh page (Ctrl+R),
menu (Settings Ctrl+, · Log folder · About · Uninstall · Quit Ctrl+Q).
The window size is saved on closing, and so is the last opened page.

## Features by page

| Page | What it does |
|---|---|
| **Overview** | Tiles with live values (GPU name, temperature, load, VRAM · RAM usage · uptime · updates · prefixes · drives), quick access to the most important pages, notices (temporarily mounted or unmounted drives, kernel restart, missing ProtonPlus, updates), system information. |
| **Launchers** | Wizard for well-known Windows launchers: **Ubisoft Connect, EA App, Epic Games Launcher, GOG Galaxy 2.0 (full offline installer, current version via GOG's version query, MD5-verified), Battle.net, Rockstar Games Launcher, Amazon Games**. Steam (Windows) is deliberately missing: Proton always replaces its `steam.exe` with its built-in version. A click on “Install …” asks for the prefix (new or existing), location, runner (Proton via umu, Proton directly, Wine), launch options with GOverlay import, program arguments, environment variables and shortcuts (start menu, desktop), then downloads the **official installer** with progress (cache `~/.cache/cachy-daily-toolkit/installers`), creates the prefix, installs (unattended where that works reliably) and **creates the shortcut automatically** as soon as the newly installed main program exists – even if the launcher opens itself after the installation. Proven Proton settings are stored per launcher (e.g. `PROTON_ENABLE_WAYLAND=0` – the Chromium/CEF interfaces of the store launchers stay white under the Wayland driver; GOG Galaxy still runs under Wayland, Ubisoft Connect and Rockstar with GE-Proton 11-6 or later, see below –, Battle.net `WINE_SIMULATE_WRITECOPY=1`), and the recommended runner is preselected; Ubisoft Connect gets a settings file in advance (overlay off, closing quits the client). The wizard warns about a location on NTFS/exFAT. Installed launchers are **detected in all prefixes**, including manually installed ones; one click creates the shortcut for them. Per installed launcher: launch, settings (runner, launch options, variables, shortcuts), **uninstall**. |
| **Run program** | Choose an .exe/.msi or drop it via drag and drop, choose a prefix or create a new one. **Runner** selectable per launch (Same as prefix, Proton via umu, Proton directly, Wine). **Launch options in Steam syntax** (`mangohud gamemoderun %command% -windowed`, environment variables in front), templates, **“From GOverlay”** (derives the line from the GOverlay/MangoHud configuration) and “Open GOverlay”, additional environment variables, **command preview** of the complete launch command. **Install …** opens the install dialog (see below). winecfg, winetricks, drive_c, file manager association for .exe/.msi. |
| **Install …** | Asks before every installation: name of the application, **target folder** (default folder, additional locations or any other folder – created if needed) and prefix name or an existing prefix, runner, architecture, launch options, shortcut in the start menu/on the desktop. Recognises known launcher installers by their file name (then unattended and with a known program path). Creates the prefix, runs the installer in the log window and **then creates the shortcut automatically**: reliably for known launchers, otherwise for the most likely newly installed program (can be changed under “Manage prefixes”); only if nothing is found does the tool ask. The icon is read from the .exe. |
| **Manage prefixes** | **Locations**: change the default folder (optionally moving existing prefixes along), add/remove additional locations – prefixes in all locations are found automatically and marked “ADDITIONAL LOCATION”. Every prefix (also outside the default folder, Wine and Proton layout) as an expandable row: launch, winecfg, winetricks, drive_c, backup, **rename**, **move** (to another file system via rsync), **create shortcut**, **delete**; **runner** (Proton/Wine/umu), **launch options** and **environment variables** for all programs in the prefix, **Windows DPI**; below that the prefix's applications with launch/edit/**uninstall** and markers for start menu/desktop, as well as detected launchers without a shortcut with “Create shortcut”. **Windows programs …** shows the prefix's Windows program list (like “Uninstall a program” on Windows) with uninstall per entry. Restore or delete backups. |
| **Locations & migration** | Choose the target folder (free space is shown). Detected launchers with type (native/Flatpak/Wine prefix), path and size: Steam, Lutris, Heroic, Bottles, Steam compat tools as well as Ubisoft Connect, GOG Galaxy, Rockstar, Xbox, Epic, Battle.net, EA App in prefixes with any name. Selection → **rsync with progress**, then delete the original and create a symlink. Entries that have already been moved are marked. |
| **ProtonPlus & compat tools** | Status (native/Flatpak/missing), launch (only when installed). **Install**: preferably as a native package from the package repositories (on CachyOS `cachyos/protonplus`, one click, `pacman -S --needed` via pkexec; updates then arrive with the system update), otherwise via Flatpak (Flathub is set up as a user remote if needed). Tags for umu-launcher and proton-cachyos. List of installed Proton/Wine versions in Steam, Heroic, Lutris, Bottles, umu; all tool paths with present/missing. |
| **Disk usage** | All real file systems with bars and free/total; gaming folders (Steam, Lutris, Heroic, Bottles, prefix root, target folder, backups) with sizes and total. |
| **Mount drives** | Additional NVMe/SATA/USB partitions (system drive hidden, can optionally be shown) with size, file system, model, transport, status (in fstab / temporary / not mounted / no file system). Selection → form: UUID (copyable), NTFS driver (ntfs3/ntfs-3g), base directory, folder name, computed mount point, suggested mount options per file system, **fstab line as a preview**. One click adds the entry, creates an fstab backup, runs `systemctl daemon-reload` and `mount` and sets the owner for ext4/btrfs/xfs. **Format** (ext4/btrfs/xfs/exFAT) with label; the confirmation requires typing the device name. |
| **Create folder** | Create a folder on a mount point with you as the owner (pkexec); take ownership of an existing folder recursively. |
| **Update & DKMS** | Available updates (checkupdates, without root), running kernel with restart notice, NVIDIA module version, DKMS status. **Update system** runs `pacman -Syu --noconfirm` via pkexec, with live output in the log; afterwards an automatic DKMS and kernel check. |
| **GPU status** | Live card per GPU: temperature, utilisation, power, GPU/VRAM clock, fan, VRAM bar. NVIDIA via nvidia-smi, AMD/Intel via sysfs. Automatic refresh every 2 s (can be switched off), full nvidia-smi output. |
| **X3D cores (CCD)** | For Ryzen with two CCDs and one 3D V-Cache die (7950X3D/7900X3D, 9950X3D/9900X3D): shows both CCDs (cores, L3, CPU numbers) and the current **core preference** of the kernel driver `amd_3d_vcache` (cache/frequency CCD, switchable with a click; one-time “Set up access” via pkexec). **Background service** (`--x3d-daemon`, systemd --user, autostart switch): detects running games like AMD's 3D V-Cache driver on Windows and keeps their entire process tree on the cache CCD, switches the core preference and moves Steam, store launchers, Discord and browsers to the other CCD; after the game the idle default applies again. **Detected games live** with source, reason, assignment and thread placement (how many threads are pinned to the assigned CCD, where they last ran, share of CPU time on the target CCD since detection) – per game with a click Cache CCD / Frequency CCD / Unrestricted / “Not a game”. Per CCD **live utilisation** (since 5.1.1) as a bar per CPU (Linux counts SMT siblings separately: 0-7 and 16-23 = CCD0). **Catalogue** (bundled compatibility list with search) and **custom rules** (also by .exe name, process name or Steam AppID). Defaults: unknown games, idle core preference, background programs, enforce assignment, additional game folders. Log of the service. |
| **Logs & diagnostics** | Journal of the current or previous boot with preset filters (everything, warnings/errors, kernel, NVIDIA/DRM, AMD, gamescope/Steam/Vulkan/Proton, Wayland/KWin), custom search pattern (regular expression) and line limit. Copy or save as a file. |
| **Clear shader caches** | Steam shader cache (native/Flatpak), DXVK/VKD3D caches in prefixes and game folders, Mesa cache, NVIDIA GLCache – with sizes, selection and confirmation. |
| **Scaling** | Desktop scaling 100–250 %: under Plasma Wayland the real value per screen (kscreen-doctor), where “Apply” takes effect immediately on the selected screen; otherwise KDE kdeglobals / GNOME / GDK_SCALE (after logging out and back in); link to the display settings; Windows DPI per prefix – **automatically matched to the desktop scaling** (Xft.dpi, e.g. 216 at 225 %): new prefixes get it on creation, prefixes without their own DPI before launch (can be switched off, custom values are kept); reset window size. |
| **Settings** (menu) | Wine prefix root, gaming target folder, backup folder, base directory for mount points, default launch options, GPU auto-refresh. |

## Uninstalling launchers and programs

“Uninstall …” (Launchers page or an application on the prefix page) offers three levels:

| Level | What happens |
|---|---|
| Program in the prefix (preselected) | Start the Windows uninstallation of the program in the prefix (entry from the Windows program list via `uninstaller --remove`, using the same runner as the prefix; a selection if there are several matches). The tool waits until downstream uninstall processes have finished as well and then removes the shortcuts and the entry. The prefix, including the games installed in it, stays. |
| Complete | End the running processes of the prefix, delete the prefix with all its files – including games installed in it – and remove all applications of the prefix including their shortcuts. Locations, the home directory and folders that are not prefixes are never deleted. |
| Shortcut only | Remove the shortcuts and the entry, files stay. |

While a launcher is still open after the installation, the tool points out when uninstalling that it should be closed first.

## Runners: Proton, umu-launcher, Wine

As in the Galaxy port, all compatibility tools are found: Proton from `compatibilitytools.d` (Steam native/Flatpak,
`/usr/share/steam/compatibilitytools.d` → **proton-cachyos**), Proton from Steam libraries and Heroic, GE-Proton/UMU-Proton
via umu (automatic download), system Wine as well as Lutris and Bottles runners. Automatic choice: system Proton > newest
Proton > umu GE > Wine; the default and “Prefer umu” are in the settings and can be overridden per prefix and per application.

| Runner | Launch | Prefix layout |
|---|---|---|
| umu | `umu-run <exe>` with `WINEPREFIX`, `PROTONPATH`, `GAMEID`, `STORE=none` | Prefix folder directly |
| Proton directly | `<proton>/proton waitforexitandrun <exe>` with `STEAM_COMPAT_DATA_PATH`; SLR Protons via `_v2-entry-point`, otherwise a hint to use umu | `<prefix>/pfx` |
| Wine | `<wine> <exe>` with `WINEPREFIX` (`pfx/` if present) | Prefix folder directly |

**GE-Proton/UMU-Proton “latest version”:** umu downloads these versions via the GitHub API. Without authentication GitHub allows
60 requests per hour per internet connection; if the limit is exhausted or GitHub cannot be reached and no version is
available locally yet, umu aborts. The tool checks this beforehand (via `api.github.com/rate_limit`, which does not count towards the limit):
if a version is available locally in `~/.local/share/Steam/compatibilitytools.d`, it is used; otherwise the tool automatically falls back to
the installed Proton (proton-cachyos) and states the reason in the log. If a shortcut with such a runner is launched
while the runner cannot be downloaded, a clear message appears instead of a silent abort.

**Wayland driver (KDE Wayland as default):** Proton programs start with `PROTON_ENABLE_WAYLAND=1` (Settings →
“Proton Wayland driver as default”). Exception per prefix (Manage prefixes → “Proton Wayland driver”) or per
application (Edit): “On” or “Off (XWayland)”; a custom `PROTON_ENABLE_WAYLAND` variable in the environment or the
launch options always takes precedence. This fixes, for example, the white Wine window frame in games that start in a window and
then switch to fullscreen. **The store launchers from the wizard deliberately stay on XWayland:** in testing (RTX 4090,
proton-cachyos 11) Ubisoft Connect and Rockstar Games Launcher stayed completely white with the Wayland driver, while under XWayland
the login appeared immediately (screenshots under `reference/wayland-test-2026-10-17/`); the EA App shows the same result
(GE-Proton #188). Games that a launcher starts itself inherit its Windows desktop and therefore XWayland – this
cannot be changed from the outside under Wine.

**Exception GE-Proton 11-6 and later (since 5.0.4):** Since 11-6, GE-Proton also renders Chromium/CEF interfaces with the
Wayland driver (across processes via DMA-BUF). In testing with GE-Proton11-9, Ubisoft Connect and the Rockstar
Games Launcher appeared as native Wayland windows with the login page, sharp at 216 DPI. If one of these two launchers runs with
GE-Proton 11-6 or later (installed permanently or “GE-Proton (latest version)” via umu), the tool omits the catalogue default
`PROTON_ENABLE_WAYLAND=0` at launch. **The launcher and all games it starts then run under KDE Wayland.**
With “Off (XWayland)” on the application, the old behaviour remains.

**GOG Galaxy under Wayland (since 5.0.4):** GOG Galaxy 2.1 renders its login dialog completely with the Wayland driver
– with GE-Proton11-9 **and** with proton-cachyos 11. This is probably due to the launch arguments
`--in-process-gpu` and `libglesv2=d`. The catalogue therefore no longer sets `PROTON_ENABLE_WAYLAND=0` for GOG; for
existing entries the tool lifts the old default at launch. EA App, Epic, Battle.net and Amazon are
untested and stay on XWayland. Screenshots: `reference/wayland-test-2026-10-17/gog-galaxy-*.png`.

**Game shortcuts next to a running launcher (since 5.0.4):** Wine loads the graphics driver per Windows desktop.
If a program, such as the launcher, is already running in the prefix, the tool starts further programs via `runinprefix`. So they
no longer wait until the launcher exits, as they did with `waitforexitandrun`. With the Wayland driver they get their own
Wine desktop (`explorer.exe /desktop=root,…,wayland start.exe /d <folder> <exe>`) and run there as real
Wayland windows, while the launcher stays on XWayland. Previously such games died immediately (“no driver could be
loaded”). Order: **launch the launcher first, then the game.** If the game starts first instead, a launcher
started afterwards does not come up. Evidence: `reference/wayland-test-2026-10-17/zweiter-desktop.txt`.

Launch options are merged like in Steam: global (settings) + prefix + application. Prefixes for umu/Proton are created via `wineboot -u` (clean return code instead of `umu-run ''`); a creation step counts as
successful as soon as the prefix exists completely. When a
Wine prefix is created, `WINEDLLOVERRIDES=mscoree=d;mshtml=d` is set so that no Mono/Gecko dialogs appear; afterwards
`wineboot --end-session` and `wineserver -w` run so that the process finishes cleanly.

**Shortcuts** are stored as `cachy-app-<id>.desktop` in the start menu (`~/.local/share/applications`) and/or on the
desktop (xdg-user-dir DESKTOP) and launch via `cachyos-dailytool --run <id>` – so the runner, launch options
and environment variables from the tool apply. Registry: `~/.config/cachy-daily-toolkit/registry.json`.

Root actions (fstab, mount, mkfs, mkdir/chown, pacman) run as **one** combined
command via `pkexec`, so the password is requested only once per action. The output
appears in the log window of the respective page and can be copied.

## X3D core assignment (CCD)

Ryzen 9 X3D processors with two core complex dies have the 3D V-Cache on only one CCD (on the 7950X3D: CCD0 with 96 MB L3, CCD1 with
32 MB and a higher clock). On Windows, AMD's **3D V-Cache Performance Optimizer** (game detected → CPPC core preference to the
cache CCD, second CCD “parked” for the game) and the **Application Compatibility Database** (list of known programs with a fixed
assignment) make sure that games end up on the cache CCD. For this, Linux only has the kernel driver `amd_3d_vcache` (since 6.13), which can switch the
core preference but does not detect games – without further action, the scheduler with “CPPC Dynamic Preferred Cores =
Driver” prefers the **frequency** CCD, and games spread their threads across both CCDs (measured with Surviving Mars on the desktop: around three
quarters of the CPU time on the CCD without V-Cache).

The DailyTool replicates both parts (`cachyos_dailytool/x3d.py`, page “X3D cores (CCD)”):

| Component | Implementation |
|---|---|
| Topology | L3 instances from sysfs (`/sys/devices/system/cpu/cpu*/cache/index3`): the CCD with the larger L3 is the cache CCD. Without two L3 caches of different sizes, nothing has any effect. |
| Core preference | `/sys/bus/platform/drivers/amd_x3d_vcache/AMDI0101:00/amd_x3d_mode` = `cache` or `frequency` (kernel driver, BIOS “CPPC Dynamic Preferred Cores” set to Driver/Auto). Writing requires root: “Set up access” creates `/etc/tmpfiles.d/cachyos-dailytool-x3d.conf` via pkexec (the user's group, mode 0664) and applies it immediately; after that the service switches without a password. |
| Detection (“Game Bar”) | Every 1.5 s the service checks the user's own processes: Windows programs under Wine/Proton with an open render device (`/dev/dri/renderD*`, `/dev/nvidia*`), launcher markers in the environment (`SteamAppId`, `UMU_ID`/`GAMEID`, `HEROIC_APP_NAME`, `LUTRIS_GAME_UUID`) and native programs in game folders (Steam libraries, `~/Games`, Heroic, Lutris, custom folders). Excluded: Wine system processes, store clients, installers/runtime components, Steam Runtime/gamescope wrappers. Related processes (same AppID/same prefix) form a session with the entire process tree. |
| Assignment (“parking”) | CPU affinity of all threads of the game tree to the CCD (`sched_setaffinity`, without root). Threads that the game itself pins within the CCD are left untouched; threads placed outside it are brought back with “Enforce assignment”. Steam, launchers, Discord and browsers (catalogue `background`/`launcher`) move to the other CCD in the meantime and back afterwards. |
| Catalogue (“Compatibility Database”) | `cachyos_dailytool/data/x3d_catalog.json`: games (name, .exe, Steam AppID, optionally a fixed mode), launchers, background programs, system processes. AMD's own list is not public – unknown games get the default (cache CCD, like AMD's driver), exceptions are entered as a custom rule (`~/.config/cachy-daily-toolkit/x3d.json`: mode per key `exe:…`, `steam:…`, `comm:…`, `path:…`; exclusions “not a game”). |
| Shortcuts | Applications of the DailyTool have “X3D core assignment” in the edit dialog: Automatic, Cache CCD, Frequency CCD, None. Fixed values take effect immediately at launch (`taskset -c`) and count as a rule for the service. |
| Order | Exclusion (catalogue/“not a game”) → custom rule → shortcut → catalogue game → heuristic with default. Core preference: a game with cache/frequency determines it; without a game the idle default applies (frequency CCD like AMD, cache CCD or unchanged). |

Settings in `config.env`: `X3D_ENABLED`, `X3D_DEFAULT_MODE` (cache/frequency/none), `X3D_IDLE_MODE` (frequency/cache/keep),
`X3D_BACKGROUND`, `X3D_ENFORCE`, `X3D_GAME_DIRS`. State of the service: `~/.local/share/cachy-daily-toolkit/x3d/state.json`,
log `logs/x3d.log`. Command line: `cachyos-dailytool --x3d-status` (topology, core preference, detected games, without intervention),
`--x3d-daemon` (service), `--x3d-dry-run` (log only). Uninstalling removes the service and the user unit; the tmpfiles rule (root) only with `--purge`. A reinstall via
`install.sh` keeps both and restarts the service with the new code.

## Language

The interface is available in German and English. Without a setting it follows the system language (German → German,
everything else → English). Switch it with the **language icon** at the top right of the header bar, left of “Refresh page”:
clicking “Automatic (system language)”, “Deutsch” or “English” saves the choice and restarts the program right away,
keeping the current page open. If a task is running (installation, update, copying), it asks first.
The choice is stored as `UI_LANGUAGE` (auto/de/en) in `~/.config/cachy-daily-toolkit/config.env`; for a single start
`CACHYOS_DAILYTOOL_LANG=en cachyos-dailytool` works as well. Source strings are German and marked with `tr()`;
`scripts/i18n-update.py [language]` updates `cachyos_dailytool/locale/<language>.po` (new strings get an empty
`msgstr` and appear in German until translated). The test `tests/test_i18n.py` checks that the English catalogue is complete.

## Installation

Source code: <https://github.com/Cue-System90/CachyOS-DailyTool>

```bash
git clone https://github.com/Cue-System90/CachyOS-DailyTool.git
cd CachyOS-DailyTool
```

Requirements (mandatory): `python`, `python-gobject`, `gtk4`, `libadwaita`, `polkit`, `xdg-utils`, `rsync`.
For Proton: `proton-cachyos` and/or `umu-launcher`; for icons in shortcuts `python-pefile` and `python-pillow`.
For Wine functions additionally `wine` (or `wine-cachyos`/`wine-staging`), recommended `winetricks`,
`mangohud`, `gamemode`, `gamescope`, `flatpak` (ProtonPlus), `pacman-contrib` (checkupdates),
`nvidia-utils` (nvidia-smi).

**Before every installation, an existing version is removed completely** – whether it is a pacman package, a
user installation under `~/.local` or the old Bash version (v1–v4). Settings are kept.

```bash
bash install.sh                  # user installation into ~/.local, no root; asks about missing packages
bash install.sh --system         # build the pacman package (makepkg) and install it with sudo pacman -U
bash install.sh --purge          # additionally delete settings and logs first (also with --system)
bash install.sh --keep           # remove nothing, just install over the existing version
bash install.sh --deps           # only check/install dependencies
```

Ready-made package (built with `bash install.sh --build`, then located in `dist/`): `sudo pacman -U dist/cachyos-dailytool-*.pkg.tar.zst`
– it automatically replaces an installed `cachy-toolkit` (v5.0.4 and older).

Start: application menu → **CachyOS DailyTool**, or `cachyos-dailytool` in the terminal
(`~/.local/bin/cachyos-dailytool` for a user installation). `cachyos-dailytool FILE.exe` or
`--launch FILE.exe` opens the “Run program” page directly with the file – this is also how
the file manager association works. `cachyos-dailytool --run <id>` launches a registered application without the interface
(this is how the shortcuts work), `--page prefixes` opens the prefix page.

## Uninstallation

Three equivalent ways, all of which remove **all** traces regardless of the installation type:

```bash
bash install.sh --uninstall [--purge] [--prefixes] [--yes]     # from the extracted archive
bash ~/.local/lib/cachyos-dailytool/cachyos_dailytool/uninstall.sh    # for a user installation
bash /usr/share/cachyos-dailytool/uninstall.sh                    # for a package installation
```

Or in the app: Menu → **Uninstall …** (with checkboxes for settings/logs).

The following are removed: the pacman package (`pacman -Rns`, asks via sudo/pkexec), `~/.local/lib/cachyos-dailytool`,
`~/.local/bin/cachyos-dailytool`, the menu entry, icons, the .exe/.msi file association including its entries in
`mimeapps.list`, the old Bash version (`cachy-daily-toolkit.sh` and its menu entry) as well as its
temporary log files; menu and icon caches are updated.

| Option | Effect |
|---|---|
| none | Settings (`~/.config/cachy-daily-toolkit`) and logs are kept |
| `--purge` | Settings and logs are deleted |
| `--prefixes` | additionally the Wine prefix root and prefix backups (paths from the configuration) – asks for confirmation, except with `--yes` |
| `--yes` | no prompts · `--quiet` summary only |

At the end the script checks whether any remnants are left and reports them with exit code 1.

## Files

```
cachyos_dailytool/            Python package
  system.py               backend without GTK (drives, prefixes, launchers, GPU, journal, caches, updates)
  config.py               config.env (compatible with v1–v4)
  uninstall.sh            complete uninstallation (installed along with the app, can also be called from the app)
  compat.py               detection of Proton/Wine/umu (as in the Galaxy port)
  launcher.py             launch plans (umu / Proton / Wine), prefix creation, winetricks, DPI
  launch_options.py       Steam syntax for launch options
  goverlay.py             GOverlay/MangoHud integration and templates
  registry.py             prefixes outside the default folder, settings per prefix, applications
  shortcuts.py            start menu/desktop shortcuts, icon from .exe (pefile + Pillow)
  launchers.py            launcher catalogue (official installers, silent arguments, program paths), download, detection
  x3d.py                  X3D core assignment: topology, amd_x3d_mode, game detection, affinity, background service, systemd --user
  i18n.py                 translation (tr(), language selection, .po reader); locale/en.po English catalogue
  data/x3d_catalog.json   compatibility catalogue (games, launchers, background programs, Wine system processes)
  ui/                     app.py, window.py, widgets.py, dashboard.py, pages_*.py (incl. pages_launchers.py, pages_x3d.py), dialogs.py, style.css
data/                     .desktop file and icons (hicolor)
scripts/                  launch script, package build, i18n-update.py (catalogue maintenance), uninstall.sh (wrapper for cachyos_dailytool/uninstall.sh)
tests/                    unit tests of the backend logic
legacy/                   Bash version v4 (reference)
install.sh · PKGBUILD · LICENSE · README.md (German) · README.en.md (English)
```

## Development

```bash
python3 -m unittest discover -s tests -t .     # backend tests
python3 -m cachyos_dailytool                       # run from the source tree
python3 -m cachyos_dailytool --screenshot FOLDER   # render and capture all pages (Spectacle)
python3 -m cachyos_dailytool --x3d-status          # X3D: topology, core preference, detected games (without intervention)
python3 -m cachyos_dailytool --x3d-dry-run         # X3D service as a dry run (log only)
CACHYOS_DAILYTOOL_LANG=en python3 -m cachyos_dailytool   # interface in English
scripts/i18n-update.py en                          # update the translation catalogue after text changes
```

Configuration: `~/.config/cachy-daily-toolkit/config.env` · Logs: `~/.local/share/cachy-daily-toolkit/logs/`
(toolkit log, Wine output per prefix, ProtonPlus, X3D service).

## Safety notes

- **Formatting** irrevocably deletes all data on the selected partition; the confirmation requires the device name.
- Before every fstab change, `/etc/fstab.bak.<timestamp>` is created (not rotated automatically).
- `pacman -Syu --noconfirm` does not confirm conflicts interactively; in case of key or dependency problems pacman aborts and the log shows the cause – then follow up in the terminal.
- The migration deletes the source folder only after a successful rsync and then creates the symlink. Quit the affected programs beforehand.
- Deleting a prefix also removes save games inside the prefix; create a backup first.

## Background on the drive functions

- The system drive is determined via `findmnt -o SOURCE /` and `lsblk -o PKNAME`; btrfs subvolume suffixes such as `[/@]` are removed (otherwise the system drive would not be recognised on standard CachyOS installations).
- The transport type (NVMe/SATA/USB) is only set on the physical disk, not on the partition; it is assigned via the parent disk so that SATA/ATA partitions appear as well.
- Mount options follow the Arch Wiki: ext4/xfs `defaults,noatime` (TRIM via fstrim.timer), btrfs `compress=zstd:1,ssd,discard=async`, NTFS/vfat/exFAT with `uid`/`gid` (NTFS: `windows_names` only with ntfs3).
- Duplicate paths (`/mnt/x` + `x`) are detected and avoided when the mount point is assembled.
