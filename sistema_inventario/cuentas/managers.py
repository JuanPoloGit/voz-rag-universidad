"""
Manager personalizado para UsuarioAudacIA.

Separa la lógica de creación de usuarios del modelo para mantener
models.py enfocado en la estructura de datos.
"""

from django.contrib.auth.models import BaseUserManager


class UsuarioAudacIAManager(BaseUserManager):
    """Manager que exige nombre_completo y username, sin requerir email."""

    def create_user(self, username, nombre_completo, password=None, **extra_fields):
        if not username:
            raise ValueError("El nombre de usuario es obligatorio.")
        if not nombre_completo:
            raise ValueError("El nombre completo es obligatorio.")

        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)

        user = self.model(
            username=username,
            nombre_completo=nombre_completo,
            **extra_fields,
        )
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, username, nombre_completo, password=None, **extra_fields):
        """Crea el único superusuario del sistema.

        Este método se usa solo desde manage.py createsuperuser o desde
        el comando personalizado recuperar_superusuario.
        """
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields["rol"] = "SUPERUSUARIO"

        if extra_fields.get("is_staff") is not True:
            raise ValueError("El superusuario debe tener is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("El superusuario debe tener is_superuser=True.")

        return self.create_user(username, nombre_completo, password, **extra_fields)
