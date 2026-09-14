# Bitácora — Vps

> Registro cronológico para retomar con contexto. Más reciente arriba.
> Lee primero [README.md](README.md) y [SEGURIDAD.md](SEGURIDAD.md).

---

## 2026-09-14 (domingo) — WHMCS Fase 3, mejoras 1-4 (root pw, no-bloqueante, productos, panel cliente)

Tras validar el ciclo E2E, se abordaron los 5 puntos pendientes 1 por 1:
- **#1 Clave root:** el motor aplica la password de WHMCS a **root** por SSH (helper
  `set_root_password`, chpasswd vía stdin — no queda en el VMX). SSH sigue key-only; esa
  clave sirve para **WHM/consola**, no para SSH. Username de la ficha = **root**. Para
  trackear la VM sin depender del Username, el motor guarda `whmcs_serviceid` (columna nueva)
  y `/accion`+`/editar` resuelven la VM por serviceid. Módulo manda `root_password` +
  `whmcs_serviceid`. **Se probará bien con cPanel** (donde luce el WHM).
- **#2 CreateAccount no-bloqueante:** el módulo ya no espera los 2-11 min (poll corto 25s,
  luego devuelve success — el progreso se ve en el NOC). Botón admin **"Sincronizar datos"**
  (`hostingcl_vps_Sync`) trae la IP por serviceid cuando el VPS termina. Necesario para cPanel
  (~11 min supera el límite de PHP).
- **#3 Productos:** creados PRUEBA VPS Empresas (vps-empresas) y Cyber Black (vps-cyber-black)
  duplicando el Estándar y cambiando el Sabor. Los 3 gratis, ocultos, setup manual.
- **#4 Panel de cliente:** `hostingcl_vps_ClientArea` + `clientarea.tpl` — tarjeta simple en
  el área de cliente (estado + IP + acceso root/WHM), sin tripas internas; "aprovisionando…"
  mientras no hay IP. Usa datos que WHMCS ya tiene (no llama al motor).
- **cPanel = checkbox por producto** ("Instalar cPanel" en Module Settings): OFF clona la
  dorada base, ON clona `dorada-almalinux9.7-cpanel`. En producción los planes reales incluyen
  cPanel (checkbox ON). Para la 2ª prueba (con cPanel): crear un producto extra "PRUEBA VPS
  Estándar cPanel" con el checkbox marcado (no hacen falta 3 más).
- **#5 licencia cPanel — PENDIENTE, se resuelve al ÚLTIMO:** automatizar asignar/liberar la
  licencia compartida (pool) a la IP. Falta que el usuario diga cómo la asigna hoy (¿Manage2
  API con usuario+access hash? ¿portal? ¿addon WHMCS?). La 2ª prueba con cPanel se lanza
  después de esto (para que el WHM nazca licenciado).

## 2026-09-14 (domingo) — 🏆 WHMCS Fase 3 VALIDADO E2E: las 4 operaciones desde el panel

**HITO:** el ciclo de vida COMPLETO se disparó y validó desde WHMCS (producción real),
con el cliente de pruebas alcadio (28875) y el producto oculto gratis "PRUEBA VPS Estándar"
(grupo ZZZ-PRUEBAS, sabor vps-estandar, modo produccion, sin cPanel, setup manual).
Se creó la orden (Active, gratis) y se dispararon los botones de Module Commands:
- **Create** → módulo → canal → motor → **VPS real `vps-hcl-0008-alcadio`** (4vCPU/4096MB/103GB,
  priv 10.100.16.247, púb 38.19.57.102, NAT real, NetBox ambas IPs). WHMCS mostró "Service
  Created Successfully" y el módulo escribió solo en la ficha **Dedicated IP** (la pública) y
  **Username** (el nombre de la VM). ✅
- **Suspend** → IP pública bloqueada (address-list), VM viva, estado suspendido. ✅
- **Unsuspend** → IP desbloqueada, estado activo. ✅
- **Terminate** → papelera 7 días + IP/NAT liberados + **NetBox borrado real** (ambas IPs
  desaparecieron). ✅
Todo visible EN VIVO en el NOC: se le agregó al dashboard (dashboard.py, contenedor
hosting-dashboard) que el job en curso se auto-despliega con sus pasos y que la pestaña "Jobs"
se enciende (spinner + "N en curso") desde cualquier tab — así aparecen también las creaciones
disparadas por WHMCS. **Detalle conocido:** CreateAccount hace polling bloqueante; con este
create rápido (~2 min sin cPanel) PHP aguantó y devolvió OK, pero con cPanel (~11 min)
superaría el límite de PHP → pendiente pasar a "no bloqueante + sincronizar" antes de la fase
cPanel. **Próximas features pedidas por el usuario:** (1) Username=root + password de WHMCS
inyectada como clave de root vía cloud-init en el primer boot (SSH sigue key-only; sirve para
WHM sin passwd manual); (2) panel de estado en área de cliente WHMCS (solo estado simple);
(3) crear los productos PRUEBA Empresas y Cyber Black (calcar, cambiando sabor).

## 2026-09-14 (domingo) — WHMCS Fase 3: canal seguro OK + módulo hostingcl_vps escrito

