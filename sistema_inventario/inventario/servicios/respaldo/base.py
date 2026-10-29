"""Clases base e interfaces para el servicio polimórfico de respaldos de base de datos."""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


class BackendRespaldo(ABC):
    """Interfaz abstracta para motores de respaldo y restauración de base de datos."""

    extension: str = ""
    tipo_motor: str = ""

    @abstractmethod
    def generar(
        self,
        ruta_destino: str,
        db_origen: Optional[str] = None,
        db_config: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Genera el archivo de respaldo en la ruta especificada."""
        pass

    @abstractmethod
    def restaurar(
        self,
        ruta_archivo: str,
        db_destino: Optional[str] = None,
        db_config: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Restaura la base de datos a partir del archivo de respaldo especificado."""
        pass
