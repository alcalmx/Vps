# Test Multi-host Fase A1: bootstrap, adopcion de VMs, /hosts con scan, credenciales por host.
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
os.environ.setdefault("GOVC_PASSWORD", "clave-principal")
sys.path.insert(0, "c:/Users/alcalmx/Desktop/Proyectos/Vps/engine")
import app as motor  # noqa: E402
GOVC_REAL = motor.govc   # referencia al govc real (antes de los monkeypatches)

# 1) bootstrap: host principal auto-registrado desde las env
hs = motor.hosts_lista()
assert len(hs) == 1 and hs[0]["id"] == "esxi-245" and hs[0]["ip"] == "10.100.37.245", hs
assert hs[0]["pass_env"] == "GOVC_PASSWORD" and hs[0]["datastore"] == "DiscoA37245"
print("ok - bootstrap: esxi-245 auto-registrado desde engine.env")

# 2) adopcion: SOLO en el bootstrap inicial (tabla hosts vacia). Tras el fix Codex A1
#    #1, una VM que queda host=NULL DESPUES NO se adopta por prioridad al reiniciar
#    (host_de_vm la rechaza; la reconciliacion la denuncia).
with motor.DB_LOCK, motor.db() as c:
    c.execute("INSERT INTO vms(nombre,estado,vcpu,ram_mb,disco_gb,host) "
              "VALUES('vps-hcl-0001-x','activo',4,4096,100,NULL)")
motor.init_db()   # ya hay hosts → NO re-adopta
with motor.DB_LOCK, motor.db() as c:
    row = c.execute("SELECT host FROM vms WHERE nombre='vps-hcl-0001-x'").fetchone()
assert row["host"] is None, "una VM NULL tras el bootstrap NO debe re-adoptarse: %s" % row["host"]
try:
    motor.host_de_vm("vps-hcl-0001-x")
    raise SystemExit("FALLO: host_de_vm debia rechazar una VM sin host")
except RuntimeError:
    pass
# limpieza para no ensuciar el resto
with motor.DB_LOCK, motor.db() as c:
    c.execute("DELETE FROM vms WHERE nombre='vps-hcl-0001-x'")
print("ok - adopcion SOLO en bootstrap inicial; VM NULL posterior NO se re-adopta (host_de_vm rechaza)")

# 3) govc con host: inyecta credenciales DEL host (env var nombrada por pass_env)
os.environ["GOVC_PASSWORD_ESXI121"] = "clave-del-121"
with motor.DB_LOCK, motor.db() as c:
    c.execute("INSERT INTO hosts(id, ip, api_url, govc_user, pass_env, datastore, estado, prioridad) "
              "VALUES('esxi-121','192.168.200.121','https://192.168.200.121/sdk','svc-vps',"
              "'GOVC_PASSWORD_ESXI121','DiscoB121','activo',200)")
capturado = {}
def fake_run(args, capture_output=None, text=None, timeout=None, env=None):
    capturado["env"] = env
    class R: returncode, stdout, stderr = 0, "{}", ""
    return R()
real_run = motor.subprocess.run
motor.subprocess.run = fake_run
motor.govc("about", host=motor.host_get("esxi-121"))
motor.subprocess.run = real_run
e = capturado["env"]
assert e["GOVC_URL"] == "https://192.168.200.121/sdk" and e["GOVC_PASSWORD"] == "clave-del-121" \
    and e["GOVC_DATASTORE"] == "DiscoB121", "credenciales del host no inyectadas"
print("ok - govc(host=...): credenciales y datastore DEL host inyectadas al subprocess")

# 4) /hosts: admin ve ambos con recursos (scan simulado); whmcs 403
DS_JSON = json.dumps({"datastores": [{"summary": {"name": "DiscoA37245", "freeSpace": 1100 * 1024**3}}]})
DS_JSON_B = json.dumps({"datastores": [{"summary": {"name": "DiscoB121", "freeSpace": 500 * 1024**3}}]})
HOST_JSON = json.dumps({"hostSystems": [{"summary": {
    "hardware": {"numCpuCores": 32, "memorySize": 128 * 1024**3},
    "quickStats": {"overallMemoryUsage": 64 * 1024}}}]})
