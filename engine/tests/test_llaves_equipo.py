# Tests LLAVES DEL EQUIPO (2026-10-23): tildes en Crear VPS que agregan al VPS la llave pública de
# alguien del equipo. Catálogo equipo/llaves.json en el motor (el dashboard manda solo ids); se
# instalan en el paso 14 en pruebas y producción; un fallo avisa sin abortar; en uso personal con
# llaves marcadas y sin BYO no se genera llave de entrega.
import json
import os
import sys
import tempfile
import time
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

# ── catálogo del repo ───────────────────────────────────────────────────────
LE = motor.LLAVES_EQUIPO
assert set(LE) == {"alcadio", "fabian", "jonatan"}, sorted(LE)
assert len(LE["alcadio"]["llaves"]) == 1 and LE["alcadio"]["huellas"] == ["SHA256:GEHyGvIXwcGV/hQOXybpAaoFGWGtvQMLEC2FQcoaOdQ"]
assert len(LE["fabian"]["llaves"]) == 2 and LE["jonatan"]["llaves"] == []
h = cli.get("/health", headers=ADMIN).get_json()
eq = {e["id"]: e for e in h["llaves_equipo"]}
assert eq["alcadio"]["disponible"] is True and eq["jonatan"]["disponible"] is False
assert all("llaves" not in e for e in h["llaves_equipo"]), "/health no expone las llaves completas, solo huellas"
assert cli.get("/health", headers=WHMCS).get_json() == {"ok": True}
print("ok - catálogo: Alcadio (YubiKey), Fabián (2 llaves), Jonatan sin llave; /health con huellas, sin llaves")

# ── validación del catálogo ─────────────────────────────────────────────────
KEY = LE["alcadio"]["llaves"][0]
def cat(entradas):
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, "equipo"))
    with open(os.path.join(d, "equipo", "llaves.json"), "w", encoding="utf-8") as fh:
        json.dump({"llaves_equipo": entradas} if entradas is not None else {"x": 1}, fh)
    return d
def falla(entradas, frag):
    try:
        motor.cargar_llaves_equipo(cat(entradas))
        raise AssertionError("debía fallar: %s" % frag)
    except RuntimeError as e:
        assert frag in str(e), (frag, str(e))
assert motor.cargar_llaves_equipo(tempfile.mkdtemp()) == {}, "sin archivo → catálogo vacío"
falla(None, "falta la lista")
falla(["x"], "cada entrada debe ser un objeto")
falla([{"id": "Alcadio", "nombre": "A", "llaves": []}], "id inválido")
falla([{"id": "a\n", "nombre": "A", "llaves": []}], "id inválido")
falla([{"id": "a", "nombre": "A", "llaves": []}, {"id": "a", "nombre": "B", "llaves": []}], "repetido")
falla([{"id": "a", "nombre": " ", "llaves": []}], "sin 'nombre'")
falla([{"id": "a", "nombre": "A", "llaves": KEY}], "debe ser una lista")
falla([{"id": "a", "nombre": "A", "llaves": ['command="/bin/sh" ' + KEY]}], "una línea sin opciones")
falla([{"id": "a", "nombre": "A", "llaves": [KEY + "\nssh-rsa AAAA"]}], "una línea sin opciones")
falla([{"id": "a", "nombre": "A", "llaves": ["ssh-rsa " + "A" * 60 + " roto"]}], "a:")
ok = motor.cargar_llaves_equipo(cat([{"id": "a.b-c", "nombre": "  Ana  ", "llaves": [KEY]}, {"id": "z", "nombre": "Z", "llaves": []}]))
assert ok["a.b-c"]["nombre"] == "Ana" and ok["a.b-c"]["huellas"] == LE["alcadio"]["huellas"] and ok["z"]["llaves"] == []
print("ok - validación del catálogo: ids, duplicados, nombre, lista, solo públicas de una línea sin opciones, huella real")

# ── /crear: validación de llaves_equipo (sin lanzar nada) ───────────────────
base = {"marca": "hosting.cl", "sabor": "personalizado", "cliente": "acme", "hostname": "h1", "vcpu": 2, "ram_mb": 2048,
        "disco_gb": 30, "modo": "produccion", "instalar_cpanel": False, "actor": "alcadio", "uso": "personal"}
# (uso personal = producción siempre: el motor lo fuerza; se deja explícito para que la suite se lea sola — Codex)
for malo, frag, code in (("alcadio", "lista de ids", 400), ([1], "lista de ids", 400), (["x"] * 21, "lista de ids", 400),
                         (["noexiste"], "desconocida", 400), (["jonatan"], "todavía no tiene una llave", 400)):
    r = cli.post("/crear", headers=ADMIN, json=dict(base, llaves_equipo=malo))
    assert r.status_code == code and frag in r.get_json()["error"], (malo, r.get_json())
