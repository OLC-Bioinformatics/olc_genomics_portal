"""
Django settings for olc_webportalv2 project.

For more information on this file, see
https://docs.djangoproject.com/en/dev/topics/settings/

For the full list of settings and their values, see
https://docs.djangoproject.com/en/dev/ref/settings/
"""
from django.utils.translation import ugettext_lazy as language
from sentry_sdk.integrations.django import DjangoIntegration
from django.core.exceptions import ImproperlyConfigured
from celery.schedules import crontab
from kombu import Queue
import sentry_sdk
import environ
import os


ROOT_DIR = environ.Path(__file__) - 3  # (olc_webportalv2/config/settings/prod.py - 3 = olc_webportalv2/)
APPS_DIR = ROOT_DIR.path('olc_webportalv2')

# Load operating system environment variables and then prepare to use them
env = environ.Env()

# .env file, should load only in development environment
READ_DOT_ENV_FILE = env.bool('DJANGO_READ_DOT_ENV_FILE', default=True)
SECRET_KEY = env('SECRET_KEY')

try:
    ENVIRONMENT = env('ENVIRONMENT')
except (KeyError, ImproperlyConfigured):
    os.environ['ENVIRONMENT'] = 'ERROR'
    ENVIRONMENT = env('ENVIRONMENT')

if READ_DOT_ENV_FILE:
    # Operating System Environment variables have precedence over variables defined in the .env file,
    # that is to say variables from the .env files will only be used if not defined
    # as environment variables.
    env_file = str(ROOT_DIR.path('env'))
    print('Loading : {} from prod.py'.format(env_file))
    env.read_env(env_file)
    print('The .env file has been loaded. See prod.py for more information')

print('Loaded prod settings')
# APP CONFIGURATION
# ------------------------------------------------------------------------------
DJANGO_APPS = [
    # Default Django apps:
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.sites',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.postgres',

    # Django-autocomplete-light, for form auto completion. It's docs say it should go before django.contrib.admin
    'dal',
    'dal_select2',

    # Admin
    'django.contrib.admin',
]
THIRD_PARTY_APPS = [
    'crispy_forms',  # Form layouts
    'allauth',  # registration
    'allauth.account',  # registration
    'allauth.socialaccount',  # registration
    'django_filters'  # database filtering
]

# Apps specific for this project go here.
LOCAL_APPS = [
    # custom users app
    'olc_webportalv2.users.apps.UsersConfig',
    # Your stuff: custom apps go here
    'olc_webportalv2.cowbat.apps.CowbatConfig',
    'olc_webportalv2.data.apps.DataConfig',
    'olc_webportalv2.geneseekr.apps.GeneseekrConfig',
    'olc_webportalv2.metadata.apps.MetadataConfig',
    'olc_webportalv2.api.apps.ApiConfig',
    'olc_webportalv2.vir_typer.apps.VirTyperConfig',
    'olc_webportalv2.sequence_database.apps.SequenceDatabaseConfig',

    # Need this to get django-multiselectfield to work
    'multiselectfield',

    # Django-bootstrap-forms
    'bootstrapform',

    # Sortable HTML tables
    'django_tables2',

    # django-widget-tweaks
    'widget_tweaks',

    # REST framework
    'rest_framework'

]

# See: https://docs.djangoproject.com/en/dev/ref/settings/#installed-apps
INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

# MIDDLEWARE CONFIGURATION
# ------------------------------------------------------------------------------
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.locale.LocaleMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

REST_FRAMEWORK = {
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 10
}

# CHANGE PERMISSONS ON UPLOADED FILES TO ALLOW FOR COWBAT TO RUN
FILE_UPLOAD_DIRECTORY_PERMISSIONS = 0o766
FILE_UPLOAD_PERMISSIONS = 0o766

# MIGRATIONS CONFIGURATION
# ------------------------------------------------------------------------------
MIGRATION_MODULES = {
    'sites': 'olc_webportalv2.contrib.sites.migrations'
}

