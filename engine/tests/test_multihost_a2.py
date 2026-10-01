# Tests Multi-host A2: seleccion de host (control del usuario), CRUD /hosts con
# validacion en vivo, y propagacion del host correcto en los flujos.
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
os.environ["GOVC_PASSWORD_ESXI121"] = "clave-121"
sys.path.insert(0, "c:/Users/alcalmx/Desktop/Proyectos/Vps/engine")
import app as motor  # noqa: E402

motor.flujo_crear = lambda *a, **k: None
cli = motor.app.test_client()
ADMIN = {"X-Auth-Token": "tok-admin-test"}
WHMCS = {"X-Auth-Token": "tok-whmcs-test"}
BASE = {"marca": "hosting.cl", "sabor": "vps-estandar", "cliente": "t", "modo": "pruebas",
        "instalar_cpanel": False}

def fila_host(vm):
    with motor.DB_LOCK, motor.db() as c:
        r = c.execute("SELECT host FROM vms WHERE nombre=?", (vm,)).fetchone()
    return r["host"] if r else None

# ── POST /hosts: enrolamiento con validacion en vivo ─────────────────────────
r = cli.post("/hosts", json={"id": "esxi-121", "ip": "192.168.200.121", "pass_env": "NO_EXISTE",
                             "datastore": "DiscoB", "ssh_key": "/keys/k121"}, headers=ADMIN)
assert r.status_code == 400 and "NO_EXISTE" in r.get_json()["error"], r.get_json()
print("ok - POST /hosts con pass_env inexistente -> 400")

def govc_ok(*a, timeout=120, host=None):
    return "{}"
def esxi_pong(cmd, timeout=300, host=None):
    return "pong"
motor.govc = govc_ok
motor.esxi_ssh = esxi_pong
motor.datastore_libre_gb = lambda host=None: 800

r = cli.post("/hosts", json={"id": "esxi-121", "ip": "192.168.200.121",
                             "pass_env": "GOVC_PASSWORD_ESXI121", "datastore": "DiscoB121",
                             "ssh_key": "/keys/vps_engine_esxi_121", "prioridad": 50},
             headers=ADMIN)
assert r.status_code == 200 and r.get_json()["datastore_libre_gb"] == 800, r.get_json()
assert motor.host_get("esxi-121")["prioridad"] == 50
print("ok - POST /hosts valida en vivo (about+pong+datastore) y enrola")

r = cli.post("/hosts", json={"id": "esxi-121", "ip": "192.168.200.121",
                             "pass_env": "GOVC_PASSWORD_ESXI121", "datastore": "D",
                             "ssh_key": "/k"}, headers=ADMIN)
assert r.status_code == 409, "duplicado debia dar 409"
def esxi_mudo(cmd, timeout=300, host=None):
    raise RuntimeError("connection refused")
motor.esxi_ssh = esxi_mudo
r = cli.post("/hosts", json={"id": "esxi-131", "ip": "192.168.200.131",
                             "pass_env": "GOVC_PASSWORD_ESXI121", "datastore": "D",
                             "ssh_key": "/k"}, headers=ADMIN)
assert r.status_code == 400 and "NO enrolable" in r.get_json()["error"]
print("ok - duplicado 409; validacion en vivo fallida -> 400 (no se enrola)")

# ── seleccion de host en /crear ──────────────────────────────────────────────
# esxi-121 quedo con prioridad 50 (< 100 del principal) → es el preferido AHORA
r = cli.post("/crear", json=dict(BASE, hostname="w1.hosting.cl", whmcs_serviceid="900"), headers=WHMCS)
assert r.status_code == 200, r.get_json()
vm_w = r.get_json()["vm"]
assert fila_host(vm_w) == "esxi-121", "WHMCS debia ir al host de MEJOR PRIORIDAD (esxi-121), fue a %s" % fila_host(vm_w)
print("ok - WHMCS -> host preferido por PRIORIDAD del usuario (esxi-121, prio 50)")

r = cli.post("/crear", json=dict(BASE, hostname="a1.hosting.cl", host="esxi-245"), headers=ADMIN)
assert r.status_code == 200
assert fila_host(r.get_json()["vm"]) == "esxi-245", "admin debia poder FORZAR host"
print("ok - admin fuerza host explicito (esxi-245)")

