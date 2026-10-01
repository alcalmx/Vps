# Pendientes de validación con Codex

> ✅ **DEUDA SALDADA 8/8 el 2026-10-01** — todos los ítems construidos sin Codex en el
> período 09-17/10-01 quedaron re-validados y APROBADOS por Codex. No hay deuda abierta.
> La REGLA permanente sigue vigente: todo código nuevo que se haga sin Codex vuelve a
> entrar aquí como deuda hasta pasar su revisión (dosificando cuota: lotes, 1-2 rondas).
>
> Histórico: Codex agotó su cuota el 2026-09-17; el trabajo continuó sin él y se
> re-validó por lotes a medida que volvía la cuota (fixes #1-#7, #10-#13, A1/A2, Medios,
> wizard Fase D, y el lote final personalizado + 2 menores).

| Estado | Ítem | Commits | Notas |
|---|---|---|---|
| ✅ 2026-09-28 | **Fix #8 — cupo del host + espacio real del datastore** — APROBADO por Codex (2 rondas): corregido rechazo de downgrades (solo se chequean dimensiones que aumentan); modelo cupo-lógico-atómico validado + condiciones operativas documentadas; carrera editar-crear = pendiente documentado | `73caacd` + correcciones hoy | CERRADO. Pendiente futuro: endurecer editar (releer+validar+registrar bajo lock) |
| ✅ 2026-09-28 | **vps-mantencion.sh — orquestador de mantención diaria** — APROBADO por Codex (4 rondas: 8 hallazgos → 5 bloqueantes → 1 bloqueante → OK) | `118ea1e` | Parseo con `jq -ers` validado (documento único, raíz objeto, estado/tipo/job_id con anclas `\A..\z`); verificación del TIPO real del job (cierra falso-éxito del gate); rc de cada curl comprobado; deadlines monotónicos (/proc/uptime); orden purga→reconciliar garantizado; token fuera de argv (`--config` 600) + `--noproxy`/`-q`; readiness `/health`; `TimeoutStartSec=1200`. DESPLEGADO 2026-09-28 (md5 6d584b43, respaldo .bak-20260928, daemon-reload; timer 04:30) |
| ✅ 2026-09-28 | **Fix #9 — pinning de host keys SSH** — APROBADO con observaciones por Codex (2 rondas): verificación de huellas SHA256 con confirmación humana en los scripts, mikrotik con -F/GlobalKnownHostsFile=/dev/null (confianza exclusiva), stderr conservado, mv atómico + preservación de entradas, TOFU de VPS documentado como límite aceptado | `6afb991` + correcciones hoy | CERRADO. Pendiente menor: verificación automática de huellas en CI (KH_CONFIRM), lock entre scripts |

| ✅ 2026-09-28 | **Fix #14+#15 — secretos con expiración + límites de input** — APROBADO por Codex (3 rondas): corregidos 500 por .strip() sobre no-str (actor/marca/sabor/hostname/modo/vm/accion/pubkey/root_password), fullmatch en IDs (no match), root_password NO se modifica (se preserva exacta), redacción-en-lectura de secretos vencidos en /job | `1c09236` + correcciones hoy | CERRADO. Menor documentado: send_expires_at UTC (dif. de minutos/1h, no migrado) |

| ✅ 2026-09-29 | **Medios #16-#23** — APROBADO COMPLETO por Codex (3 rondas): #16/#17/#21/#23 en r1; #18 (chpasswd sin éxito falso, plazo total vía _ssh_exec, sin stderr crudo, rechaza 
) y #19 (growfs contrato exit-0+estado, fstype antes de tocar, parse estricto, verificación contra el umbral del plan) en r2; #22 (tokenizador _terse_props que respeta comillas/escapes, todas las entradas, comment con valor) en r3 | `bef0f41` + `ddde9d9`+`754844e` | CERRADO. **DESPLEGADO 2026-09-29** con OK de Alcadio (respaldo app.py.bak-medios-20260929, md5 verificado, health 200). Nota: #20 ya estaba CERRADO antes (por los rediseños #12/#13 — las llamadas de red de la purga corren fuera; ver bitácora) |

