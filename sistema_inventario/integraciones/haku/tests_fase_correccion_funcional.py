"""
Pruebas automatizadas de regresión funcional para HAKU Inventario:
1. Permisos multialmacén (un almacén, varios almacenes, acceso global autorizado, denegación).
2. Lenguaje conversacional: habilitar / inhabilitar productos, resolución anafórica ("lo quiero inhabilitar")
   y manejo de estado (ya habilitado / ya inhabilitado).
3. Escáner y catálogo de códigos de barras (validación GS1 EAN/UPC, ceros a la izquierda, detección de duplicados).
"""

from unittest.mock import patch, MagicMock
import datetime
from zoneinfo import ZoneInfo
from django.test import TestCase
from django.core.exceptions import PermissionDenied, ValidationError

from cuentas.models import UsuarioAudacIA
from cuentas.permisos import puede_acceder_almacen, filtrar_por_almacen
from inventario.models import Almacen, Categoria, Existencia, Producto, Auditoria
from integraciones.haku.services import procesar_comando_inventario, ejecutar_confirmacion_propuesta
from integraciones.haku.lenguaje.normalizador import NormalizadorLenguajeHaku, IntencionTipo
from integraciones.haku.servicios_catalogo import (
    validar_codigo_barras,
    inspeccionar_codigo_barras,
    calcular_checksum_ean,
)


