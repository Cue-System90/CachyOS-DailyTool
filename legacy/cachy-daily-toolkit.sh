#!/usr/bin/env bash
# cachy-daily-toolkit.sh (v4)
# Alltagstool fuer CachyOS. Nutzt kdialog (KDE-Look) wenn vorhanden,
# faellt sonst auf zenity zurueck. Kategorisiertes Menue, zusaetzliche
# Alltagsfunktionen: Proton-GE-Manager, GPU-Status, MangoHud-Toggle,
# Wine-Prefix-Backup/Restore, Shader-Cache leeren, Speicherplatz-Uebersicht,
# kombiniertes System-Update inkl. DKMS-Check.

set -uo pipefail

APP_TITLE="CachyOS Alltagstool"
CONF_DIR="$HOME/.config/cachy-daily-toolkit"
CONF_FILE="$CONF_DIR/config.env"
mkdir -p "$CONF_DIR"

# -----------------------------------------------------------------------------
# Persistente UI-Skalierung (Fenstergroesse)
# -----------------------------------------------------------------------------
# Fruehere Versionen hatten die Fenstergroessen (--width/--height) fest im
# Code verdrahtet. Dadurch ging jede manuelle Groessenanpassung durch den
# Benutzer beim naechsten Start des Tools wieder verloren, da nichts
# gespeichert wurde. Jetzt werden Breite/Hoehe aus $CONF_FILE geladen (falls
# vorhanden) und koennen ueber das Menue dauerhaft geaendert werden.
UI_WIDTH=760
UI_HEIGHT=520
if [[ -f "$CONF_FILE" ]]; then
    # shellcheck disable=SC1090
    source "$CONF_FILE"
fi

save_ui_scaling() {
    local w="$1" h="$2"
    {
        grep -v '^UI_WIDTH=' "$CONF_FILE" 2>/dev/null | grep -v '^UI_HEIGHT='
        echo "UI_WIDTH=$w"
        echo "UI_HEIGHT=$h"
    } > "$CONF_FILE.tmp" 2>/dev/null
    mv "$CONF_FILE.tmp" "$CONF_FILE"
}

adjust_tool_scaling() {
    local NEW_W NEW_H
    NEW_W=$(ui_entry "Fensterbreite in Pixel (aktuell: $UI_WIDTH):" "$UI_WIDTH") || return 0
    [[ -z "$NEW_W" ]] && return 0
    NEW_H=$(ui_entry "Fensterhoehe in Pixel (aktuell: $UI_HEIGHT):" "$UI_HEIGHT") || return 0
    [[ -z "$NEW_H" ]] && return 0

    if ! [[ "$NEW_W" =~ ^[0-9]+$ ]] || ! [[ "$NEW_H" =~ ^[0-9]+$ ]]; then
        ui_error "Breite und Hoehe muessen ganze Zahlen sein."
        return 1
    fi

    UI_WIDTH="$NEW_W"
    UI_HEIGHT="$NEW_H"
    save_ui_scaling "$UI_WIDTH" "$UI_HEIGHT"

    if [[ "$UI_BACKEND" == "kdialog" ]]; then
        ui_info "Groesse gespeichert ($UI_WIDTH x $UI_HEIGHT) und wird ab jetzt verwendet.\nHinweis: kdialog unterstuetzt --geometry nur fuer wenige Dialogtypen (u.a. Textbox); bei Listen/Checklisten kann KDE die Groesse teils ignorieren (bekannter kdialog/KWin-Bug)."
    else
        ui_info "Groesse gespeichert ($UI_WIDTH x $UI_HEIGHT) und wird ab jetzt fuer alle Dialoge dieses Tools verwendet."
    fi
}

# -----------------------------------------------------------------------------
# UI-Abstraktion: kdialog (KDE-Look) bevorzugt, zenity als Fallback
# -----------------------------------------------------------------------------
UI_BACKEND=""
if command -v kdialog >/dev/null 2>&1; then
    UI_BACKEND="kdialog"
elif command -v zenity >/dev/null 2>&1; then
    UI_BACKEND="zenity"
else
    echo "Weder kdialog noch zenity gefunden. Bitte eines der beiden installieren." >&2
    exit 1
fi

ui_info() {
    local text="$1"
    if [[ "$UI_BACKEND" == "kdialog" ]]; then
        kdialog --title "$APP_TITLE" --geometry "${UI_WIDTH}x${UI_HEIGHT}" --msgbox "$text"
    else
        zenity --info --title="$APP_TITLE" --text="$text" --width="$UI_WIDTH" 2>/dev/null
    fi
}

ui_error() {
    local text="$1"
    if [[ "$UI_BACKEND" == "kdialog" ]]; then
        kdialog --title "$APP_TITLE" --geometry "${UI_WIDTH}x${UI_HEIGHT}" --error "$text"
    else
        zenity --error --title="$APP_TITLE" --text="$text" --width="$UI_WIDTH" 2>/dev/null
    fi
}

ui_question() {
    local text="$1"
    if [[ "$UI_BACKEND" == "kdialog" ]]; then
        kdialog --title "$APP_TITLE" --geometry "${UI_WIDTH}x${UI_HEIGHT}" --yesno "$text"
    else
        zenity --question --title="$APP_TITLE" --text="$text" --width="$UI_WIDTH" 2>/dev/null
    fi
}

ui_entry() {
    local text="$1" default="${2:-}"
    if [[ "$UI_BACKEND" == "kdialog" ]]; then
        kdialog --title "$APP_TITLE" --geometry "${UI_WIDTH}x${UI_HEIGHT}" --inputbox "$text" "$default"
    else
        zenity --entry --title="$APP_TITLE" --text="$text" --entry-text="$default" 2>/dev/null
    fi
}

ui_filesel() {
    local text="$1" start="${2:-$HOME}"
    if [[ "$UI_BACKEND" == "kdialog" ]]; then
        kdialog --title "$text" --getopenfilename "$start"
    else
        zenity --file-selection --title="$text" --filename="$start" 2>/dev/null
    fi
}

ui_dirsel() {
    local text="$1" start="${2:-$HOME}"
    if [[ "$UI_BACKEND" == "kdialog" ]]; then
        kdialog --title "$text" --getexistingdirectory "$start"
    else
        zenity --file-selection --directory --title="$text" --filename="$start" 2>/dev/null
    fi
}

