# Bitácora — Vps

> Registro cronológico para retomar con contexto. Más reciente arriba.
> Lee primero [README.md](README.md) y [SEGURIDAD.md](SEGURIDAD.md).

---

## 2026-09-21 (lunes) — 🏗️ MULTI-HOST Fase A2: flujos host-aware + selección del USUARIO + CRUD

La fase grande del multi-host, **sin Codex (en la deuda)**, 10 escenarios nuevos + 11 suites de
regresión verdes. DECISIÓN DE DISEÑO DE ALCADIO: **la selección de host la controla él, nunca
el motor por capacidad** (eso se automatizará cuando él quiera):
- **Selección en /crear**: (a) admin/dashboard puede FORZAR host con el param `host` (403 para
  whmcs, 400 desconocido, 409 pausado); (b) sin param → el host ACTIVO de mejor PRIORIDAD (campo
  que administra el usuario). Si el elegido no tiene cupo/espacio → FALLA con motivo (sin failover).
- **Flujos host-aware**: crear/eliminar/editar/purga/reconciliación/vms resuelven el host de cada
  VM (vms.host) y le hablan con SUS credenciales/datastore/llave: govc(host=), esxi_ssh(host=),
  listar_wrapper(host=), power_state/apagar_graceful(host=), datastore_libre_gb(host). Probado:
  un eliminar de VM en esxi-121 opera SOLO esxi-121 en todas las llamadas.
- **Purga y reconciliación POR HOST**: barren cada host del registro (pausados incluidos — sus
  VMs se siguen gestionando); un host CAÍDO → alerta + sus filas se saltan ese día (los demás
  purgan igual; SEMÁNTICA NUEVA: ya no aborta el job completo). La reconciliación de huérfanas
  exige listado fresco DEL host de la fila (evidencia real por host).
- **Cupo PER-HOST**: límites max_* propios (NULL hereda HOST_MAX_* globales), atómico con la
  reserva; mensajes con el nombre del host.
- **CRUD /hosts** (solo admin): POST enrola CON VALIDACIÓN EN VIVO (govc about + wrapper pong +
  datastore legible — exige host ya preparado: svc-vps/llave/wrapper/huella, Fase C); PATCH
  pausar/activar/prioridad/límites/notas (pausado = sin creaciones nuevas, gestión normal);
  DELETE solo sin VMs (papelera incluida).
- Compat single-host TOTAL: con solo esxi-245 activo todo se comporta idéntico a ayer.
- PENDIENTE DEPLOY. Quedan: **Fase B** (pestaña Motor en dashboard) y **Fase C** (enrolar-host
  para el 192.168.200.121).

## 2026-09-21 (lunes) — ✅ PRIMERA PURGA REAL VALIDADA: falla transitoria + convergencia automática

El ciclo de purga definitiva se estrenó el fin de semana EXACTAMENTE como fue diseñado:
- **Sáb 19 04:30**: 2 entradas del 11-09 elegibles → 1 llave de bóveda borrada, pero las 2
  entradas SALTADAS (falla transitoria a esa hora — sospecha: ventana de backups nocturnos del
  ESXi; el diseño conservador conservó todo para el reintento).