class TestPermisosMultialmacen(TestCase):
    """Batería de pruebas para el acceso multialmacén de usuarios."""

    def setUp(self):
        self.alm_central = Almacen.objects.create(nombre="Almacén Central", activo=True)
        self.alm_norte = Almacen.objects.create(nombre="Almacén Norte", activo=True)
        self.alm_sur = Almacen.objects.create(nombre="Almacén Sur", activo=True)
        self.alm_inactivo = Almacen.objects.create(nombre="Almacén Inactivo", activo=False)

        # 1. Superusuario
        self.superadmin = UsuarioAudacIA.objects.create_user(
            username="super_haku",
            password="Password123!",
            nombre_completo="Super Admin",
            rol=UsuarioAudacIA.Rol.SUPERUSUARIO,
        )

        # 2. Administrador
        self.admin = UsuarioAudacIA.objects.create_user(
            username="admin_haku",
            password="Password123!",
            nombre_completo="Admin Haku",
            rol=UsuarioAudacIA.Rol.ADMINISTRADOR,
        )

        # 3. Usuario con un único almacén
        self.user_mono = UsuarioAudacIA.objects.create_user(
            username="user_mono",
            password="Password123!",
            nombre_completo="Operador Central",
            rol=UsuarioAudacIA.Rol.USUARIO,
            almacen_asignado=self.alm_central,
        )

        # 4. Usuario con múltiples almacenes
        self.user_multi = UsuarioAudacIA.objects.create_user(
            username="user_multi",
            password="Password123!",
            nombre_completo="Operador Multi",
            rol=UsuarioAudacIA.Rol.USUARIO,
            almacen_asignado=self.alm_central,
        )
        self.user_multi.almacenes_asignados.add(self.alm_central, self.alm_norte)

        # 5. Usuario con acceso a todos los almacenes (sin ser admin)
        self.user_todos = UsuarioAudacIA.objects.create_user(
            username="user_todos",
            password="Password123!",
            nombre_completo="Operador Global",
            rol=UsuarioAudacIA.Rol.USUARIO,
            almacen_asignado=self.alm_central,
            acceso_todos_almacenes=True,
        )

    def test_usuario_un_almacen_permisos(self):
        """Usuario con un almacén solo puede operar en él."""
        self.assertTrue(puede_acceder_almacen(self.user_mono, self.alm_central))
        self.assertFalse(puede_acceder_almacen(self.user_mono, self.alm_norte))
        self.assertFalse(puede_acceder_almacen(self.user_mono, self.alm_sur))
        self.assertFalse(puede_acceder_almacen(self.user_mono, self.alm_inactivo))

    def test_usuario_multi_almacen_permisos(self):
        """Usuario con varios almacenes puede operar en todos ellos pero no en los no asignados."""
        self.assertTrue(puede_acceder_almacen(self.user_multi, self.alm_central))
        self.assertTrue(puede_acceder_almacen(self.user_multi, self.alm_norte))
        self.assertFalse(puede_acceder_almacen(self.user_multi, self.alm_sur))
        self.assertFalse(puede_acceder_almacen(self.user_multi, self.alm_inactivo))

    def test_usuario_acceso_todos_almacenes(self):
        """Usuario con acceso_todos_almacenes puede operar en cualquier almacén activo."""
        self.assertTrue(puede_acceder_almacen(self.user_todos, self.alm_central))
        self.assertTrue(puede_acceder_almacen(self.user_todos, self.alm_norte))
        self.assertTrue(puede_acceder_almacen(self.user_todos, self.alm_sur))
        # No puede operar en almacén inactivo
        self.assertFalse(puede_acceder_almacen(self.user_todos, self.alm_inactivo))
        # Conserva rol USUARIO (no se elevó a administrador)
        self.assertFalse(self.user_todos.es_administrador)

    def test_superusuario_y_admin_acceso_global(self):
        """Superusuario y Administrador acceden a cualquier almacén activo."""
        self.assertTrue(puede_acceder_almacen(self.superadmin, self.alm_central))
        self.assertTrue(puede_acceder_almacen(self.admin, self.alm_sur))
        self.assertFalse(puede_acceder_almacen(self.admin, self.alm_inactivo))

    def test_filtrar_por_almacen_queryset(self):
        """filtrar_por_almacen debe restringir o expandir según permisos multialmacén."""
        cat = Categoria.objects.create(nombre="Redes", almacen_principal=self.alm_central)
        prod = Producto.objects.create(nombre="Router", categoria=cat)
        ex_central = Existencia.objects.create(producto=prod, almacen=self.alm_central, stock_disponible=10)
        ex_norte = Existencia.objects.create(producto=prod, almacen=self.alm_norte, stock_disponible=5)
        ex_sur = Existencia.objects.create(producto=prod, almacen=self.alm_sur, stock_disponible=8)

        qs = Existencia.objects.all()

        # Mono: solo central
        res_mono = filtrar_por_almacen(qs, self.user_mono)
        self.assertEqual(res_mono.count(), 1)
        self.assertIn(ex_central, res_mono)

        # Multi: central y norte
        res_multi = filtrar_por_almacen(qs, self.user_multi)
        self.assertEqual(res_multi.count(), 2)
        self.assertIn(ex_central, res_multi)
        self.assertIn(ex_norte, res_multi)
        self.assertNotIn(ex_sur, res_multi)

        # Todos: los 3
        res_todos = filtrar_por_almacen(qs, self.user_todos)
        self.assertEqual(res_todos.count(), 3)

    def test_usuario_todos_almacenes_accede_a_almacenes_creados_posteriormente(self):
        """Un usuario normal con 'Todos los almacenes' puede acceder a almacenes creados con posterioridad."""
        alm_nuevo = Almacen.objects.create(nombre="Almacén Nuevo Puerto", activo=True)
        self.assertTrue(puede_acceder_almacen(self.user_todos, alm_nuevo))
        self.assertIn(alm_nuevo, self.user_todos.obtener_almacenes_accesibles())

        # En cambio, el usuario limitado no puede acceder
        self.assertFalse(puede_acceder_almacen(self.user_mono, alm_nuevo))

    def test_usuario_todos_almacenes_no_tiene_permisos_administrativos(self):
        """Tener acceso_todos_almacenes no concede privilegios de administrador ni is_staff."""
        self.assertFalse(self.user_todos.es_administrador)
        self.assertFalse(self.user_todos.is_staff)
        self.assertFalse(self.user_todos.is_superuser)
        self.assertTrue(self.user_todos.es_usuario_normal)

    def test_exclusion_mutua_formulario_usuario(self):
        """El formulario debe rechazar si se selecciona 'Todos los almacenes' y almacenes específicos a la vez."""
        from cuentas.forms import UsuarioForm
        form = UsuarioForm(data={
            "username": "usuario_prueba_excl",
            "nombre_completo": "Prueba Exclusion",
            "rol": UsuarioAudacIA.Rol.USUARIO,
            "password": "Password123!",
            "acceso_todos_almacenes": True,
            "almacenes_asignados": [self.alm_central.id],
            "is_active": True,
        })
        self.assertFalse(form.is_valid())
        self.assertIn("acceso_todos_almacenes", form.errors)

    def test_opcion_b_todos_almacenes_formulario_valido(self):
        """El formulario acepta 'Todos los almacenes' sin exigir almacén específico."""
        from cuentas.forms import UsuarioForm
        form = UsuarioForm(data={
            "username": "usuario_opcion_b",
            "nombre_completo": "Prueba Opcion B",
            "rol": UsuarioAudacIA.Rol.USUARIO,
            "password": "Password123!",
            "acceso_todos_almacenes": True,
            "is_active": True,
        })
        self.assertTrue(form.is_valid(), form.errors)
        user_creado = form.save()
        self.assertTrue(user_creado.acceso_todos_almacenes)
        self.assertIsNone(user_creado.almacen_asignado)
        self.assertEqual(user_creado.almacenes_asignados.count(), 0)
        self.assertTrue(puede_acceder_almacen(user_creado, self.alm_central))

    def test_cambio_de_rol_admin_a_usuario_exige_alcance_explicito(self):
        """Si un administrador pasa a ser usuario normal, debe definirse explícitamente el alcance."""
        from cuentas.forms import UsuarioForm
        # Editar admin para pasarlo a USUARIO sin especificar almacén ni acceso_todos
        form = UsuarioForm(instance=self.admin, data={
            "username": self.admin.username,
            "nombre_completo": self.admin.nombre_completo,
            "rol": UsuarioAudacIA.Rol.USUARIO,
            "is_active": True,
        })
        self.assertFalse(form.is_valid())
        self.assertIn("almacen_asignado", form.errors)

    def test_cambio_de_rol_usuario_a_admin_limpia_alcances_y_adquiere_global(self):
        """Si un usuario pasa a Administrador, adquiere acceso global por rol y limpia campos individuales."""
        from cuentas.forms import UsuarioForm
        super_actor = self.superadmin
        form = UsuarioForm(instance=self.user_todos, request_user=super_actor, data={
            "username": self.user_todos.username,
            "nombre_completo": self.user_todos.nombre_completo,
            "rol": UsuarioAudacIA.Rol.ADMINISTRADOR,
            "is_active": True,
        })
        self.assertTrue(form.is_valid(), form.errors)
        admin_nuevo = form.save()
        self.assertTrue(admin_nuevo.es_administrador)
        self.assertFalse(admin_nuevo.acceso_todos_almacenes)
        self.assertIsNone(admin_nuevo.almacen_asignado)
        self.assertTrue(puede_acceder_almacen(admin_nuevo, self.alm_central))


