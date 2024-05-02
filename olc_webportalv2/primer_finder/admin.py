#!/usr/bin/env python

# Django imports
from django.contrib import admin

# Portal-specific imports
from olc_webportalv2.primer_finder.models import \
    PrimerVerifierRequest, \
    VerifierPanel, \
    VerifierSEQID, \
    VerifierPrimerSet, \
    VerifierPrimers, \
    VerifierAzureRequest, \
    ValidatorRequest, \
    ValidatorPanel, \
    ValidatorPrimerSet, \
    ValidatorPrimers, \
    ValidatorSEQID, \
    PrimerValidatorAzureRequest


admin.site.register(PrimerVerifierRequest)
admin.site.register(VerifierPrimerSet)
admin.site.register(VerifierPrimers)
admin.site.register(VerifierPanel)
admin.site.register(VerifierSEQID)
admin.site.register(VerifierAzureRequest)

admin.site.register(ValidatorRequest)
admin.site.register(ValidatorPrimerSet)
admin.site.register(ValidatorPrimers)
admin.site.register(ValidatorPanel)
admin.site.register(ValidatorSEQID)
admin.site.register(PrimerValidatorAzureRequest)