r = cli.post("/crear", headers=WHMCS, json=dict(base, uso="cliente", sabor="vps-estandar", whmcs_serviceid="9", llaves_equipo=["alcadio"]))
assert r.status_code == 403 and "solo el NOC puede agregar llaves" in r.get_json()["error"], r.get_json()
# Alcadio 2026-10-23: un VPS de CLIENTE no admite llaves del equipo
r = cli.post("/crear", headers=ADMIN, json=dict(base, uso="cliente", llaves_equipo=["alcadio"]))
assert r.status_code == 400 and "solo para VPS de uso personal" in r.get_json()["error"], r.get_json()
print("ok - /crear: llaves_equipo validado (lista de ids, conocidos, con llave cargada, solo uso personal; WHMCS no puede)")

# ── flujo completo con stubs ────────────────────────────────────────────────
capt = {"inst": [], "secv": 0}
class _Cli:
    def set_missing_host_key_policy(self, *a): pass
    def connect(self, *a, **k): pass
    def close(self): pass
class _Pm:
    SSHClient = _Cli
    AutoAddPolicy = object
FALLA_EN = set()
def inst(ip, pub):
    if any(pub == k for i in FALLA_EN for k in LE[i]["llaves"]):
        raise OSError("connection reset")
    capt["inst"].append((ip, pub))
def govc_flujo(*a, timeout=120, host=None):
    if a[0] == "host.portgroup.info":
        return json.dumps({"portgroup": [{"spec": {"name": "VM Network", "vlanId": 0}},
                                         {"spec": {"name": "Sistemas_Internos", "vlanId": 0}},
                                         {"spec": {"name": "Vps_Hosting.cl", "vlanId": 81}}]})
    if a[0] == "vm.ip":
        return "10.0.0.9"
    return ""
def secv(job, nombre, ip, cliente, fqdn):
    capt["secv"] += 1
    return {"url": "https://send/x", "password": "p", "item_id": "it", "id": "s"}
_orig = {k: getattr(motor, k) for k in ("govc", "esxi_ssh", "ip_libre_produccion", "ip_libre_pruebas", "ip_publica_libre",
                                        "crear_nat", "datastore_libre_gb", "listar_wrapper", "netbox_ip_add", "securizar_vps",
                                        "instalar_pubkey_en_vm", "paramiko", "MGMT_PRIVKEY_PATH", "mikrotik")}
os.makedirs("/tmp", exist_ok=True)
def esperar(jid):
    for _ in range(300):
        j = cli.get("/job/%s?usuario=x&total=1" % jid, headers=ADMIN).get_json()
        if j["estado"] != "corriendo":
            return j
        time.sleep(0.05)
    raise AssertionError("job no terminó")
def crear(**kw):
    capt["inst"].clear(); capt["secv"] = 0
    r = cli.post("/crear", headers=ADMIN, json=dict(base, **kw))
    assert r.status_code == 200, r.get_json()
    j = esperar(r.get_json()["job_id"])
    assert j["estado"] == "ok", json.dumps(j, ensure_ascii=False)[:1500]
    return j
