"""
Script auxiliar para inicializar datos de prueba en la base de datos de desarrollo.
Crea almacenes y usuarios con los tres roles: Superusuario, Administrador y Usuario común.
"""

import os
import sys

# Agregar la raíz de sistema_inventario al PYTHONPATH
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "inventario_audacia.settings")
django.setup()

from cuentas.models import UsuarioAudacIA
from inventario.models import Almacen


def poblar_datos_prueba():
    print("[INFO] Inicializando datos de prueba en Inventario AudacIA...")

    # 1. Crear Almacenes
    alm_norte, _ = Almacen.objects.get_or_create(
        nombre="Almacén Central Norte",
        defaults={"descripcion": "Sede principal norte para almacenamiento y distribución."},
    )
    alm_sur, _ = Almacen.objects.get_or_create(
        nombre="Almacén Satélite Sur",
        defaults={"descripcion": "Bodega regional sur para despacho rápido."},
    )
    print(f" [OK] Almacenes configurados: '{alm_norte.nombre}' y '{alm_sur.nombre}'")

    # 2. Superusuario
    if not UsuarioAudacIA.objects.filter(username="superadmin").exists():
        UsuarioAudacIA.objects.create_superuser(
            username="superadmin",
            password="SuperAdmin2026!",
            nombre_completo="Super Administrador AudacIA",
        )
        print(" [OK] Superusuario creado: 'superadmin' (Clave: SuperAdmin2026!)")
    else:
        print(" [INFO] Superusuario 'superadmin' ya existe.")

    # 3. Administrador
    if not UsuarioAudacIA.objects.filter(username="admin_general").exists():
        admin = UsuarioAudacIA.objects.create_user(
            username="admin_general",
            password="AdminGeneral2026!",
            nombre_completo="Administrador de Operaciones",
            rol=UsuarioAudacIA.Rol.ADMINISTRADOR,
            almacen_asignado=None,
        )
        print(" [OK] Administrador creado: 'admin_general' (Clave: AdminGeneral2026!)")
    else:
        print(" [INFO] Administrador 'admin_general' ya existe.")

    # 4. Usuarios Comunes (requieren almacén obligatorio)
    if not UsuarioAudacIA.objects.filter(username="operador_norte").exists():
        UsuarioAudacIA.objects.create_user(
            username="operador_norte",
            password="OperadorNorte2026!",
            nombre_completo="Operador Bodega Norte",
            rol=UsuarioAudacIA.Rol.USUARIO,
            almacen_asignado=alm_norte,
        )
        print(" [OK] Usuario Norte creado: 'operador_norte' (Clave: OperadorNorte2026!) -> Almacén Norte")
    else:
        print(" [INFO] Usuario 'operador_norte' ya existe.")

    if not UsuarioAudacIA.objects.filter(username="operador_sur").exists():
        UsuarioAudacIA.objects.create_user(
            username="operador_sur",
            password="OperadorSur2026!",
            nombre_completo="Operador Bodega Sur",
            rol=UsuarioAudacIA.Rol.USUARIO,
            almacen_asignado=alm_sur,
        )
        print(" [OK] Usuario Sur creado: 'operador_sur' (Clave: OperadorSur2026!) -> Almacén Sur")
    else:
        print(" [INFO] Usuario 'operador_sur' ya existe.")

    print("\n[ÉXITO] Base de datos poblada con éxito para pruebas locales.")


if __name__ == "__main__":
    poblar_datos_prueba()
