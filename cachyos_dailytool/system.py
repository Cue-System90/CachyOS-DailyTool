"""Systemfunktionen (Backend, ohne GTK): Laufwerke, Wine-Prefixe, Launcher, GPU, Journal, Caches, Updates.

Alle Funktionen sind so geschrieben, dass sie aus einem Hintergrund-Thread aufgerufen werden können.
Root-Aktionen laufen über pkexec (Fallback sudo) als EIN zusammengesetzter bash-Befehl, damit nur
einmal nach dem Passwort gefragt wird.
"""
from __future__ import annotations

import glob
import json
import os
import re
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .i18n import N_, tr

HOME = Path.home()
DATA_DIR = Path(os.environ.get("XDG_DATA_HOME", HOME / ".local" / "share")) / "cachy-daily-toolkit"
LOG_DIR = DATA_DIR / "logs"

LogFn = Callable[[str], None]


# --------------------------------------------------------------------------- Prozesse
def have(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def run(argv: list[str], timeout: float | None = 60, env: dict[str, str] | None = None,
        cwd: str | Path | None = None) -> subprocess.CompletedProcess:
    full_env = {**os.environ, **env} if env else None
    try:
        return subprocess.run(argv, text=True, capture_output=True, timeout=timeout, env=full_env, cwd=cwd)
    except FileNotFoundError as exc:
        return subprocess.CompletedProcess(argv, 127, "", tr("Programm nicht gefunden: {0}").format(exc.filename))
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(argv, 124, exc.stdout or "", tr("Zeitüberschreitung"))


def stream(argv: list[str], log: LogFn, env: dict[str, str] | None = None, cwd: str | Path | None = None,
           on_start: Callable[[subprocess.Popen], None] | None = None) -> int:
    """Startet argv und reicht jede Ausgabezeile (auch \\r-getrennte Fortschrittszeilen) an log weiter.

    Die Ausgabe läuft über eine Datei statt einer Pipe: Hintergrundprozesse (z. B. wineserver, services.exe), die das
    Ausgabeende erben, würden sonst das Ende der Pipe und damit den Abschluss blockieren.
    """
    import tempfile
    import time as _t
    full_env = (dict(env) if env and "PATH" in env else ({**os.environ, **env} if env else None))
    tmp = tempfile.NamedTemporaryFile(prefix="cachy-stream-", suffix=".log", delete=False)
    try:
        proc = subprocess.Popen(argv, stdout=tmp, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                env=full_env, cwd=cwd)
    except FileNotFoundError as exc:
        tmp.close()
        os.unlink(tmp.name)
        log(tr("Programm nicht gefunden: {0}").format(exc.filename))
        return 127
    if on_start:
        on_start(proc)
    buf = ""
    with open(tmp.name, "r", encoding="utf-8", errors="replace") as fh:
        while True:
            chunk = fh.read()
            if chunk:
                for ch in chunk:
                    if ch in ("\n", "\r"):
                        if buf.strip():
                            log(buf.rstrip())
                        buf = ""
                    else:
                        buf += ch
            elif proc.poll() is not None:
                rest = fh.read()
                for ch in rest:
                    if ch in ("\n", "\r"):
                        if buf.strip():
                            log(buf.rstrip())
                        buf = ""
                    else:
                        buf += ch
                break
            else:
                _t.sleep(0.1)
    if buf.strip():
        log(buf.rstrip())
    tmp.close()
    try:
        os.unlink(tmp.name)
    except OSError:
        pass
    return proc.returncode if proc.returncode is not None else proc.wait()


@dataclass
class StepContext:
    cancelled: Callable[[], bool]
    on_start: Callable[[subprocess.Popen], None] | None = None


def run_step_sequence(steps: list, log: LogFn, env: dict[str, str] | None = None, cwd: str | Path | None = None,
                      on_start: Callable[[subprocess.Popen], None] | None = None,
                      cancelled: Callable[[], bool] = lambda: False) -> int:
    """Schritte nacheinander ausführen. Ein Schritt ist argv, (argv, env), (argv, env, prüfung) oder step(log, ctx) -> int.

    Endet ein Schritt mit Code ≠ 0, bricht die Folge ab – außer seine Prüfung liefert True (Ergebnis liegt vor).
    """
    rc = 0
    for step in steps:
        if cancelled():
            return 130
        if callable(step):
            # Python-Schritt: step(log, ctx) -> Rückgabecode; ctx.cancelled()/ctx.on_start wie bei Befehlen
            ctx = StepContext(cancelled=cancelled, on_start=on_start)
            try:
                rc = int(step(log, ctx) or 0)
            except Exception as exc:
                log(tr("Fehler: {0}").format(exc))
                return 1
            if rc != 0:
                return rc
            continue
        if isinstance(step, tuple):
            argv, step_env = step[0], step[1]
            check = step[2] if len(step) > 2 else None
        else:
            argv, step_env, check = step, None, None
        shown_env = dict(env or {})
        if step_env:
            shown_env.update({k: v for k, v in step_env.items() if os.environ.get(k) != v})
        shown = " ".join(f"{k}={shlex.quote(v)}" for k, v in sorted(shown_env.items())
                         if k in ("WINEPREFIX", "WINEARCH", "PROTONPATH", "GAMEID", "STEAM_COMPAT_DATA_PATH", "WINEDLLOVERRIDES"))
        log("$ " + (shown + " " if shown else "") + " ".join(shlex.quote(a) for a in argv))
        rc = stream(argv, log, env=(step_env if step_env else env), cwd=cwd, on_start=on_start)
        if rc != 0:
            if check is not None and check():
                log(tr("Rückgabecode {0} ignoriert – das Ergebnis ist vollständig vorhanden.").format(rc))
                rc = 0
                continue
            return rc
    return rc


def root_helper() -> str | None:
    for c in ("pkexec", "sudo"):
        if have(c):
            return c
    return None


def root_argv(shell_cmd: str) -> list[str]:
    helper = root_helper()
    if not helper:
        raise RuntimeError(tr("Weder pkexec noch sudo gefunden – Root-Aktionen sind nicht möglich."))
    return [helper, "bash", "-c", shell_cmd]


def detach(argv: list[str], env: dict[str, str] | None = None, cwd: str | Path | None = None,
           logname: str = "detached") -> subprocess.Popen:
    """Startet ein Programm unabhängig vom Tool; Ausgabe landet in einer Logdatei."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logfile = LOG_DIR / f"{re.sub(r'[^A-Za-z0-9_.-]+', '_', logname)}.log"
    fh = open(logfile, "ab")
    full_env = {**os.environ, **env} if env else None
    return subprocess.Popen(argv, stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                            env=full_env, cwd=cwd, start_new_session=True)


def human_bytes(n: float | int | None) -> str:
    if n is None:
        return "–"
    n = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(n) < 1024 or unit == "TiB":
            return f"{n:.0f} {unit}" if unit in ("B", "KiB") else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TiB"


def dir_size(path: str | Path) -> int:
    """Belegter Platz in Bytes (du -sb; Fehler bei einzelnen Unterordnern werden ignoriert)."""
    if not Path(path).exists():
        return 0
    r = run(["du", "-sb", str(path)], timeout=900)
    if r.stdout.strip():
        try:
            return int(r.stdout.split()[0])
        except ValueError:
            pass
    return 0


def open_path(path: str | Path) -> None:
    subprocess.Popen(["xdg-open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)


# --------------------------------------------------------------------------- System
@dataclass
class SystemInfo:
    hostname: str = ""
    distro: str = ""
    kernel: str = ""
    desktop: str = ""
    session: str = ""
    cpu: str = ""
    mem_total: int = 0
    mem_avail: int = 0
    uptime_s: float = 0.0
    chassis: str = ""


def system_info() -> SystemInfo:
    info = SystemInfo(hostname=os.uname().nodename, kernel=os.uname().release,
                      desktop=os.environ.get("XDG_CURRENT_DESKTOP", "–"),
                      session=os.environ.get("XDG_SESSION_TYPE", "–"))
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            if line.startswith("PRETTY_NAME="):
                info.distro = line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                info.cpu = line.split(":", 1)[1].strip()
                break
    except OSError:
        pass
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                info.mem_total = int(line.split()[1]) * 1024
            elif line.startswith("MemAvailable:"):
                info.mem_avail = int(line.split()[1]) * 1024
    except OSError:
        pass
    try:
        info.uptime_s = float(Path("/proc/uptime").read_text().split()[0])
    except OSError:
        pass
    try:
        info.chassis = Path("/sys/class/dmi/id/chassis_type").read_text().strip()
    except OSError:
        pass
    return info


def fmt_uptime(seconds: float) -> str:
    s = int(seconds)
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m = s // 60
    if d:
        return tr("{0} T {1} h {2} min").format(d, h, m)
    if h:
        return f"{h} h {m} min"
    return f"{m} min"


# --------------------------------------------------------------------------- GPU
@dataclass
class GpuStatus:
    vendor: str
    name: str
    temp: float | None = None
    util: float | None = None
    vram_used: int | None = None
    vram_total: int | None = None
    power: float | None = None
    clock_gpu: int | None = None
    clock_mem: int | None = None
    fan: int | None = None
    driver: str = ""


def _to_float(s: str) -> float | None:
    try:
        return float(s.strip())
    except (ValueError, AttributeError):
        return None


def _nvidia_status() -> list[GpuStatus]:
    fields = ["name", "temperature.gpu", "utilization.gpu", "memory.used", "memory.total", "power.draw",
              "clocks.gr", "clocks.mem", "driver_version", "fan.speed"]
    r = run(["nvidia-smi", f"--query-gpu={','.join(fields)}", "--format=csv,noheader,nounits"], timeout=10)
    out: list[GpuStatus] = []
    if r.returncode != 0:
        return out
    for line in r.stdout.strip().splitlines():
        cols = [c.strip() for c in line.split(",")]
        if len(cols) < 9:
            continue
        g = GpuStatus(vendor="NVIDIA", name=cols[0], temp=_to_float(cols[1]), util=_to_float(cols[2]),
                      power=_to_float(cols[5]), driver=cols[8])
        mu, mt = _to_float(cols[3]), _to_float(cols[4])
        g.vram_used = int(mu * 1024 * 1024) if mu is not None else None
        g.vram_total = int(mt * 1024 * 1024) if mt is not None else None
        cg, cm = _to_float(cols[6]), _to_float(cols[7])
        g.clock_gpu = int(cg) if cg is not None else None
        g.clock_mem = int(cm) if cm is not None else None
        fan = _to_float(cols[9]) if len(cols) > 9 else None
        g.fan = int(fan) if fan is not None else None
        out.append(g)
    return out


def _read(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    except OSError:
        return None


def _pci_name(device: Path) -> str:
    try:
        bus = os.path.basename(os.path.realpath(device))
    except OSError:
        return "GPU"
    if have("lspci"):
        r = run(["lspci", "-mm", "-s", bus], timeout=5)
        if r.returncode == 0 and r.stdout.strip():
            try:
                parts = shlex.split(r.stdout.strip())
                # Format: slot class vendor device ... ; Geräte-Name ist Feld 3
                if len(parts) >= 4:
                    return parts[3]
            except ValueError:
                pass
    return "GPU"


def _sysfs_status(skip_nvidia: bool) -> list[GpuStatus]:
    out: list[GpuStatus] = []
    for card in sorted(glob.glob("/sys/class/drm/card[0-9]")) + sorted(glob.glob("/sys/class/drm/card[0-9][0-9]")):
        device = Path(card) / "device"
        vendor = _read(device / "vendor")
        if not vendor:
            continue
        vendor = vendor.lower()
        if vendor == "0x10de":
            if skip_nvidia:
                continue
            g = GpuStatus(vendor="NVIDIA", name=_pci_name(device), driver="nouveau")
            out.append(g)
            continue
        if vendor == "0x1002":
            g = GpuStatus(vendor="AMD", name=_pci_name(device), driver="amdgpu")
            busy = _read(device / "gpu_busy_percent")
            g.util = _to_float(busy) if busy else None
            used, total = _read(device / "mem_info_vram_used"), _read(device / "mem_info_vram_total")
            g.vram_used = int(used) if used and used.isdigit() else None
            g.vram_total = int(total) if total and total.isdigit() else None
            for hw in glob.glob(str(device / "hwmon" / "hwmon*")):
                t = _read(Path(hw) / "temp1_input")
                if t and t.lstrip("-").isdigit():
                    g.temp = int(t) / 1000
                p = _read(Path(hw) / "power1_average") or _read(Path(hw) / "power1_input")
                if p and p.isdigit():
                    g.power = int(p) / 1_000_000
                f = _read(Path(hw) / "fan1_input")
                if f and f.isdigit():
                    g.fan = int(f)
            sclk = _read(device / "pp_dpm_sclk")
            if sclk:
                for line in sclk.splitlines():
                    if line.strip().endswith("*"):
                        m = re.search(r"(\d+)\s*M[Hh]z", line)
                        if m:
                            g.clock_gpu = int(m.group(1))
            mclk = _read(device / "pp_dpm_mclk")
            if mclk:
                for line in mclk.splitlines():
                    if line.strip().endswith("*"):
                        m = re.search(r"(\d+)\s*M[Hh]z", line)
                        if m:
                            g.clock_mem = int(m.group(1))
            out.append(g)
        elif vendor == "0x8086":
            g = GpuStatus(vendor="Intel", name=_pci_name(device), driver="i915/xe")
            for hw in glob.glob(str(device / "hwmon" / "hwmon*")):
                t = _read(Path(hw) / "temp1_input")
                if t and t.lstrip("-").isdigit():
                    g.temp = int(t) / 1000
            cur = _read(device / "gt_cur_freq_mhz") or _read(device / "gt" / "gt0" / "rps_cur_freq_mhz")
            g.clock_gpu = int(cur) if cur and cur.isdigit() else None
            out.append(g)
    return out


def gpu_status() -> list[GpuStatus]:
    nv = _nvidia_status() if have("nvidia-smi") else []
    return nv + _sysfs_status(skip_nvidia=bool(nv))


# --------------------------------------------------------------------------- Laufwerke
TRAN_LABEL = {"nvme": "NVMe", "sata": "SATA", "ata": "SATA/ATA", "usb": "USB", "": "–"}


@dataclass
class Partition:
    name: str
    size: int
    fstype: str
    mountpoint: str
    uuid: str
    label: str
    tran: str
    model: str
    pkname: str
    in_fstab: bool = False
    is_system_disk: bool = False

    @property
    def path(self) -> str:
        return f"/dev/{self.name}"

    @property
    def tran_label(self) -> str:
        return TRAN_LABEL.get(self.tran, self.tran.upper())

    @property
    def temp_mounted(self) -> bool:
        return self.mountpoint.startswith("/run/media/")


def root_source() -> str | None:
    r = run(["findmnt", "-n", "-o", "SOURCE", "/"], timeout=10)
    src = r.stdout.strip().splitlines()[0].strip() if r.stdout.strip() else ""
    # btrfs-Subvolumes: "/dev/nvme1n1p2[/@]" → Suffix in eckigen Klammern entfernen
    src = re.sub(r"\[.*\]$", "", src)
    return src if src.startswith("/dev/") else None


def system_disk() -> str | None:
    """Name der physischen Platte mit der Root-Partition (z. B. nvme1n1)."""
    src = root_source()
    if not src:
        return None
    r = run(["lsblk", "-no", "PKNAME", src], timeout=10)
    parents = [l.strip() for l in r.stdout.splitlines() if l.strip()]
    if parents:
        # Bei LUKS/LVM mehrstufig: oberste physische Platte suchen
        name = parents[0]
        for _ in range(4):
            r2 = run(["lsblk", "-no", "PKNAME", f"/dev/{name}"], timeout=10)
            nxt = [l.strip() for l in r2.stdout.splitlines() if l.strip()]
            if not nxt or nxt[0] == name:
                break
            name = nxt[0]
        return name
    return os.path.basename(src)


def read_fstab() -> str:
    try:
        return Path("/etc/fstab").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def fstab_has_uuid(uuid: str, text: str | None = None) -> bool:
    if not uuid:
        return False
    text = read_fstab() if text is None else text
    return re.search(rf'^\s*UUID="?{re.escape(uuid)}"?\s', text, re.M) is not None


def _walk_blockdevices(nodes: list[dict], disk: dict | None = None):
    for n in nodes:
        d = disk if disk is not None else n
        yield n, d
        if n.get("children"):
            yield from _walk_blockdevices(n["children"], d)


def list_partitions(include_system: bool = False) -> list[Partition]:
    r = run(["lsblk", "-J", "-b", "-o", "NAME,SIZE,FSTYPE,MOUNTPOINT,TYPE,PKNAME,UUID,LABEL,TRAN,MODEL"], timeout=20)
    if r.returncode != 0 or not r.stdout.strip():
        return []
    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError:
        return []
    sysdisk = system_disk()
    fstab = read_fstab()
    out: list[Partition] = []
    for node, disk in _walk_blockdevices(data.get("blockdevices", [])):
        if node.get("type") != "part":
            continue
        tran = (disk.get("tran") or node.get("tran") or "").lower()
        if tran not in ("nvme", "sata", "ata", "usb"):
            continue
        is_sys = bool(sysdisk) and disk.get("name") == sysdisk
        if is_sys and not include_system:
            continue
        uuid = node.get("uuid") or ""
        p = Partition(name=node["name"], size=int(node.get("size") or 0), fstype=node.get("fstype") or "",
                      mountpoint=node.get("mountpoint") or "", uuid=uuid, label=node.get("label") or "",
                      tran=tran, model=(disk.get("model") or "").strip(), pkname=node.get("pkname") or disk.get("name", ""),
                      in_fstab=fstab_has_uuid(uuid, fstab), is_system_disk=is_sys)
        out.append(p)
    return out


def partition_uuid(dev: str) -> str:
    r = run(["blkid", "-s", "UUID", "-o", "value", dev], timeout=10)
    if r.returncode == 0 and r.stdout.strip():
        return r.stdout.strip()
    r = run(["lsblk", "-no", "UUID", dev], timeout=10)
    return r.stdout.strip().splitlines()[0].strip() if r.stdout.strip() else ""


def partition_fstype(dev: str) -> str:
    r = run(["lsblk", "-no", "FSTYPE", dev], timeout=10)
    return r.stdout.strip().splitlines()[0].strip() if r.stdout.strip() else ""


def mount_options_for(fstype: str, uid: int, gid: int, ntfs_driver: str = "ntfs3") -> tuple[str, str, str]:
    """Liefert (Mount-Optionen, dump/pass, fstab-fstype) nach Arch-Wiki-Empfehlungen."""
    fstype = (fstype or "").lower()
    if fstype == "ext4":
        return "defaults,noatime", "0 2", "ext4"
    if fstype == "btrfs":
        return "defaults,noatime,compress=zstd:1,ssd,discard=async", "0 0", "btrfs"
    if fstype == "xfs":
        return "defaults,noatime", "0 2", "xfs"
    if fstype == "ntfs":
        if ntfs_driver == "ntfs-3g":
            return f"defaults,uid={uid},gid={gid},noatime", "0 0", "ntfs-3g"
        return f"defaults,uid={uid},gid={gid},windows_names,noatime", "0 0", "ntfs3"
    if fstype in ("vfat", "exfat"):
        return f"defaults,uid={uid},gid={gid},umask=022,noatime", "0 0", fstype
    return "defaults,noatime", "0 2", fstype or "auto"


def needs_chown(fstab_fstype: str) -> bool:
    return fstab_fstype in ("ext4", "btrfs", "xfs")


def normalize_mountpoint(base: str, name: str) -> str:
    base = (base or "").strip().rstrip("/") or "/"
    name = (name or "").strip().strip("/")
    if not name or os.path.basename(base) == name:
        mp = base
    else:
        mp = f"{base}/{name}"
    while "//" in mp:
        mp = mp.replace("//", "/")
    return mp or "/"


def fstab_line(uuid: str, mountpoint: str, fstype: str, opts: str, dump_pass: str) -> str:
    return f"UUID={uuid}  {mountpoint}  {fstype}  {opts}  {dump_pass}"


def mount_shell_command(dev: str, line: str, mountpoint: str, chown: tuple[int, int] | None,
                        unmount_first: bool) -> str:
    q = shlex.quote
    parts: list[str] = []
    if unmount_first:
        parts.append(f"umount {q(dev)} || true")
    parts += [
        f"mkdir -p {q(mountpoint)}",
        "cp /etc/fstab /etc/fstab.bak.$(date +%Y%m%d%H%M%S)",
        f"printf '%s\\n' {q(line)} >> /etc/fstab",
        "systemctl daemon-reload",
        f"mount {q(mountpoint)}",
    ]
    if chown:
        parts.append(f"chown {chown[0]}:{chown[1]} {q(mountpoint)}")
    return " && ".join(parts)


FORMAT_FS = {"ext4": tr("ext4 (empfohlen für Linux-Daten)"), "btrfs": tr("btrfs (Snapshots, Kompression)"),
             "xfs": tr("xfs (große Dateien)"), "exfat": tr("exFAT (Austausch mit Windows/macOS)")}


def format_shell_command(dev: str, fstype: str, label: str, mounted: bool) -> str:
    q = shlex.quote
    label = re.sub(r"[^A-Za-z0-9_-]", "", label)[:16] or "DATA"
    mk = {
        "ext4": f"mkfs.ext4 -F -L {q(label)} {q(dev)}",
        "btrfs": f"mkfs.btrfs -f -L {q(label)} {q(dev)}",
        "xfs": f"mkfs.xfs -f -L {q(label)} {q(dev)}",
        "exfat": f"mkfs.exfat -n {q(label)} {q(dev)}",
    }[fstype]
    parts = [f"umount {q(dev)}"] if mounted else []
    parts.append(mk)
    return " && ".join(parts)


def create_folder_shell_command(path: str, uid: int, gid: int) -> str:
    q = shlex.quote(path)
    return f"mkdir -p {q} && chown {uid}:{gid} {q} && chmod 755 {q}"


@dataclass
class MountedFs:
    target: str
    source: str
    fstype: str
    size: int
    used: int
    avail: int
    use_pct: int


def mounted_filesystems() -> list[MountedFs]:
    r = run(["findmnt", "-J", "-b", "--real", "-o", "TARGET,SOURCE,FSTYPE,SIZE,USED,AVAIL,USE%"], timeout=15)
    if r.returncode != 0 or not r.stdout.strip():
        return []
    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError:
        return []
    out: list[MountedFs] = []

    def _int(v) -> int:
        try:
            return int(str(v).rstrip("%"))
        except (TypeError, ValueError):
            return 0

    for node, _ in _walk_blockdevices(data.get("filesystems", [])):
        if not node.get("target") or node.get("fstype") in ("squashfs", "fuse.portal"):
            continue
        out.append(MountedFs(target=node["target"], source=node.get("source") or "", fstype=node.get("fstype") or "",
                             size=_int(node.get("size")), used=_int(node.get("used")), avail=_int(node.get("avail")),
                             use_pct=_int(node.get("use%"))))
    seen: set[str] = set()
    uniq = []
    for m in out:
        # btrfs-Subvolumes desselben Geräts ("/dev/x[/@home]") nur einmal zeigen – der erste Mount (meist /) steht für alle
        dev = re.sub(r"\[.*\]$", "", m.source) if m.source.startswith("/dev/") else m.source + "@" + m.target
        if m.target in seen or dev in seen:
            continue
        seen.add(m.target)
        seen.add(dev)
        uniq.append(m)
    return uniq


UNSUITABLE_PREFIX_FS = ("ntfs", "ntfs3", "fuseblk", "exfat", "vfat", "msdos")


def fs_type(path: str | Path) -> str:
    """Dateisystem des nächstgelegenen vorhandenen Elternordners (findmnt -T)."""
    p = Path(path)
    while not p.exists() and p != p.parent:
        p = p.parent
    r = run(["findmnt", "-T", str(p), "-n", "-o", "FSTYPE"], timeout=10)
    return (r.stdout.strip().splitlines() or [""])[0].strip()


def is_mountpoint(path: str) -> bool:
    return run(["findmnt", "-T", path], timeout=10).returncode == 0 if path else False


# --------------------------------------------------------------------------- Wine-Prefixe
@dataclass
class Prefix:
    name: str
    path: Path
    arch: str = "?"
    mtime: float = 0.0
    dpi: int | None = None
    size: int | None = None
    layout: str = "wine"            # wine (drive_c direkt) | proton (pfx/drive_c, STEAM_COMPAT_DATA_PATH)
    external: bool = False          # außerhalb des Standardordners

    @property
    def wine_dir(self) -> Path:
        return self.path / "pfx" if self.layout == "proton" else self.path


def _prefix_arch(path: Path) -> str:
    try:
        with open(path / "system.reg", encoding="utf-8", errors="replace") as fh:
            for _ in range(20):
                line = fh.readline()
                if line.startswith("#arch="):
                    return line.strip().split("=", 1)[1]
    except OSError:
        pass
    return "?"


_DESKTOP_KEY_RE = re.compile(r"^\[Control Panel\\\\Desktop\](?:[ \t]+\d+)?[ \t]*(?:\n|\Z)", re.M | re.I)
_LOGPIXELS_RE = re.compile(r'^"LogPixels"=dword:([0-9a-fA-F]{1,8})[ \t]*$', re.M | re.I)


def _reg_section(text: str, header: re.Pattern) -> tuple[int, int] | None:
    """(Anfang, Ende) der Wertzeilen eines Schlüssels in einer Wine-.reg-Datei – ohne Kopfzeile."""
    m = header.search(text)
    if not m:
        return None
    nxt = re.compile(r"^\[", re.M).search(text, m.end())
    return m.end(), (nxt.start() if nxt else len(text))


def prefix_dpi(wine_dir: Path) -> int | None:
    """Windows-DPI (HKCU\\Control Panel\\Desktop\\LogPixels); None = nie gesetzt, Wine nimmt dann 96.

    Nur dieser Schlüssel zählt – unter Software\\Wine\\Fonts legt Wine einen gleichnamigen Wert (96) für den Schriftcache an."""
    try:
        text = (Path(wine_dir) / "user.reg").read_text(encoding="latin-1")
    except OSError:
        return None
    sec = _reg_section(text, _DESKTOP_KEY_RE)
    m = _LOGPIXELS_RE.search(text, *sec) if sec else None
    return int(m.group(1), 16) if m else None


def write_prefix_dpi(wine_dir: Path, dpi: int) -> None:
    """LogPixels direkt in user.reg schreiben. Nur bei beendetem wineserver des Prefix aufrufen – er hält die Registry
    im Speicher und überschreibt die Datei beim Beenden (siehe prefix_wineserver_pids)."""
    reg = Path(wine_dir) / "user.reg"
    text = reg.read_text(encoding="latin-1")          # Wine schreibt ASCII mit Escapes; latin-1 erhält jedes Byte
    line = f'"LogPixels"=dword:{int(dpi):08x}'
    sec = _reg_section(text, _DESKTOP_KEY_RE)
    if sec is None:
        text += ("" if text.endswith("\n") else "\n") + f"\n[Control Panel\\\\Desktop] {int(time.time())}\n{line}\n"
    else:
        start, end = sec
        body = text[start:end]
        if _LOGPIXELS_RE.search(body):
            body = _LOGPIXELS_RE.sub(line, body, count=1)
        else:
            pos = 0                                    # nach den #time=/#class=-Zeilen des Schlüssels einfügen
            for ln in body.splitlines(keepends=True):
                if not ln.startswith("#"):
                    break
                pos += len(ln)
            body = body[:pos] + line + "\n" + body[pos:]
        if not text[:start].endswith("\n"):         # Kopfzeile ganz am Dateiende ohne Zeilenumbruch
            body = "\n" + body
        text = text[:start] + body + text[end:]
    tmp = reg.with_name(reg.name + ".cachy-tmp")
    tmp.write_text(text, encoding="latin-1")
    shutil.copymode(reg, tmp)
    os.replace(tmp, reg)


def prefix_wineserver_pids(wine_dir: Path) -> list[int]:
    """Laufende wineserver dieses Prefix (auch aus Proton/umu-Containern): wineserver arbeitet im Ordner
    /tmp/.wine-<uid>/server-<st_dev>-<st_ino> des Prefix-Verzeichnisses."""
    try:
        st = Path(wine_dir).stat()
        pids = [d for d in os.listdir("/proc") if d.isdigit()]
    except OSError:
        return []
    tag = f"server-{st.st_dev:x}-{st.st_ino:x}"
    found: list[int] = []
    for pid in pids:
        try:
            if not Path(f"/proc/{pid}/comm").read_text().startswith("wineserver"):
                continue
            cwd = os.readlink(f"/proc/{pid}/cwd")
        except OSError:
            continue
        if os.path.basename(cwd.removesuffix(" (deleted)").rstrip("/")) == tag:
            found.append(int(pid))
    return found


def prefix_layout(path: Path) -> str | None:
    """'wine', 'proton' oder None, wenn kein Prefix."""
    if not path.is_dir():
        return None
    if (path / "drive_c").is_dir() or (path / "system.reg").is_file():
        return "wine"
    if (path / "pfx" / "drive_c").is_dir() or (path / "pfx" / "system.reg").is_file():
        return "proton"
    return None


def is_prefix(path: Path) -> bool:
    return prefix_layout(path) is not None


def describe_prefix(path: Path, external: bool = False) -> Prefix | None:
    layout = prefix_layout(path)
    if layout is None:
        return None
    wine_dir = path / "pfx" if layout == "proton" else path
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    return Prefix(name=path.name, path=path, arch=_prefix_arch(wine_dir), mtime=mtime, dpi=prefix_dpi(wine_dir),
                  layout=layout, external=external)


def list_prefixes(root: Path, extra: list[Path] | None = None, locations: list[Path] | None = None) -> list[Prefix]:
    """Prefixe im Standardordner, in allen weiteren Speicherorten und einzeln eingetragene Pfade – ohne Duplikate.

    Prefixe außerhalb des Standardordners sind als external markiert.
    """
    out: list[Prefix] = []
    seen: set[str] = set()

    def scan(base: Path, external: bool) -> None:
        try:
            entries = sorted(base.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            return
        for d in entries:
            key = str(d.resolve()) if d.exists() else str(d)
            if key in seen:
                continue
            p = describe_prefix(d, external=external)
            if p:
                seen.add(key)
                out.append(p)

    scan(root, False)
    root_key = str(root.resolve()) if root.exists() else str(root)
    for loc in locations or []:
        if (str(loc.resolve()) if loc.exists() else str(loc)) != root_key:
            scan(loc, True)
    for e in extra or []:
        key = str(e.resolve()) if e.exists() else str(e)
        if key in seen:
            continue
        p = describe_prefix(e, external=True)
        if p:
            seen.add(key)
            out.append(p)
    return out


def prefix_name_error(name: str) -> str | None:
    """Fehlermeldung für einen ungültigen Prefix-Ordnernamen, sonst None."""
    n = (name or "").strip()
    if not n:
        return tr("Bitte einen Namen eingeben.")
    if n in (".", "..") or n.startswith(".") or "/" in n or "\0" in n:
        return tr("„{0}“ ist als Ordnername nicht erlaubt (kein Punkt am Anfang, kein „/“).").format(n)
    return None


def safe_to_delete_prefix(prefix: Path, protected: list[Path]) -> str | None:
    """Fehlermeldung, wenn prefix nicht gelöscht werden darf (kein Prefix, Speicherort, Home, Wurzel …), sonst None."""
    try:
        p = prefix.resolve()
    except OSError:
        return tr("{0} ist nicht erreichbar.").format(prefix)
    if not is_prefix(p):
        return tr("{0} ist kein Wine-Prefix – Löschen verweigert.").format(p)
    guarded = {Path("/").resolve(), Path.home().resolve()}
    for q in protected:
        try:
            guarded.add(Path(q).resolve())
        except OSError:
            pass
    for g in guarded:
        if p == g or g.is_relative_to(p):
            return tr("{0} ist ein Speicherort oder enthält einen – Löschen verweigert.").format(p)
    return None


def prefix_pids(prefix: Path) -> list[int]:
    """PIDs eigener Prozesse, deren WINEPREFIX bzw. STEAM_COMPAT_DATA_PATH auf diesen Prefix zeigt (Wine, Proton, umu)."""
    try:
        target = str(prefix.resolve())
    except OSError:
        return []
    keys = (b"WINEPREFIX=", b"STEAM_COMPAT_DATA_PATH=")
    out: list[int] = []
    me = os.getpid()
    for d in Path("/proc").iterdir():
        if not d.name.isdigit() or int(d.name) == me:
            continue
        try:
            env = (d / "environ").read_bytes()
        except OSError:
            continue
        for item in env.split(b"\0"):
            for k in keys:
                if item.startswith(k):
                    val = item[len(k):].decode("utf-8", "replace").rstrip("/")
                    try:
                        if val and (str(Path(val).resolve()) in (target, str(Path(target) / "pfx"))):
                            out.append(int(d.name))
                    except OSError:
                        pass
    return sorted(set(out))


def kill_prefix_processes(prefix: Path, log: LogFn | None = None, timeout: float = 8.0) -> int:
    """Alle Prozesse des Prefix beenden (SIGTERM, dann SIGKILL). Liefert die Anzahl betroffener Prozesse."""
    import signal
    import time as _t
    pids = prefix_pids(prefix)
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    end = _t.monotonic() + timeout
    while _t.monotonic() < end and any(Path(f"/proc/{p}").exists() for p in pids):
        _t.sleep(0.3)
    for pid in pids:
        if Path(f"/proc/{pid}").exists():
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
    if pids and log:
        log(tr("{0} laufende Prozesse des Prefix beendet.").format(len(pids)))
    return len(pids)


def move_dir(src: Path, dest: Path, log: LogFn) -> Path:
    """Verzeichnis verschieben (rename, sonst rsync + löschen). dest ist der neue Pfad des Verzeichnisses."""
    if dest.exists():
        raise RuntimeError(tr("Ziel existiert bereits: {0}").format(dest))
    if not src.exists():
        raise RuntimeError(tr("Quelle fehlt: {0}").format(src))
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.rename(src, dest)
        log(tr("Verschoben: {0} → {1}").format(src, dest))
        return dest
    except OSError:
        pass
    log(tr("rsync {0}/ → {1}/ (anderes Dateisystem)").format(src, dest))
    rc = stream(["rsync", "-a", "--info=progress2", "--no-inc-recursive", f"{src}/", f"{dest}/"], log)
    if rc != 0:
        raise RuntimeError(tr("rsync fehlgeschlagen (Code {0}); Quelle blieb unverändert").format(rc))
    shutil.rmtree(src)
    log(tr("Quelle entfernt: {0}").format(src))
    return dest


def prefix_env(path: Path, arch: str | None = None) -> dict[str, str]:
    env = {"WINEPREFIX": str(path)}
    if arch in ("win32", "win64"):
        env["WINEARCH"] = arch
    return env


def create_prefix_argv(path: Path, arch: str) -> tuple[list[str], dict[str, str]]:
    argv = ["wineboot", "--init"] if have("wineboot") else ["wine", "wineboot", "--init"]
    return argv, prefix_env(path, arch)


def set_prefix_dpi(path: Path, dpi: int) -> subprocess.CompletedProcess:
    return run(["wine", "reg", "add", r"HKCU\Control Panel\Desktop", "/v", "LogPixels", "/t", "REG_DWORD",
                "/d", str(int(dpi)), "/f"], env=prefix_env(path), timeout=180)


def build_launch_argv(exe: str, launch_options: str, runner: list[str] | None = None) -> list[str]:
    """Steam-Syntax: 'mangohud %command%' → mangohud wine exe.exe. Ohne %command% werden die Optionen vorangestellt."""
    base = (runner or ["wine"]) + [exe]
    opts = (launch_options or "").strip()
    if not opts:
        return base
    try:
        tokens = shlex.split(opts)
    except ValueError:
        tokens = opts.split()
    if "%command%" in tokens:
        out: list[str] = []
        for t in tokens:
            if t == "%command%":
                out.extend(base)
            else:
                out.append(t)
        return out
    return tokens + base


def launch_exe(exe: str, prefix: Path, mode: str = "normal", launch_options: str = "") -> subprocess.Popen:
    env = prefix_env(prefix)
    cwd = str(Path(exe).parent) if Path(exe).exists() else None
    if mode == "winecfg":
        return detach(["winecfg"] if have("winecfg") else ["wine", "winecfg"], env=env, logname=f"winecfg-{prefix.name}")
    if mode == "winetricks":
        return detach(["winetricks", "--gui"], env=env, logname=f"winetricks-{prefix.name}")
    if mode == "explorer":
        open_path(prefix / "drive_c")
        return subprocess.Popen(["true"])
    if mode == "msi" or exe.lower().endswith(".msi"):
        argv = build_launch_argv(exe, launch_options, runner=["wine", "msiexec", "/i"])
    else:
        argv = build_launch_argv(exe, launch_options)
    # Umgebungsvariablen am Anfang (FOO=bar mangohud %command%) wie bei Steam auswerten
    while argv and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", argv[0]):
        k, v = argv.pop(0).split("=", 1)
        env[k] = v
    return detach(argv, env=env, cwd=cwd, logname=f"wine-{prefix.name}")


@dataclass
class Backup:
    name: str
    path: Path
    size: int
    mtime: float


def list_backups(backup_dir: Path) -> list[Backup]:
    out: list[Backup] = []
    try:
        for p in sorted(backup_dir.glob("*.tar.gz"), key=lambda p: p.stat().st_mtime, reverse=True):
            st = p.stat()
            out.append(Backup(name=p.name, path=p, size=st.st_size, mtime=st.st_mtime))
    except OSError:
        pass
    return out


def backup_argv(root: Path, name: str, archive: Path) -> list[str]:
    # -h: Symlinks dereferenzieren, damit Prefixe mit relativen Links wiederherstellbar bleiben
    return ["tar", "-czhf", str(archive), "-C", str(root), name]


def restore_argv(archive: Path, root: Path) -> list[str]:
    return ["tar", "-xzf", str(archive), "-C", str(root)]


def backup_archive_name(name: str) -> str:
    return f"{name}-{time.strftime('%Y%m%d-%H%M%S')}.tar.gz"


# --------------------------------------------------------------------------- .exe-Verknüpfung
EXE_MIME = ("application/x-ms-dos-executable", "application/x-msi", "application/vnd.microsoft.portable-executable")
HANDLER_DESKTOP = "cachy-daily-toolkit-launch.desktop"


def exe_handler_default() -> str:
    r = run(["xdg-mime", "query", "default", EXE_MIME[0]], timeout=10)
    return r.stdout.strip()


def register_exe_handler(launcher_cmd: str) -> str:
    apps = Path(os.environ.get("XDG_DATA_HOME", HOME / ".local" / "share")) / "applications"
    apps.mkdir(parents=True, exist_ok=True)
    desktop = apps / HANDLER_DESKTOP
    name = tr("Mit Wine-Prefix starten")
    comment = tr("Wählt oder erstellt einen Wine-Prefix vor dem Start einer .exe/.msi")
    desktop.write_text(
        f"[Desktop Entry]\nType=Application\nName={name}\n"
        f"Comment={comment}\n"
        f"Exec={launcher_cmd} --launch %f\nTryExec=wine\nIcon=wine\nTerminal=false\n"
        f"MimeType={';'.join(EXE_MIME)};\nCategories=Utility;\nNoDisplay=true\n", encoding="utf-8")
    run(["update-desktop-database", str(apps)], timeout=30)
    for mime in EXE_MIME:
        run(["xdg-mime", "default", HANDLER_DESKTOP, mime], timeout=10)
    return str(desktop)


# --------------------------------------------------------------------------- Launcher & Migration
@dataclass
class Launcher:
    name: str
    kind: str
    path: Path
    is_symlink: bool = False
    target: str = ""
    size: int | None = None

    @property
    def key(self) -> str:
        return f"{self.name}|{self.path}"


def _first_existing(cands: list[tuple[str, Path]]) -> tuple[str, Path] | None:
    for kind, p in cands:
        if p.exists() or p.is_symlink():
            return kind, p
    return None


def _find_name(root: Path, needles: tuple[str, ...], maxdepth: int) -> bool:
    needles_l = tuple(n.lower() for n in needles)
    root_depth = len(root.parts)
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            depth = len(Path(dirpath).parts) - root_depth
            for n in dirnames + filenames:
                nl = n.lower()
                if any(x in nl for x in needles_l):
                    return True
            if depth >= maxdepth:
                dirnames[:] = []
    except OSError:
        pass
    return False


PREFIX_LAUNCHERS: list[tuple[str, tuple[str, ...], int]] = [
    ("Ubisoft Connect", ("ubisoftconnect", "ubisoft game launcher"), 4),
    ("GOG Galaxy", ("galaxyclient",), 4),
    ("Rockstar Games Launcher", ("rockstar games",), 5),
    ("Xbox App", ("xboxapp", "xbox"), 4),
    ("Epic Games Store", ("epicgameslauncher",), 4),
    ("Battle.net", ("battlenet", "battle.net"), 4),
    ("EA App / Origin", ("eaapp", "ea desktop", "origin"), 4),
]


def scan_launchers(prefix_root: Path, home: Path = HOME) -> list[Launcher]:
    found: list[Launcher] = []

    def add(name: str, kind: str, p: Path) -> None:
        is_link = p.is_symlink()
        target = os.path.realpath(p) if is_link else ""
        found.append(Launcher(name=name, kind=kind, path=p, is_symlink=is_link, target=target))

    hit = _first_existing([(tr("Nativ"), home / ".local/share/Steam"), (tr("Nativ (~/.steam/steam)"), home / ".steam/steam"),
                           ("Flatpak", home / ".var/app/com.valvesoftware.Steam/data/Steam")])
    if hit:
        add("Steam", *hit)
    hit = _first_existing([(tr("Nativ"), home / ".local/share/lutris"), ("Flatpak", home / ".var/app/net.lutris.Lutris/data/lutris")])
    if hit:
        add("Lutris", *hit)
    hit = _first_existing([(tr("Nativ"), home / ".config/heroic"), ("Flatpak", home / ".var/app/com.heroicgameslauncher.hgl/config/heroic")])
    if hit:
        add("Heroic (Epic/GOG/Amazon)", *hit)
    hit = _first_existing([(tr("Nativ"), home / ".local/share/bottles"), ("Flatpak", home / ".var/app/com.usebottles.bottles/data/bottles")])
    if hit:
        add("Bottles", *hit)
    hit = _first_existing([(tr("Nativ"), home / ".local/share/Steam/compatibilitytools.d"),
                           (tr("Nativ"), home / ".steam/root/compatibilitytools.d")])
    if hit:
        add(tr("Steam Compat-Tools (Proton-GE u. a.)"), *hit)

    roots: list[Path] = []
    for r in (prefix_root, home / "Games", home / ".wine"):
        if r.is_dir() and r not in roots:
            roots.append(r)
    seen: set[Path] = set()
    for r in roots:
        drive_cs: list[Path] = []
        if (r / "drive_c").is_dir():
            drive_cs.append(r / "drive_c")
        r_depth = len(r.parts)
        try:
            for dirpath, dirnames, _ in os.walk(r):
                depth = len(Path(dirpath).parts) - r_depth
                if "drive_c" in dirnames:
                    drive_cs.append(Path(dirpath) / "drive_c")
                    dirnames.remove("drive_c")
                if depth >= 2:
                    dirnames[:] = []
        except OSError:
            continue
        for dc in drive_cs:
            pdir = dc.parent
            if pdir in seen:
                continue
            seen.add(pdir)
            for name, needles, depth in PREFIX_LAUNCHERS:
                if _find_name(dc, needles, depth):
                    add(name, tr("Wine-Prefix: {0}").format(pdir.name), pdir)
    return found


def migrate_dir(src: Path, dest_root: Path, log: LogFn) -> Path:
    """rsync nach dest_root/<name>, Quelle entfernen, Symlink setzen. Wirft RuntimeError bei Fehlern."""
    dest = dest_root / src.name
    if src.is_symlink():
        raise RuntimeError(tr("{0} ist bereits ein Symlink → {1}").format(src, os.path.realpath(src)))
    if not src.exists():
        raise RuntimeError(tr("{0} existiert nicht").format(src))
    if dest.resolve() == src.resolve():
        raise RuntimeError(tr("Ziel und Quelle sind identisch"))
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"rsync {src}/ → {dest}/")
    rc = stream(["rsync", "-a", "--info=progress2", "--no-inc-recursive", f"{src}/", f"{dest}/"], log)
    if rc != 0:
        raise RuntimeError(tr("rsync fehlgeschlagen (Code {0}); Quelle blieb unverändert").format(rc))
    log(tr("Entferne Quelle {0} …").format(src))
    shutil.rmtree(src)
    os.symlink(dest, src)
    log(tr("Symlink gesetzt: {0} → {1}").format(src, dest))
    return dest


# --------------------------------------------------------------------------- ProtonPlus & Compat-Tools
PROTONPLUS_FLATPAK = "com.vysp3r.ProtonPlus"


def protonplus_status() -> str | None:
    if have("protonplus"):
        return "native"
    if have("flatpak") and run(["flatpak", "info", PROTONPLUS_FLATPAK], timeout=20).returncode == 0:
        return "flatpak"
    return None


def protonplus_launch(status: str) -> subprocess.Popen:
    if status == "native":
        return detach(["protonplus"], logname="protonplus")
    return detach(["flatpak", "run", PROTONPLUS_FLATPAK], logname="protonplus")


def protonplus_install_steps() -> list[list[str]]:
    steps: list[list[str]] = []
    r = run(["flatpak", "remotes", "--user", "--columns=name"], timeout=20)
    if "flathub" not in r.stdout:
        steps.append(["flatpak", "remote-add", "--user", "--if-not-exists", "flathub",
                      "https://flathub.org/repo/flathub.flatpakrepo"])
    steps.append(["flatpak", "install", "--user", "-y", "--or-update", "flathub", PROTONPLUS_FLATPAK])
    return steps


def compat_tool_dirs(prefix_root: Path, home: Path = HOME) -> list[tuple[str, Path]]:
    return [
        (tr("Steam – eigene Compat-Tools"), home / ".local/share/Steam/compatibilitytools.d"),
        (tr("Steam (Flatpak) – Compat-Tools"), home / ".var/app/com.valvesoftware.Steam/data/Steam/compatibilitytools.d"),
        (tr("System – proton-cachyos u. a."), Path("/usr/share/steam/compatibilitytools.d")),
        ("Heroic – Proton", home / ".config/heroic/tools/proton"),
        ("Heroic – Wine", home / ".config/heroic/tools/wine"),
        (tr("Lutris – Wine-Runner"), home / ".local/share/lutris/runners/wine"),
        (tr("Bottles – Runner"), home / ".local/share/bottles/runners"),
        ("umu – Proton", home / ".local/share/umu/compatibilitytools"),
        (tr("Wine-Prefix-Root"), prefix_root),
    ]


@dataclass
class CompatTool:
    name: str
    location: str
    path: Path


def list_compat_tools(prefix_root: Path, home: Path = HOME) -> list[CompatTool]:
    out: list[CompatTool] = []
    for label, d in compat_tool_dirs(prefix_root, home):
        if label == tr("Wine-Prefix-Root") or not d.is_dir():
            continue
        try:
            for sub in sorted(d.iterdir(), key=lambda p: p.name.lower()):
                if sub.is_dir():
                    out.append(CompatTool(name=sub.name, location=label, path=sub))
        except OSError:
            pass
    return out


# --------------------------------------------------------------------------- Shader-Caches
@dataclass
class CacheEntry:
    key: str
    title: str
    description: str
    dirs: list[Path] = field(default_factory=list)
    files: list[Path] = field(default_factory=list)
    size: int = 0

    @property
    def exists(self) -> bool:
        return any(d.exists() for d in self.dirs) or bool(self.files)


def _dxvk_files(roots: list[Path]) -> list[Path]:
    out: list[Path] = []
    for r in roots:
        if not r.is_dir():
            continue
        try:
            for dirpath, dirnames, filenames in os.walk(r):
                dirnames[:] = [d for d in dirnames if not d.startswith(".")]
                for f in filenames:
                    if f.endswith((".dxvk-cache", ".vkd3d-proton.cache", ".vkd3d-proton.cache.write")):
                        out.append(Path(dirpath) / f)
        except OSError:
            continue
    return out


def shader_caches(prefix_root: Path, games_dir: Path, home: Path = HOME, with_sizes: bool = True) -> list[CacheEntry]:
    entries = [
        CacheEntry("steam", tr("Steam Shader-Cache"), tr("steamapps/shadercache (nativ und Flatpak)"),
                   dirs=[p for p in (home / ".local/share/Steam/steamapps/shadercache",
                                     home / ".var/app/com.valvesoftware.Steam/data/Steam/steamapps/shadercache") if p.is_dir()]),
        CacheEntry("dxvk", tr("DXVK / VKD3D-Caches"),
                   tr("*.dxvk-cache und *.vkd3d-proton.cache unter {0} und {1}").format(prefix_root, games_dir),
                   files=_dxvk_files([prefix_root, games_dir])),
        CacheEntry("mesa", tr("Mesa Shader-Cache"), "~/.cache/mesa_shader_cache*",
                   dirs=[Path(p) for p in glob.glob(str(home / ".cache/mesa_shader_cache*"))]),
        CacheEntry("nvidia", "NVIDIA GLCache", tr("~/.cache/nvidia/GLCache und ~/.nv/GLCache"),
                   dirs=[p for p in (home / ".cache/nvidia/GLCache", home / ".nv/GLCache") if p.is_dir()]),
    ]
    if with_sizes:
        for e in entries:
            e.size = sum(dir_size(d) for d in e.dirs)
            for f in e.files:
                try:
                    e.size += f.stat().st_size
                except OSError:
                    pass
    return entries


def clear_cache(entry: CacheEntry) -> tuple[int, int]:
    """Leert den Inhalt der Cache-Ordner (Ordner selbst bleibt) bzw. löscht Cache-Dateien. Liefert (Bytes, Anzahl)."""
    freed, count = 0, 0
    for d in entry.dirs:
        if not d.is_dir():
            continue
        for child in d.iterdir():
            try:
                freed += dir_size(child) if child.is_dir() and not child.is_symlink() else child.stat().st_size
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child)
                else:
                    child.unlink()
                count += 1
            except OSError:
                pass
    for f in entry.files:
        try:
            freed += f.stat().st_size
            f.unlink()
            count += 1
        except OSError:
            pass
    return freed, count


# --------------------------------------------------------------------------- Speicherplatz
def gaming_dirs(prefix_root: Path, games_dir: Path, backup_dir: Path, home: Path = HOME) -> list[tuple[str, Path]]:
    cands = [
        ("Steam", home / ".local/share/Steam"),
        ("Steam (Flatpak)", home / ".var/app/com.valvesoftware.Steam"),
        ("Lutris", home / ".local/share/lutris"),
        ("Lutris (Flatpak)", home / ".var/app/net.lutris.Lutris"),
        ("Heroic", home / ".config/heroic"),
        ("Heroic (Flatpak)", home / ".var/app/com.heroicgameslauncher.hgl"),
        ("Bottles", home / ".local/share/bottles"),
        ("Bottles (Flatpak)", home / ".var/app/com.usebottles.bottles"),
        (tr("Wine-Prefix-Root"), prefix_root),
        (tr("Gaming-Zielordner"), games_dir),
        (tr("Prefix-Backups"), backup_dir),
    ]
    out: list[tuple[str, Path]] = []
    seen: set[Path] = set()
    for label, p in cands:
        if p in seen:
            continue
        seen.add(p)
        out.append((label, p))
    return out


# --------------------------------------------------------------------------- Journal
JOURNAL_PRESETS: dict[str, tuple[str, list[str], str | None]] = {
    "boot": (tr("Aktueller Boot – alles"), [], None),
    "errors": (tr("Nur Warnungen und Fehler"), ["-p", "warning"], None),
    "kernel": (tr("Kernel-Meldungen (dmesg)"), ["-k"], None),
    "nvidia": ("NVIDIA / DRM / GSP", [], r"nvidia|nvrm|\bdrm\b|gsp"),
    "amdgpu": ("AMD GPU", [], r"amdgpu|radeon"),
    "gaming": ("gamescope / Steam / Vulkan / Proton", [], r"gamescope|steam|pressure-vessel|vulkan|proton|wine"),
    "wayland": ("Wayland / KWin / Plasma", [], r"kwin|plasma|wayland|xwayland"),
}


def journal(preset: str, pattern: str = "", limit: int = 4000, previous_boot: bool = False) -> str:
    title, extra, preset_re = JOURNAL_PRESETS.get(preset, JOURNAL_PRESETS["boot"])
    argv = ["journalctl", "-b", "-1" if previous_boot else "0", "--no-pager", "-o", "short", *extra]
    r = run(argv, timeout=120)
    if r.returncode != 0 and not r.stdout:
        return r.stderr.strip() or tr("journalctl lieferte keine Daten.")
    lines = r.stdout.splitlines()
    regexes = [re.compile(x, re.I) for x in (preset_re, pattern.strip()) if x]
    if regexes:
        lines = [l for l in lines if all(rx.search(l) for rx in regexes)]
    if not lines:
        return tr("Keine passenden Einträge gefunden.")
    if len(lines) > limit:
        lines = [tr("… {0} ältere Zeilen ausgeblendet …").format(len(lines) - limit)] + lines[-limit:]
    return "\n".join(lines)


# --------------------------------------------------------------------------- Updates
_updates_lock = __import__("threading").Lock()
_updates_cache: tuple[float, tuple[list[str] | None, str]] | None = None


def pending_updates(max_age: float = 60.0) -> tuple[list[str] | None, str]:
    """(Liste der Updates, Hinweis). None = konnte nicht ermittelt werden.

    Serialisiert parallele Aufrufe (checkupdates sperrt seine temporäre Datenbank) und cached das Ergebnis kurz,
    damit Übersicht und Seitenleiste nicht doppelt synchronisieren.
    """
    global _updates_cache
    with _updates_lock:
        if _updates_cache and time.time() - _updates_cache[0] < max_age:
            return _updates_cache[1]
        res = _pending_updates_uncached()
        _updates_cache = (time.time(), res)
        return res


def invalidate_updates_cache() -> None:
    global _updates_cache
    _updates_cache = None


def _pending_updates_uncached() -> tuple[list[str] | None, str]:
    if have("checkupdates"):
        r = run(["checkupdates"], timeout=180)
        if r.returncode == 0:
            return [l for l in r.stdout.splitlines() if l.strip()], "checkupdates"
        if r.returncode == 2:
            return [], "checkupdates"
        return None, r.stderr.strip() or tr("checkupdates fehlgeschlagen")
    r = run(["pacman", "-Qu"], timeout=60)
    if r.returncode == 0:
        return [l for l in r.stdout.splitlines() if l.strip()], tr("pacman -Qu (lokale Datenbank, evtl. veraltet)")
    if r.returncode == 1 and not r.stdout.strip():
        return [], tr("pacman -Qu (lokale Datenbank, evtl. veraltet)")
    return None, r.stderr.strip() or tr("pacman -Qu fehlgeschlagen")


def update_argv() -> list[str]:
    return root_argv("pacman -Syu --noconfirm")


def dkms_status() -> str:
    if not have("dkms"):
        # Kennung: pages_system prüft „nicht installiert“ im Text – Übersetzung an der Anzeigestelle (N_)
        return N_("dkms ist nicht installiert (bei CachyOS-Kerneln mit fertigen Modulpaketen normal).")
    r = run(["dkms", "status"], timeout=60)
    return r.stdout.strip() or r.stderr.strip() or tr("Keine DKMS-Module registriert.")


def kernel_needs_reboot() -> bool:
    return not Path("/usr/lib/modules", os.uname().release).exists()


def nvidia_module_info() -> str:
    v = _read(Path("/sys/module/nvidia/version"))
    if v:
        return v
    r = run(["modinfo", "-F", "version", "nvidia"], timeout=10)
    return r.stdout.strip()


# --------------------------------------------------------------------------- Desktop-Skalierung
def desktop_backend() -> str:
    d = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    if "kde" in d or have("kwriteconfig6"):
        return "kde"
    if "gnome" in d or have("gsettings"):
        return "gnome"
    return "other"


@dataclass
class ScreenScale:
    name: str
    scale: float
    width: int = 0
    height: int = 0

    @property
    def percent(self) -> int:
        return int(round(self.scale * 100))

    @property
    def label(self) -> str:
        return f"{self.name} ({self.width}×{self.height})" if self.width and self.height else self.name


def kde_wayland() -> bool:
    """Plasma-Wayland-Sitzung: Skalierung gilt je Bildschirm (kscreen-doctor), kdeglobals ist dort wirkungslos."""
    return desktop_backend() == "kde" and os.environ.get("XDG_SESSION_TYPE") == "wayland" and have("kscreen-doctor")


def parse_kscreen_json(text: str) -> list[ScreenScale]:
    """Aktive Bildschirme aus `kscreen-doctor -j`, Hauptbildschirm (priority 1) zuerst."""
    try:
        data = json.loads(text or "")
    except ValueError:
        return []
    found: list[tuple[int, ScreenScale]] = []
    for o in (data.get("outputs") or []) if isinstance(data, dict) else []:
        scale = o.get("scale")
        if not (o.get("enabled") and o.get("connected")) or not isinstance(scale, (int, float)) or scale <= 0:
            continue
        mode = next((m for m in o.get("modes") or [] if str(m.get("id")) == str(o.get("currentModeId"))), {})
        size = mode.get("size") or o.get("size") or {}
        prio = o.get("priority") or (1 if o.get("primary") else 99)
        found.append((prio, ScreenScale(str(o.get("name") or o.get("id")), float(scale),
                                        int(size.get("width") or 0), int(size.get("height") or 0))))
    return [s for _prio, s in sorted(found, key=lambda t: t[0])]


def screen_scales() -> list[ScreenScale]:
    """Skalierung je Bildschirm unter Plasma Wayland; leer in anderen Sitzungen."""
    if not kde_wayland():
        return []
    return parse_kscreen_json(run(["kscreen-doctor", "-j"], timeout=10).stdout)


def desktop_scale_get() -> tuple[int | None, str]:
    be = desktop_backend()
    if be == "kde":
        screens = screen_scales()
        if screens:
            return screens[0].percent, "kde-wayland"
    if be == "kde" and have("kreadconfig6"):
        r = run(["kreadconfig6", "--file", "kdeglobals", "--group", "KScreen", "--key", "ScaleFactor"], timeout=10)
        f = _to_float(r.stdout)
        return (int(round(f * 100)) if f else None), be
    if be == "gnome" and have("gsettings"):
        r = run(["gsettings", "get", "org.gnome.desktop.interface", "text-scaling-factor"], timeout=10)
        f = _to_float(r.stdout)
        return (int(round(f * 100)) if f else None), be
    f = _to_float(os.environ.get("GDK_SCALE", ""))
    return (int(round(f * 100)) if f else None), be


def wine_dpi_for_desktop() -> int | None:
    """Windows-DPI passend zur Desktop-Skalierung (None = unbekannt).

    Wine läuft über X11/XWayland – Xft.dpi ist genau die DPI, mit der X11-Programme selbst skalieren (KDE „Legacy-
    Anwendungen skalieren sich selbst“: 96 × Faktor). Skaliert KWin sie stattdessen hoch, steht dort 96 – dann darf Wine
    nicht zusätzlich vergrößern."""
    if os.environ.get("DISPLAY") and have("xrdb"):
        m = re.search(r"^Xft\.dpi:\s*([0-9.]+)", run(["xrdb", "-query"], timeout=5).stdout or "", re.M)
        f = _to_float(m.group(1)) if m else None
        if f:
            return max(96, min(480, int(round(f))))
    if have("kreadconfig6"):
        f = _to_float(run(["kreadconfig6", "--file", "kwinrc", "--group", "Xwayland", "--key", "Scale"], timeout=5).stdout)
        if f:
            return max(96, min(480, int(round(96 * f))))
    if os.environ.get("XDG_SESSION_TYPE") == "wayland":
        return None                 # globaler Faktor sagt unter Wayland nichts darüber, ob X11-Programme selbst skalieren
    pct, _be = desktop_scale_get()
    return max(96, min(480, int(round(96 * pct / 100)))) if pct else None


def desktop_scale_set(percent: int, screen: str = "") -> str:
    """Skalierung setzen. Plasma Wayland: je Bildschirm über kscreen-doctor, sofort wirksam (screen = Ausgangsname)."""
    factor = f"{percent / 100:.2f}"
    be = desktop_backend()
    if screen and kde_wayland():
        r = run(["kscreen-doctor", f"output.{screen}.scale.{percent / 100:g}"], timeout=30)
        if r.returncode == 0:
            return tr("Skalierung von {0} auf {1} % gesetzt – sofort wirksam. Windows-Programme in Prefixen mit "
                      "eigener DPI behalten ihre Größe (unter „Windows-DPI je Wine-Prefix“ anpassbar).").format(screen, percent)
        return (r.stderr or r.stdout).strip()[:300] or tr("kscreen-doctor fehlgeschlagen")
    if be == "kde" and have("kwriteconfig6"):
        r = run(["kwriteconfig6", "--file", "kdeglobals", "--group", "KScreen", "--key", "ScaleFactor", factor], timeout=10)
        if r.returncode == 0:
            return tr("KDE-Skalierung auf {0} % gesetzt (kdeglobals/KScreen). Wirkt nach Ab- und Anmelden. "
                      "Unter Plasma Wayland zählt zusätzlich die Skalierung je Bildschirm in den Anzeige-Einstellungen.").format(percent)
        return r.stderr.strip() or tr("kwriteconfig6 fehlgeschlagen")
    if be == "gnome" and have("gsettings"):
        r = run(["gsettings", "set", "org.gnome.desktop.interface", "text-scaling-factor", factor], timeout=10)
        return tr("GNOME-Textskalierung auf Faktor {0} gesetzt.").format(factor) if r.returncode == 0 else r.stderr.strip()
    profile = HOME / ".profile"
    with open(profile, "a", encoding="utf-8") as fh:
        fh.write(f"\nexport GDK_SCALE={factor}\n")
    return tr("Kein KDE/GNOME erkannt – GDK_SCALE={0} wurde in ~/.profile eingetragen.").format(factor)


def open_display_settings() -> bool:
    be = desktop_backend()
    if be == "kde" and have("systemsettings"):
        detach(["systemsettings", "kcm_kscreen"], logname="systemsettings")
        return True
    if be == "gnome" and have("gnome-control-center"):
        detach(["gnome-control-center", "display"], logname="gnome-control-center")
        return True
    return False