def fake_govc(*args, timeout=120, host=None):
    if args[0] == "datastore.info":
        return DS_JSON_B if (host or {}).get("id") == "esxi-121" else DS_JSON
    if args[0] == "host.info":
        return HOST_JSON
    raise AssertionError(args)
motor.govc = fake_govc
# una VM comprometida EN esxi-245 para verificar el "comprometido por host"
with motor.DB_LOCK, motor.db() as c:
    c.execute("INSERT INTO vms(nombre,estado,vcpu,ram_mb,disco_gb,host) "
              "VALUES('vps-hcl-0002-y','activo',4,4096,100,'esxi-245')")
cli = motor.app.test_client()
r = cli.get("/hosts", headers={"X-Auth-Token": "tok-whmcs-test"})
assert r.status_code == 403
r = cli.get("/hosts", headers={"X-Auth-Token": "tok-admin-test"}).get_json()
assert len(r["hosts"]) == 2
h245 = next(h for h in r["hosts"] if h["id"] == "esxi-245")
h121 = next(h for h in r["hosts"] if h["id"] == "esxi-121")
assert h245["recursos"]["alcanzable"] and h245["recursos"]["datastore_libre_gb"] == 1100
assert h245["recursos"]["cpu_cores"] == 32 and h245["recursos"]["mem_total_gb"] == 128
assert h245["recursos"]["mem_uso_gb"] == 64
assert h245["recursos"]["comprometido"]["vms"] == 1 and h245["recursos"]["comprometido"]["disco_gb"] == 100
assert h121["recursos"]["datastore_libre_gb"] == 500 and h121["recursos"]["comprometido"]["vms"] == 0
print("ok - /hosts: 403 whmcs; admin ve 2 hosts con scan vivo + comprometido por host")

# 5) host inalcanzable -> alcanzable False sin romper el endpoint
def govc_caido(*args, timeout=120, host=None):
    raise RuntimeError("govc about: connection refused")
motor.govc = govc_caido
r = cli.get("/hosts", headers={"X-Auth-Token": "tok-admin-test"}).get_json()
assert all(not h["recursos"]["alcanzable"] for h in r["hosts"])
assert "error" in r["hosts"][0]["recursos"]
print("ok - host caido: alcanzable=false con el error, endpoint sigue vivo")

print("\nTESTS MULTI-HOST A1 OK")

# 6) hallazgos Codex A1: govc(host=) exige config completa + secreto; _redact; límite 0
motor.govc = GOVC_REAL   # restaurar el govc real (los tests previos lo monkeypatchearon)
# govc con host incompleto -> RuntimeError (no hereda globales)
try:
    motor.govc("about", host={"id": "roto", "ip": "1.2.3.4"})  # sin api_url/user/datastore/pass_env
    raise SystemExit("FALLO: govc debia exigir config completa")
except RuntimeError as e:
    assert "incompleta" in str(e), e
# govc con pass_env que no existe en el entorno -> RuntimeError claro
try:
    motor.govc("about", host={"id": "roto2", "api_url": "https://1.2.3.4/sdk", "govc_user": "svc-vps",
                              "datastore": "D", "pass_env": "NO_EXISTE_XYZ"})
    raise SystemExit("FALLO: govc debia exigir el secreto")
except RuntimeError as e:
    assert "NO_EXISTE_XYZ" in str(e), e
# _redact quita credenciales embebidas de un texto de error
assert motor._redact("x https://user:secreto@1.2.3.4/sdk y") == "x https://***@1.2.3.4/sdk y"
# límite 0 explícito del host = SIN límite (no hereda el global)
assert motor._lim(0, 24) is None, "límite 0 del host debe ser 'sin límite'"
assert motor._lim(None, 24) == 24, "None hereda el global"
assert motor._lim(None, 0) is None, "None + global 0 = sin límite"
print("ok - A1 hallazgos: govc exige config+secreto, _redact credenciales, límite 0 = sin límite")

