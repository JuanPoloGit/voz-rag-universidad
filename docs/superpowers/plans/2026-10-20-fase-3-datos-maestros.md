# Fase 3 — Datos Maestros · Plan de Implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implementar CRUD completo para almacenes, ubicaciones, categorías y productos, con capa de servicios, traslados estructurales obligatorios, fusión de duplicados, y trazabilidad de auditoría completa.

**Architecture:** Capa de servicios (`inventario/servicios/`) como única autoridad para mutaciones de datos maestros y stock. Vistas CBV consumen los servicios y aplican permisos vía `cuentas.permisos` (`RolRequeridoMixin`, `AccesoAlmacenMixin`). Toda creación/edición/cambio de estado pasa por `registrar_auditoria()`. Templates extienden `templates/base.html`.

**Tech Stack:** Django 6.1.2, Python 3.13, SQLite (desarrollo/tests)

**Spec:** `gemini-code-1791922781976.md` §7 (almacenes, ubicaciones, categorías), §8 (productos, stock, códigos, fusión)

## Global Constraints

- **Stock negativo prohibido** — Ninguna operación puede dejar `Existencia.stock_disponible < 0`
- **`codigo_interno` inmutable** — Una vez generado en `Producto.save()`, jamás se modifica (ni al cambiar categoría/nombre)
- **Auditar toda mutación con acciones en mayúsculas** — `CREAR_ALMACEN`, `EDITAR_ALMACEN`, `DESACTIVAR_ALMACEN`, `CREAR_UBICACION`, `EDITAR_UBICACION`, `DESACTIVAR_UBICACION`, `CREAR_CATEGORIA`, `EDITAR_CATEGORIA`, `DESACTIVAR_CATEGORIA`, `CREAR_PRODUCTO`, `EDITAR_PRODUCTO`, `ARCHIVAR_PRODUCTO`, `FUSIONAR_PRODUCTOS`, `EXCEPCION_CATEGORIA_ALMACEN` → `registrar_auditoria()`
- **Sincronización con caché en RAM de Haku** — Las mutaciones de `Producto`, `Existencia`, `Movimiento` deben llamar a `.save()` sobre las instancias (tras bloqueos) o emitir `post_save` para asegurar que las señales de `integraciones.haku.cache` invaliden el catálogo, ubicaciones y stock en RAM automáticamente.
- **Permisos por almacén (§6.2)** — USUARIO puede crear y editar productos, ubicaciones y categorías asociadas a su `almacen_asignado`. No puede confirmar conteos, archivar productos, desactivar almacenes/ubicaciones/categorías ni fusionar duplicados. ADMINISTRADOR y SUPERUSUARIO tienen acceso global a todos los almacenes y a funciones destructivas/administrativas.
- **Archivos prohibidos** — No modificar `integraciones/haku/**`, `cuentas/**`, `cerebro_audacia/**`, `scripts/**`
- **Archivos acotados** — `inventario_audacia/settings.py` solo para retirar `SILENCED_SYSTEM_CHECKS` si queda obsoleto; `inventario_audacia/urls.py` ya tiene los prefijos necesarios
- **Venv y runner** — `cd sistema_inventario; ..\venv\Scripts\python.exe manage.py <cmd>`. Para TDD rápido por tarea: `manage.py test cuentas inventario`. Para compuerta de QA/Auditoría antes de commit: `manage.py check` + `manage.py test cuentas inventario`.

## Review Focus

1. **Carrera al generar `codigo_interno`** — Dos creaciones simultáneas de producto en la misma categoría podrían generar el mismo consecutivo. El servicio debe usar `select_for_update()` sobre el último código de la categoría. Test: crear dos productos concurrentemente y verificar códigos distintos.
2. **Cascade al desactivar almacén referenciado como `almacen_principal` de categorías** — El almacén destino del traslado no debe ser el mismo que se desactiva, y las categorías deben actualizar su `almacen_principal`. Test: desactivar almacén con categorías y verificar actualización.
3. **Bypass de permisos por manipulación de POST** — Un USUARIO enviando `almacen_id` de otro almacén en el formulario. Test: POST con `almacen_id` ajeno → 403.
4. **Unicidad de `codigo_barras`** — Enviar un código de barras que ya pertenece a otro producto. Test: POST duplicado → ValidationError sin crear producto.
5. **Desactivar ubicación con `ProductoUbicacion` activos** — Debe exigir trasladar primero. Test: desactivar ubicación con productos → error; trasladar y luego desactivar → OK.

---

## File Structure

```
sistema_inventario/
├── templates/
│   └── base.html                                    # Task 1 (nueva)
├── inventario/
│   ├── servicios/
│   │   ├── __init__.py                              # Task 1 (nuevo)
│   │   ├── datos_maestros.py                        # Tasks 1-4 (nuevo)
│   │   └── stock.py                                 # Tasks 4-5 (nuevo)
│   ├── forms.py                                     # Tasks 1-4 (nuevo)
│   ├── views.py                                     # Tasks 1-6 (nuevo)
│   ├── urls.py                                      # Tasks 1-6 (modificar)
│   ├── admin.py                                     # Task 1 (fix bug L105)
│   └── templates/inventario/
│       ├── almacen_lista.html                       # Task 1
│       ├── almacen_form.html                        # Task 1
│       ├── ubicacion_lista.html                     # Task 2
│       ├── ubicacion_form.html                      # Task 2
│       ├── categoria_lista.html                     # Task 3
│       ├── categoria_form.html                      # Task 3
│       ├── producto_lista.html                      # Task 4
│       ├── producto_form.html                       # Task 4
│       ├── producto_detalle.html                    # Task 4
│       ├── trasladar_almacen.html                   # Task 5
│       ├── trasladar_ubicacion.html                 # Task 5
│       ├── trasladar_categoria.html                 # Task 5
│       └── producto_fusionar.html                   # Task 6
│   └── tests/
│       ├── test_almacenes.py                        # Task 1
│       ├── test_ubicaciones.py                      # Task 2
│       ├── test_categorias.py                       # Task 3
│       ├── test_productos.py                        # Task 4
│       ├── test_traslados_estructurales.py          # Task 5
│       └── test_fusion_productos.py                 # Task 6
```

