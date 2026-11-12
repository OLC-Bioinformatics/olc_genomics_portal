"""Regression tests for portal routing and form template configuration."""

from django.contrib.staticfiles import finders
from django.test import Client, TestCase
from django.template.loader import get_template
from django.urls import resolve


class PortalRoutingTests(TestCase):
    def test_admin_url_resolves(self):
        match = resolve("/admin/")

        self.assertEqual(match.namespace, "admin")
        self.assertEqual(match.url_name, "index")

    def test_admin_without_trailing_slash_redirects(self):
        response = Client().get("/admin", HTTP_HOST="localhost")

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response["Location"], "/admin/")

    def test_crispy_bootstrap5_template_is_available(self):
        self.assertIsNotNone(get_template("bootstrap5/uni_form.html"))

    def test_research_assembly_template_compiles(self):
        self.assertIsNotNone(
            get_template("cowbat/research_assembly_home.html")
        )

    def test_home_uses_bootstrap_5_data_attributes(self):
        response = Client().get("/", HTTP_HOST="localhost")
        html = response.content.decode(response.charset)

        self.assertEqual(response.status_code, 200)
        self.assertIn('data-bs-toggle="collapse"', html)
        self.assertIn('data-bs-toggle="dropdown"', html)
        self.assertNotIn('data-toggle="', html)

    def test_base_assets_exist_and_jquery_precedes_project_javascript(self):
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
