# Tests MULTI-MARCA (2026-10-23): catálogo de marcas con validación al arrancar, red de
# pruebas común heredada, prefijo de VM por marca, marcas_detalle en /health, marca inactiva
# rechazada y portgroup del host resuelto por nombre o por VLAN.
import json
import os
import sys
import tempfile
from pathlib import Path

# Rutas derivadas del propio archivo (Codex menor #5): la suite corre en cualquier checkout.
# Entorno FIJADO (no setdefault): tokens, catálogo y base de datos no dependen de la máquina.
RAIZ = Path(__file__).resolve().parents[2]          # …/Vps
os.environ["ENGINE_TOKEN"] = "tok-admin-test"
os.environ["WHMCS_TOKEN"] = "tok-whmcs-test"
os.environ["MGMT_PUBKEY"] = "ssh-ed25519 " + "A" * 68 + " t@t"
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.db")
os.environ["CONFIG_DIR"] = str(RAIZ)
os.environ["ESXI_HOST"] = "10.100.37.245"
os.environ["GOVC_URL"] = "https://10.100.37.245/sdk"
os.environ["GOVC_USERNAME"] = "svc-vps"
os.environ["GOVC_PASSWORD"] = "x"
os.environ.pop("MODO", None)
os.environ.pop("DORADA", None)
sys.path.insert(0, str(RAIZ / "engine"))
import app as motor  # noqa: E402

cli = motor.app.test_client()
ADMIN = {"X-Auth-Token": "tok-admin-test"}
WHMCS = {"X-Auth-Token": "tok-whmcs-test"}

# ── catálogo del repo: 6 marcas con VPS (mapeo de ALTA_MARCAS del NOC) ──────
ESPERADAS = {"hosting.cl": ("vps-hcl", "10.100.16.0/24", 81),
             "planetahosting.cl": ("vps-phcl", "10.100.18.0/24", 82),
             "planetahosting.pe": ("vps-phpe", "10.100.20.0/24", 83),
             "planetahosting.com.co": ("vps-phco", "10.100.22.0/24", 84),
             "inkahosting.pe": ("vps-ihpe", "10.100.24.0/24", 85),
             "unohosting.pe": ("vps-uhpe", "10.100.24.0/24", 85),      # red COMPARTIDA (RouterData:
             "hosting.pe": ("vps-hpe", "10.100.24.0/24", 85),          # "Inkahosting.pe unohosting.pe, Hosting.pe")
             "hostingcenter.cl": ("vps-hccl", "10.100.26.0/24", 86)}
assert set(motor.MARCAS) == set(ESPERADAS), sorted(motor.MARCAS)
assert "_pruebas" not in motor.MARCAS and "_pruebas.json" not in motor.MARCAS
for mid, (pref, subred, vlan) in ESPERADAS.items():
    m = motor.MARCAS[mid]
    assert m["prefijo_vm"] == pref and m["red_default"]["subred"] == subred, mid
    assert m["red_default"]["vlan"] == vlan and m["red_default"]["gateway"] == subred.replace("0/24", "1"), mid
    assert m["dominio_hostname"] == mid and m.get("activo", True) is True
    assert m["red_pruebas"]["subred"] == "192.168.200.0/24", "red de pruebas común heredada"
    assert m["red_pruebas"]["portgroup"] == "VM Network" and m["red_pruebas"]["vlan"] == 0, "sin etiqueta, explícito"
assert motor.marcas_activas() == sorted(ESPERADAS)
# la red de pruebas es UNA (la de _pruebas.json) para todas, hosting.cl incluida
assert motor.MARCAS["hosting.cl"]["red_pruebas"] == motor.MARCAS["planetahosting.cl"]["red_pruebas"]
# tres marcas comparten la VLAN 85; los prefijos son todos distintos
assert len({motor.MARCAS[m]["prefijo_vm"] for m in ESPERADAS}) == len(ESPERADAS)
assert {motor.MARCAS[m]["red_default"]["subred"] for m in ("inkahosting.pe", "unohosting.pe", "hosting.pe")} == {"10.100.24.0/24"}
print("ok - 8 marcas cargadas desde marcas/, con la red de pruebas común y VLAN/subred del NOC (VLAN 85 compartida por 3)")

