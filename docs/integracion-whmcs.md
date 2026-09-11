# Integración WHMCS — diseño y qué necesitamos (Fase 3)

> Definido con el usuario el 2026-09-11. El WHMCS lo administra **Cristian**; este
> documento fija el diseño del flujo y la **lista exacta de lo que hay que pedirle**.

---

## El flujo HOY (manual, el que se automatiza)

1. El cliente contrata en la web de hosting.cl (checkout de 3 pasos: dominio → cuenta → pago).
2. Con el pago confirmado, se crea **su ficha/servicio en WHMCS** automáticamente.
3. **Desde aquí todo es manual y tarda horas:** Fabián crea el VPS → se lo pasa a Alcadio,
   que configura la red → vuelve a Fabián → él entrega al cliente usuario/contraseña por
   WHMCS.

**Lo que se automatiza: del punto 2 al cliente con su VPS en la mano.**

## Decisión: el punto de entrada es el WHMCS (la ficha), no el formulario web

- La web es la vitrina; **WHMCS es la fuente de verdad del pago** y de todo el ciclo
  posterior (renovación, mora, upgrade, cancelación). Un solo punto de integración cubre
  todo el ciclo de vida.
- Mecanismo estándar: un **provisioning module** (server module) de WHMCS — PHP pequeño
  que ante cada evento llama a la API del motor `vps-engine` (que ya existe y está probada).

## El flujo automatizado (diseño)

```
pago confirmado
  → WHMCS activa el servicio → módulo "hostingcl_vps": CreateAccount
      → POST /crear al motor  (producto WHMCS → sabor; dominio → hostname;
                               llave pública del cliente si la dio → BYO)
      → el motor hace TODO (~11 min): IP privada+pública, NAT, clon dorada-cpanel,
        cloud-init, licencia cPanel, securización/entrega
      → el módulo hace polling de /job/<id> (los mismos pasos visibles)
  → al OK: el módulo guarda en la ficha la IP dedicada y los datos,
           marca el servicio Activo y WHMCS dispara el CORREO DE BIENVENIDA:
             · IP pública del VPS
             · acceso SSH: su llave BYO, o el link del Send para descargar la llave
             · WHM: "define tu contraseña con `passwd root` y entra a https://IP:2087"
  → cliente operando en ~15 min desde el pago, sin intervención humana.

mora     → SuspendAccount   → [confirmación Telegram al equipo] → /accion suspender
pagó     → UnsuspendAccount → /accion reanudar
upgrade  → ChangePackage    → /editar (upgrade caliente / downgrade con reinicio breve)
cancela  → TerminateAccount → [confirmación Telegram] → /accion eliminar (papelera 7 días)
```

## 📋 LO QUE NECESITAMOS DE CRISTIAN / DEL WHMCS

1. **Acceso para el conector**: poder instalar un módulo custom en
   `modules/servers/` del WHMCS (lo escribimos nosotros; Cristian lo instala o nos da
   acceso). Ideal: también un **entorno/producto de prueba** para probar sin pagos reales.
2. **Credenciales de la API de WHMCS** (API Identifier + Secret, con permisos acotados):
   para que el motor/módulo actualice la ficha (IP dedicada, notas) y dispare correos.
3. **Mapa de productos**: los IDs de los productos WHMCS de VPS (Estándar / Empresas /
   Cyber Black, por marca) para mapearlos a nuestros sabores. Confirmar que el alta del
   producto esté en "activar automáticamente al confirmar el pago".
4. **Campos custom en el producto/checkout**:
   - `Llave pública SSH (opcional)` → alimenta el modo BYO.
   - Confirmar qué llega del checkout web a la ficha (dominio, ciclo, addon SSL) — el
     **dominio** del paso 1 sirve como hostname del VPS.
5. **Conectividad WHMCS → motor**: ¿dónde está alojado el WHMCS? (¿alcanza a noc-monitor?).
   Definir el camino seguro: exponer la API del motor SOLO para la IP del WHMCS
   (allowlist + token dedicado), o vía VPN/tunel interno. **Nunca la API abierta a internet.**
6. **Plantilla del correo de bienvenida** ("Tu VPS está listo") con las variables que el
   módulo llenará: IP, link del Send / nota BYO, instrucción passwd root para WHM.
7. **Política de eventos**: confirmar qué dispara WHMCS en mora (Auto-Suspension a los N
   días) y en cancelación, para colgar ahí la confirmación por Telegram antes de ejecutar.

## Prerrequisitos nuestros antes del go-live

- [ ] **Subdominio público de Sends** (`entrega.hosting.cl`) — sin él, el cliente externo
      no puede descargar su llave (hoy el vault es solo interno). Ver
      [entrega-credenciales.md](entrega-credenciales.md). Con BYO no hace falta, pero el
      flujo gestionado lo requiere.
- [ ] Exposición segura de la API del motor hacia el WHMCS (allowlist + token dedicado).
- [ ] Bot de Telegram con botones de confirmación para suspender/eliminar (los bots ya
      existen en noc-monitor; falta el manejador).
- [ ] Módulo PHP `hostingcl_vps` (lo escribimos cuando tengamos los puntos 1-4).

## Preguntas abiertas

- ¿El checkout pide al cliente el dominio — usamos ese dominio como hostname del VPS, o
  generamos `vpsNNN.hosting.cl` y el dominio se apunta después? (definir con Cristian).
- ¿Multi-marca en el mismo WHMCS? (los productos de Planeta Hosting, etc. — el módulo ya
  soporta marcas vía nuestro motor).
