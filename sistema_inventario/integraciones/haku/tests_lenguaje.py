"""Pruebas rigurosas de la capa lingüística costeña, desambiguación y no-mutación.

Valida:
1. Expresiones costeñas naturales:
   - 'Haku búscame el cable ese HDMI'
   - 'Haku cuánto queda de cable UTP'
   - 'Haku dónde dejaron la ponchadora'
   - 'pásame ese cable' (ambigüedad estricta)
   - 'Haku sácame tres de esos' (con y sin contexto)
2. La capa lingüística nunca ejecuta movimientos ni muta la base de datos (Regla 2).
3. Contexto conversacional limitado a 30 minutos (Regla 4).
4. Desambiguación de códigos sin asumir CAB-001 == CAB-0001 (Regla 5).
5. Concurrencia y convivencia segura con Haku Visitante (Regla 8).
"""

from __future__ import annotations

import json
import threading
import time
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from cuentas.models import UsuarioAudacIA
from inventario.models import Almacen, Auditoria, Categoria, Existencia, Movimiento, Prestamo, Producto, Ubicacion
from integraciones.haku.lenguaje.costeno import InterpretadorCosteno, ValidadorSalidaEmpresarial
from integraciones.haku.lenguaje.normalizador import IntencionResultado, IntencionTipo, NormalizadorLenguajeHaku
from integraciones.haku.lenguaje.siglas import NormalizadorSiglasTecnicas
from integraciones.haku.services import (
    HAKU_GPU_LOCK,
    HakuOcupadoError,
    adquirir_lock_haku,
    ejecutar_confirmacion_propuesta,
    procesar_comando_inventario,
)


class BaseLenguajeTestCase(TestCase):
    """Configuración base con datos de catálogo y usuarios para pruebas de lenguaje."""

    def setUp(self):
        User = get_user_model()
        self.almacen = Almacen.objects.create(nombre="Almacén Redes")
        self.categoria = Categoria.objects.create(nombre="Cables y Herramientas", almacen_principal=self.almacen)
        self.ubicacion_rack = Ubicacion.objects.create(almacen=self.almacen, nombre="Estante A-1")
        self.ubicacion_cajon = Ubicacion.objects.create(almacen=self.almacen, nombre="Cajón Herramientas 3")

        self.usuario = User.objects.create_user(
            username="tecnico_costeno",
            email="costeno@audacia.local",
            password="password123",
            nombre_completo="Técnico Caribe AudacIA",
            rol="ADMINISTRADOR",
        )

        # 1. Cable HDMI
        self.prod_hdmi = Producto.objects.create(
            codigo_interno="CAB-1001",
            nombre="Cable HDMI 2.0 2m",
            categoria=self.categoria,
            stock_minimo=5,
        )
        self.prod_hdmi.ubicaciones.add(self.ubicacion_rack)
        Existencia.objects.create(
            producto=self.prod_hdmi,
            almacen=self.almacen,
            stock_disponible=25,
            stock_en_prestamo=2,
        )

        # 2. Cable UTP
        self.prod_utp = Producto.objects.create(
            codigo_interno="CAB-1002",
            nombre="Cable UTP Cat 6 Bobina",
            categoria=self.categoria,
            stock_minimo=2,
        )
        self.prod_utp.ubicaciones.add(self.ubicacion_rack)
        Existencia.objects.create(
            producto=self.prod_utp,
            almacen=self.almacen,
            stock_disponible=8,
            stock_en_prestamo=0,
        )

        # 3. Ponchadora RJ45
        self.prod_ponchadora = Producto.objects.create(
            codigo_interno="HER-2001",
            nombre="Ponchadora RJ45 Impacto",
            categoria=self.categoria,
            stock_minimo=1,
        )
        self.prod_ponchadora.ubicaciones.add(self.ubicacion_cajon)
        Existencia.objects.create(
            producto=self.prod_ponchadora,
            almacen=self.almacen,
            stock_disponible=3,
            stock_en_prestamo=1,
        )

        self.client = Client()
        self.client.force_login(self.usuario)


class ExpresionesCostenasTestCase(BaseLenguajeTestCase):
    """Valida la interpretación de giros idiomáticos naturales costeños."""

    def test_buscame_el_cable_ese_hdmi(self):
        """'Haku búscame el cable ese HDMI' debe identificar el Cable HDMI sin ambigüedad."""
        intencion = NormalizadorLenguajeHaku.interpretar(
            mensaje="Haku búscame el cable ese HDMI",
            usuario=self.usuario,
        )
        self.assertIn(intencion.tipo, [IntencionTipo.BUSCAR_PRODUCTO, IntencionTipo.CONSULTAR_STOCK])
        self.assertEqual(intencion.producto, self.prod_hdmi)

    def test_cuanto_queda_de_cable_utp(self):
        """'Haku cuánto queda de cable UTP' debe reconocer la intención de consultar stock de Cable UTP."""
        intencion = NormalizadorLenguajeHaku.interpretar(
            mensaje="Haku cuánto queda de cable UTP",
            usuario=self.usuario,
        )
        self.assertEqual(intencion.tipo, IntencionTipo.CONSULTAR_STOCK)
        self.assertEqual(intencion.producto, self.prod_utp)

        # Procesado por el servicio debe devolver los datos de existencia
        res = procesar_comando_inventario(
            usuario=self.usuario,
            mensaje="Haku cuánto queda de cable UTP",
        )
        self.assertEqual(res["tipo"], "consulta")
        self.assertIn("8", res["mensaje_pantalla"])

    def test_donde_dejaron_la_ponchadora(self):
        """'Haku dónde dejaron la ponchadora' debe reconocer intención de consultar ubicación de Ponchadora."""
        intencion = NormalizadorLenguajeHaku.interpretar(
            mensaje="Haku dónde dejaron la ponchadora",
            usuario=self.usuario,
        )
        self.assertEqual(intencion.tipo, IntencionTipo.CONSULTAR_UBICACION)
        self.assertEqual(intencion.producto, self.prod_ponchadora)

        res = procesar_comando_inventario(
            usuario=self.usuario,
            mensaje="Haku dónde dejaron la ponchadora",
        )
        self.assertEqual(res["tipo"], "consulta")
        self.assertIn("Cajón Herramientas 3", res["mensaje_pantalla"])

    def test_pasame_ese_cable_genera_aclaracion_y_no_salida(self):
        """Regla 3: 'pásame ese cable' NO debe generar salida automática; debe solicitar aclaración."""
        intencion = NormalizadorLenguajeHaku.interpretar(
            mensaje="pásame ese cable",
            usuario=self.usuario,
        )
        self.assertEqual(intencion.tipo, IntencionTipo.SOLICITAR_ACLARACION)
        self.assertIn("ese cable", intencion.mensaje_aclaracion)

        # A través del servicio
        res = procesar_comando_inventario(
            usuario=self.usuario,
            mensaje="pásame ese cable",
        )
        self.assertEqual(res["tipo"], "aclaracion")
        self.assertNotIn("propuesta", res)

    def test_sacame_tres_de_esos_sin_contexto_solicita_aclaracion(self):
        """'Haku sácame tres de esos' sin haber consultado ningún producto antes debe solicitar aclaración."""
        intencion = NormalizadorLenguajeHaku.interpretar(
            mensaje="Haku sácame tres de esos",
            usuario=self.usuario,
            contexto_sesion={},
        )
        self.assertEqual(intencion.tipo, IntencionTipo.SOLICITAR_ACLARACION)

    def test_sacame_tres_de_esos_con_contexto_activo_genera_propuesta(self):
        """'Haku sácame tres de esos' con contexto conversacional reciente (< 30 min) enlaza al producto previo."""
        # Sesión donde hace 2 minutos se consultó Cable HDMI
        sesion_activa = {
            "haku_ultimo_producto_id": self.prod_hdmi.id,
            "haku_ultimo_acceso": time.time() - 120,  # 2 min atrás
        }

        intencion = NormalizadorLenguajeHaku.interpretar(
            mensaje="Haku sácame tres de esos para Pedro",
            usuario=self.usuario,
            contexto_sesion=sesion_activa,
        )
        self.assertEqual(intencion.tipo, IntencionTipo.REGISTRAR_SALIDA)
        self.assertEqual(intencion.producto, self.prod_hdmi)
        self.assertEqual(intencion.cantidad, 3)

        # Verificamos que procesar_comando_inventario cree una propuesta pendiente (NO movimiento ejecutado)
        res = procesar_comando_inventario(
            usuario=self.usuario,
            mensaje="Haku sácame tres de esos para Pedro",
            sesion=sesion_activa,
        )
        self.assertEqual(res["tipo"], "propuesta")
        self.assertTrue(res["requiere_confirmacion"])
        self.assertEqual(res["propuesta"]["producto_id"], self.prod_hdmi.id)
        self.assertEqual(res["propuesta"]["cantidad"], 3)


class ReglaNoMutacionTestCase(BaseLenguajeTestCase):
    """Regla 2: La capa lingüística nunca debe ejecutar movimientos de inventario."""

    def test_capa_linguistica_no_muta_base_de_datos(self):
        """Ejecutar intenciones directas no debe crear registros en Movimiento, Prestamo ni Auditoria."""
        movimientos_iniciales = Movimiento.objects.count()
        prestamos_iniciales = Prestamo.objects.count()
        auditorias_iniciales = Auditoria.objects.count()

        frases = [
            "Haku registra salida de 5 Cable HDMI para Carlos por mantenimiento",
            "mete 10 Cable UTP",
            "presta 1 Ponchadora a Juan",
            "devolver Cable HDMI de Carlos",
        ]

        for frase in frases:
            intencion = NormalizadorLenguajeHaku.interpretar(mensaje=frase, usuario=self.usuario)
            # Solo devuelve estructura de intención
            self.assertIsInstance(intencion, IntencionResultado)

            # A través del procesador de comandos nunca debe autoejecutar movimientos
            res = procesar_comando_inventario(usuario=self.usuario, mensaje=frase)
            self.assertNotEqual(res["tipo"], "confirmacion_ejecutada")
            self.assertIn(res["tipo"], ["propuesta", "aclaracion", "consulta", "conversacion"])

        # Ninguna fila en base de datos debe haberse alterado
        self.assertEqual(Movimiento.objects.count(), movimientos_iniciales)
        self.assertEqual(Prestamo.objects.count(), prestamos_iniciales)
        self.assertEqual(Auditoria.objects.count(), auditorias_iniciales)


class ContextoConversacional2HorasTestCase(BaseLenguajeTestCase):
    """Regla 4: Contexto temporal efímero limitado estrictamente a 2 horas (7200 segundos)."""

    def test_contexto_valido_dentro_de_2_horas(self):
        """Contexto con 40 minutos sigue recordando el producto previo."""
        sesion = {
            "haku_ultimo_producto_id": self.prod_utp.id,
            "haku_ultimo_acceso": time.time() - (40 * 60),  # 40 min (< 2 horas)
        }
        intencion = NormalizadorLenguajeHaku.interpretar(
            mensaje="Haku sácame 2 de esos para María",
            usuario=self.usuario,
            contexto_sesion=sesion,
        )
        self.assertEqual(intencion.tipo, IntencionTipo.REGISTRAR_SALIDA)
        self.assertEqual(intencion.producto, self.prod_utp)

    def test_contexto_caducado_tras_2_horas(self):
        """Contexto con 125 minutos (> 120 min) está expirado y solicita aclaración."""
        sesion = {
            "haku_ultimo_producto_id": self.prod_utp.id,
            "haku_ultimo_acceso": time.time() - (125 * 60),  # 125 min (> 120 min)
        }
        intencion = NormalizadorLenguajeHaku.interpretar(
            mensaje="Haku sácame 2 de esos para María",
            usuario=self.usuario,
            contexto_sesion=sesion,
        )
        # Al estar caducado, no puede asumir el producto anterior
        self.assertEqual(intencion.tipo, IntencionTipo.SOLICITAR_ACLARACION)