| ✅ 2026-10-01 | **VPS personalizado + 2 menores** — APROBADO COMPLETO por Codex (2 rondas, lote único por dosificación). **#1 personalizado**: r1 RECHAZADO (int() permisivo: True→1, 24.9→24, 1e400→500) → validador de entero JSON estricto (rechaza bool/float/string antes del rango) → r2 APROBADO. **#2 checkbox llave Claude**: r1 RECHAZADO (grep del prefijo base64 en cualquier parte no prueba llave efectiva — línea comentada/command= daba falso "ya instalada") → awk que exige entrada de shell pleno con la llave completa → r2 APROBADO. **#3 pre-chequeo portgroup + error de clave**: r1 APROBADO; aplicada además su NOTA (fuga de reserva real: aborto en pre-chequeo dejaba fila 'creando' con IP+cupo; la reconciliación solo alertaba) → compensación try/except en pasos 2-3b que borra la fila → r2 APROBADO | commits de hoy | **DEUDA CODEX SALDADA 8/8.** Tests: test_custom ampliado (bool/float/string/overflow + compensación); 15/15 suites verdes. DESPLEGADO (motor build+restart health 200; dashboard del checkbox ya iba desde 29-09) |

| ✅ 2026-09-29 | **Wizard de enrolamiento (Fase D)** — APROBADO por Codex tras **5 rondas** de revisión de seguridad (código NUEVO revisado ANTES de desplegar): /hosts/preparar (huella 2 pasos + job con root temporal solo-en-memoria y frontera de redacción), almacén de secretos en /data (sin restart), copiar-doradas streaming con import spool+pre-scan fail-closed+presupuesto de extracción+publicación 2 fases con token, reserva atómica de identidad id+ip entre TODOS los endpoints de hosts, _ssh_exec con drenaje concurrente. Incluye y reemplaza la revisión pendiente de enrolar-host.sh #DIAG. Evidencia empírica en ESXi real (tar adversario + tar-bomb) | commits 0be7b4f..a35deea + hoy | APROBADO y DESPLEGADO 2026-09-29 (wrappers ambos hosts + motor + dashboard, con respaldos); E2E del wizard a cargo de Alcadio |

| ✅ 2026-09-28 | **Multi-host A1 (fundación: tabla hosts, bootstrap, govc(host=), scan)** — APROBADO por Codex tras 5 rondas: corregidos adopción única por IP+PRAGMA user_version, govc con allowlist de entorno (no filtra secretos ajenos) + TLS respetado + redacción del secreto literal, comprometido=None ante fallo BD, parser de ceros/límites. Tests en test_multihost.py (8 escenarios) | `47bad6b` + correcciones (commit de hoy) | CERRADO |

| ✅ 2026-09-28 | **Multi-host A2 (flujos host-aware + CRUD /hosts)** — APROBADO por Codex tras 4 rondas: corregidos 6 hallazgos (aislamiento por (host,entrada) en purga/reconciliación, host_de_vm falla explícito, api_url derivada de ip, validación numérica POST/PATCH). Nuevos tests: test_a2_hardening.py | Vps `253a975` + correcciones (commit de hoy) | CERRADO. Pendiente aún: B (pestaña Motor, fnm 6ddd360) y C (enrolar-host.sh) |

## Cómo re-validar (cuando vuelva la cuota)

1. `/codex:setup --enable-review-gate` (reactivar el gate).
2. Por cada ítem ⏳: armar prompt con el diff de los commits + contexto + resultados de
   tests (mismo formato de la serie de fixes), enviarlo con `--resume-last` si el hilo
   de auditoría sigue vivo, aplicar hallazgos, marcar ✅ aquí con la fecha.
