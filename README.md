# CachyOS DailyTool v5

**English:** [README.en.md](README.en.md)

Ein Alltagswerkzeug für **CachyOS / Arch Linux** mit einer echten grafischen Oberfläche
(Python, GTK4, libadwaita) statt der bisherigen kdialog-/zenity-Dialogketten. Alle Funktionen
sind direkt per Klick erreichbar: links die Seitenleiste mit Bereichen und Zählern, rechts die
jeweilige Seite mit Formularen, Listen, Live-Werten und einem Protokollfenster für laufende Aktionen.
Dunkles Erscheinungsbild in Anthrazit mit Kupfer-Akzent, Icon aus dem CueSystem-Logo.
Die Oberfläche gibt es auf **Deutsch und Englisch**, umschaltbar über das Sprach-Symbol in der Kopfzeile (siehe [Sprache](#sprache)).

Bis v5.0.4 hieß das Projekt „CachyOS Alltagstool“ (Paket und Befehl `cachy-toolkit`). Seit v5.1.0 heißen Paket
und Befehl `cachyos-dailytool`; das neue Paket ersetzt das alte, der Installer räumt die alte Installation weg und
vorhandene App-Verknüpfungen werden beim ersten Start auf den neuen Befehl umgeschrieben.

Die Bash-Version v4 liegt unverändert unter `legacy/` und wird vom Installer ersetzt;
die Konfiguration (`~/.config/cachy-daily-toolkit/config.env`) wird übernommen.

## Oberfläche

```
ÜBERSICHT                 Übersicht: GPU, RAM, Laufzeit, Updates, Prefixe, Laufwerke · Schnellzugriff · Hinweise · Systemdaten
WINE & PREFIXE            Launcher · Programm starten · Prefixe verwalten
LAUNCHER & SPEICHERORTE   Speicherorte & Migration · ProtonPlus & Compat-Tools · Speicherplatz
LAUFWERKE                 Laufwerke einbinden · Ordner anlegen
SYSTEM                    Update & DKMS · GPU-Status · X3D-Kerne (CCD) · Logs & Diagnose · Shader-Cache leeren · Skalierung
```

Zähler in der Seitenleiste: Anzahl Wine-Prefixe, Anzahl zusätzlicher Partitionen (kupfer, wenn
eine noch keinen fstab-Eintrag hat), Anzahl verfügbarer Paket-Updates. Kopfzeile: Sprache
(Symbol mit DE/EN – ein Klick auf eine Sprache startet das Programm in dieser Sprache neu), Seite aktualisieren (Strg+R),
Menü (Einstellungen Strg+, · Protokollordner · Über · Deinstallieren · Beenden Strg+Q).
Die Fenstergröße wird beim Schließen gespeichert; die zuletzt geöffnete Seite ebenfalls.

## Funktionen je Seite

| Seite | Was sie tut |
|---|---|
| **Übersicht** | Kacheln mit Live-Werten (GPU-Name, Temperatur, Last, VRAM · RAM-Belegung · Laufzeit · Updates · Prefixe · Laufwerke), Schnellzugriff auf die wichtigsten Seiten, Hinweise (temporär gemountete oder nicht eingebundene Laufwerke, Kernel-Neustart, fehlendes ProtonPlus, Updates), Systemdaten. |
| **Launcher** | Assistent für bekannte Windows-Launcher: **Ubisoft Connect, EA App, Epic Games Launcher, GOG Galaxy 2.0 (vollständiger Offline-Installer, aktuelle Version über GOGs Versionsabfrage, MD5-geprüft), Battle.net, Rockstar Games Launcher, Amazon Games**. Steam (Windows) fehlt bewusst: Proton ersetzt dessen `steam.exe` zwingend durch seine eingebaute Version. Ein Klick auf „Installieren …“ fragt Prefix (neu oder vorhanden), Speicherort, Runner (Proton über umu, Proton direkt, Wine), Startoptionen mit GOverlay-Übernahme, Programmargumente, Umgebungsvariablen und Verknüpfungen (Startmenü, Schreibtisch) ab, lädt dann den **offiziellen Installer** mit Fortschritt (Zwischenspeicher `~/.cache/cachy-daily-toolkit/installers`), legt den Prefix an, installiert (unbeaufsichtigt, wo das zuverlässig geht) und **legt die Verknüpfung automatisch an**, sobald das neu installierte Hauptprogramm existiert – auch wenn sich der Launcher nach der Installation selbst öffnet. Je Launcher sind belegte Proton-Einstellungen hinterlegt (z. B. `PROTON_ENABLE_WAYLAND=0` – die Chromium-/CEF-Oberflächen der Store-Launcher bleiben unter dem Wayland-Treiber weiß; GOG Galaxy läuft trotzdem unter Wayland, Ubisoft Connect und Rockstar mit GE-Proton ab 11-6, siehe unten –, Battle.net `WINE_SIMULATE_WRITECOPY=1`), der empfohlene Runner ist vorausgewählt; Ubisoft Connect bekommt vorab eine Einstellungsdatei (Overlay aus, Schließen beendet den Client). Bei einem Speicherort auf NTFS/exFAT warnt der Assistent. Installierte Launcher werden in **allen Prefixen erkannt**, auch manuell installierte; ein Klick legt dafür die Verknüpfung an. Je installiertem Launcher: Starten, Einstellungen (Runner, Startoptionen, Variablen, Verknüpfungen), **Deinstallieren**. |
| **Programm starten** | .exe/.msi wählen oder per Drag & Drop ablegen, Prefix wählen oder neu anlegen. **Runner** je Start wählbar (Wie Prefix, Proton über umu, Proton direkt, Wine). **Startoptionen in Steam-Syntax** (`mangohud gamemoderun %command% -windowed`, Umgebungsvariablen vorne), Vorlagen, **„Aus GOverlay übernehmen“** (leitet die Zeile aus der GOverlay-/MangoHud-Konfiguration ab) und „GOverlay öffnen“, zusätzliche Umgebungsvariablen, **Befehlsvorschau** des vollständigen Startbefehls. **Installieren …** öffnet den Installations-Dialog (siehe unten). winecfg, winetricks, drive_c, Dateimanager-Verknüpfung für .exe/.msi. |
| **Installieren …** | Fragt vor jeder Installation: Name der Anwendung, **Zielordner** (Standardordner, weitere Speicherorte oder beliebiger anderer Ordner – wird bei Bedarf angelegt) und Prefix-Name oder ein vorhandener Prefix, Runner, Architektur, Startoptionen, Verknüpfung im Startmenü/auf dem Schreibtisch. Erkennt bekannte Launcher-Installer am Dateinamen (dann unbeaufsichtigt und mit bekanntem Programmpfad). Legt den Prefix an, führt den Installer im Protokollfenster aus und **legt danach automatisch die Verknüpfung an**: bei bekannten Launchern sicher, sonst für das wahrscheinlichste neu installierte Programm (änderbar unter „Prefixe verwalten“); nur wenn nichts gefunden wird, fragt das Tool nach. Icon wird aus der .exe gelesen. |
| **Prefixe verwalten** | **Speicherorte**: Standardordner ändern (auf Wunsch vorhandene Prefixe mitverschieben), weitere Orte hinzufügen/entfernen – Prefixe in allen Orten werden automatisch gefunden und mit „WEITERER ORT“ markiert. Jeder Prefix (auch außerhalb des Standardordners, Wine- und Proton-Layout) als aufklappbare Zeile: Starten, winecfg, winetricks, drive_c, Backup, **Umbenennen**, **Verschieben** (anderes Dateisystem per rsync), **Verknüpfung anlegen**, **Löschen**; **Runner** (Proton/Wine/umu), **Startoptionen** und **Umgebungsvariablen** für alle Programme im Prefix, **Windows-DPI**; darunter die Anwendungen des Prefix mit Starten/Bearbeiten/**Deinstallieren** und Markierung für Startmenü/Schreibtisch, sowie gefundene Launcher ohne Verknüpfung mit „Verknüpfung anlegen“. **Windows-Programme …** zeigt die Windows-Programmliste des Prefix (wie „Programme deinstallieren“ unter Windows) mit Deinstallieren je Eintrag. Backups wiederherstellen oder löschen. |
| **Speicherorte & Migration** | Zielordner wählen (freier Platz wird angezeigt). Gefundene Launcher mit Art (nativ/Flatpak/Wine-Prefix), Pfad und Größe: Steam, Lutris, Heroic, Bottles, Steam-Compat-Tools sowie Ubisoft Connect, GOG Galaxy, Rockstar, Xbox, Epic, Battle.net, EA App in beliebig benannten Prefixen. Auswahl → **rsync mit Fortschritt**, danach Original löschen und Symlink setzen. Bereits verschobene Einträge sind markiert. |
| **ProtonPlus & Compat-Tools** | Status (nativ/Flatpak/fehlt), starten, per Flatpak installieren/aktualisieren (Flathub wird bei Bedarf als Benutzer-Remote eingerichtet). Tags für umu-launcher und proton-cachyos. Liste installierter Proton-/Wine-Versionen in Steam, Heroic, Lutris, Bottles, umu; alle Tool-Pfade mit Vorhanden/Fehlt. |
| **Speicherplatz** | Alle realen Dateisysteme mit Balken und frei/gesamt; Gaming-Ordner (Steam, Lutris, Heroic, Bottles, Prefix-Root, Zielordner, Backups) mit Größen und Summe. |
| **Laufwerke einbinden** | Zusätzliche NVMe-/SATA-/USB-Partitionen (Systemlaufwerk ausgeblendet, optional einblendbar) mit Größe, Dateisystem, Modell, Transport, Status (in fstab / temporär / nicht eingebunden / kein Dateisystem). Auswahl → Formular: UUID (kopierbar), NTFS-Treiber (ntfs3/ntfs-3g), Basisverzeichnis, Ordnername, berechneter Mountpoint, vorgeschlagene Mount-Optionen je Dateisystem, **fstab-Zeile als Vorschau**. Ein Klick trägt ein, legt ein fstab-Backup an, führt `systemctl daemon-reload` und `mount` aus und setzt bei ext4/btrfs/xfs den Besitzer. **Formatieren** (ext4/btrfs/xfs/exFAT) mit Label; Bestätigung erfordert das Eintippen des Gerätenamens. |
| **Ordner anlegen** | Ordner auf einem Mountpoint mit dir als Besitzer anlegen (pkexec); Besitz eines vorhandenen Ordners rekursiv übernehmen. |
| **Update & DKMS** | Verfügbare Updates (checkupdates, ohne Root), laufender Kernel mit Neustart-Hinweis, NVIDIA-Modulversion, DKMS-Status. **System aktualisieren** führt `pacman -Syu --noconfirm` über pkexec aus, Ausgabe live im Protokoll; danach automatisch DKMS- und Kernel-Prüfung. |
| **GPU-Status** | Live-Karte je GPU: Temperatur, Auslastung, Leistung, GPU-/VRAM-Takt, Lüfter, VRAM-Balken. NVIDIA über nvidia-smi, AMD/Intel über sysfs. Automatische Aktualisierung alle 2 s (abschaltbar), vollständige nvidia-smi-Ausgabe. |
| **X3D-Kerne (CCD)** | Für Ryzen mit zwei CCDs und einem 3D-V-Cache-Die (7950X3D/7900X3D, 9950X3D/9900X3D): zeigt beide CCDs (Kerne, L3, CPU-Nummern) und die aktuelle **Rangfolge der Kerne** des Kernel-Treibers `amd_3d_vcache` (Cache-/Frequenz-CCD, per Klick umschaltbar; einmalig „Zugriff einrichten“ per pkexec). **Hintergrunddienst** (`--x3d-daemon`, systemd --user, Autostart-Schalter): erkennt laufende Spiele wie AMDs 3D-V-Cache-Treiber unter Windows und hält ihren gesamten Prozessbaum auf dem Cache-CCD, stellt die Rangfolge um und verschiebt Steam, Store-Launcher, Discord und Browser auf den anderen CCD; nach dem Spiel gilt wieder die Ruhe-Vorgabe. **Erkannte Spiele live** mit Quelle, Grund, Zuweisung, Thread-Lage (wie viele Threads auf dem zugewiesenen CCD gebunden sind, wo sie zuletzt liefen, Anteil der CPU-Zeit auf dem Ziel-CCD seit Erkennung) – je Spiel per Klick Cache-CCD / Frequenz-CCD / Frei / „Kein Spiel“. Je CCD **Auslastung live** (ab 5.1.1) als Balken je CPU (Linux zählt SMT-Geschwister getrennt: 0-7 und 16-23 = CCD0). **Katalog** (mitgelieferte Kompatibilitätsliste mit Suche) und **eigene Festlegungen** (auch per .exe-Name, Prozessname oder Steam-AppID). Vorgaben: unbekannte Spiele, Ruhe-Rangfolge, Hintergrundprogramme, Zuweisung erzwingen, zusätzliche Spieleordner. Protokoll des Dienstes. |
| **Logs & Diagnose** | Journal des aktuellen oder vorherigen Boots mit Vorfiltern (alles, Warnungen/Fehler, Kernel, NVIDIA/DRM, AMD, gamescope/Steam/Vulkan/Proton, Wayland/KWin), eigenem Suchmuster (regulärer Ausdruck) und Zeilenlimit. Kopieren oder als Datei speichern. |
| **Shader-Cache leeren** | Steam-Shader-Cache (nativ/Flatpak), DXVK-/VKD3D-Caches in Prefixen und Spieleordner, Mesa-Cache, NVIDIA GLCache – mit Größen, Auswahl und Bestätigung. |
| **Skalierung** | Desktop-Skalierung 100–250 %: unter Plasma Wayland der echte Wert je Bildschirm (kscreen-doctor), „Anwenden“ wirkt dort sofort auf den gewählten Bildschirm; sonst KDE kdeglobals / GNOME / GDK_SCALE (nach Ab-/Anmelden); Verweis auf die Anzeige-Einstellungen; Windows-DPI je Prefix – **automatisch passend zur Desktop-Skalierung** (Xft.dpi, z. B. 216 bei 225 %): neue Prefixe bekommen sie beim Anlegen, Prefixe ohne eigene DPI vor dem Start (abschaltbar, eigene Werte bleiben); Fenstergröße zurücksetzen. |
| **Einstellungen** (Menü) | Wine-Prefix-Root, Gaming-Zielordner, Backup-Ordner, Basisverzeichnis für Mountpoints, Standard-Startoptionen, GPU-Autoaktualisierung. |

## Deinstallation von Launchern und Programmen

„Deinstallieren …“ (Launcher-Seite oder Anwendung auf der Prefix-Seite) bietet drei Stufen:

| Stufe | Was passiert |
|---|---|
| Programm im Prefix (Vorauswahl) | Windows-Deinstallation des Programms im Prefix starten (Eintrag aus der Windows-Programmliste per `uninstaller --remove`, über denselben Runner wie der Prefix; bei mehreren Treffern Auswahl). Das Tool wartet, bis auch nachgelagerte Deinstallationsprozesse beendet sind, und entfernt dann Verknüpfungen und Eintrag. Der Prefix samt darin installierten Spielen bleibt. |
| Vollständig | Laufende Prozesse des Prefix beenden, Prefix samt allen Dateien – auch darin installierter Spiele – löschen, alle Anwendungen des Prefix samt Verknüpfungen entfernen. Speicherorte, das Home-Verzeichnis und Ordner, die keine Prefixe sind, werden nie gelöscht. |
| Nur Verknüpfung | Verknüpfungen und Eintrag entfernen, Dateien bleiben. |

Während ein Launcher nach der Installation noch geöffnet ist, weist das Tool beim Deinstallieren darauf hin, ihn zuerst zu schließen.

## Runner: Proton, umu-launcher, Wine

Wie im Galaxy-Port werden alle Kompatibilitätstools gefunden: Proton aus `compatibilitytools.d` (Steam nativ/Flatpak,
`/usr/share/steam/compatibilitytools.d` → **proton-cachyos**), Proton aus Steam-Bibliotheken und Heroic, GE-Proton/UMU-Proton
über umu (automatischer Download), System-Wine sowie Lutris- und Bottles-Runner. Automatik: System-Proton > neuestes
Proton > umu-GE > Wine; Standard und „umu bevorzugen“ stehen in den Einstellungen, je Prefix und je Anwendung überschreibbar.

| Runner | Start | Prefix-Layout |
|---|---|---|
| umu | `umu-run <exe>` mit `WINEPREFIX`, `PROTONPATH`, `GAMEID`, `STORE=none` | Prefix-Ordner direkt |
| Proton direkt | `<proton>/proton waitforexitandrun <exe>` mit `STEAM_COMPAT_DATA_PATH`; SLR-Protons über `_v2-entry-point`, sonst Hinweis auf umu | `<prefix>/pfx` |
| Wine | `<wine> <exe>` mit `WINEPREFIX` (`pfx/`, falls vorhanden) | Prefix-Ordner direkt |

**GE-Proton/UMU-Proton „neueste Version“:** umu lädt diese Versionen über die GitHub-API. Ohne Anmeldung erlaubt GitHub
60 Abfragen pro Stunde je Internetanschluss; ist das Limit erschöpft oder GitHub nicht erreichbar und noch keine Version
lokal vorhanden, bricht umu ab. Das Tool prüft das vorher (über `api.github.com/rate_limit`, zählt nicht aufs Limit):
liegt eine Version lokal in `~/.local/share/Steam/compatibilitytools.d`, wird sie genutzt; sonst weicht es automatisch auf
das installierte Proton (proton-cachyos) aus und nennt den Grund im Protokoll. Startet eine Verknüpfung mit einem solchen
Runner, obwohl er gerade nicht ladbar ist, erscheint eine klare Meldung statt eines stillen Abbruchs.

**Wayland-Treiber (KDE-Wayland als Standard):** Proton-Programme starten mit `PROTON_ENABLE_WAYLAND=1` (Einstellungen →
„Wayland-Treiber von Proton als Vorgabe“). Ausnahme je Prefix (Prefixe verwalten → „Wayland-Treiber von Proton“) oder je
Anwendung (Bearbeiten): „An“ oder „Aus (XWayland)“; eine eigene `PROTON_ENABLE_WAYLAND`-Variable in Umgebung oder
Startoptionen hat immer Vorrang. Behebt z. B. den weißen Wine-Fensterrahmen bei Spielen, die erst im Fenster starten und
dann auf Vollbild wechseln. **Bewusst XWayland bleiben die Store-Launcher aus dem Assistenten:** Im Test (RTX 4090,
proton-cachyos 11) blieben Ubisoft Connect und Rockstar Games Launcher mit dem Wayland-Treiber rein weiß, unter XWayland
erschien sofort die Anmeldung (Bilder unter `reference/wayland-test-2026-10-17/`); für die EA App gibt es denselben Befund
(GE-Proton #188). Spiele, die ein Launcher selbst startet, erben dessen Windows-Desktop und damit XWayland – das lässt
sich unter Wine nicht von außen ändern.

**Ausnahme GE-Proton ab 11-6 (ab 5.0.4):** GE-Proton zeichnet Chromium-/CEF-Oberflächen seit 11-6 auch mit dem
Wayland-Treiber (prozessübergreifend per DMA-BUF). Im Test mit GE-Proton11-9 erschienen Ubisoft Connect und der Rockstar
Games Launcher als native Wayland-Fenster mit Anmeldeseite, scharf bei 216 DPI. Läuft einer dieser beiden Launcher mit
GE-Proton ab 11-6 (fest installiert oder „GE-Proton (neueste Version)“ über umu), lässt das Tool die Katalog-Vorgabe
`PROTON_ENABLE_WAYLAND=0` beim Start weg. **Launcher und alle Spiele, die er startet, laufen dann unter KDE-Wayland.**
Mit „Aus (XWayland)“ an der Anwendung bleibt es beim alten Verhalten.

**GOG Galaxy unter Wayland (ab 5.0.4):** GOG Galaxy 2.1 zeichnet seinen Anmeldedialog mit dem Wayland-Treiber
vollständig – mit GE-Proton11-9 **und** mit proton-cachyos 11. Vermutlich liegt das an den Startargumenten
`--in-process-gpu` und `libglesv2=d`. Der Katalog setzt für GOG deshalb kein `PROTON_ENABLE_WAYLAND=0` mehr; bei
bestehenden Einträgen hebt das Tool die alte Vorgabe beim Start auf. EA App, Epic, Battle.net und Amazon sind
ungetestet und bleiben auf XWayland. Bilder: `reference/wayland-test-2026-10-17/gog-galaxy-*.png`.

**Spiel-Verknüpfungen neben einem laufenden Launcher (ab 5.0.4):** Wine lädt den Grafiktreiber je Windows-Desktop.
Läuft im Prefix schon ein Programm, etwa der Launcher, startet das Tool weitere Programme per `runinprefix`. Sie warten
also nicht mehr bis zum Ende des Launchers, wie es `waitforexitandrun` tat. Mit Wayland-Treiber kommen sie auf einen eigenen
Wine-Desktop (`explorer.exe /desktop=root,…,wayland start.exe /d <Ordner> <Exe>`) und laufen dort als echte
Wayland-Fenster, während der Launcher unter XWayland bleibt. Vorher starben solche Spiele sofort („no driver could be
loaded“). Reihenfolge: **erst den Launcher starten, dann das Spiel.** Startet umgekehrt zuerst das Spiel, kommt ein
danach gestarteter Launcher nicht hoch. Belege: `reference/wayland-test-2026-10-17/zweiter-desktop.txt`.

Startoptionen werden wie bei Steam zusammengeführt: global (Einstellungen) + Prefix + Anwendung. Prefixe für umu/Proton entstehen per `wineboot -u` (sauberer Rückgabecode statt `umu-run ''`); ein Erstellungsschritt gilt als
erfolgreich, sobald der Prefix vollständig vorhanden ist. Beim Anlegen eines
Wine-Prefix wird `WINEDLLOVERRIDES=mscoree=d;mshtml=d` gesetzt, damit keine Mono-/Gecko-Dialoge erscheinen; danach
`wineboot --end-session` und `wineserver -w`, damit der Vorgang sauber abschließt.

**Verknüpfungen** liegen als `cachy-app-<id>.desktop` im Startmenü (`~/.local/share/applications`) und/oder auf dem
Schreibtisch (xdg-user-dir DESKTOP) und starten über `cachyos-dailytool --run <id>` – dadurch gelten Runner, Startoptionen
und Umgebungsvariablen aus dem Tool. Registry: `~/.config/cachy-daily-toolkit/registry.json`.

Root-Aktionen (fstab, mount, mkfs, mkdir/chown, pacman) laufen als **ein** zusammengesetzter
Befehl über `pkexec`, sodass pro Aktion nur einmal nach dem Passwort gefragt wird. Die Ausgabe
erscheint im Protokollfenster der jeweiligen Seite und kann kopiert werden.

## X3D-Kernzuteilung (CCD)

Ryzen-9-X3D-Prozessoren mit zwei Core Complex Dies haben den 3D V-Cache nur auf einem CCD (beim 7950X3D: CCD0 mit 96 MB L3, CCD1 mit
32 MB und höherem Takt). Unter Windows sorgen AMDs **3D V-Cache Performance Optimizer** (Spiel erkannt → CPPC-Rangfolge auf den
Cache-CCD, zweiter CCD für das Spiel „geparkt“) und die **Application Compatibility Database** (Liste bekannter Programme mit fester
Zuordnung) dafür, dass Spiele auf dem Cache-CCD landen. Linux kennt dafür nur den Kernel-Treiber `amd_3d_vcache` (ab 6.13), der die
Rangfolge umschalten kann, aber keine Spiele erkennt – ohne weiteres Zutun bevorzugt der Scheduler mit „CPPC Dynamic Preferred Cores =
Driver“ den **Frequenz**-CCD, und Spiele verteilen ihre Threads über beide CCDs (gemessen bei Surviving Mars auf dem Desktop: rund drei
Viertel der CPU-Zeit auf dem CCD ohne V-Cache).

Das DailyTool bildet beide Teile nach (`cachyos_dailytool/x3d.py`, Seite „X3D-Kerne (CCD)“):

| Baustein | Umsetzung |
|---|---|
| Topologie | L3-Instanzen aus sysfs (`/sys/devices/system/cpu/cpu*/cache/index3`): der CCD mit dem größeren L3 ist der Cache-CCD. Ohne zwei unterschiedlich große L3 bleibt alles wirkungslos. |
| Rangfolge der Kerne | `/sys/bus/platform/drivers/amd_x3d_vcache/AMDI0101:00/amd_x3d_mode` = `cache` oder `frequency` (Kernel-Treiber, BIOS „CPPC Dynamic Preferred Cores“ auf Driver/Auto). Schreiben braucht Root: „Zugriff einrichten“ legt per pkexec `/etc/tmpfiles.d/cachyos-dailytool-x3d.conf` an (Gruppe des Nutzers, Modus 0664) und wendet sie sofort an; danach schaltet der Dienst ohne Passwort. |
| Erkennung („Game Bar“) | Der Dienst prüft alle 1,5 s die eigenen Prozesse: Windows-Programme unter Wine/Proton mit offenem Render-Gerät (`/dev/dri/renderD*`, `/dev/nvidia*`), Launcher-Markierungen in der Umgebung (`SteamAppId`, `UMU_ID`/`GAMEID`, `HEROIC_APP_NAME`, `LUTRIS_GAME_UUID`) und native Programme in Spieleordnern (Steam-Bibliotheken, `~/Games`, Heroic, Lutris, eigene Ordner). Ausgeschlossen: Wine-Systemprozesse, Store-Clients, Installer/Laufzeitkomponenten, Steam-Runtime/gamescope-Wrapper. Zusammengehörige Prozesse (gleiche AppID/gleicher Prefix) bilden eine Sitzung mit dem gesamten Prozessbaum. |
| Zuweisung („Parken“) | CPU-Affinität aller Threads des Spielbaums auf den CCD (`sched_setaffinity`, ohne Root). Threads, die das Spiel selbst innerhalb des CCD festlegt, bleiben unangetastet; außerhalb platzierte werden mit „Zuweisung erzwingen“ zurückgeholt. Steam, Launcher, Discord, Browser (Katalog `background`/`launcher`) wechseln währenddessen auf den anderen CCD und danach zurück. |
| Katalog („Compatibility Database“) | `cachyos_dailytool/data/x3d_catalog.json`: Spiele (Name, .exe, Steam-AppID, optional fester Modus), Launcher, Hintergrundprogramme, Systemprozesse. AMDs eigene Liste ist nicht öffentlich – unbekannte Spiele bekommen die Vorgabe (Cache-CCD, wie AMDs Treiber), Ausnahmen trägt man als eigene Festlegung ein (`~/.config/cachy-daily-toolkit/x3d.json`: Modus je Schlüssel `exe:…`, `steam:…`, `comm:…`, `path:…`; Ausschlüsse „kein Spiel“). |
| Verknüpfungen | Anwendungen des DailyTools haben im Bearbeiten-Dialog „X3D-Kernzuteilung“: Automatisch, Cache-CCD, Frequenz-CCD, Keine. Feste Werte wirken sofort beim Start (`taskset -c`) und gelten dem Dienst als Festlegung. |
| Reihenfolge | Ausschluss (Katalog/„kein Spiel“) → eigene Festlegung → Verknüpfung → Katalog-Spiel → Heuristik mit Vorgabe. Rangfolge: ein Spiel mit Cache/Frequenz bestimmt sie; ohne Spiel gilt die Ruhe-Vorgabe (Frequenz-CCD wie AMD, Cache-CCD oder unverändert). |

Einstellungen in `config.env`: `X3D_ENABLED`, `X3D_DEFAULT_MODE` (cache/frequency/none), `X3D_IDLE_MODE` (frequency/cache/keep),
`X3D_BACKGROUND`, `X3D_ENFORCE`, `X3D_GAME_DIRS`. Zustand des Dienstes: `~/.local/share/cachy-daily-toolkit/x3d/state.json`,
Protokoll `logs/x3d.log`. Kommandozeile: `cachyos-dailytool --x3d-status` (Topologie, Rangfolge, erkannte Spiele, ohne Eingriff),
`--x3d-daemon` (Dienst), `--x3d-dry-run` (nur protokollieren). Die Deinstallation entfernt Dienst und Benutzer-Unit; die tmpfiles-Regel (Root) nur mit `--purge`. Eine Neuinstallation über
`install.sh` lässt beides stehen und startet den Dienst mit dem neuen Code neu.

## Sprache

Die Oberfläche gibt es auf Deutsch und Englisch. Ohne Einstellung folgt sie der Systemsprache (deutsch → Deutsch,
sonst Englisch). Umschalten über das **Sprach-Symbol** oben rechts in der Kopfzeile, links neben „Seite aktualisieren“:
Ein Klick auf „Automatisch (Systemsprache)“, „Deutsch“ oder „English“ speichert die Wahl und startet das Programm sofort
neu, die geöffnete Seite bleibt offen. Läuft gerade eine Aufgabe (Installation, Update, Kopieren), fragt es vorher nach.
Die Wahl steht als `UI_LANGUAGE` (auto/de/en) in `~/.config/cachy-daily-toolkit/config.env`; für einen einzelnen Start
geht auch `CACHYOS_DAILYTOOL_LANG=en cachyos-dailytool`. Quelltexte sind Deutsch und mit `tr()` markiert;
`scripts/i18n-update.py [sprache]` aktualisiert `cachyos_dailytool/locale/<sprache>.po` (neue Texte mit leerem
`msgstr`, bis dahin erscheinen sie Deutsch). Der Test `tests/test_i18n.py` prüft, dass der englische Katalog vollständig ist.

## Installation

Quellcode: <https://github.com/Cue-System90/CachyOS-DailyTool>

```bash
git clone https://github.com/Cue-System90/CachyOS-DailyTool.git
cd CachyOS-DailyTool
```

Voraussetzungen (Pflicht): `python`, `python-gobject`, `gtk4`, `libadwaita`, `polkit`, `xdg-utils`, `rsync`.
Für Proton: `proton-cachyos` und/oder `umu-launcher`; für Icons in Verknüpfungen `python-pefile` und `python-pillow`.
Für Wine-Funktionen zusätzlich `wine` (oder `wine-cachyos`/`wine-staging`), empfohlen `winetricks`,
`mangohud`, `gamemode`, `gamescope`, `flatpak` (ProtonPlus), `pacman-contrib` (checkupdates),
`nvidia-utils` (nvidia-smi).

**Vor jeder Installation wird eine vorhandene Version restlos entfernt** – egal ob als pacman-Paket, als
Benutzer-Installation unter `~/.local` oder als alte Bash-Version (v1–v4). Einstellungen bleiben dabei erhalten.

```bash
bash install.sh                  # Benutzer-Installation nach ~/.local, kein Root; fragt fehlende Pakete ab
bash install.sh --system         # pacman-Paket bauen (makepkg) und mit sudo pacman -U installieren
bash install.sh --purge          # vorher zusätzlich Einstellungen und Protokolle löschen (auch mit --system)
bash install.sh --keep           # nichts entfernen, nur drüberinstallieren
bash install.sh --deps           # nur Abhängigkeiten prüfen/installieren
```

Fertiges Paket (mit `bash install.sh --build` gebaut, liegt dann in `dist/`): `sudo pacman -U dist/cachyos-dailytool-*.pkg.tar.zst`
– es ersetzt ein installiertes `cachy-toolkit` (v5.0.4 und älter) automatisch.

Start: Anwendungsmenü → **CachyOS DailyTool**, oder im Terminal `cachyos-dailytool`
(`~/.local/bin/cachyos-dailytool` bei Benutzer-Installation). `cachyos-dailytool DATEI.exe` bzw.
`--launch DATEI.exe` öffnet direkt die Seite „Programm starten“ mit der Datei – so arbeitet auch
die Dateimanager-Verknüpfung. `cachyos-dailytool --run <id>` startet eine registrierte Anwendung ohne Oberfläche
(so arbeiten die Verknüpfungen), `--page prefixes` öffnet die Prefix-Seite.

## Deinstallation

Drei gleichwertige Wege, alle entfernen **sämtliche** Spuren unabhängig von der Installationsart:

```bash
bash install.sh --uninstall [--purge] [--prefixes] [--yes]     # aus dem entpackten Archiv
bash ~/.local/lib/cachyos-dailytool/cachyos_dailytool/uninstall.sh    # bei Benutzer-Installation
bash /usr/share/cachyos-dailytool/uninstall.sh                    # bei Paket-Installation
```

Oder in der App: Menü → **Deinstallieren …** (mit Häkchen für Einstellungen/Protokolle).

Entfernt werden: pacman-Paket (`pacman -Rns`, fragt per sudo/pkexec), `~/.local/lib/cachyos-dailytool`,
`~/.local/bin/cachyos-dailytool`, Menüeintrag, Icons, die .exe/.msi-Verknüpfung samt Einträgen in
`mimeapps.list`, die alte Bash-Version (`cachy-daily-toolkit.sh` und ihr Menüeintrag) sowie deren
temporäre Logdateien; Menü- und Icon-Caches werden aktualisiert.

| Option | Wirkung |
|---|---|
| ohne | Einstellungen (`~/.config/cachy-daily-toolkit`) und Protokolle bleiben erhalten |
| `--purge` | Einstellungen und Protokolle werden gelöscht |
| `--prefixes` | zusätzlich Wine-Prefix-Root und Prefix-Backups (Pfade aus der Konfiguration) – fragt nach, außer mit `--yes` |
| `--yes` | keine Rückfragen · `--quiet` nur Zusammenfassung |

Am Ende prüft das Skript, ob noch Reste vorhanden sind, und meldet sie mit Exit-Code 1.

## Dateien

```
cachyos_dailytool/            Python-Paket
  system.py               Backend ohne GTK (Laufwerke, Prefixe, Launcher, GPU, Journal, Caches, Updates)
  config.py               config.env (kompatibel zu v1–v4)
  uninstall.sh            vollständige Deinstallation (wird mitinstalliert, auch aus der App aufrufbar)
  compat.py               Erkennung von Proton/Wine/umu (wie im Galaxy-Port)
  launcher.py             Startpläne (umu / Proton / Wine), Prefix-Erstellung, winetricks, DPI
  launch_options.py       Steam-Syntax für Startoptionen
  goverlay.py             GOverlay-/MangoHud-Anbindung und Vorlagen
  registry.py             Prefixe außerhalb des Standardordners, Einstellungen je Prefix, Anwendungen
  shortcuts.py            Startmenü-/Schreibtisch-Verknüpfungen, Icon aus .exe (pefile + Pillow)
  launchers.py            Launcher-Katalog (offizielle Installer, Silent-Argumente, Programmpfade), Download, Erkennung
  x3d.py                  X3D-Kernzuteilung: Topologie, amd_x3d_mode, Spielerkennung, Affinität, Hintergrunddienst, systemd --user
  i18n.py                 Übersetzung (tr(), Sprachwahl, .po-Leser); locale/en.po englischer Katalog
  data/x3d_catalog.json   Kompatibilitätskatalog (Spiele, Launcher, Hintergrundprogramme, Wine-Systemprozesse)
  ui/                     app.py, window.py, widgets.py, dashboard.py, pages_*.py (u. a. pages_launchers.py, pages_x3d.py), dialogs.py, style.css
data/                     .desktop-Datei und Icons (hicolor)
scripts/                  Startskript, Paketbau, i18n-update.py (Katalogpflege), uninstall.sh (Wrapper auf cachyos_dailytool/uninstall.sh)
tests/                    Unit-Tests der Backend-Logik
legacy/                   Bash-Version v4 (Referenz)
install.sh · PKGBUILD · LICENSE · README.md (Deutsch) · README.en.md (English)
```

## Entwicklung

```bash
python3 -m unittest discover -s tests -t .     # Backend-Tests
python3 -m cachyos_dailytool                       # aus dem Quellbaum starten
python3 -m cachyos_dailytool --screenshot ORDNER   # alle Seiten rendern und aufnehmen (Spectacle)
python3 -m cachyos_dailytool --x3d-status          # X3D: Topologie, Rangfolge, erkannte Spiele (ohne Eingriff)
python3 -m cachyos_dailytool --x3d-dry-run         # X3D-Dienst im Probelauf (nur protokollieren)
CACHYOS_DAILYTOOL_LANG=en python3 -m cachyos_dailytool   # Oberfläche auf Englisch
scripts/i18n-update.py en                          # Übersetzungskatalog nach Textänderungen aktualisieren
```

Konfiguration: `~/.config/cachy-daily-toolkit/config.env` · Protokolle: `~/.local/share/cachy-daily-toolkit/logs/`
(Toolkit-Log, Wine-Ausgaben je Prefix, ProtonPlus, X3D-Dienst).

## Sicherheitshinweise

- **Formatieren** löscht unwiderruflich alle Daten der gewählten Partition; die Bestätigung verlangt den Gerätenamen.
- Vor jeder fstab-Änderung wird `/etc/fstab.bak.<Zeitstempel>` angelegt (nicht automatisch rotiert).
- `pacman -Syu --noconfirm` bestätigt Konflikte nicht interaktiv; bei Schlüssel- oder Abhängigkeitsproblemen bricht pacman ab und das Protokoll zeigt die Ursache – dann im Terminal nacharbeiten.
- Die Migration löscht den Quellordner erst nach erfolgreichem rsync und setzt dann den Symlink. Betroffene Programme vorher beenden.
- Prefix-Löschen entfernt auch Spielstände innerhalb des Prefix; vorher ein Backup anlegen.

## Hintergrund zu den Laufwerksfunktionen

- Das Systemlaufwerk wird über `findmnt -o SOURCE /` und `lsblk -o PKNAME` ermittelt; btrfs-Subvolume-Suffixe wie `[/@]` werden entfernt (sonst würde das Systemlaufwerk bei CachyOS-Standardinstallationen nicht erkannt).
- Der Transport-Typ (NVMe/SATA/USB) steht nur an der physischen Platte, nicht an der Partition; er wird über die Eltern-Platte zugeordnet, damit auch SATA/ATA-Partitionen erscheinen.
- Mount-Optionen folgen dem Arch-Wiki: ext4/xfs `defaults,noatime` (TRIM per fstrim.timer), btrfs `compress=zstd:1,ssd,discard=async`, NTFS/vfat/exFAT mit `uid`/`gid` (NTFS: `windows_names` nur bei ntfs3).
- Doppelte Pfade (`/mnt/x` + `x`) werden beim Zusammensetzen des Mountpoints erkannt und vermieden.
