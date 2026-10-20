"""Prueba A — Sistema en Reposo (Baseline) para AudacIA.

Mide el consumo basal de hardware sin servicios de IA activos durante 10 segundos.
Registra muestras cada 250 ms y captura los procesos dominantes del sistema.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from cerebro_audacia.benchmark_recursos.telemetria import (
    MuestreadorRecursos,
    obtener_telemetria_gpu,
    obtener_telemetria_sistema,
)

RESULTADOS_DIR = Path(__file__).resolve().parent / "resultados"
RESULTADOS_DIR.mkdir(parents=True, exist_ok=True)


def obtener_top_procesos() -> List[Dict[str, Any]]:
    """Obtiene los 8 procesos con mayor consumo de memoria en el sistema."""
    try:
        ps_cmd = (
            "Get-Process | Sort-Object -Property WorkingSet64 -Descending | "
            "Select-Object -First 8 -Property Id, ProcessName, @{Name='WS_MB';Expression={[math]::Round($_.WorkingSet64 / 1MB, 2)}}, @{Name='CPU_s';Expression={[math]::Round($_.CPU, 2)}} | "
            "ConvertTo-Json"
        )
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            encoding="utf-8",
            stderr=subprocess.DEVNULL,
        )
        data = json.loads(out)
        return data if isinstance(data, list) else [data]
    except Exception as e:
        return [{"error": str(e)}]


def ejecutar_prueba_reposo(duracion_segundos: float = 10.0, intervalo_s: float = 0.25) -> Dict[str, Any]:
    print(f"\n==================================================================")
    print(f" PRUEBA A — SISTEMA EN REPOSO ({duracion_segundos}s, muestreo cada {intervalo_s*1000:.0f}ms)")
    print(f"==================================================================")

    # 1. Foto previa
    sys_ini = obtener_telemetria_sistema()
    gpu_ini = obtener_telemetria_gpu()
    top_proc_ini = obtener_top_procesos()

    print(f"Estado Inicial:")
    print(f"  RAM Usada   : {sys_ini['ram_usada_gib']} GiB / {sys_ini['ram_total_gib']} GiB ({sys_ini['ram_carga_pct']}%)")
    print(f"  RAM Libre   : {sys_ini['ram_libre_gib']} GiB ({sys_ini['ram_libre_mib']} MiB)")
    print(f"  VRAM Usada  : {gpu_ini.get('vram_usada_mib')} MiB / {gpu_ini.get('vram_total_mib')} MiB")
    print(f"  VRAM Libre  : {gpu_ini.get('vram_libre_mib')} MiB")
    print(f"  Compromiso  : {sys_ini['memoria_comprometida_mib']} MiB / {sys_ini['limite_compromiso_mib']} MiB")

    # 2. Muestreo continuo
    muestreador = MuestreadorRecursos(intervalo_s=intervalo_s)
    muestreador.iniciar()
    time.sleep(duracion_segundos)
    muestras = muestreador.detener()

    # 3. Resumen y métricas
    resumen = muestreador.calcular_resumen(
        baseline_vram=gpu_ini.get("vram_usada_mib", 0.0),
        baseline_ram=sys_ini.get("ram_usada_mib", 0.0),
    )

    archivo_csv = RESULTADOS_DIR / "prueba_a_reposo_crudo.csv"
    archivo_json = RESULTADOS_DIR / "prueba_a_reposo.json"
    muestreador.exportar_csv(archivo_csv)

    resultado_final = {
        "prueba": "Prueba A — Sistema en Reposo",
        "fecha": time.strftime("%Y-%m-%d %H:%M:%S"),
        "condiciones": {
            "duracion_segundos": duracion_segundos,
            "intervalo_muestreo_s": intervalo_s,
            "total_muestras": len(muestras),
            "servicios_activos": "Ningún servicio de IA activo",
        },
        "telemetria_inicial": {
            "sistema": sys_ini,
            "gpu": gpu_ini,
        },
        "resumen_muestreo": resumen,
        "top_procesos_consumo": top_proc_ini,
        "archivos_guardados": {
            "crudo_csv": str(archivo_csv),
            "resumen_json": str(archivo_json),
        },
    }

    with open(archivo_json, "w", encoding="utf-8") as f:
        json.dump(resultado_final, f, indent=2, ensure_ascii=False)

    print(f"\nResultados:")
    print(f"  Total Muestras      : {len(muestras)}")
    print(f"  VRAM Promedio       : {resumen['vram_usada']['promedio']} MiB (Pico: {resumen['vram_pico_mib']} MiB)")
    print(f"  RAM Promedio        : {resumen['ram_usada']['promedio']} MiB ({resumen['ram_usada']['promedio']/1024:.2f} GiB)")
    print(f"  CPU Promedio        : {resumen['cpu_sistema']['promedio']}% (Pico: {resumen['cpu_sistema']['max']}%)")
    print(f"  GPU Util Promedio   : {resumen['gpu_util']['promedio']}%")
    print(f"  Guardado en         : {archivo_json}")
    return resultado_final


if __name__ == "__main__":
    ejecutar_prueba_reposo()
