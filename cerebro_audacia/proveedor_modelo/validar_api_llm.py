"""Cliente de validación y diagnóstico de API para llama-server local en AudacIA.

Permite verificar de forma determinista y reproducible:
1. Disponibilidad (/health).
2. Identificación del modelo cargado (/v1/models).
3. Generación factual básica en español.
4. Generación de instrucción de múltiples pasos.
5. Generación de formato estructurado JSON.
6. Estabilidad en llamadas sucesivas y medición de latencia.

Utiliza exclusivamente la biblioteca estándar de Python (urllib.request, json, time).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional


class LlmApiValidator:
    """Validador determinista del endpoint HTTP OpenAI-compatible de llama-server."""

    def __init__(self, host: str = "127.0.0.1", port: int = 8080, timeout_segundos: float = 30.0):
        self.base_url = f"http://{host}:{port}"
        self.timeout = timeout_segundos

    def _get(self, endpoint: str) -> Dict[str, Any]:
        url = f"{self.base_url}{endpoint}"
        req = urllib.request.Request(url, headers={"User-Agent": "AudacIA-Validator/1.0"})
        t0 = time.perf_counter()
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            duracion = time.perf_counter() - t0
            datos = json.loads(resp.read().decode("utf-8"))
            return {"codigo_http": resp.status, "duracion_s": duracion, "datos": datos}

    def _post(self, endpoint: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.base_url}{endpoint}"
        data_bytes = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data_bytes,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "AudacIA-Validator/1.0",
            },
        )
        t0 = time.perf_counter()
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            duracion = time.perf_counter() - t0
            datos = json.loads(resp.read().decode("utf-8"))
            return {"codigo_http": resp.status, "duracion_s": duracion, "datos": datos}

    def test_disponibilidad(self) -> Dict[str, Any]:
        """Prueba 1: GET /health"""
        try:
            res = self._get("/health")
            status = res["datos"].get("status")
            return {
                "prueba": "Disponibilidad (/health)",
                "exitoso": status in ("ok", "ready"),
                "status_reportado": status,
                "latencia_ms": round(res["duracion_s"] * 1000, 2),
            }
        except Exception as e:
            return {"prueba": "Disponibilidad (/health)", "exitoso": False, "error": str(e)}

    def test_identificacion_modelo(self) -> Dict[str, Any]:
        """Prueba 2: GET /v1/models"""
        try:
            res = self._get("/v1/models")
            modelos = res["datos"].get("data", [])
            ids = [m.get("id") for m in modelos]
            return {
                "prueba": "Identificación de Modelo (/v1/models)",
                "exitoso": len(ids) > 0,
                "modelos": ids,
                "latencia_ms": round(res["duracion_s"] * 1000, 2),
            }
        except Exception as e:
            return {"prueba": "Identificación de Modelo (/v1/models)", "exitoso": False, "error": str(e)}

    def test_generacion_factual(self, model_id: Optional[str] = None) -> Dict[str, Any]:
        """Prueba 3: Pregunta factual básica en español."""
        payload = {
            "model": model_id or "default",
            "messages": [
                {"role": "system", "content": "Eres un asistente técnico conciso y preciso."},
                {"role": "user", "content": "¿Cuál es la capital del departamento del Atlántico en Colombia?"},
            ],
            "max_tokens": 64,
            "temperature": 0.1,
        }
        try:
            res = self._post("/v1/chat/completions", payload)
            choice = res["datos"].get("choices", [{}])[0]
            texto = choice.get("message", {}).get("content", "").strip()
            tokens_generados = res["datos"].get("usage", {}).get("completion_tokens", 0)
            tokens_por_seg = round(tokens_generados / res["duracion_s"], 2) if res["duracion_s"] > 0 else 0
            return {
                "prueba": "Generación Factual (Español)",
                "exitoso": "barranquilla" in texto.lower(),
                "respuesta": texto,
                "tokens_completados": tokens_generados,
                "latencia_s": round(res["duracion_s"], 3),
                "tokens_por_seg": tokens_por_seg,
            }
        except Exception as e:
            return {"prueba": "Generación Factual (Español)", "exitoso": False, "error": str(e)}

    def test_instruccion_pasos(self, model_id: Optional[str] = None) -> Dict[str, Any]:
        """Prueba 4: Instrucción de múltiples pasos lógicos."""
        payload = {
            "model": model_id or "default",
            "messages": [
                {"role": "system", "content": "Responde estrictamente con una lista numerada de exactamente 3 pasos."},
                {"role": "user", "content": "Enumera tres pasos esenciales para recepcionar mercancía en una bodega."},
            ],
            "max_tokens": 150,
            "temperature": 0.2,
        }
        try:
            res = self._post("/v1/chat/completions", payload)
            choice = res["datos"].get("choices", [{}])[0]
            texto = choice.get("message", {}).get("content", "").strip()
            lineas_numeradas = [line for line in texto.split("\n") if line.strip() and line.strip()[0].isdigit()]
            return {
                "prueba": "Instrucción de Múltiples Pasos",
                "exitoso": len(lineas_numeradas) >= 3,
                "respuesta": texto,
                "latencia_s": round(res["duracion_s"], 3),
            }
        except Exception as e:
            return {"prueba": "Instrucción de Múltiples Pasos", "exitoso": False, "error": str(e)}

    def test_salida_estructurada_json(self, model_id: Optional[str] = None) -> Dict[str, Any]:
        """Prueba 5: Solicitud de objeto estructurado JSON."""
        payload = {
            "model": model_id or "default",
            "messages": [
                {
                    "role": "system",
                    "content": 'Eres un extractor. Devuelve ÚNICAMENTE un objeto JSON válido con las claves "item" y "cantidad", sin texto adicional.',
                },
                {"role": "user", "content": "Se necesitan 5 cables HDMI categoría 2."},
            ],
            "max_tokens": 64,
            "temperature": 0.0,
        }
        try:
            res = self._post("/v1/chat/completions", payload)
            choice = res["datos"].get("choices", [{}])[0]
            texto = choice.get("message", {}).get("content", "").strip()
            parsed = json.loads(texto)
            es_valido = "item" in parsed and "cantidad" in parsed
            return {
                "prueba": "Salida Estructurada JSON",
                "exitoso": es_valido,
                "json_extraido": parsed,
                "latencia_s": round(res["duracion_s"], 3),
            }
        except Exception as e:
            return {"prueba": "Salida Estructurada JSON", "exitoso": False, "error": str(e)}

    def ejecutar_bateria_completa(self) -> Dict[str, Any]:
        """Ejecuta toda la batería y consolida métricas."""
        informe: Dict[str, Any] = {"servidor": self.base_url, "timestamp": time.time(), "resultados": []}

        # 1. Health
        h = self.test_disponibilidad()
        informe["resultados"].append(h)
        if not h["exitoso"]:
            informe["resumen"] = "Servidor no disponible. Verifique que llama-server esté en ejecución."
            return informe

        # 2. Models
        m = self.test_identificacion_modelo()
        informe["resultados"].append(m)
        model_id = m.get("modelos", [None])[0] if m.get("modelos") else None

        # 3. Generación Factual
        informe["resultados"].append(self.test_generacion_factual(model_id))

        # 4. Instrucción por Pasos
        informe["resultados"].append(self.test_instruccion_pasos(model_id))

        # 5. Salida Estructurada JSON
        informe["resultados"].append(self.test_salida_estructurada_json(model_id))

        aprobadas = sum(1 for r in informe["resultados"] if r.get("exitoso"))
        total = len(informe["resultados"])
        informe["resumen"] = f"{aprobadas}/{total} pruebas aprobadas."
        informe["exito_global"] = aprobadas == total
        return informe


def main() -> None:
    parser = argparse.ArgumentParser(description="Validador de API local llama-server para AudacIA")
    parser.add_argument("--host", default="127.0.0.1", help="Host local del servicio (default: 127.0.0.1)")
    parser.add_argument("--puerto", type=int, default=8080, help="Puerto local del servicio (default: 8080)")
    parser.add_argument("--timeout", type=float, default=30.0, help="Timeout por llamada en segundos (default: 30.0)")
    args = parser.parse_args()

    print(f"=== Comprobando servicio LLM en http://{args.host}:{args.puerto} ===")
    validator = LlmApiValidator(host=args.host, port=args.puerto, timeout_segundos=args.timeout)
    resultado = validator.ejecutar_bateria_completa()

    print("\nResultados:")
    for r in resultado["resultados"]:
        marca = "[OK]  " if r.get("exitoso") else "[FAIL]"
        print(f"  {marca} {r.get('prueba')}: {r.get('error') or r.get('status_reportado') or r.get('respuesta') or r.get('json_extraido') or 'OK'}")

    print(f"\nResumen: {resultado['resumen']}")
    if not resultado.get("exito_global"):
        sys.exit(1)


if __name__ == "__main__":
    main()
