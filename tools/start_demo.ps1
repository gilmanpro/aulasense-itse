# start_demo.ps1 - Arranca el stack demo de AulaSense y registra los PIDs.
#
# Uso:
#   powershell tools\start_demo.ps1            # ingesta + dashboard + openmeteo (--loop)
#   powershell tools\start_demo.ps1 -Stub      # SOLO mecanica: 3 procesos `python -c sleep(30)`
#
# Que lanza (con el python del venv server\.venv):
#   ingesta   -> server\ingesta\ingesta_mqtt.py
#   dashboard -> server\dashboard\app.py           (http://127.0.0.1:8000, solo local)
#   openmeteo -> server\fuentes\openmeteo.py --loop
#
# Los PIDs se escriben en .tmp\demo_pids.json (los usa stop_demo.ps1):
#   { name, pid, started_at, script, process_name, log } por proceso.
# Logs en .tmp\demo_<name>.log y .tmp\demo_<name>.err.log.
#
# -Stub existe porque en F2 los modulos de ingesta/dashboard los escriben otros devs
# EN PARALELO: permite probar arranque+parada sin depender de que ya existan.
# Si un modulo aun no existe, se avisa con WARN y se omite (no se cae).
# Este script NUNCA imprime credenciales ni URLs no locales.

param(
    [switch]$Stub
)
$ErrorActionPreference = "Stop"
$Raiz = Split-Path -Parent $PSScriptRoot
Set-Location $Raiz
$Tmp = Join-Path $Raiz ".tmp"
New-Item -ItemType Directory -Force -Path $Tmp | Out-Null
$PidsArchivo = Join-Path $Tmp "demo_pids.json"

$PyVenv = Join-Path $Raiz "server\.venv\Scripts\python.exe"
if (-not (Test-Path $PyVenv)) {
    Write-Host "ERROR: no existe server\.venv\Scripts\python.exe" -ForegroundColor Red
    Write-Host "Crea el venv con: C:\Python315\python.exe -m venv server\.venv" -ForegroundColor Yellow
    Write-Host "y luego: server\.venv\Scripts\python.exe -m pip install -r server\requirements.txt" -ForegroundColor Yellow
    exit 1
}

# Si ya hay una demo viva no se lanza otra (evita pids pisados y procesos huerfanos).
if (Test-Path $PidsArchivo) {
    $previo = Get-Content $PidsArchivo -Raw | ConvertFrom-Json
    $vivos = @($previo.procesos | Where-Object { Get-Process -Id $_.pid -ErrorAction SilentlyContinue })
    if ($vivos.Count -gt 0) {
        Write-Host ("ERROR: ya hay una demo en marcha (PIDs: {0})." -f (($vivos | ForEach-Object { $_.pid }) -join ", ")) -ForegroundColor Red
        Write-Host "Apagala primero con: powershell tools\stop_demo.ps1" -ForegroundColor Yellow
        exit 1
    }
    Remove-Item $PidsArchivo -Force
}

$Servicios = @(
    @{ name = "ingesta";   script = "server\ingesta\ingesta_mqtt.py";  args = @() }
    @{ name = "dashboard"; script = "server\dashboard\app.py";         args = @() }
    @{ name = "openmeteo"; script = "server\fuentes\openmeteo.py";     args = @("--loop") }
)

$procesos = @()
foreach ($s in $Servicios) {
    if (-not $Stub -and -not (Test-Path $s.script)) {
        Write-Host ("  ! {0}: falta {1} (otro dev lo escribe en paralelo) - se omite" -f $s.name, $s.script) -ForegroundColor Yellow
        continue
    }

    $log = Join-Path $Tmp ("demo_{0}.log" -f $s.name)
    $errLog = Join-Path $Tmp ("demo_{0}.err.log" -f $s.name)

    if ($Stub) {
        # Codigo SIN espacios a proposito: Start-Process concatena ArgumentList sin
        # comillas y un argv con espacios se romperia.
        $argumentos = @("-c", "__import__('time').sleep(30)")
    } else {
        $argumentos = @($s.script) + @($s.args)
    }

    $p = Start-Process -FilePath $PyVenv -ArgumentList $argumentos -WorkingDirectory $Raiz `
        -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $log -RedirectStandardError $errLog

    $procesos += [pscustomobject]@{
        name         = $s.name
        pid          = $p.Id
        started_at   = (Get-Date).ToString("s")
        script       = $(if ($Stub) { "stub" } else { $s.script })
        process_name = $p.ProcessName
        log          = $log
    }
    Write-Host ("  + {0,-9} PID {1}" -f $s.name, $p.Id) -ForegroundColor Gray
}

if ($procesos.Count -eq 0) {
    Write-Host "ERROR: no se lanzo ningun proceso (faltan todos los modulos). Nada que registrar." -ForegroundColor Red
    exit 1
}

# Comprobacion temprana: un proceso que muere en el primer segundo es un fallo de arranque.
Start-Sleep -Milliseconds 1200
$muertos = @()
foreach ($proc in $procesos) {
    if (-not (Get-Process -Id $proc.pid -ErrorAction SilentlyContinue)) { $muertos += $proc.name }
}
if ($muertos.Count -gt 0) {
    Write-Host ("  WARN: proceso(s) que murieron al arrancar: {0}" -f ($muertos -join ", ")) -ForegroundColor Yellow
    Write-Host ("  Revisa logs: .tmp\demo_<name>.err.log (ver .tmp\demo_<name>.log)") -ForegroundColor Yellow
}

# Persistir PIDs (solo estos los apaga stop_demo.ps1).
@{
    iniciado_en = (Get-Date).ToString("s")
    stub        = [bool]$Stub
    dashboard   = "http://127.0.0.1:8000"
    procesos    = @($procesos)
} | ConvertTo-Json -Depth 4 | Set-Content -Path $PidsArchivo -Encoding UTF8

Write-Host ""
Write-Host "=== DEMO AULASENSE ARRIBA ===" -ForegroundColor Green
Write-Host "  Dashboard : http://127.0.0.1:8000  (solo local; bind 127.0.0.1)"
Write-Host "  Open-Meteo: escribe en server\datos\aulasense.db -> fuentes_externas (degrada sin coords)"
if ($Stub) { Write-Host "  MODO STUB : 3 procesos python -c sleep(30) (prueba de mecanica, sin servicios reales)" -ForegroundColor Yellow }
Write-Host ("  PIDs      : {0}" -f (($procesos | ForEach-Object { "{0}={1}" -f $_.name, $_.pid }) -join ", "))
Write-Host "  Logs      : .tmp\demo_*.log"
Write-Host "  APAGAR    : powershell tools\stop_demo.ps1" -ForegroundColor Yellow
exit 0