**Etapa 1 — CANAL SEGURO funcionando (validado E2E):** el WHMCS (201.148.105.100) llama al
motor por `https://noc.hosting.cl/vps-api/` → nginx de noc-monitor proxya a `127.0.0.1:8224`.
Dos cerrojos: **allowlist nginx** (201.148.105.100 + 10.100.36.241) + **X-Auth-Token**. El
motor sigue cerrado al mundo. Diagnóstico de red que costó: noc-monitor es interno puro
(192.168.122.252 + 10.255.0.253, sin IP pública); el WHMCS resolvía noc.hosting.cl a la
interna pero el **firewalld de noc-monitor rechazaba el 443** desde la pública del WHMCS
(solo abría 443 a redes internas). Fix: regla rich runtime+permanente para 201.148.105.100
(SIN `--reload` para no romper puertos de contenedores). El WHMCS sale con su **pública
201.148.105.100** hacia noc-monitor (confirmado en el log). `curl /vps-api/health` desde el
WHMCS devuelve el JSON de sabores. ✅

**Token dedicado WHMCS:** el motor ahora acepta `WHMCS_TOKEN` además de `ENGINE_TOKEN`
(auth() en app.py) — revocable aparte del dashboard. Generado e instalado en engine.env,
verificado (200 con token, 401 sin). El token va en el Access Hash del "Server" de WHMCS.

**Etapa 2 — Módulo `hostingcl_vps` escrito** (`whmcs-modulo/hostingcl_vps/hostingcl_vps.php`,
versionado en el repo; se sube a `<whmcs>/modules/servers/`). Funciones: MetaData,
ConfigOptions (Sabor/Marca/Modo/cPanel), CreateAccount→/crear, Suspend/Unsuspend→/accion,
Terminate→/accion eliminar (con confirmación), ChangePackage→/editar, TestConnection→/health.
Guarda el nombre de la VM en tblhosting.username y la IP pública en dedicatedip. CreateAccount
hace polling bloqueante del job (~2 min sin cPanel) — suficiente para el harness manual;
producción a escala se pasaría a pending+cron. **Cliente de pruebas:** alcadio almarza (WHMCS
id 28875). **Pendiente:** subir el módulo, crear el "Server" WHMCS con el token, crear los 3
productos de prueba (grupo oculto, gratis, modo=produccion, sin cPanel) y disparar la secuencia.

## 2026-09-14 (domingo) — Ajuste de disco (+3 GiB) para que el guest muestre el tamaño neto

Fabián/usuario notaron que las VMs muestran "menos" RAM y disco de lo asignado. Se investigó
con datos reales (prueba10 = Cyber Black 8192 MB / 150 GiB, y ddos-monitor viejo):
- **RAM:** asignado 8192 MB → guest ve 7648-7696 MB (`free -g` muestra 7). Reserva del
  hipervisor ~500 MB, **overhead normal de virtualización, NO un tema de MB vs GB** (el VMX
  ya usa `memSize` en MB; vSphere solo lo *muestra* como "8 GB" por ser múltiplo redondo).
  **Decisión: la RAM se deja igual** (valores nominales 4096/6144/8192) — es el estándar de la
  industria y sobreasignar cuesta RAM real (solo ~59 GiB libres en el host).
- **Disco:** de 150 GiB asignados, `/` mostraba 147 GiB. Desglose exacto (lsblk): /boot 1 GiB
  + swap 2 GiB + / 147 GiB = 150. La pérdida es **exactamente 3 GiB fijos** (boot+swap),
  confirmado también en prueba9 (100→97). La dorada nueva ya es lean (swap 2 GiB, partición
  única con growpart) vs. las VMs viejas con LVM+swap 4 GiB.
  **Decisión del usuario: "al disco dale más".** Se sumaron **+3 GiB** a cada sabor para que
  `/` muestre el tamaño anunciado: **Estándar 100→103**, **Empresas 150→153**, **Cyber Black
  150→153**. Como el disco es **thin**, el extra NO ocupa espacio real hasta usarse (costo ~0).
  Documentado en cada sabor con el campo `_nota_disco`. Motor reconstruido y verificado.

## 2026-09-14 (domingo) — Acceso al WHMCS real: exploración y mapeo de productos (Fase 3 arranca)

El usuario consiguió acceso admin al **WHMCS de hosting.cl** (`panel.hosting.cl/admin`,
servidor `201.148.105.100`, mismo datacenter, noc-monitor lo alcanza a 0.35 ms vía
192.168.122.1). Exploración guiada, todo READ-ONLY (WHMCS en producción real: 1149 órdenes
pendientes). Hallazgos completos en **docs/whmcs-hallazgos.md**. Resumen:
- **API disponible** (Setup → Staff Management → Manage API Credentials); se pueden generar
  credenciales+roles. Falta crear una dedicada al motor.
- **Catálogo VPS = grupo "VPS 2026"**, tipo Server/VPS, módulo actual **Auto Release**,
  Auto Setup "al recibir primer pago" (coincide con nuestra política). **Mapeo id→sabor:**
  **VPS Estandar=335 → Estándar**, **VPS Empresas=336 → Empresas**, **VPS Cyber Black=338 →
  Cyber Black**. VPS Premium (~337) NO está en la web → fuera del piloto. Descripción de
  Estandar calza exacto con el sabor (4GB/100GB/4vCPU/cPanel/VMware).
