# Script de PowerShell para generar certificados autofirmados de prueba con SAN (Subject Alternative Name)
# Utiliza el binario openssl.exe ya presente en el equipo (instalación de Git for Windows).

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$certDir = Join-Path $scriptDir "certificados"

if (-not (Test-Path $certDir)) {
    New-Item -ItemType Directory -Path $certDir -Force | Out-Null
}

# 1. Localizar binario de OpenSSL en el sistema sin instalar nada
$opensslBin = $null
$posiblesRutas = @(
    "C:\Users\AudacIA\AppData\Local\Programs\Git\usr\bin\openssl.exe",
    "C:\Program Files\Git\usr\bin\openssl.exe",
    "C:\Program Files (x86)\Git\usr\bin\openssl.exe"
)

foreach ($ruta in $posiblesRutas) {
    if (Test-Path $ruta) {
        $opensslBin = $ruta
        break
    }
}

if (-not $opensslBin) {
    $cmd = Get-Command openssl -ErrorAction SilentlyContinue
    if ($cmd) {
        $opensslBin = $cmd.Source
    }
}

if (-not $opensslBin) {
    Write-Error "No se encontró el ejecutable openssl.exe en las rutas conocidas de Git."
    exit 1
}

Write-Host "Utilizando OpenSSL encontrado en: $opensslBin" -ForegroundColor Cyan

# 2. Configurar Subject Alternative Names (SAN) con la IP LAN exacta y nombres de host
$ipLan = "172.16.130.73"
$cnfPath = Join-Path $certDir "openssl_san.cnf"
$keyPath = Join-Path $certDir "key.pem"
$certPath = Join-Path $certDir "cert.pem"

$cnfContent = @"
[req]
default_bits = 2048
prompt = no
default_md = sha256
distinguished_name = dn
x509_extensions = v3_req

[dn]
C = CO
ST = Atlantico
L = Barranquilla
O = Universidad Simon Bolivar
OU = AudacIA
CN = $ipLan

[v3_req]
subjectAltName = @alt_names
basicConstraints = CA:FALSE
keyUsage = digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth

[alt_names]
IP.1 = $ipLan
IP.2 = 127.0.0.1
DNS.1 = S4-AUDACIA-LT09.unisimon.edu.co
DNS.2 = S4-AUDACIA-LT09
DNS.3 = localhost
DNS.4 = audacia-prueba.local
"@

Set-Content -Path $cnfPath -Value $cnfContent -Encoding ASCII

# 3. Generar clave privada y certificado autofirmado (vigencia 30 días para pruebas)
Write-Host "Generando certificado autofirmado y clave privada..." -ForegroundColor Yellow

& $opensslBin req -x509 -nodes -days 30 -newkey rsa:2048 `
    -keyout $keyPath `
    -out $certPath `
    -config $cnfPath

if ((Test-Path $keyPath) -and (Test-Path $certPath)) {
    Write-Host "Certificado generado exitosamente en:" -ForegroundColor Green
    Write-Host "  Cert: $certPath"
    Write-Host "  Key:  $keyPath"
    Write-Host ""
    Write-Host "Subject Alternative Names incluidos:" -ForegroundColor Cyan
    & $opensslBin x509 -in $certPath -noout -text | Select-String "IP Address|DNS:"
} else {
    Write-Error "Fallo en la generación de los archivos de certificado."
    exit 1
}
