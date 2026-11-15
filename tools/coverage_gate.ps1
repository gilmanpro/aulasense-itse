# Gate de cobertura de la suite COMPLETA de AulaSense (F8, 2026-11-15).
# Vive fuera de pyproject.toml/[tool.pytest.ini_options].addopts a proposito: las
# corridas parciales (server/tests 77 %, analysis/tests 42 %) y el venv WSL (sin
# pytest-cov) se romperian si el --cov fuese implicito. Ver .tmp/f8_coverage.md.
#
# Uso:  powershell tools/coverage_gate.ps1            # umbral 78 (medido: 86 %)
#       powershell tools/coverage_gate.ps1 -Umbral 80 # otro umbral puntual
param([int]$Umbral = 78)

$ErrorActionPreference = "Stop"
$raiz = Split-Path -Parent $PSScriptRoot
$py = Join-Path $raiz "server\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Error "No existe el venv Windows del server: $py"
    exit 2
}

Push-Location $raiz
try {
    & $py -m pytest -q --cov=server --cov=analysis --cov-report=term "--cov-fail-under=$Umbral"
    exit $LASTEXITCODE
} finally {
    Pop-Location
}