---

### Task 1: Scaffolding + Almacén CRUD

**Files:**
- Create: `templates/base.html`
- Create: `inventario/servicios/__init__.py`
- Create: `inventario/servicios/datos_maestros.py`
- Create: `inventario/forms.py`
- Create: `inventario/views.py`
- Modify: `inventario/urls.py`
- Create: `inventario/templates/inventario/almacen_lista.html`
- Create: `inventario/templates/inventario/almacen_form.html`
- Modify: `inventario/admin.py:105` — quitar `"motivo"` de `PrestamoAdmin.search_fields`
- Test: `inventario/tests/test_almacenes.py`

**Interfaces:**
- Consumes: `registrar_auditoria()` de `inventario.auditoria`, `RolRequeridoMixin` de `cuentas.permisos`
- Produces:
  - `crear_almacen(*, nombre: str, descripcion: str = "", usuario, ip_origen: str = None) -> Almacen`
  - `actualizar_almacen(*, almacen: Almacen, nombre: str, descripcion: str, usuario, ip_origen: str = None) -> Almacen`
  - `reactivar_almacen(*, almacen: Almacen, usuario, ip_origen: str = None) -> Almacen`
  - `AlmacenForm(ModelForm)` — fields: `nombre`, `descripcion`
  - URL names: `inventario:almacen-lista`, `inventario:almacen-crear`, `inventario:almacen-editar`

- [ ] **Step 1: Write failing tests for almacén service**

```python
# inventario/tests/test_almacenes.py
class TestCrearAlmacen(TestCase):
    def test_crea_almacen_y_registra_auditoria(self):
        almacen = crear_almacen(nombre="Principal", usuario=self.admin)
        self.assertEqual(almacen.nombre, "Principal")
        self.assertTrue(almacen.activo)
        self.assertTrue(Auditoria.objects.filter(tipo_entidad="Almacen", accion="crear").exists())

    def test_nombre_duplicado_lanza_validation_error(self):
        crear_almacen(nombre="A", usuario=self.admin)
        with self.assertRaises(ValidationError):
            crear_almacen(nombre="A", usuario=self.admin)

class TestActualizarAlmacen(TestCase):
    def test_actualiza_nombre_y_registra_valores_anteriores(self):
        almacen = crear_almacen(nombre="Viejo", usuario=self.admin)
        actualizar_almacen(almacen=almacen, nombre="Nuevo", descripcion="", usuario=self.admin)
        almacen.refresh_from_db()
        self.assertEqual(almacen.nombre, "Nuevo")
        aud = Auditoria.objects.filter(tipo_entidad="Almacen", accion="editar").first()
        self.assertEqual(aud.valores_anteriores["nombre"], "Viejo")
```

- [ ] **Step 2: Run tests — expect FAIL (import error)**

```powershell
..\venv\Scripts\python.exe manage.py test inventario.tests.test_almacenes -v2
```

- [ ] **Step 3: Implement service functions in `inventario/servicios/datos_maestros.py`**

Cada función: validar con `full_clean()`, guardar dentro de `transaction.atomic()`, llamar a `registrar_auditoria()` con snapshot JSON de valores anteriores/nuevos, retornar la entidad. `crear_almacen` valida unicidad de nombre. `actualizar_almacen` captura valores anteriores antes de `save()`.

- [ ] **Step 4: Run tests — expect PASS**

- [ ] **Step 5: Create `templates/base.html`**

Estructura HTML5 adaptable con bloques: `{% block title %}`, `{% block content %}`, `{% block extra_js %}`. Navegación con links a las secciones de inventario. Zona de `{% if messages %}` para mensajes de Django. Sin CDN — todo local.

- [ ] **Step 6: Write failing tests for almacén views**

```python
class TestAlmacenVistas(TestCase):
    def test_lista_requiere_login(self):
        r = self.client.get(reverse("inventario:almacen-lista"))
        self.assertEqual(r.status_code, 302)

    def test_usuario_normal_no_puede_crear_almacen(self):
        self.client.force_login(self.usuario_normal)
        r = self.client.get(reverse("inventario:almacen-crear"))
        self.assertEqual(r.status_code, 403)

    def test_admin_crea_almacen_via_post(self):
        self.client.force_login(self.admin)
        r = self.client.post(reverse("inventario:almacen-crear"), {"nombre": "Nuevo", "descripcion": ""})
        self.assertRedirects(r, reverse("inventario:almacen-lista"))
        self.assertTrue(Almacen.objects.filter(nombre="Nuevo").exists())

    def test_lista_filtra_inactivos_por_defecto(self):
        crear_almacen(nombre="Activo", usuario=self.admin)
        inactivo = crear_almacen(nombre="Inactivo", usuario=self.admin)
        inactivo.activo = False; inactivo.save()
        self.client.force_login(self.admin)
        r = self.client.get(reverse("inventario:almacen-lista"))
        self.assertContains(r, "Activo")
        self.assertNotContains(r, "Inactivo")
```

- [ ] **Step 7: Implement `AlmacenForm`, views (`AlmacenListaView`, `AlmacenCrearView`, `AlmacenEditarView`), URLs and templates**

