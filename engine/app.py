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
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import socket
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
# #9: pinning de host keys de la INFRAESTRUCTURA FIJA (ESXi, MikroTiks). El deploy
# genera este archivo con ssh-keyscan (ver noc-monitor/); si falta o la huella no
# calza → la conexión se RECHAZA (fail-closed, anti-MITM). Las conexiones a VPS
# recién creados quedan en TOFU deliberado: su llave nace con la VM (es imposible
# pre-conocerla) y sus IPs se REUTILIZAN (un known_hosts las haría chocar).
KNOWN_HOSTS_PATH = os.environ.get("KNOWN_HOSTS_PATH", "/keys/known_hosts")
# ── Fase D (wizard de enrolamiento): artefactos que el motor ESCRIBE ──────────
# /keys es un volumen READ-ONLY (llaves fijas del deploy); lo que el wizard genera
# (llave por host, huellas pinneadas tras confirmación humana, passwords svc-vps)
# vive bajo /data (rw, mismo volumen 700-root que la BD del registro).
ENROL_DIR = os.environ.get("ENROL_DIR", "/data/enrolamiento")
ENROL_KEYS_DIR = os.path.join(ENROL_DIR, "keys")
KNOWN_HOSTS_DATA = os.path.join(ENROL_DIR, "known_hosts")
SECRETOS_HOSTS_PATH = os.path.join(ENROL_DIR, "secretos.env")
WRAPPER_TEMPLATE = os.environ.get("WRAPPER_TEMPLATE", "/app/config/vps-wrapper.sh")
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
# Límites de recursos del host para VPS de clientes (#8 / SEGURIDAD.md §Límites:
# proteger la infraestructura que comparte el host; 0 = sin límite). El cupo se
# verifica ATÓMICO con la reserva del nombre (mismo lock) → sin sobreventa entre
# creaciones concurrentes. Valores por defecto = propuesta de SEGURIDAD.md.
HOST_MAX_VCPU = int(os.environ.get("HOST_MAX_VCPU", "24"))
HOST_MAX_RAM_MB = int(os.environ.get("HOST_MAX_RAM_MB", "65536"))
HOST_MAX_DISCO_GB = int(os.environ.get("HOST_MAX_DISCO_GB", "600"))
# Reserva de seguridad del datastore: una creación aborta si al datastore le quedan
# menos de (disco del plan + esta reserva) GB libres — colchón para la infra.
DATASTORE_RESERVA_GB = int(os.environ.get("DATASTORE_RESERVA_GB", "50"))
# #14: los secretos de entrega (link+clave del Send) se REDACTAN del resultado de los
# jobs cuando el Send ya expiró (vive ~2 días) — barrido en la mantención diaria.
SEND_SECRETO_TTL_DIAS = int(os.environ.get("SEND_SECRETO_TTL_DIAS", "2"))
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
# #15: tope duro del body (nuestros payloads reales son < 8 KB; 64 KB da holgura)
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024

@app.errorhandler(413)
def _err_413(e):
    return jsonify({"error": "body demasiado grande (máx 64 KB)"}), 413

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
          hostname TEXT, ip TEXT, estado TEXT,          -- creando|activo|suspendido|eliminando|papelera|purgando|error
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
                          ("whmcs_serviceid", "TEXT"), ("host", "TEXT")):
            try:
                c.execute("ALTER TABLE vms ADD COLUMN %s %s" % (col, decl))
            except sqlite3.OperationalError as e:
                # #21: SOLO la columna duplicada es esperable; una BD bloqueada o
                # corrupta debe ABORTAR el arranque, no dejar el esquema a medias
                if "duplicate column name" not in str(e).lower():
                    raise
        try:
            c.execute("ALTER TABLE jobs ADD COLUMN resultado TEXT")  # datos de acceso
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e).lower():
                raise
        # jobs 'corriendo' huérfanos de un proceso anterior (reinicio del motor): sus
        # threads ya no existen → error, para que no bloqueen el gate de 1-job-por-VM
        # ni queden girando eternamente en el dashboard
        c.execute("UPDATE jobs SET estado='error', error='interrumpido por reinicio del motor', "
                  "updated_at=datetime('now','localtime') WHERE estado='corriendo'")
        # ── MULTI-HOST Fase A1 (2026-09-17) ──────────────────────────────────
        # Registro de hosts VMware donde el motor puede actuar. Las CREDENCIALES
        # nunca van aquí: api/ssh referencian rutas y NOMBRES de env vars del
        # engine.env (pass_env). Los límites NULL heredan los HOST_MAX_* globales.
        c.executescript("""
        CREATE TABLE IF NOT EXISTS hosts(
          id TEXT PRIMARY KEY,               -- slug: esxi-245
          ip TEXT UNIQUE NOT NULL,
          api_url TEXT,                      -- GOVC_URL de este host
          govc_user TEXT,                    -- usuario API (svc-vps del host)
          pass_env TEXT,                     -- NOMBRE de la env var con su password
          datastore TEXT,
          ssh_port INTEGER DEFAULT 22,
          ssh_key TEXT,                      -- llave del wrapper de ESTE host
          estado TEXT DEFAULT 'activo',      -- activo | pausado
          prioridad INTEGER DEFAULT 100,     -- menor = preferido
          max_vcpu INTEGER, max_ram_mb INTEGER, max_disco_gb INTEGER,
          notas TEXT,
          created_at TEXT DEFAULT (datetime('now','localtime')),
          updated_at TEXT DEFAULT (datetime('now','localtime')));
        """)
        # bootstrap: el host actual de las env se auto-registra como principal y ADOPTA
        # las VMs previas — SOLO en la migración inicial (tabla hosts vacía). La
        # adopción se hace UNA vez, al host legacy IDENTIFICADO por ESXI_HOST, dentro
        # de este mismo bloque (nunca en cada arranque ni "al de mejor prioridad": eso
        # reasignaría una fila host=NULL al host equivocado y anularía la protección de
        # host_de_vm — hallazgo Codex A1 #1). api_url se DERIVA de ESXI_HOST (no del
        # GOVC_URL literal, que podría traer credenciales embebidas — #3b).
        # marca de migración PERSISTENTE (PRAGMA user_version): la adopción legacy corre
        # UNA sola vez en la vida de la BD — no basta "tabla hosts vacía" (podría
        # re-vaciarse). user_version 0 = BD previa al multi-host → migrar; ≥1 = ya migrada.
        migrada = c.execute("PRAGMA user_version").fetchone()[0]
        if not migrada and os.environ.get("ESXI_HOST"):
            esxi_ip = os.environ["ESXI_HOST"]
            # el host legacy se identifica por IP (no por slug, que podría colisionar
            # con otra IP — hallazgo Codex A1 ronda3 #1). Si ya existe por IP, se reusa
            # su id; si no, se crea con slug único.
            fila = c.execute("SELECT id FROM hosts WHERE ip=?", (esxi_ip,)).fetchone()
            if fila:
                hid = fila["id"]
            else:
                base_slug = "esxi-" + esxi_ip.split(".")[-1]
                hid = base_slug
                if c.execute("SELECT 1 FROM hosts WHERE id=?", (hid,)).fetchone():
                    hid = "esxi-" + esxi_ip.replace(".", "-")   # slug por IP completa
                    n = 1                                        # y si AÚN colisiona, sufijo
                    while c.execute("SELECT 1 FROM hosts WHERE id=?", (hid,)).fetchone():
                        n += 1
                        hid = "esxi-%s-%d" % (esxi_ip.replace(".", "-"), n)
                c.execute("INSERT INTO hosts(id, ip, api_url, govc_user, pass_env, datastore, "
                          "ssh_port, ssh_key, notas) VALUES(?,?,?,?,?,?,?,?,?)",
                          (hid, esxi_ip, "https://%s/sdk" % esxi_ip,
                           os.environ.get("GOVC_USERNAME", "svc-vps"), "GOVC_PASSWORD",
                           os.environ.get("GOVC_DATASTORE", "DiscoA37245"),
                           int(os.environ.get("ESXI_SSH_PORT", "22")),
                           os.environ.get("ESXI_SSH_KEY", "/keys/vps_engine_esxi"),
                           "host principal (bootstrap desde engine.env)"))
            # adopción única de las filas legacy hacia ESTE host (no por prioridad)
            c.execute("UPDATE vms SET host=? WHERE host IS NULL", (hid,))
            c.execute("PRAGMA user_version=1")   # migración completada — no se repite
            # todo esto ocurre dentro del mismo with DB_LOCK/db (transacción única)
        # en arranques posteriores NO se reparan los host=NULL: si aparece alguno,
        # host_de_vm() lo rechaza y la reconciliación lo denuncia.

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

CREDS_URL_RE = re.compile(r"//[^/@\s]*@")   # //user:pass@  en una URL
# variables de sistema que el subprocess govc necesita (allowlist; el resto NO se hereda
# al ejecutar contra un host explícito — ver govc()). GOVC_BIN se resuelve por la ruta
# absoluta GOVC, no por env.
GOVC_ENV_SISTEMA = ("PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TMPDIR", "TMP",
                    "TEMP", "SSL_CERT_FILE", "SSL_CERT_DIR", "USER", "LOGNAME")

def _redact(txt):
    """Redacta credenciales embebidas en URLs de un texto de error antes de exponerlo
    a HTTP/logs (#3a Codex A1): //user:pass@host → //***@host."""
    return CREDS_URL_RE.sub("//***@", txt or "")

# ── Acceso al ESXi: govc (API, usuario svc-vps) y SSH restringido (wrapper) ──
def govc(*args, timeout=120, host=None):
    """govc contra el host por defecto (env del contenedor) o contra un host del
    registro multi-host (dict de la tabla hosts). Con host: se EXIGE su configuración
    completa (api_url/govc_user/datastore/pass_env) y que el secreto exista — NUNCA se
    heredan los GOVC_* globales (una op de B jamás debe caer al A por un campo vacío —
    hallazgo Codex A1 #2). El env del subprocess se construye sobreescribiendo TODOS
    los GOVC_* con los del host."""
    # secreto por defecto = el password global (host=None opera el host principal por
    # env): así el filtrado literal del error cubre AMBAS ramas (Codex A1 ronda4 obs.2)
    env, secreto = None, os.environ.get("GOVC_PASSWORD")
    if host is not None:
        faltan = [k for k in ("api_url", "govc_user", "datastore", "pass_env") if not host.get(k)]
        if faltan:
            raise RuntimeError("host %s con configuración incompleta (faltan %s) — no se opera"
                               % (host.get("id"), ", ".join(faltan)))
        secreto = secreto_host(host["pass_env"])
        if not secreto:
            raise RuntimeError("host %s: la variable %s no está en el entorno del motor "
                               "ni en su almacén de secretos" % (host["id"], host["pass_env"]))
        # entorno por ALLOWLIST (no denylist): SOLO las variables de sistema que el
        # subprocess govc necesita + los GOVC_* de ESTE host. Así ningún secreto del
        # motor ni de otros hosts (incluido un pass_env con nombre arbitrario) llega al
        # subprocess — hallazgo Codex A1 ronda3 #2.
        env = {k: os.environ[k] for k in GOVC_ENV_SISTEMA if k in os.environ}
        env["GOVC_URL"] = host["api_url"]
        env["GOVC_USERNAME"] = host["govc_user"]
        env["GOVC_PASSWORD"] = secreto
        env["GOVC_DATASTORE"] = host["datastore"]
        # política TLS: se respeta la del entorno (default 1 = ESXi autofirmado); NO se
        # fuerza a 1 para no desactivar la verificación donde ya estaba activa (#1 ronda2)
        env["GOVC_INSECURE"] = os.environ.get("GOVC_INSECURE", "1")
    r = subprocess.run([GOVC, *args], capture_output=True, text=True, timeout=timeout, env=env)
    if r.returncode:
        msg = (r.stderr or r.stdout).strip()
        if secreto:                       # #3a: quitar el valor LITERAL del secreto PRIMERO,
            msg = msg.replace(secreto, "***")   # luego redactar URLs con credenciales embebidas
        raise RuntimeError("govc %s: %s" % (args[0], _redact(msg)[:300]))
    return r.stdout.strip()

# ── Multi-host (Fase A1): registro de hosts y scan de recursos ───────────────
def host_get(hid):
    with DB_LOCK, db() as c:
        r = c.execute("SELECT * FROM hosts WHERE id=?", (hid,)).fetchone()
    return dict(r) if r else None

def host_principal():
    """El host ACTIVO de mejor prioridad (menor número) — lo administra el usuario
    desde la gestión (A2: la selección NO es automática por capacidad; si el
    preferido no da, la creación falla con mensaje claro y el humano decide)."""
    with DB_LOCK, db() as c:
        r = c.execute("SELECT * FROM hosts WHERE estado='activo' "
                      "ORDER BY prioridad, created_at LIMIT 1").fetchone()
    if not r:
        raise RuntimeError("no hay hosts ACTIVOS en el registro — revisar GET /hosts")
    return dict(r)

def host_de_vm(nombre):
    """Host donde VIVE una VM (columna vms.host). FALLA explícito si la fila no tiene
    host o si apunta a un host que ya no está en el registro — NUNCA cae al principal:
    resolver mal el host de una VM EXISTENTE llevaría una operación destructiva
    (apagar/eliminar/editar) al host equivocado (hallazgo Codex A2 #3). El bootstrap
    A1 adoptó todas las filas previas, así que 'sin host' aquí = anomalía a revisar."""
    with DB_LOCK, db() as c:
        r = c.execute("SELECT host FROM vms WHERE nombre=?", (nombre,)).fetchone()
    if not r or not r["host"]:
        raise RuntimeError("la VM %s no tiene host asociado en el registro — revisar a mano "
                           "(no se opera para no tocar el host equivocado)" % nombre)
    h = host_get(r["host"])
    if not h:
        raise RuntimeError("la VM %s apunta al host '%s' que ya no está en el registro — "
                           "revisar a mano" % (nombre, r["host"]))
    return h

def hosts_lista():
    with DB_LOCK, db() as c:
        return [dict(r) for r in c.execute("SELECT * FROM hosts ORDER BY prioridad, created_at")]

SCAN_TIMEOUT = int(os.environ.get("HOST_SCAN_TIMEOUT", "15"))   # #4: presupuesto corto por consulta

def _lim(valor_host, global_):
    """Límite efectivo: el del host si no es None (0 = sin límite, como validar_cupo);
    si el host no define, hereda el global (0/None → None = sin límite) — #5 Codex A1."""
    v = valor_host if valor_host is not None else global_
    return v or None

def host_recursos(h):
    """Scan EN VIVO de un host: datastore libre, CPU/RAM del fierro y lo comprometido
    por el motor en ese host. Cualquier falla → alcanzable=False (no lanza). Timeout
    corto por consulta para que un host lento no bloquee el listado (#4)."""
    out = {"alcanzable": False, "datastore_libre_gb": None, "cpu_cores": None,
           "mem_total_gb": None, "mem_uso_gb": None}
    try:
        raw = govc("datastore.info", "-json", h["datastore"], host=h, timeout=SCAN_TIMEOUT)
        data = json.loads(raw)
        for ds in (data.get("datastores") or data.get("Datastores") or []):
            s = ds.get("summary") or ds.get("Summary") or {}
            if (s.get("name") or s.get("Name")) == h["datastore"]:
                libre = s.get("freeSpace", s.get("FreeSpace"))
                if libre is not None:
                    out["datastore_libre_gb"] = int(libre) // (1024 ** 3)
        raw = govc("host.info", "-json", host=h, timeout=SCAN_TIMEOUT)
        data = json.loads(raw)
        hs = (data.get("hostSystems") or data.get("HostSystems") or [{}])[0]
        summ = hs.get("summary") or hs.get("Summary") or {}
        hw = summ.get("hardware") or summ.get("Hardware") or {}
        qs = summ.get("quickStats") or summ.get("QuickStats") or {}
        cores = hw.get("numCpuCores", hw.get("NumCpuCores"))
        if cores is not None:
            out["cpu_cores"] = cores
        mem = hw.get("memorySize", hw.get("MemorySize"))
        if mem is not None:
            out["mem_total_gb"] = int(mem) // (1024 ** 3)
        uso = qs.get("overallMemoryUsage", qs.get("OverallMemoryUsage"))
        if uso is not None:                    # #5: 0 es válido (host recién arrancado)
            out["mem_uso_gb"] = int(uso) // 1024   # viene en MB
        out["alcanzable"] = out["datastore_libre_gb"] is not None
    except Exception as e:  # noqa: BLE001 — el scan informa, no rompe
        out["error"] = _redact(str(e))[:120]   # #3a: sin credenciales embebidas
    try:
        with DB_LOCK, db() as c:
            r = c.execute("SELECT COALESCE(SUM(vcpu),0) v, COALESCE(SUM(ram_mb),0) m, "
                          "COALESCE(SUM(disco_gb),0) d, COUNT(*) n FROM vms "
                          "WHERE host=? AND estado NOT IN ('papelera','purgando')", (h["id"],)).fetchone()
        out["comprometido"] = {"vcpu": r["v"], "ram_mb": r["m"], "disco_gb": r["d"], "vms": r["n"]}
    except Exception as e:  # noqa: BLE001 — el scan nunca lanza; pero no inventa 0
        # comprometido DESCONOCIDO (no 0 ficticio, que aparentaría capacidad libre)
        out["comprometido"] = None
        out["error_inventario"] = _redact(str(e))[:120]
    out["limites"] = {"vcpu": _lim(h.get("max_vcpu"), HOST_MAX_VCPU),
                      "ram_mb": _lim(h.get("max_ram_mb"), HOST_MAX_RAM_MB),
                      "disco_gb": _lim(h.get("max_disco_gb"), HOST_MAX_DISCO_GB)}
    return out

