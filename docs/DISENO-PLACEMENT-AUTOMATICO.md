# Diseño — Placement automático (multi-host / multi-datastore) + Jev como asesor

> Conversación de diseño del 2026-10-01. **NO implementado** — queda documentado para
> retomarlo cuando haya más de un host productivo (o un host con >1 datastore).
> Decisión de fondo: hoy el host lo elige Alcadio (prioridad / host forzado); la meta
> futura es que el motor decida solo, SIN perder el override manual.

## 1. El placement es scoring determinista, NO inteligencia

Elegir dónde crear un VPS es el mismo problema que resuelve el DRS de vCenter:
**filtrar → puntuar → elegir**. Algoritmo determinista, instantáneo, reproducible y
testeable. Nada de LLM en el camino síncrono de `/crear` (ver §5).

### 1.1 Filtro (restricciones duras — "¿dónde SE PUEDE crear?")

Todo esto YA existe, hoy validado contra UN candidato; pasaría a correrse contra la lista:

- Host `activo` y en el motor correcto (`uso_clientes` / `uso_admin` según el rol).
- Portgroup del modo presente en el host (pre-chequeo 3b de flujo_crear).
- Cupo lógico con espacio (validar_cupo #8 — barrera atómica bajo NOMBRE_LOCK).
- Datastore con espacio físico tras la reserva (DATASTORE_RESERVA_GB, fail-closed).

### 1.2 Puntaje (criterio blando — "¿dónde es MEJOR?")

Estrategias clásicas:

- **Spread (balancear)** ← la elegida para nuestro caso: tomar el candidato que quede
  con MENOR porcentaje de uso DESPUÉS de colocar el VPS. Balancea solo, sin mover nada.
- Pack (llenar primero): útil para apagar fierros o reservar hosts a VPS grandes.
  Descartada por ahora.

Puntaje = fórmula ponderada simple sobre el estado POST-creación: % disco provisionado,
% RAM comprometida, % vCPU. Los datos ya los entrega `host_recursos()`.

**La `prioridad` actual NO muere: queda como DESEMPATE.** Sigue siendo la palanca del
usuario, pero deja de ser la única señal.

### 1.3 Elección determinista + reserva atómica

Mismo input → misma respuesta, siempre. La reserva del elegido va bajo el lock del cupo
existente para que dos creaciones concurrentes no tomen el mismo hueco.

## 2. Multi-datastore: el candidato pasa a ser el PAR (host, datastore)

Mismo algoritmo un nivel más adentro. Hoy `hosts` tiene UN datastore por host; habría
que pasar a lista (o tabla `datastores`). Consecuencias a tener claras ANTES de partir:

- **Doradas en cada datastore** donde se pueda crear (o aceptar clone cross-datastore,
  más lento). Es la logística del puente al 20051, multiplicada.
- **Presupuesto de disco POR DATASTORE**, no por host. Y vale la nota de Codex en #8:
  si dos hosts comparten un datastore, los presupuestos se SUMAN.

## 3. Despliegue por etapas: MODO SOMBRA primero

Mismo patrón conservador con que se validó la purga:

1. **Sombra**: el motor calcula y registra en el job "yo habría elegido esxi-X /
   datastore-Y por estas razones", pero la decisión sigue siendo del usuario
   (prioridad / host forzado, como hoy).
2. **Comparación**: unas semanas cotejando sus elecciones contra las reales.
3. **Auto**: cuando coincida/convenza, se activa el modo automático. El param `host`
   forzado queda como OVERRIDE PERMANENTE (nunca se retira).

## 4. Frontera que NO se cruza (por ahora)

**Balancear = elegir bien al crear, no mover VMs ya creadas.** El rebalanceo en vivo
(Storage vMotion de VPS de clientes) es otra liga de riesgo; con buen placement inicial
casi nunca hace falta. Si algún día se evalúa, sesión propia + Codex.

## 5. Jev: asesor de capacidad periódico, NUNCA en el camino de creación

Razones para dejarlo fuera de la decisión síncrona: la decisión debe ser determinista,
instantánea y testeable (la suite no puede testear una opinión de LLM), y los inputs son
puros números — una fórmula le gana siempre. Un LLM ahí solo agrega latencia, costo y
no-determinismo al código más auditado del sistema.

Donde SÍ aporta (mismo rol que su revisión periódica de perfiles en fastnetmon):
**informe periódico de capacidad** — "estadísticas pero más lindas", en palabras de
Alcadio (le encantó la idea). Jev mira el histórico de `host_recursos` + jobs y narra:

- Proyección de llenado: "al ritmo actual, datastore1 del 20051 se llena en ~6 semanas".
- Desbalance: "el 245 está al 80% de RAM y el 20051 al 30% — ajustar pesos o pedir
  host nuevo a Fabián".
- Fricciones: "3 creaciones fallaron por espacio; el margen de reserva quedó corto".

En una frase: **el motor decide con fórmula; Jev opina sobre tendencias y avisa cuándo
cambiar la fórmula o comprar fierro.** Juicio y narrativa, no aritmética en caliente.

## 6. Prerrequisitos para implementar (checklist al retomar)

- [ ] ≥2 hosts productivos reales (o 1 host con ≥2 datastores) — sin eso, no hay decisión.
- [ ] Decidir pesos iniciales de la fórmula (disco / RAM / vCPU) con Alcadio.
- [ ] Esquema datastores (si aplica multi-datastore): tabla/lista + migración.
- [ ] Histórico de `host_recursos` persistido (hoy el scan es en vivo; el informe de Jev
      necesita serie temporal — definir dónde y con qué retención).
- [ ] Revisión Codex del algoritmo de placement ANTES de salir de modo sombra.
