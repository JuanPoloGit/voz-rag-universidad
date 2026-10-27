"""
Servicio centralizado de gestión de respaldos (Backups) del sistema.
Política A - Colombia: Compresión GZIP, Checksum SHA-256, Auditoría inmutable y protección contra Path Traversal.
"""

import datetime
import gzip
import hashlib
import os
import re
import shutil
import sqlite3
from typing import Any, Dict, List, Optional

from django.conf import settings
from django.db import connection

from inventario.auditoria import registrar_auditoria
from scripts.colombia_calendario import es_dia_laboral


def obtener_directorio_respaldos(output_dir: Optional[str] = None) -> str:
    """Retorna la ruta absoluta del directorio de respaldos garantizando su existencia."""
    if output_dir:
        dir_path = os.path.abspath(output_dir)
    else:
        base_dir = getattr(settings, "BASE_DIR", os.path.dirname(os.path.abspath(__file__)))
        dir_path = os.path.join(base_dir, "backups")
    os.makedirs(dir_path, exist_ok=True)
    return dir_path


def calcular_sha256_archivo(ruta_archivo: str) -> str:
    """Calcula el hash SHA-256 de un archivo en bloques de 64KB."""
    hasher = hashlib.sha256()
    with open(ruta_archivo, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def formatear_tamano(tamano_bytes: int) -> str:
    """Convierte bytes a formato legible (KB, MB, GB)."""
    if tamano_bytes < 1024:
        return f"{tamano_bytes} B"
    elif tamano_bytes < 1024 * 1024:
        return f"{tamano_bytes / 1024:.1f} KB"
    elif tamano_bytes < 1024 * 1024 * 1024:
        return f"{tamano_bytes / (1024 * 1024):.2f} MB"
    return f"{tamano_bytes / (1024 * 1024 * 1024):.2f} GB"


def generar_respaldo_bd(
    usuario=None,
    force: bool = True,
    output_dir: Optional[str] = None,
    db_path: Optional[str] = None,
    motivo: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Genera un respaldo de la base de datos SQLite con compresión gzip y checksum SHA-256.

    Args:
        usuario: Instancia de UsuarioAudacIA que ejecuta la acción (o None para CLI/automático).
        force: Si True, ignora validación de días laborales.
        output_dir: Carpeta destino personalizada (opcional).
        db_path: Ruta a la base de datos a respaldar (opcional; por defecto la conexión activa).
        motivo: Motivo de auditoría.

    Returns:
        Dict con metadatos del respaldo o motivo de omisión.
    """
    ahora = datetime.datetime.now()

    # Validar calendario laboral si force=False
    if not force and not es_dia_laboral(ahora.date()):
        return {
            "omitido": True,
            "motivo": f"Fecha {ahora.date().isoformat()} no es laboral en Colombia (fin de semana o festivo).",
        }

    directorio = obtener_directorio_respaldos(output_dir)
    timestamp_str = ahora.strftime("%Y%m%d_%H%M%S")
    temp_sqlite = os.path.join(directorio, f"temp_{timestamp_str}.sqlite3")
    backup_filename = f"backup_{timestamp_str}.sqlite3.gz"
    final_gz_path = os.path.join(directorio, backup_filename)
    sha_path = final_gz_path + ".sha256"

    try:
        # 1. Copia consistente de base de datos
        if db_path and os.path.exists(db_path):
            shutil.copy2(db_path, temp_sqlite)
        else:
            db_name = settings.DATABASES.get("default", {}).get("NAME")
            if isinstance(db_name, str) and os.path.exists(db_name) and not db_name.startswith(":memory:"):
                src_conn = sqlite3.connect(f"file:{os.path.abspath(db_name)}?mode=ro", uri=True)
                dest_conn = sqlite3.connect(temp_sqlite)
                src_conn.backup(dest_conn)
                src_conn.close()
                dest_conn.close()
            else:
                connection.ensure_connection()
                dest_conn = sqlite3.connect(temp_sqlite)
                connection.connection.backup(dest_conn)
                dest_conn.close()

        # 2. Compresión GZIP
        with open(temp_sqlite, "rb") as f_in, gzip.open(final_gz_path, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)

        # 3. Eliminar temporal sin comprimir
        if os.path.exists(temp_sqlite):
            os.remove(temp_sqlite)

        # 4. Checksum SHA-256
        checksum = calcular_sha256_archivo(final_gz_path)
        with open(sha_path, "w", encoding="utf-8") as f:
            f.write(f"{checksum}  {backup_filename}\n")

        tamano_bytes = os.path.getsize(final_gz_path)

        # 5. Registro inmutable en auditoría
        if not db_path:
            try:
                registrar_auditoria(
                    usuario=usuario,
                    accion="BACKUP_SISTEMA",
                    tipo_entidad="Sistema",
                    id_entidad="backup",
                    valores_nuevos={
                        "archivo": backup_filename,
                        "sha256": checksum,
                        "tamano_bytes": tamano_bytes,
                    },
                    motivo=motivo or f"Respaldo generado con éxito: {backup_filename}",
                )
            except Exception:
                pass  # Si la auditoría falla por contexto de prueba, no abortar

        return {
            "omitido": False,
            "archivo": backup_filename,
            "ruta": final_gz_path,
            "sha256": checksum,
            "tamano_bytes": tamano_bytes,
            "tamano_formateado": formatear_tamano(tamano_bytes),
            "fecha": ahora,
        }
    except Exception as exc:
        if os.path.exists(temp_sqlite):
            os.remove(temp_sqlite)
        if os.path.exists(final_gz_path):
            os.remove(final_gz_path)
        if os.path.exists(sha_path):
            os.remove(sha_path)
        raise exc


def listar_respaldos(output_dir: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Lista todos los respaldos existentes en el directorio, verificando su integridad.

    Returns:
        Lista de diccionarios ordenados por fecha descendente.
    """
    directorio = obtener_directorio_respaldos(output_dir)
    respaldos = []

    patron_archivo = re.compile(r"^backup_(\d{8}_\d{6})\.sqlite3\.gz$")

    for entrada in os.listdir(directorio):
        match = patron_archivo.match(entrada)
        if not match:
            continue

        ruta_completa = os.path.join(directorio, entrada)
        if not os.path.isfile(ruta_completa):
            continue

        timestamp_str = match.group(1)
        try:
            fecha = datetime.datetime.strptime(timestamp_str, "%Y%m%d_%H%M%S")
        except ValueError:
            mtime = os.path.getmtime(ruta_completa)
            fecha = datetime.datetime.fromtimestamp(mtime)

        tamano_bytes = os.path.getsize(ruta_completa)

        # Leer o verificar checksum
        ruta_sha = ruta_completa + ".sha256"
        sha_guardado = ""
        if os.path.exists(ruta_sha):
            try:
                with open(ruta_sha, "r", encoding="utf-8") as f:
                    contenido = f.read().strip()
                    if contenido:
                        sha_guardado = contenido.split()[0]
            except Exception:
                sha_guardado = ""

        # Verificar integridad (si existe sha guardado)
        integridad_ok = False
        if sha_guardado:
            try:
                sha_calculado = calcular_sha256_archivo(ruta_completa)
                integridad_ok = (sha_calculado.lower() == sha_guardado.lower())
            except Exception:
                integridad_ok = False

        respaldos.append({
            "archivo": entrada,
            "ruta": ruta_completa,
            "fecha": fecha,
            "tamano_bytes": tamano_bytes,
            "tamano_formateado": formatear_tamano(tamano_bytes),
            "sha256": sha_guardado,
            "integridad_ok": integridad_ok,
            "tiene_sha": bool(sha_guardado),
        })

    # Ordenar por fecha más reciente primero
    respaldos.sort(key=lambda r: r["fecha"], reverse=True)
    return respaldos


def obtener_estadisticas_respaldos(output_dir: Optional[str] = None) -> Dict[str, Any]:
    """Calcula estadísticas generales de las copias de seguridad existentes."""
    respaldos = listar_respaldos(output_dir)
    total_copias = len(respaldos)
    tamano_total_bytes = sum(r["tamano_bytes"] for r in respaldos)
    ultimo_respaldo = respaldos[0] if respaldos else None

    return {
        "total_copias": total_copias,
        "tamano_total_bytes": tamano_total_bytes,
        "tamano_total_formateado": formatear_tamano(tamano_total_bytes),
        "ultimo_respaldo": ultimo_respaldo,
    }


def obtener_ruta_respaldo_segura(nombre_archivo: str, output_dir: Optional[str] = None) -> str:
    """
    Retorna la ruta absoluta segura a un archivo de respaldo o checksum,
    previniendo ataques de Path Traversal (LFI / Directory Traversal).

    Lanza ValueError o FileNotFoundError si el archivo no es válido o no existe.
    """
    # 1. Sanitizar nombre extrayendo solo el basename
    nombre_limpio = os.path.basename(nombre_archivo)
    if nombre_limpio != nombre_archivo:
        raise ValueError("Nombre de archivo inválido (posible intento de path traversal).")

    # 2. Validar que cumpla con el formato estricto permitido
    patron_valido = re.compile(r"^backup_\d{8}_\d{6}\.sqlite3\.gz(\.sha256)?$")
    if not patron_valido.match(nombre_limpio):
        raise ValueError("Nombre de archivo con formato no permitido.")

    directorio = obtener_directorio_respaldos(output_dir)
    ruta_completa = os.path.join(directorio, nombre_limpio)

    # 3. Comprobar que resuelva dentro del directorio de respaldos
    ruta_real = os.path.realpath(ruta_completa)
    dir_real = os.path.realpath(directorio)
    if not ruta_real.startswith(dir_real + os.sep) and ruta_real != dir_real:
        raise ValueError("Acceso fuera del directorio autorizado de respaldos.")

    if not os.path.isfile(ruta_real):
        raise FileNotFoundError(f"El archivo '{nombre_limpio}' no existe en el almacenamiento.")

    return ruta_real
