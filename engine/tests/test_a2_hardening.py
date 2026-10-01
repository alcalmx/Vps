# Tests de los 6 hallazgos de Codex sobre multi-host A2 (aislamiento por host + validación).
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
os.environ["GP_B"] = "clave-b"
sys.path.insert(0, "c:/Users/alcalmx/Desktop/Proyectos/Vps/engine")
import app as motor  # noqa: E402

cli = motor.app.test_client()
ADMIN = {"X-Auth-Token": "tok-admin-test"}

# dos hosts en el registro
with motor.DB_LOCK, motor.db() as c:
    c.execute("INSERT INTO hosts(id,ip,api_url,govc_user,pass_env,datastore,ssh_key,estado,prioridad) "
              "VALUES('esxi-b','10.0.0.2','https://10.0.0.2/sdk','svc-vps','GP_B','DsB','/k/b','activo',200)")

# ── Hallazgo #3: host_de_vm FALLA explícito, no cae al principal ──────────────
with motor.DB_LOCK, motor.db() as c:
    c.execute("INSERT INTO vms(nombre,estado,host) VALUES('vps-hcl-0001-sinhost','activo',NULL)")
    c.execute("INSERT INTO vms(nombre,estado,host) VALUES('vps-hcl-0002-badhost','activo','esxi-noexiste')")
for vm, motivo in (("vps-hcl-0001-sinhost", "sin host"), ("vps-hcl-0002-badhost", "host inexistente")):
    try:
        motor.host_de_vm(vm)
        raise SystemExit("FALLO: host_de_vm debia lanzar para %s" % motivo)
    except RuntimeError as e:
        assert "revisar a mano" in str(e)
print("ok - #3: host_de_vm falla explicito (sin host / host inexistente), no cae al principal")

# ── Hallazgo #1: purga NO toca una entrada física en host distinto al registrado ─
ENTRADA = "20200101-000000-vps-hcl-0009-dup"   # vieja (>7 días)
with motor.DB_LOCK, motor.db() as c:
    # registrada en esxi-245 pero físicamente aparecerá en esxi-b
    c.execute("INSERT INTO vms(nombre,estado,papelera_entrada,vault_item,host) "
              "VALUES('vps-hcl-0009-dup','papelera',?, 'vlt', 'esxi-245')", (ENTRADA,))
purgas_esxi = []
def fake_esxi(cmd, timeout=120, host=None):
    hid = (host or {}).get("id")
    if cmd == "list-trash":
        # la entrada aparece SOLO en esxi-b (host equivocado); esxi-245 vacío
        return (ENTRADA + "\nOK") if hid == "esxi-b" else "OK"
    if cmd == "list-vps":
        return "OK"
    if cmd.startswith("purge-entry"):
        purgas_esxi.append((hid, cmd))
        return "purged"
    raise AssertionError(cmd)
def fake_vault(path, payload, timeout=60):
    purgas_esxi.append(("VAULT", payload))
    return {"ok": True}
motor.esxi_ssh = fake_esxi
motor.provision_post = fake_vault
motor.PROVISION_TOKEN = "tok"
motor.flujo_purgar(motor.Job("purgar-papelera", "-", "test", ["p"]))
with motor.DB_LOCK, motor.db() as c:
    sigue = c.execute("SELECT vault_item FROM vms WHERE papelera_entrada=?", (ENTRADA,)).fetchone()
assert sigue is not None and sigue["vault_item"] == "vlt", "la fila NO debia tocarse (entrada en host equivocado)"
assert not purgas_esxi, "NO debia purgar nada ni tocar boveda: %s" % purgas_esxi
with motor.DB_LOCK, motor.db() as c:
    avisos = "\n".join(r["detalle"] for r in c.execute("SELECT detalle FROM operaciones WHERE accion='purgar-papelera'"))
assert "host equivocado" in avisos or "host distinto" in avisos, "debia alertar deriva por host"
print("ok - #1: entrada física en host != registrado -> deriva, NO se purga ni se toca la boveda")

# limpieza de esa fila para no ensuciar los siguientes
with motor.DB_LOCK, motor.db() as c:
    c.execute("DELETE FROM vms WHERE papelera_entrada=?", (ENTRADA,))