class DesambiguacionCodigosTestCase(BaseLenguajeTestCase):
    """Regla 5: Mejorar normalización de códigos. No asumir CAB-001 == CAB-0001."""

    def setUp(self):
        super().setUp()
        # Crear deliberadamente dos productos cuyos sufijos numéricos coinciden (CAB-001 y CAB-0001)
        self.prod_cab_001 = Producto.objects.create(
            codigo_interno="CAB-001",
            nombre="Cable USB Tipo C Corto",
            categoria=self.categoria,
        )
        self.prod_cab_0001 = Producto.objects.create(
            codigo_interno="CAB-0001",
            nombre="Cable USB Tipo C Reforzado 3m",
            categoria=self.categoria,
        )

    def test_multiples_coincidencias_solicita_aclaracion(self):
        """Al decir CAB-001 habiendo tanto CAB-001 como CAB-0001, debe listar ambas opciones y pedir aclaración."""
        intencion = NormalizadorLenguajeHaku.interpretar(
            mensaje="Haku cuánto hay de CAB-001",
            usuario=self.usuario,
        )
        self.assertEqual(intencion.tipo, IntencionTipo.SOLICITAR_ACLARACION)
        self.assertIn("varias coincidencias", intencion.mensaje_aclaracion)
        self.assertIn("CAB-001", intencion.mensaje_aclaracion)
        self.assertIn("CAB-0001", intencion.mensaje_aclaracion)

    def test_coincidencia_unica_resuelve_directamente(self):
        """Si solo existe un código numérico con ese valor (ej. HER-2001), resuelve sin ambigüedad."""
        intencion = NormalizadorLenguajeHaku.interpretar(
            mensaje="Haku cuánto hay de HER-2001",
            usuario=self.usuario,
        )
        self.assertEqual(intencion.tipo, IntencionTipo.CONSULTAR_STOCK)
        self.assertEqual(intencion.producto, self.prod_ponchadora)


class ConcurrenciaHakuVisitanteTestCase(BaseLenguajeTestCase):
    """Regla 8: Simulación de convivencia con Haku Visitante sin bloqueos."""

    def test_lock_haku_visitante_activo_no_bloquea_servidor(self):
        """Cuando Haku Visitante ocupa el Lock, Haku Inventario maneja la espera controlada."""
        bloqueo_visitante = threading.Event()
        liberar_visitante = threading.Event()

        def proceso_visitante():
            with HAKU_GPU_LOCK:
                bloqueo_visitante.set()
                liberar_visitante.wait(timeout=1.5)

        t = threading.Thread(target=proceso_visitante)
        t.start()
        bloqueo_visitante.wait(timeout=1.0)

        # Haku Inventario intenta acceder al lock con timeout corto
        try:
            with self.assertRaises(HakuOcupadoError):
                with adquirir_lock_haku(timeout=0.1):
                    pass
        finally:
            liberar_visitante.set()
            t.join()

        # Una vez que Visitante libera el Lock, Inventario opera normalmente
        with adquirir_lock_haku(timeout=1.0):
            self.assertTrue(True)


class PruebasCostenoOperativasTestCase(BaseLenguajeTestCase):
    """Casos de prueba obligatorios de interpretación coloquial costeña y consultas operativas."""

    def setUp(self):
        super().setUp()
        from integraciones.haku.cache import HakuCacheManager
        HakuCacheManager.reset_metricas()

    def test_caso_1_movimientos_de_hoy(self):
        """1. 'Hey Haku, cómo van los movimientos de hoy' -> CONSULTAR_METRICAS_MOVIMIENTOS."""
        intencion = NormalizadorLenguajeHaku.interpretar(
            mensaje="Hey Haku, cómo van los movimientos de hoy",
            usuario=self.usuario,
        )
        self.assertEqual(intencion.tipo, IntencionTipo.CONSULTAR_METRICAS_MOVIMIENTOS)

    def test_caso_2_movida_vaina_hoy(self):
        """2. 'Ajá Haku, cómo está de movida la vaina hoy' -> CONSULTAR_METRICAS_MOVIMIENTOS."""
        intencion = NormalizadorLenguajeHaku.interpretar(
            mensaje="Ajá Haku, cómo está de movida la vaina hoy",
            usuario=self.usuario,
        )
        self.assertEqual(intencion.tipo, IntencionTipo.CONSULTAR_METRICAS_MOVIMIENTOS)

    def test_caso_3_como_va_el_almacen(self):
        """3. 'Haku dime cómo va el almacén' -> CONSULTAR_RESUMEN_INVENTARIO."""
        intencion = NormalizadorLenguajeHaku.interpretar(
            mensaje="Haku dime cómo va el almacén",
            usuario=self.usuario,
        )
        self.assertEqual(intencion.tipo, IntencionTipo.CONSULTAR_RESUMEN_INVENTARIO)

    def test_caso_4_que_tanto_ha_salido_del_cable_hdmi(self):
        """4. 'Qué tanto ha salido del cable HDMI' -> CONSULTAR_PRODUCTO_MOVIMIENTOS."""
        intencion = NormalizadorLenguajeHaku.interpretar(
            mensaje="Qué tanto ha salido del cable HDMI",
            usuario=self.usuario,
        )
        self.assertEqual(intencion.tipo, IntencionTipo.CONSULTAR_PRODUCTO_MOVIMIENTOS)
        self.assertEqual(intencion.producto, self.prod_hdmi)

    def test_caso_5_como_se_ha_movido_esa_vaina_sin_contexto(self):
        """5. 'Cómo se ha movido esa vaina' sin producto en contexto -> SOLICITAR_ACLARACION sin propagar 'vaina'."""
        intencion = NormalizadorLenguajeHaku.interpretar(
            mensaje="Cómo se ha movido esa vaina",
            usuario=self.usuario,
            contexto_sesion={},
        )
        self.assertEqual(intencion.tipo, IntencionTipo.SOLICITAR_ACLARACION)
        self.assertIn("ese producto", intencion.mensaje_aclaracion)
        self.assertNotIn("vaina", intencion.mensaje_aclaracion)

    def test_caso_6_como_se_ha_movido_esa_vaina_con_producto_activo(self):
        """6. 'Cómo se ha movido esa vaina' con producto activo -> CONSULTAR_PRODUCTO_MOVIMIENTOS."""
        sesion = {
            "haku_ultimo_producto_id": self.prod_hdmi.id,
            "haku_ultimo_acceso": time.time(),
        }
        intencion = NormalizadorLenguajeHaku.interpretar(
            mensaje="Cómo se ha movido esa vaina",
            usuario=self.usuario,
            contexto_sesion=sesion,
        )
        self.assertEqual(intencion.tipo, IntencionTipo.CONSULTAR_PRODUCTO_MOVIMIENTOS)
        self.assertEqual(intencion.producto, self.prod_hdmi)

    def test_seguridad_expresiones_coloquiales_nunca_ejecutan_automaticamente(self):
        """Regla crítica de seguridad: 'Ey Haku sácame unos cables' no muta la BD, genera propuesta."""
        mov_antes = Movimiento.objects.count()
        respuesta = procesar_comando_inventario(
            mensaje="Ey Haku sácame unos cables",
            usuario=self.usuario,
        )
        # No se ha creado ningún movimiento en la BD
        self.assertEqual(Movimiento.objects.count(), mov_antes)
        # Si encuentra producto (ej. Cables), debe requerir confirmación
        self.assertTrue(
            respuesta.get("tipo") in ("propuesta", "aclaracion"),
            f"Tipo inesperado: {respuesta.get('tipo')}",
        )
        if respuesta.get("tipo") == "propuesta":
            self.assertTrue(respuesta.get("requiere_confirmacion"))

    def test_servicio_consultar_metricas_movimientos(self):
        """Servicio responde con métricas del día y pregunta por producto específico."""
        respuesta = procesar_comando_inventario(
            mensaje="Hey Haku, cómo van los movimientos de hoy",
            usuario=self.usuario,
        )
        self.assertEqual(respuesta["tipo"], "consulta")
        self.assertIn("¿Quieres revisar algún producto específico?", respuesta["mensaje_pantalla"])
        self.assertIn("entradas", respuesta["mensaje_pantalla"])
        self.assertIn("salidas", respuesta["mensaje_pantalla"])

    def test_servicio_consultar_resumen_inventario(self):
        """Servicio responde con información agregada del almacén sin ejecutar cambios."""
        respuesta = procesar_comando_inventario(
            mensaje="Haku dime cómo va el almacén",
            usuario=self.usuario,
        )
        self.assertEqual(respuesta["tipo"], "consulta")
        self.assertIn("productos registrados", respuesta["mensaje_pantalla"].lower())
        self.assertIn("movimientos del d", respuesta["mensaje_pantalla"].lower())

    def test_servicio_consultar_producto_movimientos(self):
        """Servicio responde con movimientos específicos de producto."""
        respuesta = procesar_comando_inventario(
            mensaje="Qué tanto ha salido del cable HDMI",
            usuario=self.usuario,
        )
        self.assertEqual(respuesta["tipo"], "consulta")
        self.assertIn("Cable HDMI", respuesta["mensaje_pantalla"])
        self.assertIn("movimientos", respuesta["mensaje_pantalla"])

    def test_metricas_internas_intenciones_detectadas(self):
        """Verifica que las métricas agregadas de intenciones se acumulan sin almacenar textos personales."""
        from integraciones.haku.cache import HakuCacheManager
        HakuCacheManager.reset_metricas()

        NormalizadorLenguajeHaku.interpretar("Hey Haku, cómo van los movimientos de hoy", usuario=self.usuario)
        NormalizadorLenguajeHaku.interpretar("Haku dime cómo va el almacén", usuario=self.usuario)
        NormalizadorLenguajeHaku.interpretar("Haku cuánto hay de cable UTP", usuario=self.usuario)

        metricas = HakuCacheManager.obtener_metricas_intenciones()
        self.assertIn(IntencionTipo.CONSULTAR_METRICAS_MOVIMIENTOS.value, metricas)
        self.assertIn(IntencionTipo.CONSULTAR_RESUMEN_INVENTARIO.value, metricas)
        self.assertIn(IntencionTipo.CONSULTAR_STOCK.value, metricas)
        self.assertEqual(metricas[IntencionTipo.CONSULTAR_METRICAS_MOVIMIENTOS.value], 1)
        self.assertEqual(metricas[IntencionTipo.CONSULTAR_RESUMEN_INVENTARIO.value], 1)
        self.assertEqual(metricas[IntencionTipo.CONSULTAR_STOCK.value], 1)
        # Validar que solo son contadores agregados (enteros), sin frases ni audios
        for k, v in metricas.items():
            self.assertIsInstance(k, str)
            self.assertIsInstance(v, int)

    # ─────────────────────────────────────────────────────────────────────────
    # NUEVAS PRUEBAS DE DOMINIO OPERATIVO (SEPARACIÓN DE HAKU VISITANTE)
    # ─────────────────────────────────────────────────────────────────────────

    def test_positiva_hey_haku_como_va_el_dia_de_hoy(self):
        """'Hey Haku cómo va el día de hoy' -> CONSULTAR_METRICAS_MOVIMIENTOS."""
        intencion = NormalizadorLenguajeHaku.interpretar(
            mensaje="Hey Haku cómo va el día de hoy",
            usuario=self.usuario,
        )
        self.assertEqual(intencion.tipo, IntencionTipo.CONSULTAR_METRICAS_MOVIMIENTOS)

    def test_positiva_que_tan_movido_estuvo_el_dia(self):
        """'Qué tan movido estuvo el día' -> CONSULTAR_METRICAS_MOVIMIENTOS."""
        intencion = NormalizadorLenguajeHaku.interpretar(
            mensaje="Qué tan movido estuvo el día",
            usuario=self.usuario,
        )
        self.assertEqual(intencion.tipo, IntencionTipo.CONSULTAR_METRICAS_MOVIMIENTOS)

    def test_positiva_aja_haku_como_va_la_movida(self):
        """'Ajá Haku cómo va la movida' -> CONSULTAR_METRICAS_MOVIMIENTOS."""
        intencion = NormalizadorLenguajeHaku.interpretar(
            mensaje="Ajá Haku cómo va la movida",
            usuario=self.usuario,
        )
        self.assertEqual(intencion.tipo, IntencionTipo.CONSULTAR_METRICAS_MOVIMIENTOS)

    def test_positiva_como_estuvo_el_voleo_hoy(self):
        """'Cómo estuvo el voleo hoy' -> CONSULTAR_METRICAS_MOVIMIENTOS."""
        intencion = NormalizadorLenguajeHaku.interpretar(
            mensaje="Cómo estuvo el voleo hoy",
            usuario=self.usuario,
        )
        self.assertEqual(intencion.tipo, IntencionTipo.CONSULTAR_METRICAS_MOVIMIENTOS)

    def test_negativa_cuentame_una_historia(self):
        """'Cuéntame una historia' -> NO_INTENCION_INVENTARIO (fuera de dominio)."""
        intencion = NormalizadorLenguajeHaku.interpretar(
            mensaje="Cuéntame una historia",
            usuario=self.usuario,
        )
        self.assertEqual(intencion.tipo, IntencionTipo.NO_INTENCION_INVENTARIO)

        respuesta = procesar_comando_inventario(
            mensaje="Cuéntame una historia",
            usuario=self.usuario,
        )
        self.assertEqual(respuesta["tipo"], "fuera_de_dominio")
        self.assertIn("Solo gestiono operaciones y consultas del inventario", respuesta["mensaje_pantalla"])

    def test_negativa_que_opinas_del_clima(self):
        """'Qué opinas del clima' -> NO_INTENCION_INVENTARIO (fuera de dominio)."""
        intencion = NormalizadorLenguajeHaku.interpretar(
            mensaje="Qué opinas del clima",
            usuario=self.usuario,
        )
        self.assertEqual(intencion.tipo, IntencionTipo.NO_INTENCION_INVENTARIO)

        respuesta = procesar_comando_inventario(
            mensaje="Qué opinas del clima",
            usuario=self.usuario,
        )
        self.assertEqual(respuesta["tipo"], "fuera_de_dominio")
        self.assertIn("Solo gestiono operaciones y consultas del inventario", respuesta["mensaje_pantalla"])

    def test_ambigua_haku_pasame_eso_sin_producto_activo(self):
        """'Haku pásame eso' sin producto activo -> SOLICITAR_ACLARACION."""
        intencion = NormalizadorLenguajeHaku.interpretar(
            mensaje="Haku pásame eso",
            usuario=self.usuario,
            contexto_sesion={},
        )
        self.assertEqual(intencion.tipo, IntencionTipo.SOLICITAR_ACLARACION)
        self.assertIn("No tengo un producto asociado actualmente", intencion.mensaje_aclaracion)

    def test_respuesta_metricas_del_dia_tono_operativo_controlado(self):
        """Para CONSULTAR_METRICAS_MOVIMIENTOS, la respuesta es formal y neutral empresarial."""
        respuesta = procesar_comando_inventario(
            mensaje="Hey Haku cómo va el día de hoy",
            usuario=self.usuario,
        )
        self.assertEqual(respuesta["tipo"], "consulta")
        self.assertIn("Durante la jornada se registraron movimientos de inventario", respuesta["mensaje_pantalla"])
        self.assertNotIn("movida", respuesta["mensaje_pantalla"].lower())
        self.assertNotIn("vaina", respuesta["mensaje_pantalla"].lower())
        self.assertIn("¿Quieres revisar algún producto específico?", respuesta["mensaje_pantalla"])


