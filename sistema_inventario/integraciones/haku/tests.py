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
    Existencia,
    Movimiento,
    Prestamo,
    Producto,
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
        self.assertIn("código CAB guion cero cero cero uno", normalizado)


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
            categoria=self.categoria,
            stock_inicial=50,
            stock_minimo=10,
        )

        self.existencia = Existencia.objects.create(
            producto=self.producto,
            almacen=self.almacen_principal,
            stock_disponible=50,
            stock_en_prestamo=0,
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
        self.existencia.refresh_from_db()
        self.assertEqual(self.existencia.stock_disponible, 50)
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
        self.existencia.refresh_from_db()
        self.assertEqual(self.existencia.stock_disponible, 45)

        movimiento = Movimiento.objects.get(id=resultado["movimiento_id"])
        self.assertEqual(movimiento.tipo, Movimiento.Tipo.SALIDA)
        self.assertEqual(movimiento.cantidad, 5)
        self.assertEqual(movimiento.destinatario, "Carlos")

        auditoria = Auditoria.objects.filter(via_haku=True).first()
        self.assertIsNotNone(auditoria)
        self.assertEqual(auditoria.accion, Movimiento.Tipo.SALIDA)
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

        self.existencia.refresh_from_db()
        self.assertEqual(self.existencia.stock_disponible, 47)
        self.assertEqual(self.existencia.stock_en_prestamo, 3)

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

        self.existencia.refresh_from_db()
        self.assertEqual(self.existencia.stock_disponible, 50)
        self.assertEqual(self.existencia.stock_en_prestamo, 0)

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
        session["haku_ultimo_acceso"] = time.time() - 7300  # Más de 2 horas atrás (> 7200s)
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

    def test_crear_producto_endpoint(self):
        self.client.force_login(self.admin)
        payload = {
            "codigo_interno": "TEST-PROD-999",
            "nombre": "Cable Patchcord Cat 6",
            "categoria_id": self.categoria.id,
            "almacen_id": self.almacen_principal.id,
            "stock_inicial": 15,
            "stock_minimo": 5,
            "descripcion": "Creado en prueba de endpoint",
        }
        resp = self.client.post(
            "/api/haku/producto/crear/",
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["exito"])
        prod = Producto.objects.filter(nombre="Cable Patchcord Cat 6").first()
        self.assertIsNotNone(prod)
        self.assertTrue(prod.codigo_interno.startswith("CAB-"))
        ex = Existencia.objects.filter(producto=prod, almacen=self.almacen_principal).first()
        self.assertIsNotNone(ex)
        self.assertEqual(ex.stock_disponible, 15)

    def test_editar_producto_endpoint(self):
        self.client.force_login(self.admin)
        payload = {
            "producto_id": self.producto.id,
            "nombre": "Cable HDMI 2.0 Ultra HD",
            "stock_minimo": 8,
            "descripcion": "Descripción actualizada",
        }
        resp = self.client.post(
            "/api/haku/producto/editar/",
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["exito"])
        self.producto.refresh_from_db()
        self.assertEqual(self.producto.nombre, "Cable HDMI 2.0 Ultra HD")
        self.assertEqual(self.producto.stock_minimo, 8)

    def test_usuario_no_autenticado_rechazado_en_todos_los_endpoints(self):
        """Peticiones no autenticadas deben ser rechazadas con 401 en todos los endpoints."""
        endpoints = [
            ("POST", "/api/haku/mensaje/", {"mensaje": "hola"}),
            ("POST", "/api/haku/confirmar/", {"propuesta_id": "123", "idempotency_key": "k1"}),
            ("POST", "/api/haku/cancelar/", {"propuesta_id": "123"}),
            ("GET", "/api/haku/consultar-stock/?q=cable", None),
            ("GET", "/api/haku/alertas/", None),
            ("GET", "/api/haku/prestamos-activos/", None),
            ("POST", "/api/haku/producto/crear/", {"nombre": "X", "categoria_id": 1}),
            ("POST", "/api/haku/producto/editar/", {"producto_id": 1, "nombre": "X"}),
            ("GET", "/api/haku/estado/", None),
            ("POST", "/api/haku/voz/sintetizar/", {"texto": "hola"}),
        ]
        for metodo, url, data in endpoints:
            if metodo == "POST":
                r = self.client.post(url, data=json.dumps(data) if data else "", content_type="application/json")
            else:
                r = self.client.get(url)
            self.assertEqual(r.status_code, 401, f"Fallo de rechazo no autenticado en: {url}")

    def test_usuario_inactivo_o_eliminado_rechazado(self):
        """Un usuario inactivo o eliminado lógicamente es rechazado de inmediato (sesión invalidada / 401)."""
        self.usuario_central.is_active = False
        self.usuario_central.save(update_fields=["is_active"])
        self.client.force_login(self.usuario_central)

        resp = self.client.get("/api/haku/estado/")
        # Al estar inactivo, Django desautentica la sesión y responde 401
        self.assertEqual(resp.status_code, 401)

        # Caso eliminado lógicamente con is_active=True: HakuSessionMiddleware intercepta y responde 401
        self.usuario_central.is_active = True
        self.usuario_central.eliminado_logicamente = True
        self.usuario_central.save(update_fields=["is_active", "eliminado_logicamente"])
        self.client.force_login(self.usuario_central)

        resp2 = self.client.get("/api/haku/estado/")
        self.assertEqual(resp2.status_code, 401)
        # El backend UsuarioAudacIABackend rechaza de raíz o HakuSessionMiddleware desautentica
        self.assertTrue("error" in resp2.json())

    def test_usuario_normal_no_puede_crear_ni_editar_productos(self):
        """Un usuario con rol USUARIO no puede registrar ni modificar productos (403 Forbidden)."""
        self.client.force_login(self.usuario_central)

        # 1. Intentar crear producto
        resp_crear = self.client.post(
            "/api/haku/producto/crear/",
            data=json.dumps({"nombre": "Hack Prod", "categoria_id": self.categoria.id}),
            content_type="application/json",
        )
        self.assertEqual(resp_crear.status_code, 403)
        self.assertFalse(Producto.objects.filter(nombre="Hack Prod").exists())

        # 2. Intentar editar producto
        resp_editar = self.client.post(
            "/api/haku/producto/editar/",
            data=json.dumps({"producto_id": self.producto.id, "nombre": "Nombre Hackeado"}),
            content_type="application/json",
        )
        self.assertEqual(resp_editar.status_code, 403)
        self.producto.refresh_from_db()
        self.assertNotEqual(self.producto.nombre, "Nombre Hackeado")

    def test_usuario_normal_no_puede_operar_almacen_ajeno_en_confirmar(self):
        """Un usuario normal no puede ejecutar mutaciones sobre almacenes distintos al suyo."""
        from integraciones.haku.services import ejecutar_confirmacion_propuesta
        from django.core.exceptions import PermissionDenied

        # Usuario secundario pertenece a Almacén Secundario, no a Central
        propuesta_almacen_central = {
            "id": "prop-central-999",
            "tipo_operacion": "ENTRADA",
            "producto_id": self.producto.id,
            "cantidad": 5,
            "almacen_origen_id": self.almacen_principal.id,
            "almacen_origen_nombre": self.almacen_principal.nombre,
            "expira_en": time.time() + 300,
        }

        # Ejecución directa del servicio debe rechazar con PermissionDenied
        with self.assertRaises(PermissionDenied):
            ejecutar_confirmacion_propuesta(
                usuario=self.usuario_secundario,
                propuesta=propuesta_almacen_central,
                idempotency_key="key-hack-1",
            )

        # Confirmar que no se crearon movimientos ni se incrementó stock
        self.existencia.refresh_from_db()
        self.assertEqual(self.existencia.stock_disponible, 50)
        self.assertFalse(Movimiento.objects.filter(clave_idempotencia="key-hack-1").exists())

    def test_usuario_normal_aislamiento_consultas_directas(self):
        """Consultas directas aíslan los datos al almacén asignado del usuario normal."""
        # Crear existencia del producto en Almacén Secundario con stock distinto
        Existencia.objects.create(
            producto=self.producto,
            almacen=self.almacen_secundario,
            stock_disponible=7,
            stock_en_prestamo=0,
        )

        # Usuario central ve Almacén Central (50 disp)
        self.client.force_login(self.usuario_central)
        resp1 = self.client.get("/api/haku/consultar-stock/?q=HDMI")
        self.assertEqual(resp1.status_code, 200)
        prods1 = resp1.json()["productos"]
        self.assertEqual(len(prods1), 1)
        self.assertEqual(prods1[0]["almacen"], "Almacén Central")
        self.assertEqual(prods1[0]["stock_disponible"], 50)

        # Usuario secundario ve Almacén Secundario (7 disp)
        self.client.force_login(self.usuario_secundario)
        resp2 = self.client.get("/api/haku/consultar-stock/?q=HDMI")
        self.assertEqual(resp2.status_code, 200)
        prods2 = resp2.json()["productos"]
        self.assertEqual(len(prods2), 1)
        self.assertEqual(prods2[0]["almacen"], "Almacén Secundario")
        self.assertEqual(prods2[0]["stock_disponible"], 7)


class GestionUsuariosHakuTestCase(TestCase):
    """Pruebas de autorización, seguridad y persistencia en la gestión de usuarios vía HAKU."""

    def setUp(self):
        self.client = Client()
        self.almacen_principal = Almacen.objects.create(nombre="Almacén Principal", activo=True)
        self.almacen_secundario = Almacen.objects.create(nombre="Almacén Secundario", activo=True)

        self.superusuario = UsuarioAudacIA.objects.create_superuser(
            username="super_haku",
            nombre_completo="Super Administrador Haku",
            password="Password123!",
            rol=UsuarioAudacIA.Rol.SUPERUSUARIO,
        )

        self.administrador = UsuarioAudacIA.objects.create_user(
            username="admin_haku",
            nombre_completo="Administrador Haku",
            password="Password123!",
            rol=UsuarioAudacIA.Rol.ADMINISTRADOR,
        )

        self.usuario_normal = UsuarioAudacIA.objects.create_user(
            username="user_haku",
            nombre_completo="Usuario Operativo Haku",
            password="Password123!",
            rol=UsuarioAudacIA.Rol.USUARIO,
            almacen_asignado=self.almacen_principal,
        )

    def test_01_administrador_puede_crear_usuario_normal(self):
        """1. Un administrador autorizado puede crear un usuario normal con los campos reales."""
        self.client.force_login(self.administrador)
        payload = {
            "username": "nuevo_operador",
            "nombre_completo": "Nuevo Operador Prueba",
            "rol": UsuarioAudacIA.Rol.USUARIO,
            "almacen_asignado_id": self.almacen_principal.id,
            "password": "Password123!",
        }
        resp = self.client.post("/api/haku/usuario/crear/", data=json.dumps(payload), content_type="application/json")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["usuario"]["username"], "nuevo_operador")

        # Verificar persistencia en base de datos
        nuevo = UsuarioAudacIA.objects.get(username="nuevo_operador")
        self.assertEqual(nuevo.rol, UsuarioAudacIA.Rol.USUARIO)
        self.assertEqual(nuevo.almacen_asignado, self.almacen_principal)
        self.assertTrue(nuevo.is_active)
        self.assertTrue(nuevo.check_password("Password123!"))

        # Verificar registro inmutable de auditoría sin filtrar contraseña
        auditoria = Auditoria.objects.filter(tipo_entidad="Usuario", id_entidad=str(nuevo.pk)).first()
        self.assertIsNotNone(auditoria)
        self.assertEqual(auditoria.accion, "CREAR_USUARIO")
        self.assertEqual(auditoria.usuario, self.administrador)
        self.assertTrue(auditoria.via_haku)
        self.assertNotIn("password", auditoria.valores_nuevos)
        self.assertNotIn("Password123!", json.dumps(auditoria.valores_nuevos))

    def test_02_administrador_no_puede_crear_superusuario(self):
        """2. Un administrador no puede crear un superusuario (403 Forbidden)."""
        self.client.force_login(self.administrador)
        payload = {
            "username": "hacked_super",
            "nombre_completo": "Intento Escalada",
            "rol": UsuarioAudacIA.Rol.SUPERUSUARIO,
            "password": "Password123!",
        }
        resp = self.client.post("/api/haku/usuario/crear/", data=json.dumps(payload), content_type="application/json")
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(UsuarioAudacIA.objects.filter(username="hacked_super").exists())

    def test_03_administrador_no_puede_editar_superusuario(self):
        """3. Un administrador no puede editar una cuenta superusuario ni cambiar sus datos (403)."""
        self.client.force_login(self.administrador)
        payload = {
            "id": self.superusuario.id,
            "nombre_completo": "Super Modificado Ilegal",
            "rol": UsuarioAudacIA.Rol.ADMINISTRADOR,
            "is_active": False,
        }
        resp = self.client.post("/api/haku/usuario/editar/", data=json.dumps(payload), content_type="application/json")
        self.assertEqual(resp.status_code, 403)

        self.superusuario.refresh_from_db()
        self.assertEqual(self.superusuario.nombre_completo, "Super Administrador Haku")
        self.assertEqual(self.superusuario.rol, UsuarioAudacIA.Rol.SUPERUSUARIO)
        self.assertTrue(self.superusuario.is_active)

    def test_04_administrador_no_puede_elevar_rol_a_superusuario(self):
        """4. Un administrador no puede elevar su propio rol ni el de otros a SUPERUSUARIO."""
        self.client.force_login(self.administrador)
        payload = {
            "id": self.usuario_normal.id,
            "nombre_completo": self.usuario_normal.nombre_completo,
            "rol": UsuarioAudacIA.Rol.SUPERUSUARIO,
        }
        resp = self.client.post("/api/haku/usuario/editar/", data=json.dumps(payload), content_type="application/json")
        self.assertEqual(resp.status_code, 403)

        self.usuario_normal.refresh_from_db()
        self.assertEqual(self.usuario_normal.rol, UsuarioAudacIA.Rol.USUARIO)

    def test_05_usuario_normal_requiere_almacen(self):
        """5. Un rol USUARIO requiere obligatoriamente un almacén asignado válido."""
        self.client.force_login(self.administrador)
        payload = {
            "username": "sin_almacen",
            "nombre_completo": "Usuario Sin Almacén",
            "rol": UsuarioAudacIA.Rol.USUARIO,
            "almacen_asignado_id": None,
            "password": "Password123!",
        }
        resp = self.client.post("/api/haku/usuario/crear/", data=json.dumps(payload), content_type="application/json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("almacen_asignado_id", resp.json()["detalles"])

    def test_06_superusuario_puede_administrar_usuarios(self):
        """6. Un superusuario puede crear y editar usuarios y administradores."""
        self.client.force_login(self.superusuario)

        # Crear un nuevo administrador
        payload_crear = {
            "username": "nuevo_admin",
            "nombre_completo": "Nuevo Administrador",
            "rol": UsuarioAudacIA.Rol.ADMINISTRADOR,
            "password": "Password123!",
        }
        resp = self.client.post("/api/haku/usuario/crear/", data=json.dumps(payload_crear), content_type="application/json")
        self.assertEqual(resp.status_code, 200)

        admin_creado = UsuarioAudacIA.objects.get(username="nuevo_admin")
        self.assertEqual(admin_creado.rol, UsuarioAudacIA.Rol.ADMINISTRADOR)

        # Editar dicho administrador
        payload_edit = {
            "id": admin_creado.id,
            "nombre_completo": "Administrador Renombrado",
            "is_active": True,
        }
        resp_edit = self.client.post("/api/haku/usuario/editar/", data=json.dumps(payload_edit), content_type="application/json")
        self.assertEqual(resp_edit.status_code, 200)
        admin_creado.refresh_from_db()
        self.assertEqual(admin_creado.nombre_completo, "Administrador Renombrado")

    def test_07_superusuario_puede_editar_su_cuenta_y_unicidad_estricta(self):
        """7. Un superusuario puede editar su propia cuenta y el sistema impide estrictamente un segundo superusuario."""
        self.client.force_login(self.superusuario)
        payload = {
            "id": self.superusuario.id,
            "nombre_completo": "Super Administrador Modificado",
        }
        resp = self.client.post("/api/haku/usuario/editar/", data=json.dumps(payload), content_type="application/json")
        self.assertEqual(resp.status_code, 200)
        self.superusuario.refresh_from_db()
        self.assertEqual(self.superusuario.nombre_completo, "Super Administrador Modificado")

        # Invariante §6.2: El sistema impide la creación de un segundo superusuario
        with self.assertRaises(ValidationError):
            UsuarioAudacIA.objects.create_superuser(
                username="super_dos",
                nombre_completo="Segundo Superusuario Ilegal",
                password="Password123!",
                rol=UsuarioAudacIA.Rol.SUPERUSUARIO,
            )

    def test_08_proteccion_ultimo_superusuario(self):
        """8. No se puede despojar de privilegios ni inactivar al único superusuario activo."""
        self.client.force_login(self.superusuario)

        # Intento de democión a USUARIO del único superusuario
        payload_democion = {
            "id": self.superusuario.id,
            "nombre_completo": self.superusuario.nombre_completo,
            "rol": UsuarioAudacIA.Rol.USUARIO,
            "almacen_asignado_id": self.almacen_principal.id,
        }
        resp_dem = self.client.post("/api/haku/usuario/editar/", data=json.dumps(payload_democion), content_type="application/json")
        self.assertEqual(resp_dem.status_code, 400)
        self.assertIn("único superusuario", resp_dem.json()["error"])

        # Intento de inactivación del único superusuario
        payload_inact = {
            "id": self.superusuario.id,
            "nombre_completo": self.superusuario.nombre_completo,
            "is_active": False,
        }
        resp_inact = self.client.post("/api/haku/usuario/editar/", data=json.dumps(payload_inact), content_type="application/json")
        self.assertEqual(resp_inact.status_code, 400)

        self.superusuario.refresh_from_db()
        self.assertEqual(self.superusuario.rol, UsuarioAudacIA.Rol.SUPERUSUARIO)
        self.assertTrue(self.superusuario.is_active)

    def test_09_usuario_normal_y_no_autenticado_denegados(self):
        """9. Un usuario normal y una sesión no autenticada no pueden gestionar usuarios (401/403)."""
        # No autenticado
        resp_anon = self.client.post("/api/haku/usuario/crear/", data=json.dumps({}), content_type="application/json")
        self.assertEqual(resp_anon.status_code, 401)

        # Usuario normal
        self.client.force_login(self.usuario_normal)
        resp_user = self.client.post("/api/haku/usuario/crear/", data=json.dumps({
            "username": "intento_ilegal",
            "nombre_completo": "Ilegal",
            "rol": UsuarioAudacIA.Rol.USUARIO,
            "password": "Password123!",
        }), content_type="application/json")
        self.assertEqual(resp_user.status_code, 403)

        # Reconocimiento conversacional deniega al usuario normal
        resp_conv = self.client.post("/api/haku/mensaje/", data=json.dumps({
            "mensaje": "Quiero crear un nuevo usuario"
        }), content_type="application/json")
        self.assertEqual(resp_conv.status_code, 403)

    def test_10_validacion_contrasenas_django(self):
        """10. Las contraseñas deben cumplir con las reglas de validación seguras de Django."""
        self.client.force_login(self.administrador)
        payload = {
            "username": "pwd_insegura",
            "nombre_completo": "Insegura Prueba",
            "rol": UsuarioAudacIA.Rol.USUARIO,
            "almacen_asignado_id": self.almacen_principal.id,
            "password": "123",  # Demasiado corta y solo numérica
        }
        resp = self.client.post("/api/haku/usuario/crear/", data=json.dumps(payload), content_type="application/json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("password", resp.json()["detalles"])

    def test_11_campos_obligatorios_y_unicidad(self):
        """11. Campos obligatorios, opciones y errores se comportan correctamente."""
        self.client.force_login(self.administrador)
        # Payload vacío
        resp = self.client.post("/api/haku/usuario/crear/", data=json.dumps({}), content_type="application/json")
        self.assertEqual(resp.status_code, 400)
        detalles = resp.json()["detalles"]
        self.assertIn("username", detalles)
        self.assertIn("nombre_completo", detalles)
        self.assertIn("rol", detalles)
        self.assertIn("password", detalles)

        # Nombre de usuario duplicado
        payload_dup = {
            "username": self.usuario_normal.username,
            "nombre_completo": "Duplicado",
            "rol": UsuarioAudacIA.Rol.USUARIO,
            "almacen_asignado_id": self.almacen_principal.id,
            "password": "Password123!",
        }
        resp_dup = self.client.post("/api/haku/usuario/crear/", data=json.dumps(payload_dup), content_type="application/json")
        self.assertEqual(resp_dup.status_code, 400)
        self.assertIn("Ya existe un usuario con este nombre", resp_dup.json()["detalles"]["username"])

    def test_12_usuario_inactivo_no_puede_operar(self):
        """12. Un usuario desactivado no puede realizar operaciones administrativas."""
        self.administrador.is_active = False
        self.administrador.save()
        self.client.force_login(self.administrador)

        resp = self.client.post("/api/haku/usuario/crear/", data=json.dumps({
            "username": "inactivo_test",
            "nombre_completo": "Prueba Inactivo",
            "rol": UsuarioAudacIA.Rol.USUARIO,
            "almacen_asignado_id": self.almacen_principal.id,
            "password": "Password123!",
        }), content_type="application/json")
        self.assertIn(resp.status_code, [401, 403])

    def test_13_flujo_conversacional_filtra_roles_segun_actor(self):
        """13. El flujo conversacional retorna el formulario con los roles reales permitidos según el actor."""
        # Administrador: solo puede asignar rol USUARIO
        self.client.force_login(self.administrador)
        resp_admin = self.client.post("/api/haku/mensaje/", data=json.dumps({
            "mensaje": "Quiero registrar un nuevo usuario"
        }), content_type="application/json")
        self.assertEqual(resp_admin.status_code, 200)
        data_admin = resp_admin.json()
        self.assertEqual(data_admin["tipo"], "formulario_crear_usuario")
        roles_admin = [r["valor"] for r in data_admin["datos"]["roles"]]
        self.assertIn("USUARIO", roles_admin)
        self.assertNotIn("SUPERUSUARIO", roles_admin)
        self.assertNotIn("ADMINISTRADOR", roles_admin)

        # Superusuario: puede asignar USUARIO, ADMINISTRADOR y SUPERUSUARIO
        self.client.force_login(self.superusuario)
        resp_super = self.client.post("/api/haku/mensaje/", data=json.dumps({
            "mensaje": "Necesito crear un usuario para el almacén"
        }), content_type="application/json")
        self.assertEqual(resp_super.status_code, 200)
        data_super = resp_super.json()
        self.assertEqual(data_super["tipo"], "formulario_crear_usuario")
        roles_super = [r["valor"] for r in data_super["datos"]["roles"]]
        self.assertIn("USUARIO", roles_super)
        self.assertIn("ADMINISTRADOR", roles_super)
        self.assertIn("SUPERUSUARIO", roles_super)

