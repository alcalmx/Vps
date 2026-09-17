#!/bin/sh
# Mantención diaria del motor VPS (se instala en /usr/local/bin/vps-mantencion.sh
# y lo dispara vps-mantencion.timer): 1) purga de papelera (>7 días, allowlist del
# registro) y 2) reconciliación de las 4 fuentes. Ambos quedan como jobs visibles
# en el dashboard (pestaña Jobs) y sus alertas en la tabla de auditoría.
# La reconciliación espera a que la purga TERMINE: comparten el gate de mantención
# (vm='-') y dispararla antes devolvería el job de purga en vez de reconciliar.
# El exit code refleja la mantención completa: 0 solo si AMBOS jobs terminaron 'ok'
# (un job en 'error' o sin estado terminal deja el service 'failed' en systemd).
set -eu

ET=$(sed -n 's/^ENGINE_TOKEN=//p' /opt/vps-engine/engine.env)
[ -n "$ET" ] || { echo "ERROR: ENGINE_TOKEN no encontrado en engine.env" >&2; exit 1; }
API=http://127.0.0.1:8224
FALLA=0

# poll_job <job_id>: imprime el estado final (ok|error); rc 1 si no llega a terminal
poll_job() {
  _est=""
  for _ in $(seq 1 30); do
    _j=$(curl -sf -H "X-Auth-Token: $ET" "$API/job/$1")
    _est=$(printf '%s' "$_j" | sed -n 's/.*"estado": *"\(corriendo\|ok\|error\)".*/\1/p' | head -1)
    if [ "$_est" = "ok" ] || [ "$_est" = "error" ]; then
      printf '%s' "$_est"
      return 0
    fi
    sleep 10
  done
  return 1
}

# lanzar <ruta> : dispara el POST y deja el job_id en $JID (aborta si no hay id)
lanzar() {
  _r=$(curl -sf -X POST -H "X-Auth-Token: $ET" -H 'Content-Type: application/json' \
       -d '{"actor":"timer"}' "$API$1")
  echo "$1 -> $_r"
  JID=$(printf '%s' "$_r" | sed -n 's/.*"job_id": *"\([a-f0-9]\{6,\}\)".*/\1/p')
  [ -n "$JID" ] || { echo "ERROR: $1 no devolvió job_id" >&2; exit 1; }
}

lanzar /purgar-papelera
EST=$(poll_job "$JID") || { echo "ERROR: la purga no llegó a estado terminal en ~5 min (job $JID)" >&2; exit 1; }
echo "purga termino: $EST (job $JID)"
[ "$EST" = "ok" ] || FALLA=1

lanzar /reconciliar
EST=$(poll_job "$JID") || { echo "ERROR: la reconciliación no llegó a estado terminal en ~5 min (job $JID)" >&2; exit 1; }
echo "reconciliacion termino: $EST (job $JID)"
[ "$EST" = "ok" ] || FALLA=1

exit $FALLA
