# ==============================================================================
# Script de Generacion de Certificados TLS Locales - Inventario AudacIA
# Soporta mkcert (recomendado) y generador integrado para desarrollo local.
# ==============================================================================

[CmdletBinding()]
param (
    [string]$CertsDir,
    [switch]$InstalarConfianza
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ScriptsRoot = Split-Path -Parent $ScriptDir
$ProjectDir = Split-Path -Parent $ScriptsRoot
$VenvPython = Join-Path (Split-Path -Parent $ProjectDir) "venv\Scripts\python.exe"

if (-not (Test-Path $VenvPython)) {
    $VenvPython = "python"
}

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
    Write-Host " [OK] Certificados emitidos con mkcert en '$CertsDir'." -ForegroundColor Green
} else {
    Write-Host "[AVISO] mkcert no se encuentra instalado en el PATH del sistema." -ForegroundColor Yellow
    Write-Host "  -> Recomendacion para cero advertencias: winget install FiloSottile.mkcert" -ForegroundColor Yellow
    Write-Host "[INFO] Generando certificados TLS autofirmados locales con Python..." -ForegroundColor Cyan

    $PyScript = Join-Path $ScriptDir "generar_certificados_python.py"
    & $VenvPython $PyScript $CertsDir

    if ($InstalarConfianza) {
        try {
            Write-Host "[INFO] Registrando certificado en el almacen de entidades raiz de Windows..." -ForegroundColor Cyan
            Import-Certificate -FilePath $CertFile -CertStoreLocation Cert:\CurrentUser\Root | Out-Null
            Write-Host " [OK] Certificado registrado como confiable en Windows." -ForegroundColor Green
        } catch {
            Write-Host " [AVISO] Registro en almacen de certificados omitido o cancelado: $_" -ForegroundColor DarkGray
        }
    } else {
        Write-Host ""
        Write-Host " [TIP] Para registrar confianza en Windows y ver el candado cerrado en el navegador:" -ForegroundColor Yellow
        Write-Host "       powershell -ExecutionPolicy Bypass -File .\scripts\plataforma\generar_certificados.ps1 -InstalarConfianza" -ForegroundColor White
    }
}

if ((Test-Path $CertFile) -and (Test-Path $KeyFile)) {
    Write-Host ""
    Write-Host "----------------------------------------------------------" -ForegroundColor Green
    Write-Host " [LISTO] Certificados TLS listos para despliegue HTTPS:" -ForegroundColor Green
    Write-Host "         - Certificado: $CertFile" -ForegroundColor White
    Write-Host "         - Clave privada: $KeyFile" -ForegroundColor White
    Write-Host "----------------------------------------------------------" -ForegroundColor Green
} else {
    Write-Error "Error: No se pudieron generar los certificados en '$CertsDir'."
    exit 1
}
