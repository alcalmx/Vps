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
import ipaddress
import json
import os
import re
import sqlite3
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import paramiko
from flask import Flask, jsonify, request

# ── Configuración ────────────────────────────────────────────────────────────
TOKEN = os.environ["ENGINE_TOKEN"]
ESXI_HOST = os.environ.get("ESXI_HOST", "10.100.37.245")
ESXI_SSH_PORT = int(os.environ.get("ESXI_SSH_PORT", "22"))
ESXI_SSH_KEY = os.environ.get("ESXI_SSH_KEY", "/keys/vps_engine_esxi")
GOVC = os.environ.get("GOVC_BIN", "/usr/local/bin/govc")
DATASTORE = os.environ.get("GOVC_DATASTORE", "DiscoA37245")
MGMT_PUBKEY = os.environ["MGMT_PUBKEY"].strip()
MGMT_PRIVKEY_PATH = os.environ.get("MGMT_PRIVKEY_PATH", "")  # para verificar ssh + cPanel
DB_PATH = os.environ.get("DB_PATH", "/data/registry.db")
CONFIG_DIR = os.environ.get("CONFIG_DIR", "/app/config")
MODO = os.environ.get("MODO", "pruebas")  # pruebas | produccion
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

def db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with DB_LOCK, db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS vms(
          nombre TEXT PRIMARY KEY, marca TEXT, sabor TEXT, cliente TEXT,
          hostname TEXT, ip TEXT, estado TEXT,          -- creando|activo|suspendido|papelera|error
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
ethernet0.present = "TRUE"
ethernet0.virtualDev = "vmxnet3"
ethernet0.networkName = "{portgroup}"
ethernet0.addressType = "generated"
vmci0.present = "TRUE"
tools.syncTime = "TRUE"
mem.hotadd = "TRUE"
vcpu.hotadd = "TRUE"
disk.EnableUUID = "TRUE"
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

def red_activa(marca_cfg):
    return marca_cfg["red_pruebas"] if MODO == "pruebas" else marca_cfg["red_default"]

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
    "Instalar cPanel (última versión)",
    "Registrar y finalizar",
]

def flujo_crear(job, marca, sabor_slug, cliente, hostname, instalar_cpanel):
    marca_cfg = MARCAS[marca]
    sabor = SABORES[(marca, sabor_slug)]
    red = red_activa(marca_cfg)
    prefijo = ipaddress.ip_network(red["subred"]).prefixlen
    nombre = job.vm

    # 1. validar / reservar en registro
    job.paso()
    if vm_registrada(nombre):
        raise RuntimeError("colisión de nombre: %s ya registrado" % nombre)
    with DB_LOCK, db() as c:
        c.execute("INSERT INTO vms(nombre,marca,sabor,cliente,hostname,estado,vcpu,ram_mb,disco_gb) "
                  "VALUES(?,?,?,?,?,'creando',?,?,?)",
                  (nombre, marca, sabor_slug, cliente, hostname,
                   sabor["vcpu"], sabor["ram_mb"], sabor["disco_gb"]))
    job.detalle("%s · %s (%d vCPU / %d MB / %d GB) · modo %s"
                % (nombre, sabor["nombre_web"], sabor["vcpu"], sabor["ram_mb"], sabor["disco_gb"], MODO))

    # 2. IP libre
    job.paso()
    ip = ip_libre_pruebas(red["subred"], red["gateway"], job)
    set_estado(nombre, "creando", ip=ip)
    job.detalle("IP asignada: %s (gw %s)" % (ip, red["gateway"]))

    # 3. espacio
    job.paso()
    df = esxi_ssh("df")
    job.detalle("datastore: %s" % df)

    # 4. clonar
    job.paso("IP %s reservada" % ip)
    esxi_ssh("mkdir-vm %s" % nombre)
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

    # 11. verificar ssh
    job.paso()
    if MGMT_PRIVKEY_PATH:
        ultimo = None
        for _ in range(10):
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
                time.sleep(6)
        if ultimo:
            raise RuntimeError("SSH con llave de gestión no entra: %s" % ultimo)
        job.detalle("SSH root@%s con llave de gestión: OK" % ip)
    else:
        job.detalle("sin MGMT_PRIVKEY_PATH configurada — verificación omitida")

    # 12. cPanel
    job.paso()
    if instalar_cpanel and MGMT_PRIVKEY_PATH:
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

    # 13. finalizar
    job.paso()
    set_estado(nombre, "activo")
    job.ok("VPS %s activo en %s — %s/%s" % (nombre, ip, marca, sabor_slug))

# ── FLUJOS: suspender / reanudar / eliminar / editar / purga ─────────────────
def flujo_suspender(job):
    vm = guardarraices(job.vm)
    job.paso()  # "Validar guardarraíles"
    job.paso("VM %s en registro (estado %s)" % (job.vm, vm["estado"]))  # → apagar
    apagar_graceful(job, job.vm)
    job.paso("apagada")  # → marcar
    set_estado(job.vm, "suspendido")
    job.ok("VPS %s suspendido (apagado + marcado)" % job.vm)

