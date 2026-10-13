"""Pruebas automatizadas del carril 4 — Haku Inventario, API Interna y Voz Web.

Cubre:
1. Normalización fonética para síntesis de voz (AudacIA -> Audacia).
2. Convivencia y cola segura (lock) con Haku Visitante.
3. Síntesis de audio exclusiva para el cliente (bytes WAV válidos).
4. Autenticación y permisos por rol/almacén en la API.
5. Flujo de pre-confirmación en dos pasos para mutaciones.
6. Prohibición estricta de stock negativo vía Haku.
7. Trazabilidad con via_haku=True en Movimiento y Auditoria.
8. Préstamos y devoluciones vía Haku.
9. Expiración de contexto conversacional tras 30 minutos de inactividad.
"""

import json
import threading
import time
import wave
import io
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client, RequestFactory, TestCase

from cuentas.models import UsuarioAudacIA
from inventario.models import (
    Almacen,
    Auditoria,
    Categoria,
    Movimiento,
    Prestamo,
    Producto,
    TipoMovimiento,
    Ubicacion,
)
from integraciones.haku.middleware import HakuSessionMiddleware
from integraciones.haku.services import (
    HAKU_GPU_LOCK,
    HakuOcupadoError,
    adquirir_lock_haku,
    ejecutar_confirmacion_propuesta,
    normalizar_para_tts,
    procesar_comando_inventario,
    sintetizar_audio_bytes,
)


class NormalizacionFoneticatestCase(TestCase):
    """Verifica las reglas innegociables de pronunciación para Piper TTS."""

    def test_reemplazo_audacia_para_evitar_audakia(self):
        """Regla 1: en pantalla 'Inventario AudacIA', para Piper TTS 'Inventario Audacia'."""
        texto_original = "Bienvenido a Inventario AudacIA. Sistema local de AudacIA."
        normalizado = normalizar_para_tts(texto_original)

        self.assertIn("Audacia", normalizado)
        self.assertNotIn("AudacIA", normalizado)
        self.assertEqual(normalizado, "Bienvenido a Inventario Audacia. Sistema local de Audacia.")

    def test_limpieza_markdown_y_codigos(self):
        """El texto enviado al TTS debe eliminar asteriscos y formatear códigos."""
        texto = "**Stock bajo:** El producto `CAB-0001` tiene pocas unidades."
        normalizado = normalizar_para_tts(texto)

        self.assertNotIn("**", normalizado)
        self.assertNotIn("`", normalizado)
        self.assertIn("código CAB guion 0001", normalizado)


class ColaSeguraLockTestCase(TestCase):
    """Verifica el lock y cola segura para convivir con Haku Visitante."""

    def test_adquirir_y_liberar_lock(self):
        with adquirir_lock_haku(timeout=2.0):
            # El lock debe estar ocupado
            self.assertTrue(HAKU_GPU_LOCK._is_owned())

        # Al salir del bloque, el lock queda libre
        self.assertFalse(HAKU_GPU_LOCK._is_owned())

    def test_timeout_lock_cuando_esta_ocupado(self):
        """Si otro hilo (ej. Haku Visitante) retiene el lock, debe lanzar HakuOcupadoError."""
        bloqueado = threading.Event()
        liberar = threading.Event()

        def hilo_visitante():
            with HAKU_GPU_LOCK:
                bloqueado.set()
                liberar.wait(timeout=2.0)

        t = threading.Thread(target=hilo_visitante)
        t.start()
        bloqueado.wait(timeout=1.0)

        # Haku Inventario intenta acceder pero expira el timeout
        with self.assertRaises(HakuOcupadoError):
            with adquirir_lock_haku(timeout=0.2):
                pass

        liberar.set()
        t.join()


class SintesisAudioClienteTestCase(TestCase):
    """Verifica que el audio se genere como WAV válido para el navegador cliente."""

    def test_genera_wav_valido(self):
        wav_bytes = sintetizar_audio_bytes("Inventario AudacIA")
        self.assertTrue(len(wav_bytes) > 44)  # Cabecera mínima WAV
        self.assertTrue(wav_bytes.startswith(b"RIFF"))

        # Validar que wave lo lea sin error
        buffer = io.BytesIO(wav_bytes)
        with wave.open(buffer, "rb") as w:
            self.assertEqual(w.getnchannels(), 1)
            self.assertEqual(w.getsampwidth(), 2)


