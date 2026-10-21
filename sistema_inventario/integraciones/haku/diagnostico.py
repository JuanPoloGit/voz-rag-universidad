"""Módulo de diagnóstico en memoria para Haku Inventario — integraciones/haku/diagnostico.py.

Registra métricas operativas en RAM (sin persistencia en base de datos)
para observar el comportamiento del cerebro conversacional en tiempo real.

Métricas recolectadas:
- Distribución de intenciones detectadas (por tipo)
- Tasa de fallback (SOLICITUD_NO_COMPRENDIDA / NO_INTENCION_INVENTARIO)
- Latencia promedio de interpretación lingüística
- Contadores de propuestas generadas vs confirmadas vs canceladas vs expiradas
- Contadores de traslados ejecutados
- Aciertos / fallos del contexto conversacional (delegado a HakuOperationalContext)

Uso:
    from integraciones.haku.diagnostico import HakuDiagnostico

    HakuDiagnostico.registrar_intencion("CONSULTAR_STOCK", latencia_ms=12.5)
    HakuDiagnostico.registrar_propuesta("generada")
    metricas = HakuDiagnostico.obtener_metricas()
    HakuDiagnostico.reset()
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from typing import Any, Dict


class HakuDiagnostico:
    """Recolector de métricas en memoria (thread-safe) para Haku Inventario."""

    _lock = threading.Lock()

    # Contadores de intenciones detectadas
    _intenciones: Dict[str, int] = defaultdict(int)

    # Contadores de propuestas
    _propuestas_generadas: int = 0
    _propuestas_confirmadas: int = 0
    _propuestas_canceladas: int = 0
    _propuestas_expiradas: int = 0

    # Contadores de traslados
    _traslados_ejecutados: int = 0

    # Latencia de interpretación (acumulador y conteo para promedio)
    _latencia_total_ms: float = 0.0
    _latencia_conteo: int = 0
    _latencia_max_ms: float = 0.0

    # Timestamp de inicio de recolección
    _inicio_recoleccion: float = time.time()

    @classmethod
    def registrar_intencion(cls, tipo_intencion: str, latencia_ms: float = 0.0) -> None:
        """Registra una intención detectada y opcionalmente la latencia de interpretación."""
        with cls._lock:
            cls._intenciones[tipo_intencion] += 1
            if latencia_ms > 0:
                cls._latencia_total_ms += latencia_ms
                cls._latencia_conteo += 1
                if latencia_ms > cls._latencia_max_ms:
                    cls._latencia_max_ms = latencia_ms

    @classmethod
    def registrar_propuesta(cls, estado: str) -> None:
        """Registra un evento de propuesta: 'generada', 'confirmada', 'cancelada', 'expirada'."""
        with cls._lock:
            if estado == "generada":
                cls._propuestas_generadas += 1
            elif estado == "confirmada":
                cls._propuestas_confirmadas += 1
            elif estado == "cancelada":
                cls._propuestas_canceladas += 1
            elif estado == "expirada":
                cls._propuestas_expiradas += 1

    @classmethod
    def registrar_traslado(cls) -> None:
        """Registra un traslado entre almacenes ejecutado exitosamente."""
        with cls._lock:
            cls._traslados_ejecutados += 1

    @classmethod
    def obtener_metricas(cls) -> Dict[str, Any]:
        """Retorna un snapshot completo de las métricas recolectadas."""
        from .contexto import HakuOperationalContext

        with cls._lock:
            total_intenciones = sum(cls._intenciones.values())
            fallback_count = (
                cls._intenciones.get("SOLICITUD_NO_COMPRENDIDA", 0)
                + cls._intenciones.get("NO_INTENCION_INVENTARIO", 0)
            )
            tasa_fallback = (fallback_count / total_intenciones * 100) if total_intenciones > 0 else 0.0
            latencia_promedio = (cls._latencia_total_ms / cls._latencia_conteo) if cls._latencia_conteo > 0 else 0.0

            ctx_metricas = HakuOperationalContext.obtener_metricas()

            return {
                "recoleccion_desde": cls._inicio_recoleccion,
                "total_intenciones": total_intenciones,
                "distribucion_intenciones": dict(cls._intenciones),
                "tasa_fallback_pct": round(tasa_fallback, 2),
                "fallback_count": fallback_count,
                "latencia_promedio_ms": round(latencia_promedio, 2),
                "latencia_max_ms": round(cls._latencia_max_ms, 2),
                "latencia_muestras": cls._latencia_conteo,
                "propuestas": {
                    "generadas": cls._propuestas_generadas,
                    "confirmadas": cls._propuestas_confirmadas,
                    "canceladas": cls._propuestas_canceladas,
                    "expiradas": cls._propuestas_expiradas,
                },
                "traslados_ejecutados": cls._traslados_ejecutados,
                "contexto": ctx_metricas,
            }

    @classmethod
    def reset(cls) -> None:
        """Reinicia todos los contadores de diagnóstico."""
        with cls._lock:
            cls._intenciones = defaultdict(int)
            cls._propuestas_generadas = 0
            cls._propuestas_confirmadas = 0
            cls._propuestas_canceladas = 0
            cls._propuestas_expiradas = 0
            cls._traslados_ejecutados = 0
            cls._latencia_total_ms = 0.0
            cls._latencia_conteo = 0
            cls._latencia_max_ms = 0.0
            cls._inicio_recoleccion = time.time()

        from .contexto import HakuOperationalContext
        HakuOperationalContext.reset_metricas()
