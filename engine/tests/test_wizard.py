# Tests Fase D (wizard de enrolamiento): almacén de secretos, validaciones de
# /hosts/preparar y /hosts/<id>/copiar-doradas, y flujo preparar con mocks.
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
ENROL = tempfile.mkdtemp()
os.environ["ENROL_DIR"] = ENROL
sys.path.insert(0, "c:/Users/alcalmx/Desktop/Proyectos/Vps/engine")
import app as motor  # noqa: E402

cli = motor.app.test_client()
ADMIN = {"X-Auth-Token": "tok-admin-test"}
WHMCS = {"X-Auth-Token": "tok-whmcs-test"}

# ── almacén de secretos ──────────────────────────────────────────────────────
assert motor.secreto_host("NO_EXISTE_XX") is None
motor.secreto_host_guardar("GOVC_PASSWORD_T1", "clave-uno")
motor.secreto_host_guardar("GOVC_PASSWORD_T2", "clave=con=igual")
assert motor.secreto_host("GOVC_PASSWORD_T1") == "clave-uno"
assert motor.secreto_host("GOVC_PASSWORD_T2") == "clave=con=igual", "valor con '=' se preserva"
motor.secreto_host_guardar("GOVC_PASSWORD_T1", "clave-rotada")
assert motor.secreto_host("GOVC_PASSWORD_T1") == "clave-rotada", "rotación reemplaza"
with open(motor.SECRETOS_HOSTS_PATH, encoding="utf-8") as f:
    contenido = f.read()
assert contenido.count("GOVC_PASSWORD_T1=") == 1, "sin duplicados tras rotar"
# el entorno tiene prioridad (hosts enrolados a mano via engine.env)
os.environ["GOVC_PASSWORD_T1"] = "del-entorno"
assert motor.secreto_host("GOVC_PASSWORD_T1") == "del-entorno"
del os.environ["GOVC_PASSWORD_T1"]
# prefijo no confunde: T1 no matchea T1B
motor.secreto_host_guardar("GOVC_PASSWORD_T1B", "otra")
assert motor.secreto_host("GOVC_PASSWORD_T1") == "clave-rotada"
print("ok - almacén de secretos: guardar/rotar/prioridad-env/prefijos")

# ── /hosts/preparar: auth y validaciones ─────────────────────────────────────
r = cli.post("/hosts/preparar", json={"accion": "huella", "ip": "192.168.200.51"})
assert r.status_code == 401
r = cli.post("/hosts/preparar", json={"accion": "huella", "ip": "192.168.200.51"}, headers=WHMCS)
assert r.status_code == 403, "whmcs no puede enrolar hosts"
r = cli.post("/hosts/preparar", json={"accion": "huella", "ip": "999.1.1.1"}, headers=ADMIN)
assert r.status_code == 400 and "ip" in r.get_json()["error"]
r = cli.post("/hosts/preparar", json={"accion": "otra", "ip": "192.0.2.1"}, headers=ADMIN)
assert r.status_code == 400 and "accion" in r.get_json()["error"]

base_ej = {"accion": "ejecutar", "ip": "192.0.2.1", "datastore": "ds1",
           "huella_confirmada": "SHA256:xxxx", "root_password": "pw",
           "uso_clientes": True, "uso_admin": True}
r = cli.post("/hosts/preparar", json=dict(base_ej, id="MAL_ID!"), headers=ADMIN)
assert r.status_code == 400 and "id inválido" in r.get_json()["error"]
r = cli.post("/hosts/preparar", json=dict(base_ej, id="esxi-t1", huella_confirmada="xxxx"), headers=ADMIN)
assert r.status_code == 400 and "huella" in r.get_json()["error"]
r = cli.post("/hosts/preparar", json=dict(base_ej, id="esxi-t1", root_password=123), headers=ADMIN)
assert r.status_code == 400 and "root_password" in r.get_json()["error"]
r = cli.post("/hosts/preparar", json=dict(base_ej, id="esxi-t1", root_password="a\nb"), headers=ADMIN)
assert r.status_code == 400 and "control" in r.get_json()["error"]
r = cli.post("/hosts/preparar", json=dict(base_ej, id="esxi-t1", root_password="a" * 200), headers=ADMIN)
assert r.status_code == 400
r = cli.post("/hosts/preparar", json=dict(base_ej, id="esxi-t1",
             diag_pubkey="ssh-ed25519 AAAA cmt"), headers=ADMIN)
