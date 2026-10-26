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

    def test_vulnerabilidad_traslado_implicito_bloqueado(self):
        """
        VULNERABILIDAD RESUELTA:
        Un usuario con permiso de editar catálogo pero SIN permiso de trasladar almacén
        NO debe poder cambiar la categoría del producto a una categoría de otro almacén.
        """
        # Conceder editar catálogo pero NO trasladar almacén
        p_editar = PermisoAudacIA.objects.get(codigo="catalogo.editar")
        self.user_op.permisos_personalizados.add(p_editar)

        # Intento de trasladar de Almacén A a Almacén B mediante actualizar_producto
        with self.assertRaises(PermissionDenied) as ctx:
            actualizar_producto(
                producto=self.prod,
                nombre=self.prod.nombre,
                categoria=self.cat_b,  # Perteneciente a Almacén B
                tipo=None,
                stock_minimo=2,
                codigo_barras="",
                nombre_empaque="",
                unidades_por_empaque=None,
                descripcion="",
                ubicaciones=[self.ub_b1.id],
                usuario=self.user_op,
            )
        self.assertIn("trasladar productos", str(ctx.exception))

    def test_vulnerabilidad_reubicacion_interna_bloqueada(self):
        """
        Un usuario con permiso de editar catálogo pero SIN catalogo.reubicar_interno
        NO debe poder modificar las ubicaciones físicas en los estantes.
        """
        p_editar = PermisoAudacIA.objects.get(codigo="catalogo.editar")
        self.user_op.permisos_personalizados.add(p_editar)

        with self.assertRaises(PermissionDenied) as ctx:
            actualizar_producto(
                producto=self.prod,
                nombre=self.prod.nombre,
                categoria=self.cat_a,
                tipo=None,
                stock_minimo=2,
                codigo_barras="",
                nombre_empaque="",
                unidades_por_empaque=None,
                descripcion="",
                ubicaciones=[self.ub_a2.id],  # Cambio de estante interno sin permiso
                usuario=self.user_op,
            )
        self.assertIn("reubicar productos", str(ctx.exception))

    def test_traslado_permitido_para_administrador(self):
        """El administrador sí puede realizar traslados y cambiar categorías de almacén."""
        producto_actualizado = actualizar_producto(
            producto=self.prod,
            nombre=self.prod.nombre,
            categoria=self.cat_b,
            tipo=None,
            stock_minimo=2,
            codigo_barras="",
            nombre_empaque="",
            unidades_por_empaque=None,
            descripcion="",
            ubicaciones=[self.ub_b1.id],
            usuario=self.admin,
        )
        self.assertEqual(producto_actualizado.categoria.almacen_principal, self.almacen_b)
