# Entrega de credenciales al cliente (bóveda Vaultwarden + Bitwarden Send)

> Cómo llega la llave SSH del VPS al cliente. Hoy funciona para nosotros (acceso
> interno); este documento define cómo debe funcionar para un **cliente externo**
> cuando el alta la dispare WHMCS.

---

## El componente: Vaultwarden (la bóveda)

- **Qué es:** un servidor Bitwarden self-hosted (contenedor `vaultwarden` en noc-monitor,
  `127.0.0.1:8222`, publicado por nginx en `https://noc.hosting.cl/vault/`). Proyecto
  [[VpsClientes]] — se reutiliza aquí.
- **Qué guarda:** cada llave SSH de cliente queda como ítem "Clave SSH" en una colección
  `cliente-<nombre>` dentro de la organización "Hosting.cl - Clientes VPS". Auditable y
  re-entregable si el cliente pierde la llave.
- **Cómo se entrega:** con un **Bitwarden Send** — un enlace de un solo recurso, que
  **expira** (48 h) y admite **contraseña**. El motor lo crea automáticamente al securizar.

## Cómo funciona HOY (y por qué)

El enlace del Send vive en `https://noc.hosting.cl/vault/#/send/...`. Ese dominio está
**segmentado por IP de origen** (solo redes internas/oficina). Por eso **nosotros** podemos
descargar la llave —tenemos acceso interno—, pero **un cliente externo NO alcanzaría ese
enlace**. Funcionó en la prueba porque la descargamos desde dentro.

## El problema a resolver para clientes reales

Cuando un cliente nuevo contrate (y deje su email en WHMCS), hay que hacerle llegar la
llave. Las opciones y sus implicancias:

| Opción | Cómo | Seguridad | Veredicto |
|---|---|---|---|
| **A. Enviar la llave privada por email** | adjuntar/pegar el `id_ed25519` en un correo | ❌ Mala — la privada queda para siempre en un buzón, viaja en claro | **No** |
| **B. Email con el LINK del Send** | correo con el enlace del Send (expira, con contraseña por canal aparte) | ✅ Buena — el link caduca y se puede proteger | **Sí**, pero requiere que el link sea alcanzable por el cliente |
| **C. Abrir TODO el vault a internet** | quitar la segmentación de `/vault/` | ❌ Mala — expone login/admin de la bóveda | **No** |
| **D. Exponer SOLO las rutas de Send** | un subdominio/publicación público que sirva únicamente los Send, dejando login/admin de la bóveda restringido | ✅ Buena — es para lo que Send está diseñado (enlaces públicos, efímeros) | **Recomendada** para la entrega |
| **E. Área de cliente de WHMCS** | mostrar la credencial en el panel del cliente (no por email) | ✅ La más estándar en hosting | **Ideal a futuro** (Fase 3, con WHMCS) |

## Recomendación

**Combinar D + B ahora, y E cuando esté WHMCS:**

1. **Publicar un subdominio público solo para Sends** — ej. `entrega.hosting.cl` o
   `send.hosting.cl` apuntando a las rutas de Send de Vaultwarden, dejando el login y el
   admin de la bóveda **detrás de la segmentación actual**. Así el enlace es alcanzable por
   el cliente sin exponer la bóveda. (Nunca la opción C: abrir todo el vault.)
2. **Entregar por email el LINK del Send** (no la llave cruda), con **expiración** y, si se
   quiere, **contraseña** enviada por un canal aparte (SMS/WhatsApp).
3. **Nunca** mandar la llave privada directamente en el correo.
4. **Futuro (Fase 3):** cuando WHMCS dispare el alta, lo más limpio es entregar la
   credencial en el **área de cliente de WHMCS** (el cliente entra a su cuenta y la ve),
   que es el estándar del rubro. El email quedaría como aviso ("tu VPS está listo, entra a
   tu panel").

## Decisión pendiente del usuario

- ¿Vamos por el **subdominio público de Sends** (`entrega.hosting.cl`) + email con el link?
- ¿O esperamos a WHMCS y entregamos por el **área de cliente**?
- Mientras tanto (pocos clientes, manual): seguimos entregando nosotros la llave por el
  canal seguro que ya usemos, descargándola desde el vault interno.
