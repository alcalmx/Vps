# Test #6: borrar_nat con MikroTik simulado (mikrotik() monkeypatcheado).
# Verifica orden seguro, verificación estricta y fallas -> RuntimeError sin liberar.
import os
import sys
import tempfile

os.environ.setdefault("ENGINE_TOKEN", "tok-admin-test")
os.environ.setdefault("WHMCS_TOKEN", "tok-whmcs-test")
os.environ.setdefault("MGMT_PUBKEY", "ssh-ed25519 " + "A" * 68 + " t@t")
os.environ.setdefault("DB_PATH", os.path.join(tempfile.mkdtemp(), "t.db"))
os.environ.setdefault("CONFIG_DIR", "c:/Users/alcalmx/Desktop/Proyectos/Vps")
sys.path.insert(0, "c:/Users/alcalmx/Desktop/Proyectos/Vps/engine")
import app as motor  # noqa: E402

PRIV, PUB, LISTA = "10.100.16.247", "38.19.57.102", "Red57-0"
llamadas = []

def fake_mikrotik_factory(fallo_remove=False, fallo_verif=False, residuo=False, fallo_set=False):
    def fake(cmd, host=None, timeout=25):
        llamadas.append(cmd)
        if "nat remove" in cmd:
            return (not fallo_remove), ""
        if "nat print terse where" in cmd:
            if fallo_verif:
                return False, ""
            return True, (" 0  chain=srcnat src-address=%s\n" % PRIV) if residuo else ""
        if "address-list set" in cmd:
            return (not fallo_set), ""
        return True, ""
    return fake

def caso(nombre, debe_fallar, intento_liberar_esperado=False, **kw):
    global llamadas
    llamadas = []
    motor.mikrotik = fake_mikrotik_factory(**kw)
    try:
        r = motor.borrar_nat(PRIV, PUB, LISTA)
        assert not debe_fallar, "%s: debia lanzar y no lanzo" % nombre
        assert r is True
    except RuntimeError as e:
        assert debe_fallar, "%s: lanzo inesperadamente: %s" % (nombre, e)
    libero = any("address-list set" in c and "disabled=no" in c for c in llamadas)
    if debe_fallar and not intento_liberar_esperado:
        assert not libero, "%s: LIBERO la publica pese a la falla!" % nombre
    else:
        assert libero, "%s: no libero la publica en el caso feliz" % nombre
        # orden: la liberacion ocurre DESPUES de las 2 verificaciones
        i_verif = max(i for i, c in enumerate(llamadas) if "print terse where" in c)
        i_lib = next(i for i, c in enumerate(llamadas) if "address-list set" in c)
        assert i_lib > i_verif, "%s: libero ANTES de verificar" % nombre
    print("ok -", nombre)

caso("feliz: remove+verificacion vacia -> libera al final", False)
caso("remove falla -> RuntimeError, publica sin liberar", True, fallo_remove=True)
caso("verificacion no responde -> RuntimeError, sin liberar (antes: exito falso)", True, fallo_verif=True)
caso("queda regla residual -> RuntimeError, sin liberar", True, residuo=True)
caso("liberacion falla -> RuntimeError (NAT ya revertido, se reporta)", True,
     intento_liberar_esperado=True, fallo_set=True)
print("\nTESTS DE borrar_nat OK")
