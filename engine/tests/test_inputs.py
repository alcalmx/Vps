# Test #14 (expiracion de secretos) + #15 (limites de input).
import json
import os
import sys
import tempfile

os.environ.setdefault("ENGINE_TOKEN", "tok-admin-test")
os.environ.setdefault("WHMCS_TOKEN", "tok-whmcs-test")
os.environ.setdefault("MGMT_PUBKEY", "ssh-ed25519 " + "A" * 68 + " t@t")
os.environ.setdefault("DB_PATH", os.path.join(tempfile.mkdtemp(), "t.db"))
os.environ.setdefault("CONFIG_DIR", "c:/Users/alcalmx/Desktop/Proyectos/Vps")
os.environ.setdefault("ESXI_HOST", "10.100.37.245")
os.environ.setdefault("GOVC_URL", "https://10.100.37.245/sdk")
os.environ.setdefault("GOVC_USERNAME", "svc-vps")
os.environ.setdefault("GOVC_PASSWORD", "x")
sys.path.insert(0, "c:/Users/alcalmx/Desktop/Proyectos/Vps/engine")
import app as motor  # noqa: E402

cli = motor.app.test_client()
ADMIN = {"X-Auth-Token": "tok-admin-test"}

# ── #14: expiracion de secretos de entrega ──────────────────────────────────
res_viejo = {"ip": "10.100.16.240", "publica": "38.19.57.100",
             "send_url": "https://vault/send/abc", "send_password": "clave123"}
res_nuevo = dict(res_viejo, send_url="https://vault/send/def", send_password="clave456")
with motor.DB_LOCK, motor.db() as c:
    c.execute("INSERT INTO jobs(id,tipo,vm,estado,pasos,actor,resultado,created_at) "
              "VALUES('jv','crear','vps-hcl-0001-v','ok','[]','t',?,datetime('now','localtime','-5 days'))",
              (json.dumps(res_viejo),))
    c.execute("INSERT INTO jobs(id,tipo,vm,estado,pasos,actor,resultado,created_at) "
              "VALUES('jn','crear','vps-hcl-0002-n','ok','[]','t',?,datetime('now','localtime'))",
              (json.dumps(res_nuevo),))
n = motor.expirar_secretos_jobs()
assert n == 1, "debia expirar exactamente 1 (viejo), expiro %d" % n
with motor.DB_LOCK, motor.db() as c:
    jv = json.loads(c.execute("SELECT resultado FROM jobs WHERE id='jv'").fetchone()["resultado"])
    jn = json.loads(c.execute("SELECT resultado FROM jobs WHERE id='jn'").fetchone()["resultado"])
assert jv["send_url"] == "(expirado)" and jv["send_password"] == "(expirado)"
assert jv["ip"] == "10.100.16.240" and jv["publica"] == "38.19.57.100", "lo no-sensible se conserva"
assert jn["send_password"] == "clave456", "el job reciente NO se toca"
assert motor.expirar_secretos_jobs() == 0, "segunda corrida idempotente"
print("ok - #14: secretos viejos redactados, recientes intactos, no-sensible conservado, idempotente")

# ── #15: body malformado -> 400 JSON limpio ─────────────────────────────────
r = cli.post("/accion", data="esto no es json {", headers=ADMIN)
assert r.status_code == 400 and "JSON" in r.get_json()["error"], (r.status_code, r.get_data()[:80])
r = cli.post("/crear", data="[1,2,3]", headers=ADMIN)  # JSON valido pero no-objeto
assert r.status_code == 400
print("ok - #15: JSON malformado o no-objeto -> 400 JSON")

# body gigante -> 413 JSON
r = cli.post("/accion", data="x" * (70 * 1024), headers=dict(ADMIN, **{"Content-Type": "application/json"}))
assert r.status_code == 413 and "grande" in r.get_json()["error"], (r.status_code, r.get_data()[:80])
print("ok - #15: body de 70KB -> 413 JSON")

