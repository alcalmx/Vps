# Test #7: flujo_eliminar como saga re-ejecutable, con dependencias simuladas.
# Simula un primer intento que muere en el NAT y verifica que el REINTENTO complete.
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

VM = "vps-hcl-0099-saga"
with motor.DB_LOCK, motor.db() as c:
    c.execute("INSERT INTO vms(nombre,marca,sabor,cliente,hostname,estado,ip,publica,pub_lista,host) "
              "VALUES(?,?,?,?,?,?,?,?,?,'esxi-245')",
              (VM, "hosting.cl", "vps-estandar", "t", "t.cl", "activo",
               "10.100.16.240", "38.19.57.101", "Red57-0"))

estado_mundo = {"registrada_esxi": True, "encendida": True, "nat": True,
                "carpeta_en_vps": True, "nat_falla": True}

def fake_power_state(nombre, host=None):
    if not estado_mundo["registrada_esxi"]:
        return "?"
    return "poweredOn" if estado_mundo["encendida"] else "poweredOff"

def fake_apagar(job, nombre, espera=60, host=None):
    estado_mundo["encendida"] = False

def fake_govc(*args, timeout=120, host=None):
    if args[0] == "vm.unregister":
        if not estado_mundo["registrada_esxi"]:
            raise RuntimeError("govc vm.unregister: vm '%s' not found" % args[1])
        estado_mundo["registrada_esxi"] = False
        return ""
    return ""

def fake_borrar_nat(privada, publica, lista, job=None):
    if estado_mundo["nat_falla"]:
        raise RuntimeError("no pude VERIFICAR la reversión del NAT (consulta a RouterData falló)")
    estado_mundo["nat"] = False
    return True

def fake_esxi_ssh(cmd, timeout=120, host=None):
    if cmd.startswith("trash-vm"):
        if not estado_mundo["carpeta_en_vps"]:
            raise RuntimeError("wrapper 'trash-vm' (exit 1): no existe: " + VM)
        estado_mundo["carpeta_en_vps"] = False
        estado_mundo["en_papelera"] = True
        return "OK 20260915-999999-" + VM
    if cmd == "list-vps":
        return (VM + "\nOK") if estado_mundo["carpeta_en_vps"] else "vps-hcl-0001-otro\nOK"
    if cmd == "list-trash":
        return ("20260915-999999-" + VM + "\nOK") if estado_mundo.get("en_papelera") else "OK"
    return ""

motor.power_state = fake_power_state
motor.apagar_graceful = fake_apagar
motor.govc = fake_govc
motor.borrar_nat = fake_borrar_nat
motor.esxi_ssh = fake_esxi_ssh
motor.netbox_ip_del = lambda *a, **k: None

def estado_vm():
    with motor.DB_LOCK, motor.db() as c:
        r = c.execute("SELECT estado, papelera_entrada FROM vms WHERE nombre=?", (VM,)).fetchone()
    return dict(r)

# INTENTO 1: muere en el NAT (RouterData caído) — ya apagó y des-registró
job1 = motor.Job("eliminar", VM, "test", ["v", "a", "u", "n", "s", "p"])
try:
    motor.flujo_eliminar(job1)
    raise SystemExit("FALLO: el intento 1 debía abortar en el NAT")
except RuntimeError as e:
    assert "NAT" in str(e)
e = estado_vm()
assert e["estado"] == "eliminando", "estado tras abortar: %s (esperaba eliminando)" % e["estado"]
assert not estado_mundo["registrada_esxi"] and not estado_mundo["encendida"]
assert estado_mundo["nat"], "el NAT NO debe haberse liberado"
print("ok - intento 1 aborta en NAT: VM des-registrada, estado 'eliminando', NAT intacto")

# INTENTO 2 (reintento con RouterData sano): debe completar pese a apagar/unregister ya hechos
estado_mundo["nat_falla"] = False
job2 = motor.Job("eliminar", VM, "test", ["v", "a", "u", "n", "s", "p"])
motor.flujo_eliminar(job2)
e = estado_vm()
assert e["estado"] == "papelera", "estado final: %s" % e["estado"]
assert not estado_mundo["nat"], "el NAT debía liberarse en el reintento"
assert e["papelera_entrada"].endswith(VM)
print("ok - REINTENTO completa: unregister tolerado, NAT liberado, papelera con entrada")

# INTENTO 3 (doble reintento sobre papelera ya movida): entrada REAL recuperada de list-trash
job3 = motor.Job("eliminar", VM, "test", ["v", "a", "u", "n", "s", "p"])
motor.flujo_eliminar(job3)
e = estado_vm()
assert e["estado"] == "papelera"
assert e["papelera_entrada"] == "20260915-999999-" + VM, "entrada real no recuperada: %s" % e["papelera_entrada"]
print("ok - re-reintento idempotente: entrada REAL recuperada de _papelera via list-trash")

# CASO ENGANOSO: error 'no existe' pero la carpeta SIGUE en VPS/ -> debe re-lanzar
estado_mundo["carpeta_en_vps"] = True
estado_mundo["en_papelera"] = False
real_esxi = motor.esxi_ssh
def esxi_mentiroso(cmd, timeout=120, host=None):
    if cmd.startswith("trash-vm"):
        raise RuntimeError("wrapper 'trash-vm' (exit 1): no existe: " + VM)  # miente
    return real_esxi(cmd, timeout, host=host)
motor.esxi_ssh = esxi_mentiroso
with motor.DB_LOCK, motor.db() as c:
    c.execute("UPDATE vms SET estado='activo' WHERE nombre=?", (VM,))
estado_mundo["registrada_esxi"] = True
estado_mundo["encendida"] = False
job4 = motor.Job("eliminar", VM, "test", ["v", "a", "u", "n", "s", "p"])
try:
    motor.flujo_eliminar(job4)
    raise SystemExit("FALLO: debio detectar el 'no existe' enganoso")
except RuntimeError as ex:
    assert "SIGUE en VPS/" in str(ex), str(ex)
print("ok - error 'no existe' enganoso detectado (la carpeta seguia en VPS/) -> abort seguro")
motor.esxi_ssh = real_esxi

# GUARD DE ESTADO: VM 'eliminando' rechaza suspender/editar (409), permite eliminar
with motor.DB_LOCK, motor.db() as c:
    c.execute("UPDATE vms SET estado='eliminando' WHERE nombre=?", (VM,))
cli = motor.app.test_client()
H = {"X-Auth-Token": "tok-admin-test"}
r = cli.post("/accion", json={"vm": VM, "accion": "suspender"}, headers=H)
assert r.status_code == 409, "suspender sobre eliminando: %s" % r.status_code
r = cli.post("/editar", json={"vm": VM, "sabor": "vps-estandar"}, headers=H)
assert r.status_code == 409, "editar sobre eliminando: %s" % r.status_code
print("ok - VM 'eliminando': suspender y editar -> 409; solo eliminar procede")

print("\nTESTS DE SAGA OK")