# ═════════════════════════════════════════════════════════════════════════════
# MATRIZ DE PRUEBAS 1: 20 CONSULTAS GENERALES COSTEÑAS (SIN PRODUCTO)
# ═════════════════════════════════════════════════════════════════════════════


class MatrizConsultasGeneralesCostenasTestCase(BaseLenguajeTestCase):
    """20 pruebas que validan que giros y comodines de actividad general nunca se confundan con productos."""

    def _verificar(self, mensaje: str, intencion_esperada: IntencionTipo):
        analisis = InterpretadorCosteno.normalizar(mensaje)
        intencion = NormalizadorLenguajeHaku.interpretar(mensaje, self.usuario)
        self.assertEqual(intencion.tipo, intencion_esperada)
        self.assertFalse(intencion.requiere_confirmacion)
        res = procesar_comando_inventario(usuario=self.usuario, mensaje=mensaje)
        self.assertEqual(res["tipo"], "consulta")

    def test_01_como_va_la_vaina_hoy(self):
        self._verificar("cómo va la vaina hoy", IntencionTipo.CONSULTAR_METRICAS_MOVIMIENTOS)

    def test_02_como_va_la_vaina(self):
        self._verificar("cómo va la vaina", IntencionTipo.CONSULTAR_METRICAS_MOVIMIENTOS)

    def test_03_como_esta_la_vaina(self):
        self._verificar("cómo está la vaina", IntencionTipo.CONSULTAR_METRICAS_MOVIMIENTOS)

    def test_04_como_anda_la_cosa_hoy(self):
        self._verificar("cómo anda la cosa hoy", IntencionTipo.CONSULTAR_METRICAS_MOVIMIENTOS)

    def test_05_como_va_la_cosa_hoy(self):
        self._verificar("cómo va la cosa hoy", IntencionTipo.CONSULTAR_METRICAS_MOVIMIENTOS)

    def test_06_como_estuvo_la_vuelta(self):
        self._verificar("cómo estuvo la vuelta", IntencionTipo.CONSULTAR_METRICAS_MOVIMIENTOS)

    def test_07_como_sigue_la_vuelta_hoy(self):
        self._verificar("cómo sigue la vuelta hoy", IntencionTipo.CONSULTAR_METRICAS_MOVIMIENTOS)

    def test_08_como_va_la_movida_de_hoy(self):
        self._verificar("cómo va la movida de hoy", IntencionTipo.CONSULTAR_METRICAS_MOVIMIENTOS)

    def test_09_como_va_la_movida(self):
        self._verificar("cómo va la movida", IntencionTipo.CONSULTAR_METRICAS_MOVIMIENTOS)

    def test_10_que_tal_estuvo_la_jornada(self):
        self._verificar("qué tal estuvo la jornada", IntencionTipo.CONSULTAR_METRICAS_MOVIMIENTOS)

    def test_11_como_estuvo_la_jornada(self):
        self._verificar("cómo estuvo la jornada", IntencionTipo.CONSULTAR_METRICAS_MOVIMIENTOS)

    def test_12_como_estuvo_el_voleo(self):
        self._verificar("cómo estuvo el voleo", IntencionTipo.CONSULTAR_METRICAS_MOVIMIENTOS)

    def test_13_como_va_el_voleo_hoy(self):
        self._verificar("cómo va el voleo hoy", IntencionTipo.CONSULTAR_METRICAS_MOVIMIENTOS)

    def test_14_que_voleo_hay_hoy(self):
        self._verificar("qué voleo hay hoy", IntencionTipo.CONSULTAR_METRICAS_MOVIMIENTOS)

    def test_15_como_va_el_trabajo_hoy(self):
        self._verificar("cómo va el trabajo hoy", IntencionTipo.CONSULTAR_METRICAS_MOVIMIENTOS)

    def test_16_que_se_ha_movido_hoy(self):
        self._verificar("qué se ha movido hoy", IntencionTipo.CONSULTAR_METRICAS_MOVIMIENTOS)

    def test_17_que_tanto_se_ha_movido_hoy(self):
        self._verificar("qué tanto se ha movido hoy", IntencionTipo.CONSULTAR_METRICAS_MOVIMIENTOS)

    def test_18_como_va_eso_hoy(self):
        self._verificar("cómo va eso hoy", IntencionTipo.CONSULTAR_METRICAS_MOVIMIENTOS)

    def test_19_como_va_el_dia(self):
        self._verificar("cómo va el día", IntencionTipo.CONSULTAR_METRICAS_MOVIMIENTOS)

    def test_20_dime_como_va_el_almacen(self):
        self._verificar("dime cómo va el almacén", IntencionTipo.CONSULTAR_RESUMEN_INVENTARIO)


# ═════════════════════════════════════════════════════════════════════════════
# MATRIZ DE PRUEBAS 2: 20 CONSULTAS CON PRODUCTO EN CONTEXTO RAM
# ═════════════════════════════════════════════════════════════════════════════


class MatrizConsultasConContextoRAMTestCase(BaseLenguajeTestCase):
    """20 pruebas que validan la resolución correcta de 'esa vaina', 'eso', etc., cuando hay antecedente."""

    def setUp(self):
        super().setUp()
        self.ctx = {
            "haku_ultimo_producto_id": str(self.prod_hdmi.id),
            "haku_ultimo_acceso": time.time(),
        }

    def _verificar(self, mensaje: str, intencion_esperada: IntencionTipo, prod_esperado=None):
        intencion = NormalizadorLenguajeHaku.interpretar(mensaje, self.usuario, contexto_sesion=self.ctx)
        self.assertEqual(intencion.tipo, intencion_esperada)
        if prod_esperado:
            self.assertEqual(intencion.producto, prod_esperado)
        self.assertFalse(intencion.requiere_confirmacion)

    def test_01_como_va_esa_vaina_con_foco(self):
        self._verificar("cómo va esa vaina", IntencionTipo.CONSULTAR_PRODUCTO_MOVIMIENTOS, self.prod_hdmi)

    def test_02_que_paso_con_esa_vaina(self):
        self._verificar("qué pasó con esa vaina", IntencionTipo.CONSULTAR_PRODUCTO_MOVIMIENTOS, self.prod_hdmi)

    def test_03_como_se_ha_movido_esa_vaina(self):
        self._verificar("cómo se ha movido esa vaina", IntencionTipo.CONSULTAR_PRODUCTO_MOVIMIENTOS, self.prod_hdmi)

    def test_04_que_tanto_ha_salido_de_eso(self):
        self._verificar("qué tanto ha salido de eso", IntencionTipo.CONSULTAR_PRODUCTO_MOVIMIENTOS, self.prod_hdmi)

    def test_05_cuanto_ha_salido(self):
        self._verificar("cuánto ha salido", IntencionTipo.CONSULTAR_PRODUCTO_MOVIMIENTOS, self.prod_hdmi)

    def test_06_que_movimiento_tuvo(self):
        self._verificar("qué movimiento tuvo", IntencionTipo.CONSULTAR_PRODUCTO_MOVIMIENTOS, self.prod_hdmi)

    def test_07_como_ha_estado_saliendo(self):
        self._verificar("cómo ha estado saliendo", IntencionTipo.CONSULTAR_PRODUCTO_MOVIMIENTOS, self.prod_hdmi)

    def test_08_cuanto_queda_de_esa_vaina(self):
        self._verificar("cuánto queda de esa vaina", IntencionTipo.CONSULTAR_STOCK, self.prod_hdmi)

    def test_09_quedan_muchos(self):
        self._verificar("quedan muchos", IntencionTipo.CONSULTAR_STOCK, self.prod_hdmi)

    def test_10_quedan_pocos(self):
        self._verificar("quedan pocos", IntencionTipo.CONSULTAR_STOCK, self.prod_hdmi)

    def test_11_hay_bastante(self):
        self._verificar("hay bastante", IntencionTipo.CONSULTAR_STOCK, self.prod_hdmi)

    def test_12_hay_algo(self):
        self._verificar("hay algo", IntencionTipo.CONSULTAR_STOCK, self.prod_hdmi)

    def test_13_todavia_hay(self):
        self._verificar("todavía hay", IntencionTipo.CONSULTAR_STOCK, self.prod_hdmi)

    def test_14_quedo_pelado(self):
        self._verificar("quedó pelado", IntencionTipo.CONSULTAR_STOCK, self.prod_hdmi)

    def test_15_quedo_vacio(self):
        self._verificar("quedó vacío", IntencionTipo.CONSULTAR_STOCK, self.prod_hdmi)

    def test_16_se_acabo(self):
        self._verificar("se acabó", IntencionTipo.CONSULTAR_STOCK, self.prod_hdmi)

    def test_17_no_hay_nada(self):
        self._verificar("no hay nada", IntencionTipo.CONSULTAR_STOCK, self.prod_hdmi)

    def test_18_donde_esta_esa_vaina(self):
        self._verificar("dónde está esa vaina", IntencionTipo.CONSULTAR_UBICACION, self.prod_hdmi)

    def test_19_donde_dejaron_eso(self):
        self._verificar("dónde dejaron eso", IntencionTipo.CONSULTAR_UBICACION, self.prod_hdmi)

    def test_20_quien_tiene_prestado_eso(self):
        self._verificar("quién tiene prestado eso", IntencionTipo.PRESTAMOS_ACTIVOS, self.prod_hdmi)


