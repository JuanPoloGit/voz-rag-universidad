"""Suite Integral de Pruebas de Estrés, Concurrencia y Escalabilidad de AudacIA.

Fase 4: Auditoría Multiagente y Evaluación de Escalabilidad del LLM Central.
Soporta:
1. Modalidad Pico (Burst): Barrera coordinada instantánea a 1, 2, 3, 4, 5, 8 y 10 clientes.
2. Modalidad Sostenida (Sustained Load): 10 usuarios virtuales con pausas realistas (think times) durante 5 minutos.
3. Medición precisa de TTFT (Time-To-First-Token) mediante streaming SSE (stream=True).
4. Clases mixtas de prompts: Cortas (A), Medianas (B), RAG Largo (C), Generación Extensa (D), Inventario (F) y Auditoría (G).
5. Muestreo de telemetría de alta frecuencia (VRAM, RAM, CPU, GPU %, slots de llama-server).
6. Exportación de datos crudos por solicitud y por ronda (CSV + JSON).
"""

from __future__ import annotations

import csv
import json
import os
import random
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
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
DATOS_DIR = RESULTADOS_DIR / "datos_stress_llm"
DATOS_DIR.mkdir(parents=True, exist_ok=True)
PROVEEDOR_DIR = SCRIPT_DIR.parent / "proveedor_modelo"

URL_BASE = "http://127.0.0.1:8080"
URL_CHAT = f"{URL_BASE}/v1/chat/completions"
URL_HEALTH = f"{URL_BASE}/health"
URL_SLOTS = f"{URL_BASE}/slots"
MODEL_NAME = "Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf"

# ═════════════════════════════════════════════════════════════════════════════
# CATÁLOGO DE PROMPTS REPRESENTATIVOS POR CLASE
# ═════════════════════════════════════════════════════════════════════════════

