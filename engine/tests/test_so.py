# Tests MULTI-SO (2026-10-01): elección de sistema operativo, dorada por SO y
# configuración de red por FAMILIA (netplan en Ubuntu vs nmcli en AlmaLinux).
import json
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

cli = motor.app.test_client()
ADMIN = {"X-Auth-Token": "tok-admin-test"}
WHMCS = {"X-Auth-Token": "tok-whmcs-test"}

# ── catálogo ────────────────────────────────────────────────────────────────
assert "almalinux9" in motor.SISTEMAS and "ubuntu26.04" in motor.SISTEMAS
assert motor.SISTEMAS["ubuntu26.04"]["familia"] == "debian"
assert motor.SISTEMAS["almalinux9"]["familia"] == "rhel"
assert motor.SISTEMAS["ubuntu26.04"]["cpanel"] is False, "cPanel no soporta Ubuntu 26.04"
h = cli.get("/health", headers=ADMIN).get_json()
assert {s["so"] for s in h["sistemas"]} == set(motor.SISTEMAS), h.get("sistemas")
assert h["so_default"] == "almalinux9"
print("ok - catálogo de sistemas expuesto en /health (con familia y soporte cPanel)")

# ── el SO sale del plan (so_default), y un so_default desconocido NO pasa ───
assert motor.so_de_sabor({"so_default": "ubuntu26.04"}) == "ubuntu26.04"
assert motor.so_de_sabor({}) == "almalinux9", "sin so_default → el global"
# so_default inválido NO se acepta en silencio: desconocido, y tampoco tipos falsy/raros
# (Codex multiso #e: 0/False usaban el global y una lista reventaba con TypeError)
for malo in ("windows95", 0, False, ["ubuntu26.04"], {"a": 1}, 3.5):
    try:
        motor.so_de_sabor({"so_default": malo})
        raise AssertionError("so_default inválido debía fallar: %r" % (malo,))
    except RuntimeError as e:
        assert "inválido" in str(e) and "so_default" in str(e), (malo, str(e))
# ausente o vacío SÍ heredan el global (es lo legítimo)
assert motor.so_de_sabor({"so_default": None}) == "almalinux9"
assert motor.so_de_sabor({"so_default": ""}) == "almalinux9"
# el validador común protege las DOS vías de entrada (nada llega a SISTEMAS[so] con basura)
for malo in ("noexiste", None, 7, []):
    try:
        motor.validar_so(malo, "parámetro")
        raise AssertionError("validar_so debía rechazar %r" % (malo,))
    except RuntimeError as e:
        assert "inválido" in str(e)
print("ok - el SO lo define el plan; so_default inválido falla ruidosamente")

# ── RED POR FAMILIA: lo crítico — Ubuntu no tiene nmcli ─────────────────────
dns = ["8.8.8.8", "1.1.1.1"]
ud_rhel = motor.cloudinit_userdata("h.cl", "10.0.0.5", 24, "10.0.0.1", dns, "rhel")
ud_deb = motor.cloudinit_userdata("h.cl", "10.0.0.5", 24, "10.0.0.1", dns, "debian")
assert "nmcli" in ud_rhel and "netplan" not in ud_rhel
assert "netplan" in ud_deb and "nmcli" not in ud_deb, "Ubuntu NO trae NetworkManager"
assert "10.0.0.5/24" in ud_deb and "via: 10.0.0.1" in ud_deb
assert "8.8.8.8, 1.1.1.1" in ud_deb, "DNS en formato lista de netplan"
assert "'0600'" in ud_deb, "netplan exige permisos restrictivos"
# MISMA clave de interfaz que el metadata: si cloud-init regenera 50-cloud-init.yaml, netplan
# FUSIONA por clave y nuestro 60-vps.yaml gana — en vez de dos definiciones peleando por la NIC
assert "nic0:" in ud_deb and "vps0" not in ud_deb, "la clave debe coincidir con la del metadata"
assert "rm -f /etc/netplan/50-cloud-init.yaml" in ud_deb, "debe retirar el netplan de cloud-init"
# netplan FUSIONA mapas: un dhcp4:true previo sobreviviría si no se declara explícitamente
assert "dhcp4: false" in ud_deb and "dhcp6: false" in ud_deb, "DHCP debe quedar apagado explícito"
assert "renderer: networkd" in ud_deb
# el marcador SOLO debe escribirse si toda la cadena salió bien (sh -e)
assert "sh, -ec" in ud_deb, "la cadena de red debe abortar al primer fallo"
assert ud_deb.count("vps-engine.provisioned") == 1 and "netplan apply; touch" in ud_deb
# la familia por defecto sigue siendo rhel (compat con lo existente)
assert motor.cloudinit_userdata("h.cl", "10.0.0.5", 24, "10.0.0.1", dns) == ud_rhel
print("ok - userdata por familia: netplan en Ubuntu, nmcli en AlmaLinux")

# metadata: gateway4 (deprecado) solo para rhel; routes modernas para debian
md_rhel = json.loads(motor.cloudinit_metadata("vm", "h.cl", "10.0.0.5", 24, "10.0.0.1", dns, "rhel"))
md_deb = json.loads(motor.cloudinit_metadata("vm", "h.cl", "10.0.0.5", 24, "10.0.0.1", dns, "debian"))
nic_r = md_rhel["network"]["ethernets"]["nic0"]
nic_d = md_deb["network"]["ethernets"]["nic0"]
assert nic_r.get("gateway4") == "10.0.0.1" and "routes" not in nic_r
assert nic_d.get("routes") == [{"to": "default", "via": "10.0.0.1"}] and "gateway4" not in nic_d
print("ok - metadata: routes modernas en Ubuntu, gateway4 solo en AlmaLinux")