def flujo_reanudar(job):
    guardarraices(job.vm)
    job.paso()
    job.paso()
    govc("vm.power", "-on", job.vm)
    job.paso("encendida")
    t0 = time.time()
    ip = ""
    while time.time() - t0 < 300 and not ip:
        try:
            ip = govc("vm.ip", "-wait", "30s", job.vm, timeout=45)
        except RuntimeError:
            job.detalle("esperando que levante… (%ds)" % int(time.time() - t0))
    set_estado(job.vm, "activo")
    job.ok("VPS %s reanudado%s" % (job.vm, (" — IP %s" % ip) if ip else " (sin confirmación de IP)"))

def flujo_eliminar(job):
    vm = guardarraices(job.vm)
    job.paso()
    job.paso("VM %s validada (registro + prefijo)" % job.vm)  # → apagar
    if power_state(job.vm) != "poweredOff":
        apagar_graceful(job, job.vm)
    job.paso("apagada")  # → unregister
    govc("vm.unregister", job.vm)
    job.paso("des-registrada del ESXi")  # → papelera
    out = esxi_ssh("trash-vm %s" % job.vm)
    entrada = out.replace("OK", "").strip()
    set_estado(job.vm, "papelera", papelera_entrada=entrada)
    job.ok("VPS %s movido a papelera (%s) — purga automática a los 7 días" % (job.vm, entrada))

def flujo_editar(job, sabor_slug):
    vm = guardarraices(job.vm)
    sabor = SABORES[(vm["marca"], sabor_slug)]
    job.paso()
    cambios = []
    if sabor["vcpu"] != vm["vcpu"]:
        cambios.append("vCPU %d→%d" % (vm["vcpu"], sabor["vcpu"]))
    if sabor["ram_mb"] != vm["ram_mb"]:
        cambios.append("RAM %d→%d MB" % (vm["ram_mb"], sabor["ram_mb"]))
    crecer = sabor["disco_gb"] > vm["disco_gb"]
    if crecer:
        cambios.append("disco %d→%d GB" % (vm["disco_gb"], sabor["disco_gb"]))
    elif sabor["disco_gb"] < vm["disco_gb"]:
        cambios.append("disco se MANTIENE en %d GB (nunca se achica)" % vm["disco_gb"])
    if not cambios:
        job.ok("sin cambios: la VM ya coincide con %s" % sabor_slug)
        return
    job.paso("cambios: " + ", ".join(cambios))  # → apagar
    encendida = power_state(job.vm) == "poweredOn"
    if encendida:
        apagar_graceful(job, job.vm)
    job.paso("apagada" if encendida else "ya estaba apagada")  # → aplicar
    govc("vm.change", "-vm", job.vm, "-c", str(sabor["vcpu"]), "-m", str(sabor["ram_mb"]))
    if crecer:
        esxi_ssh("grow-disk %s %d" % (job.vm, sabor["disco_gb"]))
    job.paso("aplicado")  # → encender
    if encendida or vm["estado"] == "activo":
        govc("vm.power", "-on", job.vm)
    disco_final = sabor["disco_gb"] if crecer else vm["disco_gb"]
    set_estado(job.vm, vm["estado"], vcpu=sabor["vcpu"], ram_mb=sabor["ram_mb"], disco_gb=disco_final)
    with DB_LOCK, db() as c:
        c.execute("UPDATE vms SET sabor=? WHERE nombre=?", (sabor_slug, job.vm))
    job.ok("VPS %s ahora es %s (%s)" % (job.vm, sabor_slug, ", ".join(cambios)))

def flujo_purgar(job):
    job.paso()
    out = esxi_ssh("purge-trash", timeout=600)
    purgadas = [l for l in out.splitlines() if l.startswith("purged:")]
    with DB_LOCK, db() as c:
        for l in purgadas:
            entrada = l.split(":", 1)[1].strip()
            c.execute("DELETE FROM vms WHERE papelera_entrada=?", (entrada,))
    job.ok("papelera purgada: %d entradas con >7 días eliminadas" % len(purgadas))

