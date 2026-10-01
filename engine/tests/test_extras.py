# Tests extra fix #4/#5: fail-fast de config + resultado capado en /job para whmcs
import json
import os
import subprocess
import sys
import tempfile

ENGINE_DIR = "c:/Users/alcalmx/Desktop/Proyectos/Vps/engine"
CONFIG_DIR = "c:/Users/alcalmx/Desktop/Proyectos/Vps"
IMPORT_APP = "import sys; sys.path.insert(0, '%s'); import app" % ENGINE_DIR
PUB = "ssh-ed25519 " + "A" * 68 + " t@t"

# 1) tokens iguales -> el motor NO arranca
env = dict(os.environ, ENGINE_TOKEN="x", WHMCS_TOKEN="x", MGMT_PUBKEY=PUB,
           DB_PATH=os.path.join(tempfile.mkdtemp(), "a.db"), CONFIG_DIR=CONFIG_DIR)
r = subprocess.run([sys.executable, "-c", IMPORT_APP], env=env, capture_output=True, text=True)
assert r.returncode != 0 and "no puede ser igual" in r.stderr, r.stderr[-300:]
print("ok - tokens iguales -> arranque rechazado")

# 2) MODO invalido -> no arranca
env2 = dict(env, ENGINE_TOKEN="a", WHMCS_TOKEN="b", MODO="chanta")
r = subprocess.run([sys.executable, "-c", IMPORT_APP], env=env2, capture_output=True, text=True)
assert r.returncode != 0 and "MODO inv" in r.stderr, r.stderr[-300:]
print("ok - MODO invalido -> arranque rechazado")

# 3) WHMCS_MODO invalido -> no arranca
env3 = dict(env, ENGINE_TOKEN="a", WHMCS_TOKEN="b", WHMCS_MODO="chanta")
r = subprocess.run([sys.executable, "-c", IMPORT_APP], env=env3, capture_output=True, text=True)
assert r.returncode != 0 and "WHMCS_MODO inv" in r.stderr, r.stderr[-300:]
print("ok - WHMCS_MODO invalido -> arranque rechazado")

# 4) /job/<id>: resultado visible para admin, capado para whmcs
os.environ.update(ENGINE_TOKEN="tok-admin-test", WHMCS_TOKEN="tok-whmcs-test",
                  MGMT_PUBKEY=PUB, DB_PATH=os.path.join(tempfile.mkdtemp(), "b.db"),
                  CONFIG_DIR=CONFIG_DIR, MODO="pruebas", WHMCS_MODO="pruebas")
sys.path.insert(0, ENGINE_DIR)
import app as motor  # noqa: E402

cli = motor.app.test_client()
with motor.DB_LOCK, motor.db() as c:
    c.execute("INSERT INTO jobs(id,tipo,vm,estado,pasos,actor,resultado) VALUES(?,?,?,?,?,?,?)",
              ("j1", "crear", "vps-hcl-0001-t", "ok", "[]", "t",
               json.dumps({"send_url": "https://send.secreta", "send_password": "clave123"})))
ja = cli.get("/job/j1", headers={"X-Auth-Token": "tok-admin-test"}).get_json()
jw = cli.get("/job/j1", headers={"X-Auth-Token": "tok-whmcs-test"}).get_json()
assert ja.get("resultado", {}).get("send_password") == "clave123", "admin debe ver resultado"
assert "resultado" not in jw, "whmcs NO debe ver resultado: %s" % jw
assert jw.get("estado") == "ok" and "pasos" in jw, "whmcs sí ve estado/pasos (lo que usa el módulo)"
print("ok - /job: resultado (credenciales Send) visible admin / capado whmcs; estado/pasos intactos")
print("EXTRAS OK")
