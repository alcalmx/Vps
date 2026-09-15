#!/usr/bin/env python3
"""vps-engine — ciclo de vida de VPS en ESXi (crear / eliminar / suspender / editar).

API interna (solo 127.0.0.1:8224, red de host en noc-monitor). La llama la sección
"VPS" del dashboard NOC. Proyecto: Proyectos/Vps (repo github.com/alcalmx/Vps).

Toda operación corre como JOB asíncRONO con pasos visibles:
  POST /crear {marca, sabor, cliente, hostname, instalar_cpanel} → {job_id}
  GET  /job/<id>            → {estado, pasos:[{nombre, estado, detalle, ts}], ...}
  GET  /jobs                → últimos jobs
  GET  /vms                 → VMs gestionadas (registro + power state en vivo)
  POST /accion {vm, accion} → suspender | reanudar | eliminar (job)
  POST /editar {vm, sabor}  → cambio de plan (job)
  POST /purgar-papelera     → purga >7 días (job)

SEGURIDAD (ver SEGURIDAD.md del repo — 5 capas):
  - solo VMs del registro y con prefijo vps-; el ESXi se toca vía usuario API
    svc-vps (sin root) y vía SSH con wrapper restringido (vps-wrapper.sh).
  - eliminar = papelera (mover), nunca destrucción directa.
"""
import base64
import gzip
import hmac
import ipaddress
import json
import os
import re
import sqlite3
import subprocess
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import paramiko
from flask import Flask, jsonify, request

# ── Configuración ────────────────────────────────────────────────────────────
TOKEN = os.environ["ENGINE_TOKEN"]
# Token dedicado para el conector WHMCS (revocable aparte del dashboard). Opcional.
WHMCS_TOKEN = os.environ.get("WHMCS_TOKEN", "")
if WHMCS_TOKEN and WHMCS_TOKEN == TOKEN:
    raise RuntimeError("WHMCS_TOKEN no puede ser igual a ENGINE_TOKEN (el conector quedaría con rol admin)")
ESXI_HOST = os.environ.get("ESXI_HOST", "10.100.37.245")
ESXI_SSH_PORT = int(os.environ.get("ESXI_SSH_PORT", "22"))
ESXI_SSH_KEY = os.environ.get("ESXI_SSH_KEY", "/keys/vps_engine_esxi")
GOVC = os.environ.get("GOVC_BIN", "/usr/local/bin/govc")
DATASTORE = os.environ.get("GOVC_DATASTORE", "DiscoA37245")
MGMT_PUBKEY = os.environ["MGMT_PUBKEY"].strip()
MGMT_PRIVKEY_PATH = os.environ.get("MGMT_PRIVKEY_PATH", "")  # para verificar ssh + cPanel
# MikroTik (producción): elegir IP privada/pública y crear NAT — mismo acceso que el NOC
MIKROTIK_KEY = os.environ.get("MIKROTIK_KEY", "/keys/mikrotik_key")
MIKROTIK_USER = os.environ.get("MIKROTIK_USER", "claude")
MIKROTIK_PORT = os.environ.get("MIKROTIK_PORT", "2420")
ROUTERDATA = os.environ.get("ROUTERDATA_HOST", "172.16.1.90")   # decide/rutea
CCR_BORDE = os.environ.get("CCR_BORDE_HOST", "172.16.1.69")     # ping de verificación
# address-list de RouterData por red pública (igual que ALTA_PUB_REDES del NOC)
PUB_REDES = {"Red57-0": "38.19.57", "Red104-0": "201.148.104", "Red105-0": "201.148.105",
             "Red106-0": "201.148.106", "Red107-0": "201.148.107"}
# vps-provision (bóveda) para guardar la llave del cliente y crear el Bitwarden Send
PROVISION_URL = os.environ.get("PROVISION_URL", "http://127.0.0.1:8223")
PROVISION_TOKEN = os.environ.get("PROVISION_TOKEN", "")
# NetBox (IPAM vigente = NETBOX2 :8090): registro automático de las IPs de cada VPS
NETBOX_URL = os.environ.get("NETBOX_API_URL", "").rstrip("/")
NETBOX_TOKEN = os.environ.get("NETBOX_API_TOKEN", "")
# WHMCS API (dirección motor → WHMCS): rellenar la IP en la ficha, disparar correos, etc.
# Genérico: cualquier acción de la API de WHMCS. Inerte si no está configurado.
WHMCS_API_URL = os.environ.get("WHMCS_API_URL", "")        # ej. https://panel.hosting.cl/includes/api.php
WHMCS_API_ID = os.environ.get("WHMCS_API_IDENTIFIER", "")
WHMCS_API_SECRET = os.environ.get("WHMCS_API_SECRET", "")
DB_PATH = os.environ.get("DB_PATH", "/data/registry.db")
CONFIG_DIR = os.environ.get("CONFIG_DIR", "/app/config")
MODO = os.environ.get("MODO", "pruebas")  # pruebas | produccion
# Modo FIJO para las creaciones del rol whmcs (#5: el conector no elige modo por
# body — lo fija el motor). Para el go-live real: WHMCS_MODO=produccion en engine.env.
WHMCS_MODO = os.environ.get("WHMCS_MODO", MODO)
# fail-fast de configuración: mejor que el contenedor no arranque a que corra con
# un modo inválido o con los dos tokens iguales (WHMCS quedaría con rol admin)
if MODO not in ("pruebas", "produccion"):
    raise RuntimeError("MODO inválido: %r (pruebas | produccion)" % MODO)
if WHMCS_MODO not in ("pruebas", "produccion"):
    raise RuntimeError("WHMCS_MODO inválido: %r (pruebas | produccion)" % WHMCS_MODO)
DORADA_DEFAULT = os.environ.get("DORADA", "dorada-almalinux9.7")

app = Flask(__name__)

# ── Config de marcas y sabores (del repo, horneadas en la imagen) ────────────
def _load_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)

MARCAS = {}
SABORES = {}
for f in os.listdir(os.path.join(CONFIG_DIR, "marcas")):
    if f.endswith(".json"):
        m = _load_json(os.path.join(CONFIG_DIR, "marcas", f))
        MARCAS[m["marca"]] = m
for marca_dir in os.listdir(os.path.join(CONFIG_DIR, "sabores")):
    d = os.path.join(CONFIG_DIR, "sabores", marca_dir)
    if os.path.isdir(d):
        for f in os.listdir(d):
            if f.endswith(".json"):
                s = _load_json(os.path.join(d, f))
                SABORES[(s["marca"], s["slug"])] = s

# ── Registro (SQLite) ────────────────────────────────────────────────────────
DB_LOCK = threading.Lock()
# Serializan las secciones de ASIGNACIÓN de recursos compartidos entre creaciones
# concurrentes (jobs en threads): la ventana leer→elegir→reservar debe ser atómica
# o dos jobs eligen el mismo recurso (la selección es determinista: siempre el más
# alto libre). Un lock POR RECURSO (son independientes) para no serializar de más:
# p.ej. reservar un nombre no espera el barrido ping de otra creación. DB_LOCK solo
# protege SQLite. Orden de anidamiento: lock de asignación por fuera, DB_LOCK dentro.
NOMBRE_LOCK = threading.Lock()   # elegir número + INSERT de la reserva
IP_PRIV_LOCK = threading.Lock()  # elegir IP privada + grabarla en el registro
IP_PUB_LOCK = threading.Lock()   # elegir IP pública + crear NAT + grabarla
JOB_GATE_LOCK = threading.Lock() # gate "un solo job activo por VM" (ver lanzar_job_exclusivo)

