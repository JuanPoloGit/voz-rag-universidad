# ==============================================================================
# AUDACIA - SCRIPT DE INICIO DE LLAMA-SERVER (INFERENCIA LOCAL)
# ==============================================================================

param(
    [string]$Perfil = "desarrollo_rtx2000ada",
    [string]$ModeloOverride = "",
    [string]$BinarioOverride = "",
    [int]$PuertoOverride = 0,
    [switch]$Esperar
)

$ErrorActionPreference = "Stop"

$DirectorioScript = $PSScriptRoot
$RaizProyecto = (Resolve-Path "$DirectorioScript\..\..").Path
$ArchivoConfig = Join-Path $DirectorioScript "config_llm.json"
$ArchivoLog = Join-Path $DirectorioScript "llama_server.log"
$ArchivoErr = Join-Path $DirectorioScript "llama_server_err.log"

Write-Host "==================================================================" -ForegroundColor Cyan
Write-Host " AudacIA - Iniciando Servicio LLM Local (llama-server)" -ForegroundColor Cyan
Write-Host "==================================================================" -ForegroundColor Cyan

# 1. Cargar Configuración
if (-not (Test-Path $ArchivoConfig)) {
    Write-Error "ERROR: No se encontró el archivo de configuración en: $ArchivoConfig"
    exit 1
}

$ConfigRaw = Get-Content -Path $ArchivoConfig -Raw -Encoding UTF8 | ConvertFrom-Json
$HostLocal = $ConfigRaw.servicio.host
$Puerto = if ($PuertoOverride -gt 0) { $PuertoOverride } else { [int]$ConfigRaw.servicio.puerto }
$UrlSalud = "http://" + $HostLocal + ":" + $Puerto + "/health"

$PerfilConfig = $ConfigRaw.perfiles.$Perfil
if (-not $PerfilConfig) {
    Write-Error "ERROR: Perfil '$Perfil' no encontrado en $ArchivoConfig"
    exit 1
}

Write-Host "[1/6] Perfil seleccionado: $Perfil" -ForegroundColor Yellow
Write-Host "      Descripción: $($PerfilConfig.descripcion)" -ForegroundColor DarkGray

# 2. Prevenir Instancias Duplicadas
Write-Host "[2/6] Verificando que no existan instancias duplicadas..." -ForegroundColor Yellow

$ProcesosExistentes = Get-Process -Name "llama-server" -ErrorAction SilentlyContinue
if ($ProcesosExistentes) {
    Write-Host "ADVERTENCIA: Ya existe un proceso 'llama-server' en ejecución." -ForegroundColor Yellow
    Write-Host "Comprobando si responde en $UrlSalud..." -ForegroundColor DarkGray
    try {
        $Resp = Invoke-RestMethod -Uri $UrlSalud -Method Get -TimeoutSec 3 -ErrorAction Stop
        if ($Resp.status -eq "ok" -or $Resp.status -eq "ready") {
            Write-Host "ÉXITO: El servidor llama-server ya está activo y respondiendo correctamente en $UrlSalud." -ForegroundColor Green
            exit 0
        }
    } catch {
        Write-Warning "El proceso llama-server existe pero no responde en $UrlSalud. Deténgalo con .\detener_llama_server.ps1 antes de reiniciar."
        exit 1
    }
}

$PuertoEnUso = Get-NetTCPConnection -LocalPort $Puerto -State Listen -ErrorAction SilentlyContinue
if ($PuertoEnUso) {
    Write-Error "ERROR: El puerto $Puerto ya está ocupado por el PID $($PuertoEnUso.OwningProcess)."
    exit 1
}

# 3. Validar Hardware y VRAM con nvidia-smi
Write-Host "[3/6] Inspeccionando GPU local con nvidia-smi..." -ForegroundColor Yellow
try {
    $NvidiaSmiPath = if (Test-Path "C:\Windows\system32\nvidia-smi.exe") { "C:\Windows\system32\nvidia-smi.exe" } else { "nvidia-smi" }
    $GpuRaw = (& $NvidiaSmiPath "--query-gpu=name,memory.total,memory.free" "--format=csv,noheader,nounits" 2>$null) | Out-String
    if ($GpuRaw -and $GpuRaw.Trim()) {
        $PrimeraLinea = ($GpuRaw.Trim() -split "`n")[0]
        $GpuParts = $PrimeraLinea.Split(',')
        if ($GpuParts.Count -ge 3) {
            $GpuNombre = $GpuParts[0].Trim()
            $VramTotal = [int]$GpuParts[1].Trim()
            $VramLibre = [int]$GpuParts[2].Trim()
            Write-Host "      GPU Detectada: $GpuNombre" -ForegroundColor Green
            Write-Host "      VRAM Libre: $VramLibre MB / Total: $VramTotal MB" -ForegroundColor Green

            $VramMinima = [int]$PerfilConfig.presupuesto_vram_esperado_mb
            if ($VramLibre -lt $VramMinima) {
                Write-Warning "PRECAUCION: VRAM libre ($VramLibre MB) es menor que el presupuesto esperado ($VramMinima MB)."
            }
        }
    }
} catch {
    Write-Warning "nvidia-smi falló: $_"
}

