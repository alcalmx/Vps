#!/bin/sh
# Genera/actualiza el known_hosts PINNED del motor (#9): huellas de la infraestructura
# FIJA (ESXi + MikroTik RouterData + CCR de borde). Se corre en noc-monitor (root) en
# el deploy, y de nuevo SOLO si una llave de host cambia legítimamente (reinstalación).
# El motor rechaza conexiones a estas máquinas si la huella no calza (anti-MITM).
#
# ⚠️ ssh-keyscan confía en QUIEN RESPONDE (un MITM durante el scan pinnearía su huella).
# Por eso este script MUESTRA las huellas SHA256 y EXIGE confirmación humana contra la
# consola/gestión del equipo antes de instalar (Codex #9 obs.1). Modo no-interactivo:
# KH_CONFIRM=si (para CI con red de gestión confiable).
set -eu

OUT=/opt/vps-engine/keys/known_hosts
ESXI=${ESXI_HOST:-10.100.37.245}
RD=${ROUTERDATA_HOST:-172.16.1.90}
CCR=${CCR_BORDE_HOST:-172.16.1.69}
PMK=${MIKROTIK_PORT:-2420}

# temp en el MISMO directorio que $OUT → el mv final es atómico (mismo filesystem)
TMP=$(mktemp "$(dirname "$OUT")/.kh.XXXXXX")
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

# huellas SHA256 para VERIFICACIÓN humana (compáralas con la consola de cada equipo).
# Se listan TODAS las llaves capturadas (una por algoritmo: rsa, ecdsa, ed25519).
echo "== Huellas SHA256 capturadas — VERIFICAR contra la consola/gestión de cada equipo =="
ssh-keygen -E sha256 -lf "$TMP" | sort -u
echo "==============================================================================="
if [ "${KH_CONFIRM:-}" != "si" ]; then
  printf "¿Las huellas coinciden con las de los equipos (canal autenticado)? [escribe 'si']: "
  read -r RESP
  [ "$RESP" = "si" ] || { echo "Cancelado — no se instaló nada." >&2; rm -f "$TMP"; exit 1; }
fi

# preservar entradas de OTROS hosts ya pinneados (p.ej. ESXi adicionales enrolados con
# enrolar-host.sh): se re-generan SOLO las 3 fijas, el resto se conserva (#9 obs.3)
if [ -f "$OUT" ]; then
  grep -vE "^(${ESXI} |\[${RD}\]:${PMK} |\[${CCR}\]:${PMK} )" "$OUT" >> "$TMP" || true
fi
sort -u "$TMP" -o "$TMP"
mv "$TMP" "$OUT"
chmod 644 "$OUT"
echo "OK: $(grep -c . "$OUT") líneas en $OUT (incluye hosts adicionales preservados)"