# DEBUG
# ------------------------------------------------------------------------------
# See: https://docs.djangoproject.com/en/dev/ref/settings/#debug
if ENVIRONMENT == 'DEV':
    DEBUG = env.bool('DJANGO_DEBUG', True)
else:
    DEBUG = env.bool('DJANGO_DEBUG', False)


# FIXTURE CONFIGURATION
# ------------------------------------------------------------------------------
# See: https://docs.djangoproject.com/en/dev/ref/settings/#std:setting-FIXTURE_DIRS
FIXTURE_DIRS = (
    str(APPS_DIR.path('fixtures')),
)

# EMAIL CONFIGURATION
# ------------------------------------------------------------------------------
EMAIL_BACKEND = env('DJANGO_EMAIL_BACKEND', default='django.core.mail.backends.smtp.EmailBackend')
EMAIL_USE_TLS = True
EMAIL_HOST = 'smtp.gmail.com'
EMAIL_PORT = 587
if ENVIRONMENT == 'DEV':
    pass
else:
    EMAIL_HOST_USER = env('EMAIL_HOST_USER')
    EMAIL_HOST_PASSWORD = env('EMAIL_HOST_PASSWORD')

# MANAGER CONFIGURATION
# ------------------------------------------------------------------------------
# See: https://docs.djangoproject.com/en/dev/ref/settings/#/As
ADMINS = [
     ("""Adam Koziol""", 'adam.koziol@canada.ca'),
]

# See: https://docs.djangoproject.com/en/dev/ref/settings/#managers
MANAGERS = ADMINS

# DATABASE CONFIGURATION
# ------------------------------------------------------------------------------
# See:
DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql_psycopg2',
            'NAME': os.environ['DB_NAME'],
            'USER': os.environ['DB_USER'],
            'PASSWORD': os.environ['DB_PASS'],
            'HOST': os.environ['DB_SERVICE'],
            'PORT': os.environ['DB_PORT']
        }
}
DATABASES['default']['ATOMIC_REQUESTS'] = True


# GENERAL CONFIGURATION
# ------------------------------------------------------------------------------
# Local time zone for this installation. Choices can be found here:
# http://en.wikipedia.org/wiki/List_of_tz_zones_by_name
# although not all choices may be available on all operating systems.
# In a Windows environment this must be set to your system time zone.
TIME_ZONE = 'Canada/Eastern'

# See: https://docs.djangoproject.com/en/dev/ref/settings/#language-code
# LANGUAGE_CODE = 'en-ca'

LANGUAGES = (
    ('en-ca', language('English')),
    ('fr', language('French')),
)

LANGUAGE_CODE = 'en-ca'

# See: https://docs.djangoproject.com/en/dev/ref/settings/#site-id
SITE_ID = 1

# See: https://docs.djangoproject.com/en/dev/ref/settings/#use-i18n
USE_I18N = True

# See: https://docs.djangoproject.com/en/dev/ref/settings/#use-l10n
USE_L10N = True

# See: https://docs.djangoproject.com/en/dev/ref/settings/#use-tz
USE_TZ = True

# TEMPLATE CONFIGURATION
# ------------------------------------------------------------------------------
# See: https://docs.djangoproject.com/en/dev/ref/settings/#templates
TEMPLATES = [
    {
        # See: https://docs.djangoproject.com/en/dev/ref/settings/#std:setting-TEMPLATES-BACKEND
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        # See: https://docs.djangoproject.com/en/dev/ref/settings/#template-dirs
        'DIRS': [
            str(APPS_DIR.path('templates')),
        ],
        'OPTIONS': {
            # See: https://docs.djangoproject.com/en/dev/ref/settings/#template-debug
            'debug': DEBUG,
            # See: https://docs.djangoproject.com/en/dev/ref/settings/#template-loaders
            # https://docs.djangoproject.com/en/dev/ref/templates/api/#loader-types
            'loaders': [
                'django.template.loaders.filesystem.Loader',
                'django.template.loaders.app_directories.Loader',
            ],
            # See: https://docs.djangoproject.com/en/dev/ref/settings/#template-context-processors
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.template.context_processors.i18n',
                'django.template.context_processors.media',
                'django.template.context_processors.static',
                'django.template.context_processors.tz',
                'django.contrib.messages.context_processors.messages',
                # Your stuff: custom template context processors go here
            ],
        },
    },
]

