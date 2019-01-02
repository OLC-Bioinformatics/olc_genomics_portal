from olc_webportalv2.new_multisample import views
from django.conf.urls import url, include


urlpatterns = [

    # Index page
    url(r'^$', views.new_multisample, name='new_multisample'),
    # Individual project page
    url(r'^project/(?P<project_id>\d+)/$',
        views.project_detail, name="project_detail"),

    # Project table only
    url(r'^project-table/(?P<project_id>\d+)/$',
        views.only_project_table, name="only_project_table"),

    # Upload data for project
    url(r'^project/(?P<project_id>\d+)/upload$',
        views.upload_samples, name="upload_samples"),

    url(r'^sample/(?P<sample_id>\d+)/sendsketch_results_table$',
        views.sendsketch_results_table, name="sendsketch_results_table"),

    # GeneSippr result tables.
    url(r'^project/(?P<project_id>\d+)/genesippr_results$',
        views.display_genesippr_results, name="display_genesippr_results"),

    # Confindr result tables.
    url(r'^project/(?P<project_id>\d+)/confindr_results$',
        views.confindr_results_table, name="confindr_results_table"),

    # GenomeQAML result tables.
    url(r'^project/(?P<project_id>\d+)/genomeqaml_results$',
        views.genomeqaml_results, name="genomeqaml_results"),

    # Remove project
    url(r'^project/(?P<project_id>\d+)/remove$',
        views.project_remove, name="project_remove"),

    # Project remove confirm
    url(r'^project/(?P<project_id>\d+)/remove_confirm$',
        views.project_remove_confirm, name="project_remove_confirm"),

    # Remove sample
    url(r'^sample/(?P<sample_id>\d+)/remove$',
        views.sample_remove, name="sample_remove"),

    # Sample remove confirm
    url(r'^sample/(?P<sample_id>\d+)/remove_confirm$',
        views.sample_remove_confirm, name="sample_remove_confirm"),

    # GDCS detail
    url(r'^sample/(?P<sample_id>\d+)/gdcs_detail$',
        views.gdcs_detail, name="gdcs_detail"),

    # Genomeqaml detail
    url(r'^sample/(?P<sample_id>\d+)/genomeqaml_detail$',
        views.genomeqaml_detail, name="genomeqaml_detail"),

    # AMR detail
    url(r'^sample/(?P<sample_id>\d+)/amr_detail$',
        views.amr_detail, name="amr_detail"),

    # Forbidden
    url(r'^forbidden$',
        views.forbidden, name="forbidden"),

    # Task Queue
    url(r'^task_queue$',
        views.task_queue, name="task_queue"),
]
