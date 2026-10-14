"""
Comando administrativo para la restauración de respaldos con verificación obligatoria de checksum SHA-256.
Totalmente determinista, no bloqueante y libre de bucles o contención transaccional de SQLite.
"""

import gzip
import hashlib
import os
import shutil
import sys
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connections

from inventario.auditoria import registrar_auditoria


class Command(BaseCommand):
    help = "Restaura la base de datos a partir de un archivo de respaldo previo previa verificación de SHA-256."

    def add_arguments(self, parser):
        parser.add_argument(
            "archivo",
            type=str,
            help="Ruta absoluta o relativa al archivo de respaldo (.sqlite3.gz o .sqlite3).",
        )
        parser.add_argument(
            "--destino",
            type=str,
            default=None,
            help="Ruta al archivo SQLite de destino (por defecto la base de datos configurada en settings).",
        )
        parser.add_argument(
            "--no-input",
            action="store_true",
            help="Omite confirmaciones interactivas (obligatorio para pruebas automatizadas).",
        )
        parser.add_argument(
            "--skip-checksum",
            action="store_true",
            help="Omite la verificación del checksum SHA-256 (no recomendado en producción).",
        )

    def handle(self, *args, **options):
        archivo = options["archivo"]
        destino_custom = options.get("destino")
        no_input = options.get("no_input", False)
        skip_checksum = options.get("skip_checksum", False)

        if not os.path.exists(archivo):
            raise CommandError(f"El archivo de respaldo no existe: '{archivo}'")

        # 1. Verificación de integridad SHA-256
        sha_path = archivo + ".sha256"
        if not skip_checksum and os.path.exists(sha_path):
            with open(sha_path, "r", encoding="utf-8") as f:
                line = f.readline().strip()
                checksum_esperado = line.split()[0] if line else ""

            hasher = hashlib.sha256()
            with open(archivo, "rb") as f:
                while chunk := f.read(65536):
                    hasher.update(chunk)
            checksum_real = hasher.hexdigest()

            if checksum_real.lower() != checksum_esperado.lower():
                raise CommandError(
                    f"Fallo de integridad: Checksum SHA-256 no coincide.\n"
                    f"Esperado: {checksum_esperado}\n"
                    f"Calculado: {checksum_real}\n"
                    f"El archivo '{archivo}' puede estar corrupto o adulterado."
                )
            self.stdout.write(self.style.SUCCESS(f"Verificación SHA-256 exitosa: {checksum_real}"))
        elif not skip_checksum:
            self.stdout.write(
                self.style.WARNING(f"Advertencia: No se encontró archivo de checksum '{sha_path}'.")
            )

        # 2. Confirmación (solo si interactivo y no se especificó --no-input)
        if not no_input and sys.stdin.isatty():
            confirm = input(
                "ADVERTENCIA: Esta operación reemplazará los datos actuales por los del respaldo.\n"
                "¿Desea continuar? (s/N): "
            )
            if confirm.lower() != "s":
                self.stdout.write("Operación cancelada por el usuario.")
                return

        # 3. Determinar ruta de base de datos destino
        if destino_custom:
            destino = destino_custom
        else:
            destino = str(settings.DATABASES["default"]["NAME"])

        # 4. Cerrar todas las conexiones activas de Django para evitar cerrojos en SQLite
        for conn in connections.all():
            conn.close()

        # 5. Descompresión y reemplazo directo del archivo de base de datos (libre de deadlocks)
        temp_restaurado = None
        try:
            if archivo.endswith(".gz"):
                temp_restaurado = archivo + ".uncompressed_tmp"
                with gzip.open(archivo, "rb") as f_in, open(temp_restaurado, "wb") as f_out:
                    shutil.copyfileobj(f_in, f_out)
                origen_sqlite = temp_restaurado
            else:
                origen_sqlite = archivo

            # Reemplazar archivo destino
            if destino != ":memory:" and not destino.startswith("file:"):
                dir_dest = os.path.dirname(os.path.abspath(destino))
                if dir_dest:
                    os.makedirs(dir_dest, exist_ok=True)
                shutil.copy2(origen_sqlite, destino)

            # 6. Registrar en auditoría si es la base de datos principal
            if not destino_custom:
                try:
                    registrar_auditoria(
                        usuario=None,
                        accion="RESTAURAR_SISTEMA",
                        tipo_entidad="Sistema",
                        id_entidad="restore",
                        valores_nuevos={
                            "archivo": os.path.basename(archivo),
                        },
                        motivo=f"Restauración de base de datos desde {os.path.basename(archivo)}",
                        ip_origen="127.0.0.1",
                    )
                except Exception:
                    pass

            self.stdout.write(
                self.style.SUCCESS(
                    f"Base de datos restaurada exitosamente desde '{os.path.basename(archivo)}'."
                )
            )

        except Exception as exc:
            raise CommandError(f"Error restaurando la base de datos: {exc}")
        finally:
            if temp_restaurado and os.path.exists(temp_restaurado):
                try:
                    os.remove(temp_restaurado)
                except OSError:
                    pass
