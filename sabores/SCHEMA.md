# Formato de un sabor (plan de VPS)

Un **sabor** es un plan estático tal como se vende en la web de la marca.
Un archivo JSON por sabor, en `sabores/<marca>/<slug>.json`. El usuario dicta
los valores desde la web; aquí quedan versionados y el motor los consume.

```json
{
  "slug": "vps-basico",
  "marca": "hosting.cl",
  "nombre_web": "VPS Básico",
  "vcpu": 2,
  "ram_mb": 4096,
  "disco_gb": 50,
  "so_default": "almalinux9",
  "red": {
    "portgroup": null,
    "nota": "null = usar el portgroup default de la marca (marcas/<marca>.json)"
  },
  "extras": {
    "ips_adicionales": 0,
    "backup_incluido": false
  },
  "precio_ref_clp": null,
  "activo": true,
  "notas": "Texto libre: lo que diga la web u observaciones del usuario."
}
```

## Reglas

- `slug`: minúsculas, `[a-z0-9-]`, único dentro de la marca. Es el identificador
  que usa el motor y el dashboard.
- `ram_mb` en MB y `disco_gb` en GB para evitar ambigüedades.
- `disco_gb` es el tamaño FINAL del disco: el motor clona la dorada (disco chico)
  y lo crece a este valor; cloud-init expande el filesystem al primer boot.
- `so_default`: debe existir como plantilla dorada (`_plantillas/dorada-<so>` en
  el datastore). Fase 1: `almalinux9`.
- `precio_ref_clp` es solo referencia informativa (la facturación es de WHMCS,
  no de este sistema).
- Un plan que sale de la web NO se borra: se marca `"activo": false` (las VMs
  existentes lo siguen referenciando).

## Cambios de plan (upgrade/downgrade)

El motor aplica el delta entre el sabor actual y el nuevo:
- vCPU/RAM: sube o baja (con reinicio en fase 1).
- Disco: **solo puede crecer**. Si el sabor destino tiene menos disco, se mantiene
  el actual y se registra la excepción en la auditoría.
