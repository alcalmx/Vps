# Tests de los JOBS HUÉRFANOS (2026-10-26): la limpieza de jobs 'corriendo' de un proceso anterior corre
# solo al ARRANCAR el servicio, nunca al importar el módulo. Ese día un `import app` de diagnóstico en otro
# proceso (mismo registry.db) marcó como "interrumpido por reinicio del motor" una creación que seguía viva.
import ast
import os
import subprocess
import sys
import tempfile

os.environ.setdefault("ENGINE_TOKEN", "tok-admin-test")
os.environ.setdefault("WHMCS_TOKEN", "tok-whmcs-test")
os.environ.setdefault("MGMT_PUBKEY", "ssh-ed25519 " + "A" * 68 + " t@t")
os.environ.setdefault("MGMT_PRIVKEY_PATH", "/tmp/fake-mgmt-key")
os.environ.setdefault("DB_PATH", os.path.join(tempfile.mkdtemp(), "t.db"))
os.environ.setdefault("CONFIG_DIR", "c:/Users/alcalmx/Desktop/Proyectos/Vps")
os.environ.setdefault("ESXI_HOST", "10.100.37.245")
os.environ.setdefault("GOVC_URL", "https://10.100.37.245/sdk")
os.environ.setdefault("GOVC_USERNAME", "svc-vps")
os.environ.setdefault("GOVC_PASSWORD", "x")
MOTOR_DIR = "c:/Users/alcalmx/Desktop/Proyectos/Vps/engine"
sys.path.insert(0, MOTOR_DIR)
import app as motor  # noqa: E402


def estado(jid):
    with motor.DB_LOCK, motor.db() as c:
        return tuple(c.execute("SELECT estado, error FROM jobs WHERE id=?", (jid,)).fetchone())


# un job vivo (lo que hacía el motor con la creación de Alcadio)
vivo = motor.Job("crear", "vps-ihpe-0099-prueba", "alcadio", motor.PASOS_CREAR)
assert estado(vivo.id) == ("corriendo", None)

# (1) OTRO proceso importa el motor contra el MISMO registro (lo que hizo el diagnóstico del 26-10):
#     init_db corre, pero el job vivo NO se toca
r = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, %r); import app" % MOTOR_DIR],
                   env=dict(os.environ, PYTHONUTF8="1"), capture_output=True, text=True, timeout=120)
assert r.returncode == 0, r.stderr[-800:]
assert estado(vivo.id) == ("corriendo", None), "importar el módulo no debe marcar jobs vivos"
# init_db repetido en el mismo proceso tampoco
motor.init_db()
assert estado(vivo.id) == ("corriendo", None)
print("ok - importar el motor (otro proceso o init_db repetido) no toca los jobs que están corriendo")

# (2) el arranque real sí limpia los huérfanos de un proceso anterior, y solo esos
terminado = motor.Job("eliminar", "vps-hcl-0099-x", "t", ["paso"])
terminado.ok("listo")
assert estado(terminado.id)[0] == "ok", estado(terminado.id)
n = motor.marcar_jobs_huerfanos()
assert n == 1, n
assert estado(vivo.id) == ("error", "interrumpido por reinicio del motor"), estado(vivo.id)
assert estado(terminado.id)[0] == "ok", "un job terminado no se toca"
assert motor.marcar_jobs_huerfanos() == 0
print("ok - al arrancar el servicio los jobs 'corriendo' huérfanos pasan a error (y solo esos)")

# (3) el servicio la llama ANTES de abrir el puerto, y solo en el bloque __main__
arbol = ast.parse(open(os.path.join(MOTOR_DIR, "app.py"), encoding="utf-8").read())
main = [n for n in arbol.body if isinstance(n, ast.If) and isinstance(n.test, ast.Compare)
        and getattr(n.test.left, "id", "") == "__name__"]
assert len(main) == 1, "un solo bloque __main__"
llamadas = [ast.unparse(st.value.func) for st in main[0].body if isinstance(st, ast.Expr) and isinstance(st.value, ast.Call)]
assert llamadas == ["marcar_jobs_huerfanos", "app.run"], llamadas
fuera = [ast.unparse(st) for st in arbol.body if isinstance(st, ast.Expr) and isinstance(st.value, ast.Call)
         and "marcar_jobs_huerfanos" in ast.unparse(st)]
assert not fuera, "marcar_jobs_huerfanos no debe llamarse a nivel de módulo: %r" % fuera
init_src = ast.get_source_segment(open(os.path.join(MOTOR_DIR, "app.py"), encoding="utf-8").read(),
                                  next(n for n in arbol.body if isinstance(n, ast.FunctionDef) and n.name == "init_db"))
assert "estado='corriendo'" not in init_src and "marcar_jobs_huerfanos" not in init_src.replace(
    "ver marcar_jobs_huerfanos", ""), "init_db ya no limpia jobs"
print("ok - la limpieza corre solo en __main__, antes de app.run (no al importar ni en init_db)")
