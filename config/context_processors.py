"""Context processors used by FoodPort templates."""

from django.conf import settings
from django.http import HttpRequest


_APP_HOME_ROUTES = {
    "ampliseq": ("ampliseq:ampliseq", "AmpliSeq Home", {"ampliseq"}),
    "cowsnphr": ("cowsnphr:cowsnphr", "COWSNPhR Home", {"cowsnphr"}),
    "data": ("data:data_home", "Data Home", {"data_home"}),
    "filezone": ("filezone:filezone", "FileZone Home", {"filezone"}),
    "metadata": ("metadata:metadata_home", "Metadata Home", {"metadata_home"}),
    "metadata_upload": ("metadata:metadata_home", "Metadata Home", set()),
    "primer_finder": (
        "primer_finder:primer_home",
        "PrimerFinder Home",
        {"primer_home"},
    ),
    "sequence_database": (
        "sequence_database:database_home",
        "Database Home",
        {"database_home"},
    ),
    "vir_typer": (
        "vir_typer:vir_typer_home",
        "VirusTyper Home",
        {"vir_typer_home"},
    ),
}

_COWBAT_RESEARCH_ROUTES = {
    "blob_assembly",
    "upload_assembly",
    "upload_assembly_upload",
    "manage_assembly",
    "manage_assembly_run",
    "delete_research_files",
    "research_assembly_rename_status",
    "upload_sequence_blob_sas",
}

_GENESEEKR_HOME_ROUTES = {
    "amr_": ("geneseekr:amr_home", "AMR Home"),
    "neighbor_": ("geneseekr:neighbor_home", "Neighbor Home"),
    "prokka_": ("geneseekr:prokka_home", "Annotation Home"),
    "tree_": ("geneseekr:tree_home", "Tree Home"),
}
_GENESEEKR_LANDING_PAGES = {
    "amr_home",
    "geneseekr_home",
    "neighbor_home",
    "prokka_home",
    "tree_home",
}


def auth_settings(request):
    """Provide authentication state and redirect details to templates.

    Args:
        request: The current Django request.

    Returns:
        Authentication state and redirect details for template contexts.

    """
    redirect_field_name = (
        request.GET.get("redirect_field_name")
        or request.POST.get("redirect_field_name")
        or "next"
    )
    redirect_field_value = (
        request.GET.get(redirect_field_name)
        or request.POST.get(redirect_field_name)
        or request.GET.get("redirect_to")
        or request.POST.get("redirect_to")
        or ""
    )
    return {
        "MICROSOFT_AUTH_ENABLED": getattr(
            settings,
            "MICROSOFT_AUTH_ENABLED",
            False,
        ),
        "ACCOUNT_ALLOW_REGISTRATION": getattr(
            settings,
            "ACCOUNT_ALLOW_REGISTRATION",
            False,
        ),
        "redirect_field_name": redirect_field_name,
        "redirect_field_value": redirect_field_value,
        "redirect_to": request.GET.get(
            "redirect_to",
            request.POST.get("redirect_to", ""),
        ),
    }


def app_home_navigation(request: HttpRequest) -> dict[str, str]:
    """Provide the nearest app landing page for shared template navigation."""
    resolver_match = getattr(request, "resolver_match", None)
    if resolver_match is None:
        return {}

    namespace = resolver_match.namespace
    url_name = resolver_match.url_name or ""

    if namespace == "cowbat":
        if url_name in {"assembly_home", "research_assembly"}:
            return {}
        route, label = (
            ("cowbat:research_assembly", "Research Assembly Home")
            if url_name in _COWBAT_RESEARCH_ROUTES
            else ("cowbat:assembly_home", "Assembly Home")
        )
    elif namespace == "geneseekr":
        if url_name in _GENESEEKR_LANDING_PAGES:
            return {}
        route, label = ("geneseekr:geneseekr_home", "GeneSeekr Home")
        for prefix, destination in _GENESEEKR_HOME_ROUTES.items():
            if url_name.startswith(prefix):
                route, label = destination
                break
    else:
        destination = _APP_HOME_ROUTES.get(namespace)
        if destination is None:
            return {}
        route, label, landing_pages = destination
        if url_name in landing_pages:
            return {}

    return {
        "app_home_route": route,
        "app_home_label": label,
    }
