"""Resolucion de identidad del visitante.

Este modulo es la unica autoridad sobre "con quien esta hablando HACU". Existe
porque la deteccion ingenua (`soy X` -> perfil X) permitia que cualquier adjetivo,
profesion o insulto se convirtiera en un perfil persistido en disco: en una
exhibicion abierta al publico eso es el riesgo principal del sistema.

Reglas de admision de un nombre:
  1. Debe venir de un patron de presentacion explicito, no de cualquier "soy".
  2. Debe superar el filtro lexico (no ser una palabra comun del espanol).
  3. Debe superar el filtro morfologico (sufijos tipicos de adjetivo/profesion),
     con lista de excepciones para nombres reales que los comparten.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .routing import normalizar

# Palabras que nunca son un nombre propio: pronombres, adjetivos, profesiones,
# estados, gentilicios, terminos del propio sistema e insultos. La lista no juzga
# el contenido: solo declara que no identifica a una persona.
PALABRAS_NO_NOMBRE: frozenset[str] = frozenset(
    """
    yo tu el ella ello nosotros ustedes ellos alguien nadie todos nada algo esto eso aquello
    hombre mujer chico chica nino nina bebe joven viejo adulto persona gente humano senor senora
    estudiante alumno profesor profesora maestro docente rector rectora decano ingeniero ingeniera
    medico medica doctor doctora abogado abogada arquitecto arquitecta artista musico cantante
    disenador programador desarrollador tecnico cientifico investigador enfermero contador
    periodista fotografo escritor deportista empresario vendedor conductor operador administrador
    robot maquina programa sistema asistente usuario visitante invitado publico expositor guia
    hacu audacia unisimon universidad simon bolivar ia gpt chatgpt claude llama modelo bot
    bueno buena malo mala feliz triste cansado aburrido enojado bravo contento nervioso
    inteligente tonto listo torpe genio bobo idiota estupido imbecil ridiculo raro loco
    grande pequeno alto bajo gordo flaco fuerte debil rapido lento nuevo viejo mejor peor
    colombiano colombiana barranquillero costeno paisa rolo venezolano mexicano extranjero
    racista homofobico machista xenofobo misogino nazi fascista comunista terrorista
    asesino ladron violador criminal delincuente mentiroso corrupto
    puto puta marica maricon perra zorra cabron pendejo gonorrea malparido hijueputa
    gay lesbiana hetero trans bisexual cristiano catolico ateo musulman judio
    fan admirador amigo amiga novio novia esposo esposa padre madre papa mama hijo hija
    aqui alla hoy manana ayer ahora siempre nunca casi muy mas menos tan solo tambien
    que quien como donde cuando porque cual cuanto si no tal vez quizas claro obvio
    """.split()
)

# Nombres propios reales que colisionan con el filtro morfologico de sufijos.
NOMBRES_PERMITIDOS: frozenset[str] = frozenset(
    """
    salvador rosario amparo soledad trinidad libertad caridad natividad consuelo mercedes
    dolores milagros aurora socorro remedios pilar rocio nieves rosa vicente clemente
    bautista celeste ariadne dante constanza esperanza alianza
    """.split()
)

# Sufijos que en espanol casi siempre marcan adjetivo, gentilicio o profesion.
SUFIJOS_NO_NOMBRE: tuple[str, ...] = (
    "ista", "ismo", "oso", "osa", "ante", "ente", "able", "ible",
    "cion", "sion", "dad", "tud", "aje", "mente", "ero", "era",
)

# Se aplica sobre el texto en minusculas SIN quitar tildes, para poder devolver
# "Sofia" como "Sofía". Admite un guion interno ("Ana-Maria") y nombres de dos
# palabras ("Juan Carlos").
_LETRAS = r"[a-záéíóúüñ]+(?:-[a-záéíóúüñ]+)?"
_PATRON_PRESENTACION = re.compile(
    r"(?:^|[,.;:!?]\s*|\s)(?:me\s+llamo|mi\s+nombre\s+es|me\s+dicen|puedes\s+llamarme|"
    rf"llamame|yo\s+soy|soy)\s+({_LETRAS}(?:\s+{_LETRAS})?)"
)
_PATRON_NEGACION = re.compile(r"\bno\s+(?:me\s+llamo|soy|es)\b")
_MARCADORES_CORRECCION = (
    "en realidad", "realmente", "en verdad", "antes dije", "antes te dije", "me equivoque",
    "equivoque", "corrijo", "correccion", "perdon", "perdona", "disculpa", "mentira",
    "ya no", "mejor dicho", "quise decir",
)
_CONECTORES = frozenset("y o de del la el un una que pero para con sin por en a al mi tu su".split())


def _titular(palabra: str) -> str:
    """Capitaliza respetando guiones internos: 'ana-maria' -> 'Ana-Maria'."""
    return "-".join(trozo.capitalize() for trozo in palabra.split("-"))


@dataclass(frozen=True)
class EventoIdentidad:
    """Resultado de analizar un mensaje en busca de la identidad del visitante."""

    usuario_actual: str
    usuario_anterior: str
    cambio: bool
    es_correccion: bool

    @property
    def requiere_migracion(self) -> bool:
        """Corregir el nombre debe mover el perfil, no crear uno paralelo."""
        return self.cambio and self.es_correccion


class IdentityResolver:
    """Mantiene el perfil activo y valida cualquier intento de cambiarlo."""

    def __init__(self, usuario_por_defecto: str = "visitante") -> None:
        self._por_defecto = usuario_por_defecto
        self._activo = usuario_por_defecto

    @property
    def usuario_activo(self) -> str:
        return self._activo

    def reiniciar(self) -> str:
        """Vuelve al perfil anonimo, tipicamente al terminar con un visitante."""
        self._activo = self._por_defecto
        return self._activo

    def fijar_manualmente(self, nombre: str) -> str | None:
        """Asignacion directa por parte del operador; tambien pasa por validacion."""
        candidato = self._validar(nombre.strip().lower())
        if candidato is None:
            return None
        self._activo = candidato
        return candidato

    def procesar(self, mensaje: str) -> EventoIdentidad:
        """Analiza el turno y actualiza el perfil activo si detecta una presentacion valida."""
        anterior = self._activo
        plano = normalizar(mensaje)

        if _PATRON_NEGACION.search(plano):
            return EventoIdentidad(anterior, anterior, cambio=False, es_correccion=False)

        nombre = self._extraer_nombre(mensaje.lower())
        if nombre is None or nombre == anterior:
            return EventoIdentidad(anterior, anterior, cambio=False, es_correccion=False)

        es_correccion = any(m in plano for m in _MARCADORES_CORRECCION)
        self._activo = nombre
        return EventoIdentidad(nombre, anterior, cambio=True, es_correccion=es_correccion)

    # ---------------------------------------------------------------- internos

    def _extraer_nombre(self, texto: str) -> str | None:
        """Devuelve el nombre valido de la ultima presentacion del mensaje, si la hay."""
        for bruto in reversed(_PATRON_PRESENTACION.findall(texto)):
            partes = bruto.split()
            primero = self._validar(partes[0])
            if primero is None:
                continue
            if len(partes) == 2 and normalizar(partes[1]) not in _CONECTORES:
                segundo = self._validar(partes[1])
                if segundo is not None:
                    return f"{primero} {segundo}"
            return primero
        return None

    def _validar(self, palabra: str) -> str | None:
        """Aplica los tres filtros y devuelve el nombre con sus tildes, o None."""
        if not palabra:
            return None
        plano = normalizar(palabra)
        trozos = plano.split("-")
        if len(trozos) > 2 or not all(t.isalpha() for t in trozos):
            return None
        if not 3 <= len(plano) <= 20:
            return None
        if plano in PALABRAS_NO_NOMBRE or any(t in PALABRAS_NO_NOMBRE for t in trozos):
            return None
        if plano not in NOMBRES_PERMITIDOS and plano.endswith(SUFIJOS_NO_NOMBRE):
            return None
        return _titular(palabra)

    def es_nombre_valido(self, nombre: str) -> bool:
        """Predicado publico usado por el saneado de la base de datos."""
        if nombre == self._por_defecto:
            return True
        partes = nombre.lower().split()
        return bool(partes) and all(self._validar(p) is not None for p in partes)
