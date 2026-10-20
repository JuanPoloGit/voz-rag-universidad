# ==============================================================================
# Script de Ejecución de Respaldo cada 2 Horas — Inventario AudacIA (Política A)
# Valida calendario laboral colombiano antes de ejecutar.
# ==============================================================================

[CmdletBinding()]
param (
    [switch]$Force
)

$ErrorActionPreference = "Stop"

# Ubicar raíz del proyecto
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ScriptsRoot = Split-Path -Parent $ScriptDir
$ProjectDir = Split-Path -Parent $ScriptsRoot
$VenvPython = Join-Path (Split-Path -Parent $ProjectDir) "venv\Scripts\python.exe"

if (-not (Test-Path $VenvPython)) {
    $VenvPython = "python"
}

# 1. Validar calendario laboral en Colombia (a menos que se use -Force)
if (-not $Force) {
    $CalendarioScript = Join-Path $ScriptDir "colombia_calendario.py"
    & $VenvPython $CalendarioScript --es-laboral
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[INFO] $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') - Fecha actual es no laboral en Colombia. Respaldo omitido según Política A." -ForegroundColor Yellow
        exit 0
    }
}

# 2. Ejecutar comando Django de respaldo
Write-Host "[INFO] $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') - Iniciando respaldo automático de Inventario AudacIA..." -ForegroundColor Cyan
Set-Location $ProjectDir
$BackupArgs = @("manage.py", "backup_inventario", "--no-input")
if ($Force) {
    $BackupArgs += "--force"
}
& $VenvPython @BackupArgs

if ($LASTEXITCODE -eq 0) {
    Write-Host "[ÉXITO] $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') - Respaldo completado satisfactoriamente." -ForegroundColor Green
} else {
    Write-Error "[ERROR] Falló la ejecución del respaldo de inventario."
    exit $LASTEXITCODE
}
