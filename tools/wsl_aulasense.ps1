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
#   salud              F26 (SPEC 16.7): /api/salud con sesion + sondas + tuneles wsl-port
#                      (SOLO campos id/state/remote/auto_start; NUNCA el objeto completo)
#   refresh-portproxy  imprime el comando netsh EXACTO a ejecutar una vez como admin
#
# Sin admin (situacion actual): portproxy NO aplicable; via activa = fallback NAT
# http://<WSL_IP>:8000 (la unit ya lleva AULASENSE_ORIGINS_EXTRA con ese host:puerto).
# Este script NUNCA IMPRIME credenciales: el verbo salud LEE el .env UNICO de la raiz
# solo para autenticarse en /login (valores jamas van a stdout); el resto sigue sin
# leer ni mostrar secretos.
#
# Codigos de salida (F25-auditoria): 0 = OK/informativo; 1 = fallo REAL de la accion
# (start no dejo active una unit, stop dejo algo corriendo, url sin respuesta,
# refresh-portproxy sin WSL_IP). El stack Windows usa exit code para encadenar.
#
# Uso: powershell tools\wsl_aulasense.ps1 status
#      powershell tools\wsl_aulasense.ps1 start -Distro debian-aulasense   (nombrado)
param(
    [Parameter(Position = 0)]
    [ValidateSet("start", "stop", "status", "logs", "refresh-portproxy", "url", "salud")]
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
# F26 verbo salud: dashboard CANONICO en debian-aulasense = :8100 interno (SPEC 16.7/16.10).
$PuertoInterno = 8100
$RutaEnvUnico = Join-Path (Split-Path $PSScriptRoot -Parent) ".env"
$WslPortExe = "C:\Users\Gilman\Desktop\APPplicaciones\05-WSL-Redes\wsl-port\wsl+ssh\wsl-port-unified\.venv\Scripts\wsl-port.exe"
$TunelesSalud = @("pub-aulasense-8100", "pub-aulasense-ota")

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

function Test-UrlExterno {
    # sondas PUBLICAS (VPS): curl con --noproxy — el proxy del sistema intermedia y
    # congela Invoke-WebRequest hacia 167.114.169.134 (hallazgo F26-b verbo salud).
    # Devuelve [int] del codigo HTTP; 0 = sin respuesta (incluye curl 000/errores).
    param([string]$Url)
    $eapPrev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $cod = & curl.exe -sS -o NUL -w "%{http_code}" --noproxy "*" --max-time 10 $Url 2>$null
    } catch { $cod = "000" } finally { $ErrorActionPreference = $eapPrev }
    $n = 0
    if ([int]::TryParse("$cod", [ref]$n)) { return $n }
    return 0
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

# ---------------------------------------------------------------------------
# F26-b verbo salud (SPEC 16.7 / D-F26-8): agrega systemctl + /api/salud (con
# sesion via /login, credenciales del .env UNICO JAMAS impresas) + sondas
# locales/publicas + tuneles wsl-port (SOLO id/state/remote/auto_start).
# ---------------------------------------------------------------------------
function Get-EnvValor {
    # Lee UNA clave del .env UNICO de la raiz SIN imprimirla nunca. Si la clave
    # no esta, se ignora (los seams AULASENSE_*_ENV de tests no aplican aqui).
    param([string]$Nombre)
    if (-not (Test-Path $RutaEnvUnico)) { return "" }
    $linea = Select-String -Path $RutaEnvUnico -Pattern ("^{0}=" -f [regex]::Escape($Nombre)) |
             Select-Object -First 1
    if (-not $linea) { return "" }
    return ($linea.Line -split "=", 2)[1].Trim()
}

function Test-UrlConSesion {
    # Sonda GET con la cookie de sesion ya iniciada; devuelve [int] o 0.
    param([string]$Url, $Sesion)
    try {
        $resp = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 5 -WebSession $Sesion
        return [int]$resp.StatusCode
    } catch {
        $res = $_.Exception.Response
        if ($res -and $res.StatusCode) { return [int]$res.StatusCode }
        return 0
    }
}

function New-SesionDashboard {
    # POST /login con credenciales del .env UNICO (valores NUNCA impresos).
    # Devuelve @{Sesion=...; Estado='ok'|'sin-credenciales'|'login-fallo:<cod>'|'sin-respuesta'}.
    param([string]$Base)
    $usuario = Get-EnvValor "AULASENSE_ADMIN_USER"
    $pass = Get-EnvValor "AULASENSE_ADMIN_PASSWORD"
    if (-not $usuario -or -not $pass) {
        return @{ Sesion = $null; Estado = "sin-credenciales" }
    }
    try {
        $sess = $null
        $resp = Invoke-WebRequest -Uri ($Base + "/login") -Method POST -UseBasicParsing `
            -ContentType "application/json" -Body (@{ username = $usuario; password = $pass } | ConvertTo-Json) `
            -SessionVariable sess -TimeoutSec 8
        $code = [int]$resp.StatusCode
    } catch {
        $res = $_.Exception.Response
        if ($res -and $res.StatusCode) {
            return @{ Sesion = $null; Estado = ("login-fallo:{0}" -f [int]$res.StatusCode) }
        }
        return @{ Sesion = $null; Estado = "sin-respuesta" }
    }
    if ($code -ne 200) { return @{ Sesion = $null; Estado = ("login-fallo:{0}" -f $code) } }
    return @{ Sesion = $sess; Estado = "ok" }
}

function Get-TunelesSalud {
    # wsl-port --json status: SOLO campos id/state/remote/auto_start de los tuneles
    # del proyecto. El objeto completo contiene datos de acceso (lesson pass VPS):
    # nunca se serializa aqui ni se escribe en disco.
    if (-not (Test-Path $WslPortExe)) { return @() }
    $eapPrev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $txt = & $WslPortExe --json status 2>$null | Out-String
    } catch { $txt = "" } finally { $ErrorActionPreference = $eapPrev }
    if (-not $txt) { return @() }
    try { $j = $txt | ConvertFrom-Json } catch { return @() }
    $fuente = @($j.tunnels)
    $res = @()
    foreach ($t in $fuente) {
        if ($null -eq $t) { continue }
        $id = if ($t.id) { $t.id } elseif ($t.name) { $t.name } else { "" }
        if ($id -notin $TunelesSalud) { continue }
        $remote = $t.remote
        if ($remote -is [array]) { $remote = ($remote -join ",") }
        $auto = $null
        if ($t.PSObject.Properties.Name -contains "auto_start") { $auto = [bool]$t.auto_start }
        elseif ($t.PSObject.Properties.Name -contains "autoStart") { $auto = [bool]$t.autoStart }
        $res += [ordered]@{
            id         = $id
            state      = "{0}" -f $t.state
            remote     = "{0}" -f $remote
            auto_start = $auto
        }
    }
    return $res
}

