"""Pruebas unitarias y de integración para las 10 mejoras de inventario y cuentas (v2)."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from cuentas.forms import UsuarioForm
from inventario.forms import ConteoFisicoForm, ProductoForm, TrasladoForm
from inventario.models import Almacen, Categoria, Existencia, Prestamo, Producto, ProductoUbicacion, Ubicacion
from inventario.servicios.datos_maestros import crear_almacen, crear_categoria, crear_producto, crear_ubicacion
from inventario.servicios.operaciones import registrar_prestamo, registrar_traslado_entre_almacenes, registrar_traslado_interno

Usuario = get_user_model()


class MejorasV2TestCase(TestCase):
    def setUp(self):
        super().setUp()
        self.superadmin = Usuario.objects.create_superuser(
            username="super_v2",
            email="super@audacia.local",
            password="Password123!",
            nombre_completo="Superusuario V2",
            rol=Usuario.Rol.SUPERUSUARIO,
        )
        self.admin = Usuario.objects.create_user(
            username="admin_v2",
            email="admin@audacia.local",
            password="Password123!",
            nombre_completo="Admin V2",
            rol=Usuario.Rol.ADMINISTRADOR,
            creado_por=self.superadmin,
        )
        self.almacen_a = crear_almacen(nombre="Almacen A V2", usuario=self.superadmin)
        self.almacen_b = crear_almacen(nombre="Almacen B V2", usuario=self.superadmin)

        self.ubicacion_a1 = crear_ubicacion(nombre="Estante A-1", almacen=self.almacen_a, usuario=self.superadmin)
        self.ubicacion_a2 = crear_ubicacion(nombre="Estante A-2", almacen=self.almacen_a, usuario=self.superadmin)
        self.ubicacion_b1 = crear_ubicacion(nombre="Estante B-1", almacen=self.almacen_b, usuario=self.superadmin)

        self.categoria_a = crear_categoria(nombre="Ferretería V2", almacen_principal=self.almacen_a, usuario=self.superadmin)

        self.usuario = Usuario.objects.create_user(
            username="user_v2",
            email="user@audacia.local",
            password="Password123!",
            nombre_completo="Usuario Normal V2",
            rol=Usuario.Rol.USUARIO,
            creado_por=self.admin,
            almacen_asignado=self.almacen_a,
        )

        self.producto = crear_producto(
            nombre="Taladro Percutor V2",
            categoria=self.categoria_a,
            stock_inicial=10,
            nombre_empaque="Caja",
            unidades_por_empaque=5,
            ubicaciones=[self.ubicacion_a1.id],
            usuario=self.superadmin,
        )

    def test_punto_7_producto_form_exige_ubicacion_obligatoria(self):
        """ProductoForm debe rechazar creación o edición sin ubicación física."""
        form_invalido = ProductoForm(
            data={
                "nombre": "Producto Sin Ubicacion",
                "almacen": self.almacen_a.id,
                "categoria": self.categoria_a.id,
                "stock_inicial": 5,
                "stock_minimo": 1,
                "ubicacion": "",
            },
            usuario=self.superadmin,
        )
        self.assertFalse(form_invalido.is_valid())
        self.assertIn("ubicacion", form_invalido.errors)

        form_valido = ProductoForm(
            data={
                "nombre": "Producto Con Ubicacion",
                "almacen": self.almacen_a.id,
                "categoria": self.categoria_a.id,
                "stock_inicial": 5,
                "stock_minimo": 1,
                "ubicacion": self.ubicacion_a1.id,
            },
            usuario=self.superadmin,
        )
        self.assertTrue(form_valido.is_valid(), form_valido.errors)

    def test_punto_6_no_se_puede_desasignar_unico_usuario_de_almacen_con_stock(self):
        """No permite desasignar al único usuario asignado a un almacén si tiene existencias."""
        form = UsuarioForm(
            data={
                "username": self.usuario.username,
                "nombre_completo": self.usuario.nombre_completo,
                "rol": Usuario.Rol.USUARIO,
                "almacenes_asignados": [self.almacen_b.id],  # Quita Almacen A (que tiene stock) y deja Almacen B
            },
            instance=self.usuario,
            request_user=self.admin,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("almacenes_asignados", form.errors)
        error_msg = str(form.errors["almacenes_asignados"])
        self.assertIn("no hay ningún otro usuario asignado", error_msg)

    def test_punto_3_traslado_form_valida_motivo_y_reubicacion_interna(self):
        """TrasladoForm exige motivo obligatorio y reconoce traslado interno si cambia ubicación origen."""
        # Sin motivo es inválido
        form_sin_motivo = TrasladoForm(
            data={
                "producto": self.producto.id,
                "almacen_origen": self.almacen_a.id,
                "ubicacion_origen": self.ubicacion_a2.id,
                "motivo": "",
            },
            usuario=self.superadmin,
        )
        self.assertFalse(form_sin_motivo.is_valid())
        self.assertIn("motivo", form_sin_motivo.errors)

        # Con motivo y cambio de ubicación interna es válido
        form_interno = TrasladoForm(
            data={
                "producto": self.producto.id,
                "almacen_origen": self.almacen_a.id,
                "ubicacion_origen": self.ubicacion_a2.id,
                "motivo": "Traslado interno a estante A-2",
            },
            usuario=self.superadmin,
        )
        self.assertTrue(form_interno.is_valid(), form_interno.errors)
        # La cantidad por defecto se autocompleta con todo el stock disponible
        self.assertEqual(form_interno.cleaned_data["cantidad"], 10)

    def test_punto_3_traslado_entre_almacenes_exige_almacen_y_ubicacion_destino(self):
        """Traslado a otro almacén requiere almacén destino, ubicación destino y categoría destino."""
        cat_b = Categoria.objects.filter(almacen_principal=self.almacen_b).first()
        form_entre = TrasladoForm(
            data={
                "producto": self.producto.id,
                "almacen_origen": self.almacen_a.id,
                "ubicacion_origen": self.ubicacion_a1.id,  # Mantiene la actual
                "almacen_destino": self.almacen_b.id,
                "ubicacion_destino": self.ubicacion_b1.id,
                "categoria_destino": cat_b.id,
                "motivo": "Traslado entre bodegas",
            },
            usuario=self.superadmin,
        )
        self.assertTrue(form_entre.is_valid(), form_entre.errors)
        self.assertEqual(form_entre.cleaned_data["cantidad"], 10)

    def test_punto_1_perfil_username_solo_editable_por_superusuario(self):
        """Un superusuario puede editar username en perfil; un usuario normal no puede."""
        self.client.force_login(self.superadmin)
        resp_super = self.client.post(
            reverse("inventario:perfil-actualizar-nombre"),
            {"nombre_completo": "Super Editado", "username": "super_editado"},
        )
        self.assertEqual(resp_super.status_code, 302)
        self.superadmin.refresh_from_db()
        self.assertEqual(self.superadmin.username, "super_editado")

        self.client.force_login(self.usuario)
        # Intento de cambiar username por usuario no superusuario es rechazado
        resp_intento = self.client.post(
            reverse("inventario:perfil-actualizar-nombre"),
            {"nombre_completo": "Usuario Normal Editado", "username": "intento_hack"},
        )
        self.assertEqual(resp_intento.status_code, 302)
        self.usuario.refresh_from_db()
        self.assertEqual(self.usuario.username, "user_v2")

        # Modificación de nombre manteniendo el mismo username es permitida
        resp_normal = self.client.post(
            reverse("inventario:perfil-actualizar-nombre"),
            {"nombre_completo": "Usuario Normal Editado", "username": "user_v2"},
        )
        self.assertEqual(resp_normal.status_code, 302)
        self.usuario.refresh_from_db()
        self.assertEqual(self.usuario.nombre_completo, "Usuario Normal Editado")

    def test_punto_10_mapa_ubicaciones_incluye_informacion_empaque(self):
        """MovimientoListaView debe entregar en el JSON el nombre y factor de unidades por empaque."""
        self.client.force_login(self.superadmin)
        resp = self.client.get(reverse("inventario:movimiento-lista"))
        self.assertEqual(resp.status_code, 200)
        mapa_json = resp.context["mapa_ubicaciones_json"]
        self.assertIn('"nombre_empaque": "Caja"', mapa_json)
        self.assertIn('"unidades_por_empaque": 5', mapa_json)

    def test_usuario_normal_con_un_solo_almacen_restringido_a_traslado_interno(self):
        """Usuario normal con 1 almacén asignado: destino oculto/vacío y solo reubicación interna permitida."""
        form = TrasladoForm(usuario=self.usuario)
        # almacen_destino debe estar deshabilitado/vacío
        self.assertEqual(form.fields["almacen_destino"].queryset.count(), 0)
        self.assertTrue(form.fields["almacen_destino"].widget.is_hidden)

        # Intento de transferir a almacén externo debe ser rechazado
        form_intento = TrasladoForm(
            data={
                "producto": self.producto.id,
                "almacen_origen": self.almacen_a.id,
                "almacen_destino": self.almacen_b.id,
                "ubicacion_origen": self.ubicacion_a1.id,
                "motivo": "Intento de enviar a otro almacén",
            },
            usuario=self.usuario,
        )
        self.assertFalse(form_intento.is_valid())
        self.assertIn("almacen_destino", form_intento.errors)

        # Traslado interno de ubicación dentro de su almacén sí es válido
        form_interno_ok = TrasladoForm(
            data={
                "producto": self.producto.id,
                "almacen_origen": self.almacen_a.id,
                "ubicacion_origen": self.ubicacion_a2.id,
                "motivo": "Reubicación al estante A-2",
            },
            usuario=self.usuario,
        )
        self.assertTrue(form_interno_ok.is_valid(), form_interno_ok.errors)
        self.assertIsNone(form_interno_ok.cleaned_data["almacen_destino"])

    def test_usuario_normal_con_multiples_almacenes_solo_ve_sus_almacenes(self):
        """Usuario normal con varios almacenes asignados solo ve sus almacenes en destino (no los ajenos)."""
        almacen_c = crear_almacen(nombre="Almacen C Ajeno", usuario=self.superadmin)
        self.usuario.almacenes_asignados.add(self.almacen_b)

        self.assertEqual(self.usuario.get_almacenes_asignados().count(), 2)
        form = TrasladoForm(usuario=self.usuario)

        # almacen_destino solo debe contener almacenes asignados al usuario (Almacen A y B), no Almacen C
        dest_alms = list(form.fields["almacen_destino"].queryset.values_list("id", flat=True))
        self.assertIn(self.almacen_a.id, dest_alms)
        self.assertIn(self.almacen_b.id, dest_alms)
        self.assertNotIn(almacen_c.id, dest_alms)

        # Intento de transferir al almacén ajeno C es rechazado
        form_intento = TrasladoForm(
            data={
                "producto": self.producto.id,
                "almacen_origen": self.almacen_a.id,
                "almacen_destino": almacen_c.id,
                "motivo": "Intento transferir a almacén ajeno",
            },
            usuario=self.usuario,
        )
        self.assertFalse(form_intento.is_valid())
        self.assertIn("almacen_destino", form_intento.errors)

    def test_movimiento_lista_contexto_y_acotamiento_para_usuario_normal(self):
        """MovimientoListaView pasa banderas de usuario único y acota productos del JSON al almacén asignado."""
        import json
        # Creamos un producto en el almacén B (al que el usuario no tiene acceso)
        cat_b = crear_categoria(nombre="Química B", almacen_principal=self.almacen_b, usuario=self.superadmin)
        prod_b = crear_producto(nombre="Solvente B", categoria=cat_b, stock_inicial=5, usuario=self.superadmin)

        self.client.force_login(self.usuario)
        resp = self.client.get(reverse("inventario:movimiento-lista"))
        self.assertEqual(resp.status_code, 200)

        # Banderas de contexto
        self.assertTrue(resp.context["es_usuario_normal"])
        self.assertEqual(resp.context["usuario_almacenes_count"], 1)
        self.assertEqual(resp.context["usuario_almacen_unico_id"], str(self.almacen_a.id))
        self.assertEqual(resp.context["usuario_almacen_unico_nombre"], self.almacen_a.nombre)

        # El JSON de mapa_ubicaciones solo contiene el producto de su almacén A, NO el producto B
        mapa_dict = json.loads(resp.context["mapa_ubicaciones_json"])
        self.assertIn(str(self.producto.id), mapa_dict)
        self.assertNotIn(str(prod_b.id), mapa_dict)

    def test_crear_almacen_autocrea_categoria_general(self):
        """Crear un almacén debe auto-generar la categoría por defecto 'General - [Nombre]'."""
        nuevo_alm = crear_almacen(nombre="Almacen Automatizado", usuario=self.superadmin)
        cat_auto = Categoria.objects.filter(almacen_principal=nuevo_alm, nombre=f"General - {nuevo_alm.nombre}").first()
        self.assertIsNotNone(cat_auto)
        self.assertTrue(cat_auto.activo)

    def test_traslado_entre_almacenes_elimina_existencia_origen_invariante_unico(self):
        """Al trasladar existencias a otro almacén, la existencia origen con 0 stock es eliminada para cumplir invariante."""
        cat_dest = Categoria.objects.filter(almacen_principal=self.almacen_b).first()
        ex_o, ex_d, mov = registrar_traslado_entre_almacenes(
            producto=self.producto,
            almacen_origen=self.almacen_a,
            almacen_destino=self.almacen_b,
            cantidad=10,
            usuario=self.superadmin,
            ubicacion_destino=self.ubicacion_b1,
            categoria_destino=cat_dest,
        )
        self.assertEqual(ex_d.stock_disponible, 10)
        # La existencia en el almacén origen con 0 unidades debe eliminarse
        self.assertFalse(Existencia.objects.filter(producto=self.producto, almacen=self.almacen_a).exists())
        # El producto ahora pertenece a la categoría y almacén de destino
        self.producto.refresh_from_db()
        self.assertEqual(self.producto.categoria, cat_dest)
        self.assertEqual(self.producto.ubicaciones.first(), self.ubicacion_b1)

    def test_asistente_traslado_ubicacion_vista(self):
        """UbicacionTrasladarView permite mover la ubicación a otro almacén reasignando categorías a los productos."""
        self.client.force_login(self.superadmin)
        url = reverse("inventario:trasladar-ubicacion", args=[self.ubicacion_a1.pk])
        resp_get = self.client.get(url)
        self.assertEqual(resp_get.status_code, 200)
        self.assertContains(resp_get, self.ubicacion_a1.nombre)

        cat_b = Categoria.objects.filter(almacen_principal=self.almacen_b).first()
        resp_post = self.client.post(
            url,
            {
                "almacen_destino": self.almacen_b.pk,
                f"categoria_producto_{self.producto.id}": cat_b.pk,
            },
        )
        self.assertEqual(resp_post.status_code, 302)
        self.ubicacion_a1.refresh_from_db()
        self.assertEqual(self.ubicacion_a1.almacen, self.almacen_b)
        self.producto.refresh_from_db()
        self.assertEqual(self.producto.categoria, cat_b)

    def test_asistente_traslado_categoria_vista(self):
        """CategoriaTrasladarView permite mover la categoría a otro almacén reasignando ubicaciones a los productos."""
        self.client.force_login(self.superadmin)
        url = reverse("inventario:categoria-trasladar", args=[self.categoria_a.pk])
        resp_get = self.client.get(url)
        self.assertEqual(resp_get.status_code, 200)
        self.assertContains(resp_get, self.categoria_a.nombre)

        resp_post = self.client.post(
            url,
            {
                "almacen_destino": self.almacen_b.pk,
                f"ubicacion_producto_{self.producto.id}": self.ubicacion_b1.pk,
            },
        )
        self.assertEqual(resp_post.status_code, 302)
        self.categoria_a.refresh_from_db()
        self.assertEqual(self.categoria_a.almacen_principal, self.almacen_b)
        self.producto.refresh_from_db()
        self.assertEqual(self.producto.ubicaciones.first(), self.ubicacion_b1)

    def test_movimiento_lista_filtros_fecha_y_almacen(self):
        """MovimientoListaView filtra correctamente por rango de fechas y almacén."""
        from django.utils import timezone
        hoy = timezone.localdate().strftime("%Y-%m-%d")
        self.client.force_login(self.superadmin)
        resp = self.client.get(reverse("inventario:movimiento-lista"), {
            "fecha_desde": hoy,
            "fecha_hasta": hoy,
            "almacen_id": str(self.almacen_a.id),
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["fecha_desde"], hoy)
        self.assertEqual(resp.context["fecha_hasta"], hoy)
        self.assertEqual(resp.context["almacen_id"], str(self.almacen_a.id))

    def test_editar_ubicacion_con_productos_captura_necesita_traslado_y_muestra_enlace_asistente(self):
        """Al editar una ubicación con productos y cambiar de almacén, debe responder con el enlace al Asistente de Traslado."""
        self.client.force_login(self.superadmin)
        url = reverse("inventario:ubicacion-editar", args=[self.ubicacion_a1.pk])
        resp = self.client.post(
            url,
            {
                "nombre": self.ubicacion_a1.nombre,
                "descripcion": self.ubicacion_a1.descripcion,
                "almacen": self.almacen_b.pk,
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertFalse(data["success"])
        self.assertIn("almacen", data["errors"])
        error_msg = data["errors"]["almacen"][0]
        self.assertIn("Asistente de Traslado de Ubicación", error_msg)
        self.assertIn(f"/inventario/ubicaciones/{self.ubicacion_a1.pk}/trasladar/", error_msg)

    def test_usuario_normal_bloqueado_en_traslado_y_vista_traslado(self):
        """Los usuarios estándar no deben tener acceso a realizar traslados ni ver la pestaña de traslados."""
        self.client.force_login(self.usuario)
        # GET a endpoint de traslado -> 403
        resp_get = self.client.get(reverse("inventario:movimiento-traslado"))
        self.assertEqual(resp_get.status_code, 403)

        # POST a endpoint de traslado -> 403
        resp_post = self.client.post(
            reverse("inventario:movimiento-traslado"),
            data={"producto": self.producto.id, "motivo": "Intento no autorizado"},
        )
        self.assertEqual(resp_post.status_code, 403)

        # GET a vista=TRASLADO en movimientos -> 403
        resp_vista = self.client.get(reverse("inventario:movimiento-lista") + "?vista=TRASLADO")
        self.assertEqual(resp_vista.status_code, 403)

    def test_administrador_autorizado_en_traslado(self):
        """Los administradores y superusuarios pueden acceder al registro y vista de traslados."""
        self.client.force_login(self.admin)
        resp_get = self.client.get(reverse("inventario:movimiento-traslado"))
        self.assertEqual(resp_get.status_code, 200)

        resp_vista = self.client.get(reverse("inventario:movimiento-lista") + "?vista=TRASLADO")
        self.assertEqual(resp_vista.status_code, 200)

    def test_prestamo_abierto_muestra_badge_prestamo_no_cerrado(self):
        """Un préstamo en estado ABIERTO debe mostrar la etiqueta 'Préstamo' y las acciones de Devolver y Baja."""
        p_obj, mov, _ = registrar_prestamo(
            producto=self.producto,
            almacen=self.almacen_a,
            cantidad=3,
            destinatario="Prof. Carlos Gómez",
            motivo="Práctica de robótica",
            usuario=self.superadmin,
        )
        self.assertEqual(p_obj.estado, Prestamo.Estado.ABIERTO)
        self.assertEqual(p_obj.cantidad_pendiente_devolucion, 3)

        self.client.force_login(self.admin)
        resp = self.client.get(reverse("inventario:movimiento-lista") + "?vista=PRESTAMOS")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '<span class="badge badge-purple">Préstamo</span>')
        self.assertContains(resp, "Devolver")
        self.assertContains(resp, "Baja daño")
        # No debe figurar como Cerrado en la columna de estado
        self.assertNotContains(resp, '<span class="badge badge-ok">Cerrado</span>')

    def test_selector_movimiento_responsivo_segun_rol(self):
        """El selector de tipo de movimiento debe aplicar cols-4 para usuario normal y cols-5 para admin."""
        self.client.force_login(self.usuario)
        resp_user = self.client.get(reverse("inventario:movimiento-lista"))
        self.assertContains(resp_user, "mov-type-selector cols-4")

        self.client.force_login(self.admin)
        resp_admin = self.client.get(reverse("inventario:movimiento-lista"))
        self.assertContains(resp_admin, "mov-type-selector cols-5")

    def test_desactivar_categoria_con_productos_mensaje_sin_corchetes(self):
        """Al fallar la desactivación de categoría con productos, el mensaje no debe contener corchetes ni comillas."""
        self.client.force_login(self.admin)
        resp = self.client.post(
            reverse("inventario:categoria-desactivar", kwargs={"pk": self.categoria_a.pk}),
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        messages_list = list(resp.context["messages"])
        self.assertTrue(len(messages_list) > 0)
        error_msg = str(messages_list[0])
        self.assertIn("No se puede desactivar una categoría con productos activos asociados.", error_msg)
        self.assertNotIn("['", error_msg)
        self.assertNotIn("']", error_msg)

    def test_dashboard_enlaces_tarjetas(self):
        """El dashboard debe enlazar catálogo, sin stock, stock bajo y préstamos con persistencia de almacén."""
        self.client.force_login(self.admin)

        # 1. Sin filtro de almacén
        resp = self.client.get(reverse("web:dashboard"))
        self.assertEqual(resp.status_code, 200)

        # Tarjeta Catálogo tiene enlace
        self.assertContains(resp, reverse("inventario:producto-lista"))
        self.assertContains(resp, 'title="Ver catálogo de productos"')

        # Tarjetas Sin Stock y Stock Bajo
        self.assertContains(resp, reverse("inventario:alertas-lista") + "?nivel=CRITICA")
        self.assertContains(resp, reverse("inventario:alertas-lista") + "?nivel=BAJA")

        # Tarjeta Préstamo tiene enlace a vista=PRESTAMOS
        self.assertContains(resp, "vista=PRESTAMOS")
        self.assertContains(resp, 'title="Ver tabla de préstamos"')

        # Tarjeta Stock Disponible NO tiene enlace propio
        self.assertNotContains(resp, 'title="Ver stock disponible"')

        # 2. Con almacén seleccionado (persistencia de almacén)
        resp_alm = self.client.get(reverse("web:dashboard") + f"?almacen_id={self.almacen_a.pk}")
        self.assertEqual(resp_alm.status_code, 200)
        self.assertContains(resp_alm, f"{reverse('inventario:producto-lista')}?almacen_id={self.almacen_a.pk}")
        self.assertContains(resp_alm, f"{reverse('inventario:alertas-lista')}?nivel=CRITICA&almacen_id={self.almacen_a.pk}")
        self.assertContains(resp_alm, f"{reverse('inventario:alertas-lista')}?nivel=BAJA&almacen_id={self.almacen_a.pk}")
        self.assertContains(resp_alm, f"{reverse('inventario:movimiento-lista')}?vista=PRESTAMOS&almacen_id={self.almacen_a.pk}")

    def test_seccion_categorias_y_almacenes_y_enlaces_filtros(self):
        """Las vistas deben titularse 'Categorías y Almacenes', enlazar al catálogo con filtros y mostrar conteo de artículos."""
        self.client.force_login(self.admin)

        # 1. Categorías y Tipos
        resp_cat = self.client.get(reverse("inventario:categoria-lista"))
        self.assertEqual(resp_cat.status_code, 200)
        self.assertContains(resp_cat, "Categorías y Almacenes")
        self.assertContains(resp_cat, f"/inventario/productos/?almacen_id={self.almacen_a.pk}&categoria_id={self.categoria_a.pk}")
        self.assertContains(resp_cat, "artículo(s)")

        # 2. Ubicaciones
        resp_ub = self.client.get(reverse("inventario:ubicacion-lista"))
        self.assertEqual(resp_ub.status_code, 200)
        self.assertContains(resp_ub, "Categorías y Almacenes")
        self.assertContains(resp_ub, f"/inventario/productos/?almacen_id={self.almacen_a.pk}&ubicacion_id={self.ubicacion_a1.pk}")
        self.assertContains(resp_ub, "artículo(s)")

        # 3. Almacenes
        resp_alm = self.client.get(reverse("inventario:almacen-lista"))
        self.assertEqual(resp_alm.status_code, 200)
        self.assertContains(resp_alm, "Categorías y Almacenes")
        self.assertContains(resp_alm, f"/inventario/productos/?almacen_id={self.almacen_a.pk}")
        self.assertContains(resp_alm, "artículo(s)")

    def test_persistencia_notificaciones_leidas_al_cerrar_sesion(self):
        """Las notificaciones leídas deben persistir en base de datos al cerrar sesión y volver a iniciar sesión."""
        from inventario.models import AlertaStockLeida
        from inventario.servicios.datos_maestros import crear_producto

        # Creamos un producto con stock 0 y stock_minimo 10 para generar alerta crítica
        p_agotado = crear_producto(
            nombre="Alambre de Cobre",
            categoria=self.categoria_a,
            stock_inicial=0,
            stock_minimo=10,
            ubicaciones=[self.ubicacion_a1.id],
            usuario=self.superadmin,
        )

        self.client.force_login(self.admin)
        resp_inicial = self.client.get(reverse("inventario:alertas-lista"))
        self.assertEqual(resp_inicial.status_code, 200)

        clave = f"{p_agotado.pk}:{self.almacen_a.pk}:0"

        # Marcar como leída vía POST
        resp_post = self.client.post(
            reverse("inventario:alerta-marcar-leida"),
            data={"key": clave, "accion": "una"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(resp_post.status_code, 200)

        # Verificar persistencia en modelo AlertaStockLeida
        self.assertTrue(
            AlertaStockLeida.objects.filter(usuario=self.admin, clave=clave).exists()
        )

        # Simular cierre de sesión completo
        self.client.logout()

        # Iniciar sesión nuevamente con el mismo usuario
        self.client.force_login(self.admin)
        resp_despues = self.client.get(reverse("inventario:alertas-lista"))
        self.assertEqual(resp_despues.status_code, 200)

        # En context processor o vista, la alerta debe permanecer leída
        alertas = resp_despues.context["alertas"]
        self.assertEqual(alertas["total_no_leidas"], 0)

    def test_alertas_stock_filtros_nivel_y_almacen(self):
        """La vista de alertas de stock debe permitir filtrar por almacén, nivel (crítica/baja) y estado de lectura."""
        self.client.force_login(self.admin)

        # Crear producto crítico (stock 0) en almacén A
        prod_crit = crear_producto(
            nombre="Tornillos Acero Crit",
            categoria=self.categoria_a,
            stock_inicial=0,
            ubicaciones=[self.ubicacion_a1.id],
            stock_minimo=5,
            usuario=self.superadmin,
        )

        # Crear producto con stock bajo en almacén B
        cat_b = crear_categoria(nombre="Ferretería B", almacen_principal=self.almacen_b, usuario=self.superadmin)
        prod_bajo = crear_producto(
            nombre="Lija Madera Bajo",
            categoria=cat_b,
            stock_inicial=2,
            ubicaciones=[self.ubicacion_b1.id],
            stock_minimo=10,
            usuario=self.superadmin,
        )

        # 1. Sin filtros: ambas alertas presentes en el contexto y formulario con filtros disponible
        resp = self.client.get(reverse("inventario:alertas-lista"))
        self.assertEqual(resp.status_code, 200)
        nombres_todas = [n.producto.nombre for n in resp.context["notificaciones"]]
        self.assertIn("Tornillos Acero Crit", nombres_todas)
        self.assertIn("Lija Madera Bajo", nombres_todas)
        self.assertContains(resp, 'name="almacen_id"')
        self.assertContains(resp, 'name="nivel"')
        self.assertContains(resp, 'name="estado"')

        # 2. Filtrar por almacén A: solo Tornillos Acero Crit en notificaciones
        resp_alm_a = self.client.get(reverse("inventario:alertas-lista") + f"?almacen_id={self.almacen_a.pk}")
        self.assertEqual(resp_alm_a.status_code, 200)
        nombres_alm_a = [n.producto.nombre for n in resp_alm_a.context["notificaciones"]]
        self.assertIn("Tornillos Acero Crit", nombres_alm_a)
        self.assertNotIn("Lija Madera Bajo", nombres_alm_a)
        self.assertEqual(resp_alm_a.context["alertas"]["total_criticas"], 1)
        self.assertEqual(resp_alm_a.context["alertas"]["total_bajas"], 0)

        # 3. Filtrar por almacén B: solo Lija Madera Bajo en notificaciones
        resp_alm_b = self.client.get(reverse("inventario:alertas-lista") + f"?almacen_id={self.almacen_b.pk}")
        self.assertEqual(resp_alm_b.status_code, 200)
        nombres_alm_b = [n.producto.nombre for n in resp_alm_b.context["notificaciones"]]
        self.assertIn("Lija Madera Bajo", nombres_alm_b)
        self.assertNotIn("Tornillos Acero Crit", nombres_alm_b)
        self.assertEqual(resp_alm_b.context["alertas"]["total_criticas"], 0)
        self.assertEqual(resp_alm_b.context["alertas"]["total_bajas"], 1)

        # 4. Filtrar por nivel CRITICA: solo Tornillos Acero Crit en notificaciones
        resp_crit = self.client.get(reverse("inventario:alertas-lista") + "?nivel=CRITICA")
        self.assertEqual(resp_crit.status_code, 200)
        nombres_crit = [n.producto.nombre for n in resp_crit.context["notificaciones"]]
        self.assertIn("Tornillos Acero Crit", nombres_crit)
        self.assertNotIn("Lija Madera Bajo", nombres_crit)

        # 5. Filtrar por nivel BAJA: solo Lija Madera Bajo en notificaciones
        resp_baja = self.client.get(reverse("inventario:alertas-lista") + "?nivel=BAJA")
        self.assertEqual(resp_baja.status_code, 200)
        nombres_baja = [n.producto.nombre for n in resp_baja.context["notificaciones"]]
        self.assertIn("Lija Madera Bajo", nombres_baja)
        self.assertNotIn("Tornillos Acero Crit", nombres_baja)

        # 6. Combinación de filtros: Almacén A + nivel BAJA (no hay coincidencias)
        resp_cruce = self.client.get(reverse("inventario:alertas-lista") + f"?almacen_id={self.almacen_a.pk}&nivel=BAJA")
        self.assertEqual(resp_cruce.status_code, 200)
        self.assertEqual(len(resp_cruce.context["notificaciones"]), 0)
        self.assertContains(resp_cruce, "No se encontraron alertas de stock que coincidan con los filtros aplicados.")
