# Bitácora — Vps

> Registro cronológico para retomar con contexto. Más reciente arriba.
> Lee primero [README.md](README.md) y [SEGURIDAD.md](SEGURIDAD.md).

---

## 2026-10-01 (jueves) — 🔑 CLAVE DE ROOT PARA WHM + VPS gestionados en dos columnas

Pregunta de Alcadio: *"cuando yo creo el vps desde el dashboard un vps con cPanel, ¿qué contraseña
root utiliza para el acceso al cPanel? En WHMCS usamos la clave de la ficha, pero aquí no"*.

**El hueco:** un VPS creado desde WHMCS recibe la clave de root de la ficha del cliente (el módulo
la manda y el motor la aplica con `chpasswd` por SSH). Creado desde el **dashboard** no se mandaba
ninguna → el VPS quedaba **sin forma de entrar a WHM**. Recordatorio de Alcadio: *"la clave root es
solo para el acceso al cPanel"* — y así es: **el SSH sigue siendo solo con llave**; esa clave sirve
para WHM/cPanel por navegador y para la consola de VMware, nunca para login SSH.

**Qué se construyó:**
- `password_root_auto()`: 20 caracteres de un alfabeto de 66 símbolos (~121 bits) **sin `:`** (es el
  delimitador de `chpasswd`), sin saltos de línea y **sin caracteres ambiguos** (l, I, O, 0, 1) —
  la clave se lee y se dicta por teléfono. Viaja solo por **stdin**, nunca interpolada en un comando.
- Se genera **solo si se pidió cPanel y no vino clave**; una clave suministrada (WHMCS) se conserva
  **exacta** y nunca se devuelve en el resultado (el cliente ya la tiene en su ficha).
- **Solo se entrega si se CONFIRMÓ su aplicación** (bloqueante que levantó Codex): si `chpasswd`
  devuelve error o se agota el plazo, el job NO muestra clave sino el aviso *"no se pudo confirmar
  la aplicación — define una a mano"*. Mostrar una clave que quizá no quedó puesta es peor que no
  mostrar ninguna.
- La clave viaja en `jobs.resultado`, visible **solo al NOC** (`/job` ya borra `resultado` entero
  para los demás roles) y **se redacta al vencer el TTL** por las dos rutas (lectura y barrido de
  mantención), igual que `send_url`/`send_password`.
- El panel verde de acceso la muestra con botón de copiar y la etiqueta de que es **solo para WHM**.
- Para poder probar el camino de fallo sin SSH real, la lógica quedó en dos funciones:
  `aplicar_root_password` (aplica y dice si se confirmó) y `entregar_root_password` (decide qué va
  al resultado).

**Codex:** aprobó en 2 rondas. R1 favorable con un bloqueante — *"distinguir «generada» de
«aplicada»: hoy puedes entregar una clave que no funciona"* → corregido. R2 **APROBADO**, con dos
notas: generar para cualquier rol (no acotar a admin, es condición del servicio y no del rol —
aceptado) y que el camino de fallo **sí** era testeable sin SSH → por eso la extracción de helpers.
Tests nuevos en `test_rootpw.py` (11 asserts): generador, generación/no-generación por combinación
de rol y cPanel, conservación exacta de la clave ajena, rechazo explícito y timeout, ocultamiento a
WHMCS y expiración por ambas rutas. **18 suites verdes.**

**PENDIENTE operativo:** crear un VPS con cPanel en staging y comprobar que **WHM acepta** la clave
generada (política de contraseñas de cPanel) — es lo único que Codex dejó fuera de su aprobación.

**UI (pedidos de Alcadio en la misma sesión):**
- **VPS gestionados en DOS columnas**, igual que Jobs: *Mis VPS* (creados a mano desde el dashboard)
  y *Clientes (automático · WHMCS)*. El criterio es `whmcs_serviceid`: lo trae siempre la creación
  por WHMCS (es obligatorio para ese token) y nunca la manual. Cada caja tiene su scroll propio; la
  tabla se compactó (cliente y fecha bajo el nombre, acciones como íconos con tooltip) para caber en
  media pantalla. No hizo falta tocar el motor: `/vms` ya devolvía la columna.
- Las cajas de Jobs y de VPS se **extendieron hacia abajo** (`calc(100vh - 290px)` en vez de 380px).

**Desplegado:** motor (`app.py.bak-rootpw-20261001`, build + restart, health 200) y dashboard
(`dashboard.py.bak-vmscols-20261001`, md5 verificado, servicio `dashboard` activo, 200). Antes de
subir se hizo **diff contra el archivo del servidor**: 5 hunks, todos míos — la regla que quedó del
incidente de ayer.

---

## 2026-10-01 (jueves) — 🐧 MOTOR MULTI-SO DESPLEGADO: ya se puede ofrecer Ubuntu

Paso 2 del pedido de Alcadio (tras construir la dorada): el motor ahora **elige sistema operativo**.
APROBADO por Codex en 3 rondas y desplegado.

**Qué se construyó:**
- Catálogo `SISTEMAS` (SO → dorada física + **familia** + si admite cPanel), con **guarda de
  coherencia al arrancar**: si `DORADA` apuntara a una plantilla de otro SO, el motor NO arranca
  (evita mandarle red de NetworkManager a un Ubuntu y dejarlo sin red).
- **Red POR FAMILIA** (lo crítico): `rhel` → nmcli (como siempre); `debian` → **netplan**, con
  `dhcp4/dhcp6: false` explícitos (netplan FUSIONA mapas: un dhcp4 previo sobreviviría),
  `renderer: networkd`, misma clave de interfaz que el metadata (`nic0`, para que fusione en vez
  de competir) y la cadena `rm 50-cloud-init.yaml; netplan generate; netplan apply; touch marcador`
  con `sh -ec` (el marcador solo se escribe si TODO salió bien). El metadata usa `routes:` en
  debian (`gateway4` está deprecado en netplan moderno).
- El SO lo define el **plan** (`so_default`, campo que ya existía y se ignoraba); el **NOC** puede
  forzar otro al crear; **WHMCS no** (vende catálogo) → 403.
- **cPanel + SO incompatible se rechaza ANTES de crear** (en el endpoint y también en el worker).
- Pre-chequeo de que la **plantilla exista en ese host** antes de tocar el ESXi (valida la que
  REALMENTE se clonará: con cPanel acepta `-cpanel` o la base, por el fallback ya auditado).
- `vms.so` registra el SO de cada VM; `/health` expone el catálogo y el dashboard tiene **selector
  de SO** que se preselecciona con el del plan y avisa si se combina cPanel con un SO que no lo soporta.
- **Formulario de Crear VPS REDISEÑADO** (pedido de Alcadio: "que se vea más profesional" y
  luego "sigue ese diseño" señalando el panel de DDoS): **tres tarjetas separadas** con esquinas
  redondeadas (14px) y cabecera en MAYÚSCULAS con ícono — **QUÉ SE CREA** (marca/plan/SO/plantilla
  + specs del Personalizado en recuadro propio), **PARA QUIÉN** (cliente/hostname/llave SSH) y
  **DÓNDE SE CREA** (host/entorno) — todo **centrado** (máx 1080px), las dos primeras lado a lado,
  controles `-sm` parejos y botones centrados al pie. Los nombres de las secciones los validó
  Alcadio ("está muy bien eso de qué se crea / para quién").
- **Pestaña Jobs rediseñada (pedido de Alcadio):** el paso a paso ya NO se muestra en un panel al
  pie (obligaba a bajar) sino en una **ventana flotante** centrada: se abre sola al lanzar una
  creación (tras cambiar a la pestaña Jobs) y al pulsar "Ver pasos"; se cierra con el botón,
  Escape o clic fuera, y al cerrarla se detiene el sondeo y se refresca la lista para abrir otro
  job. NO se auto-abre al detectar un job corriendo (reaparecería cada vez que se cerrara). El
  wizard (preparar host / copiar doradas) usa la misma ventana. El panel verde de acceso al VPS
  (comando SSH + `ssh-keygen -R`) ahora aparece dentro de la ventana.
- **Ventana de pasos afinada:** se quitó el scroll horizontal (la tarjeta interna tenía
  `max-width:860px`, más ancha que el modal, y el nombre del paso iba con `nowrap`); se retiró esa
  tarjeta (redundante dentro de la ventana), el modal pasó a `modal-xl`, la tabla ganó **cabecera
  PASO · DETALLE · HORA**, y al terminar el job se muestra un **resumen**: cuánto **demoró**,
  inicio, fin y número de pasos (helper `vpsengDur`).
- **Jobs en DOS columnas** (antes tres): **Mis jobs** absorbe la caja de *Sistema* —reconciliar,
  purga y enrolamientos también son tareas del NOC— y **Clientes (automático · WHMCS)**.
