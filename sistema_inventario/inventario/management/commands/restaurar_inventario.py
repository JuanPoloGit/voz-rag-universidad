"""Comando administrativo para la restauración de respaldos con verificación obligatoria de checksum SHA-256.

Soporta motores duales SQLite y PostgreSQL mediante la capa polimórfica de inventario.servicios.respaldo.
"""

import os
import sys
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from inventario.servicios.respaldo import restaurar_respaldo_bd


class Command(BaseCommand):
    help = "Restaura la base de datos a partir de un archivo de respaldo previo previa verificación de SHA-256."

    def add_arguments(self, parser):
        parser.add_argument(
            "archivo",
            type=str,
            help="Ruta absoluta o relativa al archivo de respaldo (.sqlite3.gz, .sqlite3 o .pgdump).",
        )
        parser.add_argument(
            "--destino",
            type=str,
            default=None,
            help="Ruta o nombre de la base de datos de destino (por defecto la configurada en settings).",
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

        # 1. Confirmación interactiva (solo si no se especificó --no-input)
        if not no_input and sys.stdin.isatty():
            confirm = input(
                "ADVERTENCIA: Esta operación reemplazará los datos actuales por los del respaldo.\n"
                "¿Desea continuar? (s/N): "
            )
            if confirm.lower() != "s":
                self.stdout.write("Operación cancelada por el usuario.")
                return

        # 2. Ejecución delegada a servicio polimórfico
        try:
            res = restaurar_respaldo_bd(
                ruta_archivo=archivo,
                db_destino=destino_custom,
                skip_checksum=skip_checksum,
                usuario=None,
            )
            if not skip_checksum:
                self.stdout.write(self.style.SUCCESS(f"Verificación SHA-256 exitosa: {res['sha256']}"))
            self.stdout.write(
                self.style.SUCCESS(
                    f"Base de datos restaurada exitosamente desde '{res['archivo']}'."
                )
            )
        except Exception as exc:
            raise CommandError(str(exc))
