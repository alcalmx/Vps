# Bitácora — Vps

> Registro cronológico para retomar con contexto. Más reciente arriba.
> Lee primero [README.md](README.md) y [SEGURIDAD.md](SEGURIDAD.md).

---

## 2026-09-10 (jueves, tarde-4) — svc-vps con rol mínimo verificado + dorada CONSTRUYÉNDOSE

**Cuenta y rol (usuario ejecutó 01 y 02; Claude completó lo que el head+pipefail cortó):**
- `svc-vps` creado en el ESXi. El script 02 creó el rol pero ABORTÓ antes de aplicar
  el permiso (bug: `head -5` + pipefail — corregido en el repo). Claude aplicó
  `govc permissions.set svc-vps → VpsOperator` y verificó:
  - `esxcli system permission list` → svc-vps = **Custom** (ya no Admin).
  - Intento de auto-elevación (`permissions.set → Admin` como svc-vps) → **denegado** ✔
  - Nota: leer info del host (p.ej. autostart.info) SÍ puede — es solo lectura
    (System.Read implícito), no es hallazgo.
- `engine.env` completado con `MGMT_PUBKEY`/`MGMT_PRIVKEY_PATH` de la nueva llave
  **vps_engine_mgmt** (ed25519, generada en /opt/vps-engine/keys/ — el motor NO
  reutiliza gestion_hosting porque esa privada tiene passphrase).

**Dorada dorada-almalinux9.7 — construcción lanzada (en curso al cierre):**
- ks.cfg real (IP temporal 192.168.122.239, pubkey del motor) → mini-ISO **OEMDRV**
  generado EN EL VPS WINDOWS con IMAPI2FS/PowerShell (noc-monitor no tiene
  genisoimage y no se quiso instalar software; script: scratchpad/make-oemdrv-iso.ps1,
  técnica reutilizable).
- ISO subido a `_plantillas/`; wrapper `mkdir-vm` + `create-disk 10` OK.
- **Trampa vmx encontrada:** un vmx mínimo sin `pciBridge0/4-7` NO enciende
  ("No PCIe slot available for SCSI0"). Fix: bloque estándar de pciBridges —
  **agregado también al VMX_TEMPLATE del engine** (mismo bug aplicaba).
- VM registrada y encendida con svc-vps (register/power.on del rol mínimo: funcionan).
  Anaconda instala desatendido; al terminar reboot → dorada-seal limpia identidad y
  APAGA. Monitor en background espera el poweredOff.

**Siguiente al terminar la dorada:** unregister de la VM de construcción (queda solo
el directorio con el vmdk en _plantillas/), build de la imagen vps-engine en
noc-monitor + quadlet (pedir OK: deploy a producción), sección dashboard, prueba
end-to-end vps-hcl-0001.

---

## 2026-09-10 (jueves, tarde-3) — Código del motor completo + infra del ESXi preparada

