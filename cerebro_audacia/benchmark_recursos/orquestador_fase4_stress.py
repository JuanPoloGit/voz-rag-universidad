"""Orquestador Maestro de la Fase 4 de AudacIA: Pruebas de Estrés y Escalabilidad.

Ejecuta de forma secuencial y controlada:
1. Arranque en frío y línea base aislada (1 cliente, modelo caliente y frío).
2. Progresión de ráfagas (Burst) en n_parallel=1: 1, 2, 3, 4, 5, 8 y 10 clientes (3 rondas c/u).
3. Prueba de uso sostenido (Sustained Load) de 10 usuarios virtuales durante 5 minutos (300 s).
4. Pruebas comparativas de ranuras paralelas: n_parallel=2 y n_parallel=4.
5. Restauración estricta del entorno a su configuración base (n_parallel=1).
6. Consolidación de métricas en JSON y CSVs.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

from cerebro_audacia.benchmark_recursos.benchmark_stress_concurrencia import (
    DATOS_DIR,
    ejecutar_pico_concurrencia,
    ejecutar_solicitud_streaming,
    ejecutar_uso_sostenido_10_usuarios,
    reiniciar_llama_server_con_config,
    restaurar_configuracion_original,
)
from cerebro_audacia.benchmark_recursos.telemetria import (
    obtener_telemetria_gpu,
    obtener_telemetria_proceso,
    obtener_telemetria_sistema,
)


def ejecutar_orquestacion_completa() -> Dict[str, Any]:
    print("=" * 80)
    print(" AUDACIA — FASE 4: EJECUCIÓN DEL PROTOCOLO DE ESTRÉS Y ESCALABILIDAD")
    print("=" * 80)

    informe_final: Dict[str, Any] = {
        "fase": "Fase 4: Auditoría y Escalabilidad LLM",
        "fecha_ejecucion": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "hardware": {
            "gpu": "NVIDIA RTX 2000 Ada Generation Laptop GPU (8.188 MiB VRAM)",
            "ram": "16 GB DDR5 (15.51 GiB utilizables en Windows)",
            "os": "Windows 11 Pro",
        },
        "configuracion_baseline": {
            "modelo": "Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf",
            "n_ctx": 8192,
            "parallel_default": 1,
            "flash_attn": True,
            "kv_8bits": True,
        },
        "resultados_pico_parallel_1": {},
        "resultado_sostenido_10_usuarios_parallel_1": {},
        "resultados_comparativa_paralelismo": {},
        "restauracion_confirmada": False,
    }

    try:
        # ---------------------------------------------------------------------
        # ETAPA 1: LÍNEA BASE EN n_parallel=1 (Arranque en frío y warmup)
        # ---------------------------------------------------------------------
        print("\n>>> ETAPA 1: Iniciando servidor en configuración baseline (parallel=1, n_ctx=8192)...")
        pid_base, t_arranque_frio = reiniciar_llama_server_con_config(parallel=1, n_ctx=8192)
        tele_gpu_inicio = obtener_telemetria_gpu()
        
        # Warmup / primera inferencia
        print("    Ejecutando warmup (primera inferencia)...")
        warmup_res = ejecutar_solicitud_streaming(0, "A_corta")
        print(f"    Warmup completado en {warmup_res['latencia_total_s']:.2f}s | TTFT: {warmup_res['ttft_s']:.2f}s | Tokens/s: {warmup_res['tokens_por_segundo_generacion']:.1f}")

        informe_final["linea_base_arranque"] = {
            "pid_servidor": pid_base,
            "tiempo_arranque_frio_s": t_arranque_frio,
            "vram_en_reposo_modelo_mib": tele_gpu_inicio.get("vram_usada_mib", 0.0),
            "vram_libre_modelo_mib": tele_gpu_inicio.get("vram_libre_mib", 0.0),
            "warmup_latencia_s": warmup_res["latencia_total_s"],
            "warmup_ttft_s": warmup_res["ttft_s"],
        }

        # ---------------------------------------------------------------------
        # ETAPA 2: BATERÍA DE PICOS DE CONCURRENCIA EN n_parallel=1
        # Niveles: 1, 2, 3, 4, 5, 8 y 10 clientes concurrentes (3 rondas c/u)
        # ---------------------------------------------------------------------
        print("\n>>> ETAPA 2: Batería progresiva de picos de concurrencia coordinados (parallel=1)...")
        niveles_concurrencia = [1, 2, 3, 4, 5, 8, 10]
        for n_cli in niveles_concurrencia:
            res_pico = ejecutar_pico_concurrencia(
                num_clientes=n_cli,
                num_rondas=3,
                etiqueta_config="parallel_1"
            )
            informe_final["resultados_pico_parallel_1"][f"{n_cli}_clientes"] = res_pico
            time.sleep(2.0)

        # ---------------------------------------------------------------------
        # ETAPA 3: USO SOSTENIDO DE 10 USUARIOS VIRTUALES (5 MINUTOS)
        # ---------------------------------------------------------------------
        print("\n>>> ETAPA 3: Prueba obligatoria de uso sostenido (10 usuarios, 5 minutos)...")
        res_sostenido = ejecutar_uso_sostenido_10_usuarios(
            duracion_segundos=300.0,
            num_usuarios=10,
            etiqueta_config="parallel_1"
        )
        informe_final["resultado_sostenido_10_usuarios_parallel_1"] = res_sostenido
        time.sleep(3.0)

        # ---------------------------------------------------------------------
        # ETAPA 4: COMPARATIVA DE ESTRATEGIAS DE PARALELISMO (n_parallel=2 y 4)
        # ---------------------------------------------------------------------
        print("\n>>> ETAPA 4: Comparativa experimental de ranuras paralelas...")

        # A) Evaluar n_parallel=2
        print("\n--- Evaluando n_parallel=2 ---")
        pid_p2, t_arranque_p2 = reiniciar_llama_server_con_config(parallel=2, n_ctx=8192)
        tele_p2 = obtener_telemetria_gpu()
        print(f"    n_parallel=2 activo: VRAM={tele_p2.get('vram_usada_mib', 0)} MiB (Libre={tele_p2.get('vram_libre_mib', 0)} MiB)")
        ejecutar_solicitud_streaming(0, "A_corta") # warmup
        
        # Probar ráfagas en parallel=2 (2, 4 y 10 clientes)
        res_p2_2c = ejecutar_pico_concurrencia(2, num_rondas=2, etiqueta_config="parallel_2")
        res_p2_4c = ejecutar_pico_concurrencia(4, num_rondas=2, etiqueta_config="parallel_2")
        res_p2_10c = ejecutar_pico_concurrencia(10, num_rondas=2, etiqueta_config="parallel_2")

        informe_final["resultados_comparativa_paralelismo"]["parallel_2"] = {
            "vram_usada_base_mib": tele_p2.get("vram_usada_mib", 0.0),
            "vram_libre_base_mib": tele_p2.get("vram_libre_mib", 0.0),
            "burst_2_clientes": res_p2_2c,
            "burst_4_clientes": res_p2_4c,
            "burst_10_clientes": res_p2_10c,
        }

        # B) Evaluar n_parallel=4 (verificando seguridad de VRAM)
        print("\n--- Evaluando n_parallel=4 ---")
        pid_p4, t_arranque_p4 = reiniciar_llama_server_con_config(parallel=4, n_ctx=8192)
        tele_p4 = obtener_telemetria_gpu()
        print(f"    n_parallel=4 activo: VRAM={tele_p4.get('vram_usada_mib', 0)} MiB (Libre={tele_p4.get('vram_libre_mib', 0)} MiB)")
        ejecutar_solicitud_streaming(0, "A_corta") # warmup

        # Probar ráfagas en parallel=4 (4 y 10 clientes)
        res_p4_4c = ejecutar_pico_concurrencia(4, num_rondas=2, etiqueta_config="parallel_4")
        res_p4_10c = ejecutar_pico_concurrencia(10, num_rondas=2, etiqueta_config="parallel_4")

        informe_final["resultados_comparativa_paralelismo"]["parallel_4"] = {
            "vram_usada_base_mib": tele_p4.get("vram_usada_mib", 0.0),
            "vram_libre_base_mib": tele_p4.get("vram_libre_mib", 0.0),
            "burst_4_clientes": res_p4_4c,
            "burst_10_clientes": res_p4_10c,
        }

    finally:
        # ---------------------------------------------------------------------
        # ETAPA 5: RESTAURACIÓN ESTRICTA DEL ENTORNO DE DESARROLLO
        # ---------------------------------------------------------------------
        print("\n>>> ETAPA 5: Restaurando configuración y limpiando procesos temporales...")
        restaurar_configuracion_original()
        informe_final["restauracion_confirmada"] = True

    # Guardar informe consolidado final
    archivo_informe = DATOS_DIR / "informe_consolidado_fase4.json"
    with open(archivo_informe, "w", encoding="utf-8") as f:
        json.dump(informe_final, f, indent=2, ensure_ascii=False)

    print("\n" + "=" * 80)
    print(f" PROTOCOLO DE FASE 4 COMPLETADO EXITOSAMENTE")
    print(f" Informe maestro guardado en: {archivo_informe}")
    print("=" * 80)

    return informe_final


if __name__ == "__main__":
    ejecutar_orquestacion_completa()
