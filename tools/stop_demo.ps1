# stop_demo.ps1 - Apaga SOLO los procesos que lanzo start_demo.ps1.
#
# Lee .tmp\demo_pids.json y mata UNICAMENTE esos PIDs. Antes de matar verifica que
# el proceso sigue vivo y que su nombre actual coincide (aprox.) con el registrado
# en `process_name`: asi nunca mata un PID reutilizado por otra aplicacion.
# Al final borra el json. No toca nada fuera de ese archivo.
#
# Uso: powershell tools\stop_demo.ps1
$ErrorActionPreference = "Stop"
$Raiz = Split-Path -Parent $PSScriptRoot
Set-Location $Raiz
$PidsArchivo = Join-Path $Raiz ".tmp\demo_pids.json"

if (-not (Test-Path $PidsArchivo)) {
    Write-Host "No hay .tmp\demo_pids.json: no hay demo que apagar (o ya fue apagada)." -ForegroundColor Yellow
    exit 0
}

$doc = Get-Content $PidsArchivo -Raw | ConvertFrom-Json
$apagados = @()
$yaFuera = @()
$protegidos = @()

foreach ($p in @($doc.procesos)) {
    $proc = Get-Process -Id $p.pid -ErrorAction SilentlyContinue
    if ($null -eq $proc) {
        $yaFuera += ("{0} (PID {1} ya no existe)" -f $p.name, $p.pid)
        continue
    }

    # Guarda anti-PID-reutilizado: comparacion aproximada por contencion en ambos sentidos.
    $esperado = ("{0}" -f $p.process_name).ToLower()
    $actual = $proc.ProcessName.ToLower()
    if ($esperado -ne "" -and ($actual -notlike "*$esperado*") -and ($esperado -notlike "*$actual*")) {
        $protegidos += ("{0} (PID {1} ahora es '{2}': NO se toca)" -f $p.name, $p.pid, $proc.ProcessName)
        continue
    }

    Stop-Process -Id $p.pid -Force -ErrorAction SilentlyContinue
    $apagados += ("{0} (PID {1})" -f $p.name, $p.pid)
}

# Verificacion: ninguno de los PIDs registrados debe seguir vivo.
$vivos = @()
foreach ($p in @($doc.procesos)) {
    if (Get-Process -Id $p.pid -ErrorAction SilentlyContinue) {
        $vivos += ("{0} (PID {1}) SIGUE VIVO" -f $p.name, $p.pid)
    }
}

Start-Sleep -Milliseconds 300
Remove-Item $PidsArchivo -Force -ErrorAction SilentlyContinue

Write-Host "=== DEMO APAGADA ===" -ForegroundColor Green
if ($apagados.Count) { Write-Host ("  Apagados : {0}" -f ($apagados -join ", ")) }
if ($yaFuera.Count) { Write-Host ("  Ya fuera : {0}" -f ($yaFuera -join ", ")) -ForegroundColor Yellow }
foreach ($h in $protegidos) { Write-Host "  Protegido: $h" -ForegroundColor Yellow }
if ($vivos.Count) {
    Write-Host ("  HUERFANOS: {0}" -f ($vivos -join ", ")) -ForegroundColor Red
    Write-Host "  Reintenta este script o matalos a mano desde el Administrador de tareas." -ForegroundColor Yellow
    exit 1
}
Write-Host "  0 procesos huerfanos. Logs en .tmp\demo_*.log" -ForegroundColor Gray
exit 0