# ==============================================================================
# Script para registrar el certificado TLS de desarrollo en Windows
# Permite eliminar la advertencia ERR_CERT_AUTHORITY_INVALID y tener candado seguro.
# ==============================================================================

[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ScriptsRoot = Split-Path -Parent $ScriptDir
$ProjectDir = Split-Path -Parent $ScriptsRoot
$CertFile = Join-Path $ProjectDir "certs\localhost.pem"

if (-not (Test-Path $CertFile)) {
    Write-Error "No se encontro el certificado en '$CertFile'. Ejecuta primero iniciar_sistema.ps1."
    exit 1
}

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "   INSTALACION DE CERTIFICADO TLS AUDACIA EN WINDOWS      " -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "[INFO] Importando '$CertFile' en Entidades de Certificacion Raiz de Confianza..." -ForegroundColor Cyan
Write-Host "[!] IMPORTANTE: Si Windows te muestra una ventana de 'Advertencia de seguridad', haz clic en 'Si'." -ForegroundColor Yellow
Write-Host ""

try {
    Import-Certificate -FilePath $CertFile -CertStoreLocation Cert:\CurrentUser\Root | Out-Null
    Write-Host "[OK] Certificado registrado con exito como Entidad Raiz Confiable." -ForegroundColor Green
    Write-Host "[TIP] Cierra completamente tu navegador (Chrome / Edge / Firefox) y vuelve a abrir:" -ForegroundColor White
    Write-Host "      https://localhost:8000/" -ForegroundColor Green
} catch {
    Write-Host "[AVISO] Error al importar mediante PowerShell: $_" -ForegroundColor Red
    Write-Host ""
    Write-Host "Instalacion Manual Alternativa:" -ForegroundColor Yellow
    Write-Host "1. Presiona Win+R y escribe: certmgr.msc" -ForegroundColor White
    Write-Host "2. Ve a 'Entidades de certificacion raiz de confianza' -> 'Certificados'." -ForegroundColor White
    Write-Host "3. Clic derecho -> 'Todas las tareas' -> 'Importar...' y selecciona:" -ForegroundColor White
    Write-Host "   $CertFile" -ForegroundColor Cyan
}
