#!/bin/sh
# vps-wrapper.sh — ÚNICO punto de entrada SSH de vps-engine al ESXi.
# Se instala en /vmfs/volumes/DiscoA37245/VPS/_bin/vps-wrapper.sh y se fuerza en
# authorized_keys:
#   command="/vmfs/volumes/DiscoA37245/VPS/_bin/vps-wrapper.sh",no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty ssh-rsa AAAA... vps-engine
#
# Protocolo: SSH_ORIGINAL_COMMAND = "<subcomando> <args...>"
# Todo opera EXCLUSIVAMENTE bajo $BASE. Los nombres se validan con regex estricta
# (sin '/', sin '..', sin espacios) → no hay traversal posible.
# trash-vm solo MUEVE a _papelera; el único borrado real es purge-entry (UNA
# entrada explícita del motor, re-validando nombre y >7 días aquí — #12).

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

  grow-disk)  # grow-disk <vps> <GB>  (solo crecer; idempotente: si ya está, OK)
    valid_vps "$1" || die "nombre inválido: $1"
    echo "$2" | grep -Eq '^[0-9]{1,4}$' || die "tamaño inválido: $2"
    [ "$2" -le 2000 ] || die "tamaño fuera de rango: $2"
    [ -f "$BASE/$1/$1.vmdk" ] || die "disco no existe: $1"
    cur=$(grep -o 'RW [0-9]*' "$BASE/$1/$1.vmdk" | awk '{print $2}' | head -1)
    want=$(( $2 * 2097152 ))   # GB → sectores de 512B
    if [ -n "$cur" ] && [ "$cur" -ge "$want" ]; then
      echo "OK (ya en tamaño >= ${2}G)"
    else
      vmkfstools -X "${2}G" "$BASE/$1/$1.vmdk" && echo OK
    fi ;;

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

  purge-entry)  # purge-entry <entrada> → borra DEFINITIVO **UNA** entrada (>7 días)
    # (#12: el motor manda una ALLOWLIST explícita entrada por entrada; el wrapper
    #  re-valida nombre estricto Y antigüedad por su lado — cinturón y tirantes.
    #  Reemplaza al antiguo purge-trash masivo.)
    valid_trash "$1" || die "entrada de papelera inválida: $1"
    d="$BASE/_papelera/$1"
    [ -d "$d" ] || die "no existe en papelera: $1"
    find "$BASE/_papelera" -maxdepth 1 -type d -name "$1" -mtime +7 2>/dev/null | grep -q . \
      || die "aún no cumple 7 días en papelera: $1"
    rm -rf "$d" && log "PURGED(entry): $1" && echo "purged: $1" ;;

  # Los listados FALLAN CERRADO (die) si el directorio no se puede leer: un listado
  # vacío por error enmascarado haría creer al motor que no hay nada (y su
  # reconciliación de huérfanas borraría registros/llaves en masa). El OK final es
  # el sentinel que el motor verifica para confiar en el listado.
  list-vps)
    out=$(ls -1 "$BASE" 2>&1) || die "no pude listar VPS/: $out"
    echo "$out" | grep -E '^vps-' || true   # sin VPS no es error: el sentinel debe salir igual
    echo OK ;;
  list-trash)
    [ -d "$BASE/_papelera" ] || die "_papelera inaccesible"
    out=$(ls -1 "$BASE/_papelera" 2>&1) || die "no pude listar _papelera: $out"
    [ -n "$out" ] && echo "$out"
    echo OK ;;
  df)          df -h /vmfs/volumes/DiscoA37245 | tail -1 ;;

  *) die "subcomando no permitido: '$cmd'" ;;
esac

rc=$?
log "rc=$rc: $SSH_ORIGINAL_COMMAND"
exit $rc