assert r.status_code == 400 and "diag_pubkey" in r.get_json()["error"], "ed25519 rechazada (ESXi 8)"
r = cli.post("/hosts/preparar", json=dict(base_ej, id="esxi-t1", datastore=""), headers=ADMIN)
assert r.status_code == 400 and "datastore" in r.get_json()["error"]
# datastore con metacaracteres de shell / traversal → 400 (Codex wizard #1)
for ds_malo in ("ds$(id)", "ds x", "a;b", "../otro", "ds/sub", 'ds"q', "ds`w`"):
    r = cli.post("/hosts/preparar", json=dict(base_ej, id="esxi-t1", datastore=ds_malo), headers=ADMIN)
    assert r.status_code == 400 and "datastore" in r.get_json()["error"], ds_malo
print("ok - /hosts/preparar: auth + validaciones (id/huella/root_password/diag_pubkey/datastore+inyección)")

# ── flujo preparar con mocks (sin red): job completo y registro ──────────────
class _FakeKey:
    def get_name(self): return "ssh-rsa"
    def asbytes(self): return b"fake-key-bytes"
    def get_base64(self): return "FAKEB64" * 10

_ejecutado = {"exec": [], "govc": []}
_forzar_fallo = {"patron": None}   # si el comando contiene el patrón → rc=1 (simula fallo remoto)
class _FakeCli:
    def exec_command(self, cmd, timeout=60):
        _ejecutado["exec"].append(cmd)
        _rc = 1 if (_forzar_fallo["patron"] and _forzar_fallo["patron"] in cmd) else 0
        class _Ch:
            def __init__(self, data): self._d = data
            def recv(self, n):
                d, self._d = self._d, b""   # una vez datos, luego EOF
                return d
            def recv_stderr(self, n): return b""
            def recv_exit_status(self): return _rc
            def exit_status_ready(self): return True
            def close(self): pass
            def shutdown_write(self): pass
        if "index($0,k)>0" in cmd:       # verificación de la llave del motor por datastore
            _data = b"1 1\n"             # 1 línea con la llave, 1 confinada al wrapper
        elif "grep -cF" in cmd:
            _data = b"0\n"
        elif "permission list" in cmd:   # CSV real: esxcli rotula los roles custom como "Custom"
            _data = b"IsGroup,Principal,Role,RoleDescription,\nfalse,root,Admin,Full access rights,\nfalse,svc-vps,Custom,,\n"
        else:
            _data = b""
        ch = _Ch(_data)
        class _S:
            def __init__(self, c): self.channel = c
            def write(self, *a): pass
            def flush(self): pass
            def read(self, n=-1): return b""
        return _S(ch), _S(ch), _S(ch)
    def close(self): pass

motor.huella_host = lambda ip, puerto=22: (_FakeKey(), "SHA256:HUELLA-OK")
motor.govc_root = lambda args, ip, pw, timeout=60, usuario="root": _ejecutado["govc"].append((usuario, args[0])) or ""
motor.cliente_root_esxi = lambda ip, p, k, password=None, root_key=None: _FakeCli()
motor._bootstrap_key = lambda: _FakeKey()
motor.pin_host_key = lambda ip, p, k: None
_rsa_fake = type("R", (), {"get_base64": lambda s: "RSAB64" * 20,
                           "write_private_key": lambda s, f: f.write("PRIV")})()
motor.paramiko.RSAKey.generate = staticmethod(lambda bits: _rsa_fake)
motor.paramiko.RSAKey.from_private_key_file = staticmethod(lambda p: _rsa_fake)  # reuso en re-preparación
motor.govc = lambda *a, **k: ""                      # validación en vivo (about)
motor.esxi_ssh = lambda cmd, timeout=300, host=None: "pong" if cmd == "ping" else "OK"
motor.datastore_libre_gb = lambda host=None: 500
# template del wrapper para el mock
_wt = os.path.join(ENROL, "wrapper-template.sh")
with open(_wt, "w", encoding="utf-8") as f:
    f.write("#!/bin/sh\nBASE=/vmfs/volumes/XXX/VPS\necho hola\n")
motor.WRAPPER_TEMPLATE = _wt

# sin ningún uso elegido → 400 (debe servir al menos a un motor)
r = cli.post("/hosts/preparar", json={"accion": "ejecutar", "id": "esxi-t9", "ip": "192.0.2.9",
             "datastore": "dsX", "huella_confirmada": "SHA256:HUELLA-OK",
             "root_password": "secreta-root"}, headers=ADMIN)
