<?php
/**
 * hostingcl_vps — Módulo de aprovisionamiento WHMCS para el motor "Vps" (hosting.cl)
 * -------------------------------------------------------------------------------
 * Traduce los eventos del ciclo de vida de WHMCS en llamadas a la API del motor
 * (que corre en noc-monitor, expuesta SOLO al WHMCS por el canal /vps-api/).
 *
 *   pago      -> CreateAccount    -> POST /crear
 *   mora      -> SuspendAccount   -> POST /accion {suspender}
 *   pagó      -> UnsuspendAccount -> POST /accion {reanudar}
 *   upgrade   -> ChangePackage    -> POST /editar
 *   cancela   -> TerminateAccount -> POST /accion {eliminar}
 *
 * Instalar en:  <whmcs>/modules/servers/hostingcl_vps/hostingcl_vps.php
 * Requiere un "Server" en WHMCS con:
 *   Hostname   = noc.hosting.cl
 *   Secure     = sí (https)
 *   Access Hash= el token dedicado del motor (WHMCS_TOKEN)
 *
 * NOTA: en esta fase de pruebas CreateAccount hace polling bloqueante del job
 * (~la creación tarda ~2 min sin cPanel). Para producción a gran escala se puede
 * pasar a "pending + verificación por cron"; para el harness manual, el polling
 * permite ver la secuencia completa desde la ficha.
 */

if (!defined('WHMCS')) {
    die('This file cannot be accessed directly');
}

use Illuminate\Database\Capsule\Manager as Capsule;

/* ------------------------------------------------------------------ *
 *  Metadatos y opciones de configuración por producto
 * ------------------------------------------------------------------ */

function hostingcl_vps_MetaData()
{
    return [
        'DisplayName' => 'Motor Vps (hosting.cl)',
        'APIVersion' => '1.1',
        'RequiresServer' => true,
    ];
}

function hostingcl_vps_ConfigOptions()
{
    return [
        'Sabor' => [
            'Type' => 'dropdown',
            'Options' => 'vps-estandar,vps-empresas,vps-cyber-black',
            'Default' => 'vps-estandar',
            'Description' => 'Plan del motor a aprovisionar',
        ],
        'Marca' => [
            'Type' => 'text',
            'Size' => '20',
            'Default' => 'hosting.cl',
        ],
        'Modo' => [
            'Type' => 'dropdown',
            'Options' => 'produccion,pruebas',
            'Default' => 'produccion',
            'Description' => 'Red donde nace el VPS',
        ],
        'Instalar cPanel' => [
            'Type' => 'yesno',
            'Description' => 'Marcar para instalar cPanel (en pruebas: dejar SIN marcar)',
        ],
    ];
}

/* ------------------------------------------------------------------ *
 *  Helpers: llamada al motor por el canal seguro
 * ------------------------------------------------------------------ */

function hostingcl_vps_api(array $params, $method, $path, $payload = null)
{
    $host = !empty($params['serverhostname']) ? $params['serverhostname'] : $params['serverip'];
    $base = 'https://' . $host . '/vps-api';
    $token = !empty($params['serveraccesshash']) ? $params['serveraccesshash'] : $params['serverpassword'];

    $ch = curl_init($base . $path);
    $headers = ['X-Auth-Token: ' . $token, 'Accept: application/json'];
    curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
    curl_setopt($ch, CURLOPT_CONNECTTIMEOUT, 10);
    curl_setopt($ch, CURLOPT_TIMEOUT, 30);
    curl_setopt($ch, CURLOPT_CUSTOMREQUEST, $method);
    if ($payload !== null) {
        curl_setopt($ch, CURLOPT_POSTFIELDS, json_encode($payload));
        $headers[] = 'Content-Type: application/json';
    }
    curl_setopt($ch, CURLOPT_HTTPHEADER, $headers);

    $resp = curl_exec($ch);
    $code = (int) curl_getinfo($ch, CURLINFO_HTTP_CODE);
    $err = curl_error($ch);
    curl_close($ch);

    if ($resp === false) {
        return ['_http' => 0, '_error' => $err ?: 'sin respuesta del motor'];
    }
    $json = json_decode($resp, true);
    if (!is_array($json)) {
        $json = ['_raw' => $resp];
    }
    $json['_http'] = $code;
    return $json;
}