# ── validador: cada error nombra la marca y el campo ─────────────────────────
def marca_ok(mid="demo.cl", **cambios):
    m = {"marca": mid, "nombre": "Demo", "prefijo_vm": "vps-demo", "activo": True,
         "pub_lista": "Red107-0", "dominio_hostname": mid,
         "red_default": {"subred": "10.100.90.0/24", "gateway": "10.100.90.1", "dns": ["1.1.1.1"],
                         "portgroup": "Vps_Demo", "vlan": 90},
         "red_pruebas": {"subred": "192.168.200.0/24", "gateway": "192.168.200.1",
                         "dns": ["1.1.1.1"], "portgroup": "VM Network"}}
    m.update(cambios)
    return m

def debe_fallar(marcas, *fragmentos):
    try:
        motor.validar_marcas(marcas)
    except RuntimeError as e:
        for f in fragmentos:
            assert f in str(e), (f, str(e))
        return str(e)
    raise AssertionError("debía fallar: %s" % fragmentos[0])

assert motor.validar_marcas({"demo.cl": marca_ok()})["demo.cl"]["prefijo_vm"] == "vps-demo"
debe_fallar({"demo.cl": marca_ok(marca="otra")}, "marcas/demo.cl.json", "'marca'")
debe_fallar({"Demo": marca_ok("Demo")}, "minúsculas")
for campo in ("nombre", "prefijo_vm", "dominio_hostname"):
    m = marca_ok(); del m[campo]
    debe_fallar({"demo.cl": m}, "falta el campo '%s'" % campo)
debe_fallar({"demo.cl": marca_ok(activo="si")}, "'activo'")
for malo in ("hcl", "vps-", "vps-HCL", "vps-ph1", "vps-demasiadolargo", "vps-hcl-", "vps-demo\n"):
    debe_fallar({"demo.cl": marca_ok(prefijo_vm=malo)}, "prefijo_vm", "wrapper")
debe_fallar({"demo.cl\n": marca_ok("demo.cl\n")}, "'marca'")   # fullmatch: sin salto de línea final
debe_fallar({"a.cl": marca_ok("a.cl"), "b.cl": marca_ok("b.cl", red_default=dict(marca_ok()["red_default"], subred="10.100.91.0/24", gateway="10.100.91.1"))},
            "prefijo_vm 'vps-demo' repetido con la marca a.cl")
# dos marcas con la MISMA red sí se aceptan (VLAN 85 compartida): la IP libre se elige por red, no por marca
assert set(motor.validar_marcas({"a.cl": marca_ok("a.cl"), "b.cl": marca_ok("b.cl", prefijo_vm="vps-bb")})) == {"a.cl", "b.cl"}
m = marca_ok(); del m["red_pruebas"]
debe_fallar({"demo.cl": m}, "falta 'red_pruebas'", "_pruebas.json")
m = marca_ok(); del m["red_default"]["portgroup"]
debe_fallar({"demo.cl": m}, "red_default: falta 'portgroup'")
m = marca_ok(); m["red_default"]["subred"] = "10.100.90.0/23"
debe_fallar({"demo.cl": m}, "/24")
m = marca_ok(); m["red_default"]["subred"] = "10.100.90.5/24"
debe_fallar({"demo.cl": m}, "subred/gateway inválidos")
m = marca_ok(); m["red_default"]["gateway"] = "10.100.91.1"
debe_fallar({"demo.cl": m}, "gateway 10.100.91.1 no pertenece")
for gw in ("10.100.90.0", "10.100.90.255"):     # Codex medio #2: red y broadcast no sirven de gateway
    m = marca_ok(); m["red_default"]["gateway"] = gw
    debe_fallar({"demo.cl": m}, "gateway %s es la dirección de red o de broadcast" % gw)
