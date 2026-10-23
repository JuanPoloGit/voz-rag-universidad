# ==============================================================================
# Script de Inicio de Servidor Seguro HTTPS (Uvicorn ASGI) - Inventario AudacIA
# Implementacion de la Decision 8: Servidor HTTPS Opcion B (Uvicorn con TLS directo)
# ==============================================================================

[CmdletBinding()]
param (
    [string]$HostName = "0.0.0.0",
    [int]$Port = 8000,
    [switch]$Reload,
    [switch]$SinMigraciones
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ScriptsRoot = Split-Path -Parent $ScriptDir
$ProjectDir = Split-Path -Parent $ScriptsRoot
$VenvPython = Join-Path (Split-Path -Parent $ProjectDir) "venv\Scripts\python.exe"

if (-not (Test-Path $VenvPython)) {
    $VenvPython = "python"
}

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "   INVENTARIO AUDACIA - SERVIDOR HTTPS LOCAL (UVICORN)    " -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

# 0. Liberar el puerto si quedo un proceso huerfano previo
$Occupied = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue
if ($Occupied) {
    foreach ($conn in $Occupied) {
        if ($conn.OwningProcess -gt 4) {
            Write-Host "[INFO] Liberando puerto $Port (terminando proceso anterior PID $($conn.OwningProcess))..." -ForegroundColor Yellow
            Stop-Process -Id $conn.OwningProcess -Force -ErrorAction SilentlyContinue
        }
    }
    Start-Sleep -Milliseconds 800
}

# 1. Verificar certificados TLS
$CertsDir = Join-Path $ProjectDir "certs"
$CertFile = Join-Path $CertsDir "localhost.pem"
$KeyFile = Join-Path $CertsDir "localhost-key.pem"

if ((-not (Test-Path $CertFile)) -or (-not (Test-Path $KeyFile))) {
    Write-Host "[INFO] Certificados TLS no encontrados. Generandolos automaticamente..." -ForegroundColor Yellow
    $GenScript = Join-Path $ScriptDir "generar_certificados.ps1"
    & powershell -ExecutionPolicy Bypass -File $GenScript
}

# 2. Configurar variables de entorno para HTTPS local
$env:USE_SQLITE = "True"
$env:SESSION_COOKIE_SECURE = "True"
$env:CSRF_COOKIE_SECURE = "True"

# 3. Comprobacion del estado de la aplicacion Django
Set-Location $ProjectDir
Write-Host "[INFO] Verificando integridad de Django..." -ForegroundColor DarkGray
& $VenvPython manage.py check --database default 2>&1 | Out-Null

# 4. Iniciar servicio mDNS para publicar inventario-audacia.local en la LAN
$MdnsScript = Join-Path $ScriptDir "anunciar_mdns.py"
$MdnsProcess = $null
if (Test-Path $MdnsScript) {
    try {
        $MdnsProcess = Start-Process -FilePath $VenvPython -ArgumentList @($MdnsScript, "--port", "$Port") -WindowStyle Hidden -PassThru
        Write-Host " [OK] mDNS activo: anunciando 'https://inventario-audacia.local:$Port/' en la red local." -ForegroundColor Cyan
    } catch {
        Write-Host " [AVISO] No se pudo iniciar el servicio mDNS de fondo: $_" -ForegroundColor DarkGray
    }
}

# 5. Lanzar Uvicorn ASGI con terminacion TLS
Write-Host ""
Write-Host "----------------------------------------------------------" -ForegroundColor Green
Write-Host " [OK] Servidor HTTPS listo y disponible en:" -ForegroundColor Green
Write-Host "      Red Local (mDNS): https://inventario-audacia.local:${Port}/" -ForegroundColor White
Write-Host "      Equipo Local:     https://localhost:${Port}/" -ForegroundColor White
Write-Host " [i] Para detener el servidor presione Ctrl + C" -ForegroundColor DarkGray
Write-Host "----------------------------------------------------------" -ForegroundColor Green
Write-Host ""

try {
    if ($Reload) {
        & $VenvPython -m uvicorn inventario_audacia.asgi:application --host $HostName --port $Port --ssl-certfile $CertFile --ssl-keyfile $KeyFile --reload
    } else {
        & $VenvPython -m uvicorn inventario_audacia.asgi:application --host $HostName --port $Port --ssl-certfile $CertFile --ssl-keyfile $KeyFile
    }
} finally {
    if ($MdnsProcess -and (-not $MdnsProcess.HasExited)) {
        Write-Host "[INFO] Deteniendo servicio mDNS..." -ForegroundColor Yellow
        Stop-Process -Id $MdnsProcess.Id -Force -ErrorAction SilentlyContinue
    }
}
