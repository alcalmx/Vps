# Test de la matriz de permisos por rol (#4/#5) contra la app REAL (test_client).
# flujo_crear se stubbea: nada externo se toca (ni ESXi ni MikroTik ni red).
import os
import sys
import tempfile

os.environ.setdefault("ENGINE_TOKEN", "tok-admin-test")
os.environ.setdefault("WHMCS_TOKEN", "tok-whmcs-test")
os.environ.setdefault("MGMT_PUBKEY", "ssh-ed25519 " + "A" * 68 + " test@test")
os.environ.setdefault("DB_PATH", os.path.join(tempfile.mkdtemp(), "test.db"))
os.environ.setdefault("CONFIG_DIR", r"c:\Users\alcalmx\Desktop\Proyectos\Vps")
os.environ.setdefault("MODO", "pruebas")
os.environ.setdefault("WHMCS_MODO", "pruebas")

os.environ.setdefault("ESXI_HOST", "10.100.37.245")
os.environ.setdefault("GOVC_URL", "https://10.100.37.245/sdk")
os.environ.setdefault("GOVC_USERNAME", "svc-vps")
os.environ.setdefault("GOVC_PASSWORD", "x")
sys.path.insert(0, r"c:\Users\alcalmx\Desktop\Proyectos\Vps\engine")
import app as motor  # noqa: E402

motor.flujo_crear = lambda *a, **k: None  # stub: el hilo del job no hace nada
cli = motor.app.test_client()

ADMIN = {"X-Auth-Token": "tok-admin-test"}
WHMCS = {"X-Auth-Token": "tok-whmcs-test"}
MALO = {"X-Auth-Token": "tok-invalido"}

def chk(cond, msg):
    assert cond, "FALLO: " + msg
    print("ok -", msg)

# health público (sin cambio)
chk(cli.get("/health").status_code == 200, "/health sin token -> 200")

# sin token / token inválido
chk(cli.get("/vms").status_code == 401, "/vms sin token -> 401")
chk(cli.get("/vms", headers=MALO).status_code == 401, "/vms token invalido -> 401")

# admin: todo como siempre
chk(cli.get("/vms", headers=ADMIN).status_code == 200, "/vms admin -> 200")
chk(cli.get("/jobs", headers=ADMIN).status_code == 200, "/jobs admin -> 200")
chk(cli.get("/auditoria", headers=ADMIN).status_code == 200, "/auditoria admin -> 200")

# whmcs: endpoints de NOC vetados (403)
for ep in ("/vms", "/jobs", "/auditoria"):
    chk(cli.get(ep, headers=WHMCS).status_code == 403, ep + " whmcs -> 403")
chk(cli.post("/purgar-papelera", headers=WHMCS).status_code == 403, "/purgar-papelera whmcs -> 403")

# whmcs: los que sí usa el módulo siguen abiertos
chk(cli.get("/vm-por-servicio?serviceid=999", headers=WHMCS).status_code == 200,
    "/vm-por-servicio whmcs -> 200")
chk(cli.get("/job/noexiste", headers=WHMCS).status_code == 404, "/job/<id> whmcs -> pasa auth (404 por id)")

# whmcs en /accion: vm explícito prohibido, serviceid obligatorio
r = cli.post("/accion", json={"vm": "vps-hcl-0001-x", "accion": "suspender"}, headers=WHMCS)
chk(r.status_code == 403, "/accion whmcs con vm explicito -> 403")
r = cli.post("/accion", json={"accion": "suspender"}, headers=WHMCS)
chk(r.status_code == 400, "/accion whmcs sin serviceid -> 400")
r = cli.post("/accion", json={"accion": "suspender", "serviceid": "999999"}, headers=WHMCS)
chk(r.status_code == 404, "/accion whmcs serviceid inexistente -> 404")

# whmcs en /editar: mismas reglas
r = cli.post("/editar", json={"vm": "vps-hcl-0001-x", "sabor": "vps-estandar"}, headers=WHMCS)
chk(r.status_code == 403, "/editar whmcs con vm explicito -> 403")
r = cli.post("/editar", json={"sabor": "vps-estandar"}, headers=WHMCS)
chk(r.status_code == 400, "/editar whmcs sin serviceid -> 400")

# whmcs en /crear: whmcs_serviceid obligatorio + modo del body IGNORADO (usa WHMCS_MODO)
base = {"marca": "hosting.cl", "sabor": "vps-estandar", "cliente": "test", "hostname": "t1.hosting.cl"}
r = cli.post("/crear", json=base, headers=WHMCS)
chk(r.status_code == 400 and "whmcs_serviceid" in r.get_json()["error"],
    "/crear whmcs sin whmcs_serviceid -> 400")
r = cli.post("/crear", json={**base, "modo": "produccion", "whmcs_serviceid": "111"}, headers=WHMCS)
chk(r.status_code == 200 and r.get_json()["modo"] == "pruebas",
    "/crear whmcs con modo=produccion -> creado en WHMCS_MODO=pruebas (override)")

# admin en /crear: el modo del body SE RESPETA (selector del dashboard, sin cambio)
r = cli.post("/crear", json={**base, "hostname": "t2.hosting.cl", "modo": "pruebas"}, headers=ADMIN)
chk(r.status_code == 200 and r.get_json()["modo"] == "pruebas", "/crear admin respeta modo del body")

# la discrepancia de modo del conector quedó auditada
r = cli.get("/auditoria", headers=ADMIN).get_json()
chk(any("WHMCS_MODO" in (op.get("detalle") or "") for op in r["operaciones"]),
    "discrepancia de modo whmcs auditada en operaciones")

print("\nTODOS LOS TESTS DE ROLES PASARON")
