# Test VPS personalizado: solo admin, rangos, cupo, y specs correctas en el registro.
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

_orig_flujo_crear = motor.flujo_crear      # el real, para el test de compensación
motor.flujo_crear = lambda *a, **k: None   # stub: nada externo
cli = motor.app.test_client()
ADMIN = {"X-Auth-Token": "tok-admin-test"}
WHMCS = {"X-Auth-Token": "tok-whmcs-test"}
BASE = {"marca": "hosting.cl", "sabor": "personalizado", "cliente": "custom",
        "hostname": "c1.hosting.cl", "modo": "pruebas"}

# whmcs no puede usar personalizado
r = cli.post("/crear", json=dict(BASE, whmcs_serviceid="123", vcpu=2, ram_mb=2048, disco_gb=50), headers=WHMCS)
assert r.status_code == 403 and "NOC" in r.get_json()["error"], (r.status_code, r.get_json())
print("ok - whmcs con personalizado -> 403")

# specs faltantes -> 400 (entero exigido)
r = cli.post("/crear", json=BASE, headers=ADMIN)
assert r.status_code == 400 and "entero" in r.get_json()["error"], r.get_json()
# fuera de rango -> 400
for specs in ({"vcpu": 0, "ram_mb": 2048, "disco_gb": 50},
              {"vcpu": 25, "ram_mb": 2048, "disco_gb": 50},
              {"vcpu": 2, "ram_mb": 512, "disco_gb": 50},
              {"vcpu": 2, "ram_mb": 2048, "disco_gb": 20},
              {"vcpu": 2, "ram_mb": 2048, "disco_gb": 700}):
    r = cli.post("/crear", json=dict(BASE, **specs), headers=ADMIN)
    assert r.status_code == 400 and "fuera de rango" in r.get_json()["error"], (specs, r.get_json())
print("ok - specs faltantes/fuera de rango -> 400 (5 casos borde)")

# validación ESTRICTA de entero (Codex personalizado #1): bool, float y string NO
# deben colar por coerción (True->1, 24.9->24, "6"->6)
for mala in ({"vcpu": True, "ram_mb": 2048, "disco_gb": 50},      # bool (subclase de int)
             {"vcpu": 2.0, "ram_mb": 2048, "disco_gb": 50},       # float entero
             {"vcpu": 24.9, "ram_mb": 2048, "disco_gb": 50},      # float que truncaría a 24
             {"vcpu": "6", "ram_mb": 2048, "disco_gb": 50},       # string numérico
             {"vcpu": 2, "ram_mb": 1e400, "disco_gb": 50}):       # overflow -> no debe dar 500
    r = cli.post("/crear", json=dict(BASE, **mala), headers=ADMIN)
    assert r.status_code == 400 and "entero" in r.get_json()["error"], (mala, r.status_code, r.get_json())
print("ok - entero estricto: bool/float/string/overflow -> 400 (sin 500)")

# creacion valida: registro con specs exactas y sabor 'personalizado'
r = cli.post("/crear", json=dict(BASE, vcpu=6, ram_mb=12288, disco_gb=200), headers=ADMIN)
assert r.status_code == 200 and r.get_json()["ok"], r.get_json()
vm = r.get_json()["vm"]
with motor.DB_LOCK, motor.db() as c:
    row = dict(c.execute("SELECT sabor,vcpu,ram_mb,disco_gb FROM vms WHERE nombre=?", (vm,)).fetchone())
assert row == {"sabor": "personalizado", "vcpu": 6, "ram_mb": 12288, "disco_gb": 200}, row
print("ok - personalizado 6/12G/200G creado: registro exacto (%s)" % vm)

# el cupo del host (#8) tambien lo frena: otro de 200GB + 300GB excede 600 (ya hay 200)
r = cli.post("/crear", json=dict(BASE, hostname="c2.hosting.cl", vcpu=4, ram_mb=4096, disco_gb=300), headers=ADMIN)
assert r.status_code == 200
r = cli.post("/crear", json=dict(BASE, hostname="c3.hosting.cl", vcpu=4, ram_mb=4096, disco_gb=200), headers=ADMIN)
assert r.status_code == 409 and "excedido" in r.get_json()["error"], (r.status_code, r.get_json())
print("ok - el personalizado respeta el cupo del host (409 al exceder)")

# compensación (Codex personalizado #3): si flujo_crear aborta en un pre-chequeo
# (antes de tocar el ESXi), la fila 'creando' se BORRA (libera IP y cupo) en vez de
# quedar huérfana. Se simula haciendo fallar el pre-chequeo de datastore.
import time as _t  # noqa: E402
_orig_ds = motor.datastore_libre_gb
motor.flujo_crear = _orig_flujo_crear            # restaurar el real (estaba stubbeado)
motor.ip_libre_pruebas = lambda *a, **k: "192.168.200.123"   # determinista (sin ping real)
motor.datastore_libre_gb = lambda h: 0           # 0 GB → aborta en el paso 3
motor.esxi_ssh = lambda *a, **k: (_ for _ in ()).throw(AssertionError("no debió tocar el ESXi"))
r = cli.post("/crear", json=dict(BASE, hostname="comp.hosting.cl", vcpu=2, ram_mb=2048, disco_gb=50), headers=ADMIN)
assert r.status_code == 200, r.get_json()    # el job se lanza; falla en background
for _ in range(100):
    with motor.DB_LOCK, motor.db() as c:
        viva = c.execute("SELECT COUNT(*) n FROM vms WHERE hostname='comp.hosting.cl' AND estado='creando'").fetchone()["n"]
    if viva == 0:
        break
    _t.sleep(0.1)
with motor.DB_LOCK, motor.db() as c:
    quedan = c.execute("SELECT COUNT(*) n FROM vms WHERE hostname='comp.hosting.cl'").fetchone()["n"]
assert quedan == 0, "la fila 'creando' quedó huérfana tras abortar el pre-chequeo (debía borrarse)"
motor.datastore_libre_gb = _orig_ds
motor.flujo_crear = lambda *a, **k: None   # volver al stub para el resto
print("ok - compensación: aborto en pre-chequeo borra la fila reservada (libera IP y cupo)")

# el catalogo sigue funcionando igual
r = cli.post("/crear", json={"marca": "hosting.cl", "sabor": "vps-estandar", "cliente": "t",
                             "hostname": "t2.hosting.cl", "modo": "pruebas",
                             "instalar_cpanel": False}, headers=ADMIN)
assert r.status_code == 200 or (r.status_code == 409 and "excedido" in r.get_json()["error"]), r.get_json()
print("ok - catalogo intacto (regresion)")

print("\nTESTS DE PERSONALIZADO OK")
