# Test #12/#13: flujo_purgar con allowlist del registro y boveda-primero.
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

motor.PROVISION_TOKEN = "tok-prov"  # activar la rama de boveda

VIEJA_OK = "20260901-120000-vps-hcl-0001-a"      # registrada, vault ok -> purga
VIEJA_VAULT_MAL = "20260901-120001-vps-hcl-0002-b"  # registrada, vault FALLA -> conservada
JOVEN = "20991231-235959-vps-hcl-0003-c"         # registrada pero joven -> no purga
AJENA = "20260901-120002-vps-hcl-0004-d"         # en _papelera, SIN registro -> deriva
VIEJA_PURGE_MAL = "20260901-120003-vps-hcl-0005-e"  # vault ok, purge FALLA -> vault NULL, fila queda

with motor.DB_LOCK, motor.db() as c:
    for entrada, vault in ((VIEJA_OK, "v1"), (VIEJA_VAULT_MAL, "v2"), (JOVEN, "v3"), (VIEJA_PURGE_MAL, "v5")):
        c.execute("INSERT INTO vms(nombre,estado,papelera_entrada,vault_item,host) VALUES(?,?,?,?,'esxi-245')",
                  (entrada[16:], "papelera", entrada, vault))

papelera = {VIEJA_OK, VIEJA_VAULT_MAL, JOVEN, AJENA, VIEJA_PURGE_MAL}
vault_llamadas, purgadas_esxi = [], []

def fake_esxi_ssh(cmd, timeout=120, host=None):
    if cmd == "list-trash":
        return "\n".join(sorted(papelera)) + "\nOK"
    if cmd.startswith("purge-entry "):
        e = cmd.split(" ", 1)[1]
        if e == VIEJA_PURGE_MAL:
            raise RuntimeError("wrapper 'purge-entry' (exit 1): mv fallo")
        papelera.discard(e)
        purgadas_esxi.append(e)
        return "purged: " + e
    raise AssertionError("cmd inesperado: " + cmd)

def fake_provision_post(path, payload, timeout=60):
    assert path == "/vault-borrar-item"
    vault_llamadas.append(payload["item_id"])
    if payload["item_id"] == "v2":
        raise RuntimeError("boveda caida")
    return {"ok": True}

motor.esxi_ssh = fake_esxi_ssh
motor.provision_post = fake_provision_post

def fila(entrada):
    with motor.DB_LOCK, motor.db() as c:
        r = c.execute("SELECT vault_item FROM vms WHERE papelera_entrada=?", (entrada,)).fetchone()
    return dict(r) if r else None

job = motor.Job("purgar-papelera", "-", "test", ["Purgar"])
motor.flujo_purgar(job)

assert fila(VIEJA_OK) is None and VIEJA_OK not in papelera, "VIEJA_OK debia purgarse completa"
assert "v1" in vault_llamadas
assert fila(VIEJA_VAULT_MAL) == {"vault_item": "v2"} and VIEJA_VAULT_MAL in papelera, "VAULT_MAL debia conservarse intacta"
assert fila(JOVEN) == {"vault_item": "v3"} and JOVEN in papelera, "JOVEN no debia tocarse"
assert AJENA in papelera and fila(AJENA) is None, "AJENA no debia tocarse"
assert fila(VIEJA_PURGE_MAL) == {"vault_item": None}, "PURGE_MAL: vault debia quedar NULL (llave ya borrada)"
assert VIEJA_PURGE_MAL in papelera, "PURGE_MAL: la entrada debia conservarse"
print("ok - corrida 1: purga selectiva, boveda-primero, deriva intacta, joven intacta")

# corrida 2: PURGE_MAL ya sin vault pendiente y el wrapper ahora funciona
def fake_esxi_ssh2(cmd, timeout=120, host=None):
    if cmd == "list-trash":
        return "\n".join(sorted(papelera)) + "\nOK"
    if cmd.startswith("purge-entry "):
        e = cmd.split(" ", 1)[1]
        papelera.discard(e)
        purgadas_esxi.append(e)
        return "purged: " + e
    raise AssertionError(cmd)
