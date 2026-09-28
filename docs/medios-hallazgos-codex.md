# Medios #16-#23 — hallazgos de Codex (2026-09-28)

> Revisión de Codex (thread nuevo). **Veredicto global: RECHAZADO** por bloqueantes en
> #18, #19 y #22. Aprobados: #16, #17, #21, #23. (#20 no se incluyó en el prompt — revisar aparte.)
> Acción: corregir los 3 bloqueantes, re-enviar a Codex, luego marcar en pendiente-revision-codex.md.

## ✅ Aprobados (dentro del alcance mostrado)

- **#16 health por rol** — sin token → 200 `{"ok":true}` (healthcheck del quadlet OK); rol whmcs →
  misma respuesta mínima (sin fuga de inventario); solo admin recibe modo/marcas/sabores.
- **#17 matriz de estados** — coherente; sin KeyError en `[acc]` por la validación previa.
  *Nota menor:* en `editar` puede haber KeyError en `reg["marca"]` si el registro está incompleto
  (aceptable si `marca` es invariante del registro). Límite: la matriz valida estados pero no
  demuestra exclusión mutua entre hilos (eso lo dan los locks por VM).
- **#21 migraciones** — el match `duplicate column name` no depende del locale; aborta conservador
  ante otros errores. *Límite:* abortar NO revierte ALTERs previos; si se exige atomicidad
  persistente, haría falta transacción explícita con rollback. Corrupción como otra subclase de
  `DatabaseError` tampoco quedaría capturada aquí.
- **#23 excepts amplios** — degradación deliberada aceptable (ARP capa extra; `power_state` → "?"
  como señal). No bloqueante; un diagnóstico interno sin secretos ayudaría.

## ❌ #18 chpasswd — BLOQUEANTE

1. **Éxito falso:** si `MGMT_PRIVKEY_PATH` vacío o falta `ip`, la función devuelve `False`; el
   caller lo ignora y publica "contraseña de root aplicada". → comprobar el retorno, o lanzar
   excepción controlada en esas condiciones.
2. **Job puede colgar indefinidamente:** `timeout=15` de `connect()` NO limita la ejecución de
   `chpasswd`; `recv_exit_status()` puede bloquear, y esperar el estado antes de drenar
   stdout/stderr bloquea si se llena la ventana SSH (documentado por Paramiko). → plazo TOTAL +
   drenar ambos streams + cerrar al vencer.
3. **Inyección por newline:** una `root_password` con `\n` mete otra entrada `usuario:clave` en el
   protocolo de chpasswd. → rechazar `\n` (validación previa).
4. **stderr puede traer secretos:** truncar a 120 no sanea. → publicar rc + causa controlada, NO
   volcar stderr crudo al job/log.
5. Mover `connect()` dentro del `try/finally` (cerrar también ante fallo de conexión).

## ❌ #19 growfs — BLOQUEANTE

(La derivación de disco/partición para `/dev/sda3`, `/dev/vda2`, `/dev/nvme0n1p2` está bien; NVMe p1 NO es problema.)
1. **"VERIFICADO" no verifica crecimiento:** `FS_OK 100 100` (o `100 90`) da éxito. Solo convierte
   números. → comparar antes/después para detectar crecimiento; para afirmar "ya estaba correcto"
   contrastar con el objetivo (considerando overhead del FS).
2. **NOCHANGE no prueba tamaño objetivo:** raíz en sda2 seguida de sda3; growpart puede dar NOCHANGE
   porque la partición siguiente impide crecer, y resize2fs termina ok sin cambiar → anuncia
   expansión falsa. Aceptar rc=1 es correcto pero no basta.
3. **Parser permisivo:** `FS_OK basura basura` → ValueError pero igual publica éxito;
   `FS_OK_EXTRA 100 200` pasa `startswith`. → exigir etiqueta EXACTA, 3 campos, tamaños positivos;
   salida inválida = error.
4. **Disco completo sin partición:** `/dev/nvme0n1` → PART=1, DISK="" → `growpart /dev/ 1`;
   `/dev/sda` → grep sin número → aborta por set -e. → distinguir explícitamente disco vs partición.
5. **rc mal normalizados:** sin `findmnt` sale 127 (no 2); fallo de resize2fs/xfs_growfs conserva su
   rc (si fuese 3 → mal clasificado como "no automatizable"). → normalizar fallos operativos a 2,
   reservar 3 para casos detectados deliberadamente.
6. **Pipelines ocultan fallos:** `lsblk|head`, `df|tail|tr` pueden esconder fallo del 1er comando.
7. **fstype se detecta DESPUÉS de growpart:** puede modificar la partición y luego decir "no
   automatizable". → comprobar fstype ANTES de modificar.
8. **Mismo bloqueo SSH que #18:** `exec_command(timeout=90)` no acota `recv_exit_status()`; los 12
   reintentos no ayudan si el primero nunca retorna.

## ❌ #22 address-list — BLOQUEANTE (detección)

1. **Falso negativo:** `0 X comment="" address=... list=...` pasa aunque el comentario esté vacío.
2. **Falso negativo con duplicados:** 1ª entrada deshabilitada+comentada, 2ª para la misma IP/lista
   habilitada → solo mira la 1ª y no alerta.
3. **Falso positivo de formato:** un encabezado/comentario previo al registro puede convertirse en
   `linea`; el regex solo exige `X` como 1er carácter tras el índice, no interpreta el set de flags.
4. `"comment=" in linea` comprueba subcadena, no propiedad con valor.
→ Consultar los IDs coincidentes y obtener explícitamente `disabled` y `comment`, verificando
   cardinalidad y valores (RouterOS `find`/`get`); no confiar en el parser de `terse` "aproximado".

## Cómo retomar (mañana)
1. Corregir #18, #19, #22 con lo de arriba (dosificado, pocas rondas).
2. Re-enviar SOLO esos 3 a Codex (`--resume-last` del thread nuevo o uno fresco).
3. Al aprobar: deploy + marcar Medios ✅ en pendiente-revision-codex.md.
4. Revisar aparte **#20** (no incluido en esta revisión).
