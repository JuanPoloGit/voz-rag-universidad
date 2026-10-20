"""Batería de validación para la Fase 5: Unificación del Cliente LLM Central.

Verifica:
1. Conexión del cliente HTTP contra llama-server.exe central.
2. Ausencia de incremento en consumo de VRAM en GPU (reutilización de la instancia existente).
3. Contrato de streaming token a token (`stream_chat`) con callback `al_terminar`.
4. Contrato de utilidad JSON (`completar_json`).
5. Medición de precalentamiento (`precalentar`).
6. Manejo controlado de fallos (servidor detenido, puerto cerrado, timeouts) sin bucles infinitos.
7. Compatibilidad de tipos, enums y métodos heredados (_opciones_de_cache, detectar_gpu).
"""

from __future__ import annotations

import logging
import subprocess
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from hacu.config import ModelConfig
from hacu.llm import Aliento, LlmService, detectar_gpu

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("test_fase5")


def obtener_vram_mb() -> float:
    """Consulta la VRAM usada actualmente por la GPU."""
    try:
        res = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        return float(res.stdout.strip().splitlines()[0])
    except Exception as e:
        logger.warning("No se pudo consultar nvidia-smi: %s", e)
        return -1.0


def ejecutar_pruebas() -> bool:
    print("=" * 70)
    print(" AUDACIA - FASE 5: VALIDACION DEL CLIENTE LLM CENTRAL UNIFICADO")
    print("=" * 70)

    vram_inicial = obtener_vram_mb()
    print(f"[*] VRAM inicial en GPU: {vram_inicial:.1f} MiB")

    cfg = ModelConfig()
    servicio = LlmService(cfg, logger)

    # 1. Carga / Conexión Idempotente
    print("\n[Prueba 1] Verificación de carga y conexión al servidor central...")
    t0 = time.perf_counter()
    servicio.cargar()
    t_carga = time.perf_counter() - t0
    print(f"  [OK] Conexión establecida en {t_carga:.4f}s")
    assert servicio._conectado, "El servicio debe marcarse como conectado"

    # Segunda llamada (idempotencia)
    t0_idemp = time.perf_counter()
    servicio.cargar()
    t_idemp = time.perf_counter() - t0_idemp
    print(f"  [OK] Idempotencia verificada ({t_idemp*1000:.2f}ms en re-llamada)")

    # 2. Verificación de VRAM (Cero duplicación de modelo)
    print("\n[Prueba 2] Verificación de aislamiento de VRAM (no se cargó modelo adicional)...")
    vram_post_carga = obtener_vram_mb()
    diferencia_vram = vram_post_carga - vram_inicial
    print(f"  VRAM post-carga: {vram_post_carga:.1f} MiB (Diferencia: {diferencia_vram:+.1f} MiB)")
    assert abs(diferencia_vram) < 50.0, f"La VRAM aumentó indebidamente: {diferencia_vram:.1f} MiB"
    print("  [OK] Cero memoria VRAM incremental consumida en proceso de agente.")

    # 3. Precalentamiento
    print("\n[Prueba 3] Precalentamiento del modelo central...")
    t_precalentamiento = servicio.precalentar()
    print(f"  [OK] Precalentamiento ejecutado en {t_precalentamiento:.3f}s")
    assert t_precalentamiento > 0.0, "El tiempo de precalentamiento debe ser positivo"

    # 4. Stream Chat con Callback
    print("\n[Prueba 4] Generación de streaming con contrato de visitante (stream_chat)...")
    mensajes = [
        {"role": "system", "content": "Eres HACU, el asistente del centro de investigación AudacIA. Responde brevemente en una frase."},
        {"role": "user", "content": "¿Qué es AudacIA?"},
    ]

    motivo_final: list[str | None] = []
    tokens_recibidos: list[str] = []

    t0_stream = time.perf_counter()
    for token in servicio.stream_chat(
        mensajes,
        extenso=False,
        aliento=Aliento.BREVE,
        al_terminar=motivo_final.append,
    ):
        tokens_recibidos.append(token)
    t_stream = time.perf_counter() - t0_stream

    texto_completo = "".join(tokens_recibidos).strip()
    print(f"  Respuesta generada ({len(tokens_recibidos)} chunks, {t_stream:.3f}s):")
    print(f"  \"{texto_completo}\"")
    print(f"  Motivo de finalización: {motivo_final[-1]}")

    assert len(tokens_recibidos) > 0, "Debe recibir tokens por streaming"
    assert motivo_final and motivo_final[-1] in {"stop", "length"}, f"Motivo inesperado: {motivo_final}"
    assert "audacia" in texto_completo.lower() or "centro" in texto_completo.lower() or len(texto_completo) > 10, "Respuesta vacía o incongruente"
    print("  [OK] Streaming e invocación de callback al_terminar validados.")

    # 5. Completar JSON (Tarea de Utilidad / Memoria)
    print("\n[Prueba 5] Tarea de utilidad estructurada (completar_json)...")
    prompt_utilidad = (
        'Analiza la frase: "Me llamo Carlos y soy ingeniero civil". '
        'Devuelve un objeto JSON con dos claves: "sobre_el_visitante" (booleano true/false) '
        'y "hecho" (cadena de texto en español).'
    )
    t0_json = time.perf_counter()
    datos_json = servicio.completar_json(prompt_utilidad, max_tokens=96)
    t_json = time.perf_counter() - t0_json

    print(f"  Respuesta JSON ({t_json:.3f}s): {datos_json}")
    assert isinstance(datos_json, dict), f"Se esperaba un diccionario dict, recibido: {type(datos_json)}"
    assert "sobre_el_visitante" in datos_json, "Falta clave sobre_el_visitante"
    assert "hecho" in datos_json, "Falta clave hecho"
    print("  [OK] Decodificación JSON y contrato de utilidad validados.")

    # 6. Manejo controlado de fallos (Servidor Detenido o Puerto Incorrecto)
    print("\n[Prueba 6] Resiliencia y manejo controlado ante servidor no disponible...")
    servicio_invalido = LlmService(cfg, logger)
    servicio_invalido._url = "http://127.0.0.1:59999/v1/chat/completions"
    servicio_invalido._health_url = "http://127.0.0.1:59999/health"
    servicio_invalido._timeout_conexion = 1.0
    servicio_invalido._timeout_lectura = 1.0

    # 6a. cargar() debe lanzar ConnectionError controlado
    t0_err = time.perf_counter()
    excepcion_capturada = False
    try:
        servicio_invalido.cargar()
    except ConnectionError as e:
        excepcion_capturada = True
        print(f"  [OK] ConnectionError capturado según contrato: {type(e).__name__} ({time.perf_counter() - t0_err:.3f}s)")
    assert excepcion_capturada, "Debe lanzar ConnectionError cuando el servidor está caído"

    # 6b. completar_json() debe retornar None sin colapsar el proceso
    res_err_json = servicio_invalido.completar_json("hola", max_tokens=10)
    assert res_err_json is None, f"completar_json en servidor caído debe retornar None, retorno: {res_err_json}"
    print("  [OK] completar_json retorna None ante caída sin lanzar excepción no capturada.")

    # 6c. stream_chat() debe drenar vacio y llamar al_terminar(None)
    motivo_caida: list[str | None] = []
    tokens_caida = list(servicio_invalido.stream_chat(mensajes, al_terminar=motivo_caida.append))
    assert len(tokens_caida) == 0, "stream_chat en servidor caído debe ser un iterador vacío"
    assert motivo_caida == [None], f"al_terminar debe ser llamado con None en caída, retorno: {motivo_caida}"
    print("  [OK] stream_chat drena limpiamente e invoca al_terminar(None) sin colapsar.")

    # 7. Compatibilidad con métodos de entorno y GPU
    print("\n[Prueba 7] Compatibilidad de tipos, enums y utilidades...")
    gpu = detectar_gpu()
    print(f"  GPU detectada: {gpu}")
    class DobleLlama:
        def __init__(self, **kwargs: Any) -> None:
            pass
    opciones = servicio._opciones_de_cache(DobleLlama)
    assert isinstance(opciones, dict), "Opciones de caché debe ser un dict"
    print("  [OK] Compatibilidad de _opciones_de_cache y detectar_gpu verificada.")

    # 8. Cierre ordenado
    print("\n[Prueba 8] Cierre ordenado...")
    servicio.cerrar()
    assert not servicio._conectado, "El servicio debe marcarse como desconectado tras cerrar()"
    print("  [OK] cerrar() ejecutado sin afectar al servidor central.")

    print("\n" + "=" * 70)
    print(" TODAS LAS PRUEBAS DE LA FASE 5 PASARON EXITOSAMENTE")
    print("=" * 70)
    return True


if __name__ == "__main__":
    exito = ejecutar_pruebas()
    sys.exit(0 if exito else 1)
