"""Comando de gestión para poblar datos de prueba completos e idempotentes.

Crea almacenes, usuarios (4 roles), ubicaciones, 5 categorías, 16 productos
(incluyendo stock óptimo, stock bajo, stock crítico/agotado y productos estancados),
movimientos de entrada/salida/traslado distribuidos en los últimos 7 días,
préstamos (abiertos, parciales y cerrados) y conteos físicos (preliminar y confirmado).
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from cuentas.models import UsuarioAudacIA
from inventario.models import (
    Almacen,
    Categoria,
    ConteoFisico,
    Existencia,
    Movimiento,
    Prestamo,
    Producto,
    Ubicacion,
)
from inventario.servicios.datos_maestros import (
    crear_categoria,
    crear_producto,
    crear_ubicacion,
)
from inventario.servicios.operaciones import (
    confirmar_conteo_fisico,
    registrar_conteo_fisico,
    registrar_devolucion_prestamo,
    registrar_entrada,
    registrar_prestamo,
    registrar_salida,
    registrar_traslado_entre_almacenes,
)


class Command(BaseCommand):
    help = "Puebla la base de datos local con datos de prueba completos para QA manual."

    def handle(self, *args, **options):
        self.stdout.write(self.style.MIGRATE_HEADING("=== Poblando datos de prueba en Inventario AudacIA ==="))

        with transaction.atomic():
            # 1. Almacenes
            alm_norte, _ = Almacen.objects.get_or_create(
                nombre="Almacén Central Norte",
                defaults={
                    "descripcion": "Sede principal norte para almacenamiento y distribución general.",
                    "activo": True,
                },
            )
            alm_sur, _ = Almacen.objects.get_or_create(
                nombre="Almacén Satélite Sur",
                defaults={
                    "descripcion": "Bodega regional sur para despacho rápido y laboratorios.",
                    "activo": True,
                },
            )

            # 2. Usuarios de prueba (idempotentes y activos)
            superadmin = UsuarioAudacIA.objects.filter(username="superadmin").first()
            if not superadmin:
                superadmin = UsuarioAudacIA.objects.create_superuser(
                    username="superadmin",
                    password="SuperAdmin2026!",
                    nombre_completo="Super Administrador AudacIA",
                )

            admin_gen = UsuarioAudacIA.objects.filter(username="admin_general").first()
            if not admin_gen:
                admin_gen = UsuarioAudacIA.objects.create_user(
                    username="admin_general",
                    password="AdminGeneral2026!",
                    nombre_completo="Administrador de Operaciones",
                    rol=UsuarioAudacIA.Rol.ADMINISTRADOR,
                    almacen_asignado=None,
                )

            op_norte = UsuarioAudacIA.objects.filter(username="operador_norte").first()
            if not op_norte:
                op_norte = UsuarioAudacIA.objects.create_user(
                    username="operador_norte",
                    password="OperadorNorte2026!",
                    nombre_completo="Operador Bodega Norte",
                    rol=UsuarioAudacIA.Rol.USUARIO,
                    almacen_asignado=alm_norte,
                )

            op_sur = UsuarioAudacIA.objects.filter(username="operador_sur").first()
            if not op_sur:
                op_sur = UsuarioAudacIA.objects.create_user(
                    username="operador_sur",
                    password="OperadorSur2026!",
                    nombre_completo="Operador Bodega Sur",
                    rol=UsuarioAudacIA.Rol.USUARIO,
                    almacen_asignado=alm_sur,
                )
            elif not op_sur.is_active:
                op_sur.is_active = True
                op_sur.save(update_fields=["is_active"])

            # 3. Ubicaciones (3 por almacén)
            ubicaciones_spec = [
                ("NOR-E01-N01", alm_norte, "Estantería 01 - Nivel 01 (Herramientas)"),
                ("NOR-E01-N02", alm_norte, "Estantería 01 - Nivel 02 (Eléctricos)"),
                ("NOR-E02-N01", alm_norte, "Estantería 02 - Nivel 01 (Protección EPP)"),
                ("SUR-E01-N01", alm_sur, "Estantería 01 - Nivel 01 (Papelería)"),
                ("SUR-E01-N02", alm_sur, "Estantería 01 - Nivel 02 (Químicos)"),
                ("SUR-E02-N01", alm_sur, "Estantería 02 - Nivel 01 (Reserva Sur)"),
            ]
            ubicaciones = {}
            for nombre_ub, alm, desc in ubicaciones_spec:
                ub = Ubicacion.objects.filter(nombre=nombre_ub, almacen=alm).first()
                if not ub:
                    ub = crear_ubicacion(
                        nombre=nombre_ub,
                        almacen=alm,
                        descripcion=desc,
                        usuario=admin_gen,
                    )
                ubicaciones[nombre_ub] = ub

            # 4. Categorías (5 categorías)
            categorias_spec = [
                ("Herramientas Manuales", alm_norte),
                ("Material Eléctrico", alm_norte),
                ("Equipos de Protección", alm_norte),
                ("Papelería Técnica", alm_sur),
                ("Químicos y Solventes", alm_sur),
            ]
            categorias = {}
            for nombre_cat, alm in categorias_spec:
                cat = Categoria.objects.filter(nombre=nombre_cat).first()
                if not cat:
                    cat = crear_categoria(
                        nombre=nombre_cat,
                        almacen_principal=alm,
                        usuario=admin_gen,
                    )
                categorias[nombre_cat] = cat

            # 5. Productos (16 productos con estados variados)
            productos_spec = [
                # (nombre, categoria, stock_ini, stock_min, cod_barras, empaque, unid_emp, ubicacion)
                ("Destornillador Aislado 1000V Estrella", "Herramientas Manuales", 28, 10, "7701234567001", "Caja", 6, "NOR-E01-N01"),
                ("Alicate Universal Profesional 8 pulg", "Herramientas Manuales", 18, 8, "7701234567002", "", None, "NOR-E01-N01"),
                ("Juego de Llaves Allen Milimétricas", "Herramientas Manuales", 4, 10, "7701234567003", "Estuche", 1, "NOR-E01-N01"),  # Stock Bajo
                ("Martillo de Bola 16 oz Fibra", "Herramientas Manuales", 12, 5, "7701234567004", "", None, "NOR-E01-N01"),
                ("Multímetro Digital True RMS", "Material Eléctrico", 15, 5, "7701234567005", "Caja", 1, "NOR-E01-N02"),
                ("Rollo Cable THHN Calibre 12 AWG", "Material Eléctrico", 35, 10, "7701234567006", "Rollo", 1, "NOR-E01-N02"),
                ("Cinta Aislante Autofundente 3M", "Material Eléctrico", 3, 12, "7701234567007", "Paquete", 10, "NOR-E01-N02"),  # Stock Bajo
                ("Breaker Termomagnético Bipolar 30A", "Material Eléctrico", 0, 6, "7701234567008", "Caja", 4, "NOR-E01-N02"),   # Sin Stock (Crítico)
                ("Casco Dielectrico Tipo I Clase E", "Equipos de Protección", 24, 10, "7701234567009", "", None, "NOR-E02-N01"),
                ("Guantes de Nitrilo Anticorte Par", "Equipos de Protección", 5, 15, "7701234567010", "Paquete", 12, "NOR-E02-N01"),  # Stock Bajo
                ("Gafas de Seguridad Policarbonato UV", "Equipos de Protección", 0, 8, "7701234567011", "Caja", 12, "NOR-E02-N01"),  # Sin Stock (Crítico)
                ("Resma Papel Bond Carta 75g", "Papelería Técnica", 45, 15, "7701234567012", "Caja", 10, "SUR-E01-N01"),
                ("Marcador Permanente Industrial Negro", "Papelería Técnica", 36, 12, "7701234567013", "Caja", 12, "SUR-E01-N01"),
                ("Bitácora Foliada de Laboratorio", "Papelería Técnica", 14, 6, "7701234567014", "", None, "SUR-E01-N01"),
                ("Alcohol Isopropílico Grado Electrónico 1L", "Químicos y Solventes", 22, 8, "7701234567015", "Caja", 6, "SUR-E01-N02"),
                ("Limpiador de Contactos Dielectrico Spray", "Químicos y Solventes", 2, 10, "7701234567016", "Caja", 12, "SUR-E01-N02"),  # Stock Bajo
            ]

            productos = {}
            for (nombre_p, cat_nombre, s_ini, s_min, barras, emp, u_emp, ub_nombre) in productos_spec:
                prod = Producto.objects.filter(nombre=nombre_p).first()
                if not prod:
                    prod = crear_producto(
                        nombre=nombre_p,
                        categoria=categorias[cat_nombre],
                        stock_inicial=s_ini,
                        stock_minimo=s_min,
                        codigo_barras=barras,
                        nombre_empaque=emp,
                        unidades_por_empaque=u_emp,
                        descripcion=f"Artículo de catálogo técnico: {nombre_p}.",
                        ubicaciones=[ubicaciones[ub_nombre].id],
                        usuario=admin_gen,
                    )
                productos[nombre_p] = prod

            # 6. Movimientos operativos, préstamos y conteos (solo si no se han corrido antes)
            if not Movimiento.objects.filter(clave_idempotencia__startswith="SEED-").exists():
                ahora = timezone.now()

                # Fechar 2 productos como creados hace 45 días sin movimientos recientes (para reporte de estancados)
                for nombre_estancado in ("Martillo de Bola 16 oz Fibra", "Bitácora Foliada de Laboratorio"):
                    p_est = productos[nombre_estancado]
                    hace_45d = ahora - timedelta(days=45)
                    Producto.objects.filter(pk=p_est.pk).update(fecha_creacion=hace_45d)
                    Movimiento.objects.filter(producto=p_est).update(fecha_hora=hace_45d)

                # Entradas y salidas en los últimos 6 días para poblar el gráfico de balance
                ops_dias = [
                    # (hace_dias, tipo, prod_nombre, almacen, ubicacion, cantidad, usuario, motivo, destinatario)
                    (5, "ENTRADA", "Destornillador Aislado 1000V Estrella", alm_norte, "NOR-E01-N01", 12, op_norte, "Compra orden OC-401", ""),
                    (5, "SALIDA", "Alicate Universal Profesional 8 pulg", alm_norte, "NOR-E01-N01", 3, op_norte, "Dotación taller mecánico", "Ing. Roberto Méndez"),
                    (4, "ENTRADA", "Rollo Cable THHN Calibre 12 AWG", alm_norte, "NOR-E01-N02", 10, op_norte, "Reposición trimestral", ""),
                    (4, "SALIDA", "Rollo Cable THHN Calibre 12 AWG", alm_norte, "NOR-E01-N02", 6, op_norte, "Instalación banco de pruebas", "Técnico David Ríos"),
                    (3, "ENTRADA", "Resma Papel Bond Carta 75g", alm_sur, "SUR-E01-N01", 20, op_sur, "Abastecimiento papelería", ""),
                    (3, "SALIDA", "Resma Papel Bond Carta 75g", alm_sur, "SUR-E01-N01", 8, op_sur, "Entrega coordinación académica", "Laura Castellanos"),
                    (2, "SALIDA", "Casco Dielectrico Tipo I Clase E", alm_norte, "NOR-E02-N01", 4, op_norte, "Brigada de mantenimiento eléctrico", "Cuadrilla Norte"),
                    (1, "ENTRADA", "Alcohol Isopropílico Grado Electrónico 1L", alm_sur, "SUR-E01-N02", 6, op_sur, "Recepción proveedor químico", ""),
                    (1, "SALIDA", "Marcador Permanente Industrial Negro", alm_sur, "SUR-E01-N01", 5, op_sur, "Rotulado de estanterías", "Operaciones Sur"),
                    (0, "SALIDA", "Destornillador Aislado 1000V Estrella", alm_norte, "NOR-E01-N01", 4, op_norte, "Mantenimiento tableros", "Ing. Sofía Vergara"),
                ]

                for idx, (dias, tipo, p_nom, alm, ub_nom, cant, usr, mot, dest) in enumerate(ops_dias, start=1):
                    clave = f"SEED-MOV-{idx:03d}"
                    if tipo == "ENTRADA":
                        _, mov = registrar_entrada(
                            producto=productos[p_nom],
                            almacen=alm,
                            cantidad=cant,
                            ubicacion=ubicaciones[ub_nom],
                            usuario=usr,
                            motivo=mot,
                            clave_idempotencia=clave,
                        )
                    else:
                        _, mov, _ = registrar_salida(
                            producto=productos[p_nom],
                            almacen=alm,
                            cantidad=cant,
                            ubicacion=ubicaciones[ub_nom],
                            motivo=mot,
                            destinatario=dest,
                            usuario=usr,
                            clave_idempotencia=clave,
                        )
                    fecha_obj = ahora - timedelta(days=dias, hours=2)
                    Movimiento.objects.filter(pk=mov.pk).update(fecha_hora=fecha_obj)

                # Traslado entre almacenes
                _, _, mov_tras = registrar_traslado_entre_almacenes(
                    producto=productos["Casco Dielectrico Tipo I Clase E"],
                    almacen_origen=alm_norte,
                    almacen_destino=alm_sur,
                    cantidad=4,
                    ubicacion_origen=ubicaciones["NOR-E02-N01"],
                    ubicacion_destino=ubicaciones["SUR-E02-N01"],
                    motivo="Refuerzo de seguridad para sede Sur",
                    usuario=admin_gen,
                    clave_idempotencia="SEED-TRAS-001",
                )
                Movimiento.objects.filter(pk=mov_tras.pk).update(fecha_hora=ahora - timedelta(days=2))

                # Préstamo 1: Abierto
                registrar_prestamo(
                    producto=productos["Multímetro Digital True RMS"],
                    almacen=alm_norte,
                    cantidad=3,
                    destinatario="Prof. Carlos Gómez (Lab. Robótica)",
                    ubicacion=ubicaciones["NOR-E01-N02"],
                    motivo="Prácticas de instrumentación electrónica",
                    usuario=op_norte,
                    clave_idempotencia="SEED-PRES-001",
                )

                # Préstamo 2: Devuelto Parcialmente (prestó 4, devolvió 2, quedan 2 pendientes)
                prestamo_parcial, _, _ = registrar_prestamo(
                    producto=productos["Alicate Universal Profesional 8 pulg"],
                    almacen=alm_norte,
                    cantidad=4,
                    destinatario="Ing. Camilo Restrepo (Taller CNC)",
                    ubicacion=ubicaciones["NOR-E01-N01"],
                    motivo="Montaje de bancada mecánica",
                    usuario=op_norte,
                    clave_idempotencia="SEED-PRES-002",
                )
                registrar_devolucion_prestamo(
                    prestamo=prestamo_parcial,
                    cantidad=2,
                    motivo="Entrega parcial al finalizar primer turno",
                    usuario=op_norte,
                    clave_idempotencia="SEED-DEV-001",
                )

                # Préstamo 3: Cerrado (prestó 2, devolvió 2)
                prestamo_cerrado, _, _ = registrar_prestamo(
                    producto=productos["Alcohol Isopropílico Grado Electrónico 1L"],
                    almacen=alm_sur,
                    cantidad=2,
                    destinatario="Dra. Natalia Pineda (Lab. Química)",
                    ubicacion=ubicaciones["SUR-E01-N02"],
                    motivo="Limpieza de placas PCB",
                    usuario=op_sur,
                    clave_idempotencia="SEED-PRES-003",
                )
                registrar_devolucion_prestamo(
                    prestamo=prestamo_cerrado,
                    cantidad=2,
                    motivo="Devolución completa de envases sellados no usados",
                    usuario=op_sur,
                    clave_idempotencia="SEED-DEV-002",
                )

                # Conteo Físico 1: Preliminar (pendiente de confirmación administrativa)
                registrar_conteo_fisico(
                    producto=productos["Destornillador Aislado 1000V Estrella"],
                    almacen=alm_norte,
                    cantidad_contada=35,
                    motivo="Conteo cíclico semanal en Estantería 01",
                    usuario=op_norte,
                )

                # Conteo Físico 2: Confirmado por el administrador
                conteo_conf = registrar_conteo_fisico(
                    producto=productos["Marcador Permanente Industrial Negro"],
                    almacen=alm_sur,
                    cantidad_contada=30,
                    motivo="Verificación física mensual de papelería",
                    usuario=op_sur,
                )
                confirmar_conteo_fisico(
                    conteo=conteo_conf,
                    usuario=admin_gen,
                )

        # Resumen final
        self.stdout.write(
            self.style.SUCCESS(
                f"Datos de prueba listos:\n"
                f"  - Almacenes: {Almacen.objects.count()}\n"
                f"  - Ubicaciones: {Ubicacion.objects.count()}\n"
                f"  - Categorías: {Categoria.objects.count()}\n"
                f"  - Productos: {Producto.objects.count()}\n"
                f"  - Existencias: {Existencia.objects.count()}\n"
                f"  - Movimientos: {Movimiento.objects.count()}\n"
                f"  - Préstamos: {Prestamo.objects.count()}\n"
                f"  - Conteos Físicos: {ConteoFisico.objects.count()}"
            )
        )