**Autorización del usuario:** construir la automatización de creación, lanzable desde
el dashboard y con el proceso VISIBLE paso a paso ("saber si se queda parado en algún
punto"). Diseño de visibilidad: toda operación es un job asíncrono con pasos
(pendiente→corriendo→ok/error + detalle + hora) persistidos en SQLite; el dashboard
hace polling de /job/<id> cada 3 s. Spec UI en docs/dashboard-integracion.md.

**Código escrito (en el repo):**
- [engine/app.py](engine/app.py) — vps-engine completo: jobs con pasos, registro
  SQLite (vms/jobs/operaciones), flujos crear (13 pasos, incl. cPanel última versión
  con polling del log), suspender/reanudar, eliminar (papelera), editar (delta de
  sabor; disco solo crece), purga, auditoría. Guardarraíles en código
  (`guardarraices()`: registro + regex prefijo).
- [engine/Containerfile](engine/Containerfile) + [engine/vps-engine.container](engine/vps-engine.container)
  (quadlet, Network=host, 127.0.0.1:8224, healthcheck).
- [esxi/vps-wrapper.sh](esxi/vps-wrapper.sh) — wrapper SSH restringido (subcomandos:
  ping, mkdir-vm, create-disk [solo doradas], clone-disk, grow-disk, trash-vm,
  restore-trash, purge-trash [>7 días], list-*, df).
- [dorada/ks.cfg](dorada/ks.cfg) + [dorada/README.md](dorada/README.md) — construcción
  DESATENDIDA de la dorada desde la ISO local vía kickstart en mini-ISO OEMDRV
  (anaconda lo toma solo). Particionado con / al final (growpart), cloud-init
  datasource VMware, sella identidad y se apaga al primer boot.
- [docs/dashboard-integracion.md](docs/dashboard-integracion.md) — spec de la sección
  "VPS" del NOC (3 tabs: Crear con panel de progreso en vivo, Gestionados con
  acciones, Jobs/auditoría).

**Infra ejecutada HOY:**
- ESXi: creado `[DiscoA37245] VPS/` con `_plantillas/ _papelera/ _bin/`.
- noc-monitor: `/opt/vps-engine/{keys,data}`, llave **RSA 4096** `vps_engine_esxi`
  (RSA por el FIPS del ESXi), `engine.env` (600) con token + credenciales svc-vps.
- Wrapper subido a `_bin/` y llave instalada en authorized_keys del ESXi con
  `command=` forzado. **Probado:** `ping`→pong; `ls /etc`→denegado;
  `trash-vm noc-monitor.hosting.cl`→denegado por regex; `df`→OK.

**Bloqueado por el clasificador de permisos (requiere al usuario):**
- Crear la cuenta `svc-vps` en el ESXi y (2º paso) instalar govc en noc-monitor +
  crear rol mínimo. Scripts listos y revisables:
  - `sh scripts/01-esxi-cuenta-svc-vps.sh '<GOVC_PASSWORD del engine.env>'`
  - `ssh noc-monitor 'bash -s' < scripts/02-rol-vpsoperator.sh`
  El rol **VpsOperator** deliberadamente SIN `VirtualMachine.Inventory.Delete`
  (la API no puede destruir archivos de VM ni comprometida; borrar = wrapper→papelera).

**Pendiente (orden):**
- [ ] Usuario ejecuta scripts 01 y 02 (o autoriza a Claude a correrlos)
- [ ] Construir la dorada (dorada/README.md) — ~15 min desatendido
- [ ] Build de la imagen vps-engine + quadlet en noc-monitor (deploy con OK)
- [ ] Sección "VPS" en el dashboard (docs/dashboard-integracion.md) + permiso rol
- [ ] Prueba end-to-end: crear vps-hcl-0001 desde el dashboard viendo el progreso

---

## 2026-09-10 (jueves, tarde-2) — NAT/IP pública al flujo, red de pruebas y cPanel definidos

**Definiciones del usuario:**
- **NAT + IP pública**: también se copia del NOC. Mapeado el código real:
  `/api/alta/publica` (dashboard.py ~9365) elige la pública de la address-list de la
  marca con 5 validaciones (habilitada sin comentario / sin NAT incl. deshabilitados /
  sin ARP vivo / sin otras address-lists / muda al ping desde el CCR 172.16.1.69);
  `/api/alta/crear` (~9414) crea srcnat+dstnat en RouterData (comment "[NOC] ..."),
  deshabilita+comenta la entrada de la address-list, registra ambas IPs en NetBox y
  pausa el Monitoreo Externo. Agregado como pasos 2–3 del flujo Crear (README).
  **En pruebas se omite** (solo IP privada).
- **cPanel**: los 3 planes lo incluyen → instalarlo automáticamente (última versión)
  tras el primer boot, vía SSH con la llave de gestión. Tarda 30–60 min → tarea en
  background + notificación. Futuro: dorada con cPanel preinstalado.
- **Red de PRUEBAS: 192.168.122.0/24, gw 192.168.122.1** — la 10.100.48.0/24 está en
  otra VLAN que no llega al host de pruebas; se usará cuando creemos VPS reales en un
  host real. Verificado en el ESXi: la 192.168.122.x va por el portgroup
  **"Switch Interno 1 Data ethr6"** (el de noc-monitor, Claude_Code y Prueba1).
  Mismo proceso de IP libre (validar que RouterData vea la 122 en ARP; si no,
  barrido local desde noc-monitor).
- **ISO local vs centralizada**: pregunta abierta del usuario — propuesta de Claude:
  biblioteca **centralizada** (datastore NFS montado en todos los hosts) como estándar
  de flota, porque la ISO solo se usa para construir doradas (1 vez por versión de SO)
  y centralizada evita duplicados/desincronización; las **doradas sí van locales** en
  cada host (el clon vmkfstools debe ser local para ser rápido). Implementar al sumar
  el 2º host; fase 1 sigue con la ISO local. PENDIENTE ok del usuario.

**marcas/hosting.cl.json** actualizado: red_default (producción) + red_pruebas +
ip_publica_nat documentados.

---

## 2026-09-10 (jueves, tarde) — Sabores, red y repo definidos por el usuario

**Respuestas del usuario a las preguntas abiertas:**
1. **Sabores** (de la web hosting.cl, captura 2026-09): creados en `sabores/hosting.cl/`
   — `vps-estandar` (4 vCPU/4 GB/100 GB SSD, $99.900+IVA/mes), `vps-empresas`
   (4 vCPU/6 GB/150 GB, $119.900), `vps-cyber-black` (6 vCPU/8 GB/150 GB, $179.900,
   CyberBoost 4/año). Los 3 incluyen 30 Licencia cPanel, backups externos semanales,
   1 IP y SSL.
2. **Red de los VPS: 10.100.48.0/24.** La búsqueda de IP libre se COPIA de la que
   ya existe en el NOC: `/api/alta/privada` (dashboard.py ~9336) — SSH al MikroTik
   RouterData 172.16.1.90, NAT+ARP de la /24, ocupadas = NAT ∪ ARP ∪ {.1}, asigna
   el octeto más alto libre desde .254 hacia abajo. Documentado en marcas/hosting.cl.json.
3. **SO: AlmaLinux con la ISO que YA está en el host** (encontradas con find profundo;
   el barrido inicial era maxdepth 2): `[datastore1 (7)] AlmaLinux-9.7-x86_64-minimal.iso`
   y `[DiscoA37245] AlmaLinux-8.10-x86_64-minimal (1).iso`. La dorada se construye
   una vez desde la 9.7 + cloud-init + open-vm-tools. Más ISOs/SOs después.
4. **Cupos: postergados** — primero hacer funcionar la creación en 10.100.37.245.
5. **Repo creado: https://github.com/alcalmx/Vps.git** → push inicial hecho
   (con el workaround DNS `http.curloptResolve=github.com:443:140.82.112.3`,
   igual que DomoticaHome).

**Además:** el usuario confirmó que la securización (vps-provision) y la bóveda
(Vaultwarden) **se siguen usando** con este proyecto, y que probablemente la
securización sea absorbida por este proyecto más adelante. README actualizado.

**Preguntas abiertas nuevas:**
- ¿Qué portgroup del ESXi transporta la 10.100.48.0/24? (candidato: "Switch
  Interno 1 Data ethr6"; verificar antes de la primera creación)
- ¿Gateway 10.100.48.1? (asumido por convención del wizard de altas)
- **cPanel**: los 3 planes lo incluyen — ¿la dorada lo trae preinstalado (licencia
  se activa por IP) o se instala post-creación? Impacta el diseño de la dorada.

**Pendiente (orden sugerido):**
- [ ] Confirmar portgroup + gateway con el usuario
- [ ] Crear en ESXi: `[DiscoA37245] VPS/` (_plantillas, _papelera, _bin),
      usuario `svc-vps` + rol custom, llave `vps_engine_esxi` + wrapper
- [ ] Plantilla dorada AlmaLinux 9.7 desde la ISO del host
- [ ] engine/: esqueleto Flask + govc + registro SQLite; `crear` end-to-end
      contra `vps-hcl-0000-test`

---

## 2026-09-10 (jueves) — Nace el proyecto: diseño completo documentado

**Contexto (pedido del usuario):** automatizar el ciclo de vida de los VPS de
clientes en VMware — crear, eliminar, suspender, editar. Marca inicial hosting.cl
(sabores estáticos de la web, el usuario los dictará). Fase 1 visible en el
dashboard NOC. Futuro: WHMCS dispara la creación. Host de pruebas: 10.100.37.245.

**Qué se hizo:**
- **Reconocimiento del ESXi 10.100.37.245:** ESXi 7.0 U3 (build 22348816),
  16 cores/32 threads, 160 GB RAM, datastores `datastore1` (95 GB, libre) y
  `DiscoA37245` (1.8 TB, 1.1 TB libres). 10 VMs registradas — es el host de
  PRODUCCIÓN (noc-monitor, Claude_Code=VPS de IA, xrp-node en "Prueba2"...).
  Portgroups: VM Network, Lan Pfsense, VTR, Switch Interno 1 Data ethr6 (7
  clientes activos), Red de máquinas virtuales (0 activos), Interconnect.
  **No hay ISOs en los datastores; no hay ovftool en el host.**
- **Diseño completo** en [README.md](README.md): plantilla dorada (OVA GenericCloud
  AlmaLinux con cloud-init) + clon `vmkfstools` thin + VM por govc + config por
  guestinfo cloud-init. Motor `vps-engine` en noc-monitor (127.0.0.1:8224, patrón
  vps-provision). Registro SQLite + auditoría. Flujos de crear/eliminar(papelera
  7 días)/suspender(power off + estado)/editar(CPU/RAM/crecer disco).
- **Modelo de seguridad** en [SEGURIDAD.md](SEGURIDAD.md): 5 capas — registro de
  gestionadas, prefijo `vps-` + ruta bajo `VPS/`, usuario API `svc-vps` con rol
  mínimo (no root), SSH con `command=` wrapper que whitelistea subcomandos y rutas,
  papelera con retención. Cupos de recursos por host para no ahogar producción.
- **Estructura de sabores y marcas:** [sabores/SCHEMA.md](sabores/SCHEMA.md) define
  el formato JSON de un plan; [marcas/hosting.cl.json](marcas/hosting.cl.json)
  creado con la red default PENDIENTE de definir.
- Verificado que NO duplica a VpsClientes (ese securiza post-instalación; este
  crea las máquinas — se encadenan al final del flujo de creación).

**Decisiones:** ver tabla "Decisiones de diseño" del README (clon vmkfstools por
falta de vCenter; GenericCloud OVA en vez de ISO; SQLite; papelera; suspensión =
power off comercial, no suspend VMware).

**Preguntas abiertas para el usuario (bloquean los siguientes pasos):**
1. **Sabores de hosting.cl**: dictar los planes de la web (vCPU/RAM/disco/nombre).
2. **Red de los VPS de clientes**: ¿qué portgroup usar? ¿rango de IPs, gateway,
   máscara? (¿o VLAN nueva?) — hoy las VMs cuelgan mayormente de
   "Switch Interno 1 Data ethr6".
3. **SO**: ¿AlmaLinux 9 como dorada inicial? (hoy los clientes reciben "casi
   siempre AlmaLinux" según VpsClientes).
4. **Cupos**: propuse máx 64 GB RAM / 24 vCPU / 600 GB disco para clientes en
   este host — ¿ajustamos?
5. **Repo GitHub privado** `alcalmx/Vps`: ¿lo creas para hacer push?

**Pendiente (orden sugerido):**
- [ ] Respuestas 1–5 de arriba
- [ ] Crear en ESXi: `[DiscoA37245] VPS/` (_plantillas, _papelera, _bin),
      usuario `svc-vps` + rol custom, llave `vps_engine_esxi` + wrapper
- [ ] Plantilla dorada AlmaLinux (descargar OVA GenericCloud, desplegar con
      ovftool desde noc-monitor, ajustar: hot-add, open-vm-tools, growpart)
- [ ] engine/: esqueleto Flask + govc + registro SQLite; primero `crear`
      end-to-end contra una VM de prueba `vps-hcl-0000-test`
- [ ] Resto de operaciones + sección "VPS" en dashboard NOC

---

<!-- Plantilla para nuevas entradas:
## AAAA-MM-DD (día) — Título
**Qué se hizo:**
**Decisiones:**
**Pendiente:**
-->