# ── endpoint /crear: quién puede elegir SO y validaciones ───────────────────
_flujo_real = motor.flujo_crear
motor.flujo_crear = lambda *a, **k: None
BASE = {"marca": "hosting.cl", "sabor": "vps-estandar", "cliente": "t", "modo": "pruebas",
        "instalar_cpanel": False}
r = cli.post("/crear", json=dict(BASE, hostname="s1.cl", so="noexiste"), headers=ADMIN)
assert r.status_code == 400 and "desconocido" in r.get_json()["error"], r.get_json()
r = cli.post("/crear", json=dict(BASE, hostname="s2.cl", so=123), headers=ADMIN)
assert r.status_code == 400 and "texto" in r.get_json()["error"]
r = cli.post("/crear", json=dict(BASE, hostname="s3.cl", so="ubuntu26.04",
                                 whmcs_serviceid="9"), headers=WHMCS)
assert r.status_code == 403 and "NOC" in r.get_json()["error"], "WHMCS no elige SO"
print("ok - solo el NOC elige SO; SO inválido o no-texto -> 400")

# cPanel + Ubuntu = incompatible, se avisa ANTES de crear
r = cli.post("/crear", json=dict(BASE, hostname="s4.cl", so="ubuntu26.04",
                                 instalar_cpanel=True), headers=ADMIN)
assert r.status_code == 400 and "no lo soporta" in r.get_json()["error"], r.get_json()
# un plan SIN cPanel + Ubuntu elegido en el selector: ruta normal del NOC
r = cli.post("/crear", json={"marca": "hosting.cl", "sabor": "vps-estandar", "cliente": "t",
                             "hostname": "s5.cl", "modo": "pruebas", "instalar_cpanel": False,
                             "so": "ubuntu26.04"}, headers=ADMIN)
assert r.status_code == 200, r.get_json()
with motor.DB_LOCK, motor.db() as c:
    row = c.execute("SELECT sabor FROM vms WHERE hostname='s5.cl'").fetchone()
assert row, "la reserva debe existir"   # el `so` lo graba el worker (aquí está stubbeado)
print("ok - cPanel+Ubuntu rechazado con motivo; Ubuntu por selector crea y queda registrado")

# ── la COMPENSACIÓN cubre también los fallos de SO (Codex multiso #e) ──────
# un `so` inválido que llegue al worker debe: fallar el job, NO reventar con KeyError y
# LIBERAR la reserva (si no, quedaría una fila 'creando' huérfana reteniendo IP y cupo)
import time as _t  # noqa: E402
motor.flujo_crear = _flujo_real
motor.esxi_ssh = lambda *a, **k: (_ for _ in ()).throw(AssertionError("no debió tocar el ESXi"))
r = cli.post("/crear", json={"marca": "hosting.cl", "sabor": "vps-estandar", "cliente": "t",
                             "hostname": "comp-so.cl", "modo": "pruebas",
                             "instalar_cpanel": False}, headers=ADMIN)
assert r.status_code == 200, r.get_json()
vm_comp = r.get_json()["vm"]
# forzar el fallo DENTRO del worker: el catálogo pierde la entrada justo después del 200
_sis = motor.SISTEMAS.pop("almalinux9")
for _ in range(100):
    with motor.DB_LOCK, motor.db() as c:
        viva = c.execute("SELECT COUNT(*) n FROM vms WHERE nombre=?", (vm_comp,)).fetchone()["n"]
    if viva == 0:
        break
    _t.sleep(0.1)
motor.SISTEMAS["almalinux9"] = _sis
with motor.DB_LOCK, motor.db() as c:
    quedan = c.execute("SELECT COUNT(*) n FROM vms WHERE nombre=?", (vm_comp,)).fetchone()["n"]
assert quedan == 0, "un fallo de SO en el worker dejó la reserva huérfana (IP y cupo retenidos)"
print("ok - compensación: un SO inválido en el worker libera la reserva (sin KeyError)")

# el WORKER rechaza un `so` FALSY explícito (False/0/[]/{}) en vez de caer al SO del plan
class _FakeJob:
    vm = "vps-hcl-9999-fake"
    def paso(self, d=""): pass
    def detalle(self, t): pass
for falsy in (False, 0, [], {}):
    try:
        motor.flujo_crear(_FakeJob(), "hosting.cl", "vps-estandar", "t", "h.cl", False,
                          "pruebas", so=falsy)
        raise AssertionError("el worker debía rechazar so=%r" % (falsy,))
    except RuntimeError as e:
        assert "inválido" in str(e) and "parámetro" in str(e), (falsy, str(e))
print("ok - el worker rechaza un SO falsy explícito (no lo confunde con 'sin parámetro')")

# ── guarda de coherencia del catálogo (Codex multiso #f) ───────────────────
for k, v in motor.SISTEMAS.items():
    assert v["token"] in v["dorada"], (k, v)
print("ok - cada sistema apunta a una dorada que lo nombra (guarda contra DORADA mal puesta)")

print("\nTESTS MULTI-SO OK")
