# Vps — Ciclo de vida automatizado de VPS en VMware ESXi

> Sistema que automatiza el ciclo de vida completo de los VPS de clientes:
> **crear, eliminar, suspender/reanudar y editar** máquinas virtuales en los hosts
> ESXi de las marcas de hosting (partimos con **hosting.cl**).
> Fase 1: operado desde el dashboard NOC. Futuro: disparado por altas en WHMCS.

---

## Qué resuelve

Hoy los VPS se crean a mano en la UI de ESXi (instalación desde ISO, configuración
manual). Este proyecto los crea **desde una plantilla dorada en segundos**, con el
sabor (plan) que el cliente contrató en la web, y deja registro auditable de cada
operación.

**Alcance fase 1 (en construcción):**
1. **Crear** un VPS desde un sabor (plan estático de la web de hosting.cl).
2. **Eliminar** un VPS (con papelera de retención — nada se borra al instante).
3. **Suspender / reanudar** un VPS (cliente moroso, abuso, etc.).
4. **Editar** un VPS (CPU, RAM, crecer disco = upgrade de plan).

**Futuro conversado (NO fase 1):** webhook desde WHMCS (alta de servicio → creación
automática), dashboard propio, más marcas (Planeta Hosting, etc.), IPAM vía NetBox.

---

## Relación con proyectos existentes (NO duplicar)

| Proyecto | Qué hace | Relación |
|---|---|---|
| **VpsClientes** (`vps-provision`, puerto 8223) | Securiza un VPS **ya instalado**: genera llave del cliente, la guarda en Vaultwarden, endurece sshd | La securización y la **bóveda se siguen usando** (confirmado por el usuario 2026-09-10): la creación encadena con vps-provision para entregar la llave del cliente. A futuro este proyecto probablemente **absorba** la securización dentro de su propio flujo (la bóveda queda igual). |
| **ServerVmware** | Operación del host ESXi 10.100.37.245 (backups ghettoVCB, hardware) | Los backups NO cubren los VPS de clientes por defecto (`vms.txt` es lista explícita) — decidir política. |
| **fastnetmon / dashboard NOC** | El dashboard donde vivirá la sección "VPS" (crear/listar/acciones/notificaciones) | Mismo patrón que la sección "VPS Clientes": página + proxy a API interna con token. |
| **NetBox (NETBOX2 :8090)** | IPAM vigente | Futuro: reservar IP del VPS automáticamente. Fase 1: IP se ingresa a mano. |

---

## Arquitectura (fase 1)

```
 Operador (dashboard NOC, noc.hosting.cl)
   sección "VPS" — crear / listar / suspender / editar / eliminar + notificaciones
        │  POST /api/vps/... (login + permiso vps_engine)
        ▼
 ┌──────────────────────────────────────────────┐
 │ vps-engine (contenedor en noc-monitor)        │
 │ 127.0.0.1:8224, red de host                   │
 │ Flask + govc + paramiko                       │
 │ - registro de VMs gestionadas (SQLite)        │
 │ - log de auditoría append-only                │
 │ - sabores y marcas (JSON, de este repo)       │
 └───────┬──────────────────────────┬───────────┘
         │ API vSphere (443)        │ SSH restringido (wrapper)
         │ usuario svc-vps          │ llave vps_engine_esxi
         │ (rol custom, NO root)    │ solo vmkfstools/mkdir en /VPS/
         ▼                          ▼
 ┌──────────────────────────────────────────────┐
 │ ESXi host (pruebas: 10.100.37.245, 7.0 U3)    │
 │ [DiscoA37245]/VPS/          ← SOLO aquí opera │
 │   ├── _plantillas/  (VMs doradas por SO)      │
 │   ├── _papelera/    (eliminados, retención)   │
 │   └── vps-hcl-XXXX/ (VPS de clientes)         │
 └──────────────────────────────────────────────┘
```

### Componentes

1. **vps-engine** — servicio API interna en noc-monitor (patrón calcado de
   vps-provision): contenedor podman con quadlet, escucha solo `127.0.0.1:8224`,
   auth por header `X-Auth-Token`. Código en [engine/](engine/).
2. **Sección "VPS" en el dashboard NOC** — formulario de creación (marca, sabor,
   cliente, hostname, IP), tabla de VPS gestionados con acciones y estado en vivo,
   panel de notificaciones/auditoría.
3. **Registro de VMs gestionadas** — SQLite en `/opt/vps-engine/data/registry.db`.
   Fuente de verdad de qué VMs puede tocar el sistema. **Toda operación destructiva
   exige que la VM esté en el registro** (ver [SEGURIDAD.md](SEGURIDAD.md)).
4. **Plantillas doradas** — una VM apagada por SO en `[DiscoA37245] VPS/_plantillas/`
   (AlmaLinux primero), con cloud-init + open-vm-tools. La creación clona su disco.
5. **Sabores** — planes estáticos de la web, JSON por marca en [sabores/](sabores/).
   El usuario los dicta y aquí quedan versionados.

---

### Visibilidad del proceso (jobs con pasos)