# 4. Localizar Ejecutable llama-server.exe
Write-Host "[4/6] Verificando ejecutable de llama-server..." -ForegroundColor Yellow

$RutaBinDirecta = Join-Path $DirectorioScript "bin\llama-server.exe"
$RutaBinRaiz = Join-Path $RaizProyecto "bin\llama-server.exe"
$CmdPath = (Get-Command "llama-server.exe" -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source)

$RutaEjecutable = $null
if (-not [string]::IsNullOrWhiteSpace($BinarioOverride) -and (Test-Path $BinarioOverride)) {
    $RutaEjecutable = (Resolve-Path $BinarioOverride).Path
} elseif (-not [string]::IsNullOrWhiteSpace($env:LLAMA_SERVER_BIN) -and (Test-Path $env:LLAMA_SERVER_BIN)) {
    $RutaEjecutable = (Resolve-Path $env:LLAMA_SERVER_BIN).Path
} elseif (Test-Path $RutaBinDirecta) {
    $RutaEjecutable = (Resolve-Path $RutaBinDirecta).Path
} elseif (Test-Path $RutaBinRaiz) {
    $RutaEjecutable = (Resolve-Path $RutaBinRaiz).Path
} elseif ($CmdPath -and (Test-Path $CmdPath)) {
    $RutaEjecutable = (Resolve-Path $CmdPath).Path
}

if (-not $RutaEjecutable) {
    Write-Host ""
    Write-Host "==================================================================" -ForegroundColor Red
    Write-Host " BLOQUEO DETECTADO: NO SE ENCONTRO 'llama-server.exe'" -ForegroundColor Red
    Write-Host "==================================================================" -ForegroundColor Red
    Write-Host "Para utilizar el servicio LLM en Windows con aceleracion CUDA:" -ForegroundColor Yellow
    Write-Host "1. Descargue la version oficial de llama.cpp para Windows CUDA:" -ForegroundColor White
    Write-Host "   https://github.com/ggerganov/llama.cpp/releases" -ForegroundColor Cyan
    Write-Host "2. Extraiga el ejecutable en:" -ForegroundColor White
    Write-Host "   $DirectorioScript\bin\llama-server.exe" -ForegroundColor Cyan
    Write-Host "==================================================================" -ForegroundColor Red
    exit 2
}

Write-Host "      Ejecutable localizado: $RutaEjecutable" -ForegroundColor Green

# 5. Localizar Archivo de Modelo GGUF
Write-Host "[5/6] Verificando archivo del modelo GGUF..." -ForegroundColor Yellow

$NombreModelo = $PerfilConfig.modelo_archivo
$RutaModeloDefecto = Join-Path $RaizProyecto $PerfilConfig.modelo_ruta_relativa
$RutaModeloModels = Join-Path $RaizProyecto "models\$NombreModelo"

$RutaModelo = $null
if (-not [string]::IsNullOrWhiteSpace($ModeloOverride) -and (Test-Path $ModeloOverride)) {
    $RutaModelo = (Resolve-Path $ModeloOverride).Path
} elseif (-not [string]::IsNullOrWhiteSpace($env:LLAMA_MODEL_PATH) -and (Test-Path $env:LLAMA_MODEL_PATH)) {
    $RutaModelo = (Resolve-Path $env:LLAMA_MODEL_PATH).Path
} elseif (Test-Path $RutaModeloDefecto) {
    $RutaModelo = (Resolve-Path $RutaModeloDefecto).Path
} elseif (Test-Path $RutaModeloModels) {
    $RutaModelo = (Resolve-Path $RutaModeloModels).Path
}

if (-not $RutaModelo) {
    Write-Host ""
    Write-Host "==================================================================" -ForegroundColor Red
    Write-Host " BLOQUEO DETECTADO: MODELO GGUF NO ENCONTRADO" -ForegroundColor Red
    Write-Host "==================================================================" -ForegroundColor Red
    Write-Host "El archivo $NombreModelo no existe en disco." -ForegroundColor Yellow
    Write-Host "Ruta esperada: $RutaModeloDefecto" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Comando para descargarlo con huggingface-cli:" -ForegroundColor Yellow
    Write-Host "  pip install huggingface-hub" -ForegroundColor White
    Write-Host "  huggingface-cli download bartowski/Meta-Llama-3.1-8B-Instruct-GGUF $NombreModelo --local-dir $(Join-Path $RaizProyecto 'models')" -ForegroundColor Cyan
    Write-Host "==================================================================" -ForegroundColor Red
    exit 3
}

