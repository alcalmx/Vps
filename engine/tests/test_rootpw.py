# Tests de la CLAVE DE ROOT para VPS con cPanel (2026-10-01): el motor la genera cuando se
# pide cPanel y no viene ninguna (el dashboard no manda), la entrega SOLO si confirmó su
# aplicación, la oculta a quien no sea admin y la redacta al vencer el TTL.
import json
import os
import sys
import tempfile
import time as _t

os.environ.setdefault("ENGINE_TOKEN", "tok-admin-test")
os.environ.setdefault("WHMCS_TOKEN", "tok-whmcs-test")
os.environ.setdefault("MGMT_PUBKEY", "ssh-ed25519 " + "A" * 68 + " t@t")
os.environ.setdefault("MGMT_PRIVKEY_PATH", "/tmp/fake-mgmt-key")
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
WHMCS = {"X-Auth-Token": "tok-whmcs-test"}

# ── el generador ────────────────────────────────────────────────────────────
pw = motor.password_root_auto()
assert len(pw) == 20, len(pw)
assert ":" not in pw and "\n" not in pw and "\r" not in pw, "rompería chpasswd (root:clave)"
assert len(set(motor.password_root_auto() for _ in range(50))) == 50, "debe ser aleatoria"
assert not (set(pw) & set("lIO01")), "sin caracteres ambiguos"
print("ok - generador: 20 chars, sin ':' ni saltos, aleatoria, sin ambiguos")

# ── qué captura el motor: se guarda lo que REALMENTE se manda a flujo_crear ──
_capt = {}
def _fake_flujo(job, marca, sabor_slug, cliente, hostname, instalar_cpanel, modo,
                pubkey_cliente=None, root_password=None, whmcs_serviceid=None,
                sabor_def=None, host=None, so=None, root_password_auto=False):
    _capt.clear()
    _capt.update(cpanel=instalar_cpanel, pw=root_password, auto=root_password_auto)
motor.flujo_crear = _fake_flujo

BASE = {"marca": "hosting.cl", "cliente": "t", "modo": "pruebas"}

# (1) cPanel SIN clave -> el motor GENERA (es la ruta del dashboard)
r = cli.post("/crear", json=dict(BASE, sabor="vps-estandar", hostname="p1.cl",
                                 instalar_cpanel=True), headers=ADMIN)
assert r.status_code == 200, r.get_json()
assert _capt["auto"] is True and _capt["pw"] and len(_capt["pw"]) == 20
print("ok - cPanel sin clave (dashboard) -> el motor genera una")

# (2) cPanel CON clave -> se respeta EXACTA (ruta de WHMCS; #15: no se modifica)
mia = "  Clave Con Espacios Y $imbolos!  "
r = cli.post("/crear", json=dict(BASE, sabor="vps-estandar", hostname="p2.cl",
                                 instalar_cpanel=True, root_password=mia,
                                 whmcs_serviceid="55"), headers=WHMCS)
assert r.status_code == 200, r.get_json()
assert _capt["pw"] == mia, "la clave suministrada NO se toca"
assert _capt["auto"] is False, "no es autogenerada"
print("ok - clave suministrada se conserva EXACTA y no se marca como generada")

# (3) SIN cPanel -> no se genera nada (el VPS es key-only, no necesita clave)
r = cli.post("/crear", json=dict(BASE, sabor="vps-estandar", hostname="p3.cl",
                                 instalar_cpanel=False), headers=ADMIN)
assert r.status_code == 200, r.get_json()
assert _capt["pw"] is None and _capt["auto"] is False
print("ok - sin cPanel no se genera clave")

# (4) también se genera para WHMCS si pidiera cPanel sin clave (es condición del servicio,
#     no del rol — criterio de Codex)
r = cli.post("/crear", json=dict(BASE, sabor="vps-estandar", hostname="p4.cl",
                                 instalar_cpanel=True, whmcs_serviceid="56"), headers=WHMCS)
assert r.status_code == 200, r.get_json()
assert _capt["auto"] is True and _capt["pw"]
print("ok - WHMCS con cPanel y sin clave: también se genera")

# ── camino de FALLO sin SSH real (lo que pidió Codex en la ronda 2) ─────────
class _JobFalso(object):
    def __init__(self): self.detalles = []
    def detalle(self, t): self.detalles.append(t)