Toda operación (crear/eliminar/suspender/editar) corre como **job asíncrono** cuyo
avance se persiste paso a paso en SQLite: cada paso con estado
`pendiente → corriendo → ok/error`, detalle y hora. El dashboard hace polling de
`/job/<id>` cada 3 s y pinta la lista de pasos en vivo — se ve exactamente por
dónde va la creación y en qué paso se atascó si falla. Spec de la UI en
[docs/dashboard-integracion.md](docs/dashboard-integracion.md).

## Flujo de cada operación

### Crear
1. Validar entrada: marca + sabor existen, hostname único.
2. **IP privada libre** — lógica copiada de `/api/alta/privada` del NOC (NAT+ARP del
   MikroTik RouterData 172.16.1.90; ocupadas = NAT ∪ ARP ∪ {.1}; el octeto libre más
   alto desde .254). En **pruebas**: red 192.168.122.0/24; en producción: la subred
   de la marca (hosting.cl: 10.100.48.0/24).
3. **IP pública + NAT** — lógica copiada de `/api/alta/publica` y `/api/alta/crear`
   del NOC: elegir pública de la address-list de la marca (habilitada, sin comentario,
   sin NAT, sin ARP, sin otras listas, muda al ping) → crear `srcnat` + `dstnat` en
   RouterData con etiqueta `[NOC]`, deshabilitar/comentar la entrada de la
   address-list, registrar ambas IPs en NetBox, pausar Monitoreo Externo.
   En **pruebas** este paso se omite (la VM queda solo con la privada).
4. Verificar cupo del host (RAM/CPU/disco comprometidos vs límites de `hosts.json`).
5. SSH (wrapper): `mkdir [DiscoA37245]/VPS/vps-hcl-<id>` + `vmkfstools -i` clon
   **thin** del disco dorado.
6. govc: crear VM (CPU/RAM/red del sabor), adjuntar el disco clonado.
7. Inyectar configuración por **guestinfo cloud-init** (hostname, IP estática,
   gateway, DNS, llave pública de gestión). La contraseña NUNCA viaja.
8. Power on → esperar IP/estado por VMware Tools (timeout con reintentos).
9. **Instalar cPanel (última versión)** vía SSH con la llave de gestión — tarda
   30–60 min; corre como tarea en segundo plano con notificación al terminar.
   (Optimización futura: dorada con cPanel preinstalado para entrega casi instantánea.)
10. Registrar en el registro + auditoría + notificación en NOC.
11. (Encadenable) llamar a vps-provision → llave del cliente a la bóveda.

### Eliminar (papelera, nunca destrucción directa)
1. La VM debe estar en el registro (si no, se rechaza).
2. Confirmación doble en el dashboard (escribir el nombre de la VM).
3. Apagar (graceful, luego forzado) → unregister → **mover** el directorio a
   `VPS/_papelera/<fecha>-<nombre>/`.
4. Purga definitiva tras **7 días** (job diario), o restauración manual antes.

### Suspender / Reanudar
- **Suspender cliente** (caso hosting: morosidad): apagado graceful (forzado a los
  60 s) + estado `suspendido` en el registro. No es el "suspend" de VMware (no
  necesitamos guardar la RAM; ocupa disco y complica).
- **Reanudar**: power on + verificación de que levanta (Tools/IP).

### Editar (upgrade/downgrade de plan)
- CPU/RAM: requiere la VM apagada (shutdown → reconfigure → power on; ventana de
  ~1 min). La plantilla dorada llevará hot-add habilitado para que a futuro los
  **upgrades** sean en caliente.
- Disco: **solo crecer** (nunca reducir — corrompe). `vmkfstools -X` + growpart
  dentro del guest. La dorada llevará cloud-init con `growpart` automático al boot.
- Cambio de sabor = aplicar deltas del sabor destino + registrar el cambio.

---

## Decisiones de diseño