class BaseHakuTestCase(TestCase):
    """Fixture base con datos maestros para pruebas de integración."""

    def setUp(self):
        self.almacen_principal = Almacen.objects.create(nombre="Almacén Central")
        self.almacen_secundario = Almacen.objects.create(nombre="Almacén Secundario")

        self.categoria = Categoria.objects.create(
            nombre="Cables",
            almacen_principal=self.almacen_principal,
        )

        self.ubicacion = Ubicacion.objects.create(
            nombre="Estante A-1",
            almacen=self.almacen_principal,
        )

        self.producto = Producto.objects.create(
            codigo_interno="CAB-0001",
            nombre="Cable HDMI 2m",
            almacen=self.almacen_principal,
            categoria=self.categoria,
            stock_inicial=50,
            stock_disponible=50,
            stock_minimo=10,
        )

        self.superusuario = UsuarioAudacIA.objects.create_superuser(
            username="superadmin",
            password="Password1",
            nombre_completo="Super Administrador",
            rol=UsuarioAudacIA.Rol.SUPERUSUARIO,
        )

        self.admin = UsuarioAudacIA.objects.create_user(
            username="admin1",
            password="Password1",
            nombre_completo="Administrador Uno",
            rol=UsuarioAudacIA.Rol.ADMINISTRADOR,
        )

        self.usuario_central = UsuarioAudacIA.objects.create_user(
            username="user_central",
            password="Password1",
            nombre_completo="Usuario Central",
            rol=UsuarioAudacIA.Rol.USUARIO,
            almacen_asignado=self.almacen_principal,
        )

        self.usuario_secundario = UsuarioAudacIA.objects.create_user(
            username="user_secundario",
            password="Password1",
            nombre_completo="Usuario Secundario",
            rol=UsuarioAudacIA.Rol.USUARIO,
            almacen_asignado=self.almacen_secundario,
        )

        self.client = Client()


class PreConfirmacionHakuTestCase(BaseHakuTestCase):
    """Verifica el flujo de Pre-Confirmación obligatoria en dos pasos."""

    def test_salida_genera_propuesta_sin_modificar_stock(self):
        """El comando de salida primero propone los datos y NO muta la base de datos."""
        res = procesar_comando_inventario(
            usuario=self.usuario_central,
            mensaje="registrar salida de 5 unidades de Cable HDMI para Carlos motivo mantenimiento",
        )

        self.assertEqual(res["tipo"], "propuesta")
        self.assertTrue(res["requiere_confirmacion"])
        self.assertEqual(res["propuesta"]["cantidad"], 5)
        self.assertEqual(res["propuesta"]["destinatario"], "Carlos")

        # Verificar que el stock NO ha cambiado aún
        self.producto.refresh_from_db()
        self.assertEqual(self.producto.stock_disponible, 50)
        self.assertEqual(Movimiento.objects.count(), 0)

    def test_confirmacion_atomica_salida_con_via_haku(self):
        """Al confirmar la propuesta, se descuenta el stock y se registra via_haku=True."""
        res_prop = procesar_comando_inventario(
            usuario=self.usuario_central,
            mensaje="salida de 5 Cable HDMI para Carlos por mantenimiento",
        )
        propuesta = res_prop["propuesta"]

        # Confirmar la operación
        resultado = ejecutar_confirmacion_propuesta(
            usuario=self.usuario_central,
            propuesta=propuesta,
        )

        self.assertTrue(resultado["exito"])
        self.assertEqual(resultado["stock_nuevo"], 45)

        # Verificar base de datos
        self.producto.refresh_from_db()
        self.assertEqual(self.producto.stock_disponible, 45)

        movimiento = Movimiento.objects.get(id=resultado["movimiento_id"])
        self.assertEqual(movimiento.tipo, TipoMovimiento.SALIDA)
        self.assertEqual(movimiento.cantidad, 5)
        self.assertEqual(movimiento.destinatario, "Carlos")
        self.assertTrue(movimiento.via_haku)

        auditoria = Auditoria.objects.filter(via_haku=True).first()
        self.assertIsNotNone(auditoria)
        self.assertEqual(auditoria.accion, TipoMovimiento.SALIDA)
        self.assertEqual(auditoria.usuario, self.usuario_central)

    def test_prohibicion_stock_negativo_via_haku(self):
        """Intentar retirar más stock del disponible debe ser rechazado."""
        res_prop = procesar_comando_inventario(
            usuario=self.usuario_central,
            mensaje="salida de 100 Cable HDMI para Carlos por mantenimiento",
        )

        # Debe responder con error informativo o fallar en confirmación
        if res_prop["tipo"] == "error":
            self.assertIn("insuficiente", res_prop["mensaje_pantalla"].lower())
        else:
            with self.assertRaises(ValidationError):
                ejecutar_confirmacion_propuesta(
                    usuario=self.usuario_central,
                    propuesta=res_prop["propuesta"],
                )