Views usan `LoginRequiredMixin` + `RolRequeridoMixin(roles_permitidos=(Rol.ADMINISTRADOR, Rol.SUPERUSUARIO))`. `AlmacenListaView` filtra `activo=True, eliminado=False` por defecto, con parámetro GET `?mostrar_inactivos=1` para admin. Form POST llama al servicio correspondiente, no al `form.save()` directo. Cada vista extrae `ip_origen = request.META.get("REMOTE_ADDR")` y lo pasa al servicio.

- [ ] **Step 8: Fix `admin.py` L105** — quitar `"motivo"` de `PrestamoAdmin.search_fields`

- [ ] **Step 9: Run full regression**

```powershell
..\venv\Scripts\python.exe manage.py check
..\venv\Scripts\python.exe manage.py test cuentas inventario integraciones.haku -v2
```

- [ ] **Step 10: Commit**

```powershell
git add -A; git commit -m "feat(inventario): base template, almacen CRUD con capa de servicios y auditoria"
```

---

### Task 2: Ubicación CRUD

**Files:**
- Modify: `inventario/servicios/datos_maestros.py`
- Modify: `inventario/forms.py`
- Modify: `inventario/views.py`
- Modify: `inventario/urls.py`
- Create: `inventario/templates/inventario/ubicacion_lista.html`
- Create: `inventario/templates/inventario/ubicacion_form.html`
- Test: `inventario/tests/test_ubicaciones.py`

**Interfaces:**
- Consumes: `AlmacenForm`, `AccesoAlmacenMixin`, `obtener_almacen_o_denegar()`, `filtrar_por_almacen()`
- Produces:
  - `crear_ubicacion(*, nombre: str, almacen: Almacen, descripcion: str = "", usuario, ip_origen=None) -> Ubicacion`
  - `actualizar_ubicacion(*, ubicacion: Ubicacion, nombre: str, descripcion: str, usuario, ip_origen=None) -> Ubicacion`
  - `UbicacionForm(ModelForm)` — fields: `nombre`, `descripcion`, `almacen` (oculto para USUARIO, visible para admin)
  - URL names: `inventario:ubicacion-lista`, `inventario:ubicacion-crear`, `inventario:ubicacion-editar`

- [ ] **Step 1: Write failing tests for ubicación service**

```python
class TestCrearUbicacion(TestCase):
    def test_crea_ubicacion_en_almacen_y_audita(self):
        ubic = crear_ubicacion(nombre="Estante A", almacen=self.almacen, usuario=self.admin)
        self.assertEqual(ubic.almacen, self.almacen)
        self.assertTrue(Auditoria.objects.filter(tipo_entidad="Ubicacion", accion="crear").exists())

    def test_nombre_duplicado_en_mismo_almacen_falla(self):
        crear_ubicacion(nombre="A", almacen=self.almacen, usuario=self.admin)
        with self.assertRaises(ValidationError):
            crear_ubicacion(nombre="A", almacen=self.almacen, usuario=self.admin)

    def test_nombre_duplicado_en_otro_almacen_ok(self):
        otro = crear_almacen(nombre="Otro", usuario=self.admin)
        crear_ubicacion(nombre="A", almacen=self.almacen, usuario=self.admin)
        ubic2 = crear_ubicacion(nombre="A", almacen=otro, usuario=self.admin)
        self.assertEqual(ubic2.nombre, "A")

class TestActualizarUbicacion(TestCase):
    def test_renombrar_audita_nombre_anterior(self):
        ubic = crear_ubicacion(nombre="Viejo", almacen=self.almacen, usuario=self.admin)
        actualizar_ubicacion(ubicacion=ubic, nombre="Nuevo", descripcion="", usuario=self.admin)
        aud = Auditoria.objects.filter(tipo_entidad="Ubicacion", accion="editar").first()
        self.assertEqual(aud.valores_anteriores["nombre"], "Viejo")
```

- [ ] **Step 2: Run tests — expect FAIL**

- [ ] **Step 3: Implement service functions**

`crear_ubicacion` valida `unique_together(almacen, nombre)` vía `full_clean()`. `actualizar_ubicacion` captura snapshot previo.

- [ ] **Step 4: Run tests — expect PASS**

- [ ] **Step 5: Write failing tests for ubicación views**

```python
class TestUbicacionVistas(TestCase):
    def test_usuario_ve_solo_ubicaciones_de_su_almacen(self):
        self.client.force_login(self.usuario_normal)  # almacen_asignado = almacen_a
        r = self.client.get(reverse("inventario:ubicacion-lista"))
        # Solo muestra ubicaciones de almacen_a
        self.assertContains(r, "Estante A")
        self.assertNotContains(r, "Estante B")  # está en almacen_b

    def test_usuario_crea_ubicacion_en_su_almacen(self):
        self.client.force_login(self.usuario_normal)
        r = self.client.post(reverse("inventario:ubicacion-crear"), {"nombre": "Nueva", "descripcion": ""})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(Ubicacion.objects.filter(nombre="Nueva", almacen=self.almacen_a).exists())

    def test_usuario_no_puede_crear_en_otro_almacen(self):
        self.client.force_login(self.usuario_normal)
        r = self.client.post(reverse("inventario:ubicacion-crear"),
                             {"nombre": "Hack", "almacen": self.almacen_b.pk})
        # AccesoAlmacenMixin fuerza almacen_asignado; ignora el POST
        ubic = Ubicacion.objects.filter(nombre="Hack").first()
        if ubic:
            self.assertEqual(ubic.almacen, self.almacen_a)
```

- [ ] **Step 6: Implement form, views, URLs, templates**

`UbicacionListaView` usa `AccesoAlmacenMixin` + `filtrar_por_almacen()`. Para USUARIO, el campo `almacen` se fuerza a `almacen_asignado` en `form_valid()` (nunca confiar en el POST). Para admin, se muestra selector de almacén.

- [ ] **Step 7: Run regression + commit**

