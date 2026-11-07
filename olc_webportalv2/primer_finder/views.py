#!/usr/bin/env python3

"""
Views for the primer_finder app. These views are responsible for rendering the
HTML templates, and for handling the logic of the primer_finder app.
"""

# Standard imports
import json

# Django imports
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.utils.translation import ugettext_lazy as _
from django.shortcuts import (
    get_object_or_404,
    redirect,
    render
)
from django.views.decorators.csrf import csrf_exempt
from django.db import transaction

# Third-party imports

# Portal-specific imports
from olc_webportalv2.geneseekr.forms import EmailForm

from olc_webportalv2.primer_finder.forms import (
    ValidatorExperimentForm,
    ValidatorTargetFormSetFactory,
    VerifierForm,
    canonicalize_primer_base_name,
)
from olc_webportalv2.primer_finder.methods import (
    populate_panel,
    populate_primer_details,
    populate_primer_sets,
)
from olc_webportalv2.primer_finder.models import (
    VerifierRequest,
    ValidatorPanel,
    ValidatorPrimers,
    ValidatorPrimerSet,
    ValidatorRequest,
    ValidatorSample,
    ValidatorTarget,
    VerifierPanel,
    VerifierPrimerSet
)

from olc_webportalv2.primer_finder.tasks import (
    run_primer_validator,
    run_primer_verifier
)


@csrf_exempt  # needed or IE explodes
@login_required
def primer_home(request: HttpRequest) -> HttpResponse:
    """
    View for the primer home page. This view is responsible for rendering the
    home page.

    :param request: The HTTP request object

    :return: The HTTP response object
    """
    # Render the home page
    return render(
        request,
        'primer_finder/primer_home.html',
        {}
    )


@csrf_exempt  # needed or IE explodes
@login_required
def primer_verifier_home(request: HttpRequest) -> HttpResponse:
    """
    View for the primer validator home page. This view is responsible for
    rendering the home page, and for deleting projects from the database.

    :param request: The HTTP request object

    :return: The HTTP response object
    """

    # Retrieve all the projects from the database that are associated with the
    # current user
    verifier_projects = VerifierRequest.objects.filter(user=request.user)
    if request.method == "POST":

        # If the delete button is pressed, delete the project from the database
        if request.POST.get('delete'):
            query = VerifierRequest.objects.filter(
                pk=request.POST.get('delete')
            )
            query.delete()

    return render(
        request,
        'primer_finder/verifier_home.html',
        {
            'verifier_projects': verifier_projects
        }
    )


