#!/usr/bin/env bash
# Deinstallation aus dem Quellbaum/Archiv – siehe cachy_toolkit/uninstall.sh
exec bash "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/cachy_toolkit/uninstall.sh" "$@"
