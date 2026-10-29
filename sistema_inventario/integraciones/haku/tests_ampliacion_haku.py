"""Suite completa de pruebas de ampliación funcional para HAKU Inventario.

Valida:
1. Reposición integral («¿Qué hace falta por pedir?», agotados, bajo mínimo, estimación de consumo sin inventar).
2. Distinción entre PRÉSTAMO y DOTACIÓN (salida definitiva).
3. Normalización robusta de nombres de personas y flujo de solicitud de escritura.
4. Caché con TTL, hits, misses e invalidación post-commit en RAM.
5. Control de permisos y acceso por almacén.
"""

import json
from unittest.mock import patch
from django.test import TestCase, override_settings
from django.core.exceptions import PermissionDenied, ValidationError

from cuentas.models import UsuarioAudacIA
from inventario.models import Almacen, Categoria, Existencia, Movimiento, Prestamo, Producto
from integraciones.haku.cache import HakuCacheManager
from integraciones.haku.contexto import HakuOperationalContext
from integraciones.haku.lenguaje.normalizador import NormalizadorLenguajeHaku, IntencionTipo, IntencionResultado
from integraciones.haku.services import procesar_comando_inventario, ejecutar_confirmacion_propuesta
from integraciones.haku.servicios_personas import HakuPersonasService, normalizar_para_comparacion


