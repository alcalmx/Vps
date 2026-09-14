{* Panel de estado del VPS en el área de cliente — simple, sin detalles internos *}
<div class="card border-0 shadow-sm mt-3">
  <div class="card-body">
    <h5 class="card-title mb-3"><i class="fas fa-server me-2"></i>Tu servidor VPS</h5>

    {if $ip neq '' && $ip neq null}
      <div class="row g-2 mb-2">
        <div class="col-md-4"><span class="text-muted">Estado:</span> <strong>{$estado}</strong></div>
        <div class="col-md-4"><span class="text-muted">Dirección IP:</span> <strong>{$ip}</strong></div>
        <div class="col-md-4"><span class="text-muted">Hostname:</span> {$hostname}</div>
      </div>
      <hr>
      <p class="mb-1"><strong>Acceso:</strong> usuario <code>root</code></p>
      <p class="mb-1">Panel WHM: <a href="https://{$ip}:2087" target="_blank" rel="noopener">https://{$ip}:2087</a></p>
      <p class="text-muted small mb-0">El acceso por SSH es con tu llave. La contraseña de tu cuenta sirve para el panel WHM.</p>
    {else}
      <p class="text-muted mb-0">
        <i class="fas fa-spinner fa-spin me-2"></i>
        Tu VPS se está aprovisionando. En unos minutos verás aquí su dirección IP y los datos de acceso.
      </p>
    {/if}
  </div>
</div>