m = marca_ok(); m["red_default"]["dns"] = "1.1.1.1"
debe_fallar({"demo.cl": m}, "'dns'")
for malo in ([""], ["no-es-una-ip"], ["1.1.1.1", "8.8.8"], ["2606:4700::1111"], [8]):
    m = marca_ok(); m["red_default"]["dns"] = malo
    debe_fallar({"demo.cl": m}, "dns")
for malo in (None, 0, 5000, "82", True):
    m = marca_ok(); m["red_default"]["vlan"] = malo
    debe_fallar({"demo.cl": m}, "red_default: 'vlan'", "1-4094")
for malo in ("x", -1, 4095, True):       # en pruebas es opcional, pero si viene debe ser válida (0 permitido)
    m = marca_ok(); m["red_pruebas"]["vlan"] = malo
    debe_fallar({"demo.cl": m}, "red_pruebas: 'vlan'", "0-4094")
m = marca_ok(); m["red_pruebas"]["vlan"] = 0
assert motor.validar_marcas({"demo.cl": m}), "red_pruebas con vlan 0 (sin etiqueta) es válida"
m = marca_ok(); m["red_pruebas"].pop("vlan", None)
assert motor.validar_marcas({"demo.cl": m}), "red_pruebas sin vlan es válida (no se verifica la etiqueta)"
for malo in ([100], [102, 100], [0, 5], [1, 255], ["100", "102"], [True, 102]):
    m = marca_ok(); m["red_default"]["publica_rango_prueba"] = malo
    debe_fallar({"demo.cl": m}, "publica_rango_prueba")
debe_fallar({"demo.cl": marca_ok(pub_lista="Red999-0")}, "pub_lista 'Red999-0' desconocida", "Red107-0")
assert motor.validar_marcas({"demo.cl": marca_ok(pub_lista=None)}), "sin pub_lista es válido (usa el default del flujo)"
print("ok - validador de marcas: prefijo, redes /24, gateway, VLAN, rango, lista pública, duplicados")

# ── cargador: archivos '_' ignorados, nombre de archivo = marca, herencia ────
tmp = tempfile.mkdtemp()
os.makedirs(os.path.join(tmp, "marcas"))
def escribir(nombre, doc):
    with open(os.path.join(tmp, "marcas", nombre), "w", encoding="utf-8") as fh:
        json.dump(doc, fh)
m = marca_ok(); del m["red_pruebas"]
escribir("demo.cl.json", m)
try:
    motor.cargar_marcas(tmp)
    raise AssertionError("sin _pruebas.json y sin red_pruebas propia debía fallar")
except RuntimeError as e:
    assert "falta 'red_pruebas'" in str(e)
escribir("_pruebas.json", {"_comentario": "x", "red_pruebas": marca_ok()["red_pruebas"]})
escribir("_notas.json", {"cualquier": "cosa"})
cargadas = motor.cargar_marcas(tmp)
assert set(cargadas) == {"demo.cl"}, "los '_*.json' no son marcas"
assert cargadas["demo.cl"]["red_pruebas"]["portgroup"] == "VM Network", "heredó la red de pruebas común"
m2 = marca_ok("otra.cl", prefijo_vm="vps-otra")
m2["red_default"] = dict(m2["red_default"], subred="10.100.91.0/24", gateway="10.100.91.1")
m2["red_pruebas"] = dict(m2["red_pruebas"], portgroup="Mi Red")
escribir("otra.cl.json", m2)
cargadas = motor.cargar_marcas(tmp)
assert cargadas["otra.cl"]["red_pruebas"]["portgroup"] == "Mi Red", "una red_pruebas propia NO se pisa"
escribir("mal-nombre.json", marca_ok("tercera.cl", prefijo_vm="vps-ter"))
try:
    motor.cargar_marcas(tmp)
    raise AssertionError("archivo que no se llama como su marca debía fallar")
except RuntimeError as e:
    assert "mal-nombre.json" in str(e) and "tercera.cl.json" in str(e)
