# Test #9: pinning de host keys — cliente pinned fail-closed, mikrotik con opciones
# estrictas, y conexiones a VPS nuevos SIN el pinning (TOFU deliberado, IPs reusadas).
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
import paramiko  # noqa: E402

TMP = tempfile.mkdtemp()

# 1) sin known_hosts -> fail-closed con mensaje claro
motor.KNOWN_HOSTS_PATH = os.path.join(TMP, "no-existe")
try:
    motor.cliente_ssh_pinned()
    raise SystemExit("FALLO: sin known_hosts debia lanzar")
except RuntimeError as e:
    assert "pinning" in str(e) and "ssh-keyscan" in str(e)
print("ok - sin known_hosts -> RuntimeError fail-closed")

# 2) con known_hosts real -> cliente con huella cargada y RejectPolicy
kf = os.path.join(TMP, "hostkey")
subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", "", "-q", "-f", kf], check=True)
pub = open(kf + ".pub").read().split()
kh = os.path.join(TMP, "known_hosts")
with open(kh, "w", newline="\n") as f:
    f.write("10.100.37.245 %s %s\n" % (pub[0], pub[1]))
    f.write("[172.16.1.90]:2420 %s %s\n" % (pub[0], pub[1]))
motor.KNOWN_HOSTS_PATH = kh
cli = motor.cliente_ssh_pinned()
assert isinstance(cli._policy, paramiko.RejectPolicy), "la policy debe ser RejectPolicy"
assert cli.get_host_keys().lookup("10.100.37.245"), "huella del ESXi no cargada"
assert cli.get_host_keys().lookup("[172.16.1.90]:2420"), "huella de RouterData (puerto no estandar) no cargada"
print("ok - cliente pinned: RejectPolicy + huellas cargadas (incl. formato [host]:puerto)")

# 3) mikrotik() usa UserKnownHostsFile + StrictHostKeyChecking=yes
capturado = {}
def fake_run(args, **kw):
    capturado["args"] = args
    class R: returncode, stdout = 0, "x"
    return R()
real_run = motor.subprocess.run
motor.subprocess.run = fake_run
ok, out = motor.mikrotik("/ip firewall nat print count-only")
motor.subprocess.run = real_run
a = capturado["args"]
assert "UserKnownHostsFile=%s" % kh in a, "falta UserKnownHostsFile: %s" % a
assert "StrictHostKeyChecking=yes" in a, "falta StrictHostKeyChecking=yes: %s" % a
assert "StrictHostKeyChecking=no" not in a
print("ok - mikrotik(): opciones estrictas con el known_hosts pinned")

# 4) las conexiones a VPS NUEVOS siguen con AutoAdd y SIN cargar el known_hosts
#    (TOFU deliberado: la llave nace con la VM y las IPs se reutilizan)
import inspect
src = inspect.getsource(motor.instalar_pubkey_en_vm) + inspect.getsource(motor.set_root_password)
assert "AutoAddPolicy" in src and "cliente_ssh_pinned" not in src, \
    "las conexiones a VPS no deben usar el pinning (IPs reusadas chocarian)"
print("ok - VPS nuevos: TOFU deliberado intacto (AutoAdd, sin known_hosts)")

print("\nTESTS DE PINNING OK")

# ── ronda 2 Codex #9: mikrotik aísla confianza (-F /dev/null + GlobalKnownHostsFile) y conserva stderr
import app as _mk
cap = {}
def fake_run_mk(args, capture_output=None, text=None, timeout=None, env=None):
    cap["args"] = args
    class R: returncode = 0; stdout = "ok-salida"; stderr = ""
    return R()
_real = _mk.subprocess.run
_mk.subprocess.run = fake_run_mk
ok, out = _mk.mikrotik("/ip firewall nat print count-only")
a = cap["args"]
assert "-F" in a and a[a.index("-F")+1] == "/dev/null", "falta -F /dev/null"
assert "GlobalKnownHostsFile=/dev/null" in a, "falta GlobalKnownHostsFile=/dev/null"
assert "StrictHostKeyChecking=yes" in a and ("UserKnownHostsFile=%s" % _mk.KNOWN_HOSTS_PATH) in a
assert ok and out == "ok-salida"
# ante fallo: se conserva stderr (rechazo de host key)
def fake_run_mk_err(args, capture_output=None, text=None, timeout=None, env=None):
    class R: returncode = 255; stdout = ""; stderr = "Host key verification failed."
    return R()
_mk.subprocess.run = fake_run_mk_err
ok2, out2 = _mk.mikrotik("/ip firewall nat print")
assert not ok2 and "Host key verification failed" in out2, "el stderr del rechazo debia conservarse: %r" % out2
_mk.subprocess.run = _real
print("ok - #9 ronda2: mikrotik con -F/GlobalKnownHostsFile /dev/null + stderr conservado en fallo")

print("\nTESTS DE PINNING (rondas 1+2) OK")
