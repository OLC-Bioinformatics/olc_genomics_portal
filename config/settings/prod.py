"""
Django settings for olc_webportalv2 project.

For more information on this file, see
https://docs.djangoproject.com/en/dev/topics/settings/

For the full list of settings and their values, see
https://docs.djangoproject.com/en/dev/ref/settings/
"""

# Standard imports
import os
import sentry_sdk

# Third-party imports
from celery.schedules import crontab
import django
try:
    from django.utils.translation import gettext_lazy as language
except ImportError:
    from django.utils.translation import ugettext_lazy as language
import environ
from kombu import Queue
from sentry_sdk.integrations.django import DjangoIntegration

# (olc_webportalv2/config/settings/base.py - 3 = olc_webportalv2/)
ROOT_DIR = environ.Path(__file__) - 3
APPS_DIR = ROOT_DIR.path('olc_webportalv2')
DATA_UPLOAD_MAX_NUMBER_FIELDS = 10000

# Load operating system environment variables and then prepare to use them
env = environ.Env()

# .env file, should load only in development environment
READ_DOT_ENV_FILE = env.bool('DJANGO_READ_DOT_ENV_FILE', default=True)
SECRET_KEY = env('SECRET_KEY')

if READ_DOT_ENV_FILE:
    # Operating System Environment variables have precedence over variables
    # defined in the .env file, that is to say variables from the .env files
    # will only be used if not defined as environment variables.
    ENV_FILE = str(ROOT_DIR.path('env'))
    print('Loading : {}'.format(ENV_FILE))
    env.read_env(ENV_FILE)
    print('The .env file has been loaded. See base.py for more information')

print('Loaded prod settings')

# APP CONFIGURATION
DJANGO_APPS = [
    # Default Django apps:
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.sites',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.postgres',

    # django-autocomplete-light. Has to go before admin.
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
    'microsoft_authentication',  # MS AD authentication
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
    'olc_webportalv2.primer_finder.apps.PrimerFinderConfig',
    'olc_webportalv2.ampliseq.apps.AmpliseqConfig',
    'olc_webportalv2.cowsnphr.apps.CowsnphrConfig',
    'olc_webportalv2.filezone.apps.FileZoneConfig',
    'olc_webportalv2.metadata_upload.apps.MetadataUploadConfig',
    # Need this to get django-multiselectfield to work
    'multiselectfield',

    # Django-bootstrap-forms
    'bootstrapform',

    # Sortable HTML tables
    'django_tables2',

    # Highcharts
    # 'highcharts',

    # django-widget-tweaks
    'widget_tweaks',

    # REST!
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
    'DEFAULT_PAGINATION_CLASS':
        'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 10
}

# CHANGE PERMISSIONS ON UPLOADED FILES TO ALLOW FOR COWBAT TO RUN
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
DEBUG = env.bool('DJANGO_DEBUG', True)

# FIXTURE CONFIGURATION
# ------------------------------------------------------------------------------
# https://docs.djangoproject.com/en/dev/ref/settings/#std:setting-FIXTURE_DIRS
FIXTURE_DIRS = (
    str(APPS_DIR.path('fixtures')),
)

# EMAIL CONFIGURATION
# ------------------------------------------------------------------------------
EMAIL_BACKEND = env(
    'DJANGO_EMAIL_BACKEND',
    default='django.core.mail.backends.smtp.EmailBackend'
)
EMAIL_USE_TLS = True

# EMAIL_HOST = 'smtp.gmail.com'
DEFAULT_FROM_EMAIL = \
    'cfia.foodport.donotreply-nepasrepondre.aliport.acia@inspection.gc.ca'
EMAIL_HOST = 'email-smtp.ca-central-1.amazonaws.com'
EMAIL_PORT = 587
EMAIL_RELAY_SECRET = env('EMAIL_RELAY_SECRET')

# Uncomment these when you want to have emails sent - can't be done when on
# your local machine due to firewall? (I think)
EMAIL_HOST_USER = env('EMAIL_HOST_USER')
EMAIL_HOST_PASSWORD = env('EMAIL_HOST_PASSWORD')

# MANAGER CONFIGURATION
# ------------------------------------------------------------------------------
# See: https://docs.djangoproject.com/en/dev/ref/settings/#admins
ADMINS = [
     ("""Adam Koziol""", 'adam.koziol@inspection.gc.ca'),
]

# See: https://docs.djangoproject.com/en/dev/ref/settings/#managers
MANAGERS = ADMINS

