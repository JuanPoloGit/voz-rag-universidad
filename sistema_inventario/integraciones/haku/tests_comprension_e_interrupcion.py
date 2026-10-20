"""Batería de pruebas exhaustivas para la comprensión conversacional e interrupción de voz de Haku.

Verifica los 12 criterios de aceptación obligatorios:
1. Listar productos con existencias y después preguntar por la ubicación de todos.
2. Responder a una aclaración con «de los tres», «de todos ellos» u otra referencia plural.
3. Consultar un producto y cambiar después a una pregunta global de stock cero.
4. Distinguir productos agotados de productos por debajo del mínimo.
5. Hacer preguntas contextuales legítimas sobre el último producto.
6. Cambiar de tema y comprobar que no se arrastra información irrelevante.
7. Usar expresiones coloquiales y variantes fonéticas sin provocar selecciones arbitrarias.
8. Formular preguntas ambiguas y verificar que HAKU pide aclaración cuando sea necesario.
9. Confirmar que las restricciones de usuario y almacén siguen aplicándose.
10. Verificar que ninguna consulta informativa modifica el inventario.
11. Interrumpir la voz con la barra espaciadora en distintos momentos de la reproducción (validación estática de JS).
12. Comprobar que una interrupción no inicia una grabación accidental ni permite reanudar audio antiguo.
"""

from __future__ import annotations

import time
from pathlib import Path
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Sum
from django.test import TestCase

from cuentas.models import UsuarioAudacIA
from inventario.models import Almacen, Categoria, Existencia, Movimiento, Prestamo, Producto, Ubicacion

from .cache import HakuCacheManager
from .contexto import EstadoOperativoHaku, HakuOperationalContext
from .lenguaje.normalizador import IntencionResultado, IntencionTipo, NormalizadorLenguajeHaku
from .services import procesar_comando_inventario