# serviceid no numerico -> 400 en /accion, /editar y /vm-por-servicio
r = cli.post("/accion", json={"accion": "suspender", "serviceid": "abc; DROP"}, headers=ADMIN)
assert r.status_code == 400 and "numérico" in r.get_json()["error"]
r = cli.post("/editar", json={"sabor": "vps-estandar", "serviceid": "12x"}, headers=ADMIN)
assert r.status_code == 400
r = cli.get("/vm-por-servicio?serviceid=abc", headers=ADMIN)
assert r.status_code == 400
print("ok - #15: serviceid no numerico -> 400 en los 3 endpoints")

# root_password larga -> 400 (no truncar); whmcs_serviceid no numerico -> 400
base = {"marca": "hosting.cl", "sabor": "vps-estandar", "cliente": "t", "hostname": "t1.hosting.cl",
        "modo": "pruebas"}
r = cli.post("/crear", json=dict(base, root_password="x" * 200), headers=ADMIN)
assert r.status_code == 400 and "root_password" in r.get_json()["error"]
r = cli.post("/crear", json=dict(base, whmcs_serviceid="12abc"), headers=ADMIN)
assert r.status_code == 400 and "whmcs_serviceid" in r.get_json()["error"]
r = cli.post("/crear", json=dict(base, pubkey_cliente="ssh-ed25519 " + "A" * 5000), headers=ADMIN)
assert r.status_code == 400 and "larga" in r.get_json()["error"]
print("ok - #15: root_password >128, whmcs_serviceid invalido y pubkey gigante -> 400")

# actor saneado: caracteres raros filtrados, largo acotado (via /reconciliar -> job.actor)
motor.esxi_ssh = lambda cmd, timeout=120: "OK"   # reconciliacion vacia e inocua
motor.mikrotik = lambda *a, **k: (True, "")
r = cli.post("/reconciliar", json={"actor": "  malo`$(rm)\u0007;<script>" + "z" * 100},
             headers=ADMIN)
assert r.status_code == 200
jid = r.get_json()["job_id"]
with motor.DB_LOCK, motor.db() as c:
    actor = c.execute("SELECT actor FROM jobs WHERE id=?", (jid,)).fetchone()["actor"]
assert all(ch.isalnum() or ch in "@:. _+-" for ch in actor), "actor con chars raros: %r" % actor
assert len(actor) <= 60
print("ok - #15: actor saneado (%r)" % actor)

print("\nTESTS DE INPUTS/SECRETOS OK")

# ── ronda 2 Codex #14/#15 ──────────────────────────────────────────────────
# no-str en actor/campos → NO 500
assert motor.limpiar_actor(123) == "api" and motor.limpiar_actor(True) == "api" and motor.limpiar_actor(["x"]) == "api"
r = cli.post("/crear", json={"marca": 123, "sabor": "vps-estandar", "hostname": "t.hosting.cl", "modo": "pruebas"}, headers=ADMIN)
assert r.status_code == 400, "marca no-str debia dar 400, dio %s" % r.status_code
r = cli.post("/crear", json={"marca": "hosting.cl", "sabor": "vps-estandar", "hostname": "t.hosting.cl", "modo": ["x"]}, headers=ADMIN)
assert r.status_code == 400, "modo no-str debia dar 400"
r = cli.post("/accion", json={"vm": 123, "accion": "suspender"}, headers=ADMIN)
assert r.status_code == 400, "vm no-str en /accion debia dar 400"
print("ok - #15 ronda2: campos/actor no-str -> 400/'api' (sin 500)")

# fullmatch (no match): un \n NO puede colarse. El endpoint hace .strip() (un \n al
# final ya se limpia); lo que fullmatch cubre y match NO es el \n EN MEDIO.
assert motor.SERVICEID_RE.fullmatch("123\n") is None, "fullmatch NO debe aceptar 123 con \\n"
assert motor.SERVICEID_RE.match("123\n") is not None, "match SI acepta 123 con \\n (por eso fullmatch)"
r = cli.post("/accion", json={"accion": "suspender", "serviceid": "12\n34"}, headers=ADMIN)
assert r.status_code == 400, "serviceid con \\n en medio debia rechazarse: %s" % r.status_code
print("ok - #15 ronda2: fullmatch bloquea el \\n que match dejaria pasar")

