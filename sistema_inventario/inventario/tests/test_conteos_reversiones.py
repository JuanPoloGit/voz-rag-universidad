"""Pruebas unitarias para conteos físicos y reversiones compensatorias."""

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.contrib.auth import get_user_model

from cuentas.models import UsuarioAudacIA
from inventario.models import Almacen, Categoria, ConteoFisico, Existencia, Movimiento, Producto
from inventario.servicios.operaciones import (
    confirmar_conteo_fisico,
    registrar_conteo_fisico,
    registrar_entrada,
    registrar_salida,
    revertir_movimiento,
)

User = get_user_model()


class ConteosReversionesTest(TestCase):
    def setUp(self):
        self.almacen = Almacen.objects.create(nombre="Almacén Central", activo=True)

        self.superadmin = User.objects.create_superuser(
            username="superadmin",
            password="Password123",
            nombre_completo="Super Admin",
        )
        self.admin = User.objects.create_user(
            username="admin",
            password="Password123",
            nombre_completo="Administrador",
            rol=UsuarioAudacIA.Rol.ADMINISTRADOR,
        )
        self.operador = User.objects.create_user(
            username="operador",
            password="Password123",
            nombre_completo="Operador",
            rol=UsuarioAudacIA.Rol.USUARIO,
            almacen_asignado=self.almacen,
        )

        self.categoria = Categoria.objects.create(
            nombre="Electrónica",
            almacen_principal=self.almacen,
        )
        self.producto = Producto.objects.create(
            nombre="Arduino Uno R3",
            categoria=self.categoria,
            stock_minimo=3,
        )

        # Entrada inicial de 10 unidades
        registrar_entrada(
            producto=self.producto,
            almacen=self.almacen,
            cantidad=10,
            usuario=self.operador,
            clave_idempotencia="STOCK-INI-ARDUINO",
        )

    def test_registrar_conteo_fisico_no_altera_stock(self):
        """El registro preliminar calcula diferencia pero no altera el stock del sistema."""
        # Se contaron 8 unidades físicas (diferencia: -2)
        conteo = registrar_conteo_fisico(
            producto=self.producto,
            almacen=self.almacen,
            cantidad_contada=8,
            usuario=self.operador,
            motivo="Conteo quincenal",
        )
        ex = Existencia.objects.get(producto=self.producto, almacen=self.almacen)
        self.assertEqual(ex.stock_disponible, 10)  # Inalterado
        self.assertEqual(conteo.stock_sistema, 10)
        self.assertEqual(conteo.cantidad_contada, 8)
        self.assertEqual(conteo.diferencia, -2)
        self.assertFalse(conteo.confirmado)

    def test_usuario_normal_no_puede_confirmar_conteo(self):
        """Solo administradores o superusuario pueden confirmar ajustes de conteo (§6.2, §9.7)."""
        conteo = registrar_conteo_fisico(
            producto=self.producto,
            almacen=self.almacen,
            cantidad_contada=8,
            usuario=self.operador,
        )
        with self.assertRaises(PermissionDenied):
            confirmar_conteo_fisico(
                conteo=conteo,
                usuario=self.operador,
            )

    def test_confirmar_conteo_fisico_por_administrador(self):
        """El administrador confirma el conteo y se aplica el ajuste atómico."""
        conteo = registrar_conteo_fisico(
            producto=self.producto,
            almacen=self.almacen,
            cantidad_contada=12,  # Aparecieron 2 más (+2)
            usuario=self.operador,
            motivo="Hallazgo en caja cerrada",
        )
        conteo_db, mov = confirmar_conteo_fisico(
            conteo=conteo,
            usuario=self.admin,
        )
        ex = Existencia.objects.get(producto=self.producto, almacen=self.almacen)
        self.assertEqual(ex.stock_disponible, 12)
        self.assertTrue(conteo_db.confirmado)
        self.assertEqual(conteo_db.usuario_confirmacion, self.admin)
        self.assertEqual(mov.tipo, Movimiento.Tipo.AJUSTE_CONTEO)
        self.assertEqual(mov.stock_disponible_anterior, 10)
        self.assertEqual(mov.stock_disponible_posterior, 12)

    def test_revertir_salida_restaura_stock(self):
        """Revertir una salida suma de vuelta la cantidad y marca REVERTIDO (§9.6)."""
        _, mov_salida, _ = registrar_salida(
            producto=self.producto,
            almacen=self.almacen,
            cantidad=4,
            motivo="Salida por error",
            destinatario="Persona X",
            usuario=self.operador,
            clave_idempotencia="SAL-REV-TEST",
        )
        ex = Existencia.objects.get(producto=self.producto, almacen=self.almacen)
        self.assertEqual(ex.stock_disponible, 6)

        mov_orig, mov_rev = revertir_movimiento(
            movimiento=mov_salida,
            usuario=self.admin,
            motivo="Cancelación de orden de salida",
        )
        ex.refresh_from_db()
        self.assertEqual(ex.stock_disponible, 10)  # Restaurado
        self.assertEqual(mov_orig.estado, Movimiento.Estado.REVERTIDO)
        self.assertEqual(mov_rev.tipo, Movimiento.Tipo.REVERSION)
        self.assertEqual(mov_rev.movimiento_original, mov_orig)

    def test_revertir_entrada_rechaza_si_provoca_stock_negativo(self):
        """Rechaza revertir una entrada si el stock actual es menor a la cantidad a revertir."""
        # Se ingresan 5 más (total 15)
        _, mov_entrada = registrar_entrada(
            producto=self.producto,
            almacen=self.almacen,
            cantidad=5,
            usuario=self.operador,
            clave_idempotencia="ENT-REV-TEST",
        )
        # Luego se retiran 12 (quedan 3)
        registrar_salida(
            producto=self.producto,
            almacen=self.almacen,
            cantidad=12,
            motivo="Consumo de stock",
            destinatario="Taller",
            usuario=self.operador,
            clave_idempotencia="SAL-CONSUMO",
        )
        # Intentar revertir la entrada de 5 cuando solo quedan 3 disponibles debe fallar
        with self.assertRaises(ValidationError) as ctx:
            revertir_movimiento(
                movimiento=mov_entrada,
                usuario=self.admin,
                motivo="Error en remisión de entrada",
            )
        self.assertIn("stock negativo", str(ctx.exception).lower())

    def test_confirmar_conteo_recupera_existencia_faltante_o_inconsistente(self):
        """Si un conteo físico histórico no tiene Existencia para su almacén, se alinea al almacén de la categoría sin lanzar DoesNotExist."""
        almacen_secundario = Almacen.objects.create(nombre="Almacén Secundario", activo=True)
        # Conteo con almacén diferente antes de la alineación
        conteo = ConteoFisico.objects.create(
            producto=self.producto,
            almacen=almacen_secundario,
            cantidad_contada=14,
            stock_sistema=10,
            diferencia=4,
            usuario_conteo=self.operador,
            confirmado=False,
        )

        conteo_conf, mov = confirmar_conteo_fisico(
            conteo=conteo,
            usuario=self.admin,
        )
        self.assertTrue(conteo_conf.confirmado)
        self.assertEqual(conteo_conf.almacen, self.almacen)
        ex = Existencia.objects.get(producto=self.producto, almacen=self.almacen)
        self.assertEqual(ex.stock_disponible, 14)
        self.assertEqual(mov.tipo, Movimiento.Tipo.AJUSTE_CONTEO)

    def test_registrar_conteo_rechaza_almacen_incompatible(self):
        """Registrar un conteo para un almacén distinto al de la categoría del producto es rechazado."""
        almacen_otro = Almacen.objects.create(nombre="Almacén Otro", activo=True)
        with self.assertRaises(ValidationError) as ctx:
            registrar_conteo_fisico(
                producto=self.producto,
                almacen=almacen_otro,
                cantidad_contada=10,
                usuario=self.admin,
            )
        self.assertIn("categoría", str(ctx.exception).lower())

