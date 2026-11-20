from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse
from django.utils import translation
from django.views.i18n import set_language as django_set_language
from django.views.generic import DetailView, ListView, RedirectView, UpdateView

from .models import User


def set_language(request):
    """Persist language changes for authenticated users and keep the cookie."""
    language = request.POST.get("language")
    supported_languages = {code for code, _name in settings.LANGUAGES}
    if (
        request.method == "POST"
        and request.user.is_authenticated
        and language in supported_languages
    ):
        request.user.language = language
        request.user.save(update_fields=["language"])
    return django_set_language(request)


class UserDetailView(LoginRequiredMixin, DetailView):
    model = User
    # These next two lines tell the view to index lookups by username
    slug_field = 'username'
    slug_url_kwarg = 'username'


class UserRedirectView(LoginRequiredMixin, RedirectView):
    permanent = False

    def get_redirect_url(self):
        lang = self.request.user.language
        translation.activate(lang)
        return reverse('users:detail',
                       kwargs={'username': self.request.user.username})

class UserUpdateView(LoginRequiredMixin, UpdateView):

    fields = ['name',
              'language',
              'lab',
              'rank']

    # we already imported User in the view code above, remember?
    model = User
    
    # send the user back to their own page after a successful update
    def get_success_url(self):
        lang =self.request.POST.get('language')
        translation.activate(lang)
        return reverse('users:detail',
                       kwargs={'username': self.request.user.username})

    def get_object(self):
        # Only get the User record for the user making the request
        return User.objects.get(username=self.request.user.username)


class UserListView(LoginRequiredMixin, ListView):
    model = User
    # These next two lines tell the view to index lookups by username
    slug_field = 'username'
    slug_url_kwarg = 'username'