def db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with DB_LOCK, db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS vms(
          nombre TEXT PRIMARY KEY, marca TEXT, sabor TEXT, cliente TEXT,
          hostname TEXT, ip TEXT, estado TEXT,          -- creando|activo|suspendido|eliminando|papelera|error
          vcpu INTEGER, ram_mb INTEGER, disco_gb INTEGER,
          papelera_entrada TEXT,
          created_at TEXT DEFAULT (datetime('now','localtime')),
          updated_at TEXT DEFAULT (datetime('now','localtime')));
        CREATE TABLE IF NOT EXISTS jobs(
          id TEXT PRIMARY KEY, tipo TEXT, vm TEXT, estado TEXT,  -- corriendo|ok|error
          pasos TEXT, error TEXT, actor TEXT,
          created_at TEXT DEFAULT (datetime('now','localtime')),
          updated_at TEXT DEFAULT (datetime('now','localtime')));
        CREATE TABLE IF NOT EXISTS operaciones(
          id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT DEFAULT (datetime('now','localtime')),
          actor TEXT, accion TEXT, vm TEXT, detalle TEXT, resultado TEXT);
        """)
        # columnas de producción (IP pública/NAT + bóveda) — idempotente
        for col, decl in (("publica", "TEXT"), ("pub_lista", "TEXT"),
                          ("vault_item", "TEXT"), ("send_url", "TEXT"), ("send_id", "TEXT"),
                          ("byo_pubkey_fp", "TEXT"), ("nb_priv_id", "TEXT"), ("nb_pub_id", "TEXT"),
                          ("whmcs_serviceid", "TEXT")):
            try:
                c.execute("ALTER TABLE vms ADD COLUMN %s %s" % (col, decl))
            except sqlite3.OperationalError:
                pass  # ya existe
        try:
            c.execute("ALTER TABLE jobs ADD COLUMN resultado TEXT")  # datos de acceso
        except sqlite3.OperationalError:
            pass
        # jobs 'corriendo' huérfanos de un proceso anterior (reinicio del motor): sus
        # threads ya no existen → error, para que no bloqueen el gate de 1-job-por-VM
        # ni queden girando eternamente en el dashboard
        c.execute("UPDATE jobs SET estado='error', error='interrumpido por reinicio del motor', "
                  "updated_at=datetime('now','localtime') WHERE estado='corriendo'")

def audit(actor, accion, vm, detalle, resultado):
    with DB_LOCK, db() as c:
        c.execute("INSERT INTO operaciones(actor,accion,vm,detalle,resultado) VALUES(?,?,?,?,?)",
                  (actor, accion, vm, detalle[:500], resultado[:200]))

# ── Jobs con pasos visibles ──────────────────────────────────────────────────
class Job:
    """Job asíncrono cuyo avance (pasos) se persiste en SQLite y el dashboard
    lo va leyendo por GET /job/<id>. Cada paso: pendiente → corriendo → ok/error."""

    def __init__(self, tipo, vm, actor, nombres_pasos):
        self.id = uuid.uuid4().hex[:12]
        self.tipo, self.vm, self.actor = tipo, vm, actor
        self.pasos = [{"nombre": n, "estado": "pendiente", "detalle": "", "ts": ""} for n in nombres_pasos]
        self._i = -1
        with DB_LOCK, db() as c:
            c.execute("INSERT INTO jobs(id,tipo,vm,estado,pasos,actor) VALUES(?,?,?,?,?,?)",
                      (self.id, tipo, vm, "corriendo", json.dumps(self.pasos), actor))

    def _save(self, estado=None, error=None):
        with DB_LOCK, db() as c:
            c.execute("UPDATE jobs SET pasos=?, estado=COALESCE(?,estado), error=COALESCE(?,error), "
                      "updated_at=datetime('now','localtime') WHERE id=?",
                      (json.dumps(self.pasos), estado, error, self.id))

    def paso(self, detalle=""):
        """Cierra el paso actual como ok y abre el siguiente."""
        if self._i >= 0:
            self.pasos[self._i]["estado"] = "ok"
            if detalle:
                self.pasos[self._i]["detalle"] = detalle
        self._i += 1
        if self._i < len(self.pasos):
            self.pasos[self._i]["estado"] = "corriendo"
            self.pasos[self._i]["ts"] = time.strftime("%H:%M:%S")
        self._save()

    def detalle(self, txt):
        """Actualiza el detalle del paso en curso (progreso dentro del paso)."""
        if 0 <= self._i < len(self.pasos):
            self.pasos[self._i]["detalle"] = txt
            self._save()

    def ok(self, detalle_final=""):
        if 0 <= self._i < len(self.pasos):
            self.pasos[self._i]["estado"] = "ok"
            if detalle_final:
                self.pasos[self._i]["detalle"] = detalle_final
        self._save(estado="ok")
        audit(self.actor, self.tipo, self.vm, "job %s" % self.id, "ok")

    def set_resultado(self, d):
        """Guarda datos estructurados del resultado (acceso, IPs, link) — el
        dashboard los usa para mostrar el panel 'Cómo iniciar sesión' al final."""
        with DB_LOCK, db() as c:
            c.execute("UPDATE jobs SET resultado=? WHERE id=?", (json.dumps(d), self.id))

    def fail(self, msg):
        if 0 <= self._i < len(self.pasos):
            self.pasos[self._i]["estado"] = "error"
            self.pasos[self._i]["detalle"] = str(msg)[:400]
        self._save(estado="error", error=str(msg)[:400])
        audit(self.actor, self.tipo, self.vm, "job %s" % self.id, "ERROR: %s" % str(msg)[:150])


def run_job(job, fn):
    def _wrap():
        try:
            fn(job)
        except Exception as e:  # noqa: BLE001 — el job registra cualquier falla
            job.fail("%s: %s" % (type(e).__name__, e))
    threading.Thread(target=_wrap, daemon=True).start()

def job_activo(vm):
    """Job 'corriendo' para esa VM, o None."""
    with DB_LOCK, db() as c:
        row = c.execute("SELECT id,tipo FROM jobs WHERE vm=? AND estado='corriendo' LIMIT 1",
                        (vm,)).fetchone()
    return dict(row) if row else None

def lanzar_job_exclusivo(tipo, vm, actor, pasos):
    """Gate de EXCLUSIÓN MUTUA por VM: un solo job activo a la vez. Chequeo +
    creación del job en una sección crítica (JOB_GATE_LOCK por fuera, DB_LOCK por
    dentro) para que dos requests simultáneas no pasen ambas el chequeo. Evita
    eliminar/editar/suspender en paralelo sobre la misma VM (o durante su creación).
    Devuelve (job, None) o (None, job_activo_existente).
    OJO ARQUITECTURA: el gate (y los locks de asignación) son threading.Lock →
    válidos SOLO con UN proceso (app.run threaded / 1 contenedor). Si esto migra a
    gunicorn/multiproceso, el gate debe pasar a transacciones SQLite (BEGIN
    IMMEDIATE) y la limpieza de init_db dejaría de ser segura tal cual."""
    with JOB_GATE_LOCK:
        act = job_activo(vm)
        if act:
            return None, act
        job = Job(tipo, vm, actor, pasos)
    return job, None

def lanzar_o_fallar(job, fn):
    """run_job con manejo del caso patológico: si el hilo NO llega a arrancar
    (Thread.start falla), el job se marca error — si quedara 'corriendo' sin hilo,
    bloquearía el gate de su VM para siempre. Devuelve None si ok, o (json, 500)."""
    try:
        run_job(job, fn)
        return None
    except Exception as e:  # noqa: BLE001
        job.fail("no se pudo lanzar el hilo del job: %s" % e)
        return jsonify({"error": "no se pudo lanzar el job (ver registro de operaciones)"}), 500

# ── Acceso al ESXi: govc (API, usuario svc-vps) y SSH restringido (wrapper) ──
def govc(*args, timeout=120):
    r = subprocess.run([GOVC, *args], capture_output=True, text=True, timeout=timeout)
    if r.returncode:
        raise RuntimeError("govc %s: %s" % (args[0], (r.stderr or r.stdout).strip()[:300]))
    return r.stdout.strip()

def esxi_ssh(comando, timeout=300):
    """Ejecuta un subcomando del wrapper restringido. El authorized_keys fuerza
    command= → lo que enviamos llega como SSH_ORIGINAL_COMMAND al wrapper."""
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    cli.connect(ESXI_HOST, port=ESXI_SSH_PORT, username="root",
                key_filename=ESXI_SSH_KEY, timeout=20,
                allow_agent=False, look_for_keys=False)
    try:
        _, out, err = cli.exec_command(comando, timeout=timeout)
        code = out.channel.recv_exit_status()
        o, e = out.read().decode(), err.read().decode()
        if code != 0:
            raise RuntimeError("wrapper '%s' (exit %d): %s" % (comando.split()[0], code, (e or o).strip()[:300]))
        return o.strip()
    finally:
        cli.close()

# ── IP libre ─────────────────────────────────────────────────────────────────
def _ping(ip):
    return subprocess.run(["ping", "-c", "1", "-W", "1", ip],
                          capture_output=True).returncode == 0

def ip_libre_pruebas(subred, gateway, job=None):
    """Red de pruebas 192.168.122.0/24 (local a noc-monitor): barrido ping
    concurrente + ARP local + IPs ya reservadas en el registro. Octeto más alto
    libre desde .254 hacia abajo (mismo criterio que /api/alta/privada del NOC)."""
    net = ipaddress.ip_network(subred)
    candidatas = [str(h) for h in net.hosts()]
    ocupadas = set()
    with ThreadPoolExecutor(max_workers=64) as ex:
        for ip, viva in zip(candidatas, ex.map(_ping, candidatas)):
            if viva:
                ocupadas.add(ip)
    try:
        arp = subprocess.run(["ip", "neigh"], capture_output=True, text=True).stdout
        for m in re.finditer(r"(\d+\.\d+\.\d+\.\d+)\s.*(?:REACHABLE|STALE|DELAY|PROBE)", arp):
            if ipaddress.ip_address(m.group(1)) in net:
                ocupadas.add(m.group(1))
    except Exception:
        pass
    with DB_LOCK, db() as c:
        for row in c.execute("SELECT ip FROM vms WHERE ip IS NOT NULL AND estado != 'papelera'"):
            ocupadas.add(row["ip"])
    ocupadas.add(gateway)
    if job:
        job.detalle("%d IPs ocupadas detectadas (ping+ARP+registro)" % len(ocupadas))
    for ip in reversed(candidatas):
        if ip not in ocupadas:
            return ip
    raise RuntimeError("sin IPs libres en %s" % subred)


# ── Producción: MikroTik RouterData (IP privada/pública + NAT) ────────────────
def mikrotik(cmd, host=None, timeout=25):
    """Corre un comando en el MikroTik por SSH (mismo acceso claude@2420 del NOC)."""
    host = host or ROUTERDATA
    r = subprocess.run(
        ["ssh", "-i", MIKROTIK_KEY, "-o", "StrictHostKeyChecking=no", "-o", "BatchMode=yes",
         "-o", "LogLevel=ERROR", "-o", "ConnectTimeout=10", "-p", MIKROTIK_PORT,
         "%s@%s" % (MIKROTIK_USER, host), cmd],
        capture_output=True, text=True, timeout=timeout)
    return r.returncode == 0, r.stdout


def _octs(texto, red):
    """Octetos donde aparece red.X en el texto (NAT: src-address y to-addresses)."""
    return set(int(m.group(1)) for m in re.finditer(re.escape(red) + r"\.(\d+)\b", texto))


def _octs_arp_vivo(out_arp, red):
    """Octetos con ARP REALMENTE vivo (failed/incomplete = libre). El barrido de IPs
    deja entradas failed de todo el /24; sin este filtro parecería todo ocupado."""
    octs = set()
    for line in out_arp.splitlines():
        if "status=failed" in line or "status=incomplete" in line:
            continue
        m = re.search(r"address=" + re.escape(red) + r"\.(\d+)\b", line)
        if m:
            octs.add(int(m.group(1)))
    return octs


def ip_libre_produccion(subred, gateway, job=None):
    """IP privada libre por RouterData (NAT+ARP), no por ping — noc-monitor no
    barre la 10.100.16.x pero RouterData es la fuente de verdad. ocupadas =
    NAT ∪ ARP-vivo ∪ {.1} ∪ registro; octeto más alto libre desde .254."""
    red = ".".join(subred.split("/")[0].split(".")[:3])
    ok_nat, out_nat = mikrotik("/ip firewall nat print terse")
    ok_arp, out_arp = mikrotik("/ip arp print terse")
    if not ok_nat or not ok_arp:
        raise RuntimeError("no pude consultar RouterData (NAT/ARP) para la IP privada")
    en_nat, en_arp = _octs(out_nat, red), _octs_arp_vivo(out_arp, red)
    ocupadas = en_nat | en_arp | {int(gateway.split(".")[-1])}
    with DB_LOCK, db() as c:
        for row in c.execute("SELECT ip FROM vms WHERE ip LIKE ? AND estado!='papelera'", (red + ".%",)):
            try:
                ocupadas.add(int(row["ip"].split(".")[-1]))
            except (ValueError, AttributeError):
                pass
    if job:
        job.detalle("RouterData %s: %d en NAT · %d vivas en ARP · %d ocupadas"
                    % (red, len(en_nat), len(en_arp), len(ocupadas)))
    for o in range(254, 1, -1):
        if o not in ocupadas:
            return "%s.%d" % (red, o)
    raise RuntimeError("sin IPs privadas libres en %s" % subred)


def ip_publica_libre(lista, rango=None, job=None):
    """IP pública libre de la address-list (mismo criterio que /api/alta/publica del
    NOC): candidata habilitada y sin comentario, sin NAT previo, sin ARP vivo, sin
    otras address-lists, y muda al ping desde el CCR de borde. Si `rango` = [lo, hi],
    se restringe a ese rango (para pruebas). Devuelve (ip, rechazadas)."""
    red = PUB_REDES.get(lista)
    if not red:
        raise RuntimeError("lista pública desconocida: %s" % lista)
    ok_l, out_l = mikrotik("/ip firewall address-list print terse where list=" + lista)
    ok_nat, out_nat = mikrotik("/ip firewall nat print terse")
    ok_arp, out_arp = mikrotik("/ip arp print terse")
    if not (ok_l and ok_nat and ok_arp):
        raise RuntimeError("no pude consultar RouterData para la IP pública")
    candidatas = []
    for line in out_l.splitlines():
        m = re.search(r"address=" + re.escape(red) + r"\.(\d+)\b", line)
        if not m:
            continue
        oct_ = int(m.group(1))
        if rango and not (rango[0] <= oct_ <= rango[1]):
            continue
        deshab = bool(re.match(r"^\s*\d+\s+X", line))   # X = deshabilitada = en uso
        if not deshab and "comment=" not in line:       # habilitada y sin comentario = libre
            candidatas.append(oct_)
    candidatas.sort(reverse=True)
    en_nat, en_arp = _octs(out_nat, red), _octs_arp_vivo(out_arp, red)
    rechazadas = []
    for oct_ in candidatas:
        ip = "%s.%d" % (red, oct_)
        if oct_ in en_nat:
            rechazadas.append("%s (NAT)" % ip); continue
        if oct_ in en_arp:
            rechazadas.append("%s (ARP viva)" % ip); continue
        ok_o, out_o = mikrotik("/ip firewall address-list print terse where address=" + ip)
        otras = [l for l in (out_o or "").splitlines() if l.strip() and ("list=" + lista) not in l]
        if otras:
            rechazadas.append("%s (otras listas)" % ip); continue
        ok_p, out_p = mikrotik("/ping %s count=3" % ip, host=CCR_BORDE)
        if not ok_p or "received=0" not in out_p:
            rechazadas.append("%s (responde ping)" % ip); continue
        if job:
            job.detalle("pública %s libre (validada: lista/NAT/ARP/otras/ping)" % ip)
        return ip, rechazadas
    raise RuntimeError("sin IP pública libre en %s. Rechazadas: %s" % (lista, ", ".join(rechazadas) or "ninguna candidata"))


def crear_nat(privada, publica, lista, hostname, marca_nombre, job=None):
    """NAT 1:1 en RouterData, con el MISMO formato que 'Alta de Servicios' del NOC:
      - srcnat CON comentario '[NOC] <host> <ip-corta>, VPS <marca>'
      - dstnat SIN comentario (queda agrupado bajo el srcnat)
      - la entrada de la address-list se comenta '<host> - VPS <marca>' y se deshabilita."""
    # re-chequeo anti-carrera: ninguna de las 2 IPs debe tener NAT ya
    ok, out = mikrotik("/ip firewall nat print terse")
    if ok and re.search(r"=(?:%s|%s)\b" % (re.escape(privada), re.escape(publica)), out):
        raise RuntimeError("una de las IPs ya tiene NAT (¿carrera?) — abortado")
    pub_short = ".".join(publica.split(".")[-2:])
    etiqueta = "%s %s, VPS %s" % (hostname, pub_short, marca_nombre)
    ok1, o1 = mikrotik('/ip firewall nat add chain=srcnat src-address=%s action=src-nat '
                       'to-addresses=%s comment="[NOC] %s"' % (privada, publica, etiqueta))
    if not ok1:
        raise RuntimeError("srcnat falló: %s" % o1[:200])
    ok2, o2 = mikrotik('/ip firewall nat add chain=dstnat dst-address=%s action=dst-nat '
                       'to-addresses=%s' % (publica, privada))   # dstnat SIN comment
    if not ok2:
        mikrotik('/ip firewall nat remove [find where chain=srcnat and src-address="%s" and to-addresses="%s"]'
                 % (privada, publica))  # rollback del srcnat (valores ENTRECOMILLADOS: RouterOS no matchea sin comillas)
        raise RuntimeError("dstnat falló (srcnat revertido): %s" % o2[:200])
    mikrotik('/ip firewall address-list set [find where list=%s and address="%s"] '
             'comment="%s - VPS %s" disabled=yes' % (lista, publica, hostname, marca_nombre))
    if job:
        job.detalle("NAT 1:1 creado: %s ↔ %s (srcnat con comentario [NOC], dstnat sin comentario)"
                    % (privada, publica))


def borrar_nat(privada, publica, lista, job=None):
    """Revierte el NAT y libera la pública en la address-list (al borrar el VPS).
    Igual que /api/alta/baja del NOC: los valores del `find` van ENTRECOMILLADOS —
    sin comillas RouterOS no matchea y el remove no borra nada (bug detectado 2026-09-11).
    ORDEN SEGURO (#6): remover → VERIFICAR que el par exacto ya no existe → recién
    ahí liberar la pública. Lanza RuntimeError en cualquier falla o si queda NAT:
    ante la duda la pública queda deshabilitada/comentada (nadie la reasigna) y el
    job de eliminación debe quedar en error — nunca éxito en falso."""
    ok1, o1 = mikrotik('/ip firewall nat remove [find where chain=srcnat and src-address="%s" and to-addresses="%s"]'
                       % (privada, publica))
    ok2, o2 = mikrotik('/ip firewall nat remove [find where chain=dstnat and dst-address="%s" and to-addresses="%s"]'
                       % (publica, privada))
    if not (ok1 and ok2):
        raise RuntimeError("remove NAT falló (srcnat ok=%s, dstnat ok=%s): %s"
                           % (ok1, ok2, (o1 or o2).strip()[:150]))
    # verificación ESTRICTA del par exacto (no por aparición suelta de la IP: reglas
    # de terceros con esa privada no deben dar falso positivo, ni una consulta caída
    # falso éxito)
    okv1, res1 = mikrotik('/ip firewall nat print terse where chain=srcnat and src-address="%s" and to-addresses="%s"'
                          % (privada, publica))
    okv2, res2 = mikrotik('/ip firewall nat print terse where chain=dstnat and dst-address="%s" and to-addresses="%s"'
                          % (publica, privada))
    if not (okv1 and okv2):
        raise RuntimeError("no pude VERIFICAR la reversión del NAT %s↔%s (consulta a RouterData falló) — "
                           "la pública queda SIN liberar por seguridad" % (privada, publica))
    residuo = [l for l in (res1 + res2).splitlines() if l.strip()]
    if residuo:
        raise RuntimeError("el NAT %s↔%s SIGUE presente tras el remove (%d regla/s) — "
                           "la pública queda SIN liberar por seguridad" % (privada, publica, len(residuo)))
    # NAT comprobadamente revertido → recién ahora se libera la pública
    ok3, o3 = mikrotik('/ip firewall address-list set [find where list=%s and address="%s"] '
                       'comment="" disabled=no' % (lista, publica))
    if not ok3:
        raise RuntimeError("NAT revertido, pero no pude liberar la pública %s en %s: %s"
                           % (publica, lista, o3.strip()[:150]))
    if job:
        job.detalle("NAT removido (verificado el par exacto) y pública %s liberada en %s" % (publica, lista))
    return True


# ── Securización: llave del cliente → bóveda → Bitwarden Send ─────────────────
def gen_ed25519(comment):
    """Genera un par ed25519; devuelve (privada, pública, fingerprint)."""
    with tempfile.TemporaryDirectory() as tmp:
        kf = os.path.join(tmp, "key")
        subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", "", "-C", comment, "-f", kf],
                       check=True, capture_output=True)
        priv = open(kf).read()
        pub = open(kf + ".pub").read().strip()
        fp = subprocess.run(["ssh-keygen", "-lf", kf + ".pub"],
                            capture_output=True, text=True).stdout.split()[1]
    return priv, pub, fp


# ── NetBox (IPAM): registrar/limpiar las IPs de cada VPS — best effort ───────
def netbox_req(method, path, payload=None, timeout=10):
    import urllib.request
    req = urllib.request.Request(
        NETBOX_URL + path, method=method,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Authorization": "Token " + NETBOX_TOKEN,
                 "Content-Type": "application/json", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode()
        return json.loads(body) if body.strip() else {}


def netbox_ip_add(address_cidr, dns_name, descripcion):
    """Registra una IP en NetBox (status active). Devuelve el id, o None si falla
    (best-effort: nunca bloquea la creación). Si la IP ya existe, devuelve su id."""
    if not (NETBOX_URL and NETBOX_TOKEN):
        return None
    try:
        r = netbox_req("GET", "/api/ipam/ip-addresses/?address=" + address_cidr.split("/")[0])
        if r.get("count"):
            nb_id = r["results"][0]["id"]
            netbox_req("PATCH", "/api/ipam/ip-addresses/%d/" % nb_id,
                       {"status": "active", "dns_name": dns_name, "description": descripcion})
            return nb_id
        r = netbox_req("POST", "/api/ipam/ip-addresses/",
                       {"address": address_cidr, "status": "active",
                        "dns_name": dns_name, "description": descripcion})
        return r.get("id")
    except Exception:  # noqa: BLE001
        return None


def netbox_ip_del(nb_id):
    """Limpia un registro de IP en NetBox (best-effort). Intenta DELETE; si el token
    no tiene permiso de borrado (403), lo marca como 'deprecated' (liberada)."""
    if not (NETBOX_URL and NETBOX_TOKEN and nb_id):
        return False
    try:
        netbox_req("DELETE", "/api/ipam/ip-addresses/%d/" % int(nb_id))
        return True
    except Exception:  # noqa: BLE001 — fallback: marcar liberada
        try:
            netbox_req("PATCH", "/api/ipam/ip-addresses/%d/" % int(nb_id),
                       {"status": "deprecated", "dns_name": "",
                        "description": "LIBERADA (VPS eliminado por vps-engine)"})
            return True
        except Exception:  # noqa: BLE001
            return False

# ── Cliente API de WHMCS (dirección motor → WHMCS) ───────────────────────────
def whmcs_api(action, params=None, timeout=15):
    """Llama a la API de WHMCS. Genérico: sirve para cualquier acción (UpdateClientProduct,
    SendEmail, etc.). Devuelve el JSON de respuesta, o None si no está configurado."""
    if not (WHMCS_API_URL and WHMCS_API_ID and WHMCS_API_SECRET):
        return None
    import urllib.request, urllib.parse
    data = {"identifier": WHMCS_API_ID, "secret": WHMCS_API_SECRET,
            "action": action, "responsetype": "json"}
    if params:
        data.update({k: v for k, v in params.items() if v is not None})
    req = urllib.request.Request(WHMCS_API_URL, method="POST",
                                 data=urllib.parse.urlencode(data).encode())
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode()
        return json.loads(body) if body.strip() else {}

def whmcs_set_ip(serviceid, ip, job=None):
    """Rellena la IP dedicada de un servicio en la ficha de WHMCS (best-effort). Solo actúa si
    hay serviceid (creación disparada por WHMCS) y la API está configurada."""
    if not (serviceid and ip and WHMCS_API_URL):
        return False
    try:
        r = whmcs_api("UpdateClientProduct", {"serviceid": serviceid, "dedicatedip": ip})
        ok = bool(r and r.get("result") == "success")
        if job:
            job.detalle("IP %s enviada a la ficha de WHMCS (servicio %s): %s"
                        % (ip, serviceid, "ok" if ok else ("respuesta: %s" % (r or "sin config"))))
        return ok
    except Exception as e:  # noqa: BLE001 — no bloquear la creación por WHMCS
        if job:
            job.detalle("aviso: no se pudo actualizar la ficha WHMCS (%s)" % e)
        return False


def provision_post(path, payload, timeout=60):
    """Llama a vps-provision (bóveda). Devuelve el dict de respuesta o lanza."""
    import urllib.request
    import urllib.error
    req = urllib.request.Request(PROVISION_URL + path, data=json.dumps(payload).encode(),
                                 method="POST", headers={"Content-Type": "application/json",
                                                         "X-Auth-Token": PROVISION_TOKEN})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


# Comentario restringido a caracteres seguros (defensa en profundidad: la llave además
# viaja por stdin y nunca se interpola en un comando de shell — ver instalar_pubkey_en_vm).
PUBKEY_RE = re.compile(r"^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(256|384|521)) [A-Za-z0-9+/=]{40,3000}( [A-Za-z0-9@ ._:+=/-]{0,120})?$")


def fingerprint_pubkey(pub):
    with tempfile.TemporaryDirectory() as tmp:
        pf = os.path.join(tmp, "k.pub")
        with open(pf, "w", newline="\n") as fh:
            fh.write(pub.strip() + "\n")
        r = subprocess.run(["ssh-keygen", "-lf", pf], capture_output=True, text=True)
        if r.returncode:
            raise RuntimeError("llave pública inválida: " + (r.stderr or r.stdout)[:120])
        return r.stdout.split()[1]


def instalar_pubkey_en_vm(ip, pub):
    """Agrega una llave pública al authorized_keys de la VM (vía llave de gestión).
    La llave viaja por STDIN (mismo patrón que set_root_password/chpasswd): el comando
    remoto es fijo, así el contenido no puede inyectar shell aunque el comentario
    traiga $, backticks, etc. Lanza RuntimeError si la instalación falla."""
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    cli.connect(ip, username="root", key_filename=MGMT_PRIVKEY_PATH, timeout=15,
                allow_agent=False, look_for_keys=False)
    script = ('key=$(cat) && mkdir -p /root/.ssh && chmod 700 /root/.ssh '
              '&& touch /root/.ssh/authorized_keys && chmod 600 /root/.ssh/authorized_keys '
              '&& { grep -qxF -- "$key" /root/.ssh/authorized_keys '
              '|| printf \'%s\\n\' "$key" >> /root/.ssh/authorized_keys; }')
    try:
        stdin, out, err = cli.exec_command(script, timeout=30)
        stdin.write(pub.strip() + "\n")
        stdin.flush()  # no-op con el bufsize=-1 por defecto (paramiko no bufferiza), seguro ante cambios
        stdin.channel.shutdown_write()
        rc = out.channel.recv_exit_status()
        if rc != 0:
            detalle = err.read().decode(errors="replace")[:200]
            raise RuntimeError("instalar llave pública en %s falló (rc=%s): %s" % (ip, rc, detalle))
    finally:
        cli.close()


def securizar_byo(job, nombre, ip, pubkey_cliente):
    """Modo BYO ('trae tu llave'): instala la PÚBLICA que entregó el cliente.
    No se genera nada, no se custodia nada — la privada nunca toca nuestros
    sistemas (estándar más alto). El fingerprint queda en el registro."""
    fp = fingerprint_pubkey(pubkey_cliente)
    instalar_pubkey_en_vm(ip, pubkey_cliente)
    with DB_LOCK, db() as c:
        c.execute("UPDATE vms SET byo_pubkey_fp=? WHERE nombre=?", (fp, nombre))
    job.detalle("llave PÚBLICA del cliente instalada (BYO, %s) — sin custodia: la privada la tiene solo el cliente" % fp)
    return fp


def securizar_vps(job, nombre, ip, cliente, hostname):
    """Genera el par del cliente, instala su pública en la VM (SSH con la llave de
    gestión) y delega en vps-provision guardar en la bóveda + crear el Send.
    Devuelve el dict con el link de entrega, o None si no hay llave/token."""
    if not (MGMT_PRIVKEY_PATH and PROVISION_TOKEN):
        job.detalle("securización omitida (falta llave de gestión o token de vps-provision)")
        return None
    priv, pub, fp = gen_ed25519("cliente:%s %s" % (cliente, hostname))
    instalar_pubkey_en_vm(ip, pub)
    job.detalle("llave del cliente instalada en la VM (%s)" % fp)
    # guardar en la bóveda + crear Send vía vps-provision
    import urllib.request
    import urllib.error
    item_name = "%s (%s) - %s" % (hostname, ip, cliente)
    notes = "Cliente: %s\nVPS: %s %s\nGenerada por vps-engine al crear el VPS." % (cliente, hostname, ip)
    payload = json.dumps({"cliente": cliente, "item_name": item_name, "notes": notes,
                          "priv": priv, "pub": pub, "fingerprint": fp, "days": 2}).encode()
    req = urllib.request.Request(PROVISION_URL + "/vault-guardar-enviar", data=payload,
                                 method="POST", headers={"Content-Type": "application/json",
                                                         "X-Auth-Token": PROVISION_TOKEN})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            r = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError("bóveda/Send falló: HTTP %s %s" % (e.code, e.read().decode()[:200]))
    job.detalle("guardada en la bóveda (%s) y Send de entrega creado" % item_name)
    return r


# ── Plantilla VMX ────────────────────────────────────────────────────────────
VMX_TEMPLATE = """.encoding = "UTF-8"
config.version = "8"
virtualHW.version = "19"
guestOS = "centos9-64"
displayName = "{name}"
memSize = "{ram_mb}"
numvcpus = "{vcpu}"
firmware = "bios"
pciBridge0.present = "TRUE"
pciBridge4.present = "TRUE"
pciBridge4.virtualDev = "pcieRootPort"
pciBridge4.functions = "8"
pciBridge5.present = "TRUE"
pciBridge5.virtualDev = "pcieRootPort"
pciBridge5.functions = "8"
pciBridge6.present = "TRUE"
pciBridge6.virtualDev = "pcieRootPort"
pciBridge6.functions = "8"
pciBridge7.present = "TRUE"
pciBridge7.virtualDev = "pcieRootPort"
pciBridge7.functions = "8"
scsi0.present = "TRUE"
scsi0.virtualDev = "pvscsi"
scsi0:0.present = "TRUE"
scsi0:0.fileName = "{name}.vmdk"
scsi0:0.deviceType = "scsi-hardDisk"
scsi0:0.ctkEnabled = "TRUE"
ethernet0.present = "TRUE"
ethernet0.virtualDev = "vmxnet3"
ethernet0.networkName = "{portgroup}"
ethernet0.addressType = "generated"
vmci0.present = "TRUE"
tools.syncTime = "TRUE"
mem.hotadd = "TRUE"
vcpu.hotadd = "TRUE"
disk.EnableUUID = "TRUE"
ctkEnabled = "TRUE"
powerType.powerOff = "soft"
powerType.reset = "soft"
"""

def _gz64(texto):
    return base64.b64encode(gzip.compress(texto.encode())).decode()

def cloudinit_metadata(nombre, hostname, ip, prefijo, gateway, dns):
    md = {
        "instance-id": nombre,
        "local-hostname": hostname,
        "network": {"version": 2, "ethernets": {"nic0": {
            "match": {"name": "e*"},
            "addresses": ["%s/%d" % (ip, prefijo)],
            "gateway4": gateway,
            "nameservers": {"addresses": dns},
        }}},
    }
    return json.dumps(md)

def cloudinit_userdata(hostname, ip, prefijo, gateway, dns):
    # El datasource VMware en ESXi standalone suele detectarse en la etapa
    # "network" (después de que la NIC ya subió por DHCP), así que cloud-init
    # NO aplica la red estática del metadata. La forzamos determinísticamente
    # con nmcli (script en write_files → runcmd, corre con NetworkManager arriba).
    dns_str = " ".join(dns)
    return """#cloud-config
hostname: %(host)s
disable_root: false
ssh_pwauth: false
users:
  - name: root
    ssh_authorized_keys:
      - %(pub)s
growpart:
  mode: auto
  devices: ['/']
write_files:
  - path: /usr/local/sbin/vps-netcfg.sh
    permissions: '0755'
    content: |
      #!/bin/bash
      DEV=$(nmcli -t -f DEVICE,TYPE dev status | awk -F: '$2=="ethernet"{print $1; exit}')
      CON=$(nmcli -t -f NAME,DEVICE con show --active | awk -F: -v d="$DEV" '$2==d{print $1; exit}')
      [ -z "$CON" ] && CON=$(nmcli -t -f NAME,DEVICE con show | awk -F: -v d="$DEV" '$2==d{print $1; exit}')
      [ -z "$CON" ] && CON="$DEV"
      nmcli con mod "$CON" ipv4.addresses %(ip)s/%(pfx)d ipv4.gateway %(gw)s ipv4.dns "%(dns)s" ipv4.method manual
      nmcli con up "$CON"
runcmd:
  - [bash, /usr/local/sbin/vps-netcfg.sh]
  - [sh, -c, 'touch /var/lib/vps-engine.provisioned']
""" % {"host": hostname, "pub": MGMT_PUBKEY, "ip": ip, "pfx": prefijo,
       "gw": gateway, "dns": dns_str}

# ── Helpers de dominio ───────────────────────────────────────────────────────
VM_RE = re.compile(r"^vps-[a-z]{2,5}-[a-z0-9][a-z0-9-]{0,40}$")

def vm_registrada(nombre):
    with DB_LOCK, db() as c:
        row = c.execute("SELECT * FROM vms WHERE nombre=?", (nombre,)).fetchone()
    return dict(row) if row else None

def guardarraices(nombre):
    """Capas 1 y 2: en el registro Y nombre con prefijo vps-. Lanza si no."""
    if not VM_RE.match(nombre or ""):
        raise RuntimeError("nombre fuera de la convención vps-*: %r" % nombre)
    vm = vm_registrada(nombre)
    if not vm:
        raise RuntimeError("la VM %s NO está en el registro de gestionadas — no se toca" % nombre)
    return vm

def set_estado(nombre, estado, **extra):
    sets = ", ".join(["estado=?"] + ["%s=?" % k for k in extra])
    with DB_LOCK, db() as c:
        c.execute("UPDATE vms SET %s, updated_at=datetime('now','localtime') WHERE nombre=?" % sets,
                  (estado, *extra.values(), nombre))

def siguiente_nombre(marca_cfg, cliente):
    # Numeración por MAX(sufijo)+1, no por COUNT: robusto ante borrados (si se
    # borra una del medio, no se reutiliza su número ni colisiona con las vivas).
    pref = marca_cfg["prefijo_vm"]
    rx = re.compile(r"^%s-(\d+)" % re.escape(pref))
    with DB_LOCK, db() as c:
        nums = [int(m.group(1)) for r in c.execute("SELECT nombre FROM vms WHERE nombre LIKE ?",
                (pref + "-%",)) for m in [rx.match(r["nombre"])] if m]
    nxt = (max(nums) + 1) if nums else 1
    slug = re.sub(r"[^a-z0-9-]", "", (cliente or "").lower().replace(" ", "-"))[:20]
    base = "%s-%04d" % (pref, nxt)
    return ("%s-%s" % (base, slug)) if slug else base

def reservar_vm(marca, marca_cfg, sabor_slug, sabor, cliente, hostname, whmcs_serviceid=None):
    """Elige el siguiente nombre y lo RESERVA (INSERT estado='creando') en una sola
    sección crítica. Antes el nombre se calculaba en el endpoint y se insertaba
    después en el hilo del job: dos creaciones simultáneas podían calcular el mismo
    número. Con la reserva atómica, la segunda ve la fila de la primera y toma el
    número siguiente. (nombre es PRIMARY KEY: tercera capa por si acaso.)"""
    with NOMBRE_LOCK:
        nombre = siguiente_nombre(marca_cfg, cliente)
        with DB_LOCK, db() as c:
            c.execute("INSERT INTO vms(nombre,marca,sabor,cliente,hostname,estado,vcpu,ram_mb,disco_gb,whmcs_serviceid) "
                      "VALUES(?,?,?,?,?,'creando',?,?,?,?)",
                      (nombre, marca, sabor_slug, cliente, hostname,
                       sabor["vcpu"], sabor["ram_mb"], sabor["disco_gb"], whmcs_serviceid))
    return nombre

def red_activa(marca_cfg, modo):
    return marca_cfg["red_pruebas"] if modo == "pruebas" else marca_cfg["red_default"]

def power_state(nombre):
    try:
        out = govc("vm.info", "-json", nombre)
        vms = json.loads(out).get("virtualMachines") or json.loads(out).get("VirtualMachines") or []
        if vms:
            return vms[0].get("runtime", vms[0].get("Runtime", {})).get("powerState",
                   vms[0].get("Runtime", {}).get("PowerState", "?"))
    except Exception:
        pass
    return "?"

def apagar_graceful(job, nombre, espera=60):
    """Shutdown por Tools; si a los `espera` s sigue prendida, power off duro."""
    try:
        govc("vm.power", "-s", nombre)
    except RuntimeError as e:
        if "already" in str(e) or "powered off" in str(e).lower():
            return
        govc("vm.power", "-off", nombre)
        return
    t0 = time.time()
    while time.time() - t0 < espera:
        if power_state(nombre) == "poweredOff":
            return
        time.sleep(4)
        job.detalle("esperando apagado graceful (%ds)…" % int(time.time() - t0))
    job.detalle("no apagó graceful en %ds → power off forzado" % espera)
    govc("vm.power", "-off", nombre)

# ── FLUJO: crear ─────────────────────────────────────────────────────────────
PASOS_CREAR = [
    "Validar datos y asignar nombre",
    "Buscar IP privada libre",
    "Verificar espacio en datastore",
    "Clonar disco de la plantilla dorada",
    "Crecer disco al tamaño del plan",
    "Generar y subir configuración (.vmx)",
    "Registrar la VM en el ESXi",
    "Inyectar cloud-init (hostname, red, llave de gestión)",
    "Encender la VM",
    "Esperar IP por VMware Tools",
    "Verificar acceso SSH con la llave de gestión",
    "Elegir IP pública y crear NAT",
    "Instalar cPanel (última versión)",
    "Securizar y entregar llave al cliente",
    "Registrar y finalizar",
]

def set_root_password(ip, password):
    """Aplica la contraseña de root en la VM por SSH (chpasswd vía stdin — sin problemas
    de escape, y NO queda en el VMX). SSH sigue solo con llave: esta clave sirve para WHM/
    consola, no para SSH (PasswordAuthentication no lo permite). Best-effort."""
    if not (MGMT_PRIVKEY_PATH and ip and password):
        return False
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    cli.connect(ip, username="root", key_filename=MGMT_PRIVKEY_PATH, timeout=15,
                allow_agent=False, look_for_keys=False)
    try:
        stdin, _out, _err = cli.exec_command("chpasswd")
        stdin.write("root:%s\n" % password)
        stdin.channel.shutdown_write()
        _out.channel.recv_exit_status()
    finally:
        cli.close()
    return True

def flujo_crear(job, marca, sabor_slug, cliente, hostname, instalar_cpanel, modo,
                pubkey_cliente=None, root_password=None, whmcs_serviceid=None):
    marca_cfg = MARCAS[marca]
    sabor = SABORES[(marca, sabor_slug)]
    red = red_activa(marca_cfg, modo)
    prefijo = ipaddress.ip_network(red["subred"]).prefixlen
    nombre = job.vm

    # 1. validar la reserva del registro (el INSERT atómico ya lo hizo reservar_vm
    #    en /crear — cierra la carrera de nombre/número entre creaciones paralelas)
    job.paso()
    reg = vm_registrada(nombre)
    if not reg or reg.get("estado") != "creando":
        raise RuntimeError("reserva de %s no encontrada o en estado inesperado" % nombre)
    job.detalle("%s · %s (%d vCPU / %d MB / %d GB) · modo %s"
                % (nombre, sabor["nombre_web"], sabor["vcpu"], sabor["ram_mb"], sabor["disco_gb"], modo))

    # 2. IP privada libre — RouterData en producción, barrido local en pruebas.
    #    Bajo IP_PRIV_LOCK: elegir y GRABAR es atómico entre jobs (ambos flujos
    #    consultan el registro, así el siguiente ya la ve ocupada).
    job.paso()
    with IP_PRIV_LOCK:
        if modo == "produccion":
            ip = ip_libre_produccion(red["subred"], red["gateway"], job)
        else:
            ip = ip_libre_pruebas(red["subred"], red["gateway"], job)
        set_estado(nombre, "creando", ip=ip)
    job.detalle("IP privada asignada: %s (gw %s)" % (ip, red["gateway"]))

    # 3. espacio
    job.paso()
    df = esxi_ssh("df")
    job.detalle("datastore: %s" % df)

    # 4. clonar
    job.paso("IP %s reservada" % ip)
    esxi_ssh("mkdir-vm %s" % nombre)
    # catálogo de doradas: 2 por SO — base y '-cpanel' (preinstalado). Si el plan
    # lleva cPanel se clona la variante (entrega ~7 min); si aún no existe, se cae
    # al plan B: base + instalación post-creación (30-60 min).
    cpanel_preinstalado = False
    if instalar_cpanel:
        try:
            job.detalle("clonando %s-cpanel → %s (thin, cPanel preinstalado)…" % (DORADA_DEFAULT, nombre))
            esxi_ssh("clone-disk %s-cpanel %s" % (DORADA_DEFAULT, nombre), timeout=900)
            cpanel_preinstalado = True
        except RuntimeError as e:
            if "plantilla no existe" in str(e):
                job.detalle("dorada -cpanel no disponible → se usará la base e instalación post-creación")
            else:
                raise
    if not cpanel_preinstalado:
        job.detalle("clonando %s → %s (thin)…" % (DORADA_DEFAULT, nombre))
        esxi_ssh("clone-disk %s %s" % (DORADA_DEFAULT, nombre), timeout=900)

    # 5. crecer disco
    job.paso()
    esxi_ssh("grow-disk %s %d" % (nombre, sabor["disco_gb"]))
    job.detalle("disco extendido a %d GB (el guest lo crece al boot)" % sabor["disco_gb"])

    # 6. vmx
    job.paso()
    vmx = VMX_TEMPLATE.format(name=nombre, ram_mb=sabor["ram_mb"], vcpu=sabor["vcpu"],
                              portgroup=red["portgroup"])
    with open("/tmp/%s.vmx" % nombre, "w") as fh:
        fh.write(vmx)
    govc("datastore.upload", "-ds", DATASTORE, "/tmp/%s.vmx" % nombre,
         "VPS/%s/%s.vmx" % (nombre, nombre))
    os.unlink("/tmp/%s.vmx" % nombre)
    job.detalle("VM con CBT activo (ctkEnabled) — lista para backups incrementales")

    # 7. registrar en ESXi
    job.paso()
    govc("vm.register", "-ds", DATASTORE, "VPS/%s/%s.vmx" % (nombre, nombre))

    # 8. cloud-init vía guestinfo
    job.paso()
    fqdn = hostname if "." in hostname else "%s.%s" % (hostname, marca_cfg.get("dominio_hostname", marca))
    md = cloudinit_metadata(nombre, fqdn, ip, prefijo, red["gateway"], red["dns"])
    ud = cloudinit_userdata(fqdn, ip, prefijo, red["gateway"], red["dns"])
    govc("vm.change", "-vm", nombre,
         "-e", "guestinfo.metadata=%s" % _gz64(md),
         "-e", "guestinfo.metadata.encoding=gzip+base64",
         "-e", "guestinfo.userdata=%s" % _gz64(ud),
         "-e", "guestinfo.userdata.encoding=gzip+base64")

    # 9. power on
    job.paso()
    govc("vm.power", "-on", nombre)

    # 10. esperar IP
    job.paso()
    t0, ip_real = time.time(), ""
    while time.time() - t0 < 420:
        try:
            ip_real = govc("vm.ip", "-wait", "30s", nombre, timeout=45)
        except RuntimeError:
            ip_real = ""
        if ip_real:
            break
        job.detalle("esperando VMware Tools/IP… (%ds)" % int(time.time() - t0))
    if not ip_real:
        raise RuntimeError("la VM no reportó IP en 7 min — revisar consola en la UI de ESXi")
    if ip_real == ip:
        job.detalle("VM arriba con su IP estática %s" % ip)
    else:
        job.detalle("VM arriba (IP DHCP transitoria %s); la estática %s se fija al "
                    "arranque y se confirma en el paso siguiente" % (ip_real, ip))

    # 11. verificar ssh (hasta ~4 min: el primer boot con cPanel preinstalado es
    #     pesado — la IP estática y sshd pueden tardar 2-3 min en quedar arriba).
    #     AUTO-REINICIO (visto 2026-09-15, vps-hcl-0013): a veces el primer boot NO
    #     aplica la IP estática (carrera cloud-init/NetworkManager con el growpart y
    #     el primer arranque de cPanel) aunque la config quede bien escrita — un
    #     reinicio la aplica. Si a los ~2.5 min no hay SSH, se reinicia la VM UNA vez
    #     (reboot por Tools; si falla, reset duro) y se sigue esperando.
    job.paso()
    if MGMT_PRIVKEY_PATH:
        ultimo = None
        t0 = time.time()
        reiniciada = False
        for intento in range(40):
            try:
                cli = paramiko.SSHClient()
                cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                cli.connect(ip, username="root", key_filename=MGMT_PRIVKEY_PATH,
                            timeout=10, allow_agent=False, look_for_keys=False)
                cli.close()
                ultimo = None
                break
            except Exception as e:
                ultimo = e
                transcurrido = int(time.time() - t0)
                if not reiniciada and transcurrido >= 150:
                    reiniciada = True
                    # govc puede fallar con RuntimeError (exit != 0) o TimeoutExpired
                    # (subprocess) — ninguno debe abortar la espera de SSH
                    try:
                        govc("vm.power", "-r", nombre)
                        job.detalle("sin SSH tras %ds — reinicio automático de la VM (1 vez): "
                                    "el primer boot a veces no aplica la IP estática" % transcurrido)
                    except (RuntimeError, subprocess.TimeoutExpired):
                        try:
                            govc("vm.power", "-reset", nombre)
                            job.detalle("sin SSH tras %ds — reset automático de la VM (reboot por Tools no disponible)"
                                        % transcurrido)
                        except (RuntimeError, subprocess.TimeoutExpired) as e3:
                            job.detalle("no pude reiniciar la VM automáticamente (%s) — sigo esperando" % str(e3)[:80])
                else:
                    job.detalle("esperando sshd en %s… (%ds%s)"
                                % (ip, transcurrido,
                                   "; ya reiniciada 1 vez" if reiniciada
                                   else "; el primer boot con cPanel tarda 2-3 min"))
                time.sleep(10)
        if ultimo:
            raise RuntimeError("SSH con llave de gestión no entra tras %ds%s: %s"
                               % (int(time.time() - t0),
                                  " (incluso tras 1 reinicio automático)" if reiniciada else "",
                                  ultimo))
        job.detalle("SSH root@%s con llave de gestión: OK" % ip)
        # clave de root (opcional, viene de WHMCS): se aplica por SSH; SSH sigue key-only,
        # esta clave sirve para WHM/consola, no para login SSH.
        if root_password:
            try:
                set_root_password(ip, root_password)
                job.detalle("contraseña de root aplicada (para WHM/consola; SSH sigue solo con llave)")
            except Exception as e:  # noqa: BLE001 — no bloquear la creación por esto
                job.detalle("aviso: no se pudo aplicar la contraseña de root (%s)" % e)
    else:
        job.detalle("sin MGMT_PRIVKEY_PATH configurada — verificación omitida")

    # 12. IP pública + NAT 1:1 (solo producción)
    job.paso()
    publica = pub_lista = None
    if modo == "produccion":
        pub_lista = marca_cfg.get("pub_lista", "Red107-0")
        rango = red.get("publica_rango_prueba")   # [lo, hi] para restringir en pruebas de prod
        # Bajo IP_PUB_LOCK: elegir pública + crear NAT + grabar es atómico entre jobs
        # (tras crear_nat la IP queda visiblemente tomada en RouterData: NAT + entrada
        # deshabilitada). El re-chequeo interno de crear_nat sigue cubriendo actores
        # EXTERNOS al motor (altas manuales del NOC).
        with IP_PUB_LOCK:
            publica, _rech = ip_publica_libre(pub_lista, rango, job)
            crear_nat(ip, publica, pub_lista, fqdn, marca_cfg.get("nombre", marca), job)
            set_estado(nombre, "creando", publica=publica, pub_lista=pub_lista)
        # registrar ambas IPs en NetBox (IPAM vigente) — best effort, no bloquea
        desc_base = "%s - VPS %s (%s)" % (fqdn, marca_cfg.get("nombre", marca), nombre)
        nb_priv = netbox_ip_add(ip + "/24", fqdn, desc_base + " · NAT 1:1 a %s · alta vps-engine" % publica)
        nb_pub = netbox_ip_add(publica + "/32", fqdn, desc_base + " · NAT 1:1 a %s (RouterData) · alta vps-engine" % ip)
        if nb_priv or nb_pub:
            with DB_LOCK, db() as c:
                c.execute("UPDATE vms SET nb_priv_id=?, nb_pub_id=? WHERE nombre=?",
                          (nb_priv, nb_pub, nombre))
        job.detalle("pública %s ↔ privada %s (NAT 1:1 activo) · NetBox: %s"
                    % (publica, ip,
                       "ambas IPs registradas" if (nb_priv and nb_pub) else
                       "registro parcial/omitido (revisar IPAM)" if (nb_priv or nb_pub) else
                       "no disponible — registrar a mano"))
        # si la creación vino de WHMCS, rellenar la IP en la ficha AL INSTANTE (sin Sync manual)
        if whmcs_serviceid:
            whmcs_set_ip(whmcs_serviceid, publica, job)
    else:
        job.detalle("omitido (modo pruebas — la VM queda solo con IP privada)")

    # 13. cPanel
    job.paso()
    if cpanel_preinstalado:
        # ya hay NAT (paso anterior) → la VM tiene internet: activar licencia y verificar WHM
        if MGMT_PRIVKEY_PATH:
            try:
                cli = paramiko.SSHClient()
                cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                cli.connect(ip, username="root", key_filename=MGMT_PRIVKEY_PATH, timeout=15,
                            allow_agent=False, look_for_keys=False)
                _, out, _ = cli.exec_command(
                    "/usr/local/cpanel/scripts/mainipcheck >/dev/null 2>&1; "
                    "/usr/local/cpanel/cpkeyclt >/dev/null 2>&1; "
                    "/usr/local/cpanel/cpanel -V 2>/dev/null | head -1; "
                    "curl -sk -o /dev/null -w '%{http_code}' https://127.0.0.1:2087/ || true", timeout=180)
                res = out.read().decode().strip().splitlines()
                cli.close()
                ver = res[0] if res else "?"
                whm_http = res[-1] if len(res) > 1 else "?"
                job.detalle("cPanel %s PREINSTALADO — licencia solicitada con su IP; WHM responde (%s) en https://%s:2087"
                            % (ver, whm_http, publica or ip))
            except Exception as e:  # noqa: BLE001 — no bloquear por la activación
                job.detalle("cPanel PREINSTALADO (WHM en https://%s:2087); activación de licencia "
                            "quedó para el ciclo automático (%s)" % (publica or ip, str(e)[:80]))
        else:
            job.detalle("cPanel PREINSTALADO en la dorada (WHM en https://%s:2087)" % (publica or ip))
    elif instalar_cpanel and MGMT_PRIVKEY_PATH:
        cli = paramiko.SSHClient()
        cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        cli.connect(ip, username="root", key_filename=MGMT_PRIVKEY_PATH, timeout=15,
                    allow_agent=False, look_for_keys=False)
        cli.exec_command(
            "nohup sh -c 'cd /home && curl -o latest -L "
            "https://securedownloads.cpanel.net/latest && sh latest' "
            ">/root/cpanel-install.log 2>&1 & echo lanzado")
        t0 = time.time()
        while time.time() - t0 < 5400:  # hasta 90 min
            time.sleep(60)
            try:
                _, out, _ = cli.exec_command(
                    "grep -c 'Thank you for installing cPanel' /root/cpanel-install.log 2>/dev/null; "
                    "pgrep -f 'sh latest' >/dev/null && echo corriendo || echo terminado")
                res = out.read().decode().split()
            except Exception:
                cli.close()
                cli = paramiko.SSHClient()
                cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                cli.connect(ip, username="root", key_filename=MGMT_PRIVKEY_PATH, timeout=15,
                            allow_agent=False, look_for_keys=False)
                continue
            if res and res[0] != "0":
                job.detalle("cPanel instalado (WHM en https://%s:2087)" % ip)
                break
            if len(res) > 1 and res[1] == "terminado":
                raise RuntimeError("el instalador de cPanel terminó SIN mensaje de éxito — ver /root/cpanel-install.log en la VM")
            job.detalle("instalando cPanel… %d min (tarda 30-60)" % int((time.time() - t0) / 60))
        else:
            raise RuntimeError("cPanel no terminó en 90 min")
        cli.close()
    else:
        job.detalle("omitido (instalar_cpanel=%s)" % instalar_cpanel)

    # 14. securizar y entregar la llave al cliente (solo producción)
    #     Dos modos: BYO (el cliente trajo su pública — no se genera ni custodia
    #     nada) o gestionado (generamos par → bóveda → Bitwarden Send).
    job.paso()
    entrega = None
    byo_fp = None
    if modo == "produccion":
        if pubkey_cliente:
            byo_fp = securizar_byo(job, nombre, ip, pubkey_cliente)
        else:
            entrega = securizar_vps(job, nombre, ip, cliente, fqdn)
            if entrega:
                with DB_LOCK, db() as c:
                    c.execute("UPDATE vms SET vault_item=?, send_url=? WHERE nombre=?",
                              (entrega.get("item_id"), entrega.get("url"), nombre))
                job.detalle("ENTREGAR al cliente → %s%s" % (
                    entrega.get("url", ""),
                    (" · contraseña: " + entrega["password"]) if entrega.get("password") else ""))
    else:
        job.detalle("omitido (modo pruebas — la securización va en producción)")

    # 15. finalizar
    job.paso()
    set_estado(nombre, "activo")
    destino = ("pública %s → %s" % (publica, ip)) if publica else ip
    # datos de acceso para el panel del dashboard (demo)
    acceso_ip = publica or ip
    res = {"vm": nombre, "sabor": sabor_slug, "privada": ip, "usuario": "root",
           "acceso_ip": acceso_ip,
           "ssh_cmd": ("ssh -i TU_LLAVE_PRIVADA root@%s" if byo_fp else "ssh -i id_ed25519 root@%s") % acceso_ip,
           "publica": publica, "cpanel": bool(instalar_cpanel),
           "byo": bool(byo_fp), "byo_fp": byo_fp,
           "whm": ("https://%s:2087" % acceso_ip) if instalar_cpanel else None}
    if entrega and entrega.get("url"):
        res["send_url"] = entrega["url"]
        res["send_password"] = entrega.get("password")
        with DB_LOCK, db() as c:
            c.execute("UPDATE vms SET send_id=? WHERE nombre=?", (entrega.get("id"), nombre))
    job.set_resultado(res)
    job.ok("VPS %s activo (%s) — %s/%s%s" % (nombre, destino, marca, sabor_slug,
                                             " · llave BYO del cliente" if byo_fp else ""))

# ── FLUJOS: suspender / reanudar / eliminar / editar / purga ─────────────────
def set_bloqueo_publica(lista, publica, bloquear, job=None):
    """Suspensión por address-list (como lo hace el usuario a mano): en RouterData hay
    un drop `dst-address-list=<lista>` en raw. Habilitar la entrada = la IP entra al drop
    = BLOQUEADA (suspendida). Deshabilitarla = fuera del drop = pasa (activa). El comentario
    (cliente) se conserva siempre. La VM NO se toca — sigue corriendo."""
    disabled = "no" if bloquear else "yes"   # habilitada(no) = bloquea · deshabilitada(yes) = pasa
    ok, out = mikrotik('/ip firewall address-list set [find where list=%s and address="%s"] disabled=%s'
                       % (lista, publica, disabled))
    if not ok:
        raise RuntimeError("MikroTik no aplicó el %s de %s: %s"
                           % ("bloqueo" if bloquear else "desbloqueo", publica, out[:150]))
    # verificar el estado real de la entrada
    ok2, out2 = mikrotik('/ip firewall address-list print terse where list=%s and address="%s"'
                         % (lista, publica))
    esta_bloqueada = ok2 and not re.match(r"^\s*\d+\s+X", out2 or "")  # sin 'X' (no deshabilitada) = en el drop
    if bloquear and not esta_bloqueada:
        raise RuntimeError("la IP %s no quedó bloqueada tras el cambio" % publica)
    if not bloquear and esta_bloqueada:
        raise RuntimeError("la IP %s no quedó desbloqueada tras el cambio" % publica)
    if job:
        job.detalle("IP pública %s %s en %s (drop raw)"
                    % (publica, "BLOQUEADA" if bloquear else "desbloqueada", lista))


def flujo_suspender(job):
    vm = guardarraices(job.vm)
    job.paso()  # "Validar guardarraíles"
    if vm["estado"] == "suspendido":
        job.ok("VPS %s ya estaba suspendido" % job.vm)
        return
    if not (vm.get("publica") and vm.get("pub_lista")):
        raise RuntimeError("el VPS no tiene IP pública registrada — no se puede suspender por address-list")
    job.paso("VM %s validada (estado %s) — no se apaga, sigue corriendo" % (job.vm, vm["estado"]))  # → bloquear
    set_bloqueo_publica(vm["pub_lista"], vm["publica"], True, job)
    job.paso("acceso público cortado")  # → marcar
    set_estado(job.vm, "suspendido")
    job.ok("VPS %s SUSPENDIDO — IP pública %s bloqueada; la VM sigue corriendo (datos intactos)"
           % (job.vm, vm["publica"]))


def flujo_reanudar(job):
    vm = guardarraices(job.vm)
    job.paso()  # "Validar guardarraíles"
    if vm["estado"] != "suspendido":
        job.ok("VPS %s no estaba suspendido (estado %s) — nada que reanudar" % (job.vm, vm["estado"]))
        return
    if not (vm.get("publica") and vm.get("pub_lista")):
        raise RuntimeError("el VPS no tiene IP pública registrada")
    job.paso("VM %s validada" % job.vm)  # → desbloquear
    set_bloqueo_publica(vm["pub_lista"], vm["publica"], False, job)
    job.paso("acceso público restaurado")  # → marcar
    set_estado(job.vm, "activo")
    job.ok("VPS %s REANUDADO — IP pública %s desbloqueada, servicio restablecido" % (job.vm, vm["publica"]))

def flujo_eliminar(job):
    """Eliminación como SAGA re-ejecutable (#7): cada paso tolera 'ya está hecho',
    así un eliminar que abortó a medias (p.ej. RouterData caído en el paso NAT) se
    recupera simplemente REINTENTANDO la acción — sin cirugía manual. El estado
    'eliminando' queda visible en el registro/dashboard mientras corre o si aborta."""
    vm = guardarraices(job.vm)
    job.paso()
    set_estado(job.vm, "eliminando")
    job.paso("VM %s validada (registro + prefijo)" % job.vm)  # → apagar
    ps = power_state(job.vm)
    if ps == "?":
        job.detalle("la VM no aparece en el ESXi (probable reintento tras des-registro previo) — se omite apagar")
    elif ps != "poweredOff":
        apagar_graceful(job, job.vm)
    job.paso("apagada")  # → unregister
    try:
        govc("vm.unregister", job.vm)
    except RuntimeError as e:
        # no basta el texto del error: se CONFIRMA contra el inventario real
        if "not found" in str(e).lower() and power_state(job.vm) == "?":
            job.detalle("ya estaba des-registrada del ESXi (verificado en inventario) — sigo")
        else:
            raise
    job.paso("des-registrada del ESXi")  # → liberar NAT
    if vm.get("publica") and vm.get("pub_lista"):
        # #6: si el NAT no queda COMPROBADAMENTE revertido, borrar_nat lanza y la
        # eliminación ABORTA aquí (job en error, VM fuera de papelera, pública sin
        # liberar). Antes seguía a papelera con un "aviso" → NAT huérfano apuntando
        # a un VPS muerto y pública reasignable. Reintento: correr eliminar de nuevo
        # cuando RouterData esté sano (la saga idempotente completa es el fix #7).
        borrar_nat(vm["ip"], vm["publica"], vm["pub_lista"], job)
    else:
        job.detalle("sin IP pública que liberar")
    # limpiar el registro de IPs en NetBox (best-effort)
    nb1 = netbox_ip_del(vm.get("nb_priv_id"))
    nb2 = netbox_ip_del(vm.get("nb_pub_id"))
    if vm.get("nb_priv_id") or vm.get("nb_pub_id"):
        job.detalle((job.pasos[job._i]["detalle"] + " · NetBox: " +
                     ("IPs eliminadas del IPAM" if (nb1 or nb2) else "no se pudo limpiar — revisar")).strip(" ·"))
    job.paso("NAT liberado")  # → eliminar Send (política B: el link de entrega se va al borrar)
    if vm.get("send_id") and PROVISION_TOKEN:
        try:
            provision_post("/send/delete", {"send_id": vm["send_id"]})
            job.detalle("enlace de entrega (Send) eliminado")
        except Exception as e:  # noqa: BLE001
            job.detalle("aviso: no se pudo borrar el Send (%s)" % str(e)[:120])
    else:
        job.detalle("sin enlace de entrega que borrar")
    job.paso("enlace de entrega cerrado")  # → papelera (la llave de la bóveda se conserva hasta la purga)
    try:
        out = esxi_ssh("trash-vm %s" % job.vm)
        entrada = out.replace("OK", "").strip()
    except RuntimeError as e:
        if "no existe" not in str(e):
            raise
        # no basta el texto del error: se CONFIRMA el estado real en el datastore —
        # la carpeta no debe seguir en VPS/ y debe existir SU entrada en _papelera
        # (la movió un intento anterior; así se recupera la entrada REAL aunque aquel
        # intento muriera antes de registrarla)
        if job.vm in (esxi_ssh("list-vps") or "").split():
            raise RuntimeError("trash-vm dijo 'no existe' pero %s SIGUE en VPS/ — revisar a mano" % job.vm)
        entradas = [l.strip() for l in (esxi_ssh("list-trash") or "").splitlines()
                    if l.strip().endswith("-" + job.vm)]
        if not entradas:
            raise RuntimeError("la carpeta de %s no está en VPS/ ni en _papelera — revisar a mano" % job.vm)
        entrada = sorted(entradas)[-1]  # la más reciente (prefijo AAAAMMDD-HHMMSS ordena bien)
        job.detalle("carpeta ya movida por un intento anterior — entrada verificada en _papelera (%s)" % entrada)
    set_estado(job.vm, "papelera", papelera_entrada=entrada)
    job.ok("VPS %s en papelera (%s) — Send cerrado; la llave sigue en la bóveda hasta la purga (7 días)" % (job.vm, entrada))

def flujo_editar(job, sabor_slug):
    """Cambio de plan (decisión 2026-09-11, v2):
    - UPGRADE: CPU/RAM en caliente (hot-add) y el disco crece — sin corte.
    - DOWNGRADE: CPU/RAM bajan con un REINICIO BREVE (~1-2 min; VMware no permite
      quitar CPU/RAM en caliente). El disco NUNCA se achica (corrompería el
      filesystem del cliente): se mantiene el tamaño actual."""
    vm = guardarraices(job.vm)
    sabor = SABORES[(vm["marca"], sabor_slug)]

    # 1. calcular el cambio
    job.paso()
    if sabor_slug == vm["sabor"]:
        job.ok("la VM ya tiene el plan %s — nada que cambiar" % sabor_slug)
        return
    d_cpu = sabor["vcpu"] - vm["vcpu"]
    d_ram = sabor["ram_mb"] - vm["ram_mb"]
    crecer = sabor["disco_gb"] > vm["disco_gb"]
    disco_final = max(sabor["disco_gb"], vm["disco_gb"])
    cambios = []
    if d_cpu:
        cambios.append("vCPU %d→%d" % (vm["vcpu"], sabor["vcpu"]))
    if d_ram:
        cambios.append("RAM %d→%d MB" % (vm["ram_mb"], sabor["ram_mb"]))
    if crecer:
        cambios.append("disco %d→%d GB" % (vm["disco_gb"], sabor["disco_gb"]))
    elif sabor["disco_gb"] < vm["disco_gb"]:
        cambios.append("disco se MANTIENE en %d GB (achicarlo corrompería los datos)" % vm["disco_gb"])
    if not (d_cpu or d_ram or crecer):
        with DB_LOCK, db() as c:
            c.execute("UPDATE vms SET sabor=? WHERE nombre=?", (sabor_slug, job.vm))
        job.ok("plan actualizado a %s — %s" % (sabor_slug, "; ".join(cambios) or "recursos idénticos"))
        return
    requiere_reinicio = d_cpu < 0 or d_ram < 0
    tipo = "DOWNGRADE (requiere reinicio breve)" if requiere_reinicio else "UPGRADE en caliente"
    job.paso("%s: %s" % (tipo, ", ".join(cambios)))  # → aplicar CPU/RAM

    # 2. CPU/RAM: subir = hot-add sin corte; bajar = apagar→cambiar→encender
    if d_cpu or d_ram:
        if requiere_reinicio:
            encendida = power_state(job.vm) == "poweredOn"
            if encendida:
                job.detalle("apagando para bajar CPU/RAM (no existe hot-remove)…")
                apagar_graceful(job, job.vm)
            govc("vm.change", "-vm", job.vm, "-c", str(sabor["vcpu"]), "-m", str(sabor["ram_mb"]))
            if encendida:
                govc("vm.power", "-on", job.vm)
                job.detalle("CPU/RAM ajustados a la baja; VM encendida de nuevo (corte ~1-2 min)")
            else:
                job.detalle("CPU/RAM ajustados (la VM estaba apagada)")
        else:
            govc("vm.change", "-vm", job.vm, "-c", str(sabor["vcpu"]), "-m", str(sabor["ram_mb"]))
            job.detalle("CPU/RAM aplicados en caliente (hot-add), sin reinicio")
    else:
        job.detalle("CPU/RAM sin cambios")
    job.paso()  # → disco

    # 3. disco: intentar hot-extend por API; si el vCenter lo bloquea, ciclo breve
    #    apagar→crecer→encender. Luego expandir el filesystem del guest por SSH.
    if crecer:
        encendida = power_state(job.vm) == "poweredOn"
        hot_ok = False
        if encendida:
            try:
                govc("vm.disk.change", "-vm", job.vm, "-disk.filePath",
                     "[%s] VPS/%s/%s.vmdk" % (DATASTORE, job.vm, job.vm),
                     "-size", "%dG" % sabor["disco_gb"])
                hot_ok = True
                job.detalle("vmdk extendido EN CALIENTE a %d GB" % sabor["disco_gb"])
            except RuntimeError as e:
                if "managing it" in str(e) or "restricted" in str(e):
                    job.detalle("el vCenter bloquea el hot-extend vía host → crecimiento con "
                                "reinicio breve (con acceso al vCenter sería sin corte)")
                else:
                    raise
        if not hot_ok:
            if encendida:
                apagar_graceful(job, job.vm)
            esxi_ssh("grow-disk %s %d" % (job.vm, sabor["disco_gb"]))
            job.detalle("vmdk extendido a %d GB (VM apagada)" % sabor["disco_gb"])
            if encendida:
                govc("vm.power", "-on", job.vm)
                job.detalle("VM encendida de nuevo; esperando SSH para expandir el filesystem…")
        # expandir el FS del guest (con reintentos por si viene de un boot)
        if MGMT_PRIVKEY_PATH and vm.get("ip"):
            salida, err = [], None
            for intento in range(12):
                try:
                    cli = paramiko.SSHClient()
                    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                    cli.connect(vm["ip"], username="root", key_filename=MGMT_PRIVKEY_PATH,
                                timeout=10, allow_agent=False, look_for_keys=False)
                    script = ("echo 1 > /sys/class/block/sda/device/rescan; "
                              "PART=$(lsblk -no NAME,MOUNTPOINT /dev/sda | awk '$2==\"/\"{print $1}' | tr -dc '0-9'); "
                              "growpart /dev/sda ${PART:-3}; "
                              "xfs_growfs / 2>/dev/null || resize2fs /dev/sda${PART:-3} 2>/dev/null; "
                              "df -h / | tail -1")
                    _, out, _ = cli.exec_command(script, timeout=60)
                    salida = out.read().decode().strip().splitlines()
                    cli.close()
                    err = None
                    break
                except Exception as e:  # noqa: BLE001 — la VM puede estar arrancando
                    err = e
                    time.sleep(10)
            if err:
                job.detalle("vmdk crecido; no se pudo expandir el FS por SSH (%s) — se expandirá al próximo arranque"
                            % str(err)[:100])
            else:
                job.detalle("filesystem expandido: %s" % (salida[-1] if salida else "ok"))
        else:
            job.detalle("vmdk crecido; el guest expandirá el FS al próximo arranque")
    else:
        job.detalle("disco sin cambios")
    job.paso()  # → registrar

    # 4. registrar (el disco registrado es el REAL: nunca baja)
    set_estado(job.vm, vm["estado"], vcpu=sabor["vcpu"], ram_mb=sabor["ram_mb"], disco_gb=disco_final)
    with DB_LOCK, db() as c:
        c.execute("UPDATE vms SET sabor=? WHERE nombre=?", (sabor_slug, job.vm))
    cierre = "con un reinicio breve" if requiere_reinicio else "sin corte de servicio"
    job.ok("VPS %s cambiado a %s (%s) — %s" % (job.vm, sabor_slug, ", ".join(cambios), cierre))

def flujo_purgar(job):
    job.paso()
    out = esxi_ssh("purge-trash", timeout=600)
    purgadas = [l.split(":", 1)[1].strip() for l in out.splitlines() if l.startswith("purged:")]
    llaves_borradas = 0
    with DB_LOCK, db() as c:
        for entrada in purgadas:
            # política B: al purgar definitivo, borrar también la llave de la bóveda
            for row in c.execute("SELECT vault_item FROM vms WHERE papelera_entrada=?", (entrada,)):
                if row["vault_item"] and PROVISION_TOKEN:
                    try:
                        provision_post("/vault-borrar-item", {"item_id": row["vault_item"]})
                        llaves_borradas += 1
                    except Exception:  # noqa: BLE001 — no bloquear la purga por la bóveda
                        pass
            c.execute("DELETE FROM vms WHERE papelera_entrada=?", (entrada,))
    job.ok("papelera purgada: %d VPS (>7 días) · %d llaves eliminadas de la bóveda"
           % (len(purgadas), llaves_borradas))

# ── API ──────────────────────────────────────────────────────────────────────
def auth():
    """Devuelve el ROL del token: 'admin' (dashboard/NOC, todo permitido) o 'whmcs'
    (conector: solo crear/accion/editar por serviceid + consultas de su servicio),
    o None si no autoriza. Comparación en tiempo constante (hmac.compare_digest).
    Sin WHMCS_TOKEN configurado existe solo el rol admin (comportamiento previo).
    Los 'if not auth()' existentes siguen funcionando (str truthy / None falsy)."""
    tok = request.headers.get("X-Auth-Token") or ""
    if not tok:
        return None
    if hmac.compare_digest(tok, TOKEN):
        return "admin"
    if WHMCS_TOKEN and hmac.compare_digest(tok, WHMCS_TOKEN):
        return "whmcs"
    return None

ERR_SOLO_ADMIN = "operación reservada al token de administración (rol del token: whmcs)"

@app.route("/health")
def health():
    detalle = [{"marca": s["marca"], "slug": s["slug"], "nombre_web": s["nombre_web"],
                "vcpu": s["vcpu"], "ram_mb": s["ram_mb"], "disco_gb": s["disco_gb"],
                "activo": s.get("activo", True)} for s in SABORES.values()]
    detalle.sort(key=lambda s: (s["marca"], s["slug"]))
    return jsonify({"ok": True, "modo": MODO,
                    "marcas": sorted(MARCAS), "sabores": sorted("%s/%s" % k for k in SABORES),
                    "sabores_detalle": detalle})

@app.route("/crear", methods=["POST"])
def crear():
    rol = auth()
    if not rol:
        return jsonify({"error": "unauthorized"}), 401
    d = request.get_json(force=True)
    marca = (d.get("marca") or "").strip()
    sabor = (d.get("sabor") or "").strip()
    cliente = (d.get("cliente") or "").strip()
    hostname = (d.get("hostname") or "").strip().lower()
    actor = (d.get("actor") or "dashboard").strip()
    modo = (d.get("modo") or MODO).strip()   # pruebas | produccion (default = env)
    if rol == "whmcs":
        # #4: el conector siempre crea CON serviceid (es su única llave a la VM)
        if not d.get("whmcs_serviceid"):
            return jsonify({"error": "whmcs_serviceid es obligatorio para el token de WHMCS"}), 400
        # #5: el modo lo fija el motor (WHMCS_MODO), no el body del conector
        if modo != WHMCS_MODO:
            audit(actor, "crear", "-", "modo del body (%s) ignorado para rol whmcs — se usa WHMCS_MODO=%s"
                  % (modo, WHMCS_MODO), "aviso")
        modo = WHMCS_MODO
    if modo not in ("pruebas", "produccion"):
        return jsonify({"error": "modo inválido (pruebas | produccion)"}), 400
    if marca not in MARCAS:
        return jsonify({"error": "marca desconocida: %s" % marca}), 400
    if (marca, sabor) not in SABORES:
        return jsonify({"error": "sabor desconocido: %s/%s" % (marca, sabor)}), 400
    if not re.match(r"^[a-z0-9][a-z0-9.-]{1,60}$", hostname):
        return jsonify({"error": "hostname inválido (minúsculas, dígitos, puntos, guiones)"}), 400
    if not SABORES[(marca, sabor)].get("activo", True):
        return jsonify({"error": "el sabor %s no está activo" % sabor}), 400
    # cPanel: lo decide el PLAN (todos los de hosting.cl lo incluyen). El parámetro
    # explícito instalar_cpanel queda como override para API/pruebas.
    if "instalar_cpanel" in d:
        cpanel = bool(d["instalar_cpanel"])
    else:
        cpanel = (SABORES[(marca, sabor)].get("extras", {}).get("cpanel_licencia_cuentas", 0) or 0) > 0
    # BYO key (opcional): el cliente trae su llave PÚBLICA — no generamos ni custodiamos
    pubkey_cliente = (d.get("pubkey_cliente") or "").strip().replace("\r", "").replace("\n", " ").strip()
    if pubkey_cliente:
        if not PUBKEY_RE.match(pubkey_cliente):
            return jsonify({"error": "llave pública inválida — pega una línea tipo "
                                     "'ssh-ed25519 AAAA… comentario' (ed25519, rsa o ecdsa)"}), 400
        try:
            fingerprint_pubkey(pubkey_cliente)
        except RuntimeError as e:
            return jsonify({"error": str(e)}), 400
    # clave de root opcional (la genera WHMCS) → se aplica en la VM para WHM/consola
    root_password = (d.get("root_password") or "").strip() or None
    # id del servicio en WHMCS (para trackear la VM sin depender del Username)
    whmcs_serviceid = (str(d.get("whmcs_serviceid")).strip() if d.get("whmcs_serviceid") else None)
    nombre, job = None, None
    try:
        # reserva del nombre + creación del job BAJO EL MISMO GATE: sin esto hay una
        # ventana en que la fila 'creando' (con whmcs_serviceid) ya existe pero su job
        # no — un Terminate de WHMCS llegando justo ahí resolvería la VM por serviceid
        # y pasaría el gate, lanzando un eliminar sobre una VM a medio nacer
        with JOB_GATE_LOCK:
            nombre = reservar_vm(marca, MARCAS[marca], sabor, SABORES[(marca, sabor)],
                                 cliente, hostname, whmcs_serviceid)
            job = Job("crear", nombre, actor, PASOS_CREAR)
        run_job(job, lambda j: flujo_crear(j, marca, sabor, cliente, hostname, cpanel, modo,
                                           pubkey_cliente or None, root_password, whmcs_serviceid))
    except Exception as e:
        # deshacer lo que alcanzó a quedar: el job se marca error (libera el gate) y
        # la reserva se borra solo si sigue intacta — sin filas 'creando' huérfanas
        if job:
            job.fail("no se pudo lanzar el hilo de creación: %s" % e)
        if nombre:
            with DB_LOCK, db() as c:
                c.execute("DELETE FROM vms WHERE nombre=? AND estado='creando' AND ip IS NULL", (nombre,))
        audit(actor, "crear", nombre or "-", "creación no lanzada: %s" % e, "ERROR")
        return jsonify({"error": "no se pudo lanzar la creación (ver registro de operaciones)"}), 500
    return jsonify({"ok": True, "job_id": job.id, "vm": nombre, "modo": modo,
                    "byo": bool(pubkey_cliente)})

@app.route("/job/<jid>")
def job_get(jid):
    rol = auth()
    if not rol:
        return jsonify({"error": "unauthorized"}), 401
    with DB_LOCK, db() as c:
        row = c.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
    if not row:
        return jsonify({"error": "job no existe"}), 404
    d = dict(row)
    d["pasos"] = json.loads(d["pasos"])
    if rol != "admin":
        # el resultado puede traer credenciales de entrega (link+clave del Send) —
        # solo para el NOC; el módulo WHMCS únicamente lee estado/pasos (#14 parcial)
        d.pop("resultado", None)
    elif d.get("resultado"):
        try:
            d["resultado"] = json.loads(d["resultado"])
        except (ValueError, TypeError):
            d["resultado"] = None
    return jsonify(d)

@app.route("/jobs")
def jobs_list():
    rol = auth()
    if not rol:
        return jsonify({"error": "unauthorized"}), 401
    if rol != "admin":
        return jsonify({"error": ERR_SOLO_ADMIN}), 403
    with DB_LOCK, db() as c:
        rows = c.execute("SELECT id,tipo,vm,estado,error,created_at,updated_at "
                         "FROM jobs ORDER BY created_at DESC LIMIT 30").fetchall()
    return jsonify({"jobs": [dict(r) for r in rows]})

@app.route("/vms")
def vms_list():
    rol = auth()
    if not rol:
        return jsonify({"error": "unauthorized"}), 401
    if rol != "admin":
        return jsonify({"error": ERR_SOLO_ADMIN}), 403
    with DB_LOCK, db() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM vms WHERE estado != 'papelera' ORDER BY nombre").fetchall()]
        papelera = [dict(r) for r in c.execute(
            "SELECT nombre,papelera_entrada,updated_at FROM vms WHERE estado='papelera'").fetchall()]
    for r in rows:
        r["power"] = power_state(r["nombre"])
    return jsonify({"vms": rows, "papelera": papelera})

ACCIONES = {"suspender": (flujo_suspender, ["Validar guardarraíles", "Bloquear IP pública (address-list)", "Marcar suspendido"]),
            "reanudar": (flujo_reanudar, ["Validar guardarraíles", "Desbloquear IP pública", "Marcar activo"]),
            "eliminar": (flujo_eliminar, ["Validar guardarraíles", "Apagar", "Des-registrar del ESXi", "Liberar IP pública y NAT", "Cerrar enlace de entrega (Send)", "Mover a papelera"])}

def vm_por_serviceid(sid):
    """Resuelve el nombre de la VM activa asociada a un serviceid de WHMCS (para poder
    suspender/eliminar sin depender del Username de la ficha)."""
    if not sid:
        return None
    with DB_LOCK, db() as c:
        row = c.execute("SELECT nombre FROM vms WHERE whmcs_serviceid=? AND estado != 'papelera' "
                        "ORDER BY created_at DESC LIMIT 1", (str(sid),)).fetchone()
    return row["nombre"] if row else None

@app.route("/vm-por-servicio")
def vm_por_servicio_get():
    """Consulta LIVIANA (solo BD, sin tocar el ESXi) de la VM de un serviceid de WHMCS:
    devuelve estado, IP privada y pública. Para que el módulo pueble la ficha rápido."""
    if not auth():
        return jsonify({"error": "unauthorized"}), 401
    sid = (request.args.get("serviceid") or "").strip()
    if not sid:
        return jsonify({"error": "falta serviceid"}), 400
    with DB_LOCK, db() as c:
        row = c.execute("SELECT nombre,estado,ip,publica,hostname FROM vms WHERE whmcs_serviceid=? "
                        "ORDER BY created_at DESC LIMIT 1", (str(sid),)).fetchone()
    if not row:
        return jsonify({"ok": True, "encontrada": False})
    d = dict(row)
    d.update({"ok": True, "encontrada": True})
    return jsonify(d)

@app.route("/accion", methods=["POST"])
def accion():
    rol = auth()
    if not rol:
        return jsonify({"error": "unauthorized"}), 401
    d = request.get_json(force=True)
    vm, acc = (d.get("vm") or "").strip(), (d.get("accion") or "").strip()
    serviceid = (str(d.get("serviceid")).strip() if d.get("serviceid") else "")
    actor = (d.get("actor") or "dashboard").strip()
    if acc not in ACCIONES:
        return jsonify({"error": "acción desconocida"}), 400
    # #4: el rol whmcs opera SOLO por serviceid — la VM se resuelve en el servidor,
    # así el conector no puede tocar VMs ajenas (pertenencia por construcción)
    if rol == "whmcs":
        if vm:
            return jsonify({"error": "el token de WHMCS opera solo por serviceid (vm explícito no permitido)"}), 403
        if not serviceid:
            return jsonify({"error": "falta serviceid"}), 400
    # caso WHMCS: si no vino el nombre, se resuelve por el serviceid
    por_sid = False
    if not vm and serviceid:
        vm = vm_por_serviceid(serviceid) or ""
        por_sid = True
        if not vm:
            return jsonify({"error": "no hay VM activa para el serviceid %s" % serviceid}), 404
    try:
        reg = guardarraices(vm)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 400
    # una VM a medio eliminar SOLO acepta reintentar el eliminar (#7; la matriz
    # completa de estados por operación es el hallazgo #17)
    if reg.get("estado") == "eliminando" and acc != "eliminar":
        return jsonify({"error": "la VM %s está a medio eliminar — solo se permite reintentar 'eliminar'" % vm}), 409
    # eliminar exige confirmación = nombre de la VM (o el serviceid si se identificó por él)
    if acc == "eliminar":
        conf = d.get("confirmacion")
        if not (conf == vm or (por_sid and conf == serviceid)):
            return jsonify({"error": "confirmación requerida: reescribe el nombre exacto de la VM"}), 400
    fn, pasos = ACCIONES[acc]
    job, activo = lanzar_job_exclusivo(acc, vm, actor, pasos)
    if not job:
        return jsonify({"error": "la VM %s tiene un job en curso (%s, id %s) — reintenta cuando termine"
                        % (vm, activo["tipo"], activo["id"])}), 409
    err = lanzar_o_fallar(job, fn)
    if err:
        return err
    return jsonify({"ok": True, "job_id": job.id, "vm": vm})

@app.route("/editar", methods=["POST"])
def editar():
    rol = auth()
    if not rol:
        return jsonify({"error": "unauthorized"}), 401
    d = request.get_json(force=True)
    vm, sabor = (d.get("vm") or "").strip(), (d.get("sabor") or "").strip()
    serviceid = (str(d.get("serviceid")).strip() if d.get("serviceid") else "")
    actor = (d.get("actor") or "dashboard").strip()
    if rol == "whmcs":
        if vm:
            return jsonify({"error": "el token de WHMCS opera solo por serviceid (vm explícito no permitido)"}), 403
        if not serviceid:
            return jsonify({"error": "falta serviceid"}), 400
    if not vm and serviceid:
        vm = vm_por_serviceid(serviceid) or ""
        if not vm:
            return jsonify({"error": "no hay VM activa para el serviceid %s" % serviceid}), 404
    try:
        reg = guardarraices(vm)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 400
    if reg.get("estado") == "eliminando":
        return jsonify({"error": "la VM %s está a medio eliminar — solo se permite reintentar 'eliminar'" % vm}), 409
    if (reg["marca"], sabor) not in SABORES:
        return jsonify({"error": "sabor desconocido para la marca %s" % reg["marca"]}), 400
    job, activo = lanzar_job_exclusivo("editar", vm, actor,
              ["Calcular cambio de plan (upgrade/downgrade)", "Aplicar CPU/RAM",
               "Ajustar disco (solo crece, nunca se achica)", "Actualizar registro"])
    if not job:
        return jsonify({"error": "la VM %s tiene un job en curso (%s, id %s) — reintenta cuando termine"
                        % (vm, activo["tipo"], activo["id"])}), 409
    err = lanzar_o_fallar(job, lambda j: flujo_editar(j, sabor))
    if err:
        return err
    return jsonify({"ok": True, "job_id": job.id})

@app.route("/purgar-papelera", methods=["POST"])
def purgar():
    rol = auth()
    if not rol:
        return jsonify({"error": "unauthorized"}), 401
    if rol != "admin":
        return jsonify({"error": ERR_SOLO_ADMIN}), 403
    job, activo = lanzar_job_exclusivo("purgar-papelera", "-", "timer", ["Purgar entradas >7 días"])
    if not job:
        # purga ya corriendo → idempotente: se devuelve el job existente, sin error
        return jsonify({"ok": True, "job_id": activo["id"], "nota": "purga ya en curso"})
    err = lanzar_o_fallar(job, flujo_purgar)
    if err:
        return err
    return jsonify({"ok": True, "job_id": job.id})

@app.route("/auditoria")
def auditoria():
    rol = auth()
    if not rol:
        return jsonify({"error": "unauthorized"}), 401
    if rol != "admin":
        return jsonify({"error": ERR_SOLO_ADMIN}), 403
    with DB_LOCK, db() as c:
        rows = c.execute("SELECT * FROM operaciones ORDER BY id DESC LIMIT 100").fetchall()
    return jsonify({"operaciones": [dict(r) for r in rows]})

init_db()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8224, threaded=True)