# ═════════════════════════════════════════════════════════════════════════════
# MATRIZ DE PRUEBAS 3: 20 ACCIONES TRANSACCIONALES COSTEÑAS
# ═════════════════════════════════════════════════════════════════════════════


class MatrizAccionesTransaccionalesCostenasTestCase(BaseLenguajeTestCase):
    """20 pruebas que validan formas costeñas de salida, entrada, préstamo y devolución sin mutar BD."""

    def _verificar(self, mensaje: str, intencion_esperada: IntencionTipo, prod_esperado, cantidad=None, dest=None):
        mov_antes = Movimiento.objects.count()
        prest_antes = Prestamo.objects.count()

        intencion = NormalizadorLenguajeHaku.interpretar(mensaje, self.usuario)
        self.assertEqual(intencion.tipo, intencion_esperada)
        self.assertEqual(intencion.producto, prod_esperado)
        self.assertTrue(intencion.requiere_confirmacion)
        if cantidad is not None:
            self.assertEqual(intencion.cantidad, cantidad)
        if dest is not None:
            self.assertEqual(intencion.destinatario, dest)

        # Verificar que el servicio NUNCA ejecute movimientos directamente en BD (Regla de inmutabilidad)
        res = procesar_comando_inventario(usuario=self.usuario, mensaje=mensaje)
        self.assertNotEqual(res["tipo"], "confirmacion_ejecutada")
        self.assertIn(res["tipo"], ["propuesta", "aclaracion", "conversacion"])

        self.assertEqual(Movimiento.objects.count(), mov_antes)
        self.assertEqual(Prestamo.objects.count(), prest_antes)

    def test_01_sacame_dos_cables_hdmi_pa_pedro(self):
        self._verificar("sácame dos cables HDMI pa Pedro", IntencionTipo.REGISTRAR_SALIDA, self.prod_hdmi, cantidad=2, dest="Pedro")

    def test_02_despachame_tres_cables_hdmi(self):
        self._verificar("despáchame tres cables HDMI", IntencionTipo.REGISTRAR_SALIDA, self.prod_hdmi, cantidad=3)

    def test_03_mandame_un_cable_hdmi(self):
        self._verificar("mándame un cable HDMI", IntencionTipo.REGISTRAR_SALIDA, self.prod_hdmi, cantidad=1)

    def test_04_bajame_dos_cables_hdmi(self):
        self._verificar("bájame dos cables HDMI", IntencionTipo.REGISTRAR_SALIDA, self.prod_hdmi, cantidad=2)

    def test_05_pasame_un_cable_hdmi(self):
        self._verificar("pásame un cable HDMI", IntencionTipo.REGISTRAR_SALIDA, self.prod_hdmi, cantidad=1)

    def test_06_saca_dos_cables_hdmi(self):
        self._verificar("saca dos cables HDMI", IntencionTipo.REGISTRAR_SALIDA, self.prod_hdmi, cantidad=2)

    def test_07_saca_ahi_dos_cables_hdmi(self):
        self._verificar("saca ahí dos cables HDMI", IntencionTipo.REGISTRAR_SALIDA, self.prod_hdmi, cantidad=2)

    def test_08_despacha_tres_cables_hdmi(self):
        self._verificar("despacha tres cables HDMI", IntencionTipo.REGISTRAR_SALIDA, self.prod_hdmi, cantidad=3)

    def test_09_mete_diez_cables_hdmi(self):
        self._verificar("mete diez cables HDMI", IntencionTipo.REGISTRAR_ENTRADA, self.prod_hdmi, cantidad=10)

    def test_10_metelo_con_contexto(self):
        ctx = {"haku_ultimo_producto_id": str(self.prod_hdmi.id), "haku_ultimo_acceso": time.time()}
        intencion = NormalizadorLenguajeHaku.interpretar("mételo al inventario", self.usuario, contexto_sesion=ctx)
        self.assertEqual(intencion.tipo, IntencionTipo.REGISTRAR_ENTRADA)
        self.assertEqual(intencion.producto, self.prod_hdmi)

    def test_11_emboca_cinco_cables_hdmi(self):
        self._verificar("emboca cinco cables HDMI", IntencionTipo.REGISTRAR_ENTRADA, self.prod_hdmi, cantidad=5)

    def test_12_agregale_cuatro_con_contexto(self):
        ctx = {"haku_ultimo_producto_id": str(self.prod_hdmi.id), "haku_ultimo_acceso": time.time()}
        intencion = NormalizadorLenguajeHaku.interpretar("agrégale cuatro a ese producto", self.usuario, contexto_sesion=ctx)
        self.assertEqual(intencion.tipo, IntencionTipo.REGISTRAR_ENTRADA)
        self.assertEqual(intencion.producto, self.prod_hdmi)
        self.assertEqual(intencion.cantidad, 4)

    def test_13_llego_diez_cables_hdmi(self):
        self._verificar("llegó diez cables HDMI", IntencionTipo.REGISTRAR_ENTRADA, self.prod_hdmi, cantidad=10)

    def test_14_llegaron_cinco_cables_hdmi(self):
        self._verificar("llegaron cinco cables HDMI", IntencionTipo.REGISTRAR_ENTRADA, self.prod_hdmi, cantidad=5)

    def test_15_prestame_una_ponchadora_pa_carlos(self):
        self._verificar("préstame una ponchadora pa Carlos", IntencionTipo.PRESTAMO, self.prod_ponchadora, cantidad=1, dest="Carlos")

    def test_16_presta_dos_cables_hdmi_pa_laboratorio(self):
        self._verificar("presta dos cables HDMI para laboratorio", IntencionTipo.PRESTAMO, self.prod_hdmi, cantidad=2, dest="laboratorio")

    def test_17_dejame_una_ponchadora_pa_carlos(self):
        self._verificar("déjame una ponchadora pa Carlos", IntencionTipo.PRESTAMO, self.prod_ponchadora, cantidad=1, dest="Carlos")

    def test_18_pasame_uno_prestado_con_contexto(self):
        ctx = {"haku_ultimo_producto_id": str(self.prod_ponchadora.id), "haku_ultimo_acceso": time.time()}
        intencion = NormalizadorLenguajeHaku.interpretar("pásame uno prestado para María", self.usuario, contexto_sesion=ctx)
        self.assertEqual(intencion.tipo, IntencionTipo.PRESTAMO)
        self.assertEqual(intencion.producto, self.prod_ponchadora)
        self.assertEqual(intencion.cantidad, 1)

    def test_19_te_regreso_el_cable_hdmi_de_carlos(self):
        self._verificar("te regreso el cable HDMI de Carlos", IntencionTipo.DEVOLUCION, self.prod_hdmi, dest="Carlos")

    def test_20_devuelvo_dos_cables_hdmi(self):
        self._verificar("devuelvo dos cables HDMI", IntencionTipo.DEVOLUCION, self.prod_hdmi, cantidad=2)


# ═════════════════════════════════════════════════════════════════════════════
# MATRIZ DE PRUEBAS 4: 20 CONSULTAS CON ERRORES FONÉTICOS WHISPER
# ═════════════════════════════════════════════════════════════════════════════


