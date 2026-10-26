# Tests del modo «SOLO VPS INTERNOS» (Alcadio 2026-10-26): un usuario del dashboard SIN permiso de VPS de clientes
# (Gerardo, Cristian) solo ve y opera SUS VPS de uso personal. El proxy del dashboard lo afirma con solo_personal
# (query en /vms, /jobs, /job; campo en /crear, /accion, /editar). Quien tiene el permiso no cambia nada.
import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
os.environ["ENGINE_TOKEN"] = "tok-admin-test"
os.environ["WHMCS_TOKEN"] = "tok-whmcs-test"
os.environ["MGMT_PUBKEY"] = "ssh-ed25519 " + "A" * 68 + " t@t"
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.db")
os.environ["CONFIG_DIR"] = str(RAIZ)
os.environ["ESXI_HOST"] = "10.100.37.245"
os.environ["GOVC_URL"] = "https://10.100.37.245/sdk"
os.environ["GOVC_USERNAME"] = "svc-vps"
os.environ["GOVC_PASSWORD"] = "x"
os.environ["MGMT_PRIVKEY_PATH"] = ""
os.environ.pop("MODO", None)
sys.path.insert(0, str(RAIZ / "engine"))
import app as motor  # noqa: E402

assert motor.DB_PATH == os.environ["DB_PATH"], "esta suite debe correr en su propio proceso"
cli = motor.app.test_client()
ADMIN = {"X-Auth-Token": "tok-admin-test"}
WHMCS = {"X-Auth-Token": "tok-whmcs-test"}
ERR = motor.ERR_SOLO_PERSONAL
ERR_OP = motor.ERR_SOLO_OPERAR

# ── datos: VPS y jobs de cliente, personales de gerardo y de fabian, y un job del sistema ──────────
VMS = [("vps-int-0010-gerardo", "interno", "personal", "gerardo", "activo"),
       ("vps-int-0011-gerardo", "interno", "personal", "gerardo", "creando"),
       ("vps-int-0012-fabian", "interno", "personal", "fabian", "activo"),
       ("vps-hcl-0010-acme", "hosting.cl", None, None, "activo"),
       ("vps-hcl-0011-acme", "hosting.cl", "cliente", None, "creando"),
       ("vps-hcl-0012-viejo", "hosting.cl", None, None, "papelera"),
       ("vps-int-0013-gerardo", "interno", "personal", "gerardo", "papelera")]
with motor.DB_LOCK, motor.db() as c:
    for nombre, marca, uso, dueno, estado in VMS:
        c.execute("INSERT INTO vms(nombre,marca,sabor,cliente,estado,ip,uso,propietario) VALUES(?,?,?,?,?,?,?,?)",
                  (nombre, marca, "vps-estandar", dueno or "acme", estado, "10.0.0.1", uso, dueno))
    for jid, vm, uso, dueno, tipo in [("j-ger-00001", "vps-int-0010-gerardo", "personal", "gerardo", "crear"),
                                      ("j-fab-00001", "vps-int-0012-fabian", "personal", "fabian", "crear"),
                                      ("j-cli-00001", "vps-hcl-0010-acme", None, None, "crear"),
                                      ("j-sis-00001", "", None, None, "reconciliar")]:
        c.execute("INSERT INTO jobs(id,tipo,vm,estado,pasos,actor,motor,uso,propietario) VALUES(?,?,?,?,?,?,?,?,?)",
                  (jid, tipo, vm, "ok", "[]", dueno or "fabian", "admin", uso, dueno))
    # 61 jobs de clientes MÁS NUEVOS: el filtro de solo_personal va en el SQL, antes del LIMIT
    for i in range(61):
        c.execute("INSERT INTO jobs(id,tipo,vm,estado,pasos,actor,motor,uso,propietario,created_at) "
                  "VALUES(?,?,?,?,?,?,?,?,?,datetime('now','localtime','+1 hour'))",
                  ("cli%08d" % i, "crear", "vps-hcl-9%03d-x" % i, "ok", "[]", "fabian", "admin", None, None))


def vms(q):
    d = cli.get("/vms?" + q, headers=ADMIN).get_json()
    return sorted(v["nombre"] for v in d["vms"]), sorted(p["nombre"] for p in d["papelera"])