PROMPTS_CATALOGO = {
    "A_corta": {
        "descripcion": "Pregunta breve factual o saludo (Visitante)",
        "messages": [
            {"role": "system", "content": "Eres HACU, asistente conversacional de la exhibición de AudacIA. Responde de forma cordial, clara y en máximo dos oraciones."},
            {"role": "user", "content": "Hola, ¿cuál es la misión principal de AudacIA en la universidad?"}
        ],
        "max_tokens": 64,
        "temperatura": 0.3
    },
    "B_mediana": {
        "descripcion": "Consulta operativa de inventario (HAKU Inventario)",
        "messages": [
            {"role": "system", "content": "Eres HAKU, asistente del sistema de inventario universitario. Consulta existencias y responde en formato conciso."},
            {"role": "user", "content": "¿Hay existencias disponibles del osciloscopio digital en el laboratorio de robótica y cuál es su código de referencia?"}
        ],
        "max_tokens": 128,
        "temperatura": 0.2
    },
    "C_larga_rag": {
        "descripcion": "Prompt largo con contexto documental Markdown tipo RAG (~800 tokens entrada)",
        "messages": [
            {"role": "system", "content": "Eres HACU. Responde utilizando exclusivamente los fragmentos documentales adjuntos."},
            {
                "role": "user",
                "content": (
                    "A continuación se presenta un extracto del portafolio didáctico de AudacIA:\n\n"
                    "# Proyecto Orion: Asistente Robótico Institucional\n"
                    "El proyecto Orion consiste en una plataforma robótica de servicio diseñada para la interacción en campus universitarios. "
                    "Cuenta con navegación autónoma LiDAR 2D, síntesis de voz fonética basada en redes neuronales, y un módulo conversacional "
                    "basado en modelos de lenguaje pequeños (SLM) cuantizados a 4 bits. Sus objetivos pedagógicos abarcan la demostración "
                    "práctica de robótica social para estudiantes de ingeniería mecatrónica y sistemas. En el año 2025 atendió a más de 1.200 "
                    "visitantes en la jornada de puertas abiertas.\n\n"
                    "# Proyecto Ágora: Aula Inteligente con Detección de Aforo\n"
                    "El sistema Ágora implementa visión artificial en el borde mediante cámaras estéreo y modelos YOLOv8 compactos para conteo "
                    "de personas en tiempo real sin almacenamiento de datos biométricos. Optimiza el consumo de energía y climatización en salas "
                    "de cómputo de la facultad de ingeniería.\n\n"
                    "Pregunta del visitante: Explica detalladamente en qué consiste el Proyecto Orion, qué tecnología utiliza para navegar y cómo "
                    "apoya la formación práctica de los estudiantes de ingeniería mecatrónica."
                )
            }
        ],
        "max_tokens": 192,
        "temperatura": 0.3
    },
    "D_extensa": {
        "descripcion": "Generación extensa explicativa (alta demanda de tokens)",
        "messages": [
            {"role": "system", "content": "Eres un arquitecto de software de IA. Provee una explicación técnica exhaustiva y estructurada."},
            {
                "role": "user",
                "content": (
                    "Realiza un análisis comparativo y detallado entre el uso de modelos de lenguaje locales cuantizados (como Llama 3.1 8B Q4) "
                    "frente a modelos comerciales en la nube (como GPT-4) para una universidad pública, abordando: 1) Soberanía y privacidad de datos, "
                    "2) Latencia y dependencia de red, 3) Costes de infraestructura GPU vs suscripción SaaS, y 4) Concurrencia de estudiantes."
                )
            }
        ],
        "max_tokens": 320,
        "temperatura": 0.3
    },
    "F_inventario_accion": {
        "descripcion": "Solicitud operativa con estructura transaccional (HAKU)",
        "messages": [
            {"role": "system", "content": "Eres el módulo de procesamiento de lenguaje natural de Haku Inventario. Identifica la intención y los parámetros."},
            {"role": "user", "content": "Registra una salida de 5 metros de cable de red categoría 6 para el laboratorio 304 a nombre del profesor Rodríguez."}
        ],
        "max_tokens": 128,
        "temperatura": 0.1
    },
    "G_auditoria_simulada": {
        "descripcion": "Auditoría analítica de tabla de transacciones (Agente Auditoría)",
        "messages": [
            {"role": "system", "content": "Eres el Agente de Auditoría de AudacIA. Analiza la siguiente tabla de transacciones e identifica inconsistencias o riesgos de control interno."},
            {
                "role": "user",
                "content": (
                    "Tabla de transacciones recientes:\n"
                    "| TX | Fecha | Usuario | Acción | Producto | Cantidad | Ubicación |\n"
                    "| TX-901 | 2026-10-14 09:12 | admin_lab | SALIDA | Multímetro Digital Fluke | 3 | Lab-Electrónica |\n"
                    "| TX-902 | 2026-10-14 11:30 | jpolo | PRESTAMO | Tarjeta FPGA Spartan-7 | 1 | Lab-Robótica |\n"
                    "| TX-903 | 2026-10-14 14:05 | auditor_tmp | AJUSTE | Cable UTP Cat6 305m | -2 | Bodega-Central |\n\n"
                    "Determina si la transacción TX-903 requiere justificación adicional de acuerdo a la normativa y emite un dictamen breve."
                )
            }
        ],
        "max_tokens": 160,
        "temperatura": 0.2
    }
}


def obtener_pid_llama_server() -> Optional[int]:
    """Obtiene el PID del proceso llama-server activo."""
    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command", "Get-Process -Name 'llama-server' | Select-Object -ExpandProperty Id -First 1"],
            encoding="utf-8",
            stderr=subprocess.DEVNULL,
        ).strip()
        if out:
            return int(out)
    except Exception:
        pass
    return None


def obtener_estado_slots() -> List[Dict[str, Any]]:
    """Consulta el endpoint /slots de llama-server si está disponible."""
    try:
        req = urllib.request.Request(URL_SLOTS)
        with urllib.request.urlopen(req, timeout=1.0) as r:
            return json.loads(r.read().decode())
    except Exception:
        return []


