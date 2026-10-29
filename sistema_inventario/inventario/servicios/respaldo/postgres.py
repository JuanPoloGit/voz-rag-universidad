"""Backend de respaldos para PostgreSQL usando pg_dump -Fc y pg_restore."""

import os
import subprocess
from typing import Any, Dict, Optional

from django.conf import settings
from django.db import connections

from .base import BackendRespaldo


class RespaldoPostgres(BackendRespaldo):
    """Backend de respaldos para PostgreSQL en formato Custom comprimido (-Fc)."""

    extension: str = ".pgdump"
    tipo_motor: str = "postgresql"

    def _obtener_config_conexion(self, db_config: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
        cfg = db_config or settings.DATABASES.get("default", {})
        return {
            "name": str(cfg.get("NAME", "")),
            "user": str(cfg.get("USER", "")),
            "password": str(cfg.get("PASSWORD", "")),
            "host": str(cfg.get("HOST", "localhost") or "localhost"),
            "port": str(cfg.get("PORT", "5432") or "5432"),
        }

    def generar(
        self,
        ruta_destino: str,
        db_origen: Optional[str] = None,
        db_config: Optional[Dict[str, Any]] = None,
    ) -> None:
        cfg = self._obtener_config_conexion(db_config)
        nombre_bd = db_origen or cfg["name"]

        cmd = [
            "pg_dump",
            "-Fc",
            "-h", cfg["host"],
            "-p", cfg["port"],
            "-U", cfg["user"],
            "-f", ruta_destino,
            nombre_bd,
        ]

        # SEGURIDAD ESTRICTA: La contraseña NUNCA se expone en la línea de comandos.
        # Se envía exclusivamente a través de la variable de entorno PGPASSWORD.
        env = os.environ.copy()
        if cfg["password"]:
            env["PGPASSWORD"] = cfg["password"]

        try:
            subprocess.run(
                cmd,
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )
        except FileNotFoundError:
            raise RuntimeError(
                "El comando 'pg_dump' no fue encontrado en el sistema. "
                "Verifica que las herramientas de cliente de PostgreSQL estén instaladas y en el PATH."
            )
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.strip() if exc.stderr else "Error desconocido durante la ejecución de pg_dump"
            raise RuntimeError(f"Error al generar respaldo de PostgreSQL: {stderr}") from exc

    def restaurar(
        self,
        ruta_archivo: str,
        db_destino: Optional[str] = None,
        db_config: Optional[Dict[str, Any]] = None,
    ) -> None:
        # Cerrar conexiones activas de Django
        for conn in connections.all():
            conn.close()

        cfg = self._obtener_config_conexion(db_config)
        nombre_bd = db_destino or cfg["name"]

        cmd = [
            "pg_restore",
            "--clean",
            "--if-exists",
            "--no-owner",
            "-h", cfg["host"],
            "-p", cfg["port"],
            "-U", cfg["user"],
            "-d", nombre_bd,
            ruta_archivo,
        ]

        env = os.environ.copy()
        if cfg["password"]:
            env["PGPASSWORD"] = cfg["password"]

        try:
            subprocess.run(
                cmd,
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )
        except FileNotFoundError:
            raise RuntimeError(
                "El comando 'pg_restore' no fue encontrado en el sistema. "
                "Verifica que las herramientas de cliente de PostgreSQL estén instaladas y en el PATH."
            )
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.strip() if exc.stderr else "Error desconocido durante la ejecución de pg_restore"
            raise RuntimeError(f"Error al restaurar respaldo de PostgreSQL: {stderr}") from exc
