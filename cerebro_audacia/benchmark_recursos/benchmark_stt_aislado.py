"""Prueba C — STT de Forma Aislada para AudacIA.

Evalúa el motor de transcripción local Faster-Whisper (CTranslate2 CUDA float16):
1. Tiempo de inicialización y carga del modelo en GPU.
2. VRAM y RAM atribuible al proceso.
3. Transcripción de los 5 audios reales de prueba (movimientos, stock, orden, costeño, código).
4. Medición de latencia, duración de audio y Real-Time Factor (RTF = latencia / dur_audio).
5. Evaluación con beam_size=1 (rápido) y beam_size=3 (alta precisión).
6. Telemetría continua con muestreador desacoplado.
"""

from __future__ import annotations

import json
import os
import sys
import time
import wave
from pathlib import Path
from typing import Any, Dict, List

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
AUDIOS_DIR = SCRIPT_DIR / "audios"

# Configuración de DLLs de CUDA en Windows para CTranslate2
def configurar_cuda():
    candidatos = [
        os.path.join(sys.prefix, "Lib", "site-packages", "nvidia", "cublas", "bin"),
        os.path.join(sys.prefix, "Lib", "site-packages", "nvidia", "cuda_nvrtc", "bin"),
        os.path.join(sys.prefix, "Lib", "site-packages", "nvidia", "cudnn", "bin"),
        r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.4\bin",
    ]
    for c in candidatos:
        if os.path.isdir(c):
            if c not in os.environ.get("PATH", ""):
                os.environ["PATH"] = c + os.pathsep + os.environ.get("PATH", "")
            if hasattr(os, "add_dll_directory"):
                try:
                    os.add_dll_directory(c)
                except Exception:
                    pass

configurar_cuda()


