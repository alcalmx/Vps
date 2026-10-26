# Tests ACCESO POR CONTRASEÑA DE ROOT (2026-10-26): solo VPS de CLIENTE desde el dashboard. El motor genera una
# contraseña fuerte, la aplica, habilita el SSH por contraseña con un drop-in propio y lo verifica; sin llave de
# entrega. Uso personal y WHMCS siguen con llave.
import json
import os
import subprocess
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

# ── el script remoto es fijo, válido para bash y escribe el drop-in que gana a 00-hosting.conf ──
sc = motor.SSHD_CLAVE_SCRIPT
assert motor.SSHD_CLAVE_CONF == "/etc/ssh/sshd_config.d/00-acceso-clave.conf"
assert "00-acceso-clave.conf" < "00-hosting.conf" < "50-cloud-init.conf" < "99-vps.conf", "sshd: gana el primero leído"
assert "%%" not in sc and "f=/etc/ssh/sshd_config.d/00-acceso-clave.conf;" in sc
assert "printf '%s\\n'" in sc and "'PermitRootLogin yes' 'PasswordAuthentication yes'" in sc
assert "sshd -t" in sc and "rm -f \"$f\"" in sc and "sshd -T" in sc
tmp = os.path.join(tempfile.mkdtemp(), "s.sh")
with open(tmp, "w", newline="\n") as fh:
    fh.write(sc + "\n")
assert subprocess.run(["bash", "-n", tmp]).returncode == 0, "el script remoto debe ser sintaxis bash válida"
print("ok - script de habilitación: fijo, sintaxis válida, drop-in 00-acceso-clave.conf (se lee antes que el de la dorada)")

# ── /crear: validaciones ────────────────────────────────────────────────────
base = {"marca": "hosting.cl", "sabor": "personalizado", "cliente": "acme", "hostname": "h1", "vcpu": 2, "ram_mb": 2048,
        "disco_gb": 30, "modo": "produccion", "instalar_cpanel": False, "actor": "alcadio"}
for extra, code, frag in ((dict(acceso="otro"), 400, "acceso inválido"), (dict(acceso=5), 400, "acceso debe ser texto"),
                          (dict(acceso="clave", uso="personal"), 400, "solo con llave"),
                          (dict(acceso="clave", pubkey_cliente=motor.LLAVES_EQUIPO["alcadio"]["llaves"][0]), 400, "elige una u otra"),
                          (dict(acceso="clave", root_password="MiClave123!"), 400, "la genera el motor")):
    r = cli.post("/crear", headers=ADMIN, json=dict(base, **extra))
    assert r.status_code == code and frag in r.get_json()["error"], (extra, r.get_json())
r = cli.post("/crear", headers=WHMCS, json=dict(base, sabor="vps-estandar", whmcs_serviceid="7", acceso="clave"))
assert r.status_code == 403 and "solo para VPS creados desde el dashboard" in r.get_json()["error"], r.get_json()
print("ok - /crear: acceso validado (llave|clave; clave solo dashboard, solo cliente, sin BYO ni clave externa)")

# ── habilitar_ssh_clave (unidad) ────────────────────────────────────────────
class _Cli:
    def set_missing_host_key_policy(self, *a): pass
    def connect(self, *a, **k): pass
    def close(self): pass
class _Pm:
    SSHClient = _Cli
    AutoAddPolicy = object
class JobFalso:
    def __init__(self): self.d = []
    def detalle(self, t): self.d.append(t)
_orig = {k: getattr(motor, k) for k in ("govc", "esxi_ssh", "ip_libre_produccion", "ip_libre_pruebas", "ip_publica_libre",
                                        "crear_nat", "datastore_libre_gb", "listar_wrapper", "netbox_ip_add",
                                        "securizar_vps", "paramiko", "MGMT_PRIVKEY_PATH", "mikrotik", "_ssh_exec")}
capt = {"cmds": [], "chpasswd": [], "secv": 0}
SSHD = {"out": "permitrootlogin yes\npasswordauthentication yes\n", "falla": None, "chpasswd_falla": False}
def fake_exec(cli_, comando, stdin_data=None, timeout=60):
    capt["cmds"].append(comando)
    if comando == "chpasswd":
        if SSHD["chpasswd_falla"]:
            raise RuntimeError("'chpasswd' rc=1: x")
        capt["chpasswd"].append(stdin_data)
        return ""
    if comando == motor.SSHD_CLAVE_SCRIPT:
        if SSHD["falla"]:
            raise SSHD["falla"]
        return SSHD["out"]
    return ""
