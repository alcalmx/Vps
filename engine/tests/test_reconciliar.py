# Test #11: flujo_reconciliar — deteccion en 4 fuentes, reparacion SOLO NetBox,
# y garantia dura de que JAMAS muta ESXi/RouterOS/boveda.
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

motor.PROVISION_TOKEN = "tok-prov"
motor.NETBOX_URL = "http://netbox.test"
motor.NETBOX_TOKEN = "nbt_x"

# ── Escenario ────────────────────────────────────────────────────────────────
# A: activo, publica, NAT completo, NetBox ids vigentes, vault v1 en boveda -> TODO OK
# B: activo, publica, FALTA dstnat, nb_priv_id MUERTO (404) -> alerta NAT + reparacion NetBox
# C: activo en registro pero SIN VM en ESXi -> alerta
# D: activo modo pruebas (sin publica) -> sin chequeos NAT/NetBox
# E: 'eliminando' sin VM (recuperacion pendiente #7) -> alerta especifica
# F: en transito (job corriendo) y ausente del ESXi -> EXCLUIDA (cero alertas)
# X: VM vps-* en ESXi sin fila -> deriva
# vault: refs v1 (A), v2 (B, NO existe en boveda) -> alerta; v9 huerfana en boveda -> alerta
with motor.DB_LOCK, motor.db() as c:
    c.execute("INSERT INTO vms(nombre,estado,ip,publica,pub_lista,hostname,nb_priv_id,nb_pub_id,vault_item) "
              "VALUES('vps-hcl-0001-a','activo','10.100.16.240','38.19.57.100','Red57-0','a.cl',11,12,'v1')")
    c.execute("INSERT INTO vms(nombre,estado,ip,publica,pub_lista,hostname,nb_priv_id,nb_pub_id,vault_item) "
              "VALUES('vps-hcl-0002-b','activo','10.100.16.241','38.19.57.101','Red57-0','b.cl',666,22,'v2')")
    c.execute("INSERT INTO vms(nombre,estado,ip,hostname) VALUES('vps-hcl-0003-c','activo','10.100.16.242','c.cl')")
    c.execute("INSERT INTO vms(nombre,estado,ip,hostname) VALUES('vps-hcl-0004-d','activo','192.168.122.50','d.cl')")
    c.execute("INSERT INTO vms(nombre,estado,ip,publica,pub_lista,hostname) "
              "VALUES('vps-hcl-0005-e','eliminando','10.100.16.243','38.19.57.102','Red57-0','e.cl')")
    c.execute("INSERT INTO vms(nombre,estado,ip,hostname) VALUES('vps-hcl-0006-f','creando','10.100.16.244','f.cl')")
    c.execute("INSERT INTO jobs(id,tipo,vm,estado,pasos,actor) VALUES('jf','crear','vps-hcl-0006-f','corriendo','[]','t')")

MUTACIONES = []  # cualquier intento de mutar fuentes externas cae aqui -> test FALLA

def fake_esxi_ssh(cmd, timeout=120, host=None):
    if cmd == "list-vps":
        return "vps-hcl-0001-a\nvps-hcl-0002-b\nvps-hcl-0004-d\nvps-hcl-0099-x\nOK"
    MUTACIONES.append("esxi:" + cmd)
    raise AssertionError("reconciliar NO debe llamar esxi_ssh con: " + cmd)

def fake_mikrotik(cmd, host=None, timeout=25):
    if "print terse where" not in cmd or any(w in cmd for w in (" add ", " remove ", " set ")):
        MUTACIONES.append("mikrotik:" + cmd)
        return False, ""
    if "address-list" in cmd:
        if "38.19.57.100" in cmd:
            return True, ' 5 X  list=Red57-0 address=38.19.57.100 comment=a.cl - VPS\n'  # A: TOMADA ok
        return True, ' 9   list=Red57-0 address=38.19.57.101\n'   # B: habilitada y SIN comment -> alerta #22
    if "38.19.57.100" in cmd:
        return True, " 5  chain=srcnat ...\n"          # A: ambas consultas con match
    if "chain=srcnat" in cmd and "38.19.57.101" in cmd:
        return True, " 7  chain=srcnat ...\n"          # B: srcnat si
    return True, ""                                     # B: dstnat FALTA

