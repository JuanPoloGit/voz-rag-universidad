# ==============================================================================
# Script de Generación de Certificados TLS Locales — Inventario AudacIA
# Soporta mkcert (recomendado) y generador integrado para desarrollo local.
# ==============================================================================

[CmdletBinding()]
param (
    [string]$CertsDir
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $ScriptDir
$VenvPython = Join-Path (Split-Path -Parent $ProjectDir) "venv\Scripts\python.exe"

if (-not $CertsDir) {
    $CertsDir = Join-Path $ProjectDir "certs"
}

if (-not (Test-Path $CertsDir)) {
    New-Item -ItemType Directory -Path $CertsDir -Force | Out-Null
}

$CertFile = Join-Path $CertsDir "localhost.pem"
$KeyFile = Join-Path $CertsDir "localhost-key.pem"

$Mkcert = Get-Command "mkcert" -ErrorAction SilentlyContinue

if ($Mkcert) {
    Write-Host "[INFO] Utilizando mkcert para generar certificados locales confiables..." -ForegroundColor Cyan
    & mkcert -install
    & mkcert -key-file $KeyFile -cert-file $CertFile localhost 127.0.0.1 ::1
    Write-Host "[ÉXITO] Certificados emitidos con mkcert en '$CertsDir'." -ForegroundColor Green
} else {
    Write-Host "[AVISO] mkcert no se encuentra instalado en el PATH del sistema." -ForegroundColor Yellow
    Write-Host "  -> Para validación de CA raíz automática en navegadores, puede instalarlo con:" -ForegroundColor Yellow
    Write-Host "     winget install FiloSottile.mkcert   (o choco install mkcert)" -ForegroundColor Yellow
    Write-Host "[INFO] Generando certificados TLS autofirmados locales de alta compatibilidad..." -ForegroundColor Cyan

    $PyScript = Join-Path $ScriptDir "generar_certificados_python.py"
    & $VenvPython $PyScript $CertsDir
}

if ((Test-Path $CertFile) -and (Test-Path $KeyFile)) {
    Write-Host "[LISTO] Certificados TLS listos para despliegue HTTPS en Uvicorn:" -ForegroundColor Green
    Write-Host "  - Certificado: $CertFile"
    Write-Host "  - Clave privada: $KeyFile"
} else {
    Write-Error "Error: No se pudieron generar los certificados en '$CertsDir'."
    exit 1
}
