#!/bin/sh
# Mantención diaria del motor VPS (se instala en /usr/local/bin/vps-mantencion.sh y lo
# dispara vps-mantencion.timer): 1) purga de papelera (>7 días, allowlist del registro) y
# 2) reconciliación de las 4 fuentes. Ambos quedan como jobs visibles en el dashboard
# (pestaña Jobs) y sus alertas en la tabla de auditoría.
#
# La reconciliación NO se lanza hasta que la purga alcanzó estado terminal confirmado
# (comparten el gate de mantención vm='-'). El exit code es la señal de alerta de systemd:
#   0  -> ambas operaciones terminaron 'ok'
#   1  -> alguna terminó en 'error' terminal (o reconciliación con resultado desconocido)
#   1  -> purga con resultado DESCONOCIDO/no-terminal: se ABORTA sin reconciliar
#
# Endurecido tras revisión Codex (deuda de re-validación), rondas 1-2:
#  - JSON validado con `jq -ers` (slurp): se exige documento único, raíz objeto y campo con
#    tipo/valor esperados; un "estado" anidado, un doc extra o un string con "\n" ya no
#    pueden fingir un 'ok'. Estos filtros validan ANTES de emitir su único resultado, así que
#    no producen salida parcial ante una entrada inválida (propiedad de los filtros, no de -s).
#  - se verifica el TIPO real del job; se distingue "otro tipo confirmado" (esperar su
#    término y re-POST) de "tipo desconocido por fallo de lectura" (reintentar leer el MISMO
#    job, nunca re-POST) para no duplicar una operación ya encolada.
#  - cada curl comprueba su rc explícitamente (set -e NO protege dentro de $(...)); una
#    transferencia incompleta o un HTTP>=400 nunca se aceptan como estado del job.
#  - deadlines por reloj MONOTÓNICO (/proc/uptime, inmune a ajustes de hora), comprobados
#    antes de cada espera; siempre hay una consulta final antes de declarar timeout.
#  - el token va en un --config de curl (600), NO en argv ni en el entorno; no se registra
#    ningún cuerpo de respuesta. -q + noproxy '*' evitan curlrc/proxies heredados.
#  - traps: limpieza en EXIT y handlers de señal que SALEN con código != 0.
set -eu

API=http://127.0.0.1:8224
ENV_FILE=/opt/vps-engine/engine.env
DEADLINE_JOB=300     # espera máx por un job hasta estado terminal (s)
DEADLINE_GATE=180    # espera máx si el gate lo tiene OTRA operación (s)
DEADLINE_READY=60    # espera máx de readiness del motor al inicio (s)
POLL=5               # intervalo de sondeo (s)

# job_id contractual del motor = uuid4().hex[:12] (12 hex minúsculas)
JQ_1OBJ='if length!=1 then error("docs") else .[0] end | if type!="object" then error("raiz") else . end'
JQ_ESTADO="$JQ_1OBJ"' | .estado | if type=="string" and (.=="corriendo" or .=="ok" or .=="error") then . else error("estado") end'
# \A y \z (no ^/$): en Oniguruma ^/$ delimitan LÍNEA, así "…\n" pasaría (y $(...) come el \n)
JQ_TIPO="$JQ_1OBJ"' | .tipo | if type=="string" and test("\\A[a-z][a-z-]*\\z") then . else error("tipo") end'
JQ_JOBID="$JQ_1OBJ"' | .job_id | if type=="string" and test("\\A[0-9a-f]{12}\\z") then . else error("job_id") end'

# ── token: exactamente una definición, sin CR/LF ni caracteres de control ────────
_n=$(grep -c '^ENGINE_TOKEN=' "$ENV_FILE" 2>/dev/null || true)
[ "$_n" = "1" ] || { echo "ERROR: se esperaba exactamente 1 ENGINE_TOKEN= en $ENV_FILE (hay ${_n:-0})" >&2; exit 1; }
ET=$(sed -n 's/^ENGINE_TOKEN=//p' "$ENV_FILE" | head -1)
_CR=$(printf '\r'); ET=${ET%"$_CR"}   # quita SOLO un CR final (CRLF); un CR interior queda y lo caza el detector
[ -n "$(printf '%s' "$ET" | tr -d '[:space:]')" ] || { echo "ERROR: ENGINE_TOKEN vacío o en blanco" >&2; exit 1; }
if printf '%s' "$ET" | LC_ALL=C grep -q '[[:cntrl:]]'; then
  echo "ERROR: ENGINE_TOKEN con caracteres de control (CR/LF interior u otros)" >&2; exit 1
fi

# ── config de curl: el secreto NO entra a argv ni al entorno (fuera del process list) ─
CFG=$(mktemp) || { echo "ERROR: no se pudo crear archivo temporal" >&2; exit 1; }
chmod 600 "$CFG"
cleanup() { rm -f "$CFG"; }
trap cleanup EXIT
trap 'cleanup; exit 130' INT
trap 'cleanup; exit 143' TERM
trap 'cleanup; exit 129' HUP
# escapar \ y " para el formato de config de curl (valores entre comillas)
ET_ESC=$(printf '%s' "$ET" | sed 's/\\/\\\\/g; s/"/\\"/g')
{
  printf 'header = "X-Auth-Token: %s"\n' "$ET_ESC"
  echo 'noproxy = "*"'
  echo 'silent'
  echo 'show-error'
  echo 'fail'                 # HTTP >=400 -> rc 22 (no se parsea un cuerpo de error como dato)
  echo 'connect-timeout = 5'
  echo 'max-time = 20'
} > "$CFG"
ET=; ET_ESC=   # ya no se necesitan en variables

