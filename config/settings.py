"""Configurazione locale e PostgreSQL privato. Nessuna credenziale nel codice."""
import os
import re
import secrets
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

BASE_DIR = Path(__file__).resolve().parent.parent
env_file = BASE_DIR / '.env'
if env_file.is_file():
    for line in env_file.read_text(encoding='utf-8').splitlines():
        if not line.strip() or line.lstrip().startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        key, value = key.strip(), value.strip()
        if key.startswith('ARCHIVIO_') or key == 'DATABASE_URL':
            os.environ.setdefault(key, value.strip('"\''))

ARCHIVIO_AMBIENTE = os.getenv('ARCHIVIO_AMBIENTE', 'sviluppo')
if ARCHIVIO_AMBIENTE not in {'sviluppo', 'produzione'}:
    raise RuntimeError('ARCHIVIO_AMBIENTE deve essere sviluppo o produzione.')
PRODUZIONE = ARCHIVIO_AMBIENTE == 'produzione'
RUNTIME_ROOT = Path(os.getenv('ARCHIVIO_RUNTIME_ROOT', str(BASE_DIR / 'runtime'))).resolve()
RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
SECRET_KEY = os.getenv('ARCHIVIO_SECRET_KEY', '')
if not SECRET_KEY:
    if PRODUZIONE:
        raise RuntimeError('Impostare ARCHIVIO_SECRET_KEY prima di avviare la produzione.')
    secret_file = RUNTIME_ROOT / 'secret.key'
    try:
        descriptor = os.open(secret_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'w') as output:
            output.write(secrets.token_urlsafe(64))
    except FileExistsError:
        pass
    SECRET_KEY = secret_file.read_text().strip()
if len(SECRET_KEY) < 50:
    raise RuntimeError('Usare una chiave segreta di almeno 50 caratteri.')
DEBUG = False
ALLOWED_HOSTS = [x.strip() for x in os.getenv('ARCHIVIO_HOSTS', '127.0.0.1,localhost').split(',') if x.strip()]
if PRODUZIONE and ('*' in ALLOWED_HOSTS or not os.getenv('DATABASE_URL')):
    raise RuntimeError('Produzione: configurare host espliciti e DATABASE_URL dedicata.')

INSTALLED_APPS = [
    'django.contrib.auth', 'django.contrib.contenttypes', 'django.contrib.sessions',
    'django.contrib.messages', 'django.contrib.staticfiles', 'rest_framework',
    'archivio',
]
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware', 'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware', 'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware', 'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware', 'config.middleware.PrivateResponseMiddleware',
]
ROOT_URLCONF = 'config.urls'
TEMPLATES = [{'BACKEND': 'django.template.backends.django.DjangoTemplates', 'DIRS': [], 'APP_DIRS': True,
              'OPTIONS': {'context_processors': ['django.template.context_processors.request',
                  'django.contrib.auth.context_processors.auth', 'django.contrib.messages.context_processors.messages']}}]
WSGI_APPLICATION = 'config.wsgi.application'
ASGI_APPLICATION = 'config.asgi.application'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': RUNTIME_ROOT / 'db.sqlite3',
                         'OPTIONS': {'timeout': 30}}}
database_url = os.getenv('DATABASE_URL', '')
if database_url:
    url = urlsplit(database_url)
    if url.scheme not in {'postgres', 'postgresql'} or not url.hostname or not url.username:
        raise RuntimeError('DATABASE_URL deve indicare PostgreSQL con un utente dedicato.')
    schema = os.getenv('ARCHIVIO_SCHEMA', 'archivio_factory')
    if not re.fullmatch(r'[a-z][a-z0-9_]{0,62}', schema):
        raise RuntimeError('Nome schema PostgreSQL non valido.')
    sslmode = parse_qs(url.query).get('sslmode', ['require'])[0]
    if sslmode not in {'require', 'verify-ca', 'verify-full'} and PRODUZIONE:
        raise RuntimeError('Produzione: connessione PostgreSQL cifrata richiesta.')
    DATABASES = {'default': {'ENGINE': 'django.db.backends.postgresql', 'NAME': unquote(url.path.lstrip('/')),
        'USER': unquote(url.username), 'PASSWORD': unquote(url.password or ''), 'HOST': url.hostname,
        'PORT': url.port or 5432, 'CONN_MAX_AGE': 60,
        'OPTIONS': {'sslmode': sslmode, 'options': '-c search_path=' + schema}}}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', 'OPTIONS': {'min_length': 12}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]
PASSWORD_HASHERS = ['django.contrib.auth.hashers.PBKDF2PasswordHasher', 'django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher']
LANGUAGE_CODE = 'it'
TIME_ZONE = 'Europe/Rome'
USE_I18N = True
USE_TZ = True
LOGIN_URL = '/accesso/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/accesso/'
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_SECURE = PRODUZIONE
CSRF_COOKIE_SECURE = PRODUZIONE
SECURE_SSL_REDIRECT = PRODUZIONE
SECURE_HSTS_SECONDS = 31536000 if PRODUZIONE else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = PRODUZIONE
SECURE_HSTS_PRELOAD = False
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'
CSRF_TRUSTED_ORIGINS = [x.strip() for x in os.getenv('ARCHIVIO_ORIGINI_CSRF', '').split(',') if x.strip()]

STATIC_URL = '/static/'
STATIC_ROOT = RUNTIME_ROOT / 'static'
PRIVATE_MEDIA_ROOT = Path(os.getenv('ARCHIVIO_MEDIA_ROOT', str(RUNTIME_ROOT / 'media'))).resolve()
PRIVATE_MEDIA_ROOT.mkdir(parents=True, exist_ok=True)
ARCHIVIO_MAX_VIDEO_BYTES = int(os.getenv('ARCHIVIO_MAX_VIDEO_BYTES', 512 * 1024 * 1024))
ARCHIVIO_MAX_ALLEGATO_BYTES = int(os.getenv('ARCHIVIO_MAX_ALLEGATO_BYTES', 25 * 1024 * 1024))
ARCHIVIO_MAX_ALLEGATI = int(os.getenv('ARCHIVIO_MAX_ALLEGATI', 20))
ARCHIVIO_MAX_PACCHETTO_BYTES = int(os.getenv('ARCHIVIO_MAX_PACCHETTO_BYTES', 1024 * 1024 * 1024))
DATA_UPLOAD_MAX_MEMORY_SIZE = ARCHIVIO_MAX_ALLEGATO_BYTES + 2 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 0
DATA_UPLOAD_MAX_NUMBER_FILES = ARCHIVIO_MAX_ALLEGATI + 1
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': ['rest_framework.authentication.SessionAuthentication', 'archivio.authentication.FactoryAuthentication'],
    'DEFAULT_PERMISSION_CLASSES': ['rest_framework.permissions.IsAuthenticated'],
    'DEFAULT_RENDERER_CLASSES': ['rest_framework.renderers.JSONRenderer'],
    'DEFAULT_THROTTLE_CLASSES': ['rest_framework.throttling.UserRateThrottle', 'rest_framework.throttling.AnonRateThrottle'],
    'DEFAULT_THROTTLE_RATES': {'user': '300/min', 'anon': '30/min'},
    'EXCEPTION_HANDLER': 'archivio.api.gestisci_errore',
}
EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
