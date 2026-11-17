# check_parche_drift.ps1 — verificador del ESPEJO firmware/parche_aulasense <-> vendor Native (AGENTS regla 17).
#
# Compara, con Get-FileHash SHA256, los 8 fuentes propios (incl. app_ota.c/.h — F26,
# regla 17 actualizada) + Kconfig.projbuild entre firmware/parche_aulasense/ y
# firmware/espectre/src/cpp/frontend/native/app/main/.
# De CMakeLists.txt compara SOLO la lista SRCS (la cabecera del parche es un comentario
# canonico por convencion README §6; el resto del fichero es del vendor).
#
# Uso: powershell -File tools/check_parche_drift.ps1    -> exit 0 (espejo OK) | 1 (drift)
Set-StrictMode -Version Latest

$raiz = Split-Path -Parent $PSScriptRoot
$parche = Join-Path $raiz 'firmware\parche_aulasense'
$vendor = Join-Path $raiz 'firmware\espectre\src\cpp\frontend\native\app\main'
$diffs = [System.Collections.Generic.List[string]]::new()

function Get-SrcsLineas([string]$ruta) {
    $dentro = $false
    foreach ($linea in Get-Content -LiteralPath $ruta) {
        if ($linea -match '^\s*SRCS\s*$') { $dentro = $true; continue }
        if ($dentro -and $linea -match '^\s*(INCLUDE_DIRS|REQUIRES)\b') { break }
        if ($dentro -and $linea.Trim()) { $linea.Trim() }
    }
}

foreach ($f in 'app_imagen.c', 'app_imagen.h', 'app_feedback.c', 'app_feedback.h', 'app_provision.c', 'app_provision.h', 'app_ota.c', 'app_ota.h', 'Kconfig.projbuild') {
    $a = Join-Path $parche $f
    $b = Join-Path $vendor $f
    if (-not (Test-Path -LiteralPath $a)) { $diffs.Add("FALTA en parche: $f"); continue }
    if (-not (Test-Path -LiteralPath $b)) { $diffs.Add("FALTA en vendor: $f"); continue }
    $ha = (Get-FileHash -Algorithm SHA256 -LiteralPath $a).Hash
    $hb = (Get-FileHash -Algorithm SHA256 -LiteralPath $b).Hash
    if ($ha -ne $hb) { $diffs.Add("DIFF $f ($($ha.Substring(0, 12)) != $($hb.Substring(0, 12)))") }
}

$srcsParche = @(Get-SrcsLineas (Join-Path $parche 'CMakeLists.txt'))
$srcsVendor = @(Get-SrcsLineas (Join-Path $vendor 'CMakeLists.txt'))
if (Compare-Object $srcsParche $srcsVendor) {
    $diffs.Add("DIFF SRCS CMakeLists.txt (parche=[$(([string[]]$srcsParche) -join ', ')] vendor=[$(([string[]]$srcsVendor) -join ', ')])")
}

if ($diffs.Count -eq 0) {
    Write-Output 'ESPEJO OK: 8 fuentes (incl. app_ota.*) + Kconfig.projbuild (hash) + SRCS de CMakeLists coinciden'
    exit 0
}
Write-Output "DRIFT DETECTADO ($($diffs.Count) diferencia(s)):"
$diffs | ForEach-Object { Write-Output "  - $_" }
exit 1