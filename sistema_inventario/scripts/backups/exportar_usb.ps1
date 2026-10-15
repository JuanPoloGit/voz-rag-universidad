# ==============================================================================
# Script de Exportación de Respaldos a Unidad USB / Extraíble — Inventario AudacIA
# Copia los respaldos y archivos de verificación SHA-256 hacia un medio externo.
# ==============================================================================

[CmdletBinding()]
param (
    [string]$DestinoUSB,
    [int]$CantidadCopias = 5
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ScriptsRoot = Split-Path -Parent $ScriptDir
$ProjectDir = Split-Path -Parent $ScriptsRoot
$BackupsDir = Join-Path $ProjectDir "backups"

if (-not (Test-Path $BackupsDir)) {
    Write-Warning "El directorio de respaldos '$BackupsDir' no existe o está vacío."
    exit 0
}

# 1. Detectar unidad USB si no se especificó
if (-not $DestinoUSB) {
    $UsbDrive = Get-CimInstance Win32_Volume | Where-Object { $_.DriveType -eq 2 -and $_.DriveLetter } | Select-Object -First 1
    if ($UsbDrive) {
        $DestinoUSB = Join-Path $UsbDrive.DriveLetter "AudacIA_Backups"
        Write-Host "[INFO] Detectada unidad extraíble en $($UsbDrive.DriveLetter). Destino: $DestinoUSB" -ForegroundColor Cyan
    } else {
        Write-Error "No se detectó automáticamente ninguna unidad USB extraíble. Por favor especifique el parámetro -DestinoUSB (ej: -DestinoUSB E:\Respaldos)."
        exit 1
    }
}

if (-not (Test-Path $DestinoUSB)) {
    New-Item -ItemType Directory -Path $DestinoUSB -Force | Out-Null
}

# 2. Obtener los respaldos más recientes
$Archivos = Get-ChildItem -Path $BackupsDir -Filter "backup_*.sqlite3.gz" | Sort-Object LastWriteTime -Descending | Select-Object -First $CantidadCopias

if (-not $Archivos) {
    Write-Warning "No se encontraron archivos de respaldo .sqlite3.gz en '$BackupsDir'."
    exit 0
}

Write-Host "[INFO] Copiando las $CantidadCopias copias más recientes hacia '$DestinoUSB'..." -ForegroundColor Cyan

foreach ($Archivo in $Archivos) {
    $DestGz = Join-Path $DestinoUSB $Archivo.Name
    Copy-Item -Path $Archivo.FullName -Destination $DestGz -Force
    Write-Host " -> Copiado: $($Archivo.Name)" -ForegroundColor Green

    # Copiar también archivo de checksum SHA-256
    $ShaFile = "$($Archivo.FullName).sha256"
    if (Test-Path $ShaFile) {
        Copy-Item -Path $ShaFile -Destination $DestinoUSB -Force
        Write-Host " -> Verificación copiada: $($Archivo.Name).sha256" -ForegroundColor DarkGreen
    }
}

Write-Host "[ÉXITO] Sincronización a medio extraíble finalizada." -ForegroundColor Green
