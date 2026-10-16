#!/usr/bin/env bash
# CachyOS Alltagstool – vollständige Deinstallation, unabhängig von der Installationsart.
#   uninstall.sh               → Programm entfernen (pacman-Paket und/oder ~/.local), Menüeintrag, Icons,
#                                .exe-Verknüpfung, Reste der alten Bash-Version; Einstellungen/Protokolle bleiben
#   uninstall.sh --purge       → zusätzlich Einstellungen (~/.config/cachy-daily-toolkit) und Protokolle löschen
#   uninstall.sh --prefixes    → zusätzlich Wine-Prefix-Root und Prefix-Backups löschen (fragt nach, außer mit --yes)
#   uninstall.sh --yes         → keine Rückfragen        --quiet → nur Zusammenfassung
# Root-Helfer für das Paket: sudo im Terminal, sonst pkexec (oder CACHY_ROOT_HELPER=…).
set -uo pipefail
APP_ID="io.cuesystem.CachyToolkit"
PKG="cachy-toolkit"
HOME_DIR="${HOME:?HOME ist nicht gesetzt}"
CONF_DIR="${XDG_CONFIG_HOME:-$HOME_DIR/.config}/cachy-daily-toolkit"
DATA_DIR="${XDG_DATA_HOME:-$HOME_DIR/.local/share}/cachy-daily-toolkit"
APPS="${XDG_DATA_HOME:-$HOME_DIR/.local/share}/applications"
ICONS="${XDG_DATA_HOME:-$HOME_DIR/.local/share}/icons/hicolor"
PURGE=0; PREFIXES=0; YES=0; QUIET=0
for a in "$@"; do
  case "$a" in
    --purge) PURGE=1 ;; --prefixes) PREFIXES=1 ;; --yes|-y) YES=1 ;; --quiet|-q) QUIET=1 ;; --keep-config) ;;
    -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
    *) echo "Unbekannte Option: $a" >&2; exit 2 ;;
  esac
done
removed=(); kept=()
say() { [ "$QUIET" = 1 ] || printf '  %s\n' "$*"; }
rm_path() {
  local p="${1:?}"
  case "$p" in /|"$HOME_DIR"|"$HOME_DIR/"|/home|/usr|/etc) echo "Verweigert: $p" >&2; return 1 ;; esac
  if [ -e "$p" ] || [ -L "$p" ]; then
    rm -rf -- "${p:?}" && removed+=("$p") && say "[entfernt]  $p"
  fi
}
root_helper() {
  if [ -n "${CACHY_ROOT_HELPER:-}" ]; then echo "$CACHY_ROOT_HELPER"
  elif [ -t 0 ] && command -v sudo >/dev/null 2>&1; then echo sudo
  elif command -v pkexec >/dev/null 2>&1; then echo pkexec
  elif command -v sudo >/dev/null 2>&1; then echo sudo
  fi
}

# Pfade aus der Konfiguration lesen, bevor sie ggf. gelöscht wird
PREFIX_ROOT=""; BACKUP_DIR=""
if [ -f "$CONF_DIR/config.env" ]; then
  PREFIX_ROOT=$(sed -n 's/^WINE_PREFIX_ROOT="\{0,1\}\([^"]*\)"\{0,1\}$/\1/p' "$CONF_DIR/config.env" | head -1)
  BACKUP_DIR=$(sed -n 's/^PREFIX_BACKUP_DIR="\{0,1\}\([^"]*\)"\{0,1\}$/\1/p' "$CONF_DIR/config.env" | head -1)
fi
PREFIX_ROOT="${PREFIX_ROOT:-$HOME_DIR/Games/wine-prefixes}"
BACKUP_DIR="${BACKUP_DIR:-$HOME_DIR/Games/prefix-backups}"

# 1) pacman-Paket
if command -v pacman >/dev/null 2>&1 && pacman -Qq "$PKG" >/dev/null 2>&1; then
  helper=$(root_helper)
  if [ -n "$helper" ]; then
    say "Entferne Paket $PKG ($helper pacman -Rns) …"
    if $helper pacman -Rns --noconfirm "$PKG"; then removed+=("Paket $PKG"); else kept+=("Paket $PKG – pacman -Rns fehlgeschlagen, bitte manuell: sudo pacman -Rns $PKG"); fi
  else
    kept+=("Paket $PKG – kein sudo/pkexec gefunden, bitte manuell: sudo pacman -Rns $PKG")
  fi
