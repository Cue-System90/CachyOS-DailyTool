# Maintainer: CueSystem (lokal, CachyOS)
# Reines Python-Paket ohne Build-Werkzeuge: Quellen werden direkt nach site-packages installiert.
pkgname=cachyos-dailytool
pkgver=5.1.2
pkgrel=1
pkgdesc="CachyOS DailyTool – GTK4/libadwaita-Oberfläche für Wine-Prefixe, Launcher-Migration, ProtonPlus, X3D-Kernzuteilung (CCD), Laufwerke (fstab), GPU-Status, Journal, Shader-Caches und System-Update"
arch=('any')
url="https://github.com/Cue-System90/CachyOS-DailyTool"
license=('MIT')
provides=('cachy-toolkit')
conflicts=('cachy-toolkit')
replaces=('cachy-toolkit')   # Paketname bis v5.0.4
depends=('python>=3.11' 'python-gobject' 'gtk4' 'libadwaita' 'polkit' 'xdg-utils' 'util-linux' 'rsync' 'tar' 'hicolor-icon-theme')
optdepends=(
  'umu-launcher: Proton außerhalb von Steam (Standard für proton-cachyos-slr)'
  'proton-cachyos: CachyOS-Proton-Build'
  'wine: System-Wine als Runner (alternativ wine-cachyos, wine-staging)'
  'goverlay: MangoHud-Konfiguration, Startoptionen übernehmen'
  'python-pefile: Icons aus .exe für Verknüpfungen'
  'python-pillow: Icons aus .exe für Verknüpfungen'
  'winetricks: Zusatzkomponenten im Prefix'
  'mangohud: FPS-Overlay als Startoption'
  'gamemode: gamemoderun als Startoption'
  'gamescope: Micro-Compositor als Startoption'
  'flatpak: ProtonPlus per Flatpak installieren'
  'pacman-contrib: checkupdates für die Update-Anzeige ohne Root'
  'nvidia-utils: nvidia-smi für den GPU-Status'
  'ntfs-3g: NTFS per FUSE-Treiber einbinden'
  'exfatprogs: exFAT formatieren'
  'spectacle: Screenshot-Modus für Doku/Tests'
)
source=("$pkgname-$pkgver.tar.gz")
sha256sums=('SKIP')

package() {
  cd "$srcdir/$pkgname-$pkgver"
  local site
  site="$(python -c 'import sysconfig; print(sysconfig.get_path("purelib"))')"
  install -d "$pkgdir$site"
  cp -r cachyos_dailytool "$pkgdir$site/"
  find "$pkgdir$site/cachyos_dailytool" -name '__pycache__' -type d -prune -exec rm -rf {} +
  python -m compileall -q -d "$site/cachyos_dailytool" "$pkgdir$site/cachyos_dailytool"

  install -d "$pkgdir$site/$pkgname-$pkgver.dist-info"
  printf 'Metadata-Version: 2.1\nName: cachyos-dailytool\nVersion: %s\nSummary: %s\nLicense: MIT\n' "$pkgver" "$pkgdesc" \
    > "$pkgdir$site/$pkgname-$pkgver.dist-info/METADATA"
  printf 'pacman\n' > "$pkgdir$site/$pkgname-$pkgver.dist-info/INSTALLER"
  printf '[console_scripts]\ncachyos-dailytool = cachyos_dailytool.__main__:main\n' > "$pkgdir$site/$pkgname-$pkgver.dist-info/entry_points.txt"

  install -d "$pkgdir/usr/bin"
  cat > "$pkgdir/usr/bin/cachyos-dailytool" <<'LAUNCHER'
#!/usr/bin/python3
import sys
from cachyos_dailytool.__main__ import main
sys.exit(main())
LAUNCHER
  chmod 755 "$pkgdir/usr/bin/cachyos-dailytool"

  install -Dm644 data/io.cuesystem.CachyOSDailyTool.desktop "$pkgdir/usr/share/applications/io.cuesystem.CachyOSDailyTool.desktop"
  local d size
  for d in data/icons/hicolor/*/apps; do
    size="$(basename "$(dirname "$d")")"
    install -Dm644 "$d/io.cuesystem.CachyOSDailyTool.png" "$pkgdir/usr/share/icons/hicolor/$size/apps/io.cuesystem.CachyOSDailyTool.png"
  done
  install -Dm755 cachyos_dailytool/uninstall.sh "$pkgdir/usr/share/$pkgname/uninstall.sh"
  install -Dm644 README.md "$pkgdir/usr/share/doc/$pkgname/README.md"
  install -Dm644 LICENSE "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
}
