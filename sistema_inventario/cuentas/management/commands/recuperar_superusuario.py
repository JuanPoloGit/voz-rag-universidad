"""
Comando administrativo para recuperar y restablecer la contraseña del superusuario (§8.1, Decisión 6).

Restablece y reactiva únicamente al superusuario existente.
No crea un superusuario si no existe en el sistema.
Valida la política de contraseñas de AudacIA y no expone credenciales en pantalla.
"""

import getpass
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import models, transaction

from cuentas.models import UsuarioAudacIA
from cuentas.services import limpiar_intentos_fallidos
from inventario.auditoria import registrar_auditoria


class Command(BaseCommand):
    help = "Recupera y restablece la contraseña del superusuario existente en el sistema (§8.1, Decisión 6)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--username",
            type=str,
            default=None,
            help="Nombre de usuario del superusuario a recuperar (opcional si solo existe un superusuario).",
        )
        parser.add_argument(
            "--password",
            type=str,
            default=None,
            help="Nueva contraseña del superusuario (opcional; si se omite, se solicita interactivamente de forma segura).",
        )

    def handle(self, *args, **options):
        username = options.get("username")
        password = options.get("password")

        # 1. Localizar al superusuario existente (§8.1, Decisión 6: sólo restablece, no crea)
        if username:
            try:
                user = UsuarioAudacIA.objects.get(username=username)
            except UsuarioAudacIA.DoesNotExist:
                raise CommandError(f"No se encontró ningún superusuario con username '{username}'.")

            if not (user.rol == UsuarioAudacIA.Rol.SUPERUSUARIO or user.is_superuser or user.es_superusuario_inventario):
                raise CommandError(f"El usuario '{username}' no tiene privilegios de superusuario.")
        else:
            candidatos = UsuarioAudacIA.objects.filter(
                models.Q(rol=UsuarioAudacIA.Rol.SUPERUSUARIO) | models.Q(is_superuser=True)
            )
            total = candidatos.count()
            if total == 0:
                raise CommandError(
                    "No se encontró ningún superusuario en el sistema para recuperar. "
                    "El sistema exige que haya sido creado previamente con createsuperuser."
                )
            if total > 1:
                raise CommandError(
                    "Se encontraron múltiples cuentas con privilegios de superusuario. "
                    "Especifique el nombre de usuario usando el argumento --username."
                )
            user = candidatos.first()

        # 2. Obtener la nueva contraseña (vía argumento o getpass interactivo seguro)
        if not password:
            self.stdout.write(f"Iniciando recuperación para el superusuario '{user.username}'.")
            password = getpass.getpass("Nueva contraseña: ")
            password_confirm = getpass.getpass("Confirme la nueva contraseña: ")
            if password != password_confirm:
                raise CommandError("Las contraseñas ingresadas no coinciden.")

        # 3. Validar la política de contraseñas de Django (incluye LetraYNumeroValidator)
        try:
            validate_password(password, user=user)
        except ValidationError as exc:
            raise CommandError(f"La contraseña no cumple la política de seguridad: {'; '.join(exc.messages)}")

        # 4. Restablecer, reactivar y auditar atómicamente
        with transaction.atomic():
            user.set_password(password)
            user.is_active = True
            user.eliminado_logicamente = False
            user.save()

            # Desbloquear en caché e IP de loopback
            limpiar_intentos_fallidos(user, ip="127.0.0.1")

            registrar_auditoria(
                usuario=user,
                accion="RECUPERAR_SUPERUSUARIO",
                tipo_entidad="Usuario",
                id_entidad=str(user.id),
                valores_nuevos={
                    "is_active": True,
                    "eliminado_logicamente": False,
                },
                motivo="Restablecimiento y recuperación administrativa de superusuario por CLI",
                ip_origen="127.0.0.1",
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Superusuario '{user.username}' recuperado y restablecido exitosamente."
            )
        )
