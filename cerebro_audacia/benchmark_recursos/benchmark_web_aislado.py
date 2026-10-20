"""Prueba D — Aplicación Web sin Inferencia para AudacIA.

Evalúa el consumo de memoria, CPU y latencia del servidor Django / Uvicorn (puerto 8000):
- Identifica el proceso PID que escucha en el puerto 8000.
- Mide Working Set, Private Bytes y CPU en reposo.
- Envía 10 peticiones GET concurrentes de solo lectura (HTTPS con ssl._create_unverified_context).
- Mide latencia promedio, mínima, máxima, P50 y P95.
- Cuantifica el incremento de memoria del worker.
"""

from __future__ import annotations

import json
import os
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from cerebro_audacia.benchmark_recursos.telemetria import (
    MuestreadorRecursos,
    obtener_telemetria_gpu,
    obtener_telemetria_proceso,
    obtener_telemetria_sistema,
)

SCRIPT_DIR = Path(__file__).resolve().parent
RESULTADOS_DIR = SCRIPT_DIR / "resultados"
RESULTADOS_DIR.mkdir(parents=True, exist_ok=True)

URL_WEB = "https://127.0.0.1:8000"


def obtener_pid_servidor_web(puerto: int = 8000) -> Optional[int]:
    """Obtiene el PID del proceso que escucha en el puerto web."""
    try:
        ps_cmd = f"(Get-NetTCPConnection -LocalPort {puerto} -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -First 1)"
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            encoding="utf-8",
            stderr=subprocess.DEVNULL,
        ).strip()
        if out:
            return int(out)
    except Exception:
        pass
    return None


def ejecutar_prueba_web_aislado() -> Dict[str, Any]:
    print(f"\n==================================================================")
    print(f" PRUEBA D — APLICACIÓN WEB SIN INFERENCIA (Django / Uvicorn HTTPS)")
    print(f"==================================================================")

    pid_web = obtener_pid_servidor_web(8000)
    if not pid_web:
        print("ADVERTENCIA: No se detectó ningún servidor web escuchando en el puerto 8000.")
        return {"error": "Servidor web no detectado en puerto 8000"}

    proc_ini = obtener_telemetria_proceso(pid_web)
    sys_ini = obtener_telemetria_sistema()
    gpu_ini = obtener_telemetria_gpu()

    print(f"Servidor Web Detectado:")
    print(f"  PID                 : {pid_web}")
    print(f"  Working Set Inicial : {proc_ini.get('working_set_mib')} MiB")
    print(f"  Private Bytes Inicial: {proc_ini.get('private_bytes_mib')} MiB")

    # Iniciar muestreador
    muestreador = MuestreadorRecursos(intervalo_s=0.2, pids_objetivo=[pid_web])
    muestreador.iniciar()

    # Contexto SSL que permite certificados autofirmados de desarrollo
    ctx_ssl = ssl.create_default_context()
    ctx_ssl.check_hostname = False
    ctx_ssl.verify_mode = ssl.CERT_NONE

    rutas_prueba = ["/", "/static/css/estilos.css", "/"]
    latencias: List[float] = []
    respuestas_info = []

    print(f"\nEjecutando 10 peticiones GET de solo lectura a {URL_WEB}...")
    for i in range(10):
        url = f"{URL_WEB}{rutas_prueba[i % len(rutas_prueba)]}"
        req = urllib.request.Request(url, headers={"User-Agent": "AudacIA-Benchmark-Web/1.0"})
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=5.0, context=ctx_ssl) as resp:
                dur = time.perf_counter() - t0
                status = resp.status
                latencias.append(round(dur, 4))
                respuestas_info.append({"url": url, "codigo": status, "latencia_s": round(dur, 4)})
        except urllib.error.HTTPError as e:
            dur = time.perf_counter() - t0
            latencias.append(round(dur, 4))
            respuestas_info.append({"url": url, "codigo": e.code, "latencia_s": round(dur, 4)})
        except Exception as e:
            respuestas_info.append({"url": url, "error": str(e)})

        time.sleep(0.1)

    time.sleep(0.5)
    muestras = muestreador.detener()
    proc_post = obtener_telemetria_proceso(pid_web)
    resumen_telemetria = muestreador.calcular_resumen(
        baseline_vram=gpu_ini.get("vram_usada_mib", 0.0),
        baseline_ram=sys_ini.get("ram_usada_mib", 0.0),
    )

    archivo_csv = RESULTADOS_DIR / "prueba_d_web_aislado_crudo.csv"
    archivo_json = RESULTADOS_DIR / "prueba_d_web_aislado.json"
    muestreador.exportar_csv(archivo_csv)

    lat_ord = sorted(latencias) if latencias else [0.0]
    n_lat = len(lat_ord)

    resultado_final = {
        "prueba": "Prueba D — Aplicación Web sin Inferencia",
        "fecha": time.strftime("%Y-%m-%d %H:%M:%S"),
        "servidor": {
            "endpoint": URL_WEB,
            "pid": pid_web,
            "servidor_asgi": "Uvicorn + Django 5.x",
            "protocolo": "HTTPS (TLS)",
        },
        "memoria_proceso_web": {
            "working_set_inicial_mib": proc_ini.get("working_set_mib"),
            "working_set_final_mib": proc_post.get("working_set_mib"),
            "delta_working_set_mib": round(proc_post.get("working_set_mib", 0) - proc_ini.get("working_set_mib", 0), 2),
            "private_bytes_mib": proc_post.get("private_bytes_mib"),
        },
        "latencias_http": {
            "total_peticiones": len(latencias),
            "latencia_promedio_s": round(sum(latencias) / n_lat, 4),
            "latencia_min_s": min(lat_ord),
            "latencia_max_s": max(lat_ord),
            "latencia_p50_s": lat_ord[int(0.50 * n_lat)],
            "latencia_p95_s": lat_ord[min(n_lat - 1, int(0.95 * n_lat))],
        },
        "telemetria_muestreo": resumen_telemetria,
        "detalle_peticiones": respuestas_info,
        "archivos_guardados": {
            "crudo_csv": str(archivo_csv),
            "resumen_json": str(archivo_json),
        },
    }

    with open(archivo_json, "w", encoding="utf-8") as f:
        json.dump(resultado_final, f, indent=2, ensure_ascii=False)

    print(f"\nResumen de Prueba D:")
    print(f"  Working Set Web     : {proc_post.get('working_set_mib')} MiB (Private: {proc_post.get('private_bytes_mib')} MiB)")
    print(f"  Latencia media HTTP : {resultado_final['latencias_http']['latencia_promedio_s']*1000:.1f} ms (P50: {resultado_final['latencias_http']['latencia_p50_s']*1000:.1f} ms)")
    print(f"  Guardado en         : {archivo_json}")
    return resultado_final


if __name__ == "__main__":
    ejecutar_prueba_web_aislado()
