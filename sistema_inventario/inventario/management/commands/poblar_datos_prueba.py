"""Comando de gestión para poblar datos de prueba completos e idempotentes.

Crea almacenes, usuarios con los 3 roles (Superusuario, Administrador, Operadores monosede y multisede),
ubicaciones físicas, categorías con tipos opcionales asociados, productos con empaques y códigos EAN,
movimientos de entrada/salida/traslados (interno y entre almacenes), préstamos en todos sus estados
(abiertos, con devolución parcial, con baja por daño, cerrados) y conteos físicos (pendientes y confirmados).
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from cuentas.models import IntentoFallido, UsuarioAudacIA
from inventario.models import (
    Almacen,
    Auditoria,
    Categoria,
    ConteoFisico,
    DevolucionPrestamo,
    Existencia,
    Movimiento,
    Prestamo,
    Producto,
    ProductoUbicacion,
    TipoCategoria,
    Ubicacion,
)
from inventario.servicios.datos_maestros import (
    crear_categoria,
    crear_producto,
    crear_tipo_categoria,
    crear_ubicacion,
)
from inventario.servicios.operaciones import (
    confirmar_conteo_fisico,
    registrar_baja_prestamo,
    registrar_conteo_fisico,
    registrar_devolucion_prestamo,
    registrar_entrada,
    registrar_prestamo,
    registrar_salida,
    registrar_traslado_entre_almacenes,
    registrar_traslado_interno,
)


class Command(BaseCommand):
    help = "Puebla la base de datos local con datos de prueba realistas para QA manual y demostraciones."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limpiar",
            action="store_true",
            help="Elimina registros de prueba previos para garantizar un estado limpio.",
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.MIGRATE_HEADING("=== Poblando datos de prueba en Inventario AudacIA ==="))

        if options.get("limpiar"):
            self.stdout.write("Limpiando datos existentes en cascada segura...")
            with transaction.atomic():
                ConteoFisico.objects.all().delete()
                DevolucionPrestamo.objects.all().delete()
                Prestamo.objects.all().delete()
                Movimiento.objects.all().delete()
                Existencia.objects.all().delete()
                ProductoUbicacion.objects.all().delete()
                Producto.objects.all().delete()
                TipoCategoria.objects.all().delete()
                Categoria.objects.all().delete()
                Ubicacion.objects.all().delete()

                # Desvincular usuarios de almacenes antes de borrar almacenes
                for u in UsuarioAudacIA.objects.all():
                    u.almacenes_asignados.clear()
                UsuarioAudacIA.objects.all().update(almacen_asignado=None, creado_por=None)
                Almacen.objects.all().delete()

                from django.db import connection
                with connection.cursor() as cursor:
                    cursor.execute("DELETE FROM inventario_auditoria;")
                IntentoFallido.objects.all().delete()
                # El superusuario es ineliminable por diseño de seguridad; eliminamos el resto de cuentas de prueba
                UsuarioAudacIA.objects.filter(is_superuser=False).delete()
            self.stdout.write(self.style.SUCCESS("[OK] Base de datos limpia."))

        with transaction.atomic():
            # 1. Almacenes institucionales
            alm_norte, _ = Almacen.objects.get_or_create(
                nombre="Almacén Central Norte",
                defaults={
                    "descripcion": "Sede principal norte para almacenamiento y distribución general.",
                    "activo": True,
                    "eliminado": False,
                },
            )
            alm_sur, _ = Almacen.objects.get_or_create(
                nombre="Almacén Satélite Sur",
                defaults={
                    "descripcion": "Bodega regional sur para despacho rápido y laboratorios.",
                    "activo": True,
                    "eliminado": False,
                },
            )

            # 2. Usuarios por rol y alcance
            password_estandar = "Audacia2026!"

            # Superusuario único e ineliminable (regla de negocio: exactamente 1 superusuario)
            superadmin = UsuarioAudacIA.objects.filter(rol=UsuarioAudacIA.Rol.SUPERUSUARIO).first()
            if not superadmin:
                superadmin = UsuarioAudacIA.objects.create_superuser(
                    username="superadmin",
                    password=password_estandar,
                    nombre_completo="Super Administrador Institucional",
                    rol=UsuarioAudacIA.Rol.SUPERUSUARIO,
                )
            else:
                superadmin.set_password(password_estandar)
                superadmin.nombre_completo = "Super Administrador Institucional"
                superadmin.is_active = True
                superadmin.eliminado_logicamente = False
                superadmin.save()

            # Administrador de operaciones (creado por superadmin)
            admin_gen = UsuarioAudacIA.objects.filter(username="admin_general").first()
            if not admin_gen:
                admin_gen = UsuarioAudacIA.objects.create_user(
                    username="admin_general",
                    password=password_estandar,
                    nombre_completo="Coordinador General de Operaciones",
                    rol=UsuarioAudacIA.Rol.ADMINISTRADOR,
                    almacen_asignado=None,
                    creado_por=superadmin,
                )
            else:
                admin_gen.set_password(password_estandar)
                admin_gen.nombre_completo = "Coordinador General de Operaciones"
                admin_gen.rol = UsuarioAudacIA.Rol.ADMINISTRADOR
                admin_gen.creado_por = superadmin
                admin_gen.is_active = True
                admin_gen.eliminado_logicamente = False
                admin_gen.save()

            # Operador Sede Norte (monosede)
            op_norte = UsuarioAudacIA.objects.filter(username="operador_norte").first()
            if not op_norte:
                op_norte = UsuarioAudacIA.objects.create_user(
                    username="operador_norte",
                    password=password_estandar,
                    nombre_completo="Operador Bodega Norte",
                    rol=UsuarioAudacIA.Rol.USUARIO,
                    almacen_asignado=alm_norte,
                    creado_por=admin_gen,
                )
            else:
                op_norte.set_password(password_estandar)
                op_norte.almacen_asignado = alm_norte
                op_norte.creado_por = admin_gen
                op_norte.is_active = True
                op_norte.eliminado_logicamente = False
                op_norte.save()
            op_norte.almacenes_asignados.set([alm_norte])

            # Operador Sede Sur (monosede)
            op_sur = UsuarioAudacIA.objects.filter(username="operador_sur").first()
            if not op_sur:
                op_sur = UsuarioAudacIA.objects.create_user(
                    username="operador_sur",
                    password=password_estandar,
                    nombre_completo="Operador Bodega Sur",
                    rol=UsuarioAudacIA.Rol.USUARIO,
                    almacen_asignado=alm_sur,
                    creado_por=admin_gen,
                )
            else:
                op_sur.set_password(password_estandar)
                op_sur.almacen_asignado = alm_sur
                op_sur.creado_por = admin_gen
                op_sur.is_active = True
                op_sur.eliminado_logicamente = False
                op_sur.save()
            op_sur.almacenes_asignados.set([alm_sur])

            # Operador Campus (multisede: asignado tanto a Norte como a Sur)
            op_campus = UsuarioAudacIA.objects.filter(username="operador_campus").first()
            if not op_campus:
                op_campus = UsuarioAudacIA.objects.create_user(
                    username="operador_campus",
                    password=password_estandar,
                    nombre_completo="Técnico de Soporte Campus (Multi-Almacén)",
                    rol=UsuarioAudacIA.Rol.USUARIO,
                    almacen_asignado=alm_norte,
                    creado_por=admin_gen,
                )
            else:
                op_campus.set_password(password_estandar)
                op_campus.almacen_asignado = alm_norte
                op_campus.creado_por = admin_gen
                op_campus.is_active = True
                op_campus.eliminado_logicamente = False
                op_campus.save()
            op_campus.almacenes_asignados.set([alm_norte, alm_sur])

            # Operador genérico de compatibilidad ("operador")
            op_generico = UsuarioAudacIA.objects.filter(username="operador").first()
            if not op_generico:
                op_generico = UsuarioAudacIA.objects.create_user(
                    username="operador",
                    password=password_estandar,
                    nombre_completo="Operador General Central",
                    rol=UsuarioAudacIA.Rol.USUARIO,
                    almacen_asignado=alm_norte,
                    creado_por=admin_gen,
                )
            else:
                op_generico.set_password(password_estandar)
                op_generico.almacen_asignado = alm_norte
                op_generico.creado_por = admin_gen
                op_generico.is_active = True
                op_generico.eliminado_logicamente = False
                op_generico.save()
            op_generico.almacenes_asignados.set([alm_norte])

            # 3. Ubicaciones físicas (3 por almacén)
            ubicaciones_spec = [
                ("NOR-E01-N01", alm_norte, "Estantería 01 - Nivel 01 (Herramientas Manuales)"),
                ("NOR-E01-N02", alm_norte, "Estantería 01 - Nivel 02 (Material Eléctrico)"),
                ("NOR-E02-N01", alm_norte, "Estantería 02 - Nivel 01 (Equipos de Protección Personal)"),
                ("SUR-E01-N01", alm_sur, "Estantería 01 - Nivel 01 (Papelería Técnica e Impresos)"),
                ("SUR-E01-N02", alm_sur, "Estantería 01 - Nivel 02 (Químicos y Solventes Especializados)"),
                ("SUR-E02-N01", alm_sur, "Estantería 02 - Nivel 01 (Reserva General Sede Sur)"),
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

            # 4. Categorías y Tipos opcionales
            categorias_spec = [
                ("Herramientas Manuales", alm_norte, ["Destornillador", "Alicate", "Llave", "Martillo"]),
                ("Material Eléctrico", alm_norte, ["Instrumento", "Cable", "Aislante", "Protección eléctrica"]),
                ("Equipos de Protección", alm_norte, ["Cabeza", "Manos", "Visual"]),
                ("Papelería Técnica", alm_sur, ["Papel", "Escritura", "Registro"]),
                ("Químicos y Solventes", alm_sur, ["Solvente", "Aerosol"]),
            ]
            categorias = {}
            tipos_map = {}
            for nombre_cat, alm, lista_tipos in categorias_spec:
                cat = Categoria.objects.filter(nombre=nombre_cat).first()
                if not cat:
                    cat = crear_categoria(
                        nombre=nombre_cat,
                        almacen_principal=alm,
                        usuario=admin_gen,
                    )
                categorias[nombre_cat] = cat
                for nombre_tipo in lista_tipos:
                    tipo_obj = TipoCategoria.objects.filter(categoria=cat, nombre=nombre_tipo).first()
                    if not tipo_obj:
                        tipo_obj = crear_tipo_categoria(
                            nombre=nombre_tipo,
                            categoria=cat,
                            usuario=admin_gen,
                        )
                    tipos_map[(nombre_cat, nombre_tipo)] = tipo_obj

            # 5. Catálogo de Productos (16 artículos reales con ubicación física única)
            productos_spec = [
                # (nombre, categoria, tipo_nombre, stock_ini, stock_min, cod_barras, empaque, unid_emp, ubicacion)
                ("Destornillador Aislado 1000V Estrella", "Herramientas Manuales", "Destornillador", 28, 10, "7701234567001", "Caja", 6, "NOR-E01-N01"),
                ("Alicate Universal Profesional 8 pulg", "Herramientas Manuales", "Alicate", 18, 8, "7701234567002", "", None, "NOR-E01-N01"),
                ("Juego de Llaves Allen Milimétricas", "Herramientas Manuales", "Llave", 4, 10, "7701234567003", "Estuche", 1, "NOR-E01-N01"),  # Stock Bajo
                ("Martillo de Bola 16 oz Fibra", "Herramientas Manuales", "Martillo", 12, 5, "7701234567004", "", None, "NOR-E01-N01"),
                ("Multímetro Digital True RMS", "Material Eléctrico", "Instrumento", 15, 5, "7701234567005", "Caja", 1, "NOR-E01-N02"),
                ("Rollo Cable THHN Calibre 12 AWG", "Material Eléctrico", "Cable", 35, 10, "7701234567006", "Rollo", 1, "NOR-E01-N02"),
                ("Cinta Aislante Autofundente 3M", "Material Eléctrico", "Aislante", 3, 12, "7701234567007", "Paquete", 10, "NOR-E01-N02"),  # Stock Bajo
                ("Breaker Termomagnético Bipolar 30A", "Material Eléctrico", "Protección eléctrica", 0, 6, "7701234567008", "Caja", 4, "NOR-E01-N02"),   # Sin Stock (Crítico)
                ("Casco Dieléctrico Tipo I Clase E", "Equipos de Protección", "Cabeza", 24, 10, "7701234567009", "", None, "NOR-E02-N01"),
                ("Guantes de Nitrilo Anticorte Par", "Equipos de Protección", "Manos", 5, 15, "7701234567010", "Paquete", 12, "NOR-E02-N01"),  # Stock Bajo
                ("Gafas de Seguridad Policarbonato UV", "Equipos de Protección", "Visual", 0, 8, "7701234567011", "Caja", 12, "NOR-E02-N01"),  # Sin Stock (Crítico)
                ("Resma Papel Bond Carta 75g", "Papelería Técnica", "Papel", 45, 15, "7701234567012", "Caja", 10, "SUR-E01-N01"),
                ("Marcador Permanente Industrial Negro", "Papelería Técnica", "Escritura", 36, 12, "7701234567013", "Caja", 12, "SUR-E01-N01"),
                ("Bitácora Foliada de Laboratorio", "Papelería Técnica", "Registro", 14, 6, "7701234567014", "", None, "SUR-E01-N01"),
                ("Alcohol Isopropílico Grado Electrónico 1L", "Químicos y Solventes", "Solvente", 22, 8, "7701234567015", "Caja", 6, "SUR-E01-N02"),
                ("Limpiador de Contactos Dieléctrico Spray", "Químicos y Solventes", "Aerosol", 2, 10, "7701234567016", "Caja", 12, "SUR-E01-N02"),  # Stock Bajo
            ]

            productos = {}
            for (nombre_p, cat_nombre, tipo_nom, s_ini, s_min, barras, emp, u_emp, ub_nombre) in productos_spec:
                tipo_obj = tipos_map.get((cat_nombre, tipo_nom))
                prod = Producto.objects.filter(nombre=nombre_p).first()
                if not prod:
                    prod = crear_producto(
                        nombre=nombre_p,
                        categoria=categorias[cat_nombre],
                        tipo=tipo_obj,
                        stock_inicial=s_ini,
                        stock_minimo=s_min,
                        codigo_barras=barras,
                        nombre_empaque=emp,
                        unidades_por_empaque=u_emp,
                        descripcion=f"Artículo de catálogo técnico institucional: {nombre_p}.",
                        ubicaciones=[ubicaciones[ub_nombre].id],
                        usuario=admin_gen,
                    )
                else:
                    if prod.tipo_id is None and tipo_obj is not None:
                        prod.tipo = tipo_obj
                        prod.save(update_fields=["tipo"])
                    # Asegurar ubicación única asignada
                    ub_target = ubicaciones[ub_nombre]
                    if not prod.ubicaciones.filter(id=ub_target.id).exists():
                        prod.ubicaciones.clear()
                        ProductoUbicacion.objects.create(producto=prod, ubicacion=ub_target)
                productos[nombre_p] = prod

            # 6. Historial de movimientos, préstamos y conteos
            if not Movimiento.objects.filter(clave_idempotencia__startswith="SEED-").exists():
                ahora = timezone.now()

                # Simular antigüedad para 2 productos sin rotación (reporte de estancados)
                for nombre_estancado in ("Martillo de Bola 16 oz Fibra", "Bitácora Foliada de Laboratorio"):
                    p_est = productos[nombre_estancado]
                    hace_45d = ahora - timedelta(days=45)
                    Producto.objects.filter(pk=p_est.pk).update(fecha_creacion=hace_45d)
                    Movimiento.objects.filter(producto=p_est).update(fecha_hora=hace_45d)

                # Entradas y dotaciones en los últimos días
                ops_dias = [
                    (5, "ENTRADA", "Destornillador Aislado 1000V Estrella", alm_norte, "NOR-E01-N01", 12, op_norte, "Compra orden OC-401", ""),
                    (5, "SALIDA", "Alicate Universal Profesional 8 pulg", alm_norte, "NOR-E01-N01", 3, op_norte, "Dotación taller mecánico", "Ing. Roberto Méndez"),
                    (4, "ENTRADA", "Rollo Cable THHN Calibre 12 AWG", alm_norte, "NOR-E01-N02", 10, op_norte, "Reposición trimestral de conductores", ""),
                    (4, "SALIDA", "Rollo Cable THHN Calibre 12 AWG", alm_norte, "NOR-E01-N02", 6, op_norte, "Instalación banco de pruebas", "Técnico David Ríos"),
                    (3, "ENTRADA", "Resma Papel Bond Carta 75g", alm_sur, "SUR-E01-N01", 20, op_sur, "Abastecimiento papelería", ""),
                    (3, "SALIDA", "Resma Papel Bond Carta 75g", alm_sur, "SUR-E01-N01", 8, op_sur, "Entrega coordinación académica", "Laura Castellanos"),
                    (2, "SALIDA", "Casco Dieléctrico Tipo I Clase E", alm_norte, "NOR-E02-N01", 4, op_norte, "Brigada de mantenimiento eléctrico", "Cuadrilla Norte"),
                    (1, "ENTRADA", "Alcohol Isopropílico Grado Electrónico 1L", alm_sur, "SUR-E01-N02", 6, op_sur, "Recepción proveedor químico", ""),
                    (1, "SALIDA", "Marcador Permanente Industrial Negro", alm_sur, "SUR-E01-N01", 5, op_sur, "Rotulado de estanterías", "Operaciones Sur"),
                    (0, "SALIDA", "Destornillador Aislado 1000V Estrella", alm_norte, "NOR-E01-N01", 4, op_norte, "Mantenimiento preventivo de tableros", "Ing. Sofía Vergara"),
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

                # Traslado entre almacenes (Norte -> Sur)
                _, _, mov_tras = registrar_traslado_entre_almacenes(
                    producto=productos["Casco Dieléctrico Tipo I Clase E"],
                    almacen_origen=alm_norte,
                    almacen_destino=alm_sur,
                    cantidad=4,
                    ubicacion_origen=ubicaciones["NOR-E02-N01"],
                    ubicacion_destino=ubicaciones["SUR-E02-N01"],
                    motivo="Refuerzo de dotación para cuadrilla Sede Sur",
                    usuario=admin_gen,
                    clave_idempotencia="SEED-TRAS-001",
                )
                Movimiento.objects.filter(pk=mov_tras.pk).update(fecha_hora=ahora - timedelta(days=2))

                # Traslado interno de ubicación dentro del mismo almacén
                _, mov_interno = registrar_traslado_interno(
                    producto=productos["Rollo Cable THHN Calibre 12 AWG"],
                    almacen=alm_norte,
                    ubicacion_origen=ubicaciones["NOR-E01-N02"],
                    ubicacion_destino=ubicaciones["NOR-E01-N01"],
                    motivo="Reorganización interna a estantería de herramientas",
                    usuario=op_norte,
                    clave_idempotencia="SEED-TRAS-INT-001",
                )
                Movimiento.objects.filter(pk=mov_interno.pk).update(fecha_hora=ahora - timedelta(days=1))

                # Préstamo 1: Abierto (3 multímetros prestados a docente)
                registrar_prestamo(
                    producto=productos["Multímetro Digital True RMS"],
                    almacen=alm_norte,
                    cantidad=3,
                    destinatario="Prof. Carlos Gómez (Lab. Robótica)",
                    ubicacion=ubicaciones["NOR-E01-N02"],
                    motivo="Prácticas de laboratorio de circuitos e instrumentación",
                    usuario=op_norte,
                    clave_idempotencia="SEED-PRES-001",
                )

                # Préstamo 2: Devuelto Parcialmente (prestó 4, devolvió 2, restan 2 pendientes)
                prestamo_parcial, _, _ = registrar_prestamo(
                    producto=productos["Alicate Universal Profesional 8 pulg"],
                    almacen=alm_norte,
                    cantidad=4,
                    destinatario="Ing. Camilo Restrepo (Taller CNC)",
                    ubicacion=ubicaciones["NOR-E01-N01"],
                    motivo="Montaje y ajuste de bancada mecánica",
                    usuario=op_norte,
                    clave_idempotencia="SEED-PRES-002",
                )
                registrar_devolucion_prestamo(
                    prestamo=prestamo_parcial,
                    cantidad=2,
                    motivo="Entrega parcial de 2 unidades al finalizar primer turno",
                    usuario=op_norte,
                    clave_idempotencia="SEED-DEV-001",
                )

                # Préstamo 3: Devuelto Parcial con Baja por Daño
                prestamo_baja, _, _ = registrar_prestamo(
                    producto=productos["Juego de Llaves Allen Milimétricas"],
                    almacen=alm_norte,
                    cantidad=2,
                    destinatario="Téc. Fernando Ruiz (Mantenimiento)",
                    ubicacion=ubicaciones["NOR-E01-N01"],
                    motivo="Desarme de motores trifásicos",
                    usuario=op_norte,
                    clave_idempotencia="SEED-PRES-BAJA-001",
                )
                registrar_baja_prestamo(
                    prestamo=prestamo_baja,
                    cantidad=1,
                    motivo="Baja por rotura accidental de llave 5mm durante torque",
                    usuario=admin_gen,
                )

                # Préstamo 4: Cerrado (prestó 2, devolvió 2)
                prestamo_cerrado, _, _ = registrar_prestamo(
                    producto=productos["Alcohol Isopropílico Grado Electrónico 1L"],
                    almacen=alm_sur,
                    cantidad=2,
                    destinatario="Dra. Natalia Pineda (Lab. Química)",
                    ubicacion=ubicaciones["SUR-E01-N02"],
                    motivo="Limpieza de placas de prueba",
                    usuario=op_sur,
                    clave_idempotencia="SEED-PRES-003",
                )
                registrar_devolucion_prestamo(
                    prestamo=prestamo_cerrado,
                    cantidad=2,
                    motivo="Devolución completa de envases sellados no utilizados",
                    usuario=op_sur,
                    clave_idempotencia="SEED-DEV-002",
                )

                # Conteo Físico 1: Pendiente de confirmación (con discrepancia: contado 35 vs sistema 36 -> dif -1)
                registrar_conteo_fisico(
                    producto=productos["Destornillador Aislado 1000V Estrella"],
                    almacen=alm_norte,
                    cantidad_contada=35,
                    motivo="Conteo cíclico semanal en Estantería 01",
                    usuario=op_norte,
                )

                # Conteo Físico 2: Confirmado por Administrador
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

        # Resumen final de la operación
        self.stdout.write(
            self.style.SUCCESS(
                f"\n=== Base de datos poblada con éxito ===\n"
                f"  - Almacenes: {Almacen.objects.count()}\n"
                f"  - Ubicaciones: {Ubicacion.objects.count()}\n"
                f"  - Categorías: {Categoria.objects.count()}\n"
                f"  - Tipos de Categoría: {TipoCategoria.objects.count()}\n"
                f"  - Productos: {Producto.objects.count()}\n"
                f"  - Existencias: {Existencia.objects.count()}\n"
                f"  - Movimientos: {Movimiento.objects.count()}\n"
                f"  - Préstamos: {Prestamo.objects.count()}\n"
                f"  - Conteos Físicos: {ConteoFisico.objects.count()}\n\n"
                f"Usuarios y Credenciales (Contraseña única: Audacia2026!):\n"
                f"  1. Superusuario:       {superadmin.username}\n"
                f"  2. Administrador:      admin_general (Creado por {superadmin.username})\n"
                f"  3. Operador Norte:     operador_norte (Almacén Central Norte)\n"
                f"  4. Operador Sur:       operador_sur (Almacén Satélite Sur)\n"
                f"  5. Operador Multisede: operador_campus (Norte y Sur)\n"
                f"  6. Operador General:   operador (Almacén Central Norte)"
            )
        )
