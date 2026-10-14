"""
Comando administrativo para la generación de respaldos automáticos y manuales (Política A - Colombia).
Crea archivos comprimidos (.sqlite3.gz / .sql.gz) con verificación de integridad SHA-256.
Totalmente determinista, no bloqueante y libre de bucles o contenciones de base de datos.
"""

import datetime
import gzip
import hashlib
import os
import re
import shutil
import sqlite3
from typing import List, Tuple
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, connections

from inventario.auditoria import registrar_auditoria
from scripts.colombia_calendario import es_dia_laboral, obtener_ultimos_dias_laborales


class Command(BaseCommand):
    help = "Genera un respaldo de la base de datos con compresión gzip y checksum SHA-256 (Política A)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--output-dir",
            type=str,
            default=None,
            help="Directorio de destino para los respaldos (por defecto: BASE_DIR/backups).",
        )
        parser.add_argument(
            "--db-path",
            type=str,
            default=None,
            help="Ruta específica a la base de datos SQLite a respaldar (opcional; por defecto la configurada en Django).",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Fuerza la ejecución incluso fuera del calendario laboral (útil para pruebas o manual).",
        )
        parser.add_argument(
            "--no-input",
            action="store_true",
            help="Ejecución no interactiva y desatendida.",
        )

    def handle(self, *args, **options):
        ahora = datetime.datetime.now()
        force = options.get("force", False)

        # 1. Validar calendario laboral colombiano a menos que se use --force
        if not force and not es_dia_laboral(ahora.date()):
            self.stdout.write(
                self.style.WARNING(
                    f"Fecha {ahora.date().isoformat()} es no laboral en Colombia (fin de semana o festivo). "
                    "Respaldo omitido según Política A (use --force para forzar)."
                )
            )
            return

        # 2. Configurar directorio de respaldos
        base_dir = getattr(settings, "BASE_DIR", os.path.dirname(os.path.abspath(__file__)))
        output_dir = options.get("output_dir") or os.path.join(base_dir, "backups")
        os.makedirs(output_dir, exist_ok=True)

        timestamp_str = ahora.strftime("%Y%m%d_%H%M%S")
        temp_sqlite = os.path.join(output_dir, f"temp_{timestamp_str}.sqlite3")
        backup_filename = f"backup_{timestamp_str}.sqlite3.gz"
        final_gz_path = os.path.join(output_dir, backup_filename)
        sha_path = final_gz_path + ".sha256"

        try:
            # 3. Copia de respaldo no bloqueante
            db_path_custom = options.get("db_path")
            if db_path_custom and os.path.exists(db_path_custom):
                shutil.copy2(db_path_custom, temp_sqlite)
            else:
                # Usar SQLite Online Backup sobre la conexión Django
                connection.ensure_connection()
                dest_conn = sqlite3.connect(temp_sqlite)
                connection.connection.backup(dest_conn)
                dest_conn.close()

            # 4. Compresión GZIP
            with open(temp_sqlite, "rb") as f_in, gzip.open(final_gz_path, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)

            # Eliminar temporal sin comprimir
            if os.path.exists(temp_sqlite):
                os.remove(temp_sqlite)

            # 5. Cálculo y almacenamiento del Checksum SHA-256
            hasher = hashlib.sha256()
            with open(final_gz_path, "rb") as f:
                while chunk := f.read(65536):
                    hasher.update(chunk)
            checksum = hasher.hexdigest()

            with open(sha_path, "w", encoding="utf-8") as f:
                f.write(f"{checksum}  {backup_filename}\n")

            tamano_bytes = os.path.getsize(final_gz_path)

            # 6. Registrar en auditoría inmutable
            if not db_path_custom:
                try:
                    registrar_auditoria(
                        usuario=None,
                        accion="BACKUP_SISTEMA",
                        tipo_entidad="Sistema",
                        id_entidad="backup",
                        valores_nuevos={
                            "archivo": backup_filename,
                            "sha256": checksum,
                            "tamano_bytes": tamano_bytes,
                        },
                        motivo=f"Respaldo generado con éxito: {backup_filename}",
                        ip_origen="127.0.0.1",
                    )
                except Exception:
                    pass

            # 7. Aplicar Política A de retención (limpieza de históricos fuera de ventana)
            self._aplicar_politica_retencion(output_dir, ahora.date())

            self.stdout.write(
                self.style.SUCCESS(
                    f"Respaldo completado: {final_gz_path} ({tamano_bytes} bytes)\n"
                    f"SHA-256: {checksum}"
                )
            )

        except Exception as exc:
            if os.path.exists(temp_sqlite):
                os.remove(temp_sqlite)
            raise CommandError(f"Error generando el respaldo: {exc}")

    def _aplicar_politica_retencion(self, output_dir: str, fecha_referencia: datetime.date):
        """
        Aplica la Política A de retención sobre los archivos en output_dir:
        - 2 horas: respaldos recientes de los últimos 2 días laborales.
        - Diarios: 1 respaldo por día de los últimos 30 días laborales.
        - Semanales: 1 respaldo por semana de las últimas 12 semanas.
        Purga archivos obsoletos garantizando que nunca se elimine el más reciente.
        """
        patron = re.compile(r"^backup_(\d{8})_(\d{6})\.sqlite3\.gz$")
        archivos_validos: List[Tuple[datetime.datetime, str]] = []

        for nombre in os.listdir(output_dir):
            match = patron.match(nombre)
            if match:
                fecha_str, hora_str = match.groups()
                try:
                    dt = datetime.datetime.strptime(f"{fecha_str}_{hora_str}", "%Y%m%d_%H%M%S")
                    archivos_validos.append((dt, nombre))
                except ValueError:
                    continue

        if len(archivos_validos) <= 1:
            return

        archivos_validos.sort(key=lambda x: x[0], reverse=True)

        dias_laborales_30 = set(obtener_ultimos_dias_laborales(fecha_referencia, 30))
        dias_laborales_2 = set(obtener_ultimos_dias_laborales(fecha_referencia, 2))
        limite_12_semanas = fecha_referencia - datetime.timedelta(weeks=12)

        conservar_archivos = set()
        conservar_archivos.add(archivos_validos[0][1])

        diarios_registrados = set()
        semanas_registradas = set()

        for dt, nombre in archivos_validos:
            f_date = dt.date()

            # Regla 1: Cada 2 horas de los últimos 2 días laborales
            if f_date in dias_laborales_2:
                conservar_archivos.add(nombre)
                continue

            # Regla 2: 1 diario por cada uno de los últimos 30 días laborales
            if f_date in dias_laborales_30:
                if f_date not in diarios_registrados:
                    diarios_registrados.add(f_date)
                    conservar_archivos.add(nombre)
                    continue

            # Regla 3: 1 semanal hasta 12 semanas
            if f_date >= limite_12_semanas:
                year_week = dt.isocalendar()[:2]
                if year_week not in semanas_registradas:
                    semanas_registradas.add(year_week)
                    conservar_archivos.add(nombre)
                    continue

        # Eliminar archivos que no clasificaron en la política
        for dt, nombre in archivos_validos:
            if nombre not in conservar_archivos:
                gz_path = os.path.join(output_dir, nombre)
                sha_path = gz_path + ".sha256"
                try:
                    if os.path.exists(gz_path):
                        os.remove(gz_path)
                    if os.path.exists(sha_path):
                        os.remove(sha_path)
                except OSError:
                    pass
