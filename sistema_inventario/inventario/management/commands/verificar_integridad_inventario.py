"""Comando de gestión de verificación de integridad de inventario.

Ejecuta una auditoría de solo lectura para validar:
1. No existan stocks disponibles negativos en ninguna Existencia.
2. No existan stocks en préstamo negativos.
3. Coherencia exacta entre sum(prestamos_pendientes) y Existencia.stock_en_prestamo.
4. Regla de almacén único: ningún producto debe tener existencias con stock > 0 en múltiples almacenes.
5. Coherencia entre prefijos de códigos de producto y SecuenciaCodigo.
6. Existencia de duplicados en códigos de producto (codigo_interno).

Retorna código de salida 0 si el sistema es íntegro, o 1 si se detectan anomalías.
"""

from typing import List
from django.core.management.base import BaseCommand
from django.db.models import Sum, Q, Count
from inventario.models import Producto, Existencia, Prestamo, SecuenciaCodigo


class Command(BaseCommand):
    help = "Audita la coherencia física e integridad de datos del inventario (solo lectura)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--detallado",
            action="store_true",
            help="Muestra el detalle individual de cada producto auditado.",
        )

    def handle(self, *args, **options):
        detallado = options.get("detallado", False)
        self.stdout.write(self.style.MIGRATE_HEADING("=== AUDITORIA DE INTEGRIDAD DE INVENTARIO (AUDACIA) ==="))

        errores: List[str] = []
        advertencias: List[str] = []

        # 1. Stocks disponibles negativos
        negativos_disponibles = Existencia.objects.filter(stock_disponible__lt=0).select_related("producto", "almacen")
        if negativos_disponibles.exists():
            for ex in negativos_disponibles:
                errores.append(
                    f"Existencia #{ex.id}: Producto '{ex.producto.nombre}' ({ex.producto.codigo_interno}) "
                    f"en '{ex.almacen.nombre}' tiene stock_disponible negativo: {ex.stock_disponible}."
                )

        # 2. Stocks en préstamo negativos
        negativos_prestamo = Existencia.objects.filter(stock_en_prestamo__lt=0).select_related("producto", "almacen")
        if negativos_prestamo.exists():
            for ex in negativos_prestamo:
                errores.append(
                    f"Existencia #{ex.id}: Producto '{ex.producto.nombre}' ({ex.producto.codigo_interno}) "
                    f"en '{ex.almacen.nombre}' tiene stock_en_prestamo negativo: {ex.stock_en_prestamo}."
                )

        # 3. Coherencia sum(prestamos_pendientes) vs Existencia.stock_en_prestamo
        existencias = Existencia.objects.select_related("producto", "almacen").all()
        for ex in existencias:
            total_prestamos = Prestamo.objects.filter(
                producto=ex.producto,
                almacen=ex.almacen,
                estado__in=[Prestamo.Estado.ABIERTO, Prestamo.Estado.DEVUELTO_PARCIAL],
            ).aggregate(total=Sum("cantidad_pendiente_devolucion"))["total"] or 0

            if total_prestamos != ex.stock_en_prestamo:
                errores.append(
                    f"Descuadre de prestamo en Existencia #{ex.id} ('{ex.producto.nombre}' @ '{ex.almacen.nombre}'): "
                    f"stock_en_prestamo registrado = {ex.stock_en_prestamo}, pero sum(pendientes) = {total_prestamos}."
                )
            elif detallado and ex.stock_en_prestamo > 0:
                self.stdout.write(
                    f"  [OK] '{ex.producto.codigo_interno}': {ex.stock_en_prestamo} unidades en prestamo cuadradas."
                )

        # 4. Regla de almacén único: ningún producto con stock > 0 en múltiples almacenes
        prods_multiples = (
            Existencia.objects.filter(Q(stock_disponible__gt=0) | Q(stock_en_prestamo__gt=0))
            .values("producto_id")
            .annotate(conteo_almacenes=Count("almacen_id", distinct=True))
            .filter(conteo_almacenes__gt=1)
        )
        if prods_multiples.exists():
            for item in prods_multiples:
                prod = Producto.objects.get(pk=item["producto_id"])
                errores.append(
                    f"Violacion de almacen unico: Producto '{prod.nombre}' ({prod.codigo_interno}) "
                    f"tiene stock registrado en {item['conteo_almacenes']} almacenes distintos."
                )

        # 5. Unicidad de códigos de producto
        codigos_duplicados = (
            Producto.objects.values("codigo_interno")
            .annotate(conteo=Count("id"))
            .filter(conteo__gt=1)
        )
        if codigos_duplicados.exists():
            for item in codigos_duplicados:
                errores.append(
                    f"Codigo duplicado: '{item['codigo_interno']}' se repite en {item['conteo']} productos."
                )

        # 6. Coherencia de SecuenciaCodigo
        secuencias = SecuenciaCodigo.objects.all()
        for sec in secuencias:
            max_num = 0
            for prod in Producto.objects.filter(codigo_interno__startswith=f"{sec.prefijo}-"):
                partes = prod.codigo_interno.split("-")
                if len(partes) >= 2 and partes[1].isdigit():
                    num = int(partes[1])
                    if num > max_num:
                        max_num = num

            if sec.ultimo_valor < max_num:
                errores.append(
                    f"Secuencia desfasada para prefijo '{sec.prefijo}': "
                    f"ultimo_valor registrado = {sec.ultimo_valor}, pero existe producto con numero {max_num}."
                )
            elif detallado:
                self.stdout.write(f"  [OK] Secuencia '{sec.prefijo}': valor actual {sec.ultimo_valor} (max en BD: {max_num}).")

        # Reporte final
        self.stdout.write("=" * 55)
        if not errores and not advertencias:
            self.stdout.write(self.style.SUCCESS("[OK] INTEGRIDAD COMPLETA: Todos los chequeos pasaron exitosamente sin inconsistencias."))
            return

        if advertencias:
            self.stdout.write(self.style.WARNING(f"\nSe encontraron {len(advertencias)} advertencias:"))
            for adv in advertencias:
                self.stdout.write(self.style.WARNING(f"  - {adv}"))

        if errores:
            self.stdout.write(self.style.ERROR(f"\nSe encontraron {len(errores)} INCONSISTENCIAS CRITICAS:"))
            for err in errores:
                self.stdout.write(self.style.ERROR(f"  - {err}"))
            raise SystemExit(1)
