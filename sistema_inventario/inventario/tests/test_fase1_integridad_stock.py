"""Pruebas de verificación para la Fase 1: Integridad Transaccional y Consistencia de Stock."""

import concurrent.futures
from io import StringIO
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import connection, IntegrityError
from django.test import TransactionTestCase

from cuentas.models import UsuarioAudacIA
from inventario.db.transacciones import transaccion_con_reintento
from inventario.models import Almacen, Categoria, Existencia, Movimiento, Prestamo, Producto, SecuenciaCodigo
from inventario.servicios.excepciones import InconsistenciaInventario, StockInsuficiente
from inventario.servicios.operaciones import (
    confirmar_conteo_fisico,
    registrar_baja_prestamo,
    registrar_conteo_fisico,
    registrar_entrada,
    registrar_prestamo,
    registrar_salida,
    revertir_movimiento,
)

User = get_user_model()


class Fase1IntegridadStockTest(TransactionTestCase):
    """Pruebas funcionales y de concurrencia de las protecciones de la Fase 1."""

    def setUp(self):
        self.almacen = Almacen.objects.create(nombre="Almacen Fase 1", activo=True)
        self.admin = User.objects.create_user(
            username="admin_fase1",
            password="Password123",
            nombre_completo="Admin Fase 1",
            rol=UsuarioAudacIA.Rol.ADMINISTRADOR,
        )
        self.cat = Categoria.objects.create(
            nombre="Robótica Experimental",
            almacen_principal=self.almacen,
        )
        self.producto = Producto.objects.create(
            nombre="Brazo Robótico 6DOF",
            categoria=self.cat,
            stock_minimo=2,
        )

    def test_secuencia_codigo_prefijo_atomico_concurrente(self):
        """Creación concurrente de productos con el mismo prefijo genera códigos secuenciales únicos sin colisión."""
        nombres = [f"Actuador Serie {i}" for i in range(8)]
        productos_creados = []
        errores = []

        @transaccion_con_reintento(
            max_intentos=6,
            tiempos_espera=(0.05, 0.10, 0.20, 0.35, 0.50, 0.80),
        )
        def _crear_producto_reintentable(nombre):
            return Producto.objects.create(
                nombre=nombre,
                categoria=self.cat,
                stock_minimo=1,
            )

        def crear_producto(nombre):
            connection.close()
            try:
                p = _crear_producto_reintentable(nombre)
                return p.codigo_interno
            except Exception as e:
                return str(e)
            finally:
                connection.close()

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            futuros = [executor.submit(crear_producto, nom) for nom in nombres]
            for f in concurrent.futures.as_completed(futuros):
                res = f.result()
                if res.startswith("ROB-"):
                    productos_creados.append(res)
                else:
                    errores.append(res)

        self.assertEqual(len(errores), 0, f"Errores en creación concurrente: {errores}")
        self.assertEqual(len(productos_creados), 8)
        # Todos los códigos deben ser únicos
        self.assertEqual(len(set(productos_creados)), 8)

        # Verificar que SecuenciaCodigo se actualizó al número máximo creado
        sec = SecuenciaCodigo.objects.get(prefijo="ROB")
        self.assertGreaterEqual(sec.ultimo_valor, 8)

    def test_reversion_ajuste_conteo_delta_relativa(self):
        """Revertir un ajuste de conteo físico debe ser delta-relativo y preservar operaciones intermedias (§1.C)."""
        # 1. Ingresar 10 unidades iniciales
        registrar_entrada(
            producto=self.producto,
            almacen=self.almacen,
            cantidad=10,
            usuario=self.admin,
            clave_idempotencia="ENTRADA-INICIAL-F1",
        )
        ex = Existencia.objects.get(producto=self.producto, almacen=self.almacen)
        self.assertEqual(ex.stock_disponible, 10)

        # 2. Registrar y confirmar un conteo de 15 unidades (delta = +5)
        conteo = registrar_conteo_fisico(
            producto=self.producto,
            almacen=self.almacen,
            cantidad_contada=15,
            usuario=self.admin,
            motivo="Conteo físico mensual",
        )
        conteo_conf, mov_ajuste = confirmar_conteo_fisico(
            conteo=conteo,
            usuario=self.admin,
        )
        ex.refresh_from_db()
        self.assertEqual(ex.stock_disponible, 15)

        # 3. Operación legítima intermedia: salida de 3 unidades
        registrar_salida(
            producto=self.producto,
            almacen=self.almacen,
            cantidad=3,
            motivo="Uso en laboratorio",
            destinatario="Estudiante Investigador",
            usuario=self.admin,
            clave_idempotencia="SALIDA-INTERMEDIA-F1",
        )
        ex.refresh_from_db()
        self.assertEqual(ex.stock_disponible, 12)

        # 4. Revertir el ajuste de conteo:
        # En la lógica anterior sobreescribiría ciegamente a stock_anterior = 10 (perdiendo la salida de 3).
        # En la lógica delta-relativa, el ajuste original sumó +5, por lo que revertir resta 5 del stock actual (12 - 5 = 7).
        mov_orig, mov_rev = revertir_movimiento(
            movimiento=mov_ajuste,
            usuario=self.admin,
            motivo="Error en digitación de conteo físico",
        )

        ex.refresh_from_db()
        self.assertEqual(ex.stock_disponible, 7)
        self.assertEqual(mov_rev.stock_disponible_anterior, 12)
        self.assertEqual(mov_rev.stock_disponible_posterior, 7)

    def test_reversion_ajuste_falla_si_consumo_provoca_stock_negativo(self):
        """Si el stock sumado por un conteo fue consumido, la reversión falla para no dejar stock negativo."""
        registrar_entrada(
            producto=self.producto,
            almacen=self.almacen,
            cantidad=5,
            usuario=self.admin,
            clave_idempotencia="ENTRADA-INI-F1-NEG",
        )

        # Ajuste a 10 unidades (delta = +5)
        conteo = registrar_conteo_fisico(
            producto=self.producto,
            almacen=self.almacen,
            cantidad_contada=10,
            usuario=self.admin,
        )
        _, mov_ajuste = confirmar_conteo_fisico(
            conteo=conteo,
            usuario=self.admin,
        )

        # Consumir 8 unidades -> quedan 2 disponibles
        registrar_salida(
            producto=self.producto,
            almacen=self.almacen,
            cantidad=8,
            motivo="Consumo masivo",
            destinatario="Proyecto Grado",
            usuario=self.admin,
            clave_idempotencia="SAL-8-UNID",
        )
        ex = Existencia.objects.get(producto=self.producto, almacen=self.almacen)
        self.assertEqual(ex.stock_disponible, 2)

        # Intentar revertir el conteo requeriría restar 5, pero solo hay 2 disponibles
        with self.assertRaises(ValidationError):
            revertir_movimiento(
                movimiento=mov_ajuste,
                usuario=self.admin,
                motivo="Intento de reversión que causaría negativo",
            )

        # El stock debe permanecer intacto en 2
        ex.refresh_from_db()
        self.assertEqual(ex.stock_disponible, 2)

    def test_dar_baja_prestamo_consistencia_estricta(self):
        """Baja por daño/pérdida de préstamo no debe permitir montos superiores a stock_en_prestamo (§1.E)."""
        registrar_entrada(
            producto=self.producto,
            almacen=self.almacen,
            cantidad=10,
            usuario=self.admin,
            clave_idempotencia="ENTRADA-PREST-BAJA",
        )

        prestamo, mov, _ = registrar_prestamo(
            producto=self.producto,
            almacen=self.almacen,
            cantidad=4,
            destinatario="Laboratorio Robótica",
            usuario=self.admin,
            clave_idempotencia="PREST-4-UNID",
        )
        ex = Existencia.objects.get(producto=self.producto, almacen=self.almacen)
        self.assertEqual(ex.stock_disponible, 6)
        self.assertEqual(ex.stock_en_prestamo, 4)

        # Dar de baja 1 unidad por rotura
        prestamo, baja, mov_baja = registrar_baja_prestamo(
            prestamo=prestamo,
            cantidad=1,
            usuario=self.admin,
            motivo="Pieza rota durante ensayo",
        )
        ex.refresh_from_db()
        self.assertEqual(ex.stock_disponible, 6)  # No se añade a disponible
        self.assertEqual(ex.stock_en_prestamo, 3)  # Se reduce préstamo
        self.assertEqual(prestamo.cantidad_pendiente_devolucion, 3)

        # Intentar dar de baja 5 unidades (supera los 3 pendientes) debe fallar
        with self.assertRaises(ValidationError):
            registrar_baja_prestamo(
                prestamo=prestamo,
                cantidad=5,
                usuario=self.admin,
                motivo="Exceso de baja",
            )

    def test_check_constraints_base_de_datos(self):
        """Las restricciones CheckConstraint de base de datos impiden estados inválidos a nivel motor (§1.D)."""
        ex = Existencia.objects.get_or_create(
            producto=self.producto,
            almacen=self.almacen,
            defaults={"stock_disponible": 5, "stock_en_prestamo": 0},
        )[0]

        # 1. Intento de stock disponible negativo por UPDATE directo
        with self.assertRaises(IntegrityError):
            Existencia.objects.filter(id=ex.id).update(stock_disponible=-5)

        # 2. Intento de stock en préstamo negativo por UPDATE directo
        with self.assertRaises(IntegrityError):
            Existencia.objects.filter(id=ex.id).update(stock_en_prestamo=-2)

    def test_idempotencia_deduplicacion(self):
        """Operaciones con la misma clave de idempotencia no duplican stock (§1.B)."""
        clave = "IDEMP-TEST-F1"
        _, m1 = registrar_entrada(
            producto=self.producto,
            almacen=self.almacen,
            cantidad=7,
            usuario=self.admin,
            clave_idempotencia=clave,
        )

        _, m2 = registrar_entrada(
            producto=self.producto,
            almacen=self.almacen,
            cantidad=7,
            usuario=self.admin,
            clave_idempotencia=clave,
        )

        self.assertEqual(m1.id, m2.id)
        ex = Existencia.objects.get(producto=self.producto, almacen=self.almacen)
        self.assertEqual(ex.stock_disponible, 7)

    def test_comando_verificar_integridad(self):
        """El comando de verificación detecta anomalías y confirma integridad completa (§1.F)."""
        # Ejecución normal: debe pasar exitosamente
        out = StringIO()
        call_command("verificar_integridad_inventario", detallado=True, stdout=out)
        salida = out.getvalue()
        self.assertIn("INTEGRIDAD COMPLETA", salida)
