# Maintainer: CueSystem (lokal, CachyOS)
# Reines Python-Paket ohne Build-Werkzeuge: Quellen werden direkt nach site-packages installiert.
pkgname=cachy-toolkit
pkgver=5.0.0
pkgrel=1
pkgdesc="CachyOS Alltagstool – GTK4/libadwaita-Oberfläche für Wine-Prefixe, Launcher-Migration, ProtonPlus, Laufwerke (fstab), GPU-Status, Journal, Shader-Caches und System-Update"
arch=('any')
url="https://cachyos.org"
license=('MIT')
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
  cp -r cachy_toolkit "$pkgdir$site/"
  find "$pkgdir$site/cachy_toolkit" -name '__pycache__' -type d -prune -exec rm -rf {} +
  python -m compileall -q -d "$site/cachy_toolkit" "$pkgdir$site/cachy_toolkit"

  install -d "$pkgdir$site/$pkgname-$pkgver.dist-info"
  printf 'Metadata-Version: 2.1\nName: cachy-toolkit\nVersion: %s\nSummary: %s\nLicense: MIT\n' "$pkgver" "$pkgdesc" \
    > "$pkgdir$site/$pkgname-$pkgver.dist-info/METADATA"
  printf 'pacman\n' > "$pkgdir$site/$pkgname-$pkgver.dist-info/INSTALLER"
  printf '[console_scripts]\ncachy-toolkit = cachy_toolkit.__main__:main\n' > "$pkgdir$site/$pkgname-$pkgver.dist-info/entry_points.txt"

  install -d "$pkgdir/usr/bin"
  cat > "$pkgdir/usr/bin/cachy-toolkit" <<'LAUNCHER'
#!/usr/bin/python3
import sys
from cachy_toolkit.__main__ import main
sys.exit(main())
LAUNCHER
  chmod 755 "$pkgdir/usr/bin/cachy-toolkit"

  install -Dm644 data/io.cuesystem.CachyToolkit.desktop "$pkgdir/usr/share/applications/io.cuesystem.CachyToolkit.desktop"
  local d size
  for d in data/icons/hicolor/*/apps; do
    size="$(basename "$(dirname "$d")")"
    install -Dm644 "$d/io.cuesystem.CachyToolkit.png" "$pkgdir/usr/share/icons/hicolor/$size/apps/io.cuesystem.CachyToolkit.png"
  done
  install -Dm755 cachy_toolkit/uninstall.sh "$pkgdir/usr/share/$pkgname/uninstall.sh"
  install -Dm644 README.md "$pkgdir/usr/share/doc/$pkgname/README.md"
  install -Dm644 LICENSE "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
}
