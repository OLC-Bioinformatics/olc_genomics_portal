"""Regression tests for portal routing and form template configuration."""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.staticfiles import finders
from django.template.loader import get_template
from django.test import (
    Client,
    RequestFactory,
    TestCase,
    override_settings,
)
from django.urls import resolve, reverse
from django.utils.translation import override


class PortalRoutingTests(TestCase):
    """Verify portal routes, navigation, templates, and static assets."""

    def test_admin_url_resolves(self):
        """Resolve the configured Django admin URL."""
        match = resolve("/admin/")

        self.assertEqual(match.namespace, "admin")
        self.assertEqual(match.url_name, "index")

    def test_admin_without_trailing_slash_redirects(self):
        """Redirect the slashless admin URL to its canonical path."""
        response = Client().get("/admin", HTTP_HOST="localhost")

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response["Location"], "/admin/")

    def test_crispy_bootstrap5_template_is_available(self):
        """Load the configured Bootstrap 5 crispy-forms template."""
        self.assertIsNotNone(get_template("bootstrap5/uni_form.html"))

    def test_research_assembly_template_compiles(self):
        """Load the research assembly template without syntax errors."""
        self.assertIsNotNone(
            get_template("cowbat/research_assembly_home.html")
        )

    def test_cowsnphr_tree_template_compiles(self):
        """Load the translated tree page without template syntax errors."""
        self.assertIsNotNone(get_template("cowsnphr/cowsnphr_tree.html"))

    def test_dropzone_upload_templates_compile(self):
        """Load each template that initializes the localized Dropzone UI."""
        templates = (
            "ampliseq/upload_sequence_data.html",
            "cowbat/custom_assembly_create.html",
            "cowbat/custom_assembly_upload.html",
            "cowbat/upload_interop.html",
            "cowbat/upload_metadata.html",
            "cowbat/upload_sequence_data.html",
            "cowsnphr/cowsnphr_upload.html",
            "filezone/container_view.html",
            "vir_typer/vir_typer_upload_sequences.html",
        )

        for template in templates:
            with self.subTest(template=template):
                self.assertIsNotNone(get_template(template))

    def test_validator_target_labels_are_translated(self):
        """Translate labels rendered from dynamically added target forms."""
        from olc_webportalv2.primer_finder.forms import ValidatorTargetForm

        with override("fr"):
            form = ValidatorTargetForm()

            self.assertEqual(
                str(form["target_name"].label),
                "Nom de la cible",
            )

    def test_cowsnphr_tree_dropdowns_translate_labels_keep_values(self):
        """Translate TidyTree options without changing library values."""
        request = RequestFactory().get("/")
        request.phylogenetic_tree = ""

        with override("fr"):
            html = get_template("cowsnphr/cowsnphr_tree.html").render(
                {"request": request}
            )

        self.assertIn('horizontal: "Horizontal"', html)
        self.assertIn('circular: "Circulaire"', html)
        self.assertIn('weighted: "Arbre pondéré"', html)
        self.assertIn('smooth: "Courbe lissée"', html)
        self.assertIn('.attr("value", d => d)', html)

    def test_home_uses_bootstrap_5_data_attributes(self):
        """Render the home page with Bootstrap 5 navigation attributes."""
        response = Client().get("/", HTTP_HOST="localhost")
        html = response.content.decode(response.charset)

        self.assertEqual(response.status_code, 200)
        self.assertIn('data-bs-toggle="collapse"', html)
        self.assertIn('data-bs-toggle="dropdown"', html)
        self.assertNotIn('data-toggle="', html)
        self.assertIn('data-bs-target="#navbarSupportedContent"', html)
        self.assertIn('aria-labelledby="metadataDropdownButton"', html)
        self.assertIn('aria-labelledby="dropdownMenuButton"', html)

    def test_french_home_translates_the_virus_typer_links(self):
        """Render the Virus Typer links using the existing French label."""
        response = self.client.get("/", HTTP_ACCEPT_LANGUAGE="fr")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Typage viral")
        self.assertNotContains(response, ">VirusTyper")

    def test_home_and_about_render_the_shared_portal_shell(self):
        """Render the common navigation and scripts on both landing pages."""
        for path in ("/", "/about/"):
            with self.subTest(path=path):
                response = self.client.get(path)

                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'id="log-in-link"')
                self.assertContains(response, "jquery-3.7.1.min.js")
                self.assertContains(response, "project.js")

    def test_navigation_menu_items_link_to_their_sections(self):
        """Link each navigation item to a resolvable route."""
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)

        menu_routes = (
            "sequence_database:database_home",
            "metadata:metadata_home",
            "metadata:metadata_browse",
            "metadata:metadata_submit",
            "metadata_upload:upload_files",
            "filezone:filezone",
            "data:data_home",
            "cowbat:assembly_home",
            "cowbat:research_assembly",
            "geneseekr:geneseekr_home",
            "geneseekr:tree_home",
            "geneseekr:amr_home",
            "geneseekr:prokka_home",
            "geneseekr:neighbor_home",
            "vir_typer:vir_typer_home",
            "primer_finder:primer_home",
            "ampliseq:ampliseq",
            "cowsnphr:cowsnphr",
        )
        for route_name in menu_routes:
            with self.subTest(route=route_name):
                target = reverse(route_name)
                self.assertContains(response, f'href="{target}"')
                self.assertIsNotNone(resolve(target))

    def test_navigation_shows_login_or_profile_actions(self):
        """Show login or profile actions based on authentication state."""
        anonymous_response = self.client.get("/")
        login_url = reverse("account_login")
        self.assertContains(
            anonymous_response,
            f'href="{login_url}"',
        )
        self.assertContains(anonymous_response, 'id="log-in-link"')

        user = get_user_model().objects.create_user(
            username="navigation-test",
            password="password",
        )
        self.client.force_login(user)
        authenticated_response = self.client.get("/")
        profile_url = reverse(
            "users:detail",
            kwargs={"username": user.username},
        )
        logout_url = reverse("account_logout")

        self.assertContains(
            authenticated_response,
            f'href="{profile_url}"',
        )
        self.assertContains(
            authenticated_response,
            f'action="{logout_url}"',
        )
        self.assertContains(authenticated_response, 'name="csrfmiddlewaretoken"')
        self.assertNotContains(authenticated_response, 'id="log-in-link"')

    def test_email_login_offers_signup_when_registration_is_enabled(self):
        response = self.client.get(reverse("account_login"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            f'<a href="{reverse("account_signup")}">Create an account</a>',
        )
        signup_response = self.client.get(reverse("account_signup"))
        self.assertEqual(signup_response.status_code, 200)

    @override_settings(ACCOUNT_ALLOW_REGISTRATION=False)
    def test_email_login_hides_signup_when_registration_is_disabled(self):
        response = self.client.get(reverse("account_login"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Create an account")

    def test_language_selector_posts_and_sets_the_selected_language(self):
        """Post language selection and set the language cookie."""
        response = self.client.get("/")
        set_language_url = reverse("set_language")
        self.assertContains(
            response,
            f'action="{set_language_url}" method="post"',
        )
        self.assertContains(response, 'name="language"')

        response = self.client.post(
            set_language_url,
            {"language": "fr", "next": "/"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.cookies[settings.LANGUAGE_COOKIE_NAME].value,
            "fr",
        )

    def test_language_selector_enforces_csrf_protection(self):
        """Require a CSRF token when changing the selected language."""
        client = Client(enforce_csrf_checks=True)
        page = client.get("/", HTTP_HOST="localhost")
        csrf_token = page.cookies["csrftoken"].value
        set_language_url = reverse("set_language")
        post_data = {"language": "fr", "next": "/"}

        self.assertContains(page, 'name="csrfmiddlewaretoken"')
        denied_response = client.post(
            set_language_url,
            post_data,
            HTTP_HOST="localhost",
        )
        accepted_response = client.post(
            set_language_url,
            {**post_data, "csrfmiddlewaretoken": csrf_token},
            HTTP_HOST="localhost",
        )

        self.assertEqual(denied_response.status_code, 403)
        self.assertEqual(accepted_response.status_code, 302)

    def test_javascript_catalog_uses_the_selected_language(self):
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = "fr"

        response = self.client.get("/i18n/jsi18n/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "En attente")
        self.assertContains(
            response,
            '"Drop files to upload or click": "D\\u00e9posez',
        )

    def test_authenticated_language_selector_updates_account_preference(self):
        """Persist the selector's language in the authenticated user account."""
        user = get_user_model().objects.create_user(
            username="language-preference-test",
            password="password",
            language="en-ca",
        )
        self.client.force_login(user)

        response = self.client.post(
            reverse("set_language"),
            {"language": "fr", "next": "/en-ca/users/"},
        )

        user.refresh_from_db()
        self.assertEqual(user.language, "fr")
        self.assertEqual(
            response.cookies[settings.LANGUAGE_COOKIE_NAME].value,
            "fr",
        )
        self.assertEqual(response["Location"], "/fr/users/")

    def test_authenticated_account_language_overrides_browser_cookie(self):
        """Apply the saved account language on each authenticated request."""
        user = get_user_model().objects.create_user(
            username="account-language-test",
            password="password",
            language="fr",
        )
        self.client.force_login(user)
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = "en-ca"

        redirect_response = self.client.get("/en-ca/users/")

        self.assertEqual(redirect_response.status_code, 302)
        self.assertEqual(redirect_response["Location"], "/fr/users/")

        response = self.client.get(redirect_response["Location"])
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<html lang="fr">')
        self.assertEqual(response["Content-Language"], "fr")

    def test_base_assets_exist_and_jquery_precedes_project_javascript(self):
        """Find base assets and ensure jQuery loads before project scripts."""
        response = Client().get("/", HTTP_HOST="localhost")
        html = response.content.decode(response.charset)

        self.assertEqual(response.status_code, 200)
        for asset in (
            "css/bootstrap.min.css",
            "css/bootstrap-datepicker.min.css",
            "css/project.css",
            "js/jquery-3.7.1.min.js",
            "js/bootstrap.min.js",
            "js/project.js",
            "js/dropzone_i18n.js",
        ):
            with self.subTest(asset=asset):
                self.assertIsNotNone(finders.find(asset))

        self.assertLess(
            html.index("jquery-3.7.1.min.js"),
            html.index("project.js"),
        )
