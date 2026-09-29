# RUNBOOK — Enrolar un host ESXi nuevo al motor VPS

> Procedimiento estandarizado y repetible para incorporar un ESXi al pool multi-host del
> motor `vps-engine`. Leer junto a [../SEGURIDAD.md](../SEGURIDAD.md) (las 5 capas) y
> [../README.md](../README.md). Todo cambio de código sin Codex queda en
> [pendiente-revision-codex.md](pendiente-revision-codex.md).

## ✅ CAMINO OFICIAL desde 2026-09-29: el WIZARD del dashboard

Las Fases A y B de este runbook ya **no se ejecutan a mano**: el wizard
(dashboard → pestaña Motor → "Preparar host nuevo") las hace completas — huella cotejada
por el humano contra la consola, rol `VpsOperator` + `svc-vps` rotado, wrapper confinado,
huella pinneada, secreto al almacén del motor, registro pausado y validación en vivo.
Incluye un checkbox opcional para instalar la **llave de diagnóstico de Claude** (Fase 0).
Luego: botón "Copiar doradas" (streaming vía el motor) → **Activar**. Validado E2E el
2026-09-29 sobre esxi-20051. Este runbook queda como **referencia de QUÉ hace el wizard**,
para auditoría, y como plan B manual (`enrolar-host.sh`) si el dashboard no estuviera.

## Resumen del flujo

```
Fase 0  Conectividad + acceso de diagnóstico (Claude)   [checkbox del wizard, o a mano]
Fase A  Securización del host                            [WIZARD (antes: enrolar-host.sh)]
Fase B  Registro en el motor                             [WIZARD (antes: engine.env + dashboard)]
Fase C  Cupo + doradas + prueba + demos → producción    [botones del dashboard + gerencia]
```

El host **solo** queda operativo para el motor cuando la Fase B valida EN VIVO (API + wrapper
`pong` + datastore). Nada se acepta a ciegas.

---

## ⚠️ Reglas de oro (aprendidas en campo)

1. **Llaves SSH de ESXi = RSA, nunca ed25519.** El sshd de **ESXi 8.0 rechaza ed25519**
   (`Permission denied (publickey)` aunque la llave esté bien puesta). La llave del motor ya se
   genera RSA; la de diagnóstico también debe ser RSA.
2. **La password de root del ESXi NUNCA se guarda.** Se usa solo durante el enrolamiento
   (prompts interactivos). El motor jamás la conoce (usa `svc-vps` con rol mínimo).
3. **Cada host, su propia llave y su propio `svc-vps`** (compromiso de uno no abre los demás).
4. **La llave del motor va confinada** (`command=` → wrapper, sin shell). La de diagnóstico va
   con shell pleno pero es OTRA llave, separada.

---

## Fase 0 — Conectividad y acceso de diagnóstico

### 0.1 Conectividad (verificar ANTES de todo)
La red del host nuevo puede estar en otro segmento → abrir ruta/VLAN/firewall primero.

| Origen | Destino | Puertos | Para qué |
|---|---|---|---|
| **noc-monitor** (192.168.122.252) | host nuevo | **443** (API govc) + **22** (SSH wrapper) | el motor enrola y opera |
| **VPS de IA** (este) | host nuevo | **22** | acceso de diagnóstico de Claude |

Test rápido desde cada origen: `ping`, y `nc -z <ip> 443` / `nc -z <ip> 22` (o
`bash -c 'echo > /dev/tcp/<ip>/443'`).

### 0.2 Acceso de diagnóstico de Claude (RSA dedicada por host)
En el VPS de IA:
```sh
ssh-keygen -t rsa -b 4096 -N "" -C "claude-ia-rsa" -f ~/.ssh/claude_esxi_<id>_rsa
```
Autorizar la pública en el host (una de dos):
- **Integrado** en la Fase A: `DIAG_PUBKEY=~/.ssh/claude_esxi_<id>_rsa.pub` al llamar
  `enrolar-host.sh` (lo instala en la misma corrida, validando que sea RSA).
- **Manual** (host limpio, aún sin Fase A): entrar por consola/password y
  `printf '%s\n' '<contenido .pub>' >> /etc/ssh/keys-root/authorized_keys`.

Verificar: `ssh -i ~/.ssh/claude_esxi_<id>_rsa root@<ip> "hostname; vmware -v"`.

---

## Fase A — Securización del host (`enrolar-host.sh`)

