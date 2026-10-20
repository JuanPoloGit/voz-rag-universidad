"""Capa de memoria temporal en RAM para Haku Inventario — HakuOperationalContext.

Responsabilidades clave:
1. Almacenar el estado operativo mínimo de interacción (foco del producto, última intención,
   propuesta activa, candidatos).
2. Desacoplado 100% de Django Session (la autenticación web del usuario sigue en DB/cookie por 7 días;
   el contexto conversacional reside en RAM mediante LocMemCache con TTL de 30 minutos).
3. NO almacena audios, transcripciones completas, conversaciones completas ni respuestas generadas.
4. Límite de 20 contextos simultáneos con política de desalojo LRU (óptimo para 10 usuarios concurrentes).
5. Versión de contexto (version_contexto) para invalidar de forma segura esquemas obsoletos.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from django.core.cache import caches

logger = logging.getLogger("integraciones.haku.contexto")

CACHE_ALIAS = "haku_contexto"
VERSION_CONTEXTO_ACTUAL = 2
MAX_CANDIDATOS = 3
MAX_ULTIMAS_ACCIONES = 5
TIMEOUT_INACTIVIDAD_SEGUNDOS = 7200  # 2 horas (7200 segundos)


@dataclass
class EstadoOperativoHaku:
    """Estado operativo efímero mínimo de Haku Inventario."""

    usuario_id: int
    session_key: str
    almacen_id: Optional[int] = None

    # 1. Producto actualmente en contexto conversacional
    producto_id: Optional[int] = None
    producto_codigo: Optional[str] = None
    producto_nombre: Optional[str] = None

    # 2. Última interacción registrada
    ultima_intencion: Optional[str] = None
    ultima_busqueda: Optional[str] = None

    # 3. Productos candidatos para desambiguación rápida (máximo 3)
    # Formato: [{"id": 101, "nombre": "Cable HDMI 2 metros", "codigo": "CAB-001"}]
    candidatos: List[Dict[str, Any]] = field(default_factory=list)

    # 4. Operación pendiente de pre-confirmación en 2 pasos
    propuesta_activa: Optional[Dict[str, Any]] = None

    # 5. Memoria operacional reciente (máximo 5 elementos)
    # Formato: [{"accion": "CONSULTAR_STOCK", "producto": "Cable HDMI", "producto_id": 101, "codigo": "CAB-001", "cantidad": 15, "fecha": ...}]
    # NO almacena chat history, ni audios, ni conversaciones completas.
    ultimas_acciones: List[Dict[str, Any]] = field(default_factory=list)

    # 5-bis. Colección de productos resultante de la última consulta de lista (máximo 10 elementos)
    # Formato: [{"id": 101, "codigo": "CAB-001", "nombre": "Cable HDMI"}]
    coleccion_productos: List[Dict[str, Any]] = field(default_factory=list)
    coleccion_total_original: int = 0

    # 6. Timestamp de control y versionado
    ultimo_acceso: float = field(default_factory=time.time)
    version_contexto: int = VERSION_CONTEXTO_ACTUAL

    def esta_expirado(self, timeout_segundos: int = TIMEOUT_INACTIVIDAD_SEGUNDOS) -> bool:
        """Determina si han transcurrido más de 2 horas sin actividad."""
        return (time.time() - self.ultimo_acceso) > timeout_segundos

    def actualizar_actividad(self) -> None:
        """Actualiza el timestamp de última interacción activa."""
        self.ultimo_acceso = time.time()

    def limpiar_foco_conversacional(self) -> None:
        """Purga el foco del producto, propuestas y memoria operacional sin cerrar sesión web."""
        self.producto_id = None
        self.producto_codigo = None
        self.producto_nombre = None
        self.ultima_intencion = None
        self.ultima_busqueda = None
        self.candidatos = []
        self.coleccion_productos = []
        self.coleccion_total_original = 0
        self.propuesta_activa = None
        self.ultimas_acciones = []

    def asignar_producto(self, producto_id: int, codigo: str, nombre: str) -> None:
        """Establece el producto actualmente referenciado en el foco operativo."""
        self.producto_id = producto_id
        self.producto_codigo = codigo
        self.producto_nombre = nombre
        self.actualizar_actividad()

    def asignar_coleccion(self, lista_items: List[Dict[str, Any]], total_original: Optional[int] = None) -> None:
        """Guarda hasta 10 productos estructurados resultantes de una consulta colectiva, registrando el total original."""
        limpios = []
        for item in lista_items[:10]:
            prod_id = item.get("id") or item.get("producto_id")
            if prod_id:
                limpios.append({
                    "id": int(prod_id),
                    "codigo": str(item.get("codigo", "")),
                    "nombre": str(item.get("nombre", "")),
                })
        self.coleccion_productos = limpios
        self.coleccion_total_original = int(total_original) if total_original is not None else len(lista_items)
        self.actualizar_actividad()

    def asignar_candidatos(self, lista_candidatos: List[Dict[str, Any]]) -> None:
        """Guarda hasta 3 candidatos mínimos útiles para desambiguación."""
        limpios = []
        for c in lista_candidatos[:MAX_CANDIDATOS]:
            limpios.append({
                "id": c.get("id"),
                "nombre": str(c.get("nombre", "")),
                "codigo": str(c.get("codigo", "")),
            })
        self.candidatos = limpios
        self.actualizar_actividad()

    def registrar_accion(
        self,
        accion: str,
        producto: Optional[str] = None,
        producto_id: Optional[int] = None,
        codigo: Optional[str] = None,
        cantidad: Optional[int] = None,
        detalles: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Registra una acción operacional en la memoria reciente (máximo 5 elementos).

        NO almacena mensajes de texto de la conversación, ni transcripciones, ni audios.
        """
        item = {
            "accion": accion,
            "producto": producto,
            "producto_id": producto_id,
            "codigo": codigo,
            "fecha": time.time(),
        }
        if cantidad is not None:
            item["cantidad"] = cantidad
        if detalles:
            claves_prohibidas = {
                "texto", "mensaje", "audio", "conversacion", "prompt", "respuesta",
                "mensaje_usuario", "transcripcion", "raw_audio", "wav", "pcm", "samples",
                "dialogo", "historial", "user_message", "transcript",
            }
            for k, v in detalles.items():
                if k.lower() not in claves_prohibidas and not isinstance(v, (bytes, bytearray)):
                    item[k] = v

        self.ultimas_acciones.append(item)
        if len(self.ultimas_acciones) > MAX_ULTIMAS_ACCIONES:
            self.ultimas_acciones = self.ultimas_acciones[-MAX_ULTIMAS_ACCIONES:]
        self.actualizar_actividad()

    def obtener_producto_anterior(self) -> Optional[Dict[str, Any]]:
        """Recupera el producto de las acciones operacionales anteriores."""
        if not self.ultimas_acciones:
            return None
        # Buscar primero una acción previa que tuviera un producto diferente al actual
        for item in reversed(self.ultimas_acciones):
            if item.get("producto_id") and (self.producto_id is None or item["producto_id"] != self.producto_id):
                return item
        # Si no hay diferente, retornar la última con producto
        for item in reversed(self.ultimas_acciones):
            if item.get("producto_id"):
                return item
        return None

    def to_dict(self) -> Dict[str, Any]:
        """Serializa a diccionario para almacenamiento seguro en caché."""
        return {
            "usuario_id": self.usuario_id,
            "session_key": self.session_key,
            "almacen_id": self.almacen_id,
            "producto_id": self.producto_id,
            "producto_codigo": self.producto_codigo,
            "producto_nombre": self.producto_nombre,
            "ultima_intencion": self.ultima_intencion,
            "ultima_busqueda": self.ultima_busqueda,
            "candidatos": self.candidatos,
            "coleccion_productos": self.coleccion_productos,
            "coleccion_total_original": self.coleccion_total_original,
            "propuesta_activa": self.propuesta_activa,
            "ultimas_acciones": self.ultimas_acciones,
            "ultimo_acceso": self.ultimo_acceso,
            "version_contexto": self.version_contexto,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> EstadoOperativoHaku:
        """Reconstruye el objeto desde diccionario validando versión."""
        return cls(
            usuario_id=data.get("usuario_id", 0),
            session_key=data.get("session_key", ""),
            almacen_id=data.get("almacen_id"),
            producto_id=data.get("producto_id"),
            producto_codigo=data.get("producto_codigo"),
            producto_nombre=data.get("producto_nombre"),
            ultima_intencion=data.get("ultima_intencion"),
            ultima_busqueda=data.get("ultima_busqueda"),
            candidatos=data.get("candidatos", []),
            coleccion_productos=data.get("coleccion_productos", []),
            coleccion_total_original=data.get("coleccion_total_original", 0),
            propuesta_activa=data.get("propuesta_activa"),
            ultimas_acciones=data.get("ultimas_acciones", []),
            ultimo_acceso=data.get("ultimo_acceso", time.time()),
            version_contexto=data.get("version_contexto", VERSION_CONTEXTO_ACTUAL),
        )


class HakuOperationalContext:
    """Gestor de almacenamiento en RAM de contextos operativos con LocMemCache."""

    _lock = threading.Lock()
    _context_hits: int = 0
    _context_misses: int = 0

    @classmethod
    def _obtener_cache(cls):
        """Retorna el backend de caché configurado para contexto o default si no existe."""
        try:
            return caches[CACHE_ALIAS]
        except Exception:
            return caches["default"]

    @classmethod
    def _generar_clave(cls, usuario_id: int, session_key: str) -> str:
        """Genera la clave única y aislada por usuario y dispositivo/sesión."""
        return f"haku:ctx:{usuario_id}:{session_key}"

    @classmethod
    def obtener(cls, usuario_id: int, session_key: str) -> Optional[EstadoOperativoHaku]:
        """Recupera el contexto activo de la RAM.

        Si transcurrieron más de 30 minutos sin actividad, limpia el foco y retorna None.
        """
        if not usuario_id or not session_key:
            return None

        cache = cls._obtener_cache()
        clave = cls._generar_clave(usuario_id, session_key)
        data = cache.get(clave)

        if data is None:
            with cls._lock:
                cls._context_misses += 1
            return None

        # Reconstruir estado
        try:
            estado = EstadoOperativoHaku.from_dict(data)
        except Exception as exc:
            logger.warning("Error deserializando estado de contexto (%s); descartando", exc)
            cache.delete(clave)
            with cls._lock:
                cls._context_misses += 1
            return None

        # Verificar versión de estructura
        if estado.version_contexto != VERSION_CONTEXTO_ACTUAL:
            logger.info("Versión de contexto obsoleta (%s vs %s); invalidando", estado.version_contexto, VERSION_CONTEXTO_ACTUAL)
            cache.delete(clave)
            with cls._lock:
                cls._context_misses += 1
            return None

        # Control de expiración por inactividad (2 horas = 7200 segundos)
        if estado.esta_expirado(TIMEOUT_INACTIVIDAD_SEGUNDOS):
            logger.info("Contexto conversacional expirado (> 2 horas) para usuario %s", usuario_id)
            estado.limpiar_foco_conversacional()
            cls.limpiar(usuario_id, session_key)
            with cls._lock:
                cls._context_misses += 1
            return None

        with cls._lock:
            cls._context_hits += 1

        estado.actualizar_actividad()
        cls.guardar(estado)
        return estado

    @classmethod
    def guardar(cls, estado: EstadoOperativoHaku) -> None:
        """Persiste el estado operativo en LocMemCache con TTL de 30 minutos."""
        if not estado or not estado.usuario_id or not estado.session_key:
            return

        cache = cls._obtener_cache()
        clave = cls._generar_clave(estado.usuario_id, estado.session_key)
        cache.set(clave, estado.to_dict(), timeout=TIMEOUT_INACTIVIDAD_SEGUNDOS)

    @classmethod
    def obtener_o_crear(
        cls,
        usuario_id: int,
        session_key: str,
        almacen_id: Optional[int] = None,
    ) -> EstadoOperativoHaku:
        """Obtiene el contexto existente o crea uno nuevo en RAM."""
        estado = cls.obtener(usuario_id, session_key)
        if estado is None:
            estado = EstadoOperativoHaku(
                usuario_id=usuario_id,
                session_key=session_key,
                almacen_id=almacen_id,
            )
            cls.guardar(estado)
        elif almacen_id and estado.almacen_id != almacen_id:
            estado.almacen_id = almacen_id
            cls.guardar(estado)
        return estado

    @classmethod
    def limpiar(cls, usuario_id: int, session_key: str) -> None:
        """Elimina el contexto operativo de la memoria."""
        cache = cls._obtener_cache()
        clave = cls._generar_clave(usuario_id, session_key)
        cache.delete(clave)

    @classmethod
    def limpiar_todo(cls) -> None:
        """Limpia todo el caché de contextos (útil para pruebas)."""
        cache = cls._obtener_cache()
        cache.clear()

    @classmethod
    def obtener_metricas(cls) -> Dict[str, int]:
        """Retorna las métricas internas de aciertos y fallos de contexto."""
        with cls._lock:
            return {
                "context_hits": cls._context_hits,
                "context_misses": cls._context_misses,
            }

    @classmethod
    def reset_metricas(cls) -> None:
        """Reinicia los contadores de métricas."""
        with cls._lock:
            cls._context_hits = 0
            cls._context_misses = 0


def conectar_senales_contexto():
    """Conecta el listener para limpiar el contexto en RAM cuando el usuario cierra sesión."""
    from django.contrib.auth.signals import user_logged_out

    def _on_user_logged_out(sender, request, user, **kwargs):
        if user and getattr(user, "id", None):
            session_key = getattr(request, "session", None) and getattr(request.session, "session_key", None)
            if session_key:
                HakuOperationalContext.limpiar(user.id, session_key)

    user_logged_out.connect(_on_user_logged_out, dispatch_uid="haku_logout_cleanup")

