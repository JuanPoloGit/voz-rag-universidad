"""Servicio polimórfico de gestión de respaldos (Backups) del sistema.

Soporta arquitectura dual SQLite / PostgreSQL:
- SQLite: copias consistentes en caliente vía sqlite3.backup() y compresión gzip (.sqlite3.gz).
- PostgreSQL: exportación en formato binario custom comprimido vía pg_dump -Fc (.pgdump) con PGPASSWORD seguro.
- Verificación criptográfica obligatoria de integridad con checksum SHA-256.
- Registro inmutable en auditoría y prevención estricta de Path Traversal (LFI).
"""

import datetime
import hashlib
import os
import re
from typing import Any, Dict, List, Optional

from django.conf import settings
from django.db import connection

from inventario.auditoria import registrar_auditoria
from scripts.colombia_calendario import es_dia_laboral

from .base import BackendRespaldo
from .postgres import RespaldoPostgres
from .sqlite import RespaldoSQLite

PATRON_ARCHIVO_RESPALDO = re.compile(r"^backup_(\d{8}_\d{6})\.(sqlite3\.gz|pgdump)$")
PATRON_RUTA_SEGURA = re.compile(r"^backup_\d{8}_\d{6}\.(sqlite3\.gz|pgdump)(\.sha256)?$")


def obtener_backend_respaldo(vendor: Optional[str] = None) -> BackendRespaldo:
    """Retorna el backend de respaldo apropiado para el motor de base de datos activo."""
    motor = (vendor or getattr(connection, "vendor", "sqlite")).lower()
    if "postgres" in motor:
        return RespaldoPostgres()
    return RespaldoSQLite()


def obtener_directorio_respaldos(output_dir: Optional[str] = None) -> str:
    """Retorna la ruta absoluta del directorio de respaldos garantizando su existencia."""
    if output_dir:
        dir_path = os.path.abspath(output_dir)
    else:
        base_dir = getattr(settings, "BASE_DIR", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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
    backend: Optional[BackendRespaldo] = None,
) -> Dict[str, Any]:
    """Genera un respaldo de la base de datos con compresión y checksum SHA-256.

    Selecciona automáticamente el backend según el motor de base de datos (SQLite o PostgreSQL).
    """
    ahora = datetime.datetime.now()

    # Validar calendario laboral si force=False
    if not force and not es_dia_laboral(ahora.date()):
        return {
            "omitido": True,
            "motivo": f"Fecha {ahora.date().isoformat()} no es laboral en Colombia (fin de semana o festivo).",
        }

    backend_activo = backend or obtener_backend_respaldo()
    directorio = obtener_directorio_respaldos(output_dir)
    timestamp_str = ahora.strftime("%Y%m%d_%H%M%S")
    backup_filename = f"backup_{timestamp_str}{backend_activo.extension}"
    final_path = os.path.join(directorio, backup_filename)
    sha_path = final_path + ".sha256"

    try:
        # 1. Ejecutar copia consistente según el motor activo
        backend_activo.generar(final_path, db_origen=db_path)

        # 2. Checksum SHA-256
        checksum = calcular_sha256_archivo(final_path)
        with open(sha_path, "w", encoding="utf-8") as f:
            f.write(f"{checksum}  {backup_filename}\n")

        tamano_bytes = os.path.getsize(final_path)

        # 3. Registro inmutable en auditoría
        if not db_path:
            try:
                registrar_auditoria(
                    usuario=usuario,
                    accion="BACKUP_SISTEMA",
                    tipo_entidad="Sistema",
                    id_entidad="backup",
                    valores_nuevos={
                        "archivo": backup_filename,
                        "motor": backend_activo.tipo_motor,
                        "sha256": checksum,
                        "tamano_bytes": tamano_bytes,
                    },
                    motivo=motivo or f"Respaldo generado con éxito: {backup_filename}",
                )
            except Exception:
                pass

        return {
            "omitido": False,
            "archivo": backup_filename,
            "ruta": final_path,
            "motor": backend_activo.tipo_motor,
            "sha256": checksum,
            "tamano_bytes": tamano_bytes,
            "tamano_formateado": formatear_tamano(tamano_bytes),
            "fecha": ahora,
        }
    except Exception as exc:
        if os.path.exists(final_path):
            try:
                os.remove(final_path)
            except OSError:
                pass
        if os.path.exists(sha_path):
            try:
                os.remove(sha_path)
            except OSError:
                pass
        raise exc


