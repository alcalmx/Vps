# Tests ACCESO POR CONTRASEÑA DE ROOT (2026-10-26): solo VPS de CLIENTE desde el dashboard. El motor genera una
# contraseña (12: 2 especiales + 1 mayúscula + minúsculas/números), la aplica, habilita el SSH por contraseña con un
# drop-in propio, lo VERIFICA con la config efectiva (sshd -T -C) y un LOGIN REAL solo con contraseña; ante cualquier
# falla revierte y confirma la reversión (si no puede: "incierto"). Uso personal y WHMCS siguen con llave.
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

# ── scripts remotos: fijos, sintaxis bash válida, drop-in que gana a la dorada, reversión y verificación efectiva ──
assert motor.SSHD_CLAVE_CONF == "/etc/ssh/sshd_config.d/00-acceso-clave.conf"
assert "00-acceso-clave.conf" < "00-hosting.conf" < "50-cloud-init.conf" < "99-vps.conf", "sshd: gana el primero leído"
assert not motor._SSHD_PREVIO.endswith(".conf"), "el respaldo no debe incluirse en sshd"
for nombre in ("SSHD_CLAVE_SCRIPT", "SSHD_CLAVE_ROLLBACK", "SSHD_CLAVE_LIMPIEZA"):
    sc = getattr(motor, nombre)
    assert "@CONF@" not in sc and "@PREVIO@" not in sc and "@EFECTIVO@" not in sc, nombre
    tmp = os.path.join(tempfile.mkdtemp(), "s.sh")
    with open(tmp, "w", newline="\n") as fh:
        fh.write(sc + "\n")
    assert subprocess.run(["bash", "-n", tmp]).returncode == 0, "%s: sintaxis bash inválida" % nombre
sc = motor.SSHD_CLAVE_SCRIPT
assert "printf '%s\\n'" in sc and "'PermitRootLogin yes' 'PasswordAuthentication yes'" in sc
assert "restaurar()" in sc and "cp -p \"$f\" \"$b\"" in sc and "sshd -t" in sc
assert 'sshd -T -C "user=root,host=$c,addr=$c"' in sc and "authenticationmethods" in sc, "config EFECTIVA para root"
rb = motor.SSHD_CLAVE_ROLLBACK
assert 'mv -f "$b" "$f"; else rm -f "$f"' in rb and "sshd -t || exit 3" in rb and "sshd -T -C" in rb
print("ok - scripts: fijos, sintaxis válida, respaldo/restauración, verificación efectiva con -C")

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

# ── VM simulada: _ssh_exec por script y login por contraseña ─────────────────
LOGINS = []
VM = {}
class _Cli:
    def set_missing_host_key_policy(self, *a): pass
    def connect(self, ip, **k):
        if "password" in k:   # login de prueba: SOLO contraseña, sin agente ni llaves
            assert k.get("allow_agent") is False and k.get("look_for_keys") is False and "key_filename" not in k
            LOGINS.append((ip, k["password"]))
            if not VM["login_ok"]:
                raise Exception("Authentication failed.")
    def close(self): pass
class _Pm:
    SSHClient = _Cli
    AutoAddPolicy = object
class JobFalso:
    def __init__(self): self.d = []
    def detalle(self, t): self.d.append(t)
capt = {"cmds": [], "chpasswd": [], "secv": 0}
def fake_exec(cli_, comando, stdin_data=None, timeout=60):
    capt["cmds"].append(comando)
    if comando == "chpasswd":
        if VM["chpasswd_falla"]:
            raise RuntimeError("'chpasswd' rc=1: x")
        capt["chpasswd"].append(stdin_data)
        return ""
    if comando == motor.SSHD_CLAVE_SCRIPT:
        if VM["falla_script"]:
            raise VM["falla_script"]
        return VM["efectivo"]
    if comando == motor.SSHD_CLAVE_ROLLBACK:
        if VM["falla_rollback"]:
            raise VM["falla_rollback"]
        return VM["rollback"]
    return ""
def reset_vm():
    VM.update(login_ok=True, efectivo="permitrootlogin yes\npasswordauthentication yes\nauthenticationmethods any\n",
              falla_script=None, falla_rollback=None, chpasswd_falla=False,
              rollback="permitrootlogin without-password\npasswordauthentication no\nauthenticationmethods any\n")
    LOGINS.clear()
