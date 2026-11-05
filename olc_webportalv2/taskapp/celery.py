"""
Celery configuration for the Django app.
"""
# Local imports
import os

# Third-party imports
from celery import Celery
from django.apps import apps, AppConfig
from django.conf import settings


if not settings.configured:
    # set the default Django settings module for the 'celery' program.
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.prod')

# Create a Celery instance with the name of the Django app and the Redis
# broker URL.
app = Celery('olc_webportalv2', broker='redis://redis:6379')


class CeleryConfig(AppConfig):
    """
    Celery configuration class for the Django app. This class is responsible
    for setting up Celery with the Django settings and automatically
    discovering tasks from all installed apps.
    """
    # The name of the Django app. This should match the name of the app
    name = 'olc_webportalv2.taskapp'
    verbose_name = 'Celery Config'

    def ready(self):
        """
        This method is called when the Django app is ready. It configures Celery
        to use the Django settings and automatically discovers tasks from all
        installed apps.
        """
        # Workaround for the issue where Celery autodiscover_tasks will crash
        # python manage.py shell due to runtime errors
        if os.environ.get("SKIP_CELERY_AUTODISCOVER") == "1":
            return

        # Using a string here means the worker will not have to
        # pickle the object when using Windows.
        app.config_from_object('config.settings.prod')
        installed_apps = [app_config.name for app_config in apps.get_app_configs()]
        app.autodiscover_tasks(lambda: installed_apps, force=True)


@app.task(bind=True)
def debug_task(self):
    """
    A simple Celery task for debugging purposes. This task prints the request
    information to the console. It can be used to verify that Celery is set up
    correctly and that tasks are being executed as expected.
    """
    print(f'Request: {self.request!r}')
