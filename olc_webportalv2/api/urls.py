from olc_webportalv2.api import views
from django.conf.urls import url, include
from django.urls import path
from rest_framework.routers import DefaultRouter
from rest_framework.urlpatterns import format_suffix_patterns
from rest_framework.schemas import get_schema_view


schema_view = get_schema_view(title='API')

# Router
router = DefaultRouter()
router.register(
    r"research_assembly_runs",
    views.SequencingRunViewSet,
    basename="research_assembly_runs"
)

# URL patterns
urlpatterns = [

    # REST API Stuff
    path('schema/', schema_view),
    # TODO: Enforce run name regex
    path('upload/<str:run_name>/<str:filename>', views.UploadView.as_view()),
    path('run_cowbat/<str:run_name>', views.StartCowbatView.as_view()),
    path('email_relay/', views.EmailRelayView.as_view(), name='email_relay'),
    path(
        'research_assembly/',
        views.ResearchAssemblyView.as_view(),
        name='research_assembly'
    ),
]

urlpatterns = format_suffix_patterns(urlpatterns)

urlpatterns += router.urls
