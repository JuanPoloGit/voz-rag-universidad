"""Rescate lexico: lo que el embedding no encuentra pero esta escrito.

El problema, medido sobre el corpus real: a "¿cual seria el proyecto mas
interesante para los conductores?" no volvia la ficha de Deteccion de Fatiga
Visual, que dice literalmente "conductores" y "cabina". Ganaba el Proyecto
Tanque, porque un dron terrestre esta semanticamente mas cerca de "conducir
vehiculos" que una ficha sobre parpados y somnolencia.

Un embedding de parafrasis mide parecido de sentido global, y una palabra rara
dentro de una ficha larga se diluye. Pero esa palabra rara es justo la senal mas
fuerte que existe: si el visitante dice "conductores" y hay UNA ficha en todo el
corpus que habla de conductores, esa ficha va.

De ahi la regla: una pieza se rescata cuando comparte con la pregunta una palabra
DISTINTIVA, entendida como una que aparece en pocas piezas del corpus. La rareza
se mide sobre el propio corpus y no se lista a mano: "proyecto" sale en casi
todas y no distingue nada; "conductor" sale en dos y lo distingue todo.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .routing import normalizar

# Palabras vacias del castellano. La lista es corta a proposito: el filtro de
# rareza ya se encarga de lo generico del dominio ("proyecto", "audacia"), y una
# lista larga escrita a mano envejece mal.
_VACIAS: frozenset[str] = frozenset("""
    para pero como cuando donde porque sobre entre desde hasta segun
    tiene tienen tener hacer hace hacen puede pueden quiere quieren
    esta estan este esta esto esos esas aquel aquella algun alguna
    todo toda todos todas otro otra otros otras mismo misma
    mas menos muy tambien solo solamente ademas entonces ahora
    cual cuales quien quienes cuanto cuanta cuantos cuantas
    seria serian sera seran sido siendo
    interesante interesantes gustaria explicame cuentame dime
    personas gente cosas forma manera parte partes