def ejecutar_solicitud_streaming(
    cliente_id: int,
    clase_prompt: str,
    barrier: Optional[threading.Barrier] = None,
    timeout_segundos: float = 120.0,
) -> Dict[str, Any]:
    """Ejecuta una solicitud HTTP con streaming SSE hacia llama-server midiendo TTFT exacto."""
    prompt_cfg = PROMPTS_CATALOGO[clase_prompt]
    payload = {
        "model": MODEL_NAME,
        "messages": prompt_cfg["messages"],
        "max_tokens": prompt_cfg["max_tokens"],
        "temperature": prompt_cfg["temperatura"],
        "stream": True,
    }
    data_bytes = json.dumps(payload).encode("utf-8")

    # Si hay barrera de sincronización, esperar a que todos los clientes estén listos
    if barrier is not None:
        barrier.wait()

    t_envio = time.perf_counter()
    timestamp_envio = datetime.now().isoformat()
    t_primer_token: Optional[float] = None
    t_final: Optional[float] = None
    tokens_generados = 0
    codigo_http = 0
    error_msg: Optional[str] = None
    texto_acumulado: List[str] = []

    req = urllib.request.Request(
        URL_CHAT,
        data=data_bytes,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout_segundos) as response:
            codigo_http = response.status
            for linea in response:
                linea_str = linea.decode("utf-8", errors="replace").strip()
                if not linea_str:
                    continue
                if linea_str.startswith("data: "):
                    contenido_data = linea_str[6:].strip()
                    if contenido_data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(contenido_data)
                        delta = chunk.get("choices", [{}])[0].get("delta", {})
                        texto_chunk = delta.get("content", "")
                        if texto_chunk:
                            if t_primer_token is None:
                                t_primer_token = time.perf_counter()
                            tokens_generados += 1
                            texto_acumulado.append(texto_chunk)
                    except Exception:
                        pass
            t_final = time.perf_counter()
    except urllib.error.HTTPError as he:
        codigo_http = he.code
        error_msg = f"HTTPError {he.code}: {he.reason}"
        t_final = time.perf_counter()
    except Exception as exc:
        codigo_http = 599
        error_msg = f"Exception: {type(exc).__name__} - {exc}"
        t_final = time.perf_counter()

    latencia_total_s = (t_final - t_envio) if t_final else (time.perf_counter() - t_envio)
    ttft_s = (t_primer_token - t_envio) if t_primer_token else latencia_total_s
    tiempo_generacion_s = (t_final - t_primer_token) if (t_final and t_primer_token) else 0.0

    tok_s_generacion = (tokens_generados / tiempo_generacion_s) if tiempo_generacion_s > 0 else 0.0
    tok_s_efectivo = (tokens_generados / latencia_total_s) if latencia_total_s > 0 else 0.0

    return {
        "cliente_id": cliente_id,
        "clase_prompt": clase_prompt,
        "timestamp_envio": timestamp_envio,
        "codigo_http": codigo_http,
        "exito": (codigo_http == 200 and error_msg is None and tokens_generados > 0),
        "error": error_msg,
        "ttft_s": round(ttft_s, 4),
        "latencia_total_s": round(latencia_total_s, 4),
        "tiempo_generacion_s": round(tiempo_generacion_s, 4),
        "tokens_generados": tokens_generados,
        "tokens_por_segundo_generacion": round(tok_s_generacion, 2),
        "tokens_por_segundo_efectivo": round(tok_s_efectivo, 2),
        "respuesta_resumida": "".join(texto_acumulado)[:120].replace("\n", " "),
    }


# ═════════════════════════════════════════════════════════════════════════════
# MODALIDAD 1: PICO DE CONCURRENCIA COORDINADO (BURST)
# ═════════════════════════════════════════════════════════════════════════════

