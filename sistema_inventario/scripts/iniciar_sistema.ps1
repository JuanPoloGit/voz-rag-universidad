# ==============================================================================
# Script de Inicio de Servidor Seguro HTTPS (Uvicorn ASGI) — Inventario AudacIA
# Implementación de la Decisión 8: Servidor HTTPS Opción B (Uvicorn con TLS directo)
# ==============================================================================

[CmdletBinding()]
param (
    [string]$HostName = "127.0.0.1",
    [int]$Port = 8000,
    [switch]$Reload,
    [switch]$SinMigraciones
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $ScriptDir
$VenvPython = Join-Path (Split-Path -Parent $ProjectDir) "venv\Scripts\python.exe"

if (-not (Test-Path $VenvPython)) {
    $VenvPython = "python"
}

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "   INVENTARIO AUDACIA — SERVIDOR HTTPS LOCAL (UVICORN)    " -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

# 1. Verificar certificados TLS
$CertsDir = Join-Path $ProjectDir "certs"
$CertFile = Join-Path $CertsDir "localhost.pem"
$KeyFile = Join-Path $CertsDir "localhost-key.pem"

if ((-not (Test-Path $CertFile)) -or (-not (Test-Path $KeyFile))) {
    Write-Host "[INFO] Certificados TLS no encontrados. Generándolos automáticamente..." -ForegroundColor Yellow
    $GenScript = Join-Path $ScriptDir "generar_certificados.ps1"
    & powershell -ExecutionPolicy Bypass -File $GenScript
}

# 2. Configurar variables de entorno recomendadas para HTTPS
$env:USE_SQLITE = "True"
$env:SESSION_COOKIE_SECURE = "True"
$env:CSRF_COOKIE_SECURE = "True"

# 3. Comprobación de estado de la aplicación
Set-Location $ProjectDir
Write-Host "[INFO] Verificando integridad de la aplicación..." -ForegroundColor DarkGray
& $VenvPython manage.py check --database default
if ($LASTEXITCODE -ne 0) {
    Write-Warning "Advertencia durante la comprobación de Django (consulte la salida previa)."
}

# 4. Lanzar Uvicorn ASGI con terminación TLS
Write-Host ""
Write-Host "----------------------------------------------------------" -ForegroundColor Green
Write-Host " [✓] Servidor HTTPS listo y disponible en:" -ForegroundColor Green
Write-Host "     https://${HostName}:${Port}/" -ForegroundColor White
Write-Host "     https://localhost:${Port}/" -ForegroundColor White
Write-Host " [i] Para detener el servidor presione Ctrl + C" -ForegroundColor DarkGray
Write-Host "----------------------------------------------------------" -ForegroundColor Green
Write-Host ""

$UvicornArgs = @(
    "-m", "uvicorn",
    "inventario_audacia.asgi:application",
    "--host", $HostName,
    "--port", "$Port",
    "--ssl-certfile", $CertFile,
    "--ssl-keyfile", $KeyFile
)

if ($Reload) {
    $UvicornArgs += "--reload"
}

& $VenvPython $UvicornArgs
