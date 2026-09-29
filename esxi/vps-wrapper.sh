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
# plantilla transferible = una dorada o un .iso auxiliar (p.ej. ks-oemdrv.iso)
valid_plantilla() { valid_dorada "$1" || echo "$1" | grep -Eq '^[a-z0-9][a-z0-9._-]{0,40}\.iso$'; }
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
  df)          df -h "$BASE" | tail -1 ;;   # (antes hardcodeaba DiscoA37245 — bug multi-host)

  # ── Fase D (wizard): mover plantillas entre hosts SIN shell libre ───────────
  # El motor (noc-monitor) alcanza a todos los hosts pero ellos no se ven entre
  # sí → la copia viaja: ssh origen export-plantilla | ssh destino import-plantilla.
  # Confinado a _plantillas/, comprimido (los thin viajan livianos) y con el CRC
  # de gzip como verificación de integridad extremo a extremo.
  list-plantillas)   # listado fail-closed con sentinel (mismo criterio que list-vps)
    [ -d "$BASE/_plantillas" ] || die "_plantillas inaccesible"
    out=$(ls -1 "$BASE/_plantillas" 2>&1) || die "no pude listar _plantillas: $out"
    [ -n "$out" ] && echo "$out"
    echo OK ;;

  export-plantilla)  # export-plantilla <dorada-*|*.iso>  → tar.gz por stdout
    valid_plantilla "$1" || die "plantilla inválida: $1"
    [ -e "$BASE/_plantillas/$1" ] || die "no existe: $1"
    cd "$BASE/_plantillas" || die "no pude entrar a _plantillas"
    # rc de AMBOS lados del pipe, con archivo de control en dir EXCLUSIVO (mkdir
    # sin -p falla si existe → nadie puede pre-plantar un symlink con ese nombre)
    ctl="/tmp/.exp.$$"
    mkdir "$ctl" || die "control dir en uso"
    ( tar cf - "$1"; echo $? > "$ctl/rc" ) | gzip -1
    grc=$?
    trc=$(cat "$ctl/rc" 2>/dev/null); rm -rf "$ctl"
    [ "$grc" = "0" ] && [ "$trc" = "0" ] || die "export de $1 falló (tar=${trc:-?} gzip=$grc)" ;;

  import-plantilla)  # import-plantilla <nombre> <token>  ← tar.gz por stdin.
    # VALIDA TODO y deja ".ok.<token>.<n>" SIN publicar: el motor confirma con
    # publicar-plantilla SOLO si el export también terminó rc=0. El TOKEN (hex8,
    # uno por transferencia) evita que un intento viejo interfiera con el nuevo.
    valid_plantilla "$1" || die "plantilla inválida: $1"
    echo "$2" | grep -Eq '^[a-f0-9]{8}$' || die "token inválido"
    [ -e "$BASE/_plantillas/$1" ] && die "ya existe: $1"
    # staging/copias huérfanos (conexión cortada): limpiar los >3h
    find "$BASE/_plantillas" -maxdepth 1 \( -name '.stage.*' -o -name '.ok.*' \) -mmin +180 -exec rm -rf {} + 2>/dev/null
    # CUOTA: el spool comparte datastore con las VMs — exigir margen y capar el stream
    libre_kb=$(df -k "$BASE" | tail -1 | awk '{print $4}')
    echo "$libre_kb" | grep -Eq '^[0-9]+$' || die "no pude medir el espacio libre"
    [ "$libre_kb" -ge 104857600 ] || die "espacio insuficiente para importar (se exigen 100G libres)"
    st="$BASE/_plantillas/.stage.$$"
    mkdir "$st" || die "staging en uso"
    fail() { rm -rf "$st"; die "$*"; }
    ctl="$st/ctl"; x="$st/x"
    mkdir "$ctl" "$x" || fail "no pude crear ctl/x"
    # 1) SPOOL a disco ANTES de tocar tar (capado a 32G: un stream mayor queda
    #    truncado y el CRC de gzip lo rechaza). Ningún rc vive donde extrae tar.
    head -c 34359738368 > "$ctl/spool.tgz" || fail "no pude recibir el stream"
    gunzip -t "$ctl/spool.tgz" 2>/dev/null || fail "stream gzip corrupto (CRC) o truncado"
    # 2) PRE-SCAN FAIL-CLOSED de los miembros ANTES de extraer NADA: los listados
    #    van a archivos de control con rc verificado y conteos que deben calzar —
    #    ninguna validación se aprueba por "ausencia de salida" (Codex r3 #1)
    gunzip -c "$ctl/spool.tgz" | tar tf - > "$ctl/nombres" || fail "tar ilegible (tf)"
    grep -q . "$ctl/nombres" || fail "tar vacío"
    ( gunzip -c "$ctl/spool.tgz" | tar tvf - > "$ctl/tipos" ) || fail "tar ilegible (tvf)"
    n_nom=$(wc -l < "$ctl/nombres"); n_tip=$(wc -l < "$ctl/tipos")
    [ "$n_nom" = "$n_tip" ] || fail "listados inconsistentes ($n_nom nombres vs $n_tip tipos)"
    while IFS= read -r m; do
      case "$m" in "$1"|"$1"/*) : ;; *) fail "miembro fuera de '$1': $m" ;; esac
      case "$m" in *../*|*/..|..*) fail "miembro con '..': $m" ;; esac
    done < "$ctl/nombres"
    while IFS= read -r tl; do
      case "$tl" in d*|-*) : ;; *) fail "tipo no permitido (symlink/hardlink/especial): ${tl%% *}" ;; esac
    done < "$ctl/tipos"
    # 3) extraer en área limpia y re-validar (cinturón), también fail-closed
    gunzip -c "$ctl/spool.tgz" | tar xf - -C "$x" || fail "extracción falló"
    rm -f "$ctl/spool.tgz"
    [ "$(ls -A "$x" | wc -l)" = "1" ] && [ -e "$x/$1" ] || fail "contenido inesperado tras extraer"
    find "$x" ! -type f ! -type d > "$ctl/malos" || fail "find (tipos) falló"
    grep -q . "$ctl/malos" && fail "tipos no permitidos post-extracción"
    find "$x" -type f ! -links 1 > "$ctl/hlinks" || fail "find (hardlinks) falló"
    grep -q . "$ctl/hlinks" && fail "hardlinks no permitidos"
    case "$1" in
      dorada-*) [ -d "$x/$1" ] || fail "$1 debía ser directorio" ;;
      *)        [ -f "$x/$1" ] || fail "$1 debía ser archivo" ;;
    esac
    rm -rf "$BASE/_plantillas/.ok.$2.$1"
    mv "$x/$1" "$BASE/_plantillas/.ok.$2.$1" && rm -rf "$st" && log "IMPORT(validada): $1 [$2]" && echo OK ;;

  publicar-plantilla)  # publicar-plantilla <nombre> <token> — 2ª fase, la confirma el motor
    valid_plantilla "$1" || die "plantilla inválida: $1"
    echo "$2" | grep -Eq '^[a-f0-9]{8}$' || die "token inválido"
    [ -e "$BASE/_plantillas/.ok.$2.$1" ] || die "no hay copia validada de: $1 [$2]"
    [ -e "$BASE/_plantillas/$1" ] && die "ya existe: $1"
    mv "$BASE/_plantillas/.ok.$2.$1" "$BASE/_plantillas/$1" && log "PUBLISH: $1 [$2]" && echo OK ;;

  descartar-plantilla)  # descartar-plantilla <nombre> <token> — descarta una copia NO confirmada
    valid_plantilla "$1" || die "plantilla inválida: $1"
    echo "$2" | grep -Eq '^[a-f0-9]{8}$' || die "token inválido"
    rm -rf "$BASE/_plantillas/.ok.$2.$1" && echo OK ;;

  thin-plantilla)    # thin-plantilla <dorada>  → punch-zero post-import (recupera thin)
    valid_dorada "$1" || die "solo doradas: $1"
    [ -f "$BASE/_plantillas/$1/$1.vmdk" ] || die "disco no existe: $1"
    vmkfstools -K "$BASE/_plantillas/$1/$1.vmdk" >/dev/null && echo OK ;;

  *) die "subcomando no permitido: '$cmd'" ;;
esac

rc=$?
log "rc=$rc: $SSH_ORIGINAL_COMMAND"
exit $rc
