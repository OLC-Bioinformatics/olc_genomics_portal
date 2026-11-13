"""Settings for isolated local and automated tests."""

import os

from django.core.exceptions import ImproperlyConfigured

os.environ["DJANGO_READ_DOT_ENV_FILE"] = "false"
os.environ["SECRET_KEY"] = "foodport-test-only-secret-key"
os.environ["EMAIL_RELAY_SECRET"] = "foodport-test-only-email-secret"
os.environ["EMAIL_HOST_USER"] = "test@example.invalid"
os.environ["EMAIL_HOST_PASSWORD"] = "not-used"
os.environ["SENTRY_DSN"] = ""
os.environ["MICROSOFT_AUTH_ENABLED"] = "false"
os.environ["ENVIRONMENT"] = "TEST"

_TEST_DB = {
    "NAME": os.environ.get("FOODPORT_TEST_DB_NAME", "foodport_test"),
    "USER": os.environ.get("FOODPORT_TEST_DB_USER", "foodport_test"),
    "PASSWORD": os.environ.get(
        "FOODPORT_TEST_DB_PASSWORD", "foodport_test_only"
    ),
    "HOST": os.environ.get("FOODPORT_TEST_DB_HOST", "test-postgres"),
    "PORT": os.environ.get("FOODPORT_TEST_DB_PORT", "5432"),
    "TEST_NAME": os.environ.get(
        "FOODPORT_TEST_DB_TEST_NAME",
        "test_foodport_test",
    ),
}

if _TEST_DB["HOST"] not in {
    "localhost",
    "127.0.0.1",
    "::1",
    "test-postgres",
    "postgres",
}:
    raise ImproperlyConfigured(
        "Test settings only allow a local PostgreSQL test host."
    )
if "test" not in _TEST_DB["NAME"].lower():
    raise ImproperlyConfigured('The test database name must include "test".')
if "test" not in _TEST_DB["TEST_NAME"].lower():
    raise ImproperlyConfigured(
        'The test-run database name must include "test".'
    )

os.environ["DB_NAME"] = _TEST_DB["NAME"]
os.environ["DB_USER"] = _TEST_DB["USER"]
os.environ["DB_PASS"] = _TEST_DB["PASSWORD"]
os.environ["DB_SERVICE"] = _TEST_DB["HOST"]
os.environ["DB_PORT"] = _TEST_DB["PORT"]

from .prod import *  # noqa: E402, F403, I001


DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": _TEST_DB["NAME"],
        "USER": _TEST_DB["USER"],
        "PASSWORD": _TEST_DB["PASSWORD"],
        "HOST": _TEST_DB["HOST"],
        "PORT": _TEST_DB["PORT"],
        "ATOMIC_REQUESTS": True,
        "TEST": {"NAME": _TEST_DB["TEST_NAME"]},
    }
}

ENVIRONMENT = "TEST"
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
MEDIA_ROOT = os.environ.get(
    "FOODPORT_TEST_MEDIA_ROOT",
    "/tmp/foodport-test-media",
)
SENTRY_DSN = ""
CELERY_BROKER_URL = "memory://"
CELERY_broker_url = "memory://"
CELERY_RESULT_BACKEND = "cache+memory://"
RESULT_BACKEND = "cache+memory://"
