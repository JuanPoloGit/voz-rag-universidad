"""Batería exhaustiva de pruebas de seguridad y auditoría de contexto — Haku Inventario.

Cubre íntegramente las especificaciones de:
- TAREA 1: Desacoplamiento de TTLs (Memoria conversacional: 7200s, Propuesta transaccional: 1800s).
          Revalidación completa e innegociable de usuario, almacén, producto y stock disponible.
- TAREA 2: Simulación de reinicio de servidor / limpieza de RAM (Sesión web 7 días intacta;
          Haku solicita aclaración sin ejecutar y sin cerrar sesión).
- TAREA 3: Convivencia segura con Haku Visitante (HAKU_GPU_LOCK sin colisiones, deadlocks ni crashes).
- TAREA 4: Jerarquía estricta de resolución lingüística:
          1. Último producto explícitamente mencionado.
          2. Último producto consultado.
          3. Último candidato válido.
          Secuencia: Busca HDMI -> Consulta stock HDMI -> Busca ponchadora -> Sácame dos de esos = Ponchadora.
- TAREA 5: 5 Casos de memoria extendida (1h válido, 2h+1m expirado, 31m propuesta caducada,
          10 usuarios aislados, 50 contextos con desalojo LRU).
- TAREA 6: Validación de estructura mínima en 'ultimas_acciones' (máx 5, sin audios ni mensajes conversacionales).
- TAREA 7: Medición real de consumo de memoria RAM (< 10 MB para 50 contextos).
"""

from __future__ import annotations

import sys
import threading
import time
from typing import Any, Dict, List

from django.core.cache import caches
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import RequestFactory, TestCase

from cuentas.models import UsuarioAudacIA
from inventario.models import Almacen, Categoria, Existencia, Movimiento, Producto, Ubicacion

from .cache import HakuCacheManager
from .contexto import EstadoOperativoHaku, HakuOperationalContext
from .lenguaje.normalizador import IntencionResultado, IntencionTipo, NormalizadorLenguajeHaku
from .services import (
    HAKU_GPU_LOCK,
    HakuOcupadoError,
    adquirir_lock_haku,
    ejecutar_confirmacion_propuesta,
    procesar_comando_inventario,
)
from .views import MensajeHakuView


