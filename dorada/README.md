# Plantillas doradas — catálogo y construcción

## Catálogo (decisión del usuario 2026-09-11)

**Por cada versión de sistema operativo se mantienen DOS doradas:**

| Dorada | Contenido | Para qué |
|---|---|---|
| `dorada-<so><ver>` | SO base + cloud-init + open-vm-tools + growpart + llave de gestión + sshd endurecido | VPS sin panel; y es la BASE desde la que se construye la variante cPanel |
| `dorada-<so><ver>-cpanel` | Lo mismo + **cPanel última versión preinstalado** (sin licencia activada; se registra al primer boot con su IP/hostname) | VPS con cPanel: baja la entrega de ~45 min a ~7 min. Los 3 planes de hosting.cl lo incluyen |

Catálogo actual:
- `dorada-almalinux9.7` ✅ (v4, con growpart)
- `dorada-almalinux9.7-cpanel` ⏳ pendiente de construir
- Futuros SOs (almalinux8.10, ubuntu, …) siguen el mismo patrón de a pares.

**Selección automática (motor):** al crear con "Instalar cPanel" marcado, el motor clona la
variante `-cpanel` si existe (entrega rápida) y solo si no existe cae al plan B de instalar
cPanel post-creación (30–60 min). Sin cPanel marcado → clona la base.

**Mantenimiento:** las doradas se reconstruyen periódicamente (parches) con la fábrica
automatizada (kickstart + OEMDRV, ~20 min solas). La variante cPanel se reconstruye a partir
de la base: clonar → instalar cPanel → preparación para plantilla (limpiar identidad cPanel)
→ sellar → mover a `_plantillas/`.

**Política de mantenimiento (usuario 2026-09-11):** el camino principal es SIEMPRE clonar
doradas (rápido con y sin cPanel); la instalación post-creación de cPanel (30-60 min) es solo
un fallback de emergencia y probablemente no sobreviva en el proyecto. Mantenimiento:
- Los VPS clonados **se auto-actualizan** (cPanel trae `upcp` nocturno + dnf del SO) — la
  dorada no necesita estar al día al minuto.
- **Refrescar las doradas ~mensualmente** (o cuando cPanel salte de versión mayor): con la
  fábrica automatizada es ~1 hora casi toda desatendida (base ~20 min + variante ~40 min).
  A futuro puede programarse (reconstrucción automática periódica con notificación).

**Licenciamiento cPanel (importante):** cPanel se licencia **por IP**. La dorada `-cpanel`
lleva cPanel instalado pero SIN licencia amarrada (identidad limpiada al sellar); cada clon
intenta activar la licencia con SU IP al primer boot (`cpkeyclt`). En pruebas corre con el
trial (15 días); en producción la **IP pública del VPS debe estar en el pool de licencias**
de hosting.cl (las "30 Licencia cPanel" de los planes). El formulario ya no pregunta por
cPanel: **lo decide el plan** (extras.cpanel_licencia_cuentas > 0 → usa la dorada -cpanel).

---

# Construcción de la plantilla dorada base (dorada-almalinux9.7)

> Instalación **100 % desatendida** desde la ISO que ya está en el host, usando
> kickstart en un mini-ISO con etiqueta **OEMDRV** (anaconda lo detecta y aplica
> solo, sin tocar el menú de arranque). Se hace UNA vez por versión de SO.

## Resultado

VM apagada `dorada-almalinux9.7` en `[DiscoA37245] VPS/_plantillas/`, AlmaLinux 9.7
minimal con: cloud-init (datasource VMware/guestinfo), open-vm-tools, llave de
gestión en root, sshd endurecido (root solo con llave, sin contraseñas), identidad
sellada (sin machine-id, sin host keys, cloud-init clean) y particionado con `/`
al final para que growpart crezca el disco de cada clon al tamaño del sabor.

## Procedimiento (lo ejecuta el que construye, típicamente desde noc-monitor)

1. **Preparar ks.cfg**: tomar [ks.cfg](ks.cfg) y reemplazar `__IP_TEMPORAL__`
   (una IP libre de 192.168.122.0/24, solo se usa durante el %post para dnf)
   y `__MGMT_PUBKEY__` (la pública de gestion@hosting.cl).