function Convert-ToWslPath {
    # C:\... -> /mnt/c/... (unica maquina con proyecto en C:, F26-b).
    param([string]$Win)
    return "/mnt/c" + ($Win.Substring(2) -replace '\\', '/')
}

function Set-TunelesEstadoArchivo {
    # Escribe server/datos/tuneles_estado.json (Windows) Y lo publica en el DATOS_DIR
    # de la distro (el panel /salud de WSL lee datos/tuneles_estado.json via
    # _datos_dir(); forma a2: {ts_ms, tuneles:[{id,state,remote,ts_ms}]}).
    # Solo el subconjunto seguro: NINGUN dato de acceso (ni auto_start en el archivo).
    param($Tuneles)
    $dir = Join-Path (Split-Path $PSScriptRoot -Parent) "server\datos"
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir | Out-Null }
    $ts = [DateTimeOffset]::Now.ToUnixTimeMilliseconds()
    $payload = @{ ts_ms = $ts; tuneles = @($Tuneles | ForEach-Object {
        [ordered]@{ id = $_.id; state = $_.state; remote = $_.remote; ts_ms = $ts }
    }) }
    $ruta = Join-Path $dir "tuneles_estado.json"
    # UTF-8 SIN BOM: PS 5.1 escribe BOM con -Encoding UTF8 y json.loads del dashboard
    # (read_text "utf-8") lo rechteria como "archivo ilegible" (verificado F26-b).
    $json = $payload | ConvertTo-Json -Depth 5
    [System.IO.File]::WriteAllText($ruta, $json, (New-Object System.Text.UTF8Encoding($false)))
    # Publicacion WSL (best-effort) en /home/aulasense/aulasense-datos (DATOS_DIR real).
    try {
        $jsonWsl = Convert-ToWslPath $ruta
        $tmpSh = Join-Path (Split-Path $PSScriptRoot -Parent) ".tmp\f26b_pub_tuneles.sh"
        @(
            "#!/bin/bash",
            "mkdir -p /home/aulasense/aulasense-datos 2>/dev/null || true",
            "cp '$jsonWsl' /home/aulasense/aulasense-datos/tuneles_estado.json 2>/dev/null && echo WSL-publicado || echo WSL-sin-permiso",
            "chmod 600 /home/aulasense/aulasense-datos/tuneles_estado.json 2>/dev/null || true"
        ) | Set-Content -Path $tmpSh -Encoding ASCII
        $r = Invoke-Wsl -Argumentos @("bash", (Convert-ToWslPath $tmpSh))
        Write-Host ("  tuneles_estado : " + (Format-Salida $r)) -ForegroundColor Gray
    } catch {
        Write-Host "  tuneles_estado : publicacion WSL fallo (el panel marcara 'sin datos' con frescura)" -ForegroundColor Yellow
    }
    return $ruta
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
    "salud" {
        # F26-b (SPEC 16.7 / D-F26-8): resumen de salud del sistema. Default: la
        # distro canONICA debian-aulasense (:8100 interno). Con -Distro explicito
        # se respeta lo indicado.
        if ($Distro -eq "Debian") { $Distro = "debian-aulasense" }
        Assert-Wsl
        Write-Host "=== AulaSense SALUD ($Distro) ===" -ForegroundColor Cyan

        # 1) Units (las 3 de siempre + OTA "si existe").
        $unidades = @($Units + "aulasense-ota.service")
        $estadosUnits = [ordered]@{}
        foreach ($u in $unidades) {
            $estado = Get-Activo $u
            if ($estado -eq "unknown") { $estado = "no-instalada" }
            $estadosUnits[$u] = $estado
            $color = if ($estado -eq "active") { "Green" }
                     elseif ($estado -in @("inactive", "no-instalada") -and $u -like "*vision*") { "Yellow" }
                     elseif ($estado -eq "inactive" -and $u -like "*ota*") { "Yellow" }
                     else { "Red" }
            Write-Host ("  {0,-32} {1}" -f $u, $estado) -ForegroundColor $color
        }

        # 2) Base local: loopback :8100 (localhost-forwarding) con fallback NAT.
        $base = "http://127.0.0.1:$PuertoInterno"
        $codigoLogin = Test-Url ($base + "/login")
        if ($codigoLogin -eq 0) {
            $ip = Get-WslIp
            if ($ip) {
                $base = "http://{0}:{1}" -f $ip, $PuertoInterno
                $codigoLogin = Test-Url ($base + "/login")
            }
        }
        Write-Host ("  base local     : {0} [/login HTTP {1}]" -f $base, $codigoLogin) -ForegroundColor Gray

        # 3) /api/salud DETRAS de login (D-F26-8; no es publico-local). Sesion con
        #    credenciales del .env UNICO - los valores nunca se imprimen.
        $estadoSalud = "no-ejecutado"
        $ses = New-SesionDashboard -Base $base
        if ($ses.Estado -eq "ok") {
            $codigo = Test-UrlConSesion ($base + "/api/salud") $ses.Sesion
            if ($codigo -eq 200) {
                $resp = Invoke-WebRequest -Uri ($base + "/api/salud") -UseBasicParsing -TimeoutSec 8 -WebSession $ses.Sesion
                $estadoSalud = "ok"
                Write-Host "  /api/salud (sesion): 200" -ForegroundColor Green
                Write-Host ("    " + (($resp.Content -replace '\s+', ' ').Trim())) -ForegroundColor Gray
            } elseif ($codigo -eq 404) {
                $estadoSalud = "endpoint-pendiente (a2/a3)"
                Write-Host "  /api/salud: 404 - el endpoint lo despliega a3 (pendiente)" -ForegroundColor Yellow
            } else {
                $estadoSalud = "http-{0}" -f $codigo
                Write-Host ("  /api/salud: HTTP {0}" -f $codigo) -ForegroundColor Red
            }
        } else {
            $estadoSalud = $ses.Estado
            Write-Host ("  /api/salud: sesion no obtenida ({0})" -f $ses.Estado) -ForegroundColor Yellow
        }

        # 4) Sondas sin cookie: /login 200 y /api/eventos 401, local y publico.
        $codEventosLocal = Test-Url ($base + "/api/eventos")
        $urlPublica = Get-EnvValor "DASHBOARD_URL_PUBLIC"
        if (-not $urlPublica) { $urlPublica = "http://167.114.169.134:10062" }
        $codLoginPub = Test-UrlExterno ($urlPublica + "/login")
        $codEventosPub = Test-UrlExterno ($urlPublica + "/api/eventos")
        Write-Host ("  sondas local   : /login {0} | /api/eventos {1} (esperado 200/401)" -f $codigoLogin, $codEventosLocal) -ForegroundColor Gray
        Write-Host ("  sondas publicas: {0} -> /login {1} | /api/eventos {2} (esperado 200/401)" -f $urlPublica, $codLoginPub, $codEventosPub) -ForegroundColor Gray

        # 5) OTA externa: URL del manifiesto del .env (si esta definida).
        $urlOta = Get-EnvValor "AULASENSE_OTA_MANIFEST_URL"
        if ($urlOta) {
            $codOta = Test-UrlExterno $urlOta
            Write-Host ("  OTA manifiesto : {0} -> HTTP {1}" -f $urlOta, $codOta) -ForegroundColor $(if ($codOta -eq 200) { "Green" } else { "Yellow" })
        } else {
            Write-Host "  OTA manifiesto : (AULASENSE_OTA_MANIFEST_URL vacia)" -ForegroundColor Yellow
        }

        # 6) Tuneles wsl-port: SOLO id/state/remote/auto_start. El JSON crudo del
        #    estado NUNCA se imprime ni se guarda (contiene datos de acceso).
        $tuneles = @(Get-TunelesSalud)
        foreach ($t in $tuneles) {
            Write-Host ("  tunel {0,-22} state={1} remote={2} auto_start={3}" -f $t.id, $t.state, $t.remote, $t.auto_start) -ForegroundColor $(if ($t.state -eq "running") { "Green" } else { "Red" })
        }
        $faltan = @($TunelesSalud | Where-Object { $_ -notin ($tuneles | ForEach-Object { $_.id }) })
        if ($faltan.Count -gt 0) {
            Write-Host ("  tuneles sin estado (no existen aun): {0}" -f ($faltan -join ", ")) -ForegroundColor Yellow
        }
        $rutaJson = $null
        if ($tuneles.Count -gt 0) {
            $rutaJson = Set-TunelesEstadoArchivo -Tuneles $tuneles
            Write-Host ("  escrito        : {0} (solo id/state/remote/ts_ms)" -f $rutaJson) -ForegroundColor Gray
        }

        # 7) Veredicto compacto (JSON solo con campos seguros; para lectores maquina).
        $resumen = [ordered]@{
            distro        = $Distro
            units         = $estadosUnits
            api_salud     = $estadoSalud
            login_local   = $codigoLogin
            eventos_local = $codEventosLocal
            login_publico = $codLoginPub
            eventos_publico = $codEventosPub
            ota_manifest  = if ($urlOta) { Test-UrlExterno $urlOta } else { $null }
            tuneles       = @($tuneles | ForEach-Object { [ordered]@{ id = $_.id; state = $_.state; remote = $_.remote; auto_start = $_.auto_start } })
        }
        Write-Host ("RESULTADO-SALUD " + ($resumen | ConvertTo-Json -Compress -Depth 5)) -ForegroundColor White

        $critico = ($estadosUnits["aulasense-dashboard.service"] -ne "active") -or ($codigoLogin -eq 0)
        exit $(if ($critico) { 1 } else { 0 })
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