r = cli.post("/crear", json=dict(BASE, hostname="w2.hosting.cl", whmcs_serviceid="901", host="esxi-245"), headers=WHMCS)
assert r.status_code == 403, "whmcs NO puede elegir host: %s" % r.status_code
r = cli.post("/crear", json=dict(BASE, hostname="a2.hosting.cl", host="esxi-999"), headers=ADMIN)
assert r.status_code == 400
r = cli.patch("/hosts/esxi-121", json={"estado": "pausado"}, headers=ADMIN)
assert r.status_code == 200 and r.get_json()["host"]["estado"] == "pausado"
# NUEVO (2 motores): 'pausado' es del motor de CLIENTES; el gate del motor ADMIN es
# uso_admin. Sin uso_admin, la creación manual del NOC se rechaza (409) ANTES de reservar.
r = cli.patch("/hosts/esxi-121", json={"uso_admin": False}, headers=ADMIN)
assert r.status_code == 200
r = cli.post("/crear", json=dict(BASE, hostname="a3.hosting.cl", host="esxi-121"), headers=ADMIN)
assert r.status_code == 409 and "manual" in r.get_json()["error"], r.get_json()
r = cli.patch("/hosts/esxi-121", json={"uso_admin": True}, headers=ADMIN)   # restaurar
assert r.status_code == 200
print("ok - whmcs con host -> 403; host desconocido -> 400; sin uso_admin -> 409 (motor admin)")

# pausado → el default de CLIENTES vuelve al principal (esxi-245)
r = cli.post("/crear", json=dict(BASE, hostname="w3.hosting.cl", whmcs_serviceid="902"), headers=WHMCS)
assert r.status_code == 200 and fila_host(r.get_json()["vm"]) == "esxi-245", "pausado no debe recibir creaciones de clientes"
print("ok - host pausado no recibe creaciones de clientes (default vuelve al principal)")

# ── cupo PER-HOST ────────────────────────────────────────────────────────────
r = cli.patch("/hosts/esxi-121", json={"estado": "activo", "max_disco_gb": 120}, headers=ADMIN)
assert r.status_code == 200
# ya hay 1 VM de 103 GB en esxi-121 (w1) → otra de 103 excede su limite propio de 120
r = cli.post("/crear", json=dict(BASE, hostname="a4.hosting.cl", host="esxi-121"), headers=ADMIN)
assert r.status_code == 409 and "esxi-121" in r.get_json()["error"] and "excedido" in r.get_json()["error"], r.get_json()
# pero en esxi-245 (limite global 600) SI cabe
r = cli.post("/crear", json=dict(BASE, hostname="a5.hosting.cl", host="esxi-245"), headers=ADMIN)
assert r.status_code == 200
print("ok - cupo PER-HOST: esxi-121 lleno rechaza con su nombre; esxi-245 acepta")

# ── propagacion del host en flujo_eliminar ───────────────────────────────────
capturas = []
motor.power_state = lambda nombre, host=None: capturas.append(("power", (host or {}).get("id"))) or "poweredOff"
motor.govc = lambda *a, timeout=120, host=None: capturas.append((a[0], (host or {}).get("id"))) or ""
motor.esxi_ssh = lambda cmd, timeout=300, host=None: (capturas.append((cmd.split()[0], (host or {}).get("id"))) or
                                                      ("OK 20260921-000000-" + cmd.split()[1] if cmd.startswith("trash-vm") else "OK"))
motor.netbox_ip_del = lambda *a, **k: None
job = motor.Job("eliminar", vm_w, "test", ["v", "a", "u", "n", "s", "p"])
motor.flujo_eliminar(job)
hosts_usados = {h for _, h in capturas}
assert hosts_usados == {"esxi-121"}, "el eliminar debia operar SOLO esxi-121, uso: %s" % hosts_usados
print("ok - flujo_eliminar opera el host DONDE VIVE la VM (esxi-121) en todas las llamadas")

# ── DELETE /hosts ────────────────────────────────────────────────────────────
r = cli.delete("/hosts/esxi-121", headers=ADMIN)
assert r.status_code == 409 and "VM" in r.get_json()["error"], "con VMs (papelera incl.) debe negar"
with motor.DB_LOCK, motor.db() as c:
    c.execute("DELETE FROM vms WHERE host='esxi-121'")
r = cli.delete("/hosts/esxi-121", headers=ADMIN)
assert r.status_code == 200 and motor.host_get("esxi-121") is None
print("ok - DELETE: con VMs 409; sin VMs elimina")

print("\nTESTS MULTI-HOST A2 OK")
