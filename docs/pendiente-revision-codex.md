# Pendientes de validación con Codex

> Codex agotó su cuota el 2026-09-17 (se renueva ~2026-10-15). Por regla de Alcadio,
> el trabajo continúa sin él, PERO todo lo hecho en este período debe **re-validarse
> con Codex cuando vuelva la cuota**. Este archivo es la lista de esa deuda.
> Al retomar: pasarle a Codex cada ítem (diff + contexto + tests) como se hizo con
> los fixes #1-#7 y #10-#13, aplicar sus hallazgos razonables y marcar aquí.

| Estado | Ítem | Commits | Notas |
|---|---|---|---|
| ✅ 2026-09-28 | **Fix #8 — cupo del host + espacio real del datastore** — APROBADO por Codex (2 rondas): corregido rechazo de downgrades (solo se chequean dimensiones que aumentan); modelo cupo-lógico-atómico validado + condiciones operativas documentadas; carrera editar-crear = pendiente documentado | `73caacd` + correcciones hoy | CERRADO. Pendiente futuro: endurecer editar (releer+validar+registrar bajo lock) |
| ⏳ | **vps-mantencion.sh** — versión final (ronda 3 de Codex quedó cortada por cuota; rondas 1-2 aplicadas) | `f036907` | Validado funcionalmente contra mock (feliz EXIT=0, error EXIT=1) y corrida real Succeeded. Revisar: la versión final del refactor poll_job/lanzar |
| ✅ 2026-09-28 | **Fix #9 — pinning de host keys SSH** — APROBADO con observaciones por Codex (2 rondas): verificación de huellas SHA256 con confirmación humana en los scripts, mikrotik con -F/GlobalKnownHostsFile=/dev/null (confianza exclusiva), stderr conservado, mv atómico + preservación de entradas, TOFU de VPS documentado como límite aceptado | `6afb991` + correcciones hoy | CERRADO. Pendiente menor: verificación automática de huellas en CI (KH_CONFIRM), lock entre scripts |

| ⏳ | **Fix #14+#15** — expirar_secretos_jobs (TTL del Send) + límites de input (64KB/413, json_body 400, serviceid numérico, root_password≤128, actor saneado) | `1c09236` | Revisar especialmente: charset del actor (¿demasiado restrictivo?), interacción del 413 con el módulo PHP, y si conviene redactar más campos del resultado |

| ⏳ | **Medios #16-#23** — health por rol, matriz de estados, rc en chpasswd, growfs robusto (FS_OK/ERR/SKIP), migraciones estrictas, address-list en reconciliación | `bef0f41` | Revisar especialmente: matriz de estados vs flujos WHMCS reales, script growfs (¿casos de partición no numerada?), y el supuesto "duplicate column name" en versiones futuras de SQLite |

| ⏳ | **VPS personalizado** — sabor 'personalizado' en /crear (solo admin, specs 1-24/1024-65536/25-600, sabor_def a flujo_crear, cupo #8 aplica) + UI en dashboard | (commits Vps + fastnetmon) | Revisar: rangos, interacción con /editar sobre filas personalizadas, y la UI |

| ✅ 2026-09-28 | **Multi-host A1 (fundación: tabla hosts, bootstrap, govc(host=), scan)** — APROBADO por Codex tras 5 rondas: corregidos adopción única por IP+PRAGMA user_version, govc con allowlist de entorno (no filtra secretos ajenos) + TLS respetado + redacción del secreto literal, comprometido=None ante fallo BD, parser de ceros/límites. Tests en test_multihost.py (8 escenarios) | `47bad6b` + correcciones (commit de hoy) | CERRADO |

| ✅ 2026-09-28 | **Multi-host A2 (flujos host-aware + CRUD /hosts)** — APROBADO por Codex tras 4 rondas: corregidos 6 hallazgos (aislamiento por (host,entrada) en purga/reconciliación, host_de_vm falla explícito, api_url derivada de ip, validación numérica POST/PATCH). Nuevos tests: test_a2_hardening.py | Vps `253a975` + correcciones (commit de hoy) | CERRADO. Pendiente aún: B (pestaña Motor, fnm 6ddd360) y C (enrolar-host.sh) |

## Cómo re-validar (cuando vuelva la cuota)

1. `/codex:setup --enable-review-gate` (reactivar el gate).
2. Por cada ítem ⏳: armar prompt con el diff de los commits + contexto + resultados de
   tests (mismo formato de la serie de fixes), enviarlo con `--resume-last` si el hilo
   de auditoría sigue vivo, aplicar hallazgos, marcar ✅ aquí con la fecha.