/** Consulta un job hasta que termina (ok|error) o se agota el tiempo. */
function hostingcl_vps_poll(array $params, $jobid, $maxseconds)
{
    $waited = 0;
    while ($waited < $maxseconds) {
        $j = hostingcl_vps_api($params, 'GET', '/job/' . $jobid);
        $estado = isset($j['estado']) ? $j['estado'] : '';
        if ($estado === 'ok' || $estado === 'error') {
            return $estado;
        }
        sleep(6);
        $waited += 6;
    }
    return 'timeout';
}

/** Devuelve la IP pública de una VM ya creada (o '' si aún no). */
function hostingcl_vps_ip_publica(array $params, $vm)
{
    $r = hostingcl_vps_api($params, 'GET', '/vms');
    if (!empty($r['vms']) && is_array($r['vms'])) {
        foreach ($r['vms'] as $v) {
            if (isset($v['nombre']) && $v['nombre'] === $vm) {
                return isset($v['publica']) ? $v['publica'] : '';
            }
        }
    }
    return '';
}

/** Nombre de cliente corto y seguro para el motor. */
function hostingcl_vps_cliente(array $params)
{
    $base = isset($params['clientsdetails']['firstname']) ? $params['clientsdetails']['firstname'] : '';
    $base = preg_replace('/[^a-z0-9]/', '', strtolower($base));
    if ($base === '') {
        $base = 'cli' . (isset($params['userid']) ? $params['userid'] : '0');
    }
    return substr($base, 0, 20);
}

/* ------------------------------------------------------------------ *
 *  Ciclo de vida
 * ------------------------------------------------------------------ */

function hostingcl_vps_CreateAccount(array $params)
{
    try {
        $sabor = $params['configoption1'];
        $marca = $params['configoption2'] ?: 'hosting.cl';
        $modo  = $params['configoption3'] ?: 'produccion';
        $cpanel = (isset($params['configoption4']) && $params['configoption4'] === 'on');

        $hostname = strtolower(trim($params['domain']));
        if ($hostname === '') {
            $hostname = 'vps' . $params['serviceid'] . '.hosting.cl';
        }

        $payload = [
            'marca' => $marca,
            'sabor' => $sabor,
            'cliente' => hostingcl_vps_cliente($params),
            'hostname' => $hostname,
            'modo' => $modo,
            'instalar_cpanel' => $cpanel,
            'actor' => 'whmcs:' . $params['serviceid'],
            'whmcs_serviceid' => (string) $params['serviceid'],   // trackea la VM sin usar el Username
            'root_password' => $params['password'],               // clave de root (WHM/consola)
        ];

        $r = hostingcl_vps_api($params, 'POST', '/crear', $payload);
        if (($r['_http'] ?? 0) !== 200 || empty($r['ok'])) {
            return 'Error al crear: ' . ($r['error'] ?? $r['_error'] ?? ('HTTP ' . ($r['_http'] ?? '?')));
        }

        $vm = $r['vm'];
        $jobid = $r['job_id'];

        // El usuario de acceso del VPS es root (WHM/SSH). El motor trackea la VM por serviceid.
        try {
            Capsule::table('tblhosting')->where('id', $params['serviceid'])->update(['username' => 'root']);
        } catch (\Exception $e) { /* no bloquear */ }

        // Esperar a que el job termine (la creación sin cPanel tarda ~2 min)
        $estado = hostingcl_vps_poll($params, $jobid, 240);

        if ($estado === 'error') {
            return 'El motor reportó error creando el VPS (job ' . $jobid . ')';
        }

        // Guardar la IP pública en la ficha (dedicatedip)
        $ip = hostingcl_vps_ip_publica($params, $vm);
        if ($ip !== '') {
            try {
                Capsule::table('tblhosting')->where('id', $params['serviceid'])->update(['dedicatedip' => $ip]);
            } catch (\Exception $e) { /* no bloquear */ }
        }

        // ok o timeout: el VPS quedó creándose/creado. Marcamos success.
        return 'success';
    } catch (\Exception $e) {
        return 'Excepción CreateAccount: ' . $e->getMessage();
    }
}