- **Proceso manual actual confirmado:** Create/Suspend/Unsuspend/Terminate Action =
  `Create Support Ticket` (cada evento abre un ticket para hacerlo a mano). Server Group
  `VPS OpenVZ - VMWARE`. Welcome Email `Dedicated/VPS Server Welcome Email`. Admin ID de la
  API: `4 | Jose Miguel Gutierrez (pepe)`.
- **Al entrar nuestro módulo:** en 335/336/338 se cambia Module Name Auto Release →
  `hostingcl_vps`, y las Actions dejan de abrir ticket y llaman al motor.
- **Pendientes:** (Q3) quién tiene acceso FTP/SSH a `modules/servers/` del WHMCS; construir
  el canal seguro (nginx /vps-api/ → allowlist 201.148.105.100 + token); crear credencial API
  del motor; revisar el Server "VPS OpenVZ - VMWARE"; decidir Premium.

## 2026-09-14 (domingo) — CBT activado en cada VPS (backups incrementales, pedido de Fabián)

Fabián pidió (KB Broadcom 320557) que las VMs nazcan con **CBT (Changed Block Tracking)**
activo para permitir backups incrementales. Implementado en la plantilla VMX del motor
(`VMX_TEMPLATE` en engine/app.py): se agregaron **`ctkEnabled = "TRUE"`** (punto 3, CBT a
nivel de toda la VM) y **`scsi0:0.ctkEnabled = "TRUE"`** (punto 4, CBT en el disco). Así
cada VPS nace listo para respaldo incremental sin tocar nada después (se activa limpio
porque la VM nace sin snapshots). Nota en el paso del .vmx: "VM con CBT activo — lista para
backups incrementales". Si un VPS tuviera varios discos, habría que sumar una línea
`scsiX:Y.ctkEnabled` por disco (hoy son de 1 disco → basta scsi0:0). Motor reconstruido y
reiniciado en noc-monitor. En el flujo, la perilla 💾 backups pasó de "Off hoy" a "Preparado"
(VM lista; falta definir software/retención/addon comercial con Fabián). Fabián de acuerdo.

## 2026-09-14 (domingo) — Página "Flujo final esperado" + decisiones de diseño WHMCS

Se creó la página **Flujo final esperado** (`docs/flujo-final.html`, servida en
`/vps-flujo`, link arriba a la derecha del tablero): diagrama de carriles del happy path,
mapa 1:1 de eventos, matriz de "quién puede hacer qué", opcionales (perillas a decidir con
el equipo) y 12 casos borde. Decisión ganadora confirmada por el usuario: **el cliente solo
dispara la creación al pagar; no puede apagar ni eliminar** (lo destructivo pasa por el
equipo con confirmación Telegram + papelera 7 días). La opción de que el cliente elimine
solo existe pero queda OFF (anotada por si el equipo la quiere activar).

**DETALLE — licencia cPanel es SHARED (pool):** el diagrama del flujo se corrigió al orden
real del motor (clona la dorada con cPanel ya preinstalado → enciende → SSH por privada →
IP pública+NAT → recién ahí licencia → securiza). La licencia cPanel es **por IP pública**
(inherente), por eso va después del NAT y se difiere con él si no hay pública. **NUEVO dato
del usuario:** usan **licencia compartida (shared/pool)**, no standalone: al crear se asigna
una licencia del pool a la IP pública; **al eliminar hay que LIBERARLA** y devolverla al pool
(hoy el borrado libera IP/NAT/NetBox pero NO la licencia → PENDIENTE agregarlo). Pregunta
abierta: cómo se asigna/libera (¿manage2 API de cPanel u otro mecanismo?) para automatizarlo.

**ACLARACIÓN — multi-marca:** hosting.cl tiene **un WHMCS por marca** (no uno multimarca).
El **piloto es solo hosting.cl** (un WHMCS, una marca). Cada instancia de WHMCS se configura
fija con su marca y el motor la recibe por parámetro (ya soportado, sin cambios). A futuro,
cada marca nueva (Planeta Hosting, etc.) = otro WHMCS que se conecta al mismo motor con su
**propio token + allowlist** (se enlaza con la seguridad del canal, pendiente).

**DECISIÓN DE DISEÑO — "pagos no instantáneos":** confirmada por el usuario. El motor
**nunca crea antes de confirmar el pago**. Instantáneo (tarjeta/webpay) → crea al toque;
transferencia/depósito → espera a que WHMCS marque el pago recibido o un admin apruebe la
orden. Nunca "crear al hacer el pedido" (antes de pagar). En WHMCS = Auto-Setup del producto
"al recibir el primer pago" (o "al aceptar la orden" para revisión manual) — ya está en el
checklist para Cristian (punto 3).