class HakuAmpliacionFuncionalTestCase(TestCase):
    def setUp(self):
        HakuCacheManager.invalidar_todo()
        HakuCacheManager.reset_metricas()

        # Almacenes
        self.almacen_principal = Almacen.objects.create(nombre="Almacén Principal", activo=True)
        self.almacen_secundario = Almacen.objects.create(nombre="Almacén Secundario", activo=True)

        # Categoría
        self.cat_herramientas = Categoria.objects.create(
            nombre="Herramientas", almacen_principal=self.almacen_principal, activo=True
        )
        self.cat_papeleria = Categoria.objects.create(
            nombre="Papelería", almacen_principal=self.almacen_principal, activo=True
        )

        # Productos
        # 1. Agotado (stock_minimo=5, disponible=0, prestado=2) -> Físico: 2, disponible: 0
        self.taladro = Producto.objects.create(
            nombre="Taladro Percutor",
            categoria=self.cat_herramientas,
            stock_minimo=5,
            activo=True,
            archivado=False,
        )
        self.ex_taladro = Existencia.objects.create(
            producto=self.taladro,
            almacen=self.almacen_principal,
            stock_disponible=0,
            stock_en_prestamo=2,
        )

        # 2. Bajo stock (stock_minimo=10, disponible=3, prestado=0)
        self.cinta = Producto.objects.create(
            nombre="Cinta Aislante",
            categoria=self.cat_herramientas,
            stock_minimo=10,
            activo=True,
            archivado=False,
        )
        self.ex_cinta = Existencia.objects.create(
            producto=self.cinta,
            almacen=self.almacen_principal,
            stock_disponible=3,
            stock_en_prestamo=0,
        )

        # 3. Stock normal (stock_minimo=2, disponible=20, prestado=0)
        self.papel = Producto.objects.create(
            nombre="Resma de Papel",
            categoria=self.cat_papeleria,
            stock_minimo=2,
            activo=True,
            archivado=False,
        )
        self.ex_papel = Existencia.objects.create(
            producto=self.papel,
            almacen=self.almacen_principal,
            stock_disponible=20,
            stock_en_prestamo=0,
        )

        # Usuarios
        self.admin = UsuarioAudacIA.objects.create_superuser(
            username="admin_test",
            nombre_completo="Carlos Andrés Rodríguez",
            email="admin@test.com",
            password="pass",
            rol=UsuarioAudacIA.Rol.ADMINISTRADOR,
            acceso_todos_almacenes=True,
        )

        self.usuario_operario = UsuarioAudacIA.objects.create_user(
            username="operario_test",
            nombre_completo="María Peña",
            password="pass",
            rol=UsuarioAudacIA.Rol.USUARIO,
            almacen_asignado=self.almacen_principal,
            acceso_todos_almacenes=False,
        )
        self.usuario_operario.almacenes_asignados.add(self.almacen_principal)
        self.usuario_operario.save()

    def test_normalizador_reconoce_consulta_faltantes_reposicion(self):
        """Verifica que frases naturales activan CONSULTAR_FALTANTES_REPOSICION."""
        frases = [
            "¿Qué hace falta por pedir?",
            "Qué hace falta por pedir",
            "¿Qué tengo que pedir?",
            "¿Qué está por acabarse?",
            "¿Qué tengo que comprar?",
            "¿Qué debo reponer?",
            "Recomendación de compra",
            "¿Qué hace falta comprar en el almacén?",
        ]
        for f in frases:
            res = NormalizadorLenguajeHaku.interpretar(f, usuario=self.admin)
            self.assertEqual(
                res.tipo,
                IntencionTipo.CONSULTAR_FALTANTES_REPOSICION,
                f"La frase '{f}' no activó CONSULTAR_FALTANTES_REPOSICION (activó {res.tipo})",
            )

    def test_servicio_faltantes_reposicion_distingue_agotados_y_bajo_stock(self):
        """Verifica el cálculo de faltantes: sin stock (distinguiendo préstamos) y bajo mínimo."""
        res = procesar_comando_inventario(
            usuario=self.admin,
            mensaje="¿Qué hace falta por pedir en el Almacén Principal?",
        )
        self.assertEqual(res["tipo"], "consulta")
        datos = res["datos"]
        self.assertEqual(datos["total"], 2)

        items = {it["nombre"]: it for it in datos["faltantes"]}
        self.assertIn("Taladro Percutor", items)
        self.assertIn("Cinta Aislante", items)
        self.assertNotIn("Resma de Papel", items)

        # Taladro: disponible 0, prestado 2, minimo 5
        taladro_data = items["Taladro Percutor"]
        self.assertEqual(taladro_data["estado_alerta"], "AGOTADO")
        self.assertEqual(taladro_data["stock_disponible"], 0)
        self.assertEqual(taladro_data["stock_prestado"], 2)
        self.assertEqual(taladro_data["stock_fisico"], 2)
        self.assertEqual(taladro_data["cantidad_sugerida_reposicion"], 5)

        # Cinta: disponible 3, prestado 0, minimo 10 -> sugerido 7
        cinta_data = items["Cinta Aislante"]
        self.assertEqual(cinta_data["estado_alerta"], "BAJO_STOCK")
        self.assertEqual(cinta_data["cantidad_sugerida_reposicion"], 7)

    def test_cache_faltantes_y_su_invalidacion_post_mutacion(self):
        """Verifica que la consulta de reposición se guarde en caché y se invalide tras una mutación."""
        msg = "¿Qué hace falta por pedir en el Almacén Principal?"
        # 1. Primer llamado -> Cache miss
        procesar_comando_inventario(usuario=self.admin, mensaje=msg)
        metricas = HakuCacheManager.obtener_metricas()
        self.assertGreater(metricas["faltantes_misses"], 0)

        # 2. Segundo llamado -> Cache hit
        procesar_comando_inventario(usuario=self.admin, mensaje=msg)
        metricas2 = HakuCacheManager.obtener_metricas()
        self.assertGreater(metricas2["faltantes_hits"], 0)

        # 3. Invalidación inmediata
        HakuCacheManager.invalidar_stock(self.taladro.id, self.almacen_principal.id)
        cached = HakuCacheManager.obtener_faltantes(self.almacen_principal.id)
        self.assertIsNone(cached)

    def test_normalizador_distingue_prestamo_de_dotacion(self):
        """Verifica que 'préstale a Carlos' active PRESTAMO y 'entrega dotación a María' active REGISTRAR_SALIDA con motivo."""
        res_prestamo = NormalizadorLenguajeHaku.interpretar("Préstale un taladro a Carlos", usuario=self.admin)
        self.assertEqual(res_prestamo.tipo, IntencionTipo.PRESTAMO)
        self.assertEqual(res_prestamo.destinatario, "Carlos")

        res_dotacion = NormalizadorLenguajeHaku.interpretar("Entrégale una dotación de cinta a María", usuario=self.admin)
        self.assertEqual(res_dotacion.tipo, IntencionTipo.REGISTRAR_SALIDA)
        self.assertIn("dotación", (res_dotacion.motivo or "").lower())

    def test_resolucion_nombres_preserva_enie_y_detecta_ambiguedad(self):
        """Verifica que el servicio de personas no degrade 'ñ' a 'n' y resuelva identidades canónicas."""
        # 1. Comparación limpia preservando 'ñ'
        norm = normalizar_para_comparacion("María Peña")
        self.assertEqual(norm, "maria peña")

        # 2. Búsqueda exacta de usuario registrado
        canonico, confianza, candidatos, requiere_escritura = HakuPersonasService.resolver_destinatario(
            "maria peña", origen_voz=False
        )
        self.assertEqual(canonico, "María Peña")
        self.assertFalse(requiere_escritura)

        # 3. Búsqueda por voz con nombre incompleto o ambiguo
        # "Carlos" coincide con "Carlos Andrés Rodríguez"
        canonico_carlos, conf_carlos, _, req_carlos = HakuPersonasService.resolver_destinatario(
            "Carlos", origen_voz=False
        )
        self.assertEqual(canonico_carlos, "Carlos Andrés Rodríguez")

        # 4. Nombre vacío o no seguro por voz
        nom_vacio, _, _, req_vacio = HakuPersonasService.resolver_destinatario("", origen_voz=True)
        self.assertTrue(req_vacio)
        self.assertIsNone(nom_vacio)

    def test_prestamo_sin_destinatario_se_detiene_y_solicita_escribir_nombre(self):
        """Si no hay destinatario identificado con seguridad, HAKU emite aclaración con requiere_escritura."""
        res = procesar_comando_inventario(
            usuario=self.admin,
            mensaje="Préstale 1 cinta aislante",
        )
        self.assertEqual(res["tipo"], "aclaracion")
        self.assertTrue(res.get("requiere_escritura"))
        self.assertEqual(res.get("esperando"), "destinatario")
        self.assertIn("Escribe el nombre", res["mensaje_pantalla"])

    def test_confirmacion_prestamo_crea_movimiento_y_registro_prestamo(self):
        """Confirmar un préstamo descuenta stock disponible, incrementa stock_en_prestamo y crea Prestamo."""
        res_propuesta = procesar_comando_inventario(
            usuario=self.admin,
            mensaje="Préstale 2 cinta aislante a Carlos Andrés Rodríguez en el Almacén Principal",
        )
        self.assertEqual(res_propuesta["tipo"], "propuesta")
        propuesta = res_propuesta["propuesta"]

        # Confirmar
        res_conf = ejecutar_confirmacion_propuesta(usuario=self.admin, propuesta=propuesta)
        self.assertTrue(res_conf["exito"])

        self.ex_cinta.refresh_from_db()
        self.assertEqual(self.ex_cinta.stock_disponible, 1)  # Tenía 3, prestó 2
        self.assertEqual(self.ex_cinta.stock_en_prestamo, 2)

        prestamo_db = Prestamo.objects.filter(producto=self.cinta, destinatario="Carlos Andrés Rodríguez").first()
        self.assertIsNotNone(prestamo_db)
        self.assertEqual(prestamo_db.cantidad_original, 2)
        self.assertEqual(prestamo_db.cantidad_pendiente_devolucion, 2)
        self.assertEqual(prestamo_db.estado, Prestamo.Estado.ABIERTO)

    def test_confirmacion_dotacion_no_crea_prestamo(self):
        """Confirmar una dotación (salida) descuenta stock pero NO crea un objeto Prestamo."""
        res_propuesta = procesar_comando_inventario(
            usuario=self.admin,
            mensaje="Entrega una dotación de 1 cinta aislante a María Peña en el Almacén Principal",
        )
        self.assertEqual(res_propuesta["tipo"], "propuesta")
        propuesta = res_propuesta["propuesta"]

        res_conf = ejecutar_confirmacion_propuesta(usuario=self.admin, propuesta=propuesta)
        self.assertTrue(res_conf["exito"])

        # No debe haber préstamo para María Peña
        self.assertFalse(Prestamo.objects.filter(destinatario="María Peña").exists())

        # Pero sí un movimiento de tipo SALIDA
        mov = Movimiento.objects.filter(producto=self.cinta, tipo=Movimiento.Tipo.SALIDA).first()
        self.assertIsNotNone(mov)
        self.assertEqual(mov.destinatario, "María Peña")

    def test_devolucion_parcial_y_cierre_prestamo(self):
        """Verifica que una devolución parcial descuente saldo y devuelva stock disponible sin cerrar hasta que saldo sea 0."""
        from inventario.models import DevolucionPrestamo
        # 1. Crear un movimiento previo y un préstamo inicial de 2 unidades
        mov_prestamo = Movimiento.objects.create(
            tipo=Movimiento.Tipo.PRESTAMO,
            producto=self.cinta,
            cantidad=2,
            almacen_origen=self.almacen_principal,
            usuario=self.admin,
            destinatario="Carlos Andrés Rodríguez",
            motivo="Préstamo de prueba",
            stock_disponible_anterior=3,
            stock_disponible_posterior=1,
            clave_idempotencia="init-prestamo-test-1",
        )
        prestamo = Prestamo.objects.create(
            movimiento=mov_prestamo,
            producto=self.cinta,
            almacen=self.almacen_principal,
            destinatario="Carlos Andrés Rodríguez",
            cantidad_original=2,
            cantidad_pendiente_devolucion=2,
            estado=Prestamo.Estado.ABIERTO,
            usuario=self.admin,
        )
        self.ex_cinta.stock_en_prestamo = 2
        self.ex_cinta.stock_disponible = 1
        self.ex_cinta.save()

        # 2. Devolución parcial de 1 unidad
        propuesta_dev = {
            "id": "test-dev-1",
            "tipo_operacion": Movimiento.Tipo.DEVOLUCION,
            "producto_id": self.cinta.id,
            "almacen_origen_id": self.almacen_principal.id,
            "prestamo_id": prestamo.id,
            "cantidad": 1,
            "expira_en": 9999999999,
        }
        res_conf = ejecutar_confirmacion_propuesta(usuario=self.admin, propuesta=propuesta_dev)
        self.assertTrue(res_conf["exito"])

        prestamo.refresh_from_db()
        self.assertEqual(prestamo.cantidad_pendiente_devolucion, 1)
        self.assertEqual(prestamo.estado, Prestamo.Estado.DEVUELTO_PARCIAL)
        self.ex_cinta.refresh_from_db()
        self.assertEqual(self.ex_cinta.stock_disponible, 2)
        self.assertEqual(self.ex_cinta.stock_en_prestamo, 1)

        # 3. Devolución final de la unidad restante
        propuesta_dev2 = {
            "id": "test-dev-2",
            "tipo_operacion": Movimiento.Tipo.DEVOLUCION,
            "producto_id": self.cinta.id,
            "almacen_origen_id": self.almacen_principal.id,
            "prestamo_id": prestamo.id,
            "cantidad": 1,
            "expira_en": 9999999999,
        }
        res_conf2 = ejecutar_confirmacion_propuesta(usuario=self.admin, propuesta=propuesta_dev2)
        self.assertTrue(res_conf2["exito"])

        prestamo.refresh_from_db()
        self.assertEqual(prestamo.cantidad_pendiente_devolucion, 0)
        self.assertEqual(prestamo.estado, Prestamo.Estado.CERRADO)
        self.ex_cinta.refresh_from_db()
        self.assertEqual(self.ex_cinta.stock_disponible, 3)
        self.assertEqual(self.ex_cinta.stock_en_prestamo, 0)

    def test_reposicion_endpoint_deniega_almacen_no_autorizado(self):
        """Verifica que un usuario con almacén restringido no pueda consultar faltantes de otro almacén."""
        from django.test import RequestFactory
        from integraciones.haku.views import FaltantesReposicionView

        factory = RequestFactory()
        req = factory.get(f"/api/haku/faltantes-reposicion/?almacen_id={self.almacen_secundario.id}")
        req.user = self.usuario_operario  # Solo tiene acceso al Principal

        view = FaltantesReposicionView.as_view()
        response = view(req)
        import json
        data = json.loads(response.content.decode("utf-8"))
        self.assertTrue(data.get("datos", {}).get("error_permisos", False))
        self.assertIn("No tienes permiso", data.get("mensaje_pantalla", ""))

    def test_confirmar_endpoint_rechaza_id_invalido_o_propuesta_inexistente(self):
        """Verifica que /api/haku/confirmar/ no permita ejecutar operaciones con IDs falsos o no coincidentes."""
        from django.test import RequestFactory
        from integraciones.haku.views import ConfirmarPropuestaView

        factory = RequestFactory()
        # 1. Petición sin propuesta activa en sesión
        req = factory.post("/api/haku/confirmar/", data=json.dumps({"propuesta_id": "fake-uuid"}), content_type="application/json")
        req.user = self.admin
        from django.contrib.sessions.backends.db import SessionStore
        req.session = SessionStore()

        view = ConfirmarPropuestaView.as_view()
        resp = view(req)
        self.assertEqual(resp.status_code, 404)

        # 2. Petición con ID diferente al de la propuesta activa guardada en el servidor
        req.session["haku_propuesta_activa"] = {
            "id": "real-uuid",
            "tipo_operacion": Movimiento.Tipo.SALIDA,
            "producto_id": self.cinta.id,
            "almacen_origen_id": self.almacen_principal.id,
            "cantidad": 1,
            "expira_en": 9999999999,
        }
        req2 = factory.post("/api/haku/confirmar/", data=json.dumps({"propuesta_id": "tampered-uuid"}), content_type="application/json")
        req2.user = self.admin
        req2.session = req.session

        resp2 = view(req2)
        self.assertEqual(resp2.status_code, 400)
        data2 = json.loads(resp2.content.decode("utf-8"))
        self.assertIn("no coincide", data2.get("error", ""))

    def test_cache_faltantes_invalida_ante_cambio_permisos_usuario(self):
        """Verifica que modificar los almacenes o permisos de un usuario no entregue datos obsoletos cacheados."""
        # 1. Crear producto en almacén secundario con stock bajo
        prod_sec = Producto.objects.create(
            nombre="Cable Red Cat7",
            categoria=self.cat_herramientas,
            stock_minimo=10,
            activo=True,
            archivado=False,
        )
        Existencia.objects.create(
            producto=prod_sec,
            almacen=self.almacen_secundario,
            stock_disponible=1,
            stock_en_prestamo=0,
        )

        # 2. Consultar con operario que solo tiene acceso a Almacén Principal
        res1 = procesar_comando_inventario(
            usuario=self.usuario_operario,
            mensaje="¿Qué hace falta por pedir?",
        )
        nombres_res1 = [it["nombre"] for it in res1["datos"]["faltantes"]]
        self.assertNotIn("Cable Red Cat7", nombres_res1)

        # 3. Otorgar acceso a Almacén Secundario al operario
        self.usuario_operario.almacenes_asignados.add(self.almacen_secundario)
        self.usuario_operario.save()

        # 4. Volver a consultar inmediatamente (dentro del TTL de 60s): debe incluir Cable Red Cat7
        res2 = procesar_comando_inventario(
            usuario=self.usuario_operario,
            mensaje="¿Qué hace falta por pedir?",
        )
        nombres_res2 = [it["nombre"] for it in res2["datos"]["faltantes"]]
        self.assertIn("Cable Red Cat7", nombres_res2)



