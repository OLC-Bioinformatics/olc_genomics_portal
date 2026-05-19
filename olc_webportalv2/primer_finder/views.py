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

# Portal-specific imports
from olc_webportalv2.geneseekr.forms import EmailForm

from olc_webportalv2.primer_finder.forms import (
    PrimerValidatorForm,
    PrimerVerifierForm
)
from olc_webportalv2.primer_finder.methods import (
    exclusivity_panel_retrieve,
    populate_panel,
    populate_primer_details,
    populate_primer_sets,
)
from olc_webportalv2.primer_finder.models import (
    PrimerVerifierRequest,
    ValidatorPanel,
    ValidatorPrimers,
    ValidatorPrimerSet,
    ValidatorRequest,
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
def primer_validator_home(request: HttpRequest) -> HttpResponse:
    """
    View for the primer validator home page. This view is responsible for
    rendering the home page, and for deleting projects from the database.

    :param request: The HTTP request object

    :return: The HTTP response object
    """

    # Retrieve all the projects from the database that are associated with the
    # current user
    verifier_projects = PrimerVerifierRequest.objects.filter(user=request.user)
    if request.method == "POST":

        # If the delete button is pressed, delete the project from the database
        if request.POST.get('delete'):
            query = PrimerVerifierRequest.objects.filter(
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
def primer_validator_request(request: HttpRequest) -> HttpResponse:
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
    form = PrimerVerifierForm()
    if request.method == "POST":
        form = PrimerVerifierForm(request.POST)
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
            validator_request.inclusivity_panel = form.cleaned_data.get(
                'inclusivity_panel'
            )
            validator_request.exclusivity_panel = form.cleaned_data.get(
                'exclusivity_panel'
            )
            validator_request.project_name = form.cleaned_data.get(
                'project_name'
            )
            validator_request.status = 'Processing'
            validator_request.save()

            # Give the project a name if it was not provided
            if not validator_request.project_name:
                validator_request.project_name = \
                    PrimerVerifierRequest.objects.get(
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
            inclusivity_query = PrimerVerifierRequest.objects.filter(
                pk=validator_request.pk)[0].inclusivity_panel
            exclusivity_query = PrimerVerifierRequest.objects.filter(
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

            # Create and submit the batch job
            run_primer_verifier.apply_async(
                queue='cowbat',
                kwargs={'verifier_request_pk': validator_request.pk},
                countdown=10
            )
            # Redirect the view to validator_processing
            return redirect(
                'primer_finder:primer_validator_processing',
                validator_pk=validator_request.pk
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
def primer_validator_processing(
    request: HttpRequest,
    validator_pk: int
) -> HttpResponse:
    """
    View for the primer validator processing page. This view is responsible for
    rendering the processing page, and for saving the email to the database.

    :param request: The HTTP request object
    :param verifier_pk: The primary key of the verifier request

    :return: The HTTP response object
    """
    # Retrieve the PrimerVerifierRequest object
    validator_request = get_object_or_404(
        PrimerVerifierRequest,
        pk=validator_pk
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
                if validator_request.emails_array is None:
                    validator_request.emails_array = []

                # If the email is not already in the database, save it
                if email_address not in validator_request.emails_array:
                    validator_request.emails_array.append(email_address)
                    validator_request.save()
                    form = EmailForm()
                    messages.success(request, _("Email saved"))
    return render(
        request,
        'primer_finder/verifier_processing.html',
        {
            'primer_verifier_request': validator_request,
            'form': form
        }
    )


@csrf_exempt  # needed or IE explodes
@login_required
def primer_validator_results(
    request: HttpRequest,
    validator_pk: int
) -> HttpResponse:
    """
    View for the primer validator results page. This view is responsible for
    rendering the results page, and for populating the page with the
    appropriate data.

    :param request: The HTTP request object
    :param verifier_pk: The primary key of the verifier request

    :return: The HTTP response object
    """
    validator_request = get_object_or_404(
        PrimerVerifierRequest,
        pk=validator_pk
    )

    # Retrieve the PrimerSet object(s) corresponding to the verifier_request
    # primary key
    primer_set = VerifierPrimerSet.objects.filter(
        verifier_request_id=validator_request.pk
    )

    # Retrieve the Panel object(s) corresponding to the verifier_request
    # primary key
    panel_set = VerifierPanel.objects.filter(
        verifier_request_id=validator_request.pk
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
            'request': validator_request,
            'mismatch_details': json.loads(validator_request.summary),
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
def primer_verifier_home(request: HttpRequest) -> HttpResponse:
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
    if request.method == "POST":
        if request.POST.get('delete'):
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
def primer_verifier_request(
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
    # Create the form
    form = PrimerValidatorForm()
    if request.method == "POST":
        # Populate the form with data from the request
        form = PrimerValidatorForm(request.POST)

        # Form validation
        if form.is_valid():
            # Save the information from the ModelForm into the database
            # (but don't commit yet; certain required elements still need to
            # be populated)
            verifier_request = form.save(commit=False)

            # Populate the missing elements as required
            verifier_request.user = request.user
            verifier_request.forward_primer = form.cleaned_data.get(
                'forward_primer'
            )
            verifier_request.reverse_primer = form.cleaned_data.get(
                'reverse_primer'
            )
            verifier_request.probe_sequence = form.cleaned_data.get(
                'probe_sequence'
            )
            verifier_request.inclusivity_panel = form.cleaned_data.get(
                'inclusivity_panel'
            )
            verifier_request.project_name = form.cleaned_data.get(
                'project_name'
            )
            verifier_request.status = 'Processing'

            # Save the entry
            verifier_request.save()

            # Give the project a name if it was not provided
            if not verifier_request.project_name:
                # Set the name of the project using the generic name used for
                # batch jobs, containers, etc.
                # ('primer-validator-' + primer_validator_request.pk)
                verifier_request.project_name = \
                    ValidatorRequest.objects.get(
                        pk=verifier_request.pk
                    ).container_namer()
                verifier_request.save()

            # Extract the list of all primer base names
            primer_list = form.cleaned_data.get('primer_list') or []

            # Enter the list of primer base names into the database
            populate_primer_sets(
                pk=verifier_request.pk,
                primer_list=primer_list,
                model=ValidatorPrimerSet
            )

            # Populate the database with the details of the primers
            primer_details = form.cleaned_data.get('primer_details') or {}

            # Create a query of all the primer base_names
            primer_query = ValidatorPrimerSet.objects.filter(
                validator_request_id=verifier_request.pk
            )
            populate_primer_details(
                query=primer_query,
                details=primer_details,
                model=ValidatorPrimers
            )
            # Create queries for the list of the entries in the
            # inclusivity_panel and exclusivity_panel
            inclusivity_query = ValidatorRequest.objects.filter(
                pk=verifier_request.pk)[0].inclusivity_panel

            # Populate the exclusivity query
            exclusivity_panel = exclusivity_panel_retrieve(
                inclusivity=inclusivity_query[0]
            )
            verifier_request.exclusivity_panel = exclusivity_panel
            verifier_request.save()

            # Create queries for the list of the entries in the
            # inclusivity_panel and exclusivity_panel
            exclusivity_query = ValidatorRequest.objects.filter(
                pk=verifier_request.pk
            )[0].exclusivity_panel

            # Populate the database with the inclusivity/exclusivity panel and
            # the genus/genera
            populate_panel(
                genera=inclusivity_query,
                pk=verifier_request.pk,
                panel_type='inclusivity',
                panel=ValidatorPanel
            )
            populate_panel(
                genera=exclusivity_query,
                pk=verifier_request.pk,
                panel_type='exclusivity',
                panel=ValidatorPanel
            )

            # Create and submit the batch job
            run_primer_validator.apply_async(
                queue="cowbat",
                kwargs={"validator_request_pk": verifier_request.pk},
                countdown=10,
            )
            # Redirect the view to primer_verifier_processing
            return redirect(
                'primer_finder:primer_verifier_processing',
                verifier_pk=verifier_request.pk
            )
    return render(request,
                  'primer_finder/validator_request.html',
                  {
                      'form': form,
                  })


@csrf_exempt  # needed or IE explodes
@login_required
def primer_verifier_processing(
    request: HttpRequest,
    verifier_pk: int
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
    verifier_request = get_object_or_404(
        ValidatorRequest,
        pk=verifier_pk
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
                if verifier_request.emails_array is None:
                    verifier_request.emails_array = []

                # Check if the email address is already in the array
                if email_address not in verifier_request.emails_array:
                    verifier_request.emails_array.append(email_address)
                    verifier_request.save()
                    form = EmailForm()
                    messages.success(request, _("Email saved"))
    return render(request,
                  'primer_finder/validator_processing.html',
                  {
                      'primer_validator_request': verifier_request,
                      'form': form
                  })


@csrf_exempt  # needed or IE explodes
@login_required
def primer_verifier_results(
    request: HttpRequest,
    verifier_pk: int
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
    verifier_request = get_object_or_404(
        ValidatorRequest, pk=verifier_pk
    )

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
            'request': verifier_request,
            'mismatch_details': json.loads(
                verifier_request.summary
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
def primer_verifier_report(request, verifier_pk):
    """
    View for the primer verifier report page.

    Args:
        request (object): The HTTP request object.
        verifier_pk (int): The primary key of the ValidatorRequest object.
    """
    verifier_request = get_object_or_404(ValidatorRequest, pk=verifier_pk)

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
    finder_projects = PrimerVerifierRequest.objects.filter(user=request.user)

    if request.method == "POST":
        if request.POST.get('delete'):
            query = PrimerVerifierRequest.objects.filter(
                pk=request.POST.get('delete')
            )
            query.delete()

    return render(request,
                  'primer_finder/verifier_home.html',
                  {
                      'finder_projects': finder_projects
                  })
