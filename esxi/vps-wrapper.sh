#!/bin/sh
# vps-wrapper.sh — ÚNICO punto de entrada SSH de vps-engine al ESXi.
# Se instala en /vmfs/volumes/DiscoA37245/VPS/_bin/vps-wrapper.sh y se fuerza en
# authorized_keys:
#   command="/vmfs/volumes/DiscoA37245/VPS/_bin/vps-wrapper.sh",no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty ssh-rsa AAAA... vps-engine
#
# Protocolo: SSH_ORIGINAL_COMMAND = "<subcomando> <args...>"
# Todo opera EXCLUSIVAMENTE bajo $BASE. Los nombres se validan con regex estricta
# (sin '/', sin '..', sin espacios) → no hay traversal posible.
# trash-vm solo MUEVE a _papelera; el único borrado real es purge-trash (>7 días).

BASE=/vmfs/volumes/DiscoA37245/VPS
LOG=$BASE/_bin/wrapper.log

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') [$$] $*" >> "$LOG"; }
die() { echo "ERROR: $*" >&2; log "DENY: $* | cmd='$SSH_ORIGINAL_COMMAND'"; exit 1; }

# shellcheck disable=SC2086
set -- $SSH_ORIGINAL_COMMAND
cmd=$1
[ -n "$cmd" ] && shift

valid_vps()    { echo "$1" | grep -Eq '^vps-[a-z]{2,5}-[a-z0-9][a-z0-9-]{0,40}$'; }
valid_dorada() { echo "$1" | grep -Eq '^dorada-[a-z0-9][a-z0-9.-]{1,30}$'; }
valid_name()   { valid_vps "$1" || valid_dorada "$1"; }
valid_trash()  { echo "$1" | grep -Eq '^[0-9]{8}-[0-9]{6}-vps-[a-z]{2,5}-[a-z0-9][a-z0-9-]{0,40}$'; }
# las doradas viven en _plantillas/, los vps en la raíz de VPS/
dir_of()       { if valid_dorada "$1"; then echo "$BASE/_plantillas/$1"; else echo "$BASE/$1"; fi; }

case "$cmd" in
  ping)
    echo pong ;;

  mkdir-vm)  # mkdir-vm <nombre>
    valid_name "$1" || die "nombre inválido: $1"
    d=$(dir_of "$1")
    [ -e "$d" ] && die "ya existe: $1"
    mkdir -p "$d" && echo OK ;;

  create-disk)  # create-disk <nombre> <GB>  (disco thin vacío; para construir doradas)
    valid_dorada "$1" || die "create-disk es solo para doradas: $1"
    echo "$2" | grep -Eq '^[0-9]{1,4}$' || die "tamaño inválido: $2"
    [ "$2" -ge 5 ] && [ "$2" -le 2000 ] || die "tamaño fuera de rango (5-2000): $2"
    d=$(dir_of "$1")
    [ -d "$d" ] || die "directorio no existe (mkdir-vm primero): $1"
    [ -e "$d/$1.vmdk" ] && die "disco ya existe: $1"
    vmkfstools -c "${2}G" -d thin "$d/$1.vmdk" && echo OK ;;

  clone-disk)  # clone-disk <dorada> <vps>
    valid_dorada "$1" || die "plantilla inválida: $1"
    valid_vps "$2" || die "nombre de vps inválido: $2"
    src="$BASE/_plantillas/$1/$1.vmdk"
    dst="$BASE/$2/$2.vmdk"
    [ -f "$src" ] || die "plantilla no existe: $1"
    [ -d "$BASE/$2" ] || die "directorio destino no existe (mkdir-vm primero): $2"
    [ -e "$dst" ] && die "disco destino ya existe: $2"
    vmkfstools -i "$src" -d thin "$dst" && echo OK ;;

  grow-disk)  # grow-disk <vps> <GB>  (solo crecer; vmkfstools -X rechaza achicar)
    valid_vps "$1" || die "nombre inválido: $1"
    echo "$2" | grep -Eq '^[0-9]{1,4}$' || die "tamaño inválido: $2"
    [ "$2" -le 2000 ] || die "tamaño fuera de rango: $2"
    [ -f "$BASE/$1/$1.vmdk" ] || die "disco no existe: $1"
    vmkfstools -X "${2}G" "$BASE/$1/$1.vmdk" && echo OK ;;

  trash-vm)  # trash-vm <vps>  → mueve a _papelera (NUNCA borra)
    valid_vps "$1" || die "solo se botan vps (nombre inválido): $1"
    [ -d "$BASE/$1" ] || die "no existe: $1"
    ts=$(date '+%Y%m%d-%H%M%S')
    mv "$BASE/$1" "$BASE/_papelera/$ts-$1" || die "mv falló"
    touch "$BASE/_papelera/$ts-$1"   # mtime = momento del botado (lo usa purge-trash)
    echo "OK $ts-$1" ;;

  restore-trash)  # restore-trash <entrada-papelera>  → devuelve el dir a VPS/
    valid_trash "$1" || die "entrada de papelera inválida: $1"
    [ -d "$BASE/_papelera/$1" ] || die "no existe en papelera: $1"
    orig=$(echo "$1" | cut -c17-)
    [ -e "$BASE/$orig" ] && die "ya existe un $orig activo"
    mv "$BASE/_papelera/$1" "$BASE/$orig" && echo "OK $orig" ;;

  purge-trash)  # purge-trash → borra DEFINITIVO entradas con >7 días en _papelera
    find "$BASE/_papelera" -maxdepth 1 -type d -mtime +7 2>/dev/null | while read -r d; do
      b=$(basename "$d")
      valid_trash "$b" || { log "purge SKIP nombre raro: $b"; continue; }
      rm -rf "$BASE/_papelera/$b" && log "PURGED: $b" && echo "purged: $b"
    done
    echo OK ;;

  list-vps)    ls -1 "$BASE" 2>/dev/null | grep -E '^vps-'; echo OK ;;
  list-trash)  ls -1 "$BASE/_papelera" 2>/dev/null; echo OK ;;
  df)          df -h /vmfs/volumes/DiscoA37245 | tail -1 ;;

  *) die "subcomando no permitido: '$cmd'" ;;
esac

rc=$?
log "rc=$rc: $SSH_ORIGINAL_COMMAND"
exit $rc