| Decisión | Elección | Por qué |
|---|---|---|
| Cómo crear VMs | Plantilla dorada + clon `vmkfstools` + cloud-init guestinfo | ESXi standalone (sin vCenter) no tiene clone por API. El clon de disco + VMX propio es el método robusto y rápido (~segundos con thin). Sin instalar nada en el host. |
| Origen de la dorada | Instalación única desde la **ISO AlmaLinux 9.7** que ya está en el host (`[datastore1 (7)] AlmaLinux-9.7-x86_64-minimal.iso`) + instalar cloud-init y open-vm-tools a mano | Decisión del usuario 2026-09-10: partir con el AlmaLinux que ya tenemos en este VMware; después se agregan más ISOs/SOs. (También hay AlmaLinux 8.10 en DiscoA37245.) |
| Búsqueda de IP libre | Copiar la lógica de `/api/alta/privada` del dashboard NOC | Ya probada en producción: consulta NAT+ARP del MikroTik RouterData (172.16.1.90) por SSH y asigna el octeto libre más alto de la /24. Se aplica sobre la subred de la marca (hosting.cl: 10.100.48.0/24). |
| IP pública + NAT | Copiar `/api/alta/publica` + `/api/alta/crear` del NOC | Mismo criterio ya probado: candidata de la address-list validada 5 veces (lista/NAT/ARP/otras listas/ping) → srcnat+dstnat en RouterData + NetBox + pausa de monitoreo. |
| Red de PRUEBAS | 192.168.122.0/24, gw .1, portgroup "Switch Interno 1 Data ethr6" (verificado: es el de noc-monitor y el VPS de IA) | La 10.100.48.0/24 está en otra VLAN que no llega a este host de pruebas. Producción usará 10.100.48.0/24 en el host real. Sin NAT/pública en pruebas. |
| cPanel | Post-instalación automática, última versión, tras el primer boot | Decisión del usuario 2026-09-10. Tarda 30–60 min → tarea en background con notificación. Futuro: dorada con cPanel preinstalado. |
| API al ESXi | govc con usuario local `svc-vps` (rol custom) | Sin vCenter igual hay API por 443. Rol con privilegios mínimos de VM; la clave NO es la de root. |
| SSH al ESXi | Llave dedicada + `command=` wrapper que whitelistea operaciones | Solo lo que la API no puede (vmkfstools). El wrapper valida que toda ruta esté bajo `/VPS/`. Detalle en SEGURIDAD.md. |
| Dónde corre el motor | noc-monitor, contenedor + quadlet | Patrón probado (vps-provision), mismo dashboard, misma operación. |
| Registro | SQLite (no JSON plano) | Concurrencia (dashboard + jobs), histórico de operaciones consultable. |
| Eliminación | Papelera con retención 7 días | El host de pruebas ES el de producción: un borrado equivocado sin papelera sería catastrófico. |
| Suspensión | Power off + estado, no suspend VMware | El caso de uso es comercial (moroso), no de checkpoint. |
| Multi-marca | `marcas/<marca>.json` + prefijo de nombre (`vps-hcl-`, `vps-ph-`...) | hosting.cl primero; agregar marca = un JSON + sabores, sin tocar código. |
| Multi-host | `hosts.json` con credenciales/cupos por host | Fase 1 un host; el diseño ya es N hosts. |

---

## ⚠️ El host de "pruebas" es el de producción

`10.100.37.245` corre las VMs de infraestructura (noc-monitor, Claude_Code —
**este mismo VPS de IA** —, monitoreo_red, unifi, rsyslogmk, xrp-node en Prueba2...).
"Crear y borrar sin miedo" aplica **solo a lo que este sistema crea** dentro de
`[DiscoA37245] VPS/`. Los guardarraíles del sistema (registro + prefijo + wrapper
SSH + papelera) existen precisamente por esto. Ver [SEGURIDAD.md](SEGURIDAD.md).

Recursos del host (2026-09-10): 16 cores / 32 threads, 160 GB RAM,
DiscoA37245 1.8 TB (1.1 TB libres). Definir cupo reservado para infra en `hosts.json`.

---

## Estructura del repo

```
Vps/
├── README.md          ← este archivo (visión + arquitectura)
├── SEGURIDAD.md       ← modelo de amenazas y controles (leer antes de codear)
├── BITACORA.md        ← registro cronológico de sesiones
├── CLAUDE.md          ← instrucciones para retomar el proyecto
├── marcas/            ← una marca por JSON (redes, prefijos, DNS)
│   └── hosting.cl.json
├── sabores/           ← planes estáticos por marca (los dicta el usuario)
│   ├── SCHEMA.md      ← formato de un sabor
│   └── hosting.cl/
│       └── (pendiente: el usuario dictará los planes de la web)
├── engine/            ← código del servicio vps-engine (pendiente)
└── esxi/              ← wrapper SSH y utilitarios que viven en el host (pendiente)
```

## Estado y roadmap

- [x] Diseño y documentación (2026-09-10)
- [x] Sabores de hosting.cl dictados de la web (estándar / empresas / cyber-black)
- [x] Subred de los VPS: 10.100.48.0/24 (IP libre: lógica de /api/alta/privada del NOC)
- [x] Red de pruebas: 192.168.122.0/24 gw .1, portgroup "Switch Interno 1 Data ethr6"
- [x] cPanel: post-instalación automática con la última versión (futuro: dorada con cPanel)
- [x] Flujo NAT + IP pública: copiar /api/alta/publica y /api/alta/crear del NOC (solo producción)
- [ ] Confirmar portgroup de la 10.100.48.0/24 en el host de producción (cuando toque)
- [ ] ISOs: propuesta = biblioteca centralizada NFS montada en todos los hosts
      (implementar al sumar el 2º host; fase 1 usa la ISO local del host de pruebas)
- [ ] Crear en ESXi: carpeta `VPS/`, usuario `svc-vps` + rol, llave + wrapper SSH
- [ ] Plantilla dorada AlmaLinux 9.7 (instalar desde la ISO del host + cloud-init + open-vm-tools)
- [ ] engine: crear (end-to-end contra VM de prueba)
- [ ] engine: eliminar (papelera) / suspender / editar
- [ ] Sección "VPS" en dashboard NOC + notificaciones
- [ ] Encadenar con vps-provision (llave del cliente a la bóveda)
- [ ] Futuro: WHMCS → creación automática; NetBox IPAM; más marcas
