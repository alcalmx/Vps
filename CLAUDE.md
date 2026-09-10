# Instrucciones para Claude — Proyecto Vps

## Al iniciar una sesión

1. Leer `BITACORA.md` (entrada más reciente arriba) para saber en qué quedó.
2. Leer `README.md` (arquitectura) y `SEGURIDAD.md` (controles — OBLIGATORIO
   antes de escribir/desplegar código que toque el ESXi).

## Contexto rápido

- **Qué es:** ciclo de vida de VPS de clientes en ESXi (crear/eliminar/suspender/editar),
  fase 1 operado desde el dashboard NOC. Marca inicial: hosting.cl.
- **ESXi de pruebas = el de PRODUCCIÓN** (10.100.37.245): ahí corren noc-monitor,
  este mismo VPS de IA, xrp-node (VM "Prueba2"), etc. Solo se crea/borra dentro de
  `[DiscoA37245] VPS/` y con los guardarraíles de SEGURIDAD.md. Acceso diagnóstico:
  `ssh -i ~/.ssh/claude_esxi root@10.100.37.245` (el motor usará credenciales propias).
- **No confundir con VpsClientes** (vps-provision :8223): ese securiza VPS ya
  instalados y custodia llaves en Vaultwarden. Este proyecto crea las máquinas y
  al final puede encadenar con aquel.
- El motor `vps-engine` correrá en **noc-monitor** (contenedor + quadlet,
  127.0.0.1:8224), patrón idéntico a vps-provision.
- Los **sabores** (planes de la web) los dicta el usuario → `sabores/hosting.cl/*.json`
  según `sabores/SCHEMA.md`. No inventar planes.

## Reglas del proyecto

- Toda operación destructiva pasa por las 5 capas de SEGURIDAD.md (registro,
  prefijo `vps-`, usuario svc-vps sin root, wrapper SSH, papelera 7 días).
  **Nunca atajarlas "porque es una prueba".**
- Secretos solo en `/opt/vps-engine/engine.env` del server — jamás en el repo.
- Documentar cada sesión en `BITACORA.md` (más reciente arriba).
- Deploys a noc-monitor (producción): pedir OK explícito del usuario.
- Cuando el repo tenga remoto en GitHub: commit + push tras cada cambio.