@csrf_exempt  # needed or IE explodes
@login_required
def primer_verifier_request(request: HttpRequest) -> HttpResponse:
    """
    View for the primer validator request form. This view is responsible for
    rendering the form, validating the form, and saving the form to the
    database. The view also populates the database with the primer details
    and the panel details. The view also creates and submits the batch job
    to the Celery queue.

    Args:
        request (object): The HTTP request object.

    Returns:
        object: The HTTP response object.
    """
    form = VerifierForm()
    if request.method == "POST":
        form = VerifierForm(request.POST)
        if form.is_valid():
            # Save the form but don't commit yet
            validator_request = form.save(commit=False)
            validator_request.user = request.user
            validator_request.primer_sequences = form.cleaned_data.get(
                'primer_sequences'
            )
            validator_request.probe_sequence = form.cleaned_data.get(
                'probe_sequence'
            )
            inclusivity_panel = list(form.cleaned_data.get(
                'inclusivity_panel'
            ) or [])
            exclusivity_panel = list(form.cleaned_data.get(
                'exclusivity_panel'
            ) or [])
            for panel_values, field_name in (
                (inclusivity_panel, 'inclusivity_benchmark'),
                (exclusivity_panel, 'exclusivity_benchmark'),
            ):
                benchmark = form.cleaned_data.get(field_name)
                if benchmark and benchmark not in panel_values:
                    panel_values.append(benchmark)
            validator_request.inclusivity_panel = inclusivity_panel
            validator_request.exclusivity_panel = exclusivity_panel
            validator_request.inclusivity_genus = form.cleaned_data.get(
                'inclusivity_genus', ''
            )
            validator_request.exclusivity_genus = form.cleaned_data.get(
                'exclusivity_genus', ''
            )
            validator_request.inclusivity_seqids = form.cleaned_data.get(
                'inclusivity_seqids', []
            )
            validator_request.exclusivity_seqids = form.cleaned_data.get(
                'exclusivity_seqids', []
            )
            validator_request.inclusivity_benchmark = form.cleaned_data.get(
                'inclusivity_benchmark', ''
            )
            validator_request.exclusivity_benchmark = form.cleaned_data.get(
                'exclusivity_benchmark', ''
            )
            validator_request.project_name = form.cleaned_data.get(
                'project_name'
            )
            validator_request.status = 'Processing'
            validator_request.save()

            # Give the project a name if it was not provided
            if not validator_request.project_name:
                validator_request.project_name = \
                    VerifierRequest.objects.get(
                        pk=validator_request.pk
                    ).container_namer()
            validator_request.save()

            # Extract the list of all primer base names
            primer_list = form.cleaned_data.get('primer_list') or []

            # Enter the list of primer base names into the database
            populate_primer_sets(
                pk=validator_request.pk,
                primer_list=primer_list
            )

            # Populate the database with the details of the primers
            primer_details = form.cleaned_data.get('primer_details') or {}

            # Create a query of all the primer base_names
            primer_query = VerifierPrimerSet.objects.filter(
                verifier_request_id=validator_request.pk
            )

            # Populate the database with the details of the primers
            populate_primer_details(
                query=primer_query,
                details=primer_details
            )

            # Create queries for the list of the entries in the
            # inclusivity_panel and exclusivity_panel
            inclusivity_query = VerifierRequest.objects.filter(
                pk=validator_request.pk)[0].inclusivity_panel
            exclusivity_query = VerifierRequest.objects.filter(
                pk=validator_request.pk)[0].exclusivity_panel

            # Populate the database with the inclusivity/exclusivity panel and
            # the genus/genera
            populate_panel(
                genera=inclusivity_query,
                pk=validator_request.pk,
                panel_type='inclusivity'
            )
            populate_panel(
                genera=exclusivity_query,
                pk=validator_request.pk,
                panel_type='exclusivity'
            )
            populate_panel(
                genera=([form.cleaned_data.get('inclusivity_genus')] if
                        form.cleaned_data.get('inclusivity_genus') else []) +
                (['selected-seqids'] if form.cleaned_data.get(
                    'inclusivity_seqids'
                ) else []),
                pk=validator_request.pk,
                panel_type='inclusivity'
            )
            populate_panel(
                genera=([form.cleaned_data.get('exclusivity_genus')] if
                        form.cleaned_data.get('exclusivity_genus') else []) +
                (['selected-seqids'] if form.cleaned_data.get(
                    'exclusivity_seqids'
                ) else []),
                pk=validator_request.pk,
                panel_type='exclusivity'
            )

            # Create and submit the batch job
            run_primer_verifier.apply_async(
                queue='primer_finder',
                kwargs={'verifier_request_pk': validator_request.pk},
                countdown=10
            )
            # Redirect the view to verifier_processing
            return redirect(
                'primer_finder:primer_verifier_processing',
                verifier_pk=validator_request.pk
            )
    return render(
        request,
        'primer_finder/verifier_request.html',
        {
            'form': form,
        }
    )


@csrf_exempt  # needed or IE explodes
@login_required
def primer_verifier_processing(
    request: HttpRequest,
    verifier_pk: int
) -> HttpResponse:
    """
    View for the primer validator processing page. This view is responsible for
    rendering the processing page, and for saving the email to the database.

    :param request: The HTTP request object
    :param verifier_pk: The primary key of the verifier request

    :return: The HTTP response object
    """
    # Retrieve the VerifierRequest object
    verifier_request = get_object_or_404(
        VerifierRequest,
        pk=verifier_pk
    )

    # Create an instance of the EmailForm
    form = EmailForm()

    # If the form is submitted, save the email to the database
    if request.method == 'POST':
        form = EmailForm(request.POST)

        # Form validation
        if form.is_valid():

            # Save the email to the database
            email_address = form.cleaned_data.get('email')

            # Ensure that the email is provided and a string
            if email_address and isinstance(email_address, str):
                if verifier_request.emails_array is None:
                    verifier_request.emails_array = []

                # If the email is not already in the database, save it
                if email_address not in verifier_request.emails_array:
                    verifier_request.emails_array.append(email_address)
                    verifier_request.save()
                    form = EmailForm()
                    messages.success(request, _("Email saved"))
    return render(
        request,
        'primer_finder/verifier_processing.html',
        {
            'primer_verifier_request': verifier_request,
            'form': form
        }
    )


