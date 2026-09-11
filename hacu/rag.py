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

        fragmentos = self._divisor.split_text(texto)
        ids = [f"{archivo.name}::{huella}::{i}" for i in range(len(fragmentos))]
        destino.upsert(
            documents=fragmentos,
            ids=ids,
            metadatas=[{"source": archivo.name, "doc_hash": huella} for _ in fragmentos],
        )

        huerfanos = [i for i in ids_previos if i not in set(ids)]
        if huerfanos:
            destino.delete(ids=huerfanos)

        self._log.info(
            "Indexado %s: %d fragmentos (%d obsoletos eliminados)",
            archivo.name, len(fragmentos), len(huerfanos),
        )

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
                resultado = coleccion.query(query_texts=[consulta], n_results=n_results)
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

    def buscar(self, intencion: Intencion, consulta: str, n_results: int) -> str | None:
        """Recupera fragmentos del corpus correspondiente a la intencion detectada."""
        coleccion = self._colecciones.get(intencion)
        if coleccion is None or not consulta.strip():
            return None
        try:
            resultado = coleccion.query(query_texts=[consulta], n_results=n_results)
        except Exception:
            self._log.error("Error consultando el corpus %s", intencion.value, exc_info=True)
            return None

        documentos = (resultado.get("documents") or [[]])[0]
        return "\n---\n".join(documentos) if documentos else None
