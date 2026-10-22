"""
Vistas de autenticación y cuentas — Inventario AudacIA.
"""

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login as auth_login, logout as auth_logout, update_session_auth_hash
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect, render
from django.urls import reverse, reverse_lazy
from django.views import View

from inventario.auditoria import registrar_auditoria

from .forms import CambiarPasswordForm, LoginForm, UsuarioForm, PerfilUsuarioForm
from .models import UsuarioAudacIA
from .permisos import RolRequeridoMixin
from .services import limpiar_intentos_fallidos, registrar_intento_fallido, verificar_bloqueo


class LoginView(View):
    """Vista de inicio de sesión con control de bloqueo temporal y duración de sesión."""

    template_name = "cuentas/login.html"

    def get(self, request):
        if request.user.is_authenticated:
            return redirect(settings.LOGIN_REDIRECT_URL)
        form = LoginForm()
        return render(request, self.template_name, {"form": form})

    def post(self, request):
        form = LoginForm(request.POST)
        ip = request.META.get("REMOTE_ADDR")

        if form.is_valid():
            username = form.cleaned_data["username"]
            password = form.cleaned_data["password"]
            recordar = form.cleaned_data.get("recordar", False)

            # 1. Verificar si la cuenta o IP están bloqueadas antes de autenticar
            bloqueado, mensaje_bloqueo = verificar_bloqueo(username, ip)
            if bloqueado:
                form.add_error(None, mensaje_bloqueo)
                return render(request, self.template_name, {"form": form})

            # 2. Intentar autenticación
            user = authenticate(request, username=username, password=password)

            if user is not None:
                # Éxito: limpiar intentos fallidos y rotar sesión
                limpiar_intentos_fallidos(user, ip)
                auth_login(request, user)

                rol_nombre = user.get_rol_display() if hasattr(user, "get_rol_display") else user.rol
                messages.success(request, f"Hola, {user.username} ({rol_nombre}).")

                # Configurar caducidad de la sesión
                if recordar:
                    request.session.set_expiry(settings.SESSION_COOKIE_AGE)  # 7 días
                else:
                    request.session.set_expiry(settings.SESSION_IDLE_TIMEOUT_SECONDS)  # 8 horas / browser close

                next_url = request.GET.get("next") or settings.LOGIN_REDIRECT_URL
                return redirect(next_url)
            else:
                # Fallo: registrar intento y comprobar si se disparó el bloqueo
                registrar_intento_fallido(username, ip)
                bloqueado, mensaje_bloqueo = verificar_bloqueo(username, ip)
                if bloqueado:
                    form.add_error(None, mensaje_bloqueo)
                else:
                    form.add_error(None, "Nombre de usuario o contraseña incorrectos.")

        return render(request, self.template_name, {"form": form})


class LogoutView(View):
    """Vista de cierre de sesión seguro."""

    def post(self, request):
        auth_logout(request)
        return redirect(settings.LOGOUT_REDIRECT_URL)

    def get(self, request):
        auth_logout(request)
        return redirect(settings.LOGOUT_REDIRECT_URL)


class CambiarPasswordView(LoginRequiredMixin, View):
    """Permite al usuario cambiar su propia contraseña validando la clave actual."""

    template_name = "cuentas/cambiar_password.html"

    def get(self, request):
        form = CambiarPasswordForm(user=request.user)
        return render(request, self.template_name, {"form": form})

    def post(self, request):
        form = CambiarPasswordForm(user=request.user, data=request.POST)
        next_url = request.POST.get("next") or reverse("cuentas:perfil")
        if form.is_valid():
            nueva_password = form.cleaned_data["password_nueva"]
            request.user.set_password(nueva_password)
            request.user.save()
            update_session_auth_hash(request, request.user)

            registrar_auditoria(
                usuario=request.user,
                accion="CAMBIAR_PASSWORD",
                tipo_entidad="Usuario",
                id_entidad=str(request.user.id),
                motivo="Cambio voluntario de contraseña por el usuario",
                ip_origen=request.META.get("REMOTE_ADDR"),
            )

            messages.success(request, "Contraseña actualizada exitosamente.")
            if request.headers.get("x-requested-with") == "XMLHttpRequest":
                return JsonResponse({"success": True, "redirect_url": next_url})
            return redirect(next_url)

        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            errores = {}
            for field, err_list in form.errors.get_json_data().items():
                errores[field] = [e["message"] for e in err_list]
            return JsonResponse({"success": False, "errors": errores}, status=400)

        return render(request, self.template_name, {"form": form})