# ── Hallazgo #4/#6: POST /hosts deriva api_url de ip; ip 999 rechazada ─────────
r = cli.post("/hosts", json={"id": "esxi-x", "ip": "999.1.1.1", "pass_env": "GP_B",
                             "datastore": "D", "ssh_key": "/k"}, headers=ADMIN)
assert r.status_code == 400 and "ip inválida" in r.get_json()["error"], r.get_json()
print("ok - #6: ip 999.1.1.1 rechazada (validación real, no sintáctica)")

# api_url malicioso se IGNORA (se deriva de ip): validamos que govc reciba la ip, no el api_url
visto = {}
def fake_govc(*a, timeout=120, host=None):
    visto["api_url"] = (host or {}).get("api_url")
    return "{}"
motor.govc = fake_govc
motor.esxi_ssh = lambda cmd, timeout=300, host=None: "pong"
motor.datastore_libre_gb = lambda host=None: 500
r = cli.post("/hosts", json={"id": "esxi-c", "ip": "10.0.0.3", "api_url": "https://evil.attacker/sdk",
                             "pass_env": "GP_B", "datastore": "DsC", "ssh_key": "/k/c"}, headers=ADMIN)
assert r.status_code == 200, r.get_json()
assert visto["api_url"] == "https://10.0.0.3/sdk", "api_url debia derivarse de ip, no del body: %s" % visto
with motor.DB_LOCK, motor.db() as c:
    au = c.execute("SELECT api_url FROM hosts WHERE id='esxi-c'").fetchone()["api_url"]
assert au == "https://10.0.0.3/sdk", "api_url guardado debia ser el derivado: %s" % au
print("ok - #4: api_url se deriva de la ip (SSH y API no pueden apuntar a hosts distintos)")

# ── Hallazgo #5: prioridad 0 se respeta; límites inválidos -> 400 ─────────────
r = cli.post("/hosts", json={"id": "esxi-d", "ip": "10.0.0.4", "pass_env": "GP_B",
                             "datastore": "DsD", "ssh_key": "/k/d", "prioridad": 0}, headers=ADMIN)
assert r.status_code == 200, r.get_json()
with motor.DB_LOCK, motor.db() as c:
    prio = c.execute("SELECT prioridad FROM hosts WHERE id='esxi-d'").fetchone()["prioridad"]
assert prio == 0, "prioridad 0 debia respetarse (no convertirse en 100), quedó %s" % prio
print("ok - #5: prioridad 0 se respeta (era el bug del `or 100`)")

r = cli.post("/hosts", json={"id": "esxi-e", "ip": "10.0.0.5", "pass_env": "GP_B",
                             "datastore": "DsE", "ssh_key": "/k/e", "max_disco_gb": -5}, headers=ADMIN)
assert r.status_code == 400 and "max_disco_gb" in r.get_json()["error"], r.get_json()
r = cli.post("/hosts", json={"id": "esxi-f", "ip": "10.0.0.6", "pass_env": "GP_B",
                             "datastore": "DsF", "ssh_key": "/k/f", "ssh_port": 99999}, headers=ADMIN)
assert r.status_code == 400 and "ssh_port" in r.get_json()["error"], r.get_json()
r = cli.patch("/hosts/esxi-b", json={"max_vcpu": "abc"}, headers=ADMIN)
assert r.status_code == 400 and "max_vcpu" in r.get_json()["error"], r.get_json()
r = cli.patch("/hosts/esxi-b", json={"prioridad": True}, headers=ADMIN)   # bool no es entero
assert r.status_code == 400, "bool debia rechazarse como prioridad"
print("ok - #5: límites negativos/no-enteros/puerto fuera de rango -> 400 (POST y PATCH)")

# ── Ronda 2 Codex: reconciliación 'purgando' NO borra si la copia vive en otro host ─
ENT2 = "20200101-000000-vps-hcl-0020-recon"
with motor.DB_LOCK, motor.db() as c:
    # registrada en esxi-245 estado 'purgando', pero física SOLO en esxi-b
    c.execute("INSERT INTO vms(nombre,estado,papelera_entrada,vault_item,host) "
              "VALUES('vps-hcl-0020-recon','purgando',?, 'vlt2', 'esxi-245')", (ENT2,))
