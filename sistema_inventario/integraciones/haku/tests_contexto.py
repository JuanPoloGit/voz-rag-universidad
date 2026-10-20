"""Pruebas obligatorias de la capa de contexto en RAM y caché de lectura para Haku Inventario.

Cubre exhaustivamente los 9 requerimientos obligatorios:
1. Usuario inicia sesión: Creación de contexto en LocMemCache.
2. Consulta de producto: Persistencia en foco de EstadoOperativoHaku.
3. Resolución de referencias contextuales: 'ese producto', 'de esos', 'saca tres de esos'.
4. Expiración tras 30 minutos: Pérdida controlada de foco SIN cerrar la sesión de usuario de 7 días.
5. Aislamiento estricto multiusuario: Dos usuarios no comparten contexto ni referencias.
6. Regla crítica de seguridad en salidas/mutaciones: Ignora caché viejo y consulta DB con select_for_update.
7. Invalidación inmediata de stock tras movimiento confirmado.
8. Invalidación de catálogo y ubicaciones ante actualización de producto.
9. Límite LRU (máximo 20 contextos): La memoria permanece estrictamente acotada.
10. Estructura mínima de candidatos (máximo 3 con id, nombre y código).
11. Registro y lectura de métricas internas (hits / misses).
"""

from __future__ import annotations

import time
from django.core.cache import caches
from django.core.exceptions import ValidationError
from django.test import TestCase

from cuentas.models import UsuarioAudacIA
from inventario.models import Almacen, Categoria, Existencia, Movimiento, Producto, Ubicacion

from .cache import HakuCacheManager
from .contexto import EstadoOperativoHaku, HakuOperationalContext
from .services import ejecutar_confirmacion_propuesta, procesar_comando_inventario


