#!/usr/bin/env python

# Django imports
from django.contrib import admin

# Portal-specific imports
from olc_webportalv2.primer_finder.models import (
    VerifierRequest,
    VerifierPanel,
    VerifierSEQID,
    VerifierPrimerSet,
    VerifierPrimers,
    VerifierAnalysisTask,
    ValidatorRequest,
    ValidatorPanel,
    ValidatorPrimerSet,
    ValidatorPrimers,
    ValidatorSEQID,
    ValidatorAnalysisTask,
    ValidatorSample,
    ValidatorTarget,
)


admin.site.register(VerifierRequest)
admin.site.register(VerifierPrimerSet)
admin.site.register(VerifierPrimers)
admin.site.register(VerifierPanel)
admin.site.register(VerifierSEQID)
admin.site.register(VerifierAnalysisTask)

admin.site.register(ValidatorRequest)
admin.site.register(ValidatorPrimerSet)
admin.site.register(ValidatorPrimers)
admin.site.register(ValidatorPanel)
admin.site.register(ValidatorSEQID)
admin.site.register(ValidatorAnalysisTask)
admin.site.register(ValidatorSample)
admin.site.register(ValidatorTarget)
