#!/usr/bin/env bash
# Baut das pacman-Paket (ohne Installation) und legt es unter dist/ ab.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PKGVER="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$HERE/cachy_toolkit/__init__.py")"
DIST="${1:-$HERE/dist}"
BUILD="$(mktemp -d)"
trap 'rm -rf "${BUILD:?}"' EXIT
mkdir -p "$BUILD/cachy-toolkit-$PKGVER" "$DIST"
cp -r "$HERE/cachy_toolkit" "$HERE/data" "$HERE/scripts" "$HERE/README.md" "$HERE/LICENSE" "$BUILD/cachy-toolkit-$PKGVER/"
find "$BUILD/cachy-toolkit-$PKGVER" -name '__pycache__' -type d -prune -exec rm -rf {} +
tar -C "$BUILD" -czf "$BUILD/cachy-toolkit-$PKGVER.tar.gz" "cachy-toolkit-$PKGVER"
sed "s/^pkgver=.*/pkgver=$PKGVER/" "$HERE/PKGBUILD" > "$BUILD/PKGBUILD"
( cd "$BUILD" && PKGDEST="$DIST" SRCPKGDEST="$DIST" makepkg -f --noconfirm --nodeps )
( cd "$DIST" && sha256sum cachy-toolkit-*.pkg.tar.zst > SHA256SUMS )
echo "Paket liegt in: $DIST"; ls -la "$DIST"