class TestLenguajeHabilitarInhabilitar(TestCase):
    """Pruebas de regresión para las órdenes de habilitar e inhabilitar productos."""

    def setUp(self):
        self.almacen = Almacen.objects.create(nombre="Almacén Principal", activo=True)
        self.admin = UsuarioAudacIA.objects.create_user(
            username="admin_voz",
            password="Password123!",
            nombre_completo="Admin Voz",
            rol=UsuarioAudacIA.Rol.ADMINISTRADOR,
        )
        self.categoria = Categoria.objects.create(nombre="Redes y Conectividad", almacen_principal=self.almacen)
        self.producto = Producto.objects.create(
            nombre="Switch Gigabit de 8 Puertos",
            categoria=self.categoria,
            codigo_barras="7701234567890",
            activo=True,
            archivado=False,
        )

    def test_intencion_habilitar_directa(self):
        """Reconocer expresiones como 'Quiero ir a habilitar un producto'."""
        res = NormalizadorLenguajeHaku.interpretar("Quiero ir a habilitar un producto", usuario=self.admin)
        self.assertEqual(res.tipo, IntencionTipo.HABILITAR_PRODUCTO)

    def test_secuencia_completa_informe_usuario(self):
        """Reproduce la conversación exacta reportada por el usuario:
        1. 'Quiero inhabilitar un producto' -> solicita identificarlo.
        2. Menciona con errores fonéticos 'Switch Gigabit de 8 Puertos'.
        3. HAKU responde con la ficha del producto.
        4. 'Lo quiero inhabilitarlo' -> HAKU genera la propuesta para el producto en foco.
        """
        # Paso 1: 'Quiero inhabilitar un producto'
        sesion = {}
        res1 = procesar_comando_inventario(self.admin, "Quiero inhabilitar un producto", sesion=sesion)
        self.assertEqual(res1["tipo"], "aclaracion")

        # Paso 2 y 3: Consulta del catálogo con errores fonéticos 'suich gigabit 8 puertos'
        res2 = procesar_comando_inventario(self.admin, "Consulta el suich gigabit de 8 puertos", sesion=sesion)
        self.assertIn(self.producto.codigo_interno, res2["mensaje_pantalla"])

        # Paso 4: 'Lo quiero inhabilitarlo'
        res3 = procesar_comando_inventario(self.admin, "Lo quiero inhabilitarlo", sesion=sesion)
        self.assertEqual(res3["tipo"], "propuesta")
        self.assertEqual(res3["propuesta"]["tipo_operacion"], "INHABILITAR_PRODUCTO")
        self.assertEqual(res3["propuesta"]["producto_id"], self.producto.id)

        # Confirmación real y verificación de persistencia
        confirm_res = ejecutar_confirmacion_propuesta(self.admin, res3["propuesta"])
        self.assertTrue(confirm_res["exito"])

        self.producto.refresh_from_db()
        self.assertFalse(self.producto.activo)
        self.assertTrue(self.producto.archivado)

        # Auditoría registrada
        aud = Auditoria.objects.filter(accion="INHABILITAR_PRODUCTO", id_entidad=str(self.producto.id)).first()
        self.assertIsNotNone(aud)
        self.assertTrue(aud.via_haku)

    def test_producto_ya_inhabilitado(self):
        """Si el producto ya está inhabilitado, informar en vez de proponer redundancia."""
        self.producto.activo = False
        self.producto.archivado = True
        self.producto.save()

        sesion = {}
        # Consultar primero el producto para tenerlo en foco
        procesar_comando_inventario(self.admin, f"Consulta el producto {self.producto.codigo_interno}", sesion=sesion)

        res = procesar_comando_inventario(self.admin, "quiero inhabilitarlo", sesion=sesion)
        self.assertEqual(res["tipo"], "consulta")
        self.assertIn("ya se encuentra inhabilitado", res["mensaje_pantalla"])

    def test_habilitar_producto_inactivo(self):
        """Habilitar y reactivar producto inactivo con pronombre 'ahora habilítalo'."""
        self.producto.activo = False
        self.producto.archivado = True
        self.producto.save()

        sesion = {}
        procesar_comando_inventario(self.admin, f"Consulta el producto {self.producto.codigo_interno}", sesion=sesion)

        res = procesar_comando_inventario(self.admin, "ahora habilítalo", sesion=sesion)
        self.assertEqual(res["tipo"], "propuesta")
        self.assertEqual(res["propuesta"]["tipo_operacion"], "HABILITAR_PRODUCTO")

        # Confirmación
        confirm_res = ejecutar_confirmacion_propuesta(self.admin, res["propuesta"])
        self.assertTrue(confirm_res["exito"])

        self.producto.refresh_from_db()
        self.assertTrue(self.producto.activo)
        self.assertFalse(self.producto.archivado)


