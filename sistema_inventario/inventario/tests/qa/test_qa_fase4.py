"""Pruebas adversariales de QA para la Fase 4: Operaciones, Préstamos, Conteos y Alertas.

Escenarios adversariales verificados:
1. Doble reversión de un movimiento ya revertido (debe lanzar ValidationError y preservar consistencia).
2. Intento de devolución o baja sobre un préstamo ya cerrado (debe fallar limpiamente con ValidationError).
3. Traslado entre almacenes que deje el almacén de origen exactamente en 0 (debe permitirlo, marcar alerta crítica y evitar stock negativo).
4. Intento de un usuario normal de confirmar un conteo físico vía POST directo a la vista (debe retornar 403 Forbidden).
"""

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model

from cuentas.models import UsuarioAudacIA
from inventario.models import (
    Almacen,
    Categoria,
    ConteoFisico,
    Existencia,
    Movimiento,
    Prestamo,
    Producto,
)
from inventario.servicios.alertas import obtener_alertas_stock, verificar_estado_alerta
from inventario.servicios.operaciones import (
    confirmar_conteo_fisico,
    registrar_baja_prestamo,
    registrar_conteo_fisico,
    registrar_devolucion_prestamo,
    registrar_entrada,
    registrar_prestamo,
    registrar_salida,
    registrar_traslado_entre_almacenes,
    revertir_movimiento,
)

User = get_user_model()