```powershell
..\venv\Scripts\python.exe manage.py test cuentas inventario integraciones.haku -v2
git add -A; git commit -m "feat(inventario): ubicacion CRUD con permisos por almacen"
```

---

### Task 3: Categoría CRUD

**Files:**
- Modify: `inventario/servicios/datos_maestros.py`
- Modify: `inventario/forms.py`
- Modify: `inventario/views.py`
- Modify: `inventario/urls.py`
- Create: `inventario/templates/inventario/categoria_lista.html`
- Create: `inventario/templates/inventario/categoria_form.html`
- Test: `inventario/tests/test_categorias.py`

**Interfaces:**
- Consumes: `crear_almacen`, `Almacen`, `AccesoAlmacenMixin`
- Produces:
  - `crear_categoria(*, nombre: str, almacen_principal: Almacen, usuario, ip_origen=None) -> Categoria`
  - `actualizar_categoria(*, categoria: Categoria, nombre: str, almacen_principal: Almacen, usuario, ip_origen=None) -> Categoria`
    — Si `almacen_principal` cambia, **no** traslada aquí; devuelve flag `requiere_traslado=True` y la vista redirige al flujo de traslado (Task 5). Solo guarda si el almacén no cambia o si el traslado ya se completó.
  - `CategoriaForm(ModelForm)` — fields: `nombre`, `almacen_principal`
  - URL names: `inventario:categoria-lista`, `inventario:categoria-crear`, `inventario:categoria-editar`

- [ ] **Step 1: Write failing tests for categoría service**

```python
class TestCrearCategoria(TestCase):
    def test_crea_categoria_con_almacen_principal(self):
        cat = crear_categoria(nombre="Cables", almacen_principal=self.almacen, usuario=self.admin)
        self.assertEqual(cat.almacen_principal, self.almacen)
        self.assertEqual(cat.prefijo_codigo, "CAB")

    def test_nombre_duplicado_falla(self):
        crear_categoria(nombre="Cables", almacen_principal=self.almacen, usuario=self.admin)
        with self.assertRaises(ValidationError):
            crear_categoria(nombre="Cables", almacen_principal=self.almacen, usuario=self.admin)

class TestActualizarCategoria(TestCase):
    def test_editar_nombre_sin_cambiar_almacen_ok(self):
        cat = crear_categoria(nombre="Viejo", almacen_principal=self.almacen, usuario=self.admin)
        actualizar_categoria(categoria=cat, nombre="Nuevo", almacen_principal=self.almacen, usuario=self.admin)
        cat.refresh_from_db()
        self.assertEqual(cat.nombre, "Nuevo")

    def test_cambiar_almacen_con_productos_requiere_traslado(self):
        # Cuando la categoría tiene productos y se cambia el almacén, no se guarda directamente
        # El servicio lanza NecesitaTraslado para que la vista redirija al flujo de traslado
        cat = crear_categoria(nombre="Cables", almacen_principal=self.almacen, usuario=self.admin)
        # ... crear producto en esta categoría ...
        with self.assertRaises(NecesitaTraslado):
            actualizar_categoria(categoria=cat, nombre="Cables",
                                 almacen_principal=self.otro_almacen, usuario=self.admin)

class TestExcepcionCategoriaAlmacen(TestCase):
    def test_producto_en_almacen_distinto_requiere_confirmacion(self):
        # §7.3: excepciones poco frecuentes con confirmación explícita
        # Cuando un producto tiene existencia en un almacén != cat.almacen_principal
        # crear la existencia requiere confirmacion_excepcion=True y motivo
        from inventario.servicios.stock import crear_existencia_excepcion
        with self.assertRaises(ValidationError):
            crear_existencia_excepcion(producto=self.prod, almacen=self.otro_almacen,
                                       usuario=self.admin, confirmacion=False, motivo="")

        ex = crear_existencia_excepcion(producto=self.prod, almacen=self.otro_almacen,
                                         usuario=self.admin, confirmacion=True,
                                         motivo="Traslado especial autorizado")
        self.assertIsNotNone(ex)
        aud = Auditoria.objects.filter(accion="excepcion_categoria_almacen").first()
        self.assertIn("Traslado especial autorizado", aud.motivo)
```

- [ ] **Step 2: Run tests — expect FAIL**

- [ ] **Step 3: Implement service functions**

`actualizar_categoria`: si `almacen_principal` cambia y la categoría tiene productos con existencias, lanzar `NecesitaTraslado(categoria, almacen_nuevo)` (excepción custom definida en `servicios/__init__.py`). Si no tiene productos, actualizar directamente.

- [ ] **Step 4: Run tests — expect PASS**

- [ ] **Step 5: Write failing tests for categoría views + implement views/templates**

USUARIO puede crear/editar categorías donde `almacen_principal` sea su `almacen_asignado`. Admin ve todas. La vista de edición captura `NecesitaTraslado` y redirige a `inventario:trasladar-categoria` (Task 5 — por ahora redirige a lista con mensaje de error informativo).

- [ ] **Step 6: Run regression + commit**

```powershell
..\venv\Scripts\python.exe manage.py test cuentas inventario integraciones.haku -v2
git add -A; git commit -m "feat(inventario): categoria CRUD con deteccion de traslado pendiente"
```

---

### Task 4: Producto CRUD + inicialización de stock

**Files:**
- Create: `inventario/servicios/stock.py`
- Modify: `inventario/servicios/datos_maestros.py`
- Modify: `inventario/forms.py`
- Modify: `inventario/views.py`
- Modify: `inventario/urls.py`
- Create: `inventario/templates/inventario/producto_lista.html`
- Create: `inventario/templates/inventario/producto_form.html`
- Create: `inventario/templates/inventario/producto_detalle.html`
- Test: `inventario/tests/test_productos.py`

