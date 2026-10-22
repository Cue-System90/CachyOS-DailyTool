#!/usr/bin/env bash
# Deinstallation aus dem Quellbaum/Archiv – siehe cachyos_dailytool/uninstall.sh
exec bash "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/cachyos_dailytool/uninstall.sh" "$@"