# See: http://django-crispy-forms.readthedocs.io/en/latest/install.html#template-packs
CRISPY_TEMPLATE_PACK = 'bootstrap4'

# STATIC FILE CONFIGURATION
# ------------------------------------------------------------------------------
# See: https://docs.djangoproject.com/en/dev/ref/settings/#static-root
STATIC_ROOT = str(ROOT_DIR('staticfiles'))

# See: https://docs.djangoproject.com/en/dev/ref/settings/#static-url
STATIC_URL = '/static/'

# See: https://docs.djangoproject.com/en/dev/ref/contrib/staticfiles/#std:setting-STATICFILES_DIRS
STATICFILES_DIRS = [
    str(APPS_DIR.path('static')),
]

# See: https://docs.djangoproject.com/en/dev/ref/contrib/staticfiles/#staticfiles-finders
STATICFILES_FINDERS = [
    'django.contrib.staticfiles.finders.FileSystemFinder',
    'django.contrib.staticfiles.finders.AppDirectoriesFinder',
]

# MEDIA CONFIGURATION
# ------------------------------------------------------------------------------
# See: https://docs.djangoproject.com/en/dev/ref/settings/#media-root
MEDIA_ROOT = str(APPS_DIR('media'))

# See: https://docs.djangoproject.com/en/dev/ref/settings/#media-url
MEDIA_URL = '/media/'

# LOCALE CONFIGURATION
LOCALE_PATHS = [
    str(APPS_DIR.path('locale')),
]

# URL Configuration
# ------------------------------------------------------------------------------
ROOT_URLCONF = 'config.urls'

# See: https://docs.djangoproject.com/en/dev/ref/settings/#wsgi-application
WSGI_APPLICATION = 'config.wsgi.application'

# PASSWORD STORAGE SETTINGS
# ------------------------------------------------------------------------------
# See https://docs.djangoproject.com/en/dev/topics/auth/passwords/#using-argon2-with-django
PASSWORD_HASHERS = [
    'django.contrib.auth.hashers.Argon2PasswordHasher',
    'django.contrib.auth.hashers.PBKDF2PasswordHasher',
    'django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher',
    'django.contrib.auth.hashers.BCryptSHA256PasswordHasher',
    'django.contrib.auth.hashers.BCryptPasswordHasher',
]

# PASSWORD VALIDATION
# https://docs.djangoproject.com/en/dev/ref/settings/#auth-password-validators
# ------------------------------------------------------------------------------

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
        'OPTIONS': {
            'min_length': 6,
        }
    },
]

# AUTHENTICATION CONFIGURATION
# ------------------------------------------------------------------------------
AUTHENTICATION_BACKENDS = [
    'django.contrib.auth.backends.ModelBackend',
    'allauth.account.auth_backends.AuthenticationBackend',
]

# Some really nice defaults
ACCOUNT_AUTHENTICATION_METHOD = 'email'
ACCOUNT_EMAIL_REQUIRED = True
if ENVIRONMENT == 'DEV':
    ACCOUNT_EMAIL_VERIFICATION = 'none'  # Options are 'optional', 'mandatory' and 'none'
    ACCOUNT_EMAIL_SUBJECT_PREFIX = 'OLC Web Portal'
else:
    ACCOUNT_EMAIL_VERIFICATION = 'none'  # Options are 'optional', 'mandatory' and 'none'