def ejecutar_pico_concurrencia(
    num_clientes: int,
    num_rondas: int = 3,
    distribucion_prompts: Optional[List[str]] = None,
    etiqueta_config: str = "parallel_1",
) -> Dict[str, Any]:
    """Ejecuta una prueba de pico coordinado con N clientes simultáneos lanzados por barrera."""
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Iniciando Pico Concurrencia: {num_clientes} clientes | Config: {etiqueta_config} | Rondas: {num_rondas}")
    
    clases_disponibles = list(PROMPTS_CATALOGO.keys())
    resultados_rondas: List[Dict[str, Any]] = []
    solicitudes_crudas_todas: List[Dict[str, Any]] = []

    pid_llm = obtener_pid_llama_server()

    for ronda in range(1, num_rondas + 1):
        barrier = threading.Barrier(num_clientes)
        threads: List[threading.Thread] = []
        resultados_clientes: List[Dict[str, Any]] = [None] * num_clientes

        def _worker(cid: int, cprompt: str):
            res = ejecutar_solicitud_streaming(cid, cprompt, barrier=barrier)
            res["ronda"] = ronda
            res["num_clientes"] = num_clientes
            res["config_paralelismo"] = etiqueta_config
            resultados_clientes[cid] = res

        pids_list = [pid_llm] if pid_llm else None
        muestreador = MuestreadorRecursos(intervalo_s=0.1, pids_objetivo=pids_list)
        muestreador.iniciar()

        t_ronda_inicio = time.perf_counter()

        for c_idx in range(num_clientes):
            if distribucion_prompts and len(distribucion_prompts) == num_clientes:
                prompt_asig = distribucion_prompts[c_idx]
            else:
                prompt_asig = clases_disponibles[c_idx % len(clases_disponibles)]
            th = threading.Thread(target=_worker, args=(c_idx, prompt_asig))
            threads.append(th)
            th.start()

        for th in threads:
            th.join()

        t_ronda_fin = time.perf_counter()
        duracion_ronda_s = t_ronda_fin - t_ronda_inicio
        muestras_ronda = muestreador.detener()
        resumen_tele = muestreador.calcular_resumen()

        solicitudes_crudas_todas.extend(resultados_clientes)

        # Métricas de la ronda
        latencias = [r["latencia_total_s"] for r in resultados_clientes if r["exito"]]
        ttfts = [r["ttft_s"] for r in resultados_clientes if r["exito"]]
        tokens_totales = sum(r["tokens_generados"] for r in resultados_clientes if r["exito"])
        exitos = sum(1 for r in resultados_clientes if r["exito"])
        fallos = num_clientes - exitos

        p50_lat = statistics.median(latencias) if latencias else 0.0
        p95_lat = statistics.quantiles(latencias, n=20)[-1] if len(latencias) >= 20 else (max(latencias) if latencias else 0.0)
        p50_ttft = statistics.median(ttfts) if ttfts else 0.0
        p95_ttft = statistics.quantiles(ttfts, n=20)[-1] if len(ttfts) >= 20 else (max(ttfts) if ttfts else 0.0)
        max_lat = max(latencias) if latencias else 0.0
        min_lat = min(latencias) if latencias else 0.0

        throughput_agregado = round(tokens_totales / duracion_ronda_s, 2) if duracion_ronda_s > 0 else 0.0

        vram_pico = resumen_tele.get("vram_pico_mib", 0.0)
        vram_libre_min = min([m.get("vram_libre_mib", 7956.0) for m in muestras_ronda]) if muestras_ronda else 0.0
        ram_pico_gib = round(resumen_tele.get("ram_pico_mib", 0.0) / 1024, 2)
        gpu_util_pico = resumen_tele.get("gpu_util", {}).get("max", 0.0)

        res_ronda = {
            "ronda": ronda,
            "num_clientes": num_clientes,
            "duracion_total_s": round(duracion_ronda_s, 3),
            "solicitudes_exitosas": exitos,
            "solicitudes_fallidas": fallos,
            "tokens_generados_total": tokens_totales,
            "throughput_agregado_tok_s": throughput_agregado,
            "ttft_p50_s": round(p50_ttft, 3),
            "ttft_p95_s": round(p95_ttft, 3),
            "latencia_p50_s": round(p50_lat, 3),
            "latencia_p95_s": round(p95_lat, 3),
            "latencia_min_s": round(min_lat, 3),
            "latencia_max_s": round(max_lat, 3),
            "vram_usada_pico_mib": vram_pico,
            "vram_libre_min_mib": vram_libre_min,
            "ram_usada_pico_gib": ram_pico_gib,
            "gpu_util_pico_pct": gpu_util_pico,
        }
        resultados_rondas.append(res_ronda)
        print(f"   Ronda {ronda}: Duración={duracion_ronda_s:.2f}s | Éxitos={exitos}/{num_clientes} | TTFT P50={p50_ttft:.2f}s | Lat P50={p50_lat:.2f}s | Lat Max={max_lat:.2f}s | VRAM Pico={res_ronda['vram_usada_pico_mib']} MiB")
        time.sleep(1.0)

    # Consolidado de las rondas
    latencias_global = [r["latencia_total_s"] for r in solicitudes_crudas_todas if r["exito"]]
    ttfts_global = [r["ttft_s"] for r in solicitudes_crudas_todas if r["exito"]]
    total_tokens_global = sum(r["tokens_generados"] for r in solicitudes_crudas_todas if r["exito"])
    total_exitos = sum(1 for r in solicitudes_crudas_todas if r["exito"])
    total_intentos = len(solicitudes_crudas_todas)

    consolidado = {
        "num_clientes": num_clientes,
        "config_paralelismo": etiqueta_config,
        "total_rondas": num_rondas,
        "total_solicitudes": total_intentos,
        "total_exitosas": total_exitos,
        "tasa_exito_pct": round((total_exitos / total_intentos * 100), 1) if total_intentos > 0 else 0.0,
        "ttft_p50_s": round(statistics.median(ttfts_global), 3) if ttfts_global else 0.0,
        "ttft_p95_s": round(statistics.quantiles(ttfts_global, n=20)[-1] if len(ttfts_global) >= 20 else max(ttfts_global), 3) if ttfts_global else 0.0,
        "latencia_p50_s": round(statistics.median(latencias_global), 3) if latencias_global else 0.0,
        "latencia_p95_s": round(statistics.quantiles(latencias_global, n=20)[-1] if len(latencias_global) >= 20 else max(latencias_global), 3) if latencias_global else 0.0,
        "latencia_max_s": round(max(latencias_global), 3) if latencias_global else 0.0,
        "latencia_promedio_s": round(statistics.mean(latencias_global), 3) if latencias_global else 0.0,
        "throughput_agregado_promedio_tok_s": round(statistics.mean([r["throughput_agregado_tok_s"] for r in resultados_rondas]), 2) if resultados_rondas else 0.0,
        "vram_max_global_mib": max([r["vram_usada_pico_mib"] for r in resultados_rondas]) if resultados_rondas else 0.0,
        "rondas_detalle": resultados_rondas,
    }

    # Guardar datos crudos en CSV y JSON
    archivo_csv = DATOS_DIR / f"pico_{num_clientes}_clientes_{etiqueta_config}_crudo.csv"
    with open(archivo_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(solicitudes_crudas_todas[0].keys()))
        writer.writeheader()
        writer.writerows(solicitudes_crudas_todas)

    archivo_json = DATOS_DIR / f"pico_{num_clientes}_clientes_{etiqueta_config}_resumen.json"
    with open(archivo_json, "w", encoding="utf-8") as f:
        json.dump(consolidado, f, indent=2, ensure_ascii=False)

    return consolidado


