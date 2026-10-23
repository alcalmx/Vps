# Tests USO PERSONAL vs CLIENTE (2026-10-23): un VPS de uso personal va a la red interna
# (marca uso=interno, 10.100.36.0/24) sin IP pública ni NAT, lleva dueño, solo su dueño (o un
# superadmin con total=1) lo ve en /vms, /jobs y /job y lo opera en /accion y /editar; suspender
# lo apaga y reanudar lo enciende. Un VPS de cliente es el de siempre.
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
os.environ["MGMT_PRIVKEY_PATH"] = ""      # sin verificación SSH real
os.environ.pop("MODO", None)
sys.path.insert(0, str(RAIZ / "engine"))
import app as motor  # noqa: E402

assert motor.DB_PATH == os.environ["DB_PATH"], "esta suite debe correr en su propio proceso"
cli = motor.app.test_client()
ADMIN = {"X-Auth-Token": "tok-admin-test"}
WHMCS = {"X-Auth-Token": "tok-whmcs-test"}

# ── catálogo: la red interna existe, no es comercial, hereda los planes ─────
assert motor.MARCA_INTERNA == "interno"
assert "interno" in motor.MARCAS and "interno" not in motor.marcas_activas()
mi = motor.MARCAS["interno"]
assert mi["uso"] == "interno" and mi["prefijo_vm"] == "vps-int" and mi["pub_lista"] is None
assert mi["red_default"]["subred"] == "10.100.36.0/24" and mi["red_default"]["vlan"] == 0
assert ("interno", "vps-estandar") in motor.SABORES and motor.SABORES[("interno", "vps-estandar")]["heredado_de"] == "hosting.cl"
h = cli.get("/health", headers=ADMIN).get_json()
assert "interno" not in h["marcas"], "la red interna NO va en el selector de marcas comerciales"
det = {d["marca"]: d for d in h["marcas_detalle"]}
assert det["interno"]["uso"] == "interno" and det["interno"]["red_default"]["subred"] == "10.100.36.0/24"
assert det["interno"]["pub_lista"] is None and det["interno"]["sabores"] == det["hosting.cl"]["sabores"]
assert all(d["uso"] == "cliente" for m, d in det.items() if m != "interno")
# el validador exige VLAN a las marcas comerciales pero no a la interna; 'uso' raro falla; alias válidos
def marca_ok(mid="demo.cl", **cambios):
    m = {"marca": mid, "nombre": "Demo", "prefijo_vm": "vps-demo", "activo": True, "pub_lista": "Red107-0",
         "dominio_hostname": mid,
         "red_default": {"subred": "10.100.90.0/24", "gateway": "10.100.90.1", "dns": ["1.1.1.1"],
                         "portgroup": "Vps_Demo", "vlan": 90},
         "red_pruebas": {"subred": "192.168.200.0/24", "gateway": "192.168.200.1", "dns": ["1.1.1.1"],
                         "portgroup": "VM Network", "vlan": 0}}
    m.update(cambios)
    return m
def falla(marcas, *frag):
    try:
        motor.validar_marcas(marcas)
        raise AssertionError("debía fallar: %s" % (frag,))
    except RuntimeError as e:
        for f in frag:
            assert f in str(e), (f, str(e))
falla({"demo.cl": marca_ok(uso="personal")}, "'uso'")
m = marca_ok(uso="interno"); m["red_default"]["vlan"] = 0
assert motor.validar_marcas({"demo.cl": m}), "la red interna acepta vlan 0"
m = marca_ok(); m["red_default"]["vlan"] = 0
falla({"demo.cl": m}, "red_default: 'vlan'", "1-4094")
m = marca_ok(); m["red_default"]["portgroups"] = ["A", ""]
falla({"demo.cl": m}, "'portgroups'")
m = marca_ok(); m["red_default"]["portgroups"] = ["Vps_Demo", "vps_demo_alias"]
assert motor.validar_marcas({"demo.cl": m})
print("ok - red interna en el catálogo: uso=interno, sin VLAN ni lista pública, fuera del selector, planes heredados")

# ── portgroup por alias (la red interna no tiene VLAN → solo por nombre) ────
HOST = {"id": "esxi-x", "api_url": "https://x/sdk", "govc_user": "svc-vps", "datastore": "ds", "pass_env": "P"}
_govc_real = motor.govc
def con_host(pgs, ls=None, info_falla=False):
    def fake(*args, timeout=120, host=None):
        if args[0] == "host.portgroup.info":
            if info_falla:
                raise RuntimeError("x")
            return json.dumps({"portgroup": [{"spec": {"name": n, "vlanId": v}} for n, v in pgs]})
        if args[:2] == ("ls", "network"):
            return "\n".join("/ha-datacenter/network/%s" % n for n in (ls if ls is not None else [n for n, _ in pgs]))
        raise AssertionError(args)
    return fake