@csrf_exempt  # needed or IE explodes
@login_required
def primer_verifier_results(
    request: HttpRequest,
    verifier_pk: int
) -> HttpResponse:
    """
    View for the primer validator results page. This view is responsible for
    rendering the results page, and for populating the page with the
    appropriate data.

    :param request: The HTTP request object
    :param verifier_pk: The primary key of the verifier request

    :return: The HTTP response object
    """
    verifier_request = get_object_or_404(
        VerifierRequest,
        pk=verifier_pk
    )

    # Retrieve the PrimerSet object(s) corresponding to the verifier_request
    # primary key
    primer_set = VerifierPrimerSet.objects.filter(
        verifier_request_id=verifier_request.pk
    )

    # Retrieve the Panel object(s) corresponding to the verifier_request
    # primary key
    panel_set = VerifierPanel.objects.filter(
        verifier_request_id=verifier_request.pk
    )

    # Create a list of all the primer names
    primer_list = sorted({primer.primer_name for primer in primer_set})

    # Create a dictionary to store the clean primers
    clean_primers = {}

    # Since the hacky way I used to have primer,panel,genus-specific elements
    # in the HTML involved using variables, the primers cannot have certain
    # characters, or start with a digit
    for primer in primer_list:

        # Initialise the clean primer as the primer
        clean_primer = primer

        # Remove illegal characters
        for char in ['-', '_']:
            if char in primer:
                clean_primer = clean_primer.replace(char, '')

                # If the primer name starts with a digit, prepend an 'a' to
                # the start of the name
                if clean_primer[:1].isdigit():
                    clean_primer = 'a' + clean_primer
        clean_primers[primer] = clean_primer
    return render(
        request,
        'primer_finder/verifier_results.html',
        {
            'request': verifier_request,
            'mismatch_details': json.loads(verifier_request.summary),
            'primers': primer_list,
            'clean_primers': clean_primers,
            'panels': sorted(
                {
                    panel.panel for panel in panel_set
                }, reverse=True
            ),
            'genera': sorted(
                {
                    panel.genus.lower() for panel in panel_set
                }
            )
        }
    )


@csrf_exempt  # needed or IE explodes
@login_required
def primer_validator_home(request: HttpRequest) -> HttpResponse:
    """
    View for the primer verifier home page. This view is responsible for
    rendering the home page and for displaying the user's primer verifier
    projects.

    Args:
        request (HttpRequest): The HTTP request object.

    Returns:
        HttpResponse: The HTTP response object.
    """
    # Get the user's validator projects
    validator_projects = ValidatorRequest.objects.filter(user=request.user)

    # Handle project deletion
    if request.method == "POST" and request.POST.get('delete'):
        query = ValidatorRequest.objects.filter(
            pk=request.POST.get('delete')
        )
        query.delete()

    return render(request,
                  'primer_finder/validator_home.html',
                  {
                      'validator_projects': validator_projects
                  })


