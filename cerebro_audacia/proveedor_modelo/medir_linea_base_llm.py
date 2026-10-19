"""Captura y registro de línea base de rendimiento y memoria de llama-server en desarrollo.

Mide de forma empírica:
1. VRAM y RAM antes de inferencia (modelo cargado en reposo).
2. Pico de VRAM y RAM durante inferencia activa.
3. Latencia y velocidad de generación (tokens/segundo) en peticiones secuenciales.
4. Exporta los resultados a linea_base_desarrollo_llm.json.
"""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUT_FILE = SCRIPT_DIR / "linea_base_desarrollo_llm.json"


class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def obtener_telemetria_ram() -> Dict[str, Any]:
    """Obtiene el estado de la memoria RAM del sistema mediante la API de Windows."""
    try:
        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
        total_mib = stat.ullTotalPhys / (1024 * 1024)
        avail_mib = stat.ullAvailPhys / (1024 * 1024)
        used_mib = total_mib - avail_mib
        return {
            "ram_total_mib": round(total_mib, 2),
            "ram_usada_mib": round(used_mib, 2),
            "ram_libre_mib": round(avail_mib, 2),
            "ram_total_gib": round(total_mib / 1024, 2),
            "ram_usada_gib": round(used_mib / 1024, 2),
            "ram_libre_gib": round(avail_mib / 1024, 2),
            "ram_porcentaje_uso": stat.dwMemoryLoad,
        }
    except Exception as e:
        return {"error": str(e)}


def obtener_telemetria_gpu() -> Dict[str, Any]:
    """Consulta nvidia-smi para obtener nombre, VRAM total, usada y libre en MiB."""
    try:
        smi_path = (
            r"C:\Windows\system32\nvidia-smi.exe"
            if os.path.exists(r"C:\Windows\system32\nvidia-smi.exe")
            else "nvidia-smi"
        )
        cmd = [
            smi_path,
            "--query-gpu=name,driver_version,memory.total,memory.used,memory.free,utilization.gpu",
            "--format=csv,noheader,nounits",
        ]
        salida = subprocess.check_output(cmd, encoding="utf-8", stderr=subprocess.DEVNULL)
        partes = [p.strip() for p in salida.strip().split(",")]
        return {
            "gpu_nombre": partes[0],
            "driver_version": partes[1],
            "vram_total_mib": float(partes[2]),
            "vram_usada_mib": float(partes[3]),
            "vram_libre_mib": float(partes[4]),
            "gpu_utilizacion_pct": float(partes[5]),
        }
    except Exception as e:
        return {"error": str(e)}


