# ==============================================================================
# Script de Diagnóstico y Verificación del Entorno — Inventario AudacIA
# Audita dependencias, configuración de seguridad, certificados y base de datos.
# ==============================================================================

$ErrorActionPreference = "Continue"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $ScriptDir
$VenvPython = Join-Path (Split-Path -Parent $ProjectDir) "venv\Scripts\python.exe"

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "   DIAGNOSTICO DEL ENTORNO DE PLATAFORMA - AUDACIA       " -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

$Errores = 0

# 1. Entorno Virtual y Python
Write-Host "`n[1/5] Verificando Python y Entorno Virtual..." -ForegroundColor Yellow
if (Test-Path $VenvPython) {
    $PyVer = & $VenvPython --version
    Write-Host " [OK] Python en venv detectado: $PyVer ($VenvPython)" -ForegroundColor Green
} else {
    Write-Host " [ERROR] No se encontro Python en el entorno virtual '$VenvPython'." -ForegroundColor Red
    $Errores++
}

# 2. Dependencias Criticas
Write-Host "`n[2/5] Verificando paquetes instalados..." -ForegroundColor Yellow
$CheckScript = "import django, uvicorn, cryptography, sqlite3; print(f'Django {django.__version__}'); print(f'Uvicorn {uvicorn.__version__}'); print(f'Cryptography {cryptography.__version__}'); print(f'SQLite {sqlite3.sqlite_version}')"

$DepResult = & $VenvPython -c $CheckScript 2>&1
if ($LASTEXITCODE -eq 0) {
    foreach ($line in $DepResult) {
        Write-Host " [OK] $line" -ForegroundColor Green
    }
} else {
    Write-Host " [ERROR] Faltan dependencias criticas en el entorno virtual." -ForegroundColor Red
    Write-Host $DepResult -ForegroundColor DarkGray
    $Errores++
}

# 3. Certificados TLS Locales
Write-Host "`n[3/5] Verificando certificados TLS para HTTPS..." -ForegroundColor Yellow
$CertsDir = Join-Path $ProjectDir "certs"
$CertFile = Join-Path $CertsDir "localhost.pem"
$KeyFile = Join-Path $CertsDir "localhost-key.pem"

if ((Test-Path $CertFile) -and (Test-Path $KeyFile)) {
    $CertSize = (Get-Item $CertFile).Length
    $KeySize = (Get-Item $KeyFile).Length
    Write-Host " [OK] Certificado presente: $CertFile ($CertSize bytes)" -ForegroundColor Green
    Write-Host " [OK] Clave privada presente: $KeyFile ($KeySize bytes)" -ForegroundColor Green
} else {
    Write-Host " [AVISO] No se encontraron certificados en '$CertsDir'. Ejecute generar_certificados.ps1." -ForegroundColor Yellow
}

# 4. Chequeo del Sistema Django
Write-Host "`n[4/5] Ejecutando validacion de Django (manage.py check)..." -ForegroundColor Yellow
Set-Location $ProjectDir
$CheckDjango = & $VenvPython manage.py check 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host " [OK] Sistema Django verificado sin errores." -ForegroundColor Green
} else {
    Write-Host " [ERROR] Fallo la verificacion de Django:" -ForegroundColor Red
    Write-Host $CheckDjango -ForegroundColor DarkGray
    $Errores++
}

# 5. Calendario Laboral Colombiano
Write-Host "`n[5/5] Probando modulo de calendario laboral colombiano..." -ForegroundColor Yellow
$CalScript = Join-Path $ScriptDir "colombia_calendario.py"
$CalTest = & $VenvPython $CalScript --listar-festivos 2026 2>&1
if ($LASTEXITCODE -eq 0 -and ($CalTest.Count -eq 18)) {
    Write-Host " [OK] Calendario de festivos Ley Emiliani operativo (18 festivos/ano)." -ForegroundColor Green
} else {
    Write-Host " [ERROR] Error validando el modulo de festivos colombianos." -ForegroundColor Red
    $Errores++
}

Write-Host "`n==========================================================" -ForegroundColor Cyan
if ($Errores -eq 0) {
    Write-Host " [OK] EL ENTORNO ESTA 100% OPERATIVO Y LISTO PARA DESPLIEGUE HTTPS" -ForegroundColor Green
} else {
    Write-Host " [ERROR] SE ENCONTRARON $Errores INCONSISTENCIAS EN EL ENTORNO." -ForegroundColor Red
}
Write-Host "==========================================================" -ForegroundColor Cyan

exit $Errores
