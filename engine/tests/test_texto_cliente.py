# Tests del TEXTO PARA EL CLIENTE (Alcadio 2026-10-26): el resultado de /crear trae la marca y el plan
# tal como los conoce el cliente, para que el panel del job arme el correo con los datos de acceso.
import inspect
import os
import sys
import tempfile

os.environ.setdefault("ENGINE_TOKEN", "tok-admin-test")
os.environ.setdefault("WHMCS_TOKEN", "tok-whmcs-test")
os.environ.setdefault("MGMT_PUBKEY", "ssh-ed25519 " + "A" * 68 + " t@t")
os.environ.setdefault("MGMT_PRIVKEY_PATH", "/tmp/fake-mgmt-key")
os.environ.setdefault("DB_PATH", os.path.join(tempfile.mkdtemp(), "t.db"))
os.environ.setdefault("CONFIG_DIR", "c:/Users/alcalmx/Desktop/Proyectos/Vps")
os.environ.setdefault("ESXI_HOST", "10.100.37.245")
os.environ.setdefault("GOVC_URL", "https://10.100.37.245/sdk")
os.environ.setdefault("GOVC_USERNAME", "svc-vps")
os.environ.setdefault("GOVC_PASSWORD", "x")
sys.path.insert(0, "c:/Users/alcalmx/Desktop/Proyectos/Vps/engine")
import app as motor  # noqa: E402

# marca con planes propios
d = motor.datos_comerciales("hosting.cl", motor.MARCAS["hosting.cl"], motor.SABORES[("hosting.cl", "vps-estandar")])
assert d == {"marca_nombre": "Hosting.cl", "plan": "VPS Estándar"}, d
# marca que hereda los planes de hosting.cl (sabores_de): el plan conserva su nombre, la firma es de SU marca
d = motor.datos_comerciales("inkahosting.pe", motor.MARCAS["inkahosting.pe"],
                            motor.SABORES[("inkahosting.pe", "vps-estandar")])
assert d == {"marca_nombre": "Inkahosting.pe", "plan": "VPS Estándar"}, d
# plan personalizado: su nombre ya lleva los recursos pedidos
sab_p = {"nombre_web": "Personalizado 2 vCPU / 2 GB RAM / 30 GB", "vcpu": 2}
assert motor.datos_comerciales("planetahosting.cl", motor.MARCAS["planetahosting.cl"], sab_p)["plan"] == sab_p["nombre_web"]
# datos incompletos: no revienta (marca = su slug, plan ausente)
assert motor.datos_comerciales("x.cl", {}, None) == {"marca_nombre": "x.cl", "plan": None}
assert motor.datos_comerciales("x.cl", None, {}) == {"marca_nombre": "x.cl", "plan": None}
# todas las marcas activas tienen nombre visible para la firma
for m, cfg in motor.MARCAS.items():
    assert motor.datos_comerciales(m, cfg, None)["marca_nombre"] == cfg["nombre"], m
# el flujo de creación los agrega al resultado del job (antes de entregar las credenciales)
src = inspect.getsource(motor.flujo_crear)
i = src.index("res.update(datos_comerciales(marca, marca_cfg, sabor))")
assert i < src.index("entregar_root_password(res,") < src.index("job.set_resultado(res)"), "orden del resultado"
print("ok - texto para el cliente: el resultado de /crear trae marca y plan (9 marcas, heredados y personalizado)")

# Codex r1 #2: una clave que vino de fuera (WHMCS) se aplica pero NO se devuelve; el resultado marca que quedó
# puesta para que el texto no le pida al cliente definirla con passwd. Si no se confirmó, no se marca.
# Recorrido completo como en flujo_crear: aplicar_root_password (canal SSH simulado) -> entregar_root_password.
class _JobFalso(object):
    def __init__(self): self.detalles = []
    def detalle(self, t): self.detalles.append(t)

_orig_srp = motor.set_root_password
try:
    # externa aplicada: se marca, el secreto no aparece en ningún valor del resultado
    motor.set_root_password = lambda ip, pw: True
    ok = motor.aplicar_root_password(_JobFalso(), "1.2.3.4", "ClaveWhmcs1!", False)
    r = motor.entregar_root_password({"vm": "v"}, "ClaveWhmcs1!", False, ok)
    assert ok is True and r == {"vm": "v", "root_password_externa": True}, r
    # externa NO confirmada (rechazo o timeout): se avisa como con la autogenerada, sin marca ni secreto (Codex r2 #2)
    for err in ("chpasswd falló (rc=1) — la clave NO quedó aplicada",
                "no se pudo CONFIRMAR la aplicación de la clave (timeout/canal)"):
        motor.set_root_password = lambda ip, pw, e=err: (_ for _ in ()).throw(RuntimeError(e))
        ok = motor.aplicar_root_password(_JobFalso(), "1.2.3.4", "ClaveWhmcs1!", False)
        r = motor.entregar_root_password({}, "ClaveWhmcs1!", False, ok)
        assert ok is False and "root_password_error" in r and "root_password_externa" not in r, r
        assert "ClaveWhmcs1!" not in str(r), r
finally:
    motor.set_root_password = _orig_srp
assert motor.entregar_root_password({}, None, False, True) == {}
r = motor.entregar_root_password({}, "Auto!Gen#123", True, True)
assert r == {"root_password": "Auto!Gen#123"} and "root_password_externa" not in r, r
print("ok - clave externa: aplicada se marca sin devolverla; no confirmada se avisa (el texto no la da por buena)")