# #14 redacción EN LECTURA: job viejo con secretos -> /job (admin) los muestra '(expirado)' aunque no se haya purgado
import json as _json
with motor.DB_LOCK, motor.db() as c:
    c.execute("INSERT INTO jobs(id,tipo,vm,estado,pasos,actor,resultado,created_at) "
              "VALUES('jold','crear','vps-hcl-0003-o','ok','[]','t',?,datetime('now','localtime','-5 days'))",
              (_json.dumps({"ip": "10.0.0.9", "send_url": "https://s/x", "send_password": "claveXYZ"}),))
r = cli.get("/job/jold", headers=ADMIN).get_json()
assert r["resultado"]["send_password"] == "(expirado)" and r["resultado"]["send_url"] == "(expirado)", r["resultado"]
assert r["resultado"]["ip"] == "10.0.0.9", "lo no-sensible se conserva en la lectura"
# la fila en BD NO fue modificada por la lectura (redacción es solo en la respuesta)
with motor.DB_LOCK, motor.db() as c:
    raw = _json.loads(c.execute("SELECT resultado FROM jobs WHERE id='jold'").fetchone()["resultado"])
assert raw["send_password"] == "claveXYZ", "la lectura NO debe mutar la BD (eso lo hace la purga)"
print("ok - #14 ronda2: redaccion EN LECTURA de secretos vencidos (sin mutar BD; la purga los borra)")

print("\nTESTS DE INPUTS/SECRETOS (rondas 1+2) OK")

# ── ronda 3 Codex #15: pubkey/root_password no-str -> 400; root_password NO se modifica
base_ok = {"marca": "hosting.cl", "sabor": "vps-estandar", "hostname": "tt.hosting.cl", "modo": "pruebas"}
r = cli.post("/crear", json=dict(base_ok, pubkey_cliente=123), headers=ADMIN)
assert r.status_code == 400 and "pubkey_cliente" in r.get_json()["error"], r.get_json()
r = cli.post("/crear", json=dict(base_ok, root_password=True), headers=ADMIN)
assert r.status_code == 400 and "root_password" in r.get_json()["error"], r.get_json()
# root_password con espacios significativos se conserva EXACTA (no .strip())
motor.flujo_crear = lambda *a, **k: None
capt = {}
_orig_res = motor.reservar_vm
def cap_reservar(marca, mc, ss, sabor, cliente, hostname, whmcs_serviceid=None, host=None):
    return _orig_res(marca, mc, ss, sabor, cliente, hostname, whmcs_serviceid, host)
# interceptar run_job para capturar el root_password que llega a flujo_crear
_orig_runjob = motor.run_job
def cap_runjob(job, fn):
    import inspect
    # fn es lambda j: flujo_crear(j, ..., root_password, ...); lo ejecutamos con un job real ya se stubbeo flujo_crear
    return _orig_runjob(job, fn)
# más simple: verificar que len>128 sobre el valor EXACTO (con espacios) rechaza, y que "  x  " no se recorta a "x"
r = cli.post("/crear", json=dict(base_ok, hostname="tt2.hosting.cl", root_password=" " * 3 + "Xy" ), headers=ADMIN)
assert r.status_code == 200, "una pass con espacios debe aceptarse tal cual: %s" % r.get_json()
r = cli.post("/crear", json=dict(base_ok, hostname="tt3.hosting.cl", root_password="a"*129), headers=ADMIN)
assert r.status_code == 400, "129 chars debe rechazar"
r = cli.post("/crear", json=dict(base_ok, hostname="tt4.hosting.cl", root_password="a"*120 + " "*20), headers=ADMIN)
assert r.status_code == 400, "140 chars (aunque 20 sean espacios) debe rechazar SIN recortar: %s" % r.status_code
print("ok - #15 ronda3: pubkey/root_password no-str -> 400; root_password NO se recorta (espacios cuentan)")

print("\nTESTS DE INPUTS/SECRETOS (rondas 1+2+3) OK")
