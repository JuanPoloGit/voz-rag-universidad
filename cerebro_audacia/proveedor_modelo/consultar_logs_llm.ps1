# ==============================================================================
# AUDACIA - CONSULTA DE REGISTROS DE LLAMA-SERVER
# ==============================================================================

param(
    [int]$Lineas = 50,
    [switch]$Continuo
)

$DirectorioScript = $PSScriptRoot
$ArchivoLog = Join-Path $DirectorioScript "llama_server.log"
$ArchivoErr = Join-Path $DirectorioScript "llama_server_err.log"

Write-Host "==================================================================" -ForegroundColor Cyan
Write-Host " AudacIA - Registros de llama-server" -ForegroundColor Cyan
Write-Host "==================================================================" -ForegroundColor Cyan

if (-not (Test-Path $ArchivoLog) -and -not (Test-Path $ArchivoErr)) {
    Write-Host "No se encontraron archivos de registro en $DirectorioScript." -ForegroundColor Yellow
    Write-Host "El servicio aun no ha sido iniciado desde este directorio." -ForegroundColor DarkGray
    exit 0
}

if (Test-Path $ArchivoErr) {
    $ErrContent = Get-Content -Path $ArchivoErr -Raw -ErrorAction SilentlyContinue
    if (-not [string]::IsNullOrWhiteSpace($ErrContent)) {
        Write-Host "--- ULTIMOS ERRORES CAPTURADOS (STDERR) ---" -ForegroundColor Red
        Get-Content -Path $ArchivoErr -Tail 15
        Write-Host ""
    }
}

if (Test-Path $ArchivoLog) {
    Write-Host "--- ULTIMAS $Lineas LINEAS DE LOG (STDOUT) ---" -ForegroundColor Yellow
    if ($Continuo) {
        Get-Content -Path $ArchivoLog -Tail $Lineas -Wait
    } else {
        Get-Content -Path $ArchivoLog -Tail $Lineas
    }
}
