# Tests ACCESO POR CONTRASEÑA DE ROOT (2026-10-26): solo VPS de CLIENTE desde el dashboard. El motor genera una
# contraseña (12: 2 especiales + 1 mayúscula + minúsculas/números), la aplica, habilita el SSH por contraseña con un
# drop-in propio y lo VERIFICA: config efectiva (sshd -T -C) + sshd activo + LOGIN REAL solo con contraseña contra la
# llave de host FIJADA (la del canal de gestión, presente en la VM). Toda falla revierte de forma idempotente y la
# reversión se confirma (config que bloquea + sshd activo + login RECHAZADO); si no, "incierto".
import base64
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

# ── scripts remotos ─────────────────────────────────────────────────────────
assert motor.SSHD_CLAVE_CONF == "/etc/ssh/sshd_config.d/00-acceso-clave.conf"
assert "00-acceso-clave.conf" < "00-hosting.conf" < "50-cloud-init.conf" < "99-vps.conf", "sshd: gana el primero leído"
assert not motor._SSHD_PREVIO.endswith(".conf"), "el respaldo no debe incluirse en sshd"
for nombre in ("SSHD_CLAVE_SCRIPT", "SSHD_CLAVE_ROLLBACK", "SSHD_CLAVE_LIMPIEZA"):
    sc = getattr(motor, nombre)
    assert "@CONF@" not in sc and "@PREVIO@" not in sc and "@EFECTIVO@" not in sc and "@ACTIVO@" not in sc, nombre
    assert sc.count("@HOSTKEYS@") == sc.count("@@HOSTKEYS@@"), nombre   # solo el marcador impreso, ningún placeholder
    tmp = os.path.join(tempfile.mkdtemp(), "s.sh")
    with open(tmp, "w", newline="\n") as fh:
        fh.write(sc + "\n")
    assert subprocess.run(["bash", "-n", tmp]).returncode == 0, "%s: sintaxis bash inválida" % nombre
sc, rb = motor.SSHD_CLAVE_SCRIPT, motor.SSHD_CLAVE_ROLLBACK
assert "printf '%s\\n'" in sc and "'PermitRootLogin yes' 'PasswordAuthentication yes'" in sc
assert 'sshd -T -C "user=root,host=$c,addr=$c"' in sc and "@@HOSTKEYS@@" in sc and "activo=si" in sc
# reversión IDEMPOTENTE: el respaldo se COPIA (nunca se mueve), en el script y en el rollback
assert 'cp -p "$b" "$f"' in sc and 'cp -p "$b" "$f"' in rb and 'mv -f "$b"' not in sc and 'mv -f "$b"' not in rb
assert "sshd -t || exit 3" in rb and "sshd -T -C" in rb and "@@HOSTKEYS@@" in rb and "ssh.socket" in rb
print("ok - scripts: fijos, sintaxis válida, reversión idempotente, config efectiva + sshd activo + llaves de host")

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

# ── VM simulada con estado ──────────────────────────────────────────────────
class Llave:
    def __init__(self, nombre, cuerpo): self.n, self.b = nombre, cuerpo
    def get_name(self): return self.n
    def asbytes(self): return self.b
K_VM = Llave("ssh-ed25519", b"llave-de-la-vm")
K_IMPOSTOR = Llave("ssh-ed25519", b"llave-de-otro")
class AuthExc(Exception): pass
class SSHExc(Exception): pass
class Politica: pass
VM, LOGINS, capt = {}, [], {"cmds": [], "chpasswd": [], "secv": 0}
def linea_hostkey(k): return "%s %s root@vm" % (k.get_name(), base64.b64encode(k.asbytes()).decode())
def salida(clave_abierta, activo=True, incompleta=False, hostkeys=(K_VM,)):
    cfg = ("permitrootlogin yes\npasswordauthentication yes\n" if clave_abierta
           else "permitrootlogin without-password\npasswordauthentication no\n")
    if not incompleta:
        cfg += "authenticationmethods any\n"
    return ("activo=%s\n" % ("si" if activo else "no")) + cfg + "@@HOSTKEYS@@\n" + "\n".join(linea_hostkey(k) for k in hostkeys) + "\n"
class _Transport:
    def get_remote_server_key(self): return VM["mgmt_key"]
