"""Pruebas unitarias para el ciclo de vida de préstamos, devoluciones parciales y bajas."""

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.contrib.auth import get_user_model

from cuentas.models import UsuarioAudacIA
from inventario.models import Almacen, Categoria, Existencia, Movimiento, Prestamo, DevolucionPrestamo, Producto
from inventario.servicios.operaciones import (
    registrar_entrada,
    registrar_prestamo,
    registrar_devolucion_prestamo,
    registrar_baja_prestamo,
)

User = get_user_model()


class PrestamosTest(TestCase):
    def setUp(self):
        self.almacen = Almacen.objects.create(nombre="Almacén de Herramientas", activo=True)
        self.user = User.objects.create_user(
            username="operador",
            password="Password123",
            nombre_completo="Operador Test",
            rol=UsuarioAudacIA.Rol.USUARIO,
            almacen_asignado=self.almacen,
        )
        self.categoria = Categoria.objects.create(
            nombre="Equipos de Medición",
            almacen_principal=self.almacen,
        )
        self.producto = Producto.objects.create(
            nombre="Multímetro Digital Fluke",
            categoria=self.categoria,
            stock_minimo=2,
        )
        # Ingresamos 10 multímetros al almacén
        registrar_entrada(
            producto=self.producto,
            almacen=self.almacen,
            cantidad=10,
            usuario=self.user,
            clave_idempotencia="STOCK-INI-TEST",
        )

    def test_registrar_prestamo_exitoso(self):
        """Registrar un préstamo descuenta disponible y suma a en_prestamo."""
        prestamo, mov, alerta = registrar_prestamo(
            producto=self.producto,
            almacen=self.almacen,
            cantidad=4,
            destinatario="Estudiante Ana Gómez",
            usuario=self.user,
            motivo="Práctica de circuitos",
            clave_idempotencia="PRES-001",
        )
        ex = Existencia.objects.get(producto=self.producto, almacen=self.almacen)
        self.assertEqual(ex.stock_disponible, 6)
        self.assertEqual(ex.stock_en_prestamo, 4)
        self.assertEqual(prestamo.cantidad_original, 4)
        self.assertEqual(prestamo.cantidad_pendiente_devolucion, 4)
        self.assertEqual(prestamo.estado, Prestamo.Estado.ABIERTO)
        self.assertEqual(mov.tipo, Movimiento.Tipo.PRESTAMO)
        self.assertEqual(mov.destinatario, "Estudiante Ana Gómez")

    def test_prestamo_rechaza_stock_insuficiente(self):
        """No se puede prestar más del stock disponible."""
        with self.assertRaises(ValidationError) as ctx:
            registrar_prestamo(
                producto=self.producto,
                almacen=self.almacen,
                cantidad=15,  # Solo hay 10
                destinatario="Persona X",
                usuario=self.user,
            )
        self.assertIn("Stock insuficiente", str(ctx.exception))

    def test_devolucion_parcial_y_cierre_total(self):
        """Devolución parcial deja saldo y DEVUELTO_PARCIAL; la siguiente cierra."""
        prestamo, _, _ = registrar_prestamo(
            producto=self.producto,
            almacen=self.almacen,
            cantidad=5,
            destinatario="Técnico Luis",
            usuario=self.user,
        )
        # 1. Devolución parcial de 2 unidades
        prestamo, dev1, mov1 = registrar_devolucion_prestamo(
            prestamo=prestamo,
            cantidad=2,
            usuario=self.user,
            motivo="Entrega parcial de 2 equipos",
        )
        ex = Existencia.objects.get(producto=self.producto, almacen=self.almacen)
        self.assertEqual(ex.stock_disponible, 7)
        self.assertEqual(ex.stock_en_prestamo, 3)
        self.assertEqual(prestamo.cantidad_pendiente_devolucion, 3)
        self.assertEqual(prestamo.estado, Prestamo.Estado.DEVUELTO_PARCIAL)
        self.assertEqual(dev1.tipo, DevolucionPrestamo.Tipo.DEVOLUCION)
        self.assertEqual(mov1.tipo, Movimiento.Tipo.DEVOLUCION)

        # 2. Devolución restante de 3 unidades -> CERRADO
        prestamo, dev2, mov2 = registrar_devolucion_prestamo(
            prestamo=prestamo,
            cantidad=3,
            usuario=self.user,
            motivo="Entrega final de los 3 restantes",
        )
        ex.refresh_from_db()
        self.assertEqual(ex.stock_disponible, 10)
        self.assertEqual(ex.stock_en_prestamo, 0)
        self.assertEqual(prestamo.cantidad_pendiente_devolucion, 0)
        self.assertEqual(prestamo.estado, Prestamo.Estado.CERRADO)
        self.assertIsNotNone(prestamo.fecha_cierre)

    def test_baja_por_dano_no_suma_a_stock_disponible(self):
        """Baja por daño/pérdida reduce stock_en_prestamo y saldo pero NO suma a disponible (§9.4)."""
        prestamo, _, _ = registrar_prestamo(
            producto=self.producto,
            almacen=self.almacen,
            cantidad=4,
            destinatario="Laboratorista Mario",
            usuario=self.user,
        )
        # De las 4 unidades, 1 unidad se rompió y no puede recuperarse
        prestamo, baja, mov = registrar_baja_prestamo(
            prestamo=prestamo,
            cantidad=1,
            usuario=self.user,
            motivo="Pantalla rota irreparablemente durante prueba",
        )
        ex = Existencia.objects.get(producto=self.producto, almacen=self.almacen)
        # Disponible debe seguir en 6 (NO debe haber sumado 1)
        self.assertEqual(ex.stock_disponible, 6)
        # En préstamo debe reducirse de 4 a 3
        self.assertEqual(ex.stock_en_prestamo, 3)
        self.assertEqual(prestamo.cantidad_pendiente_devolucion, 3)
        self.assertEqual(prestamo.estado, Prestamo.Estado.DEVUELTO_PARCIAL)
        self.assertEqual(baja.tipo, DevolucionPrestamo.Tipo.BAJA_DANO)
        self.assertEqual(mov.tipo, Movimiento.Tipo.BAJA_PRESTAMO)

    def test_devolucion_rechaza_exceso_de_cantidad(self):
        """Rechaza devolver más unidades de las pendientes."""
        prestamo, _, _ = registrar_prestamo(
            producto=self.producto,
            almacen=self.almacen,
            cantidad=3,
            destinatario="Estudiante",
            usuario=self.user,
        )
        with self.assertRaises(ValidationError) as ctx:
            registrar_devolucion_prestamo(
                prestamo=prestamo,
                cantidad=5,
                usuario=self.user,
                motivo="Devolución errónea",
            )
        self.assertIn("supera la cantidad pendiente", str(ctx.exception))