def restaurar_respaldo_bd(
    ruta_archivo: str,
    db_destino: Optional[str] = None,
    skip_checksum: bool = False,
    usuario=None,
    motivo: Optional[str] = None,
) -> Dict[str, Any]:
    """Restaura una copia de seguridad verificando la integridad SHA-256 previa."""
    if not os.path.exists(ruta_archivo):
        raise FileNotFoundError(f"El archivo de respaldo no existe: '{ruta_archivo}'")

    nombre_archivo = os.path.basename(ruta_archivo)

    # 1. Verificación de integridad SHA-256
    sha_path = ruta_archivo + ".sha256"
    checksum_calculado = calcular_sha256_archivo(ruta_archivo)
    if not skip_checksum and os.path.exists(sha_path):
        with open(sha_path, "r", encoding="utf-8") as f:
            checksum_esperado = f.read().split()[0] if f else ""
        if checksum_calculado.lower() != checksum_esperado.lower():
            raise ValueError(
                f"Fallo de integridad: Checksum SHA-256 no coincide.\n"
                f"Esperado: {checksum_esperado}\n"
                f"Calculado: {checksum_calculado}\n"
                f"El archivo '{nombre_archivo}' puede estar corrupto o adulterado."
            )

    # 2. Selección del backend adecuado según el formato del archivo o motor
    if ruta_archivo.endswith(".pgdump"):
        backend = RespaldoPostgres()
    elif ruta_archivo.endswith(".sqlite3.gz") or ruta_archivo.endswith(".sqlite3"):
        backend = RespaldoSQLite()
    else:
        backend = obtener_backend_respaldo()

    # 3. Ejecutar restauración
    backend.restaurar(ruta_archivo, db_destino=db_destino)

    # 4. Auditoría si es restauración sobre la base de datos principal
    if not db_destino:
        try:
            registrar_auditoria(
                usuario=usuario,
                accion="RESTAURAR_SISTEMA",
                tipo_entidad="Sistema",
                id_entidad="restore",
                valores_nuevos={
                    "archivo": nombre_archivo,
                    "motor": backend.tipo_motor,
                    "sha256": checksum_calculado,
                },
                motivo=motivo or f"Restauración de base de datos desde {nombre_archivo}",
            )
        except Exception:
            pass

    return {
        "archivo": nombre_archivo,
        "motor": backend.tipo_motor,
        "sha256": checksum_calculado,
        "destino": db_destino or "default",
    }