class MatrizErroresFoneticosWhisperTestCase(BaseLenguajeTestCase):
    """20 pruebas que validan la resiliencia fonética ante aspiraciones, apócopes y elisiones costeñas."""

    def test_01_como_van_lo_movimiento_de_hoy(self):
        intencion = NormalizadorLenguajeHaku.interpretar("cómo van lo movimiento de hoy", self.usuario)
        self.assertEqual(intencion.tipo, IntencionTipo.CONSULTAR_METRICAS_MOVIMIENTOS)

    def test_02_que_tanto_se_ha_movio_hoy(self):
        intencion = NormalizadorLenguajeHaku.interpretar("qué tanto se ha movío hoy", self.usuario)
        self.assertEqual(intencion.tipo, IntencionTipo.CONSULTAR_METRICAS_MOVIMIENTOS)

    def test_03_sacame_do_cables_hdmi_pa_juan(self):
        intencion = NormalizadorLenguajeHaku.interpretar("sácame dos cables HDMI pa Juan", self.usuario)
        self.assertEqual(intencion.tipo, IntencionTipo.REGISTRAR_SALIDA)
        self.assertEqual(intencion.producto, self.prod_hdmi)
        self.assertEqual(intencion.destinatario, "Juan")

    def test_04_despachame_tres_de_eto_cable_con_foco(self):
        ctx = {"haku_ultimo_producto_id": str(self.prod_hdmi.id), "haku_ultimo_acceso": time.time()}
        intencion = NormalizadorLenguajeHaku.interpretar("despáchame tres de eto cable", self.usuario, contexto_sesion=ctx)
        self.assertEqual(intencion.tipo, IntencionTipo.REGISTRAR_SALIDA)
        self.assertEqual(intencion.producto, self.prod_hdmi)
        self.assertEqual(intencion.cantidad, 3)

    def test_05_mandame_un_cable_hdmi_pa_la_sala(self):
        intencion = NormalizadorLenguajeHaku.interpretar("mándame un cable HDMI pa la sala", self.usuario)
        self.assertEqual(intencion.tipo, IntencionTipo.REGISTRAR_SALIDA)
        self.assertEqual(intencion.producto, self.prod_hdmi)

    def test_06_bajame_dos_de_eto_cable_con_foco(self):
        ctx = {"haku_ultimo_producto_id": str(self.prod_hdmi.id), "haku_ultimo_acceso": time.time()}
        intencion = NormalizadorLenguajeHaku.interpretar("bájame dos de eto cable", self.usuario, contexto_sesion=ctx)
        self.assertEqual(intencion.tipo, IntencionTipo.REGISTRAR_SALIDA)
        self.assertEqual(intencion.producto, self.prod_hdmi)
        self.assertEqual(intencion.cantidad, 2)

    def test_07_quedo_pelao_el_cable_hdmi(self):
        intencion = NormalizadorLenguajeHaku.interpretar("quedó pelao el cable HDMI", self.usuario)
        self.assertEqual(intencion.tipo, IntencionTipo.CONSULTAR_STOCK)
        self.assertEqual(intencion.producto, self.prod_hdmi)

    def test_08_ehta_escaso_el_cable_hdmi(self):
        intencion = NormalizadorLenguajeHaku.interpretar("ehtá escaso el cable HDMI", self.usuario)
        self.assertEqual(intencion.tipo, IntencionTipo.CONSULTAR_STOCK)
        self.assertEqual(intencion.producto, self.prod_hdmi)

    def test_09_se_acabo_el_cable_hdmi(self):
        intencion = NormalizadorLenguajeHaku.interpretar("se acabao el cable HDMI", self.usuario)
        self.assertEqual(intencion.tipo, IntencionTipo.CONSULTAR_STOCK)
        self.assertEqual(intencion.producto, self.prod_hdmi)

    def test_10_ehta_agotao_el_cable_hdmi(self):
        intencion = NormalizadorLenguajeHaku.interpretar("ehtá agotao el cable HDMI", self.usuario)
        self.assertEqual(intencion.tipo, IntencionTipo.CONSULTAR_STOCK)
        self.assertEqual(intencion.producto, self.prod_hdmi)

    def test_11_donde_ehta_guardao_el_cable_hdmi(self):
        intencion = NormalizadorLenguajeHaku.interpretar("dónde ehtá guardao el cable HDMI", self.usuario)
        self.assertEqual(intencion.tipo, IntencionTipo.CONSULTAR_UBICACION)
        self.assertEqual(intencion.producto, self.prod_hdmi)

    def test_12_donde_ehta_tirao_el_cable_hdmi(self):
        intencion = NormalizadorLenguajeHaku.interpretar("dónde ehtá tirao el cable HDMI", self.usuario)
        self.assertEqual(intencion.tipo, IntencionTipo.CONSULTAR_UBICACION)
        self.assertEqual(intencion.producto, self.prod_hdmi)

    def test_13_que_tanto_ha_salio_de_eso_con_foco(self):
        ctx = {"haku_ultimo_producto_id": str(self.prod_hdmi.id), "haku_ultimo_acceso": time.time()}
        intencion = NormalizadorLenguajeHaku.interpretar("qué tanto ha salío de eso", self.usuario, contexto_sesion=ctx)
        self.assertEqual(intencion.tipo, IntencionTipo.CONSULTAR_PRODUCTO_MOVIMIENTOS)
        self.assertEqual(intencion.producto, self.prod_hdmi)

    def test_14_como_se_ha_movio_esa_vaina_con_foco(self):
        ctx = {"haku_ultimo_producto_id": str(self.prod_hdmi.id), "haku_ultimo_acceso": time.time()}
        intencion = NormalizadorLenguajeHaku.interpretar("cómo se ha movío esa vaina", self.usuario, contexto_sesion=ctx)
        self.assertEqual(intencion.tipo, IntencionTipo.CONSULTAR_PRODUCTO_MOVIMIENTOS)
        self.assertEqual(intencion.producto, self.prod_hdmi)

    def test_15_prestame_la_ponchadora_pa_pedro(self):
        intencion = NormalizadorLenguajeHaku.interpretar("préstame la ponchadora pa Pedro", self.usuario)
        self.assertEqual(intencion.tipo, IntencionTipo.PRESTAMO)
        self.assertEqual(intencion.producto, self.prod_ponchadora)
        self.assertEqual(intencion.destinatario, "Pedro")

    def test_16_quien_tiene_prestao_el_cable_hdmi(self):
        intencion = NormalizadorLenguajeHaku.interpretar("quién tiene prestao el cable HDMI", self.usuario)
        self.assertEqual(intencion.tipo, IntencionTipo.PRESTAMOS_ACTIVOS)
        self.assertEqual(intencion.producto, self.prod_hdmi)

    def test_17_te_regreso_el_cable_prestao_con_foco(self):
        ctx = {"haku_ultimo_producto_id": str(self.prod_hdmi.id), "haku_ultimo_acceso": time.time()}
        intencion = NormalizadorLenguajeHaku.interpretar("te regreso el cable prestao de Carlos", self.usuario, contexto_sesion=ctx)
        self.assertEqual(intencion.tipo, IntencionTipo.DEVOLUCION)
        self.assertEqual(intencion.producto, self.prod_hdmi)

    def test_18_lo_traje_otra_vez_el_cable_hdmi(self):
        intencion = NormalizadorLenguajeHaku.interpretar("lo traje otra vez el cable HDMI", self.usuario)
        self.assertEqual(intencion.tipo, IntencionTipo.DEVOLUCION)
        self.assertEqual(intencion.producto, self.prod_hdmi)

    def test_19_mete_diez_de_eto_cable_con_foco(self):
        ctx = {"haku_ultimo_producto_id": str(self.prod_hdmi.id), "haku_ultimo_acceso": time.time()}
        intencion = NormalizadorLenguajeHaku.interpretar("mete diez de eto cable", self.usuario, contexto_sesion=ctx)
        self.assertEqual(intencion.tipo, IntencionTipo.REGISTRAR_ENTRADA)
        self.assertEqual(intencion.producto, self.prod_hdmi)
        self.assertEqual(intencion.cantidad, 10)

    def test_20_llegaron_cinco_pa_inventario_con_foco(self):
        ctx = {"haku_ultimo_producto_id": str(self.prod_hdmi.id), "haku_ultimo_acceso": time.time()}
        intencion = NormalizadorLenguajeHaku.interpretar("llegaron cinco pa inventario", self.usuario, contexto_sesion=ctx)
        self.assertEqual(intencion.tipo, IntencionTipo.REGISTRAR_ENTRADA)
        self.assertEqual(intencion.producto, self.prod_hdmi)
        self.assertEqual(intencion.cantidad, 5)


# ═════════════════════════════════════════════════════════════════════════════
# BLINDAJE FINAL DE SEPARACIÓN LINGÜÍSTICA: ENTRADA COSTEÑA VS SALIDA FORMAL
# ═════════════════════════════════════════════════════════════════════════════


class BlindajeSeparacionLinguisticaTestCase(BaseLenguajeTestCase):
    """Garantiza la estricta separación arquitectónica entre:
    1. Capa de Comprensión del Operario (acepta modismos costeños y fonética relajada).
    2. Capa de Salida Empresarial (100% formal, neutral, profesional, sin regionalismos).
    """

    PALABRAS_PROHIBIDAS = [
        "ajá",
        "compae",
        "compadre",
        "mi llave",
        "cuadro",
        "la vaina",
        "esa vuelta",
        "pelao",
        "voleo",
        "caleta",
        "ombe",
    ]

    def _verificar_ausencia_regionalismos(self, texto: str):
        """Verifica que el texto de salida no contenga ningún regionalismo prohibido."""
        if not texto:
            return
        prohibidos_encontrados = ValidadorSalidaEmpresarial.detectar_regionalismos_prohibidos(texto)
        self.assertEqual(
            prohibidos_encontrados,
            [],
            f"Se detectaron regionalismos prohibidos en la salida de Haku: {prohibidos_encontrados}. Texto: '{texto}'"
        )
        for palabra in self.PALABRAS_PROHIBIDAS:
            self.assertNotIn(
                palabra.lower(),
                texto.lower(),
                f"La palabra prohibida '{palabra}' fue encontrada en la salida: '{texto}'"
            )

    # ─────────────────────────────────────────────────────────────────────────
    # SECCIÓN A: COMPRENSIÓN COSTEÑA (ENTRADA)
    # ─────────────────────────────────────────────────────────────────────────

    def test_a1_comprension_como_va_la_vaina_hoy(self):
        """Validar: 'cómo va la vaina hoy' -> CONSULTAR_METRICAS_MOVIMIENTOS."""
        intencion = NormalizadorLenguajeHaku.interpretar(
            mensaje="cómo va la vaina hoy",
            usuario=self.usuario,
        )
        self.assertEqual(intencion.tipo, IntencionTipo.CONSULTAR_METRICAS_MOVIMIENTOS)

    def test_a2_comprension_sacame_dos_de_esa_vaina_con_foco(self):
        """Validar: 'sácame dos de esa vaina' con producto en foco -> REGISTRAR_SALIDA."""
        ctx = {"haku_ultimo_producto_id": str(self.prod_hdmi.id), "haku_ultimo_acceso": time.time()}
        intencion = NormalizadorLenguajeHaku.interpretar(
            mensaje="sácame dos de esa vaina",
            usuario=self.usuario,
            contexto_sesion=ctx,
        )
        self.assertEqual(intencion.tipo, IntencionTipo.REGISTRAR_SALIDA)
        self.assertEqual(intencion.producto, self.prod_hdmi)
        self.assertEqual(intencion.cantidad, 2)

    def test_a3_comprension_dejame_uno_prestado_pa_carlos(self):
        """Validar: 'déjame uno prestado pa Carlos' con foco o mención -> PRESTAMO."""
        ctx = {"haku_ultimo_producto_id": str(self.prod_hdmi.id), "haku_ultimo_acceso": time.time()}
        intencion = NormalizadorLenguajeHaku.interpretar(
            mensaje="déjame uno prestado pa Carlos",
            usuario=self.usuario,
            contexto_sesion=ctx,
        )
        self.assertEqual(intencion.tipo, IntencionTipo.PRESTAMO)
        self.assertEqual(intencion.producto, self.prod_hdmi)
        self.assertEqual(intencion.destinatario, "Carlos")
        self.assertEqual(intencion.cantidad, 1)

    def test_a4_variaciones_foneticas_whisper(self):
        """Validar normalización fonética relajada:
        prestao -> prestado, agotao -> agotado, salío -> salido,
        movío -> movido, ehtá -> está, pa -> para,
        lo movimiento -> los movimientos, eto cable -> estos cables.
        """
        self.assertEqual(InterpretadorCosteno.normalizar_fonetica_whisper("cable prestao"), "cable prestado")
        self.assertEqual(InterpretadorCosteno.normalizar_fonetica_whisper("stock agotao"), "stock agotado")
        self.assertEqual(InterpretadorCosteno.normalizar_fonetica_whisper("cuánto ha salío"), "cuánto ha salido")
        self.assertEqual(InterpretadorCosteno.normalizar_fonetica_whisper("cómo se ha movío"), "cómo se ha movido")
        self.assertEqual(InterpretadorCosteno.normalizar_fonetica_whisper("ehtá en el estante"), "está en el estante")
        self.assertEqual(InterpretadorCosteno.normalizar_fonetica_whisper("cable pa Carlos"), "cable para Carlos")
        self.assertEqual(InterpretadorCosteno.normalizar_fonetica_whisper("lo movimiento de hoy"), "los movimientos de hoy")
        self.assertEqual(InterpretadorCosteno.normalizar_fonetica_whisper("eto cable"), "estos cables")

    # ─────────────────────────────────────────────────────────────────────────
    # SECCIÓN B: PROTECCIÓN DE SALIDA EMPRESARIAL (PRUEBAS NEGATIVAS)
    # ─────────────────────────────────────────────────────────────────────────

    def test_b1_salida_consultas_stock_no_contiene_regionalismos(self):
        """Consulta de stock generada nunca debe contener regionalismos prohibidos."""
        res = procesar_comando_inventario(
            usuario=self.usuario,
            mensaje="Ajá compae, dime cómo está el stock del cable HDMI",
        )
        self.assertEqual(res["tipo"], "consulta")
        self._verificar_ausencia_regionalismos(res["mensaje_pantalla"])
        self._verificar_ausencia_regionalismos(res["mensaje_verbal"])

    def test_b2_salida_movimientos_no_contiene_regionalismos(self):
        """Consulta de métricas generada nunca debe contener regionalismos prohibidos."""
        res = procesar_comando_inventario(
            usuario=self.usuario,
            mensaje="Ajá Haku, cómo va la vaina hoy",
        )
        self.assertEqual(res["tipo"], "consulta")
        self._verificar_ausencia_regionalismos(res["mensaje_pantalla"])
        self._verificar_ausencia_regionalismos(res["mensaje_verbal"])
        self.assertIn("Durante la jornada se registraron movimientos de inventario", res["mensaje_pantalla"])

    def test_b3_salida_prestamos_no_contiene_regionalismos(self):
        """Propuesta de préstamo generada nunca debe contener regionalismos prohibidos."""
        res = procesar_comando_inventario(
            usuario=self.usuario,
            mensaje="Compae déjame uno prestado pa Carlos del cable HDMI",
        )
        self.assertEqual(res["tipo"], "propuesta")
        self._verificar_ausencia_regionalismos(res["mensaje_pantalla"])
        self._verificar_ausencia_regionalismos(res["mensaje_verbal"])

    def test_b4_salida_devoluciones_no_contiene_regionalismos(self):
        """Propuesta de devolución generada nunca debe contener regionalismos prohibidos."""
        # Crear un préstamo previo para Carlos con su Movimiento asociado
        mov_prestamo = Movimiento.objects.create(
            clave_idempotencia="demo-prestamo-carlos-b4",
            tipo=Movimiento.Tipo.PRESTAMO,
            producto=self.prod_hdmi,
            cantidad=1,
            almacen_origen=self.almacen,
            usuario=self.usuario,
            destinatario="Carlos",
            motivo="Préstamo de prueba",
            stock_disponible_anterior=25,
            stock_disponible_posterior=24,
        )
        Prestamo.objects.create(
            movimiento=mov_prestamo,
            producto=self.prod_hdmi,
            almacen=self.almacen,
            usuario=self.usuario,
            destinatario="Carlos",
            cantidad_original=1,
            cantidad_pendiente_devolucion=1,
            estado=Prestamo.Estado.ABIERTO,
        )
        res = procesar_comando_inventario(
            usuario=self.usuario,
            mensaje="Ajá mi llave te regreso el cable prestao de Carlos",
        )
        self.assertEqual(res["tipo"], "propuesta")
        self._verificar_ausencia_regionalismos(res["mensaje_pantalla"])
        self._verificar_ausencia_regionalismos(res["mensaje_verbal"])

    def test_b5_salida_confirmaciones_no_contiene_regionalismos(self):
        """Confirmación de transacción nunca debe contener regionalismos prohibidos."""
        propuesta = {
            "id": "prop-test-blindaje",
            "tipo_operacion": "ENTRADA",
            "producto_id": self.prod_hdmi.id,
            "producto_nombre": self.prod_hdmi.nombre,
            "producto_codigo": self.prod_hdmi.codigo_interno,
            "cantidad": 5,
            "almacen_origen_id": self.almacen.id,
            "almacen_origen_nombre": self.almacen.nombre,
            "destinatario": "",
            "motivo": "Entrada probada",
            "creado_en": time.time(),
            "expira_en": time.time() + 1800,
        }
        res = ejecutar_confirmacion_propuesta(self.usuario, propuesta)
        self.assertTrue(res["exito"])
        self._verificar_ausencia_regionalismos(res["mensaje_pantalla"])
        self._verificar_ausencia_regionalismos(res["mensaje_verbal"])

    def test_b6_salida_aclaraciones_no_contiene_regionalismos(self):
        """Mensaje de aclaración nunca debe contener regionalismos prohibidos."""
        # Operario usa modismos costeños ambiguos sin producto
        res = procesar_comando_inventario(
            usuario=self.usuario,
            mensaje="Ajá compae sácame dos de esa vaina",
        )
        self.assertEqual(res["tipo"], "aclaracion")
        self._verificar_ausencia_regionalismos(res["mensaje_pantalla"])
        self._verificar_ausencia_regionalismos(res["mensaje_verbal"])

        res2 = procesar_comando_inventario(
            usuario=self.usuario,
            mensaje="Cómo se ha movido esa vuelta",
        )
        self.assertEqual(res2["tipo"], "aclaracion")
        self._verificar_ausencia_regionalismos(res2["mensaje_pantalla"])
        self._verificar_ausencia_regionalismos(res2["mensaje_verbal"])


