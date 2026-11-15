# F5a - Punto unico del analisis de AulaSense (C5). Ruta fijada por PLAN.md F5a.
# Ejecuta en orden: analizar_dataset.py -> eda.py -> modelo_ocupacion.py.
# Las figuras se REGENERAN dentro de cada script (no hay paso separado; -NoFiguras las omite).
# Validacion de entorno primero: python 3.15 (el del PATH esta roto, regla 3 AGENTS) +
# reachability WSL (BD canonica en modo solo-lectura).
# Salida: OK/FALLO por paso; exit 0 solo si TODOS los pasos ejecutados terminan OK.
#
# Uso (desde cualquier cwd):
#   powershell analysis/run_analisis.ps1
#   powershell analysis/run_analisis.ps1 -SinWsl      # smoke offline (modelo se omite: exige BD)
#   powershell analysis/run_analisis.ps1 -NoFiguras   # solo metricas
# (ASCII puro: Windows PowerShell 5.1 lee los .ps1 sin BOM como ANSI y rompe los acentos)
param(
    [string]$Python = "C:\Python315\python.exe",
    [string]$Distro = "debian-aulasense",
    [switch]$SinWsl,
    [switch]$NoFiguras
)

$ErrorActionPreference = "Continue"
$RAIZ = Split-Path -Parent $PSScriptRoot          # aulasense/
Set-Location $RAIZ
$resultados = @()

function Add-Resultado([string]$paso, [string]$estado, [string]$detalle) {
    $script:resultados += [pscustomobject]@{ Paso = $paso; Estado = $estado; Detalle = $detalle }
    Write-Host ("[{0}] {1} :: {2}" -f $estado, $paso, $detalle)
}

# ---------------------------------------------------------------- validacion de entorno
Write-Host "== Validacion de entorno =="
if (-not (Test-Path $Python)) {
    Add-Resultado "entorno:python" "FALLO" "no existe $Python (regla 3 AGENTS: python del PATH roto)"
    $resultados | Format-Table -AutoSize
    exit 1
}
$pyVer = & $Python --version 2>&1
Add-Resultado "entorno:python" "OK" "$pyVer ($Python)"

& $Python -c "import pandas, sklearn, matplotlib, numpy" 2>$null
if ($LASTEXITCODE -ne 0) {
    Add-Resultado "entorno:libs" "FALLO" "faltan pandas/sklearn/matplotlib en $Python"
    $resultados | Format-Table -AutoSize
    exit 1
}
Add-Resultado "entorno:libs" "OK" "pandas + sklearn + matplotlib + numpy importables"

if ($SinWsl) {
    Add-Resultado "entorno:wsl" "OMITIDO" "-SinWsl activo (analisis parcial offline)"
} else {
    # sin comillas internas: PS 5.1 las rompe al re-citar para nativos; -V basta
    $wslProbe = (wsl -d $Distro -u root -- python3 -V 2>&1) -join " "
    if ($LASTEXITCODE -eq 0 -and $wslProbe -match "Python 3") {
        Add-Resultado "entorno:wsl" "OK" "distro $Distro alcanzable ($wslProbe)"
    } else {
        Add-Resultado "entorno:wsl" "FALLO" "wsl -d $Distro no responde: $wslProbe"
        $resultados | Format-Table -AutoSize
        exit 1
    }
}

# ---------------------------------------------------------------- pasos del analisis
Write-Host "`n== Paso 1/3: pipeline base (analizar_dataset.py) =="
$a1 = @("analysis/analizar_dataset.py")
if ($NoFiguras) { $a1 += "--no-graficos" }
if ($SinWsl) { $a1 += "--sin-wsl" }
& $Python @a1
if ($LASTEXITCODE -eq 0) { Add-Resultado "1:pipeline" "OK" "docs/analisis/analisis.json + informe_metricas.md + 3 figuras base" }
else { Add-Resultado "1:pipeline" "FALLO" "exit $LASTEXITCODE" }

Write-Host "`n== Paso 2/3: EDA formal (eda.py) =="
$a2 = @("analysis/eda.py")
if ($NoFiguras) { $a2 += "--no-figuras" }
if ($SinWsl) { $a2 += "--sin-wsl" }
& $Python @a2
if ($LASTEXITCODE -eq 0) { Add-Resultado "2:eda" "OK" "analysis/informe_eda.md + docs/analisis/eda.json + 5 figuras eda_*" }
else { Add-Resultado "2:eda" "FALLO" "exit $LASTEXITCODE" }

Write-Host "`n== Paso 3/3: modelo/indicador de ocupacion (modelo_ocupacion.py) =="
if ($SinWsl) {
    Add-Resultado "3:modelo" "OMITIDO" "el modelo entrena contra la BD real (WSL ro); usar sin -SinWsl"
} else {
    $a3 = @("analysis/modelo_ocupacion.py")
    if ($NoFiguras) { $a3 += "--no-figuras" }
    & $Python @a3
    if ($LASTEXITCODE -eq 0) { Add-Resultado "3:modelo" "OK" "docs/analisis/modelo_ocupacion.json + 3 figuras ocupacion_*" }
    else { Add-Resultado "3:modelo" "FALLO" "exit $LASTEXITCODE" }
}

# ---------------------------------------------------------------- resumen
Write-Host "`n== Resumen =="
$resultados | Format-Table -AutoSize
$fallidos = @($resultados | Where-Object { $_.Estado -eq "FALLO" })
if ($fallidos.Count -gt 0) {
    Write-Host ("RESULTADO GLOBAL: FALLO ({0} paso(s))" -f $fallidos.Count)
    exit 1
}
Write-Host "RESULTADO GLOBAL: OK - figuras regeneradas por cada script (11 en docs/analisis/graficos)"
exit 0
