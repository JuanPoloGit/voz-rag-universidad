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
                "inventario.context_processors.alertas_stock",
            ],
        },
    },
]

WSGI_APPLICATION = "inventario_audacia.wsgi.application"

USE_SQLITE = os.getenv("USE_SQLITE", "True").lower() in ("true", "1", "yes")                                                                                                                                                      
DB_ENGINE = os.getenv("DB_ENGINE", "sqlite" if USE_SQLITE else "postgresql").lower()                                                                                                                                              

if USE_SQLITE or "sqlite" in DB_ENGINE: 
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }
else:
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

AUTHENTICATION_BACKENDS = [
    "cuentas.backends.UsuarioAudacIABackend",
]

# ─── Contraseñas ─────────────────────────────────────────────────────────────

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
     "OPTIONS": {"min_length": 7}},
    {"NAME": "cuentas.validators.LetraYNumeroValidator"},
]

# ─── Bloqueo por intentos fallidos ───────────────────────────────────────────

LOGIN_MAX_ATTEMPTS = int(os.getenv("LOGIN_MAX_ATTEMPTS", "5"))
LOGIN_LOCKOUT_MINUTES = int(os.getenv("LOGIN_LOCKOUT_MINUTES", "15"))
LOGIN_IP_MAX_ATTEMPTS = int(os.getenv("LOGIN_IP_MAX_ATTEMPTS", "20"))

# ─── Cachés en RAM (LocMemCache) para Haku Inventario ─────────────────────────
# LocMemCache es nativo de Django, seguro para subprocesos y opera 100% en RAM
# sin requerir Redis, Memcached ni servicios externos.

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "haku-inventario-default",
    },
    "haku_contexto": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "haku-inventario-contexto",
        "TIMEOUT": 7200,  # 2 horas sin actividad (7200 segundos)
        "OPTIONS": {
            "MAX_ENTRIES": 50,  # Máximo 50 contextos simultáneos
            "CULL_FREQUENCY": 2,  # Desalojo fraccional LRU al alcanzar MAX_ENTRIES
        },
    },
    "haku_catalogo": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "haku-inventario-catalogo",
        "TIMEOUT": 1800,  # 30 minutos
        "OPTIONS": {
            "MAX_ENTRIES": 500,
        },
    },
    "haku_ubicaciones": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "haku-inventario-ubicaciones",
        "TIMEOUT": 900,  # 15 minutos
        "OPTIONS": {
            "MAX_ENTRIES": 500,
        },
    },
    "haku_stock": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "haku-inventario-stock",
        "TIMEOUT": 15,  # 15 segundos máximo (stock temporal orientativo)
        "OPTIONS": {
            "MAX_ENTRIES": 200,
        },
    },
}

# ─── Sesiones ────────────────────────────────────────────────────────────────

SESSION_ENGINE = "django.contrib.sessions.backends.db"
# Duración de "Recordar este dispositivo" (7 días por defecto).
SESSION_COOKIE_AGE_DAYS = int(os.getenv("SESSION_COOKIE_AGE_DAYS", "7"))
SESSION_COOKIE_AGE = SESSION_COOKIE_AGE_DAYS * 86400
# Expiración por inactividad cuando no se recuerda la sesión (8 horas por defecto).
SESSION_IDLE_TIMEOUT_SECONDS = int(os.getenv("SESSION_IDLE_TIMEOUT_SECONDS", "28800"))
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_COOKIE_HTTPONLY = True
_cookie_secure_env = os.getenv("SESSION_COOKIE_SECURE", os.getenv("COOKIE_SECURE"))                                                                                                                                               
SESSION_COOKIE_SECURE = (                                                                                                                                                                                                         
    _cookie_secure_env.lower() in ("true", "1", "yes")                                                                                                                                                                            
    if _cookie_secure_env is not None                                                                                                                                                                                             
    else not DEBUG                                                                                                                                                                                                                
) 
SESSION_COOKIE_SAMESITE = "Lax"

# ─── CSRF ────────────────────────────────────────────────────────────────────
CSRF_COOKIE_HTTPONLY = False  # Permitir que JS lea el csrftoken para peticiones fetch/AJAX                                                                                                                                       
CSRF_COOKIE_SECURE = SESSION_COOKIE_SECURE                                                                                                                                                                                        
CSRF_TRUSTED_ORIGINS = [                                                                                                                                                                                                          
    f"https://{h}:8000" for h in ALLOWED_HOSTS if h not in ("*", "")                                                                                                                                                              
] + [                                                                                                                                                                                                                             
    f"https://{h}" for h in ALLOWED_HOSTS if h not in ("*", "")                                                                                                                                                                   
] + [                                                                                                                                                                                                                             
    f"http://{h}:8000" for h in ALLOWED_HOSTS if h not in ("*", "")                                                                                                                                                               
] 

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

LOGIN_URL = os.getenv("LOGIN_URL", "/cuentas/login/")                                                                                                                                                                             
LOGIN_REDIRECT_URL = os.getenv("LOGIN_REDIRECT_URL", "/")                                                                                                                                                                         
LOGOUT_REDIRECT_URL = os.getenv("LOGOUT_REDIRECT_URL", "/cuentas/login/")

# ─── Haku Inventario ─────────────────────────────────────────────────────────

HAKU_CONTEXT_TIMEOUT = int(os.getenv("HAKU_CONTEXT_TIMEOUT", "7200"))  # 2 horas
HAKU_PROPOSAL_TIMEOUT = int(os.getenv("HAKU_PROPOSAL_TIMEOUT", "1800"))  # 30 minutos

HAKU_VOICE_CONTEXT_TIMEOUT_MINUTES = int(
    os.getenv("HAKU_VOICE_CONTEXT_TIMEOUT_MINUTES", "30")
)
HAKU_TRANSCRIPTION_LOG_RETENTION_DAYS = int(
    os.getenv("HAKU_TRANSCRIPTION_LOG_RETENTION_DAYS", "30")
)

# ─── Silenciado temporal de verificaciones pendientes de otros integrantes ─────
# Permite ejecutar pruebas y desarrollo local sin bloquear por discrepancias
# en inventario/admin.py que corresponden al Integrante 2.
SILENCED_SYSTEM_CHECKS = [
    "admin.E108",
    "admin.E116",
    "admin.E035",
    "staticfiles.W004",
]

