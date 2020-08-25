# Django-related imports
from django.conf.urls import url, include
from django.utils.translation import gettext_lazy as _
# Geneseekr-specific things
from olc_webportalv2.geneseekr import views


urlpatterns = [
    # GeneSeekr Stuff
    url(_(r'^geneseekr_home/'), views.geneseekr_home, name='geneseekr_home'),
    url(_(r'^geneseekr_query/'), views.geneseekr_query, name='geneseekr_query'),
    url(_(r'^geneseekr_processing/(?P<geneseekr_request_pk>\d+)/$'), views.geneseekr_processing, name='geneseekr_processing'),
    url(_(r'^geneseekr_results/(?P<geneseekr_request_pk>\d+)/$'), views.geneseekr_results, name='geneseekr_results'),
    url(_(r'^geneseekr_rename/(?P<geneseekr_request_pk>\d+)/$'), views.geneseekr_rename, name='geneseekr_rename'),

    # Tree Stuff
    url(_(r'^tree_home/'), views.tree_home, name='tree_home'),
    url(_(r'^tree_request/'), views.tree_request, name='tree_request'),
    url(_(r'^tree_result/(?P<tree_request_pk>\d+)/$'), views.tree_result, name='tree_result'),    
    url(_(r'^tree_rename/(?P<tree_request_pk>\d+)/$'), views.tree_rename, name='tree_rename'),

    # AMR Stuff 
    url(_(r'^amr_home/'), views.amr_home, name='amr_home'),
    url(_(r'^amr_request/'), views.amr_request, name='amr_request'),
    url(_(r'^amr_result/(?P<amr_request_pk>\d+)/'), views.amr_result, name='amr_result'),    
    url(_(r'^amr_detail/(?P<amr_detail_pk>\d+)/'), views.amr_detail, name='amr_detail'),
    url(_(r'^amr_rename/(?P<amr_request_pk>\d+)/'), views.amr_rename, name='amr_rename'),

    # Prokka Stuff 
    url(_(r'^prokka_home/'), views.prokka_home, name='prokka_home'),
    url(_(r'^prokka_request/'), views.prokka_request, name='prokka_request'),
    url(_(r'^prokka_result/(?P<prokka_request_pk>\d+)/'), views.prokka_result, name='prokka_result'),    
    url(_(r'^prokka_rename/(?P<prokka_request_pk>\d+)/'), views.prokka_rename, name='prokka_rename'),
    
    # Nearest neighbor stuff
    url(_(r'^neighbor_home/'), views.neighbor_home, name='neighbor_home'),
    url(_(r'^neighbor_request'), views.neighbor_request, name='neighbor_request'),
    url(_(r'^neighbor_result/(?P<neighbor_request_pk>\d+)/$'), views.neighbor_result, name='neighbor_result'),
    url(_(r'^neighbor_rename/(?P<neighbor_request_pk>\d+)/'), views.neighbor_rename, name='neighbor_rename'),
]
