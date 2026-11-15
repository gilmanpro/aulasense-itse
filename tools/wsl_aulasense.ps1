# wsl_aulasense.ps1 - Control del stack AulaSense dentro de WSL Debian 13 (F25, SPEC §15.1).
#
# Runtime PRIMARIO del server (ingesta + dashboard + vision) = systemd de la distro `Debian`.
# Este script es el envoltorio Windows: NO arranca procesos en Windows ni toca el stack
# Windows (eso es start_demo/stop_demo.ps1). Nunca conviven los dos (split-brain prohibido,
# AGENTS.md regla 3): antes de `start`, para el stack Windows con tools\stop_demo.ps1.
#
# Fallback documentado (D-F25-3 / skill wsl-manager-cli): `wsl-manager` NO esta en PATH en
# esta maquina, asi que se invoca `wsl.exe` directamente. Si algun dia aparece el CLI, sus
# equivalentes son 1:1 con los verbos de aqui.
#
# Verbos:
#   status             systemctl is-active de las 3 units + URL efectiva del dashboard
#   url                solo la URL efectiva (127.0.0.1:8000 si responde; si no, WSL_IP:8000)
#   start              enable --now ingesta y luego dashboard (ingesta PRIMERO)
#   stop               stop de dashboard, ingesta y vision (orden inverso)
#   logs [unit] [n]    journalctl -n N --no-pager de una unit (por defecto las 3, 50 lineas)
#   refresh-portproxy  imprime el comando netsh EXACTO a ejecutar una vez como admin
#
# Sin admin (situacion actual): portproxy NO aplicable; via activa = fallback NAT
# http://<WSL_IP>:8000 (la unit ya lleva AULASENSE_ORIGINS_EXTRA con ese host:puerto).
# Este script NUNCA lee ni imprime .env, claves ni credenciales.
#
# Codigos de salida (F25-auditoria): 0 = OK/informativo; 1 = fallo REAL de la accion
# (start no dejo active una unit, stop dejo algo corriendo, url sin respuesta,
# refresh-portproxy sin WSL_IP). El stack Windows usa exit code para encadenar.
#
# Uso: powershell tools\wsl_aulasense.ps1 status
#      powershell tools\wsl_aulasense.ps1 start -Distro debian-aulasense   (nombrado)
param(
    [Parameter(Position = 0)]
    [ValidateSet("start", "stop", "status", "logs", "refresh-portproxy", "url")]
    [string]$Accion = "status",
    [Parameter(Position = 1)]
    [string]$Unidad = "",
    [Parameter(Position = 2)]
    [ValidateRange(1, 10000)]
    [int]$Lineas = 50,
    # Parametrizado (antes vivia en una variable y el comentario mandaba "ajustar -Distro"
    # que no existia). Default intacto: Debian = stack F25-b1 de puerto 8000.
    [string]$Distro = "Debian"
)

$ErrorActionPreference = "Stop"

$Usuario = "aulasense"
$Units = @("aulasense-ingesta.service", "aulasense-dashboard.service", "aulasense-vision.service")
$PuertoDashboard = 8000

function Assert-Wsl {
    $distros = (& wsl.exe -l -q) -replace "`0", ""
    if (-not ($distros | Where-Object { $_.Trim() -eq $Distro })) {
        Write-Host "La distro '$Distro' no existe. Instalala o pasa -Distro <nombre> a este script." -ForegroundColor Red
        exit 1
    }
}