RED_INT = motor.MARCAS["interno"]["red_default"]
try:
    motor.govc = con_host([("VM Network", 0), ("Sistemas Internos", 0)])
    assert motor.resolver_portgroup(HOST, RED_INT, "produccion") == ("Sistemas Internos", ""), "alias por nombre"
    motor.govc = con_host([("VM Network", 0), ("Sistemas_Internos", 0), ("Sistemas Internos", 0)])
    assert motor.resolver_portgroup(HOST, RED_INT, "produccion")[0] == "Sistemas_Internos", "el principal gana"
    motor.govc = con_host([("VM Network", 0), ("Sistemas Internos", 85)])
    try:
        motor.resolver_portgroup(HOST, RED_INT, "produccion")
        raise AssertionError("alias en VLAN 85 debía rechazarse (la red interna va sin etiqueta)")
    except RuntimeError as e:
        assert "VLAN 85" in str(e) and "sin etiqueta" in str(e), str(e)
    motor.govc = con_host([("VM Network", 0), ("Vps_Hosting.cl", 81)])
    try:
        motor.resolver_portgroup(HOST, RED_INT, "produccion")
        raise AssertionError("sin ningún nombre aceptable debía fallar")
    except RuntimeError as e:
        assert "NO tiene el portgroup 'Sistemas_Internos'" in str(e) and "ni otro en la VLAN" not in str(e), str(e)
    motor.govc = con_host([], ls=["Sistemas Internos"], info_falla=True)
    assert motor.resolver_portgroup(HOST, RED_INT, "produccion")[0] == "Sistemas Internos", "alias también en el fallback"
finally:
    motor.govc = _govc_real
print("ok - portgroup de la red interna por nombre/alias, sin adivinar por VLAN 0")

# ── /crear: validaciones del uso personal (sin lanzar nada) ─────────────────
base = {"sabor": "personalizado", "cliente": "x", "hostname": "h.cl", "vcpu": 2, "ram_mb": 2048, "disco_gb": 30,
        "modo": "pruebas", "instalar_cpanel": False}
r = cli.post("/crear", headers=ADMIN, json=dict(base, marca="hosting.cl", uso="raro"))
assert r.status_code == 400 and "uso inválido" in r.get_json()["error"]
r = cli.post("/crear", headers=WHMCS, json=dict(base, marca="hosting.cl", uso="personal", whmcs_serviceid="123"))
assert r.status_code == 403 and "solo para el NOC" in r.get_json()["error"], r.get_json()
r = cli.post("/crear", headers=ADMIN, json=dict(base, marca="hosting.cl", uso="personal"))
assert r.status_code == 400 and "usuario del dashboard identificado" in r.get_json()["error"], r.get_json()
r = cli.post("/crear", headers=ADMIN, json=dict(base, marca="hosting.cl", uso="personal", actor="gerardo", whmcs_serviceid="5"))
assert r.status_code == 400 and "no se asocia a un servicio de WHMCS" in r.get_json()["error"], r.get_json()
r = cli.post("/crear", headers=ADMIN, json=dict(base, marca="interno", uso="cliente", actor="gerardo"))
assert r.status_code == 400 and "solo para VPS de uso personal" in r.get_json()["error"], r.get_json()
_mi = motor.MARCA_INTERNA
motor.MARCA_INTERNA = None
r = cli.post("/crear", headers=ADMIN, json=dict(base, marca="hosting.cl", uso="personal", actor="gerardo"))
assert r.status_code == 409 and "no hay red interna" in r.get_json()["error"], r.get_json()
motor.MARCA_INTERNA = _mi
print("ok - /crear: uso inválido 400, personal solo NOC con usuario, sin WHMCS, red interna solo para personal")

# ── creación PERSONAL completa (modo producción) con todo lo externo stubbeado ──
capt = {}
def govc_flujo(*a, timeout=120, host=None):
    if a[0] == "host.portgroup.info":
        return json.dumps({"portgroup": [{"spec": {"name": "VM Network", "vlanId": 0}},
                                         {"spec": {"name": "Sistemas_Internos", "vlanId": 0}}]})
    if a[0] == "datastore.upload":
        with open(a[3], encoding="utf-8") as fh:
            capt["vmx"] = fh.read()
        return ""
    if a[0] == "vm.ip":
        return "10.100.36.250"
    if a[0] == "vm.power":
        capt.setdefault("power", []).append(a[1])
        return ""
    return ""