def obtener_duracion_wav(ruta: Path) -> float:
    try:
        with wave.open(str(ruta), "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
            return round(frames / float(rate), 3)
    except Exception:
        return 0.0


def ejecutar_prueba_stt_aislado() -> Dict[str, Any]:
    print(f"\n==================================================================")
    print(f" PRUEBA C — STT DE FORMA AISLADA (Faster-Whisper base / CUDA float16)")
    print(f"==================================================================")

    pid_actual = os.getpid()
    sys_pre = obtener_telemetria_sistema()
    gpu_pre = obtener_telemetria_gpu()
    baseline_vram = gpu_pre.get("vram_usada_mib", 0.0)
    baseline_ram = sys_pre.get("ram_usada_mib", 0.0)

    # 1. Carga del modelo
    print("\n[1/4] Inicializando Faster-Whisper 'base' en CUDA float16...")
    t0_carga = time.perf_counter()
    from faster_whisper import WhisperModel
    model = WhisperModel("base", device="cuda", compute_type="float16")
    tiempo_carga_s = round(time.perf_counter() - t0_carga, 3)

    gpu_post_carga = obtener_telemetria_gpu()
    proc_post_carga = obtener_telemetria_proceso(pid_actual)
    vram_stt_carga = round(gpu_post_carga.get("vram_usada_mib", 0) - baseline_vram, 2)

    print(f"  Tiempo de carga     : {tiempo_carga_s} s")
    print(f"  VRAM consumida STT  : {vram_stt_carga} MiB")
    print(f"  Working Set proceso : {proc_post_carga.get('working_set_mib')} MiB")

    # Iniciar muestreo continuo
    muestreador = MuestreadorRecursos(intervalo_s=0.1, pids_objetivo=[pid_actual])
    muestreador.iniciar()

    # 2. Audios de prueba
    archivos_audio = sorted(list(AUDIOS_DIR.glob("*.wav")))
    if not archivos_audio:
        print(f"ERROR: No se encontraron audios en {AUDIOS_DIR}")
        return {"error": "Sin audios"}

    print(f"\n[2/4] Transcribiendo {len(archivos_audio)} audios con beam_size=1 (Modo Rápido)...")
    resultados_beam1 = []
    for audio_path in archivos_audio:
        dur_audio = obtener_duracion_wav(audio_path)
        t0 = time.perf_counter()
        segments, info = model.transcribe(str(audio_path), language="es", beam_size=1)
        texto = " ".join([seg.text.strip() for seg in segments]).strip()
        latencia = round(time.perf_counter() - t0, 4)
        rtf = round(latencia / max(0.001, dur_audio), 4)

        resultados_beam1.append({
            "archivo": audio_path.name,
            "duracion_audio_s": dur_audio,
            "latencia_s": latencia,
            "rtf": rtf,
            "transcripcion": texto,
            "idioma_prob": round(info.language_probability, 3),
        })
        print(f"  {audio_path.name:18s} ({dur_audio:4.2f}s) -> Latencia: {latencia*1000:6.1f} ms | RTF: {rtf:6.4f} | \"{texto[:45]}...\"")

    print(f"\n[3/4] Transcribiendo {len(archivos_audio)} audios con beam_size=3 (Modo Precisión)...")
    resultados_beam3 = []
    for audio_path in archivos_audio:
        dur_audio = obtener_duracion_wav(audio_path)
        t0 = time.perf_counter()
        segments, info = model.transcribe(str(audio_path), language="es", beam_size=3)
        texto = " ".join([seg.text.strip() for seg in segments]).strip()
        latencia = round(time.perf_counter() - t0, 4)
        rtf = round(latencia / max(0.001, dur_audio), 4)

        resultados_beam3.append({
            "archivo": audio_path.name,
            "duracion_audio_s": dur_audio,
            "latencia_s": latencia,
            "rtf": rtf,
            "transcripcion": texto,
            "idioma_prob": round(info.language_probability, 3),
        })
        print(f"  {audio_path.name:18s} ({dur_audio:4.2f}s) -> Latencia: {latencia*1000:6.1f} ms | RTF: {rtf:6.4f} | \"{texto[:45]}...\"")

    # 4. Detener muestreador y exportar
    print("\n[4/4] Consolidando telemetría y resultados...")
    muestras = muestreador.detener()
    resumen_telemetria = muestreador.calcular_resumen(
        baseline_vram=baseline_vram,
        baseline_ram=baseline_ram,
    )

    archivo_csv = RESULTADOS_DIR / "prueba_c_stt_aislado_crudo.csv"
    archivo_json = RESULTADOS_DIR / "prueba_c_stt_aislado.json"
    muestreador.exportar_csv(archivo_csv)

    latencias_b1 = [r["latencia_s"] for r in resultados_beam1]
    latencias_b3 = [r["latencia_s"] for r in resultados_beam3]
    rtf_b1 = [r["rtf"] for r in resultados_beam1]
    rtf_b3 = [r["rtf"] for r in resultados_beam3]

    resultado_final = {
        "prueba": "Prueba C — STT de Forma Aislada",
        "fecha": time.strftime("%Y-%m-%d %H:%M:%S"),
        "configuracion": {
            "modelo": "faster-whisper base",
            "backend": "CTranslate2",
            "dispositivo": "cuda",
            "compute_type": "float16",
            "modelo_servidor_definitivo": "medium / large-v3 en RTX 5070 Ti 16GB",
        },
        "carga_modelo": {
            "tiempo_carga_s": tiempo_carga_s,
            "vram_delta_carga_mib": vram_stt_carga,
            "working_set_proceso_mib": proc_post_carga.get("working_set_mib"),
            "private_bytes_proceso_mib": proc_post_carga.get("private_bytes_mib"),
        },
        "resumen_beam_1": {
            "latencia_promedio_s": round(sum(latencias_b1) / len(latencias_b1), 4),
            "latencia_min_s": min(latencias_b1),
            "latencia_max_s": max(latencias_b1),
            "rtf_promedio": round(sum(rtf_b1) / len(rtf_b1), 4),
            "rtf_min": min(rtf_b1),
            "detalles": resultados_beam1,
        },
        "resumen_beam_3": {
            "latencia_promedio_s": round(sum(latencias_b3) / len(latencias_b3), 4),
            "latencia_min_s": min(latencias_b3),
            "latencia_max_s": max(latencias_b3),
            "rtf_promedio": round(sum(rtf_b3) / len(rtf_b3), 4),
            "rtf_min": min(rtf_b3),
            "detalles": resultados_beam3,
        },
        "telemetria_muestreo": resumen_telemetria,
        "archivos_guardados": {
            "crudo_csv": str(archivo_csv),
            "resumen_json": str(archivo_json),
        },
    }

    with open(archivo_json, "w", encoding="utf-8") as f:
        json.dump(resultado_final, f, indent=2, ensure_ascii=False)

    print(f"\nResumen de Prueba C:")
    print(f"  VRAM Consumida STT  : {vram_stt_carga} MiB (Pico telemetría: {resumen_telemetria['vram_pico_mib']} MiB)")
    print(f"  Latencia media (b=1): {resultado_final['resumen_beam_1']['latencia_promedio_s']*1000:.1f} ms | RTF medio: {resultado_final['resumen_beam_1']['rtf_promedio']:.4f}")
    print(f"  Latencia media (b=3): {resultado_final['resumen_beam_3']['latencia_promedio_s']*1000:.1f} ms | RTF medio: {resultado_final['resumen_beam_3']['rtf_promedio']:.4f}")
    print(f"  Guardado en         : {archivo_json}")
    return resultado_final


if __name__ == "__main__":
    ejecutar_prueba_stt_aislado()