- **Dom 20 04:30**: reintento diario → **las 2 purgadas** (una sin re-tocar la bóveda: su
  vault_item ya estaba NULL — la idempotencia del #13 operando). + **2 secretos de entrega
  expirados** (TTL del #14, los Send del 0013/0014).
- **Lun 21**: 0/0/0 limpio. Papelera actual: lote del 14-09 (purga mañana). Timer impecable
  los 4 días (purga + reconciliación ok cada madrugada).
**Conclusión: purga + saltadas + convergencia + expiración de secretos = validado en producción
sin intervención humana.** Observación menor: si las saltadas de las 04:30 se repiten seguido,
considerar mover el timer a 05:30 (fuera de la ventana de backups).

## 2026-09-17 (miércoles, CIERRE) — 📌 ESTADO Y PENDIENTES para retomar

**Estado del motor: endurecido, completo y en producción.** Esta semana (15→17):
auditoría Codex **24/24 respondida** (22 fixes + #22-resto cerrado por decisión + #24
postergado), auto-reinicio del primer boot, timer de mantención diaria (04:30, purga +
reconciliación), **VPS Personalizado** (motor + dashboard v2, validado con prueba real de
Alcadio: 0016 con 2vCPU/4GB/40GB verificados en el ESXi), y **Multi-host Fase A1** desplegada
(tabla hosts, bootstrap esxi-245, 16 VMs adoptadas, GET /hosts con scan vivo).

**PENDIENTES (orden sugerido para retomar):**
1. **Multi-host Fase A2** (próxima sesión grande): flujos host-aware (govc/esxi_ssh por el host
   de cada VM — hoy govc(host=) existe pero los flujos usan el principal), selección automática
   al crear, CRUD /hosts. Luego **B** (pestaña Motor en dashboard) y **C** (enrolar-host para
   el 192.168.200.121). Diseño completo en la entrada "diseño multi-host" del 17-09.
2. **Sábado 19-09 04:30**: primera purga con borrado DEFINITIVO real (entradas del 11-09) —
   revisar el job en el dashboard (debería purgar prueba7/prueba8 + llaves de bóveda).
3. **~15-oct: vuelve la cuota de Codex** → reactivar gate (/codex:setup --enable-review-gate)
   y pasar la DEUDA (7 ítems en docs/pendiente-revision-codex.md).
4. **Negocio**: explicación de Gerardo (Bloque 3 post-cPanel), correo de bienvenida (SendEmail),
   licencia cPanel #5 (¿Manage2?), checklist go-live 335/336/338.
5. **Conversar**: llave backup@esxi sin restricciones (Fabián) · #24 gunicorn/jobs durables
   (sesión de arquitectura) · si el host tendrá más RAM para clientes (límites HOST_MAX_*).

## 2026-09-17 (miércoles) — 🏗️ MULTI-HOST Fase A1: fundación (tabla hosts + scan + /hosts)

Primera fase del frente multi-host (plan de 4 fases en la entrada anterior). **Cero cambio de
comportamiento**: los flujos siguen operando el host principal; esto es la FUNDACIÓN.
- **Tabla `hosts`** en el registro: id, ip, api_url, govc_user, **pass_env** (NOMBRE de la env
  var con la password — los secretos siguen SOLO en engine.env), datastore, ssh_port/ssh_key
  (llave por host), estado activo|pausado, prioridad, límites max_* por host (NULL = hereda
  los HOST_MAX_* globales). + columna **vms.host**.
- **Bootstrap automático**: al arrancar, si la tabla está vacía, el host de las env actuales se
  auto-registra como `esxi-245` (principal) y las VMs existentes lo ADOPTAN (host IS NULL).
- **govc(host=...)**: inyecta las credenciales DEL host al subprocess (URL/usuario/password/
  datastore) — la base para operar múltiples ESXi con credenciales independientes.
- **host_recursos()** (scan en vivo por host): datastore libre, CPU cores y RAM total/uso del
  fierro (govc host.info), lo COMPROMETIDO por el motor en ese host (SUM per-host) y los
  límites efectivos. Host caído → alcanzable=false con el error, sin romper nada.
- **GET /hosts** (solo admin): la API que alimentará la pestaña "Motor" del dashboard.
- Tests: 5 escenarios (bootstrap, adopción, credenciales por host inyectadas, /hosts con scan
  simulado y comprometido por host, host caído) + regresión completa de las 9 suites.
- Sin Codex (en la deuda). **Siguiente: Fase A2** — flujos host-aware (govc/esxi_ssh resuelven
  el host de cada VM), selección automática al crear, CRUD de hosts. Luego B (pestaña) y C
  (script enrolar-host para el 192.168.200.121).
- **🚀 DESPLEGADO Y PROBADO EN VIVO**: bootstrap ok (esxi-245 auto-registrado), 16 VMs
  adoptadas, scan real del host: 1041 GB libres en datastore, 16 cores, 159 GB RAM (104 en
  uso por la infra), comprometido 0 (todo en papelera). GET /hosts operativo para la
  futura pestaña Motor.

## 2026-09-17 (miércoles) — ✨ VPS PERSONALIZADO (motor + dashboard) desplegado · diseño multi-host anotado

**Feature pedida por Alcadio**: opción "Personalizado" en el tab Crear del dashboard.
- **Motor** (`/crear`, commit 0449e4c): sabor `personalizado` con specs explícitas — SOLO rol
  admin (WHMCS sigue con catálogo fijo, 403), rangos vCPU 1-24 / RAM 1024-65536 MB / disco
  25-600 GB, pasa por el cupo del host (#8) y el chequeo real de datastore. `sabor_def` se
  inyecta a flujo_crear (ya no re-consulta el catálogo). Registro guarda sabor='personalizado'
  + specs reales (compatible con /editar hacia sabores del catálogo). Tests 5/5 + validado en
  vivo (400 sin specs). Sin Codex (en la deuda).
- **Dashboard v2** (repo fastnetmon, commit e79e07f): opción en el select + fila de specs
  condicional + validación de rangos en cliente; parche con backup (.bak-20260917), imagen v2
  reconstruida, verificado sirviendo el form. DESCUBRIMIENTO: noc.hosting.cl lo sirve el **v2**
  (:8081 de noc-monitor) — el comentario del quadlet sobre "v1 sigue sirviendo" está obsoleto.

**PRÓXIMO FRENTE — GESTIÓN MULTI-HOST DEL MOTOR (diseño acordado, para sesión propia):**
Pestaña "Motor" en el dashboard para administrar EN QUÉ hosts VMware puede actuar el motor
(hoy fijo: ESXI_HOST=10.100.37.245). Piezas del diseño:
1. Tabla `hosts` en el registro (ip, datastore, portgroup/red, estado activo/pausado, límites
   por host) + endpoints CRUD `/hosts` (solo admin) + columna `host` en vms.
2. **Credenciales POR HOST** (SEGURIDAD.md: no se comparten): cada host nuevo necesita su
   svc-vps (API), su llave SSH con wrapper command=, el wrapper en su datastore y su huella en
   known_hosts → script "enrolar-host" que lo semi-automatice.
3. Scan de recursos por host (datastore libre + RAM/CPU vía govc host.info) + cupo #8 per-host.
4. Selección automática al crear: host activo con espacio/cupo; "lleno → siguiente".
5. Efecto dominó: reconciliación/purga/saga/papelera conscientes del host de cada VM.
Ejemplo de Alcadio: agregar 192.168.200.121 y que el motor cree ahí cuando el .245 no dé más.

## 2026-09-17 (miércoles) — #22 resto CERRADO por decisión: el formato NAT es un CONTRATO (no se toca)

Alcadio recordó el porqué del formato del comentario NAT y se VERIFICÓ contra el código del
Monitoreo_externo (fastnetmon/Monitoreo_externo/app/server.js): el sync de targets (cada 5 min)
toma los **srcnat** cuyo comentario empieza con `[NOC]` y parsea **grupo = lo que va tras la
ÚLTIMA coma** (parseNocComment). Gracias a eso **cada VPS del motor entra SOLO al monitoreo**
(grupo "VPS hosting.cl") sin intervención manual. Conclusiones:
- El sufijo/tag propuesto para IDs únicos **habría roto la agrupación** (cada VPS caería en un
  grupo propio) → DESCARTADO. El dstnat también queda SIN comentario (decisión estética de
  agrupación de Alcadio). **El formato NAT completo es intocable.**
- La identificación de reglas propias queda como está: par exacto de IPs (#6) + address-list
  vigilada (#22 parcial) + reconciliación. Riesgo residual (regla manual duplicada con el mismo
  par) aceptado y documentado.
- **La decisión quedó blindada EN EL CÓDIGO**: docstring de crear_nat explica el contrato y
  referencia el parser del monitoreo, para que nadie lo "mejore" sin saber.
- Verificado también: el monitor IGNORA los dstnat por completo, y el dashboard no parsea
  comentarios de NAT en alta/baja (solo address-lists). Con esto la AUDITORÍA queda 24/24
  resuelta o cerrada por decisión (salvo #24 postergado a sesión de arquitectura).

## 2026-09-17 (miércoles) — MEDIOS #16-#23 barridos (queda solo #24 postergado)

Tanda final de la auditoría, **sin Codex (anotada en la deuda)**, tests en 8 suites verdes:
- **#16**: /health sin token (healthcheck del quadlet) o rol whmcs → `{"ok": true}` pelado; el
  detalle (modo/marcas/sabores) solo para rol admin (el dashboard lo recibe via proxy con token).
- **#17**: **matriz de estados por operación** en /accion y /editar: suspender solo desde
  'activo'; reanudar desde activo/suspendido (activo→noop ok, compat WHMCS Unsuspend); eliminar
  desde activo/suspendido/creando/eliminando; editar solo activo/suspendido + **sabor destino
  debe estar activo** (400). Estados no aptos → 409 con mensaje claro.
- **#18**: `set_root_password` verifica el exit de chpasswd (+flush) — sigue best-effort pero
  con señal real (aviso en el job si falla, ya no éxito falso).
- **#19**: script de crecimiento de FS REESCRITO: detecta el dispositivo real de la raíz
  (findmnt, ya no asume /dev/sda3), tolera growpart NOCHANGE (rc=1) y falla con rc=2, crece
  según fstype (xfs/ext4) verificando exit codes, y **compara tamaño antes/después** (FS_OK
  a→b GB en el job); LVM/fs raro → FS_SKIP "crecer a mano". Simulado con binarios fake: 3 caminos.
- **#20**: CERRADO por los rediseños #12/#13 (las llamadas de red de la purga ya corren FUERA
  de DB_LOCK; verificado en el código actual).
- **#21**: migraciones estrictas — solo "duplicate column name" se tolera; BD bloqueada/corrupta
  ABORTA el arranque (supuesto del mensaje validado contra sqlite real).
- **#22 (parcial)**: la reconciliación ahora verifica también la **address-list** de cada
  pública (debe figurar TOMADA: deshabilitada+comentada) → alerta si está liberada por error.
  El resto del #22 (IDs únicos por regla NAT) queda como DECISIÓN DE NEGOCIO: cambiaría el
  formato compartido con las altas manuales del NOC — conversar antes de tocar.
- **#23**: los 2 `except: pass` restantes son best-effort legítimos (el retorno es la señal) —
  documentados con su justificación en el código.
- **#24 (bajo) POSTERGADO** explícitamente: gunicorn + jobs durables es cambio de arquitectura
  (amarrado a multiproceso/locks→SQLite BEGIN IMMEDIATE) — sesión propia, idealmente con Codex.
- **🚀 DESPLEGADO (OK del usuario) y VALIDADO EN VIVO**: /health mínimo sin token + detalle con
  admin; healthcheck del quadlet OK; reconciliación ok con el check de address-list activo.
  **🏁 AUDITORÍA CODEX: 23/24 hallazgos RESUELTOS Y EN PRODUCCIÓN** (+auto-reinicio, timer de
  mantención, gap de purga). Pendientes finales: #24 (arquitectura, sesión propia), IDs únicos
  NAT (#22 resto — decisión de negocio con el NOC), deuda de re-validación Codex (5 ítems,
  docs/pendiente-revision-codex.md, ~15-oct), llave backup@esxi (Fabián).

## 2026-09-17 (miércoles) — FIX #14+#15 (ALTOS finales): secretos con expiración + límites de input

Aplicados **sin Codex (sin cuota; anotados en docs/pendiente-revision-codex.md)**. Con estos
**quedan CERRADOS los 9 hallazgos ALTOS** de la auditoría. Cambios en engine/app.py:
- **#14 — expirar_secretos_jobs()**: los datos de entrega (send_url/send_password) se REDACTAN
  del resultado de jobs con más de SEND_SECRETO_TTL_DIAS (env, default 2 — la vida real del
  Send); el resto del resultado (IPs, WHM, fingerprint) se conserva para el historial. Corre en
  la mantención diaria (dentro de flujo_purgar) e informa el conteo en el resumen. Idempotente.
- **#15 — límites de input**: MAX_CONTENT_LENGTH 64 KB (413 JSON); json_body() tolerante al
  Content-Type pero JSON malformado/no-objeto → 400 JSON limpio (antes: HTML de Flask);
  serviceid/whmcs_serviceid estrictamente numéricos (400); root_password máx 128 (RECHAZA, no
  trunca — una clave truncada sería otra clave); pubkey con tope de largo antes de la regex;
  cliente cap 40; **actor saneado** (charset acotado + máx 60 — ya no puede contaminar
  auditoría/UI con caracteres de control o markup).
- Tests: 6 escenarios nuevos (#14 completo con TTL e idempotencia; 400/413 JSON; serviceid en
  3 endpoints; root_password/whmcs_serviceid/pubkey; actor saneado end-to-end hasta la tabla
  jobs) + regresión total de los 6 suites en verde.
- **🚀 DESPLEGADO (OK del usuario) y VALIDADO EN VIVO:** 400/413/400 correctos contra el motor
  real, y el barrido de secretos redactó **12 → 2** jobs con claves de Send vivas (las 2
  restantes tienen <2 días y expiran solas). La mantención completa re-validada de paso
  (purga ok + reconciliación ok). **Con esto: 6/6 críticos y 9/9 altos CERRADOS Y EN
  PRODUCCIÓN.** Quedan 7 medios, 2 bajos, y la deuda de re-validación Codex (4 ítems, ~15-oct).

## 2026-09-17 (miércoles) — 🚀 DEPLOY #8+#9 con validación en vivo

Desplegado el paquete (OK del usuario, cupos por DEFECTO 24 vCPU/64 GB/600 GB — ajustables en
engine.env con HOST_MAX_* y DATASTORE_RESERVA_GB):
- known_hosts PINNED generado en noc-monitor (**4 huellas**: ESXi ecdsa+rsa, RouterData y CCR
  en [host]:2420); script instalado en /usr/local/bin/vps-known-hosts.sh para rotaciones.
- Contenedor rebuild + restart, health 200.
- **Validación en vivo:** reconciliación completa OK usando esxi_ssh PINNED (RejectPolicy contra
  el ESXi real) y consulta estricta al RouterData con la huella (1025 reglas NAT). Anti-MITM
  operativo en las 3 máquinas de infraestructura fija.

## 2026-09-17 (miércoles) — FIX #9 (ALTO): pinning de host keys SSH (ESXi + MikroTiks)

Aplicado **sin Codex (sin cuota; anotado en docs/pendiente-revision-codex.md junto al #8 y el
timer)**. Cambios:
- **cliente_ssh_pinned()**: paramiko con `load_host_keys(KNOWN_HOSTS_PATH)` + **RejectPolicy** —
  host desconocido o huella distinta → conexión RECHAZADA (anti-MITM). Fail-closed: sin el
  archivo no hay conexión a la infra fija. Usado por esxi_ssh (antes AutoAddPolicy).
- **mikrotik()**: `-o UserKnownHostsFile=/keys/known_hosts -o StrictHostKeyChecking=yes`
  (antes `no`). Cubre RouterData y el CCR de borde (formato `[host]:2420`).
- **noc-monitor/known-hosts.sh** (nuevo, versionado): genera el archivo con ssh-keyscan de las
  3 máquinas; NO instala archivos incompletos (si una no responde, aborta). Re-correr SOLO ante
  cambio legítimo de llaves (reinstalación de ESXi/MikroTik) — si el motor empieza a rechazar
  conexiones, la pregunta es POR QUÉ cambió la huella, no cómo callarlo.
- **TOFU deliberado en VPS nuevos** (documentado en el código): las conexiones a las VMs recién
  creadas siguen con AutoAdd SIN known_hosts — su llave nace con la VM (imposible pre-conocerla)
  y las IPs se REUTILIZAN (.247 en cada prueba: un known_hosts las haría chocar entre sí).
- Tests: 4 escenarios (fail-closed sin archivo; RejectPolicy + huellas cargadas incl. puerto no
  estándar; opciones estrictas en mikrotik; TOFU de VPS intacto) + regresión total verde.

## 2026-09-17 (miércoles) — FIX #8 (ALTO): cupo del host + espacio real del datastore

Aplicado **sin revisión de Codex (sin cuota — regla del usuario aplicada)**, compensado con
tests exhaustivos + regresión completa. Implementa además los "Límites de recursos" que
SEGURIDAD.md prometía. Cambios en engine/app.py:
- **Cupo del host** (env `HOST_MAX_VCPU=24`, `HOST_MAX_RAM_MB=65536`, `HOST_MAX_DISCO_GB=600` —
  la propuesta de SEGURIDAD.md; 0 = sin límite): `validar_cupo()` suma lo comprometido (filas
  vigentes; papelera/purgando no cuentan) y rechaza si el nuevo plan no cabe. **Atómico con la
  reserva del nombre** (mismo NOMBRE_LOCK) → cero sobreventa entre creaciones concurrentes
  (test: 9 hilos, cupo 600, exactamente 6 de 100 GB caben). /crear responde **409 con el motivo**
  (WHMCS lo muestra en el módulo). `editar` valida su delta (upgrades consumen, downgrades pasan).
- **Espacio real del datastore** (paso 3): `datastore_libre_gb()` vía `govc datastore.info -json`
  (bytes exactos; tolera shapes de distintas versiones de govc; sin datastore → fail-closed).
  Regla: `libres ≥ disco_del_plan + DATASTORE_RESERVA_GB` (env, default 50) o la creación aborta
  ANTES de clonar. Antes este paso solo imprimía el df sin verificar nada.
- **⚠️ OPERATIVO:** los límites por defecto quedan ACTIVOS al desplegar: 24 vCPU / 64 GB RAM /
  600 GB disco para clientes en el host. Con los sabores actuales (~100-153 GB) caben ~4-6 VPS
  → **Alcadio debe definir los valores reales** en engine.env (HOST_MAX_*) según lo que quiera
  reservar para la infraestructura del host compartido.
- Tests: 5 escenarios nuevos (concurrencia sin sobreventa, papelera libera, bordes exactos,
  parser datastore 3 variantes, 409 endpoint) + regresión total (roles/saga/purga/reconciliación).

## 2026-09-17 (miércoles) — ⏰ Timer de mantención diaria instalado (gap encontrado: la purga nunca corría sola)

Pregunta del usuario sobre "la purga real" destapó que **la purga diaria del diseño nunca se
automatizó** (sin timer/cron/scheduler — solo corría a mano). Instalado en noc-monitor (con OK):
- **vps-mantencion.timer** (04:30 America/Santiago, Persistent=true) → **vps-mantencion.service**
  (oneshot, Requires vps-engine) → **/usr/local/bin/vps-mantencion.sh**: POST /purgar-papelera →
  poll hasta estado terminal → POST /reconciliar → poll; exit 0 SOLO si ambos jobs 'ok' (falla
  visible como 'failed' en systemd). Archivos versionados en **noc-monitor/** del repo.
- Codex revisó 2 rondas (abort sin job_id/terminal, capturas -sf, sed del token, Requires,
  propagación del error al exit code — todo aplicado); validado contra mock HTTP (feliz EXIT=0,
  job error EXIT=1) y **corrida real: Succeeded** (purga ok + reconciliación ok).
- ⚠️ **Codex AGOTÓ SU CUOTA** en la ronda 3 (formalidad; se renueva ~15-oct). Regla del usuario
  aplicada: se sigue sin Codex mencionándolo. **Review gate DESACTIVADO** mientras tanto
  (reactivar con /codex:setup --enable-review-gate cuando vuelva la cuota).
- **Primera purga con borrado definitivo: sábado 19-09 04:30** (las entradas del 11-09 ~19:00
  cumplen 7 días exactos recién en la noche del 18) — revisar ese job en el dashboard.

## 2026-09-17 (miércoles) — FIX #11 (ALTO): reconciliación registro ↔ ESXi/RouterData/NetBox/bóveda

Aplicado y **aprobado por Codex a la primera**. Nuevo `flujo_reconciliar` + endpoint
**POST /reconciliar** (solo admin; idempotente — comparte el gate de mantención vm='-' con la
purga). Filosofía del #12/#13: **reparar solo con propiedad propia; alertar todo lo demás**:
- **ESXi vs registro**: VMs vps-* sin fila → alerta de deriva (el "futuro" de SEGURIDAD.md ya
  implementado); filas sin carpeta en VPS/ → alerta (mensaje específico si es un 'eliminando'
  pendiente de reintento). VMs con job corriendo se excluyen (tránsito legítimo).
- **NAT vs registro**: verificación del par exacto srcnat+dstnat por cada VPS con pública —
  SOLO consulta; NAT incompleto o RouterData caído → alerta (jamás repara: tabla compartida).
- **NetBox**: la ÚNICA reparación automática — ids muertos/perdidos se re-registran
  (netbox_ip_add idempotente por address) y el id vuelve a la fila.
- **Bóveda**: /list de vps-provision → llaves referenciadas inexistentes y llaves huérfanas sin
  fila → alertas (el motor no borra nada de la bóveda aquí).
- Alertas: al job (dashboard) + tabla de auditoría (cap 20 + resumen). GARANTÍA testeada: cero
  mutaciones a ESXi/RouterOS/bóveda (el test falla ante cualquier intento).
- Tests: escenario de 7 VMs (deriva, sin carpeta, eliminando, tránsito excluido, NAT incompleto
  detectado, NetBox reparado y persistido, llave inexistente + huérfana, pruebas sin ruido) +
  regresión completa (purga/saga/roles) verde.
- **Pendientes anotados por Codex (menores):** validar también la entrada de address-list de la
  pública (no solo el par NAT) — se puede sumar cuando toque el #22 (IDs únicos RouterOS); y
  asumir alertas transitorias si un job arranca justo después del snapshot (sin mutación, solo ruido).
- Cómo se usa: `curl -X POST -H "X-Auth-Token: $ET" http://127.0.0.1:8224/reconciliar` (o botón
  futuro en el dashboard); candidato a timer diario junto a la purga.
- **🚀 DESPLEGADO (OK del usuario) y PRIMERA CORRIDA REAL: 0 ALERTAS** — job 2e71397e689b:
  0 VMs vigentes (todo en papelera, correcto), NAT/NetBox limpios, y **12 llaves referenciadas ·
  12 en bóveda · 0 huérfanas**: toda la semana de pruebas dejó las 4 fuentes consistentes
  (validación indirecta de todos los fixes). Nota cosmética anotada: el conteo "en tránsito"
  incluye al propio job de mantención (vm='-') — pulir cuando se toque ese código.

## 2026-09-15 (lunes, noche) — Bloque 3: Gerardo VA A EXPLICAR su config (plan B del diff DESCARTADO)

Decisión del usuario: **Gerardo accedió a explicar** qué le configura a un cPanel antes de
entregarlo. El plan B (ingeniería inversa por diff de perfiles) queda **descartado** — no
retomarlo. Próximo paso del Bloque 3: cuando Alcadio traiga la explicación (idealmente lista de
pasos con pantallas de WHM o comandos, y los VALORES que usa), replicarla junto a Claude y
programarla como paso post-cPanel del motor (whmapi1/archivos + verificación, mismo estándar de
los fixes de hoy). El PTR sigue siendo nuestro (MikroTik) → directo.

## 2026-09-15 (lunes, noche) — FIX #12+#13 (ALTOS): purga con allowlist + marca 'purgando' + bóveda-primero

Aplicado y **aprobado por Codex en 5 rondas** (las objeciones fueron subiendo el nivel: allowlist,
ventanas de interrupción, borrado lógico masivo por listado enmascarado, evidencia persistida,
deriva falsa por snapshots). Diseño final (engine/app.py + esxi/vps-wrapper.sh):
- **Wrapper**: nuevo `purge-entry <entrada>` (borra UNA entrada explícita re-validando nombre
  estricto y >7 días por su lado); **eliminado el `purge-trash` masivo**. `list-vps`/`list-trash`
  ahora **fallan cerrado** si no pueden leer (antes enmascaraban con 2>/dev/null → un listado
  vacío falso habría causado borrado lógico masivo) y mantienen el sentinel OK.
- **Motor (flujo_purgar)**: allowlist = entradas de _papelera ∩ registro propio ∩ >7 días
  (prefijo AAAAMMDD-HHMMSS). Entradas AJENAS al registro → deriva: alerta y NO se tocan.
  `listar_wrapper()` exige el sentinel OK (listado no confiable → aborta sin tocar nada; usado
  también en la saga #7). Por entrada: **bóveda PRIMERO** (#13; sin PROVISION_TOKEN o con falla →
  entrada conservada, reintento diario; el HTTP 404 estructurado se tolera como ya-borrada) →
  vault_item=NULL → **marca estado='purgando'** (evidencia persistida) → purge-entry → DELETE.
- **Reconciliación por evidencia propia**: filas ausentes de _papelera SOLO se limpian si llevan
  la marca 'purgando' (purga propia interrumpida antes del DELETE); una fila 'papelera'
  desaparecida SIN marca (borrado manual, pérdida de datastore) se CONSERVA con alerta (job +
  auditoría). VM restaurada a VPS/ (restore-trash) → aviso, no se toca. La reconciliación re-lee
  registro y _papelera FRESCOS (evita deriva falsa por purgas del mismo loop o eliminaciones
  concurrentes). Guards: /accion y /editar → 409 sobre 'purgando'.
- Tests: 8 escenarios de purga (incl. listado caído/sin sentinel → aborta sin borrado lógico;
  ausencias masivas sin marca conservadas; con marca converge) + regresión saga #7 + wrapper
  local (purga vieja/rechaza joven/inválida/inexistente; _papelera inaccesible → die).
- **🚀 DESPLEGADO (con OK del usuario) y VALIDADO EN VIVO:** (1) wrapper al ESXi con backup
  (`vps-wrapper.sh.bak-20260915`), normalización CRLF (sed; el ESXi no tiene tr) y `sh -n` antes
  de reemplazar; probado por el canal real del motor: ping→pong, list-trash con sentinel (14
  entradas, la más vieja 2026-09-11), purge-trash → "no permitido", purge-entry joven →
  rechazada. (2) Contenedor rebuild + restart, health 200. (3) **Purga real ejecutada: 0
  purgadas (todo joven) · 0 ajenas (las 14 entradas están TODAS en el registro — consistencia
  total) · 0 desaparecidas.** Primera purga real esperable ~2026-09-18 (cuando las entradas del
  11-09 cumplan 7 días) — revisar ese job en el dashboard.

## 2026-09-15 (lunes, tarde) — #10 CERRADO por verificación: el "ssh root al ESXi" está confinado como diseñado

**Verificado en el host real** (con Codex de acuerdo en cerrar así, sin cambio de código):
- La llave del motor en /etc/ssh/keys-root/authorized_keys tiene EXACTAMENTE el confinamiento de
  la capa 4: command= al wrapper + no-pty + no-port/X11/agent-forwarding → sin shell root aunque
  la llave se filtre. authorized_keys (600 root) y wrapper (755 root) no-escribibles por el motor.
- La sesión autentica como root porque ESXi lo exige para vmkfstools/mv del datastore (un usuario
  con rol admin sería root-equivalente); svc-vps es solo para la API. **La protección es el
  forced-command, no la identidad** — se corrigió la redacción imprecisa de SEGURIDAD.md
  ("no opera como root ni por SSH") y el docstring de esxi_ssh para reflejar la realidad.
  (El docstring viaja en el próximo deploy funcional; no amerita rebuild propio.)
- **⚠️ HALLAZGO COLATERAL para Alcadio (fuera del proyecto Vps, NO se tocó):** en el mismo
  authorized_keys de root hay una llave `backup@esxi` SIN NINGUNA restricción — shell root
  completo si se filtra. Recomendación: restringirla (command= de su herramienta de backup y/o
  from= con la IP de origen). Conversarlo con Fabián/operaciones.

## 2026-09-15 (lunes, tarde) — FIX #7 (ALTO): eliminación = saga re-ejecutable con estado 'eliminando'

Aplicado y **aprobado por Codex** (2 rondas — pidió guard de estado, verificación del estado real
tras errores, y recuperar la entrada real de papelera). Cambios en engine/app.py:
- **Estado 'eliminando'** persistido al iniciar el flujo (visible en dashboard; agregado al schema
  comment). Éxito → 'papelera' como siempre; aborto → queda 'eliminando' + job error.
- **Pasos idempotentes para REINTENTAR** un eliminar abortado (antes moría en apagar/unregister):
  apagar se omite si la VM ya no está en el inventario (power_state "?"); unregister tolera
  "not found" SOLO si el inventario vivo lo confirma; borrar_nat ya era re-ejecutable (#6);
  trash-vm tolera "no existe" SOLO tras confirmar con **list-vps** (no sigue en VPS/) y recuperar
  la **entrada real** con **list-trash** (wrapper ya lo tenía) — sin placeholders, incluso si el
  intento anterior murió entre el mv y el registro.
- **Guard de estado**: /accion y /editar → 409 sobre una VM 'eliminando' salvo reintentar
  'eliminar' (adelanto puntual del #17). Errores engañosos ("no existe" con la carpeta aún en
  VPS/) → abort con "revisar a mano".
- Tests 5/5 (módulo real, deps simuladas): aborto en NAT deja estado seguro; reintento completa;
  entrada real recuperada; error engañoso detectado; 409 de suspender/editar sobre 'eliminando'.
- Recuperación operativa: un eliminar que aborta (p.ej. RouterData caído) se resuelve
  **re-disparando la misma acción** (dashboard o Terminate de WHMCS) cuando el entorno sane.

## 2026-09-15 (lunes, tarde) — ✅ E2E COMPLETO desde WHMCS con el motor endurecido: TODO VALIDADO

**Segundo Create desde WHMCS (mismo servicio 36655, botón Create sobre el servicio terminado):**
job d00d47ab81cf → **vps-hcl-0014-alcadio ACTIVO en ~4 min** (pública 38.19.57.102 ↔ privada
10.100.16.247). Esta vez el primer boot aplicó la IP a la primera (sin auto-reinicio). Validado
en producción con el motor endurecido + auto-reinicio desplegados:
- Canal WHMCS → motor con token acotado (actor whmcs:36655) ✅
- Modo fijado por WHMCS_MODO=produccion (no por el body) ✅
- Reserva atómica de nombre (0014, sin chocar con la 0013 en papelera del MISMO serviceid) ✅
- IP privada/pública por locks + NAT creado con re-chequeo ✅
- Clave de root de la ficha aplicada → WHM 200 en https://38.19.57.102:2087, licencia solicitada ✅
- **IP en la ficha de WHMCS rellenada sola** (whmcs_set_ip) ✅
- Terminate previo (0013) limpio: papelera + "sin IP pública que liberar" ✅
**El flujo completo Create→(uso)→Terminate quedó operativo tal como venía, ahora endurecido.**

**Cierre del ciclo:** Terminate de la 0014 también validado (job 337485fa0245, 38 s): apagada →
des-registrada → **NAT removido con la verificación estricta del par exacto** (primer ejercicio
en producción del camino completo del fix #6 con NAT real — un residuo habría abortado el job)
→ pública 38.19.57.102 liberada → Send cerrado → papelera 20260915-173035. **Sin VPS de prueba
activos; RouterData limpio.** Próxima sesión: hallazgos ALTOS de Codex (#7 saga + #11
reconciliación primero, luego #10 aclarar ssh root vs svc-vps del ESXi), y los pendientes de
negocio (bloque 3 Gerardo, correo bienvenida, licencia #5, go-live).

## 2026-09-15 (lunes, tarde) — 🧪 Prueba E2E desde WHMCS: incidente del primer boot + FIX auto-reinicio

**Prueba real del ciclo desde WHMCS** (Create del usuario, producto cPanel, servicio 36655):
- ✅ El canal y los fixes nuevos funcionaron en vivo: actor whmcs:36655, modo produccion por
  WHMCS_MODO, nombre vps-hcl-0013-alcadio reservado atómico, IP privada 10.100.16.247 por lock,
  clon + registro + cloud-init + encendido ok.
- ❌ **INCIDENTE (guest, no motor):** el primer boot de la dorada cPanel NO aplicó la IP estática
  (carrera cloud-init/NetworkManager con growpart 103GB + primer arranque pesado de cPanel).
  Evidencia: metadata del VMX descodificada = config PERFECTA; Tools corriendo; NIC conectada
  (Vps_Hosting.cl); solo IPv6 link-local. El motor abortó LIMPIO a los 320s (sin pública, sin
  NAT, sin huérfanos — los fixes de hoy trabajando). Un **reinicio manual del guest aplicó la IP
  al instante** (confirmado con vigía: ping OK tras reboot). La 0010 de ayer pasó por el mismo
  estado transitorio y se recuperó dentro de la ventana — defecto LATENTE, no regresión.
- ✅ **Terminate de la 0013 desde WHMCS: limpio** (job 400efd1489e0, 6 s, camino "sin IP pública
  que liberar", a papelera). Eliminación con motor endurecido validada en producción.
- **FIX auto-reinicio (aprobado por Codex, 2 rondas):** en el paso 11 (verificar SSH), si a los
  ~150 s no entra SSH se reinicia la VM UNA vez (govc vm.power -r por Tools, fallback -reset;
  ambos capturan RuntimeError y subprocess.TimeoutExpired para nunca abortar la espera), presupuesto
  total ~8.7 min, detalle claro en el job y en el error final ("incluso tras 1 reinicio automático").
  Flags -r/-reset verificados contra el govc real del contenedor. Con esto, el incidente de hoy se
  auto-sana sin intervención.
- Pendiente inmediato: redesplegar el contenedor con este fix y repetir el Create para el E2E completo.

## 2026-09-15 (lunes, tarde) — 🚀 DESPLEGADO a producción (noc-monitor) el motor endurecido (6 fixes)

**Deploy ejecutado y verificado** (commit b3b70a7 → contenedor vps-engine):
- Backups en el server: `build/engine/app.py.bak-20260915` y `engine.env.bak-20260915`.
- app.py subido (md5 verificado idéntico al repo), **`WHMCS_MODO=produccion` agregado a
  engine.env** (requisito del fix #4+#5 para que las creaciones de WHMCS sigan saliendo
  en producción), `podman build` + `systemctl restart vps-engine` → contenedor sano.
- **Smoke tests contra el motor VIVO — todos OK:** /health 200 (modo pruebas default del
  dashboard intacto); /vms con ENGINE_TOKEN → 200; con WHMCS_TOKEN → **403**;
  /purgar-papelera whmcs → **403**; /accion whmcs con vm explícita → **403**;
  /vm-por-servicio whmcs → 200; token inválido → 401. La matriz de roles funciona en vivo.
- **PENDIENTE la prueba E2E** (crear VPS de prueba → validar → eliminar): el clasificador
  de permisos de la sesión de Claude bloqueó la creación de VMs en producción (límite
  razonable). El usuario la dispara desde el dashboard NOC (tab Crear: VPS Estandar,
  modo producción, sin cPanel, cliente test-fixes) o con curl al motor; Claude monitorea
  el job (lectura) y valida NAT/papelera al eliminar.

## 2026-09-15 (lunes, tarde) — FIX #6: eliminación segura del NAT (verificado antes de liberar) — 🏁 6/6 CRÍTICOS CERRADOS

Aplicado y **aprobado por Codex con observaciones** (validó además la sintaxis RouterOS v6/v7
contra la documentación de MikroTik). Cambios en engine/app.py:
- **borrar_nat con orden seguro**: remove srcnat/dstnat verificando el exit de CADA comando →
  **verificación estricta del par exacto** (`print terse where chain=... and src-address="X" and
  to-addresses="Y"`, 2 consultas, valores entrecomillados — ya no la búsqueda difusa por
  aparición de la IP, que daba falsos positivos con reglas ajenas y ÉXITO FALSO si la consulta
  caía) → la pública se libera en la address-list **SOLO después de verificar** (antes se
  liberaba antes de verificar). Cualquier falla → RuntimeError; la pública queda deshabilitada/
  comentada → ni el motor ni el alta manual del NOC la reasignan.
- **flujo_eliminar ya no traga la falla del NAT**: si borrar_nat lanza, la eliminación ABORTA —
  job en ERROR, la VM NO va a papelera, sus IPs siguen "ocupadas" en el registro. Antes: seguía
  a papelera con un "aviso" → NAT huérfano apuntando a un VPS muerto + pública reasignable.
- **⚠️ Estado de recuperación (hasta el fix #7 - saga):** si la eliminación aborta en el paso NAT,
  la VM queda apagada y DES-REGISTRADA del ESXi pero 'activa' en el registro, con NetBox/Send/
  papelera sin ejecutar. Es la condición SEGURA (nada se pierde ni se reasigna); la recuperación
  es re-intentar eliminar cuando RouterData esté sano — pero el re-intento hoy tropieza con los
  pasos ya hechos (apagar/unregister no son idempotentes aún). Eso lo resuelve el #7.
- Tests con mikrotik() simulado: 5/5 (feliz con orden asertado; remove caído; verificación caída
  —antes daba éxito falso—; regla residual; liberación caída). En fallas nunca se libera.

**🏁 Con esto los 6 hallazgos CRÍTICOS de la auditoría Codex están cerrados (todos aprobados por
Codex, commiteados y pusheados). NADA desplegado aún al contenedor vps-engine — desplegar en
lote: podman build + WHMCS_MODO=produccion en engine.env (ver fix #4+#5). Siguen 9 ALTOS
(#7 saga de eliminación, #8 espacio datastore, #9 host keys SSH, #10 ssh root al ESXi vs
SEGURIDAD.md, #11 reconciliación, #12 purga con allowlist, #13 bóveda en purga, #14 secretos en
jobs, #15 límites de input) + 7 medios + 2 bajos.**

## 2026-09-15 (lunes, tarde) — FIX #4+#5: permisos por token (rol whmcs acotado) + modo no forzable

Aplicado y **aprobado por Codex** (2 rondas: pidió fail-fast de config, capar `resultado` en /job
y validar el modelo de confianza del serviceid). Cambios en engine/app.py:
- **auth() devuelve ROL**: 'admin' (ENGINE_TOKEN, todo igual) / 'whmcs' (WHMCS_TOKEN) / None, con
  hmac.compare_digest (parte del #16). Sin WHMCS_TOKEN configurado → solo admin (compat).
- **Rol whmcs = allowlist de lo que usa el módulo**: /crear (whmcs_serviceid OBLIGATORIO), /accion
  y /editar (vm explícito → 403; serviceid obligatorio; VM resuelta SOLO server-side →
  pertenencia por construcción), /vm-por-servicio, /job/<id> (SIN `resultado`: ahí van las
  credenciales del Send — el módulo solo lee estado/pasos). /vms, /jobs, /auditoria,
  /purgar-papelera → 403. Antes el token WHMCS podía TODO (purgar, eliminar por nombre, etc.).
- **#5 modo**: rol whmcs NO elige modo — usa env **WHMCS_MODO** (default MODO); discrepancia con
  el body se audita. Rol admin conserva el selector del dashboard.
- **Fail-fast al arrancar**: WHMCS_TOKEN==ENGINE_TOKEN, MODO o WHMCS_MODO inválidos → el
  contenedor NO parte (RuntimeError con mensaje claro).
- **⚠️ DEPLOY**: agregar **WHMCS_MODO=produccion** a engine.env — sin eso las creaciones de WHMCS
  salen en modo pruebas (los productos ZZZ crean en producción vía body, que ahora se ignora).
- Modelo de confianza validado con Codex: 1 solo WHMCS → su token representa la instancia
  completa; frontera real = no alcanza VMs sin serviceid ni operaciones de NOC. **Pendiente
  multi-marca** (si algún día hay 2+ WHMCS): token por marca + chequeo marca↔serviceid.
- Tests contra la app real (flask test_client, flujo stubbeado): 21 de matriz de roles + 3
  arranques rechazados + resultado capado. Módulo PHP: CERO cambios (ya envía todo lo requerido).

## 2026-09-15 (lunes, tarde) — FIX #3: exclusión mutua por VM ("un solo job activo por VM")

Aplicado y **aprobado por Codex** (rechazó la 1ª versión por una carrera fina real: la reserva
de /crear graba whmcs_serviceid ANTES de existir el job → un Terminate de WHMCS llegando en esa
ventana resolvía la VM por serviceid, pasaba el gate y eliminaba una VM a medio nacer; corregido).
Cambios en engine/app.py:
- **Gate por VM** (`lanzar_job_exclusivo` + JOB_GATE_LOCK): antes de crear un job se verifica en
  sección crítica que no haya otro 'corriendo' para esa VM (tabla jobs). /accion y /editar
  devuelven **409** con tipo+id del job activo; /purgar-papelera es idempotente (devuelve el job
  existente); /crear hace **reserva de nombre + creación del job bajo el mismo gate** (cierra la
  ventana del serviceid). Protege también la creación: eliminar/editar durante un crear → 409.
- **Limpieza al arrancar** (init_db): jobs 'corriendo' huérfanos de un proceso anterior →
  estado='error' "interrumpido por reinicio del motor" (antes quedaban girando eternos en el
  dashboard; con el gate habrían bloqueado su VM para siempre).
- **Thread.start fallido** (`lanzar_o_fallar`): si el hilo no arranca, el job se marca error
  (libera el gate) + 500 genérico; /crear además borra la reserva intacta y audita.
- **OJO arquitectura** (documentado en el código): el gate y los locks son threading.Lock →
  válidos SOLO con 1 proceso. Migrar a gunicorn (hallazgo #24) exige gate por transacciones
  SQLite (BEGIN IMMEDIATE) y repensar la limpieza de init_db.
- Cambios visibles (deseables): acción sobre VM ocupada = 409 claro (antes corría en paralelo);
  tras reinicio los jobs interrumpidos salen 'error' (antes 'corriendo' eterno).
- Probado: 6 acciones simultáneas sobre la misma VM → pasa 1; Terminate martillando en paralelo
  a un Create → 409; huérfanos limpiados no bloquean.

## 2026-09-15 (lunes, tarde) — FIX #2: carreras de asignación (nombre/IP priv/IP púb+NAT) cerradas

Aplicado y **aprobado por Codex** (con sus 3 observaciones ya incorporadas). El problema: los jobs
de creación corren en threads y la selección es determinista (el recurso más alto libre), así que
dos creaciones simultáneas (p.ej. dos pagos WHMCS juntos) elegían el MISMO nombre/IP. Cambios en
engine/app.py, **sin cambio de comportamiento observable** en el caso secuencial:
- **3 locks por recurso** (NOMBRE_LOCK, IP_PRIV_LOCK, IP_PUB_LOCK): la ventana leer→elegir→reservar
  de cada recurso es atómica entre jobs; el clonado (minutos) sigue en paralelo y reservar un
  nombre no espera el barrido ping de otra creación.
- **reservar_vm()**: el nombre se calcula y se INSERTa ('creando') en una sola sección crítica en
  /crear (antes: cálculo en endpoint + INSERT después en el hilo = ventana). nombre es PK (3ª capa).
  El paso 1 de flujo_crear ya no inserta: valida la reserva.
- **IP privada**: elegir + set_estado(ip=...) bajo lock (ambos ip_libre_* consultan el registro →
  el siguiente job la ve ocupada). **IP pública**: elegir + crear_nat + grabar bajo lock; el
  re-chequeo anti-carrera interno de crear_nat queda como capa contra actores EXTERNOS (altas
  manuales del NOC sobre RouterData).
- Robustez del endpoint: si Job/run_job fallan tras reservar, se borra la reserva (sin fila
  'creando' huérfana por esa vía); errores de reserva → auditoría + mensaje genérico (sin SQL crudo).
- Probado: 12 creaciones concurrentes → nombres 0001..0012 únicos y 12 IPs únicas (sin lock, colisiona).
- Pendiente relacionado (hallazgos #7/#11): reconciliación de filas 'creando' de jobs muertos a
  mitad de flujo (preexistente, no lo introduce este fix).
- Permisos git (commit/push) agregados a .claude/settings.json de Proyectos para el flujo de trabajo.

## 2026-09-15 (lunes, tarde) — 🔍 Auditoría del motor con Codex (24 hallazgos) + fix #1 aplicado

Se instaló el flujo Claude→Codex (plugin openai-codex; review gate activado; regla: todo código
nuevo pasa por revisión de Codex, y si Codex está sin cuota se sigue sin él). **Codex auditó
engine/app.py completo**: 24 hallazgos (6 críticos, 9 altos, 7 medios, 2 bajos) — informe en el
chat; los críticos: (1) inyección shell vía pubkey BYO, (2) carreras en asignación nombre/IPs/NAT,
(3) sin exclusión mutua por VM, (4) WHMCS_TOKEN = admin total, (5) el body puede forzar modo
produccion, (6) borrar_nat no verifica y la eliminación sigue ante fallo. Matices: el (5) es
decisión de diseño (selector por creación) a endurecer antes del go-live; el (10, ssh root al
ESXi) contradice SEGURIDAD.md → verificar. Se acordó con el usuario ir **uno a uno, sin cambiar
comportamiento observable** (mismo flujo/resultados; depurar por debajo).

**FIX #1 (inyección vía pubkey_cliente) APLICADO y aprobado por Codex:**
- `instalar_pubkey_en_vm`: la llave ahora viaja por **STDIN** con script remoto FIJO
  (`key=$(cat)` + `grep -qxF -- "$key"` + `printf`) — mismo patrón que set_root_password/chpasswd.
  Ya no se interpola nada en el shell. Además verifica exit status y lanza RuntimeError si falla
  (antes fallaba en silencio reportando "llave instalada").
- `PUBKEY_RE`: comentario restringido a `[A-Za-z0-9@ ._:+=/-]{0,120}` (defensa en profundidad;
  rechaza $, backticks, ;, | con el mismo mensaje "llave pública inválida" de siempre).
- Probado local: compila; 5 llaves legítimas pasan / 4 maliciosas rechazadas; simulación bash del
  script = instala, no duplica, y un payload `$(touch PWNED)` queda como texto inerte.
- Nota técnica: Codex objetó buffering de stdin en paramiko; verificado contra el fuente real:
  con bufsize=-1 paramiko es NO bufferizado (write→_write_all inmediato), el EOF no puede
  adelantarse. Se agregó `stdin.flush()` igual como seguro. Codex aprobó con esa evidencia.
- **OJO: el fix está en el repo, NO desplegado aún al contenedor vps-engine de noc-monitor**
  (se desplegará en lote al cerrar varios fixes). Siguiente: hallazgo #2 (carreras de asignación).

## 2026-09-15 (lunes, cierre) — 📌 ESTADO Y PENDIENTES para retomar en otro chat

**Modelo mental — el proyecto en 4 bloques:**
1. **WHMCS** — 🟢 listo (canal seguro, módulo hostingcl_vps, callback motor→WHMCS, validado E2E).
2. **Motor** — 🟢 prácticamente cerrado (crear/gestionar + IP a la ficha sola + upgrades/Change Package).
3. **Config post-levantamiento** (cPanel: PTR, dominio, DNS, SSL, cuenta cPanel…) — 🔴 **próximo frente**.
4. **Entrega al cliente** — 🟡 (BYO, bóveda/Send, clave root→WHM listos; falta correo de bienvenida con IP).

**Lo hecho esta sesión (bloques 1-2):** relleno automático de IP en la ficha (opción C: motor→WHMCS
API, `whmcs_set_ip`/`whmcs_api` en el motor, credential + IP allowlist 192.168.122.252 en WHMCS —
todo en docs/whmcs-intervenciones.md). Change Package (editar/upgrade) validado. Catálogo de prueba
completo: **6 productos** (3 sabores × con/sin cPanel) en grupo ZZZ-PRUEBAS. UX del dashboard: paso a
paso en pestaña Jobs (2 columnas), al crear salta a Jobs. TZ del contenedor corregida. Sin VPS de
prueba activos (todo terminado).

**PENDIENTES (para el próximo chat):**
- **Bloque 3 — el gran frente (config post-cPanel):** el usuario va a **hablar con Gerardo (operaciones)**
  para que muestre qué le hace a un cPanel antes de entregarlo (PTR, cuenta cPanel del dominio, DNS,
  AutoSSL, seguridad, etc.). Gerardo es reacio (hoy lo hace con un agente Claude por máquina — caro,
  no repetible). **Plan B si no coopera:** ingeniería inversa = crear un cPanel de prueba y hacer
  **diff** vs uno ya entregado de producción; el **PTR ya es NUESTRO** (red/MikroTik) → automatizar
  directo. Objetivo: codificar esos pasos en el motor (determinista, gratis, repetible).
- **Bloque 4 — correo de bienvenida con IP:** falta agregar el permiso **SendEmail** al rol API "Motor
  VPS callback" (no aparecía en su versión de WHMCS — revisar) y programar el motor para dispararlo con
  la IP al terminar el VPS. El cable motor→WHMCS ya está.
- **#5 — Licencia cPanel (compartida):** automatizar asignar del pool a la IP al crear + liberar al
  eliminar. Falta que el usuario diga **cómo la asigna hoy** (¿Manage2 API con usuario+access hash? portal? addon?).
- **Go-live productos reales (335/336/338):** cambiar SOLO Module Settings a Motor Vps. Prerrequisitos
  (checklist "Go-live" del tablero): #5 licencia, quitar límite de IPs de prueba (publica_rango_prueba
  .100-.102 → pool completo Red57-0, config del motor), plan para clientes actuales (quedan manuales;
  solo nuevos se automatizan), Auto Setup manual → 1 orden real → luego "al pagar".

## 2026-09-15 (lunes) — Motor→WHMCS: cliente API + relleno automático de IP (sin "Sincronizar" manual)

El usuario notó que tras la creación había que apretar "Sincronizar datos" para traer la IP a
la ficha (por el diseño no-bloqueante). Se eligió la **opción C: el MOTOR avisa a WHMCS por su
API** (dirección motor→WHMCS, reutilizable para más cosas). Implementado:
- **Cliente API genérico** `whmcs_api(action, params)` en engine/app.py (config `WHMCS_API_URL`
  = `https://panel.hosting.cl/includes/api.php`, `WHMCS_API_IDENTIFIER`, `WHMCS_API_SECRET` en
  engine.env). Inerte si no está configurado.
- **`whmcs_set_ip(serviceid, ip)`** (UpdateClientProduct) cableado en `flujo_crear` justo tras
  asignar la pública/NAT → **rellena la IP en la ficha AL INSTANTE** para creaciones con
  `whmcs_serviceid`. Ya no hace falta "Sincronizar" (que queda como respaldo manual).
- **Credential API** creada por el usuario (rol "Motor VPS callback" con UpdateClientProduct +
  GetClientsProducts; SendEmail no aparecía en su versión → queda para el correo de bienvenida).
- **403 "Invalid IP 192.168.122.252":** la API de WHMCS tiene **allowlist de IPs**. El motor sale
  con `192.168.122.252` (noc-monitor ens192) hacia panel.hosting.cl (201.148.105.100). El usuario
  agregó esa IP en Setup → General Settings → Security → **API IP Access Restriction** (SIN borrar
  las existentes de la web/otras integraciones). Test OK: `result: success` (34.156 servicios en
  ese WHMCS — negocio grande, go-live cuidadoso). **Próximo:** correo de bienvenida con IP (via
  SendEmail) cuando se defina el permiso. Nota UX: el paso a paso del dashboard ahora sale en la
  pestaña Jobs (2 columnas) y al crear desde el tab Crear salta a Jobs; TZ del contenedor corregida.

## 2026-09-14 (domingo, cierre) — Flujo web→WHMCS mapeado + checklist de go-live a producción

Cerrando el día, se investigó (sin preguntar a Cristian, todo self-service) **cómo entra una
orden real** desde la web hosting.cl:
- El botón "Contratar" de la web apunta a **`www.hosting.cl/contratar?pid=335&cycle=monthly`**
  (checkout PROPIO de la web, no el carrito nativo de WHMCS). Lleva el **pid del producto
  WHMCS** (335=VPS Estandar, 336=Empresas, 338=Cyber Black) y el precio ($99.900). 
- Confirmado por la API: la credencial **"nuevas contrataciones"** (Manage API Credentials)
  tenía **Last Access hace ~17 min** — o sea la web **crea las órdenes por la API de WHMCS**
  (opción B1: checkout propio → WHMCS API). Ese WHMCS tiene MUCHAS integraciones API activas
  (triage IA de tickets, notificación de pago, análisis de tráfico, dominios, etc.).
- **Flujo completo:** web `/contratar` (pid) → API "nuevas contrataciones" → WHMCS crea
  cliente+orden+servicio (producto 335) → pago → CreateAccount (nuestro módulo) → motor. La
  cadena **web pid ↔ WHMCS producto ↔ sabor** cuadra 1:1. **Para el motor no cambia nada** —
  se cuelga de la activación tras el pago, sin importar cómo se creó la orden.
- Distinción aclarada al usuario: **área de cliente** (portal del cliente: ordenar/pagar/ver,
  NO botones de módulo) vs **área admin** (staff, con Create/Suspend/Terminate). El checkout
  crea **cuenta+orden+servicio**. Nuestro panel #4 se ve al entrar el cliente a su servicio.

**CHECKLIST DE GO-LIVE agregado al tablero** (sección "Go-live: pasar los productos REALES a
la automatización"): 1) automatizar licencia cPanel (#5), 2) quitar el límite de IPs de prueba
(.100-.102 → pool completo Red57-0, config del motor), 3) definir clientes actuales (quedan
manuales; solo nuevos se automatizan, o migrarlos registrándolos en el motor), 4) cambiar el
módulo en cada producto real (SOLO pestaña Module Settings → Motor Vps + grupo Motor VPS +
sabor + Instalar cPanel; Details y Pricing NO se tocan), 5) Auto Setup manual → 1 orden real
de prueba → luego "al pagar". El usuario ABRIÓ el VPS Estandar 335 real, llenó la config
correcta y le dio **Cancel Changes** (no se tocó producción). Auto Setup "al recibir el primer
pago" = nunca crea sin pago confirmado.

**Estado para mañana:** #1-#4 hechos y validados (incl. cPanel + WHM login con la clave de la
ficha). Falta **#5 (licencia cPanel)** — pendiente que el usuario diga cómo asigna la licencia
compartida (Manage2 API / portal / addon). Tablero al 78% global, Fase 3 al 55%.

**Limpieza + fix de reloj (2026-09-15):** el usuario eliminó vps-hcl-0010 (liberó la .102).
Se detectó que los timestamps de los jobs salían 3h adelantados: el **contenedor vps-engine
estaba en UTC** (sin TZ). Se agregó al quadlet `/etc/containers/systemd/vps-engine.container`
**`Environment=TZ=America/Santiago`** + `Volume=/etc/localtime:/etc/localtime:ro` y se reinició
(daemon-reload). Verificado: contenedor ahora en hora de Chile (-03). Los jobs nuevos salen en
hora local; los viejos quedan en UTC (ya grabados). (Complementa el arreglo de relojes previo.)

## 2026-09-14 (domingo) — 🔑 2ª prueba con cPanel: login a WHM con la clave de la ficha (Feature #1 validada E2E)

El usuario creó desde WHMCS un producto con **"Instalar cPanel" marcado** (PRUEBA VPS
Estándar cPanel, duplicado del Estándar) y disparó Create → **vps-hcl-0010-alcadio**
(clona la dorada `-cpanel`). Verificado por SSH: job **ok**, **cPanel 138 instalado**, **WHM
responde 200**, **root con contraseña aplicada** (`passwd -S root → PS`, la que WHMCS puso en
la ficha). **El usuario abrió `https://38.19.57.102:2087` e inició sesión en WHM con `root` +
la clave de la ficha** — llegó al initial setup wizard. **Sin licencia** (no la bloquea el
login; el #5 la agregará después). Con esto la **Feature #1 queda validada E2E**: el cliente
recibe su clave de root lista para WHM, sin `passwd` manual. También optimizado antes: endpoint
liviano `/vm-por-servicio` (solo BD) → CreateAccount vuelve en ~12s y "Sincronizar datos" es
instantáneo (antes /vms recalculaba power-state por VM y tardaba). Los 5 puntos: #1-#4 hechos
y probados; **#5 licencia cPanel = lo último** (falta que el usuario diga cómo asigna la
licencia compartida). Prueba de creación con cPanel = OK; queda vps-hcl-0010 activo en .102.

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
