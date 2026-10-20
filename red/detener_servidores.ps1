# Script de PowerShell para detener los servidores de diagnóstico en los puertos 8765 y 8766

Write-Host "Buscando procesos escuchando en los puertos 8765 y 8766..." -ForegroundColor Cyan

$puertos = @(8765, 8766)
$procesosDetenidos = 0

foreach ($puerto in $puertos) {
    # Buscar conexiones TCP en estado Listen
    $conexiones = Get-NetTCPConnection -LocalPort $puerto -State Listen -ErrorAction SilentlyContinue
    if ($conexiones) {
        foreach ($conn in $conexiones) {
            $pidProc = $conn.OwningProcess
            if ($pidProc -gt 0) {
                try {
                    $proc = Get-Process -Id $pidProc -ErrorAction SilentlyContinue
                    if ($proc) {
                        Write-Host "Deteniendo proceso ID $pidProc ($($proc.ProcessName)) en puerto $puerto..." -ForegroundColor Yellow
                        Stop-Process -Id $pidProc -Force -ErrorAction SilentlyContinue
                        $procesosDetenidos++
                    }
                } catch {
                    Write-Warning "No se pudo detener el proceso $pidProc: $_"
                }
            }
        }
    }
}

if ($procesosDetenidos -eq 0) {
    Write-Host "No se encontraron procesos activos en los puertos 8765 ni 8766." -ForegroundColor Green
} else {
    Write-Host "Servidores de diagnóstico detenidos exitosamente ($procesosDetenidos procesos finalizados)." -ForegroundColor Green
}
