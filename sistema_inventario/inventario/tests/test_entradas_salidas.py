"""Pruebas unitarias para los servicios de Entradas y Salidas de inventario."""

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.contrib.auth import get_user_model

from cuentas.models import UsuarioAudacIA
from inventario.models import Almacen, Categoria, Existencia, Movimiento, Producto, Ubicacion
from inventario.servicios.operaciones import registrar_entrada, registrar_salida

User = get_user_model()


class EntradasSalidasTest(TestCase):
    def setUp(self):
        self.almacen_a = Almacen.objects.create(nombre="Almacén Central", activo=True)
        self.almacen_b = Almacen.objects.create(nombre="Almacén Remoto", activo=True)

        self.ubicacion_a = Ubicacion.objects.create(
            nombre="Estante A-1",
            almacen=self.almacen_a,
        )

        self.admin = User.objects.create_user(
            username="admin",
            password="Password123",
            rol=UsuarioAudacIA.Rol.ADMINISTRADOR,
            nombre_completo="Admin Test",
        )
        self.user_a = User.objects.create_user(
            username="user_a",
            password="Password123",
            rol=UsuarioAudacIA.Rol.USUARIO,
            almacen_asignado=self.almacen_a,
            nombre_completo="Operador A",
        )

        self.categoria = Categoria.objects.create(
            nombre="Cables y Conexiones",
            almacen_principal=self.almacen_a,
        )

        self.producto = Producto.objects.create(
            nombre="Cable Patch Cord Cat6 1m",
            categoria=self.categoria,
            stock_minimo=5,
            nombre_empaque="Caja",
            unidades_por_empaque=10,
        )

    def test_registrar_entrada_unidades_base(self):
        """Registra una entrada de unidades directas."""
        existencia, mov = registrar_entrada(
            producto=self.producto,
            almacen=self.almacen_a,
            cantidad=15,
            usuario=self.user_a,
            ubicacion=self.ubicacion_a,
            motivo="Recepción de stock",
            clave_idempotencia="ENTRADA-TEST-001",
        )
        self.assertEqual(existencia.stock_disponible, 15)
        self.assertEqual(mov.tipo, Movimiento.Tipo.ENTRADA)
        self.assertEqual(mov.cantidad, 15)
        self.assertEqual(mov.stock_disponible_anterior, 0)
        self.assertEqual(mov.stock_disponible_posterior, 15)
        self.assertTrue(self.producto.ubicaciones.filter(id=self.ubicacion_a.id).exists())

    def test_registrar_entrada_con_empaque(self):
        """Conversión automática de empaque a unidades base (§8.5)."""
        existencia, mov = registrar_entrada(
            producto=self.producto,
            almacen=self.almacen_a,
            cantidad=0,
            es_empaque=True,
            cantidad_empaques=3,  # 3 cajas * 10 = 30 unidades
            usuario=self.user_a,
            motivo="Compra de cajas",
            clave_idempotencia="ENTRADA-EMPAQUE-001",
        )
        self.assertEqual(existencia.stock_disponible, 30)
        self.assertEqual(mov.cantidad, 30)
        self.assertIn("3 Caja(s)", mov.observacion)
        self.assertIn("30 unidades base", mov.observacion)

    def test_registrar_entrada_idempotencia(self):
        """Reintentar con la misma clave de idempotencia no duplica stock."""
        ex1, mov1 = registrar_entrada(
            producto=self.producto,
            almacen=self.almacen_a,
            cantidad=10,
            usuario=self.user_a,
            clave_idempotencia="IDEM-ENTRADA-KEY",
        )
        ex2, mov2 = registrar_entrada(
            producto=self.producto,
            almacen=self.almacen_a,
            cantidad=10,
            usuario=self.user_a,
            clave_idempotencia="IDEM-ENTRADA-KEY",
        )
        self.assertEqual(mov1.id, mov2.id)
        self.assertEqual(ex2.stock_disponible, 10)

    def test_registrar_salida_exitosa_y_alerta_bajo_stock(self):
        """Salida válida que reduce stock y dispara alerta de bajo stock."""
        registrar_entrada(
            producto=self.producto,
            almacen=self.almacen_a,
            cantidad=8,
            usuario=self.user_a,
            clave_idempotencia="ENT-PREVIA",
        )

        # Retiramos 5 unidades -> quedan 3 (stock_minimo es 5 -> BAJO STOCK)
        existencia, mov, alerta = registrar_salida(
            producto=self.producto,
            almacen=self.almacen_a,
            cantidad=5,
            motivo="Instalación en laboratorio",
            destinatario="Profesor Carlos Pérez",
            usuario=self.user_a,
            clave_idempotencia="SAL-TEST-001",
        )
        self.assertEqual(existencia.stock_disponible, 3)
        self.assertEqual(mov.tipo, Movimiento.Tipo.SALIDA)
        self.assertEqual(mov.destinatario, "Profesor Carlos Pérez")
        self.assertEqual(mov.stock_disponible_anterior, 8)
        self.assertEqual(mov.stock_disponible_posterior, 3)
        self.assertEqual(alerta["nivel"], "BAJO")

    def test_registrar_salida_alerta_critica(self):
        """Salida que agota el stock dispara alerta crítica (CRITICO)."""
        registrar_entrada(
            producto=self.producto,
            almacen=self.almacen_a,
            cantidad=4,
            usuario=self.user_a,
            clave_idempotencia="ENT-PREVIA-2",
        )

        existencia, mov, alerta = registrar_salida(
            producto=self.producto,
            almacen=self.almacen_a,
            cantidad=4,
            motivo="Uso total",
            destinatario="Ing. Roberto",
            usuario=self.user_a,
            clave_idempotencia="SAL-CRITICA-001",
        )
        self.assertEqual(existencia.stock_disponible, 0)
        self.assertEqual(alerta["nivel"], "CRITICO")

    def test_registrar_salida_rechaza_stock_insuficiente(self):
        """No permite stock negativo bajo ninguna circunstancia."""
        registrar_entrada(
            producto=self.producto,
            almacen=self.almacen_a,
            cantidad=2,
            usuario=self.user_a,
            clave_idempotencia="ENT-PREVIA-3",
        )

        with self.assertRaises(ValidationError) as ctx:
            registrar_salida(
                producto=self.producto,
                almacen=self.almacen_a,
                cantidad=5,
                motivo="Prueba exceso",
                destinatario="Alguien",
                usuario=self.user_a,
            )
        self.assertIn("Stock insuficiente", str(ctx.exception))

    def test_registrar_salida_exige_motivo_y_destinatario(self):
        """Motivo y destinatario son obligatorios en salidas (§9.3)."""
        registrar_entrada(
            producto=self.producto,
            almacen=self.almacen_a,
            cantidad=10,
            usuario=self.user_a,
            clave_idempotencia="ENT-PREVIA-4",
        )

        with self.assertRaises(ValidationError):
            registrar_salida(
                producto=self.producto,
                almacen=self.almacen_a,
                cantidad=1,
                motivo="",
                destinatario="Destinatario",
                usuario=self.user_a,
            )

        with self.assertRaises(ValidationError):
            registrar_salida(
                producto=self.producto,
                almacen=self.almacen_a,
                cantidad=1,
                motivo="Motivo válido",
                destinatario="",
                usuario=self.user_a,
            )

    def test_usuario_normal_no_puede_operar_otro_almacen(self):
        """Un usuario normal solo puede registrar operaciones en su almacén asignado."""
        with self.assertRaises(PermissionDenied):
            registrar_entrada(
                producto=self.producto,
                almacen=self.almacen_b,
                cantidad=5,
                usuario=self.user_a,
            )