**DECISIÓN DE DISEÑO — "sin IP pública libre":** si el rango público se agota, el motor
**NO revierte** lo construido. Crea todo **menos el NAT** y deja el VPS en estado
`pendiente-ip-pública` (VM viva por su privada, securización lista). Se difieren junto al
NAT: **licencia cPanel** (se activa por IP pública), **correo de bienvenida** y estado
**Activo** en WHMCS (no avisar "listo" si el cliente no puede conectarse). Avisa por
Telegram; se resuelve la IP a mano y un botón **"Completar NAT"** retoma solo la cola
faltante (asigna pública+NAT → licencia → verifica WHM → NetBox pública → activa → correo).
Razón del usuario: el NAT vive en el MikroTik, es externo a VMware y resoluble aparte.
Malla preventiva: alertar cuando queden pocas públicas libres. Esto generaliza la política
de fallas: **rollback por paso, no global** (revertir lo que no deja VPS usable; diferir lo
externo/resoluble como el NAT). Pendiente implementar: estado `pendiente-ip-pública`,
acción "Completar NAT" y alerta de capacidad.

## 2026-09-14 (domingo) — NetBox validado E2E en producción (alta y baja) + host de pruebas

**NetBox VALIDADO en producción real (vps-hcl-0003-prueba9):** el usuario corrió el flujo
completo. **Creación en 1 min 57 s** (13:28:02 → 13:29:59), modo producción + BYO (llave
pública del cliente, sin custodia), sin cPanel. En el paso del NAT el motor registró solo
las dos IPs en NETBOX2: privada `10.100.16.247/24` y pública `38.19.57.102/32` (status
active, dns_name=prueba9.cl, descripción con marca+nombre+NAT). Confirmado por API. Al
**eliminar**, ambas **desaparecieron del IPAM** (borrado real, verificado por API). Ciclo
alta/baja de NetBox cerrado. También se verificó que el disco quedó bien: VMDK de 100 GB
(107374182400 B, thin) y dentro del guest `/dev/sda3` a 97 G (growpart OK) — la diferencia
100→97 es normal (GiB + /boot + overhead XFS). Marcado hecho en el tablero.

## 2026-09-14 (domingo) — Pendiente: host VMware de pruebas (pedir a Fabián)

El usuario probará el flujo para validar en vivo lo de NetBox (avisará para actualizar el
avance). Además pidió dejar anotado en el tablero **lo que debe solicitar a Fabián**: un
**host VMware dedicado a pruebas**, para no seguir probando sobre `10.100.37.245` (que es
el ESXi de PRODUCCIÓN). Se agregó al tablero un bloque tipo checklist (igual estilo que el
de Cristian/WHMCS) con 6 ítems: (1) host ESXi 7.0.3 dedicado, (2) SSH + usuario acotado
para el motor, (3) datastore con espacio, (4) red/portgroup de pruebas aislada, (5)
recursos para 2-3 VPS a la vez, (6) si lo gobierna un vCenter, la cuenta de servicio (que
además resuelve el pendiente de huérfanos). Con el host listo se le aplica el mismo
checklist técnico de puesta en marcha ya documentado. Sirve además para validar el flujo
multi-host de Fase 3. Ítem también agregado a las tareas de Fase 2.

---

## 2026-09-11 (viernes, tarde-4) — Integración con NetBox (NETBOX2 :8090): registro automático de IPs

**Pedido del usuario:** "si registro un vps se registre alli tambien" — y explícito:
**"para esto usaremos el netbox nuevo nada que ver el viejo"** (NETBOX2 :8090, el vigente;
el viejo :8080 NO se toca).

**Qué quedó:** el motor registra en NetBox ambas IPs de cada VPS al crearlo y las limpia
(borrado real) al eliminarlo. Best-effort: si NetBox falla, la creación/borrado NO se
bloquea (avisa "registrar a mano").
- **Al crear** (tras el NAT): registra la privada `10.100.16.x/24` y la pública
  `38.19.57.x/32` como `status=active`, con dns_name=fqdn y descripción completa
  (marca, nombre VPS, el NAT 1:1, "alta vps-engine"). Guarda `nb_priv_id`/`nb_pub_id` en
  el registro SQLite (2 columnas nuevas). El paso lo muestra: "NetBox: ambas IPs registradas".
- **Al eliminar:** borra ambos registros con DELETE. Si el token no tuviera permiso de
  borrado, hace fallback a marcar `deprecated` (implementado por si acaso, pero ya no hace
  falta: el token nuevo sí borra).

**Funciones nuevas en engine/app.py:** `netbox_req` (GET/POST/PATCH/DELETE con urllib),
`netbox_ip_add` (idempotente: si la IP existe, la actualiza a active), `netbox_ip_del`
(DELETE con fallback a deprecated). Config: `NETBOX_API_URL`/`NETBOX_API_TOKEN` en engine.env.

