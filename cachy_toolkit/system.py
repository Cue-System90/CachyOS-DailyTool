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
        return subprocess.CompletedProcess(argv, 127, "", f"Programm nicht gefunden: {exc.filename}")
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(argv, 124, exc.stdout or "", "Zeitüberschreitung")


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
        log(f"Programm nicht gefunden: {exc.filename}")
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


def root_helper() -> str | None:
    for c in ("pkexec", "sudo"):
        if have(c):
            return c
    return None


def root_argv(shell_cmd: str) -> list[str]:
    helper = root_helper()
    if not helper:
        raise RuntimeError("Weder pkexec noch sudo gefunden – Root-Aktionen sind nicht möglich.")
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
        return f"{d} T {h} h {m} min"
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


FORMAT_FS = {"ext4": "ext4 (empfohlen für Linux-Daten)", "btrfs": "btrfs (Snapshots, Kompression)",
             "xfs": "xfs (große Dateien)", "exfat": "exFAT (Austausch mit Windows/macOS)"}


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


def _prefix_dpi(path: Path) -> int | None:
    try:
        text = (path / "user.reg").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = re.search(r'^"LogPixels"=dword:([0-9a-fA-F]{8})', text, re.M)
    return int(m.group(1), 16) if m else None


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
    return Prefix(name=path.name, path=path, arch=_prefix_arch(wine_dir), mtime=mtime, dpi=_prefix_dpi(wine_dir),
                  layout=layout, external=external)