- ⚠️ **INCIDENTE y lección (2026-10-01): casi rompo el dashboard por anclar un reemplazo a un
  estilo CSS.** Al sustituir el formulario busqué el bloque por `style="max-width:1000px"`, cadena
  que TAMBIÉN existía en la página *Alta de Servicios*: el reemplazo cayó ahí y borró el final de
  esa página y el inicio de la de VPS (incluido `vpsengPane-crear`), dejando la sección en blanco
  (el JS de pestañas moría con null). **Recuperado** desde `dashboard.py.bak-20261001-form` y
  rehecho anclando por el **id único de la sección** y verificando que el bloque a reemplazar
  contuviera el formulario y NADA de otras páginas antes de escribir. **REGLA: en un archivo de
  ~15k líneas, anclar SIEMPRE por id único, nunca por clases/estilos compartidos; y comprobar la
  integridad de las demás páginas tras cada reemplazo.** (El respaldo previo al deploy salvó el día.)
- **Cabeceras ilegibles, corregido:** las armé con estilos en línea y el texto quedó con el color
  del tema claro sobre fondo oscuro. Ahora usan la clase **`card-header`** propia del dashboard
  (la misma del panel DDoS), que el tema oscuro ya colorea — y de paso respeta su radio de 12px.
- **Comando de limpieza de huella SSH en el dashboard:** el panel verde de acceso ahora incluye
  `ssh-keygen -R <ip>` con botón de copiar, explicando que el aviso de "huella cambiada" ocurre
  porque la IP pública de PRUEBAS se recicla entre VPS (a los clientes reales no les pasa).
- **Bug de UI corregido (reportado por Alcadio):** el selector de Host VMware no mostraba los
  hosts al entrar directo a la sección — `vpsengInit()` poblaba marca y planes pero NO llamaba a
  `vpsengHostsSelector()`, que solo corría al hacer clic en la pestaña "Crear VPS"; quedaba el
  `<option>` estático del HTML ("Automático — host activo de mejor prioridad", resto del diseño
  anterior). Ahora se puebla en el init, el placeholder dice "cargando hosts…" y no se restaura
  una selección que ya no exista en la lista.
- **UI afinada con Alcadio:** el selector de SO muestra solo el nombre del sistema; el selector
  de plantilla (cPanel) es **condicional** — con Ubuntu la opción "con cPanel" se deshabilita y se
  fuerza "sin"; y el selector de host ya **no** ofrece "por defecto" (el NOC elige host siempre).
  Se descartó crear un plan `vps-estandar-ubuntu`: con el selector de SO al lado, duplicar planes
  por sistema no aporta (3 planes × N sistemas no escala). **Consecuencia:** hoy WHMCS NO puede
  vender Ubuntu (su token no elige SO, por diseño); cuando se quiera, se agrega una opción de SO
  al módulo PHP (como el checkbox de cPanel) y se sube con el `curl` documentado.

**Hallazgos de Codex corregidos:** KeyError si llegaba un `so` desconocido al worker (→ validador
único para las dos vías); valores falsy (`False`/`0`/`[]`/`{}`) colándose como "sin parámetro";
`so_default` con tipos raros usando el global en silencio; la **compensación no cubría** la
resolución de SO ni la validación de la reserva (→ TODO lo previo al ESXi dentro del try que libera
la reserva); el pre-chequeo validaba la base cuando se clonaría `-cpanel`; y el **marcador
`vps-engine.provisioned` se escribía aunque la red fallara** (→ cadena con `sh -ec`).

17 suites verdes (nueva `test_so.py`). Desplegado: motor + dashboard + el sabor Ubuntu (ojo: los
JSON de sabores viven en el build del servidor, hay que subirlos aparte del app.py). Verificado en
producción: `/health` lista los 2 sistemas y los 4 planes con su SO.