assert r.status_code == 400 and "uso" in r.get_json()["error"], r.get_json()
# con usos elegidos → procede
r = cli.post("/hosts/preparar", json={"accion": "ejecutar", "id": "esxi-t9", "ip": "192.0.2.9",
             "datastore": "dsX", "huella_confirmada": "SHA256:HUELLA-OK",
             "root_password": "secreta-root", "uso_clientes": True, "uso_admin": True}, headers=ADMIN)
assert r.status_code == 200, r.get_json()
jid = r.get_json()["job_id"]
import time as _t
for _ in range(50):
    j = cli.get("/job/%s" % jid, headers=ADMIN).get_json()
    if j["estado"] in ("ok", "error"):
        break
    _t.sleep(0.1)
assert j["estado"] == "ok", json.dumps(j["pasos"], ensure_ascii=False)
# la password de root NO aparece en ningún paso/detalle/resultado
volcado = json.dumps(j, ensure_ascii=False)
assert "secreta-root" not in volcado, "la root_password se filtró al job"
# host registrado pausado con pass_env del almacén
h = motor.host_get("esxi-t9")
assert h and h["estado"] == "pausado" and h["pass_env"] == "GOVC_PASSWORD_ESXI_T9"
assert h["uso_clientes"] == 1 and h["uso_admin"] == 1, "usos del wizard no persistidos"
assert motor.secreto_host("GOVC_PASSWORD_ESXI_T9"), "secreto svc-vps guardado en el almacén"
_govc_cmds = [c for _u, c in _ejecutado["govc"]]
assert "role.ls" in _govc_cmds and "permissions.set" in _govc_cmds
# en modo password, todas las llamadas de API van como root
assert all(u == "root" for u, _c in _ejecutado["govc"]), "modo password debe usar root en la API"
# huella distinta → job en error (anti-MITM)
r = cli.post("/hosts/preparar", json={"accion": "ejecutar", "id": "esxi-t8", "ip": "192.0.2.8",
             "datastore": "dsX", "huella_confirmada": "SHA256:OTRA",
             "root_password": "pw", "uso_clientes": True, "uso_admin": True}, headers=ADMIN)
jid = r.get_json()["job_id"]
for _ in range(50):
    j = cli.get("/job/%s" % jid, headers=ADMIN).get_json()
    if j["estado"] in ("ok", "error"):
        break
    _t.sleep(0.1)
assert j["estado"] == "error" and "no coincide" in j["error"]
assert motor.host_get("esxi-t8") is None, "host NO registrado si la huella no coincide"
print("ok - flujo preparar: job ok, secreto en almacén, sin fuga de root_password, anti-MITM")

# ── modo LLAVE (auth_modo='llave'): sin clave root, svc-vps se auto-rebaja ──────
# falta de root_password NO es error en modo llave
_ejecutado["govc"].clear(); _ejecutado["exec"].clear()
r = cli.post("/hosts/preparar", json={"accion": "ejecutar", "id": "esxi-llave", "ip": "192.0.2.7",
             "datastore": "dsK", "huella_confirmada": "SHA256:HUELLA-OK",
             "auth_modo": "llave", "uso_clientes": False, "uso_admin": True}, headers=ADMIN)
assert r.status_code == 200, r.get_json()
_jidk = r.get_json()["job_id"]
for _ in range(50):
    jk = cli.get("/job/%s" % _jidk, headers=ADMIN).get_json()
    if jk["estado"] in ("ok", "error"):
        break
    _t.sleep(0.1)
assert jk["estado"] == "ok", json.dumps(jk["pasos"], ensure_ascii=False)
h = motor.host_get("esxi-llave")
assert h and h["uso_admin"] == 1 and h["uso_clientes"] == 0
# la API (rol + permisos) se usó COMO svc-vps (Admin temporal), nunca como root
assert _ejecutado["govc"], "esperaba llamadas de API en modo llave"
assert all(u == "svc-vps" for u, _c in _ejecutado["govc"]), "modo llave: la API va como svc-vps, no root"
# esxcli creó la cuenta, dio Admin temporal y verificó permisos
_exec_join = " ".join(_ejecutado["exec"])
assert "esxcli system account" in _exec_join and "permission set -i svc-vps -r Admin" in _exec_join
assert "permission list" in _exec_join, "debe verificar el auto-rebaje por esxcli"
print("ok - modo llave: enrola sin clave root; API como svc-vps; auto-rebaje verificado")