Se corre **EN noc-monitor** (root), con la password de root del ESXi a mano. Prerrequisitos:
imagen `localhost/vps-engine:latest` construida (para `govc`) y
`/opt/vps-engine/build/vps-wrapper.sh` presente.

```sh
# opcional: DIAG_PUBKEY para instalar de paso la llave de diagnóstico (RSA)
DIAG_PUBKEY=/opt/vps-engine/keys/claude_diag_<id>.pub \
  /opt/vps-engine/build/... /enrolar-host.sh <id> <ip> <datastore> [ssh_port]
```

Qué automatiza (idempotente):
1. Llave RSA 4096 dedicada del host en `/opt/vps-engine/keys/vps_engine_esxi_<id>`.
2. Rol `VpsOperator` (46 privilegios exactos, sin root/Global.*) + usuario `svc-vps` con
   password generada — vía `govc` con las credenciales root **temporales**.
3. Estructura `VPS/{_plantillas,_papelera,_bin}` en el datastore + wrapper con su `BASE` +
   `authorized_keys` con `command=` forzado (capa 4).
4. (3b, opcional) Llave de diagnóstico RSA con shell pleno.
5. Huella SSH del host al `known_hosts` pinneado, con **confirmación humana SHA256** contra la
   consola del ESXi (no aceptar a ciegas).

Al final imprime los datos para la Fase B (la password de `svc-vps` sale UNA vez — copiarla ya).

---

## Fase B — Registro en el motor

1. **engine.env** (en noc-monitor, archivo 600 root) — agregar la línea que imprimió la Fase A:
   ```
   GOVC_PASSWORD_<ID>=<svcpass>
   ```
   y reiniciar: `systemctl restart vps-engine`.
2. **Doradas**: copiar las plantillas al datastore nuevo (pesado; vCenter clone o `vmkfstools`):
   `/vmfs/volumes/<datastore>/VPS/_plantillas/dorada-almalinux9.7/` (+ la `-cpanel`).
3. **Dashboard → pestaña Motor → "Enrolar host nuevo"** con:
   `id`, `ip`, `datastore`, `ssh_key=/keys/vps_engine_esxi_<id>`, `pass_env=GOVC_PASSWORD_<ID>`.
   El motor hace `POST /hosts` y **valida en vivo** (`govc about` + wrapper `pong` + datastore
   libre) antes de aceptar. Si falta algo → 400 "host NO enrolable".

---

## Fase C — Cupo, prueba y paso a producción

1. **Cupo** del host (protege recursos): definir `max_vcpu` / `max_ram_mb` / `max_disco_gb`
   (dashboard PATCH /hosts). Para staging, chico; para producción, según hardware.
2. **Prueba E2E**: crear un VPS de test en el host, verificar (IP, NAT, SSH), eliminar.
3. **Demos** a gerencia. Si aprueban → cambiar discos y el host pasa a **producción**
   (revisar que `estado=activo`, prioridad y cupo queden como producción).

---

## Ejemplo trabajado — `esxi-20051` (2026-09-28)

| Campo | Valor |
|---|---|
| id | `esxi-20051` |
| ip | `192.168.200.51` (`esxi20051cl.dedicados.cl`) |
| ESXi | 8.0.3 build-25205845 · 20c/40t · ~128 GB RAM |
| datastore | `datastore1` → BASE wrapper `/vmfs/volumes/datastore1/VPS` |
| diag key (Claude) | `~/.ssh/claude_esxi_20051_rsa` (RSA; la ed25519 fue rechazada) |
| pass_env | `GOVC_PASSWORD_ESXI-20051` |
| propósito | STAGING → 1er host de producción si gerencia aprueba (cambiando discos) |

---

## Troubleshooting

- **`Permission denied (publickey)` con la llave de diagnóstico** → ¿es ed25519? ESXi 8 la
  rechaza; usar RSA. Confirmar ubicación `/etc/ssh/keys-root/authorized_keys`, `permitrootlogin
  yes`, y que la línea no se partió al pegar (`od -c` para ver bytes; usar `printf '%s\n'`).
- **`host NO enrolable` en el dashboard (400)** → falló la validación en vivo: revisar que
  `svc-vps`/llave/wrapper/huella estén instalados (Fase A) y que `GOVC_PASSWORD_<ID>` exista en
  engine.env con el motor reiniciado.
- **`ssh-keygen: not found` en el ESXi** → entorno mínimo; no calcular huellas en el host,
  hacerlo desde noc-monitor / el VPS de IA.