print("ok - cargador: '_' ignorados, red de pruebas heredada sin pisar la propia, nombre de archivo = marca")

# ── prefijo por marca en el nombre de la VM ──────────────────────────────────
assert motor.siguiente_nombre(motor.MARCAS["planetahosting.cl"], "Acme SA") == "vps-phcl-0001-acme-sa"
assert motor.siguiente_nombre(motor.MARCAS["inkahosting.pe"], "") == "vps-ihpe-0001"
assert motor.siguiente_nombre(motor.MARCAS["hosting.cl"], "x") == "vps-hcl-0001-x"
print("ok - cada marca numera con su propio prefijo")

# ── /health: marcas activas + detalle con redes; rol whmcs no ve nada ────────
h = cli.get("/health", headers=ADMIN).get_json()
assert h["marcas"] == sorted(ESPERADAS)
det = {d["marca"]: d for d in h["marcas_detalle"]}
assert set(det) == set(ESPERADAS)
p = det["planetahosting.cl"]
assert p["nombre"] == "PlanetaHosting.cl" and p["prefijo_vm"] == "vps-phcl" and p["dominio"] == "planetahosting.cl"
assert p["red_default"] == {"subred": "10.100.18.0/24", "gateway": "10.100.18.1", "vlan": 82,
                            "portgroup": "Vps_Planetahosting.cl"}
assert p["red_pruebas"]["subred"] == "192.168.200.0/24" and p["red_pruebas"]["vlan"] == 0
assert p["pub_lista"] == "Red57-0" and p["pub_red"] == "38.19.57" and p["publica_rango_prueba"] == [100, 102]
assert p["sabores"] == 0, "planetahosting.cl aún no tiene planes dictados"
assert det["hosting.cl"]["sabores"] == sum(1 for k, v in motor.SABORES.items()
                                           if k[0] == "hosting.cl" and v.get("activo", True)) >= 3
for d in h["marcas_detalle"]:
    assert "nota" not in d and "_pub_lista_nota" not in d, "el detalle no arrastra notas internas"
hw = cli.get("/health", headers=WHMCS).get_json()
assert hw == {"ok": True}, "rol whmcs: mínimo, sin catálogo"
print("ok - /health expone marcas_detalle (nombre, prefijo, redes, VLAN, lista pública, nº de planes)")

# ── marca inactiva: desaparece del selector y /crear la rechaza ──────────────
motor.MARCAS["inkahosting.pe"]["activo"] = False
try:
    h = cli.get("/health", headers=ADMIN).get_json()
    assert "inkahosting.pe" not in h["marcas"] and "inkahosting.pe" not in {d["marca"] for d in h["marcas_detalle"]}
    r = cli.post("/crear", headers=ADMIN, json={"marca": "inkahosting.pe", "sabor": "personalizado",
                                                "cliente": "x", "hostname": "h.inkahosting.pe",
                                                "vcpu": 2, "ram_mb": 2048, "disco_gb": 30})
    assert r.status_code == 400 and "no está activa" in r.get_json()["error"], r.get_json()
    r = cli.post("/crear", headers=ADMIN, json={"marca": "noexiste.cl", "sabor": "personalizado",
                                                "cliente": "x", "hostname": "h.cl"})
    assert r.status_code == 400 and "marca desconocida" in r.get_json()["error"]
finally:
    motor.MARCAS["inkahosting.pe"]["activo"] = True
print("ok - marca inactiva: fuera del selector y rechazada al crear; marca desconocida → 400")

# ── portgroup del host: por nombre exacto o por VLAN ────────────────────────
class JobFalso:
    def __init__(self):
        self.detalles = []
    def detalle(self, msg, *a):
        self.detalles.append(msg)

HOST = {"id": "esxi-x", "api_url": "https://x/sdk", "govc_user": "svc-vps", "datastore": "ds", "pass_env": "P"}
_govc_real = motor.govc
# resolver_portgroup devuelve (nombre, nota); aquí casi siempre interesa el nombre
rp = lambda *a, **k: motor.resolver_portgroup(*a, **k)[0]

