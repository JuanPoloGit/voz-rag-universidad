"""Script para poblar datos de prueba iniciales en Inventario AudacIA.

Permite probar de inmediato:
1. Login con rol Administrador (admin / Audacia2026!) o Usuario (operador / Audacia2026!).
2. Consultas de stock disponible de productos.
3. Alertas de stock bajo ("Cable Patch Cord UTP 3m") y sin stock ("Ponchadora RJ45 Profesional").
4. Préstamos activos pendientes (2 unidades de Ponchadora a Carlos Martínez).
5. Devolución de préstamo dictada por voz ("devolver préstamo de Carlos Martínez").
6. Registro de salida con pre-confirmación en dos pasos.
"""

import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "inventario_audacia.settings")
django.setup()

from cuentas.models import UsuarioAudacIA
from inventario.models import (
    Almacen,
    Auditoria,
    Categoria,
    Existencia,
    Movimiento,
    Prestamo,
    Producto,
    ProductoUbicacion,
    Ubicacion,
)


def poblar():
    print("=" * 60)
    print("Iniciando carga de datos de prueba para Inventario AudacIA...")
    print("=" * 60)

    # 1. Almacenes
    alm_central, _ = Almacen.objects.get_or_create(
        nombre="Almacén Central",
        defaults={"descripcion": "Almacén principal de campus", "activo": True},
    )
    alm_satelite, _ = Almacen.objects.get_or_create(
        nombre="Almacén Satélite",
        defaults={"descripcion": "Laboratorios de electrónica y redes", "activo": True},
    )
    print(f"[OK] Almacenes listos: '{alm_central.nombre}', '{alm_satelite.nombre}'")

    # 2. Usuarios
    admin_user = UsuarioAudacIA.objects.filter(username="admin").first()
    if not admin_user:
        admin_user = UsuarioAudacIA.objects.create_superuser(
            username="admin",
            password="Audacia2026!",
            nombre_completo="Administrador AudacIA",
            rol=UsuarioAudacIA.Rol.SUPERUSUARIO,
        )
        print("[OK] Superusuario creado: admin / Audacia2026!")
    else:
        admin_user.set_password("Audacia2026!")
        admin_user.save()
        print("[OK] Superusuario actualizado: admin / Audacia2026!")

    operador_user = UsuarioAudacIA.objects.filter(username="operador").first()
    if not operador_user:
        operador_user = UsuarioAudacIA.objects.create_user(
            username="operador",
            password="Audacia2026!",
            nombre_completo="Operador Central",
            rol=UsuarioAudacIA.Rol.USUARIO,
            almacen_asignado=alm_central,
        )
        print("[OK] Usuario estándar creado: operador / Audacia2026! (Almacén Central)")
    else:
        operador_user.set_password("Audacia2026!")
        operador_user.almacen_asignado = alm_central
        operador_user.save()
        print("[OK] Usuario estándar actualizado: operador / Audacia2026!")

    # 3. Categorías
    cat_cables, _ = Categoria.objects.get_or_create(
        nombre="Cables",
        defaults={"almacen_principal": alm_central, "activo": True},
    )
    cat_herramientas, _ = Categoria.objects.get_or_create(
        nombre="Herramientas",
        defaults={"almacen_principal": alm_central, "activo": True},
    )
    cat_redes, _ = Categoria.objects.get_or_create(
        nombre="Redes",
        defaults={"almacen_principal": alm_satelite, "activo": True},
    )
    print("[OK] Categorías listas: Cables, Herramientas, Redes")

    # 4. Ubicaciones
    ub_estante_a, _ = Ubicacion.objects.get_or_create(
        nombre="Estante A-1",
        almacen=alm_central,
        defaults={"descripcion": "Pasillo 1, nivel medio", "activo": True},
    )
    ub_cajon_b, _ = Ubicacion.objects.get_or_create(
        nombre="Cajón Herramientas B-2",
        almacen=alm_central,
        defaults={"descripcion": "Mueble metálico de herramientas", "activo": True},
    )
    ub_rack_1, _ = Ubicacion.objects.get_or_create(
        nombre="Rack de Servidores 1",
        almacen=alm_satelite,
        defaults={"descripcion": "Sala de telecomunicaciones", "activo": True},
    )
    print("[OK] Ubicaciones creadas.")

    # 5. Productos y Existencias
    # Producto 1: Cable HDMI (Stock saludable)
    p_hdmi, _ = Producto.objects.get_or_create(
        codigo_interno="CAB-0001",
        defaults={
            "nombre": "Cable HDMI 2m",
            "categoria": cat_cables,
            "stock_inicial": 50,
            "stock_minimo": 10,
            "activo": True,
        },
    )
    ProductoUbicacion.objects.get_or_create(producto=p_hdmi, ubicacion=ub_estante_a)
    e_hdmi, _ = Existencia.objects.get_or_create(
        producto=p_hdmi,
        almacen=alm_central,
        defaults={"stock_disponible": 50, "stock_en_prestamo": 0},
    )

    # Producto 2: Cable UTP (Alerta de Stock Bajo)
    p_utp, _ = Producto.objects.get_or_create(
        codigo_interno="CAB-0002",
        defaults={
            "nombre": "Cable Patch Cord UTP 3m",
            "categoria": cat_cables,
            "stock_inicial": 20,
            "stock_minimo": 10,
            "activo": True,
        },
    )
    ProductoUbicacion.objects.get_or_create(producto=p_utp, ubicacion=ub_estante_a)
    e_utp, _ = Existencia.objects.get_or_create(
        producto=p_utp,
        almacen=alm_central,
        defaults={"stock_disponible": 4, "stock_en_prestamo": 0},
    )
    # Asegurar stock bajo
    e_utp.stock_disponible = 4
    e_utp.save()

    # Producto 3: Ponchadora (Alerta Sin Stock y con Préstamo Activo)
    p_ponch, _ = Producto.objects.get_or_create(
        codigo_interno="HER-0001",
        defaults={
            "nombre": "Ponchadora RJ45 Profesional",
            "categoria": cat_herramientas,
            "stock_inicial": 3,
            "stock_minimo": 1,
            "activo": True,
        },
    )
    ProductoUbicacion.objects.get_or_create(producto=p_ponch, ubicacion=ub_cajon_b)
    e_ponch, _ = Existencia.objects.get_or_create(
        producto=p_ponch,
        almacen=alm_central,
        defaults={"stock_disponible": 0, "stock_en_prestamo": 2},
    )
    e_ponch.stock_disponible = 0
    e_ponch.stock_en_prestamo = 2
    e_ponch.save()

    # Producto 4: Switch Gigabit (En Almacén Satélite)
    p_switch, _ = Producto.objects.get_or_create(
        codigo_interno="RED-0001",
        defaults={
            "nombre": "Switch Gigabit 8 Puertos",
            "categoria": cat_redes,
            "stock_inicial": 10,
            "stock_minimo": 3,
            "activo": True,
        },
    )
    ProductoUbicacion.objects.get_or_create(producto=p_switch, ubicacion=ub_rack_1)
    e_switch, _ = Existencia.objects.get_or_create(
        producto=p_switch,
        almacen=alm_satelite,
        defaults={"stock_disponible": 10, "stock_en_prestamo": 0},
    )

    print("[OK] Productos y existencias configurados con alertas reales.")

    # 6. Préstamo activo para pruebas
    prestamo_activo = Prestamo.objects.filter(
        destinatario="Carlos Martínez",
        producto=p_ponch,
        estado=Prestamo.Estado.ABIERTO,
    ).first()

    if not prestamo_activo:
        mov_prestamo = Movimiento.objects.create(
            clave_idempotencia="demo-prestamo-carlos-001",
            tipo=Movimiento.Tipo.PRESTAMO,
            producto=p_ponch,
            cantidad=2,
            almacen_origen=alm_central,
            usuario=admin_user,
            destinatario="Carlos Martínez",
            motivo="Instalación de cableado estructurado aula 204",
            stock_disponible_anterior=2,
            stock_disponible_posterior=0,
        )
        prestamo_activo = Prestamo.objects.create(
            movimiento=mov_prestamo,
            producto=p_ponch,
            almacen=alm_central,
            destinatario="Carlos Martínez",
            cantidad_original=2,
            cantidad_pendiente_devolucion=2,
            estado=Prestamo.Estado.ABIERTO,
            usuario=admin_user,
        )
        print("[OK] Préstamo activo registrado: 2 Ponchadoras prestadas a Carlos Martínez.")
    else:
        print("[OK] Préstamo activo ya existía.")

    print("\n" + "=" * 60)
    print("¡Base de datos lista para pruebas!")
    print("Credenciales:")
    print("  • Superusuario: admin / Audacia2026!")
    print("  • Operador:     operador / Audacia2026!")
    print("URL del Panel Haku: http://127.0.0.1:8000/api/haku/panel/")
    print("=" * 60)


if __name__ == "__main__":
    poblar()