def list_prefixes(root: Path, extra: list[Path] | None = None) -> list[Prefix]:
    """Prefixe im Standardordner plus zusätzlich eingetragene Pfade (auch außerhalb), ohne Duplikate."""
    out: list[Prefix] = []
    seen: set[str] = set()
    try:
        entries = sorted(root.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        entries = []
    for d in entries:
        p = describe_prefix(d)
        if p:
            seen.add(str(d.resolve()) if d.exists() else str(d))
            out.append(p)
    for e in extra or []:
        key = str(e.resolve()) if e.exists() else str(e)
        if key in seen:
            continue
        p = describe_prefix(e, external=True)
        if p:
            seen.add(key)
            out.append(p)
    return out


def move_dir(src: Path, dest: Path, log: LogFn) -> Path:
    """Verzeichnis verschieben (rename, sonst rsync + löschen). dest ist der neue Pfad des Verzeichnisses."""
    if dest.exists():
        raise RuntimeError(f"Ziel existiert bereits: {dest}")
    if not src.exists():
        raise RuntimeError(f"Quelle fehlt: {src}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.rename(src, dest)
        log(f"Verschoben: {src} → {dest}")
        return dest
    except OSError:
        pass
    log(f"rsync {src}/ → {dest}/ (anderes Dateisystem)")
    rc = stream(["rsync", "-a", "--info=progress2", "--no-inc-recursive", f"{src}/", f"{dest}/"], log)
    if rc != 0:
        raise RuntimeError(f"rsync fehlgeschlagen (Code {rc}); Quelle blieb unverändert")
    shutil.rmtree(src)
    log(f"Quelle entfernt: {src}")
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
    desktop.write_text(
        "[Desktop Entry]\nType=Application\nName=Mit Wine-Prefix starten\n"
        "Comment=Wählt oder erstellt einen Wine-Prefix vor dem Start einer .exe/.msi\n"
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

    hit = _first_existing([("Nativ", home / ".local/share/Steam"), ("Nativ (~/.steam/steam)", home / ".steam/steam"),
                           ("Flatpak", home / ".var/app/com.valvesoftware.Steam/data/Steam")])
    if hit:
        add("Steam", *hit)
    hit = _first_existing([("Nativ", home / ".local/share/lutris"), ("Flatpak", home / ".var/app/net.lutris.Lutris/data/lutris")])
    if hit:
        add("Lutris", *hit)
    hit = _first_existing([("Nativ", home / ".config/heroic"), ("Flatpak", home / ".var/app/com.heroicgameslauncher.hgl/config/heroic")])
    if hit:
        add("Heroic (Epic/GOG/Amazon)", *hit)
    hit = _first_existing([("Nativ", home / ".local/share/bottles"), ("Flatpak", home / ".var/app/com.usebottles.bottles/data/bottles")])
    if hit:
        add("Bottles", *hit)
    hit = _first_existing([("Nativ", home / ".local/share/Steam/compatibilitytools.d"),
                           ("Nativ", home / ".steam/root/compatibilitytools.d")])
    if hit:
        add("Steam Compat-Tools (Proton-GE u. a.)", *hit)

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
                    add(name, f"Wine-Prefix: {pdir.name}", pdir)
    return found


def migrate_dir(src: Path, dest_root: Path, log: LogFn) -> Path:
    """rsync nach dest_root/<name>, Quelle entfernen, Symlink setzen. Wirft RuntimeError bei Fehlern."""
    dest = dest_root / src.name
    if src.is_symlink():
        raise RuntimeError(f"{src} ist bereits ein Symlink → {os.path.realpath(src)}")
    if not src.exists():
        raise RuntimeError(f"{src} existiert nicht")
    if dest.resolve() == src.resolve():
        raise RuntimeError("Ziel und Quelle sind identisch")
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"rsync {src}/ → {dest}/")
    rc = stream(["rsync", "-a", "--info=progress2", "--no-inc-recursive", f"{src}/", f"{dest}/"], log)
    if rc != 0:
        raise RuntimeError(f"rsync fehlgeschlagen (Code {rc}); Quelle blieb unverändert")
    log(f"Entferne Quelle {src} …")
    shutil.rmtree(src)
    os.symlink(dest, src)
    log(f"Symlink gesetzt: {src} → {dest}")
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
        ("Steam – eigene Compat-Tools", home / ".local/share/Steam/compatibilitytools.d"),
        ("Steam (Flatpak) – Compat-Tools", home / ".var/app/com.valvesoftware.Steam/data/Steam/compatibilitytools.d"),
        ("System – proton-cachyos u. a.", Path("/usr/share/steam/compatibilitytools.d")),
        ("Heroic – Proton", home / ".config/heroic/tools/proton"),
        ("Heroic – Wine", home / ".config/heroic/tools/wine"),
        ("Lutris – Wine-Runner", home / ".local/share/lutris/runners/wine"),
        ("Bottles – Runner", home / ".local/share/bottles/runners"),
        ("umu – Proton", home / ".local/share/umu/compatibilitytools"),
        ("Wine-Prefix-Root", prefix_root),
    ]


@dataclass
class CompatTool:
    name: str
    location: str
    path: Path


def list_compat_tools(prefix_root: Path, home: Path = HOME) -> list[CompatTool]:
    out: list[CompatTool] = []
    for label, d in compat_tool_dirs(prefix_root, home):
        if label == "Wine-Prefix-Root" or not d.is_dir():
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
        CacheEntry("steam", "Steam Shader-Cache", "steamapps/shadercache (nativ und Flatpak)",
                   dirs=[p for p in (home / ".local/share/Steam/steamapps/shadercache",
                                     home / ".var/app/com.valvesoftware.Steam/data/Steam/steamapps/shadercache") if p.is_dir()]),
        CacheEntry("dxvk", "DXVK / VKD3D-Caches", f"*.dxvk-cache und *.vkd3d-proton.cache unter {prefix_root} und {games_dir}",
                   files=_dxvk_files([prefix_root, games_dir])),
        CacheEntry("mesa", "Mesa Shader-Cache", "~/.cache/mesa_shader_cache*",
                   dirs=[Path(p) for p in glob.glob(str(home / ".cache/mesa_shader_cache*"))]),
        CacheEntry("nvidia", "NVIDIA GLCache", "~/.cache/nvidia/GLCache und ~/.nv/GLCache",
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
        ("Wine-Prefix-Root", prefix_root),
        ("Gaming-Zielordner", games_dir),
        ("Prefix-Backups", backup_dir),
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
    "boot": ("Aktueller Boot – alles", [], None),
    "errors": ("Nur Warnungen und Fehler", ["-p", "warning"], None),
    "kernel": ("Kernel-Meldungen (dmesg)", ["-k"], None),
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
        return r.stderr.strip() or "journalctl lieferte keine Daten."
    lines = r.stdout.splitlines()
    regexes = [re.compile(x, re.I) for x in (preset_re, pattern.strip()) if x]
    if regexes:
        lines = [l for l in lines if all(rx.search(l) for rx in regexes)]
    if not lines:
        return "Keine passenden Einträge gefunden."
    if len(lines) > limit:
        lines = [f"… {len(lines) - limit} ältere Zeilen ausgeblendet …"] + lines[-limit:]
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
        return None, r.stderr.strip() or "checkupdates fehlgeschlagen"
    r = run(["pacman", "-Qu"], timeout=60)
    if r.returncode == 0:
        return [l for l in r.stdout.splitlines() if l.strip()], "pacman -Qu (lokale Datenbank, evtl. veraltet)"
    if r.returncode == 1 and not r.stdout.strip():
        return [], "pacman -Qu (lokale Datenbank, evtl. veraltet)"
    return None, r.stderr.strip() or "pacman -Qu fehlgeschlagen"


def update_argv() -> list[str]:
    return root_argv("pacman -Syu --noconfirm")


def dkms_status() -> str:
    if not have("dkms"):
        return "dkms ist nicht installiert (bei CachyOS-Kerneln mit fertigen Modulpaketen normal)."
    r = run(["dkms", "status"], timeout=60)
    return r.stdout.strip() or r.stderr.strip() or "Keine DKMS-Module registriert."


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


def desktop_scale_get() -> tuple[int | None, str]:
    be = desktop_backend()
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


def desktop_scale_set(percent: int) -> str:
    factor = f"{percent / 100:.2f}"
    be = desktop_backend()
    if be == "kde" and have("kwriteconfig6"):
        r = run(["kwriteconfig6", "--file", "kdeglobals", "--group", "KScreen", "--key", "ScaleFactor", factor], timeout=10)
        if r.returncode == 0:
            return (f"KDE-Skalierung auf {percent} % gesetzt (kdeglobals/KScreen). Wirkt nach Ab- und Anmelden. "
                    "Unter Plasma Wayland zählt zusätzlich die Skalierung je Bildschirm in den Anzeige-Einstellungen.")
        return r.stderr.strip() or "kwriteconfig6 fehlgeschlagen"
    if be == "gnome" and have("gsettings"):
        r = run(["gsettings", "set", "org.gnome.desktop.interface", "text-scaling-factor", factor], timeout=10)
        return f"GNOME-Textskalierung auf Faktor {factor} gesetzt." if r.returncode == 0 else r.stderr.strip()
    profile = HOME / ".profile"
    with open(profile, "a", encoding="utf-8") as fh:
        fh.write(f"\nexport GDK_SCALE={factor}\n")
    return f"Kein KDE/GNOME erkannt – GDK_SCALE={factor} wurde in ~/.profile eingetragen."


def open_display_settings() -> bool:
    be = desktop_backend()
    if be == "kde" and have("systemsettings"):
        detach(["systemsettings", "kcm_kscreen"], logname="systemsettings")
        return True
    if be == "gnome" and have("gnome-control-center"):
        detach(["gnome-control-center", "display"], logname="gnome-control-center")
        return True
    return False
