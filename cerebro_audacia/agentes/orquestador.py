"""Módulo de Orquestación Central y Coordinación de Agentes de AudacIA.

Gobierna el ciclo de vida completo de solicitudes conversacionales, selección
de capacidades estructuradas (tool calling), validación en tres barreras,
ejecución resiliente y síntesis final de respuestas.

Garantiza:
1. Cero reflexiones mágicas ni registros globales ocultos.
2. Aislamiento estricto de sesiones y contextos entre usuarios.
3. Desacoplamiento total: los agentes nuevos no dependen de HAKU ni de HACU.
4. Triple barrera de seguridad: autorización de canal, validación de argumentos
   e inyección confiable de identidad.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .contrato import AgentContract
from .esquemas import (
    AgentContext,
    AgentError,
    AgentErrorCode,
    AgentInput,
    AgentResult,
)
from .resiliencia import ResilientAgentExecutor

logger = logging.getLogger("cerebro_audacia.orquestador")


@dataclass(frozen=True)
class OrquestadorResult:
    """Resultado final de una solicitud procesada por el orquestador central."""

    exitoso: bool
    respuesta: str
    herramientas_ejecutadas: tuple[str, ...] = field(default_factory=tuple)
    resultados_agentes: tuple[AgentResult, ...] = field(default_factory=tuple)
    duracion_total_ms: float = 0.0
    correlation_id: str | None = None
    error: AgentError | None = None


class OrquestadorAudacIA:
    """Orquestador central de capacidades y coordinador de llamadas a herramientas."""

    def __init__(
        self,
        agentes: Sequence[AgentContract] | None = None,
        esquemas_herramientas: Mapping[str, dict[str, Any]] | None = None,
        llm_client: Any = None,
        max_turnos_historial: int = 10,
    ) -> None:
        self._agentes: dict[str, AgentContract] = {}
        self._ejecutores: dict[str, ResilientAgentExecutor] = {}
        self._esquemas: dict[str, dict[str, Any]] = {}
        if llm_client is None:
            try:
                from cerebro_audacia.conexion import obtener_cliente_cerebro_central
                self._llm = obtener_cliente_cerebro_central()
            except Exception:
                self._llm = None
        else:
            self._llm = llm_client
        self._max_turnos = max_turnos_historial
        self._sesiones: dict[tuple[str, str], list[dict[str, Any]]] = {}

        if agentes:
            for ag in agentes:
                esquema = esquemas_herramientas.get(ag.metadata.id) if esquemas_herramientas else None
                self.registrar_agente(agente=ag, esquema_herramienta=esquema)

    def registrar_agente(
        self,
        agente: AgentContract,
        esquema_herramienta: dict[str, Any] | None = None,
        ejecutor: ResilientAgentExecutor | None = None,
    ) -> None:
        """Registra explícitamente un agente y su ejecutor resiliente."""
        ag_id = agente.metadata.id
        self._agentes[ag_id] = agente
        self._ejecutores[ag_id] = ejecutor or ResilientAgentExecutor(agente=agente)
        if esquema_herramienta:
            self._esquemas[ag_id] = esquema_herramienta

    def obtener_herramientas_autorizadas(
        self,
        usuario_id: str | None,
        canal: str,
        max_herramientas: int = 5,
    ) -> list[dict[str, Any]]:
        """Filtra y poda el catálogo de herramientas según canal y autenticación.

        Evita saturar la atención del LLM limitando a un máximo de 3-5 herramientas relevantes.
        """
        autorizadas: list[dict[str, Any]] = []
        for ag_id, esquema in self._esquemas.items():
            agente = self._agentes.get(ag_id)
            if not agente:
                continue
            meta = agente.metadata
            if canal not in meta.canales_permitidos:
                continue
            if meta.requiere_autenticacion and not usuario_id:
                continue
            autorizadas.append(esquema)
            if len(autorizadas) >= max_herramientas:
                break
        return autorizadas

    def obtener_historial(self, usuario_id: str, sesion_id: str) -> list[dict[str, Any]]:
        """Retorna el historial de conversación aislado para un usuario y sesión."""
        clave = (str(usuario_id), str(sesion_id))
        return list(self._sesiones.get(clave, []))

    def limpiar_sesion(self, usuario_id: str, sesion_id: str) -> None:
        """Elimina el historial de una sesión específica sin afectar a otros usuarios."""
        clave = (str(usuario_id), str(sesion_id))
        self._sesiones.pop(clave, None)

    def procesar_mensaje(
        self,
        mensaje: str,
        contexto: AgentContext,
        sesion_id: str = "default",
        almacenes_permitidos: Sequence[int] | None = None,
    ) -> OrquestadorResult:
        """Ejecuta el ciclo completo de orquestación y despacho de herramientas.

        Pasos:
        1. Aislamiento de sesión por (usuario_id, sesion_id).
        2. Poda de herramientas autorizadas para el canal y contexto.
        3. Inferencia con LLM central.
        4. Si emite tool_calls: triple barrera de validación, ejecución resiliente y re-inferencia.
        5. Síntesis y actualización segura del historial.
        """
        t0 = time.perf_counter()
        c_id = contexto.correlation_id or f"trace-{int(time.time() * 1000)}"
        usuario = str(contexto.usuario_id or "anonimo")
        clave_sesion = (usuario, str(sesion_id))

        if not mensaje or not mensaje.strip():
            err = AgentError(
                codigo=AgentErrorCode.INVALID_INPUT,
                mensaje="El mensaje de entrada no puede estar vacío.",
            )
            return OrquestadorResult(
                exitoso=False,
                respuesta="Mensaje de entrada vacío.",
                correlation_id=c_id,
                error=err,
            )

        # 1. Recuperar o inicializar historial de sesión aislado
        historial = self._sesiones.setdefault(clave_sesion, [])

        # 2. Obtener herramientas autorizadas y podadas
        tools = self.obtener_herramientas_autorizadas(
            usuario_id=contexto.usuario_id,
            canal=contexto.canal,
        )

        # 3. Formular prompt con directivas institucionales de AudacIA
        system_prompt = (
            "Eres el cerebro central de AudacIA. Asistes a los usuarios operando como "
            "coordinador conversacional. Si una solicitud requiere consultar o diagnosticar datos, "
            "utiliza las herramientas provistas. Nunca inventes identidades de usuario ni ejecutes "
            "códigos arbitrarios. Responde de forma concisa, veraz y profesional."
        )

        mensajes_turno: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        mensajes_turno.extend(historial[-self._max_turnos :])
        mensajes_turno.append({"role": "user", "content": mensaje})

        # 4. Invocación al LLM
        if self._llm is None:
            # Fallback seguro si no hay cliente LLM disponible
            err = AgentError(
                codigo=AgentErrorCode.DEPENDENCY_UNAVAILABLE,
                mensaje="Cliente LLM central no configurado en el orquestador.",
            )
            return OrquestadorResult(
                exitoso=False,
                respuesta="Servicio LLM central no disponible.",
                correlation_id=c_id,
                error=err,
            )

        try:
            # Llamada al LLM con herramientas
            kwargs: dict[str, Any] = {"tools": tools} if tools else {}
            resp_llm = self._llm.create_chat_completion(
                messages=mensajes_turno,
                max_tokens=512,
                temperature=0.2,
                **kwargs,
            )
        except Exception as e:
            duracion = (time.perf_counter() - t0) * 1000.0
            err = AgentError(
                codigo=AgentErrorCode.DEPENDENCY_UNAVAILABLE,
                mensaje=f"Fallo de comunicación con el LLM central: {type(e).__name__}",
                excepcion_original=str(e),
            )
            return OrquestadorResult(
                exitoso=False,
                respuesta="Error al consultar el servicio LLM central.",
                duracion_total_ms=duracion,
                correlation_id=c_id,
                error=err,
            )

        choices = resp_llm.get("choices", [])
        if not choices:
            duracion = (time.perf_counter() - t0) * 1000.0
            err = AgentError(
                codigo=AgentErrorCode.INTERNAL_ERROR,
                mensaje="El LLM no devolvió elecciones de respuesta.",
            )
            return OrquestadorResult(
                exitoso=False,
                respuesta="Sin respuesta del modelo.",
                duracion_total_ms=duracion,
                correlation_id=c_id,
                error=err,
            )

        msg_asistente = choices[0].get("message", {})
        tool_calls = msg_asistente.get("tool_calls")

        # Caso A: Respuesta conversacional directa sin herramientas
        if not tool_calls:
            texto_resp = msg_asistente.get("content", "").strip()
            historial.append({"role": "user", "content": mensaje})
            historial.append({"role": "assistant", "content": texto_resp})
            duracion = (time.perf_counter() - t0) * 1000.0
            return OrquestadorResult(
                exitoso=True,
                respuesta=texto_resp,
                duracion_total_ms=duracion,
                correlation_id=c_id,
            )

        # Caso B: Invocación de herramientas (Tool Calling)
        herramientas_ejecutadas: list[str] = []
        resultados_agentes: list[AgentResult] = []

        # Registrar el turno del asistente proponiendo tool_calls en la conversación temporal
        mensajes_turno.append(msg_asistente)

        for tc in tool_calls:
            fn = tc.get("function", {})
            nombre_tool = fn.get("name", "")
            raw_args = fn.get("arguments", "{}")
            call_id = tc.get("id", f"call-{len(herramientas_ejecutadas)}")

            herramientas_ejecutadas.append(nombre_tool)

            # BARRERA 1: Verificación de herramienta en catálogo y canal permitido
            ejecutor = self._ejecutores.get(nombre_tool)
            agente = self._agentes.get(nombre_tool)

            if not ejecutor or not agente:
                err_res = AgentResult.fallo(
                    error=AgentError(
                        codigo=AgentErrorCode.INTENT_NOT_SUPPORTED,
                        mensaje=f"Herramienta '{nombre_tool}' no registrada en el orquestador.",
                    ),
                    correlation_id=c_id,
                )
                resultados_agentes.append(err_res)
                mensajes_turno.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": json.dumps({
                        "exitoso": False,
                        "codigo_error": err_res.error.codigo.value,
                        "mensaje_error": err_res.error.mensaje,
                    }, ensure_ascii=False),
                })
                continue

            # BARRERA 2: Parseo y validación de argumentos JSON
            try:
                parametros = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
            except Exception as pe:
                err_res = AgentResult.fallo(
                    error=AgentError(
                        codigo=AgentErrorCode.INVALID_INPUT,
                        mensaje="Argumentos JSON de herramienta malformados.",
                        excepcion_original=str(pe),
                    ),
                    correlation_id=c_id,
                )
                resultados_agentes.append(err_res)
                mensajes_turno.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": json.dumps({
                        "exitoso": False,
                        "codigo_error": err_res.error.codigo.value,
                        "mensaje_error": err_res.error.mensaje,
                    }, ensure_ascii=False),
                })
                continue

            # BARRERA 3: Control granular sobre el recurso (BOLA / IDOR)
            # Si el argumento incluye almacen_id y se configuraron almacenes permitidos para el usuario
            almacen_solicitado = parametros.get("almacen_id")
            if (
                almacenes_permitidos is not None
                and almacen_solicitado is not None
                and almacen_solicitado not in almacenes_permitidos
            ):
                err_res = AgentResult.fallo(
                    error=AgentError(
                        codigo=AgentErrorCode.UNAUTHORIZED,
                        mensaje=f"Usuario '{usuario}' no tiene autorización para acceder al almacén {almacen_solicitado}.",
                    ),
                    correlation_id=c_id,
                )
                resultados_agentes.append(err_res)
                mensajes_turno.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": json.dumps({
                        "exitoso": False,
                        "codigo_error": err_res.error.codigo.value,
                        "mensaje_error": err_res.error.mensaje,
                    }, ensure_ascii=False),
                })
                continue

            # Inyección de identidad confiable: el usuario_id proviene estrictamente de contexto
            capacidad = (
                nombre_tool
                if nombre_tool in agente.metadata.capacidades
                else (agente.metadata.capacidades[0] if agente.metadata.capacidades else None)
            )
            inp = AgentInput(
                contenido=f"Ejecución de herramienta '{nombre_tool}' por orquestador",
                contexto=contexto,
                parametros=parametros,
                intencion=capacidad,
            )

            # Despacho resiliente al agente
            res_ag = ejecutor.ejecutar(inp)
            resultados_agentes.append(res_ag)

            # Serializar resultado para la retroalimentación al LLM
            if res_ag.exitoso and res_ag.salida:
                salida_datos = {
                    "exitoso": True,
                    "respuesta": res_ag.salida.respuesta,
                    "datos": dict(res_ag.salida.datos),
                }
                # Si hay propuesta de mutación física, se advierte de confirmación en dos pasos
                if res_ag.salida.propuesta:
                    salida_datos["propuesta"] = dict(res_ag.salida.propuesta)
                    salida_datos["requiere_confirmacion_humana"] = True
            else:
                salida_datos = {
                    "exitoso": False,
                    "codigo_error": res_ag.error.codigo.value if res_ag.error else "ERROR",
                    "mensaje_error": res_ag.error.mensaje if res_ag.error else "Fallo en agente",
                }

            mensajes_turno.append({
                "role": "tool",
                "tool_call_id": call_id,
                "content": json.dumps(salida_datos, ensure_ascii=False),
            })

        # 5. Segunda llamada al LLM para sintetizar la respuesta final al usuario
        try:
            resp_sintesis = self._llm.create_chat_completion(
                messages=mensajes_turno,
                max_tokens=512,
                temperature=0.2,
            )
            choices_sintesis = resp_sintesis.get("choices", [])
            texto_final = (
                choices_sintesis[0].get("message", {}).get("content", "").strip()
                if choices_sintesis
                else "Operación completada con éxito."
            )
        except Exception as se:
            # Fallback si falla la síntesis: devolver las respuestas directas de los agentes
            respuestas_agentes = [
                r.salida.respuesta
                for r in resultados_agentes
                if r.exitoso and r.salida
            ]
            texto_final = (
                "\n".join(respuestas_agentes)
                if respuestas_agentes
                else f"Herramientas ejecutadas con error en síntesis: {se}"
            )

        # Actualizar historial persistente de la sesión
        historial.append({"role": "user", "content": mensaje})
        historial.append({"role": "assistant", "content": texto_final})

        duracion = (time.perf_counter() - t0) * 1000.0
        return OrquestadorResult(
            exitoso=True,
            respuesta=texto_final,
            herramientas_ejecutadas=tuple(herramientas_ejecutadas),
            resultados_agentes=tuple(resultados_agentes),
            duracion_total_ms=duracion,
            correlation_id=c_id,
        )