def jobs(q):
    return [j["id"] for j in cli.get("/jobs?" + q, headers=ADMIN).get_json()["jobs"]]


# ── ver ───────────────────────────────────────────────────────────────────────────────────────
v, pap = vms("usuario=gerardo&total=0&solo_personal=1")
assert v == ["vps-int-0010-gerardo", "vps-int-0011-gerardo"] and pap == ["vps-int-0013-gerardo"], (v, pap)
v, pap = vms("usuario=fabian&total=0")   # con permiso: ve los de clientes y sus personales (como antes)
assert "vps-hcl-0010-acme" in v and "vps-int-0012-fabian" in v and "vps-int-0010-gerardo" not in v, v
assert "vps-hcl-0012-viejo" in pap, pap
j = jobs("usuario=gerardo&total=0&solo_personal=1")
assert j == ["j-ger-00001"], "solo sus jobs personales, aunque haya 61 de clientes más nuevos: %r" % j[:5]
assert "j-sis-00001" not in j and "j-cli-00001" not in j
jf = jobs("usuario=fabian&total=0")
assert any(x.startswith("cli") for x in jf) and "j-ger-00001" not in jf, "con permiso: clientes visibles, personales ajenos no"
r = cli.get("/job/j-cli-00001?usuario=gerardo&total=0&solo_personal=1", headers=ADMIN)
assert r.status_code == 403 and r.get_json()["error"] == ERR, r.get_json()
r = cli.get("/job/j-fab-00001?usuario=gerardo&total=0&solo_personal=1", headers=ADMIN)
assert r.status_code == 403
assert cli.get("/job/j-ger-00001?usuario=gerardo&total=0&solo_personal=1", headers=ADMIN).status_code == 200
assert cli.get("/job/j-cli-00001?usuario=fabian&total=0", headers=ADMIN).status_code == 200
# job inexistente: con solo_personal responde igual que uno ajeno (no se sondea); sin solo, el 404 de siempre
r = cli.get("/job/ffffffffffff?usuario=gerardo&total=0&solo_personal=1", headers=ADMIN)
assert r.status_code == 403 and r.get_json()["error"] == ERR, r.get_json()
assert cli.get("/job/ffffffffffff?usuario=fabian&total=0", headers=ADMIN).status_code == 404
# falla cerrado: cualquier valor que no sea un "no" explícito restringe; "0"/"false" no
assert vms("usuario=gerardo&total=0&solo_personal=si")[0] == ["vps-int-0010-gerardo", "vps-int-0011-gerardo"]
assert "vps-hcl-0010-acme" in vms("usuario=gerardo&total=0&solo_personal=0")[0]
assert "vps-hcl-0010-acme" in vms("usuario=gerardo&total=0&solo_personal=false")[0]
# solo + total (no debería llegar, el superadmin tiene todos los permisos): gana lo restrictivo
assert vms("usuario=gerardo&total=1&solo_personal=1")[0] == ["vps-int-0010-gerardo", "vps-int-0011-gerardo"]
print("ok - ver: con solo_personal, /vms (y papelera), /jobs y /job muestran solo los VPS personales propios")

# ── crear ─────────────────────────────────────────────────────────────────────────────────────
motor.flujo_crear = lambda *a, **k: None
BASE = {"marca": "hosting.cl", "cliente": "t", "modo": "pruebas", "sabor": "vps-estandar", "hostname": "h.cl", "actor": "gerardo"}
for extra in ({"uso": "cliente"}, {}):          # uso ausente = cliente
    r = cli.post("/crear", headers=ADMIN, json=dict(BASE, solo_personal=True, **extra))
    assert r.status_code == 403 and r.get_json()["error"] == ERR, r.get_json()
r = cli.post("/crear", headers=ADMIN, json=dict(BASE, solo_personal="raro", uso="cliente"))
assert r.status_code == 403, "un valor raro también restringe (falla cerrado)"
r = cli.post("/crear", headers=ADMIN, json=dict(BASE, uso="personal", modo="produccion", solo_personal=True))
assert not (r.status_code == 403 and (r.get_json() or {}).get("error") == ERR), "uso personal sí se permite: %r" % r.get_json()
r = cli.post("/crear", headers=ADMIN, json=dict(BASE, uso="cliente", hostname="h2.cl"))
assert r.status_code == 200, "sin solo_personal (con permiso) crear para un cliente sigue igual: %r" % r.get_json()
print("ok - crear: con solo_personal, un VPS para cliente se rechaza (403); uso personal sigue permitido")