# Ejecuta un comando dentro de WSL como el usuario dedicado (sin password: adduser
# --disabled-password). -u root solo se usa cuando el verbo necesita privilegios
# (systemctl enable/stop, journalctl: aulasense no esta en systemd-journal).
function Invoke-Wsl {
    param([string[]]$Argumentos, [switch]$ComoRoot)
    $base = @("-d", $Distro)
    if ($ComoRoot) { $base += @("-u", "root") } else { $base += @("-u", $Usuario) }
    $base += @("--")
    $base += $Argumentos
    # PS 5.1 + ErrorActionPreference=Stop + 2>&1 sobre un nativo convierten el stderr en
    # NativeCommandError TERMINANTE (p. ej. un `systemctl enable --now` fallido abortaba
    # el verbo con un mensaje confuso en vez de leer el Codigo). Se fuerza Continue
    # dentro y se stringuifican los registros de error antes de propagar.
    $eapPrev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $salida = & wsl.exe @base 2>&1 | ForEach-Object { "$_" }
        $codigo = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $eapPrev
    }
    return @{ Codigo = $codigo; Salida = @($salida) }
}

function Get-WslIp {
    # hostname -I no necesita root: se pide como el usuario dedicado (privilegio minimo;
    # antes se invocaba -ComoRoot sin necesidad).
    $r = Invoke-Wsl -Argumentos @("hostname", "-I")
    $ip = ("{0}" -f ($r.Salida -join " ")).Trim().Split(" ")[0]
    return $ip
}

function Get-Activo {
    param([string]$UnidadNombre)
    $r = Invoke-Wsl -Argumentos @("systemctl", "is-active", $UnidadNombre)
    return ("{0}" -f ($r.Salida -join " ")).Trim()
}

function Test-Url {
    param([string]$Url)
    try {
        $resp = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 5
        return [int]$resp.StatusCode
    } catch {
        # Error HTTP (4xx/5xx): devolvemos el codigo real, no 0.
        $res = $_.Exception.Response
        if ($res -and $res.StatusCode) { return [int]$res.StatusCode }
        return 0
    }
}

function Show-Url {
    # Orden pedido por SPEC §15.1: primero loopback (portproxy/cutover b2), luego NAT.
    # Healthcheck desde F25-e: sonda /login (200). '/' responde 302->/login sin cookie,
    # y el stack WINDOWS pre-F25-e vivo en :8000 responde 404 en /login -> discriminador limpio.
    $codigo = Test-Url ("http://127.0.0.1:{0}/login" -f $PuertoDashboard)
    $ip = Get-WslIp
    $urlNat = if ($ip) { "http://{0}:{1}/" -f $ip, $PuertoDashboard } else { "" }
    $codigoNat = if ($ip) { Test-Url ("http://{0}:{1}/login" -f $ip, $PuertoDashboard) } else { 0 }
    if ($codigo -eq 200 -or $codigo -eq 302) {
        $aviso = if ($codigoNat -eq 0) {
            "loopback OK y NAT sin respuesta: revisa que la unit del dashboard este active."
        } else { "" }
        return @{
            Url = ("http://127.0.0.1:{0}/" -f $PuertoDashboard)
            Via = "loopback (portproxy b2 o localhost forwarding)"
            Codigo = $codigo
            Nat = $urlNat
            NatCodigo = $codigoNat
            Aviso = $aviso
        }
    }
    if (-not $ip) {
        return @{ Url = "(sin IP)"; Via = "no se pudo leer hostname -I"; Codigo = 0; Nat = ""; NatCodigo = 0; Aviso = "" }
    }
    $avisoNat = if ($codigo -eq 404 -or $codigo -eq 200) {
        "OJO: /login en loopback responde $codigo => el :8000 de Windows NO es el dashboard WSL (stack Windows vivo o portproxy sin aplicar). Via efectiva = NAT hasta el cutover b2."
    } elseif ($codigo -eq 0 -and $codigoNat -eq 200) {
        "loopback :8000 sin respuesta y NAT OK: via fallback activa (portproxy pendiente, normal en b1)."
    } else { "" }
    return @{
        Url = $urlNat
        Via = "FALLBACK NAT (sin admin, portproxy pendiente)"
        Codigo = $codigoNat
        Nat = $urlNat
        NatCodigo = $codigoNat
        Aviso = $avisoNat
    }
}

function Format-Salida {
    param($r)
    return (($r.Salida | ForEach-Object { "{0}" -f $_ }) -join "`n").Trim()
}

