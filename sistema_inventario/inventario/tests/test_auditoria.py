"""
Pruebas para la infraestructura de auditoría e inmutabilidad de registros.

Verifica los requisitos de la sección 10 de la especificación:
- Registro correcto de eventos con valores anteriores y nuevos en JSON.
- Soporte para acciones del sistema (usuario nulo).
- Compatibilidad transparente con ip_origen.
- Inmutabilidad estricta: prohibido UPDATE y DELETE en registros confirmados.
- Comportamiento atómico en transacciones.
"""

from django.db import transaction
from django.test import TestCase

from cuentas.models import UsuarioAudacIA
from inventario.auditoria import registrar_auditoria
from inventario.models import Almacen, Auditoria


class AuditoriaTest(TestCase):
    """Pruebas del helper de auditoría y su inmutabilidad."""

    def setUp(self):
        self.almacen = Almacen.objects.create(nombre="Almacén Auditoría", activo=True)
        self.superusuario = UsuarioAudacIA.objects.create_superuser(
            username="super_audit",
            nombre_completo="Super Auditor",
            password="Password123",
        )

    def test_registrar_auditoria_completo(self):
        registro = registrar_auditoria(
            usuario=self.superusuario,
            accion="CREAR",
            tipo_entidad="Almacen",
            id_entidad=str(self.almacen.id),
            valores_anteriores=None,
            valores_nuevos={"nombre": "Almacén Auditoría", "activo": True},
            motivo="Creación inicial para pruebas",
            resultado="OK",
            via_haku=False,
            ip_origen="192.168.1.50",
        )

        self.assertIsNotNone(registro.id)
        self.assertEqual(registro.usuario, self.superusuario)
        self.assertEqual(registro.accion, "CREAR")
        self.assertEqual(registro.tipo_entidad, "Almacen")
        self.assertEqual(registro.id_entidad, str(self.almacen.id))
        self.assertEqual(registro.valores_nuevos["nombre"], "Almacén Auditoría")
        self.assertIn("192.168.1.50", registro.motivo)
        self.assertEqual(registro.resultado, "OK")
        self.assertFalse(registro.via_haku)

    def test_registrar_auditoria_accion_del_sistema(self):
        # Acciones del sistema tienen usuario=None
        registro = registrar_auditoria(
            usuario=None,
            accion="BACKUP_AUTOMATICO",
            tipo_entidad="Sistema",
            id_entidad="BKP-001",
            motivo="Copia incremental cada 2 horas",
        )
        self.assertIsNone(registro.usuario)
        self.assertEqual(registro.accion, "BACKUP_AUTOMATICO")

    def test_inmutabilidad_bloquea_eliminacion(self):
        registro = registrar_auditoria(
            usuario=self.superusuario,
            accion="TEST_DELETE",
            tipo_entidad="Test",
            id_entidad="1",
        )
        with self.assertRaises(PermissionError):
            registro.delete()

    def test_inmutabilidad_bloquea_modificacion(self):
        registro = registrar_auditoria(
            usuario=self.superusuario,
            accion="TEST_UPDATE",
            tipo_entidad="Test",
            id_entidad="1",
        )
        registro.motivo = "Intento de alteración fraudulenta"
        with self.assertRaises(PermissionError):
            registro.save()

    def test_auditoria_rollback_en_transaccion_fallida(self):
        conteo_inicial = Auditoria.objects.count()
        try:
            with transaction.atomic():
                registrar_auditoria(
                    usuario=self.superusuario,
                    accion="OPERACION_FALLIDA",
                    tipo_entidad="Test",
                    id_entidad="999",
                )
                raise RuntimeError("Error forzado para probar rollback atómico")
        except RuntimeError:
            pass

        self.assertEqual(Auditoria.objects.count(), conteo_inicial)
