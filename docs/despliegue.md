# Dónde vive el sistema (mapa de despliegue)

> Referencia operativa: qué componente corre dónde, con qué archivos, qué alcanza y
> cómo moverlo. Pensado para saber siempre "dónde está cada cosa" y para retomar u
> operar el sistema sin depender de la memoria.
>
> Última verificación: 2026-09-11.

---

## Vista de conjunto

Hay **tres piezas de infraestructura** en juego, en tres lugares distintos:

```
  ┌─────────────────────────────┐        ┌──────────────────────────────┐
  │ noc-monitor (192.168.122.252)│        │ ESXi 10.100.37.245           │
  │  = CONTROL PLANE             │  SSH   │  (gestionado por vCenter      │
  │                              │ +API   │   192.168.200.107)           │
  │  ┌────────────┐              │───────►│                              │
  │  │ vps-engine │ 127.0.0.1:8224│  govc  │  [DiscoA37245] VPS/          │
  │  │ (el motor) │◄──┐          │        │   ├── _plantillas/ (dorada)  │
  │  └────────────┘   │ proxy    │        │   ├── _papelera/             │
  │  ┌────────────┐   │ HTTP     │        │   ├── _bin/ (wrapper)        │
  │  │ dashboard  │───┘          │        │   └── vps-hcl-XXXX/ (VPS)    │
  │  │ (la vista) │              │        └──────────────────────────────┘
  │  └────────────┘              │
  │  ┌────────────┐┌───────────┐ │        ┌──────────────────────────────┐
  │  │ vaultwarden ││vps-provisn│ │  SSH   │ MikroTik RouterData          │
  │  │ (bóveda)    ││(securizar)│ │───────►│ 172.16.1.90                  │
  │  └────────────┘└───────────┘ │        │ (NAT + ARP → decide la IP)   │
  └─────────────────────────────┘        └──────────────────────────────┘
```

- **El sistema (motor) vive en noc-monitor**, como **contenedor propio y separado**
  (`vps-engine`), NO dentro del dashboard.
- El **dashboard solo lo invoca** por API para mostrarlo en la web. Son cajas
  independientes que se hablan por HTTP local.
- El motor **opera** el ESXi (crear/gestionar VMs) y **consulta** al MikroTik (elegir IP).

---

## 1. noc-monitor — el "control plane"

Es la VM de infraestructura donde vive todo el plano de control. Cuatro contenedores
**independientes** (Podman + quadlets):

| Contenedor | Rol | Escucha | Proyecto |
|---|---|---|---|
| **`vps-engine`** | **El motor** — crea/gestiona VPS | `127.0.0.1:8224` | Vps (este) |
| `hosting-dashboard` | La web del NOC (lo invoca) | vía nginx :443 | fastnetmon |
| `vaultwarden` | Bóveda de credenciales | `127.0.0.1:8222` | VpsClientes |
| `vps-provision` | Securización de VPS | `127.0.0.1:8223` | VpsClientes |

Cada uno es una cajita aparte: si uno se cae o se mueve, los otros siguen.

### Por qué noc-monitor es buen lugar (alcance verificado 2026-09-11)

El motor necesita alcanzar tres cosas, y desde noc-monitor **llega a las tres**:

| Debe alcanzar | Para qué | ¿Llega? |
|---|---|---|
| ESXi 10.100.37.245 / vCenter | crear y gestionar las VMs | ✅ |
| MikroTik RouterData 172.16.1.90 | elegir IP privada (NAT+ARP) | ✅ |
| Red de VPS producción 10.100.16.0/24 | entrar por SSH a la VM creada (verificar, cPanel, securizar) | ✅ (ruta vía gateway) |

Ruta por defecto de noc-monitor: `default via 192.168.122.1`. Desde ahí rutea a la red
de VPS y al MikroTik.

---

## 2. El motor `vps-engine` — sus partes (todo en noc-monitor)

| Qué | Dónde | Notas |
|---|---|---|
| Quadlet (define el contenedor) | `/etc/containers/systemd/vps-engine.container` | `Network=host`, healthcheck |
| Código + build | `/opt/vps-engine/build/` | `engine/app.py`, `Containerfile`, `marcas/`, `sabores/` |
| **Config y secretos** | `/opt/vps-engine/engine.env` | chmod 600 · token, credenciales svc-vps, MODO, dorada |
| Datos (registro) | `/opt/vps-engine/data/registry.db` | SQLite · VMs, jobs, auditoría · volumen `:/data` |
| Llaves SSH | `/opt/vps-engine/keys/` | `vps_engine_esxi` (wrapper) · `vps_engine_mgmt` (gestión) · volumen `:/keys` ro |
| API | `127.0.0.1:8224` | header `X-Auth-Token` |

**Secretos — nunca en el repo.** Solo en `engine.env` (600, root) y las llaves privadas
en `keys/` (600). El repo solo lleva código, sabores y llaves públicas.