Assert-Wsl

switch ($Accion) {
    "status" {
        Write-Host "=== AulaSense WSL ($Distro) ===" -ForegroundColor Cyan
        foreach ($u in $Units) {
            $estado = Get-Activo $u
            $color = if ($estado -eq "active") { "Green" } elseif ($u -like "*vision*" -and $estado -eq "inactive") { "Yellow" } else { "Red" }
            Write-Host ("  {0,-32} {1}" -f $u, $estado) -ForegroundColor $color
        }
        $u = Show-Url
        Write-Host ("  URL efectiva : {0} [HTTP {1}]" -f $u.Url, $u.Codigo) -ForegroundColor Gray
        Write-Host ("  Via          : {0}" -f $u.Via) -ForegroundColor Gray
        if ($u.Nat) { Write-Host ("  URL NAT WSL  : {0} [HTTP {1}]" -f $u.Nat, $u.NatCodigo) -ForegroundColor Gray }
        $ip = Get-WslIp
        Write-Host ("  WSL_IP       : {0}" -f $ip) -ForegroundColor Gray
        if ($u.Aviso) { Write-Host ("  " + $u.Aviso) -ForegroundColor Yellow }
        Write-Host "  Nota: aulasense-vision disabled por defecto (modo A exige sk-* pay-as-you-go; con sk-sp- ToS rechaza; usar modo B: tools/vision_describe.py --once)." -ForegroundColor Yellow
        exit 0
    }
    "url" {
        $u = Show-Url
        Write-Host ("{0} [{1}] ({2})" -f $u.Url, $u.Codigo, $u.Via)
        if ($u.Nat) { Write-Host ("NAT: {0} [{1}]" -f $u.Nat, $u.NatCodigo) }
        if ($u.Aviso) { Write-Host $u.Aviso -ForegroundColor Yellow }
        if ($u.Codigo -eq 200 -or $u.Codigo -eq 302 -or $u.NatCodigo -eq 200 -or $u.NatCodigo -eq 302) { exit 0 } else { exit 1 }
    }
    "start" {
        Write-Host "Arrancando stack WSL (ingesta PRIMERO). Recuerda: el stack Windows debe estar PARADO." -ForegroundColor Cyan
        foreach ($u in $Units[0..1]) {
            $r = Invoke-Wsl -Argumentos @("systemctl", "enable", "--now", $u) -ComoRoot
            Write-Host ("  {0}: {1}" -f $u, (Format-Salida $r)) -ForegroundColor Gray
        }
        $fallidas = @()
        foreach ($u in $Units) {
            $estado = Get-Activo $u
            Write-Host ("  {0,-32} {1}" -f $u, $estado)
            # Solo ingesta+dashboard son obligatoriamente active aqui; vision queda
            # inactive por diseño (ToS, modo B) y NO cuenta como fallo.
            if ($u -ne $Units[2] -and $estado -ne "active") { $fallidas += $u }
        }
        $u = Show-Url
        Write-Host ("  URL efectiva : {0} [HTTP {1}]" -f $u.Url, $u.Codigo) -ForegroundColor Gray
        if ($u.Nat) { Write-Host ("  URL NAT WSL  : {0} [HTTP {1}]" -f $u.Nat, $u.NatCodigo) -ForegroundColor Gray }
        if ($u.Aviso) { Write-Host ("  " + $u.Aviso) -ForegroundColor Yellow }
        if ($fallidas.Count -gt 0) {
            Write-Host ("FALLO: no quedaron active -> {0}. Ver: powershell tools\wsl_aulasense.ps1 logs <unit>" -f ($fallidas -join ", ")) -ForegroundColor Red
            exit 1
        }
        exit 0
    }
    "stop" {
        Write-Host "Parando stack WSL (orden inverso: vision, dashboard, ingesta)." -ForegroundColor Cyan
        foreach ($u in @($Units[2], $Units[1], $Units[0])) {
            $r = Invoke-Wsl -Argumentos @("systemctl", "stop", $u) -ComoRoot
            Write-Host ("  {0}: {1}" -f $u, (Format-Salida $r)) -ForegroundColor Gray
        }
        $vivas = @()
        foreach ($u in $Units) {
            $estado = Get-Activo $u
            Write-Host ("  {0,-32} {1}" -f $u, $estado)
            if ($estado -in @("active", "activating", "reloading")) { $vivas += $u }
        }
        Write-Host "  OJO: 'stop' NO deshabilita el autoarranque: las units habilitadas vuelven al boot de la distro. Antes del rollback a Windows (start_demo.ps1) ejecuta ADEMAS (root, evita split-brain AGENTS.md regla 3): wsl -d $Distro -u root -- systemctl disable aulasense-ingesta.service aulasense-dashboard.service" -ForegroundColor Yellow
        if ($vivas.Count -gt 0) {
            Write-Host ("FALLO: siguen corriendo -> {0}" -f ($vivas -join ", ")) -ForegroundColor Red
            exit 1
        }
        exit 0
    }
    "logs" {
        # journalctl se lee como root (el usuario aulasense no esta en systemd-journal).
        # $Unidad SOLO admite las 3 units conocidas (con o sin .service): el verbo corre
        # journalctl como root y NO debe servir para leer el journal de otras units.
        $objetivo = $Units
        if ($Unidad) {
            $candidato = if ($Unidad -like "*.service") { $Unidad } else { "$Unidad.service" }
            if ($candidato -notin $Units) {
                Write-Host ("Unidad no permitida: '$Unidad'. Validas: {0}" -f (($Units | ForEach-Object { $_ -replace "\.service$", "" }) -join ", ")) -ForegroundColor Red
                exit 1
            }
            $objetivo = @($candidato)
        }
        foreach ($u in $objetivo) {
            Write-Host ("=== {0} (ultimas {1}) ===" -f $u, $Lineas) -ForegroundColor Cyan
            $r = Invoke-Wsl -Argumentos @("journalctl", "-u", $u, "-n", "$Lineas", "--no-pager") -ComoRoot
            Write-Host (Format-Salida $r)
        }
        exit 0
    }
    "refresh-portproxy" {
        # WSL_IP cambia tras reboot de la VM: el portproxy debe reapuntarse.
        $ip = Get-WslIp
        if ([string]::IsNullOrWhiteSpace($ip)) {
            Write-Host "FALLO: no se pudo leer la WSL_IP (hostname -I vacio: distro parada o sin red). Nada que refrescar." -ForegroundColor Red
            exit 1
        }
        $instrucciones = @(
            "Refresh del portproxy REQUIERE consola elevada (Administrador). Sin admin NO aplicable.",
            "Condicion previa: stack Windows PARADO (el puerto 127.0.0.1:$PuertoDashboard lo ocupa hoy).",
            "Ejecuta UNA VEZ en PowerShell como Administrador:",
            "",
            "  netsh interface portproxy delete v4tov4 listenaddress=127.0.0.1 listenport=$PuertoDashboard",
            "  netsh interface portproxy add v4tov4 listenaddress=127.0.0.1 listenport=$PuertoDashboard connectaddress=$ip connectport=$PuertoDashboard",
            "",
            ("Estado actual: WSL_IP = {0} (puede cambiar tras cada reboot de la VM)." -f $ip),
            "Verificacion: netsh interface portproxy show v4tov4",
            "Nota: la IP 127.0.0.1 del portproxy NO se annade a AULASENSE_ORIGINS_EXTRA (ya es default de la allowlist)."
        )
        $esAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
        foreach ($l in $instrucciones) { Write-Host $l }
        if (-not $esAdmin) {
            Write-Host "`nSesion actual SIN privilegios: no se aplico nada. Via activa = FALLBACK NAT http://$ip`:$PuertoDashboard" -ForegroundColor Yellow
        }
        exit 0
    }
}