fi

# 2) Benutzer-Installation
rm_path "$HOME_DIR/.local/lib/cachy-toolkit"
rm_path "$HOME_DIR/.local/bin/cachy-toolkit"
rm_path "$APPS/$APP_ID.desktop"
rm_path "$APPS/cachy-daily-toolkit-launch.desktop"
for f in "$ICONS"/*/apps/"$APP_ID".png; do [ -e "$f" ] && rm_path "$f"; done

# 3) Alte Bash-Version (v1–v4)
rm_path "$HOME_DIR/.local/bin/cachy-daily-toolkit.sh"
rm_path "$APPS/cachy-daily-toolkit.desktop"

# 4) MIME-Zuordnungen (.exe/.msi → Tool) bereinigen
for m in "${XDG_CONFIG_HOME:-$HOME_DIR/.config}/mimeapps.list" "$APPS/mimeapps.list" "$APPS/defaults.list"; do
  if [ -f "$m" ] && grep -qE 'cachy-daily-toolkit-launch\.desktop|io\.cuesystem\.CachyToolkit\.desktop' "$m"; then
    sed -i -E 's/(cachy-daily-toolkit-launch|io\.cuesystem\.CachyToolkit)\.desktop;?//g; /^[^=[]+=$/d' "$m"
    removed+=("MIME-Einträge in $m"); say "[bereinigt] $m"
  fi
done

# 5) Temporäre Dateien der Bash-Version
for t in /tmp/cachy-*.log /tmp/wine-prefix-launcher.log /tmp/wine-dpi.log /tmp/protonplus.log /tmp/cachy-toolkit-progress.log; do
  [ -e "$t" ] && [ -O "$t" ] && rm_path "$t"
done

# 6) Einstellungen und Protokolle
if [ "$PURGE" = 1 ]; then
  rm_path "$CONF_DIR"; rm_path "$DATA_DIR"
else
  [ -d "$CONF_DIR" ] && kept+=("Einstellungen $CONF_DIR (behalten; --purge löscht sie)")
  [ -d "$DATA_DIR" ] && kept+=("Protokolle $DATA_DIR (behalten; --purge löscht sie)")
fi

# 7) Wine-Prefixe und Backups – nur auf ausdrücklichen Wunsch
if [ "$PREFIXES" = 1 ]; then
  ok=0
  if [ "$YES" = 1 ]; then ok=1
  elif [ -t 0 ]; then
    read -r -p "Wine-Prefixe unter $PREFIX_ROOT und Backups unter $BACKUP_DIR unwiderruflich löschen? [j/N] " a
    [[ "$a" =~ ^[JjYy]$ ]] && ok=1
  fi
  if [ "$ok" = 1 ]; then rm_path "$PREFIX_ROOT"; rm_path "$BACKUP_DIR"; else kept+=("Wine-Prefixe/Backups (nicht bestätigt)"); fi
fi

# 8) Menü- und Icon-Caches
command -v update-desktop-database >/dev/null 2>&1 && [ -d "$APPS" ] && update-desktop-database "$APPS" 2>/dev/null
command -v gtk-update-icon-cache >/dev/null 2>&1 && [ -d "$ICONS" ] && gtk-update-icon-cache -f -t "$ICONS" 2>/dev/null
command -v kbuildsycoca6 >/dev/null 2>&1 && kbuildsycoca6 >/dev/null 2>&1

echo "Deinstallation abgeschlossen: ${#removed[@]} Einträge entfernt."
for k in "${kept[@]}"; do echo "  [behalten]  $k"; done
left=""
for p in "$HOME_DIR/.local/lib/cachy-toolkit" "$HOME_DIR/.local/bin/cachy-toolkit" "$APPS/$APP_ID.desktop" "$APPS/cachy-daily-toolkit-launch.desktop"; do [ -e "$p" ] && left+=" $p"; done
command -v cachy-toolkit >/dev/null 2>&1 && left+=" $(command -v cachy-toolkit)"
if [ -n "$left" ]; then echo "  [Warnung]   Noch vorhanden:$left"; exit 1; fi
exit 0