**Interfaces:**
- Consumes: `crear_almacen`, `crear_ubicacion`, `crear_categoria`, `registrar_auditoria`, `AccesoAlmacenMixin`, `filtrar_por_almacen`
- Produces:
  - `crear_producto(*, nombre: str, categoria: Categoria, stock_inicial: int = 0, stock_minimo: int = 0, codigo_barras: str = "", nombre_empaque: str = "", unidades_por_empaque: int | None = None, descripcion: str = "", ubicaciones: list[int] = None, usuario, ip_origen=None) -> Producto`
  - `actualizar_producto(*, producto: Producto, nombre: str, stock_minimo: int, codigo_barras: str, nombre_empaque: str, unidades_por_empaque: int | None, descripcion: str, ubicaciones: list[int], usuario, ip_origen=None) -> Producto`
  - `archivar_producto(*, producto: Producto, usuario, ip_origen=None) -> None` — solo admin+
  - `inicializar_stock(*, producto: Producto, almacen: Almacen, cantidad: int, usuario, ip_origen=None) -> tuple[Existencia, Movimiento]`
  - `ProductoForm(ModelForm)` — fields: nombre, categoria, stock_inicial (solo en creación), stock_minimo, codigo_barras, nombre_empaque, unidades_por_empaque, descripcion
  - `ProductoUbicacionFormSet` — inline para asignar ubicaciones (filtradas por almacén)
  - URL names: `inventario:producto-lista`, `inventario:producto-crear`, `inventario:producto-editar`, `inventario:producto-detalle`, `inventario:producto-verificar-nombre`
  - `buscar_por_codigo_barras(codigo: str) -> Producto | None`
  - `verificar_nombre_similar(nombre: str, excluir_id: int = None) -> list[dict]` — retorna `[{codigo_interno, nombre}]` de productos con nombre similar

- [ ] **Step 1: Write failing tests for producto service + stock initialization**

```python
class TestCrearProducto(TestCase):
    def test_crea_producto_con_codigo_automatico(self):
        prod = crear_producto(nombre="Cable HDMI", categoria=self.cat_cables, usuario=self.admin)
        self.assertRegex(prod.codigo_interno, r"^CAB-\d{4}$")
        self.assertTrue(prod.activo)

    def test_stock_inicial_crea_existencia_y_movimiento(self):
        prod = crear_producto(nombre="Cable", categoria=self.cat_cables, stock_inicial=50, usuario=self.admin)
        ex = Existencia.objects.get(producto=prod, almacen=self.cat_cables.almacen_principal)
        self.assertEqual(ex.stock_disponible, 50)
        mov = Movimiento.objects.get(producto=prod, tipo=Movimiento.Tipo.MOVIMIENTO_INICIAL)
        self.assertEqual(mov.cantidad, 50)
        self.assertEqual(mov.stock_disponible_posterior, 50)

    def test_stock_inicial_cero_no_crea_movimiento(self):
        prod = crear_producto(nombre="Vacío", categoria=self.cat_cables, stock_inicial=0, usuario=self.admin)
        self.assertFalse(Movimiento.objects.filter(producto=prod).exists())
        # Existencia se crea con stock_disponible=0
        self.assertTrue(Existencia.objects.filter(producto=prod).exists())

    def test_codigo_barras_duplicado_falla(self):
        crear_producto(nombre="A", categoria=self.cat_cables, codigo_barras="123", usuario=self.admin)
        with self.assertRaises(ValidationError):
            crear_producto(nombre="B", categoria=self.cat_cables, codigo_barras="123", usuario=self.admin)

    def test_codigo_barras_vacio_permite_multiples(self):
        crear_producto(nombre="A", categoria=self.cat_cables, codigo_barras="", usuario=self.admin)
        prod2 = crear_producto(nombre="B", categoria=self.cat_cables, codigo_barras="", usuario=self.admin)
        self.assertIsNotNone(prod2.pk)

class TestInicializarStock(TestCase):
    def test_crea_existencia_y_movimiento_inicial_atomicamente(self):
        ex, mov = inicializar_stock(producto=self.prod, almacen=self.almacen, cantidad=100, usuario=self.admin)
        self.assertEqual(ex.stock_disponible, 100)
        self.assertEqual(mov.tipo, Movimiento.Tipo.MOVIMIENTO_INICIAL)
        self.assertEqual(mov.stock_disponible_anterior, 0)
        self.assertEqual(mov.stock_disponible_posterior, 100)

class TestActualizarProducto(TestCase):
    def test_no_puede_cambiar_codigo_interno(self):
        prod = crear_producto(nombre="A", categoria=self.cat_cables, usuario=self.admin)
        codigo_original = prod.codigo_interno
        actualizar_producto(producto=prod, nombre="B", stock_minimo=5, codigo_barras="",
                           nombre_empaque="", unidades_por_empaque=None, descripcion="",
                           ubicaciones=[], usuario=self.admin)
        prod.refresh_from_db()
        self.assertEqual(prod.codigo_interno, codigo_original)

    def test_no_puede_cambiar_stock_inicial(self):
        # stock_inicial es histórico e inmutable; actualizar_producto no lo acepta
        pass  # El campo no está en la firma de actualizar_producto

class TestArchivarProducto(TestCase):
    def test_usuario_normal_no_puede_archivar(self):
        with self.assertRaises(PermissionError):
            archivar_producto(producto=self.prod, usuario=self.usuario_normal)

    def test_admin_archiva_producto(self):
        archivar_producto(producto=self.prod, usuario=self.admin)
        self.prod.refresh_from_db()
        self.assertTrue(self.prod.archivado)
```

- [ ] **Step 2: Run tests — expect FAIL**

- [ ] **Step 3: Implement `inventario/servicios/stock.py`**