class UsuarioListView(RolRequeridoMixin, View):
    """Lista de usuarios para Administradores y Superusuario."""

    roles_permitidos = (UsuarioAudacIA.Rol.ADMINISTRADOR, UsuarioAudacIA.Rol.SUPERUSUARIO)
    template_name = "cuentas/usuario_lista.html"

    def get(self, request):
        qs = UsuarioAudacIA.objects.all().select_related("almacen_asignado").prefetch_related("almacenes_asignados")
        # Excluir siempre al propio usuario logueado (que se gestiona desde Mi Perfil)
        qs = qs.exclude(pk=request.user.pk)

        # Si es Administrador: solo puede ver usuarios operativos (Rol.USUARIO) que él mismo haya creado
        if not request.user.es_superusuario_inventario:
            qs = qs.filter(rol=UsuarioAudacIA.Rol.USUARIO, creado_por=request.user)

        return render(request, self.template_name, {"usuarios": qs})


class UsuarioCreateView(RolRequeridoMixin, View):
    """Creación de usuarios con restricciones de rol (§6.2)."""

    roles_permitidos = (UsuarioAudacIA.Rol.ADMINISTRADOR, UsuarioAudacIA.Rol.SUPERUSUARIO)
    template_name = "cuentas/usuario_form.html"

    def get(self, request):
        form = UsuarioForm(request_user=request.user)
        return render(request, self.template_name, {"form": form, "accion": "Crear"})

    def post(self, request):
        form = UsuarioForm(request.POST, request_user=request.user)
        if form.is_valid():
            form.instance.creado_por = request.user
            nuevo_usuario = form.save()

            registrar_auditoria(
                usuario=request.user,
                accion="CREAR_USUARIO",
                tipo_entidad="Usuario",
                id_entidad=str(nuevo_usuario.id),
                valores_nuevos={
                    "username": nuevo_usuario.username,
                    "nombre_completo": nuevo_usuario.nombre_completo,
                    "rol": nuevo_usuario.rol,
                    "almacen_asignado_id": nuevo_usuario.almacen_asignado_id,
                },
                motivo=f"Usuario creado por {request.user.username}",
                ip_origen=request.META.get("REMOTE_ADDR"),
            )

            messages.success(request, f"Usuario '{nuevo_usuario.username}' creado exitosamente.")
            if request.headers.get("x-requested-with") == "XMLHttpRequest":
                return JsonResponse({"success": True, "redirect_url": reverse("cuentas:usuario_lista")})
            return redirect("cuentas:usuario_lista")

        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            errores = {}
            for field, err_list in form.errors.get_json_data().items():
                errores[field] = [e["message"] for e in err_list]
            return JsonResponse({"success": False, "errors": errores}, status=400)

        return render(request, self.template_name, {"form": form, "accion": "Crear"})


class UsuarioUpdateView(RolRequeridoMixin, View):
    """Edición de usuarios respetando jerarquía de roles (§6.2)."""

    roles_permitidos = (UsuarioAudacIA.Rol.ADMINISTRADOR, UsuarioAudacIA.Rol.SUPERUSUARIO)
    template_name = "cuentas/usuario_form.html"

    def dispatch(self, request, pk, *args, **kwargs):
        from django.shortcuts import get_object_or_404
        from django.core.exceptions import PermissionDenied
        self.target_user = get_object_or_404(UsuarioAudacIA, pk=pk)

        # Restricción: No se puede editar al propio usuario aquí (debe ser desde Mi Perfil)
        if self.target_user.pk == request.user.pk:
            raise PermissionDenied("Para editar tu propia cuenta debes ingresar a Mi Perfil.")

        # Restricción §6.2: Administradores solo pueden editar usuarios (rol USUARIO) creados por ellos
        if not request.user.es_superusuario_inventario:
            if self.target_user.es_superusuario_inventario:
                raise PermissionDenied("Los administradores no pueden modificar al superusuario.")
            if self.target_user.rol != UsuarioAudacIA.Rol.USUARIO:
                raise PermissionDenied("Los administradores solo pueden gestionar usuarios con rol Usuario.")
            if self.target_user.creado_por_id != request.user.id:
                raise PermissionDenied("Los administradores solo pueden modificar los usuarios creados por ellos mismos.")

        return super().dispatch(request, pk, *args, **kwargs)

    def get(self, request, pk):
        form = UsuarioForm(instance=self.target_user, request_user=request.user)
        return render(request, self.template_name, {"form": form, "accion": "Editar", "target_user": self.target_user})

    def post(self, request, pk):
        form = UsuarioForm(request.POST, instance=self.target_user, request_user=request.user)
        if form.is_valid():
            usuario_actualizado = form.save()

            registrar_auditoria(
                usuario=request.user,
                accion="EDITAR_USUARIO",
                tipo_entidad="Usuario",
                id_entidad=str(usuario_actualizado.id),
                valores_nuevos={
                    "username": usuario_actualizado.username,
                    "nombre_completo": usuario_actualizado.nombre_completo,
                    "rol": usuario_actualizado.rol,
                    "almacen_asignado_id": usuario_actualizado.almacen_asignado_id,
                    "is_active": usuario_actualizado.is_active,
                },
                motivo=f"Usuario editado por {request.user.username}",
                ip_origen=request.META.get("REMOTE_ADDR"),
            )

            messages.success(request, f"Usuario '{usuario_actualizado.username}' actualizado.")
            if request.headers.get("x-requested-with") == "XMLHttpRequest":
                return JsonResponse({"success": True, "redirect_url": reverse("cuentas:usuario_lista")})
            return redirect("cuentas:usuario_lista")

        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            errores = {}
            for field, err_list in form.errors.get_json_data().items():
                errores[field] = [e["message"] for e in err_list]
            return JsonResponse({"success": False, "errors": errores}, status=400)

        return render(request, self.template_name, {"form": form, "accion": "Editar", "target_user": self.target_user})