# ── operar ────────────────────────────────────────────────────────────────────────────────────
def accion(vm, acc, actor="gerardo", **extra):
    return cli.post("/accion", headers=ADMIN, json=dict({"vm": vm, "accion": acc, "actor": actor}, **extra))


# VPS de cliente: 403 ANTES de validar el estado (no revela si está 'creando' u otro estado)
r = accion("vps-hcl-0011-acme", "suspender", solo_personal=True)
assert r.status_code == 403 and r.get_json()["error"] == ERR_OP, r.get_json()
r = accion("vps-hcl-0010-acme", "eliminar", solo_personal=True, confirmar="vps-hcl-0010-acme")
assert r.status_code == 403 and r.get_json()["error"] == ERR_OP
r = accion("vps-int-0012-fabian", "suspender", solo_personal=True)
assert r.status_code == 403 and r.get_json()["error"] == ERR_OP, "personal ajeno: tampoco"
# «no existe» = «no es tuyo» (Codex r1 #5): VM inexistente, nombre fuera de convención y serviceid, misma respuesta
for vm in ("vps-hcl-0999-nada", "no-es-un-vps"):
    r = accion(vm, "suspender", solo_personal=True)
    assert r.status_code == 403 and r.get_json()["error"] == ERR_OP, (vm, r.get_json())
    r = cli.post("/editar", headers=ADMIN, json={"vm": vm, "sabor": "vps-empresas", "actor": "gerardo", "solo_personal": True})
    assert r.status_code == 403 and r.get_json()["error"] == ERR_OP, (vm, r.get_json())
for ep, extra in (("/accion", {"accion": "suspender"}), ("/editar", {"sabor": "vps-empresas"})):
    for sid in ("1", "999999"):
        r = cli.post(ep, headers=ADMIN, json=dict(extra, serviceid=sid, actor="gerardo", solo_personal=True))
        assert r.status_code == 403 and r.get_json()["error"] == ERR_OP, (ep, sid, r.get_json())
# sin solo_personal se conservan los mensajes de siempre (400 inexistente / 404 serviceid)
assert accion("vps-hcl-0999-nada", "suspender", actor="fabian").status_code == 400
assert cli.post("/accion", headers=ADMIN, json={"serviceid": "999999", "accion": "suspender", "actor": "fabian"}).status_code == 404
# su propio VPS personal pasa el control (aquí lo frena el estado 'creando': 409, no 403)
r = accion("vps-int-0011-gerardo", "suspender", solo_personal=True)
assert r.status_code == 409, r.get_json()
# sin solo_personal (con permiso), un VPS de cliente 'creando' da el 409 de siempre
assert accion("vps-hcl-0011-acme", "suspender", actor="fabian").status_code == 409
# admin_total no salta la restricción
r = accion("vps-hcl-0011-acme", "suspender", solo_personal=True, admin_total=True)
assert r.status_code == 403
r = cli.post("/editar", headers=ADMIN, json={"vm": "vps-hcl-0010-acme", "sabor": "vps-empresas", "actor": "gerardo",
                                             "solo_personal": True})
assert r.status_code == 403 and r.get_json()["error"] == ERR_OP, r.get_json()
r = cli.post("/editar", headers=ADMIN, json={"vm": "vps-int-0010-gerardo", "sabor": "no-existe", "actor": "gerardo",
                                             "solo_personal": True})
assert r.status_code != 403, "su VPS personal pasa el control de dueño: %r" % r.get_json()
# el token de WHMCS no puede afirmar nada (parámetros ignorados) y opera solo por serviceid
assert cli.get("/jobs?usuario=gerardo&solo_personal=1", headers=WHMCS).status_code == 403
print("ok - operar: con solo_personal, /accion y /editar solo sobre SUS VPS personales; inexistente = ajeno (no se sondea)")
