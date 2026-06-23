#!/usr/bin/env bash
# Spouštěč denní aktualizace – aktivuje venv a spustí skript.
# Použití:  ./aktualizovat.sh            (ostrá aktualizace)
#           ./aktualizovat.sh --dry-run  (jen náhled změn)
#
# Před během počká na konektivitu k sauto.cz. Když síť není (typicky výpadek
# DNS ve WSL), běh se přeskočí – aktualizace.py už sice výpadek ustojí, ale
# nemá smysl ho pouštět naprázdno. Viz incident 22.6.2026.
set -euo pipefail
cd "$(dirname "$0")"

echo "===== $(date '+%Y-%m-%d %H:%M:%S') ====="

# Čekání na síť: 5 pokusů po 10 s, ptáme se přímo sauto API.
SAUTO="https://www.sauto.cz/api/v1/items/210644150"
for pokus in 1 2 3 4 5; do
    if curl -fsS --max-time 15 -o /dev/null "$SAUTO"; then
        break
    fi
    if [ "$pokus" -eq 5 ]; then
        echo "  ⚠️ sauto.cz nedostupné po 5 pokusech – běh se přeskakuje (data beze změny)."
        exit 0
    fi
    echo "  … síť/sauto zatím nedostupné (pokus $pokus/5), čekám 10 s"
    sleep 10
done

.venv/bin/python aktualizace.py "$@"