function hostingcl_vps_SuspendAccount(array $params)
{
    try {
        $sid = (string) $params['serviceid'];
        $r = hostingcl_vps_api($params, 'POST', '/accion',
            ['serviceid' => $sid, 'accion' => 'suspender', 'actor' => 'whmcs:' . $sid]);
        if (($r['_http'] ?? 0) !== 200 || empty($r['ok'])) {
            return 'Error suspendiendo: ' . ($r['error'] ?? $r['_error'] ?? ('HTTP ' . ($r['_http'] ?? '?')));
        }
        hostingcl_vps_poll($params, $r['job_id'], 60);
        return 'success';
    } catch (\Exception $e) {
        return 'Excepción SuspendAccount: ' . $e->getMessage();
    }
}

function hostingcl_vps_UnsuspendAccount(array $params)
{
    try {
        $sid = (string) $params['serviceid'];
        $r = hostingcl_vps_api($params, 'POST', '/accion',
            ['serviceid' => $sid, 'accion' => 'reanudar', 'actor' => 'whmcs:' . $sid]);
        if (($r['_http'] ?? 0) !== 200 || empty($r['ok'])) {
            return 'Error reanudando: ' . ($r['error'] ?? $r['_error'] ?? ('HTTP ' . ($r['_http'] ?? '?')));
        }
        hostingcl_vps_poll($params, $r['job_id'], 60);
        return 'success';
    } catch (\Exception $e) {
        return 'Excepción UnsuspendAccount: ' . $e->getMessage();
    }
}

function hostingcl_vps_TerminateAccount(array $params)
{
    try {
        $sid = (string) $params['serviceid'];
        // eliminar exige confirmación: cuando se identifica por serviceid, el propio serviceid confirma
        $r = hostingcl_vps_api($params, 'POST', '/accion',
            ['serviceid' => $sid, 'accion' => 'eliminar', 'confirmacion' => $sid, 'actor' => 'whmcs:' . $sid]);
        if (($r['_http'] ?? 0) !== 200 || empty($r['ok'])) {
            return 'Error eliminando: ' . ($r['error'] ?? $r['_error'] ?? ('HTTP ' . ($r['_http'] ?? '?')));
        }
        hostingcl_vps_poll($params, $r['job_id'], 120);
        return 'success';
    } catch (\Exception $e) {
        return 'Excepción TerminateAccount: ' . $e->getMessage();
    }
}

function hostingcl_vps_ChangePackage(array $params)
{
    try {
        $sid = (string) $params['serviceid'];
        $sabor = $params['configoption1']; // sabor del producto destino
        $r = hostingcl_vps_api($params, 'POST', '/editar',
            ['serviceid' => $sid, 'sabor' => $sabor, 'actor' => 'whmcs:' . $sid]);
        if (($r['_http'] ?? 0) !== 200 || empty($r['ok'])) {
            return 'Error editando plan: ' . ($r['error'] ?? $r['_error'] ?? ('HTTP ' . ($r['_http'] ?? '?')));
        }
        hostingcl_vps_poll($params, $r['job_id'], 240);
        return 'success';
    } catch (\Exception $e) {
        return 'Excepción ChangePackage: ' . $e->getMessage();
    }
}

/** Botón "Test Connection" del Server en WHMCS. */
function hostingcl_vps_TestConnection(array $params)
{
    $r = hostingcl_vps_api($params, 'GET', '/health');
    if (($r['_http'] ?? 0) === 200 && !empty($r['ok'])) {
        return [
            'success' => true,
            'error' => '',
        ];
    }
    return [
        'success' => false,
        'error' => 'Motor no responde: HTTP ' . ($r['_http'] ?? '?') . ' ' . ($r['_error'] ?? ''),
    ];
}
