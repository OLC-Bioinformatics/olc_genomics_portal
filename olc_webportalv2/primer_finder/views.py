# Django imports
from django.contrib.auth.decorators import login_required
from django.utils.translation import ugettext_lazy as _
from django.views.decorators.csrf import csrf_exempt
from django.contrib import messages
from django.shortcuts import \
    get_object_or_404, \
    redirect, \
    render

# Standard imports
import json

# Portal-specific imports
from olc_webportalv2.primer_finder.methods import \
    populate_primer_sets, \
    populate_primer_details, \
    populate_panel, \
    retrieve_panel_seqids, \
    populate_sequences, \
    exclusivity_panel_retrieve
from olc_webportalv2.primer_finder.models import \
    PrimerVerifierRequest, \
    VerifierPrimerSet, \
    VerifierPanel, \
    VerifierSEQID, \
    ValidatorRequest, \
    ValidatorPrimerSet, \
    ValidatorPrimers, \
    ValidatorPanel, \
    ValidatorSEQID
from olc_webportalv2.primer_finder.forms import \
    PrimerVerifierForm, \
    PrimerValidatorForm
from olc_webportalv2.primer_finder.tasks import \
    run_primer_verifier, \
    run_primer_validator
from olc_webportalv2.geneseekr.forms import EmailForm


@csrf_exempt  # needed or IE explodes
@login_required
def primer_home(request):
    return render(request,
                  'primer_finder/primer_home.html',
                  {}
                  )


@csrf_exempt  # needed or IE explodes
@login_required
def verifier_home(request):
    verifier_projects = PrimerVerifierRequest.objects.filter(user=request.user)
    if request.method == "POST":
        if request.POST.get('delete'):
            query = PrimerVerifierRequest.objects.filter(pk=request.POST.get('delete'))
            query.delete()

    return render(request,
                  'primer_finder/verifier_home.html',
                  {
                      'verifier_projects': verifier_projects
                  })


@csrf_exempt  # needed or IE explodes
@login_required
def verifier_request(request):
    form = PrimerVerifierForm()
    if request.method == "POST":
        form = PrimerVerifierForm(request.POST)
        # Form validation
        if form.is_valid():
            # Save the information from the ModelForm into the database (but don't commit yet; certain required
            # elements still need to be populated)
            primer_verifier_request = form.save(commit=False)
            # Populate the missing elements as required
            primer_verifier_request.user = request.user
            primer_verifier_request.primer_sequences = form.cleaned_data.get('primer_sequences')
            primer_verifier_request.inclusivity_panel = form.cleaned_data.get('inclusivity_panel')
            primer_verifier_request.exclusivity_panel = form.cleaned_data.get('exclusivity_panel')
            primer_verifier_request.project_name = form.cleaned_data.get('project_name')
            primer_verifier_request.status = 'Processing'
            # Save the entry
            primer_verifier_request.save()
            # Give the project a name if it was not provided
            if not primer_verifier_request.project_name:
                # Set the name of the project using the generic name used for batch jobs, containers, etc.
                # ('primer-verifier-' + primer_verifier_request.pk)
                primer_verifier_request.project_name = PrimerVerifierRequest.objects.get(
                    pk=primer_verifier_request.pk).container_namer()
            primer_verifier_request.save()
            # Extract the list of all primer base names
            primer_list = form.cleaned_data.get('primer_list')
            # Enter the list of primer base names into the database
            populate_primer_sets(pk=primer_verifier_request.pk,
                                 primer_list=primer_list)
            # Populate the database with the details of the primers
            primer_details = form.cleaned_data.get('primer_details')
            # Create a query of all the primer base_names
            primer_query = VerifierPrimerSet.objects.filter(verifier_request_id=primer_verifier_request.pk)
            populate_primer_details(query=primer_query,
                                    details=primer_details)
            # Create queries for the list of the entries in the inclusivity_panel and exclusivity_panel
            inclusivity_query = PrimerVerifierRequest.objects.filter(
                pk=primer_verifier_request.pk)[0].inclusivity_panel
            exclusivity_query = PrimerVerifierRequest.objects.filter(
                pk=primer_verifier_request.pk)[0].exclusivity_panel
            # Populate the database with the inclusivity/exclusivity panel and the genus/genera
            populate_panel(
                genera=inclusivity_query,
                pk=primer_verifier_request.pk,
                panel_type='inclusivity'
            )
            populate_panel(
                genera=exclusivity_query,
                pk=primer_verifier_request.pk,
                panel_type='exclusivity'
            )
            # Create query sets of panel objects corresponding to the PrimerVerifierRequest primary key
            panel_query = VerifierPanel.objects.filter(verifier_request_id=primer_verifier_request.pk)

            # Initialise a dictionary to store genus: list of SEQIDs
            seq_dict = dict()
            # Initialise a dictionary to store genus: seqid: sequence file path
            seq_path_dict = dict()
            # Populate a dictionary of genus: [SEQIDs] from the Azure storage containers
            for panel in panel_query:
                seq_dictionary, seq_path_dictionary = retrieve_panel_seqids(
                    seq_dictionary=seq_dict,
                    seq_path_dict=seq_path_dict,
                    container_name='primer-verifier-{genus}'.format(genus=panel.genus.lower()),
                    genus=panel.genus
                )
                seq_dict.update(seq_dictionary)
                seq_path_dict.update(seq_path_dictionary)

            # Populate the database with the sequence details for each SEQID
            populate_sequences(
                seqs=seq_dict,
                seq_path_dict=seq_path_dict,
                panel_query=panel_query,
                primer_query=primer_query,
                seqid_model=VerifierSEQID
            )
            # Create and submit the batch job
            run_primer_verifier.apply_async(queue='cowbat', args=(primer_verifier_request.pk,), countdown=10)
            # Redirect the view to verifier_processing
            return redirect('primer_finder:verifier_processing', verifier_pk=primer_verifier_request.pk)
    return render(request,
                  'primer_finder/verifier_request.html',
                  {
                      'form': form,
                  })