class _Cli:
    def __init__(self): self.pol = None
    def set_missing_host_key_policy(self, p): self.pol = p
    def get_transport(self): return _Transport()
    def connect(self, ip, **k):
        if "password" not in k:
            return   # canal de gestión (llave)
        assert k.get("allow_agent") is False and k.get("look_for_keys") is False and "key_filename" not in k
        # el servidor presenta su llave de host ANTES de autenticar: la política fijada decide
        if not isinstance(self.pol, Politica):
            raise AssertionError("el login con contraseña debe usar una política fijada, no AutoAdd")
        self.pol.missing_host_key(self, ip, VM["server_key"])
        LOGINS.append((ip, k["password"]))   # recién aquí la contraseña viaja
        forz = VM.get("login_forzado")
        if forz == "error":
            raise Exception("timeout")
        if forz == "rechazado" or (forz is None and not VM["abierto"]):
            raise AuthExc("Authentication failed.")
    def close(self): pass
class _Pm:
    SSHClient = _Cli
    AutoAddPolicy = object
    MissingHostKeyPolicy = Politica
    AuthenticationException = AuthExc
    SSHException = SSHExc
class JobFalso:
    def __init__(self): self.d = []
    def detalle(self, t): self.d.append(t)
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
        VM["abierto"] = VM["queda_abierto"]
        return VM.get("salida_script") or salida(VM["abierto"], activo=VM["activo"], hostkeys=VM["hostkeys"])
    if comando == motor.SSHD_CLAVE_ROLLBACK:
        if VM["falla_rollback"]:
            raise VM["falla_rollback"]
        VM["abierto"] = VM["rollback_deja_abierto"]
        return VM.get("salida_rollback") or salida(VM["abierto"], activo=VM["activo"], hostkeys=VM["hostkeys"])
    return ""
def reset_vm(**kw):
    VM.clear()
    VM.update(abierto=False, queda_abierto=True, rollback_deja_abierto=False, activo=True, hostkeys=(K_VM,),
              mgmt_key=K_VM, server_key=K_VM, login_forzado=None, falla_script=None, falla_rollback=None,
              chpasswd_falla=False)
    VM.update(kw)
    LOGINS.clear(); capt["cmds"].clear()
reset_vm()

_orig = {k: getattr(motor, k) for k in ("govc", "esxi_ssh", "ip_libre_produccion", "ip_libre_pruebas", "ip_publica_libre",
                                        "crear_nat", "datastore_libre_gb", "listar_wrapper", "netbox_ip_add",
                                        "securizar_vps", "paramiko", "MGMT_PRIVKEY_PATH", "mikrotik", "_ssh_exec")}
