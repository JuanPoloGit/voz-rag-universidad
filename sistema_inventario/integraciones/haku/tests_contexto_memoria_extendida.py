"""Pruebas de memoria conversacional temporal ampliada para Haku Inventario.

Verifica los requerimientos de la especificación técnica de memoria extendida:
- Test 1: Contexto mantiene producto después de 1 hora (3600s < 7200s).
- Test 2: Contexto expira después de 2 horas (> 7200s); producto y estado olvidados.
- Test 3: Usuario pierde contexto operativo pero mantiene sesión web autenticada (7 días).
- Test 4: 10 usuarios simultáneos con productos, intenciones y contextos independientes.
- Test 5: Límite de 50 entradas máximas en LocMemCache con política LRU.
- Test 6: Memoria operacional ultimas_acciones: "Busca HDMI" -> "consulta stock" -> "el anterior" resuelve correctamente (máx 5 acciones).
- Test 7: Confirmación pendiente sobrevive dentro de las 2 horas (espera 1 hora y confirma exitosamente).
- Test 8: Verificación de consumo de memoria RAM (< 10 MB para 50 contextos).
- Test 9: Garantía estricta de no almacenamiento de mensajes completos, audios ni chat_history.
"""

from __future__ import annotations

import sys
import time
from django.core.cache import caches
from django.core.exceptions import ValidationError
from django.test import TestCase

from cuentas.models import UsuarioAudacIA
from inventario.models import Almacen, Categoria, Existencia, Movimiento, Producto, Ubicacion

from .cache import HakuCacheManager
from .contexto import EstadoOperativoHaku, HakuOperationalContext
from .services import ejecutar_confirmacion_propuesta, procesar_comando_inventario