# DATABASE CONFIGURATION
# ------------------------------------------------------------------------------
# See:

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.environ['DB_NAME'],
        'USER': os.environ['DB_USER'],
        'PASSWORD': os.environ['DB_PASS'],
        'HOST': os.environ['DB_SERVICE'],
        'PORT': os.environ['DB_PORT'],
        'OPTIONS': {
            'sslmode': 'require',
        },
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
        # https://docs.djangoproject.com/en/dev/ref/settings/#std:setting-TEMPLATES-BACKEND
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        # https://docs.djangoproject.com/en/dev/ref/settings/#template-dirs
        'DIRS': [
            str(APPS_DIR.path('templates')),
        ],
        'OPTIONS': {
            # https://docs.djangoproject.com/en/dev/ref/settings/#template-debug
            'debug': DEBUG,
            # https://docs.djangoproject.com/en/dev/ref/settings/#template-loaders
            # https://docs.djangoproject.com/en/dev/ref/templates/api/#loader-types
            'loaders': [
                'django.template.loaders.filesystem.Loader',
                'django.template.loaders.app_directories.Loader',
            ],
            # https://docs.djangoproject.com/en/dev/ref/settings/#template-context-processors
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

# http://django-crispy-forms.readthedocs.io/en/latest/install.html#template-packs
CRISPY_TEMPLATE_PACK = 'bootstrap4'

# STATIC FILE CONFIGURATION
# ------------------------------------------------------------------------------
# See: https://docs.djangoproject.com/en/dev/ref/settings/#static-root
STATIC_ROOT = str(ROOT_DIR('staticfiles'))

# See: https://docs.djangoproject.com/en/dev/ref/settings/#static-url
STATIC_URL = '/static/'

# https://docs.djangoproject.com/en/dev/ref/contrib/staticfiles/#std:setting-STATICFILES_DIRS
STATICFILES_DIRS = [
    str(APPS_DIR.path('static')),
]

# https://docs.djangoproject.com/en/dev/ref/contrib/staticfiles/#staticfiles-finders
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
# https://docs.djangoproject.com/en/dev/topics/auth/passwords/#using-argon2-with-django
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
        'NAME':
            'django.contrib.auth.password_validation.'
            'UserAttributeSimilarityValidator',
    },
    {
        'NAME':
            'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME':
            'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME':
            'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

# AUTHENTICATION CONFIGURATION
# -----------------------------------------------------------------------------
AUTHENTICATION_BACKENDS = [
    'django.contrib.auth.backends.ModelBackend',
    'allauth.account.auth_backends.AuthenticationBackend',
]

# Some really nice defaults
ACCOUNT_AUTHENTICATION_METHOD = 'email'
ACCOUNT_EMAIL_REQUIRED = True
# ACCOUNT_EMAIL_VERIFICATION = 'mandatory'  # Options are 'optional',
# 'mandatory' and 'none'
ACCOUNT_EMAIL_VERIFICATION = 'mandatory'

ACCOUNT_ALLOW_REGISTRATION = env.bool(
    'DJANGO_ACCOUNT_ALLOW_REGISTRATION',
    True
)
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
CELERY_broker_url = env('CELERY_broker_url', default='redis://redis:6379')
RESULT_BACKEND = 'redis://redis:6379'
accept_content = ['application/json']
TASK_SERIALIZER = 'json'
RESULT_SERIALIZER = 'json'
# TZINFO = 'UTC'
TIMEZONE = 'Canada/Eastern'
# For celery
# CELERY_ENABLE_UTC = True

# Main Queues
task_queues = (
   Queue('default', Exchange='default', routing_key='default'),
   Queue('geneseekr', Exchange='geneseekr', routing_key='geneseekr'),
   Queue('cowbat', Exchange='cowbat', routing_key='cowbat'),
   Queue('refresh', Exchange='refresh', routing_key='refresh')
)

# Periodic Tasks
beat_schedule = {
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
    'refresh_ampliseq_containers': {
        'task': 'olc_webportalv2.ampliseq.tasks.refresh_container_names',
        'schedule': 6000.0,
        'options': {
            'queue': 'cowbat',
        }
    },
    'refresh_cowsnphr_containers': {
        'task': 'olc_webportalv2.cowsnphr.tasks.refresh_container_names',
        'schedule': 6000.0,
        'options': {
            'queue': 'cowbat',
        }
    },
}

# END CELERY

# Location of root django.contrib.admin URL, use {% url 'admin:index' %}
ADMIN_URL = r'^admin/'

# Your common stuff: Below this line define 3rd party library settings
# ------------------------------------------------------------------------------
ALLOWED_HOSTS = [
    "0.0.0.0",
    "olc.lnpr.info",
    "40.85.255.27",
    "olc.cloud.inspection.gc.ca",
    "10.148.57.4",
    "10.148.57.38",
    "localhost",
    "127.0.0.1",
    "foodport.cloud-nuage.inspection.gc.ca",
    "foodport-dev.cloud-nuage.inspection.gc.ca",
]
MAX_ATTEMPTS = 1

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
        },
    },
    "handlers": {
        "file": {
            "level": "DEBUG",
            "class": "logging.handlers.RotatingFileHandler",
            "filename": os.path.join(str(ROOT_DIR), "django_debug.log"),
            "maxBytes": 10 * 1024 * 1024,  # 10 MB
            "backupCount": 5,
            "formatter": "verbose",
        },
    },
    "loggers": {
        "django": {
            "handlers": ["file"],
            "level": "DEBUG",
            "propagate": True,
        },
        "cowbat": {
            "handlers": ["file"],
            "level": "DEBUG",
            "propagate": True,
        },
        "olc_webportalv2.cowbat.apps.CowbatConfig": {
            "handlers": ["file"],
            "level": "DEBUG",
            "propagate": True,
        },
        "olc_webportalv2.cowbat": {
            "handlers": ["file"],
            "level": "DEBUG",
            "propagate": True,
        },
    },
}

