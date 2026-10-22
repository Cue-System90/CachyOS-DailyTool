#!/usr/bin/env bash
# CachyOS DailyTool – Installation auf CachyOS / Arch Linux.
# Vor jeder Installation wird eine vorhandene Version restlos entfernt (pacman-Paket, ~/.local-Installation,
# alte Bash-Version, Menüeinträge, Icons, .exe-Verknüpfung); Einstellungen bleiben erhalten.
#   ./install.sh                     → Benutzer-Installation nach ~/.local (kein Root; empfohlen)
#   ./install.sh --system            → pacman-Paket bauen (makepkg) und mit sudo/pkexec pacman -U installieren
#   ./install.sh --purge             → vorher zusätzlich Einstellungen und Protokolle löschen (mit oder ohne --system)
#   ./install.sh --keep              → nichts vorher entfernen, nur drüberinstallieren
#   ./install.sh --build             → nur das Paket bauen und unter dist/ ablegen
#   ./install.sh --deps              → fehlende Pakete anzeigen und auf Wunsch installieren
#   ./install.sh --uninstall [--purge] [--prefixes] [--yes] → vollständige Deinstallation (scripts/uninstall.sh)
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_ID="io.cuesystem.CachyOSDailyTool"
PKGVER="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$HERE/cachyos_dailytool/__init__.py")"
DEST="${HOME:?}/.local/lib/cachyos-dailytool"
BIN="${HOME:?}/.local/bin/cachyos-dailytool"
APPS="${HOME:?}/.local/share/applications"
ICONS="${HOME:?}/.local/share/icons/hicolor"

REQUIRED=(python python-gobject gtk4 libadwaita polkit xdg-utils rsync)
RECOMMENDED=(umu-launcher proton-cachyos winetricks mangohud gamemode gamescope goverlay flatpak pacman-contrib python-pefile python-pillow)

MODE="user"; PURGE=0; KEEP=0; EXTRA=()
for a in "$@"; do
  case "$a" in
    --system) MODE="system" ;; --build) MODE="build" ;; --deps) MODE="deps" ;; --uninstall) MODE="uninstall" ;;
    --purge) PURGE=1; EXTRA+=("--purge") ;; --keep) KEEP=1 ;; --prefixes|--yes|-y|--quiet|-q) EXTRA+=("$a") ;;
    -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
    *) echo "Unbekannte Option: $a" >&2; exit 2 ;;
  esac
done

ok()   { printf '  [ok]    %s\n' "$*"; }
warn() { printf '  [warn]  %s\n' "$*"; }
fail() { printf '  [FEHLT] %s\n' "$*"; }

missing() {
  local m=()
  for p in "$@"; do pacman -Qq "$p" >/dev/null 2>&1 || m+=("$p"); done
  printf '%s\n' "${m[@]:-}"
}

check_deps() {
  echo "-- Abhängigkeiten"
  local req rec
  req=$(missing "${REQUIRED[@]}" | tr -s ' \n' ' ' | sed 's/^ *//;s/ *$//')
  rec=$(missing "${RECOMMENDED[@]}" | tr -s ' \n' ' ' | sed 's/^ *//;s/ *$//')
  if pacman -Qq wine wine-cachyos wine-staging wine-ge-custom 2>/dev/null | grep -q .; then
    ok "wine ($(pacman -Qq wine wine-cachyos wine-staging 2>/dev/null | head -1))"
  else
    warn "wine fehlt – Windows-Programme/Prefixe brauchen wine (z. B. sudo pacman -S wine-cachyos oder wine)"
  fi
  if [ -n "$req" ]; then
    fail "Pflichtpakete: $req"
    if [ -t 0 ]; then
      read -r -p "Jetzt mit pacman installieren? [J/n] " a
      if [[ "${a:-j}" =~ ^[JjYy]$ ]]; then sudo pacman -S --needed $req; else echo "Abbruch."; exit 1; fi
    else
      echo "Bitte installieren: sudo pacman -S --needed $req"; exit 1
    fi
  else
    ok "Pflichtpakete: ${REQUIRED[*]}"
  fi
  if [ -n "$rec" ]; then
    warn "Empfohlen (optional): $rec"
    if [ -t 0 ]; then
      read -r -p "Empfohlene Pakete installieren? [j/N] " a
      if [[ "${a:-n}" =~ ^[JjYy]$ ]]; then sudo pacman -S --needed $rec || true; fi
    fi
  else
    ok "Empfohlene Pakete vorhanden"
  fi
  command -v nvidia-smi >/dev/null 2>&1 && ok "nvidia-smi (GPU-Status)" || warn "nvidia-smi fehlt – GPU-Status nutzt sysfs (AMD/Intel) oder bleibt leer"
}

preclean() {
  if [ "$KEEP" = 1 ]; then warn "--keep: vorhandene Version wird nicht entfernt"; return 0; fi
  echo "-- Vorhandene Version entfernen$( [ "$PURGE" = 1 ] && echo ' (inkl. Einstellungen/Protokolle)' )"
  local args=(--quiet --reinstall)
  [ "$PURGE" = 1 ] && args+=(--purge)
  bash "$HERE/cachyos_dailytool/uninstall.sh" "${args[@]}" || warn "Vorbereinigung meldete Reste – Details oben"
}