# auth_modo inválido → 400
r = cli.post("/hosts/preparar", json={"accion": "ejecutar", "id": "esxi-x", "ip": "192.0.2.6",
             "datastore": "d", "huella_confirmada": "SHA256:HUELLA-OK", "auth_modo": "otro",
             "uso_admin": True}, headers=ADMIN)
assert r.status_code == 400 and "auth_modo" in r.get_json()["error"], r.get_json()
print("ok - auth_modo inválido -> 400")

# ── MULTI-DATASTORE: un host con 2 datastores, UNA LLAVE CONFINADA POR CADA UNO ──
r = cli.post("/hosts/preparar", json={"accion": "ejecutar", "id": "esxi-mds", "ip": "192.0.2.30",
             "datastore": "dsPrim", "datastores_extra": ["dsSec"],
             "huella_confirmada": "SHA256:HUELLA-OK", "auth_modo": "llave",
             "uso_clientes": True, "uso_admin": True}, headers=ADMIN)
assert r.status_code == 200, r.get_json()
_jm = r.get_json()["job_id"]
for _ in range(100):
    jm = cli.get("/job/%s" % _jm, headers=ADMIN).get_json()
    if jm["estado"] in ("ok", "error"):
        break
    _t.sleep(0.1)
assert jm["estado"] == "ok", json.dumps(jm["pasos"], ensure_ascii=False)
hm = motor.host_get("esxi-mds")
assert hm["datastore"] == "dsPrim", "el 1º de la lista debe quedar como datastore primario"
dss = motor.host_datastores(hm)
assert [x["ds"] for x in dss] == ["dsPrim", "dsSec"], dss
# llave DISTINTA por datastore; el primario conserva el nombre histórico (compat) y los
# secundarios van en su propio namespace con hash de (hid, ds) — sin colisiones
assert dss[0]["ssh_key"].endswith("vps_engine_esxi_esxi_mds"), dss[0]
assert "vps_engine_dsk_esxi_mds_" in dss[1]["ssh_key"], dss[1]
assert dss[0]["ssh_key"] != dss[1]["ssh_key"], "cada datastore necesita su propia llave"
assert motor.host_ds_key(hm, "dsSec") == dss[1]["ssh_key"]
assert motor.host_ds_key(hm, "ajeno") is None, "un datastore NO registrado no resuelve llave"
assert hm["ssh_key"] == dss[0]["ssh_key"], "hosts.ssh_key debe apuntar al primario"
# árbol + wrapper instalados en AMBOS datastores
_ex = " ".join(_ejecutado["exec"])
assert "/vmfs/volumes/dsPrim/VPS/_bin" in _ex and "/vmfs/volumes/dsSec/VPS/_bin" in _ex
print("ok - multi-datastore: 2 datastores enrolados, una llave confinada por cada uno")

# el PRIMARIO es INMUTABLE si el host ya tiene VMs (re-preparar NO migra máquinas)
with motor.DB_LOCK, motor.db() as c:
    c.execute("INSERT INTO vms(nombre,marca,sabor,cliente,hostname,estado,vcpu,ram_mb,disco_gb,host) "
              "VALUES('vps-mds-1','hosting.cl','vps-estandar','t','x.cl','activo',1,1024,25,'esxi-mds')")
r = cli.post("/hosts/preparar", json={"accion": "ejecutar", "id": "esxi-mds", "ip": "192.0.2.30",
             "datastore": "dsOtro", "huella_confirmada": "SHA256:HUELLA-OK",
             "auth_modo": "llave", "uso_admin": True}, headers=ADMIN)
assert r.status_code == 200, r.get_json()
_ji = r.get_json()["job_id"]
for _ in range(60):
    ji = cli.get("/job/%s" % _ji, headers=ADMIN).get_json()
    if ji["estado"] in ("ok", "error"):
        break
    _t.sleep(0.1)
assert ji["estado"] == "error" and "primario" in ji["error"], ji.get("error")
assert motor.host_get("esxi-mds")["datastore"] == "dsPrim", "el primario no debió cambiar"
with motor.DB_LOCK, motor.db() as c:
    c.execute("DELETE FROM vms WHERE nombre='vps-mds-1'")
print("ok - primario inmutable con VMs presentes (re-preparar no migra máquinas)")