# ── API ──────────────────────────────────────────────────────────────────────
def auth():
    return request.headers.get("X-Auth-Token") == TOKEN

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
    if not auth():
        return jsonify({"error": "unauthorized"}), 401
    d = request.get_json(force=True)
    marca = (d.get("marca") or "").strip()
    sabor = (d.get("sabor") or "").strip()
    cliente = (d.get("cliente") or "").strip()
    hostname = (d.get("hostname") or "").strip().lower()
    cpanel = bool(d.get("instalar_cpanel", True))
    actor = (d.get("actor") or "dashboard").strip()
    if marca not in MARCAS:
        return jsonify({"error": "marca desconocida: %s" % marca}), 400
    if (marca, sabor) not in SABORES:
        return jsonify({"error": "sabor desconocido: %s/%s" % (marca, sabor)}), 400
    if not re.match(r"^[a-z0-9][a-z0-9.-]{1,60}$", hostname):
        return jsonify({"error": "hostname inválido (minúsculas, dígitos, puntos, guiones)"}), 400
    if not SABORES[(marca, sabor)].get("activo", True):
        return jsonify({"error": "el sabor %s no está activo" % sabor}), 400
    nombre = siguiente_nombre(MARCAS[marca], cliente)
    job = Job("crear", nombre, actor, PASOS_CREAR)
    run_job(job, lambda j: flujo_crear(j, marca, sabor, cliente, hostname, cpanel))
    return jsonify({"ok": True, "job_id": job.id, "vm": nombre})

@app.route("/job/<jid>")
def job_get(jid):
    if not auth():
        return jsonify({"error": "unauthorized"}), 401
    with DB_LOCK, db() as c:
        row = c.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
    if not row:
        return jsonify({"error": "job no existe"}), 404
    d = dict(row)
    d["pasos"] = json.loads(d["pasos"])
    return jsonify(d)

@app.route("/jobs")
def jobs_list():
    if not auth():
        return jsonify({"error": "unauthorized"}), 401
    with DB_LOCK, db() as c:
        rows = c.execute("SELECT id,tipo,vm,estado,error,created_at,updated_at "
                         "FROM jobs ORDER BY created_at DESC LIMIT 30").fetchall()
    return jsonify({"jobs": [dict(r) for r in rows]})

@app.route("/vms")
def vms_list():
    if not auth():
        return jsonify({"error": "unauthorized"}), 401
    with DB_LOCK, db() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM vms WHERE estado != 'papelera' ORDER BY nombre").fetchall()]
        papelera = [dict(r) for r in c.execute(
            "SELECT nombre,papelera_entrada,updated_at FROM vms WHERE estado='papelera'").fetchall()]
    for r in rows:
        r["power"] = power_state(r["nombre"])
    return jsonify({"vms": rows, "papelera": papelera})

ACCIONES = {"suspender": (flujo_suspender, ["Validar guardarraíles", "Apagar (graceful, 60 s)", "Marcar suspendido"]),
            "reanudar": (flujo_reanudar, ["Validar guardarraíles", "Encender", "Esperar que levante"]),
            "eliminar": (flujo_eliminar, ["Validar guardarraíles", "Apagar", "Des-registrar del ESXi", "Mover a papelera"])}

@app.route("/accion", methods=["POST"])
def accion():
    if not auth():
        return jsonify({"error": "unauthorized"}), 401
    d = request.get_json(force=True)
    vm, acc = (d.get("vm") or "").strip(), (d.get("accion") or "").strip()
    actor = (d.get("actor") or "dashboard").strip()
    if acc not in ACCIONES:
        return jsonify({"error": "acción desconocida"}), 400
    try:
        guardarraices(vm)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 400
    if acc == "eliminar" and d.get("confirmacion") != vm:
        return jsonify({"error": "confirmación requerida: reescribe el nombre exacto de la VM"}), 400
    fn, pasos = ACCIONES[acc]
    job = Job(acc, vm, actor, pasos)
    run_job(job, fn)
    return jsonify({"ok": True, "job_id": job.id})

@app.route("/editar", methods=["POST"])
def editar():
    if not auth():
        return jsonify({"error": "unauthorized"}), 401
    d = request.get_json(force=True)
    vm, sabor = (d.get("vm") or "").strip(), (d.get("sabor") or "").strip()
    actor = (d.get("actor") or "dashboard").strip()
    try:
        reg = guardarraices(vm)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 400
    if (reg["marca"], sabor) not in SABORES:
        return jsonify({"error": "sabor desconocido para la marca %s" % reg["marca"]}), 400
    job = Job("editar", vm, actor,
              ["Calcular cambios de plan", "Apagar", "Aplicar CPU/RAM/disco", "Encender"])
    run_job(job, lambda j: flujo_editar(j, sabor))
    return jsonify({"ok": True, "job_id": job.id})

@app.route("/purgar-papelera", methods=["POST"])
def purgar():
    if not auth():
        return jsonify({"error": "unauthorized"}), 401
    job = Job("purgar-papelera", "-", "timer", ["Purgar entradas >7 días"])
    run_job(job, flujo_purgar)
    return jsonify({"ok": True, "job_id": job.id})

@app.route("/auditoria")
def auditoria():
    if not auth():
        return jsonify({"error": "unauthorized"}), 401
    with DB_LOCK, db() as c:
        rows = c.execute("SELECT * FROM operaciones ORDER BY id DESC LIMIT 100").fetchall()
    return jsonify({"operaciones": [dict(r) for r in rows]})

init_db()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8224, threaded=True)
