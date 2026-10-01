# Tests #16 (health por rol), #17 (matriz de estados + sabor activo),
# #19 (script growfs local con binarios simulados), #21 (migraciones estrictas).
import os
import subprocess
import sys
import tempfile

os.environ.setdefault("ENGINE_TOKEN", "tok-admin-test")
os.environ.setdefault("WHMCS_TOKEN", "tok-whmcs-test")
os.environ.setdefault("MGMT_PUBKEY", "ssh-ed25519 " + "A" * 68 + " t@t")
os.environ.setdefault("DB_PATH", os.path.join(tempfile.mkdtemp(), "t.db"))
os.environ.setdefault("CONFIG_DIR", "c:/Users/alcalmx/Desktop/Proyectos/Vps")
sys.path.insert(0, "c:/Users/alcalmx/Desktop/Proyectos/Vps/engine")
import app as motor  # noqa: E402

cli = motor.app.test_client()
ADMIN = {"X-Auth-Token": "tok-admin-test"}
WHMCS = {"X-Auth-Token": "tok-whmcs-test"}

# ── #16: /health por rol ─────────────────────────────────────────────────────
r = cli.get("/health").get_json()
assert r == {"ok": True}, "sin token debe ser minimo: %s" % r
r = cli.get("/health", headers=WHMCS).get_json()
assert r == {"ok": True}, "whmcs debe ser minimo: %s" % r
r = cli.get("/health", headers=ADMIN).get_json()
assert r["ok"] and "modo" in r and "sabores_detalle" in r, "admin debe ver el detalle"
print("ok - #16: /health minimo sin token y para whmcs; detalle solo admin")

# ── #17: matriz de estados ───────────────────────────────────────────────────
with motor.DB_LOCK, motor.db() as c:
    c.execute("INSERT INTO vms(nombre,marca,estado,ip) VALUES('vps-hcl-0050-m','hosting.cl','creando','10.100.16.200')")
    c.execute("INSERT INTO vms(nombre,marca,estado,ip,publica,pub_lista) "
              "VALUES('vps-hcl-0051-s','hosting.cl','suspendido','10.100.16.201','38.19.57.90','Red57-0')")
r = cli.post("/accion", json={"vm": "vps-hcl-0050-m", "accion": "suspender"}, headers=ADMIN)
assert r.status_code == 409 and "'creando'" in r.get_json()["error"], (r.status_code, r.get_json())
r = cli.post("/accion", json={"vm": "vps-hcl-0050-m", "accion": "reanudar"}, headers=ADMIN)
assert r.status_code == 409
r = cli.post("/editar", json={"vm": "vps-hcl-0050-m", "sabor": "vps-estandar"}, headers=ADMIN)
assert r.status_code == 409 and "cambio de plan" in r.get_json()["error"]
r = cli.post("/accion", json={"vm": "vps-hcl-0051-s", "accion": "suspender"}, headers=ADMIN)
assert r.status_code == 409, "suspender sobre suspendido -> 409 (matriz)"
print("ok - #17: creando rechaza suspender/reanudar/editar; suspendido rechaza re-suspender")

# sabor inactivo -> 400 en editar (sobre VM operativa)
motor.SABORES[("hosting.cl", "sabor-muerto")] = {"marca": "hosting.cl", "slug": "sabor-muerto",
                                                 "nombre_web": "X", "vcpu": 1, "ram_mb": 1024,
                                                 "disco_gb": 10, "activo": False}
r = cli.post("/editar", json={"vm": "vps-hcl-0051-s", "sabor": "sabor-muerto"}, headers=ADMIN)
assert r.status_code == 400 and "no está activo" in r.get_json()["error"]
del motor.SABORES[("hosting.cl", "sabor-muerto")]
print("ok - #17: sabor destino inactivo -> 400")

# ── #21: migraciones estrictas (el filtro de 'duplicate column name') ────────
import sqlite3
con = sqlite3.connect(":memory:")
con.execute("CREATE TABLE t(a TEXT)")
try:
    con.execute("ALTER TABLE t ADD COLUMN a TEXT")