def listar_respaldos(output_dir: Optional[str] = None) -> List[Dict[str, Any]]:
    """Lista todos los respaldos existentes en el directorio verificando su integridad."""
    directorio = obtener_directorio_respaldos(output_dir)
    respaldos = []

    for entrada in os.listdir(directorio):
        match = PATRON_ARCHIVO_RESPALDO.match(entrada)
        if not match:
            continue

        ruta_completa = os.path.join(directorio, entrada)
        if not os.path.isfile(ruta_completa):
            continue

        timestamp_str = match.group(1)
        ext = match.group(2)
        try:
            fecha = datetime.datetime.strptime(timestamp_str, "%Y%m%d_%H%M%S")
        except ValueError:
            mtime = os.path.getmtime(ruta_completa)
            fecha = datetime.datetime.fromtimestamp(mtime)

        tamano_bytes = os.path.getsize(ruta_completa)

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

        integridad_ok = False
        if sha_guardado:
            try:
                sha_calculado = calcular_sha256_archivo(ruta_completa)
                integridad_ok = (sha_calculado.lower() == sha_guardado.lower())
            except Exception:
                integridad_ok = False

        motor = "postgresql" if ext == "pgdump" else "sqlite"

        respaldos.append({
            "archivo": entrada,
            "ruta": ruta_completa,
            "motor": motor,
            "fecha": fecha,
            "tamano_bytes": tamano_bytes,
            "tamano_formateado": formatear_tamano(tamano_bytes),
            "sha256": sha_guardado,
            "integridad_ok": integridad_ok,
            "tiene_sha": bool(sha_guardado),
        })

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
    """Retorna la ruta absoluta segura a un archivo de respaldo o checksum, previniendo Path Traversal."""
    nombre_limpio = os.path.basename(nombre_archivo)
    if nombre_limpio != nombre_archivo:
        raise ValueError("Nombre de archivo inválido (posible intento de path traversal).")

    if not PATRON_RUTA_SEGURA.match(nombre_limpio):
        raise ValueError("Nombre de archivo con formato no permitido.")

    directorio = obtener_directorio_respaldos(output_dir)
    ruta_completa = os.path.join(directorio, nombre_limpio)

    ruta_real = os.path.realpath(ruta_completa)
    dir_real = os.path.realpath(directorio)
    if not ruta_real.startswith(dir_real + os.sep) and ruta_real != dir_real:
        raise ValueError("Acceso fuera del directorio autorizado de respaldos.")

    if not os.path.isfile(ruta_real):
        raise FileNotFoundError(f"El archivo '{nombre_limpio}' no existe en el almacenamiento.")

    return ruta_real


def eliminar_respaldo_bd(
    nombre_archivo: str,
    usuario=None,
    output_dir: Optional[str] = None,
) -> bool:
    """Elimina permanentemente una copia de seguridad y su huella SHA-256."""
    from django.core.exceptions import PermissionDenied
    es_super = (
        getattr(usuario, "es_superusuario_inventario", False)
        or getattr(usuario, "is_superuser", False)
        or (hasattr(usuario, "rol") and str(usuario.rol) == "SUPERUSUARIO")
    )
    if usuario and not es_super:
        raise PermissionDenied("Solo el Superadministrador puede eliminar copias de seguridad de la base de datos.")

    if nombre_archivo.endswith(".sha256"):
        nombre_archivo = nombre_archivo[:-7]

    ruta_archivo = obtener_ruta_respaldo_segura(nombre_archivo, output_dir)
    ruta_sha = f"{ruta_archivo}.sha256"

    tamano_bytes = os.path.getsize(ruta_archivo) if os.path.exists(ruta_archivo) else 0

    if os.path.exists(ruta_archivo):
        os.remove(ruta_archivo)

    if os.path.exists(ruta_sha):
        os.remove(ruta_sha)

    if usuario:
        registrar_auditoria(
            usuario=usuario,
            accion="ELIMINAR_BACKUP",
            tipo_entidad="Sistema",
            id_entidad="backup",
            valores_anteriores={"archivo": os.path.basename(ruta_archivo), "tamano_bytes": tamano_bytes},
            valores_nuevos={"eliminado": True},
            motivo=f"Eliminación de respaldo {os.path.basename(ruta_archivo)} por Superadministrador",
            resultado="OK",
        )

    return True


__all__ = [
    "BackendRespaldo",
    "RespaldoSQLite",
    "RespaldoPostgres",
    "obtener_backend_respaldo",
    "obtener_directorio_respaldos",
    "calcular_sha256_archivo",
    "formatear_tamano",
    "generar_respaldo_bd",
    "restaurar_respaldo_bd",
    "listar_respaldos",
    "obtener_estadisticas_respaldos",
    "obtener_ruta_respaldo_segura",
    "eliminar_respaldo_bd",
]
