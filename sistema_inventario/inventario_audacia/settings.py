"""
Configuración de Django para Inventario AudacIA.

Todas las variables sensibles y configurables se leen de .env.
Ver .env.example para la lista completa.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# ─── Rutas ───────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# ─── Seguridad ───────────────────────────────────────────────────────────────

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "dev-insecure-key-change-me")

DEBUG = os.getenv("DJANGO_DEBUG", "False").lower() in ("true", "1", "yes")

ALLOWED_HOSTS = [
    h.strip()
    for h in os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if h.strip()
]

# ─── Aplicaciones ────────────────────────────────────────────────────────────

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # --- Apps del proyecto ---
    "cuentas",
    "inventario",
    "integraciones.haku",
    "web",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "integraciones.haku.middleware.HakuSessionMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "inventario_audacia.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "inventario_audacia.wsgi.application"

# ─── Base de datos (PostgreSQL local) ────────────────────────────────────────

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("DB_NAME", "inventario_audacia"),
        "USER": os.getenv("DB_USER", "inventario"),
        "PASSWORD": os.getenv("DB_PASSWORD", ""),
        "HOST": os.getenv("DB_HOST", "localhost"),
        "PORT": os.getenv("DB_PORT", "5432"),
        # Aislamiento SERIALIZABLE garantiza que las transacciones con
        # select_for_update() no generen anomalías en los movimientos.
        "OPTIONS": {
            "options": "-c default_transaction_isolation=read\\ committed",
        },
    }
}

# ─── Modelo de usuario personalizado ─────────────────────────────────────────

AUTH_USER_MODEL = "cuentas.UsuarioAudacIA"

# ─── Contraseñas ─────────────────────────────────────────────────────────────

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
     "OPTIONS": {"min_length": 7}},
    {"NAME": "cuentas.validators.LetraYNumeroValidator"},
]

# ─── Bloqueo por intentos fallidos ───────────────────────────────────────────

LOGIN_MAX_ATTEMPTS = int(os.getenv("LOGIN_MAX_ATTEMPTS", "5"))
LOGIN_LOCKOUT_MINUTES = int(os.getenv("LOGIN_LOCKOUT_MINUTES", "15"))

# ─── Sesiones ────────────────────────────────────────────────────────────────

SESSION_ENGINE = "django.contrib.sessions.backends.db"
# Duración de "Recordar este dispositivo" (7 días por defecto).
SESSION_COOKIE_AGE_DAYS = int(os.getenv("SESSION_COOKIE_AGE_DAYS", "7"))
SESSION_COOKIE_AGE = SESSION_COOKIE_AGE_DAYS * 86400
# Si no se marca "recordar", la sesión caduca al cerrar el navegador.
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = True  # Requiere HTTPS (mkcert en red local).
SESSION_COOKIE_SAMESITE = "Lax"

# ─── CSRF ────────────────────────────────────────────────────────────────────

CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SECURE = True

# ─── Internacionalización ────────────────────────────────────────────────────

LANGUAGE_CODE = "es-co"

TIME_ZONE = os.getenv("TIME_ZONE", "America/Bogota")

USE_I18N = True
USE_TZ = True

# ─── Archivos estáticos ─────────────────────────────────────────────────────

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

# ─── Archivos media ─────────────────────────────────────────────────────────

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

# ─── Campo auto por defecto ─────────────────────────────────────────────────

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ─── Login ───────────────────────────────────────────────────────────────────

LOGIN_URL = "/cuentas/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/cuentas/login/"

# ─── Haku Inventario ─────────────────────────────────────────────────────────

HAKU_VOICE_CONTEXT_TIMEOUT_MINUTES = int(
    os.getenv("HAKU_VOICE_CONTEXT_TIMEOUT_MINUTES", "30")
)
HAKU_TRANSCRIPTION_LOG_RETENTION_DAYS = int(
    os.getenv("HAKU_TRANSCRIPTION_LOG_RETENTION_DAYS", "30")
)