except sqlite3.OperationalError as e:
    assert "duplicate column name" in str(e).lower(), "supuesto del filtro roto: %s" % e
try:
    con.execute("ALTER TABLE no_existe ADD COLUMN x TEXT")
    raise SystemExit("FALLO: debia lanzar")
except sqlite3.OperationalError as e:
    assert "duplicate column name" not in str(e).lower()
print("ok - #21: 'duplicate column name' es distinguible de errores reales (supuesto validado)")

# ── #19: script growfs simulado con binarios fake (xfs y fallas) ─────────────
SCRATCH = tempfile.mkdtemp()
BIN = os.path.join(SCRATCH, "bin")
os.makedirs(BIN)
def fake_bin(nombre, contenido):
    p = os.path.join(BIN, nombre)
    with open(p, "w", newline="\n") as f:
        f.write("#!/bin/sh\n" + contenido)
    os.chmod(p, 0o755)

fake_bin("findmnt", 'case "$2" in SOURCE) echo /dev/sda3;; FSTYPE) echo xfs;; esac')
fake_bin("lsblk", "echo sda")
fake_bin("growpart", "exit 1")            # NOCHANGE: tolerado
fake_bin("xfs_growfs", "exit 0")
fake_bin("df", 'echo "  Size"; if [ -f /tmp/.crecio ]; then echo 110000000000; else touch /tmp/.crecio; echo 103000000000; fi')
# extraer el script del codigo fuente real
import inspect, re as _re
src = inspect.getsource(motor.flujo_editar)
m = _re.search(r"script = \(\s*((?:'[^']*'\s*)+)\)", src)
script = "".join(_re.findall(r"'([^']*)'", m.group(1)))
env = dict(os.environ, PATH=BIN + os.pathsep + os.environ["PATH"])
subprocess.run(["bash", "-c", "rm -f /tmp/.crecio"], env=env)
r = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)
assert r.returncode == 0 and r.stdout.strip().startswith("FS_OK"), (r.returncode, r.stdout, r.stderr)
antes, despues = r.stdout.split()[1:3]
assert int(despues) > int(antes), "el tamano debia crecer en la simulacion"
# NUEVO contrato (Codex Medios #19): el script SIEMPRE sale 0; el estado va en la línea
# growpart error real (rc=2) -> FS_ERR (exit 0)
fake_bin("growpart", "exit 2")
r = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)
assert r.returncode == 0 and "FS_ERR growpart" in r.stdout, (r.returncode, r.stdout)
# raiz en LVM -> FS_SKIP (exit 0)
fake_bin("findmnt", 'case "$2" in SOURCE) echo /dev/mapper/vg-root;; FSTYPE) echo xfs;; esac')
r = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)
assert r.returncode == 0 and "FS_SKIP" in r.stdout, (r.returncode, r.stdout)
# disco SIN particion (/dev/sda) -> FS_SKIP, no aborta
fake_bin("findmnt", 'case "$2" in SOURCE) echo /dev/sda;; FSTYPE) echo ext4;; esac')
r = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)
assert r.returncode == 0 and "FS_SKIP disco sin particion" in r.stdout, (r.returncode, r.stdout)
# fstype raro -> FS_SKIP ANTES de tocar growpart
fake_bin("findmnt", 'case "$2" in SOURCE) echo /dev/sda3;; FSTYPE) echo btrfs;; esac')
fake_bin("growpart", "echo TOCADO > /tmp/.growpart-tocado; exit 0")
subprocess.run(["bash", "-c", "rm -f /tmp/.growpart-tocado"], env=env)
r = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)
assert r.returncode == 0 and "FS_SKIP fstype" in r.stdout
import os as _os2
assert not _os2.path.exists("/tmp/.growpart-tocado"), "fstype raro NO debe llegar a growpart"
print("ok - #19: growfs — crece y verifica; FS_ERR/FS_SKIP con exit 0; sin-particion y fstype-raro no tocan nada")

print("\nTESTS DE MEDIOS OK")