# fallo al reescribir authorized_keys (p.ej. error de lectura): el job DEBE fallar y el host
# NO quedar registrado — el original nunca se reemplaza (la cadena && no llega al mv)
_forzar_fallo["patron"] = "authorized_keys.new."
r = cli.post("/hosts/preparar", json={"accion": "ejecutar", "id": "esxi-akfail", "ip": "192.0.2.33",
             "datastore": "dsF", "huella_confirmada": "SHA256:HUELLA-OK",
             "auth_modo": "llave", "uso_admin": True}, headers=ADMIN)
assert r.status_code == 200, r.get_json()
_jf = r.get_json()["job_id"]
for _ in range(60):
    jf = cli.get("/job/%s" % _jf, headers=ADMIN).get_json()
    if jf["estado"] in ("ok", "error"):
        break
    _t.sleep(0.1)
assert jf["estado"] == "error", json.dumps(jf["pasos"], ensure_ascii=False)
assert motor.host_get("esxi-akfail") is None, "no debe registrarse si falló la instalación de llaves"
_forzar_fallo["patron"] = None
print("ok - fallo al publicar authorized_keys -> job en error y host NO registrado")

# NO colisión de rutas de llave (Codex multids #d): ni entre (hid,ds) distintos, ni con el
# nombre histórico del primario de otro host ("esxi-a-b" vs "esxi-a" + ds "b"), ni por saneo
# de nombres parecidos ("a.b" vs "a-b")
_kp = motor.ds_key_path
assert _kp("esxi-a", "b", primario=False) != _kp("esxi-a-b", "x", primario=True)
assert _kp("esxi-a", "a.b", primario=False) != _kp("esxi-a", "a-b", primario=False)
assert _kp("esxi-a", "ds1", primario=False) != _kp("esxi-b", "ds1", primario=False)
assert _kp("esxi-a", "ds1", primario=False) == _kp("esxi-a", "ds1", primario=False)  # determinista
print("ok - rutas de llave sin colisión entre hosts/datastores (namespace + hash)")

# compat y validación de la configuración (Codex multids #e)
_legacy = {"id": "h1", "datastore": "dsViejo", "ssh_key": "/keys/k1", "datastores": None}
_prim = [{"ds": "dsViejo", "ssh_key": "/keys/k1"}]
assert motor.host_datastores(_legacy) == _prim, "columna NULL → deriva del primario"
# configuración PRESENTE pero inválida: tolerante en lecturas, RUIDOSA al elegir datastore
for roto in ("{json roto",
             json.dumps([{"ds": "a"}]),                                   # sin llave
             json.dumps([{"ds": "x", "ssh_key": "/k"}]),                  # 1º != primario
             json.dumps([{"ds": "dsViejo", "ssh_key": "/keys/k1"}, {"ds": "dsViejo", "ssh_key": "/k2"}])):
    hroto = dict(_legacy, datastores=roto)
    assert motor.host_datastores(hroto) == _prim, roto          # lectura: cae al primario
    _l, _e = motor._parse_datastores(hroto)
    assert _e, "debe reportar el error: %s" % roto
    try:
        motor.host_ds_key(hroto, "dsViejo")
        raise AssertionError("host_ds_key debía fallar con configuración inválida: %s" % roto)
    except RuntimeError as e:
        assert "inválida" in str(e)
# configuración válida: host_ds_key resuelve y rechaza datastores ajenos
_ok = dict(_legacy, datastores=json.dumps([{"ds": "dsViejo", "ssh_key": "/keys/k1"},
                                           {"ds": "dsDos", "ssh_key": "/keys/k2"}]))
assert motor.host_ds_key(_ok, "dsDos") == "/keys/k2"
assert motor.host_ds_key(_ok, "ajeno") is None
print("ok - datastores: NULL deriva primario; config rota = lectura tolerante + elección ruidosa")

# validaciones de la lista de datastores
for mala, esperado in (({"datastores_extra": ["ds bad"]}, "inválido"),
                       ({"datastores_extra": ["dsPrim2"]}, "repetido"),
                       ({"datastores_extra": ["a", "b", "c", "d", "e", "f", "g", "h"]}, "máx 8"),
                       ({"datastores_extra": "noesLista"}, "lista")):
    r = cli.post("/hosts/preparar", json=dict(
        {"accion": "ejecutar", "id": "esxi-mdsx", "ip": "192.0.2.31", "datastore": "dsPrim2",
         "huella_confirmada": "SHA256:HUELLA-OK", "auth_modo": "llave", "uso_admin": True},
        **mala), headers=ADMIN)
    assert r.status_code == 400 and esperado in r.get_json()["error"], (mala, r.get_json())