`inicializar_stock`: dentro de `transaction.atomic()`, crear o obtener `Existencia`, usar `Existencia.objects.select_for_update()` para evitar carreras, crear `Movimiento(tipo=MOVIMIENTO_INICIAL, ...)`, actualizar `stock_disponible` con `F()` + `update()` atómico, registrar auditoría.

- [ ] **Step 4: Implement `crear_producto` y `actualizar_producto` en `datos_maestros.py`**

`crear_producto`: guardar producto (dispara `save()` → genera código), si `stock_inicial > 0` llamar a `inicializar_stock()`, crear `ProductoUbicacion` por cada ubicación, auditar. `actualizar_producto`: capturar snapshot previo, guardar sin tocar `codigo_interno` ni `stock_inicial`, sincronizar ubicaciones, auditar.

- [ ] **Step 5: Run service tests — expect PASS**

- [ ] **Step 6: Write failing tests for producto views**

```python
class TestProductoVistas(TestCase):
    def test_lista_filtrada_por_almacen_del_usuario(self):
        self.client.force_login(self.usuario_normal)
        r = self.client.get(reverse("inventario:producto-lista"))
        # Solo ve productos con existencia en su almacén asignado

    def test_detalle_muestra_stock_por_almacen(self):
        self.client.force_login(self.admin)
        r = self.client.get(reverse("inventario:producto-detalle", args=[self.prod.pk]))
        self.assertContains(r, "Stock disponible")
        self.assertContains(r, self.prod.codigo_interno)

    def test_crear_producto_con_empaque_calcula_unidades(self):
        # El formulario muestra nombre_empaque y unidades_por_empaque como campos opcionales
        self.client.force_login(self.admin)
        data = {"nombre": "Tornillos", "categoria": self.cat.pk, "stock_inicial": 2,
                "stock_minimo": 10, "nombre_empaque": "Caja", "unidades_por_empaque": 50}
        # stock_inicial=2 significa 2 unidades base, no 2 cajas (la conversión de empaque
        # aplica en movimientos de entrada, Phase 4, no en stock_inicial)
        r = self.client.post(reverse("inventario:producto-crear"), data)
        self.assertEqual(r.status_code, 302)

    def test_busqueda_por_codigo_barras(self):
        self.client.force_login(self.admin)
        r = self.client.get(reverse("inventario:producto-lista"), {"buscar": "123456"})
        # Si existe un producto con ese código de barras, redirige a su detalle

    def test_advertencia_nombre_similar_al_crear(self):
        crear_producto(nombre="Cable HDMI 2m", categoria=self.cat, usuario=self.admin)
        self.client.force_login(self.admin)
        # GET con parámetro de nombre candidato → respuesta incluye advertencia
        r = self.client.get(reverse("inventario:producto-verificar-nombre"),
                            {"nombre": "Cable HDMI 2 metros"})
        self.assertEqual(r.status_code, 200)
        # Respuesta JSON con lista de similares
        self.assertIn("Cable HDMI 2m", r.json()["similares"])
```

- [ ] **Step 7: Implement ProductoForm, views, URLs, templates**

`ProductoForm`: `stock_inicial` solo se muestra en creación (en edición se excluye del form). `ProductoDetalle` muestra `Existencia` por almacén, historial de movimientos, alertas de stock bajo/sin stock. `ProductoListaView` filtra por almacén del usuario y ofrece búsqueda por nombre, código interno y código de barras. La búsqueda por código de barras exacto que encuentra match redirige directamente al detalle (§8.2). **Advertencia de nombres similares (§8.3):** endpoint AJAX `inventario:producto-verificar-nombre` que recibe `?nombre=X` y devuelve JSON con productos de nombre similar (usando `trigram_similar` o `icontains` como fallback en SQLite). El formulario de creación muestra la advertencia antes de enviar, permitiendo cancelar o confirmar. URL adicional: `inventario:producto-verificar-nombre`.

- [ ] **Step 8: Run regression + commit**

```powershell
..\venv\Scripts\python.exe manage.py test cuentas inventario integraciones.haku -v2
git add -A; git commit -m "feat(inventario): producto CRUD, inicializacion de stock y busqueda por codigo de barras"
```

---

### Task 5: Traslados estructurales

**Files:**
- Modify: `inventario/servicios/stock.py`
- Modify: `inventario/servicios/datos_maestros.py`
- Modify: `inventario/views.py`
- Modify: `inventario/urls.py`
- Create: `inventario/templates/inventario/trasladar_almacen.html`
- Create: `inventario/templates/inventario/trasladar_ubicacion.html`
- Create: `inventario/templates/inventario/trasladar_categoria.html`
- Test: `inventario/tests/test_traslados_estructurales.py`

**Interfaces:**
- Consumes: `crear_almacen`, `crear_ubicacion`, `crear_categoria`, `crear_producto`, `inicializar_stock`, `Existencia`, `ProductoUbicacion`, `Movimiento`
- Produces:
  - `trasladar_productos_almacen(*, almacen_origen: Almacen, almacen_destino: Almacen, usuario, ip_origen=None) -> int` — retorna cantidad de productos trasladados
  - `trasladar_productos_ubicacion(*, ubicacion_origen: Ubicacion, ubicacion_destino: Ubicacion, usuario, ip_origen=None) -> int`
  - `trasladar_productos_categoria(*, categoria: Categoria, almacen_destino: Almacen, usuario, ip_origen=None) -> int`
  - `desactivar_almacen(*, almacen: Almacen, almacen_destino: Almacen, usuario, ip_origen=None) -> None` — traslada todo y desactiva
  - `desactivar_ubicacion(*, ubicacion: Ubicacion, ubicacion_destino: Ubicacion, usuario, ip_origen=None) -> None`
  - `desactivar_categoria(*, categoria: Categoria, usuario, ip_origen=None) -> None` — solo si no tiene productos
  - `crear_existencia_excepcion(*, producto: Producto, almacen: Almacen, usuario, confirmacion: bool, motivo: str, ip_origen=None) -> Existencia` — §7.3 excepción categoría-almacén
  - URL names: `inventario:trasladar-almacen`, `inventario:trasladar-ubicacion`, `inventario:trasladar-categoria`, `inventario:almacen-desactivar`, `inventario:ubicacion-desactivar`, `inventario:categoria-desactivar`

