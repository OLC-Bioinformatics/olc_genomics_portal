"""
URL configuration for the users app.
"""

# Django imports
from django.urls import path

# Local imports
from . import views

app_name = "users"

# URL patterns for the users app
urlpatterns = [
    path(
        "",
        views.UserListView.as_view(),
        name="list",
    ),
    path(
        "~redirect/",
        views.UserRedirectView.as_view(),
        name="redirect",
    ),
    path(
        "<str:username>/",
        views.UserDetailView.as_view(),
        name="detail",
    ),
    path(
        "~update/",
        views.UserUpdateView.as_view(),
        name="update",
    ),
]