def con_host(pgs, ls=None, info_falla=False):
    """Stub de govc: host.portgroup.info -json devuelve `pgs` [(nombre, vlan)]; ls network devuelve `ls`."""
    def fake(*args, timeout=120, host=None):
        assert host is HOST
        if args[0] == "host.portgroup.info":
            if info_falla:
                raise RuntimeError("govc host.portgroup.info: permiso denegado")
            return json.dumps({"portgroup": [{"spec": {"name": n, "vlanId": v}} for n, v in pgs]})
        if args[:2] == ("ls", "network"):
            return "\n".join("/ha-datacenter/network/%s" % n for n in (ls if ls is not None else [n for n, _ in pgs]))
        raise AssertionError("govc inesperado: %r" % (args,))
    return fake

RED_PH = {"subred": "10.100.18.0/24", "portgroup": "Vps_Planetahosting.cl", "vlan": 82}
RED_PRU = {"subred": "192.168.200.0/24", "portgroup": "VM Network", "vlan": 0}      # sin etiqueta, explícito
RED_SIN = {"subred": "192.168.200.0/24", "portgroup": "VM Network"}                 # vlan no declarada
try:
    # a) nombre exacto presente → ese, sin aviso
    j = JobFalso()
    motor.govc = con_host([("VM Network", 0), ("Vps_Planetahosting.cl", 82)])
    assert motor.resolver_portgroup(HOST, RED_PH, "produccion", j) == ("Vps_Planetahosting.cl", "") and not j.detalles
    # b) el host lo llama distinto pero está en la VLAN 82 (esxi-20039) → resuelto por VLAN, con aviso
    j = JobFalso()
    motor.govc = con_host([("VM Network", 0), ("vps_Hosting.cl", 81), ("vps_PlanetahostingCL", 82), ("vps_inkape", 85)])
    nombre_pg, nota_pg = motor.resolver_portgroup(HOST, RED_PH, "produccion", j)
    assert nombre_pg == "vps_PlanetahostingCL" and nota_pg.startswith("resuelto por VLAN 82"), (nombre_pg, nota_pg)
    assert any("resuelto por VLAN 82" in d for d in j.detalles), j.detalles
    # c) el nombre existe pero en OTRA VLAN → se rechaza (la VM nacería en la red equivocada)
    motor.govc = con_host([("Vps_Planetahosting.cl", 83)])
    try:
        rp(HOST, RED_PH, "produccion")
        raise AssertionError("portgroup con VLAN equivocada debía fallar")
    except RuntimeError as e:
        assert "VLAN 83" in str(e) and "VLAN 82" in str(e), str(e)
    # d) dos en la misma VLAN: gana el único que dice 'vps'; si ninguno/ambos lo dicen → ambiguo
    motor.govc = con_host([("vps_PlanetahostingCL", 82), ("Respaldo82", 82)])
    assert rp(HOST, RED_PH, "produccion") == "vps_PlanetahostingCL"
    for pgs in ([("RedA", 82), ("RedB", 82)], [("vps_A", 82), ("VPS_B", 82)]):   # ninguno o AMBOS dicen 'vps'
        motor.govc = con_host(pgs)
        try:
            rp(HOST, RED_PH, "produccion")
            raise AssertionError("dos candidatos ambiguos debía fallar: %s" % pgs)
        except RuntimeError as e:
            assert "2 portgroups en la VLAN 82" in str(e) and pgs[0][0] in str(e) and pgs[1][0] in str(e), str(e)
    # e) ninguno en la VLAN → error con el inventario del host y la VLAN que hay que crear
    motor.govc = con_host([("VM Network", 0), ("Vps_Hosting.cl", 81)])
    try:
        rp(HOST, RED_PH, "produccion")
        raise AssertionError("sin portgroup en la VLAN debía fallar")
    except RuntimeError as e:
        assert "NO tiene el portgroup 'Vps_Planetahosting.cl' ni otro en la VLAN 82" in str(e), str(e)
        assert "Vps_Hosting.cl (VLAN 81)" in str(e) and "Port groups, VLAN 82" in str(e), str(e)
    # f) el host no deja leer las VLAN → chequeo por nombre de siempre (ls network), con constancia
    #    y SIN el texto crudo del error (Codex medio #3: podría traer datos sensibles)
    j = JobFalso()
    motor.govc = con_host([], ls=["VM Network", "Vps_Planetahosting.cl"], info_falla=True)
    assert rp(HOST, RED_PH, "produccion", j) == "Vps_Planetahosting.cl"
    assert any("solo por nombre" in d and "host.portgroup.info falló" in d and "VLAN no verificada" in d
               for d in j.detalles), j.detalles
    assert not any("permiso denegado" in d or "SECRETO" in d for d in j.detalles), "el error crudo no va al paso a paso"
    motor.govc = con_host([], ls=["VM Network"], info_falla=True)
    try:
        rp(HOST, RED_PH, "produccion")
        raise AssertionError("fallback por nombre sin el portgroup debía fallar")
    except RuntimeError as e:
        assert "NO tiene el portgroup 'Vps_Planetahosting.cl'" in str(e) and "VLAN 82" in str(e), str(e)
    # g) inventario vacío → también cae al chequeo por nombre, y queda anotado
    j = JobFalso()
    motor.govc = con_host([], ls=["Vps_Planetahosting.cl"])
    assert rp(HOST, RED_PH, "produccion", j) == "Vps_Planetahosting.cl"
    assert any("vino vacío" in d and "VLAN no verificada" in d for d in j.detalles), j.detalles
    # h) pruebas con vlan 0 (sin etiqueta): el nombre debe existir Y estar en VLAN 0; no se adivina por VLAN 0
    motor.govc = con_host([("VM Network", 0), ("Management Network", 0)])
    assert rp(HOST, RED_PRU, "pruebas") == "VM Network"
    motor.govc = con_host([("VM Network", 85), ("Management Network", 0)])     # Codex medio #1
    try:
        rp(HOST, RED_PRU, "pruebas")
        raise AssertionError("'VM Network' etiquetado en la VLAN 85 debía rechazarse para la red sin etiqueta")
    except RuntimeError as e:
        assert "está en la VLAN 85" in str(e) and "sin etiqueta" in str(e), str(e)
    motor.govc = con_host([("Otra", 0), ("Management Network", 0)])
    try:
        rp(HOST, RED_PRU, "pruebas")
        raise AssertionError("en pruebas sin el nombre exacto debía fallar (no se adivina por VLAN 0)")
    except RuntimeError as e:
        assert "NO tiene el portgroup 'VM Network'" in str(e) and "ni otro en la VLAN" not in str(e) \
            and "sin etiqueta" in str(e), str(e)
    # i) vlan NO declarada → solo nombre (la etiqueta no se puede verificar, y así se documenta)
    motor.govc = con_host([("VM Network", 85)])
    assert rp(HOST, RED_SIN, "pruebas") == "VM Network"
