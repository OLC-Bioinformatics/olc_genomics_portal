from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class UsersConfig(AppConfig):
    name = 'olc_webportalv2.users'
    verbose_name = _("Users")

    def ready(self):
        """Override this to put in:
            Users system checks
            Users signal registration
        """
        pass
