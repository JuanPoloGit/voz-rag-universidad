# Ejecuta validaciones y suite de pruebas en SQLite y, si hay un servidor PostgreSQL
# nativo/externo disponible (sin requerir Docker), también en PostgreSQL.
#
# Uso:
#   .\sistema_inventario\scripts\test_ambos_motores.ps1
#   .\sistema_inventario\scripts\test_ambos_motores.ps1 -IncluirHaku
#   .\sistema_inventario\scripts\test_ambos_motores.ps1 -Etiquetas "inventario.tests.test_infraestructura_bd_dual"
#   .\sistema_inventario\scripts\test_ambos_motores.ps1 -SoloSqlite

param(
    [string[]]$Etiquetas = @(),
    [switch]$IncluirHaku,
    [switch]$SoloSqlite,
    [int]$Verbosity = 1
)

$ErrorActionPreference = "Stop"

if ($Etiquetas.Count -eq 0) {
    $Etiquetas = @("inventario", "cuentas", "web")
    if ($IncluirHaku) {
        $Etiquetas += "integraciones.haku"
    }
}

$scriptDir = $PSScriptRoot
$appDir = Split-Path -Parent $scriptDir
$repoRoot = Split-Path -Parent $appDir

$candidatosPython = @(
    (Join-Path $repoRoot "venv\Scripts\python.exe"),
    (Join-Path $repoRoot ".venv\Scripts\python.exe")
)

$pythonExe = $null
foreach ($cand in $candidatosPython) {
    if (Test-Path $cand) {
        $pythonExe = $cand
        break
    }
}
if (-not $pythonExe) {
    $pythonExe = "python"
}

Push-Location $appDir
try {
    Write-Host "==========================================================" -ForegroundColor Cyan
    Write-Host " [1/2] MOTOR SQLITE (WAL + transaction_mode=IMMEDIATE)" -ForegroundColor Cyan
    Write-Host "==========================================================" -ForegroundColor Cyan

    $env:DB_ENGINE = "sqlite"
    $env:USE_SQLITE = "True"

    & $pythonExe manage.py check
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }

    & $pythonExe manage.py test @Etiquetas --verbosity $Verbosity
    $sqliteExit = $LASTEXITCODE
    if ($sqliteExit -ne 0) {
        Write-Host "[FALLO] La suite en SQLite finalizó con errores (código $sqliteExit)." -ForegroundColor Red
        exit $sqliteExit
    }
    Write-Host "[OK] Suite en SQLite completada exitosamente." -ForegroundColor Green

    if ($SoloSqlite) {
        Write-Host "[INFO] Ejecución limitada a SQLite (-SoloSqlite)." -ForegroundColor Yellow
        exit 0
    }

    Write-Host ""
    Write-Host "==========================================================" -ForegroundColor Cyan
    Write-Host " [2/2] MOTOR POSTGRESQL (READ COMMITTED)" -ForegroundColor Cyan
    Write-Host "==========================================================" -ForegroundColor Cyan

    $pgDriverOk = & $pythonExe -c "
try:
    import psycopg
    print('OK')
except ImportError:
    try:
        import psycopg2
        print('OK')
    except ImportError:
        print('NO_DRIVER')
"
    if ($pgDriverOk.Trim() -ne "OK") {
        Write-Host "[OMITIDO] psycopg / psycopg2 no está instalado en el entorno actual." -ForegroundColor Yellow
        Write-Host "          Cuando dispongas de PostgreSQL nativo o remoto, instala psycopg[binary] y define DB_HOST/DB_USER/DB_PASSWORD." -ForegroundColor DarkGray
        exit 0
    }

    $pgHost = if ($env:DB_HOST) { $env:DB_HOST } else { "localhost" }
    $pgPort = if ($env:DB_PORT) { [int]$env:DB_PORT } else { 5432 }

    $tcpOk = $false
    try {
        $clienteTcp = New-Object System.Net.Sockets.TcpClient
        $iar = $clienteTcp.BeginConnect($pgHost, $pgPort, $null, $null)
        if ($iar.AsyncWaitHandle.WaitOne(1500, $false) -and $clienteTcp.Connected) {
            $tcpOk = $true
        }
        $clienteTcp.Close()
    } catch {
        $tcpOk = $false
    }

    if (-not $tcpOk) {
        Write-Host "[OMITIDO] No se detectó instancia PostgreSQL escuchando en ${pgHost}:${pgPort}." -ForegroundColor Yellow
        exit 0
    }

    $env:DB_ENGINE = "postgresql"
    $env:USE_SQLITE = "False"

    & $pythonExe manage.py check --settings=inventario_audacia.settings_test_pg
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }

    & $pythonExe manage.py test @Etiquetas --settings=inventario_audacia.settings_test_pg --verbosity $Verbosity
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[FALLO] La suite en PostgreSQL finalizó con errores." -ForegroundColor Red
        exit $LASTEXITCODE
    }
    Write-Host "[OK] Suite en PostgreSQL completada exitosamente." -ForegroundColor Green
}
finally {
    Pop-Location
}
