# ==============================================================================
# AUDACIA - SCRIPT DE DETENCION CONTROLADA DE LLAMA-SERVER
# ==============================================================================

param(
    [int]$Puerto = 8080
)

$ErrorActionPreference = "Continue"

Write-Host "==================================================================" -ForegroundColor Cyan
Write-Host " AudacIA - Deteniendo Servicio LLM Local (llama-server)" -ForegroundColor Cyan
Write-Host "==================================================================" -ForegroundColor Cyan

$DirectorioScript = $PSScriptRoot
$ArchivoEstado = Join-Path $DirectorioScript "llama_server_estado.json"

$PidParaDetener = $null
$Procesos = @()

# 1. Intentar obtener el PID desde el archivo de estado de AudacIA
if (Test-Path $ArchivoEstado) {
    try {
        $Estado = Get-Content -Path $ArchivoEstado -Raw -Encoding UTF8 | ConvertFrom-Json
        $PidCandidato = [int]$Estado.pid
        $Proc = Get-Process -Id $PidCandidato -ErrorAction SilentlyContinue
        if ($Proc -and ($Proc.ProcessName -like "*llama-server*")) {
            Write-Host "Instancia de AudacIA identificada desde estado (PID: $PidCandidato, Puerto: $($Estado.puerto))." -ForegroundColor Green
            $PidParaDetener = $PidCandidato
            $Procesos += $Proc
        } else {
            Write-Warning "El archivo de estado contenía PID $PidCandidato pero no corresponde a un llama-server activo. Limpiando estado obsoleto."
            Remove-Item -Path $ArchivoEstado -Force -ErrorAction SilentlyContinue
        }
    } catch {
        Write-Warning "No se pudo leer el archivo de estado: $_"
    }
}

# 2. Si no se identificó por estado, buscar EXCLUSIVAMENTE por el puerto local de AudacIA ($Puerto)
if (-not $PidParaDetener) {
    Write-Host "Buscando proceso escuchando en el puerto $Puerto..." -ForegroundColor DarkGray
    $Conexiones = Get-NetTCPConnection -LocalPort $Puerto -State Listen -ErrorAction SilentlyContinue
    if ($Conexiones) {
        $Pids = $Conexiones | Select-Object -ExpandProperty OwningProcess -Unique
        foreach ($PidCnx in $Pids) {
            $Proc = Get-Process -Id $PidCnx -ErrorAction SilentlyContinue
            if ($Proc -and ($Proc.ProcessName -like "*llama-server*")) {
                Write-Host "Proceso llama-server identificado en puerto $Puerto (PID: $PidCnx)." -ForegroundColor Green
                $Procesos += $Proc
            } else {
                Write-Warning "El puerto $Puerto está ocupado por PID $PidCnx ($($Proc.ProcessName)), que no es un llama-server administrado por AudacIA. No se detendrá."
            }
        }
    }
}

if (-not $Procesos) {
    Write-Host "No se encontraron procesos activos de llama-server administrados por AudacIA en el puerto $Puerto." -ForegroundColor Green
    if (Test-Path $ArchivoEstado) {
        Remove-Item -Path $ArchivoEstado -Force -ErrorAction SilentlyContinue
    }
    exit 0
}

foreach ($Proc in $Procesos) {
    Write-Host "Deteniendo proceso AudacIA '$($Proc.ProcessName)' (PID: $($Proc.Id))..." -ForegroundColor Yellow
    try {
        Stop-Process -Id $Proc.Id -Force -ErrorAction Stop
        Write-Host "Proceso $($Proc.Id) terminado exitosamente." -ForegroundColor Green
    } catch {
        Write-Error "No se pudo terminar el proceso $($Proc.Id): $_"
    }
}

# Limpiar archivo de estado tras la detención
if (Test-Path $ArchivoEstado) {
    Remove-Item -Path $ArchivoEstado -Force -ErrorAction SilentlyContinue
}

Start-Sleep -Seconds 1

# 3. Verificar liberacion de puerto
$VerifPuerto = Get-NetTCPConnection -LocalPort $Puerto -State Listen -ErrorAction SilentlyContinue
if ($VerifPuerto) {
    Write-Warning "PRECAUCION: El puerto $Puerto aun aparece como ocupado por el PID $($VerifPuerto.OwningProcess)."
} else {
    Write-Host "Puerto $Puerto liberado correctamente." -ForegroundColor Green
}

# 4. Verificar liberacion de VRAM
try {
    $NvidiaSmiPath = if (Test-Path "C:\Windows\system32\nvidia-smi.exe") { "C:\Windows\system32\nvidia-smi.exe" } else { "nvidia-smi" }
    $VramRaw = (& $NvidiaSmiPath "--query-gpu=memory.used,memory.free" "--format=csv,noheader,nounits" 2>$null) | Out-String
    if ($VramRaw -and $VramRaw.Trim()) {
        $Parts = (($VramRaw.Trim() -split "`n")[0]).Split(',')
        if ($Parts.Count -ge 2) {
            Write-Host "Estado GPU post-detencion: VRAM Usada: $($Parts[0].Trim()) MB | VRAM Libre: $($Parts[1].Trim()) MB" -ForegroundColor Cyan
        }
    }
} catch {
    # Ignorar si nvidia-smi no esta disponible
}

Write-Host "Servicio LLM detenido satisfactoriamente." -ForegroundColor Green
