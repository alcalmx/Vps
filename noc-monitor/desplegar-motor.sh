#!/bin/bash
# Despliegue del motor vps-engine en noc-monitor (guion reutilizable; nació con el multi-marca 2026-10-23).
# Uso: scp engine/app.py marcas/ sabores/ a /tmp/vps-marcas/ (engine/app.py, marcas/, sabores/) y luego
#      ssh noc-monitor 'bash /tmp/vps-marcas/desplegar_motor_marcas.sh' — o copiar este guion a /tmp/vps-marcas/.
# Lee ENGINE_TOKEN con grep (NO con source: engine.env tiene valores con espacios, p.ej. MGMT_PUBKEY).
# Espera los archivos nuevos en /tmp/vps-marcas/{engine/app.py,marcas/,sabores/}.
# Respaldo del build completo, copia, build de la imagen, reinicio y verificación por /health.
set -eu
B=/opt/vps-engine/build
SRC=/tmp/vps-marcas
TS=$(date +%Y%m%d-%H%M)
SUF="${1:-deploy}"   # sufijo del respaldo (ej: marcas, uso, pub)
[ -s "$SRC/engine/app.py" ] && [ -d "$SRC/marcas" ] && [ -d "$SRC/sabores" ] || { echo "faltan archivos en $SRC"; exit 1; }
echo "== respaldo del build =="
tar -C /opt/vps-engine -czf "/opt/vps-engine/build.bak-$SUF-$TS.tgz" build
cp -p "$B/engine/app.py" "$B/engine/app.py.bak-$SUF-$TS"
echo "respaldo: /opt/vps-engine/build.bak-$SUF-$TS.tgz y app.py.bak-$SUF-$TS"
echo "== copia =="
cp "$SRC/engine/app.py" "$B/engine/app.py"
rm -rf "$B/marcas" "$B/sabores"
cp -r "$SRC/marcas" "$SRC/sabores" "$B/"
echo "md5 app.py en build: $(md5sum "$B/engine/app.py" | cut -d' ' -f1)"
ls "$B/marcas"
echo "== build de la imagen (1-3 min) =="
cd "$B"
podman build -q -t localhost/vps-engine:latest -f engine/Containerfile . >/dev/null || {
  echo "BUILD FALLÓ: el servicio sigue con la imagen anterior; restaurar con: tar -C /opt/vps-engine -xzf /opt/vps-engine/build.bak-$SUF-$TS.tgz"; exit 1; }
echo "== reinicio =="
systemctl restart vps-engine
code=""
for i in $(seq 1 40); do
  code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8224/health || true)
  [ "$code" = "200" ] && break
  sleep 1
done
echo "vps-engine: $(systemctl is-active vps-engine) · HTTP $code tras ${i}s"
[ "$code" = "200" ] || { echo "NO responde 200: podman logs vps-engine; rollback con el tgz"; podman logs --since 3m vps-engine 2>&1 | tail -20; exit 1; }
if podman logs --since 3m vps-engine 2>&1 | grep -qi traceback; then
  echo "ATENCIÓN: tracebacks en el arranque"; podman logs --since 3m vps-engine 2>&1 | grep -i -A8 traceback | head -30; exit 1
fi
echo "== marcas que expone /health (rol admin) =="
ENGINE_TOKEN=$(grep '^ENGINE_TOKEN=' /opt/vps-engine/engine.env | cut -d= -f2- | tr -d '"')
curl -s -H "X-Auth-Token: $ENGINE_TOKEN" http://127.0.0.1:8224/health \
  | podman exec -i vps-engine python3 -c 'import json,sys; h=json.load(sys.stdin); print("marcas:", h["marcas"]); [print(" -", d["marca"], "|", d["nombre"], "|", d["prefijo_vm"], "|", d["red_default"]["subred"], "VLAN", d["red_default"]["vlan"], "| pub", d["pub_lista"], "| planes", d["sabores"]) for d in h["marcas_detalle"]]'
echo "imagen: $(podman images --format '{{.ID}} creada {{.Created}}' localhost/vps-engine:latest)"
echo "DEPLOY MOTOR OK"