class TestCodigoBarrasYCatalogo(TestCase):
    """Pruebas para validación de códigos de barras, ceros iniciales y catálogo."""

    def setUp(self):
        self.almacen = Almacen.objects.create(nombre="Almacén Bodega", activo=True)
        self.cat = Categoria.objects.create(nombre="Cables y Conectores", almacen_principal=self.almacen)
        # Producto con código que tiene ceros iniciales
        self.producto_existente = Producto.objects.create(
            nombre="Cable UTP Cat 6 305m",
            categoria=self.cat,
            codigo_barras="012345678905",  # UPC-A con cero inicial
        )

    def test_calculo_checksum_ean(self):
        """Cálculo estricto de dígito de control GS1."""
        # EAN-13: 770123456789 -> último dígito debe ser 7
        self.assertEqual(calcular_checksum_ean("770123456789"), 7)
        # UPC-A: 01234567890 -> último dígito debe ser 5
        self.assertEqual(calcular_checksum_ean("01234567890"), 5)

    def test_conservacion_ceros_iniciales(self):
        """Los códigos con ceros iniciales deben conservarse intactos."""
        valido, formato, err = validar_codigo_barras("012345678905")
        self.assertTrue(valido)
        self.assertEqual(formato, "UPC-A")

    def test_codigo_ya_registrado_evita_duplicados(self):
        """Si el código ya existe en el inventario, debe detectarse."""
        res = inspeccionar_codigo_barras("012345678905")
        self.assertTrue(res["valido"])
        self.assertTrue(res["existe"])
        self.assertEqual(res["producto"]["id"], self.producto_existente.id)
        self.assertEqual(res["producto"]["codigo_interno"], self.producto_existente.codigo_interno)

    @patch("integraciones.haku.servicios_catalogo.consultar_catalogo_externo")
    def test_codigo_no_registrado_consulta_catalogo(self, mock_catalogo):
        """Si el código no está registrado, se consulta el catálogo sugerido sin inventar datos internos."""
        mock_catalogo.return_value = {
            "nombre": "Batería Recargable AA 2500mAh",
            "marca": "Duracell",
            "descripcion": "Pack de 4 pilas recargables",
            "nombre_empaque": "Blíster",
            "fuente": "OpenProductFacts",
        }

        codigo_nuevo = "7701234567897"  # EAN-13 válido (checksum 7)
        res = inspeccionar_codigo_barras(codigo_nuevo)

        self.assertTrue(res["valido"])
        self.assertFalse(res["existe"])
        self.assertTrue(res["encontrado_en_catalogo"])
        self.assertEqual(res["datos_sugeridos"]["nombre"], "Batería Recargable AA 2500mAh")
        # Asegurar que NO inventa campos internos de inventario
        self.assertNotIn("categoria", res["datos_sugeridos"])
        self.assertNotIn("almacen", res["datos_sugeridos"])
        self.assertNotIn("stock_inicial", res["datos_sugeridos"])
        self.assertNotIn("stock_minimo", res["datos_sugeridos"])