# ═════════════════════════════════════════════════════════════════════════════
# MODALIDAD 2: USO SOSTENIDO (SUSTAINED LOAD — 10 USUARIOS, 5 MINUTOS)
# ═════════════════════════════════════════════════════════════════════════════

def ejecutar_uso_sostenido_10_usuarios(
    duracion_segundos: float = 300.0,
    num_usuarios: int = 10,
    etiqueta_config: str = "parallel_1",
) -> Dict[str, Any]:
    """Ejecuta una prueba de carga sostenida simulando 10 usuarios virtuales con pausas realistas."""
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Iniciando Carga Sostenida: {num_usuarios} usuarios virtuales durante {duracion_segundos:.0f}s ({duracion_segundos/60:.1f} min)...")
    
    stop_event = threading.Event()
    solicitudes_crudas: List[Dict[str, Any]] = []
    lock_resultados = threading.Lock()
    clases_disponibles = list(PROMPTS_CATALOGO.keys())
    pid_llm = obtener_pid_llama_server()

    pids_list = [pid_llm] if pid_llm else None
    muestreador = MuestreadorRecursos(intervalo_s=0.5, pids_objetivo=pids_list)
    muestreador.iniciar()

    t_inicio_global = time.perf_counter()

    def _usuario_virtual(uid: int):
        turno_idx = 0
        while not stop_event.is_set():
            clase_prompt = clases_disponibles[(uid + turno_idx) % len(clases_disponibles)]
            res = ejecutar_solicitud_streaming(uid, clase_prompt, barrier=None, timeout_segundos=180.0)
            res["usuario_virtual_id"] = uid
            res["turno_numero"] = turno_idx
            res["config_paralelismo"] = etiqueta_config
            
            with lock_resultados:
                solicitudes_crudas.append(res)
            
            turno_idx += 1
            if stop_event.is_set():
                break

            # Think time realista: pausa entre preguntas de 1.5 a 3.5 segundos
            pausa = random.uniform(1.5, 3.5)
            # Despertar de inmediato si se cumple el tiempo global
            stop_event.wait(timeout=pausa)

    threads = [threading.Thread(target=_usuario_virtual, args=(i,)) for i in range(num_usuarios)]
    for th in threads:
        th.start()

    # Monitoreo en vivo durante la duración
    t_fin_objetivo = t_inicio_global + duracion_segundos
    try:
        while time.perf_counter() < t_fin_objetivo:
            time.sleep(10.0)
            transcurrido = time.perf_counter() - t_inicio_global
            with lock_resultados:
                n_completadas = len(solicitudes_crudas)
                n_exitos = sum(1 for r in solicitudes_crudas if r["exito"])
            print(f"   [{transcurrido:5.1f}s / {duracion_segundos}s] Solicitudes completadas: {n_completadas} (Éxitos: {n_exitos})", flush=True)
    finally:
        stop_event.set()
        for th in threads:
            th.join(timeout=30.0)

    t_fin_global = time.perf_counter()
    duracion_real_s = t_fin_global - t_inicio_global
    muestras_sostenida = muestreador.detener()
    resumen_tele = muestreador.calcular_resumen()

    latencias = [r["latencia_total_s"] for r in solicitudes_crudas if r["exito"]]
    ttfts = [r["ttft_s"] for r in solicitudes_crudas if r["exito"]]
    tokens_totales = sum(r["tokens_generados"] for r in solicitudes_crudas if r["exito"])
    exitos = sum(1 for r in solicitudes_crudas if r["exito"])
    fallos = len(solicitudes_crudas) - exitos

    vram_inicial = muestras_sostenida[0].get("vram_usada_mib", 0.0) if muestras_sostenida else 0.0
    vram_pico = resumen_tele.get("vram_pico_mib", 0.0)
    vram_delta = round(vram_pico - vram_inicial, 1)
    vram_libre_min = min([m.get("vram_libre_mib", 7956.0) for m in muestras_sostenida]) if muestras_sostenida else 0.0
    ram_pico_gib = round(resumen_tele.get("ram_pico_mib", 0.0) / 1024, 2)
    gpu_prom = resumen_tele.get("gpu_util", {}).get("promedio", 0.0)
    gpu_pico = resumen_tele.get("gpu_util", {}).get("max", 0.0)

    consolidado = {
        "tipo_prueba": "uso_sostenido_10_usuarios",
        "config_paralelismo": etiqueta_config,
        "duracion_programada_s": duracion_segundos,
        "duracion_real_s": round(duracion_real_s, 2),
        "num_usuarios_virtuales": num_usuarios,
        "solicitudes_totales": len(solicitudes_crudas),
        "solicitudes_exitosas": exitos,
        "solicitudes_fallidas": fallos,
        "tasa_exito_pct": round((exitos / len(solicitudes_crudas) * 100), 2) if solicitudes_crudas else 0.0,
        "tokens_generados_totales": tokens_totales,
        "throughput_agregado_tok_s": round(tokens_totales / duracion_real_s, 2) if duracion_real_s > 0 else 0.0,
        "solicitudes_por_minuto": round((len(solicitudes_crudas) / duracion_real_s) * 60, 2) if duracion_real_s > 0 else 0.0,
        "ttft_p50_s": round(statistics.median(ttfts), 3) if ttfts else 0.0,
        "ttft_p95_s": round(statistics.quantiles(ttfts, n=20)[-1] if len(ttfts) >= 20 else max(ttfts), 3) if ttfts else 0.0,
        "latencia_p50_s": round(statistics.median(latencias), 3) if latencias else 0.0,
        "latencia_p95_s": round(statistics.quantiles(latencias, n=20)[-1] if len(latencias) >= 20 else max(latencias), 3) if latencias else 0.0,
        "latencia_promedio_s": round(statistics.mean(latencias), 3) if latencias else 0.0,
        "latencia_max_s": round(max(latencias), 3) if latencias else 0.0,
        "vram_inicial_mib": vram_inicial,
        "vram_pico_mib": vram_pico,
        "vram_delta_crecimiento_mib": vram_delta,
        "vram_libre_min_mib": vram_libre_min,
        "ram_usada_pico_gib": ram_pico_gib,
        "gpu_carga_promedio_pct": gpu_prom,
        "gpu_carga_pico_pct": gpu_pico,
    }

    # Guardar telemetría y datos crudos
    archivo_csv = DATOS_DIR / f"uso_sostenido_10_usuarios_{etiqueta_config}_crudo.csv"
    if solicitudes_crudas:
        with open(archivo_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(solicitudes_crudas[0].keys()))
            writer.writeheader()
            writer.writerows(solicitudes_crudas)

    archivo_telemetria_csv = DATOS_DIR / f"uso_sostenido_10_usuarios_{etiqueta_config}_telemetria.csv"
    muestreador.exportar_csv(archivo_telemetria_csv)

    archivo_json = DATOS_DIR / f"uso_sostenido_10_usuarios_{etiqueta_config}_resumen.json"
    with open(archivo_json, "w", encoding="utf-8") as f:
        json.dump(consolidado, f, indent=2, ensure_ascii=False)

    print(f"\n[FIN USO SOSTENIDO] Completadas={len(solicitudes_crudas)} | Exitos={exitos} | Throughput={consolidado['throughput_agregado_tok_s']} tok/s | P50={consolidado['latencia_p50_s']}s | P95={consolidado['latencia_p95_s']}s | Delta VRAM={consolidado['vram_delta_crecimiento_mib']} MiB")
    return consolidado