class QAOperacionesFase4AdversarialTest(TestCase):
    """Suite de pruebas de aseguramiento de calidad (QA) adversarial para la Fase 4."""

    def setUp(self):
        # 1. Almacenes para pruebas
        self.almacen_principal = Almacen.objects.create(
            nombre="Almacén QA Principal",
            descripcion="Almacén principal para pruebas de estrés QA",
            activo=True,
        )
        self.almacen_secundario = Almacen.objects.create(
            nombre="Almacén QA Secundario",
            descripcion="Almacén secundario de destino",
            activo=True,
        )

        # 2. Usuarios con roles diferenciados
        self.admin = User.objects.create_user(
            username="qa_admin",
            password="AdminPassword123!",
            nombre_completo="Administrador de Calidad",
            rol=UsuarioAudacIA.Rol.ADMINISTRADOR,
        )
        self.operador_principal = User.objects.create_user(
            username="qa_operador",
            password="UserPassword123!",
            nombre_completo="Operador Almacén Principal",
            rol=UsuarioAudacIA.Rol.USUARIO,
            almacen_asignado=self.almacen_principal,
        )
        self.operador_ajeno = User.objects.create_user(
            username="qa_operador_secundario",
            password="UserPassword123!",
            nombre_completo="Operador Almacén Secundario",
            rol=UsuarioAudacIA.Rol.USUARIO,
            almacen_asignado=self.almacen_secundario,
        )

        # 3. Categoría y productos
        self.categoria = Categoria.objects.create(
            nombre="Equipos de Laboratorio",
            almacen_principal=self.almacen_principal,
        )
        self.producto_a = Producto.objects.create(
            nombre="Osciloscopio Digital 100MHz",
            categoria=self.categoria,
            stock_minimo=3,
        )
        self.producto_b = Producto.objects.create(
            nombre="Generador de Señales RF",
            categoria=self.categoria,
            stock_minimo=2,
        )

    # =========================================================================
    # ESCENARIO 1: Doble reversión de un movimiento ya revertido
    # =========================================================================
    def test_doble_reversion_de_movimiento_lanza_validation_error(self):
        """Intento de revertir dos veces el mismo movimiento debe fallar limpiamente con ValidationError."""
        # 1. Ingreso de 10 unidades
        registrar_entrada(
            producto=self.producto_a,
            almacen=self.almacen_principal,
            cantidad=10,
            usuario=self.operador_principal,
            clave_idempotencia="QA-ENTRADA-REV-01",
        )

        # 2. Salida de 4 unidades -> Quedan 6
        ex_pre, mov_salida, _ = registrar_salida(
            producto=self.producto_a,
            almacen=self.almacen_principal,
            cantidad=4,
            motivo="Práctica de campo",
            destinatario="Profesor Investigador",
            usuario=self.operador_principal,
            clave_idempotencia="QA-SALIDA-REV-01",
        )
        self.assertEqual(ex_pre.stock_disponible, 6)
        self.assertEqual(mov_salida.estado, Movimiento.Estado.CONFIRMADO)

        # 3. Primera reversión legítima por administrador
        mov_orig_rev, mov_compensatorio = revertir_movimiento(
            movimiento=mov_salida,
            usuario=self.admin,
            motivo="Cancelación formal de orden de salida por error administrativo",
        )
        ex_post = Existencia.objects.get(producto=self.producto_a, almacen=self.almacen_principal)
        self.assertEqual(ex_post.stock_disponible, 10)  # Restaurado a 10
        self.assertEqual(mov_orig_rev.estado, Movimiento.Estado.REVERTIDO)
        self.assertEqual(mov_compensatorio.tipo, Movimiento.Tipo.REVERSION)
        self.assertEqual(mov_compensatorio.movimiento_original_id, mov_salida.id)

        # 4. Intento ADVERSARIAL de segunda reversión sobre el mismo movimiento original
        with self.assertRaises(ValidationError) as ctx:
            revertir_movimiento(
                movimiento=mov_salida,
                usuario=self.admin,
                motivo="Segundo intento malintencionado o accidental de reversión",
            )
        self.assertIn("ya ha sido revertido", str(ctx.exception).lower())

        # 5. Intento ADVERSARIAL de revertir el propio movimiento compensatorio (de tipo REVERSION)
        with self.assertRaises(ValidationError) as ctx_comp:
            revertir_movimiento(
                movimiento=mov_compensatorio,
                usuario=self.admin,
                motivo="Intento de revertir la reversión",
            )
        self.assertIn("no se admite reversión", str(ctx_comp.exception).lower())

        # 6. Verificación de integridad: el stock en base de datos debe permanecer exactamente en 10
        ex_post.refresh_from_db()
        self.assertEqual(ex_post.stock_disponible, 10)

    # =========================================================================
    # ESCENARIO 2: Intento de devolución o baja sobre un préstamo ya cerrado
    # =========================================================================
    def test_devolucion_o_baja_sobre_prestamo_cerrado_falla_limpiamente(self):
        """Intento de devolver o dar de baja sobre un préstamo con estado CERRADO debe lanzar ValidationError."""
        # 1. Ingreso de 5 generadores de señales
        registrar_entrada(
            producto=self.producto_b,
            almacen=self.almacen_principal,
            cantidad=5,
            usuario=self.operador_principal,
            clave_idempotencia="QA-ENT-GEN-01",
        )

        # 2. Préstamo de 3 unidades
        prestamo, mov_pres, _ = registrar_prestamo(
            producto=self.producto_b,
            almacen=self.almacen_principal,
            cantidad=3,
            destinatario="Estudiante Proyecto Grado",
            usuario=self.operador_principal,
            motivo="Medición de filtros pasivos",
            clave_idempotencia="QA-PRES-GEN-01",
        )
        ex = Existencia.objects.get(producto=self.producto_b, almacen=self.almacen_principal)
        self.assertEqual(ex.stock_disponible, 2)
        self.assertEqual(ex.stock_en_prestamo, 3)
        self.assertEqual(prestamo.estado, Prestamo.Estado.ABIERTO)

        # 3. Devolución total de las 3 unidades -> Préstamo pasa a CERRADO
        prestamo_cerrado, dev, _ = registrar_devolucion_prestamo(
            prestamo=prestamo,
            cantidad=3,
            usuario=self.operador_principal,
            motivo="Devolución completa e impecable de equipos",
        )
        self.assertEqual(prestamo_cerrado.estado, Prestamo.Estado.CERRADO)
        self.assertEqual(prestamo_cerrado.cantidad_pendiente_devolucion, 0)
        self.assertIsNotNone(prestamo_cerrado.fecha_cierre)

        ex.refresh_from_db()
        self.assertEqual(ex.stock_disponible, 5)
        self.assertEqual(ex.stock_en_prestamo, 0)

        # 4. Intento ADVERSARIAL de registrar una nueva devolución sobre el préstamo CERRADO
        with self.assertRaises(ValidationError) as ctx_dev:
            registrar_devolucion_prestamo(
                prestamo=prestamo_cerrado,
                cantidad=1,
                usuario=self.operador_principal,
                motivo="Intento de devolución extra sobre préstamo cerrado",
            )
        self.assertIn("ya se encuentra cerrado", str(ctx_dev.exception).lower())

        # 5. Intento ADVERSARIAL de registrar una baja por daño sobre el préstamo CERRADO
        with self.assertRaises(ValidationError) as ctx_baja:
            registrar_baja_prestamo(
                prestamo=prestamo_cerrado,
                cantidad=1,
                usuario=self.operador_principal,
                motivo="Intento de baja por daño tardía sobre préstamo cerrado",
            )
        self.assertIn("ya se encuentra cerrado", str(ctx_baja.exception).lower())

        # 6. Verificación de integridad: el stock disponible sigue siendo 5 y en préstamo 0
        ex.refresh_from_db()
        self.assertEqual(ex.stock_disponible, 5)
        self.assertEqual(ex.stock_en_prestamo, 0)

    # =========================================================================
    # ESCENARIO 3: Traslado entre almacenes que agote stock origen a exactamente 0
    # =========================================================================
    def test_traslado_entre_almacenes_stock_cero_dispara_alerta_critica_sin_negatividad(self):
        """Traslado total de stock deja origen en 0, genera alerta crítica en origen y destino queda abastecido."""
        # 1. Ingreso de exactamente 4 osciloscopios en almacén principal (stock_minimo=3)
        registrar_entrada(
            producto=self.producto_a,
            almacen=self.almacen_principal,
            cantidad=4,
            usuario=self.operador_principal,
            clave_idempotencia="QA-ENT-OSC-4",
        )

        ex_orig_ini = Existencia.objects.get(producto=self.producto_a, almacen=self.almacen_principal)
        self.assertEqual(ex_orig_ini.stock_disponible, 4)

        # 2. Traslado de las 4 unidades exactas al almacén secundario
        ex_orig, ex_dest, mov_traslado = registrar_traslado_entre_almacenes(
            producto=self.producto_a,
            almacen_origen=self.almacen_principal,
            almacen_destino=self.almacen_secundario,
            cantidad=4,
            usuario=self.operador_principal,
            motivo="Reabastecimiento urgente de laboratorio secundario",
            clave_idempotencia="QA-TRAS-TOTAL-01",
        )

        # Verificaciones cuantitativas de stock
        self.assertEqual(ex_orig.stock_disponible, 0)
        self.assertGreaterEqual(ex_orig.stock_disponible, 0, "El stock de origen no debe ser negativo")
        self.assertEqual(ex_dest.stock_disponible, 4)

        # 3. Verificación de alerta crítica sobre el almacén de origen
        alerta_origen = verificar_estado_alerta(ex_orig)
        self.assertEqual(alerta_origen["nivel"], "CRITICO")
        self.assertIn("AGOTADO", alerta_origen["mensaje"])

        # 4. Verificación en el motor de alertas globales para el almacén principal
        alertas_principal = obtener_alertas_stock(almacen=self.almacen_principal)
        self.assertGreaterEqual(alertas_principal["total_criticas"], 1)
        self.assertIn(ex_orig.id, [e.id for e in alertas_principal["criticas"]])

        # 5. Intento ADVERSARIAL de trasladar 1 unidad adicional desde el almacén agotado
        with self.assertRaises(ValidationError) as ctx_insuf:
            registrar_traslado_entre_almacenes(
                producto=self.producto_a,
                almacen_origen=self.almacen_principal,
                almacen_destino=self.almacen_secundario,
                cantidad=1,
                usuario=self.operador_principal,
                motivo="Intento de traslado cuando stock es 0",
            )
        self.assertIn("stock insuficiente", str(ctx_insuf.exception).lower())

        # El stock continúa exactamente en 0
        ex_orig.refresh_from_db()
        self.assertEqual(ex_orig.stock_disponible, 0)

    # =========================================================================
    # ESCENARIO 4: Intento de usuario normal de confirmar conteo vía POST (403 Forbidden)
    # =========================================================================
    def test_usuario_normal_confirmar_conteo_post_directo_retorna_403(self):
        """Un usuario normal intentando confirmar un conteo físico vía POST directo debe recibir HTTP 403 Forbidden."""
        # 1. Ingreso de 8 osciloscopios
        registrar_entrada(
            producto=self.producto_a,
            almacen=self.almacen_principal,
            cantidad=8,
            usuario=self.operador_principal,
            clave_idempotencia="QA-ENT-CONTEO-01",
        )

        # 2. Operador registra conteo preliminar con discrepancia (contó 10, sistema tiene 8)
        conteo = registrar_conteo_fisico(
            producto=self.producto_a,
            almacen=self.almacen_principal,
            cantidad_contada=10,
            usuario=self.operador_principal,
            motivo="Conteo de verificación semestral",
        )
        self.assertFalse(conteo.confirmado)
        self.assertEqual(conteo.diferencia, 2)

        # 3. Autenticación como usuario normal (operador_principal)
        self.client.force_login(self.operador_principal)
        url_confirmar = reverse("inventario:conteo-confirmar", kwargs={"pk": conteo.pk})

        # 4. Intento ADVERSARIAL: POST directo a la vista de confirmación
        response_adversarial = self.client.post(url_confirmar)

        # DEBE RETORNAR 403 FORBIDDEN
        self.assertEqual(
            response_adversarial.status_code,
            403,
            f"Se esperaba código 403 Forbidden para usuario normal, pero se obtuvo {response_adversarial.status_code}.",
        )

        # 5. Verificación de que el conteo NO fue confirmado ni el stock alterado
        conteo.refresh_from_db()
        self.assertFalse(conteo.confirmado)
        self.assertIsNone(conteo.usuario_confirmacion)

        ex = Existencia.objects.get(producto=self.producto_a, almacen=self.almacen_principal)
        self.assertEqual(ex.stock_disponible, 8)  # Inalterado

        # 6. Verificación legítima: Cuando un Administrador ejecuta el POST, sí debe confirmarse
        self.client.force_login(self.admin)
        response_admin = self.client.post(url_confirmar)
        self.assertRedirects(response_admin, reverse("inventario:conteo-lista"))

        conteo.refresh_from_db()
        self.assertTrue(conteo.confirmado)
        self.assertEqual(conteo.usuario_confirmacion, self.admin)

        ex.refresh_from_db()
        self.assertEqual(ex.stock_disponible, 10)  # Ajustado a 10 por el conteo
