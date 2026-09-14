# WHMCS de hosting.cl — hallazgos de la exploración (Fase 3)

> Levantado el 2026-09-14 explorando el WHMCS real con acceso admin. Es la **referencia
> técnica** para construir el conector `hostingcl_vps`. Complementa el diseño de
> [integracion-whmcs.md](integracion-whmcs.md) y el [flujo final](/vps-flujo).

## Dónde vive y cómo se alcanza

- **WHMCS:** `201.148.105.100` — mismo datacenter que noc-monitor.
- **Panel admin:** `https://panel.hosting.cl/admin/`
- **Red:** noc-monitor lo alcanza en **0.35 ms** (ruta interna vía `192.168.122.1`,
  iface `ens192`, src `192.168.122.252`). Camino interno rápido → canal seguro sin exponer a internet.
- **Dirección del flujo:** WHMCS → motor (el módulo llama a la API del motor). El motor hoy
  escucha cerrado en `127.0.0.1:8224` de noc-monitor.

## API — disponible ✅

- **Ruta:** Setup → Staff Management → **Manage API Credentials** (pestañas *API Credentials*
  y *API Roles*).
- Se pueden **generar credenciales nuevas** (botón "Generate New API Credential") con **rol acotado**.
- Ya existen varias (MercadoPago, nuevas contrataciones, etc.) → el mecanismo está en uso.
- **Pendiente:** crear una credencial + rol **dedicados al motor** (permisos mínimos:
  actualizar la ficha del servicio y disparar el correo de bienvenida).

## Catálogo de VPS — grupo "VPS 2026"

Tipo **Server/VPS**, módulo actual **Auto Release**, Auto Setup **"al recibir el primer pago"**
(coincide con nuestra política).

| Producto WHMCS | id | Sabor nuestro | Notas |
|---|---|---|---|
| VPS Estandar | **335** | Estándar | 4 GB RAM · 100 GB SSD · 4 vCPU · cPanel · VMware |
| VPS Empresas | **336** | Empresas | |
| VPS Cyber Black | **338** | Cyber Black | |
| VPS Premium | ~337 | — | **No está en la web** → fuera del piloto (no genera órdenes) |

Otros grupos (fuera del piloto):
- **Grupo "VPS"** (todos *Hidden*, `Hosting_Vps_Basico/Estandar/Empresas/Premium/Enterprise`,
  `HostingVPSPersonalizado2025`) = versiones **viejas archivadas**. Ignorar.
- **Grupo "SDC - Servidores Dedicados Cloud"** (SDC Estandar/Pro/Ultra) = línea de servidores
  dedicados, otra automatización futura.
- Grupos de hosting compartido (Gama Estandar, ECommerce, Alta Disponibilidad = "Nuevo Hosting *",
  "Hosting Cloud *") = tipo *Shared Hosting (cPanel)*, no son VPS.

## Cómo se aprovisiona HOY (el dolor a reemplazar)

En VPS Estandar → Module Settings:
- **Module Name:** `Auto Release` (no provisiona nada real — solo "suelta" la orden).
- **Server Group:** `VPS OpenVZ - VMWARE`.
- **Create Action / Suspend / Unsuspend / Terminate Action:** todas = **`Create Support Ticket`**
  → es decir, cada evento **abre un ticket para que un humano lo haga a mano**. Ese es el
  proceso manual actual.
- **Admin ID** (con el que corren los comandos API): `4 | Jose Miguel Gutierrez (pepe)`.
- **Support Dept ID:** `20 | Ingeniería` (aprox).
- **Setup:** "as soon as first payment is received" ✅.
- **Welcome Email:** plantilla `Dedicated/VPS Server Welcome Email` (la que el módulo llenará
  con IP + accesos).
- **Details:** Product Type = Server/VPS · Require Domain ✔ · Apply Tax ✔.

## Qué cambia cuando entre nuestro módulo

En estos 3 productos (335/336/338):
- `Module Name`: `Auto Release` → **`hostingcl_vps`** (nuestro conector).
- `Create/Suspend/Unsuspend/Terminate Action`: dejan de abrir ticket y pasan a **llamar al motor**
  (crear/suspender/reanudar/eliminar).
- El resto (tipo, grupo, welcome email, Auto Setup) ya está bien.

## Pendientes / preguntas abiertas

- **Q3 — instalar el módulo:** hay que poner el archivo PHP en `modules/servers/hostingcl_vps/`
  del servidor `201.148.105.100` (por FTP/SSH). **¿Quién tiene ese acceso — el usuario o Cristian?**
- **Canal seguro** (trabajo nuestro, no depende de WHMCS): exponer el motor solo para
  `201.148.105.100` vía nginx de noc-monitor (`location /vps-api/` → proxy a `127.0.0.1:8224`,
  `allow 201.148.105.100; deny all;` + token dedicado).
- **Credencial API del motor:** crear la dedicada con rol mínimo.
- **Server "VPS OpenVZ - VMWARE":** revisar Setup → Products/Services → Servers (qué apunta).
- **VPS Premium:** decidir si se retira o se deja (no está en la web).
