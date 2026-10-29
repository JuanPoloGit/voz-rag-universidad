"""Backend de respaldos para SQLite usando sqlite3.backup() y compresión gzip."""

import gzip
import os
import shutil
import sqlite3
from typing import Any, Dict, Optional

from django.conf import settings
from django.db import connection, connections

from .base import BackendRespaldo


class RespaldoSQLite(BackendRespaldo):
    """Backend de respaldos para bases de datos SQLite."""

    extension: str = ".sqlite3.gz"
    tipo_motor: str = "sqlite"

    def generar(
        self,
        ruta_destino: str,
        db_origen: Optional[str] = None,
        db_config: Optional[Dict[str, Any]] = None,
    ) -> None:
        temp_dir = os.path.dirname(ruta_destino)
        temp_uncompressed = os.path.join(temp_dir, f"tmp_{os.path.basename(ruta_destino)}.sqlite3")

        try:
            if db_origen and os.path.exists(db_origen):
                shutil.copy2(db_origen, temp_uncompressed)
            else:
                db_name = (db_config or {}).get("NAME") or settings.DATABASES.get("default", {}).get("NAME")
                if isinstance(db_name, str) and os.path.exists(db_name) and not db_name.startswith(":memory:"):
                    src_conn = sqlite3.connect(f"file:{os.path.abspath(db_name)}?mode=ro", uri=True)
                    dest_conn = sqlite3.connect(temp_uncompressed)
                    src_conn.backup(dest_conn)
                    src_conn.close()
                    dest_conn.close()
                else:
                    connection.ensure_connection()
                    dest_conn = sqlite3.connect(temp_uncompressed)
                    connection.connection.backup(dest_conn)
                    dest_conn.close()

            # Compresión GZIP directa
            with open(temp_uncompressed, "rb") as f_in, gzip.open(ruta_destino, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
        finally:
            if os.path.exists(temp_uncompressed):
                try:
                    os.remove(temp_uncompressed)
                except OSError:
                    pass

    def restaurar(
        self,
        ruta_archivo: str,
        db_destino: Optional[str] = None,
        db_config: Optional[Dict[str, Any]] = None,
    ) -> None:
        # Cerrar conexiones activas para liberar cerrojos en SQLite
        for conn in connections.all():
            conn.close()

        destino = db_destino or (db_config or {}).get("NAME") or str(settings.DATABASES["default"]["NAME"])

        temp_restaurado = None
        try:
            if ruta_archivo.endswith(".gz"):
                temp_restaurado = ruta_archivo + ".uncompressed_tmp"
                with gzip.open(ruta_archivo, "rb") as f_in, open(temp_restaurado, "wb") as f_out:
                    shutil.copyfileobj(f_in, f_out)
                origen_sqlite = temp_restaurado
            else:
                origen_sqlite = ruta_archivo

            if destino != ":memory:" and not str(destino).startswith("file:"):
                dir_dest = os.path.dirname(os.path.abspath(destino))
                if dir_dest:
                    os.makedirs(dir_dest, exist_ok=True)
                shutil.copy2(origen_sqlite, destino)
        finally:
            if temp_restaurado and os.path.exists(temp_restaurado):
                try:
                    os.remove(temp_restaurado)
                except OSError:
                    pass
