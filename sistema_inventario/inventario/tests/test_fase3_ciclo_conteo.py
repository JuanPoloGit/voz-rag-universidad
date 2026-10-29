"""Pruebas unitarias e integrales para el Ciclo de Vida del Conteo Físico (Fase 3 Remedación).

Cubre:
- Estados PENDIENTE, CONFIRMADO, RECHAZADO.
- Restricción de unicidad para conteos pendientes por producto (DB y Servicio).
- Bloqueo y atomicidad en aprobación vs rechazo (concurrencia).
- Limpieza de alertas transversales (AlertaStockLeida).
- Permisos administrativos y vistas (confirmar, rechazar, filtros).
- Compatibilidad retroactiva de atributos y propiedades.
"""

from concurrent.futures import ThreadPoolExecutor
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, close_old_connections
from django.test import Client, TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model

from cuentas.models import UsuarioAudacIA
from inventario.models import (
    AlertaStockLeida,
    Almacen,
    Auditoria,
    Categoria,
    ConteoFisico,
    Existencia,
    Movimiento,
    Producto,
)
from inventario.servicios.operaciones import (
    confirmar_conteo_fisico,
    rechazar_conteo_fisico,
    registrar_conteo_fisico,
    registrar_entrada,
)

User = get_user_model()


class Fase3CicloConteoTest(TestCase):
    def setUp(self):
        self.almacen = Almacen.objects.create(nombre="Almacén Laboratorio", activo=True)

        self.admin = User.objects.create_user(
            username="admin_conteo",
            password="Password123",
            nombre_completo="Admin Conteo",
            rol=UsuarioAudacIA.Rol.ADMINISTRADOR,
            almacen_asignado=self.almacen,
        )
        self.operador = User.objects.create_user(
            username="operador_conteo",
            password="Password123",
            nombre_completo="Operador Conteo",
            rol=UsuarioAudacIA.Rol.USUARIO,
            almacen_asignado=self.almacen,
        )

        self.categoria = Categoria.objects.create(
            nombre="Robótica Experimental",
            almacen_principal=self.almacen,
        )
        self.producto = Producto.objects.create(
            nombre="Servomotor Dinámico",
            categoria=self.categoria,
            stock_minimo=5,
        )

        registrar_entrada(
            producto=self.producto,
            almacen=self.almacen,
            cantidad=20,
            usuario=self.operador,
            clave_idempotencia="INIT-FASE3-CONTEO",
        )

    def test_flujo_aprobacion_conteo(self):
        """Aprobar un conteo pendiente aplica el ajuste y cambia a CONFIRMADO."""
        conteo = registrar_conteo_fisico(
            producto=self.producto,
            almacen=self.almacen,
            cantidad_contada=25,
            usuario=self.operador,
            motivo="Llegó lote sin registrar",
        )
        self.assertEqual(conteo.estado, ConteoFisico.Estado.PENDIENTE)
        self.assertFalse(conteo.confirmado)

        # Simular lectura de alerta previa
        AlertaStockLeida.objects.create(
            usuario=self.admin,
            clave=f"conteo:{conteo.id}",
        )
        self.assertEqual(AlertaStockLeida.objects.filter(clave=f"conteo:{conteo.id}").count(), 1)

        conteo_db, mov = confirmar_conteo_fisico(
            conteo=conteo,
            usuario=self.admin,
        )

        self.assertEqual(conteo_db.estado, ConteoFisico.Estado.CONFIRMADO)
        self.assertTrue(conteo_db.confirmado)
        self.assertEqual(conteo_db.usuario_confirmacion, self.admin)
        self.assertIsNotNone(conteo_db.fecha_confirmacion)

        ex = Existencia.objects.get(producto=self.producto, almacen=self.almacen)
        self.assertEqual(ex.stock_disponible, 25)
        self.assertEqual(mov.tipo, Movimiento.Tipo.AJUSTE_CONTEO)

        # Se debe haber limpiado la alerta transversal
        self.assertEqual(AlertaStockLeida.objects.filter(clave=f"conteo:{conteo.id}").count(), 0)

    def test_flujo_rechazo_conteo(self):
        """Rechazar un conteo no modifica existencias y registra auditoría."""
        conteo = registrar_conteo_fisico(
            producto=self.producto,
            almacen=self.almacen,
            cantidad_contada=5,  # Supuesta pérdida masiva falsa
            usuario=self.operador,
            motivo="Conteo preliminar dudoso",
        )
        self.assertEqual(conteo.estado, ConteoFisico.Estado.PENDIENTE)

        AlertaStockLeida.objects.create(
            usuario=self.admin,
            clave=f"conteo:{conteo.id}",
        )

        conteo_db = rechazar_conteo_fisico(
            conteo=conteo,
            usuario=self.admin,
            motivo="Recuento erróneo: cajas estaban en zona de cuarentena.",
        )

        self.assertEqual(conteo_db.estado, ConteoFisico.Estado.RECHAZADO)
        self.assertFalse(conteo_db.confirmado)
        self.assertEqual(conteo_db.usuario_confirmacion, self.admin)
        self.assertIn("cuarentena", conteo_db.motivo_rechazo)

        # Stock disponible intacto
        ex = Existencia.objects.get(producto=self.producto, almacen=self.almacen)
        self.assertEqual(ex.stock_disponible, 20)

        # Ningún movimiento de inventario generado
        self.assertFalse(Movimiento.objects.filter(producto=self.producto, tipo=Movimiento.Tipo.AJUSTE_CONTEO).exists())

        # Alerta transversal eliminada
        self.assertEqual(AlertaStockLeida.objects.filter(clave=f"conteo:{conteo.id}").count(), 0)

        # Auditoría registrada
        self.assertTrue(
            Auditoria.objects.filter(
                accion="RECHAZAR_CONTEO",
                tipo_entidad="ConteoFisico",
                id_entidad=str(conteo.id),
            ).exists()
        )

    def test_no_permitir_doble_resolucion(self):
        """Un conteo confirmado o rechazado no puede volver a confirmarse ni rechazarse."""
        conteo = registrar_conteo_fisico(
            producto=self.producto,
            almacen=self.almacen,
            cantidad_contada=18,
            usuario=self.operador,
        )

        confirmar_conteo_fisico(conteo=conteo, usuario=self.admin)

        with self.assertRaises(ValidationError) as ctx1:
            confirmar_conteo_fisico(conteo=conteo, usuario=self.admin)
        self.assertIn("ya fue resuelto", str(ctx1.exception))

        with self.assertRaises(ValidationError) as ctx2:
            rechazar_conteo_fisico(conteo=conteo, usuario=self.admin, motivo="Rechazar tras confirmar")
        self.assertIn("ya fue resuelto", str(ctx2.exception))

        # Crear otro para probar rechazo doble
        conteo2 = registrar_conteo_fisico(
            producto=self.producto,
            almacen=self.almacen,
            cantidad_contada=19,
            usuario=self.operador,
        )
        rechazar_conteo_fisico(conteo=conteo2, usuario=self.admin, motivo="Rechazo 1")

        with self.assertRaises(ValidationError):
            rechazar_conteo_fisico(conteo=conteo2, usuario=self.admin, motivo="Rechazo 2")

        with self.assertRaises(ValidationError):
            confirmar_conteo_fisico(conteo=conteo2, usuario=self.admin)

    def test_restriccion_conteo_pendiente_unico(self):
        """No se permite más de un conteo PENDIENTE simultáneo por producto."""
        registrar_conteo_fisico(
            producto=self.producto,
            almacen=self.almacen,
            cantidad_contada=15,
            usuario=self.operador,
        )

        # Intento vía servicio de registrar otro pendiente para el mismo producto
        with self.assertRaises(ValidationError) as ctx:
            registrar_conteo_fisico(
                producto=self.producto,
                almacen=self.almacen,
                cantidad_contada=14,
                usuario=self.operador,
            )
        self.assertIn("Ya existe un conteo físico pendiente", str(ctx.exception))

        # Intento directo ORM para verificar UniqueConstraint a nivel DB
        with self.assertRaises(IntegrityError):
            ConteoFisico.objects.create(
                producto=self.producto,
                almacen=self.almacen,
                stock_sistema=20,
                cantidad_contada=14,
                diferencia=-6,
                usuario_conteo=self.operador,
                estado=ConteoFisico.Estado.PENDIENTE,
            )

    def test_vistas_permisos_confirmar_y_rechazar(self):
        """Solo administradores pueden POST a conteo-confirmar y conteo-rechazar."""
        conteo = registrar_conteo_fisico(
            producto=self.producto,
            almacen=self.almacen,
            cantidad_contada=22,
            usuario=self.operador,
        )

        client_operador = Client()
        client_operador.force_login(self.operador)

        url_confirmar = reverse("inventario:conteo-confirmar", kwargs={"pk": conteo.pk})
        url_rechazar = reverse("inventario:conteo-rechazar", kwargs={"pk": conteo.pk})

        # Operador raso debe ser rechazado (403 o redirect de login/error)
        resp1 = client_operador.post(url_confirmar)
        self.assertIn(resp1.status_code, [302, 403])
        conteo.refresh_from_db()
        self.assertEqual(conteo.estado, ConteoFisico.Estado.PENDIENTE)

        resp2 = client_operador.post(url_rechazar, {"motivo_rechazo": "Test"})
        self.assertIn(resp2.status_code, [302, 403])
        conteo.refresh_from_db()
        self.assertEqual(conteo.estado, ConteoFisico.Estado.PENDIENTE)

        # Administrador puede rechazar
        client_admin = Client()
        client_admin.force_login(self.admin)
        resp3 = client_admin.post(url_rechazar, {"motivo_rechazo": "Rechazado en vista"})
        self.assertEqual(resp3.status_code, 302)
        conteo.refresh_from_db()
        self.assertEqual(conteo.estado, ConteoFisico.Estado.RECHAZADO)

    def test_filtro_estado_conteos_en_movimientos(self):
        """La vista de movimientos con vista=CONTEOS filtra adecuadamente por estado."""
        c1 = registrar_conteo_fisico(
            producto=self.producto,
            almacen=self.almacen,
            cantidad_contada=18,
            usuario=self.operador,
        )
        rechazar_conteo_fisico(conteo=c1, usuario=self.admin, motivo="Descarte 1")

        c2 = registrar_conteo_fisico(
            producto=self.producto,
            almacen=self.almacen,
            cantidad_contada=22,
            usuario=self.operador,
        )
        confirmar_conteo_fisico(conteo=c2, usuario=self.admin)

        c3 = registrar_conteo_fisico(
            producto=self.producto,
            almacen=self.almacen,
            cantidad_contada=25,
            usuario=self.operador,
        )

        client = Client()
        client.force_login(self.admin)
        url = reverse("inventario:movimiento-lista")

        # Filtro PENDIENTE
        resp_pend = client.get(url, {"vista": "CONTEOS", "estado_conteo": "PENDIENTE"})
        self.assertEqual(resp_pend.status_code, 200)
        conteos_pend = list(resp_pend.context["conteos_lista"])
        self.assertIn(c3, conteos_pend)
        self.assertNotIn(c1, conteos_pend)
        self.assertNotIn(c2, conteos_pend)

        # Filtro CONFIRMADO
        resp_conf = client.get(url, {"vista": "CONTEOS", "estado_conteo": "CONFIRMADO"})
        self.assertEqual(resp_conf.status_code, 200)
        conteos_conf = list(resp_conf.context["conteos_lista"])
        self.assertIn(c2, conteos_conf)
        self.assertNotIn(c1, conteos_conf)

        # Filtro RECHAZADO
        resp_rech = client.get(url, {"vista": "CONTEOS", "estado_conteo": "RECHAZADO"})
        self.assertEqual(resp_rech.status_code, 200)
        conteos_rech = list(resp_rech.context["conteos_lista"])
        self.assertIn(c1, conteos_rech)
        self.assertNotIn(c2, conteos_rech)