# -q ignora curlrc del sistema/usuario; --config aporta auth + timeouts + noproxy
CURL() { curl -q --config "$CFG" "$@"; }
mono() { cut -d. -f1 /proc/uptime; }   # segundos monotónicos (inmune a ajustes de hora)

# api_get <path> <programa_jq>: imprime el valor VALIDADO; rc 0 solo si curl Y jq OK.
# curl en su propia sustitución (rc explícito); los filtros validan antes de emitir su único
# resultado, así que no hay salida parcial ante una entrada inválida.
api_get() {
  if _b=$(CURL "$API$1"); then
    printf '%s' "$_b" | jq -ers "$2" 2>/dev/null
  else
    return 1
  fi
}

# poll_until <jid> <deadline_monotónico>: stdout estado terminal (ok|error);
# rc 0 terminal, 2 sin terminal antes del deadline. Fallos de lectura son transitorios.
poll_until() {
  _jid=$1; _lim=$2
  while :; do
    if _est=$(api_get "/job/$_jid" "$JQ_ESTADO"); then
      case "$_est" in ok|error) printf '%s' "$_est"; return 0 ;; esac
    fi
    [ "$(mono)" -lt "$_lim" ] || return 2   # se comprobó ANTES de dormir (consulta final garantizada)
    # holgura intencional: la última pasada puede excederse ~POLL + max-time (~25s) sobre el
    # deadline; TimeoutStartSec del service es el respaldo duro
    sleep "$POLL"
  done
}

# op <ruta> <tipo_esperado> <descripcion>: rc 0 => job del tipo correcto terminó 'ok';
# 1 => terminó en 'error'; 2 => resultado DESCONOCIDO/no-terminal (o no tomó el gate).
op() {
  _ruta=$1; _tipo=$2; _desc=$3
  _glim=$(( $(mono) + DEADLINE_GATE ))
  _jid=""
  while :; do
    if [ -z "$_jid" ]; then
      if [ "$(mono)" -ge "$_glim" ]; then   # no lanzar un POST después de expirar el presupuesto del gate
        echo "ERROR: $_desc no pudo tomar el gate en ${DEADLINE_GATE}s" >&2
        return 2
      fi
      if _r=$(CURL -X POST -H 'Content-Type: application/json' -d '{"actor":"timer"}' "$API$_ruta"); then
        :
      else
        _rc=$?
        echo "ERROR: POST $_ruta falló (curl rc $_rc)" >&2
        return 2
      fi
      if ! _jid=$(printf '%s' "$_r" | jq -ers "$JQ_JOBID" 2>/dev/null); then
        echo "ERROR: $_ruta no devolvió un job_id válido" >&2
        return 2
      fi
    fi
    if _rtipo=$(api_get "/job/$_jid" "$JQ_TIPO"); then
      if [ "$_rtipo" = "$_tipo" ]; then
        break                       # es NUESTRA operación (nueva o ya en curso del mismo tipo)
      fi
      # otro tipo CONFIRMADO: el gate lo tiene otra op. Esperar su término (acotado al gate) y re-POST.
      echo "gate ocupado por '$_rtipo'; espero su término para lanzar $_desc…" >&2
      poll_until "$_jid" "$_glim" >/dev/null || true
      _jid=""
    else
      # tipo DESCONOCIDO (fallo transitorio de lectura): NO re-POST; reintentar leer el MISMO job.
      echo "no pude leer el tipo del job actual; reintento la lectura…" >&2
    fi
    if [ "$(mono)" -ge "$_glim" ]; then
      echo "ERROR: $_desc no pudo tomar el gate en ${DEADLINE_GATE}s" >&2
      return 2
    fi
    sleep 3
  done
  echo "$_desc -> job $_jid"
  if _est=$(poll_until "$_jid" "$(( $(mono) + DEADLINE_JOB ))"); then
    echo "$_desc terminó: $_est (job $_jid)"
    if [ "$_est" = "ok" ]; then return 0; fi
    return 1
  fi
  echo "ERROR: $_desc sin estado terminal en ~${DEADLINE_JOB}s (job $_jid)" >&2
  return 2
}

# ── readiness del motor (arranque / timer Persistent=true tras un reboot) ─────────
_rlim=$(( $(mono) + DEADLINE_READY ))
until CURL -o /dev/null "$API/health"; do
  [ "$(mono)" -lt "$_rlim" ] || { echo "ERROR: el motor no respondió /health en ${DEADLINE_READY}s" >&2; exit 1; }
  sleep 3
done

FALLA=0

# 1) purga — debe alcanzar terminal antes de reconciliar (requisito de orden)
if op /purgar-papelera purgar-papelera "purga de papelera"; then _pc=0; else _pc=$?; fi
if [ "$_pc" -eq 2 ]; then
  echo "ERROR: purga con resultado DESCONOCIDO/no-terminal — se aborta la mantención (no se reconcilia)" >&2
  exit 1
fi
[ "$_pc" -eq 0 ] || FALLA=1        # 'error' terminal: gate libre, se continúa con FALLA=1

# 2) reconciliación
if op /reconciliar reconciliar "reconciliación"; then _pc=0; else _pc=$?; fi
[ "$_pc" -eq 0 ] || FALLA=1        # 'error' terminal o desconocido -> falla

exit "$FALLA"