**Detalle técnico resuelto (tokens v2 de NetBox 4.6):** el token del servicio daba
"Invalid v1 token". NETBOX2 usa el esquema de tokens **v2** (peppered/HMAC): el valor que
espera la API es `nbt_` + `key`(12) + `.` + `plaintext`(40) = 57 chars (igual que el token
del dashboard). El `key` y el `plaintext` son independientes; el `plaintext` solo se ve al
crear el token. Se creó un **usuario/token dedicado `vps-engine`** con ObjectPermission
view/add/change/**delete** sobre `ipam.ipaddress`, y se armó el string v2 completo. La
provisión del token quedó en un script (scratchpad, fuera del repo).

**Validado E2E:** crear IP de prueba → borrado real → `registros que quedan: 0`. IPAM limpio.

**Pendiente para el go-live real:** que una creación real de VPS aparezca sola en NETBOX2
(lo probará el usuario). Antártida: idealmente asociar cada IP a su prefijo/VLAN y device
en NetBox (hoy se registra la IP suelta con descripción; suficiente para IPAM).

---

## 2026-09-11 (viernes, tarde-3) — Creación cPanel 11 min validada + modo BYO + borrón y cuenta nueva

**Borrón y cuenta nueva (pedido del usuario):** limpieza TOTAL del entorno de pruebas —
papelera purgada (9 VMs), bóveda vaciada (3 llaves+Sends de prueba), registro y jobs en
cero, NAT verificado sin restos, públicas .100-.102 libres. Numeración reiniciada.
Doradas y plataforma intactas.

**Creación con cPanel — VALIDADA (vps-hcl-0001-prueba7):** 15/15 pasos en **11 min**.
Detalle estrella: "cPanel 138.0 PREINSTALADO — licencia solicitada con su IP; WHM
responde (200)". Fixes que lo hicieron posible: espera de SSH hasta 4 min (el primer
boot con cPanel tarda 2-3 min — el intento anterior 0011 falló por timeout de 60s) y
activación de licencia + verificación de WHM tras el NAT (cuando ya hay internet).

**Modo BYO implementado y VALIDADO:** el formulario acepta la llave PÚBLICA del cliente
(campo opcional). Con llave → se instala, no se genera ni custodia nada (privada solo del
cliente), fingerprint al registro (col. byo_pubkey_fp), panel de acceso adaptado, sin
Send/bóveda. Vacío → flujo gestionado de siempre. Validación de formato + ssh-keygen.
El usuario probó E2E: pegó su pública (de un .ppk PuTTY) y entró con PuTTY. Nota
aprendida: el ssh de Windows no lee .ppk (convertir con PuTTYgen o usar PuTTY).

**UX del dashboard:** botón "Limpiar" en Crear (resetea formulario y panel sin F5).

**Relojes (pedido del usuario — cambio de hora en Chile):** ddos-monitor y containers2
estaban en America/New_York (coincidía con Chile en invierno, el DST los delató) →
corregidos a America/Santiago. ESXi tenía NTP DESHABILITADO y 5 min de atraso →
habilitado con ntp.shoa.cl + pool.ntp.org (sincronizó al tiro). noc-monitor, rsyslogmk,
containers01 y los 3 MikroTik (RouterData + CCRs) estaban correctos.

**Contraseña de WHM — decisión de entrega:** los VPS nacen con root sin contraseña y WHM
la necesita. Decisión del usuario: **la entrega INSTRUYE al cliente** a definirla él mismo
(`passwd root` por SSH → entrar a WHM con root+esa clave). No custodiamos contraseñas; la
clave NO habilita SSH (PasswordAuthentication no). Implementado YA como "paso 3" en el
panel de acceso del dashboard; el mismo texto irá en el correo/área de cliente de WHMCS.
Documentado en docs/entrega-credenciales.md.

**Estado Fase 2: ~92%.** Validado por el usuario: crear (sin y con cPanel), editar v2
(upgrade caliente/downgrade), suspender/reanudar, eliminar, BYO. Queda: ④ notificaciones,
conectar motor al vCenter (cuenta pendiente), integración WHMCS+Telegram.

---

## 2026-09-11 (viernes, tarde-2) — Flujo entero validado por el usuario + catálogo de doradas completo + Editar v2

**El usuario probó el flujo COMPLETO solo, desde el dashboard (vps-hcl-0010-prueba5):**
crear (nació con disco 97G correcto — dorada v4 certificada) → login por pública →
**editar** Estándar→Empresas (RAM caliente + disco a 147G) → suspender → reanudar →
eliminar. **5/5 en verde, MikroTik limpio.** Bugs cazados en el camino y corregidos:
vmkfstools bloqueado con VM encendida (→ hot-extend por API), el vCenter bloquea el
hot-extend vía host (→ fallback apagar-crecer-encender), grow-disk no idempotente (→ fix),
faltaba cloud-utils-growpart en la dorada (→ v4).

**Catálogo de doradas COMPLETO (decisión: 2 por versión de SO):**
- `dorada-almalinux9.7` v4 (2.9G) — base con growpart.
- `dorada-almalinux9.7-cpanel` (7.1G) — **cPanel última versión preinstalado**, fabricada
  hoy: clon de la base +40G → instalación desatendida (~35 min) → sellado especial
  (limpia mainip/licencia; firstboot `cpanel-firstboot.service` corre mainipcheck +
  build_cpnat + cpkeyclt en cada clon) → sellado SO → des-registrada.
- Licenciamiento por IP documentado (trial en pruebas; pool de licencias en producción).
- Política de mantenimiento: refresh ~mensual (~1 h desatendida); clones se auto-actualizan (upcp).
- Motor: si el plan lleva cPanel clona la `-cpanel` (paso dice "PREINSTALADO"); fallback
  post-instalación solo si no existe. Formulario: **selector de plantilla** (sin/con cPanel)
  — el tilde viejo se quitó; nota de que al final la creación la disparará WHMCS y el
  dashboard quedará solo de gestión.

**Editar v2 (decisión del usuario: permitir downgrade sacrificando disco):**
- UPGRADE: CPU/RAM hot-add + disco crece — sin corte.
- DOWNGRADE: CPU/RAM bajan con reinicio breve (~1-2 min; no hay hot-remove).
- El disco NUNCA se achica (corrompería el FS) — se mantiene y se explica.
- Dashboard: panel de cambio de plan con selector y advertencias automáticas
  (verde=caliente / amarillo=reinicio / gris=disco se mantiene). Reemplaza al prompt().

**UX del dashboard:** botón copiar-nombre en la tabla, auto-refresco 30s de lista/jobs,
fix de legibilidad del tema oscuro (overrides body.dark-theme para #page-vps_engine).

**Pendiente:** el usuario prueba la creación "Con cPanel" (~7 min, WHM en :2087) y la
demo del downgrade. Luego: ④ notificaciones, vCenter, WHMCS.

---

## 2026-09-11 (viernes, tarde) — Suspender/reanudar por address-list (capa 1) + decisión WHMCS

**Diseño conversado con el usuario.** Su práctica real de suspensión por no-pago NO es
apagar la VM: es **habilitar la entrada de la IP pública en la address-list** (hay un drop
`dst-address-list` en firewall raw de RouterData → habilitada = bloqueada). Confirmado el
mecanismo en el MikroTik.

**Capa 1 CONSTRUIDA y desplegada** (`set_bloqueo_publica` + flujos reescritos):
- Suspender = habilitar entrada (IP cae al drop) — VM sigue corriendo, NAT/IP se conservan.
- Reanudar = deshabilitar entrada — instantáneo, sin boot.
- Guardas de estado (solo activo→suspendido y viceversa), verificación del estado real de
  la entrada tras el toggle, comentario del cliente se conserva.
- Pasos del dashboard actualizados ("Bloquear IP pública (address-list)" etc.).
- **PENDIENTE: probar E2E** (no hay VPS activo ahora; probar con la próxima creación).

**Capas 2-3 (aviso + confirmación Telegram): decisión = esperar WHMCS** (no construir
lector de correos interino). WHMCS avisa moroso/pagado → Telegram pide confirmación con
botones → con el OK se aplica el toggle. Bots de Telegram ya existen en noc-monitor
(reutilizables). Diseño completo en [docs/suspension.md](docs/suspension.md). Sin acceso
a WHMCS todavía.

---

## 2026-09-11 (viernes) — PRODUCCIÓN E2E: creación + IP pública/NAT + securización + login validados

**Sesión grande: el flujo comercial completo quedó funcionando en la red real.**

**Deploy y validación del ciclo básico (mañana):**
- Prueba manejada por el usuario desde el dashboard (crear + borrar varias veces). Se validó
  crear (0004, 0005) y eliminar a papelera. Tiempos: ~6 min sin carga, ~9 con VMs activas
  (cuello de botella = clonar disco). Se limpiaron las de ayer.
- **Hallazgo mayor: el host 10.100.37.245 NO es standalone — lo administra un vCenter**
  (192.168.200.107, VCENTER200107.DEDICADOS.CL, 10 hosts/54 VMs). El motor opera a nivel de
  host, así que cada borrado deja una entrada HUÉRFANA en el vCenter → se quita a mano con
  "Quitar del inventario" (NUNCA "Eliminar del disco", sobre todo la dorada). Se reinició
  hostd para limpiar fantasmas. Pendiente: apuntar el motor al vCenter (necesita cuenta svc).

**Arquitectura: dónde vive el sistema (pregunta del usuario):**
- El motor `vps-engine` es un **contenedor propio y separado** del dashboard (que solo lo
  invoca por API). Verificado que noc-monitor alcanza todo lo necesario (RouterData, red de
  VPS producción 10.100.16.x, ESXi). Mapa completo en [docs/despliegue.md](docs/despliegue.md).
  Decisión: es movible; noc-monitor es buen hogar por ahora.

**Red de producción (cable + VLAN del usuario):**
- El host se cableó directo al switch del rack1. Portgroup **`Vps_Hosting.cl`** (vSwitch4,
  VLAN 81) para la red **10.100.16.0/24** (producción). Pública de pruebas: **38.19.57.0/24**
  = address-list **Red57-0** del MikroTik (rango de prueba .100-.102).

**MODO PRODUCCIÓN construido y desplegado:**
- Motor: IP privada libre por RouterData (NAT+ARP, `mikrotik()` con llave `/keys/mikrotik_key`,
  claude@2420), IP pública libre de la address-list (5 validaciones como el NOC), **NAT 1:1
  real** (srcnat+dstnat), y **securización**: genera la llave del cliente, la instala en la
  VM, y vps-provision la guarda en la bóveda + crea Bitwarden Send (endpoint nuevo
  `/vault-guardar-enviar`). El entorno (pruebas/producción) se elige POR creación (selector
  en el dashboard + param `modo`). Diseño en [docs/flujo-produccion.md](docs/flujo-produccion.md).
- **Prueba E2E de producción (vps-hcl-0006-prueba2):** los 15 pasos OK — privada 10.100.16.247,
  **pública 38.19.57.102 con NAT real**, securización con Send. **El usuario descargó la llave
  del Send e inició sesión por SSH a la IP pública desde su PC** → `[root@prueba2 ~]#`.
  **FLUJO COMERCIAL VALIDADO DE PUNTA A PUNTA.**

**Panel de acceso en el dashboard (para demos):** al terminar una creación de producción,
el dashboard muestra "Acceso al VPS — cómo iniciar sesión" con la IP pública, el link de
descarga de la llave y el **comando SSH copiable** (+ variante Windows). El motor guarda un
`resultado` estructurado en el job para poblarlo.

**Bugs cazados y corregidos (gracias a las pruebas del usuario):**
1. **borrar_nat no revertía el NAT:** los valores del `find` de RouterOS deben ir
   ENTRECOMILLADOS (`src-address="X"`); sin comillas no matchea y el remove es un no-op
   silencioso. Corregido (igual que /api/alta/baja del NOC) + verificación post-borrado.
   La pública quedaba "ocupada" para siempre; ahora se libera y se reusa (verificado:
   0007 reusó 38.19.57.102).
2. **Formato del NAT:** el comentario debe ser `[NOC] <host> <ip-corta>, VPS <marca>` SOLO
   en el srcnat; el dstnat SIN comentario (convención del NOC). Corregido.
3. **Label "modo pruebas"** en el paso 1 aunque fuera producción (usaba variable global) — corregido.

**Entrega de credenciales — decisiones del usuario:**
- **Diseño final:** WHMCS con opción de que el cliente traiga su propia llave (BYO-key,
  "como los pros" — la privada del cliente nunca toca nuestros sistemas).
- **Interino (opción A+B):** enlaces públicos de nuestro vault vía **subdominio** que sirve
  SOLO los Sends (nunca abrir el vault entero) + entrega del **link por email**. A futuro.
  Plan técnico en [docs/entrega-credenciales.md](docs/entrega-credenciales.md). Por ahora se
  descarga localmente.
- Discusión de seguridad: el área de cliente WHMCS es más seguro que el link por email
  (acceso atado a identidad autenticada vs secreto que viaja). Documentado.

**Política de limpieza de la bóveda (opción B, implementada):** al **borrar** un VPS se
elimina el **Send** (link de entrega); al **purgar** (7 días) se elimina también la **llave**
de la bóveda. Endpoint nuevo en vps-provision `/vault-borrar-item` (protegido: solo sshKey,
nunca la llave de gestión hosting-interno). Se limpiaron los 4 restos de prueba (bóveda vacía).

**Estado:** Fase 2 al 70% — ① securización 100% validada; ciclo crear→borrar en producción
sólido y repetible. Pendiente: ② suspender/reanudar, ③ editar plan, ④ notificaciones,
+ conectar el motor al vCenter.

---

## 2026-09-10 (jueves, tarde-7) — Dorada OK, red estática resuelta, tablero web en la red interna

**Dorada v3 (repos online) — CONSTRUIDA OK:** el ISO minimal NO trae cloud-init/open-vm-tools
(2º intento se detuvo en "missing packages"). Fix: `repo --name=... --baseurl=` online en el
kickstart → anaconda instaló 374 paquetes (incl. open-vm-tools y cloud-init). Sellada y apagada.

**Tablero web del proyecto (pedido del usuario) — en la RED INTERNA:**
- `docs/tablero.html`: fases + avance con diseño (consola NOC oscuro / blueprint claro).
- Servido por nginx de noc-monitor: **https://noc.hosting.cl/vps** (patrón de /informe /guia;
  location = /vps → alias /usr/share/nginx/html/vps-tablero.html). Backup de dashboard.conf.
  Publicado también como artifact de Claude (privado). Se actualiza re-subiendo el HTML.

**2ª y 3ª prueba E2E — cloud-init SÍ personaliza, faltaba la red estática:**
- La dorada nueva SÍ trae cloud-init: el clon aplicó **hostname** (demo1.hosting.cl), usuarios,
  llave y SSH desde guestinfo (`DataSourceVMware [seed=guestinfo]`).
- PERO la **IP estática no se aplicó**: el clon quedó con DHCP (192.168.121.169, no la
  192.168.122.249 asignada). Causa raíz: en ESXi standalone el datasource VMware se detecta en
  la etapa **"network"** (tras subir DHCP), así cloud-init NO re-aplica la red del metadata
  (`extended_status: degraded`; el network-config.json TENÍA la config correcta pero la
  nmconnection quedó en DHCP).
- **Fix probado a mano y luego automatizado:** forzar la estática con **nmcli** vía
  `write_files` + `runcmd` en el userdata (corre en cloud-final con NetworkManager arriba).
  Manual en la VM viva: quedó en 192.168.122.249, SSH OK, **internet OK** (NAT del usuario).
- Motor actualizado (`cloudinit_userdata` ahora recibe ip/pfx/gw/dns y escribe el script nmcli),
  imagen reconstruida y `systemctl restart vps-engine`.

**Red de pruebas:** el segmento "Switch Interno 1 Data ethr6" tiene DHCP que reparte 192.168.121.x;
por eso el clon sin estática caía en 121.x. La /24 objetivo de pruebas es 192.168.122.0/24 (la que
usa el buscador de IP libre). El usuario dio **internet a toda la 192.168.122.0/24** (solo pruebas;
en producción el egress vendrá del NAT con la IP pública).

**En curso:** 3ª prueba E2E (vps-hcl-0003) con cPanel — validando que la estática ahora entra sola.

---

## 2026-09-10 (jueves, tarde-6) — DEPLOY completo + 1ª prueba E2E (falla útil) + fix dorada

**Autorizado por el usuario ("ok haslo"): deploy a producción de motor y dashboard.**

**Motor vps-engine desplegado en noc-monitor:**
- Imagen construida (`podman build`), quadlet `vps-engine.container` (Network=host,
  127.0.0.1:8224), servicio `active`. Fix menor: HealthCmd sin token (/health es
  público). Smoke test: /health OK, sin token→401, sabor malo→400.
- `engine.env` completado con MGMT_PUBKEY/MGMT_PRIVKEY (llave nueva `vps_engine_mgmt`
  ed25519 — el motor no usa gestion_hosting por su passphrase). Endpoint /health
  ahora expone `sabores_detalle` (para poblar los selects del dashboard).

**Sección "VPS" en el dashboard NOC (patrón VpsClientes):**
- dashboard.py: nav en Infra Lógica, página con 3 tabs (Crear con panel de progreso
  en vivo por polling de /job cada 3 s, VPS gestionados con acciones, Jobs), rutas
  `/api/vpseng/*` (proxy con ENGINE_TOKEN, actor = usuario de sesión), permiso
  **`vps_engine`** en _ALL_PAGES/PROTECTED_PAGES/permPages + rol `noc`.
  Backup: `dashboard.py.bak.20260910-141603`. Imagen `hosting-dashboard:v2`
  reconstruida + `systemctl restart dashboard` (HTTP 302 OK).

**1ª prueba E2E (crear vps-hcl-0001-test, cPanel off):** el pipeline MECÁNICO
funcionó completo — IP libre (192.168.122.248), clon de la dorada, grow, vmx (con
pciBridges), register, guestinfo, power on → **AlmaLinux 9.7 arrancó desde nuestra
dorada** (visto en consola). PERO el job falló, VISIBLE, en el paso "Esperar IP por
VMware Tools" (7 min) — justo la visibilidad que pidió el usuario.

**Diagnóstico (SSH al clon con la llave de gestión):** la dorada quedó **SIN
cloud-init ni open-vm-tools** → el clon no leyó su guestinfo (hostname siguió
`dorada`, sin personalizar). Causa: el `dnf` del %post falló porque la VLAN
192.168.122.0/24 tiene **NAT por whitelist de IP de origen** (noc-monitor .252 sí
navega por el mismo gw .122.1, pero una VM nueva no está en la whitelist).

**Fixes aplicados:**
- Kickstart: cloud-init + open-vm-tools movidos a `%packages` (anaconda los instala
  desde el ISO, sin depender de internet) y red a DHCP (sin IP baked). Documentado
  en dorada/README.md.
- **Dorada RECONSTRUIDA** con el fix (en curso al cierre).
- Prueba limpiada (VM a papelera + registro/jobs limpiados).

**Coordinación con el usuario:** va a **whitelistear en el MikroTik el rango
192.168.122.240-254** para dar salida a internet a los VPS de prueba (necesario solo
para el paso cPanel; el resto del flujo es local). El motor asigna las IPs de prueba
de ese rango (de .254 hacia abajo).

**Pendiente inmediato:**
- [ ] Al terminar la dorada nueva: re-test crear (cPanel off) → debe personalizar OK
- [ ] Con el NAT del usuario listo: test con cPanel on
- [ ] Push del repo con todos los fixes

---

## 2026-09-10 (jueves, tarde-5) — DORADA LISTA (construida 100 % desatendida)

**dorada-almalinux9.7 terminada:** anaconda instaló solo con el kickstart OEMDRV
(~22 min incl. dnf de cloud-init/open-vm-tools en %post), reboot, `dorada-seal`
limpió identidad (machine-id, host keys, logs, cloud-init clean) y APAGÓ la VM
sola — la señal de éxito del diseño. VM de construcción des-registrada del
inventario: la dorada queda SOLO como directorio en `_plantillas/`
(10 GB thin, **2.0 GB reales**). Nadie la puede encender por error.

**Decisión documentada (conversada con el usuario):** doradas = **copia local en
cada host** (el clon vmkfstools debe ser local para tardar segundos) con patrón
"se construye una vez, se distribuye" (manifiesto de versión + checksum; copia a
nuevos hosts vía wrapper). ISOs = centralizadas en NFS (se usan solo para
construir doradas). Se activa al sumar el 2º host.

**Estado: TODO listo para el deploy.** Pendiente SOLO del OK del usuario
(producción noc-monitor):
- [ ] Build imagen vps-engine + quadlet + start (127.0.0.1:8224)
- [ ] Sección "VPS" en el dashboard NOC (docs/dashboard-integracion.md)
- [ ] Prueba end-to-end: crear vps-hcl-0001 viendo el progreso en el dashboard

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
