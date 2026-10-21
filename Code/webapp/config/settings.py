"""Django settings for the Call Center Analysis web app.

All settings come from environment variables (App Service app settings in Azure). The
pipeline/data settings (CC_MODE, storage, Cosmos, ...) are read by ``callcenter.config``.
"""

import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def env(name, default=""):
    value = os.environ.get(name, default).strip()
    # An unresolved App Service Key Vault reference arrives as the literal reference string.
    return "" if value.startswith("@Microsoft.KeyVault(") else value


def env_bool(name, default=False):
    value = env(name)
    return default if not value else value.lower() in {"1", "true", "yes", "on"}


def env_list(name, default=""):
    return [v.strip() for v in env(name, default).split(",") if v.strip()]


CC_MODE = env("CC_MODE", "local").lower()
IS_LOCAL = CC_MODE == "local"

SECRET_KEY = env("DJANGO_SECRET_KEY") or ("local-dev-only-insecure-key" if IS_LOCAL else "")
if not SECRET_KEY and "collectstatic" in sys.argv:  # build step (Oryx) may run without app secrets
    SECRET_KEY = "build-only-not-used-at-runtime"
if not SECRET_KEY:
    raise RuntimeError("DJANGO_SECRET_KEY must be set outside local mode")

DEBUG = env_bool("DJANGO_DEBUG", IS_LOCAL)

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")
if env("WEBSITE_HOSTNAME"):  # set automatically by App Service
    ALLOWED_HOSTS.append(env("WEBSITE_HOSTNAME"))
    CSRF_TRUSTED_ORIGINS.append(f"https://{env('WEBSITE_HOSTNAME')}")

INSTALLED_APPS = [
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "accounts",
    "portal",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "accounts.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {
        "context_processors": [
            "django.template.context_processors.request",
            "django.contrib.messages.context_processors.messages",
            "portal.context.portal",
        ],
    },
}]

# No relational database: call data lives in Cosmos DB (Azure) / SQLite via the callcenter
# package (local), users come from Entra ID, sessions are signed cookies.
DATABASES = {}
SESSION_ENGINE = "django.contrib.sessions.backends.signed_cookies"
SESSION_COOKIE_AGE = 8 * 3600
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
MESSAGE_STORAGE = "django.contrib.messages.storage.cookie.CookieStorage"

if not IS_LOCAL:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = env_bool("DJANGO_SSL_REDIRECT", True)
    SECURE_REDIRECT_EXEMPT = [r"^healthz$"]
    SECURE_HSTS_SECONDS = 3600
    SECURE_CONTENT_TYPE_NOSNIFF = True

LANGUAGE_CODE = "en-us"
TIME_ZONE = env("DISPLAY_TIME_ZONE", "Asia/Kolkata")
USE_I18N = False
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
        if not DEBUG
        else "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}

WHITENOISE_USE_FINDERS = DEBUG  # serve static files without collectstatic when debugging (e.g. under gunicorn)

MAX_UPLOAD_MB = int(env("MAX_UPLOAD_MB", "200"))
FILE_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024

# ---- Authentication -------------------------------------------------------------------
# dev      : pick a demo user on the sign-in page (local mode only; refused otherwise)
# password : pick a demo user AND enter the shared DEMO_PASSWORD (demo environments without an
#            Entra app registration; sign-in is refused while DEMO_PASSWORD is empty)
# entra    : Microsoft Entra ID sign-in (OpenID Connect, auth code flow via MSAL)
AUTH_MODE = env("AUTH_MODE", "dev" if IS_LOCAL else "entra").lower()
if AUTH_MODE == "dev" and not IS_LOCAL:
    raise RuntimeError("AUTH_MODE=dev is only allowed when CC_MODE=local")
if AUTH_MODE not in {"dev", "password", "entra"}:
    raise RuntimeError(f"Unknown AUTH_MODE {AUTH_MODE!r} (dev, password or entra)")
DEMO_PASSWORD = env("DEMO_PASSWORD")
DEMO_PASSWORD_MAX_FAILURES = 5  # per client address, then locked for 15 minutes

ENTRA_TENANT_ID = env("ENTRA_TENANT_ID")
ENTRA_CLIENT_ID = env("ENTRA_CLIENT_ID")
ENTRA_CLIENT_SECRET = env("ENTRA_CLIENT_SECRET")
ENTRA_AUTHORITY = env("ENTRA_AUTHORITY") or f"https://login.microsoftonline.com/{ENTRA_TENANT_ID or 'common'}"
# Role given to signed-in users that have no app role assigned. Empty = deny access.
ENTRA_DEFAULT_ROLE = env("ENTRA_DEFAULT_ROLE", "Agent")

# ---- Integrations ---------------------------------------------------------------------
CRM_API_KEYS = env_list("CRM_API_KEYS")

POWERBI_WORKSPACE_ID = env("POWERBI_WORKSPACE_ID")
POWERBI_REPORT_ID = env("POWERBI_REPORT_ID")
# Service principal used to generate embed tokens; defaults to the Entra app registration.
POWERBI_TENANT_ID = env("POWERBI_TENANT_ID") or ENTRA_TENANT_ID
POWERBI_CLIENT_ID = env("POWERBI_CLIENT_ID") or ENTRA_CLIENT_ID
POWERBI_CLIENT_SECRET = env("POWERBI_CLIENT_SECRET") or ENTRA_CLIENT_SECRET

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", "INFO")},
    "loggers": {"azure": {"level": "WARNING"}, "urllib3": {"level": "WARNING"}, "django.request": {"level": "ERROR"}},
}

if env("APPLICATIONINSIGHTS_CONNECTION_STRING"):
    try:  # optional: pip install azure-monitor-opentelemetry
        from azure.monitor.opentelemetry import configure_azure_monitor

        configure_azure_monitor()
    except ImportError:
        pass
