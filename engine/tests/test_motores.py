# Tests de los DOS MOTORES (2026-10-01): usos independientes por host (clientes/admin)
# y etiqueta de motor en los jobs (clientes | admin | sistema).
import os
import sys
import tempfile

os.environ.setdefault("ENGINE_TOKEN", "tok-admin-test")
os.environ.setdefault("WHMCS_TOKEN", "tok-whmcs-test")
os.environ.setdefault("MGMT_PUBKEY", "ssh-ed25519 " + "A" * 68 + " t@t")
os.environ.setdefault("DB_PATH", os.path.join(tempfile.mkdtemp(), "t.db"))
os.environ.setdefault("CONFIG_DIR", "c:/Users/alcalmx/Desktop/Proyectos/Vps")
os.environ.setdefault("ESXI_HOST", "10.100.37.245")   # bootstrap: esxi legacy (uso both=1)
os.environ.setdefault("GOVC_URL", "https://10.100.37.245/sdk")
os.environ.setdefault("GOVC_USERNAME", "svc-vps")
os.environ.setdefault("GOVC_PASSWORD", "x")
os.environ["GOVC_PASSWORD_K"] = "k"
sys.path.insert(0, "c:/Users/alcalmx/Desktop/Proyectos/Vps/engine")
import app as motor  # noqa: E402

motor.flujo_crear = lambda *a, **k: None
motor.govc = lambda *a, **k: "{}"
motor.esxi_ssh = lambda *a, **k: "pong"
motor.datastore_libre_gb = lambda host=None: 800
cli = motor.app.test_client()
ADMIN = {"X-Auth-Token": "tok-admin-test"}
WHMCS = {"X-Auth-Token": "tok-whmcs-test"}
BASE = {"marca": "hosting.cl", "sabor": "vps-estandar", "cliente": "t", "modo": "pruebas",
        "instalar_cpanel": False}


def _bootstrap_legacy_id():
    for h in motor.hosts_lista():
        return h["id"]
    return None


def fila(vm, col):
    with motor.DB_LOCK, motor.db() as c:
        r = c.execute("SELECT %s FROM vms WHERE nombre=?" % col, (vm,)).fetchone()
    return r[0] if r else None


def motor_de_job(jid):
    with motor.DB_LOCK, motor.db() as c:
        r = c.execute("SELECT motor FROM jobs WHERE id=?", (jid,)).fetchone()
    return r["motor"] if r else None


LEGACY = _bootstrap_legacy_id()   # host de bootstrap: uso_clientes=1 y uso_admin=1
assert LEGACY, "esperaba un host legacy del bootstrap"
h0 = motor.host_get(LEGACY)
assert h0["uso_clientes"] == 1 and h0["uso_admin"] == 1, "el host legacy debe quedar en AMBOS motores (backfill)"
print("ok - migración: el host legacy queda en ambos motores (uso_clientes=1, uso_admin=1)")

# host SOLO-ADMIN (uso_clientes=0): no debe recibir clientes aunque esté activo
r = cli.post("/hosts", json={"id": "esxi-adm", "ip": "192.168.200.80", "pass_env": "GOVC_PASSWORD_K",
                             "datastore": "dsA", "ssh_key": "/k", "prioridad": 1,
                             "uso_clientes": False, "uso_admin": True}, headers=ADMIN)
assert r.status_code == 200, r.get_json()
hadm = motor.host_get("esxi-adm")
assert hadm["uso_clientes"] == 0 and hadm["uso_admin"] == 1
# WHMCS (automático) NO debe ir al host solo-admin aunque tenga la MEJOR prioridad (1)
r = cli.post("/crear", json=dict(BASE, hostname="c1.hosting.cl", whmcs_serviceid="700"), headers=WHMCS)
assert r.status_code == 200, r.get_json()
assert fila(r.get_json()["vm"], "host") == LEGACY, "clientes no debe caer en un host solo-admin"
assert motor_de_job(r.get_json()["job_id"]) == "clientes"
print("ok - host solo-admin: WHMCS lo ignora (va al legacy) y el job queda motor=clientes")

# host SOLO-CLIENTES (uso_admin=0): rechaza creación manual explícita
r = cli.post("/hosts", json={"id": "esxi-cli", "ip": "192.168.200.81", "pass_env": "GOVC_PASSWORD_K",
                             "datastore": "dsC", "ssh_key": "/k", "prioridad": 2,
                             "uso_clientes": True, "uso_admin": False}, headers=ADMIN)
assert r.status_code == 200, r.get_json()
r = cli.post("/crear", json=dict(BASE, hostname="m1.hosting.cl", host="esxi-cli"), headers=ADMIN)
assert r.status_code == 409 and "manual" in r.get_json()["error"], r.get_json()
print("ok - host solo-clientes: la creación manual del NOC se rechaza (409)")

# creación manual del NOC en el host admin -> job motor=admin
r = cli.post("/crear", json=dict(BASE, hostname="m2.hosting.cl", host="esxi-adm"), headers=ADMIN)
assert r.status_code == 200, r.get_json()
assert fila(r.get_json()["vm"], "host") == "esxi-adm"
assert motor_de_job(r.get_json()["job_id"]) == "admin"
print("ok - creación manual en host admin -> job motor=admin")