motor.esxi_ssh = fake_esxi_ssh2
antes = list(vault_llamadas)
job2 = motor.Job("purgar-papelera", "-", "test", ["Purgar"])
motor.flujo_purgar(job2)
assert fila(VIEJA_PURGE_MAL) is None and VIEJA_PURGE_MAL not in papelera, "PURGE_MAL debia purgarse en corrida 2"
assert "v5" not in vault_llamadas[len(antes):], "NO debia reintentar la llave v5 (ya borrada y anulada)"
assert VIEJA_VAULT_MAL in papelera and fila(VIEJA_VAULT_MAL)["vault_item"] == "v2", "VAULT_MAL sigue esperando boveda sana"
print("ok - corrida 2: la fallida se purga sin re-tocar boveda; la de boveda caida sigue esperando")

# corrida 3: ventanas de interrupcion + la exigencia final de Codex (marca propia)
HUERFANA = "20260901-120004-vps-hcl-0006-f"     # MARCA 'purgando', ausente -> limpieza propia
RESTAURADA = "20260901-120005-vps-hcl-0007-g"   # papelera, ausente PERO vm en VPS/ -> aviso
V404 = "20260901-120006-vps-hcl-0008-h"         # vault ya borrada por corrida caida (404)
DESAPARECIDA = "20260901-120009-vps-hcl-0011-x" # papelera, ausente SIN marca -> conservar+alerta
SINTOKEN = "20260901-120007-vps-hcl-0009-i"     # con vault pero SIN provision token
with motor.DB_LOCK, motor.db() as c:
    for entrada, vault, est in ((HUERFANA, "v6", "purgando"), (RESTAURADA, "v7", "papelera"),
                                (V404, "v404", "papelera"), (DESAPARECIDA, "v11", "papelera")):
        c.execute("INSERT INTO vms(nombre,estado,papelera_entrada,vault_item,host) VALUES(?,?,?,?,'esxi-245')",
                  (entrada[16:], est, entrada, vault))
papelera.add(V404)

def fake_esxi_ssh3(cmd, timeout=120, host=None):
    if cmd == "list-trash":
        return "\n".join(sorted(papelera)) + "\nOK"
    if cmd == "list-vps":
        return RESTAURADA[16:] + "\nOK"   # la 'restaurada' reaparecio en VPS/
    if cmd.startswith("purge-entry "):
        e = cmd.split(" ", 1)[1]
        papelera.discard(e)
        return "purged: " + e
    raise AssertionError(cmd)

def fake_provision_post3(path, payload, timeout=60):
    vault_llamadas.append(payload["item_id"])
    if payload["item_id"] == "v404":
        raise RuntimeError("bóveda/Send falló: HTTP 404 item no existe")
    return {"ok": True}

motor.esxi_ssh = fake_esxi_ssh3
motor.provision_post = fake_provision_post3
job3 = motor.Job("purgar-papelera", "-", "test", ["Purgar"])
motor.flujo_purgar(job3)

assert fila(HUERFANA) is None, "HUERFANA(purgando): fila debia limpiarse (purga propia confirmada)"
assert "v6" in vault_llamadas, "HUERFANA: su llave debia borrarse antes de limpiar la fila"
assert fila(RESTAURADA) is not None, "RESTAURADA: NO debia tocarse (vm reaparecio en VPS/)"
assert fila(V404) is None and V404 not in papelera, "V404: el 404 de boveda debia tolerarse y purgarse"
assert fila(DESAPARECIDA) is not None and fila(DESAPARECIDA)["vault_item"] == "v11", \
    "DESAPARECIDA sin marca: debia CONSERVARSE completa (solo alerta)"
# corrida 4: entrada con vault pero PROVISION_TOKEN ausente -> conservada
with motor.DB_LOCK, motor.db() as c:
    c.execute("INSERT INTO vms(nombre,estado,papelera_entrada,vault_item,host) VALUES(?,?,?,?,'esxi-245')",
              (SINTOKEN[16:], "papelera", SINTOKEN, "v9"))