@csrf_exempt  # needed or IE explodes
@login_required
def primer_validator_request(
    request: HttpRequest
) -> HttpResponse:
    """
    View for the primer verifier request page. This view is responsible for
    rendering the request page and for saving the email to the database.

    Args:
        request (HttpRequest): The HTTP request object.

    Returns:
        HttpResponse: The HTTP response object.
    """
    validator_data = request.POST.copy() if request.method == 'POST' else None
    if validator_data is not None:
        validator_data.setdefault('mismatches', '3')
        validator_data.setdefault('min_amplicon_size', '0')
        validator_data.setdefault('max_amplicon_size', '1500')
        validator_data.setdefault('range_buffer', '0')
        validator_data.setdefault('contig_breaks', '')
    form = ValidatorExperimentForm(
        validator_data,
        request.FILES or None,
        initial={
            'mismatches': 3,
            'min_amplicon_size': 0,
            'max_amplicon_size': 1500,
            'range_buffer': 0,
            'contig_breaks': False,
        }
    )
    target_forms = ValidatorTargetFormSetFactory(
        request.POST or None, request.FILES or None, prefix='targets'
    )
    form_valid = form.is_valid() if request.method == 'POST' else False
    target_forms.classification_rule = (
        form.cleaned_data.get('classification_rule', '') if form_valid else ''
    ) or request.POST.get('classification_rule', '')
    target_forms.logic = (
        form.cleaned_data.get('logic', '')
        if form_valid else request.POST.get('logic', '')
    )
    target_forms_valid = (
        target_forms.is_valid() if request.method == 'POST' else False
    )
    if form_valid and target_forms_valid:
        selected_categories = form.cleaned_data.get(
            'gene_exclusion_categories', []
        )
        has_gene_reference = any(
            (row.get('gene_sequence') or '').strip() or
            row.get('reference_fasta')
            for row in target_forms.cleaned_data
        )
        if selected_categories and not has_gene_reference:
            form.add_error(
                'gene_exclusion_categories',
                'Sample exclusion gene-analysis categories require at least '
                'one pasted gene reference sequence or uploaded FASTA file.'
            )
            form_valid = False
    if request.method == "POST" and form_valid and target_forms_valid:
        rows = target_forms.cleaned_data
        first = rows[0]
        primer_lines = []
        with transaction.atomic():
            verifier_request = ValidatorRequest.objects.create(
                user=request.user,
                project_name=form.cleaned_data.get('project_name') or '',
                status='Processing',
                mismatches=form.cleaned_data['mismatches'],
                min_amplicon_size=form.cleaned_data['min_amplicon_size'],
                max_amplicon_size=form.cleaned_data['max_amplicon_size'],
                contig_breaks=form.cleaned_data['contig_breaks'],
                range_buffer=form.cleaned_data['range_buffer'],
                forward_primer=first['forward_primer'],
                reverse_primer=first['reverse_primer'],
                probe_sequence='',
                inclusivity_panel=form.cleaned_data['inclusivity_panel'],
                exclusivity_panel=form.cleaned_data['exclusivity_panel'],
                gene_exclusion_categories=form.cleaned_data.get(
                    'gene_exclusion_categories', []
                ),
            )
            if not verifier_request.project_name:
                verifier_request.project_name = verifier_request.container_namer()
            sample_map = {}
            for row in rows:
                primer_name = canonicalize_primer_base_name(
                    row['target_name']
                )

                if not primer_name:
                    raise ValueError(
                        'Validator target name could not be canonicalized'
                    )

                sample = sample_map.get(primer_name)
                if sample is None:
                    sample = ValidatorSample.objects.create(
                        validator_request=verifier_request,
                        name=primer_name,
                        logic=form.cleaned_data['logic'],
                        classification_rule=form.cleaned_data[
                            'classification_rule'
                        ],
                    )
                    sample_map[primer_name] = sample

                target = ValidatorTarget.objects.create(
                    sample=sample,
                    name=primer_name,
                    forward_primer=row['forward_primer'],
                    reverse_primer=row['reverse_primer'],
                    probe_sequence=row.get('probe_sequence', ''),
                    probe_evaluation_method=row.get(
                        'probe_evaluation_method',
                        'percent_identity'
                    ),
                    probe_percent_identity=(
                        row.get('probe_percent_identity')
                        if row.get('probe_percent_identity') is not None
                        else 80.0
                    ),
                    probe_max_differences=(
                        row.get('probe_max_differences')
                        if row.get('probe_max_differences') is not None
                        else 2
                    ),
                    gene_sequence=row.get('gene_sequence', ''),
                )

                reference_fasta = row.get('reference_fasta')
                if reference_fasta:
                    target.reference_fasta = reference_fasta
                    target.save(update_fields=['reference_fasta'])

                primer_lines.extend([
                    '>{0}-F'.format(primer_name),
                    row['forward_primer'],
                    '>{0}-R'.format(primer_name),
                    row['reverse_primer'],
                ])
            verifier_request.primer_sequences = '\n'.join(primer_lines)
            verifier_request.save()
            for row in rows:
                primer_name = canonicalize_primer_base_name(
                    row['target_name']
                )
                primer_set = ValidatorPrimerSet.objects.create(
                    validator_request=verifier_request,
                    primer_name=primer_name,
                )
                ValidatorPrimers.objects.create(
                    primer=primer_set,
                    primer_header=primer_name + '-F',
                    primer_sequence=row['forward_primer'],
                )
                ValidatorPrimers.objects.create(
                    primer=primer_set,
                    primer_header=primer_name + '-R',
                    primer_sequence=row['reverse_primer'],
                )
            populate_panel(
                genera=verifier_request.inclusivity_panel,
                pk=verifier_request.pk,
                panel_type='inclusivity',
                panel=ValidatorPanel
            )
            populate_panel(
                genera=verifier_request.exclusivity_panel,
                pk=verifier_request.pk,
                panel_type='exclusivity',
                panel=ValidatorPanel
            )
        run_primer_validator.apply_async(
            queue="primer_finder",
            kwargs={"validator_request_pk": verifier_request.pk},
            countdown=10,
        )
        return redirect(
            'primer_finder:primer_validator_processing',
            validator_pk=verifier_request.pk
        )
    return render(request,
                  'primer_finder/validator_request.html',
                  {
                      'form': form,
                      'target_forms': target_forms,
                  })