try:
    motor.govc = govc_flujo
    motor.esxi_ssh = lambda cmd, timeout=300, host=None: "OK"
    motor.ip_libre_produccion = lambda subred, gateway, job=None: subred.replace("0/24", "250")
    motor.ip_libre_pruebas = lambda subred, gateway, job=None: subred.replace("0/24", "240")
    motor.ip_publica_libre = lambda lista, rango=None, job=None: ("38.19.57.101", [])
    motor.crear_nat = lambda *a, **k: None
    motor.mikrotik = lambda *a, **k: (True, "")
    motor.datastore_libre_gb = lambda host=None: 999
    motor.listar_wrapper = lambda cmd, host=None: [motor.SISTEMAS["almalinux9"]["dorada"]]
    motor.netbox_ip_add = lambda *a: "nb"
    motor.securizar_vps = secv
    motor.instalar_pubkey_en_vm = inst
    motor.paramiko = _Pm
    motor.MGMT_PRIVKEY_PATH = "/fake/gestion"

    # a) uso personal + Alcadio, sin BYO → su llave instalada y SIN llave de entrega
    j = crear(uso="personal", hostname="lab1", modo="produccion", llaves_equipo=["alcadio", "alcadio"])
    assert [p for _, p in capt["inst"]] == [KEY], "una sola vez (ids deduplicados)"
    assert capt["secv"] == 0, "uso personal con llave del equipo: no se genera llave de entrega"
    r = j["resultado"]
    assert r["llaves_equipo"] == ["Alcadio (YubiKey)"] and r["llaves_equipo_fallidas"] == [] and not r.get("send_url")
    assert r["ssh_cmd"] == "ssh root@38.19.57.101", r["ssh_cmd"]
    d14 = j["pasos"][13]["detalle"]
    assert "no se genera llave de entrega" in d14 and "llaves del equipo: instaladas Alcadio (YubiKey)" in d14, d14
    with motor.DB_LOCK, motor.db() as c:
        reg = json.loads(c.execute("SELECT llaves_equipo FROM vms WHERE nombre=?", (j["vm"],)).fetchone()[0])
    assert reg == {"instaladas": ["alcadio"], "fallidas": []}
    print("ok - uso personal + llave de Alcadio: instalada una vez, sin llave de entrega, acceso 'ssh root@<pública>'")

    # b) VPS de cliente sin llaves del equipo → se genera y entrega la del cliente (sin cambios)
    j = crear(uso="cliente", hostname="srv1", modo="produccion")
    assert capt["secv"] == 1 and capt["inst"] == [] and j["resultado"]["llaves_equipo"] == []
    assert j["resultado"]["send_url"] == "https://send/x" and j["resultado"]["ssh_cmd"].startswith("ssh -i id_ed25519")
    print("ok - VPS de cliente: llave del cliente generada y entregada, sin llaves del equipo")

    # c) uso personal + Alcadio y Fabián → las 3 llaves instaladas, sin llave de entrega
    j = crear(hostname="srv2", llaves_equipo=["alcadio", "fabian"])
    assert len(capt["inst"]) == 3 and capt["secv"] == 0 and capt["inst"][0][0] == "10.100.36.250"
    assert "instaladas Alcadio (YubiKey), Fabián" in j["pasos"][13]["detalle"]
    assert j["resultado"]["ssh_cmd"] == "ssh root@38.19.57.101"
    print("ok - uso personal con dos personas del equipo: todas sus llaves instaladas")

    # d) una falla no aborta: aviso en el paso, en el resultado y en la auditoría
    FALLA_EN.add("fabian")
    j = crear(hostname="srv3", llaves_equipo=["alcadio", "fabian"])
    r = j["resultado"]
    assert r["llaves_equipo"] == ["Alcadio (YubiKey)"] and r["llaves_equipo_fallidas"] == ["Fabián"]
    assert "NO se pudo instalar: Fabián" in j["pasos"][13]["detalle"]
    with motor.DB_LOCK, motor.db() as c:
        av = [x[0] for x in c.execute("SELECT detalle FROM operaciones WHERE accion='llaves-equipo' AND resultado='aviso'")]
    assert any("fabian (OSError)" in a for a in av), av
    FALLA_EN.clear()
    print("ok - si una llave falla, el VPS se crea igual y queda el aviso (paso, resultado y auditoría)")

    # d2) Codex: producción + uso personal + sin BYO + TODAS las llaves fallan → no se ofrece una credencial
    #     inexistente: ssh_cmd None, sin_llave_usuario True, sin Send, aviso en el cierre del job
    FALLA_EN.add("alcadio")
    j = crear(uso="personal", hostname="lab3", modo="produccion", llaves_equipo=["alcadio"])
    r = j["resultado"]
    assert r["ssh_cmd"] is None and r["sin_llave_usuario"] is True and r["modo"] == "produccion", r
    assert not r.get("send_url") and capt["secv"] == 0 and r["llaves_equipo_fallidas"] == ["Alcadio (YubiKey)"]
    assert "sin llave de acceso de usuario" in j["pasos"][-1]["detalle"], j["pasos"][-1]["detalle"]
    FALLA_EN.clear()
    # pruebas sin ninguna llave (VPS de cliente): tampoco se inventa 'ssh -i id_ed25519' (antes sí)
    j = crear(uso="cliente", hostname="srv4", modo="pruebas")
    assert j["resultado"]["ssh_cmd"] is None and j["resultado"]["sin_llave_usuario"] is True and j["resultado"]["modo"] == "pruebas"
    # con llave real instalada, sin_llave_usuario es False
    assert crear(hostname="srv5", llaves_equipo=["alcadio"])["resultado"]["sin_llave_usuario"] is False
    print("ok - sin ninguna llave de usuario instalada: sin comando SSH falso, con aviso (producción y pruebas)")

    # e) BYO pegada + llave del equipo en uso personal: la BYO se instala y la del equipo también
    j = crear(uso="personal", hostname="lab2", modo="produccion", llaves_equipo=["alcadio"],
              pubkey_cliente=LE["fabian"]["llaves"][0])
    assert capt["secv"] == 0 and len(capt["inst"]) == 2 and j["resultado"]["byo"] is True
    print("ok - BYO + llave del equipo: ambas instaladas, sin generar llave")
finally:
    for k, v in _orig.items():
        setattr(motor, k, v)
print("TODO OK - test_llaves_equipo")
