# Modelo de seguridad — proyecto Vps

> Leer y respetar SIEMPRE antes de escribir o desplegar código de este proyecto.
> El motor tiene poder de crear y **destruir** VMs en un host que también corre
> la infraestructura de producción (incluido el propio VPS de IA).

---

## Principio rector

**El sistema solo puede tocar lo que el sistema creó.** Toda otra VM del host es
invisible e intocable para él, por diseño y por capas redundantes (si una capa
falla, las demás siguen protegiendo).

## Las 5 capas de protección

### Capa 1 — Registro de VMs gestionadas (aplicación)
- SQLite `/opt/vps-engine/data/registry.db`: toda VM creada por el motor queda
  registrada (nombre, marca, sabor, cliente, IP, moid, fechas, estado).
- **Ninguna operación de mutación (eliminar/suspender/editar) acepta una VM que
  no esté en el registro.** No existe endpoint "adoptar VM existente" en fase 1.

### Capa 2 — Convención de nombres (aplicación)
- Todo lo gestionado se llama `vps-<marca>-<id>[-<cliente>]` (ej: `vps-hcl-0001-acme`).
- Además del registro, toda operación destructiva re-valida que el nombre de la VM
  empiece con `vps-` Y que su VMX viva bajo `/vmfs/volumes/DiscoA37245/VPS/`.
- Denylist explícita en código de los moid/nombres de las VMs de infraestructura
  conocidas (cinturón y tirantes).

### Capa 3 — Usuario API con rol mínimo (ESXi)
- Usuario local `svc-vps` en el ESXi con **rol custom** solo con privilegios:
  `VirtualMachine.*` (crear/configurar/power/eliminar), `Datastore.AllocateSpace`,
  `Datastore.Browse`, `Network.Assign`, `Resource.AssignVMToPool`.
- SIN: privilegios de host (config, servicios, usuarios), sin `Global.*`,
  sin gestión de otros usuarios. **La credencial de root del ESXi nunca la usa el motor.**
- Nota: en ESXi standalone los permisos se aplican a nivel host (no por carpeta),
  por eso esta capa NO basta sola — se apoya en las capas 1, 2 y 4.

### Capa 4 — SSH restringido con wrapper (ESXi)
El clon de discos (`vmkfstools`) no existe en la API → se hace por SSH, pero NUNCA
con shell libre:
- La sesión SSH autentica como `root` (en ESXi no hay alternativa real: `vmkfstools`
  y mover carpetas del datastore requieren root, y un usuario local con rol de
  administrador sería equivalente a root). **La protección NO es la identidad del
  usuario sino el confinamiento por `command=` forzado**: la llave del motor no
  obtiene shell, ni pty, ni forwarding — solo el wrapper (verificado en el host
  2026-09-15). El usuario `svc-vps` (capa 3) es solo para la API (govc), no SSH.
- Llave dedicada `vps_engine_esxi` (distinta de `claude_esxi`, que es la de
  diagnóstico humano/IA).
- En `authorized_keys` del ESXi la llave entra con
  `command="/vmfs/volumes/DiscoA37245/VPS/_bin/vps-wrapper.sh",no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty`.
- El wrapper ([esxi/vps-wrapper.sh](esxi/vps-wrapper.sh)) implementa un mini-protocolo
  de subcomandos permitidos: `clone-disk`, `mkdir-vm`, `grow-disk`, `trash-vm`,
  `purge-trash`, `list-vps`. Cada uno:
  - valida sus argumentos con patrones estrictos (nombres `vps-[a-z]+-[0-9a-z-]+`),
  - **rechaza cualquier ruta fuera de `/vmfs/volumes/DiscoA37245/VPS/`**
    (y resuelve symlinks antes de validar),
  - `trash-vm` solo MUEVE a `_papelera/` — el único borrado real es `purge-trash`
    sobre entradas con más de 7 días dentro de `_papelera/`.
- Aunque la llave se filtrara completa, el atacante no obtiene shell ni puede
  tocar nada fuera de `VPS/`.

### Capa 5 — Papelera con retención (procedimiento)
- "Eliminar" = mover a `VPS/_papelera/<AAAAMMDD>-<nombre>/`. Purga real a los
  7 días por job diario (que también pasa por el wrapper).
- El dashboard exige confirmación doble para eliminar (reescribir el nombre).

---

## Secretos y credenciales

| Secreto | Dónde vive | Dónde NO |
|---|---|---|
| Password de `svc-vps` (API ESXi) | `/opt/vps-engine/engine.env` (600, root) en noc-monitor | repo, dashboard, este VPS |
| Llave privada `vps_engine_esxi` | mismo `engine.env` / volumen del contenedor (600) | repo; NO reutilizar `claude_esxi` |
| Token API del motor (`X-Auth-Token`) | `engine.env` + `.env` del dashboard | repo |
| Llave pública de gestión (va en cada VPS creado) | pública — puede ir en repo | (la privada sigue en bóveda/`~/.ssh/gestion_hosting`, proyecto VpsClientes) |
| Credenciales de clientes | **Este proyecto no las maneja.** La llave del cliente es de vps-provision/Vaultwarden | aquí |

- El motor NUNCA loguea secretos. La auditoría guarda operación/actor/VM/resultado,
  no credenciales.
- Cada host ESXi futuro tendrá SU PROPIA llave y SU PROPIO usuario `svc-vps`
  (compromiso de uno no abre los demás).

## Superficie de red

- `vps-engine` escucha SOLO `127.0.0.1:8224` en noc-monitor (red de host, como
  vps-provision). El único cliente es el dashboard (mismo host).
- noc-monitor → ESXi: 443 (API) y 22 (SSH restringido). Nada nuevo se expone.
- El dashboard ya tiene login + 2FA + roles; la sección VPS usa
  `permission_required("vps_engine")` (permiso nuevo en la matriz de roles).

## Auditoría y notificaciones

- Tabla `operaciones` en el registro: timestamp, usuario del dashboard, acción,
  VM, parámetros (sin secretos), resultado, duración. Append-only (sin endpoint
  de borrado).
- Toda operación (ok o error) genera notificación visible en la sección VPS del NOC.
- Futuro: alertar si aparece una VM con prefijo `vps-` NO registrada (deriva),
  o si el inventario del host difiere del registro.

## Límites de recursos (proteger el host de producción)

- `hosts.json` define por host: RAM/vCPU/disco máximos asignables a VPS de clientes.
  Propuesta inicial para 10.100.37.245: reservar para infra y dejar a clientes
  **máx 64 GB RAM / 24 vCPU / 600 GB disco** (ajustable con el usuario).
- La creación calcula lo ya comprometido (suma de sabores activos en el registro)
  y rechaza si excede el cupo — antes de tocar el ESXi.

## Qué NO hace este sistema (fase 1)

- No abre puertos nuevos ni toca firewalls.
- No gestiona VMs que no creó (ni "adopta" existentes).
- No guarda contraseñas de clientes (eso es de VpsClientes/Vaultwarden).
- No opera sobre el ESXi con privilegios libres: por API usa `svc-vps` (rol mínimo,
  nunca root); por SSH la sesión autentica como root **pero sin shell** — confinada
  por `command=` al wrapper (ver capa 4). La credencial de root del ESXi (password)
  nunca la conoce ni la usa el motor.
- No borra nada de forma inmediata e irreversible.