$ItemModelo = Get-Item $RutaModelo
$TamanoGB = [math]::Round(($ItemModelo.Length / 1GB), 2)
Write-Host "      Modelo localizado: $RutaModelo ($TamanoGB GB)" -ForegroundColor Green

# 6. Lanzar Proceso
Write-Host "[6/6] Lanzando proceso llama-server en segundo plano..." -ForegroundColor Yellow

$ArgList = @(
    "-m", $RutaModelo,
    "-dev", "CUDA0",
    "--host", $HostLocal,
    "--port", $Puerto,
    "-c", $PerfilConfig.n_ctx,
    "-ngl", $PerfilConfig.n_gpu_layers,
    "-b", $PerfilConfig.n_batch,
    "-t", $PerfilConfig.n_threads,
    "--parallel", $PerfilConfig.parallel
)

if ($PerfilConfig.flash_attn -eq $true) {
    $ArgList += "-fa"
    $ArgList += "on"
}

if ($PerfilConfig.kv_8bits -eq $true) {
    $ArgList += "-ctk"
    $ArgList += "q8_0"
    $ArgList += "-ctv"
    $ArgList += "q8_0"
}

$Proceso = Start-Process -FilePath $RutaEjecutable `
    -ArgumentList $ArgList `
    -RedirectStandardOutput $ArchivoLog `
    -RedirectStandardError $ArchivoErr `
    -WindowStyle Hidden `
    -PassThru

Start-Sleep -Seconds 2

if ($Proceso.HasExited) {
    if (Test-Path $ArchivoErr) { Get-Content $ArchivoErr -Tail 15 | Write-Host -ForegroundColor Red }
    if (Test-Path $ArchivoLog) { Get-Content $ArchivoLog -Tail 15 | Write-Host -ForegroundColor Yellow }
    Write-Error "ERROR: llama-server terminó inmediatamente. Revise $ArchivoLog y $ArchivoErr"
    exit 1
}

Write-Host "      Proceso iniciado con PID: $($Proceso.Id)" -ForegroundColor Green

# Registrar archivo de estado y propiedad para el supervisor y script de parada
$ArchivoEstado = Join-Path $DirectorioScript "llama_server_estado.json"
$InfoEstado = [ordered]@{
    pid = $Proceso.Id
    puerto = $Puerto
    host = $HostLocal
    perfil = $Perfil
    modelo_archivo = $NombreModelo
    modelo_ruta = $RutaModelo
    ejecutable_ruta = $RutaEjecutable
    iniciado_en = (Get-Date -Format "o")
    audacia_id = "audacia-llama-server-$Puerto"
}
$InfoEstado | ConvertTo-Json -Depth 3 | Set-Content -Path $ArchivoEstado -Encoding UTF8

# 7. Sondeo de Salud
Write-Host "Esperando inicialización y carga de capas en VRAM..." -ForegroundColor Yellow
$Listo = $false

for ($i = 1; $i -le 60; $i++) {
    Start-Sleep -Seconds 1
    if ($Proceso.HasExited) {
        if (Test-Path $ArchivoErr) { Get-Content $ArchivoErr -Tail 15 | Write-Host -ForegroundColor Red }
        if (Test-Path $ArchivoLog) { Get-Content $ArchivoLog -Tail 15 | Write-Host -ForegroundColor Yellow }
        Write-Error "ERROR: llama-server se cerró durante la carga en VRAM."
        exit 1
    }
    try {
        $RespSalud = Invoke-RestMethod -Uri $UrlSalud -Method Get -TimeoutSec 2 -ErrorAction Stop
        if ($RespSalud.status -eq "ok" -or $RespSalud.status -eq "ready") {
            $Listo = $true
            break
        }
    } catch {
        Write-Host -NoNewline "."
    }
}

Write-Host ""
if ($Listo) {
    Write-Host "==================================================================" -ForegroundColor Green
    Write-Host " SERVICIO LLM LISTO Y OPERATIVO EN http://$HostLocal`:$Puerto" -ForegroundColor Green
    Write-Host " Modelo: $NombreModelo" -ForegroundColor Green
    Write-Host " PID: $($Proceso.Id)" -ForegroundColor Green
    Write-Host "==================================================================" -ForegroundColor Green
    
    if ($Esperar) {
        Write-Host "Modo -Esperar activo: manteniendo el proceso vivo en segundo plano..." -ForegroundColor Cyan
        Wait-Process -Id $Proceso.Id
    }
} else {
    Write-Warning "El servidor no respondió en 60 segundos. Verifique $ArchivoLog."
}