try:
    motor.paramiko = _Pm
    motor._ssh_exec = fake_exec
    H = lambda pw="Clave-1": motor.habilitar_ssh_clave(JobFalso(), "10.0.0.1", pw)
    # sin llave de gestión → nada se toca
    motor.MGMT_PRIVKEY_PATH = ""
    assert H() == "deshabilitado" and capt["cmds"] == []
    motor.MGMT_PRIVKEY_PATH = "/fake/gestion"
    # éxito: config efectiva + login real OK contra la llave fijada; limpieza del respaldo
    reset_vm()
    assert H() == "habilitado" and LOGINS == [("10.0.0.1", "Clave-1")] and motor.SSHD_CLAVE_LIMPIEZA in capt["cmds"]
    # config efectiva incompleta o que no abre → se revierte; la reversión se confirma con un login RECHAZADO
    for kw in (dict(queda_abierto=False), dict(salida_script=salida(True, incompleta=True))):
        reset_vm(**kw)
        assert H() == "deshabilitado", kw
        # la ÚNICA vez que viaja la contraseña es la prueba de rechazo, después de revertir
        assert motor.SSHD_CLAVE_ROLLBACK in capt["cmds"] and LOGINS == [("10.0.0.1", "Clave-1")], (kw, LOGINS)
    # sshd inactivo: no se puede confirmar nada → incierto
    reset_vm(activo=False)
    assert H() == "incierto", "sshd inactivo: no se puede confirmar nada"
    # login real rechazado en la habilitación → revierte y la reversión se confirma (el rechazo se repite)
    reset_vm(login_forzado="rechazado")
    assert H() == "deshabilitado" and motor.SSHD_CLAVE_ROLLBACK in capt["cmds"]
    # login con error de transporte → revierte, pero la confirmación tampoco puede hacer login → incierto
    reset_vm(login_forzado="error")
    assert H() == "incierto" and motor.SSHD_CLAVE_ROLLBACK in capt["cmds"]
    # llave de host del canal de gestión que la VM NO reporta → jamás se envía la contraseña; sin confirmación → incierto
    reset_vm(hostkeys=(K_IMPOSTOR,))
    assert H() == "incierto" and LOGINS == [], "sin llave coherente la contraseña no viaja"
    # el servidor del login presenta OTRA llave (suplantación) → la política fijada corta antes de enviar la contraseña
    reset_vm(server_key=K_IMPOSTOR)
    assert H() == "incierto" and LOGINS == [], "la contraseña nunca llega al impostor"
    # el script falla → se fuerza la reversión, confirmada
    reset_vm(falla_script=RuntimeError("'f=...' rc=5: RELOAD_FALLO secreto"))
    j = JobFalso()
    assert motor.habilitar_ssh_clave(j, "10.0.0.1", "x") == "deshabilitado" and motor.SSHD_CLAVE_ROLLBACK in capt["cmds"]
    assert "RuntimeError" in " ".join(j.d) and "secreto" not in " ".join(j.d)
    # reversiones que NO se pueden confirmar → incierto
    for kw in (dict(queda_abierto=False, falla_rollback=RuntimeError("x")),
               dict(queda_abierto=False, salida_rollback=salida(False, incompleta=True)),
               dict(queda_abierto=False, rollback_deja_abierto=True),
               dict(login_forzado="rechazado", rollback_deja_abierto=True)):
        reset_vm(**kw)
        assert H() == "incierto", kw
    print("ok - habilitar_ssh_clave: éxito solo con config efectiva + sshd activo + login real fijado; toda falla revierte; confirmación exige login rechazado; si no, incierto; la contraseña nunca va a una llave no verificada")

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
        capt["chpasswd"].clear(); capt["secv"] = 0
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
    reset_vm()
    j = crear(hostname="srv2")
    assert capt["secv"] == 1 and motor.SSHD_CLAVE_SCRIPT not in capt["cmds"] and capt["chpasswd"] == [] and LOGINS == []
    assert j["resultado"]["acceso"] == "llave" and motor.vm_registrada(j["vm"])["acceso"] == "llave"
    print("ok - acceso por llave (default): llave generada y entregada, sshd intacto, sin contraseña")

    # c) la config no abre → revertido y confirmado: solo llave, "se revirtió", contraseña visible para consola
    reset_vm(queda_abierto=False)
    j = crear(hostname="srv3", acceso="clave")
    r = j["resultado"]
    assert r["acceso_clave_estado"] == "deshabilitado" and r["ssh_cmd"] is None and r["sin_llave_usuario"] is True
    assert "se revirtió" in r["acceso_clave_error"] and r.get("root_password")
    assert "quedó solo con llave" in j["pasos"][13]["detalle"]
    # d) reversión sin confirmar → INCIERTO
    reset_vm(queda_abierto=False, falla_rollback=RuntimeError("x"))
    j = crear(hostname="srv4", acceso="clave")
    r = j["resultado"]
    assert r["acceso_clave_estado"] == "incierto" and "ESTADO INCIERTO" in r["acceso_clave_error"] and r["ssh_cmd"] is None
    assert "INCIERTO" in j["pasos"][13]["detalle"]
    # e) la contraseña no se aplicó: no se toca sshd y el mensaje pide fijarla ANTES de abrir el SSH
    reset_vm(chpasswd_falla=True)
    j = crear(hostname="srv5", acceso="clave")
    r = j["resultado"]
    assert motor.SSHD_CLAVE_SCRIPT not in capt["cmds"] and LOGINS == []
    assert r.get("root_password_error") and "Primero define y prueba una contraseña por consola" in r["acceso_clave_error"]
    assert "la contraseña no se pudo aplicar" in j["pasos"][13]["detalle"]
    print("ok - fallas: revertido confirmado = solo llave; reversión sin confirmar = incierto; sin contraseña no se abre el SSH")

    # f) modo pruebas: también se habilita y se prueba el login a la IP privada
    reset_vm()
    j = crear(hostname="srv6", acceso="clave", modo="pruebas")
    assert j["resultado"]["ssh_cmd"] == "ssh root@192.168.200.240" and LOGINS and LOGINS[0][0] == "192.168.200.240"
    print("ok - modo pruebas: acceso por contraseña habilitado y probado")
finally:
    for k, v in _orig.items():
        setattr(motor, k, v)
print("TODO OK - test_acceso_clave")