- [ ] **Step 1: Write failing tests for traslados**

```python
class TestTrasladoAlmacen(TestCase):
    def test_traslada_existencias_y_registra_movimientos(self):
        # almacen_a tiene producto con 50 unidades
        n = trasladar_productos_almacen(almacen_origen=self.almacen_a,
                                         almacen_destino=self.almacen_b, usuario=self.admin)
        self.assertEqual(n, 1)
        ex_a = Existencia.objects.get(producto=self.prod, almacen=self.almacen_a)
        ex_b = Existencia.objects.get(producto=self.prod, almacen=self.almacen_b)
        self.assertEqual(ex_a.stock_disponible, 0)
        self.assertEqual(ex_b.stock_disponible, 50)
        self.assertTrue(Movimiento.objects.filter(tipo=Movimiento.Tipo.TRASLADO_ENTRE).exists())

    def test_no_puede_trasladar_a_mismo_almacen(self):
        with self.assertRaises(ValidationError):
            trasladar_productos_almacen(almacen_origen=self.almacen_a,
                                         almacen_destino=self.almacen_a, usuario=self.admin)

class TestDesactivarAlmacen(TestCase):
    def test_con_stock_exige_destino(self):
        with self.assertRaises(ValidationError):
            desactivar_almacen(almacen=self.almacen_a, almacen_destino=None, usuario=self.admin)

    def test_traslada_y_desactiva(self):
        desactivar_almacen(almacen=self.almacen_a, almacen_destino=self.almacen_b, usuario=self.admin)
        self.almacen_a.refresh_from_db()
        self.assertFalse(self.almacen_a.activo)
        self.assertEqual(Existencia.objects.get(producto=self.prod, almacen=self.almacen_a).stock_disponible, 0)

    def test_almacen_vacio_desactiva_sin_destino(self):
        # almacen sin productos ni existencias
        desactivar_almacen(almacen=self.almacen_vacio, almacen_destino=None, usuario=self.admin)
        self.almacen_vacio.refresh_from_db()
        self.assertFalse(self.almacen_vacio.activo)

    def test_actualiza_categorias_que_apuntan_al_almacen_desactivado(self):
        # categoria con almacen_principal = almacen_a → debe apuntar a almacen_b
        desactivar_almacen(almacen=self.almacen_a, almacen_destino=self.almacen_b, usuario=self.admin)
        self.cat.refresh_from_db()
        self.assertEqual(self.cat.almacen_principal, self.almacen_b)

class TestTrasladoUbicacion(TestCase):
    def test_reasigna_producto_ubicaciones(self):
        n = trasladar_productos_ubicacion(ubicacion_origen=self.ubic_a,
                                           ubicacion_destino=self.ubic_b, usuario=self.admin)
        self.assertGreaterEqual(n, 1)
        self.assertFalse(ProductoUbicacion.objects.filter(ubicacion=self.ubic_a).exists())
        self.assertTrue(ProductoUbicacion.objects.filter(ubicacion=self.ubic_b).exists())

    def test_destino_debe_ser_mismo_almacen(self):
        with self.assertRaises(ValidationError):
            trasladar_productos_ubicacion(ubicacion_origen=self.ubic_a,
                                           ubicacion_destino=self.ubic_otro_almacen, usuario=self.admin)

class TestTrasladoCategoria(TestCase):
    def test_traslada_existencias_al_nuevo_almacen_y_actualiza_categoria(self):
        n = trasladar_productos_categoria(categoria=self.cat, almacen_destino=self.almacen_b, usuario=self.admin)
        self.assertGreaterEqual(n, 1)
        self.cat.refresh_from_db()
        self.assertEqual(self.cat.almacen_principal, self.almacen_b)
```

- [ ] **Step 2: Run tests — expect FAIL**

- [ ] **Step 3: Implement traslado functions in `stock.py`**

Cada traslado: `transaction.atomic()`, `select_for_update()` sobre existencias, crear pares de `Movimiento(TRASLADO_ENTRE)` (origen y destino), actualizar `ProductoUbicacion`, auditar cada producto trasladado. `desactivar_almacen` también actualiza `Categoria.almacen_principal` de categorías que apunten al almacén desactivado. `trasladar_productos_ubicacion` valida que origen y destino estén en el mismo almacén.

- [ ] **Step 4: Run tests — expect PASS**

- [ ] **Step 5: Write view tests + implement views/templates para flujo de desactivación**

La vista de desactivar almacén: GET muestra formulario con selector de almacén destino + resumen de productos afectados. POST ejecuta `desactivar_almacen()`. Solo admin+. La vista de trasladar categoría se integra con el flujo de edición de categoría (cuando se captura `NecesitaTraslado`). Templates muestran resumen claro de qué se va a mover y ofrecen botón Cancelar.

- [ ] **Step 6: Run regression + commit**

```powershell
..\venv\Scripts\python.exe manage.py test cuentas inventario integraciones.haku -v2
git add -A; git commit -m "feat(inventario): traslados estructurales obligatorios al desactivar almacen/ubicacion/categoria"
```

---

### Task 6: Fusión de productos duplicados

**Files:**
- Modify: `inventario/servicios/datos_maestros.py`
- Modify: `inventario/views.py`
- Modify: `inventario/urls.py`
- Create: `inventario/templates/inventario/producto_fusionar.html`
- Test: `inventario/tests/test_fusion_productos.py`

