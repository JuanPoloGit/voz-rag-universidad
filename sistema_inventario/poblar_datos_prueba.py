"""Script ejecutable para poblar datos de prueba en Inventario AudacIA.

Ejecuta el comando de gestión unificado 'poblar_datos_prueba'.
Soporta banderas como '--limpiar' para regenerar la base de datos desde cero.
"""

import os
import sys
import django

if __name__ == "__main__":
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "inventario_audacia.settings")
    django.setup()
    from django.core.management import call_command

    args = sys.argv[1:]
    call_command("poblar_datos_prueba", *args)
