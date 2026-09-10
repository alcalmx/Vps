#!/bin/sh
# Paso 1 (lo ejecuta Alcadio — creación de cuenta privilegiada, requiere humano):
# crea el usuario svc-vps en el ESXi con rol Admin TEMPORAL (el paso 2 lo degrada
# al rol mínimo VpsOperator). La contraseña es la de GOVC_PASSWORD en
# /opt/vps-engine/engine.env de noc-monitor.
#
# Ejecutar DESDE ESTE VPS de IA (Git Bash o cmd con ssh):
#   sh scripts/01-esxi-cuenta-svc-vps.sh 'LA_CONTRASEÑA'
set -e
[ -n "$1" ] || { echo "uso: $0 'contraseña de svc-vps (GOVC_PASSWORD del engine.env)'"; exit 1; }

ssh -i ~/.ssh/claude_esxi root@10.100.37.245 \
  "esxcli system account add -i svc-vps -d 'vps-engine (proyecto Vps)' -p '$1' -c '$1' \
   && esxcli system permission set -i svc-vps -r Admin \
   && echo 'CUENTA CREADA (Admin temporal):' && esxcli system permission list"

echo
echo "Listo. Ahora el paso 2 (en noc-monitor) crea el rol mínimo y degrada la cuenta."