class UsuarioEliminarLogicamenteView(RolRequeridoMixin, View):
    """Eliminación lógica de usuarios conservando integridad histórica (§6.3)."""

    roles_permitidos = (UsuarioAudacIA.Rol.ADMINISTRADOR, UsuarioAudacIA.Rol.SUPERUSUARIO)

    def post(self, request, pk):
        from django.shortcuts import get_object_or_404
        from django.core.exceptions import PermissionDenied
        target = get_object_or_404(UsuarioAudacIA, pk=pk)

        # Restricción innegociable: el superusuario es ineliminable
        if target.es_superusuario_inventario or target.is_superuser:
            raise PermissionDenied("El superusuario es ineliminable.")

        # Administrador no puede eliminar a otros administradores
        if not request.user.es_superusuario_inventario and target.rol == UsuarioAudacIA.Rol.ADMINISTRADOR:
            raise PermissionDenied("Los administradores no pueden eliminar a otros administradores.")

        target.eliminado_logicamente = True
        target.is_active = False
        target.save()

        registrar_auditoria(
            usuario=request.user,
            accion="ELIMINAR_LOGICAMENTE",
            tipo_entidad="Usuario",
            id_entidad=str(target.id),
            motivo=f"Eliminación lógica ejecutada por {request.user.username}",
            ip_origen=request.META.get("REMOTE_ADDR"),
        )

        messages.success(request, f"Usuario '{target.username}' eliminado lógicamente.")
        return redirect("cuentas:usuario_lista")


class PerfilView(LoginRequiredMixin, View):
    """Página dedicada Mi Perfil (§6.2) con ficha de usuario y modales de acción."""

    template_name = "cuentas/perfil.html"

    def get(self, request):
        password_form = CambiarPasswordForm(user=request.user)
        return render(request, self.template_name, {"form": password_form, "user": request.user})

    def post(self, request):
        user = request.user
        nombre = (request.POST.get("nombre_completo") or "").strip()
        username = (request.POST.get("username") or "").strip()
        actual = request.POST.get("password_actual")
        nueva = request.POST.get("password_nueva")
        confirmacion = request.POST.get("password_confirmacion")

        fields = []
        if nombre:
            user.nombre_completo = nombre
            fields.append("nombre_completo")
        if username and username != user.username and user.es_superusuario_inventario:
            user.username = username
            fields.append("username")
        if fields:
            user.save(update_fields=fields)

        if actual or nueva or confirmacion:
            form = CambiarPasswordForm(user=user, data=request.POST)
            if form.is_valid():
                user.set_password(form.cleaned_data["password_nueva"])
                user.save()
                update_session_auth_hash(request, user)
                messages.success(request, "Perfil y contraseña actualizados exitosamente.")
            else:
                return render(request, self.template_name, {"form": form, "user": user})
        else:
            messages.success(request, "Perfil actualizado exitosamente.")

        return redirect("cuentas:perfil")