print("\nTESTS MULTI-HOST A1 (con hallazgos Codex) OK")

# 7) hallazgos ronda 2 Codex A1: TLS respeta env, host={} rechazado, secreto redactado, comprometido=None
motor.govc = GOVC_REAL
cap2 = {}
def fake_run2(args, capture_output=None, text=None, timeout=None, env=None):
    cap2["env"] = env
    class R: returncode, stdout, stderr = 0, "{}", ""
    return R()
motor.subprocess.run = fake_run2
os.environ["GP_TLS"] = "clave-tls"
os.environ["GOVC_INSECURE"] = "0"   # entorno con verificación TLS activa
motor.govc("about", host={"id": "h", "api_url": "https://1.1.1.1/sdk", "govc_user": "u",
                          "datastore": "D", "pass_env": "GP_TLS"})
assert cap2["env"]["GOVC_INSECURE"] == "0", "TLS: debe respetar GOVC_INSECURE del entorno, no forzar 1"
# secretos ajenos purgados del env del subprocess
assert "ENGINE_TOKEN" not in cap2["env"] and "WHMCS_TOKEN" not in cap2["env"], "secretos del motor no deben filtrarse a govc"
assert "GOVC_PASSWORD_ESXI121" not in cap2["env"], "password de OTRO host no debe ir al subprocess"
os.environ["GOVC_INSECURE"] = "1"   # restaurar
motor.subprocess.run = real_run
# host={} → rechazado (no ejecuta contra el entorno global)
try:
    motor.govc("about", host={})
    raise SystemExit("FALLO: host={} debia rechazarse")
except RuntimeError as e:
    assert "incompleta" in str(e)
# el secreto LITERAL se redacta del error de govc
def fake_run_err(args, capture_output=None, text=None, timeout=None, env=None):
    class R: returncode = 1; stdout = ""; stderr = "auth falló con clave-tls en la URL"
    return R()
motor.subprocess.run = fake_run_err
try:
    motor.govc("about", host={"id": "h", "api_url": "https://1.1.1.1/sdk", "govc_user": "u",
                              "datastore": "D", "pass_env": "GP_TLS"})
    raise SystemExit("debia lanzar")
except RuntimeError as e:
    assert "clave-tls" not in str(e) and "***" in str(e), "el secreto literal debia redactarse: %s" % e
motor.subprocess.run = real_run
print("ok - ronda2: TLS respeta env, secretos ajenos purgados, host={} rechazado, secreto literal redactado")

print("\nTESTS MULTI-HOST A1 (rondas 1+2) OK")

# 8) ronda 3: allowlist real — un pass_env con nombre arbitrario NO se filtra al subprocess
motor.govc = GOVC_REAL
cap3 = {}
def fake_run3(args, capture_output=None, text=None, timeout=None, env=None):
    cap3["env"] = env
    class R: returncode, stdout, stderr = 0, "{}", ""
    return R()
motor.subprocess.run = fake_run3
os.environ["ESXI_B_PASSWORD"] = "secreto-b-arbitrario"
motor.govc("about", host={"id": "hb", "api_url": "https://2.2.2.2/sdk", "govc_user": "u",
                          "datastore": "D", "pass_env": "ESXI_B_PASSWORD"})
env3 = cap3["env"]
assert "ESXI_B_PASSWORD" not in env3, "el pass_env de nombre arbitrario NO debe filtrarse como var propia"
assert env3["GOVC_PASSWORD"] == "secreto-b-arbitrario", "el secreto debe ir SOLO como GOVC_PASSWORD"
# ninguna var que no sea de sistema o GOVC_* debe estar
for k in env3:
    assert k in motor.GOVC_ENV_SISTEMA or k.startswith("GOVC_"), "var inesperada en el env de govc: %s" % k
motor.subprocess.run = real_run
print("ok - ronda3: allowlist real (pass_env arbitrario no se filtra; solo sistema + GOVC_*)")

print("\nTESTS MULTI-HOST A1 (rondas 1+2+3) OK")
