#!/usr/bin/env bash
# Baut das pacman-Paket (ohne Installation) und legt es unter dist/ ab.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PKGVER="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$HERE/cachyos_dailytool/__init__.py")"
DIST="${1:-$HERE/dist}"
BUILD="$(mktemp -d)"
trap 'rm -rf "${BUILD:?}"' EXIT
mkdir -p "$BUILD/cachyos-dailytool-$PKGVER" "$DIST"
cp -r "$HERE/cachyos_dailytool" "$HERE/data" "$HERE/scripts" "$HERE/README.md" "$HERE/LICENSE" "$BUILD/cachyos-dailytool-$PKGVER/"
find "$BUILD/cachyos-dailytool-$PKGVER" -name '__pycache__' -type d -prune -exec rm -rf {} +
tar -C "$BUILD" -czf "$BUILD/cachyos-dailytool-$PKGVER.tar.gz" "cachyos-dailytool-$PKGVER"
sed "s/^pkgver=.*/pkgver=$PKGVER/" "$HERE/PKGBUILD" > "$BUILD/PKGBUILD"
( cd "$BUILD" && PKGDEST="$DIST" SRCPKGDEST="$DIST" makepkg -f --noconfirm --nodeps )
( cd "$DIST" && sha256sum cachyos-dailytool-*.pkg.tar.zst > SHA256SUMS )
echo "Paket liegt in: $DIST"; ls -la "$DIST"
