"""Orquestacion de un turno de conversacion.

Separa la logica del turno de la capa de presentacion. La consola de exhibicion y
el harness de pruebas ejecutan exactamente este mismo camino, de modo que lo que
se mide en las pruebas es lo que ocurre en escena.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from .context import ContextBuilder, EstadoSesion
from .cuidado import Cuidado, evaluar, respuesta_de_crisis
from .estilo import RetenedorDeCola
from .extractor import BackgroundMemoryExtractor
from .identity import IdentityResolver
from .llm import Aliento, LlmService
from .memory import HacuMemoryDB
from .routing import (
    FastRouter,
    Intencion,
    aire_breve,
    es_catalogo,
    es_confidencia,
    es_despedida,
    es_seguimiento,
    pide_desarrollo,
)


@dataclass(frozen=True)
class ResultadoTurno:
    """Traza completa de un turno, apta para consola o para informe de pruebas."""

    entrada: str
    respuesta: str
    intencion: Intencion
    usuario: str
    usuario_anterior: str
    migrado: bool
    tokens: int
    segundos: float
    coletilla_descartada: bool = False
    adulaciones_quitadas: int = 0
    fugas_limpiadas: int = 0
    extenso: bool = False
    atribuciones_quitadas: int = 0
    truncada: bool = False
    cuidado: Cuidado = Cuidado.NINGUNO

    @property
    def tokens_por_segundo(self) -> float:
        return self.tokens / self.segundos if self.segundos > 0 else 0.0


class HacuSession:
    """Un visitante frente a HACU: identidad, enrutado, generacion y persistencia."""

    def __init__(
        self,
        llm: LlmService,
        db: HacuMemoryDB,
        router: FastRouter,
        identity: IdentityResolver,
        extractor: BackgroundMemoryExtractor,
        context_builder: ContextBuilder,
        logger: logging.Logger,
        recursos_de_ayuda: str = "",
    ) -> None:
        self._llm = llm
        self._db = db
        self._router = router
        self._identity = identity
        self._extractor = extractor
        self._context = context_builder
        self._log = logger.getChild("sesion")
        self._recursos = recursos_de_ayuda
        self.estado = EstadoSesion()
        # Ultimo dominio reconocido; sostiene las preguntas de seguimiento.
        self._dominio_previo: Intencion | None = None

    @property
    def usuario_activo(self) -> str:
        return self._identity.usuario_activo

    @property
    def identidad(self) -> IdentityResolver:
        return self._identity

    def clasificar(self, texto: str) -> tuple[Intencion, float]:
        """Enruta el texto y devuelve (intencion, segundos empleados).

        Cuando el router no reconoce dominio pero el mensaje se apoya en lo ya
        dicho, se hereda el dominio del ultimo turno que si lo tuvo. Es mas fiable
        que dejarselo a la distancia semantica, que no sabe de que se venia
        hablando.
        """
        inicio = time.perf_counter()
        if self.estado.trivia:
            return Intencion.UNIVERSIDAD, time.perf_counter() - inicio

        intencion = self._router.clasificar(texto)
        if intencion is Intencion.GENERAL and self._dominio_previo and es_seguimiento(texto):
            intencion = self._dominio_previo
            self._log.debug("Seguimiento: se hereda el dominio %s", intencion.value)
        return intencion, time.perf_counter() - inicio

    def olvidar_todo(self) -> int:
        """Borra TODA la memoria y deja la sesion como recien arrancada.

        Vive aqui y no en cada interfaz porque "borrar todo" no es una sola
        operacion: la base es lo obvio, pero tambien hay que invalidar lo que el
        extractor tenga en vuelo —si no, un turno a medio analizar reescribe un
        perfil un segundo despues de purgarlo—, soltar la identidad, y tirar el
        dominio heredado y el modo trivia, que son estado de la conversacion
        anterior. La consola borraba tres de esas cinco cosas y la ventana,
        cuatro.

        Devuelve cuantos perfiles se eliminaron.
        """
        self._extractor.olvidar_todo()
        borrados = self._db.purge_all()
        self._identity.reiniciar()
        self.estado = EstadoSesion()
        self._dominio_previo = None
        self._log.warning("Memoria purgada por completo: %d perfiles", borrados)
        return borrados

    def _turno_de_crisis(self, texto: str,
                         on_token: Callable[[str], None] | None) -> ResultadoTurno:
        """Responde con el texto fijo, sin pasar por el modelo.

        No es una respuesta mas cauta: es que aqui NO hay generacion. Un 8B lleva
        todo el proyecto ignorando reglas del prompt, y en la unica prueba real
        con una persona en crisis se invento unos recursos de ayuda de otro pais
        y despacho a la persona con un "no puedo continuar con la conversacion".
        Eso no se arregla pidiendoselo mejor.

        Lo que dijo el visitante NO se guarda: queda en el historial como una
        marca neutra. Es informacion de salud mental de alguien que pasaba por
        una exhibicion, y no tiene por que quedar escrita en un SQLite.
        """
        inicio = time.perf_counter()
        usuario = self._identity.usuario_activo
        respuesta = respuesta_de_crisis(self._recursos)
        if on_token is not None:
            on_token(respuesta)
        self._log.warning("Turno de cuidado: se respondio con el texto fijo de crisis")
        self._db.add_message(usuario, "user", "[mensaje sensible, no registrado]")
        self._db.add_message(usuario, "assistant", respuesta)
        return ResultadoTurno(
            entrada=texto,
            respuesta=respuesta,
            intencion=Intencion.GENERAL,
            usuario=usuario,
            usuario_anterior=usuario,
            migrado=False,
            tokens=0,
            segundos=time.perf_counter() - inicio,
            cuidado=Cuidado.CRISIS,
        )

    def saludar(self, texto: str) -> str:
        """Abre la visita con una frase fija y la deja escrita en el historial.

        No pasa por el modelo a proposito. Una frase literal puesta delante de un
        8B es lo que el modelo acaba recitando en los turnos siguientes, y en este
        proyecto eso ya ocurrio cuatro veces con textos de andamiaje.

        Si que se guarda como turno de HACU, y eso es lo importante: cuando el
        visitante conteste "Daniela", el modelo vera la pregunta justo encima. Sin
        esto, el primer mensaje de la conversacion seria un nombre suelto sin nada
        que lo explique, y la respuesta saldria desorientada.

        Devuelve el texto guardado (cadena vacia si no hay saludo configurado),
        para que la consola y la ventana muestren exactamente lo mismo que se
        registro.
        """
        limpio = texto.strip()
        if not limpio:
            return ""
        self._db.add_message(self.usuario_activo, "assistant", limpio)
        self._log.debug("Saludo de apertura registrado para %s", self.usuario_activo)
        return limpio

    def turno(
        self,
        texto: str,
        on_token: Callable[[str], None] | None = None,
        intencion: Intencion | None = None,
    ) -> ResultadoTurno:
        """Procesa un mensaje completo del visitante y devuelve su traza."""
        cuidado = evaluar(texto)
        if cuidado is Cuidado.CRISIS:
            return self._turno_de_crisis(texto, on_token)

        evento = self._identity.procesar(texto)
        migrado = False
        if evento.requiere_migracion:
            self._db.migrate_profile(evento.usuario_anterior, evento.usuario_actual)
            migrado = True

        if intencion is None:
            intencion, _ = self.clasificar(texto)

        if intencion is not Intencion.GENERAL:
            self._dominio_previo = intencion

        usuario = self._identity.usuario_activo
        mensajes = self._context.build_messages(texto, intencion, usuario, self.estado)

        inicio = time.perf_counter()
        emitido: list[str] = []
        social = es_despedida(texto)
        retenedor = RetenedorDeCola(
            permitir_cierre_breve=social,
            permitir_calidez=es_confidencia(texto),
        )
        tokens = 0

        def emitir(texto: str) -> None:
            if not texto:
                return
            emitido.append(texto)
            if on_token is not None:
                on_token(texto)

        # Una peticion de desarrollo ("explicame cada proyecto") necesita mas techo
        # que una pregunta de tarima. Y hay que saber POR QUE termino la
        # generacion: si fue por tope, lo retenido es media frase y no se emite.
        # Anchura o fondo: las dos necesitan techo. Enumerar 32 proyectos no cabe
        # en 384 tokens, y explicar uno a fondo tampoco.
        extenso = pide_desarrollo(texto) or es_catalogo(texto)
        # Tres niveles, decididos por lo que el turno PIDE. El orden importa:
        # pedir FONDO manda sobre todo lo demas, la brevedad manda sobre la
        # ANCHURA —"¿cuantos proyectos tiene?" pide el catalogo para contarlo,
        # pero se responde con un numero— y NORMAL es lo que queda.
        #
        # `aire_breve` sustituye a `social`, que solo reconocia los cierres. El
        # guion marca 51 turnos como breves y el runtime reconocia 3: los otros
        # 48 recibian 384 tokens y el modelo los usaba. Medido sobre las
        # respuestas reales de la corrida de 100 turnos, aplicar el techo breve
        # a lo que detecta `aire_breve` arregla cinco turnos y no rompe ninguno.
        if pide_desarrollo(texto):
            aliento = Aliento.EXTENSO
        elif aire_breve(texto):
            aliento = Aliento.BREVE
        elif extenso:
            aliento = Aliento.EXTENSO
        else:
            aliento = Aliento.NORMAL
        motivo: list[str | None] = [None]

        for fragmento in self._llm.stream_chat(mensajes, extenso=extenso,
                                               aliento=aliento,
                                               al_terminar=motivo.append):
            tokens += 1
            emitir(retenedor.alimentar(fragmento))
        emitir(retenedor.cerrar(incompleta=motivo[-1] == "length"))
        segundos = time.perf_counter() - inicio

        respuesta = "".join(emitido).strip()
        if retenedor.descartada:
            self._log.debug("Coletilla de cierre descartada (regla 12)")
        if retenedor.adulaciones_quitadas:
            self._log.debug("Frases de adulacion filtradas: %d", retenedor.adulaciones_quitadas)
        if retenedor.atribuciones_quitadas:
            self._log.warning(
                "Atribuciones al visitante filtradas: %d. HACU le estaba contando al "
                "visitante lo que el visitante supuestamente dijo.",
                retenedor.atribuciones_quitadas,
            )
        if retenedor.fugas_limpiadas:
            self._log.debug("Fugas del andamiaje limpiadas: %d", retenedor.fugas_limpiadas)
        if retenedor.truncada:
            self._log.warning(
                "Respuesta cortada por tope de tokens (extenso=%s); se descarto la frase "
                "incompleta. Si se repite, subir chat_max_tokens_extenso.", extenso
            )
        self._db.add_message(usuario, "user", texto)
        self._db.add_message(usuario, "assistant", respuesta)
        # Lo que alguien cuenta de su salud mental no se convierte en un "hecho"
        # de su perfil. La cola de extraccion no lo ve.
        #
        # Los cierres y elogios tampoco: un "gracias, ha sido muy interesante"
        # no dice NADA del visitante, y el extractor lo convertia en un hecho de
        # perfil —"Ha sido un gusto y le encanta haber conocido mas sobre los
        # proyectos de AudacIA"— que ademas ocupa sitio en el maximo de doce.
        # Visto en la corrida de 100 turnos del 21/09.
        if cuidado is Cuidado.NINGUNO and not social:
            self._extractor.encolar(usuario, texto)

        return ResultadoTurno(
            entrada=texto,
            respuesta=respuesta,
            intencion=intencion,
            usuario=usuario,
            usuario_anterior=evento.usuario_anterior,
            migrado=migrado,
            tokens=tokens,
            segundos=segundos,
            coletilla_descartada=retenedor.descartada,
            adulaciones_quitadas=retenedor.adulaciones_quitadas,
            atribuciones_quitadas=retenedor.atribuciones_quitadas,
            fugas_limpiadas=retenedor.fugas_limpiadas,
            extenso=extenso,
            truncada=retenedor.truncada,
            cuidado=cuidado,
        )