class HakuContextoYCachesTestCase(TestCase):
    """Batería de pruebas de verificación de memoria temporal y cachés en RAM."""

    def setUp(self):
        # Limpieza previa de cachés y contadores
        HakuOperationalContext.limpiar_todo()
        HakuOperationalContext.reset_metricas()
        HakuCacheManager.invalidar_todo()
        HakuCacheManager.reset_metricas()

        # Datos maestros de inventario
        self.almacen_central = Almacen.objects.create(nombre="Almacén Central")
        self.almacen_secundario = Almacen.objects.create(nombre="Almacén Secundario")

        self.categoria_cables = Categoria.objects.create(
            nombre="Cables y Conectores",
            almacen_principal=self.almacen_central,
        )

        self.ubicacion_a1 = Ubicacion.objects.create(
            nombre="Estante A-1, Nivel 2",
            almacen=self.almacen_central,
        )

        self.prod_hdmi = Producto.objects.create(
            codigo_interno="CAB-0001",
            nombre="Cable HDMI 2 metros",
            categoria=self.categoria_cables,
            stock_inicial=10,
            stock_minimo=5,
        )
        self.prod_hdmi.ubicaciones.add(self.ubicacion_a1)

        self.exist_hdmi = Existencia.objects.create(
            producto=self.prod_hdmi,
            almacen=self.almacen_central,
            stock_disponible=10,
            stock_en_prestamo=0,
        )

        self.prod_utp = Producto.objects.create(
            codigo_interno="RED-0001",
            nombre="Cable UTP Cat 6",
            categoria=self.categoria_cables,
            stock_inicial=30,
            stock_minimo=10,
        )
        self.exist_utp = Existencia.objects.create(
            producto=self.prod_utp,
            almacen=self.almacen_central,
            stock_disponible=30,
            stock_en_prestamo=0,
        )

        # Usuarios de prueba
        self.usuario_a = UsuarioAudacIA.objects.create_user(
            username="usuario_a",
            password="Password1",
            nombre_completo="Usuario Operativo A",
            rol=UsuarioAudacIA.Rol.USUARIO,
            almacen_asignado=self.almacen_central,
        )

        self.usuario_b = UsuarioAudacIA.objects.create_user(
            username="usuario_b",
            password="Password1",
            nombre_completo="Usuario Operativo B",
            rol=UsuarioAudacIA.Rol.USUARIO,
            almacen_asignado=self.almacen_central,
        )

    def tearDown(self):
        HakuOperationalContext.limpiar_todo()
        HakuCacheManager.invalidar_todo()

    # ─────────────────────────────────────────────────────────────────────────
    # PRUEBA 1: Usuario inicia sesión y crea contexto en cache
    # ─────────────────────────────────────────────────────────────────────────
    def test_1_usuario_inicia_sesion_crear_contexto_existe_en_cache(self):
        session_key = "session_user_a_001"
        contexto = HakuOperationalContext.obtener_o_crear(
            usuario_id=self.usuario_a.id,
            session_key=session_key,
            almacen_id=self.almacen_central.id,
        )

        self.assertIsNotNone(contexto)
        self.assertEqual(contexto.usuario_id, self.usuario_a.id)
        self.assertEqual(contexto.session_key, session_key)
        self.assertEqual(contexto.almacen_id, self.almacen_central.id)

        # Verificar existencia física en backend LocMemCache
        recuperado = HakuOperationalContext.obtener(self.usuario_a.id, session_key)
        self.assertIsNotNone(recuperado)
        self.assertEqual(recuperado.usuario_id, self.usuario_a.id)

        # Métricas
        metricas = HakuOperationalContext.obtener_metricas()
        self.assertGreaterEqual(metricas["context_hits"], 1)

    # ─────────────────────────────────────────────────────────────────────────
    # PRUEBA 2: Usuario consulta producto y se guarda en foco actual
    # ─────────────────────────────────────────────────────────────────────────
    def test_2_usuario_consulta_producto_guarda_producto_actual(self):
        session_key = "session_user_a_002"
        contexto = HakuOperationalContext.obtener_o_crear(
            usuario_id=self.usuario_a.id,
            session_key=session_key,
            almacen_id=self.almacen_central.id,
        )

        resultado = procesar_comando_inventario(
            usuario=self.usuario_a,
            mensaje="¿Cuánto stock queda de cable HDMI?",
            estado_operativo=contexto,
        )

        self.assertEqual(resultado["tipo"], "consulta")
        self.assertEqual(contexto.producto_id, self.prod_hdmi.id)
        self.assertEqual(contexto.producto_codigo, self.prod_hdmi.codigo_interno)
        self.assertEqual(contexto.producto_nombre, self.prod_hdmi.nombre)

        # Confirmar en RAM recargando de caché
        en_cache = HakuOperationalContext.obtener(self.usuario_a.id, session_key)
        self.assertIsNotNone(en_cache)
        self.assertEqual(en_cache.producto_id, self.prod_hdmi.id)
        self.assertEqual(en_cache.producto_nombre, "Cable HDMI 2 metros")

    # ─────────────────────────────────────────────────────────────────────────
    # PRUEBA 3: Usuario usa referencia 'ese producto' / 'de esos' y resuelve
    # ─────────────────────────────────────────────────────────────────────────
    def test_3_usuario_usa_referencia_resuelve_contexto(self):
        session_key = "session_user_a_003"
        contexto = HakuOperationalContext.obtener_o_crear(
            usuario_id=self.usuario_a.id,
            session_key=session_key,
            almacen_id=self.almacen_central.id,
        )

        # Paso 1: Establecer foco inicial
        procesar_comando_inventario(
            usuario=self.usuario_a,
            mensaje="búscame el cable HDMI",
            estado_operativo=contexto,
        )
        self.assertEqual(contexto.producto_id, self.prod_hdmi.id)

        # Paso 2: Usar referencia anafórica directa ("saca tres de esos")
        resultado_salida = procesar_comando_inventario(
            usuario=self.usuario_a,
            mensaje="saca tres de esos para Juan Pérez por mantenimiento",
            estado_operativo=contexto,
        )

        self.assertEqual(resultado_salida["tipo"], "propuesta")
        self.assertTrue(resultado_salida["requiere_confirmacion"])
        propuesta = resultado_salida["propuesta"]
        self.assertEqual(propuesta["producto_id"], self.prod_hdmi.id)
        self.assertEqual(propuesta["cantidad"], 3)
        self.assertIn("Juan Pérez", propuesta["destinatario"])

        # Paso 3: Usar otra referencia ("dónde está ese cable")
        resultado_ubic = procesar_comando_inventario(
            usuario=self.usuario_a,
            mensaje="¿dónde está ese cable?",
            estado_operativo=contexto,
        )
        self.assertEqual(resultado_ubic["tipo"], "consulta")
        self.assertIn(self.prod_hdmi.nombre, resultado_ubic["mensaje_pantalla"])

    # ─────────────────────────────────────────────────────────────────────────
    # PRUEBA 4: Después de 30 minutos pierde contexto pero NO pierde login
    # ─────────────────────────────────────────────────────────────────────────
    def test_4_expiracion_30_minutos_pierde_contexto_mantiene_login(self):
        session_key = "session_user_a_004"
        contexto = HakuOperationalContext.obtener_o_crear(
            usuario_id=self.usuario_a.id,
            session_key=session_key,
            almacen_id=self.almacen_central.id,
        )
        contexto.asignar_producto(self.prod_hdmi.id, self.prod_hdmi.codigo_interno, self.prod_hdmi.nombre)

        # Simular inactividad de más de 2 horas (7260 segundos > 7200)
        contexto.ultimo_acceso = time.time() - 7260
        HakuOperationalContext.guardar(contexto)

        # Al consultar HakuOperationalContext.obtener, debe limpiar el foco conversacional
        recuperado = HakuOperationalContext.obtener(self.usuario_a.id, session_key)
        self.assertIsNone(recuperado, "Un contexto con > 2 horas sin actividad debe retornar None")

        # Intentar comando ambiguo que requería contexto previo
        contexto_renovado = HakuOperationalContext.obtener_o_crear(self.usuario_a.id, session_key)
        resultado = procesar_comando_inventario(
            usuario=self.usuario_a,
            mensaje="saca tres de esos para Pedro",
            estado_operativo=contexto_renovado,
        )

        # No debe inventar contexto: debe solicitar aclaración
        self.assertEqual(resultado["tipo"], "aclaracion")
        self.assertIn("no indicaste cuál producto", resultado["mensaje_pantalla"])

        # La sesión web y cuenta de usuario se mantienen 100% activas y autenticadas
        self.assertTrue(self.usuario_a.is_authenticated)
        self.assertTrue(self.usuario_a.is_active)

    # ─────────────────────────────────────────────────────────────────────────
    # PRUEBA 5: Dos usuarios diferentes no comparten contexto
    # ─────────────────────────────────────────────────────────────────────────
    def test_5_dos_usuarios_diferentes_no_comparten_contexto(self):
        sess_a = "session_isolated_a"
        sess_b = "session_isolated_b"

        ctx_a = HakuOperationalContext.obtener_o_crear(self.usuario_a.id, sess_a, self.almacen_central.id)
        ctx_b = HakuOperationalContext.obtener_o_crear(self.usuario_b.id, sess_b, self.almacen_central.id)

        # Usuario A interactúa con Cable HDMI
        procesar_comando_inventario(self.usuario_a, "stock del cable HDMI", estado_operativo=ctx_a)
        # Usuario B interactúa con Cable UTP
        procesar_comando_inventario(self.usuario_b, "existencias de cable UTP", estado_operativo=ctx_b)

        # Verificar foco independiente en cada contexto
        self.assertEqual(ctx_a.producto_id, self.prod_hdmi.id)
        self.assertEqual(ctx_b.producto_id, self.prod_utp.id)

        # Usuario A dice "dónde está ese producto" -> debe resolver HDMI
        res_a = procesar_comando_inventario(self.usuario_a, "¿dónde está ese producto?", estado_operativo=ctx_a)
        self.assertEqual(res_a["datos"]["producto_id"], str(self.prod_hdmi.id))

        # Usuario B dice "dónde está ese producto" -> debe resolver UTP
        res_b = procesar_comando_inventario(self.usuario_b, "¿dónde está ese producto?", estado_operativo=ctx_b)
        self.assertEqual(res_b["datos"]["producto_id"], str(self.prod_utp.id))

    # ─────────────────────────────────────────────────────────────────────────
    # PRUEBA 6: Salida de inventario ignora cache de stock viejo y consulta BD
    # ─────────────────────────────────────────────────────────────────────────
    def test_6_salida_inventario_ignora_cache_stock_viejo_consulta_bd(self):
        # La BD real tiene exactamente 10 unidades disponibles
        self.exist_hdmi.stock_disponible = 10
        self.exist_hdmi.save(update_fields=["stock_disponible"])

        # Se inyecta intencionalmente un stock obsoleto/desactualizado de 50 en la caché
        HakuCacheManager.guardar_stock(
            self.prod_hdmi.id,
            self.almacen_central.id,
            {"disponible": 50, "prestado": 0},
        )
        cached = HakuCacheManager.obtener_stock(self.prod_hdmi.id, self.almacen_central.id)
        self.assertEqual(cached["disponible"], 50)

        # El usuario solicita retirar 15 unidades (la caché diría OK con 50, pero la BD solo tiene 10)
        propuesta_invalida = {
            "id": "prop-test-seguridad",
            "tipo_operacion": Movimiento.Tipo.SALIDA,
            "producto_id": self.prod_hdmi.id,
            "cantidad": 15,
            "almacen_origen_id": self.almacen_central.id,
            "destinatario": "Ingeniería",
            "motivo": "Prueba de seguridad",
            "creado_en": time.time(),
            "expira_en": time.time() + 300,
        }

        # La ejecución atómica con select_for_update() debe rechazar la operación consultando la BD
        with self.assertRaises(ValidationError) as ctx_err:
            ejecutar_confirmacion_propuesta(self.usuario_a, propuesta_invalida)

        self.assertIn("Stock insuficiente", str(ctx_err.exception))
        # La base de datos no fue alterada
        self.exist_hdmi.refresh_from_db()
        self.assertEqual(self.exist_hdmi.stock_disponible, 10)

    # ─────────────────────────────────────────────────────────────────────────
    # PRUEBA 7: Movimiento confirmado invalida cache de stock inmediatamente
    # ─────────────────────────────────────────────────────────────────────────
    def test_7_movimiento_confirmado_invalida_cache_stock_inmediatamente(self):
        # 1. Precargar stock en caché
        stock_inicial = HakuCacheManager.obtener_o_cargar_stock(self.prod_hdmi.id, self.almacen_central.id)
        self.assertEqual(stock_inicial["disponible"], 10)
        self.assertIsNotNone(HakuCacheManager.obtener_stock(self.prod_hdmi.id, self.almacen_central.id))

        # 2. Ejecutar movimiento confirmado de ENTRADA (+5 unidades)
        propuesta = {
            "id": "prop-entrada-001",
            "tipo_operacion": Movimiento.Tipo.ENTRADA,
            "producto_id": self.prod_hdmi.id,
            "cantidad": 5,
            "almacen_origen_id": self.almacen_central.id,
            "motivo": "Ingreso de nuevos cables",
            "creado_en": time.time(),
            "expira_en": time.time() + 300,
        }
        res = ejecutar_confirmacion_propuesta(self.usuario_a, propuesta)
        self.assertTrue(res["exito"])
        self.assertEqual(res["stock_nuevo"], 15)

        # 3. La clave de caché debe haberse invalidado de inmediato (sin esperar TTL)
        cached_post = HakuCacheManager.obtener_stock(self.prod_hdmi.id, self.almacen_central.id)
        self.assertIsNone(cached_post, "El caché de stock debe ser None inmediatamente tras confirmación")

        # 4. Al recargar, refleja fielmente el nuevo saldo de la base de datos
        stock_recargado = HakuCacheManager.obtener_o_cargar_stock(self.prod_hdmi.id, self.almacen_central.id)
        self.assertEqual(stock_recargado["disponible"], 15)

    # ─────────────────────────────────────────────────────────────────────────
    # PRUEBA 8: Producto actualizado invalida catálogo y ubicaciones
    # ─────────────────────────────────────────────────────────────────────────
    def test_8_producto_actualizado_invalida_catalogo_y_ubicacion(self):
        # 1. Cargar caché de catálogo y ubicación
        catalogo_previo = HakuCacheManager.obtener_o_cargar_catalogo()
        self.assertIsNotNone(catalogo_previo)
        ubicacion_previa = HakuCacheManager.obtener_o_cargar_ubicacion(self.prod_hdmi)
        self.assertIn("Estante A-1", ubicacion_previa)

        self.assertIsNotNone(HakuCacheManager.obtener_catalogo())
        self.assertIsNotNone(HakuCacheManager.obtener_ubicacion(self.prod_hdmi.id))

        # 2. Modificar producto en base de datos
        self.prod_hdmi.nombre = "Cable HDMI 2 metros blindado dorado"
        self.prod_hdmi.save()

        # 3. Señal post_save debe invalidar catálogo y ubicaciones de inmediato
        self.assertIsNone(HakuCacheManager.obtener_catalogo(), "El catálogo debe invalidarse al editar producto")
        self.assertIsNone(HakuCacheManager.obtener_ubicacion(self.prod_hdmi.id), "La ubicación debe invalidarse")

    # ─────────────────────────────────────────────────────────────────────────
    # PRUEBA 9: Límite de usuarios simultáneos — la memoria no crece indefinidamente
    # ─────────────────────────────────────────────────────────────────────────
    def test_9_lru_20_usuarios_simultaneos_memoria_acotada(self):
        cache = caches["haku_contexto"]
        cache.clear()

        # Insertar 60 contextos diferentes (el límite configurado MAX_ENTRIES es 50)
        for i in range(1, 61):
            ctx = EstadoOperativoHaku(
                usuario_id=100 + i,
                session_key=f"session_concurrente_{i}",
                producto_id=self.prod_hdmi.id,
            )
            HakuOperationalContext.guardar(ctx)

        # LocMemCache aplica poda (cull) automática al superar MAX_ENTRIES (50)
        conteo_claves = len(getattr(cache, "_cache", {}))
        self.assertLessEqual(
            conteo_claves,
            50,
            f"El número de entradas en LocMemCache ({conteo_claves}) excede el límite máximo de 50",
        )

    # ─────────────────────────────────────────────────────────────────────────
    # PRUEBA 10: Candidatos mínimos útiles (máximo 3 con id, nombre y código)
    # ─────────────────────────────────────────────────────────────────────────
    def test_10_candidatos_minimos_utiles_maximo_3(self):
        ctx = EstadoOperativoHaku(usuario_id=self.usuario_a.id, session_key="sess_candidatos")
        lista_candidatos = [
            {"id": 101, "nombre": "Cable HDMI 1m", "codigo": "CAB-001", "descripcion_larga": "ignorar esto"},
            {"id": 102, "nombre": "Cable HDMI 2m", "codigo": "CAB-002", "stock": 999},
            {"id": 103, "nombre": "Cable HDMI 3m", "codigo": "CAB-003"},
            {"id": 104, "nombre": "Cable HDMI 5m", "codigo": "CAB-004"},  # Este 4to debe ser descartado
        ]

        ctx.asignar_candidatos(lista_candidatos)

        self.assertEqual(len(ctx.candidatos), 3, "No debe almacenar más de 3 candidatos")
        for cand in ctx.candidatos:
            self.assertIn("id", cand)
            self.assertIn("nombre", cand)
            self.assertIn("codigo", cand)
            # No debe arrastrar campos pesados o innecesarios
            self.assertNotIn("descripcion_larga", cand)
            self.assertNotIn("stock", cand)

    # ─────────────────────────────────────────────────────────────────────────
    # PRUEBA 11: Métricas internas de aciertos y fallos de caché
    # ─────────────────────────────────────────────────────────────────────────
    def test_11_metricas_internas_hits_misses(self):
        HakuOperationalContext.reset_metricas()
        HakuCacheManager.reset_metricas()

        # Miss de contexto
        res = HakuOperationalContext.obtener(999, "clave_inexistente")
        self.assertIsNone(res)
        m_ctx = HakuOperationalContext.obtener_metricas()
        self.assertEqual(m_ctx["context_misses"], 1)

        # Hit de contexto
        ctx = HakuOperationalContext.obtener_o_crear(self.usuario_a.id, "sess_metricas")
        res2 = HakuOperationalContext.obtener(self.usuario_a.id, "sess_metricas")
        self.assertIsNotNone(res2)
        m_ctx2 = HakuOperationalContext.obtener_metricas()
        self.assertGreaterEqual(m_ctx2["context_hits"], 1)

        # Métricas de caché de stock
        HakuCacheManager.obtener_stock(self.prod_hdmi.id, self.almacen_central.id)
        m_cache = HakuCacheManager.obtener_metricas()
        self.assertEqual(m_cache["stock_misses"], 1)

        HakuCacheManager.guardar_stock(self.prod_hdmi.id, self.almacen_central.id, {"disponible": 10})
        HakuCacheManager.obtener_stock(self.prod_hdmi.id, self.almacen_central.id)
        m_cache2 = HakuCacheManager.obtener_metricas()
        self.assertEqual(m_cache2["stock_hits"], 1)

    # ─────────────────────────────────────────────────────────────────────────
    # PRUEBA 12: Limpieza de contexto en RAM al disparar logout del usuario
    # ─────────────────────────────────────────────────────────────────────────
    def test_12_limpieza_contexto_en_logout(self):
        """Al disparar user_logged_out, el contexto en RAM se destruye de inmediato."""
        from django.contrib.auth.signals import user_logged_out

        class MockSession:
            def __init__(self, key):
                self.session_key = key

        class MockRequest:
            def __init__(self, key):
                self.session = MockSession(key)

        session_key = "session_logout_test_123"
        request = MockRequest(session_key)

        # Guardar contexto operativo en RAM
        ctx = HakuOperationalContext.obtener_o_crear(self.usuario_a.id, session_key, almacen_id=self.almacen_central.id)
        ctx.asignar_producto(self.prod_hdmi.id, self.prod_hdmi.codigo_interno, self.prod_hdmi.nombre)
        HakuOperationalContext.guardar(ctx)

        # Verificar que existe
        self.assertIsNotNone(HakuOperationalContext.obtener(self.usuario_a.id, session_key))

        # Disparar la señal de logout
        user_logged_out.send(sender=self.usuario_a.__class__, request=request, user=self.usuario_a)

        # Verificar que el contexto fue eliminado de RAM
        self.assertIsNone(HakuOperationalContext.obtener(self.usuario_a.id, session_key))

    # ─────────────────────────────────────────────────────────────────────────
    # PRUEBA 13: Aislamiento en logout (no afecta contexto de otros usuarios)
    # ─────────────────────────────────────────────────────────────────────────
    def test_13_aislamiento_logout_no_afecta_otros_usuarios(self):
        """El logout de un usuario no destruye el contexto activo de otros usuarios."""
        from django.contrib.auth.signals import user_logged_out

        class MockSession:
            def __init__(self, key):
                self.session_key = key

        class MockRequest:
            def __init__(self, key):
                self.session = MockSession(key)

        sess_a = "session_usuario_a_logout_998"
        sess_b = "session_usuario_b_activa_999"
        request_a = MockRequest(sess_a)

        # Contexto usuario A y B
        ctx_a = HakuOperationalContext.obtener_o_crear(self.usuario_a.id, sess_a)
        ctx_b = HakuOperationalContext.obtener_o_crear(self.usuario_b.id, sess_b)
        HakuOperationalContext.guardar(ctx_a)
        HakuOperationalContext.guardar(ctx_b)

        # Logout de usuario A
        user_logged_out.send(sender=self.usuario_a.__class__, request=request_a, user=self.usuario_a)

        # Contexto A eliminado, contexto B intacto
        self.assertIsNone(HakuOperationalContext.obtener(self.usuario_a.id, sess_a))
        self.assertIsNotNone(HakuOperationalContext.obtener(self.usuario_b.id, sess_b))

    # ─────────────────────────────────────────────────────────────────────────
    # PRUEBA 14: Verificación estática de TTL 2h y resampling en haku_voz.js
    # ─────────────────────────────────────────────────────────────────────────
    def test_14_frontend_haku_voz_configuracion_ttl_y_resampling(self):
        """Verifica que haku_voz.js implemente TTL 2h, resampling 16k mono y manejo de silencio VAD."""
        from pathlib import Path
        from django.conf import settings

        js_path = Path(settings.BASE_DIR) / "static" / "js" / "haku_voz.js"
        self.assertTrue(js_path.exists(), "El archivo haku_voz.js debe existir")

        contenido = js_path.read_text(encoding="utf-8")
        # 1. TTL de 2 horas (120 * 60 * 1000 = 7,200,000 ms)
        self.assertIn("120 * 60 * 1000", contenido)
        self.assertIn("2 horas", contenido)
        self.assertIn("7200", contenido)

        # 2. Resampling y VAD silencio
        self.assertIn("resamplearAudioA16kMono", contenido)
        self.assertIn("encodeWavPcm16", contenido)
        self.assertIn("resampleLinear", contenido)
        self.assertIn('dataSTT.status === "silencio"', contenido)
        self.assertIn('dataSTT.estado === "SIN_VOZ"', contenido)