# Einzelauswahl aus einer Liste. Erwartet abwechselnd "tag" "label" Paare.
ui_menu() {
    local title="$1"; shift
    if [[ "$UI_BACKEND" == "kdialog" ]]; then
        kdialog --title "$APP_TITLE" --geometry "${UI_WIDTH}x${UI_HEIGHT}" --menu "$title" "$@"
    else
        local args=() label
        while [[ $# -gt 0 ]]; do
            args+=("$2")
            shift 2
        done
        zenity --list --title="$APP_TITLE" --text="$title" --column="Option" \
            "${args[@]}" --width="$UI_WIDTH" --height="$UI_HEIGHT" 2>/dev/null
    fi
}

# Mehrfachauswahl (Checklist). Erwartet "tag" "label" "state(on/off)" Tripel.
ui_checklist() {
    local title="$1"; shift
    if [[ "$UI_BACKEND" == "kdialog" ]]; then
        kdialog --title "$APP_TITLE" --geometry "${UI_WIDTH}x${UI_HEIGHT}" --separate-output --checklist "$title" "$@"
    else
        local args=() tag label state zstate
        while [[ $# -gt 0 ]]; do
            tag="$1"; label="$2"; state="$3"
            zstate="FALSE"; [[ "$state" == "on" ]] && zstate="TRUE"
            args+=("$zstate" "$label" "$tag")
            shift 3
        done
        zenity --list --checklist --title="$APP_TITLE" --text="$title" \
            --column="" --column="Option" --column="Tag" \
            "${args[@]}" --width="$UI_WIDTH" --height="$UI_HEIGHT" --separator="|" --hide-column=3 2>/dev/null
    fi
}

ui_progress_run() {
    local title="$1" text="$2" cmd="$3"
    if [[ "$UI_BACKEND" == "kdialog" ]]; then
        local ref
        ref=$(kdialog --title "$title" --progressbar "$text" 0)
        qdbus "$ref" setValue 0 >/dev/null 2>&1
        bash -c "$cmd" >/tmp/cachy-toolkit-progress.log 2>&1 &
        local pid=$!
        while kill -0 "$pid" 2>/dev/null; do sleep 0.4; done
        wait "$pid"
        local rc=$?
        qdbus "$ref" close >/dev/null 2>&1
        return $rc
    else
        bash -c "$cmd" >/tmp/cachy-toolkit-progress.log 2>&1 &
        local pid=$!
        ( while kill -0 "$pid" 2>/dev/null; do sleep 0.3; echo "10"; done ) | \
            zenity --progress --pulsate --auto-close --title="$title" --text="$text" \
            --width="$UI_WIDTH" --height=120 2>/dev/null
        wait "$pid"
        return $?
    fi
}

ui_textinfo_file() {
    local title="$1" file="$2"
    if [[ "$UI_BACKEND" == "kdialog" ]]; then
        kdialog --title "$title" --textbox "$file" "$UI_WIDTH" "$UI_HEIGHT"
    else
        zenity --text-info --title="$title" --filename="$file" --width="$UI_WIDTH" --height="$UI_HEIGHT" 2>/dev/null
    fi
}

ui_slider() {
    local title="$1" min="$2" max="$3" step="$4" default="$5"
    if [[ "$UI_BACKEND" == "kdialog" ]]; then
        kdialog --title "$APP_TITLE" --slider "$title" "$min" "$max" "$step"
    else
        zenity --scale --title="$APP_TITLE" --text="$title" \
            --min-value="$min" --max-value="$max" --step="$step" --value="$default" 2>/dev/null
    fi
}

require_cmd() {
    for c in "$@"; do
        command -v "$c" >/dev/null 2>&1 || { ui_error "Fehlendes Kommando: $c\nBitte installieren und erneut versuchen."; return 1; }
    done
}

require_root_helper() {
    command -v pkexec >/dev/null 2>&1 || command -v sudo >/dev/null 2>&1 || {
        ui_error "Weder pkexec noch sudo gefunden. Root-Aktionen nicht moeglich."
        return 1
    }
}

run_as_root() {
    if command -v pkexec >/dev/null 2>&1; then
        pkexec bash -c "$1"
    else
        sudo bash -c "$1"
    fi
}

if [[ -f "$CONF_FILE" ]]; then
    source "$CONF_FILE"
fi
WINE_PREFIX_ROOT="${WINE_PREFIX_ROOT:-$HOME/Games/wine-prefixes}"
GAMES_TARGET_DRIVE="${GAMES_TARGET_DRIVE:-$HOME/Games}"
PREFIX_BACKUP_DIR="${PREFIX_BACKUP_DIR:-$HOME/Games/prefix-backups}"
mkdir -p "$WINE_PREFIX_ROOT" "$GAMES_TARGET_DRIVE" "$PREFIX_BACKUP_DIR"

save_config() {
    cat > "$CONF_FILE" <<EOF
WINE_PREFIX_ROOT="$WINE_PREFIX_ROOT"
GAMES_TARGET_DRIVE="$GAMES_TARGET_DRIVE"
PREFIX_BACKUP_DIR="$PREFIX_BACKUP_DIR"
EOF
}

list_prefixes() {
    find "$WINE_PREFIX_ROOT" -maxdepth 1 -mindepth 1 -type d -printf "%f\n" 2>/dev/null | sort
}

# -----------------------------------------------------------------------------
# 1) Wine-Prefix-Launcher fuer .exe/.msi
# -----------------------------------------------------------------------------
launch_exe() {
    require_cmd wine || return 1
    local EXE="${1:-}"
    if [[ -z "$EXE" ]]; then
        EXE=$(ui_filesel "Windows-Programm auswaehlen" "$HOME") || return 0
    fi
    [[ -f "$EXE" ]] || { ui_error "Datei nicht gefunden:\n$EXE"; return 1; }

    mapfile -t EXISTING < <(list_prefixes)
    local args=()
    local i=0
    for p in "${EXISTING[@]}"; do
        [[ -n "$p" ]] && { args+=("$p" "$p"); ((i++)); }
    done
    args+=("__NEW__" "+ Neuen Prefix erstellen")

    local CHOICE
    CHOICE=$(ui_menu "Programm: $(basename "$EXE")\nIn welchem Prefix starten?" "${args[@]}") || return 0
    [[ -z "$CHOICE" ]] && return 0

    if [[ "$CHOICE" == "__NEW__" ]]; then
        local NEW_NAME
        NEW_NAME=$(ui_entry "Name des neuen Wine-Prefix (z.B. gog-witcher3, ubisoft-connect):" "") || return 0
        [[ -z "$NEW_NAME" ]] && return 0
        local TARGET="$WINE_PREFIX_ROOT/$NEW_NAME"
        local ARCH
        ARCH=$(ui_menu "Architektur waehlen" "win64" "win64 (empfohlen)" "win32" "win32") || ARCH="win64"
        [[ -z "$ARCH" ]] && ARCH="win64"

        ui_progress_run "Prefix wird erstellt" "Erstelle $NEW_NAME ($ARCH) ..." \
            "WINEPREFIX='$TARGET' WINEARCH='$ARCH' wineboot --init"
        CHOICE="$NEW_NAME"
    fi

    local TARGET_PREFIX="$WINE_PREFIX_ROOT/$CHOICE"
    [[ -d "$TARGET_PREFIX" ]] || { ui_error "Prefix $TARGET_PREFIX existiert nicht."; return 1; }

    local RUN_MODE
    RUN_MODE=$(ui_menu "Startoptionen fuer $CHOICE" \
        "normal" "Normal starten (wine)" \
        "win32" "Als 32-bit erzwingen" \
        "winecfg" "winecfg oeffnen" \
        "explorer" "Dateimanager im Prefix (drive_c) oeffnen") || return 0
    [[ -z "$RUN_MODE" ]] && return 0

    case "$RUN_MODE" in
        winecfg)
            WINEPREFIX="$TARGET_PREFIX" winecfg
            ;;
        explorer)
            xdg-open "$TARGET_PREFIX/drive_c" 2>/dev/null || dolphin "$TARGET_PREFIX/drive_c" 2>/dev/null
            ;;
        win32)
            WINEPREFIX="$TARGET_PREFIX" WINEARCH=win32 nohup wine "$EXE" >/tmp/wine-prefix-launcher.log 2>&1 &
            ;;
        *)
            WINEPREFIX="$TARGET_PREFIX" nohup wine "$EXE" >/tmp/wine-prefix-launcher.log 2>&1 &
            ;;
    esac
}

# -----------------------------------------------------------------------------
# 2) Gaming-Daten-Zielordner (Launcher/Bibliotheken) + Migration
# -----------------------------------------------------------------------------
declare -A DATA_DIR_MAP=(
    ["Steam"]="$HOME/.local/share/Steam"
    ["Lutris"]="$HOME/.local/share/lutris"
    ["Heroic Games Launcher"]="$HOME/.config/heroic"
    ["Bottles"]="$HOME/.var/app/com.usebottles.bottles"
    ["ProtonUp-Qt Compat Tools"]="$HOME/.steam/steam/compatibilitytools.d"
)