# ═════════════════════════════════════════════════════════════════════════════
# COMPOSICIÓN SEMÁNTICA DETERMINISTA Y ESPAÑOL NEUTRO (AUDITORÍA LINGÜÍSTICA)
# ═════════════════════════════════════════════════════════════════════════════


class ComposicionSemanticaEspanolNeutroTestCase(BaseLenguajeTestCase):
    """Matriz exhaustiva de pruebas para validar la composición semántica determinista:
    - 35 frases en español neutro (movimientos, stock, ubicación, protocolo social, confirmación/cancelación).
    - 20 frases costeñas representativas (garantía de no-regresión).
    - Pruebas negativas de blindaje de salida empresarial (cero regionalismos).
    """

    PALABRAS_PROHIBIDAS = [
        "ajá", "compae", "compadre", "mi llave", "cuadro", "la vaina",
        "esa vuelta", "pelao", "voleo", "caleta", "ombe", "hágale", "dale",
    ]

    def _verificar_ausencia_regionalismos(self, texto: str):
        if not texto:
            return
        prohibidos = ValidadorSalidaEmpresarial.detectar_regionalismos_prohibidos(texto)
        self.assertEqual(prohibidos, [], f"Regionalismos detectados en salida: {prohibidos}. Texto: '{texto}'")
        for p in self.PALABRAS_PROHIBIDAS:
            self.assertNotIn(p.lower(), texto.lower(), f"Palabra prohibida '{p}' en salida: '{texto}'")

    # ─────────────────────────────────────────────────────────────────────────
    # SECCIÓN 1: ESPAÑOL NEUTRO (35 FRASES NUEVAS)
    # ─────────────────────────────────────────────────────────────────────────

    # --- 1.1 Movimientos y Resumen General ---
    def test_n01_movimientos_han_habido_hoy(self):
        res = procesar_comando_inventario(self.usuario, "qué movimientos han habido hoy")
        self.assertEqual(res["tipo"], "consulta")
        self.assertIn("Durante la jornada se registraron movimientos", res["mensaje_pantalla"])
        self._verificar_ausencia_regionalismos(res["mensaje_pantalla"])

    def test_n02_movimientos_hubo_hoy(self):
        res = procesar_comando_inventario(self.usuario, "qué movimientos hubo hoy")
        self.assertEqual(res["tipo"], "consulta")
        self.assertIn("Durante la jornada se registraron movimientos", res["mensaje_pantalla"])
        self._verificar_ausencia_regionalismos(res["mensaje_pantalla"])

    def test_n03_movimientos_se_hicieron_hoy(self):
        res = procesar_comando_inventario(self.usuario, "qué movimientos se hicieron hoy")
        self.assertEqual(res["tipo"], "consulta")
        self.assertIn("Durante la jornada se registraron movimientos", res["mensaje_pantalla"])
        self._verificar_ausencia_regionalismos(res["mensaje_pantalla"])

    def test_n04_movimientos_se_registraron_hoy(self):
        res = procesar_comando_inventario(self.usuario, "qué movimientos se registraron hoy")
        self.assertEqual(res["tipo"], "consulta")
        self.assertIn("Durante la jornada se registraron movimientos", res["mensaje_pantalla"])
        self._verificar_ausencia_regionalismos(res["mensaje_pantalla"])

    def test_n05_operaciones_tuvimos_hoy(self):
        res = procesar_comando_inventario(self.usuario, "qué operaciones tuvimos hoy")
        self.assertEqual(res["tipo"], "consulta")
        self.assertIn("Durante la jornada se registraron movimientos", res["mensaje_pantalla"])
        self._verificar_ausencia_regionalismos(res["mensaje_pantalla"])

    def test_n06_transacciones_hubo_hoy(self):
        res = procesar_comando_inventario(self.usuario, "qué transacciones hubo hoy")
        self.assertEqual(res["tipo"], "consulta")
        self.assertIn("Durante la jornada se registraron movimientos", res["mensaje_pantalla"])
        self._verificar_ausencia_regionalismos(res["mensaje_pantalla"])

    def test_n07_actividad_hubo_hoy_en_bodega(self):
        res = procesar_comando_inventario(self.usuario, "qué actividad hubo hoy en bodega")
        self.assertEqual(res["tipo"], "consulta")
        self.assertIn("Durante la jornada se registraron movimientos", res["mensaje_pantalla"])
        self._verificar_ausencia_regionalismos(res["mensaje_pantalla"])

    def test_n08_cuantos_despachos_se_hicieron_hoy(self):
        res = procesar_comando_inventario(self.usuario, "cuántos despachos se hicieron hoy")
        self.assertEqual(res["tipo"], "consulta")
        self.assertIn("Durante la jornada se registraron movimientos", res["mensaje_pantalla"])
        self._verificar_ausencia_regionalismos(res["mensaje_pantalla"])

    def test_n09_como_estuvo_el_movimiento_hoy(self):
        res = procesar_comando_inventario(self.usuario, "cómo estuvo el movimiento hoy")
        self.assertEqual(res["tipo"], "consulta")
        self.assertIn("Durante la jornada se registraron movimientos", res["mensaje_pantalla"])
        self._verificar_ausencia_regionalismos(res["mensaje_pantalla"])

    def test_n10_dame_el_resumen_del_dia(self):
        res = procesar_comando_inventario(self.usuario, "dame el resumen del día")
        self.assertEqual(res["tipo"], "consulta")
        self.assertIn("Resumen de inventario", res["mensaje_pantalla"])
        self._verificar_ausencia_regionalismos(res["mensaje_pantalla"])

    # --- 1.2 Stock y Disponibilidad ---
    def test_n11_cuanto_queda_sin_producto(self):
        res = procesar_comando_inventario(self.usuario, "cuánto queda")
        self.assertEqual(res["tipo"], "aclaracion")
        self.assertIn("¿De cuál producto deseas consultar el stock?", res["mensaje_pantalla"])
        self._verificar_ausencia_regionalismos(res["mensaje_pantalla"])

    def test_n12_cuantas_unidades_hay_sin_producto(self):
        res = procesar_comando_inventario(self.usuario, "cuántas unidades hay")
        self.assertEqual(res["tipo"], "aclaracion")
        self.assertIn("¿De cuál producto deseas consultar el stock?", res["mensaje_pantalla"])
        self._verificar_ausencia_regionalismos(res["mensaje_pantalla"])

    def test_n13_hay_disponibilidad_sin_producto(self):
        res = procesar_comando_inventario(self.usuario, "hay disponibilidad")
        self.assertEqual(res["tipo"], "aclaracion")
        self.assertIn("¿De cuál producto deseas consultar el stock?", res["mensaje_pantalla"])
        self._verificar_ausencia_regionalismos(res["mensaje_pantalla"])

    def test_n14_cuantas_piezas_quedan_sin_producto(self):
        res = procesar_comando_inventario(self.usuario, "cuántas piezas quedan")
        self.assertEqual(res["tipo"], "aclaracion")
        self.assertIn("¿De cuál producto deseas consultar el stock?", res["mensaje_pantalla"])
        self._verificar_ausencia_regionalismos(res["mensaje_pantalla"])

    def test_n15_que_cantidad_hay_disponible_sin_producto(self):
        res = procesar_comando_inventario(self.usuario, "qué cantidad hay disponible")
        self.assertEqual(res["tipo"], "aclaracion")
        self.assertIn("¿De cuál producto deseas consultar el stock?", res["mensaje_pantalla"])
        self._verificar_ausencia_regionalismos(res["mensaje_pantalla"])

    def test_n16_queda_inventario_de_cable_hdmi(self):
        res = procesar_comando_inventario(self.usuario, "queda inventario de cable HDMI")
        self.assertEqual(res["tipo"], "consulta")
        self.assertEqual(res["datos"]["codigo"], "CAB-1001")
        self._verificar_ausencia_regionalismos(res["mensaje_pantalla"])

    def test_n17_tenemos_cable_hdmi(self):
        res = procesar_comando_inventario(self.usuario, "tenemos cable HDMI")
        self.assertEqual(res["tipo"], "consulta")
        self.assertEqual(res["datos"]["codigo"], "CAB-1001")
        self._verificar_ausencia_regionalismos(res["mensaje_pantalla"])

    def test_n18_hay_existencia_de_bobina(self):
        res = procesar_comando_inventario(self.usuario, "hay existencia de bobina")
        self.assertEqual(res["tipo"], "consulta")
        self.assertEqual(res["datos"]["codigo"], "CAB-1002")
        self._verificar_ausencia_regionalismos(res["mensaje_pantalla"])

    # --- 1.3 Ubicación y Contexto Físico ---
    def test_n19_donde_esta_el_cable_hdmi(self):
        res = procesar_comando_inventario(self.usuario, "dónde está el cable HDMI")
        self.assertEqual(res["tipo"], "consulta")
        self.assertEqual(res["datos"]["codigo"], "CAB-1001")
        self.assertIn("Estante A-1", res["mensaje_pantalla"])
        self._verificar_ausencia_regionalismos(res["mensaje_pantalla"])

    def test_n20_donde_se_encuentra_sin_producto(self):
        res = procesar_comando_inventario(self.usuario, "dónde se encuentra")
        self.assertEqual(res["tipo"], "aclaracion")
        self.assertIn("¿De cuál producto deseas conocer la ubicación?", res["mensaje_pantalla"])
        self._verificar_ausencia_regionalismos(res["mensaje_pantalla"])

    def test_n21_en_que_estante_esta_sin_producto(self):
        res = procesar_comando_inventario(self.usuario, "en qué estante está")
        self.assertEqual(res["tipo"], "aclaracion")
        self.assertIn("¿De cuál producto deseas conocer la ubicación?", res["mensaje_pantalla"])
        self._verificar_ausencia_regionalismos(res["mensaje_pantalla"])

    def test_n22_en_que_rack_esta_sin_producto(self):
        res = procesar_comando_inventario(self.usuario, "en qué rack está")
        self.assertEqual(res["tipo"], "aclaracion")
        self.assertIn("¿De cuál producto deseas conocer la ubicación?", res["mensaje_pantalla"])
        self._verificar_ausencia_regionalismos(res["mensaje_pantalla"])

    def test_n23_en_que_pasillo_esta_sin_producto(self):
        res = procesar_comando_inventario(self.usuario, "en qué pasillo está")
        self.assertEqual(res["tipo"], "aclaracion")
        self.assertIn("¿De cuál producto deseas conocer la ubicación?", res["mensaje_pantalla"])
        self._verificar_ausencia_regionalismos(res["mensaje_pantalla"])

    def test_n24_en_que_gaveta_esta_sin_producto(self):
        res = procesar_comando_inventario(self.usuario, "en qué gaveta está")
        self.assertEqual(res["tipo"], "aclaracion")
        self.assertIn("¿De cuál producto deseas conocer la ubicación?", res["mensaje_pantalla"])
        self._verificar_ausencia_regionalismos(res["mensaje_pantalla"])

    def test_n25_en_que_repisa_esta_sin_producto(self):
        res = procesar_comando_inventario(self.usuario, "en qué repisa está")
        self.assertEqual(res["tipo"], "aclaracion")
        self.assertIn("¿De cuál producto deseas conocer la ubicación?", res["mensaje_pantalla"])
        self._verificar_ausencia_regionalismos(res["mensaje_pantalla"])

    def test_n26_cual_es_la_localizacion_sin_producto(self):
        res = procesar_comando_inventario(self.usuario, "cuál es la localización")
        self.assertEqual(res["tipo"], "aclaracion")
        self.assertIn("¿De cuál producto deseas conocer la ubicación?", res["mensaje_pantalla"])
        self._verificar_ausencia_regionalismos(res["mensaje_pantalla"])

    def test_n27_donde_guardan_este_producto_sin_producto(self):
        res = procesar_comando_inventario(self.usuario, "dónde guardan este producto")
        self.assertEqual(res["tipo"], "aclaracion")
        self.assertIn("¿De cuál producto deseas conocer la ubicación?", res["mensaje_pantalla"])
        self._verificar_ausencia_regionalismos(res["mensaje_pantalla"])

    # --- 1.4 Protocolo Social ---
    def test_n28_saludo_hola(self):
        res = procesar_comando_inventario(self.usuario, "hola")
        self.assertEqual(res["tipo"], "saludo")
        self.assertEqual(res["mensaje_pantalla"], "Buenos días. Sistema Haku de control de inventario listo. ¿Qué consulta u operación deseas realizar?")
        self._verificar_ausencia_regionalismos(res["mensaje_pantalla"])

    def test_n29_saludo_buenos_dias(self):
        res = procesar_comando_inventario(self.usuario, "buenos días")
        self.assertEqual(res["tipo"], "saludo")
        self.assertEqual(res["mensaje_pantalla"], "Buenos días. Sistema Haku de control de inventario listo. ¿Qué consulta u operación deseas realizar?")
        self._verificar_ausencia_regionalismos(res["mensaje_pantalla"])

    def test_n30_agradecimiento_gracias(self):
        res = procesar_comando_inventario(self.usuario, "gracias")
        self.assertEqual(res["tipo"], "agradecimiento")
        self.assertEqual(res["mensaje_pantalla"], "Con gusto. El sistema permanece disponible para nuevas consultas u operaciones.")
        self._verificar_ausencia_regionalismos(res["mensaje_pantalla"])

    def test_n31_agradecimiento_muchas_gracias(self):
        res = procesar_comando_inventario(self.usuario, "muchas gracias")
        self.assertEqual(res["tipo"], "agradecimiento")
        self.assertEqual(res["mensaje_pantalla"], "Con gusto. El sistema permanece disponible para nuevas consultas u operaciones.")
        self._verificar_ausencia_regionalismos(res["mensaje_pantalla"])

    # --- 1.5 Confirmaciones y Cancelaciones Desacopladas ---
    def test_n32_confirmar_sin_propuesta_activa(self):
        res = procesar_comando_inventario(self.usuario, "sí")
        self.assertEqual(res["tipo"], "accion_confirmar")
        self.assertEqual(res["mensaje_pantalla"], "No hay ninguna transacción pendiente de confirmación en este momento.")
        self._verificar_ausencia_regionalismos(res["mensaje_pantalla"])

    def test_n33_confirmar_con_propuesta_activa(self):
        propuesta = {
            "id": "prop-test-neutro",
            "tipo_operacion": "ENTRADA",
            "producto_id": self.prod_hdmi.id,
            "cantidad": 5,
            "expira_en": time.time() + 1800,
        }
        res = procesar_comando_inventario(self.usuario, "correcto", propuesta_activa=propuesta)
        self.assertEqual(res["tipo"], "accion_confirmar")
        self.assertEqual(res["propuesta_id"], "prop-test-neutro")
        self._verificar_ausencia_regionalismos(res["mensaje_pantalla"])

    def test_n34_cancelar_sin_propuesta_activa(self):
        res = procesar_comando_inventario(self.usuario, "cancelar")
        self.assertEqual(res["tipo"], "accion_cancelar")
        self.assertEqual(res["mensaje_pantalla"], "Entendido. No hay operaciones pendientes en ejecución.")
        self._verificar_ausencia_regionalismos(res["mensaje_pantalla"])

    def test_n35_cancelar_con_propuesta_activa(self):
        propuesta = {
            "id": "prop-test-cancelar",
            "tipo_operacion": "ENTRADA",
            "producto_id": self.prod_hdmi.id,
            "cantidad": 5,
            "expira_en": time.time() + 1800,
        }
        res = procesar_comando_inventario(self.usuario, "déjalo así", propuesta_activa=propuesta)
        self.assertEqual(res["tipo"], "accion_cancelar")
        self.assertEqual(res["propuesta_id"], "prop-test-cancelar")
        self._verificar_ausencia_regionalismos(res["mensaje_pantalla"])

    # ─────────────────────────────────────────────────────────────────────────
    # SECCIÓN 2: 20 FRASES COSTEÑAS EXISTENTES (GARANTÍA DE NO-REGRESIÓN)
    # ─────────────────────────────────────────────────────────────────────────

    def test_c01_como_va_la_vaina_hoy(self):
        intencion = NormalizadorLenguajeHaku.interpretar("cómo va la vaina hoy", self.usuario)
        self.assertEqual(intencion.tipo, IntencionTipo.CONSULTAR_METRICAS_MOVIMIENTOS)

    def test_c02_como_estuvo_la_vuelta(self):
        intencion = NormalizadorLenguajeHaku.interpretar("cómo estuvo la vuelta", self.usuario)
        self.assertEqual(intencion.tipo, IntencionTipo.CONSULTAR_METRICAS_MOVIMIENTOS)

    def test_c03_el_voleo_de_hoy(self):
        intencion = NormalizadorLenguajeHaku.interpretar("el voleo de hoy", self.usuario)
        self.assertEqual(intencion.tipo, IntencionTipo.CONSULTAR_METRICAS_MOVIMIENTOS)

    def test_c04_que_se_ha_movido_hoy(self):
        intencion = NormalizadorLenguajeHaku.interpretar("qué se ha movido hoy", self.usuario)
        self.assertEqual(intencion.tipo, IntencionTipo.CONSULTAR_METRICAS_MOVIMIENTOS)

    def test_c05_como_va_eso_hoy(self):
        intencion = NormalizadorLenguajeHaku.interpretar("cómo va eso hoy", self.usuario)
        self.assertEqual(intencion.tipo, IntencionTipo.CONSULTAR_METRICAS_MOVIMIENTOS)

    def test_c06_sacame_dos_de_esa_vaina_con_foco(self):
        ctx = {"haku_ultimo_producto_id": str(self.prod_hdmi.id), "haku_ultimo_acceso": time.time()}
        intencion = NormalizadorLenguajeHaku.interpretar("sácame dos de esa vaina", self.usuario, contexto_sesion=ctx)
        self.assertEqual(intencion.tipo, IntencionTipo.REGISTRAR_SALIDA)
        self.assertEqual(intencion.producto, self.prod_hdmi)
        self.assertEqual(intencion.cantidad, 2)

    def test_c07_dejame_uno_prestado_pa_carlos_con_foco(self):
        ctx = {"haku_ultimo_producto_id": str(self.prod_hdmi.id), "haku_ultimo_acceso": time.time()}
        intencion = NormalizadorLenguajeHaku.interpretar("déjame uno prestado pa Carlos", self.usuario, contexto_sesion=ctx)
        self.assertEqual(intencion.tipo, IntencionTipo.PRESTAMO)
        self.assertEqual(intencion.producto, self.prod_hdmi)
        self.assertEqual(intencion.destinatario, "Carlos")

    def test_c08_quedo_pelao_el_cable_hdmi(self):
        intencion = NormalizadorLenguajeHaku.interpretar("quedó pelao el cable HDMI", self.usuario)
        self.assertEqual(intencion.tipo, IntencionTipo.CONSULTAR_STOCK)
        self.assertEqual(intencion.producto, self.prod_hdmi)

    def test_c09_ehta_escaso_el_cable_hdmi(self):
        intencion = NormalizadorLenguajeHaku.interpretar("ehtá escaso el cable HDMI", self.usuario)
        self.assertEqual(intencion.tipo, IntencionTipo.CONSULTAR_STOCK)
        self.assertEqual(intencion.producto, self.prod_hdmi)

    def test_c10_se_acabo_el_cable_hdmi(self):
        intencion = NormalizadorLenguajeHaku.interpretar("se acabao el cable HDMI", self.usuario)
        self.assertEqual(intencion.tipo, IntencionTipo.CONSULTAR_STOCK)
        self.assertEqual(intencion.producto, self.prod_hdmi)

    def test_c11_ehta_agotao_el_cable_hdmi(self):
        intencion = NormalizadorLenguajeHaku.interpretar("ehtá agotao el cable HDMI", self.usuario)
        self.assertEqual(intencion.tipo, IntencionTipo.CONSULTAR_STOCK)
        self.assertEqual(intencion.producto, self.prod_hdmi)

    def test_c12_donde_ehta_guardao_el_cable_hdmi(self):
        intencion = NormalizadorLenguajeHaku.interpretar("dónde ehtá guardao el cable HDMI", self.usuario)
        self.assertEqual(intencion.tipo, IntencionTipo.CONSULTAR_UBICACION)
        self.assertEqual(intencion.producto, self.prod_hdmi)

    def test_c13_donde_ehta_tirao_el_cable_hdmi(self):
        intencion = NormalizadorLenguajeHaku.interpretar("dónde ehtá tirao el cable HDMI", self.usuario)
        self.assertEqual(intencion.tipo, IntencionTipo.CONSULTAR_UBICACION)
        self.assertEqual(intencion.producto, self.prod_hdmi)

    def test_c14_que_tanto_ha_salio_de_eso_con_foco(self):
        ctx = {"haku_ultimo_producto_id": str(self.prod_hdmi.id), "haku_ultimo_acceso": time.time()}
        intencion = NormalizadorLenguajeHaku.interpretar("qué tanto ha salío de eso", self.usuario, contexto_sesion=ctx)
        self.assertEqual(intencion.tipo, IntencionTipo.CONSULTAR_PRODUCTO_MOVIMIENTOS)
        self.assertEqual(intencion.producto, self.prod_hdmi)

    def test_c15_como_se_ha_movio_esa_vaina_con_foco(self):
        ctx = {"haku_ultimo_producto_id": str(self.prod_hdmi.id), "haku_ultimo_acceso": time.time()}
        intencion = NormalizadorLenguajeHaku.interpretar("cómo se ha movío esa vaina", self.usuario, contexto_sesion=ctx)
        self.assertEqual(intencion.tipo, IntencionTipo.CONSULTAR_PRODUCTO_MOVIMIENTOS)
        self.assertEqual(intencion.producto, self.prod_hdmi)

    def test_c16_prestame_la_ponchadora_pa_pedro(self):
        intencion = NormalizadorLenguajeHaku.interpretar("préstame la ponchadora pa Pedro", self.usuario)
        self.assertEqual(intencion.tipo, IntencionTipo.PRESTAMO)
        self.assertEqual(intencion.producto, self.prod_ponchadora)
        self.assertEqual(intencion.destinatario, "Pedro")

    def test_c17_quien_tiene_prestao_el_cable_hdmi(self):
        intencion = NormalizadorLenguajeHaku.interpretar("quién tiene prestao el cable HDMI", self.usuario)
        self.assertEqual(intencion.tipo, IntencionTipo.PRESTAMOS_ACTIVOS)
        self.assertEqual(intencion.producto, self.prod_hdmi)

    def test_c18_te_regreso_el_cable_prestao_con_foco(self):
        ctx = {"haku_ultimo_producto_id": str(self.prod_hdmi.id), "haku_ultimo_acceso": time.time()}
        intencion = NormalizadorLenguajeHaku.interpretar("te regreso el cable prestao de Carlos", self.usuario, contexto_sesion=ctx)
        self.assertEqual(intencion.tipo, IntencionTipo.DEVOLUCION)
        self.assertEqual(intencion.producto, self.prod_hdmi)

    def test_c19_lo_traje_otra_vez_el_cable_hdmi(self):
        intencion = NormalizadorLenguajeHaku.interpretar("lo traje otra vez el cable HDMI", self.usuario)
        self.assertEqual(intencion.tipo, IntencionTipo.DEVOLUCION)
        self.assertEqual(intencion.producto, self.prod_hdmi)

    def test_c20_mete_diez_de_eto_cable_con_foco(self):
        ctx = {"haku_ultimo_producto_id": str(self.prod_hdmi.id), "haku_ultimo_acceso": time.time()}
        intencion = NormalizadorLenguajeHaku.interpretar("mete diez de eto cable", self.usuario, contexto_sesion=ctx)
        self.assertEqual(intencion.tipo, IntencionTipo.REGISTRAR_ENTRADA)
        self.assertEqual(intencion.producto, self.prod_hdmi)
        self.assertEqual(intencion.cantidad, 10)