def cliente_ssh_pinned():
    """Cliente paramiko con host key PINNED (#9): carga KNOWN_HOSTS_PATH y RECHAZA
    hosts desconocidos o con huella distinta (RejectPolicy). Fail-closed: sin el
    archivo no hay conexión a la infraestructura fija."""
    if not os.path.isfile(KNOWN_HOSTS_PATH):
        raise RuntimeError("falta %s (pinning de host keys #9) — generar con ssh-keyscan "
                           "en el deploy (ver noc-monitor/known-hosts.sh)" % KNOWN_HOSTS_PATH)
    cli = paramiko.SSHClient()
    cli.load_host_keys(KNOWN_HOSTS_PATH)
    # los hosts enrolados por el wizard (Fase D) tienen su huella —confirmada por
    # un humano— en el known_hosts escribible de /data; ambos archivos pinnean
    if os.path.isfile(KNOWN_HOSTS_DATA):
        cli.load_system_host_keys(KNOWN_HOSTS_DATA)
    cli.set_missing_host_key_policy(paramiko.RejectPolicy())
    return cli

def esxi_ssh(comando, timeout=300, host=None):
    """Ejecuta un subcomando del wrapper restringido EN EL HOST indicado (dict de la
    tabla hosts; None = host principal). El authorized_keys fuerza command= → lo que
    enviamos llega como SSH_ORIGINAL_COMMAND al wrapper de ESE host, con SU llave.
    La sesión autentica como root (vmkfstools/mv del datastore lo exigen en ESXi)
    pero la llave NO da shell: command= + no-pty + no-forwarding (capa 4 de
    SEGURIDAD.md) — el confinamiento es el forced-command. Host key PINNED (#9)."""
    h = host or host_principal()
    cli = cliente_ssh_pinned()
    cli.connect(h["ip"], port=int(h.get("ssh_port") or 22), username="root",
                key_filename=h.get("ssh_key") or ESXI_SSH_KEY, timeout=20,
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
    except Exception:  # noqa: BLE001 — #23: capa EXTRA de detección (red de pruebas);
        pass           # si falla, ping + registro siguen protegiendo la elección
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
    """Corre un comando en el MikroTik por SSH (mismo acceso claude@2420 del NOC).
    #9: host key PINNED con confianza EXCLUSIVA al KNOWN_HOSTS_PATH del motor —
    `-F /dev/null` ignora cualquier config heredada (~/.ssh/config, KnownHostsCommand)
    y GlobalKnownHostsFile=/dev/null anula /etc/ssh/ssh_known_hosts: así una huella
    ausente de nuestro archivo NO puede aceptarse por otra fuente (Codex #9 obs.2)."""
    host = host or ROUTERDATA
    r = subprocess.run(
        ["ssh", "-F", "/dev/null", "-i", MIKROTIK_KEY,
         "-o", "UserKnownHostsFile=%s" % KNOWN_HOSTS_PATH,
         "-o", "GlobalKnownHostsFile=/dev/null",
         "-o", "StrictHostKeyChecking=yes", "-o", "BatchMode=yes",
         "-o", "LogLevel=ERROR", "-o", "ConnectTimeout=10", "-p", MIKROTIK_PORT,
         "%s@%s" % (MIKROTIK_USER, host), cmd],
        capture_output=True, text=True, timeout=timeout)
    # ante fallo se conserva stderr (ahí informa SSH el rechazo de host key) — #9 obs.5
    return r.returncode == 0, (r.stdout if r.returncode == 0 else (r.stderr or r.stdout))


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
      - la entrada de la address-list se comenta '<host> - VPS <marca>' y se deshabilita.

    ⚠️ EL FORMATO DEL COMENTARIO ES UN CONTRATO — NO CAMBIARLO (decisión Alcadio
    2026-09-17, cierre del hallazgo #22): el Monitoreo_externo (fastnetmon/
    Monitoreo_externo/app/server.js, parseNocComment) sincroniza cada 5 min los
    srcnat que empiezan con '[NOC]' y toma como GRUPO lo que va tras la ÚLTIMA COMA
    → cualquier sufijo/tag rompería la agrupación del monitoreo automático de los
    VPS. El dstnat va SIN comentario a propósito (estética de agrupación en la
    tabla). La identificación de reglas propias se resuelve por el par exacto de
    IPs (#6) + address-list vigilada por la reconciliación (#22 parcial)."""
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
    traiga $, backticks, etc. Lanza RuntimeError si la instalación falla.

    TOFU DELIBERADO (a diferencia de la infra fija con pinning #9): la llave del VPS
    nace con la VM y sus IPs privadas se REUTILIZAN entre altas/bajas, así que un
    known_hosts permanente por IP chocaría. LÍMITE ACEPTADO (Codex #9 obs.4): un MITM
    en la red de VPS, o una IP mal asignada, podría dirigir esta operación a otra VM.
    Mitigado por: la IP privada se elige y verifica libre en RouterData bajo IP_PRIV_LOCK
    (#2) antes de asignarla, y esta llave es la de gestión (no un secreto del cliente).
    OJO: set_root_password sí envía un secreto del cliente por este canal — su riesgo
    residual es el mismo modelo TOFU, aceptado para el aprovisionamiento aislado."""
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

def cupo_comprometido(host_id=None):
    """Recursos ya comprometidos con clientes EN UN HOST (o global si None): filas
    vigentes del registro (papelera/purgando no cuentan; 'eliminando' aún ocupa)."""
    q = "SELECT COALESCE(SUM(vcpu),0) v, COALESCE(SUM(ram_mb),0) m, " \
        "COALESCE(SUM(disco_gb),0) d FROM vms WHERE estado NOT IN ('papelera','purgando')"
    args = ()
    if host_id:
        q += " AND host=?"
        args = (host_id,)
    with DB_LOCK, db() as c:
        r = c.execute(q, args).fetchone()
    return r["v"], r["m"], r["d"]

def validar_cupo(d_vcpu, d_ram_mb, d_disco_gb, host=None):
    """#8 (per-host desde A2): lanza RuntimeError si (comprometido del host + delta)
    excede sus límites (columnas max_* del host; NULL hereda los HOST_MAX_* globales;
    0 = sin límite). Solo se chequean las dimensiones que aumentan → downgrades pasan.

    MODELO DE CAPACIDAD (validado con Codex): el cupo lógico es la barrera ATÓMICA
    ENTRE CREACIONES (bajo NOMBRE_LOCK, contando el disco PROVISIONADO de las filas
    vigentes) — NO pretende impedir toda sobreventa del host. Para que garantice que
    no se sobre-provisiona el datastore, el operador debe: (a) fijar un límite de
    disco finito y ≤ capacidad ÚTIL del datastore (descontando la dorada, overhead,
    snapshots y otros archivos que no cuenta disco_gb); (b) si varios hosts comparten
    datastore, que la SUMA de sus presupuestos quepa en esa capacidad. El chequeo
    físico de datastore (paso 3) es salvaguarda fail-closed contra consumo externo.
    LÍMITE CONOCIDO: la carrera editar-vs-crear no está cubierta (NOMBRE_LOCK solo
    coordina creaciones; editar es admin, infrecuente) — pendiente de endurecer."""
    hid = (host or {}).get("id")
    lim_v = (host or {}).get("max_vcpu") if (host or {}).get("max_vcpu") is not None else HOST_MAX_VCPU
    lim_m = (host or {}).get("max_ram_mb") if (host or {}).get("max_ram_mb") is not None else HOST_MAX_RAM_MB
    lim_d = (host or {}).get("max_disco_gb") if (host or {}).get("max_disco_gb") is not None else HOST_MAX_DISCO_GB
    v, m, d = cupo_comprometido(hid)
    etiqueta = ("host %s" % hid) if hid else "host"
    problemas = []
    # SOLO se chequea el límite en las dimensiones que AUMENTAN (delta > 0): un
    # downgrade (o delta 0) siempre pasa, aunque el host YA esté por encima del límite
    # —p.ej. tras bajar HOST_MAX_*/max_* con VMs ya creadas— porque libera o no cambia
    # esa dimensión (hallazgo Codex #8: los downgrades no deben rechazarse).
    if d_vcpu > 0 and lim_v and v + d_vcpu > lim_v:
        problemas.append("vCPU %d+%d > máx %d" % (v, d_vcpu, lim_v))
    if d_ram_mb > 0 and lim_m and m + d_ram_mb > lim_m:
        problemas.append("RAM %d+%d MB > máx %d" % (m, d_ram_mb, lim_m))
    if d_disco_gb > 0 and lim_d and d + d_disco_gb > lim_d:
        problemas.append("disco %d+%d GB > máx %d" % (d, d_disco_gb, lim_d))
    if problemas:
        raise RuntimeError("cupo del %s excedido: " % etiqueta + "; ".join(problemas) +
                           " — liberar recursos, ajustar límites del host o elegir otro host")

def datastore_libre_gb(host=None):
    """GB libres del datastore DEL HOST según la API (bytes exactos). Lanza si no
    puede obtenerse — la creación debe abortar antes de clonar (fail-closed)."""
    ds_nombre = (host or {}).get("datastore") or DATASTORE
    out = govc("datastore.info", "-json", ds_nombre, host=host)
    data = json.loads(out)
    for ds in (data.get("datastores") or data.get("Datastores") or []):
        s = ds.get("summary") or ds.get("Summary") or {}
        if (s.get("name") or s.get("Name")) == ds_nombre:
            libre = s.get("freeSpace", s.get("FreeSpace"))
            if libre is not None:
                return int(libre) // (1024 ** 3)
    raise RuntimeError("no pude obtener el espacio libre del datastore %s" % ds_nombre)

def reservar_vm(marca, marca_cfg, sabor_slug, sabor, cliente, hostname, whmcs_serviceid=None,
                host=None):
    """Elige el siguiente nombre y lo RESERVA (INSERT estado='creando', CON su host)
    en una sola sección crítica. Antes el nombre se calculaba en el endpoint y se
    insertaba después en el hilo del job: dos creaciones simultáneas podían calcular
    el mismo número. Con la reserva atómica, la segunda ve la fila de la primera y
    toma el número siguiente. (nombre es PRIMARY KEY: tercera capa por si acaso.)"""
    with NOMBRE_LOCK:
        # #8 per-host (A2): cupo del HOST elegido, atómico con la reserva — la fila
        # insertada ya cuenta como comprometida en ese host (sin sobreventa)
        validar_cupo(sabor["vcpu"], sabor["ram_mb"], sabor["disco_gb"], host=host)
        nombre = siguiente_nombre(marca_cfg, cliente)
        with DB_LOCK, db() as c:
            c.execute("INSERT INTO vms(nombre,marca,sabor,cliente,hostname,estado,vcpu,ram_mb,disco_gb,whmcs_serviceid,host) "
                      "VALUES(?,?,?,?,?,'creando',?,?,?,?,?)",
                      (nombre, marca, sabor_slug, cliente, hostname,
                       sabor["vcpu"], sabor["ram_mb"], sabor["disco_gb"], whmcs_serviceid,
                       (host or {}).get("id")))
    return nombre

def red_activa(marca_cfg, modo):
    return marca_cfg["red_pruebas"] if modo == "pruebas" else marca_cfg["red_default"]

def power_state(nombre, host=None):
    try:
        out = govc("vm.info", "-json", nombre, host=host)
        vms = json.loads(out).get("virtualMachines") or json.loads(out).get("VirtualMachines") or []
        if vms:
            return vms[0].get("runtime", vms[0].get("Runtime", {})).get("powerState",
                   vms[0].get("Runtime", {}).get("PowerState", "?"))
    except Exception:  # noqa: BLE001 — #23: el "?" ES la señal (VM ausente o ESXi
        pass           # caído); los callers lo tratan explícitamente (saga #7)
    return "?"

def apagar_graceful(job, nombre, espera=60, host=None):
    """Shutdown por Tools EN EL HOST de la VM; si a los `espera` s sigue prendida,
    power off duro."""
    try:
        govc("vm.power", "-s", nombre, host=host)
    except RuntimeError as e:
        if "already" in str(e) or "powered off" in str(e).lower():
            return
        govc("vm.power", "-off", nombre, host=host)
        return
    t0 = time.time()
    while time.time() - t0 < espera:
        if power_state(nombre, host=host) == "poweredOff":
            return
        time.sleep(4)
        job.detalle("esperando apagado graceful (%ds)…" % int(time.time() - t0))
    job.detalle("no apagó graceful en %ds → power off forzado" % espera)
    govc("vm.power", "-off", nombre, host=host)

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

def _terse_props(ln):
    """Parsea UNA línea de 'print terse' de RouterOS en (flags, {propiedad: valor})
    ESCANEANDO secuencialmente y respetando comillas/escapes: un 'comment=' que viva
    DENTRO del valor entrecomillado de otra propiedad no cuenta como propiedad
    (Codex Medios r2 #22 — nada de re.search por substring)."""
    m = re.match(r"^\s*\d+\s+(?:([A-Z]+)\s+)?(.*)$", ln)
    flags, resto = (m.group(1) or "", m.group(2)) if m else ("", "")
    props, i, n = {}, 0, len(resto)
    while i < n:
        while i < n and resto[i] == " ":
            i += 1
        j = resto.find("=", i)
        if j < 0:
            break
        clave = resto[i:j]
        i = j + 1
        if i < n and resto[i] == '"':
            i += 1
            val = []
            while i < n and resto[i] != '"':
                if resto[i] == "\\" and i + 1 < n:   # escape dentro de comillas
                    i += 1
                val.append(resto[i])
                i += 1
            i += 1   # cierra la comilla
            props[clave] = "".join(val)
        else:
            k = resto.find(" ", i)
            k = n if k < 0 else k
            props[clave] = resto[i:k]
            i = k
    return flags, props

def set_root_password(ip, password):
    """Aplica la contraseña de root en la VM por SSH (chpasswd vía stdin — sin problemas
    de escape, y NO queda en el VMX). SSH sigue solo con llave: esta clave sirve para WHM/
    consola, no para SSH. Best-effort DEL CALLER, pero esta función ya no miente (Codex
    Medios #18): condición no cumplida = EXCEPCIÓN (nada de False ignorado y "aplicada"),
    ejecución con plazo total y streams drenados (_ssh_exec), y el error NUNCA incluye el
    stderr remoto crudo (podría ecoar la línea root:<clave>)."""
    if not MGMT_PRIVKEY_PATH:
        raise RuntimeError("sin MGMT_PRIVKEY_PATH configurada")
    if not ip:
        raise RuntimeError("la VM no tiene IP registrada")
    if not password:
        raise RuntimeError("password vacía")
    if "\n" in password or "\r" in password:
        # una clave con salto inyectaría OTRA línea usuario:clave al protocolo de chpasswd
        raise RuntimeError("password con salto de línea — no se aplica")
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        cli.connect(ip, username="root", key_filename=MGMT_PRIVKEY_PATH, timeout=15,
                    allow_agent=False, look_for_keys=False)
        try:
            _ssh_exec(cli, "chpasswd", stdin_data="root:%s\n" % password, timeout=30)
        except RuntimeError as e:
            m = re.search(r"rc=(\d+)", str(e))
            # con rc explícito NO se aplicó; ante timeout no es demostrable (pudo
            # aplicarse sin alcanzar a confirmar) — la redacción lo refleja (Codex)
            if m:
                raise RuntimeError("chpasswd falló (rc=%s) — la clave NO quedó aplicada"
                                   % m.group(1)) from None
            raise RuntimeError("no se pudo CONFIRMAR la aplicación de la clave "
                               "(timeout/canal) — verificar en el guest") from None
    finally:
        cli.close()
    return True

def flujo_crear(job, marca, sabor_slug, cliente, hostname, instalar_cpanel, modo,
                pubkey_cliente=None, root_password=None, whmcs_serviceid=None,
                sabor_def=None, host=None):
    marca_cfg = MARCAS[marca]
    # sabor_def viene resuelto desde /crear (catálogo o PERSONALIZADO con specs propias)
    sabor = sabor_def or SABORES[(marca, sabor_slug)]
    # A2: el host viene ELEGIDO desde /crear (selección del usuario o prioridad);
    # snapshot del dict — si lo pausan a mitad de creación, este job termina igual
    h = host or host_de_vm(job.vm)
    red = red_activa(marca_cfg, modo)
    prefijo = ipaddress.ip_network(red["subred"]).prefixlen
    nombre = job.vm

    # 1. validar la reserva del registro (el INSERT atómico ya lo hizo reservar_vm
    #    en /crear — cierra la carrera de nombre/número entre creaciones paralelas)
    job.paso()
    reg = vm_registrada(nombre)
    if not reg or reg.get("estado") != "creando":
        raise RuntimeError("reserva de %s no encontrada o en estado inesperado" % nombre)
    job.detalle("%s · %s (%d vCPU / %d MB / %d GB) · modo %s · host %s"
                % (nombre, sabor["nombre_web"], sabor["vcpu"], sabor["ram_mb"], sabor["disco_gb"], modo, h["id"]))

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

    # 3. espacio REAL del datastore (#8, fail-closed): antes solo se mostraba el df
    job.paso()
    libre_gb = datastore_libre_gb(h)
    requerido = sabor["disco_gb"] + DATASTORE_RESERVA_GB
    if libre_gb < requerido:
        raise RuntimeError("espacio insuficiente en %s (%s): %d GB libres < %d requeridos "
                           "(plan %d GB + reserva de seguridad %d GB)"
                           % (h["datastore"], h["id"], libre_gb, requerido, sabor["disco_gb"], DATASTORE_RESERVA_GB))
    job.detalle("datastore %s (%s): %d GB libres ≥ %d requeridos (plan %d + reserva %d) — OK"
                % (h["datastore"], h["id"], libre_gb, requerido, sabor["disco_gb"], DATASTORE_RESERVA_GB))

    # 4. clonar
    job.paso("IP %s reservada" % ip)
    esxi_ssh("mkdir-vm %s" % nombre, host=h)
    # catálogo de doradas: 2 por SO — base y '-cpanel' (preinstalado). Si el plan
    # lleva cPanel se clona la variante (entrega ~7 min); si aún no existe, se cae
    # al plan B: base + instalación post-creación (30-60 min).
    cpanel_preinstalado = False
    if instalar_cpanel:
        try:
            job.detalle("clonando %s-cpanel → %s (thin, cPanel preinstalado)…" % (DORADA_DEFAULT, nombre))
            esxi_ssh("clone-disk %s-cpanel %s" % (DORADA_DEFAULT, nombre), timeout=900, host=h)
            cpanel_preinstalado = True
        except RuntimeError as e:
            if "plantilla no existe" in str(e):
                job.detalle("dorada -cpanel no disponible → se usará la base e instalación post-creación")
            else:
                raise
    if not cpanel_preinstalado:
        job.detalle("clonando %s → %s (thin)…" % (DORADA_DEFAULT, nombre))
        esxi_ssh("clone-disk %s %s" % (DORADA_DEFAULT, nombre), timeout=900, host=h)

    # 5. crecer disco
    job.paso()
    esxi_ssh("grow-disk %s %d" % (nombre, sabor["disco_gb"]), host=h)
    job.detalle("disco extendido a %d GB (el guest lo crece al boot)" % sabor["disco_gb"])

    # 6. vmx
    job.paso()
    vmx = VMX_TEMPLATE.format(name=nombre, ram_mb=sabor["ram_mb"], vcpu=sabor["vcpu"],
                              portgroup=red["portgroup"])
    with open("/tmp/%s.vmx" % nombre, "w") as fh:
        fh.write(vmx)
    govc("datastore.upload", "-ds", h["datastore"], "/tmp/%s.vmx" % nombre,
         "VPS/%s/%s.vmx" % (nombre, nombre), host=h)
    os.unlink("/tmp/%s.vmx" % nombre)
    job.detalle("VM con CBT activo (ctkEnabled) — lista para backups incrementales")

    # 7. registrar en ESXi
    job.paso()
    govc("vm.register", "-ds", h["datastore"], "VPS/%s/%s.vmx" % (nombre, nombre), host=h)

    # 8. cloud-init vía guestinfo
    job.paso()
    fqdn = hostname if "." in hostname else "%s.%s" % (hostname, marca_cfg.get("dominio_hostname", marca))
    md = cloudinit_metadata(nombre, fqdn, ip, prefijo, red["gateway"], red["dns"])
    ud = cloudinit_userdata(fqdn, ip, prefijo, red["gateway"], red["dns"])
    govc("vm.change", "-vm", nombre,
         "-e", "guestinfo.metadata=%s" % _gz64(md),
         "-e", "guestinfo.metadata.encoding=gzip+base64",
         "-e", "guestinfo.userdata=%s" % _gz64(ud),
         "-e", "guestinfo.userdata.encoding=gzip+base64", host=h)

    # 9. power on
    job.paso()
    govc("vm.power", "-on", nombre, host=h)

    # 10. esperar IP
    job.paso()
    t0, ip_real = time.time(), ""
    while time.time() - t0 < 420:
        try:
            ip_real = govc("vm.ip", "-wait", "30s", nombre, timeout=45, host=h)
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
                        govc("vm.power", "-r", nombre, host=h)
                        job.detalle("sin SSH tras %ds — reinicio automático de la VM (1 vez): "
                                    "el primer boot a veces no aplica la IP estática" % transcurrido)
                    except (RuntimeError, subprocess.TimeoutExpired):
                        try:
                            govc("vm.power", "-reset", nombre, host=h)
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
    h = host_de_vm(job.vm)   # A2: se opera el host donde VIVE la VM
    job.paso()
    set_estado(job.vm, "eliminando")
    job.paso("VM %s validada (registro + prefijo, host %s)" % (job.vm, h["id"]))  # → apagar
    ps = power_state(job.vm, host=h)
    if ps == "?":
        job.detalle("la VM no aparece en el ESXi (probable reintento tras des-registro previo) — se omite apagar")
    elif ps != "poweredOff":
        apagar_graceful(job, job.vm, host=h)
    job.paso("apagada")  # → unregister
    try:
        govc("vm.unregister", job.vm, host=h)
    except RuntimeError as e:
        # no basta el texto del error: se CONFIRMA contra el inventario real
        if "not found" in str(e).lower() and power_state(job.vm, host=h) == "?":
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
        out = esxi_ssh("trash-vm %s" % job.vm, host=h)
        entrada = out.replace("OK", "").strip()
    except RuntimeError as e:
        if "no existe" not in str(e):
            raise
        # no basta el texto del error: se CONFIRMA el estado real en el datastore —
        # la carpeta no debe seguir en VPS/ y debe existir SU entrada en _papelera
        # (la movió un intento anterior; así se recupera la entrada REAL aunque aquel
        # intento muriera antes de registrarla). Listados con sentinel verificado.
        if job.vm in listar_wrapper("list-vps", host=h):
            raise RuntimeError("trash-vm dijo 'no existe' pero %s SIGUE en VPS/ — revisar a mano" % job.vm)
        entradas = [l for l in listar_wrapper("list-trash", host=h) if l.endswith("-" + job.vm)]
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
    h = host_de_vm(job.vm)   # A2: se opera el host donde VIVE la VM
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
    # #8: el upgrade también consume cupo (deltas negativos de downgrade pasan solos).
    # Nota: chequeo no atómico con otros editar simultáneos (raro, admin) — el cupo
    # duro de las CREACIONES sí es atómico (reservar_vm).
    validar_cupo(d_cpu, d_ram, disco_final - vm["disco_gb"], host=h)
    requiere_reinicio = d_cpu < 0 or d_ram < 0
    tipo = "DOWNGRADE (requiere reinicio breve)" if requiere_reinicio else "UPGRADE en caliente"
    job.paso("%s: %s" % (tipo, ", ".join(cambios)))  # → aplicar CPU/RAM

    # 2. CPU/RAM: subir = hot-add sin corte; bajar = apagar→cambiar→encender
    if d_cpu or d_ram:
        if requiere_reinicio:
            encendida = power_state(job.vm, host=h) == "poweredOn"
            if encendida:
                job.detalle("apagando para bajar CPU/RAM (no existe hot-remove)…")
                apagar_graceful(job, job.vm, host=h)
            govc("vm.change", "-vm", job.vm, "-c", str(sabor["vcpu"]), "-m", str(sabor["ram_mb"]), host=h)
            if encendida:
                govc("vm.power", "-on", job.vm, host=h)
                job.detalle("CPU/RAM ajustados a la baja; VM encendida de nuevo (corte ~1-2 min)")
            else:
                job.detalle("CPU/RAM ajustados (la VM estaba apagada)")
        else:
            govc("vm.change", "-vm", job.vm, "-c", str(sabor["vcpu"]), "-m", str(sabor["ram_mb"]), host=h)
            job.detalle("CPU/RAM aplicados en caliente (hot-add), sin reinicio")
    else:
        job.detalle("CPU/RAM sin cambios")
    job.paso()  # → disco

    # 3. disco: intentar hot-extend por API; si el vCenter lo bloquea, ciclo breve
    #    apagar→crecer→encender. Luego expandir el filesystem del guest por SSH.
    if crecer:
        encendida = power_state(job.vm, host=h) == "poweredOn"
        hot_ok = False
        if encendida:
            try:
                govc("vm.disk.change", "-vm", job.vm, "-disk.filePath",
                     "[%s] VPS/%s/%s.vmdk" % (h["datastore"], job.vm, job.vm),
                     "-size", "%dG" % sabor["disco_gb"], host=h)
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
                apagar_graceful(job, job.vm, host=h)
            esxi_ssh("grow-disk %s %d" % (job.vm, sabor["disco_gb"]), host=h)
            job.detalle("vmdk extendido a %d GB (VM apagada)" % sabor["disco_gb"])
            if encendida:
                govc("vm.power", "-on", job.vm, host=h)
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
                    # #19 (endurecido tras Codex Medios): el script SIEMPRE sale 0 y
                    # reporta UNA línea de estado (FS_OK a b | FS_SKIP razón | FS_ERR
                    # razón) — el rc ya no mezcla "fallo de herramienta" con "caso no
                    # automatizable". Cada RESULTADO se valida no-vacío ([ -n ]): los
                    # pipes de lsblk/df no reportan rc, pero su fallo deja el valor
                    # vacío y eso se convierte en FS_ERR/FS_SKIP (no en éxito).
                    # fstype y disco-sin-partición ANTES de tocar la partición,
                    # disco-sin-partición detectado, y la ejecución va por _ssh_exec
                    # (plazo total + streams drenados, sin recv_exit_status colgante).
                    script = (
                        'command -v findmnt >/dev/null 2>&1 || { echo "FS_SKIP sin findmnt"; exit 0; }; '
                        'SRC=$(findmnt -no SOURCE /) && [ -n "$SRC" ] || { echo "FS_ERR findmnt"; exit 0; }; '
                        'FST=$(findmnt -no FSTYPE /) && [ -n "$FST" ] || { echo "FS_ERR fstype"; exit 0; }; '
                        'case "$FST" in xfs|ext4|ext3) ;; *) echo "FS_SKIP fstype $FST"; exit 0;; esac; '
                        'case "$SRC" in /dev/sd*|/dev/vd*|/dev/nvme*) ;; '
                        '*) echo "FS_SKIP raiz en $SRC (LVM/otro)"; exit 0;; esac; '
                        'case "$SRC" in *[0-9]) ;; *) echo "FS_SKIP disco sin particion ($SRC)"; exit 0;; esac; '
                        'DISK=$(lsblk -no PKNAME "$SRC" 2>/dev/null | head -1); '
                        '[ -n "$DISK" ] || { echo "FS_SKIP sin disco padre ($SRC)"; exit 0; }; '
                        'PART=$(printf %s "$SRC" | grep -oE "[0-9]+$") || { echo "FS_ERR particion"; exit 0; }; '
                        'command -v growpart >/dev/null 2>&1 || { echo "FS_ERR sin growpart"; exit 0; }; '
                        'echo 1 > "/sys/class/block/$DISK/device/rescan" 2>/dev/null || true; '
                        'ANTES=$(df -B1 --output=size / 2>/dev/null | tail -1 | tr -dc 0-9); '
                        '[ -n "$ANTES" ] || { echo "FS_ERR df"; exit 0; }; '
                        'growpart "/dev/$DISK" "$PART" >/dev/null 2>&1; RC=$?; '
                        '[ "$RC" -eq 0 ] || [ "$RC" -eq 1 ] || { echo "FS_ERR growpart rc=$RC"; exit 0; }; '
                        'case "$FST" in '
                        'xfs) xfs_growfs / >/dev/null 2>&1 || { echo "FS_ERR xfs_growfs"; exit 0; };; '
                        '*) resize2fs "$SRC" >/dev/null 2>&1 || { echo "FS_ERR resize2fs"; exit 0; };; esac; '
                        'DESPUES=$(df -B1 --output=size / 2>/dev/null | tail -1 | tr -dc 0-9); '
                        '[ -n "$DESPUES" ] || { echo "FS_ERR df2"; exit 0; }; '
                        'echo "FS_OK $ANTES $DESPUES"')
                    try:
                        salida = _ssh_exec(cli, script, timeout=120).strip().splitlines()
                    finally:
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
                # parse ESTRICTO (Codex Medios #19): etiqueta exacta + 2 enteros; y el
                # "verificado" compara contra el TAMAÑO OBJETIVO del plan, no solo
                # antes/después (growpart NOCHANGE + resize sin efecto ya no pasa)
                ultima = salida[-1] if salida else ""
                m_ok = re.fullmatch(r"FS_OK (\d+) (\d+)", ultima)
                # umbral 85% del objetivo en GiB: tolera overhead de ext4/xfs y
                # redondeos GiB/GB, pero caza un FS que quedó chico de verdad
                objetivo_b = int(sabor["disco_gb"] * (2 ** 30) * 0.85)
                if m_ok:
                    antes_b, despues_b = int(m_ok.group(1)), int(m_ok.group(2))
                    if despues_b >= objetivo_b and despues_b >= antes_b:
                        job.detalle("filesystem expandido y VERIFICADO contra el umbral del plan: "
                                    "%.1f → %.1f GB (objetivo %d GB, umbral 85%%)"
                                    % (antes_b / 1024**3, despues_b / 1024**3, sabor["disco_gb"]))
                    else:
                        job.detalle("⚠ el FS NO alcanzó el tamaño del plan (%.1f de %d GB — "
                                    "¿partición siguiente bloquea el crecimiento?) — revisar a MANO"
                                    % (despues_b / 1024**3, sabor["disco_gb"]))
                elif ultima.startswith("FS_SKIP"):
                    job.detalle("⚠ FS no automatizable (%s) — crecer a MANO en el guest" % ultima[:100])
                else:   # FS_ERR o salida inesperada: NUNCA se anuncia éxito
                    job.detalle("⚠ expansión del FS FALLÓ (%s) — revisar el guest a mano"
                                % (ultima[:100] or "sin salida"))
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

VALID_TRASH_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-vps-[a-z]{2,5}-[a-z0-9][a-z0-9-]{0,40}$")

def expirar_secretos_jobs():
    """#14: redacta send_url/send_password del resultado de jobs con más de
    SEND_SECRETO_TTL_DIAS (el Send real ya expiró — retener la clave es riesgo sin
    utilidad). El resto del resultado (IPs, WHM, fingerprint) se conserva para el
    historial del dashboard. Idempotente; corre en la mantención diaria."""
    limpiados = 0
    with DB_LOCK, db() as c:
        rows = c.execute("SELECT id, resultado FROM jobs WHERE resultado IS NOT NULL "
                         "AND created_at < datetime('now','localtime','-%d days')"
                         % SEND_SECRETO_TTL_DIAS).fetchall()
        for r in rows:
            try:
                d = json.loads(r["resultado"])
            except (ValueError, TypeError):
                continue
            if not isinstance(d, dict):
                continue
            sensibles = [k for k in ("send_url", "send_password")
                         if d.get(k) not in (None, "", "(expirado)")]
            if not sensibles:
                continue
            for k in sensibles:
                d[k] = "(expirado)"
            c.execute("UPDATE jobs SET resultado=? WHERE id=?", (json.dumps(d), r["id"]))
            limpiados += 1
    return limpiados

def listar_wrapper(cmd, host=None):
    """Listado CONFIABLE vía wrapper del host indicado: exige el sentinel 'OK' final
    (el wrapper falla cerrado si no puede leer; un listado sin OK = respuesta
    truncada/no confiable → se aborta en vez de asumir 'vacío')."""
    lineas = [l.strip() for l in (esxi_ssh(cmd, host=host) or "").splitlines() if l.strip()]
    if not lineas or lineas[-1] != "OK":
        raise RuntimeError("%s no devolvió el sentinel OK — listado no confiable" % cmd)
    return lineas[:-1]

def _vault_borrar(item_id, job, entrada):
    """Borra una llave de la bóveda de forma IDEMPOTENTE: solo el HTTP 404
    estructurado de provision_post (item inexistente — p.ej. una corrida anterior la
    borró y cayó antes de anular el puntero) cuenta como éxito. Devuelve True si la
    llave quedó borrada/inexistente, False si hay que conservar la entrada."""
    try:
        provision_post("/vault-borrar-item", {"item_id": item_id})
        return True
    except Exception as e:  # noqa: BLE001
        if re.search(r"HTTP 404\b", str(e)):
            job.detalle("la llave de %s ya no existía en la bóveda (404) — se continúa" % entrada)
            return True
        job.detalle("bóveda falló para %s (%s) — entrada conservada para reintento"
                    % (entrada, str(e)[:80]))
        return False

def flujo_purgar(job):
    """Purga con ALLOWLIST del registro (#12) y bóveda-antes-de-borrar (#13):
    el motor decide QUÉ purgar — entradas que están en SU registro (estado papelera)
    Y cumplen 7 días según el prefijo AAAAMMDD-HHMMSS — y las purga de a UNA con
    purge-entry (el wrapper re-valida nombre y antigüedad por su lado). Entradas en
    _papelera ajenas al registro NO se tocan y se reportan como deriva. Por entrada:
    bóveda primero (si falla → se conserva TODO y se reintenta en la corrida diaria
    siguiente); la fila del registro se borra solo tras purgar de verdad."""
    job.paso()
    # #14: de pasada, redactar los secretos de entrega ya expirados de jobs antiguos
    secretos = expirar_secretos_jobs()
    # A2 (endurecido tras Codex #1): la purga barre CADA host del registro (pausado
    # incluido). Se registra cada aparición física como (entrada → set de host_id):
    # una entrada SOLO es purgable si aparece EXACTAMENTE en el host que dice su
    # registro. Si aparece en otro host, en varios, o en ninguno-registrado → deriva
    # (NO se toca): así una copia/restore manual del mismo nombre en otro host jamás
    # autoriza purgar en el host equivocado ni borrar la llave de la fila de otro host.
    apariciones, hosts_caidos = {}, []
    for _h in hosts_lista():
        try:
            for l in listar_wrapper("list-trash", host=_h):
                if VALID_TRASH_RE.match(l):
                    apariciones.setdefault(l, set()).add(_h["id"])
        except Exception as e:  # noqa: BLE001 — host caído no bloquea a los demás
            hosts_caidos.append(_h["id"])
            job.detalle("⚠ host %s no purgable hoy (%s) — sus entradas esperan" % (_h["id"], str(e)[:60]))
            audit("timer", "purgar-papelera", "-", "host %s inalcanzable en purga: %s" % (_h["id"], str(e)[:100]), "aviso")
    with DB_LOCK, db() as c:
        registradas = {r["papelera_entrada"]: {"vault": r["vault_item"], "estado": r["estado"], "host": r["host"]}
                       for r in c.execute("SELECT papelera_entrada, vault_item, estado, host FROM vms "
                                          "WHERE estado IN ('papelera','purgando') "
                                          "AND papelera_entrada IS NOT NULL")}
    limite = time.strftime("%Y%m%d-%H%M%S", time.localtime(time.time() - 7 * 86400))
    purgables, ajenas, deriva_host = [], [], []
    entradas_deriva = set()   # entradas en host != registrado: excluidas de TODA limpieza
                              # esta corrida (aunque el otro host caiga en el 2º barrido)
    for entrada, hosts_fis in apariciones.items():
        reg = registradas.get(entrada)
        if not reg:
            ajenas.append(entrada)                       # física en _papelera sin registro
            continue
        if hosts_fis != {reg["host"]}:
            # registrada en un host pero física en otro(s) / duplicada → NUNCA tocar
            deriva_host.append("%s registrada en %s pero física en {%s}"
                               % (entrada, reg["host"], ",".join(sorted(hosts_fis))))
            entradas_deriva.add(entrada)
            continue
        if entrada[:15] < limite:
            purgables.append(entrada)
    if ajenas:
        job.detalle("⚠ %d entrada/s en _papelera fuera del registro (deriva) — NO se tocan: %s"
                    % (len(ajenas), ", ".join(sorted(ajenas)[:5])))
        audit("timer", "purgar-papelera", "-",
              "deriva en _papelera (no tocadas): %s" % ", ".join(sorted(ajenas)[:10]), "aviso")
    if deriva_host:
        job.detalle("⚠ %d entrada/s en host distinto al registrado (posible copia/restore) — NO se tocan: %s"
                    % (len(deriva_host), "; ".join(deriva_host[:3])))
        audit("timer", "purgar-papelera", "-",
              "entradas en host equivocado (no tocadas): %s" % "; ".join(deriva_host[:8]), "aviso")
    purgadas, llaves_borradas, saltadas = [], 0, []
    for entrada in purgables:
        reg = registradas[entrada]
        _h = host_get(reg["host"])
        if not _h or _h["id"] in hosts_caidos:           # su host cayó tras el listado
            saltadas.append(entrada)
            continue
        vault_item = reg["vault"]
        if vault_item:
            # #13: la bóveda PRIMERO — si falla (o no hay token para operarla), la
            # entrada se conserva completa y se reintenta en la corrida diaria
            if not PROVISION_TOKEN:
                saltadas.append(entrada)
                job.detalle("hay llave en bóveda pero PROVISION_TOKEN no está configurado — %s conservada" % entrada)
                continue
            if not _vault_borrar(vault_item, job, entrada):
                saltadas.append(entrada)
                continue
            llaves_borradas += 1
            # llave ya borrada: se anula el puntero para que un fallo posterior de la
            # purga no re-intente borrar una llave inexistente en la próxima corrida
            # (y si el proceso cae justo aquí, _vault_borrar tolera el 404 al reintentar)
            with DB_LOCK, db() as c:
                c.execute("UPDATE vms SET vault_item=NULL WHERE papelera_entrada=? AND host=?",
                          (entrada, _h["id"]))
        # MARCA PERSISTIDA de "purga física iniciada" ANTES del borrado real: si el
        # proceso cae entre purge-entry y el DELETE, la próxima corrida reconoce la
        # fila 'purgando' como purga PROPIA confirmada y la limpia. Una fila
        # 'papelera' ausente de _papelera SIN esta marca jamás se limpia sola.
        with DB_LOCK, db() as c:
            c.execute("UPDATE vms SET estado='purgando' WHERE papelera_entrada=? AND host=?",
                      (entrada, _h["id"]))
        try:
            esxi_ssh("purge-entry %s" % entrada, timeout=300, host=_h)
        except RuntimeError as e:
            saltadas.append(entrada)
            job.detalle("purge-entry falló para %s (%s) — se reintenta en la próxima corrida"
                        % (entrada, str(e)[:80]))
            continue
        with DB_LOCK, db() as c:
            c.execute("DELETE FROM vms WHERE papelera_entrada=? AND host=?", (entrada, _h["id"]))
        purgadas.append(entrada)
    # reconciliación: filas cuya entrada YA NO está en _papelera. SOLO se limpian
    # solas las que llevan NUESTRA marca 'purgando' (purga propia interrumpida antes
    # del DELETE). Cualquier otra ausencia (borrado manual, pérdida en el datastore)
    # NO es prueba de purga → se alerta y se conserva fila+llave para revisión humana.
    # OJO: se re-leen registro y _papelera FRESCOS — los snapshots del inicio ya no
    # sirven (este mismo loop borró filas, y un eliminar concurrente pudo agregar
    # entradas nuevas a _papelera durante la corrida → serían deriva falsa).
    # frescos POR HOST: conjunto de pares (host_id, entrada) presentes físicamente
    en_papelera2, hosts_ok2 = set(), set()
    for _h in hosts_lista():
        if _h["id"] in hosts_caidos:
            continue
        try:
            for l in listar_wrapper("list-trash", host=_h):
                if VALID_TRASH_RE.match(l):
                    en_papelera2.add((_h["id"], l))
            hosts_ok2.add(_h["id"])
        except Exception:  # noqa: BLE001
            hosts_caidos.append(_h["id"])
    with DB_LOCK, db() as c:
        registro2 = {r["papelera_entrada"]: {"vault": r["vault_item"], "estado": r["estado"], "host": r["host"]}
                     for r in c.execute("SELECT papelera_entrada, vault_item, estado, host FROM vms "
                                        "WHERE estado IN ('papelera','purgando') "
                                        "AND papelera_entrada IS NOT NULL")}
    # ausente = su par (host_registrado, entrada) NO está físico Y su host respondió
    # el listado fresco (evidencia real por host; sin cruce por nombre entre hosts).
    # Se EXCLUYEN las entradas ya detectadas como deriva en el 1er barrido: si su copia
    # vive en otro host, una caída de ESE host en el 2º barrido no debe convertirse en
    # autorización para borrar (evidencia de deriva conservada toda la corrida).
    ausentes = sorted(e for e, reg in registro2.items()
                      if (reg["host"] or "") in hosts_ok2
                      and (reg["host"], e) not in en_papelera2
                      and e not in entradas_deriva)
    # índice entrada → hosts donde aparece FÍSICAMENTE (para no limpiar una fila cuya
    # copia vive en otro host — cierra la ruta que la reconciliación dejaba abierta)
    fis_por_entrada = {}
    for hid_f, e_f in en_papelera2:
        fis_por_entrada.setdefault(e_f, set()).add(hid_f)
    limpiadas, deriva_reg = [], []
    if ausentes:
        for entrada in ausentes:
            reg = registro2[entrada]
            nombre_vm = entrada[16:]
            otros = fis_por_entrada.get(entrada, set())
            if otros:
                # la entrada existe físicamente en OTRO host (copia/restore/movida) →
                # NUNCA limpiar fila+bóveda: quedaría una carpeta sin registro ni llave
                job.detalle("⚠ %s ausente de su host %s pero PRESENTE en {%s} — conservada, "
                            "revisar a mano" % (entrada, reg["host"], ",".join(sorted(otros))))
                deriva_reg.append(entrada)
                continue
            _hf = host_get(reg["host"])
            if not _hf:                                   # host desapareció del registro
                deriva_reg.append(entrada)
                continue
            try:
                en_vps = set(listar_wrapper("list-vps", host=_hf))
            except Exception:  # noqa: BLE001 — sin listado confiable no se toca
                deriva_reg.append(entrada)
                continue
            if nombre_vm in en_vps:
                job.detalle("⚠ %s figura '%s' en el registro pero la VM está en VPS/ de %s "
                            "(¿restore manual?) — revisar el registro a mano"
                            % (nombre_vm, reg["estado"], reg["host"]))
                continue
            if reg["estado"] != "purgando":
                deriva_reg.append(entrada)
                continue
            vault_item = reg["vault"]  # normalmente NULL a esta altura
            if vault_item:
                if not PROVISION_TOKEN or not _vault_borrar(vault_item, job, entrada):
                    continue
                llaves_borradas += 1
            with DB_LOCK, db() as c:
                c.execute("DELETE FROM vms WHERE papelera_entrada=? AND host=? AND estado='purgando'",
                          (entrada, reg["host"]))
            limpiadas.append(entrada)
            job.detalle("purga propia interrumpida completada (marca 'purgando'): %s" % entrada)
    if deriva_reg:
        job.detalle("⚠ %d fila/s 'papelera' cuya entrada desapareció de _papelera SIN marca de purga "
                    "propia — conservadas, revisar a mano: %s" % (len(deriva_reg), ", ".join(deriva_reg[:5])))
        audit("timer", "purgar-papelera", "-",
              "entradas desaparecidas sin marca de purga (conservadas): %s" % ", ".join(deriva_reg[:10]), "aviso")
    job.ok("papelera: %d purgada/s (allowlist del registro, >7 días) · %d llave/s de bóveda · "
           "%d saltada/s (reintento diario) · %d ajena/s NO tocadas · %d purga/s propia/s completada/s · "
           "%d desaparecida/s conservada/s · %d secreto/s de entrega expirados"
           % (len(purgadas), llaves_borradas, len(saltadas), len(ajenas), len(limpiadas),
              len(deriva_reg), secretos))

# ── FLUJO: reconciliar (#11) ─────────────────────────────────────────────────
PASOS_RECONCILIAR = [
    "Inventario ESXi vs registro",
    "NAT en RouterData vs registro",
    "NetBox (IPAM) vs registro — con reparación",
    "Bóveda vs registro",
    "Resumen y auditoría",
]

def flujo_reconciliar(job):
    """#11: detecta desalineaciones entre el registro (SQLite) y el mundo real
    (ESXi, RouterData, NetBox, bóveda). FILOSOFÍA (heredada de la purga): el motor
    REPARA solo donde es dueño único y la escritura es idempotente (NetBox, nuestro
    IPAM); todo lo demás genera ALERTA (job + auditoría) para revisión humana —
    esta función NUNCA muta el ESXi ni RouterOS ni la bóveda. Las VMs con job
    corriendo se excluyen (estado en tránsito legítimo, no anomalía)."""
    alertas, reparadas = [], []

    # 1. ESXi vs registro — POR HOST (A2, endurecido tras Codex #2): el inventario se
    #    indexa como pares (host_id, nombre) — nunca un set global de nombres — para
    #    que una copia del mismo nombre en otro host no enmascare una ausencia real ni
    #    oculte una VM sin registrar. Host caído → alerta y sus filas se saltan.
    job.paso()
    en_esxi = set()          # pares (host_id, nombre) presentes físicamente
    por_host = {}            # host_id -> set(nombres) para la deriva por host
    hosts_ok, hosts_mal = set(), []
    for _h in hosts_lista():
        try:
            nombres = set(listar_wrapper("list-vps", host=_h))
            por_host[_h["id"]] = nombres
            en_esxi |= {(_h["id"], n) for n in nombres}
            hosts_ok.add(_h["id"])
        except Exception as e:  # noqa: BLE001
            hosts_mal.append(_h["id"])
            alertas.append("host %s INALCANZABLE para reconciliar (%s)" % (_h["id"], str(e)[:60]))
    with DB_LOCK, db() as c:
        filas = [dict(r) for r in c.execute(
            "SELECT nombre, estado, ip, publica, pub_lista, hostname, nb_priv_id, nb_pub_id, "
            "vault_item, updated_at, host FROM vms WHERE estado NOT IN ('papelera','purgando')")]
        en_transito = {r["vm"] for r in c.execute(
            "SELECT DISTINCT vm FROM jobs WHERE estado='corriendo'")}
    filas = [f for f in filas if f["nombre"] not in en_transito]
    # filas de hosts caídos: sin listado confiable no se afirma nada sobre ellas
    filas = [f for f in filas if (f.get("host") or "") in hosts_ok]
    # deriva por host: VMs vps-* presentes en un host que NO están registradas EN ESE host
    reg_por_host = {}
    for f in filas:
        reg_por_host.setdefault(f["host"], set()).add(f["nombre"])
    for hid in hosts_ok:
        for nombre in sorted(por_host.get(hid, set()) - reg_por_host.get(hid, set())):
            if nombre in en_transito:
                continue
            alertas.append("VM %s existe en %s pero NO está registrada en ese host (deriva) — "
                           "el motor no la toca; revisar a mano" % (nombre, hid))
    for f in filas:
        if (f["host"], f["nombre"]) in en_esxi:           # presente EN SU host
            continue
        if f["estado"] == "eliminando":
            alertas.append("%s quedó a medio eliminar (sin carpeta en VPS/) — reintentar 'eliminar'" % f["nombre"])
        else:
            alertas.append("fila %s (estado %s, upd %s) SIN carpeta en VPS/ — ¿eliminada a mano "
                           "o creación muerta? revisar a mano" % (f["nombre"], f["estado"], f["updated_at"]))
    job.detalle("%d VM en ESXi · %d filas vigentes · %d en tránsito (excluidas) · %d alertas hasta aquí"
                % (len(en_esxi), len(filas), len(en_transito), len(alertas)))

    # 2. NAT vs registro — SOLO consultas (la tabla NAT es compartida con el NOC:
    #    reparar/crear/quitar reglas es decisión humana, no del reconciliador)
    job.paso()
    con_publica = [f for f in filas if f.get("publica") and f.get("ip")
                   and (f["host"], f["nombre"]) in en_esxi]
    nat_ok = 0
    for f in con_publica:
        oks, rs = mikrotik('/ip firewall nat print terse where chain=srcnat and src-address="%s" and to-addresses="%s"'
                           % (f["ip"], f["publica"]))
        okd, rd = mikrotik('/ip firewall nat print terse where chain=dstnat and dst-address="%s" and to-addresses="%s"'
                           % (f["publica"], f["ip"]))
        if not (oks and okd):
            alertas.append("no pude VERIFICAR el NAT de %s (consulta a RouterData falló)" % f["nombre"])
            continue
        tiene_src = any(l.strip() for l in rs.splitlines())
        tiene_dst = any(l.strip() for l in rd.splitlines())
        if tiene_src and tiene_dst:
            nat_ok += 1
        else:
            alertas.append("NAT INCOMPLETO para %s (%s↔%s): srcnat=%s dstnat=%s — reparar a mano"
                           % (f["nombre"], f["ip"], f["publica"],
                              "ok" if tiene_src else "FALTA", "ok" if tiene_dst else "FALTA"))
        # #22 (parcial — pendiente de Codex en #11): la entrada de address-list de la
        # pública debe figurar TOMADA (deshabilitada X + comentada) mientras el VPS viva
        okl, rl = mikrotik('/ip firewall address-list print terse where list=%s and address="%s"'
                           % (f["pub_lista"], f["publica"]))
        if not okl:
            alertas.append("no pude verificar la address-list de %s" % f["nombre"])
        else:
            # #22 endurecido (Codex Medios): se examinan TODAS las entradas numeradas
            # (un duplicado habilitado ya no se esconde tras la primera), el flag X se
            # lee del campo de flags (no "primer carácter tras el índice") y el
            # comentario debe tener VALOR (comment="" vacío no cuenta como tomada)
            entradas = [ln for ln in rl.splitlines() if re.match(r"^\s*\d+\s", ln)]
            if not entradas:
                alertas.append("la pública %s de %s NO está en la address-list %s — revisar"
                               % (f["publica"], f["nombre"], f["pub_lista"]))
            for ln in entradas:
                flags, props = _terse_props(ln)
                comentario = props.get("comment", "")
                if "X" not in flags or not comentario.strip():
                    alertas.append("address-list: la pública %s de %s tiene una entrada NO "
                                   "tomada (disabled=%s, comment=%s) — ¿liberada o duplicada?"
                                   % (f["publica"], f["nombre"],
                                      "sí" if "X" in flags else "NO",
                                      repr(comentario[:30]) if comentario else "vacío"))
                    break
    job.detalle("%d VPS con pública verificados: %d NAT completos" % (len(con_publica), nat_ok))

    # 3. NetBox vs registro — REPARACIÓN idempotente (netbox_ip_add reutiliza por
    #    address si ya existe; el peor caso re-escribe los mismos datos)
    job.paso()
    if NETBOX_URL and NETBOX_TOKEN:
        for f in con_publica:
            if f["estado"] not in ("activo", "suspendido"):
                continue
            desc = "%s - VPS (%s) · reconciliación vps-engine" % (f["hostname"], f["nombre"])
            for campo, ip_val, cidr in (("nb_priv_id", f["ip"], "/24"), ("nb_pub_id", f["publica"], "/32")):
                vigente = False
                if f.get(campo):
                    try:
                        r = netbox_req("GET", "/api/ipam/ip-addresses/%d/" % int(f[campo]))
                        vigente = (r.get("address", "").split("/")[0] == ip_val)
                    except Exception:  # noqa: BLE001 — id muerto o NetBox caído: se intenta reparar
                        vigente = False
                if vigente:
                    continue
                nuevo = netbox_ip_add(ip_val + cidr, f["hostname"] or "", desc)
                if nuevo:
                    with DB_LOCK, db() as c:
                        c.execute("UPDATE vms SET %s=? WHERE nombre=?" % campo, (nuevo, f["nombre"]))
                    reparadas.append("%s: %s re-registrada en NetBox (id %s)" % (f["nombre"], ip_val, nuevo))
                else:
                    alertas.append("no pude reparar en NetBox la IP %s de %s — revisar IPAM" % (ip_val, f["nombre"]))
        job.detalle("%d reparaciones NetBox" % len(reparadas))
    else:
        job.detalle("NetBox no configurado — verificación omitida")

    # 4. Bóveda vs registro — solo lectura (/list de vps-provision)
    job.paso()
    if PROVISION_TOKEN:
        try:
            r = provision_post("/list", {})
            items = r if isinstance(r, list) else (r.get("items") or r.get("keys") or r.get("llaves") or [])
            ids_boveda = {str(it.get("id")) for it in items if it.get("id")}
            with DB_LOCK, db() as c:
                refs = {str(x["vault_item"]): x["nombre"] for x in c.execute(
                    "SELECT nombre, vault_item FROM vms WHERE vault_item IS NOT NULL")}
            for item_id, nombre in sorted(refs.items()):
                if item_id not in ids_boveda:
                    alertas.append("la llave de %s (item %s…) NO está en la bóveda — revisar" % (nombre, item_id[:8]))
            huerfanas_bov = ids_boveda - set(refs)
            if huerfanas_bov:
                nombres = {str(it.get("id")): it.get("name", "?") for it in items}
                for item_id in sorted(huerfanas_bov):
                    alertas.append("llave huérfana en la bóveda sin fila en el registro: %s (item %s…) — "
                                   "el motor NO la borra; revisar" % (nombres.get(item_id, "?"), item_id[:8]))
            job.detalle("%d llaves referenciadas · %d en bóveda · %d huérfanas"
                        % (len(refs), len(ids_boveda), len(huerfanas_bov)))
        except Exception as e:  # noqa: BLE001
            alertas.append("bóveda no consultable (%s) — verificación omitida" % str(e)[:80])
            job.detalle("bóveda no consultable")
    else:
        job.detalle("PROVISION_TOKEN no configurado — verificación omitida")

    # 5. resumen: alertas al job (visible en dashboard) + auditoría (persistente)
    job.paso()
    for a in alertas[:20]:
        audit("timer", "reconciliar", "-", a, "aviso")
    if len(alertas) > 20:
        audit("timer", "reconciliar", "-", "(%d alertas más — ver job %s)" % (len(alertas) - 20, job.id), "aviso")
    detalle_final = (" · ".join(["⚠ " + a for a in alertas[:6]]) +
                     (" · (+%d más en auditoría)" % (len(alertas) - 6) if len(alertas) > 6 else "")) if alertas else "sin desalineaciones"
    job.detalle(detalle_final)
    job.ok("reconciliación: %d alertas · %d reparaciones NetBox · %d VM revisadas (%d con pública)"
           % (len(alertas), len(reparadas), len(filas), len(con_publica)))

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

# ── Validación de inputs (#15) ───────────────────────────────────────────────
ACTOR_LIMPIO_RE = re.compile(r"[^A-Za-z0-9@:. _+-]")
SERVICEID_RE = re.compile(r"^[0-9]{1,20}$")

def json_body():
    """Body JSON del request (#15): tolerante al Content-Type (compat con módulo y
    dashboard) pero JSON malformado o no-objeto → None (el endpoint responde 400
    JSON limpio en vez del HTML de Flask)."""
    d = request.get_json(force=True, silent=True)
    return d if isinstance(d, dict) else None

ERR_BODY = "body inválido: se espera un objeto JSON"

def limpiar_actor(v):
    """#15: 'actor' es texto informativo (auditoría/jobs del dashboard) que viene del
    cliente — charset acotado y largo máximo para que no contamine logs/UI. Tolera
    valores NO string (123, true, []): no son actor válido → 'api' (evita el 500 por
    .strip() sobre no-str, hallazgo Codex ronda1)."""
    if not isinstance(v, str):
        return "api"
    v = ACTOR_LIMPIO_RE.sub("", v.strip()[:60])
    return v or "api"

@app.route("/health")
def health():
    # #16: sin token (healthcheck del quadlet) o rol whmcs → mínimo, sin inventario;
    # el detalle (modo/marcas/sabores) es solo para el rol admin (dashboard via proxy)
    if auth() != "admin":
        return jsonify({"ok": True})
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
    d = json_body()
    if d is None:
        return jsonify({"error": ERR_BODY}), 400
    try:  # #15: campos string validados por tipo (no-str → 400, no 500 por .strip())
        marca = _str_campo(d, "marca")
        sabor = _str_campo(d, "sabor")
        cliente = _str_campo(d, "cliente")[:40]
        hostname = _str_campo(d, "hostname").lower()
        modo = _str_campo(d, "modo") or MODO   # pruebas | produccion (default = env)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    actor = limpiar_actor(d.get("actor") or "dashboard")
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
    # Sabor PERSONALIZADO (pedido Alcadio 2026-09-17): specs explícitas desde el
    # dashboard. SOLO rol admin — WHMCS vende el catálogo fijo. Pasa por el MISMO
    # cupo del host (#8) y chequeo de datastore que cualquier sabor.
    if sabor == "personalizado":
        if rol != "admin":
            return jsonify({"error": "el sabor personalizado es solo para el NOC (rol admin)"}), 403
        try:
            vcpu_p = int(d.get("vcpu"))
            ram_p = int(d.get("ram_mb"))
            disco_p = int(d.get("disco_gb"))
        except (TypeError, ValueError):
            return jsonify({"error": "personalizado requiere vcpu, ram_mb y disco_gb numéricos"}), 400
        if not (1 <= vcpu_p <= 24 and 1024 <= ram_p <= 65536 and 25 <= disco_p <= 600):
            return jsonify({"error": "personalizado fuera de rango: vCPU 1-24, RAM 1024-65536 MB, "
                                     "disco 25-600 GB (además aplica el cupo del host)"}), 400
        sabor_def = {"marca": marca, "slug": "personalizado",
                     "nombre_web": "Personalizado %d vCPU / %d GB RAM / %d GB" % (vcpu_p, ram_p // 1024, disco_p),
                     "vcpu": vcpu_p, "ram_mb": ram_p, "disco_gb": disco_p, "activo": True, "extras": {}}
    else:
        if (marca, sabor) not in SABORES:
            return jsonify({"error": "sabor desconocido: %s/%s" % (marca, sabor)}), 400
        if not SABORES[(marca, sabor)].get("activo", True):
            return jsonify({"error": "el sabor %s no está activo" % sabor}), 400
        sabor_def = SABORES[(marca, sabor)]
    if not re.match(r"^[a-z0-9][a-z0-9.-]{1,60}$", hostname):
        return jsonify({"error": "hostname inválido (minúsculas, dígitos, puntos, guiones)"}), 400
    # cPanel: lo decide el PLAN (todos los de hosting.cl lo incluyen). El parámetro
    # explícito instalar_cpanel queda como override para API/pruebas (y es el switch
    # del personalizado, cuyo default es SIN cPanel).
    if "instalar_cpanel" in d:
        cpanel = bool(d["instalar_cpanel"])
    else:
        cpanel = (sabor_def.get("extras", {}).get("cpanel_licencia_cuentas", 0) or 0) > 0
    # BYO key (opcional): el cliente trae su llave PÚBLICA — no generamos ni custodiamos
    _pk = d.get("pubkey_cliente")
    if _pk is not None and not isinstance(_pk, str):   # #15: tipo (evita 500 por .strip)
        return jsonify({"error": "pubkey_cliente debe ser texto"}), 400
    pubkey_cliente = (_pk or "").strip().replace("\r", "").replace("\n", " ").strip()
    if len(pubkey_cliente) > 4096:  # #15: tope antes de la regex
        return jsonify({"error": "llave pública demasiado larga"}), 400
    if pubkey_cliente:
        if not PUBKEY_RE.match(pubkey_cliente):
            return jsonify({"error": "llave pública inválida — pega una línea tipo "
                                     "'ssh-ed25519 AAAA… comentario' (ed25519, rsa o ecdsa)"}), 400
        try:
            fingerprint_pubkey(pubkey_cliente)
        except RuntimeError as e:
            return jsonify({"error": str(e)}), 400
    # clave de root opcional (la genera WHMCS) → se aplica en la VM para WHM/consola.
    # #15: NO se modifica (nada de .strip() — alteraría la clave que WHMCS puso en la
    # ficha y el cliente no podría entrar); solo se valida tipo y longitud sobre el
    # valor EXACTO. "" o ausente → None (no aplicar).
    _rp = d.get("root_password")
    if _rp is not None and not isinstance(_rp, str):
        return jsonify({"error": "root_password debe ser texto"}), 400
    root_password = _rp or None
    if root_password and len(root_password) > 128:
        return jsonify({"error": "root_password demasiado larga (máx 128)"}), 400
    # id del servicio en WHMCS (para trackear la VM sin depender del Username)
    whmcs_serviceid = (str(d.get("whmcs_serviceid")).strip() if d.get("whmcs_serviceid") else None)
    if whmcs_serviceid and not SERVICEID_RE.fullmatch(whmcs_serviceid):
        return jsonify({"error": "whmcs_serviceid inválido (numérico)"}), 400
    # A2: SELECCIÓN DE HOST — decisión del USUARIO, nunca automática por capacidad:
    # (a) dashboard/admin puede forzar host con el param `host`; (b) sin param (WHMCS
    # o dashboard por defecto) → el host activo de MEJOR PRIORIDAD (la administra el
    # usuario). Si el elegido no tiene cupo/espacio, la creación FALLA con motivo.
    host_req = (d.get("host") or "").strip()
    try:
        if host_req:
            if rol != "admin":
                return jsonify({"error": "solo el NOC puede elegir host"}), 403
            host_sel = host_get(host_req)
            if not host_sel:
                return jsonify({"error": "host desconocido: %s (ver GET /hosts)" % host_req}), 400
            if host_sel["estado"] != "activo":
                return jsonify({"error": "el host %s está PAUSADO — reactívalo o elige otro" % host_req}), 409
        else:
            host_sel = host_principal()
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 409
    nombre, job = None, None
    try:
        # reserva del nombre + creación del job BAJO EL MISMO GATE: sin esto hay una
        # ventana en que la fila 'creando' (con whmcs_serviceid) ya existe pero su job
        # no — un Terminate de WHMCS llegando justo ahí resolvería la VM por serviceid
        # y pasaría el gate, lanzando un eliminar sobre una VM a medio nacer
        with JOB_GATE_LOCK:
            nombre = reservar_vm(marca, MARCAS[marca], sabor, sabor_def,
                                 cliente, hostname, whmcs_serviceid, host=host_sel)
            job = Job("crear", nombre, actor, PASOS_CREAR)
        run_job(job, lambda j: flujo_crear(j, marca, sabor, cliente, hostname, cpanel, modo,
                                           pubkey_cliente or None, root_password, whmcs_serviceid,
                                           sabor_def=sabor_def, host=host_sel))
    except Exception as e:
        # cupo excedido: rechazo LIMPIO con el motivo (no es un error interno)
        if "cupo del" in str(e) and "excedido" in str(e):
            audit(actor, "crear", "-", str(e), "rechazado")
            return jsonify({"error": str(e)}), 409
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
            res = json.loads(d["resultado"])
        except (ValueError, TypeError):
            res = None
        # #14: redacción EN LECTURA — si el job tiene más de SEND_SECRETO_TTL_DIAS, los
        # secretos de entrega ya no se muestran aunque la purga aún no haya corrido
        # (cierra la ventana entre la expiración del Send y la mantención diaria)
        if isinstance(res, dict):
            viejo = (row["created_at"] or "") < time.strftime(
                "%Y-%m-%d %H:%M:%S", time.localtime(time.time() - SEND_SECRETO_TTL_DIAS * 86400))
            if viejo:
                for k in ("send_url", "send_password"):
                    if res.get(k) not in (None, "", "(expirado)"):
                        res[k] = "(expirado)"
        d["resultado"] = res
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
    hcache = {h["id"]: h for h in hosts_lista()}
    for r in rows:
        _h = hcache.get(r.get("host"))
        # host inválido/ausente → no se consulta el principal por error (mostraría el
        # power del host equivocado); se marca "?" (solo lectura, no destructivo)
        r["power"] = power_state(r["nombre"], host=_h) if _h else "?"
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
    if not SERVICEID_RE.fullmatch(sid):
        return jsonify({"error": "serviceid inválido (numérico)"}), 400
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
    d = json_body()
    if d is None:
        return jsonify({"error": ERR_BODY}), 400
    try:
        vm, acc = _str_campo(d, "vm"), _str_campo(d, "accion")
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    serviceid = (str(d.get("serviceid")).strip() if d.get("serviceid") else "")
    if serviceid and not SERVICEID_RE.fullmatch(serviceid):
        return jsonify({"error": "serviceid inválido (numérico)"}), 400
    actor = limpiar_actor(d.get("actor") or "dashboard")
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
    # #17: matriz de estados aptos por operación (reanudar sobre 'activo' se deja
    # pasar: el flujo responde ok-noop y el Unsuspend de WHMCS depende de eso)
    if reg.get("estado") == "purgando":
        return jsonify({"error": "la VM %s está en purga definitiva (timer) — no admite acciones" % vm}), 409
    aptos = {"suspender": ("activo",),
             "reanudar": ("activo", "suspendido"),
             "eliminar": ("activo", "suspendido", "creando", "eliminando")}[acc]
    if reg.get("estado") not in aptos:
        sugerencia = " — reintenta 'eliminar'" if reg.get("estado") == "eliminando" else ""
        return jsonify({"error": "la VM %s está '%s': la acción '%s' no aplica%s"
                        % (vm, reg.get("estado"), acc, sugerencia)}), 409
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
    d = json_body()
    if d is None:
        return jsonify({"error": ERR_BODY}), 400
    try:
        vm, sabor = _str_campo(d, "vm"), _str_campo(d, "sabor")
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    serviceid = (str(d.get("serviceid")).strip() if d.get("serviceid") else "")
    if serviceid and not SERVICEID_RE.fullmatch(serviceid):
        return jsonify({"error": "serviceid inválido (numérico)"}), 400
    actor = limpiar_actor(d.get("actor") or "dashboard")
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
    # #17: editar solo sobre VMs operativas, y solo hacia sabores activos del catálogo
    if reg.get("estado") not in ("activo", "suspendido"):
        return jsonify({"error": "la VM %s está '%s': no admite cambio de plan" % (vm, reg.get("estado"))}), 409
    if (reg["marca"], sabor) not in SABORES:
        return jsonify({"error": "sabor desconocido para la marca %s" % reg["marca"]}), 400
    if not SABORES[(reg["marca"], sabor)].get("activo", True):
        return jsonify({"error": "el sabor %s no está activo" % sabor}), 400
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

@app.route("/hosts")
def hosts_get():
    """Multi-host A1: lista de hosts VMware del motor con recursos EN VIVO (scan por
    host: datastore, CPU/RAM del fierro, comprometido y límites). Solo admin."""
    rol = auth()
    if not rol:
        return jsonify({"error": "unauthorized"}), 401
    if rol != "admin":
        return jsonify({"error": ERR_SOLO_ADMIN}), 403
    salida = []
    for h in hosts_lista():
        d = {k: h[k] for k in ("id", "ip", "datastore", "estado", "prioridad", "notas", "created_at")}
        d["recursos"] = host_recursos(h)
        salida.append(d)
    return jsonify({"hosts": salida})

HOST_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,30}$")
ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,60}$")

# tope superior de los límites: entero grande pero muy por debajo del máx de SQLite
# (evita OverflowError al bindear; holgado para cualquier host real)
LIMITE_MAX = 1_000_000_000

def _entero_opt(v, nombre, minimo=0, maximo=LIMITE_MAX, requerido=False):
    """Valida un entero opcional (#5/#6 Codex A2): None pasa salvo `requerido`; bool y
    no-enteros → error; rango [minimo, maximo] SIEMPRE acotado (maximo por defecto
    LIMITE_MAX para no romper el bind de SQLite). Lanza ValueError con mensaje claro."""
    if v is None:
        if requerido:
            raise ValueError("%s es obligatorio" % nombre)
        return None
    if isinstance(v, bool) or not isinstance(v, int):
        raise ValueError("%s debe ser un entero" % nombre)
    if v < minimo or v > maximo:
        raise ValueError("%s fuera de rango (%s-%s)" % (nombre, minimo, maximo))
    return v

def _str_campo(d, clave):
    """Lee un campo string del body de forma segura: None→'', y rechaza tipos no-str
    (evita 500 por .strip() sobre int/list — #6 Codex A2)."""
    v = d.get(clave)
    if v is None:
        return ""
    if not isinstance(v, str):
        raise ValueError("%s debe ser texto" % clave)
    return v.strip()

# ═══ Fase D: wizard de enrolamiento (preparar un host desde el dashboard) ═════
# Ejecuta los pasos 3 y 4 del RUNBOOK como job visible. La password de root del
# ESXi viaja dashboard→motor por loopback, vive SOLO en memoria durante el job y
# JAMÁS se persiste (ni BD, ni logs, ni auditoría, ni detalles del job). La huella
# del host la confirma un humano ANTES de ejecutar (flujo en 2 pasos, sin estado
# server-side: el paso 2 re-verifica que la huella actual == la confirmada).

# datastore = UN componente de ruta (va interpolado en comandos SSH, en BASE= del
# wrapper y en command= de authorized_keys → gramática ESTRICTA anti-inyección:
# sin espacios, comillas, $, ;, /, .. ni controles — hallazgo Codex wizard #1)
DATASTORE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
# candados del almacén del wizard (secretos.env / known_hosts): las escrituras son
# read-modify-write → serializadas (dos preparaciones concurrentes no se pisan)
ENROL_LOCK = threading.Lock()
# ── Reserva ATÓMICA de identidad de host (Codex wizard r3 #2) ─────────────────
# Las operaciones sobre hosts (preparar, copiar-doradas, PATCH/DELETE/POST) deben
# excluirse entre sí por ID *y* por IP a la vez: chequear-y-actuar por separado
# deja carreras (mismo id/2 ips, copiar-vs-preparar, activar a mitad de rotación).
# Un solo lock + un mapa de claves reservadas ("id:<hid>" / "ip:<ip>") → job.
# En memoria = válido con UN proceso (misma restricción documentada del gate de
# jobs); al reiniciar el motor los jobs corriendo se marcan error y las reservas
# desaparecen con ellos.
OPS_HOSTS_LOCK = threading.Lock()
OPS_HOSTS_RESERVAS = {}   # clave -> job_id

def reservar_op_host(claves, tipo, vm_job, actor, pasos):
    """Reserva TODAS las claves y crea el job en una sección crítica única.
    Devuelve (job, None) o (None, mensaje_de_conflicto)."""
    with OPS_HOSTS_LOCK:
        for k in claves:
            if k in OPS_HOSTS_RESERVAS:
                return None, "la identidad %s está reservada por el job %s" % (k, OPS_HOSTS_RESERVAS[k])
        job = Job(tipo, vm_job, actor, pasos)
        for k in claves:
            OPS_HOSTS_RESERVAS[k] = job.id
    return job, None

def liberar_op_host(job_id):
    with OPS_HOSTS_LOCK:
        for k in [k for k, v in OPS_HOSTS_RESERVAS.items() if v == job_id]:
            del OPS_HOSTS_RESERVAS[k]

def _lanzar_op_host(job, fn):
    """run_job que LIBERA la reserva al terminar el flujo (ok o error), incluso
    si el hilo no llega a arrancar."""
    def _fn(j):
        try:
            fn(j)
        finally:
            liberar_op_host(j.id)
    err = lanzar_o_fallar(job, _fn)
    if err:
        liberar_op_host(job.id)   # el hilo no arrancó; liberar aquí (doble-free inofensivo)
    return err

# los 46 privilegios EXACTOS del rol VpsOperator (idénticos a enrolar-host.sh / esxi-245)
PRIVS_VPSOPERATOR = (
    "Datastore.AllocateSpace Datastore.Browse Datastore.DeleteFile Datastore.FileManagement "
    "Datastore.UpdateVirtualMachineFiles Global.CancelTask Global.LogEvent Network.Assign "
    "Resource.AssignVMToPool System.Anonymous System.Read System.View Task.Create Task.Update "
    "VirtualMachine.Config.AddExistingDisk VirtualMachine.Config.AddNewDisk "
    "VirtualMachine.Config.AddRemoveDevice VirtualMachine.Config.AdvancedConfig "
    "VirtualMachine.Config.Annotation VirtualMachine.Config.CPUCount VirtualMachine.Config.DiskExtend "
    "VirtualMachine.Config.EditDevice VirtualMachine.Config.Memory VirtualMachine.Config.ReloadFromPath "
    "VirtualMachine.Config.RemoveDisk VirtualMachine.Config.Rename VirtualMachine.Config.ResetGuestInfo "
    "VirtualMachine.Config.Resource VirtualMachine.Config.Settings VirtualMachine.Interact.AnswerQuestion "
    "VirtualMachine.Interact.ConsoleInteract VirtualMachine.Interact.DeviceConnection "
    "VirtualMachine.Interact.PowerOff VirtualMachine.Interact.PowerOn VirtualMachine.Interact.Reset "
    "VirtualMachine.Interact.SetCDMedia VirtualMachine.Interact.Suspend VirtualMachine.Interact.ToolsInstall "
    "VirtualMachine.Inventory.Create VirtualMachine.Inventory.CreateFromExisting "
    "VirtualMachine.Inventory.Register VirtualMachine.Inventory.Unregister "
    "VirtualMachine.State.CreateSnapshot VirtualMachine.State.RemoveSnapshot "
    "VirtualMachine.State.RevertToSnapshot").split()

def _fsync_dir(ruta):
    """fsync del DIRECTORIO tras un os.replace (durabilidad del rename ante caída).
    Best-effort: en plataformas sin O_RDONLY sobre directorios (Windows de los
    tests) simplemente no aplica."""
    try:
        dfd = os.open(ruta, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except OSError:
        pass

def secreto_host(nombre):
    """Password de la API de un host: primero el entorno (engine.env — hosts
    enrolados a mano), luego el almacén del wizard (secretos.env bajo /data, 600).
    Se relee en cada uso → los hosts del wizard NO requieren reiniciar el motor."""
    v = os.environ.get(nombre)
    if v:
        return v
    try:
        with open(SECRETOS_HOSTS_PATH, encoding="utf-8") as f:
            for ln in f:
                if ln.startswith(nombre + "="):
                    return ln.rstrip("\r\n").split("=", 1)[1]
    except FileNotFoundError:
        pass   # almacén aún no existe = sin secreto. Cualquier OTRO error de
    return None   # lectura SÍ propaga (fail-closed, no fingir almacén vacío)

def secreto_host_guardar(nombre, valor):
    """Guarda/rota un secreto del almacén del wizard: archivo 600 bajo /data.
    Read-modify-write BAJO ENROL_LOCK (dos jobs no se pisan), tmp ÚNICO 600 +
    os.replace atómico + fsync. El valor jamás pasa por logs/BD/auditoría."""
    os.makedirs(ENROL_DIR, exist_ok=True)
    with ENROL_LOCK:
        lineas = []
        try:
            with open(SECRETOS_HOSTS_PATH, encoding="utf-8") as f:
                lineas = [ln.rstrip("\n") for ln in f
                          if ln.strip() and not ln.startswith(nombre + "=")]
        except FileNotFoundError:
            pass
        lineas.append("%s=%s" % (nombre, valor))
        fd, tmp = tempfile.mkstemp(dir=ENROL_DIR, prefix=".sec.")   # nombre único, 600 (mkstemp)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write("\n".join(lineas) + "\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, SECRETOS_HOSTS_PATH)
            _fsync_dir(ENROL_DIR)   # durabilidad del rename ante caída
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

def huella_host(ip, puerto=22):
    """Host key SSH del ESXi SIN autenticar (pre-trust) + huella SHA256 (mismo
    formato que ssh-keygen -lf). El humano la coteja contra la consola del host
    (canal autenticado) antes de autorizar el enrolamiento — anti-MITM."""
    # socket propio con timeout: Transport((ip,puerto)) conecta SIN límite (Codex #9a)
    sock = socket.create_connection((ip, int(puerto)), timeout=10)
    t = paramiko.Transport(sock)
    try:
        t.start_client(timeout=10)
        k = t.get_remote_server_key()
    finally:
        t.close()
    fp = base64.b64encode(hashlib.sha256(k.asbytes()).digest()).decode().rstrip("=")
    return k, "SHA256:" + fp

def _kh_nombre(ip, puerto):
    return ip if int(puerto) == 22 else "[%s]:%d" % (ip, int(puerto))

def pin_host_key(ip, puerto, key):
    """Pinnea la host key CONFIRMADA en el known_hosts escribible del wizard (los
    hosts del deploy original siguen en /keys/known_hosts, read-only)."""
    os.makedirs(ENROL_DIR, exist_ok=True)
    with ENROL_LOCK:   # read-modify-write serializado + tmp único + replace atómico
        hk = paramiko.HostKeys()
        if os.path.isfile(KNOWN_HOSTS_DATA):
            hk.load(KNOWN_HOSTS_DATA)
        hk.add(_kh_nombre(ip, puerto), key.get_name(), key)
        fd, tmp = tempfile.mkstemp(dir=ENROL_DIR, prefix=".kh.")
        try:
            os.close(fd)
            hk.save(tmp)
            fd2 = os.open(tmp, os.O_RDWR)   # fsync del contenido antes del rename
            try:
                os.fsync(fd2)
            finally:
                os.close(fd2)
            os.chmod(tmp, 0o644)   # el binario ssh (streaming) también lo lee
            os.replace(tmp, KNOWN_HOSTS_DATA)
            _fsync_dir(ENROL_DIR)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

def cliente_root_esxi(ip, puerto, password, key_confirmada):
    """SSH como root con password, aceptando EXCLUSIVAMENTE la host key que el
    humano confirmó (RejectPolicy ante cualquier otra). Solo lo usa el wizard."""
    cli = paramiko.SSHClient()
    cli.get_host_keys().add(_kh_nombre(ip, puerto), key_confirmada.get_name(), key_confirmada)
    cli.set_missing_host_key_policy(paramiko.RejectPolicy())
    cli.connect(ip, port=int(puerto), username="root", password=password,
                timeout=15, allow_agent=False, look_for_keys=False)
    return cli

def _ssh_exec(cli, comando, stdin_data=None, timeout=60):
    """exec_command con rc verificado. Drena stdout y stderr CONCURRENTEMENTE
    (comparten la ventana SSH: leerlos en secuencia permite al remoto bloquear el
    canal llenando el otro stream — Codex wizard r2 #4), con plazo GLOBAL por reloj
    monotónico y tope de captura (se sigue drenando, se deja de guardar). El error
    NUNCA incluye el stdin (por ahí viajan llaves/artefactos)."""
    stdin, out, _err = cli.exec_command(comando, timeout=2)   # timeout POR lectura (grano fino)
    ch = out.channel
    fin = time.monotonic() + timeout
    e_parts, e_tot = [], [0]
    def _drenar_err():
        # drena stderr hasta EOF o hasta el plazo global (+margen). socket.timeout
        # NO abandona (Codex r3 #3): solo re-chequea el reloj y sigue drenando.
        while time.monotonic() <= fin + 35:
            try:
                b = ch.recv_stderr(32768)   # devuelve lo DISPONIBLE (no espera llenar)
            except socket.timeout:
                continue
            except Exception:  # noqa: BLE001 — canal cerrado: terminó la sesión
                return
            if not b:
                return   # EOF de stderr
            if e_tot[0] < 65536:
                e_parts.append(b)
                e_tot[0] += len(b)
    th = threading.Thread(target=_drenar_err, daemon=True)
    th.start()
    o_parts, o_tot = [], 0
    try:
        if stdin_data is not None:
            stdin.write(stdin_data)
            stdin.flush()
        stdin.channel.shutdown_write()
        while True:
            if time.monotonic() > fin:
                raise RuntimeError("'%s': plazo total de %ds agotado leyendo la salida"
                                   % (comando.split()[0], timeout))
            try:
                b = ch.recv(32768)   # devuelve lo disponible; el reloj se revisa cada ≤2s
            except socket.timeout:
                continue
            if not b:
                break   # EOF de stdout
            if o_tot < 262144:
                o_parts.append(b)
                o_tot += len(b)
        fin_rc = time.monotonic() + 30   # tras EOF el status llega enseguida
        while not ch.exit_status_ready():
            if time.monotonic() > fin_rc:
                raise RuntimeError("'%s': el exit status no llegó (canal colgado)"
                                   % comando.split()[0])
            time.sleep(0.2)
        rc = ch.recv_exit_status()
    finally:
        ch.close()   # cierre garantizado en TODA salida (y descuelga al hilo de stderr)
        th.join(timeout=10)
    o = b"".join(o_parts).decode(errors="replace")
    e = b"".join(e_parts).decode(errors="replace")
    if rc != 0:
        raise RuntimeError("'%s' rc=%d: %s" % (comando.split()[0], rc, (e or o).strip()[:200]))
    return o

def govc_root(args, ip, password, timeout=60):
    """govc con credenciales root TEMPORALES (solo durante el wizard, jamás se
    guardan). Mismo aislamiento de entorno que govc(host=); el password se
    redacta de cualquier error antes de propagarlo (los detalles del job son
    visibles en el dashboard)."""
    env = {k: os.environ[k] for k in GOVC_ENV_SISTEMA if k in os.environ}
    env["GOVC_URL"] = "https://%s/sdk" % ip
    env["GOVC_USERNAME"] = "root"
    env["GOVC_PASSWORD"] = password
    env["GOVC_INSECURE"] = os.environ.get("GOVC_INSECURE", "1")
    try:
        r = subprocess.run([GOVC, *args], capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        # OJO: la repr de TimeoutExpired incluye el COMANDO (y host.account lleva
        # -password) — jamás propagarla cruda (Codex wizard #7)
        raise RuntimeError("govc %s: timeout tras %ds" % (args[0], timeout)) from None
    if r.returncode:
        msg = (r.stderr or r.stdout).strip().replace(password, "***")
        raise RuntimeError("govc %s: %s" % (args[0], _redact(msg)[:200]))
    return r.stdout

PASOS_PREPARAR = ["Verificar huella y conectar (root temporal)",
                  "Crear rol VpsOperator y usuario svc-vps",
                  "Instalar llave del motor y wrapper confinado",
                  "Pinnear huella y guardar secreto",
                  "Validar en vivo y registrar"]

def flujo_preparar_host(job, p):
    """Frontera de REDACCIÓN del wizard: _preparar_host hace el trabajo y aquí se
    borran ambas credenciales (root y svc-vps) de CUALQUIER excepción antes de que
    run_job la persista como error visible del job (Codex wizard #7). La svc_pass
    se genera aquí para que la frontera la conozca."""
    svc_pass = secrets.token_urlsafe(15) + "Aa1!"
    try:
        _preparar_host(job, p, svc_pass)
    except Exception as e:  # noqa: BLE001 — redacta y re-lanza (run_job hace el fail)
        msg = str(e).replace(p["root_password"], "***").replace(svc_pass, "***")
        raise RuntimeError(msg) from None

def _preparar_host(job, p, svc_pass):
    """Fases A+B del runbook ejecutadas por el motor (wizard). p trae la
    root_password SOLO en memoria; ningún paso/detalle la contiene."""
    hid, ip, puerto, datastore = p["id"], p["ip"], p["ssh_port"], p["datastore"]
    pass_var = "GOVC_PASSWORD_" + re.sub(r"[^A-Z0-9]", "_", hid.upper())
    job.paso()
    # pre-chequeos para NO romper un host operativo con la rotación (Codex wizard #6):
    if os.environ.get(pass_var):
        raise RuntimeError("%s ya existe en engine.env — ese valor tendría PRIORIDAD sobre el "
                           "almacén: tras rotar, el motor seguiría usando la password vieja. "
                           "Gestiona este host por engine.env o retira la variable" % pass_var)
    existente = host_get(hid)
    if existente and existente.get("estado") != "pausado":
        raise RuntimeError("el host %s ya existe y está '%s' — PAUSARLO antes de re-prepararlo "
                           "(la rotación de svc-vps interrumpe operaciones en curso)"
                           % (hid, existente.get("estado")))
    if existente and existente.get("ip") != ip:
        raise RuntimeError("el host %s ya existe con otra ip (%s) — no se re-prepara hacia %s"
                           % (hid, existente.get("ip"), ip))
    otro = next((h for h in hosts_lista() if h.get("ip") == ip and h.get("id") != hid), None)
    if otro:
        raise RuntimeError("la ip %s ya pertenece al host '%s' — dos ids sobre el mismo host "
                           "rotarían la misma cuenta svc-vps" % (ip, otro["id"]))
    key_srv, fp = huella_host(ip, puerto)
    if fp != p["huella"]:
        raise RuntimeError("la huella ACTUAL del host (%s) no coincide con la confirmada (%s) — "
                           "posible host suplantado; enrolamiento abortado" % (fp, p["huella"]))
    govc_root(["about"], ip, p["root_password"], timeout=30)
    cli = cliente_root_esxi(ip, puerto, p["root_password"], key_srv)
    try:
        job.paso("huella verificada; API y SSH de root responden")

        # rol mínimo: si existe se VERIFICAN sus privilegios y se RECONCILIAN (un rol
        # previo demasiado permisivo no se acepta a ciegas — Codex wizard #8). Solo
        # "not found" cuenta como ausencia; cualquier otro error de API propaga.
        try:
            out_rol = govc_root(["role.ls", "VpsOperator"], ip, p["root_password"], timeout=30)
            tiene = set(out_rol.split())
            if tiene != set(PRIVS_VPSOPERATOR):
                govc_root(["role.update", "VpsOperator", *PRIVS_VPSOPERATOR],
                          ip, p["root_password"])
                job.detalle("rol VpsOperator existía con %d privilegios distintos — "
                            "RECONCILIADO a los 46 exactos" % len(tiene))
            else:
                job.detalle("rol VpsOperator ya existía con los privilegios exactos")
        except RuntimeError as e:
            if "not found" not in str(e).lower():
                raise   # error real de API/permisos ≠ "el rol no existe"
            govc_root(["role.create", "VpsOperator", *PRIVS_VPSOPERATOR], ip, p["root_password"])
        try:
            govc_root(["host.account.create", "-id", "svc-vps", "-password", svc_pass],
                      ip, p["root_password"])
        except RuntimeError as e:
            if "already exist" not in str(e).lower():
                raise   # solo "ya existe" habilita la rotación; otro error propaga
            govc_root(["host.account.update", "-id", "svc-vps", "-password", svc_pass],
                      ip, p["root_password"])
        govc_root(["permissions.set", "-principal", "svc-vps", "-role", "VpsOperator"],
                  ip, p["root_password"])
        # una cuenta svc-vps PREVIA podría conservar permisos con otros roles en
        # otras rutas — se verifica que TODOS sus permisos sean VpsOperator
        permisos = govc_root(["permissions.ls"], ip, p["root_password"], timeout=30)
        for lnp in permisos.splitlines():
            if "svc-vps" in lnp and "VpsOperator" not in lnp:
                raise RuntimeError("svc-vps conserva un permiso con OTRO rol (%s) — "
                                   "retirarlo a mano antes de enrolar" % lnp.strip()[:100])
        job.paso("svc-vps con rol VpsOperator (46 privilegios y permisos verificados)")

        # llave dedicada del motor (RSA: ESXi 8 RECHAZA ed25519) + wrapper + forced-command
        base = "/vmfs/volumes/%s/VPS" % datastore
        os.makedirs(ENROL_KEYS_DIR, exist_ok=True)
        key_path = os.path.join(ENROL_KEYS_DIR, "vps_engine_esxi_%s" % hid.replace("-", "_"))
        if os.path.isfile(key_path):
            rsa = paramiko.RSAKey.from_private_key_file(key_path)
            job.detalle("llave del motor ya existía — se reutiliza")
        else:
            rsa = paramiko.RSAKey.generate(4096)
            fdk = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fdk, "w") as fk:
                rsa.write_private_key(fk)
        with open(WRAPPER_TEMPLATE, encoding="utf-8") as f:
            # lambda: el string de reemplazo de re.sub interpreta backslashes (Codex #1)
            wrapper = re.sub(r"(?m)^BASE=.*$", lambda _m: "BASE=" + base, f.read(), count=1)
        _ssh_exec(cli, "mkdir -p %s/_plantillas %s/_papelera %s/_bin" % (base, base, base))
        _ssh_exec(cli, "cat > %s/_bin/vps-wrapper.sh && chmod 755 %s/_bin/vps-wrapper.sh"
                  % (base, base), stdin_data=wrapper)
        b64 = rsa.get_base64()
        linea = ('command="%s/_bin/vps-wrapper.sh",no-port-forwarding,no-X11-forwarding,'
                 'no-agent-forwarding,no-pty ssh-rsa %s vps-engine@noc-monitor (%s)'
                 % (base, b64, hid))
        ya = _ssh_exec(cli, "grep -cF '%s' /etc/ssh/keys-root/authorized_keys 2>/dev/null || true"
                       % b64[:40])
        if (ya.strip() or "0") == "0":
            _ssh_exec(cli, "cat >> /etc/ssh/keys-root/authorized_keys", stdin_data=linea + "\n")
        if p.get("diag_pubkey"):   # llave de diagnóstico IA/humano: shell pleno, SIN command=
            db64 = p["diag_pubkey"].split()[1][:40]
            ya_d = _ssh_exec(cli, "grep -cF '%s' /etc/ssh/keys-root/authorized_keys 2>/dev/null || true"
                             % db64)
            if (ya_d.strip() or "0") == "0":
                _ssh_exec(cli, "cat >> /etc/ssh/keys-root/authorized_keys",
                          stdin_data=p["diag_pubkey"] + "\n")
        job.paso("wrapper instalado con BASE=%s; llave del motor confinada por command=" % base)
    finally:
        cli.close()

    pin_host_key(ip, puerto, key_srv)
    secreto_host_guardar(pass_var, svc_pass)
    job.paso("huella pinneada; secreto %s en el almacén (sin reinicio del motor)" % pass_var)

    # validación EN VIVO con las credenciales NUEVAS (misma vara que POST /hosts)
    candidato = {"id": hid, "ip": ip, "api_url": "https://%s/sdk" % ip,
                 "govc_user": "svc-vps", "pass_env": pass_var,
                 "datastore": datastore, "ssh_port": puerto, "ssh_key": key_path}
    govc("about", host=candidato, timeout=30)
    pong = esxi_ssh("ping", host=candidato, timeout=30)
    if pong.strip() != "pong":
        raise RuntimeError("el wrapper no respondió pong: %r" % pong[:40])
    libre = datastore_libre_gb(candidato)
    with DB_LOCK, db() as c:
        # (consulta en ESTA conexión: host_get() toma DB_LOCK y no es reentrante)
        if c.execute("SELECT 1 FROM hosts WHERE id=?", (hid,)).fetchone():
            # re-preparación (rotación/reparación): actualiza credenciales. estado
            # queda EXPLÍCITO en pausado (el pre-chequeo ya lo exigió) — así el
            # mensaje final "registrado PAUSADO" es verdad siempre (Codex #6)
            c.execute("UPDATE hosts SET ip=?, api_url=?, govc_user='svc-vps', pass_env=?, "
                      "datastore=?, ssh_port=?, ssh_key=?, estado='pausado' WHERE id=?",
                      (ip, candidato["api_url"], pass_var, datastore, puerto, key_path, hid))
        else:
            c.execute("INSERT INTO hosts(id, ip, api_url, govc_user, pass_env, datastore, "
                      "ssh_port, ssh_key, estado, prioridad, notas) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                      (hid, ip, candidato["api_url"], "svc-vps", pass_var, datastore, puerto,
                       key_path, "pausado", 100, p.get("notas") or ""))
    job.set_resultado({"host": hid, "datastore_libre_gb": libre,
                       "siguiente": "copiar doradas (botón en la pestaña Motor) y luego ACTIVAR"})
    job.ok("validado en vivo (API + pong + %d GB libres) — registrado PAUSADO" % libre)

PASOS_DORADAS = ["Listar plantillas de origen y destino",
                 "Copiar plantillas (streaming vía el motor)",
                 "Re-adelgazar discos (punch-zero)"]

def _ssh_wrapper_argv(h, subcomando):
    """argv del ssh BINARIO para invocar el wrapper de un host (para pipes de
    streaming origen→destino). Pinning con confianza exclusiva a los known_hosts
    del motor (deploy RO + wizard RW), igual que cliente_ssh_pinned."""
    kh = KNOWN_HOSTS_PATH + (" " + KNOWN_HOSTS_DATA if os.path.isfile(KNOWN_HOSTS_DATA) else "")
    return ["ssh", "-i", h.get("ssh_key") or ESXI_SSH_KEY, "-p", str(h.get("ssh_port") or 22),
            "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
            "-o", "GlobalKnownHostsFile=/dev/null", "-o", "UserKnownHostsFile=%s" % kh,
            "root@%s" % h["ip"], subcomando]

def flujo_copiar_doradas(job, origen, destino):
    """Copia las plantillas (doradas + isos) de un host enrolado a otro, en
    streaming a través del motor (los ESXi no se ven entre sí). Integridad por
    el CRC de gzip extremo a extremo; luego punch-zero para recuperar thin."""
    job.paso()
    def _lista(h):
        out = esxi_ssh("list-plantillas", host=h, timeout=60)
        lineas = [ln.strip() for ln in out.splitlines() if ln.strip()]
        if not lineas or lineas[-1] != "OK":
            raise RuntimeError("listado de plantillas sin sentinel OK en %s" % h["id"])
        return [ln for ln in lineas[:-1] if not ln.startswith(".")]
    en_origen = _lista(origen)
    en_destino = set(_lista(destino))
    pend = [n for n in en_origen if n not in en_destino]
    job.paso("origen %s: %d plantillas; faltan en %s: %s"
             % (origen["id"], len(en_origen), destino["id"], ", ".join(pend) or "ninguna"))
    copiadas = []
    # el listado viene del wrapper REMOTO: solo se aceptan nombres con la gramática
    # de plantilla (defensa en profundidad; el wrapper destino igual re-valida)
    re_plantilla = re.compile(r"^(dorada-[a-z0-9][a-z0-9.-]{1,30}|[a-z0-9][a-z0-9._-]{0,40}\.iso)$")
    for n in pend:
        if not re_plantilla.fullmatch(n):
            job.detalle("se omite entrada no reconocida del listado: %r" % n[:40])
            continue
        job.detalle("copiando %s…" % n)
        t0 = time.time()
        token = uuid.uuid4().hex        # identifica ESTA transferencia (hex32, único) (Codex r3 'otros')
        exp = imp = None
        # stderr de ambos a TEMPFILES (un PIPE sin drenar se llena y bloquea la
        # copia); kill garantizado e INDEPENDIENTE de cada proceso al salir. El
        # import deja la copia VALIDADA en .ok.<n> sin publicar: la publicación
        # es una 2ª fase que exige AMBOS rc=0 (un export fallido con stream
        # gzip-válido ya no puede quedar como plantilla usable — Codex r2 #5).
        with tempfile.TemporaryFile() as fe_exp, tempfile.TemporaryFile() as fe_imp:
            try:
                exp = subprocess.Popen(_ssh_wrapper_argv(origen, "export-plantilla %s" % n),
                                       stdout=subprocess.PIPE, stderr=fe_exp)
                imp = subprocess.Popen(_ssh_wrapper_argv(destino, "import-plantilla %s %s" % (n, token)),
                                       stdin=exp.stdout, stdout=subprocess.DEVNULL, stderr=fe_imp)
                exp.stdout.close()   # el EOF le llega a import cuando export termina
                imp.wait(timeout=7200)   # plazo global de la copia
                exp.wait(timeout=60)
            except subprocess.TimeoutExpired:
                try:   # descartar la copia de ESTE intento también en el camino del timeout
                    esxi_ssh("descartar-plantilla %s %s" % (n, token), host=destino, timeout=60)
                except Exception:  # noqa: BLE001
                    pass
                raise RuntimeError("copia de %s: timeout — se terminaron ambos procesos" % n) from None
            finally:
                for pr in (exp, imp):   # cada proceso con su propio try (r2 'otros')
                    if pr is None or pr.poll() is not None:
                        continue
                    try:
                        pr.kill()
                        pr.wait(timeout=10)
                    except Exception:  # noqa: BLE001 — seguir con el otro proceso
                        pass
            if exp.returncode != 0 or imp.returncode != 0:
                try:   # descartar la copia recibida no confirmada (best-effort)
                    esxi_ssh("descartar-plantilla %s %s" % (n, token), host=destino, timeout=60)
                except Exception:  # noqa: BLE001
                    pass
                fe_exp.seek(0)
                fe_imp.seek(0)
                raise RuntimeError("copia de %s falló (export rc=%s / import rc=%s): %s | %s"
                                   % (n, exp.returncode, imp.returncode,
                                      fe_exp.read(2048).decode(errors="replace").strip()[:150],
                                      fe_imp.read(2048).decode(errors="replace").strip()[:150]))
        # 2ª fase: publicar SOLO con ambos rc=0 confirmados por el motor, con el
        # TOKEN de este intento (una copia vieja no puede publicarse por accidente)
        esxi_ssh("publicar-plantilla %s %s" % (n, token), host=destino, timeout=120)
        copiadas.append(n)
        job.detalle("%s copiada y publicada en %ds" % (n, int(time.time() - t0)))
    job.paso("%d plantillas copiadas" % len(copiadas))
    for n in copiadas:
        if n.startswith("dorada-"):
            job.detalle("punch-zero de %s…" % n)
            esxi_ssh("thin-plantilla %s" % n, host=destino, timeout=3600)
    job.ok("plantillas al día en %s — ya puedes ACTIVAR el host" % destino["id"])

@app.route("/hosts/preparar", methods=["POST"])
def hosts_preparar():
    """Wizard (2 pasos, sin estado server-side): accion=huella devuelve la huella
    SHA256 para que el humano la coteje; accion=ejecutar (con huella_confirmada +
    root_password) lanza el job que securiza, valida y registra el host."""
    rol = auth()
    if not rol:
        return jsonify({"error": "unauthorized"}), 401
    if rol != "admin":
        return jsonify({"error": ERR_SOLO_ADMIN}), 403
    d = json_body()
    if d is None:
        return jsonify({"error": ERR_BODY}), 400
    try:
        accion = _str_campo(d, "accion") or "huella"
        ip = _str_campo(d, "ip")
        puerto = _entero_opt(d.get("ssh_port"), "ssh_port", 1, 65535)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    puerto = 22 if puerto is None else puerto
    try:
        ip = str(ipaddress.IPv4Address(ip))
    except ValueError:
        return jsonify({"error": "ip inválida"}), 400
    if accion == "huella":
        try:
            _, fp = huella_host(ip, puerto)
        except Exception as e:  # noqa: BLE001 — host apagado/inalcanzable: error de usuario
            return jsonify({"error": "no pude obtener la huella de %s: %s" % (ip, str(e)[:120])}), 400
        return jsonify({"ok": True, "ip": ip, "huella": fp,
                        "nota": "cotejar contra la consola del ESXi ANTES de ejecutar"})
    if accion != "ejecutar":
        return jsonify({"error": "accion inválida (huella | ejecutar)"}), 400
    try:
        hid = _str_campo(d, "id")
        datastore = _str_campo(d, "datastore")
        huella = _str_campo(d, "huella_confirmada")
        notas = _str_campo(d, "notas")[:200]
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    if not HOST_ID_RE.fullmatch(hid or ""):
        return jsonify({"error": "id inválido (minúsculas/dígitos/guiones, 2-31)"}), 400
    # el datastore se interpola en comandos SSH/BASE=/authorized_keys → gramática
    # estricta de UN componente (sin espacios/comillas/$/;//..) — Codex wizard #1
    if not DATASTORE_RE.fullmatch(datastore or ""):
        return jsonify({"error": "datastore inválido (un nombre simple: letras/dígitos/._- "
                                 "hasta 64, sin espacios ni separadores)"}), 400
    if not huella.startswith("SHA256:"):
        return jsonify({"error": "huella_confirmada requerida (formato SHA256:…) — "
                                 "obtenerla primero con accion=huella"}), 400
    _rp = d.get("root_password")
    if not isinstance(_rp, str) or not (1 <= len(_rp) <= 128):
        return jsonify({"error": "root_password requerida (texto, 1-128)"}), 400
    if any(ord(c) < 32 for c in _rp):
        return jsonify({"error": "root_password con caracteres de control"}), 400
    diag = d.get("diag_pubkey")
    if diag is not None:
        diag = diag.strip() if isinstance(diag, str) else None
        if not diag or not re.fullmatch(r"ssh-rsa [A-Za-z0-9+/=]{100,3000}( [^\r\n]{0,100})?", diag):
            return jsonify({"error": "diag_pubkey inválida — una línea 'ssh-rsa AAAA… comentario' "
                                     "(RSA: ESXi 8 no acepta ed25519)"}), 400
    actor = limpiar_actor(d.get("actor") or "dashboard")
    # reserva ATÓMICA de la identidad completa (id + ip) en una sola sección
    # crítica: cubre mismo-id/otra-ip, misma-ip/otro-id, y excluye PATCH/DELETE/
    # POST/copiar mientras dura la rotación (Codex wizard r3 #2)
    job, conflicto = reservar_op_host(("id:" + hid, "ip:" + ip),
                                      "preparar-host", hid, actor, PASOS_PREPARAR)
    if not job:
        return jsonify({"error": "no se puede preparar %s ahora: %s" % (hid, conflicto)}), 409
    params = {"id": hid, "ip": ip, "ssh_port": puerto, "datastore": datastore,
              "huella": huella, "root_password": _rp, "diag_pubkey": diag, "notas": notas}
    err = _lanzar_op_host(job, lambda j: flujo_preparar_host(j, params))
    if err:
        return err
    return jsonify({"ok": True, "job_id": job.id, "host": hid})

@app.route("/hosts/<hid>/copiar-doradas", methods=["POST"])
def hosts_copiar_doradas(hid):
    """Copia las plantillas que le falten a un host desde otro ya enrolado
    (streaming vía el motor). Job visible; al terminar, activar el host."""
    rol = auth()
    if not rol:
        return jsonify({"error": "unauthorized"}), 401
    if rol != "admin":
        return jsonify({"error": ERR_SOLO_ADMIN}), 403
    d = json_body()
    if d is None:
        return jsonify({"error": ERR_BODY}), 400
    try:
        desde = _str_campo(d, "desde")
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    destino = host_get(hid)
    if not destino:
        return jsonify({"error": "host %s no existe" % hid}), 404
    origen = host_get(desde)
    if not origen:
        return jsonify({"error": "host de origen '%s' no existe" % desde}), 400
    if origen["id"] == destino["id"]:
        return jsonify({"error": "origen y destino no pueden ser el mismo host"}), 400
    actor = limpiar_actor(d.get("actor") or "dashboard")
    # reserva ATÓMICA de AMBAS identidades (destino y ORIGEN, por id e ip): ni una
    # re-preparación del origen a mitad de export ni viceversa (Codex wizard r3 #2)
    claves = ("id:" + destino["id"], "ip:" + (destino.get("ip") or ""),
              "id:" + origen["id"], "ip:" + (origen.get("ip") or ""))
    job, conflicto = reservar_op_host(claves, "copiar-doradas", hid, actor, PASOS_DORADAS)
    if not job:
        return jsonify({"error": "no se puede copiar ahora: %s" % conflicto}), 409
    err = _lanzar_op_host(job, lambda j: flujo_copiar_doradas(j, origen, destino))
    if err:
        return err
    return jsonify({"ok": True, "job_id": job.id, "host": hid, "desde": desde})

@app.route("/hosts", methods=["POST"])
def hosts_add():
    """A2: enrolar un host al registro. NO se acepta a ciegas: se valida EN VIVO que
    la API responda con esas credenciales y que el wrapper conteste pong (lo que
    exige que el host ya esté preparado: svc-vps + llave + wrapper + huella en
    known_hosts — ver Fase C / noc-monitor/enrolar-host)."""
    rol = auth()
    if not rol:
        return jsonify({"error": "unauthorized"}), 401
    if rol != "admin":
        return jsonify({"error": ERR_SOLO_ADMIN}), 403
    d = json_body()
    if d is None:
        return jsonify({"error": ERR_BODY}), 400
    try:
        hid = _str_campo(d, "id")
        ip = _str_campo(d, "ip")
        pass_env = _str_campo(d, "pass_env")
        datastore = _str_campo(d, "datastore")
        ssh_key = _str_campo(d, "ssh_key")
        govc_user = _str_campo(d, "govc_user") or "svc-vps"
        notas = _str_campo(d, "notas")[:200]
        # #5: validación de enteros ANTES de cualquier llamada remota; prioridad
        # admite 0 explícito (no usar `or`, que lo convertiría en el default)
        ssh_port = _entero_opt(d.get("ssh_port"), "ssh_port", 1, 65535)
        ssh_port = 22 if ssh_port is None else ssh_port
        prioridad = _entero_opt(d.get("prioridad"), "prioridad", 0, 100000)
        prioridad = 100 if prioridad is None else prioridad   # POST: None → default 100
        max_vcpu = _entero_opt(d.get("max_vcpu"), "max_vcpu", 0)
        max_ram_mb = _entero_opt(d.get("max_ram_mb"), "max_ram_mb", 0)
        max_disco_gb = _entero_opt(d.get("max_disco_gb"), "max_disco_gb", 0)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    if not HOST_ID_RE.match(hid):
        return jsonify({"error": "id inválido (minúsculas/dígitos/guiones, 2-31)"}), 400
    try:
        ip = str(ipaddress.IPv4Address(ip))   # #6: validación real, no sintáctica
    except ValueError:
        return jsonify({"error": "ip inválida"}), 400
    if not ENV_NAME_RE.match(pass_env):
        return jsonify({"error": "pass_env inválido (nombre de variable en engine.env)"}), 400
    if not secreto_host(pass_env):
        return jsonify({"error": "la variable %s no existe en el entorno del motor ni en su "
                                 "almacén de secretos — agregarla a engine.env (+restart) o "
                                 "enrolar con el wizard (/hosts/preparar)" % pass_env}), 400
    if not DATASTORE_RE.fullmatch(datastore or ""):
        return jsonify({"error": "datastore inválido (un nombre simple, sin espacios/separadores)"}), 400
    if not ssh_key:
        return jsonify({"error": "ssh_key es obligatorio"}), 400
    if host_get(hid):
        return jsonify({"error": "el host %s ya existe" % hid}), 409
    otro_ip = next((h for h in hosts_lista() if h.get("ip") == ip), None)
    if otro_ip:
        return jsonify({"error": "la ip %s ya pertenece al host %s (dos identidades sobre el "
                                 "mismo host físico no se permiten)" % (ip, otro_ip["id"])}), 409
    # #4: la URL de la API se DERIVA de la IP validada (conexión directa a ESXi) — no
    # se acepta api_url libre, para que SSH (ip) y API no puedan apuntar a hosts
    # distintos (clonar en uno y registrar en otro).
    candidato = {"id": hid, "ip": ip, "api_url": "https://%s/sdk" % ip,
                 "govc_user": govc_user, "pass_env": pass_env,
                 "datastore": datastore, "ssh_port": ssh_port, "ssh_key": ssh_key}
    try:
        govc("about", host=candidato, timeout=30)
        pong = esxi_ssh("ping", host=candidato, timeout=30)
        if pong.strip() != "pong":
            raise RuntimeError("el wrapper no respondió pong: %r" % pong[:40])
        libre = datastore_libre_gb(candidato)
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": "host NO enrolable — falló la validación en vivo: %s "
                                 "(¿svc-vps/llave/wrapper/huella instalados? ver enrolar-host)"
                                 % str(e)[:200]}), 400
    # dup-check AUTORITATIVO + INSERT en una sola sección crítica compartida con el
    # wizard: dos POST concurrentes (o un POST durante una preparación) no pueden
    # registrar la misma identidad (Codex wizard r3 #2)
    with OPS_HOSTS_LOCK:
        jid = OPS_HOSTS_RESERVAS.get("id:" + hid) or OPS_HOSTS_RESERVAS.get("ip:" + ip)
        if jid:
            return jsonify({"error": "la identidad de %s/%s está en uso por el job %s"
                            % (hid, ip, jid)}), 409
        with DB_LOCK, db() as c:
            dup = c.execute("SELECT id FROM hosts WHERE id=? OR ip=?", (hid, ip)).fetchone()
            if dup:
                return jsonify({"error": "id o ip ya registrados (host %s)" % dup["id"]}), 409
            c.execute("INSERT INTO hosts(id, ip, api_url, govc_user, pass_env, datastore, ssh_port, "
                      "ssh_key, estado, prioridad, max_vcpu, max_ram_mb, max_disco_gb, notas) "
                      "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (hid, ip, candidato["api_url"], govc_user, pass_env,
                       datastore, ssh_port, ssh_key,
                       "pausado" if d.get("pausado") else "activo",
                       prioridad, max_vcpu, max_ram_mb, max_disco_gb, notas))
    audit(limpiar_actor(d.get("actor") or "dashboard"), "host-add", hid,
          "host enrolado: %s (%s, ds %s, %d GB libres)" % (hid, ip, candidato["datastore"], libre), "ok")
    return jsonify({"ok": True, "host": hid, "datastore_libre_gb": libre})

@app.route("/hosts/<hid>", methods=["PATCH"])
def hosts_patch(hid):
    """A2: pausar/activar, prioridad, límites y notas. Pausado = no recibe creaciones
    nuevas; sus VMs existentes se siguen gestionando (eliminar/editar/purga)."""
    rol = auth()
    if not rol:
        return jsonify({"error": "unauthorized"}), 401
    if rol != "admin":
        return jsonify({"error": ERR_SOLO_ADMIN}), 403
    h_act = host_get(hid)
    if not h_act:
        return jsonify({"error": "host desconocido: %s" % hid}), 404
    d = json_body()
    if d is None:
        return jsonify({"error": ERR_BODY}), 400
    sets, vals = [], []
    if "estado" in d:
        if d["estado"] not in ("activo", "pausado"):
            return jsonify({"error": "estado inválido (activo|pausado)"}), 400
        sets.append("estado=?"); vals.append(d["estado"])
    # #5: misma validación que el POST (rechaza bool y no-enteros; prioridad admite 0
    # pero NO null — un NULL iría primero en el ORDER BY y volvería principal al host;
    # los max_* SÍ admiten null = quitar el límite/heredar el global)
    try:
        if "prioridad" in d:
            val = _entero_opt(d["prioridad"], "prioridad", 0, 100000, requerido=True)
            sets.append("prioridad=?"); vals.append(val)
        for campo in ("max_vcpu", "max_ram_mb", "max_disco_gb"):
            if campo in d:
                val = _entero_opt(d[campo], campo, 0)         # None permitido (quita el límite)
                sets.append("%s=?" % campo); vals.append(val)
        if "notas" in d:
            sets.append("notas=?"); vals.append(_str_campo(d, "notas")[:200])
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    if not sets:
        return jsonify({"error": "nada que cambiar"}), 400
    # chequeo de reserva Y mutación en la MISMA sección crítica: una preparación
    # no puede empezar entre el chequeo y el UPDATE (Codex wizard r3 #2)
    with OPS_HOSTS_LOCK:
        jid = (OPS_HOSTS_RESERVAS.get("id:" + hid)
               or OPS_HOSTS_RESERVAS.get("ip:" + (h_act.get("ip") or "")))
        if jid:
            return jsonify({"error": "el host %s tiene una operación de identidad en curso "
                                     "(job %s) — reintenta al terminar" % (hid, jid)}), 409
        with DB_LOCK, db() as c:
            c.execute("UPDATE hosts SET %s, updated_at=datetime('now','localtime') WHERE id=?"
                      % ", ".join(sets), (*vals, hid))
    audit(limpiar_actor(d.get("actor") or "dashboard"), "host-edit", hid,
          "cambios: %s" % ", ".join(k for k in d if k != "actor"), "ok")
    return jsonify({"ok": True, "host": host_get(hid)})

@app.route("/hosts/<hid>", methods=["DELETE"])
def hosts_delete(hid):
    """A2: quitar un host del registro — SOLO si no tiene ninguna VM (ni en papelera:
    sus carpetas viven en el datastore de ese host)."""
    rol = auth()
    if not rol:
        return jsonify({"error": "unauthorized"}), 401
    if rol != "admin":
        return jsonify({"error": ERR_SOLO_ADMIN}), 403
    h_del = host_get(hid)
    if not h_del:
        return jsonify({"error": "host desconocido: %s" % hid}), 404
    with OPS_HOSTS_LOCK:   # chequeo de reserva + DELETE en la misma sección (r3 #2)
        jid = (OPS_HOSTS_RESERVAS.get("id:" + hid)
               or OPS_HOSTS_RESERVAS.get("ip:" + (h_del.get("ip") or "")))
        if jid:
            return jsonify({"error": "el host %s tiene una operación de identidad en curso "
                                     "(job %s) — no se puede quitar ahora" % (hid, jid)}), 409
        with DB_LOCK, db() as c:
            n = c.execute("SELECT COUNT(*) n FROM vms WHERE host=?", (hid,)).fetchone()["n"]
            if n:
                return jsonify({"error": "el host %s tiene %d VM(s) en el registro (incl. papelera) — "
                                         "no se puede quitar" % (hid, n)}), 409
            c.execute("DELETE FROM hosts WHERE id=?", (hid,))
    audit("dashboard", "host-del", hid, "host quitado del registro", "ok")
    return jsonify({"ok": True})

@app.route("/reconciliar", methods=["POST"])
def reconciliar():
    """#11: barrido de consistencia registro ↔ ESXi/RouterData/NetBox/bóveda.
    Solo admin. Idempotente: si ya hay una mantención corriendo (reconciliar o
    purga — comparten el gate vm='-'), devuelve ese job en vez de duplicar."""
    rol = auth()
    if not rol:
        return jsonify({"error": "unauthorized"}), 401
    if rol != "admin":
        return jsonify({"error": ERR_SOLO_ADMIN}), 403
    _dr = request.get_json(force=True, silent=True)   # una LISTA JSON válida daría 500 en .get
    actor = limpiar_actor((_dr.get("actor") if isinstance(_dr, dict) else None) or "dashboard")
    job, activo = lanzar_job_exclusivo("reconciliar", "-", actor, PASOS_RECONCILIAR)
    if not job:
        return jsonify({"ok": True, "job_id": activo["id"],
                        "nota": "mantención ya en curso (%s)" % activo["tipo"]})
    err = lanzar_o_fallar(job, flujo_reconciliar)
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