_orig_srp = motor.set_root_password

# (5a) rechazo EXPLÍCITO del guest (chpasswd con rc) -> no confirmada
motor.set_root_password = lambda ip, pw: (_ for _ in ()).throw(
    RuntimeError("chpasswd falló (rc=1) — la clave NO quedó aplicada"))
jf = _JobFalso()
assert motor.aplicar_root_password(jf, "1.2.3.4", "X" * 20, True) is False
assert "no se pudo aplicar" in jf.detalles[0] and "X" * 20 not in jf.detalles[0]
res = motor.entregar_root_password({"vm": "v"}, "X" * 20, True, False)
assert "root_password" not in res and "root_password_error" in res, res
print("ok - rechazo explícito del guest: no se entrega clave, se avisa")

# (5b) TIMEOUT (pudo aplicarse o no) -> tampoco se entrega: no es demostrable
motor.set_root_password = lambda ip, pw: (_ for _ in ()).throw(
    RuntimeError("no se pudo CONFIRMAR la aplicación de la clave (timeout/canal)"))
jf2 = _JobFalso()
assert motor.aplicar_root_password(jf2, "1.2.3.4", "Y" * 20, True) is False
assert motor.entregar_root_password({}, "Y" * 20, True, False).get("root_password") is None
print("ok - timeout sin confirmar: tampoco se entrega la clave")

# (5c) éxito -> confirmada y entregada; el detalle dice que la generó el motor
motor.set_root_password = lambda ip, pw: True
jf3 = _JobFalso()
assert motor.aplicar_root_password(jf3, "1.2.3.4", "Z" * 20, True) is True
assert "GENERADA por el motor" in jf3.detalles[0] and "Z" * 20 not in jf3.detalles[0]
assert motor.entregar_root_password({}, "Z" * 20, True, True)["root_password"] == "Z" * 20
# una clave de WHMCS aplicada NO se devuelve (ya la tiene el cliente en su ficha)
assert motor.entregar_root_password({}, "de-whmcs", False, True) == {}
print("ok - éxito: confirmada y entregada (y la de WHMCS nunca se devuelve)")

motor.set_root_password = _orig_srp

# ── entrega en el resultado: SOLO si se confirmó la aplicación ───────────────
def _res_de_job(resultado):
    """Crea un job con ese resultado y lo lee por la API como admin."""
    j = motor.Job("crear", "vps-hcl-9001-x", "t", ["p"])
    j.set_resultado(resultado)
    return j

j_ok = _res_de_job({"vm": "v", "ssh_cmd": "ssh x", "acceso_ip": "1.2.3.4",
                    "root_password": "SECRETA-APLICADA"})
d = cli.get("/job/%s" % j_ok.id, headers=ADMIN).get_json()
assert d["resultado"]["root_password"] == "SECRETA-APLICADA"
# un rol NO admin no ve el resultado completo (ya era así)
d2 = cli.get("/job/%s" % j_ok.id, headers=WHMCS).get_json()
assert "resultado" not in d2, "WHMCS no debe ver el resultado (lleva secretos)"
print("ok - la clave se entrega al NOC y se oculta a WHMCS")

# ── redacción al vencer el TTL (misma ruta que los secretos de entrega) ──────
viejo = _t.strftime("%Y-%m-%d %H:%M:%S",
                    _t.localtime(_t.time() - (motor.SEND_SECRETO_TTL_DIAS + 1) * 86400))
with motor.DB_LOCK, motor.db() as c:
    c.execute("UPDATE jobs SET created_at=? WHERE id=?", (viejo, j_ok.id))
d3 = cli.get("/job/%s" % j_ok.id, headers=ADMIN).get_json()
assert d3["resultado"]["root_password"] == "(expirado)", d3["resultado"]
print("ok - al vencer el TTL la clave deja de mostrarse (como send_password)")

# el barrido que expira secretos de jobs viejos también la cubre
n = motor.expirar_secretos_jobs()
with motor.DB_LOCK, motor.db() as c:
    guardado = json.loads(c.execute("SELECT resultado FROM jobs WHERE id=?", (j_ok.id,)).fetchone()[0])
assert guardado.get("root_password") == "(expirado)", guardado
print("ok - el barrido de mantención borra la clave vencida del registro (%d job/s)" % n)

print("\nTESTS DE CLAVE DE ROOT OK")