def fake_esxi2(cmd, timeout=120, host=None):
    hid = (host or {}).get("id")
    if cmd == "list-trash":
        return (ENT2 + "\nOK") if hid == "esxi-b" else "OK"   # aparece en B, no en A
    if cmd == "list-vps":
        return "OK"
    if cmd.startswith("purge-entry"):
        raise AssertionError("no debia purgar: " + cmd)
    raise AssertionError(cmd)
vault_calls = []
motor.esxi_ssh = fake_esxi2
motor.provision_post = lambda p, pl, timeout=60: vault_calls.append(pl) or {"ok": True}
motor.flujo_purgar(motor.Job("purgar-papelera", "-", "test", ["p"]))
with motor.DB_LOCK, motor.db() as c:
    sigue = c.execute("SELECT vault_item FROM vms WHERE papelera_entrada=?", (ENT2,)).fetchone()
assert sigue is not None and sigue["vault_item"] == "vlt2", "reconciliación NO debia borrar (copia en otro host)"
assert not vault_calls, "no debia tocar la bóveda: %s" % vault_calls
print("ok - recon: fila 'purgando' ausente de su host pero presente en otro -> conservada")
with motor.DB_LOCK, motor.db() as c:
    c.execute("DELETE FROM vms WHERE papelera_entrada=?", (ENT2,))

# ── PATCH prioridad=null rechazado; límite excesivo rechazado (POST y PATCH) ──
r = cli.patch("/hosts/esxi-b", json={"prioridad": None}, headers=ADMIN)
assert r.status_code == 400 and "prioridad" in r.get_json()["error"], r.get_json()
r = cli.post("/hosts", json={"id": "esxi-g", "ip": "10.0.0.7", "pass_env": "GP_B",
                             "datastore": "DsG", "ssh_key": "/k/g", "max_ram_mb": 9223372036854775808},
             headers=ADMIN)
assert r.status_code == 400 and "max_ram_mb" in r.get_json()["error"], r.get_json()
# y max_* null en PATCH SÍ se permite (quitar el límite)
r = cli.patch("/hosts/esxi-b", json={"max_vcpu": None}, headers=ADMIN)
assert r.status_code == 200, r.get_json()
print("ok - PATCH prioridad=null -> 400; max excesivo -> 400; max_*=null permitido (herencia)")

# ── Ronda 3 Codex: deriva del 1er barrido se conserva aunque el otro host caiga en el 2º ─
ENT3 = "20200101-000000-vps-hcl-0030-flaky"
with motor.DB_LOCK, motor.db() as c:
    c.execute("INSERT INTO vms(nombre,estado,papelera_entrada,vault_item,host) "
              "VALUES('vps-hcl-0030-flaky','purgando',?, 'vlt3', 'esxi-245')", (ENT3,))
_llamadas_b = {"n": 0}
def fake_esxi3(cmd, timeout=120, host=None):
    hid = (host or {}).get("id")
    if cmd == "list-trash":
        if hid == "esxi-b":
            _llamadas_b["n"] += 1
            if _llamadas_b["n"] == 1:
                return ENT3 + "\nOK"          # 1er barrido: B responde (deriva detectada)
            raise RuntimeError("B caído en el 2º barrido")   # 2º barrido: B falla
        return "OK"                            # esxi-245 vacío siempre
    if cmd == "list-vps":
        return "OK"
    if cmd.startswith("purge-entry"):
        raise AssertionError("no debia purgar: " + cmd)
    raise AssertionError(cmd)
vault3 = []
motor.esxi_ssh = fake_esxi3
motor.provision_post = lambda p, pl, timeout=60: vault3.append(pl) or {"ok": True}
motor.flujo_purgar(motor.Job("purgar-papelera", "-", "test", ["p"]))
with motor.DB_LOCK, motor.db() as c:
    sigue = c.execute("SELECT vault_item FROM vms WHERE papelera_entrada=?", (ENT3,)).fetchone()
assert sigue is not None and sigue["vault_item"] == "vlt3", "deriva del 1er barrido debia conservarse pese a caída de B"
assert not vault3, "no debia tocar la bóveda: %s" % vault3
print("ok - recon: deriva del 1er barrido se conserva aunque el otro host caiga en el 2º barrido")
with motor.DB_LOCK, motor.db() as c:
    c.execute("DELETE FROM vms WHERE papelera_entrada=?", (ENT3,))

print("\nTESTS A2 HARDENING OK")