set_gaming_install_target() {
    local NEW_DRIVE
    NEW_DRIVE=$(ui_dirsel "Zielordner fuer Gaming-Daten waehlen" "$GAMES_TARGET_DRIVE") || return 0
    [[ -z "$NEW_DRIVE" ]] && return 0

    local args=()
    for name in "${!DATA_DIR_MAP[@]}"; do
        args+=("$name" "$name (${DATA_DIR_MAP[$name]})" "off")
    done

    local SELECTED
    SELECTED=$(ui_checklist "Zielordner: $NEW_DRIVE\nProgramme vorher beenden." "${args[@]}") || return 0
    [[ -z "$SELECTED" ]] && { ui_info "Nichts ausgewaehlt."; return 0; }

    GAMES_TARGET_DRIVE="$NEW_DRIVE"
    save_config

    local LOG=""
    while IFS= read -r name; do
        [[ -z "$name" ]] && continue
        local SRC="${DATA_DIR_MAP[$name]}"
        local BASE
        BASE=$(basename "$SRC")
        local DEST="$NEW_DRIVE/$BASE"
        if [[ -L "$SRC" ]]; then
            LOG+="$name: bereits Symlink, uebersprungen\n"
            continue
        fi
        if [[ ! -e "$SRC" ]]; then
            LOG+="$name: Quellordner fehlt, uebersprungen\n"
            continue
        fi
        mkdir -p "$(dirname "$DEST")"
        rsync -a "$SRC"/ "$DEST"/ 2>>/tmp/cachy-migration.log
        rm -rf "$SRC"
        ln -s "$DEST" "$SRC"
        LOG+="$name: -> $DEST, Symlink gesetzt\n"
    done <<< "$SELECTED"
    echo -e "$LOG" > /tmp/cachy-gaming-target-result.log
    ui_textinfo_file "Launcher-Daten verschoben" /tmp/cachy-gaming-target-result.log
}

scan_installed_launchers() {
    # Frueher wurden nur sehr spezifische, hartkodierte Pfade geprueft
    # (z.B. nur ~/.local/share/Steam fuer Steam). Damit wurden Flatpak-
    # Installationen (haeufig bei Lutris/Heroic/Bottles auf vielen
    # Distributionen der Standardweg) komplett uebersehen. Zusaetzlich
    # wurde bei der Wine-Prefix-Suche verlangt, dass der Ordnername
    # "prefix" enthaelt -- die meisten Nutzer benennen ihre Prefixe aber
    # nach dem Spiel/Programm (z.B. "gothic", "default"), nicht "prefix".
    # Dadurch wurden praktisch alle Zusatz-Launcher in Wine-Prefixen nie
    # gefunden. Diese Funktion prueft jetzt sowohl native als auch
    # Flatpak-Pfade und durchsucht alle Unterordner unter WINE_PREFIX_ROOT,
    # unabhaengig vom Ordnernamen.
    local RESULTS=()

    # Steam: nativ, Flatpak, oder klassischer Symlink ~/.steam/steam
    if [[ -d "$HOME/.local/share/Steam" ]]; then
        RESULTS+=("Steam|Native|$HOME/.local/share/Steam")
    elif [[ -d "$HOME/.steam/steam" ]]; then
        RESULTS+=("Steam|Native (Symlink)|$HOME/.steam/steam")
    elif [[ -d "$HOME/.var/app/com.valvesoftware.Steam" ]]; then
        RESULTS+=("Steam|Flatpak|$HOME/.var/app/com.valvesoftware.Steam/data/Steam")
    fi

    # Lutris: nativ oder Flatpak
    if [[ -d "$HOME/.local/share/lutris" ]]; then
        RESULTS+=("Lutris|Native|$HOME/.local/share/lutris")
    elif [[ -d "$HOME/.var/app/net.lutris.Lutris" ]]; then
        RESULTS+=("Lutris|Flatpak|$HOME/.var/app/net.lutris.Lutris/data/lutris")
    fi

    # Heroic: nativ oder Flatpak (Paketname je Distro leicht abweichend)
    if [[ -d "$HOME/.config/heroic" ]]; then
        RESULTS+=("Heroic (Epic/GOG/Amazon)|Native|$HOME/.config/heroic")
    elif [[ -d "$HOME/.var/app/com.heroicgameslauncher.hgl" ]]; then
        RESULTS+=("Heroic (Epic/GOG/Amazon)|Flatpak|$HOME/.var/app/com.heroicgameslauncher.hgl/config/heroic")
    fi

    # Bottles: nativ oder Flatpak
    if [[ -d "$HOME/.local/share/bottles" ]]; then
        RESULTS+=("Bottles|Native|$HOME/.local/share/bottles")
    elif [[ -d "$HOME/.var/app/com.usebottles.bottles" ]]; then
        RESULTS+=("Bottles|Flatpak|$HOME/.var/app/com.usebottles.bottles/data/bottles")
    fi

    # Wine-Prefixe: ALLE Unterordner unter WINE_PREFIX_ROOT und $HOME/Games
    # durchsuchen (nicht nur solche mit "prefix" im Namen), erkannt an der
    # vorhandenen drive_c-Struktur.
    local search_roots=()
    [[ -n "${WINE_PREFIX_ROOT:-}" && -d "$WINE_PREFIX_ROOT" ]] && search_roots+=("$WINE_PREFIX_ROOT")
    [[ -d "$HOME/Games" ]] && search_roots+=("$HOME/Games")
    [[ -d "$HOME/.wine" ]] && search_roots+=("$HOME")

    if [[ "${#search_roots[@]}" -gt 0 ]]; then
        while IFS= read -r -d '' drive_c; do
            local prefix_dir pname
            prefix_dir=$(dirname "$drive_c")
            pname=$(basename "$prefix_dir")
            find "$drive_c" -maxdepth 4 -iname "*UbisoftConnect*" -print -quit 2>/dev/null | grep -q . && RESULTS+=("Ubisoft Connect|Wine-Prefix:$pname|$prefix_dir")
            find "$drive_c" -maxdepth 4 -iname "*GalaxyClient*" -print -quit 2>/dev/null | grep -q . && RESULTS+=("GOG Galaxy|Wine-Prefix:$pname|$prefix_dir")
            find "$drive_c" -maxdepth 5 -iname "*Rockstar Games*" -print -quit 2>/dev/null | grep -q . && RESULTS+=("Rockstar Games Launcher|Wine-Prefix:$pname|$prefix_dir")
            find "$drive_c" -maxdepth 4 -iname "*Xbox*" -print -quit 2>/dev/null | grep -q . && RESULTS+=("Xbox App|Wine-Prefix:$pname|$prefix_dir")
            find "$drive_c" -maxdepth 4 -iname "*EpicGamesLauncher*" -print -quit 2>/dev/null | grep -q . && RESULTS+=("Epic Games Store|Wine-Prefix:$pname|$prefix_dir")
            find "$drive_c" -maxdepth 4 -iname "*BattleNet*" -print -quit 2>/dev/null | grep -q . && RESULTS+=("Battle.net|Wine-Prefix:$pname|$prefix_dir")
            find "$drive_c" -maxdepth 4 -iname "*EAapp*" -o -iname "*Origin*" 2>/dev/null | grep -q . && RESULTS+=("EA App/Origin|Wine-Prefix:$pname|$prefix_dir")
        done < <(find "${search_roots[@]}" -maxdepth 3 -mindepth 1 -type d -iname "drive_c" -print0 2>/dev/null)
    fi

    printf '%s\n' "${RESULTS[@]}"
}

migration_wizard() {
    ui_info "Es werden installierte Launcher gesucht (Steam, Lutris, Heroic, Bottles sowie Ubisoft/GOG/Epic/Rockstar/Xbox in Prefixen).\nProgramme vorher beenden." || true

    mapfile -t FOUND < <(scan_installed_launchers)
    if [[ "${#FOUND[@]}" -eq 0 ]]; then
        ui_info "Keine bekannten Launcher gefunden."
        return 0
    fi

    local args=()
    for entry in "${FOUND[@]}"; do
        IFS='|' read -r name category path <<< "$entry"
        args+=("$name" "$name [$category] ($path)" "off")
    done

    local SELECTED
    SELECTED=$(ui_checklist "Auswaehlen, was verschoben werden soll:" "${args[@]}") || return 0
    [[ -z "$SELECTED" ]] && { ui_info "Nichts ausgewaehlt."; return 0; }

    local NEW_DRIVE
    NEW_DRIVE=$(ui_dirsel "Ziel-Laufwerk/Ordner waehlen" "$GAMES_TARGET_DRIVE") || return 0
    [[ -z "$NEW_DRIVE" ]] && return 0

    local LOG=""
    while IFS= read -r cname; do
        [[ -z "$cname" ]] && continue
        for entry in "${FOUND[@]}"; do
            IFS='|' read -r name category path <<< "$entry"
            [[ "$name" == "$cname" ]] || continue
            local base dest
            base=$(basename "$path")
            dest="$NEW_DRIVE/$base"
            if [[ -L "$path" ]]; then
                LOG+="$name: bereits Symlink, uebersprungen\n"
                continue
            fi
            mkdir -p "$(dirname "$dest")"
            rsync -a "$path"/ "$dest"/ 2>>/tmp/cachy-migration.log
            rm -rf "$path"
            ln -s "$dest" "$path"
            LOG+="$name ($category): -> $dest, Symlink gesetzt\n"
        done
    done <<< "$SELECTED"

    echo -e "$LOG" > /tmp/cachy-migration-summary.log
    ui_textinfo_file "Migration abgeschlossen" /tmp/cachy-migration-summary.log
}