class ComprensionEInterrupcionTestCase(TestCase):
    """Pruebas integrales de comprensión composicional, contexto mínimo e interrupción de voz."""

    def setUp(self):
        User = get_user_model()
        HakuOperationalContext.limpiar_todo()
        HakuCacheManager.invalidar_todo()

        # 1. Almacenes
        self.almacen_principal = Almacen.objects.create(nombre="Almacén Redes Principal")
        self.almacen_secundario = Almacen.objects.create(nombre="Bodega B Secundario")

        # 2. Categoría y Ubicaciones
        self.categoria = Categoria.objects.create(
            nombre="Conectividad y Redes",
            almacen_principal=self.almacen_principal,
        )
        self.ubicacion_estante_a = Ubicacion.objects.create(
            nombre="Estante A-1, Nivel 1",
            almacen=self.almacen_principal,
        )
        self.ubicacion_rack_b = Ubicacion.objects.create(
            nombre="Rack Principal B-3",
            almacen=self.almacen_principal,
        )
        self.ubicacion_bodega_b = Ubicacion.objects.create(
            nombre="Pasillo 2, Gaveta 5",
            almacen=self.almacen_secundario,
        )

        # 3. Usuarios (Admin y Operario restringido)
        self.admin_user = User.objects.create_user(
            username="admin_haku",
            email="admin@audacia.local",
            password="password123",
            nombre_completo="Administrador General Haku",
            rol="ADMINISTRADOR",
        )
        self.operario_redes = User.objects.create_user(
            username="operario_redes",
            email="redes@audacia.local",
            password="password123",
            nombre_completo="Operario Redes Asignado",
            rol="USUARIO",
            almacen_asignado=self.almacen_principal,
        )
        self.operario_sin_almacen = User.objects.create_user(
            username="operario_huerfano",
            email="huerfano@audacia.local",
            password="password123",
            nombre_completo="Operario Sin Almacen Asignado",
            rol="USUARIO",
            almacen_asignado=None,
        )

        # 4. Catálogo de productos de prueba
        # Prod 1: Cable HDMI (Stock disponible: 15, Min: 5) -> En stock OK
        self.prod_hdmi = Producto.objects.create(
            codigo_interno="CAB-1001",
            nombre="Cable HDMI 2.0 2m",
            categoria=self.categoria,
            stock_minimo=5,
        )
        self.prod_hdmi.ubicaciones.add(self.ubicacion_estante_a)
        Existencia.objects.create(
            producto=self.prod_hdmi,
            almacen=self.almacen_principal,
            stock_disponible=15,
            stock_en_prestamo=0,
        )

        # Prod 2: Cable UTP (Stock disponible: 8, Min: 2) -> En stock OK
        self.prod_utp = Producto.objects.create(
            codigo_interno="CAB-1002",
            nombre="Cable UTP Cat 6 Bobina",
            categoria=self.categoria,
            stock_minimo=2,
        )
        self.prod_utp.ubicaciones.add(self.ubicacion_rack_b)
        Existencia.objects.create(
            producto=self.prod_utp,
            almacen=self.almacen_principal,
            stock_disponible=8,
            stock_en_prestamo=1,
        )

        # Prod 3: Switch 8 Puertos (Stock disponible: 1, Min: 3) -> BAJO STOCK (< min)
        self.prod_switch = Producto.objects.create(
            codigo_interno="RED-3001",
            nombre="Switch Gigabit 8 Puertos",
            categoria=self.categoria,
            stock_minimo=3,
        )
        self.prod_switch.ubicaciones.add(self.ubicacion_rack_b)
        Existencia.objects.create(
            producto=self.prod_switch,
            almacen=self.almacen_principal,
            stock_disponible=1,
            stock_en_prestamo=0,
        )

        # Prod 4: Ponchadora Impacto (Stock disponible: 0, Min: 2) -> AGOTADO (stock == 0)
        self.prod_ponchadora = Producto.objects.create(
            codigo_interno="HER-4001",
            nombre="Ponchadora RJ45 Impacto",
            categoria=self.categoria,
            stock_minimo=2,
        )
        self.prod_ponchadora.ubicaciones.add(self.ubicacion_estante_a)
        Existencia.objects.create(
            producto=self.prod_ponchadora,
            almacen=self.almacen_principal,
            stock_disponible=0,
            stock_en_prestamo=2,
        )

        # Prod 5: En almacén secundario exclusivamente (Stock disponible: 4, Min: 1)
        self.prod_secundario = Producto.objects.create(
            codigo_interno="ACC-5001",
            nombre="Conector RJ45 Bolsa 100",
            categoria=self.categoria,
            stock_minimo=1,
        )
        self.prod_secundario.ubicaciones.add(self.ubicacion_bodega_b)
        Existencia.objects.create(
            producto=self.prod_secundario,
            almacen=self.almacen_secundario,
            stock_disponible=4,
            stock_en_prestamo=0,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # 1. Listar productos con existencias y preguntar por la ubicación de todos
    # ─────────────────────────────────────────────────────────────────────────
    def test_01_listar_stock_y_luego_preguntar_ubicacion_coleccion(self):
        """Comprueba listar existencias y luego resolver ubicaciones de colecciones sin producto individual."""
        ctx = HakuOperationalContext.obtener_o_crear(self.admin_user.id, "session_test_01")

        # Turno 1: Listar productos con existencias
        res1 = procesar_comando_inventario(
            mensaje="¿Qué productos hay en stock?",
            usuario=self.admin_user,
            estado_operativo=ctx,
        )
        self.assertEqual(res1["tipo"], "consulta")
        self.assertGreaterEqual(len(res1["datos"]["existencias"]), 2)
        # La colección debe quedar almacenada en ctx
        self.assertGreaterEqual(len(ctx.coleccion_productos), 2)

        # Turno 2: Variaciones de consulta de ubicación colectiva
        variantes_ubicacion = [
            "¿Dónde está cada producto?",
            "Dime en qué almacén está cada uno",
            "¿Dónde guardamos esas referencias?",
            "Ubícame todos los productos que acabas de listar",
            "¿En qué estantes se encuentran?",
        ]
        for frase in variantes_ubicacion:
            res_ub = procesar_comando_inventario(
                mensaje=frase,
                usuario=self.admin_user,
                estado_operativo=ctx,
            )
            self.assertEqual(res_ub["tipo"], "consulta", f"Falló con la variante: {frase}")
            self.assertIn("Ubicación", res_ub["mensaje_pantalla"])
            # Debe incluir las referencias listadas previamente (ej. Cable HDMI y Cable UTP)
            self.assertIn("Cable HDMI", res_ub["mensaje_pantalla"])
            self.assertIn("Cable UTP", res_ub["mensaje_pantalla"])

    # ─────────────────────────────────────────────────────────────────────────
    # 2. Responder a una aclaración con «de los tres», «de todos ellos»
    # ─────────────────────────────────────────────────────────────────────────
    def test_02_aclaracion_con_referencia_plural(self):
        """Comprueba responder a aclaración de candidatos con 'de los tres' o 'de todos ellos'."""
        ctx = HakuOperationalContext.obtener_o_crear(self.admin_user.id, "session_test_02")
        # Simular que HAKU ofreció 3 candidatos para desambiguar
        candidatos = [
            {"id": self.prod_hdmi.id, "nombre": self.prod_hdmi.nombre, "codigo": self.prod_hdmi.codigo_interno},
            {"id": self.prod_utp.id, "nombre": self.prod_utp.nombre, "codigo": self.prod_utp.codigo_interno},
            {"id": self.prod_switch.id, "nombre": self.prod_switch.nombre, "codigo": self.prod_switch.codigo_interno},
        ]
        ctx.asignar_candidatos(candidatos)
        ctx.ultima_intencion = IntencionTipo.SOLICITAR_ACLARACION.value
        HakuOperationalContext.guardar(ctx)

        variaciones_plurales = [
            "de los tres",
            "de todos ellos",
            "dime de los tres",
            "muéstrame los tres",
        ]
        for frase in variaciones_plurales:
            intencion = NormalizadorLenguajeHaku.interpretar(
                mensaje=frase,
                usuario=self.admin_user,
                estado_operativo=ctx,
            )
            self.assertIn(
                intencion.tipo,
                (IntencionTipo.PRODUCTOS_CON_STOCK, IntencionTipo.CONSULTAR_UBICACION_COLECCION),
                f"La frase '{frase}' no resolvió a selección colectiva de candidatos.",
            )
            self.assertIsNotNone(intencion.datos_extra)
            self.assertIn("candidatos_plurales", intencion.datos_extra)
            self.assertEqual(len(intencion.datos_extra["candidatos_plurales"]), 3)

    # ─────────────────────────────────────────────────────────────────────────
    # 3. Consultar un producto y cambiar después a pregunta global de stock cero
    # ─────────────────────────────────────────────────────────────────────────
    def test_03_cambio_de_producto_a_stock_cero_global(self):
        """Comprueba que preguntar stock cero tras consultar un producto no arrastra el producto previo."""
        ctx = HakuOperationalContext.obtener_o_crear(self.admin_user.id, "session_test_03")

        # Turno 1: Consultar producto específico
        res1 = procesar_comando_inventario(
            mensaje="Haku, cuánto hay de cable HDMI",
            usuario=self.admin_user,
            estado_operativo=ctx,
        )
        self.assertEqual(ctx.producto_id, self.prod_hdmi.id)
        self.assertIn("Cable HDMI", res1["mensaje_pantalla"])

        # Turno 2: Pregunta global de stock cero
        preguntas_globales_cero = [
            "¿qué productos no tienen stock?",
            "cuáles están agotados",
            "qué productos están sin stock",
            "qué está en cero",
        ]
        for frase in preguntas_globales_cero:
            res_cero = procesar_comando_inventario(
                mensaje=frase,
                usuario=self.admin_user,
                estado_operativo=ctx,
            )
            self.assertEqual(res_cero["tipo"], "consulta", f"Falló con: {frase}")
            # Debe listar el producto agotado real (Ponchadora), NUNCA repetir Cable HDMI
            self.assertIn("Ponchadora", res_cero["mensaje_pantalla"])
            self.assertNotIn("Cable HDMI 2.0 2m", res_cero["mensaje_pantalla"])
            # El foco conversacional individual debe haberse limpiado
            self.assertIsNone(ctx.producto_id)

    # ─────────────────────────────────────────────────────────────────────────
    # 4. Distinguir productos agotados de productos por debajo del mínimo
    # ─────────────────────────────────────────────────────────────────────────
    def test_04_distinguir_agotados_de_bajo_minimo(self):
        """Comprueba que stock cero (agotados) y stock bajo (< mínimo) produzcan resultados distintos."""
        ctx = HakuOperationalContext.obtener_o_crear(self.admin_user.id, "session_test_04")

        # A. Consulta de Agotados (disponible == 0) -> Ponchadora
        res_agotados = procesar_comando_inventario(
            mensaje="¿Qué productos están agotados?",
            usuario=self.admin_user,
            estado_operativo=ctx,
        )
        self.assertIn("Ponchadora", res_agotados["mensaje_pantalla"])
        self.assertNotIn("Switch", res_agotados["mensaje_pantalla"])
        self.assertNotIn("Cable HDMI", res_agotados["mensaje_pantalla"])

        # B. Consulta de Por Debajo del Mínimo (disponible <= minimo y > 0) -> Switch
        res_bajo = procesar_comando_inventario(
            mensaje="¿Qué productos están por debajo del mínimo?",
            usuario=self.admin_user,
            estado_operativo=ctx,
        )
        self.assertIn("Switch", res_bajo["mensaje_pantalla"])
        self.assertNotIn("Cable HDMI", res_bajo["mensaje_pantalla"])
        self.assertNotIn("Ponchadora", res_bajo["mensaje_pantalla"])

        # C. Alertas generales de bajo stock (combina bajo stock y agotados) -> Switch y Ponchadora
        res_alertas = procesar_comando_inventario(
            mensaje="¿Qué productos tienen bajo stock?",
            usuario=self.admin_user,
            estado_operativo=ctx,
        )
        self.assertIn("Switch", res_alertas["mensaje_pantalla"])
        self.assertIn("Ponchadora", res_alertas["mensaje_pantalla"])
        self.assertNotIn("Cable HDMI", res_alertas["mensaje_pantalla"])

    # ─────────────────────────────────────────────────────────────────────────
    # 5. Preguntas contextuales legítimas sobre el último producto
    # ─────────────────────────────────────────────────────────────────────────
    def test_05_preguntas_contextuales_legitimas_ultimo_producto(self):
        """Comprueba que preguntas dependientes legibles sigan resolviendo al producto en foco."""
        ctx = HakuOperationalContext.obtener_o_crear(self.admin_user.id, "session_test_05")
        ctx.asignar_producto(self.prod_hdmi.id, self.prod_hdmi.codigo_interno, self.prod_hdmi.nombre)
        HakuOperationalContext.guardar(ctx)

        # Ubicación singular dependiente
        res_ub = procesar_comando_inventario(
            mensaje="¿Dónde está ubicado?",
            usuario=self.admin_user,
            estado_operativo=ctx,
        )
        self.assertEqual(res_ub["tipo"], "consulta")
        self.assertIn("Cable HDMI", res_ub["mensaje_pantalla"])
        self.assertIn("Estante A-1", res_ub["mensaje_pantalla"])

        # Movimientos dependientes
        res_mov = procesar_comando_inventario(
            mensaje="¿Cómo se ha movido?",
            usuario=self.admin_user,
            estado_operativo=ctx,
        )
        self.assertEqual(res_mov["tipo"], "consulta")
        self.assertIn("Cable HDMI", res_mov["mensaje_pantalla"])

    # ─────────────────────────────────────────────────────────────────────────
    # 6. Cambiar de tema y comprobar que no se arrastra información irrelevante
    # ─────────────────────────────────────────────────────────────────────────
    def test_06_cambio_de_tema_limpio(self):
        """Comprueba que preguntas sobre almacenes o movimientos del día no arrastren el producto anterior."""
        ctx = HakuOperationalContext.obtener_o_crear(self.admin_user.id, "session_test_06")
        ctx.asignar_producto(self.prod_hdmi.id, self.prod_hdmi.codigo_interno, self.prod_hdmi.nombre)
        HakuOperationalContext.guardar(ctx)

        # Cambio a almacenes
        res_alm = procesar_comando_inventario(
            mensaje="¿Qué almacenes tenemos?",
            usuario=self.admin_user,
            estado_operativo=ctx,
        )
        self.assertEqual(res_alm["tipo"], "consulta")
        self.assertIn("Almacén Redes Principal", res_alm["mensaje_pantalla"])
        self.assertNotIn("Cable HDMI", res_alm["mensaje_pantalla"])

        # Cambio a movimientos del día
        res_mov_dia = procesar_comando_inventario(
            mensaje="¿Cuántas entradas se hicieron hoy?",
            usuario=self.admin_user,
            estado_operativo=ctx,
        )
        self.assertEqual(res_mov_dia["tipo"], "consulta")
        self.assertIn("entradas", res_mov_dia["mensaje_pantalla"])

    # ─────────────────────────────────────────────────────────────────────────
    # 7. Expresiones coloquiales y variantes fonéticas
    # ─────────────────────────────────────────────────────────────────────────
    def test_07_variantes_foneticas_y_coloquiales_costenas(self):
        """Comprueba normalización de expresiones caribeñas sin selecciones arbitrarias."""
        ctx = HakuOperationalContext.obtener_o_crear(self.admin_user.id, "session_test_07")

        # Expresiones con fonética caribeña
        int1 = NormalizadorLenguajeHaku.interpretar("dime las volegas disponibles", self.admin_user, estado_operativo=ctx)
        self.assertEqual(int1.tipo, IntencionTipo.LISTAR_ALMACENES)

        int2 = NormalizadorLenguajeHaku.interpretar("cómo va el voleo de hoy", self.admin_user, estado_operativo=ctx)
        self.assertEqual(int2.tipo, IntencionTipo.CONSULTAR_METRICAS_MOVIMIENTOS)

        int3 = NormalizadorLenguajeHaku.interpretar("cuánto estoco hay de cable UTP", self.admin_user, estado_operativo=ctx)
        self.assertEqual(int3.tipo, IntencionTipo.CONSULTAR_STOCK)
        self.assertEqual(int3.producto.id, self.prod_utp.id)

    # ─────────────────────────────────────────────────────────────────────────
    # 8. Preguntas ambiguas piden aclaración
    # ─────────────────────────────────────────────────────────────────────────
    def test_08_preguntas_ambiguas_solicitan_aclaracion(self):
        """Comprueba que preguntas no específicas soliciten aclaración sin inventar datos."""
        ctx = HakuOperationalContext.obtener_o_crear(self.admin_user.id, "session_test_08")

        # Sin producto previo ni mencionado
        res1 = procesar_comando_inventario(
            mensaje="pásame ese cable",
            usuario=self.admin_user,
            estado_operativo=ctx,
        )
        self.assertEqual(res1["tipo"], "aclaracion")

        res2 = procesar_comando_inventario(
            mensaje="¿dónde está?",
            usuario=self.admin_user,
            estado_operativo=ctx,
        )
        self.assertEqual(res2["tipo"], "aclaracion")

    # ─────────────────────────────────────────────────────────────────────────
    # 9. Restricciones de usuario y almacén
    # ─────────────────────────────────────────────────────────────────────────
    def test_09_restricciones_de_almacen_en_usuario_normal(self):
        """Comprueba que un usuario normal solo acceda a su almacén asignado."""
        ctx_op = HakuOperationalContext.obtener_o_crear(self.operario_redes.id, "session_test_09_a")
        res = procesar_comando_inventario(
            mensaje="¿Qué productos hay en stock?",
            usuario=self.operario_redes,
            estado_operativo=ctx_op,
        )
        # No debe ver el producto exclusivo del almacén secundario
        self.assertNotIn("Conector RJ45 Bolsa 100", res["mensaje_pantalla"])

        # Usuario sin almacén
        ctx_huerfano = HakuOperationalContext.obtener_o_crear(self.operario_sin_almacen.id, "session_test_09_b")
        res_h = procesar_comando_inventario(
            mensaje="¿Qué productos hay en stock?",
            usuario=self.operario_sin_almacen,
            estado_operativo=ctx_huerfano,
        )
        self.assertIn("no tiene un almacén asignado", res_h["mensaje_pantalla"])

    # ─────────────────────────────────────────────────────────────────────────
    # 10. Ninguna consulta informativa modifica el inventario
    # ─────────────────────────────────────────────────────────────────────────
    def test_10_consultas_informativas_no_modifican_db(self):
        """Comprueba que ninguna consulta de inventario cree o altere movimientos o existencias."""
        ctx = HakuOperationalContext.obtener_o_crear(self.admin_user.id, "session_test_10")
        mov_count_antes = Movimiento.objects.count()
        prest_count_antes = Prestamo.objects.count()
        stock_antes = Existencia.objects.aggregate(total=Sum("stock_disponible"))["total"]

        consultas = [
            "¿Qué productos hay en stock?",
            "¿Qué productos no tienen stock?",
            "¿Qué productos tienen bajo stock?",
            "¿Dónde está cada producto?",
            "Haku, cuánto hay de cable HDMI",
            "¿Dónde está ubicado?",
            "¿Qué almacenes tenemos?",
            "Resumen del inventario",
            "¿Cómo van los movimientos de hoy?",
        ]
        for c in consultas:
            procesar_comando_inventario(mensaje=c, usuario=self.admin_user, estado_operativo=ctx)

        self.assertEqual(Movimiento.objects.count(), mov_count_antes)
        self.assertEqual(Prestamo.objects.count(), prest_count_antes)
        self.assertEqual(Existencia.objects.aggregate(total=Sum("stock_disponible"))["total"], stock_antes)

    # ─────────────────────────────────────────────────────────────────────────
    # 11 y 12. Interrupción con barra espaciadora en JS (Análisis Estático y Máquina de Estados)
    # ─────────────────────────────────────────────────────────────────────────
    def test_11_y_12_interrupcion_voz_barra_espaciadora_frontend(self):
        """Valida que haku_voz.js implemente la máquina de estados de interrupción de voz."""
        js_path = Path(settings.BASE_DIR) / "static" / "js" / "haku_voz.js"
        self.assertTrue(js_path.exists(), "El archivo haku_voz.js debe existir")
        codigo = js_path.read_text(encoding="utf-8")

        # 1. Variables de control en state
        self.assertIn("audioElement: null", codigo)
        self.assertIn("abortControllerTTS: null", codigo)
        self.assertIn("interrupcionConsumida: false", codigo)

        # 2. Función dedicada detenerReproduccionVoz
        self.assertIn("function detenerReproduccionVoz()", codigo)
        self.assertIn("abortControllerTTS.abort()", codigo)
        self.assertIn("state.audioElement.pause()", codigo)
        self.assertIn("state.audioElement.currentTime = 0", codigo)
        self.assertIn("URL.revokeObjectURL", codigo)
        self.assertIn('state.reproduciendoAudio = false', codigo)
        self.assertIn('actualizarEstadoVisual("reposo")', codigo)

        # 3. Consumo exclusivo de la pulsación de barra espaciadora
        self.assertIn("if (state.reproduciendoAudio)", codigo)
        self.assertIn("state.interrupcionConsumida = true", codigo)
        self.assertIn("if (state.interrupcionConsumida)", codigo)
        self.assertIn("e.repeat", codigo)

        # 4. Chequeo de foco en campos de texto antes de interceptar
        self.assertIn("esCampoDeTexto", codigo)
        self.assertIn("INPUT", codigo)
        self.assertIn("TEXTAREA", codigo)
        self.assertIn("isContentEditable", codigo)

        # 5. Soporte coherente en botón móvil / táctil
        self.assertIn("btnMic.addEventListener(\"touchstart\"", codigo)
        self.assertIn("btnMic.addEventListener(\"click\"", codigo)
