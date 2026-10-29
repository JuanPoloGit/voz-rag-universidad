#!/usr/bin/env python
"""Generador de DJANGO_SECRET_KEY criptográficamente segura para entornos de producción."""

from django.core.management.utils import get_random_secret_key

if __name__ == "__main__":
    key = get_random_secret_key()
    print("=" * 60)
    print("NUEVA DJANGO_SECRET_KEY GENERADA:")
    print(key)
    print("=" * 60)
    print("Copie este valor y péguelo en su archivo .env:")
    print(f"DJANGO_SECRET_KEY={key}")
