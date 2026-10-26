# Tests de la selección de IP PÚBLICA (2026-10-26): excepción Red106-0 → solo 201.148.106.0/25 (Alcadio) y
# exclusión de las IP de interfaz de RouterData en todas las listas.
import os
import sys
import tempfile
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
os.environ.pop("MODO", None)
sys.path.insert(0, str(RAIZ / "engine"))
import app as motor  # noqa: E402

assert motor.DB_PATH == os.environ["DB_PATH"], "esta suite debe correr en su propio proceso"
cli = motor.app.test_client()
ADMIN = {"X-Auth-Token": "tok-admin-test"}

# ── configuración de la excepción ───────────────────────────────────────────
assert motor.PUB_SUBRED == {"Red106-0": "201.148.106.0/25"}
assert motor._hosts_subred("Red106-0") == (1, 126) and motor._hosts_subred("Red107-0") is None
h = cli.get("/health", headers=ADMIN).get_json()
rp = {x["lista"]: x for x in h["redes_publicas"]}
assert rp["Red106-0"]["subred"] == "201.148.106.0/25" and rp["Red107-0"]["subred"] is None
print("ok - Red106-0 restringida a 201.148.106.0/25 (hosts .1-.126); expuesto en /health")

# ── router simulado ─────────────────────────────────────────────────────────
ESTADO = {}
def router(lista_red, libres, nat="", arp="", interfaces="", ping_responde=(), if_falla=False):
    """libres: octetos habilitados y sin comentario en la address-list."""
    ESTADO.clear()
    ESTADO.update(lista_red=lista_red, libres=libres, nat=nat, arp=arp, interfaces=interfaces,
                  ping=set(ping_responde), if_falla=if_falla, pings=[])
def mk(cmd, host=None, timeout=25):
    lista, red = ESTADO["lista_red"]
    if cmd.startswith("/ip firewall address-list print terse where list="):
        return True, "".join(" %d   list=%s address=%s.%d\n" % (i, lista, red, o) for i, o in enumerate(ESTADO["libres"]))
    if cmd.startswith("/ip firewall address-list print terse where address="):
        ip = cmd.split("=", 1)[1]
        return True, " 0   list=%s address=%s\n" % (lista, ip)
    if cmd == "/ip firewall nat print terse":
        return True, ESTADO["nat"]
    if cmd == "/ip arp print terse":
        return True, ESTADO["arp"]
    if cmd.startswith("/ip address print terse"):
        if ESTADO["if_falla"]:
            raise OSError("timeout")
        assert ('address~"^%s."' % red) in cmd, cmd
        return True, ESTADO["interfaces"]
    if cmd.startswith("/ping "):
        ip = cmd.split()[1]
        ESTADO["pings"].append(ip)
        return True, "sent=3 received=%d" % (3 if ip in ESTADO["ping"] else 0)
    raise AssertionError("comando inesperado: %s" % cmd)

class JobFalso:
    def __init__(self):
        self.d = []
    def detalle(self, t):
        self.d.append(t)

_mk = motor.mikrotik
try:
    motor.mikrotik = mk
    IF106 = " 10 comment=LAN HOSTING PUBLICA address=201.148.106.1/25 network=201.148.106.0 interface=sfp28-2\n"
    # .254 (enlace /30 del CCR de borde) y .200 están en la lista pero FUERA de la /25 → nunca se ofrecen
    router(("Red106-0", "201.148.106"), [254, 232, 200, 127, 126, 125, 2, 1], interfaces=IF106)
    ip, rech = motor.ip_publica_libre("Red106-0")
    assert ip == "201.148.106.126" and ESTADO["pings"] == ["201.148.106.126"], (ip, ESTADO["pings"])
    # .126 con NAT → .125
    router(("Red106-0", "201.148.106"), [254, 126, 125, 1], interfaces=IF106,
           nat=" 0 chain=srcnat action=src-nat to-addresses=201.148.106.126 src-address=10.100.36.9\n")
    assert motor.ip_publica_libre("Red106-0")[0] == "201.148.106.125"
    # el gateway .1 (dirección de interfaz) no es candidata aunque esté libre en la lista y no responda ping
    router(("Red106-0", "201.148.106"), [254, 1], interfaces=IF106)
    try:
        motor.ip_publica_libre("Red106-0")
        raise AssertionError("debía quedarse sin candidatas")
    except RuntimeError as e:
        assert "sin IP pública libre en Red106-0 (solo 201.148.106.0/25)" in str(e) and ESTADO["pings"] == [], str(e)
    # .127 (broadcast de la /25) y .0 nunca
    router(("Red106-0", "201.148.106"), [127, 0], interfaces="")
    try:
        motor.ip_publica_libre("Red106-0")
        raise AssertionError("broadcast/red no son candidatas")
    except RuntimeError as e:
        assert "ninguna candidata" in str(e)
    print("ok - Red106-0: solo .1-.126, la más alta libre; .254/.200/.127/.0 y el gateway .1 nunca se ofrecen")

    # otras listas: sin restricción de subred, pero tampoco ofrecen IPs de interfaz de RouterData
    router(("Red107-0", "201.148.107"), [254, 253], interfaces=" 3 address=201.148.107.254/24 network=201.148.107.0\n")
    j = JobFalso()
    assert motor.ip_publica_libre("Red107-0", job=j)[0] == "201.148.107.253"
    router(("Red57-0", "38.19.57"), [102, 101, 100], interfaces="")
    assert motor.ip_publica_libre("Red57-0", rango=[100, 102])[0] == "38.19.57.102", "rango de prueba intacto"
    # si no se pueden leer las interfaces: aviso y sigue (queda el ping)
    router(("Red107-0", "201.148.107"), [254], if_falla=True)
    j = JobFalso()
    assert motor.ip_publica_libre("Red107-0", job=j)[0] == "201.148.107.254"
    assert any("no pude leer las direcciones de interfaz" in x for x in j.d), j.d
    # Codex: 'network=' como único motivo de exclusión, retorno ok=False y TimeoutExpired
    router(("Red107-0", "201.148.107"), [200, 199], interfaces=" 5 address=10.0.0.1/24 network=201.148.107.200\n")
    assert motor.ip_publica_libre("Red107-0")[0] == "201.148.107.199", "la dirección de red de una subred configurada se excluye"
    import subprocess as _sp
    for falla in ("ok_false", "timeout"):
        router(("Red107-0", "201.148.107"), [254])
        def mk2(cmd, host=None, timeout=25, _f=falla):
            if cmd.startswith("/ip address print terse"):
                if _f == "timeout":
                    raise _sp.TimeoutExpired(cmd, timeout)
                return False, ""
            return mk(cmd, host, timeout)
        motor.mikrotik = mk2
        j = JobFalso()
        assert motor.ip_publica_libre("Red107-0", job=j)[0] == "201.148.107.254", falla
        assert any("no pude leer las direcciones de interfaz" in x for x in j.d), (falla, j.d)
        motor.mikrotik = mk
    print("ok - otras listas: sin restricción de subred, sin IPs de interfaz del router; best effort si la consulta falla (ok=False o timeout)")
finally:
    motor.mikrotik = _mk
print("TODO OK - test_red_publica")
