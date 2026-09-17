# Pendientes de validación con Codex

> Codex agotó su cuota el 2026-09-17 (se renueva ~2026-10-15). Por regla de Alcadio,
> el trabajo continúa sin él, PERO todo lo hecho en este período debe **re-validarse
> con Codex cuando vuelva la cuota**. Este archivo es la lista de esa deuda.
> Al retomar: pasarle a Codex cada ítem (diff + contexto + tests) como se hizo con
> los fixes #1-#7 y #10-#13, aplicar sus hallazgos razonables y marcar aquí.

| Estado | Ítem | Commits | Notas |
|---|---|---|---|
| ⏳ | **Fix #8** — cupo del host (HOST_MAX_*, atómico con la reserva, 409) + espacio real datastore (`datastore_libre_gb`, fail-closed, DATASTORE_RESERVA_GB) | `73caacd` | Tests: 5 escenarios + regresión. Revisar especialmente: carrera del cupo en `editar` (no atómica, documentada), parser de shapes de govc |
| ⏳ | **vps-mantencion.sh** — versión final (ronda 3 de Codex quedó cortada por cuota; rondas 1-2 aplicadas) | `f036907` | Validado funcionalmente contra mock (feliz EXIT=0, error EXIT=1) y corrida real Succeeded. Revisar: la versión final del refactor poll_job/lanzar |
| ⏳ | **Fix #9** — pinning de host keys (cliente_ssh_pinned/RejectPolicy, mikrotik estricto, known-hosts.sh, TOFU documentado en VPS) | `6afb991` | Revisar especialmente: manejo de rotación de llaves, formato [host]:puerto, y si el TOFU de VPS amerita endurecerse |

| ⏳ | **Fix #14+#15** — expirar_secretos_jobs (TTL del Send) + límites de input (64KB/413, json_body 400, serviceid numérico, root_password≤128, actor saneado) | `1c09236` | Revisar especialmente: charset del actor (¿demasiado restrictivo?), interacción del 413 con el módulo PHP, y si conviene redactar más campos del resultado |

| ⏳ | **Medios #16-#23** — health por rol, matriz de estados, rc en chpasswd, growfs robusto (FS_OK/ERR/SKIP), migraciones estrictas, address-list en reconciliación | `bef0f41` | Revisar especialmente: matriz de estados vs flujos WHMCS reales, script growfs (¿casos de partición no numerada?), y el supuesto "duplicate column name" en versiones futuras de SQLite |

| ⏳ | **VPS personalizado** — sabor 'personalizado' en /crear (solo admin, specs 1-24/1024-65536/25-600, sabor_def a flujo_crear, cupo #8 aplica) + UI en dashboard | (commits Vps + fastnetmon) | Revisar: rangos, interacción con /editar sobre filas personalizadas, y la UI |

## Cómo re-validar (cuando vuelva la cuota)

1. `/codex:setup --enable-review-gate` (reactivar el gate).
2. Por cada ítem ⏳: armar prompt con el diff de los commits + contexto + resultados de
   tests (mismo formato de la serie de fixes), enviarlo con `--resume-last` si el hilo
   de auditoría sigue vivo, aplicar hallazgos, marcar ✅ aquí con la fecha.