reset_vm()

_orig = {k: getattr(motor, k) for k in ("govc", "esxi_ssh", "ip_libre_produccion", "ip_libre_pruebas", "ip_publica_libre",
                                        "crear_nat", "datastore_libre_gb", "listar_wrapper", "netbox_ip_add",
                                        "securizar_vps", "paramiko", "MGMT_PRIVKEY_PATH", "mikrotik", "_ssh_exec")}
try:
    motor.paramiko = _Pm
    motor._ssh_exec = fake_exec
    # ── habilitar_ssh_clave (unidad) ────────────────────────────────────────
    motor.MGMT_PRIVKEY_PATH = ""
    j = JobFalso()
    assert motor.habilitar_ssh_clave(j, "10.0.0.1", "pw") == "deshabilitado" and "sin llave de gestión" in j.d[-1]
    motor.MGMT_PRIVKEY_PATH = "/fake/gestion"
    reset_vm(); capt["cmds"].clear()
    assert motor.habilitar_ssh_clave(JobFalso(), "10.0.0.1", "Clave-1") == "habilitado"
    assert LOGINS == [("10.0.0.1", "Clave-1")] and motor.SSHD_CLAVE_LIMPIEZA in capt["cmds"], "login real y limpieza del respaldo"
    # config efectiva incompleta (sshd -T -C) → no hay login de prueba, se revierte y se confirma
    for malo in ("permitrootlogin without-password\npasswordauthentication yes\nauthenticationmethods any\n",
                 "permitrootlogin yes\npasswordauthentication no\nauthenticationmethods any\n",
                 "permitrootlogin yes\npasswordauthentication yes\nauthenticationmethods publickey\n", ""):
        reset_vm(); capt["cmds"].clear(); VM["efectivo"] = malo
        assert motor.habilitar_ssh_clave(JobFalso(), "10.0.0.1", "x") == "deshabilitado", malo
        assert LOGINS == [] and motor.SSHD_CLAVE_ROLLBACK in capt["cmds"], malo
    # config bien pero el LOGIN REAL falla → se revierte
    reset_vm(); capt["cmds"].clear(); VM["login_ok"] = False
    j = JobFalso()
    assert motor.habilitar_ssh_clave(j, "10.0.0.1", "x") == "deshabilitado" and motor.SSHD_CLAVE_ROLLBACK in capt["cmds"]
    assert any("login de prueba con contraseña falló" in t for t in j.d), j.d
    # el script falla (rc distinto de 0) → se fuerza la reversión; el error crudo no va al paso
    reset_vm(); capt["cmds"].clear()
    VM["falla_script"] = RuntimeError("'f=/etc/ssh/...' rc=5: RELOAD_FALLO secreto")
    j = JobFalso()
    assert motor.habilitar_ssh_clave(j, "10.0.0.1", "x") == "deshabilitado" and motor.SSHD_CLAVE_ROLLBACK in capt["cmds"]
    assert "RuntimeError" in j.d[-1] and "secreto" not in " ".join(j.d)
    # la reversión no queda confirmada → "incierto" (nunca afirmar que quedó deshabilitado)
    for falla_rb, salida_rb in ((RuntimeError("x"), None), (None, "permitrootlogin yes\npasswordauthentication yes\n"), (None, "")):
        reset_vm(); VM["login_ok"] = False; VM["falla_rollback"] = falla_rb
        if salida_rb is not None:
            VM["rollback"] = salida_rb
        assert motor.habilitar_ssh_clave(JobFalso(), "10.0.0.1", "x") == "incierto", (falla_rb, salida_rb)
    print("ok - habilitar_ssh_clave: éxito solo con config efectiva + login real; toda falla revierte y confirma; si no, incierto")

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
        capt["cmds"].clear(); capt["chpasswd"].clear(); capt["secv"] = 0; LOGINS.clear()
        r = cli.post("/crear", headers=ADMIN, json=dict(base, **kw))
        assert r.status_code == 200, r.get_json()
        jj = esperar(r.get_json()["job_id"])
        assert jj["estado"] == "ok", json.dumps(jj, ensure_ascii=False)[:1500]
        return jj

    # a) cliente + contraseña: aplicada, SSH verificado con login real, sin llave de entrega
    reset_vm()
    j = crear(hostname="srv1", acceso="clave")
    r = j["resultado"]
    pw = capt["chpasswd"][0][len("root:"):].strip()
    assert len(pw) == 12 and r["root_password"] == pw, "la contraseña aplicada es la que se entrega"
    assert sum(c in motor.ROOT_ESPECIALES for c in pw) == 2 and sum(c in motor.ROOT_MAYUS for c in pw) == 1, pw
    assert LOGINS == [("10.100.16.250", pw)], "login real con LA contraseña aplicada, a la IP privada"
    assert capt["secv"] == 0 and r["acceso"] == "clave" and r["acceso_clave_estado"] == "habilitado"
    assert r["ssh_cmd"] == "ssh root@38.19.57.101" and r["sin_llave_usuario"] is False and "acceso_clave_error" not in r
    assert "verificado con un login real" in j["pasos"][13]["detalle"] and "también servirá para SSH" in j["pasos"][10]["detalle"]
    assert "acceso por contraseña de root" in j["pasos"][-1]["detalle"] and motor.vm_registrada(j["vm"])["acceso"] == "clave"
    print("ok - cliente + contraseña: 12 caracteres aplicada, login real OK, sin llave ni Send")

    # b) acceso por llave (default): lo de siempre, sin tocar sshd
    j = crear(hostname="srv2")
    assert capt["secv"] == 1 and motor.SSHD_CLAVE_SCRIPT not in capt["cmds"] and capt["chpasswd"] == [] and LOGINS == []
    assert j["resultado"]["acceso"] == "llave" and motor.vm_registrada(j["vm"])["acceso"] == "llave"
    print("ok - acceso por llave (default): llave generada y entregada, sshd intacto, sin contraseña")

    # c) login real falla → revertido y confirmado: solo llave, mensaje "se revirtió", contraseña visible para consola
    reset_vm(); VM["login_ok"] = False
    j = crear(hostname="srv3", acceso="clave")
    r = j["resultado"]
    assert r["acceso_clave_estado"] == "deshabilitado" and r["ssh_cmd"] is None and r["sin_llave_usuario"] is True
    assert "se revirtió" in r["acceso_clave_error"] and r.get("root_password")
    assert "quedó solo con llave" in j["pasos"][13]["detalle"]
    # d) reversión sin confirmar → INCIERTO
    reset_vm(); VM["login_ok"] = False; VM["falla_rollback"] = RuntimeError("x")
    j = crear(hostname="srv4", acceso="clave")
    r = j["resultado"]
    assert r["acceso_clave_estado"] == "incierto" and "ESTADO INCIERTO" in r["acceso_clave_error"] and r["ssh_cmd"] is None
    assert "INCIERTO" in j["pasos"][13]["detalle"]
    # e) la contraseña no se aplicó: no se toca sshd y el mensaje pide fijarla ANTES de abrir el SSH
    reset_vm(); VM["chpasswd_falla"] = True
    j = crear(hostname="srv5", acceso="clave")
    r = j["resultado"]
    assert motor.SSHD_CLAVE_SCRIPT not in capt["cmds"] and LOGINS == []
    assert r.get("root_password_error") and "Primero define y prueba una contraseña por consola" in r["acceso_clave_error"]
    assert "la contraseña no se pudo aplicar" in j["pasos"][13]["detalle"]
    print("ok - fallas: login fallido revierte; reversión sin confirmar = incierto; sin contraseña no se abre el SSH")

    # f) modo pruebas: también se habilita y se prueba el login a la IP privada
    reset_vm()
    j = crear(hostname="srv6", acceso="clave", modo="pruebas")
    assert j["resultado"]["ssh_cmd"] == "ssh root@192.168.200.240" and LOGINS and LOGINS[0][0] == "192.168.200.240"
    print("ok - modo pruebas: acceso por contraseña habilitado y probado")
finally:
    for k, v in _orig.items():
        setattr(motor, k, v)
print("TODO OK - test_acceso_clave")
