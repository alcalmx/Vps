#!/bin/bash
# Paso 2 (en noc-monitor, como root): instala govc, crea el rol mínimo
# VpsOperator por API y degrada svc-vps de Admin a ese rol.
#   ssh noc-monitor 'bash -s' < scripts/02-rol-vpsoperator.sh
set -euo pipefail

# ── govc ──
if ! command -v govc >/dev/null; then
  curl -fsSL -o /tmp/govc.tgz \
    https://github.com/vmware/govmomi/releases/download/v0.51.0/govc_Linux_x86_64.tar.gz
  tar -xzf /tmp/govc.tgz -C /usr/local/bin govc
  rm -f /tmp/govc.tgz
fi
govc version

# credenciales del engine (svc-vps, aún Admin)
set -a; source /opt/vps-engine/engine.env; set +a

# ── Rol VpsOperator: privilegios mínimos del motor ──
# Nota deliberada: SIN VirtualMachine.Inventory.Delete → aunque la credencial API
# se filtre completa, NO puede destruir los archivos de una VM (borrar = solo
# vía wrapper SSH → papelera). Sin privilegios de host/usuarios/red física.
PRIVS=(
  Datastore.AllocateSpace Datastore.Browse Datastore.FileManagement
  Datastore.DeleteFile Datastore.UpdateVirtualMachineFiles
  Network.Assign
  Resource.AssignVMToPool
  Task.Create Task.Update Global.CancelTask Global.LogEvent
  VirtualMachine.Config.AddExistingDisk VirtualMachine.Config.AddNewDisk
  VirtualMachine.Config.AddRemoveDevice VirtualMachine.Config.AdvancedConfig
  VirtualMachine.Config.Annotation VirtualMachine.Config.CPUCount
  VirtualMachine.Config.DiskExtend VirtualMachine.Config.EditDevice
  VirtualMachine.Config.Memory VirtualMachine.Config.RemoveDisk
  VirtualMachine.Config.Rename VirtualMachine.Config.ReloadFromPath
  VirtualMachine.Config.ResetGuestInfo VirtualMachine.Config.Resource
  VirtualMachine.Config.Settings
  VirtualMachine.Interact.PowerOn VirtualMachine.Interact.PowerOff
  VirtualMachine.Interact.Suspend VirtualMachine.Interact.Reset
  VirtualMachine.Interact.AnswerQuestion VirtualMachine.Interact.DeviceConnection
  VirtualMachine.Interact.SetCDMedia VirtualMachine.Interact.ToolsInstall
  VirtualMachine.Interact.ConsoleInteract
  VirtualMachine.Inventory.Create VirtualMachine.Inventory.CreateFromExisting
  VirtualMachine.Inventory.Register VirtualMachine.Inventory.Unregister
  VirtualMachine.State.CreateSnapshot VirtualMachine.State.RemoveSnapshot
  VirtualMachine.State.RevertToSnapshot
)

if govc role.ls VpsOperator >/dev/null 2>&1; then
  govc role.update VpsOperator "${PRIVS[@]}"
  echo "Rol VpsOperator actualizado."
else
  govc role.create VpsOperator "${PRIVS[@]}"
  echo "Rol VpsOperator creado."
fi
# (sin head: con pipefail, head corta el pipe y aborta el script a mitad de camino)
govc role.ls VpsOperator

# ── Degradar svc-vps: Admin → VpsOperator (en la raíz del host, propagado) ──
govc permissions.set -principal svc-vps -role VpsOperator -propagate=true /
echo "Permiso aplicado:"
govc permissions.ls | grep -i svc-vps || true

# ── Verificación: puede ver VMs, NO puede tocar config del host ──
echo "— VMs visibles con svc-vps:"; govc ls /ha-datacenter/vm | head -12
echo "— Intento prohibido (host autostart) — debe FALLAR:"
govc host.autostart.info >/dev/null 2>&1 && echo "⚠ PUDO (revisar rol)" || echo "OK: denegado"