print("ok - datastores_extra: inválido/repetido/tope/no-lista -> 400")

# ── re-preparación: trampas que rompen un host operativo (Codex wizard #6) ───
def _job_final(resp):
    jid2 = resp.get_json()["job_id"]
    for _ in range(50):
        jj = cli.get("/job/%s" % jid2, headers=ADMIN).get_json()
        if jj["estado"] in ("ok", "error"):
            return jj
        _t.sleep(0.1)
    return jj

# host activo → NO se re-prepara (hay que pausarlo)
with motor.DB_LOCK, motor.db() as c:
    c.execute("UPDATE hosts SET estado='activo' WHERE id='esxi-t9'")
r = cli.post("/hosts/preparar", json={"accion": "ejecutar", "id": "esxi-t9", "ip": "192.0.2.9",
             "datastore": "dsX", "huella_confirmada": "SHA256:HUELLA-OK",
             "root_password": "pw", "uso_clientes": True, "uso_admin": True}, headers=ADMIN)
j = _job_final(r)
assert j["estado"] == "error" and "PAUSAR" in j["error"], j["error"]
with motor.DB_LOCK, motor.db() as c:
    c.execute("UPDATE hosts SET estado='pausado' WHERE id='esxi-t9'")
# variable en el ENTORNO (engine.env) → NO se re-prepara (el almacén no ganaría)
os.environ["GOVC_PASSWORD_ESXI_T9"] = "vieja-de-engine-env"
r = cli.post("/hosts/preparar", json={"accion": "ejecutar", "id": "esxi-t9", "ip": "192.0.2.9",
             "datastore": "dsX", "huella_confirmada": "SHA256:HUELLA-OK",
             "root_password": "pw", "uso_clientes": True, "uso_admin": True}, headers=ADMIN)
j = _job_final(r)
assert j["estado"] == "error" and "engine.env" in j["error"], j["error"]
del os.environ["GOVC_PASSWORD_ESXI_T9"]
# misma IP con OTRO id → NO (rotarían la misma cuenta svc-vps)
r = cli.post("/hosts/preparar", json={"accion": "ejecutar", "id": "esxi-otro", "ip": "192.0.2.9",
             "datastore": "dsX", "huella_confirmada": "SHA256:HUELLA-OK",
             "root_password": "pw", "uso_clientes": True, "uso_admin": True}, headers=ADMIN)
j = _job_final(r)
assert j["estado"] == "error" and "ya pertenece" in j["error"], j["error"]
# re-preparación VÁLIDA (pausado, misma ip, sin env): rota y queda pausado
r = cli.post("/hosts/preparar", json={"accion": "ejecutar", "id": "esxi-t9", "ip": "192.0.2.9",
             "datastore": "dsX", "huella_confirmada": "SHA256:HUELLA-OK",
             "root_password": "pw", "uso_clientes": True, "uso_admin": True}, headers=ADMIN)
j = _job_final(r)
assert j["estado"] == "ok", json.dumps(j["pasos"], ensure_ascii=False)
h = motor.host_get("esxi-t9")
assert h["estado"] == "pausado", "re-preparación debe dejar (mantener) pausado"
print("ok - re-preparación: bloquea host activo / var en engine.env / ip duplicada; rota ok pausado")

# ── copiar-doradas: validaciones ─────────────────────────────────────────────
r = cli.post("/hosts/esxi-t9/copiar-doradas", json={"desde": "no-existe"}, headers=ADMIN)
assert r.status_code == 400 and "origen" in r.get_json()["error"]
r = cli.post("/hosts/no-existe/copiar-doradas", json={"desde": "esxi-t9"}, headers=ADMIN)
assert r.status_code == 404
r = cli.post("/hosts/esxi-t9/copiar-doradas", json={"desde": "esxi-t9"}, headers=ADMIN)
assert r.status_code == 400 and "mismo" in r.get_json()["error"]
r = cli.post("/hosts/esxi-t9/copiar-doradas", json={"desde": "esxi-t9"}, headers=WHMCS)
assert r.status_code == 403
print("ok - copiar-doradas: 404/400/mismo-host/rol")

print("\nTESTS WIZARD (Fase D) OK")
