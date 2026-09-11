# Diseño — flujo de producción completo (crear + IP pública + NAT + securización)

> Diseño del próximo hito: crear un VPS en la **red real de producción**, darle **IP
> pública con NAT**, **securizarlo** (llave del cliente a la bóveda y entregársela) y que
> el cliente **inicie sesión** con esa llave. Este documento es el plan a construir; las
> partes que escriben en el MikroTik de producción se marcan con ⚠️.

Fecha de diseño: 2026-09-11.

---

## Datos confirmados del entorno

| Cosa | Valor |
|---|---|
| Red privada de VPS (producción) | `10.100.16.0/24` · VLAN 81 · portgroup `Vps_Hosting.cl` |
| Red pública para pruebas | `38.19.57.0/24` → address-list **`Red57-0`** en el MikroTik |
| MikroTik que decide/rutea (RouterData) | `172.16.1.90` |
| CCR de borde (para ping de verificación) | `172.16.1.69` (GTD) |
| Acceso al MikroTik | SSH `claude@<host>` puerto **2420**, con llave (hoy `fnm_blackhole_key` del dashboard) |
| Bóveda | Vaultwarden en noc-monitor (org "Hosting.cl - Clientes VPS") |
| Securización existente | `vps-provision` (127.0.0.1:8223) — ya guarda llaves y crea Bitwarden Send |

---

## El flujo, paso a paso (lo que hará el motor en modo producción)

### A. Crear la VM en la red real
1. IP privada libre en `10.100.16.0/24` — **método RouterData** (no ping):
   consultar NAT + ARP a `172.16.1.90`, `ocupadas = NAT ∪ ARP-vivo ∪ {.1}`, tomar la más
   alta libre desde `.254`. (Ver [README §Cómo se elige la IP](../README.md#cómo-se-elige-la-ip-del-vps-sin-conflictos).)
2. Clonar la dorada → crecer disco → crear la VM en el portgroup **`Vps_Hosting.cl`**
   → cloud-init con la IP estática de la 10.100.16.x → encender → verificar SSH (todo
   como hoy, solo cambian red y portgroup).

### B. IP pública + NAT 1:1  ⚠️ (escribe en el MikroTik)
3. **Elegir IP pública libre** en `38.19.57.0/24` (address-list `Red57-0`), con las 5
   validaciones del NOC: entrada habilitada, sin comentario, sin NAT previo, sin ARP vivo,
   sin aparecer en otras address-lists, y muda al ping desde el CCR de borde `172.16.1.69`.
4. ⚠️ **Crear el NAT 1:1** en RouterData (`172.16.1.90`):
   - `srcnat`: `/ip firewall nat add chain=srcnat src-address=<privada> action=src-nat to-addresses=<publica> comment="[VPS] <hostname>"`
   - `dstnat`: `/ip firewall nat add chain=dstnat dst-address=<publica> action=dst-nat to-addresses=<privada>`
5. ⚠️ **Marcar la pública como usada**: deshabilitar + comentar su entrada en `Red57-0`.
   (Opcional, como en el NOC: registrar ambas IPs en NetBox.)

### C. Securización + entrega  ⚠️ (genera credenciales reales)
6. **Generar el par de llaves del cliente** (ed25519) — lo hace el motor.
7. **Instalar la pública del cliente** en la VM (`/root/.ssh/authorized_keys`), vía SSH con
   la llave de gestión. La VM conserva además la llave de gestión (para soporte/operación).
   El endurecimiento ya viene de la dorada (root solo con llave, sin contraseñas).
8. **Guardar el par en la bóveda** (Vaultwarden, colección `cliente-<nombre>`) y **crear un
   Bitwarden Send** con la privada → link de entrega (expira 48 h, opcional contraseña).
9. El **operador entrega el link** al cliente; el cliente descarga la llave e **inicia
   sesión**: `ssh -i <llave-descargada> root@<IP-pública>`.

### D. Registrar y notificar
10. Registro + auditoría + notificación en el NOC. El registro guarda también la pública y
    el vínculo con el ítem de la bóveda.

---

## Cómo se construye (componentes)

### 1. Acceso del motor al MikroTik
El motor (contenedor `vps-engine`) necesita poder correr comandos en RouterData igual que el
dashboard. Se le da acceso `claude@172.16.1.90:2420` montando una llave SSH autorizada en el
MikroTik (reusar `fnm_blackhole_key` o una llave dedicada del motor). Funciones nuevas:
`mikrotik(host, cmd)`, `ip_libre_produccion()`, `ip_publica_libre(lista)`, `crear_nat(priv, pub, lista, etiqueta)`.

### 2. Modo producción
`engine.env`: `MODO=produccion`. El motor usa `red_default` de la marca (10.100.16.0/24,
portgroup `Vps_Hosting.cl`) y activa los pasos B (pública+NAT). En pruebas locales
(192.168.122.x) esos pasos se omiten.

### 3. Securización — división de trabajo (reusa lo que ya existe)
La bóveda vive en `vps-provision`, que ya sabe guardar llaves y crear Sends. Pero su flujo
`/provision` se conecta **con contraseña**, y nuestras VMs nacen **solo con llave**. Por eso:

- **El motor** hace la parte de host: genera el par del cliente e instala la pública en la VM
  (SSH con la llave de gestión que ya tiene).
- **vps-provision** hace la parte de bóveda: se le agrega un endpoint pequeño
  `POST /vault-guardar-enviar {cliente, item_name, notes, priv, pub, fingerprint, days}` que
  reutiliza su `bw_ready`/`ensure_collection`/`store_sshkey` + la creación de Send, y devuelve
  `{item_id, url, password}`. Así no duplicamos las credenciales de la bóveda en el motor.

---

## Decisiones que necesito confirmar contigo

1. **Acceso del motor al MikroTik:** ¿reuso la llave `claude@2420` que ya usa el dashboard
   (la monto en el motor), o prefieres una llave dedicada del motor autorizada en RouterData?
2. **NAT real en producción:** para las pruebas voy a crear reglas NAT reales en RouterData
   con la 38.19.57.x. ¿Me confirmas que puedo, y que 38.19.57.0/24 es la red correcta para
   estas pruebas? (Las etiqueto `[VPS]` para distinguirlas y poder limpiarlas.)
3. **El cliente inicia sesión ¿por la IP pública o la privada?** Para que la prueba sea
   realista (como un cliente de verdad, desde afuera) sería por la **pública**. Confírmame
   que la pública 38.19.57.x rutea de entrada hacia la VM tras el `dstnat`.
4. **Limpieza:** al borrar un VPS de producción, ¿el motor debe también **quitar el NAT** y
   **liberar la pública** en la address-list? (Recomiendo que sí — cierre limpio.)

---

## Riesgos y cuidados

- Los pasos B y C **escriben en producción** (routing real + credenciales reales). Van con
  etiquetas `[VPS]` y quedan en la auditoría; el borrado debe revertir el NAT.
- Es el mismo criterio ya probado del NOC ("Alta de Servicios"), así que el riesgo es
  conocido, pero conviene la **primera corrida acompañada** (yo mirando el motor por detrás).
- La entrega de la llave por Bitwarden Send hereda el estándar de VpsClientes (expira, se
  borra al confirmar).
