"""Pruebas unitarias para traslados internos y entre almacenes."""

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.contrib.auth import get_user_model

from cuentas.models import UsuarioAudacIA, PermisoAudacIA
from inventario.models import Almacen, Categoria, Existencia, Movimiento, Producto, Ubicacion, ProductoUbicacion
from inventario.servicios.operaciones import (
    registrar_entrada,
    registrar_traslado_interno,
    registrar_traslado_entre_almacenes,
)

User = get_user_model()


class TrasladosTest(TestCase):
    def setUp(self):
        self.almacen_origen = Almacen.objects.create(nombre="Almacén Central", activo=True)
        self.almacen_destino = Almacen.objects.create(nombre="Almacén Periférico", activo=True)

        self.ub_estante_1 = Ubicacion.objects.create(nombre="Estante 1", almacen=self.almacen_origen)
        self.ub_estante_2 = Ubicacion.objects.create(nombre="Estante 2", almacen=self.almacen_origen)
        self.ub_destino = Ubicacion.objects.create(nombre="Zona Recepción", almacen=self.almacen_destino)

        self.user_origen = User.objects.create_user(
            username="user_origen",
            password="Password123",
            nombre_completo="Operador Origen",
            rol=UsuarioAudacIA.Rol.USUARIO,
            almacen_asignado=self.almacen_origen,
        )
        p_traslado_int = PermisoAudacIA.objects.get(codigo="movimientos.traslado_interno")
        p_traslado_alm = PermisoAudacIA.objects.get(codigo="movimientos.traslado_almacen")
        self.user_origen.permisos_personalizados.add(p_traslado_int, p_traslado_alm)
        self.user_ajeno = User.objects.create_user(
            username="user_ajeno",
            password="Password123",
            nombre_completo="Operador Destino",
            rol=UsuarioAudacIA.Rol.USUARIO,
            almacen_asignado=self.almacen_destino,
        )
        self.user_ajeno.permisos_personalizados.add(p_traslado_alm)

        self.categoria = Categoria.objects.create(
            nombre="Cables y Red",
            almacen_principal=self.almacen_origen,
        )
        self.producto = Producto.objects.create(
            nombre="Bobina UTP Cat6 305m",
            categoria=self.categoria,
            stock_minimo=1,
        )

        # 5 bobinas en almacén origen, asignadas a ub_estante_1
        registrar_entrada(
            producto=self.producto,
            almacen=self.almacen_origen,
            cantidad=5,
            usuario=self.user_origen,
            ubicacion=self.ub_estante_1,
            clave_idempotencia="ENT-BOBINA",
        )

    def test_traslado_interno_mismo_almacen(self):
        """Traslado interno cambia la asignación de ubicación sin alterar el stock total."""
        ex, mov = registrar_traslado_interno(
            producto=self.producto,
            almacen=self.almacen_origen,
            ubicacion_origen=self.ub_estante_1,
            ubicacion_destino=self.ub_estante_2,
            usuario=self.user_origen,
            motivo="Reorganización física del pasillo",
            clave_idempotencia="TRAS-INT-001",
        )
        self.assertEqual(ex.stock_disponible, 5)
        self.assertEqual(mov.tipo, Movimiento.Tipo.TRASLADO_INTERNO)
        self.assertEqual(mov.ubicacion_origen, self.ub_estante_1)
        self.assertEqual(mov.ubicacion_destino, self.ub_estante_2)

        # Ubicación 2 debe estar asociada y Ubicación 1 ya no
        self.assertTrue(self.producto.ubicaciones.filter(id=self.ub_estante_2.id).exists())
        self.assertFalse(self.producto.ubicaciones.filter(id=self.ub_estante_1.id).exists())

    def test_traslado_entre_almacenes_exitoso(self):
        """Traslado entre almacenes descuenta de origen y acredita en destino dentro de una transacción atómica."""
        ex_orig, ex_dest, mov = registrar_traslado_entre_almacenes(
            producto=self.producto,
            almacen_origen=self.almacen_origen,
            almacen_destino=self.almacen_destino,
            cantidad=5,
            usuario=self.user_origen,
            ubicacion_destino=self.ub_destino,
            motivo="Envío para proyecto sede norte",
            clave_idempotencia="TRAS-ENTRE-001",
        )
        self.assertEqual(ex_orig.stock_disponible, 0)
        self.assertEqual(ex_dest.stock_disponible, 5)
        self.assertEqual(mov.tipo, Movimiento.Tipo.TRASLADO_ENTRE)
        self.assertEqual(mov.cantidad, 5)
        self.assertEqual(mov.almacen_origen, self.almacen_origen)
        self.assertEqual(mov.almacen_destino, self.almacen_destino)

    def test_traslado_entre_almacenes_rechaza_traslado_parcial(self):
        """No permite traslado parcial entre almacenes; exige trasladar todo el stock (invariante 1 almacén por producto)."""
        with self.assertRaises(ValidationError) as ctx:
            registrar_traslado_entre_almacenes(
                producto=self.producto,
                almacen_origen=self.almacen_origen,
                almacen_destino=self.almacen_destino,
                cantidad=2,
                usuario=self.user_origen,
                ubicacion_destino=self.ub_destino,
            )
        self.assertIn("un producto solo puede estar en un único almacén", str(ctx.exception))

    def test_traslado_entre_almacenes_rechaza_stock_insuficiente(self):
        """No permite transferir más stock del disponible en origen."""
        with self.assertRaises(ValidationError) as ctx:
            registrar_traslado_entre_almacenes(
                producto=self.producto,
                almacen_origen=self.almacen_origen,
                almacen_destino=self.almacen_destino,
                cantidad=10,  # Solo hay 5
                usuario=self.user_origen,
                motivo="Intento exceso",
            )
        self.assertIn("Stock insuficiente", str(ctx.exception))

    def test_usuario_normal_solo_puede_iniciar_traslado_desde_su_almacen(self):
        """El usuario normal puede enviar desde su almacén, pero no desde almacenes ajenos (§6.2)."""
        # user_ajeno (asignado a almacen_destino) no puede transferir desde almacen_origen
        with self.assertRaises(PermissionDenied):
            registrar_traslado_entre_almacenes(
                producto=self.producto,
                almacen_origen=self.almacen_origen,
                almacen_destino=self.almacen_destino,
                cantidad=1,
                usuario=self.user_ajeno,
                motivo="Intento no autorizado",
            )