@csrf_exempt  # needed or IE explodes
@login_required
def verifier_processing(request, verifier_pk):
    primer_verifier_request = get_object_or_404(PrimerVerifierRequest, pk=verifier_pk)
    form = EmailForm()
    if request.method == 'POST':
        form = EmailForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data.get('email')
            if email not in primer_verifier_request.emails_array:
                primer_verifier_request.emails_array.append(email)
                primer_verifier_request.save()
                form = EmailForm()
                messages.success(request, _('Email saved'))
    return render(request,
                  'primer_finder/verifier_processing.html',
                  {
                      'primer_verifier_request': primer_verifier_request,
                      'form': form
                  })


@csrf_exempt  # needed or IE explodes
@login_required
def verifier_results(request, verifier_pk):
    primer_verifier_request = get_object_or_404(PrimerVerifierRequest, pk=verifier_pk)
    # Retrieve the PrimerSet object(s) corresponding to the verifier_request primary key
    primer_set = VerifierPrimerSet.objects.filter(verifier_request_id=primer_verifier_request.pk)
    # Retrieve the Panel object(s) corresponding to the verifier_request primary key
    panel_set = VerifierPanel.objects.filter(verifier_request_id=primer_verifier_request.pk)
    primer_list = sorted(list(set([primer.primer_name for primer in primer_set])))
    clean_primers = dict()
    # Since the hacky way I used to have primer,panel,genus-specific elements in the HTML involved using variables,
    # the primers cannot have certain characters, or start with a digit
    for primer in primer_list:
        # Initialise the clean primer as the primer
        clean_primer = primer
        # Remove illegal characters
        for char in ['-', '_']:
            if char in primer:
                clean_primer = clean_primer.replace(char, '')
                # If the primer name starts with a digit, prepend an 'a' to the start of the name
                if clean_primer[:1].isdigit():
                    clean_primer = 'a' + clean_primer
        clean_primers[primer] = clean_primer
    return render(request,
                  'primer_finder/verifier_results.html',
                  {
                      'primer_verifier_request': primer_verifier_request,
                      'mismatch_details': json.loads(primer_verifier_request.summary),
                      'primers': primer_list,
                      'clean_primers': clean_primers,
                      'panels': sorted(list(set([panel.panel for panel in panel_set])), reverse=True),
                      'genera': sorted(list(set([panel.genus for panel in panel_set])))
                  })


@csrf_exempt  # needed or IE explodes
@login_required
def validator_home(request):
    validator_projects = ValidatorRequest.objects.filter(user=request.user)
    if request.method == "POST":
        if request.POST.get('delete'):
            query = ValidatorRequest.objects.filter(pk=request.POST.get('delete'))
            query.delete()

    return render(request,
                  'primer_finder/validator_home.html',
                  {
                      'validator_projects': validator_projects
                  })


