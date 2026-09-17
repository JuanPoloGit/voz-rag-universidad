"""Motor RAG local sobre ChromaDB.

Dos colecciones aisladas (AudacIA / Universidad) para que el enrutador decida el
corpus y no se crucen las fuentes. La sincronizacion es idempotente y con estado:
cada fragmento guarda el hash del documento del que salio, asi que reindexar solo
cuesta cuando el .md cambio, y los fragmentos de versiones anteriores se eliminan
en lugar de quedar recuperables para siempre.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain_text_splitters import RecursiveCharacterTextSplitter

from .config import RagConfig
from .routing import Intencion


# Marcas de exportacion que arrastra el corpus ("[cite: 1]"). No aportan nada al
# embedding y el modelo puede llegar a leerlas en voz alta durante la exhibicion.
_ARTEFACTOS = re.compile(r"\s*\[cite:\s*\d+\]")


def _hash_documento(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()[:16]


def limpiar_corpus(texto: str) -> str:
    """Elimina artefactos de exportacion sin tocar el archivo original en disco."""
    return _ARTEFACTOS.sub("", texto)


@dataclass(frozen=True)
class Pieza:
    """Un fragmento indexable con el titulo de la seccion de la que salio."""

    texto: str
    titulo: str


# Tres clases de documento, y cada una responde a una profundidad distinta:
# el indice enumera, la ficha explica un proyecto, lo institucional es el resto.
def _tipo_de_documento(nombre_archivo: str) -> str:
    plano = nombre_archivo.lower()
    if "indice" in plano:
        return "indice"
    if "proyecto" in plano:
        return "ficha"
    return "institucional"


_CABECERA_SECCION = re.compile(r"^##\s+(?!#)(.+?)\s*$", re.MULTILINE)

# Una pieza mas corta que esto no informa de nada y si estorba: el titulo suelto
# de un archivo ("# AudacIA - proyectos de salud") ganaba la busqueda de
# "explicame cada proyecto" por ser corto y del tema exacto, y desplazaba a las
# fichas que si tenian el contenido.
_MINIMO_PIEZA = 120


def _partir_en_secciones(texto: str) -> list[tuple[str, str]]:
    """Divide el documento por sus encabezados `##`, conservando el preambulo."""
    cortes = list(_CABECERA_SECCION.finditer(texto))
    if not cortes:
        return [("", texto)]

    secciones: list[tuple[str, str]] = []
    preambulo = texto[: cortes[0].start()].strip()
    for i, corte in enumerate(cortes):
        fin = cortes[i + 1].start() if i + 1 < len(cortes) else len(texto)
        cuerpo = texto[corte.start():fin].strip()
        # El preambulo (titulo del archivo y su entradilla) viaja pegado a la
        # primera seccion en vez de ser una pieza suya: solo, es un titulo.
        if i == 0 and preambulo:
            cuerpo = f"{preambulo}\n\n{cuerpo}"
        secciones.append((corte.group(1).strip(), cuerpo))
    if not secciones and preambulo:
        secciones.append(("", preambulo))
    return secciones


class LocalRAGEngine:
    """Indexa el corpus institucional y resuelve consultas semanticas por dominio."""

    def __init__(self, config: RagConfig, logger: logging.Logger) -> None:
        self._cfg = config
        self._log = logger.getChild("rag")

        import chromadb

        self._cliente = chromadb.PersistentClient(path=str(config.db_path))
        sufijo, funcion_embedding = self._resolver_embeddings()
        self._multilingue_activo = bool(sufijo)

        self._colecciones: dict[Intencion, Any] = {
            Intencion.AUDACIA: self._cliente.get_or_create_collection(
                name=f"audacia_knowledge{sufijo}", embedding_function=funcion_embedding
            ),
            Intencion.UNIVERSIDAD: self._cliente.get_or_create_collection(
                name=f"universidad_knowledge{sufijo}", embedding_function=funcion_embedding
            ),
        }
        self._divisor = RecursiveCharacterTextSplitter(
            chunk_size=config.chunk_size, chunk_overlap=config.chunk_overlap
        )

    def _resolver_embeddings(self) -> tuple[str, Any]:
        """Devuelve (sufijo de coleccion, funcion de embedding) segun la configuracion."""
        if not self._cfg.multilingual_embeddings:
            return "", None
        try:
            from chromadb.utils import embedding_functions

            funcion = embedding_functions.SentenceTransformerEmbeddingFunction(
                model_name=self._cfg.multilingual_model
            )
            self._log.info("Embeddings multilingues activos: %s", self._cfg.multilingual_model)
            return "_ml", funcion
        except Exception:
            self._log.warning(
                "No se pudo cargar sentence-transformers; se usa el embedding por defecto",
                exc_info=True,
            )
            return "", None

    def purgar_colecciones_obsoletas(self) -> list[str]:
        """Elimina colecciones de configuraciones de embedding anteriores.

        Cambiar el modelo de embeddings crea colecciones nuevas y las viejas quedan
        ocupando disco sin que nada las consulte.
        """
        vigentes = {c.name for c in self._colecciones.values()}
        eliminadas: list[str] = []
        try:
            existentes = [c.name for c in self._cliente.list_collections()]
        except Exception:
            self._log.debug("No se pudieron listar las colecciones", exc_info=True)
            return eliminadas
        for nombre in existentes:
            if nombre in vigentes or not nombre.startswith(("audacia_knowledge", "universidad_knowledge")):
                continue
            try:
                self._cliente.delete_collection(nombre)
                eliminadas.append(nombre)
            except Exception:
                self._log.warning("No se pudo eliminar la coleccion %s", nombre, exc_info=True)
        if eliminadas:
            self._log.info("Colecciones obsoletas eliminadas: %s", ", ".join(eliminadas))
        return eliminadas

    # ---------------------------------------------------------- indexacion

    def sincronizar_documentos(self) -> None:
        """Alinea el indice vectorial con la carpeta de documentos."""
        carpeta = self._cfg.doc_folder
        if not carpeta.exists():
            carpeta.mkdir(parents=True, exist_ok=True)
            self._log.warning("Carpeta de documentos creada vacia: %s", carpeta)
            return

        archivos = sorted(p for p in carpeta.iterdir() if p.suffix.lower() in {".md", ".txt"})
        if not archivos:
            self._log.warning("No hay documentos que indexar en %s", carpeta)
            return

        for archivo in archivos:
            try:
                self._sincronizar_archivo(archivo)
            except Exception:
                self._log.error("Fallo la indexacion de %s", archivo.name, exc_info=True)

        self._purgar_documentos_borrados({a.name for a in archivos})

    def _purgar_documentos_borrados(self, vigentes: set[str]) -> None:
        """Borra los fragmentos de documentos que ya no estan en la carpeta.

        Hasta ahora solo se limpiaban las versiones antiguas de un archivo vivo:
        al retirar un `.md` del corpus, sus fragmentos seguian en el indice y
        HACU respondia con material que su autor habia dado de baja.
        """
        for intencion, coleccion in self._colecciones.items():
            try:
                actuales = coleccion.get(include=["metadatas"])
            except Exception:
                self._log.error("No se pudo revisar la coleccion %s", intencion.value, exc_info=True)
                continue
            sobrantes = [
                identificador
                for identificador, meta in zip(actuales.get("ids") or [],
                                               actuales.get("metadatas") or [], strict=False)
                if meta.get("source") not in vigentes
            ]
            if sobrantes:
                coleccion.delete(ids=sobrantes)
                fuentes = {m.get("source") for m in (actuales.get("metadatas") or [])
                           if m.get("source") not in vigentes}
                self._log.info("Documentos retirados del corpus %s: %s (%d fragmentos)",
                               intencion.value, ", ".join(sorted(map(str, fuentes))), len(sobrantes))

    def _sincronizar_archivo(self, archivo: Path) -> None:
        texto = limpiar_corpus(archivo.read_text(encoding="utf-8", errors="ignore"))
        if not texto.strip():
            return

        destino = self._coleccion_para(archivo.name)
        huella = _hash_documento(texto)
        previos = destino.get(where={"source": archivo.name}, include=["metadatas"])
        ids_previos: list[str] = list(previos.get("ids") or [])
        hashes_previos = {m.get("doc_hash") for m in (previos.get("metadatas") or [])}

        if ids_previos and hashes_previos == {huella}:
            self._log.debug("Sin cambios en %s (%d fragmentos)", archivo.name, len(ids_previos))
            return

        piezas = self._trocear(texto)
        tipo = _tipo_de_documento(archivo.name)
        ids = [f"{archivo.name}::{huella}::{i}" for i in range(len(piezas))]
        destino.upsert(
            documents=[p.texto for p in piezas],
            ids=ids,
            metadatas=[
                {"source": archivo.name, "doc_hash": huella, "tipo": tipo,
                 "titulo": p.titulo, "orden": i}
                for i, p in enumerate(piezas)
            ],
        )

        huerfanos = [i for i in ids_previos if i not in set(ids)]
        if huerfanos:
            destino.delete(ids=huerfanos)

        self._log.info(
            "Indexado %s [%s]: %d fragmentos (%d obsoletos eliminados)",
            archivo.name, tipo, len(piezas), len(huerfanos),
        )

    def _trocear(self, texto: str) -> list[Pieza]:
        """Trocea respetando las secciones `##` del documento.

        El troceo por caracteres partia las fichas por la mitad: media descripcion
        de Mary en un fragmento y la otra media en otro, de modo que una pregunta
        concreta recuperaba medio proyecto. Aqui cada seccion `##` es una pieza
        entera, y solo se subdivide la que se pasa de `chunk_max_seccion`; en ese
        caso cada trozo hereda el titulo de su seccion para no quedar huerfano.
        """
        secciones = _partir_en_secciones(texto)
        piezas: list[Pieza] = []
        for titulo, cuerpo in secciones:
            if len(cuerpo) <= self._cfg.chunk_max_seccion:
                piezas.append(Pieza(cuerpo, titulo))
                continue
            for trozo in self._divisor.split_text(cuerpo):
                encabezado = f"{titulo}\n" if titulo and not trozo.startswith(titulo) else ""
                piezas.append(Pieza(encabezado + trozo, titulo))
        return [p for p in piezas if len(p.texto.strip()) >= _MINIMO_PIEZA]

    def _coleccion_para(self, nombre_archivo: str) -> Any:
        clave = Intencion.AUDACIA if "audacia" in nombre_archivo.lower() else Intencion.UNIVERSIDAD
        return self._colecciones[clave]

    # ---------------------------------------------------------- recuperacion

    @property
    def embeddings_multilingues(self) -> bool:
        """Si la instancia acabo usando el modelo multilingue o cayo al de por defecto."""
        return self._multilingue_activo

    def buscar_relevante(
        self, consulta: str, n_results: int, umbral: float
    ) -> tuple[str, Intencion] | None:
        """Consulta ambos corpus y devuelve el mejor resultado solo si es lo bastante cercano.

        Es el rescate de las preguntas de seguimiento ("¿en que ano se fundo?"),
        que el router lexico clasifica como GENERAL por no llevar palabra clave.
        Sin esto el modelo respondia de memoria parametrica e inventaba fechas.
        """
        if not consulta.strip():
            return None

        # Una sola consulta por corpus, ya con n_results definitivo: la distancia y
        # los documentos salen del mismo viaje. Antes se embebia la consulta tres
        # veces (dos para medir, una para recuperar).
        mejor: tuple[float, Intencion, list[str]] | None = None
        for intencion, coleccion in self._colecciones.items():
            try:
                # Mismo motivo que en `buscar`: el indice no participa en el
                # rescate por distancia, solo se carga cuando toca enumerar.
                resultado = coleccion.query(query_texts=[consulta], n_results=n_results,
                                            where={"tipo": {"$ne": "indice"}})
            except Exception:
                self._log.error("Error midiendo distancia en %s", intencion.value, exc_info=True)
                continue
            distancias = (resultado.get("distances") or [[]])[0]
            documentos = (resultado.get("documents") or [[]])[0]
            if not distancias or distancias[0] is None or not documentos:
                continue
            if mejor is None or distancias[0] < mejor[0]:
                mejor = (float(distancias[0]), intencion, list(documentos))

        if mejor is None or mejor[0] > umbral:
            if mejor is not None:
                self._log.debug("Rescate descartado: distancia %.4f > %.2f", mejor[0], umbral)
            return None

        distancia, intencion, documentos = mejor
        self._log.debug("Rescate GENERAL -> %s (distancia %.4f)", intencion.value, distancia)
        return "\n---\n".join(documentos), intencion

    def cargar_indice(self, intencion: Intencion) -> str | None:
        """Devuelve el indice-catalogo ENTERO, en orden, sin pasar por el embedding.

        Buscar el indice por similitud era un error de concepto: es un documento
        pequeno, fijo y disenado para leerse completo, y la busqueda devolvia dos
        de sus cuatro fragmentos, asi que HACU enumeraba cinco proyectos de
        treinta y dos y creia que esos eran todos. Aqui se carga tal cual.
        """
        coleccion = self._colecciones.get(intencion)
        if coleccion is None:
            return None
        try:
            resultado = coleccion.get(where={"tipo": "indice"}, include=["documents", "metadatas"])
        except Exception:
            self._log.error("Error cargando el indice de %s", intencion.value, exc_info=True)
            return None

        documentos = resultado.get("documents") or []
        metadatos = resultado.get("metadatas") or []
        if not documentos:
            return None
        ordenados = sorted(zip(documentos, metadatos, strict=False),
                           key=lambda par: (par[1].get("source", ""), par[1].get("orden", 0)))
        return "\n".join(doc for doc, _ in ordenados)

    def buscar(self, intencion: Intencion, consulta: str, n_results: int,
               tipo: str | None = None) -> str | None:
        """Recupera fragmentos del corpus de la intencion detectada.

        `tipo` restringe la busqueda a una clase de documento: "indice" para
        enumerar, "ficha" para el detalle de un proyecto.

        Sin filtro se busca en todo el corpus MENOS el indice. El indice se carga
        entero con `cargar_indice` cuando toca enumerar; dejarlo competir en la
        busqueda por distancia lo colaba donde no pinta nada: a "¿cuantos
        estudiantes hay?" le llego el catalogo, y HACU contesto "32 proyectos"
        —un numero de otra pregunta— en vez de admitir que no tenia el dato.
        """
        coleccion = self._colecciones.get(intencion)
        if coleccion is None or not consulta.strip():
            return None
        try:
            resultado = coleccion.query(
                query_texts=[consulta], n_results=n_results,
                where={"tipo": tipo} if tipo else {"tipo": {"$ne": "indice"}},
            )
        except Exception:
            self._log.error("Error consultando el corpus %s", intencion.value, exc_info=True)
            return None

        documentos = (resultado.get("documents") or [[]])[0]
        return "\n---\n".join(documentos) if documentos else None