# -----------------------------------------------------------------------------
# 3) Dateimanager/.exe-Verknuepfung
# -----------------------------------------------------------------------------
configure_file_manager() {
    require_cmd xdg-mime || return 1
    local DESKTOP_FILE="$HOME/.local/share/applications/cachy-daily-toolkit-launch.desktop"
    mkdir -p "$(dirname "$DESKTOP_FILE")"
    cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=Wine Prefix Launcher
Comment=Waehlt/erstellt einen Wine-Prefix vor dem Start einer .exe/.msi
Exec=/usr/bin/env bash -lc '~/.local/bin/cachy-daily-toolkit.sh --launch %f'
TryExec=wine
Icon=wine
Terminal=false
MimeType=application/x-ms-dos-executable;application/x-msi;
Categories=Utility;
NoDisplay=false
EOF
    update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
    xdg-mime default "$(basename "$DESKTOP_FILE")" application/x-ms-dos-executable
    xdg-mime default "$(basename "$DESKTOP_FILE")" application/x-msi
    ui_info ".exe/.msi werden nun mit dem Wine-Prefix-Dialog geoeffnet.\nBei Bedarf im Dateimanager zusaetzlich als Standardanwendung setzen."
}

# -----------------------------------------------------------------------------
# 4) Desktop-UI-Skalierung (KDE/GNOME)
# -----------------------------------------------------------------------------
adjust_ui_scaling() {
    local SCALE
    SCALE=$(ui_slider "Globale Desktop-Skalierung in %" 100 250 5 100) || return 0
    [[ -z "$SCALE" ]] && return 0
    local FACTOR
    FACTOR=$(awk "BEGIN{printf \"%.2f\", $SCALE/100}")

    if command -v kwriteconfig6 >/dev/null 2>&1; then
        kwriteconfig6 --file kdeglobals --group KScreen --key ScaleFactor "$FACTOR"
        ui_info "KDE Plasma Skalierung auf $SCALE% gesetzt. Ab- und wieder anmelden zum Uebernehmen."
    elif command -v gsettings >/dev/null 2>&1; then
        gsettings set org.gnome.desktop.interface text-scaling-factor "$FACTOR"
        ui_info "GNOME Textskalierung auf Faktor $FACTOR gesetzt."
    else
        echo "export GDK_SCALE=$FACTOR" >> "$HOME/.profile"
        ui_info "Kein KDE/GNOME erkannt. GDK_SCALE=$FACTOR wurde in ~/.profile eingetragen."
    fi
}

# -----------------------------------------------------------------------------
# 5) NVMe/SATA-Laufwerke erkennen und in fstab eintragen (UUID-basiert)
# -----------------------------------------------------------------------------
get_system_disk() {
    # Ermittelt den Namen der physischen Platte, auf der sich die
    # Root-Partition befindet (z.B. "nvme0n1"), damit diese komplett
    # (inkl. /boot, /home etc. auf derselben Platte) von der Auswahl
    # ausgeschlossen werden kann. Das verhindert versehentliche
    # fstab-Eintraege oder Reformatierungen des Systemlaufwerks.
    local root_src root_part
    root_src=$(findmnt -n -o SOURCE / 2>/dev/null)
    [[ -z "$root_src" ]] && return 0
    root_part=$(basename "$root_src")
    lsblk -no PKNAME "/dev/$root_part" 2>/dev/null | head -n1
}

detect_additional_partitions() {
    # Wichtig: lsblk setzt TRAN (Transport-Typ, z.B. "sata"/"nvme") nur auf
    # der physischen Disk-Zeile (z.B. "sda"), NICHT auf den einzelnen
    # Partitionen darunter (z.B. "sda1"). Bei Partitionen ist TRAN leer.
    # Ein Filter, der direkt auf $TRAN der Partitionszeile prueft, verwirft
    # deshalb saemtliche SATA/ATA-Partitionen (z.B. "sda1", "sdb2"), obwohl
    # die zugehoerige Disk sehr wohl TRAN="sata" trug. NVMe-Partitionen sind
    # davon oft nicht betroffen, da der Kernel dort das Attribut teils an
    # die Partition vererbt -- das erklaert, warum bisher nur NVMe erkannt
    # wurde. Fix: TRAN wird zuerst pro physischer Disk (PKNAME) ermittelt
    # und dann jeder Partition ueber ihr PKNAME zugeordnet.
    local sysdisk
    sysdisk=$(get_system_disk)

    declare -A DISK_TRAN
    local dline
    while IFS= read -r dline; do
        eval "$dline"
        [[ -n "$TRAN" ]] && DISK_TRAN["$NAME"]="$TRAN"
    done < <(lsblk -dP -o NAME,TRAN)

    local line name size fstype mountpoint type tran pkname disk_tran
    while IFS= read -r line; do
        eval "$line"
        [[ "$TYPE" != "part" ]] && continue
        [[ -n "$sysdisk" && "$PKNAME" == "$sysdisk" ]] && continue
        disk_tran="${DISK_TRAN[$PKNAME]:-}"
        [[ "$disk_tran" != "nvme" && "$disk_tran" != "sata" && "$disk_tran" != "ata" ]] && continue
        printf '%s|%s|%s|%s|%s\n' "$NAME" "$SIZE" "$FSTYPE" "$MOUNTPOINT" "$disk_tran"
    done < <(lsblk -P -o NAME,SIZE,FSTYPE,MOUNTPOINT,TYPE,PKNAME)
}

fstab_entry_exists() {
    local uuid="$1"
    grep -q "UUID=$uuid" /etc/fstab 2>/dev/null
}