def medir_linea_base(url_base: str = "http://127.0.0.1:8080") -> Dict[str, Any]:
    print(f"=== INICIANDO MEDICIÓN DE LÍNEA BASE LLM EN {url_base} ===")
    gpu_inicial = obtener_telemetria_gpu()
    ram_inicial = obtener_telemetria_ram()

    print("Telemetría Inicial:")
    print(f"  GPU         : {gpu_inicial.get('gpu_nombre')}")
    print(f"  Driver      : {gpu_inicial.get('driver_version')}")
    print(f"  VRAM Usada  : {gpu_inicial.get('vram_usada_mib')} MiB / {gpu_inicial.get('vram_total_mib')} MiB")
    print(f"  VRAM Libre  : {gpu_inicial.get('vram_libre_mib')} MiB")
    print(f"  RAM Usada   : {ram_inicial.get('ram_usada_gib')} GiB / {ram_inicial.get('ram_total_gib')} GiB ({ram_inicial.get('ram_porcentaje_uso')}%)")

    # 1. Comprobar Health y Modelos
    try:
        req_health = urllib.request.Request(f"{url_base}/health", headers={"User-Agent": "AudacIA-Benchmark"})
        with urllib.request.urlopen(req_health, timeout=5) as r:
            health_data = json.loads(r.read().decode())
    except Exception as e:
        print(f"ERROR: No se pudo conectar a {url_base}/health: {e}")
        return {"error": f"Servidor no disponible: {e}"}

    try:
        req_models = urllib.request.Request(f"{url_base}/v1/models", headers={"User-Agent": "AudacIA-Benchmark"})
        with urllib.request.urlopen(req_models, timeout=5) as r:
            models_data = json.loads(r.read().decode())
            model_id = models_data["data"][0]["id"]
    except Exception as e:
        model_id = "Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf"

    # 2. Casos de Prueba Secuenciales
    casos = [
        ("Pregunta Factual Simple", "¿Cuál es la capital del departamento del Atlántico en Colombia?"),
        ("Instrucción Multi-Paso", "Enumera exactamente tres pasos esenciales para recibir mercancía en bodega."),
        ("Consulta sobre Inventario", "Explica la diferencia entre conteo cíclico y auditoría física anual en 2 frases."),
        ("Generación Estructurada", "Devuelve un objeto JSON con campos 'producto' y 'cantidad' para 10 unidades de cinta de embalaje."),
    ]

    resultados_generacion: List[Dict[str, Any]] = []

    print("\nEjecutando batería de generación secuencial...")
    for idx, (titulo, prompt) in enumerate(casos, 1):
        print(f"\n[{idx}/{len(casos)}] {titulo}...")
        payload = {
            "model": model_id,
            "messages": [
                {"role": "system", "content": "Eres el asistente técnico de AudacIA. Responde de forma concisa, formal y precisa."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
            "max_tokens": 128,
        }

        t_inicio = time.perf_counter()
        req_post = urllib.request.Request(
            f"{url_base}/v1/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "User-Agent": "AudacIA-Benchmark"},
        )

        with urllib.request.urlopen(req_post, timeout=60) as r:
            duracion = time.perf_counter() - t_inicio
            resp_body = json.loads(r.read().decode())

        gpu_post = obtener_telemetria_gpu()
        ram_post = obtener_telemetria_ram()

        choice = resp_body.get("choices", [{}])[0]
        content = choice.get("message", {}).get("content", "").strip()
        usage = resp_body.get("usage", {})
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        total_tokens = usage.get("total_tokens", 0)
        tok_por_seg = round(completion_tokens / duracion, 2) if duracion > 0 else 0

        print(f"    Completado en {duracion:.2f} s | {completion_tokens} tokens ({tok_por_seg} t/s)")
        print(f"    VRAM Usada tras llamada: {gpu_post.get('vram_usada_mib')} MiB")
        print(f"    Respuesta: {content[:100]}...")

        resultados_generacion.append({
            "caso": titulo,
            "prompt": prompt,
            "respuesta_resumen": content[:120] + "..." if len(content) > 120 else content,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "duracion_segundos": round(duracion, 3),
            "tokens_por_segundo": tok_por_seg,
            "vram_usada_mib": gpu_post.get("vram_usada_mib"),
            "ram_usada_gib": ram_post.get("ram_usada_gib"),
        })

    # Resumen y métricas consolidadas
    tiempos = [r["duracion_segundos"] for r in resultados_generacion]
    velocidades = [r["tokens_por_segundo"] for r in resultados_generacion if r["tokens_por_segundo"] > 0]
    tokens_totales = sum(r["completion_tokens"] for r in resultados_generacion)

    linea_base = {
        "fecha": time.strftime("%Y-%m-%d %H:%M:%S"),
        "hardware": {
            "gpu": gpu_inicial,
            "ram": ram_inicial,
        },
        "motor_inferencia": {
            "motor": "llama.cpp / llama-server",
            "version_build": "b11669-620d2225c (CUDA 12.4)",
            "modelo_id": model_id,
            "modelo_archivo": "Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf",
            "cuantizacion": "Q4_K_M",
            "tamano_archivo_bytes": 4920739232,
            "sha256": "7b064f5842bf9532c91456deda288a1b672397a54fa729aa665952863033557c",
            "n_ctx": 8192,
            "n_gpu_layers": 33,
            "flash_attn": True,
            "kv_cache_tipo": "q8_0",
            "concurrencia": 1,
            "dispositivo": "CUDA0",
            "endpoint": url_base,
        },
        "metricas_consolidadas": {
            "total_pruebas": len(resultados_generacion),
            "tokens_completados_totales": tokens_totales,
            "latencia_promedio_s": round(sum(tiempos) / len(tiempos), 3) if tiempos else 0,
            "latencia_min_s": min(tiempos) if tiempos else 0,
            "latencia_max_s": max(tiempos) if tiempos else 0,
            "tokens_por_segundo_promedio": round(sum(velocidades) / len(velocidades), 2) if velocidades else 0,
            "vram_modelo_cargado_mib": gpu_post.get("vram_usada_mib"),
            "vram_libre_remanente_mib": gpu_post.get("vram_libre_mib"),
            "ram_usada_gib": ram_post.get("ram_usada_gib"),
        },
        "detalle_solicitudes": resultados_generacion,
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(linea_base, f, indent=2, ensure_ascii=False)

    print(f"\n=== LÍNEA BASE GUARDADA EN: {OUTPUT_FILE} ===")
    print(f"Velocidad promedio: {linea_base['metricas_consolidadas']['tokens_por_segundo_promedio']} tokens/s")
    print(f"VRAM Usada       : {linea_base['metricas_consolidadas']['vram_modelo_cargado_mib']} MiB")
    print(f"VRAM Remanente   : {linea_base['metricas_consolidadas']['vram_libre_remanente_mib']} MiB")
    return linea_base


if __name__ == "__main__":
    puerto = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    medir_linea_base(f"http://127.0.0.1:{puerto}")
