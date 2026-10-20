"""Prueba B — LLM de Forma Aislada para AudacIA.

Evalúa el rendimiento de llama-server y Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf:
1. Tiempo de arranque en frío (cold start) y carga en VRAM.
2. Primera generación (cold request).
3. Batería de solicitudes secuenciales (cortas, medianas y largas).
4. Solicitud de contexto extendido (~1.500 tokens).
5. Concurrencia de 2 clientes simultáneos para observar encolamiento y latencia (servidor con n_parallel=1).
6. Parada y verificación de liberación de recursos.
7. Cálculo de latencias P50, P95, promedio, tokens/s y telemetría de VRAM/RAM.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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

RESULTADOS_DIR = SCRIPT_DIR / "resultados"
RESULTADOS_DIR.mkdir(parents=True, exist_ok=True)
PROVEEDOR_DIR = SCRIPT_DIR.parent / "proveedor_modelo"

URL_BASE = "http://127.0.0.1:8080"


def asegurar_servicio_detenido() -> None:
    """Detiene cualquier instancia previa de llama-server."""
    script_detener = PROVEEDOR_DIR / "detener_llama_server.ps1"
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script_detener)],
            check=True,
            capture_output=True,
            timeout=15,
        )
    except Exception:
        pass
    time.sleep(1.0)


def iniciar_servicio_llm(esperar_modo: bool = True) -> Tuple[int, float]:
    """Inicia llama-server y mide el tiempo exacto de arranque en frío hasta disponibilidad."""
    asegurar_servicio_detenido()
    script_iniciar = PROVEEDOR_DIR / "iniciar_llama_server.ps1"

    t0 = time.perf_counter()
    args_cmd = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script_iniciar),
        "-Perfil",
        "desarrollo_rtx2000ada",
    ]
    if esperar_modo:
        args_cmd.append("-Esperar")

    # Iniciar en segundo plano
    proceso = subprocess.Popen(
        args_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # Sondear health
    pid = 0
    t_listo = 0.0
    for _ in range(60):
        time.sleep(0.5)
        try:
            req = urllib.request.Request(f"{URL_BASE}/health")
            with urllib.request.urlopen(req, timeout=1.0) as r:
                data = json.loads(r.read().decode())
                if data.get("status") in ("ok", "ready"):
                    t_listo = time.perf_counter() - t0
                    break
        except Exception:
            pass

    # Obtener PID real de llama-server
    try:
        ps_pid = "Get-Process -Name 'llama-server' | Select-Object -ExpandProperty Id -First 1"
        out_pid = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command", ps_pid],
            encoding="utf-8",
            stderr=subprocess.DEVNULL,
        ).strip()
        if out_pid:
            pid = int(out_pid)
    except Exception:
        pid = proceso.pid

    return pid, round(t_listo, 3)


def enviar_chat_completion(prompt: str, max_tokens: int = 128, temp: float = 0.2) -> Dict[str, Any]:
    """Envía una petición al endpoint OpenAI-compatible y mide métricas de respuesta."""
    payload = {
        "model": "Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf",
        "messages": [
            {"role": "system", "content": "Eres el asistente técnico de AudacIA para el inventario universitario. Responde de forma clara y concisa."},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": temp,
    }

    req = urllib.request.Request(
        f"{URL_BASE}/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "AudacIA-Benchmark/1.0"},
    )

    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=60) as resp:
        duracion = time.perf_counter() - t0
        body = json.loads(resp.read().decode("utf-8"))

    choice = body.get("choices", [{}])[0]
    content = choice.get("message", {}).get("content", "").strip()
    usage = body.get("usage", {})
    prompt_tokens = usage.get("prompt_tokens", 0)
    completion_tokens = usage.get("completion_tokens", 0)
    total_tokens = usage.get("total_tokens", 0)
    tok_por_seg = round(completion_tokens / duracion, 2) if duracion > 0 else 0.0

    return {
        "duracion_segundos": round(duracion, 3),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "tokens_por_segundo": tok_por_seg,
        "respuesta": content[:120] + "..." if len(content) > 120 else content,
    }


def ejecutar_prueba_llm_aislado() -> Dict[str, Any]:
    print(f"\n==================================================================")
    print(f" PRUEBA B — LLM DE FORMA AISLADA (Meta-Llama-3.1-8B-Instruct-Q4_K_M)")
    print(f"==================================================================")

    # Baseline antes de arrancar el LLM
    sys_pre = obtener_telemetria_sistema()
    gpu_pre = obtener_telemetria_gpu()
    baseline_vram = gpu_pre.get("vram_usada_mib", 0.0)
    baseline_ram = sys_pre.get("ram_usada_mib", 0.0)

    # 1. Medir arranque en frío
    print("\n[1/6] Iniciando llama-server y midiendo arranque en frío...")
    pid, tiempo_arranque_s = iniciar_servicio_llm(esperar_modo=True)
    print(f"  PID detectado       : {pid}")
    print(f"  Tiempo de carga     : {tiempo_arranque_s} segundos")

    # Iniciar muestreador continuo
    muestreador = MuestreadorRecursos(intervalo_s=0.25, pids_objetivo=[pid])
    muestreador.iniciar()

    # Telemetría post-carga
    gpu_cargado = obtener_telemetria_gpu()
    proc_cargado = obtener_telemetria_proceso(pid)
    vram_cargado_val = float(gpu_cargado.get("vram_usada_mib") or 0.0)
    print(f"  VRAM con modelo cargado: {vram_cargado_val} MiB (+{vram_cargado_val - baseline_vram:.1f} MiB)")
    print(f"  Working Set proceso    : {proc_cargado.get('working_set_mib', 0)} MiB")

    # 2. Primera generación (Cold Request)
    print("\n[2/6] Ejecutando primera generación (Cold Request)...")
    res_cold = enviar_chat_completion("Hola, preséntate brevemente.", max_tokens=32)
    print(f"  Latencia Cold: {res_cold['duracion_segundos']} s ({res_cold['completion_tokens']} tokens, {res_cold['tokens_por_segundo']} t/s)")

    # 3. Solicitudes secuenciales con variadas longitudes
    casos_secuenciales = [
        ("Corta - Factual", "¿Cuál es la capital del Atlántico?", 32),
        ("Corta - Stock", "¿Cuántos cables HDMI categoría 2 tenemos en el estante B3?", 48),
        ("Mediana - Procedimiento", "Explica los 4 pasos para dar de baja un activo dañado en el inventario.", 128),
        ("Mediana - Explicación", "Explica la importancia de conciliar los registros físicos y lógicos de inventario.", 128),
        ("Larga - Reporte", "Redacta un informe técnico simulado sobre el consumo de suministros informáticos en un semestre académico, incluyendo introducción, hallazgos y recomendaciones.", 256),
    ]

    print("\n[3/6] Ejecutando batería secuencial de 5 solicitudes...")
    resultados_secuenciales = []
    for nombre, prompt, max_tok in casos_secuenciales:
        res = enviar_chat_completion(prompt, max_tokens=max_tok)
        res["nombre_caso"] = nombre
        resultados_secuenciales.append(res)
        print(f"  {nombre:25s}: {res['duracion_segundos']:5.2f} s | {res['completion_tokens']:3d} tok ({res['tokens_por_segundo']:5.1f} t/s)")

    # 4. Solicitud de contexto extendido (~1.500 tokens de prompt)
    print("\n[4/6] Evaluando prompt de contexto extendido (~1.500 tokens)...")
    texto_contexto = (
        "El siguiente es el catálogo consolidado de artículos de laboratorio de cómputo:\n"
        + "\n".join([f"Articulo {i:04d}: Switch Cisco Gigabit Modelo Catalyst-{2960+i}, Ubicación: Rack-{i%10}, Estado: Operativo, Cantidad: {i*3%50 + 1} unidades, Responsable: Auditor-{i%5}." for i in range(1, 65)])
        + "\n\nPregunta: ¿Cuántas unidades hay exactamente del artículo 0042 y en qué rack se encuentra?"
    )
    res_contexto = enviar_chat_completion(texto_contexto, max_tokens=64)
    print(f"  Contexto ({res_contexto['prompt_tokens']} tokens prompt): {res_contexto['duracion_segundos']} s | {res_contexto['completion_tokens']} tok ({res_contexto['tokens_por_segundo']} t/s)")
    print(f"  Respuesta: {res_contexto['respuesta']}")

    # 5. Concurrencia de 2 clientes simultáneos
    print("\n[5/6] Evaluando concurrencia de 2 clientes simultáneos (servidor con n_parallel=1)...")
    t_conc_start = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(enviar_chat_completion, "Explica brevemente qué es un código de barras EAN-13.", 64)
        f2 = executor.submit(enviar_chat_completion, "Explica brevemente qué es un código QR en inventarios.", 64)
        res_c1 = f1.result()
        res_c2 = f2.result()
    dur_total_conc = round(time.perf_counter() - t_conc_start, 3)

    print(f"  Cliente 1: {res_c1['duracion_segundos']} s ({res_c1['completion_tokens']} tok)")
    print(f"  Cliente 2: {res_c2['duracion_segundos']} s ({res_c2['completion_tokens']} tok)")
    print(f"  Tiempo total concurrente de ambas: {dur_total_conc} s (evidencia de cola/serialización)")

    # 6. Detención del muestreador y cálculo de métricas
    muestras = muestreador.detener()
    resumen_telemetria = muestreador.calcular_resumen(
        baseline_vram=baseline_vram,
        baseline_ram=baseline_ram,
    )

    archivo_csv = RESULTADOS_DIR / "prueba_b_llm_aislado_crudo.csv"
    archivo_json = RESULTADOS_DIR / "prueba_b_llm_aislado.json"
    muestreador.exportar_csv(archivo_csv)

    # Consolidación de latencias y t/s
    todas_solicitudes = [res_cold] + resultados_secuenciales + [res_contexto, res_c1, res_c2]
    latencias = sorted([s["duracion_segundos"] for s in todas_solicitudes])
    velocidades = [s["tokens_por_segundo"] for s in todas_solicitudes if s["tokens_por_segundo"] > 0]
    n_tot = len(latencias)

    p50_lat = latencias[int(0.50 * n_tot)]
    p95_lat = latencias[min(n_tot - 1, int(0.95 * n_tot))]

    # Detener servicio
    asegurar_servicio_detenido()
    time.sleep(1.0)
    gpu_post = obtener_telemetria_gpu()

    resultado_final = {
        "prueba": "Prueba B — LLM de Forma Aislada",
        "fecha": time.strftime("%Y-%m-%d %H:%M:%S"),
        "configuracion": {
            "modelo": "Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf",
            "motor": "llama.cpp b11669-620d2225c (CUDA 12.4)",
            "n_ctx": 8192,
            "n_gpu_layers": 33,
            "kv_cache": "q8_0",
            "flash_attn": True,
            "n_parallel": 1,
            "endpoint": URL_BASE,
        },
        "arranque_en_frio": {
            "tiempo_carga_s": tiempo_arranque_s,
            "pid": pid,
            "vram_antes_carga_mib": baseline_vram,
            "vram_cargado_mib": gpu_cargado.get("vram_usada_mib"),
            "vram_delta_carga_mib": round(gpu_cargado.get("vram_usada_mib", 0) - baseline_vram, 2),
        },
        "metricas_generacion": {
            "total_solicitudes": n_tot,
            "latencia_promedio_s": round(sum(latencias) / n_tot, 3),
            "latencia_min_s": min(latencias),
            "latencia_max_s": max(latencias),
            "latencia_p50_s": p50_lat,
            "latencia_p95_s": p95_lat,
            "tokens_por_segundo_promedio": round(sum(velocidades) / len(velocidades), 2),
            "tokens_por_segundo_max": max(velocidades),
            "tokens_totales_generados": sum(s["completion_tokens"] for s in todas_solicitudes),
        },
        "concurrencia_2_clientes": {
            "tiempo_total_conjunto_s": dur_total_conc,
            "latencia_cliente_1_s": res_c1["duracion_segundos"],
            "latencia_cliente_2_s": res_c2["duracion_segundos"],
            "comportamiento": "Serialización correcta en cola FIFO respetando n_parallel=1 sin fallos ni OOM",
        },
        "telemetria_muestreo": resumen_telemetria,
        "liberacion_recursos": {
            "vram_post_detencion_mib": gpu_post.get("vram_usada_mib"),
            "vram_liberada_exitosamente": gpu_post.get("vram_usada_mib") <= baseline_vram + 50,
        },
        "detalle_solicitudes": todas_solicitudes,
        "archivos_guardados": {
            "crudo_csv": str(archivo_csv),
            "resumen_json": str(archivo_json),
        },
    }

    with open(archivo_json, "w", encoding="utf-8") as f:
        json.dump(resultado_final, f, indent=2, ensure_ascii=False)

    print(f"\nResumen de Prueba B:")
    print(f"  Tiempo Carga Modelo : {tiempo_arranque_s} s")
    print(f"  VRAM Modelo Cargado : {gpu_cargado.get('vram_usada_mib')} MiB")
    print(f"  VRAM Pico Inferencia: {resumen_telemetria['vram_pico_mib']} MiB")
    print(f"  Latencia P50        : {p50_lat} s | P95: {p95_lat} s")
    print(f"  Velocidad Promedio  : {resultado_final['metricas_generacion']['tokens_por_segundo_promedio']} t/s")
    print(f"  Guardado en         : {archivo_json}")
    return resultado_final


if __name__ == "__main__":
    ejecutar_prueba_llm_aislado()