def mk_no(*a, **k):
    raise AssertionError("un VPS personal NO debe tocar RouterData: %r" % (a,))
_orig = {k: getattr(motor, k) for k in ("govc", "esxi_ssh", "ip_libre_produccion", "ip_publica_libre", "crear_nat",
                                        "datastore_libre_gb", "listar_wrapper", "netbox_ip_add", "securizar_vps",
                                        "mikrotik", "power_state")}
os.makedirs("/tmp", exist_ok=True)
try:
    motor.govc = govc_flujo
    motor.esxi_ssh = lambda cmd, timeout=300, host=None: "OK"
    motor.ip_libre_produccion = lambda subred, gateway, job=None: subred.replace("0/24", "250")
    motor.ip_publica_libre = mk_no
    motor.crear_nat = mk_no
    motor.mikrotik = mk_no
    motor.datastore_libre_gb = lambda host=None: 999
    motor.listar_wrapper = lambda cmd, host=None: [motor.SISTEMAS["almalinux9"]["dorada"]]
    motor.netbox_ip_add = lambda cidr, dns, desc: capt.setdefault("netbox", []).append((cidr, dns, desc)) or "nb%d" % len(capt["netbox"])
    motor.securizar_vps = lambda job, nombre, ip, cliente, fqdn: {"url": "https://send/x", "password": "p", "item_id": "it1", "id": "s1"}
    motor.power_state = lambda nombre, host=None: capt.get("estado_power", "poweredOff")
    r = cli.post("/crear", headers=ADMIN, json={"marca": "hosting.cl", "sabor": "vps-estandar", "cliente": "",
                                                "hostname": "lab-gerardo", "modo": "produccion",
                                                "instalar_cpanel": False, "uso": "personal", "actor": "gerardo"})
    assert r.status_code == 200, r.get_json()
    jid, nombre = r.get_json()["job_id"], r.get_json().get("vm") or r.get_json().get("nombre")
    for _ in range(300):
        j = cli.get("/job/%s?usuario=gerardo&total=0" % jid, headers=ADMIN).get_json()
        if j["estado"] != "corriendo":
            break
        time.sleep(0.1)
    assert j["estado"] == "ok", json.dumps(j, ensure_ascii=False)[:1500]
    pasos = "\n".join(p["detalle"] for p in j["pasos"])
    nombre = j["vm"]
    assert nombre == "vps-int-0001-gerardo", nombre
    assert "USO PERSONAL de gerardo" in pasos and "Uso interno (sistemas internos): red 10.100.36.0/24" in pasos, pasos
    assert "uso personal: sin IP pública ni NAT (red interna 10.100.36.0/24)" in pasos, pasos
    assert 'ethernet0.networkName = "Sistemas_Internos"' in capt["vmx"]
    assert capt["netbox"] and capt["netbox"][0][0] == "10.100.36.250/24" and len(capt["netbox"]) == 1, capt["netbox"]
    res = j["resultado"]
    assert res["publica"] is None and res["privada"] == "10.100.36.250" and res["acceso_ip"] == "10.100.36.250"
    assert res["send_url"] == "https://send/x", "la llave se entrega al dueño por la bóveda"
    reg = motor.vm_registrada(nombre)
    assert reg["estado"] == "activo" and reg["uso"] == "personal" and reg["propietario"] == "gerardo"
    assert reg["marca"] == "interno" and reg["publica"] is None and reg["ip"] == "10.100.36.250"
    assert reg["cliente"] == "gerardo", "sin cliente → el dueño va en el nombre"
    with motor.DB_LOCK, motor.db() as c:
        jrow = dict(c.execute("SELECT uso,propietario FROM jobs WHERE id=?", (jid,)).fetchone())
    assert jrow == {"uso": "personal", "propietario": "gerardo"}
    print("ok - creación de uso personal: red interna, sin pública/NAT/RouterData, NetBox solo privada, llave por bóveda, dueño registrado")

    # ── visibilidad: solo el dueño (o total=1) ───────────────────────────────
    ver = lambda u, t: [v["nombre"] for v in cli.get("/vms?usuario=%s&total=%d" % (u, t), headers=ADMIN).get_json()["vms"]]
    assert nombre in ver("gerardo", 0) and nombre not in ver("jonatan", 0) and nombre in ver("jonatan", 1) and nombre not in ver("", 0)
    jobs = lambda u, t: [x["id"] for x in cli.get("/jobs?usuario=%s&total=%d" % (u, t), headers=ADMIN).get_json()["jobs"]]
    assert jid in jobs("gerardo", 0) and jid not in jobs("jonatan", 0) and jid in jobs("alcadio", 1)
    r = cli.get("/job/%s?usuario=jonatan&total=0" % jid, headers=ADMIN)
    assert r.status_code == 403 and "uso personal" in r.get_json()["error"]
    assert cli.get("/job/%s?usuario=jonatan&total=1" % jid, headers=ADMIN).status_code == 200
    assert cli.get("/job/%s" % jid, headers=WHMCS).status_code == 403, "el conector WHMCS no ve jobs personales"
    # Codex bloqueante #1: con token WHMCS los parámetros de vista se IGNORAN (no puede afirmar identidad ni total)
    for q in ("?total=1", "?usuario=gerardo", "?usuario=gerardo&total=1", "?total=true"):
        assert cli.get("/job/%s%s" % (jid, q), headers=WHMCS).status_code == 403, q
    assert cli.get("/jobs?total=1", headers=WHMCS).status_code == 403 and cli.get("/vms?total=1", headers=WHMCS).status_code == 403
    # papelera: también filtrada por dueño
    with motor.DB_LOCK, motor.db() as c:
        c.execute("UPDATE vms SET estado='papelera', papelera_entrada='20261023-000000-%s' WHERE nombre=?" % nombre, (nombre,))
    pap = lambda u, t: [p["nombre"] for p in cli.get("/vms?usuario=%s&total=%d" % (u, t), headers=ADMIN).get_json()["papelera"]]
    assert nombre in pap("gerardo", 0) and nombre not in pap("jonatan", 0) and nombre in pap("jonatan", 1)
    with motor.DB_LOCK, motor.db() as c:
        c.execute("UPDATE vms SET estado='activo', papelera_entrada=NULL WHERE nombre=?", (nombre,))
    # Codex medio #4: 61 jobs personales AJENOS recientes no vacían la lista de los demás (filtro antes del LIMIT)
    with motor.DB_LOCK, motor.db() as c:
        for i in range(61):
            c.execute("INSERT INTO jobs(id,tipo,vm,estado,pasos,actor,motor,uso,propietario,created_at) "
                      "VALUES(?,?,?,?,?,?,?,?,?,datetime('now','localtime','+1 hour'))",
                      ("ajeno%07d" % i, "crear", "vps-int-9%03d-otro" % i, "ok", "[]", "otro", "admin", "personal", "otro"))
    assert jid in jobs("gerardo", 0), "los jobs propios siguen visibles aunque haya 61 personales ajenos más nuevos"
    assert not any(x.startswith("ajeno") for x in jobs("gerardo", 0)) and any(x.startswith("ajeno") for x in jobs("x", 1))
    with motor.DB_LOCK, motor.db() as c:
        c.execute("DELETE FROM jobs WHERE id LIKE 'ajeno%'")
    print("ok - visibilidad: /vms (y papelera), /jobs y /job solo para el dueño o un superadmin; WHMCS no puede afirmar identidad; filtro antes del LIMIT")

    # ── operar: solo el dueño o superadmin; suspender apaga, reanudar enciende ──
    r = cli.post("/accion", headers=ADMIN, json={"vm": nombre, "accion": "suspender", "actor": "jonatan"})
    assert r.status_code == 403 and "solo su dueño" in r.get_json()["error"], r.get_json()
    r = cli.post("/editar", headers=ADMIN, json={"vm": nombre, "sabor": "vps-empresas", "actor": "jonatan"})
    assert r.status_code == 403 and "solo su dueño" in r.get_json()["error"], r.get_json()
    r = cli.post("/accion", headers=WHMCS, json={"serviceid": "1", "accion": "suspender"})
    assert r.status_code == 404, "WHMCS no resuelve VMs personales (no tienen serviceid)"
    def esperar(jid2):
        for _ in range(200):
            j2 = cli.get("/job/%s?usuario=x&total=1" % jid2, headers=ADMIN).get_json()
            if j2["estado"] != "corriendo":
                return j2
            time.sleep(0.05)
        raise AssertionError("job no terminó")
    r = cli.post("/accion", headers=ADMIN, json={"vm": nombre, "accion": "suspender", "actor": "gerardo"})
    assert r.status_code == 200, r.get_json()
    j2 = esperar(r.get_json()["job_id"])
    assert j2["estado"] == "ok" and [p["nombre"] for p in j2["pasos"]][1].startswith("Apagar la VM"), j2["pasos"]
    assert "-s" in capt["power"] and motor.vm_registrada(nombre)["estado"] == "suspendido"
    assert j2["uso"] == "personal" and j2["propietario"] == "gerardo", "el job de acción hereda el dueño"
    r = cli.post("/accion", headers=ADMIN, json={"vm": nombre, "accion": "reanudar", "actor": "alcadio", "admin_total": True})
    assert r.status_code == 200, "un superadmin sí puede operarlo: %s" % r.get_json()
    j3 = esperar(r.get_json()["job_id"])
    assert j3["estado"] == "ok" and capt["power"][-1] == "-on" and motor.vm_registrada(nombre)["estado"] == "activo"
    r = cli.post("/accion", headers=ADMIN, json={"vm": nombre, "accion": "reanudar", "actor": "jonatan", "admin_total": "1"})
    assert r.status_code == 200, "admin_total lo afirma el proxy (solo lo pone para superadmin)"
    esperar(r.get_json()["job_id"])
    print("ok - operar: 403 para otros usuarios, el dueño suspende (apaga) y reanuda (enciende), superadmin también")

    # Codex medio #3: el job de EDICIÓN nace ya con dueño (sin ventana en que otro lo vea)
    r = cli.post("/editar", headers=ADMIN, json={"vm": nombre, "sabor": "vps-empresas", "actor": "gerardo"})
    assert r.status_code == 200, r.get_json()
    jed = r.get_json()["job_id"]
    with motor.DB_LOCK, motor.db() as c:
        row = dict(c.execute("SELECT uso,propietario FROM jobs WHERE id=?", (jed,)).fetchone())
    assert row == {"uso": "personal", "propietario": "gerardo"}, row
    assert cli.get("/job/%s?usuario=jonatan&total=0" % jed, headers=ADMIN).status_code == 403
    je = esperar(jed)
    assert je["estado"] == "ok", "editar un VPS personal debe funcionar (Codex r2): %s" % json.dumps(je, ensure_ascii=False)[:1200]
    rege = motor.vm_registrada(nombre)
    assert rege["sabor"] == "vps-empresas" and rege["estado"] == "activo" and rege["uso"] == "personal", dict(rege)
    print("ok - el job de edición de un VPS personal nace con su dueño")

    # Codex bloqueante #2: uso personal va SIEMPRE a la red interna aunque pidan modo pruebas
    r = cli.post("/crear", headers=ADMIN, json={"marca": "hosting.cl", "sabor": "personalizado", "cliente": "",
                                                "hostname": "lab2", "vcpu": 2, "ram_mb": 2048, "disco_gb": 30,
                                                "modo": "pruebas", "instalar_cpanel": False, "uso": "personal", "actor": "fabian"})
    assert r.status_code == 200, r.get_json()
    jf = esperar(r.get_json()["job_id"])
    assert jf["estado"] == "ok", json.dumps(jf, ensure_ascii=False)[:1200]
    pf = "\n".join(p["detalle"] for p in jf["pasos"])
    assert "modo produccion" in pf and "red 10.100.36.0/24" in pf and "192.168.200" not in pf, pf
    regf = motor.vm_registrada(jf["vm"])
    assert regf["ip"] == "10.100.36.250" and regf["propietario"] == "fabian" and jf["vm"] == "vps-int-0002-fabian"
    with motor.DB_LOCK, motor.db() as c:
        av = c.execute("SELECT detalle FROM operaciones WHERE actor='fabian' AND resultado='aviso'").fetchall()
    assert any("modo 'pruebas' ignorado" in a["detalle"] for a in av), [dict(a) for a in av]
    print("ok - uso personal con modo pruebas → producción forzada (solo la red interna), con aviso en la auditoría")

    # ── un VPS de CLIENTE sigue siendo visible para todos y no lleva dueño ──
    r = cli.post("/crear", headers=ADMIN, json={"marca": "planetahosting.cl", "sabor": "personalizado", "cliente": "acme",
                                                "hostname": "srv1", "vcpu": 2, "ram_mb": 2048, "disco_gb": 30,
                                                "modo": "pruebas", "instalar_cpanel": False, "actor": "gerardo"})
    assert r.status_code == 200, r.get_json()
    jc = esperar(r.get_json()["job_id"])
    assert jc["estado"] == "ok" and jc["uso"] == "cliente" and jc["propietario"] is None
    assert jc["vm"] in ver("jonatan", 0) and jc["vm"] in ver("", 0)
    assert motor.vm_registrada(jc["vm"])["uso"] == "cliente"
    print("ok - un VPS de cliente lo ven todos (uso=cliente, sin dueño)")
finally:
    for k, v in _orig.items():
        setattr(motor, k, v)
print("TODO OK - test_uso_personal")