# admin SIN host explícito -> host del motor admin por defecto (uso_admin, mejor prioridad)
# esxi-adm tiene prioridad 1 y uso_admin=1 -> es el preferido del motor admin
r = cli.post("/crear", json=dict(BASE, hostname="m3.hosting.cl"), headers=ADMIN)
assert r.status_code == 200, r.get_json()
assert fila(r.get_json()["vm"], "host") == "esxi-adm", "admin por defecto -> mejor host uso_admin"
print("ok - admin sin host -> host admin por defecto (prioridad en el pool admin)")

# jobs de mantención -> motor=sistema
r = cli.post("/reconciliar", json={"actor": "t"}, headers=ADMIN)
assert r.status_code == 200
assert motor_de_job(r.get_json()["job_id"]) == "sistema"
print("ok - job de reconciliación -> motor=sistema")

# GET /jobs expone motor en cada fila
js = cli.get("/jobs", headers=ADMIN).get_json()["jobs"]
assert all("motor" in j and j["motor"] in ("clientes", "admin", "sistema") for j in js)
assert {"clientes", "admin", "sistema"} <= {j["motor"] for j in js}
print("ok - GET /jobs expone motor (clientes/admin/sistema) en los tres grupos")

# PATCH: togglear usos; no se puede dejar un host sin ningún uso
r = cli.patch("/hosts/esxi-adm", json={"uso_clientes": True}, headers=ADMIN)
assert r.status_code == 200 and motor.host_get("esxi-adm")["uso_clientes"] == 1
r = cli.patch("/hosts/esxi-cli", json={"uso_clientes": False, "uso_admin": False}, headers=ADMIN)
assert r.status_code == 400 and "un motor" in r.get_json()["error"], r.get_json()
print("ok - PATCH togglea usos; impide dejar un host sin ningún motor")

# carrera del guard (Codex motores #4): partiendo de (clientes=1, admin=1), quitar un uso
# y LUEGO el otro debe ser rechazado atómicamente (el 2º ve el 1º ya aplicado). esxi-adm
# quedó en ambos usos tras el toggle anterior.
assert motor.host_get("esxi-adm")["uso_clientes"] == 1 and motor.host_get("esxi-adm")["uso_admin"] == 1
r = cli.patch("/hosts/esxi-adm", json={"uso_clientes": False}, headers=ADMIN)   # queda solo admin
assert r.status_code == 200
r = cli.patch("/hosts/esxi-adm", json={"uso_admin": False}, headers=ADMIN)       # dejaría (0,0)
# rechazado: 400 (guard preliminar, caso secuencial) o 409 (re-validación atómica, carrera real)
assert r.status_code in (400, 409) and "un motor" in r.get_json()["error"], r.get_json()
assert motor.host_get("esxi-adm")["uso_admin"] == 1, "el 2º quite no debió aplicarse"
r = cli.patch("/hosts/esxi-adm", json={"uso_clientes": True}, headers=ADMIN)      # restaurar
assert r.status_code == 200
print("ok - guard atómico: no se puede dejar un host en (0,0) ni con quites secuenciales")

# GET /hosts expone uso_clientes/uso_admin como booleanos
hs = {h["id"]: h for h in cli.get("/hosts", headers=ADMIN).get_json()["hosts"]}
assert hs["esxi-adm"]["uso_admin"] is True and hs["esxi-adm"]["uso_clientes"] is True
assert isinstance(hs["esxi-cli"]["uso_clientes"], bool)
print("ok - GET /hosts expone uso_clientes/uso_admin (booleanos)")

# carrera REAL con hilos (Codex motores r2): ambos PATCH leen el estado viejo (1,1) a la
# vez (host_get monkeypatcheado a stale) y corren en paralelo; con la re-validación atómica
# bajo DB_LOCK, exactamente uno aplica (200) y el otro se rechaza (409) — jamás (0,0).
import threading as _th  # noqa: E402
# asegurar (1,1) de partida
cli.patch("/hosts/esxi-adm", json={"uso_clientes": True, "uso_admin": True}, headers=ADMIN)
_h_real = motor.host_get
_stale = dict(_h_real("esxi-adm"))                       # snapshot (1,1)
motor.host_get = lambda hid: dict(_stale) if hid == "esxi-adm" else _h_real(hid)
_barrera = _th.Barrier(2)
_res = {}
def _patch(nombre, cambio):
    _barrera.wait()
    r = cli.patch("/hosts/esxi-adm", json=cambio, headers=ADMIN)
    _res[nombre] = r.status_code
t1 = _th.Thread(target=_patch, args=("cli", {"uso_clientes": False}))
t2 = _th.Thread(target=_patch, args=("adm", {"uso_admin": False}))
t1.start(); t2.start(); t1.join(); t2.join()
motor.host_get = _h_real                                 # restaurar
codigos = sorted(_res.values())
final = _h_real("esxi-adm")
assert (final["uso_clientes"] or final["uso_admin"]), "la carrera dejó el host en (0,0)!"
assert 200 in codigos and 409 in codigos, "esperaba un 200 y un 409 (uno aplica, otro rechazado): %s" % _res
print("ok - carrera REAL con hilos: uno aplica (200), el otro rechazado (409), nunca (0,0)")

print("\nTESTS DE LOS DOS MOTORES OK")
