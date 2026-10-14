"""URLs de la app cuentas — Inventario AudacIA."""

from django.urls import path

from . import views

app_name = "cuentas"

urlpatterns = [
    path("login/", views.LoginView.as_view(), name="login"),
    path("logout/", views.LogoutView.as_view(), name="logout"),
    path("cambiar-password/", views.CambiarPasswordView.as_view(), name="cambiar_password"),
    path("usuarios/", views.UsuarioListView.as_view(), name="usuario_lista"),
    path("usuarios/crear/", views.UsuarioCreateView.as_view(), name="usuario_crear"),
    path("usuarios/<int:pk>/editar/", views.UsuarioUpdateView.as_view(), name="usuario_editar"),
    path("usuarios/<int:pk>/eliminar/", views.UsuarioEliminarLogicamenteView.as_view(), name="usuario_eliminar"),
]