restart_x3d() {
  # X3D-Dienst (falls eingerichtet) mit dem frisch installierten Code neu starten; die Unit wird vorher neu geschrieben,
  # damit ExecStart nach einem Wechsel Benutzer-/Paketinstallation auf den richtigen Befehl zeigt ($1 = Befehl).
  local cmd="${1:-cachyos-dailytool}" unit="cachyos-dailytool-x3d.service"
  command -v systemctl >/dev/null 2>&1 || return 0
  if systemctl --user is-enabled "$unit" >/dev/null 2>&1; then
    "$cmd" --x3d-unit >/dev/null 2>&1 || warn "Unit konnte nicht neu geschrieben werden ($cmd --x3d-unit)"
    systemctl --user daemon-reload >/dev/null 2>&1 || true
    systemctl --user restart "$unit" >/dev/null 2>&1 && ok "X3D-Dienst neu gestartet ($unit)" || warn "X3D-Dienst konnte nicht neu gestartet werden: systemctl --user restart $unit"
  fi
}

refresh_shortcuts() {
  # Verknüpfungen registrierter Anwendungen (cachy-app-*.desktop) auf den installierten Befehl umschreiben
  local cmd="${1:-cachyos-dailytool}"
  local out
  out="$("$cmd" --refresh-shortcuts 2>/dev/null)" && [ -n "$out" ] && ok "$out" || true
}

root_helper() {
  # sudo im Terminal, sonst pkexec (Polkit-Dialog) – wie in uninstall.sh
  if [ -t 0 ] && command -v sudo >/dev/null 2>&1; then echo sudo
  elif command -v pkexec >/dev/null 2>&1; then echo pkexec
  elif command -v sudo >/dev/null 2>&1; then echo sudo
  fi
}

refresh_caches() {
  command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APPS" 2>/dev/null || true
  command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -f -t "$ICONS" 2>/dev/null || true
  command -v kbuildsycoca6 >/dev/null 2>&1 && kbuildsycoca6 >/dev/null 2>&1 || true
}

case "$MODE" in
  deps) check_deps ;;
  build) "$HERE/scripts/build-package.sh" "$HERE/dist" ;;
  uninstall) exec bash "$HERE/cachyos_dailytool/uninstall.sh" "${EXTRA[@]}" ;;
  system)
    echo "== CachyOS DailyTool v$PKGVER – Paket-Installation =="
    check_deps
    preclean
    "$HERE/scripts/build-package.sh" "$HERE/dist"
    PKG="$(ls -t "$HERE/dist"/cachyos-dailytool-*.pkg.tar.zst | head -1)"
    HELPER="$(root_helper)"
    [ -n "$HELPER" ] || { echo "Weder sudo noch pkexec gefunden – Paket bitte manuell installieren: pacman -U $PKG"; exit 1; }
    echo "-- Installiere $PKG ($HELPER pacman -U)"
    $HELPER pacman -U --noconfirm "$PKG"
    refresh_caches
    restart_x3d cachyos-dailytool
    refresh_shortcuts cachyos-dailytool
    echo
    echo "Fertig. Start: cachyos-dailytool (oder Anwendungsmenü „CachyOS DailyTool“)."
    echo "Deinstallation: ./install.sh --uninstall [--purge]  oder  bash /usr/share/cachyos-dailytool/uninstall.sh"
    ;;
  user)
    echo "== CachyOS DailyTool v$PKGVER – Benutzer-Installation nach ~/.local =="
    check_deps
    preclean
    echo "-- Dateien"
    mkdir -p "$DEST" "$(dirname "$BIN")" "$APPS"
    cp -r "$HERE/cachyos_dailytool" "$DEST/"
    find "${DEST:?}" -name '__pycache__' -type d -prune -exec rm -rf {} +
    chmod 755 "$DEST/cachyos_dailytool/uninstall.sh"
    ok "$DEST"
    install -m755 "$HERE/scripts/cachyos-dailytool" "$BIN" && ok "$BIN"
    install -m644 "$HERE/data/$APP_ID.desktop" "$APPS/$APP_ID.desktop"
    sed -i "s|^Exec=.*|Exec=$BIN %f|" "$APPS/$APP_ID.desktop"
    ok "$APPS/$APP_ID.desktop"
    for d in "$HERE"/data/icons/hicolor/*/apps; do
      size="$(basename "$(dirname "$d")")"
      install -Dm644 "$d/$APP_ID.png" "$ICONS/$size/apps/$APP_ID.png"
    done
    ok "Icons ($ICONS)"
    mkdir -p "$HOME/Games/wine-prefixes" "$HOME/Games/prefix-backups"
    refresh_caches
    restart_x3d "$BIN"
    refresh_shortcuts "$BIN"
    echo
    echo "Fertig. Start über das Anwendungsmenü („CachyOS DailyTool“) oder im Terminal:"
    echo "  $BIN"
    echo "Deinstallation: ./install.sh --uninstall [--purge]  oder  bash $DEST/cachyos_dailytool/uninstall.sh  oder im App-Menü „Deinstallieren …“"
    case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) echo "Hinweis: ~/.local/bin ist nicht im PATH – im Terminal den vollen Pfad nutzen.";; esac
    ;;
esac