class SeparacionFallbackLinguisticoTestCase(BaseLenguajeTestCase):
    """Pruebas para la separación semántica entre baja comprensión y fuera de dominio."""

    # 1. SALUDOS
    def test_saludo_buenas(self):
        intencion = NormalizadorLenguajeHaku.interpretar("buenas", self.usuario)
        self.assertEqual(intencion.tipo, IntencionTipo.SALUDO)
        res = procesar_comando_inventario(self.usuario, "buenas")
        self.assertEqual(res["tipo"], "saludo")

    def test_saludo_buenas_buenas(self):
        intencion = NormalizadorLenguajeHaku.interpretar("buenas buenas", self.usuario)
        self.assertEqual(intencion.tipo, IntencionTipo.SALUDO)
        res = procesar_comando_inventario(self.usuario, "buenas buenas")
        self.assertEqual(res["tipo"], "saludo")

    def test_saludo_hola(self):
        intencion = NormalizadorLenguajeHaku.interpretar("hola", self.usuario)
        self.assertEqual(intencion.tipo, IntencionTipo.SALUDO)

    def test_saludo_hola_haku(self):
        intencion = NormalizadorLenguajeHaku.interpretar("hola Haku", self.usuario)
        self.assertEqual(intencion.tipo, IntencionTipo.SALUDO)

    def test_saludo_buenos_dias(self):
        intencion = NormalizadorLenguajeHaku.interpretar("buenos días", self.usuario)
        self.assertEqual(intencion.tipo, IntencionTipo.SALUDO)

    def test_saludo_saludos(self):
        intencion = NormalizadorLenguajeHaku.interpretar("saludos", self.usuario)
        self.assertEqual(intencion.tipo, IntencionTipo.SALUDO)

    # 2. AGRADECIMIENTO
    def test_agradecimiento_gracias(self):
        intencion = NormalizadorLenguajeHaku.interpretar("gracias", self.usuario)
        self.assertEqual(intencion.tipo, IntencionTipo.AGRADECIMIENTO)

    def test_agradecimiento_muchas_gracias(self):
        intencion = NormalizadorLenguajeHaku.interpretar("muchas gracias", self.usuario)
        self.assertEqual(intencion.tipo, IntencionTipo.AGRADECIMIENTO)

    def test_agradecimiento_te_agradezco(self):
        intencion = NormalizadorLenguajeHaku.interpretar("te agradezco", self.usuario)
        self.assertEqual(intencion.tipo, IntencionTipo.AGRADECIMIENTO)

    # 3. BAJA COMPRENSIÓN
    def test_baja_comprension_menos(self):
        intencion = NormalizadorLenguajeHaku.interpretar("menos", self.usuario)
        self.assertEqual(intencion.tipo, IntencionTipo.SOLICITUD_NO_COMPRENDIDA)
        res = procesar_comando_inventario(self.usuario, "menos")
        self.assertEqual(res["tipo"], "no_comprendido")
        self.assertEqual(res["mensaje_pantalla"], "Disculpa, no entendí la solicitud. ¿Puedes repetirla?")

    def test_baja_comprension_eh(self):
        intencion = NormalizadorLenguajeHaku.interpretar("eh", self.usuario)
        self.assertEqual(intencion.tipo, IntencionTipo.SOLICITUD_NO_COMPRENDIDA)

    def test_baja_comprension_mmm(self):
        intencion = NormalizadorLenguajeHaku.interpretar("mmm", self.usuario)
        self.assertEqual(intencion.tipo, IntencionTipo.SOLICITUD_NO_COMPRENDIDA)

    def test_baja_comprension_ah(self):
        intencion = NormalizadorLenguajeHaku.interpretar("ah", self.usuario)
        self.assertEqual(intencion.tipo, IntencionTipo.SOLICITUD_NO_COMPRENDIDA)

    def test_baja_comprension_que(self):
        intencion = NormalizadorLenguajeHaku.interpretar("qué", self.usuario)
        self.assertEqual(intencion.tipo, IntencionTipo.SOLICITUD_NO_COMPRENDIDA)

    def test_baja_comprension_aja(self):
        intencion = NormalizadorLenguajeHaku.interpretar("ajá", self.usuario)
        self.assertEqual(intencion.tipo, IntencionTipo.SOLICITUD_NO_COMPRENDIDA)

    # 4. FUERA DE DOMINIO REAL
    def test_fuera_dominio_clima(self):
        intencion = NormalizadorLenguajeHaku.interpretar("cuál es el clima mañana", self.usuario)
        self.assertEqual(intencion.tipo, IntencionTipo.NO_INTENCION_INVENTARIO)
        res = procesar_comando_inventario(self.usuario, "cuál es el clima mañana")
        self.assertEqual(res["tipo"], "fuera_de_dominio")
        self.assertEqual(
            res["mensaje_pantalla"],
            "Solo gestiono operaciones y consultas del inventario (stock, ubicaciones, movimientos, préstamos y métricas del almacén)."
        )

    def test_fuera_dominio_musica(self):
        intencion = NormalizadorLenguajeHaku.interpretar("pon música", self.usuario)
        self.assertEqual(intencion.tipo, IntencionTipo.NO_INTENCION_INVENTARIO)
        res = procesar_comando_inventario(self.usuario, "pon música")
        self.assertEqual(res["tipo"], "fuera_de_dominio")

    def test_fuera_dominio_chiste(self):
        intencion = NormalizadorLenguajeHaku.interpretar("cuéntame un chiste", self.usuario)
        self.assertEqual(intencion.tipo, IntencionTipo.NO_INTENCION_INVENTARIO)
        res = procesar_comando_inventario(self.usuario, "cuéntame un chiste")
        self.assertEqual(res["tipo"], "fuera_de_dominio")

    def test_fuera_dominio_partido(self):
        intencion = NormalizadorLenguajeHaku.interpretar("quién ganó el partido", self.usuario)
        self.assertEqual(intencion.tipo, IntencionTipo.NO_INTENCION_INVENTARIO)
        res = procesar_comando_inventario(self.usuario, "quién ganó el partido")
        self.assertEqual(res["tipo"], "fuera_de_dominio")

    def test_fuera_dominio_noticias(self):
        intencion = NormalizadorLenguajeHaku.interpretar("qué noticias hay", self.usuario)
        self.assertEqual(intencion.tipo, IntencionTipo.NO_INTENCION_INVENTARIO)
        res = procesar_comando_inventario(self.usuario, "qué noticias hay")
        self.assertEqual(res["tipo"], "fuera_de_dominio")

    # 5. REGRESIÓN: NO FALSEAR OPERACIONES CON 'MENOS' NI CONSULTAS SIN PRODUCTO
    def test_regresion_dame_cinco_menos_de_cable_hdmi(self):
        """'dame cinco menos de cable HDMI' NO debe ser SOLICITUD_NO_COMPRENDIDA."""
        intencion = NormalizadorLenguajeHaku.interpretar("dame cinco menos de cable HDMI", self.usuario)
        self.assertNotEqual(intencion.tipo, IntencionTipo.SOLICITUD_NO_COMPRENDIDA)
        self.assertEqual(intencion.producto, self.prod_hdmi)

    def test_regresion_quedan_menos_unidades_de_cable_hdmi(self):
        """'quedan menos unidades de cable HDMI' NO debe ser SOLICITUD_NO_COMPRENDIDA."""
        intencion = NormalizadorLenguajeHaku.interpretar("quedan menos unidades de cable HDMI", self.usuario)
        self.assertNotEqual(intencion.tipo, IntencionTipo.SOLICITUD_NO_COMPRENDIDA)
        self.assertEqual(intencion.tipo, IntencionTipo.CONSULTAR_STOCK)
        self.assertEqual(intencion.producto, self.prod_hdmi)

    def test_regresion_cuanto_stock_hay_sin_producto(self):
        """'cuánto stock hay' debe mantener aclaración de producto (SOLICITAR_ACLARACION)."""
        intencion = NormalizadorLenguajeHaku.interpretar("cuánto stock hay", self.usuario)
        self.assertEqual(intencion.tipo, IntencionTipo.SOLICITAR_ACLARACION)
        self.assertIn("stock", intencion.mensaje_aclaracion.lower())

    def test_prioridad_saludo_con_operacion(self):
        """'buenas, sácame dos cables' debe priorizar la operación y no ser saludo aislado."""
        intencion = NormalizadorLenguajeHaku.interpretar("buenas, sácame dos cables", self.usuario)
        self.assertNotEqual(intencion.tipo, IntencionTipo.SALUDO)
        self.assertIn(intencion.tipo, [IntencionTipo.REGISTRAR_SALIDA, IntencionTipo.SOLICITAR_ACLARACION])





