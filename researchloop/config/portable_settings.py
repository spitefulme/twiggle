"""Operational core configuration; never imports synthetic QA settings."""
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

BASE_DIR = Path(__file__).resolve().parents[2]
SECRET_KEY = os.environ['TWIGGLE_SECRET_KEY']
if len(SECRET_KEY) < 64:
    raise ValueError('Application secret must have at least 64 characters')
ORIGIN = os.environ['TWIGGLE_ORIGIN']
if not re.fullmatch(r'https?://[a-z0-9.-]+(?::[0-9]{1,5})?', ORIGIN):
    raise ValueError('Origin must be one canonical ASCII origin')
origin = urlsplit(ORIGIN)
if origin.port in {0, 80 if origin.scheme == 'http' else 443}:
    raise ValueError('Use a non-default port or omit the default port')
if (origin.scheme not in {'http', 'https'} or not origin.hostname or origin.path or origin.query or origin.fragment or origin.username or origin.password or ORIGIN.endswith('/')):
    raise ValueError('TWIGGLE_ORIGIN must be one exact HTTP(S) origin')
if origin.scheme == 'http' and origin.hostname not in {'127.0.0.1', 'localhost'}:
    raise ValueError('HTTP allowed only on loopback')
DEBUG = False
U1_ALLOW_SYNTHETIC = False
TWIGGLE_PORTABLE = True
ALLOWED_HOSTS = [origin.hostname, '127.0.0.1']
CSRF_TRUSTED_ORIGINS = [ORIGIN]
DATABASES = {'default': {'ENGINE': 'django.db.backends.postgresql', 'NAME': 'twiggle', 'USER': os.environ.get('TWIGGLE_DATABASE_USER', 'twiggle'), 'PASSWORD': os.environ['POSTGRES_PASSWORD'], 'HOST': 'db', 'PORT': '5432', 'CONN_MAX_AGE': 0}}
USE_TZ = True
ROOT_URLCONF = 'researchloop.config.urls'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
INSTALLED_APPS = ['django.contrib.auth', 'django.contrib.contenttypes', 'django.contrib.sessions', 'django.contrib.staticfiles'] + ['researchloop.' + name for name in ['identity', 'governance', 'studies', 'participation', 'cards', 'trees', 'analysis', 'evidence_delivery']]
MIDDLEWARE = ['django.middleware.security.SecurityMiddleware', 'django.contrib.sessions.middleware.SessionMiddleware', 'django.middleware.csrf.CsrfViewMiddleware', 'django.contrib.auth.middleware.AuthenticationMiddleware', 'researchloop.interaction.middleware.SecurityHeaders', 'researchloop.portable.middleware.ExactOrigin']
TEMPLATES = [{'BACKEND': 'django.template.backends.django.DjangoTemplates', 'DIRS': [BASE_DIR / 'researchloop/templates'], 'APP_DIRS': True, 'OPTIONS': {'context_processors': ['django.template.context_processors.request', 'django.template.context_processors.csrf']}}]
STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'researchloop/static']
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = origin.scheme == 'https'
SESSION_COOKIE_NAME = '__Host-twiggle_session' if SESSION_COOKIE_SECURE else 'twiggle_local_session'
CSRF_COOKIE_SECURE = SESSION_COOKIE_SECURE
SESSION_COOKIE_SAMESITE = 'Strict'
CSRF_COOKIE_SAMESITE = 'Strict'
SECURE_REFERRER_POLICY = 'no-referrer'
DATA_UPLOAD_MAX_MEMORY_SIZE = 262144
AUTH_PASSWORD_VALIDATORS = [{'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', 'OPTIONS': {'min_length': 14}}, {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'}, {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'}, {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'}]
# No forwarded-header trust: a TLS proxy forwards the configured Host/Origin.
# The single allowed origin is checked separately; health has no application data.
LOGGING = {'version': 1, 'disable_existing_loggers': False, 'handlers': {'null': {'class': 'logging.NullHandler'}}, 'loggers': {'django.security': {'handlers': ['null'], 'propagate': False}}}