**✅ VALIDADO EN VIVO (2026-10-01, la condición de Codex CUMPLIDA):** Alcadio creó
`vps-hcl-0003-prueba26` (Cyber Black, **Ubuntu 26.04**, modo produccion, esxi-20051) — job
06311c82e7e0, **15/15 pasos ok**. Evidencia:
- Clonó `dorada-ubuntu26.04` y **tomó la IP ESTÁTICA en el PRIMER arranque** ("VM arriba con su IP
  estática 10.100.16.235") — con AlmaLinux siempre reporta una DHCP transitoria primero, así que
  el netplan quedó mejor que el camino nmcli.
- SSH con la llave de gestión OK · NAT 1:1 a 38.19.57.102 + NetBox · cPanel correctamente
  **omitido** (`instalar_cpanel=False`) · llave entregada por Send.
- Estado de red verificado por SSH: `ens192` con 10.100.16.235/24, default `proto static`, DNS
  1.1.1.1/8.8.8.8, **solo `60-vps.yaml` en /etc/netplan** (el 50-cloud-init.yaml NO reaparece) y
  el marcador `vps-engine.provisioned` presente (la cadena `sh -ec` completa salió bien).
- **TRAS UN REINICIO**: vuelve con la MISMA IP, ruta, DNS y un único netplan. Sin DHCP residual.

**Ubuntu queda listo para ofrecer a clientes.** Pendiente menor: la dorada Ubuntu hoy solo está en
**esxi-20051** — copiarla a esxi-245 y esxi-20039 para poder crear Ubuntu también ahí.

## 2026-10-01 (jueves) — 🐧 DORADA UBUNTU 26.04 CONSTRUIDA (VPS sin panel, no depende de cPanel)

Alcadio: "a veces me piden máquinas VPS con Ubuntu 26.04.1" — y es algo que **no depende de la
licencia de cPanel**. Decisiones suyas: **primero la dorada**, y alcance **"VPS limpio sin panel"**.

**Construida en esxi-20051** (`_plantillas/dorada-ubuntu26.04`, 2,3 GB reales, disco 10 GB thin).
Camino MUCHO más corto que el de AlmaLinux: Ubuntu publica **OVA cloud oficial** con cloud-init,
open-vm-tools, growpart y netplan ya dentro → **no se instaló nada y no hizo falta internet**
(la red 192.168.200.0/24 no tiene salida; se comprobó). Procedimiento completo y reproducible
documentado en `dorada/README.md`; semilla cloud-init versionada en `dorada/ubuntu-seed.yaml`.

Hallazgos de la construcción:
- **La API de svc-vps NO puede importar OVAs** ("Permission to perform this operation was denied")
  — el privilegio mínimo funcionando. Se usó la **llave de bootstrap** (root shell) para subir el
  disco y convertirlo con `vmkfstools -i -d thin`.
- La OVA declara **pvscsi + vmxnet3** = el mismo hardware del `VMX_TEMPLATE` del motor → los
  clones arrancarán sin ajustes de hardware.
- **El datasource VMware/guestinfo FUNCIONA en Ubuntu**: la VM leyó la semilla por guestinfo,
  se configuró y **se apagó sola** a los ~2,5 min (señal de sellado correcto). Es el mismo
  mecanismo que usarán los clones. ✅
- El wrapper del motor ya la lista: `list-plantillas` → `dorada-ubuntu26.04`. ✅

**⚠️ PENDIENTE para poder crear VPS Ubuntu (paso 2):** el motor usa `DORADA_DEFAULT` fijo e
inyecta la red con **`nmcli` (NetworkManager)**, que **Ubuntu Server no tiene** (usa netplan /
systemd-networkd) → un clon Ubuntu nacería sin IP y la creación fallaría esperando SSH. Falta:
respetar el `so_default` que YA existe en el esquema de sabores, generar el userdata **según el
SO** (netplan vs nmcli), selector de SO en Crear VPS y opción por producto en WHMCS. Luego:
copiar la dorada a los demás hosts y probar una creación real.

## 2026-10-01 (jueves) — 🎯 GATE DE STAGING SUPERADO en ESXi 8 real: modo llave + multi-datastore

**esxi-20039 (192.168.200.39) enrolado por Alcadio desde el dashboard, en MODO LLAVE y con DOS
DATASTORES** (job 2f81159f35f7, los 5 pasos en verde). Primera prueba real de ambas features:
- Entró por la llave de bootstrap del motor (SIN clave root) y el auto-rebaje de svc-vps quedó
  verificado ("ventana Admin cerrada").
- Instaló wrapper + llave confinada en `Raid10-20039` y `Raid10-20039A`, validó cada uno por
  separado (1925 GB / 1501 GB libres) y registró el host PAUSADO.

**Gate que exigía Codex, CUMPLIDO en sus dos partes (evidencia empírica, no mocks):**
1. **Aislamiento A/B:** `df` del wrapper con cada llave → la primaria reporta
   `/vmfs/volumes/Raid10-20039` y la secundaria `/vmfs/volumes/Raid10-20039A`. Cada llave opera
   SOLO bajo el BASE de su datastore.
2. **Integridad de authorized_keys:** tras DOS reescrituras (una por datastore) sobreviven las 9
   líneas correctas — las 2 llaves de Fabián, `raid-check-automation`, la de diagnóstico de Claude
   y la de bootstrap intactas — más las 2 del motor con su `command=` propio. Permisos 0600, sin
   temporales `.new.` sueltos.

**Incidente previo (2 intentos fallidos, ambos sin tocar el host):** el .39 tenía instalada la
PRIMERA versión de la llave de bootstrap (antes del regenerado a PEM) → el motor la rechazó con
mensaje claro. Corregido por Claude con respaldo (`authorized_keys.bak-20261001-claude`),
reemplazando SOLO esa línea y verificando el conteo antes de publicar. **Lección: la única fuente
fiable de la pública es el botón "Copiar llave del motor" del wizard (la lee del motor).**

Estado de hosts: esxi-245 (pausado, ambos motores), esxi-20051 (pausado, solo admin, datastore1),
esxi-20039 (pausado, solo admin, 2 datastores).

**Módulo WHMCS desactualizado 17 días — detectado y corregido.** Tras la creación, la ficha mostró
`Module Command Error — No se pudo leer el motor (HTTP 403)`. Diagnóstico por los logs del motor:
`GET /vms 403`. Causa: la copia del módulo INSTALADA en el WHMCS era la del 14-09 por la mañana
(llamaba a `/vms`, endpoint solo-admin); ese mismo día lo optimizamos a `/vm-por-servicio`
(commit 5a35c52) y **se publicó en noc-monitor pero nunca se bajó en el WHMCS**. El 403 era
CORRECTO (el token de WHMCS no debe listar los VPS de todos los clientes). La IP sí había llegado
a la ficha porque la escribe el MOTOR, no la lee WHMCS. Alcadio actualizó el módulo con el `curl`
documentado (md5 4d3ce022… verificado) y se confirmó en los logs:
`GET /vm-por-servicio?serviceid=36681 200`. **Lección anotada en docs/whmcs-intervenciones.md:
publicar en noc-monitor NO es instalar en WHMCS — hay que bajarlo allá y verificar md5.**

**MOTOR DE CLIENTES (WHMCS) VALIDADO CON LA SEPARACIÓN NUEVA.** Alcadio lanzó una creación desde
WHMCS (job bc8cc5f9942d, `vps-hcl-0002-alcadio`, serviceid 36681): quedó etiquetada **motor=clientes**
(caja "Clientes (automático · WHMCS)" del dashboard, separada de "Mis jobs") y eligió **esxi-20051**
porque Alcadio le puso **prioridad 99** frente a los 100 de esxi-245 — confirmando la regla
"menor gana" y, antes de eso, que con empate (100 vs 100) ganaba el más antiguo (esxi-245).
Completó los 15 pasos: pública 38.19.57.102 ↔ 10.100.16.236, **IP enviada a la ficha de WHMCS: ok**,
cPanel 138 con WHM 200 y llave entregada por Send. El conector WHMCS quedó intacto tras los cambios.

**REGRESIÓN DEL FLUJO DE CREACIÓN: IMPECABLE.** Alcadio creó `vps-hcl-0001-prueba23` en
esxi-20051 (job f023390764fc, motor=admin, modo produccion): los **15 pasos en verde** — clon de la
dorada con cPanel, disco 153 GB, CBT, IP privada 10.100.16.237 ↔ pública 38.19.57.102 (NAT 1:1 +
NetBox), SSH con llave de gestión, cPanel 138.0 con WHM 200, llave entregada por Send. Confirma que
los cambios de hoy (dos motores + modo llave + multi-datastore Inc.1) NO alteraron el camino de
creación. Nota esperada: `vms.datastore` queda NULL — poblarlo es parte del Incremento 2; hoy la
creación usa siempre el datastore primario del host. Además, la creación en un host **pausado**
funcionó como estaba diseñado (pausado = solo frena al motor de clientes, no al admin).

## ⏯️ PARA RETOMAR (2026-10-01) — MULTI-DATASTORE: Incremento 1 DESPLEGADO + falta Incremento 2

**Incremento 1 (enrolar con 1..8 datastores): DESPLEGADO 2026-10-01 y APROBADO por Codex (4 rondas) + GATE DE STAGING SUPERADO en ESXi real (ver entrada de arriba).**
16 suites verdes. Validado en vivo con esxi-20039 (2 datastores, aislamiento A/B demostrado).

Qué quedó hecho:
- Esquema: `vms.datastore` + `hosts.datastores` (JSON [{"ds","ssh_key"},…], 1º = primario).
- `ds_key_path(hid, ds, primario)`: primario = nombre histórico (compat); secundarios en namespace
  `vps_engine_dsk_<hid>_<sha256(hid\0ds)[:16]>` (sin colisiones). Creación con O_EXCL.
- `_parse_datastores` / `host_datastores(h, estricto)` / `host_ds_key(h, ds)`: NULL → deriva
  primario; config presente-pero-rota = lectura tolerante + `datastores_error` visible, y ELEGIR
  datastore lanza. Valida nombres con DATASTORE_RE.
- `_preparar_host`: bucle por datastore (árbol VPS/ + wrapper BASE=ese ds + llave con command=);
  publicación de authorized_keys atómica (temporal único, chmod 600, cadena &&, error de lectura
  aborta) + verificación contra la línea COMPLETA (n==1, exactas==1). Diag key una sola vez.
  Validación en vivo: API 1 vez + pong del wrapper de CADA ds. Primario INMUTABLE si hay VMs.
- Endpoint: `datastores_extra` (lista, ≤7 extras, sin duplicados, DATASTORE_RE).
- `host_recursos`: una consulta govc POR datastore (un secundario ausente ya no marca el host
  inalcanzable) con `error` por ds. GET /hosts expone `datastores` y `datastores_error`.
- Dashboard: wizard con "+ agregar otro datastore" (inputs dinámicos) y lista de hosts mostrando
  cada datastore con su espacio libre.

**GATE DE STAGING (condición de Codex, antes de producción, en ESXi 8 real):**
1. la llave del datastore A opera SOLO bajo el BASE de A y NO el de B (y viceversa);
2. un fallo real antes del `mv` conserva `authorized_keys` íntegro (bootstrap + diagnóstico).
Hacerlo en esxi-20051 (staging), NUNCA en .39 (producción de Alcadio).

**Incremento 2 (pendiente):** elegir datastore al CREAR (selector en Crear VPS como el de host);
`flujo_crear` usa `host_ds_key(h, ds)` y clona del `_plantillas` de ESE ds; grabar `vms.datastore`;
eliminar/editar/suspender resuelven la llave por `vms.datastore`; copiar-doradas por ds; cupo y
reconciliación/purga por ds. **Después:** auto-selección por espacio (lo dejó Alcadio para luego).

## ⏯️ (histórico) multi-datastore — fundación

**Estado:** fundación commiteada en `b199127` (NO desplegada; el motor en prod sigue 1-datastore,
intacto). Alcadio pidió construirlo YA. Se continúa posiblemente con otro modelo (Opus).

**Diseño cerrado — "una llave confinada por datastore":** cada datastore usable del host tiene su
propio árbol `VPS/` + wrapper (BASE=ese ds) + llave RSA con `command=` a ese wrapper. El wrapper
auditado NO se toca (se instancia por datastore). svc-vps es UNO por host (API, rol VpsOperator
cubre todos los datastores); SOLO las llaves SSH del wrapper son por-datastore. Las doradas van en
cada datastore (clon lee/escribe dentro del mismo ds → invariante del wrapper intacta).

**Ya hecho (en `b199127`, seguro/aditivo/tolerante a NULL):**
- Esquema: `vms.datastore` (dónde vive cada VM) + `hosts.datastores` (JSON [{"ds","ssh_key"},…],
  1º = primario = hosts.datastore/ssh_key; NULL → deriva del primario).
- Helpers `host_datastores(h)` y `host_ds_key(h, ds)` (engine/app.py ~línea 517).

**Incremento 1 (enrolar con N datastores) — FALTA:**
- `_preparar_host` (~línea 3468): convertir el bloque de instalación wrapper+llave en un BUCLE por
  datastore (sufijo de key `_<ds>` para los no-primarios), construir `ds_meta=[{ds,ssh_key}]`,
  instalar diag key UNA vez. Guardar `datastores`=json en el INSERT y UPDATE de hosts (~3534/3541).
- Endpoint `hosts_preparar` (~línea 3559): aceptar `datastores` (lista de nombres, validar c/u con
  DATASTORE_RE, dedup, tope ~8); `p["datastore"]`=1º, `p["datastores"]`=lista. Mantener compat con
  `datastore` single.
- `host_recursos` (~línea 525): espacio libre POR datastore (hoy solo el primario).
- Dashboard: wizard con "+ agregar datastore" (1 o 2); lista de hosts mostrando sus datastores.
- Tests (test_wizard) + Codex (dosificar) + deploy. El camino de creación SIGUE en el primario
  (sin cambios) → Incremento 1 es seguro de desplegar.

**Incremento 2 (elegir datastore al crear):** selector de datastore en Crear VPS; `flujo_crear`
usa esxi_ssh con la llave del ds elegido (host_ds_key) y clona de ese `_plantillas`; `vms.datastore`
se graba; eliminar/editar/suspender resuelven la llave por `vms.datastore`; copiar-doradas por ds;
UI. **Después:** auto-selección por espacio.

**OJO:** probar en ESXi real (esxi-20051 staging), NUNCA en .39 (producción). Regla de deuda Codex
vigente. Mini-tabla "Copiar doradas" ya está separada en su tarjeta. Modo llave ya desplegado y
corregido (esxcli rotula custom="Custom"); falta que Alcadio lance el E2E de esxi-20051 en modo llave.

---

## 2026-10-01 (jueves) — 💬 DISEÑO CONVERSADO (sin código): placement automático + Jev asesor

Sesión solo de conversación con Alcadio sobre el futuro "que el motor decida solo dónde
crear" (multi-host y multi-datastore con balanceo). Acordado y documentado en
**docs/DISENO-PLACEMENT-AUTOMATICO.md** — leer ese doc al retomar el tema. Lo esencial:
- Placement = **scoring determinista** (filtrar→puntuar→elegir), estrategia **spread**
  (menor % de uso post-creación), prioridad actual queda como desempate. Nada de LLM en
  el camino síncrono de /crear.
- Multi-datastore: el candidato pasa a ser el par (host, datastore); exige doradas en
  cada datastore y presupuesto por datastore (nota #8: compartido ⇒ se suman).
- Despliegue en **modo sombra** primero (el motor registra qué habría elegido, decide
  Alcadio); el host forzado queda como override permanente. NO se cruza a rebalanceo en
  vivo (svMotion) por ahora.
- **Jev = asesor de capacidad periódico** ("estadísticas más lindas" — le encantó a
  Alcadio): informes de proyección de llenado, desbalance y fricciones sobre el
  histórico de host_recursos + jobs. Opina sobre tendencias; nunca decide en caliente.
- Prerrequisito nuevo detectado: **persistir histórico de host_recursos** (hoy el scan
  es en vivo) para que el informe de Jev tenga serie temporal.

## 2026-10-01 (jueves) — 🧪 Gate de staging del modo llave: hallazgo real (esxcli rotula custom="Custom")

Alcadio enroló esxi-20051 por el wizard en modo LLAVE (tras borrarlo y reinstalar la llave de
bootstrap correcta — ver nota abajo). El job falló en la verificación del auto-rebaje:
"svc-vps quedó en rol 'Custom' (se esperaba VpsOperator)". HALLAZGO que solo un ESXi real expone
(ni mocks ni Codex lo sabían): **esxcli `system permission list` rotula TODOS los roles
personalizados como "Custom"**, no da el nombre. El auto-rebaje SÍ funcionó (svc-vps perdió Admin y
quedó en el rol custom); solo la verificación esperaba el literal "VpsOperator". La recuperación
actuó bien (dejó svc-vps en NoAccess al fallar). FIX: la verificación acepta "Custom" (el
`permissions.set VpsOperator` con rc=0 justo antes garantiza que ese Custom es VpsOperator; se
rechazan Admin/ReadOnly/NoAccess/ausente → ventana Admin cerrada). Mock del test ajustado a la
salida real. 16/16 verde. Motor redesplegado (health 200). **El gate de staging cumplió su función.**
Pendiente: re-correr el enrolamiento (debe salir ok) y Codex del diff menor (dosificación).

Nota llave de bootstrap: Alcadio había instalado la PRIMERA versión de la llave (antes del
reemplazo a PEM); se corrigió en esxi-20051 (reemplazada la línea por la pública definitiva) y se
VALIDÓ que el motor entra (BOOTSTRAP_OK). Para hosts nuevos usar siempre la pública definitiva
(`...AAACAQC1LwUb...`) o el botón "Copiar llave del motor" del wizard. Se purgaron 3 VPS de prueba
en papelera de esxi-20051 (carpetas + filas) y se borró el host del registro para el enrolamiento
limpio. (2 Sends de bóveda de prueba quedan y expiran solos.)

## 2026-10-01 (jueves) — 🔑 WIZARD "root por llave": enrolar sin clave root + id auto-derivado

Pedido de Alcadio: enrolar desde el wizard SIN teclear la clave root, usando una llave pre-instalada
(sus hosts nuevos "no tienen clave root" fija). Punto técnico clave: la API de vSphere NO acepta
llaves SSH (solo usuario+password), así que una llave sola no basta para crear el rol mínimo.

**Solución (aprobada por Alcadio vía AskUserQuestion — auto-rebaje + dejar la llave):**
- **Llave de bootstrap del motor**: pareja RSA generada en noc-monitor (`/data/enrolamiento/keys/
  bootstrap_root`, 600, PEM). La PÚBLICA se instala en `/etc/ssh/keys-root/authorized_keys` de cada
  host nuevo (endpoint GET /hosts/bootstrap-pubkey + botón "Copiar llave del motor" en el wizard).
  Generación atómica a prueba de multi-proceso (tmp+fsync+os.link, no sobrescribe).
- **Modo `auth_modo=llave`** en /hosts/preparar: el motor entra como root por la llave de bootstrap;
  svc-vps se crea por **esxcli** (SIN shell, -s false), se le da **Admin TEMPORAL**, y con su propia
  sesión de API crea el rol mínimo y se **AUTO-REBAJA** a VpsOperator. Verificación POSITIVA por CSV
  (svc-vps presente, no-grupo, Role==VpsOperator exacto; vacío/ambiguo/múltiple→aborta). Ante
  cualquier fallo tras conceder Admin, **recuperación a NoAccess verificada**; si falla, ALERTA de
  "posible Admin residual" (no se oculta). admin_concedido se marca ANTES del comando (cubre
  "aplicado pero respuesta perdida").
- Modo password sigue igual. El checkbox de mi llave de diagnóstico sigue opcional.
- **Dashboard**: toggle "clave root / llave del motor" en el wizard (oculta el campo de clave en
  modo llave y muestra la pública a copiar); **id del host AUTO-DERIVADO de la IP** (readonly:
  192.168.200.39 → esxi-20039) para estandarizar y que el admin no invente nombres.

**Codex**: 4 rondas (código sensible). Hallazgos corregidos: validación de entero del personalizado
(ya venía), idempotencia de llave por awk→CSV, recuperación de Admin, redacción-antes-de-truncar,
not-found acotado a role.ls (ambas ramas), account add/set determinista (listar primero), carrera
de la llave entre procesos (os.link), admin_concedido antes del comando. **GLOBAL APROBADO** en
revisión estática. Tests: test_wizard amplía modo llave (sin root_password; API como svc-vps;
esxcli; CSV; auth_modo inválido→400); 16/16 verde. DESPLEGADO (motor+dashboard, health 200;
bootstrap-pubkey verificado).

**GATE PENDIENTE antes de usar en producción (lo exige Codex, es operativo no de código):** E2E en
un ESXi 8 de staging (esxi-20051) que demuestre auto-rebaje, alcance del permiso de svc-vps y
comportamiento de sesiones. Mocks/--help no lo acreditan. NO se tocó el host .39 de producción.
Nota: en .39 verifiqué solo-lectura (datastores: datastore1 95GB + Raid10-20039 3.5TB + Raid10-20039A
3.6TB; ESXi 8.0.3) — mi llave personal ya está en su root.

## 2026-10-01 (jueves) — 🧩 DOS MOTORES: creación de clientes (autónoma) vs admin (manual), separadas

Pedido de Alcadio: reestructurar el dashboard para tener DOS motores independientes sobre un
mismo registro de hosts — **clientes** (100% autónomo, WHMCS al pagar) y **admin** (creación
manual de VPS personalizados desde el dashboard, sin entrar a VMware). Adoptar un host para un
motor NO lo mete en el otro; un host puede estar en ambos (decisiones del usuario vía AskUserQuestion).

**Backend (engine/app.py):**
- `hosts` gana `uso_clientes`/`uso_admin` (ADD COLUMN DEFAULT 1 → los 2 hosts ya enrolados quedan
  en AMBOS motores, sin romper comportamiento). `jobs` gana `motor` (clientes|admin|sistema).
- `host_principal(motor)`: clientes = activo + uso_clientes + prioridad; admin = uso_admin (el
  'pausado' ya NO frena al admin, es concepto del pool de clientes). `/crear` elige pool por rol
  (whmcs→clientes, admin→admin) y valida uso_admin en host explícito. Jobs etiquetados por origen;
  mantención (reconciliar/purga/enrolar/doradas) → sistema (clasificador por tipo).
- Wizard pide el/los motor(es) al enrolar (≥1); re-preparar NO toca los usos. POST/PATCH aceptan
  los flags; guard "al menos un motor" con re-validación ATÓMICA bajo DB_LOCK (cierra una carrera
  de dos PATCH concurrentes que podían dejar (0,0) — hallazgo de Codex).

**Frontend (dashboard):** pestaña Motor con la lista dividida en 2 secciones (clientes/admin) y
chips para mover un host entre motores; pestaña Jobs en 3 grupos (Clientes/Mis jobs/Sistema);
selector de Crear muestra solo hosts del motor admin; wizard con checkboxes de motor.

**Codex:** revisó backend + lógica, 2 rondas → GLOBAL APROBADO (único bloqueante: la carrera del
PATCH, corregida y probada con un test de hilos+barrera). **Deuda: ya revisado, no suma.**
Tests: nueva suite test_motores (migración, filtrado por uso, etiqueta de job, guard atómico,
carrera real con hilos); test_multihost_a2 y test_wizard actualizados al contrato nuevo; 16/16 verde.
DESPLEGADO (motor + dashboard, respaldos, md5 verificado, health 200); migración confirmada en
prod (esxi-245 y esxi-20051 en ambos motores; jobs viejos → sistema).

## 2026-10-01 (jueves) — ✅ DEUDA CODEX SALDADA 8/8: "VPS personalizado" + 2 menores APROBADOS

Último lote de la deuda, revisado por Codex en 2 rondas (lote único por dosificación de cuota):
- **#1 VPS personalizado** (sabor con specs explícitas, solo admin, cupo #8 aplica): Codex r1
  RECHAZÓ la validación — `int()` coercía `True`→1, `24.9`→24 y un `1e400` daba OverflowError/500.
  Reemplazado por `_spec()` que exige entero JSON real (rechaza bool/float/string antes del rango).
  r2 APROBADO. Confirmado que `/editar` sobre filas personalizado funciona (lee specs de la fila,
  destino del catálogo) y que no hay `SABORES[...]` que reviente con esas filas. "Editar hacia
  personalizado" bloqueado = límite de diseño aceptable.
- **#2 Checkbox llave de Claude**: Codex r1 RECHAZÓ la idempotencia — `grep` del prefijo base64 en
  cualquier parte del archivo daba falso "ya instalada" (línea comentada o con command=).
  Cambiado a `awk '$1=="ssh-rsa" && $2==<llave completa>'` (exige entrada de shell pleno efectiva).
  r2 APROBADO. El proxy del dashboard ya cerraba el vector de "llave arbitraria" (browser manda
  booleano, servidor pone la llave).
- **#3 Pre-chequeo de portgroup + error de clave legible**: r1 APROBADO. Codex dejó una NOTA que
  resultó un bug real: un aborto en los pre-chequeos (datastore o el nuevo portgroup) dejaba la
  fila 'creando' con IP y cupo colgados — run_job solo marca el job error y la reconciliación solo
  ALERTA. Agregada COMPENSACIÓN: try/except en los pasos 2-3b (todo lo previo a tocar el ESXi) que
  borra la fila reservada (libera IP y cupo) y re-lanza. Verificado que las funciones de IP solo
  leen (no reservan estado externo). r2 APROBADO.
Tests: test_custom ampliado (bool/float/string/overflow + compensación con aborto forzado);
regresión completa 15/15 verde. Motor desplegado (health 200); el dashboard del checkbox ya iba
desde el 29-09. **Con esto la deuda de revisión Codex queda 8/8 — sin pendientes abiertos.**

## 2026-09-29 (martes) — 🏆 PRIMERA CREACIÓN COMPLETA EN esxi-20051 (modo produccion, 15/15 pasos)

Tras crear Alcadio el portgroup `Vps_Hosting.cl` (VLAN 81, VSwitch1/vmnic1) el retry salió
PERFECTO: `vps-hcl-0019-prueba21` (Cyber Black) — clon -cpanel, disco 153 GB, CBT, IP privada
10.100.16.239, NAT 1:1 con la pública 38.19.57.102, NetBox ambas IPs, cPanel 138 PREINSTALADO
con licencia a su IP, WHM 200, llave entregada por Send. Job 122ec7e95012, ~5 min. La VLAN 81
CONFIRMADA tagged hasta el host nuevo. **esxi-20051 queda validado para producción real** (la
cadena completa wizard→enrolar→crear funciona en un host levantado de cero). El pre-chequeo de
portgroup (paso 3b) pasó su primera ejecución real encontrando la red y siguiendo de largo.

## 2026-09-29 (martes) — 🧪 1ª creación en esxi-20051: falló por portgroup ausente → pre-chequeo nuevo + VLAN 81 al host

Alcadio lanzó la primera creación en esxi-20051 (`vps-hcl-0018-prueba20`, Cyber Black, **modo
produccion**). El motor hizo todo bien hasta encender, pero la VM pedía el portgroup
`Vps_Hosting.cl` (VLAN 81) que NO existía en el host → ESXi la encendió con la NIC muerta y el
job murió a los 525 s esperando SSH (con reinicio automático y error honesto — los fixes Medios
en acción). Diagnóstico con la llave diag: el host solo tenía "VM Network"/"Management Network".
**Acciones:**
- VM revertida por el flujo estándar (eliminar → papelera 20260929-204428, NAT/IP liberados).
- Alcadio creó el portgroup `Vps_Hosting.cl` VLAN 81 en **VSwitch1** (uplink vmnic1, link 1G) —
  la interfaz dedicada al tráfico de clientes, separada de vSwitch0 (administración), igual que
  producción. Falta confirmar que la boca del switch traiga la VLAN 81 tagged (lo dirá el retry).
- **Mejora desplegada (fail-fast):** paso 3b en flujo_crear — `govc ls network` en el host
  destino; si el portgroup del modo no existe, aborta ANTES de clonar con mensaje claro y la
  lista de redes del host. Suites saga/cupo/wizard/medios verdes; motor redesplegado (health 200).
  Anotado como deuda menor de Codex (cambio chico, dosificación).

## 2026-09-29 (martes) — 📊 Tablero de avances y runbook actualizados con el enrolamiento estandarizado

A pedido de Alcadio, el flujo de enrolamiento quedó reflejado en los "Avances del proyecto"
(`/vps` → docs/tablero.html, publicado en nginx con respaldo): nota "Ahora mismo" con el hito,
ítems nuevos en Fase 3 (multi-host ✔ + wizard ✔, 55%→70%, global 78%→83%), "Host de pruebas —
pedir a Fabián" marcado RESUELTO con esxi-20051, esquema multi-host, y el checklist técnico
anotado como "lo hace el wizard". RUNBOOK-ENROLAR-HOST.md ahora abre declarando el wizard como
CAMINO OFICIAL (el script queda de plan B/auditoría). Ajustes del mismo día tras las pruebas de
Alcadio: error humano cuando el host rechaza la password ("el host RECHAZÓ la password de root…")
y el mensaje de la llave diag anexado al cierre del paso 3 (detalle() era sobrescrito) — commit
5d1e130, motor redesplegado.

## 2026-09-29 (martes) — ✅ E2E del wizard VALIDADO por Alcadio + checkbox de llave de Claude + tarjeta manual retirada

**E2E real del wizard (Alcadio):** se borró esxi-20051 del motor y Alcadio lo re-enroló
COMPLETO desde el dashboard: huella cotejada → "Preparar y enrolar" → job 5b088117c2b9 ok
en 4 s (rol reconciliado, svc-vps rotado, wrapper, huella pinneada, secreto al almacén,
749 GB validados) → doradas intactas en datastore1 (verificado) → ACTIVADO. La clave de
root no aparece en ningún registro. Primera validación de punta a punta por el usuario.

**Checkbox "llave de Claude" (pedido de Alcadio):** el wizard ahora ofrece un tilde para
instalar la llave pública de diagnóstico del VPS de IA (root, shell pleno) en el host que
se enrola. El navegador solo manda un booleano `agregar_llave_claude`; la llave la inyecta
el PROXY del dashboard (constante CLAUDE_DIAG_PUBKEY — el browser nunca elige la llave), y
el motor ya la validaba (formato ssh-rsa estricto, instalación idempotente). El job ahora
registra "llave de diagnóstico (Claude) INSTALADA / ya estaba instalada" (commit aee6e7b).

**Tarjeta "Enrolar host ya preparado (manual/avanzado)" RETIRADA del dashboard** (decisión
de Alcadio: el wizard cubre todo). El endpoint POST /hosts del motor sigue vivo (token +
permiso) por si algún día hace falta via API; la tarjeta queda en git para restaurarla.

**Deploy (OK de Alcadio):** motor (build+restart, /health 200) y dashboard (respaldo
dashboard.py.bak-20260929-llavediag, md5 verificado, build v2, restart, 200). Deuda de
revisión: cambio chico anotado como pendiente menor para Codex (dosificación).

## 2026-09-29 (martes) — 🚀 DEPLOY de los fixes Medios #18/#19/#22 a noc-monitor (con OK de Alcadio)

Con el "Ok dale" de Alcadio se desplegó el motor con los fixes de Medios aprobados por Codex
(commits ddde9d9 + 754844e, repo en 357452c):
- Respaldo del app.py previo en el server: `app.py.bak-medios-20260929`.
- scp de engine/app.py (md5 verificado igual local/server: 73eaada2…), `podman build -q`,
  `systemctl restart vps-engine` → activo, `/health` 200 `{"ok":true}`, y el dashboard
  consultando /hosts y /jobs con 200 en los logs.
Con esto el motor en producción incluye TODO lo aprobado: wizard Fase D + Medios #16-#23.
La deuda Codex queda 7/8 (solo falta "VPS personalizado", retenido por dosificación de cuota).
**Siguiente:** E2E del wizard por Alcadio (re-preparar esxi-20051 desde el dashboard) y su
primera creación de VPS en el host nuevo — ambas correrán ya con el motor completo.

## 2026-09-29 (martes) — ✅ Medios #16-#23 APROBADO COMPLETO por Codex (trabajo autónomo)

Mientras Alcadio estaba fuera (loop autónomo), se corrigieron los 3 bloqueantes de Medios
documentados el 28 y Codex los aprobó en 2 rondas más (3 en total):
- **#18 chpasswd**: ya no hay éxito falso (condición no cumplida = excepción; el caller la
  convierte en aviso), ejecución con plazo total y streams drenados reutilizando el _ssh_exec
  endurecido del wizard, el error nunca incluye stderr remoto crudo (podría ecoar root:<clave>),
  y una clave con 
 se rechaza (inyectaría otra línea al protocolo). Ante timeout la redacción
  es honesta: "no se pudo CONFIRMAR" (pudo aplicarse sin alcanzar a confirmar).
- **#19 growfs**: contrato nuevo — el script del guest SIEMPRE sale 0 y reporta UNA línea
  (FS_OK a b | FS_SKIP razón | FS_ERR razón); fstype y disco-sin-partición se chequean ANTES
  de tocar nada; parse ESTRICTO en Python (fullmatch) y "VERIFICADO" ahora compara contra el
  UMBRAL DEL PLAN (85% del objetivo en GiB) — un growpart NOCHANGE bloqueado por otra partición
  ya no se anuncia como éxito. Probado contra bash real con binarios fake (5 escenarios).
- **#22 address-list**: tokenizador _terse_props que parsea la línea terse respetando
  comillas/escapes (un comment= dentro del valor de OTRA propiedad ya no cuenta), se examinan
  TODAS las entradas (duplicado habilitado alerta), y el comment debe tener VALOR.
Tests: test_medios y test_reconciliar actualizados al contrato nuevo (incl. mock de entrada
duplicada y 4 casos del tokenizador); regresión verde. Commits ddde9d9 + 754844e.
**PENDIENTE: deploy a noc-monitor con OK del usuario.**
Además: verificado que la mantención de las 04:30 de HOY corrió con el vps-mantencion.sh
NUEVO y terminó ok (purga + reconciliación) — primera ejecución real validada.
Nota #20: ya estaba CERRADO por los rediseños #12/#13 (registrado en la bitácora del 18-09).

## 2026-09-29 (martes) — ✅ WIZARD DE ENROLAMIENTO (Fase D) APROBADO por Codex (5 rondas)

Alcadio priorizó estandarizar el enrolamiento COMPLETO desde el dashboard. Construido y
revisado ANTES de desplegar (el código más sensible del sistema: maneja la root del ESXi):

**Qué es:** pestaña Motor → "Preparar host nuevo (wizard)": formulario (id/ip/datastore/
password de root) → obtener huella SSH → humano la confirma contra la consola → job visible
que hace TODO (rol VpsOperator verificado/reconciliado + svc-vps rotado + llave RSA generada
por el motor + wrapper confinado + huella pinneada + validación en vivo + registro PAUSADO)
→ botón "Copiar doradas" (streaming vía el motor con publicación en 2 fases) → activar.
Cero terminal. La password de root viaja por loopback, vive solo en memoria del job y una
FRONTERA DE REDACCIÓN garantiza que ninguna excepción persistida la contenga.

**Las 5 rondas de Codex** (hallazgos todos reales, todos corregidos):
- r1: inyección de shell vía datastore (CRÍTICO) → DATASTORE_RE estricta; import tar sin
  confinamiento; rc de pipelines; cuelgues SSH; almacén concurrente; re-preparación rompía
  host operativo; fuga de svc_pass por TimeoutExpired; rol existente sin verificar.
- r2: rc de control DENTRO del staging escribible por el tar (CRÍTICO: symlink .gunzip.rc)
  → spool + control fuera del árbol; hardlinks no detectados; reservas parciales; stderr
  secuencial; plantilla publicada de export fallido.
- r3: pre-scan fail-open → archivos de control con rc y conteos; carreras entre endpoints →
  RESERVA ATÓMICA de identidad (OPS_HOSTS_LOCK, claves id:+ip: compartidas por preparar/
  copiar/PATCH/DELETE/POST); hilo stderr abandonaba en timeout → channel.recv que no abandona.
- r4: tar-bomb (tar válido muy compresible agota el datastore) → PRESUPUESTO DE EXTRACCIÓN
  (suma de tamaños del tvf ANTES de extraer, tope 200G + 50G margen), token uuid hex32.
- r5: APROBADO. Precisiones menores aplicadas (cada $3 numérico en el awk).

**Evidencia empírica en el ESXi real (esxi-20051):** tar adversario (../, absoluto, symlink)
→ busybox tar confina y el wrapper rechaza; tar-bomb (4MB comprimido/1GB declarado) →
rechazado por presupuesto ANTES de extraer un byte.

**Límites aceptados documentados:** GOVC_INSECURE (postura TLS actual del sistema), CSRF del
dashboard (régimen existente), df no reserva espacio ante consumo concurrente (margen 50G),
crecimiento de stderr en tempfiles (emisores = nuestros propios hosts).

Tests: suite test_wizard.py (almacén, inyección, anti-MITM, sin fuga de root en el job,
re-preparación con 4 trampas, copiar-doradas) + REGRESIÓN COMPLETA verde (12 suites).

**DESPLEGADO 2026-09-29 con OK de Alcadio:** wrappers en AMBOS hosts (respaldos
.bak-20260929-1117, sintaxis verificada en cada uno), motor rebuild+restart (health 200,
endpoint /hosts/preparar respondiendo la huella correcta del 20051), dashboard con respaldo
dashboard.py.bak.20260929-1118-wizard (GET / 200, proxies con 401 sin sesión, tarjeta wizard
presente). GOVC_PASSWORD_ESXI_20051 RETIRADA de engine.env (respaldo .bak-wizard) para migrar
esxi-20051 a gestión por wizard — la prueba E2E la hace Alcadio desde el dashboard.
(era: PENDIENTE DEPLOY) — motor (app.py+Containerfile+wrapper template),
wrappers actualizados en AMBOS hosts (245 y 20051), dashboard (proxies + tarjeta wizard).
Tras el deploy: prueba E2E del wizard re-preparando esxi-20051 (está pausado y sin VMs).

## 2026-09-28 (lunes) — 🆕 Host nuevo esxi-20051 + estandarización de enrolamiento

Alcadio levantó un ESXi nuevo (`esxi20051cl.dedicados.cl` = **192.168.200.51**, ESXi 8.0.3,
20c/40t, ~128 GB RAM, datastore `datastore1` ~765 GB libres, sin VMs). Plan: STAGING para
probar el flujo con VPS nuevos + demos a gerencia; si aprueban, pasa a ser el **1er host de
producción** (cambiando discos). Objetivo: **estandarizar el enrolamiento** e incluir mi
acceso de diagnóstico.

- **Conectividad:** la red `.200.x` estaba aislada; Alcadio abrió ruta. Verificado: noc-monitor
  → host (443+22) OK, este VPS → host (22) OK.
- **Mi acceso (Claude):** llave RSA dedicada `~/.ssh/claude_esxi_20051_rsa`, autorizada en
  `/etc/ssh/keys-root/authorized_keys` con shell root pleno. **LECCIÓN: ESXi 8.0 RECHAZA
  ed25519** (`Permission denied` pese a llave correcta); solo aceptó RSA. Confirmado en vivo.
- **Estandarización (queda en DEUDA de Codex):**
  - `enrolar-host.sh`: nuevo bloque 3b opcional (`DIAG_PUBKEY`) que instala la llave de
    diagnóstico (shell pleno, sin command=, validando que sea `ssh-rsa`), reusando el socket
    SSH multiplexado. Header con la regla RSA-only de ESXi 8. `sh -n` OK.
  - Nuevo **`docs/RUNBOOK-ENROLAR-HOST.md`**: procedimiento completo Fase 0→C con el ejemplo
    trabajado de esxi-20051 y troubleshooting.
- **Fase A EJECUTADA** (2026-09-28): Alcadio corrió `enrolar-host.sh esxi-20051 192.168.200.51
  datastore1` en noc-monitor → rol VpsOperator + svc-vps + wrapper (BASE /vmfs/volumes/datastore1/VPS)
  + huella pinneada (SHA256 verificada). Llave del motor: /keys/vps_engine_esxi_esxi_20051.
- **Fase B EJECUTADA** (parcial): GOVC_PASSWORD_ESXI_20051 agregado a engine.env (600 root) +
  restart (health 200); **host ENROLADO vía POST /hosts** (validación en vivo OK: API+pong+datastore),
  quedó **estado=pausado** (staging, sin doradas aún). El motor ve 2 hosts, esxi-20051 alcanzable=true
  (20c, 127GB, 764GB libres, límites heredados 24vcpu/64GB/600GB).
- **Doradas — método validado (2026-09-28):** los dos ESXi NO se alcanzan entre sí (245✗→20051),
  así que la copia va por PUENTE = este VPS de IA (único con shell no confinado en ambos:
  claude_esxi→245, claude_esxi_20051_rsa→20051; las llaves del motor están confinadas al wrapper).
  Streaming byte-exacto: `ssh 245 "tar cf - <dorada> | gzip -1" | ssh 20051 "gzip -dc | tar xf -"`.
  El flat queda thick en destino (tar rellena ceros) → se re-adelgaza con `vmkfstools -K` (punch-zero).
  - `dorada-almalinux9.7` (2.9G): COPIADA (7m45s) + re-thin OK (10G→2.8G). md5 descriptor idéntico.
  - `dorada-almalinux9.7-cpanel` (40G prov/7.1G real): COPIADA (32m47s) + re-thin OK (40G→7.0G).
    md5 descriptor idéntico.
  - `ks-oemdrv.iso` (55KB): COPIADO, md5 idéntico.
  - **DORADAS COMPLETAS Y VERIFICADAS** en datastore1/VPS/_plantillas/ (754 GB libres). Integridad:
    gzip CRC en el stream (exit 0) + md5 de descriptores/iso coincidentes origen↔destino.
- **CONTINÚA MAÑANA (29-09):** **activar** esxi-20051 (despausar vía dashboard/PATCH), definir
  **cupo** del host, y **prueba E2E** (crear/eliminar un VPS de test en el host nuevo para validar
  clone-disk + NAT + SSH). Si OK → demos a gerencia → producción (cambiando discos).
  Ojo: revisar que el motor cree VPS en esxi-20051 (host forzado o prioridad) sin tocar esxi-245.

## 2026-09-28 (lunes) — ✅ Re-validación Codex: vps-mantencion.sh APROBADO (4 rondas)

6º ítem de deuda saldado. Codex fue implacable: 8 hallazgos ronda 1, 5 bloqueantes ronda 2,
1 bloqueante ronda 3, aprobado ronda 4. El script (orquestador del timer diario 04:30 que
lanza purga de papelera + reconciliación) tenía varias rutas de FALSO ÉXITO que anulaban su
propósito (exit code = alerta de systemd). Correcciones:
- **Parseo con jq -ers (no sed)**: un `{"estado":"error","detalle":{"estado":"ok"}}` con sed
  tomaba el 'ok' anidado; ahora jq lee la raíz ('error'). Documento único (length!=1 → error),
  raíz objeto, valores whitelisted; anclas `\A..\z` (no `^/$`, que en Oniguruma matchean línea
  → un "ok\n" pasaba y `$()` se comía el \n). Validadores probados contra jq 1.6 real en el host.
- **Falso-éxito del gate**: /reconciliar con el gate ocupado devuelve el job de OTRA operación
  (una purga ajena) con su job_id; el script lo tomaba como reconciliación ok. Ahora verifica el
  TIPO real (GET /job → jq .tipo) y distingue "otro tipo confirmado" (espera+re-POST) de "tipo
  desconocido por fallo de lectura" (reintenta leer el MISMO job, nunca re-POST → no duplica).
- **rc de curl**: set -e NO protege dentro de `$(...)` usado como condición; cada curl comprueba
  su rc explícito; con 'fail', HTTP≥400 → rc 22. Transferencia incompleta ya no cuenta como 'ok'.
- **Deadlines reales**: reloj monotónico /proc/uptime (inmune a saltos de hora), chequeo antes de
  cada POST y de cada sleep; orden purga→reconciliar garantizado (purga sin terminal confirmado
  ABORTA la secuencia, no reconcilia).
- **Secreto**: token en `--config` de curl (600, mktemp+trap), fuera de argv/entorno; sin log de
  cuerpos; `-q` + `noproxy '*'`; carga endurecida (exactamente 1 ENGINE_TOKEN=, solo CR final).
- **systemd**: `TimeoutStartSec=1200` (oneshot desactiva el timeout por defecto); readiness de
  /health al inicio (cubre Persistent=true tras reboot).

Commit `118ea1e` (código) + doc. **DESPLEGADO** con OK del usuario: /usr/local/bin/vps-mantencion.sh
(md5 6d584b43, respaldo .bak-20260928) + .service con TimeoutStartSec=20min (respaldo .bak),
daemon-reload; sintaxis OK en el host; timer confirmado para mañana 04:30 con la versión revisada.

## 2026-09-28 (lunes) — ✅ Codex REACTIVADO (cuenta empresa) + re-validación multi-host A2 APROBADA

Codex volvió: migrada la cuenta de personal (elalcalmx.cl@gmail.com) a la de EMPRESA
(alcadio@hosting.cl) con codex logout/login; review gate reactivado. Nota: la licencia de
Codex del panel Empresa (1, de Gerardo) es para el Codex web/IDE; el Codex CLI funciona por
OAuth con el plan Business estándar (probado). Pendiente por si se acaban tokens.

**Saldada la 1ª (y mayor) deuda de re-validación: multi-host A2.** Codex la revisó en 4 rondas
y encontró **6 hallazgos reales** que se corrigieron:
- **#3 (ALTO)** `host_de_vm` caía al host PRINCIPAL si una fila no tenía host o apuntaba a uno
  inexistente → un eliminar/editar podía operar el host equivocado. Ahora **falla explícito**.
- **#1 (ALTO)** purga identificaba por nombre global → una copia del mismo nombre en otro host
  podía autorizar purga/borrado de bóveda cruzado. Ahora se identifica por **(host, entrada)**:
  solo purga si la entrada aparece EXACTAMENTE en su host registrado; lo demás = deriva (alerta,
  no toca). La reconciliación conserva la evidencia de deriva toda la corrida (aunque el otro
  host caiga en el 2º barrido).
- **#2 (ALTO)** reconciliación unía inventarios globales → ahora conjuntos de pares (host,nombre);
  deriva de VMs no registradas se calcula POR HOST.
- **#4 (ALTO)** enrolar permitía api_url≠ip (clonar en uno, registrar en otro) → api_url se
  **deriva siempre de la ip** validada.
- **#5/#6 (MEDIO)** validación del CRUD: ip con ipaddress real, tipos no-str rechazados,
  enteros acotados (evita OverflowError/500), prioridad 0 respetada y null rechazado en PATCH.
- Tests nuevos (scratchpad test_a2_hardening.py, 8 escenarios) + 11 suites de regresión verdes.
- ⚠️ **Producción hoy NO estaba expuesta** (1 solo host: los bugs de aislamiento entre hosts y
  del CRUD solo muerden al agregar el 2º) — pero son exactamente los que romperían al enrolar,
  así que el fix debe desplegarse ANTES de sumar el 192.168.200.121.
- Quedan en la deuda: #8, mantención.sh, #9, #14+#15, medios #16-23, personalizado, multi-host
  A1, y B/C. (Ver docs/pendiente-revision-codex.md.)

## 2026-09-28 (lunes) — ✅ Re-validación Codex: #14+#15 (secretos + inputs) APROBADO

5º ítem saldado (3 rondas). Codex halló varios 500 reales y un tema fino de la clave de root:
- **500 por .strip() sobre no-str**: actor, marca, sabor, cliente, hostname, modo, vm, accion,
  pubkey_cliente y root_password ahora validan tipo (via _str_campo / isinstance) → 400 limpio,
  no AttributeError. limpiar_actor tolera no-str → "api".
- **IDs con 
**: match() aceptaba "123
" → cambiado a **fullmatch()** en los 4 usos.
- **root_password se modificaba**: el .strip() alteraba la clave que WHMCS pone en la ficha (el
  cliente no podría entrar) → ELIMINADO; se preserva EXACTA, longitud validada sin recortar.
- **#14 ventana de exposición**: además de la purga, **redacción EN LECTURA** en /job (admin) —
  secretos de jobs > TTL se muestran "(expirado)" aunque la purga no haya corrido (sin mutar BD).
- Tests en test_inputs.py (rondas 1+2+3). Regresión verde.
- **Desplegado** en noc-monitor (commit 9a364e6): build md5 9cc7d468 idéntico dentro del
  contenedor, /health 200, healthcheck podman OK.
- Menor documentado (no migrado): send_expires_at UTC — la expiración se mide desde created_at
  del job; dif. real de minutos (Send se crea segundos después) y a lo sumo 1h en cambios de
  horario, sobre TTL de 2 días.

## 2026-09-28 (lunes) — ✅ Re-validación Codex: #9 (pinning host keys) APROBADO

4º ítem saldado (2 rondas). Codex aprobó el core del pinning; se corrigieron:
- **Verificación de huellas**: known-hosts.sh y enrolar-host.sh ahora MUESTRAN las huellas SHA256
  y EXIGEN confirmación humana contra la consola del equipo antes de instalar (ssh-keyscan confía
  en quien responde → un MITM en el scan pinnearía su huella). Modo CI: KH_CONFIRM=si.
- **mikrotik()**: `-F /dev/null` + `GlobalKnownHostsFile=/dev/null` → confianza EXCLUSIVA a nuestro
  known_hosts (ninguna otra fuente de OpenSSH puede aceptar una huella); y conserva stderr ante
  fallo (ahí SSH informa el rechazo de host key).
- **Scripts robustos**: mv atómico (temp en el mismo FS), preservación de entradas de otros hosts
  (ya no regeneran a ciegas), IPs escapadas en los filtros, ssh-keygen -E sha256.
- **TOFU de VPS**: documentado como límite aceptado (llave efímera + IPs reutilizadas; mitigado
  por los locks de IP #2; la llave es la de gestión).
- Test en test_pinning.py (flags de aislamiento + stderr). Regresión verde.
- Pendiente menor (no bloqueante): verificación automática de huellas en CI, lock entre scripts.

## 2026-09-28 (lunes) — ✅ Re-validación Codex: #8 (cupo + datastore) APROBADO

3er ítem de la deuda saldado (2 rondas). Codex halló un bug REAL en validar_cupo: los
**downgrades podían rechazarse** si el host ya estaba por encima de un límite (posible tras
bajar HOST_MAX_*/max_* con VMs ya creadas) — evaluaba el total aun con delta ≤ 0. Corregido:
**solo se chequea el límite en las dimensiones que AUMENTAN** (delta > 0); downgrades y
sin-cambios siempre pasan. Modelo de capacidad VALIDADO con Codex: el cupo lógico es la barrera
ATÓMICA ENTRE CREACIONES (disco provisionado, bajo NOMBRE_LOCK); el chequeo físico del datastore
es salvaguarda fail-closed. Documentadas las condiciones operativas (límite de disco ≤ capacidad
útil; suma de presupuestos si hosts comparten datastore) y el límite conocido (carrera
editar-crear, pendiente de endurecer). flujo_editar confirma que guarda disco_final (el disco
real, nunca baja). Test nuevo en test_cupo.py (host excedido → downgrade pasa). Regresión verde.

## 2026-09-28 (lunes) — ✅ Re-validación Codex: multi-host A1 APROBADO (5 rondas)

Saldado el 2º ítem de la deuda (la fundación multi-host). Codex encontró y se corrigieron:
- **Adopción de VMs**: corría en cada arranque eligiendo host por prioridad → una fila host=NULL
  se re-adoptaba al host equivocado. Ahora: adopción ÚNICA (marca PRAGMA user_version), al host
  legacy identificado POR IP (no por slug colisionable), en transacción única. Arranques
  posteriores NO reparan NULL (host_de_vm los rechaza, la reconciliación los denuncia).
- **govc(host=)**: (a) regresión TLS que introduje (forzaba GOVC_INSECURE=1) → ahora respeta el
  entorno; (b) heredaba secretos → ahora ALLOWLIST de entorno (solo vars de sistema + GOVC_* del
  host; ni tokens del motor ni passwords de otros hosts llegan al subprocess); (c) exige config
  completa (host={} rechazado); (d) redacta el secreto literal del error (ambas ramas).
- **host_recursos**: timeout corto de scan (15s), comprometido=None ante fallo de BD (no 0
  ficticio), mem_uso 0 válido, límite 0 del host = sin límite.
- Tests: test_multihost.py con 8 escenarios (rondas 1-3 de hallazgos) + 12 suites regresión.
- ⚠️ Sin exposición en producción hoy (1 host); pero son bugs que morderían al enrolar el 2º.
  **A desplegar junto con A2** (mismo commit).

## 2026-09-25 (jueves) — 🧹 Limpieza: registros huérfanos de las doradas quitados del inventario ESXi

En el inventario del ESXi aparecían `dorada-almalinux9.7` y `dorada-almalinux9.7-cpanel`
como VMs *(huérfanas)* — registros muertos de cuando las plantillas se movieron a
`VPS/_plantillas/`. Alcadio las quitó con **"Quitar del inventario"** (unregister, NUNCA
delete-from-disk). Verificado post-limpieza: ambas plantillas **intactas y completas** en
`/vmfs/volumes/DiscoA37245/VPS/_plantillas/` (vmx + vmdk + flat + vmsd), inventario en 10
VMs sin fantasmas, papelera normal. El motor clona igual que siempre. (Contexto del día:
migraciones UniFi y rsyslogmk a noc-monitor — ver ServerVmware/BITACORA.md.)

---

## 2026-09-21 (lunes) — 🧰 MULTI-HOST Fase C: script enrolar-host LISTO (instalado, sin correr)

**`/usr/local/bin/vps-enrolar-host.sh`** en noc-monitor (repo: noc-monitor/enrolar-host.sh) +
wrapper fuente en /opt/vps-engine/build/vps-wrapper.sh. Uso: `vps-enrolar-host.sh esxi-121
192.168.200.121 <Datastore>` con la password de root del ESXi nuevo a mano (se usa 2 veces —
API y SSH — y NO se guarda). Automatiza: llave rsa4096 dedicada del host; rol **VpsOperator con
los 46 privilegios EXACTOS extraídos del esxi-245** (via govc con credenciales root temporales);
usuario svc-vps con password GENERADA (si existía, la rota); estructura VPS/ + wrapper con su
BASE correcto + authorized_keys con forced-command (capa 4); huella al known_hosts pinned (#9).
QUEDA MANUAL: copiar doradas a _plantillas (decidir método: vCenter clone / vmkfstools),
agregar GOVC_PASSWORD_<ID>=<generada> a engine.env + restart, y el clic "Validar y enrolar" en
la pestaña Motor. **El multi-host queda 100% construido — solo falta ejecutar el enrolamiento
cuando el host nuevo esté disponible.** Sin Codex (deuda A2+B+C).

## 2026-09-21 (lunes) — 🖥️ MULTI-HOST Fase B: pestaña "Motor" en el dashboard — DESPLEGADA

La cara visual del multi-host (repo fastnetmon, commit 6ddd360; parche con backup .bak-20260921):
- **Pestaña Motor** (4ª, junto a Crear/Gestión/Jobs): tabla de hosts con scan EN VIVO por host
  (datastore libre, cores y RAM del fierro, comprometido vs límites, alcanzable ✓/✗ con el
  error), **prioridad editable inline** (la palanca del usuario: decide a dónde van las
  creaciones de WHMCS), botones pausar/activar y quitar (solo sin VMs), y **formulario
  "Enrolar host nuevo"** que usa la validación en vivo del motor (API + wrapper pong +
  datastore) — la Fase C solo tendrá que preparar el host y llenar ese form.
- **Selector "Host VMware" en el tab Crear**: default "Automático — mejor prioridad" o forzar
  un host activo (los pausados no aparecen).
- 3 proxys nuevos en el dashboard: GET/POST /api/vpseng/hosts, PATCH/DELETE
  /api/vpseng/hosts/<id> (permiso vps_engine, actor = usuario de sesión, timeout 90 s).
- Validado: página sirviendo la pestaña, rutas protegidas (401 sin sesión), contenedor sano.
- Sin Codex (deuda: junto a A2). **Queda solo la Fase C**: preparar el 192.168.200.121
  (svc-vps + llave/wrapper + huella + GOVC_PASSWORD_* en engine.env) y enrolarlo desde la
  pestaña. El script noc-monitor/enrolar-host se hará en esa sesión.

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
- **🚀 DESPLEGADO Y VALIDADO EN VIVO**: /hosts con scan ok; host desconocido → 400; con el
  único host PAUSADO, /crear → 409 "no hay hosts ACTIVOS" (control del usuario operando);
  reactivado y mantención completa por-host ok (purga + reconciliación). Quedan: **Fase B**
  (pestaña Motor en dashboard) y **Fase C** (enrolar-host para el 192.168.200.121).

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