2. **Mini-ISO OEMDRV** (en noc-monitor; requiere genisoimage → `dnf -y install genisoimage`):
   ```bash
   mkdir -p /tmp/oemdrv && cp ks.cfg /tmp/oemdrv/
   genisoimage -V OEMDRV -o /tmp/ks-oemdrv.iso /tmp/oemdrv
   ```
   (La etiqueta del volumen DEBE ser exactamente `OEMDRV`.)

3. **Subir al datastore** (con govc, credenciales svc-vps):
   ```bash
   govc datastore.upload -ds DiscoA37245 /tmp/ks-oemdrv.iso VPS/_plantillas/ks-oemdrv.iso
   ```

4. **Crear la VM de construcción** (vía wrapper + govc):
   ```bash
   # wrapper: mkdir-vm dorada-almalinux9.7 ; create-disk dorada-almalinux9.7 10
   # vmx de construcción: 2 vCPU, 2048 MB, disco pvscsi, red "Switch Interno 1
   # Data ethr6" y DOS cdroms IDE:
   #   ide0:0 → /vmfs/volumes/datastore1 (7)/AlmaLinux-9.7-x86_64-minimal.iso
   #   ide0:1 → [DiscoA37245] VPS/_plantillas/ks-oemdrv.iso
   # bios.bootOrder = "cdrom,hdd" solo para la instalación
   # subir vmx con govc datastore.upload + govc vm.register + vm.power -on
   ```

5. **Esperar**: anaconda instala solo (~10-15 min) → reboot → `dorada-seal.service`
   limpia identidad y **apaga la VM**. Cuando `govc vm.info` muestre poweredOff
   estable, la instalación terminó.

6. **Sellar la plantilla**: quitar los 2 cdroms del vmx (o marcarlos
   `present=FALSE`), quitar `bios.bootOrder`, y **des-registrar la VM del
   inventario** (`govc vm.unregister`) — la dorada queda SOLO como directorio en
   `_plantillas/` (su .vmdk es lo único que usa clone-disk). Así nadie la
   enciende por error.

7. **Probar**: crear un VPS de prueba vía el engine (`POST /crear`) y verificar
   que el clon toma hostname/IP del cloud-init y que la llave de gestión entra.

## ⚠️ Lección de la 1ª construcción (2026-09-10)

- **cloud-init y open-vm-tools DEBEN ir en `%packages`, no en un `dnf` de `%post`.**
  La VLAN de construcción (192.168.122.0/24) tiene NAT por whitelist de IP de origen
  → una VM nueva no tiene salida a internet, el `dnf` del %post falla en silencio
  (el %post usa `set -x`, no `set -e`) y la dorada queda SIN esos paquetes. El
  síntoma: los clones arrancan con la identidad de la dorada (hostname `dorada`,
  sin personalizar) porque no hay cloud-init que lea el guestinfo. Anaconda instala
  `%packages` desde el repo del propio ISO → independiente de la red.
- **Red DHCP en el kickstart, no IP estática**: si se hornea una IP, cada clon
  arranca con ella hasta que cloud-init la cambia (y si cloud-init falla, se queda
  pegado ahí). Con DHCP no hay identidad de red horneada.

## Notas

- La dorada NO trae cPanel (decisión 2026-09-10: cPanel se instala post-creación,
  última versión). Cuando optimicemos con "dorada+cPanel", será otra plantilla
  (`dorada-almalinux9.7-cpanel`) construida sobre esta.
- Para nuevas versiones de SO: mismo procedimiento con otra ISO → otro nombre
  `dorada-<so><ver>`. El sabor elige con `so_default`.
- La ISO de instalación vive local en el host (fase 1). Propuesta a futuro:
  biblioteca NFS centralizada de ISOs montada en todos los hosts.

---

# Construcción de la plantilla dorada Ubuntu (dorada-ubuntu26.04)

> **Camino distinto al de AlmaLinux (mucho más corto):** Ubuntu publica **imágenes cloud
> oficiales en formato OVA** que ya traen cloud-init, open-vm-tools, growpart y netplan.
> No hace falta kickstart, ni ISO, ni salida a internet en la red de construcción.
> Construida el 2026-10-01 en esxi-20051 (~25 min, casi todo transferencia de archivos).

## Resultado

`_plantillas/dorada-ubuntu26.04/` — Ubuntu 26.04 LTS (resolute), **2,3 GB reales** (disco de
10 GB thin), con: llave de gestión en root, sshd endurecido (root solo con llave), datasource
**VMware/guestinfo** forzado, identidad sellada y open-vm-tools habilitado. Sin panel (cPanel
no soporta 26.04) — es la dorada para los VPS "limpios".

