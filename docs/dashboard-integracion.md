# Integración con el dashboard NOC — sección "VPS"

> Especificación de lo que se agrega a `/opt/hosting-dashboard/build/dashboard.py`
> en noc-monitor (patrón calcado de la sección "VPS Clientes"). El deploy a
> producción requiere OK explícito del usuario.

## Backend (proxy a vps-engine 127.0.0.1:8224 con ENGINE_TOKEN del .env)

| Ruta dashboard | Método | Proxy a | Permiso |
|---|---|---|---|
| `/api/vpseng/crear` | POST | `/crear` (+ `actor` = usuario de la sesión) | `vps_engine` |
| `/api/vpseng/job/<id>` | GET | `/job/<id>` | `vps_engine` |
| `/api/vpseng/jobs` | GET | `/jobs` | `vps_engine` |
| `/api/vpseng/vms` | GET | `/vms` | `vps_engine` |
| `/api/vpseng/accion` | POST | `/accion` | `vps_engine` |
| `/api/vpseng/editar` | POST | `/editar` | `vps_engine` |
| `/api/vpseng/salud` | GET | `/health` (para poblar selects de marca/sabor) | `vps_engine` |

- Permiso nuevo **`vps_engine`** en `_ALL_PAGES`, `PROTECTED_PAGES`, `permPages`
  y al rol `noc` (mismo procedimiento que `vps_clientes`, BITACORA VpsClientes 03-08).
- `ENGINE_TOKEN` se agrega a `/opt/hosting-dashboard/.env` **del host**.

## Página "VPS" (nav en Infraestructura Lógica) — 3 tabs

### Tab 1 — Crear
Formulario: marca (select), sabor (select con vCPU/RAM/disco visibles), cliente,
hostname, checkbox "instalar cPanel" (marcado). Botón **Crear VPS**.

Al enviar → guarda `job_id` → muestra el **panel de progreso en vivo**:

```
Creando vps-hcl-0001-acme                        [job 3f2a9c · corriendo]
  ✔ Validar datos y asignar nombre        vps-hcl-0001-acme · VPS Estándar
  ✔ Buscar IP privada libre               IP asignada: 192.168.122.246
  ✔ Clonar disco de la plantilla dorada   14:32:05
  ⟳ Instalar cPanel (última versión)      instalando… 22 min (tarda 30-60)
  · Registrar y finalizar
```

- **Polling** `GET /api/vpseng/job/<id>` cada 3 s mientras `estado == "corriendo"`.
- Ícono por estado del paso: `·` pendiente, `⟳` corriendo (spinner), `✔` ok,
  `✖` error (fila en rojo + texto del error). El campo `detalle` de cada paso se
  muestra al lado — ahí se ve "por dónde va" y dónde se atascó.
- Al terminar: banner verde (ok, con IP y acceso WHM) o rojo (error + en qué paso).
- El panel es re-abrible: la lista de "Jobs recientes" (tab 3) permite volver a
  ver el progreso de cualquier job, incluso terminado.

### Tab 2 — VPS gestionados
Tabla de `GET /api/vpseng/vms`: nombre, cliente, sabor, IP, estado del registro,
power real (verde/gris), fecha. Acciones por fila:
- **Suspender / Reanudar** → `POST /api/vpseng/accion` → mismo panel de progreso.
- **Editar** → select de sabor destino → `POST /api/vpseng/editar` → panel.
- **Eliminar** → modal que exige reescribir el nombre exacto (va como
  `confirmacion`) → panel. Muestra aviso "va a papelera, purga en 7 días".
Debajo: panel "Papelera" (entradas + fecha, restauración manual por ahora vía
wrapper `restore-trash`).

### Tab 3 — Jobs y auditoría
Lista de `GET /api/vpseng/jobs` (últimos 30: tipo, VM, estado, cuándo) — click
abre el panel de progreso del job. Sirve como historial de notificaciones de
creaciones/acciones y para retomar un job que quedó corriendo si se cerró la página.

## Nota de visibilidad (pedido del usuario 2026-09-10)

Esta primera etapa TODO se visualiza en el dashboard para poder ver si el proceso
se queda parado en algún paso. Más adelante, cuando el flujo esté maduro y lo
dispare WHMCS, la sección seguirá existiendo como monitor/auditoría aunque ya no
se use para lanzar creaciones a mano.
