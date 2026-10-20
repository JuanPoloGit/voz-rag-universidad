# ==============================================================================
# Script de Registro de Tarea Programada en Windows — Inventario AudacIA
# Programa la ejecución del respaldo cada 2 horas de lunes a viernes.
# ==============================================================================

[CmdletBinding()]
param (
    [string]$TaskName = "AudacIA_Respaldo_Inventario_2h",
    [switch]$Desinstalar
)

$ErrorActionPreference = "Stop"

if ($Desinstalar) {
    Write-Host "[INFO] Desinstalando tarea programada '$TaskName'..." -ForegroundColor Yellow
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "[ÉXITO] Tarea '$TaskName' desinstalada." -ForegroundColor Green
    exit 0
}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$BackupScript = Join-Path $ScriptDir "backup_cada_2h.ps1"

if (-not (Test-Path $BackupScript)) {
    Write-Error "No se encontró el script de respaldo en '$BackupScript'."
    exit 1
}

# Acción: Ejecutar powershell con el script
$Action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$BackupScript`""

# Disparador: Cada día, repitiendo cada 2 horas por 1 día
$Trigger = New-ScheduledTaskTrigger `
    -Daily `
    -At "08:00am"

$Trigger.RepetitionInterval = (New-TimeSpan -Hours 2)
$Trigger.RepetitionDuration = (New-TimeSpan -Days 1)

# Configuración adicional
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew

Write-Host "[INFO] Registrando tarea programada '$TaskName' en Windows Task Scheduler..." -ForegroundColor Cyan

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Description "Respaldo automático cada 2 horas para Inventario AudacIA (Política A - Colombia)" `
    -Force

Write-Host "[ÉXITO] Tarea programada '$TaskName' registrada exitosamente." -ForegroundColor Green
Write-Host "Horario: Inicia a las 08:00 AM y se ejecuta cada 2 horas (valida días laborales de Colombia internamente)."
