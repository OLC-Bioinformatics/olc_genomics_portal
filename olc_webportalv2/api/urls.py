from olc_webportalv2.api import views
from django.conf.urls import url, include
from django.urls import path
from rest_framework.urlpatterns import format_suffix_patterns
from rest_framework.schemas import get_schema_view


schema_view = get_schema_view(title='API')


urlpatterns = [

    # REST API Stuff
    path('schema/', schema_view),
    # TODO: Enforce run name regex
    path('upload/<str:run_name>/<str:filename>', views.UploadView.as_view()),
    path('run_cowbat/<str:run_name>', views.StartCowbatView.as_view()),
]

urlpatterns = format_suffix_patterns(urlpatterns)
