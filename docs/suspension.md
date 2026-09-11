# Suspensión / reactivación de VPS (morosidad)

> Decisiones tomadas con el usuario el 2026-09-11. La capa 1 (mecanismo) está
> construida; las capas 2-3 (aviso + confirmación) esperan el acceso a WHMCS.

## El mecanismo (capa 1 — CONSTRUIDO)

Suspender **NO apaga la VM**. Replica lo que el usuario hace a mano cuando llega el aviso
de no-pago: **togglear la entrada de la IP pública en su address-list** del MikroTik
RouterData. Existe un drop en firewall **raw**:

```
chain=prerouting action=drop dst-address-list=Red57-0   ;;; Bloqueo ... Destino=Red_57
```

- Entrada **habilitada** en la lista → la IP cae en el drop → **BLOQUEADA = suspendido**.
- Entrada **deshabilitada** → no matchea el drop → **pasa = activo**.

Detalles de diseño (confirmados por el usuario):
- **Solo `dst-address-list`** (corta lo que LLEGA a la IP del cliente). No se bloquea el
  sentido contrario — no es necesario.
- **La VM sigue corriendo** — datos intactos, reactivación instantánea (sin boot).
- **El NAT y la IP pública se conservan** durante la suspensión (el cliente vuelve con su
  misma IP). Solo se liberan al BORRAR el VPS.
- El **comentario** de la entrada (cliente) se conserva en ambos sentidos del toggle.
- **Todos los VPS de clientes llevan IP pública** — no hay caso "sin pública".
- Guardas de estado: suspender solo si `activo`; reanudar solo si `suspendido` (idempotente).
- El motor **verifica** el estado real de la entrada tras el cambio (no confía en el rc).

Operable desde el dashboard (botones Suspender/Reanudar en "VPS gestionados") y por API
(`POST /accion {vm, accion: suspender|reanudar}`) — la misma API que usará WHMCS.

## El flujo completo (capas 2-3 — DECISIÓN: esperar WHMCS)

**Visión del usuario:** llega el aviso de no-pago → pedimos confirmación por **Telegram**
→ con el OK, se aplica el bloqueo.

**Decisión (2026-09-11): irnos con lo DEFINITIVO — WHMCS — de una vez.** No se construye
un lector de correos interino. Cuando tengamos acceso al WHMCS:

1. **WHMCS es quien sabe** quién no pagó / quién pagó (module command Suspend/Unsuspend
   o hooks de facturación) → dispara un aviso a nuestro sistema.
2. El sistema manda **Telegram** al equipo: "Cliente X moroso — ¿suspender vps-hcl-XXXX
   (IP pública Y)? [Suspender] [Cancelar]" — **nada se aplica sin confirmación humana**.
3. Con el OK → `POST /accion suspender` → bloqueo por address-list (capa 1).
4. Cuando paga: mismo camino con "reactivar" → desbloqueo.

**Infra Telegram existente para reutilizar:** en noc-monitor ya hay bots configurados
(`TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` en `/opt/hosting-dashboard/.env` y
`/opt/noc-agent/.env`) — no hay que crear bot nuevo, solo el manejador de botones de
confirmación (callback) cuando toque.

**Estado:** esperando acceso a WHMCS (no lo tenemos aún). La capa 1 queda usable manual
desde el dashboard mientras tanto.
