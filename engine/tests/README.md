# Suites de regresión del motor

18 suites de aserciones puras (sin pytest: cada archivo se ejecuta y revienta en el primer
`assert` que falle). Importan `engine/app.py` con un `DB_PATH` temporal y tokens de prueba,
así que **no tocan el ESXi ni la base real** — los puntos que saldrían a la red están
stubbeados en cada suite.

Correrlas todas:

```bash
cd engine/tests
for t in test_*.py; do PYTHONUTF8=1 python "$t" >/dev/null 2>&1 \
  && echo "ok  $t" || echo "FALLO $t"; done
```

Una sola, con su salida (lo normal al depurar):

```bash
PYTHONUTF8=1 python test_rootpw.py
```

`test_jq_anclas.sh` es aparte: valida el parseo de `vps-mantencion.sh` con `jq`, y necesita
`jq` instalado.

Las rutas a `engine/app.py` van absolutas dentro de cada suite, así que se pueden correr desde
cualquier directorio. Si el repo se mueve de `c:/Users/alcalmx/Desktop/Proyectos/Vps`, hay que
ajustar el `sys.path.insert` de la cabecera.

**Cada vez que Codex pide una prueba, va aquí** — es el registro de lo que una revisión dejó
cubierto. La tabla de `docs/pendiente-revision-codex.md` dice qué suite nació de qué revisión.