manage_nvme_drives() {
    require_root_helper || return 1
    require_cmd lsblk blkid findmnt || return 1

    mapfile -t DRIVES < <(detect_additional_partitions)
    if [[ "${#DRIVES[@]}" -eq 0 ]]; then
        ui_info "Keine zusaetzlichen NVMe- oder SATA/ATA-Partitionen gefunden (Systemlaufwerk wird bewusst nicht angezeigt).\nFalls das Laufwerk noch keine Partition hat, zuerst mit gparted/fdisk partitionieren."
        return 0
    fi

    local args=()
    for d in "${DRIVES[@]}"; do
        IFS='|' read -r name size fstype mnt tran <<< "$d"
        local tran_label="$tran"
        case "$tran" in
            nvme) tran_label="NVMe" ;;
            sata) tran_label="SATA" ;;
            ata) tran_label="SATA/ATA" ;;
        esac
        local uuid_preview
        uuid_preview=$(blkid -s UUID -o value "/dev/$name" 2>/dev/null)
        [[ -z "$uuid_preview" ]] && uuid_preview=$(lsblk -no UUID "/dev/$name" 2>/dev/null)
        args+=("/dev/$name" "/dev/$name  |  $size  |  ${fstype:-kein FS}  |  ${mnt:-nicht gemountet}  |  $tran_label  |  UUID: ${uuid_preview:-unbekannt}")
    done

    local DEV
    DEV=$(ui_menu "Laufwerk waehlen (Systemlaufwerk ist ausgeblendet, auch temporaer via udisks gemountete Laufwerke werden angezeigt):" "${args[@]}") || return 0
    [[ -z "$DEV" ]] && return 0

    local CUR_FSTYPE CUR_MOUNT
    CUR_FSTYPE=$(lsblk -no FSTYPE "$DEV" 2>/dev/null)
    CUR_MOUNT=$(lsblk -no MOUNTPOINT "$DEV" 2>/dev/null | head -n1)

    # blkid liefert die UUID direkt vom Dateisystem-Superblock und ist damit
    # zuverlaessiger als lsblk (z.B. bei manchen NTFS/exFAT-Faellen). lsblk
    # dient nur als Fallback, falls blkid aus irgendeinem Grund fehlschlaegt.
    local UUID
    UUID=$(blkid -s UUID -o value "$DEV" 2>/dev/null)
    [[ -z "$UUID" ]] && UUID=$(lsblk -no UUID "$DEV" 2>/dev/null)
    if [[ -z "$UUID" ]]; then
        ui_error "Konnte keine UUID fuer $DEV ermitteln (weder blkid noch lsblk). Abbruch."
        return 1
    fi

    if fstab_entry_exists "$UUID"; then
        ui_info "Fuer $DEV (UUID=$UUID) existiert bereits ein fstab-Eintrag."
        return 0
    fi

    local IS_TEMP_AUTOMOUNT="no"
    [[ -n "$CUR_MOUNT" && "$CUR_MOUNT" == /run/media/* ]] && IS_TEMP_AUTOMOUNT="yes"

    local FS_ACTION="use_existing"
    if [[ -z "$CUR_FSTYPE" ]]; then
        ui_question "Auf $DEV wurde kein Dateisystem gefunden. Jetzt formatieren?" || return 0
        FS_ACTION="format"
    elif [[ "$IS_TEMP_AUTOMOUNT" == "yes" ]]; then
        ui_question "$DEV ist aktuell temporaer via udisks2 unter\n$CUR_MOUNT eingebunden.\n\nBestehendes Dateisystem ($CUR_FSTYPE) fuer einen dauerhaften fstab-Eintrag uebernehmen? (Nein = neu formatieren)"
        [[ $? -ne 0 ]] && FS_ACTION="format"
    else
        ui_question "$DEV enthaelt bereits: $CUR_FSTYPE\n\nUebernehmen (nur fstab eintragen) oder neu formatieren (ALLE DATEN WERDEN GELOESCHT)? (Nein = neu formatieren)"
        [[ $? -ne 0 ]] && FS_ACTION="format"
    fi

    local FSTYPE="$CUR_FSTYPE"
    if [[ "$FS_ACTION" == "format" ]]; then
        FSTYPE=$(ui_menu "Dateisystem waehlen" "ext4" "ext4 (empfohlen)" "btrfs" "btrfs" "xfs" "xfs") || return 0
        [[ -z "$FSTYPE" ]] && return 0

        ui_question "ACHTUNG: $DEV wird komplett geloescht und als $FSTYPE formatiert. Fortfahren?" || return 0

        if [[ -n "$CUR_MOUNT" ]]; then
            run_as_root "umount '$DEV'" || { ui_error "Konnte $DEV nicht aushaengen (aktuell unter $CUR_MOUNT)."; return 1; }
            CUR_MOUNT=""
        fi

        local MKFS_CMD=""
        case "$FSTYPE" in
            ext4) MKFS_CMD="mkfs.ext4 -F -L nvme_data $DEV" ;;
            btrfs) MKFS_CMD="mkfs.btrfs -f -L nvme_data $DEV" ;;
            xfs) MKFS_CMD="mkfs.xfs -f -L nvme_data $DEV" ;;
        esac
        ui_progress_run "Formatiere" "Formatiere $DEV als $FSTYPE ..." "$(printf 'pkexec bash -c "%s" 2>/dev/null || sudo bash -c "%s"' "$MKFS_CMD" "$MKFS_CMD")"

        UUID=$(blkid -s UUID -o value "$DEV" 2>/dev/null)
        [[ -z "$UUID" ]] && UUID=$(lsblk -no UUID "$DEV" 2>/dev/null)
        [[ -z "$UUID" ]] && { ui_error "Konnte nach dem Formatieren keine UUID fuer $DEV ermitteln (blkid+lsblk)."; return 1; }
        if fstab_entry_exists "$UUID"; then
            ui_info "Fuer $DEV (neue UUID=$UUID) existiert bereits ein fstab-Eintrag."
            return 0
        fi
    fi

    # Vorschlag fuer den Ordnernamen aus dem Geraetenamen ableiten, damit im
    # Regelfall nur noch das Basisverzeichnis bestaetigt werden muss.
    local SUGGESTED_NAME
    SUGGESTED_NAME=$(basename "$DEV")

    local MOUNT_BASE
    MOUNT_BASE=$(ui_entry "In welchem Basisverzeichnis soll der Mountpoint angelegt werden?" "/mnt") || return 0
    [[ -z "$MOUNT_BASE" ]] && return 0
    # Trailing Slash(es) entfernen, damit z.B. "/mnt/" nicht zu einem
    # doppelten Slash beim Zusammensetzen fuehrt.
    MOUNT_BASE="${MOUNT_BASE%/}"
    [[ -z "$MOUNT_BASE" ]] && MOUNT_BASE="/"

    local MOUNT_NAME
    MOUNT_NAME=$(ui_entry "Name des Mountpoint-Ordners (wird unter $MOUNT_BASE angelegt):" "$SUGGESTED_NAME") || return 0
    [[ -z "$MOUNT_NAME" ]] && return 0
    # Fuehrende/abschliessende Slashes im Namen entfernen -- verhindert
    # Doppelpfade wie /mnt/ssd_DATA/ + ssd_DATA -> /mnt/ssd_DATA/ssd_DATA,
    # falls versehentlich ein kompletter Pfad statt nur eines Namens
    # eingegeben wurde.
    MOUNT_NAME="${MOUNT_NAME#/}"
    MOUNT_NAME="${MOUNT_NAME%/}"

    local MOUNTPOINT="$MOUNT_BASE/$MOUNT_NAME"
    # Doppelte Slashes (z.B. durch vorherige Eingabefehler) zusammenfassen.
    while [[ "$MOUNTPOINT" == *//* ]]; do
        MOUNTPOINT="${MOUNTPOINT//\/\//\/}"
    done

    # Falls der Nutzer im Basisverzeichnis bereits den vollstaendigen Pfad
    # inkl. Ordnernamen eingegeben hat (z.B. "/mnt/ssd_DATA/" als Basis UND
    # "ssd_DATA" als Name), wuerde das sonst zu einer Verdopplung fuehren.
    # Erkennung: letzte Pfadkomponente von MOUNT_BASE == MOUNT_NAME.
    if [[ "$(basename "$MOUNT_BASE")" == "$MOUNT_NAME" ]]; then
        MOUNTPOINT="$MOUNT_BASE"
    fi

    local CONFIRM_MOUNTPOINT
    CONFIRM_MOUNTPOINT=$(ui_entry "Finaler Mountpoint-Pfad (kann hier noch angepasst werden):" "$MOUNTPOINT") || return 0
    [[ -z "$CONFIRM_MOUNTPOINT" ]] && return 0
    MOUNTPOINT="${CONFIRM_MOUNTPOINT%/}"
    [[ -z "$MOUNTPOINT" ]] && MOUNTPOINT="/"

    # Ziel-fstype fuer den fstab-Eintrag kann vom erkannten Dateisystem
    # abweichen (z.B. NTFS: Treiberwahl ntfs3 vs. ntfs-3g).
    local FSTAB_FSTYPE="$FSTYPE"
    local MOUNT_OPTS DUMP_PASS
    local CUR_UID CUR_GID
    CUR_UID=$(id -u)
    CUR_GID=$(id -g)

    case "$FSTYPE" in
        ext4)
            # Arch Wiki/SSD-Empfehlung: kein dauerhaftes 'discard' auf ext4,
            # da dies bei manchen SSDs Performance/Verschleiss verschlechtern
            # kann. Stattdessen periodisches TRIM via fstrim.timer nutzen.
            MOUNT_OPTS="defaults,noatime"
            DUMP_PASS="0 2"
            ;;
        btrfs)
            # btrfs empfiehlt discard=async explizit als sichere Variante
            # (asynchrones TRIM statt synchron bei jedem delete).
            MOUNT_OPTS="defaults,noatime,compress=zstd:1,ssd,discard=async"
            DUMP_PASS="0 0"
            ;;
        xfs)
            MOUNT_OPTS="defaults,noatime"
            DUMP_PASS="0 2"
            ;;
        ntfs)
            local NTFS_DRIVER
            NTFS_DRIVER=$(ui_menu "NTFS-Treiber waehlen (z.B. Windows-Dualboot-Partition)" \
                "ntfs3" "ntfs3 (Kernel-Treiber, seit Kernel 5.15, empfohlen)" \
                "ntfs-3g" "ntfs-3g (FUSE-Treiber, aeltere Kompatibilitaet)") || NTFS_DRIVER="ntfs3"
            [[ -z "$NTFS_DRIVER" ]] && NTFS_DRIVER="ntfs3"
            FSTAB_FSTYPE="$NTFS_DRIVER"
            # uid/gid setzen den Linux-Besitzer der Dateien, da NTFS selbst
            # keine POSIX-Rechte kennt. windows_names verhindert unter dem
            # ntfs3-Treiber das Anlegen von Dateinamen, die Windows nicht
            # erlaubt (nur sinnvoll bei ntfs3, ntfs-3g kennt die Option nicht).
            if [[ "$NTFS_DRIVER" == "ntfs3" ]]; then
                MOUNT_OPTS="defaults,uid=$CUR_UID,gid=$CUR_GID,windows_names,noatime"
            else
                MOUNT_OPTS="defaults,uid=$CUR_UID,gid=$CUR_GID,noatime"
            fi
            # NTFS unterstuetzt kein fsck durch den Linux-Pass-Mechanismus.
            DUMP_PASS="0 0"
            ;;
        vfat|exfat)
            MOUNT_OPTS="defaults,uid=$CUR_UID,gid=$CUR_GID,umask=022,noatime"
            DUMP_PASS="0 0"
            ;;
        *)
            MOUNT_OPTS="defaults,noatime"
            DUMP_PASS="0 2"
            ;;
    esac

    local CUSTOM_OPTS
    CUSTOM_OPTS=$(ui_entry "Mount-Optionen fuer $FSTAB_FSTYPE (editierbar, Empfehlung vorausgefuellt):" "$MOUNT_OPTS") || return 0
    [[ -n "$CUSTOM_OPTS" ]] && MOUNT_OPTS="$CUSTOM_OPTS"

    local FSTAB_LINE="UUID=$UUID  $MOUNTPOINT  $FSTAB_FSTYPE  $MOUNT_OPTS  $DUMP_PASS"
    echo -e "Folgender Eintrag wird zu /etc/fstab hinzugefuegt:\n\n$FSTAB_LINE" > /tmp/cachy-fstab-preview.log
    ui_textinfo_file "fstab-Eintrag" /tmp/cachy-fstab-preview.log

    ui_question "Eintrag wirklich hinzufuegen und Laufwerk dauerhaft unter $MOUNTPOINT mounten?" || return 0

    CUR_MOUNT=$(lsblk -no MOUNTPOINT "$DEV" 2>/dev/null | head -n1)
    if [[ -n "$CUR_MOUNT" && "$CUR_MOUNT" != "$MOUNTPOINT" ]]; then
        run_as_root "umount '$DEV'" 2>/dev/null
    fi

    # Mountpoint wird als root angelegt (mkdir braeuchte sonst ggf. Rechte
    # im Elternverzeichnis, z.B. direkt unter /mnt). Fuer POSIX-Dateisysteme
    # (ext4/btrfs/xfs) wird der Mountpoint nach dem Mounten zusaetzlich dem
    # aktuellen Benutzer uebertragen (chown), damit der Dateimanager beim
    # Oeffnen keine "Keine Berechtigung"-Meldung anzeigt. Bei NTFS/vfat/exfat
    # ist das nicht noetig, da dort bereits uid=/gid= in den Mount-Optionen
    # gesetzt wurde.
    local NEEDS_CHOWN="no"
    case "$FSTAB_FSTYPE" in
        ext4|btrfs|xfs) NEEDS_CHOWN="yes" ;;
    esac

    local CMD="mkdir -p '$MOUNTPOINT' && cp /etc/fstab /etc/fstab.bak.\$(date +%Y%m%d%H%M%S) && echo '$FSTAB_LINE' >> /etc/fstab && systemctl daemon-reload && mount '$MOUNTPOINT'"
    if [[ "$NEEDS_CHOWN" == "yes" ]]; then
        CMD="$CMD && chown $CUR_UID:$CUR_GID '$MOUNTPOINT'"
    fi

    run_as_root "$CMD"
    if [[ $? -eq 0 ]]; then
        if [[ "$NEEDS_CHOWN" == "yes" ]]; then
            ui_info "Erfolgreich dauerhaft eingebunden:\n$DEV (UUID=$UUID) -> $MOUNTPOINT\nOwner von $MOUNTPOINT wurde auf $USER gesetzt.\nBackup von /etc/fstab wurde erstellt."
        else
            ui_info "Erfolgreich dauerhaft eingebunden:\n$DEV (UUID=$UUID) -> $MOUNTPOINT\nBackup von /etc/fstab wurde erstellt."
        fi
    else
        ui_error "Fehler beim Mounten. /etc/fstab wurde geaendert, bitte pruefen (Backup vorhanden)."
        return 1
    fi
}

# -----------------------------------------------------------------------------
# 6) Benutzereigenen Ordner auf Laufwerk anlegen
# -----------------------------------------------------------------------------
create_user_owned_folder() {
    local BASE_MOUNT
    BASE_MOUNT=$(ui_dirsel "Gemountetes Laufwerk/Verzeichnis waehlen" "/mnt") || return 0
    [[ -z "$BASE_MOUNT" ]] && return 0

    if ! findmnt -T "$BASE_MOUNT" >/dev/null 2>&1; then
        ui_error "$BASE_MOUNT ist kein gueltiger Mountpoint."
        return 1
    fi

    local FOLDER_NAME
    FOLDER_NAME=$(ui_entry "Name des neuen Ordners:" "Steam") || return 0
    [[ -z "$FOLDER_NAME" ]] && return 0

    local FULL_PATH="$BASE_MOUNT/$FOLDER_NAME"
    [[ -e "$FULL_PATH" ]] && { ui_error "$FULL_PATH existiert bereits."; return 1; }

    require_root_helper || return 1
    local CUR_UID CUR_GID
    CUR_UID=$(id -u); CUR_GID=$(id -g)
    run_as_root "mkdir -p '$FULL_PATH' && chown -R $CUR_UID:$CUR_GID '$FULL_PATH' && chmod 755 '$FULL_PATH'"

    if [[ $? -eq 0 ]]; then
        ui_info "Ordner angelegt: $FULL_PATH (Owner: $USER)"
    else
        ui_error "Fehler beim Anlegen des Ordners."
        return 1
    fi
}

# -----------------------------------------------------------------------------
# 7) UI-Skalierung fuer Wine/X11-Anwendungen im Prefix (DPI)
# -----------------------------------------------------------------------------
wine_prefix_ui_scaling() {
    require_cmd wine || return 1
    mapfile -t EXISTING < <(list_prefixes)
    if [[ "${#EXISTING[@]}" -eq 0 ]]; then
        ui_info "Keine Prefixe unter $WINE_PREFIX_ROOT gefunden."
        return 0
    fi
    local args=()
    for p in "${EXISTING[@]}"; do
        [[ -n "$p" ]] && args+=("$p" "$p")
    done

    local CHOICE
    CHOICE=$(ui_menu "Prefix fuer UI-Skalierung waehlen:" "${args[@]}") || return 0
    [[ -z "$CHOICE" ]] && return 0

    local DPI
    DPI=$(ui_slider "Windows-DPI (96=100%, 120=125%, 144=150%):" 96 240 12 120) || return 0
    [[ -z "$DPI" ]] && return 0

    local TARGET_PREFIX="$WINE_PREFIX_ROOT/$CHOICE"
    [[ -d "$TARGET_PREFIX" ]] || { ui_error "Prefix $TARGET_PREFIX existiert nicht."; return 1; }

    WINEPREFIX="$TARGET_PREFIX" wine reg add "HKCU\\Control Panel\\Desktop" \
        /v LogPixels /t REG_DWORD /d "$DPI" /f >/tmp/wine-dpi.log 2>&1

    ui_info "DPI fuer Prefix $CHOICE auf $DPI gesetzt.\nNeustart der jeweiligen Anwendung empfohlen."
}

# -----------------------------------------------------------------------------
# 8) ProtonPlus / Compat-Layer Helfer
# -----------------------------------------------------------------------------
compat_layer_helper() {
    local ACTION
    ACTION=$(ui_menu "Compatibility-Layer Helfer" \
        "launch" "ProtonPlus starten" \
        "install" "ProtonPlus installieren/aktualisieren (Flatpak bevorzugt)" \
        "paths" "Wichtige Compat-Tool-Pfade anzeigen") || return 0

    case "$ACTION" in
        launch)
            if command -v protonplus >/dev/null 2>&1; then
                nohup protonplus >/tmp/protonplus.log 2>&1 &
            elif command -v flatpak >/dev/null 2>&1 && flatpak info com.vysp3r.ProtonPlus >/dev/null 2>&1; then
                nohup flatpak run com.vysp3r.ProtonPlus >/tmp/protonplus.log 2>&1 &
            else
                ui_error "ProtonPlus wurde nicht gefunden. Nutze die Installationsoption in diesem Menue."
                return 1
            fi
            ;;
        install)
            if command -v flatpak >/dev/null 2>&1; then
                ui_question "ProtonPlus per Flatpak installieren/aktualisieren?" || return 0
                if flatpak remote-list 2>/dev/null | grep -q flathub; then
                    ui_progress_run "ProtonPlus" "Installiere/aktualisiere ProtonPlus via Flatpak ..." \
                        "flatpak install -y flathub com.vysp3r.ProtonPlus || flatpak update -y com.vysp3r.ProtonPlus"
                else
                    ui_progress_run "Flathub hinzufuegen" "Fuege Flathub hinzu und installiere ProtonPlus ..." \
                        "flatpak remote-add --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo && flatpak install -y flathub com.vysp3r.ProtonPlus"
                fi
                ui_info "ProtonPlus kann jetzt ueber dieses Menue gestartet werden."
            else
                ui_error "Flatpak ist nicht installiert. Bitte zuerst flatpak installieren."
            fi
            ;;
        paths)
            {
                echo "Wichtige Compatibility-Tool-Pfade:";
                echo "--------------------------------";
                echo "Steam custom tools: $HOME/.steam/steam/compatibilitytools.d";
                echo "Lutris runners:     $HOME/.local/share/lutris/runners";
                echo "Heroic config:      $HOME/.config/heroic";
                echo "Wine prefix root:   $WINE_PREFIX_ROOT";
            } > /tmp/cachy-compat-paths.log
            ui_textinfo_file "Compatibility-Layer Pfade" /tmp/cachy-compat-paths.log
            ;;
    esac
}

# -----------------------------------------------------------------------------
# 9) Journal-/Log-Helfer
# -----------------------------------------------------------------------------
log_helper() {
    local ACTION
    ACTION=$(ui_menu "Logs & Diagnose" \
        "boot" "Journal des aktuellen Boots anzeigen" \
        "errors" "Nur Warnungen/Fehler des aktuellen Boots" \
        "nvidia" "Kernel-/Journal-Meldungen zu NVIDIA filtern" \
        "gamescope" "Meldungen zu gamescope/Steam/Vulkan filtern") || return 0

    case "$ACTION" in
        boot)
            journalctl -b > /tmp/cachy-journal-boot.log 2>&1
            ui_textinfo_file "Journal aktueller Boot" /tmp/cachy-journal-boot.log
            ;;
        errors)
            journalctl -b -p warning > /tmp/cachy-journal-errors.log 2>&1
            ui_textinfo_file "Warnungen/Fehler aktueller Boot" /tmp/cachy-journal-errors.log
            ;;
        nvidia)
            journalctl -b | grep -Ei 'nvidia|nvrm|drm|gsp' > /tmp/cachy-journal-nvidia.log 2>&1
            [[ -s /tmp/cachy-journal-nvidia.log ]] || echo "Keine NVIDIA-bezogenen Treffer gefunden." > /tmp/cachy-journal-nvidia.log
            ui_textinfo_file "NVIDIA-relevante Journal-Eintraege" /tmp/cachy-journal-nvidia.log
            ;;
        gamescope)
            journalctl -b | grep -Ei 'gamescope|steam|pressure-vessel|vulkan' > /tmp/cachy-journal-gamescope.log 2>&1
            [[ -s /tmp/cachy-journal-gamescope.log ]] || echo "Keine gamescope/Steam-relevanten Treffer gefunden." > /tmp/cachy-journal-gamescope.log
            ui_textinfo_file "gamescope/Steam-relevante Journal-Eintraege" /tmp/cachy-journal-gamescope.log
            ;;
    esac
}

# -----------------------------------------------------------------------------
# 8) GPU-Schnellstatus (nvidia-smi)
# -----------------------------------------------------------------------------
gpu_status() {
    if ! command -v nvidia-smi >/dev/null 2>&1; then
        ui_error "nvidia-smi nicht gefunden. NVIDIA-Treiber installiert?"
        return 1
    fi
    nvidia-smi --query-gpu=name,temperature.gpu,utilization.gpu,memory.used,memory.total,power.draw,clocks.gr,clocks.mem \
        --format=csv,noheader > /tmp/cachy-gpu-status.log 2>&1
    {
        echo "GPU-Status (nvidia-smi):"
        echo "Name, Temp(C), Auslastung(%), VRAM genutzt, VRAM gesamt, Leistung(W), GPU-Takt(MHz), VRAM-Takt(MHz)"
        echo "-----------------------------------------------------------------"
        cat /tmp/cachy-gpu-status.log
    } > /tmp/cachy-gpu-status-formatted.log
    ui_textinfo_file "RTX 4090 - GPU-Status" /tmp/cachy-gpu-status-formatted.log
}

# -----------------------------------------------------------------------------
# 9) Wine-Prefix Backup/Restore
# -----------------------------------------------------------------------------
prefix_backup_restore() {
    require_cmd tar || return 1
    local ACTION
    ACTION=$(ui_menu "Wine-Prefix Backup/Restore" \
        "backup" "Prefix sichern (tar, Symlinks aufgeloest)" \
        "restore" "Prefix aus Backup wiederherstellen") || return 0

    case "$ACTION" in
        backup)
            mapfile -t EXISTING < <(list_prefixes)
            if [[ "${#EXISTING[@]}" -eq 0 ]]; then
                ui_info "Keine Prefixe gefunden."
                return 0
            fi
            local args=()
            for p in "${EXISTING[@]}"; do args+=("$p" "$p"); done
            local CHOICE
            CHOICE=$(ui_menu "Zu sichernden Prefix waehlen:" "${args[@]}") || return 0
            [[ -z "$CHOICE" ]] && return 0

            local SRC="$WINE_PREFIX_ROOT/$CHOICE"
            local TS
            TS=$(date +%Y%m%d-%H%M%S)
            local ARCHIVE="$PREFIX_BACKUP_DIR/${CHOICE}-${TS}.tar.gz"
            mkdir -p "$PREFIX_BACKUP_DIR"

            ui_progress_run "Sichere $CHOICE" "Erstelle Backup nach $ARCHIVE ..." \
                "tar -czhf '$ARCHIVE' -C '$WINE_PREFIX_ROOT' '$CHOICE'"

            if [[ -f "$ARCHIVE" ]]; then
                ui_info "Backup erstellt:\n$ARCHIVE"
            else
                ui_error "Backup fehlgeschlagen."
            fi
            ;;
        restore)
            mapfile -t BACKUPS < <(find "$PREFIX_BACKUP_DIR" -maxdepth 1 -name "*.tar.gz" -printf "%f\n" 2>/dev/null | sort)
            if [[ "${#BACKUPS[@]}" -eq 0 ]]; then
                ui_info "Keine Backups unter $PREFIX_BACKUP_DIR gefunden."
                return 0
            fi
            local args=()
            for b in "${BACKUPS[@]}"; do args+=("$b" "$b"); done
            local CHOICE
            CHOICE=$(ui_menu "Backup waehlen:" "${args[@]}") || return 0
            [[ -z "$CHOICE" ]] && return 0

            ui_question "Backup $CHOICE nach $WINE_PREFIX_ROOT entpacken? Vorhandene gleichnamige Prefixe werden ueberschrieben." || return 0
            tar -xzf "$PREFIX_BACKUP_DIR/$CHOICE" -C "$WINE_PREFIX_ROOT"
            if [[ $? -eq 0 ]]; then
                ui_info "Wiederherstellung abgeschlossen."
            else
                ui_error "Fehler bei der Wiederherstellung."
            fi
            ;;
    esac
}

# -----------------------------------------------------------------------------
# 10) Shader-Cache leeren (DXVK/VKD3D/Steam)
# -----------------------------------------------------------------------------
clear_shader_cache() {
    local args=(
        "steam" "Steam Shader-Cache (~/.local/share/Steam/steamapps/shadercache)" "off"
        "dxvk" "DXVK-Cache in allen Wine-Prefixen (*.dxvk-cache)" "off"
        "mesa" "Mesa Shader-Cache (~/.cache/mesa_shader_cache*)" "off"
    )
    local SELECTED
    SELECTED=$(ui_checklist "Welche Shader-Caches sollen geleert werden?" "${args[@]}") || return 0
    [[ -z "$SELECTED" ]] && { ui_info "Nichts ausgewaehlt."; return 0; }

    local LOG=""
    while IFS= read -r opt; do
        case "$opt" in
            steam)
                local d="$HOME/.local/share/Steam/steamapps/shadercache"
                if [[ -d "$d" ]]; then
                    local size_before
                    size_before=$(du -sh "$d" 2>/dev/null | cut -f1)
                    rm -rf "$d"/*
                    LOG+="Steam Shader-Cache geleert (vorher: $size_before)\n"
                else
                    LOG+="Steam Shader-Cache-Ordner nicht gefunden\n"
                fi
                ;;
            dxvk)
                local count=0
                while IFS= read -r -d '' f; do
                    rm -f "$f"
                    ((count++))
                done < <(find "$WINE_PREFIX_ROOT" -name "*.dxvk-cache" -print0 2>/dev/null)
                LOG+="$count DXVK-Cache-Dateien geloescht\n"
                ;;
            mesa)
                find "$HOME/.cache" -maxdepth 1 -iname "mesa_shader_cache*" -exec rm -rf {} \; 2>/dev/null
                LOG+="Mesa Shader-Cache geleert\n"
                ;;
        esac
    done <<< "$SELECTED"

    echo -e "$LOG" > /tmp/cachy-shadercache-result.log
    ui_textinfo_file "Shader-Cache Ergebnis" /tmp/cachy-shadercache-result.log
}

# -----------------------------------------------------------------------------
# 11) Speicherplatz-Uebersicht (Gaming-Ordner)
# -----------------------------------------------------------------------------
disk_usage_overview() {
    local paths=(
        "$HOME/.local/share/Steam"
        "$HOME/.local/share/lutris"
        "$HOME/.config/heroic"
        "$HOME/.var/app/com.usebottles.bottles"
        "$WINE_PREFIX_ROOT"
        "$GAMES_TARGET_DRIVE"
        "$PREFIX_BACKUP_DIR"
    )
    {
        echo "Speicherplatz-Uebersicht Gaming-relevanter Ordner:"
        echo "---------------------------------------------------"
        for p in "${paths[@]}"; do
            if [[ -e "$p" ]]; then
                du -sh "$p" 2>/dev/null
            else
                echo "0	$p (nicht vorhanden)"
            fi
        done
    } > /tmp/cachy-disk-usage.log
    ui_textinfo_file "Speicherplatz-Uebersicht" /tmp/cachy-disk-usage.log
}

# -----------------------------------------------------------------------------
# 12) System-Update inkl. DKMS-Check
# -----------------------------------------------------------------------------
system_update_check() {
    require_root_helper || return 1
    ui_question "System jetzt aktualisieren (pacman -Syu) und DKMS-Status pruefen?" || return 0

    ui_progress_run "System-Update" "pacman -Syu laeuft ..." \
        "pkexec pacman -Syu --noconfirm > /tmp/cachy-update.log 2>&1 || sudo pacman -Syu --noconfirm > /tmp/cachy-update.log 2>&1"

    {
        echo "=== pacman -Syu Log (Auszug, letzte 40 Zeilen) ==="
        tail -n 40 /tmp/cachy-update.log
        echo ""
        echo "=== DKMS-Status ==="
        if command -v dkms >/dev/null 2>&1; then
            dkms status
        else
            echo "dkms nicht installiert."
        fi
    } > /tmp/cachy-update-summary.log

    ui_textinfo_file "Update-Zusammenfassung" /tmp/cachy-update-summary.log
}

# -----------------------------------------------------------------------------
# Hauptmenue (kategorisiert)
# -----------------------------------------------------------------------------
main_menu() {
    while true; do
        local CHOICE
        CHOICE=$(ui_menu "CachyOS Alltagstool - Kategorie waehlen:"             "wine" "Wine/Prefixe: starten, sichern, DPI, .exe-Verknuepfung"             "launcher" "Launcher & Speicherorte: Migration, Zielordner, ProtonPlus"             "storage" "Laufwerke: NVMe/SATA einbinden, Ordner anlegen, Speicherplatz"             "system" "System: Update+DKMS, GPU-Status, Logs, UI-Skalierung, Fenstergroesse, Cache"             "Beenden" "Programm beenden") || return 0

        case "$CHOICE" in
            wine)
                local SUB
                SUB=$(ui_menu "Wine/Prefixe"                     "launch" ".exe/.msi mit Wine-Prefix starten"                     "dpi" "UI-Skalierung fuer Wine/X11-Anwendungen im Prefix"                     "backup" "Prefix sichern/wiederherstellen"                     "mime" "Dateimanager/.exe-Verknuepfung konfigurieren"                     "back" "Zurueck") || continue
                case "$SUB" in
                    launch) launch_exe ;;
                    dpi) wine_prefix_ui_scaling ;;
                    backup) prefix_backup_restore ;;
                    mime) configure_file_manager ;;
                esac
                ;;
            launcher)
                local SUB
                SUB=$(ui_menu "Launcher & Speicherorte"                     "target" "Gaming-Daten-Zielordner festlegen"                     "migrate" "Migrationswizard: Launcher verschieben"                     "compat" "ProtonPlus / Compat-Layer Helfer"                     "back" "Zurueck") || continue
                case "$SUB" in
                    target) set_gaming_install_target ;;
                    migrate) migration_wizard ;;
                    compat) compat_layer_helper ;;
                esac
                ;;
            storage)
                local SUB
                SUB=$(ui_menu "Laufwerke"                     "nvme" "NVMe/SATA-Laufwerke erkennen und in fstab eintragen"                     "folder" "Benutzereigenen Ordner auf Laufwerk anlegen"                     "usage" "Speicherplatz-Uebersicht (Gaming-Ordner)"                     "back" "Zurueck") || continue
                case "$SUB" in
                    nvme) manage_nvme_drives ;;
                    folder) create_user_owned_folder ;;
                    usage) disk_usage_overview ;;
                esac
                ;;
            system)
                local SUB
                SUB=$(ui_menu "System"                     "update" "System-Update + DKMS-Check"                     "gpu" "GPU-Schnellstatus (nvidia-smi)"                     "logs" "Journal-/Log-Helfer"                     "scaling" "Desktop-UI-Skalierung (KDE/GNOME)"                     "toolscale" "Fenstergroesse dieses Tools aendern"                     "cache" "Shader-Cache leeren"                     "back" "Zurueck") || continue
                case "$SUB" in
                    update) system_update_check ;;
                    gpu) gpu_status ;;
                    logs) log_helper ;;
                    scaling) adjust_ui_scaling ;;
                    toolscale) adjust_tool_scaling ;;
                    cache) clear_shader_cache ;;
                esac
                ;;
            Beenden|"") return 0 ;;
        esac
    done
}

if [[ "${1:-}" == "--launch" ]]; then
    shift
    launch_exe "${1:-}"
else
    main_menu
fi