@csrf_exempt  # needed or IE explodes
@login_required
def primer_validator_processing(
    request: HttpRequest,
    validator_pk: int
) -> HttpResponse:
    """
    View for the primer verifier processing page. This view is responsible for
    rendering the processing page and for saving the email to the database.

    Args:
        request (object): The HTTP request object.
        verifier_pk (int): The primary key of the ValidatorRequest object.

    Returns:
        HttpResponse: The HTTP response object.
    """
    # Retrieve the ValidatorRequest object
    validator_request = get_object_or_404(
        ValidatorRequest,
        pk=validator_pk
    )

    # Create the email form
    form = EmailForm()

    # If the form is submitted, add the email to the database
    if request.method == 'POST':
        form = EmailForm(request.POST)

        # Validate the form
        if form.is_valid():
            email_address = form.cleaned_data.get('email')

            # Validate the email address
            if email_address and isinstance(email_address, str):
                if validator_request.emails_array is None:
                    validator_request.emails_array = []

                # Check if the email address is already in the array
                if email_address not in validator_request.emails_array:
                    validator_request.emails_array.append(email_address)
                    validator_request.save()
                    form = EmailForm()
                    messages.success(request, _("Email saved"))
    return render(request,
                  'primer_finder/validator_processing.html',
                  {
                    'primer_validator_request': validator_request,
                      'form': form
                  })


