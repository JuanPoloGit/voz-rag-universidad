"""Arnés Reproducible de Carga y Concurrencia para Servidor Definitivo (AudacIA).

Diseñado para simular hasta 10 usuarios humanos simultáneos contra la instancia central
de llama-server.exe (o backend web de AudacIA), con perfiles representativos,
tiempos de reflexión humana (think-time) y medición exhaustiva de métricas:
- TTFT (Time to First Token)
- Latencia total, P50, P95, P99
- Throughput (tokens/s individuales y agregados)
- Solicitudes completadas, rechazadas (HTTP 429/503), expiradas y fallidas
- Cola de espera y tiempo de encolamiento
- Monitor de salud de hardware con disyuntor automático de parada de emergencia.

PRECAUCIÓN: En el equipo de desarrollo (RTX 2000 Ada, 8GB VRAM), ejecutar únicamente
con la bandera `--safe-dev` o `--dry-run`. La prueba completa de 10 usuarios sostenida
debe realizarse en el servidor definitivo (RTX 5070 Ti, 16GB VRAM, 32GB RAM).
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import random
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

RAIZ = Path(__file__).resolve().parent.parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from cerebro_audacia.benchmark_recursos.telemetria import (
    obtener_telemetria_gpu,
    obtener_telemetria_sistema,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("harness_10_usuarios")

PERSONAS_USUARIOS = [
    {"id": "usr_01_daniela", "rol": "estudiante_ingenieria", "interes": "vision_artificial"},
    {"id": "usr_02_carlos", "rol": "docente_investigador", "interes": "robotica_autonoma"},
    {"id": "usr_03_sofia", "rol": "visitante_colegio", "interes": "proyectos_innovacion"},
    {"id": "usr_04_alejandro", "rol": "almacenista_inventario", "interes": "sensores_iot"},
    {"id": "usr_05_mariana", "rol": "visitante_externo", "interes": "historia_audacia"},
    {"id": "usr_06_felipe", "rol": "estudiante_postgrado", "interes": "modelos_lenguaje"},
    {"id": "usr_07_laura", "rol": "coordinadora_academica", "interes": "talleres_formativos"},
    {"id": "usr_08_andres", "rol": "ingeniero_software", "interes": "arquitectura_distribuida"},
    {"id": "usr_09_valeria", "rol": "visitante_general", "interes": "saludo_y_recorrido"},
    {"id": "usr_10_guillermo", "rol": "auditor_institucional", "interes": "catalogo_general"},
]

BANCO_TURNOS = [
    # Breves (social / acuse)
    {"tipo": "BREVE", "prompt": "Hola, buenos días. ¿Qué es AudacIA?", "max_tokens": 64},
    {"tipo": "BREVE", "prompt": "¿Quién es el director o responsable de este centro?", "max_tokens": 64},
    {"tipo": "BREVE", "prompt": "Muchas gracias, ha sido muy clara la información.", "max_tokens": 48},
    # Normales (tarima / factual)
    {"tipo": "NORMAL", "prompt": "¿Cuáles son las principales áreas de investigación que desarrollan aquí?", "max_tokens": 180},
    {"tipo": "NORMAL", "prompt": "Explícame brevemente el proyecto de visión artificial y qué sensores utiliza.", "max_tokens": 220},
    {"tipo": "NORMAL", "prompt": "¿Qué equipos de robótica o hardware especializado tienen en los laboratorios?", "max_tokens": 200},
    # Extensos (desarrollo a fondo / catálogo)
    {"tipo": "EXTENSO", "prompt": "Desarrolla en detalle los seis proyectos emblemáticos del centro y sus aplicaciones industriales.", "max_tokens": 384},
    {"tipo": "EXTENSO", "prompt": "Explícame paso a paso cómo se transfiere el conocimiento de IA desde la universidad hacia las empresas.", "max_tokens": 384},
]


@dataclass
class MetricaTurno:
    usuario_id: str
    turno_tipo: str
    inicio_ts: float
    ttft_s: float
    latencia_total_s: float
    tokens_generados: int
    tokens_por_segundo: float
    codigo_http: int
    estado: str  # OK, RECHAZADO_SATURACION, TIMEOUT, ERROR_SOCKET
    error_detalle: str = ""


@dataclass
class MonitorHardware:
    vram_max_mb: float = 0.0
    vram_actual_mb: float = 0.0
    ram_uso_pct: float = 0.0
    ram_usada_gib: float = 0.0
    memoria_comprometida_mib: float = 0.0
    cpu_pct: float = 0.0
    gpu_temp_c: float = 0.0
    gpu_temp_max_c: float = 0.0
    abortar_por_seguridad: bool = False
    motivo_aborto: str = ""

def evaluar_validez_muestra_estadistica(n_muestras: int) -> dict[str, Any]:
    """Evalúa la validez estadística para percentiles de latencia en producción.
    - < 100 solicitudes: Insuficiente para certificar P95 o P99.
    - 100 - 999 solicitudes: Válido para P50 y exploratorio para P95; insuficiente para P99.
    - >= 1.000 solicitudes: Estadísticamente adecuado para P50, P95 y P99.
    """
    if n_muestras < 100:
        return {
            "p50_valido": n_muestras >= 20,
            "p95_valido": False,
            "p99_valido": False,
            "nivel": "INSUFICIENTE",
            "mensaje": f"Muestra insuficiente ({n_muestras} < 100 solicitudes). P95 y P99 carecen de validez estadística para producción.",
        }
    elif n_muestras < 1000:
        return {
            "p50_valido": True,
            "p95_valido": True,
            "p99_valido": False,
            "nivel": "EXPLORATORIO_P95",
            "mensaje": f"Muestra moderada ({n_muestras} solicitudes). P50 y P95 son exploratorios válidos; P99 carece de resolución suficiente (se requieren >= 1.000 solicitudes).",
        }
    else:
        return {
            "p50_valido": True,
            "p95_valido": True,
            "p99_valido": True,
            "nivel": "CERTIFICABLE",
            "mensaje": f"Muestra estadísticamente adecuada ({n_muestras} >= 1.000 solicitudes) para evaluar P50, P95 y P99.",
        }


class Arnestres10Usuarios:
    def __init__(
        self,
        endpoint_url: str,
        num_usuarios: int = 10,
        duracion_segundos: int = 180,
        modo_rafaga: bool = False,
        rampa_activa: bool = True,
        safe_dev: bool = False,
    ):
        self.endpoint_url = endpoint_url
        self.num_usuarios = min(len(PERSONAS_USUARIOS), max(1, num_usuarios))
        self.duracion_s = duracion_segundos
        self.modo_rafaga = modo_rafaga
        self.rampa_activa = rampa_activa
        self.safe_dev = safe_dev

        self.resultados: list[MetricaTurno] = []
        self._lock = threading.Lock()
        self.monitor = MonitorHardware()
        self.detener_evento = threading.Event()

    def _hilo_vigilancia_hardware(self):
        """Disyuntor de emergencia de hardware con muestreo de VRAM, RAM, CPU y temperatura."""
        while not self.detener_evento.is_set():
            try:
                gpu_m = obtener_telemetria_gpu()
                sys_m = obtener_telemetria_sistema()
            except Exception as e:
                logger.warning("Error leyendo sensores de telemetria: %s", e)
                time.sleep(1.0)
                continue

            vram = gpu_m.get("vram_usada_mib", 0.0)
            temp = gpu_m.get("gpu_temp_c", 0.0)
            ram_pct = sys_m.get("ram_carga_pct", 0.0)
            cpu = sys_m.get("cpu_sistema_pct", 0.0)

            with self._lock:
                self.monitor.vram_actual_mb = vram
                if vram > self.monitor.vram_max_mb:
                    self.monitor.vram_max_mb = vram
                if temp > self.monitor.gpu_temp_max_c:
                    self.monitor.gpu_temp_max_c = temp
                self.monitor.gpu_temp_c = temp
                self.monitor.ram_uso_pct = ram_pct
                self.monitor.ram_usada_gib = sys_m.get("ram_usada_gib", 0.0)
                self.monitor.memoria_comprometida_mib = sys_m.get("memoria_comprometida_mib", 0.0)
                self.monitor.cpu_pct = cpu

                # 1. Límites de seguridad de VRAM
                limite_vram = 7500.0 if self.safe_dev else 15200.0
                if vram > limite_vram:
                    self.monitor.abortar_por_seguridad = True
                    self.monitor.motivo_aborto = f"VRAM excedió umbral de seguridad: {vram:.1f} MB > {limite_vram:.1f} MB"
                    self.detener_evento.set()
                    logger.critical("[DISYUNTOR DE SEGURIDAD] %s", self.monitor.motivo_aborto)
                    break

                # 2. Límites de seguridad de temperatura GPU (>83°C)
                if temp > 83.0:
                    self.monitor.abortar_por_seguridad = True
                    self.monitor.motivo_aborto = f"Temperatura GPU excedió umbral térmico: {temp:.1f}°C > 83.0°C"
                    self.detener_evento.set()
                    logger.critical("[DISYUNTOR DE SEGURIDAD] %s", self.monitor.motivo_aborto)
                    break

                # 3. Límites de RAM del sistema (>92%)
                if ram_pct > 92.0:
                    self.monitor.abortar_por_seguridad = True
                    self.monitor.motivo_aborto = f"Uso de RAM física crítico: {ram_pct:.1f}% > 92.0%"
                    self.detener_evento.set()
                    logger.critical("[DISYUNTOR DE SEGURIDAD] %s", self.monitor.motivo_aborto)
                    break

            time.sleep(1.0)

    def _simular_usuario(self, persona: dict[str, str], delay_inicio: float):
        if delay_inicio > 0:
            time.sleep(delay_inicio)

        usr_id = persona["id"]
        interes = persona["interes"]
        historial_local: list[dict[str, str]] = [
            {"role": "system", "content": f"Eres HACU. Hablas con {usr_id}, interesado en {interes}."}
        ]

        limite_fin = time.monotonic() + self.duracion_s

        while time.monotonic() < limite_fin and not self.detener_evento.is_set():
            turno_def = random.choice(BANCO_TURNOS)
            prompt = turno_def["prompt"]
            historial_local.append({"role": "user", "content": prompt})

            payload = {
                "messages": historial_local[-4:],  # Contexto deslizante de 2 turnos previos
                "max_tokens": turno_def["max_tokens"],
                "temperature": 0.3,
                "stream": True,
            }

            req = urllib.request.Request(
                self.endpoint_url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json", "User-Agent": f"AudacIA-UserSim/{usr_id}"},
            )

            t_inicio = time.perf_counter()
            t_primer_token: float | None = None
            tokens_recibidos = 0
            codigo_http = 0
            estado = "OK"
            error_det = ""
            resp = None

            try:
                resp = urllib.request.urlopen(req, timeout=30.0)
                codigo_http = resp.status
                for linea_bytes in resp:
                    if self.detener_evento.is_set():
                        break
                    linea = linea_bytes.decode("utf-8", errors="replace").strip()
                    if not linea or not linea.startswith("data: "):
                        continue
                    datos_txt = linea[6:].strip()
                    if datos_txt == "[DONE]":
                        break
                    try:
                        chunk = json.loads(datos_txt)
                        contenido = chunk.get("choices", [{}])[0].get("delta", {}).get("content")
                        if contenido:
                            if t_primer_token is None:
                                t_primer_token = time.perf_counter()
                            tokens_recibidos += 1
                    except json.JSONDecodeError:
                        continue

            except urllib.error.HTTPError as e:
                codigo_http = e.code
                estado = "RECHAZADO_SATURACION" if e.code in (429, 503) else f"HTTP_{e.code}"
                error_det = str(e)
            except TimeoutError as e:
                codigo_http = 408
                estado = "TIMEOUT"
                error_det = str(e)
            except Exception as e:
                codigo_http = 0
                estado = "ERROR_SOCKET"
                error_det = type(e).__name__
            finally:
                t_total = time.perf_counter() - t_inicio
                if resp:
                    try:
                        resp.close()
                    except Exception:
                        pass

            ttft = (t_primer_token - t_inicio) if t_primer_token else t_total
            tok_s = (tokens_recibidos / t_total) if t_total > 0 and tokens_recibidos > 0 else 0.0

            metrica = MetricaTurno(
                usuario_id=usr_id,
                turno_tipo=turno_def["tipo"],
                inicio_ts=t_inicio,
                ttft_s=ttft,
                latencia_total_s=t_total,
                tokens_generados=tokens_recibidos,
                tokens_por_segundo=tok_s,
                codigo_http=codigo_http,
                estado=estado,
                error_detalle=error_det,
            )

            with self._lock:
                self.resultados.append(metrica)

            # Think-time entre turnos humanos: ráfaga (0.5-1.5s) vs normal (3.0-7.0s)
            if not self.modo_rafaga:
                time.sleep(random.uniform(3.0, 7.0))
            else:
                time.sleep(random.uniform(0.5, 1.5))

    def ejecutar(self) -> dict[str, Any]:
        logger.info("==================================================================")
        logger.info(" INICIANDO ARNES DE CARGA Y CONCURRENCIA AUDACIA")
        logger.info(" Endpoint: %s", self.endpoint_url)
        logger.info(" Usuarios virtuales: %d", self.num_usuarios)
        logger.info(" Duracion prevista: %d segundos", self.duracion_s)
        logger.info(" Modo: %s | Rampa activa: %s | Modo Seguro Dev: %s",
                    "RAFAGA" if self.modo_rafaga else "SOSTENIDO", self.rampa_activa, self.safe_dev)
        logger.info("==================================================================")

        hilo_vig = threading.Thread(target=self._hilo_vigilancia_hardware, daemon=True)
        hilo_vig.start()

        hilos_usuarios = []
        for i in range(self.num_usuarios):
            delay = (i * 2.0) if self.rampa_activa else 0.0
            persona = PERSONAS_USUARIOS[i]
            t = threading.Thread(target=self._simular_usuario, args=(persona, delay), name=persona["id"])
            hilos_usuarios.append(t)
            t.start()

        for t in hilos_usuarios:
            t.join()

        self.detener_evento.set()
        hilo_vig.join(timeout=2.0)

        return self._generar_reporte()

    def _generar_reporte(self) -> dict[str, Any]:
        with self._lock:
            metr = list(self.resultados)

        if not metr:
            return {"error": "No se registraron metricas"}

        totales = len(metr)
        exitosas = [m for m in metr if m.estado == "OK"]
        saturadas = [m for m in metr if m.estado == "RECHAZADO_SATURACION"]
        timeouts = [m for m in metr if m.estado == "TIMEOUT"]
        errores = [m for m in metr if m.estado not in ("OK", "RECHAZADO_SATURACION", "TIMEOUT")]

        latencias = sorted([m.latencia_total_s for m in exitosas])
        ttfts = sorted([m.ttft_s for m in exitosas])

        def percentil(datos: list[float], p: float) -> float:
            if not datos:
                return 0.0
            k = (len(datos) - 1) * (p / 100.0)
            f = math.floor(k)
            c = math.ceil(k)
            if f == c:
                return datos[int(k)]
            return datos[f] * (c - k) + datos[c] * (k - f)

        p50 = percentil(latencias, 50)
        p90 = percentil(latencias, 90)
        p95 = percentil(latencias, 95)
        p99 = percentil(latencias, 99)

        ttft_p50 = percentil(ttfts, 50)
        ttft_p95 = percentil(ttfts, 95)

        total_tokens = sum(m.tokens_generados for m in exitosas)
        tiempo_total_s = max(m.inicio_ts + m.latencia_total_s for m in metr) - min(m.inicio_ts for m in metr)
        throughput_agregado = (total_tokens / tiempo_total_s) if tiempo_total_s > 0 else 0.0

        tok_s_promedio = (
            sum(m.tokens_por_segundo for m in exitosas) / len(exitosas)
            if exitosas
            else 0.0
        )

        resumen = {
            "timestamp": datetime.now().isoformat(),
            "configuracion": {
                "endpoint": self.endpoint_url,
                "usuarios_simulados": self.num_usuarios,
                "duracion_segundos": self.duracion_s,
                "modo": "rafaga" if self.modo_rafaga else "sostenido",
                "safe_dev": self.safe_dev,
            },
            "volumen": {
                "solicitudes_totales": totales,
                "solicitudes_completadas_ok": len(exitosas),
                "solicitudes_rechazadas_saturacion": len(saturadas),
                "solicitudes_timeout": len(timeouts),
                "solicitudes_error": len(errores),
                "tasa_exito_pct": round(len(exitosas) / totales * 100.0, 2) if totales else 0.0,
            },
            "latencia_segundos": {
                "ttft_p50": round(ttft_p50, 4),
                "ttft_p95": round(ttft_p95, 4),
                "latencia_p50": round(p50, 4),
                "latencia_p90": round(p90, 4),
                "latencia_p95": round(p95, 4),
                "latencia_p99": round(p99, 4),
            },
            "rendimiento_tokens": {
                "total_tokens_generados": total_tokens,
                "tokens_por_segundo_promedio_individual": round(tok_s_promedio, 2),
                "throughput_agregado_tokens_por_segundo": round(throughput_agregado, 2),
            },
            "hardware": {
                "vram_max_observada_mib": round(self.monitor.vram_max_mb, 1),
                "gpu_temp_max_c": round(self.monitor.gpu_temp_max_c, 1),
                "ram_uso_max_pct": round(self.monitor.ram_uso_pct, 1),
                "ram_usada_gib": round(self.monitor.ram_usada_gib, 2),
                "memoria_comprometida_mib": round(self.monitor.memoria_comprometida_mib, 1),
                "cpu_pct": round(self.monitor.cpu_pct, 1),
                "aborto_de_seguridad": self.monitor.abortar_por_seguridad,
                "motivo_aborto": self.monitor.motivo_aborto,
            },
            "estadistica": {
                "muestras_exitosas": len(exitosas),
                "validez": evaluar_validez_muestra_estadistica(len(exitosas)),
            },
        }

        # Guardar archivo JSON en resultados
        dir_res = RAIZ / "cerebro_audacia" / "benchmark_recursos" / "resultados"
        dir_res.mkdir(parents=True, exist_ok=True)
        nombre_base = f"estres_{self.num_usuarios}u_{resumen['configuracion']['modo']}"
        archivo_json = dir_res / f"{nombre_base}.json"
        with open(archivo_json, "w", encoding="utf-8") as f:
            json.dump(resumen, f, indent=2, ensure_ascii=False)

        logger.info("Resultados guardados en: %s", archivo_json)
        return resumen


def main():
    parser = argparse.ArgumentParser(description="Arnés de estrés y concurrencia para AudacIA")
    parser.add_argument("--url", default="http://127.0.0.1:8080/v1/chat/completions", help="Endpoint LLM")
    parser.add_argument("--usuarios", type=int, default=10, help="Número de usuarios virtuales (1 a 10)")
    parser.add_argument("--duracion", type=int, default=60, help="Duración en segundos")
    parser.add_argument("--rafaga", action="store_true", help="Modo ráfaga con think-time reducido")
    parser.add_argument("--sin-rampa", action="store_true", help="Desactivar rampa de inicio gradual")
    parser.add_argument("--safe-dev", action="store_true", help="Modo seguro para entorno de desarrollo")
    parser.add_argument("--dry-run", action="store_true", help="Ejecución de validación rápida de 5s")

    args = parser.parse_args()

    duracion = 4 if args.dry_run else args.duracion
    usuarios = 2 if args.dry_run else (min(2, args.usuarios) if args.safe_dev else args.usuarios)
    rampa = False if args.dry_run else (not args.sin_rampa)

    arnes = Arnestres10Usuarios(
        endpoint_url=args.url,
        num_usuarios=usuarios,
        duracion_segundos=duracion,
        modo_rafaga=args.rafaga,
        rampa_activa=rampa,
        safe_dev=args.safe_dev or args.dry_run,
    )

    resumen = arnes.ejecutar()
    print("\n" + "=" * 70)
    print(" RESUMEN DE EJECUCION DE ARNES")
    print("=" * 70)
    print(json.dumps(resumen, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
