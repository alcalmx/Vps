# Tests ACCESO POR CONTRASEÑA DE ROOT (2026-10-26): solo VPS de CLIENTE desde el dashboard. El motor genera una
# contraseña (12: 2 especiales + 1 mayúscula + minúsculas/números), la aplica, habilita el SSH por contraseña con un
# drop-in propio y lo VERIFICA: config efectiva (sshd -T -C) + sshd activo + LOGIN REAL solo con contraseña. La llave de
# host se captura en el canal que recibió el chpasswd y se EXIGE en todas las conexiones siguientes. Preparación con tres
# estados (ausente / respaldo completo / fallida: no toca el original); reversión idempotente y confirmada (config que
# bloquea + sshd activo + login RECHAZADO); si no, "incierto". Uso personal y WHMCS siguen con llave.
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

# ── scripts remotos: estáticos ──────────────────────────────────────────────
assert motor.SSHD_CLAVE_CONF == "/etc/ssh/sshd_config.d/00-acceso-clave.conf"
assert "00-acceso-clave.conf" < "00-hosting.conf" < "50-cloud-init.conf" < "99-vps.conf", "sshd: gana el primero leído"
assert not motor._SSHD_PREVIO.endswith(".conf") and not motor._SSHD_AUSENTE.endswith(".conf"), "sshd no los incluye"
for nombre in ("SSHD_CLAVE_SCRIPT", "SSHD_CLAVE_ROLLBACK", "SSHD_CLAVE_LIMPIEZA"):
    sc = getattr(motor, nombre)
    for ph in ("@CONF@", "@PREVIO@", "@AUSENTE@", "@EFECTIVO@", "@ACTIVO@"):
        assert ph not in sc, (nombre, ph)
    assert sc.count("@HOSTKEYS@") == sc.count("@@HOSTKEYS@@"), nombre   # solo el marcador impreso
    tmp = os.path.join(tempfile.mkdtemp(), "s.sh")
    with open(tmp, "w", newline="\n") as fh:
        fh.write(sc + "\n")
    assert subprocess.run(["bash", "-n", tmp]).returncode == 0, "%s: sintaxis bash inválida" % nombre
sc, rb = motor.SSHD_CLAVE_SCRIPT, motor.SSHD_CLAVE_ROLLBACK
assert 'sshd -T -C "user=root,host=$c,addr=$c"' in sc and "@@HOSTKEYS@@" in sc and "activo=si" in sc
assert 'mv -f "$b"' not in sc and 'mv -f "$b"' not in rb, "el respaldo nunca se mueve (reversión idempotente)"
print("ok - scripts: fijos, sintaxis válida, sin placeholders")

# ── scripts remotos: ejecución REAL con bash sobre archivos temporales (sshd/systemctl/cp de prueba) ──────────
def msys(p):
    p = p.replace("\\", "/")
    return ("/" + p[0].lower() + p[2:]) if len(p) > 1 and p[1] == ":" else p
caja = tempfile.mkdtemp()
shim = os.path.join(caja, "bin")
os.makedirs(shim)
def shim_w(nombre, txt):
    with open(os.path.join(shim, nombre), "w", newline="\n") as fh:
        fh.write(txt)
shim_w("sshd", '#!/bin/bash\ncase "$1" in\n  -t) [ -n "$SSHD_T_FALLA" ] && exit 1; exit 0;;\n'
               '  -T) echo "permitrootlogin yes"; echo "passwordauthentication yes"; echo "authenticationmethods any";;\nesac\n')
shim_w("systemctl", '#!/bin/bash\nexit 0\n')
shim_w("cp", '#!/bin/bash\nif [ -n "$CP_FALLA" ]; then exit 1; fi\nexec /usr/bin/cp "$@"\n')
CONF = caja.replace("\\", "/") + "/00-acceso-clave.conf"
PREV, AUS = CONF + ".vps-engine-previo", CONF + ".vps-engine-ausente"
def correr(script, **env):
    g = 'export PATH="%s:$PATH"\n' % msys(shim) + script.replace(motor.SSHD_CLAVE_CONF, CONF)
    p = subprocess.run(["bash", "-c", g], capture_output=True, text=True, env=dict(os.environ, **env))
    return p.returncode, p.stdout