def fake_netbox_req(method, path, payload=None, timeout=10):
    if method == "GET" and path.endswith("/11/"):
        return {"address": "10.100.16.240/24"}
    if method == "GET" and path.endswith("/12/"):
        return {"address": "38.19.57.100/32"}
    if method == "GET" and path.endswith("/22/"):
        return {"address": "38.19.57.101/32"}
    if method == "GET" and path.endswith("/666/"):
        raise RuntimeError("HTTP 404")
    raise AssertionError("netbox_req inesperado: %s %s" % (method, path))

def fake_netbox_ip_add(cidr, dns, desc):
    return 777 if cidr.startswith("10.100.16.241") else None

def fake_provision_post(path, payload, timeout=60):
    assert path == "/list", "solo /list permitido, llego: " + path
    return [{"id": "v1", "name": "a.cl (38.19.57.100) - cliA"},
            {"id": "v9", "name": "huerfana-vieja"}]

motor.esxi_ssh = fake_esxi_ssh
motor.mikrotik = fake_mikrotik
motor.netbox_req = fake_netbox_req
motor.netbox_ip_add = fake_netbox_ip_add
motor.provision_post = fake_provision_post

with motor.DB_LOCK, motor.db() as c:
    c.execute("UPDATE vms SET host='esxi-245' WHERE host IS NULL")
job = motor.Job("reconciliar", "-", "test", list(motor.PASOS_RECONCILIAR))
motor.flujo_reconciliar(job)

with motor.DB_LOCK, motor.db() as c:
    avisos = [r["detalle"] for r in c.execute("SELECT detalle FROM operaciones WHERE accion='reconciliar'")]
    b_row = dict(c.execute("SELECT nb_priv_id FROM vms WHERE nombre='vps-hcl-0002-b'").fetchone())
texto = "\n".join(avisos)

assert not MUTACIONES, "INTENTO DE MUTACION EXTERNA: %s" % MUTACIONES
assert "vps-hcl-0099-x" in texto and "deriva" in texto, "falta alerta de deriva ESXi"
assert "vps-hcl-0003-c" in texto and "SIN carpeta" in texto, "falta alerta fila sin VM"
assert "vps-hcl-0005-e" in texto and "reintentar 'eliminar'" in texto, "falta alerta de eliminando pendiente"
assert "vps-hcl-0006-f" not in texto, "la VM en transito NO debia alertarse"
assert "NAT INCOMPLETO para vps-hcl-0002-b" in texto and "dstnat=FALTA" in texto, "falta alerta NAT incompleto"
assert "NAT INCOMPLETO para vps-hcl-0001-a" not in texto, "A tiene NAT completo, no debia alertar"
assert "entrada NO" in texto and "38.19.57.101" in texto, "falta alerta #22 de address-list de B"
assert "38.19.57.100 de vps-hcl-0001-a tiene una entrada" not in texto, "A esta TOMADA, no debia alertar #22"
assert str(b_row["nb_priv_id"]) == "777", "NetBox: el id muerto de B debia repararse a 777 (quedo %s)" % b_row["nb_priv_id"]
assert "la llave de vps-hcl-0002-b" in texto and "NO está en la bóveda" in texto, "falta alerta llave inexistente"
assert "huerfana-vieja" in texto, "falta alerta de llave huerfana en boveda"
assert "vps-hcl-0004-d" not in texto, "la VM de pruebas (sin publica) no debia generar alertas"
print("TESTS DE RECONCILIACION OK — deteccion 4 fuentes, reparacion solo NetBox, cero mutaciones externas")