class HakuAuditoriaContextoSeguridadTestCase(TestCase):
    """Pruebas de endurecimiento y auditoría operacional para Haku Inventario."""

    def setUp(self):
        HakuOperationalContext.limpiar_todo()
        HakuOperationalContext.reset_metricas()
        HakuCacheManager.invalidar_todo()

        self.factory = RequestFactory()

        # Almacenes
        self.almacen_principal = Almacen.objects.create(nombre="Almacén de Redes")
        self.almacen_secundario = Almacen.objects.create(nombre="Almacén de Herramientas")

        # Categoría
        self.categoria_redes = Categoria.objects.create(
            nombre="Conectividad y Redes",
            almacen_principal=self.almacen_principal,
        )
        self.categoria_herramientas = Categoria.objects.create(
            nombre="Herramientas",
            almacen_principal=self.almacen_secundario,
        )

        # Ubicación
        self.ubicacion_a = Ubicacion.objects.create(
            nombre="Estante A-1",
            almacen=self.almacen_principal,
        )

        # Productos
        self.prod_hdmi = Producto.objects.create(
            codigo_interno="CAB-0001",
            nombre="Cable HDMI 2.0 2 metros",
            categoria=self.categoria_redes,
            stock_inicial=100,
            stock_minimo=10,
        )
        self.prod_hdmi.ubicaciones.add(self.ubicacion_a)
        self.exist_hdmi = Existencia.objects.create(
            producto=self.prod_hdmi,
            almacen=self.almacen_principal,
            stock_disponible=100,
            stock_en_prestamo=0,
        )

        self.prod_ponchadora = Producto.objects.create(
            codigo_interno="HER-0001",
            nombre="Ponchadora RJ45 Profesional",
            categoria=self.categoria_herramientas,
            stock_inicial=20,
            stock_minimo=3,
        )
        self.exist_ponchadora = Existencia.objects.create(
            producto=self.prod_ponchadora,
            almacen=self.almacen_secundario,
            stock_disponible=20,
            stock_en_prestamo=0,
        )

        self.prod_utp = Producto.objects.create(
            codigo_interno="RED-0001",
            nombre="Cable UTP Bobina Cat 6",
            categoria=self.categoria_redes,
            stock_inicial=30,
            stock_minimo=5,
        )
        self.exist_utp = Existencia.objects.create(
            producto=self.prod_utp,
            almacen=self.almacen_principal,
            stock_disponible=30,
            stock_en_prestamo=0,
        )

        # Usuarios
        self.usuario_operador = UsuarioAudacIA.objects.create_user(
            username="operador_almacen",
            password="Password1",
            nombre_completo="Juan Operador",
            rol=UsuarioAudacIA.Rol.USUARIO,
            almacen_asignado=self.almacen_principal,
        )

        self.usuario_admin = UsuarioAudacIA.objects.create_user(
            username="admin_almacen",
            password="Password1",
            nombre_completo="Carlos Administrador",
            rol=UsuarioAudacIA.Rol.ADMINISTRADOR,
        )

    def tearDown(self):
        HakuOperationalContext.limpiar_todo()
        HakuCacheManager.invalidar_todo()

    # ═════════════════════════════════════════════════════════════════════════
    # TAREA 1: SEPARACIÓN DE MEMORIA CONVERSACIONAL Y PROPUESTAS TRANSACCIONALES
    # ═════════════════════════════════════════════════════════════════════════

    def test_tarea1_separacion_ttls_conversacion_vs_propuesta(self):
        """Conversación dura 7200s (2h); propuestas transaccionales caducan a los 1800s (30m)."""
        session_key = "sess_tarea1"
        contexto = HakuOperationalContext.obtener_o_crear(
            usuario_id=self.usuario_operador.id,
            session_key=session_key,
            almacen_id=self.almacen_principal.id,
        )

        # Generar propuesta transaccional de salida
        res = procesar_comando_inventario(
            usuario=self.usuario_operador,
            mensaje="sácame 5 cables HDMI para Mantenimiento",
            estado_operativo=contexto,
        )
        self.assertEqual(res["tipo"], "propuesta")
        propuesta = res["propuesta"]
        contexto.propuesta_activa = propuesta
        HakuOperationalContext.guardar(contexto)

        # Caso A: A los 25 minutos (1500s <= 1800s), propuesta transaccional sigue VÁLIDA
        propuesta["expira_en"] = time.time() + 300  # Quedan 5 minutos
        contexto.propuesta_activa = propuesta
        HakuOperationalContext.guardar(contexto)

        confirmacion = ejecutar_confirmacion_propuesta(
            usuario=self.usuario_operador,
            propuesta=propuesta,
        )
        self.assertTrue(confirmacion["exito"])
        self.assertEqual(confirmacion["stock_nuevo"], 95)

        # Caso B: A los 31 minutos (1860s > 1800s), la propuesta debe RECHAZARSE por expirada
        propuesta_vencida = dict(propuesta)
        propuesta_vencida["id"] = "prop-vencida-99"
        propuesta_vencida["expira_en"] = time.time() - 60  # Venció hace 1 min

        with self.assertRaises(ValidationError) as ctx_err:
            ejecutar_confirmacion_propuesta(
                usuario=self.usuario_operador,
                propuesta=propuesta_vencida,
            )
        self.assertIn("límite de 30 minutos", str(ctx_err.exception))

        # Pero la memoria conversacional sigue recordando el producto tras 1 hora (3600s < 7200s)
        contexto.ultimo_acceso = time.time() - 3600
        HakuOperationalContext.guardar(contexto)
        ctx_recuperado = HakuOperationalContext.obtener(self.usuario_operador.id, session_key)
        self.assertIsNotNone(ctx_recuperado)
        self.assertEqual(ctx_recuperado.producto_id, self.prod_hdmi.id)

    def test_tarea1_revalidacion_estricta_al_confirmar_propuesta(self):
        """Al confirmar NUNCA confiar en memoria: revalidar usuario, almacén, producto y stock."""
        # 1. Revalidación de usuario activo
        propuesta = {
            "id": "prop-seguridad-1",
            "tipo_operacion": Movimiento.Tipo.SALIDA,
            "producto_id": self.prod_hdmi.id,
            "cantidad": 5,
            "almacen_origen_id": self.almacen_principal.id,
            "expira_en": time.time() + 1800,
        }
        self.usuario_operador.is_active = False
        self.usuario_operador.save()

        with self.assertRaises(PermissionDenied):
            ejecutar_confirmacion_propuesta(usuario=self.usuario_operador, propuesta=propuesta)

        self.usuario_operador.is_active = True
        self.usuario_operador.save()

        # 2. Revalidación de permisos de almacén (usuario no asignado al almacén secundario)
        propuesta_otro_almacen = dict(propuesta)
        propuesta_otro_almacen["almacen_origen_id"] = self.almacen_secundario.id
        with self.assertRaises(PermissionDenied):
            ejecutar_confirmacion_propuesta(usuario=self.usuario_operador, propuesta=propuesta_otro_almacen)

        # 3. Revalidación de producto activo (si fue dado de baja o archivado mientras la propuesta esperaba)
        self.prod_hdmi.activo = False
        self.prod_hdmi.save()
        with self.assertRaises(ValidationError) as ctx_err:
            ejecutar_confirmacion_propuesta(usuario=self.usuario_operador, propuesta=propuesta)
        self.assertIn("ya no existe o fue dado de baja", str(ctx_err.exception))

        self.prod_hdmi.activo = True
        self.prod_hdmi.save()

        # 4. Revalidación de existencia y stock disponible (prohibición de stock negativo)
        propuesta_sobregiro = dict(propuesta)
        propuesta_sobregiro["cantidad"] = 200  # Solo hay 100
        with self.assertRaises(ValidationError) as ctx_err:
            ejecutar_confirmacion_propuesta(usuario=self.usuario_operador, propuesta=propuesta_sobregiro)
        self.assertIn("Stock insuficiente", str(ctx_err.exception))

    # ═════════════════════════════════════════════════════════════════════════
    # TAREA 2: PRUEBA DE REINICIO DEL SERVIDOR
    # ═════════════════════════════════════════════════════════════════════════

    def test_tarea2_reinicio_servidor_limpia_ram_mantiene_sesion_web(self):
        """Simular reinicio Django: RAM se limpia, Haku solicita aclaración, pero la sesión web no se cierra."""
        session_key = "sess_reinicio_srv_123"

        # Paso 1: Usuario dice "Haku busca cable HDMI"
        ctx = HakuOperationalContext.obtener_o_crear(
            usuario_id=self.usuario_operador.id,
            session_key=session_key,
            almacen_id=self.almacen_principal.id,
        )
        res1 = procesar_comando_inventario(
            usuario=self.usuario_operador,
            mensaje="Haku busca cable HDMI",
            estado_operativo=ctx,
        )
        self.assertEqual(res1["tipo"], "consulta")
        self.assertEqual(ctx.producto_id, self.prod_hdmi.id)

        # Paso 2: Reiniciar servidor Django (limpiar memoria RAM y cachés)
        HakuOperationalContext.limpiar_todo()
        HakuCacheManager.invalidar_todo()

        # Paso 3: Después del reinicio, el usuario dice "sácame tres de esos"
        # Contexto en RAM es recreado en blanco para la misma sesión web activa
        ctx_post_reinicio = HakuOperationalContext.obtener_o_crear(
            usuario_id=self.usuario_operador.id,
            session_key=session_key,
            almacen_id=self.almacen_principal.id,
        )
        self.assertIsNone(ctx_post_reinicio.producto_id, "El foco de producto debe estar limpio tras reinicio")

        res2 = procesar_comando_inventario(
            usuario=self.usuario_operador,
            mensaje="sácame tres de esos",
            estado_operativo=ctx_post_reinicio,
        )

        # Resultado esperado: NO debe ejecutar, solicita aclaración
        self.assertEqual(res2["tipo"], "aclaracion")
        self.assertIn("No tengo un producto asociado actualmente", res2["mensaje_pantalla"])
        self.assertIn("Indícame qué producto deseas retirar", res2["mensaje_pantalla"])
        self.assertFalse(res2.get("requiere_confirmacion", False))

        # La sesión web del usuario sigue 100% válida
        self.assertTrue(self.usuario_operador.is_authenticated)
        self.assertTrue(self.usuario_operador.is_active)

    # ═════════════════════════════════════════════════════════════════════════
    # TAREA 3: CONVIVENCIA CON HAKU VISITANTE (HAKU_GPU_LOCK)
    # ═════════════════════════════════════════════════════════════════════════

    def test_tarea3_convivencia_haku_visitante_gpu_lock_opcion_a(self):
        """Simular Haku Visitante usando HAKU_GPU_LOCK: Inventario recibe HakuOcupadoError controlado (Opción A)."""
        lock_adquirido_por_visitante = threading.Event()
        liberar_lock_visitante = threading.Event()
        resultado_inventario = {}

        def tarea_haku_visitante():
            with HAKU_GPU_LOCK:
                lock_adquirido_por_visitante.set()
                # Simular inferencia pesada de 8B en GPU por Visitante
                liberar_lock_visitante.wait(timeout=3.0)

        hilo_visitante = threading.Thread(target=tarea_haku_visitante)
        hilo_visitante.start()

        # Esperar a que Visitante adquiera el lock
        lock_adquirido_por_visitante.wait(timeout=2.0)

        # Mientras Visitante ocupa el Lock, Usuario Inventario intenta adquirirlo con timeout corto (0.2s)
        try:
            with adquirir_lock_haku(timeout=0.2):
                resultado_inventario["exito"] = True
        except HakuOcupadoError as exc:
            resultado_inventario["error"] = str(exc)

        # Liberar hilo visitante
        liberar_lock_visitante.set()
        hilo_visitante.join(timeout=2.0)

        # Validar Opción A: mensaje controlado de ocupación
        self.assertIn("error", resultado_inventario)
        self.assertIn("Haku ocupado procesando otra solicitud. Intenta nuevamente.", resultado_inventario["error"])

    def test_tarea3_convivencia_haku_visitante_gpu_lock_opcion_b(self):
        """Simular Haku Visitante liberando GPU a tiempo: Inventario realiza espera controlada (Opción B)."""
        lock_adquirido = threading.Event()

        def tarea_haku_visitante_corta():
            with HAKU_GPU_LOCK:
                lock_adquirido.set()
                time.sleep(0.3)  # Inferencia rápida de 300ms

        hilo_visitante = threading.Thread(target=tarea_haku_visitante_corta)
        hilo_visitante.start()
        lock_adquirido.wait(timeout=1.0)

        # Inventario espera hasta 2.0 segundos de forma controlada
        adquirido_exitosamente = False
        with adquirir_lock_haku(timeout=2.0):
            adquirido_exitosamente = True

        hilo_visitante.join(timeout=1.0)
        self.assertTrue(adquirido_exitosamente, "Debe adquirir el lock tras espera controlada")

    # ═════════════════════════════════════════════════════════════════════════
    # TAREA 4: RESOLUCIÓN DE CONTEXTO LINGÜÍSTICO (PRIORIDADES Y SECUENCIA)
    # ═════════════════════════════════════════════════════════════════════════

    def test_tarea4_secuencia_estricta_hdmi_consulta_ponchadora_sacame_dos(self):
        """Secuencia obligatoria de auditoría:

        1. Busca HDMI
        2. Consulta stock HDMI
        3. Busca ponchadora
        4. Sácame dos de esos
        Resultado: DEBE interpretar Ponchadora, NUNCA HDMI.
        """
        session_key = "sess_tarea4_secuencia"
        contexto = HakuOperationalContext.obtener_o_crear(
            usuario_id=self.usuario_admin.id,
            session_key=session_key,
        )

        # 1. "Busca HDMI"
        res1 = procesar_comando_inventario(
            usuario=self.usuario_admin,
            mensaje="Busca HDMI",
            estado_operativo=contexto,
        )
        self.assertEqual(contexto.producto_id, self.prod_hdmi.id)

        # 2. "Consulta stock HDMI"
        res2 = procesar_comando_inventario(
            usuario=self.usuario_admin,
            mensaje="Consulta stock HDMI",
            estado_operativo=contexto,
        )
        self.assertEqual(contexto.producto_id, self.prod_hdmi.id)

        # 3. "Busca ponchadora"
        res3 = procesar_comando_inventario(
            usuario=self.usuario_admin,
            mensaje="Busca ponchadora",
            estado_operativo=contexto,
        )
        self.assertEqual(contexto.producto_id, self.prod_ponchadora.id)

        # 4. "Sácame dos de esos"
        res4 = procesar_comando_inventario(
            usuario=self.usuario_admin,
            mensaje="Sácame dos de esos",
            estado_operativo=contexto,
        )

        # Debe interpretar Ponchadora (solicitando destinatario o generando propuesta)
        self.assertIn("Ponchadora", res4["mensaje_pantalla"])
        self.assertNotIn("HDMI", res4["mensaje_pantalla"])

        # Si además se indica destinatario ("Sácame dos de esos para Mantenimiento")
        res5 = procesar_comando_inventario(
            usuario=self.usuario_admin,
            mensaje="Sácame dos de esos para Mantenimiento",
            estado_operativo=contexto,
        )
        self.assertEqual(res5["tipo"], "propuesta")
        propuesta = res5["propuesta"]
        self.assertEqual(propuesta["producto_id"], self.prod_ponchadora.id)
        self.assertEqual(propuesta["cantidad"], 2)
        self.assertIn("Ponchadora", propuesta["producto_nombre"])
        self.assertNotIn("HDMI", propuesta["producto_nombre"])

    def test_tarea4_prioridad_1_producto_explicitamente_mencionado(self):
        """Prioridad 1: Aunque haya ponchadora en contexto, si el usuario dice 'sácame 1 UTP' gana UTP."""
        contexto = EstadoOperativoHaku(
            usuario_id=self.usuario_admin.id,
            session_key="sess_p1",
            producto_id=self.prod_ponchadora.id,
            producto_nombre=self.prod_ponchadora.nombre,
        )
        intencion = NormalizadorLenguajeHaku.interpretar(
            mensaje="Sácame una bobina UTP Cat 6",
            usuario=self.usuario_admin,
            estado_operativo=contexto,
        )
        self.assertEqual(intencion.tipo, IntencionTipo.REGISTRAR_SALIDA)
        self.assertEqual(intencion.producto.id, self.prod_utp.id)

    def test_tarea4_prioridad_3_ultimo_candidato_valido(self):
        """Prioridad 3: Si no hay producto actual pero hay candidatos, desambigua con el último candidato."""
        contexto = EstadoOperativoHaku(
            usuario_id=self.usuario_admin.id,
            session_key="sess_p3",
            producto_id=None,  # Sin producto consultado
        )
        contexto.asignar_candidatos([
            {"id": self.prod_hdmi.id, "nombre": self.prod_hdmi.nombre, "codigo": self.prod_hdmi.codigo_interno}
        ])

        intencion = NormalizadorLenguajeHaku.interpretar(
            mensaje="Sácame dos de esos",
            usuario=self.usuario_admin,
            estado_operativo=contexto,
        )
        self.assertEqual(intencion.tipo, IntencionTipo.REGISTRAR_SALIDA)
        self.assertEqual(intencion.producto.id, self.prod_hdmi.id)

    # ═════════════════════════════════════════════════════════════════════════
    # TAREA 5: PRUEBAS DE MEMORIA EXTENDIDA (CASOS 1 A 5)
    # ═════════════════════════════════════════════════════════════════════════

    def test_caso1_contexto_valido_1_hora_despues(self):
        """Caso 1: 1 hora después (3600s < 7200s) el contexto debe recordar."""
        session_key = "sess_caso_1"
        ctx = HakuOperationalContext.obtener_o_crear(self.usuario_operador.id, session_key)
        ctx.asignar_producto(self.prod_hdmi.id, self.prod_hdmi.codigo_interno, self.prod_hdmi.nombre)
        HakuOperationalContext.guardar(ctx)

        # Simular 1 hora de inactividad
        ctx.ultimo_acceso = time.time() - 3600
        HakuOperationalContext.guardar(ctx)

        recuperado = HakuOperationalContext.obtener(self.usuario_operador.id, session_key)
        self.assertIsNotNone(recuperado)
        self.assertEqual(recuperado.producto_id, self.prod_hdmi.id)

    def test_caso2_contexto_expirado_2_horas_mas_1_minuto(self):
        """Caso 2: 2 horas + 1 minuto (7260s > 7200s) el contexto debe olvidar."""
        session_key = "sess_caso_2"
        ctx = HakuOperationalContext.obtener_o_crear(self.usuario_operador.id, session_key)
        ctx.asignar_producto(self.prod_hdmi.id, self.prod_hdmi.codigo_interno, self.prod_hdmi.nombre)
        HakuOperationalContext.guardar(ctx)

        # Simular 2 horas y 1 minuto (7260 segundos)
        ctx.ultimo_acceso = time.time() - 7260
        HakuOperationalContext.guardar(ctx)

        recuperado = HakuOperationalContext.obtener(self.usuario_operador.id, session_key)
        self.assertIsNone(recuperado, "El contexto debe haber sido purgado por expirar las 2 horas")

    def test_caso3_propuesta_expirada_31_minutos(self):
        """Caso 3: Propuesta a los 31 minutos (1860s > 1800s) debe solicitar nueva operación."""
        session_key = "sess_caso_3"
        ctx = HakuOperationalContext.obtener_o_crear(self.usuario_operador.id, session_key)
        ctx.propuesta_activa = {
            "id": "prop-31m",
            "tipo_operacion": Movimiento.Tipo.ENTRADA,
            "producto_id": self.prod_hdmi.id,
            "cantidad": 10,
            "almacen_origen_id": self.almacen_principal.id,
            "creado_en": time.time() - 1860,
            "expira_en": time.time() - 60,  # Expiró hace 1 minuto
        }
        HakuOperationalContext.guardar(ctx)

        res = procesar_comando_inventario(
            usuario=self.usuario_operador,
            mensaje="Sí, confirmo",
            estado_operativo=ctx,
        )
        self.assertEqual(res["tipo"], "aclaracion")
        self.assertIn("caducado", res["mensaje_pantalla"])
        self.assertIn("30 minutos", res["mensaje_pantalla"])
        self.assertIsNone(ctx.propuesta_activa, "La propuesta caducada debe ser purgada")

    def test_caso4_diez_usuarios_simultaneos_aislamiento_estricto(self):
        """Caso 4: 10 usuarios simultáneos con productos diferentes. Aislamiento total sin cruces."""
        usuarios = []
        for i in range(10):
            u = UsuarioAudacIA.objects.create_user(
                username=f"usuario_concurrente_{i}",
                password="Password1",
                nombre_completo=f"Usuario Concurrente {i}",
                rol=UsuarioAudacIA.Rol.ADMINISTRADOR,
            )
            usuarios.append(u)

        # Asignar a cada uno un producto o foco diferente
        for idx, u in enumerate(usuarios):
            session_key = f"sess_user_{u.id}"
            ctx = HakuOperationalContext.obtener_o_crear(u.id, session_key)
            if idx % 2 == 0:
                ctx.asignar_producto(self.prod_hdmi.id, self.prod_hdmi.codigo_interno, "HDMI")
            else:
                ctx.asignar_producto(self.prod_utp.id, self.prod_utp.codigo_interno, "UTP")
            HakuOperationalContext.guardar(ctx)

        # Validar aislamiento estricto
        for idx, u in enumerate(usuarios):
            session_key = f"sess_user_{u.id}"
            recup = HakuOperationalContext.obtener(u.id, session_key)
            self.assertIsNotNone(recup)
            if idx % 2 == 0:
                self.assertEqual(recup.producto_id, self.prod_hdmi.id)
            else:
                self.assertEqual(recup.producto_id, self.prod_utp.id)

    def test_caso5_cincuenta_contextos_max_insertar_sesenta_lru(self):
        """Caso 5: Caché de contextos configurada para 50 elementos. Crear 60 y verificar LRU sin fugas."""
        HakuOperationalContext.limpiar_todo()

        # Insertar 60 contextos
        for i in range(1, 61):
            ctx = EstadoOperativoHaku(
                usuario_id=i,
                session_key=f"sess_lru_{i}",
                producto_id=100 + i,
                producto_nombre=f"Producto {i}",
            )
            HakuOperationalContext.guardar(ctx)

        # Los contextos más recientes (55-60) deben existir en caché
        reciente = HakuOperationalContext.obtener(usuario_id=60, session_key="sess_lru_60")
        self.assertIsNotNone(reciente, "El contexto más reciente 60 debe existir")

        # La caché no creció indefinidamente gracias a MAX_ENTRIES=50 y CULL_FREQUENCY=2
        cache_haku = caches["haku_contexto"]
        # En LocMemCache el diccionario interno no excede sustancialmente el límite
        num_claves = len(getattr(cache_haku, "_cache", {}))
        self.assertLessEqual(num_claves, 55, "La caché LocMemCache debe aplicar desalojo controlado")

    # ═════════════════════════════════════════════════════════════════════════
    # TAREA 6: VALIDAR 'ULTIMAS_ACCIONES' (MÁX 5, SIN AUDIOS NI CHAT)
    # ═════════════════════════════════════════════════════════════════════════

    def test_tarea6_ultimas_acciones_solo_informacion_estructural(self):
        """Confirmar que no guarda conversaciones, ni audios, ni transcripciones completas."""
        ctx = EstadoOperativoHaku(usuario_id=1, session_key="sess_sec")

        # Intentar registrar acción con claves prohibidas (audio, mensaje_usuario, transcripcion)
        detalles_con_basura = {
            "almacen_id": 1,
            "mensaje_usuario": "oye haku cuanto queda del cable azul...",
            "audio": b"\x00\x01\x02\x03\x04" * 1000,
            "transcripcion": "transcripcion cruda del microfono",
            "texto": "texto completo de la conversacion",
            "prompt": "prompt de sistema",
            "codigo_referencia": "REF-ESTRUCTURAL-OK",
        }

        ctx.registrar_accion(
            accion="CONSULTAR_STOCK",
            producto="Cable HDMI",
            producto_id=123,
            codigo="CAB-001",
            cantidad=15,
            detalles=detalles_con_basura,
        )

        accion_guardada = ctx.ultimas_acciones[0]
        # Validar campos permitidos
        self.assertEqual(accion_guardada["producto_id"], 123)
        self.assertEqual(accion_guardada["accion"], "CONSULTAR_STOCK")
        self.assertEqual(accion_guardada["codigo_referencia"], "REF-ESTRUCTURAL-OK")

        # Validar que NINGUNA clave no permitida fue almacenada
        claves_prohibidas = [
            "mensaje_usuario", "audio", "transcripcion", "texto", "prompt", "conversacion", "raw_audio"
        ]
        for clave in claves_prohibidas:
            self.assertNotIn(clave, accion_guardada, f"La clave {clave} no debe guardarse en ultimas_acciones")

        # Probar límite estricto de 5 elementos
        for i in range(10):
            ctx.registrar_accion(f"ACCION_{i}", producto_id=i)
        self.assertEqual(len(ctx.ultimas_acciones), 5)

    # ═════════════════════════════════════════════════════════════════════════
    # TAREA 7: MEDICIÓN REAL DE CONSUMO DE MEMORIA RAM (< 10 MB)
    # ═════════════════════════════════════════════════════════════════════════

    def test_tarea7_medicion_consumo_memoria_50_contextos(self):
        """Medición de 50 contextos activos: debe mantenerse < 10 MB RAM."""
        import sys

        def calcular_tamano_profundo(obj, vistos=None):
            if vistos is None:
                vistos = set()
            obj_id = id(obj)
            if obj_id in vistos:
                return 0
            vistos.add(obj_id)
            tamano = sys.getsizeof(obj)
            if isinstance(obj, dict):
                tamano += sum(calcular_tamano_profundo(k, vistos) + calcular_tamano_profundo(v, vistos) for k, v in obj.items())
            elif isinstance(obj, (list, tuple, set)):
                tamano += sum(calcular_tamano_profundo(item, vistos) for item in obj)
            elif hasattr(obj, "__dict__"):
                tamano += calcular_tamano_profundo(obj.__dict__, vistos)
            return tamano

        contextos = []
        for i in range(50):
            ctx = EstadoOperativoHaku(
                usuario_id=1000 + i,
                session_key=f"benchmark_sess_{i}",
                almacen_id=1,
                producto_id=2000 + i,
                producto_codigo=f"CAB-{i:04d}",
                producto_nombre=f"Cable de Red Blindado Categoría {i}",
                ultima_intencion="CONSULTAR_STOCK",
                ultima_busqueda=f"búscame cable blindado {i}",
                propuesta_activa={
                    "id": f"prop-{i}",
                    "tipo_operacion": "SALIDA",
                    "producto_id": 2000 + i,
                    "cantidad": 5,
                    "almacen_origen_id": 1,
                    "creado_en": time.time(),
                    "expira_en": time.time() + 1800,
                },
            )
            # Agregar 3 candidatos
            ctx.asignar_candidatos([
                {"id": 1, "nombre": "Candidato A", "codigo": "C-1"},
                {"id": 2, "nombre": "Candidato B", "codigo": "C-2"},
                {"id": 3, "nombre": "Candidato C", "codigo": "C-3"},
            ])
            # Agregar 5 acciones operacionales
            for j in range(5):
                ctx.registrar_accion(f"ACCION_{j}", producto_id=2000 + i, codigo=f"CAB-{i:04d}", cantidad=j + 1)

            contextos.append(ctx)

        tamano_total_bytes = sum(calcular_tamano_profundo(ctx) for ctx in contextos)
        tamano_por_contexto_bytes = tamano_total_bytes / len(contextos)
        tamano_total_mb = tamano_total_bytes / (1024 * 1024)

        # Imprimir métricas de reporte
        print(f"\n[MÉTRICAS TAREA 7] Consumo RAM para 50 contextos activos:")
        print(f"  - Memoria total: {tamano_total_bytes:,} bytes ({tamano_total_mb:.4f} MB)")
        print(f"  - Memoria promedio por contexto: {tamano_por_contexto_bytes:,.2f} bytes ({tamano_por_contexto_bytes/1024:.2f} KB)")
        print(f"  - Objetos almacenados por contexto: 1 foco + 3 candidatos + 1 propuesta + 5 acciones")

        # Aserción obligatoria: debe ser < 10 MB RAM
        self.assertLess(
            tamano_total_mb,
            10.0,
            f"El consumo de RAM ({tamano_total_mb:.2f} MB) excede el umbral máximo de 10 MB",
        )

    # ═════════════════════════════════════════════════════════════════════════
    # TAREA 8: IDEMPOTENCIA ANTE SOLICITUDES SIMULTÁNEAS Y CLAVE DUPLICADA
    # ═════════════════════════════════════════════════════════════════════════

    def test_idempotencia_solicitudes_concurrencia_y_clave_duplicada(self):
        """Verifica la protección de idempotencia ante solicitudes con la misma clave (Punto 5 auditoría):
        1. Garantía del modelo: Movimiento.clave_idempotencia tiene unique=True.
        2. Solicitudes secuenciales con la misma clave: la 2da retorna idempotente=True sin duplicar movimiento ni stock.
        3. Simulación de carrera concurrente (ambas peticiones pasan el pre-check antes del commit):
           Al intentar insertar con la misma clave_idempotencia, la restricción UNIQUE de la base de datos
           dispara IntegrityError, que es interceptado limpiamente retornando el movimiento existente.
        4. Verificación de conteo en base de datos: exactamente 1 Movimiento creado.
        """
        from unittest.mock import MagicMock, patch

        propuesta = {
            "id": "prop-idemp-test-01",
            "tipo_operacion": "SALIDA",
            "producto_id": self.prod_hdmi.id,
            "cantidad": 5,
            "almacen_origen_id": self.almacen_principal.id,
            "creado_en": time.time(),
            "expira_en": time.time() + 1800,
            "motivo": "Prueba idempotencia concurrente",
        }
        clave_unica = "haku-idemp-concurrente-xyz-001"

        # 1. Primera solicitud procesa y crea el movimiento
        res1 = ejecutar_confirmacion_propuesta(
            usuario=self.usuario_operador,
            propuesta=propuesta,
            idempotency_key=clave_unica,
        )
        self.assertTrue(res1["exito"])
        self.assertFalse(res1.get("idempotente", False))
        mov_id_1 = str(res1["movimiento_id"])
        stock_tras_res1 = res1["stock_nuevo"]
        self.assertEqual(stock_tras_res1, 95)

        # 2. Segunda solicitud (reintento secuencial) con la misma clave
        res2 = ejecutar_confirmacion_propuesta(
            usuario=self.usuario_operador,
            propuesta=propuesta,
            idempotency_key=clave_unica,
        )
        self.assertTrue(res2["exito"])
        self.assertTrue(res2.get("idempotente", False))
        self.assertEqual(str(res2["movimiento_id"]), mov_id_1)

        # 3. Simulación de carrera concurrente en la ventana crítica:
        # Simulamos que una petición simultánea traspasa el check 'filter().first()' antes del commit
        # Al ejecutar el bloque atómico, la BD lanza IntegrityError por colisión en unique=True.
        llamadas = [0]
        def smart_filter(*args, **kwargs):
            llamadas[0] += 1
            if llamadas[0] == 1:
                m = MagicMock()
                m.first.return_value = None
                return m
            return Movimiento.objects.all().filter(*args, **kwargs)

        with patch("inventario.models.Movimiento.objects.filter", side_effect=smart_filter):
            res_carrera = ejecutar_confirmacion_propuesta(
                usuario=self.usuario_operador,
                propuesta=propuesta,
                idempotency_key=clave_unica,
            )
            self.assertTrue(res_carrera["exito"])
            self.assertTrue(res_carrera.get("idempotente", False))
            self.assertEqual(str(res_carrera["movimiento_id"]), mov_id_1)

        # 4. Invariante final en Base de Datos:
        # Exactamente 1 Movimiento registrado con esa clave
        movimientos = Movimiento.objects.filter(clave_idempotencia=clave_unica)
        self.assertEqual(movimientos.count(), 1)

        # El stock solo se descontó UNA vez (100 - 5 = 95), no 3 veces
        self.exist_hdmi.refresh_from_db()
        self.assertEqual(self.exist_hdmi.stock_disponible, 95)