# ═════════════════════════════════════════════════════════════════════════════
# GESTIÓN Y CAMBIO TEMPORAL DE CONFIGURACIÓN DEL SERVICIO LLM
# ═════════════════════════════════════════════════════════════════════════════

def reiniciar_llama_server_con_config(
    parallel: int = 1,
    n_ctx: int = 8192,
) -> Tuple[int, float]:
    """Detiene y reinicia de forma limpia llama-server con parámetros controlados."""
    script_detener = PROVEEDOR_DIR / "detener_llama_server.ps1"
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script_detener)], capture_output=True)
    time.sleep(2.0)

    # Modificar temporalmente config_llm.json
    archivo_config = PROVEEDOR_DIR / "config_llm.json"
    with open(archivo_config, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    cfg["perfiles"]["desarrollo_rtx2000ada"]["parallel"] = parallel
    cfg["perfiles"]["desarrollo_rtx2000ada"]["n_ctx"] = n_ctx

    with open(archivo_config, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)

    # Iniciar servicio (sin -Esperar para que PowerShell no bloquee y deje el proceso detached)
    script_iniciar = PROVEEDOR_DIR / "iniciar_llama_server.ps1"
    t0 = time.perf_counter()
    subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script_iniciar), "-Perfil", "desarrollo_rtx2000ada"],
        capture_output=True,
        timeout=60,
    )

    t_listo = 0.0
    for _ in range(60):
        time.sleep(0.5)
        try:
            req = urllib.request.Request(URL_HEALTH)
            with urllib.request.urlopen(req, timeout=1.0) as r:
                d = json.loads(r.read().decode())
                if d.get("status") in ("ok", "ready"):
                    t_listo = time.perf_counter() - t0
                    break
        except Exception:
            pass

    pid = obtener_pid_llama_server() or 0
    print(f"Servidor LLM iniciado con parallel={parallel}, n_ctx={n_ctx} (PID={pid}, Tiempo arranque={t_listo:.2f}s)")
    return pid, round(t_listo, 3)


def restaurar_configuracion_original() -> None:
    """Restaura estrictamente la configuración original del entorno de desarrollo."""
    print("\n[RESTAURACIÓN] Restaurando configuración original de desarrollo (parallel=1, n_ctx=8192)...")
    reiniciar_llama_server_con_config(parallel=1, n_ctx=8192)
    print("[RESTAURACIÓN] Configuración restaurada con éxito.")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="AudacIA Concurrency Benchmark")
    parser.add_argument("--modo", choices=["pico", "sostenido", "completo"], default="completo")
    parser.add_argument("--clientes", type=int, default=10)
    parser.add_argument("--duracion", type=float, default=300.0)
    args = parser.parse_args()

    if args.modo == "pico":
        ejecutar_pico_concurrencia(args.clientes)
    elif args.modo == "sostenido":
        ejecutar_uso_sostenido_10_usuarios(args.duracion, args.clientes)
    else:
        print("Modo completo se ejecutará desde el orquestador principal.")