ACCOUNT_ALLOW_REGISTRATION = env.bool('DJANGO_ACCOUNT_ALLOW_REGISTRATION', True)
ACCOUNT_ADAPTER = 'olc_webportalv2.users.adapters.AccountAdapter'
SOCIALACCOUNT_ADAPTER = 'olc_webportalv2.users.adapters.SocialAccountAdapter'

# Custom user app defaults
# Select the correct user model
AUTH_USER_MODEL = 'users.User'
LOGIN_REDIRECT_URL = 'users:redirect'
LOGIN_URL = 'account_login'


# SLUGLIFIER
AUTOSLUG_SLUGIFY_FUNCTION = 'slugify.slugify'

# CELERY
INSTALLED_APPS += ['olc_webportalv2.taskapp.celery.CeleryConfig']
CELERY_BROKER_URL = env('CELERY_BROKER_URL', default='redis://redis:6379')
CELERY_RESULT_BACKEND = 'redis://redis:6379'
CELERY_ACCEPT_CONTENT = ['application/json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'

# Main Queues
CELERY_QUEUES = (
   Queue('default', Exchange='default', routing_key='default'),
   Queue('geneseekr', Exchange='geneseekr', routing_key='geneseekr'),
   Queue('cowbat', Exchange='cowbat', routing_key='cowbat'),
)

# Periodic Tasks
CELERYBEAT_SCHEDULE = {
    'monitor_tasks': {
        'task': 'olc_webportalv2.cowbat.tasks.monitor_tasks',
        'schedule': 30.0,
        'options': {'queue': 'default'},
        },

    'clean_old_containers': {
        'task': 'olc_webportalv2.cowbat.tasks.clean_old_containers',
        'schedule': crontab(hour=2),
        'options': {'queue': 'default'},
        },
}
# END CELERY

# Location of root django.contrib.admin URL, use {% url 'admin:index' %}
ADMIN_URL = r'^admin/'

# Your common stuff: Below this line define 3rd party library settings
# ------------------------------------------------------------------------------
if ENVIRONMENT == 'DEV':
    ALLOWED_HOSTS = ['0.0.0.0', '192.168.1.22', '192.168.1.20', "192.168.1.8", '192.168.1.12']
else:
    ALLOWED_HOSTS = ['0.0.0.0', 'olc.lnpr.info', '40.85.255.27', 'olc.cloud.inspection.gc.ca']
MAX_ATTEMPTS = 1

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'file': {
            'level': 'DEBUG',
            'class': 'logging.FileHandler',
            'filename': 'django_debug.log',
        },
    },
    'loggers': {
        'django': {
            'handlers': ['file'],
            'level': 'DEBUG',
            'propagate': True,
        },
        'cowbat': {
            'handlers': ['file'],
            'level': 'DEBUG',
            'propagate': True,
        },
        'olc_webportalv2.cowbat.apps.CowbatConfig': {
            'handlers': ['file'],
            'level': 'DEBUG',
            'propagate': True,
        },
        'olc_webportalv2.cowbat': {
            'handlers': ['file'],
            'level': 'DEBUG',
            'propagate': True,
        },
    },
}

# Azure storage related stuff - credentials
AZURE_ACCOUNT_NAME = env('AZURE_ACCOUNT_NAME')
AZURE_ACCOUNT_KEY = env('AZURE_ACCOUNT_KEY')
BATCH_ACCOUNT_NAME = env('BATCH_ACCOUNT_NAME')
BATCH_ACCOUNT_URL = env('BATCH_ACCOUNT_URL')
BATCH_ACCOUNT_KEY = env('BATCH_ACCOUNT_KEY')
VM_IMAGE = env('VM_IMAGE')
VM_CLIENT_ID = env('VM_CLIENT_ID')
VM_SECRET = env('VM_SECRET')
VM_TENANT = env('VM_TENANT')

if 'SENTRY_DSN' in os.environ:
    sentry_sdk.init(
        dsn=env('SENTRY_DSN'),
        integrations=[DjangoIntegration()]
    )
