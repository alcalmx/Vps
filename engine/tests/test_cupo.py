# Test #8: cupo del host (atomico con la reserva) + espacio real del datastore.
import json
import os
import sys
import tempfile
import threading

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

MC = {"prefijo_vm": "vps-hcl"}
SABOR = {"vcpu": 4, "ram_mb": 4096, "disco_gb": 100}

# 1) cupo atomico con reservas CONCURRENTES: max disco 600 -> caben exactamente 6 de 100
motor.HOST_MAX_VCPU = 0        # sin limite (aislar la variable disco)
motor.HOST_MAX_RAM_MB = 0
motor.HOST_MAX_DISCO_GB = 600
oks, rechazos = [], []
def intento(i):
    try:
        oks.append(motor.reservar_vm("hosting.cl", MC, "vps-estandar", SABOR, "c%d" % i, "h%d.cl" % i))
    except RuntimeError as e:
        assert "cupo del host excedido" in str(e), str(e)
        rechazos.append(str(e))
hilos = [threading.Thread(target=intento, args=(i,)) for i in range(9)]
[t.start() for t in hilos]; [t.join() for t in hilos]
assert len(oks) == 6 and len(rechazos) == 3, "esperaba 6 ok / 3 rechazos, hubo %d/%d" % (len(oks), len(rechazos))
v, m, d = motor.cupo_comprometido()
assert d == 600, "comprometido debia ser 600 GB exactos, es %d" % d
print("ok - 9 reservas concurrentes con cupo 600GB -> exactamente 6 caben, 3 rechazadas, cero sobreventa")

# 2) papelera NO cuenta en el cupo; downgrade (delta negativo) siempre pasa
with motor.DB_LOCK, motor.db() as c:
    c.execute("UPDATE vms SET estado='papelera' WHERE nombre=?", (oks[0],))
v, m, d = motor.cupo_comprometido()
assert d == 500, "tras mandar una a papelera: 500, es %d" % d
motor.validar_cupo(0, 0, -50)   # downgrade: no lanza
motor.validar_cupo(0, 0, 100)   # vuelve a caber una
try:
    motor.validar_cupo(0, 0, 101)
    raise SystemExit("FALLO: 101 GB no debia caber")
except RuntimeError:
    pass
print("ok - papelera libera cupo; downgrades pasan; borde exacto respetado")

# 3) limites de vCPU y RAM tambien rechazan
motor.HOST_MAX_VCPU = 24
with motor.DB_LOCK, motor.db() as c:
    c.execute("UPDATE vms SET vcpu=24 WHERE estado='creando' AND nombre=?", (oks[1],))
try:
    motor.validar_cupo(1, 0, 0)
    raise SystemExit("FALLO: vCPU 24+... no debia caber")
except RuntimeError as e:
    assert "vCPU" in str(e)
print("ok - limite de vCPU rechaza con mensaje claro")

# 4) datastore_libre_gb: parsea el shape real de govc (lowercase) y variantes
REAL = {"datastores": [
    {"summary": {"name": "datastore1 (7)", "freeSpace": 98228502528}},
    {"summary": {"name": "DiscoA37245", "freeSpace": 1117893165056}}]}
UPPER = {"Datastores": [{"Summary": {"Name": "DiscoA37245", "FreeSpace": 214748364800}}]}
def fake_govc_factory(payload):
    def f(*args, timeout=120, host=None):
        assert args[0] == "datastore.info", args
        return json.dumps(payload)
    return f
motor.govc = fake_govc_factory(REAL)
assert motor.datastore_libre_gb() == 1041, "esperaba 1041 GB, dio %s" % motor.datastore_libre_gb()
motor.govc = fake_govc_factory(UPPER)
assert motor.datastore_libre_gb() == 200
motor.govc = fake_govc_factory({"datastores": []})
try:
    motor.datastore_libre_gb()
    raise SystemExit("FALLO: sin datastore debia lanzar")
except RuntimeError:
    pass
print("ok - datastore_libre_gb: shape real, shape uppercase, y ausencia -> fail-closed")

# 5) endpoint /crear con cupo excedido -> 409 con motivo (no 500 generico)
motor.HOST_MAX_DISCO_GB = 1     # nada cabe
cli = motor.app.test_client()
r = cli.post("/crear", json={"marca": "hosting.cl", "sabor": "vps-estandar", "cliente": "t",
                             "hostname": "t9.hosting.cl", "modo": "pruebas"},
             headers={"X-Auth-Token": "tok-admin-test"})
assert r.status_code == 409 and "excedido" in r.get_json()["error"], \
    "esperaba 409 cupo, dio %s %s" % (r.status_code, r.get_json())
print("ok - /crear con cupo excedido -> 409 con motivo claro")

print("\nTESTS DE CUPO/DATASTORE OK")

# 6) hallazgo Codex #8: host YA excedido -> el downgrade PASA (solo se chequea lo que aumenta)
import app as _m2
_m2.HOST_MAX_VCPU = 16
_m2.HOST_MAX_RAM_MB = 0
_m2.HOST_MAX_DISCO_GB = 0
# forzar comprometido excedido: 20 vCPU (limite 16) — simulando que bajaron el limite
with _m2.DB_LOCK, _m2.db() as c:
    c.execute("DELETE FROM vms")
    c.execute("INSERT INTO vms(nombre,estado,vcpu,ram_mb,disco_gb) VALUES('vps-hcl-9001-x','activo',20,4096,50)")
v,m,d = _m2.cupo_comprometido()
assert v == 20, v
_m2.validar_cupo(-2, 0, 0)        # downgrade de vCPU: PASA aunque 18>16
_m2.validar_cupo(0, 0, 0)         # sin cambios: PASA
_m2.validar_cupo(0, -1024, 100)  # baja RAM, sube disco (sin limite disco): PASA
try:
    _m2.validar_cupo(1, 0, 0)     # AUMENTA vCPU estando excedido: RECHAZA
    raise SystemExit("FALLO: aumento de vCPU sobre limite excedido debia rechazar")
except RuntimeError as e:
    assert "vCPU" in str(e)
print("ok - #8: host excedido -> downgrade/sin-cambio pasan; solo el AUMENTO de la dimension excedida rechaza")

print("\nTESTS DE CUPO/DATASTORE (con hallazgo #8) OK")