@csrf_exempt  # needed or IE explodes
@login_required
def primer_validator_results(
    request: HttpRequest,
    validator_pk: int
) -> HttpResponse:
    """
    View for the primer verifier results page. This view is responsible for
    rendering the results page for a specific primer validation request.

    Args:
        request (object): The HTTP request object.
        verifier_pk (int): The primary key of the ValidatorRequest object.

    Returns:
        HttpResponse: The HTTP response object.
    """
    # Retrieve the ValidatorRequest object
    validator_request = get_object_or_404(
        ValidatorRequest, pk=validator_pk
    )

    # Retrieve the PrimerSet object(s) corresponding to the validator_request
    # primary key
    primer_set = ValidatorPrimerSet.objects.filter(
        validator_request_id=validator_request.pk
    )

    # Retrieve the Panel object(s) corresponding to the validator_request
    # primary key
    panel_set = ValidatorPanel.objects.filter(
        validator_request_id=validator_request.pk
    )

    # Create a list of all the primer names
    primer_list = sorted(
        list(
            {
                primer.primer_name for primer in primer_set
            }
        )
    )

    # Initialise a dictionary to hold the cleaned primer names
    clean_primers = {}
    # Since the hacky way I used to have primer,panel,genus-specific elements
    # in the HTML involved using variables, the primers cannot have certain
    # characters, or start with a digit
    for primer in primer_list:
        # Initialise the clean primer as the primer
        clean_primer = primer
        # Remove illegal characters
        for char in ['-', '_']:
            if char in primer:
                clean_primer = clean_primer.replace(char, '')
                # If the primer name starts with a digit, prepend an 'a' to
                # the start of the name
                if clean_primer[:1].isdigit():
                    clean_primer = 'a' + clean_primer
        # Store the cleaned primer name
        clean_primers[primer] = clean_primer
    return render(
        request,
        'primer_finder/verifier_results.html',
        {
            'request': validator_request,
            'mismatch_details': json.loads(
                validator_request.summary
            ),
            'primers': primer_list,
            'clean_primers': clean_primers,
            'panels': sorted(list(
                {
                    panel.panel for panel in panel_set
                }
            ), reverse=True),
            'genera': sorted(
                list(
                    {
                        panel.genus.lower() for panel in panel_set
                    }
                )
            )
        }
    )


@csrf_exempt  # needed or IE explodes
@login_required
def primer_validator_report(request, validator_pk):
    """
    View for the primer verifier report page.

    Args:
        request (object): The HTTP request object.
        verifier_pk (int): The primary key of the ValidatorRequest object.
    """
    verifier_request = get_object_or_404(ValidatorRequest, pk=validator_pk)

    # Retrieve the PrimerSet object(s) corresponding to the validator_request
    # primary key
    primer_set = ValidatorPrimerSet.objects.filter(
        validator_request_id=verifier_request.pk
    )
    # Retrieve the Panel object(s) corresponding to the validator_request
    # primary key
    panel_set = ValidatorPanel.objects.filter(
        validator_request_id=verifier_request.pk
    )
    primer_list = sorted(
        list(
            set([primer.primer_name for primer in primer_set])
        )
    )
    clean_primers = {}

    # Since the hacky way I used to have primer,panel,genus-specific elements
    # in the HTML involved using variables,
    # the primers cannot have certain characters, or start with a digit
    for primer in primer_list:
        # Initialise the clean primer as the primer
        clean_primer = primer
        # Remove illegal characters
        for char in ['-', '_']:
            if char in primer:
                clean_primer = clean_primer.replace(char, '')
                # If the primer name starts with a digit, prepend an 'a' to
                # the start of the name
                if clean_primer[:1].isdigit():
                    clean_primer = 'a' + clean_primer
        clean_primers[primer] = clean_primer
    return render(request,
                  'primer_finder/validator_report.html',
                  {
                      'primer_validator_request': verifier_request,
                      'mismatch_details': json.loads(verifier_request.summary),
                      'totals': json.loads(verifier_request.totals),
                      'allowed_mismatches': [6, 5, 4, 3, 2, 1],
                      'primers': primer_list,
                      'clean_primers': clean_primers,
                      'panels': sorted(
                          list(
                              set(
                                  [panel.panel for panel in panel_set])),
                          reverse=True
                        ),
                      'genera': sorted(
                          list(
                              set(
                                  [panel.genus for panel in panel_set]
                                )
                            )
                        ),
                      'forward': verifier_request.forward_primer.split(
                          '\n'
                        )[1],
                      'reverse': verifier_request.reverse_primer.split('\n')[1]
                  })


@csrf_exempt  # needed or IE explodes
@login_required
def finder_home(request):
    """
    View for the primer finder home page.

    Args:
        request (object): The HTTP request object.
    """
    finder_projects = VerifierRequest.objects.filter(user=request.user)

    if request.method == "POST":
        if request.POST.get('delete'):
            query = VerifierRequest.objects.filter(
                pk=request.POST.get('delete')
            )
            query.delete()

    return render(request,
                  'primer_finder/verifier_home.html',
                  {
                      'finder_projects': finder_projects
                  })
