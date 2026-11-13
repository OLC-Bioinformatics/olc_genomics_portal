"""Regression tests for portal routing and form template configuration."""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.staticfiles import finders
from django.template.loader import get_template
from django.test import Client, TestCase
from django.urls import resolve, reverse


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
            f'href="{logout_url}"',
        )
        self.assertNotContains(authenticated_response, 'id="log-in-link"')

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
        ):
            with self.subTest(asset=asset):
                self.assertIsNotNone(finders.find(asset))

        self.assertLess(
            html.index("jquery-3.7.1.min.js"),
            html.index("project.js"),
        )
