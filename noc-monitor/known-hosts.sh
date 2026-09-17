#!/bin/sh
# Genera/actualiza el known_hosts PINNED del motor (#9): huellas de la infraestructura
# FIJA (ESXi + MikroTik RouterData + CCR de borde). Se corre en noc-monitor (root) en
# el deploy, y de nuevo SOLO si una llave de host cambia legítimamente (reinstalación).
# El motor rechaza conexiones a estas máquinas si la huella no calza (anti-MITM).
set -eu

OUT=/opt/vps-engine/keys/known_hosts
ESXI=${ESXI_HOST:-10.100.37.245}
RD=${ROUTERDATA_HOST:-172.16.1.90}
CCR=${CCR_BORDE_HOST:-172.16.1.69}
PMK=${MIKROTIK_PORT:-2420}

TMP=$(mktemp)
ssh-keyscan -T 10 -p 22 "$ESXI" >> "$TMP" 2>/dev/null || true
ssh-keyscan -T 10 -p "$PMK" "$RD" >> "$TMP" 2>/dev/null || true
ssh-keyscan -T 10 -p "$PMK" "$CCR" >> "$TMP" 2>/dev/null || true

# sanity: las TRES fuentes deben estar (si una no respondió, NO se instala un archivo
# incompleto — el motor quedaría fail-closed contra esa máquina)
FALTA=0
grep -qF "$ESXI " "$TMP" || { echo "ERROR: falta huella del ESXi $ESXI" >&2; FALTA=1; }
grep -qF "[$RD]:$PMK " "$TMP" || { echo "ERROR: falta huella de RouterData [$RD]:$PMK" >&2; FALTA=1; }
grep -qF "[$CCR]:$PMK " "$TMP" || { echo "ERROR: falta huella del CCR [$CCR]:$PMK" >&2; FALTA=1; }
[ "$FALTA" -eq 0 ] || { rm -f "$TMP"; exit 1; }

mv "$TMP" "$OUT"
chmod 644 "$OUT"
echo "OK: $(grep -c . "$OUT") huellas instaladas en $OUT"
awk "{print \$1, \$2}" "$OUT" | sort -u
