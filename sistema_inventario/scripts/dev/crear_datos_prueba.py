"""Script auxiliar de desarrollo para inicializar datos de prueba en Inventario AudacIA.

Ejecuta el comando de gestión unificado 'poblar_datos_prueba'.
"""

import os
import sys

# Agregar la raíz de sistema_inventario al PYTHONPATH
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import django

if __name__ == "__main__":
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "inventario_audacia.settings")
    django.setup()
    from django.core.management import call_command

    args = sys.argv[1:]
    call_command("poblar_datos_prueba", *args)
