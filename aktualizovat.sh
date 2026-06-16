#!/usr/bin/env bash
# Spouštěč denní aktualizace – aktivuje venv a spustí skript.
# Použití:  ./aktualizovat.sh            (ostrá aktualizace)
#           ./aktualizovat.sh --dry-run  (jen náhled změn)
set -euo pipefail
cd "$(dirname "$0")"
.venv/bin/python aktualizace.py "$@"
