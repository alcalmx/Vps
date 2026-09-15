# WHMCS — Todo lo que intervenimos (inventario y "dónde está cada cosa")

> Referencia de **todo lo que creamos/configuramos en el WHMCS de hosting.cl** para la
> integración con el motor Vps. Sirve para saber **de dónde sacar la info** si alguien pregunta.
> Complementa [whmcs-hallazgos.md](whmcs-hallazgos.md) (exploración) y el
> [flujo final](/vps-flujo). WHMCS: `panel.hosting.cl/admin` · servidor `201.148.105.100`
> (`/home/panelhosting/public_html`).

---

## Resumen — las 8 piezas que tocamos en WHMCS

| # | Qué | Dónde en WHMCS |
|---|---|---|
| 1 | **Módulo** `hostingcl_vps` (el conector) | Archivos en `modules/servers/hostingcl_vps/` |
| 2 | **Server** "motor-vps" (apunta al motor) | Setup → Products/Services → Servers |
| 3 | **Server Group** "Motor VPS" | Setup → Products/Services → Servers |
| 4 | **Grupo de productos** de prueba (oculto) | Setup → Products/Services |
| 5 | **4 productos** de prueba | Setup → Products/Services |
| 6 | **Cliente** de prueba (alcadio) | Clients → 28875 |
| 7 | **API Credential + Role** (callback motor→WHMCS) | Setup → Staff Management → Manage API Credentials |
| 8 | **API IP Access Restriction** (permitir al motor) | Setup → General Settings → Security |

> Los productos **REALES** (335/336/338) **NO se modificaron** — solo se documentó el cambio
> para el go-live (ver checklist en el tablero).

---

## 1. Módulo `hostingcl_vps` (el conector)

- **Ubicación en el servidor:** `/home/panelhosting/public_html/modules/servers/hostingcl_vps/`
  - `hostingcl_vps.php` — el módulo (funciones del ciclo de vida)
  - `clientarea.tpl` — panel de estado del área de cliente
- **Versionado (copia maestra):** repo `Vps/whmcs-modulo/hostingcl_vps/`
- **Cómo se actualiza:** se sirve por `https://noc.hosting.cl/vps-modulo` (y `/vps-modulo-tpl`) y
  se baja con `curl -o` (el pegado en PuTTY corrompe archivos grandes).
- **Qué hace:** traduce eventos de WHMCS → llamadas a la API del motor
  (crear/suspender/reanudar/eliminar/editar), botón admin "Sincronizar datos", panel de cliente.

## 2. Server "motor-vps"

- **WHMCS:** Setup → Products/Services → **Servers** → server llamado **`motor-vps`**
- **Config:**
  - Hostname: `noc.hosting.cl` · **Secure (SSL): sí**
  - Type/Module: **Motor Vps (hosting.cl)**
  - **Access Hash / Password:** el **token del motor** (empieza con `vpswhmcs_`). El valor real
    vive en `/opt/vps-engine/engine.env` (`WHMCS_TOKEN`) de noc-monitor.
- **Verificar:** botón **"Test Connection"** en la ficha del server (llama a `/health` del motor).

## 3. Server Group "Motor VPS"

- **WHMCS:** Setup → Products/Services → Servers → grupo **`Motor VPS`** (contiene `motor-vps`).
- Los productos apuntan a este **grupo** (no al server directo).

## 4. Grupo de productos de prueba

- **WHMCS:** Setup → Products/Services → grupo **`ZZZ-PRUEBAS Motor VPS (automatización)`**
- **Oculto** (Hidden) → no aparece en la web. Prefijo `ZZZ-` para que quede al fondo.

## 5. Los 4 productos de prueba (en ese grupo)

Todos: tipo **Server/VPS**, módulo **Motor Vps**, Server Group **Motor VPS**, **gratis**, **ocultos**,
setup **manual** ("Do not automatically setup"). En Module Settings: Marca `hosting.cl`, Modo `produccion`.

| Producto | Sabor | Instalar cPanel |
|---|---|---|
| PRUEBA VPS Estándar | vps-estandar | no |
| PRUEBA VPS Empresas | vps-empresas | no |
| PRUEBA VPS Cyber Black | vps-cyber-black | no |
| PRUEBA VPS Estándar cPanel | vps-estandar | **sí** |

## 6. Cliente de prueba

- **Clients → alcadio almarza — #28875** (alcadio@hosting.cl / prueba.cl). Se usa para colgar las
  órdenes de prueba (sin crear clientes nuevos).

## 7. API Credential + Role — callback motor→WHMCS

- **WHMCS:** Setup → Staff Management → **Manage API Credentials**
  - **Role** "Motor VPS callback": permisos **UpdateClientProduct** + **GetClientsProducts**
    (SendEmail pendiente para el correo de bienvenida).
  - **Credential** "Motor VPS - callback": Identifier público + Secret. El **Secret real** vive en
    `/opt/vps-engine/engine.env` (`WHMCS_API_SECRET`), no se guarda en docs.
- **Para qué:** que el **motor entre a WHMCS** (dirección motor→WHMCS) y rellene la IP en la ficha
  al crear (y a futuro dispare el correo). Endpoint: `https://panel.hosting.cl/includes/api.php`.
- **Nota:** esta es la ÚNICA API credential que creamos. El **módulo NO usa API credential** (WHMCS
  lo llama internamente y él llama al motor con el token del server).

## 8. API IP Access Restriction

- **WHMCS:** Setup → General Settings → pestaña **Security** → **API IP Access Restriction**
- **Agregado:** `192.168.122.252` (etiqueta "Noc.monitor Alcadio") — es la IP con la que el motor
  sale hacia la API de WHMCS. Sin esto, la API responde `403 Invalid IP`.
- ⚠️ **Conservar las demás IPs** de la lista (las usa la web para crear órdenes, etc.).

---

## Contraparte en el motor (noc-monitor) — el otro lado del cable

- **Canal seguro WHMCS→motor:** nginx `location /vps-api/` (allowlist a la IP del WHMCS) +
  firewalld (443 abierto para `201.148.105.100`) + token `WHMCS_TOKEN`.
- **Cliente motor→WHMCS:** en `/opt/vps-engine/engine.env`:
  - `WHMCS_TOKEN` (el que va en el Access Hash del server motor-vps)
  - `WHMCS_API_URL` = `https://panel.hosting.cl/includes/api.php`
  - `WHMCS_API_IDENTIFIER` + `WHMCS_API_SECRET` (la credential del punto 7)

## Cómo se conecta con la web (contexto)

- La web hosting.cl usa un **checkout propio** (`www.hosting.cl/contratar?pid=335`) que crea las
  órdenes por la **API de WHMCS** (credencial "nuevas contrataciones", NO es nuestra). El **pid**
  amarra web ↔ producto WHMCS (335/336/338) ↔ sabor. Nuestro módulo se dispara al activarse el
  servicio tras el pago — sin importar cómo se creó la orden.

## Para el go-live (productos reales 335/336/338)

Hoy usan módulo **Auto Release** (manual). Pasarlos a la automatización = cambiar **solo la pestaña
Module Settings** (módulo → Motor Vps, grupo Motor VPS, sabor, Instalar cPanel). Prerrequisitos y
orden seguro: sección **"Go-live"** del tablero.
