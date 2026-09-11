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