---

## 3. El dashboard — solo la ventana

- Código: `/opt/hosting-dashboard/build/dashboard.py` (contenedor `hosting-dashboard`,
  imagen `hosting-dashboard:v2`, servicio `dashboard.service`).
- La sección **"VPS (crear/gestionar)"** hace de proxy: rutas `/api/vpseng/*` que llaman
  al motor en `127.0.0.1:8224` con el `ENGINE_TOKEN` (guardado en
  `/opt/hosting-dashboard/.env` del host).
- Permiso `vps_engine` en la matriz de roles (asignado al rol `noc`).
- **El tablero de avances** (`docs/tablero.html`) se sirve como estático en
  `https://noc.hosting.cl/vps` (nginx: `location = /vps` → `/usr/share/nginx/html/vps-tablero.html`).

Relación clave: **el dashboard puede vivir, morir o moverse sin afectar al motor**, y
viceversa. El único vínculo es la URL del proxy + el token.

---

## 4. El ESXi / vCenter — dónde se materializan los VPS

- **Host de trabajo:** `10.100.37.245` (ESXi 7.0.3), **gestionado por vCenter**
  `192.168.200.107` (`VCENTER200107.DEDICADOS.CL`, 10 hosts / 54 VMs).
- Todo lo del sistema vive bajo `[DiscoA37245] VPS/`:
  - `_plantillas/` → la **dorada** (`dorada-almalinux9.7`)
  - `_papelera/` → VPS eliminados (retención 7 días)
  - `_bin/` → el **wrapper** SSH (`vps-wrapper.sh`)
  - `vps-hcl-XXXX/` → cada VPS de cliente
- **Cuenta API:** `svc-vps` (rol mínimo `VpsOperator`) — el motor nunca usa root.
- **Acceso SSH del motor:** llave `vps_engine_esxi` con `command=` forzado al wrapper.
- ⚠️ **Nota vCenter:** como el motor opera a nivel de host, cada borrado deja una entrada
  "huérfana" en el vCenter (se quita con **"Quitar del inventario"**, nunca "Eliminar del
  disco"). Pendiente Fase 2: apuntar el motor al vCenter para inventario consistente.

---

## 5. La red — quién decide y transporta las IPs

- **MikroTik RouterData `172.16.1.90`** → la fuente de verdad para elegir IP privada
  (tablas NAT + ARP) y para crear el NAT de la IP pública. Ver
  [README §Cómo se elige la IP](../README.md#cómo-se-elige-la-ip-del-vps-sin-conflictos).
- **Red de VPS producción:** `10.100.16.0/24` — VLAN **81**, portgroup **`Vps_Hosting.cl`**
  (vSwitch4, uplink por cable directo al switch del rack1). Aquí nacen los VPS reales.
- **Red de pruebas:** `192.168.122.0/24` (portgroup "Switch Interno 1 Data ethr6").

---

## Cómo mover el sistema a otra VM/host (si algún día hace falta)

El motor es una **cajita portátil**. Para mudarlo:

1. Copiar a la nueva VM: el quadlet, `/opt/vps-engine/engine.env`, `/opt/vps-engine/data/`
   y `/opt/vps-engine/keys/`. Reconstruir la imagen (`podman build`).
2. Verificar que la nueva VM **alcance** ESXi/vCenter, RouterData y la red de VPS.
3. En el dashboard, cambiar la URL del proxy de `127.0.0.1:8224` a `<ip-nueva>:8224`
   (una línea) y asegurar ese camino (que el 8224 solo lo alcance el dashboard, con token;
   hoy escucha solo en localhost).

Nada de esto reescribe lógica: es mover archivos y apuntar el proxy.

---

## Resumen — "dónde vive cada cosa"

| Cosa | Vive en |
|---|---|
| **El sistema (motor)** | noc-monitor · contenedor `vps-engine` · `127.0.0.1:8224` |
| Config y secretos del motor | noc-monitor · `/opt/vps-engine/engine.env` |
| Registro (VMs/jobs/auditoría) | noc-monitor · `/opt/vps-engine/data/registry.db` |
| Llaves del motor | noc-monitor · `/opt/vps-engine/keys/` |
| La web que lo muestra | noc-monitor · contenedor `hosting-dashboard` (solo lo invoca) |
| Tablero de avances | `https://noc.hosting.cl/vps` |
| Las VMs / la dorada / la papelera | ESXi 10.100.37.245 · `[DiscoA37245] VPS/` |
| Inventario general de VMs | vCenter 192.168.200.107 |
| Decisión de IP + NAT | MikroTik RouterData 172.16.1.90 |
| Bóveda de credenciales | noc-monitor · `vaultwarden` · `https://noc.hosting.cl/vault` |
| Código fuente (repo) | github.com/alcalmx/Vps |