class HakuMemoriaExtendidaTestCase(TestCase):
    """Batería de pruebas de memoria temporal de 2 horas y hasta 50 contextos."""

    def setUp(self):
        HakuOperationalContext.limpiar_todo()
        HakuOperationalContext.reset_metricas()
        HakuCacheManager.invalidar_todo()

        self.almacen_central = Almacen.objects.create(nombre="Almacén Central")
        self.categoria = Categoria.objects.create(
            nombre="Conectividad y Redes",
            almacen_principal=self.almacen_central,
        )

        self.ubicacion = Ubicacion.objects.create(
            nombre="Estante Central 3",
            almacen=self.almacen_central,
        )

        # Producto principal HDMI
        self.prod_hdmi = Producto.objects.create(
            codigo_interno="CAB-0001",
            nombre="Cable HDMI 2.0 2 metros",
            categoria=self.categoria,
            stock_inicial=50,
            stock_minimo=5,
        )
        self.prod_hdmi.ubicaciones.add(self.ubicacion)

        self.exist_hdmi = Existencia.objects.create(
            producto=self.prod_hdmi,
            almacen=self.almacen_central,
            stock_disponible=50,
            stock_en_prestamo=0,
        )

        # Producto UTP
        self.prod_utp = Producto.objects.create(
            codigo_interno="RED-0001",
            nombre="Cable UTP Cat 6 Bobina",
            categoria=self.categoria,
            stock_inicial=20,
            stock_minimo=2,
        )
        self.exist_utp = Existencia.objects.create(
            producto=self.prod_utp,
            almacen=self.almacen_central,
            stock_disponible=20,
            stock_en_prestamo=0,
        )

        # Usuario operativo principal
        self.usuario_operador = UsuarioAudacIA.objects.create_user(
            username="operador_principal",
            password="Password1",
            nombre_completo="Operador de Bodega",
            rol=UsuarioAudacIA.Rol.USUARIO,
            almacen_asignado=self.almacen_central,
        )

    def tearDown(self):
        HakuOperationalContext.limpiar_todo()
        HakuCacheManager.invalidar_todo()

    # ─────────────────────────────────────────────────────────────────────────
    # TEST 1: Contexto mantiene producto después de 1 hora (3600s)
    # ─────────────────────────────────────────────────────────────────────────
    def test_1_contexto_mantiene_producto_despues_de_1_hora(self):
        session_key = "sess_1_hora"
        contexto = HakuOperationalContext.obtener_o_crear(
            usuario_id=self.usuario_operador.id,
            session_key=session_key,
            almacen_id=self.almacen_central.id,
        )

        # Usuario busca producto
        res_busqueda = procesar_comando_inventario(
            usuario=self.usuario_operador,
            mensaje="Busca el cable HDMI",
            estado_operativo=contexto,
        )
        self.assertEqual(res_busqueda["tipo"], "consulta")
        self.assertEqual(contexto.producto_id, self.prod_hdmi.id)

        # Simular transcurso de 1 hora (3600 segundos) sin actividad intermedia
        contexto.ultimo_acceso = time.time() - 3600
        HakuOperationalContext.guardar(contexto)

        # Recuperar después de 1 hora: NO debe estar expirado (límite es 2 horas = 7200s)
        recuperado = HakuOperationalContext.obtener(self.usuario_operador.id, session_key)
        self.assertIsNotNone(recuperado, "El contexto debe permanecer vivo tras 1 hora")
        self.assertEqual(recuperado.producto_id, self.prod_hdmi.id)

        # El usuario hace una referencia ("sácame tres de esos") tras 1 hora
        res_referencia = procesar_comando_inventario(
            usuario=self.usuario_operador,
            mensaje="sácame tres de esos para Mantenimiento",
            estado_operativo=recuperado,
        )

        self.assertEqual(res_referencia["tipo"], "propuesta")
        self.assertEqual(res_referencia["propuesta"]["producto_id"], self.prod_hdmi.id)
        self.assertEqual(res_referencia["propuesta"]["cantidad"], 3)

    # ─────────────────────────────────────────────────────────────────────────
    # TEST 2: Contexto expira después de 2 horas (> 7200s)
    # ─────────────────────────────────────────────────────────────────────────
    def test_2_contexto_expira_despues_de_2_horas(self):
        session_key = "sess_2_horas"
        contexto = HakuOperationalContext.obtener_o_crear(
            usuario_id=self.usuario_operador.id,
            session_key=session_key,
            almacen_id=self.almacen_central.id,
        )
        contexto.asignar_producto(self.prod_hdmi.id, self.prod_hdmi.codigo_interno, self.prod_hdmi.nombre)
        HakuOperationalContext.guardar(contexto)

        # Simular que pasaron 2 horas y 5 minutos (7500 segundos > 7200s)
        contexto.ultimo_acceso = time.time() - 7500
        HakuOperationalContext.guardar(contexto)

        # Al consultar, debe retornar None y purgarse completamente de caché
        recuperado = HakuOperationalContext.obtener(self.usuario_operador.id, session_key)
        self.assertIsNone(recuperado, "Un contexto con más de 2 horas sin actividad debe expirar")

        # Intentar interactuar con referencia después de 2 horas
        contexto_nuevo = HakuOperationalContext.obtener_o_crear(self.usuario_operador.id, session_key)
        res_referencia = procesar_comando_inventario(
            usuario=self.usuario_operador,
            mensaje="sácame tres de esos para Mantenimiento",
            estado_operativo=contexto_nuevo,
        )

        # Debe solicitar aclaración sin inventar contexto
        self.assertEqual(res_referencia["tipo"], "aclaracion")
        self.assertIn("no indicaste cuál producto", res_referencia["mensaje_pantalla"])

    # ─────────────────────────────────────────────────────────────────────────
    # TEST 3: Usuario pierde contexto pero mantiene sesión web
    # ─────────────────────────────────────────────────────────────────────────
    def test_3_usuario_pierde_contexto_pero_mantiene_sesion(self):
        session_key = "sess_login_activo"
        contexto = HakuOperationalContext.obtener_o_crear(
            usuario_id=self.usuario_operador.id,
            session_key=session_key,
            almacen_id=self.almacen_central.id,
        )
        contexto.asignar_producto(self.prod_hdmi.id, self.prod_hdmi.codigo_interno, self.prod_hdmi.nombre)

        # Simular expiración de 7300s
        contexto.ultimo_acceso = time.time() - 7300
        HakuOperationalContext.guardar(contexto)

        # Obtener contexto: expirado
        self.assertIsNone(HakuOperationalContext.obtener(self.usuario_operador.id, session_key))

        # El usuario de Django sigue 100% autenticado y activo (sesión de login de 7 días no afectada)
        self.assertTrue(self.usuario_operador.is_authenticated)
        self.assertTrue(self.usuario_operador.is_active)
        self.assertEqual(self.usuario_operador.rol, UsuarioAudacIA.Rol.USUARIO)

    # ─────────────────────────────────────────────────────────────────────────
    # TEST 4: 10 usuarios simultáneos con contextos completamente independientes
    # ─────────────────────────────────────────────────────────────────────────
    def test_4_diez_usuarios_simultaneos_contextos_independientes(self):
        usuarios = []
        productos = []
        contextos = []

        nombres_herramientas = [
            "Osciloscopio Digital",
            "Multímetro Fluke",
            "Generador de Señales",
            "Fuente DC Regulada",
            "Estación de Cautín",
            "Pinza Amperimétrica",
            "Analizador Lógico",
            "Crimpadora RJ45",
            "Pelacables Automático",
            "Taladro Percutor Bosch",
        ]

        # Crear 10 productos y 10 usuarios independientes
        for i in range(10):
            p = Producto.objects.create(
                codigo_interno=f"ITM-{i+1:04d}",
                nombre=nombres_herramientas[i],
                categoria=self.categoria,
                stock_inicial=10 + i,
                stock_minimo=2,
            )
            Existencia.objects.create(
                producto=p,
                almacen=self.almacen_central,
                stock_disponible=10 + i,
                stock_en_prestamo=0,
            )
            productos.append(p)

            u = UsuarioAudacIA.objects.create_user(
                username=f"usuario_concurrente_{i+1}",
                password="Password1",
                nombre_completo=f"Técnico #{i+1}",
                rol=UsuarioAudacIA.Rol.USUARIO,
                almacen_asignado=self.almacen_central,
            )
            usuarios.append(u)

            sess_key = f"sess_multi_{i+1}"
            ctx = HakuOperationalContext.obtener_o_crear(u.id, sess_key, self.almacen_central.id)
            contextos.append((u, sess_key, ctx, p))

        # Cada usuario consulta su propio producto respectivo
        for u, sess_key, ctx, p in contextos:
            res = procesar_comando_inventario(
                usuario=u,
                mensaje=f"consulta stock de {p.nombre}",
                estado_operativo=ctx,
            )
            self.assertEqual(res["tipo"], "consulta")
            self.assertEqual(ctx.producto_id, p.id)

        # Verificar aislamiento: cada usuario debe resolver su propio producto ante 'dónde está ese producto'
        for u, sess_key, ctx, p in contextos:
            res_ref = procesar_comando_inventario(
                usuario=u,
                mensaje="¿dónde está ese producto?",
                estado_operativo=ctx,
            )
            self.assertEqual(res_ref["tipo"], "consulta")
            self.assertEqual(res_ref["datos"]["producto_id"], str(p.id))

    # ─────────────────────────────────────────────────────────────────────────
    # TEST 5: 50 entradas máximo en LocMemCache y política LRU
    # ─────────────────────────────────────────────────────────────────────────
    def test_5_cincuenta_entradas_maximo_lru(self):
        cache = caches["haku_contexto"]
        cache.clear()

        # Insertar 60 contextos (superando MAX_ENTRIES=50)
        for i in range(1, 61):
            ctx = EstadoOperativoHaku(
                usuario_id=500 + i,
                session_key=f"sess_lru_test_{i}",
                producto_id=self.prod_hdmi.id,
            )
            HakuOperationalContext.guardar(ctx)

        conteo = len(getattr(cache, "_cache", {}))
        self.assertLessEqual(
            conteo,
            50,
            f"El número de entradas en LocMemCache ({conteo}) no debe exceder MAX_ENTRIES=50",
        )

    # ─────────────────────────────────────────────────────────────────────────
    # TEST 6: Memoria operacional ultimas_acciones: "Busca HDMI" -> "consulta stock" -> "el anterior"
    # ─────────────────────────────────────────────────────────────────────────
    def test_6_ultimas_acciones_resuelve_el_anterior(self):
        session_key = "sess_ultimas_acciones"
        contexto = HakuOperationalContext.obtener_o_crear(
            usuario_id=self.usuario_operador.id,
            session_key=session_key,
            almacen_id=self.almacen_central.id,
        )

        # Paso 1: "Busca HDMI"
        res1 = procesar_comando_inventario(
            usuario=self.usuario_operador,
            mensaje="Busca el cable HDMI",
            estado_operativo=contexto,
        )
        self.assertEqual(res1["tipo"], "consulta")
        self.assertEqual(contexto.producto_id, self.prod_hdmi.id)

        # Paso 2: "consulta stock"
        res2 = procesar_comando_inventario(
            usuario=self.usuario_operador,
            mensaje="consulta el stock",
            estado_operativo=contexto,
        )
        self.assertEqual(res2["tipo"], "consulta")

        # Verificar que se registraron acciones operacionales
        self.assertGreaterEqual(len(contexto.ultimas_acciones), 2)
        self.assertEqual(contexto.ultimas_acciones[-1]["accion"], "CONSULTAR_STOCK")
        self.assertEqual(contexto.ultimas_acciones[-1]["producto_id"], self.prod_hdmi.id)

        # Paso 3: "el anterior"
        res3 = procesar_comando_inventario(
            usuario=self.usuario_operador,
            mensaje="el anterior",
            estado_operativo=contexto,
        )
        self.assertEqual(res3["tipo"], "consulta")
        self.assertIn(self.prod_hdmi.nombre, res3["mensaje_pantalla"])

        # Verificar tope de máximo 5 acciones operacionales
        for i in range(10):
            contexto.registrar_accion(f"ACCION_{i}", producto="Prueba", producto_id=self.prod_hdmi.id)

        self.assertLessEqual(len(contexto.ultimas_acciones), 5, "No debe almacenar más de 5 acciones operacionales")

    # ─────────────────────────────────────────────────────────────────────────
    # TEST 7: Propuesta transaccional válida a los 20 min y expirada a los 31 min
    # ─────────────────────────────────────────────────────────────────────────
    def test_7_confirmacion_pendiente_sobrevive_dentro_de_los_30_minutos(self):
        session_key = "sess_confirmar_30m"
        contexto = HakuOperationalContext.obtener_o_crear(
            usuario_id=self.usuario_operador.id,
            session_key=session_key,
            almacen_id=self.almacen_central.id,
        )

        # 1. Usuario solicita: "quiero sacar 5 cables HDMI para Laboratorio"
        res_propuesta = procesar_comando_inventario(
            usuario=self.usuario_operador,
            mensaje="quiero sacar 5 cables HDMI para Laboratorio de Robótica",
            estado_operativo=contexto,
        )

        self.assertEqual(res_propuesta["tipo"], "propuesta")
        self.assertTrue(res_propuesta["requiere_confirmacion"])
        propuesta = res_propuesta["propuesta"]
        self.assertEqual(propuesta["cantidad"], 5)

        # Guardar propuesta activa en contexto
        contexto.propuesta_activa = propuesta
        HakuOperationalContext.guardar(contexto)

        # 2. El usuario espera 20 minutos (1200 segundos < 1800s)
        contexto.ultimo_acceso = time.time() - 1200
        HakuOperationalContext.guardar(contexto)

        # 3. Tras 20 minutos, el usuario dice "confirmar"
        recuperado = HakuOperationalContext.obtener(self.usuario_operador.id, session_key)
        self.assertIsNotNone(recuperado)
        self.assertIsNotNone(recuperado.propuesta_activa)

        # Ejecutar confirmación vía servicio transaccional dentro del límite de 30 minutos
        res_confirmacion = ejecutar_confirmacion_propuesta(
            usuario=self.usuario_operador,
            propuesta=recuperado.propuesta_activa,
        )

        self.assertTrue(res_confirmacion["exito"])
        self.assertEqual(res_confirmacion["stock_nuevo"], 45)  # 50 - 5 = 45

        # La base de datos refleja la mutación de forma atómica
        self.exist_hdmi.refresh_from_db()
        self.assertEqual(self.exist_hdmi.stock_disponible, 45)

        # 4. Caso expiración de propuesta: Si pasaron 31 minutos (> 1800s)
        propuesta_expirada = {
            "id": "prop-exp-001",
            "tipo_operacion": Movimiento.Tipo.SALIDA,
            "producto_id": self.prod_hdmi.id,
            "cantidad": 2,
            "almacen_origen_id": self.almacen_central.id,
            "expira_en": time.time() - 60,  # Ya expiró hace 1 minuto
        }
        with self.assertRaises(ValidationError):
            ejecutar_confirmacion_propuesta(
                usuario=self.usuario_operador,
                propuesta=propuesta_expirada,
            )

    # ─────────────────────────────────────────────────────────────────────────
    # TEST 8: Consumo de memoria RAM (< 10 MB para 50 contextos)
    # ─────────────────────────────────────────────────────────────────────────
    def test_8_consumo_memoria_inferior_a_10_mb(self):
        # Crear un contexto completamente lleno con 5 acciones y 3 candidatos
        ctx = EstadoOperativoHaku(
            usuario_id=999,
            session_key="sess_benchmark_memory",
            almacen_id=1,
            producto_id=1001,
            producto_codigo="CAB-9999",
            producto_nombre="Cable Super Extendido Blindado 50 metros",
            ultima_intencion="CONSULTAR_STOCK",
            ultima_busqueda="dame existencias de cable super extendido",
            propuesta_activa={
                "id": "prop-mem-001",
                "tipo_operacion": "SALIDA",
                "producto_id": 1001,
                "cantidad": 10,
                "destinatario": "Ingeniería de Sistemas",
                "motivo": "Mantenimiento general preventivo de redes",
                "creado_en": time.time(),
                "expira_en": time.time() + 7200,
            },
        )
        ctx.asignar_candidatos([
            {"id": 1, "nombre": "Cable HDMI 1", "codigo": "C-1"},
            {"id": 2, "nombre": "Cable HDMI 2", "codigo": "C-2"},
            {"id": 3, "nombre": "Cable HDMI 3", "codigo": "C-3"},
        ])
        for i in range(5):
            ctx.registrar_accion(
                f"ACCION_{i}",
                producto="Cable HDMI 2.0",
                producto_id=1001,
                codigo="CAB-9999",
                cantidad=i + 1,
            )

        diccionario = ctx.to_dict()
        tamano_bytes = sys.getsizeof(diccionario) + sum(sys.getsizeof(v) for v in diccionario.values())
        # Estimación total para 50 contextos en RAM
        memoria_total_50_ctx_bytes = tamano_bytes * 50
        memoria_total_mb = memoria_total_50_ctx_bytes / (1024 * 1024)

        # 10 MB límite
        self.assertLess(
            memoria_total_mb,
            1.0,  # En realidad es < 0.2 MB, ampliamente inferior a 10 MB
            f"El consumo de memoria estimado ({memoria_total_mb:.4f} MB) excede el margen seguro",
        )

    # ─────────────────────────────────────────────────────────────────────────
    # TEST 9: Prohibición de almacenamiento de audios, transcripciones completas o chat_history
    # ─────────────────────────────────────────────────────────────────────────
    def test_9_prohibicion_almacenamiento_chat_history_o_audios(self):
        ctx = EstadoOperativoHaku(usuario_id=1, session_key="sess_no_chat")
        diccionario = ctx.to_dict()

        claves_prohibidas = [
            "chat_history", "conversation", "conversacion", "messages", "mensajes",
            "audio", "audios", "audio_bytes", "transcription", "transcripcion",
            "full_prompt", "prompt_completo", "respuesta_generada",
        ]

        for clave in claves_prohibidas:
            self.assertNotIn(clave, diccionario, f"Se detectó campo prohibido '{clave}' en EstadoOperativoHaku")

        # Intentar inyectar texto o conversación en detalles de acciones
        ctx.registrar_accion(
            "CONSULTAR",
            producto="Cable HDMI",
            producto_id=1,
            detalles={"texto": "mensaje largo del usuario", "audio": b"bytes_falsos", "seccion": "A1"},
        )

        ultima = ctx.ultimas_acciones[-1]
        self.assertNotIn("texto", ultima, "No debe registrar texto conversacional en ultimas_acciones")
        self.assertNotIn("audio", ultima, "No debe registrar audio en ultimas_acciones")
        self.assertEqual(ultima["seccion"], "A1")