# Azure storage related stuff - credentials
AZURE_ACCOUNT_NAME = env('AZURE_ACCOUNT_NAME')
AZURE_ACCOUNT_KEY = env('AZURE_ACCOUNT_KEY')
BATCH_ACCOUNT_NAME = env('BATCH_ACCOUNT_NAME')
BATCH_ACCOUNT_URL = env('BATCH_ACCOUNT_URL')
BATCH_ACCOUNT_KEY = env('BATCH_ACCOUNT_KEY')
BATCH_ACCOUNT_SUBNET = env('BATCH_ACCOUNT_SUBNET')
VM_IMAGE = env('VM_IMAGE')
VM_CLIENT_ID = env('VM_CLIENT_ID')
VM_SECRET = env('VM_SECRET')
VM_TENANT = env('VM_TENANT')
AMPLISEQ_IMAGE = env('AMPLISEQ_IMAGE')
COWSNPHR_IMAGE = env('COWSNPHR_IMAGE')
AD_APP_ID = env("AD_APP_ID")
AD_APP_SECRET = env("AD_APP_SECRET")
SITE_URL = env("SITE_URL", default="https://foodport-dev.cloud-nuage.inspection.gc.ca")

# Define the URL for the batch service API
BATCH_SERVICE_URL = "http://batch:5000/submit_batch_request"

# Define the headers for the batch API call
BATCH_URL_HEADERS = {
    "Content-Type": "application/json"
}
try:
    ENVIRONMENT = env('ENVIRONMENT')
except django.core.exceptions.ImproperlyConfigured:
    ENVIRONMENT = 'DEV'

sentry_sdk.init(dsn=env('SENTRY_DSN'), integrations=[DjangoIntegration()])

# Tell Django to trust the X-Forwarded-Proto header that comes from the proxy
# (nginx) and to use it to determine whether the request is secure
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Microsoft AD login
MICROSOFT = {
    "app_id": AD_APP_ID,
    "app_secret": AD_APP_SECRET,
    "redirect": SITE_URL + "/microsoft_authentication/callback",
    "scopes": ["user.read"],
    "authority": "https://login.microsoftonline.com/" + VM_TENANT,
    "valid_email_domains": ["inspection.gc.ca", "gmail.com"],
    "logout_uri": SITE_URL + "/",
}
LOGIN_URL = "/microsoft_authentication/login"
# LOGIN_REDIRECT_URL = "/admin"  # optional and can be changed to any other url

LOGIN_REDIRECT_URL = "/"  # optional and can be changed to any other url


# True: creates new Django User after valid microsoft authentication.
# False: it will only allow those users which are already created in Django
# User model and will validate the email using Microsoft.
MICROSOFT_CREATE_NEW_DJANGO_USER = True  # Optional, default value is True

# Set this to False to prevent users from registering via the standard
# django-allauth registration page. You may want to do this if you only
# want users to be able to register via Microsoft AD authentication
ACCOUNT_ALLOW_REGISTRATION = False