""".split())

_PALABRA = re.compile(r"[a-z0-9ñ]{4,}")

# Una palabra deja de distinguir cuando aparece en mas de esta fraccion de las
# piezas. Con ~40 fichas, 0.15 son 6: "conductor" (2 piezas) distingue, "sensor"
# (una docena) no.
_FRACCION_MAXIMA = 0.15
_MINIMO_PIEZAS_RARAS = 2


def raiz(palabra: str) -> str:
    """Quita el plural mas comun. "conductores" y "conductor" son la misma senal."""
    for sufijo in ("es", "s"):
        if len(palabra) > 5 and palabra.endswith(sufijo):
            return palabra[: -len(sufijo)]
    return palabra


def raices(texto: str) -> set[str]:
    """Palabras con contenido de un texto, ya normalizadas y sin plural."""
    return {raiz(p) for p in _PALABRA.findall(normalizar(texto)) if p not in _VACIAS}


# Una mayuscula que NO abre oracion marca un nombre propio. Es la senal que
# separa "MacondoLab" de "anos": las dos son palabras raras en el corpus —dos
# piezas y una— pero solo una nombra algo. Sin esta distincion, rescatar por
# palabra rara en las preguntas sin dominio metia la ficha de Mary en un
# "¿cuantos anos tienes?", y con ella desactivaba el aviso de fuera-de-dominio.
_NOMBRE_PROPIO = re.compile(r"(?<![.!?¡¿]\s)(?<!^)\b([A-ZÁÉÍÓÚÜÑ][\wÁÉÍÓÚÜÑáéíóúüñ-]{2,})")


def nombres_propios(texto: str) -> set[str]:
    """Raices de las palabras que aparecen como nombre propio en el texto."""
    return {raiz(normalizar(m)) for m in _NOMBRE_PROPIO.findall(texto)} - _VACIAS


@dataclass
class Pieza:
    """Un fragmento del corpus, con lo que hace falta para decidir si se rescata."""

    texto: str
    titulo: str
    tipo: str
    raices: frozenset[str] = field(default_factory=frozenset)
    propios: frozenset[str] = field(default_factory=frozenset)
    raices_titulo: frozenset[str] = field(default_factory=frozenset)


class IndiceLexico:
    """Indice invertido en memoria sobre las piezas de un corpus.

    Ocupa lo que ocupa el corpus (decenas de kilobytes) y se construye en
    milisegundos, asi que se rehace entero en cada arranque a partir de lo que
    hay en Chroma: no puede desincronizarse de lo que se busca.
    """

    def __init__(self, piezas: list[Pieza] | None = None) -> None:
        self._piezas: list[Pieza] = []
        self._por_raiz: dict[str, set[int]] = {}
        self._propios: set[str] = set()
        for pieza in piezas or []:
            self.anadir(pieza)

    def __len__(self) -> int:
        return len(self._piezas)

    def anadir(self, pieza: Pieza) -> None:
        indice = len(self._piezas)
        entero = f"{pieza.titulo} {pieza.texto}"
        propias = pieza.raices or frozenset(raices(entero))
        nombres = pieza.propios or frozenset(nombres_propios(entero))
        del_titulo = pieza.raices_titulo or frozenset(raices(pieza.titulo))
        self._piezas.append(
            Pieza(pieza.texto, pieza.titulo, pieza.tipo, propias, nombres, del_titulo)
        )
        for r in propias:
            self._por_raiz.setdefault(r, set()).add(indice)
        self._propios |= set(nombres)

    def distintivas(self, consulta: str, solo_nombres: bool = False) -> set[str]:
        """Palabras de la consulta que de verdad seleccionan algo del corpus.

        `solo_nombres` exige ademas que la palabra aparezca en el corpus como
        nombre propio. Hace falta en las preguntas sin dominio detectado, donde
        rescatar por rareza a secas es peligroso: "anos" sale en UNA pieza y
        arrastraba la ficha de Mary a un "¿cuantos anos tienes?".
        """
        if not self._piezas:
            return set()
        techo = max(_MINIMO_PIEZAS_RARAS, int(len(self._piezas) * _FRACCION_MAXIMA))
        candidatas = {
            r for r in raices(consulta)
            if 0 < len(self._por_raiz.get(r, ())) <= techo
        }
        return candidatas & self._propios if solo_nombres else candidatas

    def rescatar(self, consulta: str, ya_recuperado: set[str], maximo: int,
                 tipo: str | None = None, solo_nombres: bool = False) -> list[Pieza]:
        """Piezas que comparten palabra distintiva con la pregunta y no volvieron.

        Se ordenan por cuantas palabras distintivas comparten: si el visitante
        dice "conductores" y "somnolencia", la ficha que lleva las dos va antes
        que la que lleva una.

        A igualdad de palabras compartidas gana la pieza cuyo TITULO lleva la
        palabra. Sin ese desempate mandaba el orden de indexacion, y eso tenia
        una consecuencia medida: "¿Que es MacondoLab?" empata a una palabra en
        cuatro piezas —"Personas del centro", "Publicaciones cientificas",
        "MacondoLab" y "CICV"—, el cupo es de dos, y las dos que ganaban eran
        las dos que solo lo MENCIONAN de pasada, porque su fichero se indexa
        antes. La seccion titulada "MacondoLab", que es la que responde, salia
        cuarta y no entraba nunca.
        """
        buscadas = self.distintivas(consulta, solo_nombres)
        if not buscadas:
            return []
        candidatos: list[tuple[int, int, int]] = []
        for indice, pieza in enumerate(self._piezas):
            if tipo is not None and pieza.tipo != tipo:
                continue
            if pieza.texto in ya_recuperado:
                continue
            comunes = len(buscadas & pieza.raices)
            if comunes:
                candidatos.append((comunes, len(buscadas & pieza.raices_titulo), indice))
        candidatos.sort(key=lambda t: (-t[0], -t[1], t[2]))
        return [self._piezas[i] for _, _, i in candidatos[:maximo]]