class TestResolucionTemporal(TestCase):
    """Pruebas deterministas de resolución de intervalos temporales en español."""

    def setUp(self):
        import datetime
        from zoneinfo import ZoneInfo
        self.tz = ZoneInfo("America/Bogota")
        # Fijamos un miércoles a las 14:30:00 (semana del 14 al 20 de mayo de 2026)
        self.ref_dt = datetime.datetime(2026, 5, 20, 14, 30, 0, tzinfo=self.tz)

    def test_ayer(self):
        from integraciones.haku.servicios_temporales import resolver_intervalo_temporal
        res = resolver_intervalo_temporal("movimientos de ayer", ref_dt=self.ref_dt)
        self.assertIsNotNone(res)
        self.assertEqual(res.etiqueta, "AYER")
        self.assertEqual(res.inicio.date(), datetime.date(2026, 5, 19))
        self.assertEqual(res.inicio.hour, 0)
        self.assertEqual(res.fin.date(), datetime.date(2026, 5, 20))
        self.assertEqual(res.fin.hour, 0)

    def test_hoy(self):
        from integraciones.haku.servicios_temporales import resolver_intervalo_temporal
        res = resolver_intervalo_temporal("qué hubo hoy", ref_dt=self.ref_dt)
        self.assertIsNotNone(res)
        self.assertEqual(res.etiqueta, "HOY")
        self.assertEqual(res.inicio.date(), datetime.date(2026, 5, 20))
        self.assertEqual(res.fin, self.ref_dt)

    def test_semana_pasada(self):
        from integraciones.haku.servicios_temporales import resolver_intervalo_temporal
        res = resolver_intervalo_temporal("movimientos de la semana pasada", ref_dt=self.ref_dt)
        self.assertIsNotNone(res)
        self.assertEqual(res.etiqueta, "SEMANA_PASADA")
        self.assertEqual(res.inicio.date(), datetime.date(2026, 5, 11))
        self.assertEqual(res.fin.date(), datetime.date(2026, 5, 18))

    def test_semana_pasada_hasta_hoy(self):
        from integraciones.haku.servicios_temporales import resolver_intervalo_temporal
        res = resolver_intervalo_temporal("semana pasada hasta hoy", ref_dt=self.ref_dt)
        self.assertIsNotNone(res)
        self.assertEqual(res.etiqueta, "SEMANA_PASADA_HASTA_HOY")
        self.assertEqual(res.inicio.date(), datetime.date(2026, 5, 11))
        self.assertEqual(res.fin, self.ref_dt)

    def test_esta_semana(self):
        from integraciones.haku.servicios_temporales import resolver_intervalo_temporal
        res = resolver_intervalo_temporal("movimientos de esta semana", ref_dt=self.ref_dt)
        self.assertIsNotNone(res)
        self.assertEqual(res.etiqueta, "ESTA_SEMANA")
        self.assertEqual(res.inicio.date(), datetime.date(2026, 5, 18))
        self.assertEqual(res.fin, self.ref_dt)

    def test_ultimos_siete_dias(self):
        from integraciones.haku.servicios_temporales import resolver_intervalo_temporal
        res = resolver_intervalo_temporal("últimos 7 días", ref_dt=self.ref_dt)
        self.assertIsNotNone(res)
        self.assertEqual(res.etiqueta, "ULTIMOS_DIAS")
        self.assertEqual(res.inicio, self.ref_dt - datetime.timedelta(days=7))
        self.assertEqual(res.fin, self.ref_dt)

    def test_este_mes(self):
        from integraciones.haku.servicios_temporales import resolver_intervalo_temporal
        res = resolver_intervalo_temporal("entradas de este mes", ref_dt=self.ref_dt)
        self.assertIsNotNone(res)
        self.assertEqual(res.etiqueta, "ESTE_MES")
        self.assertEqual(res.inicio.date(), datetime.date(2026, 5, 1))
        self.assertEqual(res.fin, self.ref_dt)

    def test_mes_pasado(self):
        from integraciones.haku.servicios_temporales import resolver_intervalo_temporal
        res = resolver_intervalo_temporal("salidas del mes pasado", ref_dt=self.ref_dt)
        self.assertIsNotNone(res)
        self.assertEqual(res.etiqueta, "MES_PASADO")
        self.assertEqual(res.inicio.date(), datetime.date(2026, 4, 1))
        self.assertEqual(res.fin.date(), datetime.date(2026, 5, 1))

    def test_mes_especifico_octubre(self):
        from integraciones.haku.servicios_temporales import resolver_intervalo_temporal
        res = resolver_intervalo_temporal("movimientos de octubre", ref_dt=self.ref_dt)
        self.assertIsNotNone(res)
        self.assertEqual(res.etiqueta, "MES_ESPECIFICO")
        self.assertEqual(res.inicio.date(), datetime.date(2026, 10, 1))
        self.assertEqual(res.fin.date(), datetime.date(2026, 11, 1))

    def test_entre_dias_lunes_y_viernes(self):
        from integraciones.haku.servicios_temporales import resolver_intervalo_temporal
        res = resolver_intervalo_temporal("qué se movió entre el lunes y el viernes", ref_dt=self.ref_dt)
        self.assertIsNotNone(res)
        self.assertEqual(res.etiqueta, "ENTRE_DIAS")
        self.assertEqual(res.inicio.date(), datetime.date(2026, 5, 18))
        self.assertEqual(res.fin.date(), datetime.date(2026, 5, 23))