def leer(p=CONF):
    return open(p).read() if os.path.exists(p) else None
def limpiar():
    for x in (CONF, PREV, AUS, CONF + ".tmp", PREV + ".tmp"):
        if os.path.exists(x):
            os.remove(x)
# 1) sin archivo previo: se instala con marca de ausencia; revertir (dos veces) lo borra
limpiar()
rc, out = correr(sc)
assert rc == 0 and "PermitRootLogin yes" in leer() and os.path.exists(AUS) and not os.path.exists(PREV), (rc, out)
assert "permitrootlogin yes" in out and "activo=si" in out
for _ in range(2):
    rc, out = correr(rb)
    assert rc == 0 and leer() is None and "SIN_TRANSACCION" not in out, (rc, out)
# 2) con archivo previo: respaldo completo; revertir (dos veces) lo deja IGUAL al original
limpiar()
open(CONF, "w", newline="\n").write("ORIGINAL\n")
rc, out = correr(sc)
assert rc == 0 and "PermitRootLogin yes" in leer() and leer(PREV) == "ORIGINAL\n" and not os.path.exists(AUS)
for _ in range(2):
    rc, out = correr(rb)
    assert rc == 0 and leer() == "ORIGINAL\n", (rc, leer())
# 3) con archivo previo y la COPIA del respaldo falla: exit 6, el original NO se toca; la reversión no lo modifica
limpiar()
open(CONF, "w", newline="\n").write("ORIGINAL\n")
rc, out = correr(sc, CP_FALLA="1")
assert rc == 6 and "PREPARACION_FALLIDA" in out and leer() == "ORIGINAL\n" and not os.path.exists(PREV) and not os.path.exists(AUS)
rc, out = correr(rb)
assert rc == 0 and "SIN_TRANSACCION" in out and leer() == "ORIGINAL\n", (rc, out, leer())
# 4) sshd -t falla tras instalar: el script restaura solo (con previo → original; sin previo → borrado)
limpiar()
open(CONF, "w", newline="\n").write("ORIGINAL\n")
rc, out = correr(sc, SSHD_T_FALLA="1")
assert rc == 3 and "SSHD_INVALIDO" in out and leer() == "ORIGINAL\n"
limpiar()
rc, out = correr(sc, SSHD_T_FALLA="1")
assert rc == 3 and leer() is None
# 5) limpieza tras el éxito borra respaldo y marca
limpiar()
open(CONF, "w", newline="\n").write("ORIGINAL\n")
correr(sc)
rc, _ = correr(motor.SSHD_CLAVE_LIMPIEZA)
assert rc == 0 and not os.path.exists(PREV) and not os.path.exists(AUS) and "PermitRootLogin yes" in leer()
limpiar()
print("ok - scripts ejecutados de verdad: ausente / respaldo completo / preparación fallida (original intacto); reversión idempotente")

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

# ── VM simulada con estado e identidad ──────────────────────────────────────
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
def salida(clave_abierta, activo=True, incompleta=False, hostkeys=None):
    cfg = ("permitrootlogin yes\npasswordauthentication yes\n" if clave_abierta
           else "permitrootlogin without-password\npasswordauthentication no\n")
    if not incompleta:
        cfg += "authenticationmethods any\n"
    hk = VM["hostkeys"] if hostkeys is None else hostkeys
    return ("activo=%s\n" % ("si" if activo else "no")) + cfg + "@@HOSTKEYS@@\n" + "\n".join(linea_hostkey(k) for k in hk) + "\n"
class _Transport:
    def get_remote_server_key(self): return VM["mgmt_key"]
