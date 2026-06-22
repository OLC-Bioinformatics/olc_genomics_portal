"""
URL configuration for the API app.
"""

# Django imports
from django.urls import path

# Third party imports
from rest_framework.routers import DefaultRouter
from rest_framework.schemas import get_schema_view
from rest_framework.urlpatterns import format_suffix_patterns

# Local imports
from olc_webportalv2.api import views

app_name = "api"

schema_view = get_schema_view(
    title="API",
)

# Router
router = DefaultRouter()
router.register(
    "research_assembly_runs",
    views.SequencingRunViewSet,
    basename="research_assembly_runs",
)

# URL patterns
urlpatterns = [
    # REST API Stuff
    path(
        "schema/",
        schema_view,
        name="schema",
    ),
    path(
        "upload/<str:run_name>/<str:filename>",
        views.UploadView.as_view(),
        name="upload",
    ),
    path(
        "run_cowbat/<str:run_name>",
        views.StartCowbatView.as_view(),
        name="run_cowbat",
    ),
    path(
        "email_relay/",
        views.EmailRelayView.as_view(),
        name="email_relay",
    ),
    path(
        "research_assembly/",
        views.ResearchAssemblyView.as_view(),
        name="research_assembly",
    ),
]

urlpatterns = format_suffix_patterns(urlpatterns)

urlpatterns += router.urls