papelera.add(SINTOKEN)
motor.PROVISION_TOKEN = ""
def fake_esxi_ssh4(cmd, timeout=120, host=None):
    if cmd == "list-trash":
        return SINTOKEN + "\nOK"
    if cmd == "list-vps":
        return "OK"
    raise AssertionError("con vault sin token NO debia llegar a purge-entry: " + cmd)
motor.esxi_ssh = fake_esxi_ssh4
job4 = motor.Job("purgar-papelera", "-", "test", ["Purgar"])
motor.flujo_purgar(job4)
assert fila(SINTOKEN) is not None and fila(SINTOKEN)["vault_item"] == "v9", "SINTOKEN: conservada intacta"
print("ok - corrida 3/4: huerfana limpiada, restaurada intacta, 404 tolerado, sin-token conservada")

# corrida 5: caminos catastroficos que objeto Codex — el listado NO confiable ABORTA todo
motor.PROVISION_TOKEN = "tok-prov"
SOBREVIVE = "20260901-120008-vps-hcl-0010-j"
with motor.DB_LOCK, motor.db() as c:
    c.execute("INSERT INTO vms(nombre,estado,papelera_entrada,vault_item,host) VALUES(?,?,?,?,'esxi-245')",
              (SOBREVIVE[16:], "papelera", SOBREVIVE, "v10"))

def esxi_caido(cmd, timeout=120, host=None):
    raise RuntimeError("wrapper 'list-trash' (exit 1): no pude listar _papelera: I/O error")
motor.esxi_ssh = esxi_caido
motor.flujo_purgar(motor.Job("purgar-papelera", "-", "test", ["Purgar"]))  # A2: alerta y sigue
assert fila(SOBREVIVE) is not None, "host caido NO debe borrar filas"
print("ok - host caido -> alerta + cero borrado logico (A2: no aborta el job)")

def esxi_sin_sentinel(cmd, timeout=120, host=None):
    return ""  # respuesta vacia sin OK = no confiable
motor.esxi_ssh = esxi_sin_sentinel
motor.flujo_purgar(motor.Job("purgar-papelera", "-", "test", ["Purgar"]))  # A2: no confiable = caido
assert fila(SOBREVIVE) is not None
print("ok - sin sentinel -> tratado como host caido, cero borrado logico")

# corrida 6: ausencias masivas SIN marca -> TODAS conservadas (solo alerta); con marca -> convergen
motor.PROVISION_TOKEN = "tok-prov"
with motor.DB_LOCK, motor.db() as c:
    for i in range(4):
        ent = "20260901-12001%d-vps-hcl-002%d-k" % (i, i)
        c.execute("INSERT INTO vms(nombre,estado,papelera_entrada,vault_item,host) VALUES(?,?,?,?,'esxi-245')",
                  (ent[16:], "papelera", ent, None))
    c.execute("INSERT INTO vms(nombre,estado,papelera_entrada,vault_item,host) VALUES(?,?,?,?,'esxi-245')",
              ("vps-hcl-0030-m", "purgando", "20260901-120020-vps-hcl-0030-m", None))
def esxi_vacio_ok(cmd, timeout=120, host=None):
    if cmd in ("list-trash", "list-vps"):
        return "OK"  # papelera legitimamente vacia (sentinel presente)
    raise AssertionError("no debia purgar nada: " + cmd)
motor.esxi_ssh = esxi_vacio_ok
motor.flujo_purgar(motor.Job("purgar-papelera", "-", "test", ["Purgar"]))
with motor.DB_LOCK, motor.db() as c:
    sin_marca = c.execute("SELECT COUNT(*) c FROM vms WHERE estado='papelera' AND papelera_entrada LIKE '%-k'").fetchone()["c"]
    con_marca = c.execute("SELECT COUNT(*) c FROM vms WHERE papelera_entrada LIKE '%-m'").fetchone()["c"]
assert sin_marca == 4, "las 4 sin marca debian conservarse (quedan %d)" % sin_marca
assert con_marca == 0, "la fila con marca 'purgando' debia converger (limpiarse)"
print("ok - ausencias masivas sin marca conservadas + alerta; con marca propia converge")

print("\nTESTS DE PURGA OK")