@csrf_exempt  # needed or IE explodes
@login_required
def validator_request(request):
    form = PrimerValidatorForm()
    if request.method == "POST":
        form = PrimerValidatorForm(request.POST)
        # Form validation
        if form.is_valid():
            # Save the information from the ModelForm into the database (but don't commit yet; certain required
            # elements still need to be populated)
            primer_validator_request = form.save(commit=False)
            # Populate the missing elements as required
            primer_validator_request.user = request.user
            primer_validator_request.forward_primer = form.cleaned_data.get('forward_primer')
            primer_validator_request.reverse_primer = form.cleaned_data.get('reverse_primer')
            primer_validator_request.probe_sequence = form.cleaned_data.get('probe_sequence')
            primer_validator_request.inclusivity_panel = form.cleaned_data.get('inclusivity_panel')
            primer_validator_request.project_name = form.cleaned_data.get('project_name')
            primer_validator_request.status = 'Processing'
            # Save the entry
            primer_validator_request.save()
            # Give the project a name if it was not provided
            if not primer_validator_request.project_name:
                # Set the name of the project using the generic name used for batch jobs, containers, etc.
                # ('primer-validator-' + primer_validator_request.pk)
                primer_validator_request.project_name = ValidatorRequest.objects.get(
                    pk=primer_validator_request.pk).container_namer()
            primer_validator_request.save()
            # Extract the list of all primer base names
            primer_list = form.cleaned_data.get('primer_list')
            # Enter the list of primer base names into the database
            populate_primer_sets(
                pk=primer_validator_request.pk,
                primer_list=primer_list,
                model=ValidatorPrimerSet
            )
            # Populate the database with the details of the primers
            primer_details = form.cleaned_data.get('primer_details')
            # Create a query of all the primer base_names
            primer_query = ValidatorPrimerSet.objects.filter(validator_request_id=primer_validator_request.pk)
            populate_primer_details(
                query=primer_query,
                details=primer_details,
                model=ValidatorPrimers
            )
            # Create queries for the list of the entries in the inclusivity_panel and exclusivity_panel
            inclusivity_query = ValidatorRequest.objects.filter(
                pk=primer_validator_request.pk)[0].inclusivity_panel
            # Populate the exclusivity query
            exclusivity_panel = exclusivity_panel_retrieve(inclusivity=inclusivity_query[0])
            primer_validator_request.exclusivity_panel = exclusivity_panel
            primer_validator_request.save()
            exclusivity_query = ValidatorRequest.objects.filter(
                pk=primer_validator_request.pk)[0].exclusivity_panel
            # Populate the database with the inclusivity/exclusivity panel and the genus/genera
            populate_panel(
                genera=inclusivity_query,
                pk=primer_validator_request.pk,
                panel_type='inclusivity',
                panel=ValidatorPanel
            )
            populate_panel(
                genera=exclusivity_query,
                pk=primer_validator_request.pk,
                panel_type='exclusivity',
                panel=ValidatorPanel
            )
            # Create query sets of panel objects corresponding to the PrimerValidatorRequest primary key
            panel_query = ValidatorPanel.objects.filter(validator_request_id=primer_validator_request.pk)
            # Initialise a dictionary to store genus: list of SEQIDs
            seq_dict = dict()
            # Initialise a dictionary to store genus: seqid: sequence file path
            seq_path_dict = dict()
            # Populate a dictionary of genus: [SEQIDs] from the Azure storage containers
            for panel in panel_query:
                seq_dictionary, seq_path_dictionary = retrieve_panel_seqids(
                    seq_dictionary=seq_dict,
                    seq_path_dict=seq_path_dict,
                    container_name='primer-verifier-{genus}'.format(genus=panel.genus.lower()),
                    genus=panel.genus
                )
                seq_dict.update(seq_dictionary)
                seq_path_dict.update(seq_path_dictionary)

            # Populate the database with the sequence details for each SEQID
            populate_sequences(
                seqs=seq_dict,
                seq_path_dict=seq_path_dict,
                panel_query=panel_query,
                primer_query=primer_query,
                seqid_model=ValidatorSEQID
            )
            # Create and submit the batch job
            run_primer_validator.apply_async(queue='cowbat', args=(primer_validator_request.pk,), countdown=10)
            # Redirect the view to validator_processing
            return redirect('primer_finder:validator_processing', validator_pk=primer_validator_request.pk)
    return render(request,
                  'primer_finder/validator_request.html',
                  {
                      'form': form,
                  })