class _Cli:
    def __init__(self): self.pol = None
    def set_missing_host_key_policy(self, p): self.pol = p
    def get_transport(self): return _Transport()
    def connect(self, ip, **k):
        # el servidor presenta su llave ANTES de autenticar: una política fijada decide
        if isinstance(self.pol, Politica):
            self.pol.missing_host_key(self, ip, VM["server_key"] if "password" in k else VM["mgmt_key"])
        if "password" not in k:
            return   # canal de gestión (llave)
        assert isinstance(self.pol, Politica), "el login con contraseña debe usar la política fijada, no AutoAdd"
        assert k.get("allow_agent") is False and k.get("look_for_keys") is False and "key_filename" not in k
        LOGINS.append((ip, k["password"]))   # recién aquí viaja la contraseña
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
        if VM.get("suplantar_tras_chpasswd"):   # otro equipo toma la IP DESPUÉS de recibir la contraseña
            VM.update(mgmt_key=K_IMPOSTOR, server_key=K_IMPOSTOR, hostkeys=(K_IMPOSTOR,))
        return ""
    if comando == motor.SSHD_CLAVE_SCRIPT:
        if VM["falla_script"]:
            raise VM["falla_script"]
        VM["abierto"] = VM["queda_abierto"]
        return VM.get("salida_script") or salida(VM["abierto"], activo=VM["activo"])
    if comando == motor.SSHD_CLAVE_ROLLBACK:
        if VM["falla_rollback"]:
            raise VM["falla_rollback"]
        VM["abierto"] = VM["rollback_deja_abierto"]
        return VM.get("salida_rollback") or salida(VM["abierto"], activo=VM["activo"])
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
    H = lambda pw="Clave-1", hk=K_VM: motor.habilitar_ssh_clave(JobFalso(), "10.0.0.1", pw, hk)
    # sin llave de gestión o sin llave de host fijada → nada se toca
    motor.MGMT_PRIVKEY_PATH = ""
    assert H() == "deshabilitado" and capt["cmds"] == []
    motor.MGMT_PRIVKEY_PATH = "/fake/gestion"
    reset_vm()
    assert H(hk=None) == "deshabilitado" and capt["cmds"] == [] and LOGINS == [], "sin llave capturada no se toca la VM"
    # set_root_password devuelve la llave de host del canal que recibió el chpasswd
    reset_vm()
    assert motor.set_root_password("10.0.0.1", "Clave-1") is K_VM and capt["chpasswd"][-1] == "root:Clave-1\n"
    cap = {}
    assert motor.aplicar_root_password(JobFalso(), "10.0.0.1", "Clave-1", True, para_ssh=True, captura=cap) is True
    assert cap["hostkey"] is K_VM
    # éxito: config efectiva + login real OK con la llave fijada; limpieza de respaldo/marca
    reset_vm()
    assert H() == "habilitado" and LOGINS == [("10.0.0.1", "Clave-1")] and motor.SSHD_CLAVE_LIMPIEZA in capt["cmds"]
    # config efectiva incompleta o que no abre → se revierte; la confirmación es un login RECHAZADO
    for kw in (dict(queda_abierto=False), dict(salida_script=salida(True, incompleta=True, hostkeys=(K_VM,)))):
        reset_vm(**kw)
        assert H() == "deshabilitado", kw
        assert motor.SSHD_CLAVE_ROLLBACK in capt["cmds"] and LOGINS == [("10.0.0.1", "Clave-1")], (kw, LOGINS)
    reset_vm(activo=False)
    assert H() == "incierto", "sshd inactivo: no se puede confirmar nada"
    reset_vm(login_forzado="rechazado")
    assert H() == "deshabilitado" and motor.SSHD_CLAVE_ROLLBACK in capt["cmds"]
    reset_vm(login_forzado="error")
    assert H() == "incierto" and motor.SSHD_CLAVE_ROLLBACK in capt["cmds"]
    # la VM no reporta la llave fijada → no se envía la contraseña; sin confirmar → incierto
    reset_vm(hostkeys=(K_IMPOSTOR,))
    assert H() == "incierto" and LOGINS == [], "sin llave coherente la contraseña no viaja"
    # otro servidor responde en la conexión de gestión (llave distinta a la fijada) → se corta antes de autenticar
    reset_vm(mgmt_key=K_IMPOSTOR, server_key=K_IMPOSTOR, hostkeys=(K_IMPOSTOR,))
    assert H() == "incierto" and LOGINS == [] and motor.SSHD_CLAVE_SCRIPT not in capt["cmds"], "nada se ejecuta en el impostor"
    # solo el login con contraseña cae en otro servidor → la política fijada lo corta antes de enviar la contraseña
    reset_vm(server_key=K_IMPOSTOR)
    assert H() == "incierto" and LOGINS == [], "la contraseña nunca llega al impostor"
    # el script falla (incluida preparación fallida, exit 6) → se fuerza la reversión, confirmada
    for falla in (RuntimeError("'f=...' rc=5: RELOAD_FALLO secreto"), RuntimeError("'f=...' rc=6: PREPARACION_FALLIDA")):
        reset_vm(falla_script=falla)
        j = JobFalso()
        assert motor.habilitar_ssh_clave(j, "10.0.0.1", "x", K_VM) == "deshabilitado" and motor.SSHD_CLAVE_ROLLBACK in capt["cmds"]
        assert "RuntimeError" in " ".join(j.d) and "secreto" not in " ".join(j.d)
    # reversiones que NO se pueden confirmar → incierto
    for kw in (dict(queda_abierto=False, falla_rollback=RuntimeError("x")),
               dict(queda_abierto=False, salida_rollback=salida(False, incompleta=True, hostkeys=(K_VM,))),
               dict(queda_abierto=False, rollback_deja_abierto=True),
               dict(login_forzado="rechazado", rollback_deja_abierto=True)):
        reset_vm(**kw)
        assert H() == "incierto", kw
    print("ok - habilitar_ssh_clave: llave del chpasswd exigida en toda conexión; éxito solo con config efectiva + sshd activo + login real; toda falla revierte; confirmación = login rechazado; si no, incierto")

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

    # c) Codex r3: otro equipo toma la IP ENTRE el chpasswd y la habilitación → nada se ejecuta ni se envía; incierto
    reset_vm(suplantar_tras_chpasswd=True)
    j = crear(hostname="srv7", acceso="clave")
    r = j["resultado"]
    assert motor.SSHD_CLAVE_SCRIPT not in capt["cmds"] and LOGINS == [], (capt["cmds"], LOGINS)
    assert r["acceso_clave_estado"] == "incierto" and r["ssh_cmd"] is None and "ESTADO INCIERTO" in r["acceso_clave_error"]
    print("ok - suplantación entre el chpasswd y la habilitación: la llave fijada corta todo; estado incierto")

    # d) la config no abre → revertido y confirmado: solo llave, "se revirtió", contraseña visible para consola
    reset_vm(queda_abierto=False)
    j = crear(hostname="srv3", acceso="clave")
    r = j["resultado"]
    assert r["acceso_clave_estado"] == "deshabilitado" and r["ssh_cmd"] is None and r["sin_llave_usuario"] is True
    assert "se revirtió" in r["acceso_clave_error"] and r.get("root_password")
    assert "quedó solo con llave" in j["pasos"][13]["detalle"]
    # e) reversión sin confirmar → INCIERTO
    reset_vm(queda_abierto=False, falla_rollback=RuntimeError("x"))
    j = crear(hostname="srv4", acceso="clave")
    r = j["resultado"]
    assert r["acceso_clave_estado"] == "incierto" and "ESTADO INCIERTO" in r["acceso_clave_error"] and r["ssh_cmd"] is None
    assert "INCIERTO" in j["pasos"][13]["detalle"]
    # f) la contraseña no se aplicó: no se toca sshd y el mensaje pide fijarla ANTES de abrir el SSH
    reset_vm(chpasswd_falla=True)
    j = crear(hostname="srv5", acceso="clave")
    r = j["resultado"]
    assert motor.SSHD_CLAVE_SCRIPT not in capt["cmds"] and LOGINS == []
    assert r.get("root_password_error") and "Primero define y prueba una contraseña por consola" in r["acceso_clave_error"]
    assert "la contraseña no se pudo aplicar" in j["pasos"][13]["detalle"]
    print("ok - fallas: revertido confirmado = solo llave; reversión sin confirmar = incierto; sin contraseña no se abre el SSH")

    # g) modo pruebas: también se habilita y se prueba el login a la IP privada
    reset_vm()
    j = crear(hostname="srv6", acceso="clave", modo="pruebas")
    assert j["resultado"]["ssh_cmd"] == "ssh root@192.168.200.240" and LOGINS and LOGINS[0][0] == "192.168.200.240"
    print("ok - modo pruebas: acceso por contraseña habilitado y probado")
finally:
    for k, v in _orig.items():
        setattr(motor, k, v)
print("TODO OK - test_acceso_clave")
