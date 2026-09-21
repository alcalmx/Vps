#!/bin/sh
# enrolar-host.sh — Fase C multi-host: prepara un ESXi NUEVO para que el motor lo
# adopte. Se corre EN noc-monitor (root) con la password de root del ESXi nuevo a
# mano (se usa 2 veces: API y SSH). Automatiza:
#   1. Par de llaves SSH dedicado del host (rsa 4096) en /opt/vps-engine/keys/
#   2. Rol VpsOperator (los 46 privilegios EXACTOS del esxi-245) + usuario svc-vps
#      con password generada — vía govc con credenciales root TEMPORALES (no se guardan)
#   3. Estructura VPS/{_plantillas,_papelera,_bin} en el datastore + wrapper con su
#      BASE correcto + authorized_keys con forced-command (capa 4 de SEGURIDAD.md)
#   4. Huella del host al known_hosts pinned del motor (#9)
# QUEDA MANUAL: copiar las doradas a _plantillas/ (pesado — vía vCenter o vmkfstools),
# agregar GOVC_PASSWORD_<ID> a engine.env + restart, y el clic "Validar y enrolar"
# en la pestaña Motor del dashboard.
set -eu

[ $# -ge 3 ] || { echo "uso: $0 <id ej. esxi-121> <ip> <datastore> [ssh_port=22]" >&2; exit 1; }
HID=$1; HIP=$2; HDS=$3; HPORT=${4:-22}
echo "$HID" | grep -Eq '^[a-z0-9][a-z0-9-]{1,30}$' || { echo "id inválido" >&2; exit 1; }
echo "$HIP" | grep -Eq '^[0-9]{1,3}(\.[0-9]{1,3}){3}$' || { echo "ip inválida" >&2; exit 1; }

KEYS=/opt/vps-engine/keys
KH=$KEYS/known_hosts
LLAVE=$KEYS/vps_engine_esxi_$(echo "$HID" | tr - _)
WRAPPER_SRC=/opt/vps-engine/build/vps-wrapper.sh
IDUP=$(echo "$HID" | tr 'a-z-' 'A-Z_')
PASSVAR="GOVC_PASSWORD_$IDUP"
BASE_NUEVO="/vmfs/volumes/$HDS/VPS"

[ -f "$WRAPPER_SRC" ] || { echo "falta $WRAPPER_SRC (copiar del repo esxi/vps-wrapper.sh)" >&2; exit 1; }

printf "Password de ROOT del ESXi %s (solo para el enrolamiento, no se guarda): " "$HIP"
stty -echo; read -r ROOTPASS; stty echo; echo

SVCPASS=$(openssl rand -base64 18 | tr -d '=+/' | cut -c1-20)Aa1!

# ── 1. llave dedicada del host ────────────────────────────────────────────────
if [ ! -f "$LLAVE" ]; then
  ssh-keygen -t rsa -b 4096 -N "" -q -C "vps-engine@noc-monitor ($HID)" -f "$LLAVE"
  chmod 600 "$LLAVE"
  echo "[1/5] llave generada: $LLAVE"
else
  echo "[1/5] llave ya existía: $LLAVE (se reutiliza)"
fi
PUB=$(cat "$LLAVE.pub")

# ── 2. rol VpsOperator + usuario svc-vps (govc, credenciales root temporales) ─
GV() {
  podman run --rm --network host --entrypoint /usr/local/bin/govc \
    -e GOVC_URL="https://$HIP/sdk" -e GOVC_USERNAME=root \
    -e GOVC_PASSWORD="$ROOTPASS" -e GOVC_INSECURE=1 \
    localhost/vps-engine:latest "$@"
}
PRIVS="Datastore.AllocateSpace Datastore.Browse Datastore.DeleteFile Datastore.FileManagement \
Datastore.UpdateVirtualMachineFiles Global.CancelTask Global.LogEvent Network.Assign \
Resource.AssignVMToPool System.Anonymous System.Read System.View Task.Create Task.Update \
VirtualMachine.Config.AddExistingDisk VirtualMachine.Config.AddNewDisk \
VirtualMachine.Config.AddRemoveDevice VirtualMachine.Config.AdvancedConfig \
VirtualMachine.Config.Annotation VirtualMachine.Config.CPUCount VirtualMachine.Config.DiskExtend \
VirtualMachine.Config.EditDevice VirtualMachine.Config.Memory VirtualMachine.Config.ReloadFromPath \
VirtualMachine.Config.RemoveDisk VirtualMachine.Config.Rename VirtualMachine.Config.ResetGuestInfo \
VirtualMachine.Config.Resource VirtualMachine.Config.Settings VirtualMachine.Interact.AnswerQuestion \
VirtualMachine.Interact.ConsoleInteract VirtualMachine.Interact.DeviceConnection \
VirtualMachine.Interact.PowerOff VirtualMachine.Interact.PowerOn VirtualMachine.Interact.Reset \
VirtualMachine.Interact.SetCDMedia VirtualMachine.Interact.Suspend VirtualMachine.Interact.ToolsInstall \
VirtualMachine.Inventory.Create VirtualMachine.Inventory.CreateFromExisting \
VirtualMachine.Inventory.Register VirtualMachine.Inventory.Unregister \
VirtualMachine.State.CreateSnapshot VirtualMachine.State.RemoveSnapshot \
VirtualMachine.State.RevertToSnapshot"
if GV role.ls VpsOperator >/dev/null 2>&1; then
  echo "[2/5] rol VpsOperator ya existe en $HIP"
else
  # shellcheck disable=SC2086
  GV role.create VpsOperator $PRIVS
  echo "[2/5] rol VpsOperator creado (46 privilegios, idéntico al esxi-245)"
fi
if GV host.account.create -id svc-vps -password "$SVCPASS" 2>/dev/null; then
  echo "      usuario svc-vps creado"
else
  GV host.account.update -id svc-vps -password "$SVCPASS"
  echo "      usuario svc-vps ya existía → password ROTADA a la nueva"
fi
GV permissions.set -principal svc-vps -role VpsOperator
echo "      permiso svc-vps=VpsOperator aplicado"

# ── 3. datastore + wrapper + authorized_keys (SSH root, multiplexado: 1 prompt) ─
CTL="-o ControlMaster=auto -o ControlPath=/tmp/enrolar-$HID.sock -o ControlPersist=120 \
-o StrictHostKeyChecking=accept-new -p $HPORT"
echo "[3/5] preparando datastore y wrapper (te pedirá la password de root UNA vez por SSH)…"
# shellcheck disable=SC2086
sed "s|^BASE=.*|BASE=$BASE_NUEVO|" "$WRAPPER_SRC" | ssh $CTL "root@$HIP" \
  "mkdir -p $BASE_NUEVO/_plantillas $BASE_NUEVO/_papelera $BASE_NUEVO/_bin && \
   cat > $BASE_NUEVO/_bin/vps-wrapper.sh && chmod 755 $BASE_NUEVO/_bin/vps-wrapper.sh && \
   echo '  wrapper instalado con BASE=$BASE_NUEVO'"
LINEA="command=\"$BASE_NUEVO/_bin/vps-wrapper.sh\",no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty $PUB"
# shellcheck disable=SC2086
echo "$LINEA" | ssh $CTL "root@$HIP" \
  "grep -qF \"\$(cat | cut -d' ' -f3)\" /etc/ssh/keys-root/authorized_keys 2>/dev/null && \
   echo '  llave ya estaba en authorized_keys' || true"
# instalación real (idempotente por material de llave)
# shellcheck disable=SC2086
ssh $CTL "root@$HIP" "grep -qF '$(echo "$PUB" | awk '{print $2}' | cut -c1-40)' /etc/ssh/keys-root/authorized_keys 2>/dev/null" || \
  echo "$LINEA" | ssh $CTL "root@$HIP" "cat >> /etc/ssh/keys-root/authorized_keys && echo '  llave agregada con forced-command'"
# cerrar el socket de control
# shellcheck disable=SC2086
ssh $CTL -O exit "root@$HIP" 2>/dev/null || true

# ── 4. huella al known_hosts pinned (#9) ─────────────────────────────────────
TMP=$(mktemp)
ssh-keyscan -T 10 -p "$HPORT" "$HIP" > "$TMP" 2>/dev/null
grep -q . "$TMP" || { echo "ERROR: keyscan vacío de $HIP" >&2; rm -f "$TMP"; exit 1; }
# quitar huellas viejas de esta ip y agregar las frescas
grep -v "^$HIP " "$KH" 2>/dev/null > "$KH.tmp" || true
cat "$KH.tmp" "$TMP" > "$KH" && rm -f "$KH.tmp" "$TMP"
chmod 644 "$KH"
echo "[4/5] huella de $HIP agregada al known_hosts pinned"

# ── 5. resumen y pasos manuales ──────────────────────────────────────────────
echo
echo "[5/5] LISTO EN EL HOST. Pasos finales (manuales):"
echo "  a) AGREGAR a /opt/vps-engine/engine.env esta línea (el secreto NO queda en logs ni BD):"
echo "       $PASSVAR=$SVCPASS"
echo "     y reiniciar el motor:  systemctl restart vps-engine"
echo "  b) COPIAR las doradas al datastore nuevo (pesado; vía vCenter clone o vmkfstools):"
echo "       $BASE_NUEVO/_plantillas/dorada-almalinux9.7/  (+ la -cpanel)"
echo "  c) En la pestaña MOTOR del dashboard → 'Enrolar host nuevo' con:"
echo "       id=$HID  ip=$HIP  datastore=$HDS  ssh_key=/keys/$(basename "$LLAVE")  pass_env=$PASSVAR"
echo "     El motor valida en vivo (API + wrapper pong + datastore) antes de aceptar."