## Por qué este camino

- La imagen oficial YA trae lo necesario: `cloud-init 26.1`, `open-vm-tools 13`,
  `cloud-guest-utils` (growpart), `netplan`, `openssh-server` → **no se instala nada**, y por
  eso no importa que la red de construcción (192.168.200.0/24) no tenga salida a internet.
- La OVA declara **pvscsi + vmxnet3**, el mismo hardware del `VMX_TEMPLATE` del motor → los
  clones arrancan sin ajustes.

## Procedimiento (reproducible)

1. **Descargar y verificar** (en noc-monitor, `/opt/doradas-build`):
   ```sh
   curl -sL -o SHA256SUMS https://cloud-images.ubuntu.com/releases/resolute/release/SHA256SUMS
   curl -L -o ubuntu-26.04-server-cloudimg-amd64.ova \
        https://cloud-images.ubuntu.com/releases/resolute/release/ubuntu-26.04-server-cloudimg-amd64.ova
   sha256sum -c <(grep 'amd64.ova' SHA256SUMS | tr -d '*')
   ```
   (`26.04` redirige al nombre clave **resolute**.)
2. **Extraer** el OVA (`tar xf`) → `.ovf` + `.vmdk` (streamOptimized, 792 MB).
3. **Transferir el disco al host** y convertirlo a thin nativo (la API de svc-vps NO puede
   importar OVAs — privilegio mínimo; se usa la **llave de bootstrap** del motor, root shell):
   ```sh
   cat *.vmdk | ssh -i <bootstrap_root> root@<host> "cat > <ds>/build-ubuntu/src.vmdk"
   ssh ... "cd <ds>/build-ubuntu && vmkfstools -i src.vmdk -d thin dorada-ubuntu26.04.vmdk && rm src.vmdk"
   ```
4. **VMX de construcción** = el `VMX_TEMPLATE` del motor con `guestOS = "ubuntu-64"` + la
   semilla cloud-init en `guestinfo.metadata`/`guestinfo.userdata` (gzip+base64). La semilla
   versionada: [ubuntu-seed.yaml](ubuntu-seed.yaml).
5. **Registrar y encender**: `vim-cmd solo/registervm` + `vmsvc/power.on`. La VM se configura
   y **se apaga sola** (~2,5 min) — ese apagado ES la señal de éxito del sellado.
6. **Des-registrar**, mover la carpeta a `_plantillas/dorada-ubuntu26.04/` y limpiar las líneas
   `guestinfo.*` del vmx de la plantilla (higiene: el motor escribe su propio vmx al clonar).

## Verificado en la construcción

- **El datasource VMware funciona en Ubuntu**: cloud-init leyó la semilla por guestinfo y
  ejecutó todo (es el mismo mecanismo que usarán los clones). ✅
- El wrapper del motor la lista: `list-plantillas` → `dorada-ubuntu26.04`. ✅

## CONTRATO de la dorada (lo que el motor asume)

- **Sin configuración de red propia**: se sella sin `/etc/netplan/50-cloud-init.yaml` ni otros
  YAML de red. El ÚNICO origen de la red del clon es el motor (cloud-init por guestinfo).
- `datasource_list: [ VMware, NoCloud, None ]` → el clon lee su config del guestinfo del VMX.
- Identidad vacía: sin machine-id, sin host keys, cloud-init limpio (cada clon genera la suya).
- Root con la **llave de gestión** y sshd endurecido (`PermitRootLogin prohibit-password`,
  sin contraseñas) — es como entra el motor a verificar y securizar.
- open-vm-tools habilitado (el motor lee la IP por VMware Tools).

## ✅ Soporte en el motor (desplegado 2026-10-01)

El motor ya elige SO: catálogo `SISTEMAS` (SO → dorada + familia + si admite cPanel), respeta el
`so_default` del plan, el NOC puede forzar otro SO al crear, y **genera la red según la familia**
(netplan para Ubuntu, nmcli para AlmaLinux). Plan listo para vender: `vps-estandar-ubuntu`.

**PENDIENTE operativo:** prueba real de creación Ubuntu en staging (IP/ruta/DNS/SSH + reinicio,
verificando qué archivos de red reaparecen) antes de ofrecerlo a clientes, y copiar la dorada a
los demás hosts (hoy solo está en esxi-20051).