**Interfaces:**
- Consumes: `crear_producto`, `inicializar_stock`, `Existencia`, `Movimiento`, `ProductoUbicacion`, `Prestamo`, `registrar_auditoria`
- Produces:
  - `fusionar_productos(*, producto_principal: Producto, producto_duplicado: Producto, usuario, ip_origen=None) -> Producto` — retorna el producto principal actualizado
  - URL name: `inventario:producto-fusionar`

- [ ] **Step 1: Write failing tests for fusión**

```python
class TestFusionarProductos(TestCase):
    def test_suma_existencias_al_principal(self):
        fusionar_productos(producto_principal=self.prod_a, producto_duplicado=self.prod_b,
                          usuario=self.admin)
        ex = Existencia.objects.get(producto=self.prod_a, almacen=self.almacen)
        self.assertEqual(ex.stock_disponible, 50 + 30)  # prod_a tenía 50, prod_b tenía 30

    def test_reasigna_movimientos_al_principal(self):
        fusionar_productos(producto_principal=self.prod_a, producto_duplicado=self.prod_b,
                          usuario=self.admin)
        self.assertFalse(Movimiento.objects.filter(producto=self.prod_b).exists())
        self.assertTrue(Movimiento.objects.filter(producto=self.prod_a).count() >= 2)

    def test_duplicado_queda_archivado(self):
        fusionar_productos(producto_principal=self.prod_a, producto_duplicado=self.prod_b,
                          usuario=self.admin)
        self.prod_b.refresh_from_db()
        self.assertTrue(self.prod_b.archivado)
        self.assertFalse(self.prod_b.activo)

    def test_usuario_normal_no_puede_fusionar(self):
        with self.assertRaises(PermissionError):
            fusionar_productos(producto_principal=self.prod_a, producto_duplicado=self.prod_b,
                              usuario=self.usuario_normal)

    def test_no_fusionar_consigo_mismo(self):
        with self.assertRaises(ValidationError):
            fusionar_productos(producto_principal=self.prod_a, producto_duplicado=self.prod_a,
                              usuario=self.admin)

    def test_auditoria_detallada(self):
        fusionar_productos(producto_principal=self.prod_a, producto_duplicado=self.prod_b,
                          usuario=self.admin)
        aud = Auditoria.objects.filter(accion="fusionar").first()
        self.assertIn("prod_b", str(aud.valores_anteriores))
        self.assertEqual(aud.tipo_entidad, "Producto")

    def test_reasigna_prestamos_activos(self):
        # Si prod_b tiene préstamos abiertos, se reasignan a prod_a
        fusionar_productos(producto_principal=self.prod_a, producto_duplicado=self.prod_b,
                          usuario=self.admin)
        self.assertFalse(Prestamo.objects.filter(producto=self.prod_b).exists())

    def test_unifica_ubicaciones_sin_duplicar(self):
        # prod_a en ubic_1, prod_b en ubic_1 y ubic_2 → prod_a en ubic_1 y ubic_2
        fusionar_productos(producto_principal=self.prod_a, producto_duplicado=self.prod_b,
                          usuario=self.admin)
        ubics = set(ProductoUbicacion.objects.filter(producto=self.prod_a)
                    .values_list("ubicacion_id", flat=True))
        self.assertEqual(ubics, {self.ubic_1.pk, self.ubic_2.pk})
```

- [ ] **Step 2: Run tests — expect FAIL**

- [ ] **Step 3: Implement `fusionar_productos` en `datos_maestros.py`**

Dentro de `transaction.atomic()`:
1. Validar que sean productos distintos y que el usuario es admin+
2. `select_for_update()` ambas existencias
3. Sumar `stock_disponible` y `stock_en_prestamo` del duplicado al principal
4. `Movimiento.objects.filter(producto=duplicado).update(producto=principal)`
5. `Prestamo.objects.filter(producto=duplicado).update(producto=principal)`
6. `DevolucionPrestamo` se reasigna a través de la FK a Prestamo (cascade)
7. `ProductoUbicacion`: crear las que no existan en principal, eliminar las del duplicado
8. Marcar duplicado como `archivado=True, activo=False`
9. Poner existencia del duplicado a 0
10. Auditar con snapshot completo: producto principal, duplicado, cantidades movidas

- [ ] **Step 4: Run tests — expect PASS**

- [ ] **Step 5: Write view tests + implement vista de fusión**

GET: formulario con selector del producto duplicado (excluye el principal y los archivados). Muestra resumen: stock que se sumará, movimientos que se reasignarán, préstamos activos. POST: ejecuta fusión. Solo admin+ (`RolRequeridoMixin`).

- [ ] **Step 6: Run regression + commit**

```powershell
..\venv\Scripts\python.exe manage.py test cuentas inventario integraciones.haku -v2
git add -A; git commit -m "feat(inventario): fusion de productos duplicados con reasignacion de historial"
```

---

## Checklist final de la Fase 3

Antes de reportar la fase como completa:

- [ ] `manage.py check` sin errores ni warnings (excepto los esperados)
- [ ] `manage.py makemigrations --check --dry-run` limpio (no hay migraciones pendientes)
- [ ] Regresión completa: `manage.py test cuentas inventario integraciones.haku` — todos pasan
- [ ] Los 62 tests previos siguen pasando
- [ ] `SILENCED_SYSTEM_CHECKS` revisado — retirar si los errores de admin.py ya están corregidos
- [ ] Ningún archivo modificado fuera de `inventario/`, `templates/` y `inventario_audacia/settings.py`
- [ ] Toda mutación registra auditoría con valores anteriores y nuevos
- [ ] Permisos por almacén verificados: USUARIO no accede a datos de otro almacén