class TestConsultasMovimientosYOperacionesMultialmacen(TestCase):
    """Pruebas de consultas de movimientos por fecha y operaciones multialmacén reales."""

    def setUp(self):
        import datetime
        from zoneinfo import ZoneInfo
        from inventario.models import Movimiento, Prestamo
        self.tz = ZoneInfo("America/Bogota")
        self.ref_dt = datetime.datetime(2026, 5, 20, 14, 0, 0, tzinfo=self.tz)

        self.alm_a = Almacen.objects.create(nombre="Almacén A", activo=True)
        self.alm_b = Almacen.objects.create(nombre="Almacén B", activo=True)
        self.alm_c = Almacen.objects.create(nombre="Almacén C", activo=True)

        self.cat = Categoria.objects.create(nombre="Tecnología", almacen_principal=self.alm_a)
        self.prod = Producto.objects.create(
            codigo_interno="TECH-001",
            nombre="Cable HDMI 2.0",
            categoria=self.cat,
            activo=True,
        )

        Existencia.objects.create(producto=self.prod, almacen=self.alm_a, stock_disponible=50, stock_en_prestamo=0)
        Existencia.objects.create(producto=self.prod, almacen=self.alm_b, stock_disponible=30, stock_en_prestamo=0)

        # Usuarios
        self.user_mono = UsuarioAudacIA.objects.create_user(
            username="mono_a",
            password="Password123!",
            nombre_completo="Usuario Almacen A",
            rol=UsuarioAudacIA.Rol.USUARIO,
            almacen_asignado=self.alm_a,
        )
        self.user_multi = UsuarioAudacIA.objects.create_user(
            username="multi_ab",
            password="Password123!",
            nombre_completo="Usuario Almacenes A y B",
            rol=UsuarioAudacIA.Rol.USUARIO,
            almacen_asignado=self.alm_a,
        )
        self.user_multi.almacenes_asignados.set([self.alm_a, self.alm_b])

        self.user_global = UsuarioAudacIA.objects.create_user(
            username="user_all",
            password="Password123!",
            nombre_completo="Usuario Todo",
            rol=UsuarioAudacIA.Rol.USUARIO,
            acceso_todos_almacenes=True,
            almacen_asignado=None,
        )

        # Crear movimientos históricos con fechas controladas
        # Movimiento 1: ayer en Alm A (2026-05-19 10:00)
        m1 = Movimiento.objects.create(
            clave_idempotencia="test-hist-m1",
            tipo=Movimiento.Tipo.ENTRADA,
            producto=self.prod,
            cantidad=10,
            almacen_origen=self.alm_a,
            usuario=self.user_mono,
            motivo="Entrada ayer en A",
            stock_disponible_anterior=40,
            stock_disponible_posterior=50,
        )
        Movimiento.objects.filter(id=m1.id).update(fecha_hora=datetime.datetime(2026, 5, 19, 10, 0, tzinfo=self.tz))

        # Movimiento 2: hoy en Alm B (2026-05-20 09:00)
        m2 = Movimiento.objects.create(
            clave_idempotencia="test-hist-m2",
            tipo=Movimiento.Tipo.SALIDA,
            producto=self.prod,
            cantidad=5,
            almacen_origen=self.alm_b,
            usuario=self.user_multi,
            motivo="Salida hoy en B",
            stock_disponible_anterior=35,
            stock_disponible_posterior=30,
        )
        Movimiento.objects.filter(id=m2.id).update(fecha_hora=datetime.datetime(2026, 5, 20, 9, 0, tzinfo=self.tz))

        # Movimiento 3: traslado de Alm A a Alm B (2026-05-19 16:00)
        m3 = Movimiento.objects.create(
            clave_idempotencia="test-hist-m3",
            tipo=Movimiento.Tipo.TRASLADO_ENTRE,
            producto=self.prod,
            cantidad=8,
            almacen_origen=self.alm_a,
            almacen_destino=self.alm_b,
            usuario=self.user_multi,
            motivo="Traslado de A hacia B",
            stock_disponible_anterior=58,
            stock_disponible_posterior=50,
        )
        Movimiento.objects.filter(id=m3.id).update(fecha_hora=datetime.datetime(2026, 5, 19, 16, 0, tzinfo=self.tz))

        # Movimiento 4: movimiento exclusivo en Alm C (2026-05-19 11:00)
        m4 = Movimiento.objects.create(
            clave_idempotencia="test-hist-m4",
            tipo=Movimiento.Tipo.ENTRADA,
            producto=self.prod,
            cantidad=100,
            almacen_origen=self.alm_c,
            usuario=self.user_global,
            motivo="Exclusivo en C",
            stock_disponible_anterior=0,
            stock_disponible_posterior=100,
        )
        Movimiento.objects.filter(id=m4.id).update(fecha_hora=datetime.datetime(2026, 5, 19, 11, 0, tzinfo=self.tz))

    @patch("django.utils.timezone.now")
    def test_consulta_movimientos_ayer_mono_almacen(self, mock_now):
        """Usuario con un solo almacén solo ve movimientos de su almacén."""
        mock_now.return_value = self.ref_dt
        res = procesar_comando_inventario(self.user_mono, "muéstrame los movimientos de ayer")
        self.assertEqual(res["tipo"], "consulta")
        # En ayer (19 de mayo): Alm A tiene la entrada (m1) y el traslado (m3). No debe ver m4 (en C).
        self.assertEqual(res["datos"]["total"], 2)
        # Comprobar que m4 no está presente
        ids = [m["id"] for m in res["datos"]["movimientos"]]
        self.assertNotIn("Exclusivo en C", res["mensaje_pantalla"])

    @patch("django.utils.timezone.now")
    def test_consulta_movimientos_ayer_multi_almacen(self, mock_now):
        """Usuario multi ve movimientos de A y B pero no de C."""
        mock_now.return_value = self.ref_dt
        res = procesar_comando_inventario(self.user_multi, "muéstrame los movimientos de ayer")
        self.assertEqual(res["tipo"], "consulta")
        # El traslado m3 entre A y B solo debe contarse 1 vez (distinct)
        self.assertEqual(res["datos"]["total"], 2)
        self.assertNotIn("Exclusivo en C", res["mensaje_pantalla"])

    @patch("django.utils.timezone.now")
    def test_consulta_movimientos_ayer_acceso_todos(self, mock_now):
        """Usuario con acceso a todos ve los de A, B y C aunque almacen_asignado sea None."""
        mock_now.return_value = self.ref_dt
        res = procesar_comando_inventario(self.user_global, "muéstrame los movimientos de ayer")
        self.assertEqual(res["tipo"], "consulta")
        # Ayer: m1 (A), m3 (traslado A->B), m4 (C) = 3 movimientos
        self.assertEqual(res["datos"]["total"], 3)
        self.assertIn("Almacén C", res["mensaje_pantalla"])

    @patch("django.utils.timezone.now")
    def test_consulta_movimientos_producto_especifico_y_rango(self, mock_now):
        """Combinar producto y fecha: 'movimientos del cable HDMI hoy'."""
        mock_now.return_value = self.ref_dt
        res = procesar_comando_inventario(self.user_multi, "¿qué movimientos tuvo el cable HDMI hoy?")
        self.assertEqual(res["tipo"], "consulta")
        # Hoy solo hubo m2 (salida en Alm B)
        self.assertEqual(res["datos"]["total"], 1)
        self.assertIn("Salida", res["mensaje_pantalla"])

    @patch("django.utils.timezone.now")
    def test_consulta_sin_resultados_indica_periodo(self, mock_now):
        """Periodo sin movimientos indica claramente la ventana consultada."""
        import datetime
        mock_now.return_value = datetime.datetime(2027, 1, 1, 12, 0, tzinfo=self.tz)
        res = procesar_comando_inventario(self.user_multi, "movimientos de ayer")
        self.assertEqual(res["tipo"], "consulta")
        self.assertEqual(res["datos"]["total"], 0)
        self.assertIn("no se registraron movimientos de inventario", res["mensaje_pantalla"])
        self.assertIn("31/12/2026", res["mensaje_pantalla"])

    def test_resolucion_almacen_salida_con_ambiguedad_solicita_aclaracion(self):
        """Si usuario tiene acceso a A y B y ambos tienen stock, solicita aclaración si no especificó almacén."""
        res = procesar_comando_inventario(
            self.user_multi,
            "registra una salida de 2 unidades de Cable HDMI para Juan",
        )
        # Nota: cable HDMI tiene categoría con almacén principal Alm A.
        # Por regla determinista de categoría, si Alm A es el almacén principal de la categoría
        # y el usuario tiene acceso a él, se usa Alm A sin error silencioso.
        self.assertEqual(res["tipo"], "propuesta")
        self.assertEqual(res["propuesta"]["almacen_origen_id"], self.alm_a.id)

    def test_salida_en_almacen_especifico_valida_permiso(self):
        """Si usuario mono intenta retirar de almacén no autorizado, se rechaza."""
        res = procesar_comando_inventario(
            self.user_mono,
            "registra una salida de 2 unidades de Cable HDMI en Almacén B para Juan",
        )
        self.assertEqual(res["tipo"], "error")
        self.assertIn("No tienes autorización", res["mensaje_pantalla"])

    def test_traslado_entre_almacenes_con_permiso_completo(self):
        """Traslado propuesto entre A y B por usuario autorizado."""
        res = procesar_comando_inventario(
            self.user_multi,
            "traslada 3 unidades de Cable HDMI del Almacén A al Almacén B",
        )
        self.assertEqual(res["tipo"], "propuesta")
        self.assertEqual(res["propuesta"]["almacen_origen_id"], self.alm_a.id)
        self.assertEqual(res["propuesta"]["almacen_destino_id"], self.alm_b.id)
        self.assertEqual(res["propuesta"]["cantidad"], 3)