try:
    motor.paramiko = _Pm
    motor._ssh_exec = fake_exec
    motor.MGMT_PRIVKEY_PATH = ""
    j = JobFalso()
    assert motor.habilitar_ssh_clave(j, "10.0.0.1") is False and "sin llave de gestión" in j.d[-1]
    motor.MGMT_PRIVKEY_PATH = "/fake/gestion"
    assert motor.habilitar_ssh_clave(JobFalso(), "10.0.0.1") is True
    for malo in ("permitrootlogin without-password\npasswordauthentication yes\n", "permitrootlogin yes\npasswordauthentication no\n", ""):
        SSHD["out"] = malo
        assert motor.habilitar_ssh_clave(JobFalso(), "10.0.0.1") is False, malo
    SSHD["out"] = "permitrootlogin yes\npasswordauthentication yes\n"
    SSHD["falla"] = RuntimeError("'f=/etc/ssh/sshd_config.d/00-acceso-clave.conf;' rc=3: SSHD_INVALIDO secreto")
    j = JobFalso()
    assert motor.habilitar_ssh_clave(j, "10.0.0.1") is False and "RuntimeError" in j.d[-1] and "secreto" not in j.d[-1]
    SSHD["falla"] = None
    print("ok - habilitar_ssh_clave: True solo si sshd -T confirma ambas opciones; errores sin texto crudo")

    # ── flujo completo con stubs ────────────────────────────────────────────
    def govc_flujo(*a, timeout=120, host=None):
        if a[0] == "host.portgroup.info":
            return json.dumps({"portgroup": [{"spec": {"name": "VM Network", "vlanId": 0}},
                                             {"spec": {"name": "Vps_Hosting.cl", "vlanId": 81}}]})
        if a[0] == "vm.ip":
            return "10.0.0.9"
        return ""
    def secv(job, nombre, ip, cliente, fqdn):
        capt["secv"] += 1
        return {"url": "https://send/x", "password": "p", "item_id": "it", "id": "s"}
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
    os.makedirs("/tmp", exist_ok=True)
    def esperar(jid):
        for _ in range(300):
            jj = cli.get("/job/%s?usuario=x&total=1" % jid, headers=ADMIN).get_json()
            if jj["estado"] != "corriendo":
                return jj
            time.sleep(0.05)
        raise AssertionError("job no terminó")
    def crear(**kw):
        capt["cmds"].clear(); capt["chpasswd"].clear(); capt["secv"] = 0
        r = cli.post("/crear", headers=ADMIN, json=dict(base, **kw))
        assert r.status_code == 200, r.get_json()
        jj = esperar(r.get_json()["job_id"])
        assert jj["estado"] == "ok", json.dumps(jj, ensure_ascii=False)[:1500]
        return jj

    # a) cliente + acceso por contraseña: contraseña fuerte aplicada, SSH habilitado, sin llave de entrega
    j = crear(hostname="srv1", acceso="clave")
    r = j["resultado"]
    assert len(capt["chpasswd"]) == 1 and capt["chpasswd"][0].startswith("root:")
    pw = capt["chpasswd"][0][len("root:"):].strip()
    assert len(pw) == 20 and r["root_password"] == pw, "la contraseña aplicada es la que se entrega"
    assert motor.SSHD_CLAVE_SCRIPT in capt["cmds"] and capt["secv"] == 0
    assert r["acceso"] == "clave" and r["ssh_cmd"] == "ssh root@38.19.57.101" and r["sin_llave_usuario"] is False
    assert not r.get("send_url") and not r.get("byo") and "acceso_clave_error" not in r
    assert "HABILITADO y verificado" in j["pasos"][13]["detalle"] and "también servirá para SSH" in j["pasos"][10]["detalle"]
    assert "acceso por contraseña de root" in j["pasos"][-1]["detalle"]
    assert motor.vm_registrada(j["vm"])["acceso"] == "clave"
    print("ok - cliente + contraseña: 20 caracteres aplicada y entregada, SSH por contraseña verificado, sin llave ni Send")

    # b) acceso por llave (por defecto): lo de siempre, sin tocar sshd
    j = crear(hostname="srv2")
    assert capt["secv"] == 1 and motor.SSHD_CLAVE_SCRIPT not in capt["cmds"] and capt["chpasswd"] == []
    assert j["resultado"]["acceso"] == "llave" and motor.vm_registrada(j["vm"])["acceso"] == "llave"
    print("ok - acceso por llave (default): llave generada y entregada, sshd intacto, sin contraseña")

    # c) falla al habilitar sshd: VPS creado, aviso, sin comando SSH falso; la contraseña igual se muestra (consola)
    SSHD["out"] = "permitrootlogin without-password\npasswordauthentication no\n"
    j = crear(hostname="srv3", acceso="clave")
    r = j["resultado"]
    assert r["ssh_cmd"] is None and r["sin_llave_usuario"] is True and "acceso_clave_error" in r and r.get("root_password")
    assert "SSH con contraseña NO habilitado" in j["pasos"][13]["detalle"]
    SSHD["out"] = "permitrootlogin yes\npasswordauthentication yes\n"
    # d) la contraseña no se pudo aplicar: NO se abre el SSH por contraseña
    SSHD["chpasswd_falla"] = True
    j = crear(hostname="srv4", acceso="clave")
    r = j["resultado"]
    assert motor.SSHD_CLAVE_SCRIPT not in capt["cmds"], "sin contraseña confirmada no se habilita el login por contraseña"
    assert r.get("root_password_error") and "acceso_clave_error" in r and r["ssh_cmd"] is None
    assert "la contraseña no se pudo aplicar" in j["pasos"][13]["detalle"]
    SSHD["chpasswd_falla"] = False
    print("ok - fallas: sshd no habilitado o contraseña no aplicada → VPS creado con aviso, sin comando SSH falso")

    # e) modo pruebas: también se habilita (no toca el router)
    j = crear(hostname="srv5", acceso="clave", modo="pruebas")
    assert motor.SSHD_CLAVE_SCRIPT in capt["cmds"] and j["resultado"]["ssh_cmd"] == "ssh root@192.168.200.240"
    print("ok - modo pruebas: acceso por contraseña también habilitado")
finally:
    for k, v in _orig.items():
        setattr(motor, k, v)
print("TODO OK - test_acceso_clave")
