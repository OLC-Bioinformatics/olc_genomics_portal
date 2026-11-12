"""
URL configuration for the API app.
"""

# Django imports
from django.urls import path

# Third party imports
from rest_framework.routers import DefaultRouter
from rest_framework.schemas import get_schema_view
from rest_framework.urlpatterns import format_suffix_patterns
from rest_framework.authtoken.views import obtain_auth_token
from olc_webportalv2.api.entra import (
    EntraDesktopExchangeView,
    EntraDesktopLogoutView,
)
from olc_webportalv2.api.pairing import (
    PairingExchangeView,
    PairingStartView,
    pairing_approval,
)

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
    path("auth/token/", obtain_auth_token, name="api_token_auth"),
    path(
        "auth/entra/exchange/",
        EntraDesktopExchangeView.as_view(),
        name="entra_desktop_exchange",
    ),
    path(
        "auth/entra/logout/",
        EntraDesktopLogoutView.as_view(),
        name="entra_desktop_logout",
    ),
    path(
        "auth/pairing/start/",
        PairingStartView.as_view(),
        name="desktop_pairing_start",
    ),
    path(
        "auth/pairing/exchange/",
        PairingExchangeView.as_view(),
        name="desktop_pairing_exchange",
    ),
    path(
        "auth/pairing/approve/<str:pairing_id>/",
        pairing_approval,
        name="desktop_pairing_approval",
    ),
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
    path(
        "nanopore/runs/",
        views.NanoporeRunView.as_view(),
        name="nanopore_runs",
    ),
    path(
        "nanopore/runs/<int:run_pk>/",
        views.NanoporeRunDetailView.as_view(),
        name="nanopore_run_detail",
    ),
    path(
        "nanopore/runs/<int:run_pk>/results/latest/",
        views.NanoporeLatestResultView.as_view(),
        name="nanopore_latest_result",
    ),
    path(
        "nanopore/runs/<int:run_pk>/results/<int:generation>/",
        views.NanoporeIterationResultView.as_view(),
        name="nanopore_iteration_result",
    ),
    path(
        "nanopore/runs/<int:run_pk>/files/prepare/",
        views.NanoporeFilePrepareView.as_view(),
        name="nanopore_file_prepare",
    ),
    path(
        "nanopore/runs/<int:run_pk>/files/<int:file_pk>/complete/",
        views.NanoporeFileCompleteView.as_view(),
        name="nanopore_file_complete",
    ),
    path(
        "nanopore/runs/<int:run_pk>/finalize/",
        views.NanoporeFinalizeView.as_view(),
        name="nanopore_finalize",
    ),
]

urlpatterns = format_suffix_patterns(urlpatterns)

urlpatterns += router.urls