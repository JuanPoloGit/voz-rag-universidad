"""
Pruebas de seguridad y cumplimiento de permisos operativos y vulnerabilidad de traslados.
"""

from django.test import TestCase
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied

from cuentas.models import PermisoAudacIA
from inventario.models import Almacen, Categoria, Ubicacion, Producto, Existencia
from inventario.servicios.operaciones import (
    registrar_entrada,
    registrar_salida,
    registrar_prestamo,
    registrar_devolucion_prestamo,
    registrar_baja_prestamo,
    registrar_traslado_entre_almacenes,
    registrar_traslado_interno,
)
from inventario.servicios.datos_maestros import (
    crear_producto,
    actualizar_producto,
)

User = get_user_model()


class PermisosOperativosTest(TestCase):
    def setUp(self):
        self.almacen_a = Almacen.objects.create(nombre="Almacén A", activo=True)
        self.almacen_b = Almacen.objects.create(nombre="Almacén B", activo=True)

        self.admin = User.objects.create_user(
            username="admin_op",
            nombre_completo="Admin Op",
            password="Password123",
            rol=User.Rol.ADMINISTRADOR,
        )
        self.user_op = User.objects.create_user(
            username="user_op",
            nombre_completo="User Op",
            password="Password123",
            rol=User.Rol.USUARIO,
            almacen_asignado=self.almacen_a,
        )
        self.user_op.almacenes_asignados.add(self.almacen_a, self.almacen_b)

        # Limpiar permisos automáticos para probar granularidad exacta
        self.user_op.permisos_personalizados.clear()

        self.cat_a = Categoria.objects.create(nombre="Cat A", almacen_principal=self.almacen_a)
        self.cat_b = Categoria.objects.create(nombre="Cat B", almacen_principal=self.almacen_b)

        self.ub_a1 = Ubicacion.objects.create(nombre="Estante A1", almacen=self.almacen_a)
        self.ub_a2 = Ubicacion.objects.create(nombre="Estante A2", almacen=self.almacen_a)
        self.ub_b1 = Ubicacion.objects.create(nombre="Estante B1", almacen=self.almacen_b)

        self.prod = Producto.objects.create(
            nombre="Osciloscopio Digital",
            categoria=self.cat_a,
            stock_minimo=2,
        )
        self.prod.ubicaciones.add(self.ub_a1)

    def test_entrada_denegada_sin_permiso(self):
        """No se puede registrar entrada si no se tiene el permiso movimientos.entrada."""
        with self.assertRaises(PermissionDenied):
            registrar_entrada(
                producto=self.prod,
                almacen=self.almacen_a,
                cantidad=5,
                usuario=self.user_op,
                clave_idempotencia="TEST-ENT-1",
            )

    def test_entrada_permitida_con_permiso(self):
        """Se puede registrar entrada una vez concedido movimientos.entrada."""
        p_entrada = PermisoAudacIA.objects.get(codigo="movimientos.entrada")
        self.user_op.permisos_personalizados.add(p_entrada)

        res = registrar_entrada(
            producto=self.prod,
            almacen=self.almacen_a,
            cantidad=5,
            usuario=self.user_op,
            clave_idempotencia="TEST-ENT-2",
        )
        mov = res[0] if isinstance(res, (tuple, list)) else res
        self.assertIsNotNone(mov.id)
        existencia = Existencia.objects.get(producto=self.prod, almacen=self.almacen_a)
        self.assertEqual(existencia.stock_disponible, 5)

    def test_dotacion_denegada_sin_permiso(self):
        """No se puede registrar dotación si falta el permiso movimientos.dotacion."""
        # Agregar stock primero con admin
        registrar_entrada(
            producto=self.prod,
            almacen=self.almacen_a,
            cantidad=10,
            usuario=self.admin,
            clave_idempotencia="TEST-ENT-ADMIN",
        )

        with self.assertRaises(PermissionDenied):
            registrar_salida(
                producto=self.prod,
                almacen=self.almacen_a,
                cantidad=2,
                motivo="DOTACION",
                destinatario="Laboratorio",
                usuario=self.user_op,
                clave_idempotencia="TEST-SAL-1",
            )

    def test_prestamo_denegado_sin_permiso(self):
        """No se puede registrar préstamo si falta el permiso movimientos.prestamo."""
        registrar_entrada(
            producto=self.prod,
            almacen=self.almacen_a,
            cantidad=10,
            usuario=self.admin,
            clave_idempotencia="TEST-ENT-ADMIN-2",
        )

        with self.assertRaises(PermissionDenied):
            registrar_prestamo(
                producto=self.prod,
                almacen=self.almacen_a,
                cantidad=2,
                destinatario="Profesor X",
                motivo="Clase",
                usuario=self.user_op,
                clave_idempotencia="TEST-PRES-1",
            )

    def test_traslado_entre_almacenes_denegado_sin_permiso(self):
        """No se puede trasladar entre almacenes sin movimientos.traslado_almacen."""
        registrar_entrada(
            producto=self.prod,
            almacen=self.almacen_a,
            cantidad=10,
            usuario=self.admin,
            clave_idempotencia="TEST-ENT-ADMIN-3",
        )
        with self.assertRaises(PermissionDenied):
            registrar_traslado_entre_almacenes(
                producto=self.prod,
                almacen_origen=self.almacen_a,
                almacen_destino=self.almacen_b,
                cantidad=2,
                usuario=self.user_op,
            )

    def test_traslado_entre_almacenes_permitido_con_permiso(self):
        """Se puede trasladar entre almacenes si se concede movimientos.traslado_almacen."""
        registrar_entrada(
            producto=self.prod,
            almacen=self.almacen_a,
            cantidad=10,
            usuario=self.admin,
            clave_idempotencia="TEST-ENT-ADMIN-4",
        )
        p_tras = PermisoAudacIA.objects.get(codigo="movimientos.traslado_almacen")
        self.user_op.permisos_personalizados.add(p_tras)

        ex_orig, ex_dest, mov = registrar_traslado_entre_almacenes(
            producto=self.prod,
            almacen_origen=self.almacen_a,
            almacen_destino=self.almacen_b,
            cantidad=3,
            usuario=self.user_op,
        )
        self.assertEqual(ex_orig.stock_disponible, 7)
        self.assertEqual(ex_dest.stock_disponible, 3)

    def test_traslado_interno_denegado_sin_permiso(self):
        """No se puede realizar traslado interno de ubicación sin movimientos.traslado_interno."""
        registrar_entrada(
            producto=self.prod,
            almacen=self.almacen_a,
            cantidad=10,
            usuario=self.admin,
            clave_idempotencia="TEST-ENT-ADMIN-5",
        )
        with self.assertRaises(PermissionDenied):
            registrar_traslado_interno(
                producto=self.prod,
                almacen=self.almacen_a,
                ubicacion_origen=self.ub_a1,
                ubicacion_destino=self.ub_a2,
                usuario=self.user_op,
            )

    def test_traslado_interno_permitido_con_permiso(self):
        """Se puede realizar traslado interno si se concede movimientos.traslado_interno."""
        registrar_entrada(
            producto=self.prod,
            almacen=self.almacen_a,
            cantidad=10,
            usuario=self.admin,
            clave_idempotencia="TEST-ENT-ADMIN-6",
        )
        p_int = PermisoAudacIA.objects.get(codigo="movimientos.traslado_interno")
        self.user_op.permisos_personalizados.add(p_int)

        ex, mov = registrar_traslado_interno(
            producto=self.prod,
            almacen=self.almacen_a,
            ubicacion_origen=self.ub_a1,
            ubicacion_destino=self.ub_a2,
            usuario=self.user_op,
        )
        self.prod.refresh_from_db()
        self.assertEqual(self.prod.ubicaciones.first(), self.ub_a2)
