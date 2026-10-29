"""Pruebas de concurrencia e idempotencia multihilo para movimientos de inventario."""

import concurrent.futures
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TransactionTestCase
from django.contrib.auth import get_user_model

from cuentas.models import UsuarioAudacIA
from inventario.models import Almacen, Categoria, Existencia, Producto
from inventario.servicios.operaciones import (
    registrar_entrada,
    registrar_prestamo,
    registrar_salida,
)

User = get_user_model()


class ConcurrenciaMovimientosTest(TransactionTestCase):
    """Pruebas de concurrencia usando TransactionTestCase para soporte real multihilo."""

    def setUp(self):
        self.almacen = Almacen.objects.create(nombre="Almacén Concurrente", activo=True)
        self.user = User.objects.create_user(
            username="concurrency_user",
            password="Password123",
            nombre_completo="Usuario Concurrencia",
            rol=UsuarioAudacIA.Rol.ADMINISTRADOR,
        )
        self.cat = Categoria.objects.create(
            nombre="Componentes Críticos",
            almacen_principal=self.almacen,
        )
        self.producto = Producto.objects.create(
            nombre="Sensor LiDAR",
            categoria=self.cat,
            stock_minimo=1,
        )

    def test_carrera_por_la_ultima_unidad_no_genera_stock_negativo(self):
        """10 hilos compiten por 1 sola unidad en stock; exactamente 1 triunfa y el stock termina en 0."""
        # Se ingresa 1 sola unidad
        registrar_entrada(
            producto=self.producto,
            almacen=self.almacen,
            cantidad=1,
            usuario=self.user,
            clave_idempotencia="INI-CONC-1",
        )

        exitos = 0
        fallos = 0

        def intentar_salida(idx):
            # Cierra conexiones previas del hilo para obtener una nueva limpia
            connection.close()
            try:
                registrar_salida(
                    producto=self.producto,
                    almacen=self.almacen,
                    cantidad=1,
                    motivo=f"Retiro concurrente hilo {idx}",
                    destinatario=f"Destinatario {idx}",
                    usuario=self.user,
                    clave_idempotencia=f"CONC-SAL-{idx}",
                )
                return True
            except ValidationError:
                return False
            except Exception:
                # En SQLite puede ocurrir OperationalError por database is locked bajo alta contención
                return False
            finally:
                connection.close()

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futuros = [executor.submit(intentar_salida, i) for i in range(10)]
            for f in concurrent.futures.as_completed(futuros):
                if f.result():
                    exitos += 1
                else:
                    fallos += 1

        ex = Existencia.objects.get(producto=self.producto, almacen=self.almacen)
        # Regla de oro: El stock NUNCA puede ser negativo
        self.assertGreaterEqual(ex.stock_disponible, 0)
        self.assertEqual(ex.stock_disponible, 0)
        self.assertEqual(exitos, 1)
        self.assertEqual(fallos, 9)

    def test_idempotencia_doble_clic_multihilo(self):
        """Múltiples hilos enviando la misma clave de idempotencia no duplican stock."""
        clave_unica = "DOBLE-CLIC-KEY-12345"

        def reintentar_entrada():
            connection.close()
            try:
                registrar_entrada(
                    producto=self.producto,
                    almacen=self.almacen,
                    cantidad=5,
                    usuario=self.user,
                    clave_idempotencia=clave_unica,
                )
                return True
            except Exception:
                return False
            finally:
                connection.close()

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futuros = [executor.submit(reintentar_entrada) for _ in range(5)]
            concurrent.futures.wait(futuros)

        ex = Existencia.objects.get(producto=self.producto, almacen=self.almacen)
        # Debe tener exactamente 5 unidades, nunca 25
        self.assertEqual(ex.stock_disponible, 5)
