# Script de PowerShell para iniciar el servidor dual de diagnóstico en primer plano o segundo plano

param(
    [string]$HostIp = "172.16.130.73",
    [switch]$SegundoPlano
)

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$servidorPy = Join-Path $scriptDir "servidor.py"
$certPath = Join-Path $scriptDir "certificados\cert.pem"
$keyPath = Join-Path $scriptDir "certificados\key.pem"

# Verificar certificados
if (-not (Test-Path $certPath) -or -not (Test-Path $keyPath)) {
    Write-Host "Certificados no encontrados. Ejecutando generar_certificados.ps1..." -ForegroundColor Yellow
    & (Join-Path $scriptDir "generar_certificados.ps1")
}

Write-Host "Iniciando servidores de diagnóstico en IP: $HostIp" -ForegroundColor Cyan
Write-Host "  HTTP:  http://${HostIp}:8765/" -ForegroundColor Green
Write-Host "  HTTPS: https://${HostIp}:8766/" -ForegroundColor Green

if ($SegundoPlano) {
    Write-Host "Iniciando en segundo plano (proceso independiente)..." -ForegroundColor Yellow
    $logPath = Join-Path $scriptDir "servidor.log"
    $errPath = Join-Path $scriptDir "servidor_err.log"
    $proc = Start-Process -FilePath "python" `
        -ArgumentList "`"$servidorPy`" --modo dual --host $HostIp" `
        -PassThru -NoNewWindow -RedirectStandardOutput $logPath -RedirectStandardError $errPath
    Write-Host "Servidor corriendo en segundo plano con PID $($proc.Id). Log: $logPath" -ForegroundColor Green
    Write-Host "Para detenerlo: powershell -File red/detener_servidores.ps1" -ForegroundColor Cyan
} else {
    Write-Host "Iniciando en primer plano (Ctrl+C para salir)..." -ForegroundColor Yellow
    python "$servidorPy" --modo dual --host $HostIp
}