class PrestamosHakuTestCase(BaseHakuTestCase):
    """Verifica préstamos y devoluciones por voz vía Haku."""

    def test_flujo_completo_prestamo_y_devolucion(self):
        # 1. Crear propuesta de préstamo
        res_prestamo = procesar_comando_inventario(
            usuario=self.usuario_central,
            mensaje="prestar 3 Cable HDMI a María Gómez",
        )
        self.assertEqual(res_prestamo["tipo"], "propuesta")

        # 2. Confirmar préstamo
        ejecutar_confirmacion_propuesta(
            usuario=self.usuario_central,
            propuesta=res_prestamo["propuesta"],
        )

        self.producto.refresh_from_db()
        self.assertEqual(self.producto.stock_disponible, 47)
        self.assertEqual(self.producto.stock_en_prestamo, 3)

        prestamo = Prestamo.objects.get(destinatario="María Gómez")
        self.assertEqual(prestamo.cantidad_pendiente_devolucion, 3)
        self.assertEqual(prestamo.estado, Prestamo.Estado.ABIERTO)

        # 3. Devolver préstamo
        res_dev = procesar_comando_inventario(
            usuario=self.usuario_central,
            mensaje="devolver préstamo de María Gómez",
        )
        self.assertEqual(res_dev["tipo"], "propuesta")

        ejecutar_confirmacion_propuesta(
            usuario=self.usuario_central,
            propuesta=res_dev["propuesta"],
        )

        self.producto.refresh_from_db()
        self.assertEqual(self.producto.stock_disponible, 50)
        self.assertEqual(self.producto.stock_en_prestamo, 0)

        prestamo.refresh_from_db()
        self.assertEqual(prestamo.estado, Prestamo.Estado.CERRADO)
        self.assertEqual(prestamo.cantidad_pendiente_devolucion, 0)


class PermisosAlmacenTestCase(BaseHakuTestCase):
    """Verifica restricciones de almacén para rol Usuario."""

    def test_usuario_no_puede_operar_otro_almacen(self):
        """Usuario secundario no debe poder operar productos del almacén principal."""
        res = procesar_comando_inventario(
            usuario=self.usuario_secundario,
            mensaje="salida de 2 Cable HDMI para Pedro motivo prueba",
        )
        # El producto del almacén central no está visible para su almacén
        self.assertNotEqual(res.get("tipo"), "propuesta")


class MiddlewareTimeoutTestCase(BaseHakuTestCase):
    """Verifica el timeout de 30 minutos del contexto conversacional de Haku."""

    def test_expiracion_contexto_tras_30_minutos(self):
        factory = RequestFactory()
        request = factory.get("/api/haku/estado/")
        request.user = self.usuario_central

        session = self.client.session
        session["haku_conversacion"] = ["mensaje anterior"]
        session["haku_propuesta_activa"] = {"id": "123"}
        session["haku_ultimo_acceso"] = time.time() - 2000  # Más de 30 min atrás (1800s)
        session.save()
        request.session = session

        middleware = HakuSessionMiddleware(lambda r: None)
        middleware(request)

        # El contexto debe haberse limpiado
        self.assertEqual(request.session.get("haku_conversacion"), [])
        self.assertIsNone(request.session.get("haku_propuesta_activa"))
        self.assertTrue(request.session.get("haku_contexto_expirado"))

        # Pero el usuario NO fue deslogueado
        self.assertTrue(request.user.is_authenticated)


class ApiEndpointsTestCase(BaseHakuTestCase):
    """Verifica las respuestas JSON de los endpoints de la API."""

    def test_no_autenticado_retorna_401(self):
        resp = self.client.get("/api/haku/estado/")
        self.assertEqual(resp.status_code, 401)

    def test_mensaje_endpoint_con_usuario_autenticado(self):
        self.client.force_login(self.usuario_central)
        resp = self.client.post(
            "/api/haku/mensaje/",
            data=json.dumps({"mensaje": "¿Cuánto stock hay de Cable HDMI?"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["tipo"], "consulta")
        self.assertIn("50", data["mensaje_pantalla"])

    def test_sintetizar_endpoint_retorna_wav(self):
        self.client.force_login(self.usuario_central)
        resp = self.client.post(
            "/api/haku/voz/sintetizar/",
            data=json.dumps({"texto": "Prueba de voz en Inventario AudacIA"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "audio/wav")
        self.assertTrue(resp.content.startswith(b"RIFF"))