@csrf_exempt  # needed or IE explodes
@login_required
def validator_processing(request, validator_pk):
    primer_validator_request = get_object_or_404(ValidatorRequest, pk=validator_pk)
    form = EmailForm()
    if request.method == 'POST':
        form = EmailForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data.get('email')
            if email not in primer_validator_request.emails_array:
                primer_validator_request.emails_array.append(email)
                primer_validator_request.save()
                form = EmailForm()
                messages.success(request, _('Email saved'))
    return render(request,
                  'primer_finder/validator_processing.html',
                  {
                      'request': primer_validator_request,
                      'form': form
                  })


@csrf_exempt  # needed or IE explodes
@login_required
def validator_results(request, validator_pk):
    primer_validator_request = get_object_or_404(ValidatorRequest, pk=validator_pk)
    # Retrieve the PrimerSet object(s) corresponding to the validator_request primary key
    primer_set = ValidatorPrimerSet.objects.filter(validator_request_id=primer_validator_request.pk)
    # Retrieve the Panel object(s) corresponding to the validator_request primary key
    panel_set = ValidatorPanel.objects.filter(validator_request_id=primer_validator_request.pk)
    primer_list = sorted(list(set([primer.primer_name for primer in primer_set])))
    clean_primers = dict()
    # Since the hacky way I used to have primer,panel,genus-specific elements in the HTML involved using variables,
    # the primers cannot have certain characters, or start with a digit
    for primer in primer_list:
        # Initialise the clean primer as the primer
        clean_primer = primer
        # Remove illegal characters
        for char in ['-', '_']:
            if char in primer:
                clean_primer = clean_primer.replace(char, '')
                # If the primer name starts with a digit, prepend an 'a' to the start of the name
                if clean_primer[:1].isdigit():
                    clean_primer = 'a' + clean_primer
        clean_primers[primer] = clean_primer
    return render(request,
                  'primer_finder/validator_results.html',
                  {
                      'primer_validator_request': primer_validator_request,
                      'mismatch_details': json.loads(primer_validator_request.summary),
                      'primers': primer_list,
                      'clean_primers': clean_primers,
                      'panels': sorted(list(set([panel.panel for panel in panel_set])), reverse=True),
                      'genera': sorted(list(set([panel.genus for panel in panel_set])))
                  }
                  )


@csrf_exempt  # needed or IE explodes
@login_required
def validator_report(request, validator_pk):
    primer_validator_request = get_object_or_404(ValidatorRequest, pk=validator_pk)
    # Retrieve the PrimerSet object(s) corresponding to the validator_request primary key
    primer_set = ValidatorPrimerSet.objects.filter(validator_request_id=primer_validator_request.pk)
    # Retrieve the Panel object(s) corresponding to the validator_request primary key
    panel_set = ValidatorPanel.objects.filter(validator_request_id=primer_validator_request.pk)
    primer_list = sorted(list(set([primer.primer_name for primer in primer_set])))
    clean_primers = dict()
    # Since the hacky way I used to have primer,panel,genus-specific elements in the HTML involved using variables,
    # the primers cannot have certain characters, or start with a digit
    for primer in primer_list:
        # Initialise the clean primer as the primer
        clean_primer = primer
        # Remove illegal characters
        for char in ['-', '_']:
            if char in primer:
                clean_primer = clean_primer.replace(char, '')
                # If the primer name starts with a digit, prepend an 'a' to the start of the name
                if clean_primer[:1].isdigit():
                    clean_primer = 'a' + clean_primer
        clean_primers[primer] = clean_primer
    return render(request,
                  'primer_finder/validator_report.html',
                  {
                      'primer_validator_request': primer_validator_request,
                      'mismatch_details': json.loads(primer_validator_request.summary),
                      'totals': json.loads(primer_validator_request.totals),
                      'allowed_mismatches': [6, 5, 4, 3, 2, 1],
                      'primers': primer_list,
                      'clean_primers': clean_primers,
                      'panels': sorted(list(set([panel.panel for panel in panel_set])), reverse=True),
                      'genera': sorted(list(set([panel.genus for panel in panel_set]))),
                      'forward': primer_validator_request.forward_primer.split('\n')[1],
                      'reverse': primer_validator_request.reverse_primer.split('\n')[1]
                  })


@csrf_exempt  # needed or IE explodes
@login_required
def finder_home(request):
    finder_projects = PrimerVerifierRequest.objects.filter(user=request.user)

    if request.method == "POST":
        if request.POST.get('delete'):
            query = PrimerVerifierRequest.objects.filter(pk=request.POST.get('delete'))
            query.delete()

    return render(request,
                  'primer_finder/verifier_home.html',
                  {
                      'finder_projects': finder_projects
                  })
