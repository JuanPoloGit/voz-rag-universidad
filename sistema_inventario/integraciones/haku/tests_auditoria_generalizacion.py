"""Batería de pruebas de auditoría de generalización, validación de colecciones,
semántica de stock y barge-in de Haku.

Verifica:
1. Generalización del intérprete (las 7 frases clave y variaciones colombianas/costeñas).
2. Prevención de asignación arbitraria en expresiones ambiguas.
3. Gestión rigurosa de colecciones (>10 productos, advertencia explícita de truncado,
   limpieza por cambio de tema o expiración, aislamiento de sesiones).
4. Semántica exacta de stock (disponible == 0 con/sin préstamo, bajo stock estricto,
   stock igual al mínimo, consulta en otros almacenes según permisos).
5. Invariantes de barge-in e interrupción de audio en cliente.
"""

from __future__ import annotations

import time
from pathlib import Path
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase

from inventario.models import Almacen, Categoria, Existencia, Movimiento, Prestamo, Producto, Ubicacion

from .cache import HakuCacheManager
from .contexto import EstadoOperativoHaku, HakuOperationalContext
from .lenguaje.normalizador import IntencionResultado, IntencionTipo, NormalizadorLenguajeHaku
from .services import procesar_comando_inventario


class AuditoriaGeneralizacionHakuTestCase(TestCase):
    """Pruebas exhaustivas de generalización, colecciones, stock y barge-in."""

    def setUp(self):
        User = get_user_model()
        HakuOperationalContext.limpiar_todo()
        HakuCacheManager.invalidar_todo()

        # Almacenes
        self.almacen_principal = Almacen.objects.create(nombre="Almacén Central")
        self.almacen_secundario = Almacen.objects.create(nombre="Bodega Norte")

        # Categoría y Ubicaciones
        self.categoria = Categoria.objects.create(
            nombre="Equipos de Red",
            almacen_principal=self.almacen_principal,
        )
        self.ubicacion_a = Ubicacion.objects.create(
            nombre="Pasillo 1 - Estante A",
            almacen=self.almacen_principal,
        )
        self.ubicacion_b = Ubicacion.objects.create(
            nombre="Pasillo 4 - Gaveta 2",
            almacen=self.almacen_secundario,
        )

        # Usuarios
        self.admin = User.objects.create_user(
            username="admin_auditoria",
            email="admin@audacia.local",
            password="password123",
            nombre_completo="Admin Auditoria",
            rol="ADMINISTRADOR",
        )
        self.operario = User.objects.create_user(
            username="operario_auditoria",
            email="operario@audacia.local",
            password="password123",
            nombre_completo="Operario Auditoria",
            rol="USUARIO",
            almacen_asignado=self.almacen_principal,
        )

        # Productos de prueba
        # 1. Switch Gigabit: stock_disponible=10, min=5 (stock normal)
        self.prod_switch = Producto.objects.create(
            codigo_interno="SW-101",
            nombre="Switch Gigabit 24 Puertos",
            categoria=self.categoria,
            stock_minimo=5,
        )
        self.prod_switch.ubicaciones.add(self.ubicacion_a)
        self.exist_switch = Existencia.objects.create(
            producto=self.prod_switch,
            almacen=self.almacen_principal,
            stock_disponible=10,
            stock_en_prestamo=0,
        )

        # 2. Router VPN: stock_disponible=0, prestamo=0, min=2 (agotado puro)
        self.prod_router = Producto.objects.create(
            codigo_interno="RT-202",
            nombre="Router VPN Balanceador",
            categoria=self.categoria,
            stock_minimo=2,
        )
        self.prod_router.ubicaciones.add(self.ubicacion_a)
        self.exist_router = Existencia.objects.create(
            producto=self.prod_router,
            almacen=self.almacen_principal,
            stock_disponible=0,
            stock_en_prestamo=0,
        )

        # 3. Access Point: stock_disponible=0, prestamo=4, min=3 (agotado disponible pero con unidades prestadas)
        self.prod_ap = Producto.objects.create(
            codigo_interno="AP-303",
            nombre="Access Point WiFi 6",
            categoria=self.categoria,
            stock_minimo=3,
        )
        self.prod_ap.ubicaciones.add(self.ubicacion_a)
        self.exist_ap = Existencia.objects.create(
            producto=self.prod_ap,
            almacen=self.almacen_principal,
            stock_disponible=0,
            stock_en_prestamo=4,
        )

        # 4. Patch Cord: stock_disponible=2, min=5 (bajo stock estricto: 0 < 2 < 5)
        self.prod_patch = Producto.objects.create(
            codigo_interno="PC-404",
            nombre="Patch Cord Cat 6A 1m",
            categoria=self.categoria,
            stock_minimo=5,
        )
        self.prod_patch.ubicaciones.add(self.ubicacion_a)
        self.exist_patch = Existencia.objects.create(
            producto=self.prod_patch,
            almacen=self.almacen_principal,
            stock_disponible=2,
            stock_en_prestamo=0,
        )

        # 5. Conector RJ45: stock_disponible=10, min=10 (igual al mínimo: no es bajo stock)
        self.prod_rj45 = Producto.objects.create(
            codigo_interno="RJ-505",
            nombre="Conector RJ45 Blindado",
            categoria=self.categoria,
            stock_minimo=10,
        )
        self.prod_rj45.ubicaciones.add(self.ubicacion_a)
        self.exist_rj45 = Existencia.objects.create(
            producto=self.prod_rj45,
            almacen=self.almacen_principal,
            stock_disponible=10,
            stock_en_prestamo=0,
        )

        # 6. Fibra Óptica: agotado en Central (0), pero disponible en Norte (8)
        self.prod_fibra = Producto.objects.create(
            codigo_interno="FO-606",
            nombre="Rollo Fibra Óptica Monomodo",
            categoria=self.categoria,
            stock_minimo=2,
        )
        self.prod_fibra.ubicaciones.add(self.ubicacion_b)
        self.exist_fibra_central = Existencia.objects.create(
            producto=self.prod_fibra,
            almacen=self.almacen_principal,
            stock_disponible=0,
            stock_en_prestamo=0,
        )
        self.exist_fibra_norte = Existencia.objects.create(
            producto=self.prod_fibra,
            almacen=self.almacen_secundario,
            stock_disponible=8,
            stock_en_prestamo=0,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # 1. AUDITORÍA DE GENERALIZACIÓN DEL INTÉRPRETE
    # ─────────────────────────────────────────────────────────────────────────

    def test_frase_1_ubicar_referencias_mencionadas(self):
        """«Ubícame las referencias que acabas de mencionar» -> CONSULTAR_UBICACION_COLECCION."""
        normalizador = NormalizadorLenguajeHaku()
        # Contexto con colección activa
        contexto = {
            "haku_coleccion_productos": [
                {"id": self.prod_switch.id, "nombre": self.prod_switch.nombre},
                {"id": self.prod_patch.id, "nombre": self.prod_patch.nombre},
            ]
        }
        res = NormalizadorLenguajeHaku.interpretar(
            mensaje="Ubícame las referencias que acabas de mencionar",
            usuario=self.admin,
            contexto_sesion=contexto,
        )
        self.assertEqual(res.tipo, IntencionTipo.CONSULTAR_UBICACION_COLECCION)

    def test_frase_2_bodegas_donde_tenemos_guardados_articulos(self):
        """«¿En cuáles bodegas tenemos guardados esos artículos?» -> CONSULTAR_UBICACION_COLECCION."""
        contexto = {
            "haku_coleccion_productos": [
                {"id": self.prod_router.id, "nombre": self.prod_router.nombre},
            ]
        }
        res = NormalizadorLenguajeHaku.interpretar(
            mensaje="¿En cuáles bodegas tenemos guardados esos artículos?",
            usuario=self.admin,
            contexto_sesion=contexto,
        )
        self.assertEqual(res.tipo, IntencionTipo.CONSULTAR_UBICACION_COLECCION)

    def test_frase_3_cuales_de_esos_ya_se_acabaron(self):
        """«¿Cuáles de esos ya se acabaron?» -> PRODUCTOS_SIN_STOCK con filtrar_coleccion."""
        contexto = {
            "haku_coleccion_productos": [
                {"id": self.prod_switch.id, "nombre": self.prod_switch.nombre},
                {"id": self.prod_router.id, "nombre": self.prod_router.nombre},
            ]
        }
        res = NormalizadorLenguajeHaku.interpretar(
            mensaje="¿Cuáles de esos ya se acabaron?",
            usuario=self.admin,
            contexto_sesion=contexto,
        )
        self.assertEqual(res.tipo, IntencionTipo.PRODUCTOS_SIN_STOCK)
        self.assertTrue(res.datos_extra.get("filtrar_coleccion"))

    def test_frase_4_por_debajo_de_lo_que_deberiamos_tener(self):
        """«Muéstrame los que están por debajo de lo que deberíamos tener» -> PRODUCTOS_BAJO_STOCK."""
        res = NormalizadorLenguajeHaku.interpretar(
            mensaje="Muéstrame los que están por debajo de lo que deberíamos tener",
            usuario=self.admin,
        )
        self.assertEqual(res.tipo, IntencionTipo.PRODUCTOS_BAJO_STOCK)

    def test_frase_5_del_otro_almacen_que_queda(self):
        """«Y del otro almacén, ¿qué queda?» con producto en contexto -> CONSULTAR_STOCK (otro_almacen)."""
        contexto = {
            "haku_ultimo_producto_id": str(self.prod_fibra.id),
            "haku_ultimo_producto_nombre": self.prod_fibra.nombre,
        }
        res = NormalizadorLenguajeHaku.interpretar(
            mensaje="Y del otro almacén, ¿qué queda?",
            usuario=self.admin,
            contexto_sesion=contexto,
        )
        self.assertEqual(res.tipo, IntencionTipo.CONSULTAR_STOCK)
        self.assertTrue(res.datos_extra.get("otro_almacen"))
        self.assertEqual(res.producto.id, self.prod_fibra.id)

    def test_frase_6_eso_donde_lo_guardamos(self):
        """«¿Eso dónde lo guardamos?» con producto en contexto -> CONSULTAR_UBICACION."""
        contexto = {
            "haku_ultimo_producto_id": str(self.prod_switch.id),
            "haku_ultimo_producto_nombre": self.prod_switch.nombre,
        }
        res = NormalizadorLenguajeHaku.interpretar(
            mensaje="¿Eso dónde lo guardamos?",
            usuario=self.admin,
            contexto_sesion=contexto,
        )
        self.assertEqual(res.tipo, IntencionTipo.CONSULTAR_UBICACION)
        self.assertEqual(res.producto.id, self.prod_switch.id)

    def test_frase_7_cambiando_de_tema_que_entro_hoy(self):
        """«Bueno, cambiando de tema, ¿qué entró hoy?» -> CONSULTAR_METRICAS_MOVIMIENTOS (limpia contexto)."""
        contexto = {
            "haku_ultimo_producto_id": str(self.prod_switch.id),
            "haku_ultimo_producto_nombre": self.prod_switch.nombre,
            "haku_coleccion_productos": [{"id": self.prod_switch.id}],
        }
        res = NormalizadorLenguajeHaku.interpretar(
            mensaje="Bueno, cambiando de tema, ¿qué entró hoy?",
            usuario=self.admin,
            contexto_sesion=contexto,
        )
        self.assertEqual(res.tipo, IntencionTipo.CONSULTAR_METRICAS_MOVIMIENTOS)
        # Verifica que no arrastró el producto anterior
        self.assertIsNone(res.producto)

    def test_variaciones_colombianas_y_costenas(self):
        """Variaciones idiomáticas naturales no deben romper el entendimiento composicional."""
        # 1. Movimiento con léxico local: «¿Qué mercancía nos ingresó hoy por la mañana?»
        res1 = NormalizadorLenguajeHaku.interpretar(
            mensaje="¿Qué mercancía nos ingresó hoy por la mañana?",
            usuario=self.admin,
        )
        self.assertEqual(res1.tipo, IntencionTipo.CONSULTAR_METRICAS_MOVIMIENTOS)

        # 2. Bajo stock perifrástico: «Muéstrame los que están por debajo de lo necesario»
        res2 = NormalizadorLenguajeHaku.interpretar(
            mensaje="Muéstrame los que están por debajo de lo necesario",
            usuario=self.admin,
        )
        self.assertEqual(res2.tipo, IntencionTipo.PRODUCTOS_BAJO_STOCK)

        # 3. Plural coloquial con colección: «Ubícame esos artículos que dijiste»
        ctx = {"haku_coleccion_productos": [{"id": self.prod_switch.id}]}
        res3 = NormalizadorLenguajeHaku.interpretar(
            mensaje="Ubícame esos artículos que dijiste",
            usuario=self.admin,
            contexto_sesion=ctx,
        )
        self.assertEqual(res3.tipo, IntencionTipo.CONSULTAR_UBICACION_COLECCION)

    def test_prevencion_asignacion_arbitraria_en_expresiones_ambiguas(self):
        """Frases ambiguas o vacías NO deben vincularse a un producto aleatorio."""
        # Sin contexto, "búscame el producto" no debe adivinar
        res = NormalizadorLenguajeHaku.interpretar(
            mensaje="búscame el producto",
            usuario=self.admin,
        )
        self.assertIn(res.tipo, (IntencionTipo.SOLICITAR_ACLARACION, IntencionTipo.SOLICITUD_NO_COMPRENDIDA, IntencionTipo.BUSCAR_PRODUCTO))
        self.assertIsNone(res.producto)

        # "la vaina esa" sin contexto no es producto
        res2 = NormalizadorLenguajeHaku.interpretar(
            mensaje="la vaina esa",
            usuario=self.admin,
        )
        self.assertIn(res2.tipo, (IntencionTipo.SOLICITUD_NO_COMPRENDIDA, IntencionTipo.SOLICITAR_ACLARACION))
        self.assertIsNone(res2.producto)

    # ─────────────────────────────────────────────────────────────────────────
    # 2. VALIDACIÓN DE COLECCIONES (>10 ITEMS, TRUNCADO EXPLÍCITO, SEGURIDAD)
    # ─────────────────────────────────────────────────────────────────────────

    def test_coleccion_mas_de_10_productos_y_aviso_explicito(self):
        """Si una consulta devuelve >10 items, la colección guarda total_original y avisa del truncado."""
        # Creamos 12 productos sin stock adicionales
        for i in range(12):
            p = Producto.objects.create(
                codigo_interno=f"AGOT-{i+1:03d}",
                nombre=f"Producto Agotado {i+1:02d}",
                categoria=self.categoria,
                stock_minimo=1,
            )
            p.ubicaciones.add(self.ubicacion_a)
            Existencia.objects.create(
                producto=p,
                almacen=self.almacen_principal,
                stock_disponible=0,
                stock_en_prestamo=0,
            )

        sess_key = "sess_admin_10"
        sesion = {"session_key": sess_key}
        # 1. Consulta global de productos sin stock (hay al menos 14 productos agotados)
        resp1 = procesar_comando_inventario(self.admin, "cuáles productos están agotados", sesion=sesion)
        self.assertEqual(resp1["tipo"], "consulta")
        self.assertGreater(resp1["datos"]["total"], 10)

        # Comprobar que en EstadoOperativo se registraron 10 en memoria pero se conoce el total original
        estado = HakuOperationalContext.obtener_o_crear(self.admin.id, sess_key)
        self.assertEqual(len(estado.coleccion_productos), 10)
        self.assertGreater(estado.coleccion_total_original, 10)

        # 2. Preguntar la ubicación colectiva
        resp2 = procesar_comando_inventario(self.admin, "ubícame las referencias que acabas de mencionar", sesion=sesion)
        self.assertEqual(resp2["tipo"], "consulta")
        
        # Verificar que NO hay omisión silenciosa: aviso explícito de truncado en pantalla y voz
        self.assertTrue(resp2["datos"]["es_parcial"])
        self.assertEqual(resp2["datos"]["total"], 10)
        self.assertEqual(resp2["datos"]["total_original"], estado.coleccion_total_original)
        self.assertIn("Aviso: Mostrando la ubicación de las 10 referencias conservadas", resp2["mensaje_pantalla"])

    def test_cambio_de_tema_evita_reutilizar_coleccion_antigua(self):
        """Un cambio explícito de tema limpia el foco y evita usar la colección previa."""
        sess_key = "sess_cambio_tema"
        sesion = {"session_key": sess_key}
        # 1. Generar foco con colección
        procesar_comando_inventario(self.admin, "cuáles productos no tienen stock", sesion=sesion)
        estado = HakuOperationalContext.obtener_o_crear(self.admin.id, sess_key)
        self.assertTrue(len(estado.coleccion_productos) > 0)

        # 2. Cambiar de tema
        resp_tema = procesar_comando_inventario(self.admin, "cambiando de tema, qué se movió hoy", sesion=sesion)
        self.assertEqual(resp_tema["tipo"], "consulta")

        # 3. Foco debe estar limpio
        estado_despues = HakuOperationalContext.obtener_o_crear(self.admin.id, sess_key)
        self.assertEqual(len(estado_despues.coleccion_productos), 0)
        self.assertIsNone(estado_despues.producto_id)

    def test_aislamiento_de_colecciones_entre_usuarios(self):
        """Dos usuarios diferentes tienen estados operativos y colecciones independientes."""
        sess_admin = "sess_iso_admin"
        sess_op = "sess_iso_op"
        sesion_admin = {"session_key": sess_admin}
        sesion_operario = {"session_key": sess_op}

        # Admin consulta productos sin stock
        procesar_comando_inventario(self.admin, "cuáles productos están agotados", sesion=sesion_admin)
        estado_admin = HakuOperationalContext.obtener_o_crear(self.admin.id, sess_admin)
        self.assertTrue(len(estado_admin.coleccion_productos) > 0)

        # Operario no debe heredar la colección del admin
        estado_operario = HakuOperationalContext.obtener_o_crear(self.operario.id, sess_op)
        self.assertEqual(len(estado_operario.coleccion_productos), 0)

    # ─────────────────────────────────────────────────────────────────────────
    # 3. VERIFICACIÓN DE LA SEMÁNTICA DEL STOCK
    # ─────────────────────────────────────────────────────────────────────────

    def test_semantica_stock_disponible_cero_sin_prestamo(self):
        """0 unidades disponibles y 0 prestadas -> sin_stock es True, stock_bajo es False."""
        self.assertTrue(self.exist_router.sin_stock)
        self.assertFalse(self.exist_router.stock_bajo)

    def test_semantica_stock_disponible_cero_con_prestamo(self):
        """0 unidades disponibles y 4 prestadas -> sin_stock es True (no disponible), stock_bajo es False."""
        self.assertTrue(self.exist_ap.sin_stock)
        self.assertFalse(self.exist_ap.stock_bajo)
        self.assertEqual(self.exist_ap.stock_total, 4)
        self.assertEqual(self.exist_ap.stock_disponible, 0)

    def test_semantica_stock_bajo_estricto(self):
        """2 disponibles con mínimo de 5 -> stock_bajo es True (0 < 2 < 5), sin_stock es False."""
        self.assertTrue(self.exist_patch.stock_bajo)
        self.assertFalse(self.exist_patch.sin_stock)

    def test_semantica_stock_igual_al_minimo(self):
        """10 disponibles con mínimo de 10 -> stock_bajo es False (sección 8.4: stock_disponible < stock_minimo)."""
        self.assertFalse(self.exist_rj45.stock_bajo)
        self.assertFalse(self.exist_rj45.sin_stock)

    def test_semantica_producto_agotado_en_un_almacen_pero_disponible_en_otro(self):
        """Fibra óptica: 0 en Central, 8 en Norte. Admin puede consultar otro almacén, Operario limitado."""
        sesion_admin = {"haku_ultimo_producto_id": str(self.prod_fibra.id)}
        resp_admin = procesar_comando_inventario(self.admin, "y del otro almacén qué queda", sesion=sesion_admin)
        self.assertEqual(resp_admin["tipo"], "consulta")
        self.assertIn("Bodega Norte", resp_admin["mensaje_pantalla"])
        self.assertIn("8 disponibles", resp_admin["mensaje_pantalla"])

        # Operario asignado solo a Central no puede consultar existencias en almacenes no autorizados
        sesion_op = {"haku_ultimo_producto_id": str(self.prod_fibra.id)}
        resp_op = procesar_comando_inventario(self.operario, "y del otro almacén qué queda", sesion=sesion_op)
        self.assertEqual(resp_op["tipo"], "consulta")
        self.assertIn("No tienes permisos para consultar existencias en otros almacenes", resp_op["mensaje_pantalla"])

    # ─────────────────────────────────────────────────────────────────────────
    # 4. VALIDACIÓN DE INVARIANTES DE BARGE-IN Y REPRODUCCIÓN EN CLIENTE
    # ─────────────────────────────────────────────────────────────────────────

    def test_invariantes_barge_in_en_script_javascript(self):
        """Verifica estáticamente que haku_voz.js implementa todas las reglas de barge-in requeridas."""
        js_path = Path(settings.BASE_DIR) / "static" / "js" / "haku_voz.js"
        self.assertTrue(js_path.exists(), "El archivo haku_voz.js debe existir.")
        content = js_path.read_text(encoding="utf-8")

        # 1. AbortController para cancelar síntesis TTS en vuelo
        self.assertIn("state.abortControllerTTS.abort()", content)

        # 2. Pausa y liberación inmediata de elemento HTMLAudioElement
        self.assertIn("state.audioElement.pause()", content)
        self.assertIn("state.audioElement.currentTime = 0", content)

        # 3. Token monotónico ttsPlaybackId para impedir que audios tardíos resuciten
        self.assertIn("ttsPlaybackId", content)
        self.assertIn("currentPlaybackId !== state.ttsPlaybackId", content)

        # 4. Consumo de interrupción: la barra espaciadora o clic de interrupción no inicia grabación accidental
        self.assertIn("state.interrupcionConsumida = true", content)
        self.assertIn("if (state.interrupcionConsumida)", content)

        # 5. Descarte de grabaciones menores a 1000 bytes o vacías
        self.assertIn("if (audioBlob.size < 1000)", content)

        # 6. Exclusión estricta de inputs, textareas y editables para permitir escribir con barra espaciadora
        self.assertIn("activo.tagName === \"INPUT\"", content)
        self.assertIn("activo.tagName === \"TEXTAREA\"", content)
        self.assertIn("activo.isContentEditable", content)
