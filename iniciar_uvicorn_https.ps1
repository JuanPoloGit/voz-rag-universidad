# Script de diagnóstico para iniciar Django con Uvicorn y HTTPS nativo para AudacIA

param(
    [string]$HostIp = "0.0.0.0",
    [int]$Puerto = 8000
)

$scriptDir = $PSScriptRoot
$venvPython = Join-Path $scriptDir ".venv\Scripts\python.exe"
$appDir = Join-Path $scriptDir "sistema_inventario"
$certFile = Join-Path $scriptDir "red\certificados\cert.pem"
$keyFile = Join-Path $scriptDir "red\certificados\key.pem"

if (-not (Test-Path $certFile) -or -not (Test-Path $keyFile)) {
    Write-Error "No se encontraron los certificados en red\certificados\"
    exit 1
}

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host " AUDACIA -- SERVIDOR DJANGO + UVICORN + HTTPS" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "  Escuchando en:  https://${HostIp}:${Puerto}/" -ForegroundColor Green
Write-Host "  Acceso LAN:     https://172.16.130.73:${Puerto}/" -ForegroundColor Green
Write-Host "  Panel Haku:     https://172.16.130.73:${Puerto}/api/haku/panel/" -ForegroundColor Green
Write-Host "  Admin Login:    https://172.16.130.73:${Puerto}/admin/login/" -ForegroundColor Green
Write-Host "----------------------------------------------------------" -ForegroundColor DarkGray
Write-Host "Presiona Ctrl+C para detener el servidor." -ForegroundColor Yellow
Write-Host "----------------------------------------------------------" -ForegroundColor DarkGray

& $venvPython -m uvicorn inventario_audacia.asgi:application `
    --app-dir $appDir `
    --host $HostIp `
    --port $Puerto `
    --ssl-certfile $certFile `
    --ssl-keyfile $keyFile
