# ==============================================================================
# AUDACIA - COMPROBACION DE SALUD Y DISPONIBILIDAD DE LLAMA-SERVER
# ==============================================================================

param(
    [string]$UrlBase = "http://127.0.0.1:8080"
)

$ErrorActionPreference = "Continue"

Write-Host "==================================================================" -ForegroundColor Cyan
Write-Host " AudacIA - Verificacion de Estado del Servicio LLM" -ForegroundColor Cyan
Write-Host " URL Base: $UrlBase" -ForegroundColor DarkGray
Write-Host "==================================================================" -ForegroundColor Cyan

# 1. Comprobar /health
$UrlSalud = "$UrlBase/health"
Write-Host "Consultando disponibilidad (/health)..." -ForegroundColor Yellow

$Stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
try {
    $RespSalud = Invoke-RestMethod -Uri $UrlSalud -Method Get -TimeoutSec 3 -ErrorAction Stop
    $Stopwatch.Stop()
    $LatenciaMs = $Stopwatch.ElapsedMilliseconds

    Write-Host "  Estado del Servicio : $($RespSalud.status)" -ForegroundColor Green
    Write-Host "  Latencia de Sondeo  : $LatenciaMs ms" -ForegroundColor Green
} catch {
    $Stopwatch.Stop()
    Write-Host "  FALLO: No se pudo conectar con $UrlSalud" -ForegroundColor Red
    Write-Host "  Detalle: $_" -ForegroundColor DarkGray
    Write-Host "  El servicio llama-server no se encuentra activo o no esta escuchando en este puerto." -ForegroundColor Yellow
    exit 1
}

# 2. Comprobar /v1/models
$UrlModelos = "$UrlBase/v1/models"
Write-Host "Consultando catalogo de modelos cargados (/v1/models)..." -ForegroundColor Yellow

try {
    $RespModelos = Invoke-RestMethod -Uri $UrlModelos -Method Get -TimeoutSec 3 -ErrorAction Stop
    $ModelosCargados = $RespModelos.data

    Write-Host "  Modelos Reportados:" -ForegroundColor Cyan
    foreach ($Mod in $ModelosCargados) {
        Write-Host "    - ID: $($Mod.id) (Propietario: $($Mod.owned_by))" -ForegroundColor White
    }
} catch {
    Write-Warning "No se pudo consultar el endpoint /v1/models: $_"
}

# 3. Telemetria GPU
try {
    $NvidiaSmiPath = if (Test-Path "C:\Windows\system32\nvidia-smi.exe") { "C:\Windows\system32\nvidia-smi.exe" } else { "nvidia-smi" }
    $VramRaw = (& $NvidiaSmiPath "--query-gpu=name,memory.used,memory.total,utilization.gpu" "--format=csv,noheader,nounits" 2>$null) | Out-String
    if ($VramRaw -and $VramRaw.Trim()) {
        $Parts = (($VramRaw.Trim() -split "`n")[0]).Split(',')
        if ($Parts.Count -ge 4) {
            Write-Host "Estado de GPU:" -ForegroundColor Cyan
            Write-Host "  Tarjeta    : $($Parts[0].Trim())" -ForegroundColor White
            Write-Host "  VRAM Usada : $($Parts[1].Trim()) MB / $($Parts[2].Trim()) MB" -ForegroundColor White
            Write-Host "  Carga GPU  : $($Parts[3].Trim()) %" -ForegroundColor White
        }
    }
} catch {
    # Ignorar si nvidia-smi no esta disponible
}

Write-Host "==================================================================" -ForegroundColor Green
Write-Host " Diagnostico finalizado." -ForegroundColor Green
Write-Host "==================================================================" -ForegroundColor Green