finally:
    motor.govc = _govc_real
print("ok - portgroup del host: nombre exacto, VLAN (rótulos distintos por host), VLAN equivocada rechazada (incl. sin etiqueta), ambigüedad, fallback por nombre con constancia")

# ── el portgroup resuelto llega al VMX: flujo REAL de creación hasta registrar la VM ────
# Marca planetahosting.cl en modo producción sobre un host que llama 'vps_PlanetahostingCL' a la
# VLAN 82 (como esxi-20039). Todo lo que sale a la red está stubbeado; el flujo se corta en
# vm.register (paso 7) con una excepción controlada, después de que el VMX ya se subió.
HOST_B = {"id": "esxi-b", "ip": "10.0.0.9", "api_url": "https://10.0.0.9/sdk", "govc_user": "svc-vps",
          "datastore": "DsB", "pass_env": "GP_B", "ssh_key": "/k/b", "estado": "activo", "prioridad": 50}
SABOR_P = {"marca": "planetahosting.cl", "slug": "personalizado", "nombre_web": "Personalizado 2 vCPU / 2 GB RAM / 30 GB",
           "vcpu": 2, "ram_mb": 2048, "disco_gb": 30, "activo": True, "extras": {}}
capturado = {}
def govc_flujo(*a, timeout=120, host=None):
    assert host is HOST_B, "todo govc del flujo va al host elegido"
    if a[0] == "host.portgroup.info":
        return json.dumps({"portgroup": [{"spec": {"name": "VM Network", "vlanId": 0}},
                                         {"spec": {"name": "vps_PlanetahostingCL", "vlanId": 82}}]})
    if a[0] == "datastore.upload":
        with open(a[3], encoding="utf-8") as fh:
            capturado["vmx"] = fh.read()
        capturado["destino"] = a[4]
        return ""
    if a[0] == "vm.register":
        raise RuntimeError("fin-de-prueba: el VMX ya se subió")
    raise AssertionError("govc inesperado en el flujo: %r" % (a,))
_orig = (motor.govc, motor.esxi_ssh, motor.ip_libre_produccion, motor.datastore_libre_gb, motor.listar_wrapper)
os.makedirs("/tmp", exist_ok=True)      # el flujo escribe /tmp/<vm>.vmx antes de subirlo
try:
    motor.govc = govc_flujo
    motor.esxi_ssh = lambda cmd, timeout=300, host=None: "OK"
    motor.ip_libre_produccion = lambda subred, gateway, job=None: subred.replace("0/24", "250")
    motor.datastore_libre_gb = lambda host=None: 999
    motor.listar_wrapper = lambda cmd, host=None: [motor.SISTEMAS["almalinux9"]["dorada"]]
    nombre = motor.reservar_vm("planetahosting.cl", motor.MARCAS["planetahosting.cl"], "personalizado",
                               SABOR_P, "acme", "srv1", host=HOST_B)
    assert nombre == "vps-phcl-0001-acme", nombre
    job = motor.Job("crear", nombre, "test", motor.PASOS_CREAR)
    try:
        motor.flujo_crear(job, "planetahosting.cl", "personalizado", "acme", "srv1", False, "produccion",
                          sabor_def=SABOR_P, host=HOST_B, so="almalinux9")
        raise AssertionError("el flujo debía cortarse en vm.register")
    except RuntimeError as e:
        assert "fin-de-prueba" in str(e), str(e)
    vmx = capturado["vmx"]
    assert 'ethernet0.networkName = "vps_PlanetahostingCL"' in vmx, vmx
    assert 'displayName = "%s"' % nombre in vmx or nombre in vmx
    assert capturado["destino"] == "VPS/%s/%s.vmx" % (nombre, nombre)
    detalles = "\n".join(p["detalle"] for p in job.pasos)
    assert "PlanetaHosting.cl: red 10.100.18.0/24 (VLAN 82)" in detalles, detalles
    assert "portgroup 'vps_PlanetahostingCL' — resuelto por VLAN 82" in detalles, detalles   # en el cierre del paso 3
    assert "IP privada asignada: 10.100.18.250 (gw 10.100.18.1)" in detalles, detalles
    reg = motor.vm_registrada(nombre)
    assert reg["ip"] == "10.100.18.250" and reg["marca"] == "planetahosting.cl" and reg["host"] == "esxi-b"
finally:
    motor.govc, motor.esxi_ssh, motor.ip_libre_produccion, motor.datastore_libre_gb, motor.listar_wrapper = _orig
    with motor.DB_LOCK, motor.db() as c:
        c.execute("DELETE FROM vms WHERE marca='planetahosting.cl'")
print("ok - flujo de creación real (stubs): la VM de planetahosting.cl nace en la red de su marca y el VMX lleva el portgroup resuelto por VLAN")

print("TODO OK - test_marcas")
